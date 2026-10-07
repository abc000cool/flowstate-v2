"""The driver-settings calibration rule of docs/FRISCO_PROTOCOL.md Amendment 1.

Amendment 1 (2026-10-06, written before any run that uses it) lets two driver
settings be calibrated, on fixed grids: the population's mean maximum
acceleration ``a_max`` at the measured mean + k·sd, k ∈ :data:`K_GRID`, and
SUMO's keep-right eagerness ``lc_keep_right`` ∈ :data:`KEEP_RIGHT_GRID`. Every
grid pair is run and scored on two targets:

* **lane use** — the root-mean-square error of the lane shares, in percentage
  points (:func:`validation.lane_use.share_rmse_pp`);
* **discharge** — the flow at the corridor's downstream peak sections while
  the bottleneck is active against the observed one, as a relative error
  (:func:`discharge_error`: the mean over the sections of
  ``|q_sim − q_obs| / q_obs``).

The rule (:func:`select_pair`), word for word from the amendment: among the
pairs whose lane-share RMSE is within 1 percentage point of the grid's
minimum, choose the one whose discharge error is smallest; ties go to the
smaller change (k, then ``lc_keep_right`` nearer its current 0). If no pair
improves lane use or discharge against the current setting, the current
setting stays and the report says so.

How "improves" is read (:func:`improves`), stated here because the amendment
does not define a noise allowance: a pair improves a target when its error on
that target is **strictly smaller** than the current setting's, with no noise
band (I-24 runs one seed, I-94 two; the amendment fixes neither a band nor a
replicate count for one). Applied literally: the current setting stays when
no pair improves either target; otherwise the rule's choice stands, even when
it is worse than the current setting on one of the two targets (the rule
trades lane use within its 1-point band for discharge).

Ties: two discharge errors are equal when they agree to
:data:`TIE_REL_TOL` (relative) — floating-point noise, not a tolerance on the
measurement. The band includes its edge (RMSE ≤ minimum + 1 pp, to
:data:`BAND_ABS_TOL`).

Pure functions; the grid runner is ``scripts/calibrate_driver_grid.py``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

K_GRID: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)
"""Amendment 1, item 1: mean ``a_max`` = measured mean + k·sd."""

KEEP_RIGHT_GRID: Final[tuple[float, ...]] = (0.0, 0.1, 0.25, 0.5, 1.0)
"""Amendment 1, item 2: ``lc_keep_right`` values."""

CURRENT: Final[tuple[float, float]] = (0.0, 0.0)
"""The current setting ``(k, lc_keep_right)``: the measured mean ``a_max``, keep-right 0."""

RMSE_BAND_PP: Final[float] = 1.0
"""Amendment 1: pairs within this many percentage points of the grid's minimum RMSE."""

BAND_ABS_TOL: Final[float] = 1e-9
"""Floating-point slack on the band's edge [pp]."""

TIE_REL_TOL: Final[float] = 1e-9
"""Discharge errors equal to this relative precision are a tie."""

RULE_TEXT: Final[str] = (
    "docs/FRISCO_PROTOCOL.md Amendment 1: among the pairs whose lane-share RMSE is within "
    "1 percentage point of the grid's minimum, choose the one whose discharge error is "
    "smallest; ties go to the smaller change (k, then lc_keep_right nearer its current 0). "
    "If no pair improves lane use or discharge against the current setting, the current "
    "setting stays and the report says so."
)

IMPROVES_TEXT: Final[str] = (
    "a pair improves a target when its error on that target is strictly smaller than the "
    "current setting's (k = 0, lc_keep_right = 0), with no noise band — the amendment fixes "
    "none; the current setting stays only when no pair improves either target"
)

DISCHARGE_ERROR_TEXT: Final[str] = (
    "mean over the corridor's discharge sections of |q_sim - q_obs| / q_obs, q the flow "
    "over the scored window [veh/h] (seed-mean when several seeds run)"
)


def relative_error(sim: float, obs: float) -> float:
    """``|sim − obs| / obs``.

    Raises:
        ValueError: ``obs`` not positive, or a non-finite value.
    """
    if not (math.isfinite(sim) and math.isfinite(obs)):
        raise ValueError("relative_error: non-finite value")
    if obs <= 0.0:
        raise ValueError(f"relative_error: the observed value must be > 0, got {obs}")
    return abs(sim - obs) / obs


