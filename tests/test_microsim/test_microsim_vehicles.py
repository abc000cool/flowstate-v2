"""Fleet generation tests: heterogeneity, AV tagging, demand XML (no SUMO)."""

import math
import xml.etree.ElementTree as ET

import pytest

from flowstate_core.config import AVSpec, FleetSpec, RingNetwork
from flowstate_core.rng import make_rng
from microsim import (
    build_corridor_plan,
    build_ring_plan,
    corridor_departures,
    draw_vehicle_params,
    tag_avs,
    write_corridor_routes,
    write_ring_routes,
)
from microsim.vehicles import IDM_HARD_LOWER, IDM_PARAM_ORDER

SEED = 20260829


class TestDrawVehicleParams:
    def test_reproducible_for_fixed_seed(self):
        fleet = FleetSpec()
        a = draw_vehicle_params(fleet, 50, make_rng(SEED))
        b = draw_vehicle_params(fleet, 50, make_rng(SEED))
        assert a == b

    def test_different_seeds_differ(self):
        fleet = FleetSpec()
        a = draw_vehicle_params(fleet, 50, make_rng(SEED))
        b = draw_vehicle_params(fleet, 50, make_rng(SEED + 1))
        assert a != b

    def test_zero_heterogeneity_returns_means(self):
        fleet = FleetSpec(heterogeneity_frac=0.0)
        (p,) = draw_vehicle_params(fleet, 1, make_rng(SEED))
        assert p["v0"] == pytest.approx(fleet.v0)
        assert p["T"] == pytest.approx(fleet.T)

    @pytest.mark.parametrize("seed", [1, 42, 999])
    def test_hard_lower_bounds_and_truncation(self, seed):
        """Property (seeded): every draw stays physical and within ±3σ."""
        fleet = FleetSpec(heterogeneity_frac=0.3)  # widest allowed
        means = {"v0": fleet.v0, "T": fleet.T, "a_max": fleet.a_max, "b": fleet.b, "s0": fleet.s0}
        for p in draw_vehicle_params(fleet, 400, make_rng(seed)):
            for key in IDM_PARAM_ORDER:
                sigma = fleet.heterogeneity_frac * means[key]
                assert p[key] >= IDM_HARD_LOWER[key]
                assert p[key] <= means[key] + 3.0 * sigma + 1e-12
                assert p[key] >= means[key] - 3.0 * sigma - 1e-12 or p[key] >= IDM_HARD_LOWER[key]


class TestTagAvs:
    def test_penetration_count_is_exact(self):
        """round(penetration·n) AVs, chosen without replacement (§3.3)."""
        is_av, complied = tag_avs(2000, AVSpec(penetration=0.2, compliance=0.6), make_rng(SEED))
        assert sum(is_av) == round(0.2 * 2000) == 400
        assert all(a or not c for a, c in zip(is_av, complied, strict=True))

    @pytest.mark.parametrize("seed", [3, 42, 777])
    def test_compliance_fraction_matches_expectation_large_n(self, seed):
        """Property (seeded): Bernoulli(p) compliance over many AVs → ~p.

        n_avs = 400, p = 0.6: binomial σ = √(400·0.24) ≈ 9.8, so a ±4σ band
        is ±39 vehicles around 240.
        """
        av = AVSpec(penetration=0.2, compliance=0.6)
        _is_av, complied = tag_avs(2000, av, make_rng(seed))
        n_complied = sum(complied)
        assert abs(n_complied - 0.6 * 400) <= 4.0 * math.sqrt(400 * 0.6 * 0.4)

    def test_ring_dampening_share_gives_exactly_one_av(self):
        """penetration 0.045 of 22 vehicles = the single Stern-style AV."""
        is_av, complied = tag_avs(22, AVSpec(penetration=0.045, compliance=1.0), make_rng(SEED))
        assert sum(is_av) == 1
        assert sum(complied) == 1  # compliance 1.0 ⇒ the AV complies

    def test_zero_penetration(self):
        is_av, complied = tag_avs(100, AVSpec(penetration=0.0), make_rng(SEED))
        assert not any(is_av) and not any(complied)


