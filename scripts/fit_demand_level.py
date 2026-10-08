"""Fit a corridor's demand level on link flows under an insertion constraint (B5).

docs/PRE_FRISCO_PROGRAM.md, phase B5. The rule is docs/FRISCO_PROTOCOL.md Amendment 6
(approved by the coordinator on 2026-10-07, before any run) and lives, pure, in
:mod:`calibration.demand_level`: among the grid scales whose mean inserted fraction over
the fit seeds is at least the from-arm battery's realised share minus 0.01, the largest
share of fit-window flow bins under GEH 5; ties to the smaller mean GEH, then the smaller
change from the from-arm's level. No qualifying scale: ``constraint_unmet``, nothing is
chosen, no scenario is written and the process exits 3 (the stage stops). Speed RMSPE is
reported only. Five seeds per scale, the from-arm battery's first five.

Why a new script. ``scripts/i24_fit_demand_scale.py`` minimises segment-speed RMSPE on one
seed per scale; it cannot see vehicles held off the network and twice chose a backlog
(Amendment 2's refits, round p14's s = 1.125 inserting 0.783). That script and its defaults
are untouched (its committed fits re-derive byte for byte, tests/test_scripts/
test_fit_demand_level.py); this one reuses its helpers.

``--corridor i24``: the from-arm is the B2 arm ``scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml``
(909b89f298c5), its base ``scenarios/i24_replica_flow_rc_corrected_dc.yaml`` (219f7db55a74)
scaled by the carried s = 0.925 (``artifacts/demand_scale_i24_flow_dc.json`` best.scale;
checked: the recipe must give the from-arm's hash), its battery
``artifacts/i24_validation_dc_refit_rc.json`` (stage p13; realised 0.967). Each run is the
I-24 fitter's own (``i24_fit_demand_scale._job`` through its ``_run_jobs`` pool, its
``scaled_config``: mainline and on-ramp inflows x s, the base's population). The objective
is the criterion row's (section, 5-min window) bins of 06:30-07:30 against
``hourly_flows_veh_h_recommended`` (``artifacts/i24_validation_observed.json``, the
battery's observed side, checked), scored on the five seeds' mean flow per bin as the
battery's link-flow row scores its replicate mean; 07:30-08:30 is held out and reported.
Grid: 0.6-1.1 by 0.1, then ±2 x 0.025 around the coarse round's constrained choice (50
runs). One more run, the from-arm itself on the first fit seed, must reproduce the from-arm
battery's first replicate (``reproduction``; the readout treats a difference as a problem).
Outputs ``artifacts/demand_level_fit_i24.json`` and, with ``--write-scenario``,
``scenarios/<from-arm stem>_b5.yaml`` named the same (by default
``i24_replica_flow_rc_speedcal_dc_refit_b5``). Another from-arm (the plan's schedule change of
2026-10-07 lets C7b's rule select it) is given by ``--from-scenario``, ``--base-yaml`` and
``--from-battery`` (``--carried-scale`` when its level is not the p4 fit's 0.925); the plan's
expected hashes guard the default files only, and the recipe check (base x carried scale = the
from-arm) and the battery's hash guard every arm.

``--corridor i94``: the from-arm is ``--from-scenario`` (stage p17's arm, selected by D10's
rule; default ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml``) with its battery
``artifacts/validation_<its name>.json`` (scored against the calibration-day observations,
checked). One corridor-wide factor f on the mainline and every on-ramp inflow (the I-24
helper's arithmetic without its 6-decimal rounding, so f = 1 is the from-arm's document
exactly), grid 0.95-1.05 by 0.025, which must lie inside the data-quality artifact's
``parameters.count_error`` (protocol §7.1; refused otherwise). The objective is the
calibration-day station-hours of C1 (hours anchored at the study period's start), pooled
over the seeds as the gate pools them; the validation days are reported as the held-out
reading. Runs go through ``scripts/run_pairs.py`` (the battery's replicate worker) under
``runs/mndot_i94_b5_fit/`` and are scored by the battery's own
``validation.battery.analyse_replicates`` in a memory-planned pool; trajectories are pruned
after scoring, everything else is kept (every archive carries it). A finished run or a
scored run is not repeated on a resumed fit. 25 runs plus the reproduction run. Outputs
``artifacts/demand_level_fit_i94.json`` and, with ``--write-scenario``,
``scenarios/<from-arm stem>_b5.yaml`` named ``<from-arm name>_b5``.

Hash policy. A record written from 2026-10-04 to 2026-10-07 (the from-arm batteries, the plan's
expected hashes 909b89f298c5 / 219f7db55a74) quotes a policy-3 config hash; policy 4 moved every
hash by its version (Amendment 4, W1b and W2 at every weave; I-24 has no weaving section, so its
physics is unchanged). A recorded hash is matched against a document under the current policy or,
when the core provides it, ``flowstate_core.config.config_hash_v3`` (:func:`names_document`);
everything this script writes carries the current policy's hash.

Exit status: 0 a scale was chosen; 3 ``constraint_unmet`` (artifact written, no scenario);
2 refused before any simulation (an input is not what the plan names); 1 a run failed.

Run (repository root; stages ``p15_i24_b5`` and ``p17_i94_b5`` of scripts/gcp/pipeline_i24.sh)::

    uv run --no-sync python scripts/fit_demand_level.py --corridor i24 --procs 8 --write-scenario
    uv run --no-sync python scripts/fit_demand_level.py --corridor i94 --procs 10 --write-scenario \\
        --from-scenario scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import i24_fit_demand_scale as fit24  # the I-24 fitter's helpers; its defaults are not touched

from calibration.demand_level import (
    I24_COARSE,
    I94_GRID,
    INSERTION_TOLERANCE,
    N_SEEDS,
    REFINE_HALF_WIDTH,
    REFINE_STEP,
    RULE_TEXT,
    ObjectiveReading,
    ScaleReading,
    Selection,
    min_inserted_from,
    pooled_objective,
    refine_scales,
    replicate_mean_objective,
    select_scale,
    within_count_error,
)
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "flowstate.demand_level_fit/1"
SPEC = (
    "docs/PRE_FRISCO_PROGRAM.md B5; docs/FRISCO_PROTOCOL.md Amendment 6 (approved by the "
    "coordinator 2026-10-07, before any run)"
)
EXIT_REFUSED = 2
EXIT_CONSTRAINT_UNMET = 3
EXIT_RUN_FAILED = 1
SOURCE_COMMIT_ENV = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE = ".source_commit"

# --- I-24 (stage p15_i24_b5) ------------------------------------------------------------------
I24_BASE = "scenarios/i24_replica_flow_rc_corrected_dc.yaml"
I24_BASE_HASH = "219f7db55a74"
I24_FROM = "scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml"
I24_FROM_HASH = "909b89f298c5"
I24_FROM_BATTERY = "artifacts/i24_validation_dc_refit_rc.json"
#: the fit that set the from-arm's carried level (stage p4_i24_refit; best.scale 0.925)
I24_CARRIED_FIT = "artifacts/demand_scale_i24_flow_dc.json"
I24_OUT = "artifacts/demand_level_fit_i24.json"
I24_SUFFIX = "_b5"
#: the default arm's output (``<from-arm stem>_b5``, as on I-94)
I24_NAME = "i24_replica_flow_rc_speedcal_dc_refit_b5"
I24_SCENARIO_OUT = f"scenarios/{I24_NAME}.yaml"

# --- I-94 (stage p17_i94_b5) ------------------------------------------------------------------
P1A = "artifacts/p1_rehearsal_2026-10-04"
I94_FROM = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml"
I94_OBSERVATIONS = f"{P1A}/observations_calibration.json"
I94_VALIDATION_OBSERVATIONS = f"{P1A}/observations_validation.json"
I94_DATA_QUALITY = f"{P1A}/dq/data_quality.json"
I94_PROFILE = "fhwa_tat3_2004"  # stage p8's battery profile (its detector reads the wave speed)
I94_OUT = "artifacts/demand_level_fit_i94.json"
I94_RUNS = "runs/mndot_i94_b5_fit"
I94_SUFFIX = "_b5"
C3_AGGREGATION_S = 900.0  # protocol §4 C3: 15-minute station speeds (reported only here)

OBJECTIVE_TEXT = {
    "i24": (
        "share of the criterion row's (section, 5-min window) bins of 06:30-07:30 CST (windows "
        "0-11) with GEH < 5, the seeds' mean 5-min crossings x 12 against the observed crossings "
        "divided by the recommended coverage (artifacts/i24_validation_observed.json "
        "hourly_flows_veh_h_recommended), as scripts/i24_validate.py's link-flow row scores a "
        "battery's replicate mean; 07:30-08:30 (windows 12-23) held out"
    ),
    "i94": (
        "share of the calibration-day station-hours (protocol §4 C1: every mainline station, hours "
        "anchored at the study period's start) with GEH < 5, every seed's station-hours pooled as "
        "the baseline gate pools replicates; the validation days are the held-out reading"
    ),
}


class Refused(Exception):
    """An input is not what the plan names; nothing is simulated (exit 2)."""


class RunFailed(Exception):
    """A fit run failed or returned an inconsistent run set (exit 1)."""


# --------------------------------------------------------------------------- shared helpers


def _rel(path: Path | str) -> str:
    p = Path(path).resolve()
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


def _abs(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO / p


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True)


def _json_safe(obj: Any) -> Any:
    """Plain JSON: numpy values as Python ones, non-finite floats as None."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "tolist"):
        return _json_safe(obj.tolist())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _nan_as_none(rows: Any) -> Any:
    """A (nested) list of floats with non-finite values as None (as a battery artifact stores them)."""
    if isinstance(rows, list | tuple):
        return [_nan_as_none(v) for v in rows]
    if rows is None:
        return None
    v = float(rows)
    return v if math.isfinite(v) else None


