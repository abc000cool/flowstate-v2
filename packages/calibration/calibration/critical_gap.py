"""Critical gaps of lane changes from accepted and rejected gaps (WP-78).

The weave model (``microsim.runner._weave_change_ok``, docs/WEAVE_MODEL_PLAN.md)
accepts a change when the target-lane gaps clear ``s0 + A · v`` on both
sides (``A`` = ``WEAVE_DEFAULTS`` ``accept_gap_s`` for the entering movement,
``exit_accept_gap_s`` for the exiting one), the follower can absorb the
changer within its comfortable deceleration, and the brake-gap guard. VM X
(``artifacts/i24_lane_change_gaps.json``) found that acceptance would refuse
48.5 % of the real entering changes and 22.1 % of the exiting ones in the
I-24 MOTION Hickory Hollow–Bell Road weave. The accepted gaps alone cannot
say how small a gap real drivers *require*: a driver who took a 3 s gap may
have required 1 s or 2.9 s. This module estimates the distribution of the
**critical gap** — the smallest gap a driver accepts — from the gap each
driver accepted together with the gaps the same driver let go by
(:func:`calibration.lane_change_gaps.gap_sequences`), and maps it onto the
acceptance's parameters.

**Troutbeck's maximum-likelihood estimator** (Troutbeck 1992, *Estimating the
critical acceptance gap from traffic movements*, Physical Infrastructure
Centre Research Report 92-5, QUT; assessed as the most reliable of the
common estimators by Brilon, Koenig & Troutbeck 1999, *Useful estimation
procedures for critical gaps*, Transp. Res. A 33:161–186; stated as
``L = ∏ [F(a_i) − F(r_i)]`` with ``a_i`` the accepted and ``r_i`` the largest
rejected gap by Weinert 2000, TRB Circular E-C018, 409–421, Eq. 1). A driver
is assumed *consistent*: it rejects every gap below its critical gap and
takes the first one above, so its critical gap lies in ``(r_i, a_i]``. The
critical gaps of the population are log-normal (Troutbeck 1992), ``ln t_c ~
N(μ, σ²)``, and ``μ, σ`` maximize ``Σ ln [F(a_i) − F(r_i)]``. The median
critical gap is ``exp(μ)``, the mean ``exp(μ + σ²/2)`` (Troutbeck 2014,
*Estimating the mean critical gap*, TRR 2461:76–84, reports the mean). Cases:

* **No rejected gap.** ``r_i = 0`` and the driver contributes ``F(a_i)``
  (``no_rejection="include"``, the default). Weinert (2000, §2.2) compared
  the choice on field data: with such drivers kept (his sample 1) the
  critical gaps were up to 1.7 s smaller than with only drivers who rejected
  at least one gap (his samples 2–3), and he kept the latter;
  ``no_rejection="exclude"`` reproduces that choice and every artifact
  reports both. Under the consistent-driver model the drivers who accept at
  once are informative (their critical gap is below the gap they took), so
  excluding them biases the estimate upward; the synthetic tests show it.
* **Inconsistent driver** (a rejected gap at least as large as the accepted
  one, ``r_i ≥ a_i``): ``F(a_i) − F(r_i) ≤ 0`` has no logarithm, and the
  consistent-driver model cannot hold for that driver. Marczak, Daamen &
  Buisson (2013, Transp. Res. C 36:530–546) found such drivers common at
  freeway merges ("rejected gaps are scattered with the accepted gaps,
  clearly showing inconsistent choice behaviour"), after Daamen, Loot &
  Hoogendoorn (2010, TRR 2188:108–118). ``inconsistent="exclude"`` (the
  default) leaves the driver out; ``"drop_rejected"`` keeps its accepted gap
  alone (``r_i = 0``). Both are counted, never silent.
* **No vehicle on a side** at the change (none within the extraction's
  200 m range): the accepted gap is ``+inf`` and the driver contributes
  ``1 − F(r_i)`` (right-censored); with no rejected gap either it carries no
  information and is counted as uninformative.

**Lead and lag.** A lane change needs a gap ahead (the lead gap, over the
changer's speed) and one behind (the lag gap, over the new follower's speed),
and the lane-change literature estimates the two critical gaps separately
(Marczak et al. 2013 §2.4 after Choudhury et al. 2007). The *separate*
estimator (:func:`fit_critical_gap`) is Troutbeck's per side: for the lead
side ``r_i`` is the largest lead time gap among the driver's rejected gaps,
likewise for the lag side. It treats every rejected gap as rejected on both
sides, but a gap refused for its lag says nothing about its lead: the lead
side's largest "rejected" gap can then exceed the driver's lead critical gap,
which biases the separate estimator upward and creates spurious
inconsistencies. The *joint* estimator (:func:`fit_joint_critical_gaps`,
this module's extension of Troutbeck's likelihood; it reduces to the
separate one when a side never binds) takes the driver's behaviour as the
weave's acceptance itself takes it — a gap is accepted when **both** sides
clear their critical gaps — with independent log-normal lead and lag
critical gaps per driver. A driver's likelihood is the probability that its
critical pair lies inside the accepted pair's rectangle and outside the
union of the rectangles of every rejected (lead, lag) combination it was
offered; the union of origin-anchored rectangles is a staircase whose
product measure is exact (:func:`_staircases`). The synthetic tests show the
joint estimator recovering drivers who need both sides where the separate
one does not. Artifacts report both; the acceptance proposal uses the joint
one (the acceptance's own conjunction) with the separate one as a
sensitivity.

**Coverage (I-24 MOTION, about half the peak vehicle-time tracked,
docs/I24_DATA.md §4).** An untracked vehicle inside an observed gap makes
the accepted gap larger than the true one (never smaller), and an untracked
vehicle between two observed neighbours merges two true gaps: a rejected gap
may be larger than its true value, or lost into the accepted one. The
accepted gap is the upper edge of every driver's interval and all the
information of a driver without a rejected gap, so the fitted critical gaps
are biased upward: the true population accepts gaps at least this small in
expectation (the synthetic thinning test measures the direction on a known
population), and an acceptance calibrated to these medians is, if anything,
still stricter than the real drivers.

**Mapping to the acceptance** (:func:`model_parity_critical_gaps`,
:func:`acceptance_mapping`). At speed parity (the observed weave crossings'
median closing speeds are within 1.4 m/s of zero, VM X) the brake-gap terms
vanish and the guard reduces to ``gap > 2 s0``, so the acceptance's leader
side needs a bumper gap ``2 s0 + A · v`` — a critical time gap ``A + 2 s0 / v``
over the changer's speed — and its follower side the larger of ``2 s0 + A ·
v_F`` (time ``A + 2 s0 / v_F`` over the follower's speed) and the absorption
gap ``(s0 + v_F T) / √(1 − (v_F/v0)⁴ + b/a_max)``, which does not depend on
``A``. A fitted median critical time gap ``t̂`` at a class's median speed
``v̄`` therefore implies ``A = t̂ − 2 s0 / v̄`` on either side, provided on the
follower side that ``t̂`` is above the absorption floor; below it no ``A``
reproduces it and the follower deceleration the median gap would impose,
``−a_IDM(v_F, gap = t̂ v_F)``, is reported instead.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import ndtr, ndtri

from calibration.lane_change_gaps import (
    SPEED_CLASSES_MS,
    AcceptanceParams,
    _idm_accel_vec,
    weave_acceptance,
)
from flowstate_core.rng import make_rng, spawn_seeds

NoRejection = Literal["include", "exclude"]
Inconsistent = Literal["exclude", "drop_rejected"]

MIN_DRIVERS: Final[int] = 30
"""Fewest usable drivers for a fit; a smaller group is reported unfitted."""

MIN_REJECTING: Final[int] = 10
"""Fewest used drivers with a rejected gap for a fit: without rejections the
likelihood ``∏ F(a_i)`` grows without bound as the distribution moves to
zero, so a group whose information is almost all accepted gaps is reported
unfitted rather than as a degenerate fit."""

DEFAULT_N_BOOT: Final[int] = 200
"""Bootstrap replicates (drivers resampled with replacement) per fit."""

DEFAULT_SEED: Final[int] = 20260925
"""Master seed of the bootstrap (``flowstate_core.rng.spawn_seeds``)."""

MOVEMENT_PARAMETER: Final[dict[str, tuple[str, bool]]] = {
    "entering": ("accept_gap_s", False),
    "exiting": ("exit_accept_gap_s", True),
}
"""The ``WEAVE_DEFAULTS`` key each movement's acceptance reads, and whether the
change is to the right (the band convention: the auxiliary lane is the
rightmost, so entering is a change to the left)."""

_SIGMA_FLOOR: Final[float] = 1e-3
_P_FLOOR: Final[float] = 1e-300


# --- driver tables -------------------------------------------------------------


def driver_gaps(
    records: pd.DataFrame,
    samples: pd.DataFrame,
    *,
    max_lookback_s: float | None = None,
) -> pd.DataFrame:
    """One row per sampled change: its accepted gaps and largest rejected gaps.

    Args:
        records: :attr:`calibration.lane_change_gaps.LaneChangeGaps.records`.
        samples: :attr:`calibration.lane_change_gaps.GapSequences.samples` of
            the same records.
        max_lookback_s: Only rejected instants at most this long before the
            change count (a sensitivity); None = all sampled.

    Returns:
        Frame with ``change`` (row of ``records``), the record's ``t, veh_id,
        zone, zone_kind, movement, direction, v, lag_v, confirmed, suspect``
        (and ``seed`` / ``group`` when present); ``a_lead_s``, ``a_lag_s`` (the
        accepted time gaps; ``inf`` with no vehicle within range on that side,
        NaN when the time gap is undefined); ``lead_closing_ms``,
        ``lag_closing_ms`` (accepted); ``r_lead_s``, ``r_lag_s`` (the largest
        finite time gap that side offered over the driver's non-suspect
        rejected instants; 0 when none); ``n_rejected_gaps``,
        ``n_rejected_samples``; ``lookback_s`` (the history sampled).
    """
    change = np.unique(samples["change"].to_numpy(dtype=np.int64))
    rec = records.iloc[change].reset_index(drop=True)
    keep = [
        c
        for c in (
            "seed",
            "group",
            "t",
            "veh_id",
            "zone",
            "zone_kind",
            "movement",
            "direction",
            "v",
            "lag_v",
            "confirmed",
            "suspect",
            "lead_closing_ms",
            "lag_closing_ms",
        )
        if c in rec.columns
    ]
    out = rec[keep].copy()
    out.insert(0, "change", change)
    lead_gap = rec["lead_gap_m"].to_numpy(dtype=np.float64)
    lag_gap = rec["lag_gap_m"].to_numpy(dtype=np.float64)
    out["a_lead_s"] = np.where(
        np.isfinite(lead_gap), rec["lead_time_gap_s"].to_numpy(dtype=np.float64), np.inf
    )
    out["a_lag_s"] = np.where(
        np.isfinite(lag_gap), rec["lag_time_gap_s"].to_numpy(dtype=np.float64), np.inf
    )
    rej = samples[(samples["status"] == "rejected") & ~samples["suspect"].astype(bool)]
    if max_lookback_s is not None:
        rej = rej[rej["dt_before_s"].to_numpy(dtype=np.float64) <= max_lookback_s + 1e-9]
    grp = rej.groupby("change")
    r_lead = grp["lead_time_gap_s"].max()
    r_lag = grp["lag_time_gap_s"].max()
    n_gaps = grp["gap_index"].nunique()
    n_samp = grp.size()
    look = samples.groupby("change")["dt_before_s"].max()
    idx = pd.Index(change)
    out["r_lead_s"] = r_lead.reindex(idx).fillna(0.0).to_numpy(dtype=np.float64)
    out["r_lag_s"] = r_lag.reindex(idx).fillna(0.0).to_numpy(dtype=np.float64)
    out["n_rejected_gaps"] = n_gaps.reindex(idx).fillna(0).to_numpy(dtype=np.int64)
    out["n_rejected_samples"] = n_samp.reindex(idx).fillna(0).to_numpy(dtype=np.int64)
    out["lookback_s"] = look.reindex(idx).to_numpy(dtype=np.float64)
    return out


def rejected_points(samples: pd.DataFrame, *, max_lookback_s: float | None = None) -> pd.DataFrame:
    """Every rejected (lead, lag) time-gap combination, for the joint estimator.

    Non-suspect ``rejected`` instants of :func:`gap_sequences`; a side with no
    vehicle within range is ``inf`` (it imposed nothing), and an instant whose
    time gap is undefined on a side that has a vehicle is dropped.

    Args:
        samples: :attr:`calibration.lane_change_gaps.GapSequences.samples`.
        max_lookback_s: As :func:`driver_gaps`.

    Returns:
        Frame ``change, lead_s, lag_s``.
    """
    rej = samples[(samples["status"] == "rejected") & ~samples["suspect"].astype(bool)]
    if max_lookback_s is not None:
        rej = rej[rej["dt_before_s"].to_numpy(dtype=np.float64) <= max_lookback_s + 1e-9]
    lead = np.where(
        np.isfinite(rej["lead_gap_m"].to_numpy(dtype=np.float64)),
        rej["lead_time_gap_s"].to_numpy(dtype=np.float64),
        np.inf,
    )
    lag = np.where(
        np.isfinite(rej["lag_gap_m"].to_numpy(dtype=np.float64)),
        rej["lag_time_gap_s"].to_numpy(dtype=np.float64),
        np.inf,
    )
    ok = ~np.isnan(lead) & ~np.isnan(lag)
    return pd.DataFrame(
        {
            "change": rej["change"].to_numpy(dtype=np.int64)[ok],
            "lead_s": lead[ok],
            "lag_s": lag[ok],
        }
    )


# --- the log-normal and its fits --------------------------------------------------


def _cdf(t: NDArray[np.float64], mu: float, sigma: float) -> NDArray[np.float64]:
    """Log-normal CDF with ``F(0) = 0`` and ``F(inf) = 1``."""
    with np.errstate(divide="ignore"):
        z = (np.log(t) - mu) / sigma
    return np.asarray(ndtr(z), dtype=np.float64)


def _interval_prob(
    a: NDArray[np.float64], r: NDArray[np.float64], mu: float, sigma: float
) -> NDArray[np.float64]:
    """``F(a) − F(r)`` for ``0 ≤ r < a ≤ inf``, from the upper tail where that is exact."""
    with np.errstate(divide="ignore"):
        za = (np.log(a) - mu) / sigma
        zr = (np.log(r) - mu) / sigma
    upper = ndtr(-zr) - ndtr(-za)
    lower = ndtr(za) - ndtr(zr)
    return np.asarray(np.where(zr > 0.0, upper, lower), dtype=np.float64)


@dataclass(frozen=True)
class LogNormalFit:
    """A fitted log-normal critical-gap distribution, ``ln t_c ~ N(mu, sigma²)``.

    Attributes:
        mu: Location of ``ln t_c``.
        sigma: Scale of ``ln t_c``.
        loglik: Maximized (weighted) log-likelihood.
        converged: Whether the optimizer reported success.
        counts: ``n_input``, ``n_undefined`` (a non-positive or NaN accepted
            gap, or a NaN rejected one), ``n_no_rejection``,
            ``n_no_rejection_excluded``, ``n_inconsistent``,
            ``n_inconsistent_excluded``, ``n_uninformative`` (accepted gap
            ``inf`` and nothing rejected), ``n_censored`` (accepted ``inf``
            among the used), ``n_used``.
    """

    mu: float
    sigma: float
    loglik: float
    converged: bool
    counts: dict[str, int]

    @property
    def median(self) -> float:
        """Median critical gap, ``exp(mu)``."""
        return math.exp(self.mu)

    @property
    def mean(self) -> float:
        """Mean critical gap, ``exp(mu + sigma²/2)``."""
        return math.exp(self.mu + 0.5 * self.sigma**2)

    @property
    def sd(self) -> float:
        """Standard deviation of the critical gap."""
        return self.mean * math.sqrt(math.expm1(self.sigma**2))

    def quantile(self, q: float) -> float:
        """The ``q`` quantile of the critical gap."""
        return math.exp(self.mu + self.sigma * float(ndtri(q)))

    def to_dict(self) -> dict[str, Any]:
        """JSON form (seconds; rounded)."""
        return {
            "mu": round(self.mu, 5),
            "sigma": round(self.sigma, 5),
            "median_s": round(self.median, 4),
            "mean_s": round(self.mean, 4),
            "sd_s": round(self.sd, 4),
            "p10_s": round(self.quantile(0.1), 4),
            "p90_s": round(self.quantile(0.9), 4),
            "loglik": round(self.loglik, 4),
            "converged": self.converged,
            "degenerate": self.sigma <= 2.0 * _SIGMA_FLOOR,
            **self.counts,
        }


def _prepare(
    accepted: NDArray[np.float64],
    rejected: NDArray[np.float64],
    no_rejection: NoRejection,
    inconsistent: Inconsistent,
) -> tuple[NDArray[np.bool_], NDArray[np.float64], dict[str, int]]:
    """Which drivers enter the likelihood, their effective ``r``, and the counts."""
    if no_rejection not in ("include", "exclude"):
        raise ValueError(f"no_rejection must be 'include' or 'exclude', got {no_rejection!r}")
    if inconsistent not in ("exclude", "drop_rejected"):
        raise ValueError(f"inconsistent must be 'exclude' or 'drop_rejected', got {inconsistent!r}")
    a, r = accepted, rejected.copy()
    undefined = np.isnan(a) | (a <= 0.0) | np.isnan(r)
    r = np.where(np.isnan(r), 0.0, np.maximum(r, 0.0))
    none_rej = ~undefined & (r <= 0.0)
    incons = ~undefined & (r > 0.0) & (r >= a)
    use = ~undefined.copy()
    n_incons_excl = 0
    if inconsistent == "exclude":
        use &= ~incons
        n_incons_excl = int(np.sum(incons))
    else:
        r = np.where(incons, 0.0, r)
    no_rej_now = use & (r <= 0.0)
    n_norej_excl = 0
    if no_rejection == "exclude":
        n_norej_excl = int(np.sum(no_rej_now & ~incons))
        use &= r > 0.0
    uninformative = use & np.isinf(a) & (r <= 0.0)
    use &= ~uninformative
    counts = {
        "n_input": int(a.size),
        "n_undefined": int(np.sum(undefined)),
        "n_no_rejection": int(np.sum(none_rej)),
        "n_no_rejection_excluded": n_norej_excl,
        "n_inconsistent": int(np.sum(incons)),
        "n_inconsistent_excluded": n_incons_excl,
        "n_uninformative": int(np.sum(uninformative)),
        "n_censored": int(np.sum(use & np.isinf(a))),
        "n_used": int(np.sum(use)),
        "n_with_rejection": int(np.sum(use & (r > 0.0))),
    }
    return use, r, counts


def _start(a: NDArray[np.float64], r: NDArray[np.float64]) -> tuple[float, float]:
    """A starting point: moments of the log interval midpoints."""
    with np.errstate(invalid="ignore"):
        inner = np.where(r > 0.0, np.sqrt(np.where(np.isinf(a), 1.0, a) * np.maximum(r, 0.0)), a)
    mid = np.where(np.isinf(a), 1.5 * np.maximum(r, 1e-3), inner)
    lm = np.log(mid[np.isfinite(mid) & (mid > 0.0)])
    if lm.size == 0:
        return 0.0, 0.5
    return float(np.mean(lm)), max(float(np.std(lm)), 0.2)


def _minimize(
    nll: Callable[[NDArray[np.float64]], float], x0: Sequence[float]
) -> tuple[NDArray[np.float64], float, bool]:
    """Nelder–Mead on the negative log-likelihood (log-scale parameters inside ``nll``)."""
    res = minimize(
        nll,
        np.asarray(x0, dtype=np.float64),
        method="Nelder-Mead",
        options={"xatol": 1e-7, "fatol": 1e-9, "maxiter": 8000, "maxfev": 16000},
    )
    return np.asarray(res.x, dtype=np.float64), float(res.fun), bool(res.success)


def fit_critical_gap(
    accepted: Sequence[float] | NDArray[np.float64],
    rejected: Sequence[float] | NDArray[np.float64],
    *,
    weights: NDArray[np.float64] | None = None,
    no_rejection: NoRejection = "include",
    inconsistent: Inconsistent = "exclude",
    x0: tuple[float, float] | None = None,
) -> LogNormalFit:
    """Troutbeck's maximum-likelihood critical gap for one side (module docstring).

    Args:
        accepted: Each driver's accepted gap (``inf`` = right-censored).
        rejected: Each driver's largest rejected gap (0 = none rejected).
        weights: Per-driver weights (bootstrap multiplicities); ones when None.
        no_rejection: ``"include"`` (``r = 0``, Troutbeck) or ``"exclude"``.
        inconsistent: ``"exclude"`` or ``"drop_rejected"`` for ``r ≥ a``.
        x0: Starting ``(mu, sigma)``.

    Returns:
        :class:`LogNormalFit`.

    Raises:
        ValueError: With fewer than two usable drivers.
    """
    a = np.asarray(accepted, dtype=np.float64)
    r = np.asarray(rejected, dtype=np.float64)
    if a.shape != r.shape:
        raise ValueError("accepted and rejected must have one value per driver")
    use, r_eff, counts = _prepare(a, r, no_rejection, inconsistent)
    if counts["n_used"] < 2:
        raise ValueError(f"too few usable drivers to fit: {counts}")
    w = np.ones(a.size) if weights is None else np.asarray(weights, dtype=np.float64)
    au, ru, wu = a[use], r_eff[use], w[use]

    def nll(theta: NDArray[np.float64]) -> float:
        sigma = max(math.exp(float(theta[1])), _SIGMA_FLOOR)
        p = _interval_prob(au, ru, float(theta[0]), sigma)
        return float(-np.sum(wu * np.log(np.maximum(p, _P_FLOOR))))

    mu0, s0 = x0 if x0 is not None else _start(au, ru)
    x, fun, ok = _minimize(nll, (mu0, math.log(max(s0, _SIGMA_FLOOR))))
    return LogNormalFit(
        mu=float(x[0]),
        sigma=max(math.exp(float(x[1])), _SIGMA_FLOOR),
        loglik=-fun,
        converged=ok,
        counts=counts,
    )


# --- the joint (lead AND lag) estimator ---------------------------------------------


@dataclass(frozen=True)
class JointData:
    """Parameter-free preparation of the joint likelihood.

    Attributes:
        a_lead: Accepted lead gaps of the used drivers.
        a_lag: Accepted lag gaps of the used drivers.
        driver: Staircase step → driver (index into ``a_lead``).
        x: Step's lead bound (descending within a driver).
        y: Step's lag bound (ascending within a driver).
        y_prev: The previous step's lag bound (0 for a driver's first step).
        used: Mask over the input drivers that entered.
        counts: As :attr:`LogNormalFit.counts`, plus ``n_points`` (rejected
            combinations of the used drivers) and ``n_steps``.
    """

    a_lead: NDArray[np.float64]
    a_lag: NDArray[np.float64]
    driver: NDArray[np.int64]
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    y_prev: NDArray[np.float64]
    used: NDArray[np.bool_]
    counts: dict[str, int]


def _staircases(
    a_lead: NDArray[np.float64],
    a_lag: NDArray[np.float64],
    pt_driver: NDArray[np.int64],
    pt_lead: NDArray[np.float64],
    pt_lag: NDArray[np.float64],
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """The Pareto staircase of each driver's rejected rectangles, clipped to its accepted one.

    The union of rectangles ``[0, x_j] × [0, y_j]`` is fixed by its maximal
    corners; sorted by ``x`` descending they have ``y`` ascending, and the
    union's product measure is ``Σ_k F_L(x_k) · (F_G(y_k) − F_G(y_{k−1}))``
    with ``y_0 = 0``.
    """
    if pt_driver.size == 0:
        e = np.zeros(0)
        return np.zeros(0, dtype=np.int64), e, e, e
    x = np.minimum(pt_lead, a_lead[pt_driver])
    y = np.minimum(pt_lag, a_lag[pt_driver])
    order = np.lexsort((-y, -x, pt_driver))
    d, x, y = pt_driver[order], x[order], y[order]
    frame = pd.DataFrame({"d": d, "y": y})
    prev_max = frame.groupby("d")["y"].cummax().groupby(frame["d"]).shift(1)
    keep = y > prev_max.fillna(-np.inf).to_numpy(dtype=np.float64)
    d, x, y = d[keep], x[keep], y[keep]
    first = np.ones(d.size, dtype=bool)
    first[1:] = d[1:] != d[:-1]
    y_prev = np.where(first, 0.0, np.roll(y, 1))
    return d, x, y, y_prev


def prepare_joint(
    a_lead: Sequence[float] | NDArray[np.float64],
    a_lag: Sequence[float] | NDArray[np.float64],
    pt_driver: Sequence[int] | NDArray[np.int64],
    pt_lead: Sequence[float] | NDArray[np.float64],
    pt_lag: Sequence[float] | NDArray[np.float64],
    *,
    no_rejection: NoRejection = "include",
    inconsistent: Inconsistent = "exclude",
) -> JointData:
    """Select drivers and build the staircases of the joint likelihood.

    Args:
        a_lead: Each driver's accepted lead gap (``inf`` = none within range).
        a_lag: Each driver's accepted lag gap.
        pt_driver: For every rejected combination, its driver (index).
        pt_lead: Its lead gap (``inf`` = no lead within range).
        pt_lag: Its lag gap.
        no_rejection: As :func:`fit_critical_gap` (a driver without a
            rejected combination contributes ``F_L(a_L) F_G(a_G)``).
        inconsistent: As :func:`fit_critical_gap`; a driver is inconsistent
            when a rejected combination is at least as large on both sides as
            the accepted one.

    Returns:
        :class:`JointData`.
    """
    if no_rejection not in ("include", "exclude"):
        raise ValueError(f"no_rejection must be 'include' or 'exclude', got {no_rejection!r}")
    if inconsistent not in ("exclude", "drop_rejected"):
        raise ValueError(f"inconsistent must be 'exclude' or 'drop_rejected', got {inconsistent!r}")
    al = np.asarray(a_lead, dtype=np.float64)
    ag = np.asarray(a_lag, dtype=np.float64)
    pd_ = np.asarray(pt_driver, dtype=np.int64)
    pl = np.asarray(pt_lead, dtype=np.float64)
    pg = np.asarray(pt_lag, dtype=np.float64)
    n = al.size
    undefined = np.isnan(al) | np.isnan(ag) | (al <= 0.0) | (ag <= 0.0)
    pt_ok = ~np.isnan(pl) & ~np.isnan(pg) & ~(np.isinf(pl) & np.isinf(pg))
    pd_, pl, pg = pd_[pt_ok], pl[pt_ok], pg[pt_ok]
    n_pts = np.bincount(pd_, minlength=n)
    dominated = (pl >= al[pd_]) & (pg >= ag[pd_])
    incons = np.bincount(pd_[dominated], minlength=n) > 0
    incons &= ~undefined
    none_rej = ~undefined & (n_pts == 0)
    use = ~undefined.copy()
    n_incons_excl = 0
    drop_pts = np.zeros(n, dtype=bool)
    if inconsistent == "exclude":
        use &= ~incons
        n_incons_excl = int(np.sum(incons))
    else:
        drop_pts = incons
    has_pts = (n_pts > 0) & ~drop_pts
    n_norej_excl = 0
    if no_rejection == "exclude":
        n_norej_excl = int(np.sum(use & ~has_pts & ~incons))
        use &= has_pts
    uninformative = use & np.isinf(al) & np.isinf(ag) & ~has_pts
    use &= ~uninformative
    new_index = np.cumsum(use) - 1
    keep_pt = use[pd_] & ~drop_pts[pd_]
    d, x, y, y_prev = _staircases(
        al[use], ag[use], new_index[pd_[keep_pt]], pl[keep_pt], pg[keep_pt]
    )
    counts = {
        "n_input": int(n),
        "n_undefined": int(np.sum(undefined)),
        "n_no_rejection": int(np.sum(none_rej)),
        "n_no_rejection_excluded": n_norej_excl,
        "n_inconsistent": int(np.sum(incons)),
        "n_inconsistent_excluded": n_incons_excl,
        "n_uninformative": int(np.sum(uninformative)),
        "n_censored": int(np.sum(use & (np.isinf(al) | np.isinf(ag)))),
        "n_used": int(np.sum(use)),
        "n_with_rejection": int(np.unique(d).size),
        "n_points": int(np.sum(keep_pt)),
        "n_steps": int(d.size),
    }
    return JointData(al[use], ag[use], d, x, y, y_prev, use, counts)


@dataclass(frozen=True)
class JointFit:
    """The joint estimator's lead and lag distributions.

    Attributes:
        lead: Lead-side critical gaps (its ``counts`` are the joint's).
        lag: Lag-side critical gaps.
        loglik: Maximized (weighted) log-likelihood.
        converged: Optimizer success.
        counts: :attr:`JointData.counts`.
    """

    lead: LogNormalFit
    lag: LogNormalFit
    loglik: float
    converged: bool
    counts: dict[str, int]


def _joint_nll(theta: NDArray[np.float64], data: JointData, w: NDArray[np.float64]) -> float:
    s_l = max(math.exp(float(theta[1])), _SIGMA_FLOOR)
    s_g = max(math.exp(float(theta[3])), _SIGMA_FLOOR)
    m_l, m_g = float(theta[0]), float(theta[2])
    inside = _cdf(data.a_lead, m_l, s_l) * _cdf(data.a_lag, m_g, s_g)
    if data.driver.size:
        steps = _cdf(data.x, m_l, s_l) * (_cdf(data.y, m_g, s_g) - _cdf(data.y_prev, m_g, s_g))
        inside = inside - np.bincount(data.driver, weights=steps, minlength=inside.size)
    return float(-np.sum(w * np.log(np.maximum(inside, _P_FLOOR))))


def fit_joint_critical_gaps(
    data: JointData,
    *,
    weights: NDArray[np.float64] | None = None,
    x0: tuple[float, float, float, float] | None = None,
) -> JointFit:
    """The joint lead–lag maximum-likelihood estimator (module docstring).

    Args:
        data: :func:`prepare_joint`.
        weights: Per used driver (bootstrap multiplicities); ones when None.
        x0: Starting ``(mu_lead, sigma_lead, mu_lag, sigma_lag)``.

    Returns:
        :class:`JointFit`.

    Raises:
        ValueError: With fewer than two usable drivers.
    """
    if data.counts["n_used"] < 2:
        raise ValueError(f"too few usable drivers to fit: {data.counts}")
    w = np.ones(data.a_lead.size) if weights is None else np.asarray(weights, dtype=np.float64)
    if x0 is None:
        rl = np.zeros(data.a_lead.size)
        rg = np.zeros(data.a_lag.size)
        if data.driver.size:
            np.maximum.at(rl, data.driver, np.where(np.isfinite(data.x), data.x, 0.0))
            np.maximum.at(rg, data.driver, np.where(np.isfinite(data.y), data.y, 0.0))
        ml, sl = _start(data.a_lead, rl)
        mg, sg = _start(data.a_lag, rg)
        x0 = (ml, sl, mg, sg)
    start = (x0[0], math.log(max(x0[1], _SIGMA_FLOOR)), x0[2], math.log(max(x0[3], _SIGMA_FLOOR)))
    x, fun, ok = _minimize(lambda th: _joint_nll(th, data, w), start)
    lead = LogNormalFit(
        float(x[0]), max(math.exp(float(x[1])), _SIGMA_FLOOR), -fun, ok, data.counts
    )
    lag = LogNormalFit(float(x[2]), max(math.exp(float(x[3])), _SIGMA_FLOOR), -fun, ok, data.counts)
    return JointFit(lead, lag, -fun, ok, data.counts)


# --- bootstrap ------------------------------------------------------------------------


def _ci(values: NDArray[np.float64]) -> list[float] | None:
    finite = values[np.isfinite(values)]
    if finite.size < 2:
        return None
    lo, hi = np.percentile(finite, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def bootstrap_medians(
    n: int,
    fit: Callable[[NDArray[np.float64]], tuple[float, ...]],
    *,
    n_boot: int,
    seed: int,
) -> NDArray[np.float64]:
    """Refit on ``n_boot`` driver resamples (with replacement).

    Args:
        n: Number of drivers.
        fit: Maps per-driver weights (resample multiplicities) to a tuple of
            statistics; a failed fit returns NaNs.
        n_boot: Replicates.
        seed: RNG seed (``flowstate_core.rng.make_rng``).

    Returns:
        Array ``(n_boot, k)``.
    """
    rng = make_rng(seed)
    rows = []
    for _ in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n).astype(np.float64)
        rows.append(fit(w))
    if not rows:
        return np.zeros((0, len(fit(np.ones(n)))), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def _with_ci(
    fit: LogNormalFit, boot_mu: NDArray[np.float64], boot_sigma: NDArray[np.float64]
) -> dict[str, Any]:
    out = fit.to_dict()
    med = np.exp(boot_mu)
    mean = np.exp(boot_mu + 0.5 * boot_sigma**2)
    out["ci95"] = {
        "median_s": _ci(med),
        "mean_s": _ci(mean),
        "mu": _ci(boot_mu),
        "sigma": _ci(boot_sigma),
    }
    out["n_boot"] = int(np.sum(np.isfinite(boot_mu)))
    return out


# --- groups -----------------------------------------------------------------------------


def _q50(values: NDArray[np.float64]) -> float | None:
    finite = values[np.isfinite(values)]
    return round(float(np.median(finite)), 4) if finite.size else None


def _fit_one(
    drv: pd.DataFrame,
    pts: pd.DataFrame,
    *,
    n_boot: int,
    seed: int,
    min_drivers: int,
) -> tuple[dict[str, Any], dict[str, NDArray[np.float64]]]:
    """Separate (lead, lag) and joint fits with bootstrap CIs for one driver group."""
    out: dict[str, Any] = {}
    boot: dict[str, NDArray[np.float64]] = {}
    n = len(drv)
    a_l = drv["a_lead_s"].to_numpy(dtype=np.float64)
    a_g = drv["a_lag_s"].to_numpy(dtype=np.float64)
    r_l = drv["r_lead_s"].to_numpy(dtype=np.float64)
    r_g = drv["r_lag_s"].to_numpy(dtype=np.float64)
    pos = pd.Index(drv["change"].to_numpy(dtype=np.int64))
    sel = pts[pts["change"].isin(pos)]
    pt_d = pos.get_indexer(pd.Index(sel["change"].to_numpy(dtype=np.int64)))
    pt_l = sel["lead_s"].to_numpy(dtype=np.float64)
    pt_g = sel["lag_s"].to_numpy(dtype=np.float64)

    for side, a, r in (("lead", a_l, r_l), ("lag", a_g, r_g)):
        _use, _, counts = _prepare(a, r, "include", "exclude")
        if counts["n_used"] < min_drivers or counts["n_with_rejection"] < MIN_REJECTING:
            out[f"separate_{side}"] = {"fitted": False, **counts}
            continue
        f = fit_critical_gap(a, r)
        x0 = (f.mu, f.sigma)

        def one(w: NDArray[np.float64], a: Any = a, r: Any = r, x0: Any = x0) -> tuple[float, ...]:
            try:
                g = fit_critical_gap(a, r, weights=w, x0=x0)
            except ValueError:
                return (math.nan, math.nan)
            return (g.mu, g.sigma)

        b = bootstrap_medians(n, one, n_boot=n_boot, seed=seed)
        out[f"separate_{side}"] = {"fitted": True, **_with_ci(f, b[:, 0], b[:, 1])}
        boot[f"separate_{side}"] = np.exp(b[:, 0])
        excl = (
            fit_critical_gap(a, r, no_rejection="exclude")
            if _enough(a, r, "exclude", min_drivers)
            else None
        )
        out[f"separate_{side}"]["sensitivity_no_rejection_excluded"] = (
            excl.to_dict() if excl is not None else None
        )

    data = prepare_joint(a_l, a_g, pt_d, pt_l, pt_g)
    if data.counts["n_used"] < min_drivers or data.counts["n_with_rejection"] < MIN_REJECTING:
        out["joint"] = {"fitted": False, **data.counts}
        return out, boot
    jf = fit_joint_critical_gaps(data)
    jx0 = (jf.lead.mu, jf.lead.sigma, jf.lag.mu, jf.lag.sigma)
    n_used = data.a_lead.size

    def one_joint(w: NDArray[np.float64]) -> tuple[float, ...]:
        g = fit_joint_critical_gaps(data, weights=w, x0=jx0)
        return (g.lead.mu, g.lead.sigma, g.lag.mu, g.lag.sigma)

    jb = bootstrap_medians(n_used, one_joint, n_boot=n_boot, seed=seed)
    lead_d = _with_ci(jf.lead, jb[:, 0], jb[:, 1])
    lag_d = _with_ci(jf.lag, jb[:, 2], jb[:, 3])
    counts = {k: lead_d.pop(k) for k in list(lead_d) if k in data.counts}
    for k in data.counts:
        lag_d.pop(k, None)
    lead_d.pop("loglik")
    lag_d.pop("loglik")
    lead_d.pop("converged")
    lag_d.pop("converged")
    out["joint"] = {
        "fitted": True,
        "lead": lead_d,
        "lag": lag_d,
        "loglik": round(jf.loglik, 4),
        "converged": jf.converged,
        **counts,
    }
    boot["joint_lead"] = np.exp(jb[:, 0])
    boot["joint_lag"] = np.exp(jb[:, 2])
    excl_data = prepare_joint(a_l, a_g, pt_d, pt_l, pt_g, no_rejection="exclude")
    if excl_data.counts["n_used"] >= min_drivers:
        ex = fit_joint_critical_gaps(excl_data)
        out["joint"]["sensitivity_no_rejection_excluded"] = {
            "lead_median_s": round(ex.lead.median, 4),
            "lag_median_s": round(ex.lag.median, 4),
            "n_used": ex.counts["n_used"],
        }
    else:
        out["joint"]["sensitivity_no_rejection_excluded"] = None
    return out, boot


def _enough(
    a: NDArray[np.float64], r: NDArray[np.float64], no_rejection: NoRejection, min_drivers: int
) -> bool:
    _, _, counts = _prepare(a, r, no_rejection, "exclude")
    return counts["n_used"] >= min_drivers


def select_drivers(
    drivers: pd.DataFrame,
    *,
    movements: Sequence[str] = ("entering", "exiting"),
    zone_kinds: Sequence[str] = ("merge", "diverge", "weave"),
    include_unconfirmed: bool = False,
    include_suspect: bool = False,
) -> pd.DataFrame:
    """The drivers the fits use: the movements and zone kinds asked for,
    confirmed and non-suspect changes unless asked (as ``summarize_gaps``)."""
    sel = drivers[
        drivers["movement"].isin(list(movements)) & drivers["zone_kind"].isin(list(zone_kinds))
    ]
    if not include_unconfirmed and "confirmed" in sel.columns:
        sel = sel[sel["confirmed"].astype(bool)]
    if not include_suspect and "suspect" in sel.columns:
        sel = sel[~sel["suspect"].astype(bool)]
    return sel


def fit_groups(
    drivers: pd.DataFrame,
    points: pd.DataFrame,
    *,
    by: Sequence[str] = ("zone", "zone_kind", "movement"),
    speed_classes: Sequence[tuple[str, float, float]] = SPEED_CLASSES_MS,
    min_drivers: int = MIN_DRIVERS,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, NDArray[np.float64]]]]:
    """Separate and joint critical-gap fits per group and changer-speed class.

    Args:
        drivers: :func:`select_drivers` output.
        points: :func:`rejected_points` of the same samples.
        by: Grouping columns.
        speed_classes: ``(label, lo, hi)`` changer-speed classes [m/s]; each
            group also gets an ``"all"`` row.
        min_drivers: Fewest usable drivers for a fit.
        n_boot: Bootstrap replicates.
        seed: Master seed; each row's bootstrap seed is
            ``spawn_seeds(seed, n_rows)[row]``.

    Returns:
        ``(rows, boots)``: JSON-ready rows (group keys, ``speed_class``,
        ``n_drivers``, the median changer and lag speeds and closing speeds,
        the share with a rejected gap, ``separate_lead``, ``separate_lag``,
        ``joint``) and, aligned, each row's bootstrap replicate medians (for
        :func:`acceptance_mapping`; not serialized).
    """
    keys = list(by)
    jobs: list[tuple[dict[str, str], str, pd.DataFrame]] = []
    for group_key, sub in drivers.groupby(keys, sort=True):
        key_t = group_key if isinstance(group_key, tuple) else (group_key,)
        head = {k: str(val) for k, val in zip(keys, key_t, strict=True)}
        jobs.append((head, "all", sub))
        vv = sub["v"].to_numpy(dtype=np.float64)
        for label, lo, hi in speed_classes:
            jobs.append((head, label, sub[(vv >= lo) & (vv < hi)]))
    seeds = spawn_seeds(seed, len(jobs))
    rows: list[dict[str, Any]] = []
    boots: list[dict[str, NDArray[np.float64]]] = []
    for (head, label, sub), row_seed in zip(jobs, seeds, strict=True):
        row: dict[str, Any] = {**head, "speed_class": label, "n_drivers": len(sub)}
        boot: dict[str, NDArray[np.float64]] = {}
        if len(sub):
            row["v_ms_p50"] = _q50(sub["v"].to_numpy(dtype=np.float64))
            if "lag_v" in sub.columns:
                row["lag_v_ms_p50"] = _q50(sub["lag_v"].to_numpy(dtype=np.float64))
            for c in ("lead_closing_ms", "lag_closing_ms"):
                if c in sub.columns:
                    row[f"{c}_p50"] = _q50(sub[c].to_numpy(dtype=np.float64))
            row["share_with_rejected"] = round(float(np.mean(sub["n_rejected_gaps"] > 0)), 4)
            row["lookback_s_p50"] = _q50(sub["lookback_s"].to_numpy(dtype=np.float64))
            fits, boot = _fit_one(
                sub, points, n_boot=n_boot, seed=row_seed, min_drivers=min_drivers
            )
            row.update(fits)
        rows.append(row)
        boots.append(boot)
    return rows, boots


# --- the mapping onto the weave's acceptance -------------------------------------------


def model_parity_critical_gaps(
    v: Sequence[float] | NDArray[np.float64],
    params: AcceptanceParams,
    *,
    rightward: bool,
    accept_s: float | None = None,
) -> dict[str, NDArray[np.float64]]:
    """The acceptance's critical time gaps at speed parity, per speed [s].

    Evaluates :func:`calibration.lane_change_gaps.weave_acceptance` with the
    neighbours at the changer's speed ``v`` and returns ``lead_s`` (need on the
    leader side over ``v``), ``lag_s`` (need on the follower side over the
    follower's speed ``v``), and the follower side's two parts, ``lag_time_s``
    (``A + 2 s0 / v``) and ``lag_absorb_s`` (the absorption gap over ``v``).

    Args:
        v: Speeds [m/s] (positive).
        params: :class:`AcceptanceParams`.
        rightward: The movement's direction (exiting = True).
        accept_s: Override of the movement's time gap ``A``.

    Returns:
        Arrays keyed ``lead_s``, ``lag_s``, ``lag_time_s``, ``lag_absorb_s``.
    """
    vv = np.asarray(v, dtype=np.float64)
    p = params
    if accept_s is not None:
        if rightward:
            p = dataclasses.replace(params, exit_accept_gap_s=float(accept_s))
        else:
            p = dataclasses.replace(params, accept_gap_s=float(accept_s))
    a_now = p.exit_accept_gap_s if rightward else p.accept_gap_s
    one = np.ones_like(vv)
    acc = weave_acceptance(vv, one, vv, one, vv, np.full(vv.shape, rightward), p)
    rad = 1.0 - (vv / p.v0_ms) ** 4 + p.b / p.a_max
    with np.errstate(divide="ignore", invalid="ignore"):
        absorb = np.where(
            rad > 0.0, (p.s0_m + vv * p.T_s) / np.sqrt(np.where(rad > 0, rad, 1.0)), np.inf
        )
        return {
            "lead_s": np.asarray(acc["need_lead_m"], dtype=np.float64) / vv,
            "lag_s": np.asarray(acc["need_lag_m"], dtype=np.float64) / vv,
            "lag_time_s": a_now + 2.0 * p.s0_m / vv,
            "lag_absorb_s": absorb / vv,
        }


def implied_follower_decel(t_lag_s: float, v_f: float, params: AcceptanceParams) -> float:
    """The deceleration [m/s²] a follower at ``v_f`` needs toward a changer at
    parity a bumper gap of ``t_lag_s · v_f`` ahead (``−a_IDM``; the acceptance
    requires it to be at most ``b``)."""
    p = params
    acc = _idm_accel_vec(
        np.array([v_f]),
        p.v0_ms,
        np.array([t_lag_s * v_f]),
        np.zeros(1),
        p.T_s,
        p.a_max,
        p.b,
        p.s0_m,
    )
    return float(-acc[0])


def acceptance_mapping(
    rows: Sequence[Mapping[str, Any]],
    boots: Sequence[Mapping[str, NDArray[np.float64]]],
    params: AcceptanceParams,
    *,
    movement: str,
    estimator: Literal["joint", "separate"] = "joint",
) -> dict[str, Any]:
    """The acceptance time gap ``A`` that reproduces a movement's fitted median critical gaps.

    Uses the speed-class rows (not ``"all"``) of one group that have a fit.
    Per class ``c`` with ``n_c`` drivers, median changer speed ``v̄_c`` and
    median accepted-lag speed ``v̄_F,c``: ``A_lead,c = t̂_lead,c − 2 s0 / v̄_c``;
    ``A_lag,c = t̂_lag,c − 2 s0 / v̄_F,c`` where ``t̂_lag,c`` is at or above the
    absorption floor (else no ``A`` reproduces it: ``lag_above_absorb_floor``
    False; the follower deceleration the median gap implies is given for every
    class). A negative implied ``A`` means the ``2 s0`` floor alone asks more
    than the fitted median (``*_reachable`` False). Classes whose fit is
    degenerate (``sigma`` at its floor) are skipped. The proposal is the
    ``n``-weighted least-squares ``A`` over the classes — per side
    (``accept_s_lead``, ``accept_s_lag``) and, for the model's single time
    gap per movement, over both sides together (``accept_s``) — floored at 0.
    The 95 % intervals are percentile intervals over the bootstrap replicates,
    each class resampled independently.

    Args:
        rows: :func:`fit_groups` rows of one group (``all`` rows are skipped).
        boots: The aligned bootstrap medians.
        params: The acceptance's current parameters (``s0``, ``T``, ``a_max``,
            ``b``, ``v0``, and the current ``A``).
        movement: ``"entering"`` or ``"exiting"``.
        estimator: Which fit the proposal reads.

    Returns:
        JSON-ready dict: ``parameter``, ``current``, per-class table,
        ``accept_s_lead``, ``accept_s_lag``, ``accept_s`` (each with ``ci95``),
        and the model's parity critical gaps at the proposal per class.
    """
    key, rightward = MOVEMENT_PARAMETER[movement]
    current = params.exit_accept_gap_s if rightward else params.accept_gap_s
    s0 = params.s0_m
    lead_key = "joint" if estimator == "joint" else "separate_lead"
    lag_key = "joint" if estimator == "joint" else "separate_lag"
    classes: list[dict[str, Any]] = []
    boot_lead: list[NDArray[np.float64]] = []
    boot_lag: list[NDArray[np.float64]] = []
    for row, bt in zip(rows, boots, strict=True):
        if row.get("speed_class") == "all" or row.get("v_ms_p50") is None:
            continue
        lead_fit = row.get(lead_key) or {}
        lag_fit = row.get(lag_key) or {}
        if not lead_fit.get("fitted") or not lag_fit.get("fitted"):
            continue
        lead_d = lead_fit["lead"] if estimator == "joint" else lead_fit
        lag_d = lag_fit["lag"] if estimator == "joint" else lag_fit
        if lead_d.get("degenerate") or lag_d.get("degenerate"):
            continue
        t_l = float(lead_d["median_s"])
        t_g = float(lag_d["median_s"])
        n_c = int(lead_fit["n_used"])
        v_c = float(row["v_ms_p50"])
        v_f = float(row.get("lag_v_ms_p50") or v_c)
        par_now_l = model_parity_critical_gaps([v_c], params, rightward=rightward)
        par_now_g = model_parity_critical_gaps([v_f], params, rightward=rightward)
        floor_g = float(par_now_g["lag_absorb_s"][0])
        a_l = t_l - 2.0 * s0 / v_c
        a_g = t_g - 2.0 * s0 / v_f
        feasible = t_g >= floor_g
        classes.append(
            {
                "speed_class": row["speed_class"],
                "n": n_c,
                "v_ms": v_c,
                "lag_v_ms": v_f,
                "fitted_lead_median_s": t_l,
                "fitted_lag_median_s": t_g,
                "model_lead_s_now": round(float(par_now_l["lead_s"][0]), 4),
                "model_lag_s_now": round(float(par_now_g["lag_s"][0]), 4),
                "lag_absorb_floor_s": round(floor_g, 4),
                "implied_accept_s_lead": round(a_l, 4),
                "implied_accept_s_lag": round(a_g, 4) if feasible else None,
                "lag_above_absorb_floor": bool(feasible),
                "lead_reachable": bool(a_l >= 0.0),
                "lag_reachable": bool(feasible and a_g >= 0.0),
                "implied_follower_decel_ms2": round(implied_follower_decel(t_g, v_f, params), 4),
            }
        )
        suffix_l = "joint_lead" if estimator == "joint" else "separate_lead"
        suffix_g = "joint_lag" if estimator == "joint" else "separate_lag"
        boot_lead.append(np.asarray(bt.get(suffix_l, np.full(1, np.nan))) - 2.0 * s0 / v_c)
        boot_lag.append(
            np.asarray(bt.get(suffix_g, np.full(1, np.nan))) - 2.0 * s0 / v_f
            if feasible
            else np.full(1, np.nan)
        )
    out: dict[str, Any] = {
        "parameter": key,
        "movement": movement,
        "estimator": estimator,
        "current": current,
        "classes": classes,
    }
    if not classes:
        out.update({"accept_s_lead": None, "accept_s_lag": None, "accept_s": None})
        return out
    n = np.array([c["n"] for c in classes], dtype=np.float64)
    al = np.array([c["implied_accept_s_lead"] for c in classes], dtype=np.float64)
    feas = np.array([c["lag_above_absorb_floor"] for c in classes], dtype=bool)
    ag = np.array(
        [c["implied_accept_s_lag"] if c["lag_above_absorb_floor"] else np.nan for c in classes],
        dtype=np.float64,
    )

    def _wmean(vals: NDArray[np.float64], wts: NDArray[np.float64]) -> float:
        ok = np.isfinite(vals) & (wts > 0)
        return float(np.sum(vals[ok] * wts[ok]) / np.sum(wts[ok])) if ok.any() else math.nan

    both_v = np.concatenate([al, ag])
    both_w = np.concatenate([n, np.where(feas, n, 0.0)])
    point = {
        "accept_s_lead": _wmean(al, n),
        "accept_s_lag": _wmean(ag, np.where(feas, n, 0.0)),
        "accept_s": _wmean(both_v, both_w),
    }
    n_rep = min((b.size for b in boot_lead), default=0)
    reps: dict[str, list[float]] = {k: [] for k in point}
    if n_rep > 1:
        for i in range(n_rep):
            bl = np.array([b[i] for b in boot_lead])
            bg = np.array([b[i] if b.size > 1 else np.nan for b in boot_lag])
            reps["accept_s_lead"].append(_wmean(bl, n))
            reps["accept_s_lag"].append(_wmean(bg, np.where(feas, n, 0.0)))
            reps["accept_s"].append(
                _wmean(np.concatenate([bl, bg]), np.concatenate([n, np.where(feas, n, 0.0)]))
            )
    for k, val in point.items():
        if not math.isfinite(val):
            out[k] = None
            continue
        ci = _ci(np.asarray(reps[k], dtype=np.float64)) if reps[k] else None
        out[k] = {
            "value": round(max(val, 0.0), 4),
            "unfloored": round(val, 4),
            "ci95": None if ci is None else [max(ci[0], 0.0), max(ci[1], 0.0)],
        }
    a_star = out["accept_s"]["value"] if out["accept_s"] else None
    if a_star is not None:
        for c in classes:
            pl = model_parity_critical_gaps(
                [c["v_ms"]], params, rightward=rightward, accept_s=a_star
            )
            pg = model_parity_critical_gaps(
                [c["lag_v_ms"]], params, rightward=rightward, accept_s=a_star
            )
            c["model_lead_s_at_proposal"] = round(float(pl["lead_s"][0]), 4)
            c["model_lag_s_at_proposal"] = round(float(pg["lag_s"][0]), 4)
    return out
