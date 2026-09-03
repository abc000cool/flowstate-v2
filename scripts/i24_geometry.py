"""I-24 MOTION corridor geometry: OSM network ↔ mile-marker coordinates.

The replica's road network comes from OpenStreetMap (``data/osm/i24_motion.osm``,
motorway + motorway_link ways in a bbox around the testbed, fetched by
``scripts/fetch_gallery_osm.fetch_one``), compiled by ``netconvert`` through
``microsim.networks.osm_import``. The trajectory data lives in the I-24 MOTION
roadway coordinate (``x_position`` ≈ mile marker × 5280 ft). The auxiliary
information distributed with the data (``mile_marker_layer.csv``,
``ramp_and_landmark_layer.csv``, ``pole_layer.csv``; WGS84 and Tennessee
state-plane columns) ties the two together: every landmark is projected onto
the compiled network here, which yields (a) the westbound mainline edge
chain in driving order, (b) the linear position along that chain of every
mile marker, and (c) a fitted affine map between chain position and the
data's ``x`` (0 at MM 62.7, increasing westbound).

Projection: SUMO's ``netconvert`` projects OSM lon/lat with UTM (here zone
16N, ``+proj=utm +zone=16 +ellps=WGS84``) and shifts by ``netOffset``. The
forward UTM transform is implemented below (standard Transverse Mercator
series, Snyder 1987 / USGS PP 1395, eqs. 8-9 to 8-15; accurate to
millimetres) so no projection library is needed; :func:`check_projection`
verifies it against OSM nodes that survive as junctions in the compiled net.
"""

from __future__ import annotations

import csv
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import sumolib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import REPO_ROOT

OSM_FILE = REPO_ROOT / "data" / "osm" / "i24_motion.osm"
AUX_DIR = REPO_ROOT / "data" / "i24motion" / "auxiliary_information"

#: Testbed limits in mile markers (westbound travel runs 62.7 → 58.7).
MM_UPSTREAM_WB = 62.7
MM_DOWNSTREAM_WB = 58.7
FT_PER_MILE = 5280.0
M_PER_MILE = 1609.344

# WGS84 ellipsoid
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)
_EP2 = _E2 / (1.0 - _E2)
_K0 = 0.9996


def utm_forward(lon_deg: float, lat_deg: float, zone: int) -> tuple[float, float]:
    """WGS84 lon/lat → UTM (zone, northern hemisphere) easting/northing [m].

    Snyder (1987) *Map Projections — A Working Manual*, USGS PP 1395,
    Transverse Mercator eqs. 8-9 … 8-15 (ellipsoidal series).
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
    return x + 500000.0, y


@dataclass(frozen=True)
class NetProjection:
    """The compiled net's projection: UTM zone + netOffset."""

    zone: int
    offset_x: float
    offset_y: float

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = utm_forward(lon, lat, self.zone)
        return x + self.offset_x, y + self.offset_y


def read_projection(net_path: Path) -> NetProjection:
    """Parse ``<location netOffset=... projParameter=...>`` from a .net.xml."""
    loc = ET.parse(net_path).getroot().find("location")
    if loc is None:
        raise ValueError(f"{net_path}: no <location> element")
    params = loc.get("projParameter", "")
    m = re.search(r"\+zone=(\d+)", params)
    if "+proj=utm" not in params or m is None:
        raise ValueError(f"{net_path}: unsupported projection {params!r}")
    ox, oy = (float(v) for v in loc.get("netOffset", "0,0").split(","))
    return NetProjection(zone=int(m.group(1)), offset_x=ox, offset_y=oy)


def check_projection(net: sumolib.net.Net, proj: NetProjection, osm_file: Path) -> float:
    """Max distance [m] between projected OSM node coords and net junctions.

    OSM node ids survive as junction ids in the compiled network, so every
    shared id is an independent check of :func:`utm_forward` + offset.
    """
    osm_nodes = {
        n.get("id"): (float(n.get("lon")), float(n.get("lat")))
        for n in ET.parse(osm_file).getroot().iter("node")
    }
    worst = 0.0
    n_checked = 0
    for node in net.getNodes():
        ll = osm_nodes.get(node.getID())
        if ll is None:
            continue
        px, py = proj.to_xy(*ll)
        nx, ny = node.getCoord()
        worst = max(worst, math.hypot(px - nx, py - ny))
        n_checked += 1
    if n_checked == 0:
        raise ValueError("no OSM node ids found among net junctions")
    return worst


