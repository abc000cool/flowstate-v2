"""The measured merge model in the runner (``RampSpec.merge = "measured"``; docs/MERGE_MODEL.md).

Integration tests on the repository's fixtures (docs/MERGE_MODEL.md §4, G0):
the schema and its hash neutrality; the acceleration-lane zone on the ramp
fixture and the McKnight Rd cut; the weaving-section zone on the golden weave
fixture, the Ruth St twin, the T.H.52 capacity fixture and the two-auxiliary
lane T.H.61 stretch — zero collisions and no lock on every one; the run's
records (``meta.json["measured_merges"]`` / ``["measured_merge_model"]``,
the drivers' critical gaps in ``vehicles.parquet``); determinism; AVs; and
the T.H.52 corridor section test's criteria, which the model does not yet
meet (strict ``xfail`` with its numbers, beside the weave's own).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest
import sumolib

from flowstate_core.config import RampSpec, ScenarioConfig, config_hash
from microsim import merge_model, run_micro
from microsim.runner import DRIVER_GAP_COLUMNS

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def _module(name: str) -> ModuleType:
    """A sibling test module (its fixture configs are the single source)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def measured(cfg: ScenarioConfig, extra_on_ramps: tuple[str, ...] = ()) -> ScenarioConfig:
    """``cfg`` with its ``weave`` / ``scripted`` on-ramps (and ``extra_on_ramps``) on ``measured``."""
    raw = cfg.model_dump(mode="json")
    for r in raw["network"]["ramps"]:
        if r["kind"] == "on" and (
            r.get("merge") in ("weave", "scripted") or r.get("name") in extra_on_ramps
        ):
            r["merge"] = "measured"
            r["merge_params"] = {}
            if r.get("weave") is not None:
                r["weave"]["weave_params"] = {}
    raw["name"] = f"{raw['name']}_measured"
    return ScenarioConfig.model_validate(raw)


def _merge_cfg(duration_s: float = 300.0, **net: object) -> ScenarioConfig:
    """The golden merge interchange (``tests/fixtures/merge.osm``) on ``measured``."""
    return ScenarioConfig.model_validate(
        {
            "name": "merge_measured_test",
            "network": {
                "kind": "osm",
                "osm_file": str(REPO / "tests" / "fixtures" / "merge.osm"),
                "corridor_edges": ["100", "101", "102", "103"],
                "inflow": [[0.0, 0.6]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "test on-ramp",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, 0.25]],
                        "merge": "measured",
                    }
                ],
                **net,
            },
            "sim": {"duration_s": duration_s},
            "seed": 3,
        }
    )


def _hard_brake_steps(paths) -> int:
    """Vehicle-steps at SUMO's emergency deceleration (9 m/s², passenger default)."""
    a = pd.read_parquet(paths.trajectories, columns=["a"]).a
    return int((a <= -9.0 + 1e-6).sum())


def _zone(meta: dict, ramp: str) -> dict:
    return next(z for z in meta["measured_merges"] if z["ramp"] == ramp)


def _accounting(z: dict) -> None:
    """The movement identity and the crossing bookkeeping of one zone."""
    assert z["n_entered"] == (
        z["n_changed_in"] + z["n_changed_out"] + z["n_missed"] + z["n_unfinished"]
    ), z
    assert z["n_missed_exit"] <= z["n_missed"], z
    assert z["n_exec_accepted"] + z["n_exec_forced"] == z["n_crossings_in"] + z["n_crossings_out"]


