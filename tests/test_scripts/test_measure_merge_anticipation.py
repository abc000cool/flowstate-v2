"""scripts/measure_merge_anticipation.py end to end on a tiny synthetic processed table (M1).

The I-24 MOTION table is never read here: a few thousand rows laid out as
``convert_i24_to_parquet`` writes them (``t, veh_id, x, lane, v, length`` on
the 0.2 s grid, data x on the westbound axis), with one planted entrant in
the Hickory Hollow-Bell Road weave and one in the Old Hickory acceleration
lane (the zones come from the committed ``artifacts/i24_replica_inputs.json``),
run through the driver's chunk loop into a temporary artifact.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_measure_merge_anticipation", SCRIPTS / "measure_merge_anticipation.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mma = _load()

DT = 0.2
T = np.round(np.arange(0, round(60.0 / DT) + 1) * DT, 6)


def _veh(vid: str, x: np.ndarray, lane: np.ndarray | int, v: np.ndarray | float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": T,
            "veh_id": vid,
            "x": x,
            "lane": np.broadcast_to(np.asarray(lane, dtype=np.int64), T.shape).copy(),
            "v": np.broadcast_to(np.asarray(v, dtype=float), T.shape).copy(),
            "length": 5.0,
        }
    )


def _world(tag: str, x_change: float, speed: float, dv: float) -> list[pd.DataFrame]:
    """A lane-4 platoon at ``speed`` and an entrant changing 5 -> 4 at t = 40 s at ``x_change``."""
    t_on, t_change, x_mid, gap = 30.0, 40.0, 31.3, 60.0
    xf0 = x_change - x_mid - speed * t_change
    frames = [_veh(f"{tag}m{i}", xf0 - i * gap + speed * T, 4, speed) for i in range(-3, 5)]
    x_on = xf0 + speed * t_on + x_mid
    x = np.where(T < t_on, x_on + (speed + dv) * (T - t_on), x_on + speed * (T - t_on))
    v = np.where(T < t_on, speed + dv, speed)
    frames.append(_veh(f"{tag}c", x, np.where(T < t_change, 5, 4), v))
    return frames


def test_end_to_end_on_a_synthetic_table(tmp_path: Path) -> None:
    zones, _ = mma.i24_zones()
    by = {z.name: z for z in zones}
    weave, merge = by[mma.WEAVE_ZONE], by[mma.MERGE_ZONE]
    df = pd.concat(
        [
            *_world("w", weave.x_lo_m + 300.0, 24.0, 3.0),
            *_world("o", merge.x_lo_m + 600.0, 12.0, -2.0),
        ],
        ignore_index=True,
    )
    wb = tmp_path / "wb"
    wb.mkdir()
    df.to_parquet(wb / "trajectories.parquet", index=False)
    (wb / "meta.json").write_text(json.dumps({"data_hash": "synthetic"}))
    out = tmp_path / "anticipation.json"
    mma.main(["--wb-dir", str(wb), "--out", str(out), "--t-range", "0", "900", "--n-boot", "20"])
    art = json.loads(out.read_text())
    assert art["data_hash"] == "synthetic"
    assert len(art["table_sha256"]) == 64
    assert art["model_constant"]["value_m"] == 120.0
    assert art["counts"]["events"]["n_events"] == 2
    ev = art["events"]
    rows = [dict(zip(ev["columns"], r, strict=True)) for r in ev["rows"]]
    assert {r["zone"] for r in rows} == {mma.WEAVE_ZONE, mma.MERGE_ZONE}
    # the planted reaches: the weave entrant overtakes at +3 m/s into its gap (beside it from
    # 19.6 s), holds 24 m/s from 30 s and changes at 40 s; the acceleration-lane entrant falls
    # back at -2 m/s (beside its gap from 15.8 s) and its 2 m/s offset lies inside the ±2 band
    w = next(r for r in rows if r["zone"] == mma.WEAVE_ZONE)
    assert w["speed_class"] == "v>=20"
    assert (w["gap_outcome"], w["gap_reach_m"]) == ("onset", 520.8)
    assert (w["speed_1_outcome"], w["speed_1_reach_m"]) == ("onset", 240.0)
    o = next(r for r in rows if r["zone"] == mma.MERGE_ZONE)
    assert o["speed_class"] == "10<=v<20"
    assert (o["gap_outcome"], o["gap_reach_m"]) == ("onset", 262.0)
    assert (o["speed_1_outcome"], o["speed_1_reach_m"]) == ("onset", 120.0)
    assert o["speed_2_outcome"] == "window_start"
    # two events cannot pass the rule's 100-event floor: nothing is proposed
    prop = art["preregistered_proposal"]
    assert prop["selected"] is None and prop["proposed_value_m"] is None
    assert prop["current_value_m"] == 120.0
    assert [s["name"] for s in prop["strata"]] == ["primary", "fallback"]
    assert {r["definition"] for r in art["summary"]} == {"gap", "speed_1", "speed_2"}
    assert art["coverage"]["by_zone"] and art["change_position_m"]
    assert art["coverage"]["coverage_context"]["windows"][0]["n_events"] == 2
    assert {s["sensitivity"] for s in art["sensitivities"]} >= {
        "no_bridging",
        "change_at_least_200m_past_zone_start",
        "all_speed_classes",
    }


def test_window_label() -> None:
    assert mma.window_label(0.0) == "06:00"
    assert mma.window_label(899.9) == "06:00"
    assert mma.window_label(5400.0) == "07:30"
