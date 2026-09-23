"""Corridor geometry for OSM-imported networks (CLAUDE.md §3.2.4).

The ``osm_generic`` onboarding path needs three things a raw ``netconvert``
import does not give: which edges form the one-direction mainline of the
corridor, where its interchange ramps attach, and where a detector station
given as (lat, lon) sits along it. This module provides all three generically,
for any freeway corridor:

* :func:`utm_forward` / :func:`read_net_projection` / :func:`lonlat_to_net_xy`
  — WGS84 lon/lat → the compiled network's own metric frame. ``netconvert``
  projects OSM with UTM and shifts by ``netOffset``; the forward UTM transform
  is implemented here (Snyder 1987, USGS PP 1395, Transverse Mercator
  eqs. 8-9 … 8-15; millimetre accuracy) so no projection library is needed.
* :func:`mainline_chain` — discovers the motorway edge chain travelling in a
  requested compass bearing, following successors and predecessors while the
  heading stays near that bearing.
* :func:`ramps_for_chain` — finds the ``motorway_link`` chains entering and
  leaving that mainline, ready to become :class:`flowstate_core.config.RampSpec`.
* :func:`chain_polyline` / :func:`x_of_lonlat` / :func:`lanes_profile` — the
  chain's geometry and the linear-``x`` coordinate of a lon/lat point, with the
  perpendicular offset so a caller can reject points that belong to another
  road (docs/CONTRACTS.md §3: ``x`` is the position along the route).

This generalises the corridor-specific helpers of ``scripts/i24_geometry.py``
(which projects I-24 MOTION mile markers onto the westbound chain); the
formulas and the per-edge projection convention are the same, verified there
against OSM node ids surviving as junction ids (sub-metre agreement).

Bearings are **compass** degrees (0° = north, 90° = east, 180° = south,
270° = west), computed in the network's projected frame where +x is east and
+y is north. All distances are metres.

Edge ids discovered here are only stable as *corridor edges* when the network
was imported with ``geometry_remove=False`` (raw OSM way ids, ``#``-split at
junctions): ``--geometry.remove`` joins runs of ways into one edge that
carries only one member way's id, and pruning by that id at load time would
silently drop the rest of the chain (see :func:`microsim.networks.osm_import`).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

# --- WGS84 / UTM --------------------------------------------------------------

_A = 6378137.0
"""WGS84 semi-major axis [m]."""
_F = 1.0 / 298.257223563
"""WGS84 flattening."""
_E2 = _F * (2.0 - _F)
_EP2 = _E2 / (1.0 - _E2)
_K0 = 0.9996
"""UTM central-meridian scale factor."""
_FALSE_EASTING_M = 500000.0

#: Default SUMO edge type of a mainline freeway after an OSM import with the
#: shipped ``osmNetconvert.typ.xml`` typemap.
MOTORWAY_TYPES: tuple[str, ...] = ("highway.motorway",)

#: Default SUMO edge types of interchange ramps (OSM ``motorway_link`` ways).
LINK_TYPES: tuple[str, ...] = ("highway.motorway_link",)

#: Default cap on how many link edges one ramp chain may span.
MAX_LINK_EDGES: int = 4

#: Default heading tolerance when discovering a one-direction chain [deg].
MAX_HEADING_DEV_DEG: float = 60.0


def utm_forward(lon_deg: float, lat_deg: float, zone: int) -> tuple[float, float]:
    """WGS84 lon/lat → UTM easting/northing [m] (northern hemisphere).

    Snyder (1987) *Map Projections — A Working Manual*, USGS PP 1395,
    Transverse Mercator eqs. 8-9 … 8-15 (ellipsoidal series).

    Args:
        lon_deg: Longitude [deg, east positive].
        lat_deg: Latitude [deg, north positive].
        zone: UTM zone number (1–60).

    Returns:
        ``(easting, northing)`` [m], easting including the 500 km false
        easting, northing measured from the equator.
    """
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    n = _A / math.sqrt(1.0 - _E2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = _EP2 * math.cos(phi) ** 2
    a = (lam - lam0) * math.cos(phi)
    e4, e6 = _E2**2, _E2**3
    m = _A * (
        (1 - _E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * _E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )
    x = (
        _K0
        * n
        * (a + (1 - t + c) * a**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * _EP2) * a**5 / 120)
    )
    y = _K0 * (
        m
        + n
        * math.tan(phi)
        * (
            a**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * _EP2) * a**6 / 720
        )
    )
    return x + _FALSE_EASTING_M, y


@dataclass(frozen=True)
class NetProjection:
    """A compiled network's projection: UTM zone plus ``netOffset``.

    Attributes:
        zone: UTM zone number parsed from ``projParameter``.
        offset_x: ``netOffset`` easting shift [m] (added after projecting).
        offset_y: ``netOffset`` northing shift [m].
    """

    zone: int
    offset_x: float
    offset_y: float

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        """WGS84 lon/lat → network coordinates [m]."""
        x, y = utm_forward(lon, lat, self.zone)
        return x + self.offset_x, y + self.offset_y


def _projection_from_strings(proj_parameter: str, net_offset: str, where: str) -> NetProjection:
    """Build a :class:`NetProjection` from the raw ``<location>`` attributes."""
    match = re.search(r"\+zone=(\d+)", proj_parameter)
    if "+proj=utm" not in proj_parameter or match is None:
        raise ValueError(
            f"{where}: unsupported projection {proj_parameter!r} "
            "(only UTM networks are supported; import the corridor with netconvert's default "
            "OSM projection)"
        )
    ox, oy = (float(v) for v in net_offset.split(","))
    return NetProjection(zone=int(match.group(1)), offset_x=ox, offset_y=oy)


def read_net_projection(net_xml: str | Path) -> NetProjection:
    """Parse ``<location netOffset=… projParameter=…>`` from a ``.net.xml``.

    Args:
        net_xml: Path of a compiled SUMO network.

    Returns:
        The network's :class:`NetProjection`.

    Raises:
        ValueError: No ``<location>`` element, or a non-UTM projection.
    """
    location = ET.parse(str(net_xml)).getroot().find("location")
    if location is None:
        raise ValueError(f"{net_xml}: no <location> element (not a compiled SUMO network?)")
    return _projection_from_strings(
        location.get("projParameter", ""), location.get("netOffset", "0,0"), str(net_xml)
    )


def net_projection(net: Any) -> NetProjection:
    """The :class:`NetProjection` of an already parsed ``sumolib`` network.

    Args:
        net: A ``sumolib.net.Net`` (from ``sumolib.net.readNet``).

    Returns:
        The network's projection.

    Raises:
        ValueError: The network carries no UTM projection (e.g. a
            programmatically built corridor, whose ``projParameter`` is
            ``"!"``).
    """
    location: dict[str, str] = getattr(net, "_location", {}) or {}
    return _projection_from_strings(
        location.get("projParameter", ""), location.get("netOffset", "0,0"), "network"
    )


def lonlat_to_net_xy(net: Any, lon: float, lat: float) -> tuple[float, float]:
    """WGS84 lon/lat → the network's projected coordinates [m].

    The ``sumolib`` equivalent (``Net.convertLonLat2XY``) needs ``pyproj``,
    which is not a dependency here; this uses :func:`utm_forward` instead.

    Args:
        net: A ``sumolib.net.Net``.
        lon: Longitude [deg].
        lat: Latitude [deg].

    Returns:
        ``(x, y)`` in network coordinates [m].
    """
    return net_projection(net).to_xy(lon, lat)


# --- chain geometry -----------------------------------------------------------


@dataclass(frozen=True)
class PointOnChain:
    """Where a lon/lat point falls on a corridor chain.

    Attributes:
        x_m: Linear position along the chain from its start [m]
            (docs/CONTRACTS.md §3 ``x``).
        offset_m: Perpendicular distance from the chain centreline [m]. A
            station on the modelled carriageway is a few metres off; a point
            on the opposite carriageway or another road is tens to hundreds
            of metres off, so callers reject above a threshold (60 m is the
            convention used for the I-24 landmark layers).
        edge_id: Chain edge the point projects onto.
        lane_pos: Distance from that edge's start [m] (SUMO lane position).
    """

    x_m: float
    offset_m: float
    edge_id: str
    lane_pos: float


def _edge_shape(net: Any, edge_id: str) -> list[tuple[float, float]]:
    """2-D shape of an edge [m], as ``(x, y)`` pairs, junctions included.

    ``netconvert`` trims an edge's shape back from the junctions at both
    ends so the junction has area — at a wide interchange node the hole
    between two consecutive edge shapes is tens of metres, and a point
    inside it would be reported that far *off* the corridor. Taking
    ``includeJunctions=True`` closes every hole (the shape then runs from
    junction centre to junction centre), so the chain polyline is continuous
    and a perpendicular offset means what a caller thinks it means.
    """
    return [(float(p[0]), float(p[1])) for p in net.getEdge(edge_id).getShape(True)]


def chain_polyline(net: Any, edge_ids: Sequence[str]) -> list[tuple[float, float]]:
    """Concatenate a chain's edge shapes into one polyline.

    Duplicate points at the joins (the shared junction coordinate) are
    dropped, so the result is the corridor centreline in network coordinates.

    Args:
        net: A ``sumolib.net.Net``.
        edge_ids: Chain edge ids in driving order.

    Returns:
        ``(x, y)`` points [m] along the chain.

    Raises:
        ValueError: ``edge_ids`` is empty.
        KeyError: An edge id is not in the network.
    """
    if not edge_ids:
        raise ValueError("chain_polyline needs at least one edge")
    points: list[tuple[float, float]] = []
    for eid in edge_ids:
        shape = _edge_shape(net, eid)
        if points and shape and math.dist(points[-1], shape[0]) < 1e-6:
            shape = shape[1:]
        points.extend(shape)
    return points


def chain_offsets(net: Any, edge_ids: Sequence[str]) -> list[float]:
    """Cumulative linear-x offset [m] of each chain edge's start."""
    offsets = [0.0]
    for eid in edge_ids[:-1]:
        offsets.append(offsets[-1] + float(net.getEdge(eid).getLength()))
    return offsets


