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
counts samples with occupancy = 0 (S1 — the loop detects nothing), samples
with occupancy > 0 and flow = 0 (S2), samples with occupancy above k* = 0.35
(S3), and the entropy of the occupancy samples (S4, low for a constant
series), and declares the detector bad for the day when any count exceeds an
empirically chosen threshold. Those thresholds were set for 30-second samples and are not used
here; where a check below follows one of S1–S4 its docstring says which, and
every threshold states its own reason. Checks the DSA does not cover (flow
ceilings, implied vehicle length, day outliers, lane imbalance, mass balance)
cite no source for their numbers and state a reason instead.

**Standing queues are traffic, not faults.** A window with no vehicle
counted is judged by its occupancy, never by its speed: with nothing crossing
the loop a window mean speed is undefined (MnDOT reports none; other sources
carry a stale or default value), so the speed field of a zero-count window is
not read. Such a window is

* **empty** — occupancy at most :data:`EMPTY_LOOP_OCCUPANCY_PCT`: nothing over
  the loop. Where this detector usually counts traffic, a run of empty windows
  is a dead loop or a closure (``zero_run``, the failure Chen et al.'s S1
  counts: occupancy = 0);
* **occupied** — the loop covered :data:`ZERO_FLOW_MIN_OCCUPIED_S` or more
  (S2's occupancy > 0 with flow = 0). It is a **standstill** — a vehicle
  standing on the loop, not flagged — when either (a) its stretch of
  consecutive occupied zero-count windows lasts at most
  :data:`STANDSTILL_MAX_S` with a mean occupancy of at least
  :data:`STANDSTILL_MIN_OCCUPANCY_PCT` (a stop-and-go jam passing the loop),
  or (b) a neighbouring sensor reads congestion within
  :data:`QUEUE_CONTEXT_S` of the window: another lane of the same station or,
  for a mainline sensor, the nearest mainline station upstream or downstream,
  with occupancy at least :data:`CONGESTED_OCCUPANCY_PCT` or a vehicle counted
  below :data:`calibration.conservation.CONGESTED_SPEED_MS`. An occupied
  window that is neither is a hanging-on loop or missed counts
  (``zero_flow_occupied``): occupancy held high with no count while the
  neighbours flow freely;
* **unreported** — no occupancy (a count-only source): judged as empty unless
  a neighbour reads congestion at the time (b).

**Verdicts.** ``exclude`` drops the whole sensor-day (the DSA's day-level
logic: once a detector is shown to malfunction on a day its plausible-looking
readings that day are not trusted either). ``suspect`` keeps the day but
masks the windows the finding names (only those, and only the quantity it
names — a frozen occupancy does not cost the counts). A sensor-day's verdict
is the worst of its findings. :func:`mask_grid` applies the verdicts to a
grid, :func:`mask_frame` to a tidy frame (the observed targets are masked this
way before they are averaged over dates, docs/FRISCO_PROTOCOL.md §2.2);
:class:`QualityVerdicts` reads them from a report or from its JSON.

**Lane order.** A station whose detector lane labels are out of order passes
every check above: its counts are good, only filed under the wrong lane (the
I-94 WB station S791, whose loop labelled lane 1 reads the leftmost lane —
docs/I94_LANE_SHARES.md §3). Lane use is not independent from one station to
the next: as the flow rises traffic spreads from the outer lanes to the inner
ones and back, so a station's per-lane share series rises and falls with the
same-numbered lanes of its neighbours and against the mirrored ones. The
check (``lane_order``, :func:`lane_order_check`) correlates every per-lane
station's share series with those of the nearest station on either side that
has the same lane count (5-minute windows, pooled over the dates): a station
that reads *mirrored* against every neighbour it can be read against, where
those neighbours are themselves anchored (two of them, or one that agrees
with a third station), and whose two supporting signatures — occupancy per
vehicle and the share of light traffic, lane 1 against the last lane — both
point the other way from the neighbours', is ``reversed``. Its sensor-days
are ``suspect`` with nothing masked (the counts are good) and the report
records ``lanes_reversed`` so a consumer may remap lane k to lane n + 1 − k;
nothing here remaps. A mirrored reading short of that is ``uncertain`` —
flagged, never remapped. A station with no comparable neighbour is ``not
checkable``, which is not a pass. Lane numbers come from the source
(``lane_numbers``: the IRIS lane of each MnDOT detector, 1 = rightmost) or, for
a generic per-lane CSV, from lane ids that are the numbers 1..n.

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

import hashlib
import json
import math
import warnings
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Final, Literal, cast

import numpy as np
import pandas as pd

from calibration.conservation import (
    CONGESTED_SPEED_MS,
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
    normalize_date,
    sensor_id,
    silent_lane_note,
    station_grid,
)
from calibration.lane_totals import counted_lanes
from calibration.loaders.detector_csv import detector_interval_s, local_dates, local_seconds
from calibration.observations import parse_clock
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
    "lane_order",
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
"""Expected vehicles over a run of empty zero-count windows at which the run
is flagged.

Reason: the expectation is this detector's own median count at the same
time of day over the dates (:data:`MIN_PROFILE_DAYS`), so a quiet night is
expected to read zero and is left alone; under a Poisson count the
probability of no vehicle where 20 are expected is e⁻²⁰ ≈ 2·10⁻⁹. Only windows
whose loop is empty (:data:`EMPTY_LOOP_OCCUPANCY_PCT`) or whose occupancy is
unreported with no congestion next to it enter a run: the failure Chen et
al.'s S1 counts is samples with occupancy = 0, a loop that detects nothing —
not a loop with a vehicle standing on it. A full road closure reads the same
as a dead loop."""

EMPTY_LOOP_OCCUPANCY_PCT: Final[float] = 1.0
"""Highest occupancy of a zero-count window whose loop is empty [percent].

Reason: a loop with nothing over it reads 0 % (Chen et al. 2003, S1:
occupancy = 0). 1 % leaves room for a published percentage rounded up from a
trace and for the presence of a vehicle counted just before the window that
spills across its boundary (a 7 m effective length at 5 m/s covers the loop
1.4 s: under 1 % of a 5-minute window; in 30-second data the one spilled
window only shortens the run)."""

STANDSTILL_MIN_OCCUPANCY_PCT: Final[float] = 50.0
"""Least mean occupancy of a stretch of zero-count windows for it to be a
vehicle standing on the loop [percent].

Reason: moving traffic that covers the loop half the time cannot cross it
without a count: at occupancy o and speed v a lane carries q = o·v/L, which at
o = 50 %, v = 1 m/s and L = 7 m is still one vehicle every 14 s. A stretch
covered more than half the time with nothing counted is a vehicle stopped on
the loop."""

STANDSTILL_MAX_S: Final[float] = 300.0
"""Longest stretch of occupied zero-count windows accepted as a standstill
without a neighbour's confirmation [s] (5 min).

Reason: a vehicle stands on a loop for as long as the stopped part of a jam
takes to pass it, the jam's width over its speed; at the empirical
stop-and-go wave speed of about 20 km/h (CLAUDE.md §7.1: 14–22 km/h) five
minutes is a standing jam 1.7 km wide. A longer stop is a full stoppage whose
queue reaches the neighbouring lanes and stations, and is accepted only when
one of them reads congestion (:data:`QUEUE_CONTEXT_S`)."""

CONGESTED_OCCUPANCY_PCT: Final[float] = 20.0
"""Occupancy at or above which a neighbouring sensor reads congestion
[percent].

Reason: past the capacity point. Occupancy is q·L/v; at a lane capacity of
2,200 veh/h, 25 m/s (90 km/h) and a 6.5 m effective length it is 16 %, so a
window above 20 % carries more vehicles per metre than any free-flowing lane
— queued traffic."""

QUEUE_CONTEXT_S: Final[float] = 300.0
"""How far before and after a zero-count window a neighbour's congestion
counts for it [s].

Reason: a jam front moves about 20 km/h (5.6 m/s, CLAUDE.md §7.1), so a queue
standing on one loop reaches or leaves a station 1.7 km away within 5
minutes — wider than the station spacing of an instrumented freeway; in
5-minute data it is the adjacent window."""

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
"""Occupied time in a window with no vehicle counted that needs explaining [s].

Reason: a vehicle detected just before a window boundary spills its
occupancy into the next window, but a 7 m effective length at 5 m/s occupies
the loop 1.4 s in all; 5 s of occupancy with no count is a vehicle standing
on the loop (a standstill, module docstring), a hanging-on loop or a missed
count (Chen et al.'s S2: occupancy > 0 with flow = 0)."""

ZERO_FLOW_OCCUPIED_SUSPECT_SHARE: Final[float] = 0.01
"""Share of a day's windows occupied with no count that neither a standstill
nor a neighbour's congestion explains at which the day is suspect. Reason
(convention): one or two unexplained windows are a boundary effect or a
standstill no neighbour saw; more than one window in a hundred is a fault."""

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

LANE_ORDER_WINDOW_S: Final[float] = 300.0
"""Window over which the lane-order check forms lane shares [s]; finer data
are summed to it. Reason: a 30-s window carries a handful of vehicles per lane,
whose shares are noise; five minutes is the standard reporting window."""

LANE_ORDER_MIN_VEH: Final[float] = 60.0
"""Vehicles a station must count in a window for its lane shares to enter the
lane-order correlation. Reason: a lane share's binomial standard deviation is
then at most √(0.25/60) ≈ 6.5 percentage points, below the 10–30 point swing
of lane use between light and heavy traffic the check reads."""

LANE_ORDER_MIN_WINDOWS: Final[int] = 48
"""Windows (pooled over dates) a pair of stations must share, and windows a
supporting signature needs, before either is read. Reason: four hours of
5-minute windows; a correlation of 0 over 48 windows has a standard error of
1/√48 ≈ 0.14."""

LANE_ORDER_MIN_CORRELATION: Final[float] = 0.3
"""Mean correlation a pair's reading (same-numbered or mirrored lanes) must
reach, and the margin by which it must exceed the other reading. Reason: two
standard errors of a zero correlation over 48 windows; the shared lane-use
pattern of a freeway corridor reads 0.6-0.9 (docs/I94_LANE_SHARES.md §3)."""

LANE_ORDER_MIN_CONTRAST: Final[float] = 0.10
"""Relative difference between lane 1 and the last lane below which a
supporting signature (occupancy per vehicle, light-traffic share) gives no
direction. Reason (convention): loop sensitivities differ by about this much
(the IRIS field lengths of one station's loops differ by up to 15 %), which is
why these signatures only support the correlation and never decide alone."""

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
    empty_loop_occupancy_pct: float = EMPTY_LOOP_OCCUPANCY_PCT
    zero_flow_min_occupied_s: float = ZERO_FLOW_MIN_OCCUPIED_S
    standstill_min_occupancy_pct: float = STANDSTILL_MIN_OCCUPANCY_PCT
    standstill_max_s: float = STANDSTILL_MAX_S
    congested_occupancy_pct: float = CONGESTED_OCCUPANCY_PCT
    congested_speed_ms: float = CONGESTED_SPEED_MS
    queue_context_s: float = QUEUE_CONTEXT_S
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
    lane_order_window_s: float = LANE_ORDER_WINDOW_S
    lane_order_min_veh: float = LANE_ORDER_MIN_VEH
    lane_order_min_windows: int = LANE_ORDER_MIN_WINDOWS
    lane_order_min_correlation: float = LANE_ORDER_MIN_CORRELATION
    lane_order_min_contrast: float = LANE_ORDER_MIN_CONTRAST
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
    "zero_run_min_expected_veh": "Poisson e^-20 against the detector's own profile",
    "min_profile_days": "a median of < 3 values is not robust",
    "day_judged_min_valid_s": "3 h of all-zero readings is a dead detector or a closure",
    "low_flow_veh_h": "same as calibration.onboarding.DEFAULT_ALIVE_VEH_H",
    "empty_loop_occupancy_pct": (
        "an empty loop reads 0 % (Chen et al. 2003 S1: occupancy = 0); 1 % for rounding "
        "and a boundary spill"
    ),
    "zero_flow_min_occupied_s": "beyond a boundary spill of one vehicle (cf. S2)",
    "standstill_min_occupancy_pct": (
        "moving traffic covering the loop half the time is counted (q = o v / L)"
    ),
    "standstill_max_s": "a 1.7 km standing jam passing at about 20 km/h (CLAUDE.md 7.1)",
    "congested_occupancy_pct": "past capacity: 16 % at 2,200 veh/h/lane, 25 m/s, 6.5 m",
    "congested_speed_ms": "40 km/h: CLAUDE.md 7.2 jam threshold (calibration.conservation)",
    "queue_context_s": "a jam front moves about 20 km/h: 1.7 km in 5 min (CLAUDE.md 7.1)",
    "zero_flow_occupied_suspect_share": "convention: one unexplained window in a hundred",
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
    "lane_order_window_s": "5-min shares: a 30-s window holds a handful of vehicles per lane",
    "lane_order_min_veh": "binomial sd of a share <= 6.5 pp at 60 vehicles",
    "lane_order_min_windows": "4 h of 5-min windows: sd of a zero correlation 0.14",
    "lane_order_min_correlation": "two standard errors of a zero correlation over 48 windows",
    "lane_order_min_contrast": "convention: loop sensitivities differ by about 10-15 %",
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
        "no vehicle counted and the loop empty (occupancy at most 1 %, or unreported with no "
        "congestion next to it) for a stretch in which this detector usually counts 20 or more "
        "(cf. Chen et al. 2003, S1: occupancy = 0)"
    ),
    "zero_day": "no vehicle counted in three or more hours of readings",
    "low_flow": "mean flow below 30 veh/h over three or more hours of readings",
    "zero_flow_occupied": (
        "the loop is occupied 5 s or more in a window with no vehicle counted, and neither a "
        "standstill (at least 50 % occupancy for at most 5 min) nor congestion at a "
        "neighbouring lane or station within 5 min explains it (cf. Chen et al. 2003, S2)"
    ),
    "flow_without_occupancy": "occupancy reads 0 while 600+ veh/h per lane pass",
    "implied_length": "flow, occupancy and speed imply a mean vehicle length outside 2.5-25 m",
    "day_outlier": (
        "the day's volume against the detector's median day, with the corridor-wide day "
        "factor divided out"
    ),
    "lane_imbalance": "a lane's flow against the mean of the station's other lanes",
    "lane_order": (
        "the station's lane labels against its neighbours': per-lane shares correlate with the "
        "same-numbered lanes of the nearest stations with the same lane count, or with the "
        "mirrored ones (labels reversed), supported by occupancy per vehicle and light-traffic "
        "share; flagged, never remapped here"
    ),
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


