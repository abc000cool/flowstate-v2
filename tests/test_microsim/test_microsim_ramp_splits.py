"""Ramp-guess split pieces mapped onto a scenario's corridor chain."""

from __future__ import annotations

from microsim.networks import RAMP_SPLIT_OFF, RAMP_SPLIT_ON, expand_ramp_splits


def test_expand_orders_on_piece_first_and_off_piece_last() -> None:
    present = ["a", "b" + RAMP_SPLIT_ON, "b", "b" + RAMP_SPLIT_OFF, "c"]
    assert expand_ramp_splits(["a", "b", "c"], present) == present


def test_expand_is_idempotent_and_never_duplicates() -> None:
    present = ["a", "b" + RAMP_SPLIT_ON, "b", "c"]
    once = expand_ramp_splits(["a", "b", "c"], present)
    assert once == ["a", "b" + RAMP_SPLIT_ON, "b", "c"]
    assert expand_ramp_splits(once, present) == once
    # a chain that already names a piece beside its parent is not re-expanded
    assert expand_ramp_splits(["a", "b" + RAMP_SPLIT_ON, "b", "c"], present) == once


def test_expand_keeps_unsplit_and_unknown_ids() -> None:
    assert expand_ramp_splits(["x", "y"], ["x"]) == ["x", "y"]