def source_commit() -> dict[str, str | None]:
    """The source commit (``$FLOWSTATE_SOURCE_COMMIT``, else ``.source_commit``) and this checkout's HEAD.

    On the VM the checkout is a ``git archive`` committed afresh (scripts/gcp/vm_setup.sh), so its
    HEAD names no commit of the repository; the launcher's source commit is the one to cite.
    """
    head = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=30
        )
        head = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    source = os.environ.get(SOURCE_COMMIT_ENV, "").strip() or None
    f = REPO / SOURCE_COMMIT_FILE
    if source is None and f.is_file():
        source = next(iter(f.read_text().split()), None)
    return {"code": source or head, "checkout_head": head}


def scale_label(scale: float) -> str:
    """A scale's run-tree label (``s0.975``)."""
    return f"s{scale:.3f}"


def fit_seeds(battery_seeds: Sequence[int], scenario_seed: int, replicates: int) -> list[int]:
    """The battery's first five seeds, which must be the scenario's own ``spawn_seeds``.

    Raises:
        Refused: The battery ran other seeds than the scenario's first ones, or fewer than five.
    """
    seeds = [int(s) for s in battery_seeds][:N_SEEDS]
    want = spawn_seeds(int(scenario_seed), max(int(replicates), N_SEEDS))[:N_SEEDS]
    if len(seeds) < N_SEEDS or seeds != want:
        raise Refused(
            f"the from-arm battery's first {N_SEEDS} seeds {seeds} are not the scenario's "
            f"spawn_seeds({scenario_seed}, {replicates})[:{N_SEEDS}] = {want}"
        )
    return seeds


def run_selection(
    readings: Sequence[ScaleReading], floor: float, reference: float, seeds: Sequence[int]
) -> Selection:
    return select_scale(readings, min_inserted=floor, reference_scale=reference, seeds=seeds)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=1, allow_nan=False) + "\n")


# --------------------------------------------------------------------------- I-24


@dataclass
class I24Setup:
    """Everything the I-24 fit reads before it simulates, checked."""

    base: Path
    base_hash: str
    population: str
    from_scenario: Path
    from_name: str
    from_hash: str
    carried: float
    carried_source: str
    battery_path: Path
    battery: dict[str, Any]
    realised_mean: float
    floor: float
    seeds: list[int]
    out_name: str
    obs_hourly: np.ndarray
    obs_speeds: np.ndarray


def _hash_of(doc: dict[str, Any]) -> str:
    return config_hash(ScenarioConfig.model_validate(doc))


def _expected(given: str | None, path: Path, default_path: str, default_hash: str) -> str | None:
    """The hash a file must carry: ``given``, else the plan's hash when it is the plan's default file."""
    if given:
        return given
    return default_hash if path.resolve() == _abs(default_path).resolve() else None


