"""Tune every strategy on tuning seeds, then evaluate each one's best on evaluation seeds (WP-105).

Implements docs/FRISCO_PROTOCOL.md §8.4 ("every strategy gets the same tuning
budget, tuned on tuning seeds and evaluated on separate evaluation seeds never
used in tuning; the objective is total delay including waiting time") with the
measures of §8.2–8.3. Every run goes through ``scripts/corridor_sweep.py``'s
own worker (``_worker``: ``microsim.runner.run_micro``, then
``validation.metrics.compute_metrics`` and ``compute_waiting_metrics``) into a
``<cell>/<config hash>/<seed>/`` tree, resumable, analysed by the sweep's own
``analyze``; nothing here steps a simulation or computes a metric itself.

**Phase 1, tuning** (``<out>/tuning/``): the baseline (no strategy) and, for
each strategy, the SAME number ``--budget`` of candidate settings
(:data:`SPACES`: candidate 0 is the textbook setting — exactly the config
``corridor_sweep.py`` runs for that strategy — and candidates 1 … N−1 are the
first points of the unscrambled Halton sequence after its origin, mapped onto
the strategy's declared parameter box), each on every tuning seed.

**Selection** (rules fixed before tuning, protocol §8.4): the objective is the
mean over tuning seeds of ``total_delay_incl_waiting_veh_h``, lower is better.
A candidate is not selectable when it is *incomplete* (a tuning run failed),
*disqualified* (any SUMO collision in any tuning run), has *collisions not
recorded*, has an *undefined objective* (a run without the measure), or is
*below the baseline's throughput* (``throughput_veh_h`` paired difference
against the baseline over the tuning seeds has a 95 % interval entirely below
zero — reported, not selectable; with one tuning seed the interval is undefined
and the rule cannot fire, which the summary flags). The best is the selectable
candidate with the lowest objective, ties to the lower candidate index. A
strategy with no selectable candidate is not evaluated and the summary says
why.

**Phase 2, evaluation** (``<out>/evaluation/``): the baseline and each
strategy's best on ``--eval-seeds`` evaluation seeds (at least 20 for a
headline result; fewer is labelled underpowered), then the comparison table of
``validation.strategy_compare`` (the fixed measure set, paired by seed against
the baseline; it refuses when a measure is missing).

**Seed streams, disjoint by construction.** Evaluation seeds are the sweep's
own, ``flowstate_core.rng.spawn_seeds(scenario.seed, n)`` — the children
``(i,)`` of ``SeedSequence(scenario.seed)`` — so an evaluation run is the very
run ``corridor_sweep.py`` makes for the same config and seed. Tuning seeds come
from a separate namespace: the children ``(TUNING_SPAWN_KEY, i)`` of the same
``SeedSequence`` (:func:`tuning_seeds`), reduced to integers exactly as
``spawn_seeds`` reduces its own. Distinct spawn keys give distinct seed
sequences; the derived integers are checked to be disjoint every time
(:func:`seed_streams` raises otherwise).

Outputs: ``<out>/tuning/MANIFEST.json`` and ``<out>/evaluation/MANIFEST.json``
(the sweep's manifest layout plus a ``tuning`` / ``selection`` block),
``--summary`` (default ``<out>/strategy_tune.json``, schema
``flowstate.strategy_tune/1``, with the commit and ``code_dirty`` over
:data:`CODE_PATHS`) and a Markdown summary beside it.

``--plan-only`` prints the candidates and the run counts and writes nothing;
``--analyze-only`` re-analyses an existing tree (and refuses when the
evaluation tree was run for a different selection than the tuning tree now
gives).

Run (VM)::

    uv run --no-sync python scripts/strategy_tune.py --scenario scenarios/X.yaml \\
        --strategies alinea vsl --rho-target-veh-km 19.9 --x-ref 11027 --span 1110 11027 \\
        --budget 6 --tuning-seeds 3 --eval-seeds 20 --procs 30 --out runs/X_tune
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corridor_sweep import _done, _worker, analyze, cell_config, run_records, write_comparison

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from flowstate_core.strategies import on_ramps
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make ``code_dirty`` true: this script,
#: the sweep worker it runs and the packages they import (the convention of
#: scripts/data_quality_report.py and tests/test_scripts/test_code_dirty.py).
CODE_PATHS = (
    "scripts/strategy_tune.py",
    "scripts/corridor_sweep.py",
    "packages/validation",
    "packages/microsim",
    "packages/controllers",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

SCHEMA = "flowstate.strategy_tune/1"
BASELINE = "baseline"
TUNING_DIR = "tuning"
EVALUATION_DIR = "evaluation"

#: The tuning objective (protocol §8.4), lower is better.
OBJECTIVE = "total_delay_incl_waiting_veh_h"
#: The measure a selectable setting may not lose against the baseline.
THROUGHPUT = "throughput_veh_h"

#: Spawn-key namespace of the tuning seeds (ASCII "tune"), far above any
#: replicate index the evaluation stream's children ``(i,)`` use.
TUNING_SPAWN_KEY = 0x74756E65

DEFAULT_BUDGET = 6
DEFAULT_TUNING_SEEDS = 3
DEFAULT_EVAL_SEEDS = 20
#: Candidate parameter values are rounded to this many decimals (readable
#: configs; the rounding is part of the declared candidate).
DECIMALS = 4

#: Selection statuses (module docstring).
SELECTABLE = "selectable"
INCOMPLETE = "incomplete"
DISQUALIFIED_COLLISION = "disqualified_collision"
COLLISIONS_NOT_RECORDED = "collisions_not_recorded"
OBJECTIVE_UNDEFINED = "objective_undefined"
THROUGHPUT_BELOW_BASELINE = "throughput_below_baseline"


@dataclass(frozen=True)
class Dimension:
    """One tuned parameter: its box, its textbook value and where both come from."""

    name: str
    lo: float
    hi: float
    default: float
    unit: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "lo": self.lo,
            "hi": self.hi,
            "default": self.default,
            "unit": self.unit,
            "source": self.source,
        }


@dataclass(frozen=True)
class Space:
    """A strategy's declared search space.

    Attributes:
        strategy: Strategy name (``--strategies``).
        description: What a candidate changes.
        dims: The tuned parameters.
        vehicle_controller: The AV controller the strategy deploys (None for
            an infrastructure strategy).
        infra: The ``flowstate_core.strategies`` strategy it patches in.
    """

    strategy: str
    description: str
    dims: tuple[Dimension, ...]
    vehicle_controller: str | None
    infra: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "description": self.description,
            "vehicle_controller": self.vehicle_controller,
            "infra": self.infra,
            "dims": [d.to_dict() for d in self.dims],
        }


_ALINEA_DIMS = (
    Dimension(
        "rho_target_factor",
        0.8,
        1.1,
        1.0,
        "× rho_c",
        "ALINEA's target density as a multiple of the corridor's critical density per lane "
        "(--rho-target-veh-km, the FD artifact's rho_c); ALINEA practice sets the target at or "
        "slightly below critical (Papageorgiou, Hadj-Salem & Blosseville 1991)",
    ),
    Dimension(
        "k_r_veh_h_per_veh_km",
        25.0,
        100.0,
        50.0,
        "veh/h per veh/km",
        "ALINEA gain, half to double controllers.ramp_meter.ALINEA_DEFAULTS (50, the density "
        "form of the classic K_R ≈ 70 veh/h per % occupancy)",
    ),
)
_VSL_DIMS = (
    Dimension(
        "v_on_kmh",
        50.0,
        80.0,
        60.0,
        "km/h",
        "speed below which a gantry escalates (controllers.vsl.VSL_THRESHOLD_DEFAULTS v_on, a "
        "Phase-1 placeholder; v_off kept 10 km/h above, the defaults' hysteresis), across the "
        "ladder's 50-80 km/h rungs",
    ),
    Dimension(
        "rho_on_veh_km",
        30.0,
        90.0,
        40.0,
        "veh/km (segment, all lanes)",
        "segment density above which a gantry escalates (VSL_THRESHOLD_DEFAULTS rho_on, a "
        "Phase-1 placeholder; rho_off kept 10 veh/km below); the segment density counts every "
        "lane, so the box spans about 10-30 veh/km per lane on a three-lane road",
    ),
)
_FS_DIMS = (
    Dimension(
        "dx0_scale",
        0.75,
        1.5,
        1.0,
        "× Stern et al. dx0_k",
        "scales the three region intercepts dx0_1..3 (4.5, 5.25, 6.0 m; Stern et al. 2018 §3.1)",
    ),
    Dimension(
        "d_scale",
        0.5,
        2.0,
        1.0,
        "× Stern et al. d_k",
        "scales the three region curvatures d_1..3 (1.5, 1.0, 0.5 m/s²; Stern et al. 2018 §3.1)",
    ),
)
_FS_CAP_DIMS = (
    Dimension(
        "h_max_s",
        1.3,
        2.0,
        2.0,
        "s",
        "largest time headway the capacity-aware FollowerStopper holds on purpose "
        "(FOLLOWER_STOPPER_CAPACITY_DEFAULTS 2.0 s; the 1.3-2.0 s range named for its sweep, "
        "docs/I24_SWEEP.md)",
    ),
    Dimension(
        "g0_m",
        2.0,
        6.0,
        4.0,
        "m",
        "standstill part of the headway cap (FOLLOWER_STOPPER_CAPACITY_DEFAULTS 4.0 m), ±2 m",
    ),
)

#: The declared search spaces, by strategy (module docstring).
SPACES: dict[str, Space] = {
    "alinea": Space("alinea", "every on-ramp metered by ALINEA", _ALINEA_DIMS, None, "alinea"),
    "vsl": Space("vsl", "threshold VSL on every gantry segment", _VSL_DIMS, None, "vsl"),
    "vsl+alinea": Space(
        "vsl+alinea", "both, tuned jointly", _VSL_DIMS + _ALINEA_DIMS, None, "vsl+alinea"
    ),
    "follower_stopper": Space(
        "follower_stopper",
        "FollowerStopper AVs at --av-penetration / --av-compliance",
        _FS_DIMS,
        "follower_stopper",
        "none",
    ),
    "follower_stopper_capacity": Space(
        "follower_stopper_capacity",
        "capacity-aware FollowerStopper AVs at --av-penetration / --av-compliance",
        _FS_CAP_DIMS,
        "follower_stopper_capacity",
        "none",
    ),
}

_FS_DX0 = {"dx0_1": 4.5, "dx0_2": 5.25, "dx0_3": 6.0}
_FS_D = {"d_1": 1.5, "d_2": 1.0, "d_3": 0.5}


# --------------------------------------------------------------------------- seeds


def _seed_ints(children: Sequence[np.random.SeedSequence]) -> list[int]:
    """Integers from seed sequences, reduced exactly as ``spawn_seeds`` reduces them."""
    return [int(c.generate_state(1, dtype=np.uint64)[0] % (2**63)) for c in children]


def tuning_seeds(master_seed: int, n: int) -> list[int]:
    """The tuning stream: children ``(TUNING_SPAWN_KEY, i)`` of ``SeedSequence(master_seed)``.

    Deterministic, and the first k seeds are the same for any n >= k.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    root = np.random.SeedSequence(master_seed, spawn_key=(TUNING_SPAWN_KEY,))
    return _seed_ints(root.spawn(n))


