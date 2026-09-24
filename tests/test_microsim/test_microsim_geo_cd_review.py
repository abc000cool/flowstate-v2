"""Adversarial checks of the 2026-09-24 ramp discovery (``microsim.geo``).

Two layers. :class:`TestCdPathSearch` drives :func:`geo._cd_path` on a
duck-typed net (edge ids, types, lengths and outgoing lists — all the search
reads) so loops, bounds and dominance are exact arithmetic. The rest compiles
hand-written OSM through real ``netconvert`` like ``test_microsim_geo_ramps``
(whose fixture helpers are reused) and runs :func:`geo.ramps_for_chain`:

* a link loop returning to the split's own node, or upstream of it, is not a
  collector–distributor road;
* ``oneway=-1`` on a joining arterial is resolved by netconvert before
  discovery sees it, so the reversed way is the same entrance;
* two splits feeding one C-D road claim each link edge once;
* discovery is deterministic and leaves the net untouched.
"""

from __future__ import annotations

import importlib.util
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from microsim import geo

_spec = importlib.util.spec_from_file_location(
    "_geo_ramp_fixtures", Path(__file__).with_name("test_microsim_geo_ramps.py")
)
assert _spec is not None and _spec.loader is not None
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

LINK = "highway.motorway_link"
MAINLINE_TYPE = "highway.motorway"


class _Edge:
    def __init__(self, eid: str, etype: str, length: float) -> None:
        self._id, self._type, self._length = eid, etype, length
        self.out: list[_Edge] = []

    def getID(self) -> str:
        return self._id

    def getType(self) -> str:
        return self._type

    def getLength(self) -> float:
        return self._length

    def getOutgoing(self) -> list[_Edge]:
        return list(self.out)


class _Net:
    """Just enough of ``sumolib.net.Net`` for ``_cd_path``."""

    def __init__(self, chain: list[str]) -> None:
        self.edges: dict[str, _Edge] = {}
        for eid in chain:
            self.add(eid, MAINLINE_TYPE, 500.0)
        for a, b in pairwise(chain):
            self.link(a, b)
        self.chain_index = {eid: i for i, eid in enumerate(chain)}

    def add(self, eid: str, etype: str = LINK, length: float = 100.0) -> _Edge:
        self.edges[eid] = _Edge(eid, etype, length)
        return self.edges[eid]

    def link(self, a: str, b: str) -> None:
        self.edges[a].out.append(self.edges[b])

    def getEdge(self, eid: str) -> _Edge:
        return self.edges[eid]

    def search(self, start: str, split_index: int, **kw: Any) -> tuple[list[str], str] | None:
        opts: dict[str, Any] = {"max_length_m": 3000.0, "max_edges": 12, "used": set()}
        opts.update(kw)
        return geo._cd_path(
            self,
            self.edges[start],
            chain_index=self.chain_index,
            split_index=split_index,
            link_types={LINK},
            **opts,
        )


