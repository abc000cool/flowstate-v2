"""Regenerate an auto-report from a committed battery artifact and one figure replicate (stage p22_reports).

E13 (docs/PRE_FRISCO_PROGRAM.md) regenerates the program's auto-reports on the cloud, because the
speed-contour figures need trajectories and a battery keeps at most its first seed's. A 20-seed
re-run is not needed for that and is not made: every table of a report written here is read from
the committed battery artifact, and only the figures come from ONE replicate re-run with its
trajectories, the kept seed (6914975401685141156, the first of ``spawn_seeds(42, 20)``, the seed
every corridor battery keeps).

**What comes from where.**

* From the committed battery artifact (all of its replicates): the acceptance-criteria rows as the
  battery scored them, the metric table with its replicate intervals (``metrics_ci``), the model
  integrity blocks (collisions, locks, insertion, weave exits and releases, waiting), the observed
  data block, the baseline gate when the artifact carries one, the speed criterion by time
  aggregation when it carries the segment matrices, the artifact's notes, the versions and seeds;
  for the ring, its ``ring`` block (thresholds, per-check intervals, the two criteria rows).
* From the figure replicate (one seed, re-run by the stage): the speed-contour figures (the
  product renderer's own contour, :func:`validation.report._render_contour`, and for the ring the
  baseline-versus-controller pair, :func:`validation.report._render_contour_pair`), an observed
  versus simulated field for I-24 arms when the recording is present and its data hash is the
  battery's (``--i24-observed``), and the calibration artifacts its metadata names (the
  configuration fixes them, so they are the battery's). Whether the replicate reproduces the
  battery's record of the same seed is checked field by field (corridor batteries: its insertion,
  collision and weave-release counters; I-24 batteries: its per-ramp counters, realised demand and
  collisions; the ring: every check value) and stated under Provenance, never assumed:
  ``identical`` bit for bit after the artifact's JSON round trip, or ``reproduces`` with
  floating-point values within :data:`REPRO_REL_TOL` (SUMO's state can differ in the last digits
  between platforms), else it does not reproduce and the report says so twice.

Every report says so under Provenance in one line, ``figures from replicate <seed>; tables from
<artifact>``. :mod:`validation.report` is not changed: its callers' reports are byte-identical, and
the tables shared with it are formatted by its own helpers (criteria rows, metric rows, the lock
and weave-release lines, the observed-data rows), so the two cannot drift.

**Subcommands.**

* ``plan --arm LABEL:SCENARIO:BATTERY ... --seed S --out runs/p22``: checks every arm before
  anything is simulated and writes ``PLAN.json`` and ``pairs.txt`` (the ``scripts/run_pairs.py``
  arguments, one token per line, each corridor arm pinned to its scenario's hash today). An empty
  SCENARIO takes the battery's own record of it (``scenario_file.path`` of an I-24 battery,
  ``scenario`` of a corridor battery); SCENARIO ``ring_sugiyama`` makes the arm the ring benchmark,
  read from the battery's ``ring`` block. An arm whose battery or scenario is missing is
  **skipped**; an arm whose scenario does not hash as its battery recorded (today's policy or
  policy 3), whose battery does not hold the seed, whose scenario file differs from the one an
  I-24 battery recorded (sha256), or whose report directory holds files this script did not write
  is **refused**. One line per arm, ``p22: <label> planned|skipped|refused: ...``; exit 1 when an
  arm is refused, else 0.
* ``ring-run --plan runs/p22/PLAN.json``: the planned ring arm's seed through
  :func:`validation.ring_benchmark.evaluate_ring_benchmark` (the battery's own function: the ring as
  shipped and with one FollowerStopper vehicle), writing ``<out>/<label>/ring_benchmark.json``.
* ``render --plan runs/p22/PLAN.json --record artifacts/p22_reports.json``: one report per planned
  arm, ``<reports-root>/<label>/report.md`` with ``figures/*.png`` (each PNG at most
  :data:`MAX_FIGURE_BYTES`, palette-quantised and then shrunk when the renderer's file is larger)
  and ``report.pdf`` when fpdf2 is importable, then the record (schema
  :data:`RECORD_SCHEMA`): every arm with its status (``written``, ``failed``, or the plan's
  ``skipped`` / ``refused``), inputs (paths and sha256), config hashes, figure run, reproduction
  check, outputs and sizes, plus the seed, the source commit and ``created_at``. The record is
  rewritten after every arm (``complete`` false until the last), so a stage cut short keeps the
  reports it finished. Exit 1 when a planned arm could not be rendered.

Usage (repo root; stage p22_reports in scripts/gcp/pipeline_i24.sh)::

    uv run --no-sync python scripts/regenerate_report.py plan --seed 6914975401685141156 \\
        --out runs/p22 --arm e13_i24_b2:scenarios/X.yaml:artifacts/i24_validation_Y.json
    uv run --no-sync python scripts/run_pairs.py --out runs/p22 --procs 14 $(cat runs/p22/pairs.txt)
    uv run --no-sync python scripts/regenerate_report.py ring-run --plan runs/p22/PLAN.json
    uv run --no-sync python scripts/regenerate_report.py render --plan runs/p22/PLAN.json \\
        --record artifacts/p22_reports.json --i24-observed
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from jinja2 import Environment, StrictUndefined

from flowstate_core.config import CONFIG_HASH_VERSION, ScenarioConfig, config_hash, config_hash_v3
from flowstate_core.units import ms_to_kmh
from validation.battery import (
    collision_count,
    insertion_stats,
    json_safe,
    records_insertion,
    weave_release_rows,
)
from validation.criteria import CriteriaResult, get_profile
from validation.fields import speed_field
from validation.metrics import CI, CI_LEVEL, MIN_REPLICATES
from validation.observed import DetectorWaveSpeed, ObservedProvenance
from validation.report import (
    FUEL_LIMITATION,
    FUEL_NOTE,
    OBSERVED_NOTE,
    _criteria_rows,
    _fmt,
    _lock_context,
    _metric_rows,
    _observed_rows,
    _position_text,
    _render_contour,
    _render_contour_pair,
    _RunInfo,
    _weave_release_context,
    speed_aggregation_rows,
)
from validation.ring_benchmark import (
    DAMPENING_CONTROLLER,
    RING_SCENARIO,
    SINGLE_AV_PENETRATION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: This script, as every report it writes names it (the marker a re-run recognises).
GENERATOR = "scripts/regenerate_report.py"
#: The stage that runs it.
STAGE = "p22_reports"
#: Schema of the record ``render`` writes (``artifacts/p22_reports.json``).
RECORD_SCHEMA = "flowstate.p22_reports/1"
#: Schema of ``PLAN.json``.
PLAN_SCHEMA = "flowstate.p22_plan/1"
#: The kept seed: the first of ``spawn_seeds(42, 20)``, the seed a corridor battery keeps.
KEPT_SEED = 6914975401685141156
#: Largest PNG a report keeps [bytes]; a larger render is quantised, then shrunk.
MAX_FIGURE_BYTES = 300_000
#: Palette size of a quantised figure.
PALETTE_COLORS = 256
#: Each shrink step's linear scale, and the smallest overall scale tried.
SHRINK_STEP = 0.85
SHRINK_MIN = 0.3
#: Schema of a corridor battery artifact (scripts/corridor_battery.py).
CORRIDOR_SCHEMA = "flowstate.corridor_validation/1"
#: Relative tolerance under which a floating-point value of the figure replicate counts as the
#: battery's when it is not bit-identical: SUMO's own state can differ in the last digits between
#: platforms (docs/E12_PLATFORM_TESTS.md), which changes no reading of a report. Integers, flags and
#: text are compared exactly.
REPRO_REL_TOL = 1e-9
#: Files ``plan`` writes under ``--out``.
PLAN_FILE = "PLAN.json"
PAIRS_FILE = "pairs.txt"
#: The ring benchmark's record ``ring-run`` writes in the ring arm's directory.
RING_RECORD = "ring_benchmark.json"
#: Bins of the I-24 observed-versus-simulated field [s, m] (docs/figures/i24_validation_fields.png).
I24_FIELD_DT_S = 60.0
I24_FIELD_DX_M = 100.0
#: The I-24 replica's geometry (sim x = a + b * data x), as scripts/i24_validate.py reads it.
I24_INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
#: Source-commit conventions of the cloud stages (scripts/gcp/vm_setup.sh).
SOURCE_COMMIT_ENV = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE = ".source_commit"
#: The stage's default arms (label:scenario:battery; an empty scenario is the battery's own record).
DEFAULT_ARMS: tuple[str, ...] = (
    "e13_i24_b2:scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml:"
    "artifacts/i24_validation_dc_refit_rc_p23.json",
    "e13_i24_b5::artifacts/i24_validation_p15_b5.json",
    "e13_i94_rbc:scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml:"
    "artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2_rbc_gated.json",
    "e13_i94_b5::artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2_rbc_b5_gated.json",
    "e13_ring:ring_sugiyama:artifacts/i24_validation_dc_refit_rc.json",
)

_LABEL = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Fraction to percent (labels only; one place, no bare factor in the formatting).
_PERCENT = 100.0
#: What a report directory written here may hold.
_OWN_ENTRIES = frozenset({"report.md", "report.pdf", "figures"})

#: ``(t_range_s, x_range_m, columns) -> frame``: the I-24 recording's mainline rows.
ObservedLoader = Callable[[tuple[float, float], tuple[float, float], list[str]], pd.DataFrame]


@dataclass(frozen=True)
class Arm:
    """One report to regenerate: its label, scenario (empty: the battery's record) and battery."""

    label: str
    scenario: str
    battery: str


def parse_arm(spec: str) -> Arm:
    """Parse ``LABEL:SCENARIO:BATTERY`` (SCENARIO may be empty).

    Raises:
        ValueError: Not three fields, an empty label or battery, or a label that is not a plain
            directory name.
    """
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValueError(f"arm {spec!r}: expected LABEL:SCENARIO:BATTERY")
    label, scenario, battery = (p.strip() for p in parts)
    if not label or not battery:
        raise ValueError(f"arm {spec!r}: the label and the battery are required")
    if not _LABEL.match(label):
        raise ValueError(f"arm {spec!r}: the label must match {_LABEL.pattern}")
    return Arm(label, scenario, battery)


def sha256_file(path: str | Path) -> str:
    """sha256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_commit() -> dict[str, str | None]:
    """The source commit (``$FLOWSTATE_SOURCE_COMMIT``, else ``.source_commit``) and this HEAD.

    On the VM the checkout is a ``git archive`` committed afresh (scripts/gcp/vm_setup.sh), so its
    HEAD names no commit of the repository; the launcher's source commit is the one to cite.
    """
    head: str | None = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
        head = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    source = os.environ.get(SOURCE_COMMIT_ENV, "").strip() or None
    f = REPO_ROOT / SOURCE_COMMIT_FILE
    if source is None and f.is_file():
        source = next(iter(f.read_text().split()), None)
    return {"code": source or head, "checkout_head": head}


def battery_kind(doc: Mapping[str, Any]) -> str:
    """``corridor`` (scripts/corridor_battery.py) or ``i24`` (scripts/i24_validate.py).

    Raises:
        ValueError: Neither schema.
    """
    if doc.get("schema") == CORRIDOR_SCHEMA:
        return "corridor"
    if "schema_version" in doc and isinstance(doc.get("simulated"), Mapping):
        return "i24"
    raise ValueError("not a battery artifact (neither flowstate.corridor_validation/1 nor I-24)")


def battery_seeds(doc: Mapping[str, Any], kind: str) -> list[int]:
    """The battery's replicate seeds, in its order (the ring arm: its ring block's)."""
    if kind == "ring":
        return [int(s) for s in doc["ring"]["seeds"]]
    return [int(s) for s in doc["seeds"]]


def recorded_scenario(doc: Mapping[str, Any], kind: str) -> str | None:
    """The scenario file the battery records it ran (None when it records none)."""
    if kind == "i24":
        sf = doc.get("scenario_file")
        return str(sf["path"]) if isinstance(sf, Mapping) and sf.get("path") else None
    scn = doc.get("scenario")
    return str(scn) if scn else None


def damped_ring(cfg: ScenarioConfig) -> ScenarioConfig:
    """The ring benchmark's dampening arm, as :func:`evaluate_ring_benchmark` builds it."""
    damped = cfg.model_copy(deep=True)
    damped.av.penetration = SINGLE_AV_PENETRATION
    damped.av.compliance = 1.0
    damped.av.controller = DAMPENING_CONTROLLER
    return damped


