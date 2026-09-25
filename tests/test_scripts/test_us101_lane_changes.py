"""Tests for ``scripts/us101_lane_changes.py`` (WP-81, docs/ROADMAP.md §5 D2).

Synthetic frames on a shared 0.5 s grid (the US-101 runs' 2 Hz cadence),
uniform speeds and known lane sequences, so every class, count, vehicle-km
and fuel ratio is hand-computed. Trajectory lanes are SUMO indices (0 =
rightmost), as ``trajectories.parquet`` stores them. One integration test runs
a tiny generated corridor through ``run_micro`` to check the script reads a
real run directory (AV ids, per-vehicle fuel, the corridor block, the
per-vehicle table) the way the runner writes it. The sweep script's boundary
fallback is checked on the committed scenarios only.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lc = _load("flowstate_us101_lane_changes", "us101_lane_changes.py")

DT = 0.5
T0 = 100.0


def _track(
    veh_id: str,
    x0: float,
    v: float,
    lanes: list[int],
    *,
    k0: int = 0,
    is_av: bool = False,
) -> pd.DataFrame:
    """Uniform-speed samples on the grid ``T0 + DT · k`` from slot ``k0`` (SUMO lane indices)."""
    k = k0 + np.arange(len(lanes))
    return pd.DataFrame(
        {
            "t": T0 + DT * k,
            "veh_id": veh_id,
            "x": x0 + v * DT * np.arange(len(lanes)),
            "lane": np.asarray(lanes, dtype=np.int32),
            "v": float(v),
            "is_av": is_av,
        }
    )


def _analyse(df: pd.DataFrame, av_ids: list[str], **kw: Any) -> dict[str, Any]:
    kw.setdefault("fuel_ml_per_vehicle", {str(v): 10.0 for v in df["veh_id"].unique()})
    kw.setdefault("n_lanes", 3)
    kw.setdefault("span_m", (0.0, 1.0e5))
    kw.setdefault("warmup_s", 0.0)
    result: dict[str, Any] = lc.analyse_frame(df, av_ids=av_ids, **kw)
    return result


def _by_changer(res: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rec = res["_records"]
    return {str(r["veh_id"]): r for r in rec.to_dict("records")}


N = 40  # samples per track (20 s)


class TestClasses:
    """Pass-around, human-behind-human, AV changes and cut-ins on one frame."""

    @pytest.fixture
    def res(self) -> dict[str, Any]:
        df = pd.concat(
            [
                # H follows the AV A (bumper gap 5 m) in SUMO lane 1 and leaves to lane 2.
                _track("A", 110.0, 10.0, [1] * N, is_av=True),
                _track("H", 100.0, 10.0, [1] * 20 + [2] * 20),
                # G follows the human Q in lane 0 and leaves to lane 1 (nothing within 200 m there).
                _track("Q", 1020.0, 10.0, [0] * N),
                _track("G", 1000.0, 10.0, [0] * 20 + [1] * 20),
                # The AV B changes lanes itself.
                _track("B", 2000.0, 10.0, [2] * 20 + [1] * 20, is_av=True),
                # C moves from lane 0 into lane 1 just ahead of the AV D (bumper gap 15 m).
                _track("D", 2980.0, 10.0, [1] * N, is_av=True),
                _track("C", 3000.0, 10.0, [0] * 20 + [1] * 20),
            ],
            ignore_index=True,
        )
        return _analyse(df, ["A", "B", "D"])

    def test_human_passing_an_av_is_a_pass_around(self, res: dict[str, Any]) -> None:
        h = _by_changer(res)["H"]
        assert h["klass"] == "pass_around"
        assert h["pass_around"] and h["pass_around_at_change"]
        assert h["origin_leader_id"] == "A"
        assert h["origin_leader_gap_m"] == pytest.approx(5.0)
        assert not h["changer_is_av"]

    def test_human_behind_a_human_is_not(self, res: dict[str, Any]) -> None:
        g = _by_changer(res)["G"]
        assert g["origin_leader_id"] == "Q"
        assert not g["pass_around"] and not g["pass_around_at_change"]
        assert g["klass"] == "other"

    def test_av_changes_are_separated(self, res: dict[str, Any]) -> None:
        b = _by_changer(res)["B"]
        assert b["klass"] == "av" and b["changer_is_av"]
        assert not b["pass_around"] and not b["cut_in"]
        c = res["counts"]
        assert (c["n_changes"], c["n_av"], c["n_human"]) == (4, 1, 3)

    def test_cut_in_ahead_of_an_av(self, res: dict[str, Any]) -> None:
        c = _by_changer(res)["C"]
        assert c["lag_id"] == "D" and c["new_follower_is_av"]
        assert c["klass"] == "cut_in" and not c["pass_around"]

    def test_counts_by_class(self, res: dict[str, Any]) -> None:
        c = res["counts"]
        assert c["n_pass_around"] == 1
        assert c["n_pass_around_at_change"] == 1
        assert c["n_cut_in"] == 1
        assert c["n_other_human"] == 1
        assert res["rates"]["pass_around_share_of_human_changes"] == pytest.approx(1 / 3)


class TestLookback:
    """The AV was directly ahead within the lookback, but a human cut in before the change."""

    @staticmethod
    def _frame(cut_in_slot: int) -> pd.DataFrame:
        return pd.concat(
            [
                _track("E", 5020.0, 10.0, [1] * N, is_av=True),
                _track("I", 5010.0, 10.0, [2] * cut_in_slot + [1] * (N - cut_in_slot)),
                _track("L", 5000.0, 10.0, [1] * 20 + [0] * 20),
            ],
            ignore_index=True,
        )

    def test_av_ahead_inside_the_lookback_is_a_pass_around(self) -> None:
        # I enters lane 1 at slot 14; L's last origin-lane sample is slot 19 and the
        # 5 s lookback reaches slot 9, where E (the AV) was L's leader.
        res = _analyse(self._frame(14), ["E"])
        rec = _by_changer(res)["L"]
        assert rec["origin_leader_id"] == "I"
        assert not rec["pass_around_at_change"]
        assert rec["pass_around"]
        assert set(rec["origin_leader_ids_lookback"]) == {"E", "I"}

    def test_av_ahead_only_before_the_lookback_is_not(self) -> None:
        res = _analyse(self._frame(5), ["E"])
        rec = _by_changer(res)["L"]
        assert not rec["pass_around"]
        assert rec["origin_leader_ids_lookback"] == ["I"]

    def test_shorter_lookback_drops_it(self) -> None:
        res = _analyse(self._frame(14), ["E"], lookback_s=2.0)
        assert not _by_changer(res)["L"]["pass_around"]

    def test_leader_beyond_range_does_not_count(self) -> None:
        df = pd.concat(
            [
                _track("A", 400.0, 10.0, [1] * N, is_av=True),  # 295 m ahead of H's front
                _track("H", 100.0, 10.0, [1] * 20 + [2] * 20),
            ],
            ignore_index=True,
        )
        rec = _by_changer(_analyse(df, ["A"]))["H"]
        assert rec["origin_leader_id"] is None and not rec["pass_around"]
        rec = _by_changer(_analyse(df, ["A"], leader_range_m=300.0))["H"]
        assert rec["origin_leader_id"] == "A" and rec["pass_around"]


class TestPerKm:
    """Rates count changes and vehicle-km inside the span and the window only."""

    @pytest.fixture
    def res(self) -> dict[str, Any]:
        # 2 lanes, 5 m per sample at 10 m/s. P drives 0 → 800 m from T0 (161 samples)
        # and changes at x = 30 m (slot 6: before the window and outside the span),
        # 200 and 400 m (inside both) and 700 m (outside the span); R drives 300 → 400 m
        # from T0 and changes at 380 m; the AV S drives 550 → 650 m from T0 + 5 s and
        # changes at 580 m.
        p_lanes = [0] * 6 + [1] * 34 + [0] * 40 + [1] * 60 + [0] * 21
        r_lanes = [0] * 16 + [1] * 5
        s_lanes = [1] * 6 + [0] * 15
        df = pd.concat(
            [
                _track("P", 0.0, 10.0, p_lanes),
                _track("R", 300.0, 10.0, r_lanes),
                _track("S", 550.0, 10.0, s_lanes, k0=10, is_av=True),
            ],
            ignore_index=True,
        )
        return _analyse(df, ["S"], n_lanes=2, span_m=(100.0, 600.0), warmup_s=T0 + 5.0)

    def test_vehicle_km_in_span_and_window(self, res: dict[str, Any]) -> None:
        # P: all of [100, 600) after T0 + 5 s (it is at 50 m then) = 500 m; R: 350 → 400 m
        # = 50 m; S: 550 → 600 m = 50 m.
        assert res["vkt_km"]["human"] == pytest.approx(0.55)
        assert res["vkt_km"]["av"] == pytest.approx(0.05)
        assert res["vkt_km"]["all"] == pytest.approx(0.60)

    def test_changes_per_veh_km(self, res: dict[str, Any]) -> None:
        c = res["counts"]
        assert (c["n_changes"], c["n_human"], c["n_av"]) == (4, 3, 1)
        r = res["rates"]
        assert r["lc_per_veh_km_human"] == pytest.approx(3 / 0.55)
        assert r["lc_per_veh_km_av"] == pytest.approx(1 / 0.05)
        assert r["lc_per_veh_km_all"] == pytest.approx(4 / 0.60)
        # all six detected changes stay in the per-vehicle (whole-record) counts
        assert res["detector_counts"]["n_changes"] == 6

    def test_span_distance_clips_a_crossing_segment(self) -> None:
        veh = np.array([0, 0, 0], dtype=np.int64)
        t = np.array([0.0, 1.0, 2.0])
        x = np.array([90.0, 110.0, 130.0])
        d = lc.span_distance_m(veh, t, x, 1, t_lo=0.0, x_lo=100.0, x_hi=120.0)
        assert d[0] == pytest.approx(20.0)
        d = lc.span_distance_m(veh, t, x, 1, t_lo=1.0, x_lo=100.0, x_hi=120.0)
        assert d[0] == pytest.approx(10.0)


class TestFuel:
    def test_binning(self) -> None:
        per_vehicle = pd.DataFrame(
            {
                "veh_id": ["h0a", "h0b", "h1", "h2", "h3", "av", "hx"],
                "is_av": [False, False, False, False, False, True, False],
                "whole_journey": [True, True, True, True, True, True, False],
                "fuel_ml": [100.0, 60.0, 90.0, 130.0, 70.0, 500.0, 999.0],
                "trip_km": [1.0, 0.5, 0.75, 1.0, 0.5, 1.0, 1.0],
                "n_changes": [0, 0, 1, 2, 3, 2, 0],
            }
        )
        out = lc.fuel_by_changes(per_vehicle)
        assert out["0"]["n"] == 2
        assert out["0"]["mean_ml_per_km"] == pytest.approx(110.0)
        assert out["0"]["pooled_ml_per_km"] == pytest.approx(160.0 / 1.5)
        assert out["1"] == {"n": 1, "mean_ml_per_km": 120.0, "pooled_ml_per_km": 120.0}
        assert out["2+"]["n"] == 2
        assert out["2+"]["mean_ml_per_km"] == pytest.approx(135.0)
        assert out["2+"]["pooled_ml_per_km"] == pytest.approx(200.0 / 1.5)
        assert out["changed"]["n"] == 3
        assert out["changed"]["mean_ml_per_km"] == pytest.approx(130.0)
        assert out["changed_minus_unchanged_ml_per_km"] == pytest.approx(20.0)
        assert (out["n_humans"], out["n_humans_not_whole_journey"]) == (6, 1)

    def test_empty_bin_is_null(self) -> None:
        per_vehicle = pd.DataFrame(
            {
                "veh_id": ["h"],
                "is_av": [False],
                "whole_journey": [True],
                "fuel_ml": [10.0],
                "trip_km": [0.1],
                "n_changes": [0],
            }
        )
        out = lc.fuel_by_changes(per_vehicle)
        assert out["1"]["mean_ml_per_km"] is None
        assert out["changed_minus_unchanged_ml_per_km"] is None

    def test_whole_journeys_and_class_ratios_on_a_frame(self) -> None:
        # U enters before the warm-up (not a whole journey); W never arrives; K and M
        # are whole journeys with 1 and 0 changes; the AV Z's fuel stays in its class.
        df = pd.concat(
            [
                _track("U", 0.0, 10.0, [0] * 30),
                _track("K", 0.0, 10.0, [0] * 10 + [1] * 20, k0=20),
                _track("M", 0.0, 20.0, [1] * 20, k0=20),
                _track("W", 0.0, 10.0, [0] * 30, k0=30),
                _track("Z", 0.0, 10.0, [1] * 20, k0=20, is_av=True),
            ],
            ignore_index=True,
        )
        fuel = {"U": 50.0, "K": 30.0, "M": 76.0, "W": 20.0, "Z": 40.0}
        arrived = {"U": True, "K": True, "M": True, "W": False, "Z": True}
        res = _analyse(
            df, ["Z"], fuel_ml_per_vehicle=fuel, n_lanes=2, warmup_s=T0 + 10.0, arrived=arrived
        )
        fbc = res["fuel_by_changes"]
        # K: 29 samples at 10 m/s = 145 m -> 30 / 0.145; M: 19 x 0.5 x 20 = 190 m -> 76 / 0.19
        assert fbc["1"]["n"] == 1 and fbc["0"]["n"] == 1
        assert fbc["1"]["mean_ml_per_km"] == pytest.approx(30.0 / 0.145)
        assert fbc["0"]["mean_ml_per_km"] == pytest.approx(76.0 / 0.19)
        assert fbc["n_humans_not_whole_journey"] == 2
        km = {"U": 0.145, "K": 0.145, "M": 0.19, "W": 0.145, "Z": 0.095}
        human_km = km["U"] + km["K"] + km["M"] + km["W"]
        assert res["fuel"]["human"]["ml_per_km"] == pytest.approx(176.0 / human_km)
        assert res["fuel"]["av"]["ml_per_km"] == pytest.approx(40.0 / 0.095)
        assert res["fuel"]["all"]["ml_per_km"] == pytest.approx(216.0 / sum(km.values()))

    def test_decomposition_is_exact(self) -> None:
        base = {"fuel": {"all": {"ml_per_km": 60.0}}}
        level = {
            "fuel": {
                "all": {"km": 10.0, "ml_per_km": (8.0 * 62.0 + 2.0 * 70.0) / 10.0},
                "human": {"km": 8.0, "ml_per_km": 62.0},
                "av": {"km": 2.0, "ml_per_km": 70.0},
            }
        }
        d = lc.fuel_decomposition(level, base)
        assert d["from_humans"] == pytest.approx(0.8 * 2.0)
        assert d["from_avs"] == pytest.approx(0.2 * 10.0)
        assert d["from_humans"] + d["from_avs"] == pytest.approx(d["total"])


def _run(
    pen: float,
    scalars: dict[str, float],
    *,
    av_ids: list[str] | None = None,
    neighbours: dict[str, Any] | None = None,
    pass_rate: float = 0.0,
    fuel: tuple[float, float, float] = (60.0, 60.0, 0.0),
) -> dict[str, Any]:
    """A per-run record as ``analyse_run`` writes it (only the keys ``aggregate`` reads)."""
    f_all, f_h, f_a = fuel
    rec: dict[str, Any] = {
        "penetration": pen,
        "av_ids": av_ids or [],
        "scalars": dict(scalars),
        "rates": {
            "pass_arounds_per_human_veh_km": pass_rate,
            "cut_ins_per_human_veh_km": 0.0,
        },
        "counts": {"n_changes": 1},
        "fuel": {
            "all": {"km": 10.0, "ml_per_km": f_all},
            "human": {"km": 10.0 * (1 - pen), "ml_per_km": f_h},
            "av": {"km": 10.0 * pen, "ml_per_km": f_a if pen else None},
        },
        "fuel_by_changes": {
            b: {"n": 1, "mean_ml_per_km": 1.0} for b in ("0", "1", "2+", "changed")
        },
    }
    if neighbours is not None:
        rec["neighbours"] = neighbours
    return rec


class TestAggregate:
    SEEDS = (1, 2, 3)

    def test_paired_delta_matches_hand_computation(self) -> None:
        base = {1: 1.0, 2: 2.0, 3: 4.0}
        level = {1: 1.5, 2: 2.1, 3: 4.9}
        d = lc.paired(level, base)
        diffs = np.array([0.5, 0.1, 0.9])
        half = stats.t.ppf(0.975, 2) * diffs.std(ddof=1) / math.sqrt(3)
        assert d["mean"] == pytest.approx(0.5)
        assert d["lo95"] == pytest.approx(0.5 - half)
        assert d["pct_of_baseline"] == pytest.approx(100.0 * 0.5 / (7.0 / 3.0))
        assert d["resolved"] is bool(0.5 - half > 0)

    def test_counterfactual_counts_the_same_vehicles_in_the_baseline(self) -> None:
        neighbours = {
            # A and B are AVs at the level; in the baseline they are ordinary drivers.
            "changes": [
                ["h1", ["A"], None],  # behind A: counts
                ["h2", ["x", "B"], None],  # B in the lookback: counts
                ["A", ["h9"], None],  # the changer is an AV at the level: left out
                ["h3", ["x"], "B"],  # into the gap ahead of B: a baseline cut-in
                ["h4", ["x"], None],
            ],
            "span_m_per_vehicle": {"A": 500.0, "B": 500.0, "h1": 1000.0, "h2": 1000.0},
        }
        level = _run(0.1, {}, av_ids=["A", "B"], pass_rate=5.0)
        cf = lc.counterfactual(level, {"neighbours": neighbours})
        assert cf["baseline_human_veh_km"] == pytest.approx(2.0)
        assert cf["baseline_pass_arounds_per_human_veh_km"] == pytest.approx(1.0)
        assert cf["baseline_cut_ins_per_human_veh_km"] == pytest.approx(0.5)
        assert cf["excess_pass_arounds_per_human_veh_km"] == pytest.approx(4.0)

    def test_levels_checks_and_verdict(self) -> None:
        nb = {"changes": [["h1", ["A"], None]], "span_m_per_vehicle": {"h1": 2000.0}}
        per_run = {"baseline": {}, "fs_p0.05": {}}
        for i, s in enumerate(self.SEEDS):
            base_sc = {
                "orig_fuel_ml_per_veh_km": 66.0 + 0.1 * i,
                "lc_per_veh_km_human": 1.0 + 0.01 * i,
                "fuel_ml_per_veh_km_human": 60.0 + 0.1 * i,
                "fuel_ml_per_km_human_0_changes": 58.0 + 0.1 * i,
                "fuel_ml_per_km_human_changed_minus_unchanged": 2.0 + 0.1 * i,
            }
            lvl_sc = {
                "orig_fuel_ml_per_veh_km": 67.0 + 0.1 * i + 0.02 * i * i,
                "lc_per_veh_km_human": 1.3 + 0.01 * i + 0.003 * i * i,
                "fuel_ml_per_veh_km_human": 60.5 + 0.1 * i + 0.01 * i * i,
                "fuel_ml_per_km_human_0_changes": 58.0 + 0.1 * i + 0.01 * (i - 1),
                "fuel_ml_per_km_human_changed_minus_unchanged": 2.5 + 0.1 * i,
            }
            per_run["baseline"][s] = _run(0.0, base_sc, neighbours=nb)
            per_run["fs_p0.05"][s] = _run(0.05, lvl_sc, av_ids=["A"], pass_rate=1.0 + 0.1 * i)
        out = lc.aggregate(per_run, list(self.SEEDS))
        lv = out["levels"]["fs_p0.05"]
        assert out["missing_runs"] == []
        assert lv["n_runs"] == 3 and lv["penetration"] == 0.05
        # the only baseline change behind A: 1 per 2 km of the other drivers
        cf = lv["counterfactual"]
        assert cf["baseline_pass_arounds_per_human_veh_km"]["mean"] == pytest.approx(0.5)
        assert cf["excess_pass_arounds_per_human_veh_km"]["mean"] == pytest.approx(0.6)
        checks = lv["hypothesis_checks"]
        assert checks == {
            "a_fuel_increase_reproduces": True,
            "b_humans_change_more": True,
            "c_excess_changes_behind_avs": True,
            "d_humans_burn_more": True,
            "e_changes_cost_fuel": True,
        }
        assert lv["verdict"] == "supported" and lv["failed_checks"] == []
        assert lv["diagnostic_unchanged_humans_fuel_rises"] is False

    def test_verdict_rules(self) -> None:
        all_true = dict.fromkeys(lc.RESOLVED_CHECKS, True)
        assert lc._verdict(all_true) == ("supported", [])
        no_a = {**all_true, "a_fuel_increase_reproduces": False}
        assert lc._verdict(no_a)[0] == "not_applicable"
        no_b = {**all_true, "b_humans_change_more": False}
        assert lc._verdict(no_b) == ("not_supported", ["b_humans_change_more"])

    def test_missing_runs_are_listed(self) -> None:
        per_run = {"baseline": {1: _run(0.0, {"x": 1.0})}, "fs_p0.01": {}}
        out = lc.aggregate(per_run, [1, 2])
        assert {"cell": "baseline", "seed": 2} in out["missing_runs"]
        assert {"cell": "fs_p0.01", "seed": 1} in out["missing_runs"]


def test_constants_are_the_runners() -> None:
    from microsim.runner import CORRIDOR_INSERTION_BUFFER_M
    from microsim.vehicles import VEHICLE_LENGTH_M

    assert lc.DEFAULT_LENGTH_M == VEHICLE_LENGTH_M
    assert lc.CORRIDOR_INSERTION_BUFFER_M == CORRIDOR_INSERTION_BUFFER_M


def test_is_av_disagreement_is_refused() -> None:
    df = _track("A", 0.0, 10.0, [0] * 10, is_av=True)
    with pytest.raises(ValueError, match="is_av disagrees"):
        _analyse(df, [])


def test_sweep_boundary_fallback_is_the_committed_schedule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yaml

    from flowstate_core.config import ScenarioConfig

    sweep = _load("flowstate_us101_penetration_sweep", "us101_penetration_sweep.py")
    monkeypatch.setattr(sweep, "BOUNDARY_SCENARIO", tmp_path / "absent.yaml")
    cfg, source = sweep._base_with_boundary()
    assert source == "scenarios/us101_replica_calibrated.yaml network.boundary"
    cal = ScenarioConfig.from_yaml(REPO_ROOT / "scenarios" / "us101_replica_calibrated.yaml")
    got = ScenarioConfig.model_validate(cfg)
    assert got.network.boundary == cal.network.boundary
    assert got.network.boundary is not None and len(got.network.boundary.steps) == 32
    base = yaml.safe_load((REPO_ROOT / "scenarios" / "us101_replica.yaml").read_text())
    base_cfg = ScenarioConfig.model_validate(base)
    assert got.model_copy(
        update={"network": got.network.model_copy(update={"boundary": None})}
    ) == base_cfg.model_copy(
        update={"network": base_cfg.network.model_copy(update={"boundary": None})}
    )


@pytest.mark.integration
def test_reads_a_real_run_directory(tmp_path: Path) -> None:
    """A tiny 3-lane corridor with FollowerStopper AVs, 150 simulated seconds."""
    from flowstate_core.config import ScenarioConfig
    from microsim import run_micro

    cfg = ScenarioConfig.model_validate(
        {
            "name": "lc_tiny",
            "network": {"kind": "corridor", "length_m": 300.0, "lanes": 3, "inflow": [[0.0, 0.8]]},
            "av": {"penetration": 0.2, "compliance": 1.0, "controller": "follower_stopper"},
            "sim": {"duration_s": 150.0, "warmup_s": 30.0},
        }
    )
    paths = run_micro(cfg, 11, tmp_path)
    rec = lc.analyse_run(paths.run_dir)
    meta = json.loads(paths.meta.read_text())
    assert rec["study_span_m"] == [
        meta["corridor"]["x_first_edge_m"],
        meta["corridor"]["x_first_edge_m"] + 300.0,
    ]
    assert rec["av"]["is_av_checked"] and rec["av"]["n_av_recorded"] == len(meta["av_ids"]) > 0
    assert rec["arrival_source"] == "vehicles.parquet"
    assert rec["fuel_ratio_matches_compute_metrics"] is True
    assert rec["n_trajectory_without_fuel"] == 0
    assert rec["vkt_km"]["av"] > 0.0 and rec["vkt_km"]["human"] > 0.0
    assert rec["site_metrics"]["x_ref_m"] == pytest.approx(rec["study_span_m"][0] + 150.0)
    assert "site_throughput_veh_h" in rec["scalars"] and "orig_fuel_ml_per_veh_km" in rec["scalars"]
    written = json.loads((paths.run_dir / lc.PER_RUN_FILE).read_text())
    assert written["version"] == lc.PER_RUN_VERSION and "neighbours" not in written


def _write_tree(root: Path, cells: dict[str, list[int]], seeds: list[int]) -> None:
    """A sweep tree with per-run records only (``<cell>/<hash>/<seed>/lane_changes.json``)."""
    manifest = {"scenario": "t", "cells": {c: f"h{c}" for c in cells}, "seeds": seeds}
    (root / "MANIFEST.json").write_text(json.dumps(manifest))
    nb = {"changes": [], "span_m_per_vehicle": {"h": 1000.0}}
    for cell, present in cells.items():
        pen = 0.0 if cell == "baseline" else 0.1
        for i, s in enumerate(present):
            rec = _run(
                pen, {"lc_per_veh_km_human": 1.0 + 0.1 * i}, neighbours=nb if not pen else None
            )
            rec["version"] = lc.PER_RUN_VERSION
            rec["parameters"] = {
                "lookback_s": lc.DEFAULT_LOOKBACK_S,
                "leader_range_m": lc.DEFAULT_LEADER_RANGE_M,
                "min_dwell_s": None,
            }
            d = root / cell / f"h{cell}" / str(s)
            d.mkdir(parents=True)
            (d / lc.PER_RUN_FILE).write_text(json.dumps(rec))


def test_analyze_only_writes_the_artifact(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"baseline": [1, 2], "fs_p0.10": [1, 2]}, [1, 2])
    out = tmp_path / "a.json"
    assert lc.main(["--sweep", str(tmp_path), "--analyze-only", "--out", str(out)]) == 0
    art = json.loads(out.read_text())
    assert art["n_runs_analysed"] == 4 and art["missing_runs"] == []
    assert set(art["levels"]) == {"baseline", "fs_p0.10"}
    assert art["levels"]["fs_p0.10"]["verdict"] == "not_applicable"
    assert art["decision_rule"]["checks"].keys() == lc.RESOLVED_CHECKS.keys()


def test_missing_runs_fail_unless_partial_is_allowed(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"baseline": [1, 2], "fs_p0.10": [1]}, [1, 2])
    out = tmp_path / "a.json"
    assert lc.main(["--sweep", str(tmp_path), "--analyze-only", "--out", str(out)]) == 1
    assert json.loads(out.read_text())["missing_runs"] == [{"cell": "fs_p0.10", "seed": 2}]
    args = ["--sweep", str(tmp_path), "--analyze-only", "--allow-partial", "--out", str(out)]
    assert lc.main(args) == 0