LaneOrderReading = Literal["consistent", "mirrored", "inconclusive"]
LaneOrderVerdict = Literal["ok", "reversed", "uncertain", "inconclusive", "not_checkable"]


@dataclass(frozen=True)
class LaneOrderPair:
    """One neighbour a station's lane order was read against (:func:`lane_order_check`).

    Attributes:
        neighbour: The neighbouring station.
        distance_m: Distance between the two [m].
        n_windows: Windows both stations count with every lane reporting and
            at least ``lane_order_min_veh`` vehicles.
        direct: Mean correlation of the same-numbered lanes' share series
            (``None`` when not computable).
        mirrored: Mean correlation of lane k against the neighbour's lane
            n + 1 − k.
        reading: ``consistent``, ``mirrored`` or ``inconclusive``.
    """

    neighbour: str
    distance_m: float
    n_windows: int
    direct: float | None
    mirrored: float | None
    reading: LaneOrderReading

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "neighbour": self.neighbour,
            "distance_m": _num(self.distance_m, 1),
            "n_windows": self.n_windows,
            "direct": _num(self.direct, 3),
            "mirrored": _num(self.mirrored, 3),
            "reading": self.reading,
        }


@dataclass(frozen=True)
class LaneOrderStation:
    """A per-lane station's lane-order verdict (module docstring, "Lane order").

    Attributes:
        station: Station id.
        x_m: Its position [m] (``None`` when unknown).
        lanes: Lane count (from the stations table, else the numbered sensors).
        sensors_by_lane: Lane number (as labelled; 1 = the source's lane 1)
            → sensor id; empty when the station is not checkable.
        verdict: ``ok``, ``reversed``, ``uncertain``, ``inconclusive`` or
            ``not_checkable``.
        lanes_reversed: The evidence is strong enough for a consumer to remap
            lane k to lane ``lanes + 1 − k`` (only with ``reversed``).
        reason: Plain-language statement of the verdict.
        pairs: The comparable neighbours and their readings.
        occupancy_per_vehicle_s: Free-flow occupancy per vehicle by lane [s].
        light_share: Share of the light-traffic vehicles by lane.
        support: Signature → ``reversed`` (lane 1 against the last lane
            points the other way from every read neighbour), ``as_labelled``,
            ``mixed`` or ``unavailable``.
    """

    station: str
    x_m: float | None
    lanes: int
    sensors_by_lane: dict[int, str]
    verdict: LaneOrderVerdict
    lanes_reversed: bool
    reason: str
    pairs: tuple[LaneOrderPair, ...] = ()
    occupancy_per_vehicle_s: dict[int, float] = field(default_factory=dict)
    light_share: dict[int, float] = field(default_factory=dict)
    support: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON form (lane keys as strings)."""
        return {
            "station": self.station,
            "x_m": _num(self.x_m, 1),
            "lanes": self.lanes,
            "sensors_by_lane": {str(k): v for k, v in sorted(self.sensors_by_lane.items())},
            "verdict": self.verdict,
            "lanes_reversed": self.lanes_reversed,
            "reason": self.reason,
            "pairs": [p.to_dict() for p in self.pairs],
            "occupancy_per_vehicle_s": {
                str(k): _num(v) for k, v in sorted(self.occupancy_per_vehicle_s.items())
            },
            "light_share": {str(k): _num(v) for k, v in sorted(self.light_share.items())},
            "support": dict(sorted(self.support.items())),
        }


LANE_ORDER_METHOD: Final[str] = (
    "per-lane shares over lane_order_window_s windows with at least lane_order_min_veh vehicles, "
    "pooled over the dates, correlated with the nearest station on either side with the same "
    "lane count; a pair reads consistent (same-numbered lanes) or mirrored (lane k against "
    "n + 1 - k) when that mean correlation reaches lane_order_min_correlation and beats the other "
    "by as much. reversed: mirrored against every read neighbour, those neighbours anchored (two "
    "of them, or one consistent with a third station), and both available supporting signatures "
    "(free-flow occupancy per vehicle, light-traffic share: lane 1 against the last lane) "
    "opposite to the neighbours'; its sensor-days are suspect with nothing masked and "
    "lanes_reversed is recorded for consumers to remap. uncertain: any other unexplained "
    "mirrored reading (flagged, never remapped). not_checkable: no comparable neighbour"
)
"""How :func:`lane_order_check` decides (recorded in the report)."""


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
        lane_order: The lane-order verdict of every per-lane mainline station
            (empty for station totals).
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
    lane_order: tuple[LaneOrderStation, ...] = ()
    schema: str = QUALITY_SCHEMA

    def lanes_reversed(self) -> list[str]:
        """Stations whose lane labels the lane-order check found reversed (strong evidence)."""
        return [st.station for st in self.lane_order if st.lanes_reversed]

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
            "lane_order": {
                "method": LANE_ORDER_METHOD,
                "stations": [st.to_dict() for st in self.lane_order],
                "lanes_reversed": self.lanes_reversed(),
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
    "standstill_windows",
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
    queue_nearby: np.ndarray | None = None,
) -> _DayEval:
    """Window- and day-level checks of one sensor-day (module docstring).

    ``queue_nearby`` marks the windows in which a neighbouring sensor reads
    congestion within :data:`QUEUE_CONTEXT_S` (:func:`neighbour_congestion`);
    None means no neighbour is known.
    """
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
    # The speed of a zero-count window is never read: with no vehicle crossing
    # the loop it is undefined (module docstring, "Standing queues").
    nearby = np.zeros(n, dtype=bool) if queue_nearby is None else np.asarray(queue_nearby, bool)
    occupied_s = oc / 100.0 * dt
    occupied_zero = (
        np.isfinite(fc)
        & (fc == 0.0)
        & np.isfinite(oc)
        & (occupied_s >= th.zero_flow_min_occupied_s)
    )
    standstill = occupied_zero & nearby
    max_stand = max(1, math.floor(th.standstill_max_s / dt + 1e-9))
    starts, lengths, _ = _value_runs(np.where(occupied_zero, 1.0, np.nan))
    for s, length in zip(starts, lengths, strict=True):
        stretch = slice(int(s), int(s) + int(length))
        if length <= max_stand and float(np.mean(oc[stretch])) >= (th.standstill_min_occupancy_pct):
            standstill[stretch] = True
    zfo = occupied_zero & ~standstill
    n_zfo = int(zfo.sum())
    ev.stats["zero_flow_occupied_windows"] = n_zfo
    ev.stats["standstill_windows"] = int(standstill.sum())
    share = n_zfo / max(ev.n_valid, 1)
    if n_zfo and share >= th.zero_flow_occupied_suspect_share:
        z_starts, z_lengths, _ = _value_runs(np.where(zfo, 1.0, np.nan))
        k = int(np.argmax(z_lengths))
        ev.add(
            "zero_flow_occupied",
            "exclude" if share >= th.window_flag_exclude_share else "suspect",
            f"the loop is occupied {th.zero_flow_min_occupied_s:g} s or more with no vehicle "
            f"counted in {n_zfo} window(s) ({share:.1%} of the day; longest "
            f"{_duration(z_lengths[k] * dt)} from {grid.window_label(int(z_starts[k]))}) that "
            f"neither a standstill (at least {th.standstill_min_occupancy_pct:g} % occupancy for "
            f"at most {_duration(th.standstill_max_s)}) nor congestion at a neighbouring lane or "
            f"station within {_duration(th.queue_context_s)} explains — a hanging-on loop or "
            f"missed counts (or a stoppage no neighbouring detector saw)",
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
        # only an empty loop is dead (S1: occupancy = 0); an unreported
        # occupancy counts as empty unless a neighbour reads congestion
        empty = np.isfinite(oc) & (oc <= th.empty_loop_occupancy_pct)
        unreported = ~np.isfinite(oc) & ~nearby
        dead = finite_c & (fc == 0.0) & (empty | unreported)
        zero = np.where(dead, 1.0, np.nan)
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
            unreported_run = bool(
                (~np.isfinite(oc[flagged_starts[k] : flagged_starts[k] + flagged_lengths[k]])).all()
            )
            loop = (
                "occupancy not reported and no neighbouring lane or station congested"
                if unreported_run
                else f"the loop empty (occupancy at most {th.empty_loop_occupancy_pct:g} %)"
            )
            ev.add(
                "zero_run",
                "exclude" if share >= th.window_flag_exclude_share else "suspect",
                f"no vehicle counted, {loop}, for {_duration(flagged_lengths[k] * dt)} from "
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


def neighbours(grid: DetectorGrid) -> dict[str, tuple[str, ...]]:
    """Each sensor's neighbours for the standstill rule (module docstring).

    The other lanes of its station and, for a mainline sensor with a known
    position, every sensor of the nearest mainline station upstream and
    downstream (by ``x_m``; ramps are not neighbours of the mainline).

    Args:
        grid: The detector grid.

    Returns:
        Sensor id → neighbouring sensor ids.
    """
    members = grid.stations()
    position: dict[str, float] = {}
    for station, sids in members.items():
        info = grid.sensors[sids[0]]
        if info.kind == "mainline" and info.x_m is not None:
            position[station] = float(info.x_m)
    ordered = sorted(position, key=lambda st: (position[st], st))
    adjacent: dict[str, list[str]] = {}
    for i, station in enumerate(ordered):
        adjacent[station] = [ordered[j] for j in (i - 1, i + 1) if 0 <= j < len(ordered)]
    out: dict[str, tuple[str, ...]] = {}
    for sid, info in grid.sensors.items():
        same = [m for m in members[info.station] if m != sid]
        near = [m for st in adjacent.get(info.station, []) for m in members[st]]
        out[sid] = tuple(same + near)
    return out


def _dilate(mask: np.ndarray, k: int) -> np.ndarray:
    """``mask`` (dates × windows) widened by ``k`` windows either side, per date."""
    if k <= 0 or not mask.any():
        return mask.copy()
    n = mask.shape[1]
    cum = np.concatenate(
        [np.zeros((mask.shape[0], 1), dtype=np.int64), np.cumsum(mask, axis=1, dtype=np.int64)],
        axis=1,
    )
    idx = np.arange(n)
    lo = np.clip(idx - k, 0, n)
    hi = np.clip(idx + k + 1, 0, n)
    widened: np.ndarray = (cum[:, hi] - cum[:, lo]) > 0
    return widened


def neighbour_congestion(
    grid: DetectorGrid, thresholds: QualityThresholds | None = None
) -> dict[str, np.ndarray]:
    """Windows in which a neighbour of each sensor reads congestion.

    A sensor reads congestion in a window when its occupancy is at least
    ``congested_occupancy_pct`` or a vehicle was counted at a speed below
    ``congested_speed_ms`` (readings outside the physical ranges are
    ignored). A sensor's mask is the union of its :func:`neighbours`' readings,
    widened by ``queue_context_s`` either side.

    Args:
        grid: The detector grid, as delivered.
        thresholds: The rules (default :class:`QualityThresholds`).

    Returns:
        Sensor id → boolean array ``(dates, windows)``.
    """
    th = thresholds or QualityThresholds()
    lo_o, hi_o = th.occupancy_range_pct
    reads: dict[str, np.ndarray] = {}
    for sid in grid.sensors:
        q = grid.flow_veh_h[sid]
        o = grid.occupancy_pct[sid]
        v = grid.speed_ms[sid]
        o_ok = np.isfinite(o) & (o >= lo_o) & (o <= hi_o)
        v_ok = np.isfinite(v) & (v >= 0.0) & (v <= th.speed_ceiling_ms)
        counted = np.isfinite(q) & (q > 0.0)
        with np.errstate(invalid="ignore"):
            reads[sid] = (o_ok & (o >= th.congested_occupancy_pct)) | (
                counted & v_ok & (v < th.congested_speed_ms)
            )
    k = math.floor(th.queue_context_s / grid.interval_s + 1e-9)
    shape = (len(grid.dates), grid.n_windows)
    out: dict[str, np.ndarray] = {}
    for sid, near in neighbours(grid).items():
        union = np.zeros(shape, dtype=bool)
        for m in near:
            union |= reads[m]
        out[sid] = _dilate(union, k)
    return out


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


# --- lane order ------------------------------------------------------------------


def _station_table_values(
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, int], dict[str, float]]:
    """``(lane count, position)`` by station from a stations table (missing values left out)."""
    if stations is None:
        return {}, {}
    records: Iterable[Mapping[str, Any]] = (
        cast(list[dict[str, Any]], stations.to_dict("records"))
        if isinstance(stations, pd.DataFrame)
        else stations
    )
    lanes: dict[str, int] = {}
    position: dict[str, float] = {}
    for raw in records:
        ident = raw.get("station", raw.get("id"))
        if ident is None or (isinstance(ident, float) and math.isnan(ident)):
            continue
        n = raw.get("lanes")
        if n is not None and not (isinstance(n, float) and math.isnan(n)):
            try:
                lanes[str(ident)] = int(n)
            except (TypeError, ValueError):
                pass
        x = raw.get("x_m")
        if x is not None and not (isinstance(x, float) and math.isnan(x)):
            position[str(ident)] = float(x)
    return lanes, position


def _lane_numbers_of(grid: DetectorGrid, lane_numbers: Mapping[str, int] | None) -> dict[str, int]:
    """Mainline lane sensor → its lane number (module docstring, "Lane order").

    With ``lane_numbers`` only the sensors it names are numbered (an auxiliary
    or merge loop it leaves out is not a lane of the cross-section); without
    it a lane id that is an integer is the number. Silent sensors (no flow
    anywhere: not installed) are left out.
    """
    out: dict[str, int] = {}
    for sid, info in grid.sensors.items():
        if info.lane is None or info.kind != "mainline" or sid in grid.silent:
            continue
        if lane_numbers is not None:
            if sid in lane_numbers:
                out[sid] = int(lane_numbers[sid])
            continue
        try:
            out[sid] = int(str(info.lane))
        except ValueError:
            continue
    return out


def _blocks(values: np.ndarray, k: int) -> np.ndarray:
    """Sum of every ``k`` consecutive windows per date, flattened; NaN when any is missing."""
    n_blocks = values.shape[1] // k
    if n_blocks == 0:
        return np.zeros(0)
    trimmed = values[:, : n_blocks * k].reshape(values.shape[0], n_blocks, k)
    out: np.ndarray = trimmed.sum(axis=2).reshape(-1)
    return out


def _corr(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation, NaN when either series is constant."""
    if x.size < 2 or float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def _contrast_direction(first: float, last: float, min_contrast: float) -> int:
    """+1 / −1 when lane 1 reads above / below the last lane by the relative contrast, else 0."""
    mean = (first + last) / 2.0
    if not (math.isfinite(first) and math.isfinite(last)) or mean <= 0.0:
        return 0
    contrast = (first - last) / mean
    if abs(contrast) < min_contrast:
        return 0
    return 1 if contrast > 0.0 else -1


