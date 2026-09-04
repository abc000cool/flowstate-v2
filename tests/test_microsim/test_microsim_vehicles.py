"""Fleet generation tests: heterogeneity, AV tagging, demand XML (no SUMO)."""

import math
import typing
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


def _write_calibration(path, mean, cov):
    """Write a minimal IDMCalibration artifact for fleet-draw tests."""
    from flowstate_core.artifacts import IDMCalibration

    IDMCalibration(
        created_at="2026-08-29T00:00:00Z",
        source="unit-test synthetic population",
        data_hash="test-hash-123",
        mean=mean,
        cov=cov,
        n_episodes_fit=10,
        n_episodes_holdout=4,
        holdout_gap_rmse_m=1.0,
    ).save(path)
    return path


class TestDrawFromCalibration:
    """fleet.idm_calibration consumption (docs/CONTRACTS.md §2)."""

    MEAN: typing.ClassVar[dict[str, float]] = {
        "v0": 30.0,
        "T": 1.2,
        "a_max": 0.8,
        "b": 1.5,
        "s0": 2.2,
    }
    # Correlated, comfortably away from the hard floors.
    COV: typing.ClassVar[list[list[float]]] = [
        [4.0, 0.1, 0.0, 0.0, 0.0],
        [0.1, 0.04, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.01, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.04, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.04],
    ]

    def _fleet(self, tmp_path):
        art = _write_calibration(tmp_path / "idm_cal.json", self.MEAN, self.COV)
        return FleetSpec(idm_calibration=str(art))

    def test_population_stats_override_scalar_fields(self, tmp_path):
        """The artifact's mean, not FleetSpec.v0 etc., governs the draws."""
        art = _write_calibration(tmp_path / "idm_cal.json", self.MEAN, self.COV)
        fleet = FleetSpec(v0=10.0, T=0.5, idm_calibration=str(art))
        draws = draw_vehicle_params(fleet, 400, make_rng(SEED))
        for key in IDM_PARAM_ORDER:
            sigma = math.sqrt(self.COV[IDM_PARAM_ORDER.index(key)][IDM_PARAM_ORDER.index(key)])
            sample_mean = sum(p[key] for p in draws) / len(draws)
            # sample mean of 400 truncated-normal draws: ±5·σ/√400 band
            assert abs(sample_mean - self.MEAN[key]) <= 5.0 * sigma / 20.0

    def test_reproducible_and_seed_sensitive(self, tmp_path):
        fleet = self._fleet(tmp_path)
        a = draw_vehicle_params(fleet, 30, make_rng(SEED))
        b = draw_vehicle_params(fleet, 30, make_rng(SEED))
        c = draw_vehicle_params(fleet, 30, make_rng(SEED + 1))
        assert a == b
        assert a != c

    @pytest.mark.parametrize("seed", [7, 42, 4242])
    def test_truncation_and_hard_floors(self, seed, tmp_path):
        """±3σ per marginal AND the physical floors hold for every draw."""
        mean = dict(self.MEAN, a_max=0.25)  # 3σ below floor 0.2 → floor binds
        art = _write_calibration(tmp_path / "cal.json", mean, self.COV)
        draws = draw_vehicle_params(FleetSpec(idm_calibration=str(art)), 300, make_rng(seed))
        for p in draws:
            for i, key in enumerate(IDM_PARAM_ORDER):
                sigma = math.sqrt(self.COV[i][i])
                assert p[key] >= IDM_HARD_LOWER[key]
                assert p[key] <= mean[key] + 3.0 * sigma + 1e-12
                assert p[key] >= mean[key] - 3.0 * sigma - 1e-12 or p[key] >= IDM_HARD_LOWER[key]

    def test_missing_artifact_raises(self):
        fleet = FleetSpec(idm_calibration="does/not/exist.json")
        with pytest.raises(FileNotFoundError, match="IDMCalibration"):
            draw_vehicle_params(fleet, 1, make_rng(SEED))


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

    def test_single_lane_keeps_free_insertion(self, tmp_path):
        """lanes=1 (default) keeps the Phase-1 free/max scheme byte-for-byte."""
        plan = build_corridor_plan([(0.0, 0.5)], 60.0, FleetSpec(), AVSpec(), make_rng(SEED))
        path = write_corridor_routes(("entry", "ce0"), plan, "IDM", 0.5, tmp_path / "c3.rou.xml")
        for v in ET.parse(path).getroot().findall("vehicle"):
            assert v.get("departLane") == "free"
            assert v.get("departPos") == "free"
            assert v.get("departSpeed") == "max"

    def test_multi_lane_round_robin_base_avg(self, tmp_path):
        """M3 demand-realization fix: lanes>1 pins departLane round-robin in
        departure order with edge-start insertion at the prevailing speed
        (see write_corridor_routes docstring / docs/M2_RESULTS.md §7.7)."""
        plan = build_corridor_plan([(0.0, 2.4)], 60.0, FleetSpec(), AVSpec(), make_rng(SEED))
        path = write_corridor_routes(
            ("entry", "ce0"), plan, "IDM", 0.5, tmp_path / "c5.rou.xml", lanes=5
        )
        vehicles = ET.parse(path).getroot().findall("vehicle")
        assert len(vehicles) == plan.n
        for rank, v in enumerate(vehicles):
            assert v.get("departLane") == str(rank % 5)
            assert v.get("departPos") == "base"
            assert v.get("departSpeed") == "avg"
        # Departure order is time-sorted, so the round-robin covers all lanes.
        assert {v.get("departLane") for v in vehicles} == {"0", "1", "2", "3", "4"}