def chain_length_m(net: Any, edge_ids: Sequence[str]) -> float:
    """Total length of a chain [m] (sum of SUMO edge lengths)."""
    return float(sum(float(net.getEdge(eid).getLength()) for eid in edge_ids))


def lanes_profile(net: Any, edge_ids: Sequence[str]) -> list[tuple[float, float, int]]:
    """Lane count along the chain as ``(x_start_m, x_end_m, lanes)`` runs.

    Consecutive edges with the same lane count are merged, so a corridor of
    many short OSM ways collapses to the few places where the road actually
    widens or drops a lane.

    Args:
        net: A ``sumolib.net.Net``.
        edge_ids: Chain edge ids in driving order.

    Returns:
        Contiguous runs covering ``[0, chain length]``.
    """
    runs: list[tuple[float, float, int]] = []
    x = 0.0
    for eid in edge_ids:
        edge = net.getEdge(eid)
        length = float(edge.getLength())
        lanes = int(edge.getLaneNumber())
        if runs and runs[-1][2] == lanes:
            start, _, _ = runs[-1]
            runs[-1] = (start, x + length, lanes)
        else:
            runs.append((x, x + length, lanes))
        x += length
    return runs


def _project_on_polyline(
    shape: Sequence[tuple[float, float]], px: float, py: float
) -> tuple[float, float]:
    """``(distance along the polyline [m], perpendicular distance [m])``."""
    best = (0.0, math.inf)
    run = 0.0
    for (x0, y0), (x1, y1) in pairwise(shape):
        dx, dy = x1 - x0, y1 - y0
        seg = math.hypot(dx, dy)
        if seg == 0.0:
            continue
        u = ((px - x0) * dx + (py - y0) * dy) / (seg * seg)
        u = min(max(u, 0.0), 1.0)
        d = math.hypot(px - (x0 + u * dx), py - (y0 + u * dy))
        if d < best[1]:
            best = (run + u * seg, d)
        run += seg
    return best