def seed_streams(master_seed: int, n_tune: int, n_eval: int) -> tuple[list[int], list[int]]:
    """``(tuning seeds, evaluation seeds)``, checked disjoint.

    Raises:
        ValueError: The two lists share a seed (never observed; the check is
            what makes "never used in tuning" a fact rather than a hope).
    """
    tune = tuning_seeds(master_seed, n_tune)
    evaluation = spawn_seeds(master_seed, n_eval)
    shared = sorted(set(tune) & set(evaluation))
    if shared or len(set(tune)) != len(tune):
        raise ValueError(f"tuning and evaluation seeds are not disjoint: {shared}")
    return tune, evaluation


# --------------------------------------------------------------------------- candidates


def candidate_values(space: Space, budget: int) -> list[dict[str, float]]:
    """The ``budget`` candidates of one space (module docstring).

    Candidate 0 is the textbook setting (every dimension at its default);
    candidates 1 … budget−1 are Halton points 1 … budget−1 (unscrambled,
    ``scipy.stats.qmc.Halton``; point 0 is the box's corner and is skipped),
    mapped linearly onto the box and rounded to :data:`DECIMALS`.

    Raises:
        ValueError: ``budget < 1``.
    """
    from scipy.stats import qmc

    if budget < 1:
        raise ValueError(f"budget must be >= 1, got {budget}")
    out = [{d.name: d.default for d in space.dims}]
    if budget > 1:
        points = qmc.Halton(d=len(space.dims), scramble=False).random(budget)[1:]
        for row in points:
            out.append(
                {
                    d.name: round(d.lo + float(u) * (d.hi - d.lo), DECIMALS)
                    for d, u in zip(space.dims, row, strict=True)
                }
            )
    return out


