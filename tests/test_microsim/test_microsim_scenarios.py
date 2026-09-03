"""Scenario YAML loading tests (no SUMO) and OSM onboarding (CLAUDE.md §3.2.4).

The onboarding tests build a scenario from the hand-written interchange
fixture of ``test_microsim_osm_ramps.py`` through ``scenario_from_osm``
(which runs ``netconvert``), round-trip it through YAML, and run it for
30 simulated seconds — so they carry the ``integration`` marker.
"""

from __future__ import annotations

import importlib.util
import json
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
from microsim import load_scenario, resolve_scenario, run_micro, scenario_from_osm
from microsim import networks as microsim_networks
from microsim.scenarios import OSM_DEFAULTS_SCENARIO, SCENARIOS_DIR


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
