"""Synthetic tests for the heavy-share and auxiliary-lane helpers.

``calibration.lanechange.heavy_share`` and
``calibration.lanechange.aux_lane_fragments`` back
``scripts/i24_heavy_share.py --by-lane`` (``artifacts/i24_heavy_by_lane.json``,
docs/MERGE_ROUND6_PLAN.md §2.4). Every case here is a hand-built frame with
the answer computed by hand.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from calibration.lanechange import (
    DEFAULT_HEAVY_CLASSES,
    aux_lane_fragments,
    heavy_share,
)


def _samples(spec: dict[str, tuple[int, int]]) -> pd.DataFrame:
    """``{veh_id: (cls, n_samples)}`` → a sample frame."""
    rows = [{"veh_id": v, "cls": cls} for v, (cls, n) in spec.items() for _ in range(n)]
    return pd.DataFrame(rows)


def test_heavy_share_counts_fragments_once_and_samples_all() -> None:
    # Three cars (2, 5, 3 samples) and one semi with 10 samples: 1 of 4
    # fragments is heavy (25%) but 10 of 20 samples are (50%).
    df = _samples({"a": (0, 2), "b": (1, 5), "c": (3, 3), "d": (4, 10)})
    got = heavy_share(df)
    assert got.n_fragments == 4
    assert got.n_samples == 20
    assert got.n_heavy_fragments == 1
    assert got.n_heavy_samples == 10
    assert got.share_fragments == pytest.approx(0.25)
    assert got.share_vehicle_time == pytest.approx(0.5)
    assert got.fragments_by_class == {0: 1, 1: 1, 3: 1, 4: 1}
    assert got.samples_by_class == {0: 2, 1: 5, 3: 3, 4: 10}
    assert got.n_fragments_mixed_class == 0


def test_heavy_share_both_heavy_classes_and_json_form() -> None:
    df = _samples({"a": (0, 4), "s": (4, 3), "t": (5, 1)})
    got = heavy_share(df)
    assert DEFAULT_HEAVY_CLASSES == (4, 5)
    assert got.n_heavy_fragments == 2
    assert got.share_fragments == pytest.approx(2 / 3)
    assert got.share_vehicle_time == pytest.approx(0.5)
    d = got.to_dict()
    assert d["fragments_by_class"] == {"0": 1, "4": 1, "5": 1}
    assert d["share_vehicle_time"] == pytest.approx(0.5)
    # Only class 4 heavy: the truck stops counting on both sides.
    only_semi = heavy_share(df, heavy_classes=(4,))
    assert only_semi.n_heavy_fragments == 1
    assert only_semi.share_vehicle_time == pytest.approx(3 / 8)


def test_heavy_share_is_invariant_to_class_uniform_thinning() -> None:
    rng = np.random.default_rng(20260917)
    n_car, n_heavy = 400, 40
    ids = [f"c{i}" for i in range(n_car)] + [f"h{i}" for i in range(n_heavy)]
    cls = [0] * n_car + [4] * n_heavy
    reps = rng.integers(3, 12, size=len(ids))
    df = pd.DataFrame(
        {
            "veh_id": np.repeat(ids, reps),
            "cls": np.repeat(cls, reps),
        }
    )
    full = heavy_share(df)
    assert full.share_fragments == pytest.approx(n_heavy / (n_car + n_heavy))
    # Drop half the fragments with a probability that does not depend on the
    # class: the share of fragments survives to sampling error, which is the
    # coverage assumption the artifact's note names.
    keep = {v for v in ids if rng.random() < 0.5}
    thinned = heavy_share(df[df["veh_id"].isin(keep)])
    assert thinned.n_fragments < full.n_fragments
    assert thinned.share_fragments == pytest.approx(full.share_fragments, abs=0.05)
    # A class-dependent thinning biases it upward, the documented direction.
    biased = heavy_share(df[(df["cls"] == 4) | df["veh_id"].isin(keep)])
    assert biased.share_fragments > full.share_fragments + 0.05


def test_heavy_share_empty_and_mixed_class() -> None:
    empty = heavy_share(pd.DataFrame({"veh_id": [], "cls": []}))
    assert empty.n_fragments == 0
    assert math.isnan(empty.share_fragments)
    assert empty.to_dict()["share_fragments"] is None
    mixed = heavy_share(pd.DataFrame({"veh_id": ["a", "a", "b"], "cls": [0, 4, 0]}))
    assert mixed.n_fragments == 2
    assert mixed.n_fragments_mixed_class == 1
    assert mixed.n_heavy_fragments == 1
    with pytest.raises(ValueError, match="heavy_classes"):
        heavy_share(pd.DataFrame({"veh_id": ["a"], "cls": [0]}), heavy_classes=())
    with pytest.raises(ValueError, match="missing columns"):
        heavy_share(pd.DataFrame({"veh_id": ["a"]}))


def _track(veh: str, lanes: list[int], x0: float = 0.0, t0: float = 0.0) -> pd.DataFrame:
    """One fragment: one sample per entry of ``lanes``, 100 m and 1 s apart."""
    n = len(lanes)
    return pd.DataFrame(
        {
            "t": t0 + np.arange(n, dtype=float),
            "veh_id": veh,
            "x": x0 + 100.0 * np.arange(n, dtype=float),
            "lane": lanes,
        }
    )


def test_aux_lane_fragments_separates_origin_from_mere_use() -> None:
    aux_span = (750.0, 1950.0)
    frames = [
        # Starts on the ramp lane inside the span, merges to lane 4: origin.
        _track("ramp", [5, 5, 5, 4], x0=800.0, t0=10.0),
        # Mainline fragment upstream of the lane that later drifts into it:
        # on_aux but not origin.
        _track("drifter", [4, 4, 5, 5], x0=500.0, t0=10.0),
        # Never touches the lane.
        _track("mainline", [3, 3, 3, 3], x0=500.0, t0=10.0),
        # On lane 5 but outside the span (a different auxiliary lane).
        _track("other_ramp", [5, 5], x0=3500.0, t0=10.0),
        # Starts on lane 5 upstream of the span, so not inside it at birth.
        _track("early", [5, 5, 5], x0=600.0, t0=10.0),
    ]
    df = pd.concat(frames, ignore_index=True)
    got = aux_lane_fragments(df, aux_lane=5, x_range_m=aux_span)
    assert got.on_aux == {"ramp", "drifter", "early"}
    assert got.aux_origin == {"ramp"}
    assert got.aux_lane == 5
    assert got.x_range_m == aux_span
    # Shuffling the rows cannot change the answer.
    shuffled = df.sample(frac=1.0, random_state=7).reset_index(drop=True)
    assert aux_lane_fragments(shuffled, aux_lane=5, x_range_m=aux_span) == got


def test_aux_lane_fragments_origin_depends_on_what_the_caller_passes() -> None:
    aux_span = (750.0, 1950.0)
    df = _track("drifter", [4, 4, 5, 5], x0=500.0, t0=10.0)
    # Clipping the upstream mainline samples away makes the same fragment
    # look ramp-origin: the docstring's warning, as a test.
    clipped = df[df["x"] >= 750.0]
    assert aux_lane_fragments(df, aux_lane=5, x_range_m=aux_span).aux_origin == frozenset()
    assert aux_lane_fragments(clipped, aux_lane=5, x_range_m=aux_span).aux_origin == {"drifter"}


def test_aux_lane_fragments_empty_and_bad_input() -> None:
    empty = pd.DataFrame({"t": [], "veh_id": [], "x": [], "lane": []})
    got = aux_lane_fragments(empty, aux_lane=5, x_range_m=(0.0, 1.0))
    assert got.on_aux == frozenset() and got.aux_origin == frozenset()
    with pytest.raises(ValueError, match="increasing"):
        aux_lane_fragments(empty, aux_lane=5, x_range_m=(1.0, 1.0))
    with pytest.raises(ValueError, match="missing columns"):
        aux_lane_fragments(pd.DataFrame({"t": [0.0]}), aux_lane=5, x_range_m=(0.0, 1.0))