@dataclass
class _LaneOrderData:
    """Working record of one checkable station."""

    station: str
    x_m: float
    lanes: int
    sensors_by_lane: dict[int, str]
    shares: dict[int, np.ndarray]
    valid: np.ndarray
    occupancy_per_vehicle_s: dict[int, float]
    light_share: dict[int, float]
    directions: dict[str, int]


def _lane_order_data(
    station: str,
    x_m: float,
    by_lane: dict[int, str],
    flows: Mapping[str, np.ndarray],
    occupancy: Mapping[str, np.ndarray],
    grid: DetectorGrid,
    th: QualityThresholds,
) -> _LaneOrderData:
    """Block counts, shares and the two supporting signatures of one station."""
    dt = grid.interval_s
    k = max(1, round(th.lane_order_window_s / dt))
    block_s = k * dt
    n = len(by_lane)
    lanes = range(1, n + 1)
    veh = {ln: _blocks(flows[by_lane[ln]] * dt / 3600.0, k) for ln in lanes}
    occ_s = {ln: _blocks(occupancy[by_lane[ln]] / 100.0 * dt, k) for ln in lanes}
    stack = np.stack([veh[ln] for ln in lanes])
    complete = np.isfinite(stack).all(axis=0)
    total = np.where(complete, np.nansum(stack, axis=0), 0.0)
    valid = complete & (total >= th.lane_order_min_veh)
    with np.errstate(invalid="ignore", divide="ignore"):
        shares = {
            ln: np.where(valid, veh[ln] / np.where(total > 0.0, total, 1.0), np.nan) for ln in lanes
        }
    directions: dict[str, int] = {}
    # free-flow occupancy per vehicle (slower lanes and longer vehicles read higher)
    occ_stack = np.stack([occ_s[ln] for ln in lanes])
    occ_complete = complete & np.isfinite(occ_stack).all(axis=0) & (stack > 0.0).all(axis=0)
    with np.errstate(invalid="ignore"):
        mean_occ_pct = np.where(occ_complete, occ_stack.sum(axis=0) / (n * block_s) * 100.0, np.inf)
    free = occ_complete & (mean_occ_pct < th.congested_occupancy_pct)
    opv: dict[int, float] = {}
    if int(free.sum()) >= th.lane_order_min_windows:
        opv = {ln: float(occ_s[ln][free].sum() / veh[ln][free].sum()) for ln in lanes}
        directions["occupancy_per_vehicle"] = _contrast_direction(
            opv[1], opv[n], th.lane_order_min_contrast
        )
    # light traffic: lane use by choice, the outer lane carrying most
    per_lane_h = total / (n * block_s / 3600.0)
    light = complete & (total > 0.0) & (per_lane_h < th.lane_min_flow_veh_h_lane)
    light_share: dict[int, float] = {}
    if int(light.sum()) >= th.lane_order_min_windows and float(total[light].sum()) > 0.0:
        grand = float(total[light].sum())
        light_share = {ln: float(veh[ln][light].sum()) / grand for ln in lanes}
        directions["light_traffic_share"] = _contrast_direction(
            light_share[1], light_share[n], th.lane_order_min_contrast
        )
    return _LaneOrderData(
        station=station,
        x_m=x_m,
        lanes=n,
        sensors_by_lane=dict(by_lane),
        shares=shares,
        valid=valid,
        occupancy_per_vehicle_s=opv,
        light_share=light_share,
        directions=directions,
    )


