"""Read-side helpers over run artifacts: metrics, heatmaps, staging.

Everything here is deterministic post-processing of the contract run layout
(docs/CONTRACTS.md §3): ``<run_root>/<config_hash>/<seed>/`` with
``edges.parquet`` (both tiers), ``trajectories.parquet`` + ``meta.json``
(micro tier), ``meta.json`` (macro tier, ``tier="screening"``).

Micro-tier metrics come straight from :func:`validation.metrics.compute_metrics`.
Macro-tier (screening) replicates have no per-vehicle trajectories, so a
reduced :class:`validation.metrics.Metrics` is computed from the binned
``edges.parquet`` fields instead; metrics that need vehicle identity (travel
times, fuel) are reported ``NaN`` — honestly absent, never fabricated
(CLAUDE.md §0.1). Screening metrics support screening comparisons only, never
validation claims (CLAUDE.md §5.6).

Per-replicate metrics are cached beside the artifacts as ``metrics.json``
(the cache file is additive; it does not alter the contract layout).
"""

from __future__ import annotations

import dataclasses
import io
import json
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from flowstate_core.units import ms_to_kmh, s_to_h, veh_s_to_veh_h
from validation.fields import SpeedField
from validation.metrics import CI, Metrics, aggregate, compute_metrics
from validation.waves import detect_waves

HeatmapField = Literal["speed", "density"]

_FIELD_COLUMNS: dict[str, str] = {"speed": "mean_speed", "density": "density"}

_METRICS_CACHE_SCHEMA = 1

#: km per m derived from the unit helpers (no inline magic conversions,
#: CLAUDE.md §2): 1 m = 1 m/s sustained for 1 s = ms_to_kmh(1) km/h · s_to_h(1) h.
_KM_PER_M = ms_to_kmh(1.0) * s_to_h(1.0)


def replicate_dirs(run_root: str | Path) -> list[Path]:
    """Replicate directories under a run root (``<hash>/<seed>/`` layout)."""
    root = Path(run_root)
    return sorted(p.parent for p in root.glob("*/*/meta.json"))


def load_meta(replicate_dir: Path) -> dict[str, Any]:
    """Parse one replicate's ``meta.json``."""
    return json.loads((replicate_dir / "meta.json").read_text())  # type: ignore[no-any-return]


def _centers_to_edges(centers: np.ndarray) -> np.ndarray:
    """Bin edges from (possibly non-uniform) bin centers via midpoints."""
    c = np.asarray(centers, dtype=np.float64)
    if c.size == 1:
        return np.array([c[0] - 0.5, c[0] + 0.5])
    mid = 0.5 * (c[:-1] + c[1:])
    first = c[0] - (mid[0] - c[0])
    last = c[-1] + (c[-1] - mid[-1])
    return np.concatenate([[first], mid, [last]])


