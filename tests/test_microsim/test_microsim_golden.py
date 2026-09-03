"""Golden regressions for the micro tier (CLAUDE.md §9).

GOLDEN UPDATE RULE: a change to any file in ``tests/golden/`` must come with a
PR note explaining the physics or code change that moved the numbers (and,
for a SUMO bump, the new pinned version). Never edit a golden by hand — every
value in it is produced by running the pinned engine through
:func:`microsim.run_micro` + :func:`validation.metrics.compute_metrics`, see
``tests/golden/README.md`` and :func:`regenerate` below.

Each golden stores summary statistics only (never trajectories): the
:class:`validation.metrics.Metrics` fields plus the run's vehicle counts and
fuel total, together with the config snapshot, its hash, the seed, and the
package versions that produced it. The tests re-run the same seeded config
and compare:

* config hash — exact (a mismatch means the scenario YAML or the config
  schema changed; the golden then describes a different experiment);
* ``eclipse-sumo`` / ``libsumo`` versions — exact (goldens are per SUMO
  version, CLAUDE.md §9);
* integer counts (vehicles, waves) — exact;
* floating-point statistics — relative tolerance :data:`REL_TOL`.

Why 1e-6 and not exact: SUMO with a fixed seed and step length is
deterministic per version, and the run artifacts are byte-identical across
repeats (``test_microsim_determinism.py``), so the physics is reproduced
exactly. The summary statistics, however, pass through pandas/numpy
reductions (groupby standard deviations, trapezoid sums, percentile
interpolation) whose floating-point summation order may change between
numpy/pandas releases at the ~1e-15 level. 1e-6 is a million times looser
than that and a million times tighter than any physics change of interest.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from flowstate_core.config import ScenarioConfig, config_hash
from microsim import RunPaths, load_scenario, run_micro
from validation.metrics import compute_metrics

pytestmark = pytest.mark.integration

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
GOLDEN_SCHEMA_VERSION = 1

#: Relative tolerance on floating-point summary statistics (see module docstring).
REL_TOL = 1e-6

#: Integer statistics compared exactly.
EXACT_KEYS = frozenset({"wave_count", "n_vehicles_planned", "n_vehicles_departed"})

#: Golden cases: name -> (scenario YAML name, config overrides applied in
#: :func:`case_config`). Overrides are recorded in the golden file.
CASES: dict[str, tuple[str, dict[str, float]]] = {
    "ring_sugiyama": ("ring_sugiyama", {}),
    # The CLAUDE.md §9 "corridor_10km 2-min smoke run": the versioned scenario
    # with only its duration shortened to two simulated minutes.
    "corridor_10km_smoke": ("corridor_10km", {"sim.duration_s": 120.0}),
}


def case_config(case: str) -> ScenarioConfig:
    """Scenario config for a golden case: the versioned YAML plus overrides.

    Args:
        case: Key of :data:`CASES`.

    Returns:
        The config whose hash the golden file records.
    """
    scenario, overrides = CASES[case]
    cfg = load_scenario(scenario).model_copy(deep=True)
    for dotted, value in overrides.items():
        block, field = dotted.split(".", 1)
        setattr(getattr(cfg, block), field, value)
    return cfg


def _json_float(value: float) -> float | None:
    """NaN → ``None`` so the golden file is strict JSON."""
    return None if isinstance(value, float) and math.isnan(value) else value


def summarize(paths: RunPaths) -> dict[str, Any]:
    """Summary statistics of one completed micro run, in golden-file layout.

    Args:
        paths: Artifact paths of the run.

    Returns:
        ``config_hash``, ``seed``, ``sumo_seed``, ``tier``, ``seeded``,
        ``versions``, ``config`` (snapshot), ``run`` (counts + fuel total)
        and ``metrics`` (:class:`validation.metrics.Metrics` as a dict).
    """
    meta = json.loads(paths.meta.read_text())
    metrics = compute_metrics(paths.run_dir)
    return {
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "sumo_seed": meta["sumo_seed"],
        "tier": meta["tier"],
        "seeded": meta["seeded"],
        "versions": meta["versions"],
        "config": meta["config"],
        "run": {
            "n_vehicles_planned": meta["n_vehicles_planned"],
            "n_vehicles_departed": meta["n_vehicles_departed"],
            "fuel_total_ml": _json_float(meta["fuel_total_ml"]),
        },
        "metrics": {k: _json_float(v) for k, v in dataclasses.asdict(metrics).items()},
    }


def _assert_stats_match(section: str, expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare one golden section: exact for ints/None, ``REL_TOL`` for floats."""
    assert set(actual) == set(expected), f"{section}: key set changed"
    for key, exp in expected.items():
        act = actual[key]
        label = f"{section}.{key}"
        if exp is None:
            assert act is None, f"{label}: golden NaN, got {act!r}"
        elif key in EXACT_KEYS:
            assert act == exp, f"{label}: golden {exp!r}, got {act!r}"
        else:
            assert act == pytest.approx(exp, rel=REL_TOL), (
                f"{label}: golden {exp!r}, got {act!r} (rel tol {REL_TOL:g})"
            )