class TestSchema:
    def test_measured_with_and_without_a_weave_block(self):
        on = {
            "kind": "on",
            "edges": ["200"],
            "attach_edge": "102",
            "inflow": [[0.0, 0.1]],
            "merge": "measured",
        }
        assert RampSpec.model_validate(on).weave is None
        spec = RampSpec.model_validate({**on, "weave": {"exit_ramp": "x"}})
        assert spec.weave is not None and spec.weave.weave_params == {}
        with pytest.raises(ValueError, match="constants are fixed"):
            RampSpec.model_validate(
                {**on, "weave": {"exit_ramp": "x", "weave_params": {"courtesy": 1.0}}}
            )
        with pytest.raises(ValueError, match="merge='scripted' only"):
            RampSpec.model_validate({**on, "merge_params": {"accept_gap_s": 0.3}})
        # the weave block still belongs to weave (and measured) only
        with pytest.raises(ValueError, match="merge='weave' only"):
            RampSpec.model_validate({**on, "merge": "scripted", "weave": {"exit_ramp": "x"}})

    def test_parameter_set_and_hash_neutrality(self):
        cfg = _merge_cfg()
        assert cfg.network.merge_model_set == "central"
        raw = cfg.model_dump(mode="json")
        raw["network"]["merge_model_set"] = "central"  # explicit default hashes as omitted
        assert config_hash(ScenarioConfig.model_validate(raw)) == config_hash(cfg)
        raw["network"]["merge_model_set"] = "us101_gaps"
        assert config_hash(ScenarioConfig.model_validate(raw)) != config_hash(cfg)
        raw["network"]["merge_model_set"] = "bogus"
        with pytest.raises(ValueError):
            ScenarioConfig.model_validate(raw)
        # a set other than central needs a measured ramp
        raw = cfg.model_dump(mode="json")
        raw["network"]["ramps"][0]["merge"] = "lane_change"
        raw["network"]["merge_model_set"] = "delta_zero"
        with pytest.raises(ValueError, match="applies to merge='measured' only"):
            ScenarioConfig.model_validate(raw)
        # the new field leaves every other scenario's hash alone
        raw["network"].pop("merge_model_set")
        plain = ScenarioConfig.model_validate(raw)
        assert "merge_model_set" not in json.dumps(
            plain.model_dump(mode="json", exclude_defaults=True)
        )

    def test_the_parameter_sets_are_the_artifacts(self):
        assert set(merge_model.PARAMETER_SETS) == {
            "central",
            "us101_gaps",
            "delta_zero",
            "tau_r_low",
            "tau_r_high",
        }
        art = json.loads(merge_model.params_artifact_path().read_text())
        assert set(art["sets"]) == set(merge_model.PARAMETER_SETS)