class TestRampPlans:
    """Ramp demand and routing (docs/CONTRACTS.md §2 RampSpec)."""

    CORRIDOR = ("e0", "e1", "e2", "e3")

    def _ramps(self):
        from flowstate_core.config import RampSpec

        return [
            RampSpec(kind="on", edges=["on_a"], attach_edge="e1", inflow=[(0.0, 0.25)], name="A"),
            RampSpec(kind="off", edges=["off_b"], attach_edge="e2", exit_fraction=[(0.0, 0.5)]),
            RampSpec(kind="off", edges=["off_c"], attach_edge="e0", exit_fraction=[(0.0, 0.2)]),
        ]

    def test_plan_without_ramps_is_unchanged(self):
        base = build_corridor_plan([(0.0, 0.5)], 120.0, FleetSpec(), AVSpec(), make_rng(SEED))
        again = build_corridor_plan(
            [(0.0, 0.5)],
            120.0,
            FleetSpec(),
            AVSpec(),
            make_rng(SEED),
            ramps=(),
            corridor_edges=self.CORRIDOR,
        )
        assert base == again and base.route == () and base.route_of(0) == "main"

    def test_on_ramp_adds_departures_and_routes(self):
        plan = build_corridor_plan(
            [(0.0, 0.5)],
            120.0,
            FleetSpec(),
            AVSpec(),
            make_rng(SEED),
            ramps=self._ramps(),
            corridor_edges=self.CORRIDOR,
        )
        n_main, n_on = 60, 30  # 120 s at 0.5 and 0.25 veh/s
        assert plan.n == n_main + n_on
        assert len(plan.route) == plan.n
        origins = [r.split("_")[0] for r in plan.route]
        assert origins[:n_main] == ["main"] * n_main and origins[n_main:] == ["on0"] * n_on
        # Off-ramp draws: mainline vehicles may leave at off2 (e0, 20%) or off1
        # (e2, 50%); on-ramp vehicles enter at e1 so they can only take off1.
        main_routes = plan.route[:n_main]
        on_routes = plan.route[n_main:]
        assert set(main_routes) <= {"main", "main_off2", "main_off1"}
        assert set(on_routes) <= {"on0", "on0_off1"}
        assert "main_off2" in main_routes and "main_off1" in main_routes
        frac_off2 = sum(r == "main_off2" for r in main_routes) / n_main
        assert 0.05 < frac_off2 < 0.45
        # Every vehicle still has params, tags and a departure time.
        assert len(plan.params) == len(plan.is_av) == len(plan.depart_s) == plan.n

    def test_ramp_plan_is_deterministic(self):
        a = build_corridor_plan(
            [(0.0, 0.5)],
            60.0,
            FleetSpec(),
            AVSpec(),
            make_rng(SEED),
            ramps=self._ramps(),
            corridor_edges=self.CORRIDOR,
        )
        b = build_corridor_plan(
            [(0.0, 0.5)],
            60.0,
            FleetSpec(),
            AVSpec(),
            make_rng(SEED),
            ramps=self._ramps(),
            corridor_edges=self.CORRIDOR,
        )
        assert a == b

    def test_unknown_attach_edge_raises(self):
        with pytest.raises(ValueError, match="not in corridor_edges"):
            build_corridor_plan(
                [(0.0, 0.5)],
                60.0,
                FleetSpec(),
                AVSpec(),
                make_rng(SEED),
                ramps=self._ramps(),
                corridor_edges=("x",),
            )

    def test_ramp_routes_edge_lists(self):
        from microsim.vehicles import ramp_routes

        routes = ramp_routes(self.CORRIDOR, self._ramps())
        assert routes["main"] == self.CORRIDOR
        assert routes["on0"] == ("on_a", "e1", "e2", "e3")
        assert routes["main_off1"] == ("e0", "e1", "e2", "off_b")
        assert routes["main_off2"] == ("e0", "off_c")
        assert routes["on0_off1"] == ("on_a", "e1", "e2", "off_b")
        assert "on0_off2" not in routes  # off-ramp upstream of the on-ramp

    def test_route_file_names_routes_and_ramp_insertion(self, tmp_path):
        from microsim.vehicles import ramp_routes

        ramps = self._ramps()
        plan = build_corridor_plan(
            [(0.0, 0.5)],
            40.0,
            FleetSpec(),
            AVSpec(),
            make_rng(SEED),
            ramps=ramps,
            corridor_edges=self.CORRIDOR,
        )
        path = write_corridor_routes(
            self.CORRIDOR,
            plan,
            "IDM",
            0.5,
            tmp_path / "r.rou.xml",
            lanes=3,
            routes=ramp_routes(self.CORRIDOR, ramps),
        )
        root = ET.parse(path).getroot()
        route_ids = {r.get("id") for r in root.findall("route")}
        assert {"main", "on0", "main_off1", "main_off2", "on0_off1"} <= route_ids
        vehicles = root.findall("vehicle")
        assert len(vehicles) == plan.n
        for v in vehicles:
            assert v.get("route") in route_ids
            if v.get("route").startswith("on"):
                assert v.get("departLane") == "free" and v.get("departPos") == "base"
            else:
                assert v.get("departLane") in {"0", "1", "2"}
        # Unknown route id in the plan is a hard error.
        bad = plan.__class__(**{**plan.__dict__, "route": ("nope",) * plan.n})
        with pytest.raises(ValueError, match="unknown route"):
            write_corridor_routes(self.CORRIDOR, bad, "IDM", 0.5, tmp_path / "bad.rou.xml")


