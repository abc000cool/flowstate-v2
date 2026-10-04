"""Uncertainty runs over driver behaviour and demand (WP-106, Frisco plan Stage 1 item 11).

docs/FRISCO_PROTOCOL.md §8.5: the baseline and the best setting of each
strategy are re-run with the driver settings and the demand varied within
their plausible ranges — a Latin hypercube of parameter samples (default
10), each with its own seeds (default 5) — and an effect is called robust only
if its sign holds in at least 90 % of the samples. The parameter space, the
sampler, how a sample changes a scenario and the aggregation are
:mod:`validation.uncertainty`; this script runs the design.

Every run goes through ``scripts/corridor_sweep.py``'s own worker
(``_worker``: ``microsim.runner.run_micro`` then
``validation.metrics.compute_metrics`` with the same ``--x-ref``/``--span``),
and every arm is built by its ``cell_config`` (penetration, compliance,
controller, strategy), so the metrics are exactly the ones a sweep records,
over the same window. An arm is ``NAME key=value ...`` with the keys
``strategy`` (default ``none``), ``controller``, ``penetration``,
``compliance`` (default 1.0), ``rho_target_veh_km`` (ALINEA strategies) and
``override`` — a YAML/JSON file with an ``av`` mapping merged into the arm
(e.g. a tuned controller's ``controller_params``). Overrides may touch ``av``
only: anything else would change what the arms share (protocol §8.1). The
``baseline`` arm (no vehicles controlled, strategy ``none``) is always run.

Run tree (``--out``)::

    DESIGN.json                         the design: space, samples, seeds, arms, hashes
    populations/idm_<key>.json          derived driver populations (mean T / v0 scaled)
    <sample>/scenario.yaml              the sample's scenario variant (before any arm)
    <sample>/<arm>/<config hash>/<seed>/metrics.json (+ meta.json)
    uncertainty.json, uncertainty.md    the aggregate (validation.uncertainty)

Resumable: a run whose ``metrics.json`` exists is skipped; a ``DESIGN.json``
from different inputs is refused. ``--plan-only`` prints the run count and
the simulated time it costs and writes nothing; ``--analyze-only``
re-aggregates an existing tree. Seeds nest in samples
(``validation.uncertainty.run_seeds``), never the sweep's evaluation seeds.
The commit and ``code_dirty`` over this script's code paths are recorded.

Example (a rehearsal: below the §8.5 minimum, said so in the outputs)::

    uv run --no-sync python scripts/uncertainty_runs.py --scenario scenarios/X.yaml \\
        --arm vsl strategy=vsl --arm fs10 controller=follower_stopper penetration=0.10 \\
        --samples 4 --seeds 2 --x-ref 11027 --span 1110 11027 --procs 8 --out runs/X_uncertainty
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corridor_sweep import FIELDS, _done, _worker, cell_config

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.strategies import STRATEGIES, needs_target
from validation.uncertainty import (
    DEFAULT_BASELINE,
    METRIC_LABELS,
    PARAMETER_KINDS,
    PROTOCOL_MIN_SAMPLES,
    PROTOCOL_MIN_SEEDS,
    ParameterSpace,
    RunRecord,
    Sample,
    UncertaintyResult,
    aggregate,
    apply,
    default_space,
    file_sha256,
    run_seeds,
    sample_space,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make ``code_dirty`` true: this script, the
#: sweep worker it runs and the packages they import (the convention of
#: scripts/data_quality_report.py and tests/test_scripts/test_code_dirty.py — a
#: rewritten data file is not dirty code).
CODE_PATHS = (
    "scripts/uncertainty_runs.py",
    "scripts/corridor_sweep.py",
    "packages/validation",
    "packages/microsim",
    "packages/controllers",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

DESIGN_SCHEMA = "flowstate.uncertainty_design/1"
DESIGN_NAME = "DESIGN.json"
JSON_NAME = "uncertainty.json"
MARKDOWN_NAME = "uncertainty.md"
POPULATION_DIR = "populations"

ARM_KEYS = ("strategy", "controller", "penetration", "compliance", "rho_target_veh_km", "override")
ARM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

#: The metrics in report order: the sweep's own FIELDS first, then the other
#: fields validation.metrics records; any further numeric field follows sorted.
METRIC_ORDER = (*FIELDS, *(k for k in METRIC_LABELS if k not in FIELDS))


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


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _merge(into: dict[str, Any], patch: dict[str, Any]) -> None:
    """Deep-merge ``patch`` into ``into`` (mappings merge, anything else replaces)."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(into.get(k), dict):
            _merge(into[k], v)
        else:
            into[k] = json.loads(json.dumps(v))