def _read_pair(a: _LaneOrderData, b: _LaneOrderData, th: QualityThresholds) -> LaneOrderPair:
    """Correlate two stations' share series, same-numbered against mirrored lanes."""
    common = a.valid & b.valid
    n_common = int(common.sum())
    distance = abs(b.x_m - a.x_m)
    if n_common < th.lane_order_min_windows:
        return LaneOrderPair(b.station, distance, n_common, None, None, "inconclusive")
    n = a.lanes
    outer = [ln for ln in range(1, n + 1) if ln != n + 1 - ln]
    direct = [_corr(a.shares[ln][common], b.shares[ln][common]) for ln in outer]
    mirror = [_corr(a.shares[ln][common], b.shares[n + 1 - ln][common]) for ln in outer]
    d_ok = [v for v in direct if math.isfinite(v)]
    m_ok = [v for v in mirror if math.isfinite(v)]
    d = float(np.mean(d_ok)) if d_ok else None
    m = float(np.mean(m_ok)) if m_ok else None
    c = th.lane_order_min_correlation
    reading: LaneOrderReading = "inconclusive"
    if d is not None and m is not None:
        if d >= c and d - m >= c:
            reading = "consistent"
        elif m >= c and m - d >= c:
            reading = "mirrored"
    return LaneOrderPair(b.station, distance, n_common, d, m, reading)


def _pairs_text(pairs: Sequence[LaneOrderPair]) -> str:
    parts = []
    for p in pairs:
        if p.direct is None or p.mirrored is None:
            parts.append(f"{p.neighbour} ({p.n_windows} common windows: too few)")
        else:
            parts.append(
                f"{p.neighbour} {p.reading} (same-numbered lanes {p.direct:+.2f}, mirrored "
                f"{p.mirrored:+.2f}, {p.n_windows} windows)"
            )
    return "; ".join(parts)


