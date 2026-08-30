# M5 Load Test — 10 Concurrent Sweep Jobs Through the Docker Stack

M5 hardening gate (CLAUDE.md §11): the full compose stack (API + Redis + RQ
workers) under 10 concurrent sweep jobs, plus one micro-tier (SUMO) sweep
through the same queue. Executed 2026-08-29 with `scripts/m5_load_test.py`
against the release-state 2.0.0 image; every number below is copied from
that run's JSON output (no free-text numbers). Result: **PASS — 42/42 runs
done, 0 failed, healthz p95 13.3 ms (limit 500 ms).**

## 1. Setup

Machine (host):

| | |
|---|---|
| CPU (`sysctl -n machdep.cpu.brand_string`) | Apple M4 |
| Cores (`sysctl -n hw.ncpu`) | 10 |
| RAM (`sysctl -n hw.memsize`) | 17 179 869 184 B (16 GiB) |
| Docker runtime | colima 0.10.3 (macOS Virtualization.Framework, aarch64 VM: **4 vCPU, 5.77 GiB** — the stack ran inside this VM) |
| Docker / Compose | 29.7.2 / 5.5.0 (server 29.5.2, Ubuntu 24.04.4 VM) |

Stack (versions from the run artifacts' `meta.json`): Python 3.12.14,
eclipse-sumo/libsumo 1.27.1, numpy 2.5.2, pandas 3.0.5, pyarrow 25.0.1,
flowstate packages 2.0.0. One image for API and workers; SQLite metadata
(WAL, 30 s busy timeout) + Parquet payloads on the shared `flowstate-runs`
volume.

Commands:

```sh
docker compose up -d --build --scale worker=2      # api + redis + 2 workers
uv run --no-sync python scripts/m5_load_test.py    # brings nothing up itself
docker compose down                                 # afterwards
```

The script asserts, and this run satisfied: every sweep `done` with zero
failed runs; every child run `done` with the full replicate count; every
`/runs/{id}/metrics` endpoint returning 200; `/healthz` sampled every 250 ms
*while jobs ran* returning 200 with p95 < 500 ms; per-phase timeout 900 s.

## 2. Phase 1 — 10 concurrent macro sweeps

Workload: 10 sweeps POSTed back-to-back, each a 2×2 grid (penetration
{0.02, 0.05} × compliance {0.5, 1.0} × FollowerStopper) × 3 replicates on a
2 km single-lane macro-tier corridor (20 CTM cells, 300 s horizon) — 40 runs
/ 120 replicates total, individually tiny by design so the test exercises
queue/API/SQLite **concurrency**, not compute: 10 sweep fan-out jobs + 40 run
jobs drained by 2 workers.

| Measurement | Value |
|---|---|
| POST all 10 sweeps | 0.06 s |
| Wall time, first POST → all 40 runs settled | **12.27 s** |
| Per-sweep (POST → all its runs done), sorted | 3.29, 4.39, 6.58, 6.67, 7.77, 8.82, 8.89, 9.98, 11.09, 12.21 s |
| Runs done / failed | **40 / 0** |
| `/runs/{id}/metrics` responses | 40/40 HTTP 200, each with n = 3 replicates |
| `/healthz` during load (n = 47 samples) | p50 4.9 ms, **p95 13.3 ms**, max 14.3 ms, 0 non-200 |

The per-sweep spread (3.3 → 12.2 s) is the two workers draining the shared
queue — sweeps settle in near-FIFO order, none starve, and the API stays
flat-latency throughout.

## 3. Phase 2 — micro-tier (SUMO) sweep under queue concurrency

Workload: one sweep, 2 cells (penetration 0.05 × compliance {0.5, 1.0} ×
FollowerStopper) × 2 replicates, on the 230 m / 22-vehicle Sugiyama ring,
300 s at 0.5 s steps — real libsumo simulations inside the queued worker
containers, both runs executing concurrently on the two workers.

| Measurement | Value |
|---|---|
| Wall time, POST → both runs settled | **2.11 s** |
| Runs done / failed | **2 / 0** |
| `/runs/{id}/metrics` responses | 2/2 HTTP 200, each with n = 2 replicates |
| `/healthz` during load (n = 9 samples) | p50 4.7 ms, p95 11.9 ms, max 11.9 ms, 0 non-200 |
| Per-replicate SUMO wall time (from `meta.json`) | 0.32–0.38 s (realtime factor 797–953×) |

2.11 s is honest, not suspicious: a 22-vehicle 600-step ring is ~13 200
vehicle-updates, which libsumo executes in ~0.3 s per replicate; artifact
inspection confirmed full trajectories (13 200 rows/replicate, 22 vehicles,
60 edge bins) with `eclipse-sumo 1.27.1` recorded in every `meta.json`.

## 4. Stability findings

- **Zero failures under load, zero API/store fixes needed.** All 53 RQ jobs
  (10 + 1 sweep fan-outs, 42 runs) logged `Job OK`; worker logs contain no
  errors, tracebacks, or RQ timeouts; container restart counts stayed 0 for
  both workers and the API.
- **SQLite under concurrency held.** Two worker processes plus the API
  (polling every 1 s, healthz every 250 ms) shared the WAL-mode metadata
  store through the load with no `database is locked` errors — the fresh
  connection-per-operation + WAL + 30 s busy-timeout design in `api.store`
  needed no hardening changes.
- **API responsiveness under load**: healthz p95 13.3 ms against the 500 ms
  budget (37× headroom), no non-200 responses at any point.
- **pyarrow 24.0.0 rejected during this hardening pass.** An earlier run of
  this same test also passed on a pyarrow 24.0.0 image (Linux containers
  are unaffected), but pinning the workspace to 24.0.0 — the only version
  that silences libsumo's import-time libarrow-mismatch warning —
  intermittently **livelocks** libsumo+pyarrow processes on macOS (hard
  spin in mimalloc inside the duplicated `libarrow.2400.dylib`;
  `tests/test_microsim` hung >50 min where it normally takes 8 s). The
  workspace therefore constrains `pyarrow>=18,!=24.0.0` and ships 25.0.1;
  details in CHANGELOG.md §2.0.0-hardening.

## 5. Reproduce

```sh
docker compose up -d --build --scale worker=2
uv run --no-sync python scripts/m5_load_test.py --json-out /tmp/m5.json
docker compose down
```

Numbers will vary with hardware; the assertions (zero failed runs, all
metrics endpoints 200, healthz p95 < 500 ms) are what must hold.
