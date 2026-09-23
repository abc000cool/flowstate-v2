"""Demand (inflow) calibration against observed link counts (CLAUDE.md §6.3).

Iterative proportional scaling: simulate the corridor with the current inflow
profile, compare simulated link counts to observed counts per time bin with
the GEH statistic (FHWA Traffic Analysis Toolbox Vol. III, 2004 edition
FHWA-HRT-04-040, "Wisconsin DOT freeway model calibration criteria" table:
GEH < 5 for individual link flows in > 85% of cases; the 2019 update
FHWA-HOP-18-036 prescribes no fixed GEH target — see
``validation.criteria`` for the verified wording), and scale each inflow step
by the (damped) observed/simulated ratio of its overlapping bins until the
criterion is met or the iteration cap is reached. GEH is defined on *hourly*
volumes; shorter bins are scaled to hourly-equivalent flows on both sides.

Two call forms share the fitter core (:func:`fit_inflow_profile`):

* ``fit_inflow(scenario, counts, *, created_at, source, ...)`` — the §6.3
  entry point. The initial profile is the scenario network's own inflow and,
  unless ``simulate_fn`` is given, the microscopic tier supplies the
  simulator (``microsim.demand_adapter.make_simulate_fn``, imported lazily so
  this package never depends on SUMO at import time).
* ``fit_inflow(observed_counts, initial_profile, simulate_fn, ...)`` — the
  engine-agnostic form: the simulator is *injected*, which keeps the fitter
  unit-testable against a fast surrogate.

All interfaces are SI (veh/s); GEH is computed on veh/h via
``flowstate_core.units`` at the comparison boundary only.

A second, engine-agnostic fitter lives here too: :func:`fit_multipliers`
searches a handful of named scalar multipliers (ramp inflow levels, exit
fractions, a boundary speed schedule, ...) against an injected objective —
deterministic compass search on a shrinking grid, memoized and resumable,
with the round's candidates evaluated through an injected ``map_fn`` so a
driver script can spread them over a process pool.

Finally, the *direct* path from measurements to a scenario's boundary
conditions: :func:`demand_from_observations` and
:func:`ramp_flows_from_observations` read a ``flowstate.observations/1``
artifact (:mod:`calibration.observations`) and produce the inflow and ramp
steps a corridor scenario needs, recorded together as a
``flowstate.demand/1`` :class:`DemandArtifact`. No fitting is involved there —
the numbers are the detectors', scaled into SI — so it is the fast path for
onboarding a corridor whose boundary is instrumented; the GEH fitter above
remains the way to correct a boundary that is not.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import Any, Literal, overload

import numpy as np
import pandas as pd

from calibration.observations import Observations
from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import ScenarioConfig
from flowstate_core.units import veh_h_to_veh_s, veh_s_to_veh_h

SimulateFn = Callable[[DemandProfile], pd.DataFrame]
"""Injected simulator: profile → DataFrame with columns ``t_start_s``,
``t_end_s``, ``flow_veh_s`` on the same bins as the observed counts. The
microscopic tier provides the real implementation (a corridor run returning
binned link counts, ``microsim.demand_adapter``); tests use a fast surrogate."""

COUNT_COLUMNS: tuple[str, ...] = ("t_start_s", "t_end_s", "flow_veh_s")
"""Required columns of an observed-counts frame (SI: seconds, veh/s)."""


def geh(m_veh_h: float, c_veh_h: float) -> float:
    """GEH statistic between modeled and counted *hourly* volumes.

    ``GEH = √(2(m−c)²/(m+c))`` (FHWA Traffic Analysis Toolbox Vol. III,
    2004, Eq. 4 usage). Returns 0 when both volumes are 0.

    Args:
        m_veh_h: Modeled volume [veh/h].
        c_veh_h: Counted volume [veh/h].
    """
    denom = m_veh_h + c_veh_h
    if denom <= 0.0:
        return 0.0
    return sqrt(2.0 * (m_veh_h - c_veh_h) ** 2 / denom)


def _step_spans(steps: list[tuple[float, float]], horizon_s: float) -> list[tuple[float, float]]:
    """[start, end) span of each profile step, closed by ``horizon_s``."""
    starts = [t for t, _ in steps]
    ends = [*starts[1:], max(horizon_s, starts[-1])]
    return list(zip(starts, ends, strict=True))


def _validate_counts(observed_counts: pd.DataFrame) -> None:
    for col in COUNT_COLUMNS:
        if col not in observed_counts.columns:
            raise ValueError(f"fit_inflow: observed_counts missing column {col!r}")
    if observed_counts.empty:
        raise ValueError("fit_inflow: observed_counts holds no bins")
    starts = observed_counts["t_start_s"].to_numpy(dtype=float)
    ends = observed_counts["t_end_s"].to_numpy(dtype=float)
    if np.any(ends <= starts):
        raise ValueError("fit_inflow: every observed bin needs t_end_s > t_start_s")


def fit_inflow_profile(
    observed_counts: pd.DataFrame,
    initial_profile: DemandProfile,
    simulate_fn: SimulateFn,
    *,
    created_at: str,
    source: str,
    data_hash: str = "",
    geh_threshold: float = 5.0,
    geh_pass_frac: float = 0.85,
    max_iters: int = 25,
    max_scale_step: float = 2.0,
) -> DemandProfile:
    """Fit an inflow profile to observed link counts with an injected simulator.

    Each iteration simulates with the current profile, computes per-bin GEH
    (on veh/h), and stops when at least ``geh_pass_frac`` of bins have
    ``GEH < geh_threshold`` (the FHWA 2004 Vol. III table shape: GEH < 5 for
    85% of links). Otherwise every profile step is scaled by the
    duration-weighted mean of the observed/simulated flow ratios of the bins
    overlapping that step, clipped to ``[1/max_scale_step, max_scale_step]``
    per iteration for stability (proportional fitting with damping).

    The returned profile's ``geh_vs_counts`` is the **worst-bin (max) GEH**
    of the returned profile — a conservative, honest summary (the pass/fail
    fraction is what the criterion checks).

    Args:
        observed_counts: DataFrame with columns ``t_start_s``, ``t_end_s``
            [s] and ``flow_veh_s`` [veh/s] — observed link counts per bin.
        initial_profile: Starting ``DemandProfile`` (steps in SI veh/s).
        simulate_fn: Injected simulator (see :data:`SimulateFn`); the real
            one comes from the microscopic tier.
        created_at: ISO-8601 timestamp for the artifact (caller-supplied).
        source: Human-readable provenance of the observed counts.
        data_hash: Hash of the observed-count data (provenance).
        geh_threshold: Per-bin GEH acceptance threshold.
        geh_pass_frac: Required fraction of bins meeting the threshold.
        max_iters: Iteration cap.
        max_scale_step: Per-iteration scale-factor clip (> 1).

    Returns:
        Fitted ``DemandProfile`` (converged, or the best profile at the
        iteration cap) with ``geh_vs_counts`` set.

    Raises:
        ValueError: On malformed inputs or a simulator returning mismatched
            bins.
    """
    _validate_counts(observed_counts)
    if max_scale_step <= 1.0:
        raise ValueError(f"max_scale_step must be > 1, got {max_scale_step}")
    if not 0.0 < geh_pass_frac <= 1.0:
        raise ValueError(f"geh_pass_frac must be in (0, 1], got {geh_pass_frac}")

    obs = observed_counts.sort_values("t_start_s")
    bin_start = obs["t_start_s"].to_numpy(dtype=float)
    bin_end = obs["t_end_s"].to_numpy(dtype=float)
    obs_flow = obs["flow_veh_s"].to_numpy(dtype=float)
    horizon = float(bin_end.max())

    def make_profile(steps: list[tuple[float, float]]) -> DemandProfile:
        return DemandProfile(
            created_at=created_at,
            source=source,
            data_hash=data_hash,
            steps=steps,
            geh_vs_counts=None,
        )

    profile = make_profile(list(initial_profile.steps))
    gehs = np.empty(0)
    for iteration in range(max_iters + 1):
        sim = simulate_fn(profile).sort_values("t_start_s")
        sim_start = sim["t_start_s"].to_numpy(dtype=float)
        if sim_start.shape != bin_start.shape or not np.allclose(sim_start, bin_start):
            raise ValueError("simulate_fn returned bins that do not match observed_counts")
        sim_flow = sim["flow_veh_s"].to_numpy(dtype=float)
        gehs = np.array(
            [
                geh(veh_s_to_veh_h(m), veh_s_to_veh_h(c))
                for m, c in zip(sim_flow, obs_flow, strict=True)
            ]
        )
        # Stop on the criterion, or at the cap *without* a trailing unscored
        # scale step — the returned GEH always describes the returned profile.
        if float(np.mean(gehs < geh_threshold)) >= geh_pass_frac or iteration == max_iters:
            break
        # Proportional scaling, damped.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(sim_flow > 0, obs_flow / np.maximum(sim_flow, 1e-12), max_scale_step)
        ratios = np.clip(ratios, 1.0 / max_scale_step, max_scale_step)
        new_steps: list[tuple[float, float]] = []
        for (s_start, s_end), (_, q) in zip(
            _step_spans(list(profile.steps), horizon), profile.steps, strict=True
        ):
            overlap = np.minimum(bin_end, s_end) - np.maximum(bin_start, s_start)
            weights = np.maximum(overlap, 0.0)
            factor = float(np.average(ratios, weights=weights)) if weights.sum() > 0 else 1.0
            new_steps.append((s_start, q * factor))
        profile = make_profile(new_steps)

    return DemandProfile(
        created_at=created_at,
        source=source,
        data_hash=data_hash,
        steps=list(profile.steps),
        geh_vs_counts=float(np.max(gehs)) if gehs.size else None,
    )


@overload
def fit_inflow(
    scenario: ScenarioConfig,
    counts: pd.DataFrame,
    simulate_fn: SimulateFn | None = None,
    *,
    created_at: str,
    source: str,
    data_hash: str = "",
    geh_threshold: float = 5.0,
    geh_pass_frac: float = 0.85,
    max_iters: int = 25,
    max_scale_step: float = 2.0,
    x_ref_m: float | None = None,
    workdir: str | Path | None = None,
    seed: int | None = None,
) -> DemandProfile: ...


@overload
def fit_inflow(
    scenario: pd.DataFrame,
    counts: DemandProfile,
    simulate_fn: SimulateFn,
    *,
    created_at: str,
    source: str,
    data_hash: str = "",
    geh_threshold: float = 5.0,
    geh_pass_frac: float = 0.85,
    max_iters: int = 25,
    max_scale_step: float = 2.0,
    x_ref_m: float | None = None,
    workdir: str | Path | None = None,
    seed: int | None = None,
) -> DemandProfile: ...


def fit_inflow(
    scenario: ScenarioConfig | pd.DataFrame,
    counts: pd.DataFrame | DemandProfile,
    simulate_fn: SimulateFn | None = None,
    *,
    created_at: str,
    source: str,
    data_hash: str = "",
    geh_threshold: float = 5.0,
    geh_pass_frac: float = 0.85,
    max_iters: int = 25,
    max_scale_step: float = 2.0,
    x_ref_m: float | None = None,
    workdir: str | Path | None = None,
    seed: int | None = None,
) -> DemandProfile:
    """Fit a corridor inflow profile to observed link counts (CLAUDE.md §6.3).

    Two call forms:

    ``fit_inflow(scenario, counts, ...)``
        ``scenario`` is a :class:`~flowstate_core.config.ScenarioConfig`
        whose network carries an ``inflow`` (corridor or OSM); its inflow is
        the starting profile. ``counts`` is the observed-counts frame
        (columns :data:`COUNT_COLUMNS`, bins in **simulation time**). When
        ``simulate_fn`` is ``None`` the microscopic tier is used:
        ``microsim.demand_adapter.make_simulate_fn`` runs one seeded
        replicate per iteration with the scenario's own ``seed`` (or
        ``seed``) and counts crossings of ``x_ref_m`` on the observed bins.
        ``x_ref_m`` is in trajectory coordinates (docs/CONTRACTS.md §3);
        ``None`` means the upstream end of the corridor proper, i.e. the
        boundary the counts describe. Run artifacts go under ``workdir``
        (a temporary directory, removed afterwards, when ``None``).

    ``fit_inflow(observed_counts, initial_profile, simulate_fn, ...)``
        The engine-agnostic form; see :func:`fit_inflow_profile`.

    Args:
        scenario: Scenario config, or the observed-counts frame (legacy
            form).
        counts: Observed-counts frame, or the initial profile (legacy form).
        simulate_fn: Injected simulator (see :data:`SimulateFn`); required
            in the legacy form, optional in the scenario form.
        created_at: ISO-8601 timestamp for the artifact (caller-supplied).
        source: Human-readable provenance of the observed counts.
        data_hash: Hash of the observed-count data (provenance).
        geh_threshold: Per-bin GEH acceptance threshold.
        geh_pass_frac: Required fraction of bins meeting the threshold.
        max_iters: Iteration cap.
        max_scale_step: Per-iteration scale-factor clip (> 1).
        x_ref_m: Scenario form only — counting cross-section [m].
        workdir: Scenario form only — run-tree root for the adapter's runs.
        seed: Scenario form only — replicate seed (default: scenario seed).

    Returns:
        Fitted ``DemandProfile`` with ``geh_vs_counts`` set.

    Raises:
        TypeError: If the two forms are mixed (e.g. legacy form without
            ``simulate_fn``).
        ValueError: If the scenario network has no inflow to calibrate, or
            on malformed inputs (see :func:`fit_inflow_profile`).
        ImportError: Scenario form without ``simulate_fn`` when the
            ``microsim`` package is not installed.
    """
    kwargs = {
        "created_at": created_at,
        "source": source,
        "data_hash": data_hash,
        "geh_threshold": geh_threshold,
        "geh_pass_frac": geh_pass_frac,
        "max_iters": max_iters,
        "max_scale_step": max_scale_step,
    }
    if isinstance(scenario, ScenarioConfig):
        if not isinstance(counts, pd.DataFrame):
            raise TypeError("fit_inflow(scenario, counts): counts must be a DataFrame")
        return _fit_inflow_scenario(
            scenario,
            counts,
            simulate_fn,
            x_ref_m=x_ref_m,
            workdir=workdir,
            seed=seed,
            **kwargs,
        )
    if not isinstance(scenario, pd.DataFrame) or not isinstance(counts, DemandProfile):
        raise TypeError(
            "fit_inflow takes (ScenarioConfig, counts DataFrame) or "
            "(counts DataFrame, DemandProfile, simulate_fn)"
        )
    if simulate_fn is None:
        raise TypeError("fit_inflow(observed_counts, initial_profile, ...) requires simulate_fn")
    return fit_inflow_profile(scenario, counts, simulate_fn, **kwargs)


def _fit_inflow_scenario(
    cfg: ScenarioConfig,
    counts: pd.DataFrame,
    simulate_fn: SimulateFn | None,
    *,
    x_ref_m: float | None,
    workdir: str | Path | None,
    seed: int | None,
    created_at: str,
    source: str,
    data_hash: str,
    geh_threshold: float,
    geh_pass_frac: float,
    max_iters: int,
    max_scale_step: float,
) -> DemandProfile:
    """Scenario form of :func:`fit_inflow` (see its docstring)."""
    inflow = getattr(cfg.network, "inflow", None)
    if not inflow:
        raise ValueError(
            f"fit_inflow(scenario, counts): network kind {cfg.network.kind!r} of "
            f"scenario {cfg.name!r} carries no inflow profile to calibrate"
        )
    _validate_counts(counts)
    initial = DemandProfile(
        created_at=created_at,
        source=source,
        data_hash=data_hash,
        steps=[(float(t), float(q)) for t, q in inflow],
    )
    kwargs = {
        "created_at": created_at,
        "source": source,
        "data_hash": data_hash,
        "geh_threshold": geh_threshold,
        "geh_pass_frac": geh_pass_frac,
        "max_iters": max_iters,
        "max_scale_step": max_scale_step,
    }
    if simulate_fn is not None:
        return fit_inflow_profile(counts, initial, simulate_fn, **kwargs)

    try:
        from microsim.demand_adapter import make_simulate_fn
    except ImportError as exc:
        raise ImportError(
            "fit_inflow(scenario, counts) without simulate_fn needs the 'microsim' "
            "package (the SUMO microscopic tier) for its default simulator; install "
            "the flowstate workspace with microsim, or pass simulate_fn=..."
        ) from exc
    bins = [
        (float(a), float(b)) for a, b in zip(counts["t_start_s"], counts["t_end_s"], strict=True)
    ]
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="flowstate_demand_fit_")
        workdir = tmp.name
    try:
        adapter = make_simulate_fn(cfg, workdir, bins=bins, x_ref_m=x_ref_m, seed=seed)
        return fit_inflow_profile(counts, initial, adapter, **kwargs)
    finally:
        if tmp is not None:
            tmp.cleanup()


# --- named scalar multipliers: compass search on a shrinking grid ---------------


@dataclass(frozen=True)
class MultiplierSpec:
    """One named scalar multiplier with bounds and a starting value.

    Attributes:
        name: Unique label (the key of the ``values`` dict handed to the
            objective).
        initial: Starting value; ties in the objective are broken toward it.
        lower: Inclusive lower bound.
        upper: Inclusive upper bound.
    """

    name: str
    initial: float = 1.0
    lower: float = 0.5
    upper: float = 1.5

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("MultiplierSpec needs a non-empty name")
        if not self.lower < self.upper:
            raise ValueError(
                f"{self.name}: lower must be < upper, got [{self.lower}, {self.upper}]"
            )
        if not self.lower <= self.initial <= self.upper:
            raise ValueError(
                f"{self.name}: initial {self.initial} outside [{self.lower}, {self.upper}]"
            )


@dataclass(frozen=True)
class EvalResult:
    """What an objective returns for one point.

    Attributes:
        objective: The fitted-window objective to minimise (a non-finite
            value is kept in the log but never selected).
        diagnostics: Anything else worth recording — held-out scores,
            insertion fractions, config hashes. Must be picklable when the
            evaluation runs in a process pool.
    """

    objective: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalRecord:
    """One entry of the evaluation log (a point and what it scored).

    Attributes:
        values: Multiplier values of the point.
        objective: Fitted-window objective.
        diagnostics: Held-out diagnostics returned with it.
        round: Round that requested the evaluation (0 = the initial point;
            prior records keep the round they were made in).
    """

    values: dict[str, float]
    objective: float
    diagnostics: dict[str, Any]
    round: int


@dataclass(frozen=True)
class RoundSummary:
    """What one round of :func:`fit_multipliers` did.

    Attributes:
        index: Round number (1-based).
        step: Grid half-width per multiplier during the round.
        n_fresh: Evaluations performed (cache misses).
        n_cached: Candidate points answered from the cache.
        moved: Whether the incumbent changed.
        best: Incumbent after the round.
        objective: Its objective.
    """

    index: int
    step: dict[str, float]
    n_fresh: int
    n_cached: int
    moved: bool
    best: dict[str, float]
    objective: float


@dataclass
class MultiplierFit:
    """Result of :func:`fit_multipliers`.

    Attributes:
        best: Best point found (the incumbent at the end).
        objective: Its fitted-window objective.
        diagnostics: The held-out diagnostics recorded at the best point.
        log: Every evaluation known — prior records first, then this call's
            in evaluation order.
        rounds: Per-round summaries.
        n_evaluations: Fresh evaluations performed by this call.
        n_cached: Candidate points answered from the cache by this call.
        converged: Whether the step fell below ``tol`` before the round cap.
        step: Final grid half-width per multiplier.
    """

    best: dict[str, float]
    objective: float
    diagnostics: dict[str, Any]
    log: list[EvalRecord]
    rounds: list[RoundSummary]
    n_evaluations: int
    n_cached: int
    converged: bool
    step: dict[str, float]


EvaluateFn = Callable[[dict[str, float]], EvalResult]
"""Objective: multiplier values → :class:`EvalResult`. Pure and picklable
when used with a process-pool ``map_fn``."""

MapFn = Callable[[EvaluateFn, list[dict[str, float]]], Iterable[EvalResult]]
"""``map``-shaped evaluator of a batch of candidate points (order-preserving);
the default is the builtin ``map``, a driver passes ``Pool.map``."""

_KEY_DECIMALS = 9


def _point_key(values: Mapping[str, float], names: Sequence[str]) -> tuple[float, ...]:
    return tuple(round(float(values[n]), _KEY_DECIMALS) for n in names)


def _objective_key(objective: float) -> float:
    return float(objective) if math.isfinite(objective) else math.inf


def fit_multipliers(
    params: Sequence[MultiplierSpec],
    evaluate: EvaluateFn,
    *,
    grid: int = 3,
    rounds: int = 3,
    step: float | Mapping[str, float] | None = None,
    shrink: float = 0.5,
    tol: float = 1e-3,
    combine: bool = True,
    map_fn: MapFn = map,
    prior: Iterable[EvalRecord] | None = None,
    on_round: Callable[[MultiplierFit], None] | None = None,
) -> MultiplierFit:
    """Minimise an objective over a few named scalar multipliers.

    Deterministic compass (pattern) search on a shrinking coordinate grid.
    Round 0 evaluates the initial point. Each later round places, for every
    multiplier, ``grid`` points centred on the incumbent and spanning
    ``±step`` along that coordinate alone (``grid=3`` is ``{x−h, x, x+h}``),
    clipped to the bounds; every point not already in the cache is evaluated
    in ONE ``map_fn`` batch, so a process pool runs a round's candidates in
    parallel. With ``combine`` the per-coordinate winners are then joined
    into one extra point (evaluated when at least two coordinates moved),
    which lets a separable objective converge in a round instead of one
    coordinate per round. The incumbent moves to the best point seen; when
    nothing beats it the step is multiplied by ``shrink``, and the search
    stops once every step is below ``tol`` or ``rounds`` is exhausted.

    Ties are broken toward the initial values (smallest maximum distance
    from ``initial``), then by the values themselves, so the result never
    depends on evaluation order. Every evaluated point is memoized by its
    rounded values; ``prior`` records (e.g. the log of an interrupted run)
    seed the cache, which makes the fit resumable, and ``on_round`` receives
    the partial result after every round for checkpointing.

    Args:
        params: The multipliers (unique names, bounds, initial values).
        evaluate: Objective, ``values → EvalResult``. A non-finite objective
            is logged and treated as worst.
        grid: Odd number of points per coordinate per round (≥ 3).
        rounds: Maximum number of rounds after the initial point (≥ 0).
        step: Initial grid half-width — one value for all, a per-name
            mapping, or ``None`` for a quarter of each range.
        shrink: Step multiplier applied after a round without improvement
            (in ``(0, 1)``).
        tol: Stop when every step is below this.
        combine: Also try the point joining the per-coordinate winners.
        map_fn: Batch evaluator (see :data:`MapFn`).
        prior: Previously evaluated points to seed the cache and log.
        on_round: Callback with the partial result after each round.

    Returns:
        :class:`MultiplierFit` with the best point, its diagnostics, the
        full log and the per-round summaries.

    Raises:
        ValueError: On duplicate names, an even or too-small grid, a
            non-positive step, a shrink outside (0, 1) or negative rounds.
    """
    names = [p.name for p in params]
    if not names:
        raise ValueError("fit_multipliers needs at least one MultiplierSpec")
    if len(set(names)) != len(names):
        raise ValueError(f"multiplier names must be unique, got {names}")
    if grid < 3 or grid % 2 == 0:
        raise ValueError(f"grid must be an odd integer >= 3, got {grid}")
    if rounds < 0:
        raise ValueError(f"rounds must be >= 0, got {rounds}")
    if not 0.0 < shrink < 1.0:
        raise ValueError(f"shrink must be in (0, 1), got {shrink}")
    spec = {p.name: p for p in params}
    if step is None:
        steps = {n: (spec[n].upper - spec[n].lower) / 4.0 for n in names}
    elif isinstance(step, Mapping):
        missing = [n for n in names if n not in step]
        if missing:
            raise ValueError(f"step mapping lacks {missing}")
        steps = {n: float(step[n]) for n in names}
    else:
        steps = {n: float(step) for n in names}
    if any(h <= 0.0 for h in steps.values()):
        raise ValueError(f"steps must be > 0, got {steps}")
    initial = {n: float(spec[n].initial) for n in names}

    cache: dict[tuple[float, ...], EvalRecord] = {}
    log: list[EvalRecord] = []
    for rec in prior or ():
        if set(rec.values) != set(names):
            raise ValueError(f"prior record names {sorted(rec.values)} != {sorted(names)}")
        clean = EvalRecord(
            values={n: float(rec.values[n]) for n in names},
            objective=float(rec.objective),
            diagnostics=dict(rec.diagnostics),
            round=int(rec.round),
        )
        cache[_point_key(clean.values, names)] = clean
        log.append(clean)

    n_fresh_total = 0
    n_cached_total = 0

    def evaluate_batch(points: list[dict[str, float]], round_index: int) -> list[EvalRecord]:
        """Answer ``points`` from the cache, evaluating the misses in one batch."""
        nonlocal n_fresh_total, n_cached_total
        fresh: list[dict[str, float]] = []
        seen: set[tuple[float, ...]] = set()
        for pt in points:
            key = _point_key(pt, names)
            if key in cache:
                n_cached_total += 1
            elif key not in seen:
                seen.add(key)
                fresh.append({n: float(pt[n]) for n in names})
        if fresh:
            results = list(map_fn(evaluate, fresh))
            if len(results) != len(fresh):
                raise ValueError(
                    f"map_fn returned {len(results)} results for {len(fresh)} candidates"
                )
            for pt, res in zip(fresh, results, strict=True):
                rec = EvalRecord(
                    values=dict(pt),
                    objective=float(res.objective),
                    diagnostics=dict(res.diagnostics),
                    round=round_index,
                )
                cache[_point_key(pt, names)] = rec
                log.append(rec)
                n_fresh_total += 1
        return [cache[_point_key(pt, names)] for pt in points]

    def rank_key(rec: EvalRecord) -> tuple[float, float, tuple[float, ...]]:
        dist = max(abs(rec.values[n] - initial[n]) for n in names)
        return (_objective_key(rec.objective), dist, _point_key(rec.values, names))

    def clip(name: str, value: float) -> float:
        return min(max(value, spec[name].lower), spec[name].upper)

    incumbent = evaluate_batch([dict(initial)], 0)[0]
    summaries: list[RoundSummary] = []
    converged = all(h < tol for h in steps.values())

    def snapshot() -> MultiplierFit:
        return MultiplierFit(
            best=dict(incumbent.values),
            objective=incumbent.objective,
            diagnostics=dict(incumbent.diagnostics),
            log=list(log),
            rounds=list(summaries),
            n_evaluations=n_fresh_total,
            n_cached=n_cached_total,
            converged=converged,
            step=dict(steps),
        )

    half = (grid - 1) // 2
    for r in range(1, rounds + 1):
        if converged:
            break
        fresh_before, cached_before = n_fresh_total, n_cached_total
        candidates: list[dict[str, float]] = []
        along: dict[str, list[dict[str, float]]] = {n: [] for n in names}
        for n in names:
            seen_vals = {round(incumbent.values[n], _KEY_DECIMALS)}
            for k in range(-half, half + 1):
                if k == 0:
                    continue
                v = clip(n, incumbent.values[n] + steps[n] * k / half)
                if round(v, _KEY_DECIMALS) in seen_vals:
                    continue
                seen_vals.add(round(v, _KEY_DECIMALS))
                pt = dict(incumbent.values)
                pt[n] = v
                candidates.append(pt)
                along[n].append(pt)
        records = evaluate_batch(candidates, r)
        by_key = {_point_key(rec.values, names): rec for rec in records}
        pool = [incumbent, *records]
        if combine:
            winners: dict[str, float] = {}
            for n in names:
                axis = [incumbent, *(by_key[_point_key(pt, names)] for pt in along[n])]
                winners[n] = min(axis, key=rank_key).values[n]
            moved = [n for n in names if winners[n] != incumbent.values[n]]
            if len(moved) >= 2:
                pool.extend(evaluate_batch([winners], r))
        best = min(pool, key=rank_key)
        improved = rank_key(best) < rank_key(incumbent)
        if improved:
            incumbent = best
        else:
            steps = {n: h * shrink for n, h in steps.items()}
            converged = all(h < tol for h in steps.values())
        summaries.append(
            RoundSummary(
                index=r,
                step=dict(steps),
                n_fresh=n_fresh_total - fresh_before,
                n_cached=n_cached_total - cached_before,
                moved=improved,
                best=dict(incumbent.values),
                objective=incumbent.objective,
            )
        )
        if on_round is not None:
            on_round(snapshot())
    return snapshot()


# --- observations → scenario boundary conditions --------------------------------

DEMAND_SCHEMA = "flowstate.demand/1"
"""Schema tag of the demand artifact (docs/CONTRACTS.md, "Detector observations")."""

CONSERVATION_METHOD = (
    "ramp detectors where present, else conservation between consecutive mainline "
    "stations (q_on = max(0, q_down - q_up); f_off = max(0, (q_up - q_down)/q_up))"
)
"""The artifact's ``method`` string — what the numbers below actually are."""

