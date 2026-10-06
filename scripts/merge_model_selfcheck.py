"""The measured merge model's local gates: the self-check against the measurements and the fixture grid.

docs/MERGE_MODEL.md §3 and §4 (G0, G1). Everything here runs on the
repository's fixtures only (``tests/fixtures/*.osm``: the laptop rule — no
corridor scenario runs locally) and writes its run trees under ``--work-dir``
(a temporary directory, deleted afterwards unless ``--keep``).

Subcommands:

* ``check`` — the self-check (§3). Runs the merge fixtures (the McKnight Rd
  acceleration lane, ``tests/fixtures/mcknight_merge.osm``, and the corridor's
  T.H.52 weaving section, ``tests/fixtures/weave_th52_corridor.osm``, under
  their tests' observed demand) with ``merge: measured`` and, as the
  arrival-step reference, with the models they replace (``lane_change`` at
  McKnight Rd, ``weave`` at T.H.52); runs the committed extractors on the
  measured runs (``scripts/i24_critical_gaps.py --sim-run-dir``,
  ``scripts/lane_change_relaxation.py --source trajectories``) and reports

  (a) the model's own fitted critical gaps against the inputs
      (``artifacts/merge_model_params.json``, the central set): the input
      median inside the fit's 95 % interval;
  (b) the partner speeds at the change: the entrant faster than its new
      follower and than its new leader (median ``rel_speed_ms`` at 0 s);
  (c) the gap at the change: the new follower at ≤ 0.9 and the entrant
      behind its new leader at ≤ 0.8 of the population's normal time gap
      (``ratio_pop`` median at 0 s), both larger at 5 s (recovering);
  (d) arrival-step crossings not suppressed: the share of entering crossings
      made within one and two steps of the entrant's first step in the zone
      (the measured model takes the vehicle one step before it arrives and
      requests on its first step in the zone, so its earliest crossing is the
      next step), against the share the replaced model's own lane-change
      model made in the arrival step.

* ``grid`` — the 29-run fixture grid of the weave record (docs/WEAVE_MODEL_PLAN.md,
  WP-51..WP-64: the Ruth St module at both demand points and both fleets, the
  Ruth St corridor fleet at the 271.4 m window, the T.H.52 corridor-demand and
  capacity fixtures, the two two-entrance fixtures, the moderate fixture and
  the golden; seeds 3 / 4 / 5 where the record has them), plus the McKnight Rd,
  ramp and T.H.61 fixtures, for one model: per run the departures, the
  crossings by movement, give-ups, collisions, −9 m/s² vehicle-steps, the
  lowest one-minute zone speed and a lock flag.

* ``th52`` — the T.H.52 corridor section test's criteria
  (``tests/test_microsim/test_microsim_merge_managed_meter.py``,
  ``test_th52_corridor_section_carries_free_flow_demand``) at the given seeds
  (default 3–22, the protocol's 20-seed form) for one model, optionally at a
  speed factor (docs/MERGE_MODEL.md §2: 1.245 is a sensitivity, not the
  acceptance).

Cloud gates (docs/MERGE_MODEL.md §4; never run locally — corridor runs):

* ``station-flows`` — gate B's readout. A 35-minute slice holds no complete
  station-hour, so the corridor battery scores no GEH there; this reads one
  station's simulated 5-minute flows (each vehicle counted once, at its first
  sample at or past the station) from run directories with trajectories,
  beside the observed flows of the matching clock windows
  (``--clock-offset-s``: the slice's t = 0 is the four-hour run's 5,400 s,
  07:00), with each run's departed share and collisions.
* ``colliding-pairs`` — gate C. Rebuilds the (sample, seed) pairs an
  uncertainty artifact recorded collisions for
  (``validation.uncertainty.apply`` on ``--scenario``) and runs each once,
  four hours, recording the collisions, departures and zone counters.

Run (from the repository root)::

    uv run --no-sync python scripts/merge_model_selfcheck.py check --out /tmp/selfcheck.json
    uv run --no-sync python scripts/merge_model_selfcheck.py grid --model measured --out /tmp/grid.json
    uv run --no-sync python scripts/merge_model_selfcheck.py th52 --model measured --seeds 3-22
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests" / "test_microsim"
SCRIPTS = REPO / "scripts"
PARAMS = REPO / "artifacts" / "merge_model_params.json"

#: A one-minute window of a zone's edge whose mean speed over all lanes is
#: below this is a standstill; one after the warm-up flags a lock [m/s]
#: (SUMO's halting threshold, microsim.runner.HALTING_SPEED_MS, 0.1 m/s, is
#: per vehicle; a whole minute of a zone at a walking pace is a lock).
LOCK_SPEED_MS = 0.5
#: Warm-up before the lock windows are read [s] (the T.H.52 tests' 120 s).
WARMUP_S = 120.0
#: SUMO's default emergency deceleration of a passenger car [m/s²].
EMERGENCY_DECEL_MS2 = 9.0


def _load_test_module(name: str) -> ModuleType:
    """A test module of ``tests/test_microsim`` (its fixture configs are the single source)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TESTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def to_model(cfg: Any, model: str, extra_on_ramps: Sequence[str] = ()) -> Any:
    """``cfg`` with its ``weave`` / ``scripted`` on-ramps (and ``extra_on_ramps``) on ``model``.

    ``measured``: the weave blocks are kept (their pairing names the
    weaving section) with ``weave_params`` emptied (the model's constants are
    fixed); ``merge_params`` are dropped. ``weave`` / ``lane_change``: the
    config as its test has it.
    """
    from flowstate_core.config import ScenarioConfig

    if model != "measured":
        return cfg
    raw = cfg.model_dump(mode="json")
    for r in raw["network"]["ramps"]:
        if r["kind"] != "on":
            continue
        if r.get("merge") in ("weave", "scripted") or r.get("name") in extra_on_ramps:
            r["merge"] = "measured"
            r["merge_params"] = {}
            if r.get("weave") is not None:
                r["weave"]["weave_params"] = {}
    raw["name"] = f"{raw['name']}_measured"
    return ScenarioConfig.model_validate(raw)