class TestCdPathSearch:
    def test_a_loop_returning_upstream_is_not_a_rejoin(self) -> None:
        net = _Net(["c0", "c1", "c2"])
        net.add("L1"), net.add("L2")
        net.link("c1", "L1"), net.link("L1", "L2"), net.link("L2", "c0")
        assert net.search("L1", split_index=1) is None
        net.link("L2", "c1")  # back into the split edge itself
        assert net.search("L1", split_index=1) is None

    def test_a_loop_returning_to_the_split_node_is_not_a_rejoin(self) -> None:
        # chain[1] starts at the node chain[0] ends on: a path feeding it has
        # come straight back to the split, a zero-length "C-D road".
        net = _Net(["c0", "c1", "c2"])
        net.add("L1"), net.add("L2")
        net.link("c0", "L1"), net.link("L1", "L2"), net.link("L2", "c1")
        assert net.search("L1", split_index=0) is None
        net.link("L2", "c2")  # the same road also reaching the next node: that one counts
        assert net.search("L1", split_index=0) == (["L1", "L2"], "c2")

    def test_the_length_bound_is_measured_along_the_path(self) -> None:
        net = _Net(["c0", "c1", "c2"])
        for k in range(4):
            net.add(f"L{k}", length=900.0)  # 3600 m walked, ~1000 m as the crow flies
        net.link("c0", "L0"), net.link("L0", "L1"), net.link("L1", "L2"), net.link("L2", "L3")
        net.link("L3", "c2")
        assert net.search("L0", split_index=0, max_length_m=3000.0) is None
        assert net.search("L0", split_index=0, max_length_m=3600.0) == (
            ["L0", "L1", "L2", "L3"],
            "c2",
        )

    def test_a_short_but_edge_heavy_route_does_not_hide_an_admissible_one(self) -> None:
        # From S two routes reach J: eleven 50 m pieces (13 edges to J, over
        # max_edges) and two 600 m pieces (4 edges). J rejoins the chain.
        # The metre-shorter route used to set the dominance record for J and
        # prune the admissible one, so no C-D road was found.
        net = _Net(["c0", "c1", "c2"])
        net.add("S", length=10.0), net.link("c0", "S")
        prev = "S"
        for k in range(11):
            net.add(f"A{k}", length=50.0), net.link(prev, f"A{k}")
            prev = f"A{k}"
        net.add("B0", length=600.0), net.add("B1", length=600.0)
        net.link("S", "B0"), net.link("B0", "B1")
        net.add("J", length=10.0), net.link(prev, "J"), net.link("B1", "J"), net.link("J", "c2")
        assert net.search("S", split_index=0, max_edges=12) == (["S", "B0", "B1", "J"], "c2")
        # with room for the short pieces, the metre-shortest route wins
        assert net.search("S", split_index=0, max_edges=13) == (
            ["S", *(f"A{k}" for k in range(11)), "J"],
            "c2",
        )

    def test_the_edge_bound_counts_the_start_edge(self) -> None:
        net = _Net(["c0", "c1", "c2"])
        ids = [f"L{k}" for k in range(5)]
        for eid in ids:
            net.add(eid)
        net.link("c0", ids[0])
        for a, b in pairwise(ids):
            net.link(a, b)
        net.link(ids[-1], "c2")
        assert net.search("L0", split_index=0, max_edges=5) == (ids, "c2")
        assert net.search("L0", split_index=0, max_edges=4) is None

    def test_links_already_claimed_are_not_walked(self) -> None:
        net = _Net(["c0", "c1", "c2"])
        net.add("L1"), net.add("L2")
        net.link("c0", "L1"), net.link("L1", "L2"), net.link("L2", "c2")
        assert net.search("L1", split_index=0) == (["L1", "L2"], "c2")
        assert net.search("L1", split_index=0, used={"L2"}) is None


def _loop_to_own_node_osm() -> str:
    """Three links leaving node 2 and coming back to it (a triangle)."""
    nodes, ways = fx._mainline()
    nodes += [
        fx._node(50, fx.LAT - 0.0008, fx.LONS[1] + 0.001),
        fx._node(51, fx.LAT - 0.0012, fx.LONS[1] - 0.001),
    ]
    ways += [
        fx._way(500, [2, 50], "motorway_link", 1, fx.ONEWAY),
        fx._way(501, [50, 51], "motorway_link", 1, fx.ONEWAY),
        fx._way(502, [51, 2], "motorway_link", 1, fx.ONEWAY),
    ]
    return fx._document(nodes, ways)


def _loop_to_upstream_node_osm() -> str:
    """Links leaving node 3 and returning to node 2."""
    nodes, ways = fx._mainline()
    nodes.append(fx._node(50, fx.LAT - 0.0008, fx.LONS[1] + 0.003))
    ways += [
        fx._way(500, [3, 50], "motorway_link", 1, fx.ONEWAY),
        fx._way(501, [50, 2], "motorway_link", 1, fx.ONEWAY),
    ]
    return fx._document(nodes, ways)


def _reversed_oneway_osm() -> str:
    """The lane-add fixture with the joining way drawn backwards and ``oneway=-1``."""
    nodes, ways = fx._mainline((2, 2, 3, 3, 3))
    nodes.append(fx._node(30, fx.LAT - 0.0006, fx.LONS[2] - 0.002))
    ways.append(fx._way(300, [3, 30], "secondary", 1, '<tag k="oneway" v="-1"/>'))
    return fx._document(nodes, ways)


