"""Active bottlenecks and check C6 of the corridor study protocol.

docs/FRISCO_PROTOCOL.md §5 asks whether the model reproduces *where and when
the slowdowns form*. This module identifies active bottlenecks on a station ×
five-minute speed matrix and compares the observed ones with those of each
simulated replicate.

**Active bottleneck** (after Chen, Skabardonis & Varaiya 2004, "Systematic
identification of freeway bottlenecks", TRR 1867, as summarised in NCDOT
report 2016-10; the protocol's wording): in a five-minute window a bottleneck
is active between two neighbouring stations when the upstream station's speed
is below :data:`UPSTREAM_SPEED_MAX_MS` (40 mph) and the downstream station is
at least :data:`SPEED_DIFFERENCE_MIN_MS` (20 mph) faster. It counts as
activated when that holds in at least :data:`PERSISTENCE_MIN_ACTIVE` of
:data:`PERSISTENCE_WINDOWS` consecutive windows.

How the rule is read here (every choice is a constant or stated below):

* A window in which either station's speed is missing (NaN) does not satisfy
  the condition; nothing is imputed.
* A **qualifying block** is any run of :data:`PERSISTENCE_WINDOWS` consecutive
  windows holding at least :data:`PERSISTENCE_MIN_ACTIVE` windows that satisfy
  the condition. Overlapping or touching blocks are merged; within each merged
  run the **episode** lasts from its first to its last satisfying window
  (inclusive, so the one or two windows the rule tolerates inside a block are
  counted as active).
* One :class:`Bottleneck` per station pair: its **activation** is the start of
  the first episode, its **active duration** the summed length of its
  episodes, its **queue reach** the furthest upstream station that is below
  :data:`UPSTREAM_SPEED_MAX_MS` together with every station between it and the
  bottleneck, at the queue's longest extent over the episodes' windows.

The comparison (protocol §5.1–5.4; every threshold a FlowState rule):

1. **Location** — each observed bottleneck active for at least
   :data:`MIN_OBSERVED_ACTIVE_S` is reproduced at the same station pair or an
   adjacent one (pair indices within :data:`ADJACENT_PAIRS`) in at least
   :data:`LOCATION_MIN_REPLICATE_SHARE` of the replicates. Observed and
   simulated bottlenecks are matched **one to one** within each replicate
   (§5.4's last sentence): one simulated bottleneck reproduces at most one
   observed one. The matching (:func:`match_replicate`) is the assignment that
   reproduces the most observed bottlenecks, then the most at their own pair,
   then the longest-active simulated ones.
2. **Timing** — over the replicates that reproduced it, the median simulated
   activation is within :data:`ACTIVATION_TOLERANCE_S` of the observed one and
   the median active duration within :data:`DURATION_TOLERANCE_SHARE` of it.
3. **Queue reach** — the median simulated queue-reach station index (same
   replicates) is within :data:`QUEUE_REACH_TOLERANCE_STATIONS` of the observed
   queue's.
4. **No phantom** — at most :data:`PHANTOM_MAX_REPLICATE_SHARE` of the
   replicates contain *any* simulated bottleneck active for more than
   :data:`PHANTOM_ACTIVE_S` whose pair is neither an observed bottleneck's pair
   nor adjacent to one. It is counted **per replicate**, so a phantom that
   moves between neighbouring station pairs from seed to seed counts once in
   each replicate it appears in, not once per pair. A simulated bottleneck at
   or next to an observed one is not a phantom, whether or not the matching of
   rule 1 used it (the protocol's "not at, or adjacent to, an observed one").
   The per-pair breakdown is kept as a diagnostic (:class:`Phantom`).

An observed set with no bottleneck active for :data:`MIN_OBSERVED_ACTIVE_S`
leaves rules 1–3 with nothing to reproduce (they hold, and the result says
so); rule 4 is still applied.

The module is pure: it takes matrices, not files. The simulated station
speeds the gate feeds it are the battery's per-replicate segment speeds
(:mod:`validation.baseline_gate` states exactly which).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import linear_sum_assignment

from flowstate_core.units import s_to_h
from validation.observed import clock_label

FloatArray = NDArray[np.float64]

#: Metres in one international mile (exact, by the 1959 international yard and
#: pound agreement: 1 mile = 1,609.344 m). ``flowstate_core.units`` has no mile.
M_PER_MILE: Final[float] = 1609.344

#: One mile per hour in m/s: :data:`M_PER_MILE` metres per hour, an hour being
#: ``1 / s_to_h(1)`` seconds (0.44704 m/s).
MPH_TO_MS: Final[float] = M_PER_MILE * s_to_h(1.0)

#: Seconds per minute (the protocol states its windows and tolerances in minutes).
_S_PER_MIN: Final[float] = 60.0

#: The upstream station of an active bottleneck is slower than this
#: (docs/FRISCO_PROTOCOL.md §5: 40 mph, 64.4 km/h; confirmed from the
#: secondary source).
UPSTREAM_SPEED_MAX_MS: Final[float] = 40.0 * MPH_TO_MS

#: The downstream station is at least this much faster (§5: 20 mph,
#: 32.2 km/h; confirmed from the secondary source).
SPEED_DIFFERENCE_MIN_MS: Final[float] = 20.0 * MPH_TO_MS

#: The rule is defined on five-minute windows (§5); a matrix on another
#: window is refused rather than resampled.
BOTTLENECK_WINDOW_S: Final[float] = 5.0 * _S_PER_MIN

#: Consecutive windows the persistence rule looks at (§5). Persistence rule
#: from a secondary summary; verify against the original paper before the
#: first Frisco run.
PERSISTENCE_WINDOWS: Final[int] = 7

#: Windows of :data:`PERSISTENCE_WINDOWS` that must hold the condition (§5).
#: Persistence rule from a secondary summary; verify against the original
#: paper before the first Frisco run.
PERSISTENCE_MIN_ACTIVE: Final[int] = 5

#: Observed bottlenecks active at least this long must be reproduced (§5.1).
MIN_OBSERVED_ACTIVE_S: Final[float] = 30.0 * _S_PER_MIN

#: Station pairs counted as "adjacent" to one another: pair indices differing
#: by at most this (§5.1, "the same station pair or an adjacent one").
ADJACENT_PAIRS: Final[int] = 1

#: Share of replicates that must reproduce each such bottleneck (§5.1).
LOCATION_MIN_REPLICATE_SHARE: Final[float] = 0.80

#: Median simulated activation within this of the observed one (§5.2).
ACTIVATION_TOLERANCE_S: Final[float] = 15.0 * _S_PER_MIN

#: Median simulated active duration within this share of the observed (§5.2).
DURATION_TOLERANCE_SHARE: Final[float] = 0.30

#: Median simulated queue reach within this many stations (§5.3).
QUEUE_REACH_TOLERANCE_STATIONS: Final[int] = 1

#: A phantom bottleneck counts when active for more than this (§5.4).
PHANTOM_ACTIVE_S: Final[float] = 30.0 * _S_PER_MIN

#: Rule 4 fails when more than this share of the replicates contain a phantom
#: (§5.4: "at most 50 % of the replicates", counted per replicate).
PHANTOM_MAX_REPLICATE_SHARE: Final[float] = 0.50

#: Weights of the one-to-one matching of rule 1 (§5.4's last sentence;
#: :func:`match_replicate`): every allowed pair (same or adjacent station pair)
#: is worth :data:`_MATCH_WEIGHT`, a same-pair match :data:`_SAME_PAIR_WEIGHT`
#: more, and the simulated bottleneck's active time a share of one below that.
#: The steps are far enough apart (a corridor has far fewer than 1,000 station
#: pairs) that the assignment maximises the count of reproduced bottlenecks
#: first, then the count at their own pair, then the active time.
_MATCH_WEIGHT: Final[float] = 1.0e6
_SAME_PAIR_WEIGHT: Final[float] = 1.0e3

#: Rule names, in protocol order (§5.1–5.4).
RULES: Final[tuple[str, ...]] = ("location", "timing", "queue_reach", "no_phantom")

_TOL: Final[float] = 1e-9


def _num(value: float | None) -> float | None:
    """Finite float or ``None`` (JSON has no NaN)."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)


