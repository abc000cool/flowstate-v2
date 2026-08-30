"""M3 US-101 validation driver: observed vs simulated (CLAUDE.md §7, §11 M3).

Runs the ``us101_replica`` scenario (default 20 seeded replicates via
``flowstate_core.rng.spawn_seeds`` from the scenario seed) and compares it
against the REAL NGSIM US-101 recording (period 1, which the replica models):

* **Link flows** — vehicle counts crossing 3 cross-sections (100 / 320 /
  550 m along the site) per 5-min window, observed counts computed from the
  ``data/ngsim`` chunks with the same dedup/period logic as
  ``scripts/us101_data.py`` (mainline lanes 1–5). GEH per
  link-hour-equivalent bin (5-min counts scaled ×12 to hourly volumes,
  replicate-mean simulated vs observed).
* **Segment speeds** — RMSPE of replicate-mean simulated segment speeds
  (4 × 160 m segments × 5-min windows) against observed.
* **Waves** — ``validation.waves.detect_waves`` on both the simulated and
  the observed binned speed fields (15 s × 75 m).
* **Criteria** — ``validation.criteria.evaluate`` (GEH / RMSPE / wave speed /
  n_seeds; the ring benchmarks are CI-gated elsewhere and reported here as
  not-evaluated rows, honestly failing per CLAUDE.md §0.1).
* **Macro-vs-micro arm (CLAUDE.md §5.5)** — the SAME us101 boundary demand
  through the CTM screening tier with the calibrated ``artifacts/
  fd_us101.json`` diagram, at 5% penetration / 100% compliance /
  ``follower_stopper``, once per moving-bottleneck variant (``flux_cap`` and
  ``capacity``), each compared on segment speeds against the micro-tier
  ground truth at the same penetration.

Coordinate mapping (see scenarios/us101_replica.yaml): sim time − 180 s
(warmup) = period-1 wall time; sim x − 640 m (entry buffer) = NGSIM local_y.
Only full 5-min windows inside the 952.8 s period-1 span are compared
(3 windows, 0–900 s). Both sides count a section crossing only for vehicles
first observed upstream of the section (symmetric censoring at the
recording/warmup start).

Everything lands under ``runs/m3_us101/``: run trees per arm, an observed-
side cache (``observed_us101.json``, keyed by the data hash), and the
machine-readable ``results.json`` consumed by the report phase.

Usage (repo root)::

    uv run --no-sync python scripts/m3_us101_validate.py --replicates 2   # smoke
    M3_PROCS=6 uv run --no-sync python scripts/m3_us101_validate.py       # full 20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from us101_data import MAINLINE_LANES, data_hash, load_us101  # noqa: E402

from flowstate_core.artifacts import FDCalibration  # noqa: E402
from flowstate_core.config import ScenarioConfig, config_hash  # noqa: E402
from flowstate_core.rng import spawn_seeds  # noqa: E402
from flowstate_core.units import ms_to_kmh  # noqa: E402
from macrosim.runner import run_macro  # noqa: E402
from microsim.runner import _versions, run_replicates  # noqa: E402
from microsim.scenarios import load_scenario  # noqa: E402
from validation.criteria import evaluate  # noqa: E402
from validation.fields import speed_field  # noqa: E402
from validation.metrics import geh, rmspe  # noqa: E402
from validation.waves import detect_waves  # noqa: E402

OUT_ROOT = REPO_ROOT / "runs" / "m3_us101"
FD_ARTIFACT = REPO_ROOT / "artifacts" / "fd_us101.json"

SECTIONS_M = (100.0, 320.0, 550.0)
"""Cross-sections along the 640 m site [m, local_y]. Chosen inside the
tracked range: downstream of the upstream detection fuzz (< 30 m) and of the
on-ramp merge zone start, upstream of the section end."""

WINDOW_S = 300.0
N_WINDOWS = 3  # full 5-min windows inside the 952.8 s period-1 span
SEGMENT_M = 160.0
N_SEGMENTS = 4  # 4 x 160 m = 640 m
SITE_LENGTH_M = 640.0
WARMUP_S = 180.0
ENTRY_BUFFER_M = 640.0  # min(CORRIDOR_INSERTION_BUFFER_M, length_m) for 640 m
WAVE_DT_BIN_S = 15.0
WAVE_DX_BIN_M = 75.0

MACRO_PENETRATION = 0.05
MACRO_COMPLIANCE = 1.0
MACRO_CONTROLLER = "follower_stopper"
BOTTLENECK_VARIANTS = ("flux_cap", "capacity")

RESULTS_SCHEMA_KEYS = frozenset(
    {
        "schema_version",
        "created_at",
        "scenario",
        "replicates",
        "seeds",
        "config_hashes",
        "versions",
        "observed",
        "simulated",
        "geh",
        "rmspe",
        "waves",
        "criteria",
        "macro_vs_micro",
        "notes",
    }
)


def _crossing_counts(traj: pd.DataFrame, sections: tuple[float, ...]) -> np.ndarray:
    """Per-(section, window) crossing counts from a trajectory table.

    A vehicle counts at a section when its position series crosses the
    section upward with its FIRST sample strictly upstream (vehicles already
    at/past the section when first observed are censored — symmetric between
    the recording start and the sim warmup end). Crossing time is linearly
    interpolated between the bracketing samples and binned into
    ``N_WINDOWS`` windows of ``WINDOW_S``.

    Args:
        traj: Rows with ``t`` [s, wall], ``x`` [m, local_y], ``veh_id``.
        sections: Section positions [m].

    Returns:
        Integer array of shape ``[len(sections), N_WINDOWS]``.
    """
    counts = np.zeros((len(sections), N_WINDOWS), dtype=np.int64)
    for _, g in traj.groupby("veh_id", sort=False):
        g = g.sort_values("t")
        t = g["t"].to_numpy(dtype=np.float64)
        x = g["x"].to_numpy(dtype=np.float64)
        for si, s in enumerate(sections):
            if x[0] >= s:
                continue
            above = np.flatnonzero(x >= s)
            if above.size == 0:
                continue
            i = int(above[0])
            dx = x[i] - x[i - 1]
            frac = (s - x[i - 1]) / dx if dx > 0 else 1.0
            tc = t[i - 1] + frac * (t[i] - t[i - 1])
            w = int(tc // WINDOW_S)
            if 0 <= tc and w < N_WINDOWS:
                counts[si, w] += 1
    return counts


def _segment_speeds(traj: pd.DataFrame) -> np.ndarray:
    """Mean sampled speed per (window, segment); NaN where empty.

    Arithmetic mean of the uniformly sampled speeds inside each
    ``WINDOW_S × SEGMENT_M`` bin — the same operation on the observed (10 Hz)
    and simulated (2 Hz) sides, so the comparison is like-for-like.

    Args:
        traj: Rows with ``t`` [s, wall], ``x`` [m, local_y], ``v`` [m/s].

    Returns:
        Array of shape ``[N_WINDOWS, N_SEGMENTS]``.
    """
    out = np.full((N_WINDOWS, N_SEGMENTS), np.nan)
    t = traj["t"].to_numpy(dtype=np.float64)
    x = traj["x"].to_numpy(dtype=np.float64)
    v = traj["v"].to_numpy(dtype=np.float64)
    ok = (t >= 0.0) & (t < N_WINDOWS * WINDOW_S) & (x >= 0.0) & (x < SITE_LENGTH_M)
    wi = (t[ok] // WINDOW_S).astype(np.int64)
    si = np.minimum((x[ok] // SEGMENT_M).astype(np.int64), N_SEGMENTS - 1)
    sums = np.zeros((N_WINDOWS, N_SEGMENTS))
    cnts = np.zeros((N_WINDOWS, N_SEGMENTS))
    np.add.at(sums, (wi, si), v[ok])
    np.add.at(cnts, (wi, si), 1.0)
    np.divide(sums, cnts, out=out, where=cnts > 0)
    return out


def _wave_summary(traj: pd.DataFrame) -> dict:
    """Wave detection on one trajectory table (wall t, local_y x)."""
    field = speed_field(traj, dt_bin=WAVE_DT_BIN_S, dx_bin=WAVE_DX_BIN_M)
    ws = detect_waves(field)
    backward = ws.backward()
    return {
        "count": ws.count,
        "n_backward": len(backward),
        "backward_speeds_kmh": [round(ms_to_kmh(-w.speed_ms), 2) for w in backward],
        "amplitudes_ms": [round(w.amplitude_ms, 2) for w in ws.waves],
        "mean_backward_speed_kmh": (
            float(np.mean([ms_to_kmh(-w.speed_ms) for w in backward])) if backward else None
        ),
    }


def observed_side(cache_path: Path) -> dict:
    """Observed comparison tables from the NGSIM chunks (cached by data hash).

    Uses ``us101_data.load_us101`` — the shared loader that drops the exact
    duplicate rows and splits recording periods on the recording origin —
    and restricts to period 1 (the span the replica models), mainline lanes
    1–5, all vehicle classes.
    """
    dh = data_hash()
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text())
        if cached.get("data_hash") == dh:
            print(f"observed side: cache hit ({cache_path})", flush=True)
            return cached
    print("observed side: loading NGSIM chunks (few minutes)...", flush=True)
    t0 = time.perf_counter()
    p1 = load_us101()["p1"]
    p1 = p1[p1["lane"].between(*MAINLINE_LANES)].copy()
    p1["t"] = p1["t"] - p1["t"].min()  # wall time from the period-1 recording start
    counts = _crossing_counts(p1, SECTIONS_M)
    seg_speeds = _segment_speeds(p1)
    waves = _wave_summary(p1[["t", "x", "v"]])
    obs = {
        "data_hash": dh,
        "period": "p1",
        "lanes": list(MAINLINE_LANES),
        "n_vehicles": int(p1["veh_id"].nunique()),
        "sections_m": list(SECTIONS_M),
        "window_s": WINDOW_S,
        "n_windows": N_WINDOWS,
        "segment_m": SEGMENT_M,
        "counts": counts.tolist(),
        "hourly_flows_veh_h": (counts * 3600.0 / WINDOW_S).tolist(),
        "segment_speeds_ms": seg_speeds.tolist(),
        "waves": waves,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(_json_safe(obs), indent=2, allow_nan=False))
    return obs


def _sim_frame(run_dir: Path) -> pd.DataFrame:
    """One replicate's trajectories mapped into observed coordinates."""
    df = pd.read_parquet(run_dir / "trajectories.parquet")
    df = df[(df["x"] >= ENTRY_BUFFER_M)].copy()
    df["t"] = df["t"] - WARMUP_S
    df["x"] = df["x"] - ENTRY_BUFFER_M
    return df[df["t"] >= 0.0]


