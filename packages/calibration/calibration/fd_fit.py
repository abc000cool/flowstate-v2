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
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

from flowstate_core.artifacts import FDCalibration, TriangularFD
from flowstate_core.rng import make_rng


def _hash_dataframe(df: pd.DataFrame) -> str:
    """Deterministic sha256 hex digest of a dataframe's contents."""
    h = hashlib.sha256()
    h.update(",".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


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
) -> tuple[float, float, float, float]:
    """One full fit pass → (v_f, w, rho_jam, r2_freeflow)."""
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
    return v_f, w, rho_jam, r2


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
) -> FDCalibration:
    """Fit a triangular fundamental diagram with bootstrap CIs (§6.1).

    See the module docstring for the method. Branch splitting: uncongested
    points are those with ``density <= uncongested_max_density`` when given,
    else ``occupancy <= uncongested_max_occupancy`` when an ``occupancy``
    column exists, else (crudely) the lowest-density 30% of points. Congested
    points are the non-uncongested points beyond the provisional critical
    density ``q_max / v_f`` — the ambiguous band between the thresholds is
    deliberately excluded from both branch fits.

    Args:
        df: Tidy table with columns ``density_veh_m`` [veh/m] and
            ``flow_veh_s`` [veh/s]; optional ``occupancy`` (fraction) — e.g.
            the output of ``calibration.loaders.pems.load_pems_station_csv``.
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
        seed: RNG seed for the bootstrap (``flowstate_core.rng``).
        min_points: Minimum points required on each branch.
        notes: Free-text note stored on the artifact.

    Returns:
        ``FDCalibration`` artifact with the fitted ``TriangularFD`` (CIs for
        ``v_f``, ``w``, ``rho_jam``, ``rho_c``, ``q_max``) and diagnostics.

    Raises:
        ValueError: On missing columns or a degenerate fit.
    """
    for col in ("density_veh_m", "flow_veh_s"):
        if col not in df.columns:
            raise ValueError(f"fit_triangular_fd: missing column {col!r}")
    density = df["density_veh_m"].to_numpy(dtype=float)
    flow = df["flow_veh_s"].to_numpy(dtype=float)
    occupancy = df["occupancy"].to_numpy(dtype=float) if "occupancy" in df.columns else None
    finite = np.isfinite(density) & np.isfinite(flow)
    density, flow = density[finite], flow[finite]
    if occupancy is not None:
        occupancy = occupancy[finite]

    fit_kwargs = dict(
        uncongested_max_density=uncongested_max_density,
        uncongested_max_occupancy=uncongested_max_occupancy,
        congested_quantile=congested_quantile,
        q_max_percentile=q_max_percentile,
        min_points=min_points,
    )
    v_f, w, rho_jam, r2 = _fit_once(density, flow, occupancy, **fit_kwargs)

    ci95: dict[str, tuple[float, float]] = {}
    n_ok = 0
    if n_bootstrap > 0:
        rng = make_rng(seed)
        n = density.shape[0]
        samples: dict[str, list[float]] = {k: [] for k in ("v_f", "w", "rho_jam", "rho_c", "q_max")}
        for _ in range(n_bootstrap):
            idx = rng.integers(0, n, size=n)
            try:
                bv, bw, brho, _ = _fit_once(
                    density[idx],
                    flow[idx],
                    occupancy[idx] if occupancy is not None else None,
                    **fit_kwargs,
                )
            except (ValueError, RuntimeError):
                continue  # degenerate resample (e.g. congested branch too thin)
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
    boot_note = f"bootstrap: {n_ok}/{n_bootstrap} resamples usable (seed {seed})."
    return FDCalibration(
        created_at=created_at,
        source=source,
        data_hash=data_hash if data_hash is not None else _hash_dataframe(df),
        fd=fd,
        n_observations=int(density.shape[0]),
        r2_freeflow=float(r2),
        congested_quantile=congested_quantile,
        notes=(notes + " " if notes else "") + boot_note,
    )
