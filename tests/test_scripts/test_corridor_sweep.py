"""Tests for the analysis half of ``scripts/corridor_sweep.py``.

No simulation runs here: the sweep tree is faked on disk (``MANIFEST.json``,
one ``metrics.json`` per cell × seed, optionally a ``meta.json`` beside it, the
layout ``<out>/<cell>/<config hash>/<seed>/`` the pipeline archives) and
``analyze`` is called the way ``--analyze-only --allow-partial`` calls it. The
``diagnostics`` block is checked against hand-computed means, 95 % t-intervals
and the unstoppable share; a tree without any ``meta.json`` (the archives
before 2026-09-24) must report ``n_runs_with_meta == 0`` and leave every
existing key of the summary untouched.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "corridor_sweep.py"


def _load_script() -> ModuleType:
    """Import ``scripts/corridor_sweep.py`` by path (``scripts/`` is not a package)."""
    spec = importlib.util.spec_from_file_location("flowstate_corridor_sweep", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sweep = _load_script()

SEEDS = [11, 22, 33]
CELLS = {"baseline": "aaaaaaaaaaaa", "strategy_alinea": "bbbbbbbbbbbb"}
GRID = {
    "baseline": {"penetration": 0.0, "compliance": 1.0, "controller": None, "strategy": "none"},
    "strategy_alinea": {
        "penetration": 0.0,
        "compliance": 1.0,
        "controller": None,
        "strategy": "alinea",
    },
}

#: Per-seed meter counters of the alinea cell: (n_released, n_passed_unstoppable).
METER = {11: (100, 5), 22: (110, 10), 33: (120, 0)}
#: Per-seed weave counters of the alinea cell.
WEAVE = {
    11: {
        "n_entered": 40,
        "n_exited": 30,
        "n_reached_section_exiting": 31,
        "n_forced": 3,
        "n_forced_deferred": 7,
        "n_unfinished": 1,
        "wait_s_mean": 4.0,
        "n_cooperations": 600,
        "mean_follower_decel_ms2": 0.40,
        "n_changer_eased": 200,
        "n_vacated": 220,
        "n_vacate_refused": 20,
        "n_vacate_skipped_no_gap": 4,
        "n_vacate_requests": 240,
        "n_pair_releases": 2,
        "n_missed_exit": 0,
    },
    22: {
        "n_entered": 44,
        "n_exited": 33,
        "n_reached_section_exiting": 34,
        "n_forced": 4,
        "n_forced_deferred": 9,
        "n_unfinished": 0,
        "wait_s_mean": 5.0,
        "n_cooperations": 660,
        "mean_follower_decel_ms2": 0.44,
        "n_changer_eased": 220,
        "n_vacated": 230,
        "n_vacate_refused": 25,
        "n_vacate_skipped_no_gap": 5,
        "n_vacate_requests": 260,
        "n_pair_releases": 3,
        "n_missed_exit": 1,
    },
    33: {
        "n_entered": 48,
        "n_exited": 36,
        "n_reached_section_exiting": 37,
        "n_forced": 5,
        "n_forced_deferred": 11,
        "n_unfinished": 2,
        "wait_s_mean": 6.0,
        "n_cooperations": 720,
        "mean_follower_decel_ms2": 0.48,
        "n_changer_eased": 240,
        "n_vacated": 240,
        "n_vacate_refused": 30,
        "n_vacate_skipped_no_gap": 6,
        "n_vacate_requests": 280,
        "n_pair_releases": 4,
        "n_missed_exit": 2,
    },
}


def _metrics(cell: str, seed: int) -> dict[str, float]:
    """Deterministic, cell- and seed-dependent values for every summary field."""
    offset = 0.0 if cell == "baseline" else 10.0
    return {f: float(100 + i * 7 + (seed % 10) + offset) for i, f in enumerate(sweep.FIELDS)}


def _meta(cell: str, seed: int) -> dict[str, Any]:
    if cell == "baseline":
        return {"tier": "micro", "ramp_meters": [], "weave_sections": []}
    released, passed = METER[seed]
    return {
        "tier": "micro",
        "ramp_meters": [
            {
                "ramp": "hickory_hollow",
                "controller": "alinea",
                "interval_s": 60.0,
                "n_released": released,
                "n_passed_unstoppable": passed,
                "releases_s": [],
                "rates": [],
            }
        ],
        "weave_sections": [{"ramp": "old_hickory", "exit": "bell", **WEAVE[seed]}],
    }


def _build_tree(root: Path, *, with_meta: bool) -> None:
    root.mkdir(parents=True)
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "experiment": root.name,
                "scenario": "scenarios/fake.yaml",
                "base_config_hash": CELLS["baseline"],
                "grid_spec": {
                    "penetration": [],
                    "compliance": [1.0],
                    "controllers": [],
                    "strategies": ["none", "alinea"],
                },
                "rho_target_veh_km": 29.2,
                "metrics_args": {"x_ref": 1.0, "span": [0.0, 2.0]},
                "grid": GRID,
                "cells": CELLS,
                "seeds": SEEDS,
            }
        )
    )
    for cell, chash in CELLS.items():
        for seed in SEEDS:
            d = root / cell / chash / str(seed)
            d.mkdir(parents=True)
            (d / "metrics.json").write_text(json.dumps(_metrics(cell, seed)))
            if with_meta:
                (d / "meta.json").write_text(json.dumps(_meta(cell, seed)))


def _t_ci(values: list[float]) -> tuple[float, float, float]:
    """Hand-computed mean and 95 % t-interval, independent of the script's helper."""
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    mean = float(arr.mean())
    half = float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / math.sqrt(n))
    return mean, mean - half, mean + half


