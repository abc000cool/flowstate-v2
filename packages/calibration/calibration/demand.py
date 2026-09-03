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
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from math import sqrt
from pathlib import Path
from typing import overload

import numpy as np
import pandas as pd

from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import ScenarioConfig
from flowstate_core.units import veh_s_to_veh_h

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