RampKind = Literal["on", "off"]


def _step_grid(obs: Observations, step_s: float) -> tuple[int, int]:
    """``(windows_per_step, n_steps)`` for a step length on the window grid.

    Raises:
        ValueError: ``step_s`` is not a whole multiple of the observations'
            window, or does not divide the analysed span.
    """
    ratio = step_s / obs.window_s
    if step_s <= 0 or abs(ratio - round(ratio)) > 1e-9:
        raise ValueError(
            f"step_s ({step_s}) must be a positive whole multiple of the observations' "
            f"window_s ({obs.window_s})"
        )
    per_step = round(ratio)
    if obs.n_windows % per_step:
        raise ValueError(
            f"step_s ({step_s}) does not divide the analysed span: {obs.n_windows} windows "
            f"of {obs.window_s} s leave a partial step"
        )
    return per_step, obs.n_windows // per_step


def _step_means(values: Sequence[float], per_step: int, n_steps: int) -> list[float]:
    """Per-step mean of the *observed* windows (NaN when a step observed none)."""
    out: list[float] = []
    for k in range(n_steps):
        chunk = [v for v in values[k * per_step : (k + 1) * per_step] if not math.isnan(v)]
        out.append(sum(chunk) / len(chunk) if chunk else float("nan"))
    return out