# --- the fixture configs ------------------------------------------------------


def fixture(name: str, seed: int) -> tuple[Any, tuple[str, ...]]:
    """``(config, extra on-ramps put on measured)`` of a named fixture run."""
    mmt = _load_test_module("test_microsim_merge_managed_meter")
    if name == "th52_corridor":
        return mmt._th52_corridor_config(seed), ()
    if name == "th52_capacity":
        return mmt._th52_config(seed), ()
    if name == "th52_corridor_demand":
        return mmt._th52_config(seed, **mmt.TH52_CORRIDOR_DEMAND), ()
    if name in ("th52_upstream", "th52_upstream_fleet"):
        fleet = mmt.corridor_fleet_block() if name.endswith("_fleet") else None
        # E1 is the corridor's on-ramp 40648744 (TH52_UPSTREAM_DEMAND), which
        # the measured scenario variants put on measured too
        return mmt._th52_upstream_config(seed, "lane_change", fleet), ("upstream entrance",)
    if name.startswith("ruth_"):
        ws = _load_test_module("test_microsim_weave_short_section")
        demand = "entrance_peak" if "entr" in name else "exit_peak"
        fleet = ws.CORRIDOR_FLEET if "fleet" in name else None
        params = {"vacate_ahead_m": 271.4} if name.endswith("_271") else None
        return ws.ruth_config(seed, **ws.RUTH_DEMAND[demand], fleet=fleet, weave_params=params), ()
    if name == "th61":
        th61 = _load_test_module("test_microsim_th61_lane_end")
        return th61.th61_config(seed, merge="weave"), ()
    if name == "mcknight":
        fg = _load_test_module("test_microsim_scripted_force_guard")
        return fg.mcknight_config(seed), ()
    if name in ("weave_golden", "weave_moderate", "merge_golden"):
        gold = _load_test_module("test_microsim_golden")
        if name == "merge_golden":
            return gold._merge_config("merge_scripted", "scripted", duration_s=300.0), ()
        cfg = gold._weave_config()
        if name == "weave_moderate":
            # test_moderate_crossing_demand_does_not_crawl on the same fixture
            from flowstate_core.config import ScenarioConfig

            raw = cfg.model_dump(mode="json")
            raw["name"] = "weave_moderate"
            raw["network"]["inflow"] = [[0.0, 0.3]]
            raw["network"]["ramps"][0]["inflow"] = [[0.0, 0.15]]
            cfg = ScenarioConfig.model_validate(raw)
        return cfg, ()
    raise SystemExit(f"unknown fixture {name!r}")


