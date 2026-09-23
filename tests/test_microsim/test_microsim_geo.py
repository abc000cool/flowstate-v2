"""Corridor geometry and bbox onboarding (CLAUDE.md §3.2.4).

A hand-written OSM fixture shaped like a real divided freeway: two 3-lane
carriageways running opposite ways along the same ~2.4 km alignment (with a
curve, so headings are not constant), one ``motorway_link`` off-ramp leaving
the eastbound side and one joining it further downstream. Everything below
runs real ``netconvert`` on that fixture — never the network: the Overpass
download is mocked wherever it is exercised.

Covers :mod:`microsim.geo` (projection, chain discovery, ramp discovery,
lon/lat → linear x) and :func:`microsim.scenarios.corridor_from_bbox`, the
"any freeway corridor from a bounding box" entry point.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from itertools import pairwise
from pathlib import Path

import pytest
import sumolib

from flowstate_core.config import OSMNetwork, ScenarioConfig, config_hash
from microsim import geo, networks
from microsim.networks import osm_import
from microsim.scenarios import corridor_from_bbox

pytestmark = pytest.mark.integration

#: Alignment of the fixture corridor: seven points 0.005° of longitude apart
#: (~395 m at this latitude) with a bend in the middle, so edge headings vary
#: by ~15° along the chain instead of being a single straight line.
LONS: tuple[float, ...] = tuple(-93.2800 + 0.005 * i for i in range(7))
LATS: tuple[float, ...] = (44.9700, 44.9700, 44.9705, 44.9715, 44.9725, 44.9730, 44.9730)
#: Offset of the opposite carriageway [deg lat] (~44 m south).
CARRIAGEWAY_OFFSET = 0.0004
#: A bbox comfortably containing the fixture.
BBOX = (44.9650, -93.2900, 44.9800, -93.2400)

EB_EDGES = ["100", "101", "102"]
WB_EDGES = ["200", "201", "202"]


def fixture_osm(last_lanes: int = 3) -> str:
    """The OSM XML fixture: two carriageways, one on-ramp, one off-ramp.

    Args:
        last_lanes: Lane count of the final eastbound way (3 = uniform;
            4 exercises a lane change along the chain).

    Returns:
        A self-contained OSM XML document ``netconvert`` accepts.
    """
    nodes = [
        f'  <node id="{i}" lat="{lat:.6f}" lon="{lon:.6f}"/>'
        for i, (lon, lat) in enumerate(zip(LONS, LATS, strict=True), start=1)
    ]
    nodes += [
        f'  <node id="{10 + i}" lat="{lat - CARRIAGEWAY_OFFSET:.6f}" lon="{lon:.6f}"/>'
        for i, (lon, lat) in enumerate(zip(LONS, LATS, strict=True), start=1)
    ]
    # Ramp ends, ~130 m off the mainline at a shallow angle (real gore geometry).
    nodes.append(f'  <node id="30" lat="{LATS[2] - 0.0012:.6f}" lon="{LONS[2] + 0.004:.6f}"/>')
    nodes.append(f'  <node id="31" lat="{LATS[4] - 0.0012:.6f}" lon="{LONS[4] - 0.004:.6f}"/>')

    def way(way_id: int, refs: list[int], highway: str, lanes: int) -> str:
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        return (
            f'  <way id="{way_id}">\n    {nds}\n'
            f'    <tag k="highway" v="{highway}"/>\n'
            '    <tag k="oneway" v="yes"/>\n'
            f'    <tag k="lanes" v="{lanes}"/>\n'
            '    <tag k="maxspeed" v="70 mph"/>\n  </way>'
        )

    ways = [
        way(100, [1, 2, 3], "motorway", 3),
        way(101, [3, 4, 5], "motorway", 3),
        way(102, [5, 6, 7], "motorway", last_lanes),
        way(200, [17, 16, 15], "motorway", 3),
        way(201, [15, 14, 13], "motorway", 3),
        way(202, [13, 12, 11], "motorway", 3),
        way(300, [3, 30], "motorway_link", 1),  # off-ramp leaving after edge 100
        way(301, [31, 5], "motorway_link", 1),  # on-ramp joining at edge 102
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="hand-written-test-fixture">\n'
        + "\n".join(nodes)
        + "\n"
        + "\n".join(ways)
        + "\n</osm>\n"
    )


def _import_raw(osm_text: str, workdir: Path) -> Path:
    """Compile a fixture at raw-way granularity (the discovery import)."""
    osm_path = workdir / "fixture.osm"
    workdir.mkdir(parents=True, exist_ok=True)
    osm_path.write_text(osm_text)
    bundle = osm_import(osm_file=osm_path, workdir=workdir / "net", geometry_remove=False)
    return bundle.net_path


@pytest.fixture(scope="module")
def osm_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("geo") / "fixture.osm"
    path.write_text(fixture_osm())
    return path


@pytest.fixture(scope="module")
def net(tmp_path_factory, osm_file):
    """The compiled fixture network (raw way ids), shared by the geo tests."""
    workdir = tmp_path_factory.mktemp("geo_net")
    return sumolib.net.readNet(str(_import_raw(osm_file.read_text(), workdir)))


def _on_alignment(fraction: float) -> tuple[float, float]:
    """A (lon, lat) point ``fraction`` of the way along the alignment."""
    span = (len(LONS) - 1) * fraction
    i = min(int(span), len(LONS) - 2)
    u = span - i
    return (
        LONS[i] + u * (LONS[i + 1] - LONS[i]),
        LATS[i] + u * (LATS[i + 1] - LATS[i]),
    )


class TestProjection:
    def test_net_projection_matches_the_file(self, net, tmp_path_factory, osm_file):
        from_net = geo.net_projection(net)
        path = _import_raw(osm_file.read_text(), tmp_path_factory.mktemp("proj"))
        assert geo.read_net_projection(path) == from_net
        assert from_net.zone == 15  # Minnesota longitudes

    def test_lonlat_matches_the_junctions_netconvert_placed(self, net):
        """OSM node ids survive as junction ids: every shared id checks
        :func:`microsim.geo.utm_forward` plus the netOffset independently.
        (Way-interior nodes become shape points, not junctions, so only the
        way ends are comparable.)"""
        junctions = {n.getID(): n.getCoord() for n in net.getNodes()}
        checked = 0
        for i, (lon, lat) in enumerate(zip(LONS, LATS, strict=True), start=1):
            coord = junctions.get(str(i))
            if coord is None:
                continue
            px, py = geo.lonlat_to_net_xy(net, lon, lat)
            assert abs(px - coord[0]) < 0.01 and abs(py - coord[1]) < 0.01
            checked += 1
        assert checked >= 4  # the three way ends plus the corridor start

    def test_non_utm_network_is_refused(self, tmp_path):
        from microsim import corridor

        bundle = corridor(500.0, workdir=tmp_path / "plain")
        plain = sumolib.net.readNet(str(bundle.net_path))
        with pytest.raises(ValueError, match="unsupported projection"):
            geo.lonlat_to_net_xy(plain, LONS[0], LATS[0])


class TestMainlineChain:
    def test_bearing_selects_the_carriageway(self, net):
        assert geo.mainline_chain(net, 90.0) == EB_EDGES
        assert geo.mainline_chain(net, 270.0) == WB_EDGES

    def test_start_near_anchors_the_walk(self, net):
        lon, lat = _on_alignment(0.5)
        assert geo.mainline_chain(net, 90.0, start_near=(lon, lat)) == EB_EDGES
        assert (
            geo.mainline_chain(net, 270.0, start_near=(lon, lat - CARRIAGEWAY_OFFSET)) == WB_EDGES
        )

    def test_links_are_not_part_of_the_mainline(self, net):
        assert not {"300", "301"} & set(geo.mainline_chain(net, 90.0))

    def test_tight_tolerance_truncates_at_the_bend(self, net):
        """The chain stops where the heading leaves the tolerance — the edges
        either side of the bend differ by ~12°."""
        # 100 (86°) and 102 (86°) stay, 101 (74°) drops out and breaks the
        # chain; the walk seeds at the longest candidate and stops there.
        assert geo.mainline_chain(net, 90.0, max_heading_dev_deg=5.0) == ["102"]

    def test_no_matching_type_raises_and_lists_the_types(self, net):
        with pytest.raises(ValueError, match=r"highway\.motorway_link"):
            geo.mainline_chain(net, 90.0, highway_types=("highway.trunk",))

    def test_no_matching_heading_raises(self, net):
        with pytest.raises(ValueError, match="within 30"):
            geo.mainline_chain(net, 0.0, max_heading_dev_deg=30.0)


class TestRampsForChain:
    def test_finds_both_ramps_with_kinds_and_attach_edges(self, net):
        chain = geo.mainline_chain(net, 90.0)
        ramps = geo.ramps_for_chain(net, chain)
        assert [(r.kind, r.edges, r.attach_edge) for r in ramps] == [
            ("off", ("300",), "100"),
            ("on", ("301",), "102"),
        ]

    def test_positions_are_the_junctions_on_the_chain(self, net):
        chain = geo.mainline_chain(net, 90.0)
        offsets = geo.chain_offsets(net, chain)
        off_ramp, on_ramp = geo.ramps_for_chain(net, chain)
        # The diverge is at the END of its attach edge, the merge at the START.
        assert off_ramp.x_m == pytest.approx(offsets[1], abs=0.01)
        assert on_ramp.x_m == pytest.approx(offsets[2], abs=0.01)

    def test_opposite_carriageway_has_no_ramps(self, net):
        assert geo.ramps_for_chain(net, geo.mainline_chain(net, 270.0)) == []


class TestChainGeometry:
    def test_polyline_is_continuous_and_covers_the_chain(self, net):
        chain = geo.mainline_chain(net, 90.0)
        points = geo.chain_polyline(net, chain)
        # No hole at the junctions: every junction on the chain is a point of
        # the polyline (netconvert trims edge shapes back from its junctions).
        for edge_id in chain:
            for node in (net.getEdge(edge_id).getFromNode(), net.getEdge(edge_id).getToNode()):
                nx, ny = node.getCoord()
                assert min(abs(x - nx) + abs(y - ny) for x, y in points) < 1e-6
        assert all(a != b for a, b in pairwise(points))
        walked = sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in pairwise(points))
        assert walked == pytest.approx(geo.chain_length_m(net, chain), rel=0.02)

    def test_x_is_monotone_along_the_corridor_with_small_offsets(self, net):
        chain = geo.mainline_chain(net, 90.0)
        xs = []
        for step in range(21):
            lon, lat = _on_alignment(step / 20.0)
            point = geo.x_of_lonlat(net, chain, lon, lat)
            assert point.offset_m < 5.0
            assert point.edge_id in chain
            xs.append(point.x_m)
        assert xs == sorted(xs)
        assert xs[0] == pytest.approx(0.0, abs=1.0)
        assert xs[-1] == pytest.approx(geo.chain_length_m(net, chain), abs=1.0)

    def test_lane_pos_reconstructs_x(self, net):
        chain = geo.mainline_chain(net, 90.0)
        offsets = dict(zip(chain, geo.chain_offsets(net, chain), strict=True))
        point = geo.x_of_lonlat(net, chain, *_on_alignment(0.6))
        assert offsets[point.edge_id] + point.lane_pos == pytest.approx(point.x_m)

    def test_a_point_200_m_away_reports_a_large_offset(self, net):
        chain = geo.mainline_chain(net, 90.0)
        lon, lat = _on_alignment(0.5)
        point = geo.x_of_lonlat(net, chain, lon, lat - 0.0018)  # ~200 m south
        assert point.offset_m > 150.0

    def test_lanes_profile_merges_runs_and_reports_a_lane_change(self, tmp_path):
        uniform = sumolib.net.readNet(str(_import_raw(fixture_osm(), tmp_path / "uniform")))
        chain = geo.mainline_chain(uniform, 90.0)
        profile = geo.lanes_profile(uniform, chain)
        assert profile == [(0.0, pytest.approx(geo.chain_length_m(uniform, chain)), 3)]

        widened = sumolib.net.readNet(
            str(_import_raw(fixture_osm(last_lanes=4), tmp_path / "widened"))
        )
        chain = geo.mainline_chain(widened, 90.0)
        profile = geo.lanes_profile(widened, chain)
        assert [lanes for _, _, lanes in profile] == [3, 4]
        assert profile[0][1] == pytest.approx(profile[1][0])
        assert profile[-1][1] == pytest.approx(geo.chain_length_m(widened, chain))


class TestOverpassDownload:
    def test_query_shape_and_bbox_order(self, monkeypatch):
        seen: dict[str, str] = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'<?xml version="1.0"?><osm/>'

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["data"] = request.data.decode()
            seen["timeout"] = timeout
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        xml = networks._download_bbox_overpass((44.9, -93.3, 45.0, -93.2), timeout_s=42)
        assert xml.startswith("<?xml")
        assert seen["url"] == networks.OVERPASS_ENDPOINT
        assert seen["timeout"] == 42
        query = urllib.parse.unquote_plus(seen["data"]).removeprefix("data=")
        assert query.startswith("[out:xml][timeout:42];")
        assert 'way["highway"~"^(motorway|motorway_link)$"](44.9,-93.3,45.0,-93.2);' in query
        assert query.endswith("(._;>;); out body;")

    def test_non_xml_response_raises(self, monkeypatch):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"<html>rate limited</html>"

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        with pytest.raises(RuntimeError, match="non-XML response"):
            networks._download_bbox_overpass((44.9, -93.3, 45.0, -93.2))

    def test_osm_import_downloads_once_and_reuses_the_extract(self, tmp_path, monkeypatch):
        calls: list[tuple[float, float, float, float]] = []

        def fake(bbox, **kwargs):
            calls.append(bbox)
            return fixture_osm()

        monkeypatch.setattr(networks, "_download_bbox_overpass", fake)
        workdir = tmp_path / "net"
        first = osm_import(bbox=BBOX, workdir=workdir, download="overpass", geometry_remove=False)
        assert calls == [BBOX]
        assert (workdir / "extract.osm").is_file()
        second = osm_import(bbox=BBOX, workdir=workdir, download="overpass", geometry_remove=False)
        assert calls == [BBOX]  # the persisted extract is reused, the map is not re-read
        assert first.edge_ids == second.edge_ids

    def test_default_download_is_still_the_osm_api(self, tmp_path, monkeypatch):
        """``osm_import``'s default is unchanged; only onboarding opts in."""
        used: list[str] = []

        def fake_osm_api(bbox, dest):
            used.append("osm_api")
            dest.write_text(fixture_osm())
            return dest

        monkeypatch.setattr(networks, "_download_bbox", fake_osm_api)
        monkeypatch.setattr(
            networks,
            "_download_bbox_overpass",
            lambda *a, **k: pytest.fail("overpass must not be the default"),
        )
        osm_import(bbox=BBOX, workdir=tmp_path / "net", geometry_remove=False)
        assert used == ["osm_api"]