def _steps_from_means(
    means: Sequence[float], step_s: float, convert: Callable[[float], float], label: str
) -> tuple[list[tuple[float, float]], int]:
    """Per-step means → profile steps, dropping the steps nothing was observed in.

    A piecewise-constant profile holds its last value until the next step, so
    *omitting* an unobserved step carries the previous one across it — the one
    assumption this path makes, and the reason the number of carried steps is
    returned and recorded on the artifact instead of being swallowed. The
    profile always starts at t=0: when the first steps are unobserved the
    first observed value is used, rather than inventing a level for them.

    Args:
        means: Per-step observed means (NaN = nothing observed).
        step_s: Step length [s].
        convert: Unit conversion applied to each value.
        label: Name used in the error message.

    Returns:
        ``(steps, n_carried)`` — the ``(t_start_s, value)`` steps and the
        number of steps that were not observed.

    Raises:
        ValueError: No step was observed at all.
    """
    observed = [(k, v) for k, v in enumerate(means) if not math.isnan(v)]
    if not observed:
        raise ValueError(
            f"{label}: no window of the analysed span was observed — there is nothing to "
            f"build a demand profile from (check the station id, dates and coverage)"
        )
    steps: list[tuple[float, float]] = []
    for k, value in observed:
        t_start = 0.0 if not steps and k > 0 else float(k * step_s)
        steps.append((t_start, float(convert(value))))
    return steps, len(means) - len(observed)