def _assert_ci(entry: dict[str, Any], values: list[float]) -> None:
    mean, lo, hi = _t_ci(values)
    assert entry["n"] == len(values)
    assert entry["underpowered"] is True  # three seeds
    assert entry["mean"] == pytest.approx(mean)
    assert entry["lo95"] == pytest.approx(lo)
    assert entry["hi95"] == pytest.approx(hi)


def test_diagnostics_aggregate_meters_and_weaves(tmp_path: Path) -> None:
    root = tmp_path / "fake_sweep"
    _build_tree(root, with_meta=True)
    summary = sweep.analyze(root, tmp_path / "summary.json", allow_partial=False)
    assert summary["incomplete_cells"] == []
    assert set(summary["cells"]) == set(CELLS)

    # The baseline's metas carry no meter and no section: counted, but empty maps.
    base = summary["cells"]["baseline"]["diagnostics"]
    assert base == {"n_runs_with_meta": 3, "ramp_meters": {}, "weave_sections": {}}

    diag = summary["cells"]["strategy_alinea"]["diagnostics"]
    assert diag["n_runs_with_meta"] == 3
    assert list(diag["ramp_meters"]) == ["hickory_hollow"]
    meter = diag["ramp_meters"]["hickory_hollow"]
    assert meter["controller"] == "alinea"
    released = [float(METER[s][0]) for s in SEEDS]
    passed = [float(METER[s][1]) for s in SEEDS]
    _assert_ci(meter["n_released"], released)
    _assert_ci(meter["n_passed_unstoppable"], passed)
    shares = [p / (r + p) for r, p in zip(released, passed, strict=True)]
    assert shares == pytest.approx([5 / 105, 10 / 120, 0.0])
    _assert_ci(meter["share_passed_unstoppable"], shares)

    assert list(diag["weave_sections"]) == ["old_hickory"]
    weave = diag["weave_sections"]["old_hickory"]
    assert set(weave) == set(sweep.WEAVE_FIELDS)
    for field in sweep.WEAVE_FIELDS:
        _assert_ci(weave[field], [float(WEAVE[s][field]) for s in SEEDS])

    # The artifact on disk is the returned summary.
    assert json.loads((tmp_path / "summary.json").read_text()) == summary


def test_diagnostics_weave_counters_a_meta_predates_are_empty_not_zero() -> None:
    """A weave section written before the follower-cooperation counters
    (2026-09-24, block 3) has no ``n_cooperations``, ``mean_follower_decel_ms2``
    or ``n_changer_eased``, nor the later ``n_vacated``, ``n_vacate_refused``,
    ``n_pair_releases``, ``n_missed_exit``, ``n_vacate_skipped_no_gap`` and
    ``n_vacate_requests``: their intervals are empty
    (``n`` = 0), never a zero mean, and the counters that are there aggregate
    as before. A section with cooperations but ``mean_follower_decel_ms2``
    null (none commanded) contributes to the count and not to the decel; one
    with the cooperation counters but none of the later four leaves those
    four empty."""
    old_meta = {"weave_sections": [{"ramp": "w", **{k: 1.0 for k in sweep.WEAVE_FIELDS[:7]}}]}
    new_meta = {
        "weave_sections": [
            {
                "ramp": "w",
                **{k: 3.0 for k in sweep.WEAVE_FIELDS[:7]},
                "n_cooperations": 0,
                "mean_follower_decel_ms2": None,
                "n_changer_eased": 5,
            }
        ]
    }
    weave = sweep.diagnostics_block([old_meta, new_meta])["weave_sections"]["w"]
    assert set(weave) == set(sweep.WEAVE_FIELDS)
    for field in sweep.WEAVE_FIELDS[:7]:
        _assert_ci(weave[field], [1.0, 3.0])
    # one seed: the mean is that seed's value and the t-interval has no width to give
    for field, value in (("n_cooperations", 0.0), ("n_changer_eased", 5.0)):
        assert weave[field]["n"] == 1
        assert weave[field]["mean"] == value
        assert math.isnan(weave[field]["lo95"]) and math.isnan(weave[field]["hi95"])
    assert weave["mean_follower_decel_ms2"]["n"] == 0
    assert weave["mean_follower_decel_ms2"]["mean"] is None
    for field in (
        "n_vacated",
        "n_vacate_refused",
        "n_pair_releases",
        "n_missed_exit",
        "n_vacate_skipped_no_gap",
        "n_vacate_requests",
    ):
        assert weave[field]["n"] == 0, field
        assert weave[field]["mean"] is None, field


