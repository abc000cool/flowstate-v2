"""``scripts/corridor_sweep.py``'s waiting measures and comparison table (WP-105).

The summary fields gain the measures including waiting (appended: the fields
other scripts import keep their order); ``run_records`` joins each run's
``metrics.json`` with its ``meta.json`` collision count; ``--comparison``
writes the protocol §8.3 table, and exits with the reason when a tree lacks a
measure (the archives written before WP-105). Fake trees only; the worker's
own run is covered by ``test_strategy_tune.py``'s end-to-end study.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_scripts import test_corridor_sweep as base

sweep = base.sweep

PRE_WP105_FIELDS = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "mean_tt_s",
    "p90_tt_s",
    "fuel_ml_per_veh_km",
    "wave_count",
    "wave_speed_kmh",
    "wave_amplitude_ms",
)


def test_fields_append_the_waiting_measures() -> None:
    assert sweep.FIELDS[: len(PRE_WP105_FIELDS)] == PRE_WP105_FIELDS
    for f in (
        "mean_tt_incl_waiting_s",
        "p90_tt_incl_waiting_s",
        "total_delay_incl_waiting_veh_h",
        "insertion_delay_veh_h",
        "meter_wait_veh_h",
        "n_censored",
    ):
        assert f in sweep.FIELDS


def _with_collisions(root: Path, n: int = 0) -> None:
    """Add the collision counter to every meta of a ``base._build_tree`` tree."""
    for meta in root.glob("*/*/*/meta.json"):
        m: dict[str, Any] = json.loads(meta.read_text())
        m["n_collisions"] = n
        meta.write_text(json.dumps(m))


def test_run_records_join_metrics_and_collisions(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    base._build_tree(root, with_meta=True)
    records = sweep.run_records(root, base.CELLS, base.SEEDS)
    assert set(records) == set(base.CELLS)
    rec = records["strategy_alinea"][22]
    assert rec["throughput_veh_h"] == base._metrics("strategy_alinea", 22)["throughput_veh_h"]
    assert rec["n_collisions"] is None  # the fake metas predate the counter
    _with_collisions(root)
    assert sweep.run_records(root, base.CELLS, base.SEEDS)["baseline"][11]["n_collisions"] == 0


def test_the_comparison_is_written_for_a_complete_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    base._build_tree(root, with_meta=True)
    _with_collisions(root)
    out = tmp_path / "cmp" / "comparison.json"
    payload = sweep.write_comparison(sweep.run_records(root, base.CELLS, base.SEEDS), out)
    assert set(payload["arms"]) == set(base.CELLS)
    d = payload["arms"]["strategy_alinea"]["vs_baseline_paired"]["total_delay_incl_waiting_veh_h"]
    assert d["mean"] == pytest.approx(10.0) and d["n"] == 3
    assert json.loads(out.read_text()) == payload
    assert "model estimate" in out.with_suffix(".md").read_text()


def test_analyze_only_with_comparison_refuses_an_old_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "tree"
    base._build_tree(root, with_meta=True)
    _with_collisions(root)
    # an archive written before WP-105: no waiting measures in metrics.json
    for p in root.glob("*/*/*/metrics.json"):
        m = json.loads(p.read_text())
        for k in (
            "mean_tt_incl_waiting_s",
            "p90_tt_incl_waiting_s",
            "total_delay_incl_waiting_veh_h",
        ):
            m.pop(k)
        p.write_text(json.dumps(m))
    argv = [
        "corridor_sweep.py",
        "--scenario",
        "unused.yaml",
        "--x-ref",
        "1",
        "--span",
        "0",
        "2",
        "--out",
        str(root),
        "--summary",
        str(tmp_path / "s.json"),
        "--analyze-only",
        "--comparison",
        str(tmp_path / "cmp.json"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="refusing the strategy comparison"):
        sweep.main()
    assert (tmp_path / "s.json").is_file()  # the summary itself is still written
    assert not (tmp_path / "cmp.json").exists()