def demand_from_observations(
    obs: Observations, upstream_station: str, *, step_s: float = 300.0
) -> list[tuple[float, float]]:
    """Corridor inflow steps [veh/s] from the upstream station's observed flow.

    The boundary of a corridor scenario is the flow its most upstream mainline
    detector measured, on the simulation clock: step ``k`` starts at
    ``k · step_s`` (t=0 is the artifact's ``t0_local``) and carries that
    period's mean observed flow. Nothing is fitted and nothing is smoothed.

    Args:
        obs: Observations artifact.
        upstream_station: Station id at the corridor's upstream boundary.
        step_s: Step length [s]; a whole multiple of the artifact's window
            that divides the analysed span.

    Returns:
        ``[(t_start_s, inflow_veh_s), ...]`` — the scenario's
        ``network.inflow``, ordered and starting at t=0.

    Raises:
        KeyError: The artifact holds no such station.
        ValueError: A malformed ``step_s``, or a station with no observed
            window (see :func:`_steps_from_means`).
    """
    if upstream_station not in obs.flows_veh_h:
        raise KeyError(
            f"observations for {obs.corridor!r} hold no station {upstream_station!r}; "
            f"stations: {sorted(obs.flows_veh_h)}"
        )
    per_step, n_steps = _step_grid(obs, step_s)
    means = _step_means(obs.flows_veh_h[upstream_station], per_step, n_steps)
    steps, _ = _steps_from_means(
        means, step_s, veh_h_to_veh_s, f"inflow from station {upstream_station!r}"
    )
    return steps


