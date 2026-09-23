"""Programmatic SUMO network builders (CLAUDE.md §3.2).

Each builder writes plain-XML node/edge inputs, runs ``netconvert`` (located
via :func:`sumolib.checkBinary`), and returns a :class:`NetBundle` describing
the generated network plus the linear-x coordinate mapping used everywhere in
the micro tier (docs/CONTRACTS.md §3: ``x`` is the position along the route;
ring = arc length).

Builders:

* :func:`ring` — regular polygon of one-lane edges with **explicit** ``length``
  attributes so arc lengths sum exactly to the circumference regardless of
  chord geometry (the Sugiyama et al. 2008 ring benchmark geometry,
  CLAUDE.md §3.2.1).
* :func:`corridor` — straight chain of edges (default 1 km segments) with a
  short upstream entry edge for vehicle insertion (CLAUDE.md §3.2.2).
* :func:`osm_import` — the ``osm_generic`` "any city" onboarding pipeline
  (CLAUDE.md §3.2.4): OSM extract (file or bbox download) → ``netconvert``
  with the highway typemap → optional corridor pruning to named edges.
* :func:`patch_net` — a second ``netconvert`` pass over an already compiled
  network, for patches that name edges netconvert generated itself (the
  ``-AddedOnRampEdge`` pieces of ``--ramps.guess``; see
  :func:`lane_end_patch_file`).

All geometry is SI (meters). ``netconvert`` always receives
``--no-internal-links`` so vehicles never occupy internal junction lanes and
edge lengths partition the drivable length exactly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import re
import subprocess
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import sumolib

from controllers.vsl import VSL_SEGMENT_TARGET_M, gantry_segments
from microsim.paths import effective_roots, ensure_within_roots

#: Free-flow speed limit written on generated edges [m/s]. Deliberately above
#: any plausible per-vehicle desired-speed draw (v0 ≤ 38 + 3σ, CLAUDE.md §3.1)
#: so the vType ``maxSpeed`` — not the road — governs desired speed.
EDGE_SPEED_LIMIT_MS: float = 50.0

#: Default upstream entry-edge length for corridors [m].
ENTRY_EDGE_LENGTH_M: float = 100.0

#: Default main-segment length for corridors [m] (CLAUDE.md task spec: 1 km).
CORRIDOR_SEGMENT_M: float = 1000.0


@dataclass(frozen=True)
class NetBundle:
    """A generated SUMO network plus its linear-x coordinate mapping.

    Attributes:
        net_path: Path of the compiled ``.net.xml``.
        edge_ids: Edge ids in route order (for a ring: around the loop; for a
            corridor: entry edge first, then upstream → downstream).
        edge_lengths: Length of each edge [m], same order as ``edge_ids``.
        offsets: Cumulative linear-x offset of each edge's start [m], same
            order (``offsets[0] == 0``).
        total_length_m: Sum of all edge lengths [m]. For a ring this equals
            the circumference.
        workdir: Directory holding the netconvert inputs and output.
        kind: Which builder produced the bundle.
        entry_edge: Id of the upstream insertion edge (corridor only).
        exit_edge: Id of the downstream exit-buffer edge (corridor only;
            present when the corridor was built with ``exit_m > 0`` to host
            a measured downstream boundary condition outside the corridor
            proper — see :class:`flowstate_core.config.BoundarySpec`).
    """

    net_path: Path
    edge_ids: tuple[str, ...]
    edge_lengths: tuple[float, ...]
    offsets: tuple[float, ...]
    total_length_m: float
    workdir: Path
    kind: Literal["ring", "corridor", "osm"]
    entry_edge: str | None = None
    exit_edge: str | None = None
    patch_files: tuple[str, ...] = ()
    """netconvert patch files applied on import (on-ramp merge models); empty otherwise."""
    terminated_lanes: tuple[str, ...] = ()
    """Attach edges whose guessed acceleration lane (lane 0) was terminated at
    the edge's end by a connection patch (:func:`lane_end_patch_file`); empty
    otherwise."""

    def linear_x(self, edge_id: str, lane_pos: float) -> float:
        """Map (edge id, position-on-edge [m]) → linear x [m].

        Args:
            edge_id: One of ``edge_ids``.
            lane_pos: Distance from the edge start [m].

        Returns:
            Position along the route from the route start [m].

        Raises:
            KeyError: Unknown edge id.
        """
        return self._offset_by_id[edge_id] + lane_pos

    def locate(self, x: float) -> tuple[str, float]:
        """Map linear x [m] → (edge id, position-on-edge [m]).

        Ring bundles wrap ``x`` modulo the circumference; corridor bundles
        clamp into ``[0, total_length_m]``.
        """
        if self.kind == "ring":
            x = x % self.total_length_m
        else:
            x = min(max(x, 0.0), self.total_length_m)
        i = min(bisect_right(self.offsets, x) - 1, len(self.edge_ids) - 1)
        i = max(i, 0)
        return self.edge_ids[i], x - self.offsets[i]

    @property
    def _offset_by_id(self) -> dict[str, float]:
        return dict(zip(self.edge_ids, self.offsets, strict=True))

    @property
    def main_edges(self) -> tuple[str, ...]:
        """Edge ids excluding entry/exit buffers (the analysis corridor proper)."""
        buffers = {self.entry_edge, self.exit_edge} - {None}
        if not buffers:
            return self.edge_ids
        return tuple(e for e in self.edge_ids if e not in buffers)

    def segments(self, target_m: float = VSL_SEGMENT_TARGET_M) -> list[tuple[str, ...]]:
        """Group the main edges into VSL gantry segments (CLAUDE.md §4.4).

        Consecutive main edges are grouped greedily by cumulative length via
        :func:`controllers.vsl.gantry_segments`: segments aim at ``target_m``,
        are at least half of it long (500 m at the default), never split an
        edge, and a trailing short remainder joins the previous segment. A
        generated corridor of 1 km edges therefore yields one segment per
        edge, a corridor consisting of a single long edge stays one segment,
        and an OSM chain of short ways is merged into gantry-sized groups.

        Args:
            target_m: Target segment length [m].

        Returns:
            Edge-id groups in route order, covering every main edge once.
        """
        edges = self.main_edges
        length_by_id = dict(zip(self.edge_ids, self.edge_lengths, strict=True))
        bounds = gantry_segments([length_by_id[e] for e in edges], target_m)
        return [tuple(edges[a:b]) for a, b in bounds]


def _netconvert(args: list[str]) -> None:
    """Run ``netconvert`` (via ``sumolib.checkBinary``), raising on failure."""
    binary = sumolib.checkBinary("netconvert")
    env = dict(os.environ)
    env.setdefault("SUMO_HOME", str(Path(binary).parent.parent))
    proc = subprocess.run([binary, *args], capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"netconvert failed (exit {proc.returncode}):\n{proc.stderr.strip()}")


def _write_plain_and_convert(workdir: Path, stem: str, nodes_xml: str, edges_xml: str) -> Path:
    """Write ``.nod.xml``/``.edg.xml`` and compile them into a ``.net.xml``."""
    workdir.mkdir(parents=True, exist_ok=True)
    nod = workdir / f"{stem}.nod.xml"
    edg = workdir / f"{stem}.edg.xml"
    net = workdir / f"{stem}.net.xml"
    nod.write_text(nodes_xml)
    edg.write_text(edges_xml)
    _netconvert(
        [
            "--node-files",
            str(nod),
            "--edge-files",
            str(edg),
            "-o",
            str(net),
            "--no-internal-links",
            "--no-turnarounds",
        ]
    )
    return net


def ring(circumference_m: float, n_segments: int = 8, workdir: Path | None = None) -> NetBundle:
    """Build a single-lane closed ring as a regular polygon of edges.

    Node coordinates lie on a circle of radius ``C/2π`` (visual only); each
    edge carries an **explicit** ``length = C/n_segments`` attribute, so the
    drivable arc lengths sum exactly to ``circumference_m`` even though the
    polygon chords are shorter (Sugiyama et al. 2008 benchmark geometry,
    CLAUDE.md §3.2.1). ``--no-internal-links`` keeps junctions length-free.

    Args:
        circumference_m: Ring circumference [m].
        n_segments: Number of polygon edges (≥ 8 for reasonable geometry).
        workdir: Directory for netconvert inputs/output. Required.

    Returns:
        The compiled :class:`NetBundle` (``kind="ring"``).

    Raises:
        ValueError: Bad circumference/segment count, or missing workdir.
        RuntimeError: netconvert failure.
    """
    if circumference_m <= 0:
        raise ValueError(f"circumference_m must be > 0, got {circumference_m}")
    if n_segments < 8:
        raise ValueError(f"n_segments must be >= 8, got {n_segments}")
    if workdir is None:
        raise ValueError("ring() requires an explicit workdir")

    seg_len = circumference_m / n_segments
    radius = circumference_m / (2.0 * math.pi)
    nodes = ["<nodes>"]
    for i in range(n_segments):
        ang = 2.0 * math.pi * i / n_segments
        nodes.append(
            f'  <node id="rn{i}" x="{radius * math.cos(ang):.4f}" '
            f'y="{radius * math.sin(ang):.4f}"/>'
        )
    nodes.append("</nodes>")
    edges = ["<edges>"]
    for i in range(n_segments):
        edges.append(
            f'  <edge id="re{i}" from="rn{i}" to="rn{(i + 1) % n_segments}" '
            f'numLanes="1" speed="{EDGE_SPEED_LIMIT_MS}" length="{seg_len:.8f}"/>'
        )
    edges.append("</edges>")

    net = _write_plain_and_convert(workdir, "ring", "\n".join(nodes), "\n".join(edges))
    edge_ids = tuple(f"re{i}" for i in range(n_segments))
    lengths = tuple(seg_len for _ in range(n_segments))
    offsets = tuple(i * seg_len for i in range(n_segments))
    return NetBundle(
        net_path=net,
        edge_ids=edge_ids,
        edge_lengths=lengths,
        offsets=offsets,
        total_length_m=circumference_m,
        workdir=workdir,
        kind="ring",
    )


def corridor(
    length_m: float,
    lanes: int = 1,
    workdir: Path | None = None,
    segment_m: float = CORRIDOR_SEGMENT_M,
    entry_m: float = ENTRY_EDGE_LENGTH_M,
    exit_m: float = 0.0,
) -> NetBundle:
    """Build a straight corridor: entry edge + chain of main segments.

    The linear-x origin is the start of the **entry** edge; the corridor
    proper begins at ``x = entry_m``. Main segments are ``segment_m`` long
    (default 1 km) with a shorter final remainder segment when needed. With
    ``exit_m > 0`` an exit-buffer edge is appended after the last main
    segment: it hosts a measured downstream boundary condition (a speed
    schedule applied via ``edge.setMaxSpeed``) OUTSIDE the corridor proper,
    per standard FHWA microsimulation calibration practice of imposing
    field-measured conditions at the model boundaries (FHWA Traffic
    Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019).

    Args:
        length_m: Main corridor length [m] (excluding entry/exit buffers).
        lanes: Lane count (1–8 per the config schema).
        workdir: Directory for netconvert inputs/output. Required.
        segment_m: Main segment length [m].
        entry_m: Upstream insertion-edge length [m].
        exit_m: Downstream exit-buffer edge length [m]; 0 disables it.

    Returns:
        The compiled :class:`NetBundle` (``kind="corridor"``,
        ``entry_edge="entry"``, ``exit_edge="exit"`` when ``exit_m > 0``).

    Raises:
        ValueError: Bad dimensions or missing workdir.
        RuntimeError: netconvert failure.
    """
    if length_m <= 0:
        raise ValueError(f"length_m must be > 0, got {length_m}")
    if lanes < 1:
        raise ValueError(f"lanes must be >= 1, got {lanes}")
    if exit_m < 0:
        raise ValueError(f"exit_m must be >= 0, got {exit_m}")
    if workdir is None:
        raise ValueError("corridor() requires an explicit workdir")

    n_full = int(length_m // segment_m)
    remainder = length_m - n_full * segment_m
    seg_lengths = [segment_m] * n_full + ([remainder] if remainder > 1e-9 else [])
    if not seg_lengths:  # length_m < segment_m
        seg_lengths = [length_m]

    xs = [-entry_m, 0.0]
    for sl in seg_lengths:
        xs.append(xs[-1] + sl)
    if exit_m > 0:
        xs.append(xs[-1] + exit_m)
    nodes = ["<nodes>"]
    for i, x in enumerate(xs):
        nodes.append(f'  <node id="cn{i}" x="{x:.4f}" y="0.0"/>')
    nodes.append("</nodes>")

    edge_ids = ["entry"] + [f"ce{i}" for i in range(len(seg_lengths))]
    lengths = [entry_m, *seg_lengths]
    if exit_m > 0:
        edge_ids.append("exit")
        lengths.append(exit_m)
    edges = ["<edges>"]
    for i, (eid, elen) in enumerate(zip(edge_ids, lengths, strict=True)):
        edges.append(
            f'  <edge id="{eid}" from="cn{i}" to="cn{i + 1}" numLanes="{lanes}" '
            f'speed="{EDGE_SPEED_LIMIT_MS}" length="{elen:.8f}"/>'
        )
    edges.append("</edges>")

    net = _write_plain_and_convert(workdir, "corridor", "\n".join(nodes), "\n".join(edges))
    offsets: list[float] = [0.0]
    for elen in lengths[:-1]:
        offsets.append(offsets[-1] + elen)
    return NetBundle(
        net_path=net,
        edge_ids=tuple(edge_ids),
        edge_lengths=tuple(lengths),
        offsets=tuple(offsets),
        total_length_m=float(sum(lengths)),
        workdir=workdir,
        kind="corridor",
        entry_edge="entry",
        exit_edge="exit" if exit_m > 0 else None,
    )


RAMP_SPLIT_ON: Final[str] = "-AddedOnRampEdge"
RAMP_SPLIT_OFF: Final[str] = "-AddedOffRampEdge"


def expand_ramp_splits(edge_ids: Sequence[str], present: Iterable[str]) -> list[str]:
    """Expand corridor edge ids into the pieces netconvert's ramp guessing made.

    ``--ramps.guess`` (without ``--ramps.no-split``) splits the highway edge
    after an on-ramp into ``<id>-AddedOnRampEdge`` (the acceleration-lane
    piece, first) and ``<id>`` (the rest), and the edge before an off-ramp
    into ``<id>`` and ``<id>-AddedOffRampEdge`` (the deceleration-lane piece,
    last). Scenario files keep the load-time ids (pruning happens before the
    split); the compiled chain is the expanded one.

    Args:
        edge_ids: Driving-order corridor ids as named in the scenario.
        present: Edge ids of the compiled net.

    Returns:
        The driving-order chain with every split piece in place.
    """
    have = set(present)
    out: list[str] = []
    for edge_id in edge_ids:
        if edge_id.endswith((RAMP_SPLIT_ON, RAMP_SPLIT_OFF)):
            pieces = [edge_id]  # already a piece: never re-expanded
        else:
            pieces = []
            if edge_id + RAMP_SPLIT_ON in have:
                pieces.append(edge_id + RAMP_SPLIT_ON)
            if edge_id in have or not (
                edge_id + RAMP_SPLIT_ON in have or edge_id + RAMP_SPLIT_OFF in have
            ):
                pieces.append(edge_id)
            if edge_id + RAMP_SPLIT_OFF in have:
                pieces.append(edge_id + RAMP_SPLIT_OFF)
        for piece in pieces:
            if piece not in out:
                out.append(piece)
    return out


def _osm_typemap() -> Path | None:
    """Locate SUMO's OSM highway typemap next to the pip-installed binaries."""
    binary = Path(sumolib.checkBinary("netconvert"))
    candidate = binary.parent.parent / "data" / "typemap" / "osmNetconvert.typ.xml"
    return candidate if candidate.is_file() else None


