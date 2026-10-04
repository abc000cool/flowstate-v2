"""calibration.transfer_check (WP-103): do the driver settings fit a corridor?

Synthetic corridors with a planted free-flow speed and capacity
(``synthetic_transfer.py``), the analytical IDM quantities against hand
computations and the committed equilibrium artifact, the verdict rules, the
recommendations (including "the population cannot match"), the simulated
capacity sidecars, missing truck data, and the outputs. No real detector data
is read; the only repository files read are small committed artifacts.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

from calibration.conservation import detector_grid
from calibration.idm_fit import equilibrium_gap
from calibration.transfer_check import (
    CANNOT_MATCH,
    GENERATED_EDGE_SPEED_MS,
    IDM_HARD_LOWER,
    IDM_PARAM_ORDER,
    MODEL_DRAW_SEED,
    PASSENGER_LENGTH_M,
    TRANSFER_SCHEMA,
    Adjustments,
    Comparison,
    Interval,
    ObservedSide,
    Population,
    active_windows,
    aggregate_windows,
    check_transfer,
    desired_speeds,
    draw_drivers,
    evaluate_sidecar,
    free_flow_speeds,
    heavy_share_from_classification,
    judge_absolute,
    judge_relative,
    mean_driver_capacity,
    model_side,
    model_speed_limit,
    observe,
    population_capacity,
    population_from_artifact,
    population_from_fleet,
    population_from_scenario,
    range_on_curve,
    solve_on_curve,
    uncertainty_range,
)
from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import FleetSpec, HeavyVehicleSpec
from flowstate_core.rng import make_rng

REPO_ROOT = Path(__file__).resolve().parents[2]
MPH = 0.44704
N_DRAWS = 2000
N_BOOT = 200


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


syn = _load(Path(__file__).with_name("synthetic_transfer.py"), "flowstate_wp103_syn")

#: The synthetic measured population of these tests (diagonal covariance).
POP_MEAN = {"v0": 33.0, "T": 1.4, "a_max": 1.0, "b": 1.7, "s0": 2.0}
POP_SD = {"v0": 3.0, "T": 0.3, "a_max": 0.3, "b": 0.5, "s0": 0.5}


def _write_population(path: Path, *, mean: dict[str, float] | None = None, sd=None) -> Path:
    m = dict(POP_MEAN if mean is None else mean)
    s = dict(POP_SD if sd is None else sd)
    IDMCalibration(
        created_at="2026-10-04T00:00:00Z",
        source="synthetic test population",
        data_hash="0" * 64,
        mean=m,
        cov=np.diag([s[k] ** 2 for k in IDM_PARAM_ORDER]).tolist(),
        n_episodes_fit=100,
        n_episodes_holdout=40,
        holdout_gap_rmse_m=4.0,
    ).save(path)
    return path


@pytest.fixture(scope="module")
def pop_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write_population(tmp_path_factory.mktemp("pop") / "idm_synthetic.json")


@pytest.fixture(scope="module")
def population(pop_path: Path) -> Population:
    return population_from_artifact(pop_path)


_OBSERVED: dict[tuple[Any, ...], ObservedSide] = {}


def observed_side(limit_ms: float | None = None, **planted: Any) -> ObservedSide:
    """Observed side of a planted synthetic corridor (cached per planting)."""
    key = (limit_ms, tuple(sorted(planted.items())))
    if key not in _OBSERVED:
        _OBSERVED[key] = observe(
            syn.corridor_frame(**planted),
            stations=syn.stations_table(limit_ms),
            n_bootstrap=N_BOOT,
        )
    return _OBSERVED[key]


def _homogeneous(model: str = "IDM", **heavy: Any) -> Population:
    fleet = FleetSpec(model=model, v0=30.0, T=1.5, s0=2.0, heterogeneity_frac=0.0, **heavy)
    return population_from_fleet(fleet, label="homogeneous")


# ---------------------------------------------------------------------------
# Observed side
# ---------------------------------------------------------------------------


class TestObserved:
    def test_planted_free_flow_speed_is_recovered(self) -> None:
        obs = observed_side(ff_speed=30.0, capacity=1800.0)
        ff = obs.free_flow
        assert ff.median_ms == pytest.approx(30.0, rel=0.01)
        assert ff.interval is not None and ff.interval.unit == "day"
        assert ff.interval.lo <= 30.05 and ff.interval.hi >= 29.95
        assert ff.n_stations == 4 and ff.n_days == 5
        assert 400.0 <= (ff.flow_median_veh_h_lane or 0.0) <= 900.0
        assert ff.p15_ms < ff.median_ms < ff.p85_ms
        assert obs.quality["applied"] is True
        assert obs.quality["summary"]["n_exclude"] == 0
        assert ff.relative_to_limit is None

    def test_planted_capacity_is_recovered_at_the_active_bottleneck(self) -> None:
        obs = observed_side(ff_speed=30.0, capacity=1800.0)
        cap = obs.capacity
        assert cap.basis == "bottleneck_discharge"
        assert cap.stations == ("S3",)
        assert cap.value_veh_h_lane == pytest.approx(1800.0, rel=0.02)
        assert cap.interval is not None
        assert cap.headway_s == pytest.approx(3600.0 / cap.value_veh_h_lane)
        cats = {s.station: s.category for s in obs.capacity_stations}
        assert cats == {
            "S1": "never_congested",
            "S2": "congested",
            "S3": "bottleneck_discharge",
            "S4": "never_congested",
        }
        assert [(b["upstream"], b["downstream"], b["n_days"]) for b in obs.bottlenecks] == [
            ("S2", "S3", 5)
        ]
        s3 = next(s for s in obs.capacity_stations if s.station == "S3")
        assert s3.discharge_flow_veh_h_lane == pytest.approx(1800.0, rel=0.02)
        s2 = next(s for s in obs.capacity_stations if s.station == "S2")
        assert s2.n_congested_days == 5 and s2.n_breakdowns == 5
        assert s2.pre_breakdown_flow_veh_h_lane is not None

    def test_a_road_that_never_breaks_down_gives_only_a_lower_bound(self) -> None:
        obs = observed_side(ff_speed=30.0, capacity=1800.0, bottleneck=False)
        cap = obs.capacity
        assert cap.basis == "lower_bound"
        values = [s.capacity_veh_h_lane for s in obs.capacity_stations]
        assert cap.value_veh_h_lane == pytest.approx(max(values))
        assert all(s.category == "never_congested" for s in obs.capacity_stations)
        assert any("lower bound" in n for n in obs.notes)

    def test_per_lane_input_is_summed_to_stations(self) -> None:
        frame = syn.corridor_frame(
            ff_speed=30.0, capacity=1800.0, per_lane=True, dates=syn.DATES[:3]
        )
        obs = observe(frame, stations=syn.stations_table(), n_bootstrap=N_BOOT)
        assert obs.per_lane is True
        assert obs.median_lanes == 3
        assert obs.free_flow.median_ms == pytest.approx(30.0, rel=0.01)
        assert obs.capacity.value_veh_h_lane == pytest.approx(1800.0, rel=0.02)

    def test_posted_limit_comes_from_the_stations_table(self) -> None:
        obs = observed_side(26.8224, ff_speed=30.0, capacity=1800.0)
        assert obs.speed_limit_ms == pytest.approx(26.8224)
        assert obs.speed_limit_source.startswith("stations table")
        assert obs.free_flow.relative_to_limit == pytest.approx(30.0 / 26.8224, rel=0.01)

    def test_finer_windows_are_averaged_to_five_minutes(self) -> None:
        frame = syn.corridor_frame(dates=syn.DATES[:1])
        frame = frame[frame["station"] == "S1"].reset_index(drop=True)
        fine = frame.loc[frame.index.repeat(5)].reset_index(drop=True)
        fine["timestamp"] = [
            t + timedelta(seconds=60 * (i % 5)) for i, t in enumerate(fine["timestamp"])
        ]
        fine.attrs["interval_s"] = 60.0
        grid = detector_grid(fine)
        coarse, note = aggregate_windows(grid, 300.0)
        assert note is None and coarse.interval_s == 300.0
        assert coarse.n_windows == len(frame)
        np.testing.assert_allclose(coarse.flow_veh_h["S1"][0], frame["flow_veh_h"].to_numpy())
        np.testing.assert_allclose(coarse.speed_ms["S1"][0], frame["speed_ms"].to_numpy())
        # a missing minute leaves its 5-minute window empty, never a partial mean
        holed = grid.flow_veh_h["S1"].copy()
        holed[0, 2] = np.nan
        coarse2, _ = aggregate_windows(
            grid.replace_values({"S1": holed}, grid.occupancy_pct, grid.speed_ms), 300.0
        )
        assert np.isnan(coarse2.flow_veh_h["S1"][0, 0])
        assert np.isfinite(coarse2.flow_veh_h["S1"][0, 1])

    def test_active_bottleneck_needs_five_of_seven_windows(self) -> None:
        five = np.array([[1, 1, 0, 1, 1, 1, 0, 0, 0, 0]], dtype=bool)
        four = np.array([[1, 1, 0, 1, 0, 1, 0, 0, 0, 0]], dtype=bool)
        act = active_windows(five, (5, 7))
        assert act.tolist() == [[True, True, False, True, True, True, False, False, False, False]]
        assert not active_windows(four, (5, 7)).any()
        assert not active_windows(five[:, :6], (5, 7)).any()

    def test_truck_share_from_classification_counts(self) -> None:
        heavy = heavy_share_from_classification(
            syn.classification_frame(0.12),
            column_map={
                "station": "Site",
                "date": "Day",
                "heavy_count": "Trucks",
                "total_count": "Volume",
            },
            definition="FHWA classes 5-13",
            n_bootstrap=N_BOOT,
        )
        assert heavy.available and heavy.share == pytest.approx(0.12, abs=1e-12)
        assert heavy.n_days == 5 and heavy.interval is not None
        assert heavy.interval.unit == "day"
        assert heavy.definition == "FHWA classes 5-13"
        only_two = heavy_share_from_classification(
            syn.classification_frame(0.12),
            column_map={
                "station": "Site",
                "date": "Day",
                "heavy_count": "Trucks",
                "total_count": "Volume",
            },
            dates=syn.DATES[:2],
            n_bootstrap=N_BOOT,
        )
        assert only_two.n_days == 2 and only_two.interval is None  # < 3 days: no interval

    def test_truck_share_refuses_percent_and_impossible_counts(self) -> None:
        frame = syn.classification_frame(0.12).rename(columns={"Trucks": "heavy_share"})
        frame["heavy_share"] = 12.0
        with pytest.raises(ValueError, match="fraction"):
            heavy_share_from_classification(frame)
        bad = syn.classification_frame(0.12)
        bad["Trucks"] = bad["Volume"] + 1
        with pytest.raises(ValueError, match="above its total"):
            heavy_share_from_classification(
                bad, column_map={"heavy_count": "Trucks", "total_count": "Volume"}
            )
        with pytest.raises(ValueError, match="heavy_count"):
            heavy_share_from_classification(syn.classification_frame(0.12))


# ---------------------------------------------------------------------------
# Model side
# ---------------------------------------------------------------------------


class TestModelSide:
    def test_draws_and_constants_match_the_runner(self) -> None:
        from microsim import networks, vehicles

        cal = IDMCalibration.load(REPO_ROOT / "artifacts" / "idm_i24.json")
        runner = vehicles._draw_from_calibration(cal, 400, make_rng(5))
        mine = draw_drivers(population_from_artifact("artifacts/idm_i24.json"), n=400, seed=5)
        np.testing.assert_array_equal([p["v0"] for p in runner], mine.v0)
        np.testing.assert_array_equal([p["T"] for p in runner], mine.T)
        np.testing.assert_array_equal([p["s0"] for p in runner], mine.s0)
        assert dict(vehicles.IDM_HARD_LOWER) == IDM_HARD_LOWER
        assert tuple(vehicles.IDM_PARAM_ORDER) == IDM_PARAM_ORDER
        assert vehicles.VEHICLE_LENGTH_M == PASSENGER_LENGTH_M
        # a generated straight road caps no driver: the sidecars' roads are uncapped
        assert networks.EDGE_SPEED_LIMIT_MS == GENERATED_EDGE_SPEED_MS
        assert networks.EDGE_SPEED_LIMIT_MS > float(mine.v0.max())

    def test_homogeneous_idm_capacity_matches_the_hand_computation(self) -> None:
        # q(v) = v/((2 + 1.5 v)/sqrt(1 - (v/30)^4) + 5), maximised independently
        # (scipy bounded minimisation): 1,798.13 veh/h at 17.194 m/s
        pop = _homogeneous()
        d = draw_drivers(pop, n=50)
        assert np.ptp(d.v0) == 0.0 and np.ptp(d.T) == 0.0
        q, v, limited = population_capacity("IDM", d, desired_speeds(d, None, 1.0))
        assert q == pytest.approx(1798.13, abs=0.05)
        assert v == pytest.approx(17.194, abs=0.01)
        assert not limited
        q_mean, v_mean = mean_driver_capacity("IDM", pop.passenger_means(), None)
        assert q_mean == pytest.approx(q, abs=0.05) and v_mean == pytest.approx(v, abs=0.01)
        gap = equilibrium_gap(v, {"v0": 30.0, "T": 1.5, "s0": 2.0})
        assert 3600.0 * v / (gap + 5.0) == pytest.approx(q, abs=0.05)

    def test_eidm_capacity_is_the_improved_idm_closed_form(self) -> None:
        pop = _homogeneous("EIDM")
        d = draw_drivers(pop, n=50)
        q, v, limited = population_capacity("EIDM", d, desired_speeds(d, None, 1.0))
        expected = 0.999 * 30.0 / (2.0 + 0.999 * 30.0 * 1.5 + 5.0) * 3600.0
        assert q == pytest.approx(expected, rel=1e-9) and q == pytest.approx(2076.64, abs=0.01)
        assert limited and v == pytest.approx(0.999 * 30.0)

    def test_free_flow_speed_is_the_idm_equilibrium(self) -> None:
        # q(v) = 1000/3600 on the free branch: 28.278 m/s (brentq, independently)
        pop = _homogeneous()
        d = draw_drivers(pop, n=10)
        speeds, beyond = free_flow_speeds("IDM", d, desired_speeds(d, None, 1.0), 1000.0 / 3600.0)
        assert beyond == 0
        np.testing.assert_allclose(speeds, 28.2783, atol=1e-3)
        capped, _ = free_flow_speeds("IDM", d, desired_speeds(d, 25.0, 1.0), 1000.0 / 3600.0)
        assert float(capped.max()) < 25.0
        # beyond a driver's own capacity it is put at its capacity speed and counted
        _, beyond = free_flow_speeds("IDM", d, desired_speeds(d, None, 1.0), 1900.0 / 3600.0)
        assert beyond == 10
        eidm, _ = free_flow_speeds("EIDM", d, desired_speeds(d, None, 1.0), 1000.0 / 3600.0)
        np.testing.assert_allclose(eidm, 30.0)

    def test_reproduces_the_committed_equilibrium_artifact(self) -> None:
        record = json.loads(
            (REPO_ROOT / "artifacts" / "idm_i24_capacity_equilibrium.json").read_text()
        )
        rows = {r["artifact"]: r for r in record["populations"]}
        for artifact in ("artifacts/idm_i24.json", "artifacts/idm_i24_capacity.json"):
            row = rows[artifact]
            pop = population_from_artifact(artifact)
            q, v = mean_driver_capacity("IDM", pop.passenger_means(), None)
            assert q == pytest.approx(row["equilibrium_capacity_veh_h_lane"], abs=0.06)
            assert v == pytest.approx(row["v_at_capacity_ms"], abs=0.002)
            het = row["heterogeneous"]
            d = draw_drivers(pop, n=het["n_draws"], seed=het["seed"])
            qp, vp, limited = population_capacity("IDM", d, desired_speeds(d, None, 1.0))
            assert qp == pytest.approx(het["capacity_veh_h_lane"], abs=0.06)
            assert vp == pytest.approx(het["v_at_capacity_ms"], abs=0.002)
            assert limited == het["limited_by_min_v0"]

    def test_a_speed_limit_caps_desired_speed_and_lowers_idm_capacity(self, population) -> None:
        d = draw_drivers(population, n=N_DRAWS)
        v_des = desired_speeds(d, 26.8224, 1.0)
        assert float(v_des.max()) == pytest.approx(26.8224)
        assert np.all(desired_speeds(d, 26.8224, 1.2) >= v_des)
        free = population_capacity("IDM", d, desired_speeds(d, None, 1.0))[0]
        capped = population_capacity("IDM", d, v_des)[0]
        assert capped < free

    def test_trucks_lower_capacity(self) -> None:
        heavy = HeavyVehicleSpec(
            fraction=0.2,
            length_m=18.0,
            emission_class="HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
            v0=27.0,
            T=2.0,
            a_max=0.6,
            b=1.5,
            s0=3.0,
            heterogeneity_frac=0.0,
        )
        mixed = _homogeneous(heavy=heavy)
        d = draw_drivers(mixed, n=4000)
        assert d.heavy.mean() == pytest.approx(0.2, abs=0.02)
        assert set(np.unique(d.length)) == {5.0, 18.0}
        q_mixed = population_capacity("IDM", d, desired_speeds(d, None, 1.0))[0]
        d0 = draw_drivers(mixed, Adjustments(heavy_fraction=0.0), n=4000)
        assert not d0.heavy.any()
        assert q_mixed < population_capacity("IDM", d0, desired_speeds(d0, None, 1.0))[0]

    def test_population_from_scenario_reads_only_the_fleet_block(self, tmp_path, pop_path) -> None:
        scenario = tmp_path / "s.yaml"
        scenario.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "network": {"kind": "osm", "osm_file": "nowhere.osm"},
                    "fleet": {"model": "EIDM", "idm_calibration": str(pop_path)},
                }
            )
        )
        pop = population_from_scenario(scenario)
        assert pop.model == "EIDM" and pop.network_kind == "osm"
        assert pop.calibration is not None and pop.heavy_fraction == 0.0
        assert len(pop.sources["scenario_sha256"]) == 64
        assert len(pop.sources["idm_calibration_sha256"]) == 64
        # a map-imported road carries the posted limit; a generated one caps nobody
        assert model_speed_limit(pop, 26.8) == 26.8
        assert model_speed_limit(replace(pop, network_kind="corridor"), 26.8) is None
        assert model_speed_limit(replace(pop, network_kind=None), 26.8) == 26.8


# ---------------------------------------------------------------------------
# Verdict rules
# ---------------------------------------------------------------------------


def _interval(lo: float, hi: float) -> Interval:
    return Interval(lo=lo, hi=hi, level=0.95, n_resamples=100, unit="day", n_units=5, seed=0)


class TestVerdicts:
    def test_relative_rule(self) -> None:
        iv = _interval(95.0, 105.0)
        assert judge_relative(100.0, iv, 103.0, 0.05)[0] == "ok"
        assert judge_relative(100.0, iv, 108.0, 0.05)[0] == "inconclusive"
        assert judge_relative(100.0, iv, 112.0, 0.05)[0] == "mismatch"
        assert judge_relative(100.0, iv, 89.0, 0.05)[0] == "mismatch"
        assert judge_relative(100.0, None, 106.0, 0.05)[0] == "mismatch"
        assert judge_relative(None, iv, 100.0, 0.05)[0] == "not_available"
        assert judge_relative(100.0, iv, None, 0.05)[0] == "not_available"
        assert "below" in judge_relative(100.0, iv, 89.0, 0.05)[1]

    def test_lower_bound_rule(self) -> None:
        iv = _interval(1600.0, 1700.0)
        assert judge_relative(1650.0, iv, 1500.0, 0.05, lower_bound=True)[0] == "mismatch"
        assert judge_relative(1650.0, iv, 1560.0, 0.05, lower_bound=True)[0] == "inconclusive"
        assert judge_relative(1650.0, iv, 2400.0, 0.05, lower_bound=True)[0] == "inconclusive"

    def test_absolute_rule(self) -> None:
        iv = _interval(0.11, 0.13)
        assert judge_absolute(0.12, iv, 0.10, 0.03)[0] == "ok"
        assert judge_absolute(0.12, iv, 0.0, 0.03)[0] == "mismatch"
        assert judge_absolute(0.12, _interval(0.05, 0.20), 0.08, 0.03)[0] == "inconclusive"
        assert judge_absolute(None, None, 0.0, 0.03)[0] == "not_available"

    def test_solve_on_curve_takes_the_nearest_crossing(self) -> None:
        xs, ys = [0.8, 0.9, 1.0, 1.1, 1.2], [5.0, 4.0, 3.0, 2.0, 1.0]
        assert solve_on_curve(xs, ys, 2.5, 1.0) == pytest.approx(1.05)
        assert solve_on_curve(xs, ys, 3.0, 1.0) == pytest.approx(1.0)
        assert solve_on_curve(xs, ys, 6.0, 1.0) is None
        assert solve_on_curve([0, 1, 2, 3], [0.0, 2.0, 0.0, 2.0], 1.0, 2.6) == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    def test_settings_that_fit_need_no_change(self, population) -> None:
        # the synthetic population carries ~1,918 veh/h/lane and ~32.2 m/s at
        # the light-traffic flow: plant the corridor there
        report = check_transfer(
            observed_side(ff_speed=32.2, capacity=1900.0), population, sidecars=[], n_draws=N_DRAWS
        )
        assert report.comparison("free_flow_speed").verdict == "ok"
        assert report.comparison("capacity_per_lane").verdict == "ok"
        assert report.recommendation("free_flow_speed").action == "none"
        assert report.recommendation("capacity_per_lane").action == "none"
        assert report.model.capacity_basis == "analytical"

    def test_a_mismatch_inside_the_measured_ranges_is_adjusted(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1700.0), population, sidecars=[], n_draws=N_DRAWS
        )
        ff = report.comparison("free_flow_speed")
        assert ff.verdict == "mismatch" and ff.difference is not None and ff.difference < -0.05
        rec = report.recommendation("free_flow_speed")
        assert rec.action == "adjust" and rec.chosen == "v0_scale"
        (knob,) = rec.knobs
        assert knob.fits and knob.range_lo == pytest.approx(30.0 / 33.0)
        assert knob.range_hi == pytest.approx(36.0 / 33.0)
        assert 0.91 < (knob.needed or 0.0) < 0.96
        cap = report.recommendation("capacity_per_lane")
        assert report.comparison("capacity_per_lane").verdict == "mismatch"
        assert cap.action == "adjust" and cap.chosen == "t_scale"
        (t_knob,) = cap.knobs
        assert t_knob.fits and 1.0 < (t_knob.needed or 0.0) < 1.2
        assert t_knob.range_lo == pytest.approx(1.1 / 1.4) and t_knob.range_hi == pytest.approx(
            1.7 / 1.4
        )
        assert any("single corridor-wide adjustment" in n for n in report.notes)

    def test_a_corridor_outside_the_measured_ranges_cannot_be_matched(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=24.0, capacity=1300.0), population, sidecars=[], n_draws=N_DRAWS
        )
        ff = report.recommendation("free_flow_speed")
        assert ff.action == "cannot_match" and ff.chosen is None
        assert CANNOT_MATCH in ff.text
        (knob,) = ff.knobs
        assert not knob.fits and knob.needed is None
        cap = report.recommendation("capacity_per_lane")
        assert cap.action == "cannot_match" and CANNOT_MATCH in cap.text
        assert "never reaches" in cap.knobs[0].note
        assert CANNOT_MATCH in report.to_markdown()

    def test_drivers_faster_than_the_limit_need_a_speed_factor(self, population) -> None:
        # posted 60 mph: every model driver is capped there (~26.1 m/s at the
        # light-traffic flow); the corridor's drivers do 30 m/s
        report = check_transfer(
            observed_side(26.8224, ff_speed=30.0, capacity=1700.0),
            population,
            sidecars=[],
            n_draws=N_DRAWS,
        )
        assert report.model.speed_limit_ms == pytest.approx(26.8224)
        assert report.comparison("free_flow_speed").verdict == "mismatch"
        rec = report.recommendation("free_flow_speed")
        knobs = {k.name: k for k in rec.knobs}
        assert not knobs["v0_scale"].fits
        sf = knobs["speed_factor"]
        assert sf.fits and not sf.available and 1.1 < (sf.needed or 0.0) < 1.3
        assert rec.action == "needs_engine_change" and rec.chosen == "speed_factor"
        assert "speedFactor" in sf.how

    def test_missing_truck_data_is_reported_not_guessed(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1800.0), population, sidecars=[], n_draws=N_DRAWS
        )
        c = report.comparison("truck_share")
        assert c.verdict == "not_available" and c.observed is None
        assert report.recommendation("truck_share").action == "no_data"
        assert "not available in these data" in report.to_markdown()

    def test_a_measured_truck_share_is_recommended(self, population) -> None:
        base = observed_side(ff_speed=32.2, capacity=1900.0)
        heavy = heavy_share_from_classification(
            syn.classification_frame(0.12),
            column_map={"date": "Day", "heavy_count": "Trucks", "total_count": "Volume"},
            n_bootstrap=N_BOOT,
        )
        obs = replace(base, heavy=heavy)
        report = check_transfer(obs, population, sidecars=[], n_draws=N_DRAWS)
        assert report.comparison("truck_share").verdict == "mismatch"
        rec = report.recommendation("truck_share")
        assert rec.action == "adjust" and rec.chosen == "heavy_fraction"
        assert rec.knobs[0].needed == pytest.approx(0.12)
        assert "artifacts/idm_i24_heavy.json" in rec.knobs[0].how  # no heavy block yet
        assert "12.0%" in report.to_markdown()


# ---------------------------------------------------------------------------
# Simulated capacity sidecars
# ---------------------------------------------------------------------------

GRID = [(1.0, 1650.0), (0.95, 1700.0), (0.9, 1750.0), (0.85, 1800.0), (0.8, 1850.0)]


def _sidecar(tmp: Path, source: Path, *, model: str | None = "IDM", name: str = "cap") -> Path:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": str(source),
        "corridor": {"lanes": 3},
        "table": [
            {"T_scale": f, "T_mean_s": 1.4 * f, "capacity_veh_h_lane": c, "n": 2} for f, c in GRID
        ],
        "runs": [
            {"T_scale": f, "seed": 1, "throughput_veh_h_lane": c, "mean_speed_ms_at_ref": 16.0}
            for f, c in GRID
        ],
    }
    if model is not None:
        base = tmp / f"{name}_base.yaml"
        base.write_text(yaml.safe_dump({"fleet": {"model": model}}))
        payload["base_scenario"] = str(base)
    path = tmp / f"{name}.calibration.json"
    path.write_text(json.dumps(payload))
    return path


def _derived(tmp: Path, source: Path, t_scale: float, name: str) -> Path:
    cal = IDMCalibration.load(source)
    mean = dict(cal.mean)
    mean["T"] *= t_scale
    out = tmp / name
    cal.model_copy(update={"mean": mean}).save(out)
    return out


class TestSidecars:
    def test_a_simulated_capacity_is_preferred(self, tmp_path, pop_path, population) -> None:
        sc = _sidecar(tmp_path, pop_path)
        side, _, chosen = model_side(
            population,
            speed_limit_ms=None,
            free_flow_flow_veh_h_lane=700.0,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        assert chosen is not None and side.capacity_basis == "simulated"
        sim = side.capacity_simulated
        assert sim is not None and sim.raw_veh_h_lane == pytest.approx(1650.0)
        assert sim.conditions_ratio == 1.0 and side.capacity_veh_h_lane == pytest.approx(1650.0)
        assert side.capacity_speed_ms == pytest.approx(16.0)
        assert side.capacity_population[0] > 1650.0  # the analytical index, reported beside it

    def test_a_t_scaled_population_reads_its_point_on_the_grid(self, tmp_path, pop_path) -> None:
        sc = _sidecar(tmp_path, pop_path)
        derived = population_from_artifact(_derived(tmp_path, pop_path, 0.925, "d.json"))
        cand, _ = evaluate_sidecar(sc, derived, explicit=False)
        assert cand.accepted and cand.t_scale_current == pytest.approx(0.925)
        side, _, _ = model_side(
            derived,
            speed_limit_ms=None,
            free_flow_flow_veh_h_lane=700.0,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        assert side.capacity_veh_h_lane == pytest.approx(1725.0)

    def test_this_corridors_limit_is_carried_by_the_analytical_ratio(
        self, tmp_path, pop_path, population
    ) -> None:
        sc = _sidecar(tmp_path, pop_path)
        side, _, _ = model_side(
            population,
            speed_limit_ms=55 * MPH,
            free_flow_flow_veh_h_lane=700.0,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        sim = side.capacity_simulated
        assert sim is not None and 0.85 < sim.conditions_ratio < 1.0
        assert sim.value_veh_h_lane == pytest.approx(1650.0 * sim.conditions_ratio)

    def test_a_sidecar_run_under_another_model_is_not_used(self, tmp_path, pop_path, population):
        sc = _sidecar(tmp_path, pop_path, model="EIDM")
        cand, data = evaluate_sidecar(sc, population, explicit=True)
        assert not cand.accepted and data is None and "EIDM" in cand.reason

    def test_an_unrecorded_model_needs_an_explicit_choice(self, tmp_path, pop_path, population):
        sc = _sidecar(tmp_path, pop_path, model=None)
        assert not evaluate_sidecar(sc, population, explicit=False)[0].accepted
        cand, _ = evaluate_sidecar(sc, population, explicit=True)
        assert cand.accepted and "named explicitly" in cand.reason

    def test_another_population_or_a_scale_off_the_grid_is_not_used(self, tmp_path, pop_path):
        sc = _sidecar(tmp_path, pop_path)
        other = _write_population(tmp_path / "other.json", mean={**POP_MEAN, "v0": 31.0})
        cand, _ = evaluate_sidecar(sc, population_from_artifact(other), explicit=True)
        assert not cand.accepted and "different population" in cand.reason
        far = population_from_artifact(_derived(tmp_path, pop_path, 0.7, "far.json"))
        cand, _ = evaluate_sidecar(sc, far, explicit=True)
        assert not cand.accepted and "outside the sidecar's grid" in cand.reason

    def test_capacity_recommendation_reads_the_simulated_grid(self, tmp_path, pop_path, population):
        sc = _sidecar(tmp_path, pop_path)
        report = check_transfer(
            observed_side(ff_speed=32.2, capacity=1780.0),
            population,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        assert report.model.capacity_basis == "simulated"
        rec = report.recommendation("capacity_per_lane")
        assert rec.action == "adjust"
        target = report.comparison("capacity_per_lane").observed
        expected = 1.0 - (target - 1650.0) / 1000.0  # the grid is linear: 50 per 0.05
        assert rec.knobs[0].needed == pytest.approx(expected, abs=1e-6)
        # above everything the grid carried: no extrapolation, plain words
        report = check_transfer(
            observed_side(ff_speed=32.2, capacity=1950.0),
            population,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        rec = report.recommendation("capacity_per_lane")
        assert rec.action == "cannot_match" and "never reaches" in rec.knobs[0].note

    def test_an_eidm_fleet_without_a_simulated_capacity_has_no_model_value(self, pop_path) -> None:
        eidm = population_from_artifact(pop_path, model="EIDM")
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1800.0), eidm, sidecars=[], n_draws=N_DRAWS
        )
        assert report.model.capacity_basis == "none"
        assert report.comparison("capacity_per_lane").verdict == "not_available"
        rec = report.recommendation("capacity_per_lane")
        assert rec.action == "no_data" and "calibrate_capacity" in rec.text


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


class TestOutputs:
    def test_json_is_stable_and_complete(self, population) -> None:
        obs = observed_side(ff_speed=30.0, capacity=1700.0)
        a = check_transfer(obs, population, sidecars=[], n_draws=N_DRAWS, provenance={"k": "v"})
        b = check_transfer(obs, population, sidecars=[], n_draws=N_DRAWS, provenance={"k": "v"})
        assert a.to_json() == b.to_json()
        payload = json.loads(a.to_json())
        assert payload["schema"] == TRANSFER_SCHEMA
        assert set(payload) == {
            "schema",
            "rules",
            "rule_sources",
            "observed",
            "model",
            "comparisons",
            "recommendations",
            "implied_headway",
            "provenance",
            "notes",
        }
        assert set(payload["rules"]) == set(payload["rule_sources"])
        assert [c["quantity"] for c in payload["comparisons"]] == [
            "truck_share",
            "free_flow_speed",
            "capacity_per_lane",
        ]
        assert payload["model"]["draw_seed"] == MODEL_DRAW_SEED
        assert len(payload["model"]["population_sources"]["idm_calibration_sha256"]) == 64
        assert payload["provenance"] == {"k": "v"}
        hw = payload["implied_headway"]
        assert hw["observed_gross_s"] == pytest.approx(
            3600.0 / obs.capacity.value_veh_h_lane, rel=1e-3
        )
        assert hw["observed_net_s"] < hw["observed_gross_s"]

    def test_markdown_speaks_plainly(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1700.0), population, sidecars=[], n_draws=N_DRAWS
        )
        text = report.to_markdown()
        assert "drivers here drive about 7% slower" in text
        assert "Capacity per lane:** the road carried about" in text
        assert "## Recommendations" in text and "a reviewer decides" in text
        assert "| capacity per lane |" in text
        assert not any(
            math.isnan(x) for x in [report.comparison("capacity_per_lane").observed or 0.0]
        )


# ---------------------------------------------------------------------------
# Ranges for the uncertainty runs (WP-106b)
# ---------------------------------------------------------------------------

#: A synthetic monotone curve: capacity falls by 50 veh/h/lane per 0.05 of the T factor.
CURVE_XS = [float(x) for x in np.linspace(0.8, 1.2, 9)]
CURVE_YS = [3000.0 - 1000.0 * x for x in CURVE_XS]


def _cap_comparison(
    interval: tuple[float, float] | None = (1930.0, 1960.0),
    *,
    observed: float | None = 1945.0,
    model: float | None = 2000.0,
    verdict: str = "mismatch",
) -> Comparison:
    return Comparison(
        quantity="capacity_per_lane",
        unit="veh/h/lane",
        observed=observed,
        observed_interval=None if interval is None else _interval(*interval),
        model=model,
        model_basis="synthetic",
        difference=None,
        tolerance=0.05,
        rule="synthetic",
        verdict=verdict,  # type: ignore[arg-type]
        explanation="synthetic explanation",
    )


def _range(c: Comparison, **kw: Any):
    def curve() -> tuple[list[float], list[float]]:
        return CURVE_XS, CURVE_YS

    args: dict[str, Any] = {
        "knob": "t_scale",
        "parameter": "T",
        "reference_mean": 1.4,
        "comparison": c,
        "measured": (0.8, 1.2),
        "curve": curve,
        "curve_kind": "analytical",
    }
    args.update(kw)
    return uncertainty_range(**args)


def _never_called() -> tuple[list[float], list[float]]:
    raise AssertionError("the curve is read only when there is an interval to read")


class TestUncertaintyRange:
    def test_crossings_on_a_monotone_curve_are_solve_on_curves(self) -> None:
        lo, hi = range_on_curve(CURVE_XS, CURVE_YS, 1930.0, 1960.0) or (0.0, 0.0)
        assert (lo, hi) == (pytest.approx(1.04), pytest.approx(1.07))
        assert lo == pytest.approx(solve_on_curve(CURVE_XS, CURVE_YS, 1960.0, 1.0))
        assert hi == pytest.approx(solve_on_curve(CURVE_XS, CURVE_YS, 1930.0, 1.0))
        rising = [20.0 + 10.0 * x for x in CURVE_XS]  # free-flow speed rises with v0
        assert range_on_curve(CURVE_XS, rising, 30.5, 29.5) == (
            pytest.approx(0.95),
            pytest.approx(1.05),
        )
        assert range_on_curve(CURVE_XS, CURVE_YS, 1000.0, 3000.0) == (0.8, 1.2)
        assert range_on_curve(CURVE_XS, CURVE_YS, 2300.0, 2400.0) is None

    def test_a_curve_that_leaves_the_band_gives_the_hull_and_nan_breaks_it(self) -> None:
        assert range_on_curve([0, 1, 2, 3, 4], [0.0, 2.0, 0.0, 2.0, 0.0], 1.5, 2.5) == (
            pytest.approx(0.75),
            pytest.approx(3.25),
        )
        holed = [0.0, 2.0, float("nan"), 2.0, 0.0]
        assert range_on_curve([0, 1, 2, 3, 4], holed, 1.5, 2.5) == (
            pytest.approx(0.75),
            pytest.approx(3.25),
        )
        assert range_on_curve([0, 1, 2], [0.0, float("nan"), 4.0], 1.0, 3.0) is None

    def test_the_range_is_read_off_the_curve_at_the_intervals_ends(self) -> None:
        u = _range(_cap_comparison())
        assert u.basis == "observed_interval" and not u.clipped
        assert (u.low, u.high) == (pytest.approx(1.04), pytest.approx(1.07))
        assert u.observed_interval == (1930.0, 1960.0) and u.curve == "analytical"
        assert "1930–1960 veh/h/lane" in u.reason and "analytical curve" in u.reason
        d = u.to_dict()
        assert d["parameter_low"] == pytest.approx(1.04 * 1.4, abs=1e-4)
        assert d["parameter_high"] == pytest.approx(1.07 * 1.4, abs=1e-4)
        assert d["measured_range"] == [0.8, 1.2] and d["clipped"] is False
        # the point estimate is inside what is read, as the verdict rule widens it
        u2 = _range(_cap_comparison(observed=1925.0))
        assert u2.observed_interval == (1925.0, 1960.0)
        assert u2.high == pytest.approx(1.075)

    def test_an_interval_beyond_the_measured_range_is_clipped(self) -> None:
        # capacity 2050–2400 needs T x 0.6–0.95; the measured range stops at 0.8
        u = _range(_cap_comparison((2050.0, 2400.0), observed=2100.0))
        assert u.basis == "observed_interval" and u.clipped
        assert (u.low, u.high) == (pytest.approx(0.8), pytest.approx(0.95))
        assert "clipped at the low end" in u.reason
        # a crossing exactly at the range's end is not a clip
        exact = _range(_cap_comparison((2000.0, 2200.0), observed=2100.0))
        assert (exact.low, exact.high) == (pytest.approx(0.8), pytest.approx(1.0))
        assert not exact.clipped
        # a simulated grid is never extrapolated: the span cuts the range at both ends
        grid = _range(
            _cap_comparison((1850.0, 2150.0), observed=2000.0),
            span=(0.9, 1.1),
            span_text="the measured range ∩ the simulated grid",
            curve_kind="simulated",
        )
        assert (grid.low, grid.high) == (pytest.approx(0.9), pytest.approx(1.1))
        assert grid.clipped and "low and high ends" in grid.reason and grid.curve == "simulated"

    @pytest.mark.parametrize(
        ("comparison", "kw", "words"),
        [
            (
                _cap_comparison(None, observed=None, verdict="not_available"),
                {},
                "was not observed",
            ),
            (_cap_comparison(), {"lower_bound": True}, "only a lower bound"),
            (_cap_comparison(model=None, verdict="not_available"), {}, "no capacity per lane"),
            (_cap_comparison(), {"curve": None}, "no capacity per lane value"),
            (_cap_comparison(None), {}, "no 95 % interval (fewer than 3 days"),
            (_cap_comparison(verdict="inconclusive"), {}, "the check is inconclusive"),
        ],
    )
    def test_without_an_interval_to_read_the_measured_range_is_used(
        self, comparison: Comparison, kw: dict[str, Any], words: str
    ) -> None:
        args = {"curve": _never_called, **kw}
        u = _range(comparison, **args)
        assert u.basis == "measured_range_fallback" and words in u.reason
        assert (u.low, u.high) == (0.8, 1.2) and not u.clipped
        assert u.observed_interval is None and u.curve is None

    def test_a_curve_that_never_enters_the_interval_falls_back(self) -> None:
        u = _range(_cap_comparison((1000.0, 1100.0), observed=1050.0))
        assert u.basis == "measured_range_fallback"
        assert "no value of the mean time headway (T)" in u.reason
        assert "spans 1800–2200 veh/h/lane" in u.reason

    def test_the_check_reads_its_own_curves(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=32.2, capacity=1900.0), population, sidecars=[], n_draws=N_DRAWS
        )
        assert report.comparison("truck_share").uncertainty_range is None
        ff = report.comparison("free_flow_speed")
        u = ff.uncertainty_range
        assert u is not None and u.basis == "observed_interval" and u.knob == "v0_scale"
        assert u.reference_mean == pytest.approx(33.0)
        assert u.measured_range == (pytest.approx(30.0 / 33.0), pytest.approx(36.0 / 33.0))
        # the model's free-flow speed at the ends is the interval's (it rises with v0)
        q_ff = (report.model.free_flow_flow_veh_h_lane or 0.0) / 3600.0
        for factor, edge in ((u.low, u.observed_interval[0]), (u.high, u.observed_interval[1])):
            d = draw_drivers(population, Adjustments(v0_scale=factor), n=N_DRAWS)
            speeds, _ = free_flow_speeds("IDM", d, desired_speeds(d, None, 1.0), q_ff)
            assert float(speeds.mean()) == pytest.approx(edge, abs=0.01)
        cap = report.comparison("capacity_per_lane").uncertainty_range
        assert cap is not None and cap.basis == "observed_interval" and cap.knob == "t_scale"
        # capacity falls with T: the low end meets the interval's high end
        for factor, edge in (
            (cap.low, cap.observed_interval[1]),
            (cap.high, cap.observed_interval[0]),
        ):
            d = draw_drivers(population, Adjustments(t_scale=factor), n=N_DRAWS)
            q = population_capacity("IDM", d, desired_speeds(d, None, 1.0))[0]
            assert q == pytest.approx(edge, abs=1.0)

    def test_a_mismatch_range_holds_the_recommended_value(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1700.0), population, sidecars=[], n_draws=N_DRAWS
        )
        for quantity in ("free_flow_speed", "capacity_per_lane"):
            u = report.comparison(quantity).uncertainty_range
            (knob,) = report.recommendation(quantity).knobs
            assert u is not None and u.basis == "observed_interval" and u.knob == knob.name
            assert u.low <= (knob.needed or 0.0) <= u.high  # the same curve, the point inside

    def test_what_the_check_cannot_read_falls_back_with_its_reason(self, population) -> None:
        far = check_transfer(
            observed_side(ff_speed=24.0, capacity=1300.0), population, sidecars=[], n_draws=N_DRAWS
        )
        for quantity in ("free_flow_speed", "capacity_per_lane"):
            u = far.comparison(quantity).uncertainty_range
            assert u is not None and u.basis == "measured_range_fallback"
            assert "no value of the" in u.reason
        bound = check_transfer(
            observed_side(ff_speed=30.0, capacity=1800.0, bottleneck=False),
            population,
            sidecars=[],
            n_draws=N_DRAWS,
        )
        u = bound.comparison("capacity_per_lane").uncertainty_range
        assert u is not None and u.basis == "measured_range_fallback"
        assert "only a lower bound" in u.reason
        assert (u.low, u.high) == (pytest.approx(1.1 / 1.4), pytest.approx(1.7 / 1.4))
        short = check_transfer(
            observed_side(ff_speed=30.0, capacity=1700.0, dates=syn.DATES[:2]),
            population,
            sidecars=[],
            n_draws=N_DRAWS,
        )
        for quantity in ("free_flow_speed", "capacity_per_lane"):
            u = short.comparison(quantity).uncertainty_range
            assert u is not None and "no 95 % interval" in u.reason

    def test_a_simulated_capacity_range_is_read_off_the_grid(
        self, tmp_path, pop_path, population
    ) -> None:
        sc = _sidecar(tmp_path, pop_path)
        report = check_transfer(
            observed_side(ff_speed=32.2, capacity=1780.0),
            population,
            sidecars=[sc],
            n_draws=N_DRAWS,
        )
        c = report.comparison("capacity_per_lane")
        u = c.uncertainty_range
        assert u is not None and u.basis == "observed_interval" and u.curve == "simulated"
        lo, hi = u.observed_interval
        # the grid is linear, 1650 at T x 1.0 rising 50 per 0.05 down to 0.8
        assert u.low == pytest.approx(1.0 - (hi - 1650.0) / 1000.0, abs=1e-6)
        assert u.high == pytest.approx(1.0 - (lo - 1650.0) / 1000.0, abs=1e-6)
        assert "simulated grid" in u.reason

    def test_outputs_carry_the_ranges(self, population) -> None:
        report = check_transfer(
            observed_side(ff_speed=30.0, capacity=1800.0, bottleneck=False),
            population,
            sidecars=[],
            n_draws=N_DRAWS,
        )
        payload = json.loads(report.to_json())
        by_q = {c["quantity"]: c for c in payload["comparisons"]}
        assert "uncertainty_range" not in by_q["truck_share"]
        ff = by_q["free_flow_speed"]["uncertainty_range"]
        assert set(ff) == {
            "knob",
            "parameter",
            "reference_mean",
            "low",
            "high",
            "parameter_low",
            "parameter_high",
            "basis",
            "reason",
            "clipped",
            "measured_range",
            "observed_interval",
            "curve",
        }
        assert ff["knob"] == "v0_scale" and ff["basis"] == "observed_interval"
        assert ff["parameter_low"] == pytest.approx(ff["low"] * 33.0, abs=1e-3)
        cap = by_q["capacity_per_lane"]["uncertainty_range"]
        assert cap["basis"] == "measured_range_fallback" and cap["observed_interval"] is None
        text = report.to_markdown()
        assert "## Ranges for the uncertainty runs" in text
        assert "**Mean desired speed (v0):** ×" in text
        assert "the values that keep the model inside the observed 95 % interval" in text
        assert "**Mean time headway (T):** × 0.786–1.21 (mean time headway 1.10–1.70 s)" in text
        assert "only a lower bound" in text
