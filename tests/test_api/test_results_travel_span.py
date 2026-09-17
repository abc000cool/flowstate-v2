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
    ids=[
        "ring",
        "osm-run-without-the-geometry-block",
        "zero-length",
        "text-length",
        "no-length",
        "no-network",
        "no-config",
    ],
)
def test_no_geometric_span_for_non_corridor_networks(meta: dict[str, Any]) -> None:
    assert res.analysis_span(meta) is None


def _osm_meta(
    total_length_m: float | None = 2000.0,
    x_first_edge_m: float | None = 0.0,
    exit_buffer_m: float | None = None,
    corridor: dict[str, Any] | str | None = "default",
) -> dict[str, Any]:
    """An OSM replicate's meta, as ``microsim.runner`` writes it."""
    meta: dict[str, Any] = {
        "tier": "micro",
        "seed": 7,
        "config_hash": "0123456789ab",
        "config": {
            "network": {"kind": "osm", "corridor_edges": ["100", "101"]},
            "sim": {"warmup_s": 0.0},
        },
    }
    if corridor == "default":
        corridor = {
            "kind": "osm",
            "total_length_m": total_length_m,
            "x_first_edge_m": x_first_edge_m,
        }
    if corridor is not None:
        meta["corridor"] = corridor
    if exit_buffer_m is not None:
        meta["boundary"] = {"kind": "speed_schedule", "exit_buffer_m": exit_buffer_m}
    return meta


def test_osm_span_comes_from_the_recorded_geometry() -> None:
    """The regression: OSM replicates each measured over their own extent.

    The I-24 flagship is an OSM corridor, and ``compute_metrics``' own
    default span runs to the *median* per-vehicle furthest position — which
    shrinks when a replicate congests, so a congested controlled cell could
    report a lower travel time than a free-flowing sibling and the run's CI
    was a measurement artifact rather than seed-to-seed spread.
    """
    # No boundary schedule: a margin is kept clear of the network end so
    # vehicles actually cross the exit bound (same reason as a corridor's).
    assert res.analysis_span(_osm_meta()) == (0.0, 2000.0 - res.CORRIDOR_EXIT_MARGIN_M)

    # With the boundary on the last edge (docs/CONTRACTS.md §2), the corridor
    # proper ends where that edge starts and the margin is the edge itself.
    assert res.analysis_span(_osm_meta(exit_buffer_m=250.0)) == (0.0, 1750.0)

    # A chain whose first corridor edge is not the linear-x origin starts there.
    assert res.analysis_span(_osm_meta(x_first_edge_m=300.0)) == (300.0, 1900.0)


@pytest.mark.parametrize(
    "meta",
    [
        _osm_meta(corridor=None),
        _osm_meta(corridor={"kind": "osm", "total_length_m": 2000.0}),
        _osm_meta(corridor={"kind": "osm", "x_first_edge_m": 0.0}),
        _osm_meta(total_length_m=0.0),
        _osm_meta(total_length_m="2000"),  # type: ignore[arg-type]
        _osm_meta(x_first_edge_m=-5.0),
        _osm_meta(total_length_m=60.0),  # shorter than the exit margin
        _osm_meta(total_length_m=500.0, x_first_edge_m=500.0, exit_buffer_m=100.0),
    ],
    ids=[
        "no-block-older-run",
        "no-x-first-edge",
        "no-total-length",
        "zero-length",
        "text-length",
        "negative-offset",
        "shorter-than-the-margin",
        "empty-span",
    ],
)
def test_an_unusable_osm_geometry_block_yields_no_span(meta: dict[str, Any]) -> None:
    """Never a guessed span: ``None`` leaves ``compute_metrics`` its default."""
    assert res.analysis_span(meta) is None


def _uniform_trajectories(n_veh: int, speed_ms: float, x_end: float) -> pd.DataFrame:
    """``n_veh`` vehicles driving at one speed from x = 0 to ``x_end``."""
    rows: list[dict[str, float | str]] = []
    for i in range(n_veh):
        t, x = 2.0 * i, 0.0
        while x <= x_end:
            rows.append({"t": t, "veh_id": f"v{i}", "x": x, "v": speed_ms})
            t += _DT
            x += speed_ms * _DT
    return pd.DataFrame(rows)


