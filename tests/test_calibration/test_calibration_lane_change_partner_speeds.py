"""The partner speed of calibration.lane_change_relaxation (WP-91), on hand-built frames.

Each planted change puts a changer C (20 m/s) into lane 1 at t = 35 s between a
new follower F and a new leader L that drive at their own constant speeds. The
partner speed is the front vehicle's speed minus the rear one's: ``v_C − v_F``
on the follower side and ``v_L − v_C`` on the leader side, so it is known at
every offset. The additions leave every existing key of the summaries as it was.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_gaps import lane_change_gaps
from calibration.lane_change_relaxation import (
    MEASURES,
    PARTNER_SPEEDS,
    SIDES,
    PostChangeGaps,
    concat_results,
    normal_time_gaps,
    post_change_gaps,
    summarize_relaxation,
    with_population_ratio,
)

DT = 0.2
TC = 35.0
LEN = 5.0
V_C = 20.0
G_F = 20.0  # F's bumper gap to C at the change [m]
G_L = 25.0  # C's bumper gap to L at the change [m]

SUMMARY_KEYS_BEFORE_WP91 = {
    "zone_kind",
    "movement",
    "speed_class",
    "side",
    "offsets_s",
    "n_changes",
    "n_measured",
    "n_ref_own",
    "censor",
    "n",
    "ref_own_s",
    "measures",
    "rear_v_ms_p50",
    "complete_case",
    "fits",
}


def _event(i: int, d_f: float, d_l: float) -> list[pd.DataFrame]:
    """Change ``i``: F at ``V_C − d_f``, L at ``V_C + d_l``; tracks from TC − 3 s to TC + 30 s."""
    k = np.arange(round((TC - 3.0) / DT), round((TC + 30.0) / DT) + 1)
    t = np.round(k * DT, 6)
    tau = t - TC
    x_c0 = 2000.0 * i + 500.0
    v_f, v_l = V_C - d_f, V_C + d_l

    def veh(vid: str, x: np.ndarray, lane: np.ndarray | int, v: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "t": t,
                "veh_id": vid,
                "x": x,
                "lane": np.broadcast_to(np.asarray(lane, dtype=np.int64), t.shape),
                "v": v,
                "length": LEN,
            }
        )

    return [
        veh(f"c{i}", x_c0 + V_C * tau, np.where(tau >= -1e-9, 1, 2), V_C),
        veh(f"f{i}", x_c0 - LEN - G_F + v_f * tau, 1, v_f),
        veh(f"l{i}", x_c0 + LEN + G_L + v_l * tau, 1, v_l),
    ]


def _run(d_f: list[float], d_l: list[float]) -> tuple[pd.DataFrame, pd.DataFrame, PostChangeGaps]:
    parts: list[pd.DataFrame] = []
    for i, (a, b) in enumerate(zip(d_f, d_l, strict=True)):
        parts += _event(i, a, b)
    df = pd.concat(parts, ignore_index=True)
    rec = lane_change_gaps(df, (), mainline_lanes=(1, 2), dt_s=DT).records
    mask = rec["veh_id"].astype(str).str.match(r"^c\d+$").to_numpy()
    assert mask.sum() == len(d_f)
    res = post_change_gaps(df, rec, changes=mask, dt_s=DT, equilibrium=(2.0, 1.2))
    return df, rec[mask], res


class TestPartnerSpeed:
    def test_front_minus_rear_on_both_sides(self) -> None:
        _, rec, res = _run([2.0], [3.0])
        fol = res.values["follower"]
        lead = res.values["leader"]
        assert "rel_speed_ms" in fol and "rel_speed_ms" in lead
        assert fol["rel_speed_ms"][0, 0] == pytest.approx(2.0)  # the changer's minus F's
        assert lead["rel_speed_ms"][0, 0] == pytest.approx(3.0)  # L's minus the changer's
        # minus the record's closing speeds at the change
        assert fol["rel_speed_ms"][0, 0] == pytest.approx(-float(rec["lag_closing_ms"].iloc[0]))
        assert lead["rel_speed_ms"][0, 0] == pytest.approx(-float(rec["lead_closing_ms"].iloc[0]))
        # read exactly where the side was read, constant speeds at every offset
        for vals, expect in ((fol, 2.0), (lead, 3.0)):
            read = np.isfinite(vals["space_gap_m"])
            np.testing.assert_array_equal(np.isfinite(vals["rel_speed_ms"]), read)
            np.testing.assert_allclose(vals["rel_speed_ms"][read], expect)
            np.testing.assert_allclose(
                vals["rel_speed_ms"][read], (vals["front_v_ms"] - vals["rear_v_ms"])[read]
            )
        # the side's gap opens at the partner speed
        off = res.offsets_s
        k5 = int(np.flatnonzero(off == 5.0)[0])
        assert fol["space_gap_m"][0, k5] - fol["space_gap_m"][0, 0] == pytest.approx(5 * 2.0)
        assert lead["space_gap_m"][0, k5] - lead["space_gap_m"][0, 0] == pytest.approx(5 * 3.0)

    def test_negative_when_the_gap_closes(self) -> None:
        _, _, res = _run([-1.5], [-0.5])
        assert res.values["follower"]["rel_speed_ms"][0, 0] == pytest.approx(-1.5)
        assert res.values["leader"]["rel_speed_ms"][0, 0] == pytest.approx(-0.5)

    def test_summary_quantiles_and_the_partner_speed(self) -> None:
        d_f = [-1.0, 0.0, 1.0, 2.0, 3.0]
        d_l = [0.0, 0.5, 1.0, 1.5, 2.0]
        df, _, res = _run(d_f, d_l)
        res = with_population_ratio(res, normal_time_gaps(df, dt_s=DT), min_n=1)
        rows = summarize_relaxation(res, n_boot=0, fit_measures=())
        top = {r["side"]: r for r in rows if r["speed_class"] == "all"}
        assert set(top) == set(SIDES)
        fol, lead = top["follower"]["rel_speed_ms"], top["leader"]["rel_speed_ms"]
        assert fol["n"][0] == 5 and lead["n"][0] == 5
        assert (fol["p25"][0], fol["p50"][0], fol["p75"][0]) == pytest.approx((0.0, 1.0, 2.0))
        assert (lead["p25"][0], lead["p50"][0], lead["p75"][0]) == pytest.approx((0.5, 1.0, 1.5))
        # the partner's speed at the change: the changer on the follower side, L on the leader side
        assert top["follower"]["front_v_ms_p50"][0] == pytest.approx(V_C)
        assert top["leader"]["front_v_ms_p50"][0] == pytest.approx(V_C + 1.0)
        assert top["follower"]["rear_v_ms_p50"][0] == pytest.approx(V_C - 1.0)

    def test_additive_the_existing_keys_are_unchanged(self) -> None:
        _, _, res = _run([1.0, 2.0], [1.0, 2.0])
        rows = summarize_relaxation(res, n_boot=0)
        for r in rows:
            assert SUMMARY_KEYS_BEFORE_WP91 <= set(r)
            assert set(r) - SUMMARY_KEYS_BEFORE_WP91 == {"front_v_ms_p50", "rel_speed_ms"}
            assert set(r["measures"]) == set(MEASURES)
        assert not set(PARTNER_SPEEDS) & set(MEASURES)

    def test_long_form_concat_and_a_result_without_the_array(self) -> None:
        _, _, res = _run([2.0], [3.0])
        long = res.to_long()
        assert "rel_speed_ms" in long.columns
        np.testing.assert_allclose(long["rel_speed_ms"], long["front_v_ms"] - long["rear_v_ms"])
        both = concat_results([res, res])
        assert both.values["follower"]["rel_speed_ms"].shape[0] == 2
        # a result made before WP-91 (no partner-speed array) is summarized from its two speeds
        legacy = PostChangeGaps(
            res.events,
            res.offsets_s,
            {s: {k: v for k, v in res.values[s].items() if k != "rel_speed_ms"} for s in SIDES},
            res.counts,
            res.parameters,
        )
        a = summarize_relaxation(res, n_boot=0, fit_measures=())
        b = summarize_relaxation(legacy, n_boot=0, fit_measures=())
        assert [r["rel_speed_ms"] for r in a] == [r["rel_speed_ms"] for r in b]
        assert "rel_speed_ms" not in legacy.to_long().columns