def _bracketing_stations(obs: Observations, x_m: float) -> tuple[str | None, str | None]:
    """Ids of the mainline stations immediately up- and downstream of ``x_m``."""
    upstream: str | None = None
    downstream: str | None = None
    for station in obs.mainline_stations():
        position = float(station.x_m or 0.0)
        if position <= x_m:
            upstream = station.id
        elif downstream is None:
            downstream = station.id
    return upstream, downstream


def _conservation_means(
    obs: Observations, kind: RampKind, x_m: float, per_step: int, n_steps: int
) -> tuple[list[float], str, str]:
    """Per-step ramp means from the flow difference across the ramp.

    ``q_on = max(0, q_down − q_up)`` [veh/h] and
    ``f_off = max(0, (q_up − q_down) / q_up)``: the standard closure when a
    ramp carries no detector. It attributes *all* of the flow change between
    two stations to this ramp, so it is only as good as the assumption that
    nothing else happens in between — which is why a ramp detector is
    preferred whenever one exists.

    Returns:
        ``(means, upstream_id, downstream_id)``; the means are NaN in any
        step where either station was unobserved.

    Raises:
        ValueError: The ramp is not bracketed by two mainline stations.
    """
    upstream, downstream = _bracketing_stations(obs, x_m)
    if upstream is None or downstream is None:
        raise ValueError(
            f"ramp at x_m={x_m:g} is not bracketed by two mainline stations "
            f"(upstream={upstream!r}, downstream={downstream!r}); give it a detector "
            f"station or extend the observed span"
        )
    up = _step_means(obs.flows_veh_h[upstream], per_step, n_steps)
    down = _step_means(obs.flows_veh_h[downstream], per_step, n_steps)
    means: list[float] = []
    for q_up, q_down in zip(up, down, strict=True):
        if math.isnan(q_up) or math.isnan(q_down):
            means.append(float("nan"))
        elif kind == "on":
            means.append(max(0.0, q_down - q_up))
        else:
            means.append(max(0.0, (q_up - q_down) / q_up) if q_up > 0.0 else 0.0)
    return means, upstream, downstream