def test_a_congested_osm_replicate_no_longer_reports_the_faster_trip(tmp_path: Path) -> None:
    """The regression, end to end on two replicates of one OSM geometry.

    Replicate 1 is free-flowing and covers the chain; replicate 2 crawls and
    gets 500 m in. Measured over their own observed extents — what the OSM
    path did, because ``analysis_span`` returned ``None`` — the *congested*
    replicate reports the *lower* travel time, because its span shrank with
    it; those two numbers then became a mean ± 95% CI.
    """
    free = _write_replicate(
        tmp_path / "osmhash" / "1",
        {**_osm_meta(), "seed": 1},
        _uniform_trajectories(6, speed_ms=30.0, x_end=1980.0),
    )
    jammed = _write_replicate(
        tmp_path / "osmhash" / "2",
        {**_osm_meta(), "seed": 2},
        _uniform_trajectories(6, speed_ms=10.0, x_end=500.0),
    )

    # What the product used to serve: each replicate's own extent.
    own_extent_free = compute_metrics(free, span=None)
    own_extent_jammed = compute_metrics(jammed, span=None)
    assert own_extent_jammed.mean_tt_s < own_extent_free.mean_tt_s  # the artifact

    # One span for both, from the geometry meta.json records.
    span = (0.0, 2000.0 - res.CORRIDOR_EXIT_MARGIN_M)
    assert res.analysis_span(res.load_meta(free)) == span
    assert res.analysis_span(res.load_meta(jammed)) == span

    metrics_free = res.replicate_metrics(free)
    metrics_jammed = res.replicate_metrics(jammed)
    assert metrics_free.n_travel_time_veh == 6
    assert metrics_free.mean_tt_s == pytest.approx(1900.0 / 30.0, rel=0.02)
    # The crawling replicate never reaches the exit bound: absent, not faster.
    assert metrics_jammed.n_travel_time_veh == 0
    assert math.isnan(metrics_jammed.mean_tt_s)


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
    # past it or every already-run replicate would serve them for ever. Schema
    # 3 is the same argument for OSM runs, whose caches were written from each
    # replicate's own observed extent.
    assert res._METRICS_CACHE_SCHEMA >= 3

    assert res.cached_replicate_metrics(rep) is None  # foreign schema: absent
    metrics = res.replicate_metrics(rep)
    assert metrics.mean_tt_s != 999.0
    assert json.loads((rep / res.METRICS_CACHE_NAME).read_text())["schema"] == (
        res._METRICS_CACHE_SCHEMA
    )


def test_an_osm_cache_written_before_the_span_fix_is_recomputed(tmp_path: Path) -> None:
    """Schema 2 caches hold the per-replicate-extent travel times for OSM runs.

    Every I-24 replicate already on disk has one; without the bump the API
    would serve those numbers for ever, since the artifacts they were
    computed from have not changed.
    """
    rep = _write_replicate(
        tmp_path / "osmhash" / "1",
        _osm_meta(),
        _uniform_trajectories(6, speed_ms=30.0, x_end=1980.0),
    )
    (rep / res.METRICS_CACHE_NAME).write_text(
        json.dumps({"schema": 2, "metrics": {"mean_tt_s": 999.0, "p90_tt_s": 999.0}})
    )
    assert res.cached_replicate_metrics(rep) is None
    assert res.replicate_metrics(rep).mean_tt_s == pytest.approx(1900.0 / 30.0, rel=0.02)


def test_report_span_is_one_span_for_an_all_osm_run_set(tmp_path: Path) -> None:
    """``api.jobs._report_span``: a report's groups share one measured distance.

    The span comes from a replicate's ``meta.json``, not from the run row's
    config — an OSM config carries no length at all, so the config-only read
    returned ``None`` for the flagship corridor and the report fell back to
    the reference group's observed extent.
    """
    from api.jobs import _report_span

    runs = []
    for i, x_end in enumerate((1980.0, 1900.0), start=1):
        run_root = tmp_path / f"run_{i}"
        meta = _osm_meta()
        _write_replicate(run_root / "osmhash" / str(i), meta, _uniform_trajectories(4, 30.0, x_end))
        runs.append({"id": f"run_{i}", "run_root": str(run_root), "config": meta["config"]})

    assert _report_span(runs) == (0.0, 2000.0 - res.CORRIDOR_EXIT_MARGIN_M)

    # A run whose replicates are a different geometry is not folded in.
    odd_root = tmp_path / "run_odd"
    _write_replicate(
        odd_root / "osmhash" / "9",
        _osm_meta(total_length_m=5000.0),
        _uniform_trajectories(2, 30.0, 900.0),
    )
    runs.append({"id": "run_odd", "run_root": str(odd_root), "config": _osm_meta()["config"]})
    assert _report_span(runs) is None

    # A run with no artifacts on disk falls back to the config (no span here),
    # rather than raising inside the report job.
    assert _report_span([{"id": "gone", "run_root": str(tmp_path / "nope"), "config": {}}]) is None
