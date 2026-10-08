"""Choose a corridor's demand level on link flows without holding demand off the network.

The selection rule of docs/PRE_FRISCO_PROGRAM.md, phase B5 (proposed as Amendment 6 to
docs/FRISCO_PROTOCOL.md; approved by the coordinator on 2026-10-07 before any run). A
demand level is one multiplier ``s`` on a scenario's mainline and on-ramp inflows (exit
fractions and the boundary unchanged). Every scale of a grid is simulated on the same
seeds, the from-arm battery's first five; this module turns those per-scale, per-seed
readings into the choice. It is pure: it simulates nothing and reads no file.

Why. The speed-objective fitter (``scripts/i24_fit_demand_scale.py``) cannot see vehicles
held off the network, and it twice chose a backlog: Amendment 2's refits realised 0.918 /
0.930 of their demand against the 0.977 floor, and round p14 chose s = 1.125 with 0.783
inserted (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.6). Protocol §7.1 and CLAUDE.md §6.3 ask for
a GEH-driven demand fit.

The rule (:func:`select_scale`), fixed before any run:

* **Objective.** The share of scored flow bins with GEH < 5 in the fit window (larger is
  better). Ties go to the smaller mean GEH over the same bins, then to the smaller change
  ``|s - reference|`` (the from-arm's own level: I-24 0.925, I-94 1), and an exact tie on
  all three to the smaller scale (a deterministic last resort the amendment does not name).
  Speed RMSPE is reported only.
* **Constraint.** The mean over the fit seeds of each seed's inserted fraction (departed /
  planned vehicles, the battery's ``demand_realized_fraction``) must be at least
  ``min_inserted`` = the from-arm battery's mean realised share minus 0.01 (Amendment 2's
  clarification; §8.4.5 C2). If no scale qualifies, the selection is ``constraint_unmet``:
  no scale is chosen and the fit stops.

The objective is the estimator the corridor's own link-flow criterion uses, read on the fit
window only, so the fit optimises what the C3 reading later judges:

* :func:`replicate_mean_objective`: the GEH of each bin's flow averaged over the seeds, as
  ``scripts/i24_validate.py``'s link-flow row scores a battery's replicate-mean flows (I-24:
  the criterion row's (section, 5-min window) bins of 06:30-07:30 against
  ``hourly_flows_veh_h_recommended``, 07:30-08:30 held out);
* :func:`pooled_objective`: every seed's comparisons pooled, as the corridor battery and the
  baseline gate's C1 pool station-hours over replicates (I-94: the calibration-day
  station-hours, hours anchored at the study period's start).

Grids (:data:`I24_COARSE`, :func:`refine_scales`, :data:`I94_GRID`): I-24 runs a coarse
round 0.6-1.1 by 0.1, then ±2 steps of 0.025 around the coarse round's constrained choice;
I-94 one round of one corridor-wide factor, 0.95-1.05 by 0.025, inside the data-quality
artifact's count uncertainty (:func:`within_count_error`). Two report-only readings live here
too, since they are as pure: :func:`breakdown_flags` (the coordinator's breakdown reading,
pre-registered 2026-10-07) and :func:`departed_share_locks` (protocol §9.5's no-lock rule).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from statistics import median
from typing import Any, Final

from calibration.demand import geh

#: GEH bound of the objective (strict ``<``; FHWA TAT Vol. III 2004, protocol §4 C1).
GEH_THRESHOLD: Final[float] = 5.0
#: The constraint's tolerance below the from-arm's realised share (FRISCO_PROTOCOL
#: Amendment 2 clarification: one percentage point).
INSERTION_TOLERANCE: Final[float] = 0.01
#: Seeds per scale: the from-arm battery's first five (B5, new).
N_SEEDS: Final[int] = 5
#: I-24's coarse round (the fitter's corrected-profile grid).
I24_COARSE: Final[tuple[float, ...]] = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1)
#: I-24's refine round: ±REFINE_HALF_WIDTH steps of REFINE_STEP around the coarse choice.
REFINE_STEP: Final[float] = 0.025
REFINE_HALF_WIDTH: Final[int] = 2
#: I-94's one round: one corridor-wide factor.
I94_GRID: Final[tuple[float, ...]] = (0.95, 0.975, 1.0, 1.025, 1.05)
#: Scales are compared and recorded at this many decimals (the grids' own resolution and more).
SCALE_DECIMALS: Final[int] = 6
#: Report-only breakdown reading (coordinator's decision, 2026-10-07): a replicate realising
#: less than BREAKDOWN_FLOOR of its demand while the battery's mean is at least BREAKDOWN_MIN_MEAN.
BREAKDOWN_FLOOR: Final[float] = 0.9
BREAKDOWN_MIN_MEAN: Final[float] = 0.95
#: Protocol §9.5: a seed collapses when its departed share is below this ratio of the median.
LOCK_MEDIAN_RATIO: Final[float] = 0.8

ESTIMATORS: Final[tuple[str, ...]] = ("replicate_mean", "pooled")


def _scale_key(scale: float) -> float:
    return round(float(scale), SCALE_DECIMALS)


@dataclass(frozen=True)
class ObjectiveReading:
    """The objective on one scale: scored bins, those under GEH 5, their mean GEH.

    Attributes:
        estimator: ``replicate_mean`` or ``pooled`` (module docstring).
        n_bins: Scored bins (comparisons).
        n_under: Bins with GEH < :data:`GEH_THRESHOLD`.
        mean_geh: Mean GEH over the scored bins; NaN when there is none.
    """

    estimator: str
    n_bins: int
    n_under: int
    mean_geh: float

    @property
    def share(self) -> Fraction:
        """``n_under / n_bins`` exactly (ties are exact); raises on no bin."""
        if self.n_bins <= 0:
            raise ValueError("no scored bin: the objective is not defined")
        return Fraction(self.n_under, self.n_bins)

    def to_dict(self) -> dict[str, Any]:
        """JSON form: the counts, the share as a float and the mean GEH."""
        return {
            "estimator": self.estimator,
            "n_bins": self.n_bins,
            "n_under_5": self.n_under,
            "under_5": float(self.share) if self.n_bins > 0 else None,
            "mean_geh": self.mean_geh if math.isfinite(self.mean_geh) else None,
        }


def _reading(estimator: str, values: Sequence[float], threshold: float) -> ObjectiveReading:
    n_under = sum(1 for g in values if g < threshold)
    mean = math.fsum(values) / len(values) if values else math.nan
    return ObjectiveReading(estimator, len(values), n_under, mean)


def replicate_mean_objective(
    sim_flows: Sequence[Sequence[float]],
    obs_flows: Sequence[float],
    *,
    threshold: float = GEH_THRESHOLD,
) -> ObjectiveReading:
    """GEH of each bin's seed-mean flow against the observed flow.

    Args:
        sim_flows: ``sim_flows[k][b]``: seed ``k``'s hourly flow in bin ``b`` [veh/h].
        obs_flows: ``obs_flows[b]``: the observed hourly flow in bin ``b`` [veh/h].
        threshold: The GEH bound (strict).

    Returns:
        The reading over the bins where the observed flow and every seed's flow are finite.

    Raises:
        ValueError: No seed, or a seed whose bins do not match the observed ones.
    """
    if not sim_flows:
        raise ValueError("no seed's flows")
    n = len(obs_flows)
    if any(len(s) != n for s in sim_flows):
        raise ValueError(f"every seed needs {n} bins, as the observed flows have")
    values: list[float] = []
    for b in range(n):
        o = float(obs_flows[b])
        col = [float(s[b]) for s in sim_flows]
        if not math.isfinite(o) or not all(math.isfinite(v) for v in col):
            continue
        values.append(geh(math.fsum(col) / len(col), o))
    return _reading("replicate_mean", values, threshold)


def pooled_objective(
    pairs: Sequence[Sequence[tuple[float, float]]], *, threshold: float = GEH_THRESHOLD
) -> ObjectiveReading:
    """Every seed's (simulated, observed) hourly-flow comparisons pooled.

    Args:
        pairs: ``pairs[k]``: seed ``k``'s comparisons as ``(sim_veh_h, obs_veh_h)``.
        threshold: The GEH bound (strict).

    Returns:
        The reading over every finite comparison of every seed.

    Raises:
        ValueError: No seed.
    """
    if not pairs:
        raise ValueError("no seed's comparisons")
    values = [
        geh(float(m), float(c))
        for seed in pairs
        for m, c in seed
        if math.isfinite(float(m)) and math.isfinite(float(c))
    ]
    return _reading("pooled", values, threshold)


@dataclass(frozen=True)
class ScaleReading:
    """One grid scale's readings over the fit seeds.

    Attributes:
        scale: The demand multiplier.
        seeds: The fit seeds, in order.
        inserted: Each seed's inserted fraction (departed / planned), in seed order.
        objective: The objective over the fit window (an estimator of this module).
    """

    scale: float
    seeds: tuple[int, ...]
    inserted: tuple[float, ...]
    objective: ObjectiveReading

    @property
    def mean_inserted(self) -> float:
        """Mean inserted fraction over the seeds."""
        return math.fsum(self.inserted) / len(self.inserted)


def min_inserted_from(realised_mean: float, tolerance: float = INSERTION_TOLERANCE) -> float:
    """The constraint's floor: the from-arm battery's mean realised share minus ``tolerance``.

    Raises:
        ValueError: A realised share outside (0, 1] or a negative tolerance.
    """
    if not (math.isfinite(realised_mean) and 0.0 < realised_mean <= 1.0):
        raise ValueError(f"realised share must be in (0, 1], got {realised_mean}")
    if not tolerance >= 0.0:
        raise ValueError(f"tolerance must be >= 0, got {tolerance}")
    return realised_mean - tolerance


def refine_scales(
    centre: float, step: float = REFINE_STEP, half_width: int = REFINE_HALF_WIDTH
) -> tuple[float, ...]:
    """The refine round: ``centre ± k·step`` for k = 1..half_width, positive scales only."""
    out = [
        round(centre + k * step, 3)
        for k in range(-half_width, half_width + 1)
        if k != 0 and centre + k * step > 0.0
    ]
    return tuple(out)


def within_count_error(scales: Sequence[float], count_error: float) -> bool:
    """Whether every factor lies in ``[1 - count_error, 1 + count_error]`` (protocol §7.1)."""
    if not (math.isfinite(count_error) and count_error >= 0.0):
        raise ValueError(f"count_error must be a finite value >= 0, got {count_error}")
    return all(abs(float(s) - 1.0) <= count_error + 1e-9 for s in scales)


@dataclass(frozen=True)
class Selection:
    """The rule's result (:func:`select_scale`).

    Attributes:
        chosen_scale: The chosen scale; None when the constraint is unmet.
        constraint_unmet: True when no scale's mean inserted fraction reaches the floor.
        min_inserted: The floor.
        reference_scale: The from-arm's level (the "smaller change" is measured from it).
        seeds: The fit seeds.
        qualifying_scales: Scales meeting the constraint, ascending.
        order: Qualifying scales, best first by the rule.
        decided_by: Which key separated the choice from the runner-up (``share``,
            ``mean_geh``, ``change``, ``scale``), ``only`` with one qualifying scale, None
            when unmet.
        reason: One sentence for logs and scenario headers.
        per_scale: Every scale's readings as recorded (ascending).
    """

    chosen_scale: float | None
    constraint_unmet: bool
    min_inserted: float
    reference_scale: float
    seeds: tuple[int, ...]
    qualifying_scales: tuple[float, ...]
    order: tuple[float, ...]
    decided_by: str | None
    reason: str
    per_scale: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "rule": RULE_TEXT,
            "chosen_scale": self.chosen_scale,
            "constraint_unmet": self.constraint_unmet,
            "min_inserted": self.min_inserted,
            "reference_scale": self.reference_scale,
            "seeds": list(self.seeds),
            "qualifying_scales": list(self.qualifying_scales),
            "order": list(self.order),
            "decided_by": self.decided_by,
            "reason": self.reason,
            "per_scale": [dict(p) for p in self.per_scale],
        }


RULE_TEXT: Final[str] = (
    "among the scales whose mean inserted fraction over the fit seeds is at least min_inserted "
    "(the from-arm battery's mean realised share - 0.01), the largest share of fit-window flow bins "
    "with GEH < 5; ties to the smaller mean GEH over the same bins, then the smaller change "
    "|s - reference_scale|, then the smaller scale; no qualifying scale: constraint_unmet, nothing "
    "chosen (docs/PRE_FRISCO_PROGRAM.md B5, docs/FRISCO_PROTOCOL.md Amendment 6)"
)


def _change(r: ScaleReading, reference: float) -> float:
    return round(abs(float(r.scale) - float(reference)), 9)


def _rank_key(r: ScaleReading, reference: float) -> tuple[Fraction, float, float, float]:
    mean = r.objective.mean_geh
    return (-r.objective.share, mean, _change(r, reference), _scale_key(r.scale))


def _decided_by(best: ScaleReading, runner_up: ScaleReading, reference: float) -> str:
    a, b = _rank_key(best, reference), _rank_key(runner_up, reference)
    for name, x, y in zip(("share", "mean_geh", "change", "scale"), a, b, strict=True):
        if x != y:
            return name
    return "scale"  # unreachable: scales are unique


def _summary(r: ScaleReading, floor: float, reference: float) -> dict[str, Any]:
    return {
        "scale": _scale_key(r.scale),
        "mean_inserted": r.mean_inserted,
        "inserted_per_seed": list(r.inserted),
        "meets_constraint": bool(r.mean_inserted >= floor),
        "objective": r.objective.to_dict(),
        "change": _change(r, reference),
    }


def _check(readings: Sequence[ScaleReading], seeds: Sequence[int] | None) -> tuple[int, ...]:
    if not readings:
        raise ValueError("no scale to choose from")
    keys = [_scale_key(r.scale) for r in readings]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a scale is given twice: {sorted(keys)}")
    first = tuple(int(s) for s in readings[0].seeds)
    want = first if seeds is None else tuple(int(s) for s in seeds)
    for r in readings:
        if not (math.isfinite(r.scale) and r.scale > 0.0):
            raise ValueError(f"scale {r.scale} is not a positive number")
        got = tuple(int(s) for s in r.seeds)
        if not got:
            raise ValueError(f"scale {r.scale}: no seed")
        if got != want:
            raise ValueError(
                f"scale {r.scale}: seeds {list(got)} are not the fit seeds {list(want)}"
            )
        if len(r.inserted) != len(got):
            raise ValueError(
                f"scale {r.scale}: {len(r.inserted)} inserted fractions for {len(got)} seeds"
            )
        if not all(math.isfinite(f) and 0.0 <= f <= 1.0 for f in r.inserted):
            raise ValueError(f"scale {r.scale}: an inserted fraction outside [0, 1]")
        if r.objective.n_bins <= 0:
            raise ValueError(f"scale {r.scale}: no scored bin")
    return want


def select_scale(
    readings: Sequence[ScaleReading],
    *,
    min_inserted: float,
    reference_scale: float,
    seeds: Sequence[int] | None = None,
) -> Selection:
    """Apply the rule (module docstring) to per-scale, per-seed readings.

    Args:
        readings: One reading per scale (any order; scales unique).
        min_inserted: The constraint's floor (:func:`min_inserted_from`).
        reference_scale: The from-arm's level; "the smaller change" is measured from it.
        seeds: The fit seeds every reading must carry, in order (default: the first
            reading's).

    Returns:
        The :class:`Selection`; ``chosen_scale`` None and ``constraint_unmet`` True when no
        scale qualifies.

    Raises:
        ValueError: No reading, a scale given twice, readings on other seeds than the fit
            seeds, an inserted fraction outside [0, 1], a scale without a scored bin, or a
            floor outside (0, 1].
    """
    if not (math.isfinite(min_inserted) and 0.0 < min_inserted <= 1.0):
        raise ValueError(f"min_inserted must be in (0, 1], got {min_inserted}")
    fit_seeds = _check(readings, seeds)
    ascending = sorted(readings, key=lambda r: _scale_key(r.scale))
    per_scale = tuple(_summary(r, min_inserted, reference_scale) for r in ascending)
    qualifying = [r for r in ascending if r.mean_inserted >= min_inserted]
    n, k, n_seeds = len(ascending), len(qualifying), len(fit_seeds)
    if not qualifying:
        top = max(ascending, key=lambda r: (r.mean_inserted, -_scale_key(r.scale)))
        return Selection(
            chosen_scale=None,
            constraint_unmet=True,
            min_inserted=min_inserted,
            reference_scale=reference_scale,
            seeds=fit_seeds,
            qualifying_scales=(),
            order=(),
            decided_by=None,
            reason=(
                f"CONSTRAINT UNMET: no scale of {n} has a mean inserted fraction over the "
                f"{n_seeds} fit seeds of at least {min_inserted:.4f} (highest {top.mean_inserted:.4f}"
                f", s = {_scale_key(top.scale):g}); nothing is chosen and the fit stops"
            ),
            per_scale=per_scale,
        )
    order = sorted(qualifying, key=lambda r: _rank_key(r, reference_scale))
    best = order[0]
    decided = "only" if k == 1 else _decided_by(best, order[1], reference_scale)
    obj = best.objective
    reason = (
        f"s = {_scale_key(best.scale):g}: GEH < 5 on {obj.n_under} of {obj.n_bins} fit-window bins "
        f"({float(obj.share):.4f}, mean GEH {obj.mean_geh:.3f}; {obj.estimator} over {n_seeds} seeds), "
        f"the best of the {k} of {n} scales whose mean inserted fraction is at least "
        f"{min_inserted:.4f} (this one {best.mean_inserted:.4f})"
    )
    if decided == "only":
        reason += "; the only qualifying scale"
    elif decided != "share":
        runner = order[1]
        reason += f"; tied on the share with s = {_scale_key(runner.scale):g}, decided by {decided}"
    return Selection(
        chosen_scale=_scale_key(best.scale),
        constraint_unmet=False,
        min_inserted=min_inserted,
        reference_scale=reference_scale,
        seeds=fit_seeds,
        qualifying_scales=tuple(_scale_key(r.scale) for r in qualifying),
        order=tuple(_scale_key(r.scale) for r in order),
        decided_by=decided,
        reason=reason,
        per_scale=per_scale,
    )


def breakdown_flags(
    realised: Sequence[float],
    *,
    floor: float = BREAKDOWN_FLOOR,
    min_mean: float = BREAKDOWN_MIN_MEAN,
) -> list[bool] | None:
    """The breakdown reading per replicate (report-only; coordinator's decision 2026-10-07).

    A replicate is a breakdown when it realises less than ``floor`` of its planned demand
    while the battery's mean realised share is at least ``min_mean``.

    Returns:
        One flag per replicate, or None when the battery's mean is below ``min_mean`` (the
        reading does not apply) or there is no replicate.
    """
    vals = [float(v) for v in realised]
    if not vals:
        return None
    if math.fsum(vals) / len(vals) < min_mean:
        return None
    return [v < floor for v in vals]


def departed_share_locks(
    departed: Sequence[float], *, ratio: float = LOCK_MEDIAN_RATIO
) -> list[bool]:
    """Protocol §9.5's no-lock rule per seed: departed share below ``ratio`` x the median.

    Returns:
        One flag per seed (True = the seed collapsed); empty without seeds.
    """
    vals = [float(v) for v in departed]
    if not vals:
        return []
    m = median(vals)
    return [v < ratio * m for v in vals]
