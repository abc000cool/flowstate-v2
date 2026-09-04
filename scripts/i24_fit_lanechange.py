"""Fit SUMO's lane-change parameters on the I-24 lane observables
(docs/I24_CAPACITY.md §6, the step after the demand level).

The merge diagnostic left the Old Hickory merge behaviour as the replica's
residual and named ``lcCooperative``, ``lcAssertive`` and ``lcSpeedGain``
(with ``lcKeepRight``) as the levers. This driver evaluates a grid over
``FleetSpec.lc_cooperative / lc_assertive / lc_speed_gain / lc_keep_right``
on ``scenarios/i24_replica_speedcal.yaml`` with ONE seeded replicate per
point (the scenario's first replicate seed, as ``scripts/i24_fit_demand_scale.py``
does), computes the simulated lane observables of every run with
``calibration.lanechange`` on the same sections as the observed side
(``artifacts/i24_lanechange_observed.json``, from
``scripts/i24_lanechange_observed.py``), scores each point with
``lane_change_objective`` on the FIRST HOUR of the study period
(06:30–07:30 CST) and reports the SECOND hour (07:30–08:30) held out, and
writes a ``LaneChangeCalibration`` artifact. The objective is the lane-use
share table plus the lane-change rate — never a validation criterion
(CLAUDE.md §0.1; the speed/GEH/wave criteria are evaluated afterwards by
``scripts/i24_validate.py``).

Coordinates: sim x = a + b · data x and sim t = study t + 600 s
(``artifacts/i24_replica_inputs.json`` ``geometry``), exactly as
``scripts/i24_validate.py`` maps replicates. SUMO lane indices are mapped to
the data's band convention (1 = leftmost) with the compiled edges' lane
counts (``calibration.lanechange.band_lane_from_sim``); the assumption that
extra lanes are on the right (acceleration / weaving lanes) is verified on
the compiled net of every run before its trajectories are scored.

Runs land under ``runs/i24_lanechange/<config_hash>/<seed>/`` (kept, so a
grid can be resumed with ``--reuse-runs``). A full replica run takes minutes
and 2–3 GB; keep ``--procs`` at 2 on a 16 GB laptop and run the grid on the
VM.

Usage (repo root)::

    uv run --no-sync python scripts/i24_fit_lanechange.py --smoke                     # mechanics
    uv run --no-sync python scripts/i24_fit_lanechange.py --procs 8                   # default grid
    uv run --no-sync python scripts/i24_fit_lanechange.py --procs 8 \\
        --grid "lc_cooperative=1,0.5,0;lc_assertive=1,2,4;lc_speed_gain=1;lc_keep_right=0"
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import WARMUP_S
from i24_data import REPO_ROOT
from i24_lanechange_observed import (
    FIT_WINDOW_S,
    HIST_DX_M,
    HOLDOUT_WINDOW_S,
    LANES,
    MIN_DWELL_S,
    inputs,
    print_table,
    sum_windows,
)
from i24_lanechange_observed import OUT as OBSERVED

from calibration.lanechange import (
    DEFAULT_MAX_GAP_FACTOR,
    DEFAULT_RATE_WEIGHT,
    LaneObservables,
    band_lane_from_sim,
    lane_change_objective,
    lane_observables,
)
from flowstate_core.artifacts import (
    LANE_CHANGE_PARAMS,
    LaneChangeCalibration,
    LaneChangeGridPoint,
    LaneObservablesRecord,
)
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions
from microsim.scenarios import load_scenario

SCENARIO = "i24_replica_speedcal"
OUT_ROOT = REPO_ROOT / "runs" / "i24_lanechange"
OUT = REPO_ROOT / "artifacts" / "i24_lanechange_fit.json"
SMOKE_OUT = OUT_ROOT / "smoke" / "i24_lanechange_fit_smoke.json"
DEFAULT_GRID = "lc_cooperative=1,0.5,0;lc_assertive=1,2,4;lc_speed_gain=1,0.5;lc_keep_right=0"
"""18 points around SUMO's defaults and the merge diagnostic's variants."""
SMOKE_STUDY_S = 900.0
"""Study time simulated by ``--smoke`` (one observed chunk) after the warmup."""


def parse_grid(spec: str) -> list[dict[str, float]]:
    """``name=v1,v2;name=v3`` → the Cartesian product as parameter dicts.

    Names must be ``LANE_CHANGE_PARAMS``; a parameter left out keeps the
    scenario's value.
    """
    axes: dict[str, list[float]] = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, values = part.partition("=")
        name = name.strip()
        if name not in LANE_CHANGE_PARAMS:
            raise ValueError(f"unknown lane-change parameter {name!r}; use {LANE_CHANGE_PARAMS}")
        vals = [float(v) for v in values.split(",") if v.strip()]
        if not vals:
            raise ValueError(f"no values for {name}")
        axes[name] = vals
    if not axes:
        raise ValueError("empty grid")
    names = list(axes)
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*axes.values())]


def point_config(base: ScenarioConfig, point: dict[str, float], smoke: bool) -> ScenarioConfig:
    """Base scenario with the point's lane-change fields (and a short smoke horizon)."""
    fleet = base.fleet.model_copy(update=point)
    update: dict[str, Any] = {"fleet": fleet}
    if smoke:
        update["sim"] = base.sim.model_copy(update={"duration_s": WARMUP_S + SMOKE_STUDY_S})
    return base.model_copy(update=update)