def _vsl_params(v: Mapping[str, float]) -> dict[str, float]:
    v_on = float(v["v_on_kmh"])
    rho_on = float(v["rho_on_veh_km"])
    return {
        "v_on": kmh_to_ms(v_on),
        "v_off": kmh_to_ms(v_on + 10.0),
        "rho_on": veh_km_to_veh_m(rho_on),
        "rho_off": veh_km_to_veh_m(rho_on - 10.0),
    }


def candidate_config(
    base: dict[str, Any],
    space: Space,
    values: Mapping[str, float],
    *,
    textbook: bool,
    rho_c_veh_km: float | None,
    penetration: float,
    compliance: float,
) -> dict[str, Any]:
    """The scenario config of one candidate (a serialized ``ScenarioConfig``).

    The textbook candidate writes no override beyond what
    ``corridor_sweep.cell_config`` writes for the strategy, so it is that
    sweep's cell, config hash included. Other candidates add their parameters:
    the ALINEA target and gain on every metered ramp, the VSL thresholds in
    ``av.vsl_params``, the FollowerStopper constants in
    ``av.controller_params``.
    """
    pen = penetration if space.vehicle_controller else 0.0
    comp = compliance if space.vehicle_controller else 1.0
    rho = None
    if "alinea" in space.infra:
        if rho_c_veh_km is None:
            raise SystemExit("--rho-target-veh-km is required for the alinea strategies")
        # the textbook candidate's target is rho_c itself, unrounded: the
        # sweep's own alinea cell, config hash included
        rho = (
            float(rho_c_veh_km)
            if textbook
            else round(float(values["rho_target_factor"]) * rho_c_veh_km, DECIMALS)
        )
    cfg = cell_config(base, pen, comp, space.vehicle_controller, space.infra, rho)
    if textbook:
        return cfg
    if "alinea" in space.infra:
        for ramp in on_ramps(cfg):
            ramp["meter"]["params"]["k_r_veh_h_per_veh_km"] = float(values["k_r_veh_h_per_veh_km"])
    if "vsl" in space.infra:
        cfg["av"]["vsl_params"] = _vsl_params(values)
    if space.vehicle_controller == "follower_stopper":
        cfg["av"]["controller_params"] = {
            **{k: v * float(values["dx0_scale"]) for k, v in _FS_DX0.items()},
            **{k: v * float(values["d_scale"]) for k, v in _FS_D.items()},
        }
    elif space.vehicle_controller == "follower_stopper_capacity":
        cfg["av"]["controller_params"] = {
            "h_max_s": float(values["h_max_s"]),
            "g0_m": float(values["g0_m"]),
        }
    return cfg


def cell_name(strategy: str, j: int) -> str:
    """Tuning-tree cell of candidate ``j``."""
    return f"{strategy}_c{j:02d}"


def best_cell(strategy: str) -> str:
    """Evaluation-tree cell of a strategy's selected setting."""
    return f"{strategy}_best"