def _exit_fraction_means(
    obs: Observations, station_id: str, x_m: float, per_step: int, n_steps: int
) -> tuple[list[float], str]:
    """Exit fractions from a ramp detector: ramp flow over upstream mainline flow.

    Raises:
        ValueError: No mainline station upstream of the ramp.
    """
    upstream, _ = _bracketing_stations(obs, x_m)
    if upstream is None:
        raise ValueError(
            f"off-ramp at x_m={x_m:g} has no mainline station upstream of it, so its "
            f"measured flow cannot be turned into an exit fraction"
        )
    ramp = _step_means(obs.flows_veh_h[station_id], per_step, n_steps)
    main = _step_means(obs.flows_veh_h[upstream], per_step, n_steps)
    means = [
        float("nan")
        if math.isnan(q_ramp) or math.isnan(q_main) or q_main <= 0.0
        else min(1.0, max(0.0, q_ramp / q_main))
        for q_ramp, q_main in zip(ramp, main, strict=True)
    ]
    return means, upstream


def _observed(obs: Observations, station_id: str | None) -> bool:
    """True when the artifact holds at least one observed window for a station."""
    if not station_id or station_id not in obs.flows_veh_h:
        return False
    return any(not math.isnan(v) for v in obs.flows_veh_h[station_id])