def discharge_error(sim: Mapping[str, float], obs: Mapping[str, float]) -> float:
    """Mean relative discharge error over the sections (:data:`DISCHARGE_ERROR_TEXT`).

    Args:
        sim: Simulated flow by section [veh/h].
        obs: Observed flow by section [veh/h], the same keys.

    Raises:
        ValueError: The section sets differ or are empty.
    """
    if set(sim) != set(obs) or not obs:
        raise ValueError(
            f"discharge_error: simulated sections {sorted(sim)} != observed {sorted(obs)}"
        )
    return sum(relative_error(float(sim[s]), float(obs[s])) for s in obs) / len(obs)


@dataclass(frozen=True)
class GridScore:
    """One grid pair's two scores.

    Attributes:
        k: ``a_max`` shift in measured standard deviations.
        lc_keep_right: SUMO ``lcKeepRight``.
        lane_rmse_pp: Lane-share RMSE [percentage points]; ``None`` when not scored.
        discharge_error: Relative discharge error (fraction); ``None`` when not scored.
    """

    k: float
    lc_keep_right: float
    lane_rmse_pp: float | None
    discharge_error: float | None

    @property
    def pair(self) -> tuple[float, float]:
        """``(k, lc_keep_right)``."""
        return (float(self.k), float(self.lc_keep_right))

    @property
    def scored(self) -> bool:
        """Both scores present and finite."""
        return (
            self.lane_rmse_pp is not None
            and self.discharge_error is not None
            and math.isfinite(self.lane_rmse_pp)
            and math.isfinite(self.discharge_error)
        )


def improves(pair: GridScore, current: GridScore) -> dict[str, bool]:
    """Which targets ``pair`` improves against ``current`` (:data:`IMPROVES_TEXT`)."""
    if not (pair.scored and current.scored):
        raise ValueError("improves: both pairs must be scored")
    assert pair.lane_rmse_pp is not None and current.lane_rmse_pp is not None
    assert pair.discharge_error is not None and current.discharge_error is not None
    return {
        "lane_use": pair.lane_rmse_pp < current.lane_rmse_pp,
        "discharge": pair.discharge_error < current.discharge_error,
    }