@dataclass(frozen=True)
class Plan:
    """Everything the tuning phase runs, built without running anything."""

    scenario: str
    base_json: dict[str, Any]
    base_hash: str
    strategies: tuple[str, ...]
    budget: int
    tune_seeds: list[int]
    eval_seeds: list[int]
    metrics_args: dict[str, Any]
    rho_c_veh_km: float | None
    penetration: float
    compliance: float
    grid: dict[str, dict[str, Any]]
    configs: dict[str, dict[str, Any]]
    hashes: dict[str, str]

    @property
    def n_tuning_runs(self) -> int:
        return len(self.grid) * len(self.tune_seeds)

    @property
    def n_evaluation_runs(self) -> int:
        return (1 + len(self.strategies)) * len(self.eval_seeds)


def build_plan(
    base_cfg: ScenarioConfig,
    scenario: str,
    strategies: Sequence[str],
    *,
    budget: int,
    n_tune: int,
    n_eval: int,
    metrics_args: dict[str, Any],
    rho_c_veh_km: float | None,
    penetration: float = 0.05,
    compliance: float = 1.0,
) -> Plan:
    """Candidates, configs, hashes and seeds of a tuning study (nothing runs).

    Raises:
        SystemExit: An unknown strategy, a missing ALINEA target, an AV
            strategy at zero penetration, or fewer than one tuning seed.
        ValueError: Unequal budgets (never: every space gets ``budget``).
    """
    unknown = [s for s in strategies if s not in SPACES]
    if unknown:
        raise SystemExit(f"no declared search space for {unknown}; choose from {sorted(SPACES)}")
    if len(set(strategies)) != len(strategies) or not strategies:
        raise SystemExit("give each strategy once, at least one")
    if any("alinea" in SPACES[s].infra for s in strategies) and rho_c_veh_km is None:
        raise SystemExit(
            "--rho-target-veh-km is required for the alinea strategies (the corridor's per-lane "
            "critical density, e.g. rho_c of its FD artifact); there is no default"
        )
    if any(SPACES[s].vehicle_controller for s in strategies) and not 0.0 < penetration <= 0.3:
        raise SystemExit("--av-penetration must lie in (0, 0.3] for the AV strategies")
    if n_tune < 1 or n_eval < 1:
        raise SystemExit("need at least one tuning and one evaluation seed")
    tune, evaluation = seed_streams(base_cfg.seed, n_tune, n_eval)
    base_json = json.loads(base_cfg.model_dump_json())
    grid: dict[str, dict[str, Any]] = {
        BASELINE: {
            "penetration": 0.0,
            "compliance": 1.0,
            "controller": None,
            "strategy": "none",
        }
    }
    configs = {BASELINE: cell_config(base_json, 0.0, 1.0, None, "none", None)}
    counts: dict[str, int] = {}
    for strategy in strategies:
        space = SPACES[strategy]
        values = candidate_values(space, budget)
        counts[strategy] = len(values)
        for j, v in enumerate(values):
            name = cell_name(strategy, j)
            configs[name] = candidate_config(
                base_json,
                space,
                v,
                textbook=j == 0,
                rho_c_veh_km=rho_c_veh_km,
                penetration=penetration,
                compliance=compliance,
            )
            grid[name] = {
                "penetration": penetration if space.vehicle_controller else 0.0,
                "compliance": compliance if space.vehicle_controller else 1.0,
                "controller": space.vehicle_controller,
                "strategy": space.infra,
                "tuned_strategy": strategy,
                "candidate": j,
                "textbook": j == 0,
                "values": v,
            }
    if len(set(counts.values())) > 1:
        raise ValueError(f"unequal tuning budgets {counts}")
    hashes = {n: config_hash(ScenarioConfig.model_validate(c)) for n, c in configs.items()}
    return Plan(
        scenario=scenario,
        base_json=base_json,
        base_hash=config_hash(base_cfg),
        strategies=tuple(strategies),
        budget=budget,
        tune_seeds=tune,
        eval_seeds=evaluation,
        metrics_args=metrics_args,
        rho_c_veh_km=rho_c_veh_km,
        penetration=penetration,
        compliance=compliance,
        grid=grid,
        configs=configs,
        hashes=hashes,
    )


def plan_lines(plan: Plan) -> list[str]:
    """The ``--plan-only`` printout."""
    lines = [
        f"strategies: {', '.join(plan.strategies)}; budget {plan.budget} candidate(s) each "
        f"(candidate 0 = textbook)",
        f"tuning seeds ({len(plan.tune_seeds)}): {plan.tune_seeds}",
        f"evaluation seeds ({len(plan.eval_seeds)}): first {plan.eval_seeds[:3]}"
        + (" — UNDERPOWERED (< 20)" if len(plan.eval_seeds) < 20 else ""),
    ]
    if len(plan.tune_seeds) < 2:
        lines.append(
            "warning: one tuning seed — the throughput rule's interval is undefined and cannot "
            "exclude a candidate"
        )
    for name, g in plan.grid.items():
        if name == BASELINE:
            continue
        lines.append(f"  {name} [{plan.hashes[name]}]: {g['values']}")
    lines.append(
        f"tuning runs: {len(plan.grid)} cells × {len(plan.tune_seeds)} seeds = {plan.n_tuning_runs}"
    )
    lines.append(
        f"evaluation runs: {1 + len(plan.strategies)} cells × {len(plan.eval_seeds)} seeds = "
        f"{plan.n_evaluation_runs}"
    )
    lines.append(f"total runs: {plan.n_tuning_runs + plan.n_evaluation_runs}")
    return lines