def names_document(recorded: str | None, doc: dict[str, Any]) -> str | None:
    """The hash policy under which ``recorded`` is ``doc``'s config hash: ``current``, ``v3`` or None.

    Records written from 2026-10-04 to 2026-10-07 quote policy-3 hashes (module docstring).
    """
    if recorded is None:
        return None
    if recorded == _hash_of(doc):
        return "current"
    import flowstate_core.config as core

    v3 = getattr(core, "config_hash_v3", None)
    if v3 is not None and recorded == v3(doc):
        return "v3"
    return None


def i24_setup(args: argparse.Namespace) -> I24Setup:
    """Read and check the I-24 inputs (module docstring); raises :class:`Refused`."""
    base = _abs(args.base_yaml or I24_BASE)
    base_doc = yaml.safe_load(base.read_text())
    base_hash = _hash_of(base_doc)
    want_base = _expected(args.base_hash, base, I24_BASE, I24_BASE_HASH)
    if want_base is not None and names_document(want_base, base_doc) is None:
        raise Refused(f"{_rel(base)} hashes {base_hash}, not the expected {want_base}")
    population = str(base_doc["fleet"]["idm_calibration"])
    from_path = _abs(args.from_scenario or I24_FROM)
    from_doc = yaml.safe_load(from_path.read_text())
    from_hash = _hash_of(from_doc)
    want_from = _expected(args.from_hash, from_path, I24_FROM, I24_FROM_HASH)
    if want_from is not None and names_document(want_from, from_doc) is None:
        raise Refused(f"{_rel(from_path)} hashes {from_hash}, not the expected {want_from}")
    from_name = str(from_doc["name"])
    if args.carried_scale is not None:
        carried, carried_source = float(args.carried_scale), "--carried-scale"
    else:
        carried = float(json.loads(_abs(I24_CARRIED_FIT).read_text())["best"]["scale"])
        carried_source = f"{I24_CARRIED_FIT} best.scale"
    recipe = _hash_of(fit24.scaled_config(carried, population, "corrected", base, from_name))
    if recipe != from_hash:
        raise Refused(
            f"the base scaled by the carried s = {carried} hashes {recipe}, not the from-arm's "
            f"{from_hash}: the from-arm is not this base's level"
        )
    battery_path = _abs(args.from_battery or I24_FROM_BATTERY)
    battery = json.loads(battery_path.read_text())
    if names_document(battery.get("config_hash"), from_doc) is None:
        raise Refused(
            f"{_rel(battery_path)} ran {battery.get('config_hash')}, not the from-arm ({from_hash})"
        )
    realised = [float(x) for x in battery["simulated"]["demand_realized_fraction"]]
    realised_mean = math.fsum(realised) / len(realised)
    seeds = fit_seeds(battery["seeds"], int(base_doc["seed"]), int(base_doc["replicates"]))
    obs_hourly = fit24.observed_hourly_flows()
    if _canon(_nan_as_none(obs_hourly.tolist())) != _canon(
        _nan_as_none(battery["observed"][fit24.GEH_OBSERVED_KEY])
    ):
        raise Refused(
            f"{_rel(fit24.OBSERVED)} {fit24.GEH_OBSERVED_KEY} is not the from-arm battery's "
            "observed side (the objective and C3 would read different tables)"
        )
    obs_speeds = np.asarray(json.loads(fit24.OBSERVED.read_text())["segment_speeds_ms"], float)
    return I24Setup(
        base=base,
        base_hash=base_hash,
        population=population,
        from_scenario=from_path,
        from_name=from_name,
        from_hash=from_hash,
        carried=carried,
        carried_source=carried_source,
        battery_path=battery_path,
        battery=battery,
        realised_mean=realised_mean,
        floor=min_inserted_from(realised_mean, INSERTION_TOLERANCE),
        seeds=seeds,
        out_name=args.name or f"{from_path.stem}{I24_SUFFIX}",
        obs_hourly=obs_hourly,
        obs_speeds=obs_speeds,
    )


def i24_jobs(setup: I24Setup, scales: Sequence[float]) -> list[tuple[Any, ...]]:
    """The I-24 fitter's job tuples: every scale on every fit seed, under the output name."""
    return [
        (float(s), int(seed), setup.population, "corrected", str(setup.base), setup.out_name)
        for s in scales
        for seed in setup.seeds
    ]


def _flows(counts: Sequence[Sequence[float]], windows: Sequence[int]) -> list[float]:
    """One run's hourly flows over ``windows``, (section, window) bins section by section (x 12)."""
    arr = np.asarray(counts, dtype=float) * (3600.0 / fit24.WINDOW_S)
    return arr[:, list(windows)].ravel().tolist()


def _obs(obs_hourly: np.ndarray, windows: Sequence[int]) -> list[float]:
    return np.asarray(obs_hourly, dtype=float)[:, list(windows)].ravel().tolist()


def i24_scale(
    scale: float, rows: list[dict[str, Any]], setup: I24Setup
) -> tuple[ScaleReading, dict[str, Any]]:
    """One scale's reading and its record from its five runs (seed order)."""
    by_seed = {int(r["seed"]): r for r in rows}
    runs = [by_seed[s] for s in setup.seeds]
    hashes = sorted({r["config_hash"] for r in runs})
    if len(hashes) != 1:
        raise RunFailed(f"scale {scale}: runs of different configurations {hashes}")
    train, test = list(fit24.TRAIN_WINDOWS), list(fit24.TEST_WINDOWS)
    objective = replicate_mean_objective(
        [_flows(r["counts_per_window"], train) for r in runs], _obs(setup.obs_hourly, train)
    )
    held_out = replicate_mean_objective(
        [_flows(r["counts_per_window"], test) for r in runs], _obs(setup.obs_hourly, test)
    )
    with np.errstate(invalid="ignore"):
        seg_mean = np.nanmean(
            np.asarray([np.asarray(r["segment_speeds_ms"], float) for r in runs]), axis=0
        )
    reading = ScaleReading(
        scale=float(scale),
        seeds=tuple(setup.seeds),
        inserted=tuple(float(r["inserted_fraction"]) for r in runs),
        objective=objective,
    )
    record = {
        "scale": round(float(scale), 6),
        "config_hash": hashes[0],
        "mean_inserted": reading.mean_inserted,
        "meets_constraint": bool(reading.mean_inserted >= setup.floor),
        "objective": objective.to_dict(),
        "held_out": held_out.to_dict(),
        "speed_rmspe_reported": {
            "replicate_mean_train": fit24._rmspe_windows(seg_mean, setup.obs_speeds, train),
            "replicate_mean_test": fit24._rmspe_windows(seg_mean, setup.obs_speeds, test),
            "per_seed_train": [r["rmspe_train"] for r in runs],
            "per_seed_test": [r["rmspe_test"] for r in runs],
        },
        "per_seed": [
            {
                **{k: r[k] for k in ("seed", "config_hash", "inserted_fraction", "wall_s")},
                "geh_train": fit24.geh_reading(r["counts_per_window"], setup.obs_hourly, train),
                "geh_test": fit24.geh_reading(r["counts_per_window"], setup.obs_hourly, test),
                "rmspe_train": r["rmspe_train"],
                "rmspe_test": r["rmspe_test"],
                "rmspe_all": r["rmspe_all"],
                "counts_per_window": r["counts_per_window"],
                "segment_speeds_ms": _nan_as_none(r["segment_speeds_ms"]),
            }
            for r in runs
        ],
    }
    return reading, record