@dataclass(frozen=True)
class Selection:
    """The rule's outcome on one grid.

    Attributes:
        chosen: The pair to use ``(k, lc_keep_right)`` — the current setting
            when it stays.
        current: The current setting.
        current_stays: The chosen pair is the current setting (for either
            reason in ``outcome``).
        outcome: ``"no_improvement"`` (no pair improves either target: the
            current setting stays by the amendment's last sentence),
            ``"rule_chose_current"`` (some pair improves a target, but the rule
            picks the current setting) or ``"rule_chose_other"``.
        min_rmse_pp: The grid's minimum lane-share RMSE.
        band_pp: ``min_rmse_pp + 1`` — the band's upper edge.
        candidates: Pairs inside the band, in grid order.
        best_discharge_error: The smallest discharge error among the candidates.
        tied: Candidates tied at that error (before the tie-break).
        improvers: Pairs (other than the current) that improve at least one
            target, with which.
        unscored: Pairs left out of the rule for a missing score.
        explanation: The decision in words, for the artifact and the report.
    """

    chosen: tuple[float, float]
    current: tuple[float, float]
    current_stays: bool
    outcome: str
    min_rmse_pp: float
    band_pp: float
    candidates: tuple[tuple[float, float], ...]
    best_discharge_error: float
    tied: tuple[tuple[float, float], ...]
    improvers: tuple[tuple[tuple[float, float], tuple[str, ...]], ...]
    unscored: tuple[tuple[float, float], ...] = ()
    explanation: str = ""
    rule: str = field(default=RULE_TEXT)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready record."""
        return {
            "rule": self.rule,
            "improves_reading": IMPROVES_TEXT,
            "chosen": {"k": self.chosen[0], "lc_keep_right": self.chosen[1]},
            "current": {"k": self.current[0], "lc_keep_right": self.current[1]},
            "current_stays": self.current_stays,
            "outcome": self.outcome,
            "min_lane_rmse_pp": self.min_rmse_pp,
            "band_upper_pp": self.band_pp,
            "candidates": [{"k": k, "lc_keep_right": r} for k, r in self.candidates],
            "best_discharge_error": self.best_discharge_error,
            "tied": [{"k": k, "lc_keep_right": r} for k, r in self.tied],
            "improvers": [
                {"k": p[0], "lc_keep_right": p[1], "improves": list(what)}
                for p, what in self.improvers
            ],
            "unscored": [{"k": k, "lc_keep_right": r} for k, r in self.unscored],
            "explanation": self.explanation,
        }


def _tie_break_key(pair: tuple[float, float], current: tuple[float, float]) -> tuple[float, float]:
    """Smaller change first: |k − k_cur|, then |lc_keep_right − its current|."""
    return (abs(pair[0] - current[0]), abs(pair[1] - current[1]))


def select_pair(
    scores: Iterable[GridScore],
    *,
    current: tuple[float, float] = CURRENT,
    band_pp: float = RMSE_BAND_PP,
) -> Selection:
    """Apply the Amendment-1 rule (module docstring) to a scored grid.

    Args:
        scores: One :class:`GridScore` per pair; pairs without both scores
            are left out of the rule and listed as ``unscored``.
        current: The current setting; it must be among the scored pairs.
        band_pp: The RMSE band [pp] (the amendment's 1).

    Returns:
        The :class:`Selection`.

    Raises:
        ValueError: A pair appears twice, the current setting is missing or
            unscored, or nothing is scored.
    """
    rows = list(scores)
    pairs = [r.pair for r in rows]
    if len(set(pairs)) != len(pairs):
        raise ValueError("select_pair: a grid pair appears twice")
    cur_pair = (float(current[0]), float(current[1]))
    by_pair = {r.pair: r for r in rows}
    if cur_pair not in by_pair:
        raise ValueError(f"select_pair: the current setting {cur_pair} is not in the grid")
    cur = by_pair[cur_pair]
    if not cur.scored:
        raise ValueError(
            f"select_pair: the current setting {cur_pair} has no score — the rule compares "
            "every pair against it"
        )
    scored = [r for r in rows if r.scored]
    unscored = tuple(r.pair for r in rows if not r.scored)
    rmse = {r.pair: float(r.lane_rmse_pp) for r in scored if r.lane_rmse_pp is not None}
    disc = {r.pair: float(r.discharge_error) for r in scored if r.discharge_error is not None}
    min_rmse = min(rmse.values())
    upper = min_rmse + band_pp
    candidates = [r.pair for r in scored if rmse[r.pair] <= upper + BAND_ABS_TOL]
    best = min(disc[p] for p in candidates)
    tied = [p for p in candidates if math.isclose(disc[p], best, rel_tol=TIE_REL_TOL, abs_tol=0.0)]
    winner = min(tied, key=lambda p: _tie_break_key(p, cur_pair))
    improvers: list[tuple[tuple[float, float], tuple[str, ...]]] = []
    for r in scored:
        if r.pair == cur_pair:
            continue
        what = tuple(name for name, better in improves(r, cur).items() if better)
        if what:
            improvers.append((r.pair, what))
    if not improvers:
        chosen, outcome = cur_pair, "no_improvement"
        explanation = (
            f"No pair improves lane use (current {rmse[cur_pair]:.3f} pp) or discharge "
            f"(current {disc[cur_pair]:.4f}) against the current setting k = {cur_pair[0]:g}, "
            f"lc_keep_right = {cur_pair[1]:g}: the current setting stays (Amendment 1)."
        )
    else:
        chosen = winner
        outcome = "rule_chose_current" if winner == cur_pair else "rule_chose_other"
        explanation = (
            f"Grid minimum lane-share RMSE {min_rmse:.3f} pp; {len(candidates)} pair(s) within "
            f"{band_pp:g} pp of it (<= {upper:.3f} pp); smallest discharge error among them "
            f"{best:.4f}"
            + (
                f", tied between {len(tied)} pairs and broken to the smaller change"
                if len(tied) > 1
                else ""
            )
            + f": k = {winner[0]:g}, lc_keep_right = {winner[1]:g} (lane RMSE "
            f"{rmse[winner]:.3f} pp, discharge error {disc[winner]:.4f}; current setting "
            f"{rmse[cur_pair]:.3f} pp, {disc[cur_pair]:.4f})."
            + (" The rule keeps the current setting." if winner == cur_pair else "")
        )
    return Selection(
        chosen=chosen,
        current=cur_pair,
        current_stays=chosen == cur_pair,
        outcome=outcome,
        min_rmse_pp=min_rmse,
        band_pp=upper,
        candidates=tuple(candidates),
        best_discharge_error=best,
        tied=tuple(tied),
        improvers=tuple(improvers),
        unscored=unscored,
        explanation=explanation,
    )


def grid_pairs(
    k_grid: Sequence[float] = K_GRID, keep_right_grid: Sequence[float] = KEEP_RIGHT_GRID
) -> list[tuple[float, float]]:
    """Every ``(k, lc_keep_right)`` pair, k outermost, in grid order."""
    return [(float(k), float(r)) for k in k_grid for r in keep_right_grid]


def is_amendment_grid(pairs: Sequence[tuple[float, float]]) -> bool:
    """Whether ``pairs`` is exactly the Amendment-1 grid (any order)."""
    return sorted(pairs) == sorted(grid_pairs())
