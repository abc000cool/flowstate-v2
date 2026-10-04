"""The layout audit (``microsim.layout_audit``, WP-102; docs/LAYOUT_CHECKLIST.md).

The audit lists a compiled corridor segment by segment with every on-ramp
join, off-ramp split, lane drop/gain, weave, C-D road and speed change, and
runs the automatic checks (a)–(g). These tests build the committed OSM
fixtures with ``netconvert`` (well under a second each, no simulation) and pin

* the table: segments and events in travel order with their offsets, lane
  counts, sides and lengths, on ``merge.osm``, ``weave.osm``, ``splits.osm``,
  ``mcknight_merge.osm`` and ``weave_th61_lane_end.osm``;
* each automatic check firing on a planted defect (a modified fixture or a
  netconvert patch written to ``tmp_path``) and staying quiet on the correct
  layout;
* the inverse UTM hook in ``microsim.geo`` the positions come from, and the
  JSON / CSV / markdown outputs.

The committed fixtures are never rewritten; every variant is a copy.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from itertools import pairwise
from pathlib import Path

import pytest
import sumolib

from flowstate_core.config import RampSpec
from microsim.geo import net_xy_to_lonlat, utm_forward, utm_inverse
from microsim.layout_audit import (
    CHECKS,
    CSV_COLUMNS,
    SCHEMA,
    SHORT_AUX_LANE_M,
    SHORT_SEGMENT_M,
    LayoutAudit,
    LayoutEvent,
    _join_verdict,
    audit_layout,
    check_rule,
    imagery_links,
    osm_entrance_side,
    parse_maxspeed,
)
from microsim.networks import osm_import, weave_sections
from microsim.split_audit import parse_osm

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MERGE = FIXTURES / "merge.osm"
WEAVE = FIXTURES / "weave.osm"
SPLITS = FIXTURES / "splits.osm"
MCKNIGHT = FIXTURES / "mcknight_merge.osm"
TH61 = FIXTURES / "weave_th61_lane_end.osm"
TH61_LEFT_DROP = FIXTURES / "weave_th61_lane_end_left_drop.con.xml"

MERGE_CHAIN = ("100", "101", "102", "103")
WEAVE_CHAIN = ("100", "101", "102", "103", "104")
SPLITS_CHAIN = ("300", "301", "302", "303")
MCKNIGHT_CHAIN = ("900", "638377595", "638519829", "901")
TH61_CHAIN = ("100", "101", "102", "103", "104", "105")
GUESS = ("--ramps.guess", "--ramps.ramp-length", "250")


def _audit(
    tmp_path: Path,
    osm: Path,
    chain: tuple[str, ...],
    keep: tuple[str, ...],
    *,
    extra: tuple[str, ...] = (),
    patches: tuple[Path, ...] = (),
    ramps: list[RampSpec] | None = None,
    name: str = "net",
) -> LayoutAudit:
    bundle = osm_import(
        osm_file=osm,
        corridor_edges=chain,
        keep_edges=keep,
        workdir=tmp_path / name,
        netconvert_extra=extra,
        patch_files=list(patches),
    )
    return audit_layout(
        bundle.net_path,
        osm,
        chain,
        ramps=ramps,
        corridor=name,
        netconvert_extra=extra,
        patch_files=list(patches),
    )


def _variant(tmp_path: Path, source: Path, name: str, *subs: tuple[str, str]) -> Path:
    """A copy of ``source`` with regex substitutions applied (each must match)."""
    text = source.read_text()
    for pattern, repl in subs:
        text, n = re.subn(pattern, repl, text, flags=re.S)
        assert n >= 1, f"{pattern!r} did not match {source.name}"
    out = tmp_path / name
    out.write_text(text)
    return out


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def _kinds(audit: LayoutAudit) -> list[str]:
    return [e.kind for e in audit.events]


def _checks(flags) -> set[str]:
    return {f.check for f in flags}


def _merge_ramps() -> list[RampSpec]:
    return [
        RampSpec(kind="on", name="on 200", edges=["200"], attach_edge="102", inflow=[(0.0, 0.1)]),
        RampSpec(
            kind="off", name="off 201", edges=["201"], attach_edge="100", exit_fraction=[(0.0, 0.1)]
        ),
    ]


# --- the geo hook -------------------------------------------------------------


class TestInverseUTM:
    def test_round_trip_is_sub_millimetre_across_a_zone(self) -> None:
        worst = 0.0
        for lon in (-98.9, -96.0, -93.1, -93.0):
            for lat in (0.5, 32.9, 44.95, 60.0):
                zone = int((lon + 180.0) // 6) + 1
                e, n = utm_forward(lon, lat, zone)
                back = utm_forward(*utm_inverse(e, n, zone), zone)
                worst = max(worst, math.hypot(back[0] - e, back[1] - n))
        assert worst < 1e-3

    def test_compiled_junctions_sit_on_their_osm_nodes(self, tmp_path: Path) -> None:
        bundle = osm_import(
            osm_file=MCKNIGHT,
            corridor_edges=MCKNIGHT_CHAIN,
            keep_edges=("178547099",),
            workdir=tmp_path / "m",
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        graph = parse_osm(MCKNIGHT)
        compared = 0
        for node in net.getNodes():
            if node.getID() not in graph.nodes:
                continue
            lon, lat = net_xy_to_lonlat(net, *node.getCoord())
            lat0, lon0 = graph.nodes[node.getID()]
            d = math.hypot(
                (lon - lon0) * 111_320.0 * math.cos(math.radians(lat)), (lat - lat0) * 110_574.0
            )
            assert d < 0.05, node.getID()
            compared += 1
        assert compared >= 5


# --- the table on correct layouts ---------------------------------------------


@pytest.fixture(scope="module")
def merge_audit(tmp_path_factory: pytest.TempPathFactory) -> LayoutAudit:
    return _audit(
        tmp_path_factory.mktemp("merge"), MERGE, MERGE_CHAIN, ("200", "201"), ramps=_merge_ramps()
    )


@pytest.fixture(scope="module")
def splits_audit(tmp_path_factory: pytest.TempPathFactory) -> LayoutAudit:
    # splits.osm: its turn:lanes value carries pipes the markdown must escape
    return _audit(tmp_path_factory.mktemp("out"), SPLITS, SPLITS_CHAIN, ("400", "401"))


class TestMergeFixture:
    """``merge.osm``: an exit, then an entrance with a 556 m added lane that drops."""

    def test_segments_in_travel_order(self, merge_audit: LayoutAudit) -> None:
        audit = merge_audit
        segs = audit.segments
        assert [s.edge for s in segs] == list(MERGE_CHAIN)
        assert [s.lanes for s in segs] == [2, 2, 3, 2]
        assert [s.osm_lanes for s in segs] == [2, 2, 3, 2]
        assert segs[0].x_start_m == 0.0
        for a, b in pairwise(segs):
            assert a.x_end_m == b.x_start_m
            assert a.x_end_m - a.x_start_m == pytest.approx(a.length_m, abs=0.11)
        assert segs[-1].x_end_m == audit.length_m
        assert all(s.lat is not None and abs(s.lat - 40.0) < 0.001 for s in segs)
        # the fixture's nodes: segment 1 starts at node 2 (lon -95.9940)
        assert segs[1].lon == pytest.approx(-95.994, abs=1e-5)

    def test_events_in_order_with_offsets(self, merge_audit: LayoutAudit) -> None:
        audit = merge_audit
        segs = audit.segments
        assert _kinds(audit) == ["off_ramp", "lane_gain", "on_ramp", "lane_drop"]
        off, gain, on, drop = audit.events
        assert [e.index for e in audit.events] == [1, 2, 3, 4]
        assert off.x_m == segs[0].x_end_m and off.segment == 0
        assert gain.x_m == on.x_m == segs[2].x_start_m
        assert drop.x_m == segs[2].x_end_m
        # the exit: right, from lane 0 which also continues (an option lane)
        f = off.detail.finding
        assert (f.exit_edge, f.verdict, f.exit_from_lanes, f.option_lanes) == (
            "201",
            "ok",
            (0,),
            (0,),
        )
        assert off.detail.ramp == "off 201"
        # the gain: lane 0 of 102 begins, fed by the on-ramp, as OSM says (2 -> 3)
        lc = gain.detail
        assert (lc.lanes_before, lc.lanes_after, lc.side, lc.source) == (2, 3, "right", "osm")
        assert lc.starting_lanes == (0,) and lc.osm_agrees is True
        # the entrance: right, onto the added lane 0 that drops at 102's end
        j = on.detail
        assert j.ramp == "on 200" and j.osm_side == "right" and j.compiled_side == "rightmost"
        assert j.verdict == "ok" and j.joined_lanes == (0,) and j.mainline_fed is False
        assert j.aux_end == "drop" and j.aux_length_m == pytest.approx(segs[2].length_m, abs=0.1)
        assert all(o < 0 for o in j.osm_offsets_m)
        # the drop: lane 0 ends, 3 -> 2 as in OSM
        assert (drop.detail.side, drop.detail.source, drop.detail.ending_lanes) == (
            "right",
            "osm",
            (0,),
        )

    def test_no_layout_flag_only_the_missing_speed_limits(self, merge_audit: LayoutAudit) -> None:
        audit = merge_audit
        checks = {f.check for _, _, f in audit.flags()}
        assert checks == {"speed_limit"}  # merge.osm carries no maxspeed tag
        assert len(audit.flags_by("speed_limit")) == 4
        assert all("no maxspeed" in f.message for f in audit.flags_by("speed_limit"))
        assert audit.defects() == []


class TestWeaveFixture:
    def test_the_auxiliary_lane_is_a_weave(self, tmp_path: Path) -> None:
        ramps = [
            RampSpec(kind="on", name="A on", edges=["200"], attach_edge="102", inflow=[(0.0, 0.1)]),
            RampSpec(
                kind="off",
                name="A off",
                edges=["201"],
                attach_edge="102",
                exit_fraction=[(0.0, 0.1)],
            ),
        ]
        audit = _audit(tmp_path, WEAVE, WEAVE_CHAIN, ("200", "201"), ramps=ramps)
        assert _kinds(audit) == ["lane_gain", "on_ramp", "weave", "off_ramp", "lane_drop"]
        (weave,) = audit.events_of("weave")
        seg102 = audit.segments[2]
        assert weave.detail.on_ramp == "A on" and weave.detail.off_ramp == "A off"
        assert weave.detail.edges == ("102",) and weave.detail.exit_edge == "201"
        assert weave.detail.length_m == pytest.approx(seg102.length_m, abs=0.1)
        assert weave.detail.lane_continues is False
        (drop,) = audit.events_of("lane_drop")
        assert "leave at the exit" in drop.detail.how and drop.detail.source == "osm"
        # the runner's own weave detection agrees
        net = sumolib.net.readNet(audit.net_path)
        (section,) = weave_sections(net, list(audit.chain), ramps)
        assert section.edges == weave.detail.edges
        assert section.length_m == pytest.approx(weave.detail.length_m, abs=0.1)
        assert not [f for f in audit.flags() if f[2].check != "speed_limit"]


class TestSplitsFixture:
    def test_a_right_and_a_left_exit_both_ok(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"))
        assert _kinds(audit) == ["off_ramp", "off_ramp"]
        right, left = (e.detail.finding for e in audit.events)
        assert (right.exit_edge, right.expected_side, right.compiled_side) == (
            "400",
            "right",
            "rightmost",
        )
        assert (left.exit_edge, left.expected_side, left.compiled_side) == (
            "401",
            "left",
            "leftmost",
        )
        assert audit.events[0].x_m == audit.segments[0].x_end_m
        assert audit.events[1].x_m == audit.segments[2].x_end_m
        assert "split_side" not in _checks(f for _, _, f in audit.flags())
        # discovered ramps carry no scenario names
        assert audit.events[0].detail.ramp == "ramp 400"


class TestGuessedLanes:
    def test_mcknight_guessed_acceleration_lane_is_a_note(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, MCKNIGHT, MCKNIGHT_CHAIN, ("178547099",), extra=GUESS)
        assert [s.edge for s in audit.segments] == [
            "900",
            "638377595",
            "638519829-AddedOnRampEdge",
            "638519829",
            "901",
        ]
        assert _kinds(audit) == ["lane_gain", "on_ramp", "lane_drop"]
        gain, on, drop = audit.events
        assert gain.detail.source == drop.detail.source == "ramp_guessing"
        j = on.detail
        assert j.attach_edge == "638519829-AddedOnRampEdge" and j.aux_guessed
        assert j.aux_end == "drop" and j.aux_length_m == pytest.approx(251.0, abs=0.5)
        assert j.verdict == "ok" and j.osm_side == "right"
        # every tag is present and agrees: nothing above a note
        assert audit.ramp_guessing
        assert {f.severity for _, _, f in audit.flags()} == {"info"}
        assert _checks(f for _, _, f in audit.flags()) == {"guessed_aux_lane", "lanes_vs_osm"}
        piece = audit.segments[2]
        assert piece.guessed_piece and (piece.lanes, piece.osm_lanes) == (4, 3)
        assert piece.speed_ms == pytest.approx(parse_maxspeed("55 mph"), abs=0.01)

    def test_guessed_deceleration_lane_on_the_exit(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, MERGE, MERGE_CHAIN, ("200", "201"), extra=GUESS)
        (off,) = audit.events_of("off_ramp")
        assert off.detail.decel_lane_guessed
        assert off.detail.decel_length_m == pytest.approx(251.0, abs=0.5)
        assert "guessed_aux_lane" in _checks(off.flags)


# --- each automatic check on a planted defect ---------------------------------


class TestCheckAOnRampSide:
    def test_a_right_ramp_joined_to_the_leftmost_lane_is_a_defect(self, tmp_path: Path) -> None:
        patch = _write(
            tmp_path,
            "wrong_join.con.xml",
            '<connections>\n  <connection from="200" to="102" fromLane="0" toLane="2"/>\n'
            "</connections>\n",
        )
        audit = _audit(tmp_path, MERGE, MERGE_CHAIN, ("200", "201"), patches=(patch,))
        (on,) = audit.events_of("on_ramp")
        assert on.detail.osm_side == "right" and on.detail.compiled_side == "leftmost"
        assert on.detail.verdict == "wrong_side"
        (flag,) = [f for f in on.flags if f.check == "on_ramp_side"]
        assert flag.severity == "defect" and "rightmost lane" in flag.message
        assert [t[2].check for t in audit.defects()] == ["on_ramp_side"]

    def test_quiet_on_a_left_ramp_compiled_on_the_left(self, tmp_path: Path) -> None:
        left = _variant(
            tmp_path,
            MERGE,
            "merge_left.osm",
            (r'<node id="10" lat="39.9994"', '<node id="10" lat="40.0006"'),
        )
        audit = _audit(tmp_path, left, MERGE_CHAIN, ("200", "201"))
        (on,) = audit.events_of("on_ramp")
        assert on.detail.osm_side == "left" and on.detail.compiled_side == "leftmost"
        assert on.detail.verdict == "ok" and not _checks(on.flags) & {"on_ramp_side"}
        # the added lane begins on the left: a note, not a defect
        (gain,) = audit.events_of("lane_gain")
        assert gain.detail.side == "left" and "lane_change_left" in _checks(gain.flags)

    def test_entrance_side_from_geometry_alone(self) -> None:
        graph = parse_osm(MERGE)
        side, offsets = osm_entrance_side(graph, "200", ["101", "102"], merge_node="3")
        assert side == "right" and offsets and all(o < -1.0 for o in offsets)
        assert osm_entrance_side(graph, "999", ["101"]) == ("unknown", ())

    def test_verdicts_and_remedies(self) -> None:
        assert _join_verdict("right", "rightmost", False, "e")[0] == "ok"
        assert _join_verdict("left", "all", False, "e")[0] == "ok"
        verdict, remedy = _join_verdict("left", "rightmost", True, "45608485")
        assert verdict == "added_lane_wrong_side" and "--ramps.unset 45608485" in remedy
        verdict, remedy = _join_verdict("right", "leftmost", False, "e")
        assert verdict == "wrong_side" and "patch_files" in remedy
        assert _join_verdict("unknown", "rightmost", False, "e")[0] == "unknown"


class TestCheckBAccelerationLane:
    def test_a_ramp_merging_straight_into_a_through_lane_is_flagged(self, tmp_path: Path) -> None:
        no_accel = _variant(
            tmp_path,
            MERGE,
            "merge_noaccel.osm",
            (r'(<way id="102">.*?)<tag k="lanes" v="3"/>', r'\1<tag k="lanes" v="2"/>'),
        )
        audit = _audit(tmp_path, no_accel, MERGE_CHAIN, ("200", "201"))
        (on,) = audit.events_of("on_ramp")
        assert on.detail.mainline_fed is True and on.detail.aux_lanes == ()
        (flag,) = [f for f in on.flags if f.check == "no_accel_lane"]
        assert flag.severity == "warning"
        assert "no acceleration lane in the compiled net" in flag.message
        assert "Minnesota" in flag.message and "starved" in flag.message
        # with ramp guessing the lane exists (guessed), and the check is quiet
        guessed = _audit(tmp_path, no_accel, MERGE_CHAIN, ("200", "201"), extra=GUESS, name="g")
        (on_g,) = guessed.events_of("on_ramp")
        assert on_g.detail.mainline_fed is False and on_g.detail.aux_guessed
        assert "no_accel_lane" not in _checks(on_g.flags)
        assert "guessed_aux_lane" in _checks(on_g.flags)

    def test_a_short_added_lane_is_flagged(self, tmp_path: Path) -> None:
        # way 102 split ~26 m after the merge node: 3 lanes, then 2 (the compiled
        # edge gains the junction geometry and comes out near 70 m)
        short = _variant(
            tmp_path,
            MERGE,
            "merge_short.osm",
            (r'<node id="4" ', '<node id="35" lat="40.0000" lon="-95.9877"/>\n  <node id="4" '),
            (
                r'<way id="102">\s*<nd ref="3"/><nd ref="4"/>',
                '<way id="102">\n    <nd ref="3"/><nd ref="35"/>',
            ),
            (
                r'(<way id="103">)',
                '<way id="1020">\n    <nd ref="35"/><nd ref="4"/>\n'
                '    <tag k="highway" v="motorway"/>\n    <tag k="oneway" v="yes"/>\n'
                '    <tag k="lanes" v="2"/>\n  </way>\n  \\1',
            ),
        )
        chain = ("100", "101", "102", "1020", "103")
        audit = _audit(tmp_path, short, chain, ("200", "201"))
        (on,) = audit.events_of("on_ramp")
        assert on.detail.aux_end == "drop"
        assert on.detail.aux_length_m is not None and on.detail.aux_length_m < SHORT_AUX_LANE_M
        (flag,) = [f for f in on.flags if f.check == "no_accel_lane"]
        assert "only" in flag.message and f"{SHORT_AUX_LANE_M:g} m" in flag.message

    def test_quiet_on_a_real_acceleration_lane(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, MERGE, MERGE_CHAIN, ("200", "201"))
        assert audit.flags_by("no_accel_lane") == []


class TestCheckCLanesVsOSM:
    def test_compiled_count_differs_from_the_tag(self, tmp_path: Path) -> None:
        patch = _write(
            tmp_path, "lanes302.edg.xml", '<edges>\n  <edge id="302" numLanes="2"/>\n</edges>\n'
        )
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"), patches=(patch,))
        seg = audit.segments[2]
        (flag,) = [f for f in seg.flags if f.check == "lanes_vs_osm"]
        assert flag.severity == "warning" and "compiled 2 lanes, map 3" in flag.message

    def test_a_missing_tag_is_flagged(self, tmp_path: Path) -> None:
        untagged = _variant(
            tmp_path,
            SPLITS,
            "splits_nolanes.osm",
            (r'(<way id="301">.*?)<tag k="lanes" v="3"/>\n', r"\1"),
        )
        audit = _audit(tmp_path, untagged, SPLITS_CHAIN, ("400", "401"))
        seg = audit.segments[1]
        assert seg.osm_lanes is None
        (flag,) = [f for f in seg.flags if f.check == "lanes_vs_osm"]
        assert "no readable lanes tag" in flag.message and "default" in flag.message

    def test_quiet_when_every_tag_agrees(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"))
        assert audit.flags_by("lanes_vs_osm") == []


class TestCheckDLaneChanges:
    def test_a_drop_the_map_does_not_have_is_flagged(self, tmp_path: Path) -> None:
        patch = _write(
            tmp_path, "lanes302.edg.xml", '<edges>\n  <edge id="302" numLanes="2"/>\n</edges>\n'
        )
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"), patches=(patch,))
        (drop,) = audit.events_of("lane_drop")
        assert (drop.detail.from_edge, drop.detail.to_edge) == ("301", "302")
        assert (drop.detail.osm_lanes_before, drop.detail.osm_lanes_after) == (3, 3)
        assert drop.detail.osm_agrees is False and drop.detail.source == "unexplained"
        (flag,) = [f for f in drop.flags if f.check == "lane_change_not_in_osm"]
        assert flag.severity == "warning" and "3 -> 2" in flag.message
        (gain,) = audit.events_of("lane_gain")
        assert "lane_change_not_in_osm" in _checks(gain.flags)

    def test_quiet_when_the_map_draws_the_drop(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, MERGE, MERGE_CHAIN, ("200", "201"))
        (drop,) = audit.events_of("lane_drop")
        assert drop.detail.osm_agrees is True and drop.flags == ()

    def test_a_left_side_drop_is_noted(self, tmp_path: Path) -> None:
        audit = _audit(
            tmp_path, TH61, TH61_CHAIN, ("200", "201"), patches=(TH61_LEFT_DROP,), name="left"
        )
        drops = audit.events_of("lane_drop")
        last = drops[-1]
        assert (last.detail.from_edge, last.detail.side, last.detail.ending_lanes) == (
            "104",
            "left",
            (2,),
        )
        assert _checks(last.flags) == {"lane_change_left"}
        plain = _audit(tmp_path, TH61, TH61_CHAIN, ("200", "201"), name="right")
        assert plain.events_of("lane_drop")[-1].detail.side == "right"
        assert plain.flags_by("lane_change_left") == []


class TestCheckESpeedLimit:
    def test_unreadable_mismatched_and_changing_limits(self, tmp_path: Path) -> None:
        osm = _variant(
            tmp_path,
            MCKNIGHT,
            "mck_speed.osm",
            (
                r'(<way id="638519829">.*?)<tag k="maxspeed" v="55 mph"/>',
                r'\1<tag k="maxspeed" v="65 mph"/>',
            ),
            (
                r'(<way id="900">.*?)<tag k="maxspeed" v="55 mph"/>',
                r'\1<tag k="maxspeed" v="signals"/>',
            ),
        )
        patch = _write(
            tmp_path, "speed.edg.xml", '<edges>\n  <edge id="901" speed="20.0"/>\n</edges>\n'
        )
        audit = _audit(tmp_path, osm, MCKNIGHT_CHAIN, ("178547099",), extra=GUESS, patches=(patch,))
        by_edge = {s.edge: s for s in audit.segments}
        (unreadable,) = by_edge["900"].flags
        assert unreadable.check == "speed_limit" and "does not read" in unreadable.message
        (mismatch,) = by_edge["901"].flags
        assert mismatch.check == "speed_limit" and "map maxspeed 55 mph" in mismatch.message
        changes = audit.events_of("speed_change")
        assert [(c.detail.from_edge, c.detail.to_edge) for c in changes] == [
            ("900", "638377595"),
            ("638377595", "638519829-AddedOnRampEdge"),
            ("638519829", "901"),
        ]
        assert all(_checks(c.flags) == {"speed_limit"} for c in changes)
        assert changes[1].detail.after_ms == pytest.approx(parse_maxspeed("65 mph"), abs=0.01)

    def test_quiet_when_every_way_is_tagged_and_constant(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, MCKNIGHT, MCKNIGHT_CHAIN, ("178547099",), extra=GUESS)
        assert audit.flags_by("speed_limit") == [] and audit.events_of("speed_change") == []

    def test_maxspeed_parsing(self) -> None:
        assert parse_maxspeed("55 mph") == pytest.approx(24.5872, abs=1e-4)
        assert parse_maxspeed("100") == pytest.approx(27.7778, abs=1e-4)
        assert parse_maxspeed("90 km/h") == pytest.approx(25.0, abs=1e-9)
        assert parse_maxspeed("10 knots") == pytest.approx(5.1444, abs=1e-4)
        for raw in (None, "none", "signals", "US:urban", "55 mph;45 mph", ""):
            assert parse_maxspeed(raw) is None


class TestCheckFShortSegment:
    def test_a_ten_metre_segment_is_flagged(self, tmp_path: Path) -> None:
        short = _variant(
            tmp_path,
            SPLITS,
            "splits_short.osm",
            (r'<node id="3" ', '<node id="25" lat="40.0000" lon="-95.9938826"/>\n  <node id="3" '),
            (
                r'<way id="301">\s*<nd ref="2"/><nd ref="3"/>',
                '<way id="3010">\n    <nd ref="2"/><nd ref="25"/>\n'
                '    <tag k="highway" v="motorway"/>\n    <tag k="oneway" v="yes"/>\n'
                '    <tag k="lanes" v="3"/>\n  </way>\n  <way id="301">\n'
                '    <nd ref="25"/><nd ref="3"/>',
            ),
        )
        audit = _audit(tmp_path, short, ("300", "3010", "301", "302", "303"), ("400", "401"))
        flagged = [s for s in audit.segments if "short_segment" in _checks(s.flags)]
        assert [s.edge for s in flagged] == ["3010"]
        assert flagged[0].length_m < SHORT_SEGMENT_M

    def test_quiet_on_the_fixture(self, tmp_path: Path) -> None:
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"))
        assert audit.flags_by("short_segment") == []


class TestCheckGSplitSide:
    def test_a_right_exit_fed_from_the_left_lane_is_a_defect(self, tmp_path: Path) -> None:
        patch = _write(
            tmp_path,
            "split_wrong.con.xml",
            "<connections>\n"
            '  <connection from="300" to="400" fromLane="2" toLane="0"/>\n'
            '  <connection from="300" to="301" fromLane="0" toLane="0"/>\n'
            '  <connection from="300" to="301" fromLane="1" toLane="1"/>\n'
            '  <connection from="300" to="301" fromLane="2" toLane="2"/>\n'
            "</connections>\n",
        )
        audit = _audit(tmp_path, SPLITS, SPLITS_CHAIN, ("400", "401"), patches=(patch,))
        first = audit.events_of("off_ramp")[0]
        assert first.detail.finding.verdict == "wrong_side"
        (flag,) = [f for f in first.flags if f.check == "split_side"]
        assert flag.severity == "defect" and "Fix:" in flag.message
        assert [t[2].check for t in audit.defects()] == ["split_side"]
        # the split audit's record is carried whole
        assert audit.split_audit[0].verdict == "wrong_side"


# --- C-D roads, the runner's merge models, outputs ----------------------------


def _cd_osm(tmp_path: Path) -> Path:
    """A link chain leaving at node 2 and rejoining at node 5, an exit off it at node 50
    (the shape of ``tests/test_microsim/test_microsim_geo_ramps.py``'s C-D fixture)."""
    lat = 40.0
    lons = [-96.0 + 0.006 * i for i in range(6)]
    nodes = [
        f'  <node id="{i + 1}" lat="{lat:.6f}" lon="{lon:.6f}"/>' for i, lon in enumerate(lons)
    ]
    nodes += [
        f'  <node id="50" lat="{lat - 0.0006:.6f}" lon="{lons[1] + 0.002:.6f}"/>',
        f'  <node id="51" lat="{lat - 0.0006:.6f}" lon="{lons[3] + 0.002:.6f}"/>',
        f'  <node id="52" lat="{lat - 0.0015:.6f}" lon="{lons[2]:.6f}"/>',
    ]

    def way(wid: int, refs: list[int], highway: str) -> str:
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        lanes = 3 if highway == "motorway" else 1
        return (
            f'  <way id="{wid}">\n    {nds}\n    <tag k="highway" v="{highway}"/>\n'
            f'    <tag k="lanes" v="{lanes}"/>\n    <tag k="oneway" v="yes"/>\n'
            '    <tag k="maxspeed" v="55 mph"/>\n  </way>'
        )

    ways = [way(100 + i, [i + 1, i + 2], "motorway") for i in range(5)]
    ways += [
        way(500, [2, 50], "motorway_link"),
        way(501, [50, 51], "motorway_link"),
        way(502, [51, 5], "motorway_link"),
        way(510, [50, 52], "motorway_link"),
    ]
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
        + "\n".join(nodes)
        + "\n"
        + "\n".join(ways)
        + "\n</osm>\n"
    )
    return _write(tmp_path, "cd.osm", text)