def lane_order_check(
    grid: DetectorGrid,
    *,
    lane_numbers: Mapping[str, int] | None = None,
    stations: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    thresholds: QualityThresholds | None = None,
    flows: Mapping[str, np.ndarray] | None = None,
    occupancy: Mapping[str, np.ndarray] | None = None,
) -> list[LaneOrderStation]:
    """Judge every per-lane mainline station's lane order (module docstring, "Lane order").

    Args:
        grid: A per-lane detector grid (a station grid yields nothing).
        lane_numbers: Lane sensor id → its lane number in the source's
            numbering (MnDOT/IRIS: 1 = the rightmost lane). Default: lane ids
            that are integers are the numbers.
        stations: Optional stations table: ``lanes`` gives each station's
            lane count (a station whose numbered sensors do not cover 1..lanes
            is not checkable), ``x_m`` its position when the data have none.
        thresholds: The rules (default :class:`QualityThresholds`).
        flows: Flow arrays to use (default the grid's; the report passes its
            masked flows, so excluded sensor-days do not enter).
        occupancy: Occupancy arrays likewise.

    Returns:
        One :class:`LaneOrderStation` per mainline station with lane sensors,
        in position order (stations without a position last).
    """
    th = thresholds or QualityThresholds()
    if not grid.per_lane:
        return []
    flows = grid.flow_veh_h if flows is None else flows
    occupancy = grid.occupancy_pct if occupancy is None else occupancy
    table_lanes, table_x = _station_table_values(stations)
    numbers = _lane_numbers_of(grid, lane_numbers)
    members: dict[str, list[str]] = {}
    position: dict[str, float] = {}
    for sid, info in grid.sensors.items():
        if info.kind != "mainline" or info.lane is None:
            continue
        members.setdefault(info.station, []).append(sid)
        if info.x_m is not None:
            position.setdefault(info.station, float(info.x_m))
    for station in members:
        if station not in position and station in table_x:
            position[station] = table_x[station]

    data: dict[str, _LaneOrderData] = {}
    unchecked: dict[str, tuple[int, str]] = {}
    for station, sids in members.items():
        numbered = {sid: numbers[sid] for sid in sids if sid in numbers}
        n = table_lanes.get(station, len(numbered))
        values = sorted(numbered.values())
        if n < 2:
            unchecked[station] = (n, "fewer than two lanes: there is no order to check")
        elif values != list(range(1, n + 1)):
            have = ", ".join(str(v) for v in values) or "none"
            unchecked[station] = (
                n,
                f"its lane sensors are numbered {have}, not exactly 1..{n} (an excluded or "
                f"silent loop, or lane ids that are not lane numbers): not checkable",
            )
        elif station not in position:
            unchecked[station] = (n, "no position: its neighbours are unknown")
        else:
            by_lane = {ln: sid for sid, ln in numbered.items()}
            data[station] = _lane_order_data(
                station, position[station], by_lane, flows, occupancy, grid, th
            )

    # the nearest checkable station with the same lane count on either side
    nb: dict[str, list[str]] = {}
    for station, d in data.items():
        same = [o for o in data.values() if o.station != station and o.lanes == d.lanes]
        up = [o for o in same if o.x_m < d.x_m]
        down = [o for o in same if o.x_m > d.x_m]
        chosen = []
        if up:
            chosen.append(max(up, key=lambda o: (o.x_m, o.station)).station)
        if down:
            chosen.append(min(down, key=lambda o: (o.x_m, o.station)).station)
        nb[station] = chosen
    pairs: dict[tuple[str, str], LaneOrderPair] = {}
    for station, near in nb.items():
        for other in near:
            pairs[(station, other)] = _read_pair(data[station], data[other], th)

    read = {s: [o for o in nb[s] if pairs[(s, o)].reading != "inconclusive"] for s in data}
    candidates = {
        s for s in data if read[s] and all(pairs[(s, o)].reading == "mirrored" for o in read[s])
    }

    reversed_set: set[str] = set()

    def through(a: str, o: str) -> str:
        """``a``'s reading of ``o``, read through ``o``'s remapped lanes when ``o`` was found
        reversed (mirrored against a reversed station is consistent with its true order)."""
        r = pairs[(a, o)].reading
        if o in reversed_set and r in ("consistent", "mirrored"):
            return "consistent" if r == "mirrored" else "mirrored"
        return r

    def anchored(b: str, excluding: str) -> bool:
        """``b`` agrees with some neighbour other than ``excluding`` (read through a reversal)."""
        return any(through(b, c) == "consistent" for c in nb[b] if c != excluding)

    def support(s: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for sig in ("occupancy_per_vehicle", "light_traffic_share"):
            own = data[s].directions.get(sig, 0)
            others = [data[o].directions.get(sig, 0) for o in read[s]]
            others = [v for v in others if v != 0]
            if own == 0 or not others:
                out[sig] = "unavailable"
            elif all(v == -own for v in others):
                out[sig] = "reversed"
            elif all(v == own for v in others):
                out[sig] = "as_labelled"
            else:
                out[sig] = "mixed"
        return out

    verdicts: dict[str, tuple[LaneOrderVerdict, str]] = {}
    supports = {s: support(s) for s in data}

    def explained(s: str) -> bool:
        """A mirrored reading of ``s`` is laid on a neighbour found reversed: ``s`` is read
        through that neighbour's remapped lanes (the second loop), never reversed itself."""
        return any(o in reversed_set for o in read[s])

    def confirmed_reversed(s: str) -> bool:
        corroborated = len(read[s]) >= 2 or anchored(read[s][0], s)
        available = [v for v in supports[s].values() if v != "unavailable"]
        return corroborated and bool(available) and all(v == "reversed" for v in available)

    # The reversed stations first, to a fixpoint: the first pass is the rule on
    # the raw readings; a later pass may anchor a station through a neighbour
    # found reversed. A candidate whose mirrored reading is laid on a reversed
    # neighbour is explained by it and left to the second loop below.
    while True:
        found = {s for s in candidates - reversed_set if not explained(s) and confirmed_reversed(s)}
        if not found:
            break
        reversed_set |= found

    def majority_support(s: str, exclude: set[str]) -> dict[str, str]:
        """``s``'s signatures against the corridor's other stations with as many lanes.

        Used only to break the tie of a mirrored pair that no third station
        anchors: ``reversed`` when ``s`` points against a strict majority (of
        at least two stations with a direction), ``as_labelled`` with it.
        """
        out: dict[str, str] = {}
        for sig in ("occupancy_per_vehicle", "light_traffic_share"):
            own = data[s].directions.get(sig, 0)
            others = [
                data[o].directions.get(sig, 0)
                for o in data
                if o not in exclude and data[o].lanes == data[s].lanes
            ]
            others = [v for v in others if v != 0]
            balance = sum(others)
            if own == 0 or len(others) < 2 or 2 * abs(balance) <= len(others):
                out[sig] = "unavailable"
            else:
                out[sig] = "reversed" if own * balance < 0 else "as_labelled"
        return out

    def singled_out(own: dict[str, str], other: dict[str, str]) -> bool:
        a = [v for v in own.values() if v != "unavailable"]
        b = [v for v in other.values() if v != "unavailable"]
        return (
            bool(a)
            and all(v == "reversed" for v in a)
            and bool(b)
            and all(v == "as_labelled" for v in b)
        )

    def support_text(sup: dict[str, str]) -> str:
        return ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sup.items())

    for s in sorted(candidates):
        if s in verdicts:
            continue
        if s not in reversed_set and explained(s):
            continue  # read through the reversed neighbour in the loop below
        corroborated = len(read[s]) >= 2 or anchored(read[s][0], s)
        sup = supports[s]
        available = [v for v in sup.values() if v != "unavailable"]
        confirmed = bool(available) and all(v == "reversed" for v in available)
        n = data[s].lanes
        pair_text = _pairs_text([pairs[(s, o)] for o in read[s]])
        if s in reversed_set:
            shown = ", ".join(f"{k.replace('_', ' ')}" for k, v in sup.items() if v == "reversed")
            verdicts[s] = (
                "reversed",
                f"lane labels reversed: its lane shares follow the MIRRORED lanes of "
                f"{pair_text}, and lane 1 against lane {n} runs opposite to the neighbours' in "
                f"{shown}; the counts are good and nothing is masked; lanes_reversed: a consumer "
                f"may read lane k as lane {n + 1} - k",
            )
            continue
        b = read[s][0]
        if not corroborated and b in candidates and b not in verdicts and len(read[b]) == 1:
            # a mirrored pair no third station anchors: the corridor's other
            # stations may single out one of the two (never enough to remap)
            ms = majority_support(s, {s, b})
            mb = majority_support(b, {s, b})
            for odd, fine, m_odd, m_fine in ((s, b, ms, mb), (b, s, mb, ms)):
                if singled_out(m_odd, m_fine):
                    supports[odd], supports[fine] = m_odd, m_fine
                    verdicts[odd] = (
                        "uncertain",
                        f"lane labels probably reversed: its lane shares follow the mirrored lanes "
                        f"of {_pairs_text([pairs[(odd, fine)]])}, its only clear neighbour, and "
                        f"against the corridor's other {data[odd].lanes}-lane stations its "
                        f"signatures point the other way ({support_text(m_odd)}) while "
                        f"{fine}'s agree; not remapped: no third station anchors the pair",
                    )
                    verdicts[fine] = (
                        "ok",
                        f"its mirrored reading against {odd} is laid on {odd}: {odd}'s "
                        f"signatures point against the corridor's other stations, {fine}'s "
                        f"with them ({support_text(m_fine)})",
                    )
                    break
            if s in verdicts:
                continue
        why = []
        if not corroborated:
            why.append(
                f"its only clear neighbour {b} is not anchored by a third station, so the pair "
                "cannot say which of the two is reversed"
            )
        if not available:
            why.append("no supporting signature could be read")
        elif not confirmed:
            why.append(f"the supporting signatures do not confirm it ({support_text(sup)})")
        verdicts[s] = (
            "uncertain",
            f"lane labels may be out of order: {pair_text}; not confirmed ({'; '.join(why)}); "
            "flagged, not remapped",
        )
    for s in data:
        if s in verdicts:
            continue
        if not nb[s]:
            verdicts[s] = (
                "not_checkable",
                f"no station with {data[s].lanes} lanes on either side to compare with",
            )
            continue
        flips = []
        for o in read[s]:
            r = pairs[(s, o)].reading
            if o in reversed_set:
                r = "consistent" if r == "mirrored" else "mirrored"
            flips.append((o, r))
        if any(r == "mirrored" for _, r in flips):
            unexplained = [o for o, r in flips if r == "mirrored"]
            verdicts[s] = (
                "uncertain",
                f"its lane shares follow the mirrored lanes of {', '.join(unexplained)}, which no "
                f"reversed station explains ({_pairs_text([pairs[(s, o)] for o in read[s]])}); "
                "flagged, not remapped",
            )
        elif flips:
            note = (
                f" (read through the reversed labels of {', '.join(o for o in read[s] if o in reversed_set)})"
                if any(o in reversed_set for o in read[s])
                else ""
            )
            verdicts[s] = (
                "ok",
                f"lane order agrees with {_pairs_text([pairs[(s, o)] for o in read[s]])}{note}",
            )
        else:
            verdicts[s] = (
                "inconclusive",
                f"no neighbour reading is clear enough: {_pairs_text([pairs[(s, o)] for o in nb[s]])}",
            )

    out: list[LaneOrderStation] = []
    for s, d in data.items():
        verdict, reason = verdicts[s]
        out.append(
            LaneOrderStation(
                station=s,
                x_m=d.x_m,
                lanes=d.lanes,
                sensors_by_lane=dict(d.sensors_by_lane),
                verdict=verdict,
                lanes_reversed=s in reversed_set,
                reason=reason,
                pairs=tuple(pairs[(s, o)] for o in nb[s]),
                occupancy_per_vehicle_s=dict(d.occupancy_per_vehicle_s),
                light_share=dict(d.light_share),
                support=supports[s],
            )
        )
    for s, (n, reason) in unchecked.items():
        out.append(
            LaneOrderStation(
                station=s,
                x_m=position.get(s),
                lanes=n,
                sensors_by_lane={},
                verdict="not_checkable",
                lanes_reversed=False,
                reason=reason,
            )
        )
    out.sort(key=lambda st: (st.x_m is None, st.x_m if st.x_m is not None else 0.0, st.station))
    return out