def read_landmarks() -> tuple[dict[float, tuple[float, float]], list[dict[str, str]]]:
    """Mile markers ``{MM: (lon, lat)}`` and the ramp/landmark rows."""
    mm = {}
    with open(AUX_DIR / "mile_marker_layer.csv") as f:
        for r in csv.DictReader(f):
            mm[float(r["MM"])] = (float(r["X_WGS84"]), float(r["Y_WGS84"]))
    with open(AUX_DIR / "ramp_and_landmark_layer.csv") as f:
        ramps = list(csv.DictReader(f))
    return mm, ramps


def _project_point_on_polyline(
    shape: list[tuple[float, float]], px: float, py: float
) -> tuple[float, float]:
    """(distance along polyline [m], perpendicular distance [m]) of the closest point."""
    best = (0.0, math.inf)
    run = 0.0
    for (x0, y0), (x1, y1) in pairwise(shape):
        dx, dy = x1 - x0, y1 - y0
        seg = math.hypot(dx, dy)
        if seg == 0.0:
            continue
        u = ((px - x0) * dx + (py - y0) * dy) / (seg * seg)
        u = min(max(u, 0.0), 1.0)
        cx, cy = x0 + u * dx, y0 + u * dy
        d = math.hypot(px - cx, py - cy)
        if d < best[1]:
            best = (run + u * seg, d)
        run += seg
    return best


def edge_bearing_deg(edge: sumolib.net.edge.Edge) -> float:
    """Math bearing (degrees CCW from +x) of an edge's start→end chord."""
    sh = edge.getShape()
    (x0, y0), (x1, y1) = sh[0], sh[-1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0


def westbound_mainline_chain(net: sumolib.net.Net) -> list[sumolib.net.edge.Edge]:
    """Westbound mainline edges in driving order.

    Mainline = ``highway.motorway`` edges; westbound = math bearing in
    (90°, 200°) (the corridor runs ESE→WNW). The chain is walked from the
    edge with no westbound-motorway predecessor along successor links.
    """
    wb = [
        e
        for e in net.getEdges(withInternal=False)
        if e.getType() == "highway.motorway" and 90.0 < edge_bearing_deg(e) < 200.0
    ]
    ids = {e.getID() for e in wb}
    heads = [e for e in wb if not any(p.getID() in ids for p in e.getIncoming())]
    if len(heads) != 1:
        raise ValueError(f"expected one westbound chain head, found {[h.getID() for h in heads]}")
    chain = [heads[0]]
    while True:
        nxt = [s for s in chain[-1].getOutgoing() if s.getID() in ids]
        if not nxt:
            break
        if len(nxt) > 1:
            raise ValueError(
                f"branching mainline after {chain[-1].getID()}: {[n.getID() for n in nxt]}"
            )
        chain.append(nxt[0])
    if len(chain) != len(wb):
        missing = ids - {e.getID() for e in chain}
        raise ValueError(f"westbound motorway edges not on the chain: {sorted(missing)}")
    return chain


@dataclass(frozen=True)
class ChainGeometry:
    """Westbound chain with the fitted chain-position ↔ data-x map."""

    edge_ids: tuple[str, ...]
    edge_lengths: tuple[float, ...]
    offsets: tuple[float, ...]
    lanes: tuple[int, ...]
    mm_chain_pos: dict[float, float]
    """Chain position [m] of each mile marker sign (from the aux layer)."""
    mm_perp_m: dict[float, float]
    """Perpendicular distance [m] of each sign from the chain (sanity)."""
    slope_m_per_mile: float
    """Fitted chain metres per data mile (should be ≈ 1609 if the OSM
    centreline and the roadway spline agree)."""
    chain_pos_at_mm_upstream: float
    """Chain position [m] where data x = 0 (MM 62.7)."""
    residual_rms_m: float
    ramp_chain_pos: dict[str, float]
    """Chain position [m] of each westbound ramp landmark."""

    @property
    def total_length_m(self) -> float:
        return float(sum(self.edge_lengths))

    def chain_pos_of_data_x(self, x_m: float) -> float:
        """Data ``x`` [m, 0 at MM 62.7] → chain position [m]."""
        return self.chain_pos_at_mm_upstream + x_m * self.slope_m_per_mile / M_PER_MILE

    def data_x_of_chain_pos(self, pos_m: float) -> float:
        """Chain position [m] → data ``x`` [m]."""
        return (pos_m - self.chain_pos_at_mm_upstream) * M_PER_MILE / self.slope_m_per_mile


def chain_geometry(net: sumolib.net.Net, proj: NetProjection) -> ChainGeometry:
    """Project the mile markers and westbound ramps onto the westbound chain."""
    chain = westbound_mainline_chain(net)
    lengths = [float(e.getLength()) for e in chain]
    offsets = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    # Project onto each edge's own polyline, rescaling the along-distance so
    # it stays consistent with SUMO's edge length (offsets are edge lengths).
    pos_by_edge: list[tuple[float, list[tuple[float, float]]]] = []
    for e, off in zip(chain, offsets, strict=True):
        pos_by_edge.append((float(off), [tuple(p) for p in e.getShape()]))

    def project(lon: float, lat: float) -> tuple[float, float]:
        px, py = proj.to_xy(lon, lat)
        best = (math.nan, math.inf)
        for (off, sh), e in zip(pos_by_edge, chain, strict=True):
            along, perp = _project_point_on_polyline(sh, px, py)
            if perp < best[1]:
                poly_len = sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in pairwise(sh))
                scale = float(e.getLength()) / poly_len if poly_len > 0 else 1.0
                best = (off + along * scale, perp)
        return best

    mm, ramps = read_landmarks()
    mm_pos: dict[float, float] = {}
    mm_perp: dict[float, float] = {}
    for m, (lon, lat) in mm.items():
        pos, perp = project(lon, lat)
        if perp < 60.0:  # signs stand on the roadside of one carriageway
            mm_pos[m] = pos
            mm_perp[m] = perp
    if len(mm_pos) < 3:
        raise ValueError(f"only {len(mm_pos)} mile markers project onto the westbound chain")
    miles = np.array(sorted(mm_pos))
    pos = np.array([mm_pos[m] for m in miles])
    # Westbound: chain position increases as mile marker decreases.
    slope, intercept = np.polyfit(miles, pos, 1)
    fitted = slope * miles + intercept
    rms = float(np.sqrt(np.mean((pos - fitted) ** 2)))
    ramp_pos = {}
    for r in ramps:
        if r["Direction"] != "wb":
            continue
        p, perp = project(float(r["X_WGS84"]), float(r["Y_WGS84"]))
        if perp < 80.0:
            ramp_pos[r["Landmark name"]] = p
    return ChainGeometry(
        edge_ids=tuple(e.getID() for e in chain),
        edge_lengths=tuple(lengths),
        offsets=tuple(float(o) for o in offsets),
        lanes=tuple(e.getLaneNumber() for e in chain),
        mm_chain_pos=mm_pos,
        mm_perp_m=mm_perp,
        slope_m_per_mile=float(-slope),
        chain_pos_at_mm_upstream=float(slope * MM_UPSTREAM_WB + intercept),
        residual_rms_m=rms,
        ramp_chain_pos=ramp_pos,
    )