@dataclass(frozen=True)
class Arm:
    """One arm of the design (the baseline is ``Arm(DEFAULT_BASELINE)``)."""

    name: str
    strategy: str = "none"
    controller: str | None = None
    penetration: float = 0.0
    compliance: float = 1.0
    rho_target_veh_km: float | None = None
    override: str | None = None
    override_sha256: str | None = None
    override_av: dict[str, Any] = field(default_factory=dict)

    def config(self, doc: dict[str, Any]) -> dict[str, Any]:
        """The arm's configuration from a serialized scenario (not modified)."""
        cfg = cell_config(
            doc,
            self.penetration,
            self.compliance,
            self.controller,
            self.strategy,
            self.rho_target_veh_km,
        )
        if self.override_av:
            _merge(cfg.setdefault("av", {}), self.override_av)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        """JSON form (the design records it)."""
        return {
            "name": self.name,
            "strategy": self.strategy,
            "controller": self.controller,
            "penetration": self.penetration,
            "compliance": self.compliance,
            "rho_target_veh_km": self.rho_target_veh_km,
            "override": self.override,
            "override_sha256": self.override_sha256,
            "override_av": self.override_av,
        }


def parse_arm(tokens: Sequence[str]) -> Arm:
    """``NAME key=value ...`` → :class:`Arm` (module docstring)."""
    if not tokens:
        raise ValueError("--arm needs a name")
    name, *pairs = tokens
    if not ARM_NAME.match(name):
        raise ValueError(f"arm name {name!r}: letters, digits and _ . + - only")
    if name == DEFAULT_BASELINE:
        raise ValueError(
            f"{DEFAULT_BASELINE!r} is the do-nothing arm, always run; pick another name"
        )
    kv: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or key not in ARM_KEYS:
            raise ValueError(f"arm {name}: {pair!r} is not key=value with a key of {ARM_KEYS}")
        if key in kv:
            raise ValueError(f"arm {name}: {key} given twice")
        kv[key] = value
    strategy = kv.get("strategy", "none")
    if strategy not in STRATEGIES:
        raise ValueError(f"arm {name}: strategy {strategy!r} not in {STRATEGIES}")
    controller = kv.get("controller") or None
    penetration = float(kv.get("penetration", 0.0))
    compliance = float(kv.get("compliance", 1.0))
    rho = float(kv["rho_target_veh_km"]) if "rho_target_veh_km" in kv else None
    if controller is not None and penetration <= 0.0:
        raise ValueError(f"arm {name}: a controller needs penetration > 0")
    if penetration > 0.0 and controller is None:
        raise ValueError(f"arm {name}: penetration > 0 needs a controller")
    if needs_target(strategy) and rho is None:
        raise ValueError(
            f"arm {name}: strategy {strategy} needs rho_target_veh_km (the corridor's per-lane "
            "critical density, e.g. rho_c of its FD artifact); there is no default"
        )
    override = kv.get("override")
    override_av: dict[str, Any] = {}
    override_sha: str | None = None
    if override is not None:
        path = Path(override)
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or set(raw) != {"av"} or not isinstance(raw["av"], dict):
            raise ValueError(
                f"arm {name}: override {override} must be a mapping with the single key 'av' "
                "(anything else would change what the arms share, protocol §8.1)"
            )
        override_av = raw["av"]
        override_sha = file_sha256(path)
    arm = Arm(
        name=name,
        strategy=strategy,
        controller=controller,
        penetration=penetration,
        compliance=compliance,
        rho_target_veh_km=rho,
        override=override,
        override_sha256=override_sha,
        override_av=override_av,
    )
    if strategy == "none" and controller is None and not override_av:
        raise ValueError(f"arm {name}: identical to the baseline")
    return arm


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    ap = argparse.ArgumentParser(
        prog="python scripts/uncertainty_runs.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--scenario", type=Path, help="base scenario YAML")
    ap.add_argument(
        "--arm",
        action="append",
        nargs="+",
        default=[],
        metavar="TOKEN",
        help="NAME key=value ... (repeatable); the baseline is always run",
    )
    ap.add_argument("--samples", type=int, default=PROTOCOL_MIN_SAMPLES, help="parameter samples")
    ap.add_argument("--seeds", type=int, default=PROTOCOL_MIN_SEEDS, help="seeds per sample")
    ap.add_argument(
        "--seed", type=int, default=None, help="design seed (default: the scenario's seed)"
    )
    ap.add_argument(
        "--parameters",
        nargs="+",
        choices=PARAMETER_KINDS,
        default=None,
        help="kinds to vary (default: every kind that applies to the scenario)",
    )
    ap.add_argument(
        "--heavy-range", type=float, nargs=2, metavar=("LO", "HI"), help="measured truck share"
    )
    ap.add_argument("--heavy-source", default=None, help="where --heavy-range comes from")
    ap.add_argument(
        "--set-range",
        action="append",
        nargs=4,
        default=[],
        metavar=("KIND", "LO", "HI", "SOURCE"),
        help="replace a kind's range; SOURCE (required) says where it comes from",
    )
    ap.add_argument(
        "--centre",
        choices=("measured", "configured"),
        default="measured",
        help="driver ranges: the protocol §7.2 measured range (default), or that range "
        "narrowed to the configured mean ± 1 sd",
    )
    ap.add_argument("--x-ref", type=float, help="throughput cross-section [m]")
    ap.add_argument("--span", type=float, nargs=2, metavar=("LO", "HI"), help="analysed span [m]")
    ap.add_argument("--headline", default=None, help="metric of the plain-language headline")
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--out", required=True, type=Path, help="run tree root")
    ap.add_argument("--summary", type=Path, default=None, help="also write the JSON here (+ .md)")
    ap.add_argument("--keep-trajectories", action="store_true")
    ap.add_argument("--plan-only", action="store_true", help="print the plan; write nothing")
    ap.add_argument("--analyze-only", action="store_true", help="re-aggregate an existing tree")
    return ap


