"""Triangular fundamental-diagram fitting (CLAUDE.md §6.1).

Fits a triangular FD (Newell/Daganzo form — Daganzo 1994; Treiber & Kesting,
*Traffic Flow Dynamics*, ch. 4) to a flow-density scatter from station data:

1. **Free-flow branch**: least-squares regression through the origin
   (``q = v_f ρ``) on uncongested points, selected by an occupancy or density
   threshold.
2. **Capacity**: ``q_max`` = the 95th-percentile observed flow (a robust
   stand-in for the noisy scatter maximum; CLAUDE.md §6.1).
3. **Congested branch**: quantile regression at τ = 0.9 of ``q`` on ``ρ``
   through the congested cloud. Congested detector scatter lies mostly
   *below* the equilibrium bound (non-equilibrium and transient states), so
   an upper quantile tracks the equilibrium branch better than a mean fit
   (Treiber & Kesting ch. 4 discussion of FD scatter). The fitted line
   ``q = a + wρ`` gives the wave speed ``w`` (slope, negative) and jam
   density ``ρ_jam = −a/w`` (density intercept).

Quantile regression is solved *exactly* as a linear program via
``scipy.optimize.linprog`` (statsmodels is deliberately not a dependency):
minimize ``Σ τ u⁺ + (1−τ) u⁻`` subject to ``y − Xβ = u⁺ − u⁻``, ``u± ≥ 0`` —
the standard Koenker-Bassett (1978) LP formulation.

Uncertainty: seeded nonparametric bootstrap (default n = 200) over data rows;
95% CIs are the 2.5/97.5 percentiles of the refitted parameters
(CLAUDE.md §0.6 — honest uncertainty).

**Input hygiene and the plausibility gate.** Detector exports carry failed
intervals as negative sentinels (``Flow = -1``), occupancies above 100%, and
zero-density rows with positive flow; a mis-declared aggregation interval or
occupancy unit rescales a whole column. None of these show up in ``R²`` or in
the bootstrap CI width — a mixed-unit upload fits with ``R² = 0.99`` and a jam
density three orders of magnitude too high. This module therefore

* drops non-physical rows before fitting and **reports the counts by reason**
  on the artifact (``FDCalibration.dropped_rows``), refusing outright when the
  dropped share exceeds ``max_dropped_fraction``;
* records the fitted columns' min/max on the artifact
  (``FDCalibration.input_ranges``) — the one diagnostic a scale error cannot
  hide from; and
* **range-checks the fitted diagram** against documented per-lane bounds
  (:data:`DEFAULT_FD_BOUNDS`) before the artifact is constructed, naming the
  offending value in customer units and the likely cause.

Unit mistakes that rescale a whole column are caught earlier and more
precisely at the loader (``calibration.loaders.pems.load_pems_station_csv``
cross-checks ``q/ρ`` against the reported speed); the gate here is the
backstop for everything that reaches the fitter by another path.

**Size.** The congested-branch LP is quadratic in row count (≈ 46 s for one
fit at 2·10⁵ rows), and a bootstrap materialises one resampled copy of the
data per refit. The fit is therefore capped at ``max_fit_rows`` rows by a
seeded, order-preserving subsample (recorded on the artifact), and bootstrap
resamples are generated and consumed in bounded batches instead of being
built up front.
"""

from __future__ import annotations

import hashlib
import multiprocessing
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

from flowstate_core.artifacts import FDCalibration, TriangularFD
from flowstate_core.rng import make_rng
from flowstate_core.units import ms_to_kmh, veh_m_to_veh_km, veh_s_to_veh_h

V_F_BOUNDS_MS: Final[tuple[float, float]] = (5.0, 45.0)
"""Plausible fitted free-flow speed [m/s] — 18–162 km/h."""

W_ABS_BOUNDS_MS: Final[tuple[float, float]] = (2.0, 10.0)
"""Plausible |congested wave speed| [m/s] — 7.2–36 km/h, generous either side
of the 14–22 km/h empirical band CLAUDE.md §7.1 accepts."""

RHO_JAM_BOUNDS_VEH_M: Final[tuple[float, float]] = (0.05, 0.30)
"""Plausible per-lane jam density [veh/m] — 50–300 veh/km."""

Q_MAX_BOUNDS_VEH_S: Final[tuple[float, float]] = (0.1, 1.0)
"""Plausible per-lane capacity [veh/s] — 360–3600 veh/h."""


