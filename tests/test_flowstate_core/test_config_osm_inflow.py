"""OSMNetwork inflow steps are validated like CorridorNetwork's."""

from __future__ import annotations

import pytest

from flowstate_core.config import OSMNetwork


def _net(inflow: list[tuple[float, float]]) -> OSMNetwork:
    return OSMNetwork(osm_file="data/osm/i24_nashville.osm", corridor_edges=["1"], inflow=inflow)


def test_negative_inflow_rejected() -> None:
    with pytest.raises(ValueError, match="inflow must be >= 0"):
        _net([(0.0, -0.5)])


def test_unordered_inflow_rejected() -> None:
    with pytest.raises(ValueError, match="ordered"):
        _net([(600.0, 0.5), (0.0, 0.4)])


def test_ordered_non_negative_inflow_accepted() -> None:
    assert _net([(0.0, 0.4), (600.0, 0.5)]).inflow[1] == (600.0, 0.5)
