"""Map correction at two I-94 WB splits (2026-09-24): ``OSMNetwork.patch_files``.

On the committed MnDOT extract netconvert compiled two right-hand exits on the
leftmost lanes: the 12th Street exit (``82150350``) got a fourth, left lane of
``1001426896`` under ``--ramps.guess`` and the Mounds/Kellogg exit
(``18207912``) the two leftmost of the five lanes of ``45608485`` regardless
of options. Through traffic trapped in those lanes started the corridor-wide
lock of the 2026-09-23 rounds (docs/ONBOARDING_MNDOT.md §9). The scenario
fixes them with ``--ramps.unset 1001426896`` and an explicit connection
patch; this test pins both compiled splits.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from flowstate_core.config import OSMNetwork, ScenarioConfig, config_hash
from microsim.networks import osm_import

SCENARIO = Path("scenarios/mndot_i94_wb_stpaul_weave.yaml")
OSM = Path("data/osm/mndot_i94_wb_stpaul.osm")


def _connections(net_path: Path, from_edge: str) -> list[tuple[int, str, int]]:
    root = ET.parse(net_path).getroot()
    return sorted(
        (int(c.get("fromLane", -1)), str(c.get("to")), int(c.get("toLane", -1)))
        for c in root.findall("connection")
        if c.get("from") == from_edge
    )


@pytest.mark.skipif(not (SCENARIO.is_file() and OSM.is_file()), reason="MnDOT files absent")
def test_i94_splits_exit_on_the_right(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(yaml.safe_load(SCENARIO.read_text()))
    net = cfg.network
    assert isinstance(net, OSMNetwork)
    assert net.patch_files == ["data/osm/mndot_i94_wb_stpaul.splits.con.xml"]
    keep = tuple(e for r in net.ramps for e in r.edges)
    bundle = osm_import(
        osm_file=net.osm_file,
        corridor_edges=tuple(net.corridor_edges),
        workdir=tmp_path,
        keep_edges=keep,
        patch_files=[Path(p) for p in net.patch_files],
        netconvert_extra=tuple(net.netconvert_extra),
    )
    # 12th Street: three lanes, lane 0 an option lane (exit and through).
    assert _connections(bundle.net_path, "1001426896") == [
        (0, "82150350", 0),
        (0, "82578022", 0),
        (1, "82578022", 1),
        (2, "82578022", 2),
    ]
    # Mounds / Kellogg: the two rightmost of five lanes exit.
    assert _connections(bundle.net_path, "45608485") == [
        (0, "18207912", 0),
        (1, "18207912", 1),
        (2, "45782590", 0),
        (3, "45782590", 1),
        (4, "45782590", 2),
    ]
    # the acceleration lanes from ramp guessing survive the exclusion
    root = ET.parse(bundle.net_path).getroot()
    assert sum(1 for e in root.findall("edge") if "AddedOnRampEdge" in str(e.get("id"))) == 5


def test_patch_files_enter_the_config_hash() -> None:
    base = yaml.safe_load(SCENARIO.read_text()) if SCENARIO.is_file() else None
    if base is None:
        pytest.skip("MnDOT scenario absent")
    a = ScenarioConfig.model_validate(base)
    base["network"]["patch_files"] = []
    b = ScenarioConfig.model_validate(base)
    assert config_hash(a) != config_hash(b)