def _shared_cd_osm() -> str:
    """Two splits (nodes 2 and 3) feeding one C-D road that rejoins at node 5."""
    nodes, ways = fx._mainline()
    nodes += [
        fx._node(50, fx.LAT - 0.0006, fx.LONS[1] + 0.002),
        fx._node(53, fx.LAT - 0.0006, fx.LONS[2] + 0.002),
        fx._node(51, fx.LAT - 0.0006, fx.LONS[3] + 0.002),
    ]
    ways += [
        fx._way(500, [2, 50], "motorway_link", 1, fx.ONEWAY),
        fx._way(501, [50, 51], "motorway_link", 1, fx.ONEWAY),
        fx._way(502, [51, 5], "motorway_link", 1, fx.ONEWAY),
        fx._way(520, [3, 53], "motorway_link", 1, fx.ONEWAY),
        fx._way(521, [53, 51], "motorway_link", 1, fx.ONEWAY),
    ]
    return fx._document(nodes, ways)


def _discover(osm_text: str, workdir: Path) -> tuple[Any, list[str], list[geo.RampCandidate]]:
    _, net = fx._compile(osm_text, workdir)
    chain = geo.mainline_chain(net, 90.0)
    assert chain == fx.MAINLINE
    return net, chain, geo.ramps_for_chain(net, chain)


@pytest.mark.integration
class TestLoopsAreNotCdRoads:
    def test_a_loop_back_to_the_split_node_is_one_exit(self, tmp_path: Path) -> None:
        _, _, ramps = _discover(_loop_to_own_node_osm(), tmp_path)
        assert [(r.kind, r.edges, r.attach_edge, r.discovery) for r in ramps] == [
            ("off", ("500", "501", "502"), "100", "motorway_link")
        ]
        assert not any(r.cd_road or r.cd_pair for r in ramps)

    def test_a_loop_back_upstream_is_one_exit(self, tmp_path: Path) -> None:
        _, _, ramps = _discover(_loop_to_upstream_node_osm(), tmp_path)
        assert [(r.kind, r.edges, r.attach_edge, r.discovery) for r in ramps] == [
            ("off", ("500", "501"), "101", "motorway_link")
        ]


@pytest.mark.integration
class TestNonLinkJoinDirections:
    def test_a_reversed_oneway_arterial_is_the_same_entrance(self, tmp_path: Path) -> None:
        # netconvert builds the edge against the way's node order, so the
        # entrance arrives as "-300" and is judged on its driving heading.
        _, _, ramps = _discover(_reversed_oneway_osm(), tmp_path)
        assert [(r.kind, r.edges, r.attach_edge, r.discovery) for r in ramps] == [
            ("on", ("-300",), "102", "lane_add")
        ]


@pytest.mark.integration
class TestSharedLinksClaimedOnce:
    def test_a_second_split_into_the_cd_road_is_an_exit_and_no_link_is_claimed_twice(
        self, tmp_path: Path
    ) -> None:
        _, _, ramps = _discover(_shared_cd_osm(), tmp_path)
        claimed = [e for r in ramps for e in r.edges]
        assert len(claimed) == len(set(claimed))
        assert set(claimed) == {"500", "501", "502", "520", "521"}
        by_first = {r.edges[0]: r for r in ramps}
        assert by_first["500"].cd_road and by_first["500"].edges == ("500", "501")
        assert by_first["502"].cd_road and by_first["502"].kind == "on"
        # the second split's road merges into the C-D road: it is a plain exit
        # and its joining leg an inventory-only attachment of the pair
        assert (by_first["520"].kind, by_first["520"].cd_road, by_first["520"].discovery) == (
            "off",
            False,
            "motorway_link",
        )
        assert (by_first["521"].kind, by_first["521"].attach_via_cd) == ("on", "500")


@pytest.mark.integration
class TestDeterminism:
    def test_discovery_twice_on_one_net_is_identical_and_leaves_it_unchanged(
        self, tmp_path: Path
    ) -> None:
        _, net = fx._compile(fx.cd_road_osm(), tmp_path)

        def snapshot() -> list[tuple[Any, ...]]:
            return [
                (
                    e.getID(),
                    e.getType(),
                    e.getLength(),
                    tuple(e.getShape()),
                    tuple(o.getID() for o in e.getOutgoing()),
                    tuple(o.getID() for o in e.getIncoming()),
                )
                for e in net.getEdges()
            ]

        before = snapshot()
        first = geo.ramps_for_chain(net, geo.mainline_chain(net, 90.0))
        second = geo.ramps_for_chain(net, geo.mainline_chain(net, 90.0))
        assert first == second and len(first) == 3
        assert snapshot() == before
