"""Uncertainty over driver behaviour and demand (WP-106, Frisco plan Stage 1 item 11).

docs/FRISCO_PROTOCOL.md §8.5: the best setting of each strategy and the
do-nothing baseline are re-run with the driver settings and the demand varied
within their plausible ranges — at least :data:`PROTOCOL_MIN_SAMPLES`
parameter samples (Latin hypercube), each with at least
:data:`PROTOCOL_MIN_SEEDS` seeds — and a strategy's effect is called robust
only if its sign holds in at least :data:`ROBUST_SIGN_SHARE` of the samples.
This module is the analysis half; ``scripts/uncertainty_runs.py`` runs the
design through ``scripts/corridor_sweep.py``'s worker, so every metric is the
one a sweep records, over the same window.

**The parameter space** (:class:`UncertainParameter`, :class:`ParameterSpace`).
Every parameter carries a ``source`` — a parameter without one is refused,
so no range can be chosen to make a result look robust. The kinds and what
each changes in a :class:`~flowstate_core.config.ScenarioConfig`
(:data:`KIND_MAPS_TO`):

``demand_scale``
    One factor on every boundary inflow step and every on-ramp inflow step
    (off-ramp exit fractions are shares, not counts, and stay). Range ``1 ±``
    the count error the study's data-quality artifact records
    (``parameters.count_error`` of ``calibration.data_quality``'s JSON, read
    by :func:`data_quality_count_error`; basis ``data_quality_count_error``),
    docs/FRISCO_PROTOCOL.md §8.5's rule. Without that artifact the range is
    ``1 ± COUNT_ERROR`` — the ±5 % default of
    ``calibration.conservation.DEFAULT_COUNT_ERROR`` — flagged ``assumed``
    (basis ``count_error``). One common factor treats the count error as a
    corridor-wide bias, the case that moves total demand most; independent
    per-detector errors would partly cancel.
``t_scale`` / ``v0_scale``
    A factor on the passenger population's mean desired time headway ``T``
    / mean desired speed ``v0``. For an artifact population
    (``fleet.idm_calibration``) the sample gets a derived ``IDMCalibration``
    with the mean multiplied and the covariance unchanged — the derivation
    of ``scripts/calibrate_capacity.py`` and of ``calibration.transfer_check``'s
    knobs; for a scalar fleet ``fleet.T`` / ``fleet.v0`` is multiplied. The
    heavy population is untouched (the transfer check's knobs act on the
    passenger population only).

    **With the corridor's transfer check** (``transfer``: the parsed
    ``transfer_check.json``, schema :data:`TRANSFER_SCHEMA`; WP-106b) the
    range is what is not known about *this corridor's* population: the knob
    values whose model value stays inside the observed 95 % interval of the
    quantity the knob controls — mean T ↔ capacity per lane, mean v0 ↔
    free-flow speed — as ``calibration.transfer_check.uncertainty_range``
    read them off its curves (basis ``observed_interval``, not assumed),
    carried over in the parameter's own units (mean T in s, mean v0 in m/s)
    and cut to this population's measured range below. Where the check could
    not read an interval (not observed, only a lower bound, too few days, an
    inconclusive verdict, no curve inside the interval) the measured range is
    used, flagged ``assumed``, with the check's reason in the source (basis
    ``measured_range_fallback``); so is a range of any basis other than
    ``observed_interval`` (e.g. the check's ``analytical_index_fallback``,
    capacity read off its analytical index rather than a simulated capacity —
    §8.5 then asks for the measured range, labelled assumed). An observed
    interval range is widened to include the configured (calibrated) mean
    before it is cut to the measured range (§8.5). The check must have been
    run on this population or on one it derives from by mean T / v0 alone
    (:func:`_transfer_population`); any other is refused.

    **Without it** the range is the *measured range* of
    docs/FRISCO_PROTOCOL.md §7.2 — the measured source population's mean ±
    :data:`MEASURED_RANGE_SIGMAS` standard deviations of its drivers
    (``calibration.transfer_check.measured_range``; a corridor-wide mean may
    move inside the central ~68 % of what the trajectories measured across
    drivers), within the CLAUDE.md §3.1 calibration range — expressed as a
    factor on the configured mean, and flagged ``assumed`` (basis
    ``measured_range``): it is the spread of individual drivers, the range
    calibration may choose a population from, not a calibration uncertainty,
    and it makes nearly every result uncertain for reasons unrelated to what
    is not known. The source of a derived population (a capacity-calibrated
    T scaling) is the one its ``*.calibration.json`` sidecar names, used
    only when the two differ in nothing but mean T / v0
    (:func:`source_population`); otherwise the configured artifact is its own
    source, and the configured mean need not sit in the middle (I-24's
    capacity population has mean T 1.32 s in a measured range of
    0.99–2.03 s). ``centre="configured"`` narrows the measured range to the
    configured mean ± the same spread, recorded in the source (it is also
    the cut and the fallback of a transfer-check range). A scalar fleet's
    spread is its configured ``heterogeneity_frac``, not a measurement
    (basis ``configured_spread``, assumed); a transfer check needs an
    artifact population.
``heavy_fraction``
    ``fleet.heavy.fraction`` set to the value; only for a scenario with a
    heavy population. Range: a measured interval with its source
    (``heavy_range``/``heavy_source``), else the transfer check's
    classification-count interval (basis ``classification_interval``), else
    the configured share ± :data:`HEAVY_SHARE_ASSUMED_HALF_WIDTH`, flagged
    ``assumed`` (basis ``assumed_tolerance``) — the transfer check's
    truck-share tolerance (3 points), the smallest difference it calls a
    mismatch.

Every parameter records its ``basis`` (:data:`BASIS_WORDS`), shown beside its
range in the plan and the report.

**Sampling** (:func:`latin_hypercube`, :func:`sample_space`). A Latin
hypercube on ``[0, 1)^d`` from one seeded PCG64 stream (McKay, Beckman &
Conover 1979, Technometrics 21:239–245): each parameter's range is cut into
``n`` equal strata and every stratum is hit exactly once. Implemented
directly in numpy rather than ``scipy.stats.qmc`` so that the design is fixed
by the pinned numpy stream alone (scipy's sampler has changed its seeding
argument and defaults across releases). Seeds nest in samples
(:func:`run_seeds`): every sample has its own seeds and every arm of a sample
runs those same seeds, so arms pair by sample *and* seed.

**Aggregation** (:func:`nested_summary`, :func:`aggregate`). Per arm and
metric, and for each strategy arm's paired differences against the
baseline, the two-level model

    y_ij = μ + s_i + e_ij,   i = 1..M samples,  j = 1..K_i seeds of sample i,

with ``s_i`` the parameter sample's effect (variance σ_s²) and ``e_ij`` the
seed's (variance σ_e²), independent; seeds are nested in samples. The pooled
mean weights samples equally (each stands for an equal-probability stratum
of the parameter space): ``μ̂ = (1/M) Σ_i ȳ_i`` with ``ȳ_i`` the mean over
sample ``i``'s seeds. Then ``Var(ȳ_i) = σ_s² + σ_e²/K_i`` and

    Var(μ̂) = σ_s²/M + σ_e²/M² · Σ_i 1/K_i     (balanced: σ_s²/M + σ_e²/(M·K)),

the between-sample and the within-sample (seed) variance combined. For
independent ``X_i`` with common mean and variances ``v_i``,
``E[Σ (X_i − X̄)²] = (1 − 1/M) Σ v_i``, so the variance of the sample means
``S_B² = Σ (ȳ_i − μ̂)²/(M − 1)`` has expectation ``(1/M) Σ v_i`` and
``S_B²/M`` is unbiased for ``Var(μ̂)``: both components enter through it. In
the balanced case ``S_B² = MS_B/K``, with ``MS_B`` the between-sample mean
square of the one-way random-effects ANOVA, ``E[MS_B] = σ_e² + K σ_s²``, so
``S_B²/M = MS_B/(M K)``. The interval is

    μ̂ ± t(0.975, M − 1) · S_B / √M,

exact under normality in the balanced case and approximate when runs are
missing (unequal ``K_i``; no Satterthwaite correction is applied). The
Latin hypercube's samples are stratified, not independent; for an output
monotone in each input the stratified mean's variance is at most the
independent one's (McKay, Beckman & Conover 1979), so treating them as
independent errs wide. The components are reported as standard deviations:
``σ̂_e² = Σ_i Σ_j (y_ij − ȳ_i)² / Σ_i (K_i − 1)`` (pooled within-sample) and
``σ̂_s² = max(0, S_B² − σ̂_e² · mean_i(1/K_i))``. The spread of the outcome
over the plausible range is the 5th–95th percentile of the ``ȳ_i`` (numpy's
linear interpolation, Hyndman–Fan type 7); each ``ȳ_i`` still carries its
seed noise ``σ_e²/K_i``.

**Paired effects and robustness.** For a strategy arm, ``d_ij = y^arm_ij −
y^base_ij`` on the seeds where both are finite, analysed with the same
formulas. The effect's direction holds in sample ``i`` when ``sign(d̄_i) =
sign(μ̂_d) ≠ 0``; the share is over every sample of the design (a sample
without an estimate counts against robustness), and the effect is ``robust``
iff the share is at least :data:`ROBUST_SIGN_SHARE`, else ``uncertain``
(§8.5). ``resolved`` says, separately, whether the pooled interval excludes
zero.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal, get_args

import numpy as np
from scipy.stats import t as student_t

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import (
    OSMNetwork,
    RingNetwork,
    ScenarioConfig,
    config_hash,
)
from flowstate_core.constants import IDM_RANGES
from flowstate_core.rng import make_rng
from validation.criteria import zero_collisions

SCHEMA: Final[str] = "flowstate.uncertainty/1"
"""Schema tag of :meth:`UncertaintyResult.to_json`."""

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
"""Repository root: scenario files reference artifacts repo-relatively."""

ParameterKind = Literal["demand_scale", "t_scale", "v0_scale", "heavy_fraction"]
"""What a parameter changes in a scenario (module docstring)."""

PARAMETER_KINDS: Final[tuple[ParameterKind, ...]] = get_args(ParameterKind)
"""Every kind, in the order a default space lists them."""

KIND_MAPS_TO: Final[Mapping[str, str]] = {
    "demand_scale": (
        "factor on every network.inflow step and every on-ramp's inflow step "
        "(off-ramp exit fractions unchanged)"
    ),
    "t_scale": (
        "factor on the passenger population's mean desired time headway T: a derived "
        "IDMCalibration (mean T x factor, covariance unchanged) as fleet.idm_calibration, "
        "or fleet.T for a scalar fleet; the heavy population unchanged"
    ),
    "v0_scale": (
        "factor on the passenger population's mean desired speed v0: a derived "
        "IDMCalibration (mean v0 x factor, covariance unchanged) as fleet.idm_calibration, "
        "or fleet.v0 for a scalar fleet; the heavy population unchanged"
    ),
    "heavy_fraction": "fleet.heavy.fraction set to the value",
}
"""How each kind maps to the configuration (recorded in every output)."""

COUNT_ERROR: Final[float] = 0.05
"""Relative count error of every detector (±5 %), the half-width of
``demand_scale`` when no data-quality artifact is given, then flagged
assumed. Reason: ``calibration.conservation.DEFAULT_COUNT_ERROR``, the
data-quality check's stated working assumption (a test checks they agree;
``validation`` does not depend on ``calibration``)."""

DATA_QUALITY_SCHEMA: Final[str] = "flowstate.data_quality/1"
"""Schema of the data-quality artifact whose ``parameters.count_error`` sets
the demand range (``calibration.data_quality.QUALITY_SCHEMA``; a test checks
they agree)."""

MEASURED_RANGE_SIGMAS: Final[float] = 1.0
"""Half-width of a driver knob's range in standard deviations of the
population's drivers. Reason: ``calibration.transfer_check.MEASURED_RANGE_SIGMAS``
(a test checks they agree) — a corridor-wide mean may move inside the
central ~68 % of what the trajectories measured across drivers."""

HEAVY_SHARE_ASSUMED_HALF_WIDTH: Final[float] = 0.03
"""Half-width of the assumed truck-share range (3 percentage points), used
only when no measured interval is supplied, and then flagged ``assumed``.
Reason: ``calibration.transfer_check.HEAVY_SHARE_TOLERANCE`` (a test checks
they agree) — the smallest truck-share difference the transfer check calls a
mismatch (it moves the passenger-car-equivalent flow by 3 %, inside the
capacity tolerance)."""

HEAVY_FRACTION_BOUNDS: Final[tuple[float, float]] = (0.0, 0.5)
"""Settable truck share (``HeavyVehicleSpec.fraction`` bounds)."""

TRANSFER_SCHEMA: Final[str] = "flowstate.transfer_check/1"
"""Schema of the driver-settings check's JSON
(``calibration.transfer_check.TRANSFER_SCHEMA``; a test checks they agree)."""

TRANSFER_QUANTITY: Final[Mapping[str, str]] = {
    "t_scale": "capacity_per_lane",
    "v0_scale": "free_flow_speed",
}
"""The transfer-check comparison whose ``uncertainty_range`` sets a driver knob."""

RangeBasis = Literal[
    "count_error",
    "data_quality_count_error",
    "observed_interval",
    "measured_range_fallback",
    "measured_range",
    "configured_spread",
    "classification_interval",
    "assumed_tolerance",
    "stated",
]
"""Where a parameter's range comes from (module docstring)."""