def ramp_flows_from_observations(
    obs: Observations,
    ramps: Sequence[Mapping[str, Any]],
    *,
    step_s: float = 300.0,
) -> list[dict[str, Any]]:
    """Ramp inflow / exit-fraction steps from the observations artifact.

    A ramp with its own detector station uses it (``method: "detector"``);
    otherwise the flow difference between the mainline stations bracketing the
    ramp supplies it (``method: "conservation"``, :data:`CONSERVATION_METHOD`).

    Args:
        obs: Observations artifact.
        ramps: Ramp descriptors — ``name``, ``kind`` (``on``/``on_ramp`` or
            ``off``/``off_ramp``), ``x_m``, and optionally ``station`` (the
            observations key of its detector row).
        step_s: Step length [s] (see :func:`demand_from_observations`).

    Returns:
        One dict per ramp, in input order: ``name``, ``kind`` (``on``/``off``),
        ``x_m``, ``method``, ``n_steps``, ``n_steps_carried`` and either
        ``inflow_steps`` ``[[t_start_s, veh_s], ...]`` (on-ramps) or
        ``exit_fraction_steps`` ``[[t_start_s, fraction], ...]`` (off-ramps).

    Raises:
        ValueError: A malformed descriptor or ``step_s``, a ramp that is
            neither measured nor bracketed by two mainline stations, or a
            ramp with no observed step at all.
    """
    per_step, n_steps = _step_grid(obs, step_s)
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(ramps):
        name = str(raw.get("name") or f"ramp_{index}")
        kind_raw = str(raw.get("kind") or "").strip().lower()
        kind: RampKind
        if kind_raw in ("on", "on_ramp"):
            kind = "on"
        elif kind_raw in ("off", "off_ramp"):
            kind = "off"
        else:
            raise ValueError(
                f"ramp {name!r}: kind must be 'on'/'on_ramp' or 'off'/'off_ramp', got {kind_raw!r}"
            )
        if raw.get("x_m") is None:
            raise ValueError(f"ramp {name!r}: needs an 'x_m' position on the corridor")
        x_m = float(raw["x_m"])
        station_id = raw.get("station")
        station_id = str(station_id) if station_id else None

        record: dict[str, Any] = {"name": name, "kind": kind, "x_m": x_m}
        if _observed(obs, station_id):
            assert station_id is not None
            record["station"] = station_id
            record["method"] = "detector"
            if kind == "on":
                means = _step_means(obs.flows_veh_h[station_id], per_step, n_steps)
            else:
                means, upstream = _exit_fraction_means(obs, station_id, x_m, per_step, n_steps)
                record["reference_station"] = upstream
        else:
            means, upstream, downstream = _conservation_means(obs, kind, x_m, per_step, n_steps)
            record["method"] = "conservation"
            record["reference_station"] = upstream
            record["downstream_station"] = downstream
        convert: Callable[[float], float] = veh_h_to_veh_s if kind == "on" else (lambda v: v)
        steps, carried = _steps_from_means(means, step_s, convert, f"ramp {name!r}")
        key = "inflow_steps" if kind == "on" else "exit_fraction_steps"
        record[key] = [[t, v] for t, v in steps]
        record["n_steps"] = n_steps
        record["n_steps_carried"] = carried
        out.append(record)
    return out


