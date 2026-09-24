"""Split audit: does every exit leave the compiled mainline on the side OSM draws it on?

On the I-94 WB St. Paul extract (docs/ONBOARDING_MNDOT.md §9, 2026-09-24)
``netconvert`` compiled two right-hand exits onto the LEFTMOST lanes of the
mainline — the 12th Street exit from a fourth lane that ``--ramps.guess``
added on the left, the Mounds/Kellogg exit from lanes 3–4 of 5 regardless of
options. Through vehicles trapped in a lane that led only to an exit stalled
the other lanes and the corridor locked from its downstream end. Nothing in
the lane-profile check (``CorridorBuild.lane_check``) could see it: the lane
*count* was right at every station; the *side* was wrong.

This module makes the check generic. For every connection from a corridor
(mainline) edge into a non-corridor edge at a diverge it compares

* the **OSM side** of the leaving way — the signed lateral offset of the
  link's first nodes from the continuing mainline way (cross product of the
  mainline direction and the vector to the link node, in local metres;
  negative = right of travel), plus the ``turn:lanes`` tag when the way
  carries one (OSM lists lanes left to right);
* the **compiled side** — which lanes of the compiled edge feed the exit
  (``fromLane`` relative to the edge's lane count; SUMO lane 0 is the
  rightmost), and whether the edge gained a lane over its OSM ``lanes`` tag;

and gives a verdict (:data:`Verdict`). A defect's remedy is stated in the
engine's own terms: an ``OSMNetwork.patch_files`` connection patch restating
the split (rightmost lanes to a right exit, the rest continuing;
:func:`connection_patch_lines`) or ``--ramps.unset <edge>`` when the wrong
lane was added by ramp guessing (:func:`ramps_unset_edges`).

Importable without SUMO running: ``sumolib`` reads the compiled net, the OSM
extract is a light XML parse.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import sumolib

from microsim.networks import expand_ramp_splits

Side = Literal["left", "right", "unknown"]
CompiledSide = Literal["rightmost", "leftmost", "middle", "all"]
Verdict = Literal["ok", "wrong_side", "added_lane_wrong_side", "unknown"]

#: The verdicts that mean the compiled split traps through traffic.
DEFECT_VERDICTS: frozenset[str] = frozenset({"wrong_side", "added_lane_wrong_side"})

#: How far along the link (from the split node) its nodes are sampled for
#: the lateral offset [m]. A link is drawn parallel to the mainline for its
#: first tens of metres and then peels away; 300 m sees the peel without
#: reaching the ramp's own curve toward the cross street.
LINK_SAMPLE_M: float = 300.0

#: How much continuing-mainline geometry is collected downstream of the split
#: [m] — enough for every sampled link node to project onto it.
CONTINUING_MIN_M: float = 500.0

#: Below this lateral offset [m] the side is not called: a link node inside
#: the mainline's own width says nothing about which side it is on.
MIN_SIDE_OFFSET_M: float = 1.0

#: Suffixes ``netconvert --ramps.guess`` appends to the piece of an edge it
#: splits off; stripped to recover the load-time edge id.
_RAMP_SPLIT_SUFFIXES: tuple[str, ...] = ("-AddedOffRampEdge", "-AddedOnRampEdge")

#: Metres per degree of latitude (WGS84 mean); longitude is scaled by cos(lat).
_M_PER_DEG_LAT: float = 110_574.0
_M_PER_DEG_LON_EQ: float = 111_320.0


@dataclass(frozen=True)
class OSMWay:
    """One OSM way: its node refs in order and its tags."""

    id: str
    nodes: tuple[str, ...]
    tags: dict[str, str]

    @property
    def highway(self) -> str:
        return self.tags.get("highway", "")

    @property
    def is_link(self) -> bool:
        return self.highway.endswith("_link")

    @property
    def lanes(self) -> int | None:
        """The ``lanes`` tag as an integer, or ``None`` when absent or unparsable."""
        raw = self.tags.get("lanes")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None


@dataclass(frozen=True)
class OSMGraph:
    """The parts of an OSM extract the audit reads: node positions and ways."""

    nodes: dict[str, tuple[float, float]]
    """Node id → ``(lat, lon)`` in degrees."""
    ways: dict[str, OSMWay]

    def ways_from(self, node_id: str) -> list[OSMWay]:
        """Ways whose FIRST node is ``node_id`` (one-way roads leaving it)."""
        return [w for w in self.ways.values() if w.nodes and w.nodes[0] == node_id]


def parse_osm(osm_path: str | Path) -> OSMGraph:
    """Parse an OSM XML extract into :class:`OSMGraph` (nodes and ways only).

    Args:
        osm_path: ``.osm`` XML file.

    Returns:
        The graph. Relations are ignored; a way referencing a node the
        extract does not carry keeps the reference (its position is unknown
        and such a node is skipped by the geometry).
    """
    nodes: dict[str, tuple[float, float]] = {}
    ways: dict[str, OSMWay] = {}
    for _, elem in ET.iterparse(str(osm_path), events=("end",)):
        if elem.tag == "node":
            nid = elem.get("id")
            lat, lon = elem.get("lat"), elem.get("lon")
            if nid is not None and lat is not None and lon is not None:
                nodes[nid] = (float(lat), float(lon))
            elem.clear()
        elif elem.tag == "way":
            wid = elem.get("id")
            if wid is not None:
                ways[wid] = OSMWay(
                    id=wid,
                    nodes=tuple(str(nd.get("ref")) for nd in elem.findall("nd")),
                    tags={str(t.get("k")): str(t.get("v")) for t in elem.findall("tag")},
                )
            elem.clear()
    return OSMGraph(nodes=nodes, ways=ways)


def load_time_edge_id(edge_id: str) -> str:
    """The netconvert load-time id behind a compiled edge id.

    ``--ramps.guess`` splits an edge and names the piece
    ``<id>-AddedOffRampEdge`` / ``<id>-AddedOnRampEdge``; the load-time id is
    what ``--ramps.unset`` and a connection patch must name.
    """
    for suffix in _RAMP_SPLIT_SUFFIXES:
        if edge_id.endswith(suffix):
            return edge_id[: -len(suffix)]
    return edge_id


def osm_way_id(edge_id: str) -> str:
    """The OSM way id behind a compiled edge id.

    Strips the ramp-split suffix, the ``#n`` junction-split index and the
    leading ``-`` of a way compiled in its backward direction.
    """
    base = load_time_edge_id(edge_id).split("#", 1)[0]
    return base[1:] if base.startswith("-") else base


def _local_xy(
    graph: OSMGraph, node_id: str, origin: tuple[float, float]
) -> tuple[float, float] | None:
    """Node position in metres east/north of ``origin`` (equirectangular)."""
    pos = graph.nodes.get(node_id)
    if pos is None:
        return None
    lat, lon = pos
    lat0, lon0 = origin
    return (
        (lon - lon0) * _M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)),
        (lat - lat0) * _M_PER_DEG_LAT,
    )


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _signed_offset(point: tuple[float, float], polyline: Sequence[tuple[float, float]]) -> float:
    """Signed lateral offset of ``point`` from ``polyline`` [m]: + left, − right.

    The nearest segment is found first (distance to the clamped projection);
    the sign is the cross product of that segment's direction with the vector
    from its start to the point — positive when the point lies to the left
    of the direction of travel (x east, y north).
    """
    best: tuple[float, float] | None = None  # (distance, signed offset)
    px, py = point
    for (ax, ay), (bx, by) in pairwise(polyline):
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        if seg == 0.0:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (seg * seg)))
        qx, qy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        cross = dx * (py - ay) - dy * (px - ax)
        signed = math.copysign(dist, cross) if cross != 0.0 else 0.0
        if best is None or dist < best[0]:
            best = (dist, signed)
    return best[1] if best is not None else math.nan


def _split_node(
    graph: OSMGraph, from_way: OSMWay | None, link_way: OSMWay, hint: str
) -> str | None:
    """The OSM node where the link leaves the mainline.

    ``hint`` is the compiled edge's to-node id, which netconvert keeps equal
    to the OSM node id at a plain junction; otherwise the first node the link
    shares with the diverging way, or the link's first node.
    """
    if hint in graph.nodes and hint in link_way.nodes:
        return hint
    if from_way is not None:
        shared = [n for n in link_way.nodes if n in from_way.nodes]
        if shared:
            return shared[0]
    return link_way.nodes[0] if link_way.nodes else None


def _continuing_polyline(
    graph: OSMGraph,
    split_node: str,
    from_way: OSMWay | None,
    link_way: OSMWay,
    continuing_way: OSMWay | None,
    min_length_m: float = CONTINUING_MIN_M,
) -> list[tuple[float, float]]:
    """Mainline geometry downstream of the split, in local metres.

    Taken, in order of preference, from the compiled continuing edge's own
    way when it passes through the split node, from the rest of the
    diverging way when the split node lies inside it, else from any
    non-link way of the same highway class leaving the node; then extended
    along same-class ways until ``min_length_m`` is collected.
    """
    origin = graph.nodes.get(split_node)
    if origin is None:
        return []
    highway = from_way.highway if from_way is not None else ""

    def _tail(way: OSMWay) -> tuple[str, ...]:
        if split_node not in way.nodes:
            return ()
        idx = way.nodes.index(split_node)
        return way.nodes[idx:]

    refs: tuple[str, ...] = ()
    for candidate in (continuing_way, from_way):
        if candidate is not None and candidate.id != link_way.id:
            refs = _tail(candidate)
            if len(refs) >= 2:
                break
            refs = ()

    def _heading_into(node_id: str, way: OSMWay | None) -> tuple[float, float] | None:
        """Unit direction of ``way`` arriving at ``node_id`` (its last segment)."""
        if way is None or node_id not in way.nodes:
            return None
        idx = way.nodes.index(node_id)
        for prev in reversed(way.nodes[:idx]):
            a, b = _local_xy(graph, prev, origin), _local_xy(graph, node_id, origin)
            if a is not None and b is not None and math.dist(a, b) > 0.0:
                return ((b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b))
        return None

    def _straightest(candidates: list[OSMWay], arriving: tuple[float, float] | None) -> OSMWay:
        """Of the ways leaving a node, the one continuing straightest.

        At a genuine fork two same-class ways leave the node; file order
        would pick whichever the extract lists first and could call the
        branch the mainline (a right link then reads as left of it). The
        continuation is the way whose first segment turns least from the
        arriving direction; without an arriving direction, file order.
        """
        if arriving is None or len(candidates) == 1:
            return candidates[0]

        def _turn(way: OSMWay) -> float:
            start = _local_xy(graph, way.nodes[0], origin)
            for ref in way.nodes[1:]:
                point = _local_xy(graph, ref, origin)
                if start is not None and point is not None and math.dist(start, point) > 0.0:
                    d = math.dist(start, point)
                    dot = arriving[0] * (point[0] - start[0]) + arriving[1] * (point[1] - start[1])
                    return -dot / d  # smallest = straightest
            return math.inf

        return min(candidates, key=_turn)

    arriving = _heading_into(split_node, from_way)
    if len(refs) < 2:
        candidates = [
            way
            for way in graph.ways_from(split_node)
            if way.id != link_way.id
            and not way.is_link
            and (not highway or way.highway == highway)
            and len(way.nodes) >= 2
        ]
        if candidates:
            refs = _straightest(candidates, arriving).nodes
    if len(refs) < 2:
        return []

    points = [p for p in (_local_xy(graph, n, origin) for n in refs) if p is not None]
    last = refs[-1]
    seen = {split_node}
    while _polyline_length(points) < min_length_m and last not in seen:
        seen.add(last)
        nxt = [
            w
            for w in graph.ways_from(last)
            if not w.is_link and (not highway or w.highway == highway) and len(w.nodes) >= 2
        ]
        if not nxt:
            break
        step = (
            (points[-1][0] - points[-2][0], points[-1][1] - points[-2][1])
            if len(points) >= 2
            else None
        )
        chosen = _straightest(nxt, step)
        last = chosen.nodes[-1]
        points += [p for p in (_local_xy(graph, n, origin) for n in chosen.nodes[1:]) if p]
    return points


def link_offsets(
    graph: OSMGraph,
    link_way: OSMWay,
    split_node: str,
    mainline: Sequence[tuple[float, float]],
    sample_m: float = LINK_SAMPLE_M,
) -> tuple[float, ...]:
    """Signed lateral offsets [m] of the link's first nodes from the mainline.

    Nodes after the split node are sampled until ``sample_m`` of link length
    has been walked (the first node past the split is always included).
    Positive = left of the direction of travel, negative = right.
    """
    origin = graph.nodes.get(split_node)
    if origin is None or split_node not in link_way.nodes or len(mainline) < 2:
        return ()
    start = link_way.nodes.index(split_node)
    out: list[float] = []
    walked = 0.0
    prev = _local_xy(graph, split_node, origin)
    for ref in link_way.nodes[start + 1 :]:
        point = _local_xy(graph, ref, origin)
        if point is None:
            continue
        if prev is not None:
            walked += math.dist(prev, point)
        prev = point
        out.append(round(_signed_offset(point, mainline), 1))
        if walked >= sample_m:
            break
    return tuple(out)


def side_of_offsets(offsets: Sequence[float], min_offset_m: float = MIN_SIDE_OFFSET_M) -> Side:
    """Which side the offsets put the link on: the sign of the largest one."""
    usable = [o for o in offsets if not math.isnan(o)]
    if not usable:
        return "unknown"
    extreme = max(usable, key=abs)
    if abs(extreme) < min_offset_m:
        return "unknown"
    return "left" if extreme > 0.0 else "right"


def osm_exit_side(
    graph: OSMGraph,
    from_way_id: str,
    link_way_id: str,
    *,
    continuing_way_id: str | None = None,
    split_node: str | None = None,
) -> tuple[Side, tuple[float, ...]]:
    """The side a link leaves the mainline on, from OSM geometry alone.

    Args:
        graph: The parsed extract.
        from_way_id: OSM way the exit diverges from.
        link_way_id: OSM way of the leaving link.
        continuing_way_id: OSM way of the mainline past the split, when
            known (the compiled continuing edge's way); otherwise found by
            walking the extract.
        split_node: The diverge node, when known.

    Returns:
        ``(side, offsets)`` — the side and the signed offsets [m] of the
        link's sampled nodes (+ left, − right). ``("unknown", ())`` when
        the extract lacks the ways, the split node or a continuing way.
    """
    link = graph.ways.get(link_way_id)
    if link is None:
        return "unknown", ()
    from_way = graph.ways.get(from_way_id)
    node = split_node or _split_node(graph, from_way, link, "")
    if node is None:
        return "unknown", ()
    continuing = graph.ways.get(continuing_way_id) if continuing_way_id else None
    mainline = _continuing_polyline(graph, node, from_way, link, continuing)
    offsets = link_offsets(graph, link, node, mainline)
    return side_of_offsets(offsets), offsets


@dataclass(frozen=True)
class TurnLanesReading:
    """What a ``turn:lanes`` tag says about an exit (lanes listed left → right)."""

    side: Side
    n_exit: int
    """Lanes on that side carrying a turn toward it."""
    n_option: int
    """Of those, lanes that also continue (``through;slight_right`` and the like)."""


def read_turn_lanes(tag: str | None) -> TurnLanesReading | None:
    """Read a ``turn:lanes`` tag as an exit side and lane count.

    Trailing lanes containing ``right`` are a right exit, leading lanes
    containing ``left`` a left exit; ``through`` on such a lane makes it an
    option lane. A tag with both, or neither, reads as ``unknown``.
    """
    if not tag:
        return None
    lanes = tag.split("|")

    def _count(seq: Sequence[str], word: str) -> tuple[int, int]:
        n = n_opt = 0
        for lane in seq:
            if word not in lane:
                break
            n += 1
            n_opt += "through" in lane
        return n, n_opt

    n_right, opt_right = _count(list(reversed(lanes)), "right")
    n_left, opt_left = _count(lanes, "left")
    if n_right and not n_left:
        return TurnLanesReading("right", n_right, opt_right)
    if n_left and not n_right:
        return TurnLanesReading("left", n_left, opt_left)
    return TurnLanesReading("unknown", 0, 0)


def compiled_side(from_lanes: Iterable[int], n_lanes: int) -> CompiledSide:
    """Where the exit's feeding lanes sit on the compiled edge (lane 0 = rightmost)."""
    lanes = set(from_lanes)
    if not lanes:
        return "middle"
    if lanes >= set(range(n_lanes)):
        return "all"
    if 0 in lanes:
        return "rightmost"
    if n_lanes - 1 in lanes:
        return "leftmost"
    return "middle"


@dataclass(frozen=True)
class SplitFinding:
    """One diverge from a corridor edge, audited (module docstring).

    Attributes:
        from_edge: Compiled mainline edge the exit leaves.
        exit_edge: Compiled edge the exit leads into (not on the corridor).
        continuing_edge: Compiled corridor edge past the split, or ``None``
            at the corridor's end.
        x_m: Chain position of the split [m] (the from-edge's end).
        osm_way: OSM way id behind ``from_edge``.
        osm_lanes: The way's ``lanes`` tag, when parsable.
        turn_lanes: The way's raw ``turn:lanes`` tag, when present.
        turn_lanes_side: Its reading (``unknown`` when untagged or unreadable).
        osm_side: Side the link leaves on, from the extract's geometry.
        osm_offsets_m: Signed offsets [m] of the link's sampled nodes from
            the continuing mainline (+ left, − right).
        compiled_lanes: Lane count of the compiled from-edge.
        exit_from_lanes: Its lanes feeding the exit (sorted).
        compiled_side: Where those lanes sit.
        option_lanes: Of those, lanes that also feed the continuing edge.
        added_lane: The compiled edge has more lanes than the OSM tag.
        exit_lanes: Lane count of the exit edge.
        continuing_lanes: Lane count of the continuing edge, when there is one.
        verdict: :data:`Verdict`.
        remedy: What fixes it, in the engine's terms; empty when ``ok``.
    """

    from_edge: str
    exit_edge: str
    continuing_edge: str | None
    x_m: float
    osm_way: str
    osm_lanes: int | None
    turn_lanes: str | None
    turn_lanes_side: Side
    osm_side: Side
    osm_offsets_m: tuple[float, ...]
    compiled_lanes: int
    exit_from_lanes: tuple[int, ...]
    compiled_side: CompiledSide
    option_lanes: tuple[int, ...]
    added_lane: bool
    exit_lanes: int
    continuing_lanes: int | None
    verdict: Verdict
    remedy: str
    exit_connections: tuple[tuple[int, int], ...] = ()
    """The compiled ``(fromLane, toLane)`` pairs into ``exit_edge``, sorted.
    Internal (not in :meth:`as_dict`): a connection patch restating another
    exit of the same edge repeats these, since netconvert drops every
    computed connection of an edge that a patch names."""

    @property
    def is_defect(self) -> bool:
        return self.verdict in DEFECT_VERDICTS

    @property
    def is_ramp_split_piece(self) -> bool:
        """``from_edge`` is a piece ``--ramps.guess`` split off (one lane added)."""
        return self.from_edge != load_time_edge_id(self.from_edge)

    @property
    def load_time_lanes(self) -> int:
        """Lane count of the load-time edge a patch names.

        A ramp-split piece carries the one lane guessing added on top of the
        load-time edge; a patch is read before the split exists, so it must
        use the count without it (with the edge in ``--ramps.unset``, the
        piece is not built and the patch lands where it was computed).
        """
        return self.compiled_lanes - 1 if self.is_ramp_split_piece else self.compiled_lanes

    @property
    def expected_side(self) -> Side:
        """The side the exit should be compiled on: geometry first, tag second."""
        return self.osm_side if self.osm_side != "unknown" else self.turn_lanes_side

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready record (what the API's corridor summary carries)."""
        return {
            "from_edge": self.from_edge,
            "exit_edge": self.exit_edge,
            "continuing_edge": self.continuing_edge,
            "x_m": self.x_m,
            "osm_way": self.osm_way,
            "osm_lanes": self.osm_lanes,
            "turn_lanes": self.turn_lanes,
            "turn_lanes_side": self.turn_lanes_side,
            "osm_side": self.osm_side,
            "osm_offsets_m": list(self.osm_offsets_m),
            "compiled_lanes": self.compiled_lanes,
            "exit_from_lanes": list(self.exit_from_lanes),
            "compiled_side": self.compiled_side,
            "option_lanes": list(self.option_lanes),
            "added_lane": self.added_lane,
            "exit_lanes": self.exit_lanes,
            "continuing_lanes": self.continuing_lanes,
            "verdict": self.verdict,
            "remedy": self.remedy,
        }


def _verdict(expected: Side, side: CompiledSide, added_lane: bool) -> tuple[Verdict, str]:
    """Verdict and its remedy from the expected side and the compiled side."""
    if expected == "unknown":
        return "unknown", "no continuing mainline way found in the extract; check by hand"
    if side == "all" or side == expected + "most":
        return "ok", ""
    if added_lane:
        return (
            "added_lane_wrong_side",
            "the lane feeding the exit was added by ramp guessing on the wrong side: "
            "add `--ramps.unset <edge>` to netconvert_extra (scripts/onboard_corridor.py "
            "--write-split-patch does), then re-audit",
        )
    return (
        "wrong_side",
        "restate the split with an OSMNetwork.patch_files connection patch "
        "(scripts/onboard_corridor.py --write-split-patch writes it)",
    )


def audit_splits(
    net_path: str | Path,
    osm_path: str | Path,
    corridor_edges: Sequence[str],
) -> list[SplitFinding]:
    """Audit every diverge from the corridor against the OSM extract.

    Args:
        net_path: Compiled ``.net.xml`` (as ``osm_import`` wrote it).
        osm_path: The extract it was compiled from.
        corridor_edges: Corridor edge ids in driving order (load-time ids;
            ramp-split pieces are expanded here).

    Returns:
        One finding per (corridor edge, non-corridor outgoing edge), in
        chain order. An edge leaving the corridor at a plain lane-to-lane
        junction with no exit yields nothing.
    """
    net = sumolib.net.readNet(str(net_path))
    graph = parse_osm(osm_path)
    present = [e.getID() for e in net.getEdges(withInternal=False)]
    chain = expand_ramp_splits(list(corridor_edges), present)
    chain_set = set(chain)
    x = 0.0
    findings: list[SplitFinding] = []
    for i, edge_id in enumerate(chain):
        edge = net.getEdge(edge_id)
        x += float(edge.getLength())
        n_lanes = int(edge.getLaneNumber())
        outgoing = edge.getOutgoing()
        continuing = chain[i + 1] if i + 1 < len(chain) else None
        cont_edge = next((e for e in outgoing if e.getID() == continuing), None)
        cont_lanes = (
            {c.getFromLane().getIndex() for c in outgoing[cont_edge]} if cont_edge else set()
        )
        from_way = graph.ways.get(osm_way_id(edge_id))
        for to_edge, conns in outgoing.items():
            if to_edge.getID() in chain_set:
                continue
            exit_lanes = tuple(sorted({c.getFromLane().getIndex() for c in conns}))
            link = graph.ways.get(osm_way_id(to_edge.getID()))
            side: Side = "unknown"
            offsets: tuple[float, ...] = ()
            if link is not None:
                node = _split_node(graph, from_way, link, str(edge.getToNode().getID()))
                if node is not None:
                    mainline = _continuing_polyline(
                        graph,
                        node,
                        from_way,
                        link,
                        graph.ways.get(osm_way_id(continuing)) if continuing else None,
                    )
                    offsets = link_offsets(graph, link, node, mainline)
                    side = side_of_offsets(offsets)
            tag = from_way.tags.get("turn:lanes") if from_way is not None else None
            reading = read_turn_lanes(tag)
            osm_lanes = from_way.lanes if from_way is not None else None
            added = osm_lanes is not None and n_lanes > osm_lanes
            where = compiled_side(exit_lanes, n_lanes)
            expected: Side = side if side != "unknown" else (reading.side if reading else "unknown")
            verdict, remedy = _verdict(expected, where, added)
            findings.append(
                SplitFinding(
                    from_edge=edge_id,
                    exit_edge=str(to_edge.getID()),
                    continuing_edge=continuing,
                    x_m=round(x, 1),
                    osm_way=osm_way_id(edge_id),
                    osm_lanes=osm_lanes,
                    turn_lanes=tag,
                    turn_lanes_side=reading.side if reading else "unknown",
                    osm_side=side,
                    osm_offsets_m=offsets,
                    compiled_lanes=n_lanes,
                    exit_from_lanes=exit_lanes,
                    compiled_side=where,
                    option_lanes=tuple(sorted(set(exit_lanes) & cont_lanes)),
                    added_lane=added,
                    exit_lanes=int(to_edge.getLaneNumber()),
                    continuing_lanes=int(cont_edge.getLaneNumber()) if cont_edge else None,
                    verdict=verdict,
                    remedy=remedy,
                    exit_connections=tuple(
                        sorted(
                            (c.getFromLane().getIndex(), c.getToLane().getIndex()) for c in conns
                        )
                    ),
                )
            )
    return findings


def split_defects(findings: Iterable[SplitFinding]) -> list[SplitFinding]:
    """The findings whose verdict is a defect."""
    return [f for f in findings if f.is_defect]


def ramps_unset_edges(findings: Iterable[SplitFinding]) -> list[str]:
    """Load-time edge ids ``--ramps.unset`` must name.

    The ``added_lane_wrong_side`` findings, and every ``wrong_side`` finding
    on a ramp-split piece: its patch names the load-time edge with the
    load-time lane count (:attr:`SplitFinding.load_time_lanes`), and ramp
    guessing would otherwise rebuild the piece over the patched connections
    and move the exit again.
    """
    out: list[str] = []
    for f in findings:
        if f.verdict == "added_lane_wrong_side" or (
            f.verdict == "wrong_side" and f.is_ramp_split_piece
        ):
            edge = load_time_edge_id(f.from_edge)
            if edge not in out:
                out.append(edge)
    return out


def _connection_line(from_edge: str, to: str, from_lane: int, to_lane: int) -> str:
    return f'  <connection from="{from_edge}" to="{to}" fromLane="{from_lane}" toLane="{to_lane}"/>'


def connection_patch_lines(finding: SplitFinding) -> list[str]:
    """``<connection>`` lines restating one split on the side OSM draws it.

    Exit lanes: the ``turn:lanes`` count when the tag reads, else the exit
    edge's lane count; they are the rightmost lanes for a right exit, the
    leftmost for a left one. Every other lane continues, mapped onto the
    continuing edge in order; an option lane continues as well, keeping its
    index — the tag's option lanes when it reads for that side, else every
    exit lane the continuing edge has room for (a split where the mainline
    keeps its lane count is an option lane, not a drop lane). The lines are
    what ``data/osm/mndot_i94_wb_stpaul.splits.con.xml`` states for the
    Mounds/Kellogg split (five lanes to three: two drop lanes, no option).

    On a ramp-split piece the lines name the load-time edge with its
    load-time lane count (:attr:`SplitFinding.load_time_lanes`); the edge
    must then be in ``--ramps.unset`` (:func:`ramps_unset_edges`).

    Returns:
        Nothing for a finding without a continuing edge or a known side.
    """
    expected = finding.expected_side
    if finding.continuing_edge is None or finding.continuing_lanes is None or expected == "unknown":
        return []
    n = finding.load_time_lanes
    reading = read_turn_lanes(finding.turn_lanes)
    k = (
        reading.n_exit
        if reading and reading.side == expected and reading.n_exit
        else finding.exit_lanes
    )
    k = max(1, min(k, finding.exit_lanes, n - 1 if n > 1 else 1))
    from_edge = load_time_edge_id(finding.from_edge)
    cont, cont_n = finding.continuing_edge, finding.continuing_lanes
    if reading and reading.side == expected:
        n_option = min(reading.n_option, k)
    else:
        n_option = max(0, k - max(0, n - cont_n))
    lines: list[str] = []

    def _line(to: str, from_lane: int, to_lane: int) -> str:
        return _connection_line(from_edge, to, from_lane, to_lane)

    if expected == "right":
        exit_lanes = list(range(k))
        cont_lanes = list(range(k - n_option, n))
        shift = k - n_option
        for j in exit_lanes:
            lines.append(_line(finding.exit_edge, j, j))
        for j in cont_lanes:
            lines.append(_line(cont, j, max(0, min(j - shift, cont_n - 1))))
    else:
        exit_lanes = list(range(n - k, n))
        cont_lanes = list(range(0, n - k + n_option))
        for j in cont_lanes:
            lines.append(_line(cont, j, max(0, min(j, cont_n - 1))))
        for j in exit_lanes:
            lines.append(_line(finding.exit_edge, j, j - (n - k)))
    return lines


def split_patch_xml(findings: Iterable[SplitFinding], *, note: str = "") -> str:
    """A ``*.con.xml`` patch restating every ``wrong_side`` split.

    netconvert drops every computed connection of an edge a patch names, so
    a restated split also repeats, as compiled, the connections into every
    other exit leaving the same edge (a node with a right and a left exit
    would otherwise lose the one that was fine). An XML comment may not
    contain ``--`` (netconvert refuses the file), so runs of hyphens in
    ``note`` are collapsed.

    Args:
        findings: Audit findings; only ``wrong_side`` ones are restated
            (``added_lane_wrong_side`` is fixed by ``--ramps.unset``, see
            :func:`ramps_unset_edges`).
        note: Optional provenance line for the file's comment.

    Returns:
        The file text, or an empty string when nothing needs restating.
    """
    all_findings = list(findings)
    lines: list[str] = []
    for f in all_findings:
        if f.verdict != "wrong_side":
            continue
        lines += connection_patch_lines(f)
        # A piece's added lane is its lane 0 (netconvert 1.27.1 puts the
        # deceleration lane on the right and shifts the rest up by one, for a
        # left exit too), so a sibling's lanes shift down by one on the
        # load-time edge and a connection from the added lane is dropped.
        shift = 1 if f.is_ramp_split_piece else 0
        for other in all_findings:
            if other.from_edge == f.from_edge and other.exit_edge != f.exit_edge:
                lines += [
                    _connection_line(load_time_edge_id(f.from_edge), other.exit_edge, a - shift, b)
                    for a, b in other.exit_connections
                    if 0 <= a - shift < f.load_time_lanes
                ]
    if not lines:
        return ""
    lines = list(dict.fromkeys(lines))
    head = "<!-- split audit (microsim.split_audit): exits restated on the side OSM draws them"
    if note:
        head += f"; {re.sub(r'-{2,}', '-', note).replace('>', ' ')}"
    head += " -->"
    return "\n".join([head, "<connections>", *lines, "</connections>", ""])


def format_split_table(findings: Sequence[SplitFinding], *, label: str = "splits") -> list[str]:
    """Plain-text lines for the onboarding inventory, one split per line.

    Args:
        findings: The audit to print.
        label: Heading of the block — ``"splits"`` for the audit the scenario
            will compile, ``"splits before fixes"`` for the one the fixes
            were derived from.
    """
    defects = split_defects(findings)
    lines = [
        f"  {label} ({len(findings)} exits audited against the extract; "
        f"{len(defects)} defect{'s' if len(defects) != 1 else ''})"
    ]
    for f in findings:
        if f.osm_offsets_m:
            mags = [abs(o) for o in f.osm_offsets_m]
            osm = f"OSM {f.osm_side} ({min(mags):.0f}-{max(mags):.0f} m"
        else:
            osm = f"OSM {f.osm_side} (no geometry"
        osm += f"; turn:lanes {f.turn_lanes})" if f.turn_lanes else ")"
        lanes = ",".join(str(i) for i in f.exit_from_lanes) or "-"
        compiled = f"compiled {f.compiled_side} lane(s) {lanes} of {f.compiled_lanes}"
        if f.added_lane:
            compiled += f" (OSM lanes={f.osm_lanes}: a lane was added)"
        lines.append(
            f"    x={f.x_m / 1000.0:7.3f} km  {f.from_edge} -> {f.exit_edge}  {osm}  "
            f"{compiled}  {f.verdict.upper()}"
        )
        if f.remedy:
            lines.append(f"             fix: {f.remedy}")
    return lines