def _from_doc_i24(setup: I24Setup) -> dict[str, Any]:
    return dict(yaml.safe_load(setup.from_scenario.read_text()))


def i24_reproduction(row: dict[str, Any] | None, setup: I24Setup) -> dict[str, Any]:
    """The from-arm on the first fit seed against the from-arm battery's first replicate."""
    if row is None:
        return {"run": False, "exact": False, "differs": ["not run"]}
    sim = setup.battery["simulated"]
    i = [int(s) for s in sim["seeds"]].index(int(row["seed"]))
    battery_seg = np.round(np.asarray(sim["segment_speeds_ms_per_replicate"][i], float), 3)
    checks = {
        "config_hash": row["config_hash"] == setup.from_hash
        and names_document(setup.battery["config_hash"], _from_doc_i24(setup)) is not None,
        "counts_per_window": _canon(row["counts_per_window"])
        == _canon(sim["counts_per_replicate"][i]),
        "inserted_fraction": float(row["inserted_fraction"])
        == float(sim["demand_realized_fraction"][i]),
        "segment_speeds_ms": _canon(_nan_as_none(row["segment_speeds_ms"]))
        == _canon(_nan_as_none(battery_seg.tolist())),
    }
    differs = [k for k, ok in checks.items() if not ok]
    return {
        "run": True,
        "what": "the from-arm configuration (base x the carried scale, the from-arm's name) on the "
        "first fit seed, against the from-arm battery's replicate of that seed: the code tree "
        "reproduces the battery the criteria compare with",
        "seed": int(row["seed"]),
        "config_hash": row["config_hash"],
        "inserted_fraction": row["inserted_fraction"],
        "checks": checks,
        "exact": not differs,
        "differs": differs,
    }


def i24_fit(args: argparse.Namespace) -> int:
    setup = i24_setup(args)
    print(
        f"i24: from-arm {_rel(setup.from_scenario)} ({setup.from_hash}, carried s = {setup.carried:g});"
        f" battery realised {setup.realised_mean:.4f} -> constraint >= {setup.floor:.4f}; seeds "
        f"{setup.seeds}",
        flush=True,
    )
    repro_job = (
        setup.carried,
        setup.seeds[0],
        setup.population,
        "corrected",
        str(setup.base),
        setup.from_name,
    )
    coarse = list(I24_COARSE)
    rows = fit24._run_jobs([*i24_jobs(setup, coarse), repro_job], args.procs)
    repro_row, rows = rows[-1], rows[:-1]
    by_scale = _group(rows)
    readings = {s: i24_scale(s, by_scale[s], setup) for s in coarse}
    coarse_sel = run_selection(
        [r for r, _ in readings.values()], setup.floor, setup.carried, setup.seeds
    )
    _print_round("coarse", readings, coarse_sel)
    refine: list[float] = []
    final = coarse_sel
    if not coarse_sel.constraint_unmet:
        assert coarse_sel.chosen_scale is not None
        refine = [s for s in refine_scales(coarse_sel.chosen_scale) if _key(s) not in _keys(coarse)]
        more = fit24._run_jobs(i24_jobs(setup, refine), args.procs)
        by_scale.update(_group(more))
        readings.update({s: i24_scale(s, by_scale[s], setup) for s in refine})
        final = run_selection(
            [r for r, _ in readings.values()], setup.floor, setup.carried, setup.seeds
        )
        _print_round("coarse + refine", readings, final)
    repro = i24_reproduction(repro_row, setup)
    print(
        f"reproduction of the from-arm battery's seed {setup.seeds[0]}: "
        + ("exact" if repro["exact"] else f"DIFFERS ({', '.join(repro['differs'])})"),
        flush=True,
    )
    payload = {
        "schema": SCHEMA,
        "created_at": _now(),
        "spec": SPEC,
        **source_commit(),
        "versions": _versions(),
        "corridor": "i24",
        "rule": RULE_TEXT,
        "objective": {"estimator": "replicate_mean", "definition": OBJECTIVE_TEXT["i24"]},
        "inserted_fraction_definition": fit24.INSERTED_DEFINITION.replace(
            "the fit's single seed", "each fit seed"
        ),
        "from_arm": {
            "scenario": _rel(setup.from_scenario),
            "sha256": _sha(setup.from_scenario),
            "name": setup.from_name,
            "config_hash": setup.from_hash,
            "battery": {
                "path": _rel(setup.battery_path),
                "sha256": _sha(setup.battery_path),
                "realised_mean": setup.realised_mean,
                "n_replicates": len(setup.battery["simulated"]["demand_realized_fraction"]),
            },
        },
        "base": {
            "scenario": _rel(setup.base),
            "sha256": _sha(setup.base),
            "config_hash": setup.base_hash,
            "population": setup.population,
        },
        "reference_scale": setup.carried,
        "reference_scale_source": setup.carried_source,
        "recipe_check": {
            "what": "the base scaled by the reference scale under the from-arm's name is the from-arm's "
            "configuration (checked before any run; the fit refuses otherwise)",
            "ok": True,
        },
        "min_inserted": setup.floor,
        "insertion_tolerance": INSERTION_TOLERANCE,
        "seeds": setup.seeds,
        "observed": {"path": _rel(fit24.OBSERVED), "key": fit24.GEH_OBSERVED_KEY},
        "grid": {
            "coarse": coarse,
            "refine_step": REFINE_STEP,
            "refine_half_width": REFINE_HALF_WIDTH,
            "coarse_choice": coarse_sel.chosen_scale,
            "refine": refine,
            "n_runs": sum(len(v) for v in by_scale.values()) + 1,
        },
        "per_scale": [readings[s][1] for s in sorted(readings)],
        "coarse_selection": coarse_sel.to_dict(),
        "selection": final.to_dict(),
        "constraint_unmet": final.constraint_unmet,
        "chosen": None,
        "reproduction": repro,
        "scenario_out": None,
    }
    out = _abs(args.out or I24_OUT)
    if final.constraint_unmet:
        write_json(out, payload)
        return _unmet(final, out)
    chosen = final.chosen_scale
    assert chosen is not None
    record = readings[_match(readings, chosen)][1]
    payload["chosen"] = {
        k: record[k] for k in ("scale", "config_hash", "mean_inserted", "objective", "held_out")
    }
    doc = fit24.scaled_config(chosen, setup.population, "corrected", setup.base, setup.out_name)
    if args.write_scenario:
        scn = _abs(
            args.scenario_out
            or setup.from_scenario.with_name(f"{setup.from_scenario.stem}{I24_SUFFIX}.yaml")
        )
        h = write_scenario_i24(scn, doc, chosen, setup, final, out)
        payload["scenario_out"] = {"path": _rel(scn), "name": setup.out_name, "config_hash": h}
    write_json(out, payload)
    print(f"selection: {final.reason}\n-> {_rel(out)}", flush=True)
    return 0


