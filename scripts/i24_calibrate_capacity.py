"""Capacity calibration of the I-24 car-following population (FHWA Vol. III step 1).

``scripts/i24_capacity_experiment.py`` shows that the population fitted on
car-following episodes (``artifacts/idm_i24.json``, T = 1.51 s) sustains
about 1,650 veh/h per lane on a straight four-lane corridor, below the
1,780 veh/h per lane the instrument *tracked* at the same site — a lower
bound on what the road carried (``artifacts/fd_i24.json``, ``q_max`` CI low
end). A replica whose fleet cannot carry the observed flow queues from the
first window whatever the demand estimate.

The FHWA Traffic Analysis Toolbox Vol. III calibration procedure addresses
exactly this: calibrate capacity first (car-following parameters against
field capacity), then demand, then system performance. This script scales
the population's mean desired time headway ``T`` — the IDM parameter that
sets capacity — by a factor ``f`` and finds the ``f*`` at which the
simulated capacity meets the field target, by linear interpolation between
grid points. Nothing else changes: the covariance, the other means, the
lane-change parameters and the demand are untouched. The cost is reported
honestly: the population-mean gap RMSE over a seeded sample of the
car-following episodes, before and after.

The target is the tracked lower bound, because it is the only field
capacity available without the radar-detector counts (ROADMAP §6); the true
capacity is higher, so ``f*`` is conservative.

Outputs ``artifacts/idm_i24_capacity.json`` (a derived ``IDMCalibration``
usable as ``fleet.idm_calibration``) and the sidecar
``artifacts/idm_i24_capacity.calibration.json`` with the full table.

Run: ``uv run --no-sync python scripts/i24_calibrate_capacity.py --procs 3``
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from calibration.idm_fit import gap_rmse
from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import CorridorNetwork, ScenarioConfig
from flowstate_core.units import h_to_s, veh_h_to_veh_s, veh_s_to_veh_h
from microsim import load_scenario, run_micro
from microsim.runner import _versions

REPO = Path(__file__).resolve().parents[1]
REPLICA_YAML = REPO / "scenarios" / "i24_replica_corrected.yaml"
SOURCE = REPO / "artifacts" / "idm_i24.json"
FD = REPO / "artifacts" / "fd_i24.json"
EPISODES = REPO / "data" / "i24motion" / "processed" / "i24_wb_episodes.pkl"
OUT = REPO / "artifacts" / "idm_i24_capacity.json"
SIDECAR = REPO / "artifacts" / "idm_i24_capacity.calibration.json"

T_SCALES = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75)
SEEDS = (1, 2)
LANES = 4
LENGTH_M = 4000.0
X_REF_M = 3000.0
DEMAND_VEH_H_LANE = 2400.0  # saturating: capacity is what gets through
DURATION_S = 1800.0
WARMUP_S = 300.0
EPISODE_SAMPLE = 1500
EPISODE_SEED = 7


def derived_artifact(src: IDMCalibration, f: float, note: str) -> IDMCalibration:
    mean = dict(src.mean)
    mean["T"] = mean["T"] * f
    return src.model_copy(update={"mean": mean, "notes": note})


def crossings_per_hour(
    t: np.ndarray, x: np.ndarray, ids: np.ndarray, x_ref: float, t_lo: float, t_hi: float
) -> float:
    beyond = x >= x_ref
    first: dict[Any, float] = {}
    order = np.argsort(t, kind="stable")
    for i in order:
        if beyond[i] and ids[i] not in first:
            first[ids[i]] = float(t[i])
    n = sum(1 for tt in first.values() if t_lo <= tt < t_hi)
    return n * h_to_s(1.0) / (t_hi - t_lo)


def _job(args: tuple[float, int, str]) -> dict[str, Any]:
    f, seed, artifact_path = args
    import pandas as pd

    base = load_scenario(REPLICA_YAML)
    fleet = base.fleet.model_copy(update={"idm_calibration": artifact_path})
    cfg = ScenarioConfig(
        name="i24_capacity_calibration",
        tier="micro",
        network=CorridorNetwork(
            length_m=LENGTH_M,
            lanes=LANES,
            inflow=[(0.0, veh_h_to_veh_s(DEMAND_VEH_H_LANE * LANES))],
        ),
        fleet=fleet,
        av=base.av,
        sim=base.sim.model_copy(update={"duration_s": DURATION_S, "warmup_s": WARMUP_S}),
        perturbation=None,
        seed=seed,
        replicates=1,
    )
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v"])
    thr = (
        crossings_per_hour(
            df["t"].to_numpy(),
            df["x"].to_numpy(),
            df["veh_id"].to_numpy(),
            X_REF_M,
            WARMUP_S,
            DURATION_S,
        )
        / LANES
    )
    mid = df[(df["x"] >= X_REF_M - 500) & (df["x"] < X_REF_M + 500) & (df["t"] >= WARMUP_S)]
    return {
        "T_scale": f,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "throughput_veh_h_lane": round(thr, 1),
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "mean_speed_ms_at_ref": round(float(mid["v"].mean()), 3) if len(mid) else None,
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def episode_rmse(cal: IDMCalibration, episodes: list[Any]) -> float:
    params = dict(cal.mean)
    return float(np.mean([gap_rmse(e, params) for e in episodes]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument(
        "--target", type=float, default=None, help="veh/h/lane; default: FD q_max CI lower bound"
    )
    args = ap.parse_args()
    src = IDMCalibration.load(SOURCE)
    fd = json.loads(FD.read_text())
    target = (
        args.target if args.target is not None else veh_s_to_veh_h(fd["fd"]["ci95"]["q_max"][0])
    )
    print(f"target capacity {target:.0f} veh/h/lane (FD q_max lower bound, artifacts/fd_i24.json)")

    with tempfile.TemporaryDirectory() as td:
        paths: dict[float, str] = {}
        for f in T_SCALES:
            p = Path(td) / f"idm_T{f:.3f}.json"
            p.write_text(
                derived_artifact(src, f, f"capacity-calibration trial, T x {f}").model_dump_json()
            )
            paths[f] = str(p)
        jobs = [(f, s, paths[f]) for f in T_SCALES for s in SEEDS]
        with mp.get_context("spawn").Pool(min(args.procs, len(jobs))) as pool:
            rows = pool.map(_job, jobs)
    table = []
    for f in T_SCALES:
        rs = [r for r in rows if r["T_scale"] == f]
        cap = float(np.mean([r["throughput_veh_h_lane"] for r in rs]))
        table.append(
            {
                "T_scale": f,
                "T_mean_s": round(src.mean["T"] * f, 4),
                "capacity_veh_h_lane": round(cap, 1),
                "n": len(rs),
            }
        )
        print(f"  T x {f:.2f} (T = {src.mean['T'] * f:.3f} s): capacity {cap:.0f} veh/h/lane")

    # Interpolate f* on the (monotone in expectation) capacity curve.
    caps = np.array([r["capacity_veh_h_lane"] for r in table])
    fs = np.array([r["T_scale"] for r in table])
    if caps[0] >= target:
        f_star, how = 1.0, "capacity already meets the target; population unchanged"
    elif caps.max() < target:
        f_star, how = (
            float(fs[int(np.argmax(caps))]),
            "target not reached within the grid; best grid point taken",
        )
    else:
        k = int(np.argmax(caps >= target))  # first index meeting the target (scales descend)
        f_hi, f_lo = fs[k - 1], fs[k]
        c_hi, c_lo = caps[k - 1], caps[k]
        f_star = float(f_hi + (target - c_hi) * (f_lo - f_hi) / (c_lo - c_hi))
        how = f"linear interpolation between T x {f_hi} ({c_hi:.0f}) and T x {f_lo} ({c_lo:.0f})"
    print(f"f* = {f_star:.4f} ({how})")

    episodes = pickle.load(EPISODES.open("rb")) if EPISODES.exists() else []
    rng = np.random.default_rng(EPISODE_SEED)
    sample = (
        [
            episodes[i]
            for i in rng.choice(
                len(episodes), size=min(EPISODE_SAMPLE, len(episodes)), replace=False
            )
        ]
        if episodes
        else []
    )
    note = (
        f"Derived from artifacts/idm_i24.json by scripts/i24_calibrate_capacity.py: population mean T "
        f"scaled by {f_star:.4f} so that the straight 4-lane capacity meets {target:.0f} veh/h/lane "
        f"(the tracked FD q_max lower bound; true capacity is higher). Covariance and other means "
        f"unchanged. FHWA Traffic Analysis Toolbox Vol. III step 1 (capacity calibration). "
        f"Details: artifacts/idm_i24_capacity.calibration.json. Source notes: {src.notes}"
    )
    derived = derived_artifact(src, f_star, note)
    rmse_before = episode_rmse(src, sample) if sample else float("nan")
    rmse_after = episode_rmse(derived, sample) if sample else float("nan")
    print(
        f"population-mean gap RMSE on {len(sample)} sampled episodes: {rmse_before:.3f} m -> {rmse_after:.3f} m"
    )
    OUT.write_text(derived.model_dump_json(indent=1))
    SIDECAR.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "versions": _versions(),
                "source": str(SOURCE.relative_to(REPO)),
                "target_veh_h_lane": round(target, 1),
                "target_source": "artifacts/fd_i24.json fd.ci95.q_max[0] (tracked lower bound)",
                "corridor": {
                    "length_m": LENGTH_M,
                    "lanes": LANES,
                    "x_ref_m": X_REF_M,
                    "demand_veh_h_lane": DEMAND_VEH_H_LANE,
                    "duration_s": DURATION_S,
                    "warmup_s": WARMUP_S,
                },
                "table": table,
                "runs": rows,
                "T_scale": round(f_star, 4),
                "T_mean_s": {"before": src.mean["T"], "after": derived.mean["T"]},
                "interpolation": how,
                "episode_gap_rmse_m": {
                    "n_episodes": len(sample),
                    "seed": EPISODE_SEED,
                    "before": round(rmse_before, 4),
                    "after": round(rmse_after, 4),
                },
            },
            indent=1,
        )
    )
    print(f"-> {OUT}\n-> {SIDECAR}")


if __name__ == "__main__":
    main()