def build_space(args: argparse.Namespace, base: ScenarioConfig) -> ParameterSpace:
    """The parameter space from the scenario and the range options."""
    heavy = (float(args.heavy_range[0]), float(args.heavy_range[1])) if args.heavy_range else None
    space = default_space(
        base,
        heavy_range=heavy,
        heavy_source=args.heavy_source,
        kinds=args.parameters,
        centre=args.centre,
    )
    for kind, lo, hi, source in args.set_range:
        space = space.with_range(kind, float(lo), float(hi), source)
    return space


def design_inputs(
    args: argparse.Namespace, base: ScenarioConfig, space: ParameterSpace, arms: list[Arm]
) -> dict[str, Any]:
    """Everything the design is a function of (its key covers exactly this)."""
    return {
        "scenario": str(args.scenario),
        "scenario_sha256": file_sha256(args.scenario),
        "base_config_hash": config_hash(base),
        "design_seed": args.seed if args.seed is not None else base.seed,
        "n_samples": args.samples,
        "seeds_per_sample": args.seeds,
        "space": space.to_dict(),
        "arms": [a.to_dict() for a in arms],
        "metrics_args": {"x_ref": float(args.x_ref), "span": [float(v) for v in args.span]},
    }


def plan_lines(
    n_samples: int, n_seeds: int, arms: list[Arm], duration_s: float, space: ParameterSpace
) -> list[str]:
    """The plan and its cost in simulated time (no money is guessed)."""
    n_arms = len(arms) + 1
    n_runs = n_samples * n_seeds * n_arms
    lines = [
        f"plan: {n_samples} samples × {n_seeds} seeds × {n_arms} arms "
        f"(baseline{''.join(' + ' + a.name for a in arms)}) = {n_runs} runs",
        f"cost: {n_runs} runs × {duration_s:.0f} s simulated = {n_runs * duration_s / 3600.0:.1f} "
        "simulated hours (wall-clock depends on the machine and is not estimated here)",
    ]
    if n_samples < PROTOCOL_MIN_SAMPLES or n_seeds < PROTOCOL_MIN_SEEDS:
        lines.append(
            f"below the docs/FRISCO_PROTOCOL.md §8.5 minimum ({PROTOCOL_MIN_SAMPLES} samples × "
            f"{PROTOCOL_MIN_SEEDS} seeds): a rehearsal, labelled as one in the outputs"
        )
    for p in space.parameters:
        lines.append(
            f"  {p.name}: {p.low:.4g} – {p.high:.4g}{' (assumed)' if p.assumed else ''}; {p.source}"
        )
    return lines