class TestCollectorDistributor:
    def test_the_pair_is_listed_with_its_ends(self, tmp_path: Path) -> None:
        osm = _cd_osm(tmp_path)
        chain = ("100", "101", "102", "103", "104")
        audit = _audit(tmp_path, osm, chain, ("500", "501", "502", "510"))  # ramps discovered
        (cd,) = audit.events_of("cd_road")
        segs = audit.segments
        assert cd.detail.pair == "500"
        assert cd.detail.split_x_m == segs[0].x_end_m
        assert cd.detail.rejoin_x_m == segs[4].x_start_m
        assert cd.detail.length_m == pytest.approx(segs[4].x_start_m - segs[0].x_end_m, abs=0.2)
        (off,) = audit.events_of("off_ramp")
        (on,) = audit.events_of("on_ramp")
        assert off.detail.cd_pair == "500" and on.detail.cd_pair == "500"
        assert on.detail.ramp_edge == "502" and on.x_m == segs[4].x_start_m
        # travel order: the split, then the pair, ..., then the re-entry
        assert _kinds(audit).index("off_ramp") < _kinds(audit).index("cd_road")
        assert _kinds(audit).index("cd_road") < _kinds(audit).index("on_ramp")


class TestOutputs:
    def test_json(self, splits_audit: LayoutAudit, tmp_path: Path) -> None:
        audit = splits_audit
        text = audit.to_json(tmp_path / "a.json")
        data = json.loads((tmp_path / "a.json").read_text())
        assert text == (tmp_path / "a.json").read_text()
        assert data["schema"] == SCHEMA
        assert data["counts"]["segments"] == 4 and data["counts"]["events"] == 2
        assert [c["id"] for c in data["checks"]] == [c.id for c in CHECKS]
        assert data["cannot_see"]
        event = data["events"][0]
        assert event["detail"]["verdict"] == "ok" and event["links"]["satellite"].startswith(
            "https://www.google.com/maps/@?api=1"
        )
        assert data["segments"][0]["links"]["street_view"]

    def test_csv_interleaves_segments_and_events(self, splits_audit: LayoutAudit) -> None:
        audit = splits_audit
        rows = list(csv.DictReader(io.StringIO(audit.to_csv())))
        assert list(rows[0]) == list(CSV_COLUMNS)
        assert len(rows) == len(audit.segments) + len(audit.events)
        assert [r["row_type"] for r in rows] == [
            "segment",
            "event",
            "segment",
            "segment",
            "event",
            "segment",
        ]
        assert all(r["checked_by"] == r["imagery_date"] == r["result"] == "" for r in rows)
        assert all(r["satellite_url"] for r in rows)
        xs = [float(r["x_start_m"]) for r in rows]
        assert xs == sorted(xs)

    def test_markdown_checklist(self, splits_audit: LayoutAudit) -> None:
        audit = splits_audit
        text = audit.to_markdown()
        assert text.startswith("# Layout checklist: ")
        assert "## Events (one line per event, in travel order)" in text
        assert "| checked by | imagery date | result |" in text
        event_lines = [ln for ln in text.splitlines() if re.match(r"^\| \d+ \| \d+\.\d{3} \| ", ln)]
        assert len(event_lines) == len(audit.events)
        assert all(ln.endswith("|  |  |  |") for ln in event_lines)
        assert "none\\|none\\|slight_right" in text  # turn:lanes pipes escaped
        assert "## The automatic checks" in text and "## What this audit cannot see" in text
        for rule in CHECKS:
            assert f"`{rule.id}`" in text

    def test_imagery_links(self) -> None:
        links = imagery_links(44.95, -93.0)
        assert links["satellite"] == (
            "https://www.google.com/maps/@?api=1&map_action=map&center=44.950000,-93.000000"
            "&zoom=18&basemap=satellite"
        )
        assert (
            links["satellite_alt"]
            == "https://www.bing.com/maps?cp=44.950000~-93.000000&lvl=18&style=a"
        )
        assert "map_action=pano" in links["street_view"]
        assert imagery_links(None, None) == {}

    def test_every_check_has_a_rule_and_a_reason(self) -> None:
        letters = [c.letter for c in CHECKS if c.letter]
        assert letters == ["a", "b", "c", "d", "e", "f", "g"]
        for rule in CHECKS:
            assert rule.rule and rule.reason and check_rule(rule.id) is rule


def test_events_are_typed_records() -> None:
    assert LayoutEvent.__dataclass_params__.frozen  # type: ignore[attr-defined]
