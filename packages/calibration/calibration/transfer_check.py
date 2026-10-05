"""Do our driver settings fit this corridor? (WP-103, Frisco plan Stage 1 item 8).

FlowState's driver populations were measured elsewhere — on I-24 MOTION
trajectories near Nashville (``artifacts/idm_i24.json``) and on NGSIM US-101
in Los Angeles (``artifacts/idm_us101.json``). Before one of them drives a
new corridor, this module compares what the corridor's own detectors say
about its drivers with what the population would do there, quantity by
quantity, and recommends a corridor-wide adjustment **inside the measured
ranges** when they disagree (docs/FRISCO_PROTOCOL.md §7.2). It recommends; a
person decides, and the decision is recorded with the study.

Three quantities are compared, each by a rule fixed here before any corridor
is looked at (:class:`TransferRules`, every threshold with its reason in
:data:`RULE_SOURCES`):

**Free-flow speed.** Observed: the station mean speed in the five-minute
windows of light traffic — occupancy at most
:data:`FREE_FLOW_MAX_OCCUPANCY_PCT` (the free-branch cut of
:func:`calibration.fd_fit.fit_triangular_fd`), flow at most
:data:`FREE_FLOW_MAX_FLOW_VEH_H_LANE` per lane (the Highway Capacity Manual's
free-flow measurement condition) and at least
:data:`FREE_FLOW_MIN_COUNT_VEH_LANE` vehicles per lane in the window — pooled
over the mainline stations: median and 15th / 85th percentiles of the
window means (not of individual vehicles' speeds). Model: each drawn driver
wants ``min(v0, speed factor × posted limit)`` — SUMO caps the IDM's desired
speed at the lane's limit times the vehicle's ``speedFactor``, which FlowState
writes as the fleet's ``speed_factor`` on passenger vehicles (1.0 unless the
scenario sets it, WP-109; spread by ``speed_dev`` when set) and as 1.0 on
heavy vehicles (``microsim.vehicles``) — and in steady traffic at the
observed free-flow flow holds its IDM equilibrium speed, which is below its
desired speed by the ``(v/v0)^4`` term. The model value is the mean over drivers of
that equilibrium speed: what a detector's window mean would read.

**Capacity per lane.** Observed, per mainline station: the 95th percentile
of the five-minute per-lane flow (CLAUDE.md §6.1, the capacity definition of
:mod:`calibration.fd_fit`), with the stations classified by what limits
their flow. A station downstream of an *active bottleneck* (the definition of
docs/FRISCO_PROTOCOL.md §5: upstream below 40 mph, downstream at least 20 mph
faster, in 5 of 7 consecutive windows) measures the bottleneck's discharge —
road-limited flow; a station that never slows below 40 mph for 15 minutes
never reached capacity and its flow is a lower bound. The corridor value is
the median over the bottleneck-discharge stations (else over the stations
that reached congestion); when none did, the corridor's capacity was not
observed and only its lower bound (the largest station value) is reported.
The 15-minute pre-breakdown flow and the discharge flow during activation are
reported beside it as diagnostics. Model: a **simulated** straight-road
capacity when the repository holds one for this population under the same
car-following model (a ``*.calibration.json`` sidecar of
``scripts/calibrate_capacity.py``), adjusted by the analytical ratio for this
corridor's speed limit and truck share, which the sidecar's straight road did
not have; otherwise the **analytical** IDM equilibrium capacity of the drawn
population (a common-speed single lane, the convention of
``scripts/i24_calibrate_capacity.py --equilibrium``). The analytical value is
an index: SUMO's simulated four-lane capacity was 0.86–0.91 of the
mean-driver closed form for the I-24 population
(``artifacts/idm_i24_capacity_equilibrium.json``), and SUMO's EIDM about 11 %
below plain IDM on the same population (docs/ONBOARDING_MNDOT.md §10) — so
for an EIDM fleet without a simulated measurement there is no model value.

**Truck share.** Observed only from classification counts given to this
module (never inferred from loop data, never guessed); otherwise "not
available". Model: ``FleetSpec.heavy.fraction``.

**Verdicts.** ``ok`` when the model is within the quantity's tolerance of
the observed value; ``mismatch`` when it lies outside the tolerance band
around the whole observed interval (a difference the data resolve);
``inconclusive`` in between, or when only a lower bound is observed;
``not_available`` without an observed or a model value. Intervals are
seeded percentile bootstraps over **days** (detector readings of one day are
not independent).

**Recommendations**, one quantity at a time and in this order — truck share
(a fleet composition fact), then free-flow speed, then capacity — each
evaluated analytically with the previous ones applied: the knob, its
measured range (the population's own spread, :data:`MEASURED_RANGE_SIGMAS`
standard deviations either side of the measured mean, intersected with the
CLAUDE.md §3.1 calibration range), the value it would need and whether that
fits. When nothing fits the report says so in so many words: the population
cannot match this corridor inside its measured ranges.

**Ranges for the uncertainty runs** (WP-106b, docs/FRISCO_PROTOCOL.md §8.5
as clarified 2026-10-04). For each knob with a model curve — the mean time
headway (``t_scale``) for capacity per lane, the mean desired speed
(``v0_scale``) for free-flow speed — :func:`uncertainty_range` gives the knob
values whose model value stays inside the quantity's *observed* 95 % interval:
the crossings of the interval's ends read off the same curve, and under the
same earlier adjustments, as the recommendation (:func:`range_on_curve`),
**widened to include the configured (calibrated) value** — the model the
study runs must lie inside its own uncertainty range — and then clipped to the
knob's measured range (``widened_to_configured`` records the widening). That
is what is not known about *this* corridor's population; the measured range
itself (the spread of individual drivers) is the range calibration may choose
from. An ``inconclusive`` verdict still carries an interval and it is read.
The range falls back to the measured range, with the reason, where there is
no interval to read (not observed, no model value, only a lower bound, fewer
than :data:`MIN_DAYS_FOR_INTERVAL` days, or a curve that never enters the
interval) — basis ``measured_range_fallback`` — and where capacity is read off
the **analytical** index rather than a simulated capacity (no accepted
sidecar): the index sits several per cent above SUMO's capacity for the same
population, so reading the observed interval off it would place the range in
the wrong part of the knob — basis ``analytical_index_fallback``. Every basis
other than ``observed_interval`` is labelled assumed by
``validation.uncertainty``. Recorded on the comparison as
``uncertainty_range``.

**Not done here.** No simulation is run; no per-location setting is ever
proposed; nothing is fitted to the corridor (a needed value is read off a
curve, and a person decides whether to apply it); no truck share is inferred
from vehicle lengths or occupancies.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import pandas as pd
import yaml

from calibration.conservation import (
    DetectorGrid,
    clock_text,
    detector_grid,
    normalize_date,
    station_grid,
)
from calibration.data_quality import DataQualityReport, assess_quality, mask_grid
from calibration.loaders.detector_csv import local_dates, local_seconds
from calibration.loaders.pems import MPH_TO_MS
from calibration.observations import parse_clock
from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import FleetSpec
from flowstate_core.constants import IDM_RANGES, SPEED_FACTOR_BOUNDS, SPEED_FACTOR_DEFAULT
from flowstate_core.rng import make_rng
from flowstate_core.units import ms_to_kmh, veh_s_to_veh_h

TRANSFER_SCHEMA: Final[str] = "flowstate.transfer_check/1"
"""Schema tag of the JSON report."""

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
"""Repository root: artifact paths in scenario files are repo-relative."""

Verdict = Literal["ok", "mismatch", "inconclusive", "not_available"]

# --- observed side --------------------------------------------------------------

FREE_FLOW_MAX_OCCUPANCY_PCT: Final[float] = 10.0
"""Highest occupancy of a free-flow window [percent].

Reason: the free-branch cut of :func:`calibration.fd_fit.fit_triangular_fd`
(``uncongested_max_occupancy = 0.10``) — the same corridor's FD fit and this
check then call the same windows free-flowing. It also keeps out the
low-flow windows *inside* a queue, which a flow cap alone would admit."""

FREE_FLOW_MAX_FLOW_VEH_H_LANE: Final[float] = 1000.0
"""Highest per-lane flow of a free-flow window [veh/h/lane].

Reason: the Highway Capacity Manual (6th ed., ch. 12) defines a basic freeway
segment's free-flow speed as the mean speed at flows up to 1,000 pc/h/ln,
where its speed–flow curves are flat. Applied to vehicles, not passenger-car
equivalents, so with trucks present the window is somewhat more loaded than
the HCM condition."""

FREE_FLOW_MIN_COUNT_VEH_LANE: Final[float] = 10.0
"""Fewest vehicles per lane in a free-flow window. Reason: the same floor as
``calibration.data_quality.IMPLIED_LENGTH_MIN_COUNT_VEH`` — below ten
vehicles a window's mean is decided by which vehicles happened to pass (one
slow truck among three cars); it also drops the empty night windows."""

FREE_FLOW_FALLBACK_LENGTH_M: Final[float] = 7.0
"""Effective vehicle length [m] that turns the occupancy cut into a density
cut ``q/v ≤ occupancy/g`` for windows without an occupancy. Reason: the
default ``g_effective_length_m`` of
``calibration.loaders.detector_csv.to_fd_frame``. Every window judged this
way is counted on the report."""

FREE_FLOW_PERCENTILES: Final[tuple[float, float, float]] = (15.0, 50.0, 85.0)
"""Percentiles of the window-mean speeds reported (the median is judged)."""

ANALYSIS_WINDOW_S: Final[float] = 300.0
"""Window of the observed statistics [s]. Reason: CLAUDE.md §6.1 fits the
fundamental diagram on 5-minute station data and takes its capacity from the
5-minute flows; finer data are averaged up to it (a window only when every
finer window in it is present). Coarser data are used as they are and the
report says so: a longer average lowers a high percentile."""

CAPACITY_PERCENTILE: Final[float] = 95.0
"""Percentile of a station's 5-minute per-lane flows that is its capacity.
Reason: CLAUDE.md §6.1 and the ``q_max_percentile`` default of
:func:`calibration.fd_fit.fit_triangular_fd` — a robust stand-in for the
noisy maximum."""

BREAKDOWN_SPEED_MS: Final[float] = 40.0 * MPH_TO_MS
"""Speed below which a station is congested [m/s] (40 mph, 64.4 km/h).
Reason: the upstream condition of an active bottleneck in
docs/FRISCO_PROTOCOL.md §5 (Chen, Skabardonis & Varaiya 2004)."""

BOTTLENECK_SPEED_DIFFERENCE_MS: Final[float] = 20.0 * MPH_TO_MS
"""Least speed gain from the upstream to the downstream station of an active
bottleneck [m/s] (20 mph). Reason: docs/FRISCO_PROTOCOL.md §5."""

BOTTLENECK_PERSISTENCE: Final[tuple[int, int]] = (5, 7)
"""An active bottleneck holds in at least 5 of 7 consecutive windows.
Reason: docs/FRISCO_PROTOCOL.md §5 (a FlowState rule there until the
5-of-7 rule is checked against the original paper)."""

CONGESTED_MIN_RUN_S: Final[float] = 900.0
"""A station *reached congestion* on a day when it stayed below
:data:`BREAKDOWN_SPEED_MS` this long without a break [s]. Reason: the
Highway Capacity Manual's 15-minute analysis period; a single slow window is
a platoon or a glitch, not a breakdown."""

PRE_BREAKDOWN_S: Final[float] = 900.0
"""Span before a breakdown whose mean flow is the pre-breakdown flow [s].
Reason: 15 minutes, the HCM analysis period (the flow a road sustained just
before it broke down; a diagnostic, not the judged capacity)."""

# --- comparison ------------------------------------------------------------------

FREE_FLOW_SPEED_TOLERANCE: Final[float] = 0.05
"""Relative tolerance on free-flow speed. Reason: ±5 % is ±5–6 km/h at
freeway speeds — about the step between two posted limits (5 mph = 8 km/h)
and inside the 15 % speed RMSPE target the study is scored on (protocol C3);
a smaller difference is not one a driver population can be said to have."""

CAPACITY_TOLERANCE: Final[float] = 0.05
"""Relative tolerance on capacity per lane. Reason: ±5 % is the count
accuracy assumed for every detector (``calibration.conservation.
DEFAULT_COUNT_ERROR``); a capacity difference within it is not measured."""

HEAVY_SHARE_TOLERANCE: Final[float] = 0.03
"""Absolute tolerance on the truck share (3 percentage points). Reason: with
the HCM passenger-car equivalent of 2.0 for a truck on level terrain (6th
ed., Exhibit 12-25), a share error Δ moves the equivalent flow by
Δ·(2.0 − 1) — 3 points move it by 3 %, inside the capacity tolerance."""

INTERVAL_LEVEL: Final[float] = 0.95
"""Level of every observed interval (CLAUDE.md §0.6)."""

MIN_DAYS_FOR_INTERVAL: Final[int] = 3
"""Fewest days a day-bootstrap interval needs. Reason: with one or two days
the resamples take at most three distinct values; the report gives no
interval and the verdict rests on the point estimate (and says so)."""

DEFAULT_N_BOOTSTRAP: Final[int] = 1000
"""Bootstrap resamples. Reason: ~1,000 resamples are the usual minimum for
percentile intervals (Efron & Tibshirani 1993, ch. 19)."""

DEFAULT_BOOTSTRAP_SEED: Final[int] = 0
"""Seed of the day bootstrap (any fixed value; the same as
:func:`calibration.fd_fit.fit_triangular_fd`'s default seed)."""

# --- model side -------------------------------------------------------------------

MODEL_DRAWS: Final[int] = 20000
"""Drivers drawn for the analytical model values. Reason: the draw of
``scripts/i24_calibrate_capacity.py --equilibrium`` (``EQ_DRAW_N``), so the
committed ``artifacts/idm_i24_capacity_equilibrium.json`` is reproduced."""

MODEL_DRAW_SEED: Final[int] = 11
"""Seed of the passenger draw (``EQ_DRAW_SEED`` of the same script, for the
same reason); heavy vehicles draw from ``seed + 1``."""

PASSENGER_LENGTH_M: Final[float] = 5.0
"""Passenger vType length [m]: ``microsim.vehicles.VEHICLE_LENGTH_M``
(a test checks they agree)."""

IDM_HARD_LOWER: Final[dict[str, float]] = {
    "v0": 5.0,
    "T": 0.4,
    "a_max": 0.2,
    "b": 0.5,
    "s0": 0.5,
}
"""Physical floors of a driver draw: ``microsim.vehicles.IDM_HARD_LOWER``
(a test checks they agree)."""

IDM_PARAM_ORDER: Final[tuple[str, ...]] = ("v0", "T", "a_max", "b", "s0")
"""Parameter order of a driver draw (``microsim.vehicles``)."""

SUMO_IDM_DELTA: Final[float] = 4.0
"""SUMO 1.27.1 fixes the IDM exponent δ at 4 (``microsim.vehicles``)."""

ENGINE_SPEED_FACTOR: Final[float] = SPEED_FACTOR_DEFAULT
"""The ``speedFactor`` FlowState writes on a vType when the fleet sets none
(``FleetSpec.speed_factor``'s default) and on every heavy vehicle's
(``microsim.vehicles``): desired speed capped at the lane's speed limit."""

ENGINE_HAS_SPEED_FACTOR: Final[bool] = "speed_factor" in FleetSpec.model_fields
"""The engine exposes the passenger speed factor as a scenario setting
(``FleetSpec.speed_factor``, WP-109), so the ``speed_factor`` knob is
available: a recommendation to set it needs no code change."""

GENERATED_EDGE_SPEED_MS: Final[float] = 50.0
"""Speed limit of a generated straight corridor's edges [m/s]
(``microsim.networks.EDGE_SPEED_LIMIT_MS``; a test checks they agree): above
every desired-speed draw, so such a road caps no driver. A scenario on one
(``network.kind`` ``corridor`` or ``ring``) is compared uncapped whatever the
real road posts; a map-imported one (``osm``) carries the map's limit, taken
to be the posted one (the layout checklist's item e checks that)."""

CAPACITY_SPEED_GRID: Final[int] = 2001
"""Speeds tried before the golden-section refinement of a population's
equilibrium capacity."""

GRID_CHUNK_ELEMENTS: Final[int] = 1_000_000
"""Speeds × drivers evaluated at once in the capacity grid search (bounds
memory to a few tens of MB on a laptop)."""

KNOB_GRID: Final[int] = 25
"""Points of a knob's analytical curve across its measured range."""

# --- recommendations ----------------------------------------------------------------

MEASURED_RANGE_SIGMAS: Final[float] = 1.0
"""Half-width of a knob's measured range, in standard deviations of the
measured drivers' own values. Reason: the corridor-wide mean of a parameter
may move inside the central ~68 % of what the trajectories measured across
drivers, not beyond it — further, the adjusted population's typical driver
is one the data show as atypical. Intersected with the CLAUDE.md §3.1
calibration range (``flowstate_core.constants.IDM_RANGES``)."""

HEAVY_SHARE_RANGE: Final[tuple[float, float]] = (0.0, 0.5)
"""Settable truck share (``HeavyVehicleSpec.fraction`` bounds); the share
itself is a measurement of the corridor, not a driver parameter."""

MEASURED_HEAVY_POPULATION: Final[str] = "artifacts/idm_i24_heavy.json"
"""The repository's measured truck population (I-24 MOTION semis and trucks,
197 episodes), named when a fleet without trucks needs them."""


@dataclass(frozen=True)
class TransferRules:
    """Every rule of the check (defaults: the module constants).

    Lock an instance in a study protocol to fix the rules before results are
    seen; :meth:`to_dict` is what the report records.
    """

    free_flow_max_occupancy_pct: float = FREE_FLOW_MAX_OCCUPANCY_PCT
    free_flow_max_flow_veh_h_lane: float = FREE_FLOW_MAX_FLOW_VEH_H_LANE
    free_flow_min_count_veh_lane: float = FREE_FLOW_MIN_COUNT_VEH_LANE
    free_flow_fallback_length_m: float = FREE_FLOW_FALLBACK_LENGTH_M
    analysis_window_s: float = ANALYSIS_WINDOW_S
    capacity_percentile: float = CAPACITY_PERCENTILE
    breakdown_speed_ms: float = BREAKDOWN_SPEED_MS
    bottleneck_speed_difference_ms: float = BOTTLENECK_SPEED_DIFFERENCE_MS
    bottleneck_persistence: tuple[int, int] = BOTTLENECK_PERSISTENCE
    congested_min_run_s: float = CONGESTED_MIN_RUN_S
    pre_breakdown_s: float = PRE_BREAKDOWN_S
    free_flow_speed_tolerance: float = FREE_FLOW_SPEED_TOLERANCE
    capacity_tolerance: float = CAPACITY_TOLERANCE
    heavy_share_tolerance: float = HEAVY_SHARE_TOLERANCE
    interval_level: float = INTERVAL_LEVEL
    min_days_for_interval: int = MIN_DAYS_FOR_INTERVAL
    measured_range_sigmas: float = MEASURED_RANGE_SIGMAS

    def to_dict(self) -> dict[str, Any]:
        """JSON form (tuples as lists), in field order."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


RULE_SOURCES: Final[dict[str, str]] = {
    "free_flow_max_occupancy_pct": "free-branch cut of calibration.fd_fit (0.10)",
    "free_flow_max_flow_veh_h_lane": "HCM 6th ed. ch. 12: FFS measured at flows up to 1,000 pc/h/ln",
    "free_flow_min_count_veh_lane": "as calibration.data_quality.IMPLIED_LENGTH_MIN_COUNT_VEH",
    "free_flow_fallback_length_m": "default g of calibration.loaders.detector_csv.to_fd_frame",
    "analysis_window_s": "CLAUDE.md s.6.1: 5-minute station data",
    "capacity_percentile": "CLAUDE.md s.6.1; calibration.fd_fit q_max_percentile",
    "breakdown_speed_ms": "FRISCO_PROTOCOL s.5 (40 mph; Chen, Skabardonis & Varaiya 2004)",
    "bottleneck_speed_difference_ms": "FRISCO_PROTOCOL s.5 (20 mph)",
    "bottleneck_persistence": "FRISCO_PROTOCOL s.5 (5 of 7 windows; a FlowState rule there)",
    "congested_min_run_s": "HCM 15-minute analysis period",
    "pre_breakdown_s": "HCM 15-minute analysis period (diagnostic only)",
    "free_flow_speed_tolerance": "+-5 %: about one posted-limit step; inside protocol C3's 15 %",
    "capacity_tolerance": "+-5 %: the assumed detector count accuracy",
    "heavy_share_tolerance": "3 points: <= 3 % of PCE flow at the HCM truck PCE of 2.0",
    "interval_level": "CLAUDE.md s.0.6",
    "min_days_for_interval": "fewer days give a degenerate day bootstrap",
    "measured_range_sigmas": "inside the central ~68 % of the measured drivers' values",
}
"""Source or reason of every :class:`TransferRules` field."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _num(value: float | None, digits: int = 4) -> float | None:
    """A float for JSON: ``None`` for NaN/inf/None, rounded."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_path(path: str | Path) -> Path:
    """A path as given when it exists, else relative to the repository root.

    Scenario files reference artifacts repo-relatively
    (``artifacts/idm_i24.json``), as ``microsim.vehicles`` resolves them.

    Raises:
        FileNotFoundError: Neither exists.
    """
    p = Path(path)
    if p.is_file():
        return p
    candidate = REPO_ROOT / p
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"{path!s} not found (also tried {candidate})")


def _rel(path: Path) -> str:
    """Repo-relative text when inside the repository, else as given."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _kmh(v_ms: float | None) -> str:
    return "—" if v_ms is None else f"{ms_to_kmh(v_ms):.0f} km/h ({v_ms / MPH_TO_MS:.0f} mph)"


def _share_text(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


@dataclass(frozen=True)
class Interval:
    """A seeded percentile-bootstrap interval.

    Attributes:
        lo: Lower end.
        hi: Upper end.
        level: Coverage level.
        n_resamples: Resamples used.
        unit: What was resampled (``"day"``, ``"station"``, ``"row"``).
        n_units: Units available to resample.
        seed: Seed.
    """

    lo: float
    hi: float
    level: float
    n_resamples: int
    unit: str
    n_units: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "lo": _num(self.lo),
            "hi": _num(self.hi),
            "level": self.level,
            "n_resamples": self.n_resamples,
            "unit": self.unit,
            "n_units": self.n_units,
            "seed": self.seed,
        }


def _bootstrap(
    n_units: int,
    statistic: Callable[[np.ndarray], float],
    *,
    unit: str,
    n_resamples: int,
    seed: int,
    level: float,
    min_units: int,
) -> Interval | None:
    """Percentile interval of ``statistic(drawn unit indices)``.

    ``statistic`` receives the resampled indices (with replacement, as many
    as there are units) and returns the statistic, NaN when undefined.
    Returns None with fewer than ``min_units`` units or when fewer than half
    the resamples give a finite value.
    """
    if n_units < min_units or n_resamples <= 0:
        return None
    rng = make_rng(seed)
    values = np.empty(n_resamples)
    for b in range(n_resamples):
        values[b] = statistic(rng.integers(0, n_units, size=n_units))
    finite = values[np.isfinite(values)]
    if finite.size < max(1, n_resamples // 2):
        return None
    alpha = (1.0 - level) / 2.0
    lo, hi = np.percentile(finite, [100.0 * alpha, 100.0 * (1.0 - alpha)])
    return Interval(
        lo=float(lo),
        hi=float(hi),
        level=level,
        n_resamples=int(finite.size),
        unit=unit,
        n_units=n_units,
        seed=seed,
    )


def _pooled(per_day: Sequence[np.ndarray], days: np.ndarray) -> np.ndarray:
    """The values of the drawn days, concatenated (a day drawn twice counts twice)."""
    parts = [per_day[int(d)] for d in days]
    return np.concatenate(parts) if parts else np.zeros(0)


def _percentile(values: np.ndarray, q: float) -> float:
    return float(np.percentile(values, q)) if values.size else float("nan")


def _pooled_percentile(per_day: Sequence[np.ndarray], q: float) -> Callable[[np.ndarray], float]:
    """The bootstrap statistic: percentile ``q`` of the drawn days' pooled values."""

    def stat(draw: np.ndarray) -> float:
        return _percentile(_pooled(per_day, draw), q)

    return stat


# ---------------------------------------------------------------------------
# Observed side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StationFreeFlow:
    """Free-flow speed at one mainline station.

    Attributes:
        station: Station id.
        x_m: Corridor position [m], when known.
        lanes: Lanes the station's flow covers.
        speed_limit_ms: Posted limit at the station [m/s], when known.
        n_windows: Free-flow windows.
        n_days: Days with at least one.
        median_ms: Median window-mean speed [m/s].
        p15_ms: 15th percentile [m/s].
        p85_ms: 85th percentile [m/s].
        interval: Interval of the median (bootstrap over days).
        flow_median_veh_h_lane: Median per-lane flow of those windows.
    """

    station: str
    x_m: float | None
    lanes: int
    speed_limit_ms: float | None
    n_windows: int
    n_days: int
    median_ms: float | None
    p15_ms: float | None
    p85_ms: float | None
    interval: Interval | None
    flow_median_veh_h_lane: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "station": self.station,
            "x_m": _num(self.x_m, 1),
            "lanes": self.lanes,
            "speed_limit_ms": _num(self.speed_limit_ms),
            "n_windows": self.n_windows,
            "n_days": self.n_days,
            "median_ms": _num(self.median_ms),
            "p15_ms": _num(self.p15_ms),
            "p85_ms": _num(self.p85_ms),
            "interval": None if self.interval is None else self.interval.to_dict(),
            "flow_median_veh_h_lane": _num(self.flow_median_veh_h_lane, 1),
        }


@dataclass(frozen=True)
class FreeFlowObserved:
    """Corridor-wide free-flow speed (window means pooled over stations).

    Attributes:
        n_windows: Free-flow windows.
        n_days: Days contributing.
        n_stations: Stations contributing.
        median_ms: Median [m/s] (judged).
        p15_ms: 15th percentile [m/s].
        p85_ms: 85th percentile [m/s].
        interval: Interval of the median (bootstrap over days).
        flow_median_veh_h_lane: Median per-lane flow of the windows — the
            traffic level the model's speed is evaluated at.
        n_density_fallback: Windows judged free by the density fallback
            (no occupancy).
        relative_to_limit: Median over the posted limit, when known.
    """

    n_windows: int
    n_days: int
    n_stations: int
    median_ms: float | None
    p15_ms: float | None
    p85_ms: float | None
    interval: Interval | None
    flow_median_veh_h_lane: float | None
    n_density_fallback: int
    relative_to_limit: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "n_windows": self.n_windows,
            "n_days": self.n_days,
            "n_stations": self.n_stations,
            "median_ms": _num(self.median_ms),
            "p15_ms": _num(self.p15_ms),
            "p85_ms": _num(self.p85_ms),
            "interval": None if self.interval is None else self.interval.to_dict(),
            "flow_median_veh_h_lane": _num(self.flow_median_veh_h_lane, 1),
            "n_density_fallback": self.n_density_fallback,
            "relative_to_limit": _num(self.relative_to_limit),
        }