def x_of_lonlat(net: Any, chain_edges: Sequence[str], lon: float, lat: float) -> PointOnChain:
    """Project a WGS84 point onto a corridor chain.

    The point is projected onto each chain edge's own polyline and the
    closest edge wins; the along-distance is rescaled by
    ``edge length / polyline length`` so ``x_m`` stays consistent with the
    SUMO edge lengths that define linear x (:meth:`microsim.networks.NetBundle.linear_x`).

    Args:
        net: A ``sumolib.net.Net``.
        chain_edges: Chain edge ids in driving order.
        lon: Longitude [deg].
        lat: Latitude [deg].

    Returns:
        The :class:`PointOnChain`. ``offset_m`` is the perpendicular
        distance — callers reject points that are too far from the corridor
        (they belong to another carriageway or another road).

    Raises:
        ValueError: Empty chain, or the network has no UTM projection.
    """
    if not chain_edges:
        raise ValueError("x_of_lonlat needs at least one chain edge")
    px, py = lonlat_to_net_xy(net, lon, lat)
    best: PointOnChain | None = None
    for eid, offset in zip(chain_edges, chain_offsets(net, chain_edges), strict=True):
        shape = _edge_shape(net, eid)
        along, perp = _project_on_polyline(shape, px, py)
        if best is not None and perp >= best.offset_m:
            continue
        poly_len = sum(math.dist(a, b) for a, b in pairwise(shape))
        length = float(net.getEdge(eid).getLength())
        scale = length / poly_len if poly_len > 0.0 else 1.0
        lane_pos = min(max(along * scale, 0.0), length)
        best = PointOnChain(x_m=offset + lane_pos, offset_m=perp, edge_id=eid, lane_pos=lane_pos)
    assert best is not None  # chain_edges is non-empty
    return best


