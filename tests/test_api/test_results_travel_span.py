"""Travel-time metrics served by the API are fleet metrics, not one vehicle's.

``api.results.replicate_metrics`` used to call ``compute_metrics`` with no
span, so the measurement span came from the observed position extremes: the
exit bound is reached by the single farthest-travelled vehicle alone, and the
entry bound sits inside the corridor's synthetic insertion buffer. Every micro
run the product served therefore reported one vehicle's trip as
``mean_tt_s`` — with ``p90_tt_s`` identical to it, since a percentile of one
sample is that sample — and the report's 95% CI was the seed-to-seed spread of
that one lead vehicle. ``mean_tt_s``/``p90_tt_s`` are CLAUDE.md §7.3 headline
metrics.

The span now comes from the scenario geometry (``api.results.analysis_span``):
the corridor proper, entry buffer excluded and a margin kept clear of the
downstream end so vehicles actually cross the exit bound. Ring runs have no
such span at all — ``x`` wraps around the loop — so their travel times are
reported absent rather than invented.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from api import results as res
from validation.metrics import compute_metrics

_DT = 1.0


def _corridor_meta(length_m: float, exit_buffer_m: float | None = None) -> dict[str, Any]:
    network: dict[str, Any] = {"kind": "corridor", "length_m": length_m, "lanes": 1}
    if exit_buffer_m is not None:
        network["boundary"] = {"exit_buffer_m": exit_buffer_m}
    return {
        "tier": "micro",
        "seed": 7,
        "config_hash": "0123456789ab",
        "config": {"network": network, "sim": {"warmup_s": 0.0}},
    }


def _write_replicate(directory: Path, meta: dict[str, Any], trajectories: pd.DataFrame) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(json.dumps(meta))
    trajectories.to_parquet(directory / "trajectories.parquet", index=False)
    return directory


def _corridor_trajectories(n_veh: int, x_end_far: float, x_end_rest: float) -> pd.DataFrame:
    """One fast lead vehicle that runs to ``x_end_far``, the rest stop short.

    All vehicles are inserted at a *different* position inside the upstream
    buffer, exactly as ``departPos="free"`` does, so the smallest observed
    ``x`` is one vehicle's insertion point.
    """
    rows: list[dict[str, float | str]] = []
    for i in range(n_veh):
        x0 = 20.0 * i  # somewhere in the insertion buffer
        x_end = x_end_far if i == 0 else x_end_rest
        speed = 20.0 + 0.5 * i
        t = 0.0
        x = x0
        while x <= x_end:
            rows.append({"t": t, "veh_id": f"v{i}", "x": x, "v": speed})
            t += _DT
            x += speed * _DT
    return pd.DataFrame(rows)


def test_analysis_span_is_the_corridor_proper(tmp_path: Path) -> None:
    span = res.analysis_span(_corridor_meta(10_000.0))
    assert span == (2000.0, 2000.0 + 10_000.0 - res.CORRIDOR_EXIT_MARGIN_M)

    # The buffer is capped at the corridor length (microsim.runner).
    short = res.analysis_span(_corridor_meta(1000.0))
    assert short == (1000.0, 1000.0 + 1000.0 - res.CORRIDOR_EXIT_MARGIN_M)

    # With an exit buffer the vehicles run past the corridor end, so the span
    # can use the whole corridor.
    assert res.analysis_span(_corridor_meta(1000.0, exit_buffer_m=300.0)) == (1000.0, 2000.0)


@pytest.mark.parametrize(
    "meta",
    [
        {"config": {"network": {"kind": "ring", "circumference_m": 230.0}}},
        {"config": {"network": {"kind": "osm", "corridor_edges": ["e1"]}}},
        {"config": {"network": {"kind": "corridor", "length_m": 0.0}}},
        {"config": {"network": {"kind": "corridor", "length_m": "1000"}}},
        {"config": {"network": {"kind": "corridor"}}},
        {"config": {}},
        {},
    ],
    ids=["ring", "osm", "zero-length", "text-length", "no-length", "no-network", "no-config"],
)
def test_no_geometric_span_for_non_corridor_networks(meta: dict[str, Any]) -> None:
    assert res.analysis_span(meta) is None


def test_corridor_travel_times_are_a_fleet_metric_not_the_lead_vehicle(tmp_path: Path) -> None:
    """The regression this exists for: one completing vehicle, mean == p90."""
    meta = _corridor_meta(1000.0)
    traj = _corridor_trajectories(n_veh=8, x_end_far=2400.0, x_end_rest=1960.0)
    rep = _write_replicate(tmp_path / "hash" / "7", meta, traj)

    # What the product used to serve: the raw observed extremes.
    raw_span = (float(traj["x"].min()), float(traj["x"].max()))
    lead_only = compute_metrics(rep, span=raw_span)
    assert lead_only.n_travel_time_veh == 1
    assert lead_only.mean_tt_s != lead_only.mean_tt_s  # NaN: one sample is not a mean

    metrics = res.replicate_metrics(rep)
    assert res.analysis_span(meta) == (1000.0, 1900.0)
    assert metrics.n_travel_time_veh == 8  # every vehicle crosses the corridor proper
    assert math.isfinite(metrics.mean_tt_s) and math.isfinite(metrics.p90_tt_s)
    assert metrics.p90_tt_s > metrics.mean_tt_s  # a real percentile, not the mean again

    # Entry is the span entry, not the insertion time: a vehicle at 20 m/s
    # covers the 900 m span in 45 s, whichever buffer position it started at.
    assert metrics.mean_tt_s < 45.1


def test_ring_travel_times_are_reported_absent(tmp_path: Path) -> None:
    """``x`` wraps on a ring, so a span crossing is not a corridor traversal."""
    circumference = 230.0
    rows = []
    for i in range(6):
        x = 10.0 * i
        for step in range(120):
            rows.append({"t": step * _DT, "veh_id": f"v{i}", "x": x % circumference, "v": 8.0})
            x += 8.0 * _DT
    meta: dict[str, Any] = {
        "tier": "micro",
        "seed": 3,
        "config_hash": "ringringring",
        "config": {
            "network": {"kind": "ring", "circumference_m": circumference},
            "sim": {"warmup_s": 0.0},
        },
    }
    rep = _write_replicate(tmp_path / "ring" / "3", meta, pd.DataFrame(rows))

    metrics = res.replicate_metrics(rep)
    assert np.isnan(metrics.mean_tt_s) and np.isnan(metrics.p90_tt_s)
    assert metrics.n_travel_time_veh == 0
    # The metrics that do mean something on a ring are still there.
    assert math.isfinite(metrics.sigma_v_spatial_ms)


def test_cached_metrics_from_an_older_schema_are_recomputed(tmp_path: Path) -> None:
    """The schema bump is what stops the one-vehicle numbers being served on."""
    meta = _corridor_meta(1000.0)
    traj = _corridor_trajectories(n_veh=8, x_end_far=2400.0, x_end_rest=1960.0)
    rep = _write_replicate(tmp_path / "hash" / "7", meta, traj)
    stale = {
        "schema": res._METRICS_CACHE_SCHEMA - 1,
        "metrics": {"mean_tt_s": 999.0, "p90_tt_s": 999.0},
    }
    (rep / res.METRICS_CACHE_NAME).write_text(json.dumps(stale))

    # The bump itself is load-bearing: caches written before the span fix hold
    # the one-vehicle numbers under schema 1, so the constant must have moved
    # past it or every already-run replicate would serve them for ever.
    assert res._METRICS_CACHE_SCHEMA >= 2

    assert res.cached_replicate_metrics(rep) is None  # foreign schema: absent
    metrics = res.replicate_metrics(rep)
    assert metrics.mean_tt_s != 999.0
    assert json.loads((rep / res.METRICS_CACHE_NAME).read_text())["schema"] == (
        res._METRICS_CACHE_SCHEMA
    )
