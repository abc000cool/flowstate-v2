"""Layout audit: the compiled corridor, segment by segment, for a check against imagery.

WP-102 (Stage 1, item 4 of the Frisco plan): "Check the road layout by hand
against satellite imagery: lanes, ramps, acceleration lanes, lane drops. The
map source got Minnesota's ramps wrong, and the model inherits every map
error." On the I-94 WB St. Paul extract ``netconvert`` compiled two right-hand
exits onto the leftmost lanes and the map drew no acceleration lanes, so the
on-ramps starved (docs/ONBOARDING_MNDOT.md §6, §9). :mod:`microsim.split_audit`
catches the first kind of error at every exit; this module lists **everything
a person has to look at** on the compiled network the runner simulates, in
travel order, with a satellite link at each place, and runs mechanical checks
whose flags say what to look for.

What it produces (:func:`audit_layout` → :class:`LayoutAudit`):

* **Segments** (:class:`Segment`) — one per compiled corridor edge, ramp-split
  pieces included: start and end offset along the corridor, length, compiled
  lane count against the OSM ``lanes`` tag, posted speed compiled against the
  OSM ``maxspeed`` tag, and the WGS84 position of the segment's start.
* **Events** (:class:`LayoutEvent`) — on-ramp joins (:class:`RampJoin`: which
  compiled lane the ramp joins, on which side OSM draws it, and the
  acceleration / auxiliary lane it gets, with its compiled length), off-ramp
  splits (:class:`RampSplit`, the split audit's finding), lane drops and gains
  (:class:`LaneChange`: where the compiled lane count changes, on which side,
  and whether OSM agrees), weaving sections (:class:`Weave`: an auxiliary lane
  from an entrance to an exit), collector–distributor roads (:class:`CDRoad`)
  and speed-limit changes (:class:`SpeedChange`).
* **Flags** (:class:`Flag`) — the automatic checks of :data:`CHECKS`, each a
  named rule with its constant and its reason, each flag saying what a human
  should look for on the imagery.

Output: :meth:`LayoutAudit.to_json`, :meth:`LayoutAudit.to_csv` (segments and
events interleaved in travel order, with empty ``checked_by`` /
``imagery_date`` / ``result`` columns) and :meth:`LayoutAudit.to_markdown` (the
checklist a person works through; the procedure is docs/LAYOUT_CHECKLIST.md).
``scripts/layout_audit.py`` builds the network the way the runner does and
writes all three.

This is an inspection tool. It changes nothing in the network; a remedy is
stated in the engine's own terms (an ``OSMNetwork.patch_files`` patch,
``--ramps.unset``, an OSM correction) and applied only after the imagery
confirms the error (docs/FRISCO_PROTOCOL.md §7.3).

Importable without SUMO running: ``sumolib`` reads the compiled net, the OSM
extract is a light XML parse (:func:`microsim.split_audit.parse_osm`).
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import sumolib

from flowstate_core.units import h_to_s, kmh_to_ms, ms_to_kmh
from microsim.geo import net_xy_to_lonlat, ramps_for_chain
from microsim.networks import RAMP_SPLIT_OFF, RAMP_SPLIT_ON, WEAVE_LENGTH_MAX_M, expand_ramp_splits
from microsim.split_audit import (
    CONTINUING_MIN_M,
    LINK_SAMPLE_M,
    CompiledSide,
    OSMGraph,
    OSMWay,
    Side,
    SplitFinding,
    _local_xy,
    _polyline_length,
    _signed_offset,
    audit_splits,
    compiled_side,
    load_time_edge_id,
    osm_way_id,
    parse_osm,
    side_of_offsets,
)

#: Version tag of the JSON record (:meth:`LayoutAudit.to_dict`).
SCHEMA: Final[str] = "flowstate.layout_audit/1"

Severity = Literal["defect", "warning", "info"]
"""``defect``: the compiled net contradicts the map in a way known to break the
model (an exit or entrance on the wrong side). ``warning``: the net or the map
is probably wrong here; the imagery decides. ``info``: recorded so the person
checking knows what the model assumes (a guessed lane, a speed change)."""

EventKind = Literal[
    "off_ramp", "cd_road", "lane_drop", "lane_gain", "speed_change", "on_ramp", "weave"
]

#: Order of events at one position: traffic leaves before it joins.
_KIND_ORDER: Final[dict[str, int]] = {
    "off_ramp": 0,
    "cd_road": 1,
    "lane_drop": 2,
    "lane_gain": 3,
    "speed_change": 4,
    "on_ramp": 5,
    "weave": 6,
}

JoinVerdict = Literal["ok", "wrong_side", "added_lane_wrong_side", "unknown"]
LaneSide = Literal["right", "left", "middle", "all", "unknown"]
AuxEnd = Literal["drop", "merge", "exit", "through", "corridor_end"]
ChangeSource = Literal[
    "osm", "ramp_guessing", "ramp_guessing_likely", "merge_model_patch", "unknown", "unexplained"
]

# --- named constants of the automatic checks ---------------------------------

#: Segments shorter than this [m] are flagged (check f). About two car lengths
#: plus their gaps: no real lane configuration lasts that briefly, so an edge
#: this short is a way split at a node that serves nothing (a tag change, a
#: bridge, two junction nodes drawn a few metres apart) — harmless when it is
#: only that, a defect when a gore or a lane change was mapped onto it.
SHORT_SEGMENT_M: Final[float] = 20.0

#: An acceleration lane shorter than this [m] is flagged with the missing ones
#: (check b). It is netconvert's own default ``--ramps.ramp-length``: a
#: compiled acceleration lane shorter than the shortest one netconvert would
#: guess is a lane added at one node and dropped at the next, not a lane a
#: driver can accelerate in.
SHORT_AUX_LANE_M: Final[float] = 100.0

#: How far an added lane is followed downstream [m] before it counts as a
#: through lane (an interchange that widens the road) rather than an
#: acceleration or auxiliary lane. The weave bound of
#: :func:`microsim.networks.weave_sections` (HCM 7th ed. ch. 13, L_MAX).
AUX_LANE_MAX_M: Final[float] = WEAVE_LENGTH_MAX_M

#: Compiled and OSM speeds closer than this [m/s] are the same limit (check e).
#: The ``.net.xml`` stores speeds to 2 decimals and a ``maxspeed`` in mph
#: converts inexactly; the smallest real step between posted limits is 5 mph
#: (2.2 m/s) or 10 km/h (2.8 m/s), far above this.
SPEED_TOLERANCE_MS: Final[float] = 0.5

#: Zoom level of the imagery links: about 0.6 m per pixel at mid latitudes,
#: enough to count lanes and see lane lines.
IMAGERY_ZOOM: Final[int] = 18

#: Metres in an international mile (exact by definition, 1959) and in a
#: nautical mile (exact), for OSM ``maxspeed`` values in ``mph`` / ``knots``.
M_PER_MILE: Final[float] = 1609.344
M_PER_NAUTICAL_MILE: Final[float] = 1852.0

#: m/s per mph and per knot (derived; no inline magic numbers).
MS_PER_MPH: Final[float] = M_PER_MILE / h_to_s(1.0)
MS_PER_KNOT: Final[float] = M_PER_NAUTICAL_MILE / h_to_s(1.0)

#: OSM ``maxspeed`` values this module reads: a number with an optional unit
#: (no unit means km/h, the OSM convention). Anything else (``none``,
#: ``signals``, ``US:urban``, several values) is reported as unreadable.
_MAXSPEED_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph|knots)?\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class CheckRule:
    """One automatic check: its rule, the constant it uses and why.

    Attributes:
        id: The id flags carry (:attr:`Flag.check`).
        letter: Its letter in the WP-102 work package, ``""`` for the notes.
        title: Short name.
        rule: The mechanical rule, in words.
        constant: The named constant(s) and value(s) it uses, or ``""``.
        reason: Why the rule exists.
    """

    id: str
    letter: str
    title: str
    rule: str
    constant: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "letter": self.letter,
            "title": self.title,
            "rule": self.rule,
            "constant": self.constant,
            "reason": self.reason,
        }


#: The automatic checks, in the order of the work package (a)–(g), then the
#: notes that are recorded but are not rules about a defect.
CHECKS: Final[tuple[CheckRule, ...]] = (
    CheckRule(
        "on_ramp_side",
        "a",
        "On-ramp joins on the side OSM draws it",
        "The side the ramp's last nodes lie on, relative to the mainline arriving at the merge "
        "(signed lateral offset of the link nodes within the first LINK_SAMPLE_M of the merge; "
        "negative = right of travel), must match the compiled lanes the ramp joins (SUMO lane 0 "
        "is the rightmost): a right-hand ramp joining the leftmost lane is a defect. When the "
        "lane it joins is one ramp guessing added (more compiled lanes than the OSM tag) the "
        "remedy is --ramps.unset; otherwise a connection patch.",
        f"LINK_SAMPLE_M = {LINK_SAMPLE_M:g} m; MIN_SIDE_OFFSET_M = 1 m (microsim.split_audit)",
        "The merge counterpart of the split audit: on I-94 WB St. Paul netconvert compiled two "
        "right-hand exits from the leftmost lanes and the corridor locked "
        "(docs/ONBOARDING_MNDOT.md §9); an entrance compiled on the wrong side puts entering "
        "traffic into the fast lane.",
    ),
    CheckRule(
        "no_accel_lane",
        "b",
        "On-ramp without an acceleration lane",
        "Flagged when every lane the ramp joins is also fed by the mainline edge upstream (the "
        "ramp merges straight into a through lane at a junction, so no lane is added and none "
        "drops downstream), or when the added lane runs less than SHORT_AUX_LANE_M before it "
        "drops or merges.",
        f"SHORT_AUX_LANE_M = {SHORT_AUX_LANE_M:g} m",
        "SUMO makes a ramp vehicle yield at a junction merge with no lane of its own; the "
        "Minnesota on-ramps delivered 4-6 % of their demand for this reason "
        "(docs/ONBOARDING_MNDOT.md §6). OSM rarely draws acceleration lanes.",
    ),
    CheckRule(
        "lanes_vs_osm",
        "c",
        "Compiled lane count differs from the OSM lanes tag",
        "Each segment's compiled lane count is compared with its OSM way's lanes tag "
        "(lanes:forward / lanes:backward on a two-way way). Flagged when they differ, with both "
        "values, or when the tag is missing (netconvert then uses its typemap default). A lane "
        "that ramp guessing added on a split-off piece is a note, not a warning.",
        "",
        "netconvert's ramp guessing (--ramps.guess) adds lanes the map does not draw, and a "
        "missing tag silently becomes a default; either way the model's lane count is not the "
        "map's.",
    ),
    CheckRule(
        "lane_change_not_in_osm",
        "d",
        "Lane drop or gain without a matching change in OSM",
        "At every junction where the compiled lane count changes (or a lane ends or begins), "
        "the OSM lanes tags of the ways on either side must change by the same amount. Lane "
        "changes netconvert made at a ramp-guessing piece, and lanes the runner's merge models "
        "terminated, are recorded as notes instead.",
        "",
        "A lane drop the road does not have is a bottleneck the model invents; one it misses "
        "removes a real one.",
    ),
    CheckRule(
        "speed_limit",
        "e",
        "Speed limit missing, different from OSM, or changing",
        "Flagged when the OSM way has no readable maxspeed (netconvert then uses its typemap "
        "default), when the compiled speed differs from the tag by more than "
        "SPEED_TOLERANCE_MS, and (as a note) wherever the compiled speed changes between two "
        "segments.",
        f"SPEED_TOLERANCE_MS = {SPEED_TOLERANCE_MS:g} m/s",
        "netconvert's default for highway=motorway is 39.44 m/s (142 km/h, 88 mph), above "
        "every US limit; the road's speed limit caps every driver's desired speed.",
    ),
    CheckRule(
        "short_segment",
        "f",
        "Very short segment",
        "A compiled corridor edge shorter than SHORT_SEGMENT_M.",
        f"SHORT_SEGMENT_M = {SHORT_SEGMENT_M:g} m",
        "Usually a mapping artifact (a way split at a node that serves nothing); a gore or lane "
        "change drawn onto it sits at the wrong place.",
    ),
    CheckRule(
        "split_side",
        "g",
        "Off-ramp split on the wrong side (split audit)",
        "microsim.split_audit's verdict for every exit: wrong_side or added_lane_wrong_side is "
        "a defect, unknown (the extract cannot place the exit) a warning.",
        "LINK_SAMPLE_M, MIN_SIDE_OFFSET_M (microsim.split_audit)",
        "The I-94 WB St. Paul lock (docs/ONBOARDING_MNDOT.md §9).",
    ),
    CheckRule(
        "guessed_aux_lane",
        "",
        "Lane guessed by netconvert (note)",
        "An acceleration or deceleration lane that exists only because of --ramps.guess (an "
        "-AddedOnRampEdge / -AddedOffRampEdge piece, or a lane the attach edge has beyond its "
        "OSM tag while guessing is on).",
        "",
        "Its length is --ramps.ramp-length, not the road's; the imagery says whether the lane "
        "exists and how long it really is.",
    ),
    CheckRule(
        "lane_change_left",
        "",
        "Lane ends or begins on the left (note)",
        "A lane drop or gain whose ending or starting lanes are the leftmost.",
        "",
        "Left-side lane drops and adds are uncommon on US freeways and netconvert chooses the "
        "side itself; it chose wrongly at two Minnesota exits.",
    ),
    CheckRule(
        "merge_model_lane_end",
        "",
        "Lane terminated by a merge model (note)",
        "A lane drop the runner's merge models made (RampSpec.merge terminates a guessed "
        "acceleration lane that spills past its attach edge).",
        "",
        "Not a map feature: the model ends the lane there by construction.",
    ),
)

_CHECK_BY_ID: Final[dict[str, CheckRule]] = {c.id: c for c in CHECKS}

#: What the audit cannot see; printed in every checklist (docs/LAYOUT_CHECKLIST.md).
CANNOT_SEE: Final[tuple[str, ...]] = (
    "Lane markings: solid or dashed lines, exit-only arrows, painted gores, HOV or managed-lane "
    "restrictions. The audit sees lane counts and lane-to-lane connections only.",
    "Real lengths of acceleration, deceleration and auxiliary lanes when OSM does not draw them: "
    "a compiled length is netconvert's guess (--ramps.ramp-length) or the length of the OSM "
    "way that happens to carry the extra lane, which ends wherever the mapper split the way.",
    "Exact positions: gores, taper ends and lane-drop points sit at OSM nodes, which mappers "
    "place within tens of metres; offsets are measured along SUMO edges.",
    "Which side the map puts a lane drop or gain on: the OSM lanes tag is a count. Only "
    "turn:lanes (when present) and ramp geometry give a side; the side reported for a lane "
    "change is the compiled one.",
    "Lane widths, shoulders, grades, curvature and sight distance (none are in the model).",
    "Ramp meters, signals, signs other than the maxspeed tag, advisory and variable speed "
    "limits, and work zones.",
    "Ramps outside the extract or not kept in the scenario, and ramps that join or leave a "
    "collector-distributor road (inventory only in the scenario).",
    "Imagery age: a satellite image can be years old. Record its date; construction since then "
    "is invisible to the map and to the image.",
)


class RampLike(Protocol):
    """A ramp as the scenario (:class:`flowstate_core.config.RampSpec`) or the
    onboarding discovery (:class:`microsim.geo.RampCandidate`) carries it."""

    @property
    def kind(self) -> str: ...

    @property
    def edges(self) -> Sequence[str]: ...

    @property
    def attach_edge(self) -> str: ...

    @property
    def name(self) -> str: ...


# --- records ------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    """One automatic finding.

    Attributes:
        check: The :class:`CheckRule` id.
        severity: :data:`Severity`.
        message: What was found, with the values.
        look_for: What the person checking should look for on the imagery.
    """

    check: str
    severity: Severity
    message: str
    look_for: str

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "look_for": self.look_for,
        }


@dataclass(frozen=True)
class Segment:
    """One compiled corridor edge, in travel order.

    Attributes:
        index: Position in the chain (0 = most upstream).
        edge: Compiled edge id (a ramp-guessing piece keeps its suffix).
        osm_way: The OSM way behind it.
        x_start_m: Offset of its start along the corridor [m].
        x_end_m: Offset of its end [m].
        length_m: Compiled length [m].
        lanes: Compiled lane count.
        osm_lanes: The way's lane count for this direction, when tagged.
        osm_lanes_tag: The raw tag it was read from, when present.
        speed_ms: Compiled speed limit (fastest lane) [m/s].
        osm_maxspeed: The way's raw ``maxspeed`` tag, when present.
        osm_maxspeed_ms: It read as m/s, when readable.
        guessed_piece: The edge is a piece ramp guessing split off.
        lat: Latitude of its start [deg] (``None`` without a projection).
        lon: Longitude of its start [deg].
        flags: Automatic findings about the segment itself.
    """

    index: int
    edge: str
    osm_way: str
    x_start_m: float
    x_end_m: float
    length_m: float
    lanes: int
    osm_lanes: int | None
    osm_lanes_tag: str | None
    speed_ms: float
    osm_maxspeed: str | None
    osm_maxspeed_ms: float | None
    guessed_piece: bool
    lat: float | None
    lon: float | None
    flags: tuple[Flag, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "edge": self.edge,
            "osm_way": self.osm_way,
            "x_start_m": self.x_start_m,
            "x_end_m": self.x_end_m,
            "length_m": self.length_m,
            "lanes": self.lanes,
            "osm_lanes": self.osm_lanes,
            "osm_lanes_tag": self.osm_lanes_tag,
            "speed_ms": self.speed_ms,
            "speed_kmh": round(ms_to_kmh(self.speed_ms), 1),
            "speed_mph": round(self.speed_ms / MS_PER_MPH, 1),
            "osm_maxspeed": self.osm_maxspeed,
            "osm_maxspeed_ms": self.osm_maxspeed_ms,
            "guessed_piece": self.guessed_piece,
            "lat": self.lat,
            "lon": self.lon,
            "links": imagery_links(self.lat, self.lon),
            "flags": [f.as_dict() for f in self.flags],
        }


@dataclass(frozen=True)
class RampJoin:
    """An on-ramp joining the corridor (check a, check b).

    Attributes:
        ramp: The scenario's ramp name (the joining edge id when it has none).
        ramp_edge: Compiled edge that joins the corridor.
        attach_edge: Corridor edge it joins (a ramp-guessing piece when one exists).
        attach_lanes: Lane count of the attach edge.
        joined_lanes: Lanes of the attach edge the ramp feeds (lane 0 = rightmost).
        compiled_side: Where those lanes sit.
        osm_side: Side the ramp's last nodes lie on, from the extract's geometry.
        osm_offsets_m: Signed offsets [m] of those nodes from the arriving
            mainline (+ left, − right), nearest the merge first.
        verdict: :data:`JoinVerdict`.
        remedy: What fixes a wrong-side join, in the engine's terms.
        upstream_edge: The corridor edge before the attach edge.
        mainline_fed: Whether the joined lanes also carry the mainline from
            ``upstream_edge`` (``True`` = no lane of the ramp's own; ``None``
            at the corridor's first edge).
        aux_lanes: Joined lanes the mainline does not feed (added lanes).
        aux_length_m: Compiled length of the longest added lane, from the
            merge node to where it ends [m].
        aux_end: How that lane ends (:data:`AuxEnd`).
        aux_exit_edge: The exit it reaches when ``aux_end == "exit"``.
        aux_edges: Corridor edges it runs along.
        aux_guessed: The added lane exists because of ramp guessing.
        aux_exit_option: At its exit the lane also continues (an option lane).
        osm_lanes_upstream: OSM lanes of the upstream edge's way.
        osm_lanes_attach: OSM lanes of the attach edge's way.
        cd_pair: The C-D pair id when this is a C-D road's re-entry.
    """

    ramp: str
    ramp_edge: str
    attach_edge: str
    attach_lanes: int
    joined_lanes: tuple[int, ...]
    compiled_side: CompiledSide
    osm_side: Side
    osm_offsets_m: tuple[float, ...]
    verdict: JoinVerdict
    remedy: str
    upstream_edge: str | None
    mainline_fed: bool | None
    aux_lanes: tuple[int, ...]
    aux_length_m: float | None
    aux_end: AuxEnd | None
    aux_exit_edge: str | None
    aux_edges: tuple[str, ...]
    aux_guessed: bool
    osm_lanes_upstream: int | None
    osm_lanes_attach: int | None
    cd_pair: str = ""
    aux_exit_option: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ramp": self.ramp,
            "ramp_edge": self.ramp_edge,
            "attach_edge": self.attach_edge,
            "attach_lanes": self.attach_lanes,
            "joined_lanes": list(self.joined_lanes),
            "compiled_side": self.compiled_side,
            "osm_side": self.osm_side,
            "osm_offsets_m": list(self.osm_offsets_m),
            "verdict": self.verdict,
            "remedy": self.remedy,
            "upstream_edge": self.upstream_edge,
            "mainline_fed": self.mainline_fed,
            "aux_lanes": list(self.aux_lanes),
            "aux_length_m": self.aux_length_m,
            "aux_end": self.aux_end,
            "aux_exit_edge": self.aux_exit_edge,
            "aux_edges": list(self.aux_edges),
            "aux_exit_option": self.aux_exit_option,
            "aux_guessed": self.aux_guessed,
            "osm_lanes_upstream": self.osm_lanes_upstream,
            "osm_lanes_attach": self.osm_lanes_attach,
            "cd_pair": self.cd_pair,
        }


@dataclass(frozen=True)
class RampSplit:
    """An off-ramp leaving the corridor: the split audit's finding (check g).

    Attributes:
        ramp: The scenario's ramp name (the exit edge id when it has none).
        finding: :class:`microsim.split_audit.SplitFinding`.
        decel_lane_guessed: The exit leaves a deceleration lane ramp guessing added.
        decel_length_m: That lane's compiled length [m] (the piece's length).
        cd_pair: The C-D pair id when this is a C-D road's split.
    """

    ramp: str
    finding: SplitFinding
    decel_lane_guessed: bool
    decel_length_m: float | None
    cd_pair: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ramp": self.ramp,
            **self.finding.as_dict(),
            "decel_lane_guessed": self.decel_lane_guessed,
            "decel_length_m": self.decel_length_m,
            "cd_pair": self.cd_pair,
        }


@dataclass(frozen=True)
class LaneChange:
    """A lane drop or gain at the junction of two corridor edges (check d).

    Attributes:
        from_edge: The edge before the junction.
        to_edge: The edge after it.
        lanes_before: Compiled lanes of ``from_edge``.
        lanes_after: Compiled lanes of ``to_edge``.
        side: Which side the lanes end or begin on.
        how: In words: which lanes end or begin, and how.
        ending_lanes: Lanes of ``from_edge`` that do not continue into ``to_edge``.
        starting_lanes: Lanes of ``to_edge`` that ``from_edge`` does not feed.
        osm_lanes_before: OSM lanes of ``from_edge``'s way.
        osm_lanes_after: OSM lanes of ``to_edge``'s way.
        osm_agrees: The OSM tags change by the compiled amount (``None``
            when a tag is missing).
        source: Where the change comes from (:data:`ChangeSource`).
    """

    from_edge: str
    to_edge: str
    lanes_before: int
    lanes_after: int
    side: LaneSide
    how: str
    ending_lanes: tuple[int, ...]
    starting_lanes: tuple[int, ...]
    osm_lanes_before: int | None
    osm_lanes_after: int | None
    osm_agrees: bool | None
    source: ChangeSource

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_edge": self.from_edge,
            "to_edge": self.to_edge,
            "lanes_before": self.lanes_before,
            "lanes_after": self.lanes_after,
            "side": self.side,
            "how": self.how,
            "ending_lanes": list(self.ending_lanes),
            "starting_lanes": list(self.starting_lanes),
            "osm_lanes_before": self.osm_lanes_before,
            "osm_lanes_after": self.osm_lanes_after,
            "osm_agrees": self.osm_agrees,
            "source": self.source,
        }


@dataclass(frozen=True)
class Weave:
    """An auxiliary lane from an entrance to an exit (a one-sided ramp weave).

    Attributes:
        on_ramp: The entrance's name.
        off_ramp: The exit's name.
        on_edge: The entrance's joining edge.
        exit_edge: The exit's first edge.
        edges: Corridor edges the auxiliary lane runs along.
        length_m: Compiled length of the lane from the merge node to the exit [m].
        lane: The lane the entrance joins on the first edge.
        lane_continues: The lane also continues past the exit (an option lane).
    """

    on_ramp: str
    off_ramp: str
    on_edge: str
    exit_edge: str
    edges: tuple[str, ...]
    length_m: float
    lane: int
    lane_continues: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "on_ramp": self.on_ramp,
            "off_ramp": self.off_ramp,
            "on_edge": self.on_edge,
            "exit_edge": self.exit_edge,
            "edges": list(self.edges),
            "length_m": self.length_m,
            "lane": self.lane,
            "lane_continues": self.lane_continues,
        }


@dataclass(frozen=True)
class CDRoad:
    """A collector–distributor road: a split and a re-entry sharing a pair id.

    Attributes:
        pair: The pair id (``RampSpec.cd_pair``).
        split_ramp: The split's name.
        reentry_ramp: The re-entry's name, when the scenario has one.
        split_x_m: Corridor offset of the split [m].
        rejoin_x_m: Corridor offset of the re-entry [m].
        length_m: Corridor distance between them [m].
    """

    pair: str
    split_ramp: str
    reentry_ramp: str | None
    split_x_m: float
    rejoin_x_m: float | None
    length_m: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "split_ramp": self.split_ramp,
            "reentry_ramp": self.reentry_ramp,
            "split_x_m": self.split_x_m,
            "rejoin_x_m": self.rejoin_x_m,
            "length_m": self.length_m,
        }


@dataclass(frozen=True)
class SpeedChange:
    """The compiled speed limit changes between two corridor edges (check e).

    Attributes:
        from_edge: The edge before the change.
        to_edge: The edge after it.
        before_ms: Compiled limit before [m/s].
        after_ms: Compiled limit after [m/s].
        osm_before: Raw ``maxspeed`` of ``from_edge``'s way.
        osm_after: Raw ``maxspeed`` of ``to_edge``'s way.
    """

    from_edge: str
    to_edge: str
    before_ms: float
    after_ms: float
    osm_before: str | None
    osm_after: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_edge": self.from_edge,
            "to_edge": self.to_edge,
            "before_ms": self.before_ms,
            "after_ms": self.after_ms,
            "osm_before": self.osm_before,
            "osm_after": self.osm_after,
        }


EventDetail = RampJoin | RampSplit | LaneChange | Weave | CDRoad | SpeedChange


@dataclass(frozen=True)
class LayoutEvent:
    """One thing on the corridor a person checks on the imagery.

    Attributes:
        index: Position in the checklist (1-based, travel order).
        kind: :data:`EventKind`.
        x_m: Corridor offset [m].
        segment: Index of the segment it belongs to.
        edge: The corridor edge it happens on.
        description: What the model has here, in words.
        look_for: What to look for on the imagery.
        lat: Latitude [deg] (``None`` without a projection).
        lon: Longitude [deg].
        flags: Automatic findings.
        detail: The typed record.
    """

    index: int
    kind: EventKind
    x_m: float
    segment: int
    edge: str
    description: str
    look_for: str
    lat: float | None
    lon: float | None
    flags: tuple[Flag, ...]
    detail: EventDetail

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "x_m": self.x_m,
            "segment": self.segment,
            "edge": self.edge,
            "description": self.description,
            "look_for": self.look_for,
            "lat": self.lat,
            "lon": self.lon,
            "links": imagery_links(self.lat, self.lon),
            "flags": [f.as_dict() for f in self.flags],
            "detail": self.detail.as_dict(),
        }


# --- helpers ------------------------------------------------------------------


def imagery_links(lat: float | None, lon: float | None, zoom: int = IMAGERY_ZOOM) -> dict[str, str]:
    """Links that open aerial imagery (and Street View) at a point.

    ``satellite`` and ``street_view`` use Google's documented Maps URLs
    (``api=1``); ``satellite_alt`` opens Bing Maps' aerial view at the same
    point (a second, independently dated image).

    Args:
        lat: Latitude [deg].
        lon: Longitude [deg].
        zoom: Map zoom level.

    Returns:
        ``{"satellite", "satellite_alt", "street_view"}`` → URL; empty when
        the position is unknown.
    """
    if lat is None or lon is None:
        return {}
    at = f"{lat:.6f},{lon:.6f}"
    return {
        "satellite": (
            "https://www.google.com/maps/@?api=1&map_action=map"
            f"&center={at}&zoom={zoom}&basemap=satellite"
        ),
        "satellite_alt": f"https://www.bing.com/maps?cp={lat:.6f}~{lon:.6f}&lvl={zoom}&style=a",
        "street_view": f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={at}",
    }


def parse_maxspeed(raw: str | None) -> float | None:
    """An OSM ``maxspeed`` value in m/s, or ``None`` when it does not read.

    A bare number is km/h (the OSM convention); ``mph``, ``km/h`` (``kmh``,
    ``kph``) and ``knots`` are read; anything else is unreadable.
    """
    if raw is None:
        return None
    match = _MAXSPEED_RE.match(raw)
    if match is None:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "km/h").lower()
    if unit == "mph":
        return value * MS_PER_MPH
    if unit == "knots":
        return value * MS_PER_KNOT
    return kmh_to_ms(value)


def format_speed(v_ms: float | None) -> str:
    """``24.6 m/s (89 km/h, 55 mph)``."""
    if v_ms is None:
        return "-"
    return f"{v_ms:.1f} m/s ({ms_to_kmh(v_ms):.0f} km/h, {v_ms / MS_PER_MPH:.0f} mph)"


def _is_piece(edge_id: str) -> bool:
    return edge_id.endswith((RAMP_SPLIT_ON, RAMP_SPLIT_OFF))


def _directional_tag(way: OSMWay | None, key: str, edge_id: str) -> str | None:
    """A tag for the edge's direction: ``key:backward`` on a reversed way, else
    ``key:forward``, else ``key`` itself (motorways are one-way)."""
    if way is None:
        return None
    base = load_time_edge_id(edge_id).split("#", 1)[0]
    directional = f"{key}:backward" if base.startswith("-") else f"{key}:forward"
    return way.tags.get(directional, way.tags.get(key))


def _osm_lanes(way: OSMWay | None, edge_id: str) -> tuple[int | None, str | None]:
    raw = _directional_tag(way, "lanes", edge_id)
    if raw is None:
        return None, None
    try:
        return int(raw), raw
    except ValueError:
        return None, raw


_SIDE_WORDS: Final[dict[str, LaneSide]] = {
    "rightmost": "right",
    "leftmost": "left",
    "middle": "middle",
    "all": "all",
}


def _side_word(side: str) -> LaneSide:
    """``rightmost`` → ``right`` and so on (the compiled side in plain words)."""
    return _SIDE_WORDS.get(side, "unknown")


def _lanes_text(lanes: Iterable[int]) -> str:
    return ",".join(str(i) for i in lanes) or "-"


def _lonlat(net: Any, xy: tuple[float, float]) -> tuple[float | None, float | None]:
    """``(lat, lon)`` of a network point, rounded to 6 decimals (≈ 0.1 m)."""
    try:
        lon, lat = net_xy_to_lonlat(net, float(xy[0]), float(xy[1]))
    except ValueError:
        return None, None
    return round(lat, 6), round(lon, 6)


class _OSMIndex:
    """Ways of an :class:`OSMGraph` indexed by the nodes they end at / pass."""

    def __init__(self, graph: OSMGraph) -> None:
        self.graph = graph
        self.ending: dict[str, list[OSMWay]] = {}
        self.through: dict[str, list[OSMWay]] = {}
        for way in graph.ways.values():
            if not way.nodes:
                continue
            self.ending.setdefault(way.nodes[-1], []).append(way)
            for ref in way.nodes[1:]:
                self.through.setdefault(ref, []).append(way)


def _unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    d = math.dist(a, b)
    if d == 0.0:
        return None
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _arriving_polyline(
    index: _OSMIndex,
    node: str,
    preferred: Sequence[OSMWay | None],
    link_ids: set[str],
    highway: str,
    min_length_m: float = CONTINUING_MIN_M,
) -> list[tuple[float, float]]:
    """Mainline geometry arriving at ``node``, in local metres, ending at it.

    The mirror of :func:`microsim.split_audit._continuing_polyline` for a
    merge: taken from the first ``preferred`` way that passes through
    ``node`` after its first node (the upstream corridor edge's way, then the
    attach edge's own way), else from any non-link way of the same class
    reaching ``node``; then extended upstream along same-class ways (the one
    whose last segment lines up best) until ``min_length_m`` is collected.
    """
    graph = index.graph
    origin = graph.nodes.get(node)
    if origin is None:
        return []
    refs: list[str] = []
    for way in preferred:
        if way is None or way.id in link_ids or node not in way.nodes[1:]:
            continue
        refs = list(way.nodes[: way.nodes.index(node) + 1])
        break
    if len(refs) < 2:
        candidates = [
            w
            for w in index.through.get(node, [])
            if w.id not in link_ids and not w.is_link and (not highway or w.highway == highway)
        ]
        if candidates:
            way = sorted(candidates, key=lambda w: w.id)[0]
            refs = list(way.nodes[: way.nodes.index(node) + 1])
    if len(refs) < 2:
        return []
    points = [p for p in (_local_xy(graph, r, origin) for r in refs) if p is not None]
    seen = {node}
    while _polyline_length(points) < min_length_m and refs[0] not in seen and len(points) >= 2:
        seen.add(refs[0])
        heading = _unit(points[0], points[1])
        options = [
            w
            for w in index.ending.get(refs[0], [])
            if w.id not in link_ids
            and not w.is_link
            and (not highway or w.highway == highway)
            and len(w.nodes) >= 2
        ]
        if not options:
            break

        def _misalign(way: OSMWay, heading: tuple[float, float] | None = heading) -> float:
            a = _local_xy(graph, way.nodes[-2], origin)
            b = _local_xy(graph, way.nodes[-1], origin)
            if heading is None or a is None or b is None:
                return 0.0
            u = _unit(a, b)
            return math.inf if u is None else -(u[0] * heading[0] + u[1] * heading[1])

        chosen = min(options, key=lambda w: (_misalign(w), w.id))
        refs = list(chosen.nodes[:-1]) + refs
        points = [
            p for p in (_local_xy(graph, r, origin) for r in chosen.nodes[:-1]) if p is not None
        ] + points
    return points


def osm_entrance_side(
    graph: OSMGraph,
    link_way_id: str,
    mainline_way_ids: Sequence[str],
    *,
    merge_node: str | None = None,
    sample_m: float = LINK_SAMPLE_M,
    _index: _OSMIndex | None = None,
) -> tuple[Side, tuple[float, ...]]:
    """The side an entrance joins the mainline on, from OSM geometry alone.

    The merge counterpart of :func:`microsim.split_audit.osm_exit_side`: the
    link's nodes before the merge node, walked back up to ``sample_m`` along
    the link (through a single predecessor link way when the last one is
    short), are measured against the mainline **arriving** at the merge
    (signed lateral offset, + left / − right of travel).

    Args:
        graph: The parsed extract.
        link_way_id: OSM way of the link that reaches the mainline.
        mainline_way_ids: OSM ways of the mainline at the merge, preferred
            first (the upstream corridor edge's way, the attach edge's way).
        merge_node: The merge node, when known; otherwise the last node the
            link shares with a mainline way, else its last node.
        sample_m: Link length sampled [m].

    Returns:
        ``(side, offsets)``; ``("unknown", ())`` when the extract lacks the
        link, the node or an arriving mainline way.
    """
    index = _index or _OSMIndex(graph)
    link = graph.ways.get(link_way_id)
    if link is None or len(link.nodes) < 2:
        return "unknown", ()
    mainline = [graph.ways.get(w) for w in mainline_way_ids]
    node = merge_node if merge_node is not None and merge_node in link.nodes else None
    if node is None:
        shared = [n for n in link.nodes if any(w is not None and n in w.nodes for w in mainline)]
        node = shared[-1] if shared else link.nodes[-1]
    origin = graph.nodes.get(node)
    if origin is None:
        return "unknown", ()
    highway = next((w.highway for w in mainline if w is not None), "")
    link_ids = {link.id}
    # the link's nodes before the merge node, nearest first, through single predecessors
    refs = list(reversed(link.nodes[: link.nodes.index(node)]))
    first = link.nodes[0]
    walked_ways = {link.id}
    while True:
        preds = [w for w in index.ending.get(first, []) if w.is_link and w.id not in walked_ways]
        if len(preds) != 1:
            break
        pred = preds[0]
        walked_ways.add(pred.id)
        link_ids.add(pred.id)
        refs += list(reversed(pred.nodes[:-1]))
        first = pred.nodes[0]
        if len(refs) > 200:  # a pathological extract; enough nodes for any sample
            break
    arriving = _arriving_polyline(index, node, mainline, link_ids, highway)
    if len(arriving) < 2:
        return "unknown", ()
    offsets: list[float] = []
    walked = 0.0
    prev = _local_xy(graph, node, origin)
    for ref in refs:
        point = _local_xy(graph, ref, origin)
        if point is None:
            continue
        if prev is not None:
            walked += math.dist(prev, point)
        prev = point
        offset = _signed_offset(point, arriving)
        if not math.isnan(offset):
            offsets.append(round(offset, 1))
        if walked >= sample_m:
            break
    return side_of_offsets(offsets), tuple(offsets)


@dataclass(frozen=True)
class _LaneWalk:
    """Where an added lane goes (see :func:`_walk_lane`)."""

    lane: int
    edges: tuple[str, ...]
    length_m: float
    end: AuxEnd
    exit_edge: str | None = None
    exit_option: bool = False


def _walk_lane(
    net: Any, chain: Sequence[str], start: int, lane: int, max_m: float = AUX_LANE_MAX_M
) -> _LaneWalk:
    """Follow ``lane`` of ``chain[start]`` downstream until it ends.

    The generalisation of :func:`microsim.networks._lane0_walk` to any lane:
    the lane is followed connection by connection along the chain and ends at
    an exit (a connection leaving the corridor: a weaving section's auxiliary
    lane, ``exit_option`` when it also continues), in a dead end (``drop``),
    by merging into a lane another lane of the same edge also feeds
    (``merge``), past ``max_m`` (``through``: an added through lane), or at
    the corridor's end.
    """
    chain_set = set(chain)
    edges: list[str] = []
    length = 0.0
    i = start
    first_lane = lane
    while True:
        edge = net.getEdge(chain[i])
        edges.append(chain[i])
        length += float(edge.getLength())
        lanes = edge.getLanes()
        conns = list(lanes[lane].getOutgoing())
        nxt = chain[i + 1] if i + 1 < len(chain) else None
        to_next = [c for c in conns if c.getTo().getID() == nxt]
        to_exit = [c for c in conns if c.getTo().getID() not in chain_set]
        common = (first_lane, tuple(edges), round(length, 1))
        if to_exit and length <= max_m:
            return _LaneWalk(
                *common, end="exit", exit_edge=to_exit[0].getTo().getID(), exit_option=bool(to_next)
            )
        if nxt is None:
            return _LaneWalk(*common, end="corridor_end")
        if not to_next:
            return _LaneWalk(*common, end="drop")
        targets = {c.getToLane().getIndex() for c in to_next}
        others = {
            c.getToLane().getIndex()
            for other in lanes
            if other.getIndex() != lane
            for c in other.getOutgoing()
            if c.getTo().getID() == nxt
        }
        if targets <= others:
            return _LaneWalk(*common, end="merge")
        if length >= max_m:
            return _LaneWalk(*common, end="through")
        lane = min(targets - others)
        i += 1


def _ramp_label(ramp: RampLike | None, edge_id: str) -> str:
    if ramp is not None and ramp.name:
        return str(ramp.name)
    return f"ramp {edge_id}" + ("" if ramp is not None else " (not in the scenario's ramp list)")


# --- the audit ----------------------------------------------------------------


@dataclass(frozen=True)
class LayoutAudit:
    """The corridor's layout, as compiled, for a check against imagery.

    Attributes:
        corridor: Scenario name.
        net_path: The compiled network audited.
        osm_path: The extract it was compiled from.
        corridor_edges: The scenario's corridor edges (load-time ids).
        chain: The compiled chain (ramp-guessing pieces expanded).
        length_m: Corridor length [m].
        ramp_guessing: The net was compiled with ``--ramps.guess``.
        netconvert_extra: The scenario's netconvert options.
        patch_files: Patches applied (the scenario's and the runner's).
        terminated_lanes: Attach edges whose lane 0 the runner's merge
            models terminated.
        segments: :class:`Segment` rows in travel order.
        events: :class:`LayoutEvent` rows in travel order.
        split_audit: The split audit's findings (one per exit).
        provenance: Filled in by the CLI (commit, ``code_dirty``, hashes).
    """

    corridor: str
    net_path: str
    osm_path: str
    corridor_edges: tuple[str, ...]
    chain: tuple[str, ...]
    length_m: float
    ramp_guessing: bool
    netconvert_extra: tuple[str, ...]
    patch_files: tuple[str, ...]
    terminated_lanes: tuple[str, ...]
    segments: tuple[Segment, ...]
    events: tuple[LayoutEvent, ...]
    split_audit: tuple[SplitFinding, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    # -- queries --

    def flags(self) -> list[tuple[str, float, Flag]]:
        """Every flag as ``(where, x_m, flag)`` in travel order."""
        out: list[tuple[str, float, Flag]] = []
        for s in self.segments:
            out += [(f"segment {s.index} ({s.edge})", s.x_start_m, f) for f in s.flags]
        for e in self.events:
            out += [(f"event {e.index} ({e.kind})", e.x_m, f) for f in e.flags]
        return sorted(out, key=lambda t: t[1])

    def flags_by(self, check: str) -> list[Flag]:
        """The flags of one check."""
        return [f for _, _, f in self.flags() if f.check == check]

    def defects(self) -> list[tuple[str, float, Flag]]:
        """The ``defect`` flags."""
        return [t for t in self.flags() if t[2].severity == "defect"]

    def events_of(self, kind: str) -> list[LayoutEvent]:
        """The events of one kind, in travel order."""
        return [e for e in self.events if e.kind == kind]

    def counts(self) -> dict[str, Any]:
        """Counts of segments, events by kind, flags by severity and by check."""
        flags = [f for _, _, f in self.flags()]
        return {
            "segments": len(self.segments),
            "events": len(self.events),
            "events_by_kind": dict(Counter(e.kind for e in self.events)),
            "flags_by_severity": {
                s: sum(f.severity == s for f in flags) for s in ("defect", "warning", "info")
            },
            "flags_by_check": dict(Counter(f.check for f in flags)),
        }

    # -- output --

    def to_dict(self) -> dict[str, Any]:
        """The JSON-ready record (schema :data:`SCHEMA`)."""
        return {
            "schema": SCHEMA,
            "corridor": self.corridor,
            "net_path": self.net_path,
            "osm_path": self.osm_path,
            "corridor_edges": list(self.corridor_edges),
            "chain": list(self.chain),
            "length_m": self.length_m,
            "ramp_guessing": self.ramp_guessing,
            "netconvert_extra": list(self.netconvert_extra),
            "patch_files": list(self.patch_files),
            "terminated_lanes": list(self.terminated_lanes),
            "counts": self.counts(),
            "checks": [c.as_dict() for c in CHECKS],
            "cannot_see": list(CANNOT_SEE),
            "segments": [s.as_dict() for s in self.segments],
            "events": [e.as_dict() for e in self.events],
            "provenance": dict(self.provenance),
        }

    def to_json(self, path: str | Path | None = None) -> str:
        """The record as JSON text; also written to ``path`` when given."""
        text = json.dumps(self.to_dict(), indent=2, allow_nan=False) + "\n"
        if path is not None:
            Path(path).write_text(text)
        return text

    def to_csv(self, path: str | Path | None = None) -> str:
        """Segments and events interleaved in travel order, one row each.

        Each segment row is followed by the events that belong to it (an exit
        and a lane change at the segment's end, an entrance at its start
        belongs to the segment it joins). Columns are :data:`CSV_COLUMNS`; the
        last three are empty for the person checking (docs/LAYOUT_CHECKLIST.md).
        """
        return _render_csv(self, path)

    def summary_lines(self) -> list[str]:
        """Plain-text lines for a log (the CLI prints them)."""
        return _summary_lines(self)

    def to_markdown(self) -> str:
        """The checklist a person works through (docs/LAYOUT_CHECKLIST.md)."""
        return _render_markdown(self)


#: Columns of :meth:`LayoutAudit.to_csv`; the last three are left empty for the
#: person checking.
CSV_COLUMNS: Final[tuple[str, ...]] = (
    "row",
    "row_type",
    "kind",
    "x_start_m",
    "x_end_m",
    "length_m",
    "edge",
    "osm_way",
    "lanes_model",
    "lanes_map",
    "speed_model",
    "speed_map",
    "description",
    "flags",
    "worst_severity",
    "look_for",
    "lat",
    "lon",
    "satellite_url",
    "satellite_alt_url",
    "street_view_url",
    "checked_by",
    "imagery_date",
    "result",
)


def _render_csv(audit: LayoutAudit, path: str | Path | None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    by_segment: dict[int, list[LayoutEvent]] = {}
    for e in audit.events:
        by_segment.setdefault(e.segment, []).append(e)
    row = 0
    for s in audit.segments:
        row += 1
        links = imagery_links(s.lat, s.lon)
        writer.writerow(
            {
                "row": row,
                "row_type": "segment",
                "kind": "segment",
                "x_start_m": f"{s.x_start_m:.1f}",
                "x_end_m": f"{s.x_end_m:.1f}",
                "length_m": f"{s.length_m:.1f}",
                "edge": s.edge,
                "osm_way": s.osm_way,
                "lanes_model": s.lanes,
                "lanes_map": "" if s.osm_lanes_tag is None else s.osm_lanes_tag,
                "speed_model": format_speed(s.speed_ms),
                "speed_map": s.osm_maxspeed or "",
                "description": _segment_text(s),
                "flags": ";".join(f.check for f in s.flags),
                "worst_severity": _worst(s.flags),
                "look_for": " | ".join(f.look_for for f in s.flags) or SEGMENT_LOOK_FOR,
                "lat": "" if s.lat is None else s.lat,
                "lon": "" if s.lon is None else s.lon,
                "satellite_url": links.get("satellite", ""),
                "satellite_alt_url": links.get("satellite_alt", ""),
                "street_view_url": links.get("street_view", ""),
            }
        )
        for e in by_segment.get(s.index, []):
            row += 1
            links = imagery_links(e.lat, e.lon)
            writer.writerow(
                {
                    "row": row,
                    "row_type": "event",
                    "kind": e.kind,
                    "x_start_m": f"{e.x_m:.1f}",
                    "edge": e.edge,
                    "description": f"[{e.index}] {e.description}",
                    "flags": ";".join(f.check for f in e.flags),
                    "worst_severity": _worst(e.flags),
                    "look_for": " | ".join([*(f.look_for for f in e.flags), e.look_for]),
                    "lat": "" if e.lat is None else e.lat,
                    "lon": "" if e.lon is None else e.lon,
                    "satellite_url": links.get("satellite", ""),
                    "satellite_alt_url": links.get("satellite_alt", ""),
                    "street_view_url": links.get("street_view", ""),
                }
            )
    text = buffer.getvalue()
    if path is not None:
        Path(path).write_text(text)
    return text


def _summary_lines(audit: LayoutAudit) -> list[str]:
    c = audit.counts()
    sev = c["flags_by_severity"]
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(c["events_by_kind"].items()))
    lines = [
        f"layout audit {audit.corridor}: {audit.length_m / 1000.0:.2f} km, "
        f"{c['segments']} segments, {c['events']} events ({kinds or 'none'})",
        f"  flags: {sev['defect']} defect, {sev['warning']} warning, {sev['info']} note",
    ]
    for where, x, f in audit.flags():
        if f.severity == "info":
            continue
        lines.append(
            f"    x={x / 1000.0:7.3f} km  {f.severity.upper():7s} {f.check:22s} {where}: "
            f"{f.message}"
        )
    return lines


#: What a segment row asks for when nothing about it was flagged.
SEGMENT_LOOK_FOR: Final[str] = (
    "Count the lanes along the whole segment and look for a speed-limit sign."
)

#: What each event kind asks for, whatever the automatic checks found.
LOOK_FOR: Final[dict[str, str]] = {
    "on_ramp": (
        "Where does the ramp meet the mainline, and on which side? Is there an acceleration "
        "lane beside the mainline, and where does it end (measure from the gore to the end of "
        "the taper)?"
    ),
    "off_ramp": (
        "Which side does the exit leave from, and from which lanes (an exit-only lane, an "
        "option lane)? Is there a deceleration lane before the gore, and how long is it?"
    ),
    "lane_drop": "Which lane ends, on which side, and where does its taper end?",
    "lane_gain": "Which lane begins, on which side, and is it fed by a ramp?",
    "weave": (
        "Is the auxiliary lane continuous from the entrance gore to the exit gore (a dashed "
        "line, no solid line across it)? Measure its length gore to gore."
    ),
    "cd_road": (
        "Is this a collector-distributor road (a separate roadway beside the mainline, leaving "
        "and rejoining it), and do the interchange ramps use it rather than the mainline?"
    ),
    "speed_change": (
        "Where is the speed-limit sign (Street View), and does the posted value match the model?"
    ),
}


def _worst(flags: Sequence[Flag]) -> str:
    for level in ("defect", "warning", "info"):
        if any(f.severity == level for f in flags):
            return level
    return ""


def _segment_text(s: Segment) -> str:
    text = (
        f"{s.edge} (OSM way {s.osm_way}): {s.lanes} lanes "
        f"(map {s.osm_lanes_tag if s.osm_lanes_tag is not None else 'untagged'}), "
        f"{format_speed(s.speed_ms)} (map {s.osm_maxspeed or 'untagged'})"
    )
    if s.guessed_piece:
        text += "; a piece ramp guessing split off"
    return text


# --- the build ----------------------------------------------------------------


def audit_layout(
    net_path: str | Path,
    osm_path: str | Path,
    corridor_edges: Sequence[str],
    *,
    ramps: Sequence[RampLike] | None = None,
    corridor: str = "",
    netconvert_extra: Sequence[str] = (),
    patch_files: Sequence[str | Path] = (),
    terminated_lanes: Sequence[str] = (),
) -> LayoutAudit:
    """Audit a compiled corridor against its OSM extract, segment by segment.

    Args:
        net_path: The compiled ``.net.xml`` (the runner's: its merge-model
            patches included, so the audit sees what is simulated).
        osm_path: The extract it was compiled from.
        corridor_edges: Corridor edge ids in driving order (load-time ids;
            ramp-guessing pieces are expanded here).
        ramps: The scenario's ramps (``RampSpec``) or the onboarding's
            (``RampCandidate``), for names and C-D pairs; ``None`` discovers
            them on the compiled net (:func:`microsim.geo.ramps_for_chain`).
            A ramp is matched to the compiled net by its edges (an on-ramp's
            last, an off-ramp's first), so its ``attach_edge`` may be the
            load-time id of a ramp-guessing piece; a join or split the list
            does not name is still audited, labelled as not in the list.
        corridor: Scenario name, for the report.
        netconvert_extra: The scenario's netconvert options (whether ramp
            guessing was on decides how an extra lane is read).
        patch_files: Patches the network was compiled with, for the record.
        terminated_lanes: Attach edges whose acceleration lane the runner's
            merge models terminated (``NetBundle.terminated_lanes``).

    Returns:
        The :class:`LayoutAudit`.

    Raises:
        ValueError: A corridor edge is not in the compiled network.
    """
    net = sumolib.net.readNet(str(net_path))
    graph = parse_osm(osm_path)
    index = _OSMIndex(graph)
    present = {e.getID() for e in net.getEdges(withInternal=False)}
    chain = expand_ramp_splits(list(corridor_edges), present)
    missing = [e for e in chain if e not in present]
    if missing:
        raise ValueError(f"corridor edges not in the compiled network: {missing}")
    chain_set = set(chain)
    guessing = any(a == "--ramps.guess" or a.startswith("--ramps.guess=") for a in netconvert_extra)
    terminated = set(terminated_lanes)
    ramp_list: list[RampLike] = (
        list(ramps)
        if ramps is not None
        else [r for r in ramps_for_chain(net, chain) if not r.attach_via_cd]
    )
    on_by_edge: dict[str, RampLike] = {}
    off_by_edge: dict[str, RampLike] = {}
    for r in ramp_list:
        if not r.edges:
            continue
        if r.kind == "on":
            on_by_edge.setdefault(str(r.edges[-1]), r)
        else:
            off_by_edge.setdefault(str(r.edges[0]), r)

    # cumulative offsets, rounded only for the record, so that a segment's end
    # equals the next segment's start to the decimetre
    raw: list[float] = [0.0]
    for eid in chain:
        raw.append(raw[-1] + float(net.getEdge(eid).getLength()))
    offsets = [round(v, 1) for v in raw]
    length = offsets[-1]
    seg_of = {eid: i for i, eid in enumerate(chain)}

    def way_of(eid: str) -> OSMWay | None:
        return graph.ways.get(osm_way_id(eid))

    def node_latlon(node: Any) -> tuple[float | None, float | None]:
        return _lonlat(net, node.getCoord())

    # -- segments --
    segments: list[Segment] = []
    for i, eid in enumerate(chain):
        edge = net.getEdge(eid)
        way = way_of(eid)
        n_lanes = int(edge.getLaneNumber())
        osm_lanes, lanes_tag = _osm_lanes(way, eid)
        speed = round(max(float(lane.getSpeed()) for lane in edge.getLanes()), 2)
        maxspeed = _directional_tag(way, "maxspeed", eid)
        maxspeed_ms = parse_maxspeed(maxspeed)
        lat, lon = node_latlon(edge.getFromNode())
        seg = Segment(
            index=i,
            edge=eid,
            osm_way=osm_way_id(eid),
            x_start_m=offsets[i],
            x_end_m=offsets[i + 1],
            length_m=round(float(edge.getLength()), 1),
            lanes=n_lanes,
            osm_lanes=osm_lanes,
            osm_lanes_tag=lanes_tag,
            speed_ms=speed,
            osm_maxspeed=maxspeed,
            osm_maxspeed_ms=None if maxspeed_ms is None else round(maxspeed_ms, 2),
            guessed_piece=_is_piece(eid),
            lat=lat,
            lon=lon,
        )
        segments.append(_with_segment_flags(seg, way, guessing))

    events: list[tuple[float, int, int, LayoutEvent]] = []

    def add(
        kind: EventKind,
        x_m: float,
        segment: int,
        edge_id: str,
        description: str,
        node: Any,
        flags: Sequence[Flag],
        detail: EventDetail,
    ) -> None:
        lat, lon = node_latlon(node)
        event = LayoutEvent(
            index=0,
            kind=kind,
            x_m=round(x_m, 1),
            segment=segment,
            edge=edge_id,
            description=description,
            look_for=LOOK_FOR[kind],
            lat=lat,
            lon=lon,
            flags=tuple(flags),
            detail=detail,
        )
        events.append((round(x_m, 1), _KIND_ORDER[kind], len(events), event))

    # -- lane and speed changes at every junction of the chain --
    for i in range(len(chain) - 1):
        a, b = net.getEdge(chain[i]), net.getEdge(chain[i + 1])
        for change in _lane_changes(net, a, b, chain_set, way_of, guessing, terminated):
            kind: EventKind = "lane_drop" if change[0] == "drop" else "lane_gain"
            lc = change[1]
            add(
                kind,
                segments[i].x_end_m,
                i,
                chain[i],
                _lane_change_text(kind, lc),
                a.getToNode(),
                _lane_change_flags(kind, lc),
                lc,
            )
        sa, sb = segments[i], segments[i + 1]
        if abs(sa.speed_ms - sb.speed_ms) > SPEED_TOLERANCE_MS:
            sc = SpeedChange(
                from_edge=sa.edge,
                to_edge=sb.edge,
                before_ms=sa.speed_ms,
                after_ms=sb.speed_ms,
                osm_before=sa.osm_maxspeed,
                osm_after=sb.osm_maxspeed,
            )
            add(
                "speed_change",
                sa.x_end_m,
                i,
                sa.edge,
                f"Speed limit {format_speed(sa.speed_ms)} -> {format_speed(sb.speed_ms)} "
                f"(map {sa.osm_maxspeed or 'untagged'} -> {sb.osm_maxspeed or 'untagged'})",
                a.getToNode(),
                [
                    Flag(
                        "speed_limit",
                        "info",
                        f"the compiled speed limit changes from {format_speed(sa.speed_ms)} to "
                        f"{format_speed(sb.speed_ms)} here",
                        "Find the speed-limit sign near this point (Street View) and note where "
                        "it stands and what it says.",
                    )
                ],
                sc,
            )

    # -- on-ramp joins --
    joins: list[RampJoin] = []
    for i, eid in enumerate(chain):
        edge = net.getEdge(eid)
        for src in edge.getIncoming():
            sid = src.getID()
            if sid in chain_set:
                continue
            ramp = on_by_edge.get(sid)
            join = _ramp_join(
                net, graph, index, chain, i, src, ramp, guessing, way_of, _ramp_label(ramp, sid)
            )
            joins.append(join)
            add(
                "on_ramp",
                offsets[i],
                i,
                eid,
                _join_text(join),
                edge.getFromNode(),
                _join_flags(join),
                join,
            )
            if join.aux_end == "exit" and join.aux_exit_edge is not None:
                off = off_by_edge.get(join.aux_exit_edge)
                weave = Weave(
                    on_ramp=join.ramp,
                    off_ramp=_ramp_label(off, join.aux_exit_edge),
                    on_edge=sid,
                    exit_edge=join.aux_exit_edge,
                    edges=join.aux_edges,
                    length_m=float(join.aux_length_m or 0.0),
                    lane=min(join.aux_lanes),
                    lane_continues=join.aux_exit_option,
                )
                add(
                    "weave",
                    offsets[i],
                    i,
                    eid,
                    f"Weaving section: {weave.on_ramp} -> {weave.off_ramp}, auxiliary lane "
                    f"{weave.length_m:.0f} m along {', '.join(weave.edges)}"
                    + ("; the lane continues past the exit" if weave.lane_continues else ""),
                    edge.getFromNode(),
                    [],
                    weave,
                )

    # -- off-ramp splits (the split audit) --
    findings = audit_splits(net_path, osm_path, corridor_edges)
    split_at: dict[str, tuple[float, int]] = {}
    for finding in findings:
        i = seg_of[finding.from_edge]
        edge = net.getEdge(finding.from_edge)
        ramp = off_by_edge.get(finding.exit_edge)
        guessed = finding.from_edge.endswith(RAMP_SPLIT_OFF)
        split = RampSplit(
            ramp=_ramp_label(ramp, finding.exit_edge),
            finding=finding,
            decel_lane_guessed=guessed,
            decel_length_m=round(float(edge.getLength()), 1) if guessed else None,
            cd_pair=str(getattr(ramp, "cd_pair", "") or "") if ramp is not None else "",
        )
        split_at[finding.exit_edge] = (segments[i].x_end_m, i)
        add(
            "off_ramp",
            segments[i].x_end_m,
            i,
            finding.from_edge,
            _split_text(split),
            edge.getToNode(),
            _split_flags(split),
            split,
        )

    # -- collector-distributor pairs --
    for pair, split_ramp, reentry in _cd_pairs(ramp_list):
        at = split_at.get(str(split_ramp.edges[0]))
        if at is None:
            continue
        sx, i = at
        rx: float | None = None
        if reentry is not None:
            rj = next((j for j in joins if j.ramp_edge == str(reentry.edges[-1])), None)
            rx = offsets[seg_of[rj.attach_edge]] if rj is not None else None
        cd = CDRoad(
            pair=pair,
            split_ramp=str(split_ramp.name or split_ramp.edges[0]),
            reentry_ramp=None if reentry is None else str(reentry.name or reentry.edges[-1]),
            split_x_m=sx,
            rejoin_x_m=rx,
            length_m=None if rx is None else round(rx - sx, 1),
        )
        add(
            "cd_road",
            sx,
            i,
            chain[i],
            f"Collector-distributor road {pair}: leaves at x={sx / 1000.0:.3f} km ({cd.split_ramp})"
            + (
                f", rejoins at x={rx / 1000.0:.3f} km ({cd.reentry_ramp}), "
                f"{(rx - sx):.0f} m downstream"
                if rx is not None
                else ", re-entry not in the scenario"
            ),
            net.getEdge(chain[i]).getToNode(),
            [],
            cd,
        )

    ordered = [e for *_, e in sorted(events, key=lambda t: t[:3])]
    numbered = tuple(replace(e, index=k + 1) for k, e in enumerate(ordered))
    return LayoutAudit(
        corridor=corridor,
        net_path=str(net_path),
        osm_path=str(osm_path),
        corridor_edges=tuple(corridor_edges),
        chain=tuple(chain),
        length_m=length,
        ramp_guessing=guessing,
        netconvert_extra=tuple(str(a) for a in netconvert_extra),
        patch_files=tuple(str(p) for p in patch_files),
        terminated_lanes=tuple(terminated_lanes),
        segments=tuple(segments),
        events=numbered,
        split_audit=tuple(findings),
    )


def _cd_pairs(
    ramps: Sequence[RampLike],
) -> list[tuple[str, RampLike, RampLike | None]]:
    """``(pair id, split, re-entry)`` for every C-D road in the ramp list."""
    out: list[tuple[str, RampLike, RampLike | None]] = []
    for r in ramps:
        if r.kind != "off" or not getattr(r, "cd_road", False) or not r.edges:
            continue
        pair = str(getattr(r, "cd_pair", "") or "")
        reentry = next(
            (
                o
                for o in ramps
                if o.kind == "on"
                and getattr(o, "cd_road", False)
                and str(getattr(o, "cd_pair", "")) == pair
                and o.edges
            ),
            None,
        )
        out.append((pair, r, reentry))
    return out


# --- segment checks (c, e, f) -----------------------------------------------------


def _with_segment_flags(seg: Segment, way: OSMWay | None, guessing: bool) -> Segment:
    flags: list[Flag] = []
    # (c) lane count against the OSM tag
    if way is None:
        flags.append(
            Flag(
                "lanes_vs_osm",
                "warning",
                f"no OSM way {seg.osm_way} in the extract for this edge",
                "Count the lanes on the imagery; the model's count could not be checked against "
                "the map.",
            )
        )
    elif seg.osm_lanes is None:
        flags.append(
            Flag(
                "lanes_vs_osm",
                "warning",
                (
                    f"OSM way {seg.osm_way} has no readable lanes tag"
                    + (f" ({seg.osm_lanes_tag!r})" if seg.osm_lanes_tag else "")
                    + f"; netconvert used its default for highway={way.highway}: "
                    f"{seg.lanes} lanes"
                ),
                f"Count the lanes on the imagery; if it is not {seg.lanes}, the map needs a lanes "
                "tag (an OSM correction).",
            )
        )
    elif seg.osm_lanes != seg.lanes:
        if seg.guessed_piece:
            flags.append(
                Flag(
                    "lanes_vs_osm",
                    "info",
                    f"compiled {seg.lanes} lanes, map {seg.osm_lanes}: the extra lane is the "
                    "acceleration/deceleration lane netconvert's ramp guessing added "
                    "(--ramps.guess)",
                    f"Is there a lane beside the mainline here ({seg.lanes} lanes in all), and "
                    f"over how much of these {seg.length_m:.0f} m?",
                )
            )
        else:
            hint = (
                "probably the acceleration lane ramp guessing added over the whole edge (an "
                "attach edge shorter than --ramps.ramp-length is widened, and the lane may "
                "spill onward)"
                if guessing and seg.lanes == seg.osm_lanes + 1
                else "the network and the map disagree (a patch, a directional lanes tag, or "
                "netconvert's own choice)"
            )
            flags.append(
                Flag(
                    "lanes_vs_osm",
                    "warning",
                    f"compiled {seg.lanes} lanes, map {seg.osm_lanes}: {hint}",
                    f"Count the lanes on the imagery: {seg.lanes} (model) or {seg.osm_lanes} (map)?",
                )
            )
    # (e) speed limit
    if seg.osm_maxspeed_ms is None:
        what = (
            f"OSM maxspeed {seg.osm_maxspeed!r} does not read as a speed"
            if seg.osm_maxspeed
            else f"OSM way {seg.osm_way} has no maxspeed tag"
        )
        flags.append(
            Flag(
                "speed_limit",
                "warning",
                f"{what}; the compiled limit is {format_speed(seg.speed_ms)}"
                + (" (netconvert's default for the road class)" if not seg.osm_maxspeed else ""),
                "Find the posted speed limit for this stretch (Street View, a sign upstream) and "
                "record it.",
            )
        )
    elif abs(seg.osm_maxspeed_ms - seg.speed_ms) > SPEED_TOLERANCE_MS:
        flags.append(
            Flag(
                "speed_limit",
                "warning",
                f"compiled {format_speed(seg.speed_ms)}, map maxspeed {seg.osm_maxspeed} "
                f"({format_speed(seg.osm_maxspeed_ms)})",
                "Which limit is posted here (Street View)?",
            )
        )
    # (f) short segment
    if seg.length_m < SHORT_SEGMENT_M:
        flags.append(
            Flag(
                "short_segment",
                "warning",
                f"segment only {seg.length_m:.1f} m long (below {SHORT_SEGMENT_M:g} m)",
                "Is anything really here (a gore, a lane line ending, a bridge joint), or is it "
                "a map artifact? If a gore or lane change was drawn on it, note where it really "
                "is.",
            )
        )
    return replace(seg, flags=tuple(flags))


# --- lane changes (d) -------------------------------------------------------------


def _lane_changes(
    net: Any,
    a: Any,
    b: Any,
    chain_set: set[str],
    way_of: Any,
    guessing: bool,
    terminated: set[str],
) -> list[tuple[str, LaneChange]]:
    """The lane drop and/or gain at the junction ``a`` → ``b`` (possibly none)."""
    n_a, n_b = int(a.getLaneNumber()), int(b.getLaneNumber())
    conns = list(a.getOutgoing().get(b, []))
    cont = Counter(c.getFromLane().getIndex() for c in conns)
    fed = Counter(c.getToLane().getIndex() for c in conns)
    ending = tuple(lane for lane in range(n_a) if lane not in cont)
    starting = tuple(lane for lane in range(n_b) if lane not in fed)
    dead = tuple(lane for lane in ending if not a.getLanes()[lane].getOutgoing())
    exit_only = tuple(lane for lane in ending if lane not in dead)
    ramp_fed: set[int] = set()
    for src in b.getIncoming():
        if src.getID() in chain_set:
            continue
        ramp_fed |= {c.getToLane().getIndex() for c in src.getOutgoing().get(b, [])}
    from_ramp = tuple(lane for lane in starting if lane in ramp_fed)
    unfed = tuple(lane for lane in starting if lane not in ramp_fed)
    merged_into = tuple(sorted(t for t, k in fed.items() if k >= 2))
    fanned = tuple(sorted(f for f, k in cont.items() if k >= 2))

    way_a, way_b = way_of(a.getID()), way_of(b.getID())
    osm_a, _ = _osm_lanes(way_a, a.getID())
    osm_b, _ = _osm_lanes(way_b, b.getID())
    delta = n_b - n_a
    osm_delta = None if osm_a is None or osm_b is None else osm_b - osm_a
    agrees = None if osm_delta is None else (osm_delta == delta and delta != 0)

    def source() -> ChangeSource:
        # the runner's termination patch first: it is what ends the lane here,
        # even when the next edge is a ramp-guessing piece
        if a.getID() in terminated:
            return "merge_model_patch"
        if _is_piece(a.getID()) or _is_piece(b.getID()):
            return "ramp_guessing"
        if osm_delta is None:
            return "unknown"
        if agrees:
            return "osm"
        if guessing and (
            (osm_a is not None and osm_a != n_a) or (osm_b is not None and osm_b != n_b)
        ):
            return "ramp_guessing_likely"
        return "unexplained"

    out: list[tuple[str, LaneChange]] = []
    common = {
        "from_edge": a.getID(),
        "to_edge": b.getID(),
        "lanes_before": n_a,
        "lanes_after": n_b,
        "ending_lanes": ending,
        "starting_lanes": starting,
        "osm_lanes_before": osm_a,
        "osm_lanes_after": osm_b,
        "osm_agrees": agrees,
    }
    if delta < 0 or dead:
        if ending:
            side = _side_word(compiled_side(ending, n_a))
        else:
            side = _side_word(compiled_side(merged_into, n_b)) if merged_into else "unknown"
        parts = []
        if exit_only:
            parts.append(f"lane(s) {_lanes_text(exit_only)} of {n_a} leave at the exit")
        if dead:
            parts.append(f"lane(s) {_lanes_text(dead)} of {n_a} end")
        if not ending and merged_into:
            parts.append(f"lanes merge into lane(s) {_lanes_text(merged_into)} of {n_b}")
        out.append(
            (
                "drop",
                LaneChange(
                    **common,
                    side=side,
                    how="; ".join(parts) or "lane count falls",
                    source=source(),
                ),
            )
        )
    if delta > 0 or unfed:
        if starting:
            side = _side_word(compiled_side(starting, n_b))
        else:
            side = _side_word(compiled_side(fanned, n_a)) if fanned else "unknown"
        parts = []
        if from_ramp:
            parts.append(f"lane(s) {_lanes_text(from_ramp)} of {n_b} begin, fed by an on-ramp")
        if unfed:
            parts.append(f"lane(s) {_lanes_text(unfed)} of {n_b} begin with no feed")
        if not starting and fanned:
            parts.append(f"lane(s) {_lanes_text(fanned)} of {n_a} split in two")
        out.append(
            (
                "gain",
                LaneChange(
                    **common,
                    side=side,
                    how="; ".join(parts) or "lane count rises",
                    source=source(),
                ),
            )
        )
    return out


def _lane_change_text(kind: str, lc: LaneChange) -> str:
    word = "Lane drop" if kind == "lane_drop" else "Lane gain"
    if lc.osm_lanes_before is None or lc.osm_lanes_after is None:
        osm = "map lanes untagged"
    else:
        osm = f"map {lc.osm_lanes_before} -> {lc.osm_lanes_after}" + (
            " (agrees)" if lc.osm_agrees else " (does not agree)"
        )
    return (
        f"{word} {lc.lanes_before} -> {lc.lanes_after} on the {lc.side} between {lc.from_edge} "
        f"and {lc.to_edge}: {lc.how}; {osm}; source: {lc.source.replace('_', ' ')}"
    )


def _lane_change_flags(kind: str, lc: LaneChange) -> list[Flag]:
    word = "drop" if kind == "lane_drop" else "gain"
    flags: list[Flag] = []
    osm = (
        "the map's lanes tags are missing on one side"
        if lc.osm_lanes_before is None or lc.osm_lanes_after is None
        else f"the map has {lc.osm_lanes_before} -> {lc.osm_lanes_after}"
    )
    look = (
        f"Does a lane really {'end' if word == 'drop' else 'begin'} here, on the {lc.side}? "
        f"Count the lanes before and after: model {lc.lanes_before} -> {lc.lanes_after}."
    )
    if lc.source == "unexplained":
        flags.append(
            Flag(
                "lane_change_not_in_osm",
                "warning",
                f"compiled lane {word} {lc.lanes_before} -> {lc.lanes_after}, but {osm}",
                look,
            )
        )
    elif lc.source == "ramp_guessing_likely":
        flags.append(
            Flag(
                "lane_change_not_in_osm",
                "info",
                f"compiled lane {word} {lc.lanes_before} -> {lc.lanes_after}, {osm}: probably "
                + (
                    "the end of a lane ramp guessing added"
                    if word == "drop"
                    else "a lane ramp guessing added"
                ),
                look,
            )
        )
    elif lc.source == "unknown":
        flags.append(
            Flag(
                "lane_change_not_in_osm",
                "info",
                f"compiled lane {word} {lc.lanes_before} -> {lc.lanes_after}; {osm}, so the map "
                "cannot confirm it",
                look,
            )
        )
    elif lc.source == "merge_model_patch":
        flags.append(
            Flag(
                "merge_model_lane_end",
                "info",
                f"lane {word} made by the runner's merge model (the guessed acceleration lane of "
                f"{lc.from_edge} is terminated at its end), not by the map",
                "Where does the real acceleration lane end? (The model ends it here by "
                "construction.)",
            )
        )
    if lc.side == "left":
        flags.append(
            Flag(
                "lane_change_left",
                "info",
                f"the lane {'ends' if word == 'drop' else 'begins'} on the LEFT ({lc.how})",
                "Which side does the lane really end or begin on? netconvert chooses the side; "
                "it chose wrongly at two Minnesota exits.",
            )
        )
    return flags


# --- on-ramp joins (a, b) -----------------------------------------------------------


def _ramp_join(
    net: Any,
    graph: OSMGraph,
    index: _OSMIndex,
    chain: Sequence[str],
    i: int,
    src: Any,
    ramp: RampLike | None,
    guessing: bool,
    way_of: Any,
    label: str,
) -> RampJoin:
    b = net.getEdge(chain[i])
    n_b = int(b.getLaneNumber())
    conns = list(src.getOutgoing().get(b, []))
    joined = tuple(sorted({c.getToLane().getIndex() for c in conns}))
    upstream = chain[i - 1] if i > 0 else None
    fed_by_up: set[int] | None = None
    if upstream is not None:
        up_edge = net.getEdge(upstream)
        fed_by_up = {c.getToLane().getIndex() for c in up_edge.getOutgoing().get(b, [])}
    aux = tuple(lane for lane in joined if fed_by_up is not None and lane not in fed_by_up)
    mainline_fed = None if fed_by_up is None else not aux
    walks = [_walk_lane(net, chain, i, lane) for lane in aux]
    longest = max(walks, key=lambda w: w.length_m) if walks else None

    way_b = way_of(chain[i])
    way_up = way_of(upstream) if upstream is not None else None
    osm_b, _ = _osm_lanes(way_b, chain[i])
    osm_up, _ = _osm_lanes(way_up, upstream) if upstream is not None else (None, None)
    guessed = bool(aux) and (
        _is_piece(chain[i]) or (guessing and osm_b is not None and n_b > osm_b)
    )

    mainline_ids = [osm_way_id(e) for e in (upstream, chain[i]) if e is not None]
    side, offsets = osm_entrance_side(
        graph,
        osm_way_id(src.getID()),
        mainline_ids,
        merge_node=str(b.getFromNode().getID()),
        _index=index,
    )
    where = compiled_side(joined, n_b)
    added_wrong = bool(aux) and osm_b is not None and n_b > osm_b
    verdict, remedy = _join_verdict(side, where, added_wrong, load_time_edge_id(chain[i]))
    return RampJoin(
        ramp=label,
        ramp_edge=str(src.getID()),
        attach_edge=chain[i],
        attach_lanes=n_b,
        joined_lanes=joined,
        compiled_side=where,
        osm_side=side,
        osm_offsets_m=offsets,
        verdict=verdict,
        remedy=remedy,
        upstream_edge=upstream,
        mainline_fed=mainline_fed,
        aux_lanes=aux,
        aux_length_m=None if longest is None else longest.length_m,
        aux_end=None if longest is None else longest.end,
        aux_exit_edge=None if longest is None else longest.exit_edge,
        aux_edges=() if longest is None else longest.edges,
        aux_guessed=guessed,
        osm_lanes_upstream=osm_up,
        osm_lanes_attach=osm_b,
        cd_pair=str(getattr(ramp, "cd_pair", "") or "") if ramp is not None else "",
        aux_exit_option=False if longest is None else longest.exit_option,
    )


def _join_verdict(
    expected: Side, side: CompiledSide, added_lane: bool, load_time_attach: str
) -> tuple[JoinVerdict, str]:
    """Verdict and remedy of an entrance (the mirror of the split audit's)."""
    if expected == "unknown":
        return (
            "unknown",
            "the extract does not place the ramp (no arriving mainline way); check by hand",
        )
    if side == "all" or side == expected + "most":
        return "ok", ""
    if added_lane:
        return (
            "added_lane_wrong_side",
            f"the lane the ramp joins was added by ramp guessing on the wrong side: add "
            f"`--ramps.unset {load_time_attach}` to netconvert_extra and draw the acceleration "
            "lane in the map if the imagery shows one, then re-audit",
        )
    return (
        "wrong_side",
        f"restate the merge with an OSMNetwork.patch_files connection patch joining the ramp to "
        f"the {expected}most lane of the attach edge, then re-audit",
    )


def _join_text(j: RampJoin) -> str:
    text = (
        f"On-ramp {j.ramp} ({j.ramp_edge}) joins {j.attach_edge} lane(s) "
        f"{_lanes_text(j.joined_lanes)} of {j.attach_lanes} ({_side_word(j.compiled_side)}; "
        f"map: {j.osm_side})"
    )
    if j.mainline_fed:
        text += "; no acceleration lane (the joined lane also carries the mainline)"
    elif j.aux_end is not None:
        ends = {
            "drop": "ends in a lane drop",
            "merge": "merges into the next lane",
            "exit": f"runs to the exit {j.aux_exit_edge} (weave)",
            "through": "continues as a through lane",
            "corridor_end": "runs to the corridor's end",
        }[j.aux_end]
        text += f"; added lane {j.aux_length_m:.0f} m, {ends}"
        if j.aux_guessed:
            text += " (guessed by netconvert)"
    if j.cd_pair:
        text += f"; re-entry of C-D road {j.cd_pair}"
    return text


def _join_flags(j: RampJoin) -> list[Flag]:
    flags: list[Flag] = []
    if j.osm_offsets_m:
        mags = [abs(o) for o in j.osm_offsets_m]
        drawn = f"{min(mags):.0f}-{max(mags):.0f} m to the {j.osm_side}"
    else:
        drawn = "no geometry"
    compiled = (
        f"lane(s) {_lanes_text(j.joined_lanes)} of {j.attach_lanes} ({_side_word(j.compiled_side)})"
    )
    if j.verdict in ("wrong_side", "added_lane_wrong_side"):
        flags.append(
            Flag(
                "on_ramp_side",
                "defect",
                f"the map draws the ramp on the {j.osm_side} (link nodes {drawn}); the compiled "
                f"net joins it to {compiled} [{j.verdict}]. Fix: {j.remedy}",
                f"Which side does the ramp join on? If the imagery shows the {j.osm_side}, the "
                "compiled net is wrong here.",
            )
        )
    elif j.verdict == "unknown":
        flags.append(
            Flag(
                "on_ramp_side",
                "warning",
                f"the extract does not place the ramp ({drawn}); compiled into {compiled}",
                "Which side does the ramp join on, and into which lane?",
            )
        )
    if j.mainline_fed:
        flags.append(
            Flag(
                "no_accel_lane",
                "warning",
                f"no acceleration lane in the compiled net: the ramp joins {compiled} of "
                f"{j.attach_edge}, which also carry the mainline from {j.upstream_edge} - check "
                "imagery; the Minnesota on-ramps starved for this reason "
                "(docs/ONBOARDING_MNDOT.md §6)",
                "Is there an acceleration lane? If so, measure it from the gore to the end of the "
                "taper; the model then needs it (ramp guessing with that --ramps.ramp-length, or "
                "the lane drawn in the map).",
            )
        )
    elif (
        j.aux_end in ("drop", "merge")
        and j.aux_length_m is not None
        and j.aux_length_m < SHORT_AUX_LANE_M
    ):
        flags.append(
            Flag(
                "no_accel_lane",
                "warning",
                f"the acceleration lane is only {j.aux_length_m:.0f} m long in the compiled net "
                f"(below {SHORT_AUX_LANE_M:g} m)",
                "How long is the acceleration lane on the imagery, gore to taper end?",
            )
        )
    if j.aux_guessed and j.aux_length_m is not None:
        flags.append(
            Flag(
                "guessed_aux_lane",
                "info",
                f"the lane the ramp joins ({j.aux_length_m:.0f} m in the compiled net) exists "
                "because of ramp guessing (--ramps.guess); the map draws "
                + (
                    f"{j.osm_lanes_upstream} -> {j.osm_lanes_attach} lanes here"
                    if j.osm_lanes_upstream is not None and j.osm_lanes_attach is not None
                    else "no lane change here"
                ),
                "Does an acceleration lane exist here, and how long is it (gore to taper end)?",
            )
        )
    return flags


# --- off-ramp splits (g) ----------------------------------------------------------


def _split_text(s: RampSplit) -> str:
    f = s.finding
    text = (
        f"Off-ramp {s.ramp} ({f.exit_edge}) leaves {f.from_edge} from lane(s) "
        f"{_lanes_text(f.exit_from_lanes)} of {f.compiled_lanes} "
        f"({_side_word(f.compiled_side)}; map: {f.expected_side})"
    )
    if f.option_lanes:
        text += f"; option lane(s) {_lanes_text(f.option_lanes)}"
    if f.turn_lanes:
        text += f"; turn:lanes {f.turn_lanes}"
    if s.decel_lane_guessed and s.decel_length_m is not None:
        text += f"; deceleration lane {s.decel_length_m:.0f} m (guessed by netconvert)"
    text += f"; split audit: {f.verdict}"
    if s.cd_pair:
        text += f"; split of C-D road {s.cd_pair}"
    return text


def _split_flags(s: RampSplit) -> list[Flag]:
    f = s.finding
    flags: list[Flag] = []
    if f.is_defect:
        flags.append(
            Flag(
                "split_side",
                "defect",
                f"the map draws the exit on the {f.expected_side}; the compiled net feeds it from "
                f"lane(s) {_lanes_text(f.exit_from_lanes)} of {f.compiled_lanes} "
                f"({_side_word(f.compiled_side)}) [{f.verdict}]. Fix: {f.remedy}",
                f"Which side does the exit leave from? If the imagery shows the "
                f"{f.expected_side}, the compiled net traps through traffic here.",
            )
        )
    elif f.verdict == "unknown":
        flags.append(
            Flag(
                "split_side",
                "warning",
                "the extract does not place the exit (no continuing mainline way found)",
                "Which side does the exit leave from, and from which lanes?",
            )
        )
    if s.decel_lane_guessed and s.decel_length_m is not None:
        flags.append(
            Flag(
                "guessed_aux_lane",
                "info",
                f"the {s.decel_length_m:.0f} m deceleration lane exists because of ramp guessing "
                "(--ramps.guess)",
                "Does a deceleration lane exist before the gore, and how long is it?",
            )
        )
    return flags


# --- markdown -----------------------------------------------------------------


def _cell(text: Any) -> str:
    """A markdown table cell: pipes escaped, one line."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _links_md(lat: float | None, lon: float | None) -> str:
    links = imagery_links(lat, lon)
    if not links:
        return "(no position)"
    return (
        f"[satellite]({links['satellite']}) · [alt]({links['satellite_alt']}) · "
        f"[street]({links['street_view']})"
    )


def _flags_md(flags: Sequence[Flag]) -> str:
    if not flags:
        return "-"
    return "<br>".join(f"**{f.severity}** `{f.check}`: {_cell(f.message)}" for f in flags)


def _render_markdown(audit: LayoutAudit) -> str:
    c = audit.counts()
    sev = c["flags_by_severity"]
    p = dict(audit.provenance)
    lines = [f"# Layout checklist: {audit.corridor or Path(audit.net_path).stem}", ""]
    if p:
        dirty = p.get("code_dirty")
        lines += [
            f"Built {p.get('created_at', '?')} by `{p.get('script', 'microsim.layout_audit')}` "
            f"from `{p.get('scenario', '?')}`"
            + (f" (config hash `{p['config_hash']}`)" if p.get("config_hash") else "")
            + f"; code `{str(p.get('code', '?'))[:12]}`"
            + (" with uncommitted changes in the audited code" if dirty else "")
            + ("" if dirty is not None else " (git state unknown)")
            + ".",
            "",
        ]
    lines += [
        f"- Network audited: `{audit.net_path}` (what the runner simulates)",
        f"- Map: `{audit.osm_path}`"
        + (f" (sha256 `{str(p['osm_sha256'])[:16]}`)" if p.get("osm_sha256") else ""),
        f"- netconvert options: `{' '.join(audit.netconvert_extra) or '(none)'}`; ramp guessing "
        f"{'on' if audit.ramp_guessing else 'off'}",
        f"- Patches: {', '.join(f'`{x}`' for x in audit.patch_files) or 'none'}"
        + (
            f"; merge models terminated the acceleration lane of {', '.join(audit.terminated_lanes)}"
            if audit.terminated_lanes
            else ""
        ),
        f"- Corridor: {audit.length_m / 1000.0:.2f} km, {c['segments']} segments, "
        f"{c['events']} events",
        f"- Automatic flags: **{sev['defect']} defect**, {sev['warning']} warning, "
        f"{sev['info']} note",
        "",
        "How to use this list: docs/LAYOUT_CHECKLIST.md. Open each satellite link, compare what "
        "the model has with what the imagery shows, and fill in the last three columns: who "
        "checked, the imagery date (from the imagery source, not today's date), and the result "
        "(`matches`, `differs: ...` with what the imagery shows, or `cannot tell: ...`). Nothing "
        "in the network is changed until a difference is confirmed (docs/FRISCO_PROTOCOL.md "
        "§7.3).",
        "",
        "## Automatic flags (defects and warnings)",
        "",
    ]
    serious = [t for t in audit.flags() if t[2].severity != "info"]
    if serious:
        lines += [
            "| x [km] | where | check | severity | finding | look for |",
            "|---|---|---|---|---|---|",
        ]
        for where, x, f in serious:
            lines.append(
                f"| {x / 1000.0:.3f} | {_cell(where)} | `{f.check}` | {f.severity} | "
                f"{_cell(f.message)} | {_cell(f.look_for)} |"
            )
    else:
        lines.append(
            "None. The checklist below still has to be worked through: the checks see "
            "only what the map and the compiled net say."
        )
    lines += [
        "",
        "## Events (one line per event, in travel order)",
        "",
        "| # | x [km] | event | what the model has | automatic checks | look for on the imagery "
        "| imagery | checked by | imagery date | result |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in audit.events:
        look = " ".join([*(f.look_for for f in e.flags), e.look_for])
        lines.append(
            f"| {e.index} | {e.x_m / 1000.0:.3f} | {e.kind.replace('_', ' ')} | "
            f"{_cell(e.description)} | {_flags_md(e.flags)} | {_cell(look)} | "
            f"{_links_md(e.lat, e.lon)} |  |  |  |"
        )
    lines += [
        "",
        "## Segments (lanes and speed limit along the corridor)",
        "",
        "| seg | x [km] | length [m] | edge (OSM way) | lanes model / map | speed model / map "
        "| automatic checks | imagery | checked by | imagery date | result |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in audit.segments:
        lanes_map = s.osm_lanes_tag if s.osm_lanes_tag is not None else "untagged"
        lines.append(
            f"| {s.index} | {s.x_start_m / 1000.0:.3f}-{s.x_end_m / 1000.0:.3f} | "
            f"{s.length_m:.1f} | {_cell(s.edge)} ({_cell(s.osm_way)}) | {s.lanes} / "
            f"{_cell(lanes_map)} | {_cell(format_speed(s.speed_ms))} / "
            f"{_cell(s.osm_maxspeed or 'untagged')} | {_flags_md(s.flags)} | "
            f"{_links_md(s.lat, s.lon)} |  |  |  |"
        )
    lines += ["", "## The automatic checks", ""]
    for rule in CHECKS:
        tag = f"({rule.letter}) " if rule.letter else "(note) "
        lines.append(
            f"- **{tag}`{rule.id}`: {rule.title}.** {rule.rule}"
            + (f" Constant: {rule.constant}." if rule.constant else "")
            + f" Why: {rule.reason}"
        )
    lines += ["", "## What this audit cannot see", ""]
    lines += [f"- {item}" for item in CANNOT_SEE]
    lines.append("")
    return "\n".join(lines)


def check_rule(check_id: str) -> CheckRule:
    """The :class:`CheckRule` with this id."""
    return _CHECK_BY_ID[check_id]
