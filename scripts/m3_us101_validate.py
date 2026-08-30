"""M3 US-101 validation driver: observed vs simulated (CLAUDE.md §7, §11 M3).

Runs the ``us101_replica`` scenario (default 20 seeded replicates via
``flowstate_core.rng.spawn_seeds`` from the scenario seed) against the REAL
NGSIM US-101 recording (period 1, which the replica models) in TWO arms:

* **no_boundary** — the honest predict-from-nothing case: upstream inflow
  only, free outflow. M2 predicted this cannot reproduce the observed
  congestion because the site's congestion enters from DOWNSTREAM of the
  640 m camera range (docs/M2_RESULTS.md §6/§7.3).
* **with_boundary** — the same replica plus a MEASURED downstream boundary
  condition (``flowstate_core.config.BoundarySpec``): the observed mean
  speed in the last 100 m of the site per 30 s window, applied via
  ``edge.setMaxSpeed`` on an exit-buffer edge OUTSIDE the measured span.
  Imposing measured boundary conditions is standard FHWA microsimulation
  calibration practice (Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036,
  2019). The schedule is data-derived, so it is a calibration input, not a
  seeded perturbation (``seeded`` stays False).

Per arm, the comparison tables are:

* **Link flows** — vehicle counts crossing 3 cross-sections (100 / 320 /
  550 m along the site) per 5-min window, observed counts computed from the
  ``data/ngsim`` chunks with the same dedup/period logic as
  ``scripts/us101_data.py`` (mainline lanes 1–5). GEH per
  link-hour-equivalent bin (5-min counts scaled ×12 to hourly volumes,
  replicate-mean simulated vs observed).
* **Segment speeds** — RMSPE of replicate-mean simulated segment speeds
  (4 × 160 m segments × 5-min windows) against observed.
* **Waves** — ``validation.waves.detect_waves`` on both the simulated and
  the observed binned speed fields (15 s × 75 m, clipped to the 640 m site
  on both sides).
* **Criteria** — ``validation.criteria.evaluate`` (GEH / RMSPE / wave speed /
  n_seeds; the ring benchmarks are CI-gated elsewhere and reported here as
  not-evaluated rows, honestly failing per CLAUDE.md §0.1).
* **Replicate metrics with CIs** — ``validation.metrics.compute_metrics``
  per replicate + ``validation.metrics.aggregate`` (t-distribution 95% CIs,
  CLAUDE.md §0.6).

The macro-vs-micro flux-cap-variant arm moved to
``scripts/m3_fluxcap_compare.py`` (runs/m3_fluxcap) — the follower_stopper
variant comparison here was an identical-variants null because that
controller never commands below the local equilibrium speed.

Everything lands under ``runs/m3_us101/``: run trees per arm, an observed-
side cache (``observed_us101.json``, keyed by the data hash), and the
machine-readable ``results_no_boundary.json`` / ``results_with_boundary.json``
consumed by the report phase (scripts/m3_us101_report.py).

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

from flowstate_core.config import BoundarySpec, ScenarioConfig, config_hash  # noqa: E402
from flowstate_core.rng import spawn_seeds  # noqa: E402
from flowstate_core.units import kmh_to_ms, ms_to_kmh  # noqa: E402
from microsim.runner import _versions, run_replicates  # noqa: E402
from microsim.scenarios import load_scenario  # noqa: E402
from validation.criteria import evaluate  # noqa: E402
from validation.fields import speed_field  # noqa: E402
from validation.metrics import aggregate, compute_metrics, geh, rmspe  # noqa: E402
from validation.waves import detect_waves  # noqa: E402

OUT_ROOT = REPO_ROOT / "runs" / "m3_us101"

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

BOUNDARY_WINDOW_S = 30.0
"""Downstream-boundary schedule resolution [s] (task spec)."""
BOUNDARY_TAIL_M = 100.0
"""Site tail over which the observed boundary speed is averaged [m]."""
BOUNDARY_EXIT_BUFFER_M = 200.0
"""Exit-buffer edge length hosting the boundary speed limit [m]."""

OBSERVED_CACHE_VERSION = 2

RESULTS_SCHEMA_KEYS = frozenset(
    {
        "schema_version",
        "created_at",
        "scenario",
        "arm",
        "replicates",
        "seeds",
        "config_hash",
        "versions",
        "boundary",
        "observed",
        "simulated",
        "geh",
        "rmspe",
        "waves",
        "criteria",
        "metrics_ci",
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


STRIPE_THRESH_KMH = 25.0
"""Stripe-level jam threshold [km/h] for the secondary wave analysis.