class TestAccelerationLane:
    def test_the_ramp_fixture(self, tmp_path):
        paths = run_micro(_merge_cfg(), 3, tmp_path / "accel")
        meta = json.loads(paths.meta.read_text())
        assert meta["merge_models"] == [
            {"ramp": "test on-ramp", "attach_edge": "102", "merge": "measured"}
        ]
        assert meta["weave_sections"] == [] and meta["scripted_merges"] == []
        z = _zone(meta, "test on-ramp")
        assert z["kind"] == "acceleration_lane" and z["edges"] == ["102"] and z["exit"] is None
        _accounting(z)
        assert z["n_entered"] > 20 and z["n_changed_in"] >= 0.8 * z["n_entered"], z
        assert z["n_changed_out"] == 0 and z["n_missed"] == 0
        assert z["n_handovers"] > 0 and z["vacate_window_edges"] == []
        assert meta["n_collisions"] == 0 and z["n_collisions_attributable"] == 0
        assert meta["n_vehicles_departed"] == meta["n_vehicles_planned"]
        assert _hard_brake_steps(paths) == 0
        run = meta["measured_merge_model"]
        assert run["params_artifact"] == merge_model.PARAMS_ARTIFACT
        assert run["params_sha256"] == merge_model.file_sha256(merge_model.params_artifact_path())
        assert run["parameter_set"] == "central"
        assert run["lane_end_giveup_m"] == merge_model.LANE_END_GIVEUP_M
        # relaxation: granted at the crossings, never below max(step, 0.5 T_i)
        rel = run["relaxation"]
        granted = rel["n_granted_entrant"] + rel["n_granted_follower"]
        assert (
            granted > 0 and granted == z["n_relax_granted_entrant"] + z["n_relax_granted_follower"]
        )
        assert granted - rel["n_regranted"] == (
            rel["n_restored_expired"]
            + rel["n_restored_lane_change"]
            + rel["n_restored_left"]
            + rel["n_active_at_end"]
        ), rel
        assert rel["min_tau_set_s"] >= 0.5 - 1e-9
        # every departed vehicle carries its drawn critical gaps
        veh = pd.read_parquet(paths.run_dir / "vehicles.parquet")
        assert set(DRIVER_GAP_COLUMNS) <= set(veh.columns)
        lo, hi = (
            merge_model.load_params(merge_model.params_artifact_path())
            .lag["entering_merge"]
            .bounds()
        )
        assert veh["tc_lag_merge_s"].between(lo - 1e-12, hi + 1e-12).all()

    @pytest.mark.parametrize("seed", [3, 4, 5])
    def test_mcknight_has_no_collision(self, tmp_path, seed):
        """WP-93's collision case (``tests/fixtures/mcknight_merge.osm``, the corridor's
        demand and fleet): the scripted merge collided here 13 times before its
        guard; the measured model's forced changes are always guarded."""
        fg = _module("test_microsim_scripted_force_guard")
        paths = run_micro(measured(fg.mcknight_config(seed)), seed, tmp_path / "mck")
        meta = json.loads(paths.meta.read_text())
        z = _zone(meta, "on-ramp 178547099")
        _accounting(z)
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert z["kind"] == "acceleration_lane" and z["n_unfinished"] <= 2, z
        (ramp,) = meta["ramps"]
        assert ramp["n_departed"] == ramp["n_planned"]
        assert _hard_brake_steps(paths) == 0

    def test_runs_are_deterministic(self, tmp_path):
        cfg = _merge_cfg(duration_s=150.0)
        a = run_micro(cfg, 7, tmp_path / "a")
        b = run_micro(cfg, 7, tmp_path / "b")
        assert a.trajectories.read_bytes() == b.trajectories.read_bytes()
        ma, mb = json.loads(a.meta.read_text()), json.loads(b.meta.read_text())
        assert ma["measured_merges"] == mb["measured_merges"]
        assert ma["measured_merge_model"] == mb["measured_merge_model"]

    def test_avs_are_driven_across_and_never_relaxed(self, tmp_path):
        raw = _merge_cfg(duration_s=240.0).model_dump(mode="json")
        raw["av"] = {"penetration": 0.3, "compliance": 1.0, "controller": "follower_stopper"}
        paths = run_micro(ScenarioConfig.model_validate(raw), 5, tmp_path / "av")
        meta = json.loads(paths.meta.read_text())
        assert meta["n_collisions"] == 0, meta["collisions"]
        z = _zone(meta, "test on-ramp")
        _accounting(z)
        assert meta["measured_merge_model"]["n_av_commands_withdrawn"] >= 0
        assert meta["av_ids"]