def hash_policy(cfg: ScenarioConfig, recorded: str) -> tuple[str | None, str]:
    """Which policy's hash of ``cfg`` the battery recorded: ``(policy, today's hash)``.

    The batteries before policy 4 recorded policy-3 hashes; the pin is the configuration, and the
    replicate's physics is checked separately (the reproduction check), never assumed.
    """
    today = config_hash(cfg)
    if recorded == today:
        return f"v{CONFIG_HASH_VERSION}", today
    if recorded == config_hash_v3(cfg):
        return "v3", today
    return None, today


def _report_dir_conflict(out_dir: Path) -> str | None:
    """Why ``out_dir`` may not be written (it holds files this script did not write), else None.

    A directory written here holds ``report.md`` (naming :data:`GENERATOR`), ``report.pdf`` and
    ``figures/`` only; anything else, or a ``report.md`` another generator wrote, is an existing
    report, which is never overwritten.
    """
    if not out_dir.exists():
        return None
    if not out_dir.is_dir():
        return f"{out_dir} exists and is not a directory"
    foreign = sorted(e.name for e in out_dir.iterdir() if e.name not in _OWN_ENTRIES)
    report = out_dir / "report.md"
    if report.exists() and GENERATOR not in report.read_text(errors="replace"):
        foreign.insert(0, "report.md")
    if foreign:
        return (
            f"{out_dir} holds files this script did not write ({', '.join(foreign)}); an "
            "existing report is never overwritten"
        )
    return None


def plan_arm(
    arm: Arm,
    seed: int,
    out: Path,
    reports_root: Path,
    load: Callable[[str], ScenarioConfig] | None = None,
) -> dict[str, Any]:
    """Check one arm before anything runs; returns its plan entry.

    Status ``planned`` (the run to make is in the entry), ``skipped`` (an input is missing) or
    ``refused`` (an input is not what the battery records, or the report directory is not ours).
    """
    if load is None:
        from microsim.scenarios import load_scenario

        load = load_scenario
    entry: dict[str, Any] = {
        "label": arm.label,
        "battery": arm.battery,
        "scenario": arm.scenario,
        "seed": seed,
        "report_dir": str(reports_root / arm.label),
    }

    def done(status: str, reason: str) -> dict[str, Any]:
        entry.update(status=status, reason=reason)
        return entry

    bat = Path(arm.battery)
    if not bat.is_file():
        return done("skipped", f"no battery {arm.battery}")
    try:
        doc = json.loads(bat.read_text())
    except (OSError, ValueError) as exc:
        return done("refused", f"{arm.battery} is not readable JSON ({exc})")
    if not isinstance(doc, Mapping):
        return done("refused", f"{arm.battery} is not a JSON object")
    is_ring = arm.scenario == RING_SCENARIO
    try:
        kind = "ring" if is_ring else battery_kind(doc)
    except ValueError as exc:
        return done("refused", f"{arm.battery}: {exc}")
    if is_ring and not isinstance(doc.get("ring"), Mapping):
        return done("refused", f"{arm.battery} carries no ring block")
    entry.update(kind=kind, battery_sha256=sha256_file(bat))
    scenario = arm.scenario or recorded_scenario(doc, kind)
    if not scenario:
        return done("refused", f"no scenario given and {arm.battery} records none")
    entry["scenario"] = scenario
    if not is_ring and not Path(scenario).is_file():
        return done("skipped", f"no scenario {scenario}")
    conflict = _report_dir_conflict(reports_root / arm.label)
    if conflict is not None:
        return done("refused", conflict)
    seeds = battery_seeds(doc, kind)
    if seed not in seeds:
        return done("refused", f"seed {seed} is not one of {arm.battery}'s {len(seeds)} seeds")
    if kind == "i24":
        sf = doc.get("scenario_file")
        if isinstance(sf, Mapping) and sf.get("sha256"):
            if Path(str(sf.get("path"))) != Path(scenario):
                return done(
                    "refused", f"{arm.battery} ran {sf.get('path')}, not {scenario} (scenario_file)"
                )
            now = sha256_file(scenario)
            if now != sf["sha256"]:
                return done(
                    "refused",
                    f"{scenario} has sha256 {now[:12]} now, {arm.battery} recorded "
                    f"{str(sf['sha256'])[:12]}",
                )
            entry["scenario_sha256"] = now
    if kind == "corridor" and doc.get("scenario") and Path(str(doc["scenario"])) != Path(scenario):
        return done("refused", f"{arm.battery} ran {doc['scenario']}, not {scenario}")
    try:
        cfg = load(scenario)
    except Exception as exc:  # a missing file, YAML or schema error: the arm stops, not the plan
        return done("refused", f"{scenario} does not load ({exc})")
    if is_ring:
        from microsim.scenarios import resolve_scenario

        resolved = resolve_scenario(scenario).resolve()
        entry["scenario_file"] = str(
            resolved.relative_to(REPO_ROOT) if resolved.is_relative_to(REPO_ROOT) else resolved
        )
        ring = doc["ring"]
        pol_b, today_b = hash_policy(cfg, str(ring["config_hash_baseline"]))
        pol_d, today_d = hash_policy(damped_ring(cfg), str(ring["config_hash_damped"]))
        entry.update(
            config_hash_battery=[ring["config_hash_baseline"], ring["config_hash_damped"]],
            config_hash_today=[today_b, today_d],
            hash_policy=[pol_b, pol_d],
            run_root=str(out / arm.label),
        )
        if pol_b is None or pol_d is None:
            return done(
                "refused",
                f"the ring's hashes today ({today_b}, {today_d}) are not the battery's "
                f"({ring['config_hash_baseline']}, {ring['config_hash_damped']}) under policy "
                f"v{CONFIG_HASH_VERSION} or v3",
            )
        return done("planned", f"ring benchmark, seed {seed}, hashes {today_b}/{today_d}")
    recorded = str(doc["config_hash"])
    pol, today = hash_policy(cfg, recorded)
    entry.update(
        config_hash_battery=recorded,
        config_hash_today=today,
        hash_policy=pol,
        run_dir=str(out / arm.label / today / str(seed)),
        pair=f"{arm.label}={scenario}:{seed}",
    )
    if pol is None:
        return done(
            "refused",
            f"{scenario} hashes {today} (policy v{CONFIG_HASH_VERSION}) and "
            f"{config_hash_v3(cfg)} (v3); {arm.battery} recorded {recorded}",
        )
    return done("planned", f"{scenario} seed {seed}, config {today} (battery {recorded}, {pol})")


