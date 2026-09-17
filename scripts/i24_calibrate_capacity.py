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

``--equilibrium`` is the same question asked **without a simulation**, so that
two populations can be compared before a SUMO grid is spent on either. It
computes each population's straight-road *equilibrium* capacity in closed
form — the quantity docs/I24_CAPACITY.md §1 quotes as "about 1,780 veh/h per
lane at 17 m/s" for ``artifacts/idm_i24.json``:

    s_eq(v) = (s0 + v·T) / sqrt(1 − (v/v0)^4)      (CLAUDE.md §9)
    q(v)    = v / (s_eq(v) + L),   L = 5 m vType
    q*      = max over v in (0, v0) of q(v)

evaluated at the population mean, and again over a seeded draw from the
population's truncated multivariate normal (the runner's own
``_draw_from_calibration``) to price the heterogeneity the covariance carries.
It is an index, not a simulated capacity: this script's own committed grid
puts SUMO's four-lane capacity at 0.86–0.91 of ``q*`` for the corridor-wide
population, the gap being heterogeneity, lane changes and finite acceleration.
Writes ``artifacts/idm_i24_capacity_equilibrium.json``.

Run: ``uv run --no-sync python scripts/i24_calibrate_capacity.py --procs 3``
     ``uv run --no-sync python scripts/i24_calibrate_capacity.py --equilibrium``
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
EQ_OUT = REPO / "artifacts" / "idm_i24_capacity_equilibrium.json"

#: Populations compared by ``--equilibrium`` (missing ones are skipped).
EQ_POPULATIONS = (
    "artifacts/idm_i24.json",
    "artifacts/idm_i24_capacity.json",
    "artifacts/idm_i24_merge.json",
    "artifacts/idm_i24_merge_capacity.json",
)
VEHICLE_LENGTH_M = 5.0  # the replica's passenger vType (scripts/i24_build_replica.py)
EQ_DRAW_N = 20000
EQ_DRAW_SEED = 11
EQ_V_STEPS = 40001

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


# --------------------------------------------------------------------------
# Closed-form equilibrium capacity (--equilibrium; no simulation)
# --------------------------------------------------------------------------


def equilibrium_capacity(
    params: dict[str, float], vehicle_length: float = VEHICLE_LENGTH_M
) -> tuple[float, float]:
    """Straight-road equilibrium capacity of one IDM parameter vector.

    ``q(v) = v / (s_eq(v) + L)`` with the IDM equilibrium gap
    ``s_eq(v) = (s0 + v·T)/sqrt(1 − (v/v0)^4)`` (CLAUDE.md §9), maximised over
    ``v`` on a uniform grid of :data:`EQ_V_STEPS` points in ``(0, v0)``. This
    is the single-pipe, single-driver-type capacity — no heterogeneity, no
    lane changes, no acceleration dynamics.

    Args:
        params: IDM means (v0, T, s0 used; a_max/b do not enter equilibrium).
        vehicle_length: Vehicle length [m] added to the gap for spacing.

    Returns:
        ``(q_max [veh/h], v_at_q_max [m/s])``.
    """
    v0 = float(params["v0"])
    v = np.linspace(v0 / EQ_V_STEPS, v0 * 0.999, EQ_V_STEPS)
    s_eq = (params["s0"] + v * params["T"]) / np.sqrt(1.0 - (v / v0) ** 4)
    q = v / (s_eq + vehicle_length)
    i = int(np.argmax(q))
    return float(veh_s_to_veh_h(q[i])), float(v[i])


def heterogeneous_capacity(
    cal: IDMCalibration,
    *,
    n_draws: int = EQ_DRAW_N,
    seed: int = EQ_DRAW_SEED,
    vehicle_length: float = VEHICLE_LENGTH_M,
) -> dict[str, float]:
    """Equilibrium capacity of a *drawn* population — the covariance priced in.

    Draws ``n_draws`` drivers exactly as the runner does
    (``microsim.vehicles._draw_from_calibration``: truncated multivariate
    normal, ±3σ marginals, hard physical floors) and evaluates a single-lane
    queue moving at a common speed ``v``: the mean spacing is
    ``mean_i s_eq(v; θ_i) + L``, so ``q(v) = v / that``. A driver whose ``v0``
    is at or below ``v`` cannot hold the queue speed, so the maximisation is
    confined to ``v < min_i v0_i`` — on a multi-lane road faster drivers pass
    such a driver, so this is a lower bound and the mean-parameter number an
    upper one.

    Args:
        cal: The population artifact.
        n_draws: Drivers drawn.
        seed: RNG seed for the draw.
        vehicle_length: Vehicle length [m].

    Returns:
        Capacity [veh/h], the speed at it, ``min v0`` of the draw, and whether
        the ``min v0`` bound is what stopped the maximisation.
    """
    from flowstate_core.rng import make_rng
    from microsim.vehicles import _draw_from_calibration

    draws = _draw_from_calibration(cal, n_draws, make_rng(seed))
    v0 = np.array([d["v0"] for d in draws])
    t_h = np.array([d["T"] for d in draws])
    s0 = np.array([d["s0"] for d in draws])
    v_hi = float(v0.min()) * 0.999
    v = np.linspace(v_hi / EQ_V_STEPS, v_hi, EQ_V_STEPS)
    s_eq = (s0[None, :] + v[:, None] * t_h[None, :]) / np.sqrt(
        1.0 - (v[:, None] / v0[None, :]) ** 4
    )
    q = v / (s_eq.mean(axis=1) + vehicle_length)
    i = int(np.argmax(q))
    return {
        "capacity_veh_h_lane": round(float(veh_s_to_veh_h(q[i])), 1),
        "v_at_capacity_ms": round(float(v[i]), 3),
        "min_v0_ms": round(float(v0.min()), 3),
        "limited_by_min_v0": bool(i >= EQ_V_STEPS - 2),
        "n_draws": n_draws,
        "seed": seed,
    }


