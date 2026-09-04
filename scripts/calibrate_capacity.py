"""Capacity calibration of a car-following population (FHWA Vol. III step 1), any corridor.

Corridor-agnostic form of ``scripts/i24_calibrate_capacity.py``, whose
procedure is documented in ``docs/I24_CAPACITY.md`` §3-4. The I-24 script
stays as the record of that run; this one takes every corridor-specific
input as an argument and imports the I-24 script's pure helpers unchanged,
so the two cannot drift apart.

The procedure. A population fitted on car-following episodes by gap error
identifies each driver's headway well and the population's capacity poorly
(the known IDM trade-off between congested headways and free-flow capacity).
The FHWA Traffic Analysis Toolbox Vol. III calibration sequence therefore
calibrates capacity first: the population's mean desired time headway ``T``
is scaled by a factor ``f`` — covariance, the other means, lane-change
parameters and demand untouched — and ``f*`` is the factor at which the
population's straight-road capacity meets the field capacity target, by
linear interpolation between grid points. The target defaults to the
fitted fundamental diagram's ``q_max`` bootstrap lower bound, so ``f*`` is
conservative. The cost is reported as the population-mean gap RMSE over a
seeded sample of the car-following episodes, before and after, when the
episodes are available locally.

Capacity is measured on a straight corridor with the replica's lane count
and no ramps or boundary, driven at a saturating per-lane inflow for 30
simulated minutes (5 warm-up), throughput counted at a reference section;
a grid point whose inserted fraction reaches ``DEMAND_LIMITED_INSERTED`` is
flagged as demand-limited (its throughput is the demand, not the capacity)
and the run must be repeated with a higher ``--demand-veh-h-lane``.

Outputs a derived ``IDMCalibration`` (usable as ``fleet.idm_calibration``)
and a ``<out stem>.calibration.json`` sidecar with the full grid, the runs,
the interpolation and the episode cost.

Run (US-101)::

    uv run --no-sync python scripts/calibrate_capacity.py \\
        --source artifacts/idm_us101.json --fd artifacts/fd_us101.json --lanes 5 \\
        --base-scenario scenarios/us101_replica.yaml \\
        --episodes data/processed/us101_episodes.pkl \\
        --out artifacts/idm_us101_capacity.json --procs 3

The I-24 run is reproduced by ``--source artifacts/idm_i24.json --fd
artifacts/fd_i24.json --lanes 4 --base-scenario
scenarios/i24_replica_corrected.yaml --episodes
data/i24motion/processed/i24_wb_episodes.pkl`` with the default grid,
seeds and corridor (``tests/test_calibration/test_calibration_procedure.py``
checks that the derived artifact rebuilt from the recorded I-24 table is the
committed one).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pickle
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_calibrate_capacity import crossings_per_hour, derived_artifact, episode_rmse

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import AVSpec, CorridorNetwork, FleetSpec, ScenarioConfig, SimSpec
from flowstate_core.units import veh_h_to_veh_s, veh_s_to_veh_h
from microsim import load_scenario, run_micro
from microsim.runner import _versions

REPO = Path(__file__).resolve().parents[1]

DEFAULT_T_SCALES: tuple[float, ...] = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75)
"""The I-24 grid (``scripts/i24_calibrate_capacity.py``): descending T scales."""
DEFAULT_SEEDS: tuple[int, ...] = (1, 2)
LENGTH_M = 4000.0
"""Straight-corridor length [m] (the runner prepends its own insertion buffer)."""
X_REF_M = 3000.0
"""Reference section for throughput, in run coordinates [m]."""
DEMAND_VEH_H_LANE = 2400.0
"""Saturating per-lane inflow [veh/h/lane]: capacity is what gets through."""
DURATION_S = 1800.0
WARMUP_S = 300.0
EPISODE_SAMPLE = 1500
EPISODE_SEED = 7
DEMAND_LIMITED_INSERTED = 0.98
"""Inserted fraction at or above which a grid point is demand-limited, not
capacity-limited, and its throughput is not a capacity measurement."""
REF_HALF_WINDOW_M = 500.0
"""Half-width of the window around ``x_ref`` for the mean-speed diagnostic [m]."""


def interpolate_scale(
    scales: list[float] | tuple[float, ...],
    capacities: list[float] | tuple[float, ...],
    target: float,
) -> tuple[float, str]:
    """Locate ``f*`` on the capacity-versus-T-scale grid (docs/I24_CAPACITY.md §3).

    The grid is ordered by descending scale (capacity rises as T shrinks, in
    expectation). If the largest scale already meets the target it is
    returned unchanged; if no grid point reaches the target the best grid
    point is taken; otherwise ``f*`` is the linear interpolation between the
    last grid point below the target and the first at or above it. This is
    the rule ``scripts/i24_calibrate_capacity.py`` applies inline.

    Args:
        scales: T scale factors of the grid points (any order).
        capacities: Measured capacity [veh/h/lane] per grid point.
        target: Field capacity target [veh/h/lane].

    Returns:
        ``(f_star, how)`` — the scale and a one-line description of the rule
        that produced it.
    """
    if len(scales) != len(capacities) or not scales:
        raise ValueError("scales and capacities must be non-empty and the same length")
    order = np.argsort(-np.asarray(scales, dtype=float), kind="stable")
    fs = np.asarray(scales, dtype=float)[order]
    caps = np.asarray(capacities, dtype=float)[order]
    if caps[0] >= target:
        return float(fs[0]), "capacity already meets the target; population unchanged"
    if caps.max() < target:
        return (
            float(fs[int(np.argmax(caps))]),
            "target not reached within the grid; best grid point taken",
        )
    k = int(np.argmax(caps >= target))  # first index meeting the target (scales descend)
    f_hi, f_lo = float(fs[k - 1]), float(fs[k])
    c_hi, c_lo = float(caps[k - 1]), float(caps[k])
    f_star = f_hi + (target - c_hi) * (f_lo - f_hi) / (c_lo - c_hi)
    how = f"linear interpolation between T x {f_hi:g} ({c_hi:.0f}) and T x {f_lo:g} ({c_lo:.0f})"
    return float(f_star), how


def capacity_table(
    rows: list[dict[str, Any]], t_scales: tuple[float, ...] | list[float], t_mean_s: float
) -> list[dict[str, Any]]:
    """Per-grid-point capacity: the mean throughput over that scale's seeds.

    Args:
        rows: Per-run records with ``T_scale``, ``throughput_veh_h_lane`` and
            ``inserted_fraction``.
        t_scales: Grid, in the order the table should list it.
        t_mean_s: Source population mean T [s], for the ``T_mean_s`` column.

    Returns:
        One record per grid point with ``T_scale``, ``T_mean_s``,
        ``capacity_veh_h_lane``, ``n`` (seeds) and ``demand_limited`` (True
        when every seed inserted at least ``DEMAND_LIMITED_INSERTED``).
    """
    table = []
    for f in t_scales:
        rs = [r for r in rows if r["T_scale"] == f]
        if not rs:
            raise ValueError(f"no runs for T scale {f}")
        cap = float(np.mean([r["throughput_veh_h_lane"] for r in rs]))
        limited = all(r["inserted_fraction"] >= DEMAND_LIMITED_INSERTED for r in rs)
        table.append(
            {
                "T_scale": f,
                "T_mean_s": round(t_mean_s * f, 4),
                "capacity_veh_h_lane": round(cap, 1),
                "n": len(rs),
                "demand_limited": limited,
            }
        )
    return table


def sample_episodes(episodes: list[Any], n: int, seed: int) -> list[Any]:
    """A seeded sample without replacement of at most ``n`` episodes."""
    if not episodes:
        return []
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(episodes), size=min(n, len(episodes)), replace=False)
    return [episodes[i] for i in idx]


def build_config(
    base: ScenarioConfig | None,
    fleet_artifact: str,
    *,
    lanes: int,
    length_m: float,
    demand_veh_h_lane: float,
    duration_s: float,
    warmup_s: float,
    seed: int,
    name: str = "capacity_calibration",
) -> ScenarioConfig:
    """The straight-corridor capacity scenario for one grid point.

    Fleet, AV and sim blocks come from ``base`` when given (so the replica's
    lane-change parameters and step settings carry over, as in the I-24
    script), with the fleet artifact, duration and warm-up overridden;
    without a base the fleet is a plain IDM ``FleetSpec`` on the artifact.

    Args:
        base: Replica scenario to inherit fleet/AV/sim settings from, or None.
        fleet_artifact: Path of the trial ``IDMCalibration`` artifact.
        lanes: Lane count of the straight corridor.
        length_m: Corridor length [m].
        demand_veh_h_lane: Saturating per-lane inflow [veh/h/lane].
        duration_s: Simulated duration [s].
        warmup_s: Warm-up excluded from the throughput count [s].
        seed: Replicate seed.
        name: Scenario name recorded in the run meta.

    Returns:
        A validated ``ScenarioConfig`` with no ramps, boundary or perturbation.
    """
    if base is not None:
        fleet = base.fleet.model_copy(update={"idm_calibration": fleet_artifact})
        av = base.av
        sim = base.sim.model_copy(update={"duration_s": duration_s, "warmup_s": warmup_s})
    else:
        fleet = FleetSpec(model="IDM", idm_calibration=fleet_artifact)
        av = AVSpec()
        sim = SimSpec(duration_s=duration_s, warmup_s=warmup_s)
    return ScenarioConfig(
        name=name,
        tier="micro",
        network=CorridorNetwork(
            length_m=length_m,
            lanes=lanes,
            inflow=[(0.0, veh_h_to_veh_s(demand_veh_h_lane * lanes))],
        ),
        fleet=fleet,
        av=av,
        sim=sim,
        perturbation=None,
        seed=seed,
        replicates=1,
    )


def _job(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one (T scale, seed) grid point and measure its per-lane throughput."""
    import pandas as pd

    base = load_scenario(payload["base_scenario"]) if payload["base_scenario"] else None
    cfg = build_config(
        base,
        payload["artifact_path"],
        lanes=payload["lanes"],
        length_m=payload["length_m"],
        demand_veh_h_lane=payload["demand_veh_h_lane"],
        duration_s=payload["duration_s"],
        warmup_s=payload["warmup_s"],
        seed=payload["seed"],
        name=payload["name"],
    )
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, payload["seed"], Path(td))
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v"])
    x_ref = payload["x_ref_m"]
    thr = (
        crossings_per_hour(
            df["t"].to_numpy(),
            df["x"].to_numpy(),
            df["veh_id"].to_numpy(),
            x_ref,
            payload["warmup_s"],
            payload["duration_s"],
        )
        / payload["lanes"]
    )
    mid = df[
        (df["x"] >= x_ref - REF_HALF_WINDOW_M)
        & (df["x"] < x_ref + REF_HALF_WINDOW_M)
        & (df["t"] >= payload["warmup_s"])
    ]
    return {
        "T_scale": payload["t_scale"],
        "seed": payload["seed"],
        "config_hash": meta["config_hash"],
        "throughput_veh_h_lane": round(thr, 1),
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "mean_speed_ms_at_ref": round(float(mid["v"].mean()), 3) if len(mid) else None,
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def _rel(path: Path) -> str:
    """Repo-relative path when inside the repo, else as given."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--source", type=Path, required=True, help="source IDMCalibration artifact")
    ap.add_argument("--out", type=Path, required=True, help="derived IDMCalibration artifact")
    ap.add_argument(
        "--sidecar", type=Path, default=None, help="default: <out stem>.calibration.json"
    )
    ap.add_argument("--fd", type=Path, default=None, help="FDCalibration artifact for the target")
    ap.add_argument(
        "--target",
        type=float,
        default=None,
        help="explicit capacity target [veh/h/lane]; default: FD q_max bootstrap lower bound",
    )
    ap.add_argument("--lanes", type=int, required=True, help="lane count of the straight corridor")
    ap.add_argument(
        "--base-scenario",
        type=Path,
        default=None,
        help="replica YAML whose fleet/AV/sim settings the capacity runs inherit",
    )
    ap.add_argument("--episodes", type=Path, default=None, help="pickled episode list for the cost")
    ap.add_argument("--t-scales", type=float, nargs="+", default=list(DEFAULT_T_SCALES))
    ap.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    ap.add_argument("--length-m", type=float, default=LENGTH_M)
    ap.add_argument("--x-ref-m", type=float, default=X_REF_M)
    ap.add_argument("--demand-veh-h-lane", type=float, default=DEMAND_VEH_H_LANE)
    ap.add_argument("--duration-s", type=float, default=DURATION_S)
    ap.add_argument("--warmup-s", type=float, default=WARMUP_S)
    ap.add_argument("--episode-sample", type=int, default=EPISODE_SAMPLE)
    ap.add_argument("--episode-seed", type=int, default=EPISODE_SEED)
    ap.add_argument("--procs", type=int, default=3)
    args = ap.parse_args(argv)
    if (args.fd is None) == (args.target is None):
        ap.error("give exactly one of --fd and --target")
    sidecar = args.sidecar or args.out.with_name(args.out.stem + ".calibration.json")

    src = IDMCalibration.load(args.source)
    if args.target is not None:
        target = float(args.target)
        target_source = "explicit --target"
    else:
        fd = json.loads(args.fd.read_text())
        target = veh_s_to_veh_h(fd["fd"]["ci95"]["q_max"][0])
        target_source = f"{_rel(args.fd)} fd.ci95.q_max[0] (bootstrap lower bound)"
    print(f"target capacity {target:.0f} veh/h/lane ({target_source})")

    t_scales = tuple(float(f) for f in args.t_scales)
    seeds = tuple(int(s) for s in args.seeds)
    base_scenario = str(args.base_scenario) if args.base_scenario else None
    if base_scenario:
        load_scenario(base_scenario)  # fail early on a bad base
    with tempfile.TemporaryDirectory() as td:
        paths: dict[float, str] = {}
        for f in t_scales:
            p = Path(td) / f"idm_T{f:.4f}.json"
            p.write_text(
                derived_artifact(src, f, f"capacity-calibration trial, T x {f}").model_dump_json()
            )
            paths[f] = str(p)
        jobs = [
            {
                "t_scale": f,
                "seed": s,
                "artifact_path": paths[f],
                "base_scenario": base_scenario,
                "lanes": args.lanes,
                "length_m": args.length_m,
                "x_ref_m": args.x_ref_m,
                "demand_veh_h_lane": args.demand_veh_h_lane,
                "duration_s": args.duration_s,
                "warmup_s": args.warmup_s,
                "name": f"{args.out.stem}_calibration",
            }
            for f in t_scales
            for s in seeds
        ]
        with mp.get_context("spawn").Pool(min(args.procs, len(jobs))) as pool:
            rows = pool.map(_job, jobs)
    table = capacity_table(rows, t_scales, src.mean["T"])
    for r in table:
        flag = "  [demand-limited: raise --demand-veh-h-lane]" if r["demand_limited"] else ""
        print(
            f"  T x {r['T_scale']:.2f} (T = {r['T_mean_s']:.3f} s): "
            f"capacity {r['capacity_veh_h_lane']:.0f} veh/h/lane{flag}"
        )
    f_star, how = interpolate_scale(
        [r["T_scale"] for r in table], [r["capacity_veh_h_lane"] for r in table], target
    )
    print(f"f* = {f_star:.4f} ({how})")

    episodes: list[Any] = []
    if args.episodes is not None and args.episodes.exists():
        episodes = pickle.load(args.episodes.open("rb"))
    elif args.episodes is not None:
        print(f"episodes not found at {args.episodes}: cost not evaluated")
    sample = sample_episodes(episodes, args.episode_sample, args.episode_seed)
    note = (
        f"Derived from {_rel(args.source)} by scripts/calibrate_capacity.py: population mean T "
        f"scaled by {f_star:.4f} so that the straight {args.lanes}-lane capacity meets "
        f"{target:.0f} veh/h/lane ({target_source}). Covariance and other means unchanged. "
        f"FHWA Traffic Analysis Toolbox Vol. III step 1 (capacity calibration). "
        f"Details: {_rel(sidecar)}. Source notes: {src.notes}"
    )
    derived = derived_artifact(src, f_star, note).model_copy(
        update={"created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    rmse_before = episode_rmse(src, sample) if sample else float("nan")
    rmse_after = episode_rmse(derived, sample) if sample else float("nan")
    if sample:
        print(
            f"population-mean gap RMSE on {len(sample)} sampled episodes: "
            f"{rmse_before:.3f} m -> {rmse_after:.3f} m"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(derived.model_dump_json(indent=1))
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": derived.created_at,
                "versions": _versions(),
                "script": "scripts/calibrate_capacity.py",
                "source": _rel(args.source),
                "base_scenario": _rel(args.base_scenario) if args.base_scenario else None,
                "target_veh_h_lane": round(target, 1),
                "target_source": target_source,
                "corridor": {
                    "length_m": args.length_m,
                    "lanes": args.lanes,
                    "x_ref_m": args.x_ref_m,
                    "demand_veh_h_lane": args.demand_veh_h_lane,
                    "duration_s": args.duration_s,
                    "warmup_s": args.warmup_s,
                },
                "t_scales": list(t_scales),
                "seeds": list(seeds),
                "table": table,
                "runs": rows,
                "T_scale": round(f_star, 4),
                "T_mean_s": {"before": src.mean["T"], "after": derived.mean["T"]},
                "interpolation": how,
                "episode_gap_rmse_m": {
                    "episodes": _rel(args.episodes) if args.episodes else None,
                    "n_episodes": len(sample),
                    "seed": args.episode_seed,
                    "before": round(rmse_before, 4) if sample else None,
                    "after": round(rmse_after, 4) if sample else None,
                },
            },
            indent=1,
        )
    )
    print(f"-> {args.out}\n-> {sidecar}")


if __name__ == "__main__":
    main()
