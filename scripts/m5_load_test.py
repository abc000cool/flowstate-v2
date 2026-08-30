"""M5 load test — 10 concurrent sweep jobs through the Docker stack (CLAUDE.md §11).

This script brings nothing up itself. It assumes the compose stack is already
running with the worker service scaled out, e.g.::

    docker compose up -d --build
    docker compose up -d --scale worker=2
    uv run --no-sync python scripts/m5_load_test.py

Phase 1 (macro load): POSTs ``--sweeps`` (default 10) sweeps, each a light
macro-tier 2×2 grid (2 penetrations × 2 compliances × 1 controller) × 3
replicates on a 2 km corridor — individual jobs are deliberately tiny so the
test exercises *concurrency* (RQ fan-out, two workers, shared SQLite metadata
store, API under polling load), not raw compute. While the jobs run, a
background sampler hits ``/healthz`` continuously.

Phase 2 (micro under queue concurrency): one micro-tier sweep (2 cells × 2
replicates, 300 s Sugiyama ring) through the same stack, proving SUMO/libsumo
replicates execute correctly inside the queued-worker containers.

Hard assertions (exit code 1 on any failure):

- every sweep reaches ``status=done`` with ``runs_failed == 0``;
- every child run reaches ``status=done`` and its ``/metrics`` endpoint
  answers 200 with the expected replicate count;
- ``/healthz`` p95 measured while jobs were running is < 500 ms and every
  sample returned HTTP 200;
- everything completes inside ``--timeout`` seconds per phase.

Reports wall time per phase, per-sweep completion timings, and healthz latency
stats; ``--json-out`` additionally writes the raw numbers as JSON (used to
fill in docs/M5_LOAD_TEST.md).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Scenario payloads (kept deliberately light — see module docstring)
# ---------------------------------------------------------------------------

#: Phase-1 base scenario: 2 km single-lane corridor on the macro screening
#: tier. 20 CTM cells at the default dx=100 m; a 300 s horizon costs
#: milliseconds per replicate, so sweep jobs stress the queue, not the CPU.
MACRO_SCENARIO: dict[str, Any] = {
    "name": "m5_load_macro_corridor_2km",
    "tier": "macro",
    "network": {
        "kind": "corridor",
        "length_m": 2000.0,
        "lanes": 1,
        "inflow": [[0.0, 0.35], [60.0, 0.45]],
    },
    "sim": {"duration_s": 300.0, "step_length_s": 0.5, "output_hz": 1.0},
    "seed": 42,
    "replicates": 3,
}

#: Phase-1 grid: 2×2 (× 1 controller) → 4 cells × 3 replicates per sweep.
MACRO_GRID: dict[str, Any] = {
    "penetrations": [0.02, 0.05],
    "compliances": [0.5, 1.0],
    "controllers": ["follower_stopper"],
    "replicates": 3,
}

#: Phase-2 scenario: canonical 230 m / 22-vehicle Sugiyama ring, 300 s, on
#: the micro (SUMO) tier — small enough to finish quickly, real enough to
#: prove libsumo replicates run under queued-worker concurrency.
MICRO_SCENARIO: dict[str, Any] = {
    "name": "m5_load_micro_ring_300s",
    "tier": "micro",
    "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
    "fleet": {
        "model": "IDM",
        "v0": 33.3,
        "T": 1.2,
        "a_max": 0.73,
        "b": 1.67,
        "s0": 2.0,
        "delta": 4.0,
        "heterogeneity_frac": 0.12,
    },
    "sim": {"duration_s": 300.0, "step_length_s": 0.5, "warmup_s": 60.0, "output_hz": 2.0},
    "seed": 42,
    "replicates": 2,
}

#: Phase-2 grid: 2 cells (1 penetration × 2 compliances) × 2 replicates.
MICRO_GRID: dict[str, Any] = {
    "penetrations": [0.05],
    "compliances": [0.5, 1.0],
    "controllers": ["follower_stopper"],
    "replicates": 2,
}

HEALTHZ_P95_LIMIT_MS = 500.0
TERMINAL = ("done", "failed")


# ---------------------------------------------------------------------------
# Healthz sampler
# ---------------------------------------------------------------------------


@dataclass
class HealthSampler:
    """Background /healthz prober; collects latencies while jobs run."""

    base_url: str
    interval_s: float = 0.25
    latencies_ms: list[float] = field(default_factory=list)
    non_200: int = 0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def _loop(self) -> None:
        with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                try:
                    r = client.get("/healthz")
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    self.latencies_ms.append(dt_ms)
                    if r.status_code != 200:
                        self.non_200 += 1
                except httpx.HTTPError:
                    self.latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                    self.non_200 += 1
                self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15.0)
        lat = sorted(self.latencies_ms)
        if not lat:
            return {"n": 0, "non_200": self.non_200}
        return {
            "n": len(lat),
            "non_200": self.non_200,
            "p50_ms": round(statistics.median(lat), 1),
            "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
            "max_ms": round(lat[-1], 1),
        }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def post_scenario(client: httpx.Client, config: dict[str, Any]) -> str:
    r = client.post("/api/v1/scenarios", json=config)
    if r.status_code != 201:
        raise SystemExit(f"FAIL: POST /scenarios -> {r.status_code}: {r.text[:500]}")
    return str(r.json()["scenario_id"])


def post_sweep(client: httpx.Client, scenario_id: str, grid: dict[str, Any]) -> str:
    r = client.post("/api/v1/sweeps", json={"scenario_id": scenario_id, **grid})
    if r.status_code != 202:
        raise SystemExit(f"FAIL: POST /sweeps -> {r.status_code}: {r.text[:500]}")
    return str(r.json()["sweep_id"])


def sweep_settled(body: dict[str, Any]) -> bool:
    """A sweep is settled when the fan-out finished and every cell's run has
    reached a terminal state (or the fan-out itself failed)."""
    if body["status"] == "failed":
        return True
    if body["status"] != "done":
        return False  # fan-out still queued/running
    cells = body["cells"]
    return all(c["status"] in TERMINAL for c in cells) and len(cells) == body["runs_total"]


def poll_sweeps(
    client: httpx.Client,
    posted: dict[str, float],
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, dict[str, Any]]:
    """Poll all sweeps until settled; returns {sweep_id: last body} and stamps
    per-sweep completion wall times into the returned bodies."""
    t_deadline = time.perf_counter() + timeout_s
    pending = set(posted)
    results: dict[str, dict[str, Any]] = {}
    while pending:
        if time.perf_counter() > t_deadline:
            raise SystemExit(
                f"FAIL: timeout after {timeout_s:.0f}s with {len(pending)} unsettled "
                f"sweeps: {sorted(pending)}"
            )
        for sweep_id in sorted(pending):
            r = client.get(f"/api/v1/sweeps/{sweep_id}")
            if r.status_code != 200:
                raise SystemExit(f"FAIL: GET /sweeps/{sweep_id} -> {r.status_code}: {r.text[:300]}")
            body = r.json()
            if sweep_settled(body):
                body["_wall_s"] = time.perf_counter() - posted[sweep_id]
                results[sweep_id] = body
                pending.discard(sweep_id)
        if pending:
            time.sleep(poll_interval_s)
    return results


def check_sweep_results(
    client: httpx.Client,
    results: dict[str, dict[str, Any]],
    expected_cells: int,
    expected_replicates: int,
) -> tuple[list[str], int]:
    """Assert every sweep is fully green and every run's metrics respond.

    Returns (failure messages, number of metrics endpoints checked).
    """
    failures: list[str] = []
    metrics_checked = 0
    for sweep_id, body in sorted(results.items()):
        if body["status"] != "done":
            failures.append(f"{sweep_id}: status={body['status']} error={body['error']}")
            continue
        if body["runs_failed"] != 0 or body["runs_done"] != expected_cells:
            failures.append(
                f"{sweep_id}: runs_done={body['runs_done']} runs_failed={body['runs_failed']}"
                f" (expected {expected_cells}/0)"
            )
        for cell in body["cells"]:
            run_id = cell["run_id"]
            if cell["status"] != "done":
                run = client.get(f"/api/v1/runs/{run_id}").json()
                failures.append(
                    f"{sweep_id}/{run_id}: run status={cell['status']}"
                    f" error={str(run.get('error'))[:300]}"
                )
                continue
            prog = cell["progress"]
            if prog["completed_replicates"] != expected_replicates:
                failures.append(
                    f"{sweep_id}/{run_id}: {prog['completed_replicates']}/"
                    f"{expected_replicates} replicates"
                )
            m = client.get(f"/api/v1/runs/{run_id}/metrics")
            metrics_checked += 1
            if m.status_code != 200:
                failures.append(f"{sweep_id}/{run_id}: /metrics -> {m.status_code}: {m.text[:200]}")
            else:
                mb = m.json()
                if mb["n_replicates"] != expected_replicates:
                    failures.append(
                        f"{sweep_id}/{run_id}: metrics n_replicates="
                        f"{mb['n_replicates']} != {expected_replicates}"
                    )
    return failures, metrics_checked


def check_health_stats(stats: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    if stats.get("n", 0) == 0:
        failures.append(f"{label}: no healthz samples collected")
        return failures
    if stats["non_200"] != 0:
        failures.append(f"{label}: {stats['non_200']}/{stats['n']} healthz probes not 200/ok")
    if stats["p95_ms"] >= HEALTHZ_P95_LIMIT_MS:
        failures.append(
            f"{label}: healthz p95 {stats['p95_ms']:.1f} ms >= {HEALTHZ_P95_LIMIT_MS:.0f} ms"
        )
    return failures


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def run_phase(
    client: httpx.Client,
    base_url: str,
    *,
    label: str,
    scenario: dict[str, Any],
    grid: dict[str, Any],
    n_sweeps: int,
    timeout_s: float,
    poll_interval_s: float,
    healthz_interval_s: float,
) -> dict[str, Any]:
    n_cells = len(grid["penetrations"]) * len(grid["compliances"]) * len(grid["controllers"])
    replicates = int(grid["replicates"])
    print(
        f"\n=== {label}: {n_sweeps} sweep(s) × {n_cells} cells × {replicates} replicates "
        f"({scenario['tier']} tier) ==="
    )
    scenario_id = post_scenario(client, scenario)

    sampler = HealthSampler(base_url, interval_s=healthz_interval_s)
    sampler.start()
    t0 = time.perf_counter()
    posted: dict[str, float] = {}
    for _ in range(n_sweeps):
        posted[post_sweep(client, scenario_id, grid)] = time.perf_counter()
    t_posted = time.perf_counter() - t0
    print(f"posted {n_sweeps} sweep(s) in {t_posted:.2f}s; polling …")

    results = poll_sweeps(client, posted, timeout_s, poll_interval_s)
    wall_s = time.perf_counter() - t0
    health = sampler.stop()

    failures, metrics_checked = check_sweep_results(client, results, n_cells, replicates)
    failures += check_health_stats(health, label)

    per_sweep = sorted(round(b["_wall_s"], 2) for b in results.values())
    print(f"wall time: {wall_s:.2f}s  per-sweep (post→settled, sorted): {per_sweep}")
    print(f"healthz under load: {health}")
    print(f"metrics endpoints checked: {metrics_checked}")
    return {
        "label": label,
        "tier": scenario["tier"],
        "n_sweeps": n_sweeps,
        "cells_per_sweep": n_cells,
        "replicates_per_run": replicates,
        "total_runs": n_sweeps * n_cells,
        "post_all_s": round(t_posted, 3),
        "wall_s": round(wall_s, 2),
        "per_sweep_s": per_sweep,
        "healthz": health,
        "metrics_endpoints_checked": metrics_checked,
        "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="dev-key-change-me")
    ap.add_argument("--sweeps", type=int, default=10, help="phase-1 concurrent sweeps")
    ap.add_argument("--timeout", type=float, default=900.0, help="per-phase timeout [s]")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--healthz-interval", type=float, default=0.25)
    ap.add_argument("--json-out", default=None, help="write the raw result numbers here")
    args = ap.parse_args()

    client = httpx.Client(base_url=args.base_url, headers={"X-API-Key": args.api_key}, timeout=30.0)

    # Preflight: the stack must already be up, healthy, and on the Redis queue
    # (an inline queue would serialize everything and test nothing).
    try:
        r = client.get("/healthz")
    except httpx.HTTPError as exc:
        print(f"FAIL: cannot reach {args.base_url}/healthz ({exc}); is the stack up?")
        return 1
    if r.status_code != 200:
        print(f"FAIL: /healthz -> {r.status_code}: {r.text[:300]}")
        return 1
    health = r.json()
    if health.get("queue_kind") != "redis":
        print(f"FAIL: queue_kind={health.get('queue_kind')!r}; this test needs the Redis queue")
        return 1
    print(f"preflight ok: {health}")

    report: dict[str, Any] = {"base_url": args.base_url, "phases": []}
    all_failures: list[str] = []
    try:
        phase1 = run_phase(
            client,
            args.base_url,
            label="phase1_macro_load",
            scenario=MACRO_SCENARIO,
            grid=MACRO_GRID,
            n_sweeps=args.sweeps,
            timeout_s=args.timeout,
            poll_interval_s=args.poll_interval,
            healthz_interval_s=args.healthz_interval,
        )
        report["phases"].append(phase1)
        all_failures += phase1["failures"]

        phase2 = run_phase(
            client,
            args.base_url,
            label="phase2_micro_sumo",
            scenario=MICRO_SCENARIO,
            grid=MICRO_GRID,
            n_sweeps=1,
            timeout_s=args.timeout,
            poll_interval_s=args.poll_interval,
            healthz_interval_s=args.healthz_interval,
        )
        report["phases"].append(phase2)
        all_failures += phase2["failures"]
    finally:
        client.close()
        if args.json_out:
            with open(args.json_out, "w") as f:
                json.dump(report, f, indent=2)
            print(f"\nraw numbers -> {args.json_out}")

    print("\n=== RESULT ===")
    if all_failures:
        for f_ in all_failures:
            print(f"FAIL: {f_}")
        return 1
    total_runs = sum(p["total_runs"] for p in report["phases"])
    print(
        f"PASS: {len(report['phases'])} phases, {total_runs} runs, zero failures; "
        + "; ".join(
            f"{p['label']} wall {p['wall_s']}s healthz p95 {p['healthz'].get('p95_ms')}ms"
            for p in report["phases"]
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