def equilibrium_report(paths: tuple[str, ...] = EQ_POPULATIONS) -> dict[str, Any]:
    """Compare the populations' straight-road equilibrium capacities.

    Also calibrates the index against this script's own committed simulated
    grid (``artifacts/idm_i24_capacity.calibration.json``): every point of that
    grid is the corridor-wide population with its mean ``T`` scaled, so the
    ratio simulated/closed-form there is the model factor (heterogeneity, lane
    changes, acceleration) that a closed-form number has to be multiplied by
    before it can be read as a four-lane SUMO capacity.

    Args:
        paths: Repo-relative ``IDMCalibration`` paths; missing ones are skipped.

    Returns:
        The report dict written to ``artifacts/idm_i24_capacity_equilibrium.json``.
    """
    rows = []
    for rel in paths:
        path = REPO / rel
        if not path.is_file():
            continue
        cal = IDMCalibration.load(path)
        q, v = equilibrium_capacity(dict(cal.mean))
        sd = np.sqrt(np.diag(np.array(cal.cov, dtype=float)))
        rows.append(
            {
                "artifact": rel,
                "mean": {k: round(float(x), 4) for k, x in cal.mean.items()},
                "sd": {k: round(float(s), 4) for k, s in zip(cal.param_names, sd, strict=True)},
                "n_episodes_fit": cal.n_episodes_fit,
                "n_episodes_holdout": cal.n_episodes_holdout,
                "holdout_gap_rmse_m": (
                    None
                    if cal.holdout_gap_rmse_m is None or not np.isfinite(cal.holdout_gap_rmse_m)
                    else round(float(cal.holdout_gap_rmse_m), 4)
                ),
                "equilibrium_capacity_veh_h_lane": round(q, 1),
                "v_at_capacity_ms": round(v, 3),
                "heterogeneous": heterogeneous_capacity(cal),
            }
        )
    model_factor: list[dict[str, Any]] = []
    if SIDECAR.is_file() and (REPO / EQ_POPULATIONS[0]).is_file():
        src = IDMCalibration.load(REPO / EQ_POPULATIONS[0])
        for point in json.loads(SIDECAR.read_text())["table"]:
            mean = dict(src.mean)
            mean["T"] = float(point["T_mean_s"])
            q, _ = equilibrium_capacity(mean)
            model_factor.append(
                {
                    "T_mean_s": point["T_mean_s"],
                    "closed_form_veh_h_lane": round(q, 1),
                    "simulated_veh_h_lane": point["capacity_veh_h_lane"],
                    "ratio": round(point["capacity_veh_h_lane"] / q, 4),
                }
            )
    ratios = [row["ratio"] for row in model_factor]
    return {
        "schema_version": 1,
        "kind": "idm_equilibrium_capacity",
        "formula": (
            "s_eq(v) = (s0 + v*T)/sqrt(1 - (v/v0)^4); q(v) = v/(s_eq(v) + L), L = "
            f"{VEHICLE_LENGTH_M:g} m; capacity = max over v in (0, v0) of q(v). "
            "Closed form, straight road, one lane, no lane changes."
        ),
        "vehicle_length_m": VEHICLE_LENGTH_M,
        "populations": rows,
        "model_factor": {
            "source": str(SIDECAR.relative_to(REPO)),
            "meaning": (
                "simulated four-lane SUMO capacity divided by the closed-form capacity of the "
                "same population, over this script's committed T-scaling grid; multiply a "
                "closed-form number by this to read it as a simulated capacity"
            ),
            "min": round(min(ratios), 4) if ratios else None,
            "max": round(max(ratios), 4) if ratios else None,
            "rows": model_factor,
        },
    }


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
    ap.add_argument(
        "--equilibrium",
        action="store_true",
        help="no simulation: write artifacts/idm_i24_capacity_equilibrium.json comparing the "
        "closed-form straight-road capacity of every population in EQ_POPULATIONS",
    )
    args = ap.parse_args()
    if args.equilibrium:
        report = equilibrium_report()
        EQ_OUT.write_text(json.dumps(report, indent=1))
        print(report["formula"])
        head = f"{'population':44s} {'T':>6s} {'s0':>5s} {'v0':>6s} {'q* [veh/h/lane]':>16s}"
        print(head)
        for row in report["populations"]:
            print(
                f"{row['artifact']:44s} {row['mean']['T']:6.3f} {row['mean']['s0']:5.2f} "
                f"{row['mean']['v0']:6.2f} {row['equilibrium_capacity_veh_h_lane']:16.1f}"
                f"  (drawn population {row['heterogeneous']['capacity_veh_h_lane']:.0f};"
                f" holdout gap RMSE {row['holdout_gap_rmse_m']} m)"
            )
        mf = report["model_factor"]
        print(f"simulated/closed-form over the committed grid: {mf['min']}-{mf['max']}")
        print(f"-> {EQ_OUT}")
        return
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
