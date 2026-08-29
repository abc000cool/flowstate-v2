"""Space-time traffic fields binned from vehicle trajectories.

Implements the binned space-time mean-speed field of docs/CONTRACTS.md §4 and
generalized (bin-area) definitions of density and flow after Edie (1963):
over a space-time region A with area |A| = Δt·Δx, density is the total time
vehicles spend inside A divided by |A|, and flow is the total distance they
travel inside A divided by |A|. These definitions are exact for continuous
trajectories; here they are approximated from discretely sampled trajectory
rows, each sample carrying its sampling interval as a time weight.

References:
    Edie, L. C. (1963). Discussion of traffic stream measurements and
    definitions. Proceedings of the 2nd International Symposium on the
    Theory of Traffic Flow, OECD, Paris, 139-154.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]

_REQUIRED_COLUMNS: tuple[str, ...] = ("t", "x", "v")


@dataclass
class SpeedField:
    """Binned space-time mean-speed field (docs/CONTRACTS.md §4).

    Attributes:
        t_edges: Time bin edges [s], shape ``[nt + 1]``, ascending.
        x_edges: Space bin edges [m], shape ``[nx + 1]``, ascending.
        mean_speed: Mean sampled speed per bin [m/s], shape ``[nt, nx]``;
            ``NaN`` where a bin contains no trajectory samples.
    """

    t_edges: FloatArray
    x_edges: FloatArray
    mean_speed: FloatArray


@dataclass
class DensityField:
    """Edie generalized density on a space-time grid.

    Attributes:
        t_edges: Time bin edges [s], shape ``[nt + 1]``.
        x_edges: Space bin edges [m], shape ``[nx + 1]``.
        density: Density per bin [veh/m]: total vehicle-time spent in the
            bin divided by the bin area Δt·Δx (Edie 1963). Zero (not NaN)
            where no vehicle was observed — an empty region genuinely has
            zero density under Edie's definition.
    """

    t_edges: FloatArray
    x_edges: FloatArray
    density: FloatArray


@dataclass
class FlowField:
    """Edie generalized flow on a space-time grid.

    Attributes:
        t_edges: Time bin edges [s], shape ``[nt + 1]``.
        x_edges: Space bin edges [m], shape ``[nx + 1]``.
        flow: Flow per bin [veh/s]: total vehicle-distance travelled in the
            bin divided by the bin area Δt·Δx (Edie 1963). Zero where no
            vehicle was observed.
    """

    t_edges: FloatArray
    x_edges: FloatArray
    flow: FloatArray


def _clean(trajectories: pd.DataFrame, dt_bin: float, dx_bin: float) -> pd.DataFrame:
    """Validate inputs and drop rows with NaN in required columns."""
    if dt_bin <= 0 or dx_bin <= 0:
        raise ValueError(f"bin sizes must be > 0, got dt_bin={dt_bin}, dx_bin={dx_bin}")
    missing = [c for c in _REQUIRED_COLUMNS if c not in trajectories.columns]
    if missing:
        raise ValueError(f"trajectories missing required columns: {missing}")
    clean = trajectories.dropna(subset=list(_REQUIRED_COLUMNS))
    if clean.empty:
        raise ValueError("trajectories contain no usable (t, x, v) samples")
    return clean


def _edges(lo: float, hi: float, width: float) -> FloatArray:
    """Bin edges starting at ``lo`` with the given width, covering ``hi``."""
    n = max(1, int(np.ceil((hi - lo) / width - 1e-9)))
    return np.asarray(lo + width * np.arange(n + 1, dtype=np.float64))


def _bin_index(values: FloatArray, edges: FloatArray) -> NDArray[np.intp]:
    """Bin index per value; values at the top edge fall into the last bin."""
    width = float(edges[1] - edges[0])
    idx = np.floor((values - edges[0]) / width).astype(np.intp)
    return np.asarray(np.clip(idx, 0, len(edges) - 2), dtype=np.intp)


def _grid(
    trajectories: pd.DataFrame, dt_bin: float, dx_bin: float
) -> tuple[pd.DataFrame, FloatArray, FloatArray, NDArray[np.intp], NDArray[np.intp]]:
    """Shared binning setup: cleaned frame, edges, and per-sample bin indices."""
    clean = _clean(trajectories, dt_bin, dx_bin)
    t = clean["t"].to_numpy(dtype=np.float64)
    x = clean["x"].to_numpy(dtype=np.float64)
    t_edges = _edges(float(t.min()), float(t.max()), dt_bin)
    x_edges = _edges(float(x.min()), float(x.max()), dx_bin)
    return clean, t_edges, x_edges, _bin_index(t, t_edges), _bin_index(x, x_edges)


def _sample_weights(clean: pd.DataFrame, sample_dt: float | None) -> FloatArray:
    """Per-sample time weight [s] for Edie sums.

    Each trajectory sample stands for one sampling interval of continuous
    motion. The interval is either supplied explicitly (``sample_dt``) or
    inferred per vehicle as the median of consecutive time differences
    (uniform-rate sampling per docs/CONTRACTS.md §3, ``sim.output_hz``).

    Args:
        clean: Trajectory rows with NaNs already removed.
        sample_dt: Explicit sampling interval [s]; ``None`` to infer.

    Returns:
        Time weight per row of ``clean`` [s].

    Raises:
        ValueError: If inference is requested but the frame has no
            ``veh_id`` column or no vehicle has two or more samples.
    """
    if sample_dt is not None:
        if sample_dt <= 0:
            raise ValueError(f"sample_dt must be > 0, got {sample_dt}")
        return np.full(len(clean), float(sample_dt))
    if "veh_id" not in clean.columns:
        raise ValueError("need a veh_id column to infer sample_dt (or pass sample_dt=...)")
    weights = np.empty(len(clean), dtype=np.float64)
    per_vehicle: dict[str, float] = {}
    t_all = clean["t"].to_numpy(dtype=np.float64)
    veh_all = clean["veh_id"].to_numpy()
    for veh in pd.unique(veh_all):
        rows = np.flatnonzero(veh_all == veh)
        ts = np.sort(t_all[rows])
        if len(ts) >= 2:
            per_vehicle[str(veh)] = float(np.median(np.diff(ts)))
    if not per_vehicle:
        raise ValueError("cannot infer sample_dt: no vehicle has two or more samples")
    fallback = float(np.median(np.asarray(list(per_vehicle.values()))))
    for veh in pd.unique(veh_all):
        rows = np.flatnonzero(veh_all == veh)
        weights[rows] = per_vehicle.get(str(veh), fallback)
    return weights


def speed_field(
    trajectories: pd.DataFrame, dt_bin: float = 15.0, dx_bin: float = 75.0
) -> SpeedField:
    """Bin trajectories into a space-time mean-speed field.

    The grid starts at the minimum observed ``t`` and ``x`` and extends in
    whole bins to cover the maxima; samples lying exactly on the final edge
    fall into the last bin. Each bin holds the arithmetic mean of the sampled
    speeds inside it; empty bins are ``NaN`` (docs/CONTRACTS.md §4).

    Args:
        trajectories: Trajectory rows with at least ``t`` [s], ``x`` [m],
            ``v`` [m/s] columns (contract §3 schema).
        dt_bin: Time bin width [s].
        dx_bin: Space bin width [m].

    Returns:
        A :class:`SpeedField` with ``mean_speed`` of shape ``[nt, nx]``.

    Raises:
        ValueError: On missing columns, non-positive bin widths, or no
            usable samples.
    """
    clean, t_edges, x_edges, ti, xi = _grid(trajectories, dt_bin, dx_bin)
    v = clean["v"].to_numpy(dtype=np.float64)
    nt, nx = len(t_edges) - 1, len(x_edges) - 1
    sums = np.zeros((nt, nx), dtype=np.float64)
    counts = np.zeros((nt, nx), dtype=np.float64)
    np.add.at(sums, (ti, xi), v)
    np.add.at(counts, (ti, xi), 1.0)
    mean = np.full((nt, nx), np.nan, dtype=np.float64)
    np.divide(sums, counts, out=mean, where=counts > 0)
    return SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=mean)


def density_field(
    trajectories: pd.DataFrame,
    dt_bin: float = 15.0,
    dx_bin: float = 75.0,
    sample_dt: float | None = None,
) -> DensityField:
    """Edie generalized density from sampled trajectories.

    Density in a bin is the total time vehicles spend inside it divided by
    the bin area Δt·Δx (Edie 1963). Each sample contributes its sampling
    interval as time spent; see :func:`_sample_weights` for how the interval
    is determined.

    Args:
        trajectories: Trajectory rows (``t``, ``x``, ``v``; ``veh_id``
            required unless ``sample_dt`` is given).
        dt_bin: Time bin width [s].
        dx_bin: Space bin width [m].
        sample_dt: Explicit sampling interval [s]; ``None`` to infer per
            vehicle from consecutive time differences.

    Returns:
        A :class:`DensityField` in veh/m.

    Raises:
        ValueError: On invalid inputs (see :func:`speed_field`) or when the
            sampling interval cannot be inferred.
    """
    clean, t_edges, x_edges, ti, xi = _grid(trajectories, dt_bin, dx_bin)
    w = _sample_weights(clean, sample_dt)
    nt, nx = len(t_edges) - 1, len(x_edges) - 1
    time_sum = np.zeros((nt, nx), dtype=np.float64)
    np.add.at(time_sum, (ti, xi), w)
    return DensityField(t_edges=t_edges, x_edges=x_edges, density=time_sum / (dt_bin * dx_bin))


def flow_field(
    trajectories: pd.DataFrame,
    dt_bin: float = 15.0,
    dx_bin: float = 75.0,
    sample_dt: float | None = None,
) -> FlowField:
    """Edie generalized flow from sampled trajectories.

    Flow in a bin is the total distance vehicles travel inside it divided by
    the bin area Δt·Δx (Edie 1963). Each sample contributes ``v·τ`` where τ
    is its sampling interval, so ``flow ≈ density · space-mean speed`` holds
    by construction on every bin.

    Args:
        trajectories: Trajectory rows (``t``, ``x``, ``v``; ``veh_id``
            required unless ``sample_dt`` is given).
        dt_bin: Time bin width [s].
        dx_bin: Space bin width [m].
        sample_dt: Explicit sampling interval [s]; ``None`` to infer.

    Returns:
        A :class:`FlowField` in veh/s.

    Raises:
        ValueError: On invalid inputs or when the sampling interval cannot
            be inferred.
    """
    clean, t_edges, x_edges, ti, xi = _grid(trajectories, dt_bin, dx_bin)
    w = _sample_weights(clean, sample_dt)
    v = clean["v"].to_numpy(dtype=np.float64)
    nt, nx = len(t_edges) - 1, len(x_edges) - 1
    dist_sum = np.zeros((nt, nx), dtype=np.float64)
    np.add.at(dist_sum, (ti, xi), v * w)
    return FlowField(t_edges=t_edges, x_edges=x_edges, flow=dist_sum / (dt_bin * dx_bin))