class TestCorridorDepartures:
    def test_counts_match_piecewise_rates(self):
        times = corridor_departures([(0.0, 0.5), (100.0, 0.25)], 200.0, make_rng(SEED))
        # 0.5 veh/s × 100 s + 0.25 veh/s × 100 s = 50 + 25.
        assert len(times) == 75
        assert times == sorted(times)
        assert all(0.0 <= t < 200.0 for t in times)

    def test_reproducible(self):
        a = corridor_departures([(0.0, 0.4)], 300.0, make_rng(SEED))
        b = corridor_departures([(0.0, 0.4)], 300.0, make_rng(SEED))
        assert a == b

    def test_zero_rate_step_produces_nothing(self):
        times = corridor_departures([(0.0, 0.0), (50.0, 0.5)], 100.0, make_rng(SEED))
        assert all(t >= 50.0 for t in times)
        assert len(times) == 25


class TestRingPlanAndRoutes:
    def test_plan_reproducible_and_positions_spread(self):
        net = RingNetwork(circumference_m=230.0, n_vehicles=22)
        p1 = build_ring_plan(net, FleetSpec(), AVSpec(), make_rng(SEED))
        p2 = build_ring_plan(net, FleetSpec(), AVSpec(), make_rng(SEED))
        assert p1 == p2
        assert p1.n == 22
        # Uniform spacing ± 0.5 m jitter.
        spacing = 230.0 / 22
        for i, pos in enumerate(p1.depart_pos_m):
            assert abs(((pos - i * spacing) + 115.0) % 230.0 - 115.0) <= 0.5 + 1e-9

    def test_route_file_wellformed(self, tmp_path):
        net = RingNetwork(circumference_m=230.0, n_vehicles=22)
        plan = build_ring_plan(net, FleetSpec(), AVSpec(), make_rng(SEED))
        edge_ids = tuple(f"re{i}" for i in range(8))
        offsets = tuple(i * 230.0 / 8 for i in range(8))
        path = write_ring_routes(
            edge_ids, offsets, 230.0, plan, "IDM", 0.5, 600.0, tmp_path / "r.rou.xml"
        )
        root = ET.parse(path).getroot()
        vtypes = root.findall("vType")
        vehicles = root.findall("vehicle")
        assert len(vtypes) == 22 and len(vehicles) == 22
        assert all(v.get("depart") == "0.00" for v in vehicles)
        assert vtypes[0].get("carFollowModel") == "IDM"
        assert vtypes[0].get("speedDev") == "0"  # our RNG, not SUMO's (§0.5)
        # departPos stays within the depart edge.
        for v in vehicles:
            assert 0.0 <= float(v.get("departPos")) <= 230.0 / 8 + 1e-6


class TestCorridorRoutes:
    def test_departures_sorted_and_spread_written(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 120.0, FleetSpec(), AVSpec(), make_rng(SEED))
        edge_ids = ("entry", "ce0", "ce1")
        path = write_corridor_routes(
            edge_ids, plan, "EIDM", 0.5, tmp_path / "c.rou.xml", depart_edge_spread=0
        )
        root = ET.parse(path).getroot()
        vehicles = root.findall("vehicle")
        departs = [float(v.get("depart")) for v in vehicles]
        assert departs == sorted(departs)  # SUMO requirement
        spread = {v.get("departEdge") for v in vehicles}
        assert spread == {"0", "1", "2"}
        assert root.findall("vType")[0].get("carFollowModel") == "EIDM"

    def test_default_spread_keeps_entry_only(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 60.0, FleetSpec(), AVSpec(), make_rng(SEED))
        path = write_corridor_routes(("entry", "ce0"), plan, "IDM", 0.5, tmp_path / "c2.rou.xml")
        root = ET.parse(path).getroot()
        assert all(v.get("departEdge") is None for v in root.findall("vehicle"))