RANGE_BASES: Final[tuple[RangeBasis, ...]] = get_args(RangeBasis)
"""Every basis."""

BASIS_WORDS: Final[Mapping[str, str]] = {
    "count_error": "assumed detector count error (no data-quality artifact given)",
    "data_quality_count_error": "count error recorded in the data-quality artifact",
    "observed_interval": "observed 95 % interval (transfer check)",
    "measured_range_fallback": "§7.2 measured range; the transfer check gave no interval",
    "measured_range": "§7.2 measured range; no transfer check given",
    "configured_spread": "configured spread of a scalar fleet",
    "classification_interval": "measured truck-share interval",
    "assumed_tolerance": "± 3 points, assumed",
    "stated": "stated with the run",
}
"""Each basis in words (the plan and the report)."""

DRIVER_ASSUMED_BASES: Final[frozenset[str]] = frozenset(
    {"measured_range", "measured_range_fallback", "configured_spread"}
)
"""Driver-knob bases that are the wide transfer range, not a calibration uncertainty."""

ROBUST_SIGN_SHARE: Final[float] = 0.90
"""Share of the samples in which an effect's sign must hold for it to be
called robust (docs/FRISCO_PROTOCOL.md §8.5)."""

PROTOCOL_MIN_SAMPLES: Final[int] = 10
"""Fewest parameter samples of a protocol result (§8.5)."""

PROTOCOL_MIN_SEEDS: Final[int] = 5
"""Fewest seeds per sample of a protocol result (§8.5)."""

INTERVAL_LEVEL: Final[float] = 0.95
"""Level of every interval (CLAUDE.md §0.6)."""

SPREAD_PERCENTILES: Final[tuple[float, float]] = (5.0, 95.0)
"""Percentiles of the per-sample means reported as the spread over the
plausible range."""

DEFAULT_BASELINE: Final[str] = "baseline"
"""Name of the do-nothing arm (``scripts/corridor_sweep.py``'s baseline cell)."""

METRIC_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "throughput_veh_h": ("throughput at the reference section", "veh/h"),
    "total_delay_incl_waiting_veh_h": (
        "total delay including waiting on ramps and to enter",
        "veh-h",
    ),
    "mean_tt_incl_waiting_s": ("mean travel time including waiting", "s"),
    "p90_tt_incl_waiting_s": ("90th-percentile travel time including waiting", "s"),
    "mean_tt_s": ("mean travel time of the vehicles that completed the span", "s"),
    "p90_tt_s": ("90th-percentile travel time of the vehicles that completed the span", "s"),
    "sigma_v_temporal_ms": ("speed variation over time (temporal σ_v)", "m/s"),
    "sigma_v_spatial_ms": ("speed variation across vehicles (spatial σ_v)", "m/s"),
    "fuel_ml_per_veh_km": ("fuel per vehicle-km (a model estimate)", "ml/veh-km"),
    "wave_count": ("number of stop-and-go waves", "waves"),
    "wave_speed_kmh": ("wave speed", "km/h"),
    "wave_amplitude_ms": ("wave amplitude", "m/s"),
    "vht_veh_h": ("vehicle-hours travelled", "veh-h"),
    "vmt_veh_km": ("vehicle-kilometres travelled", "veh-km"),
    "n_travel_time_veh": ("vehicles that completed the span", "veh"),
    "insertion_delay_veh_h": ("time spent waiting to enter the road", "veh-h"),
    "meter_wait_veh_h": ("time held at ramp meters", "veh-h"),
    "n_censored": ("vehicles still on their way at the run's end (censored)", "veh"),
    "n_demand_veh": ("vehicles planned to depart in the scoring window", "veh"),
    "n_not_inserted": ("planned vehicles that never entered the road", "veh"),
    "n_tt_incl_waiting_veh": ("vehicles behind the travel time including waiting", "veh"),
    "n_tt_censored": ("of those, still on their way at the run's end", "veh"),
}
"""Plain-language names and units of ``validation.metrics.Metrics`` fields and
of ``validation.metrics.WaitingMetrics`` (WP-105: what the sweep records
beside them); a metric without an entry is named by its key."""

DEFAULT_HEADLINE: Final[str] = "total_delay_incl_waiting_veh_h"
"""Metric of the plain-language headline when the caller names none: total
delay including waiting time, the protocol's tuning objective (§8.2, §8.4),
which ``scripts/corridor_sweep.py``'s worker records (WP-105)."""

FALLBACK_HEADLINE: Final[str] = "mean_tt_s"
"""The headline when the runs carry no :data:`DEFAULT_HEADLINE` (runs written
before the demand ledger); the result states the substitution
(:attr:`UncertaintyResult.headline_note`)."""

Verdict = Literal["robust", "uncertain", "not_estimable"]
Direction = Literal["increase", "decrease", "none", "not_estimable"]


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def _json_num(x: float | None) -> float | None:
    """JSON-safe number: non-finite becomes null."""
    return float(x) if x is not None and math.isfinite(x) else None


def resolve_repo_path(path: str | Path) -> Path:
    """A path as given when it exists, else relative to the repository root
    (the rule of ``microsim.vehicles.resolve_calibration_path``).

    Raises:
        FileNotFoundError: Neither exists.
    """
    p = Path(path)
    if p.is_file():
        return p
    candidate = REPO_ROOT / p
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"not found: {path!r} (also tried {candidate})")


def file_sha256(path: str | Path) -> str:
    """sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(payload: Any) -> str:
    """12 hex chars of the sha256 of ``payload``'s canonical JSON form."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


# --- parameter space ------------------------------------------------------------


@dataclass(frozen=True)
class UncertainParameter:
    """One varied quantity and its plausible range.

    Attributes:
        name: Label (unique in a space).
        kind: What it changes (:data:`KIND_MAPS_TO`).
        low: Lower end of the range (a factor for the ``*_scale`` kinds, the
            share for ``heavy_fraction``).
        high: Upper end (strictly above ``low``).
        source: Where the range comes from — a measured artifact or a stated
            assumption. Required: a parameter without one is refused.
        assumed: The range is an assumption, not a measurement (reports say so).
        nominal: The base scenario's value (1.0 for a factor).
        basis: Where the range comes from (:data:`BASIS_WORDS`); None when
            not stated (a hand-built parameter).
    """

    name: str
    kind: ParameterKind
    low: float
    high: float
    source: str
    assumed: bool = False
    nominal: float | None = None
    basis: RangeBasis | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an uncertain parameter needs a name")
        if self.basis is not None and self.basis not in RANGE_BASES:
            raise ValueError(
                f"{self.name}: unknown basis {self.basis!r}; choose from {', '.join(RANGE_BASES)}"
            )
        if self.kind not in PARAMETER_KINDS:
            raise ValueError(
                f"{self.name}: unknown kind {self.kind!r}; choose from {', '.join(PARAMETER_KINDS)}"
            )
        if not self.source.strip():
            raise ValueError(
                f"{self.name}: no source for its range — every range must come from a measured "
                "artifact or a stated assumption (WP-106), never be chosen for the result"
            )
        if not (math.isfinite(self.low) and math.isfinite(self.high)) or not self.low < self.high:
            raise ValueError(f"{self.name}: the range must be finite with low < high")
        if self.kind == "heavy_fraction":
            lo, hi = HEAVY_FRACTION_BOUNDS
            if self.low < lo or self.high > hi:
                raise ValueError(f"{self.name}: a truck share must lie in [{lo}, {hi}]")
        elif self.low <= 0.0:
            raise ValueError(f"{self.name}: a scale factor must be positive")

    def value_at(self, u: float) -> float:
        """The value at the unit coordinate ``u`` ∈ [0, 1) (linear map)."""
        return self.low + u * (self.high - self.low)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "name": self.name,
            "kind": self.kind,
            "low": self.low,
            "high": self.high,
            "nominal": _json_num(self.nominal),
            "assumed": self.assumed,
            "basis": self.basis,
            "source": self.source,
            "maps_to": KIND_MAPS_TO[self.kind],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> UncertainParameter:
        """Inverse of :meth:`to_dict` (a record without ``basis`` reads as None)."""
        nominal = raw.get("nominal")
        return cls(
            name=str(raw["name"]),
            kind=raw["kind"],
            low=float(raw["low"]),
            high=float(raw["high"]),
            source=str(raw["source"]),
            assumed=bool(raw.get("assumed", False)),
            nominal=None if nominal is None else float(nominal),
            basis=raw.get("basis"),
        )


