"""Network builder tests: ring exactness, corridor mapping, OSM pipeline.

All tests run real ``netconvert`` (SUMO tooling) → marked integration.
"""

import io
import math
import urllib.error
import urllib.request

import pytest
import sumolib

from microsim import corridor, networks, osm_import, ring

pytestmark = pytest.mark.integration

RING_C = 230.0


class TestRing:
    def test_edge_lengths_sum_to_circumference_within_1cm(self, tmp_path):
        bundle = ring(RING_C, workdir=tmp_path)
        assert abs(sum(bundle.edge_lengths) - RING_C) < 0.01
        # And the *compiled* network agrees (explicit length attributes).
        net = sumolib.net.readNet(str(bundle.net_path))
        total = sum(e.getLength() for e in net.getEdges())
        assert abs(total - RING_C) < 0.01

    def test_offsets_partition_the_circumference(self, tmp_path):
        bundle = ring(RING_C, n_segments=10, workdir=tmp_path)
        assert bundle.offsets[0] == 0.0
        assert all(b > a for a, b in zip(bundle.offsets, bundle.offsets[1:], strict=False))
        assert bundle.total_length_m == pytest.approx(RING_C)
        # locate() wraps modulo the circumference.
        eid, pos = bundle.locate(RING_C + 5.0)
        assert eid == bundle.edge_ids[0]
        assert pos == pytest.approx(5.0)

    def test_rejects_bad_geometry(self, tmp_path):
        with pytest.raises(ValueError, match="n_segments"):
            ring(RING_C, n_segments=4, workdir=tmp_path)
        with pytest.raises(ValueError, match="circumference"):
            ring(-1.0, workdir=tmp_path)


class TestCorridor:
    def test_linear_x_mapping_monotone(self, tmp_path):
        bundle = corridor(3500.0, lanes=2, workdir=tmp_path)
        assert bundle.entry_edge == "entry"
        assert bundle.edge_ids[0] == "entry"
        # Cumulative offsets strictly increase in route order.
        assert all(b > a for a, b in zip(bundle.offsets, bundle.offsets[1:], strict=False))
        # linear_x is monotone along the route: walking the route in order
        # always increases x.
        xs = [
            bundle.linear_x(eid, frac * elen)
            for eid, elen in zip(bundle.edge_ids, bundle.edge_lengths, strict=True)
            for frac in (0.0, 0.5, 0.999)
        ]
        assert xs == sorted(xs)
        assert bundle.total_length_m == pytest.approx(100.0 + 3500.0)

    def test_locate_roundtrip(self, tmp_path):
        bundle = corridor(2200.0, workdir=tmp_path)
        for x in (0.0, 50.0, 100.0, 1099.5, 2299.9):
            eid, pos = bundle.locate(x)
            assert bundle.linear_x(eid, pos) == pytest.approx(x)

    def test_remainder_segment(self, tmp_path):
        bundle = corridor(2500.0, workdir=tmp_path, segment_m=1000.0)
        assert bundle.edge_lengths == pytest.approx((100.0, 1000.0, 1000.0, 500.0))

    def test_main_edges_exclude_entry(self, tmp_path):
        bundle = corridor(2000.0, workdir=tmp_path)
        assert "entry" not in bundle.main_edges
        assert len(bundle.main_edges) == len(bundle.edge_ids) - 1

    def test_no_exit_edge_by_default(self, tmp_path):
        bundle = corridor(2000.0, workdir=tmp_path)
        assert bundle.exit_edge is None
        assert "exit" not in bundle.edge_ids

    def test_exit_buffer_edge(self, tmp_path):
        bundle = corridor(2000.0, workdir=tmp_path, entry_m=100.0, exit_m=200.0)
        assert bundle.exit_edge == "exit"
        assert bundle.edge_ids[-1] == "exit"
        assert bundle.edge_lengths[-1] == pytest.approx(200.0)
        assert bundle.total_length_m == pytest.approx(100.0 + 2000.0 + 200.0)
        # The exit edge exists in the compiled network with the right length.
        net = sumolib.net.readNet(str(bundle.net_path))
        assert net.getEdge("exit").getLength() == pytest.approx(200.0, abs=0.5)
        # linear_x maps into the exit buffer beyond the corridor proper.
        assert bundle.linear_x("exit", 50.0) == pytest.approx(2150.0)
        # Entry AND exit are excluded from the analysis corridor.
        assert "exit" not in bundle.main_edges
        assert "entry" not in bundle.main_edges
        assert len(bundle.main_edges) == len(bundle.edge_ids) - 2

    def test_negative_exit_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="exit_m"):
            corridor(2000.0, workdir=tmp_path, exit_m=-1.0)


TINY_OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="hand-written-test-fixture">
  <node id="1" lat="40.0000" lon="-96.0000"/>
  <node id="2" lat="40.0000" lon="-95.9940"/>
  <node id="3" lat="40.0000" lon="-95.9880"/>
  <way id="100">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="motorway"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="1"/>
  </way>
