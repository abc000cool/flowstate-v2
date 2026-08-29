"""Scenario YAML loading tests (no SUMO)."""

import pytest

from flowstate_core.config import (
    CorridorNetwork,
    RingNetwork,
    ScenarioConfig,
    config_hash,
)
from microsim import load_scenario, resolve_scenario
from microsim.scenarios import SCENARIOS_DIR


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
