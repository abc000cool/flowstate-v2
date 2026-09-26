"""Synthetic-frame tests for coverage thinning (calibration.thinning, WP-91).

Every frame is built by hand: vehicles on one shared sampling grid, each with a
track of known extent. The tests check what the two models promise: the kept
vehicle-time fraction, determinism by seed (and independence from the input's
row order), distinct fragment ids that each map to one contiguous piece of one
vehicle, and that no row is invented or altered except ``veh_id``. The
fragment-duration model is checked against the committed I-24 MOTION
statistics it is built from (docs/I24_DATA.md §2).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_relaxation import normal_time_gaps
from calibration.thinning import (
    I24_FRAGMENT_HISTOGRAM,
    I24_FRAGMENT_MEDIAN_S,
    I24_FRAGMENT_P90_S,
    I24_FRAGMENT_SHARE_GE_30S,
    I24_FRAGMENT_SIGMA,
    THINNING_MODELS,
    ThinnedFrame,
    lognormal_bin_shares,
    lognormal_sigma,
    thin_fragments,
    thin_frame,
    thin_vehicles,
)


def _frame(
    n_veh: int,
    *,
    dt: float = 0.5,
    dur: tuple[float, float] = (200.0, 600.0),
    seed: int = 0,
    lanes: int = 3,
) -> pd.DataFrame:
    """``n_veh`` vehicles on a ``dt`` grid, each with its own start, duration, speed and lane."""
    rng = np.random.default_rng(seed)
    parts = []
    for i in range(n_veh):
        k0 = int(rng.integers(0, 200))
        n = int(rng.uniform(*dur) / dt)
        t = np.round((k0 + np.arange(n)) * dt, 6)
        v = float(rng.uniform(5.0, 25.0))
        parts.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": f"v{i:03d}",
                    "x": 3.0 * i + v * (t - t[0]),
                    "lane": int(rng.integers(1, lanes + 1)),
                    "v": v,
                    "length": 4.0 + 0.01 * i,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def _assert_no_row_invented(df: pd.DataFrame, th: ThinnedFrame) -> None:
    """Every kept row is an input row, unchanged except ``veh_id``; ids map back to the source."""
    rows = th.source_rows
    assert rows.dtype == np.int64
    assert np.all(np.diff(rows) > 0)  # unique, in input order
    assert len(th.frame) == rows.size
    src = df.iloc[rows].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        th.frame.drop(columns=["veh_id"]), src.drop(columns=["veh_id"]), check_exact=True
    )
    to_source = dict(
        zip(th.fragments["fragment_id"], th.fragments["source_id"].astype(str), strict=True)
    )
    mapped = th.frame["veh_id"].map(to_source)
    assert mapped.notna().all()
    np.testing.assert_array_equal(mapped.to_numpy(), src["veh_id"].astype(str).to_numpy())


class TestFragmentModelParameters:
    def test_sigma_from_the_committed_statistics(self) -> None:
        assert I24_FRAGMENT_SHARE_GE_30S == pytest.approx(62_784 / 576_511)
        assert I24_FRAGMENT_SIGMA == pytest.approx(0.8996, abs=1e-4)
        p90 = I24_FRAGMENT_MEDIAN_S * math.exp(I24_FRAGMENT_SIGMA * 1.2815515655446004)
        assert p90 == pytest.approx(I24_FRAGMENT_P90_S, abs=0.1)

    def test_reproduces_the_committed_histogram(self) -> None:
        shares = lognormal_bin_shares()
        assert len(shares) == len(I24_FRAGMENT_HISTOGRAM)
        for observed, model in shares:
            assert abs(observed - model) < 0.02
        assert sum(m for _, m in shares) == pytest.approx(1.0)

    def test_lognormal_sigma_inputs(self) -> None:
        assert lognormal_sigma(10.0, 20.0, 0.1) == pytest.approx(
            math.log(2.0) / 1.2815515655446004, rel=1e-9
        )
        with pytest.raises(ValueError):
            lognormal_sigma(10.0, 5.0, 0.1)
        with pytest.raises(ValueError):
            lognormal_sigma(10.0, 20.0, 0.6)


class TestVehicleLevel:
    def test_keeps_whole_vehicles_with_their_ids(self) -> None:
        df = _frame(150)
        th = thin_vehicles(df, 0.5, seed=3)
        assert th.counts["n_vehicles"] == 150
        assert th.counts["n_vehicles_kept"] == 75
        assert th.frame["veh_id"].nunique() == 75
        # a kept vehicle keeps every one of its rows
        kept = set(th.frame["veh_id"])
        n_rows_kept = int(df["veh_id"].isin(kept).sum())
        assert len(th.frame) == n_rows_kept
        assert th.kept_time_fraction == pytest.approx(n_rows_kept / len(df))
        assert abs(th.kept_time_fraction - 0.5) < 0.1
        assert set(th.fragments["fragment_id"]) == kept
        assert th.fragments["at_track_start"].all() and th.fragments["at_track_end"].all()
        _assert_no_row_invented(df, th)

    def test_fraction_one_is_the_identity(self) -> None:
        df = _frame(20)
        th = thin_vehicles(df, 1.0, seed=1)
        pd.testing.assert_frame_equal(th.frame, df)
        assert th.kept_time_fraction == 1.0

    def test_integer_ids_are_kept_as_they_are(self) -> None:
        df = _frame(12)
        df["veh_id"] = df["veh_id"].str[1:].astype(int)
        th = thin_vehicles(df, 0.5, seed=2)
        assert set(th.fragments["fragment_id"]) == set(th.frame["veh_id"])
        assert all(isinstance(i, int | np.integer) for i in th.fragments["fragment_id"])
        assert set(th.fragments["source_id"]) == {str(i) for i in th.frame["veh_id"]}
        fr = thin_fragments(df, 0.5, seed=2)
        _assert_no_row_invented(df, fr)

    def test_rounding_and_the_floor_of_one(self) -> None:
        df = _frame(9)
        assert thin_vehicles(df, 0.65, seed=0).counts["n_vehicles_kept"] == 6  # round(5.85)
        assert thin_vehicles(df, 0.01, seed=0).counts["n_vehicles_kept"] == 1


class TestFragmentLevel:
    @pytest.mark.parametrize("fraction", [0.5, 0.65])
    def test_kept_fraction_within_tolerance(self, fraction: float) -> None:
        df = _frame(150, dur=(500.0, 700.0))
        th = thin_fragments(df, fraction, seed=11)
        assert abs(th.kept_time_fraction - fraction) < 0.03
        assert th.counts["n_rows_kept"] == len(th.frame)
        assert th.counts["n_fragments"] > 3 * th.counts["n_vehicles"]
        _assert_no_row_invented(df, th)

    def test_fraction_one_only_splits_the_tracks(self) -> None:
        dt = 0.5
        df = _frame(40, dt=dt)
        th = thin_fragments(df, 1.0, seed=2)
        assert th.kept_time_fraction == 1.0
        assert th.counts["n_fragments"] > th.counts["n_vehicles"]
        _assert_no_row_invented(df, th)
        # consecutive fragments of one vehicle are contiguous on the grid
        for _, g in th.fragments.sort_values("t_start").groupby("source_id"):
            starts = g["t_start"].to_numpy()
            ends = g["t_end"].to_numpy()
            np.testing.assert_allclose(starts[1:] - ends[:-1], dt, atol=1e-9)
            assert g["at_track_start"].iloc[0] and g["at_track_end"].iloc[-1]
        # a per-slot measure without identity reads the same table
        a = normal_time_gaps(df, dt_s=dt, min_speed_ms=0.5)
        b = normal_time_gaps(th.frame, dt_s=dt, min_speed_ms=0.5)
        np.testing.assert_array_equal(a.counts, b.counts)

    def test_fragment_ids_are_distinct_and_each_is_one_contiguous_piece(self) -> None:
        dt = 0.5
        df = _frame(60, dt=dt)
        th = thin_fragments(df, 0.5, seed=5, id_prefix="p1-frag-")
        frags = th.fragments
        assert frags["fragment_id"].is_unique
        assert set(th.frame["veh_id"]) == set(frags["fragment_id"])
        assert not set(frags["fragment_id"]) & set(df["veh_id"])
        assert all(str(i).startswith("p1-frag-") for i in frags["fragment_id"])
        src_of = th.frame["veh_id"].map(
            dict(zip(frags["fragment_id"], frags["source_id"], strict=True))
        )
        np.testing.assert_array_equal(
            src_of.to_numpy(), df["veh_id"].iloc[th.source_rows].to_numpy()
        )
        for fid, g in th.frame.groupby("veh_id"):
            steps = np.diff(np.sort(g["t"].to_numpy()))
            assert np.allclose(steps, dt), fid  # no hole inside a fragment
        by_id = frags.set_index("fragment_id")
        sizes = th.frame.groupby("veh_id").size()
        np.testing.assert_array_equal(by_id.loc[sizes.index, "n_rows"].to_numpy(), sizes.to_numpy())
        # two fragments of one vehicle never overlap in time
        for _, g in frags.sort_values("t_start").groupby("source_id"):
            assert np.all(g["t_start"].to_numpy()[1:] > g["t_end"].to_numpy()[:-1])

    def test_deterministic_by_seed_and_independent_of_row_order(self) -> None:
        df = _frame(50)
        a = thin_fragments(df, 0.6, seed=7)
        b = thin_fragments(df, 0.6, seed=7)
        pd.testing.assert_frame_equal(a.frame, b.frame)
        pd.testing.assert_frame_equal(a.fragments, b.fragments)
        np.testing.assert_array_equal(a.source_rows, b.source_rows)
        c = thin_fragments(df, 0.6, seed=8)
        assert not a.frame["t"].equals(c.frame["t"]) or not a.frame["veh_id"].equals(
            c.frame["veh_id"]
        )
        shuffled = df.sample(frac=1.0, random_state=4).reset_index(drop=True)
        d = thin_fragments(shuffled, 0.6, seed=7)
        key = ["veh_id", "t"]
        pd.testing.assert_frame_equal(
            a.frame.sort_values(key).reset_index(drop=True),
            d.frame.sort_values(key).reset_index(drop=True),
        )
        v1 = thin_vehicles(df, 0.5, seed=7)
        v2 = thin_vehicles(shuffled, 0.5, seed=7)
        assert set(v1.frame["veh_id"]) == set(v2.frame["veh_id"])
        assert set(v1.frame["veh_id"]) != set(thin_vehicles(df, 0.5, seed=9).frame["veh_id"])

    def test_untruncated_fragments_have_i24_durations(self) -> None:
        dt = 0.1
        df = _frame(30, dt=dt, dur=(1400.0, 1500.0))
        th = thin_fragments(df, 0.5, seed=13)
        inner = th.fragments[~th.fragments["at_track_start"] & ~th.fragments["at_track_end"]]
        assert len(inner) > 500
        # a fragment's sampled span is its spell less at most one sampling interval
        dur = inner["duration_s"].to_numpy() + 0.5 * dt
        assert float(np.median(dur)) == pytest.approx(I24_FRAGMENT_MEDIAN_S, rel=0.1)
        assert float(np.mean(dur >= 30.0)) == pytest.approx(I24_FRAGMENT_SHARE_GE_30S, abs=0.03)

    def test_parameters_are_recorded(self) -> None:
        th = thin_fragments(_frame(5), 0.5, seed=1)
        p = th.parameters
        assert p["model"] == "fragment" and p["fraction"] == 0.5 and p["seed"] == 1
        assert p["fragment_median_s"] == I24_FRAGMENT_MEDIAN_S
        assert p["gap_mean_s"] == pytest.approx(p["fragment_mean_s"])  # F = 0.5
        assert p["burn_in_s"] >= 300.0


class TestInputs:
    def test_dispatch(self) -> None:
        df = _frame(10)
        assert set(THINNING_MODELS) == {"vehicle", "fragment"}
        pd.testing.assert_frame_equal(
            thin_frame(df, "vehicle", 0.5, seed=1).frame, thin_vehicles(df, 0.5, seed=1).frame
        )
        opts = {"fragment_median_s": 5.0}
        pd.testing.assert_frame_equal(
            thin_frame(df, "fragment", 0.5, seed=1, options=opts).frame,
            thin_fragments(df, 0.5, seed=1, fragment_median_s=5.0).frame,
        )
        with pytest.raises(ValueError, match="model"):
            thin_frame(df, "lane", 0.5, seed=1)
        with pytest.raises(ValueError, match="no options"):
            thin_frame(df, "vehicle", 0.5, seed=1, options=opts)

    @pytest.mark.parametrize("fraction", [0.0, -0.1, 1.2, math.nan])
    def test_bad_fraction(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="fraction"):
            thin_vehicles(_frame(3), fraction, seed=0)
        with pytest.raises(ValueError, match="fraction"):
            thin_fragments(_frame(3), fraction, seed=0)

    def test_missing_columns_and_bad_scales(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            thin_vehicles(pd.DataFrame({"t": [0.0]}), 0.5, seed=0)
        with pytest.raises(ValueError, match="positive"):
            thin_fragments(_frame(3), 0.5, seed=0, fragment_sigma=0.0)
        with pytest.raises(ValueError, match="gap_sigma"):
            thin_fragments(_frame(3), 0.5, seed=0, gap_sigma=-1.0)

    def test_empty_frame(self) -> None:
        empty = _frame(1).iloc[0:0]
        for th in (thin_vehicles(empty, 0.5, seed=0), thin_fragments(empty, 0.5, seed=0)):
            assert len(th.frame) == 0 and len(th.fragments) == 0
            assert th.counts["n_rows"] == 0 and math.isnan(th.kept_time_fraction)