@pytest.fixture(scope="module")
def build(tmp_path_factory, osm_file):
    """One full bbox onboarding of the fixture, shared by the tests below."""
    stations = [
        {"station": "S1", "lat": LATS[1], "lon": LONS[1]},
        {"station": "S2", "lat": LATS[4], "lon": LONS[4]},
        {"station": "S9", "lat": LATS[3] - 0.0018, "lon": LONS[3]},  # ~200 m off
    ]
    return corridor_from_bbox(
        "fixture_eb",
        BBOX,
        90.0,
        inflow=0.5,
        workdir=tmp_path_factory.mktemp("onboard"),
        osm_file=osm_file,
        stations=stations,
        duration_s=60.0,
        seed=11,
    )


class TestCorridorFromBbox:
    def test_config_validates_and_carries_the_discovered_chain(self, build):
        cfg = build.config
        assert isinstance(cfg, ScenarioConfig) and isinstance(cfg.network, OSMNetwork)
        assert cfg.network.corridor_edges == EB_EDGES == list(build.chain_edges)
        assert cfg.network.bbox == BBOX
        assert cfg.network.inflow == [(0.0, 0.5)]
        assert cfg.name == "fixture_eb" and cfg.seed == 11 and not cfg.seeded
        assert cfg.sim.duration_s == pytest.approx(60.0)
        # Re-validating the dumped config is the schema check the API does.
        assert ScenarioConfig.model_validate(cfg.model_dump(mode="json")) == cfg

    def test_ramps_are_zero_flow_placeholders(self, build):
        ramps = build.config.network.ramps
        assert [(r.kind, r.edges, r.attach_edge) for r in ramps] == [
            ("off", ["300"], "100"),
            ("on", ["301"], "102"),
        ]
        off, on = ramps
        assert off.exit_fraction == [(0.0, 0.0)] and off.inflow == []
        assert on.inflow == [(0.0, 0.0)] and on.exit_fraction == []
        assert all(r.name for r in ramps)
        assert [r.kind for r in build.ramps] == ["off", "on"]
        assert build.ramps[0].x_m < build.ramps[1].x_m

    def test_geometry_matches_the_compiled_net(self, build):
        net = sumolib.net.readNet(str(build.net_path))
        assert build.length_m == pytest.approx(geo.chain_length_m(net, list(build.chain_edges)))
        assert build.lanes_profile == ((0.0, pytest.approx(build.length_m), 3),)
        assert build.net_path.is_file()

    def test_stations_are_placed_and_far_ones_rejected(self, build):
        assert set(build.station_x) == {"S1", "S2"}
        assert set(build.stations_rejected) == {"S9"}
        assert build.station_x["S1"].x_m < build.station_x["S2"].x_m
        assert all(p.offset_m < 5.0 for p in build.station_x.values())
        assert build.stations_rejected["S9"].offset_m > build.max_station_offset_m

    def test_summary_reports_the_corridor(self, build):
        text = build.summary()
        assert "fixture_eb" in text and "3 lanes" in text
        assert "100 101 102" in text
        assert "S9 REJECTED" in text or "REJECTED" in text
        assert f"{build.length_m / 1000.0:.2f} km" in text

    def test_yaml_round_trip_preserves_the_config_hash(self, build, tmp_path):
        path = tmp_path / "fixture_eb.yaml"
        build.to_yaml(path)
        back = ScenarioConfig.from_yaml(path)
        assert back == build.config
        assert config_hash(back) == config_hash(build.config)

    def test_bbox_path_downloads_through_overpass(self, tmp_path, monkeypatch):
        calls: list[tuple[float, float, float, float]] = []

        def fake(bbox, **kwargs):
            calls.append(bbox)
            return fixture_osm()

        monkeypatch.setattr(networks, "_download_bbox_overpass", fake)
        built = corridor_from_bbox(
            "fixture_dl",
            BBOX,
            270.0,
            inflow=0.4,
            workdir=tmp_path / "w",
            duration_s=60.0,
        )
        assert calls == [BBOX]  # one download; the pruning import reuses the extract
        assert built.chain_edges == tuple(WB_EDGES)
        assert built.osm_file == tmp_path / "w" / "net" / "extract.osm"
        assert built.config.network.osm_file == str(built.osm_file.resolve())
        assert built.ramps == ()  # the westbound carriageway has no ramps here

    @pytest.mark.parametrize(
        "kwargs, needle",
        [
            ({"bbox": (45.0, -93.29, 44.965, -93.24)}, "south<north"),
            ({"bbox": (44.965, -93.29, 95.0, -93.24)}, "WGS84 range"),
            ({"bearing_deg": 0.0}, "within 60"),
            ({"inflow": -1.0}, ">= 0"),
            ({"stations": [{"lat": 44.97, "lon": -93.27}]}, "no 'id'"),
            ({"stations": [{"station": "a", "lat": "x", "lon": -93.27}]}, "numeric"),
            (
                {"stations": [{"station": "a", "lat": 44.97, "lon": -93.27}] * 2},
                "duplicate station id",
            ),
        ],
    )
    def test_rejects_bad_inputs(self, tmp_path, osm_file, kwargs, needle):
        params = {
            "name": "bad",
            "bbox": BBOX,
            "bearing_deg": 90.0,
            "inflow": 0.5,
            "workdir": tmp_path / "bad",
            "osm_file": osm_file,
            "duration_s": 60.0,
        }
        params.update(kwargs)
        name = params.pop("name")
        bbox = params.pop("bbox")
        bearing = params.pop("bearing_deg")
        with pytest.raises(ValueError, match=needle):
            corridor_from_bbox(name, bbox, bearing, **params)
