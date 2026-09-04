"""Tracking-coverage estimators for fragmentary trajectory data (CLAUDE.md §6).

Vision-based trajectory instruments such as I-24 MOTION (Gloudemans et al.
2023, *Transp. Res. C* 155:104311) deliver trajectory *fragments* and miss a
share of the vehicles on the road (occlusion, camera boundaries, tracker
breaks). Every count, flow and density computed from such data is therefore
a lower bound scaled by the local **tracking coverage** ``c`` — the fraction
of vehicle-time (equivalently, of vehicles present at an instant) that the
instrument tracked — while speeds are ratios of tracked quantities and are
coverage-robust. This module estimates ``c`` from the data itself so that
demand inputs derived from fragment counts can be corrected without relying
on a car-following equilibrium.

All estimators are pure functions over per-window, per-lane statistics
(plain arrays and floats) so they can be validated on synthetic data with a
known ``c`` (:func:`synthetic_validation`). Four estimators are provided:

1. :func:`coverage_equilibrium` — the model-dependent method used by
   ``scripts/i24_build_replica.py``: tracked Edie density over the density a
   calibrated IDM population would hold at the observed Edie speed.
2. :func:`coverage_gap_moments` and :func:`fit_gap_mixture` /
   :func:`coverage_gap_mixture` — the **random-thinning model**: if each
   vehicle is tracked independently with probability ``c``, an observed
   spacing between consecutive tracked vehicles in a lane is the sum of
   ``K`` true spacings with ``K`` geometric. The first two moments give a
   closed form for ``c`` given the true spacing's coefficient of variation;
   a maximum-likelihood fit of the geometric-gamma mixture estimates ``c``
   and the true spacing distribution jointly.
3. :func:`coverage_capacity_bound` — the lower bound on ``c`` implied by a
   flow ceiling, and :func:`combine_with_bound` (``c = max(estimate,
   bound)``).
4. :func:`coverage_section_crossings` — the coverage that applies to a
   *crossing count* at a section (the demand input), which differs from the
   vehicle-time coverage when fragments break near the section.

Random-thinning derivation (used by 2 and 4)
--------------------------------------------

Let the true front-to-front spacings along a lane at one instant be
``S_1, S_2, …``, independent with mean ``μ`` and variance ``σ²``
(``cv_true = σ/μ``). Each vehicle is tracked independently with probability
``c``. Between two consecutive *tracked* vehicles lie ``K − 1`` untracked
ones with ``P(K = k) = c (1 − c)^(k−1)``, ``k ≥ 1`` (geometric),
``E K = 1/c``, ``Var K = (1 − c)/c²``. Front-to-front spacings telescope, so
the observed spacing is the random sum ``Y = S_1 + … + S_K`` with ``K``
independent of the ``S_i``. Wald's identities give::

    E Y   = μ / c
    Var Y = σ²/c + (1 − c) μ²/c²
    cv_obs² = Var Y / (E Y)² = c·cv_true² + (1 − c) = 1 − c (1 − cv_true²)

hence the **moment estimator**::

    c = (1 − cv_obs²) / (1 − cv_true²)                                (M)

which needs ``cv_true < 1``: a renewal process with exponential increments
thinned at random is again Poisson, so the moments then carry no
information about ``c``. Real congested spacings are far more regular than
exponential (``cv_true ≈ 0.3–0.5``); free-flow spacings are not, which is
why (M) and the mixture fit are only trustworthy in congestion.

If ``S ~ Gamma(α, θ)`` (``μ = αθ``, ``cv_true = α^(−1/2)``), the ``k``-fold
sum is ``Gamma(kα, θ)`` and the observed spacing density is the
**geometric-gamma mixture**::

    f_Y(y) = Σ_{k≥1} c (1 − c)^(k−1) · Gamma(y; kα, θ)                (G)

whose maximum-likelihood fit over ``(c, α, θ)`` uses the whole shape of the
observed distribution (the ``K = 1`` component is the "short-gap mode" that
carries the true spacing distribution). Spacings above ``s_max`` enter as
right-censored observations through the mixture's survival function, so
tracking holes (which produce spacings no geometric sum would) cannot pull
the fit arbitrarily; spacings below ``s_dup`` are duplicate fragments of one
vehicle (a documented homography artifact) and are removed and counted.

Capacity bound (3)
------------------

``q_tracked = c · q_true`` and ``q_true ≤ q_cap`` give ``c ≥ q_tracked /
q_cap``. The bound is valid only if ``q_cap`` is at or above the true flow.
A fundamental diagram fitted to the *tracked* data has a coverage-limited
``q_max`` (a lower bound on facility capacity), so using it as ``q_cap`` does
not yield a guaranteed bound; it encodes the weaker operational statement
"the corrected flow shall not exceed the flow the calibrated fleet can
carry" (docs/I24_CAPACITY.md). A physical ceiling (HCM basic-freeway
capacity) yields a valid but weaker bound.

Section coverage (4)
--------------------

A crossing count ``N`` at section ``x_s`` over a window of length ``Δt``
counts vehicles whose fragment *spans* ``x_s``; fragments that break at
``x_s`` are lost even though their vehicle-time is tracked on either side.
On a ramp-free stretch containing ``x_s`` with tracked Edie flow ``q_loc``
and vehicle-time coverage ``c_loc`` the true flow is ``q_loc / c_loc``
(flow conservation up to the travel-time lag), so the coverage that applies
to the count is::

    c_sec = N / (Δt · q_loc / c_loc) = c_loc · N / (Δt · q_loc)         (S)

References:
    Edie, L. C. (1963). Discussion of traffic stream measurements and
    definitions. Proc. 2nd Int. Symp. Theory of Traffic Flow, 139–154.
    Treiber, M. & Kesting, A. (2013). *Traffic Flow Dynamics*, ch. 11
    (IDM equilibrium), ch. 4 (fundamental diagram scatter).
    Gloudemans, D. et al. (2023). I-24 MOTION: An instrument for freeway
    traffic science. *Transp. Res. C* 155:104311.
    HCM (2016). *Highway Capacity Manual*, 6th ed., ch. 12 (basic freeway
    segment capacity 2,250–2,400 pc/h/ln by free-flow speed).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import expit, gammaincc, gammaln, logit, logsumexp

from flowstate_core.rng import make_rng

FloatArray = NDArray[np.float64]

DEFAULT_VEHICLE_LENGTH_M: float = 5.0
"""Vehicle length [m] used with the IDM equilibrium gap (the 5 m vType)."""

DEFAULT_S_DUP_M: float = 4.0
"""Spacings below this [m] cannot separate two distinct vehicles (a vehicle
is longer than that); they are duplicate fragments of one vehicle."""

DEFAULT_S_MAX_M: float = 250.0
"""Right-censoring point [m] for the mixture fit: a spacing above it in a
congested speed class is a tracking hole or a very long geometric sum, and
only its exceedance is used."""

DEFAULT_SPEED_EDGES_KMH: tuple[float, ...] = (0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0)
"""Speed-class edges [km/h] for the per-class mixture fits; pairs at or
above the last edge form an open class."""

HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN: float = 2400.0
"""HCM 6th ed. basic freeway segment capacity [pc/h/ln] at ≥ 70 mi/h
free-flow speed — the largest tabulated value, hence an upper envelope for
a physical ceiling (heavy vehicles only reduce it)."""


# --------------------------------------------------------------------------
# Spacing extraction
# --------------------------------------------------------------------------


def snapshot_spacings(
    t: FloatArray,
    x: FloatArray,
    v: FloatArray,
    *,
    sample_dt: float,
    snapshot_dt: float,
) -> tuple[FloatArray, FloatArray]:
    """Front-to-front spacings between consecutive tracked vehicles in a lane.

    Rows are trajectory samples of one lane on a shared time grid of step
    ``sample_dt``. Every ``snapshot_dt`` a snapshot is taken: the vehicles
    present at that grid slot are ordered by position and the differences
    of consecutive positions are the observed spacings. Consecutive
    snapshots repeat pairs, so spacings are autocorrelated in time; the
    estimators here use them for distribution shape, not for standard
    errors.

    Args:
        t: Sample times [s] on the grid (multiples of ``sample_dt``).
        x: Front-bumper positions [m], travel oriented (leader has larger x).
        v: Speeds [m/s].
        sample_dt: Grid step [s].
        snapshot_dt: Snapshot interval [s]; must be a multiple of
            ``sample_dt``.

    Returns:
        ``(spacings, pair_speed)``: spacings [m] (``x_leader − x_follower``,
        may include duplicates near 0) and the mean speed of each pair
        [m/s].

    Raises:
        ValueError: On mismatched lengths or an invalid snapshot interval.
    """
    t = np.asarray(t, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if not (t.shape == x.shape == v.shape):
        raise ValueError("t, x, v must have the same shape")
    if sample_dt <= 0 or snapshot_dt <= 0:
        raise ValueError("sample_dt and snapshot_dt must be > 0")
    step = round(snapshot_dt / sample_dt)
    if step < 1 or abs(step * sample_dt - snapshot_dt) > 1e-6 * sample_dt:
        raise ValueError("snapshot_dt must be a positive multiple of sample_dt")
    if t.size == 0:
        return np.zeros(0), np.zeros(0)
    k = np.rint(t / sample_dt).astype(np.int64)
    sel = (k % step) == 0
    k, x, v = k[sel], x[sel], v[sel]
    order = np.lexsort((x, k))
    k, x, v = k[order], x[order], v[order]
    same = k[1:] == k[:-1]
    spacings = (x[1:] - x[:-1])[same]
    pair_speed = 0.5 * (v[1:] + v[:-1])[same]
    return spacings, pair_speed


# --------------------------------------------------------------------------
# 1. Equilibrium (model-dependent) estimator
# --------------------------------------------------------------------------


def idm_equilibrium_density(
    v: float, idm_mean: dict[str, float], vehicle_length: float = DEFAULT_VEHICLE_LENGTH_M
) -> float:
    """IDM equilibrium density [veh/m] at speed ``v`` for mean parameters.

    ``ρ_eq(v) = 1 / (s_eq(v) + L)`` with ``s_eq = (s0 + v T) / sqrt(1 −
    (v/v0)^4)`` (Treiber & Kesting 2013, eq. 11.9; CLAUDE.md §9).

    Args:
        v: Speed [m/s], must satisfy ``0 ≤ v < v0``.
        idm_mean: Mapping with ``v0``, ``T``, ``s0`` (SI).
        vehicle_length: Vehicle length [m].

    Returns:
        Equilibrium density [veh/m].

    Raises:
        ValueError: If ``v`` is outside ``[0, v0)``.
    """
    v0, big_t, s0 = float(idm_mean["v0"]), float(idm_mean["T"]), float(idm_mean["s0"])
    if not 0.0 <= v < v0:
        raise ValueError(f"v must be in [0, v0={v0}), got {v}")
    s_eq = (s0 + v * big_t) / math.sqrt(1.0 - (v / v0) ** 4)
    return 1.0 / (s_eq + vehicle_length)


def coverage_equilibrium(
    rho_tracked: float,
    v: float,
    idm_mean: dict[str, float],
    *,
    vehicle_length: float = DEFAULT_VEHICLE_LENGTH_M,
    v_ratio_max: float = 0.9,
) -> float:
    """Coverage as tracked density over the IDM equilibrium density.

    The method of ``scripts/i24_build_replica.py::coverage_factors``: the
    Edie speed is coverage-robust, so ``ρ_tracked / ρ_eq(v)`` isolates the
    share of vehicle-time tracked *if* the traffic sits at the calibrated
    population's equilibrium spacing at that speed. It is model-dependent
    (population form, vehicle length, non-equilibrium hysteresis in
    stop-and-go) and undefined in free flow where spacing is not
    speed-determined.

    Args:
        rho_tracked: Tracked Edie density [veh/m] (per lane).
        v: Edie speed [m/s].
        idm_mean: Mean IDM parameters (``v0``, ``T``, ``s0``).
        vehicle_length: Vehicle length [m].
        v_ratio_max: Windows with ``v ≥ v_ratio_max · v0`` are out of the
            congested regime and return ``NaN``.

    Returns:
        Coverage clipped to ``(0, 1]``, or ``NaN`` outside the regime.
    """
    if not (math.isfinite(v) and math.isfinite(rho_tracked)) or rho_tracked <= 0.0:
        return math.nan
    if v < 0.0 or v >= v_ratio_max * float(idm_mean["v0"]):
        return math.nan
    rho_eq = idm_equilibrium_density(v, idm_mean, vehicle_length)
    return float(min(max(rho_tracked / rho_eq, 1e-3), 1.0))


# --------------------------------------------------------------------------
# 2. Random-thinning (gap) estimators
# --------------------------------------------------------------------------


def _clean_spacings(spacings: FloatArray, s_dup: float) -> tuple[FloatArray, int]:
    """Finite spacings at or above ``s_dup``; returns (kept, n_duplicates)."""
    s = np.asarray(spacings, dtype=np.float64)
    s = s[np.isfinite(s)]
    dup = s < s_dup
    return s[~dup], int(dup.sum())


def coverage_gap_moments(
    spacings: FloatArray,
    cv_true: float,
    *,
    s_dup: float = DEFAULT_S_DUP_M,
    s_max: float | None = None,
) -> float:
    """Moment estimator (M) of the thinning probability from observed spacings.

    ``c = (1 − cv_obs²) / (1 − cv_true²)`` from the module derivation, with
    ``cv_obs`` the coefficient of variation of the observed spacings after
    removing duplicates and (optionally) winsorizing at ``s_max`` — the
    variance is otherwise dominated by tracking holes.

    Args:
        spacings: Observed spacings [m] (one lane, one window, ideally one
            speed class so the true spacing scale is homogeneous).
        cv_true: Assumed coefficient of variation of the true spacings
            (``< 1``); e.g. ``alpha ** -0.5`` from :func:`fit_gap_mixture`.
        s_dup: Duplicate threshold [m].
        s_max: Winsorize spacings above this [m]; ``None`` for none.

    Returns:
        Coverage clipped to ``[0, 1]``; ``NaN`` with fewer than 2 spacings.

    Raises:
        ValueError: If ``cv_true`` is not in ``[0, 1)``.
    """
    if not 0.0 <= cv_true < 1.0:
        raise ValueError(f"cv_true must be in [0, 1), got {cv_true}")
    s, _ = _clean_spacings(spacings, s_dup)
    if s.size < 2:
        return math.nan
    if s_max is not None:
        s = np.minimum(s, s_max)
    mean = float(s.mean())
    if mean <= 0.0:
        return math.nan
    cv_obs2 = float(s.var()) / mean**2
    return float(min(max((1.0 - cv_obs2) / (1.0 - cv_true**2), 0.0), 1.0))


@dataclass
class GapMixtureFit:
    """Maximum-likelihood fit of the geometric-gamma mixture (G).

    Attributes:
        c: Estimated thinning probability (tracking coverage).
        alpha: Gamma shape of the true spacing (``cv_true = alpha^-1/2``).
        theta: Gamma scale [m] of the true spacing.
        mean_true_m: True mean spacing ``αθ`` [m]; ``1 / mean_true_m`` is
            the implied true density.
        cv_true: Coefficient of variation of the true spacing.
        n: Spacings used (exact + censored) after duplicate removal.
        n_censored: Spacings above ``s_max`` entered as censored.
        n_duplicates: Spacings below ``s_dup`` removed.
        nll: Negative log-likelihood at the optimum.
        converged: Optimizer success flag.
        at_bound: True if ``c`` sits at the parameter box.
        mean_obs_m: Mean observed spacing [m] (winsorized at ``s_max``).
        cv_obs: Observed coefficient of variation (winsorized).
    """

    c: float
    alpha: float
    theta: float
    mean_true_m: float
    cv_true: float
    n: int
    n_censored: int
    n_duplicates: int
    nll: float
    converged: bool
    at_bound: bool
    mean_obs_m: float
    cv_obs: float


_C_BOUNDS = (0.02, 0.995)
_ALPHA_BOUNDS = (0.3, 400.0)
_THETA_BOUNDS = (1e-3, 1e4)


def _mixture_nll(
    params: FloatArray,
    y: FloatArray,
    log_y: FloatArray,
    w: FloatArray,
    n_cens: float,
    s_max: float,
    k_max: int,
) -> float:
    """Weighted negative log-likelihood of (G) with right censoring."""
    c = float(expit(params[0]))
    alpha = math.exp(params[1])
    theta = math.exp(params[2])
    k = np.arange(1, k_max + 1, dtype=np.float64)
    log_w = math.log(c) + (k - 1.0) * math.log1p(-c) - math.log1p(-((1.0 - c) ** k_max))
    a = k * alpha
    log_pdf = (
        (a - 1.0)[None, :] * log_y[:, None]
        - (y / theta)[:, None]
        - (a * math.log(theta))[None, :]
        - gammaln(a)[None, :]
    )
    ll = float(np.dot(w, logsumexp(log_w[None, :] + log_pdf, axis=1)))
    if n_cens > 0:
        sf = float(np.dot(np.exp(log_w), gammaincc(a, s_max / theta)))
        ll += n_cens * math.log(max(sf, 1e-300))
    return -ll


def _bin_spacings(s: FloatArray, bin_m: float) -> tuple[FloatArray, FloatArray]:
    """Grouped-data representation: bin centres and counts (``bin_m`` wide)."""
    idx = np.floor(s / bin_m).astype(np.int64)
    uniq, counts = np.unique(idx, return_counts=True)
    centres = (uniq.astype(np.float64) + 0.5) * bin_m
    return centres, counts.astype(np.float64)


def fit_gap_mixture(
    spacings: FloatArray,
    *,
    s_dup: float = DEFAULT_S_DUP_M,
    s_max: float = DEFAULT_S_MAX_M,
    k_max: int = 40,
    bin_m: float = 0.25,
    c_starts: Sequence[float] = (0.3, 0.6, 0.9),
    min_n: int = 100,
) -> GapMixtureFit:
    """Fit the geometric-gamma mixture (G) to observed spacings by MLE.

    Spacings below ``s_dup`` are removed as duplicates; spacings at or above
    ``s_max`` are right-censored. The exact spacings are grouped into
    ``bin_m`` bins (grouped-data likelihood) so the cost is independent of
    the sample size; the geometric weights are truncated at ``k_max``
    components and renormalized. Several starts in ``c`` guard against local
    optima; the best negative log-likelihood wins.

    Args:
        spacings: Observed spacings [m].
        s_dup: Duplicate threshold [m].
        s_max: Censoring point [m].
        k_max: Number of mixture components.
        bin_m: Grouping width [m] for the exact spacings.
        c_starts: Initial coverage values.
        min_n: Minimum usable spacings; fewer returns a ``NaN`` fit.

    Returns:
        A :class:`GapMixtureFit`.

    Raises:
        ValueError: On invalid thresholds.
    """
    if not 0.0 <= s_dup < s_max:
        raise ValueError(f"need 0 <= s_dup < s_max, got {s_dup}, {s_max}")
    if k_max < 2 or bin_m <= 0:
        raise ValueError("k_max must be >= 2 and bin_m > 0")
    s, n_dup = _clean_spacings(spacings, s_dup)
    n_total = int(s.size)
    empty = GapMixtureFit(
        c=math.nan,
        alpha=math.nan,
        theta=math.nan,
        mean_true_m=math.nan,
        cv_true=math.nan,
        n=n_total,
        n_censored=int((s >= s_max).sum()),
        n_duplicates=n_dup,
        nll=math.nan,
        converged=False,
        at_bound=False,
        mean_obs_m=math.nan,
        cv_obs=math.nan,
    )
    if n_total < min_n:
        return empty
    wins = np.minimum(s, s_max)
    mean_obs = float(wins.mean())
    cv_obs = float(wins.std() / mean_obs) if mean_obs > 0 else math.nan
    exact = s[s < s_max]
    n_cens = float(n_total - exact.size)
    if exact.size < 2:
        return empty
    y, w = _bin_spacings(exact, bin_m)
    log_y = np.log(y)
    # The K = 1 component holds the lower part of the distribution; start
    # its mean at a low quantile and its shape moderately regular.
    mu0 = float(np.quantile(exact, 0.3))
    alpha0 = 8.0
    bounds = [
        (logit(_C_BOUNDS[0]), logit(_C_BOUNDS[1])),
        (math.log(_ALPHA_BOUNDS[0]), math.log(_ALPHA_BOUNDS[1])),
        (math.log(_THETA_BOUNDS[0]), math.log(_THETA_BOUNDS[1])),
    ]
    best: Any = None
    for c0 in c_starts:
        x0 = np.array([logit(c0), math.log(alpha0), math.log(mu0 / alpha0)])
        res = minimize(
            _mixture_nll,
            x0,
            args=(y, log_y, w, n_cens, s_max, k_max),
            method="L-BFGS-B",
            bounds=bounds,
        )
        if best is None or res.fun < best.fun:
            best = res
    c = float(expit(best.x[0]))
    alpha = math.exp(best.x[1])
    theta = math.exp(best.x[2])
    at_bound = c <= _C_BOUNDS[0] * 1.02 or c >= _C_BOUNDS[1] * 0.999
    return GapMixtureFit(
        c=c,
        alpha=alpha,
        theta=theta,
        mean_true_m=alpha * theta,
        cv_true=alpha**-0.5,
        n=n_total,
        n_censored=int(n_cens),
        n_duplicates=n_dup,
        nll=float(best.fun),
        converged=bool(best.success),
        at_bound=at_bound,
        mean_obs_m=mean_obs,
        cv_obs=cv_obs,
    )


@dataclass
class GapMixtureResult:
    """Speed-class-resolved mixture estimate for one lane and window.

    Attributes:
        c: Coverage: per-class ``c`` weighted by the number of spacings in
            each usable class (≈ tracked vehicle-time share).
        c_min: Smallest per-class ``c`` among usable classes.
        c_max: Largest per-class ``c`` among usable classes.
        n_used: Spacings in usable classes.
        n_total: Spacings offered (after duplicate removal).
        classes: One record per class (edges, fit fields).
    """

    c: float
    c_min: float
    c_max: float
    n_used: int
    n_total: int
    classes: list[dict[str, Any]] = field(default_factory=list)


def coverage_gap_mixture(
    spacings: FloatArray,
    pair_speed: FloatArray,
    *,
    speed_edges_ms: Sequence[float],
    min_n: int = 300,
    v_max_ms: float | None = None,
    **fit_kwargs: Any,
) -> GapMixtureResult:
    """Mixture coverage per speed class, combined by spacing count.

    Conditioning on the pair's mean speed keeps the true spacing scale
    roughly homogeneous within a class (spacing grows with speed), which
    the i.i.d. assumption of (G) needs. Classes with fewer than ``min_n``
    spacings, non-converged fits, or fits at the box boundary are excluded
    from the combination but reported.

    Args:
        spacings: Observed spacings [m].
        pair_speed: Mean speed of each pair [m/s].
        speed_edges_ms: Class edges [m/s]; the last class is open-ended.
        min_n: Minimum spacings per class.
        v_max_ms: Ignore pairs faster than this (free flow, where the
            model is uninformative); ``None`` keeps all.
        **fit_kwargs: Passed to :func:`fit_gap_mixture`.

    Returns:
        A :class:`GapMixtureResult`.
    """
    s = np.asarray(spacings, dtype=np.float64)
    vv = np.asarray(pair_speed, dtype=np.float64)
    if s.shape != vv.shape:
        raise ValueError("spacings and pair_speed must have the same shape")
    edges = [float(e) for e in speed_edges_ms] + [math.inf]
    records: list[dict[str, Any]] = []
    num = 0.0
    n_used = 0
    n_total = 0
    c_vals: list[float] = []
    for lo, hi in pairwise(edges):
        m = (vv >= lo) & (vv < hi)
        if v_max_ms is not None:
            m &= vv < v_max_ms
        fit = fit_gap_mixture(s[m], min_n=min_n, **fit_kwargs)
        n_total += fit.n
        usable = fit.converged and not fit.at_bound and math.isfinite(fit.c) and fit.n >= min_n
        rec = {"v_lo_ms": lo, "v_hi_ms": hi if math.isfinite(hi) else None, "usable": usable}
        rec.update(asdict(fit))
        records.append(rec)
        if usable:
            num += fit.c * fit.n
            n_used += fit.n
            c_vals.append(fit.c)
    c = num / n_used if n_used > 0 else math.nan
    return GapMixtureResult(
        c=c,
        c_min=min(c_vals) if c_vals else math.nan,
        c_max=max(c_vals) if c_vals else math.nan,
        n_used=n_used,
        n_total=n_total,
        classes=records,
    )


# --------------------------------------------------------------------------
# 3. Capacity bound and combination
# --------------------------------------------------------------------------


def coverage_capacity_bound(q_tracked: float, q_cap: float) -> float:
    """Lower bound ``c ≥ q_tracked / q_cap`` from a flow ceiling.

    Args:
        q_tracked: Tracked flow (any consistent unit).
        q_cap: Flow ceiling in the same unit; must be ``> 0``.

    Returns:
        The bound clipped to ``[0, 1]``; ``NaN`` for a non-finite input.

    Raises:
        ValueError: If ``q_cap <= 0``.
    """
    if q_cap <= 0.0:
        raise ValueError(f"q_cap must be > 0, got {q_cap}")
    if not math.isfinite(q_tracked):
        return math.nan
    return float(min(max(q_tracked / q_cap, 0.0), 1.0))


def combine_with_bound(c_estimate: float, c_bound: float) -> float:
    """Combination rule ``c = max(estimate, bound)``.

    A ``NaN`` estimate yields the bound and vice versa; both ``NaN`` gives
    ``NaN``.
    """
    vals = [v for v in (c_estimate, c_bound) if math.isfinite(v)]
    return max(vals) if vals else math.nan


# --------------------------------------------------------------------------
# 4. Section-crossing coverage
# --------------------------------------------------------------------------


def coverage_section_crossings(
    n_crossings: float, window_s: float, q_local_tracked: float, c_local: float
) -> float:
    """Coverage applying to a crossing count at a section, eq. (S).

    Args:
        n_crossings: Tracked fragment crossings of the section in the window.
        window_s: Window length [s].
        q_local_tracked: Tracked Edie flow [veh/s] on a ramp-free stretch
            containing the section.
        c_local: Vehicle-time coverage on that stretch.

    Returns:
        ``c_local · n_crossings / (window_s · q_local_tracked)`` clipped to
        ``(0, 1]``; ``NaN`` if any input is non-positive or non-finite.
    """
    vals = (n_crossings, window_s, q_local_tracked, c_local)
    if not all(math.isfinite(v) for v in vals) or window_s <= 0 or q_local_tracked <= 0:
        return math.nan
    if c_local <= 0 or n_crossings < 0:
        return math.nan
    return float(min(max(c_local * n_crossings / (window_s * q_local_tracked), 1e-3), 1.0))


# --------------------------------------------------------------------------
# Synthetic validation
# --------------------------------------------------------------------------


def thin_positions(
    true_spacings: FloatArray,
    c: float,
    rng: np.random.Generator,
    *,
    run_length: float = 1.0,
) -> FloatArray:
    """Observed spacings after thinning a lane of vehicles.

    Vehicles sit at the cumulative sums of ``true_spacings``. With
    ``run_length == 1`` each vehicle is kept independently with probability
    ``c`` (the model of the derivation). With ``run_length > 1`` the
    tracked/untracked state is a two-state Markov chain with the same
    marginal ``c`` whose mean untracked run is ``run_length`` vehicles —
    correlated losses (a platoon lost together under an overpass), which
    violate the geometric assumption and let the tests show the bias.

    Args:
        true_spacings: True front-to-front spacings [m], ``> 0``.
        c: Marginal tracking probability in ``(0, 1]``.
        rng: Seeded generator.
        run_length: Mean length of untracked runs (1 = independent).

    Returns:
        Spacings between consecutive tracked vehicles [m].
    """
    if not 0.0 < c <= 1.0:
        raise ValueError(f"c must be in (0, 1], got {c}")
    if run_length < 1.0:
        raise ValueError("run_length must be >= 1")
    n = true_spacings.shape[0] + 1
    pos = np.concatenate([[0.0], np.cumsum(true_spacings)])
    if run_length == 1.0 or c == 1.0:
        keep = rng.random(n) < c
    else:
        # Two-state chain: leave "untracked" w.p. p_ut = 1/run_length per
        # vehicle; leave "tracked" w.p. p_tu chosen so the stationary
        # tracked share is c: p_tu = p_ut (1 - c) / c.
        p_ut = 1.0 / run_length
        p_tu = min(p_ut * (1.0 - c) / c, 1.0)
        keep = np.empty(n, dtype=bool)
        state = rng.random() < c
        u = rng.random(n)
        for i in range(n):
            keep[i] = state
            state = (u[i] >= p_tu) if state else (u[i] < p_ut)
    kept = pos[keep]
    return np.diff(kept)


def _gamma_spacings(rng: np.random.Generator, n: int, mean: float, cv: float) -> FloatArray:
    alpha = cv**-2
    return rng.gamma(alpha, mean / alpha, size=n)


def synthetic_validation(
    *,
    c_values: Sequence[float] = (0.4, 0.6, 0.8),
    n_vehicles: int = 20_000,
    seed: int = 42,
    idm_mean: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Recovery of a known coverage by every estimator on synthetic lanes.

    Regimes (true spacing generators, one lane, ``n_vehicles`` vehicles):

    * ``congested``: gamma, mean 12 m, cv 0.35 (queued traffic at one
      speed).
    * ``congested_classwidth``: platoons of 5–20 vehicles whose mean
      spacing is drawn uniformly in 10–14 m (±17%, the equilibrium-spacing
      spread across one 10 km/h speed class at ~15 km/h), cv 0.3 within a
      platoon — the heterogeneity a speed-conditioned fit still contains.
    * ``congested_heterogeneous``: platoons of 5–20 vehicles alternating
      between a jam (mean 8 m, cv 0.25) and a creeping regime (mean 25 m,
      cv 0.35) — an adversarial within-class mixing of stop-and-go states.
    * ``uncongested``: gamma, mean 50 m, cv 0.8 (free-flow spacings close
      to exponential — the regime where (M) and (G) carry little
      information).
    * ``congested_correlated``: as ``congested`` but with losses in runs of
      mean length 3 (violates the independence assumption).
    * ``idm_equilibrium``: spacings ``s_eq(v; T_i, s0_i) + L`` at 8 m/s
      from a heterogeneous IDM population (per-vehicle ``T`` and ``s0``
      drawn with the population's spread) — the regime in which the
      equilibrium estimator is exact by construction.

    For each regime and ``c`` the row reports the moment estimator with the
    generator's ``cv_true``, the mixture estimator, the equilibrium
    estimator (IDM regime only) and the capacity bound with a ceiling 10%
    above the true flow (recovers ``c / 1.1`` by construction — it is a
    bound, not an estimate).

    Args:
        c_values: True coverages to test.
        n_vehicles: Vehicles per synthetic lane.
        seed: RNG seed.
        idm_mean: IDM population means for the equilibrium regime; the
            module default (I-24 capacity-calibrated means) when ``None``.

    Returns:
        Rows with ``regime``, ``c_true``, ``cv_true``, and per-estimator
        values and errors.
    """
    rng = make_rng(seed)
    idm = idm_mean or {"v0": 32.4, "T": 1.322, "s0": 2.533}
    v_idm = 8.0
    rows: list[dict[str, Any]] = []

    def make(regime: str) -> tuple[FloatArray, float, float]:
        """True spacings, their cv, and the correlated run length."""
        if regime == "congested":
            return _gamma_spacings(rng, n_vehicles, 12.0, 0.35), 0.35, 1.0
        if regime == "congested_correlated":
            return _gamma_spacings(rng, n_vehicles, 12.0, 0.35), 0.35, 3.0
        if regime == "uncongested":
            return _gamma_spacings(rng, n_vehicles, 50.0, 0.8), 0.8, 1.0
        if regime in ("congested_heterogeneous", "congested_classwidth"):
            parts: list[FloatArray] = []
            total = 0
            jam = True
            while total < n_vehicles:
                m = int(rng.integers(5, 21))
                if regime == "congested_classwidth":
                    parts.append(_gamma_spacings(rng, m, float(rng.uniform(10.0, 14.0)), 0.3))
                else:
                    parts.append(
                        _gamma_spacings(rng, m, 8.0, 0.25)
                        if jam
                        else _gamma_spacings(rng, m, 25.0, 0.35)
                    )
                total += m
                jam = not jam
            s = np.concatenate(parts)[:n_vehicles]
            return s, float(s.std() / s.mean()), 1.0
        if regime == "idm_equilibrium":
            big_t = np.clip(rng.normal(idm["T"], 0.15 * idm["T"], n_vehicles), 0.3, None)
            s0 = np.clip(rng.normal(idm["s0"], 0.15 * idm["s0"], n_vehicles), 0.5, None)
            gap = (s0 + v_idm * big_t) / math.sqrt(1.0 - (v_idm / idm["v0"]) ** 4)
            s = gap + DEFAULT_VEHICLE_LENGTH_M
            return s, float(s.std() / s.mean()), 1.0
        raise ValueError(regime)

    regimes = (
        "congested",
        "congested_classwidth",
        "congested_heterogeneous",
        "uncongested",
        "congested_correlated",
        "idm_equilibrium",
    )
    for regime in regimes:
        for c in c_values:
            true_s, cv_true, run = make(regime)
            obs = thin_positions(true_s, c, rng, run_length=run)
            fit = fit_gap_mixture(obs)
            row: dict[str, Any] = {
                "regime": regime,
                "c_true": c,
                "cv_true": cv_true,
                "n_observed": int(obs.size),
                "gap_moments": coverage_gap_moments(obs, min(cv_true, 0.999)),
                "gap_mixture": fit.c,
                "gap_mixture_cv_true": fit.cv_true,
                "gap_mixture_mean_true_m": fit.mean_true_m,
                "true_mean_m": float(true_s.mean()),
            }
            # Tracked density: the kept vehicles over the same lane length.
            rho_true = 1.0 / float(true_s.mean())
            rho_tracked = rho_true * (obs.size + 1) / (true_s.size + 1)
            if regime == "idm_equilibrium":
                row["equilibrium"] = coverage_equilibrium(rho_tracked, v_idm, idm)
            else:
                row["equilibrium"] = math.nan
            q_true = rho_true * v_idm
            row["capacity_bound_1p1"] = coverage_capacity_bound(rho_tracked * v_idm, 1.1 * q_true)
            for key in ("gap_moments", "gap_mixture", "equilibrium", "capacity_bound_1p1"):
                row[f"err_{key}"] = row[key] - c if math.isfinite(row[key]) else math.nan
            rows.append(row)
    return rows