CapacityCategory = Literal["bottleneck_discharge", "congested", "never_congested", "no_speed"]


@dataclass(frozen=True)
class StationCapacity:
    """Capacity evidence at one mainline station.

    Attributes:
        station: Station id.
        x_m: Corridor position [m], when known.
        lanes: Lanes.
        category: ``bottleneck_discharge`` (downstream station of an active
            bottleneck on some day: road-limited flow), ``congested``
            (reached congestion, not as a bottleneck's discharge),
            ``never_congested`` (flow is a lower bound on capacity) or
            ``no_speed`` (no speed to tell).
        n_windows: Windows with a per-lane flow.
        n_days: Days with one.
        capacity_veh_h_lane: The :data:`CAPACITY_PERCENTILE` percentile of
            the 5-minute per-lane flow.
        interval: Its interval (bootstrap over days).
        speed_at_capacity_ms: Median speed of the windows at or above it.
        n_congested_days: Days on which the station reached congestion.
        n_active_windows: Windows in which the station discharged an active
            bottleneck upstream of it.
        discharge_flow_veh_h_lane: Median per-lane flow over those windows.
        n_breakdowns: Breakdowns with a clean 15 minutes before them.
        pre_breakdown_flow_veh_h_lane: Median per-lane flow over the 15
            minutes before them.
    """

    station: str
    x_m: float | None
    lanes: int
    category: CapacityCategory
    n_windows: int
    n_days: int
    capacity_veh_h_lane: float | None
    interval: Interval | None
    speed_at_capacity_ms: float | None
    n_congested_days: int
    n_active_windows: int
    discharge_flow_veh_h_lane: float | None
    n_breakdowns: int
    pre_breakdown_flow_veh_h_lane: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "station": self.station,
            "x_m": _num(self.x_m, 1),
            "lanes": self.lanes,
            "category": self.category,
            "n_windows": self.n_windows,
            "n_days": self.n_days,
            "capacity_veh_h_lane": _num(self.capacity_veh_h_lane, 1),
            "interval": None if self.interval is None else self.interval.to_dict(),
            "speed_at_capacity_ms": _num(self.speed_at_capacity_ms),
            "n_congested_days": self.n_congested_days,
            "n_active_windows": self.n_active_windows,
            "discharge_flow_veh_h_lane": _num(self.discharge_flow_veh_h_lane, 1),
            "n_breakdowns": self.n_breakdowns,
            "pre_breakdown_flow_veh_h_lane": _num(self.pre_breakdown_flow_veh_h_lane, 1),
        }


CapacityBasis = Literal["bottleneck_discharge", "congested", "lower_bound", "none"]


@dataclass(frozen=True)
class CapacityObserved:
    """Corridor capacity per lane.

    Attributes:
        basis: Which stations it rests on (module docstring); ``lower_bound``
            when no station reached congestion — the value is then the
            largest station value, a lower bound on capacity.
        value_veh_h_lane: Median over the basis stations of their capacity
            (``lower_bound``: the largest).
        interval: Interval (bootstrap over days, the station classification
            held fixed).
        stations: The basis stations.
        speed_at_capacity_ms: Median speed of their windows at or above
            their capacity.
        headway_s: ``3600 / value`` — the mean time headway per lane at it.
        discharge_flow_veh_h_lane: Median per-lane flow of the
            bottleneck-discharge stations during activation (diagnostic).
        pre_breakdown_flow_veh_h_lane: Median 15-minute pre-breakdown flow
            over every station's breakdowns (diagnostic).
    """

    basis: CapacityBasis
    value_veh_h_lane: float | None
    interval: Interval | None
    stations: tuple[str, ...]
    speed_at_capacity_ms: float | None
    headway_s: float | None
    discharge_flow_veh_h_lane: float | None
    pre_breakdown_flow_veh_h_lane: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "basis": self.basis,
            "value_veh_h_lane": _num(self.value_veh_h_lane, 1),
            "interval": None if self.interval is None else self.interval.to_dict(),
            "stations": list(self.stations),
            "speed_at_capacity_ms": _num(self.speed_at_capacity_ms),
            "headway_s": _num(self.headway_s),
            "discharge_flow_veh_h_lane": _num(self.discharge_flow_veh_h_lane, 1),
            "pre_breakdown_flow_veh_h_lane": _num(self.pre_breakdown_flow_veh_h_lane, 1),
        }


@dataclass(frozen=True)
class HeavyObserved:
    """Truck share from classification counts.

    Attributes:
        available: False when no classification data was given.
        share: Heavy vehicles over all vehicles (count-weighted).
        interval: Interval (bootstrap over days, else stations, else rows).
        n_rows: Classification rows used.
        n_days: Distinct dates among them (0 when undated).
        definition: What the source calls heavy, as stated by the caller.
        reason: Why it is not available, when not.
    """

    available: bool
    share: float | None = None
    interval: Interval | None = None
    n_rows: int = 0
    n_days: int = 0
    definition: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "available": self.available,
            "share": _num(self.share),
            "interval": None if self.interval is None else self.interval.to_dict(),
            "n_rows": self.n_rows,
            "n_days": self.n_days,
            "definition": self.definition,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ObservedSide:
    """Everything :func:`observe` measured from a corridor's detectors.

    Attributes:
        source_interval_s: Window of the input data [s].
        interval_s: Window of the statistics [s] (:data:`ANALYSIS_WINDOW_S`
            when the data are finer).
        start_local: Local clock start of the daily span.
        end_local: Local clock end.
        dates: Local dates.
        per_lane: The input was per lane.
        quality: ``{"applied": bool, ...summary}`` of the data-quality
            masking.
        stations: Mainline stations used.
        median_lanes: Median lane count of those stations.
        speed_limit_ms: The corridor's posted limit [m/s] used, when known.
        speed_limit_source: Where it came from.
        free_flow: Corridor free-flow speed.
        free_flow_stations: Per station.
        capacity: Corridor capacity per lane.
        capacity_stations: Per station.
        bottlenecks: Active-bottleneck pairs found (upstream, downstream,
            active windows, days).
        heavy: Truck share.
        rules: The rules used.
        notes: What could not be done, plainly.
    """

    source_interval_s: float
    interval_s: float
    start_local: str
    end_local: str
    dates: tuple[str, ...]
    per_lane: bool
    quality: dict[str, Any]
    stations: tuple[str, ...]
    median_lanes: float | None
    speed_limit_ms: float | None
    speed_limit_source: str
    free_flow: FreeFlowObserved
    free_flow_stations: tuple[StationFreeFlow, ...]
    capacity: CapacityObserved
    capacity_stations: tuple[StationCapacity, ...]
    bottlenecks: tuple[dict[str, Any], ...]
    heavy: HeavyObserved
    rules: TransferRules
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "grid": {
                "source_interval_s": self.source_interval_s,
                "interval_s": self.interval_s,
                "start_local": self.start_local,
                "end_local": self.end_local,
                "dates": list(self.dates),
                "per_lane": self.per_lane,
            },
            "quality": dict(self.quality),
            "stations": list(self.stations),
            "median_lanes": self.median_lanes,
            "speed_limit_ms": _num(self.speed_limit_ms),
            "speed_limit_source": self.speed_limit_source,
            "free_flow": self.free_flow.to_dict(),
            "free_flow_stations": [s.to_dict() for s in self.free_flow_stations],
            "capacity": self.capacity.to_dict(),
            "capacity_stations": [s.to_dict() for s in self.capacity_stations],
            "bottlenecks": [dict(b) for b in self.bottlenecks],
            "heavy": self.heavy.to_dict(),
            "notes": list(self.notes),
        }


@dataclass
class _Series:
    """One mainline station on the analysis grid (``days × windows``)."""

    station: str
    x_m: float | None
    lanes: int
    limit_ms: float | None
    q_lane: np.ndarray
    occ: np.ndarray
    speed: np.ndarray