def _pivot(edges: pd.DataFrame, column: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pivot the edges frame to ``(t_centers, x_centers, values[nt, nx])``."""
    # dropna=False keeps never-visited bins (all-NaN columns) on the grid so
    # the axes always span the full recorded space-time extent.
    pivot = edges.pivot_table(
        index="t_bin", columns="x_bin", values=column, aggfunc="mean", dropna=False
    )
    t_centers = pivot.index.to_numpy(dtype=np.float64)
    x_centers = pivot.columns.to_numpy(dtype=np.float64)
    return t_centers, x_centers, pivot.to_numpy(dtype=np.float64)


def _nan_mean_of_rowwise_std(matrix: np.ndarray, axis: int) -> float:
    """Mean over slices of the sample std (ddof=1) along ``axis``.

    Slices with fewer than two finite values are skipped; NaN when none
    qualify (mirrors the σ_v definitions in :class:`validation.metrics.Metrics`).
    """
    finite = np.isfinite(matrix)
    counts = finite.sum(axis=axis)
    ok = counts >= 2
    if not np.any(ok):
        return math.nan
    with np.errstate(invalid="ignore"):
        stds = np.nanstd(matrix, axis=axis, ddof=1)
    return float(np.mean(stds[ok]))


def macro_metrics(replicate_dir: Path) -> Metrics:
    """Screening-tier metrics from one macro replicate's ``edges.parquet``.

    Definitions (all from the binned Eulerian fields — see module docstring):

    - ``throughput_veh_h``: time-mean flow at the cell nearest the corridor
      midpoint, converted to veh/h.
    - ``sigma_v_spatial_ms``: per time sample, std (ddof=1) of cell speeds,
      averaged over samples; ``sigma_v_temporal_ms``: per cell, std over time,
      averaged over cells.
    - ``vmt_veh_km`` = Σ q·Δt·Δx (vehicle-distance), ``vht_veh_h`` = Σ ρ·Δt·Δx
      (vehicle-time) over the space-time grid.
    - Wave metrics via :func:`validation.waves.detect_waves` on the speed
      field (LWR/CTM is string-stable, so emergent waves cannot appear here —
      any detection reflects boundary/seeded structure, screening use only).
    - ``mean_tt_s``, ``p90_tt_s``, ``fuel_ml_per_veh_km``: NaN — the macro
      tier has no per-vehicle trajectories or emission model.
    """
    edges = pd.read_parquet(replicate_dir / "edges.parquet")
    t_centers, x_centers, speed = _pivot(edges, "mean_speed")
    _, _, density = _pivot(edges, "density")
    _, _, flow = _pivot(edges, "flow")

    dt = float(np.median(np.diff(t_centers))) if t_centers.size > 1 else math.nan
    dx = float(np.median(np.diff(x_centers))) if x_centers.size > 1 else math.nan

    mid_x = 0.5 * (float(x_centers[0]) + float(x_centers[-1]))
    ref_col = int(np.argmin(np.abs(x_centers - mid_x)))
    ref_flow = flow[:, ref_col]
    ref_flow = ref_flow[np.isfinite(ref_flow)]
    throughput = veh_s_to_veh_h(float(ref_flow.mean())) if ref_flow.size else math.nan

    sigma_spatial = _nan_mean_of_rowwise_std(speed, axis=1)
    sigma_temporal = _nan_mean_of_rowwise_std(speed, axis=0)

    if math.isfinite(dt) and math.isfinite(dx):
        vmt_km = _KM_PER_M * float(np.nansum(flow)) * dt * dx
        vht_h = s_to_h(float(np.nansum(density)) * dt * dx)
    else:
        vmt_km = math.nan
        vht_h = math.nan

    field = SpeedField(
        t_edges=_centers_to_edges(t_centers),
        x_edges=_centers_to_edges(x_centers),
        mean_speed=speed,
    )
    wave_set = detect_waves(field)
    backward = wave_set.backward()
    wave_speed_kmh = (
        float(np.mean([ms_to_kmh(-w.speed_ms) for w in backward])) if backward else math.nan
    )
    wave_amp = (
        float(np.mean([w.amplitude_ms for w in wave_set.waves])) if wave_set.count else math.nan
    )

    return Metrics(
        throughput_veh_h=throughput,
        mean_tt_s=math.nan,
        p90_tt_s=math.nan,
        sigma_v_spatial_ms=sigma_spatial,
        sigma_v_temporal_ms=sigma_temporal,
        vmt_veh_km=vmt_km,
        vht_veh_h=vht_h,
        fuel_ml_per_veh_km=math.nan,
        wave_count=wave_set.count,
        wave_speed_kmh=wave_speed_kmh,
        wave_amplitude_ms=wave_amp,
    )


def replicate_metrics(replicate_dir: Path) -> Metrics:
    """Metrics for one replicate, cached as ``metrics.json`` beside it.

    Micro replicates use the contract path
    (:func:`validation.metrics.compute_metrics`); macro replicates use
    :func:`macro_metrics`. The cache is keyed by a schema version and safely
    recomputed when unreadable.
    """
    cache_path = replicate_dir / "metrics.json"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("schema") == _METRICS_CACHE_SCHEMA:
                return Metrics(**cached["metrics"])
        except (ValueError, TypeError, KeyError):
            pass  # unreadable cache: recompute below
    meta = load_meta(replicate_dir)
    tier = str(meta.get("tier", ""))
    if tier == "micro":
        metrics = compute_metrics(replicate_dir)
    else:
        metrics = macro_metrics(replicate_dir)
    cache_path.write_text(
        json.dumps({"schema": _METRICS_CACHE_SCHEMA, "metrics": dataclasses.asdict(metrics)})
    )
    return metrics


def run_metrics(
    run_root: str | Path,
) -> tuple[list[tuple[int, Metrics]], dict[str, CI]]:
    """Per-replicate metrics (with seeds) plus the aggregate CIs for a run.

    Raises:
        FileNotFoundError: If the run root holds no completed replicates.
    """
    dirs = replicate_dirs(run_root)
    if not dirs:
        raise FileNotFoundError(f"no completed replicates under {run_root}")
    per_replicate: list[tuple[int, Metrics]] = []
    for d in dirs:
        meta = load_meta(d)
        per_replicate.append((int(meta.get("seed", -1)), replicate_metrics(d)))
    agg = aggregate([m for _, m in per_replicate])
    return per_replicate, agg


def heatmap_arrays(
    replicate_dir: Path, field: HeatmapField
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Binned space-time heatmap ``(t_centers, x_centers, values[nt, nx])``.

    Read from ``edges.parquet`` (both tiers share the schema, contract §3).
    """
    column = _FIELD_COLUMNS[field]
    edges = pd.read_parquet(replicate_dir / "edges.parquet")
    return _pivot(edges, column)


def heatmap_png(
    t_centers: np.ndarray,
    x_centers: np.ndarray,
    values: np.ndarray,
    *,
    field: HeatmapField,
    title: str,
) -> bytes:
    """Render a heatmap to PNG bytes (matplotlib Agg backend)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label = "mean speed [m/s]" if field == "speed" else "density [veh/m]"
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    mesh = ax.pcolormesh(
        _centers_to_edges(x_centers), _centers_to_edges(t_centers), values, shading="flat"
    )
    fig.colorbar(mesh, ax=ax, label=label)
    ax.set_xlabel("position x [m]")
    ax.set_ylabel("time t [s]")
    ax.set_title(title)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def finite_or_none(value: float) -> float | None:
    """Non-finite floats become None so responses stay strict JSON."""
    v = float(value)
    return v if math.isfinite(v) else None


def metrics_to_json(metrics: Metrics) -> dict[str, float | int | None]:
    """Serialize a Metrics dataclass with NaN/inf mapped to null."""
    out: dict[str, float | int | None] = {}
    for name, value in dataclasses.asdict(metrics).items():
        if isinstance(value, int):
            out[name] = value
        else:
            out[name] = finite_or_none(value)
    return out


def ci_to_json(ci: CI) -> dict[str, float | int | bool | None]:
    """Serialize a CI tuple with NaN mapped to null + the underpowered flag."""
    return {
        "mean": finite_or_none(ci.mean),
        "lo95": finite_or_none(ci.lo95),
        "hi95": finite_or_none(ci.hi95),
        "n": ci.n,
        "underpowered": ci.underpowered,
    }


def matrix_to_json(values: np.ndarray) -> list[list[float | None]]:
    """2-D array to nested lists with NaN mapped to null."""
    return [[finite_or_none(v) for v in row] for row in values.tolist()]
