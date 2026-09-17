"""The entry lane distribution in flow units (docs/MERGE_ROUND6_PLAN.md §2.1).

``OSMNetwork.entry_lane_shares`` is drawn once per inserted vehicle, so SUMO
applies it as a share of **flow**. ``--entry-lanes observed`` measures a share
of **vehicle-time** (5 Hz samples per lane), which over-represents slow lanes.
``--entry-lanes observed_flow`` and the ``_entryflow`` merge variant use the
crossing-count estimator instead; ``artifacts/i24_lane_profile.json`` carries
both, and the ``observed`` path must keep producing exactly what the committed
zip-family scenarios were built with.

Data-only and fast: no SUMO, no simulation. The one test that reads the
recording is skipped when the processed day is not on the machine.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
LANE_PROFILE = REPO / "artifacts" / "i24_lane_profile.json"
ZIP_SCENARIO = REPO / "scenarios" / "i24_replica_zip.yaml"
WB_DIR = REPO / "data" / "i24motion" / "processed" / "i24_wb_20221130"


def _load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry_row() -> dict:
    prof = json.loads(LANE_PROFILE.read_text())["observed"]
    return next(r for r in prof["rows"] if r["x_lo_m"] == 0)


# --------------------------------------------------------------------------
# The estimator, on a synthetic frame
# --------------------------------------------------------------------------


def _synthetic() -> pd.DataFrame:
    """Two lanes past a section at x = 100 m, sampled at 5 Hz as the recording is.

    Lane 1 crawls: 2 vehicles at 5 m/s, 100 s of samples each → 1,000 samples.
    Lane 2 runs: 8 vehicles at 25 m/s, 20 s each → 800 samples. Every vehicle
    of both lanes crosses the section exactly once. So lane 1 holds the larger
    share of vehicle-time (1000/1800 = 0.56) and the smaller share of flow
    (2/10 = 0.20) — the bias the flow estimator removes.
    """
    rows = []
    for lane, n_veh, v, dur in ((1, 2, 5.0, 100.0), (2, 8, 25.0, 20.0)):
        for k in range(n_veh):
            n = int(dur / 0.2)
            x0 = 100.0 - v * dur / 2.0  # centred on the section, so each crosses once
            for i in range(n):
                rows.append(
                    {
                        "t": 1000.0 * k + i * 0.2,
                        "veh_id": f"L{lane}V{k}",
                        "x": x0 + v * i * 0.2,
                        "lane": lane,
                        "v": v,
                    }
                )
    return pd.DataFrame(rows)


def test_flow_share_is_not_the_time_share_of_a_slow_lane() -> None:
    build = _load("i24_build_replica")
    df = _synthetic()
    counts = build.first_crossing_lane_counts(df, [100.0], (1, 2))[100.0]
    assert counts == {1: 2, 2: 8}

    n_samples = df["lane"].value_counts()
    time_share = n_samples[1] / len(df)
    flow_share = counts[1] / sum(counts.values())
    assert time_share == pytest.approx(1000 / 1800, abs=1e-6)
    assert flow_share == pytest.approx(0.2, abs=1e-9)
    # the whole point: many samples, few vehicles → a smaller share of flow
    assert flow_share < time_share


def test_each_vehicle_counted_once_in_the_lane_of_its_first_crossing() -> None:
    build = _load("i24_build_replica")
    # one vehicle crosses x = 100 in lane 1, falls back, crosses again in lane 2
    df = pd.DataFrame(
        {
            "t": [0.0, 0.2, 0.4, 0.6, 0.8],
            "veh_id": ["a"] * 5,
            "x": [95.0, 105.0, 95.0, 105.0, 115.0],
            "lane": [1, 1, 2, 2, 2],
            "v": [10.0] * 5,
        }
    )
    assert build.first_crossing_lane_counts(df, [100.0], (1, 2))[100.0] == {1: 1, 2: 0}


def test_sections_are_independent_and_missing_lanes_report_zero() -> None:
    build = _load("i24_build_replica")
    df = pd.DataFrame(
        {
            "t": [0.0, 0.2, 0.4],
            "veh_id": ["a"] * 3,
            "x": [0.0, 120.0, 260.0],  # one sample-to-sample jump spans x = 100 and 250
            "lane": [3, 3, 3],
            "v": [100.0] * 3,
        }
    )
    got = build.first_crossing_lane_counts(df, [100.0, 250.0, 500.0], (1, 3))
    assert got[100.0] == {1: 0, 3: 1}
    assert got[250.0] == {1: 0, 3: 1}
    assert got[500.0] == {1: 0, 3: 0}


# --------------------------------------------------------------------------
# The artifact and the two boundary estimators
# --------------------------------------------------------------------------


def test_observed_entry_lane_shares_are_unchanged() -> None:
    """``--entry-lanes observed`` keeps the vehicle-time shares it has always had."""
    merge = _load("i24_merge_experiment")
    row = _entry_row()
    raw = [float(row["share"][str(lane)]) for lane in (1, 2, 3, 4)]
    expected = [round(v / sum(raw), 4) for v in raw]
    assert merge.observed_entry_lane_shares() == expected
    # and that is what the committed zip-family scenarios were built with
    built = yaml.safe_load(ZIP_SCENARIO.read_text())["network"]["entry_lane_shares"]
    assert built == expected == [0.3498, 0.2623, 0.2196, 0.1683]


def test_entry_flow_shares_come_from_the_artifact_counts() -> None:
    merge = _load("i24_merge_experiment")
    row = _entry_row()
    counts = [float(row["n_vehicles"][str(lane)]) for lane in (1, 2, 3, 4)]
    assert merge.observed_entry_lane_flow_shares() == [round(v / sum(counts), 4) for v in counts]
    # the right lane is fast at the entry, so flow moves share into it
    assert merge.observed_entry_lane_flow_shares()[3] > merge.observed_entry_lane_shares()[3] + 0.02


def test_entryflow_variant_sets_the_flow_shares() -> None:
    merge = _load("i24_merge_experiment")
    flow = merge.observed_entry_lane_flow_shares()
    cfg = merge.variant_config("geometry_corrected_ramplc1_entryflow")
    assert cfg["network"]["entry_lane_shares"] == flow
    assert cfg["name"] == "i24_merge_geometry_corrected_ramplc1_entryflow"
    # the reference variant is untouched
    ref = merge.variant_config("geometry_corrected_ramplc1_entrylanes")
    assert ref["network"]["entry_lane_shares"] == merge.observed_entry_lane_shares()
    assert ref["name"] == "i24_merge_geometry_corrected_ramplc1_entrylanes"
    # and both carry the same everything else
    assert {k: v for k, v in ref["network"].items() if k != "entry_lane_shares"} == {
        k: v for k, v in cfg["network"].items() if k != "entry_lane_shares"
    }


def test_entryflow_composes_with_heavy() -> None:
    merge = _load("i24_merge_experiment")
    cfg = merge.variant_config("geometry_corrected_ramplc1_entryflow_heavy")
    assert cfg["network"]["entry_lane_shares"] == merge.observed_entry_lane_flow_shares()
    assert cfg["fleet"]["heavy"]["idm_calibration"] == "artifacts/idm_i24_heavy.json"
    assert cfg["name"] == "i24_merge_geometry_corrected_ramplc1_entryflow_heavy"


@pytest.mark.parametrize(
    "name",
    [
        "geometry_corrected_ramplc1_entrylanes_entryflow",
        "geometry_corrected_ramplc1_entryflow_entrylanes",
    ],
)
def test_entrylanes_and_entryflow_are_mutually_exclusive(name: str) -> None:
    merge = _load("i24_merge_experiment")
    with pytest.raises(ValueError, match="mutually exclusive"):
        merge.variant_config(name)


@pytest.mark.skipif(
    not (WB_DIR / "trajectories.parquet").is_file(), reason="processed I-24 day absent"
)
def test_builder_and_artifact_agree_on_the_flow_shares() -> None:
    """``--entry-lanes observed_flow`` and the artifact row must be the same number."""
    build = _load("i24_build_replica")
    merge = _load("i24_merge_experiment")
    shares, counts = build.entry_lane_flow_shares(build.T_STUDY_LO_S, build.T_STUDY_HI_S)
    row = _entry_row()
    assert counts == [int(row["n_vehicles"][str(lane)]) for lane in (1, 2, 3, 4)]
    assert shares == merge.observed_entry_lane_flow_shares()