# --- chain discovery ----------------------------------------------------------


def edge_bearing_deg(edge: Any) -> float:
    """Compass bearing [deg] of an edge's start→end chord (0° = north)."""
    shape = edge.getShape()
    (x0, y0), (x1, y1) = shape[0], shape[-1]
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


def _heading_at(edge: Any, *, end: bool) -> float:
    """Compass bearing [deg] of an edge's last (``end``) or first segment."""
    shape = edge.getShape()
    if len(shape) < 2:
        return edge_bearing_deg(edge)
    (x0, y0), (x1, y1) = (shape[-2], shape[-1]) if end else (shape[0], shape[1])
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


def heading_dev_deg(a_deg: float, b_deg: float) -> float:
    """Absolute angular difference between two bearings [deg, 0–180]."""
    return abs((a_deg - b_deg + 180.0) % 360.0 - 180.0)


def _candidate_edges(
    net: Any, bearing_deg: float, highway_types: Sequence[str], max_heading_dev_deg: float
) -> dict[str, Any]:
    """Edges of the requested types heading roughly in the requested bearing."""
    types = set(highway_types)
    observed: set[str] = set()
    candidates: dict[str, Any] = {}
    for edge in net.getEdges(withInternal=False):
        observed.add(str(edge.getType()))
        if edge.getType() not in types:
            continue
        if heading_dev_deg(edge_bearing_deg(edge), bearing_deg) <= max_heading_dev_deg:
            candidates[edge.getID()] = edge
    if not candidates:
        raise ValueError(
            f"no {sorted(types)} edge heads within {max_heading_dev_deg:g}° of bearing "
            f"{bearing_deg:g}°; edge types in this network: {sorted(observed)}"
        )
    return candidates