@dataclass(frozen=True)
class FDBounds:
    """Physically plausible ranges for a fitted **per-lane** triangular FD.

    The defaults (:data:`DEFAULT_FD_BOUNDS`) are deliberately wide: they
    bracket every FD this repo has fitted from real data
    (``artifacts/fd_i24.json``: v_f 67 km/h, w −16 km/h, ρ_jam 137 veh/km,
    q_max 1786 veh/h; ``artifacts/fd_us101.json``: v_f 57 km/h, w −15 km/h,
    ρ_jam 180 veh/km, q_max 2093 veh/h) and the ``v1_legacy`` preset
    (100 km/h, −20 km/h, 160 veh/km), while refusing the unit and sentinel
    failures that otherwise fit with R² ≈ 0.99.

    Pass an explicit instance when the input is legitimately outside these
    ranges — e.g. an FD pooled over several lanes (capacity and jam density
    scale with lane count), or one fitted from simulated micro data.
    """

    v_f_ms: tuple[float, float] = V_F_BOUNDS_MS
    w_abs_ms: tuple[float, float] = W_ABS_BOUNDS_MS
    rho_jam_veh_m: tuple[float, float] = RHO_JAM_BOUNDS_VEH_M
    q_max_veh_s: tuple[float, float] = Q_MAX_BOUNDS_VEH_S


DEFAULT_FD_BOUNDS: Final[FDBounds] = FDBounds()
"""Default per-lane plausibility bounds applied by :func:`fit_triangular_fd`."""

_UNIT_HINT_FLOW: Final[str] = (
    "check interval_s= (Flow must be a vehicle count per interval, not veh/h) "
    "and whether the table pools several lanes"
)
_UNIT_HINT_DENSITY: Final[str] = (
    "check occupancy_unit= (part of the input may be in percent) and g_effective_length_m="
)

_BOOTSTRAP_BATCH: Final[int] = 8
"""Resampled copies of the data held in memory at once, per worker."""

DROP_REASONS: Final[tuple[str, ...]] = (
    "non_finite",
    "negative",
    "occupancy_above_100pct",
    "zero_density_positive_flow",
)
"""Reasons a row is dropped as non-physical, in the order they are tested."""


