"""Episode schema, validation and extraction tests (calibration.episodes)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from calibration.episodes import (
    LeaderFollowerEpisode,
    extract_episodes,
    is_valid_episode,
    validate_episode,
)


def _episode(
    n: int = 80,
    dt: float = 0.5,
    gap: float = 20.0,
    v: float = 10.0,
    lane: int | None = 1,
) -> LeaderFollowerEpisode:
    t = np.arange(n) * dt
    meta: dict[str, object] = {"dataset": "synthetic", "duration_s": float(t[-1])}
    if lane is not None:
        meta["lane"] = lane
    return LeaderFollowerEpisode(
        veh_id="veh",
        t=t,
        gap_m=np.full(n, gap),
        v_follower=np.full(n, v),
        v_leader=np.full(n, v),
        metadata=meta,
    )


class TestValidateEpisode:
    def test_valid_episode_passes(self) -> None:
        validate_episode(_episode())  # 39.5 s > 30 s

    def test_too_short_rejected(self) -> None:
        ep = _episode(n=20)  # 9.5 s
        with pytest.raises(ValueError, match="duration"):
            validate_episode(ep)
        assert not is_valid_episode(ep)
        assert is_valid_episode(ep, min_duration_s=5.0)

    def test_non_uniform_dt_rejected(self) -> None:
        ep = _episode()
        ep.t = ep.t.copy()
        ep.t[40] += 0.2
        with pytest.raises(ValueError, match=r"increasing|non-uniform"):
            validate_episode(ep)

    def test_time_gap_rejected(self) -> None:
        ep = _episode()
        ep.t = ep.t.copy()
        ep.t[40:] += 5.0  # recording gap
        with pytest.raises(ValueError, match="non-uniform"):
            validate_episode(ep)

    def test_nan_rejected(self) -> None:
        ep = _episode()
        ep.gap_m = ep.gap_m.copy()
        ep.gap_m[3] = np.nan
        with pytest.raises(ValueError, match="non-finite"):
            validate_episode(ep)

    def test_nonpositive_gap_rejected(self) -> None:
        ep = _episode(gap=0.0)
        with pytest.raises(ValueError, match="gap"):
            validate_episode(ep)

    def test_negative_speed_rejected(self) -> None:
        ep = _episode()
        ep.v_leader = ep.v_leader.copy()
        ep.v_leader[0] = -0.1
        with pytest.raises(ValueError, match="speeds"):
            validate_episode(ep)

    def test_missing_lane_rejected(self) -> None:
        ep = _episode(lane=None)
        with pytest.raises(ValueError, match="lane"):
            validate_episode(ep)

    def test_mismatched_lengths_rejected(self) -> None:
        ep = _episode()
        ep.v_leader = ep.v_leader[:-1]
        with pytest.raises(ValueError, match="shape"):
            validate_episode(ep)


def _three_car_frame(lane_switch_t: float | None = None) -> pd.DataFrame:
    """Three cars in lane 1 at 10 m/s: A (front), B (middle), C (rear).

    Optionally B changes to lane 2 from ``lane_switch_t`` on, which also
    switches C's leader from B to A.
    """
    t = np.arange(81) * 0.5  # 0..40 s
    rows = []
    for veh, x0 in (("A", 200.0), ("B", 100.0), ("C", 0.0)):
        for ti in t:
            lane = 1
            if veh == "B" and lane_switch_t is not None and ti >= lane_switch_t:
                lane = 2
            rows.append(
                {
                    "t": ti,
                    "veh_id": veh,
                    "x": x0 + 10.0 * ti,
                    "lane": lane,
                    "v": 10.0,
                    "length": 5.0,
                }
            )
    return pd.DataFrame(rows)


class TestExtractEpisodes:
    def test_position_ordering_pairs_and_gap(self) -> None:
        eps = extract_episodes(_three_car_frame(), dataset="synthetic", min_duration_s=30.0)
        by_id = {ep.veh_id: ep for ep in eps}
        assert set(by_id) == {"B", "C"}  # A has no leader
        b = by_id["B"]
        assert b.metadata["leader_id"] == "A"
        assert b.metadata["dataset"] == "synthetic"
        assert b.metadata["lane"] == 1
        assert b.n == 81
        # gap = x_A - x_B - length_A = 200 - 100 - 5, constant
        np.testing.assert_allclose(b.gap_m, 95.0, rtol=1e-12)
        np.testing.assert_allclose(b.v_leader, 10.0, rtol=1e-12)
        c = by_id["C"]
        assert c.metadata["leader_id"] == "B"
        np.testing.assert_allclose(c.gap_m, 95.0, rtol=1e-12)

    def test_lane_change_cuts_episodes(self) -> None:
        eps = extract_episodes(
            _three_car_frame(lane_switch_t=20.0), dataset="synthetic", min_duration_s=15.0
        )
        b_eps = [ep for ep in eps if ep.veh_id == "B"]
        c_eps = sorted((ep for ep in eps if ep.veh_id == "C"), key=lambda e: float(e.t[0]))
        # B follows A only until its lane change (19.5 s run); alone in lane 2 after.
        assert len(b_eps) == 1
        assert b_eps[0].duration_s == pytest.approx(19.5)
        assert b_eps[0].metadata["lane"] == 1
        # C's leader flips from B to A at the change -> two episodes.
        assert len(c_eps) == 2
        assert c_eps[0].metadata["leader_id"] == "B"
        assert c_eps[1].metadata["leader_id"] == "A"
        np.testing.assert_allclose(c_eps[0].gap_m, 95.0, rtol=1e-12)
        np.testing.assert_allclose(c_eps[1].gap_m, 195.0, rtol=1e-12)  # 200 - 0 - 5

    def test_short_fragments_dropped(self) -> None:
        eps = extract_episodes(
            _three_car_frame(lane_switch_t=20.0), dataset="synthetic", min_duration_s=30.0
        )
        assert eps == []  # every run is < 30 s after the cut

    def test_all_extracted_episodes_are_valid(self) -> None:
        for ep in extract_episodes(
            _three_car_frame(lane_switch_t=20.0), dataset="synthetic", min_duration_s=15.0
        ):
            validate_episode(ep, min_duration_s=15.0)

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing columns"):
            extract_episodes(pd.DataFrame({"t": [0.0], "veh_id": ["A"]}))
