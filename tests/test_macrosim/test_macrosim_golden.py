"""Golden regression for the macro (screening) tier (CLAUDE.md §9).

GOLDEN UPDATE RULE: a change to ``tests/golden/macro_corridor.json`` must
come with a PR note explaining the physics or code change that moved the
numbers. Never edit the golden by hand — its values are produced by
:func:`macrosim.run_macro` + :func:`api.results.macro_metrics` (the
screening-tier reduction of :class:`validation.metrics.Metrics` from the
binned ``edges.parquet``), see ``tests/golden/README.md`` and
:func:`regenerate` below.

The case is a seeded (``seeded=True``, labeled) 10 km single-lane corridor
with the ``corridor_10km`` demand steps and a 120 s capacity drop at 7 km on
the ``v1_legacy`` FD preset: the shock produces a backward-propagating queue
so the wave statistics are non-trivial. LWR/CTM is string-stable, so this
run says nothing about emergent waves — it pins the solver, the inflow
boundary, the perturbation cap, the Edie-style field output, and the metric
reduction.

Tolerance: relative 1e-9 on floats, exact on integers. The tier is pure
numpy/numba arithmetic with no RNG in the PDE (the seed only feeds AV
compliance draws, unused here), so the only admissible drift is
floating-point summation order across numpy/numba releases (~1e-15); 1e-9
leaves margin for that and nothing else.
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

from api.results import macro_metrics
from flowstate_core.config import (
    CorridorNetwork,
    PerturbationSpec,
    ScenarioConfig,
    SimSpec,
    config_hash,
)
from macrosim.runner import run_macro

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "macro_corridor.json"
GOLDEN_SCHEMA_VERSION = 1

#: Relative tolerance on floating-point statistics (see module docstring).
REL_TOL = 1e-9

#: Integer statistics compared exactly.
EXACT_KEYS = frozenset({"wave_count", "n_cells"})


def golden_config() -> ScenarioConfig:
    """The macro golden scenario (its hash is recorded in the golden file)."""
    return ScenarioConfig(
        name="macro_corridor_golden",
        tier="macro",
        network=CorridorNetwork(
            length_m=10_000.0,
            lanes=1,
            inflow=[(0.0, 0.45), (120.0, 0.50), (1080.0, 0.40)],
        ),
        sim=SimSpec(duration_s=1200.0, step_length_s=0.5, output_hz=0.2),
        perturbation=PerturbationSpec(
            t_s=600.0, position_m=7000.0, duration_s=120.0, v_drop_ms=25.0
        ),
        seed=42,
        replicates=1,
    )


def _json_float(value: float) -> float | None:
    """NaN → ``None`` so the golden file is strict JSON."""
    return None if isinstance(value, float) and math.isnan(value) else value


def summarize(run_dir: Path) -> dict[str, Any]:
    """Summary statistics of one completed macro run, in golden-file layout.

    Args:
        run_dir: Run directory holding ``edges.parquet`` and ``meta.json``.

    Returns:
        ``config_hash``, ``seed``, ``tier``, ``seeded``, ``versions``,
        ``config`` (snapshot), ``run`` (grid, FD, ledger, clamp flag) and
        ``metrics`` (screening :class:`validation.metrics.Metrics` as a dict).
    """
    meta = json.loads((run_dir / "meta.json").read_text())
    metrics = macro_metrics(run_dir)
    return {
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "tier": meta["tier"],
        "seeded": meta["seeded"],
        "versions": meta["versions"],
        "config": meta["config"],
        "run": {
            "n_cells": meta["grid"]["n_cells"],
            "dx_m": meta["grid"]["dx_m"],
            "dt_s": meta["grid"]["dt_s"],
            "fd_preset": meta["fd"]["preset"],
            "fd_v_f": meta["fd"]["v_f"],
            "fd_w": meta["fd"]["w"],
            "fd_rho_jam": meta["fd"]["rho_jam"],
            "clamped": meta["clamped"],
            "vehicles_in": meta["ledger"]["vehicles_in"],
            "vehicles_out": meta["ledger"]["vehicles_out"],
            "stored_veh": meta["ledger"]["stored_veh"],
        },
        "metrics": {k: _json_float(v) for k, v in dataclasses.asdict(metrics).items()},
    }


def _assert_stats_match(section: str, expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare one golden section: exact for ints/bools/str/None, ``REL_TOL`` for floats."""
    assert set(actual) == set(expected), f"{section}: key set changed"
    for key, exp in expected.items():
        act = actual[key]
        label = f"{section}.{key}"
        if exp is None or isinstance(exp, bool | str) or key in EXACT_KEYS:
            assert act == exp, f"{label}: golden {exp!r}, got {act!r}"
        else:
            assert act == pytest.approx(exp, rel=REL_TOL), (
                f"{label}: golden {exp!r}, got {act!r} (rel tol {REL_TOL:g})"
            )


def assert_matches_golden(golden: dict[str, Any], actual: dict[str, Any]) -> None:
    """Assert a fresh run summary reproduces the golden file.

    Args:
        golden: Parsed golden JSON.
        actual: Output of :func:`summarize` for the re-run.
    """
    assert golden["schema_version"] == GOLDEN_SCHEMA_VERSION
    assert actual["config_hash"] == golden["config_hash"], (
        "config hash changed: golden_config() or the config schema no longer "
        "describes the golden experiment; regenerate per tests/golden/README.md "
        "with a PR note explaining the change"
    )
    for key in ("seed", "tier", "seeded"):
        assert actual[key] == golden[key], key
    _assert_stats_match("run", golden["run"], actual["run"])
    _assert_stats_match("metrics", golden["metrics"], actual["metrics"])


def regenerate(work_root: Path) -> Path:
    """Re-run the golden case and rewrite the golden file.

    Args:
        work_root: Scratch run-tree root for the artifacts.

    Returns:
        Path of the written golden file.
    """
    cfg = golden_config()
    run_dir = run_macro(cfg, cfg.seed, work_root)
    payload: dict[str, Any] = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "case": "macro_corridor",
        "scenario": "golden_config() in tests/test_macrosim/test_macrosim_golden.py",
        "producer": "macrosim.run_macro (v1_legacy FD) + api.results.macro_metrics",
        "regenerate": (
            "uv run --no-sync python tests/test_macrosim/test_macrosim_golden.py --regenerate"
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tolerance": {"relative": REL_TOL, "exact": sorted(EXACT_KEYS)},
        **summarize(run_dir),
    }
    GOLDEN_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    return GOLDEN_PATH


def test_golden_summary_reproduced(tmp_path: Path) -> None:
    """Fixed (config, seed) reproduces the stored screening statistics."""
    golden = json.loads(GOLDEN_PATH.read_text())
    cfg = golden_config()
    assert config_hash(cfg) == golden["config_hash"], (
        f"{GOLDEN_PATH.name}: config hash {config_hash(cfg)} != golden "
        f"{golden['config_hash']} — golden_config() or the config schema changed; "
        "regenerate per tests/golden/README.md with a PR note"
    )
    run_dir = run_macro(cfg, cfg.seed, tmp_path)
    actual = summarize(run_dir)
    assert actual["tier"] == "screening"  # CLAUDE.md §5.6 label
    assert actual["run"]["clamped"] is False
    assert_matches_golden(golden, actual)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--regenerate`` rewrites ``tests/golden/macro_corridor.json``."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--regenerate", action="store_true", required=True)
    parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="flowstate-golden-") as tmp:
        print(f"wrote {regenerate(Path(tmp))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