def edge_tables() -> tuple[list[float], list[int], list[str]]:
    """Sim-x offsets, lane counts and ids of the corridor edges (inputs geometry)."""
    geo = inputs()["geometry"]
    lengths = [float(v) for v in geo["edge_lengths_m"]]
    offsets = [0.0, *(float(v) for v in np.cumsum(lengths)[:-1])]
    return offsets, [int(n) for n in geo["edge_lanes"]], list(geo["corridor_edges"])


def check_lane_mapping(
    net_path: Path, corridor_edges: list[str], ramps: list[dict[str, Any]]
) -> dict[str, Any]:
    """Verify on the compiled net that ``band = n_lanes − index`` is right.

    Two facts make the mapping valid: the leftmost lane of every corridor
    edge continues into the leftmost lane of the next (so the extra lanes of
    5-lane edges are on the right), and every on-ramp enters its attach edge
    on lane 0 (the auxiliary lane is the rightmost one).
    """
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    problems: list[str] = []
    for a, b in pairwise(corridor_edges):
        ea, eb = net.getEdge(a), net.getEdge(b)
        na, nb = ea.getLaneNumber(), eb.getLaneNumber()
        pairs = sorted(
            (c.getFromLane().getIndex(), c.getToLane().getIndex())
            for c in ea.getOutgoing().get(eb, [])
        )
        if (na - 1, nb - 1) not in pairs:
            problems.append(
                f"{a}->{b}: leftmost lane {na - 1} does not continue into {nb - 1}; {pairs}"
            )
    for ramp in ramps:
        if ramp["kind"] != "on":
            continue
        er, ea = net.getEdge(ramp["edges"][-1]), net.getEdge(ramp["attach_edge"])
        to_lanes = sorted({c.getToLane().getIndex() for c in er.getOutgoing().get(ea, [])})
        if to_lanes != [0]:
            problems.append(
                f"on-ramp {ramp['edges'][-1]} enters {ramp['attach_edge']} on lanes {to_lanes}, not [0]"
            )
    return {"ok": not problems, "problems": problems, "net": str(net_path)}


def sim_frame(
    run_dir: Path, offsets: list[float], edge_lanes: list[int], a: float, b: float
) -> pd.DataFrame:
    """One run's trajectories in observed coordinates with band lanes."""
    df = pd.read_parquet(
        run_dir / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"]
    )
    df["lane"] = band_lane_from_sim(
        df["lane"].to_numpy(dtype=np.int64), df["x"].to_numpy(dtype=np.float64), offsets, edge_lanes
    )
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - WARMUP_S
    return df[df["t"] >= 0.0]


