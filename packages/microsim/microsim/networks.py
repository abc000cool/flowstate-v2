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

All geometry is SI (meters). ``netconvert`` always receives
``--no-internal-links`` so vehicles never occupy internal junction lanes and
edge lengths partition the drivable length exactly.
"""

from __future__ import annotations

import math
import os
import subprocess
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sumolib

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


def osm_import(
    osm_file: str | Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    corridor_edges: tuple[str, ...] | list[str] = (),
    workdir: Path | None = None,
    keep_edges: tuple[str, ...] | list[str] = (),
    geometry_remove: bool = True,
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
            network access.
        corridor_edges: SUMO edge ids forming the corridor after import, in
            driving order. Empty ⇒ keep the whole network.
        workdir: Directory for inputs/outputs. Required.
        keep_edges: Additional edge ids (e.g. interchange ramps,
            ``OSMNetwork.ramps``) kept and pinned through pruning alongside
            ``corridor_edges`` without joining the corridor chain.
        geometry_remove: Pass ``--geometry.remove`` (default) so runs of
            raw OSM ways are joined into single edges. ``False`` keeps every
            raw way as its own edge (ids are the way ids, ``#``-split at
            junctions), which is how the ids a scenario names must be
            discovered: a joined edge's id is one of its member ways and a
            corridor pruned by that id alone silently loses the rest.

    Returns:
        The compiled :class:`NetBundle` (``kind="osm"``).

    Raises:
        ValueError: Neither source given, missing workdir, or a named
            corridor or kept edge absent from the imported network.
        RuntimeError: netconvert failure.
    """
    if workdir is None:
        raise ValueError("osm_import() requires an explicit workdir")
    if osm_file is None and bbox is None:
        raise ValueError("osm_import() needs osm_file or bbox")
    workdir.mkdir(parents=True, exist_ok=True)

    if osm_file is None:
        assert bbox is not None
        osm_path = _download_bbox(bbox, workdir / "extract.osm")
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
        "--no-internal-links",
        "--no-turnarounds",
        # NOTE: --remove-edges.isolated is deliberately NOT passed — it strips
        # a standalone corridor ("road without junctions"), which is exactly
        # what a pruned analysis corridor often is.
    ]
    if geometry_remove:
        args.append("--geometry.remove")
    typemap = _osm_typemap()
    if typemap is not None:
        args += ["--type-files", str(typemap)]
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
        missing = [e for e in [*corridor_edges, *keep_edges] if e not in by_id]
        if missing:
            raise ValueError(
                f"corridor/kept edges not present after import: {missing}; "
                f"available: {sorted(by_id)}"
            )
        ordered = list(corridor_edges)
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
