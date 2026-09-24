"""Ramp discovery beyond ``motorway_link`` chains (docs/ONBOARDING_MNDOT.md §7).

Three hand-written OSM fixtures on the same straight eastbound mainline (six
nodes 0.006° of longitude apart, ~510 m at 40° N), each compiled with real
``netconvert`` at raw-way granularity like the geo tests:

* **lane-add merge** — a one-way ``secondary`` road ends on the mainline node
  where the mainline goes from 2 to 3 lanes, at a shallow angle: an entrance
  the map tags on an arterial class rather than as a link.
* **crossings** — a two-way ``secondary`` bridge crossing the mainline
  through a shared node at 90° (``layer=1``, ``bridge=yes``), and a two-way
  road crossing at a shallow angle: neither may become a ramp.
* **collector–distributor road** — a link chain leaving the mainline and
  rejoining it two edges downstream, with an exit leaving the C-D road at its
  first interior node.

The last class also runs on the committed MnDOT I-94 WB extract (slow):
the White Bear Ave entrance on that corridor is the re-entry of a C-D road,
and the Mounds Blvd split is not one (it ends on an arterial the extract does
not carry).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sumolib

from microsim import geo
from microsim.networks import osm_import
from microsim.scenarios import corridor_from_bbox

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
MNDOT_OSM = REPO_ROOT / "data" / "osm" / "mndot_i94_wb_stpaul.osm"

LAT = 40.0
LONS: tuple[float, ...] = tuple(-96.0 + 0.006 * i for i in range(6))
BBOX = (39.99, -96.01, 40.01, -95.96)


def _node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat:.6f}" lon="{lon:.6f}"/>'


def _way(way_id: int, refs: list[int], highway: str, lanes: int, *extra: str) -> str:
    nds = "".join(f'<nd ref="{r}"/>' for r in refs)
    tags = [f'<tag k="highway" v="{highway}"/>', f'<tag k="lanes" v="{lanes}"/>', *extra]
    return f'  <way id="{way_id}">\n    {nds}\n    ' + "\n    ".join(tags) + "\n  </way>"


ONEWAY = '<tag k="oneway" v="yes"/>'


def _document(nodes: list[str], ways: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="hand-written-test-fixture">\n'
        + "\n".join(nodes)
        + "\n"
        + "\n".join(ways)
        + "\n</osm>\n"
    )


def _mainline(lanes: tuple[int, ...] = (3, 3, 3, 3, 3)) -> tuple[list[str], list[str]]:
    nodes = [_node(i + 1, LAT, lon) for i, lon in enumerate(LONS)]
    ways = [_way(100 + i, [i + 1, i + 2], "motorway", n, ONEWAY) for i, n in enumerate(lanes)]
    return nodes, ways


MAINLINE = ["100", "101", "102", "103", "104"]


def lane_add_osm() -> str:
    """Mainline 2 → 3 lanes at node 3; a one-way ``secondary`` ends there at ~21°."""
    nodes, ways = _mainline((2, 2, 3, 3, 3))
    nodes.append(_node(30, LAT - 0.0006, LONS[2] - 0.002))
    ways.append(_way(300, [30, 3], "secondary", 1, ONEWAY, '<tag k="name" v="Merge Road"/>'))
    return _document(nodes, ways)


def crossings_osm() -> str:
    """A bridge through node 3 at 90° and a two-way road through node 4 at ~20°."""
    nodes, ways = _mainline()
    nodes += [_node(40, LAT + 0.002, LONS[2]), _node(41, LAT - 0.002, LONS[2])]
    ways.append(
        _way(
            400, [40, 3, 41], "secondary", 2, '<tag k="layer" v="1"/>', '<tag k="bridge" v="yes"/>'
        )
    )
    nodes += [_node(42, LAT - 0.001, LONS[3] - 0.004), _node(43, LAT + 0.001, LONS[3] + 0.004)]
    ways.append(_way(401, [42, 4, 43], "secondary", 2))
    return _document(nodes, ways)


def cd_road_osm() -> str:
    """A link chain leaving at node 2 (end of way 100) and rejoining at node 5 (start of
    way 104), with an exit leaving the C-D road at node 50. Street names are not kept by
    the import, so the candidates carry none."""
    nodes, ways = _mainline()
    nodes += [
        _node(50, LAT - 0.0006, LONS[1] + 0.002),
        _node(51, LAT - 0.0006, LONS[3] + 0.002),
        _node(52, LAT - 0.0015, LONS[2]),
    ]
    ways += [
        _way(500, [2, 50], "motorway_link", 1, ONEWAY, '<tag k="name" v="CD Road"/>'),
        _way(501, [50, 51], "motorway_link", 1, ONEWAY),
        _way(502, [51, 5], "motorway_link", 1, ONEWAY),
        _way(510, [50, 52], "motorway_link", 1, ONEWAY, '<tag k="name" v="Exit Ave"/>'),
    ]
    return _document(nodes, ways)


@pytest.fixture(scope="module")
def ramps(tmp_path_factory):
    """Ramp discovery on the real MnDOT extract (netconvert on 270 KB of OSM)."""
    if not MNDOT_OSM.is_file():
        pytest.skip("MnDOT I-94 WB extract not present")
    bundle = osm_import(
        osm_file=MNDOT_OSM, workdir=tmp_path_factory.mktemp("mndot"), geometry_remove=False
    )
    net = sumolib.net.readNet(str(bundle.net_path))
    return geo.ramps_for_chain(net, geo.mainline_chain(net, 265.0))


def _compile(osm_text: str, workdir: Path) -> tuple[Path, sumolib.net.Net]:
    workdir.mkdir(parents=True, exist_ok=True)
    osm_path = workdir / "fixture.osm"
    osm_path.write_text(osm_text)
    bundle = osm_import(osm_file=osm_path, workdir=workdir / "net", geometry_remove=False)
    return osm_path, sumolib.net.readNet(str(bundle.net_path))


class TestLaneAddMerge:
    def test_arterial_join_at_the_widening_is_an_entrance(self, tmp_path):
        _, net = _compile(lane_add_osm(), tmp_path)
        chain = geo.mainline_chain(net, 90.0)
        assert chain == MAINLINE
        ramps = geo.ramps_for_chain(net, chain)
        assert [(r.kind, r.edges, r.attach_edge, r.discovery) for r in ramps] == [
            ("on", ("300",), "102", "lane_add")
        ]
        (ramp,) = ramps
        assert not ramp.cd_road and not ramp.attach_via_cd
        assert ramp.x_m == pytest.approx(geo.chain_offsets(net, chain)[2], abs=0.01)

    def test_without_a_lane_gain_the_join_is_shallow(self, tmp_path):
        text = lane_add_osm().replace('<tag k="lanes" v="2"/>', '<tag k="lanes" v="3"/>')
        _, net = _compile(text, tmp_path)
        ramps = geo.ramps_for_chain(net, geo.mainline_chain(net, 90.0))
        assert [(r.kind, r.discovery) for r in ramps] == [("on", "shallow_join")]

    def test_a_non_drivable_class_is_ignored(self, tmp_path):
        _, net = _compile(lane_add_osm(), tmp_path)
        chain = geo.mainline_chain(net, 90.0)
        assert geo.ramps_for_chain(net, chain, drivable_types=()) == []


class TestCrossingsAreNotRamps:
    def test_bridge_and_shallow_crossing_yield_no_candidates(self, tmp_path):
        _, net = _compile(crossings_osm(), tmp_path)
        chain = geo.mainline_chain(net, 90.0)
        assert chain == MAINLINE
        # The fixture only tests something if netconvert joined the roads at
        # the shared nodes; assert that it did.
        incoming = {e.getID() for eid in ("102", "103") for e in net.getEdge(eid).getIncoming()}
        assert any(i.startswith("40") for i in incoming)
        assert geo.ramps_for_chain(net, chain) == []


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    """The compiled C-D fixture, shared by the collector–distributor tests."""
    return _compile(cd_road_osm(), tmp_path_factory.mktemp("cd"))


class TestCollectorDistributor:
    def test_split_and_reentry_form_a_pair(self, compiled):
        _, net = compiled
        chain = geo.mainline_chain(net, 90.0)
        assert chain == MAINLINE
        offsets = geo.chain_offsets(net, chain)
        ramps = geo.ramps_for_chain(net, chain)
        assert len(ramps) == 3
        # Position order: split, then the exit at the road's first node, then the re-entry.
        split, exit_ramp, reentry = ramps
        assert (split.kind, split.edges, split.attach_edge) == ("off", ("500",), "100")
        assert split.cd_road and split.discovery == "cd_road" and split.cd_pair == "500"
        assert split.rejoin_edge == "104"
        assert split.rejoin_x_m == pytest.approx(offsets[4], abs=0.01)
        assert split.x_m == pytest.approx(offsets[1], abs=0.01)
        assert (reentry.kind, reentry.edges, reentry.attach_edge) == ("on", ("501", "502"), "104")
        assert reentry.cd_road and reentry.cd_pair == "500" and reentry.rejoin_edge == ""
        assert reentry.x_m == pytest.approx(split.rejoin_x_m, abs=0.01)
        # The exit leaves the C-D road, not the mainline: inventory only.
        assert (exit_ramp.kind, exit_ramp.edges, exit_ramp.attach_edge) == ("off", ("510",), "500")
        assert exit_ramp.attach_via_cd == "500" and exit_ramp.discovery == "motorway_link"
        assert exit_ramp.x_m == pytest.approx(split.x_m + net.getEdge("500").getLength(), abs=0.01)

    def test_the_pair_is_ordered_by_position(self, compiled):
        _, net = compiled
        ramps = geo.ramps_for_chain(net, geo.mainline_chain(net, 90.0))
        assert [r.x_m for r in ramps] == sorted(r.x_m for r in ramps)

    def test_a_short_bound_leaves_the_ends_as_unrelated_ramps(self, compiled):
        # Without the pairing the road is an exit stopping at the fork and an
        # entrance walked back from the rejoin — the pre-2026-09-24 reading.
        _, net = compiled
        chain = geo.mainline_chain(net, 90.0)
        ramps = geo.ramps_for_chain(net, chain, max_cd_length_m=100.0)
        assert [(r.kind, r.edges, r.cd_road, r.discovery) for r in ramps] == [
            ("off", ("500",), False, "motorway_link"),
            ("on", ("501", "502"), False, "motorway_link"),
        ]

    def test_onboarding_carries_the_pair_and_lists_the_exit(self, compiled, tmp_path):
        osm_path, _ = compiled
        build = corridor_from_bbox(
            "fixture_cd",
            BBOX,
            90.0,
            inflow=0.5,
            workdir=tmp_path / "onboard",
            osm_file=osm_path,
            duration_s=60.0,
            seed=3,
        )
        specs = build.config.network.ramps
        assert [(r.kind, r.edges, r.cd_road, r.cd_pair) for r in specs] == [
            ("off", ["500"], True, "500"),
            ("on", ["501", "502"], True, "500"),
        ]
        assert specs[0].name == "C-D split 500" and specs[1].name == "C-D re-entry 501"
        assert len(build.ramps) == 3
        via_cd = [r for r in build.ramps if r.attach_via_cd]
        assert [(r.kind, r.edges, r.attach_via_cd) for r in via_cd] == [("off", ("510",), "500")]
        split = next(r for r in build.ramps if r.cd_road and r.kind == "off")
        reentry = next(r for r in build.ramps if r.cd_road and r.kind == "on")
        assert split.rejoin_x_m == pytest.approx(reentry.x_m, abs=0.01)
        text = build.summary()
        assert "C-D pair 500" in text and "rejoins 104" in text
        assert "1 attach to a C-D road, inventory only" in text
        # The pairing survives the YAML round trip the CLI relies on.
        dumped = build.config.model_dump(mode="json")
        assert dumped["network"]["ramps"][1]["cd_pair"] == "500"


@pytest.mark.slow
@pytest.mark.skipif(not MNDOT_OSM.is_file(), reason="MnDOT I-94 WB extract not present")
class TestMnDOTExtract:
    """The committed I-94 WB St. Paul extract (motorway and motorway_link ways only)."""

    def test_white_bear_ave_is_a_cd_pair_with_its_reentry(self, ramps):
        pairs = [r for r in ramps if r.cd_road]
        assert [(r.kind, r.edges, r.attach_edge) for r in pairs] == [
            ("off", ("18208090", "991112953"), "999007700"),
            ("on", ("745524608",), "998737536"),
        ]
        split, reentry = pairs
        assert split.cd_pair == reentry.cd_pair == "18208090"
        assert split.rejoin_edge == "998737536"
        # The C-D road returns 590 m downstream of the split (measured on the
        # raw net; the pruned build with ramp guessing reports 613 m).
        assert 550.0 < reentry.x_m - split.x_m < 650.0
        assert not any(r.attach_via_cd for r in ramps)

    def test_mounds_blvd_split_stays_an_exit(self, ramps):
        mounds = [r for r in ramps if r.edges[0] == "18207912"]
        assert [(r.kind, r.cd_road, r.discovery) for r in mounds] == [
            ("off", False, "motorway_link")
        ]

    def test_no_non_link_entrance_exists_on_a_motorway_only_extract(self, ramps):
        assert {r.discovery for r in ramps} == {"motorway_link", "cd_road"}