def observed_window(
    obs: dict[str, Any], partition: str, window: tuple[float, float]
) -> LaneObservables:
    """Observed observables on a window that is a union of the stored chunks."""
    chunks = [
        LaneObservables.from_record(LaneObservablesRecord.model_validate(c))
        for c in obs[partition]["chunks"]
    ]
    return sum_windows(chunks, window)


def _point_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one grid point (unless reusable) and score it — spawn-pool worker."""
    from microsim.runner import run_micro

    cfg = ScenarioConfig.model_validate(payload["cfg"])
    seed: int = payload["seed"]
    out_root = Path(payload["out_root"])
    obs = json.loads(Path(payload["observed"]).read_text())
    windows: dict[str, tuple[float, float] | None] = {
        k: (tuple(v) if v is not None else None) for k, v in payload["windows"].items()
    }
    rate_weight: float = payload["rate_weight"]
    chash = config_hash(cfg)
    run_dir = out_root / chash / str(seed)
    t0 = time.perf_counter()
    reused = (
        payload["reuse"]
        and (run_dir / "meta.json").is_file()
        and (run_dir / "trajectories.parquet").is_file()
    )
    if not reused:
        run_micro(cfg, seed, out_root)
    wall_run = time.perf_counter() - t0
    meta = json.loads((run_dir / "meta.json").read_text())

    offsets, edge_lanes, corridor_edges = edge_tables()
    nets = sorted((run_dir / "net").glob("*.net.xml"))
    mapping = (
        check_lane_mapping(nets[0], corridor_edges, inputs()["ramps"])
        if nets
        else {"ok": False, "problems": ["no compiled net found"], "net": ""}
    )
    if not mapping["ok"]:
        raise RuntimeError(f"lane mapping assumption violated for {chash}: {mapping['problems']}")

    geo = inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    df = sim_frame(run_dir, offsets, edge_lanes, a, b)
    dt_s = 1.0 / cfg.sim.output_hz
    kw: dict[str, Any] = {
        "lanes": LANES,
        "dt_s": dt_s,
        "max_gap_s": DEFAULT_MAX_GAP_FACTOR * dt_s,
        "min_dwell_s": MIN_DWELL_S,
        "hist_dx_m": HIST_DX_M,
    }
    sec_edges = obs["sections"]["x_edges_m"]
    zone_edges = obs["ramp_zones"]["x_edges_m"]
    result: dict[str, Any] = {
        "params": payload["point"],
        "config_hash": chash,
        "seed": seed,
        "run_dir": str(run_dir),
        "reused": bool(reused),
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "wall_s": round(wall_run, 1),
        "lane_mapping_check": mapping,
        "records": {},
        "objective": {},
    }
    for name, window in windows.items():
        if window is None:
            continue
        sim_sec = lane_observables(df, sec_edges, window_s=window, **kw)
        sim_zone = lane_observables(df, zone_edges, window_s=window, **kw)
        obs_sec = observed_window(obs, "sections", window)
        j = lane_change_objective(sim_sec, obs_sec, rate_weight=rate_weight)
        result["records"][f"sections_{name}"] = sim_sec.to_record().model_dump(mode="json")
        result["records"][f"zones_{name}"] = sim_zone.to_record().model_dump(mode="json")
        result["objective"][name] = j.to_dict()
    result["wall_total_s"] = round(time.perf_counter() - t0, 1)
    return result


def _fmt(v: float | None) -> str:
    return "   --  " if v is None else f"{v:7.4f}"


def print_grid(rows: list[dict[str, Any]]) -> None:
    print(
        "  coop  assert  sgain  kright   inserted   J_fit   share_rms  rate_rmspe   J_holdout   wall[s]"
    )
    for r in rows:
        p = r["params"]
        jf = r["objective"].get("fit", {})
        jh = r["objective"].get("holdout", {})
        print(
            f"  {p['lc_cooperative']:4.2f}  {p['lc_assertive']:5.2f}  {p['lc_speed_gain']:5.2f}  {p['lc_keep_right']:5.2f}"
            f"   {r['inserted_fraction']:.3f}   {_fmt(jf.get('value'))}  {_fmt(jf.get('share_rms'))}  {_fmt(jf.get('rate_rmspe'))}"
            f"   {_fmt(jh.get('value'))}   {r['wall_s']:7.0f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--scenario", default=SCENARIO)
    ap.add_argument(
        "--procs", type=int, default=2, help="SUMO processes (<= 2 on the 16 GB laptop)"
    )
    ap.add_argument("--grid", default=DEFAULT_GRID, help="name=v1,v2;name=v3 (Cartesian product)")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="one point (the scenario's values), 900 s of study time",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="replicate seed (default: the scenario's first replicate seed)",
    )
    ap.add_argument("--rate-weight", type=float, default=DEFAULT_RATE_WEIGHT)
    ap.add_argument("--observed", type=Path, default=OBSERVED)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--reuse-runs",
        action="store_true",
        help="score runs already on disk instead of re-simulating",
    )
    args = ap.parse_args()
    t0 = time.perf_counter()

    base = load_scenario(args.scenario)
    base_point = {name: float(getattr(base.fleet, name)) for name in LANE_CHANGE_PARAMS}
    grid = (
        [dict(base_point)] if args.smoke else [{**base_point, **p} for p in parse_grid(args.grid)]
    )
    seed = args.seed if args.seed is not None else spawn_seeds(base.seed, base.replicates)[0]
    out_root = OUT_ROOT / "smoke" if args.smoke else OUT_ROOT
    out_path = args.out or (SMOKE_OUT if args.smoke else OUT)
    obs = json.loads(args.observed.read_text())
    windows: dict[str, tuple[float, float] | None]
    if args.smoke:
        windows = {"fit": (0.0, SMOKE_STUDY_S), "holdout": None}
    else:
        windows = {"fit": FIT_WINDOW_S, "holdout": HOLDOUT_WINDOW_S}
    print(
        f"scenario {args.scenario} (config {config_hash(base)}), seed {seed}, {len(grid)} grid point(s), "
        f"fit window {windows['fit']}, holdout {windows['holdout']}, procs {args.procs}"
        + (" [SMOKE]" if args.smoke else ""),
        flush=True,
    )
    payloads = [
        {
            "cfg": point_config(base, point, args.smoke).model_dump(mode="json"),
            "point": point,
            "seed": seed,
            "out_root": str(out_root),
            "observed": str(args.observed),
            "windows": {k: (list(v) if v is not None else None) for k, v in windows.items()},
            "rate_weight": args.rate_weight,
            "reuse": args.reuse_runs,
        }
        for point in grid
    ]
    rows: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    with ctx.Pool(min(args.procs, len(payloads))) as pool:
        for r in pool.imap_unordered(_point_worker, payloads):
            rows.append(r)
            jf = r["objective"]["fit"]
            print(
                f"  done {r['params']} -> J_fit {_fmt(jf['value'])} (inserted {r['inserted_fraction']:.3f}, "
                f"{'reused' if r['reused'] else f'{r["wall_s"]:.0f} s'})",
                flush=True,
            )
    order = {json.dumps(p, sort_keys=True): i for i, p in enumerate(grid)}
    rows.sort(key=lambda r: order[json.dumps(r["params"], sort_keys=True)])
    scored = [r for r in rows if r["objective"]["fit"]["value"] is not None]
    if not scored:
        raise RuntimeError("no grid point could be scored on the fit window")
    best = min(scored, key=lambda r: r["objective"]["fit"]["value"])
    print_grid(rows)
    jb = best["objective"]["fit"]
    jh = best["objective"].get("holdout", {})
    print(
        f"best {best['params']} (config {best['config_hash']}): J_fit {jb['value']:.4f} "
        f"(share_rms {jb['share_rms']:.4f}, rate_rmspe {jb['rate_rmspe']:.4f}), J_holdout {_fmt(jh.get('value'))}"
    )

    def rec(r: dict[str, Any], key: str) -> LaneObservablesRecord | None:
        d = r["records"].get(key)
        return None if d is None else LaneObservablesRecord.model_validate(d)

    obs_fit = observed_window(obs, "sections", windows["fit"]).to_record()  # type: ignore[arg-type]
    obs_zone_fit = observed_window(obs, "ramp_zones", windows["fit"]).to_record()  # type: ignore[arg-type]
    extra: dict[str, LaneObservablesRecord] = {"observed_zones_fit": obs_zone_fit}
    sim_zone_fit = rec(best, "zones_fit")
    if sim_zone_fit is not None:
        extra["simulated_zones_fit"] = sim_zone_fit
    obs_hold = None
    if windows["holdout"] is not None:
        obs_hold = observed_window(obs, "sections", windows["holdout"]).to_record()
        extra["observed_zones_holdout"] = observed_window(
            obs, "ramp_zones", windows["holdout"]
        ).to_record()
        sim_zone_hold = rec(best, "zones_holdout")
        if sim_zone_hold is not None:
            extra["simulated_zones_holdout"] = sim_zone_hold
    grid_points = [
        LaneChangeGridPoint(
            params=r["params"],
            config_hash=r["config_hash"],
            seed=r["seed"],
            objective_fit=r["objective"]["fit"]["value"],
            objective_holdout=r["objective"].get("holdout", {}).get("value"),
            share_rms_fit=r["objective"]["fit"]["share_rms"],
            rate_rmspe_fit=r["objective"]["fit"]["rate_rmspe"],
            share_rms_holdout=r["objective"].get("holdout", {}).get("share_rms"),
            rate_rmspe_holdout=r["objective"].get("holdout", {}).get("rate_rmspe"),
            inserted_fraction=r["inserted_fraction"],
            wall_s=r["wall_s"],
        )
        for r in rows
    ]
    smoke_note = (
        f" SMOKE: one point, {SMOKE_STUDY_S:g} s of study time after the warmup, scored against the first observed chunk only; not a calibration."
        if args.smoke
        else ""
    )
    art = LaneChangeCalibration(
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=f"{obs['source']}; scenario {args.scenario}",
        data_hash=obs["data_hash"],
        params=best["params"],
        scenario=args.scenario,
        scenario_config_hash=config_hash(base),
        fit_config_hash=best["config_hash"],
        seed=seed,
        fit_window_s=windows["fit"],  # type: ignore[arg-type]
        holdout_window_s=windows["holdout"],
        objective=jb["value"],
        objective_holdout=jh.get("value"),
        objective_spec={"rate_weight": args.rate_weight},
        observed_fit=obs_fit,
        observed_holdout=obs_hold,
        simulated_fit=rec(best, "sections_fit"),  # type: ignore[arg-type]
        simulated_holdout=rec(best, "sections_holdout"),
        grid=grid_points,
        extra_observables=extra,
        zone_names=list(obs["ramp_zones"]["names"]),
        observed_source=str(
            args.observed.relative_to(REPO_ROOT) if args.observed.is_absolute() else args.observed
        ),
        versions=_versions(),
        smoke=args.smoke,
        notes=(
            "Objective: calibration.lanechange.lane_change_objective = RMS lane-share difference over "
            "(section, lane) + rate_weight x RMS relative lane-change-rate error over sections; fitted "
            f"on study time {windows['fit']} (06:30-07:30 CST), held out {windows['holdout']}; one seeded "
            "replicate per grid point; ties keep grid order. Observed rates are ratios of coverage-limited "
            "counts (docs/I24_DATA.md §4). Sim lanes mapped to the band convention with band = n_lanes - "
            "index, verified on each run's compiled net (leftmost-lane continuity, on-ramps enter lane 0). "
            f"Grid: {'smoke' if args.smoke else args.grid}." + smoke_note
        ),
    )
    art.save(out_path)
    sim_fit = rec(best, "sections_fit")
    if sim_fit is not None:
        print_table("[best point, fit window] simulated sections", sim_fit)
        print_table("[observed, fit window] sections", obs_fit)
    print(f"wrote {out_path} in {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
