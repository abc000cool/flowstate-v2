"""Scenario YAML loading tests (no SUMO) and OSM onboarding (CLAUDE.md §3.2.4).

The onboarding tests build a scenario from the hand-written interchange
fixture of ``test_microsim_osm_ramps.py`` through ``scenario_from_osm``
(which runs ``netconvert``), round-trip it through YAML, and run it for
30 simulated seconds — so they carry the ``integration`` marker.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import sumolib

from flowstate_core.config import (
    CorridorNetwork,
    OSMNetwork,
    RampSpec,
    RingNetwork,
    ScenarioConfig,
    config_hash,
)
from microsim import load_scenario, resolve_scenario, run_micro, scenario_from_osm, scenarios
from microsim import networks as microsim_networks
from microsim.geo import PointOnChain, RampCandidate
from microsim.scenarios import OSM_DEFAULTS_SCENARIO, SCENARIOS_DIR, CorridorBuild


class TestRingSugiyamaYaml:
    def test_parses_via_from_yaml(self):
        cfg = ScenarioConfig.from_yaml(SCENARIOS_DIR / "ring_sugiyama.yaml")
        assert cfg.name == "ring_sugiyama"
        assert cfg.tier == "micro"
        assert isinstance(cfg.network, RingNetwork)
        assert cfg.network.circumference_m == pytest.approx(230.0)
        assert cfg.network.n_vehicles == 22
        assert cfg.fleet.model == "IDM"
        assert cfg.fleet.heterogeneity_frac == pytest.approx(0.12)
        assert cfg.sim.duration_s == pytest.approx(600.0)
        assert cfg.perturbation is None and not cfg.seeded  # emergent, §0.2
        assert cfg.seed == 42
        assert cfg.replicates == 20

    def test_no_avs_in_baseline(self):
        cfg = load_scenario("ring_sugiyama")
        assert cfg.av.penetration == 0.0
        assert cfg.av.controller is None


class TestCorridor10kmYaml:
    def test_parses_via_from_yaml(self):
        cfg = ScenarioConfig.from_yaml(SCENARIOS_DIR / "corridor_10km.yaml")
        assert cfg.name == "corridor_10km"
        assert isinstance(cfg.network, CorridorNetwork)
        assert cfg.network.length_m == pytest.approx(10000.0)
        assert cfg.network.lanes == 1
        # Demand ramps into the unstable band and stays seeded=False.
        rates = [q for _, q in cfg.network.inflow]
        assert max(rates) == pytest.approx(0.50)  # 1800 veh/h
        assert cfg.fleet.model == "EIDM"  # IDM is string-stable here; see YAML
        assert cfg.perturbation is None and not cfg.seeded
        assert cfg.sim.duration_s == pytest.approx(1200.0)
        assert cfg.replicates == 20

    def test_inflow_time_ordered(self):
        cfg = load_scenario("corridor_10km")
        times = [t for t, _ in cfg.network.inflow]
        assert times == sorted(times)


class TestResolution:
    def test_resolve_by_name_and_path(self):
        by_name = resolve_scenario("ring_sugiyama")
        by_suffix = resolve_scenario("ring_sugiyama.yaml")
        by_path = resolve_scenario(SCENARIOS_DIR / "ring_sugiyama.yaml")
        assert by_name == by_suffix == by_path

    def test_unknown_scenario_lists_available(self):
        with pytest.raises(FileNotFoundError, match="ring_sugiyama"):
            resolve_scenario("definitely_not_a_scenario")

    def test_config_hash_is_stable(self):
        cfg = load_scenario("ring_sugiyama")
        h1, h2 = config_hash(cfg), config_hash(load_scenario("ring_sugiyama"))
        assert h1 == h2
        assert len(h1) == 12 and all(c in "0123456789abcdef" for c in h1)


# --- OSM onboarding (§3.2.4) --------------------------------------------------

CORRIDOR = ["100", "101", "102"]


def _ramp_osm() -> str:
    """The interchange fixture of the OSM ramps test module.

    Test modules are not importable from one another under pytest's
    ``--import-mode=importlib``, so the sibling file is loaded by path.
    """
    path = Path(__file__).with_name("test_microsim_osm_ramps.py")
    spec = importlib.util.spec_from_file_location("_osm_ramps_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.RAMP_OSM)


@pytest.fixture
def osm_path(tmp_path):
    p = tmp_path / "ramps.osm"
    p.write_text(_ramp_osm())
    return p


def _onboard(osm_path: Path, workdir: Path, **kwargs) -> ScenarioConfig:
    params = {
        "name": "osm_fixture",
        "osm_file": osm_path,
        "corridor_edges": CORRIDOR,
        "inflow": 0.5,
        "workdir": workdir,
        "duration_s": 30.0,
        "seed": 7,
    }
    params.update(kwargs)
    return scenario_from_osm(**params)


@pytest.mark.integration
class TestScenarioFromOSM:
    def test_builds_validated_config_with_corridor_defaults(self, osm_path, tmp_path):
        cfg = _onboard(osm_path, tmp_path / "w", lanes=2)
        base = load_scenario(OSM_DEFAULTS_SCENARIO)
        assert isinstance(cfg.network, OSMNetwork)
        assert cfg.network.corridor_edges == CORRIDOR
        assert cfg.network.inflow == [(0.0, 0.5)]
        assert cfg.network.osm_file == str(osm_path) and cfg.network.bbox is None
        assert cfg.network.ramps == [] and cfg.network.boundary is None
        assert cfg.name == "osm_fixture" and cfg.tier == "micro"
        assert cfg.seed == 7 and not cfg.seeded
        assert cfg.fleet == base.fleet
        assert cfg.sim.duration_s == pytest.approx(30.0)
        assert cfg.sim.step_length_s == base.sim.step_length_s
        assert cfg.sim.action_step_s == base.sim.action_step_s
        assert cfg.sim.output_hz == base.sim.output_hz
        assert cfg.sim.warmup_s == 0.0  # the 120 s base warm-up would outlast a 30 s run
        assert cfg.replicates == base.replicates
        assert cfg.av.penetration == 0.0 and cfg.av.controller is None
        assert (tmp_path / "w" / "net" / "osm.net.xml").is_file()

    def test_base_warmup_kept_when_it_fits(self, osm_path, tmp_path):
        base = load_scenario(OSM_DEFAULTS_SCENARIO)
        cfg = _onboard(osm_path, tmp_path / "w", duration_s=600.0)
        assert cfg.sim.warmup_s == base.sim.warmup_s
        assert _onboard(osm_path, tmp_path / "w2", warmup_s=5.0).sim.warmup_s == 5.0

    def test_yaml_round_trip_preserves_config_hash(self, osm_path, tmp_path):
        cfg = _onboard(osm_path, tmp_path / "w")
        yaml_path = tmp_path / "osm_fixture.yaml"
        cfg.to_yaml(yaml_path)
        back = load_scenario(yaml_path)
        assert back == cfg
        assert config_hash(back) == config_hash(cfg)

    def test_inflow_steps_and_units(self, osm_path, tmp_path):
        steps = [(0.0, 0.4), (10.0, 0.5)]
        cfg = _onboard(osm_path, tmp_path / "w", inflow=steps)
        assert cfg.network.inflow == steps

    @pytest.mark.parametrize(
        "kwargs, needle",
        [
            ({"corridor_edges": ["100", "101", "999"]}, "999"),
            ({"corridor_edges": []}, "at least one corridor edge"),
            ({"corridor_edges": ["100", "102"]}, "not connected"),  # 100 -> 101 -> 102
            ({"lanes": 3}, "lanes"),  # entry edge 100 has 2 lanes
            ({"inflow": [(10.0, 0.5), (0.0, 0.4)]}, "ordered"),
            ({"inflow": []}, "at least one"),
            ({"inflow": -0.1}, ">= 0"),
            ({"osm_file": None}, "osm_file or bbox"),
            ({"duration_s": 0.0}, "duration_s"),
        ],
    )
    def test_rejects_bad_inputs(self, osm_path, tmp_path, kwargs, needle):
        with pytest.raises(ValueError, match=needle):
            _onboard(osm_path, tmp_path / "w", **kwargs)

    def test_lane_check_passes_on_the_entry_edge(self, osm_path, tmp_path):
        cfg = _onboard(osm_path, tmp_path / "w", lanes=2)
        net = sumolib.net.readNet(str(tmp_path / "w" / "net" / "osm.net.xml"))
        assert net.getEdge(cfg.network.corridor_edges[0]).getLaneNumber() == 2

    def test_ramps_are_kept_and_recorded(self, osm_path, tmp_path):
        ramp = RampSpec(kind="on", edges=["200"], attach_edge="102", inflow=[(0.0, 0.2)])
        cfg = _onboard(osm_path, tmp_path / "w", ramps=[ramp])
        assert cfg.network.ramps == [ramp]
        net = sumolib.net.readNet(str(tmp_path / "w" / "net" / "osm.net.xml"))
        assert {e.getID() for e in net.getEdges(withInternal=False)} >= {*CORRIDOR, "200"}

    def test_bbox_download_is_persisted_and_recorded(self, tmp_path, monkeypatch):
        """The bbox path records the persisted extract, not the volatile map."""
        bbox = (39.99, -96.01, 40.01, -95.98)
        calls: list[tuple[float, float, float, float]] = []

        def fake_download(box, dest):
            calls.append(box)
            dest.write_text(_ramp_osm())
            return dest

        monkeypatch.setattr(microsim_networks, "_download_bbox", fake_download)
        cfg = scenario_from_osm(
            name="osm_bbox",
            bbox=bbox,
            corridor_edges=CORRIDOR,
            inflow=0.5,
            workdir=tmp_path / "w",
            duration_s=30.0,
        )
        assert calls == [bbox]
        assert cfg.network.bbox == bbox
        assert cfg.network.osm_file is not None
        extract = Path(cfg.network.osm_file)
        assert extract.is_file() and extract.parent == tmp_path / "w" / "net"

    def test_onboarded_scenario_runs(self, osm_path, tmp_path):
        """30 simulated seconds through run_micro: the pipeline output is runnable."""
        cfg = _onboard(osm_path, tmp_path / "w")
        paths = run_micro(cfg, cfg.seed, tmp_path / "run")
        meta = json.loads(paths.meta.read_text())
        assert meta["config_hash"] == config_hash(cfg)
        assert meta["seeded"] is False
        assert meta["n_vehicles_departed"] > 0
        assert paths.trajectories.is_file() and paths.edges.is_file()


# --- lane profile vs detector inventory (docs/ONBOARDING_MNDOT.md §7) ---------


def _synthetic_build() -> CorridorBuild:
    """A :class:`CorridorBuild` with a hand-made lane profile and two ramps.

    No SUMO: the lane check reads only the lane profile, the ramp positions
    and the projected station positions, so the whole check is exercised from
    literals. Lanes: 3 up to x=1000 m, 4 through the merge (1000–1400 m,
    an acceleration lane), 3 again to the end at 3000 m.
    """
    return CorridorBuild(
        config=load_scenario("corridor_10km"),
        chain_edges=("e0", "e1"),
        length_m=3000.0,
        lanes_profile=((0.0, 1000.0, 3), (1000.0, 1400.0, 4), (1400.0, 3000.0, 3)),
        ramps=(
            RampCandidate(kind="on", edges=("r1",), attach_edge="e1", x_m=1000.0),
            RampCandidate(kind="off", edges=("r2",), attach_edge="e1", x_m=2600.0),
        ),
        net_path=Path("net.xml"),
        osm_file=Path("extract.osm"),
        bbox=(44.0, -93.0, 45.0, -92.0),
        bearing_deg=270.0,
        station_x={
            "S_ok": PointOnChain(x_m=500.0, offset_m=3.0, edge_id="e0", lane_pos=500.0),
            "S_merge": PointOnChain(x_m=1200.0, offset_m=4.0, edge_id="e1", lane_pos=200.0),
            "S_far": PointOnChain(x_m=2000.0, offset_m=4.0, edge_id="e1", lane_pos=1000.0),
            "S_exit": PointOnChain(x_m=2500.0, offset_m=4.0, edge_id="e1", lane_pos=1500.0),
            "R_on": PointOnChain(x_m=1000.0, offset_m=5.0, edge_id="e1", lane_pos=0.0),
        },
        stations_rejected={
            "S_other": PointOnChain(x_m=800.0, offset_m=180.0, edge_id="e0", lane_pos=800.0)
        },
    )


#: The inventory the synthetic build is checked against: one agreeing
#: station, one at the guessed merge, one plain disagreement away from any
#: ramp, one auxiliary lane beside an exit, plus rows that must not be
#: compared at all (a ramp detector, an off-corridor station, a blank count).
_INVENTORY: list[dict[str, object]] = [
    {"station": "S_ok", "lanes": 3, "kind": "mainline"},
    {"station": "S_merge", "lanes": 3, "kind": "mainline"},
    {"station": "S_far", "lanes": 2, "kind": "mainline"},
    {"station": "S_exit", "lanes": 4, "kind": "mainline"},
    {"station": "R_on", "lanes": 1, "kind": "on_ramp"},
    {"station": "S_other", "lanes": 2, "kind": "mainline"},
    {"station": "S_blank", "lanes": "", "kind": "mainline", "x_m": 500.0},
]


class TestLaneCheck:
    def test_lanes_at_x_covers_the_chain_and_stops_at_its_ends(self):
        profile = _synthetic_build().lanes_profile
        assert scenarios.lanes_at_x(profile, 0.0) == 3
        assert scenarios.lanes_at_x(profile, 1200.0) == 4
        assert scenarios.lanes_at_x(profile, 1400.0) == 3
        assert scenarios.lanes_at_x(profile, 3000.0) == 3  # the chain's exact end
        assert scenarios.lanes_at_x(profile, 3000.1) is None
        assert scenarios.lanes_at_x(profile, -1.0) is None

    def test_reports_each_disagreement_with_a_hint(self):
        build = _synthetic_build()
        found = {m.station: m for m in build.lane_check(_INVENTORY)}
        assert set(found) == {"S_merge", "S_far", "S_exit"}  # S_ok matches
        assert (found["S_merge"].compiled_lanes, found["S_merge"].inventory_lanes) == (4, 3)
        assert found["S_merge"].hint == "acceleration lane added by ramp guessing"
        assert found["S_merge"].x_m == pytest.approx(1200.0) and found["S_merge"].delta == 1
        assert found["S_far"].hint == "map lane count differs from the inventory"
        # A missing compiled lane near a ramp has two readings and the hint
        # must name the one the check exists for as well.
        assert found["S_exit"].hint == (
            "auxiliary lane in the inventory, or an acceleration lane the map does not carry"
        )
        assert found["S_exit"].delta == -1
        assert [m.station for m in build.lane_check(_INVENTORY)] == ["S_merge", "S_far", "S_exit"]

    def test_skips_rows_it_cannot_or_must_not_compare(self):
        build = _synthetic_build()
        # 4 of 7 rows: the ramp detector, the rejected station (another
        # carriageway) and the row with no lane count are not comparable.
        assert build.lanes_compared(_INVENTORY) == 4
        assert build.lane_check([{"station": "S_ok", "lanes": 3}]) == []  # kind may be absent
        off_chain = [{"station": "X", "lanes": 9, "x_m": 9999.0}]
        assert build.lane_check(off_chain) == [] and build.lanes_compared(off_chain) == 0

    def test_a_row_x_is_used_when_the_build_placed_no_station(self):
        build = _synthetic_build()
        rows = [{"station": "S_csv", "lanes": 3, "kind": "mainline", "x_m": 1200.0}]
        (mismatch,) = build.lane_check(rows)
        assert mismatch.x_m == pytest.approx(1200.0) and mismatch.compiled_lanes == 4

    def test_tolerance_hides_differences_it_covers(self):
        build = _synthetic_build()
        # Every disagreement in the inventory is one lane wide.
        assert build.lane_check(_INVENTORY, tolerance=1) == []
        rows = [*_INVENTORY, {"station": "S_two", "lanes": 5, "kind": "mainline", "x_m": 2000.0}]
        assert [m.station for m in build.lane_check(rows, tolerance=1)] == ["S_two"]
        assert build.lane_check(rows, tolerance=2) == []

    def test_a_non_integral_lane_count_is_unusable_not_truncated(self):
        build = _synthetic_build()
        # 3.7 truncated to 3 used to invent an agreement at S_ok (3 lanes)
        # and a disagreement at S_merge (4); neither is in the inventory.
        rows = [
            {"station": "S_ok", "lanes": "3.7", "kind": "mainline"},
            {"station": "S_merge", "lanes": "3.7", "kind": "mainline"},
            {"station": "S_far", "lanes": "nan", "kind": "mainline"},
        ]
        assert build.lanes_compared(rows) == 0
        assert build.lane_check(rows) == []
        # an integral value written as a float is still a lane count
        assert build.lanes_compared([{"station": "S_ok", "lanes": "3.0"}]) == 1

    def test_an_impossible_lane_count_is_unusable_not_a_mismatch(self):
        build = _synthetic_build()
        # No freeway carriageway carries 40 lanes: the row is a unit error or
        # a both-directions total, and reporting "map 3, inventory 40" as a
        # lane disagreement would put a data-entry slip into a map check.
        rows = [
            {"station": "S_ok", "lanes": scenarios.MAX_INVENTORY_LANES + 1, "kind": "mainline"},
            {"station": "S_merge", "lanes": 40, "kind": "mainline"},
        ]
        assert build.lanes_compared(rows) == 0
        assert build.lane_check(rows) == []
        # the bound itself is still a lane count
        at_bound = [{"station": "S_ok", "lanes": scenarios.MAX_INVENTORY_LANES}]
        assert build.lanes_compared(at_bound) == 1
        assert [m.inventory_lanes for m in build.lane_check(at_bound)] == [
            scenarios.MAX_INVENTORY_LANES
        ]

    def test_summary_carries_the_block_only_when_stations_are_given(self):
        build = _synthetic_build()
        assert "lanes vs inventory" not in build.summary()
        text = build.summary(_INVENTORY)
        assert "lanes vs inventory: 1 of 4 mainline stations match" in text
        assert "map 4 lanes, inventory 3 lanes  (acceleration lane added by ramp guessing)" in text

    def test_summary_does_not_call_an_unchecked_table_a_match(self):
        build = _synthetic_build()
        # "0 of 0 mainline stations match" reads as a check that passed.
        assert "lanes vs inventory: no station table given" in build.summary([])
        assert "of 0 mainline stations match" not in build.summary([])
        uncomparable = [{"station": "R_on", "lanes": 1, "kind": "on_ramp"}]
        assert "lanes vs inventory: no comparable mainline stations" in build.summary(uncomparable)


def _load_onboard_cli():
    """Import ``scripts/onboard_corridor.py`` by path (``scripts/`` is not a package)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "onboard_corridor.py"
    spec = importlib.util.spec_from_file_location("_onboard_corridor_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestFailingMismatches:
    """Which disagreements ``--fail-on-lane-mismatch`` exits on.

    A tolerance cannot tell the benign one-lane case from the dangerous one
    (both are one lane wide), so the acceleration lane is suppressed by hint
    class and the tolerance stays at 0.
    """

    def _mismatch(self, station, compiled, inventory, hint):
        return scenarios.LaneMismatch(
            station=station,
            x_m=1000.0,
            compiled_lanes=compiled,
            inventory_lanes=inventory,
            hint=hint,
        )

    def test_the_guessed_acceleration_lane_is_not_a_failure(self):
        cli = _load_onboard_cli()
        accel = self._mismatch("S_merge", 4, 3, cli.ACCEL_LANE_HINT)
        assert cli.failing_mismatches([accel]) == []
        # ... unless it is asked for explicitly
        assert cli.failing_mismatches([accel], strict=True) == [accel]

    def test_every_other_disagreement_survives_at_tolerance_zero(self):
        cli = _load_onboard_cli()
        missing = self._mismatch(
            "S_merge",
            3,
            4,
            "auxiliary lane in the inventory, or an acceleration lane the map does not carry",
        )
        two_lanes = self._mismatch("S_wide", 5, 3, cli.ACCEL_LANE_HINT)  # delta +2
        plain = self._mismatch("S_far", 4, 3, "map lane count differs from the inventory")
        got = cli.failing_mismatches([missing, two_lanes, plain])
        assert [m.station for m in got] == ["S_merge", "S_wide", "S_far"]

    def test_the_tolerance_defaults_to_zero(self):
        cli = _load_onboard_cli()
        args = cli.parse_args(
            [
                "--name",
                "x",
                "--bbox",
                "1",
                "2",
                "3",
                "4",
                "--bearing",
                "90",
                "--workdir",
                "w",
                "--out",
                "x.yaml",
            ]
        )
        assert args.lane_tolerance == 0 and args.strict_lanes is False

    @staticmethod
    def _argv(*extra: str) -> list[str]:
        return [
            "--name",
            "x",
            "--bbox",
            "1",
            "2",
            "3",
            "4",
            "--bearing",
            "90",
            "--workdir",
            "w",
            "--out",
            "x.yaml",
            *extra,
        ]

    def test_strict_lanes_with_a_tolerance_is_refused_not_silently_ignored(self, capsys):
        """The combination asks for a stricter check and would get a looser one.

        ``lane_check(tolerance=1)`` drops every one-lane disagreement — the
        guessed acceleration lane among them — before ``failing_mismatches``
        can add it back, so ``--strict-lanes`` does nothing there. The CLI
        refuses the pair (exit 2) before it spends a minute on Overpass and
        netconvert.
        """
        cli = _load_onboard_cli()
        code = cli.main(self._argv("--strict-lanes", "--lane-tolerance", "1"))
        assert code == cli.BAD_USAGE_EXIT == 2
        out = capsys.readouterr().out
        assert "--strict-lanes" in out and "--lane-tolerance" in out
        # nothing was built: the refusal comes before the bounding box is read
        assert "scenario" not in out