# --------------------------------------------------------------------------- manifests


def tuning_manifest(plan: Plan, out: Path) -> dict[str, Any]:
    """``<out>/tuning/MANIFEST.json``: the sweep's layout plus the ``tuning`` block."""
    return {
        "experiment": f"{out.name}/{TUNING_DIR}",
        "scenario": plan.scenario,
        "base_config_hash": plan.base_hash,
        "grid_spec": {
            "phase": TUNING_DIR,
            "strategies": list(plan.strategies),
            "budget": plan.budget,
            "penetration": [plan.penetration],
            "compliance": [plan.compliance],
        },
        "rho_target_veh_km": plan.rho_c_veh_km,
        "metrics_args": plan.metrics_args,
        "grid": plan.grid,
        "cells": dict(plan.hashes),
        "seeds": plan.tune_seeds,
        "tuning": {
            "objective": OBJECTIVE,
            "objective_direction": "minimize",
            "throughput_measure": THROUGHPUT,
            "budget": plan.budget,
            "spaces": {s: SPACES[s].to_dict() for s in plan.strategies},
            "candidate_rule": "candidate 0 = textbook; 1..N-1 = unscrambled Halton points "
            f"1..N-1 on the box, rounded to {DECIMALS} decimals",
            "seed_streams": {
                "tuning": f"children (TUNING_SPAWN_KEY={TUNING_SPAWN_KEY}, i) of "
                "SeedSequence(scenario.seed)",
                "evaluation": "flowstate_core.rng.spawn_seeds(scenario.seed, n): children (i,)",
                "evaluation_seeds": plan.eval_seeds,
            },
            "av_penetration": plan.penetration,
            "av_compliance": plan.compliance,
        },
    }


