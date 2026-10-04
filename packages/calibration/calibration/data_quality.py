"""Detector data quality: per detector-day verdicts and station mass balance.

Before a corridor's detector counts become demand, boundary conditions or a
validation target, every detector-day is judged ``ok``, ``suspect`` or
``exclude`` by mechanical rules whose thresholds are the named constants
below, each with its source or its reason. The rules are fixed before any
result is seen and nothing is tuned to a dataset: a reviewer can lock the
:class:`QualityThresholds` record in a protocol and re-run it.

Input is the tidy detector frame of ``calibration.loaders.detector_csv``
(station rows, or per-lane rows with a ``lane`` column) laid out by
:func:`calibration.conservation.detector_grid`. A *sensor* is a station or one
lane of a station; a *sensor-day* is one sensor on one local date over the
grid's span of the day.

**Lineage.** The idea of judging a detector a whole day at a time from
statistics of its own series is the PeMS Daily Statistics Algorithm of Chen,
Kwon, Rice, Skabardonis & Varaiya (2003), "Detecting errors and imputing
missing data for single-loop surveillance systems", Transportation Research
Record 1855:160–167, as described by Bickel et al. (2007), "Measuring
traffic", Statistical Science 22(4):581–597, §3: per detector and day it
counts samples with occupancy = 0 (S1), samples with occupancy > 0 and
flow = 0 (S2), samples with occupancy above k* = 0.35 (S3), and the entropy of
the occupancy samples (S4, low for a constant series), and declares the
detector bad for the day when any count exceeds an empirically chosen
threshold. Those thresholds were set for 30-second samples and are not used
here; where a check below follows one of S1–S4 its docstring says which, and
every threshold states its own reason. Checks the DSA does not cover (flow
ceilings, implied vehicle length, day outliers, lane imbalance, mass balance)
cite no source for their numbers and state a reason instead.

**Verdicts.** ``exclude`` drops the whole sensor-day (the DSA's day-level
logic: once a detector is shown to malfunction on a day its plausible-looking
readings that day are not trusted either). ``suspect`` keeps the day but
masks the windows the finding names (only those, and only the quantity it
names — a frozen occupancy does not cost the counts). A sensor-day's verdict
is the worst of its findings. :func:`mask_grid` applies the verdicts.

**Mass balance.** After masking, consecutive mainline stations are compared
with the ramps between them (:mod:`calibration.conservation`): per period the
residual ``q_down − q_up − Σ q_on + Σ q_off`` and its share of the upstream
flow, and per day the summed residual against the count-error band. A segment
whose day residual leaves the band with no unmeasured ramp to explain it
points at a bad detector or an unknown ramp; when the segments either side of
one station leave it with opposite signs and similar size, that station is
the likely cause and is marked ``suspect`` for the day.

**Not done here.** No value is imputed, interpolated or rescaled: a masked or
excluded reading is NaN and stays NaN. A road closure looks exactly like a
dead detector in the counts (zero flow where traffic is usual); this module
cannot tell them apart and says so in every such finding.
"""

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from calibration.conservation import (
    DEFAULT_COUNT_ERROR,
    DEFAULT_PERIOD_S,
    Combination,
    CorridorLayout,
    DetectorGrid,
    RampSpec,
    SensorInfo,
    align_segment,
    clock_text,
    corridor_layout,
    count_tolerance,
    detector_grid,
    silent_lane_note,
    station_grid,
)
from flowstate_core.units import kmh_to_ms

QUALITY_SCHEMA: Final[str] = "flowstate.data_quality/1"
"""Schema tag of the JSON report."""

Verdict = Literal["ok", "suspect", "exclude"]
_RANK: Final[dict[str, int]] = {"ok": 0, "suspect": 1, "exclude": 2}

CHECKS: Final[tuple[str, ...]] = (
    "missing",
    "impossible",
    "stuck_flow",
    "stuck_occupancy",
    "stuck_speed",
    "zero_run",
    "zero_day",
    "low_flow",
    "zero_flow_occupied",
    "flow_without_occupancy",
    "implied_length",
    "day_outlier",
    "lane_imbalance",
    "mass_balance_pattern",
)
"""Every check, in the order it runs."""

QUANTITIES: Final[tuple[str, ...]] = ("flow", "occupancy", "speed")

# --- thresholds ---------------------------------------------------------------

MISSING_SUSPECT_SHARE: Final[float] = 0.20
"""Missing share of a day's windows above which the day is suspect.

Reason (convention): the complement of the 80 % sample-presence rule the
MnDOT loader applies to each window (``calibration.loaders.mndot``), applied
to the day."""

MISSING_EXCLUDE_SHARE: Final[float] = 0.50
"""Missing share above which the day is excluded. Reason (convention): a day
with more missing than present windows does not describe that day's traffic,
and a detector that drops out that much is not trusted for the rest."""

FLOW_CEILING_VEH_H_LANE: Final[float] = 3000.0
"""Highest plausible flow per lane in any window [veh/h/lane].

Reason: 3,000 veh/h/lane is a mean headway of 1.2 s held over the whole
window, a quarter above the 2,400 pc/h/ln basic-freeway capacity of the
Highway Capacity Manual; a window above it is a double count, a chattering
loop or a unit error, not traffic. Needs the lane count (station rows with
``lanes = 0`` are not tested)."""

SPEED_CEILING_MS: Final[float] = kmh_to_ms(160.0)
"""Highest plausible window mean speed [m/s] (160 km/h ≈ 99 mph). Reason: no
US posted limit exceeds 85 mph (137 km/h); a window *mean* above 160 km/h is a
unit or sensor error."""

OCCUPANCY_RANGE_PCT: Final[tuple[float, float]] = (0.0, 100.0)
"""Occupancy is a share of time: outside [0, 100] % it is impossible."""

IMPOSSIBLE_EXCLUDE_SHARE: Final[float] = 0.05
"""Share of a day's windows with an impossible reading at which the day is
excluded. Reason (convention): one window in twenty is a malfunction, not a
glitch; below it the day is suspect and only the offending readings are
masked."""

STUCK_FLOW_RUN_S: Final[float] = 1800.0
"""Duration of identical flow readings that marks a frozen counter [s]."""

STUCK_MIN_COUNT_VEH: Final[float] = 10.0
"""Least vehicles per window for a repeated flow to count as stuck.

Reason: for a count with mean μ ≥ 10 per window (Poisson), two consecutive
windows agree with probability ≈ 1/√(4πμ) ≤ 0.09, so 30 minutes of identical
values (6 windows at 5 min, 60 at 30 s) does not happen by chance; at night a
1-vehicle count legitimately repeats, which this floor leaves alone."""

STUCK_SINGLE_RUN_S: Final[float] = 7200.0
"""Duration of an identical occupancy or speed that marks it frozen [s].

Reason: occupancy and speed are often published rounded (integer percent,
integer mph), so consecutive windows agree far more often than counts do; two
hours of one value is beyond that rounding. Chen et al.'s S4 (low occupancy
entropy) targets the same failure."""

STUCK_MIN_OCCUPANCY_PCT: Final[float] = 2.0
"""Occupancies below this are not tested for a frozen value [percent].
Reason: 0 and 1 % are the integer-rounding floor of light traffic."""

ZERO_RUN_MIN_EXPECTED_VEH: Final[float] = 20.0
"""Expected vehicles over a run of zero counts at which the run is flagged.

Reason: the expectation is this detector's own median count at the same
time of day over the dates (:data:`MIN_PROFILE_DAYS`), so a quiet night is
expected to read zero and is left alone; under a Poisson count the
probability of no vehicle where 20 are expected is e⁻²⁰ ≈ 2·10⁻⁹. This is the
failure Chen et al.'s S1 (occupancy = 0 too often, "card off") targets; a full
road closure produces the same reading."""

MIN_PROFILE_DAYS: Final[int] = 3
"""Dates that must observe a window for its time-of-day median to exist.
Reason: a median of fewer than three values is not robust to one bad day."""

DAY_JUDGED_MIN_VALID_S: Final[float] = 10800.0
"""Valid readings a day needs before "no vehicle all day" or "too little flow
all day" is judged [s] (3 h). Reason: a lightly used ramp can read zero for
an hour at night; three hours of readings that are all zero is a dead
detector or a closure."""

LOW_FLOW_VEH_H: Final[float] = 30.0
"""Mean flow below which a sensor-day is suspect [veh/h]. Same value and
reason as ``calibration.onboarding.DEFAULT_ALIVE_VEH_H``: a lane or ramp in
service carrying 20 veh/h all day is a broken loop more often than a road."""

ZERO_FLOW_MIN_OCCUPIED_S: Final[float] = 5.0
"""Occupied time in a window with no vehicle counted that is inconsistent [s].

Reason: a vehicle detected just before a window boundary spills its
occupancy into the next window, but a 7 m effective length at 5 m/s occupies
the loop 1.4 s in all; 5 s of occupancy with no count is a hanging-on loop or
a missed count (Chen et al.'s S2: occupancy > 0 with flow = 0)."""

STANDSTILL_SPEED_MS: Final[float] = 5.0
"""Below this reported speed a zero-count, occupied window is a queue standing
on the loop and is not flagged [m/s]."""

ZERO_FLOW_OCCUPIED_SUSPECT_SHARE: Final[float] = 0.01
"""Share of a day's windows with occupancy but no count at which the day is
suspect. Reason (convention): a few standing-queue windows on an incident
day are legitimate; more than one window in a hundred is not."""

OCCUPANCY_FLOOR_FLOW_VEH_H_LANE: Final[float] = 600.0
"""Per-lane flow at which a zero occupancy is impossible [veh/h/lane].

Reason: occupancy ≈ q·L/v; at 600 veh/h/lane, a 4 m effective length and
45 m/s it is still 1.5 %, which no rounding turns into 0. Below it a zero is
rounding and is left alone."""