def _lane_order_findings(
    lane_order: Sequence[LaneOrderStation],
    evals: dict[tuple[str, int], _DayEval],
    n_dates: int,
) -> None:
    """``lane_order`` findings on every date of a reversed or uncertain station's lanes."""
    for st in lane_order:
        if st.verdict not in ("reversed", "uncertain"):
            continue
        mirrored = [
            p.mirrored for p in st.pairs if p.reading != "inconclusive" and p.mirrored is not None
        ]
        statistic = float(np.mean(mirrored)) if mirrored else None
        for sid in st.sensors_by_lane.values():
            for d in range(n_dates):
                evals[(sid, d)].add("lane_order", "suspect", st.reason, statistic=statistic)


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
    lane_numbers: Mapping[str, int] | None = None,
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
        lane_numbers: Lane sensor id (``station:lane``) → its lane number in
            the source's numbering, for the lane-order check
            (:func:`lane_order_check`; MnDOT: the IRIS lane, 1 = rightmost —
            :class:`calibration.detector_inputs.CorridorInputs` carries it).
            Default: lane ids that are integers are the numbers.

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
    nearby = neighbour_congestion(grid, th)
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
                queue_nearby=nearby[sid][d],
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
    lane_order: list[LaneOrderStation] = []
    if not grid.per_lane:
        notes.append(
            "station totals only: the lane-imbalance and lane-order checks need per-lane data"
        )
    else:
        flows, occs, _ = _masked_values(grid, evals)
        lane_order = lane_order_check(
            grid,
            lane_numbers=lane_numbers,
            stations=stations,
            thresholds=th,
            flows=flows,
            occupancy=occs,
        )
        _lane_order_findings(lane_order, evals, len(grid.dates))
        if lane_order and all(st.verdict == "not_checkable" for st in lane_order):
            notes.append(
                "lane order not checked at any station (no station has a neighbour with the same "
                "lane count and lane numbers 1..n; a per-lane MnDOT frame needs the IRIS lane "
                "numbers, lane_numbers)"
            )
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
        lane_order=tuple(lane_order),
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
# Verdicts as data: masking a frame, station-days
# ---------------------------------------------------------------------------

QUALITY_MASK_RULE: Final[str] = (
    "exclude drops the sensor-day; suspect sets aside only the windows and quantities its "
    "findings name; a station reading is set aside when any lane sensor of the station "
    "(lanes that never reported anywhere in the report excepted) sets it aside; a reading "
    "whose window overlaps a set-aside window of the report is set aside; applied before "
    "the dates are averaged"
)
"""How :func:`mask_frame` applies the verdicts (recorded with every masking)."""

_QUANTITY_COLUMNS: Final[dict[str, str]] = {
    "flow": "flow_veh_h",
    "occupancy": "occupancy_pct",
    "speed": "speed_ms",
}
_GRID_TOLERANCE_S: Final[float] = 1e-6