@dataclass(frozen=True)
class Bottleneck:
    """One active bottleneck (module docstring).

    Attributes:
        pair_index: Index ``i`` of the pair (station ``i`` upstream,
            ``i + 1`` downstream) in the matrix's station order.
        upstream: Upstream station id (the slow one).
        downstream: Downstream station id (the fast one).
        upstream_x_m: Upstream station position [m].
        downstream_x_m: Downstream station position [m].
        activation_s: Start of the first episode, seconds from the
            observations' ``t0_local`` (simulation time).
        activation_clock: Local clock label of ``activation_s``.
        active_s: Summed episode length [s].
        episodes: ``(start_s, end_s)`` of each episode, ``end_s`` the end of
            its last window.
        queue_reach_index: Index of the furthest upstream station in the
            queue at its longest extent.
        queue_reach_station: That station's id.
    """

    pair_index: int
    upstream: str
    downstream: str
    upstream_x_m: float
    downstream_x_m: float
    activation_s: float
    activation_clock: str
    active_s: float
    episodes: tuple[tuple[float, float], ...]
    queue_reach_index: int
    queue_reach_station: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "pair_index": self.pair_index,
            "upstream": self.upstream,
            "downstream": self.downstream,
            "upstream_x_m": self.upstream_x_m,
            "downstream_x_m": self.downstream_x_m,
            "activation_s": self.activation_s,
            "activation_clock": self.activation_clock,
            "active_s": self.active_s,
            "episodes": [list(e) for e in self.episodes],
            "queue_reach_index": self.queue_reach_index,
            "queue_reach_station": self.queue_reach_station,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Bottleneck:
        """Rebuild from :meth:`to_dict`."""
        return cls(
            pair_index=int(raw["pair_index"]),
            upstream=str(raw["upstream"]),
            downstream=str(raw["downstream"]),
            upstream_x_m=float(raw["upstream_x_m"]),
            downstream_x_m=float(raw["downstream_x_m"]),
            activation_s=float(raw["activation_s"]),
            activation_clock=str(raw.get("activation_clock") or ""),
            active_s=float(raw["active_s"]),
            episodes=tuple((float(a), float(b)) for a, b in raw.get("episodes", ())),
            queue_reach_index=int(raw["queue_reach_index"]),
            queue_reach_station=str(raw["queue_reach_station"]),
        )