IMPLIED_LENGTH_BAND_M: Final[tuple[float, float]] = (2.5, 25.0)
"""Plausible window-mean effective vehicle length ``v·o/q`` [m].

Reason: below 2.5 m is shorter than any motor vehicle plus a detection zone;
above 25 m is longer than a tractor-trailer plus zone. Arithmetic-mean speeds
overstate the length in stop-and-go (they exceed the harmonic mean the
identity needs), which the generous upper bound absorbs. A speed that the
source itself derives from occupancy with an assumed length passes by
construction — the check then only confirms the units."""

IMPLIED_LENGTH_MIN_COUNT_VEH: Final[float] = 10.0
"""Least vehicles per lane in a window for its implied length to be tested.
Reason: below ten vehicles the mean length is decided by which vehicles
happened to pass (one truck among three cars)."""

IMPLIED_LENGTH_MIN_TESTED_S: Final[float] = 3600.0
"""Tested windows a day needs before the implied-length check judges it [s]."""

IMPLIED_LENGTH_SUSPECT_SHARE: Final[float] = 0.10
"""Share of tested windows outside the band at which the day is suspect."""

IMPLIED_LENGTH_EXCLUDE_SHARE: Final[float] = 0.50
"""Share outside the band at which the day is excluded. Reason: when most
windows disagree, flow, occupancy and speed do not describe the same traffic
— one of them is mis-scaled or wrong — and no window of the day is trusted."""

WINDOW_FLAG_EXCLUDE_SHARE: Final[float] = 0.10
"""Share of a day's windows flagged by a frozen-flow, zero-run or
occupied-without-count finding at which the day is excluded. Reason
(convention): 2.4 hours of a full day — a detector that misbehaves that long
is malfunctioning for the day (the DSA's day-level logic)."""

DAY_RATIO_SUSPECT_BAND: Final[tuple[float, float]] = (0.8, 1.25)
"""Day volume relative to the detector's median day, after dividing out the
corridor-wide day factor, outside which the day is suspect. Reason
(convention): symmetric ±20 % in log; one lane lost or double counted at a
station of up to five lanes moves its total by 20 % or more."""

DAY_RATIO_EXCLUDE_BAND: Final[tuple[float, float]] = (0.6, 1.0 / 0.6)
"""Band outside which the day is excluded. Reason (convention): −40 % /
+67 % is more than any ordinary weekday variation of one detector against its
own corridor on the same day."""

DAY_MIN_COMPARED_SHARE: Final[float] = 0.5
"""Share of the day's windows that must be comparable with the median day for
the day-outlier check to judge it."""

MIN_SENSORS_FOR_CORRIDOR_FACTOR: Final[int] = 3
"""Sensors that must judge a date before a corridor-wide day factor is formed
(otherwise the factor is 1 and the report says so)."""

LANE_RATIO_SUSPECT_BAND: Final[tuple[float, float]] = (0.5, 2.0)
"""A lane's day flow over the mean of the station's other lanes, outside
which the lane-day is suspect. Reason (convention): at moderate flow the lanes
of a freeway station carry within a factor of two of each other; an
auxiliary or exit-only lane may not, and is declared in ``exempt_lanes``.
Bickel et al. (2007) §3 point to the high lane-to-lane correlation as the
evidence a single detector's own series cannot give."""

LANE_RATIO_EXCLUDE_BAND: Final[tuple[float, float]] = (1.0 / 3.0, 3.0)
"""Band outside which the lane-day is excluded (a two-lane station is only
ever suspect: an imbalance between two lanes cannot be laid on one)."""

LANE_MIN_FLOW_VEH_H_LANE: Final[float] = 600.0
"""Station mean flow per lane above which a window enters the lane check.
Reason: in light traffic lane use is uneven by choice, not by fault."""

LANE_MIN_TESTED_S: Final[float] = 3600.0
"""Tested windows a station-day needs before the lane check judges it [s]."""

MASS_BALANCE_MIN_EVALUATED_SHARE: Final[float] = 0.5
"""Share of a day's periods that must be complete for a segment's balance to
be judged that day."""

MASS_BALANCE_PERSISTENT_SHARE: Final[float] = 0.5
"""Share of evaluated days with an unexplained residual at which a segment is
called persistent."""

ATTRIBUTION_RATIO_BAND: Final[tuple[float, float]] = (0.5, 2.0)
"""Ratio of the two opposite residuals either side of a station within which
the station is named as their cause. Reason: a station that miscounts by δ
moves the segment above it by −δ and the one below by +δ; the factor two
leaves room for the two segments' different storage and count noise."""


@dataclass(frozen=True)
class QualityThresholds:
    """Every threshold the checks use (defaults: the module constants).

    Lock an instance in a protocol to fix the rules before results are seen;
    :meth:`to_dict` is what the report records.
    """

    missing_suspect_share: float = MISSING_SUSPECT_SHARE
    missing_exclude_share: float = MISSING_EXCLUDE_SHARE
    flow_ceiling_veh_h_lane: float = FLOW_CEILING_VEH_H_LANE
    speed_ceiling_ms: float = SPEED_CEILING_MS
    occupancy_range_pct: tuple[float, float] = OCCUPANCY_RANGE_PCT
    impossible_exclude_share: float = IMPOSSIBLE_EXCLUDE_SHARE
    stuck_flow_run_s: float = STUCK_FLOW_RUN_S
    stuck_min_count_veh: float = STUCK_MIN_COUNT_VEH
    stuck_single_run_s: float = STUCK_SINGLE_RUN_S
    stuck_min_occupancy_pct: float = STUCK_MIN_OCCUPANCY_PCT
    zero_run_min_expected_veh: float = ZERO_RUN_MIN_EXPECTED_VEH
    min_profile_days: int = MIN_PROFILE_DAYS
    day_judged_min_valid_s: float = DAY_JUDGED_MIN_VALID_S
    low_flow_veh_h: float = LOW_FLOW_VEH_H
    zero_flow_min_occupied_s: float = ZERO_FLOW_MIN_OCCUPIED_S
    standstill_speed_ms: float = STANDSTILL_SPEED_MS
    zero_flow_occupied_suspect_share: float = ZERO_FLOW_OCCUPIED_SUSPECT_SHARE
    occupancy_floor_flow_veh_h_lane: float = OCCUPANCY_FLOOR_FLOW_VEH_H_LANE
    implied_length_band_m: tuple[float, float] = IMPLIED_LENGTH_BAND_M
    implied_length_min_count_veh: float = IMPLIED_LENGTH_MIN_COUNT_VEH
    implied_length_min_tested_s: float = IMPLIED_LENGTH_MIN_TESTED_S
    implied_length_suspect_share: float = IMPLIED_LENGTH_SUSPECT_SHARE
    implied_length_exclude_share: float = IMPLIED_LENGTH_EXCLUDE_SHARE
    window_flag_exclude_share: float = WINDOW_FLAG_EXCLUDE_SHARE
    day_ratio_suspect_band: tuple[float, float] = DAY_RATIO_SUSPECT_BAND
    day_ratio_exclude_band: tuple[float, float] = DAY_RATIO_EXCLUDE_BAND
    day_min_compared_share: float = DAY_MIN_COMPARED_SHARE
    min_sensors_for_corridor_factor: int = MIN_SENSORS_FOR_CORRIDOR_FACTOR
    lane_ratio_suspect_band: tuple[float, float] = LANE_RATIO_SUSPECT_BAND
    lane_ratio_exclude_band: tuple[float, float] = LANE_RATIO_EXCLUDE_BAND
    lane_min_flow_veh_h_lane: float = LANE_MIN_FLOW_VEH_H_LANE
    lane_min_tested_s: float = LANE_MIN_TESTED_S
    mass_balance_min_evaluated_share: float = MASS_BALANCE_MIN_EVALUATED_SHARE
    mass_balance_persistent_share: float = MASS_BALANCE_PERSISTENT_SHARE
    attribution_ratio_band: tuple[float, float] = ATTRIBUTION_RATIO_BAND

    def to_dict(self) -> dict[str, Any]:
        """JSON form (tuples as lists), in field order."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


THRESHOLD_SOURCES: Final[dict[str, str]] = {
    "missing_suspect_share": "convention: complement of the MnDOT loader's 80 % window rule",
    "missing_exclude_share": "convention: more missing than present",
    "flow_ceiling_veh_h_lane": "1.2 s mean headway; a quarter above HCM basic-freeway capacity",
    "speed_ceiling_ms": "160 km/h: above any US posted limit (85 mph) for a window mean",
    "occupancy_range_pct": "definition of occupancy",
    "impossible_exclude_share": "convention: one window in twenty",
    "stuck_flow_run_s": "Poisson: 30 min of identical counts (mean >= 10) does not occur",
    "stuck_min_count_veh": "Poisson: consecutive agreement <= 0.09 at mean >= 10",
    "stuck_single_run_s": "beyond integer rounding of occupancy/speed (cf. Chen et al. 2003 S4)",
    "stuck_min_occupancy_pct": "integer-rounding floor of light traffic",
    "zero_run_min_expected_veh": "Poisson e^-20 against the detector's own profile (cf. S1)",
    "min_profile_days": "a median of < 3 values is not robust",
    "day_judged_min_valid_s": "3 h of all-zero readings is a dead detector or a closure",
    "low_flow_veh_h": "same as calibration.onboarding.DEFAULT_ALIVE_VEH_H",
    "zero_flow_min_occupied_s": "beyond a boundary spill of one vehicle (cf. S2)",
    "standstill_speed_ms": "a standing queue on the loop is legitimate",
    "zero_flow_occupied_suspect_share": "convention: one window in a hundred",
    "occupancy_floor_flow_veh_h_lane": "occupancy >= 1.5 % at 600 veh/h/lane, 45 m/s, 4 m",
    "implied_length_band_m": "shorter than any vehicle / longer than a tractor-trailer",
    "implied_length_min_count_veh": "below 10 vehicles the mean length is chance",
    "implied_length_min_tested_s": "one hour of tested windows",
    "implied_length_suspect_share": "convention",
    "implied_length_exclude_share": "most windows inconsistent: a quantity is wrong",
    "window_flag_exclude_share": "convention: 2.4 h of a full day",
    "day_ratio_suspect_band": "convention: +-20 % (a lane at a station of <= 5 lanes)",
    "day_ratio_exclude_band": "convention: -40 % / +67 %",
    "day_min_compared_share": "half the day comparable",
    "min_sensors_for_corridor_factor": "a median of < 3 sensors is not robust",
    "lane_ratio_suspect_band": "convention: lanes within a factor 2 at moderate flow",
    "lane_ratio_exclude_band": "convention: a factor 3",
    "lane_min_flow_veh_h_lane": "light traffic uses lanes unevenly by choice",
    "lane_min_tested_s": "one hour of tested windows",
    "mass_balance_min_evaluated_share": "half the day's periods complete",
    "mass_balance_persistent_share": "half the evaluated days",
    "attribution_ratio_band": "a miscount shifts both neighbours by the same amount",
}
"""Source or reason of every :class:`QualityThresholds` field (the report's
threshold table)."""

CHECK_DESCRIPTIONS: Final[dict[str, str]] = {
    "missing": "share of the day's readings that are missing",
    "impossible": (
        "physically impossible readings: negative flow, flow above "
        "3,000 veh/h per lane, occupancy outside 0-100 %, speed below 0 or above 160 km/h"
    ),
    "stuck_flow": "the count repeats one value for 30 min or more where 10+ vehicles pass",
    "stuck_occupancy": "occupancy repeats one value (2 % or more) for 2 h or more",
    "stuck_speed": "speed repeats one value for 2 h or more",
    "zero_run": (
        "no vehicle counted for a stretch in which this detector usually counts 20 or more "
        "(cf. Chen et al. 2003, S1)"
    ),
    "zero_day": "no vehicle counted in three or more hours of readings",
    "low_flow": "mean flow below 30 veh/h over three or more hours of readings",
    "zero_flow_occupied": (
        "the loop is occupied 5 s or more in a window with no vehicle counted "
        "(cf. Chen et al. 2003, S2)"
    ),
    "flow_without_occupancy": "occupancy reads 0 while 600+ veh/h per lane pass",
    "implied_length": "flow, occupancy and speed imply a mean vehicle length outside 2.5-25 m",
    "day_outlier": (
        "the day's volume against the detector's median day, with the corridor-wide day "
        "factor divided out"
    ),
    "lane_imbalance": "a lane's flow against the mean of the station's other lanes",
    "mass_balance_pattern": (
        "the segments either side of this station have opposite, similar residuals"
    ),
}
"""Plain-language description of every check (the report's method list)."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


def _num(value: float | None, digits: int = 4) -> float | None:
    """A float for JSON: ``None`` for NaN/None, rounded."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


@dataclass(frozen=True)
class Finding:
    """One non-``ok`` result of a check on a sensor-day.

    Attributes:
        check: Check name (:data:`CHECKS`).
        verdict: ``suspect`` or ``exclude``.
        reason: Plain-language sentence with the numbers behind it.
        statistic: The check's statistic (a share, a duration in s, a
            ratio …), ``None`` when not numeric.
        n_windows: Windows the finding names (0 for a day-level finding).
    """

    check: str
    verdict: Verdict
    reason: str
    statistic: float | None = None
    n_windows: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "check": self.check,
            "verdict": self.verdict,
            "reason": self.reason,
            "statistic": _num(self.statistic),
            "n_windows": int(self.n_windows),
        }


@dataclass(frozen=True)
class SensorDayQuality:
    """The verdict on one sensor on one date.

    Attributes:
        sensor: Sensor id.
        station: Station id.
        lane: Lane id (per-lane data) or ``None``.
        kind: ``mainline``, ``on_ramp`` or ``off_ramp``.
        date: Local date ``YYYY-MM-DD``.
        verdict: ``ok``, ``suspect`` or ``exclude``.
        n_expected: Windows of the day's span.
        n_valid: Windows with a finite flow as delivered.
        n_usable: Windows with a finite flow after the verdicts are applied
            (0 for an excluded day).
        stats: Every check's statistic under a fixed set of keys (``None``
            where the check could not run).
        findings: The non-``ok`` findings.
        masked: Quantity (``flow``/``occupancy``/``speed``) → the window
            indices a ``suspect`` finding masks (an excluded day masks all).
    """

    sensor: str
    station: str
    lane: str | None
    kind: str
    date: str
    verdict: Verdict
    n_expected: int
    n_valid: int
    n_usable: int
    stats: dict[str, float | int | None]
    findings: tuple[Finding, ...] = ()
    masked: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON form (fixed keys)."""
        return {
            "sensor": self.sensor,
            "station": self.station,
            "lane": self.lane,
            "kind": self.kind,
            "date": self.date,
            "verdict": self.verdict,
            "n_expected": self.n_expected,
            "n_valid": self.n_valid,
            "n_usable": self.n_usable,
            "stats": {
                k: (_num(v) if isinstance(v, float) else v) for k, v in sorted(self.stats.items())
            },
            "findings": [f.to_dict() for f in self.findings],
            "masked": {q: list(self.masked.get(q, ())) for q in QUANTITIES},
        }