def write_plan(entries: Sequence[Mapping[str, Any]], seed: int, out: Path) -> Path:
    """Write ``PLAN.json`` and ``pairs.txt`` (the run_pairs arguments) under ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    tokens: list[str] = []
    pairs: list[str] = []
    for e in entries:
        if e["status"] == "planned" and e.get("kind") != "ring":
            tokens += ["--expect-hash", f"{e['label']}={e['config_hash_today']}"]
            pairs.append(str(e["pair"]))
    (out / PAIRS_FILE).write_text("".join(f"{t}\n" for t in [*tokens, *pairs]))
    plan = {
        "schema": PLAN_SCHEMA,
        "stage": STAGE,
        "seed": seed,
        "config_hash_version": CONFIG_HASH_VERSION,
        "out": str(out),
        "arms": list(entries),
    }
    path = out / PLAN_FILE
    path.write_text(json.dumps(plan, indent=2))
    return path


def _rt(obj: object) -> object:
    """The JSON round trip an artifact's value went through."""
    return json.loads(json.dumps(json_safe(obj)))


def _close(a: object, b: object) -> bool:
    """Equal, floating-point values within :data:`REPRO_REL_TOL` (everything else exactly)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, int | float) or not isinstance(b, int | float):
            return False
        return math.isclose(float(a), float(b), rel_tol=REPRO_REL_TOL, abs_tol=0.0)
    return a == b


def _not_checked(reason: str) -> dict[str, Any]:
    return {
        "checked": False,
        "identical": None,
        "reproduces": None,
        "compared": [],
        "inexact": [],
        "differences": [],
        "rel_tol": REPRO_REL_TOL,
        "reason": reason,
    }


def _compare(pairs: Sequence[tuple[str, object, object]]) -> dict[str, Any]:
    """Field-by-field comparison of the replicate's values with the battery's (None: not recorded).

    ``identical``: every compared field is bit-identical after the artifact's JSON round trip;
    ``reproduces``: every field is, or differs only in floating-point values within
    :data:`REPRO_REL_TOL` (those fields are listed under ``inexact``).
    """
    compared: list[str] = []
    inexact: list[str] = []
    differences: list[str] = []
    for name, ours, theirs in pairs:
        if theirs is None:
            continue
        compared.append(name)
        a, b = _rt(ours), _rt(theirs)
        if a == b:
            continue
        (inexact if _close(a, b) else differences).append(name)
    if not compared:
        return _not_checked("the battery records none of the compared fields for this seed")
    return {
        "checked": True,
        "identical": not differences and not inexact,
        "reproduces": not differences,
        "compared": compared,
        "inexact": inexact,
        "differences": differences,
        "rel_tol": REPRO_REL_TOL,
        "reason": "",
    }


def reproduction_corridor(
    meta: Mapping[str, Any], doc: Mapping[str, Any], seed: int
) -> dict[str, Any]:
    """The replicate against the corridor battery's ``per_seed`` row of the same seed."""
    row = next((r for r in doc.get("per_seed") or [] if int(r.get("seed", -1)) == seed), None)
    if row is None:
        return _not_checked(f"the battery has no per_seed row for seed {seed}")
    return _compare(
        [
            (
                "insertion",
                insertion_stats(meta).to_dict() if records_insertion(meta) else None,
                row.get("insertion"),
            ),
            ("n_collisions", collision_count(meta), row.get("n_collisions")),
            ("weave_releases", weave_release_rows(meta), row.get("weave_releases")),
        ]
    )


def reproduction_i24(meta: Mapping[str, Any], doc: Mapping[str, Any], seed: int) -> dict[str, Any]:
    """The replicate against the I-24 battery's per-replicate records of the same seed."""
    sim = doc["simulated"]
    i = [int(s) for s in sim["seeds"]].index(seed)

    def at(key: str) -> object:
        values = sim.get(key)
        return values[i] if isinstance(values, list) and len(values) > i else None

    planned, departed = meta.get("n_vehicles_planned"), meta.get("n_vehicles_departed")
    realized = round(departed / planned, 4) if planned and departed is not None else None
    return _compare(
        [
            ("ramps", meta.get("ramps"), at("ramps_per_replicate")),
            ("demand_realized_fraction", realized, at("demand_realized_fraction")),
            ("n_collisions", collision_count(meta), at("n_collisions_per_replicate")),
        ]
    )


def reproduction_ring(
    record: Mapping[str, Any], doc: Mapping[str, Any], seed: int
) -> dict[str, Any]:
    """The ring re-run's checks against the battery's ring record of the same seed."""
    row = next((r for r in doc["ring"]["per_seed"] if int(r["seed"]) == seed), None)
    if row is None:
        return _not_checked(f"the battery's ring block has no record of seed {seed}")
    return _compare(
        [
            ("emergence", record.get("emergence"), row.get("emergence")),
            ("dampening", record.get("dampening"), row.get("dampening")),
        ]
    )


def reproduction_line(rep: Mapping[str, Any], seed: int) -> str:
    """The Provenance sentence on whether the figure replicate is the battery's replicate."""
    if not rep["checked"]:
        return (
            f"Whether the figure replicate reproduces the battery's replicate {seed} was not "
            f"checked: {rep['reason']}."
        )
    compared = ", ".join(rep["compared"])
    if rep["identical"]:
        return (
            f"The figure replicate reproduces the battery's replicate {seed}: {compared} are "
            "identical to the battery's record of that seed."
        )
    if rep["reproduces"]:
        return (
            f"The figure replicate reproduces the battery's replicate {seed} to within a relative "
            f"{rep['rel_tol']:g} on floating-point values (compared: {compared}): "
            f"{', '.join(rep['inexact'])} differ from the battery's record of that seed only in "
            "the last digits of floating-point values; the rest is identical."
        )
    return (
        f"The figure replicate does NOT reproduce the battery's replicate {seed}: "
        f"{', '.join(rep['differences'])} differ from the battery's record of that seed "
        f"(compared: {compared}). Its figures show this configuration and seed under the code "
        "that re-ran it, not the battery's own run."
    )


def fit_png(path: Path, max_bytes: int = MAX_FIGURE_BYTES) -> dict[str, Any]:
    """Keep a PNG at most ``max_bytes``: palette-quantise it, then shrink it step by step.

    Returns:
        ``{bytes_rendered, bytes, downsampled, scale}``.

    Raises:
        RuntimeError: Still larger at the smallest scale tried.
    """
    rendered = path.stat().st_size
    if rendered <= max_bytes:
        return {"bytes_rendered": rendered, "bytes": rendered, "downsampled": False, "scale": 1.0}
    from PIL import Image

    with Image.open(path) as im:
        image = im.convert("RGB")
    scale = 1.0
    while True:
        frame = (
            image
            if scale == 1.0
            else image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        )
        frame.quantize(
            colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        ).save(path, format="PNG", optimize=True)
        size = path.stat().st_size
        if size <= max_bytes:
            return {"bytes_rendered": rendered, "bytes": size, "downsampled": True, "scale": scale}
        if scale * SHRINK_STEP < SHRINK_MIN:
            raise RuntimeError(f"{path}: {size} bytes at scale {scale:.2f}, above {max_bytes}")
        scale *= SHRINK_STEP


def _ci(raw: Mapping[str, Any]) -> CI:
    """A replicate interval read back from an artifact (null = NaN, as it was written)."""

    def num(key: str) -> float:
        value = raw.get(key)
        return math.nan if value is None else float(value)

    return CI(num("mean"), num("lo95"), num("hi95"), int(raw.get("n") or 0))


def _criteria(rows: Sequence[Mapping[str, Any]]) -> list[CriteriaResult]:
    """The battery's criteria rows read back.

    The artifact wrote a NaN value as null (JSON has no NaN): an evaluated row's null value was
    measured and undefined, so it is NaN again (the report prints it as the product report would);
    an unevaluated row's null is "no input".
    """
    names = {f.name for f in fields(CriteriaResult)}
    out: list[CriteriaResult] = []
    for r in rows:
        kw = {k: v for k, v in r.items() if k in names}
        if kw.get("value") is None and kw.get("evaluated"):
            kw["value"] = math.nan
        out.append(CriteriaResult(**kw))
    return out


def _observed_provenance(raw: Mapping[str, Any]) -> ObservedProvenance:
    """The corridor battery's ``observations`` block as the report's provenance object."""
    names = {f.name for f in fields(ObservedProvenance)} - {"wave_speed"}
    wave = raw.get("detector_wave_speed")
    wave_speed: DetectorWaveSpeed | None = None
    if isinstance(wave, Mapping):
        w_names = {f.name for f in fields(DetectorWaveSpeed)}
        kw = {k: (math.nan if v is None else v) for k, v in wave.items() if k in w_names}
        if isinstance(kw.get("iqr_kmh"), list):
            kw["iqr_kmh"] = tuple(math.nan if v is None else float(v) for v in kw["iqr_kmh"])
        wave_speed = DetectorWaveSpeed(**kw)
    kwargs = {k: v for k, v in raw.items() if k in names}
    return ObservedProvenance(**kwargs, wave_speed=wave_speed)


def _pct(fraction: float) -> str:
    return _fmt(_PERCENT * fraction, 3)