def _seed_edge(net: Any, candidates: dict[str, Any], start_near: tuple[float, float] | None) -> Any:
    """The edge the chain walk starts from."""
    if start_near is None:
        # The mainline is the widest, then the longest, of the candidates.
        return max(
            candidates.values(),
            key=lambda e: (int(e.getLaneNumber()), float(e.getLength()), e.getID()),
        )
    px, py = lonlat_to_net_xy(net, start_near[0], start_near[1])
    return min(
        candidates.values(),
        key=lambda e: (_project_on_polyline(_edge_shape(net, e.getID()), px, py)[1], e.getID()),
    )


def mainline_chain(
    net: Any,
    bearing_deg: float,
    *,
    start_near: tuple[float, float] | None = None,
    highway_types: Sequence[str] = MOTORWAY_TYPES,
    max_heading_dev_deg: float = MAX_HEADING_DEV_DEG,
) -> list[str]:
    """Discover the one-direction mainline chain of a corridor.

    Candidates are the edges of ``highway_types`` whose start→end chord
    heads within ``max_heading_dev_deg`` of ``bearing_deg`` — on a divided
    freeway that selects one carriageway, since the opposite one heads the
    other way. The walk starts at a seed edge (the candidate nearest
    ``start_near``, else the widest and longest one) and follows predecessors
    upstream and successors downstream while they are candidates, so it stops
    where the road leaves the extract's bounding box, changes class, or turns
    away.

    At a fork the successor with the most lanes wins, then the one whose
    heading continues the current edge most closely, then the longest — the
    usual shape of a freeway mainline against a diverging branch. A branch
    that is *wider* than the mainline it leaves (rare, but real at some
    system interchanges) would be followed instead; check the reported chain
    against the map before running a study on it.

    Args:
        net: A ``sumolib.net.Net``. Import it with ``geometry_remove=False``
            if the ids will become ``corridor_edges`` (module docstring).
        bearing_deg: Requested travel direction, compass degrees
            (270 = westbound, 90 = eastbound).
        start_near: ``(lon, lat)`` anchor; the candidate edge closest to it
            seeds the walk. ``None`` seeds at the widest/longest candidate.
        highway_types: SUMO edge types treated as mainline.
        max_heading_dev_deg: Heading tolerance against ``bearing_deg`` [deg].

    Returns:
        Edge ids in driving order (upstream → downstream).

    Raises:
        ValueError: No edge of the requested types heads that way (the
            message lists the edge types present).
    """
    candidates = _candidate_edges(net, bearing_deg, highway_types, max_heading_dev_deg)
    seed = _seed_edge(net, candidates, start_near)
    chain: list[Any] = [seed]
    seen = {seed.getID()}

    def step(current: Any, *, forward: bool) -> Any | None:
        neighbours = current.getOutgoing() if forward else current.getIncoming()
        options = [e for e in neighbours if e.getID() in candidates and e.getID() not in seen]
        if not options:
            return None
        anchor = _heading_at(current, end=forward)
        return min(
            options,
            key=lambda e: (
                -int(e.getLaneNumber()),
                heading_dev_deg(_heading_at(e, end=not forward), anchor),
                -float(e.getLength()),
                e.getID(),
            ),
        )

    while (prev := step(chain[0], forward=False)) is not None:
        chain.insert(0, prev)
        seen.add(prev.getID())
    while (nxt := step(chain[-1], forward=True)) is not None:
        chain.append(nxt)
        seen.add(nxt.getID())
    return [e.getID() for e in chain]


