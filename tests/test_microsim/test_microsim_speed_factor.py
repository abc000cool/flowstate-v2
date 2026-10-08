"""The passenger speed factor on the posted limit (``FleetSpec.speed_factor``, WP-109).

The driver-settings check found Minnesota drivers at a median 105 km/h in
light traffic on a 55 mph road, while every model driver was capped at the
limit (``speedFactor="1.0"`` on every vType). SUMO's desired free-flow speed
is ``min(maxSpeed, speedFactor × lane limit)`` (SUMO documentation,
*Definition of Vehicles, Vehicle Types, and Routes*, "Speed Distributions").

Checked here: the default is hash-neutral and writes the vTypes exactly as
before; a set factor reaches the passenger vTypes (heavy vehicles keep 1.0)
without moving any other draw; a spread is drawn from an independent seeded
stream, inside SUMO's cut-offs and at SUMO's four decimals; the demand
ledger's free-flow time uses the factor; and, in a tiny SUMO run, a vehicle
drives faster than its edge's posted limit while the downstream boundary
schedule's measured speed is still the speed driven.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import AVSpec, FleetSpec, HeavyVehicleSpec, ScenarioConfig, config_hash
from flowstate_core.constants import SPEED_FACTOR_BOUNDS
from flowstate_core.rng import make_rng
from microsim import build_corridor_plan, run_micro, write_corridor_routes
from microsim.runner import RouteGeometry, _journey_table, _speed_factor_merge_note
from microsim.vehicles import draw_speed_factors

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = 20261004
HEAVY = HeavyVehicleSpec(
    fraction=0.3,
    length_m=18.0,
    emission_class="HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
    v0=27.0,
    T=2.0,
    a_max=0.6,
    b=1.5,
    s0=3.0,
)


def _plan(fleet: FleetSpec, seed: int = SEED, rate: float = 1.0) -> Any:
    return build_corridor_plan([(0.0, rate)], 600.0, fleet, AVSpec(penetration=0.1), make_rng(seed))


def _vtypes(path: Path) -> list[dict[str, str]]:
    return [dict(v.attrib) for v in ET.parse(path).getroot().findall("vType")]


class TestDefault:
    def test_the_default_is_hash_neutral(self) -> None:
        fleet = FleetSpec()
        assert fleet.speed_factor == 1.0 and fleet.speed_dev == 0.0
        assert fleet.model_dump(exclude_defaults=True) == {}
        net = {"kind": "corridor", "length_m": 1000.0, "lanes": 1, "inflow": [[0.0, 0.2]]}
        omitted = ScenarioConfig.model_validate(
            {"name": "x", "network": net, "sim": {"duration_s": 60.0}}
        )
        explicit = ScenarioConfig.model_validate(
            {
                "name": "x",
                "network": net,
                "fleet": {"speed_factor": 1.0, "speed_dev": 0.0},
                "sim": {"duration_s": 60.0},
            }
        )
        assert config_hash(explicit) == config_hash(omitted)
        changed = omitted.model_copy(
            update={"fleet": omitted.fleet.model_copy(update={"speed_factor": 1.19})}
        )
        assert config_hash(changed) != config_hash(omitted)
        # the pinned policy-v4 hash of the versioned ring (test_config_hash.py;
        # d5472987265c under policy v3, which config_hash_v3 reproduces)
        ring = ScenarioConfig.from_yaml(REPO_ROOT / "scenarios" / "ring_sugiyama.yaml")
        assert config_hash(ring) == "258c09ac0074"

    def test_the_default_plan_and_vtypes_are_as_before(self, tmp_path: Path) -> None:
        plan = _plan(FleetSpec(heavy=HEAVY))
        assert plan.speed_factor == ()
        assert {plan.speed_factor_of(i) for i in range(plan.n)} == {1.0}
        a = write_corridor_routes(("entry", "c0"), plan, "IDM", 0.5, tmp_path / "a.rou.xml")
        explicit = _plan(FleetSpec(heavy=HEAVY, speed_factor=1.0, speed_dev=0.0))
        b = write_corridor_routes(("entry", "c0"), explicit, "IDM", 0.5, tmp_path / "b.rou.xml")
        assert a.read_bytes() == b.read_bytes()
        assert all(v["speedFactor"] == "1.0" and v["speedDev"] == "0" for v in _vtypes(a))
        assert ' speedFactor="1.0" speedDev="0" ' in a.read_text()


class TestPlan:
    def test_a_factor_reaches_the_passenger_vtypes_and_nothing_else_moves(
        self, tmp_path: Path
    ) -> None:
        base = _plan(FleetSpec(heavy=HEAVY))
        plan = _plan(FleetSpec(heavy=HEAVY, speed_factor=1.19))
        # every other draw is the default plan's (the factor uses no RNG)
        for name in ("params", "is_av", "complied", "depart_s", "is_heavy", "depart_lane"):
            assert getattr(plan, name) == getattr(base, name), name
        heavy = [plan.heavy(i) for i in range(plan.n)]
        assert any(heavy) and not all(heavy)
        assert [plan.speed_factor_of(i) for i in range(plan.n)] == [
            1.0 if h else 1.19 for h in heavy
        ]
        path = write_corridor_routes(("entry", "c0"), plan, "IDM", 0.5, tmp_path / "r.rou.xml")
        vtypes = _vtypes(path)
        assert [v["speedFactor"] for v in vtypes] == ["1.0" if h else "1.1900" for h in heavy]
        assert {v["speedDev"] for v in vtypes} == {"0"}  # our RNG, not SUMO's (§0.5)

    def test_a_spread_is_drawn_from_its_own_stream(self) -> None:
        fleet = FleetSpec(speed_factor=1.2, speed_dev=0.1)
        base = _plan(FleetSpec(), rate=4.0)
        plan = _plan(fleet, rate=4.0)
        assert plan.params == base.params and plan.depart_s == base.depart_s
        sf = np.asarray(plan.speed_factor)
        assert plan.n > 2000
        assert float(sf.mean()) == pytest.approx(1.2, abs=0.01)
        assert float(sf.std()) == pytest.approx(0.1, rel=0.1)
        lo, hi = SPEED_FACTOR_BOUNDS
        assert float(sf.min()) >= lo and float(sf.max()) <= hi
        # SUMO keeps four decimals of a factor: the plan holds what SUMO runs
        assert all(round(v, 4) == v for v in plan.speed_factor)
        assert _plan(fleet, rate=4.0).speed_factor == plan.speed_factor
        assert _plan(fleet, seed=SEED + 1, rate=4.0).speed_factor != plan.speed_factor

    def test_the_draw_does_not_depend_on_the_heavy_flags(self) -> None:
        fleet = FleetSpec(speed_factor=1.2, speed_dev=0.1)
        rng = make_rng(SEED)
        none = draw_speed_factors(fleet, [], 50, rng)
        flags = [i % 3 == 0 for i in range(50)]
        some = draw_speed_factors(fleet, flags, 50, make_rng(SEED))
        assert [s for s, f in zip(some, flags, strict=True) if not f] == [
            s for s, f in zip(none, flags, strict=True) if not f
        ]
        assert {s for s, f in zip(some, flags, strict=True) if f} == {1.0}
        assert draw_speed_factors(FleetSpec(), flags, 50, rng) == ()
        assert draw_speed_factors(fleet, [], 0, rng) == ()

    def test_the_bounds_are_sumos_cut_offs(self) -> None:
        with pytest.raises(ValueError):
            FleetSpec(speed_factor=2.5)
        with pytest.raises(ValueError):
            FleetSpec(speed_factor=0.1)
        with pytest.raises(ValueError):
            FleetSpec(speed_dev=-0.1)


class TestLedger:
    def test_free_flow_time_uses_the_factor(self) -> None:
        geom = RouteGeometry(("a", "b"), (100.0, 200.0), (20.0, 10.0))
        assert geom.free_flow_s(40.0) == geom.free_flow_s(40.0, 1.0) == pytest.approx(25.0)
        # each edge at min(v0, 1.2 × limit)
        assert geom.free_flow_s(40.0, 1.2) == pytest.approx(100.0 / 24.0 + 200.0 / 12.0)
        assert geom.free_flow_s(15.0, 1.2) == pytest.approx(100.0 / 15.0 + 200.0 / 12.0)
        assert geom.free_flow_between_s(50.0, 150.0, 40.0, 1.2) == pytest.approx(
            50.0 / 24.0 + 50.0 / 12.0
        )

    def test_the_journey_table_reads_each_vehicles_factor(self) -> None:
        geom = RouteGeometry(("a", "b"), (100.0, 200.0), (20.0, 10.0))
        args: dict[str, Any] = {
            "veh_ids": ["v1", "v2"],
            "route_by_id": {"v1": "r", "v2": "r"},
            "depart_planned_s": {"v1": 0.0, "v2": 0.0},
            "v0_by_id": {"v1": 40.0, "v2": 40.0},
            "geometry": {"r": geom},
            "depart_s": {"v1": 0.5, "v2": 0.5},
            "insert_offset_m": {"v1": 0.0, "v2": 0.0},
            "arrival_s": {"v1": 30.0, "v2": 30.0},
            "distance_end_m": {},
            "meter_ramp": {},
            "meter_hold": {},
            "meter_release": {},
            "end_s": 60.0,
        }
        plain = _journey_table(**args).to_pandas().set_index("veh_id")
        assert plain["free_flow_s"].tolist() == [pytest.approx(25.0)] * 2
        fast = (
            _journey_table(**args, speed_factor_by_id={"v1": 1.2, "v2": 1.0})
            .to_pandas()
            .set_index("veh_id")
        )
        assert fast.loc["v1", "free_flow_s"] == pytest.approx(100.0 / 24.0 + 200.0 / 12.0)
        assert fast.loc["v2", "free_flow_s"] == pytest.approx(25.0)
        assert fast.loc["v1", "free_flow_covered_s"] == pytest.approx(fast.loc["v1", "free_flow_s"])


def test_a_scripted_or_weave_merge_gets_a_note() -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "name": "x",
            "network": {
                "kind": "osm",
                "osm_file": "x.osm",
                "corridor_edges": ["a", "b"],
                "inflow": [[0.0, 0.1]],
                "ramps": [
                    {
                        "kind": "on",
                        "edges": ["r"],
                        "attach_edge": "a",
                        "inflow": [[0.0, 0.1]],
                        "merge": "scripted",
                    }
                ],
            },
            "fleet": {"speed_factor": 1.2},
            "sim": {"duration_s": 60.0},
        }
    )
    note = _speed_factor_merge_note(cfg)
    assert note is not None and "scripted merge" in note and "speed factor 1" in note
    default = cfg.model_copy(update={"fleet": FleetSpec()})
    assert _speed_factor_merge_note(default) is None


# --- one tiny SUMO pair -------------------------------------------------------

#: The measured boundary speed [m/s] posted on the exit edge.
V_BOUNDARY = 10.0
FACTOR = 1.2


def _boundary_config(fleet: dict[str, Any]) -> ScenarioConfig:
    """1 km single lane, light inflow, exit edge at a 10 m/s measured speed."""
    return ScenarioConfig.model_validate(
        {
            "name": "speed_factor_boundary",
            "network": {
                "kind": "corridor",
                "length_m": 1000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.1]],
                "boundary": {"steps": [[0.0, V_BOUNDARY]], "exit_buffer_m": 300.0},
            },
            "fleet": {"heterogeneity_frac": 0.0, **fleet},
            "sim": {"duration_s": 150.0},
            "seed": 5,
        }
    )


@pytest.fixture(scope="module")
def boundary_runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, dict]]:
    tmp = tmp_path_factory.mktemp("speed_factor")
    out: dict[str, tuple[Path, dict]] = {}
    for name, fleet in (("default", {}), ("factor", {"speed_factor": FACTOR})):
        cfg = _boundary_config(fleet)
        paths = run_micro(cfg, cfg.seed, tmp / name)
        out[name] = (paths.run_dir, json.loads(paths.meta.read_text()))
    return out


def _exit_speeds(run_dir: Path) -> pd.Series:
    """Speeds on the exit edge's last 150 m (generated corridor: the 1000 m
    entry buffer, then the corridor, then the exit edge from x = 2000 m)."""
    traj = pd.read_parquet(run_dir / "trajectories.parquet", columns=["x", "v"])
    return traj.loc[(traj["x"] >= 2150.0) & (traj["x"] < 2300.0), "v"]


@pytest.mark.integration
class TestSumo:
    def test_the_factor_reaches_the_compiled_vtypes(
        self, boundary_runs: dict[str, tuple[Path, dict]]
    ) -> None:
        run_f, meta_f = boundary_runs["factor"]
        run_d, meta_d = boundary_runs["default"]
        assert {v["speedFactor"] for v in _vtypes(run_f / "net" / "demand.rou.xml")} == {"1.2000"}
        assert {v["speedFactor"] for v in _vtypes(run_d / "net" / "demand.rou.xml")} == {"1.0"}
        block = meta_f["speed_factor"]
        assert block["mean"] == FACTOR and block["dev"] == 0.0
        assert block["realized_min"] == block["realized_max"] == FACTOR
        assert block["n_vehicles"] == meta_f["n_vehicles_planned"]
        assert block["boundary_posted_divided_by"] == FACTOR
        # a default run's meta.json keeps its keys
        assert "speed_factor" not in meta_d
        assert meta_f["config_hash"] != meta_d["config_hash"]

    def test_a_vehicle_drives_above_the_posted_limit_at_the_measured_speed(
        self, boundary_runs: dict[str, tuple[Path, dict]]
    ) -> None:
        """The exit edge posts 10 / 1.2 = 8.33 m/s; the factor's vehicles drive
        10 m/s there — above the edge's limit, at the boundary's measured
        speed — and the default fleet drives the posted 10 m/s."""
        posted = V_BOUNDARY / FACTOR
        fast = _exit_speeds(boundary_runs["factor"][0])
        plain = _exit_speeds(boundary_runs["default"][0])
        assert len(fast) > 20 and len(plain) > 20
        assert float(fast.median()) == pytest.approx(V_BOUNDARY, abs=0.2)
        assert float(fast.max()) > posted + 1.0
        assert float(plain.median()) == pytest.approx(V_BOUNDARY, abs=0.2)
        assert float(plain.max()) <= V_BOUNDARY + 0.05
        assert not math.isnan(float(fast.mean()))