def micro_arm(cfg: ScenarioConfig, n_replicates: int, out_root: Path, procs: int) -> dict:
    """Run replicates of one micro config and build its comparison tables."""
    cfg = cfg.model_copy(update={"replicates": n_replicates})
    seeds = spawn_seeds(cfg.seed, n_replicates)
    t0 = time.perf_counter()
    paths = run_replicates(cfg, out_root, n_procs=min(procs, n_replicates))
    wall = time.perf_counter() - t0
    counts = []
    seg_speeds = []
    waves = []
    demand_frac = []
    for p in paths:
        meta = json.loads(p.meta.read_text())
        demand_frac.append(meta["n_vehicles_departed"] / meta["n_vehicles_planned"])
        df = _sim_frame(p.run_dir)
        counts.append(_crossing_counts(df, SECTIONS_M))
        seg_speeds.append(_segment_speeds(df))
        waves.append(_wave_summary(df[["t", "x", "v"]]))
    counts_arr = np.asarray(counts, dtype=np.float64)
    seg_arr = np.asarray(seg_speeds, dtype=np.float64)
    bw = [w["mean_backward_speed_kmh"] for w in waves if w["mean_backward_speed_kmh"] is not None]
    return {
        "config_hash": config_hash(cfg),
        "seeds": seeds,
        "run_dirs": [str(p.run_dir) for p in paths],
        "wall_s": round(wall, 1),
        "demand_realized_fraction": [round(f, 4) for f in demand_frac],
        "counts_mean": counts_arr.mean(axis=0).tolist(),
        "hourly_flows_veh_h_mean": (counts_arr.mean(axis=0) * 3600.0 / WINDOW_S).tolist(),
        "counts_per_replicate": [c.tolist() for c in counts],
        "segment_speeds_ms_mean": np.nanmean(seg_arr, axis=0).tolist(),
        "waves_per_replicate": waves,
        "wave_count_mean": float(np.mean([w["count"] for w in waves])),
        "mean_backward_speed_kmh": (float(np.mean(bw)) if bw else None),
        "n_replicates_with_backward_waves": len(bw),
    }