@dataclass(frozen=True)
class RampCandidate:
    """An interchange ramp discovered beside a corridor chain.

    Attributes:
        kind: ``"on"`` (merges into the chain) or ``"off"`` (diverges).
        edges: Ramp edge ids in driving order — for an on-ramp ending at the
            junction where it joins ``attach_edge``, for an off-ramp starting
            there (the :class:`flowstate_core.config.RampSpec` convention).
        attach_edge: Chain edge the ramp joins (on) or leaves from (off).
        x_m: Linear position of the junction along the chain [m]: the start
            of ``attach_edge`` for an on-ramp, its end for an off-ramp.
        name: The link edge's street name from the map, when it has one.
    """

    kind: Literal["on", "off"]
    edges: tuple[str, ...]
    attach_edge: str
    x_m: float
    name: str = ""


def _walk_links(
    start: Any, *, forward: bool, link_types: set[str], used: set[str], max_edges: int
) -> list[str]:
    """Follow a chain of link edges away from the mainline, bounded."""
    ids = [start.getID()]
    current = start
    while len(ids) < max_edges:
        neighbours = current.getOutgoing() if forward else current.getIncoming()
        options = [
            e
            for e in neighbours
            if e.getType() in link_types and e.getID() not in used and e.getID() not in ids
        ]
        if len(options) != 1:  # a fork or a dead end: stop, the ramp is what we have
            break
        current = options[0]
        if forward:
            ids.append(current.getID())
        else:
            ids.insert(0, current.getID())
    return ids


def ramps_for_chain(
    net: Any,
    chain: Sequence[str],
    *,
    link_types: Sequence[str] = LINK_TYPES,
    max_link_edges: int = MAX_LINK_EDGES,
) -> list[RampCandidate]:
    """Find the on- and off-ramps of a corridor chain.

    A ``motorway_link`` edge that ends on a chain edge is an on-ramp; one
    that starts on a chain edge is an off-ramp. The link chain is walked
    away from the mainline (backwards for an on-ramp, forwards for an
    off-ramp) while exactly one link continues it, bounded by
    ``max_link_edges`` — so a ramp that reaches the arterial it serves in two
    or three ways is captured whole, and a ramp fanning out into an
    interchange stops at the fork.

    Each link edge is claimed by at most one ramp, so a collector–distributor
    road that leaves the mainline and rejoins it downstream is reported as
    the off-ramp it starts as, not twice.

    Args:
        net: A ``sumolib.net.Net``.
        chain: Mainline edge ids in driving order.
        link_types: SUMO edge types treated as ramps.
        max_link_edges: Cap on the number of edges in one ramp chain.

    Returns:
        Candidates ordered by position along the chain, off-ramps before
        on-ramps at the same position. Their ``edges`` never include a chain
        edge, so they can be handed straight to
        :class:`flowstate_core.config.RampSpec`.
    """
    types = set(link_types)
    on_chain = set(chain)
    used: set[str] = set()
    found: list[RampCandidate] = []
    for eid, offset in zip(chain, chain_offsets(net, chain), strict=True):
        edge = net.getEdge(eid)
        end_x = offset + float(edge.getLength())
        for link in edge.getOutgoing():
            if link.getType() not in types or link.getID() in on_chain | used:
                continue
            ids = _walk_links(
                link, forward=True, link_types=types, used=used, max_edges=max_link_edges
            )
            used.update(ids)
            found.append(
                RampCandidate(
                    kind="off",
                    edges=tuple(ids),
                    attach_edge=eid,
                    x_m=end_x,
                    name=str(link.getName() or ""),
                )
            )
        for link in edge.getIncoming():
            if link.getType() not in types or link.getID() in on_chain | used:
                continue
            ids = _walk_links(
                link, forward=False, link_types=types, used=used, max_edges=max_link_edges
            )
            used.update(ids)
            found.append(
                RampCandidate(
                    kind="on",
                    edges=tuple(ids),
                    attach_edge=eid,
                    x_m=offset,
                    name=str(link.getName() or ""),
                )
            )
    found.sort(key=lambda r: (r.x_m, r.kind, r.attach_edge))
    return found