def make_design(
    args: argparse.Namespace, base: ScenarioConfig, space: ParameterSpace, arms: list[Arm]
) -> dict[str, Any]:
    """Sample, apply, hash and write the per-sample scenario variants."""
    out: Path = args.out
    inputs = design_inputs(args, base, space, arms)
    key = _key(inputs)
    design_path = out / DESIGN_NAME
    if design_path.is_file():
        stored = json.loads(design_path.read_text())
        if stored.get("design_key") != key:
            raise SystemExit(
                f"{design_path} holds a different design (key {stored.get('design_key')} against "
                f"{key}): use a new --out, or rerun with the inputs it records"
            )
    seed = int(inputs["design_seed"])
    samples = sample_space(space, args.samples, seed)
    seeds = run_seeds(seed, args.samples, args.seeds)
    pop_dir = out / POPULATION_DIR
    sample_rows: list[dict[str, Any]] = []
    for sample, sample_seeds in zip(samples, seeds, strict=True):
        cfg = apply(sample, base, population_dir=pop_dir)
        cfg.to_yaml(out / sample.sample_id / "scenario.yaml")
        doc = json.loads(cfg.model_dump_json())
        arm_hashes = {
            arm.name: config_hash(ScenarioConfig.model_validate(arm.config(doc)))
            for arm in [Arm(DEFAULT_BASELINE), *arms]
        }
        sample_rows.append(
            {
                **sample.to_dict(),
                "seeds": sample_seeds,
                "config_hash": config_hash(cfg),
                "idm_calibration": cfg.fleet.idm_calibration,
                "arms": arm_hashes,
            }
        )
    design = {
        "schema": DESIGN_SCHEMA,
        "experiment": out.name,
        "design_key": key,
        **inputs,
        "baseline": DEFAULT_BASELINE,
        "headline": args.headline,
        "duration_s": base.sim.duration_s,
        "samples": sample_rows,
        "provenance": (
            json.loads(design_path.read_text())["provenance"]
            if design_path.is_file()
            else {"created_at": _now(), "code": git_head(), "code_dirty": git_dirty()}
        ),
    }
    out.mkdir(parents=True, exist_ok=True)
    design_path.write_text(json.dumps(design, indent=2))
    return design