def test_diagnostics_share_skips_seeds_with_no_vehicle() -> None:
    metas = [
        {
            "ramp_meters": [
                {"ramp": "r", "controller": "alinea", "n_released": 0, "n_passed_unstoppable": 0}
            ]
        },
        {
            "ramp_meters": [
                {"ramp": "r", "controller": "alinea", "n_released": 9, "n_passed_unstoppable": 1}
            ]
        },
    ]
    block = sweep.diagnostics_block(metas)
    assert block["n_runs_with_meta"] == 2
    meter = block["ramp_meters"]["r"]
    assert meter["n_released"]["n"] == 2
    share = meter["share_passed_unstoppable"]
    assert share["n"] == 1 and share["mean"] == pytest.approx(0.1)
    assert math.isnan(share["lo95"])  # one value: no interval
    assert block["weave_sections"] == {}


def test_tree_without_meta_reports_zero_and_keeps_existing_keys(tmp_path: Path) -> None:
    with_meta = tmp_path / "with_meta"
    without = tmp_path / "without"
    _build_tree(with_meta, with_meta=True)
    _build_tree(without, with_meta=False)
    s_with = sweep.analyze(with_meta, tmp_path / "a.json", allow_partial=False)
    s_without = sweep.analyze(without, tmp_path / "b.json", allow_partial=False)

    empty = {"n_runs_with_meta": 0, "ramp_meters": {}, "weave_sections": {}}
    for cell, entry in s_without["cells"].items():
        assert entry["diagnostics"] == empty
        expected_keys = ["aggregate", "grid", "config_hash"]
        if cell != "baseline":
            expected_keys.append("vs_baseline_paired")
        expected_keys.append("diagnostics")
        assert list(entry) == expected_keys, cell

    # Everything but ``diagnostics`` is independent of whether a meta exists.
    for s in (s_with, s_without):
        for entry in s["cells"].values():
            del entry["diagnostics"]
    s_with["experiment"] = s_without["experiment"]
    assert s_with == s_without
    assert list(s_without) == [
        "experiment",
        "scenario",
        "base_config_hash",
        "grid_spec",
        "rho_target_veh_km",
        "metrics_args",
        "n_seeds",
        "seeds",
        "incomplete_cells",
        "cells",
    ]
    # Sanity on the paired delta: the alinea cell sits +10 above the baseline on every field.
    for field in sweep.FIELDS:
        d = s_without["cells"]["strategy_alinea"]["vs_baseline_paired"][field]
        assert d["mean"] == pytest.approx(10.0) and d["n"] == 3 and d["resolved"] is True


def test_print_diagnostics_lists_only_cells_with_meta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "fake_sweep"
    _build_tree(root, with_meta=True)
    summary = sweep.analyze(root, tmp_path / "summary.json", allow_partial=False)
    summary["cells"]["baseline"]["diagnostics"]["n_runs_with_meta"] = 0
    sweep.print_diagnostics(summary)
    out = capsys.readouterr().out
    assert "diagnostics baseline" not in out
    assert "diagnostics strategy_alinea (meta in 3/3 runs)" in out
    assert "meter hickory_hollow (alinea): released 110.0 [" in out
    assert "weave old_hickory: entered 44.0 [" in out
    assert "through vacated 230.0 [" in out
    assert "vacate refused 25.0 [" in out
    assert "vacate skipped (no gap) 5.0 [" in out and "vacate requests 260.0 [" in out
    assert "pair releases 3.0 [" in out


def test_a_run_with_metrics_but_no_meta_counts_as_done(tmp_path: Path) -> None:
    """Archives older than 2026-09-24 hold ``metrics.json`` only; a resume must
    not redo them (the diagnostics block simply reports no meta)."""
    d = tmp_path / "cell" / "abc123" / "7"
    d.mkdir(parents=True)
    assert not sweep._done(tmp_path, "cell", "abc123", 7)
    (d / "metrics.json").write_text("{}")
    assert sweep._done(tmp_path, "cell", "abc123", 7)