@dataclass(frozen=True)
class ParameterSpace:
    """The declared parameters, at most one per kind."""

    parameters: tuple[UncertainParameter, ...]

    def __post_init__(self) -> None:
        if not self.parameters:
            raise ValueError("a parameter space needs at least one parameter")
        names = [p.name for p in self.parameters]
        kinds = [p.kind for p in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate parameter names: {names}")
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"at most one parameter per kind: {kinds}")

    def by_kind(self, kind: str) -> UncertainParameter | None:
        """The parameter of ``kind``, if declared."""
        return next((p for p in self.parameters if p.kind == kind), None)

    def with_range(
        self,
        kind: str,
        low: float,
        high: float,
        source: str,
        *,
        assumed: bool = False,
        basis: RangeBasis | None = "stated",
    ) -> ParameterSpace:
        """This space with ``kind``'s range replaced (or the kind added).

        The nominal value is kept; the new range needs its own source.
        """
        current = self.by_kind(kind)
        if kind not in PARAMETER_KINDS:
            raise ValueError(f"unknown kind {kind!r}; choose from {', '.join(PARAMETER_KINDS)}")
        new = UncertainParameter(
            name=current.name if current is not None else kind,
            kind=kind,
            low=float(low),
            high=float(high),
            source=source,
            assumed=assumed,
            nominal=current.nominal if current is not None else None,
            basis=basis,
        )
        if current is None:
            return ParameterSpace((*self.parameters, new))
        return ParameterSpace(tuple(new if p.kind == kind else p for p in self.parameters))

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"parameters": [p.to_dict() for p in self.parameters]}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ParameterSpace:
        """Inverse of :meth:`to_dict`."""
        return cls(tuple(UncertainParameter.from_dict(p) for p in raw["parameters"]))


def _has_demand(config: ScenarioConfig) -> bool:
    net = config.network
    if isinstance(net, RingNetwork):
        return False
    if net.inflow:
        return True
    return isinstance(net, OSMNetwork) and any(r.kind == "on" and r.inflow for r in net.ramps)


SIDECAR_SUFFIX: Final[str] = ".calibration.json"
"""Suffix of the provenance sidecar ``scripts/calibrate_capacity.py`` writes beside a
derived population (``<stem>.calibration.json``, its ``source`` the measured
population it was derived from)."""

CentreRule = Literal["measured", "configured"]
"""Where a driver knob's range is centred (:func:`default_space`)."""


@dataclass(frozen=True)
class PopulationStats:
    """Means and driver standard deviations of one population, with provenance.

    Attributes:
        means: Passenger means (v0, T, a_max, b, s0).
        sds: Standard deviations of the drivers (covariance diagonal, or
            ``heterogeneity_frac × mean`` for a scalar fleet).
        label: Where the numbers come from (path and sha256, or the fleet).
        measured: Fitted from trajectories (an ``IDMCalibration``).
    """

    means: dict[str, float]
    sds: dict[str, float]
    label: str
    measured: bool


def _artifact_stats(path_text: str, resolved: Path, cal: IDMCalibration) -> PopulationStats:
    cov = np.asarray(cal.cov, dtype=float)
    sd = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    return PopulationStats(
        means={k: float(cal.mean[k]) for k in cal.param_names},
        sds={k: float(v) for k, v in zip(cal.param_names, sd, strict=True)},
        label=(
            f"{path_text} (sha256 {file_sha256(resolved)[:12]}; "
            f"{cal.n_episodes_fit} fitted episodes)"
        ),
        measured=True,
    )


def _differs_only_in_means(pop: IDMCalibration, src: IDMCalibration, keys: Sequence[str]) -> bool:
    """``pop`` is ``src`` with only the means of ``keys`` changed (covariance and
    every other mean equal) — the derivation of ``scripts/calibrate_capacity.py``
    (the rule of ``calibration.transfer_check``'s sidecar check)."""
    if tuple(pop.param_names) != tuple(src.param_names):
        return False
    if not np.allclose(np.asarray(pop.cov), np.asarray(src.cov), rtol=1e-9, atol=1e-12):
        return False
    return all(
        math.isclose(pop.mean[k], src.mean[k], rel_tol=1e-9, abs_tol=1e-12)
        for k in pop.param_names
        if k not in keys
    )


def source_population(path_text: str) -> tuple[PopulationStats, str] | None:
    """The measured population a configured artifact was derived from.

    The ``source`` named by a capacity-calibration sidecar: first
    ``<stem>.calibration.json`` beside the artifact, else any
    ``*.calibration.json`` in its directory whose source this artifact equals
    with only mean T and/or v0 scaled (:func:`_differs_only_in_means`). A
    sidecar whose source differs in anything else is not used.

    Args:
        path_text: ``fleet.idm_calibration`` as the scenario gives it.

    Returns:
        ``(stats of the source, the sidecar's path)``, or None when no sidecar
        names a verified source (the artifact is then its own measured
        population).
    """
    resolved = resolve_repo_path(path_text)
    pop = IDMCalibration.load(resolved)
    own = resolved.parent / f"{resolved.stem}{SIDECAR_SUFFIX}"
    others = sorted(p for p in resolved.parent.glob(f"*{SIDECAR_SUFFIX}") if p != own)
    for sidecar in ([own] if own.is_file() else []) + others:
        try:
            raw = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        src_text = raw.get("source") if isinstance(raw, dict) else None
        if not isinstance(src_text, str):
            continue
        try:
            src_path = resolve_repo_path(src_text)
            src = IDMCalibration.load(src_path)
        except (FileNotFoundError, ValueError):
            continue
        if src_path.resolve() == resolved.resolve():
            continue
        if _differs_only_in_means(pop, src, ("T", "v0")):
            return _artifact_stats(src_text, src_path, src), str(sidecar)
    return None


@dataclass(frozen=True)
class _MeasuredRange:
    """A driver mean's measured range for this scenario, in its own units."""

    lo: float
    hi: float
    mean: float
    text: str
    scalar: bool


def _measured_range(
    idm_key: str, config: ScenarioConfig, sigmas: float, centre: CentreRule
) -> _MeasuredRange:
    """The protocol §7.2 measured range of ``idm_key``'s mean (module docstring)."""
    fleet = config.fleet
    cal_lo, cal_hi = IDM_RANGES[idm_key]
    if fleet.idm_calibration is None:
        means = {"v0": fleet.v0, "T": fleet.T, "a_max": fleet.a_max, "b": fleet.b, "s0": fleet.s0}
        mean = means[idm_key]
        sd = fleet.heterogeneity_frac * mean
        lo, hi = max(mean - sigmas * sd, cal_lo), min(mean + sigmas * sd, cal_hi)
        text = (
            f"configured scalar fleet: mean {idm_key} {mean:.4g} ± {sigmas:g} x its configured "
            f"spread (heterogeneity_frac {fleet.heterogeneity_frac:g} x mean = {sd:.4g}) within "
            f"CLAUDE.md §3.1's {cal_lo:g}–{cal_hi:g}: {idm_key} {lo:.4g}–{hi:.4g}. A scalar "
            "fleet's spread is not a measurement"
        )
        return _MeasuredRange(lo, hi, mean, text, scalar=True)
    resolved = resolve_repo_path(fleet.idm_calibration)
    configured = _artifact_stats(fleet.idm_calibration, resolved, IDMCalibration.load(resolved))
    found = source_population(fleet.idm_calibration)
    ref, via = found if found is not None else (configured, None)
    mean = configured.means[idm_key]
    r_mean, r_sd = ref.means[idm_key], ref.sds[idm_key]
    lo, hi = max(r_mean - sigmas * r_sd, cal_lo), min(r_mean + sigmas * r_sd, cal_hi)
    lineage = (
        f"the measured population {ref.label}, source of the configured "
        f"{configured.label} (sidecar {via}; covariance shared, mean T / v0 scaled)"
        if via is not None
        else f"the measured population {configured.label} (no sidecar names another source)"
    )
    text = (
        f"{lineage}: mean {idm_key} {r_mean:.4g} ± {sigmas:g} sd of its drivers ({r_sd:.4g}, "
        f"the covariance diagonal) within CLAUDE.md §3.1's {cal_lo:g}–{cal_hi:g} — the "
        f"measured range of docs/FRISCO_PROTOCOL.md §7.2 (calibration.transfer_check."
        f"measured_range, MEASURED_RANGE_SIGMAS): {idm_key} {lo:.4g}–{hi:.4g}"
    )
    if centre == "configured":
        sd = configured.sds[idm_key]
        lo, hi = max(lo, mean - sigmas * sd), min(hi, mean + sigmas * sd)
        text += (
            f"; narrowed to the configured mean {mean:.4g} ± {sigmas:g} sd ({sd:.4g}) by "
            f"request (centre='configured'): {idm_key} {lo:.4g}–{hi:.4g}"
        )
    return _MeasuredRange(lo, hi, mean, text, scalar=False)


def _factor_text(mean: float, lo: float, hi: float) -> str:
    text = f"; as a factor on the configured mean {mean:.4g}"
    if not lo <= mean <= hi:
        text += " (the configured mean lies outside this range)"
    return text


