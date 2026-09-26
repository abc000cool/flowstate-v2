"""The pure helpers of scripts/lane_change_relaxation.py (WP-88): the restoration of SUMO's
arrival-step crossings (WP-82's fix), the US-101 weaving zone, the walk mask, the weave on-ramp
reading of a run's meta and the compact JSON writer. Synthetic inputs only."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_gaps import lane_change_gaps

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_lane_change_relaxation", SCRIPTS / "lane_change_relaxation.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lcr = _load()

RAMP = {"name": "th52", "x_lo_m": 800.0, "x_hi_m": 1100.0, "aux_band": 4}
DT = 0.5


def _veh(vid: str, t0: float, n: int, x0: float, v: float, lanes: list[int]) -> pd.DataFrame:
    t = t0 + DT * np.arange(n)
    return pd.DataFrame(
        {
            "t": t,
            "veh_id": vid,
            "x": x0 + v * (t - t0),
            "lane": np.asarray(lanes, dtype=np.int64),
            "v": v,
            "length": 5.0,
        }
    )


def _first(df: pd.DataFrame, origins: dict[str, str]) -> pd.DataFrame:
    first = df.sort_values(["veh_id", "t"]).groupby("veh_id", sort=False).head(1)
    return first.assign(origin=first["veh_id"].map(origins))


class TestArrivalCrossings:
    def test_restores_the_change_sumo_made_on_arrival(self) -> None:
        # a: from the ramp, first sample in band 3 inside the section (SUMO moved it on arrival)
        # b: from the ramp, first sample on the auxiliary band (its change is in the table)
        # c: from the mainline in band 3 (not an entrant)
        # d: from the ramp, first sample in band 3 but upstream of the section (not this ramp's join)
        df = pd.concat(
            [
                _veh("a", 10.0, 20, 805.0, 8.0, [3] * 20),
                _veh("b", 10.0, 20, 802.0, 8.0, [4] * 6 + [3] * 14),
                _veh("c", 10.0, 20, 700.0, 20.0, [3] * 20),
                _veh("d", 10.0, 20, 600.0, 8.0, [3] * 20),
            ],
            ignore_index=True,
        )
        origins = {"a": "th52", "b": "th52", "c": "mainline", "d": "th52"}
        aug, keys = lcr.add_arrival_crossings(df, _first(df, origins), [RAMP], dt_s=DT)
        assert keys == {("a", 10.0)}
        added = aug.iloc[len(df) :]
        assert len(added) == 1
        row = added.iloc[0]
        assert (row["veh_id"], row["t"], row["lane"]) == ("a", 9.5, 4)
        assert row["x"] == pytest.approx(805.0 - 8.0 * DT)
        rec = lane_change_gaps(
            aug, (), mainline_lanes=(1, 2, 3), aux_lanes=(4,), dt_s=DT, default_length_m=5.0
        ).records
        a = rec[rec["veh_id"] == "a"].iloc[0]
        assert (a["t"], a["from_lane"], a["to_lane"]) == (10.0, 4, 3)
        # a one-sample run at the track's start is unconfirmed; the script confirms it
        assert not bool(a["confirmed"])
        assert set(rec["veh_id"]) == {"a", "b"}

    def test_no_ramp_no_change(self) -> None:
        df = _veh("a", 0.0, 5, 805.0, 8.0, [3] * 5)
        aug, keys = lcr.add_arrival_crossings(df, _first(df, {"a": "other"}), [RAMP], dt_s=DT)
        assert keys == set() and aug is df


def test_weave_on_ramps_reads_merge_weave_only() -> None:
    meta = {
        "config": {"network": {"ramps": [{"merge": "weave"}, {}, {"merge": "lane_change"}]}},
        "ramps": [
            {"index": 0, "name": "th52", "kind": "on", "attach_edge": "102", "attach_x_m": 829.07, "attach_end_x_m": 1134.09},
            {"index": 1, "name": "th52 exit", "kind": "off", "attach_edge": "102", "attach_x_m": 829.07, "attach_end_x_m": 1134.09},
            {"index": 2, "name": "other", "kind": "on", "attach_edge": "103", "attach_x_m": 1134.09, "attach_end_x_m": 1355.0},
        ],
    }  # fmt: skip
    prov = {"edges": ["100", "101", "102", "103"], "edge_lanes": [3, 3, 4, 3]}
    ramps = lcr._weave_on_ramps(meta, prov)
    assert ramps == [{"name": "th52", "x_lo_m": 829.07, "x_hi_m": 1134.09, "aux_band": 4}]


def test_us101_zone_is_the_span_of_lane_6() -> None:
    x = np.linspace(100.0, 500.0, 10001)
    zone = lcr.us101_weave_zone(np.append(x, [np.nan, 5000.0]))
    assert zone.kind == "weave"
    finite = np.append(x, 5000.0)
    assert zone.x_lo_m == pytest.approx(np.quantile(finite, 0.005))
    assert zone.x_hi_m == pytest.approx(np.quantile(finite, 0.995))
    # the one stray sample at 5 km lies beyond the 99.5th percentile
    assert zone.x_hi_m < 500.0
    with pytest.raises(ValueError):
        lcr.us101_weave_zone(np.array([np.nan]))


def test_walk_mask() -> None:
    rec = pd.DataFrame(
        {
            "confirmed": [True, True, False, True, True],
            "suspect": [False, False, False, True, False],
            "movement": ["entering", "unknown", "exiting", "through", "through"],
        }
    )
    np.testing.assert_array_equal(lcr.walk_mask(rec), [True, False, False, False, True])
    assert lcr.walk_mask(rec.iloc[0:0]).size == 0


def test_compact_json_round_trips_and_refuses_nan() -> None:
    art = {"a": [1.0, None, 2.5], "b": {"c": [[1, 2], [3]], "d": []}, "e": "x", "f": {}}
    text = lcr.dumps_compact(art)
    assert json.loads(text) == art
    assert '"a": [1.0, null, 2.5]' in text
    with pytest.raises(ValueError):
        lcr.dumps_compact({"a": [math.nan]})