def _hash_dataframe(df: pd.DataFrame) -> str:
    """Deterministic sha256 hex digest of a dataframe's contents."""
    h = hashlib.sha256()
    h.update(",".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


def check_fd_plausible(fd: TriangularFD, *, bounds: FDBounds = DEFAULT_FD_BOUNDS) -> None:
    """Refuse a fitted FD whose parameters are physically implausible.

    Checks free-flow speed, wave speed, jam density and capacity against
    ``bounds`` (see :class:`FDBounds` for why the defaults are what they are).
    Sign constraints are already enforced by ``TriangularFD`` itself; this is
    the *scale* check, the one a good R² and a tight bootstrap CI cannot give.

    Args:
        fd: The fitted diagram.
        bounds: Accepted ranges (per lane by default).

    Raises:
        ValueError: If any parameter is outside its range. The message names
            every offending value in customer units (km/h, veh/km, veh/h),
            its accepted range, and the likely input mistake.
    """
    problems: list[str] = []
    lo, hi = bounds.v_f_ms
    if not lo <= fd.v_f <= hi:
        problems.append(
            f"free-flow speed v_f={ms_to_kmh(fd.v_f):.1f} km/h is outside "
            f"[{ms_to_kmh(lo):.0f}, {ms_to_kmh(hi):.0f}] km/h; {_UNIT_HINT_FLOW}"
        )
    lo, hi = bounds.w_abs_ms
    if not lo <= abs(fd.w) <= hi:
        problems.append(
            f"wave speed w={ms_to_kmh(fd.w):.1f} km/h is outside "
            f"[-{ms_to_kmh(hi):.0f}, -{ms_to_kmh(lo):.0f}] km/h; {_UNIT_HINT_DENSITY}"
        )
    lo, hi = bounds.rho_jam_veh_m
    if not lo <= fd.rho_jam <= hi:
        problems.append(
            f"jam density rho_jam={veh_m_to_veh_km(fd.rho_jam):.0f} veh/km is outside "
            f"[{veh_m_to_veh_km(lo):.0f}, {veh_m_to_veh_km(hi):.0f}] veh/km; "
            f"{_UNIT_HINT_DENSITY}"
        )
    lo, hi = bounds.q_max_veh_s
    if not lo <= fd.q_max <= hi:
        problems.append(
            f"capacity q_max={veh_s_to_veh_h(fd.q_max):.0f} veh/h is outside "
            f"[{veh_s_to_veh_h(lo):.0f}, {veh_s_to_veh_h(hi):.0f}] veh/h; {_UNIT_HINT_FLOW}"
        )
    if problems:
        raise ValueError(
            "implausible fitted fundamental diagram: "
            + "; ".join(problems)
            + ". Pass bounds= to fit_triangular_fd if this corridor really is "
            "outside the documented per-lane ranges."
        )


def quantile_line_fit(x: np.ndarray, y: np.ndarray, tau: float) -> tuple[float, float]:
    """Fit ``y = a + b·x`` by τ-quantile regression (exact LP solution).

    Koenker & Bassett (1978) formulation: minimize the asymmetrically
    weighted absolute residuals ``Σ τ u⁺ + (1−τ) u⁻`` with
    ``y − a − b·x = u⁺ − u⁻`` and ``u± ≥ 0``, solved with HiGHS via
    ``scipy.optimize.linprog``.

    Args:
        x: Predictor values, shape (n,).
        y: Response values, shape (n,).
        tau: Quantile level in (0, 1).

    Returns:
        (intercept a, slope b).

    Raises:
        ValueError: If tau is outside (0, 1) or fewer than 2 points given.
        RuntimeError: If the LP solver fails.
    """
    if not 0.0 < tau < 1.0:
        raise ValueError(f"tau must be in (0, 1), got {tau}")
    n = x.shape[0]
    if n < 2:
        raise ValueError(f"need >= 2 points for a line fit, got {n}")
    design = sparse.hstack(
        [
            sparse.csc_matrix(np.column_stack([np.ones(n), x])),
            sparse.identity(n, format="csc"),
            -sparse.identity(n, format="csc"),
        ],
        format="csc",
    )
    cost = np.concatenate([np.zeros(2), np.full(n, tau), np.full(n, 1.0 - tau)])
    bounds = [(None, None), (None, None)] + [(0.0, None)] * (2 * n)
    res = linprog(cost, A_eq=design, b_eq=y, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"quantile regression LP failed: {res.message}")
    return float(res.x[0]), float(res.x[1])


def _row_validity(
    density: np.ndarray, flow: np.ndarray, occupancy: np.ndarray | None
) -> tuple[np.ndarray, dict[str, int]]:
    """Mask of physically usable rows plus drop counts by reason.

    The reason masks are mutually exclusive and tested in the order of
    :data:`DROP_REASONS`, so the counts sum to the number of dropped rows.
    ``occupancy`` is checked only when it is passed, i.e. only when it is the
    column that drives the free/congested split (a NaN there would otherwise
    push the row silently onto the congested branch, since ``NaN <= cut`` is
    False and the congested mask is the complement of the free one).
    """
    finite = np.isfinite(density) & np.isfinite(flow)
    if occupancy is not None:
        finite &= np.isfinite(occupancy)
    negative = finite & ((density < 0.0) | (flow < 0.0))
    if occupancy is not None:
        negative |= finite & (occupancy < 0.0)
    over_occ = (
        finite & ~negative & (occupancy > 1.0) if occupancy is not None else np.zeros_like(finite)
    )
    zero_rho = finite & ~negative & ~over_occ & (density == 0.0) & (flow > 0.0)
    valid = finite & ~negative & ~over_occ & ~zero_rho
    counts = {
        "non_finite": int((~finite).sum()),
        "negative": int(negative.sum()),
        "occupancy_above_100pct": int(over_occ.sum()),
        "zero_density_positive_flow": int(zero_rho.sum()),
    }
    return valid, {k: v for k, v in counts.items() if v > 0}


def _split_branches(
    density: np.ndarray,
    occupancy: np.ndarray | None,
    v_f_hint: float | None,
    q_max: float,
    uncongested_max_density: float | None,
    uncongested_max_occupancy: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Uncongested / congested masks (see :func:`fit_triangular_fd` docs)."""
    if uncongested_max_density is not None:
        free = density <= uncongested_max_density
    elif occupancy is not None:
        free = occupancy <= uncongested_max_occupancy
    else:
        # Crude fallback for tables with neither occupancy nor an explicit
        # threshold: take the low-density 30% of points. Pass an explicit
        # threshold for serious use.
        free = density <= np.quantile(density, 0.3)
    v_f = v_f_hint if v_f_hint is not None else np.inf
    rho_c_est = q_max / v_f if np.isfinite(v_f) and v_f > 0 else np.inf
    congested = ~free & (density > rho_c_est)
    return free, congested


def _fit_once(
    density: np.ndarray,
    flow: np.ndarray,
    occupancy: np.ndarray | None,
    *,
    uncongested_max_density: float | None,
    uncongested_max_occupancy: float,
    congested_quantile: float,
    q_max_percentile: float,
    min_points: int,
) -> tuple[float, float, float, float, tuple[int, int]]:
    """One full fit pass → (v_f, w, rho_jam, r2_freeflow, (n_free, n_cong))."""
    q_max = float(np.percentile(flow, q_max_percentile))
    free, _ = _split_branches(
        density, occupancy, None, q_max, uncongested_max_density, uncongested_max_occupancy
    )
    if int(free.sum()) < min_points:
        raise ValueError(f"only {int(free.sum())} uncongested points (< {min_points})")
    rho_f, q_f = density[free], flow[free]
    denom = float(np.sum(rho_f * rho_f))
    if denom <= 0:
        raise ValueError("degenerate free-flow branch (all densities zero)")
    v_f = float(np.sum(rho_f * q_f) / denom)
    if v_f <= 0:
        raise ValueError(f"non-positive free-flow speed {v_f}")
    resid = q_f - v_f * rho_f
    ss_tot = float(np.sum((q_f - q_f.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else 1.0

    _, congested = _split_branches(
        density, occupancy, v_f, q_max, uncongested_max_density, uncongested_max_occupancy
    )
    if int(congested.sum()) < min_points:
        raise ValueError(f"only {int(congested.sum())} congested points (< {min_points})")
    a, w = quantile_line_fit(density[congested], flow[congested], congested_quantile)
    if w >= 0:
        raise ValueError(f"congested-branch slope must be negative, got {w}")
    rho_jam = -a / w
    if rho_jam <= 0:
        raise ValueError(f"non-positive jam density {rho_jam}")
    return v_f, w, rho_jam, r2, (int(free.sum()), int(congested.sum()))


def _bootstrap_worker(
    payload: tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, Any]],
) -> tuple[float, float, float] | None:
    """One bootstrap refit; ``None`` marks a degenerate resample."""
    density, flow, occupancy, fit_kwargs = payload
    try:
        v_f, w, rho_jam, _, _ = _fit_once(density, flow, occupancy, **fit_kwargs)
    except (ValueError, RuntimeError):
        return None
    return v_f, w, rho_jam


def _resample_payloads(
    density: np.ndarray,
    flow: np.ndarray,
    occupancy: np.ndarray | None,
    fit_kwargs: dict[str, Any],
    rng: np.random.Generator,
    n_bootstrap: int,
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, Any]]]:
    """Yield bootstrap resamples lazily, one copy of the data at a time.

    The draws come from ``rng`` in the same order as the pre-generated list
    this replaces, so results are unchanged; only peak memory is
    (``n_bootstrap`` × rows) smaller.
    """
    n = density.shape[0]
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        yield (
            density[idx],
            flow[idx],
            occupancy[idx] if occupancy is not None else None,
            fit_kwargs,
        )


def _batched(
    it: Iterator[tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, Any]]],
    size: int,
) -> Iterator[list[tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, Any]]]]:
    """Group a payload iterator into lists of at most ``size`` payloads."""
    batch: list[tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, Any]]] = []
    for item in it:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def fit_triangular_fd(
    df: pd.DataFrame,
    *,
    created_at: str,
    source: str,
    data_hash: str | None = None,
    uncongested_max_density: float | None = None,
    uncongested_max_occupancy: float = 0.10,
    congested_quantile: float = 0.9,
    q_max_percentile: float = 95.0,
    n_bootstrap: int = 200,
    seed: int = 0,
    min_points: int = 10,
    notes: str = "",
    n_procs: int | None = None,
    bounds: FDBounds | None = DEFAULT_FD_BOUNDS,
    max_fit_rows: int | None = 50_000,
    max_dropped_fraction: float = 0.2,
) -> FDCalibration:
    """Fit a triangular fundamental diagram with bootstrap CIs (§6.1).

    See the module docstring for the method, the input-hygiene rules and the
    plausibility gate. Branch splitting: uncongested points are those with
    ``density <= uncongested_max_density`` when given, else
    ``occupancy <= uncongested_max_occupancy`` when an ``occupancy`` column
    exists, else (crudely) the lowest-density 30% of points. Congested points
    are the non-uncongested points beyond the provisional critical density
    ``q_max / v_f`` — the ambiguous band between the thresholds is
    deliberately excluded from both branch fits.

    Non-physical rows (non-finite, negative density/flow/occupancy, occupancy
    above 100%, zero density with positive flow) are dropped before fitting
    and counted by reason on the artifact; the fitted diagram is then
    range-checked against ``bounds``.

    Args:
        df: Tidy table with columns ``density_veh_m`` [veh/m] and
            ``flow_veh_s`` [veh/s]; optional ``occupancy`` (fraction) — e.g.
            the output of ``calibration.loaders.pems.load_pems_station_csv``.
            The ``occupancy`` column is used (and validity-checked) only when
            ``uncongested_max_density`` is not given, since that is the only
            case in which it affects the fit.
        created_at: ISO-8601 timestamp for the artifact (caller-supplied;
            never auto-generated, per docs/CONTRACTS.md §5).
        source: Human-readable provenance, e.g. ``"PeMS D7 station 717490"``.
        data_hash: Hash of the input data; computed from ``df`` when None.
        uncongested_max_density: Explicit free-branch density cut [veh/m].
        uncongested_max_occupancy: Occupancy cut used when no density cut is
            given (fraction).
        congested_quantile: τ for the congested-branch quantile regression.
        q_max_percentile: Flow percentile defining capacity.
        n_bootstrap: Bootstrap resamples for the 95% CIs (0 disables).
        seed: RNG seed for the row subsample and the bootstrap
            (``flowstate_core.rng``).
        min_points: Minimum points required on each branch.
        notes: Free-text note stored on the artifact.
        n_procs: Bootstrap resamples are refitted in a process pool of this
            size when > 1. The resample index draws are made from the seeded
            generator in the same order as the serial path, and each refit is
            deterministic, so the result is identical to ``n_procs=None``;
            only wall-clock changes (each exact-LP refit costs minutes on
            10^5-bin data sets).
        bounds: Plausibility ranges for the fitted diagram
            (:class:`FDBounds`); ``None`` disables the check — do that only
            for screening fits that are never quoted as a corridor
            calibration, and say so in ``notes``.
        max_fit_rows: Cap on the rows fitted. When the usable rows exceed it,
            a subsample of exactly this many rows is drawn *without
            replacement* from ``make_rng(seed)`` — before any bootstrap draw,
            so the bootstrap is unaffected when the cap does not fire — and
            kept in input order; the raw and retained counts are recorded on
            the artifact and in ``notes``. ``None`` disables the cap, at the
            cost of a congested-branch LP that is quadratic in rows.
        max_dropped_fraction: Refuse the fit when more than this share of
            input rows is non-physical (a whole-file unit or sentinel
            problem, rather than a few failed detector intervals).

    Returns:
        ``FDCalibration`` artifact with the fitted ``TriangularFD`` (CIs for
        ``v_f``, ``w``, ``rho_jam``, ``rho_c``, ``q_max``), the input ranges,
        the dropped-row counts and diagnostics.

    Raises:
        ValueError: On missing columns, too many non-physical rows, a
            degenerate fit, or a fitted diagram outside ``bounds``.
    """
    for col in ("density_veh_m", "flow_veh_s"):
        if col not in df.columns:
            raise ValueError(f"fit_triangular_fd: missing column {col!r}")
    if max_fit_rows is not None and max_fit_rows < 2 * min_points:
        raise ValueError(f"max_fit_rows must be >= 2*min_points, got {max_fit_rows}")
    if not 0.0 <= max_dropped_fraction <= 1.0:
        raise ValueError(f"max_dropped_fraction must be in [0, 1], got {max_dropped_fraction}")
    density = df["density_veh_m"].to_numpy(dtype=float)
    flow = df["flow_veh_s"].to_numpy(dtype=float)
    # The occupancy column only ever affects the fit through the free/congested
    # split, which an explicit density cut overrides — drop it in that case so
    # it is neither checked nor carried through the bootstrap payloads.
    occupancy = (
        df["occupancy"].to_numpy(dtype=float)
        if "occupancy" in df.columns and uncongested_max_density is None
        else None
    )

    n_input = int(density.shape[0])
    valid, dropped = _row_validity(density, flow, occupancy)
    n_dropped = int(n_input - valid.sum())
    if n_input > 0 and n_dropped > max_dropped_fraction * n_input:
        raise ValueError(
            f"fit_triangular_fd: {n_dropped}/{n_input} rows are non-physical "
            f"({dropped}), above max_dropped_fraction={max_dropped_fraction}; "
            f"{_UNIT_HINT_FLOW}, {_UNIT_HINT_DENSITY}"
        )
    density, flow = density[valid], flow[valid]
    if occupancy is not None:
        occupancy = occupancy[valid]
    input_ranges = {
        "density_veh_m_min": float(density.min()) if density.size else float("nan"),
        "density_veh_m_max": float(density.max()) if density.size else float("nan"),
        "flow_veh_s_min": float(flow.min()) if flow.size else float("nan"),
        "flow_veh_s_max": float(flow.max()) if flow.size else float("nan"),
    }

    # One stream for both the subsample and the bootstrap: the subsample draw
    # comes first, and is skipped entirely when the cap does not fire, so an
    # uncapped fit draws exactly the resamples it drew before the cap existed.
    rng = make_rng(seed)
    n_usable = int(density.shape[0])
    subsampled = max_fit_rows is not None and n_usable > max_fit_rows
    if max_fit_rows is not None and subsampled:
        keep = np.sort(rng.choice(n_usable, size=max_fit_rows, replace=False))
        density, flow = density[keep], flow[keep]
        if occupancy is not None:
            occupancy = occupancy[keep]

    fit_kwargs = dict(
        uncongested_max_density=uncongested_max_density,
        uncongested_max_occupancy=uncongested_max_occupancy,
        congested_quantile=congested_quantile,
        q_max_percentile=q_max_percentile,
        min_points=min_points,
    )
    v_f, w, rho_jam, r2, (n_free, n_cong) = _fit_once(density, flow, occupancy, **fit_kwargs)
    if bounds is not None:
        check_fd_plausible(TriangularFD(v_f=v_f, w=w, rho_jam=rho_jam), bounds=bounds)

    ci95: dict[str, tuple[float, float]] = {}
    n_ok = 0
    if n_bootstrap > 0:
        samples: dict[str, list[float]] = {k: [] for k in ("v_f", "w", "rho_jam", "rho_c", "q_max")}
        payloads = _resample_payloads(density, flow, occupancy, fit_kwargs, rng, n_bootstrap)
        results: list[tuple[float, float, float] | None] = []
        if n_procs is not None and n_procs > 1:
            ctx = multiprocessing.get_context("spawn")
            n_workers = min(n_procs, n_bootstrap)
            with ctx.Pool(processes=n_workers) as pool:
                # Bounded batches: at most ``_BOOTSTRAP_BATCH`` resampled
                # copies of the data exist per worker at any time, where
                # ``pool.map`` over the whole run would hold all n_bootstrap.
                for batch in _batched(payloads, _BOOTSTRAP_BATCH * n_workers):
                    results.extend(pool.map(_bootstrap_worker, batch))
        else:
            results = [_bootstrap_worker(pl) for pl in payloads]
        for res in results:
            if res is None:
                continue  # degenerate resample (e.g. congested branch too thin)
            bv, bw, brho = res
            bfd = TriangularFD(v_f=bv, w=bw, rho_jam=brho)
            samples["v_f"].append(bv)
            samples["w"].append(bw)
            samples["rho_jam"].append(brho)
            samples["rho_c"].append(bfd.rho_c)
            samples["q_max"].append(bfd.q_max)
            n_ok += 1
        if n_ok >= max(20, n_bootstrap // 4):
            for key, vals in samples.items():
                lo, hi = np.percentile(vals, [2.5, 97.5])
                ci95[key] = (float(lo), float(hi))

    fd = TriangularFD(v_f=v_f, w=w, rho_jam=rho_jam, ci95=ci95)
    row_note = f"rows: {n_input} input, {int(density.shape[0])} fitted"
    if n_dropped:
        row_note += f", {n_dropped} dropped as non-physical ({dropped})"
    if subsampled:
        row_note += f", subsampled from {n_usable} usable (seeded, seed {seed})"
    boot_note = f"{row_note}. bootstrap: {n_ok}/{n_bootstrap} resamples usable (seed {seed})."
    return FDCalibration(
        created_at=created_at,
        source=source,
        data_hash=data_hash if data_hash is not None else _hash_dataframe(df),
        fd=fd,
        n_observations=int(density.shape[0]),
        n_rows_input=n_input,
        dropped_rows=dropped,
        input_ranges=input_ranges,
        branch_counts={"free": n_free, "congested": n_cong},
        r2_freeflow=float(r2),
        congested_quantile=congested_quantile,
        notes=(notes + " " if notes else "") + boot_note,
    )
