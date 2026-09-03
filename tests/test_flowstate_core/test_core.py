"""Tests for flowstate_core: units, RNG, config round-trip, artifacts, hashing."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from flowstate_core import (
    BoundarySpec,
    CorridorNetwork,
    DemandProfile,
    OSMNetwork,
    RampSpec,
    RingNetwork,
    ScenarioConfig,
    SimSpec,
    TriangularFD,
    config_hash,
)
from flowstate_core.constants import V1_LEGACY_FD
from flowstate_core.rng import make_rng, spawn_seeds, sumo_seed, truncated_normal
from flowstate_core.units import (
    kmh_to_ms,
    ms_to_kmh,
    veh_h_to_veh_s,
    veh_km_to_veh_m,
    veh_m_to_veh_km,
    veh_s_to_veh_h,
)


class TestUnits:
    def test_known_values(self):
        assert kmh_to_ms(36.0) == pytest.approx(10.0)
        assert ms_to_kmh(10.0) == pytest.approx(36.0)
        assert veh_km_to_veh_m(160.0) == pytest.approx(0.16)
        assert veh_h_to_veh_s(3600.0) == pytest.approx(1.0)

    @given(st.floats(-1e6, 1e6, allow_nan=False))
    def test_round_trips(self, x: float):
        assert ms_to_kmh(kmh_to_ms(x)) == pytest.approx(x, abs=1e-9)
        assert veh_m_to_veh_km(veh_km_to_veh_m(x)) == pytest.approx(x, abs=1e-9)
        assert veh_s_to_veh_h(veh_h_to_veh_s(x)) == pytest.approx(x, abs=1e-9)


class TestRng:
    def test_spawn_deterministic_and_prefix_stable(self):
        a = spawn_seeds(42, 20)
        b = spawn_seeds(42, 20)
        assert a == b
        assert spawn_seeds(42, 5) == a[:5]
        assert len(set(a)) == 20

    def test_generator_deterministic(self):
        assert make_rng(7).normal() == make_rng(7).normal()

    def test_sumo_seed_range(self):
        for s in spawn_seeds(1, 50):
            assert 0 <= sumo_seed(s) < 2**31

    def test_truncated_normal_bounds(self):
        rng = make_rng(3)
        draws = [truncated_normal(rng, 1.4, 0.17, low=0.1) for _ in range(500)]
        assert all(1.4 - 3 * 0.17 <= d <= 1.4 + 3 * 0.17 for d in draws)
        assert truncated_normal(rng, 5.0, 0.0) == 5.0

    def test_truncated_normal_rejects_bad_sigma(self):
        with pytest.raises(ValueError):
            truncated_normal(make_rng(0), 1.0, -0.1)


def _ring_config(**overrides) -> ScenarioConfig:
    base = dict(
        name="ring_test",
        network=RingNetwork(circumference_m=230.0, n_vehicles=22),
        sim=SimSpec(duration_s=600.0),
        seed=42,
    )
    base.update(overrides)
    return ScenarioConfig(**base)


class TestConfig:
    def test_yaml_round_trip(self, tmp_path):
        cfg = _ring_config()
        p = tmp_path / "ring.yaml"
        cfg.to_yaml(p)
        assert ScenarioConfig.from_yaml(p) == cfg

    def test_hash_stable_and_sensitive(self):
        h1 = config_hash(_ring_config())
        assert h1 == config_hash(_ring_config())
        assert len(h1) == 12
        assert h1 != config_hash(_ring_config(seed=43))

    def test_seeded_flag(self):
        assert not _ring_config().seeded
        cfg = _ring_config(
            perturbation={"t_s": 60, "position_m": 100, "duration_s": 10, "v_drop_ms": 5}
        )
        assert cfg.seeded

    def test_penetration_bounds_enforced(self):
        with pytest.raises(ValueError):
            _ring_config(av={"penetration": 0.5})

    def test_corridor_inflow_must_be_ordered(self):
        with pytest.raises(ValueError):
            ScenarioConfig(
                name="bad",
                network={
                    "kind": "corridor",
                    "length_m": 1000,
                    "inflow": [(60.0, 0.5), (0.0, 0.3)],
                },
                sim=SimSpec(duration_s=60),
            )


def _corridor_config(**net_overrides) -> ScenarioConfig:
    net = dict(kind="corridor", length_m=1000.0, lanes=2, inflow=[(0.0, 0.5)])
    net.update(net_overrides)
    return ScenarioConfig(name="corr_test", network=net, sim=SimSpec(duration_s=120.0))


class TestBoundarySpec:
    """Measured downstream boundary condition (docs/CONTRACTS.md §2, M3)."""

    def test_defaults_and_yaml_round_trip(self, tmp_path):
        cfg = _corridor_config(boundary={"steps": [(0.0, 12.0), (30.0, 8.5), (60.0, 10.0)]})
        net = cfg.network
        assert isinstance(net, CorridorNetwork)
        assert net.boundary is not None
        assert net.boundary.kind == "speed_schedule"
        assert net.boundary.exit_buffer_m == 200.0
        p = tmp_path / "corr.yaml"
        cfg.to_yaml(p)
        assert ScenarioConfig.from_yaml(p) == cfg

    def test_none_by_default_and_hash_sensitive(self):
        plain = _corridor_config()
        assert isinstance(plain.network, CorridorNetwork)
        assert plain.network.boundary is None
        with_b = _corridor_config(boundary={"steps": [(0.0, 12.0)]})
        assert config_hash(plain) != config_hash(with_b)

    def test_steps_must_be_time_ordered(self):
        with pytest.raises(ValueError, match="ordered"):
            BoundarySpec(steps=[(30.0, 8.0), (0.0, 12.0)])

    def test_speed_limits_must_be_positive(self):
        with pytest.raises(ValueError, match="> 0"):
            BoundarySpec(steps=[(0.0, 0.0)])
        with pytest.raises(ValueError, match="> 0"):
            BoundarySpec(steps=[(0.0, -3.0)])

    def test_needs_at_least_one_step_and_positive_buffer(self):
        with pytest.raises(ValueError):
            BoundarySpec(steps=[])
        with pytest.raises(ValueError):
            BoundarySpec(steps=[(0.0, 10.0)], exit_buffer_m=0.0)

    def test_boundary_does_not_set_seeded(self):
        cfg = _corridor_config(boundary={"steps": [(0.0, 9.0)]})
        assert cfg.seeded is False


def _osm_network(**overrides):
    base = {
        "kind": "osm",
        "osm_file": "x.osm",
        "corridor_edges": ["a", "b", "c"],
        "inflow": [(0.0, 0.5)],
    }
    base.update(overrides)
    return OSMNetwork.model_validate(base)


class TestOSMRampsAndBoundary:
    """docs/CONTRACTS.md §2: OSM corridors carry a boundary and ramps."""

    def test_boundary_needs_two_corridor_edges(self):
        net = _osm_network(boundary={"steps": [(0.0, 5.0)]})
        assert net.boundary is not None and net.boundary.kind == "speed_schedule"
        with pytest.raises(ValueError, match="at least two edges"):
            _osm_network(corridor_edges=["a"], boundary={"steps": [(0.0, 5.0)]})

    def test_on_ramp_rules(self):
        on = RampSpec(kind="on", edges=["r1"], attach_edge="b", inflow=[(0.0, 0.1)])
        assert on.exit_fraction == []
        with pytest.raises(ValueError, match="non-empty inflow"):
            RampSpec(kind="on", edges=["r1"], attach_edge="b")
        with pytest.raises(ValueError, match="cannot carry exit_fraction"):
            RampSpec(
                kind="on",
                edges=["r1"],
                attach_edge="b",
                inflow=[(0.0, 0.1)],
                exit_fraction=[(0, 0.1)],
            )
        with pytest.raises(ValueError, match="time-ordered"):
            RampSpec(kind="on", edges=["r1"], attach_edge="b", inflow=[(10.0, 0.1), (0.0, 0.2)])

    def test_off_ramp_rules(self):
        off = RampSpec(kind="off", edges=["r2"], attach_edge="b", exit_fraction=[(0.0, 0.2)])
        assert off.inflow == []
        with pytest.raises(ValueError, match="non-empty exit_fraction"):
            RampSpec(kind="off", edges=["r2"], attach_edge="b")
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            RampSpec(kind="off", edges=["r2"], attach_edge="b", exit_fraction=[(0.0, 1.5)])
        with pytest.raises(ValueError, match="cannot carry inflow"):
            RampSpec(
                kind="off",
                edges=["r2"],
                attach_edge="b",
                exit_fraction=[(0.0, 0.2)],
                inflow=[(0, 1)],
            )

    def test_ramp_must_attach_to_corridor_and_not_overlap(self):
        ramp = {"kind": "on", "edges": ["r1"], "attach_edge": "zz", "inflow": [(0.0, 0.1)]}
        with pytest.raises(ValueError, match="not in corridor_edges"):
            _osm_network(ramps=[ramp])
        overlap = {"kind": "off", "edges": ["b"], "attach_edge": "a", "exit_fraction": [(0, 0.1)]}
        with pytest.raises(ValueError, match="overlap"):
            _osm_network(ramps=[overlap])

    def test_ramps_round_trip_and_hash(self, tmp_path):
        ramp = {
            "kind": "on",
            "edges": ["r1", "r2"],
            "attach_edge": "b",
            "inflow": [(0.0, 0.1), (60.0, 0.2)],
            "name": "OH on",
        }
        cfg = ScenarioConfig.model_validate(
            {
                "name": "osm_ramps",
                "network": _osm_network(ramps=[ramp]).model_dump(),
                "sim": {"duration_s": 10.0},
            }
        )
        path = tmp_path / "s.yaml"
        cfg.to_yaml(path)
        again = ScenarioConfig.from_yaml(path)
        assert again == cfg and again.network.ramps[0].name == "OH on"
        plain = ScenarioConfig.model_validate(
            {
                "name": "osm_ramps",
                "network": _osm_network().model_dump(),
                "sim": {"duration_s": 10.0},
            }
        )
        assert config_hash(plain) != config_hash(cfg)
        assert cfg.seeded is False


class TestArtifacts:
    def test_triangular_fd_derived_quantities(self):
        fd = V1_LEGACY_FD
        # v_f=27.78 m/s, w=-5.56 m/s, rho_jam=0.16 veh/m
        # rho_c = rho_jam * -w/(v_f - w) = 0.16 * 5.556/33.33 ≈ 0.02667
        assert fd.rho_c == pytest.approx(0.16 * (kmh_to_ms(20) / kmh_to_ms(120)))
        assert fd.q_max == pytest.approx(fd.rho_c * fd.v_f)
        # Sending/receiving agree with equilibrium flow at the extremes
        assert fd.demand(0.0) == 0.0
        assert fd.supply(fd.rho_jam) == pytest.approx(0.0, abs=1e-12)
        assert fd.equilibrium_flow(fd.rho_c) == pytest.approx(fd.q_max)

    @given(st.floats(0.0, 0.16))
    def test_fd_flux_bounds(self, rho: float):
        fd = V1_LEGACY_FD
        assert 0.0 <= fd.equilibrium_flow(rho) <= fd.q_max + 1e-12
        assert fd.demand(rho) >= 0.0
        assert fd.supply(rho) >= -1e-12

    def test_fd_rejects_positive_w(self):
        with pytest.raises(ValueError):
            TriangularFD(v_f=30.0, w=5.0, rho_jam=0.16)

    def test_demand_profile_round_trip_and_lookup(self, tmp_path):
        dp = DemandProfile(
            created_at="2026-08-29T00:00:00Z",
            source="test fixture",
            data_hash="abc",
            steps=[(0.0, 0.3), (600.0, 0.6)],
        )
        p = tmp_path / "demand.json"
        dp.save(p)
        loaded = DemandProfile.load(p)
        assert loaded == dp
        assert loaded.inflow_at(-1.0) == 0.0
        assert loaded.inflow_at(0.0) == 0.3
        assert loaded.inflow_at(599.9) == 0.3
        assert loaded.inflow_at(600.0) == 0.6

    def test_demand_profile_rejects_unordered(self):
        with pytest.raises(ValueError):
            DemandProfile(
                created_at="t",
                source="s",
                data_hash="h",
                steps=[(600.0, 0.6), (0.0, 0.3)],
            )

    def test_no_leader_conventions(self):
        from flowstate_core import ControllerObs

        obs = ControllerObs(t=0, dt=0.5, v=30.0, gap=math.inf, v_leader=math.nan, v_ref=30.0)
        assert math.isinf(obs.gap)
        assert math.isnan(obs.v_leader)