def _download_bbox(bbox: tuple[float, float, float, float], dest: Path) -> Path:
    """Download an OSM extract for ``(south, west, north, east)`` to ``dest``.

    Network access is required; tests use file fixtures instead
    (CLAUDE.md §3.2.4 — the bbox path is the interactive onboarding flow).
    """
    south, west, north, east = bbox
    # The OSM API expects left,bottom,right,top = west,south,east,north.
    url = f"https://api.openstreetmap.org/api/0.6/map?bbox={west},{south},{east},{north}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        dest.write_bytes(resp.read())
    return dest


#: Overpass API endpoint used by :func:`_download_bbox_overpass`.
OVERPASS_ENDPOINT: str = "https://overpass-api.de/api/interpreter"

#: Default OSM ``highway`` values fetched for a freeway corridor: the mainline
#: and its interchange ramps, and nothing else — the extract stays small
#: (well under a megabyte for a 15 km corridor) and ``netconvert`` has no
#: arterial network to prune away.
OVERPASS_HIGHWAY_REGEX: str = "motorway|motorway_link"


def _download_bbox_overpass(
    bbox: tuple[float, float, float, float],
    *,
    highway_regex: str = OVERPASS_HIGHWAY_REGEX,
    timeout_s: float = 180.0,
) -> str:
    """Download a filtered OSM extract for a bbox through the Overpass API.

    The OSM API (:func:`_download_bbox`) returns *everything* in the window
    and refuses anything but a small area, which rules out a 10–15 km
    corridor; Overpass answers a filtered query over an arbitrary bbox. The
    query is::

        [out:xml][timeout:<t>];
        (way["highway"~"^(<regex>)$"](<south>,<west>,<north>,<east>); );
        (._;>;); out body;

    ``(._;>;)`` recurses down from the matched ways to their nodes, so the
    result is a self-contained OSM XML document ``netconvert`` accepts
    directly (same query shape as ``scripts/fetch_gallery_osm.py``).

    Args:
        bbox: ``(south, west, north, east)`` in WGS84 degrees.
        highway_regex: Alternation of OSM ``highway`` values to keep.
        timeout_s: Overpass server-side timeout and HTTP read timeout [s].

    Returns:
        The OSM XML document.

    Raises:
        RuntimeError: The endpoint answered with something that is not XML
            (Overpass reports rate limits and query errors as HTML).
    """
    south, west, north, east = bbox
    query = (
        f"[out:xml][timeout:{int(timeout_s)}];"
        f'(way["highway"~"^({highway_regex})$"]({south},{west},{north},{east}); );'
        "(._;>;); out body;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(
        OVERPASS_ENDPOINT, data=data, headers={"User-Agent": "flowstate-onboarding/2.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        payload: bytes = resp.read()
    if not payload.lstrip().startswith(b"<?xml"):
        raise RuntimeError(
            f"{OVERPASS_ENDPOINT}: non-XML response ({payload[:120]!r}) — "
            "Overpass is community-run and rate-limits; retry or use a mirror"
        )
    return payload.decode("utf-8")


def merge_patch_files(
    workdir: Path,
    attach_edge: str,
    next_edge: str,
    end_node: str,
    n_attach_lanes: int,
    n_next_lanes: int,
    merge: str,
    visibility_m: float | None = None,
) -> list[Path]:
    """netconvert patches for an on-ramp merge model (``RampSpec.merge``).

    The acceleration lane is lane 0 of ``attach_edge`` and dead-ends at
    ``end_node``; the mainline lanes ``1..n_attach_lanes-1`` continue into
    ``next_edge`` shifted by ``shift = n_attach_lanes - n_next_lanes``, which
    is one where the lane really drops at ``end_node`` (the netconvert default
    for a right-side lane drop, verified on the I-24 replica) and zero where
    the lane instead spilled into ``next_edge`` and was terminated at
    ``end_node`` by :func:`lane_end_patch_file` — then ``next_edge`` keeps its
    width and its lane 0 is the unfed remnant of the spill.

    * ``acceleration_lane`` — an edge patch marking lane 0 of the attach
      edge with SUMO's ``acceleration="true"`` (vehicles do not brake for
      the lane end).
    * ``zipper`` — a connection patch adding lane 0 → ``next_edge`` lane
      ``1 - shift`` beside the mainline lane 1 → the same lane (all other
      connections restated, since explicit connections replace the guessed
      ones for the edge) and a node patch making ``end_node`` a ``zipper``
      junction, so the two incoming lanes interleave.

    Args:
        workdir: Directory the patch files are written into.
        attach_edge: On-ramp attach edge id (carries the acceleration lane).
        next_edge: The corridor edge after it.
        end_node: Id of the node where ``attach_edge`` ends.
        n_attach_lanes: Lane count of ``attach_edge``.
        n_next_lanes: Lane count of ``next_edge``.
        merge: ``"acceleration_lane"`` or ``"zipper"``.
        visibility_m: Zipper only: SUMO connection ``visibility`` [m] on the two
            merging connections (``RampSpec.merge_visibility_m``); ``None``
            leaves SUMO's default.

    Returns:
        The written patch paths (empty for ``"lane_change"`` and ``"scripted"``).

    Raises:
        ValueError: Unknown merge model, or a lane layout that neither drops
            exactly one lane into ``next_edge`` nor keeps its width.
    """
    if merge in ("lane_change", "scripted"):
        return []  # the scripted merge drives vehicles at run time on the unpatched net
    shift = n_attach_lanes - n_next_lanes
    if shift not in (0, 1):
        raise ValueError(
            f"merge model {merge!r} needs {attach_edge} ({n_attach_lanes} lanes) to drop exactly "
            f"one lane into {next_edge} ({n_next_lanes} lanes), or to keep its width when the "
            "acceleration lane was terminated at its end"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    tag = f"merge_{_safe_file_stem(attach_edge)}"
    if merge == "acceleration_lane":
        edg = workdir / f"{tag}.edg.xml"
        edg.write_text(
            "<edges>\n"
            f'  <edge id="{attach_edge}">\n'
            '    <lane index="0" acceleration="true"/>\n'
            "  </edge>\n"
            "</edges>\n"
        )
        return [edg]
    if merge == "zipper":
        con = workdir / f"{tag}.con.xml"
        # the ramp lane and the mainline lane it merges with interleave from this distance
        vis = "" if visibility_m is None else f' visibility="{visibility_m:g}"'
        lines = ["<connections>"]
        lines.append(
            f'  <connection from="{attach_edge}" to="{next_edge}" fromLane="0" '
            f'toLane="{1 - shift}"{vis}/>'
        )
        for i in range(1, n_attach_lanes):
            lines.append(
                f'  <connection from="{attach_edge}" to="{next_edge}" fromLane="{i}" '
                f'toLane="{i - shift}"{vis if i == 1 else ""}/>'
            )
        lines.append("</connections>")
        con.write_text("\n".join(lines) + "\n")
        nod = workdir / f"{tag}.nod.xml"
        nod.write_text(f'<nodes>\n  <node id="{end_node}" type="zipper"/>\n</nodes>\n')
        return [nod, con]
    raise ValueError(f"unknown merge model {merge!r}")


#: How far past the attach edge a guessed acceleration lane may still run for
#: :func:`accel_lane_end` to call it a taper that may be terminated early [m].
#: ``--ramps.ramp-length`` (100 m by default, 250 m on the onboarded corridors)
#: is spent on the attach edge first and spills into the following edges, so a
#: tail of a few hundred metres is that spill; a lane that runs on beyond this
#: is an added through lane (an interchange that widens the roadway), not a
#: merge taper, and terminating it would delete real capacity.
ACCEL_LANE_TAIL_MAX_M: Final[float] = 400.0


@dataclass(frozen=True)
class AccelLaneEnd:
    """Where the acceleration lane starting on an attach edge stops.

    Produced by :func:`accel_lane_end`. ``reason`` is empty when lane 0 of the
    attach edge may be terminated at that edge's end (the lane either already
    dead-ends there, or runs on through ``tail`` and dead-ends within
    :data:`ACCEL_LANE_TAIL_MAX_M`); otherwise it states why it may not, in
    words that name the edges involved.

    Attributes:
        tail: Corridor edges after the attach edge the lane runs through, in
            driving order (empty when it dead-ends at the attach edge's end).
        tail_length_m: Summed length of ``tail`` [m].
        reason: Empty when the lane may be terminated, else the refusal.
    """

    tail: tuple[str, ...]
    tail_length_m: float
    reason: str

    @property
    def ok(self) -> bool:
        """Whether lane 0 may be terminated at the attach edge's end."""
        return not self.reason


def accel_lane_end(
    net: sumolib.net.Net,
    chain: Sequence[str],
    attach_edge: str,
    max_tail_m: float = ACCEL_LANE_TAIL_MAX_M,
) -> AccelLaneEnd:
    """Follow lane 0 downstream from ``attach_edge`` and say where it ends.

    ``--ramps.guess`` adds an acceleration lane of ``--ramps.ramp-length``
    metres at a merge. When the attach edge is longer than that, netconvert
    splits it and the lane dead-ends at the end of the ``-AddedOnRampEdge``
    piece; when it is shorter, the lane covers the whole attach edge and
    spills into the following corridor edges, dead-ending there instead. The
    merge models (``RampSpec.merge``) need the lane to dead-end at the attach
    edge's end, so the spill is deleted by a patch — but only when it really
    is a spill. This walk decides that: it follows lane-0 → lane-0
    connections along ``chain`` and reports

    * ``reason=""`` — the lane dead-ends at the attach edge (``tail`` empty)
      or after ``tail`` (a spill, safe to terminate early);
    * a refusal naming the edges — the lane feeds something other than the
      next corridor edge's lane 0 (a weaving section whose auxiliary lane
      also serves an exit), or it runs on past ``max_tail_m`` (an added
      through lane).

    Args:
        net: The compiled network (``sumolib.net.readNet``).
        chain: Corridor edge ids in driving order, ramp splits expanded
            (:func:`expand_ramp_splits`).
        attach_edge: The on-ramp's attach edge; must appear in ``chain``.
        max_tail_m: Longest tail still counted as a taper spill [m].

    Returns:
        The :class:`AccelLaneEnd` verdict.

    Raises:
        ValueError: ``attach_edge`` is not in ``chain``.
    """
    if attach_edge not in chain:
        raise ValueError(f"attach edge {attach_edge!r} is not on the corridor chain")
    i = chain.index(attach_edge)
    edge = net.getEdge(attach_edge)
    tail: list[str] = []
    length = 0.0
    while True:
        outgoing = edge.getLanes()[0].getOutgoing()
        if not outgoing:
            return AccelLaneEnd(tuple(tail), length, "")
        targets = sorted({c.getTo().getID() for c in outgoing})
        nxt = chain[i + 1] if i + 1 < len(chain) else None
        if nxt is None or targets != [nxt]:
            return AccelLaneEnd(
                tuple(tail),
                length,
                f"lane 0 of {edge.getID()} feeds {targets} and not only the next corridor edge "
                f"({nxt}): the lane doubles as an exit or branch lane (a weaving section), so "
                "terminating it would strand that movement",
            )
        to_lanes = sorted({c.getToLane().getIndex() for c in outgoing})
        if to_lanes != [0]:
            return AccelLaneEnd(
                tuple(tail),
                length,
                f"lane 0 of {edge.getID()} feeds lanes {to_lanes} of {nxt}, not its lane 0: "
                "the lane is not a right-side taper",
            )
        edge = net.getEdge(nxt)
        i += 1
        tail.append(nxt)
        length += float(edge.getLength())
        if length > max_tail_m:
            return AccelLaneEnd(
                tuple(tail),
                length,
                f"lane 0 runs on through {list(tail)} for {length:.0f} m past {attach_edge} "
                f"(more than {max_tail_m:.0f} m): this is an added through lane, not a merge "
                "taper, and terminating it would delete real capacity",
            )


def _safe_file_stem(edge_id: str) -> str:
    """An edge id made safe to embed in a file name.

    Replacing every unsafe character by ``_`` is not injective: an OSM
    corridor can carry both ``123#1`` and ``123.1``, which would collide on
    ``123_1`` and silently overwrite each other's patch — one merge would then
    be built from the other's connections. A short digest of the **raw** id is
    appended so two different ids can never share a stem.

    Args:
        edge_id: A compiled edge id, which may hold ``#``, ``.``, ``/`` or
            any other character SUMO allows and a file name does not.

    Returns:
        The id with every character outside ``[A-Za-z0-9_-]`` replaced by
        ``_``, followed by ``_`` and 8 hex digits of its digest.
    """
    digest = hashlib.blake2s(edge_id.encode("utf-8"), digest_size=4).hexdigest()
    return f"{re.sub(r'[^A-Za-z0-9_-]', '_', edge_id)}_{digest}"


def lane_end_patch_file(
    workdir: Path, attach_edge: str, next_edge: str, to_lanes: Sequence[int] = (0,)
) -> Path:
    """Write the connection patch that terminates lane 0 of ``attach_edge``.

    ``<delete>`` elements remove lane 0's outgoing connections and nothing
    else, so the lane dead-ends at the edge's end while lanes ``1..n`` keep
    the connections netconvert guessed. The patch names a compiled edge id
    (possibly a ``-AddedOnRampEdge`` piece netconvert itself created), so it
    must be applied to the **compiled** network with :func:`patch_net`, not on
    an OSM re-import: connection files are read before ramp guessing runs, and
    netconvert refuses a connection naming an edge that does not exist yet.

    The edge id goes into the file *name*, so every character outside
    ``[A-Za-z0-9_-]`` is replaced by ``_``: an OSM id may carry ``#``, ``.``
    or ``/``, and a dot in particular would give the file a suffix that
    :func:`_patch_args` could not route (and a slash a path that does not
    exist). The id inside the XML is untouched — that is what netconvert
    matches on.

    Args:
        workdir: Directory the patch file is written into.
        attach_edge: Edge whose lane 0 is terminated.
        next_edge: The corridor edge its lane 0 currently connects to.
        to_lanes: Lane indices of ``next_edge`` that lane 0 connects to.

    Returns:
        The written ``.con.xml`` path.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"accel_end_{_safe_file_stem(attach_edge)}.con.xml"
    lines = ["<connections>"]
    for lane in to_lanes:
        lines.append(
            f'  <delete from="{attach_edge}" to="{next_edge}" fromLane="0" toLane="{lane}"/>'
        )
    lines.append("</connections>")
    path.write_text("\n".join(lines) + "\n")
    return path


def _patch_args(patch_files: Sequence[Path]) -> list[str]:
    """netconvert flags loading plain-XML patches, routed by file suffix.

    Patches of the same kind are passed as one comma-separated value:
    netconvert refuses a repeated option ("a value for the option
    'connection-files' was already set"), which a corridor with two patched
    merges would otherwise produce.

    The kind is read from the file's **last two** suffixes, not from its
    first dot: a name built around an edge id that itself holds a dot is a
    ``.con.xml`` patch all the same.

    Args:
        patch_files: ``*.nod.xml`` / ``*.edg.xml`` / ``*.con.xml`` patches.

    Returns:
        The flag/value pairs to append to a netconvert command line.

    Raises:
        ValueError: A patch file has none of the three suffixes.
    """
    by_flag: dict[str, list[str]] = {}
    for patch in patch_files:
        flag = {
            ".nod.xml": "--node-files",
            ".edg.xml": "--edge-files",
            ".con.xml": "--connection-files",
        }.get("".join(Path(patch).suffixes[-2:]), None)
        if flag is None:
            raise ValueError(f"patch file {patch} must end in .nod.xml, .edg.xml or .con.xml")
        by_flag.setdefault(flag, []).append(str(patch))
    args: list[str] = []
    for flag, paths in by_flag.items():
        args += [flag, ",".join(paths)]
    return args


def patch_net(
    bundle: NetBundle,
    patch_files: Sequence[Path],
    *,
    internal_links: bool = False,
    stem: str = "patched",
) -> NetBundle:
    """Re-run netconvert over a **compiled** network with plain-XML patches.

    ``netconvert -s <net> --connection-files …`` edits the network that was
    built, so a patch may name edges netconvert generated itself (the
    ``-AddedOnRampEdge`` pieces of ``--ramps.guess``); an OSM re-import cannot,
    because its patches are read before those edges exist. The edge set must
    survive unchanged — the corridor order, entry/exit roles and ramp
    resolution of ``bundle`` all refer to it — and lengths/offsets are read
    back from the patched network.

    Args:
        bundle: The network to patch (any builder).
        patch_files: Patches routed by suffix (see :func:`_patch_args`).
        internal_links: Keep SUMO's internal junction lanes, as on the import
            that produced ``bundle``.
        stem: Basename of the patched network inside ``bundle.workdir``.

    Returns:
        A bundle pointing at the patched network, with ``patch_files``
        extended by the applied patches.

    Raises:
        RuntimeError: netconvert failed, or the patch changed the edge set.
        ValueError: A patch file has an unroutable suffix.
    """
    out = bundle.workdir / f"{stem}.net.xml"
    args = [
        "--sumo-net-file",
        str(bundle.net_path),
        "-o",
        str(out),
        *([] if internal_links else ["--no-internal-links"]),
        "--no-turnarounds",
        *_patch_args(patch_files),
    ]
    _netconvert(args)
    parsed = sumolib.net.readNet(str(out))
    by_id = {e.getID(): e for e in parsed.getEdges(withInternal=False)}
    missing = [e for e in bundle.edge_ids if e not in by_id]
    if missing:
        raise RuntimeError(
            f"netconvert patches {[Path(p).name for p in patch_files]} dropped corridor edges "
            f"{missing} from the network"
        )
    lengths = [float(by_id[e].getLength()) for e in bundle.edge_ids]
    offsets: list[float] = [0.0]
    for elen in lengths[:-1]:
        offsets.append(offsets[-1] + elen)
    return dataclasses.replace(
        bundle,
        net_path=out,
        edge_lengths=tuple(lengths),
        offsets=tuple(offsets),
        total_length_m=float(sum(lengths)),
        patch_files=bundle.patch_files + tuple(str(p) for p in patch_files),
    )


def osm_import(
    osm_file: str | Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    corridor_edges: tuple[str, ...] | list[str] = (),
    workdir: Path | None = None,
    keep_edges: tuple[str, ...] | list[str] = (),
    geometry_remove: bool = True,
    patch_files: Sequence[Path] = (),
    internal_links: bool = False,
    *,
    download: Literal["osm_api", "overpass"] = "osm_api",
    allowed_roots: Iterable[str | Path] | None = None,
    netconvert_extra: Sequence[str] = (),
) -> NetBundle:
    """Import an OSM extract into a SUMO network (the ``osm_generic`` pipeline).

    Steps (CLAUDE.md §3.2.4): OSM XML (file, or bbox download from the OSM
    API) → ``netconvert`` with the shipped highway typemap
    (``osmNetconvert.typ.xml``) → optional corridor pruning via
    ``--keep-edges.explicit`` to the named edges. The returned bundle orders
    edges by ``corridor_edges`` when given (the analysis corridor, upstream →
    downstream), else by edge id — linear-x offsets are only meaningful for a
    chain-like corridor selection.

    Args:
        osm_file: Path to an ``.osm`` XML extract. Takes precedence over bbox.
        bbox: ``(south, west, north, east)`` WGS84 download window; requires
            network access. The extract is written to
            ``<workdir>/extract.osm`` and an existing non-empty file there is
            reused instead of downloading again, so re-importing the same
            workdir (the second pass of a merge-model build, a rebuilt run)
            uses the same map rather than whatever the map says today.
        download: Which service a ``bbox`` is fetched from. ``"osm_api"``
            (default) is the OSM API, which returns every feature in the
            window and refuses anything but a small area. ``"overpass"`` is
            the Overpass API filtered to motorway ways
            (:func:`_download_bbox_overpass`) — the corridor-onboarding path
            (:func:`microsim.scenarios.corridor_from_bbox`), which needs
            10–15 km windows.
        corridor_edges: SUMO edge ids forming the corridor after import, in
            driving order. Empty ⇒ keep the whole network.
        workdir: Directory for inputs/outputs. Required.
        keep_edges: Additional edge ids (e.g. interchange ramps,
            ``OSMNetwork.ramps``) kept and pinned through pruning alongside
            ``corridor_edges`` without joining the corridor chain.
        internal_links: Keep SUMO's internal junction lanes (drop
            ``--no-internal-links``); default off, see ``OSMNetwork``.
        patch_files: netconvert plain-XML patches applied on top of the OSM
            import, routed by suffix — ``*.nod.xml`` (``--node-files``),
            ``*.edg.xml`` (``--edge-files``), ``*.con.xml``
            (``--connection-files``); see :func:`merge_patch_files`.
        geometry_remove: Pass ``--geometry.remove`` (default) so runs of
            raw OSM ways are joined into single edges. ``False`` keeps every
            raw way as its own edge (ids are the way ids, ``#``-split at
            junctions), which is how the ids a scenario names must be
            discovered: a joined edge's id is one of its member ways and a
            corridor pruned by that id alone silently loses the rest.
        allowed_roots: Directories ``osm_file`` must resolve inside — defence
            in depth behind the API's HTTP 422 (:mod:`microsim.paths`), so a
            config that reaches the worker unvalidated cannot hand netconvert
            an arbitrary server file. Symlinks and ``..`` are resolved before
            the comparison, and containment is checked before existence so
            the refusal is never an existence oracle. ``None`` (the library
            default, for scripts) falls back to the roots published in
            ``FLOWSTATE_WORKER_PATH_ROOTS`` and to no confinement when that
            is unset. ``bbox`` downloads and ``patch_files`` are unaffected:
            both are written by this function into ``workdir``.

    Returns:
        The compiled :class:`NetBundle` (``kind="osm"``).

    Raises:
        ValueError: Neither source given, missing workdir, ``osm_file``
            outside ``allowed_roots`` (message naming only the value and the
            roots), or a named corridor or kept edge absent from the imported
            network.
        RuntimeError: netconvert failure.
    """
    if workdir is None:
        raise ValueError("osm_import() requires an explicit workdir")
    if osm_file is None and bbox is None:
        raise ValueError("osm_import() needs osm_file or bbox")
    if osm_file is not None:
        ensure_within_roots(
            osm_file,
            Path(osm_file).resolve(),
            effective_roots(allowed_roots),
            field="osm_file",
        )
    workdir.mkdir(parents=True, exist_ok=True)

    if osm_file is None:
        assert bbox is not None
        extract = workdir / "extract.osm"
        if extract.is_file() and extract.stat().st_size > 0:
            osm_path = extract  # reuse: the map moves, a scenario must not
        elif download == "overpass":
            extract.write_text(_download_bbox_overpass(bbox))
            osm_path = extract
        else:
            osm_path = _download_bbox(bbox, extract)
    else:
        osm_path = Path(osm_file)
        if not osm_path.is_file():
            raise ValueError(f"osm_file not found: {osm_path}")

    net = workdir / "osm.net.xml"
    args = [
        "--osm-files",
        str(osm_path),
        "-o",
        str(net),
        *([] if internal_links else ["--no-internal-links"]),
        "--no-turnarounds",
        # NOTE: --remove-edges.isolated is deliberately NOT passed — it strips
        # a standalone corridor ("road without junctions"), which is exactly
        # what a pruned analysis corridor often is.
    ]
    if geometry_remove:
        args.append("--geometry.remove")
    # Scenario-level netconvert options (OSMNetwork.netconvert_extra), e.g. ramp
    # guessing for maps without acceleration lanes; appended verbatim.
    args += [str(a) for a in netconvert_extra]
    typemap = _osm_typemap()
    if typemap is not None:
        args += ["--type-files", str(typemap)]
    args += _patch_args(patch_files)
    if corridor_edges:
        # Pruning happens at load time, i.e. at raw-OSM-way granularity and
        # BEFORE --geometry.remove joins edges; the named corridor edges must
        # therefore be load-time ids (way ids, possibly ``#``-split). Without
        # the second flag the join stage can then merge a kept corridor edge
        # into its neighbor and rename it, so the requested ids no longer
        # exist in the compiled net (observed on real motorway extracts);
        # ``--geometry.remove.keep-edges.explicit`` pins the named edges
        # through the join so the pruned net contains exactly the requested
        # chain with stable ids and offsets.
        joined = ",".join([*corridor_edges, *[e for e in keep_edges if e not in corridor_edges]])
        args += ["--keep-edges.explicit", joined]
        if geometry_remove:
            args += ["--geometry.remove.keep-edges.explicit", joined]
    _netconvert(args)

    parsed = sumolib.net.readNet(str(net))
    by_id = {e.getID(): e for e in parsed.getEdges(withInternal=False)}
    if corridor_edges:
        ordered = expand_ramp_splits(corridor_edges, by_id)
        missing = [e for e in [*ordered, *keep_edges] if e not in by_id]
        if missing:
            raise ValueError(
                f"corridor/kept edges not present after import: {missing}; "
                f"available: {sorted(by_id)}"
            )
    else:
        ordered = sorted(by_id)

    lengths = [float(by_id[e].getLength()) for e in ordered]
    offsets: list[float] = [0.0]
    for elen in lengths[:-1]:
        offsets.append(offsets[-1] + elen)
    return NetBundle(
        net_path=net,
        edge_ids=tuple(ordered),
        edge_lengths=tuple(lengths),
        offsets=tuple(offsets),
        total_length_m=float(sum(lengths)),
        workdir=workdir,
        kind="osm",
    )