def _transfer_comparisons(transfer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The comparisons of a transfer-check JSON, its schema checked."""
    if transfer.get("schema") != TRANSFER_SCHEMA:
        raise ValueError(
            f"not a transfer-check report: schema {transfer.get('schema')!r}, expected "
            f"{TRANSFER_SCHEMA!r} (scripts/transfer_check.py's transfer_check.json)"
        )
    comparisons = transfer.get("comparisons")
    if not isinstance(comparisons, list):
        raise ValueError("the transfer-check report has no comparisons")
    return [c for c in comparisons if isinstance(c, Mapping)]


def _transfer_entry(transfer: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    """The ``uncertainty_range`` of the comparison that sets ``kind``.

    Raises:
        ValueError: No such comparison, or a report from before the ranges
            existed (WP-106b), or a malformed range.
    """
    quantity = TRANSFER_QUANTITY[kind]
    for c in _transfer_comparisons(transfer):
        if c.get("quantity") != quantity:
            continue
        entry = c.get("uncertainty_range")
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"the transfer-check report's {quantity} comparison has no uncertainty_range: "
                "it predates WP-106b; rerun scripts/transfer_check.py"
            )
        basis = entry.get("basis")
        if entry.get("knob") != kind or not isinstance(basis, str) or not basis.strip():
            raise ValueError(
                f"the transfer-check report's {quantity} uncertainty_range is not a {kind} range "
                f"with a stated basis: {dict(entry)}"
            )
        if entry["basis"] == "observed_interval" and (
            entry.get("parameter_low") is None or entry.get("parameter_high") is None
        ):
            raise ValueError(f"the transfer-check report's {quantity} range has no values")
        return entry
    raise ValueError(f"the transfer-check report has no {quantity} comparison")


def _transfer_population(transfer: Mapping[str, Any], config: ScenarioConfig) -> str:
    """How this scenario's driver population relates to the one the check ran on.

    The ranges are model curves of that population, carried over in absolute
    units (mean T in s, mean v0 in m/s): valid for the same population and
    for one that differs from it only in mean T and/or v0 (a calibration's
    corridor-wide adjustment, :func:`_differs_only_in_means`) under the same
    car-following model.

    Returns:
        The relation in words.

    Raises:
        ValueError: A scalar fleet on either side, another car-following
            model, another population, or a checked artifact that is gone or
            changed (nothing to compare with).
    """
    model = transfer.get("model")
    model = model if isinstance(model, Mapping) else {}
    checked_model = model.get("model")
    if checked_model != config.fleet.model:
        raise ValueError(
            f"the transfer check ran the population under {checked_model}, the scenario runs "
            f"{config.fleet.model}: its curves do not apply; rerun scripts/transfer_check.py on "
            "this scenario"
        )
    if config.fleet.idm_calibration is None:
        raise ValueError(
            "a transfer-check range needs an artifact population (fleet.idm_calibration; "
            "docs/FRISCO_PROTOCOL.md §7.2 uses the measured populations); this fleet is scalar"
        )
    sources = model.get("population_sources")
    sources = sources if isinstance(sources, Mapping) else {}
    checked_text, checked_sha = (
        sources.get("idm_calibration"),
        sources.get("idm_calibration_sha256"),
    )
    if not checked_text or not checked_sha:
        raise ValueError(
            "the transfer check was not run on an artifact population (no idm_calibration in "
            "its model.population_sources)"
        )
    own_path = resolve_repo_path(config.fleet.idm_calibration)
    own_sha = file_sha256(own_path)
    if own_sha == checked_sha:
        return f"this population, {checked_text} (sha256 {str(checked_sha)[:12]})"
    try:
        checked_path: Path | None = resolve_repo_path(str(checked_text))
    except FileNotFoundError:
        checked_path = None
    if checked_path is None or file_sha256(checked_path) != checked_sha:
        raise ValueError(
            f"the transfer check ran on {checked_text} (sha256 {str(checked_sha)[:12]}), not on "
            f"this scenario's population {config.fleet.idm_calibration} (sha256 {own_sha[:12]}), "
            "and that file is missing or changed, so the two cannot be compared: rerun "
            "scripts/transfer_check.py on this scenario"
        )
    if _differs_only_in_means(
        IDMCalibration.load(own_path), IDMCalibration.load(checked_path), ("T", "v0")
    ):
        return (
            f"{checked_text} (sha256 {str(checked_sha)[:12]}), which this scenario's population "
            f"{config.fleet.idm_calibration} differs from in mean T / v0 alone"
        )
    raise ValueError(
        f"the transfer check ran on another driver population ({checked_text}) than this "
        f"scenario's ({config.fleet.idm_calibration}), and they differ in more than mean T / v0: "
        "rerun scripts/transfer_check.py on this scenario"
    )


def _transfer_heavy(
    transfer: Mapping[str, Any], label: str
) -> tuple[tuple[float, float] | None, str]:
    """The transfer check's classification-count interval of the truck share.

    Returns:
        ``(range or None, its source or why there is none)``.
    """
    observed = transfer.get("observed")
    heavy = observed.get("heavy") if isinstance(observed, Mapping) else None
    if not isinstance(heavy, Mapping) or not heavy.get("available"):
        return None, f"{label} had no classification counts"
    iv = heavy.get("interval")
    if not isinstance(iv, Mapping) or iv.get("lo") is None or iv.get("hi") is None:
        return None, f"{label}'s classification counts gave no interval (too few days)"
    b_lo, b_hi = HEAVY_FRACTION_BOUNDS
    lo, hi = max(float(iv["lo"]), b_lo), min(float(iv["hi"]), b_hi)
    if not lo < hi:
        return None, f"{label}'s classification interval is a single value ({lo:.4g})"
    definition = f" ({heavy['definition']})" if heavy.get("definition") else ""
    share = heavy.get("share")
    share_text = f"{float(share):.4g}" if share is not None else "—"
    return (lo, hi), (
        f"classification counts of {label}: truck share {share_text}{definition}, "
        f"{float(iv.get('level', 0.95)) * 100:g} % interval over {iv.get('n_units')} "
        f"{iv.get('unit')}(s) {lo:.4g}–{hi:.4g}"
    )


PARAMETER_UNITS: Final[Mapping[str, str]] = {"T": "s", "v0": "m/s"}
"""Units of the driver means a transfer-check range is carried over in."""

WIDE_RANGE_WORDS: Final[str] = (
    "the wide transfer range of docs/FRISCO_PROTOCOL.md §7.2 — the spread of individual "
    "drivers, which calibration may choose a population from — not a calibration uncertainty "
    "of this corridor's population"
)
"""How a driver range on the measured range is described (assumed)."""


def _driver_parameter(
    kind: ParameterKind,
    idm_key: str,
    config: ScenarioConfig,
    sigmas: float,
    centre: CentreRule,
    transfer: Mapping[str, Any] | None = None,
    transfer_label: str = "the transfer check",
    lineage: str = "",
) -> UncertainParameter:
    m = _measured_range(idm_key, config, sigmas, centre)
    basis: RangeBasis
    lo, hi = m.lo, m.hi
    if transfer is None:
        assumed = True
        if m.scalar:
            basis = "configured_spread"
            source = f"assumed — {m.text}; used as a wide range, not a calibration uncertainty"
        else:
            basis = "measured_range"
            source = (
                f"assumed — {WIDE_RANGE_WORDS}; no transfer check was given (pass the corridor's "
                f"transfer_check.json for ranges from its observed intervals): {m.text}"
                f"{_factor_text(m.mean, lo, hi)}. The artifact carries no interval for its mean"
            )
    else:
        entry = _transfer_entry(transfer, kind)
        why: str | None = None
        if entry["basis"] == "observed_interval":
            p_lo, p_hi = float(entry["parameter_low"]), float(entry["parameter_high"])
            # §8.5: widened to include the configured (calibrated) mean, then
            # clipped to the measured range.
            w_lo, w_hi = min(p_lo, m.mean), max(p_hi, m.mean)
            c_lo, c_hi = max(w_lo, m.lo), min(w_hi, m.hi)
            if c_lo < c_hi:
                lo, hi = c_lo, c_hi
                basis, assumed = "observed_interval", False
                unit = PARAMETER_UNITS.get(idm_key, "")
                source = (
                    f"observed — {transfer_label}, run on {lineage}: {entry['reason']}: mean "
                    f"{idm_key} {p_lo:.4g}–{p_hi:.4g} {unit}"
                )
                if w_lo < p_lo or w_hi > p_hi:
                    source += (
                        f"; widened to include the configured mean {m.mean:.4g} {unit} "
                        f"(docs/FRISCO_PROTOCOL.md §8.5): {idm_key} {w_lo:.4g}–{w_hi:.4g} {unit}"
                    )
                if c_lo > w_lo or c_hi < w_hi:
                    source += (
                        f"; cut to this population's measured range ({m.text}): {idm_key} "
                        f"{lo:.4g}–{hi:.4g} {unit}"
                    )
                source += _factor_text(m.mean, lo, hi)
            else:
                why = (
                    f"its range of mean {idm_key} {p_lo:.4g}–{p_hi:.4g} lies outside this "
                    f"population's measured range {m.lo:.4g}–{m.hi:.4g}"
                )
        else:
            # Any basis other than an observed interval (a fallback, or capacity
            # read off the check's analytical index) is the measured range,
            # labelled assumed (§8.5).
            why = (
                f"{entry.get('reason') or 'no reason recorded'} (its range basis is "
                f"{entry['basis']}, not an observed interval)"
            )
        if why is not None:
            basis, assumed = "measured_range_fallback", True
            source = (
                f"assumed — {transfer_label} (run on {lineage}) gave no observed-interval range: "
                f"{why}; so {WIDE_RANGE_WORDS}, is used: {m.text}{_factor_text(m.mean, lo, hi)}"
            )
    if not lo < hi:
        raise ValueError(
            f"{kind}: the {idm_key} range {lo:.4g}–{hi:.4g} is empty; state a range with its source"
        )
    return UncertainParameter(
        name=kind,
        kind=kind,
        low=lo / m.mean,
        high=hi / m.mean,
        source=source,
        assumed=assumed,
        nominal=1.0,
        basis=basis,
    )


def data_quality_count_error(raw: Mapping[str, Any]) -> float:
    """The count error a data-quality artifact records (``parameters.count_error``).

    Args:
        raw: The parsed JSON of ``calibration.data_quality``'s report (schema
            :data:`DATA_QUALITY_SCHEMA`).

    Returns:
        The relative count error, a fraction in ``(0, 1)``.

    Raises:
        ValueError: Another schema, or no usable ``parameters.count_error``.
    """
    if raw.get("schema") != DATA_QUALITY_SCHEMA:
        raise ValueError(
            f"not a data-quality artifact: schema {raw.get('schema')!r}, expected "
            f"{DATA_QUALITY_SCHEMA!r} (scripts/data_quality_report.py's JSON)"
        )
    params = raw.get("parameters")
    value = params.get("count_error") if isinstance(params, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("the data-quality artifact records no parameters.count_error")
    error = float(value)
    if not (math.isfinite(error) and 0.0 < error < 1.0):
        raise ValueError(f"the data-quality artifact's count_error {error!r} is not in (0, 1)")
    return error


def default_space(
    config: ScenarioConfig,
    *,
    count_error: float = COUNT_ERROR,
    count_error_source: str | None = None,
    sigmas: float = MEASURED_RANGE_SIGMAS,
    heavy_range: tuple[float, float] | None = None,
    heavy_source: str | None = None,
    kinds: Sequence[str] | None = None,
    centre: CentreRule = "measured",
    transfer: Mapping[str, Any] | None = None,
    transfer_label: str | None = None,
) -> ParameterSpace:
    """The default space of a scenario (module docstring, every range sourced).

    Args:
        config: The base scenario.
        count_error: Relative count error behind ``demand_scale``.
        count_error_source: Where ``count_error`` was read (the data-quality
            artifact's path and sha256, :func:`data_quality_count_error`);
            None: the default :data:`COUNT_ERROR`, flagged assumed.
        sigmas: Driver-knob half-width in driver standard deviations.
        heavy_range: A measured truck-share interval; ``heavy_source`` is then
            required. Without it the transfer check's classification interval
            is used when there is one, else the assumed range, flagged.
        heavy_source: Where ``heavy_range`` comes from.
        kinds: Restrict to these kinds (each must be derivable for this
            scenario); default: every derivable kind.
        centre: ``measured`` (default): the measured range of protocol §7.2,
            the source population's mean ± ``sigmas`` sd; ``configured``:
            that range narrowed to the configured mean ± ``sigmas`` sd
            (recorded in the source). It is the driver range without a
            transfer check, and the cut and fallback with one.
        transfer: The corridor's parsed ``transfer_check.json`` (schema
            :data:`TRANSFER_SCHEMA`): driver ranges from its
            ``uncertainty_range`` entries (WP-106b). Without it every driver
            range is the measured range, flagged assumed.
        transfer_label: How ``transfer`` is cited in the sources (its path
            and sha256); default ``the transfer check``.

    Raises:
        ValueError: A requested kind cannot apply to this scenario (demand on a
            ring, a truck share without a heavy population), ``heavy_range``
            comes without a source, or ``transfer`` is not a transfer-check
            report of this scenario's population (:func:`_transfer_population`).
    """
    if heavy_range is not None and not (heavy_source or "").strip():
        raise ValueError("heavy_range needs heavy_source: where the measured interval comes from")
    wanted = list(PARAMETER_KINDS) if kinds is None else [str(k) for k in kinds]
    unknown = [k for k in wanted if k not in PARAMETER_KINDS]
    if unknown:
        raise ValueError(f"unknown kinds {unknown}; choose from {', '.join(PARAMETER_KINDS)}")
    if centre not in ("measured", "configured"):
        raise ValueError(f"centre must be 'measured' or 'configured', got {centre!r}")
    label = (transfer_label or "").strip() or "the transfer check"
    lineage = ""
    if transfer is not None:
        _transfer_comparisons(transfer)  # the schema, before anything is derived
        if "t_scale" in wanted or "v0_scale" in wanted:
            lineage = _transfer_population(transfer, config)
    explicit = kinds is not None
    params: list[UncertainParameter] = []
    for kind in PARAMETER_KINDS:
        if kind not in wanted:
            continue
        if kind == "demand_scale":
            if not _has_demand(config):
                if explicit:
                    raise ValueError("demand_scale: this scenario has no inflow to scale")
                continue
            recorded = bool((count_error_source or "").strip())
            params.append(
                UncertainParameter(
                    name=kind,
                    kind=kind,
                    low=1.0 - count_error,
                    high=1.0 + count_error,
                    source=(
                        f"detector count error ±{count_error:.3g} as recorded in the study's "
                        f"data-quality artifact {count_error_source} (parameters.count_error; "
                        "docs/FRISCO_PROTOCOL.md §8.5), applied as one corridor-wide factor on "
                        "every inflow"
                        if recorded
                        else f"assumed — detector count error ±{count_error:.0%} "
                        "(calibration.conservation.DEFAULT_COUNT_ERROR, the data-quality "
                        "check's default working assumption, not a measurement of these "
                        "detectors); no data-quality artifact was given (pass the study's "
                        "data-quality JSON for its recorded count error), applied as one "
                        "corridor-wide factor on every inflow"
                    ),
                    assumed=not recorded,
                    nominal=1.0,
                    basis="data_quality_count_error" if recorded else "count_error",
                )
            )
        elif kind in ("t_scale", "v0_scale"):
            params.append(
                _driver_parameter(
                    kind,
                    "T" if kind == "t_scale" else "v0",
                    config,
                    sigmas,
                    centre,
                    transfer,
                    label,
                    lineage,
                )
            )
        else:  # heavy_fraction
            heavy = config.fleet.heavy
            if heavy is None:
                if explicit:
                    raise ValueError("heavy_fraction: this scenario has no heavy population")
                continue
            b_lo, b_hi = HEAVY_FRACTION_BOUNDS
            measured_heavy, why_not = (
                _transfer_heavy(transfer, label) if transfer is not None else (None, "")
            )
            basis: RangeBasis
            if heavy_range is not None:
                lo, hi = max(heavy_range[0], b_lo), min(heavy_range[1], b_hi)
                source, assumed, basis = str(heavy_source), False, "classification_interval"
            elif measured_heavy is not None:
                lo, hi = measured_heavy
                source, assumed, basis = why_not, False, "classification_interval"
            else:
                lo = max(heavy.fraction - HEAVY_SHARE_ASSUMED_HALF_WIDTH, b_lo)
                hi = min(heavy.fraction + HEAVY_SHARE_ASSUMED_HALF_WIDTH, b_hi)
                source = (
                    f"assumed: the configured share {heavy.fraction:.3g} ± "
                    f"{HEAVY_SHARE_ASSUMED_HALF_WIDTH:.2f} (calibration.transfer_check."
                    "HEAVY_SHARE_TOLERANCE, the smallest difference that check calls a "
                    "mismatch); no measured truck-share interval was supplied"
                    + (f" ({why_not})" if why_not else "")
                )
                assumed, basis = True, "assumed_tolerance"
            params.append(
                UncertainParameter(
                    name=kind,
                    kind=kind,
                    low=lo,
                    high=hi,
                    source=source,
                    assumed=assumed,
                    nominal=heavy.fraction,
                    basis=basis,
                )
            )
    return ParameterSpace(tuple(params))


# --- sampling -------------------------------------------------------------------


def latin_hypercube(n: int, d: int, seed: int) -> np.ndarray:
    """A seeded Latin hypercube of ``n`` points in ``[0, 1)^d``.

    For each dimension in order: a permutation of the ``n`` strata, then one
    uniform jitter per point, both from ``make_rng(seed)``; point ``k`` lies in
    stratum ``perm[k]`` (``[perm[k]/n, (perm[k] + 1)/n)``), so each stratum of
    each dimension is hit exactly once.

    Returns:
        Array of shape ``(n, d)``.
    """
    if n < 1 or d < 1:
        raise ValueError(f"need n >= 1 and d >= 1, got n={n}, d={d}")
    rng = make_rng(seed)
    out = np.empty((n, d), dtype=float)
    for j in range(d):
        perm = rng.permutation(n)
        out[:, j] = (perm + rng.uniform(size=n)) / n
    return out


@dataclass(frozen=True)
class SampleValue:
    """One parameter's value in one sample."""

    name: str
    kind: ParameterKind
    value: float