The standard 40 km/h threshold (CLAUDE.md §7.2 default) merges this
persistently congested 640 m site into blob components whose upstream
fronts pin at the site boundary (fitted slope 0 — not backward). Lowering
the threshold isolates the deep stop-and-go stripes INSIDE the congestion,
whose fronts carry the empirical backward wave signature. Both analyses
are reported, with identical parameters on the observed and simulated
sides."""
STRIPE_DT_BIN_S = 10.0
STRIPE_DX_BIN_M = 50.0


def _wave_summary(
    traj: pd.DataFrame,
    dt_bin: float = WAVE_DT_BIN_S,
    dx_bin: float = WAVE_DX_BIN_M,
    v_jam_thresh_kmh: float | None = None,
) -> dict:
    """Wave detection on one trajectory table (wall t, local_y x, x < 640 m).

    The field is clipped to the 640 m site on both sides so the observed and
    simulated wave statistics are computed on the same spatial support.
    """
    clipped = traj[(traj["x"] >= 0.0) & (traj["x"] < SITE_LENGTH_M)]
    field = speed_field(clipped, dt_bin=dt_bin, dx_bin=dx_bin)
    if v_jam_thresh_kmh is None:
        ws = detect_waves(field)
    else:
        ws = detect_waves(field, v_jam_thresh=kmh_to_ms(v_jam_thresh_kmh))
    backward = ws.backward()
    return {
        "count": ws.count,
        "n_backward": len(backward),
        "speeds_kmh": [round(ms_to_kmh(w.speed_ms), 2) for w in ws.waves],
        "backward_speeds_kmh": [round(ms_to_kmh(-w.speed_ms), 2) for w in backward],
        "amplitudes_ms": [round(w.amplitude_ms, 2) for w in ws.waves],
        "mean_backward_speed_kmh": (
            float(np.mean([ms_to_kmh(-w.speed_ms) for w in backward])) if backward else None
        ),
    }


def _stripe_wave_summary(traj: pd.DataFrame) -> dict:
    """Stripe-level wave analysis (see :data:`STRIPE_THRESH_KMH`)."""
    return _wave_summary(
        traj,
        dt_bin=STRIPE_DT_BIN_S,
        dx_bin=STRIPE_DX_BIN_M,
        v_jam_thresh_kmh=STRIPE_THRESH_KMH,
    )


def _boundary_schedule_wall(p1: pd.DataFrame) -> list[tuple[float, float]]:
    """Observed downstream-boundary speed schedule on the wall clock.

    Mean recorded speed of mainline samples in the last ``BOUNDARY_TAIL_M``
    of the site (x ∈ [540, 640) m) per ``BOUNDARY_WINDOW_S`` window over the
    period-1 span. Empty windows are forward-filled (then back-filled for a
    leading gap) — the schedule is piecewise-constant anyway.

    Returns:
        Time-ordered ``(t_wall [s], v [m/s])`` steps.
    """
    tail = p1[(p1["x"] >= SITE_LENGTH_M - BOUNDARY_TAIL_M) & (p1["x"] < SITE_LENGTH_M)]
    t_max = float(p1["t"].max())
    n_win = math.ceil(t_max / BOUNDARY_WINDOW_S)
    vals: list[float] = []
    tv = tail["t"].to_numpy(dtype=np.float64)
    vv = tail["v"].to_numpy(dtype=np.float64)
    for w in range(n_win):
        sel = (tv >= w * BOUNDARY_WINDOW_S) & (tv < (w + 1) * BOUNDARY_WINDOW_S)
        vals.append(float(vv[sel].mean()) if sel.any() else math.nan)
    filled = pd.Series(vals).ffill().bfill().to_numpy(dtype=np.float64)
    if np.isnan(filled).any():
        raise ValueError("boundary schedule has no observed samples at all")
    return [(w * BOUNDARY_WINDOW_S, float(v)) for w, v in enumerate(filled)]


def build_boundary_spec(schedule_wall: list[tuple[float, float]]) -> BoundarySpec:
    """Map the observed wall-clock schedule into a sim-time ``BoundarySpec``.

    Sim time = wall time + 180 s warmup. The first observed value is held
    from sim t = 0 through the warmup (the recording starts congested, so
    the warmup builds toward the first observed boundary state); the last
    value holds to the end of the run.
    """
    steps: list[tuple[float, float]] = [(0.0, schedule_wall[0][1])]
    steps += [(WARMUP_S + t_wall, v) for t_wall, v in schedule_wall[1:]]
    return BoundarySpec(steps=steps, exit_buffer_m=BOUNDARY_EXIT_BUFFER_M)


def observed_side(cache_path: Path) -> dict:
    """Observed comparison tables from the NGSIM chunks (cached by data hash).

    Uses ``us101_data.load_us101`` — the shared loader that drops the exact
    duplicate rows and splits recording periods on the recording origin —
    and restricts to period 1 (the span the replica models), mainline lanes
    1–5, all vehicle classes. Also extracts the downstream boundary speed
    schedule (:func:`_boundary_schedule_wall`).
    """
    dh = data_hash()
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text())
        if cached.get("data_hash") == dh and cached.get("cache_version") == OBSERVED_CACHE_VERSION:
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
    waves_stripe = _stripe_wave_summary(p1[["t", "x", "v"]])
    schedule = _boundary_schedule_wall(p1)
    obs = {
        "cache_version": OBSERVED_CACHE_VERSION,
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
        "waves_stripe": waves_stripe,
        "waves_stripe_params": {
            "v_jam_thresh_kmh": STRIPE_THRESH_KMH,
            "dt_bin_s": STRIPE_DT_BIN_S,
            "dx_bin_m": STRIPE_DX_BIN_M,
        },
        "boundary_schedule_wall": [[t, round(v, 4)] for t, v in schedule],
        "boundary_source": (
            f"mean mainline speed, local_y in [{SITE_LENGTH_M - BOUNDARY_TAIL_M:g}, "
            f"{SITE_LENGTH_M:g}) m, {BOUNDARY_WINDOW_S:g} s windows, NGSIM US-101 p1"
        ),
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
    waves_stripe = []
    demand_frac = []
    metrics_list = []
    for p in paths:
        meta = json.loads(p.meta.read_text())
        demand_frac.append(meta["n_vehicles_departed"] / meta["n_vehicles_planned"])
        df = _sim_frame(p.run_dir)
        counts.append(_crossing_counts(df, SECTIONS_M))
        seg_speeds.append(_segment_speeds(df))
        waves.append(_wave_summary(df[["t", "x", "v"]]))
        waves_stripe.append(_stripe_wave_summary(df[["t", "x", "v"]]))
        metrics_list.append(
            compute_metrics(
                p.run_dir,
                x_ref=ENTRY_BUFFER_M + SECTIONS_M[1],
                span=(ENTRY_BUFFER_M, ENTRY_BUFFER_M + SITE_LENGTH_M),
            )
        )
    counts_arr = np.asarray(counts, dtype=np.float64)
    seg_arr = np.asarray(seg_speeds, dtype=np.float64)
    bw = [w["mean_backward_speed_kmh"] for w in waves if w["mean_backward_speed_kmh"] is not None]
    bw_stripe = [
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
        "demand_realized_fraction": [round(f, 4) for f in demand_frac],
        "counts_mean": counts_arr.mean(axis=0).tolist(),
        "hourly_flows_veh_h_mean": (counts_arr.mean(axis=0) * 3600.0 / WINDOW_S).tolist(),
        "counts_per_replicate": [c.tolist() for c in counts],
        "segment_speeds_ms_mean": np.nanmean(seg_arr, axis=0).tolist(),
        "waves_per_replicate": waves,
        "wave_count_mean": float(np.mean([w["count"] for w in waves])),
        "mean_backward_speed_kmh": (float(np.mean(bw)) if bw else None),
        "n_replicates_with_backward_waves": len(bw),
        "waves_stripe_per_replicate": waves_stripe,
        "stripe_mean_backward_speed_kmh": (float(np.mean(bw_stripe)) if bw_stripe else None),
        "n_replicates_with_stripe_backward_waves": len(bw_stripe),
        "metrics_ci": metrics_ci,
    }


def _json_safe(obj: object) -> object:
    """Replace non-finite floats with None so the JSON is strictly parseable."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def build_results(arm: str, cfg: ScenarioConfig, sim: dict, obs: dict, replicates: int) -> dict:
    """Assemble one arm's results dict (GEH, RMSPE, waves, criteria, CIs)."""
    obs_hourly = np.asarray(obs["hourly_flows_veh_h"], dtype=np.float64)
    sim_hourly = np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64)
    geh_values = [
        round(geh(float(m), float(c)), 3)
        for m, c in zip(sim_hourly.ravel(), obs_hourly.ravel(), strict=True)
    ]
    geh_frac = sum(1 for g in geh_values if g < 5.0) / len(geh_values)

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
        n_seeds=replicates,
    )

    net = cfg.network
    boundary = None
    if getattr(net, "boundary", None) is not None:
        boundary = {
            "kind": net.boundary.kind,
            "exit_buffer_m": net.boundary.exit_buffer_m,
            "n_steps": len(net.boundary.steps),
            "steps_sim_time": [[t, round(v, 4)] for t, v in net.boundary.steps],
            "source": obs["boundary_source"],
            "framing": (
                "measured downstream boundary condition per FHWA Traffic Analysis "
                "Toolbox Vol. III (FHWA-HOP-18-036, 2019) calibration practice; "
                "data-derived (seeded=False)"
            ),
        }

    return {
        "schema_version": 2,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": "us101_replica",
        "arm": arm,
        "replicates": replicates,
        "seeds": sim["seeds"],
        "config_hash": sim["config_hash"],
        "versions": _versions(),
        "boundary": boundary,
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
            "n_replicates_with_backward_waves": sim["n_replicates_with_backward_waves"],
            "field_bins": f"{WAVE_DT_BIN_S:g} s x {WAVE_DX_BIN_M:g} m, x < 640 m both sides",
            "observed_stripe": obs["waves_stripe"],
            "stripe_params": obs["waves_stripe_params"],
            "simulated_stripe_mean_backward_speed_kmh": sim["stripe_mean_backward_speed_kmh"],
            "n_replicates_with_stripe_backward_waves": (
                sim["n_replicates_with_stripe_backward_waves"]
            ),
            "stripe_note": (
                "the 40 km/h default threshold merges this fully congested 640 m site "
                "into components whose upstream front pins at the site boundary "
                "(slope 0); the stripe analysis lowers the threshold to isolate the "
                "deep stop-and-go stripes inside the congestion, identically on both "
                "sides"
            ),
        },
        "criteria": [asdict(r) for r in criteria_rows],
        "metrics_ci": sim["metrics_ci"],
        "notes": [
            "Observed side: NGSIM US-101 period 1, mainline lanes 1-5, duplicate rows "
            "dropped (scripts/us101_data.py logic); ~6% of real vehicles entered via the "
            "on-ramp and are counted at downstream sections but never injected by the "
            "replica (docs/M2_RESULTS.md §7.6).",
            "no_boundary arm: the replica has no downstream boundary congestion; M2 "
            "predicts it runs faster than observed (docs/M2_RESULTS.md §6) — RMSPE "
            "reflects that gap honestly.",
            "with_boundary arm: measured downstream speed schedule (last 100 m, 30 s "
            "windows) applied on a 200 m exit-buffer edge OUTSIDE the measured span "
            "(FHWA measured-boundary calibration practice). The replica still lacks "
            "the auxiliary lane and on/off ramps (internal merge bottleneck at "
            "~150-165 m), so remaining in-span disagreement is expected and reported.",
            "Comparisons use the 3 full 5-min windows (0-900 s) inside the 952.8 s "
            "period-1 span; the trailing partial window is excluded.",
            "Crossing counts censor vehicles first observed at/past a section on both "
            "sides (recording start vs warmup end).",
            "Macro-vs-micro flux-cap variant comparison: scripts/m3_fluxcap_compare.py "
            "-> runs/m3_fluxcap/results.json (JAD arm; the follower_stopper arm was an "
            "identical-variants null).",
        ],
    }


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
    assert data["arm"] in ("no_boundary", "with_boundary")
    assert (data["boundary"] is not None) == (data["arm"] == "with_boundary")
    for row in data["criteria"]:
        assert {"name", "value", "threshold", "passed", "evaluated"} <= set(row)
    for ci in data["metrics_ci"].values():
        assert {"mean", "lo95", "hi95", "n", "underpowered"} <= set(ci)
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
    ap.add_argument(
        "--arms",
        choices=("both", "no_boundary", "with_boundary"),
        default="both",
        help="which validation arms to run",
    )
    args = ap.parse_args()
    t0 = time.perf_counter()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    obs = observed_side(OUT_ROOT / "observed_us101.json")

    base_cfg = load_scenario("us101_replica")
    schedule_wall = [(float(t), float(v)) for t, v in obs["boundary_schedule_wall"]]
    bspec = build_boundary_spec(schedule_wall)

    arms: dict[str, ScenarioConfig] = {}
    if args.arms in ("both", "no_boundary"):
        arms["no_boundary"] = base_cfg
    if args.arms in ("both", "with_boundary"):
        net_b = base_cfg.network.model_copy(update={"boundary": bspec})
        arms["with_boundary"] = base_cfg.model_copy(update={"network": net_b})

    for arm, cfg in arms.items():
        print(f"micro arm {arm!r}: {args.replicates} replicates ...", flush=True)
        sim = micro_arm(cfg, args.replicates, OUT_ROOT / f"micro_{arm}", args.procs)
        results = build_results(arm, cfg, sim, obs, args.replicates)
        out_path = OUT_ROOT / f"results_{arm}.json"
        out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
        if arm == "with_boundary":
            cfg.to_yaml(OUT_ROOT / "us101_replica_with_boundary.yaml")
        print(
            f"[{arm}] GEH<5 fraction {results['geh']['fraction_under_5']:.0%} | "
            f"RMSPE {results['rmspe']['value']:.1%} | "
            f"sim backward wave {sim['mean_backward_speed_kmh']} km/h "
            f"({sim['n_replicates_with_backward_waves']}/{args.replicates} reps) | "
            f"obs backward wave {obs['waves']['mean_backward_speed_kmh']} km/h"
        )
        verify_schema(out_path)

    print(f"done in {time.perf_counter() - t0:.1f} s -> {OUT_ROOT}")


if __name__ == "__main__":
    main()