def pending_runs(
    root: Path, design: dict[str, Any], arms: list[Arm], keep: bool
) -> tuple[int, list[tuple[str, dict[str, Any], int, dict[str, Any], str, bool]]]:
    """``(total, pending)``: payloads for ``corridor_sweep._worker``."""
    metrics_args = {
        "x_ref": float(design["metrics_args"]["x_ref"]),
        "span": tuple(float(v) for v in design["metrics_args"]["span"]),
    }
    total = 0
    pending: list[tuple[str, dict[str, Any], int, dict[str, Any], str, bool]] = []
    for row in design["samples"]:
        sid = row["sample_id"]
        cfg = ScenarioConfig.from_yaml(root / sid / "scenario.yaml")
        doc = json.loads(cfg.model_dump_json())
        for arm in [Arm(DEFAULT_BASELINE), *arms]:
            cfg_json = arm.config(doc)
            chash = config_hash(ScenarioConfig.model_validate(cfg_json))
            if chash != row["arms"][arm.name]:
                raise SystemExit(f"{sid}/{arm.name}: config hash {chash} differs from the design")
            for seed in row["seeds"]:
                total += 1
                cell = f"{sid}/{arm.name}"
                if not _done(root, cell, chash, int(seed)):
                    pending.append((cell, cfg_json, int(seed), metrics_args, str(root), keep))
    return total, pending