def main() -> None:
    from microsim.networks import osm_import

    workdir = REPO_ROOT / "data" / "i24motion" / "processed" / "net_full"
    bundle = osm_import(osm_file=OSM_FILE, workdir=workdir)
    net = sumolib.net.readNet(str(bundle.net_path))
    proj = read_projection(bundle.net_path)
    worst = check_projection(net, proj, OSM_FILE)
    print(f"projection check: worst junction mismatch {worst:.3f} m ({proj})")
    geo = chain_geometry(net, proj)
    print(f"westbound chain: {len(geo.edge_ids)} edges, {geo.total_length_m:.0f} m")
    for eid, ln, off, la in zip(
        geo.edge_ids, geo.edge_lengths, geo.offsets, geo.lanes, strict=True
    ):
        print(
            f"  {eid:>14s}  {la} lanes  {ln:7.1f} m  @ {off:7.1f} m  data x {geo.data_x_of_chain_pos(off):8.1f} m"
        )
    print("mile markers on chain:")
    for m in sorted(geo.mm_chain_pos, reverse=True):
        print(f"  MM {m:5.1f}: chain {geo.mm_chain_pos[m]:7.1f} m (perp {geo.mm_perp_m[m]:4.1f} m)")
    print(
        f"fit: {geo.slope_m_per_mile:.1f} chain m per mile (1609.3 nominal), "
        f"residual RMS {geo.residual_rms_m:.1f} m, x=0 at chain {geo.chain_pos_at_mm_upstream:.1f} m"
    )
    print("westbound ramps:")
    for name, p in sorted(geo.ramp_chain_pos.items(), key=lambda kv: kv[1]):
        print(f"  {name:>18s}: chain {p:7.1f} m, data x {geo.data_x_of_chain_pos(p):8.1f} m")


if __name__ == "__main__":
    main()
