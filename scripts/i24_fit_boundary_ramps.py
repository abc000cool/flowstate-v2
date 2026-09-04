"""Fit the I-24 replica's ramp levels and boundary speed out of sample.

Why. With the capacity-calibrated population and the fitted demand level
(``scenarios/i24_replica_speedcal.yaml``), the replica's residual is spatial:
the first kilometre — the Old Hickory Boulevard merge — runs a third too slow
and the kilometre after it a third too fast, while everything downstream of
2.2 km is within 4% of the recording (docs/I24_CAPACITY.md §5–6). The merge
diagnostic showed the Old Hickory inflow *level* alone is not the overshoot,
but every ramp count and exit fraction in the scenario is a fragment count at
the instrument's coverage (docs/I24_DATA.md §4) — a lower bound whose
per-ramp bias is unknown — and the measured downstream speed schedule is
applied as a speed *limit* on the exit edge, which vehicles run below. Those
are boundary inputs in the FHWA sense (Traffic Analysis Toolbox Vol. III,
FHWA-HOP-18-036: demands and boundary conditions are calibration inputs),
so they are fitted the way the demand level was: on observed segment speeds
over the FIRST hour of the study period (06:30–07:30 CST, windows 0–11),
with the SECOND hour (07:30–08:30, windows 12–23) held out and reported at
every evaluation, one seeded replicate per point (the scenario's first
replicate seed). Nothing else is touched.

Multipliers (each scales one step profile of the scenario dict; times are
never changed):

* ``oh_ramp`` — Old Hickory Blvd on-ramp inflow;
* ``hh_ramp`` — Hickory Hollow Pkwy on-ramp inflow;
* ``hh_exit`` — Hickory Hollow Pkwy off-ramp exit fraction, clipped to [0, 1];
* ``bell_exit`` — Bell Road off-ramp exit fraction, clipped to [0, 1];
* ``lc_assertive`` — SUMO ``lcAssertive`` on the whole fleet (gap acceptance
  at the merge; the value itself, 1.0 = default, bounded to [1, 2]);
* ``boundary_speed`` — the measured downstream speed schedule, clipped to
  the range of speeds the instrument actually measured (its own min/max).

Method: :func:`calibration.demand.fit_multipliers` — deterministic compass
search on a shrinking coordinate grid (3 points per multiplier per round),
each round's candidates simulated in parallel through a spawn pool, every
point memoized so the fit resumes from its own artifact (``--resume``).
Ties go toward the unscaled scenario.

Outputs ``artifacts/i24_boundary_ramps_fit.json`` (every evaluation with
fitted-hour and held-out RMSPE, inserted fraction and ramp counts; best
point; provenance) and, with ``--write-scenario``,
``scenarios/i24_replica_speedcal_ramps.yaml``. ``--smoke`` runs one
shortened evaluation (600 s) of the unscaled point to prove the plumbing
and writes under ``runs/`` instead.

Run (cloud VM; each evaluation is a full 7,800 s replica run)::

    uv run --no-sync python scripts/i24_fit_boundary_ramps.py --procs 11 --rounds 4 --write-scenario
    uv run --no-sync python scripts/i24_fit_boundary_ramps.py --smoke --procs 1    # plumbing only
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import multiprocessing as mp
import sys
import tempfile
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_validate import WARMUP_S, _inputs, _json_safe, _segment_speeds, _sim_frame, _span

from calibration.demand import (
    EvalRecord,
    EvalResult,
    MultiplierFit,
    MultiplierSpec,
    fit_multipliers,
)
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from flowstate_core.units import ms_to_kmh
from microsim.runner import _versions, run_micro
from validation.metrics import rmspe

REPO = Path(__file__).resolve().parents[1]
BASE_YAML = REPO / "scenarios" / "i24_replica_speedcal.yaml"
OBSERVED = REPO / "artifacts" / "i24_validation_observed.json"
OUT = REPO / "artifacts" / "i24_boundary_ramps_fit.json"
SMOKE_OUT = REPO / "runs" / "i24_boundary_ramps" / "smoke.json"
SCENARIO_OUT = REPO / "scenarios" / "i24_replica_speedcal_ramps.yaml"
SCENARIO_NAME = "i24_replica_speedcal_ramps"

OH_ON = "Old Hickory Blvd on-ramp"
HH_OFF = "Hickory Hollow Pkwy off-ramp"
HH_ON = "Hickory Hollow Pkwy on-ramp"
BELL_OFF = "Bell Road off-ramp (collector road)"

#: Which scenario profile each multiplier scales: (ramp name, field) for
#: ramps; ``boundary_speed`` is the network's boundary schedule.
RAMP_TARGETS: dict[str, tuple[str, str]] = {
    "oh_ramp": (OH_ON, "inflow"),
    "hh_ramp": (HH_ON, "inflow"),
    "hh_exit": (HH_OFF, "exit_fraction"),
    "bell_exit": (BELL_OFF, "exit_fraction"),
}
BOUNDARY_MULTIPLIER = "boundary_speed"
#: SUMO ``lcAssertive`` (gap acceptance divisor) as a sixth coordinate: the
#: merge experiment (docs/I24_CAPACITY.md §6.1) showed it clears the Old
#: Hickory merge but only makes sense jointly with the downstream levels.
LC_ASSERTIVE_MULTIPLIER = "lc_assertive"
MULTIPLIERS: tuple[MultiplierSpec, ...] = (
    MultiplierSpec("oh_ramp", 1.0, 0.5, 1.5),
    MultiplierSpec("hh_ramp", 1.0, 0.5, 1.5),
    MultiplierSpec("hh_exit", 1.0, 0.5, 1.5),
    MultiplierSpec("bell_exit", 1.0, 0.5, 1.5),
    MultiplierSpec(BOUNDARY_MULTIPLIER, 1.0, 0.5, 1.5),
    MultiplierSpec(LC_ASSERTIVE_MULTIPLIER, 1.0, 1.0, 2.0),
)
MULTIPLIER_NAMES = tuple(m.name for m in MULTIPLIERS)

TRAIN_WINDOWS = range(0, 12)  # 06:30-07:30 CST — fitted
TEST_WINDOWS = range(12, 24)  # 07:30-08:30 CST — held out
SMOKE_DURATION_S = 600.0
SMOKE_WARMUP_S = 60.0
ARTIFACT_SCHEMA_VERSION = 1


def apply_multipliers(base: Mapping[str, Any], values: Mapping[str, float]) -> dict[str, Any]:
    """The scenario dict with each named multiplier applied to its profile.

    Args:
        base: Scenario dict as loaded from YAML (never mutated).
        values: Multiplier values keyed by name (any subset of
            :data:`MULTIPLIER_NAMES`; absent names leave their profile
            unchanged).

    Returns:
        A deep copy of ``base`` with the on-ramp inflows and off-ramp exit
        fractions multiplied (exit fractions clipped to [0, 1]) and the
        boundary speed schedule multiplied and clipped to its own unscaled
        [min, max]; step times untouched; ``name`` set to
        :data:`SCENARIO_NAME`.

    Raises:
        ValueError: On an unknown multiplier name, a missing ramp, or a
            boundary multiplier without a boundary schedule.
    """
    unknown = sorted(set(values) - set(MULTIPLIER_NAMES))
    if unknown:
        raise ValueError(f"unknown multipliers {unknown}; known: {list(MULTIPLIER_NAMES)}")
    raw = copy.deepcopy(dict(base))
    net = raw["network"]
    ramps = {r.get("name", ""): r for r in net.get("ramps", [])}
    for name, m in values.items():
        if name == LC_ASSERTIVE_MULTIPLIER:
            if float(m) != 1.0:  # 1.0 is SUMO's default and the field's default
                raw.setdefault("fleet", {})["lc_assertive"] = round(float(m), 6)
            continue
        if name == BOUNDARY_MULTIPLIER:
            boundary = net.get("boundary")
            if not boundary or not boundary.get("steps"):
                raise ValueError("boundary_speed needs a network.boundary speed schedule")
            speeds = [v for _, v in boundary["steps"]]
            lo, hi = min(speeds), max(speeds)
            boundary["steps"] = [
                [t, round(min(max(v * m, lo), hi), 6)] for t, v in boundary["steps"]
            ]
            continue
        ramp_name, field_name = RAMP_TARGETS[name]
        if ramp_name not in ramps:
            raise ValueError(f"{name}: ramp {ramp_name!r} not in the scenario")
        steps = ramps[ramp_name][field_name]
        if field_name == "exit_fraction":
            ramps[ramp_name][field_name] = [
                [t, round(min(max(f * m, 0.0), 1.0), 6)] for t, f in steps
            ]
        else:
            ramps[ramp_name][field_name] = [[t, round(q * m, 6)] for t, q in steps]
    raw["name"] = SCENARIO_NAME
    return raw


def base_scenario(smoke: bool = False, path: Path = BASE_YAML) -> dict[str, Any]:
    """The base scenario dict; ``smoke`` shortens it to one 600 s window."""
    raw = yaml.safe_load(path.read_text())
    if smoke:
        raw["sim"]["duration_s"] = SMOKE_DURATION_S
        raw["sim"]["warmup_s"] = SMOKE_WARMUP_S
        raw["name"] = f"{SCENARIO_NAME}_smoke"
    return raw


def load_observed(path: Path = OBSERVED) -> np.ndarray:
    """Observed segment speeds [m/s], shape (windows, segments); NaN where empty."""
    return np.array(json.loads(path.read_text())["segment_speeds_ms"], dtype=float)


def rmspe_windows(sim: np.ndarray, obs: np.ndarray, windows: range) -> float:
    """Segment-speed RMSPE over ``windows`` on the bins finite on both sides (NaN if none)."""
    s = sim[list(windows)].ravel()
    o = obs[list(windows)].ravel()
    ok = np.isfinite(s) & np.isfinite(o) & (o != 0.0)
    if not ok.any():
        return float("nan")
    return float(rmspe(s[ok], o[ok]))


@dataclass(frozen=True)
class SimResult:
    """One replicate reduced to what the objective needs.

    Attributes:
        segment_speeds_ms: Mean sampled speed per (window, segment) on the
            measured span, NaN where no vehicle was sampled.
        inserted_fraction: Departed / planned vehicles.
        config_hash: Hash of the configuration that ran.
        ramps: Per-ramp ``{name, kind, n_planned, n_departed}`` from the run
            metadata (``None`` when the run recorded none).
        wall_s: Wall time of the run and its reduction.
    """

    segment_speeds_ms: np.ndarray
    inserted_fraction: float
    config_hash: str
    ramps: list[dict[str, Any]] | None
    wall_s: float


SimulateFn = Callable[[dict[str, Any], int, int], SimResult]
"""``(scenario dict, seed, n_windows) → SimResult``; the real one runs SUMO."""


def study_frame(run_dir: Path, a: float, b: float, warmup_s: float) -> pd.DataFrame:
    """One replicate's trajectories in observed coordinates (data x, study t).

    ``scripts/i24_validate._sim_frame`` for the replica's 600 s warmup; the
    same arithmetic with the scenario's own warmup otherwise (the smoke
    run), so ``t = 0`` is always the start of the study period.
    """
    if warmup_s == WARMUP_S:
        return _sim_frame(run_dir, a, b)
    df = pd.read_parquet(run_dir / "trajectories.parquet")
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - warmup_s
    return df[df["t"] >= 0.0]


def simulate_point(raw: dict[str, Any], seed: int, n_win: int) -> SimResult:
    """Run one seeded replicate of ``raw`` in a temporary directory and reduce it."""
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    _lo, span_hi = _span()
    cfg = ScenarioConfig.model_validate(raw)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="i24_boundary_ramps_") as td:
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        df = study_frame(paths.run_dir, a, b, cfg.sim.warmup_s)
        seg = _segment_speeds(df, span_hi, n_win)
    ramps = meta.get("ramps")
    return SimResult(
        segment_speeds_ms=seg,
        inserted_fraction=meta["n_vehicles_departed"] / meta["n_vehicles_planned"],
        config_hash=meta["config_hash"],
        ramps=(
            [{k: r[k] for k in ("name", "kind", "n_planned", "n_departed")} for r in ramps]
            if ramps
            else None
        ),
        wall_s=time.perf_counter() - t0,
    )


def score(sim: SimResult, obs: np.ndarray) -> EvalResult:
    """Fitted-window RMSPE as the objective; held-out RMSPE and run facts as diagnostics."""
    seg = sim.segment_speeds_ms
    if seg.shape != obs.shape:
        raise ValueError(f"segment speeds {seg.shape} vs observed {obs.shape}")
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns in a smoke run
        rel = np.nanmean((seg - obs) / obs, axis=0)
        seg_mean = np.nanmean(seg, axis=0)
    return EvalResult(
        objective=rmspe_windows(seg, obs, TRAIN_WINDOWS),
        diagnostics={
            "rmspe_test": rmspe_windows(seg, obs, TEST_WINDOWS),
            "rmspe_all": rmspe_windows(seg, obs, range(obs.shape[0])),
            "inserted_fraction": round(float(sim.inserted_fraction), 4),
            "config_hash": sim.config_hash,
            "ramps": sim.ramps,
            "segment_mean_kmh": [round(ms_to_kmh(float(v)), 2) for v in seg_mean],
            "segment_rel_error": [round(float(v), 4) for v in rel],
            "wall_s": round(float(sim.wall_s), 1),
        },
    )


def evaluate_point(
    values: dict[str, float],
    *,
    seed: int,
    smoke: bool = False,
    simulate: SimulateFn = simulate_point,
    base: Mapping[str, Any] | None = None,
    observed: np.ndarray | None = None,
) -> EvalResult:
    """The objective handed to :func:`fit_multipliers` (runs in a pool worker).

    Args:
        values: Multiplier values.
        seed: Replicate seed (the same for every point).
        smoke: Shortened run (see :func:`base_scenario`).
        simulate: Injected simulator (tests pass a fake).
        base: Scenario dict to scale (default: the base YAML).
        observed: Observed segment speeds (default: the observed artifact).

    Returns:
        :class:`EvalResult` — objective = RMSPE over windows 0–11,
        diagnostics carry the held-out windows 12–23.
    """
    raw = apply_multipliers(base_scenario(smoke) if base is None else base, values)
    if smoke and base is None:
        raw["name"] = f"{SCENARIO_NAME}_smoke"
    obs = load_observed() if observed is None else observed
    return score(simulate(raw, seed, int(obs.shape[0])), obs)


def _record_row(rec: EvalRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "round": rec.round,
        "values": {n: rec.values[n] for n in MULTIPLIER_NAMES},
        "rmspe_train": rec.objective,
    }
    row.update(rec.diagnostics)
    return row


def _row_record(row: Mapping[str, Any]) -> EvalRecord:
    diagnostics = {k: v for k, v in row.items() if k not in ("round", "values", "rmspe_train")}
    return EvalRecord(
        values={n: float(row["values"][n]) for n in MULTIPLIER_NAMES},
        objective=float("nan") if row["rmspe_train"] is None else float(row["rmspe_train"]),
        diagnostics=diagnostics,
        round=int(row["round"]),
    )


def provenance(base_cfg: ScenarioConfig, seed: int, smoke: bool, grid: int) -> dict[str, Any]:
    """Everything a reader needs to reproduce the fit."""
    observed = json.loads(OBSERVED.read_text())
    return {
        "base_scenario": str(BASE_YAML.relative_to(REPO)),
        "base_config_hash": config_hash(base_cfg),
        "fleet_artifact": base_cfg.fleet.idm_calibration,
        "observed_artifact": str(OBSERVED.relative_to(REPO)),
        "observed_data_hash": observed.get("data_hash"),
        "observed_period": observed.get("period"),
        "seed": seed,
        "replicates_per_evaluation": 1,
        "smoke": smoke,
        "objective": (
            "segment-speed RMSPE, windows 0-11 (06:30-07:30 CST) fitted; "
            "windows 12-23 (07:30-08:30 CST) held out and reported as rmspe_test"
        ),
        "train_windows": [TRAIN_WINDOWS.start, TRAIN_WINDOWS.stop],
        "test_windows": [TEST_WINDOWS.start, TEST_WINDOWS.stop],
        "multipliers": [
            {"name": m.name, "initial": m.initial, "lower": m.lower, "upper": m.upper}
            for m in MULTIPLIERS
        ],
        "multiplier_targets": {
            **{k: {"ramp": r, "field": f} for k, (r, f) in RAMP_TARGETS.items()},
            BOUNDARY_MULTIPLIER: {
                "field": "network.boundary.steps",
                "clip": "unscaled schedule min/max [m/s]",
            },
        },
        "method": (
            f"calibration.demand.fit_multipliers: compass search, {grid}-point grid per "
            "multiplier per round, step halves after a round without improvement, "
            "ties toward the unscaled scenario; one seeded replicate per point"
        ),
        "seeded": False,
        "versions": _versions(),
    }


def artifact(fit: MultiplierFit, prov: dict[str, Any], created_at: str) -> dict[str, Any]:
    """The JSON artifact for a (possibly partial) fit."""
    best = _record_row(
        EvalRecord(values=fit.best, objective=fit.objective, diagnostics=fit.diagnostics, round=-1)
    )
    best.pop("round")
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "created_at": created_at,
        "provenance": prov,
        "rounds": [
            {
                "index": r.index,
                "step": r.step,
                "n_fresh": r.n_fresh,
                "n_cached": r.n_cached,
                "moved": r.moved,
                "best": r.best,
                "rmspe_train": r.objective,
            }
            for r in fit.rounds
        ],
        "n_evaluations": len(fit.log),
        "converged": fit.converged,
        "final_step": fit.step,
        "log": [_record_row(rec) for rec in fit.log],
        "best": best,
    }


def load_prior(path: Path, prov: dict[str, Any]) -> list[EvalRecord]:
    """Prior evaluations from an artifact whose provenance matches, else none."""
    if not path.is_file():
        return []
    old = json.loads(path.read_text())
    keys = ("base_config_hash", "seed", "smoke", "observed_data_hash")
    old_prov = old.get("provenance", {})
    if any(old_prov.get(k) != prov.get(k) for k in keys):
        print(f"  {path}: provenance differs, not resuming from it", flush=True)
        return []
    return [_row_record(row) for row in old.get("log", [])]


def scenario_header(best: dict[str, float], fit: MultiplierFit, chash: str) -> str:
    """YAML header comment stating what was fitted on which hour and what was held out."""
    parts = ", ".join(f"{n} x {best[n]:.4f}" for n in MULTIPLIER_NAMES)
    test = fit.diagnostics.get("rmspe_test")
    test_s = "n/a" if test is None or not np.isfinite(test) else f"{test:.3f}"
    return (
        f"# {SCENARIO_NAME} — {BASE_YAML.name} with its ramp levels and boundary speed\n"
        f"# multiplied ({parts}), fitted by scripts/i24_fit_boundary_ramps.py on\n"
        "# observed segment speeds over 06:30-07:30 CST only (windows 0-11);\n"
        "# 07:30-08:30 CST (windows 12-23) is held out and never entered the objective.\n"
        "# Old Hickory / Hickory Hollow on-ramp inflows, Hickory Hollow / Bell Road exit\n"
        "# fractions (clipped to [0, 1]) and the measured boundary schedule (clipped to its\n"
        "# measured range) are the only changes; mainline demand, fleet and lane-change\n"
        "# parameters are those of the base. See artifacts/i24_boundary_ramps_fit.json\n"
        f"# (rmspe train {fit.objective:.3f}, held-out test {test_s}, inserted "
        f"{fit.diagnostics.get('inserted_fraction', float('nan')):.3f}).\n"
        f"# config hash {chash}; seeded=False.\n"
    )


def _print_rows(rows: list[EvalRecord]) -> None:
    for rec in rows:
        d = rec.diagnostics
        vals = " ".join(f"{n}={rec.values[n]:.3f}" for n in MULTIPLIER_NAMES)
        test = d.get("rmspe_test")
        test_s = "  n/a" if test is None or not np.isfinite(test) else f"{test:.3f}"
        print(
            f"  r{rec.round} {vals} inserted={d.get('inserted_fraction', float('nan')):.3f} "
            f"rmspe train={rec.objective:.3f} test={test_s}",
            flush=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--grid", type=int, default=3, help="points per multiplier per round (odd)")
    ap.add_argument("--step", type=float, default=None, help="initial half-width (default 0.25)")
    ap.add_argument("--smoke", action="store_true", help="one 600 s run of the unscaled point")
    ap.add_argument("--resume", action="store_true", help="reuse evaluations from --out")
    ap.add_argument("--write-scenario", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out: Path = args.out if args.out is not None else (SMOKE_OUT if args.smoke else OUT)
    rounds = 0 if args.smoke else args.rounds

    base_cfg = ScenarioConfig.model_validate(base_scenario(args.smoke))
    seed = spawn_seeds(base_cfg.seed, base_cfg.replicates)[0]
    prov = provenance(base_cfg, seed, args.smoke, args.grid)
    created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    prior = load_prior(out, prov) if args.resume else []
    if prior:
        print(f"resuming with {len(prior)} prior evaluations from {out}", flush=True)
    print(
        f"base {BASE_YAML.name} ({prov['base_config_hash']}), seed {seed}, "
        f"{len(MULTIPLIERS)} multipliers, {args.grid}-point grid, {rounds} rounds"
        f"{' [SMOKE]' if args.smoke else ''}",
        flush=True,
    )
    evaluate = functools.partial(evaluate_point, seed=seed, smoke=args.smoke)
    out.parent.mkdir(parents=True, exist_ok=True)
    n_printed = len(prior)

    def checkpoint(partial: MultiplierFit) -> None:
        nonlocal n_printed
        _print_rows(partial.log[n_printed:])
        n_printed = len(partial.log)
        out.write_text(
            json.dumps(_json_safe(artifact(partial, prov, created_at)), indent=1, allow_nan=False)
        )
        last = partial.rounds[-1]
        print(
            f"round {last.index}: {'moved' if last.moved else 'held'} -> rmspe train "
            f"{last.objective:.3f}, step {max(last.step.values()):.4f}, "
            f"{last.n_fresh} fresh / {last.n_cached} cached -> {out}",
            flush=True,
        )

    ctx = mp.get_context("spawn")
    with ctx.Pool(max(1, args.procs)) as pool:

        def map_fn(fn: Any, points: list[dict[str, float]]) -> list[EvalResult]:
            return pool.map(fn, points, chunksize=1)

        fit = fit_multipliers(
            MULTIPLIERS,
            evaluate,
            grid=args.grid,
            rounds=rounds,
            step=args.step,
            map_fn=map_fn,
            prior=prior,
            on_round=checkpoint,
        )
    _print_rows(fit.log[n_printed:])
    out.write_text(
        json.dumps(_json_safe(artifact(fit, prov, created_at)), indent=1, allow_nan=False)
    )
    test = fit.diagnostics.get("rmspe_test")
    print(
        f"best {' '.join(f'{n}={fit.best[n]:.4f}' for n in MULTIPLIER_NAMES)}: rmspe train "
        f"{fit.objective:.3f}, held-out test "
        f"{'n/a' if test is None or not np.isfinite(test) else f'{test:.3f}'}, inserted "
        f"{fit.diagnostics.get('inserted_fraction', float('nan')):.3f} "
        f"({len(fit.log)} evaluations, {'converged' if fit.converged else 'round cap'})",
        flush=True,
    )
    print(f"-> {out}")
    if args.write_scenario:
        if args.smoke:
            print("--write-scenario ignored with --smoke (a 600 s run is not a fit)")
            return
        raw = apply_multipliers(base_scenario(), fit.best)
        chash = config_hash(ScenarioConfig.model_validate(raw))
        SCENARIO_OUT.write_text(
            scenario_header(fit.best, fit, chash) + yaml.safe_dump(raw, sort_keys=False)
        )
        print(f"-> {SCENARIO_OUT} ({chash})")


if __name__ == "__main__":
    main()