def evaluation_manifest(
    plan_like: Mapping[str, Any],
    out: Path,
    selection: Mapping[str, Mapping[str, Any]],
    configs: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """``<out>/evaluation/MANIFEST.json`` and the evaluation cells' configs.

    Args:
        plan_like: The tuning manifest.
        out: Study root.
        selection: Strategy → its selection record (only selected strategies).
        configs: Tuning-tree cell → config.
    """
    tm = plan_like
    cells = {BASELINE: tm["cells"][BASELINE]}
    grid = {BASELINE: tm["grid"][BASELINE]}
    eval_configs = {BASELINE: configs[BASELINE]}
    for strategy, sel in selection.items():
        src = sel["cell"]
        cells[best_cell(strategy)] = tm["cells"][src]
        grid[best_cell(strategy)] = {**tm["grid"][src], "selected_from": src}
        eval_configs[best_cell(strategy)] = configs[src]
    manifest = {
        "experiment": f"{out.name}/{EVALUATION_DIR}",
        "scenario": tm["scenario"],
        "base_config_hash": tm["base_config_hash"],
        "grid_spec": {**tm["grid_spec"], "phase": EVALUATION_DIR},
        "rho_target_veh_km": tm["rho_target_veh_km"],
        "metrics_args": tm["metrics_args"],
        "grid": grid,
        "cells": cells,
        "seeds": tm["tuning"]["seed_streams"]["evaluation_seeds"],
        "selection": {
            s: {
                "cell": sel["cell"],
                "candidate": sel["candidate"],
                "config_hash": sel["config_hash"],
            }
            for s, sel in selection.items()
        },
    }
    return manifest, eval_configs


# --------------------------------------------------------------------------- selection


def candidate_status(
    records: Mapping[int, Mapping[str, Any]],
    baseline: Mapping[int, Mapping[str, Any]],
    seeds: Sequence[int],
) -> dict[str, Any]:
    """One candidate's tuning result and status (module docstring's rules, in order).

    Args:
        records: The candidate's run records by seed (``corridor_sweep.run_records``).
        baseline: The baseline's, same seeds.
        seeds: The tuning seeds.

    Returns:
        ``{"status", "objective", "throughput_vs_baseline",
        "throughput_rule_evaluable", "collisions", "n_runs"}``.
    """
    from validation.metrics import ci
    from validation.strategy_compare import paired_delta

    def num(rec: Mapping[str, Any], key: str) -> float:
        v = rec.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else math.nan

    present = [s for s in seeds if s in records]
    objective = ci([num(records[s], OBJECTIVE) for s in present])
    thr = paired_delta(
        {s: num(baseline[s], THROUGHPUT) for s in seeds if s in baseline},
        {s: num(records[s], THROUGHPUT) for s in present},
    )
    counts = [records[s].get("n_collisions") for s in present]
    out: dict[str, Any] = {
        "n_runs": len(present),
        "objective": _ci_json(objective),
        "throughput_vs_baseline": {
            "mean": _j(thr.mean),
            "lo95": _j(thr.lo95),
            "hi95": _j(thr.hi95),
            "n": thr.n,
        },
        "throughput_rule_evaluable": bool(math.isfinite(thr.hi95)),
        "collisions": (
            int(sum(int(c) for c in counts))
            if counts and all(isinstance(c, int) for c in counts)
            else None
        ),
    }
    if len(present) < len(seeds):
        status = INCOMPLETE
    elif any(isinstance(c, int) and c > 0 for c in counts):
        status = DISQUALIFIED_COLLISION
    elif any(not isinstance(c, int) for c in counts):
        status = COLLISIONS_NOT_RECORDED
    elif objective.n < len(seeds):
        status = OBJECTIVE_UNDEFINED
    elif math.isfinite(thr.hi95) and thr.hi95 < 0.0:
        status = THROUGHPUT_BELOW_BASELINE
    else:
        status = SELECTABLE
    out["status"] = status
    return out


def select(
    tuning_manifest_: Mapping[str, Any], records: Mapping[str, Mapping[int, Mapping[str, Any]]]
) -> dict[str, Any]:
    """Every candidate's status and each strategy's selected setting.

    Returns:
        ``{"candidates": {strategy: [record per candidate]}, "selected":
        {strategy: record}, "not_evaluated": {strategy: reason}}``.

    Raises:
        SystemExit: The baseline is incomplete on the tuning seeds.
    """
    seeds = [int(s) for s in tuning_manifest_["seeds"]]
    base = records.get(BASELINE, {})
    if any(s not in base for s in seeds):
        raise SystemExit("the baseline lacks tuning runs; nothing to select against")
    strategies = tuning_manifest_["grid_spec"]["strategies"]
    candidates: dict[str, list[dict[str, Any]]] = {s: [] for s in strategies}
    for cell, g in tuning_manifest_["grid"].items():
        if cell == BASELINE:
            continue
        rec = {
            "cell": cell,
            "candidate": g["candidate"],
            "textbook": g["textbook"],
            "values": g["values"],
            "config_hash": tuning_manifest_["cells"][cell],
            **candidate_status(records.get(cell, {}), base, seeds),
        }
        candidates[g["tuned_strategy"]].append(rec)
    selected: dict[str, dict[str, Any]] = {}
    not_evaluated: dict[str, str] = {}
    for strategy, rows in candidates.items():
        rows.sort(key=lambda r: int(r["candidate"]))
        ok = [r for r in rows if r["status"] == SELECTABLE]
        if not ok:
            not_evaluated[strategy] = "no selectable candidate: " + ", ".join(
                f"c{r['candidate']:02d} {r['status']}" for r in rows
            )
            continue
        best = min(ok, key=lambda r: (float(r["objective"]["mean"]), int(r["candidate"])))
        best["selected"] = True
        selected[strategy] = best
    return {"candidates": candidates, "selected": selected, "not_evaluated": not_evaluated}


def _j(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def _ci_json(c: Any) -> dict[str, Any]:
    return {"mean": _j(c.mean), "lo95": _j(c.lo95), "hi95": _j(c.hi95), "n": int(c.n)}


# --------------------------------------------------------------------------- running


Payload = tuple[str, dict[str, Any], int, dict[str, Any], str, bool]


def pending_payloads(
    root: Path,
    cells: Mapping[str, str],
    configs: Mapping[str, dict[str, Any]],
    seeds: Sequence[int],
    metrics_args: Mapping[str, Any],
    keep: bool,
) -> list[Payload]:
    """``corridor_sweep._worker`` payloads of the runs not yet done."""
    margs = {"x_ref": float(metrics_args["x_ref"]), "span": tuple(metrics_args["span"])}
    return [
        (cell, configs[cell], int(seed), margs, str(root), keep)
        for cell, chash in cells.items()
        for seed in seeds
        if not _done(root, cell, chash, int(seed))
    ]


def execute(
    pending: Sequence[Payload],
    procs: int,
    worker: Callable[[Payload], tuple[str, int, bool, str]] = _worker,
) -> int:
    """Run the payloads through the sweep worker; returns the number that failed."""
    t0 = time.perf_counter()
    n_fail = 0

    def report(i: int, cell: str, seed: int, ok: bool, err: str) -> None:
        nonlocal n_fail
        if not ok:
            n_fail += 1
            print(f"  FAIL {cell} seed={seed}: {err}", flush=True)
        if i % 10 == 0 or i == len(pending):
            print(f"  {i}/{len(pending)} ({time.perf_counter() - t0:.0f} s)", flush=True)

    if procs <= 1:
        for i, payload in enumerate(pending, start=1):
            cell, seed, ok, err = worker(payload)
            report(i, cell, seed, ok, err)
    elif pending:
        with mp.get_context("spawn").Pool(min(procs, len(pending))) as pool:
            for i, (cell, seed, ok, err) in enumerate(
                pool.imap_unordered(worker, pending), start=1
            ):
                report(i, cell, seed, ok, err)
    print(f"runs done in {time.perf_counter() - t0:.0f} s; {n_fail} failed", flush=True)
    return n_fail


# --------------------------------------------------------------------------- analysis


def git_head() -> str:
    """The code's commit (``"<sha> <subject>"``), or ``"unknown"``."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty() -> bool | None:
    """Whether :data:`CODE_PATHS` had uncommitted changes (None without git)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def analyze_study(
    out: Path, summary_path: Path, argv: Sequence[str] | None = None
) -> dict[str, Any]:
    """Re-analyse a study tree: selection from the tuning tree, comparison from the evaluation tree.

    Runs ``corridor_sweep.analyze`` on each tree (its ``analysis.json`` and
    ``sweep_summary.json`` per tree), selects per the module docstring's rules
    and, when the evaluation tree exists, builds the comparison table
    (``<out>/comparison.json`` and ``.md``) — or records why it refused.

    Raises:
        SystemExit: No tuning manifest; the baseline lacks tuning runs; or the
            evaluation tree was run for a different selection than the tuning
            tree gives now.
    """
    from validation.strategy_compare import ComparisonRefusedError

    troot = out / TUNING_DIR
    tpath = troot / "MANIFEST.json"
    if not tpath.is_file():
        raise SystemExit(f"no tuning manifest at {tpath}")
    tm = json.loads(tpath.read_text())
    analyze(troot, troot / "sweep_summary.json", allow_partial=True)
    tseeds = [int(s) for s in tm["seeds"]]
    sel = select(tm, run_records(troot, tm["cells"], tseeds))
    tuning = tm["tuning"]
    eval_seeds = [int(s) for s in tuning["seed_streams"]["evaluation_seeds"]]
    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "provenance": {
            "script": "scripts/strategy_tune.py",
            "analysed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code": git_head(),
            "code_dirty": git_dirty(),
            "argv": list(sys.argv[1:] if argv is None else argv),
            "metrics_source": "scripts/corridor_sweep.py _worker (validation.metrics."
            "compute_metrics + compute_waiting_metrics)",
        },
        "scenario": tm["scenario"],
        "base_config_hash": tm["base_config_hash"],
        "metrics_args": tm["metrics_args"],
        "objective": {
            "measure": OBJECTIVE,
            "direction": "minimize",
            "rule": "mean over tuning seeds; collisions disqualify; a throughput paired-difference "
            "interval entirely below zero is reported but not selectable "
            "(docs/FRISCO_PROTOCOL.md §8.4)",
        },
        "budget": tuning["budget"],
        "spaces": tuning["spaces"],
        "candidate_rule": tuning["candidate_rule"],
        "seeds": {
            "tuning": tseeds,
            "evaluation": eval_seeds,
            "streams": {k: v for k, v in tuning["seed_streams"].items() if k != "evaluation_seeds"},
            "disjoint": not (set(tseeds) & set(eval_seeds)),
        },
        "tuning_throughput_rule_evaluable": len(tseeds) >= 2,
        "candidates": sel["candidates"],
        "selected": {
            s: {
                k: r[k]
                for k in ("cell", "candidate", "textbook", "values", "config_hash", "objective")
            }
            for s, r in sel["selected"].items()
        },
        "not_evaluated": sel["not_evaluated"],
        "run_counts": {
            "tuning": len(tm["cells"]) * len(tseeds),
            "evaluation": (1 + len(sel["selected"])) * len(eval_seeds),
        },
        "evaluation": None,
    }
    eroot = out / EVALUATION_DIR
    epath = eroot / "MANIFEST.json"
    if epath.is_file():
        em = json.loads(epath.read_text())
        expected = {s: r["config_hash"] for s, r in sel["selected"].items()}
        recorded = {s: v["config_hash"] for s, v in em["selection"].items()}
        if expected != recorded:
            raise SystemExit(
                f"the evaluation tree was run for selection {recorded}, but the tuning tree now "
                f"selects {expected}; re-run the study (without --analyze-only) to evaluate the "
                "current selection"
            )
        analyze(eroot, eroot / "sweep_summary.json", allow_partial=True)
        records = run_records(eroot, em["cells"], [int(s) for s in em["seeds"]])
        block: dict[str, Any] = {
            "seeds": [int(s) for s in em["seeds"]],
            "underpowered": len(em["seeds"]) < DEFAULT_EVAL_SEEDS,
            "cells": em["cells"],
            "comparison": None,
            "comparison_refused": None,
        }
        try:
            block["comparison"] = write_comparison(records, out / "comparison.json")
        except ComparisonRefusedError as exc:
            block["comparison_refused"] = str(exc)
        summary["evaluation"] = block
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False))
    summary_path.with_suffix(".md").write_text(render_summary(summary, out))
    return summary


