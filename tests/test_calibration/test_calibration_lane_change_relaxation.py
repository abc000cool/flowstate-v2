"""Synthetic-frame tests for the gaps after a lane change (calibration.lane_change_relaxation, WP-88).

Every frame is built by hand on a shared 0.2 s grid (the I-24 MOTION
processed cadence). A planted change puts a changer C into lane 1 between its
new follower F and new leader L at t = 35 s; F's time gap to C is then
``h(τ) = T_n − (T_n − T_0) · exp(−τ / τ_r)`` exactly (positions encode the
gap, speeds are the table's), and before the change F follows L at ``T_n``,
so ``ratio_own = h / T_n`` and the fit must return ``τ_r``. The censoring
cases each end one side at a known instant for a known reason.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_gaps import lane_change_gaps
from calibration.lane_change_relaxation import (
    CENSOR_REASONS,
    DEFAULT_OFFSETS_S,
    MEASURES,
    SIDES,
    PostChangeGaps,
    concat_results,
    fit_relaxation,
    normal_time_gaps,
    post_change_gaps,
    sample_events,
    summarize_relaxation,
    with_population_ratio,
)

DT = 0.2
T_END = 70.0
TC = 35.0
V = 20.0
LEN = 5.0
MAIN = (1, 2, 3)
OFFS = np.asarray(DEFAULT_OFFSETS_S)


def _grid(t0: float = 0.0, t1: float = T_END) -> np.ndarray:
    k = np.arange(round(t0 / DT), round(t1 / DT) + 1)
    return np.round(k * DT, 6)


def _veh(
    vid: str,
    t: np.ndarray,
    x: np.ndarray,
    lane: np.ndarray | int,
    v: float | np.ndarray = V,
    length: float = LEN,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": t,
            "veh_id": vid,
            "x": x,
            "lane": np.broadcast_to(np.asarray(lane, dtype=np.int64), t.shape),
            "v": np.broadcast_to(np.asarray(v, dtype=float), t.shape),
            "length": length,
        }
    )


def _h(tau: np.ndarray, tn: float, t0: float, tau_r: float) -> np.ndarray:
    return tn - (tn - t0) * np.exp(-np.maximum(tau, 0.0) / tau_r)


def _event(
    i: int,
    *,
    tn: float = 1.5,
    r0: float = 0.7 / 1.5,
    tau_r: float = 6.0,
    tc_ref: float = 1.2,
    noise: np.ndarray | None = None,
    mods: dict[str, float] | None = None,
) -> list[pd.DataFrame]:
    """One planted change at x offset ``1000 · i``: C (``c{i}``) 2 → 1 at TC between F and L.

    ``mods``: ``f_lane_change_at`` (F moves to lane 2 at TC + value), ``cut_in_at``
    (a vehicle K moves into lane 1 midway between F and C at TC + value),
    ``c_ends_at`` (C's track ends at TC + value), ``c_switch_at`` / ``f_switch_at``
    (the track continues under a new id from TC + value + 0.2), ``f_falls_back_at``
    (F's gap grows by 12 m per second from TC + value), ``no_follower`` (no F).
    """
    mods = mods or {}
    t = _grid()
    tau = t - TC
    after = t >= TC - 1e-9
    t0 = r0 * tn
    x_l = 1000.0 * i + 100.0 + V * t
    x_c = x_l - (tn - t0) * V  # continuity of F's position at TC
    h = _h(tau, tn, t0, tau_r)
    if noise is not None:
        h = h * (1.0 + noise)
    x_f = np.where(after, x_c - LEN - h * V, x_l - LEN - tn * V)
    if "f_falls_back_at" in mods:
        x_f = x_f - 12.0 * np.maximum(tau - mods["f_falls_back_at"], 0.0)
    lane_c = np.where(after, 1, 2)
    x_q = x_c + LEN + tc_ref * V  # C's leader in lane 2 before the change
    parts = [
        _veh(f"l{i}", t, x_l, 1),
        _veh(f"q{i}", t, x_q, 2),
    ]
    c_keep = np.ones(t.size, dtype=bool)
    if "c_ends_at" in mods:
        c_keep = tau <= mods["c_ends_at"] + 1e-9
    if "c_switch_at" in mods:
        s = tau <= mods["c_switch_at"] + 1e-9
        parts.append(_veh(f"c{i}", t[s], x_c[s], lane_c[s]))
        parts.append(_veh(f"c{i}b", t[~s], x_c[~s], lane_c[~s]))
    else:
        parts.append(_veh(f"c{i}", t[c_keep], x_c[c_keep], lane_c[c_keep]))
    if "no_follower" not in mods:
        lane_f = np.ones(t.size, dtype=np.int64)
        if "f_lane_change_at" in mods:
            lane_f = np.where(tau >= mods["f_lane_change_at"] - 1e-9, 2, 1)
        if "f_switch_at" in mods:
            s = tau <= mods["f_switch_at"] + 1e-9
            parts.append(_veh(f"f{i}", t[s], x_f[s], lane_f[s]))
            parts.append(_veh(f"f{i}b", t[~s], x_f[~s], lane_f[~s]))
        else:
            parts.append(_veh(f"f{i}", t, x_f, lane_f))
    if "cut_in_at" in mods:
        mid = 0.5 * (x_f + x_c - LEN) + 0.5 * LEN
        lane_k = np.where(tau >= mods["cut_in_at"] - 1e-9, 1, 2)
        # K runs in lane 2 behind C (away from Q) until it cuts in
        parts.append(_veh(f"k{i}", t, mid, lane_k))
    return parts


def _frame(parts: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(parts, ignore_index=True)


def _records(df: pd.DataFrame) -> pd.DataFrame:
    rec = lane_change_gaps(df, (), mainline_lanes=MAIN, dt_s=DT).records
    return rec


def _planted(n: int, **kw: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    for i in range(n):
        parts += _event(i, tn=1.3 + 0.01 * i, **kw)
    df = _frame(parts)
    return df, _records(df)


def _changer_rows(rec: pd.DataFrame) -> np.ndarray:
    return rec["veh_id"].astype(str).str.match(r"^c\d+$").to_numpy()


class TestPlantedRelaxation:
    def test_follower_side_reads_the_planted_curve(self) -> None:
        df, rec = _planted(40)
        mask = _changer_rows(rec)
        assert mask.sum() == 40
        res = post_change_gaps(df, rec, changes=mask, dt_s=DT)
        assert res.counts["n_unmatched"] == 0
        assert (res.events["follower_censor"] == "observed_to_end").all()
        assert (res.events["leader_censor"] == "observed_to_end").all()
        # own references: F follows L at T_n before the change, C follows Q at 1.2 s
        ev = res.events.sort_values("veh_id", key=lambda s: s.str[1:].astype(int))
        np.testing.assert_allclose(
            ev["follower_ref_own_s"].to_numpy(), 1.3 + 0.01 * np.arange(40), atol=1e-9
        )
        np.testing.assert_allclose(res.events["leader_ref_own_s"].to_numpy(), 1.2, atol=1e-9)
        assert (res.events["follower_ref_n"] == 26).all()  # walk instants 5..30 s before
        expected = _h(OFFS, 1.0, 0.7 / 1.5, 6.0)  # ratio = h / T_n
        ratio = res.values["follower"]["ratio_own"]
        np.testing.assert_allclose(ratio, np.broadcast_to(expected, ratio.shape), atol=1e-9)
        # time gap and space gap at offset 0: the accepted lag gap of the extraction
        rec_c = rec[mask].set_index("veh_id")
        lag0 = rec_c.loc[res.events["veh_id"], "lag_gap_m"].to_numpy()
        np.testing.assert_allclose(res.values["follower"]["space_gap_m"][:, 0], lag0)
        lead0 = rec_c.loc[res.events["veh_id"], "lead_gap_m"].to_numpy()
        np.testing.assert_allclose(res.values["leader"]["space_gap_m"][:, 0], lead0)
        # the leader side is flat: C keeps (T_n − T_0) · V − L to L
        assert np.all(np.isfinite(res.values["leader"]["time_gap_s"]))

    def test_fit_recovers_tau(self) -> None:
        df, rec = _planted(40)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT)
        fit = fit_relaxation(OFFS, res.values["follower"]["ratio_own"], n_boot=50, seed=1)
        assert fit["supported"], fit["reason"]
        assert fit["tau_s"] == pytest.approx(6.0, rel=0.03)
        assert fit["r0"] == pytest.approx(0.7 / 1.5, abs=0.01)
        assert fit["r_inf"] == pytest.approx(1.0, abs=0.01)
        assert fit["tau_ci95"][0] <= 6.2 and fit["tau_ci95"][1] >= 5.8
        assert fit["amplitude_excludes_zero"] is True

    def test_fit_recovers_tau_under_noise(self) -> None:
        rng = np.random.default_rng(7)
        parts: list[pd.DataFrame] = []
        n_t = _grid().size
        for i in range(60):
            parts += _event(i, tn=1.4, tau_r=8.0, noise=rng.normal(0.0, 0.03, n_t))
        df = _frame(parts)
        rec = _records(df)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT)
        assert (res.events["follower_censor"] == "observed_to_end").all()
        fit = fit_relaxation(OFFS, res.values["follower"]["ratio_own"], n_boot=100, seed=3)
        assert fit["supported"], fit["reason"]
        assert fit["tau_s"] == pytest.approx(8.0, rel=0.2)
        lo, hi = fit["tau_ci95"]
        assert lo < 8.0 < hi

    def test_ratio_eq_and_population_ratio(self) -> None:
        df, rec = _planted(10)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT, equilibrium=(0.0, 2.0))
        np.testing.assert_allclose(
            res.values["follower"]["ratio_eq"],
            res.values["follower"]["space_gap_m"] / (2.0 * V),
        )
        # at one speed before and after, the own static-gap ratio equals the own time-gap ratio
        np.testing.assert_allclose(
            res.values["follower"]["ratio_own_eq"], res.values["follower"]["ratio_own"]
        )
        normal = normal_time_gaps(df, dt_s=DT, lanes=(1,))
        with_pop = with_population_ratio(res, normal, min_n=1)
        ref = normal.lookup(np.array([V]), min_n=1)[0]
        assert math.isfinite(ref)
        np.testing.assert_allclose(
            with_pop.values["follower"]["ratio_pop"],
            res.values["follower"]["time_gap_s"] / ref,
        )
        # the original result is not modified
        assert np.all(np.isnan(res.values["follower"]["ratio_pop"]))

    def test_own_static_gap_ratio_takes_the_speed_out(self) -> None:
        """F follows at its static gap s0 + vT before (5 m/s) and after (20 m/s) the change:
        its time gap falls from T + s0/5 to T + s0/20, ``ratio_own`` reads that fall and
        ``ratio_own_eq`` reads 1."""
        s0, T = 2.0, 1.5
        t = _grid()
        after = t >= TC - 1e-9
        v_f = np.where(after, 20.0, 5.0)
        x_l = 1000.0 + 20.0 * t
        x_c = x_l - LEN - 30.0
        x_f = np.where(after, x_c - LEN - (s0 + 20.0 * T), x_l - LEN - (s0 + 5.0 * T))
        df = _frame(
            [
                _veh("l0", t, x_l, 1),
                _veh("c0", t, x_c, np.where(after, 1, 2)),
                _veh("f0", t, x_f, 1, v=v_f),
            ]
        )
        rec = _records(df)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT, equilibrium=(s0, T))
        ev = res.events.iloc[0]
        assert ev["follower_ref_own_s"] == pytest.approx(T + s0 / 5.0)
        assert ev["follower_ref_own_eq"] == pytest.approx(1.0)
        vals = res.values["follower"]
        np.testing.assert_allclose(vals["ratio_eq"][0], 1.0)
        np.testing.assert_allclose(vals["ratio_own_eq"][0], 1.0)
        np.testing.assert_allclose(vals["ratio_own"][0], (T + s0 / 20.0) / (T + s0 / 5.0))

    def test_per_vehicle_equilibrium(self) -> None:
        df, rec = _planted(3)
        mask = _changer_rows(rec)
        params = {f"f{i}": (1.0, 1.0 + i) for i in range(3)}
        res = post_change_gaps(df, rec, changes=mask, dt_s=DT, equilibrium=params)
        for row, fid in enumerate(res.events["follower_id"]):
            s0, T = params[str(fid)]
            np.testing.assert_allclose(
                res.values["follower"]["ratio_eq"][row],
                res.values["follower"]["space_gap_m"][row] / (s0 + V * T),
            )

    def test_anticipation_is_outside_the_reference(self) -> None:
        """F opens to 2.5 s over the last 5 s before the change; its reference stays T_n."""
        parts = _event(0)
        f = parts[-1]
        t = f["t"].to_numpy()
        x_l = parts[0]["x"].to_numpy()
        pre = (t > TC - 5.0 + 1e-9) & (t < TC - 1e-9)
        f.loc[pre, "x"] = x_l[pre] - LEN - 2.5 * V
        df = _frame(parts)
        rec = _records(df)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT)
        assert res.events["follower_ref_own_s"].iloc[0] == pytest.approx(1.5)
        wide = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT, pre_window_s=(30, 0))
        assert wide.events["follower_ref_n"].iloc[0] == 30
        assert wide.events["follower_ref_own_s"].iloc[0] == pytest.approx(1.5)
        tight = post_change_gaps(
            df, rec, changes=_changer_rows(rec), dt_s=DT, pre_window_s=(4, 1), min_ref_samples=3
        )
        assert tight.events["follower_ref_n"].iloc[0] == 4
        assert tight.events["follower_ref_own_s"].iloc[0] == pytest.approx(2.5)
        # four instants are too few at the default minimum of five
        short = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT, pre_window_s=(4, 1))
        assert math.isnan(short.events["follower_ref_own_s"].iloc[0])


CENSOR_CASES: dict[str, tuple[dict[str, float], str, float, str, float]] = {
    # name: (mods, follower reason, follower last offset, leader reason, leader last offset)
    "clean": ({}, "observed_to_end", 30.0, "observed_to_end", 30.0),
    "f_lane": ({"f_lane_change_at": 7.1}, "partner_lane_change", 6.0, "observed_to_end", 30.0),
    "cut_in": ({"cut_in_at": 12.5}, "cut_in", 12.0, "observed_to_end", 30.0),
    "c_lost": ({"c_ends_at": 4.3}, "changer_lost", 4.0, "changer_lost", 4.0),
    "switch": (
        {"c_switch_at": 9.0, "f_switch_at": 15.0},
        "observed_to_end",
        30.0,
        "observed_to_end",
        30.0,
    ),
    "fall_back": ({"f_falls_back_at": 15.0}, "gap_bound", 20.0, "observed_to_end", 30.0),
    "no_f": ({"no_follower": 1.0}, "no_partner", math.nan, "observed_to_end", 30.0),
}
"""Censoring cases: (mods, follower reason, follower last offset, leader reason, leader last offset)."""


class TestCensoring:
    def _run(self, t_limits_s: tuple[float, float] | None = None) -> PostChangeGaps:
        parts: list[pd.DataFrame] = []
        for i, (mods, *_) in enumerate(CENSOR_CASES.values()):
            parts += _event(i, mods=mods)
        df = _frame(parts)
        rec = _records(df)
        return post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT, t_limits_s=t_limits_s)

    def test_each_side_ends_for_its_reason(self) -> None:
        res = self._run()
        ev = res.events.set_index("veh_id")
        for i, (name, (_, fr, fo, lr, lo)) in enumerate(CENSOR_CASES.items()):
            row = ev.loc[f"c{i}"]
            assert row["follower_censor"] == fr, name
            assert row["leader_censor"] == lr, name
            if math.isnan(fo):
                assert math.isnan(row["follower_last_offset_s"]), name
            else:
                assert row["follower_last_offset_s"] == fo, name
            assert row["leader_last_offset_s"] == lo, name

    def test_nothing_is_read_after_a_side_ends(self) -> None:
        res = self._run()
        for side in SIDES:
            last = res.events[f"{side}_last_offset_s"].to_numpy()
            read = np.isfinite(res.values[side]["space_gap_m"])
            for r in range(read.shape[0]):
                expect = OFFS <= (last[r] if math.isfinite(last[r]) else -1.0)
                np.testing.assert_array_equal(read[r], expect)

    def test_window_end(self) -> None:
        res = self._run(t_limits_s=(0.0, TC + 17.0))
        ev = res.events.set_index("veh_id")
        row = ev.loc["c0"]
        assert row["follower_censor"] == "window_end"
        assert row["follower_last_offset_s"] == 15.0

    def test_counts_and_summary(self) -> None:
        res = self._run()
        counts = res.counts
        assert counts["n_changes"] == len(CENSOR_CASES)
        assert counts["n_follower_no_partner"] == 1
        assert counts["n_follower_measured"] == len(CENSOR_CASES) - 1
        assert sum(counts[f"n_follower_{r}"] for r in CENSOR_REASONS) == len(CENSOR_CASES)
        rows = summarize_relaxation(res, n_boot=10, min_n_fit=2, min_offsets_fit=3)
        assert {r["side"] for r in rows} == set(SIDES)
        top = next(r for r in rows if r["speed_class"] == "all" and r["side"] == "follower")
        assert top["movement"] == "through" and top["zone_kind"] == "basic"
        assert top["n_changes"] == len(CENSOR_CASES)
        assert top["n_measured"] == len(CENSOR_CASES) - 1
        # sides read per offset: all 6 measured at 0-4 s; c_lost ends after 4, f_lane after 6
        n = dict(zip(top["offsets_s"], top["n"], strict=True))
        assert n[0.0] == 6 and n[4.0] == 6 and n[5.0] == 5 and n[8.0] == 4
        assert n[15.0] == 3 and n[20.0] == 3 and n[25.0] == 2
        assert top["censor"]["cut_in"] == 1 and top["censor"]["no_partner"] == 1
        # read at every offset to 10 s: clean, cut_in, switch, fall_back
        assert top["complete_case"]["n"] == 4
        assert set(top["measures"]) == set(MEASURES)
        assert set(top["fits"]) == {"ratio_own", "ratio_own_eq", "ratio_pop", "ratio_eq"}
        # the speed classes: every changer at 20 m/s
        v20 = next(r for r in rows if r["speed_class"] == "v>=20" and r["side"] == "follower")
        assert v20["n_changes"] == len(CENSOR_CASES)
        # empty speed classes are not listed
        assert {r["speed_class"] for r in rows} == {"all", "v>=20"}

    def test_arrival_change_has_no_changer_reference(self) -> None:
        """C's track starts 0.4 s before its change (a ramp arrival): no own reference."""
        parts = _event(0)
        c = parts[2]
        parts[2] = c[c["t"] >= TC - 0.4 - 1e-9]
        df = _frame(parts)
        rec = _records(df)
        res = post_change_gaps(df, rec, changes=_changer_rows(rec), dt_s=DT)
        assert math.isnan(res.events["leader_ref_own_s"].iloc[0])
        assert res.events["leader_ref_n"].iloc[0] == 0
        assert res.events["follower_ref_own_s"].iloc[0] == pytest.approx(1.5)


class TestFit:
    def test_flat_curve_is_not_a_relaxation(self) -> None:
        rng = np.random.default_rng(0)
        vals = 1.0 + rng.normal(0.0, 0.05, (200, OFFS.size))
        fit = fit_relaxation(OFFS, vals, n_boot=100, seed=2)
        assert not fit["supported"]
        assert "no relaxation" in fit["reason"]
        assert fit["amplitude_excludes_zero"] is False

    def test_slow_relaxation_is_not_resolved(self) -> None:
        vals = np.broadcast_to(_h(OFFS, 1.0, 0.5, 300.0), (50, OFFS.size)).copy()
        fit = fit_relaxation(OFFS, vals, n_boot=20, seed=2)
        assert not fit["supported"]
        assert "slower than the window" in fit["reason"]

    def test_step_is_faster_than_the_first_offset(self) -> None:
        curve = np.where(OFFS > 0, 1.0, 0.5)
        vals = np.broadcast_to(curve, (50, OFFS.size)).copy()
        fit = fit_relaxation(OFFS, vals, n_boot=20, seed=2)
        assert not fit["supported"]
        assert "faster than the first offset" in fit["reason"]

    def test_a_gap_that_opens_and_closes_is_not_a_relaxation(self) -> None:
        curve = 1.0 + 0.5 * np.sin(np.pi * OFFS / 30.0)
        vals = np.broadcast_to(curve, (60, OFFS.size)).copy()
        fit = fit_relaxation(OFFS, vals, n_boot=20, seed=2)
        assert not fit["supported"]
        assert "not an exponential approach" in fit["reason"]
        assert fit["rel_rms_resid"] > 0.25

    def test_too_few_offsets(self) -> None:
        vals = np.full((100, OFFS.size), np.nan)
        vals[:, :3] = 1.0
        fit = fit_relaxation(OFFS, vals, min_n=30, min_offsets=5)
        assert not fit["supported"]
        assert fit["reason"].startswith("fewer than 5 offsets")
        assert fit["tau_s"] is None

    def test_seeded(self) -> None:
        rng = np.random.default_rng(4)
        vals = _h(OFFS, 1.0, 0.6, 5.0)[None, :] * (1 + rng.normal(0, 0.1, (80, OFFS.size)))
        a = fit_relaxation(OFFS, vals, n_boot=30, seed=11)
        b = fit_relaxation(OFFS, vals, n_boot=30, seed=11)
        assert a == b


class TestInputs:
    def test_offsets_and_step_must_be_whole_multiples(self) -> None:
        df, rec = _planted(1)
        with pytest.raises(ValueError, match="step_s"):
            post_change_gaps(df, rec, dt_s=DT, step_s=0.5)
        with pytest.raises(ValueError, match="offsets_s"):
            post_change_gaps(df, rec, dt_s=DT, offsets_s=(0.0, 7.5))
        with pytest.raises(ValueError, match="pre_window_s"):
            post_change_gaps(df, rec, dt_s=DT, pre_window_s=(5.0, 10.0))
        with pytest.raises(ValueError, match="records"):
            post_change_gaps(df, rec.drop(columns=["to_lane"]), dt_s=DT)

    def test_unmatched_records_are_counted(self) -> None:
        df, rec = _planted(2)
        bad = rec.copy()
        bad.loc[:, "t"] = bad["t"] + 3.0
        res = post_change_gaps(df, bad, dt_s=DT)
        assert res.counts["n_unmatched"] == len(bad)
        assert len(res.events) == 0

    def test_concat_and_long_form_and_sample(self) -> None:
        df, rec = _planted(4)
        mask = _changer_rows(rec)
        a = post_change_gaps(df, rec, changes=mask, dt_s=DT)
        both = concat_results([a, a])
        assert len(both.events) == 2 * len(a.events)
        assert both.counts["n_changes"] == 2 * a.counts["n_changes"]
        long = a.to_long()
        assert set(long["side"]) == set(SIDES)
        assert len(long) == int(np.isfinite(a.values["follower"]["space_gap_m"]).sum()) + int(
            np.isfinite(a.values["leader"]["space_gap_m"]).sum()
        )
        s = sample_events(a, 3, seed=5)
        assert s["n"] == 3 and len(s["rows"]) == 3
        assert len(s["rows"][0]) == len(s["columns"])
        assert s == sample_events(a, 3, seed=5)

    def test_normal_time_gaps_by_speed(self) -> None:
        """A platoon at 1.5 s and 20 m/s, another at 2.0 s and 10 m/s: the medians of their bins."""
        t = _grid(0.0, 20.0)
        parts = []
        for j in range(5):
            parts.append(_veh(f"a{j}", t, 1000.0 - j * (1.5 * V + LEN) + V * t, 1))
            parts.append(_veh(f"b{j}", t, 5000.0 - j * (2.0 * 10.0 + LEN) + 10.0 * t, 2, v=10.0))
        df = _frame(parts)
        normal = normal_time_gaps(df, dt_s=DT)
        med = normal.lookup(np.array([20.5, 10.5, 30.0]), min_n=1)
        assert med[0] == pytest.approx(1.5, abs=0.02)
        assert med[1] == pytest.approx(2.0, abs=0.02)
        assert math.isnan(med[2])
        # 4 followers per platoon, 21 instants on the 1-s grid
        assert int(normal.n.sum()) == 2 * 4 * 21
        pooled = normal + normal
        assert int(pooled.n.sum()) == 2 * int(normal.n.sum())
        d = normal.to_dict(min_n=1)
        assert any(b["p50"] is not None for b in d["bins"])
        windowed = normal_time_gaps(df, dt_s=DT, window_s=(0.0, 10.0))
        assert int(windowed.n.sum()) == 2 * 4 * 10