def active_condition(speeds_ms: ArrayLike) -> NDArray[np.bool_]:
    """The per-window condition for every station pair.

    Args:
        speeds_ms: ``[window][station]`` speeds [m/s], stations upstream
            first; NaN where not measured.

    Returns:
        Boolean ``[window][pair]``: the upstream station below
        :data:`UPSTREAM_SPEED_MAX_MS` and the downstream one at least
        :data:`SPEED_DIFFERENCE_MIN_MS` faster, both measured.
    """
    v = np.asarray(speeds_ms, dtype=np.float64)
    if v.ndim != 2:
        raise ValueError(f"speed matrix must be 2-D [window][station], got shape {v.shape}")
    if v.shape[1] < 2:
        return np.zeros((v.shape[0], 0), dtype=bool)
    up, down = v[:, :-1], v[:, 1:]
    measured = np.isfinite(up) & np.isfinite(down)
    with np.errstate(invalid="ignore"):
        slow = up < UPSTREAM_SPEED_MAX_MS
        gap = (down - up) >= SPEED_DIFFERENCE_MIN_MS - _TOL
    return np.asarray(measured & slow & gap, dtype=bool)


def episodes_of(condition: Sequence[bool] | NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Episodes of one pair's condition series (module docstring).

    Args:
        condition: Per-window condition of one station pair.

    Returns:
        ``(first, last)`` window indices (inclusive) of each episode, in
        time order; empty when no block qualifies.
    """
    c = np.asarray(condition, dtype=bool)
    n = int(c.size)
    if n < PERSISTENCE_WINDOWS:
        return []
    counts = np.convolve(c.astype(np.int64), np.ones(PERSISTENCE_WINDOWS, dtype=np.int64), "valid")
    starts = [int(k) for k in np.flatnonzero(counts >= PERSISTENCE_MIN_ACTIVE)]
    if not starts:
        return []
    merged: list[list[int]] = []
    for k in starts:
        end = k + PERSISTENCE_WINDOWS
        if merged and k <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([k, end])
    out: list[tuple[int, int]] = []
    for lo, hi in merged:
        trues = np.flatnonzero(c[lo:hi])
        out.append((lo + int(trues[0]), lo + int(trues[-1])))
    return out


def _queue_reach(speeds: FloatArray, pair: int, windows: Sequence[int]) -> int:
    """Furthest upstream station index of the queue behind ``pair`` over ``windows``."""
    reach = pair
    for k in windows:
        row = speeds[k]
        if not (math.isfinite(row[pair]) and row[pair] < UPSTREAM_SPEED_MAX_MS):
            continue
        j = pair
        while j - 1 >= 0 and math.isfinite(row[j - 1]) and row[j - 1] < UPSTREAM_SPEED_MAX_MS:
            j -= 1
        reach = min(reach, j)
    return reach


def identify_bottlenecks(
    speeds_ms: ArrayLike,
    station_ids: Sequence[str],
    x_m: Sequence[float],
    *,
    window_s: float = BOTTLENECK_WINDOW_S,
    first_window: int = 0,
    t0_local: str = "",
) -> tuple[Bottleneck, ...]:
    """Active bottlenecks on a station × five-minute speed matrix.

    Args:
        speeds_ms: ``[window][station]`` speeds [m/s], NaN where missing.
            Rows are consecutive windows.
        station_ids: Column station ids, upstream first.
        x_m: Column positions [m], strictly increasing.
        window_s: The rows' window length [s]; must be
            :data:`BOTTLENECK_WINDOW_S`.
        first_window: Observation-grid index of row 0 (the activation time of
            a row ``k`` is ``(first_window + k) · window_s``).
        t0_local: The observations' ``t0_local``, for the clock labels.

    Returns:
        One :class:`Bottleneck` per station pair that activated, in pair order.

    Raises:
        ValueError: A window other than five minutes, mismatched lengths, or
            positions not strictly increasing.
    """
    if abs(float(window_s) - BOTTLENECK_WINDOW_S) > _TOL:
        raise ValueError(
            f"the bottleneck rule is defined on {BOTTLENECK_WINDOW_S:g} s windows; got "
            f"{window_s:g} s"
        )
    v = np.asarray(speeds_ms, dtype=np.float64)
    if v.ndim != 2 or v.shape[1] != len(station_ids) or len(station_ids) != len(x_m):
        raise ValueError(
            f"speed matrix shape {v.shape} does not match {len(station_ids)} station(s) and "
            f"{len(x_m)} position(s)"
        )
    for a, b in itertools.pairwise(x_m):
        if not b > a:
            raise ValueError("station positions must be strictly increasing (upstream first)")
    condition = active_condition(v)
    out: list[Bottleneck] = []
    for pair in range(condition.shape[1]):
        found = episodes_of(condition[:, pair])
        if not found:
            continue
        active_windows = [k for lo, hi in found for k in range(lo, hi + 1)]
        start_s = (first_window + found[0][0]) * window_s
        reach = _queue_reach(v, pair, active_windows)
        out.append(
            Bottleneck(
                pair_index=pair,
                upstream=str(station_ids[pair]),
                downstream=str(station_ids[pair + 1]),
                upstream_x_m=float(x_m[pair]),
                downstream_x_m=float(x_m[pair + 1]),
                activation_s=float(start_s),
                activation_clock=clock_label(t0_local, start_s) if t0_local else "",
                active_s=float(len(active_windows) * window_s),
                episodes=tuple(
                    (
                        float((first_window + lo) * window_s),
                        float((first_window + hi + 1) * window_s),
                    )
                    for lo, hi in found
                ),
                queue_reach_index=reach,
                queue_reach_station=str(station_ids[reach]),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class RuleOutcome:
    """One of the four C6 rules.

    Attributes:
        rule: ``location``, ``timing``, ``queue_reach`` or ``no_phantom``.
        passed: Whether it holds.
        applicable: False when there was nothing for it to test (no observed
            bottleneck active long enough); it then holds.
        detail: Plain-language statement with the numbers.
    """

    rule: str
    passed: bool
    applicable: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "rule": self.rule,
            "passed": self.passed,
            "applicable": self.applicable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ObservedMatch:
    """One observed bottleneck (active long enough) against the replicates.

    Attributes:
        observed: The observed bottleneck.
        n_replicates: Replicates compared.
        n_reproduced: Replicates with a bottleneck at the same or an adjacent
            pair.
        n_same_pair: Of those, at the same pair.
        share: ``n_reproduced / n_replicates``.
        median_activation_s: Median activation of the matched bottlenecks
            (NaN when none matched).
        median_active_s: Median active duration of the matched ones.
        median_queue_reach_index: Median queue-reach station index.
        location_ok: Rule 1 for this bottleneck.
        timing_ok: Rule 2, activation part.
        duration_ok: Rule 2, duration part.
        queue_ok: Rule 3.
    """

    observed: Bottleneck
    n_replicates: int
    n_reproduced: int
    n_same_pair: int
    share: float
    median_activation_s: float
    median_active_s: float
    median_queue_reach_index: float
    location_ok: bool
    timing_ok: bool
    duration_ok: bool
    queue_ok: bool

    @property
    def activation_offset_s(self) -> float:
        """Median simulated minus observed activation [s] (NaN when unmatched)."""
        return self.median_activation_s - self.observed.activation_s

    @property
    def duration_ratio(self) -> float:
        """Median simulated over observed active duration (NaN when unmatched)."""
        if self.observed.active_s <= 0.0:
            return math.nan
        return self.median_active_s / self.observed.active_s

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "observed": self.observed.to_dict(),
            "n_replicates": self.n_replicates,
            "n_reproduced": self.n_reproduced,
            "n_same_pair": self.n_same_pair,
            "share": _num(self.share),
            "median_activation_s": _num(self.median_activation_s),
            "activation_offset_s": _num(self.activation_offset_s),
            "median_active_s": _num(self.median_active_s),
            "duration_ratio": _num(self.duration_ratio),
            "median_queue_reach_index": _num(self.median_queue_reach_index),
            "location_ok": self.location_ok,
            "timing_ok": self.timing_ok,
            "duration_ok": self.duration_ok,
            "queue_ok": self.queue_ok,
        }


@dataclass(frozen=True)
class Phantom:
    """A station pair with simulated bottlenecks the observations do not show.

    A per-pair diagnostic: rule 4 itself is counted per replicate
    (:attr:`BottleneckComparison.n_replicates_with_phantom`), so these rows
    do not decide it.

    Attributes:
        pair_index: The pair.
        upstream: Upstream station id.
        downstream: Downstream station id.
        n_replicates_long: Replicates where it is active for more than
            :data:`PHANTOM_ACTIVE_S`.
        n_replicates_any: Replicates where it activated at all.
        share_long: ``n_replicates_long`` over the replicates compared.
    """

    pair_index: int
    upstream: str
    downstream: str
    n_replicates_long: int
    n_replicates_any: int
    share_long: float

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "pair_index": self.pair_index,
            "upstream": self.upstream,
            "downstream": self.downstream,
            "n_replicates_long": self.n_replicates_long,
            "n_replicates_any": self.n_replicates_any,
            "share_long": _num(self.share_long),
        }


@dataclass(frozen=True)
class BottleneckComparison:
    """Check C6: the observed bottlenecks against the replicates.

    Attributes:
        n_replicates: Replicates compared.
        station_ids: The station order both sides were read in.
        observed: Every observed bottleneck.
        simulated: Every replicate's bottlenecks, replicate order.
        matches: One :class:`ObservedMatch` per observed bottleneck active at
            least :data:`MIN_OBSERVED_ACTIVE_S`.
        phantoms: Pairs carrying simulated bottlenecks with no observed one
            at or next to them (diagnostic, per pair).
        rules: The four rules, protocol order.
        notes: Plain statements (e.g. what the simulated speeds are).
        phantom_replicates: Indices of the replicates containing a phantom
            active for more than :data:`PHANTOM_ACTIVE_S` (rule 4's count).
    """

    n_replicates: int
    station_ids: tuple[str, ...]
    observed: tuple[Bottleneck, ...]
    simulated: tuple[tuple[Bottleneck, ...], ...]
    matches: tuple[ObservedMatch, ...]
    phantoms: tuple[Phantom, ...]
    rules: tuple[RuleOutcome, ...]
    notes: tuple[str, ...] = field(default=())
    phantom_replicates: tuple[int, ...] = field(default=())

    @property
    def n_replicates_with_phantom(self) -> int:
        """Replicates containing at least one phantom (rule 4, §5.4)."""
        return len(self.phantom_replicates)

    @property
    def phantom_share(self) -> float:
        """``n_replicates_with_phantom / n_replicates`` (NaN without replicates)."""
        return self.n_replicates_with_phantom / self.n_replicates if self.n_replicates else math.nan

    @property
    def passed(self) -> bool:
        """All four rules hold (and at least one replicate was compared)."""
        return self.n_replicates > 0 and all(r.passed for r in self.rules)

    def rule(self, name: str) -> RuleOutcome:
        """One rule by name.

        Raises:
            KeyError: No such rule.
        """
        for r in self.rules:
            if r.rule == name:
                return r
        raise KeyError(name)

    def failed_rules(self) -> tuple[RuleOutcome, ...]:
        """The rules that do not hold."""
        return tuple(r for r in self.rules if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "passed": self.passed,
            "n_replicates": self.n_replicates,
            "station_ids": list(self.station_ids),
            "rules": [r.to_dict() for r in self.rules],
            "observed": [b.to_dict() for b in self.observed],
            "matches": [m.to_dict() for m in self.matches],
            "phantoms": [p.to_dict() for p in self.phantoms],
            "n_replicates_with_phantom": self.n_replicates_with_phantom,
            "phantom_share": _num(self.phantom_share),
            "phantom_replicates": list(self.phantom_replicates),
            "simulated": [
                [
                    {
                        "pair_index": b.pair_index,
                        "activation_s": b.activation_s,
                        "active_s": b.active_s,
                        "queue_reach_index": b.queue_reach_index,
                    }
                    for b in rep
                ]
                for rep in self.simulated
            ],
            "notes": list(self.notes),
            "thresholds": thresholds(),
        }


def thresholds() -> dict[str, Any]:
    """Every C6 threshold with its protocol section (recorded in each result)."""
    return {
        "upstream_speed_max_ms": UPSTREAM_SPEED_MAX_MS,
        "upstream_speed_max": "40 mph (docs/FRISCO_PROTOCOL.md section 5)",
        "speed_difference_min_ms": SPEED_DIFFERENCE_MIN_MS,
        "speed_difference_min": "20 mph (section 5)",
        "window_s": BOTTLENECK_WINDOW_S,
        "persistence": f"{PERSISTENCE_MIN_ACTIVE} of {PERSISTENCE_WINDOWS} consecutive windows "
        "(section 5; from a secondary summary, to be verified against Chen, Skabardonis & "
        "Varaiya 2004 before the first Frisco run)",
        "min_observed_active_s": MIN_OBSERVED_ACTIVE_S,
        "adjacent_pairs": ADJACENT_PAIRS,
        "location_min_replicate_share": LOCATION_MIN_REPLICATE_SHARE,
        "activation_tolerance_s": ACTIVATION_TOLERANCE_S,
        "duration_tolerance_share": DURATION_TOLERANCE_SHARE,
        "queue_reach_tolerance_stations": QUEUE_REACH_TOLERANCE_STATIONS,
        "phantom_active_s": PHANTOM_ACTIVE_S,
        "phantom_max_replicate_share": PHANTOM_MAX_REPLICATE_SHARE,
        "phantom_counted": "per replicate: a replicate with any phantom counts once (section 5.4)",
        "matching": "observed and simulated bottlenecks matched one to one per replicate "
        "(section 5.4): most reproduced, then most at their own pair, then longest active",
        "label": "FlowState rules (docs/FRISCO_PROTOCOL.md section 5)",
    }


def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=np.float64))) if values else math.nan


def _minutes(seconds: float) -> str:
    return f"{seconds / _S_PER_MIN:.0f} min" if math.isfinite(seconds) else "n/a"


def _pair_name(b: Bottleneck) -> str:
    return f"{b.upstream}→{b.downstream}"


def match_replicate(
    significant: Sequence[Bottleneck], replicate: Sequence[Bottleneck]
) -> dict[int, Bottleneck]:
    """One replicate's one-to-one matching of rule 1 (module docstring).

    An observed bottleneck may be matched with a simulated one at the same
    pair or an adjacent one (pair indices within :data:`ADJACENT_PAIRS`);
    each simulated bottleneck serves at most one observed one, so one
    simulated bottleneck cannot reproduce two observed bottlenecks (§5.4).
    Among the assignments, the one with the most matches is taken, then the
    most same-pair matches, then the longest-active simulated bottlenecks
    (:data:`_MATCH_WEIGHT`), by :func:`scipy.optimize.linear_sum_assignment`.

    Args:
        significant: The observed bottlenecks to reproduce.
        replicate: One replicate's simulated bottlenecks.

    Returns:
        Index into ``significant`` → its matched simulated bottleneck.
    """
    if not significant or not replicate:
        return {}
    longest = max(s.active_s for s in replicate) + 1.0
    weight = np.zeros((len(significant), len(replicate)), dtype=np.float64)
    for i, obs in enumerate(significant):
        for j, sim in enumerate(replicate):
            distance = abs(sim.pair_index - obs.pair_index)
            if distance <= ADJACENT_PAIRS:
                weight[i, j] = (
                    _MATCH_WEIGHT
                    + (_SAME_PAIR_WEIGHT if distance == 0 else 0.0)
                    + sim.active_s / longest
                )
    rows, cols = linear_sum_assignment(weight, maximize=True)
    return {
        int(i): replicate[int(j)] for i, j in zip(rows, cols, strict=True) if weight[i, j] > 0.0
    }


def _is_phantom(sim: Bottleneck, observed_pairs: set[int]) -> bool:
    """Rule 4: active for more than :data:`PHANTOM_ACTIVE_S`, away from every observed pair."""
    return sim.active_s > PHANTOM_ACTIVE_S + _TOL and all(
        abs(sim.pair_index - p) > ADJACENT_PAIRS for p in observed_pairs
    )


def compare_bottlenecks(
    observed: Sequence[Bottleneck],
    simulated: Sequence[Sequence[Bottleneck]],
    *,
    station_ids: Sequence[str],
    notes: Sequence[str] = (),
) -> BottleneckComparison:
    """Apply the four C6 rules (module docstring).

    Args:
        observed: Bottlenecks of the observed day-set mean.
        simulated: One bottleneck list per replicate, read on the same
            stations and windows.
        station_ids: The station order of both sides.
        notes: Carried onto the result.

    Returns:
        The :class:`BottleneckComparison`.
    """
    n_rep = len(simulated)
    significant = [b for b in observed if b.active_s >= MIN_OBSERVED_ACTIVE_S - _TOL]
    chosen: list[list[Bottleneck]] = [[] for _ in significant]
    for rep in simulated:
        for i, sim in match_replicate(significant, rep).items():
            chosen[i].append(sim)
    matches: list[ObservedMatch] = []
    for obs, picked in zip(significant, chosen, strict=True):
        same = sum(1 for s in picked if s.pair_index == obs.pair_index)
        share = len(picked) / n_rep if n_rep else math.nan
        med_act = _median([s.activation_s for s in picked])
        med_dur = _median([s.active_s for s in picked])
        med_q = _median([float(s.queue_reach_index) for s in picked])
        matches.append(
            ObservedMatch(
                observed=obs,
                n_replicates=n_rep,
                n_reproduced=len(picked),
                n_same_pair=same,
                share=share,
                median_activation_s=med_act,
                median_active_s=med_dur,
                median_queue_reach_index=med_q,
                location_ok=bool(n_rep and share >= LOCATION_MIN_REPLICATE_SHARE - _TOL),
                timing_ok=bool(
                    picked and abs(med_act - obs.activation_s) <= ACTIVATION_TOLERANCE_S + _TOL
                ),
                duration_ok=bool(
                    picked
                    and abs(med_dur - obs.active_s)
                    <= DURATION_TOLERANCE_SHARE * obs.active_s + _TOL
                ),
                queue_ok=bool(
                    picked
                    and abs(med_q - obs.queue_reach_index) <= QUEUE_REACH_TOLERANCE_STATIONS + _TOL
                ),
            )
        )

    observed_pairs = {b.pair_index for b in observed}
    by_pair: dict[int, list[Bottleneck]] = {}
    phantom_replicates: list[int] = []
    for r, rep in enumerate(simulated):
        if any(_is_phantom(s, observed_pairs) for s in rep):
            phantom_replicates.append(r)
        for s in rep:
            if all(abs(s.pair_index - p) > ADJACENT_PAIRS for p in observed_pairs):
                by_pair.setdefault(s.pair_index, []).append(s)
    phantoms: list[Phantom] = []
    for pair in sorted(by_pair):
        found = by_pair[pair]
        n_long = sum(1 for s in found if s.active_s > PHANTOM_ACTIVE_S + _TOL)
        phantoms.append(
            Phantom(
                pair_index=pair,
                upstream=found[0].upstream,
                downstream=found[0].downstream,
                n_replicates_long=n_long,
                n_replicates_any=len(found),
                share_long=n_long / n_rep if n_rep else math.nan,
            )
        )

    rules = _rule_outcomes(matches, phantoms, n_rep, len(observed), len(phantom_replicates))
    return BottleneckComparison(
        n_replicates=n_rep,
        station_ids=tuple(str(s) for s in station_ids),
        observed=tuple(observed),
        simulated=tuple(tuple(rep) for rep in simulated),
        matches=tuple(matches),
        phantoms=tuple(phantoms),
        rules=rules,
        notes=tuple(notes),
        phantom_replicates=tuple(phantom_replicates),
    )


def _rule_outcomes(
    matches: Sequence[ObservedMatch],
    phantoms: Sequence[Phantom],
    n_rep: int,
    n_observed: int,
    n_with_phantom: int,
) -> tuple[RuleOutcome, ...]:
    """The four rules from the per-bottleneck matches and the phantom pairs."""
    if n_rep == 0:
        why = "no simulated replicate was compared"
        return tuple(RuleOutcome(r, False, True, why) for r in RULES)
    min_active = _minutes(MIN_OBSERVED_ACTIVE_S)
    if not matches:
        nothing = (
            f"no observed bottleneck was active for at least {min_active} "
            f"({n_observed} activated in all); nothing to reproduce"
        )
        location = RuleOutcome("location", True, False, nothing)
        timing = RuleOutcome("timing", True, False, nothing)
        queue = RuleOutcome("queue_reach", True, False, nothing)
    else:
        loc_parts, tim_parts, que_parts = [], [], []
        for m in matches:
            name = _pair_name(m.observed)
            loc_parts.append(
                f"{name}: reproduced at the same or an adjacent pair in {m.n_reproduced} of "
                f"{m.n_replicates} replicates ({m.share:.0%}; needs "
                f"{LOCATION_MIN_REPLICATE_SHARE:.0%})"
            )
            if m.n_reproduced:
                tim_parts.append(
                    f"{name}: median activation {m.activation_offset_s / _S_PER_MIN:+.0f} min "
                    f"from the observed {m.observed.activation_clock or _minutes(m.observed.activation_s)} "
                    f"(tolerance {_minutes(ACTIVATION_TOLERANCE_S)}); median duration "
                    f"{_minutes(m.median_active_s)} against {_minutes(m.observed.active_s)} "
                    f"observed ({m.duration_ratio - 1.0:+.0%}; tolerance "
                    f"{DURATION_TOLERANCE_SHARE:.0%})"
                )
                que_parts.append(
                    f"{name}: median queue reach at station index "
                    f"{m.median_queue_reach_index:g} against {m.observed.queue_reach_index} "
                    f"({m.observed.queue_reach_station}) observed (tolerance "
                    f"{QUEUE_REACH_TOLERANCE_STATIONS} station)"
                )
            else:
                tim_parts.append(f"{name}: not reproduced in any replicate, no timing to compare")
                que_parts.append(f"{name}: not reproduced in any replicate, no queue to compare")
        location = RuleOutcome(
            "location", all(m.location_ok for m in matches), True, "; ".join(loc_parts)
        )
        timing = RuleOutcome(
            "timing",
            all(m.timing_ok and m.duration_ok for m in matches),
            True,
            "; ".join(tim_parts),
        )
        queue = RuleOutcome(
            "queue_reach", all(m.queue_ok for m in matches), True, "; ".join(que_parts)
        )
    share = n_with_phantom / n_rep
    holds = share <= PHANTOM_MAX_REPLICATE_SHARE + _TOL
    phantom_text = (
        f"{n_with_phantom} of {n_rep} replicates ({share:.0%}; limit "
        f"{PHANTOM_MAX_REPLICATE_SHARE:.0%}) contain a bottleneck active for more than "
        f"{_minutes(PHANTOM_ACTIVE_S)} away from every observed one (counted once per replicate)"
    )
    long_pairs = [p for p in phantoms if p.n_replicates_long]
    if long_pairs:
        phantom_text += "; by station pair: " + ", ".join(
            f"{p.upstream}→{p.downstream} in {p.n_replicates_long}" for p in long_pairs
        )
    elif not phantoms:
        phantom_text += "; no simulated bottleneck away from the observed ones"
    no_phantom = RuleOutcome("no_phantom", holds, True, phantom_text)
    return (location, timing, queue, no_phantom)
