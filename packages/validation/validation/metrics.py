"""Standard run metrics with honest uncertainty (CLAUDE.md §7.3, contract §7).

All metrics are computed from a run directory's ``trajectories.parquet`` and
``meta.json`` (docs/CONTRACTS.md §3) in SI units, deterministically. GEH and
RMSPE implement the FHWA-style calibration comparison statistics (FHWA
Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019). Replicate
aggregation reports t-distribution confidence intervals and flags
underpowered sample sizes (< 20 replicates, CLAUDE.md §0.6).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import t as student_t

from flowstate_core.constants import V_JAM_THRESH
from flowstate_core.units import ms_to_kmh, s_to_h, veh_s_to_veh_h
from validation.fields import speed_field
from validation.waves import detect_waves

FloatArray = NDArray[np.float64]

#: Minimum replicate count for headline reporting (CLAUDE.md §0.6).
MIN_REPLICATES = 20

#: Two-sided confidence level for replicate CIs.
CI_LEVEL = 0.95

#: Kilometres per metre, derived from the unit helpers only (CLAUDE.md §2):
#: a distance of 1 m is 1 m/s sustained for 1 s, i.e. ms_to_kmh(1) km/h for
#: s_to_h(1) hours.
_KM_PER_M = ms_to_kmh(1.0) * s_to_h(1.0)


class CI(NamedTuple):
    """Replicate confidence interval: ``(mean, lo95, hi95, n)`` (contract §7).

    ``lo95``/``hi95`` are the two-sided t-distribution bounds at
    :data:`CI_LEVEL`; ``n`` is the number of finite replicate values used.
    """

    mean: float
    lo95: float
    hi95: float
    n: int

    @property
    def underpowered(self) -> bool:
        """True when ``n`` is below the headline minimum (CLAUDE.md §0.6)."""
        return self.n < MIN_REPLICATES


@dataclass(frozen=True)
class Metrics:
    """Standard metrics for one run (docs/CONTRACTS.md §7).

    Attributes:
        throughput_veh_h: Vehicles crossing the reference cross-section per
            hour [veh/h].
        mean_tt_s: Mean per-vehicle travel time across the measurement span
            [s]; NaN if no vehicle traverses the span.
        p90_tt_s: 90th-percentile travel time [s] (linear interpolation).
        sigma_v_spatial_ms: Spatial speed standard deviation [m/s]: at each
            output timestamp, the sample standard deviation (ddof=1) of
            instantaneous speeds across the vehicles present, averaged over
            all timestamps with at least two vehicles.
        sigma_v_temporal_ms: Temporal speed standard deviation [m/s]: for
            each vehicle, the sample standard deviation (ddof=1) of its
            speed over time, averaged over all vehicles with at least two
            samples.
        vmt_veh_km: Total distance travelled by all vehicles [veh·km]
            (VMT in the FHWA sense, reported metric), integrated per vehicle
            by the trapezoid rule over sampled speeds.
        vht_veh_h: Total time spent travelling by all vehicles [veh·h]
            (observed sample span per vehicle).
        fuel_ml_per_veh_km: Fuel consumption per vehicle-kilometre [ml/veh·km]
            from the run's meta fuel totals; NaN when not recorded.
        wave_count: Number of detected stop-and-go waves.
        wave_speed_kmh: Mean magnitude of backward (upstream-propagating)
            wave-front speeds [km/h], reported positive; NaN if no backward
            wave was detected.
        wave_amplitude_ms: Mean wave amplitude (free speed minus jam minimum)
            across all detected waves [m/s]; NaN if none.
    """

    throughput_veh_h: float
    mean_tt_s: float
    p90_tt_s: float
    sigma_v_spatial_ms: float
    sigma_v_temporal_ms: float
    vmt_veh_km: float
    vht_veh_h: float
    fuel_ml_per_veh_km: float
    wave_count: int
    wave_speed_kmh: float
    wave_amplitude_ms: float


def geh(m: float, c: float) -> float:
    """GEH statistic for hourly flows: ``√(2(m−c)² / (m+c))``.

    The standard microsimulation link-flow comparison statistic; acceptance
    practice is GEH < 5 for at least 85% of link-hour comparisons (FHWA
    Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019; UK DMRB).
    Both arguments must be hourly flows [veh/h] — GEH is not dimensionless
    and is only meaningful on hourly volumes.

    Args:
        m: Modelled hourly flow [veh/h], >= 0.
        c: Observed (count) hourly flow [veh/h], >= 0.

    Returns:
        GEH value; defined as 0 when both flows are exactly zero.

    Raises:
        ValueError: If either flow is negative.
    """
    if m < 0 or c < 0:
        raise ValueError(f"flows must be >= 0, got m={m}, c={c}")
    if m == 0 and c == 0:
        return 0.0
    return math.sqrt(2.0 * (m - c) ** 2 / (m + c))


def rmspe(sim: NDArray[np.float64], obs: NDArray[np.float64]) -> float:
    """Root-mean-square percentage error, returned as a fraction.

    ``rmspe = √(mean(((sim − obs)/obs)²))`` — e.g. ``0.15`` means 15%.
    Used for the segment-speed acceptance criterion (CLAUDE.md §7.1).

    Args:
        sim: Simulated values.
        obs: Observed values, same shape, all nonzero.

    Returns:
        RMSPE as a fraction.

    Raises:
        ValueError: On shape mismatch, empty input, or any zero observation.
    """
    sim_a = np.asarray(sim, dtype=np.float64)
    obs_a = np.asarray(obs, dtype=np.float64)
    if sim_a.shape != obs_a.shape:
        raise ValueError(f"shape mismatch: {sim_a.shape} vs {obs_a.shape}")
    if sim_a.size == 0:
        raise ValueError("rmspe of empty arrays is undefined")
    if np.any(obs_a == 0):
        raise ValueError("rmspe undefined for zero observations")
    return float(np.sqrt(np.mean(((sim_a - obs_a) / obs_a) ** 2)))


def travel_times(trajectories: pd.DataFrame, x_lo: float, x_hi: float) -> FloatArray:
    """Per-vehicle travel time across the measurement span ``[x_lo, x_hi]``.

    A vehicle's entry time is its first upward crossing of ``x_lo`` (linearly
    interpolated between the bracketing samples); if its first sample is
    already at or past ``x_lo``, the first sample time is used. The exit time
    is the first upward crossing of ``x_hi``. Vehicles that never reach
    ``x_hi``, or whose first sample is already past ``x_hi``, are excluded.

    Args:
        trajectories: Trajectory rows with ``t``, ``x``, ``veh_id`` columns.
        x_lo: Span entry position [m].
        x_hi: Span exit position [m], must exceed ``x_lo``.

    Returns:
        Array of travel times [s], one per vehicle completing the span.

    Raises:
        ValueError: If ``x_hi <= x_lo`` or required columns are missing.
    """
    if x_hi <= x_lo:
        raise ValueError(f"need x_hi > x_lo, got [{x_lo}, {x_hi}]")
    for col in ("t", "x", "veh_id"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    out: list[float] = []
    for _, group in trajectories.groupby("veh_id", sort=False):
        g = group.sort_values("t")
        t = g["t"].to_numpy(dtype=np.float64)
        x = g["x"].to_numpy(dtype=np.float64)
        t_enter = _first_crossing(t, x, x_lo)
        t_exit = _first_crossing(t, x, x_hi)
        if t_enter is None or t_exit is None:
            continue
        if x[0] >= x_hi:
            continue  # first observed already past the span exit
        if t_exit > t_enter:
            out.append(t_exit - t_enter)
    return np.asarray(out, dtype=np.float64)


def _first_crossing(t: FloatArray, x: FloatArray, x_target: float) -> float | None:
    """Time of the first upward crossing of ``x_target``; None if never."""
    if x[0] >= x_target:
        return float(t[0])
    above = np.flatnonzero(x >= x_target)
    if above.size == 0:
        return None
    i = int(above[0])
    dx = x[i] - x[i - 1]
    if dx <= 0:
        return float(t[i])
    frac = (x_target - x[i - 1]) / dx
    return float(t[i - 1] + frac * (t[i] - t[i - 1]))


def _crossing_times(trajectories: pd.DataFrame, x_ref: float) -> FloatArray:
    """Times of every upward crossing of ``x_ref`` (see :func:`count_crossings`).

    Each crossing is stamped with the time of the later sample of the pair
    that brackets ``x_ref``. Vectorised over the whole frame: rows are sorted
    by ``(veh_id, t)`` and consecutive same-vehicle pairs are tested.
    """
    for col in ("t", "veh_id", "x"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if len(trajectories) < 2:
        return np.empty(0, dtype=np.float64)
    ordered = trajectories.sort_values(["veh_id", "t"], kind="stable")
    vid = ordered["veh_id"].to_numpy()
    x = ordered["x"].to_numpy(dtype=np.float64)
    t = ordered["t"].to_numpy(dtype=np.float64)
    hit = (vid[1:] == vid[:-1]) & (x[:-1] < x_ref) & (x[1:] >= x_ref)
    return np.asarray(t[1:][hit], dtype=np.float64)


def count_crossings(
    trajectories: pd.DataFrame,
    x_ref: float,
    *,
    t_lo: float | None = None,
    t_hi: float | None = None,
) -> int:
    """Number of upward crossings of ``x_ref`` over all vehicles.

    A crossing is a consecutive, time-ordered sample pair of one vehicle
    with ``x_prev < x_ref <= x_cur``. It is stamped with the time of the
    later sample and counted when ``t_lo <= t < t_hi`` (either bound may be
    omitted). On a ring the wrap jump is downward and is therefore not
    counted, so each lap contributes exactly one crossing. This is the
    crossing definition behind :func:`compute_metrics` throughput,
    :func:`crossings_per_window` and :func:`link_hour_geh`, and the one the
    demand adapter (``microsim.demand_adapter``) uses for boundary counts.

    Args:
        trajectories: Rows with ``t`` [s], ``veh_id`` and ``x`` [m] columns
            (docs/CONTRACTS.md §3).
        x_ref: Cross-section position [m] in trajectory coordinates.
        t_lo: Inclusive lower time bound [s]; ``None`` for no bound.
        t_hi: Exclusive upper time bound [s]; ``None`` for no bound.

    Returns:
        Crossing count.

    Raises:
        ValueError: On missing columns or ``t_hi <= t_lo``.
    """
    if t_lo is not None and t_hi is not None and t_hi <= t_lo:
        raise ValueError(f"need t_hi > t_lo, got [{t_lo}, {t_hi})")
    times = _crossing_times(trajectories, x_ref)
    if t_lo is not None:
        times = times[times >= t_lo]
    if t_hi is not None:
        times = times[times < t_hi]
    return int(times.size)


def crossings_per_window(
    trajectories: pd.DataFrame,
    x_ref: float,
    *,
    t_lo: float,
    t_hi: float,
    window_s: float,
) -> NDArray[np.int64]:
    """Crossings of ``x_ref`` binned into consecutive windows of ``window_s``.

    Windows are ``[t_lo + k·window_s, t_lo + (k+1)·window_s)`` for
    ``k = 0 … n−1`` with ``n = (t_hi − t_lo) / window_s``, which must be an
    integer. Crossing semantics are those of :func:`count_crossings`.

    Args:
        trajectories: Rows with ``t``, ``veh_id`` and ``x`` columns.
        x_ref: Cross-section position [m] in trajectory coordinates.
        t_lo: Start of the first window [s] (inclusive).
        t_hi: End of the last window [s] (exclusive).
        window_s: Window length [s], > 0.

    Returns:
        Integer counts, one per window, in time order.

    Raises:
        ValueError: If ``window_s <= 0``, ``t_hi <= t_lo``, or the span is
            not a whole number of windows.
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be > 0, got {window_s}")
    if t_hi <= t_lo:
        raise ValueError(f"need t_hi > t_lo, got [{t_lo}, {t_hi})")
    n_win = round((t_hi - t_lo) / window_s)
    if n_win < 1 or abs(n_win * window_s - (t_hi - t_lo)) > 1e-6:
        raise ValueError(f"[{t_lo}, {t_hi}) s is not a whole number of {window_s} s windows")
    times = _crossing_times(trajectories, x_ref)
    times = times[(times >= t_lo) & (times < t_hi)]
    idx = np.minimum(((times - t_lo) // window_s).astype(np.int64), n_win - 1)
    counts = np.bincount(idx, minlength=n_win)[:n_win]
    return np.asarray(counts, dtype=np.int64)


def compute_metrics(
    run_dir: str | Path,
    x_ref: float | None = None,
    span: tuple[float, float] | None = None,
    dt_bin: float = 15.0,
    dx_bin: float = 75.0,
    v_jam_thresh: float = V_JAM_THRESH,
    min_area_bins: int = 4,
) -> Metrics:
    """Compute the standard metric set for one run directory.

    Reads ``trajectories.parquet`` and ``meta.json`` per docs/CONTRACTS.md
    §3. Definitions of every metric are on :class:`Metrics`. Wave metrics
    come from :func:`validation.waves.detect_waves` on the binned speed
    field. Fuel is taken from the meta key ``fuel_total_ml`` (total run fuel
    [ml], SUMO HBEFA4 totals) when present, else NaN.

    Args:
        run_dir: Directory holding ``trajectories.parquet`` and ``meta.json``.
        x_ref: Reference cross-section for throughput [m]; ``None`` uses the
            midpoint of the observed position range.
        span: Measurement span ``(x_lo, x_hi)`` [m] for travel times;
            ``None`` uses the full observed position range.
        dt_bin: Speed-field time bin [s] for wave detection.
        dx_bin: Speed-field space bin [m] for wave detection.
        v_jam_thresh: Jam threshold [m/s] for wave detection.
        min_area_bins: Minimum wave component size in bins.

    Returns:
        A :class:`Metrics` instance.

    Raises:
        FileNotFoundError: If either input file is missing.
        ValueError: If the trajectory frame is empty.
    """
    run_path = Path(run_dir)
    traj_path = run_path / "trajectories.parquet"
    meta_path = run_path / "meta.json"
    if not traj_path.is_file():
        raise FileNotFoundError(f"missing {traj_path}")
    if not meta_path.is_file():
        raise FileNotFoundError(f"missing {meta_path}")
    # Only the columns the metrics use: a 7,800 s corridor run holds ~10 M
    # rows, and the full eight-column frame plus groupby copies peaked at
    # ~7 GB of RSS in the report generator (part of what crashed a 16 GB
    # machine during the I-24 validation).
    traj = pd.read_parquet(traj_path, columns=["t", "veh_id", "x", "v"])
    if traj.empty:
        raise ValueError(f"{traj_path} holds no trajectory rows")
    meta = json.loads(meta_path.read_text())

    t_all = traj["t"].to_numpy(dtype=np.float64)
    x_all = traj["x"].to_numpy(dtype=np.float64)
    x_min, x_max = float(x_all.min()), float(x_all.max())
    if x_ref is None:
        x_ref = 0.5 * (x_min + x_max)
    if span is None:
        span = (x_min, x_max)

    # Throughput at the reference cross-section.
    t_span_s = float(t_all.max() - t_all.min())
    crossings = count_crossings(traj, x_ref)
    throughput = veh_s_to_veh_h(crossings / t_span_s) if t_span_s > 0 else math.nan

    # Travel times over the measurement span.
    tts = travel_times(traj, span[0], span[1])
    mean_tt = float(tts.mean()) if tts.size else math.nan
    p90_tt = float(np.percentile(tts, 90)) if tts.size else math.nan

    # σ_v spatial: std across vehicles at each shared output timestamp.
    by_t = traj.groupby("t")["v"]
    spatial_stds = by_t.std(ddof=1)[by_t.count() >= 2]
    sigma_spatial = float(spatial_stds.mean()) if len(spatial_stds) else math.nan

    # σ_v temporal: std over time per vehicle.
    by_veh = traj.groupby("veh_id")["v"]
    temporal_stds = by_veh.std(ddof=1)[by_veh.count() >= 2]
    sigma_temporal = float(temporal_stds.mean()) if len(temporal_stds) else math.nan

    # VMT / VHT via trapezoid integration of sampled speeds.
    vmt_km = 0.0
    vht_h = 0.0
    for _, group in traj.groupby("veh_id", sort=False):
        g = group.sort_values("t")
        t = g["t"].to_numpy(dtype=np.float64)
        v = g["v"].to_numpy(dtype=np.float64)
        if len(t) < 2:
            continue
        mid_v = 0.5 * (v[:-1] + v[1:])
        dt = np.diff(t)
        vmt_km += _KM_PER_M * float(np.sum(mid_v * dt))
        vht_h += s_to_h(float(t[-1] - t[0]))

    # Fuel per vehicle-km from meta totals, when recorded.
    fuel_total = meta.get("fuel_total_ml")
    if fuel_total is None or vmt_km <= 0:
        fuel_per_km = math.nan
    else:
        fuel_per_km = float(fuel_total) / vmt_km

    # Wave metrics from the binned speed field.
    field = speed_field(traj, dt_bin=dt_bin, dx_bin=dx_bin)
    wave_set = detect_waves(field, v_jam_thresh=v_jam_thresh, min_area_bins=min_area_bins)
    backward = wave_set.backward()
    if backward:
        wave_speed_kmh = float(np.mean([ms_to_kmh(-w.speed_ms) for w in backward]))
    else:
        wave_speed_kmh = math.nan
    if wave_set.count:
        wave_amp = float(np.mean([w.amplitude_ms for w in wave_set.waves]))
    else:
        wave_amp = math.nan

    return Metrics(
        throughput_veh_h=throughput,
        mean_tt_s=mean_tt,
        p90_tt_s=p90_tt,
        sigma_v_spatial_ms=sigma_spatial,
        sigma_v_temporal_ms=sigma_temporal,
        vmt_veh_km=vmt_km,
        vht_veh_h=vht_h,
        fuel_ml_per_veh_km=fuel_per_km,
        wave_count=wave_set.count,
        wave_speed_kmh=wave_speed_kmh,
        wave_amplitude_ms=wave_amp,
    )


def aggregate(metrics_list: list[Metrics]) -> dict[str, CI]:
    """Aggregate replicate metrics into per-field t-distribution CIs.

    For each numeric field of :class:`Metrics`, NaN replicate values are
    dropped (e.g. fuel unrecorded in some runs), then the two-sided
    :data:`CI_LEVEL` confidence interval ``mean ± t_{α/2, n−1}·s/√n`` is
    computed over the remaining ``n`` values. With ``n == 1`` the bounds are
    NaN; with ``n == 0`` the mean is NaN too. Each :class:`CI` carries an
    ``underpowered`` flag that is True when ``n <`` :data:`MIN_REPLICATES`
    (CLAUDE.md §0.6) — such values must not be quoted as headline results.

    Args:
        metrics_list: Metrics from replicate runs of one configuration.

    Returns:
        Mapping from field name to :class:`CI`.

    Raises:
        ValueError: If ``metrics_list`` is empty.
    """
    if not metrics_list:
        raise ValueError("metrics_list is empty")
    out: dict[str, CI] = {}
    for f in dataclasses.fields(Metrics):
        values = np.asarray([float(getattr(m, f.name)) for m in metrics_list], dtype=np.float64)
        finite = values[np.isfinite(values)]
        n = int(finite.size)
        if n == 0:
            out[f.name] = CI(math.nan, math.nan, math.nan, 0)
            continue
        mean = float(finite.mean())
        if n == 1:
            out[f.name] = CI(mean, math.nan, math.nan, 1)
            continue
        half = float(student_t.ppf(0.5 + CI_LEVEL / 2.0, n - 1) * finite.std(ddof=1) / math.sqrt(n))
        out[f.name] = CI(mean, mean - half, mean + half, n)
    return out


def geh_pass_fraction(geh_values: Sequence[float], threshold: float = 5.0) -> float:
    """Fraction of comparisons with ``GEH < threshold`` (strict).

    NaN entries never satisfy the bound and therefore count as failing —
    the honest reading of a comparison that could not be formed.

    Args:
        geh_values: Per-comparison GEH statistics.
        threshold: Strict upper bound; 5 is the FHWA/DOT convention
            (``validation.criteria`` carries the sourced profiles).

    Returns:
        Fraction in ``[0, 1]``.

    Raises:
        ValueError: If no comparisons are supplied.
    """
    if len(geh_values) == 0:
        raise ValueError("geh_pass_fraction of no comparisons is undefined")
    return sum(1 for g in geh_values if g < threshold) / len(geh_values)


@dataclass(frozen=True)
class LinkHourGEH:
    """Per-link-window GEH comparison (CLAUDE.md §7.1 link-flow criterion).

    One entry per matched ``(x_ref_m, window_start_s)`` bin, in the order of
    the observed rows (rows whose observed flow was NaN are dropped and
    counted in ``n_dropped_nan``).

    Attributes:
        geh: GEH statistic per bin, on hourly-equivalent volumes.
        x_ref_m: Cross-section of each bin [m].
        window_start_s: Window start of each bin [s].
        sim_veh_h: Simulated hourly-equivalent flow per bin [veh/h].
        obs_veh_h: Observed hourly-equivalent flow per bin [veh/h].
        window_s: Window length used for every bin [s].
        n_dropped_nan: Observed rows dropped because their flow was NaN.
    """

    geh: tuple[float, ...]
    x_ref_m: tuple[float, ...]
    window_start_s: tuple[float, ...]
    sim_veh_h: tuple[float, ...]
    obs_veh_h: tuple[float, ...]
    window_s: float
    n_dropped_nan: int

    def pass_fraction(self, threshold: float = 5.0) -> float:
        """Fraction of bins with ``GEH < threshold`` (:func:`geh_pass_fraction`)."""
        return geh_pass_fraction(self.geh, threshold)


def link_hour_geh(
    sim: pd.DataFrame,
    observed: pd.DataFrame,
    *,
    x_refs_m: Sequence[float],
    window_s: float = 3600.0,
) -> LinkHourGEH:
    """GEH per link-window between simulated crossings and observed counts.

    For every observed row, the simulated crossings of its cross-section
    inside ``[window_start_s, window_start_s + window_s)`` are counted with
    :func:`count_crossings`, scaled to an hourly-equivalent flow through
    ``flowstate_core.units`` (``veh_s_to_veh_h(n / window_s)``), and compared
    with the observed hourly-equivalent flow by :func:`geh`. GEH is only
    meaningful on hourly volumes; a shorter ``window_s`` (e.g. the 300 s
    windows of the I-24 comparison) scales both sides identically, which is
    the usual practice, but the resulting values are hourly-*equivalent* and
    the report must say so.

    Args:
        sim: Trajectory rows with ``t`` [s], ``veh_id`` and ``x`` [m]
            columns, in the same coordinates as ``observed``.
        observed: Rows with ``x_ref_m`` [m], ``window_start_s`` [s] and
            ``flow_veh_h`` [veh/h] columns; every ``x_ref_m`` must be one of
            ``x_refs_m`` (a guard against coordinate mix-ups).
        x_refs_m: Cross-sections the comparison may use [m].
        window_s: Window length [s] shared by every observed row.

    Returns:
        A :class:`LinkHourGEH` with one GEH per matched bin.

    Raises:
        ValueError: On missing columns, an empty simulation, a negative
            observed flow, an observed cross-section absent from
            ``x_refs_m``, or an observed window not fully covered by the
            simulated time span (an unmatched bin is an error, never a
            silent skip).
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be > 0, got {window_s}")
    x_refs = [float(x) for x in x_refs_m]
    if not x_refs:
        raise ValueError("x_refs_m is empty")
    for col in ("x_ref_m", "window_start_s", "flow_veh_h"):
        if col not in observed.columns:
            raise ValueError(f"observed missing column {col!r}")
    for col in ("t", "veh_id", "x"):
        if col not in sim.columns:
            raise ValueError(f"sim missing column {col!r}")
    if sim.empty:
        raise ValueError("sim holds no trajectory rows")
    tol = 1e-6
    t_min = float(sim["t"].min())
    t_max = float(sim["t"].max())
    obs_x = observed["x_ref_m"].to_numpy(dtype=np.float64)
    obs_w = observed["window_start_s"].to_numpy(dtype=np.float64)
    obs_q = observed["flow_veh_h"].to_numpy(dtype=np.float64)
    keep = np.isfinite(obs_q)
    n_dropped = int(np.count_nonzero(~keep))

    crossing_times: dict[int, FloatArray] = {}
    gehs: list[float] = []
    xs: list[float] = []
    ws: list[float] = []
    sims: list[float] = []
    obss: list[float] = []
    for x_o, w_o, q_o in zip(obs_x[keep], obs_w[keep], obs_q[keep], strict=True):
        matches = [i for i, x in enumerate(x_refs) if math.isclose(x, x_o, abs_tol=tol)]
        if not matches:
            raise ValueError(f"observed cross-section x = {x_o} m is not in x_refs_m")
        if q_o < 0:
            raise ValueError(f"observed flow must be >= 0, got {q_o} veh/h at x = {x_o} m")
        if w_o < t_min - tol or w_o + window_s > t_max + tol:
            raise ValueError(
                f"observed window [{w_o}, {w_o + window_s}) s at x = {x_o} m is not "
                f"covered by the simulated span [{t_min}, {t_max}] s"
            )
        i = matches[0]
        if i not in crossing_times:
            crossing_times[i] = _crossing_times(sim, x_refs[i])
        times = crossing_times[i]
        n = int(np.count_nonzero((times >= w_o) & (times < w_o + window_s)))
        q_sim = veh_s_to_veh_h(n / window_s)
        gehs.append(geh(q_sim, float(q_o)))
        xs.append(x_refs[i])
        ws.append(float(w_o))
        sims.append(q_sim)
        obss.append(float(q_o))
    return LinkHourGEH(
        geh=tuple(gehs),
        x_ref_m=tuple(xs),
        window_start_s=tuple(ws),
        sim_veh_h=tuple(sims),
        obs_veh_h=tuple(obss),
        window_s=float(window_s),
        n_dropped_nan=n_dropped,
    )