def assert_matches_golden(golden: dict[str, Any], actual: dict[str, Any]) -> None:
    """Assert a fresh run summary reproduces a golden file.

    Args:
        golden: Parsed golden JSON.
        actual: Output of :func:`summarize` for the re-run.
    """
    assert golden["schema_version"] == GOLDEN_SCHEMA_VERSION
    for dist in ("eclipse-sumo", "libsumo"):
        assert actual["versions"][dist] == golden["versions"][dist], (
            f"{dist} {actual['versions'][dist]} differs from the golden's "
            f"{golden['versions'][dist]}: goldens are per SUMO version (CLAUDE.md §9); "
            "regenerate per tests/golden/README.md and explain the bump in the PR note"
        )
    assert actual["config_hash"] == golden["config_hash"], (
        "config hash changed: the scenario YAML or the config schema no longer "
        "describes the golden experiment; regenerate per tests/golden/README.md "
        "with a PR note explaining the change"
    )
    for key in ("seed", "sumo_seed", "tier", "seeded"):
        assert actual[key] == golden[key], key
    _assert_stats_match("run", golden["run"], actual["run"])
    _assert_stats_match("metrics", golden["metrics"], actual["metrics"])


def regenerate(case: str, work_root: Path) -> Path:
    """Re-run a golden case on the pinned engine and rewrite its golden file.

    Args:
        case: Key of :data:`CASES`.
        work_root: Scratch run-tree root for the SUMO artifacts.

    Returns:
        Path of the written golden file.
    """
    scenario, overrides = CASES[case]
    cfg = case_config(case)
    paths = run_micro(cfg, cfg.seed, work_root)
    payload: dict[str, Any] = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "case": case,
        "scenario": scenario,
        "overrides": overrides,
        "producer": "microsim.run_micro + validation.metrics.compute_metrics",
        "regenerate": (
            f"uv run --no-sync python tests/test_microsim/test_microsim_golden.py "
            f"--regenerate {case}"
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tolerance": {"relative": REL_TOL, "exact": sorted(EXACT_KEYS)},
        **summarize(paths),
    }
    out = GOLDEN_DIR / f"{case}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_summary_reproduced(case: str, tmp_path: Path) -> None:
    """Fixed (config, seed) reproduces the stored summary statistics."""
    golden_path = GOLDEN_DIR / f"{case}.json"
    golden = json.loads(golden_path.read_text())
    cfg = case_config(case)
    assert config_hash(cfg) == golden["config_hash"], (
        f"{golden_path.name}: config hash {config_hash(cfg)} != golden "
        f"{golden['config_hash']} — scenario YAML or config schema changed; "
        "regenerate per tests/golden/README.md with a PR note"
    )
    paths = run_micro(cfg, cfg.seed, tmp_path)
    assert_matches_golden(golden, summarize(paths))


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--regenerate <case> [<case> ...]`` (or ``all``)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--regenerate",
        nargs="+",
        metavar="CASE",
        required=True,
        help=f"golden case(s) to regenerate: {', '.join(sorted(CASES))} or 'all'",
    )
    args = parser.parse_args(argv)
    cases = sorted(CASES) if args.regenerate == ["all"] else args.regenerate
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        parser.error(f"unknown case(s) {unknown}; choose from {sorted(CASES)}")
    with tempfile.TemporaryDirectory(prefix="flowstate-golden-") as tmp:
        for case in cases:
            out = regenerate(case, Path(tmp))
            print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