def write_scenario_i24(
    path: Path, doc: dict[str, Any], scale: float, setup: I24Setup, sel: Selection, fit_out: Path
) -> str:
    """The base scaled by the chosen s (``scaled_config``), with its provenance header."""
    h = _hash_of(doc)
    header = (
        f"# {setup.out_name} — B5's demand level on the rc family (docs/PRE_FRISCO_PROGRAM.md B5;\n"
        "#   docs/FRISCO_PROTOCOL.md Amendment 6; PROPOSED, not adopted): scripts/i24_fit_demand_scale.py's\n"
        f"#   scaled_config at s = {scale:g} on {_rel(setup.base)} ({setup.base_hash}): mainline and on-ramp\n"
        f"#   inflows x s, exit fractions and the boundary as built, fleet {setup.population}.\n"
        f"#   Fitted by scripts/fit_demand_level.py --corridor i24 ({_rel(fit_out)}) on link flows,\n"
        "#   06:30-07:30 CST (07:30-08:30 held out), five seeds per scale, under the insertion\n"
        f"#   constraint >= {sel.min_inserted:.4f} (from-arm {setup.from_name}, {setup.from_hash}, at\n"
        f"#   s = {setup.carried:g}). Selection: {sel.reason}.\n"
        f"#   config hash {h}; seeded=False.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False))
    print(f"-> {_rel(path)} ({h})", flush=True)
    return h


# --------------------------------------------------------------------------- I-94


@dataclass
class I94Setup:
    """Everything the I-94 fit reads before it simulates, checked."""

    from_scenario: Path
    from_doc: dict[str, Any]
    from_name: str
    from_hash: str
    battery_path: Path
    battery: dict[str, Any]
    realised_mean: float
    floor: float
    seeds: list[int]
    count_error: float
    grid: list[float]
    out_name: str
    observations: Path
    validation_observations: Path
    runs_root: Path


def i94_document(from_doc: dict[str, Any], factor: float, name: str) -> dict[str, Any]:
    """The from-arm's document with ``network.inflow`` and every on-ramp inflow x ``factor``.

    The arithmetic of ``i24_fit_demand_scale.scaled_config`` without its 6-decimal rounding
    (factor 1 returns the from-arm's values exactly) and without its fleet override (the
    corridor keeps its own population); exit fractions, the boundary and everything else
    unchanged.
    """
    doc = copy.deepcopy(from_doc)
    net = doc["network"]
    net["inflow"] = [[t, q * factor] for t, q in net["inflow"]]
    for ramp in net.get("ramps", []):
        if ramp.get("kind") == "on" and ramp.get("inflow"):
            ramp["inflow"] = [[t, q * factor] for t, q in ramp["inflow"]]
    doc["name"] = name
    return doc


def i94_setup(args: argparse.Namespace) -> I94Setup:
    """Read and check the I-94 inputs (module docstring); raises :class:`Refused`."""
    from_path = _abs(args.from_scenario or I94_FROM)
    from_doc = yaml.safe_load(from_path.read_text())
    from_hash = _hash_of(from_doc)
    if args.from_hash and names_document(args.from_hash, from_doc) is None:
        raise Refused(f"{_rel(from_path)} hashes {from_hash}, not the expected {args.from_hash}")
    from_name = str(from_doc["name"])
    battery_path = _abs(args.from_battery or f"artifacts/validation_{from_name}.json")
    if not battery_path.is_file():
        raise Refused(f"no from-arm battery {_rel(battery_path)}")
    battery = json.loads(battery_path.read_text())
    if names_document(battery.get("config_hash"), from_doc) is None:
        raise Refused(
            f"{_rel(battery_path)} ran {battery.get('config_hash')}, not the from-arm ({from_hash})"
        )
    observations = _abs(args.observations or I94_OBSERVATIONS)
    scored = (battery.get("observations") or {}).get("path")
    if scored is None or _abs(scored).resolve() != observations.resolve():
        raise Refused(
            f"{_rel(battery_path)} was scored against {scored}, not the calibration days "
            f"{_rel(observations)}"
        )
    realised_mean = float(battery["insertion"]["mean_departed_fraction"])
    seeds = fit_seeds(battery["seeds"], int(from_doc["seed"]), int(from_doc["replicates"]))
    dq = json.loads(_abs(args.data_quality or I94_DATA_QUALITY).read_text())
    count_error = float(dq["parameters"]["count_error"])
    grid = list(I94_GRID)
    if not within_count_error(grid, count_error):
        raise Refused(f"the grid {grid} leaves the count uncertainty ±{count_error}")
    return I94Setup(
        from_scenario=from_path,
        from_doc=from_doc,
        from_name=from_name,
        from_hash=from_hash,
        battery_path=battery_path,
        battery=battery,
        realised_mean=realised_mean,
        floor=min_inserted_from(realised_mean, INSERTION_TOLERANCE),
        seeds=seeds,
        count_error=count_error,
        grid=grid,
        out_name=args.name or f"{from_name}{I94_SUFFIX}",
        observations=observations,
        validation_observations=_abs(args.validation_observations or I94_VALIDATION_OBSERVATIONS),
        runs_root=_abs(args.runs_root or I94_RUNS),
    )


@dataclass
class I94Run:
    """One I-94 fit run, as the fit reads it."""

    label: str
    seed: int
    run_dir: Path
    config_hash: str
    planned: int
    departed: int
    arrived: int | None
    inserted: float
    n_collisions: int | None
    locked: bool | None
    cal_pairs: list[tuple[float, float]]
    val_pairs: list[tuple[float, float]]
    link_hours: list[dict[str, Any]]
    rmspe_5min: float
    rmspe_15min_point: float
    wave_speed_kmh: float


def i94_simulate(pairs: Sequence[Any], runs_root: Path, procs: int) -> None:
    """Run the pairs not yet complete (``scripts/run_pairs.py``); raises on a failed run."""
    from run_pairs import run_pairs

    from microsim.runner import is_run_complete

    todo = []
    for p in pairs:
        cfg = ScenarioConfig.model_validate(yaml.safe_load(p.scenario.read_text()))
        if is_run_complete(runs_root / p.label / config_hash(cfg) / str(p.seed)):
            print(f"{p.label} seed {p.seed}: done earlier, not repeated", flush=True)
            continue
        todo.append(p)
    if not todo:
        return
    status, manifest = run_pairs(todo, runs_root, procs=procs)
    if status != 0:
        failed = [f"{r['label']}:{r['seed']}" for r in manifest["pairs"] if r["status"] != "ok"]
        raise RunFailed(f"fit runs failed: {', '.join(failed)}")


def i94_score(dirs: Sequence[Path], args: argparse.Namespace, setup: I94Setup) -> list[I94Run]:
    """Score every run with the battery's own path (stored scores are re-read, not recomputed)."""
    from corridor_battery import choose_score_procs, prune_trajectories

    from microsim.demand_adapter import corridor_x_offset_m
    from validation.baseline_gate import aggregated_rmspe, rescore
    from validation.battery import (
        METRICS_FILE,
        SCORES_FILE,
        analyse_replicates,
        collision_count,
        load_meta,
        load_replicate_analysis,
    )
    from validation.criteria import get_profile
    from validation.observed import ObservedCorridor

    observed = ObservedCorridor.from_json(setup.observations)
    validation_days = ObservedCorridor.from_json(setup.validation_observations)
    cfg = ScenarioConfig.model_validate(setup.from_doc)
    x_offset = corridor_x_offset_m(cfg)
    x_refs = observed.mainline_x_refs()
    span = (x_refs[0] + x_offset, x_refs[-1] + x_offset)
    x_ref = x_refs[len(x_refs) // 2] + x_offset
    stored = [d for d in dirs if (d / METRICS_FILE).is_file() and (d / SCORES_FILE).is_file()]
    fresh = [d for d in dirs if d not in stored]
    analyses: dict[Path, Any] = {d: load_replicate_analysis(d) for d in stored}
    if fresh:
        procs = (
            args.score_procs
            if args.score_procs is not None
            else choose_score_procs(None, max(1, args.procs), fresh)
        )
        done = analyse_replicates(
            fresh,
            observed,
            profile=get_profile(I94_PROFILE),
            x_ref=x_ref,
            span=span,
            x_offset_m=x_offset,
            n_procs=procs,
        )
        analyses.update(dict(zip(fresh, done, strict=True)))
        deleted = prune_trajectories(fresh, keep_first=False)
        print(f"pruned {deleted} fit trajectory file(s); every other run file is kept", flush=True)
    out = []
    for d in dirs:
        a = analyses[d]
        meta = load_meta(d)
        scores = a.scores
        anchored = scores.link_hours_anchored or ()
        val = rescore(scores, scored_against=observed, target=validation_days).scores
        r15, _ = aggregated_rmspe(
            scores,
            window_s=observed.window_s,
            aggregation_s=C3_AGGREGATION_S,
            source="point" if scores.station_point_speeds_sim is not None else "segment",
        )
        out.append(
            I94Run(
                label=d.parent.parent.name,
                seed=int(d.name),
                run_dir=d,
                config_hash=str(meta.get("config_hash")),
                planned=int(a.insertion.planned),
                departed=int(a.insertion.departed),
                arrived=a.insertion.arrived,
                inserted=float(a.insertion.departed_fraction),
                n_collisions=collision_count(meta),
                locked=None if a.locks is None else a.locks.locked,
                cal_pairs=[(r.sim_veh_h, r.obs_veh_h) for r in anchored],
                val_pairs=[(r.sim_veh_h, r.obs_veh_h) for r in (val.link_hours_anchored or ())],
                link_hours=[
                    {
                        "station": r.station,
                        "window_start_s": r.window_start_s,
                        "sim_veh_h": r.sim_veh_h,
                    }
                    for r in (scores.link_hours or ())
                ],
                rmspe_5min=float(scores.rmspe),
                rmspe_15min_point=float(r15),
                wave_speed_kmh=float(a.wave_speed_kmh),
            )
        )
    return out


def i94_scale(
    scale: float, runs: list[I94Run], setup: I94Setup
) -> tuple[ScaleReading, dict[str, Any]]:
    by_seed = {r.seed: r for r in runs}
    rs = [by_seed[s] for s in setup.seeds]
    hashes = sorted({r.config_hash for r in rs})
    if len(hashes) != 1:
        raise RunFailed(f"factor {scale}: runs of different configurations {hashes}")
    objective = pooled_objective([r.cal_pairs for r in rs])
    held_out = pooled_objective([r.val_pairs for r in rs])
    reading = ScaleReading(
        scale=float(scale),
        seeds=tuple(setup.seeds),
        inserted=tuple(r.inserted for r in rs),
        objective=objective,
    )
    record = {
        "scale": round(float(scale), 6),
        "config_hash": hashes[0],
        "mean_inserted": reading.mean_inserted,
        "meets_constraint": bool(reading.mean_inserted >= setup.floor),
        "objective": objective.to_dict(),
        "held_out": {**held_out.to_dict(), "what": "validation-day station-hours (reported)"},
        "speed_rmspe_reported": {
            "rmspe_5min_segment_mean": _mean([r.rmspe_5min for r in rs]),
            "rmspe_15min_point_mean": _mean([r.rmspe_15min_point for r in rs]),
        },
        "collisions": [r.n_collisions for r in rs],
        "locked": [r.locked for r in rs],
        "per_seed": [_i94_run_record(r) for r in rs],
    }
    return reading, record


def _i94_run_record(r: I94Run) -> dict[str, Any]:
    cal = pooled_objective([r.cal_pairs])
    return {
        "seed": r.seed,
        "run_dir": _rel(r.run_dir),
        "config_hash": r.config_hash,
        "planned": r.planned,
        "departed": r.departed,
        "arrived": r.arrived,
        "inserted_fraction": r.inserted,
        "n_collisions": r.n_collisions,
        "locked": r.locked,
        "geh_calibration_days": cal.to_dict(),
        "geh_validation_days": pooled_objective([r.val_pairs]).to_dict(),
        "rmspe_5min_segment": r.rmspe_5min,
        "rmspe_15min_point": r.rmspe_15min_point,
        "wave_speed_kmh": r.wave_speed_kmh,
        "calibration_station_hours": [[m, c] for m, c in r.cal_pairs],
    }


def i94_reproduction(run: I94Run | None, setup: I94Setup) -> dict[str, Any]:
    """The from-arm on the first fit seed against the from-arm battery's replicate of that seed."""
    if run is None:
        return {"run": False, "exact": False, "differs": ["not run"]}
    rows = {int(p["seed"]): p for p in setup.battery["per_seed"]}
    ps = rows.get(run.seed)
    if ps is None:
        return {"run": True, "exact": False, "differs": ["seed not in the battery"]}
    ins = ps.get("insertion") or {}

    def hours(lh: Sequence[dict[str, Any]]) -> list[list[Any]]:
        return [[h["station"], float(h["window_start_s"]), float(h["sim_veh_h"])] for h in lh]

    checks = {
        "config_hash": run.config_hash == setup.from_hash
        and names_document(setup.battery["config_hash"], setup.from_doc) is not None,
        "insertion": (run.planned, run.departed, run.arrived)
        == (ins.get("planned"), ins.get("departed"), ins.get("arrived")),
        "n_collisions": run.n_collisions == ps.get("n_collisions"),
        "link_hour_volumes": _canon(hours(run.link_hours))
        == _canon(hours(ps.get("link_hours") or [])),
    }
    differs = [k for k, ok in checks.items() if not ok]
    return {
        "run": True,
        "what": "the from-arm scenario file on the first fit seed, against the from-arm battery's "
        "per_seed row of that seed (simulation-side quantities): the code tree reproduces the "
        "battery the criteria compare with",
        "seed": run.seed,
        "config_hash": run.config_hash,
        "checks": checks,
        "exact": not differs,
        "differs": differs,
        "reported": {"rmspe_equal": run.rmspe_5min == ps.get("rmspe")},
    }


def i94_fit(args: argparse.Namespace) -> int:
    from run_pairs import Pair

    setup = i94_setup(args)
    print(
        f"i94: from-arm {_rel(setup.from_scenario)} ({setup.from_hash}); battery realised "
        f"{setup.realised_mean:.4f} -> constraint >= {setup.floor:.4f}; grid {setup.grid} inside "
        f"±{setup.count_error}; seeds {setup.seeds}",
        flush=True,
    )
    scn_dir = setup.runs_root / "scenarios"
    scn_dir.mkdir(parents=True, exist_ok=True)
    pairs = []
    hashes: dict[float, str] = {}
    for f in setup.grid:
        doc = i94_document(setup.from_doc, f, setup.out_name)
        hashes[f] = _hash_of(doc)
        path = scn_dir / f"{scale_label(f)}.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        pairs += [Pair(label=scale_label(f), scenario=path, seed=s) for s in setup.seeds]
    pairs.append(Pair(label="from_arm", scenario=setup.from_scenario, seed=setup.seeds[0]))
    i94_simulate(pairs, setup.runs_root, args.procs)
    dirs = [
        setup.runs_root
        / p.label
        / (hashes[_grid_scale(p.label, setup.grid)] if p.label != "from_arm" else setup.from_hash)
        / str(p.seed)
        for p in pairs
    ]
    runs = i94_score(dirs, args, setup)
    repro_run = next((r for r in runs if r.label == "from_arm"), None)
    readings = {}
    for f in setup.grid:
        readings[f] = i94_scale(f, [r for r in runs if r.label == scale_label(f)], setup)
    sel = run_selection([r for r, _ in readings.values()], setup.floor, 1.0, setup.seeds)
    _print_round("grid", readings, sel)
    repro = i94_reproduction(repro_run, setup)
    print(
        f"reproduction of the from-arm battery's seed {setup.seeds[0]}: "
        + ("exact" if repro["exact"] else f"DIFFERS ({', '.join(repro['differs'])})"),
        flush=True,
    )
    payload = {
        "schema": SCHEMA,
        "created_at": _now(),
        "spec": SPEC,
        **source_commit(),
        "versions": _versions(),
        "corridor": "i94",
        "rule": RULE_TEXT,
        "objective": {"estimator": "pooled", "definition": OBJECTIVE_TEXT["i94"]},
        "inserted_fraction_definition": "n_vehicles_departed / n_vehicles_planned of each fit run "
        "(validation.battery.insertion_stats departed_fraction; the battery's "
        "insertion.mean_departed_fraction is its mean over replicates)",
        "from_arm": {
            "scenario": _rel(setup.from_scenario),
            "sha256": _sha(setup.from_scenario),
            "name": setup.from_name,
            "config_hash": setup.from_hash,
            "battery": {
                "path": _rel(setup.battery_path),
                "sha256": _sha(setup.battery_path),
                "realised_mean": setup.realised_mean,
                "n_replicates": len(setup.battery["seeds"]),
            },
        },
        "scaling": "network.inflow and every on-ramp inflow x f (exit fractions, boundary and "
        "everything else unchanged; no rounding, so f = 1 is the from-arm's document)",
        "reference_scale": 1.0,
        "min_inserted": setup.floor,
        "insertion_tolerance": INSERTION_TOLERANCE,
        "seeds": setup.seeds,
        "observed": {
            "calibration_days": _rel(setup.observations),
            "validation_days": _rel(setup.validation_observations),
        },
        "grid": {
            "scales": setup.grid,
            "count_error": setup.count_error,
            "count_error_source": f"{_rel(_abs(args.data_quality or I94_DATA_QUALITY))} "
            "parameters.count_error",
            "n_runs": len(pairs),
            "runs_root": _rel(setup.runs_root),
        },
        "per_scale": [readings[f][1] for f in sorted(readings)],
        "selection": sel.to_dict(),
        "constraint_unmet": sel.constraint_unmet,
        "chosen": None,
        "reproduction": repro,
        "scenario_out": None,
    }
    out = _abs(args.out or I94_OUT)
    if sel.constraint_unmet:
        write_json(out, payload)
        return _unmet(sel, out)
    chosen = sel.chosen_scale
    assert chosen is not None
    record = readings[_match(readings, chosen)][1]
    payload["chosen"] = {
        k: record[k] for k in ("scale", "config_hash", "mean_inserted", "objective", "held_out")
    }
    if args.write_scenario:
        scn = _abs(
            args.scenario_out
            or setup.from_scenario.with_name(f"{setup.from_scenario.stem}{I94_SUFFIX}.yaml")
        )
        doc = i94_document(setup.from_doc, chosen, setup.out_name)
        h = write_scenario_i94(scn, doc, chosen, setup, sel, out)
        payload["scenario_out"] = {"path": _rel(scn), "name": setup.out_name, "config_hash": h}
    write_json(out, payload)
    print(f"selection: {sel.reason}\n-> {_rel(out)}", flush=True)
    return 0


def write_scenario_i94(
    path: Path, doc: dict[str, Any], factor: float, setup: I94Setup, sel: Selection, fit_out: Path
) -> str:
    h = _hash_of(doc)
    header = (
        f"# {setup.out_name} — B5's demand level on {setup.from_name} (docs/PRE_FRISCO_PROGRAM.md B5;\n"
        "#   docs/FRISCO_PROTOCOL.md Amendment 6; PROPOSED, not adopted): "
        f"{_rel(setup.from_scenario)} ({setup.from_hash})\n"
        f"#   with network.inflow and every on-ramp inflow x f = {factor:g} (one corridor-wide factor\n"
        f"#   inside the count uncertainty ±{setup.count_error:g}); exit fractions, the boundary and\n"
        "#   everything else unchanged. Fitted by scripts/fit_demand_level.py --corridor i94\n"
        f"#   ({_rel(fit_out)}) on the calibration-day station-hours, five seeds per factor, under\n"
        f"#   the insertion constraint >= {sel.min_inserted:.4f}. Selection: {sel.reason}.\n"
        f"#   config hash {h}; seeded=False.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False))
    print(f"-> {_rel(path)} ({h})", flush=True)
    return h


# --------------------------------------------------------------------------- small helpers


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(s: float) -> float:
    return round(float(s), 6)


def _keys(scales: Sequence[float]) -> set[float]:
    return {_key(s) for s in scales}


def _group(rows: Sequence[dict[str, Any]]) -> dict[float, list[dict[str, Any]]]:
    out: dict[float, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(_key(r["scale"]), []).append(r)
    return out


def _match(readings: dict[float, Any], scale: float) -> float:
    return next(s for s in readings if _key(s) == _key(scale))


def _grid_scale(label: str, grid: Sequence[float]) -> float:
    return next(f for f in grid if scale_label(f) == label)


def _mean(values: Sequence[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return math.fsum(vals) / len(vals) if vals else None


def _print_round(
    what: str, readings: dict[float, tuple[ScaleReading, dict[str, Any]]], sel: Selection
) -> None:
    print(f"{what}:", flush=True)
    for s in sorted(readings):
        r, _ = readings[s]
        o: ObjectiveReading = r.objective
        mark = " <-" if sel.chosen_scale is not None and _key(s) == sel.chosen_scale else ""
        print(
            f"  s={s:.3f} inserted={r.mean_inserted:.4f} "
            f"{'ok ' if r.mean_inserted >= sel.min_inserted else 'LOW'} "
            f"geh<5 {o.n_under}/{o.n_bins} ({float(o.share):.3f}) mean {o.mean_geh:.3f}{mark}",
            flush=True,
        )
    print(f"  {sel.reason}", flush=True)


def _unmet(sel: Selection, out: Path) -> int:
    print(f"{sel.reason}\n-> {_rel(out)} (no scenario written)", flush=True)
    print("CONSTRAINT UNMET: the fit stops (docs/PRE_FRISCO_PROGRAM.md B5, Stop)", file=sys.stderr)
    return EXIT_CONSTRAINT_UNMET


# --------------------------------------------------------------------------- CLI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--corridor", choices=("i24", "i94"), required=True)
    ap.add_argument("--procs", type=int, default=4, help="simulation processes")
    ap.add_argument(
        "--score-procs", type=int, default=None, help="i94: scoring pool (default: memory-planned)"
    )
    ap.add_argument("--from-scenario", type=Path, default=None, help="the from-arm scenario file")
    ap.add_argument("--from-hash", default=None, help="the from-arm's expected config hash")
    ap.add_argument(
        "--from-battery", type=Path, default=None, help="the from-arm's battery artifact"
    )
    ap.add_argument("--base-yaml", type=Path, default=None, help="i24: the base the level scales")
    ap.add_argument("--base-hash", default=None, help="i24: the base's expected config hash")
    ap.add_argument("--carried-scale", type=float, default=None, help="i24: the from-arm's level")
    ap.add_argument(
        "--observations", type=Path, default=None, help="i94: calibration-day observations"
    )
    ap.add_argument(
        "--validation-observations", type=Path, default=None, help="i94: validation days"
    )
    ap.add_argument("--data-quality", type=Path, default=None, help="i94: the count_error source")
    ap.add_argument("--runs-root", type=Path, default=None, help="i94: the fit runs' tree")
    ap.add_argument("--out", type=Path, default=None, help="the fit artifact")
    ap.add_argument("--write-scenario", action="store_true")
    ap.add_argument("--scenario-out", type=Path, default=None)
    ap.add_argument("--name", default=None, help="the written scenario's name")
    args = ap.parse_args(argv)
    if args.procs < 1:
        ap.error("--procs must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return i24_fit(args) if args.corridor == "i24" else i94_fit(args)
    except Refused as exc:
        print(f"REFUSED: {exc}; nothing simulated", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    except RunFailed as exc:
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        return EXIT_RUN_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