@dataclass(frozen=True)
class Sample:
    """One point of the design."""

    sample_id: str
    index: int
    values: tuple[SampleValue, ...]

    def value_of(self, kind: str) -> float | None:
        """The value of ``kind``, None when the sample does not vary it."""
        return next((v.value for v in self.values if v.kind == kind), None)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "sample_id": self.sample_id,
            "index": self.index,
            "values": {v.name: v.value for v in self.values},
            "kinds": {v.name: v.kind for v in self.values},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Sample:
        """Inverse of :meth:`to_dict`."""
        kinds = raw["kinds"]
        return cls(
            sample_id=str(raw["sample_id"]),
            index=int(raw["index"]),
            values=tuple(
                SampleValue(name=str(name), kind=kinds[name], value=float(value))
                for name, value in raw["values"].items()
            ),
        )


def sample_id(index: int, n: int) -> str:
    """``s00``, ``s01``, … (at least two digits, enough for ``n``)."""
    return f"s{index:0{max(2, len(str(max(n - 1, 0))))}d}"


def sample_space(space: ParameterSpace, n: int, seed: int) -> list[Sample]:
    """``n`` Latin-hypercube samples of ``space`` (column ``j`` = parameter ``j``)."""
    unit = latin_hypercube(n, len(space.parameters), seed)
    return [
        Sample(
            sample_id=sample_id(i, n),
            index=i,
            values=tuple(
                SampleValue(name=p.name, kind=p.kind, value=float(p.value_at(float(unit[i, j]))))
                for j, p in enumerate(space.parameters)
            ),
        )
        for i in range(n)
    ]


def run_seeds(master_seed: int, n_samples: int, per_sample: int) -> list[list[int]]:
    """Seeds nested in samples: ``per_sample`` distinct seeds for each sample.

    ``SeedSequence(master_seed).spawn(n_samples)[i].spawn(per_sample)[j]``,
    reduced as ``flowstate_core.rng.spawn_seeds`` reduces a child. They are
    grandchildren of the master, so they never coincide with a sweep's
    evaluation seeds (``spawn_seeds(master_seed, n)``, its children); a
    sample's first ``k`` seeds do not depend on ``per_sample`` and its seeds
    not on ``n_samples``.
    """
    if n_samples < 0 or per_sample < 0:
        raise ValueError("counts must be >= 0")
    children = np.random.SeedSequence(master_seed).spawn(n_samples)
    return [
        [int(c.generate_state(1, dtype=np.uint64)[0] % (2**63)) for c in child.spawn(per_sample)]
        for child in children
    ]


# --- applying a sample ----------------------------------------------------------


def derived_population(
    source: IDMCalibration,
    *,
    t_scale: float,
    v0_scale: float,
    source_label: str,
    created_at: str,
) -> IDMCalibration:
    """``source`` with mean T and mean v0 multiplied, covariance unchanged."""
    mean = dict(source.mean)
    mean["T"] = mean["T"] * t_scale
    mean["v0"] = mean["v0"] * v0_scale
    notes = (
        f"Derived by validation.uncertainty (WP-106) from {source_label}: mean T x "
        f"{t_scale:.6g}, mean v0 x {v0_scale:.6g}; covariance and the other means unchanged "
        "(the derivation of scripts/calibrate_capacity.py). One parameter sample of an "
        "uncertainty run, never a calibration."
    )
    return source.model_copy(update={"mean": mean, "notes": notes, "created_at": created_at})