class TestLcStrategic:
    """FleetSpec.lc_strategic → vType lcStrategic (docs/CONTRACTS.md §2)."""

    def test_default_keeps_route_file_unchanged(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 20.0, FleetSpec(), AVSpec(), make_rng(SEED))
        default = write_corridor_routes(("e0",), plan, "IDM", 0.5, tmp_path / "a.rou.xml")
        explicit = write_corridor_routes(
            ("e0",), plan, "IDM", 0.5, tmp_path / "b.rou.xml", lc_strategic=1.0
        )
        assert default.read_text() == explicit.read_text()
        assert "lcStrategic" not in default.read_text()

    def test_nondefault_written_on_every_vtype(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 20.0, FleetSpec(), AVSpec(), make_rng(SEED))
        path = write_corridor_routes(
            ("e0",), plan, "IDM", 0.5, tmp_path / "c.rou.xml", lc_strategic=5.0
        )
        root = ET.parse(path).getroot()
        vtypes = root.findall("vType")
        assert len(vtypes) == plan.n
        assert all(v.get("lcStrategic") == "5" for v in vtypes)

    def test_merge_parameters_written_only_when_nondefault(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 20.0, FleetSpec(), AVSpec(), make_rng(SEED))
        default = write_corridor_routes(("e0",), plan, "IDM", 0.5, tmp_path / "d.rou.xml")
        text = default.read_text()
        assert "lcCooperative" not in text and "lcAssertive" not in text
        assert "lcSpeedGain" not in text
        tuned = write_corridor_routes(
            ("e0",),
            plan,
            "IDM",
            0.5,
            tmp_path / "e.rou.xml",
            lc_cooperative=0.5,
            lc_assertive=2.0,
            lc_speed_gain=1.5,
        )
        vtypes = ET.parse(tuned).getroot().findall("vType")
        assert len(vtypes) == plan.n
        assert all(v.get("lcCooperative") == "0.5" for v in vtypes)
        assert all(v.get("lcAssertive") == "2" for v in vtypes)
        assert all(v.get("lcSpeedGain") == "1.5" for v in vtypes)

    def test_fleet_spec_field(self):
        assert FleetSpec().lc_strategic == 1.0
        assert FleetSpec().lc_cooperative == 1.0
        assert (
            FleetSpec(lc_cooperative=0.5, lc_assertive=2.0, lc_speed_gain=0.0).lc_assertive == 2.0
        )
        with pytest.raises(ValueError):
            FleetSpec(lc_cooperative=1.5)
        with pytest.raises(ValueError):
            FleetSpec(lc_assertive=0.0)
        assert FleetSpec(lc_strategic=5.0).lc_strategic == 5.0
        with pytest.raises(ValueError):
            FleetSpec(lc_strategic=-1.0)
        assert FleetSpec().lc_keep_right == 1.0
        with pytest.raises(ValueError):
            FleetSpec(lc_keep_right=-0.5)

    def test_keep_right_written_only_when_changed(self, tmp_path):
        plan = build_corridor_plan([(0.0, 0.5)], 20.0, FleetSpec(), AVSpec(), make_rng(SEED))
        default = write_corridor_routes(("e0",), plan, "IDM", 0.5, tmp_path / "d.rou.xml")
        assert "lcKeepRight" not in default.read_text()
        path = write_corridor_routes(
            ("e0",), plan, "IDM", 0.5, tmp_path / "k.rou.xml", lc_strategic=5.0, lc_keep_right=0.0
        )
        vtypes = ET.parse(path).getroot().findall("vType")
        assert all(v.get("lcKeepRight") == "0" and v.get("lcStrategic") == "5" for v in vtypes)