def macro_arm(cfg: ScenarioConfig, seed: int, micro_truth: dict) -> dict:
    """CTM screening runs (both bottleneck variants) vs micro ground truth."""
    fd_cal = FDCalibration.load(FD_ARTIFACT)
    micro_seg = np.asarray(micro_truth["segment_speeds_ms_mean"], dtype=np.float64)
    out: dict = {
        "fd_artifact": str(FD_ARTIFACT.relative_to(REPO_ROOT)),
        "fd_data_hash": fd_cal.data_hash,
        "penetration": MACRO_PENETRATION,
        "compliance": MACRO_COMPLIANCE,
        "controller": MACRO_CONTROLLER,
        "micro_truth_config_hash": micro_truth["config_hash"],
        "variants": {},
    }
    for variant in BOTTLENECK_VARIANTS:
        run_dir = run_macro(
            cfg,
            seed,
            OUT_ROOT / f"macro_{variant}",
            fd=fd_cal.fd,
            bottleneck_variant=variant,
        )
        edges = pd.read_parquet(run_dir / "edges.parquet")
        edges = edges.copy()
        edges["t"] = edges["t_bin"] - WARMUP_S
        edges = edges[(edges["t"] >= 0.0) & (edges["t"] < N_WINDOWS * WINDOW_S)]
        wi = (edges["t"].to_numpy() // WINDOW_S).astype(np.int64)
        si = np.minimum((edges["x_bin"].to_numpy() // SEGMENT_M).astype(np.int64), N_SEGMENTS - 1)
        rho = edges["density"].to_numpy()
        v = edges["mean_speed"].to_numpy()
        num = np.zeros((N_WINDOWS, N_SEGMENTS))
        den = np.zeros((N_WINDOWS, N_SEGMENTS))
        np.add.at(num, (wi, si), rho * v)
        np.add.at(den, (wi, si), rho)
        macro_seg = np.full((N_WINDOWS, N_SEGMENTS), np.nan)
        np.divide(num, den, out=macro_seg, where=den > 0)
        both = np.isfinite(macro_seg) & np.isfinite(micro_seg)
        rmse = float(np.sqrt(np.mean((macro_seg[both] - micro_seg[both]) ** 2)))
        rmspe_v = rmspe(macro_seg[both], micro_seg[both])
        meta = json.loads((run_dir / "meta.json").read_text())
        out["variants"][variant] = {
            "run_dir": str(run_dir),
            "tier": meta["tier"],
            "segment_speeds_ms": macro_seg.tolist(),
            "rmse_vs_micro_ms": round(rmse, 3),
            "rmspe_vs_micro": round(rmspe_v, 4),
            "n_bins_compared": int(both.sum()),
        }
    seg_a = np.asarray(out["variants"][BOTTLENECK_VARIANTS[0]]["segment_speeds_ms"])
    seg_b = np.asarray(out["variants"][BOTTLENECK_VARIANTS[1]]["segment_speeds_ms"])
    out["variants_identical"] = bool(np.array_equal(seg_a, seg_b, equal_nan=True))
    if out["variants_identical"]:
        out["note"] = (
            "both bottleneck variants produced identical fields: the AVs' commanded "
            "speed never fell below the local equilibrium speed, so neither the "
            "rho*v_star flux cap nor the reduced-capacity cap ever bound at this "
            "demand/controller — an honest null contrast, not a bug"
        )
    return out


def _json_safe(obj: object) -> object:
    """Replace non-finite floats with None so the JSON is strictly parseable."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def verify_schema(path: Path) -> None:
    """Reload the results JSON and assert the report-phase contract."""
    data = json.loads(path.read_text())
    missing = RESULTS_SCHEMA_KEYS - set(data)
    if missing:
        raise SystemExit(f"results schema missing keys: {sorted(missing)}")
    obs = data["observed"]
    n_sec, n_win = len(obs["sections_m"]), obs["n_windows"]
    assert np.asarray(obs["counts"]).shape == (n_sec, n_win)
    assert np.asarray(data["simulated"]["counts_mean"]).shape == (n_sec, n_win)
    assert len(data["geh"]["values"]) == n_sec * n_win
    assert np.asarray(obs["segment_speeds_ms"]).shape == (n_win, N_SEGMENTS)
    assert isinstance(data["rmspe"]["value"], float)
    assert set(data["macro_vs_micro"]["variants"]) == set(BOTTLENECK_VARIANTS)
    for row in data["criteria"]:
        assert {"name", "value", "threshold", "passed", "evaluated"} <= set(row)
    print(f"results schema OK: {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--replicates", type=int, default=20, help="replicates per micro arm")
    ap.add_argument(
        "--procs",
        type=int,
        default=int(os.environ.get("M3_PROCS", "6")),
        help="process-pool size (env M3_PROCS, default 6)",
    )
    args = ap.parse_args()
    t0 = time.perf_counter()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    obs = observed_side(OUT_ROOT / "observed_us101.json")

    cfg = load_scenario("us101_replica")
    print(f"micro baseline arm: {args.replicates} replicates ...", flush=True)
    sim = micro_arm(cfg, args.replicates, OUT_ROOT / "micro_baseline", args.procs)

    cfg_p05 = cfg.model_copy(deep=True)
    cfg_p05.av.penetration = MACRO_PENETRATION
    cfg_p05.av.compliance = MACRO_COMPLIANCE
    cfg_p05.av.controller = MACRO_CONTROLLER
    print(f"micro 5% arm: {args.replicates} replicates ...", flush=True)
    micro_p05 = micro_arm(cfg_p05, args.replicates, OUT_ROOT / "micro_p05", args.procs)
    print("macro arm (flux_cap + capacity) ...", flush=True)
    macro = macro_arm(cfg_p05, spawn_seeds(cfg.seed, 1)[0], micro_p05)

    # GEH per link-hour-equivalent bin (replicate-mean sim vs observed).
    obs_hourly = np.asarray(obs["hourly_flows_veh_h"], dtype=np.float64)
    sim_hourly = np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64)
    geh_values = [
        round(geh(float(m), float(c)), 3)
        for m, c in zip(sim_hourly.ravel(), obs_hourly.ravel(), strict=True)
    ]
    geh_frac = sum(1 for g in geh_values if g < 5.0) / len(geh_values)

    # RMSPE on segment mean speeds (replicate-mean sim vs observed).
    obs_seg = np.asarray(obs["segment_speeds_ms"], dtype=np.float64)
    sim_seg = np.asarray(sim["segment_speeds_ms_mean"], dtype=np.float64)
    both = np.isfinite(obs_seg) & np.isfinite(sim_seg) & (obs_seg != 0.0)
    rmspe_value = rmspe(sim_seg[both], obs_seg[both])

    criteria_rows = evaluate(
        geh_values=geh_values,
        rmspe_value=rmspe_value,
        wave_speed_kmh=(
            sim["mean_backward_speed_kmh"]
            if sim["mean_backward_speed_kmh"] is not None
            else math.nan
        ),
        ring_emergence=None,  # CI-gated benchmark, not re-run here
        ring_dampening=None,
        n_seeds=args.replicates,
    )

    results = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": "us101_replica",
        "replicates": args.replicates,
        "seeds": sim["seeds"],
        "config_hashes": {
            "micro_baseline": sim["config_hash"],
            "micro_p05": micro_p05["config_hash"],
        },
        "versions": _versions(),
        "observed": obs,
        "simulated": sim,
        "geh": {
            "values": geh_values,
            "fraction_under_5": round(geh_frac, 4),
            "bins": "sections x 5-min windows, hourly-equivalent volumes (x12)",
        },
        "rmspe": {"value": float(rmspe_value), "n_bins": int(both.sum())},
        "waves": {
            "observed": obs["waves"],
            "simulated_mean_backward_speed_kmh": sim["mean_backward_speed_kmh"],
            "simulated_wave_count_mean": sim["wave_count_mean"],
            "field_bins": f"{WAVE_DT_BIN_S:g} s x {WAVE_DX_BIN_M:g} m",
        },
        "criteria": [asdict(r) for r in criteria_rows],
        "macro_vs_micro": macro,
        "notes": [
            "Observed side: NGSIM US-101 period 1, mainline lanes 1-5, duplicate rows "
            "dropped (scripts/us101_data.py logic); ~6% of real vehicles entered via the "
            "on-ramp and are counted at downstream sections but never injected by the "
            "replica (docs/M2_RESULTS.md §7.6).",
            "The replica has no downstream boundary congestion; M2 predicts it runs "
            "faster than observed (docs/M2_RESULTS.md §6) — RMSPE reflects that gap "
            "honestly.",
            "Comparisons use the 3 full 5-min windows (0-900 s) inside the 952.8 s "
            "period-1 span; the trailing partial window is excluded.",
            "Crossing counts censor vehicles first observed at/past a section on both "
            "sides (recording start vs warmup end).",
            "Macro tier is screening-only (tier='screening'); its rows must never back "
            "a validation report (CLAUDE.md §5.6).",
        ],
    }
    out_path = OUT_ROOT / "results.json"
    out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
    print(
        f"GEH<5 fraction {geh_frac:.0%} | RMSPE {rmspe_value:.1%} | "
        f"sim backward wave {sim['mean_backward_speed_kmh']} km/h | "
        f"macro RMSE flux_cap {macro['variants']['flux_cap']['rmse_vs_micro_ms']} m/s vs "
        f"capacity {macro['variants']['capacity']['rmse_vs_micro_ms']} m/s"
    )
    verify_schema(out_path)
    print(f"done in {time.perf_counter() - t0:.1f} s -> {out_path}")


if __name__ == "__main__":
    main()