def _population_file(
    source_path: str, t_scale: float, v0_scale: float, population_dir: Path, created_at: str | None
) -> str:
    resolved = resolve_repo_path(source_path)
    sha = file_sha256(resolved)
    key = _digest({"source_sha256": sha, "t_scale": repr(t_scale), "v0_scale": repr(v0_scale)})
    target = population_dir / f"idm_{key}.json"
    source = IDMCalibration.load(resolved)
    expected = derived_population(
        source,
        t_scale=t_scale,
        v0_scale=v0_scale,
        source_label=f"{source_path} (sha256 {sha[:12]})",
        created_at=created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if target.is_file():
        stored = IDMCalibration.load(target)
        same = stored.param_names == expected.param_names and all(
            math.isclose(stored.mean[k], expected.mean[k], rel_tol=1e-12, abs_tol=0.0)
            for k in expected.param_names
        )
        if not same or not np.array_equal(np.asarray(stored.cov), np.asarray(expected.cov)):
            raise ValueError(f"{target} exists with a different population; remove it")
    else:
        expected.save(target)
    return str(target)


def apply(
    sample: Sample,
    config: ScenarioConfig,
    *,
    population_dir: str | Path | None = None,
    created_at: str | None = None,
) -> ScenarioConfig:
    """``config`` with ``sample``'s values applied (:data:`KIND_MAPS_TO`).

    Deterministic: the same sample, config and ``population_dir`` give the
    same configuration and so the same config hash. A derived population is
    written once to ``population_dir/idm_<key>.json``, the key a digest of the
    source artifact's bytes and the two factors (so the path, which the hash
    covers, is fixed by the inputs); an existing file is checked, never
    rewritten.

    Args:
        sample: The sample.
        config: The base scenario (not modified).
        population_dir: Where derived populations go; required when the
            fleet is an artifact population and the sample moves T or v0.
        created_at: ``created_at`` of a newly written derived population
            (default: now, UTC).

    Raises:
        ValueError: A kind that cannot apply to this scenario, or a derived
            population needed without ``population_dir``.
    """
    doc = config.model_dump(mode="json")
    demand = sample.value_of("demand_scale")
    if demand is not None:
        if isinstance(config.network, RingNetwork):
            raise ValueError("demand_scale: a ring has no inflow to scale")
        net = doc["network"]
        net["inflow"] = [[t, q * demand] for t, q in net.get("inflow") or []]
        for ramp in net.get("ramps") or []:
            if ramp.get("kind") == "on":
                ramp["inflow"] = [[t, q * demand] for t, q in ramp.get("inflow") or []]
    heavy = sample.value_of("heavy_fraction")
    if heavy is not None:
        if doc["fleet"].get("heavy") is None:
            raise ValueError("heavy_fraction: this scenario has no heavy population")
        doc["fleet"]["heavy"]["fraction"] = heavy
    t_scale = sample.value_of("t_scale")
    v0_scale = sample.value_of("v0_scale")
    t_f = 1.0 if t_scale is None else t_scale
    v_f = 1.0 if v0_scale is None else v0_scale
    if t_f != 1.0 or v_f != 1.0:
        fleet = doc["fleet"]
        if fleet.get("idm_calibration"):
            if population_dir is None:
                raise ValueError(
                    "the fleet is an artifact population: a T or v0 sample needs population_dir "
                    "for its derived population"
                )
            pdir = Path(population_dir)
            pdir.mkdir(parents=True, exist_ok=True)
            fleet["idm_calibration"] = _population_file(
                str(fleet["idm_calibration"]), t_f, v_f, pdir, created_at
            )
        else:
            fleet["T"] = fleet["T"] * t_f
            fleet["v0"] = fleet["v0"] * v_f
    return ScenarioConfig.model_validate(doc)


def sample_hashes(
    samples: Iterable[Sample],
    config: ScenarioConfig,
    *,
    population_dir: str | Path | None = None,
) -> dict[str, str]:
    """``sample_id`` → config hash of the sample's scenario (before any arm)."""
    return {
        s.sample_id: config_hash(apply(s, config, population_dir=population_dir)) for s in samples
    }


# --- aggregation ----------------------------------------------------------------


@dataclass(frozen=True)
class RunRecord:
    """One run's metrics.

    Attributes:
        arm: Arm name (the baseline included).
        sample_id: Parameter sample.
        seed: Replicate seed (shared by the sample's arms).
        metrics: Metric name → value (``metrics.json``); non-finite values
            are skipped.
        n_collisions: ``meta.json["n_collisions"]``; None when not recorded.
    """

    arm: str
    sample_id: str
    seed: int
    metrics: Mapping[str, float]
    n_collisions: int | None = None


@dataclass(frozen=True)
class NestedSummary:
    """Pooled mean over the plausible range with its two-level interval.

    Attributes:
        mean: ``μ̂`` (None when no sample has a value).
        lo95: Lower end of the 95 % interval (None with fewer than 2 samples).
        hi95: Upper end.
        p05: 5th percentile of the per-sample means.
        p95: 95th percentile.
        sd_between: ``σ̂_s`` (None with fewer than 2 samples or no within df).
        sd_within: ``σ̂_e`` (None without a sample of 2+ seeds).
        n_samples: Samples with at least one value (``M``).
        n_runs: Values used (``Σ K_i``).
        df: ``M − 1`` (None below 2 samples).
        sample_means: ``sample_id`` → ``ȳ_i``.
        sample_n: ``sample_id`` → ``K_i``.
    """

    mean: float | None
    lo95: float | None
    hi95: float | None
    p05: float | None
    p95: float | None
    sd_between: float | None
    sd_within: float | None
    n_samples: int
    n_runs: int
    df: int | None
    sample_means: dict[str, float] = field(default_factory=dict)
    sample_n: dict[str, int] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        """The interval excludes zero."""
        if self.lo95 is None or self.hi95 is None:
            return False
        return self.lo95 > 0.0 or self.hi95 < 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "mean": _json_num(self.mean),
            "lo95": _json_num(self.lo95),
            "hi95": _json_num(self.hi95),
            "p05": _json_num(self.p05),
            "p95": _json_num(self.p95),
            "sd_between_samples": _json_num(self.sd_between),
            "sd_within_samples": _json_num(self.sd_within),
            "n_samples": self.n_samples,
            "n_runs": self.n_runs,
            "df": self.df,
            "per_sample": {
                sid: {"mean": _json_num(m), "n_seeds": self.sample_n[sid]}
                for sid, m in self.sample_means.items()
            },
        }


def nested_summary(groups: Mapping[str, Sequence[float]]) -> NestedSummary:
    """The two-level summary of values grouped by sample (module docstring).

    Args:
        groups: ``sample_id`` → the sample's values over its seeds; non-finite
            values are dropped and a sample left empty contributes nothing.
    """
    means: dict[str, float] = {}
    counts: dict[str, int] = {}
    ss_within = 0.0
    df_within = 0
    for sid, vals in groups.items():
        arr = np.asarray([v for v in vals if _finite(v)], dtype=float)
        if arr.size == 0:
            continue
        means[sid] = float(arr.mean())
        counts[sid] = int(arr.size)
        if arr.size > 1:
            ss_within += float(((arr - arr.mean()) ** 2).sum())
            df_within += int(arr.size) - 1
    m = len(means)
    n_runs = sum(counts.values())
    if m == 0:
        return NestedSummary(None, None, None, None, None, None, None, 0, 0, None)
    ybar = np.asarray(list(means.values()), dtype=float)
    mu = float(ybar.mean())
    p05, p95 = (float(x) for x in np.percentile(ybar, SPREAD_PERCENTILES))
    var_within = ss_within / df_within if df_within > 0 else None
    sd_within = math.sqrt(var_within) if var_within is not None else None
    if m < 2:
        return NestedSummary(
            mu, None, None, p05, p95, None, sd_within, m, n_runs, None, means, counts
        )
    s2_between = float(ybar.var(ddof=1))
    half = float(student_t.ppf(0.5 + INTERVAL_LEVEL / 2.0, m - 1)) * math.sqrt(s2_between / m)
    sd_between: float | None = None
    if var_within is not None:
        inv_k = float(np.mean([1.0 / k for k in counts.values()]))
        sd_between = math.sqrt(max(0.0, s2_between - var_within * inv_k))
    return NestedSummary(
        mean=mu,
        lo95=mu - half,
        hi95=mu + half,
        p05=p05,
        p95=p95,
        sd_between=sd_between,
        sd_within=sd_within,
        n_samples=m,
        n_runs=n_runs,
        df=m - 1,
        sample_means=means,
        sample_n=counts,
    )


def _sign(x: float) -> int:
    return (x > 0.0) - (x < 0.0)


def robustness(
    sample_effects: Mapping[str, float], pooled: float | None, n_design: int
) -> tuple[int, float | None, Verdict, Direction]:
    """The §8.5 verdict of one effect.

    Args:
        sample_effects: ``sample_id`` → the sample's mean paired effect.
        pooled: The pooled effect ``μ̂_d`` (None when not estimable).
        n_design: Samples in the design (the denominator: a sample without
            an estimate counts against robustness).

    Returns:
        ``(n_same_sign, share, verdict, direction)``; ``robust`` iff
        ``n_same_sign / n_design >= ROBUST_SIGN_SHARE`` (compared exactly, as
        fractions), ``uncertain`` otherwise, ``not_estimable`` without a
        pooled effect.
    """
    if pooled is None or not math.isfinite(pooled) or n_design <= 0:
        return 0, None, "not_estimable", "not_estimable"
    sign = _sign(pooled)
    direction: Direction = "increase" if sign > 0 else "decrease" if sign < 0 else "none"
    n_same = 0 if sign == 0 else sum(1 for v in sample_effects.values() if _sign(v) == sign)
    share = n_same / n_design
    robust = sign != 0 and Fraction(n_same, n_design) >= Fraction(str(ROBUST_SIGN_SHARE))
    return n_same, share, ("robust" if robust else "uncertain"), direction


@dataclass(frozen=True)
class EffectSummary:
    """One strategy arm's paired effect on one metric.

    Attributes:
        arm: Strategy arm.
        metric: Metric.
        effect: :func:`nested_summary` of the paired differences.
        baseline_mean: The baseline's pooled mean over the same pairs.
        pct_of_baseline: ``100 · effect.mean / baseline_mean``.
        n_same_sign: Samples whose mean effect has the pooled sign.
        n_samples_design: The denominator (every sample of the design).
        share_same_sign: ``n_same_sign / n_samples_design``.
        verdict: ``robust`` / ``uncertain`` / ``not_estimable`` (§8.5).
        direction: Sign of the pooled effect in words.
    """

    arm: str
    metric: str
    effect: NestedSummary
    baseline_mean: float | None
    pct_of_baseline: float | None
    n_same_sign: int
    n_samples_design: int
    share_same_sign: float | None
    verdict: Verdict
    direction: Direction

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "effect": self.effect.to_dict(),
            "resolved": self.effect.resolved,
            "baseline_mean": _json_num(self.baseline_mean),
            "pct_of_baseline": _json_num(self.pct_of_baseline),
            "direction": self.direction,
            "n_same_sign": self.n_same_sign,
            "n_samples_design": self.n_samples_design,
            "share_same_sign": _json_num(self.share_same_sign),
            "robust_threshold": ROBUST_SIGN_SHARE,
            "verdict": self.verdict,
        }


def _label(metric: str) -> tuple[str, str]:
    return METRIC_LABELS.get(metric, (metric, ""))


def _fmt(x: float | None, *, signed: bool = False) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    a = abs(x)
    if a >= 1000:
        text = f"{x:,.0f}"
    elif a >= 100:
        text = f"{x:.0f}"
    elif a >= 10:
        text = f"{x:.1f}"
    elif a >= 1:
        text = f"{x:.2f}"
    else:
        text = f"{x:.3f}"
    return f"+{text}" if signed and x > 0 else text


def _span(lo: float | None, hi: float | None, *, signed: bool = False) -> str:
    if lo is None and hi is None:
        return "—"
    return f"{_fmt(lo, signed=signed)} to {_fmt(hi, signed=signed)}"


def _with_unit(text: str, unit: str) -> str:
    return f"{text} {unit}" if unit and text != "—" else text