def collision_lines(
    summary: Mapping[str, Any] | None,
) -> tuple[str, str, list[dict[str, str]], list[str], str | None]:
    """The collision line, runs, location rows, limitations and banner from a ``collisions`` block.

    The block is :func:`validation.battery.collision_summary`'s, as the report's Model integrity
    section reads it; the wording is the report's.
    """
    if summary is None:
        return (
            "not recorded — the battery artifact carries no collisions block.",
            "",
            [],
            [
                "The battery records no collision count, so a collision-free simulation is not established."
            ],
            None,
        )
    total = int(summary["total"])
    per = summary["per_run"]
    not_recorded = [str(n) for n in summary.get("runs_not_recorded") or []]
    line = f"{total} over {summary['n_runs_recorded']} run(s) that record the counter"
    if not_recorded:
        line += f", not recorded for {len(not_recorded)} run(s) ({', '.join(not_recorded)})"
    line += (
        f"; per run {_fmt(per['mean'])} [{_fmt(per['lo95'])}, {_fmt(per['hi95'])}] "
        f"(two-sided {_fmt(CI_LEVEL * _PERCENT, 3)} % t-interval, n = {per['n']}"
        + (", underpowered" if per.get("underpowered") else "")
        + ")"
    )
    rate = summary["rate"]
    value = math.nan if rate.get("value") is None else float(rate["value"])
    if rate["n_runs"] == 0:
        line += "; no rate: no run that records collisions records its departed vehicles"
    elif not math.isfinite(value):
        line += "; no rate: no vehicle departed"
    else:
        line += (
            f"; {_fmt(value, 3)} per {int(rate['per_vehicles']):,} departed vehicles "
            f"({rate['n_collisions']} over {rate['n_departed']} departed in {rate['n_runs']} "
            "run(s), whole runs including warm-up)"
        )
    line += "."
    runs = ", ".join(f"{w['run']} ({w['n']})" for w in summary.get("runs_with_collisions") or [])
    locations = [
        {
            "lane": str(r["lane"]),
            "edge": str(r["edge"]),
            "n": str(r["n"]),
            "pos": _position_text(r),
            "runs": ", ".join(str(x) for x in r.get("runs", [])),
        }
        for r in summary.get("locations") or []
    ]
    limitations: list[str] = []
    banner: str | None = None
    if total > 0:
        banner = (
            f"MODEL INTEGRITY FAILURE — {total} SUMO collision(s) in "
            f"{summary['n_runs_with_collisions']} of {summary['n_runs_recorded']} run(s); the "
            "no_collisions acceptance criterion fails. A collision is a model defect, not a "
            "traffic outcome: see Model integrity and Limitations."
        )
        limitations.append(
            f"The battery contains {total} SUMO collision(s) in {summary['n_runs_with_collisions']} "
            f"of {summary['n_runs_recorded']} run(s) ({runs}). A collision is a model defect, not "
            "a traffic outcome, and every metric of those runs includes the vehicles involved."
        )
    if not_recorded:
        limitations.append(
            f"Collisions were not recorded for {len(not_recorded)} run(s) "
            f"({', '.join(not_recorded)}); a collision-free simulation is not established for them."
        )
    return line, runs, locations, limitations, banner


def insertion_line_corridor(ins: Mapping[str, Any] | None) -> str | None:
    """The report's insertion sentence from a corridor battery's ``insertion`` block."""
    if not isinstance(ins, Mapping):
        return None
    arrived = (
        "arrival not recorded"
        if ins.get("mean_arrived") is None
        else (
            f"{float(ins['mean_arrived']):.1f} arrived per run "
            f"(over {ins['n_with_arrived']} of {ins['n_runs']})"
        )
    )
    text = (
        f"Insertion: {ins['planned']} vehicles planned over {ins['n_runs']} run(s), "
        f"{ins['departed']} departed ({_fmt(ins['mean_departed_fraction'], 3)} of plan on average, "
        f"lowest {_fmt(ins['min_departed_fraction'], 3)}), {arrived}"
    )
    if ins.get("verdict"):
        text += f"; verdict: {ins['verdict']}"
    starved = ins.get("starved_ramps") or []
    if starved:
        text += f". Starved on-ramps: {', '.join(str(s) for s in starved)}"
    return text + "."


def insertion_line_i24(sim: Mapping[str, Any]) -> str | None:
    """The insertion sentence from an I-24 battery's realised-demand fractions."""
    realized = [float(v) for v in sim.get("demand_realized_fraction") or [] if v is not None]
    if not realized:
        return None
    return (
        f"Insertion: {_fmt(float(np.mean(realized)), 3)} of the planned vehicles departed on "
        f"average over {len(realized)} run(s), lowest {_fmt(min(realized), 3)} (departed over "
        "planned, per run)."
    )


def weave_exit_lines(block: Mapping[str, Any] | None) -> list[str]:
    """The report's weave-exit sentences from a corridor battery's ``weave_exits`` block."""
    if not isinstance(block, Mapping):
        return []
    threshold = f"{_pct(float(block['threshold_share']))} % threshold"
    lines: list[str] = []
    for s in block.get("sections") or []:
        missed = s["missed_exit"]
        share = missed.get("share")
        share_text = (
            f"{_pct(float(share))} %"
            if share is not None and math.isfinite(float(share))
            else "no exiter reached"
        )
        exit_name = f" (exit {s['exit']})" if s.get("exit") else ""
        state = f"above the {threshold}" if s.get("flagged") else f"within the {threshold}"
        lines.append(
            f"Weave exits at {s['ramp']}{exit_name}: {missed['n']} of {s['reached']} reached "
            f"exiters ({share_text}) were given up at the gore's end and rerouted through over "
            f"{s['n_runs']} run(s), {state}."
        )
    return lines


def _table(block: Mapping[str, Any] | None, key: str = "ci") -> list[dict[str, str]]:
    if not isinstance(block, Mapping) or not isinstance(block.get(key), Mapping):
        return []
    return _metric_rows({k: _ci(v) for k, v in block[key].items() if isinstance(v, Mapping)})