@dataclass(frozen=True)
class DemandArtifact:
    """A corridor's boundary conditions as measured (``flowstate.demand/1``).

    The scenario YAML carries these numbers literally (``network.inflow``,
    ``network.ramps[*].inflow`` / ``exit_fraction``) so it stays
    self-contained; this artifact is the provenance record that says where
    they came from and how they were derived.

    Attributes:
        corridor: Corridor name.
        observations: Path of the observations artifact they derive from.
        upstream_station: Station id supplying the mainline inflow.
        inflow_steps: ``[(t_start_s, veh_s), ...]``.
        ramps: Ramp records (:func:`ramp_flows_from_observations`).
        method: How the numbers were derived (:data:`CONSERVATION_METHOD`).
        step_s: Step length [s] of every profile.
        coverage: Honest counts — ``n_steps`` and ``n_steps_carried`` of the
            mainline inflow.
        schema: :data:`DEMAND_SCHEMA`.
    """

    corridor: str
    observations: str
    upstream_station: str
    inflow_steps: list[tuple[float, float]]
    ramps: list[dict[str, Any]] = field(default_factory=list)
    method: str = CONSERVATION_METHOD
    step_s: float = 300.0
    coverage: dict[str, float] = field(default_factory=dict)
    schema: str = DEMAND_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        """The artifact's JSON form."""
        return {
            "schema": self.schema,
            "corridor": self.corridor,
            "observations": self.observations,
            "upstream_station": self.upstream_station,
            "step_s": self.step_s,
            "inflow_steps": [[float(t), float(q)] for t, q in self.inflow_steps],
            "ramps": [dict(r) for r in self.ramps],
            "method": self.method,
            "coverage": dict(self.coverage),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DemandArtifact:
        """Rebuild from the JSON form.

        Raises:
            ValueError: The payload carries a different ``schema``.
        """
        schema = str(raw.get("schema", DEMAND_SCHEMA))
        if schema != DEMAND_SCHEMA:
            raise ValueError(f"expected schema {DEMAND_SCHEMA!r}, got {schema!r}")
        return cls(
            corridor=str(raw.get("corridor", "")),
            observations=str(raw.get("observations", "")),
            upstream_station=str(raw.get("upstream_station", "")),
            inflow_steps=[(float(t), float(q)) for t, q in raw.get("inflow_steps", ())],
            ramps=[dict(r) for r in raw.get("ramps", ())],
            method=str(raw.get("method", CONSERVATION_METHOD)),
            step_s=float(raw.get("step_s", 300.0)),
            coverage={k: float(v) for k, v in (raw.get("coverage") or {}).items()},
            schema=schema,
        )

    def to_json(self, path: str | Path) -> Path:
        """Write the artifact as JSON (parents created)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False))
        return target

    @classmethod
    def from_json(cls, path: str | Path) -> DemandArtifact:
        """Read an artifact written by :meth:`to_json`."""
        return cls.from_dict(json.loads(Path(path).read_text()))


def demand_from_observations_artifact(
    obs: Observations,
    upstream_station: str,
    *,
    ramps: Sequence[Mapping[str, Any]] = (),
    observations_path: str = "",
    step_s: float = 300.0,
) -> DemandArtifact:
    """Build the whole :class:`DemandArtifact` from an observations artifact.

    Args:
        obs: Observations artifact.
        upstream_station: Station id at the upstream boundary.
        ramps: Ramp descriptors (:func:`ramp_flows_from_observations`).
        observations_path: Path recorded as the artifact's provenance.
        step_s: Step length [s].

    Returns:
        The demand artifact, including the mainline inflow's coverage counts.

    Raises:
        KeyError: Unknown ``upstream_station``.
        ValueError: See :func:`demand_from_observations` and
            :func:`ramp_flows_from_observations`.
    """
    per_step, n_steps = _step_grid(obs, step_s)
    if upstream_station not in obs.flows_veh_h:
        raise KeyError(
            f"observations for {obs.corridor!r} hold no station {upstream_station!r}; "
            f"stations: {sorted(obs.flows_veh_h)}"
        )
    means = _step_means(obs.flows_veh_h[upstream_station], per_step, n_steps)
    inflow, carried = _steps_from_means(
        means, step_s, veh_h_to_veh_s, f"inflow from station {upstream_station!r}"
    )
    return DemandArtifact(
        corridor=obs.corridor,
        observations=observations_path,
        upstream_station=upstream_station,
        inflow_steps=inflow,
        ramps=ramp_flows_from_observations(obs, ramps, step_s=step_s),
        step_s=float(step_s),
        coverage={"n_steps": float(n_steps), "n_steps_carried": float(carried)},
    )