@dataclass(frozen=True)
class UncertaintyResult:
    """The aggregated uncertainty runs (module docstring).

    Attributes:
        baseline: The do-nothing arm's name.
        arms: Every arm, the baseline first.
        metrics: Metrics aggregated, in report order.
        sample_ids: Samples of the design.
        summaries: arm → metric → :class:`NestedSummary`.
        effects: strategy arm → metric → :class:`EffectSummary`.
        collisions: arm → collision block.
        zero_collisions: Over every run (``validation.criteria.zero_collisions``).
        n_runs: Run records aggregated.
        min_seeds_per_sample: Fewest seeds, over every arm and every sample,
            on which the arm's run is paired with the baseline's (the
            baseline's own runs for the baseline): what a §8.5 effect rests
            on, so one arm short of seeds makes the design a rehearsal.
        headline: The metric of the plain-language headline.
        space: The parameter space (None when not supplied).
        samples: The samples (empty when not supplied).
        provenance: Caller-supplied provenance (scenario, hashes, commit, …).
        missing: Expected runs without a record (caller-supplied).
        headline_note: Why the headline is not :data:`DEFAULT_HEADLINE`, when
            it is a substitute; empty otherwise.
    """

    baseline: str
    arms: tuple[str, ...]
    metrics: tuple[str, ...]
    sample_ids: tuple[str, ...]
    summaries: dict[str, dict[str, NestedSummary]]
    effects: dict[str, dict[str, EffectSummary]]
    collisions: dict[str, dict[str, Any]]
    zero_collisions: bool | None
    n_runs: int
    min_seeds_per_sample: int
    headline: str
    space: ParameterSpace | None = None
    samples: tuple[Sample, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    missing: tuple[dict[str, Any], ...] = ()
    headline_note: str = ""

    @property
    def meets_protocol_minimum(self) -> bool:
        """At least :data:`PROTOCOL_MIN_SAMPLES` samples × :data:`PROTOCOL_MIN_SEEDS`
        paired seeds in every arm and sample (:attr:`min_seeds_per_sample`)."""
        return (
            len(self.sample_ids) >= PROTOCOL_MIN_SAMPLES
            and self.min_seeds_per_sample >= PROTOCOL_MIN_SEEDS
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON form (stable key order)."""
        return {
            "schema": SCHEMA,
            "provenance": self.provenance,
            "protocol": {
                "section": "docs/FRISCO_PROTOCOL.md §8.5",
                "min_samples": PROTOCOL_MIN_SAMPLES,
                "min_seeds_per_sample": PROTOCOL_MIN_SEEDS,
                "robust_sign_share": ROBUST_SIGN_SHARE,
                "interval_level": INTERVAL_LEVEL,
                "spread_percentiles": list(SPREAD_PERCENTILES),
                "meets_protocol_minimum": self.meets_protocol_minimum,
            },
            "space": self.space.to_dict() if self.space is not None else None,
            "samples": [s.to_dict() for s in self.samples],
            "baseline": self.baseline,
            "arms": list(self.arms),
            "metrics": list(self.metrics),
            "headline": self.headline,
            "headline_note": self.headline_note,
            "n_samples_design": len(self.sample_ids),
            "min_seeds_per_sample": self.min_seeds_per_sample,
            "n_runs": self.n_runs,
            "missing_runs": list(self.missing),
            "zero_collisions": self.zero_collisions,
            "collisions": self.collisions,
            "summaries": {
                arm: {m: s.to_dict() for m, s in by_metric.items()}
                for arm, by_metric in self.summaries.items()
            },
            "effects": {
                arm: {
                    m: {**e.to_dict(), "protocol_verdict": self.protocol_verdict(e)}
                    for m, e in by_metric.items()
                }
                for arm, by_metric in self.effects.items()
            },
        }

    def protocol_verdict(self, effect: EffectSummary) -> str:
        """The §8.5 verdict as a protocol result may state it: ``effect.verdict``
        when the design meets :attr:`meets_protocol_minimum`, else ``rehearsal``
        (a direction that held in 2 of 2 samples is not a robust effect)."""
        return effect.verdict if self.meets_protocol_minimum else "rehearsal"

    def to_json(self) -> str:
        """:meth:`to_dict` as indented JSON (no NaN: non-finite is null)."""
        return json.dumps(self.to_dict(), indent=2, allow_nan=False)

    def headline_sentence(self, arm: str, metric: str | None = None) -> str:
        """The plain-language sentence for one strategy arm."""
        metric = metric or self.headline
        e = self.effects[arm].get(metric)
        label, unit = _label(metric)
        m = len(self.sample_ids)
        if e is None or e.verdict == "not_estimable":
            return f"{arm}: its effect on {label} could not be estimated from these runs."
        eff = e.effect
        text = (
            f"Under the plausible range of driver behaviour and demand, {arm} changed {label} "
            f"by {_with_unit(_fmt(eff.p05, signed=True), unit)} to "
            f"{_with_unit(_fmt(eff.p95, signed=True), unit)} (5th to 95th percentile of the "
            f"per-sample mean effects); averaged over the range, by "
            f"{_with_unit(_fmt(eff.mean, signed=True), unit)} (95 % interval "
            f"{_span(eff.lo95, eff.hi95, signed=True)})"
        )
        if e.pct_of_baseline is not None:
            text += f", {_fmt(e.pct_of_baseline, signed=True)} % of the baseline"
        if e.direction == "none":
            return text + "; the pooled change is zero, so it has no direction to hold."
        word = "robust" if e.verdict == "robust" else "uncertain"
        tail = (
            ""
            if e.verdict == "robust"
            else f" (an effect is robust only when its direction holds in at least "
            f"{ROBUST_SIGN_SHARE:.0%} of the samples)"
        )
        if not self.meets_protocol_minimum:
            tail += (
                f" — in a rehearsal below the §8.5 minimum of {PROTOCOL_MIN_SAMPLES} samples × "
                f"{PROTOCOL_MIN_SEEDS} seeds, so not a protocol verdict"
            )
        return text + f". The direction held in {e.n_same_sign} of {m} samples: {word}{tail}."

    def _driver_limitations(self) -> list[str]:
        """The limitation lines on the driver ranges, by their basis."""
        if self.space is None:
            return []
        drivers = [p for p in self.space.parameters if p.kind in ("t_scale", "v0_scale")]
        observed = [p.name for p in drivers if p.basis == "observed_interval"]
        wide = [p.name for p in drivers if p.basis in DRIVER_ASSUMED_BASES]
        lines: list[str] = []
        if observed:
            lines.append(
                f"- Driver ranges from the observed 95 % intervals ({', '.join(observed)}): the "
                "values of one corridor-wide mean whose model value stays inside the observed "
                "interval of the quantity it controls (mean T ↔ capacity per lane, mean v0 ↔ "
                "free-flow speed; calibration.transfer_check), read off analytical or simulated "
                "curves and cut to the §7.2 measured range; the spread of drivers around the "
                "mean is not varied, and the two knobs are varied independently."
            )
        if wide:
            lines.append(
                f"- Driver ranges flagged assumed ({', '.join(wide)}): the measured range of "
                "docs/FRISCO_PROTOCOL.md §7.2, the spread of the measured population's "
                "individual drivers (± 1 sd) — the range calibration may choose a population "
                "from, much wider than what is not known about this corridor's population. "
                "An effect called uncertain over it may be uncertain for reasons unrelated to "
                "that; the configured population need not sit at its centre."
            )
        return lines

    def _demand_limitation(self) -> str:
        """The limitation line on the demand range, by its basis."""
        demand = None if self.space is None else self.space.by_kind("demand_scale")
        tail = (
            ", applied as one corridor-wide factor; boundary speeds, off-ramp shares, "
            "lane-change and merge settings and the map are not varied."
        )
        if demand is not None and demand.basis == "data_quality_count_error":
            return (
                "- The demand range is the count error recorded in the study's data-quality "
                "artifact (itself the detectors' stated accuracy, not a measurement of them)" + tail
            )
        return "- The demand range is the assumed detector count error" + tail

    def _waiting_limitation(self) -> str:
        """The limitation line on waiting time, by whether the runs record it."""
        if DEFAULT_HEADLINE in self.metrics:
            return (
                "- The measures are those the corridor sweep records. Total delay and travel "
                "time including time spent waiting on ramps and to enter "
                "(docs/FRISCO_PROTOCOL.md §8.2) are measured over the vehicles planned to "
                "depart in the scoring window; the mean travel time without waiting covers "
                "only the vehicles that completed the analysed span."
            )
        return (
            "- The measures are those the corridor sweep records. These runs record no total "
            "delay including time spent waiting on ramps and to enter "
            "(docs/FRISCO_PROTOCOL.md §8.2); travel time covers only the vehicles that "
            "completed the analysed span, so a strategy that holds vehicles back can look "
            "better on it than it is."
        )

    def to_markdown(self) -> str:
        """The plain-language summary (every number from :meth:`to_dict`)."""
        prov = self.provenance
        m = len(self.sample_ids)
        lines: list[str] = ["# Uncertainty over driver behaviour and demand", ""]
        scen = prov.get("scenario")
        lines.append(
            (f"Scenario `{scen}`" if scen else "Scenario")
            + (
                f" (config hash `{prov['base_config_hash']}`)"
                if prov.get("base_config_hash")
                else ""
            )
            + f": {m} parameter samples (Latin hypercube"
            + (f", seed {prov['design_seed']}" if prov.get("design_seed") is not None else "")
            + f") × at least {self.min_seeds_per_sample} seeds each × {len(self.arms)} arm(s); "
            f"{self.n_runs} runs aggregated"
            + (f", {len(self.missing)} expected run(s) missing" if self.missing else "")
            + "."
        )
        lines.append("")
        if not self.meets_protocol_minimum:
            lines += [
                f"**Rehearsal, not a protocol result.** docs/FRISCO_PROTOCOL.md §8.5 asks for at "
                f"least {PROTOCOL_MIN_SAMPLES} samples with at least {PROTOCOL_MIN_SEEDS} seeds "
                f"each; this run has {m} samples and at least {self.min_seeds_per_sample} seeds.",
                "",
            ]
        if prov.get("code_dirty"):
            lines += ["The code had uncommitted changes when these runs were analysed.", ""]
        if self.space is not None:
            lines += [
                "## What was varied",
                "",
                "| Parameter | Range | Base value | Basis | Assumed? | Source |",
                "|---|---|---|---|---|---|",
            ]
            for p in self.space.parameters:
                basis = "not stated" if p.basis is None else BASIS_WORDS[p.basis]
                lines.append(
                    f"| {p.name} | {p.low:.4g} – {p.high:.4g} | "
                    f"{'—' if p.nominal is None else f'{p.nominal:.4g}'} | {basis} | "
                    f"{'assumed' if p.assumed else 'no'} | {p.source} |"
                )
            lines.append("")
        strategy_arms = [a for a in self.arms if a != self.baseline]
        if strategy_arms:
            lines += ["## Strategies against the do-nothing baseline", ""]
            if self.headline_note:
                lines += [self.headline_note, ""]
            for arm in strategy_arms:
                lines += [self.headline_sentence(arm), ""]
            lines += [
                "| Arm | Measure | Change (pooled) | 95 % interval | 5th–95th pct of samples "
                "| Direction held | Verdict |",
                "|---|---|---|---|---|---|---|",
            ]
            for arm in strategy_arms:
                for metric in self.metrics:
                    e = self.effects[arm].get(metric)
                    if e is None:
                        continue
                    label, unit = _label(metric)
                    eff = e.effect
                    lines.append(
                        f"| {arm} | {label} | {_with_unit(_fmt(eff.mean, signed=True), unit)} | "
                        f"{_span(eff.lo95, eff.hi95, signed=True)} | "
                        f"{_span(eff.p05, eff.p95, signed=True)} | "
                        f"{e.n_same_sign} of {e.n_samples_design} | {e.verdict}"
                        f"{'' if self.meets_protocol_minimum else ' (rehearsal)'} |"
                    )
            lines.append("")
        lines += [
            f"## The do-nothing arm ({self.baseline}) under the plausible range",
            "",
            "| Measure | Pooled mean | 95 % interval | 5th–95th pct of samples "
            "| Between-sample sd | Within-sample (seed) sd |",
            "|---|---|---|---|---|---|",
        ]
        for metric in self.metrics:
            s = self.summaries.get(self.baseline, {}).get(metric)
            if s is None:
                continue
            label, unit = _label(metric)
            lines.append(
                f"| {label} | {_with_unit(_fmt(s.mean), unit)} | {_span(s.lo95, s.hi95)} | "
                f"{_span(s.p05, s.p95)} | {_fmt(s.sd_between)} | "
                f"{_fmt(s.sd_within)} |"
            )
        lines += ["", "## Collisions", ""]
        flag = self.zero_collisions
        lines.append(
            "Zero collisions in every run."
            if flag is True
            else "Collisions occurred: "
            + ", ".join(f"{arm} {c['total']}" for arm, c in self.collisions.items() if c["total"])
            + "."
            if flag is False
            else "Not every run records its collision count, so zero collisions is not shown."
        )
        lines += [
            "",
            "## How the numbers are computed",
            "",
            "Each sample is one setting of the varied parameters; every arm of a sample runs "
            "the same seeds, so a strategy's effect is paired with the baseline by sample and "
            "seed. The pooled value is the mean of the per-sample means. Its 95 % interval is "
            "mean ± t(0.975, M − 1) · S/√M, with S the standard deviation of the M per-sample "
            "means: it carries both the variation between parameter samples and the seed-to-seed "
            "variation within them (a two-level, nested design; validation.uncertainty). The "
            "5th–95th percentile range of the per-sample means shows how far the outcome moves "
            "across the plausible range. An effect is robust only when its direction holds in "
            f"at least {ROBUST_SIGN_SHARE:.0%} of the samples (docs/FRISCO_PROTOCOL.md §8.5); "
            "a sample without an estimate counts against it.",
            "",
            "## Limitations",
            "",
            *self._driver_limitations(),
            self._demand_limitation(),
            "- The Latin-hypercube samples are treated as independent in the interval, which "
            "errs wide when the outcome moves monotonically with each parameter.",
            self._waiting_limitation(),
            "- Fuel is a model estimate (SUMO HBEFA emission classes), not measured fuel.",
            "- One corridor; model-form uncertainty is not represented by varying parameters.",
            "",
        ]
        return "\n".join(lines)


def _metric_order(records: Sequence[RunRecord], preferred: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    for r in records:
        for k, v in r.metrics.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, int | float):
                seen.add(k)
    first = [m for m in preferred if m in seen]
    return first + sorted(seen - set(first))


def aggregate(
    records: Sequence[RunRecord],
    *,
    baseline: str = DEFAULT_BASELINE,
    sample_ids: Sequence[str] | None = None,
    metrics: Sequence[str] | None = None,
    preferred_order: Sequence[str] = tuple(METRIC_LABELS),
    headline: str | None = None,
    space: ParameterSpace | None = None,
    samples: Sequence[Sample] = (),
    provenance: Mapping[str, Any] | None = None,
    missing: Sequence[Mapping[str, Any]] = (),
) -> UncertaintyResult:
    """Aggregate (arm × sample × seed) records (module docstring).

    Args:
        records: The runs.
        baseline: The do-nothing arm (must be present).
        sample_ids: Every sample of the design (the robustness denominator);
            default: the samples present in ``records``.
        metrics: Metrics to aggregate; default: every numeric metric found,
            ``preferred_order`` first, the rest sorted.
        preferred_order: Order of the known metrics.
        headline: Metric of the plain-language headline (default
            :data:`DEFAULT_HEADLINE` when present, else the first metric).
        space: The parameter space, for the report.
        samples: The samples, for the report.
        provenance: Recorded as given.
        missing: Expected runs that have no record, for the report.

    Raises:
        ValueError: No records, or no baseline record.
    """
    if not records:
        raise ValueError("no run records to aggregate")
    arms_present = sorted({r.arm for r in records})
    if baseline not in arms_present:
        raise ValueError(f"no run of the baseline arm {baseline!r}; nothing to pair against")
    arms = (baseline, *[a for a in arms_present if a != baseline])
    sids = (
        tuple(sample_ids)
        if sample_ids is not None
        else tuple(sorted({r.sample_id for r in records}))
    )
    metric_list = list(metrics) if metrics is not None else _metric_order(records, preferred_order)
    headline_note = ""
    if headline:
        head = headline
    elif DEFAULT_HEADLINE in metric_list:
        head = DEFAULT_HEADLINE
    else:
        head = FALLBACK_HEADLINE if FALLBACK_HEADLINE in metric_list else metric_list[0]
        headline_note = (
            f"These runs record no {_label(DEFAULT_HEADLINE)[0]} (docs/FRISCO_PROTOCOL.md "
            f"§8.2, the measure the headline is stated on); the headline uses "
            f"{_label(head)[0]} instead, which does not count vehicles held back on ramps or "
            "before entering the road."
        )
    if head not in metric_list:
        raise ValueError(f"headline metric {head!r} is not among the metrics aggregated")

    by_key: dict[tuple[str, str, int], RunRecord] = {}
    for r in records:
        key = (r.arm, r.sample_id, r.seed)
        if key in by_key:
            raise ValueError(f"duplicate run record {key}")
        by_key[key] = r

    def value(r: RunRecord, metric: str) -> float | None:
        v = r.metrics.get(metric)
        if v is None or isinstance(v, bool):
            return None
        fv = float(v)
        return fv if math.isfinite(fv) else None

    summaries: dict[str, dict[str, NestedSummary]] = {}
    for arm in arms:
        summaries[arm] = {}
        for metric in metric_list:
            groups: dict[str, list[float]] = {}
            for (a, sid, _seed), r in sorted(by_key.items()):
                v = value(r, metric)
                if a == arm and v is not None:
                    groups.setdefault(sid, []).append(v)
            summaries[arm][metric] = nested_summary(groups)

    effects: dict[str, dict[str, EffectSummary]] = {}
    for arm in arms[1:]:
        effects[arm] = {}
        for metric in metric_list:
            diffs: dict[str, list[float]] = {}
            base_vals: dict[str, list[float]] = {}
            for (a, sid, seed), r in sorted(by_key.items()):
                if a != arm:
                    continue
                b = by_key.get((baseline, sid, seed))
                va = value(r, metric)
                vb = value(b, metric) if b is not None else None
                if va is None or vb is None:
                    continue
                diffs.setdefault(sid, []).append(va - vb)
                base_vals.setdefault(sid, []).append(vb)
            eff = nested_summary(diffs)
            base = nested_summary(base_vals)
            n_same, share, verdict, direction = robustness(eff.sample_means, eff.mean, len(sids))
            pct = (
                100.0 * eff.mean / base.mean
                if eff.mean is not None and base.mean is not None and base.mean != 0.0
                else None
            )
            effects[arm][metric] = EffectSummary(
                arm=arm,
                metric=metric,
                effect=eff,
                baseline_mean=base.mean,
                pct_of_baseline=pct,
                n_same_sign=n_same,
                n_samples_design=len(sids),
                share_same_sign=share,
                verdict=verdict,
                direction=direction,
            )

    collisions: dict[str, dict[str, Any]] = {}
    for arm in arms:
        counts = [r.n_collisions for (a, _s, _seed), r in sorted(by_key.items()) if a == arm]
        recorded = [c for c in counts if c is not None]
        collisions[arm] = {
            "n_runs": len(counts),
            "n_runs_recorded": len(recorded),
            "total": sum(recorded) if recorded else None,
            "runs_with_collisions": [
                {"sample_id": s, "seed": seed, "n": r.n_collisions}
                for (a, s, seed), r in sorted(by_key.items())
                if a == arm and r.n_collisions
            ],
            "zero_collisions": zero_collisions(counts),
        }
    # §8.5's seeds per sample, per arm, over the seeds an arm's run is paired
    # with the baseline's run (the baseline: its own runs) — the seeds an
    # effect rests on; the fewest over every arm and sample.
    paired: list[int] = []
    for arm in arms:
        for sid in sids:
            paired.append(
                sum(
                    1
                    for a, s, seed in by_key
                    if a == arm and s == sid and (baseline, sid, seed) in by_key
                )
            )
    min_seeds = min(paired, default=0)
    return UncertaintyResult(
        baseline=baseline,
        arms=arms,
        metrics=tuple(metric_list),
        sample_ids=sids,
        summaries=summaries,
        effects=effects,
        collisions=collisions,
        zero_collisions=zero_collisions([r.n_collisions for r in by_key.values()]),
        n_runs=len(by_key),
        min_seeds_per_sample=min_seeds,
        headline=head,
        space=space,
        samples=tuple(samples),
        provenance=dict(provenance or {}),
        missing=tuple(dict(m) for m in missing),
        headline_note=headline_note,
    )


__all__ = [
    "BASIS_WORDS",
    "COUNT_ERROR",
    "DATA_QUALITY_SCHEMA",
    "DEFAULT_BASELINE",
    "DEFAULT_HEADLINE",
    "FALLBACK_HEADLINE",
    "HEAVY_SHARE_ASSUMED_HALF_WIDTH",
    "KIND_MAPS_TO",
    "MEASURED_RANGE_SIGMAS",
    "PARAMETER_KINDS",
    "PROTOCOL_MIN_SAMPLES",
    "PROTOCOL_MIN_SEEDS",
    "RANGE_BASES",
    "ROBUST_SIGN_SHARE",
    "SCHEMA",
    "TRANSFER_QUANTITY",
    "TRANSFER_SCHEMA",
    "EffectSummary",
    "NestedSummary",
    "ParameterSpace",
    "RunRecord",
    "Sample",
    "SampleValue",
    "UncertainParameter",
    "UncertaintyResult",
    "aggregate",
    "apply",
    "data_quality_count_error",
    "default_space",
    "derived_population",
    "latin_hypercube",
    "nested_summary",
    "robustness",
    "run_seeds",
    "sample_hashes",
    "sample_space",
    "source_population",
]