@dataclass(frozen=True)
class SegmentDayBalance:
    """Station-to-station mass balance of one segment on one date.

    Attributes:
        upstream: Upstream station.
        downstream: Downstream station.
        date: Local date.
        verdict: ``ok``, ``suspect`` or ``not_evaluated``.
        explanation: Plain-language statement of the result.
        n_periods: Periods of the day.
        n_valid_periods: Periods with every term measured.
        residual_veh_h: Mean residual ``q_down − q_up − Σon + Σoff`` over
            the valid periods [veh/h].
        residual_share: Summed residual over summed upstream flow.
        tolerance_share: The count-error band, as a share of upstream flow.
        share_periods_beyond_tolerance: Valid periods whose own residual
            leaves its own band (storage makes single periods noisy; reported,
            not judged).
        measured_ramps: Ramps whose counts entered the balance that day.
        unmeasured_ramps: Ramps without counts that day (their flow is the
            residual).
        period_residual_veh_h: Per-period residual (``None`` = incomplete).
    """

    upstream: str
    downstream: str
    date: str
    verdict: str
    explanation: str
    n_periods: int
    n_valid_periods: int
    residual_veh_h: float | None
    residual_share: float | None
    tolerance_share: float | None
    share_periods_beyond_tolerance: float | None
    measured_ramps: tuple[str, ...]
    unmeasured_ramps: tuple[str, ...]
    period_residual_veh_h: tuple[float | None, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "date": self.date,
            "verdict": self.verdict,
            "explanation": self.explanation,
            "n_periods": self.n_periods,
            "n_valid_periods": self.n_valid_periods,
            "residual_veh_h": _num(self.residual_veh_h, 1),
            "residual_share": _num(self.residual_share),
            "tolerance_share": _num(self.tolerance_share),
            "share_periods_beyond_tolerance": _num(self.share_periods_beyond_tolerance),
            "measured_ramps": list(self.measured_ramps),
            "unmeasured_ramps": list(self.unmeasured_ramps),
            "period_residual_veh_h": [_num(v, 1) for v in self.period_residual_veh_h],
        }


@dataclass(frozen=True)
class SegmentSummary:
    """A segment's balance over all dates.

    Attributes:
        upstream: Upstream station.
        downstream: Downstream station.
        length_m: Station spacing [m].
        n_days_evaluated: Dates judged.
        n_days_suspect: Dates with an unexplained residual.
        persistent: ``n_days_suspect`` reaches the persistence share.
        median_residual_share: Median day residual share over judged dates.
        ramps: The segment's ramps (``id (kind, measured|unmeasured)``).
    """

    upstream: str
    downstream: str
    length_m: float
    n_days_evaluated: int
    n_days_suspect: int
    persistent: bool
    median_residual_share: float | None
    ramps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "length_m": _num(self.length_m, 1),
            "n_days_evaluated": self.n_days_evaluated,
            "n_days_suspect": self.n_days_suspect,
            "persistent": self.persistent,
            "median_residual_share": _num(self.median_residual_share),
            "ramps": list(self.ramps),
        }