class TestWeavingSection:
    def test_both_movements_on_the_golden_fixture(self, tmp_path):
        gold = _module("test_microsim_golden")
        cfg = measured(gold._weave_config())
        raw = cfg.model_dump(mode="json")
        raw["network"]["osm_file"] = str(REPO / gold.WEAVE_OSM)
        paths = run_micro(ScenarioConfig.model_validate(raw), 3, tmp_path / "weave")
        meta = json.loads(paths.meta.read_text())
        assert meta["weave_sections"] == []
        z = _zone(meta, "weave on-ramp")
        assert z["kind"] == "weave" and z["exit"] == "weave exit" and z["edges"] == ["102"]
        _accounting(z)
        assert z["n_changed_in"] > 5 and z["n_changed_out"] > 5, z
        assert z["n_unfinished"] == 0 and z["n_missed"] == 0, z
        assert z["n_exited"] == z["n_reached_section_exiting"] == z["n_departed_exiting"], z
        assert meta["n_collisions"] == 0 and _hard_brake_steps(paths) == 0
        assert meta["n_vehicles_departed"] == meta["n_vehicles_planned"]

    @pytest.mark.parametrize(
        ("demand", "fleet", "seed"),
        [
            ("entrance_peak", False, 3),
            ("exit_peak", False, 3),
            ("exit_peak", True, 3),
            ("exit_peak", True, 4),
            ("exit_peak", True, 5),
        ],
    )
    def test_ruth_st_has_no_collision_and_no_lock(self, tmp_path, demand, fleet, seed):
        """The 136 m Ruth St twin (``tests/fixtures/weave_ruth.osm``), the short section."""
        ws = _module("test_microsim_weave_short_section")
        cfg = ws.ruth_config(
            seed, **ws.RUTH_DEMAND[demand], fleet=ws.CORRIDOR_FLEET if fleet else None
        )
        paths = run_micro(measured(cfg), seed, tmp_path / "ruth")
        meta = json.loads(paths.meta.read_text())
        z = _zone(meta, "ruth")
        _accounting(z)
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert z["n_unfinished"] <= 0.1 * z["n_entered"], z
        assert meta["n_vehicles_departed"] == meta["n_vehicles_planned"]
        assert z["n_exited"] >= 0.9 * z["n_reached_section_exiting"], z

    @pytest.mark.parametrize("seed", [4, 5])
    def test_th52_capacity_does_not_lock(self, tmp_path, seed):
        """``tests/fixtures/weave_th52.osm`` at capacity (B§5.13: the weave's no-lock pin
        rewritten without weave-specific counters): no collision, section lane 1's
        first 60 m above 2 m/s in every minute after the warm-up, at most 10 % of the
        driven vehicles unfinished. Measured 2026-10-05 (macOS): seeds 4 / 5 depart
        344 / 341 of 466 entrants (the weave pin's 80 % is not met: 74 / 73 %), and
        12 / 6 vehicle-steps brake at −9 m/s² — exiters admitted by the lead brake
        guard alone 0.1 m (net) behind an auxiliary-lane vehicle at equal speed,
        whose own model then restores its s0 (docs/MERGE_MODEL.md §2: no lead time
        gate for the exiting movement)."""
        mmt = _module("test_microsim_merge_managed_meter")
        paths = run_micro(measured(mmt._th52_config(seed)), seed, tmp_path / f"cap{seed}")
        meta = json.loads(paths.meta.read_text())
        z = _zone(meta, "th52")
        _accounting(z)
        net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
        x0 = sum(net.getEdge(e).getLength() for e in ("100", "101"))
        df = pd.read_parquet(paths.trajectories, columns=["t", "x", "lane", "v"])
        start = df[
            (df.x >= x0) & (df.x < x0 + 60.0) & (df.t >= 120.0) & (df.t < 1200.0) & (df.lane == 1)
        ]
        windows = start.groupby((start.t // 60.0).astype(int)).v.mean()
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert len(windows) == 18 and (windows > 2.0).all(), windows.round(1).to_dict()
        assert z["n_unfinished"] <= 0.1 * z["n_entered"], z

    def test_th61_two_auxiliary_lanes(self, tmp_path):
        """The T.H.61 stretch (two lanes lead only to the exit; WP-66): mandatory
        changers come from the lane connections against the route (B§5.2), so the
        geometry the one-lane weave misfits is one rule here. No collision and no
        standstill minute at the gore; the departures are recorded, not pinned
        (2,298 of 2,885 on macOS, seed 3, against lane_change's 2,810: the stretch
        stays the hardest fixture of the grid)."""
        th61 = _module("test_microsim_th61_lane_end")
        cfg = th61.th61_config(3, merge="weave")
        paths = run_micro(measured(cfg), 3, tmp_path / "th61")
        state = th61.th61_lane_end_state(paths, cfg.sim.duration_s)
        meta = json.loads(paths.meta.read_text())
        _accounting(_zone(meta, "th61"))
        assert state["n_collisions"] == 0, state
        assert state["zero_minutes"] == [], state


def _th52_state(paths) -> dict:
    """``test_th52_corridor_section_carries_free_flow_demand``'s criteria on a measured run."""
    mmt = _module("test_microsim_merge_managed_meter")
    meta = json.loads(paths.meta.read_text())
    z = _zone(meta, "th52")
    on = next(r for r in meta["ramps"] if r["name"] == "th52")
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
    x_end = sum(net.getEdge(e).getLength() for e in ("100", "101", "102"))
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v"])
    end = df[(df.x >= x_end - 60.0) & (df.x < x_end) & (df.t < 1200.0)]
    per_vehicle = end.groupby("veh_id").agg(t_first=("t", "min"), v=("v", "mean"))
    station = per_vehicle.groupby((per_vehicle.t_first // 300.0).astype(int)).v.mean()
    crossing = per_vehicle[per_vehicle.t_first >= mmt.TH52_FLOW_WARMUP_S]
    q_sim = len(crossing) * 3600.0 / (1200.0 - mmt.TH52_FLOW_WARMUP_S)
    q_obs = mmt._th52_observed_inflow_vph(mmt.TH52_FLOW_WARMUP_S, 1200.0)
    return {
        "mainline_departed": (
            meta["n_vehicles_departed"] - on["n_departed"],
            meta["n_vehicles_planned"] - on["n_planned"],
        ),
        "entrance_departed": (on["n_departed"], on["n_planned"]),
        "exit_end_flow_vph": round(q_sim, 1),
        "exit_end_flow_geh": round(math.sqrt(2.0 * (q_sim - q_obs) ** 2 / (q_sim + q_obs)), 2),
        "station": station,
        "n_collisions": meta["n_collisions"],
        "exits_given_up": (z["n_missed_exit"], z["n_reached_section_exiting"]),
    }


@pytest.mark.xfail(
    strict=True,
    reason="The T.H.52 section (tests/fixtures/weave_th52_corridor.osm, the observed "
    "05:30-05:50 movements, the corridor's fleet; the criteria of "
    "test_th52_corridor_section_carries_free_flow_demand, docs/FRISCO_PROTOCOL.md §9) on "
    "merge: measured (docs/MERGE_MODEL.md, 2026-10-05, macOS): the T.H.52 entrance departs "
    "348 / 346 / 324 of 407 at seeds 3 / 4 / 5 (387 required); the section's exit end carries "
    "4,017 / 3,863 / 3,860 veh/h after the fill against 4,877 observed (GEH 12.9 / 15.3 / 15.4, "
    "under 5 required) at a lowest 5-min station speed of 15.3 / 17.5 / 15.2 m/s (above 20 "
    "required); the mainline departs 1,189 / 1,129 / 1,156 of 1,196 (1,137 required); 0 / 0 / 1 "
    "exits given up of 340 / 370 / 395 reaching the section; no collision, no -9 m/s2 step. "
    "The weave reads 4,100 / 3,813 / 3,697 veh/h (GEH 11.6 / 16.1 / 18.0) on the same seeds",
)
def test_th52_corridor_section_carries_free_flow_demand_measured(tmp_path):
    """The weave's acceptance test (3) on the measured model, seed 3 (the locked
    acceptance, docs/MERGE_MODEL.md §4 G1): the same fixture, demand, fleet
    (factor 1, as locked: §2) and criteria, the T.H.52 entrance on ``measured``
    with its weave block. The weave's own test stays as it is beside it."""
    mmt = _module("test_microsim_merge_managed_meter")
    paths = run_micro(measured(mmt._th52_corridor_config(3)), 3, tmp_path / "th52_measured")
    state = _th52_state(paths)
    main_departed, main_planned = state["mainline_departed"]
    on_departed, on_planned = state["entrance_departed"]
    given_up, reached = state["exits_given_up"]
    windows = state["station"]
    assert state["n_collisions"] == 0, state
    assert main_departed >= 0.95 * main_planned, state
    assert on_departed >= 0.95 * on_planned, state
    assert state["exit_end_flow_geh"] < mmt.TH52_FLOW_GEH, state
    assert len(windows) == 4 and (windows > mmt.TH52_FREE_FLOW_MS).all(), state
    assert reached > 0 and given_up <= 0.02 * reached, state
