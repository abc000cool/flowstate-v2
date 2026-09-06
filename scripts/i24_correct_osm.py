"""Correct the I-24 OSM extract's merge and diverge geometry from measured positions.

Two auxiliary lanes in ``data/osm/i24_motion.osm`` are shorter than the road
they describe. The provider's landmark layer
(``data/i24motion/auxiliary_information/ramp_and_landmark_layer.csv``, chain
positions in ``artifacts/i24_replica_inputs.json``) and the recording's own
lane-5 occupancy (docs/I24_VALIDATION.md §0.5) put

* the Old Hickory Blvd westbound **acceleration lane** end at
  ``wb_OH_on_end`` — the map ends way ``977008894`` (5 lanes) about 250 m
  earlier, so its lane 0 dead-ends and merging traffic has 730 m instead of
  about 1,200 m;
* the Hickory Hollow Pkwy westbound **deceleration lane** start at
  ``wb_HH_off_start`` — the map starts way ``977008892`` (5 lanes) about
  350 m later, so exiting traffic has a 128 m pocket instead of about 500 m.

The correction moves the way boundaries to the landmark positions by
inserting one interpolated node at each position on way ``977008893`` and
transferring the way membership of the intervening geometry: no node is
moved, no lane count is changed, no tag is edited. Output:
``data/osm/i24_motion_corrected.osm`` plus a JSON provenance block printed to
stdout and written beside it. Run from the repo root::

    uv run --no-sync python scripts/i24_correct_osm.py
"""

from __future__ import annotations

import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import REPO_ROOT

SRC = REPO_ROOT / "data" / "osm" / "i24_motion.osm"
DST = REPO_ROOT / "data" / "osm" / "i24_motion_corrected.osm"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"

UPSTREAM_WAY = "977008894"  # 5 lanes: Old Hickory acceleration lane is lane 0
MIDDLE_WAY = "977008893"  # 4 lanes between the two auxiliary lanes
DOWNSTREAM_WAY = "977008892"  # 5 lanes: Hickory Hollow deceleration lane is lane 0
CUTS = (("wb_OH_on_end", UPSTREAM_WAY), ("wb_HH_off_start", DOWNSTREAM_WAY))
NEW_NODE_BASE = 9_900_000_000_001  # unused id range in the extract


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2.0)
    return 6_371_000.0 * math.hypot(x, lat2 - lat1)


def main() -> None:
    inputs = json.loads(INPUTS.read_text())
    geom = inputs["geometry"]
    edges, lengths = geom["corridor_edges"], geom["edge_lengths_m"]
    chain0 = float(geom["sim_origin_chain_m"])
    start_chain = {}
    c = chain0
    for e, length in zip(edges, lengths, strict=True):
        start_chain[e] = c
        c += float(length)
    landmarks = geom["ramp_landmarks_chain_m"]
    mid_start = start_chain[MIDDLE_WAY]
    offsets = {name: float(landmarks[name]) - mid_start for name, _ in CUTS}

    tree = ET.parse(SRC)
    root = tree.getroot()
    node_el = {n.get("id"): n for n in root.iter("node")}
    way_el = {w.get("id"): w for w in root.iter("way")}
    mid = way_el[MIDDLE_WAY]
    refs = [nd.get("ref") for nd in mid.findall("nd")]
    coords = [(float(node_el[r].get("lat")), float(node_el[r].get("lon"))) for r in refs]
    cum = [0.0]
    for a, b in pairwise(coords):
        cum.append(cum[-1] + _haversine(a, b))

    def insert_at(offset_m: float, new_id: str) -> int:
        """Insert an interpolated node at ``offset_m`` along the way; return its index."""
        if not 0.0 < offset_m < cum[-1]:
            raise ValueError(f"cut {offset_m:.1f} m outside way {MIDDLE_WAY} (0, {cum[-1]:.1f})")
        k = next(i for i in range(1, len(cum)) if cum[i] >= offset_m)
        f = (offset_m - cum[k - 1]) / (cum[k] - cum[k - 1])
        lat = coords[k - 1][0] + f * (coords[k][0] - coords[k - 1][0])
        lon = coords[k - 1][1] + f * (coords[k][1] - coords[k - 1][1])
        el = ET.Element("node", id=new_id, lat=f"{lat:.7f}", lon=f"{lon:.7f}", version="1")
        first_way = next(i for i, ch in enumerate(list(root)) if ch.tag == "way")
        root.insert(first_way, el)
        node_el[new_id] = el
        refs.insert(k, new_id)
        coords.insert(k, (lat, lon))
        cum.insert(k, offset_m)
        return k

    new_ids = [str(NEW_NODE_BASE + i) for i in range(len(CUTS))]
    k_up = insert_at(offsets["wb_OH_on_end"], new_ids[0])
    k_down = insert_at(offsets["wb_HH_off_start"], new_ids[1])
    if not k_up < k_down:
        raise ValueError("cuts out of order")

    def set_refs(way: ET.Element, new_refs: list[str]) -> None:
        for nd in way.findall("nd"):
            way.remove(nd)
        tags = way.findall("tag")
        for t in tags:
            way.remove(t)
        for r in new_refs:
            ET.SubElement(way, "nd", ref=r)
        for t in tags:
            way.append(t)

    up_refs = [nd.get("ref") for nd in way_el[UPSTREAM_WAY].findall("nd")]
    down_refs = [nd.get("ref") for nd in way_el[DOWNSTREAM_WAY].findall("nd")]
    if up_refs[-1] != refs[0] or down_refs[0] != refs[-1]:
        raise ValueError("way chain is not contiguous at the expected junction nodes")
    set_refs(way_el[UPSTREAM_WAY], up_refs + refs[1 : k_up + 1])
    set_refs(way_el[MIDDLE_WAY], refs[k_up : k_down + 1])
    set_refs(way_el[DOWNSTREAM_WAY], refs[k_down:] + down_refs[1:])

    tree.write(DST, encoding="utf-8", xml_declaration=True)
    prov = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(SRC.relative_to(REPO_ROOT)),
        "output": str(DST.relative_to(REPO_ROOT)),
        "inputs_config_hash": inputs.get("config_hash"),
        "landmark_chain_m": {name: float(landmarks[name]) for name, _ in CUTS},
        "cut_offset_on_way_977008893_m": {k: round(v, 1) for k, v in offsets.items()},
        "new_nodes": dict(zip([n for n, _ in CUTS], new_ids, strict=True)),
        "way_lengths_before_m": {UPSTREAM_WAY: None, MIDDLE_WAY: round(cum[-1], 1)},
        "moved_m": {
            "acceleration_lane_extended": round(offsets["wb_OH_on_end"], 1),
            "deceleration_lane_extended": round(cum[-1] - offsets["wb_HH_off_start"], 1),
        },
        "rule": "way boundaries moved to the provider's landmark positions; nodes, tags and lane counts unchanged",
    }
    (DST.with_suffix(".provenance.json")).write_text(json.dumps(prov, indent=2))
    print(json.dumps(prov, indent=2))


if __name__ == "__main__":
    main()