def aggregate_windows(grid: DetectorGrid, window_s: float) -> tuple[DetectorGrid, str | None]:
    """A station grid on ``window_s`` windows when its own are finer.

    Flow and occupancy are the means of the finer windows, speed their
    flow-weighted mean, each only where every finer window's flow is present
    (a partial mean would describe part of the window). Trailing windows that
    do not fill a whole ``window_s`` are dropped.

    Returns:
        ``(grid, note)`` — the grid unchanged (with a note) when its windows
        are not finer or do not divide ``window_s``.
    """
    interval = grid.interval_s
    if interval >= window_s - 1e-9:
        note = None
        if interval > window_s + 1e-9:
            note = (
                f"the data's windows are {interval:g} s, longer than the {window_s:g}-s analysis "
                "window; statistics use them as they are, and a high percentile of a longer "
                "average is lower"
            )
        return grid, note
    ratio = window_s / interval
    k = round(ratio)
    if abs(ratio - k) > 1e-6:
        return grid, (
            f"the data's {interval:g}-s windows do not divide {window_s:g} s; statistics use "
            "the data's own windows"
        )
    m = grid.n_windows // k
    if m < 1:
        return grid, f"the span holds less than one {window_s:g}-s window; not aggregated"
    flows: dict[str, np.ndarray] = {}
    occs: dict[str, np.ndarray] = {}
    speeds: dict[str, np.ndarray] = {}
    d = len(grid.dates)
    for sid in grid.sensors:
        q = grid.flow_veh_h[sid][:, : m * k].reshape(d, m, k)
        o = grid.occupancy_pct[sid][:, : m * k].reshape(d, m, k)
        v = grid.speed_ms[sid][:, : m * k].reshape(d, m, k)
        complete = np.isfinite(q).all(axis=2)
        flows[sid] = np.where(complete, q.mean(axis=2), np.nan)
        occ_ok = complete & np.isfinite(o).all(axis=2)
        occs[sid] = np.where(occ_ok, np.nan_to_num(o).mean(axis=2), np.nan)
        weight = np.where(np.isfinite(v) & np.isfinite(q) & (q > 0.0), q, 0.0)
        total = weight.sum(axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_v = (np.nan_to_num(v) * weight).sum(axis=2) / total
        speeds[sid] = np.where(complete & (total > 0.0), mean_v, np.nan)
    out = DetectorGrid(
        interval_s=float(window_s),
        start_s=grid.start_s,
        n_windows=int(m),
        dates=grid.dates,
        sensors=dict(grid.sensors),
        flow_veh_h=flows,
        occupancy_pct=occs,
        speed_ms=speeds,
        per_lane=grid.per_lane,
        silent=grid.silent,
    )
    note = None
    if m * k != grid.n_windows:
        note = (
            f"{grid.n_windows - m * k} trailing {interval:g}-s window(s) per day do not fill a "
            f"{window_s:g}-s window and are not used"
        )
    return out, note


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True: ``[(start, length), ...]``."""
    out: list[tuple[int, int]] = []
    start = -1
    for i, flag in enumerate(mask.tolist()):
        if flag and start < 0:
            start = i
        elif not flag and start >= 0:
            out.append((start, i - start))
            start = -1
    if start >= 0:
        out.append((start, len(mask) - start))
    return out


def active_windows(condition: np.ndarray, persistence: tuple[int, int]) -> np.ndarray:
    """Windows of an active bottleneck: the condition holds in the window and
    the window lies in a stretch of ``persistence[1]`` consecutive windows of
    which at least ``persistence[0]`` hold it (docs/FRISCO_PROTOCOL.md §5).

    Args:
        condition: Boolean ``days × windows``.
        persistence: ``(k, n)``.

    Returns:
        Boolean array of the same shape.
    """
    k, n = persistence
    out = np.zeros_like(condition, dtype=bool)
    if condition.shape[-1] < n:
        return out
    c = condition.astype(np.int64)
    csum = np.concatenate([np.zeros((c.shape[0], 1), dtype=np.int64), c.cumsum(axis=1)], axis=1)
    counts = csum[:, n:] - csum[:, :-n]
    for d, j in zip(*np.nonzero(counts >= k), strict=True):
        out[d, j : j + n] |= condition[d, j : j + n]
    return out


def _clock_seconds(text: str) -> float:
    """``"HH:MM"`` → seconds after midnight; ``"24:00"`` is the end of the day."""
    if str(text).strip() in ("24:00", "24:00:00"):
        return 86400.0
    return parse_clock(text)


def _station_limits(stations: pd.DataFrame | None) -> dict[str, float]:
    """Posted limit per station from a stations table's ``speed_limit_ms``."""
    if stations is None or "speed_limit_ms" not in stations.columns:
        return {}
    values = pd.to_numeric(stations["speed_limit_ms"], errors="coerce").to_numpy(dtype=float)
    out: dict[str, float] = {}
    for station, value in zip(stations["station"].astype(str), values, strict=True):
        if np.isfinite(value) and value > 0.0:
            out[station] = float(value)
    return out


def _corridor_limit(limits: Mapping[str, float], stations: Sequence[str]) -> float | None:
    """The limit held by most of ``stations`` (ties: the lower), as the table
    gives it (stations are grouped on the limit rounded to 0.01 m/s)."""
    counts: dict[float, int] = {}
    actual: dict[float, float] = {}
    for s in stations:
        if s in limits:
            key = round(limits[s], 2)
            counts[key] = counts.get(key, 0) + 1
            actual.setdefault(key, float(limits[s]))
    if not counts:
        return None
    return actual[min(counts, key=lambda v: (-counts[v], v))]


def heavy_share_from_classification(
    frame: pd.DataFrame,
    *,
    column_map: Mapping[str, str] | None = None,
    dates: Sequence[str] | None = None,
    start_local: str | None = None,
    end_local: str | None = None,
    stations: Sequence[str] | None = None,
    definition: str = "",
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    level: float = INTERVAL_LEVEL,
    min_units: int = MIN_DAYS_FOR_INTERVAL,
) -> HeavyObserved:
    """Truck share from classification counts (never inferred, never guessed).

    The frame carries, per row, either ``heavy_count`` and ``total_count``
    (vehicles in the row's period) or a ``heavy_share`` fraction (with an
    optional ``total_count`` to weight it); optionally ``station`` and
    ``timestamp`` (ISO-8601, the period start, on the source's local clock)
    or ``date``. ``column_map`` maps those canonical names to the file's own
    (case-insensitive). Rows are restricted to ``dates``, the daily span
    ``[start_local, end_local)`` (``HH:MM``; ``24:00`` allowed) and
    ``stations`` when the frame can tell.

    Returns:
        :class:`HeavyObserved`; the share is ``Σ heavy / Σ total`` (or the
        total-weighted mean of the shares, else their plain mean).

    Raises:
        ValueError: Neither counts nor shares, a share outside [0, 1], or a
            heavy count above its total.
    """
    lookup = {str(c).strip().lower(): c for c in frame.columns}
    wanted = {
        k: str((column_map or {}).get(k, k))
        for k in ("station", "timestamp", "date", "heavy_count", "total_count", "heavy_share")
    }

    def col(name: str) -> Any | None:
        return lookup.get(wanted[name].strip().lower())

    df = frame.copy()
    keep = np.ones(len(df), dtype=bool)
    day: pd.Series | None = None
    if col("timestamp") is not None:
        ts = df[col("timestamp")].rename("timestamp")
        tmp = pd.DataFrame({"timestamp": ts})
        day = local_dates(tmp)
        secs = local_seconds(tmp).to_numpy(dtype=float)
        if start_local:
            keep &= secs >= _clock_seconds(start_local)
        if end_local:
            keep &= secs < _clock_seconds(end_local)
    elif col("date") is not None:
        day = df[col("date")].map(lambda d: normalize_date(str(d)))
    if day is not None and dates:
        wanted_days = {normalize_date(d) for d in dates}
        keep &= day.isin(sorted(wanted_days)).to_numpy()
    station_col = col("station")
    if station_col is not None and stations:
        keep &= df[station_col].astype(str).isin([str(s) for s in stations]).to_numpy()
    df = df.loc[keep].reset_index(drop=True)
    if day is not None:
        day = day.loc[keep].reset_index(drop=True)
    if df.empty:
        return HeavyObserved(
            available=False,
            definition=definition,
            reason="the classification data hold no row inside the dates, span and stations",
        )
    heavy_c, total_c, share_c = col("heavy_count"), col("total_count"), col("heavy_share")
    if heavy_c is not None and total_c is not None:
        heavy = pd.to_numeric(df[heavy_c], errors="coerce").to_numpy(dtype=float)
        total = pd.to_numeric(df[total_c], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(heavy) & np.isfinite(total) & (total > 0.0)
        if (heavy[ok] < 0.0).any() or (heavy[ok] > total[ok]).any():
            raise ValueError("classification: a heavy count is negative or above its total")
    elif share_c is not None:
        share = pd.to_numeric(df[share_c], errors="coerce").to_numpy(dtype=float)
        if ((share < 0.0) | (share > 1.0)).any():
            raise ValueError(
                "classification: heavy_share must be a fraction in [0, 1] (not percent)"
            )
        total = (
            pd.to_numeric(df[total_c], errors="coerce").to_numpy(dtype=float)
            if total_c is not None
            else np.ones(len(df))
        )
        ok = np.isfinite(share) & np.isfinite(total) & (total > 0.0)
        heavy = share * total
    else:
        raise ValueError("classification: give heavy_count and total_count columns, or heavy_share")
    heavy, total = heavy[ok], total[ok]
    if total.sum() <= 0.0:
        return HeavyObserved(
            available=False, definition=definition, reason="no classification row with traffic"
        )
    share_value = float(heavy.sum() / total.sum())
    groups: np.ndarray
    unit = "row"
    if day is not None:
        groups = day.to_numpy(dtype=object)[ok]
        unit = "day"
    elif station_col is not None:
        groups = df[station_col].astype(str).to_numpy(dtype=object)[ok]
        unit = "station"
    else:
        groups = np.arange(int(ok.sum())).astype(object)
    labels = sorted(set(groups.tolist()))
    index = {g: i for i, g in enumerate(labels)}
    gi = np.array([index[g] for g in groups.tolist()], dtype=np.int64)
    h_g = np.bincount(gi, weights=heavy, minlength=len(labels))
    t_g = np.bincount(gi, weights=total, minlength=len(labels))

    def stat(draw: np.ndarray) -> float:
        t = t_g[draw].sum()
        return float(h_g[draw].sum() / t) if t > 0 else float("nan")

    interval = _bootstrap(
        len(labels),
        stat,
        unit=unit,
        n_resamples=n_bootstrap,
        seed=seed,
        level=level,
        min_units=min_units,
    )
    return HeavyObserved(
        available=True,
        share=share_value,
        interval=interval,
        n_rows=int(ok.sum()),
        n_days=len(labels) if unit == "day" else 0,
        definition=definition,
    )


def observe(
    data: pd.DataFrame | DetectorGrid,
    *,
    stations: pd.DataFrame | None = None,
    quality: DataQualityReport | Literal["assess"] | None = "assess",
    exempt_lanes: Iterable[str] = (),
    dates: Sequence[str] | None = None,
    start_local: str | None = None,
    end_local: str | None = None,
    only_stations: Sequence[str] | None = None,
    speed_limit_ms: float | None = None,
    classification: HeavyObserved | None = None,
    rules: TransferRules | None = None,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> ObservedSide:
    """Measure free-flow speed, capacity per lane and truck share (module docstring).

    Args:
        data: Tidy detector frame (station or per-lane rows) or a grid.
        stations: Stations table; its ``speed_limit_ms`` column, when
            present, gives the posted limits.
        quality: ``"assess"`` runs :func:`calibration.data_quality.
            assess_quality` (without the mass balance, which masks nothing)
            and masks what it excludes or sets aside; a report is applied as
            given; ``None`` skips the masking (and the report says so).
        exempt_lanes: Lane sensors exempt from the lane-imbalance check.
        dates: Local dates to use (frame input).
        start_local: Daily span start ``HH:MM`` (frame input).
        end_local: Daily span end ``HH:MM`` (frame input).
        only_stations: Mainline stations to use (default: every one) — the
            study's selected stations (docs/FRISCO_PROTOCOL.md §2.2).
        speed_limit_ms: The corridor's posted limit [m/s]; overrides the
            stations table.
        classification: Truck share measured from classification counts
            (:func:`heavy_share_from_classification`), or None.
        rules: The rules (default :class:`TransferRules`).
        n_bootstrap: Bootstrap resamples.
        seed: Bootstrap seed.

    Returns:
        The :class:`ObservedSide`.

    Raises:
        ValueError: No mainline station with a lane count is left.
    """
    th = rules or TransferRules()
    notes: list[str] = []
    grid = (
        data
        if isinstance(data, DetectorGrid)
        else detector_grid(data, dates=dates, start_local=start_local, end_local=end_local)
    )
    quality_record: dict[str, Any] = {"applied": False}
    report: DataQualityReport | None
    if quality == "assess":
        report = assess_quality(
            grid, stations=stations, exempt_lanes=exempt_lanes, mass_balance=False
        )
    else:
        report = quality
    if report is not None:
        grid = mask_grid(grid, report)
        quality_record = {
            "applied": True,
            "summary": report.summary(),
            "excluded_sensor_days": sorted(
                f"{sd.sensor} {sd.date}" for sd in report.sensor_days if sd.verdict == "exclude"
            ),
        }
    else:
        notes.append("data-quality masking was not applied: every reading was taken as delivered")
    source_interval = grid.interval_s
    sg = station_grid(grid)
    sg, agg_note = aggregate_windows(sg, th.analysis_window_s)
    if agg_note:
        notes.append(agg_note)

    limits = _station_limits(stations)
    series: list[_Series] = []
    no_lanes: list[str] = []
    for sid, info in sorted(sg.sensors.items()):
        if info.kind != "mainline":
            continue
        if only_stations is not None and sid not in set(only_stations):
            continue
        if info.lanes <= 0:
            no_lanes.append(sid)
            continue
        series.append(
            _Series(
                station=sid,
                x_m=info.x_m,
                lanes=int(info.lanes),
                limit_ms=limits.get(sid),
                q_lane=sg.flow_veh_h[sid] / info.lanes,
                occ=sg.occupancy_pct[sid],
                speed=sg.speed_ms[sid],
            )
        )
    if no_lanes:
        notes.append(
            "no lane count for station(s) " + ", ".join(no_lanes) + ": per-lane values cannot "
            "be formed there and they are not used"
        )
    if only_stations is not None:
        missing = sorted(set(only_stations) - {s.station for s in series})
        if missing:
            notes.append(
                "selected station(s) not among the usable mainline stations: " + ", ".join(missing)
            )
    if not series:
        raise ValueError("transfer check: no mainline station with a lane count in the data")

    if speed_limit_ms is not None:
        corridor_limit: float | None = float(speed_limit_ms)
        limit_source = "given"
    else:
        corridor_limit = _corridor_limit(limits, [s.station for s in series])
        limit_source = (
            "stations table (the limit most stations post)" if corridor_limit else "not known"
        )
        distinct = sorted({round(limits[s.station], 2) for s in series if s.station in limits})
        if corridor_limit is not None and len(distinct) > 1:
            shown = ", ".join(f"{v / MPH_TO_MS:.0f} mph" for v in distinct)
            notes.append(
                f"posted limits differ along the stretch ({shown}); the model is capped at the "
                f"most common, {corridor_limit / MPH_TO_MS:.0f} mph"
            )
    n_days = len(sg.dates)
    dt = sg.interval_s
    level = th.interval_level

    # --- free-flow speed ---------------------------------------------------
    ff_station: list[StationFreeFlow] = []
    ff_by_day: list[list[np.ndarray]] = [[] for _ in range(n_days)]
    flow_by_day: list[list[np.ndarray]] = [[] for _ in range(n_days)]
    n_fallback_total = 0
    ff_stations_used = 0
    for s in series:
        q, v, o = s.q_lane, s.speed, s.occ
        with np.errstate(invalid="ignore", divide="ignore"):
            count = q * dt / 3600.0
            density = q / 3600.0 / v
            base = (
                np.isfinite(q)
                & np.isfinite(v)
                & (v > 0.0)
                & (q <= th.free_flow_max_flow_veh_h_lane)
                & (count >= th.free_flow_min_count_veh_lane)
            )
            occ_ok = np.isfinite(o)
            by_occ = occ_ok & (o <= th.free_flow_max_occupancy_pct)
            by_density = ~occ_ok & (
                density <= th.free_flow_max_occupancy_pct / 100.0 / th.free_flow_fallback_length_m
            )
        mask = base & (by_occ | by_density)
        n_fallback_total += int((base & by_density).sum())
        per_day = [v[d][mask[d]] for d in range(n_days)]
        per_day_q = [q[d][mask[d]] for d in range(n_days)]
        values = np.concatenate(per_day) if per_day else np.zeros(0)
        days_with = [d for d in range(n_days) if per_day[d].size]
        interval = None
        if values.size:
            ff_stations_used += 1
            for d in range(n_days):
                ff_by_day[d].append(per_day[d])
                flow_by_day[d].append(per_day_q[d])
            interval = _bootstrap(
                n_days,
                _pooled_percentile(per_day, 50.0),
                unit="day",
                n_resamples=n_bootstrap,
                seed=seed,
                level=level,
                min_units=th.min_days_for_interval,
            )
        flows = np.concatenate(per_day_q) if per_day_q else np.zeros(0)
        ff_station.append(
            StationFreeFlow(
                station=s.station,
                x_m=s.x_m,
                lanes=s.lanes,
                speed_limit_ms=s.limit_ms,
                n_windows=int(values.size),
                n_days=len(days_with),
                median_ms=float(np.median(values)) if values.size else None,
                p15_ms=_percentile(values, FREE_FLOW_PERCENTILES[0]) if values.size else None,
                p85_ms=_percentile(values, FREE_FLOW_PERCENTILES[2]) if values.size else None,
                interval=interval,
                flow_median_veh_h_lane=float(np.median(flows)) if flows.size else None,
            )
        )
    day_speeds = [np.concatenate(parts) if parts else np.zeros(0) for parts in ff_by_day]
    day_flows = [np.concatenate(parts) if parts else np.zeros(0) for parts in flow_by_day]
    all_speeds = np.concatenate(day_speeds) if day_speeds else np.zeros(0)
    all_flows = np.concatenate(day_flows) if day_flows else np.zeros(0)
    ff_interval = None
    if all_speeds.size:
        ff_interval = _bootstrap(
            n_days,
            _pooled_percentile(day_speeds, 50.0),
            unit="day",
            n_resamples=n_bootstrap,
            seed=seed,
            level=level,
            min_units=th.min_days_for_interval,
        )
    ff_median = float(np.median(all_speeds)) if all_speeds.size else None
    if n_fallback_total:
        notes.append(
            f"{n_fallback_total} free-flow window(s) had no occupancy and were judged by density "
            f"q/v at a {th.free_flow_fallback_length_m:g} m effective length"
        )
    if not all_speeds.size:
        notes.append(
            "no free-flow window (light traffic with a speed reading) in the data: the free-flow "
            "speed is not available"
        )
    free_flow = FreeFlowObserved(
        n_windows=int(all_speeds.size),
        n_days=sum(1 for d in day_speeds if d.size),
        n_stations=ff_stations_used,
        median_ms=ff_median,
        p15_ms=_percentile(all_speeds, FREE_FLOW_PERCENTILES[0]) if all_speeds.size else None,
        p85_ms=_percentile(all_speeds, FREE_FLOW_PERCENTILES[2]) if all_speeds.size else None,
        interval=ff_interval,
        flow_median_veh_h_lane=float(np.median(all_flows)) if all_flows.size else None,
        n_density_fallback=n_fallback_total,
        relative_to_limit=(ff_median / corridor_limit) if ff_median and corridor_limit else None,
    )

    # --- congestion, bottlenecks, capacity ---------------------------------
    run_min = max(1, math.ceil(th.congested_min_run_s / dt - 1e-9))
    pre_n = max(1, math.ceil(th.pre_breakdown_s / dt - 1e-9))
    congested_days: dict[str, int] = {}
    pre_breakdown: dict[str, list[float]] = {}
    has_speed: dict[str, bool] = {}
    for s in series:
        slow = np.isfinite(s.speed) & (s.speed < th.breakdown_speed_ms)
        fast = np.isfinite(s.speed) & (s.speed >= th.breakdown_speed_ms)
        has_speed[s.station] = bool(np.isfinite(s.speed).any())
        n_cong = 0
        events: list[float] = []
        for d in range(n_days):
            runs = [r for r in _runs(slow[d]) if r[1] >= run_min]
            if runs:
                n_cong += 1
            for start, _ in runs:
                if start < pre_n:
                    continue
                window = slice(start - pre_n, start)
                if fast[d, window].all() and np.isfinite(s.q_lane[d, window]).all():
                    events.append(float(s.q_lane[d, window].mean()))
        congested_days[s.station] = n_cong
        pre_breakdown[s.station] = events

    positioned = sorted((s for s in series if s.x_m is not None), key=lambda s: float(s.x_m or 0.0))
    if len(positioned) < len(series):
        notes.append(
            "station(s) without a position cannot be paired with a neighbour: "
            + ", ".join(s.station for s in series if s.x_m is None)
            + "; active bottlenecks are searched among the others only"
        )
    active_at: dict[str, np.ndarray] = {}
    bottlenecks: list[dict[str, Any]] = []
    for up, down in pairwise(positioned):
        with np.errstate(invalid="ignore"):
            cond = (
                np.isfinite(up.speed)
                & np.isfinite(down.speed)
                & (up.speed < th.breakdown_speed_ms)
                & (down.speed - up.speed >= th.bottleneck_speed_difference_ms)
            )
        act = active_windows(cond, th.bottleneck_persistence)
        if act.any():
            active_at[down.station] = active_at.get(down.station, np.zeros_like(act)) | act
            bottlenecks.append(
                {
                    "upstream": up.station,
                    "downstream": down.station,
                    "n_active_windows": int(act.sum()),
                    "n_days": int(act.any(axis=1).sum()),
                }
            )

    station_caps: list[StationCapacity] = []
    per_day_flows: dict[str, list[np.ndarray]] = {}
    for s in series:
        per_day = [s.q_lane[d][np.isfinite(s.q_lane[d])] for d in range(n_days)]
        per_day_flows[s.station] = per_day
        flows = np.concatenate(per_day) if per_day else np.zeros(0)
        cap = _percentile(flows, th.capacity_percentile) if flows.size else None
        interval = None
        speed_at = None
        if cap is not None:
            interval = _bootstrap(
                n_days,
                _pooled_percentile(per_day, th.capacity_percentile),
                unit="day",
                n_resamples=n_bootstrap,
                seed=seed,
                level=level,
                min_units=th.min_days_for_interval,
            )
            at = np.isfinite(s.q_lane) & (s.q_lane >= cap) & np.isfinite(s.speed)
            if at.any():
                speed_at = float(np.median(s.speed[at]))
        active = active_at.get(s.station)
        n_act = int(active.sum()) if active is not None else 0
        discharge = None
        if active is not None and n_act:
            q_act = s.q_lane[active]
            q_act = q_act[np.isfinite(q_act)]
            discharge = float(np.median(q_act)) if q_act.size else None
        category: CapacityCategory
        if n_act:
            category = "bottleneck_discharge"
        elif not has_speed[s.station]:
            category = "no_speed"
        elif congested_days[s.station]:
            category = "congested"
        else:
            category = "never_congested"
        events = pre_breakdown[s.station]
        station_caps.append(
            StationCapacity(
                station=s.station,
                x_m=s.x_m,
                lanes=s.lanes,
                category=category,
                n_windows=int(flows.size),
                n_days=sum(1 for p in per_day if p.size),
                capacity_veh_h_lane=cap,
                interval=interval,
                speed_at_capacity_ms=speed_at,
                n_congested_days=congested_days[s.station],
                n_active_windows=n_act,
                discharge_flow_veh_h_lane=discharge,
                n_breakdowns=len(events),
                pre_breakdown_flow_veh_h_lane=float(np.median(events)) if events else None,
            )
        )

    with_cap = [c for c in station_caps if c.capacity_veh_h_lane is not None]
    discharge_st = [c for c in with_cap if c.category == "bottleneck_discharge"]
    congested_st = [c for c in with_cap if c.category == "congested"]
    basis: CapacityBasis
    if discharge_st:
        basis, used = "bottleneck_discharge", discharge_st
    elif congested_st:
        basis, used = "congested", congested_st
    elif with_cap:
        basis, used = "lower_bound", with_cap
        notes.append(
            "no station reached congestion in these data (or none reports a speed): the "
            "corridor's capacity was not observed, only a lower bound on it"
        )
    else:
        basis, used = "none", []
    names = [c.station for c in used]

    def corridor_value(per_station: Sequence[float]) -> float:
        if not per_station:
            return float("nan")
        arr = np.asarray(per_station, dtype=float)
        arr = arr[np.isfinite(arr)]
        if not arr.size:
            return float("nan")
        return float(arr.max()) if basis == "lower_bound" else float(np.median(arr))

    value = corridor_value([c.capacity_veh_h_lane or float("nan") for c in used]) if used else None
    cap_interval = None
    if used:
        cap_interval = _bootstrap(
            n_days,
            lambda draw: corridor_value(
                [
                    _percentile(_pooled(per_day_flows[n], draw), th.capacity_percentile)
                    for n in names
                ]
            ),
            unit="day",
            n_resamples=n_bootstrap,
            seed=seed,
            level=level,
            min_units=th.min_days_for_interval,
        )
    at_speeds: list[np.ndarray] = []
    for c in used:
        s = next(x for x in series if x.station == c.station)
        at = np.isfinite(s.q_lane) & (s.q_lane >= (c.capacity_veh_h_lane or np.inf))
        at &= np.isfinite(s.speed)
        at_speeds.append(s.speed[at])
    pooled_at = np.concatenate(at_speeds) if at_speeds else np.zeros(0)
    discharges = np.concatenate(
        [
            s.q_lane[active_at[s.station]][np.isfinite(s.q_lane[active_at[s.station]])]
            for s in series
            if s.station in active_at
        ]
        or [np.zeros(0)]
    )
    all_events = [e for s in series for e in pre_breakdown[s.station]]
    if value is not None and not math.isfinite(value):
        value = None
    capacity = CapacityObserved(
        basis=basis,
        value_veh_h_lane=value,
        interval=cap_interval,
        stations=tuple(names),
        speed_at_capacity_ms=float(np.median(pooled_at)) if pooled_at.size else None,
        headway_s=(3600.0 / value) if value else None,
        discharge_flow_veh_h_lane=float(np.median(discharges)) if discharges.size else None,
        pre_breakdown_flow_veh_h_lane=float(np.median(all_events)) if all_events else None,
    )
    if n_days < th.min_days_for_interval:
        notes.append(
            f"{n_days} day(s): fewer than the {th.min_days_for_interval} a day-bootstrap interval "
            "needs, so the observed values carry no interval and verdicts rest on the point values"
        )
    if th.analysis_window_s != ANALYSIS_WINDOW_S or abs(dt - ANALYSIS_WINDOW_S) > 1e-9:
        notes.append(
            f"the active-bottleneck rule counts {dt:g}-s windows; its 5-of-7 persistence was "
            "written for 5-minute windows"
        )
    heavy = classification or HeavyObserved(
        available=False,
        reason="no classification counts were given (loop counts carry no vehicle class)",
    )
    lanes = [s.lanes for s in series]
    return ObservedSide(
        source_interval_s=float(source_interval),
        interval_s=float(dt),
        start_local=clock_text(sg.start_s),
        end_local=clock_text(sg.start_s + sg.n_windows * dt),
        dates=tuple(sg.dates),
        per_lane=bool(grid.per_lane),
        quality=quality_record,
        stations=tuple(s.station for s in series),
        median_lanes=float(np.median(lanes)) if lanes else None,
        speed_limit_ms=corridor_limit,
        speed_limit_source=limit_source,
        free_flow=free_flow,
        free_flow_stations=tuple(ff_station),
        capacity=capacity,
        capacity_stations=tuple(station_caps),
        bottlenecks=tuple(bottlenecks),
        heavy=heavy,
        rules=th,
        notes=tuple(n for n in notes if n),
    )


# ---------------------------------------------------------------------------
# Model side: the population, its draw and its analytical quantities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Population:
    """A driver population as a fleet block runs it.

    Attributes:
        label: What it is (scenario or artifact path).
        fleet: The fleet block.
        calibration: The passenger ``IDMCalibration`` (None for a fleet of
            scalar means — not a measured population).
        heavy_calibration: The heavy population's artifact, when it has one.
        network_kind: ``corridor``/``osm``/``ring`` of the scenario, if known.
        sources: Paths and SHA-256 of the files it was read from.
    """

    label: str
    fleet: FleetSpec
    calibration: IDMCalibration | None
    heavy_calibration: IDMCalibration | None
    network_kind: str | None
    sources: dict[str, str]

    @property
    def model(self) -> str:
        """``IDM`` or ``EIDM``."""
        return str(self.fleet.model)

    @property
    def heavy_fraction(self) -> float:
        """Configured truck share."""
        return float(self.fleet.heavy.fraction) if self.fleet.heavy is not None else 0.0

    @property
    def speed_factor(self) -> float:
        """Configured passenger speed factor (``FleetSpec.speed_factor``)."""
        return float(self.fleet.speed_factor)

    @property
    def speed_dev(self) -> float:
        """Configured spread of the passenger speed factors (``FleetSpec.speed_dev``)."""
        return float(self.fleet.speed_dev)

    def passenger_means(self) -> dict[str, float]:
        """Population means (artifact or scalar fleet)."""
        if self.calibration is not None:
            return {k: float(self.calibration.mean[k]) for k in IDM_PARAM_ORDER}
        f = self.fleet
        return {"v0": f.v0, "T": f.T, "a_max": f.a_max, "b": f.b, "s0": f.s0}

    def passenger_sd(self) -> dict[str, float]:
        """Population standard deviations (covariance diagonal or ``frac × mean``)."""
        if self.calibration is not None:
            cov = np.asarray(self.calibration.cov, dtype=float)
            sd = np.sqrt(np.clip(np.diag(cov), 0.0, None))
            return dict(zip(self.calibration.param_names, map(float, sd), strict=True))
        means = self.passenger_means()
        return {k: float(self.fleet.heterogeneity_frac * means[k]) for k in IDM_PARAM_ORDER}


def _load_calibration(path: str, sources: dict[str, str], key: str) -> IDMCalibration:
    resolved = resolve_repo_path(path)
    sources[key] = _rel(resolved)
    sources[f"{key}_sha256"] = _sha256(resolved)
    return IDMCalibration.load(resolved)


def population_from_fleet(
    fleet: FleetSpec, *, label: str, network_kind: str | None = None
) -> Population:
    """A :class:`Population` from a fleet block (artifacts resolved repo-relatively)."""
    sources: dict[str, str] = {}
    cal = (
        _load_calibration(fleet.idm_calibration, sources, "idm_calibration")
        if fleet.idm_calibration
        else None
    )
    heavy_cal = (
        _load_calibration(fleet.heavy.idm_calibration, sources, "heavy_idm_calibration")
        if fleet.heavy is not None and fleet.heavy.idm_calibration
        else None
    )
    return Population(
        label=label,
        fleet=fleet,
        calibration=cal,
        heavy_calibration=heavy_cal,
        network_kind=network_kind,
        sources=sources,
    )


def population_from_scenario(path: str | Path) -> Population:
    """The fleet block of a scenario YAML.

    Only the ``fleet`` block is validated (``FleetSpec``) and the network's
    ``kind`` read; nothing else of the scenario is used.

    Raises:
        ValueError: The file has no ``fleet`` block.
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict) or "fleet" not in raw:
        raise ValueError(f"{p}: no fleet block")
    fleet = FleetSpec.model_validate(raw["fleet"] or {})
    network = raw.get("network") or {}
    kind = network.get("kind") if isinstance(network, dict) else None
    pop = population_from_fleet(fleet, label=_rel(p), network_kind=kind)
    sources = {"scenario": _rel(p), "scenario_sha256": _sha256(p), **pop.sources}
    return replace(pop, sources=sources)


def population_from_artifact(path: str | Path, *, model: str = "IDM") -> Population:
    """A passenger-only fleet on an ``IDMCalibration`` artifact."""
    resolved = resolve_repo_path(path)
    fleet = FleetSpec(model=model, idm_calibration=_rel(resolved))  # type: ignore[arg-type]
    return population_from_fleet(fleet, label=_rel(resolved))


@dataclass(frozen=True)
class Adjustments:
    """Corridor-wide knob settings applied to a population (1.0 = as measured).

    Attributes:
        v0_scale: Factor on the passenger population's mean desired speed.
        t_scale: Factor on its mean time headway (relative to the
            configured population, as ``scripts/calibrate_capacity.py``
            scales it).
        speed_factor: The passenger vehicles' SUMO ``speedFactor`` (desired
            speed cap = factor × posted limit; heavy vehicles keep 1.0);
            None = as configured (``FleetSpec.speed_factor``).
        heavy_fraction: Truck share; None = as configured.
    """

    v0_scale: float = 1.0
    t_scale: float = 1.0
    speed_factor: float | None = None
    heavy_fraction: float | None = None


@dataclass(frozen=True)
class Drivers:
    """A drawn fleet: one entry per driver.

    ``speed_z`` (only when the fleet sets ``speed_dev``) holds one standard
    normal deviate per driver: a passenger's speed factor is ``speed_factor +
    speed_dev × speed_z`` inside SUMO's cut-offs (:func:`desired_speeds`).
    """

    v0: np.ndarray
    T: np.ndarray
    s0: np.ndarray
    length: np.ndarray
    heavy: np.ndarray
    speed_z: np.ndarray | None = None


def _draw_calibrated(
    cal_mean: np.ndarray, cov: np.ndarray, n: int, rng: np.random.Generator
) -> np.ndarray:
    """Truncated multivariate normal draw, exactly as
    ``microsim.vehicles._draw_from_calibration`` (±3σ marginals, hard floors,
    rejection in batches, rows in order)."""
    sigma = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    lo = np.maximum(cal_mean - 3.0 * sigma, np.array([IDM_HARD_LOWER[k] for k in IDM_PARAM_ORDER]))
    hi = cal_mean + 3.0 * sigma
    parts: list[np.ndarray] = []
    have = 0
    for _ in range(1000):
        if have >= n:
            break
        batch = rng.multivariate_normal(cal_mean, cov, size=n - have)
        ok = np.all((batch >= lo) & (batch <= hi), axis=1)
        parts.append(batch[ok])
        have += int(ok.sum())
    out = np.concatenate(parts) if parts else np.zeros((0, len(IDM_PARAM_ORDER)))
    if out.shape[0] < n:  # pragma: no cover - degenerate artifact only
        fallback = np.clip(cal_mean, lo, hi)
        out = np.vstack([out, np.tile(fallback, (n - out.shape[0], 1))])
    return out[:n]


def _draw_scalar(
    means: Mapping[str, float], frac: float, n: int, rng: np.random.Generator
) -> np.ndarray:
    """Independent truncated normals ``N(mean, frac·mean)`` at ±3σ with the
    hard floors — the distribution of ``microsim.vehicles.draw_vehicle_params``'
    scalar path (vectorised; not its per-vehicle sequence)."""
    out = np.empty((n, len(IDM_PARAM_ORDER)))
    for j, key in enumerate(IDM_PARAM_ORDER):
        mean = float(means[key])
        sigma = frac * mean
        lo = max(mean - 3.0 * sigma, IDM_HARD_LOWER[key])
        hi = mean + 3.0 * sigma
        if sigma == 0.0:
            out[:, j] = min(max(mean, lo), hi)
            continue
        x = rng.normal(mean, sigma, size=n)
        bad = (x < lo) | (x > hi)
        for _ in range(1000):
            if not bad.any():
                break
            x[bad] = rng.normal(mean, sigma, size=int(bad.sum()))
            bad = (x < lo) | (x > hi)
        out[:, j] = np.clip(x, lo, hi)
    return out


def _population_rows(
    cal: IDMCalibration | None,
    means: Mapping[str, float],
    frac: float,
    n: int,
    rng: np.random.Generator,
    *,
    v0_scale: float = 1.0,
    t_scale: float = 1.0,
) -> np.ndarray:
    if cal is not None:
        mean = np.array([float(cal.mean[k]) for k in IDM_PARAM_ORDER])
        mean[0] *= v0_scale
        mean[1] *= t_scale
        return _draw_calibrated(mean, np.asarray(cal.cov, dtype=float), n, rng)
    scaled = dict(means)
    scaled["v0"] = scaled["v0"] * v0_scale
    scaled["T"] = scaled["T"] * t_scale
    return _draw_scalar(scaled, frac, n, rng)


def draw_drivers(
    population: Population,
    adjustments: Adjustments | None = None,
    *,
    n: int = MODEL_DRAWS,
    seed: int = MODEL_DRAW_SEED,
) -> Drivers:
    """Draw ``n`` drivers as the runner does (module docstring).

    Passengers come from ``make_rng(seed)`` (the calibrated path is the
    runner's truncated multivariate normal, value for value); trucks, when
    the fleet has a heavy block, from ``make_rng(seed + 1)``: a Bernoulli
    share, then the heavy population. ``v0_scale``/``t_scale`` act on the
    passenger population only, as the capacity calibration's T-scaling does.
    When the fleet spreads its speed factors (``speed_dev``), one standard
    normal deviate per driver comes from ``make_rng(seed + 2)``
    (:attr:`Drivers.speed_z`).
    """
    adj = adjustments or Adjustments()
    fleet = population.fleet
    rows = _population_rows(
        population.calibration,
        population.passenger_means(),
        fleet.heterogeneity_frac,
        n,
        make_rng(seed),
        v0_scale=adj.v0_scale,
        t_scale=adj.t_scale,
    )
    v0, t_h, s0 = rows[:, 0].copy(), rows[:, 1].copy(), rows[:, 4].copy()
    length = np.full(n, PASSENGER_LENGTH_M)
    heavy = np.zeros(n, dtype=bool)
    spec = fleet.heavy
    fraction = population.heavy_fraction if adj.heavy_fraction is None else adj.heavy_fraction
    if spec is not None and fraction > 0.0 and n:
        rng_h = make_rng(seed + 1)
        heavy = rng_h.uniform(size=n) < fraction
        k = int(heavy.sum())
        if k:
            h_means = {
                "v0": spec.v0 if spec.v0 is not None else fleet.v0,
                "T": spec.T if spec.T is not None else fleet.T,
                "a_max": spec.a_max if spec.a_max is not None else fleet.a_max,
                "b": spec.b if spec.b is not None else fleet.b,
                "s0": spec.s0 if spec.s0 is not None else fleet.s0,
            }
            h_rows = _population_rows(
                population.heavy_calibration, h_means, spec.heterogeneity_frac, k, rng_h
            )
            v0[heavy] = h_rows[:, 0]
            t_h[heavy] = h_rows[:, 1]
            s0[heavy] = h_rows[:, 4]
            length[heavy] = spec.length_m
    speed_z = make_rng(seed + 2).standard_normal(n) if population.speed_dev > 0.0 else None
    return Drivers(v0=v0, T=t_h, s0=s0, length=length, heavy=heavy, speed_z=speed_z)


def desired_speeds(
    drivers: Drivers,
    speed_limit_ms: float | None,
    speed_factor: float,
    speed_dev: float = 0.0,
) -> np.ndarray:
    """``min(v0, f × limit)`` per driver (SUMO's desired speed).

    ``f`` is the driver's SUMO ``speedFactor`` as ``microsim.vehicles``
    writes it: ``speed_factor`` for a passenger — spread to ``speed_factor +
    speed_dev × z`` (:attr:`Drivers.speed_z`) clipped to SUMO's cut-offs 0.2
    and 2 when ``speed_dev`` is set (the engine redraws outside the cut-offs
    rather than clipping; the two differ only there) — and 1.0 for a heavy
    vehicle.
    """
    if speed_limit_ms is None:
        return np.array(drivers.v0, dtype=float)
    factor = np.full(drivers.v0.shape, float(speed_factor))
    if speed_dev > 0.0 and drivers.speed_z is not None:
        lo, hi = SPEED_FACTOR_BOUNDS
        factor = np.clip(float(speed_factor) + float(speed_dev) * drivers.speed_z, lo, hi)
    factor = np.where(drivers.heavy, ENGINE_SPEED_FACTOR, factor)
    return np.asarray(np.minimum(drivers.v0, factor * speed_limit_ms), dtype=float)


def idm_equilibrium_gap(
    v: np.ndarray, v0: np.ndarray, t_h: np.ndarray, s0: np.ndarray
) -> np.ndarray:
    """``s_eq = (s0 + v·T)/√(1 − (v/v0)^4)`` (CLAUDE.md §9), ``inf`` at ``v ≥ v0``."""
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.clip(1.0 - (v / v0) ** SUMO_IDM_DELTA, 0.0, None)
        gap = (s0 + v * t_h) / np.sqrt(ratio)
    return np.where(v < v0, gap, np.inf)


def _driver_flow(model: str, v: np.ndarray, d: Drivers, v_des: np.ndarray) -> np.ndarray:
    """One driver's homogeneous equilibrium flow at speed ``v`` [veh/s]."""
    if model == "EIDM":
        gap = np.where(v <= v_des, d.s0 + v * d.T, np.inf)
    else:
        gap = idm_equilibrium_gap(v, v_des, d.T, d.s0)
    return np.asarray(v / (gap + d.length), dtype=float)


_GOLDEN: Final[float] = (math.sqrt(5.0) - 1.0) / 2.0


def _golden_max(
    f: Callable[[np.ndarray], np.ndarray], a: np.ndarray, b: np.ndarray, iters: int = 90
) -> np.ndarray:
    """Element-wise golden-section maximiser of a unimodal ``f`` on ``[a, b]``."""
    a, b = a.astype(float).copy(), b.astype(float).copy()
    c = b - _GOLDEN * (b - a)
    e = a + _GOLDEN * (b - a)
    fc, fe = f(c), f(e)
    for _ in range(iters):
        left = fc >= fe
        b = np.where(left, e, b)
        a = np.where(left, a, c)
        e_new = np.where(left, c, a + _GOLDEN * (b - a))
        c_new = np.where(left, b - _GOLDEN * (b - a), e)
        fe_new = np.where(left, fc, f(e_new))
        fc_new = np.where(left, f(c_new), fe)
        c, e, fc, fe = c_new, e_new, fc_new, fe_new
    return np.asarray((a + b) / 2.0, dtype=float)


def free_flow_speeds(
    model: str, drivers: Drivers, v_des: np.ndarray, flow_veh_s: float
) -> tuple[np.ndarray, int]:
    """Each driver's steady-state speed at a per-lane flow (free branch).

    IDM: the speed on the free branch ``v ≥ v*`` of the driver's own
    equilibrium flow ``v/(s_eq(v) + L)``; a driver whose own capacity is below
    the flow cannot carry it and is put at its capacity speed (counted).
    EIDM: SUMO 1.27.1's EIDM is built on the improved IDM, whose equilibrium
    gap is ``s0 + v·T`` below the desired speed (docs/CONTRACTS.md, the weave
    brake onset read from ``MSCFModel_EIDM.cpp``) — the driver holds its
    desired speed whenever ``v_des/(s0 + v_des·T + L)`` carries the flow.

    Returns:
        ``(speeds [m/s], n_beyond_capacity)``.
    """
    q = float(flow_veh_s)
    if model == "EIDM":
        cap = v_des / (drivers.s0 + v_des * drivers.T + drivers.length)
        return v_des.copy(), int((cap < q).sum())

    def own(v: np.ndarray) -> np.ndarray:
        return _driver_flow("IDM", v, drivers, v_des)

    zero = np.zeros_like(v_des)
    v_star = _golden_max(own, zero, v_des)
    q_star = own(v_star)
    beyond = q_star < q
    lo = v_star.copy()
    hi = v_des * (1.0 - 1e-12)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        above = own(mid) > q
        lo = np.where(above, mid, lo)
        hi = np.where(above, hi, mid)
    speeds = np.where(beyond, v_star, (lo + hi) / 2.0)
    return speeds, int(beyond.sum())


def population_capacity(
    model: str, drivers: Drivers, v_des: np.ndarray
) -> tuple[float, float, bool]:
    """Equilibrium capacity of a drawn population on one lane [veh/h], its speed.

    A queue moving at a common speed ``v``: mean spacing
    ``mean_i s_e,i(v) + mean_i L_i``, flow ``v`` over it, maximised over
    ``v`` below the slowest driver's desired speed (who cannot hold a faster
    queue) — the convention of ``scripts/i24_calibrate_capacity.py
    --equilibrium`` ("heterogeneous"): a lower bound on a multi-lane road,
    where faster drivers pass. IDM gap ``(s0 + vT)/√(1 − (v/v_des)^4)``; EIDM
    (improved IDM) ``s0 + vT``, so its maximum sits at the speed bound.

    Returns:
        ``(capacity [veh/h/lane], speed at it [m/s], limited_by_slowest)``.
    """
    v_hi = float(v_des.min()) * 0.999
    mean_length = float(drivers.length.mean())

    def q_of(v: float) -> float:
        if model == "EIDM":
            gap = float(np.mean(drivers.s0 + v * drivers.T))
        else:
            gap = float(
                np.mean(idm_equilibrium_gap(np.full_like(v_des, v), v_des, drivers.T, drivers.s0))
            )
        return v / (gap + mean_length)

    if model == "EIDM":
        return veh_s_to_veh_h(q_of(v_hi)), v_hi, True
    grid = np.linspace(v_hi / CAPACITY_SPEED_GRID, v_hi, CAPACITY_SPEED_GRID)
    values = np.empty(grid.size)
    step = max(1, GRID_CHUNK_ELEMENTS // max(1, v_des.size))
    for start in range(0, grid.size, step):
        v = grid[start : start + step, None]
        gap = idm_equilibrium_gap(v, v_des[None, :], drivers.T[None, :], drivers.s0[None, :])
        values[start : start + step] = v[:, 0] / (gap.mean(axis=1) + mean_length)
    i = int(np.argmax(values))
    a = grid[max(i - 1, 0)]
    b = grid[min(i + 1, len(grid) - 1)]
    best = float(
        _golden_max(lambda x: np.array([q_of(float(x[0]))]), np.array([a]), np.array([b]), 60)[0]
    )
    q_best = q_of(best)
    if values[i] > q_best:
        best, q_best = float(grid[i]), float(values[i])
    return veh_s_to_veh_h(q_best), best, bool(i >= CAPACITY_SPEED_GRID - 2)


def mean_driver_capacity(
    model: str, means: Mapping[str, float], speed_limit_ms: float | None, speed_factor: float = 1.0
) -> tuple[float, float]:
    """Closed-form equilibrium capacity of the mean passenger driver [veh/h], its speed.

    ``q(v) = v/(s_e(v) + L)`` maximised over ``v`` in ``(0, v_des)``, with
    ``v_des = min(v0, speed_factor × limit)`` and ``L`` the passenger length —
    ``equilibrium_capacity`` of ``scripts/i24_calibrate_capacity.py`` (IDM),
    or ``v_des/(s0 + v_des·T + L)`` for EIDM's improved-IDM equilibrium.
    """
    v0 = float(means["v0"])
    v_des = min(v0, speed_factor * speed_limit_ms) if speed_limit_ms is not None else v0
    d = Drivers(
        v0=np.array([v0]),
        T=np.array([float(means["T"])]),
        s0=np.array([float(means["s0"])]),
        length=np.array([PASSENGER_LENGTH_M]),
        heavy=np.array([False]),
    )
    vd = np.array([v_des])
    if model == "EIDM":
        v = v_des * 0.999
        return veh_s_to_veh_h(float(_driver_flow("EIDM", np.array([v]), d, vd)[0])), v
    v_star = _golden_max(lambda v: _driver_flow("IDM", v, d, vd), np.array([0.0]), vd)
    return veh_s_to_veh_h(float(_driver_flow("IDM", v_star, d, vd)[0])), float(v_star[0])


# ---------------------------------------------------------------------------
# Simulated capacity (sidecars of scripts/calibrate_capacity.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SidecarCandidate:
    """A capacity-calibration sidecar considered for this population.

    Attributes:
        path: Repo-relative path.
        sha256: File hash.
        accepted: Usable for this population.
        reason: Why (not).
        model: Car-following model of its runs (read from its base scenario;
            None when the sidecar does not record one).
        lanes: Lanes of its straight road.
        source: Its source population.
        t_scale_current: This population's mean T over the source's.
    """

    path: str
    sha256: str
    accepted: bool
    reason: str
    model: str | None = None
    lanes: int | None = None
    source: str | None = None
    t_scale_current: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "accepted": self.accepted,
            "reason": self.reason,
            "model": self.model,
            "lanes": self.lanes,
            "source": self.source,
            "t_scale_current": _num(self.t_scale_current),
        }


@dataclass(frozen=True)
class _Sidecar:
    candidate: SidecarCandidate
    source: Population
    scales: np.ndarray
    capacities: np.ndarray
    speeds: np.ndarray


def _same_population_but_t(pop: IDMCalibration, src: IDMCalibration) -> float | None:
    """``pop.T / src.T`` when ``pop`` is ``src`` with only its mean T scaled."""
    if tuple(pop.param_names) != tuple(src.param_names):
        return None
    if not np.allclose(np.asarray(pop.cov), np.asarray(src.cov), rtol=1e-9, atol=1e-12):
        return None
    for k in pop.param_names:
        if k != "T" and not math.isclose(pop.mean[k], src.mean[k], rel_tol=1e-9, abs_tol=1e-12):
            return None
    return float(pop.mean["T"] / src.mean["T"])


def evaluate_sidecar(
    path: str | Path, population: Population, *, explicit: bool
) -> tuple[SidecarCandidate, _Sidecar | None]:
    """Judge whether a sidecar measures this population's capacity.

    Accepted when its source population, with its mean T scaled, is this
    population (covariance and other means equal), the population's scale
    lies inside its grid, and its runs used this fleet's car-following model
    — read from the fleet block of its ``base_scenario`` as that file is
    today (the sidecar does not record the model itself). A sidecar without
    a base scenario is accepted only when named explicitly, with a note.
    """
    p = Path(path)
    sha = _sha256(p)
    rel = _rel(p)

    def reject(reason: str, **kw: Any) -> tuple[SidecarCandidate, None]:
        return SidecarCandidate(path=rel, sha256=sha, accepted=False, reason=reason, **kw), None

    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return reject("not readable JSON")
    if not isinstance(raw, dict) or not isinstance(raw.get("table"), list) or "source" not in raw:
        return reject("not a capacity-calibration sidecar (no table / source)")
    if population.calibration is None:
        return reject("the fleet is not an artifact population")
    try:
        source = population_from_artifact(str(raw["source"]))
    except (FileNotFoundError, ValueError) as exc:
        return reject(f"source population not loadable: {exc}")
    assert source.calibration is not None
    f_cur = _same_population_but_t(population.calibration, source.calibration)
    lanes = (raw.get("corridor") or {}).get("lanes")
    lanes = int(lanes) if lanes is not None else None
    if f_cur is None:
        return reject("measures a different population", source=str(raw["source"]), lanes=lanes)
    model: str | None = None
    base_fleet: FleetSpec | None = None
    base = raw.get("base_scenario")
    if base:
        try:
            base_path = resolve_repo_path(base)
            base_raw = yaml.safe_load(base_path.read_text()) or {}
            base_fleet = FleetSpec.model_validate(base_raw.get("fleet") or {})
            model = str(base_fleet.model)
        except (FileNotFoundError, ValueError):
            model = None
    kw: dict[str, Any] = {
        "source": str(raw["source"]),
        "lanes": lanes,
        "t_scale_current": f_cur,
        "model": model,
    }
    if model is None and not explicit:
        return reject(
            "its car-following model is not recorded (name it explicitly to use it)", **kw
        )
    if model is not None and model != population.model:
        return reject(f"measured under {model}, the fleet runs {population.model}", **kw)
    rows = [r for r in raw["table"] if not r.get("demand_limited")]
    scales = np.array([float(r["T_scale"]) for r in rows])
    caps = np.array([float(r["capacity_veh_h_lane"]) for r in rows])
    if not scales.size:
        return reject("no capacity-limited grid point", **kw)
    order = np.argsort(scales)
    scales, caps = scales[order], caps[order]
    if not scales[0] - 1e-9 <= f_cur <= scales[-1] + 1e-9:
        return reject(
            f"the population's T scale {f_cur:.4f} lies outside the sidecar's grid "
            f"[{scales[0]:g}, {scales[-1]:g}]",
            **kw,
        )
    speeds = np.full(scales.size, np.nan)
    for i, f in enumerate(scales):
        vals = [
            float(r["mean_speed_ms_at_ref"])
            for r in raw.get("runs", [])
            if r.get("T_scale") == f and r.get("mean_speed_ms_at_ref") is not None
        ]
        if vals:
            speeds[i] = float(np.mean(vals))
    reason = "same population (T scaled), same car-following model"
    if model is None:
        reason = (
            "same population (T scaled); the sidecar does not record its car-following model — "
            "used because it was named explicitly, assumed to be the fleet's"
        )
    candidate = SidecarCandidate(path=rel, sha256=sha, accepted=True, reason=reason, **kw)
    # the straight road's fleet: the base scenario's block (its model and any
    # trucks) on the source population; without a base, the fleet's model
    ref_fleet = (
        base_fleet.model_copy(update={"idm_calibration": source.fleet.idm_calibration})
        if base_fleet is not None
        else source.fleet.model_copy(update={"model": population.model})
    )
    source = replace(population_from_fleet(ref_fleet, label=source.label), sources=source.sources)
    return candidate, _Sidecar(candidate, source, scales, caps, speeds)


def discover_sidecars(directory: str | Path | None = None) -> list[Path]:
    """Every ``*.calibration.json`` in ``directory`` (default ``artifacts/``)."""
    base = Path(directory) if directory is not None else REPO_ROOT / "artifacts"
    return sorted(base.glob("*.calibration.json")) if base.is_dir() else []


def _interp(x: float, xs: np.ndarray, ys: np.ndarray) -> float:
    ok = np.isfinite(ys)
    if not ok.any():
        return float("nan")
    return float(np.interp(x, xs[ok], ys[ok]))


# ---------------------------------------------------------------------------
# Model side record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulatedCapacity:
    """The simulated capacity used, and how it was carried to this corridor.

    Attributes:
        sidecar: The sidecar.
        raw_veh_h_lane: Its straight-road capacity at this population's T
            scale (linear interpolation in the grid).
        conditions_ratio: Analytical capacity on this corridor (posted limit,
            truck share, adjustments) over that on the sidecar's road
            (uncapped, its own truck share): what the straight road lacked.
        value_veh_h_lane: ``raw × ratio`` — the model value.
        speed_ms: Mean speed at its reference section, interpolated.
    """

    sidecar: SidecarCandidate
    raw_veh_h_lane: float
    conditions_ratio: float
    value_veh_h_lane: float
    speed_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "sidecar": self.sidecar.to_dict(),
            "raw_veh_h_lane": _num(self.raw_veh_h_lane, 1),
            "conditions_ratio": _num(self.conditions_ratio),
            "value_veh_h_lane": _num(self.value_veh_h_lane, 1),
            "speed_ms": _num(self.speed_ms),
        }


@dataclass(frozen=True)
class ModelSide:
    """What the population would do on this corridor (analytical / simulated).

    Attributes:
        population: Label.
        population_sources: Paths and SHA-256 of the population's files.
        model: ``IDM``/``EIDM``.
        n_draws: Drivers drawn.
        draw_seed: Seed.
        speed_limit_ms: The cap applied (None: uncapped).
        speed_factor: The passenger SUMO speed factor
            (``FleetSpec.speed_factor``; 1.0 unless the scenario sets it).
        heavy_fraction: Truck share.
        passenger_means: Population means (v0, T, a_max, b, s0).
        desired_speed: ``mean``, ``p15``, ``p50``, ``p85`` of the drivers'
            desired speeds [m/s].
        free_flow_flow_veh_h_lane: The per-lane flow the free-flow speed is
            evaluated at (the observed one).
        free_flow_speed_ms: Mean steady-state speed at that flow — compared
            with the observed median.
        n_beyond_capacity: Drivers that cannot carry that flow.
        capacity_mean_driver: ``(veh/h/lane, m/s)`` closed form, passenger
            means.
        capacity_population: ``(veh/h/lane, m/s, limited)`` drawn population.
        capacity_simulated: The simulated measurement used, if any.
        capacity_basis: ``simulated``, ``analytical`` or ``none``.
        capacity_veh_h_lane: The model value compared.
        capacity_speed_ms: The speed at it.
        sidecars: Every sidecar considered, with the decision.
        notes: Plain statements.
    """

    population: str
    population_sources: dict[str, str]
    model: str
    n_draws: int
    draw_seed: int
    speed_limit_ms: float | None
    speed_factor: float
    heavy_fraction: float
    passenger_means: dict[str, float]
    desired_speed: dict[str, float]
    free_flow_flow_veh_h_lane: float | None
    free_flow_speed_ms: float | None
    n_beyond_capacity: int
    capacity_mean_driver: tuple[float, float]
    capacity_population: tuple[float, float, bool]
    capacity_simulated: SimulatedCapacity | None
    capacity_basis: Literal["simulated", "analytical", "none"]
    capacity_veh_h_lane: float | None
    capacity_speed_ms: float | None
    sidecars: tuple[SidecarCandidate, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "population": self.population,
            "population_sources": dict(self.population_sources),
            "model": self.model,
            "n_draws": self.n_draws,
            "draw_seed": self.draw_seed,
            "speed_limit_ms": _num(self.speed_limit_ms),
            "speed_factor": self.speed_factor,
            "heavy_fraction": _num(self.heavy_fraction),
            "passenger_means": {k: _num(v) for k, v in self.passenger_means.items()},
            "desired_speed": {k: _num(v) for k, v in self.desired_speed.items()},
            "free_flow_flow_veh_h_lane": _num(self.free_flow_flow_veh_h_lane, 1),
            "free_flow_speed_ms": _num(self.free_flow_speed_ms),
            "n_beyond_capacity": self.n_beyond_capacity,
            "capacity_mean_driver": {
                "veh_h_lane": _num(self.capacity_mean_driver[0], 1),
                "speed_ms": _num(self.capacity_mean_driver[1]),
            },
            "capacity_population": {
                "veh_h_lane": _num(self.capacity_population[0], 1),
                "speed_ms": _num(self.capacity_population[1]),
                "limited_by_slowest_driver": self.capacity_population[2],
            },
            "capacity_simulated": None
            if self.capacity_simulated is None
            else self.capacity_simulated.to_dict(),
            "capacity_basis": self.capacity_basis,
            "capacity_veh_h_lane": _num(self.capacity_veh_h_lane, 1),
            "capacity_speed_ms": _num(self.capacity_speed_ms),
            "sidecars": [s.to_dict() for s in self.sidecars],
            "notes": list(self.notes),
        }


class _Evaluator:
    """Analytical model quantities for one population on one corridor."""

    def __init__(
        self,
        population: Population,
        *,
        speed_limit_ms: float | None,
        free_flow_flow_veh_h_lane: float | None,
        n_draws: int,
        seed: int,
    ) -> None:
        self.pop = population
        self.limit = speed_limit_ms
        self.q_ff = free_flow_flow_veh_h_lane
        self.n = n_draws
        self.seed = seed
        self._cache: dict[tuple[Any, ...], Any] = {}

    def drivers(self, adj: Adjustments, pop: Population | None = None) -> Drivers:
        return draw_drivers(pop or self.pop, adj, n=self.n, seed=self.seed)

    def speed_factor(self, adj: Adjustments, pop: Population | None = None) -> float:
        """The passenger speed factor under ``adj`` (None = as configured)."""
        target = pop or self.pop
        return target.speed_factor if adj.speed_factor is None else float(adj.speed_factor)

    def ff_speed(self, adj: Adjustments) -> tuple[float | None, int]:
        key = ("ff", adj)
        if key not in self._cache:
            if self.q_ff is None:
                self._cache[key] = (None, 0)
            else:
                d = self.drivers(adj)
                v_des = desired_speeds(d, self.limit, self.speed_factor(adj), self.pop.speed_dev)
                speeds, beyond = free_flow_speeds(self.pop.model, d, v_des, self.q_ff / 3600.0)
                self._cache[key] = (float(speeds.mean()), beyond)
        return self._cache[key]  # type: ignore[no-any-return]

    def capacity(
        self, adj: Adjustments, *, pop: Population | None = None, uncapped: bool = False
    ) -> tuple[float, float, bool]:
        """Drawn-population capacity under ``adj`` — on this corridor (its
        limit), or ``uncapped`` (a generated straight road, whose edges'
        50 m/s limit caps no driver: ``microsim.networks.EDGE_SPEED_LIMIT_MS``)."""
        limit = None if uncapped else self.limit
        target = pop or self.pop
        key = ("cap", adj, id(target), limit)
        if key not in self._cache:
            d = self.drivers(adj, target)
            v_des = desired_speeds(d, limit, self.speed_factor(adj, target), target.speed_dev)
            self._cache[key] = population_capacity(target.model, d, v_des)
        return self._cache[key]  # type: ignore[no-any-return]


def _simulated_at(
    ev: _Evaluator, sc: _Sidecar, adj: Adjustments, f_src: float
) -> tuple[float, float, float]:
    """Simulated capacity at source scale ``f_src``, carried to this corridor:
    ``(raw, ratio, value)``."""
    raw = _interp(f_src, sc.scales, sc.capacities)
    f_cur = sc.candidate.t_scale_current or 1.0
    here = replace(adj, t_scale=f_src / f_cur)
    neutral = (
        ev.limit is None
        and adj.v0_scale == 1.0
        and ev.speed_factor(adj) == sc.source.speed_factor
        and (adj.heavy_fraction is None or adj.heavy_fraction == ev.pop.heavy_fraction)
        and ev.pop.heavy_fraction == sc.source.heavy_fraction
        and ev.pop.fleet.heavy == sc.source.fleet.heavy
    )
    if neutral:
        return raw, 1.0, raw
    num = ev.capacity(here)[0]
    den = ev.capacity(Adjustments(t_scale=f_src), pop=sc.source, uncapped=True)[0]
    ratio = num / den if den > 0 else float("nan")
    return raw, ratio, raw * ratio


def model_side(
    population: Population,
    *,
    speed_limit_ms: float | None,
    free_flow_flow_veh_h_lane: float | None,
    corridor_lanes: float | None = None,
    sidecars: Sequence[str | Path] | None = None,
    explicit_sidecars: bool = False,
    n_draws: int = MODEL_DRAWS,
    seed: int = MODEL_DRAW_SEED,
) -> tuple[ModelSide, _Evaluator, _Sidecar | None]:
    """The model's free-flow speed, capacity per lane and truck share here.

    Args:
        population: The population.
        speed_limit_ms: Posted limit that caps desired speeds (None: none).
        free_flow_flow_veh_h_lane: Per-lane flow of the observed free-flow
            windows; the model speed is evaluated there.
        corridor_lanes: Lane count used to prefer a sidecar of the same
            width.
        sidecars: Sidecar paths to consider (default: discovered in
            ``artifacts/``).
        explicit_sidecars: The paths were named by the user.
        n_draws: Drivers drawn.
        seed: Draw seed.
    """
    notes: list[str] = []
    ev = _Evaluator(
        population,
        speed_limit_ms=speed_limit_ms,
        free_flow_flow_veh_h_lane=free_flow_flow_veh_h_lane,
        n_draws=n_draws,
        seed=seed,
    )
    adj = Adjustments()
    d = ev.drivers(adj)
    v_des = desired_speeds(d, speed_limit_ms, population.speed_factor, population.speed_dev)
    desired = {
        "mean": float(v_des.mean()),
        "p15": float(np.percentile(v_des, 15.0)),
        "p50": float(np.percentile(v_des, 50.0)),
        "p85": float(np.percentile(v_des, 85.0)),
    }
    ff, beyond = ev.ff_speed(adj)
    if beyond:
        notes.append(
            f"{beyond} of {n_draws} drawn drivers cannot carry the free-flow flow in steady "
            "state and are counted at their own capacity speed"
        )
    means = population.passenger_means()
    mean_cap = mean_driver_capacity(
        population.model, means, speed_limit_ms, population.speed_factor
    )
    if population.speed_factor != ENGINE_SPEED_FACTOR or population.speed_dev > 0.0:
        notes.append(
            f"the fleet sets a speed factor of {population.speed_factor:g}"
            + (
                f" spread by {population.speed_dev:g} (SUMO speedDev)"
                if population.speed_dev > 0.0
                else ""
            )
            + ": passenger drivers want min(v0, factor × limit), heavy vehicles min(v0, limit) "
            "(microsim.vehicles)"
        )
    pop_cap = ev.capacity(adj)
    if population.calibration is None:
        notes.append(
            "the fleet is not a measured population (scalar means with a heterogeneity fraction): "
            "docs/FRISCO_PROTOCOL.md §7.2 requires one of the measured populations"
        )
    if population.fleet.delta != SUMO_IDM_DELTA:
        notes.append(
            f"the fleet states delta = {population.fleet.delta:g}; SUMO uses 4 (used here)"
        )
    if speed_limit_ms is None and population.network_kind in ("corridor", "ring"):
        notes.append(
            "the scenario runs on a generated road whose edges (50 m/s) cap no driver: its "
            "desired speeds are compared uncapped, whatever the real road posts"
        )
    elif speed_limit_ms is None:
        notes.append(
            "no posted limit given: desired speeds are uncapped. On a map-imported corridor "
            "the road's limit caps every driver's desired speed (layout checklist item e) — "
            "give the limit"
        )
    candidates: list[SidecarCandidate] = []
    accepted: list[_Sidecar] = []
    paths = list(sidecars) if sidecars is not None else discover_sidecars()
    for path in paths:
        cand, sc = evaluate_sidecar(path, population, explicit=explicit_sidecars)
        candidates.append(cand)
        if sc is not None:
            accepted.append(sc)

    def rank(sc: _Sidecar) -> tuple[float, str]:
        lanes = sc.candidate.lanes
        if lanes is None or corridor_lanes is None:
            return (math.inf, sc.candidate.path)
        return (abs(lanes - corridor_lanes), sc.candidate.path)

    chosen = min(accepted, key=rank) if accepted else None
    simulated: SimulatedCapacity | None = None
    if chosen is not None:
        f_cur = chosen.candidate.t_scale_current or 1.0
        raw, ratio, value = _simulated_at(ev, chosen, adj, f_cur)
        simulated = SimulatedCapacity(
            sidecar=chosen.candidate,
            raw_veh_h_lane=raw,
            conditions_ratio=ratio,
            value_veh_h_lane=value,
            speed_ms=_interp(f_cur, chosen.scales, chosen.speeds)
            if np.isfinite(chosen.speeds).any()
            else None,
        )
    basis: Literal["simulated", "analytical", "none"]
    if simulated is not None:
        basis, cap_value, cap_speed = "simulated", simulated.value_veh_h_lane, simulated.speed_ms
    elif population.model == "IDM":
        basis, cap_value, cap_speed = "analytical", pop_cap[0], pop_cap[1]
        notes.append(
            "no simulated capacity for this population: the analytical equilibrium capacity is "
            "compared — an index (SUMO's simulated four-lane capacity was 0.86–0.91 of the "
            "mean-driver closed form for the I-24 population, "
            "artifacts/idm_i24_capacity_equilibrium.json); verify with scripts/calibrate_capacity.py"
        )
    else:
        basis, cap_value, cap_speed = "none", None, None
        notes.append(
            f"no simulated capacity for this population under {population.model}, and its "
            "analytical value is not the fleet's capacity (SUMO's EIDM carried about 11 % less "
            "than plain IDM on the same population, docs/ONBOARDING_MNDOT.md §10): run "
            "scripts/calibrate_capacity.py for it"
        )
    side = ModelSide(
        population=population.label,
        population_sources=dict(population.sources),
        model=population.model,
        n_draws=n_draws,
        draw_seed=seed,
        speed_limit_ms=speed_limit_ms,
        speed_factor=population.speed_factor,
        heavy_fraction=population.heavy_fraction,
        passenger_means=means,
        desired_speed=desired,
        free_flow_flow_veh_h_lane=free_flow_flow_veh_h_lane,
        free_flow_speed_ms=ff,
        n_beyond_capacity=beyond,
        capacity_mean_driver=mean_cap,
        capacity_population=pop_cap,
        capacity_simulated=simulated,
        capacity_basis=basis,
        capacity_veh_h_lane=cap_value,
        capacity_speed_ms=cap_speed,
        sidecars=tuple(candidates),
        notes=tuple(notes),
    )
    return side, ev, chosen


# ---------------------------------------------------------------------------
# Comparison and recommendations
# ---------------------------------------------------------------------------


UncertaintyBasis = Literal[
    "observed_interval", "measured_range_fallback", "analytical_index_fallback"
]
"""Where a knob's uncertainty range comes from (:class:`UncertaintyRange`);
anything but ``observed_interval`` is the measured range, labelled assumed."""

CONFIGURED_KNOB: Final[float] = 1.0
"""The configured (calibrated) value of every knob: the factor 1.0 on the
checked population's own mean (a calibration's adjustment is a derived
population, checked again at its own 1.0)."""


@dataclass(frozen=True)
class UncertaintyRange:
    """A knob's range for the uncertainty runs (module docstring, WP-106b).

    Attributes:
        knob: ``t_scale`` or ``v0_scale``.
        parameter: The passenger mean it multiplies (``T``, ``v0``).
        reference_mean: That mean at knob 1.0 (the checked population's).
        low: Lower end, as a factor on ``reference_mean``.
        high: Upper end.
        basis: ``observed_interval`` (the knob values whose model value stays
            inside the observed interval, widened to the configured value),
            ``measured_range_fallback`` (the knob's measured range, because
            there was no interval to read) or ``analytical_index_fallback``
            (the measured range, because capacity came from the analytical
            index, not a simulated capacity).
        reason: Why, in words.
        clipped: An end was set by the span the curve may be read over (the
            measured range, or that ∩ a simulated grid) rather than by a
            crossing: the observed interval reaches beyond it.
        measured_range: The knob's measured range (factors) — the clip and
            the fallback.
        observed_interval: The interval read (the point estimate included,
            as :func:`judge_relative` widens it), when one was.
        curve: ``analytical``/``simulated`` when read off a curve.
        configured: The configured (calibrated) knob value
            (:data:`CONFIGURED_KNOB`).
        widened_to_configured: An end was moved to include ``configured``
            (protocol §8.5: widened to the configured value, then clipped to
            the measured range).
    """

    knob: str
    parameter: str
    reference_mean: float
    low: float
    high: float
    basis: UncertaintyBasis
    reason: str
    clipped: bool
    measured_range: tuple[float, float]
    observed_interval: tuple[float, float] | None = None
    curve: str | None = None
    configured: float = CONFIGURED_KNOB
    widened_to_configured: bool = False

    @property
    def assumed(self) -> bool:
        """The range is the measured range, not read off an observed interval."""
        return self.basis != "observed_interval"

    def to_dict(self) -> dict[str, Any]:
        """JSON form (factors and the parameter's own values)."""
        return {
            "knob": self.knob,
            "parameter": self.parameter,
            "reference_mean": _num(self.reference_mean),
            "low": _num(self.low),
            "high": _num(self.high),
            "parameter_low": _num(self.low * self.reference_mean),
            "parameter_high": _num(self.high * self.reference_mean),
            "basis": self.basis,
            "reason": self.reason,
            "clipped": self.clipped,
            "measured_range": [_num(self.measured_range[0]), _num(self.measured_range[1])],
            "observed_interval": None
            if self.observed_interval is None
            else [_num(self.observed_interval[0]), _num(self.observed_interval[1])],
            "curve": self.curve,
            "configured": _num(self.configured),
            "widened_to_configured": self.widened_to_configured,
            "assumed": self.assumed,
        }


@dataclass(frozen=True)
class Comparison:
    """One quantity, observed against model.

    Attributes:
        quantity: ``free_flow_speed``, ``capacity_per_lane`` or
            ``truck_share``.
        unit: Unit of the values.
        observed: Observed value.
        observed_interval: Its interval.
        model: Model value.
        model_basis: How the model value was obtained.
        difference: ``observed/model − 1`` (relative quantities) or
            ``observed − model`` (truck share): how the corridor differs from
            the model.
        tolerance: The tolerance.
        rule: The rule, in words.
        verdict: ``ok``/``mismatch``/``inconclusive``/``not_available``.
        explanation: Why, in words.
        uncertainty_range: The controlling knob's range for the uncertainty
            runs (free-flow speed and capacity; None for the truck share).
    """

    quantity: str
    unit: str
    observed: float | None
    observed_interval: Interval | None
    model: float | None
    model_basis: str
    difference: float | None
    tolerance: float
    rule: str
    verdict: Verdict
    explanation: str
    uncertainty_range: UncertaintyRange | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON form (``uncertainty_range`` only where there is one)."""
        out: dict[str, Any] = {
            "quantity": self.quantity,
            "unit": self.unit,
            "observed": _num(self.observed),
            "observed_interval": None
            if self.observed_interval is None
            else self.observed_interval.to_dict(),
            "model": _num(self.model),
            "model_basis": self.model_basis,
            "difference": _num(self.difference),
            "tolerance": self.tolerance,
            "rule": self.rule,
            "verdict": self.verdict,
            "explanation": self.explanation,
        }
        if self.uncertainty_range is not None:
            out["uncertainty_range"] = self.uncertainty_range.to_dict()
        return out


def judge_relative(
    observed: float | None,
    interval: Interval | None,
    model: float | None,
    tolerance: float,
    *,
    lower_bound: bool = False,
) -> tuple[Verdict, str]:
    """The verdict rule for a relative quantity (module docstring).

    ``ok`` when ``|model/observed − 1| ≤ tolerance``; ``mismatch`` when the
    model is outside ``[lo·(1 − tol), hi·(1 + tol)]`` (``lo``/``hi`` the
    interval, or the point without one); ``inconclusive`` otherwise. With
    ``lower_bound`` only a model below ``lo·(1 − tol)`` is a mismatch and
    anything else is inconclusive.
    """
    if observed is None or model is None or not observed > 0.0:
        return "not_available", "no observed value" if observed is None else "no model value"
    lo, hi = (interval.lo, interval.hi) if interval is not None else (observed, observed)
    lo, hi = min(lo, observed), max(hi, observed)
    if lower_bound:
        if model < lo * (1.0 - tolerance):
            return "mismatch", (
                f"the model ({model:.4g}) is below the lower bound the road demonstrably "
                f"carried ({lo:.4g}) by more than {tolerance:.0%}"
            )
        return "inconclusive", (
            "only a lower bound was observed (the road never reached capacity); the model is "
            "consistent with it but cannot be confirmed"
        )
    rel = model / observed - 1.0
    where = f"the model is {abs(rel):.1%} {'above' if rel > 0 else 'below'} the observed value"
    if abs(rel) <= tolerance:
        return "ok", f"{where}, within {tolerance:.0%}"
    if model < lo * (1.0 - tolerance) or model > hi * (1.0 + tolerance):
        return "mismatch", f"{where}, and more than {tolerance:.0%} outside its whole interval"
    return "inconclusive", (
        f"{where}: beyond {tolerance:.0%} of the observed value but within {tolerance:.0%} of "
        "its interval, so the data cannot resolve the difference"
    )


def judge_absolute(
    observed: float | None, interval: Interval | None, model: float | None, tolerance: float
) -> tuple[Verdict, str]:
    """The same rule for an absolute quantity (truck share, in points)."""
    if observed is None or model is None:
        return "not_available", "no observed value" if observed is None else "no model value"
    lo, hi = (interval.lo, interval.hi) if interval is not None else (observed, observed)
    lo, hi = min(lo, observed), max(hi, observed)
    diff = model - observed
    where = (
        f"the model's share is {abs(diff) * 100:.1f} points "
        f"{'above' if diff > 0 else 'below'} the observed share"
    )
    if abs(diff) <= tolerance:
        return "ok", f"{where}, within {tolerance * 100:.0f} points"
    if model < lo - tolerance or model > hi + tolerance:
        return "mismatch", (
            f"{where}, and more than {tolerance * 100:.0f} points outside its whole interval"
        )
    return "inconclusive", (
        f"{where}: within {tolerance * 100:.0f} points of its interval, so the data cannot "
        "resolve it"
    )


@dataclass(frozen=True)
class Knob:
    """A corridor-wide setting that could close a mismatch.

    Attributes:
        name: ``heavy_fraction``, ``v0_scale``, ``speed_factor``, ``t_scale``.
        label: In words.
        current: Its value now.
        needed: The value that would close the gap (None when no value in
            the evaluated span does).
        range_lo: Lower end of its measured range.
        range_hi: Upper end.
        range_source: Where the range comes from.
        fits: The needed value lies inside the range.
        available: Settable in a scenario today without a code change.
        how: How it would be applied.
        note: Anything else.
    """

    name: str
    label: str
    current: float
    needed: float | None
    range_lo: float
    range_hi: float
    range_source: str
    fits: bool
    available: bool
    how: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "name": self.name,
            "label": self.label,
            "current": _num(self.current),
            "needed": _num(self.needed),
            "range": [_num(self.range_lo), _num(self.range_hi)],
            "range_source": self.range_source,
            "fits": self.fits,
            "available": self.available,
            "how": self.how,
            "note": self.note,
        }


Action = Literal["none", "adjust", "needs_engine_change", "cannot_match", "no_data"]

CANNOT_MATCH: Final[str] = "The population cannot match this corridor inside its measured ranges."
"""The sentence the report uses when no knob fits (plain words, by design)."""


@dataclass(frozen=True)
class Recommendation:
    """What to do about one quantity (a recommendation; a person decides).

    Attributes:
        quantity: The quantity.
        action: ``none`` (ok or unresolved), ``adjust`` (a knob fits),
            ``needs_engine_change`` (only a knob the engine lacks fits),
            ``cannot_match`` (nothing fits), ``no_data``.
        knobs: The knobs evaluated.
        chosen: The knob recommended, if any.
        text: The recommendation in words.
    """

    quantity: str
    action: Action
    knobs: tuple[Knob, ...]
    chosen: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "quantity": self.quantity,
            "action": self.action,
            "knobs": [k.to_dict() for k in self.knobs],
            "chosen": self.chosen,
            "text": self.text,
        }


def solve_on_curve(
    xs: Sequence[float], ys: Sequence[float], target: float, x0: float
) -> float | None:
    """Where the piecewise-linear ``y(x)`` meets ``target``: the crossing
    nearest ``x0`` (the smallest change), None when the curve never meets it."""
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    best: float | None = None
    for i in range(len(x)):
        if math.isfinite(y[i]) and math.isclose(y[i], target, rel_tol=1e-12, abs_tol=1e-12):
            cand = float(x[i])
            if best is None or abs(cand - x0) < abs(best - x0):
                best = cand
    for i in range(len(x) - 1):
        y1, y2 = y[i], y[i + 1]
        if not (math.isfinite(y1) and math.isfinite(y2)):
            continue
        if (y1 - target) * (y2 - target) < 0.0:
            cand = float(x[i] + (target - y1) * (x[i + 1] - x[i]) / (y2 - y1))
            if best is None or abs(cand - x0) < abs(best - x0):
                best = cand
    return best


def measured_range(
    population: Population, parameter: str, sigmas: float, reference: Population | None = None
) -> tuple[float, float, str]:
    """``mean ± sigmas·sd`` of the measured reference ∩ CLAUDE.md §3.1 range.

    The reference is the measured population this one derives from (a
    T-scaled capacity population's source) when known, else itself.
    """
    ref = reference or population
    mean = ref.passenger_means()[parameter]
    sd = ref.passenger_sd()[parameter]
    lo, hi = mean - sigmas * sd, mean + sigmas * sd
    cal_lo, cal_hi = IDM_RANGES[parameter]
    lo, hi = max(lo, cal_lo), min(hi, cal_hi)
    measured = "measured" if ref.calibration is not None else "configured (not measured)"
    source = (
        f"{measured} population {ref.label}: {parameter} mean {mean:.3g} ± {sigmas:g} sd "
        f"({sd:.3g}), within CLAUDE.md §3.1's {cal_lo:g}–{cal_hi:g}"
    )
    return lo, hi, source


def _knob_curve(
    lo: float, hi: float, current: float, evaluate: Callable[[float], float | None]
) -> tuple[list[float], list[float]]:
    """``KNOB_GRID`` points across ``[lo, hi]`` (plus ``current`` when inside)
    and the model value at each (NaN where there is none)."""
    points = set(np.linspace(lo, hi, KNOB_GRID).tolist()) if hi > lo else {lo}
    if lo <= current <= hi:
        points.add(current)
    xs = sorted(float(x) for x in points)
    ys = []
    for x in xs:
        y = evaluate(x)
        ys.append(float("nan") if y is None else float(y))
    return xs, ys


def range_on_curve(
    xs: Sequence[float], ys: Sequence[float], lo: float, hi: float
) -> tuple[float, float] | None:
    """The smallest and largest ``x`` at which the piecewise-linear ``y(x)``
    lies in ``[lo, hi]``, None when it never does.

    The crossings of ``lo`` and ``hi`` are interpolated exactly as
    :func:`solve_on_curve` interpolates (for a monotone curve each is the
    one :func:`solve_on_curve` finds); points with a non-finite ``y`` break
    the curve there. For a curve that leaves and re-enters the band the
    result is the hull of the parts inside it.
    """
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    lo, hi = min(lo, hi), max(lo, hi)

    def on_edge(v: float, edge: float) -> bool:
        return math.isclose(v, edge, rel_tol=1e-12, abs_tol=1e-12)

    found: list[float] = []
    for i in range(len(x)):
        yi = float(y[i])
        if math.isfinite(yi) and (lo <= yi <= hi or on_edge(yi, lo) or on_edge(yi, hi)):
            found.append(float(x[i]))
    for i in range(len(x) - 1):
        y1, y2 = float(y[i]), float(y[i + 1])
        if not (math.isfinite(y1) and math.isfinite(y2)):
            continue
        for edge in (lo, hi):
            if (y1 - edge) * (y2 - edge) < 0.0:
                found.append(float(x[i] + (edge - y1) * (x[i + 1] - x[i]) / (y2 - y1)))
    return (min(found), max(found)) if found else None


_KNOB_WORDS: Final[dict[str, str]] = {
    "t_scale": "mean time headway (T)",
    "v0_scale": "mean desired speed (v0)",
}

_QUANTITY_WORDS: Final[dict[str, str]] = {
    "free_flow_speed": "free-flow speed",
    "capacity_per_lane": "capacity per lane",
}


def uncertainty_range(
    *,
    knob: str,
    parameter: str,
    reference_mean: float,
    comparison: Comparison,
    measured: tuple[float, float],
    curve: Callable[[], tuple[Sequence[float], Sequence[float]]] | None,
    curve_kind: str | None,
    span: tuple[float, float] | None = None,
    span_text: str = "the measured range",
    lower_bound: bool = False,
    min_days: int = MIN_DAYS_FOR_INTERVAL,
    configured: float = CONFIGURED_KNOB,
    analytical_index: bool = False,
) -> UncertaintyRange:
    """A knob's range for the uncertainty runs (module docstring, WP-106b).

    Args:
        knob: ``t_scale`` / ``v0_scale``.
        parameter: The mean it multiplies (``T`` / ``v0``).
        reference_mean: That mean at knob 1.0.
        comparison: The comparison of the quantity the knob controls.
        measured: The knob's measured range (factors): the clip, and the
            range used when there is nothing to read.
        curve: Returns the model curve ``(knob values, model values)`` —
            the one the recommendation reads; called only when an interval
            can be read. None when there is no model curve.
        curve_kind: ``analytical``/``simulated``.
        span: Where the curve may be read (default ``measured``; with a
            simulated grid, ``measured`` ∩ the grid — never extrapolated).
        span_text: ``span`` in words.
        lower_bound: Only a lower bound of the quantity was observed.
        min_days: Fewest days of an interval (the rules' value).
        configured: The configured (calibrated) knob value; the range read
            off the curve is widened to include it, then clipped to
            ``measured``.
        analytical_index: The curve is the analytical capacity index, not a
            simulated capacity: the measured range is used, basis
            ``analytical_index_fallback`` (module docstring).

    Returns:
        The range: ``observed_interval`` when the curve could be read,
        else ``measured_range_fallback`` / ``analytical_index_fallback`` with
        the reason.
    """
    c = comparison
    what = _KNOB_WORDS.get(knob, knob)
    quantity = _QUANTITY_WORDS.get(c.quantity, c.quantity.replace("_", " "))

    def fallback(
        reason: str, basis: UncertaintyBasis = "measured_range_fallback"
    ) -> UncertaintyRange:
        return UncertaintyRange(
            knob=knob,
            parameter=parameter,
            reference_mean=reference_mean,
            low=measured[0],
            high=measured[1],
            basis=basis,
            reason=reason,
            clipped=False,
            measured_range=measured,
            configured=configured,
        )

    if c.observed is None:
        return fallback(f"{quantity} was not observed ({c.explanation})")
    if lower_bound:
        return fallback(
            "only a lower bound of the capacity was observed (no station reached congestion), "
            "so its interval bounds what the road carried, not what it can carry"
        )
    if c.model is None or curve is None:
        return fallback(f"the model has no {quantity} value here ({c.explanation})")
    if c.observed_interval is None:
        return fallback(f"the {quantity} has no 95 % interval (fewer than {min_days} days of data)")
    if analytical_index:
        return fallback(
            f"the model's {quantity} comes from the analytical equilibrium index, not a "
            "simulated capacity (no accepted capacity sidecar for this population): the index "
            "is not the fleet's capacity, so the observed interval read off it would not say "
            "which values of the "
            f"{what} keep the simulated model inside it; run scripts/calibrate_capacity.py for "
            "this population",
            "analytical_index_fallback",
        )
    s_lo, s_hi = span if span is not None else measured
    if not s_lo < s_hi:
        return fallback(f"the span the curve may be read over ({span_text}) is empty")
    iv_lo = min(c.observed_interval.lo, c.observed)
    iv_hi = max(c.observed_interval.hi, c.observed)
    xs, ys = curve()
    pts = sorted(
        (float(x), float(y)) for x, y in zip(xs, ys, strict=True) if math.isfinite(float(y))
    )
    hull = range_on_curve(xs, ys, iv_lo, iv_hi)
    low, high = (max(hull[0], s_lo), min(hull[1], s_hi)) if hull is not None else (1.0, 0.0)
    if hull is None or not low < high or not pts:
        inside = [y for x, y in pts if s_lo - 1e-12 <= x <= s_hi + 1e-12]
        spans = (
            f"; over it the model's {quantity} spans {min(inside):.4g}–{max(inside):.4g} {c.unit}"
            if inside
            else ""
        )
        return fallback(
            f"no value of the {what} in {span_text} keeps the model's {quantity} inside the "
            f"observed interval {iv_lo:.4g}–{iv_hi:.4g} {c.unit}{spans}"
        )

    def strictly_inside(v: float) -> bool:
        return (
            iv_lo < v < iv_hi
            and not math.isclose(v, iv_lo, rel_tol=1e-9)
            and not math.isclose(v, iv_hi, rel_tol=1e-9)
        )

    (x_first, y_first), (x_last, y_last) = pts[0], pts[-1]
    clip_low = hull[0] < s_lo - 1e-12 or (
        math.isclose(hull[0], x_first, rel_tol=0.0, abs_tol=1e-12) and strictly_inside(y_first)
    )
    clip_high = hull[1] > s_hi + 1e-12 or (
        math.isclose(hull[1], x_last, rel_tol=0.0, abs_tol=1e-12) and strictly_inside(y_last)
    )
    ends = [e for e, flag in (("low", clip_low), ("high", clip_high)) if flag]
    reason = (
        f"the {what} values whose model {quantity} stays inside the observed "
        f"{c.observed_interval.level * 100:g} % interval {iv_lo:.4g}–{iv_hi:.4g} {c.unit}, read off "
        f"the {curve_kind} curve over {span_text} ({s_lo:.4g}–{s_hi:.4g})"
    )
    if ends:
        reason += (
            f"; clipped at the {' and '.join(ends)} end{'s' if len(ends) > 1 else ''} to "
            f"{span_text}, which the interval reaches beyond"
        )
    if c.verdict == "inconclusive":
        reason += (
            "; the verdict is inconclusive (the data cannot resolve the difference), and the "
            "interval is read all the same"
        )
    # protocol §8.5: widened to include the configured value, then clipped to
    # the measured range
    m_lo, m_hi = measured
    w_lo = max(min(low, configured), m_lo)
    w_hi = min(max(high, configured), m_hi)
    widened = w_lo < low - 1e-12 or w_hi > high + 1e-12
    if widened:
        reason += (
            f"; widened to include the configured value × {configured:.4g} "
            f"({parameter} {configured * reference_mean:.4g}), clipped to the measured range "
            f"({m_lo:.4g}–{m_hi:.4g})"
        )
    if not m_lo - 1e-12 <= configured <= m_hi + 1e-12:
        reason += (
            f"; the configured value × {configured:.4g} lies outside the measured range "
            f"{m_lo:.4g}–{m_hi:.4g}, so the range cannot include it"
        )
    return UncertaintyRange(
        knob=knob,
        parameter=parameter,
        reference_mean=reference_mean,
        low=w_lo,
        high=w_hi,
        basis="observed_interval",
        reason=reason,
        clipped=bool(ends),
        measured_range=measured,
        observed_interval=(iv_lo, iv_hi),
        curve=curve_kind,
        configured=configured,
        widened_to_configured=widened,
    )


def _choose(quantity: str, knobs: list[Knob], *, unit_text: str) -> Recommendation:
    for k in knobs:
        if k.fits and k.available:
            return Recommendation(
                quantity=quantity,
                action="adjust",
                knobs=tuple(knobs),
                chosen=k.name,
                text=(
                    f"Set {k.label} to {k.needed:.4g} (now {k.current:.4g}; measured range "
                    f"{k.range_lo:.4g}–{k.range_hi:.4g}) corridor-wide: {k.how}. This is a "
                    "recommendation — a reviewer decides and records the decision."
                ),
            )
    for k in knobs:
        if k.fits and not k.available:
            return Recommendation(
                quantity=quantity,
                action="needs_engine_change",
                knobs=tuple(knobs),
                chosen=k.name,
                text=(
                    f"Only {k.label} closes the {unit_text} gap inside its measured range "
                    f"({k.needed:.4g}, range {k.range_lo:.4g}–{k.range_hi:.4g}), and the engine "
                    f"does not expose it today: {k.how}."
                ),
            )
    return Recommendation(
        quantity=quantity,
        action="cannot_match",
        knobs=tuple(knobs),
        chosen=None,
        text=f"{CANNOT_MATCH} No knob closes the {unit_text} gap within its measured range.",
    )


@dataclass(frozen=True)
class TransferCheckReport:
    """The full check: observed, model, verdicts and recommendations.

    Attributes:
        observed: :class:`ObservedSide`.
        model: :class:`ModelSide`.
        comparisons: One per quantity.
        recommendations: One per quantity, in the order evaluated.
        implied_headway: Diagnostic: time headway at capacity, observed and
            model, gross (``3600/q``) and net of the jam spacing
            (``3600/q − (s0 + L)/v``).
        provenance: Inputs' hashes, the code, the arguments (from the caller).
        notes: Plain statements.
        schema: :data:`TRANSFER_SCHEMA`.
    """

    observed: ObservedSide
    model: ModelSide
    comparisons: tuple[Comparison, ...]
    recommendations: tuple[Recommendation, ...]
    implied_headway: dict[str, float | None]
    provenance: dict[str, Any]
    notes: tuple[str, ...]
    schema: str = TRANSFER_SCHEMA

    def comparison(self, quantity: str) -> Comparison:
        """The comparison of one quantity.

        Raises:
            KeyError: No such quantity.
        """
        for c in self.comparisons:
            if c.quantity == quantity:
                return c
        raise KeyError(quantity)

    def recommendation(self, quantity: str) -> Recommendation:
        """The recommendation for one quantity.

        Raises:
            KeyError: No such quantity.
        """
        for r in self.recommendations:
            if r.quantity == quantity:
                return r
        raise KeyError(quantity)

    def to_dict(self) -> dict[str, Any]:
        """The JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "rules": self.observed.rules.to_dict(),
            "rule_sources": dict(RULE_SOURCES),
            "observed": self.observed.to_dict(),
            "model": self.model.to_dict(),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "implied_headway": {k: _num(v) for k, v in self.implied_headway.items()},
            "provenance": dict(self.provenance),
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """The JSON text (sorted keys, NaN written as ``null``)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False)

    def to_markdown(self, *, title: str = "Do our driver settings fit this corridor?") -> str:
        """A plain-language summary for a client."""
        return render_markdown(self, title=title)


def model_speed_limit(population: Population, posted_limit_ms: float | None) -> float | None:
    """The limit that caps the model's desired speeds on this corridor.

    The posted limit, except for a scenario on a generated road
    (:data:`GENERATED_EDGE_SPEED_MS`), whose drivers no limit caps.
    """
    if population.network_kind in ("corridor", "ring"):
        return None
    return posted_limit_ms


def check_transfer(
    observed: ObservedSide,
    population: Population,
    *,
    sidecars: Sequence[str | Path] | None = None,
    explicit_sidecars: bool = False,
    n_draws: int = MODEL_DRAWS,
    seed: int = MODEL_DRAW_SEED,
    provenance: Mapping[str, Any] | None = None,
) -> TransferCheckReport:
    """Compare a population with a corridor and recommend (module docstring).

    Args:
        observed: :func:`observe`'s result.
        population: The population (:func:`population_from_scenario` /
            :func:`population_from_artifact`).
        sidecars: Capacity sidecars to consider (default: ``artifacts/``).
        explicit_sidecars: The sidecars were named by the user.
        n_draws: Drivers drawn for the analytical values.
        seed: Draw seed.
        provenance: Recorded as given (inputs' hashes, code, arguments).

    Returns:
        The :class:`TransferCheckReport`.
    """
    th = observed.rules
    model_limit = model_speed_limit(population, observed.speed_limit_ms)
    model, ev, sidecar = model_side(
        population,
        speed_limit_ms=model_limit,
        free_flow_flow_veh_h_lane=observed.free_flow.flow_median_veh_h_lane,
        corridor_lanes=observed.median_lanes,
        sidecars=sidecars,
        explicit_sidecars=explicit_sidecars,
        n_draws=n_draws,
        seed=seed,
    )
    notes: list[str] = []
    comparisons: list[Comparison] = []

    # --- truck share
    heavy_obs = observed.heavy
    v, why = judge_absolute(
        heavy_obs.share if heavy_obs.available else None,
        heavy_obs.interval,
        model.heavy_fraction,
        th.heavy_share_tolerance,
    )
    if not heavy_obs.available:
        why = f"truck share not available: {heavy_obs.reason}"
    comparisons.append(
        Comparison(
            quantity="truck_share",
            unit="fraction",
            observed=heavy_obs.share if heavy_obs.available else None,
            observed_interval=heavy_obs.interval,
            model=model.heavy_fraction,
            model_basis="FleetSpec.heavy.fraction (0 without a heavy block)",
            difference=(heavy_obs.share - model.heavy_fraction)
            if heavy_obs.available and heavy_obs.share is not None
            else None,
            tolerance=th.heavy_share_tolerance,
            rule=f"model within ±{th.heavy_share_tolerance * 100:.0f} points of the observed share",
            verdict=v,
            explanation=why,
        )
    )
    # --- free-flow speed
    ff_obs = observed.free_flow
    v, why = judge_relative(
        ff_obs.median_ms, ff_obs.interval, model.free_flow_speed_ms, th.free_flow_speed_tolerance
    )
    comparisons.append(
        Comparison(
            quantity="free_flow_speed",
            unit="m/s",
            observed=ff_obs.median_ms,
            observed_interval=ff_obs.interval,
            model=model.free_flow_speed_ms,
            model_basis=(
                f"analytical: mean {population.model} steady-state speed of {n_draws} drawn "
                f"drivers at the observed free-flow flow, desired speed min(v0, "
                f"{population.speed_factor:g} × limit)"
            ),
            difference=(ff_obs.median_ms / model.free_flow_speed_ms - 1.0)
            if ff_obs.median_ms and model.free_flow_speed_ms
            else None,
            tolerance=th.free_flow_speed_tolerance,
            rule=(
                f"model within ±{th.free_flow_speed_tolerance:.0%} of the observed median "
                "window-mean speed in light traffic"
            ),
            verdict=v,
            explanation=why,
        )
    )
    # --- capacity
    cap_obs = observed.capacity
    lower = cap_obs.basis == "lower_bound"
    v, why = judge_relative(
        cap_obs.value_veh_h_lane,
        cap_obs.interval,
        model.capacity_veh_h_lane,
        th.capacity_tolerance,
        lower_bound=lower,
    )
    if model.capacity_basis == "simulated" and model.capacity_simulated is not None:
        sim = model.capacity_simulated
        basis_text = (
            f"simulated straight-road capacity ({sim.sidecar.path}, {sim.sidecar.lanes} lanes, "
            f"{sim.raw_veh_h_lane:.0f} veh/h/lane) × {sim.conditions_ratio:.3f} for this "
            "corridor's limit and truck share"
        )
    elif model.capacity_basis == "analytical":
        basis_text = "analytical equilibrium capacity of the drawn population (an index)"
    else:
        basis_text = "none available"
    comparisons.append(
        Comparison(
            quantity="capacity_per_lane",
            unit="veh/h/lane",
            observed=cap_obs.value_veh_h_lane,
            observed_interval=cap_obs.interval,
            model=model.capacity_veh_h_lane,
            model_basis=basis_text,
            difference=(cap_obs.value_veh_h_lane / model.capacity_veh_h_lane - 1.0)
            if cap_obs.value_veh_h_lane and model.capacity_veh_h_lane
            else None,
            tolerance=th.capacity_tolerance,
            rule=(
                f"model within ±{th.capacity_tolerance:.0%} of the observed capacity "
                f"({th.capacity_percentile:g}th percentile of 5-minute per-lane flow at the "
                "stations that reached capacity)"
            ),
            verdict=v,
            explanation=why,
        )
    )

    # --- recommendations, in order, each with the previous applied
    recs: list[Recommendation] = []
    state = Adjustments()
    sigmas = th.measured_range_sigmas
    reference = sidecar.source if sidecar is not None else None
    by_q = {c.quantity: c for c in comparisons}

    # truck share
    c = by_q["truck_share"]
    if c.verdict == "not_available":
        recs.append(
            Recommendation(
                quantity="truck_share",
                action="no_data",
                knobs=(),
                chosen=None,
                text=(
                    f"The truck share is not measured in these data; the model keeps "
                    f"{model.heavy_fraction:.1%}. Ask for classification counts, or test how "
                    "much the results depend on it (docs/FRISCO_PROTOCOL.md §8.5)."
                ),
            )
        )
    elif c.verdict == "mismatch" and c.observed is not None:
        has_heavy = population.fleet.heavy is not None
        knob = Knob(
            name="heavy_fraction",
            label="the truck share (HeavyVehicleSpec.fraction)",
            current=model.heavy_fraction,
            needed=c.observed,
            range_lo=HEAVY_SHARE_RANGE[0],
            range_hi=HEAVY_SHARE_RANGE[1],
            range_source="HeavyVehicleSpec.fraction bounds (the share is the corridor's own measurement)",
            fits=HEAVY_SHARE_RANGE[0] <= c.observed <= HEAVY_SHARE_RANGE[1],
            available=True,
            how=(
                "fleet.heavy.fraction"
                if has_heavy
                else "add a fleet.heavy block on the measured truck population "
                f"({MEASURED_HEAVY_POPULATION}, with its measured length) and set its fraction"
            ),
            note="" if has_heavy else "the fleet has no heavy population",
        )
        rec = _choose("truck_share", [knob], unit_text="truck-share")
        recs.append(rec)
        if rec.action == "adjust" and has_heavy:
            state = replace(state, heavy_fraction=c.observed)
        elif rec.action == "adjust":
            notes.append(
                "the free-flow and capacity recommendations below are computed without trucks: "
                "the fleet has no heavy population to apply the measured share to; recompute "
                "after adding one"
            )
    else:
        recs.append(
            Recommendation(
                quantity="truck_share",
                action="none",
                knobs=(),
                chosen=None,
                text=f"No change: {c.explanation}.",
            )
        )

    # free-flow speed
    ranges: dict[str, UncertaintyRange] = {}
    c = by_q["free_flow_speed"]
    v_lo, v_hi, v_src = measured_range(population, "v0", sigmas, reference)
    mean_v0 = population.passenger_means()["v0"]
    g_lo, g_hi = v_lo / mean_v0, v_hi / mean_v0
    ff_state = state

    def ff_at_g(g: float) -> float | None:
        return ev.ff_speed(replace(ff_state, v0_scale=g))[0]

    def ff_curve() -> tuple[list[float], list[float]]:
        return _knob_curve(g_lo, g_hi, 1.0, ff_at_g)

    ranges["free_flow_speed"] = uncertainty_range(
        knob="v0_scale",
        parameter="v0",
        reference_mean=mean_v0,
        comparison=c,
        measured=(g_lo, g_hi),
        curve=ff_curve,
        curve_kind="analytical",
        min_days=th.min_days_for_interval,
    )
    if c.verdict == "mismatch" and c.observed is not None:
        knobs: list[Knob] = []
        xs, ys = ff_curve()
        needed = solve_on_curve(xs, ys, c.observed, 1.0) if g_lo <= g_hi else None
        knobs.append(
            Knob(
                name="v0_scale",
                label="the passenger population's mean desired speed (factor on v0)",
                current=1.0,
                needed=needed,
                range_lo=g_lo,
                range_hi=g_hi,
                range_source=v_src,
                fits=needed is not None and g_lo - 1e-9 <= needed <= g_hi + 1e-9,
                available=True,
                how=(
                    "a derived population artifact with mean v0 multiplied by the factor, "
                    "covariance unchanged (as scripts/calibrate_capacity.py derives mean T)"
                ),
                note=(
                    ""
                    if needed is not None
                    else f"over its range the model's free-flow speed spans "
                    f"{np.nanmin(ys):.2f}–{np.nanmax(ys):.2f} m/s and never reaches "
                    f"{c.observed:.2f} m/s"
                ),
            )
        )
        if model_limit is not None:
            limit = model_limit
            sf_now = population.speed_factor
            # the factor × limit inside the measured desired-speed range, and
            # inside the factor's own bounds (SUMO's cut-offs, FleetSpec)
            s_lo = max(v_lo / limit, SPEED_FACTOR_BOUNDS[0])
            s_hi = min(v_hi / limit, SPEED_FACTOR_BOUNDS[1])

            def ff_at_s(s: float) -> float | None:
                return ev.ff_speed(replace(state, speed_factor=s))[0]

            xs, ys = _knob_curve(min(s_lo, sf_now), max(s_hi, s_lo, sf_now), sf_now, ff_at_s)
            needed_s = solve_on_curve(xs, ys, c.observed, sf_now)
            knobs.append(
                Knob(
                    name="speed_factor",
                    label="the speed factor on the posted limit (SUMO speedFactor)",
                    current=sf_now,
                    needed=needed_s,
                    range_lo=s_lo,
                    range_hi=s_hi,
                    range_source=(
                        f"factor × posted limit ({limit / MPH_TO_MS:.0f} mph) inside the measured "
                        f"desired-speed range {v_lo:.1f}–{v_hi:.1f} m/s, and inside SUMO's "
                        f"speed-factor cut-offs {SPEED_FACTOR_BOUNDS[0]:g}–"
                        f"{SPEED_FACTOR_BOUNDS[1]:g}"
                    ),
                    fits=needed_s is not None and s_lo - 1e-9 <= needed_s <= s_hi + 1e-9,
                    available=ENGINE_HAS_SPEED_FACTOR,
                    how=(
                        "fleet.speed_factor in the scenario (FleetSpec.speed_factor, WP-109): "
                        "written as SUMO's speedFactor on every passenger vehicle, whose desired "
                        "speed becomes min(v0, factor × the posted limit); heavy vehicles keep "
                        "1.0, fleet.speed_dev stays as configured, and a downstream boundary "
                        "schedule is posted divided by the factor so its measured speeds are "
                        "still the speeds driven"
                        if ENGINE_HAS_SPEED_FACTOR
                        else "microsim.vehicles writes speedFactor=1.0 on every vType and "
                        "FleetSpec has no field for it — a code change, reviewed like any other"
                    ),
                    note="drivers here exceed the posted limit, which caps every model driver"
                    if c.observed > limit and sf_now <= ENGINE_SPEED_FACTOR
                    else "",
                )
            )
        rec = _choose("free_flow_speed", knobs, unit_text="free-flow speed")
        recs.append(rec)
        if rec.action == "adjust" and rec.chosen == "v0_scale":
            chosen = next(k for k in knobs if k.name == "v0_scale")
            state = replace(state, v0_scale=float(chosen.needed or 1.0))
        elif rec.action == "adjust" and rec.chosen == "speed_factor":
            chosen = next(k for k in knobs if k.name == "speed_factor")
            if chosen.needed is not None:
                state = replace(state, speed_factor=float(chosen.needed))
    else:
        recs.append(_no_change("free_flow_speed", c))

    # capacity
    c = by_q["capacity_per_lane"]
    t_lo, t_hi, t_src = measured_range(population, "T", sigmas, reference)
    mean_t = population.passenger_means()["T"]
    k_lo, k_hi = t_lo / mean_t, t_hi / mean_t
    cap_state = state
    cap_curve: Callable[[], tuple[list[float], list[float]]] | None = None
    cap_span: tuple[float, float] | None = None
    cap_span_text = "the measured range"
    if model.capacity_basis == "simulated" and sidecar is not None:
        sim_sidecar = sidecar
        f_cur_sim = sidecar.candidate.t_scale_current or 1.0

        def simulated_curve() -> tuple[list[float], list[float]]:
            xs_s = [float(f / f_cur_sim) for f in sim_sidecar.scales]
            ys_s = [
                _simulated_at(ev, sim_sidecar, cap_state, float(f))[2] for f in sim_sidecar.scales
            ]
            return xs_s, ys_s

        cap_curve = simulated_curve
        grid = [float(f / f_cur_sim) for f in sidecar.scales]
        cap_span = (max(k_lo, min(grid)), min(k_hi, max(grid)))
        cap_span_text = "the measured range ∩ the simulated grid (not extrapolated)"
    elif model.capacity_basis == "analytical":

        def cap_at(k: float) -> float | None:
            return ev.capacity(replace(cap_state, t_scale=k))[0]

        def analytical_curve() -> tuple[list[float], list[float]]:
            return _knob_curve(k_lo, k_hi, 1.0, cap_at)

        cap_curve = analytical_curve
    ranges["capacity_per_lane"] = uncertainty_range(
        knob="t_scale",
        parameter="T",
        reference_mean=mean_t,
        comparison=c,
        measured=(k_lo, k_hi),
        curve=cap_curve,
        curve_kind=model.capacity_basis if cap_curve is not None else None,
        span=cap_span,
        span_text=cap_span_text,
        lower_bound=lower,
        min_days=th.min_days_for_interval,
        analytical_index=model.capacity_basis == "analytical",
    )
    if c.verdict == "mismatch" and c.observed is not None:
        target = c.observed_interval.lo if lower and c.observed_interval else c.observed
        if model.capacity_basis == "simulated" and sidecar is not None and cap_curve is not None:
            xs, ys = cap_curve()
            needed = solve_on_curve(xs, ys, target, 1.0)
            grid_lo, grid_hi = min(xs), max(xs)
            fits = (
                needed is not None
                and max(k_lo, grid_lo) - 1e-9 <= needed <= min(k_hi, grid_hi) + 1e-9
            )
            note = (
                f"read off the simulated grid ({sidecar.candidate.path}: T × "
                f"{grid_lo:.3g}–{grid_hi:.3g} of this population, adjusted to this corridor); "
                + (
                    f"the simulated capacity spans {np.nanmin(ys):.0f}–{np.nanmax(ys):.0f} "
                    f"veh/h/lane there and never reaches {target:.0f}"
                    if needed is None
                    else "not extrapolated beyond the grid"
                )
            )
            range_lo, range_hi = max(k_lo, grid_lo), min(k_hi, grid_hi)
            range_src = f"{t_src}; and the sidecar's simulated grid"
            available = True
        elif model.capacity_basis == "analytical" and cap_curve is not None:
            xs, ys = cap_curve()
            needed = solve_on_curve(xs, ys, target, 1.0)
            fits = needed is not None and k_lo - 1e-9 <= needed <= k_hi + 1e-9
            note = (
                "read off the analytical capacity curve (an index): confirm by simulation with "
                "scripts/calibrate_capacity.py before adopting"
                + (
                    ""
                    if needed is not None
                    else f"; over the range it spans {np.nanmin(ys):.0f}–{np.nanmax(ys):.0f} "
                    f"veh/h/lane and never reaches {target:.0f}"
                )
            )
            range_lo, range_hi, range_src = k_lo, k_hi, t_src
            available = True
        else:
            needed, fits, note = None, False, "no model capacity value for this fleet"
            range_lo, range_hi, range_src = k_lo, k_hi, t_src
            available = True
        knob = Knob(
            name="t_scale",
            label="the passenger population's mean time headway (factor on T)",
            current=1.0,
            needed=needed,
            range_lo=range_lo,
            range_hi=range_hi,
            range_source=range_src,
            fits=bool(fits),
            available=available,
            how=(
                "scripts/calibrate_capacity.py's T-scaling: a derived population artifact with "
                "mean T multiplied by the factor, covariance unchanged"
            ),
            note=note,
        )
        recs.append(_choose("capacity_per_lane", [knob], unit_text="capacity"))
    elif c.verdict == "not_available" and model.capacity_basis == "none" and c.observed is not None:
        recs.append(
            Recommendation(
                quantity="capacity_per_lane",
                action="no_data",
                knobs=(),
                chosen=None,
                text=(
                    f"No model capacity for this {population.model} fleet: run "
                    "scripts/calibrate_capacity.py for the population under its own model, "
                    "then rerun this check."
                ),
            )
        )
    else:
        recs.append(_no_change("capacity_per_lane", c))

    adjusted = [r for r in recs if r.action in ("adjust", "needs_engine_change")]
    if len(adjusted) > 1:
        notes.append(
            "more than one quantity calls for an adjustment, while docs/FRISCO_PROTOCOL.md §7.2 "
            "allows a single corridor-wide adjustment: the reviewer decides which (if any) is "
            "applied and records why"
        )
    # implied headway (diagnostic)
    s0_mean = population.passenger_means()["s0"]
    jam = s0_mean + PASSENGER_LENGTH_M
    obs_q, obs_v = cap_obs.value_veh_h_lane, cap_obs.speed_at_capacity_ms
    mod_q, mod_v = model.capacity_veh_h_lane, model.capacity_speed_ms
    headway = {
        "observed_gross_s": (3600.0 / obs_q) if obs_q else None,
        "observed_net_s": (3600.0 / obs_q - jam / obs_v) if obs_q and obs_v else None,
        "model_gross_s": (3600.0 / mod_q) if mod_q else None,
        "model_net_s": (3600.0 / mod_q - jam / mod_v) if mod_q and mod_v else None,
        "population_mean_T_s": population.passenger_means()["T"],
        "jam_spacing_m": jam,
    }
    comparisons = [replace(c, uncertainty_range=ranges.get(c.quantity)) for c in comparisons]
    return TransferCheckReport(
        observed=observed,
        model=model,
        comparisons=tuple(comparisons),
        recommendations=tuple(recs),
        implied_headway=headway,
        provenance=dict(provenance or {}),
        notes=tuple(notes),
    )


def _no_change(quantity: str, c: Comparison) -> Recommendation:
    if c.verdict == "ok":
        text = f"No change: {c.explanation}."
    elif c.verdict == "inconclusive":
        text = f"No change recommended: {c.explanation}."
    else:
        text = f"Nothing to compare: {c.explanation}."
    return Recommendation(
        quantity=quantity,
        action="none" if c.verdict != "not_available" else "no_data",
        knobs=(),
        chosen=None,
        text=text,
    )


# ---------------------------------------------------------------------------
# Plain-language summary
# ---------------------------------------------------------------------------

_VERDICT_WORDS: Final[dict[str, str]] = {
    "ok": "fits",
    "mismatch": "does not fit",
    "inconclusive": "cannot tell",
    "not_available": "not available",
}


def _fmt_value(unit: str, x: float | None) -> str:
    """A comparison value in client units."""
    if x is None:
        return "—"
    if unit == "m/s":
        return f"{ms_to_kmh(x):.1f} km/h"
    if unit == "fraction":
        return _share_text(x)
    return f"{x:,.0f}"


def _cap_words(mod: ModelSide) -> str:
    """What caps the model drivers' desired speeds, in words."""
    if not mod.speed_limit_ms:
        return "nothing (no limit given)"
    if mod.speed_factor == ENGINE_SPEED_FACTOR:
        return "the posted limit"
    return f"{mod.speed_factor:g} × the posted limit (heavy vehicles at the limit)"


def _sec(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f} s"


def _ff_sentence(report: TransferCheckReport) -> str:
    c = report.comparison("free_flow_speed")
    ff = report.observed.free_flow
    if c.observed is None or c.model is None:
        return f"**Free-flow speed:** {c.explanation}."
    diff = c.observed / c.model - 1.0
    word = "faster" if diff > 0 else "slower"
    limit = report.observed.speed_limit_ms
    text = (
        f"**Free-flow speed:** drivers here drive about {abs(diff):.0%} {word} than the "
        f"population we measured would on this road: {_kmh(c.observed)} observed (median of "
        f"{ff.n_windows:,} five-minute readings in light traffic at {ff.n_stations} station(s)"
    )
    if c.observed_interval is not None:
        text += (
            f"; 95 % interval {ms_to_kmh(c.observed_interval.lo):.1f}–"
            f"{ms_to_kmh(c.observed_interval.hi):.1f} km/h"
        )
    text += f"), {_kmh(c.model)} for the model at the same traffic level."
    if limit is not None:
        sf = report.model.speed_factor
        text += (
            f" The posted limit is {limit / MPH_TO_MS:.0f} mph; observed drivers are "
            f"{c.observed / limit - 1.0:+.0%} relative to it, and "
            + (
                "the model's drivers never exceed it."
                if sf <= ENGINE_SPEED_FACTOR
                else f"the model's passenger drivers want at most {sf:g} times it "
                "(fleet.speed_factor)."
            )
        )
    return text + f" Verdict: **{_VERDICT_WORDS[c.verdict]}** ({c.explanation})."


def _cap_sentence(report: TransferCheckReport) -> str:
    c = report.comparison("capacity_per_lane")
    cap = report.observed.capacity
    if c.observed is None:
        return f"**Capacity per lane:** not observed — {c.explanation}."
    where = {
        "bottleneck_discharge": f"downstream of the active bottleneck(s), at {', '.join(cap.stations)}",
        "congested": f"at the station(s) that reached congestion ({', '.join(cap.stations)})",
        "lower_bound": "nowhere at capacity — this is the most any station carried, a lower bound",
    }.get(cap.basis, "")
    text = f"**Capacity per lane:** the road carried about {c.observed:,.0f} vehicles per hour per lane {where}"
    if c.observed_interval is not None:
        text += f" (95 % interval {c.observed_interval.lo:,.0f}–{c.observed_interval.hi:,.0f})"
    text += "."
    if c.model is not None:
        diff = c.observed / c.model - 1.0
        text += (
            f" The model's drivers carry {c.model:,.0f} ({c.model_basis}): the road carries "
            f"{abs(diff):.0%} {'more' if diff > 0 else 'less'}."
        )
    else:
        text += f" The model has no capacity value here: {c.explanation}."
    return text + f" Verdict: **{_VERDICT_WORDS[c.verdict]}**."


def _heavy_sentence(report: TransferCheckReport) -> str:
    c = report.comparison("truck_share")
    if c.observed is None:
        return (
            f"**Truck share:** not available in these data ({report.observed.heavy.reason}); "
            f"the model assumes {_share_text(c.model)}."
        )
    text = f"**Truck share:** {_share_text(c.observed)} of vehicles here are heavy"
    if report.observed.heavy.definition:
        text += f" ({report.observed.heavy.definition})"
    if c.observed_interval is not None:
        text += f", 95 % interval {_share_text(c.observed_interval.lo)}–{_share_text(c.observed_interval.hi)}"
    return (
        text + f"; the model has {_share_text(c.model)}. Verdict: **{_VERDICT_WORDS[c.verdict]}**."
    )


def _range_sentence(u: UncertaintyRange) -> str:
    """One plain sentence on a knob's uncertainty range."""
    lo, hi = u.low * u.reference_mean, u.high * u.reference_mean
    if u.parameter == "v0":
        values = f"mean desired speed {ms_to_kmh(lo):.1f}–{ms_to_kmh(hi):.1f} km/h"
    else:
        values = f"mean time headway {lo:.2f}–{hi:.2f} s"
    words = _KNOB_WORDS.get(u.knob, u.knob)
    head = f"**{words[:1].upper()}{words[1:]}:** × {u.low:.3g}–{u.high:.3g} ({values})"
    if u.basis == "observed_interval":
        edge = "measured range or simulated grid" if u.curve == "simulated" else "measured range"
        return (
            f"{head}, the values that keep the model inside the observed 95 % interval"
            + (
                f" (cut at the edge of the {edge}, which the interval reaches beyond)"
                if u.clipped
                else ""
            )
            + (
                f", widened to include the configured value (× {u.configured:.3g})"
                if u.widened_to_configured
                else ""
            )
            + "."
        )
    if u.basis == "analytical_index_fallback":
        return (
            f"{head}: the whole measured range, labelled assumed — capacity here comes from the "
            "analytical index, not a simulated capacity, so the observed interval cannot be "
            "read as values of the knob (run scripts/calibrate_capacity.py for this population)."
        )
    return (
        f"{head}: the whole measured range, the spread of individual drivers rather than what is "
        f"unknown about this corridor's population, so the uncertainty runs label it assumed; "
        f"the reason: {u.reason}."
    )


def render_markdown(
    report: TransferCheckReport,
    *,
    title: str = "Do our driver settings fit this corridor?",
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """The plain-language summary (what fits, what does not, what to do).

    Args:
        report: The report.
        title: Heading.
        provenance: Key → value lines printed under the heading.
    """
    obs, mod = report.observed, report.model
    lines = [f"# {title}", ""]
    for key, value in (provenance or {}).items():
        lines.append(f"- **{key}**: {value}")
    if provenance:
        lines.append("")
    lines += [
        "## Summary",
        "",
        f"The driver population **{mod.population}** ({mod.model}) was measured on other "
        f"roads. These are its settings against {len(obs.stations)} detector station(s) of this "
        f"corridor over {len(obs.dates)} day(s), {obs.start_local}–{obs.end_local} local.",
        "",
        f"- {_ff_sentence(report)}",
        f"- {_cap_sentence(report)}",
        f"- {_heavy_sentence(report)}",
        "",
        "| Quantity | Observed (95 % interval) | Model | Difference | Rule | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for c in report.comparisons:
        interval = (
            f" ({_fmt_value(c.unit, c.observed_interval.lo)}–"
            f"{_fmt_value(c.unit, c.observed_interval.hi)})"
            if c.observed_interval
            else ""
        )
        if c.difference is None:
            diff = "—"
        elif c.unit == "fraction":
            diff = f"{c.difference * 100:+.1f} points"
        else:
            diff = f"{c.difference:+.1%}"
        lines.append(
            f"| {c.quantity.replace('_', ' ')} | {_fmt_value(c.unit, c.observed)}{interval} | "
            f"{_fmt_value(c.unit, c.model)} | {diff} | {c.rule} | **{c.verdict}** |"
        )
    lines += [
        "",
        "Differences read as *this corridor relative to the model*. A verdict of *cannot tell* "
        "means the difference is beyond the tolerance of the observed value but not of its "
        "interval: more days would decide it.",
        "",
        "## Recommendations",
        "",
        "These are recommendations. A person decides whether to apply any of them, and the "
        "decision is recorded with the study (docs/FRISCO_PROTOCOL.md §7.2: one of the measured "
        "populations, with a single corridor-wide adjustment inside the measured ranges, never "
        "a per-location setting).",
        "",
    ]
    for r in report.recommendations:
        lines.append(
            f"### {r.quantity.replace('_', ' ').capitalize()}: {r.action.replace('_', ' ')}"
        )
        lines += ["", r.text, ""]
        if r.knobs:
            lines += [
                "| Knob | Now | Needed | Measured range | Fits | In the engine today |",
                "|---|---|---|---|---|---|",
            ]
            for k in r.knobs:
                needed = "never reached" if k.needed is None else f"{k.needed:.4g}"
                lines.append(
                    f"| {k.label} | {k.current:.4g} | {needed} | {k.range_lo:.4g}–{k.range_hi:.4g} "
                    f"| {'yes' if k.fits else 'no'} | {'yes' if k.available else 'no'} |"
                )
            lines.append("")
            for k in r.knobs:
                lines.append(f"- *{k.name}* range: {k.range_source}.")
                if k.note:
                    lines.append(f"- *{k.name}*: {k.note}.")
            lines.append("")
    ranges = [c.uncertainty_range for c in report.comparisons if c.uncertainty_range is not None]
    if ranges:
        lines += [
            "## Ranges for the uncertainty runs",
            "",
            "How far each driver setting is varied when results are tested for robustness "
            "(docs/FRISCO_PROTOCOL.md §8.5):",
            "",
        ]
        lines += [f"- {_range_sentence(u)}" for u in ranges]
        lines.append("")
    lines += ["## Free-flow speed by station", ""]
    lines += [
        "| Station | Lanes | Limit | Windows | Days | Median | 15th–85th pct | Flow (veh/h/lane) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in obs.free_flow_stations:
        limit = "—" if s.speed_limit_ms is None else f"{s.speed_limit_ms / MPH_TO_MS:.0f} mph"
        spread = (
            "—"
            if s.p15_ms is None or s.p85_ms is None
            else f"{ms_to_kmh(s.p15_ms):.0f}–{ms_to_kmh(s.p85_ms):.0f} km/h"
        )
        median = "—" if s.median_ms is None else f"{ms_to_kmh(s.median_ms):.1f} km/h"
        flow = "—" if s.flow_median_veh_h_lane is None else f"{s.flow_median_veh_h_lane:,.0f}"
        lines.append(
            f"| {s.station} | {s.lanes} | {limit} | {s.n_windows} | {s.n_days} | {median} | "
            f"{spread} | {flow} |"
        )
    ff = obs.free_flow
    lines += [
        "",
        f"Corridor: median {_kmh(ff.median_ms)}, 15th–85th percentile "
        f"{_kmh(ff.p15_ms)} to {_kmh(ff.p85_ms)} of the five-minute means. The model's drivers "
        f"want {_kmh(mod.desired_speed['mean'])} on average (15th–85th "
        f"{_kmh(mod.desired_speed['p15'])} to {_kmh(mod.desired_speed['p85'])}, individual "
        "drivers — not comparable with the spread of five-minute means, which averages many "
        "vehicles).",
        "",
        "## Capacity by station",
        "",
        "| Station | Lanes | What limits its flow | 95th pct flow (veh/h/lane) | Speed there | "
        "Discharge flow | Pre-breakdown flow (breakdowns) |",
        "|---|---|---|---|---|---|---|",
    ]
    words = {
        "bottleneck_discharge": "discharges an active bottleneck",
        "congested": "reached congestion",
        "never_congested": "never congested (a lower bound)",
        "no_speed": "no speed reading",
    }
    for sc in obs.capacity_stations:
        cap = "—" if sc.capacity_veh_h_lane is None else f"{sc.capacity_veh_h_lane:,.0f}"
        if sc.interval is not None:
            cap += f" ({sc.interval.lo:,.0f}–{sc.interval.hi:,.0f})"
        spd = (
            "—"
            if sc.speed_at_capacity_ms is None
            else f"{ms_to_kmh(sc.speed_at_capacity_ms):.0f} km/h"
        )
        dis = (
            "—" if sc.discharge_flow_veh_h_lane is None else f"{sc.discharge_flow_veh_h_lane:,.0f}"
        )
        pre = (
            "—"
            if sc.pre_breakdown_flow_veh_h_lane is None
            else f"{sc.pre_breakdown_flow_veh_h_lane:,.0f} ({sc.n_breakdowns})"
        )
        lines.append(
            f"| {sc.station} | {sc.lanes} | {words[sc.category]} | {cap} | {spd} | {dis} | {pre} |"
        )
    if obs.bottlenecks:
        lines += ["", "Active bottlenecks (docs/FRISCO_PROTOCOL.md §5 rule):", ""]
        for b in obs.bottlenecks:
            lines.append(
                f"- {b['upstream']} → {b['downstream']}: {b['n_active_windows']} active "
                f"window(s) on {b['n_days']} day(s)"
            )
    hw = report.implied_headway
    lines += [
        "",
        "## Model values and how they were obtained",
        "",
        f"- Free-flow speed: {mod.model} steady state of {mod.n_draws:,} drivers drawn as the "
        f"simulator draws them (seed {mod.draw_seed}), at the observed light-traffic flow of "
        f"{(mod.free_flow_flow_veh_h_lane or 0):,.0f} veh/h/lane, each driver's desired speed "
        f"capped at {_cap_words(mod)} (SUMO speedFactor {mod.speed_factor:g}). Analytical, "
        "no simulation.",
        f"- Capacity, mean driver (closed form): {mod.capacity_mean_driver[0]:,.0f} veh/h/lane "
        f"at {_kmh(mod.capacity_mean_driver[1])}.",
        f"- Capacity, drawn population (one lane, common speed): "
        f"{mod.capacity_population[0]:,.0f} veh/h/lane at {_kmh(mod.capacity_population[1])}.",
    ]
    if mod.capacity_simulated is not None:
        sim = mod.capacity_simulated
        lines.append(
            f"- Capacity, simulated: {sim.raw_veh_h_lane:,.0f} veh/h/lane on a straight "
            f"{sim.sidecar.lanes}-lane road ({sim.sidecar.path}), × {sim.conditions_ratio:.3f} "
            f"for this corridor's limit and truck share = **{sim.value_veh_h_lane:,.0f}** (used)."
        )
    lines.append(f"- Capacity used for the verdict: {mod.capacity_basis}.")
    if hw.get("observed_gross_s") or hw.get("model_gross_s"):
        lines.append(
            "- Time headway per lane at capacity (diagnostic): observed "
            f"{_sec(hw.get('observed_gross_s'))} ({_sec(hw.get('observed_net_s'))} net of the "
            f"{hw.get('jam_spacing_m') or 0:.1f} m jam spacing), model "
            f"{_sec(hw.get('model_gross_s'))} ({_sec(hw.get('model_net_s'))} net); the "
            f"population's mean desired headway T is {_sec(hw.get('population_mean_T_s'))}."
        )
    if mod.sidecars:
        used = mod.capacity_simulated.sidecar.path if mod.capacity_simulated else None
        lines += ["", "Simulated capacity measurements considered:", ""]
        for cand in mod.sidecars:
            status = "used" if cand.path == used else ("accepted" if cand.accepted else "not used")
            lines.append(f"- {cand.path}: {status} — {cand.reason}")
    lines += ["", "## How each number was measured", ""]
    rules = obs.rules
    lines += [
        "- Readings the data-quality check excluded or set aside are not used ("
        + (
            f"applied: {obs.quality['summary']['n_exclude']} detector-day(s) excluded, "
            f"{obs.quality['summary']['n_suspect']} kept with caveats"
            if obs.quality.get("applied") and "summary" in obs.quality
            else "NOT applied"
        )
        + "); nothing is filled in.",
        f"- Statistics use {obs.interval_s / 60:g}-minute windows (data: {obs.source_interval_s:g} s); "
        "per-lane values divide a station's total by its lanes.",
        f"- Light traffic: occupancy ≤ {rules.free_flow_max_occupancy_pct:g} %, flow ≤ "
        f"{rules.free_flow_max_flow_veh_h_lane:,.0f} veh/h/lane and ≥ "
        f"{rules.free_flow_min_count_veh_lane:g} vehicles per lane in the window.",
        f"- Capacity: the {rules.capacity_percentile:g}th percentile of a station's per-lane flows; "
        f"congested below {rules.breakdown_speed_ms / MPH_TO_MS:.0f} mph for "
        f"{rules.congested_min_run_s / 60:g} minutes; an active bottleneck as in "
        "docs/FRISCO_PROTOCOL.md §5.",
        f"- Intervals: {rules.interval_level:.0%} percentile bootstrap over days.",
        "",
        "| Rule | Value | Source or reason |",
        "|---|---|---|",
    ]
    for key, value in rules.to_dict().items():
        shown = ", ".join(f"{x:g}" for x in value) if isinstance(value, list) else f"{value:g}"
        lines.append(f"| {key} | {shown} | {RULE_SOURCES.get(key, '')} |")
    notes = list(obs.notes) + list(mod.notes) + list(report.notes)
    if notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {n}" for n in notes]
    lines += [
        "",
        "## Limits",
        "",
        "- The model values are analytical (steady-state car following) except a simulated "
        "straight-road capacity where the repository holds one; none is a simulation of this "
        "corridor.",
        "- Free-flow speeds are five-minute means at detectors, not individual vehicles' speeds; "
        "their spread is not a spread of drivers.",
        "- A station's capacity value depends on the hours and days given: a span without the "
        "peak understates it.",
        "- The truck share is used only where classification counts were given; it is never "
        "inferred from loop data.",
        "",
    ]
    return "\n".join(lines)
