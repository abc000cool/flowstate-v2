"""ROADMAP §1.4 — validate the I-24 replica against the I-24 MOTION day.

Runs ``i24_replica`` (demand as tracked) and ``i24_replica_corrected``
(demand divided by the apparent tracking coverage, docs/I24_DATA.md §4) with
20 seeded replicates each and compares them with the westbound recording over
the study period (06:30–08:30 CST) on the measured span (data x ∈ [0, 5492) m,
MM 62.7 → Bell Road). Same structure as ``scripts/m3_us101_validate.py``:

* **Link flows** — fragment crossings at six high-coverage sections (data
  x = 200, 1000, 2200, 3200, 4800, 5400 m; the 400 m and 2400 m coverage
  holes are avoided) × 24 five-minute windows, ×12 to hourly volumes, GEH per
  bin against the replicate-mean simulated crossings. The observed counts are
  the tracked counts (lower bounds); the corrected arm is additionally scored
  against counts divided by the per-window coverage factor — both tables are
  written, both labeled.
* **Segment speeds** — RMSPE of replicate-mean simulated mean sampled speed
  against observed, 10 × 549 m segments × 24 windows (speeds are
  coverage-robust; one observed side serves both arms).
* **Waves** — every recipe in ``validation.waves.WAVE_DETECTORS`` (standard
  40 km/h on 15 s × 75 m bins, the 25 km/h / 10 s × 50 m stripe variant of
  the M3 analysis, relative 0.5 × p90, and the slant-stack estimate) on
  fields of the measured span, identically on both sides; the criteria row
  is measured with the profile's ``wave_detector`` and names it. This is the
  test of docs/WAVE_SPEED_DIAGNOSIS.md's prediction that a long, congested
  corridor lets the calibrated fleet reach the 14–22 km/h band.
* **Criteria** — ``validation.criteria.evaluate`` (GEH / RMSPE / wave speed /
  ring emergence / ring dampening / n_seeds) and replicate metrics with 95% t
  CIs. The ring rows are evaluated for real: ``--ring-seeds N`` (default 20)
  runs ``ring_sugiyama`` as shipped and with one FollowerStopper vehicle for
  ``spawn_seeds(<ring scenario seed>, N)`` through
  ``validation.ring_benchmark.evaluate_ring_benchmark`` — the CI gate's
  checks (tests/test_microsim/test_microsim_ring_gate.py) applied to every
  seed — and both arms' JSON carry the ``ring`` block. ``--ring-seeds 0``
  skips it (the rows then report not evaluated, failing, per CLAUDE.md §0.1);
  ``--ring-only`` evaluates the ring, writes
  ``runs/i24_validation/ring/ring_benchmark.json`` and exits.

Coordinates: sim x = a + b · data x (``artifacts/i24_replica_inputs.json``
``geometry.sim_x_of_data_x``), sim t = data t − 1800 + 600.

Outputs: ``runs/i24_validation/<arm>/`` run trees and
``artifacts/i24_validation_<arm>.json`` (schema of the M3 results files plus
the coverage tables). The scenario YAMLs used are copied next to them.

Usage (repo root)::

    uv run --no-sync python scripts/i24_validate.py --replicates 2 --arms tracked   # smoke
    uv run --no-sync python scripts/i24_validate.py --procs 8                        # full
    uv run --no-sync python scripts/i24_validate.py --ring-only --ring-seeds 3       # ring rows
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WARMUP_S, WINDOW_S, crossings_per_window
from i24_data import REPO_ROOT, clock, data_hash, load_mainline

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_replicates
from microsim.scenarios import load_scenario
from validation.criteria import evaluate, get_profile
from validation.fields import speed_field
from validation.metrics import aggregate, compute_metrics, geh, rmspe
from validation.waves import WAVE_DETECTORS, WaveDetector

OUT_ROOT = REPO_ROOT / "runs" / "i24_validation"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"

SECTIONS_M = (200.0, 1000.0, 2200.0, 3200.0, 4800.0, 5400.0)
N_SEGMENTS = 10
PROFILE = get_profile("fhwa_default")
CRITERION_DETECTOR = PROFILE.wave_detector
STANDARD = WAVE_DETECTORS["standard"]
STRIPE = WAVE_DETECTORS["stripe"]
OBSERVED_CACHE_VERSION = 2  # v2: waves_by_detector (all WAVE_DETECTORS recipes)

ARMS = {
    "tracked": "i24_replica",
    "corrected": "i24_replica_corrected",
    "speedcal": "i24_replica_speedcal",  # FHWA step-2 demand scale (docs/I24_CAPACITY.md)
    "ramps": "i24_replica_speedcal_ramps",  # step 3: ramps, boundary, gap acceptance (§6.1)
}


def _inputs() -> dict:
    return json.loads(INPUTS.read_text())


def _span() -> tuple[float, float]:
    lo, hi = _inputs()["geometry"]["measured_span_data_x_m"]
    return float(lo), float(hi)


def _segment_speeds(traj: pd.DataFrame, span_hi: float, n_win: int) -> np.ndarray:
    """Mean sampled speed per (window, segment) on the measured span; NaN if empty."""
    seg_m = span_hi / N_SEGMENTS
    out = np.full((n_win, N_SEGMENTS), np.nan)
    t = traj["t"].to_numpy(dtype=np.float64)
    x = traj["x"].to_numpy(dtype=np.float64)
    v = traj["v"].to_numpy(dtype=np.float64)
    ok = (t >= 0.0) & (t < n_win * WINDOW_S) & (x >= 0.0) & (x < span_hi)
    wi = (t[ok] // WINDOW_S).astype(np.int64)
    si = np.minimum((x[ok] // seg_m).astype(np.int64), N_SEGMENTS - 1)
    sums = np.zeros((n_win, N_SEGMENTS))
    cnts = np.zeros((n_win, N_SEGMENTS))
    np.add.at(sums, (wi, si), v[ok])
    np.add.at(cnts, (wi, si), 1.0)
    np.divide(sums, cnts, out=out, where=cnts > 0)
    return out


def _wave_summary(traj: pd.DataFrame, span_hi: float, detector: WaveDetector) -> dict:
    """One detector's reading of the site-clipped field (its own bins)."""
    clipped = traj[(traj["x"] >= 0.0) & (traj["x"] < span_hi)]
    field = speed_field(clipped, dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m)
    m = detector.measure(field)
    bw = list(m.backward_speeds_kmh)
    amplitudes = (
        [round(w.amplitude_ms, 2) for w in detector.detect(field).waves]
        if detector.method != "stack"
        else []
    )
    return {
        "detector": detector.name,
        "detector_description": detector.describe(),
        "count": m.n_components,
        "n_backward": m.n_backward,
        "backward_speeds_kmh": [round(v, 2) for v in bw],
        "amplitudes_ms": amplitudes,
        "mean_backward_speed_kmh": m.speed_kmh if math.isfinite(m.speed_kmh) else None,
        "median_backward_speed_kmh": float(np.median(bw)) if bw else None,
        "frac_backward_in_band": m.in_band_fraction() if bw else None,
        "threshold_kmh": m.threshold_kmh if math.isfinite(m.threshold_kmh) else None,
        "stack_contrast": m.contrast if math.isfinite(m.contrast) else None,
        "note": m.note,
    }