def _fmt(x: Any) -> str:
    """A number for the Markdown tables (the comparison table's convention)."""
    if x is None or not math.isfinite(float(x)):
        return "—"
    v = float(x)
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def render_summary(summary: Mapping[str, Any], out: Path) -> str:
    """The study's Markdown summary (selection per strategy, then the comparison)."""
    lines = [
        "# Strategy tuning and evaluation",
        "",
        f"Scenario `{summary['scenario']}` (config {summary['base_config_hash']}); objective "
        f"`{summary['objective']['measure']}` (lower is better), {summary['budget']} candidate(s) "
        f"per strategy, {len(summary['seeds']['tuning'])} tuning seed(s), "
        f"{len(summary['seeds']['evaluation'])} evaluation seed(s); streams disjoint: "
        f"{summary['seeds']['disjoint']}. Code {summary['provenance']['code'][:12]}"
        + (" (uncommitted changes)" if summary["provenance"]["code_dirty"] else "")
        + ".",
        "",
    ]
    if not summary["tuning_throughput_rule_evaluable"]:
        lines += [
            "One tuning seed: the throughput rule could not exclude any candidate.",
            "",
        ]
    for strategy, rows in summary["candidates"].items():
        lines += [
            f"## {strategy}",
            "",
            "| Candidate | Values | Objective mean [95 % interval] | Throughput vs baseline "
            "[95 % interval] | Collisions | Status |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            o, t = r["objective"], r["throughput_vs_baseline"]
            mark = " **selected**" if r.get("selected") else ""
            tag = " (textbook)" if r["textbook"] else ""
            lines.append(
                f"| c{r['candidate']:02d}{tag} | "
                + ", ".join(f"{k} {v:g}" for k, v in r["values"].items())
                + f" | {_fmt(o['mean'])} [{_fmt(o['lo95'])}, {_fmt(o['hi95'])}] | "
                f"{_fmt(t['mean'])} [{_fmt(t['lo95'])}, {_fmt(t['hi95'])}] | "
                f"{'—' if r['collisions'] is None else r['collisions']} | {r['status']}{mark} |"
            )
        if strategy in summary["not_evaluated"]:
            lines += ["", f"Not evaluated: {summary['not_evaluated'][strategy]}."]
        lines.append("")
    ev = summary.get("evaluation")
    lines.append("## Evaluation")
    lines.append("")
    if ev is None:
        lines.append("Not run yet.")
    elif ev["comparison_refused"]:
        lines.append(f"Comparison refused: {ev['comparison_refused']}")
    else:
        md = out / "comparison.md"
        lines.append(md.read_text() if md.is_file() else "(comparison.md missing)")
    return "\n".join(lines) + "\n"


def print_summary(summary: Mapping[str, Any]) -> None:
    for strategy, r in summary["selected"].items():
        o = r["objective"]
        print(
            f"selected {strategy}: c{r['candidate']:02d} {r['values']} — objective "
            f"{_fmt(o['mean'])} veh·h (n={o['n']})"
        )
    for strategy, why in summary["not_evaluated"].items():
        print(f"not evaluated {strategy}: {why}")
    ev = summary.get("evaluation")
    if ev is None:
        print("evaluation: not run")
    elif ev["comparison_refused"]:
        print(f"evaluation: comparison REFUSED — {ev['comparison_refused']}")
    else:
        flag = " (UNDERPOWERED)" if ev["underpowered"] else ""
        print(f"evaluation: comparison over {len(ev['seeds'])} seeds{flag}")


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    ap = argparse.ArgumentParser(
        prog="python scripts/strategy_tune.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--scenario", type=Path, help="scenario YAML (not with --analyze-only)")
    ap.add_argument("--strategies", nargs="+", default=["alinea", "vsl"], choices=sorted(SPACES))
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="candidates per strategy")
    ap.add_argument("--tuning-seeds", type=int, default=DEFAULT_TUNING_SEEDS)
    ap.add_argument("--eval-seeds", type=int, default=DEFAULT_EVAL_SEEDS)
    ap.add_argument(
        "--rho-target-veh-km", type=float, default=None, help="corridor critical density per lane"
    )
    ap.add_argument("--av-penetration", type=float, default=0.05)
    ap.add_argument("--av-compliance", type=float, default=1.0)
    ap.add_argument("--x-ref", type=float, help="throughput cross-section [m]")
    ap.add_argument("--span", type=float, nargs=2, metavar=("LO", "HI"), help="analysed span [m]")
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--out", required=True, type=Path, help="study root")
    ap.add_argument("--summary", type=Path, default=None, help="default <out>/strategy_tune.json")
    ap.add_argument("--keep-trajectories", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """Run (or plan, or re-analyse) a study; returns the exit code."""
    args = build_parser().parse_args(argv)
    out: Path = args.out
    summary_path: Path = args.summary or out / "strategy_tune.json"
    if args.analyze_only:
        s = analyze_study(out, summary_path, argv)
        print_summary(s)
        ev = s["evaluation"]
        return 1 if ev is not None and ev["comparison_refused"] else 0
    if args.scenario is None or args.x_ref is None or args.span is None:
        raise SystemExit("--scenario, --x-ref and --span are required (except with --analyze-only)")
    base_cfg = ScenarioConfig.from_yaml(args.scenario)
    plan = build_plan(
        base_cfg,
        str(args.scenario),
        args.strategies,
        budget=args.budget,
        n_tune=args.tuning_seeds,
        n_eval=args.eval_seeds,
        metrics_args={"x_ref": float(args.x_ref), "span": [float(v) for v in args.span]},
        rho_c_veh_km=args.rho_target_veh_km,
        penetration=args.av_penetration,
        compliance=args.av_compliance,
    )
    for line in plan_lines(plan):
        print(line)
    if args.plan_only:
        return 0

    troot = out / TUNING_DIR
    troot.mkdir(parents=True, exist_ok=True)
    tm = tuning_manifest(plan, out)
    (troot / "MANIFEST.json").write_text(json.dumps(tm, indent=2))
    pending = pending_payloads(
        troot, plan.hashes, plan.configs, plan.tune_seeds, plan.metrics_args, args.keep_trajectories
    )
    print(f"tuning: {plan.n_tuning_runs} runs; {len(pending)} pending", flush=True)
    execute(pending, args.procs)

    sel = select(tm, run_records(troot, tm["cells"], plan.tune_seeds))
    em, econfigs = evaluation_manifest(tm, out, sel["selected"], plan.configs)
    eroot = out / EVALUATION_DIR
    eroot.mkdir(parents=True, exist_ok=True)
    (eroot / "MANIFEST.json").write_text(json.dumps(em, indent=2))
    pending = pending_payloads(
        eroot, em["cells"], econfigs, em["seeds"], plan.metrics_args, args.keep_trajectories
    )
    n_eval = len(em["cells"]) * len(em["seeds"])
    print(f"evaluation: {n_eval} runs; {len(pending)} pending", flush=True)
    execute(pending, args.procs)

    s = analyze_study(out, summary_path, argv)
    print_summary(s)
    print(f"summary → {summary_path} (+ .md)")
    ev = s["evaluation"]
    return 1 if ev is not None and ev["comparison_refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