</osm>
"""


class TestOSMImport:
    def test_fixture_import_produces_loadable_net(self, tmp_path):
        osm = tmp_path / "tiny.osm"
        osm.write_text(TINY_OSM)
        bundle = osm_import(osm_file=osm, workdir=tmp_path / "work")
        assert bundle.kind == "osm"
        assert len(bundle.edge_ids) >= 1
        assert bundle.total_length_m > 500.0  # ~1 km motorway stretch
        net = sumolib.net.readNet(str(bundle.net_path))
        assert len(net.getEdges()) == len(bundle.edge_ids)

    def test_corridor_pruning_orders_edges(self, tmp_path):
        osm = tmp_path / "tiny.osm"
        osm.write_text(TINY_OSM)
        bundle = osm_import(osm_file=osm, corridor_edges=("100",), workdir=tmp_path / "work")
        assert bundle.edge_ids == ("100",)
        assert bundle.offsets == (0.0,)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            osm_import(osm_file=tmp_path / "nope.osm", workdir=tmp_path / "w")

    def test_no_source_raises(self, tmp_path):
        with pytest.raises(ValueError, match="osm_file or bbox"):
            osm_import(workdir=tmp_path)


class TestOverpassDownloadErrors:
    """What a failed Overpass download says (``urlopen`` is stubbed: no network)."""

    BBOX = (36.0, -87.0, 36.01, -86.99)

    @staticmethod
    def _raise_http_error(status, reason, body=b"<html>SECRET-BODY</html>"):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                networks.OVERPASS_ENDPOINT, status, reason, {}, io.BytesIO(body)
            )

        return urlopen

    def test_http_status_and_reason_are_surfaced_body_is_not(self, monkeypatch):
        """A download's status line cannot quote the caller's input, so it is
        named — a 504 is a community-server outage to retry, not a bad bbox —
        while the response body stays out of the message."""
        monkeypatch.setattr(
            urllib.request, "urlopen", self._raise_http_error(504, "Gateway Timeout")
        )
        with pytest.raises(RuntimeError) as excinfo:
            networks._download_bbox_overpass(self.BBOX)

        message = str(excinfo.value)
        assert "Overpass API answered HTTP 504 Gateway Timeout" in message
        assert "retry later" in message
        assert "SECRET-BODY" not in message
        # No HTTPError hangs off this one: the API's redacting failure record
        # walks the chain and would add a "message withheld" line for it.
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None

    def test_a_multiline_reason_phrase_is_reduced_to_one_line(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            self._raise_http_error(429, "Too Many\nRequests " + "x" * 200),
        )
        with pytest.raises(RuntimeError) as excinfo:
            networks._download_bbox_overpass(self.BBOX)

        message = str(excinfo.value)
        assert "\n" not in message
        assert "HTTP 429 Too Many Requests" in message
        assert len(message) < 200


class TestNetBundleMath:
    def test_ring_arc_positions_are_exact(self, tmp_path):
        """The polygon chords are shorter than the arcs; explicit lengths win."""
        bundle = ring(RING_C, n_segments=8, workdir=tmp_path)
        seg = RING_C / 8
        chord = 2.0 * (RING_C / (2.0 * math.pi)) * math.sin(math.pi / 8)
        assert chord < seg  # geometry sanity: chord < arc
        assert bundle.edge_lengths == pytest.approx(tuple([seg] * 8))


class TestLaneEndPatchFile:
    """An edge id is data, not a file name: it may hold a dot (or a slash).

    The patch's *kind* is read from its last two suffixes, so a compiled id
    like ``103.5-AddedOnRampEdge`` still produces a routable ``.con.xml``.
    """

    def test_a_dotted_edge_id_is_sanitised_and_still_routes(self, tmp_path):
        path = networks.lane_end_patch_file(tmp_path / "patches", "103.5#2/a", "104")
        assert path.name.startswith("accel_end_103_5_2_a_")
        assert path.suffixes[-2:] == [".con", ".xml"]
        # The id inside the XML is untouched: netconvert matches on that.
        assert 'from="103.5#2/a"' in path.read_text()
        assert networks._patch_args([path]) == ["--connection-files", str(path)]

    def test_ids_that_sanitise_alike_do_not_share_a_file(self, tmp_path):
        """``e#1`` and ``e.1`` both sanitise to ``e_1`` — and OSM carries both.

        Sharing a stem would have the second patch overwrite the first, so one
        merge would be built from the other merge's connections. A digest of
        the raw id keeps them apart in both patch writers.
        """
        assert networks._safe_file_stem("e#1") != networks._safe_file_stem("e.1")
        first = networks.lane_end_patch_file(tmp_path / "patches", "e#1", "n")
        second = networks.lane_end_patch_file(tmp_path / "patches", "e.1", "n")
        assert first != second
        assert 'from="e#1"' in first.read_text() and 'from="e.1"' in second.read_text()
        (hash_edg,) = networks.merge_patch_files(
            tmp_path / "patches", "e#1", "b", "n", 3, 2, "acceleration_lane"
        )
        (dot_edg,) = networks.merge_patch_files(
            tmp_path / "patches", "e.1", "b", "n", 3, 2, "acceleration_lane"
        )
        assert hash_edg != dot_edg
        assert 'id="e#1"' in hash_edg.read_text() and 'id="e.1"' in dot_edg.read_text()

    def test_the_kind_comes_from_the_last_two_suffixes(self, tmp_path):
        nod = tmp_path / "a.b.nod.xml"
        edg = tmp_path / "x.edg.xml"
        assert networks._patch_args([nod, edg]) == [
            "--node-files",
            str(nod),
            "--edge-files",
            str(edg),
        ]
        with pytest.raises(ValueError, match="must end in"):
            networks._patch_args([tmp_path / "a.con.txt"])