def _wave_summaries(traj: pd.DataFrame, span_hi: float) -> dict[str, dict]:
    """Every registered detector's reading, keyed by detector name."""
    return {name: _wave_summary(traj, span_hi, d) for name, d in WAVE_DETECTORS.items()}


def observed_side(cache_path: Path) -> dict:
    """Observed comparison tables on the study period / measured span (cached)."""
    dh = data_hash()
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text())
        if cached.get("data_hash") == dh and cached.get("cache_version") == OBSERVED_CACHE_VERSION:
            print(f"observed side: cache hit ({cache_path})", flush=True)
            return cached
    print("observed side: building from the I-24 Parquet ...", flush=True)
    t0 = time.perf_counter()
    inputs = _inputs()
    span_lo, span_hi = _span()
    t_lo, t_hi = T_STUDY_LO_S, T_STUDY_HI_S
    n_win = round((t_hi - t_lo) / WINDOW_S)
    df = load_mainline(
        t_range_s=(t_lo - 60.0, t_hi + 60.0),
        x_range_m=(span_lo - 200.0, span_hi + 200.0),
        columns=["t", "veh_id", "x", "v"],
    )
    counts = np.array([crossings_per_window(df, s, t_lo, t_hi) for s in SECTIONS_M])
    cov_rows = inputs["coverage"]["rows"]
    cov_win = int(inputs["coverage"]["window_s"] // WINDOW_S)
    factors = np.array(
        [cov_rows[min(i // cov_win, len(cov_rows) - 1)]["coverage_used"] for i in range(n_win)]
    )
    study = df[(df["t"] >= t_lo) & (df["t"] < t_hi)].copy()
    study["t"] = study["t"] - t_lo
    seg = _segment_speeds(study, span_hi, n_win)
    waves_by_detector = _wave_summaries(study[["t", "x", "v"]], span_hi)
    obs = {
        "cache_version": OBSERVED_CACHE_VERSION,
        "data_hash": dh,
        "period": f"{clock(t_lo)}-{clock(t_hi)} CST",
        "t_range_s": [t_lo, t_hi],
        "span_data_x_m": [span_lo, span_hi],
        "sections_m": list(SECTIONS_M),
        "window_s": WINDOW_S,
        "n_windows": n_win,
        "n_segments": N_SEGMENTS,
        "segment_m": span_hi / N_SEGMENTS,
        "counts_tracked": counts.tolist(),
        "hourly_flows_veh_h_tracked": (counts * 3600.0 / WINDOW_S).tolist(),
        "coverage_factor_per_window": factors.round(4).tolist(),
        "hourly_flows_veh_h_corrected": (counts * 3600.0 / WINDOW_S / factors[None, :])
        .round(1)
        .tolist(),
        "segment_speeds_ms": seg.tolist(),
        "waves": waves_by_detector[STANDARD.name],
        "waves_stripe": waves_by_detector[STRIPE.name],
        "waves_stripe_params": {
            "v_jam_thresh_kmh": STRIPE.v_jam_thresh_ms * 3.6,
            "dt_bin_s": STRIPE.dt_bin_s,
            "dx_bin_m": STRIPE.dx_bin_m,
        },
        "waves_by_detector": waves_by_detector,
        "n_fragments": int(study["veh_id"].nunique()),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(_json_safe(obs), indent=2, allow_nan=False))
    return obs


def _sim_frame(run_dir: Path, a: float, b: float) -> pd.DataFrame:
    """One replicate's trajectories in observed coordinates (data x, study t)."""
    df = pd.read_parquet(run_dir / "trajectories.parquet")
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - WARMUP_S
    return df[df["t"] >= 0.0]


def _analyze_replicate(payload: tuple[str, float, float, float, float, int]) -> dict:
    """Per-replicate comparison tables + metrics (process-pool worker)."""
    run_dir_s, a, b, span_lo, span_hi, n_win = payload
    from dataclasses import asdict as _asdict

    run_dir = Path(run_dir_s)
    meta = json.loads((run_dir / "meta.json").read_text())
    df = _sim_frame(run_dir, a, b)
    return {
        "realized": meta["n_vehicles_departed"] / meta["n_vehicles_planned"],
        "ramps": meta.get("ramps"),
        "counts": np.array(
            [crossings_per_window(df, s, 0.0, n_win * WINDOW_S) for s in SECTIONS_M]
        ).tolist(),
        "seg": _segment_speeds(df, span_hi, n_win).tolist(),
        "waves_by_detector": _wave_summaries(df[["t", "x", "v"]], span_hi),
        "metrics": _asdict(
            compute_metrics(run_dir, x_ref=a + b * 2200.0, span=(a + b * span_lo, a + b * span_hi))
        ),
    }


def _existing_replicates(cfg: ScenarioConfig, out_root: Path, seeds: list[int]) -> list | None:
    """RunPaths for replicates already on disk (all seeds complete), else None."""
    from microsim.runner import RunPaths

    root = out_root / config_hash(cfg)
    paths = []
    for seed in seeds:
        d = root / str(seed)
        if not (d / "meta.json").is_file() or not (d / "trajectories.parquet").is_file():
            return None
        paths.append(
            RunPaths(
                run_dir=d,
                trajectories=d / "trajectories.parquet",
                edges=d / "edges.parquet",
                meta=d / "meta.json",
            )
        )
    return paths


def micro_arm(
    cfg: ScenarioConfig,
    n_replicates: int,
    out_root: Path,
    procs: int,
    obs: dict,
    analysis_procs: int = 6,
    reuse_runs: bool = False,
) -> dict:
    """Run one arm's replicates (or reuse complete ones on disk), then analyse them."""
    from validation.metrics import Metrics

    inputs = _inputs()
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    span_lo, span_hi = _span()
    n_win = obs["n_windows"]
    cfg = cfg.model_copy(update={"replicates": n_replicates})
    seeds = spawn_seeds(cfg.seed, n_replicates)
    t0 = time.perf_counter()
    paths = _existing_replicates(cfg, out_root, seeds) if reuse_runs else None
    if paths is None:
        paths = run_replicates(cfg, out_root, n_procs=min(procs, n_replicates))
    else:
        print(f"  reusing {len(paths)} complete replicates under {out_root}", flush=True)
    wall = time.perf_counter() - t0
    payloads = [(str(p.run_dir), a, b, span_lo, span_hi, n_win) for p in paths]
    if analysis_procs > 1:
        import multiprocessing as mp

        with mp.get_context("spawn").Pool(min(analysis_procs, len(payloads))) as pool:
            analyses = pool.map(_analyze_replicate, payloads)
    else:
        analyses = [_analyze_replicate(pl) for pl in payloads]
    counts = [np.asarray(r["counts"]) for r in analyses]
    seg_speeds = [np.asarray(r["seg"]) for r in analyses]
    waves_by_detector = {
        name: [r["waves_by_detector"][name] for r in analyses] for name in WAVE_DETECTORS
    }
    waves = waves_by_detector[STANDARD.name]
    waves_stripe = waves_by_detector[STRIPE.name]
    metrics_list = [Metrics(**r["metrics"]) for r in analyses]
    realized = [r["realized"] for r in analyses]
    ramps = [r["ramps"] for r in analyses]
    print(
        f"  analysis of {len(paths)} replicates done ({time.perf_counter() - t0 - wall:.0f} s)",
        flush=True,
    )
    counts_arr = np.asarray(counts, dtype=np.float64)
    seg_arr = np.asarray(seg_speeds, dtype=np.float64)
    wave_speed_by_detector = {
        name: _aggregate_wave_summaries(name, summaries)
        for name, summaries in waves_by_detector.items()
    }
    criterion = wave_speed_by_detector[CRITERION_DETECTOR.name]
    bw = [w["mean_backward_speed_kmh"] for w in waves if w["mean_backward_speed_kmh"] is not None]
    bws = [
        w["mean_backward_speed_kmh"]
        for w in waves_stripe
        if w["mean_backward_speed_kmh"] is not None
    ]
    metrics_ci = {
        name: {
            "mean": ci.mean,
            "lo95": ci.lo95,
            "hi95": ci.hi95,
            "n": ci.n,
            "underpowered": ci.underpowered,
        }
        for name, ci in aggregate(metrics_list).items()
    }
    return {
        "config_hash": config_hash(cfg),
        "seeds": seeds,
        "run_dirs": [str(p.run_dir) for p in paths],
        "wall_s": round(wall, 1),
        "demand_realized_fraction": [round(f, 4) for f in realized],
        "ramps_per_replicate": ramps,
        "counts_mean": counts_arr.mean(axis=0).tolist(),
        "hourly_flows_veh_h_mean": (counts_arr.mean(axis=0) * 3600.0 / WINDOW_S).tolist(),
        "counts_per_replicate": [c.tolist() for c in counts],
        "segment_speeds_ms_mean": np.nanmean(seg_arr, axis=0).tolist(),
        "waves_per_replicate": waves,
        "wave_count_mean": float(np.mean([w["count"] for w in waves])),
        "mean_backward_speed_kmh": float(np.mean(bw)) if bw else None,
        "n_replicates_with_backward_waves": len(bw),
        "all_backward_speeds_kmh": [v for w in waves for v in w["backward_speeds_kmh"]],
        "waves_stripe_per_replicate": waves_stripe,
        "stripe_mean_backward_speed_kmh": float(np.mean(bws)) if bws else None,
        "n_replicates_with_stripe_backward_waves": len(bws),
        "wave_speed_by_detector": wave_speed_by_detector,
        "criterion_detector": CRITERION_DETECTOR.name,
        "criterion_wave_speed_kmh": criterion["mean_backward_speed_kmh"],
        "metrics_ci": metrics_ci,
    }


def _aggregate_wave_summaries(name: str, summaries: list[dict]) -> dict:
    """Replicate aggregate of one detector's per-replicate readings."""
    bw = [
        w["mean_backward_speed_kmh"] for w in summaries if w["mean_backward_speed_kmh"] is not None
    ]
    return {
        "detector": name,
        "detector_description": WAVE_DETECTORS[name].describe(),
        "mean_backward_speed_kmh": float(np.mean(bw)) if bw else None,
        "median_backward_speed_kmh": float(np.median(bw)) if bw else None,
        "n_replicates_with_backward_waves": len(bw),
        "wave_count_mean": float(np.mean([w["count"] for w in summaries])),
        "all_backward_speeds_kmh": [v for w in summaries for v in w["backward_speeds_kmh"]],
        "per_replicate": summaries,
    }


def _json_safe(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, np.floating | np.integer):
        return _json_safe(obj.item())
    return obj


def _geh_table(sim_hourly: np.ndarray, obs_hourly: np.ndarray) -> dict:
    vals = [
        round(geh(float(m), float(c)), 3)
        for m, c in zip(sim_hourly.ravel(), obs_hourly.ravel(), strict=True)
    ]
    return {
        "values": vals,
        "fraction_under_5": round(sum(1 for g in vals if g < 5.0) / len(vals), 4),
        "n_bins": len(vals),
    }


def _ring_worker(payload: tuple[list[int], str]) -> dict:
    """Ring benchmark in a child process (keeps libsumo out of the parent)."""
    from validation.ring_benchmark import evaluate_ring_benchmark

    seeds, out = payload
    return evaluate_ring_benchmark(seeds, Path(out)).to_dict()


def ring_benchmark_block(n_seeds: int, out_dir: Path) -> dict:
    """Evaluate the §7.1 ring rows on ``n_seeds`` seeds of ``ring_sugiyama``.

    Seeds are ``spawn_seeds(<ring scenario seed>, n_seeds)`` (docs/CONTRACTS.md
    §6). Runs in a spawned process so the parent never loads libsumo (the
    parquet-path clash documented on ``microsim.runner._write_parquet``).
    Writes ``out_dir/ring_benchmark.json`` and returns the same dict.
    """
    import multiprocessing as mp

    from validation.ring_benchmark import RING_SCENARIO

    ring_cfg = load_scenario(RING_SCENARIO)
    seeds = spawn_seeds(ring_cfg.seed, n_seeds)
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(1) as pool:
        ring = pool.apply(_ring_worker, ((seeds, str(out_dir)),))
    ring["wall_s"] = round(time.perf_counter() - t0, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ring_benchmark.json").write_text(
        json.dumps(_json_safe(ring), indent=2, allow_nan=False)
    )
    return ring


def build_results(
    arm: str,
    cfg: ScenarioConfig,
    sim: dict,
    obs: dict,
    replicates: int,
    ring: dict | None = None,
) -> dict:
    sim_hourly = np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64)
    geh_tracked = _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_tracked"]))
    geh_corrected = _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_corrected"]))
    # speedcal derives from the coverage-corrected profile, so its flow criterion
    # is scored against the corrected counts too; both tables are reported.
    geh_primary = geh_corrected if arm in ("corrected", "speedcal", "ramps") else geh_tracked

    obs_seg = np.asarray(obs["segment_speeds_ms"], dtype=np.float64)
    sim_seg = np.asarray(sim["segment_speeds_ms_mean"], dtype=np.float64)
    both = np.isfinite(obs_seg) & np.isfinite(sim_seg) & (obs_seg != 0.0)
    rmspe_value = rmspe(sim_seg[both], obs_seg[both])

    criteria_rows = evaluate(
        PROFILE,
        geh_values=geh_primary["values"],
        rmspe_value=rmspe_value,
        wave_speed_kmh=(
            sim["criterion_wave_speed_kmh"]
            if sim["criterion_wave_speed_kmh"] is not None
            else math.nan
        ),
        wave_detector=CRITERION_DETECTOR,
        ring_emergence=None if ring is None else bool(ring["emergence"]["passed"]),
        ring_dampening=None if ring is None else bool(ring["dampening"]["passed"]),
        n_seeds=replicates,
    )
    inputs = _inputs()
    ring_note = (
        "Ring benchmark rows evaluated by validation.ring_benchmark (the CI gate's checks on "
        f"{ring['emergence']['n_seeds']} seeded replicates; pass = every replicate passes); "
        "see the 'ring' block."
        if ring is not None
        else "Ring benchmark rows not evaluated in this run (--ring-seeds 0); reported as failing per CLAUDE.md §0.1."
    )
    return {
        "schema_version": 5,
        "criteria_profile": PROFILE.name,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": ARMS[arm],
        "arm": arm,
        "replicates": replicates,
        "seeds": sim["seeds"],
        "config_hash": sim["config_hash"],
        "versions": _versions(),
        "boundary": {
            "kind": "speed_schedule on the exit edge (data x >= 5492 m)",
            "source": f"observed mean mainline speed in data x {inputs['boundary']['x_range_m']} per {inputs['boundary']['window_s']:g} s",
            "v_limit_min_ms": inputs["boundary"]["v_min_ms"],
            "v_limit_max_ms": inputs["boundary"]["v_max_ms"],
            "framing": "measured downstream boundary condition per FHWA Traffic Analysis Toolbox Vol. III (FHWA-HOP-18-036, 2019); data-derived (seeded=False)",
        },
        "ramps": [r["name"] for r in inputs["ramps"]],
        "demand_arm": (
            "as tracked (lower bound at the instrument's coverage)"
            if arm == "tracked"
            else "divided by the apparent tracking coverage per 15-min window (docs/I24_DATA.md §4)"
        ),
        "observed": obs,
        "simulated": sim,
        "geh": {
            "primary": "corrected" if arm in ("corrected", "speedcal", "ramps") else "tracked",
            "vs_tracked_counts": geh_tracked,
            "vs_coverage_corrected_counts": geh_corrected,
            "bins": "6 sections x 24 five-min windows, hourly-equivalent volumes (x12)",
        },
        "rmspe": {"value": float(rmspe_value), "n_bins": int(both.sum())},
        "waves": {
            "observed": obs["waves"],
            "observed_stripe": obs["waves_stripe"],
            "stripe_params": obs["waves_stripe_params"],
            "simulated_mean_backward_speed_kmh": sim["mean_backward_speed_kmh"],
            "simulated_wave_count_mean": sim["wave_count_mean"],
            "n_replicates_with_backward_waves": sim["n_replicates_with_backward_waves"],
            "simulated_stripe_mean_backward_speed_kmh": sim["stripe_mean_backward_speed_kmh"],
            "n_replicates_with_stripe_backward_waves": sim[
                "n_replicates_with_stripe_backward_waves"
            ],
            "criterion_detector": CRITERION_DETECTOR.name,
            "criterion_detector_description": CRITERION_DETECTOR.describe(),
            "criterion_wave_speed_kmh": sim["criterion_wave_speed_kmh"],
            "by_detector": {
                name: {
                    "detector_description": WAVE_DETECTORS[name].describe(),
                    "observed": obs["waves_by_detector"][name],
                    "simulated": sim["wave_speed_by_detector"][name],
                }
                for name in WAVE_DETECTORS
            },
            "field_bins": f"each detector's own bins (see by_detector) on data x in [0, {obs['span_data_x_m'][1]:.0f}) m, both sides",
            "prediction_under_test": "docs/WAVE_SPEED_DIAGNOSIS.md: on a long, congested corridor the calibrated fleet's emergent backward waves fall in the 14-22 km/h band",
        },
        "criteria": [asdict(r) for r in criteria_rows],
        "ring": ring,
        "metrics_ci": sim["metrics_ci"],
        "notes": [
            "Observed side: I-24 MOTION westbound fragments, mainline lanes 1-4, 06:30-08:30 CST, data x in [0, 5492) m; counts are fragment crossings (lower bounds at tracking coverage), speeds are coverage-robust.",
            "Six GEH sections chosen for coverage (holes at 400 m and 2400 m avoided); the observed count at every section is still biased low by 35-50% in the peak.",
            "Both GEH tables are reported for both arms; the criteria row uses the tracked counts for the tracked arm and the coverage-corrected counts for the corrected arm.",
            ring_note,
            "The sensitivity_grid criterion row is not evaluated by this script (the penetration x compliance sweep is a separate artifact, scripts/i24_penetration_sweep.py).",
            "compute_metrics runs on the measured span only (travel time over the span, throughput at data x = 2200 m); its wave metrics use the standard 40 km/h detector on the same site-clipped field, not the criteria row's detector.",
            f"The wave_speed criteria row is measured with the {PROFILE.name!r} profile's wave_detector ({CRITERION_DETECTOR.name}); the 'waves' block keeps the standard-detector keys of schema 4 and adds every registered detector under 'by_detector'.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=int(os.environ.get("I24_PROCS", "8")))
    ap.add_argument(
        "--arms",
        choices=("all", "both", "tracked", "corrected", "speedcal", "ramps"),
        default="all",
        help="'both' = tracked + corrected (the pre-2026-09-03 pair); 'all' adds speedcal",
    )
    ap.add_argument("--analysis-procs", type=int, default=6)
    ap.add_argument(
        "--reuse-runs",
        action="store_true",
        help="analyse complete replicates already under runs/i24_validation instead of re-simulating",
    )
    ap.add_argument(
        "--ring-seeds",
        type=int,
        default=20,
        help="seeds for the ring emergence/dampening rows (0 = not evaluated)",
    )
    ap.add_argument(
        "--ring-only",
        action="store_true",
        help="evaluate the ring benchmark, write runs/i24_validation/ring/ring_benchmark.json, exit",
    )
    args = ap.parse_args()
    t0 = time.perf_counter()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    ring: dict | None = None
    if args.ring_seeds > 0:
        print(f"ring benchmark: {args.ring_seeds} seeds ...", flush=True)
        ring = ring_benchmark_block(args.ring_seeds, OUT_ROOT / "ring")
        for arm_name in ("emergence", "dampening"):
            blk = ring[arm_name]
            print(
                f"  ring {arm_name:10s} {'PASS' if blk['passed'] else 'FAIL'}  "
                f"{blk['n_pass']}/{blk['n_seeds']} seeds  (wall {ring['wall_s']} s)",
                flush=True,
            )
    if args.ring_only:
        print(f"done in {time.perf_counter() - t0:.0f} s -> {OUT_ROOT / 'ring'}")
        return
    obs = observed_side(OUT_ROOT / "observed_i24.json")
    shutil.copy(
        OUT_ROOT / "observed_i24.json", REPO_ROOT / "artifacts" / "i24_validation_observed.json"
    )
    arms = [
        a
        for a in ARMS
        if args.arms == a
        or (
            args.arms == "all"
            and (a != "ramps" or (REPO_ROOT / "scenarios" / f"{ARMS[a]}.yaml").is_file())
        )
        or (args.arms == "both" and a in ("tracked", "corrected"))
    ]
    for arm in arms:
        cfg = load_scenario(ARMS[arm])
        print(
            f"arm {arm!r} ({ARMS[arm]}, config {config_hash(cfg)}): {args.replicates} replicates ...",
            flush=True,
        )
        sim = micro_arm(
            cfg,
            args.replicates,
            OUT_ROOT / arm,
            args.procs,
            obs,
            analysis_procs=args.analysis_procs,
            reuse_runs=args.reuse_runs,
        )
        results = build_results(arm, cfg, sim, obs, args.replicates, ring)
        out_path = REPO_ROOT / "artifacts" / f"i24_validation_{arm}.json"
        out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
        shutil.copy(REPO_ROOT / "scenarios" / f"{ARMS[arm]}.yaml", OUT_ROOT / f"{ARMS[arm]}.yaml")
        g = results["geh"]
        print(
            f"[{arm}] GEH<5 vs tracked {g['vs_tracked_counts']['fraction_under_5']:.0%}, vs corrected "
            f"{g['vs_coverage_corrected_counts']['fraction_under_5']:.0%} | RMSPE {results['rmspe']['value']:.1%} | "
            f"sim backward wave [{CRITERION_DETECTOR.name}] {sim['criterion_wave_speed_kmh']} km/h "
            f"(standard {sim['mean_backward_speed_kmh']}, {sim['n_replicates_with_backward_waves']}/{args.replicates} reps) | "
            f"obs [{CRITERION_DETECTOR.name}] {obs['waves_by_detector'][CRITERION_DETECTOR.name]['mean_backward_speed_kmh']} km/h "
            f"(standard {obs['waves']['mean_backward_speed_kmh']}, stripe {obs['waves_stripe']['mean_backward_speed_kmh']}) | "
            f"wall {sim['wall_s']} s",
            flush=True,
        )
        for row in results["criteria"]:
            print(
                f"    {row['name']:18s} {'PASS' if row['passed'] else 'FAIL'}  {row['value']}  ({row['threshold']})"
            )
    print(f"done in {time.perf_counter() - t0:.0f} s -> {OUT_ROOT}")


if __name__ == "__main__":
    main()