@dataclass(frozen=True)
class DataQualityReport:
    """Everything :func:`assess_quality` found (module docstring).

    Attributes:
        interval_s: Window length [s].
        start_s: Local clock start of the day's span [s].
        n_windows: Windows per day.
        dates: Local dates.
        per_lane: True when sensors are lanes.
        thresholds: The thresholds used.
        count_error: Relative count error per detector (mass balance band).
        combination: How count errors combine (``linear``/``quadrature``).
        period_s: Mass-balance period [s].
        exempt_lanes: Sensors exempt from the lane check.
        sensors: Every sensor's metadata.
        sensor_days: One verdict per sensor and date.
        corridor_day_factors: Date → ``{"factor", "n_sensors", "unusual"}``.
        layout: The corridor layout used for the mass balance, ``None`` when
            it could not be built.
        mass_balance: One record per segment and date.
        segments: One summary per segment.
        attributions: Station-days named by the mass-balance pattern.
        notes: Plain statements about what could not be checked.
        schema: :data:`QUALITY_SCHEMA`.
    """

    interval_s: float
    start_s: float
    n_windows: int
    dates: tuple[str, ...]
    per_lane: bool
    thresholds: QualityThresholds
    count_error: float
    combination: str
    period_s: float
    exempt_lanes: tuple[str, ...]
    sensors: tuple[SensorInfo, ...]
    sensor_days: tuple[SensorDayQuality, ...]
    corridor_day_factors: dict[str, dict[str, Any]]
    layout: CorridorLayout | None
    mass_balance: tuple[SegmentDayBalance, ...]
    segments: tuple[SegmentSummary, ...]
    attributions: tuple[dict[str, Any], ...]
    notes: tuple[str, ...]
    schema: str = QUALITY_SCHEMA

    def sensor_day(self, sensor: str, date: str) -> SensorDayQuality:
        """The verdict on one sensor-day.

        Raises:
            KeyError: No such sensor-day.
        """
        for sd in self.sensor_days:
            if sd.sensor == sensor and sd.date == date:
                return sd
        raise KeyError(f"no sensor-day {sensor!r} on {date!r}")

    def summary(self) -> dict[str, Any]:
        """Headline counts: verdicts, readings delivered and usable."""
        counts = {v: 0 for v in ("ok", "suspect", "exclude")}
        for sd in self.sensor_days:
            counts[sd.verdict] += 1
        expected = sum(sd.n_expected for sd in self.sensor_days)
        valid = sum(sd.n_valid for sd in self.sensor_days)
        usable = sum(sd.n_usable for sd in self.sensor_days)
        by_sensor: dict[str, dict[str, int]] = {}
        for sd in self.sensor_days:
            entry = by_sensor.setdefault(sd.sensor, {"ok": 0, "suspect": 0, "exclude": 0})
            entry[sd.verdict] += 1
        return {
            "n_sensors": len(self.sensors),
            "n_dates": len(self.dates),
            "n_sensor_days": len(self.sensor_days),
            "n_ok": counts["ok"],
            "n_suspect": counts["suspect"],
            "n_exclude": counts["exclude"],
            "expected_readings": expected,
            "valid_readings": valid,
            "usable_readings": usable,
            "valid_share": _num(valid / expected if expected else None),
            "usable_share": _num(usable / expected if expected else None),
            "sensors_always_excluded": sorted(
                s for s, c in by_sensor.items() if c["exclude"] == sum(c.values())
            ),
            "n_segments": len(self.segments),
            "n_segments_persistent": sum(1 for s in self.segments if s.persistent),
        }

    def verdicts(self) -> pd.DataFrame:
        """One row per sensor-day: ``sensor, date, kind, verdict, checks, reasons``."""
        rows = [
            {
                "sensor": sd.sensor,
                "date": sd.date,
                "kind": sd.kind,
                "verdict": sd.verdict,
                "checks": ",".join(f.check for f in sd.findings),
                "reasons": " | ".join(f.reason for f in sd.findings),
            }
            for sd in self.sensor_days
        ]
        return pd.DataFrame(
            rows, columns=["sensor", "date", "kind", "verdict", "checks", "reasons"]
        )

    def to_dict(self) -> dict[str, Any]:
        """The report's JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "grid": {
                "interval_s": self.interval_s,
                "start_local": clock_text(self.start_s),
                "end_local": clock_text(self.start_s + self.n_windows * self.interval_s),
                "n_windows": self.n_windows,
                "dates": list(self.dates),
                "per_lane": self.per_lane,
            },
            "parameters": {
                "thresholds": self.thresholds.to_dict(),
                "threshold_sources": dict(THRESHOLD_SOURCES),
                "count_error": self.count_error,
                "combination": self.combination,
                "period_s": self.period_s,
                "exempt_lanes": list(self.exempt_lanes),
            },
            "method": {
                "checks": dict(CHECK_DESCRIPTIONS),
                "verdicts": (
                    "exclude drops the sensor-day; suspect keeps it and masks the windows its "
                    "findings name; a sensor-day's verdict is the worst of its findings"
                ),
                "imputation": "none: masked and excluded readings are NaN and stay NaN",
                "lineage": (
                    "day-level judgement after the PeMS Daily Statistics Algorithm (Chen, Kwon, "
                    "Rice, Skabardonis & Varaiya 2003, TRR 1855:160-167; described in Bickel et "
                    "al. 2007, Statistical Science 22(4) s.3); thresholds stated per check"
                ),
            },
            "summary": self.summary(),
            "sensors": [s.to_dict() for s in self.sensors],
            "sensor_days": [sd.to_dict() for sd in self.sensor_days],
            "corridor_day_factors": {
                d: {k: (_num(v) if isinstance(v, float) else v) for k, v in sorted(rec.items())}
                for d, rec in sorted(self.corridor_day_factors.items())
            },
            "mass_balance": {
                "layout": None if self.layout is None else self.layout.to_dict(),
                "segments": [s.to_dict() for s in self.segments],
                "segment_days": [m.to_dict() for m in self.mass_balance],
                "attributions": [dict(a) for a in self.attributions],
            },
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """The JSON text (sorted keys, NaN written as ``null``)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _value_runs(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Maximal runs of identical finite values: ``(starts, lengths, values)``."""
    finite = np.isfinite(x)
    if not finite.any():
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty, np.zeros(0)
    same = np.zeros(x.size, dtype=bool)
    same[1:] = finite[1:] & finite[:-1] & (x[1:] == x[:-1])
    idx = np.flatnonzero(finite)
    new = ~same[idx]
    run_id = np.cumsum(new) - 1
    starts = idx[new]
    lengths = np.bincount(run_id)
    return starts.astype(np.int64), lengths.astype(np.int64), x[starts]


def _run_mask(n: int, starts: Iterable[int], lengths: Iterable[int]) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for s, length in zip(starts, lengths, strict=True):
        mask[int(s) : int(s) + int(length)] = True
    return mask


def _min_windows(duration_s: float, interval_s: float) -> int:
    """Windows a run needs to last ``duration_s`` (at least two)."""
    return max(2, math.ceil(duration_s / interval_s - 1e-9))


def _duration(seconds: float) -> str:
    """``"45 min"`` / ``"2 h 30 min"``."""
    minutes = round(seconds / 60.0)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" + (f" {rest} min" if rest else "")


def _profile(values: np.ndarray, min_days: int) -> np.ndarray:
    """Time-of-day median over dates where at least ``min_days`` observed."""
    count = np.isfinite(values).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        median = (
            np.nanmedian(values, axis=0) if values.shape[0] else np.full(values.shape[1], np.nan)
        )
    return np.where(count >= min_days, median, np.nan)


def _band_verdict(
    value: float, suspect: tuple[float, float], exclude: tuple[float, float]
) -> Verdict:
    if not exclude[0] <= value <= exclude[1]:
        return "exclude"
    if not suspect[0] <= value <= suspect[1]:
        return "suspect"
    return "ok"


class _DayEval:
    """Mutable working record of one sensor-day."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.n_valid = 0
        self.stats: dict[str, float | int | None] = {}
        self.findings: list[Finding] = []
        self.masks: dict[str, np.ndarray] = {q: np.zeros(n, dtype=bool) for q in QUANTITIES}

    def add(
        self,
        check: str,
        verdict: Verdict,
        reason: str,
        *,
        statistic: float | None = None,
        n_windows: int = 0,
        mask: Mapping[str, np.ndarray] | None = None,
    ) -> None:
        self.findings.append(Finding(check, verdict, reason, statistic, n_windows))
        for quantity, m in (mask or {}).items():
            self.masks[quantity] |= m

    @property
    def verdict(self) -> Verdict:
        worst: Verdict = "ok"
        for f in self.findings:
            if _RANK[f.verdict] > _RANK[worst]:
                worst = f.verdict
        return worst


_STAT_KEYS: Final[tuple[str, ...]] = (
    "missing_share",
    "n_impossible",
    "max_flow_veh_h_lane",
    "longest_stuck_flow_s",
    "longest_stuck_occupancy_s",
    "longest_stuck_speed_s",
    "zero_run_windows",
    "mean_flow_veh_h",
    "zero_flow_occupied_windows",
    "flow_without_occupancy_windows",
    "implied_length_median_m",
    "implied_length_outside_share",
    "implied_length_tested",
    "day_ratio",
    "day_ratio_relative",
    "lane_ratio",
)
"""Keys every sensor-day's ``stats`` carries (``None`` where not computed)."""


# ---------------------------------------------------------------------------
# Per sensor-day checks
# ---------------------------------------------------------------------------


def _evaluate_day(
    f: np.ndarray,
    o: np.ndarray,
    v: np.ndarray,
    *,
    info: SensorInfo,
    grid: DetectorGrid,
    reference: np.ndarray,
    th: QualityThresholds,
) -> _DayEval:
    """Window- and day-level checks of one sensor-day (module docstring)."""
    n = f.size
    dt = grid.interval_s
    ev = _DayEval(n)
    ev.stats = dict.fromkeys(_STAT_KEYS)
    finite_f = np.isfinite(f)
    ev.n_valid = int(finite_f.sum())
    missing = 1.0 - ev.n_valid / n
    ev.stats["missing_share"] = missing
    if missing > th.missing_exclude_share or missing > th.missing_suspect_share:
        verdict: Verdict = "exclude" if missing > th.missing_exclude_share else "suspect"
        limit = th.missing_exclude_share if verdict == "exclude" else th.missing_suspect_share
        ev.add(
            "missing",
            verdict,
            f"{missing:.0%} of the day's {n} readings are missing (limit {limit:.0%})",
            statistic=missing,
        )
    if ev.n_valid == 0:
        return ev
    lanes = info.lanes

    # -- impossible values ---------------------------------------------------
    neg_f = finite_f & (f < 0.0)
    over_f = (
        finite_f & (f / lanes > th.flow_ceiling_veh_h_lane)
        if lanes > 0
        else np.zeros(n, dtype=bool)
    )
    lo_o, hi_o = th.occupancy_range_pct
    bad_o = np.isfinite(o) & ((o < lo_o) | (o > hi_o))
    bad_v = np.isfinite(v) & ((v < 0.0) | (v > th.speed_ceiling_ms))
    any_bad = neg_f | over_f | bad_o | bad_v
    n_bad = int(any_bad.sum())
    ev.stats["n_impossible"] = n_bad
    if lanes > 0:
        ev.stats["max_flow_veh_h_lane"] = float(np.nanmax(f[finite_f])) / lanes
    if n_bad:
        parts = []
        if neg_f.any():
            parts.append(f"negative flow in {int(neg_f.sum())}")
        if over_f.any():
            parts.append(
                f"flow above {th.flow_ceiling_veh_h_lane:,.0f} veh/h per lane in "
                f"{int(over_f.sum())} (max {float(np.nanmax(f[over_f])) / lanes:,.0f})"
            )
        if bad_o.any():
            parts.append(f"occupancy outside {lo_o:g}-{hi_o:g} % in {int(bad_o.sum())}")
        if bad_v.any():
            parts.append(f"speed below 0 or above 160 km/h in {int(bad_v.sum())}")
        share = n_bad / max(ev.n_valid, 1)
        ev.add(
            "impossible",
            "exclude" if share >= th.impossible_exclude_share else "suspect",
            f"physically impossible readings: {'; '.join(parts)} window(s) — "
            f"{share:.1%} of the day's readings",
            statistic=share,
            n_windows=n_bad,
            mask={"flow": neg_f | over_f, "occupancy": bad_o, "speed": bad_v},
        )
    fc = np.where(neg_f | over_f, np.nan, f)
    oc = np.where(bad_o, np.nan, o)
    vc = np.where(bad_v, np.nan, v)

    # -- frozen values -------------------------------------------------------
    starts, lengths, values = _value_runs(fc)
    long_enough = lengths >= _min_windows(th.stuck_flow_run_s, dt)
    busy = values * dt / 3600.0 >= th.stuck_min_count_veh
    sel = long_enough & busy
    ev.stats["longest_stuck_flow_s"] = float(lengths[sel].max() * dt) if sel.any() else 0.0
    if sel.any():
        mask = _run_mask(n, starts[sel], lengths[sel])
        k = int(np.argmax(np.where(sel, lengths, -1)))
        share = int(mask.sum()) / max(ev.n_valid, 1)
        ev.add(
            "stuck_flow",
            "exclude" if share >= th.window_flag_exclude_share else "suspect",
            f"the count repeats {values[k]:,.0f} veh/h for {_duration(lengths[k] * dt)} from "
            f"{grid.window_label(int(starts[k]))} ({int(sel.sum())} run(s), {int(mask.sum())} "
            f"windows) — a frozen counter, not traffic",
            statistic=share,
            n_windows=int(mask.sum()),
            mask={"flow": mask, "occupancy": mask, "speed": mask},
        )
        fc = np.where(mask, np.nan, fc)
        oc = np.where(mask, np.nan, oc)
        vc = np.where(mask, np.nan, vc)

    single_min = _min_windows(th.stuck_single_run_s, dt)
    for quantity, series in (("occupancy", oc), ("speed", vc)):
        starts, lengths, values = _value_runs(series)
        floor_ok = values >= th.stuck_min_occupancy_pct if quantity == "occupancy" else values > 0.0
        sel = (lengths >= single_min) & floor_ok
        key = f"longest_stuck_{quantity}_s"
        ev.stats[key] = float(lengths[sel].max() * dt) if sel.any() else 0.0
        if not sel.any():
            continue
        mask = _run_mask(n, starts[sel], lengths[sel])
        k = int(np.argmax(np.where(sel, lengths, -1)))
        shown = f"{values[k]:g} %" if quantity == "occupancy" else f"{values[k]:.2f} m/s"
        ev.add(
            f"stuck_{quantity}",
            "suspect",
            f"{quantity} repeats {shown} for {_duration(lengths[k] * dt)} from "
            f"{grid.window_label(int(starts[k]))} — a frozen or default value; the {quantity} "
            f"of those windows is masked, the counts are kept",
            statistic=float(lengths[k] * dt),
            n_windows=int(mask.sum()),
            mask={quantity: mask},
        )
        if quantity == "occupancy":
            oc = np.where(mask, np.nan, oc)
        else:
            vc = np.where(mask, np.nan, vc)

    # -- occupied with no count (before the zero runs: the more specific finding) --
    occupied_s = oc / 100.0 * dt
    standstill = np.isfinite(vc) & (vc < th.standstill_speed_ms)
    zfo = (
        np.isfinite(fc)
        & (fc == 0.0)
        & np.isfinite(oc)
        & (occupied_s >= th.zero_flow_min_occupied_s)
        & ~standstill
    )
    n_zfo = int(zfo.sum())
    ev.stats["zero_flow_occupied_windows"] = n_zfo
    share = n_zfo / max(ev.n_valid, 1)
    if n_zfo and share >= th.zero_flow_occupied_suspect_share:
        ev.add(
            "zero_flow_occupied",
            "exclude" if share >= th.window_flag_exclude_share else "suspect",
            f"the loop is occupied {th.zero_flow_min_occupied_s:g} s or more with no vehicle "
            f"counted in {n_zfo} window(s) ({share:.1%} of the day) — a hanging-on loop or "
            f"missed counts",
            statistic=share,
            n_windows=n_zfo,
            mask={"flow": zfo, "occupancy": zfo, "speed": zfo},
        )
        fc = np.where(zfo, np.nan, fc)
        oc = np.where(zfo, np.nan, oc)
        vc = np.where(zfo, np.nan, vc)

    # -- zero counts ---------------------------------------------------------
    finite_c = np.isfinite(fc)
    mean_flow = float(np.nanmean(fc)) if finite_c.any() else None
    ev.stats["mean_flow_veh_h"] = mean_flow
    n_left = int(finite_c.sum())
    judged = n_left * dt >= th.day_judged_min_valid_s
    zero_day = judged and bool((fc[finite_c] == 0.0).all())
    ev.stats["zero_run_windows"] = 0
    if zero_day:
        ev.add(
            "zero_day",
            "exclude",
            f"no vehicle counted in {_duration(n_left * dt)} of readings — a dead detector, "
            f"or a closure (the counts cannot tell them apart; a closure must be declared)",
            statistic=float(n_left * dt),
        )
    else:
        zero = np.where(finite_c & (fc == 0.0), 1.0, np.nan)
        starts, lengths, _ = _value_runs(zero)
        expected_flow = np.where(np.isfinite(reference), reference, mean_flow or 0.0)
        flagged_starts: list[int] = []
        flagged_lengths: list[int] = []
        expected_runs: list[float] = []
        for s, length in zip(starts, lengths, strict=True):
            expected = float(np.sum(expected_flow[s : s + length])) * dt / 3600.0
            if expected >= th.zero_run_min_expected_veh:
                flagged_starts.append(int(s))
                flagged_lengths.append(int(length))
                expected_runs.append(expected)
        if flagged_starts:
            mask = _run_mask(n, flagged_starts, flagged_lengths)
            ev.stats["zero_run_windows"] = int(mask.sum())
            k = int(np.argmax(flagged_lengths))
            share = int(mask.sum()) / max(ev.n_valid, 1)
            ev.add(
                "zero_run",
                "exclude" if share >= th.window_flag_exclude_share else "suspect",
                f"no vehicle counted for {_duration(flagged_lengths[k] * dt)} from "
                f"{grid.window_label(flagged_starts[k])} where this detector usually counts "
                f"{expected_runs[k]:,.0f} ({len(flagged_starts)} run(s), {int(mask.sum())} "
                f"windows) — a dead loop, or a closure",
                statistic=share,
                n_windows=int(mask.sum()),
                mask={"flow": mask, "occupancy": mask, "speed": mask},
            )
            fc = np.where(mask, np.nan, fc)
            oc = np.where(mask, np.nan, oc)
            vc = np.where(mask, np.nan, vc)
        if judged and mean_flow is not None and mean_flow < th.low_flow_veh_h:
            ev.add(
                "low_flow",
                "suspect",
                f"mean flow {mean_flow:.1f} veh/h over {_duration(n_left * dt)} — below "
                f"{th.low_flow_veh_h:g} veh/h, too little for a lane or ramp in service",
                statistic=mean_flow,
            )

    # -- internally inconsistent records -------------------------------------
    if lanes > 0 and np.isfinite(oc).any():
        fwo = (
            np.isfinite(fc)
            & (fc / lanes >= th.occupancy_floor_flow_veh_h_lane)
            & np.isfinite(oc)
            & (oc == 0.0)
        )
        n_fwo = int(fwo.sum())
        ev.stats["flow_without_occupancy_windows"] = n_fwo
        if n_fwo:
            _, run_lengths, _ = _value_runs(np.where(fwo, 1.0, np.nan))
            ev.add(
                "flow_without_occupancy",
                "suspect",
                f"occupancy reads 0 while {th.occupancy_floor_flow_veh_h_lane:,.0f}+ veh/h per "
                f"lane pass in {n_fwo} window(s) (longest run {_duration(run_lengths.max() * dt)}) "
                f"— occupancy is not measured or its channel is dead; occupancy masked, counts kept",
                statistic=float(n_fwo),
                n_windows=n_fwo,
                mask={"occupancy": fwo},
            )
            oc = np.where(fwo, np.nan, oc)

    if lanes > 0:
        q_lane = fc / lanes
        count = q_lane * dt / 3600.0
        tested = (
            np.isfinite(q_lane)
            & (count >= th.implied_length_min_count_veh)
            & np.isfinite(oc)
            & (oc > 0.0)
            & np.isfinite(vc)
            & (vc > 0.0)
        )
        n_t = int(tested.sum())
        ev.stats["implied_length_tested"] = n_t
        if n_t:
            with np.errstate(divide="ignore", invalid="ignore"):
                length = np.where(tested, vc * (oc / 100.0) / (q_lane / 3600.0), np.nan)
            lo_l, hi_l = th.implied_length_band_m
            outside = tested & ((length < lo_l) | (length > hi_l))
            out_share = int(outside.sum()) / n_t
            ev.stats["implied_length_median_m"] = float(np.nanmedian(length[tested]))
            ev.stats["implied_length_outside_share"] = out_share
            if n_t * dt >= th.implied_length_min_tested_s and (
                out_share >= th.implied_length_suspect_share
            ):
                ev.add(
                    "implied_length",
                    "exclude" if out_share >= th.implied_length_exclude_share else "suspect",
                    f"flow, occupancy and speed imply a mean vehicle length outside "
                    f"{lo_l:g}-{hi_l:g} m in {out_share:.0%} of {n_t} tested windows (median "
                    f"{float(np.nanmedian(length[tested])):.1f} m) — one of the three is "
                    f"mis-scaled or wrong",
                    statistic=out_share,
                    n_windows=int(outside.sum()),
                )
    return ev


# ---------------------------------------------------------------------------
# Cross-day and cross-sensor checks
# ---------------------------------------------------------------------------


def _masked_values(
    grid: DetectorGrid, evals: Mapping[tuple[str, int], _DayEval]
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    """The grid's arrays with every finding's masks and every exclusion applied."""
    out: list[dict[str, np.ndarray]] = [{}, {}, {}]
    sources = (grid.flow_veh_h, grid.occupancy_pct, grid.speed_ms)
    for sid in grid.sensors:
        for q_index, quantity in enumerate(QUANTITIES):
            arr = sources[q_index][sid].copy()
            for d in range(len(grid.dates)):
                ev = evals[(sid, d)]
                if ev.verdict == "exclude":
                    arr[d] = np.nan
                else:
                    arr[d] = np.where(ev.masks[quantity], np.nan, arr[d])
            out[q_index][sid] = arr
    return out[0], out[1], out[2]


def _day_outliers(
    grid: DetectorGrid,
    flows: Mapping[str, np.ndarray],
    evals: dict[tuple[str, int], _DayEval],
    th: QualityThresholds,
) -> dict[str, dict[str, Any]]:
    """Day-outlier check on masked flows; returns the corridor day factors."""
    n_dates = len(grid.dates)
    ratios: dict[str, np.ndarray] = {}
    for sid in grid.sensors:
        values = flows[sid]
        reference = _profile(values, th.min_profile_days)
        r = np.full(n_dates, np.nan)
        for d in range(n_dates):
            comparable = np.isfinite(values[d]) & np.isfinite(reference)
            if comparable.sum() < th.day_min_compared_share * grid.n_windows:
                continue
            denominator = float(reference[comparable].sum())
            if denominator > 0.0:
                r[d] = float(values[d][comparable].sum()) / denominator
        ratios[sid] = r
    factors: dict[str, dict[str, Any]] = {}
    lo, hi = th.day_ratio_suspect_band
    for d, date in enumerate(grid.dates):
        column = np.array([ratios[s][d] for s in grid.sensors], dtype=float)
        column = column[np.isfinite(column)]
        if column.size >= th.min_sensors_for_corridor_factor:
            factor = float(np.median(column))
            factors[date] = {
                "factor": factor,
                "n_sensors": int(column.size),
                "unusual": not lo <= factor <= hi,
            }
        else:
            factors[date] = {"factor": None, "n_sensors": int(column.size), "unusual": False}
    for sid in grid.sensors:
        for d, date in enumerate(grid.dates):
            r = ratios[sid][d]
            ev = evals[(sid, d)]
            if not math.isfinite(r):
                continue
            factor = factors[date]["factor"]
            relative = r / factor if factor else r
            ev.stats["day_ratio"] = r
            ev.stats["day_ratio_relative"] = relative
            verdict = _band_verdict(relative, th.day_ratio_suspect_band, th.day_ratio_exclude_band)
            if verdict != "ok":
                corridor = (
                    f"corridor-wide day factor {factor:.2f}"
                    if factor
                    else "no corridor-wide day factor (too few sensors)"
                )
                ev.add(
                    "day_outlier",
                    verdict,
                    f"the day's volume is {relative:.2f}x this detector's median day ({corridor}) "
                    f"— a lost or doubled lane, a recalibration, or a local event",
                    statistic=relative,
                )
    return factors


def _lane_imbalance(
    grid: DetectorGrid,
    flows: Mapping[str, np.ndarray],
    evals: dict[tuple[str, int], _DayEval],
    th: QualityThresholds,
    exempt: frozenset[str],
) -> None:
    """Lane check on a per-lane grid's masked flows (no-op for a station grid)."""
    if not grid.per_lane:
        return
    dt = grid.interval_s
    for _station, members in sorted(grid.stations().items()):
        if len(members) < 2:
            continue
        stack = np.stack([flows[m] for m in members])  # (lanes, dates, windows)
        n_lanes = len(members)
        for d in range(len(grid.dates)):
            day = stack[:, d, :]
            complete = np.isfinite(day).all(axis=0)
            busy = complete & (
                np.where(complete, day.sum(axis=0), 0.0) / n_lanes >= th.lane_min_flow_veh_h_lane
            )
            if busy.sum() * dt < th.lane_min_tested_s:
                continue
            totals = day[:, busy].sum(axis=1)
            grand = float(totals.sum())
            for i, sid in enumerate(members):
                others = (grand - float(totals[i])) / (n_lanes - 1)
                if others <= 0.0:
                    continue
                ratio = float(totals[i]) / others
                ev = evals[(sid, d)]
                ev.stats["lane_ratio"] = ratio
                if sid in exempt:
                    continue
                verdict = _band_verdict(
                    ratio, th.lane_ratio_suspect_band, th.lane_ratio_exclude_band
                )
                if verdict == "exclude" and n_lanes == 2:
                    verdict = "suspect"
                if verdict != "ok":
                    extra = (
                        " (two-lane station: the imbalance cannot be laid on one lane)"
                        if n_lanes == 2
                        else ""
                    )
                    ev.add(
                        "lane_imbalance",
                        verdict,
                        f"this lane carries {ratio:.2f}x the mean of the station's other "
                        f"{n_lanes - 1} lane(s) over {int(busy.sum())} busy windows{extra} — a "
                        f"degraded or double-counting loop, unless it is an auxiliary or "
                        f"exit-only lane (declare those as exempt)",
                        statistic=ratio,
                    )


@dataclass(frozen=True)
class _BalanceKeys:
    """The fields of a :class:`SegmentDayBalance` known before it is judged."""

    upstream: str
    downstream: str
    date: str
    n_periods: int
    n_valid_periods: int
    measured_ramps: tuple[str, ...]
    unmeasured_ramps: tuple[str, ...]
    period_residual_veh_h: tuple[float | None, ...]

    def record(
        self,
        *,
        verdict: str,
        explanation: str,
        residual_veh_h: float | None,
        residual_share: float | None,
        tolerance_share: float | None,
        share_periods_beyond_tolerance: float | None,
    ) -> SegmentDayBalance:
        return SegmentDayBalance(
            upstream=self.upstream,
            downstream=self.downstream,
            date=self.date,
            verdict=verdict,
            explanation=explanation,
            n_periods=self.n_periods,
            n_valid_periods=self.n_valid_periods,
            residual_veh_h=residual_veh_h,
            residual_share=residual_share,
            tolerance_share=tolerance_share,
            share_periods_beyond_tolerance=share_periods_beyond_tolerance,
            measured_ramps=self.measured_ramps,
            unmeasured_ramps=self.unmeasured_ramps,
            period_residual_veh_h=self.period_residual_veh_h,
        )


def _mass_balance(
    grid: DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
    extra_ramps: Sequence[RampSpec],
    th: QualityThresholds,
    count_error: float,
    combination: Combination,
    period_s: float,
    notes: list[str],
) -> tuple[CorridorLayout | None, list[SegmentDayBalance], list[SegmentSummary]]:
    """Station-to-station balance on a masked station grid (module docstring)."""
    try:
        layout = corridor_layout(grid, stations=stations, extra_ramps=extra_ramps)
    except ValueError as exc:
        notes.append(f"mass balance not run: {exc}")
        return None, [], []
    records: list[SegmentDayBalance] = []
    summaries: list[SegmentSummary] = []
    for seg in layout.segments:
        aligned = align_segment(grid, seg, period_s=period_s)
        n_periods = aligned.n_periods
        seg_records: list[SegmentDayBalance] = []
        for d, date in enumerate(grid.dates):
            day_measured = [
                r for r in seg.measured() if bool(np.isfinite(grid.flow_veh_h[r.id][d]).any())
            ]
            day_unmeasured = [r for r in seg.ramps if r not in day_measured]
            up, down = aligned.up[d], aligned.down[d]
            ramp_series = [aligned.ramps[r.id][d] for r in day_measured]
            residual = down - up
            for r, series in zip(day_measured, ramp_series, strict=True):
                residual = residual - r.sign * series
            valid = np.isfinite(residual)
            n_valid = int(valid.sum())
            base = _BalanceKeys(
                upstream=seg.upstream,
                downstream=seg.downstream,
                date=date,
                n_periods=n_periods,
                n_valid_periods=n_valid,
                measured_ramps=tuple(r.id for r in day_measured),
                unmeasured_ramps=tuple(r.id for r in day_unmeasured),
                period_residual_veh_h=tuple(
                    float(x) if math.isfinite(x) else None for x in residual
                ),
            )
            upstream_total = float(up[valid].sum()) if n_valid else 0.0
            if (
                n_periods == 0
                or n_valid < th.mass_balance_min_evaluated_share * n_periods
                or upstream_total <= 0.0
            ):
                seg_records.append(
                    base.record(
                        verdict="not_evaluated",
                        explanation=(
                            f"only {n_valid} of {n_periods} periods have every term measured"
                            if upstream_total > 0.0 or not n_valid
                            else "no upstream flow in the complete periods"
                        ),
                        residual_veh_h=None,
                        residual_share=None,
                        tolerance_share=None,
                        share_periods_beyond_tolerance=None,
                    )
                )
                continue
            totals = [float(up[valid].sum()), float(down[valid].sum())]
            totals += [float(s[valid].sum()) for s in ramp_series]
            tol_day = float(
                count_tolerance([np.array(t) for t in totals], count_error, combination)
            )
            period_tol = count_tolerance([up, down, *ramp_series], count_error, combination)
            total_residual = float(residual[valid].sum())
            share = total_residual / totals[0]
            tol_share = tol_day / totals[0]
            beyond = float((np.abs(residual[valid]) > period_tol[valid]).mean())
            ons = [r for r in day_unmeasured if r.kind == "on_ramp"]
            offs = [r for r in day_unmeasured if r.kind == "off_ramp"]
            outside = abs(total_residual) > tol_day
            mean_res = total_residual / n_valid
            if not day_unmeasured:
                verdict = "suspect" if outside else "ok"
                explanation = (
                    f"unexplained residual {mean_res:+,.0f} veh/h ({share:+.1%} of upstream "
                    f"flow, band ±{tol_share:.1%}) with every ramp measured — a bad detector "
                    f"or a ramp missing from the inventory"
                    if outside
                    else f"balances within the count-error band (residual {mean_res:+,.0f} veh/h)"
                )
            elif ons and not offs:
                verdict = "suspect" if total_residual < -tol_day else "ok"
                explanation = (
                    f"residual {mean_res:+,.0f} veh/h is negative, but the only unmeasured "
                    f"ramps are entrances ({', '.join(r.id for r in ons)}) — a bad detector"
                    if verdict == "suspect"
                    else f"residual {mean_res:+,.0f} veh/h is the unmeasured entrance flow"
                )
            elif offs and not ons:
                verdict = "suspect" if total_residual > tol_day else "ok"
                explanation = (
                    f"residual {mean_res:+,.0f} veh/h is positive, but the only unmeasured "
                    f"ramps are exits ({', '.join(r.id for r in offs)}) — a bad detector"
                    if verdict == "suspect"
                    else f"residual {mean_res:+,.0f} veh/h is the unmeasured exit flow"
                )
            else:
                verdict = "ok"
                explanation = (
                    f"residual {mean_res:+,.0f} veh/h is the net of unmeasured entrances and "
                    f"exits; it cannot be judged"
                )
            seg_records.append(
                base.record(
                    verdict=verdict,
                    explanation=explanation,
                    residual_veh_h=mean_res,
                    residual_share=share,
                    tolerance_share=tol_share,
                    share_periods_beyond_tolerance=beyond,
                )
            )
        records.extend(seg_records)
        judged = [r for r in seg_records if r.verdict != "not_evaluated"]
        flagged = [r for r in judged if r.verdict == "suspect"]
        shares = [r.residual_share for r in judged if r.residual_share is not None]
        summaries.append(
            SegmentSummary(
                upstream=seg.upstream,
                downstream=seg.downstream,
                length_m=seg.length_m,
                n_days_evaluated=len(judged),
                n_days_suspect=len(flagged),
                persistent=bool(judged)
                and len(flagged) >= th.mass_balance_persistent_share * len(judged),
                median_residual_share=float(np.median(shares)) if shares else None,
                ramps=tuple(
                    f"{r.id} ({r.kind}, {'measured' if r.measured else 'unmeasured'})"
                    for r in seg.ramps
                ),
            )
        )
    return layout, records, summaries


def _attributions(
    records: Sequence[SegmentDayBalance], th: QualityThresholds
) -> list[dict[str, Any]]:
    """Stations named by opposite, similar residuals either side (module docstring)."""
    by_day: dict[str, list[SegmentDayBalance]] = {}
    for rec in records:
        by_day.setdefault(rec.date, []).append(rec)
    out: list[dict[str, Any]] = []
    lo, hi = th.attribution_ratio_band
    for date, recs in sorted(by_day.items()):
        by_upstream = {r.upstream: r for r in recs}
        for above in recs:
            below = by_upstream.get(above.downstream)
            if below is None:
                continue
            pair = (above, below)
            if not all(
                r.verdict == "suspect" and not r.unmeasured_ramps and r.residual_veh_h is not None
                for r in pair
            ):
                continue
            a, b = float(above.residual_veh_h or 0.0), float(below.residual_veh_h or 0.0)
            if a * b >= 0.0 or b == 0.0:
                continue
            ratio = abs(a) / abs(b)
            if lo <= ratio <= hi:
                out.append(
                    {
                        "station": above.downstream,
                        "date": date,
                        "residual_above_veh_h": _num(a, 1),
                        "residual_below_veh_h": _num(b, 1),
                        "direction": "undercounts" if a < 0 else "overcounts",
                    }
                )
    return out


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def assess_quality(
    data: pd.DataFrame | DetectorGrid,
    *,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    extra_ramps: Iterable[RampSpec] = (),
    thresholds: QualityThresholds | None = None,
    count_error: float = DEFAULT_COUNT_ERROR,
    combination: Combination = "linear",
    period_s: float = DEFAULT_PERIOD_S,
    exempt_lanes: Iterable[str] = (),
    mass_balance: bool = True,
    dates: Sequence[str] | None = None,
    start_local: str | None = None,
    end_local: str | None = None,
) -> DataQualityReport:
    """Judge every detector-day and check the station-to-station balance.

    Args:
        data: Tidy detector frame or a :class:`DetectorGrid`.
        stations: Optional stations table (positions, kinds, ramps without
            detectors) for the mass balance.
        extra_ramps: Ramps known to exist without a detector.
        thresholds: The rules (default :class:`QualityThresholds`).
        count_error: Relative count error per detector for the balance band.
        combination: ``linear`` (bound) or ``quadrature`` (independent).
        period_s: Mass-balance period [s].
        exempt_lanes: Sensor ids exempt from the lane check (auxiliary or
            exit-only lanes).
        mass_balance: Run the station-to-station balance.
        dates: Dates to keep (frame input only).
        start_local: Span start (frame input only).
        end_local: Span end (frame input only).

    Returns:
        The :class:`DataQualityReport`.
    """
    th = thresholds or QualityThresholds()
    grid = (
        data
        if isinstance(data, DetectorGrid)
        else detector_grid(data, dates=dates, start_local=start_local, end_local=end_local)
    )
    exempt = frozenset(str(s) for s in exempt_lanes)
    if unknown := sorted(exempt - set(grid.sensors)):
        raise ValueError(f"exempt_lanes {unknown} are not sensors of the data")
    notes: list[str] = []
    no_lanes = sorted(s for s, info in grid.sensors.items() if info.lanes <= 0)
    if no_lanes:
        notes.append(
            "lane count not stated for "
            + ", ".join(no_lanes)
            + ": the per-lane flow ceiling, the zero-occupancy test and the implied-length "
            "test did not run for them"
        )
    evals: dict[tuple[str, int], _DayEval] = {}
    for sid, info in grid.sensors.items():
        f_all = grid.flow_veh_h[sid]
        lanes = info.lanes
        impossible = f_all < 0.0
        if lanes > 0:
            impossible |= f_all / lanes > th.flow_ceiling_veh_h_lane
        clean = np.where(impossible, np.nan, f_all)
        reference = _profile(clean, th.min_profile_days)
        for d in range(len(grid.dates)):
            evals[(sid, d)] = _evaluate_day(
                f_all[d],
                grid.occupancy_pct[sid][d],
                grid.speed_ms[sid][d],
                info=info,
                grid=grid,
                reference=reference,
                th=th,
            )
    flows, _, _ = _masked_values(grid, evals)
    factors = _day_outliers(grid, flows, evals, th)
    if len(grid.dates) < th.min_profile_days:
        notes.append(
            f"{len(grid.dates)} date(s): the day-outlier check and the time-of-day reference "
            f"of the zero-run check need {th.min_profile_days}"
        )
    flows, _, _ = _masked_values(grid, evals)
    _lane_imbalance(grid, flows, evals, th, exempt)
    if not grid.per_lane:
        notes.append("station totals only: the lane-imbalance check needs per-lane data")
    if (silent := silent_lane_note(grid)) is not None:
        notes.append(silent)

    layout: CorridorLayout | None = None
    balance: list[SegmentDayBalance] = []
    summaries: list[SegmentSummary] = []
    attributions: list[dict[str, Any]] = []
    if mass_balance:
        flows, occs, speeds = _masked_values(grid, evals)
        masked = station_grid(grid.replace_values(flows, occs, speeds))
        layout, balance, summaries = _mass_balance(
            masked,
            stations=stations,
            extra_ramps=list(extra_ramps),
            th=th,
            count_error=count_error,
            combination=combination,
            period_s=period_s,
            notes=notes,
        )
        attributions = _attributions(balance, th)
        date_index = {d: i for i, d in enumerate(grid.dates)}
        members = grid.stations()
        for att in attributions:
            for sid in members.get(att["station"], []):
                evals[(sid, date_index[att["date"]])].add(
                    "mass_balance_pattern",
                    "suspect",
                    f"the segments either side of station {att['station']} have opposite "
                    f"residuals ({att['residual_above_veh_h']:+,.0f} / "
                    f"{att['residual_below_veh_h']:+,.0f} veh/h): the station "
                    f"{att['direction']} — the likely cause",
                    statistic=att["residual_above_veh_h"],
                )

    flows, _, _ = _masked_values(grid, evals)
    sensor_days: list[SensorDayQuality] = []
    for sid, info in grid.sensors.items():
        for d, date in enumerate(grid.dates):
            ev = evals[(sid, d)]
            verdict = ev.verdict
            masked_idx: dict[str, tuple[int, ...]] = {}
            if verdict == "suspect":
                masked_idx = {
                    q: tuple(int(i) for i in np.flatnonzero(ev.masks[q])) for q in QUANTITIES
                }
            sensor_days.append(
                SensorDayQuality(
                    sensor=sid,
                    station=info.station,
                    lane=info.lane,
                    kind=info.kind,
                    date=date,
                    verdict=verdict,
                    n_expected=grid.n_windows,
                    n_valid=ev.n_valid,
                    n_usable=int(np.isfinite(flows[sid][d]).sum()),
                    stats=dict(ev.stats),
                    findings=tuple(ev.findings),
                    masked=masked_idx,
                )
            )
    return DataQualityReport(
        interval_s=grid.interval_s,
        start_s=grid.start_s,
        n_windows=grid.n_windows,
        dates=grid.dates,
        per_lane=grid.per_lane,
        thresholds=th,
        count_error=float(count_error),
        combination=str(combination),
        period_s=float(period_s),
        exempt_lanes=tuple(sorted(exempt)),
        sensors=tuple(grid.sensors.values()),
        sensor_days=tuple(sensor_days),
        corridor_day_factors=factors,
        layout=layout,
        mass_balance=tuple(balance),
        segments=tuple(summaries),
        attributions=tuple(attributions),
        notes=tuple(notes),
    )


def mask_grid(grid: DetectorGrid, report: DataQualityReport) -> DetectorGrid:
    """Apply a report's verdicts: excluded sensor-days and masked windows → NaN.

    Nothing is filled in; the result has fewer finite values, never different
    ones.

    Args:
        grid: The grid the report was computed on (same sensors and span).
        report: The report.

    Returns:
        The masked grid.

    Raises:
        ValueError: The grid's window, span or dates differ from the report's,
            or the report names a sensor the grid lacks.
    """
    if (
        grid.interval_s != report.interval_s
        or grid.start_s != report.start_s
        or grid.n_windows != report.n_windows
        or grid.dates != report.dates
    ):
        raise ValueError("mask_grid: the grid is not the one the report was computed on")
    date_index = {d: i for i, d in enumerate(grid.dates)}
    arrays = {
        "flow": {k: v.copy() for k, v in grid.flow_veh_h.items()},
        "occupancy": {k: v.copy() for k, v in grid.occupancy_pct.items()},
        "speed": {k: v.copy() for k, v in grid.speed_ms.items()},
    }
    for sd in report.sensor_days:
        if sd.sensor not in grid.sensors:
            raise ValueError(f"mask_grid: the report names sensor {sd.sensor!r}, not in the grid")
        d = date_index[sd.date]
        for quantity in QUANTITIES:
            target = arrays[quantity][sd.sensor]
            if sd.verdict == "exclude":
                target[d] = np.nan
            else:
                idx = list(sd.masked.get(quantity, ()))
                if idx:
                    target[d, idx] = np.nan
    return grid.replace_values(arrays["flow"], arrays["occupancy"], arrays["speed"])


# ---------------------------------------------------------------------------
# Plain-language summary
# ---------------------------------------------------------------------------


def _main_reasons(days: Sequence[SensorDayQuality], verdict: str) -> str:
    """The most frequent checks behind ``verdict`` across ``days``."""
    counts: dict[str, int] = {}
    for sd in days:
        for f in sd.findings:
            if f.verdict == verdict:
                counts[f.check] = counts.get(f.check, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{check} ({n} day{'s' if n != 1 else ''})" for check, n in ranked)


def render_markdown(
    report: DataQualityReport,
    *,
    title: str = "Detector data quality",
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """A plain-language summary for a client (what is usable, what was dropped, why).

    Args:
        report: The report.
        title: Heading.
        provenance: Optional key → value lines (inputs, commit, …) printed
            under the heading.

    Returns:
        Markdown text.
    """
    s = report.summary()
    lines = [f"# {title}", ""]
    for key, value in (provenance or {}).items():
        lines.append(f"- **{key}**: {value}")
    if provenance:
        lines.append("")
    window_min = report.interval_s / 60.0
    span = f"{clock_text(report.start_s)}–{clock_text(report.start_s + report.n_windows * report.interval_s)}"
    unit = "lane detectors" if report.per_lane else "detector stations"
    usable = s["usable_share"] or 0.0
    valid = s["valid_share"] or 0.0
    lines += [
        "## Summary",
        "",
        f"{s['n_sensors']} {unit} over {s['n_dates']} day(s) ({span} local, "
        f"{window_min:g}-minute readings): {s['n_sensor_days']} detector-days.",
        "",
        f"- **{s['n_ok']}** detector-days passed every check, **{s['n_suspect']}** are usable "
        f"with caveats (the readings a finding names are set aside), **{s['n_exclude']}** "
        f"were dropped.",
        f"- The detectors delivered {valid:.1%} of the readings the period should contain; "
        f"after the checks, **{usable:.1%}** are usable.",
        "- Nothing was filled in: a dropped or set-aside reading is simply absent, and no "
        "missing value was estimated (imputation is out of scope).",
        "",
    ]
    by_sensor: dict[str, list[SensorDayQuality]] = {}
    for sd in report.sensor_days:
        by_sensor.setdefault(sd.sensor, []).append(sd)
    dropped = {k: v for k, v in by_sensor.items() if any(sd.verdict == "exclude" for sd in v)}
    caveat = {
        k: v
        for k, v in by_sensor.items()
        if k not in dropped and any(sd.verdict == "suspect" for sd in v)
    }
    lines += ["## Detectors dropped (on at least one day)", ""]
    if dropped:
        lines += ["| Detector | Kind | Days dropped | Why |", "|---|---|---|---|"]
        for sid, days in sorted(dropped.items()):
            n_ex = sum(1 for sd in days if sd.verdict == "exclude")
            example = next(f.reason for sd in days for f in sd.findings if f.verdict == "exclude")
            lines.append(
                f"| {sid} | {days[0].kind} | {n_ex} of {len(days)} | "
                f"{_main_reasons(days, 'exclude')}. Example: {example} |"
            )
    else:
        lines.append("None.")
    lines += ["", "## Detectors kept with caveats", ""]
    if caveat:
        lines += ["| Detector | Kind | Days with caveats | Findings |", "|---|---|---|---|"]
        for sid, days in sorted(caveat.items()):
            n_su = sum(1 for sd in days if sd.verdict == "suspect")
            lines.append(
                f"| {sid} | {days[0].kind} | {n_su} of {len(days)} | "
                f"{_main_reasons(days, 'suspect')} |"
            )
    else:
        lines.append("None.")
    lines += ["", "## Station-to-station balance", ""]
    if report.layout is None:
        lines.append(next((n for n in report.notes if n.startswith("mass balance")), "Not run."))
    else:
        lines += [
            "Between two neighbouring stations, the vehicles counted downstream should equal "
            "those counted upstream plus the entrances minus the exits in between, within the "
            f"detectors' stated accuracy (±{report.count_error:.0%} each). A day whose "
            "imbalance leaves that band with every ramp measured points at a faulty detector or "
            "a ramp missing from the inventory.",
            "",
            "| Segment | Length | Ramps | Days judged | Days unexplained | Median residual |",
            "|---|---|---|---|---|---|",
        ]
        for seg in report.segments:
            median = (
                f"{seg.median_residual_share:+.1%}"
                if seg.median_residual_share is not None
                else "—"
            )
            flag = " **persistent**" if seg.persistent else ""
            lines.append(
                f"| {seg.upstream}→{seg.downstream} | {seg.length_m:,.0f} m | "
                f"{'; '.join(seg.ramps) or 'none'} | {seg.n_days_evaluated} | "
                f"{seg.n_days_suspect}{flag} | {median} |"
            )
        if report.attributions:
            lines += ["", "Stations named as the likely cause of opposite residuals:", ""]
            for att in report.attributions:
                lines.append(
                    f"- {att['station']} on {att['date']}: {att['direction']} "
                    f"({att['residual_above_veh_h']:+,.0f} veh/h above, "
                    f"{att['residual_below_veh_h']:+,.0f} veh/h below)"
                )
        if report.layout.outside_ramps:
            lines += [
                "",
                "Ramps outside the stations' span (not in any balance): "
                + ", ".join(r.id for r in report.layout.outside_ramps),
            ]
    unusual = {d: f for d, f in report.corridor_day_factors.items() if f.get("unusual")}
    lines += ["", "## Unusual days across the corridor", ""]
    if unusual:
        lines.append(
            "These days differ from a typical day at most detectors at once — a holiday, "
            "weather or an event, not a sensor fault. They are not dropped; decide whether "
            "they belong in the analysis:"
        )
        lines.append("")
        for d, f in sorted(unusual.items()):
            lines.append(f"- {d}: {f['factor']:.2f}x a typical day ({f['n_sensors']} detectors)")
    else:
        lines.append("None found.")
    lines += ["", "## What was checked", ""]
    for check in CHECKS:
        lines.append(f"- **{check}**: {CHECK_DESCRIPTIONS[check]}")
    lines += [
        "",
        "Day-level judgement follows the PeMS Daily Statistics Algorithm (Chen, Kwon, Rice, "
        "Skabardonis & Varaiya 2003, TRR 1855); its thresholds were set for 30-second samples "
        "and are not reused. Every threshold here is a named constant with a stated reason:",
        "",
        "| Threshold | Value | Source or reason |",
        "|---|---|---|",
    ]
    for key, value in report.thresholds.to_dict().items():
        shown = ", ".join(f"{x:g}" for x in value) if isinstance(value, list) else f"{value:g}"
        lines.append(f"| {key} | {shown} | {THRESHOLD_SOURCES.get(key, '')} |")
    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {n}" for n in report.notes]
    lines += [
        "",
        "## Limits",
        "",
        "- A road or ramp closure reads exactly like a dead detector; closures must be declared, "
        "the counts cannot reveal them.",
        f"- Detector accuracy (±{report.count_error:.0%}) is an assumption, not a measurement of these detectors.",
        "- The station balance includes the change in the number of vehicles between the "
        "stations, which cancels over a day that starts and ends in free flow but not over a "
        "span that ends inside a queue.",
        "",
    ]
    return "\n".join(lines)