@dataclass(frozen=True)
class SensorDayVerdict:
    """One sensor-day's verdict, as masking needs it.

    Attributes:
        sensor: Sensor id (station, or ``station:lane``).
        station: Station id.
        lane: Lane id, or ``None`` for a station sensor.
        kind: ``mainline``, ``on_ramp`` or ``off_ramp``.
        date: Local date ``YYYY-MM-DD``.
        verdict: ``ok``, ``suspect`` or ``exclude``.
        checks: The checks behind the verdict, in finding order.
        masked: Quantity → the report-grid window indices a ``suspect``
            verdict sets aside.
        n_valid: Windows the sensor delivered that day (``None`` when the
            source does not say).
    """

    sensor: str
    station: str
    lane: str | None
    kind: str
    date: str
    verdict: str
    checks: tuple[str, ...] = ()
    masked: dict[str, tuple[int, ...]] = field(default_factory=dict)
    n_valid: int | None = None

    def set_aside(self, quantity: str, n_windows: int) -> np.ndarray:
        """Windows of ``quantity`` the verdict sets aside (every one for ``exclude``)."""
        out = np.zeros(n_windows, dtype=bool)
        if self.verdict == "exclude":
            out[:] = True
        elif self.verdict == "suspect":
            idx = [int(i) for i in self.masked.get(quantity, ()) if 0 <= int(i) < n_windows]
            out[idx] = True
        return out

    def record(self) -> dict[str, Any]:
        """``{"sensor", "date", "verdict", "checks"}`` (what a masked artifact lists)."""
        return {
            "sensor": self.sensor,
            "date": self.date,
            "verdict": self.verdict,
            "checks": list(self.checks),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SensorDayVerdict:
        """From a ``sensor_days`` entry of the report's JSON.

        Raises:
            ValueError: No station or date, or an unknown verdict.
        """
        station = raw.get("station")
        if station in (None, "") or raw.get("date") in (None, ""):
            raise ValueError(f"data-quality sensor-day without station or date: {dict(raw)}")
        lane = raw.get("lane")
        verdict = str(raw.get("verdict", ""))
        if verdict not in _RANK:
            raise ValueError(f"data-quality sensor-day with unknown verdict {verdict!r}")
        checks: list[str] = []
        for finding in raw.get("findings") or ():
            check = str(finding.get("check", ""))
            if check and check not in checks:
                checks.append(check)
        masked = raw.get("masked") or {}
        n_valid = raw.get("n_valid")
        return cls(
            sensor=str(
                raw.get("sensor") or sensor_id(str(station), None if lane is None else str(lane))
            ),
            station=str(station),
            lane=None if lane is None else str(lane),
            kind=str(raw.get("kind") or "mainline"),
            date=normalize_date(str(raw["date"])),
            verdict=verdict,
            checks=tuple(checks),
            masked={q: tuple(int(i) for i in masked.get(q) or ()) for q in QUANTITIES},
            n_valid=None if n_valid is None else int(n_valid),
        )


@dataclass(frozen=True)
class StationDay:
    """A station's verdict on one date, from its sensors' verdicts.

    Attributes:
        station: Station id.
        date: Local date.
        sensors: The verdicts of its sensors that day (lanes that never
            reported anywhere in the report left out, unless every lane is
            such a lane).
        excluded: Some sensor is judged ``exclude``.
    """

    station: str
    date: str
    sensors: tuple[SensorDayVerdict, ...]

    @property
    def excluded(self) -> bool:
        """A sensor of the station is excluded that day."""
        return any(sd.verdict == "exclude" for sd in self.sensors)

    @property
    def judged(self) -> bool:
        """At least one sensor of the station was judged that day."""
        return bool(self.sensors)

    def exclusions(self) -> list[dict[str, Any]]:
        """The excluding sensor-days as records."""
        return [sd.record() for sd in self.sensors if sd.verdict == "exclude"]


@dataclass(frozen=True)
class QualityVerdicts:
    """A data-quality report's verdicts, read from the report or its JSON.

    Attributes:
        interval_s: The report's window [s] (``None`` when its JSON omits it).
        start_s: Local clock start of the report's span [s].
        n_windows: Windows per day of the span.
        dates: The dates the report judged.
        per_lane: The sensors are lanes.
        sensor_days: One verdict per sensor and date.
        path: Where the JSON was read from (``None`` for an in-memory report).
        sha256: SHA-256 of the JSON file's bytes, or of the report's
            :meth:`DataQualityReport.to_json` text for an in-memory report.
        positions: Station → corridor position [m] from the report's
            ``sensors`` (``None`` when not stated).
        lane_order: Station → its lane-order record (the report's
            ``lane_order.stations`` entries; empty for a report written before
            the check existed, 2026-10-07).
    """

    interval_s: float | None
    start_s: float | None
    n_windows: int | None
    dates: tuple[str, ...]
    per_lane: bool
    sensor_days: tuple[SensorDayVerdict, ...]
    path: str | None = None
    sha256: str | None = None
    positions: dict[str, float | None] = field(default_factory=dict)
    lane_order: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def lanes_reversed(self) -> frozenset[str]:
        """Stations whose lane labels the report found reversed (strong evidence).

        Only ``verdict == "reversed"`` with ``lanes_reversed`` true; an
        ``uncertain`` station is flagged in the report but never listed here.
        """
        return frozenset(
            st
            for st, rec in self.lane_order.items()
            if rec.get("lanes_reversed") and rec.get("verdict") == "reversed"
        )

    @property
    def span_local(self) -> tuple[str | None, str | None]:
        """``(start, end)`` of the judged span as ``"HH:MM"`` (``None`` when unknown)."""
        if self.start_s is None:
            return None, None
        if self.interval_s is None or self.n_windows is None:
            return clock_text(self.start_s), None
        return clock_text(self.start_s), clock_text(self.start_s + self.n_windows * self.interval_s)

    @classmethod
    def from_report(cls, report: DataQualityReport, *, path: str | None = None) -> QualityVerdicts:
        """From an in-memory :class:`DataQualityReport`."""
        return cls.from_dict(
            report.to_dict(),
            path=path,
            sha256=hashlib.sha256(report.to_json().encode()).hexdigest(),
        )

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, path: str | None = None, sha256: str | None = None
    ) -> QualityVerdicts:
        """From a ``flowstate.data_quality/1`` payload.

        Raises:
            ValueError: Another schema, or a malformed sensor-day.
        """
        schema = payload.get("schema", QUALITY_SCHEMA)
        if schema != QUALITY_SCHEMA:
            raise ValueError(f"expected a {QUALITY_SCHEMA} report, got schema {schema!r}")
        grid = payload.get("grid") or {}
        days = tuple(SensorDayVerdict.from_mapping(sd) for sd in payload.get("sensor_days", ()))
        interval = grid.get("interval_s")
        start = grid.get("start_local")
        start_s = None if start in (None, "") else parse_clock(str(start))
        n_windows = grid.get("n_windows")
        if n_windows is None and interval and start_s is not None and grid.get("end_local"):
            end = str(grid["end_local"])
            end_s = 86400.0 if end in ("24:00", "24:00:00") else parse_clock(end)
            n_windows = round((end_s - start_s) / float(interval))
        dates = {normalize_date(str(d)) for d in grid.get("dates") or ()}
        dates |= {sd.date for sd in days}
        per_lane = bool(grid.get("per_lane", any(sd.lane is not None for sd in days)))
        positions: dict[str, float | None] = {}
        for sensor in payload.get("sensors") or ():
            station = str(sensor.get("station") or sensor.get("sensor") or "")
            x = sensor.get("x_m")
            if station and positions.get(station) is None:
                positions[station] = None if x is None else float(x)
        lane_order = {
            str(rec["station"]): dict(rec)
            for rec in (payload.get("lane_order") or {}).get("stations") or ()
            if rec.get("station") not in (None, "")
        }
        return cls(
            interval_s=None if interval is None else float(interval),
            start_s=start_s,
            n_windows=None if n_windows is None else int(n_windows),
            dates=tuple(sorted(dates)),
            per_lane=per_lane,
            sensor_days=days,
            path=path,
            sha256=sha256,
            positions=positions,
            lane_order=lane_order,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> QualityVerdicts:
        """Read ``data_quality.json`` (path and file hash recorded)."""
        raw = Path(path).read_bytes()
        return cls.from_dict(
            json.loads(raw), path=str(path), sha256=hashlib.sha256(raw).hexdigest()
        )

    def silent_lanes(self) -> frozenset[str]:
        """Lane sensors that delivered nothing on any date of the report.

        A detector that never reports is not an installed lane
        (:func:`calibration.conservation.silent_lane_note`): it neither
        excludes its station nor masks the station's readings.
        """
        seen: dict[str, bool] = {}
        for sd in self.sensor_days:
            if sd.lane is None or sd.n_valid is None:
                seen[sd.sensor] = False
                continue
            seen[sd.sensor] = seen.get(sd.sensor, True) and sd.n_valid == 0
        return frozenset(s for s, silent in seen.items() if silent)

    def station_sensors(self) -> dict[str, tuple[str, ...]]:
        """Station → its sensors (silent lanes left out unless every lane is silent)."""
        silent = self.silent_lanes()
        every: dict[str, list[str]] = {}
        for sd in self.sensor_days:
            members = every.setdefault(sd.station, [])
            if sd.sensor not in members:
                members.append(sd.sensor)
        return {st: tuple(sorted(counted_lanes(members, silent))) for st, members in every.items()}

    def station_days(
        self, *, kind: str | None = "mainline", stations: Collection[str] | None = None
    ) -> dict[tuple[str, str], StationDay]:
        """Every (station, date) the report judged, from its sensors' verdicts.

        Args:
            kind: Keep only sensors of this kind (``None``: every kind).
            stations: Keep only these stations (default: all).

        Returns:
            ``(station, date)`` → :class:`StationDay`.
        """
        sensors = self.station_sensors()
        out: dict[tuple[str, str], list[SensorDayVerdict]] = {}
        for sd in self.sensor_days:
            if kind is not None and sd.kind != kind:
                continue
            if stations is not None and sd.station not in stations:
                continue
            if sd.sensor not in sensors.get(sd.station, ()):
                continue
            out.setdefault((sd.station, sd.date), []).append(sd)
        return {key: StationDay(key[0], key[1], tuple(v)) for key, v in out.items()}


QualityInput = DataQualityReport | QualityVerdicts | Mapping[str, Any]
"""What :func:`mask_frame` accepts: a report, its verdicts, or its JSON payload."""


def as_verdicts(quality: QualityInput) -> QualityVerdicts:
    """:class:`QualityVerdicts` from a report, its JSON payload, or verdicts."""
    if isinstance(quality, QualityVerdicts):
        return quality
    if isinstance(quality, DataQualityReport):
        return QualityVerdicts.from_report(quality)
    return QualityVerdicts.from_dict(quality)


@dataclass(frozen=True)
class FrameMask:
    """A frame with the verdicts applied (:func:`mask_frame`).

    Attributes:
        frame: The masked copy (set-aside readings are NaN; nothing else
            changed).
        masked_sensor_days: The non-``ok`` sensor-days that apply to the
            readings considered — every ``exclude`` of a considered
            station-date, and every ``suspect`` that sets aside a considered
            window — sorted by date and sensor.
        n_masked_windows: Considered readings (one row: a sensor, date and
            window) in which at least one finite value was set aside.
        path: The report's JSON path (``None`` in memory).
        sha256: The report's hash.
    """

    frame: pd.DataFrame
    masked_sensor_days: tuple[SensorDayVerdict, ...]
    n_masked_windows: int
    path: str | None
    sha256: str | None

    def record(self) -> dict[str, Any]:
        """The ``source["quality"]`` block of a masked observations artifact."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "n_masked_sensor_days": len(self.masked_sensor_days),
            "masked_sensor_days": [sd.record() for sd in self.masked_sensor_days],
            "n_masked_windows": int(self.n_masked_windows),
            "rule": QUALITY_MASK_RULE,
        }


def mask_frame(
    frame: pd.DataFrame,
    quality: QualityInput,
    *,
    dates: Iterable[str] | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    stations: Collection[str] | None = None,
    interval_s: float | None = None,
) -> FrameMask:
    """Apply a report's verdicts to a tidy detector frame (:func:`mask_grid`'s rule).

    The readings considered are the frame's rows of ``dates`` (default every
    date), of ``stations`` (default every station) whose window overlaps
    ``[start_s, end_s)`` (default the whole day). Each must be covered by the
    report — its date judged, its window inside the report's span, its station
    (or lane) among the report's sensors — or nothing is masked and the call
    fails: a reading the report never judged is not a reading it passed. A
    considered reading loses each quantity a verdict sets aside
    (:data:`QUALITY_MASK_RULE`); per-lane verdicts apply to a station row
    through every installed lane of the station, a station verdict to every
    lane row of the station. Rows not considered are returned unchanged.

    Args:
        frame: Tidy detector frame (station or per-lane rows).
        quality: The report, its JSON payload or its :class:`QualityVerdicts`.
        dates: Dates to consider (``YYYYMMDD`` or ``YYYY-MM-DD``).
        start_s: Local clock start of the considered span [s].
        end_s: Local clock end of the considered span [s].
        stations: Stations to consider.
        interval_s: The frame's window [s] (default: ``attrs`` or inferred).

    Returns:
        The :class:`FrameMask`.

    Raises:
        ValueError: The report has no window grid, or a considered reading is
            not covered by it (date, span or sensor).
    """
    v = as_verdicts(quality)
    if v.interval_s is None or v.start_s is None or v.n_windows is None:
        raise ValueError(
            "mask_frame: the data-quality report records no window grid (grid.interval_s, "
            "start_local, n_windows)"
        )
    name = v.path or "the data-quality report"
    interval = float(interval_s or frame.attrs.get("interval_s") or detector_interval_s(frame))
    secs = local_seconds(frame).to_numpy(dtype=float)
    days = local_dates(frame).to_numpy(dtype=object)
    consider = np.ones(len(frame), dtype=bool)
    if dates is not None:
        consider &= np.isin(days, sorted({normalize_date(d) for d in dates}))
    if stations is not None:
        consider &= frame["station"].astype(str).isin({str(s) for s in stations}).to_numpy()
    if start_s is not None:
        consider &= secs + interval > float(start_s) + _GRID_TOLERANCE_S
    if end_s is not None:
        consider &= secs < float(end_s) - _GRID_TOLERANCE_S
    uncovered = sorted(set(days[consider]) - set(v.dates))
    if uncovered:
        raise ValueError(
            f"mask_frame: date(s) {uncovered} are not covered by {name} (it judged "
            f"{', '.join(v.dates) or 'no date'}); run the data-quality check over them"
        )
    lo = v.start_s
    hi = v.start_s + v.n_windows * v.interval_s
    outside = consider & (
        (secs < lo - _GRID_TOLERANCE_S) | (secs + interval > hi + _GRID_TOLERANCE_S)
    )
    if outside.any():
        first = int(np.flatnonzero(outside)[0])
        raise ValueError(
            f"mask_frame: the reading at local {clock_text(secs[first])} lies outside the span "
            f"{name} judged ({clock_text(lo)}-{clock_text(hi)})"
        )

    by_key = {(sd.sensor, sd.date): sd for sd in v.sensor_days}
    members = v.station_sensors()
    report_sensors = {sd.sensor for sd in v.sensor_days}
    has_lane = "lane" in frame.columns and bool(frame["lane"].notna().any())
    lanes = (
        frame["lane"].to_numpy(dtype=object)
        if has_lane
        else np.full(len(frame), None, dtype=object)
    )
    station_ids = frame["station"].astype(str).to_numpy(dtype=object)

    def resolve(station: str, lane: Any) -> tuple[str, ...]:
        if lane is not None and not (isinstance(lane, float) and math.isnan(lane)):
            own = sensor_id(station, str(lane))
            if own in report_sensors:
                return (own,)
        if station in report_sensors:
            return (station,)
        if lane is None or (isinstance(lane, float) and math.isnan(lane)):
            return members.get(station, ())
        return ()

    out = frame.copy()
    out.attrs = dict(frame.attrs)
    present = {q: c for q, c in _QUANTITY_COLUMNS.items() if c in out.columns}
    values = {q: out[c].to_numpy(dtype=float, copy=True) for q, c in present.items()}
    before = {q: np.isfinite(a) for q, a in values.items()}
    set_aside = {q: np.zeros(len(frame), dtype=bool) for q in present}
    applied: dict[tuple[str, str], SensorDayVerdict] = {}
    rows = np.flatnonzero(consider)
    j0 = np.floor((secs - lo) / v.interval_s + _GRID_TOLERANCE_S).astype(np.int64)
    j1 = np.ceil((secs + interval - lo) / v.interval_s - _GRID_TOLERANCE_S).astype(np.int64) - 1
    groups: dict[tuple[str, Any, str], list[int]] = {}
    for r in rows:
        lane = lanes[r]
        key_lane = (
            None if lane is None or (isinstance(lane, float) and math.isnan(lane)) else str(lane)
        )
        groups.setdefault((str(station_ids[r]), key_lane, str(days[r])), []).append(int(r))
    for (station, lane, day), idx_list in groups.items():
        sensors = resolve(station, lane)
        if not sensors:
            raise ValueError(
                f"mask_frame: station {station!r}"
                + (f" lane {lane!r}" if lane is not None else "")
                + f" is not among the sensors {name} judged"
            )
        idx = np.asarray(idx_list, dtype=np.int64)
        for sid in sensors:
            sd = by_key.get((sid, day))
            if sd is None:
                raise ValueError(f"mask_frame: {name} holds no verdict for {sid!r} on {day}")
            if sd.verdict == "ok":
                continue
            hit = sd.verdict == "exclude"
            for q in present:
                windows = sd.set_aside(q, v.n_windows)
                if not windows.any():
                    continue
                cum = np.concatenate([[0], np.cumsum(windows, dtype=np.int64)])
                a = np.clip(j0[idx], 0, v.n_windows)
                b = np.clip(j1[idx] + 1, 0, v.n_windows)
                rows_hit = (cum[b] - cum[a]) > 0
                if rows_hit.any():
                    set_aside[q][idx[rows_hit]] = True
                    hit = True
            if hit:
                applied[(sid, day)] = sd
    changed = np.zeros(len(frame), dtype=bool)
    for q, column in present.items():
        values[q][set_aside[q]] = np.nan
        out[column] = values[q]
        changed |= set_aside[q] & before[q]
    ordered = tuple(applied[k] for k in sorted(applied, key=lambda k: (k[1], k[0])))
    return FrameMask(
        frame=out,
        masked_sensor_days=ordered,
        n_masked_windows=int(changed.sum()),
        path=v.path,
        sha256=v.sha256,
    )


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
    flagged = [st for st in report.lane_order if st.verdict in ("reversed", "uncertain")]
    if report.per_lane:
        lines += ["", "## Lane order", ""]
        n_checked = sum(1 for st in report.lane_order if st.verdict != "not_checkable")
        lines.append(
            f"Each station's lane labels were compared with its neighbours' "
            f"({n_checked} of {len(report.lane_order)} stations could be compared; a station "
            "with no neighbour of the same lane count cannot be)."
        )
        lines.append("")
        if flagged:
            lines += ["| Station | Verdict | Remap advised | Why |", "|---|---|---|---|"]
            for st in flagged:
                lines.append(
                    f"| {st.station} | {st.verdict} | {'yes' if st.lanes_reversed else 'no'} | "
                    f"{st.reason} |"
                )
        else:
            lines.append("No station's lane labels disagree with its neighbours'.")
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