def _gate_context(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    return {
        "headline": str(raw.get("headline", "")),
        "rows": [
            {
                "check": str(c.get("check", "")),
                "day_set": str(c.get("day_set", "")),
                "status": str(c.get("status", "")).upper(),
                "gating": "yes" if c.get("gating") else "no",
                "plain": str(c.get("plain", "")),
            }
            for c in raw.get("checks") or []
        ],
    }


def _i24_observed_rows(obs: Mapping[str, Any]) -> list[dict[str, str]]:
    span = obs.get("span_data_x_m") or []
    rows = [
        ("recording data hash", str(obs.get("data_hash", ""))),
        ("study period", str(obs.get("period", ""))),
        (
            "measured span (data x) [m]",
            f"{_fmt(float(span[0]))}–{_fmt(float(span[1]))}" if len(span) == 2 else "",
        ),
        (
            "link-flow sections (data x) [m]",
            ", ".join(_fmt(float(s)) for s in obs.get("sections_m") or []),
        ),
        ("window [s]", _fmt(obs.get("window_s"))),
        ("windows", str(obs.get("n_windows", ""))),
        ("speed segments", str(obs.get("n_segments", ""))),
        ("fragments", str(obs.get("n_fragments", ""))),
        ("coverage estimator", str(obs.get("coverage_recommended_source", ""))),
    ]
    return [{"name": n, "detail": d} for n, d in rows if d]


def _i24_geh_rows(geh: Mapping[str, Any]) -> list[dict[str, str]]:
    names = {
        "vs_tracked_counts": "tracked crossings (lower bound)",
        "vs_coverage_corrected_counts": "crossings / apparent coverage (upper bound)",
        "vs_recommended_coverage_counts": "crossings / recommended coverage",
        "lane_set": "recommended coverage, observed lane set",
    }
    primary = {
        "tracked": "vs_tracked_counts",
        "corrected": "vs_coverage_corrected_counts",
        "recommended": "vs_recommended_coverage_counts",
    }.get(str(geh.get("primary")), "")
    rows: list[dict[str, str]] = []
    for key, name in names.items():
        table = geh.get(key)
        if isinstance(table, Mapping) and "fraction_under_5" in table:
            rows.append(
                {
                    "basis": name,
                    "share": _fmt(table.get("fraction_under_5")),
                    "n": str(table.get("n_bins", "")),
                    "row": "yes" if key == primary else "no",
                }
            )
    return rows


def _i24_wave_rows(doc: Mapping[str, Any]) -> list[dict[str, str]]:
    obs = doc.get("observed", {}).get("waves_by_detector") or {}
    sim = doc.get("simulated", {}).get("wave_speed_by_detector") or {}
    rows: list[dict[str, str]] = []
    for name in sim:
        o, s = obs.get(name) or {}, sim.get(name) or {}
        rows.append(
            {
                "detector": str(name),
                "observed": _fmt(o.get("mean_backward_speed_kmh")),
                "simulated": _fmt(s.get("mean_backward_speed_kmh")),
                "n": str(s.get("n_replicates_with_backward_waves", "")),
            }
        )
    return rows


def _ring_rows(block: Mapping[str, Any], names: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, label in names:
        v = block.get(key)
        if isinstance(v, Mapping):
            rows.append(
                {
                    "name": label,
                    "mean": _fmt(v.get("mean")),
                    "lo": _fmt(v.get("lo95")),
                    "hi": _fmt(v.get("hi95")),
                    "n": str(v.get("n", "")),
                    "min": _fmt(v.get("min")),
                    "max": _fmt(v.get("max")),
                }
            )
    return rows


def _calibrations(meta: Mapping[str, Any]) -> list[dict[str, str]]:
    entries = list(meta.get("calibration_artifacts") or [])
    if isinstance(meta.get("fleet_calibration"), Mapping):
        entries.append(meta["fleet_calibration"])
    out: list[dict[str, str]] = []
    for e in entries:
        if isinstance(e, Mapping):
            row = {
                "path": str(e.get("path", "unknown")),
                "data_hash": str(e.get("data_hash", "unknown")),
            }
            if row not in out:
                out.append(row)
    return out


def _fail_names(criteria: Sequence[CriteriaResult]) -> list[str]:
    return [c.name for c in criteria if c.status in ("FAIL", "NOT EVALUATED", "NOT RECORDED")]


def i24_observed_figure(
    run: _RunInfo,
    doc: Mapping[str, Any],
    out_dir: Path,
    loader: ObservedLoader,
    geometry: Mapping[str, float],
) -> tuple[str, str]:
    """Observed (I-24 MOTION) and simulated speed fields side by side, same bins and scale.

    The recording's study period on the measured span, and the replicate's matching window (sim
    t = data t - period start + warm-up; sim x = a + b * data x), each binned at
    :data:`I24_FIELD_DT_S` x :data:`I24_FIELD_DX_M`.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    obs_block = doc["observed"]
    t_lo, t_hi = (float(v) for v in obs_block["t_range_s"])
    x_hi = float(obs_block["span_data_x_m"][1])
    a, b = float(geometry["a"]), float(geometry["b"])
    observed = loader((t_lo, t_hi), (0.0, x_hi), ["t", "x", "v"])
    observed = observed.assign(t=observed["t"] - t_lo)
    with (run.path / "trajectories.parquet").open("rb") as f:
        sim = pd.read_parquet(f, columns=["t", "x", "v"])
    warm = run.warmup_s
    sim = sim.assign(t=sim["t"] - warm, x=(sim["x"] - a) / b)
    sim = sim[(sim["t"] >= 0.0) & (sim["t"] < t_hi - t_lo) & (sim["x"] >= 0.0) & (sim["x"] < x_hi)]
    panels = [
        (
            "observed (I-24 MOTION fragments)",
            speed_field(observed, dt_bin=I24_FIELD_DT_S, dx_bin=I24_FIELD_DX_M),
        ),
        (
            f"simulated, seed {run.seed}",
            speed_field(sim, dt_bin=I24_FIELD_DT_S, dx_bin=I24_FIELD_DX_M),
        ),
    ]
    values = np.concatenate([ms_to_kmh(f.mean_speed).ravel() for _, f in panels])
    finite = values[np.isfinite(values)]
    vmin = float(finite.min()) if finite.size else 0.0
    vmax = float(finite.max()) if finite.size else 1.0
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.0), sharey=True, layout="constrained")
    mesh = None
    for ax, (title, field) in zip(axes, panels, strict=True):
        mesh = ax.pcolormesh(
            field.x_edges,
            field.t_edges,
            ms_to_kmh(field.mean_speed),
            shading="flat",
            vmin=vmin,
            vmax=vmax,
        )
        ax.set_xlabel("data x along westbound travel [m]")
        ax.set_title(title)
    axes[0].set_ylabel("time after the study period's start [s]")
    fig.colorbar(mesh, ax=list(axes), label="mean speed [km/h]")
    name = f"observed_vs_simulated_seed_{run.seed}.png"
    fig.savefig(out_dir / name, dpi=150)
    plt.close(fig)
    caption = (
        f"Mean speed over the study period on the measured span: the I-24 MOTION recording (left) "
        f"and replicate {run.seed} (right), same bins and colour scale"
    )
    return name, caption


TEMPLATE = """# {{ title }}

{% for b in banners %}
> **{{ b }}**

{% endfor %}
Generated: {{ created_at }} by `{{ generator }}` (stage `{{ stage }}`, code `{{ code }}`).

## Provenance

{{ provenance_line }}

{{ reproduction }}

| Item | Value |
|---|---|
{% for r in provenance %}
| {{ r.name }} | {{ r.value }} |
{% endfor %}

Seeds of the battery: {{ seeds_joined }}.

### What comes from where

| Part of this report | Source |
|---|---|
{% for r in sources %}
| {{ r.part }} | {{ r.source }} |
{% endfor %}

### Package versions (battery)

{% if versions %}
{% for v in versions %}
- {{ v.name }}: `{{ v.value }}`
{% endfor %}
{% else %}
- The battery artifact records no versions.
{% endif %}

### Calibration artifacts used

{% if calibrations %}
| Artifact | data_hash |
|---|---|
{% for c in calibrations %}
| `{{ c.path }}` | `{{ c.data_hash }}` |
{% endfor %}

Read from the figure replicate's metadata: the configuration, the same as the battery's, fixes them.
{% else %}
No calibration artifacts recorded: defaults in use are literature values, not calibrated claims.
{% endif %}
{% if observed %}

### Observed data

{% if observed_note %}
{{ observed_note }}

{% endif %}
| Field | Value |
|---|---|
{% for o in observed %}
| {{ o.name }} | {{ o.detail }} |
{% endfor %}
{% endif %}
{% if ring %}

## Ring benchmark

{{ ring.statement }}

| Threshold | Value |
|---|---|
{% for t in ring.thresholds %}
| {{ t.name }} | {{ t.value }} |
{% endfor %}

### Emergence ({{ ring.emergence_result }})

| Check value | Mean | Lower | Upper | n | Smallest | Largest |
|---|---|---|---|---|---|---|
{% for m in ring.emergence %}
| {{ m.name }} | {{ m.mean }} | {{ m.lo }} | {{ m.hi }} | {{ m.n }} | {{ m.min }} | {{ m.max }} |
{% endfor %}

### Dampening ({{ ring.dampening_result }})

| Check value | Mean | Lower | Upper | n | Smallest | Largest |
|---|---|---|---|---|---|---|
{% for m in ring.dampening %}
| {{ m.name }} | {{ m.mean }} | {{ m.lo }} | {{ m.hi }} | {{ m.n }} | {{ m.min }} | {{ m.max }} |
{% endfor %}
{% else %}

## Model integrity

What the battery artifact records about the simulation itself misbehaving, pooled over its
replicates as the battery read them from every run's metadata. A collision or a lock is a model
defect, not a traffic outcome; a count the battery did not record is reported as not recorded,
never as zero.

- Collisions: {{ integrity.collision_line }}
{% if integrity.collision_runs %}
- Runs with collisions: {{ integrity.collision_runs }}
{% endif %}
- Locks: {{ integrity.lock_line }}
{% if integrity.insertion %}
- {{ integrity.insertion }}
{% endif %}
{% for line in integrity.weave_exit_lines %}
- {{ line }}
{% endfor %}
{% for line in integrity.weave_release_lines %}
- {{ line }}
{% endfor %}
{% if integrity.locations %}

| Lane | Edge | Collisions logged | Position along the lane [m] | Runs |
|---|---|---|---|---|
{% for loc in integrity.locations %}
| `{{ loc.lane }}` | `{{ loc.edge }}` | {{ loc.n }} | {{ loc.pos }} | {{ loc.runs }} |
{% endfor %}
{% endif %}
{% if integrity.lock_rows %}

| Run | Head x [m] | Section | Onset [s] | Duration [min] | To the run's end | Trapped in network | Never departed upstream |
|---|---|---|---|---|---|---|---|
{% for lk in integrity.lock_rows %}
| {{ lk.run }} | {{ lk.x }} | {{ lk.section }} | {{ lk.onset }} | {{ lk.duration }} | {{ lk.end }} | {{ lk.trapped }} | {{ lk.never }} |
{% endfor %}

{{ integrity.lock_note }}
{% endif %}
{% if waiting %}

### Waiting measures

Each replicate's travel time and total delay including the time spent waiting on ramps and to
enter the network, with replicate intervals as above.

| Measure | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
{% for m in waiting %}
| {{ m.name }} | {{ m.mean }} | {{ m.lo }} | {{ m.hi }} | {{ m.n }} | {{ m.underpowered }} |
{% endfor %}
{% endif %}
{% endif %}
{% if gate %}

## Baseline gate

{{ gate.headline }}

| Check | Day set | Status | Gating | Statement |
|---|---|---|---|---|
{% for g in gate.rows %}
| {{ g.check }} | {{ g.day_set }} | {{ g.status }} | {{ g.gating }} | {{ g.plain }} |
{% endfor %}
{% endif %}

## Acceptance criteria

| Criterion | Value | Threshold | Evaluated | Result |
|---|---|---|---|---|
{% for c in criteria %}
| {{ c.name }} | {{ c.value }} | {{ c.threshold }} | {{ c.evaluated }} | {{ c.result }} |
{% endfor %}

The rows are the battery's, as it scored them. A row marked not evaluated or not recorded is never
a pass, and an unevaluated criterion counts as failing; a row marked reported is a disclosure,
never a pass or a fail.
{% if geh_rows %}

### Link flows by the observed count's basis

| Observed count | Share of GEH below five | Bins | The criterion row's basis |
|---|---|---|---|
{% for g in geh_rows %}
| {{ g.basis }} | {{ g.share }} | {{ g.n }} | {{ g.row }} |
{% endfor %}
{% endif %}
{% if wave_rows %}

### Backward wave speed by detector

Mean backward front speed [km/h]: the recording's field, and the simulated replicates that showed
a backward front (their number in the last column).

| Detector | Observed | Simulated | Replicates with a front |
|---|---|---|---|
{% for w in wave_rows %}
| {{ w.detector }} | {{ w.observed }} | {{ w.simulated }} | {{ w.n }} |
{% endfor %}
{% endif %}
{% if aggregation %}

### Speed criterion by time aggregation

The segment-speed criterion compares a replicate mean with one recorded day. The floor column is
the recorded field against its own three-window moving average: below that resolution the day does
not repeat itself, so no ensemble mean can score under the floor.

| Aggregation | RMSPE, replicate mean vs observed | Floor: observed vs its own three-window average |
|---|---|---|
{% for a in aggregation %}
| {{ a.aggregation }} | {{ a.rmspe }} | {{ a.floor }} |
{% endfor %}
{% endif %}
{% if metrics %}

## Metrics

Mean with two-sided t-distribution confidence bounds at the {{ ci_level_pct }} percent level over
n replicates, as the battery computed them. Rows flagged underpowered have fewer than
{{ min_replicates }} replicates and must not be quoted as headline results.

{{ fuel_note }}

| Metric | Mean | Lower | Upper | n | Underpowered |
|---|---|---|---|---|---|
{% for m in metrics %}
| {{ m.name }} | {{ m.mean }} | {{ m.lo }} | {{ m.hi }} | {{ m.n }} | {{ m.underpowered }} |
{% endfor %}
{% endif %}
{% if notes %}

## Notes recorded with the battery

{% for n in notes %}
- {{ n }}
{% endfor %}
{% endif %}

## Speed contours

{{ figures_line }}

{% for f in figures %}
![{{ f.caption }}]({{ f.path }})
{% endfor %}
{% for line in figure_notes %}

{{ line }}
{% endfor %}

## Limitations

{% for line in limitations %}
- {{ line }}
{% endfor %}
"""


def _render(context: Mapping[str, Any]) -> str:
    env = Environment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.from_string(TEMPLATE).render(**context)


def _load_meta(run_dir: Path) -> dict[str, Any]:
    raw = json.loads((run_dir / "meta.json").read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{run_dir / 'meta.json'}: expected a JSON object")
    return raw


def _figure(
    path_name: tuple[str, str], fig_dir: Path, records: list[dict[str, Any]]
) -> dict[str, str]:
    name, caption = path_name
    size = fit_png(fig_dir / name)
    records.append({"path": str(fig_dir / name), **size})
    return {"path": f"figures/{name}", "caption": caption}


def _scenario_file(entry: Mapping[str, Any]) -> str | None:
    """The arm's scenario file (the ring's resolved YAML), when it is a file."""
    path = str(entry.get("scenario_file") or entry.get("scenario") or "")
    return path if path and Path(path).is_file() else None


def _versions(doc: Mapping[str, Any]) -> list[dict[str, str]]:
    v = doc.get("versions")
    return [{"name": k, "value": str(v[k])} for k in sorted(v)] if isinstance(v, Mapping) else []


@dataclass
class _Figures:
    """What the figure replicate contributes to one report."""

    figures: list[dict[str, str]]
    records: list[dict[str, Any]]
    notes: list[str]
    runs: list[str]
    run: _RunInfo
    reproduction: dict[str, Any]


def _ring_figures(entry: Mapping[str, Any], doc: Mapping[str, Any], fig_dir: Path) -> _Figures:
    """The ring arm's baseline-versus-controller contour pair and its reproduction check."""
    seed, label = int(entry["seed"]), str(entry["label"])
    ring_run = json.loads((Path(entry["run_root"]) / RING_RECORD).read_text())
    row = next(r for r in ring_run["per_seed"] if int(r["seed"]) == seed)
    base_dir, damped_dir = Path(row["run_dir_baseline"]), Path(row["run_dir_damped"])
    base = _RunInfo(base_dir, _load_meta(base_dir))
    damped = _RunInfo(damped_dir, _load_meta(damped_dir))
    hashes = [base.config_hash, damped.config_hash]
    if hashes != list(entry["config_hash_today"]) or {base.seed, damped.seed} != {str(seed)}:
        raise ValueError(
            f"{label}: the ring runs are {hashes} seed {base.seed}/{damped.seed}, planned "
            f"{entry['config_hash_today']} seed {seed}"
        )
    records: list[dict[str, Any]] = []
    pair = _render_contour_pair(base, damped, f"{DAMPENING_CONTROLLER} (one vehicle)", fig_dir, 1)
    return _Figures(
        figures=[_figure(pair, fig_dir, records)],
        records=records,
        notes=[],
        runs=[str(base_dir), str(damped_dir)],
        run=base,
        reproduction=reproduction_ring(row, doc, seed),
    )


def _corridor_figures(
    entry: Mapping[str, Any],
    doc: Mapping[str, Any],
    fig_dir: Path,
    observed_loader: ObservedLoader | None,
    geometry: Mapping[str, float] | None,
) -> _Figures:
    """A corridor arm's contour (and the I-24 observed field) and its reproduction check."""
    seed, label, kind = int(entry["seed"]), str(entry["label"]), str(entry["kind"])
    run_dir = Path(entry["run_dir"])
    if not (run_dir / "meta.json").is_file() or not (run_dir / "trajectories.parquet").is_file():
        raise FileNotFoundError(f"{label}: no figure run with trajectories at {run_dir}")
    meta = _load_meta(run_dir)
    if (
        str(meta.get("config_hash")) != entry["config_hash_today"]
        or int(meta.get("seed", -1)) != seed
    ):
        raise ValueError(
            f"{label}: {run_dir} is config {meta.get('config_hash')} seed {meta.get('seed')}, "
            f"planned {entry['config_hash_today']} seed {seed}"
        )
    run = _RunInfo(run_dir, meta, doc.get("scored_end_s"))
    records: list[dict[str, Any]] = []
    figures = [_figure(_render_contour(run, fig_dir, 0, None), fig_dir, records)]
    notes: list[str] = []
    if kind == "i24":
        if observed_loader is None or geometry is None:
            notes.append(
                "No observed field is shown: the I-24 MOTION recording was not read for this "
                "report (the stage reads it only where the data set was shipped and its data hash "
                "is the battery's)."
            )
        else:
            observed = i24_observed_figure(run, doc, fig_dir, observed_loader, geometry)
            figures.append(_figure(observed, fig_dir, records))
    rep = (
        reproduction_corridor(meta, doc, seed)
        if kind == "corridor"
        else reproduction_i24(meta, doc, seed)
    )
    return _Figures(figures, records, notes, [str(run_dir)], run, rep)


def _profile_text(doc: Mapping[str, Any]) -> tuple[str, tuple[float, float]]:
    """The battery's criteria profile as the report names it, and its wave-speed band."""
    raw = doc.get("criteria_profile")
    name = str(raw.get("name")) if isinstance(raw, Mapping) else str(raw or "fhwa_default")
    profile = get_profile(name)
    source = str(raw.get("source", "")) if isinstance(raw, Mapping) else profile.source
    return f"{profile.name} — {source}", profile.wave_speed_band_kmh


def _own_report(doc: Mapping[str, Any]) -> str | None:
    """The battery's own auto-report (written on its VM), when it is in this checkout."""
    path = doc.get("report_path")
    return str(path) if path and Path(str(path)).is_file() else None


def _provenance(
    entry: Mapping[str, Any],
    doc: Mapping[str, Any],
    figs: _Figures,
    *,
    code: str,
    scenario_sha: str | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """The Provenance table and the "what comes from where" table."""
    kind, seed, battery = str(entry["kind"]), int(entry["seed"]), str(entry["battery"])
    n = len(set(battery_seeds(doc, kind)))
    scenario = f"`{entry['scenario']}`"
    if entry.get("scenario_file") and entry["scenario_file"] != entry["scenario"]:
        scenario += f" (`{entry['scenario_file']}`)"
    if scenario_sha:
        scenario += f", sha256 `{scenario_sha[:12]}`"
    battery_hashes = (
        entry["config_hash_battery"] if kind == "ring" else [entry["config_hash_battery"]]
    )
    policies = entry["hash_policy"] if kind == "ring" else [entry["hash_policy"]]
    today = entry["config_hash_today"] if kind == "ring" else [entry["config_hash_today"]]
    configs = " (as shipped, one FollowerStopper vehicle)" if kind == "ring" else ""
    rows = [
        ("battery artifact", f"`{battery}`, sha256 `{str(entry['battery_sha256'])[:12]}`"),
        ("battery written", str(doc.get("created_at", "not recorded"))),
        ("scenario", scenario),
        ("replicates (battery)", f"n = {n} distinct seeds"),
        ("criteria profile", _profile_text(doc)[0]),
        (
            f"config hash, battery{configs}",
            ", ".join(f"`{h}`" for h in battery_hashes)
            + f" (policy {', '.join(map(str, policies))})",
        ),
        (
            f"config hash, figure replicate{configs}",
            ", ".join(f"`{h}`" for h in today) + f" (policy v{CONFIG_HASH_VERSION})",
        ),
        (
            "figure replicate",
            ", ".join(f"`{p}`" for p in figs.runs)
            + f"; seed {seed}; wall time {_fmt(figs.run.meta.get('wall_time_s'))} s",
        ),
    ]
    own = _own_report(doc)
    if own:
        rows.append(
            (
                "the battery's own auto-report",
                f"`{own}` (written on its VM from every replicate's metadata)",
            )
        )
    tables = (
        f"`{battery}`, its ring block: all {n} seeds"
        if kind == "ring"
        else f"`{battery}`: all {n} replicates of the battery"
    )
    parts = (
        "ring thresholds, per-check intervals and the two ring criteria rows"
        if kind == "ring"
        else "acceptance criteria, metric intervals, model integrity, observed data, notes"
        + (", baseline gate" if isinstance(doc.get("baseline_gate"), Mapping) else "")
    )
    sources = [
        (parts, tables),
        (
            "speed-contour figures",
            f"replicate {seed} re-run with its trajectories by stage `{STAGE}` (code `{code}`)",
        ),
        ("calibration artifacts", "the figure replicate's metadata (the configuration fixes them)"),
    ]
    return (
        [{"name": k, "value": v} for k, v in rows],
        [{"part": k, "source": v} for k, v in sources],
    )


def _ring_context(doc: Mapping[str, Any]) -> dict[str, Any]:
    ring = doc["ring"]
    emergence, dampening = ring["emergence"], ring["dampening"]
    return {
        "statement": (
            f"The ring benchmark of the battery: `{ring['scenario']}` as shipped (emergence) and "
            f"with one compliant {DAMPENING_CONTROLLER} vehicle (dampening), each over "
            f"{len(ring['seeds'])} seeds; a row passes when every seed passes its checks."
        ),
        "thresholds": [
            {"name": k, "value": str(v)} for k, v in (ring.get("thresholds") or {}).items()
        ],
        "emergence_result": (
            f"{emergence['n_pass']} of {emergence['n_seeds']} seeds pass; {emergence['rule']}"
        ),
        "dampening_result": (
            f"{dampening['n_pass']} of {dampening['n_seeds']} seeds pass; {dampening['rule']}"
        ),
        "emergence": _ring_rows(
            emergence,
            (
                ("sigma_v_ms", "spatial speed std, tail [m/s]"),
                ("drift_kmh", "jam drift [km/h] (negative: backward)"),
                ("v_min_after_warmup_ms", "lowest speed after the warm-up [m/s]"),
            ),
        ),
        "dampening": _ring_rows(
            dampening,
            (
                ("sigma_v_damped_ms", "spatial speed std with the controller, tail [m/s]"),
                ("reduction_frac", "reduction of the spatial speed std [fraction]"),
                ("v_min_tail_damped_ms", "lowest tail speed with the controller [m/s]"),
            ),
        ),
    }


def _integrity(doc: Mapping[str, Any], kind: str) -> tuple[dict[str, Any], list[str], list[str]]:
    """The Model integrity section from the battery's pooled blocks: (context, banners, limitations)."""
    banners: list[str] = []
    coll_line, coll_runs, locations, limitations, coll_banner = collision_lines(
        doc.get("collisions")
    )
    if coll_banner:
        banners.append(coll_banner)
    locks = doc.get("locks")
    if isinstance(locks, Mapping):
        lock = _lock_context(locks)
        if lock["lock_banner"]:
            banners.append(lock["lock_banner"])
        limitations += lock["lock_limitations"]
    else:
        lock = {
            "lock_line": "not recorded — the battery artifact carries no lock records.",
            "lock_rows": [],
            "lock_note": None,
        }
        limitations.append(
            "Locks were not recorded for this battery, so a simulation free of permanent "
            "standstills is not established."
        )
    weave = doc.get("weave_releases")
    weave_ctx = (
        _weave_release_context(weave)
        if isinstance(weave, Mapping) and weave.get("sections")
        else {"weave_release_lines": [], "weave_release_limitations": []}
    )
    limitations += weave_ctx["weave_release_limitations"]
    own = _own_report(doc)
    limitations.append(
        "Model-integrity lines that need every replicate's run metadata beyond what the battery "
        "artifact carries (forced lane changes, per-run wall times) are not repeated here"
        + (f"; the battery's own auto-report carries them (`{own}`)." if own else ".")
    )
    context = {
        "collision_line": coll_line,
        "collision_runs": coll_runs,
        "locations": locations,
        "lock_line": lock["lock_line"],
        "lock_rows": lock["lock_rows"],
        "lock_note": lock["lock_note"],
        "insertion": (
            insertion_line_corridor(doc.get("insertion"))
            if kind == "corridor"
            else insertion_line_i24(doc["simulated"])
        ),
        "weave_exit_lines": weave_exit_lines(doc.get("weave_exits")),
        "weave_release_lines": weave_ctx["weave_release_lines"],
    }
    return context, banners, limitations


def _observed(doc: Mapping[str, Any], kind: str) -> tuple[list[dict[str, str]], str]:
    """The observed-data rows (the report's own, for a corridor battery) and their note."""
    if kind == "corridor" and isinstance(doc.get("observations"), Mapping):
        band = _profile_text(doc)[1]
        return _observed_rows(_observed_provenance(doc["observations"]), band), OBSERVED_NOTE
    if kind == "i24" and isinstance(doc.get("observed"), Mapping):
        return _i24_observed_rows(doc["observed"]), ""
    return [], ""


def _aggregation(doc: Mapping[str, Any], kind: str) -> list[dict[str, str]] | None:
    """The speed criterion by time aggregation, when the battery carries both segment matrices."""
    if kind != "i24":
        return None
    obs_seg = doc.get("observed", {}).get("segment_speeds_ms")
    sim_seg = doc.get("simulated", {}).get("segment_speeds_ms_mean")
    if not obs_seg or not sim_seg:
        return None
    window = doc["observed"].get("window_s")
    return speed_aggregation_rows(obs_seg, sim_seg, None if window is None else float(window))


def render_arm(
    entry: Mapping[str, Any],
    *,
    created_at: str,
    code: str,
    pdf: bool,
    observed_loader: ObservedLoader | None = None,
    geometry: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Write one planned arm's report and figures; returns its record entry.

    Raises:
        FileNotFoundError: The figure run is missing.
        ValueError: The figure run is not the planned configuration or seed, or the report
            directory holds files this script did not write.
    """
    label, seed, kind = str(entry["label"]), int(entry["seed"]), str(entry["kind"])
    battery = str(entry["battery"])
    doc = json.loads(Path(battery).read_text())
    out_dir = Path(entry["report_dir"])
    conflict = _report_dir_conflict(out_dir)
    if conflict is not None:
        raise ValueError(conflict)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for old in fig_dir.glob("*.png"):
        old.unlink()
    figs = (
        _ring_figures(entry, doc, fig_dir)
        if kind == "ring"
        else _corridor_figures(entry, doc, fig_dir, observed_loader, geometry)
    )
    rep = figs.reproduction
    scen_file = _scenario_file(entry)
    scenario_sha = sha256_file(scen_file) if scen_file else None
    provenance, sources = _provenance(entry, doc, figs, code=code, scenario_sha=scenario_sha)
    seeds = battery_seeds(doc, kind)
    criteria = _criteria(doc.get("criteria") or [])
    notes = [str(n) for n in doc.get("notes") or []]
    integrity: dict[str, Any] = {}
    banners: list[str] = []
    if kind == "ring":
        criteria = [c for c in criteria if c.name in ("ring_emergence", "ring_dampening")]
        notes = [n for n in notes if "ring" in n.lower()]
        limitations = [
            "The ring tests string instability and one controller on a closed loop, not any corridor."
        ]
    else:
        integrity, banners, limitations = _integrity(doc, kind)
    failing = _fail_names(criteria)
    if failing:
        limitations.append(
            f"Criteria not passed by the battery: {', '.join(failing)}. The results are reported as "
            "they came out; no validation claim is made from rows that did not pass."
        )
    limitations.append(
        f"The contours show one replicate (seed {seed}) of {len(set(seeds))}; the tables describe "
        "all of them. A single replicate's contour illustrates the configuration; it is not a "
        "statistic and not the replicate-mean field the speed criterion compares."
    )
    if rep["checked"] and not rep["reproduces"]:
        limitations.append(reproduction_line(rep, seed))
    if kind != "ring":
        limitations += [
            "Results cover a single corridor and scenario family; transfer to other corridors is "
            "not established.",
            "Model-form uncertainty (car-following and lane-change model assumptions, demand "
            "representation) is not captured by seed-to-seed confidence intervals.",
            "AV penetration and compliance are swept assumptions, not measured behaviour; no "
            "strategy result is part of this report.",
            FUEL_LIMITATION,
        ]
    observed, observed_note = _observed(doc, kind)
    subject = f"{RING_SCENARIO} benchmark" if kind == "ring" else str(entry["scenario"])
    context = {
        "title": f"FlowState auto-report, regenerated: {label} ({subject})",
        "banners": banners,
        "created_at": created_at,
        "generator": GENERATOR,
        "stage": STAGE,
        "code": code,
        "provenance_line": f"figures from replicate {seed}; tables from {battery}",
        "reproduction": reproduction_line(rep, seed),
        "provenance": provenance,
        "seeds_joined": ", ".join(str(s) for s in seeds),
        "sources": sources,
        "versions": _versions(doc),
        "calibrations": _calibrations(figs.run.meta),
        "observed": observed,
        "observed_note": observed_note,
        "ring": _ring_context(doc) if kind == "ring" else None,
        "integrity": integrity,
        "waiting": _table(doc.get("waiting")) if kind == "corridor" else [],
        "gate": _gate_context(doc.get("baseline_gate")),
        "criteria": _criteria_rows(criteria),
        "geh_rows": _i24_geh_rows(doc.get("geh") or {}) if kind == "i24" else [],
        "wave_rows": _i24_wave_rows(doc) if kind == "i24" else [],
        "aggregation": _aggregation(doc, kind),
        "metrics": _table({"ci": doc.get("metrics_ci")}) if kind != "ring" else [],
        "ci_level_pct": _fmt(CI_LEVEL * _PERCENT, 3),
        "min_replicates": str(MIN_REPLICATES),
        "fuel_note": FUEL_NOTE,
        "notes": notes,
        "figures_line": (
            f"Figures from replicate {seed}, re-run with its trajectories by stage `{STAGE}`; a "
            "contour covers the run's scored window (its warm-up dropped)."
        ),
        "figures": figs.figures,
        "figure_notes": figs.notes,
        "limitations": limitations,
    }
    report = out_dir / "report.md"
    report.write_text(_render(context))
    pdf_path: str | None = None
    if pdf:
        from validation.report_pdf import render_pdf

        pdf_path = str(render_pdf(report, report.with_name("report.pdf")))
    return {
        "label": label,
        "kind": kind,
        "status": "written",
        "battery": battery,
        "battery_sha256": entry.get("battery_sha256"),
        "scenario": entry.get("scenario"),
        "scenario_file": scen_file,
        "scenario_sha256": scenario_sha,
        "config_hash_battery": entry.get("config_hash_battery"),
        "config_hash_today": entry.get("config_hash_today"),
        "hash_policy": entry.get("hash_policy"),
        "seed": seed,
        "tables_from": battery,
        "figures_from": f"replicate {seed}",
        "figure_runs": figs.runs,
        "figure_run_meta": {
            "config_hash": figs.run.config_hash,
            "seed": figs.run.seed,
            "wall_time_s": figs.run.meta.get("wall_time_s"),
            "n_collisions": figs.run.meta.get("n_collisions"),
            "versions": figs.run.meta.get("versions"),
        },
        "reproduction": rep,
        "report": str(report),
        "pdf": pdf_path,
        "figures": figs.records,
    }


def _pdf_available() -> bool:
    return importlib.util.find_spec("fpdf") is not None


def _i24_geometry() -> dict[str, float]:
    geo = json.loads(I24_INPUTS.read_text())["geometry"]["sim_x_of_data_x"]
    return {"a": float(geo["a"]), "b": float(geo["b"])}


def _i24_loader(expected_hash: str) -> tuple[ObservedLoader | None, str]:
    """The recording's loader when it is present and is the battery's (data hash), else a reason."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import i24_data

    try:
        have = i24_data.data_hash()
    except (OSError, KeyError, ValueError) as exc:
        return None, f"the I-24 recording is not readable here ({exc})"
    if have != expected_hash:
        return (
            None,
            f"the I-24 recording here has data hash {have[:12]}, the battery's {expected_hash[:12]}",
        )

    def load(t: tuple[float, float], x: tuple[float, float], columns: list[str]) -> pd.DataFrame:
        return i24_data.load_mainline(t_range_s=t, x_range_m=x, columns=columns)

    return load, ""


def cmd_plan(args: argparse.Namespace) -> int:
    out = Path(args.out)
    reports_root = Path(args.reports_root)
    entries: list[dict[str, Any]] = []
    labels: set[str] = set()
    for spec in args.arm or list(DEFAULT_ARMS):
        try:
            arm = parse_arm(spec)
        except ValueError as exc:
            print(f"p22: {spec} refused: {exc}", flush=True)
            entries.append({"label": spec, "status": "refused", "reason": str(exc)})
            continue
        if arm.label in labels:
            entry = {"label": arm.label, "status": "refused", "reason": "label given twice"}
        else:
            labels.add(arm.label)
            entry = plan_arm(arm, int(args.seed), out, reports_root)
        entries.append(entry)
        print(f"p22: {entry['label']} {entry['status']}: {entry['reason']}", flush=True)
    write_plan(entries, int(args.seed), out)
    return 1 if any(e["status"] == "refused" for e in entries) else 0


def cmd_ring_run(args: argparse.Namespace) -> int:
    from validation.ring_benchmark import evaluate_ring_benchmark

    plan = json.loads(Path(args.plan).read_text())
    rc = 0
    for e in plan["arms"]:
        if e.get("status") != "planned" or e.get("kind") != "ring":
            continue
        try:
            result = evaluate_ring_benchmark(
                [int(e["seed"])], e["run_root"], scenario=e["scenario"]
            ).to_dict()
        except Exception as exc:  # a failed run is reported; render records the arm as failed
            print(f"p22: {e['label']} ring run FAILED: {exc!r}", flush=True)
            rc = 1
            continue
        path = Path(e["run_root"]) / RING_RECORD
        path.write_text(json.dumps(json_safe(result), indent=2))
        print(f"p22: {e['label']} ring seed {e['seed']} -> {path}", flush=True)
    return rc


def write_record(
    path: Path,
    arms: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    created_at: str,
    commit: Mapping[str, str | None],
    complete: bool,
) -> None:
    """Write the record (rewritten after every arm, so a cut stage leaves the finished arms)."""
    doc = {
        "schema": RECORD_SCHEMA,
        "stage": STAGE,
        "created_at": created_at,
        "code": commit["code"],
        "checkout_head": commit["checkout_head"],
        "seed": seed,
        "config_hash_version": CONFIG_HASH_VERSION,
        "max_figure_bytes": MAX_FIGURE_BYTES,
        "repro_rel_tol": REPRO_REL_TOL,
        "definition": (
            "per arm: the report's tables are read from the committed battery artifact (all of "
            "its replicates); its figures come from one replicate (seed) re-run with its "
            "trajectories; reproduction compares that replicate's recorded values with the "
            "battery's record of the same seed (identical: bit for bit after the artifact's JSON "
            "round trip; reproduces: identical, or floating-point values within repro_rel_tol)"
        ),
        "complete": complete,
        "arms": list(arms),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(doc), indent=2, allow_nan=False))


def _render_one(
    e: Mapping[str, Any], *, created_at: str, code: str, pdf: bool, i24_observed: bool
) -> dict[str, Any]:
    """One planned arm through :func:`render_arm`, its failure recorded rather than raised."""
    loader: ObservedLoader | None = None
    geometry: dict[str, float] | None = None
    if i24_observed and e.get("kind") == "i24":
        doc = json.loads(Path(e["battery"]).read_text())
        loader, why = _i24_loader(str(doc.get("observed", {}).get("data_hash", "")))
        if loader is None:
            print(f"p22: {e['label']}: no observed field ({why})", flush=True)
        else:
            geometry = _i24_geometry()
    try:
        record = render_arm(
            e, created_at=created_at, code=code, pdf=pdf, observed_loader=loader, geometry=geometry
        )
    except Exception as exc:  # recorded per arm; the others are still rendered
        print(f"p22: {e['label']} render FAILED: {exc!r}", flush=True)
        return {
            **{k: e.get(k) for k in ("label", "battery", "scenario")},
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    rep = record["reproduction"]
    if not rep["checked"]:
        state = "is not checked against"
    elif rep["reproduces"]:
        state = "reproduces"
    else:
        state = "does NOT reproduce"
    sizes = ", ".join(str(f["bytes"]) for f in record["figures"])
    print(
        f"p22: {e['label']} written -> {record['report']} (the figure replicate {state} the "
        f"battery's seed {e['seed']}; figures of {sizes} bytes)",
        flush=True,
    )
    return record


def cmd_render(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text())
    created_at = args.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = source_commit()
    code = str(commit["code"] or "unknown")
    pdf = _pdf_available() if args.pdf == "auto" else args.pdf == "yes"
    out = Path(args.record)
    arms: list[dict[str, Any]] = []
    for e in plan["arms"]:
        if e.get("status") == "planned":
            arms.append(
                _render_one(
                    e, created_at=created_at, code=code, pdf=pdf, i24_observed=args.i24_observed
                )
            )
        else:
            arms.append({k: e.get(k) for k in ("label", "battery", "scenario", "status", "reason")})
        write_record(
            out, arms, seed=int(plan["seed"]), created_at=created_at, commit=commit, complete=False
        )
    write_record(
        out, arms, seed=int(plan["seed"]), created_at=created_at, commit=commit, complete=True
    )
    print(f"p22: record -> {out}", flush=True)
    return 1 if any(a.get("status") == "failed" for a in arms) else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="check every arm and write PLAN.json and pairs.txt")
    p.add_argument(
        "--arm", action="append", help="LABEL:SCENARIO:BATTERY (repeatable; default: DEFAULT_ARMS)"
    )
    p.add_argument("--seed", type=int, default=KEPT_SEED)
    p.add_argument("--out", default="runs/p22")
    p.add_argument("--reports-root", default="docs/reports")
    r = sub.add_parser("ring-run", help="run the planned ring arm's seed")
    r.add_argument("--plan", required=True)
    g = sub.add_parser("render", help="write every planned arm's report and the record")
    g.add_argument("--plan", required=True)
    g.add_argument("--record", default="artifacts/p22_reports.json")
    g.add_argument("--created-at", default=None, help="ISO timestamp (default: now, UTC)")
    g.add_argument("--pdf", choices=("auto", "yes", "no"), default="auto")
    g.add_argument(
        "--i24-observed",
        action="store_true",
        help="add the I-24 MOTION field beside the replicate's (reads data/i24motion/processed)",
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "ring-run":
        return cmd_ring_run(args)
    return cmd_render(args)


if __name__ == "__main__":
    raise SystemExit(main())