def execute(
    pending: list[tuple[str, dict[str, Any], int, dict[str, Any], str, bool]], procs: int
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
            cell, seed, ok, err = _worker(payload)
            report(i, cell, seed, ok, err)
    elif pending:
        with mp.get_context("spawn").Pool(min(procs, len(pending))) as pool:
            for i, (cell, seed, ok, err) in enumerate(
                pool.imap_unordered(_worker, pending), start=1
            ):
                report(i, cell, seed, ok, err)
    print(f"runs done in {time.perf_counter() - t0:.0f} s; {n_fail} failed", flush=True)
    return n_fail


def collect(root: Path, design: dict[str, Any]) -> tuple[list[RunRecord], list[dict[str, Any]]]:
    """Run records from the tree, and the expected runs that have none."""
    from validation.battery import collision_count

    records: list[RunRecord] = []
    missing: list[dict[str, Any]] = []
    for row in design["samples"]:
        sid = row["sample_id"]
        for arm, chash in row["arms"].items():
            for seed in row["seeds"]:
                p = root / sid / arm / chash / str(seed) / "metrics.json"
                if not p.is_file():
                    missing.append({"sample_id": sid, "arm": arm, "seed": int(seed)})
                    continue
                raw = json.loads(p.read_text())
                metrics = {
                    k: float(v)
                    for k, v in raw.items()
                    if isinstance(v, int | float) and not isinstance(v, bool)
                }
                meta = p.with_name("meta.json")
                n_coll = collision_count(json.loads(meta.read_text())) if meta.is_file() else None
                records.append(RunRecord(arm, sid, int(seed), metrics, n_coll))
    return records, missing


def analyze(root: Path, summary: Path | None, headline: str | None) -> UncertaintyResult:
    """Aggregate the tree with :func:`validation.uncertainty.aggregate`; write the outputs."""
    design = json.loads((root / DESIGN_NAME).read_text())
    records, missing = collect(root, design)
    space = ParameterSpace.from_dict(design["space"])
    samples = [Sample.from_dict(row) for row in design["samples"]]
    provenance = {
        "experiment": design["experiment"],
        "scenario": design["scenario"],
        "scenario_sha256": design["scenario_sha256"],
        "base_config_hash": design["base_config_hash"],
        "design_key": design["design_key"],
        "design_seed": design["design_seed"],
        "seeds_per_sample": design["seeds_per_sample"],
        "seeds": {row["sample_id"]: row["seeds"] for row in design["samples"]},
        "sample_config_hashes": {row["sample_id"]: row["config_hash"] for row in design["samples"]},
        "arm_config_hashes": {row["sample_id"]: row["arms"] for row in design["samples"]},
        "arms": design["arms"],
        "metrics_args": design["metrics_args"],
        "metrics_source": "scripts/corridor_sweep.py _worker (validation.metrics.compute_metrics)",
        "design_provenance": design["provenance"],
        "analysed_at": _now(),
        "code": git_head(),
        "code_dirty": git_dirty(),
    }
    result = aggregate(
        records,
        baseline=design["baseline"],
        sample_ids=[s.sample_id for s in samples],
        preferred_order=METRIC_ORDER,
        headline=headline or design.get("headline"),
        space=space,
        samples=samples,
        provenance=provenance,
        missing=missing,
    )
    text = result.to_json()
    (root / JSON_NAME).write_text(text)
    (root / MARKDOWN_NAME).write_text(result.to_markdown())
    if summary is not None:
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(text)
        summary.with_suffix(".md").write_text(result.to_markdown())
    return result


def print_result(result: UncertaintyResult, root: Path) -> None:
    """The console summary."""
    print(f"aggregate → {root / JSON_NAME}, {root / MARKDOWN_NAME}")
    if not result.meets_protocol_minimum:
        print(
            f"REHEARSAL: {len(result.sample_ids)} samples, at least "
            f"{result.min_seeds_per_sample} seeds each (§8.5 asks for {PROTOCOL_MIN_SAMPLES} × "
            f"{PROTOCOL_MIN_SEEDS})"
        )
    for arm in result.arms[1:]:
        print(result.headline_sentence(arm))
    flag = result.zero_collisions
    print(
        "collisions: "
        + (
            "PASS — zero in every run"
            if flag is True
            else "FAIL"
            if flag is False
            else "NOT RECORDED"
        )
    )
    if result.missing:
        print(f"missing runs: {len(result.missing)}")


def _analyze_or_exit(root: Path, summary: Path | None, headline: str | None) -> UncertaintyResult:
    """:func:`analyze`, a refusal (no baseline run, an unknown headline) as a clean exit."""
    try:
        return analyze(root, summary, headline)
    except ValueError as exc:
        raise SystemExit(f"analysis refused: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    root: Path = args.out
    if args.analyze_only:
        if not (root / DESIGN_NAME).is_file():
            raise SystemExit(f"{root / DESIGN_NAME} not found: nothing to analyse")
        print_result(_analyze_or_exit(root, args.summary, args.headline), root)
        return 0
    if args.scenario is None:
        raise SystemExit("--scenario is required (except with --analyze-only)")
    if args.samples < 1 or args.seeds < 1:
        raise SystemExit("--samples and --seeds must be at least 1")
    try:
        arms = [parse_arm(tokens) for tokens in args.arm]
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    names = [a.name for a in arms]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate arm names: {names}")
    base = ScenarioConfig.from_yaml(args.scenario)
    base_doc = json.loads(base.model_dump_json())
    for arm in arms:  # fail before anything runs
        try:
            ScenarioConfig.model_validate(arm.config(base_doc))
        except ValueError as exc:
            raise SystemExit(f"arm {arm.name}: {exc}") from exc
    try:
        space = build_space(args, base)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for line in plan_lines(args.samples, args.seeds, arms, base.sim.duration_s, space):
        print(line)
    if args.plan_only:
        seed = args.seed if args.seed is not None else base.seed
        for s in sample_space(space, args.samples, seed):
            print(f"  {s.sample_id}: " + ", ".join(f"{v.name}={v.value:.4g}" for v in s.values))
        return 0
    if args.x_ref is None or args.span is None:
        raise SystemExit("--x-ref and --span are required to run (the sweep's metric window)")
    design = make_design(args, base, space, arms)
    total, pending = pending_runs(root, design, arms, args.keep_trajectories)
    print(f"{total} runs; {len(pending)} pending", flush=True)
    execute(pending, args.procs)
    print_result(_analyze_or_exit(root, args.summary, args.headline), root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