#: The 29-run grid of the weave record, then the measured model's own G0 rows.
GRID: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("ruth_entr", (3, 4, 5)),
    ("ruth_exit", (3, 4, 5)),
    ("ruth_entr_fleet", (3, 4, 5)),
    ("ruth_exit_fleet", (3, 4, 5)),
    ("ruth_exit_fleet_271", (3, 4, 5)),
    ("th52_corridor_demand", (3, 4, 5)),
    ("th52_capacity", (3, 4, 5)),
    ("th52_upstream", (3, 4, 5)),
    ("th52_upstream_fleet", (3, 4, 5)),
    ("weave_moderate", (3,)),
    ("weave_golden", (3,)),
    ("merge_golden", (3,)),
    ("mcknight", (3, 4, 5)),
    ("th52_corridor", (3, 4, 5)),
    ("th61", (3,)),
)


# --- reading a run --------------------------------------------------------------


def zone_rows(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """The merge-model zones of a run (measured, weave or scripted), one dict each."""
    out: list[dict[str, Any]] = []
    for z in meta.get("measured_merges") or []:
        out.append({"model": "measured", **z})
    for z in meta.get("weave_sections") or []:
        out.append({"model": "weave", **z})
    for z in meta.get("scripted_merges") or []:
        out.append(
            {
                "model": "scripted",
                "ramp": z["ramp"],
                "edges": [z["attach_edge"]],
                "n_entered": z["n_entered"],
                "n_changed_in": z["n_changed"],
                "n_changed_out": 0,
                "n_forced": z["n_forced"],
                "n_unfinished": z["n_unfinished"],
            }
        )
    return out


def run_summary(paths: Any, wall_s: float) -> dict[str, Any]:
    """Per-run figures of the grid: departures, crossings, give-ups, safety, lock."""
    import sumolib

    meta = json.loads(paths.meta.read_text())
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "lane", "v", "a"])
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("net/**/*.net.xml"))))
    zones = zone_rows(meta)
    t_end = float(meta["config"]["sim"]["duration_s"])
    lowest: list[float] = []
    locked = False
    for z in zones:
        ramp = next((r for r in meta["ramps"] if r["name"] == z["ramp"]), None)
        if ramp is None or ramp.get("attach_x_m") is None:
            continue
        lo = float(ramp["attach_x_m"])
        hi = lo + sum(net.getEdge(e).getLength() for e in z["edges"])
        part = df[(df.x >= lo) & (df.x < hi) & (df.t >= WARMUP_S) & (df.t < t_end)]
        win = part.groupby((part.t // 60.0).astype(int)).v.mean()
        if len(win):
            lowest.append(float(win.min()))
            locked |= bool((win < LOCK_SPEED_MS).any())
        if z.get("n_entered") and z.get("n_unfinished", 0) > 0.1 * z["n_entered"]:
            locked = True
    return {
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "departed": meta["n_vehicles_departed"],
        "planned": meta["n_vehicles_planned"],
        "on_ramps": [
            [r["name"], r["n_departed"], r["n_planned"]] for r in meta["ramps"] if r["kind"] == "on"
        ],
        "collisions": meta["n_collisions"],
        "hard_brake_vehicle_steps": int((df.a <= -EMERGENCY_DECEL_MS2 + 1e-6).sum()),
        "zones": [
            {
                k: z.get(k)
                for k in (
                    "model",
                    "ramp",
                    "kind",
                    "n_entered",
                    "n_changed_in",
                    "n_changed_out",
                    "n_crossings_in",
                    "n_crossings_out",
                    "n_forced",
                    "n_exec_forced",
                    "n_missed",
                    "n_missed_exit",
                    "n_reached_section_exiting",
                    "n_unfinished",
                    "n_pair_releases",
                    "n_opposing_deferred",
                    "n_collisions_attributable",
                )
                if k in z
            }
            for z in zones
        ],
        "lowest_zone_minute_ms": min(lowest) if lowest else None,
        "lock": locked,
        "wall_s": round(wall_s, 2),
    }


def run_one(name: str, seed: int, model: str, work: Path) -> tuple[Any, dict[str, Any]]:
    """Run one fixture under one model; returns its paths and summary."""
    from microsim import run_micro

    cfg, extra = fixture(name, seed)
    cfg = to_model(cfg, model, extra)
    t0 = time.perf_counter()
    paths = run_micro(cfg, seed, work / f"{name}_{model}")
    return paths, {"fixture": name, "model": model, **run_summary(paths, time.perf_counter() - t0)}


# --- the self-check -------------------------------------------------------------


def _extract(script: str, argv: list[str]) -> None:
    """Run a committed extractor's ``main`` in-process."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(script, SCRIPTS / f"{script}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[script] = mod
    spec.loader.exec_module(mod)
    mod.main(argv)


def arrival_shares(paths: Any, ramp_name: str, steps: Sequence[float]) -> dict[str, Any]:
    """Share of an on-ramp's entrants crossing within ``steps`` seconds of reaching the zone.

    From the trajectories: each entrant's first sample on the ramp's attach
    edge (``meta.json["ramps"]`` span) and its first sample there in a lane
    above the one it arrived in (the ramp joins lane 0); an entrant first
    seen above lane 0 crossed in its arrival step (Δt = 0, SUMO's own change,
    WP-82's reading).
    """
    meta = json.loads(paths.meta.read_text())
    ramp = next(r for r in meta["ramps"] if r["name"] == ramp_name)
    lo, hi = float(ramp["attach_x_m"]), float(ramp["attach_end_x_m"])
    veh = pd.read_parquet(paths.run_dir / "vehicles.parquet", columns=["veh_id", "origin"])
    entrants = set(veh.loc[veh.origin == ramp_name, "veh_id"].astype(str))
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "lane"])
    df = df[df.veh_id.isin(entrants) & (df.x >= lo) & (df.x < hi)].sort_values(["veh_id", "t"])
    dts: list[float] = []
    for _vid, g in df.groupby("veh_id", sort=False):
        t0, lane0 = float(g.t.iloc[0]), int(g.lane.iloc[0])
        if lane0 > 0:
            dts.append(0.0)
            continue
        up = g[g.lane > 0]
        if len(up):
            dts.append(float(up.t.iloc[0]) - t0)
    n = len(dts)
    arr = np.asarray(dts)
    return {
        "n_crossings": n,
        "share_arrival_step": float((arr <= 1e-9).mean()) if n else None,
        **{f"share_within_{s:g}s": (float((arr <= s + 1e-9).mean()) if n else None) for s in steps},
        "dt_s_p50": float(np.median(arr)) if n else None,
    }


def _fit_row(art: dict[str, Any], zone_kind: str, movement: str) -> dict[str, Any] | None:
    for f in art["fits"]:
        if f["zone_kind"] == zone_kind and f["movement"] == movement and f["speed_class"] == "all":
            return f
    return None


def selfcheck(
    work: Path,
    seeds: Sequence[int],
    n_boot: int,
    log: Callable[[str], None],
    kinds: Sequence[str] = (),
) -> dict[str, Any]:
    """Run the fixtures and report (a)–(d) of docs/MERGE_MODEL.md §3."""
    from microsim.merge_model import load_params

    central = load_params(PARAMS, "central")
    zones = {
        # zone kind → (fixture, reference model, its on-ramp's name, input movements)
        "merge": ("mcknight", "lane_change", "on-ramp 178547099", ("entering_merge",)),
        "weave": ("th52_corridor", "weave", "th52", ("entering_weave", "exiting_weave")),
    }
    if kinds:
        zones = {k: v for k, v in zones.items() if k in kinds}
    report: dict[str, Any] = {"seeds": list(seeds), "params_set": "central", "zones": {}}
    for kind, (fx, ref_model, ramp_name, movements) in zones.items():
        dirs: list[str] = []
        d_measured: list[dict[str, Any]] = []
        d_ref: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        for seed in seeds:
            paths, summary = run_one(fx, seed, "measured", work / kind)
            runs.append(summary)
            dirs.append(str(paths.run_dir))
            d_measured.append(arrival_shares(paths, ramp_name, (0.5, 1.0)))
            ref_paths, _ = run_one(fx, seed, ref_model, work / f"{kind}_ref")
            d_ref.append(arrival_shares(ref_paths, ramp_name, (0.5, 1.0)))
            shutil.rmtree(ref_paths.run_dir, ignore_errors=True)
            log(f"{kind} seed {seed}: ran measured and {ref_model}")
        cg_out = work / f"critical_gaps_{kind}.json"
        rx_out = work / f"relaxation_{kind}.json"
        _extract(
            "i24_critical_gaps",
            ["--sim-run-dir", *dirs, "--out", str(cg_out), "--n-boot", str(n_boot)],
        )
        _extract(
            "lane_change_relaxation",
            [
                "--source",
                "trajectories",
                "--sim-run-dir",
                *dirs,
                "--out",
                str(rx_out),
                "--n-boot",
                str(n_boot),
            ],
        )
        cg = json.loads(cg_out.read_text())
        rx = json.loads(rx_out.read_text())
        # (a) the fitted critical gaps against the inputs
        a_rows = []
        for mv in movements:
            movement = "entering" if mv.startswith("entering") else "exiting"
            fit = _fit_row(cg, kind, movement)
            for side in ("lead", "lag"):
                dist = (central.lead if side == "lead" else central.lag).get(mv)
                if dist is None:
                    continue
                row: dict[str, Any] = {
                    "movement": mv,
                    "side": side,
                    "input_median_s": dist.median_s,
                }
                if fit is None or not fit["joint"].get("fitted"):
                    row.update({"fitted": False, "pass": False})
                else:
                    j = fit["joint"][side]
                    lo, hi = j["ci95"]["median_s"]
                    row.update(
                        {
                            "fitted": True,
                            "n_drivers": fit["n_drivers"],
                            "n_inconsistent": fit["joint"]["n_inconsistent"],
                            "fitted_median_s": j["median_s"],
                            "fitted_ci95_s": [lo, hi],
                            "ratio": j["median_s"] / dist.median_s,
                            "pass": lo <= dist.median_s <= hi,
                        }
                    )
                a_rows.append(row)
        # (b), (c) from the relaxation extractor's summary of the entering movement
        bc: dict[str, Any] = {}
        for r in rx["summary_by_zone_kind"]:
            if r["zone_kind"] != kind or r["movement"] != "entering" or r["speed_class"] != "all":
                continue
            offs = list(r["offsets_s"])
            rp = r["measures"]["ratio_pop"]["p50"]
            i5 = offs.index(5.0) if 5.0 in offs else None
            bc[r["side"]] = {
                "rel_speed_ms_p50_at_0": r["rel_speed_ms"]["p50"][0],
                "n_at_0": r["rel_speed_ms"]["n"][0],
                "ratio_pop_p50_at_0": rp[0],
                "ratio_pop_p50_at_5": rp[i5] if i5 is not None else None,
                "ratio_eq_p50_at_0": r["measures"]["ratio_eq"]["p50"][0],
            }
        fol, lea = bc.get("follower"), bc.get("leader")
        b_pass = bool(
            fol and lea and fol["rel_speed_ms_p50_at_0"] > 0 and lea["rel_speed_ms_p50_at_0"] < 0
        )

        def _recovers(side: dict[str, Any] | None) -> bool:
            return bool(
                side
                and side["ratio_pop_p50_at_5"] is not None
                and side["ratio_pop_p50_at_5"] > side["ratio_pop_p50_at_0"]
            )

        c_pass = bool(
            fol
            and lea
            and fol["ratio_pop_p50_at_0"] <= 0.9
            and lea["ratio_pop_p50_at_0"] <= 0.8
            and _recovers(fol)
            and _recovers(lea)
        )

        def _pool(rows: list[dict[str, Any]], key: str) -> float | None:
            n = sum(r["n_crossings"] for r in rows)
            if not n:
                return None
            return sum((r[key] or 0.0) * r["n_crossings"] for r in rows) / n

        d = {
            "measured_within_0.5s": _pool(d_measured, "share_within_0.5s"),
            "measured_within_1s": _pool(d_measured, "share_within_1s"),
            "reference_model": ref_model,
            "reference_arrival_step": _pool(d_ref, "share_arrival_step"),
            "reference_within_1s": _pool(d_ref, "share_within_1s"),
            "per_seed_measured": d_measured,
            "per_seed_reference": d_ref,
        }
        d_pass = (
            d["measured_within_1s"] is not None
            and d["reference_arrival_step"] is not None
            and d["measured_within_1s"] >= d["reference_arrival_step"]
        )
        report["zones"][kind] = {
            "fixture": fx,
            "runs": runs,
            "a_critical_gaps": {"rows": a_rows, "pass": all(r["pass"] for r in a_rows)},
            "b_partner_speeds": {"sides": bc, "pass": b_pass},
            "c_gap_at_change": {"sides": bc, "pass": c_pass},
            "d_arrival_crossings": {**d, "pass": d_pass},
        }
    return report


# --- the T.H.52 section criteria --------------------------------------------------


def th52_criteria(paths: Any) -> dict[str, Any]:
    """The criteria of ``test_th52_corridor_section_carries_free_flow_demand`` read off a run."""
    import sumolib

    mmt = _load_test_module("test_microsim_merge_managed_meter")
    meta = json.loads(paths.meta.read_text())
    z = next(z for z in zone_rows(meta) if z["ramp"] == "th52")
    on = next(r for r in meta["ramps"] if r["name"] == "th52")
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
    x_end = sum(net.getEdge(e).getLength() for e in ("100", "101", "102"))
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "lane", "v", "a"])
    end = df[(df.x >= x_end - 60.0) & (df.x < x_end) & (df.t < 1200.0)]
    per_vehicle = end.groupby("veh_id").agg(t_first=("t", "min"), v=("v", "mean"))
    station = per_vehicle.groupby((per_vehicle.t_first // 300.0).astype(int)).v.mean()
    crossing = per_vehicle[per_vehicle.t_first >= mmt.TH52_FLOW_WARMUP_S]
    q_sim = len(crossing) * 3600.0 / (1200.0 - mmt.TH52_FLOW_WARMUP_S)
    q_obs = mmt._th52_observed_inflow_vph(mmt.TH52_FLOW_WARMUP_S, 1200.0)
    geh = math.sqrt(2.0 * (q_sim - q_obs) ** 2 / (q_sim + q_obs))
    main = (
        meta["n_vehicles_departed"] - on["n_departed"],
        meta["n_vehicles_planned"] - on["n_planned"],
    )
    given_up, reached = z.get("n_missed_exit", 0), z.get("n_reached_section_exiting", 0)
    out = {
        "seed": meta["seed"],
        "config_hash": meta["config_hash"],
        "mainline_departed": list(main),
        "entrance_departed": [on["n_departed"], on["n_planned"]],
        "exit_end_flow_vph": round(q_sim, 1),
        "observed_inflow_vph": round(q_obs, 1),
        "exit_end_flow_geh": round(geh, 2),
        "station_speed_by_5min": [round(float(v), 1) for v in station.values],
        "n_collisions": meta["n_collisions"],
        "exits_given_up": [given_up, reached],
        "hard_brake_vehicle_steps": int((df.a <= -EMERGENCY_DECEL_MS2 + 1e-6).sum()),
    }
    out["criteria"] = {
        "i_departed": main[0] >= 0.95 * main[1] and on["n_departed"] >= 0.95 * on["n_planned"],
        "ii_a_flow_geh": geh < mmt.TH52_FLOW_GEH,
        "ii_b_station_speed": len(station) == 4 and bool((station > mmt.TH52_FREE_FLOW_MS).all()),
        "iii_collisions": meta["n_collisions"] == 0,
        "iv_give_ups": reached > 0 and given_up <= 0.02 * reached,
    }
    out["pass"] = all(out["criteria"].values())
    return out


def station_flows(
    run_dirs: Sequence[Path], observations: Path, station: str, clock_offset_s: float
) -> dict[str, Any]:
    """One station's simulated 5-min flows per run against the observed ones (gate B).

    Each vehicle is counted once, in the window of its first trajectory
    sample at or past the station's ``x_m`` (``x_offset`` 0: an OSM chain's
    observations are positioned on the simulated chain, as the battery reads
    them). Window ``k`` of a run covers ``[k·w, (k+1)·w)`` of simulated time,
    matched to the observed window ``(clock_offset_s + k·w) / w``.
    """
    obs = json.loads(observations.read_text())
    st = next(s for s in obs["stations"] if s["id"] == station)
    w = float(obs["window_s"])
    flows = obs["flows_veh_h"][station]
    rows = []
    for d in run_dirs:
        meta = json.loads((d / "meta.json").read_text())
        df = pd.read_parquet(d / "trajectories.parquet", columns=["t", "veh_id", "x"])
        first = df[df.x >= float(st["x_m"])].groupby("veh_id").t.min()
        n_win = int(float(meta["config"]["sim"]["duration_s"]) // w)
        sim = np.bincount((first // w).astype(int).clip(0, n_win), minlength=n_win + 1)[:n_win]
        k_obs = [round((clock_offset_s + k * w) / w) for k in range(n_win)]
        rows.append(
            {
                "run_dir": str(d),
                "seed": meta["seed"],
                "config_hash": meta["config_hash"],
                "departed_fraction": meta["n_vehicles_departed"]
                / max(meta["n_vehicles_planned"], 1),
                "n_collisions": meta["n_collisions"],
                "warmup_s": meta["config"]["sim"].get("warmup_s", 0.0),
                "sim_veh_h": [float(c) * 3600.0 / w for c in sim],
                "obs_veh_h": [
                    (float(flows[k]) if 0 <= k < len(flows) and flows[k] is not None else None)
                    for k in k_obs
                ],
            }
        )
    return {"station": station, "x_m": st["x_m"], "window_s": w, "runs": rows}


def colliding_pairs(
    scenario: Path, artifact: Path, out_root: Path, population_dir: Path
) -> dict[str, Any]:
    """Gate C: the (sample, seed) pairs an uncertainty artifact recorded collisions for, rerun once each."""
    from flowstate_core.config import ScenarioConfig
    from microsim import run_micro
    from validation.uncertainty import Sample, apply

    art = json.loads(artifact.read_text())
    samples = {s["sample_id"]: Sample.from_dict(s) for s in art["samples"]}
    base = ScenarioConfig.from_yaml(scenario)
    rows = []
    for block in art["collisions"].values():
        for hit in block.get("runs_with_collisions") or []:
            cfg = apply(samples[hit["sample_id"]], base, population_dir=population_dir)
            t0 = time.perf_counter()
            paths = run_micro(cfg, int(hit["seed"]), out_root)
            meta = json.loads(paths.meta.read_text())
            rows.append(
                {
                    "sample_id": hit["sample_id"],
                    "seed": hit["seed"],
                    "collisions_recorded_before": hit["n"],
                    "config_hash": meta["config_hash"],
                    "n_collisions": meta["n_collisions"],
                    "collisions": meta["collisions"],
                    "departed_fraction": meta["n_vehicles_departed"]
                    / max(meta["n_vehicles_planned"], 1),
                    "zones": [
                        {
                            k: z.get(k)
                            for k in (
                                "ramp",
                                "kind",
                                "n_changed_in",
                                "n_changed_out",
                                "n_missed_exit",
                                "n_unfinished",
                                "n_collisions_attributable",
                            )
                        }
                        for z in zone_rows(meta)
                    ],
                    "wall_s": round(time.perf_counter() - t0, 1),
                    "run_dir": str(paths.run_dir),
                }
            )
            print(json.dumps(rows[-1]), flush=True)
    return {"scenario": str(scenario), "artifact": str(artifact), "rows": rows}


def _seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sf = sub.add_parser("station-flows")
    sf.add_argument("--run-dirs", nargs="+", type=Path, required=True)
    sf.add_argument("--observations", type=Path, required=True)
    sf.add_argument("--station", default="S790")
    sf.add_argument("--clock-offset-s", type=float, default=0.0)
    sf.add_argument("--out", type=Path, default=None)
    cp = sub.add_parser("colliding-pairs")
    cp.add_argument("--scenario", type=Path, required=True)
    cp.add_argument("--uncertainty-artifact", type=Path, required=True)
    cp.add_argument("--out-root", type=Path, required=True)
    cp.add_argument("--population-dir", type=Path, required=True)
    cp.add_argument("--out", type=Path, default=None)
    for name in ("check", "grid", "th52"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, default=None, help="JSON report (default: print only)")
        p.add_argument("--work-dir", type=Path, default=None, help="run trees (default: temp)")
        p.add_argument("--keep", action="store_true", help="keep the run trees")
        if name != "check":
            p.add_argument("--model", default="measured", choices=("measured", "weave"))
        if name == "check":
            p.add_argument("--seeds", default="3-7")
            p.add_argument("--n-boot", type=int, default=200)
            p.add_argument("--kinds", default="", help="merge,weave (default both)")
        if name == "th52":
            p.add_argument("--seeds", default="3-22")
            p.add_argument("--speed-factor", type=float, default=None)
        if name == "grid":
            p.add_argument("--only", default="", help="comma-separated fixture names")
    args = ap.parse_args(argv)
    if args.cmd in ("station-flows", "colliding-pairs"):
        if args.cmd == "station-flows":
            res = station_flows(args.run_dirs, args.observations, args.station, args.clock_offset_s)
            for r in res["runs"]:
                print(
                    f"seed {r['seed']}: departed {r['departed_fraction']:.3f} collisions "
                    f"{r['n_collisions']} {args.station} sim/obs veh/h by window: "
                    + " ".join(
                        f"{a:.0f}/{b:.0f}" if b is not None else f"{a:.0f}/-"
                        for a, b in zip(r["sim_veh_h"], r["obs_veh_h"], strict=True)
                    )
                )
        else:
            res = colliding_pairs(
                args.scenario, args.uncertainty_artifact, args.out_root, args.population_dir
            )
        if args.out is not None:
            args.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
            print(f"wrote {args.out}")
        return
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="merge_model_selfcheck_"))
    work.mkdir(parents=True, exist_ok=True)
    cwd = Path.cwd()
    try:
        os.chdir(REPO)  # the golden configs name their fixtures repo-relative
        if args.cmd == "check":
            kinds = [k for k in args.kinds.split(",") if k]
            result: Any = selfcheck(work, _seeds(args.seeds), args.n_boot, print, kinds)
            for kind, z in result["zones"].items():
                print(
                    f"{kind}: (a) {z['a_critical_gaps']['pass']} (b) {z['b_partner_speeds']['pass']} "
                    f"(c) {z['c_gap_at_change']['pass']} (d) {z['d_arrival_crossings']['pass']}"
                )
        elif args.cmd == "grid":
            only = {s for s in args.only.split(",") if s}
            rows = []
            for name, seeds in GRID:
                if only and name not in only:
                    continue
                for seed in seeds:
                    paths, row = run_one(name, seed, args.model, work)
                    rows.append(row)
                    if not args.keep:
                        shutil.rmtree(paths.run_dir, ignore_errors=True)
                    print(json.dumps(row), flush=True)
            result = {"model": args.model, "rows": rows}
        else:
            from flowstate_core.config import ScenarioConfig
            from microsim import run_micro

            mmt = _load_test_module("test_microsim_merge_managed_meter")
            rows = []
            for seed in _seeds(args.seeds):
                cfg = mmt._th52_corridor_config(seed)
                if args.speed_factor is not None:
                    raw = cfg.model_dump(mode="json")
                    raw["fleet"]["speed_factor"] = args.speed_factor
                    cfg = ScenarioConfig.model_validate(raw)
                cfg = to_model(cfg, args.model)
                t0 = time.perf_counter()
                paths = run_micro(cfg, seed, work / f"th52_{args.model}")
                row = {**th52_criteria(paths), "wall_s": round(time.perf_counter() - t0, 2)}
                rows.append(row)
                if not args.keep:
                    shutil.rmtree(paths.run_dir, ignore_errors=True)
                print(json.dumps(row), flush=True)
            result = {"model": args.model, "speed_factor": args.speed_factor, "rows": rows}
        if args.out is not None:
            args.out.write_text(json.dumps(result, indent=1, default=float) + "\n")
            print(f"wrote {args.out}")
    finally:
        os.chdir(cwd)
        if args.work_dir is None and not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
