"""The corridor battery artifact's labelled link-hour tables (WP-63).

``geh.pooled_values`` is a flat list; WP-59 had to invert GEH to recover the
simulated counts and infer the list's order from the code. The artifact now
carries, per seed, one labelled row per GEH (station, position, hour, local
clock, observed and simulated volume, GEH) and a per-station-hour summary over
the seeds. No SUMO here: ``build_artifact`` is fed replicates scored on the
hand-counted fixture of ``test_validation_observed`` (every station-hour a
different count), so every number below is known before the code runs.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.test_validation.test_validation_observed import (
    TABLE_ROWS,
    TABLE_SECOND_DEPARTS_S,
    TABLE_SECOND_SIM,
    table_frame,
    table_payload,
    table_scores,
)
from validation.battery import insertion_stats, json_safe, weave_exit_summary
from validation.metrics import Metrics, geh, geh_pass_fraction
from validation.observed import ObservedCorridor, ObservedScores

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Keys the link-hour tables add; everything else in the artifact predates them.
ROW_KEYS = {"station", "x_ref_m", "window_start_s", "clock", "obs_veh_h", "sim_veh_h", "geh"}
POOLED_ROW_KEYS = {
    "station",
    "x_ref_m",
    "window_start_s",
    "clock",
    "obs_veh_h",
    "n_seeds",
    "sim_veh_h_mean",
    "sim_veh_h_min",
    "sim_veh_h_max",
    "geh_mean",
    "geh_min",
    "geh_max",
}

#: Hand counts of the two replicates, in table order (the observed fixture).
SIM_BY_SEED = (tuple(row[5] for row in TABLE_ROWS), TABLE_SECOND_SIM)


def _load_script() -> ModuleType:
    """Import ``scripts/corridor_battery.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "corridor_battery.py"
    spec = importlib.util.spec_from_file_location("_corridor_battery_link_hours", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scores() -> list[ObservedScores]:
    """Two replicates of the hand-counted fixture with different counts."""
    return [table_scores(), table_scores(table_frame(TABLE_SECOND_DEPARTS_S))]


def _build(tmp_path: Path, scores: list[ObservedScores]) -> dict[str, Any]:
    """``build_artifact`` on ``scores`` with fixed, hand-written everything else."""
    battery = _load_script()
    tmp_path.mkdir(parents=True, exist_ok=True)
    scenario = tmp_path / "table_corridor.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "name": "table_corridor",
                "tier": "micro",
                "network": {
                    "kind": "corridor",
                    "length_m": 1000.0,
                    "lanes": 1,
                    "inflow": [[0.0, 0.2]],
                },
                "sim": {"duration_s": 7200.0, "step_length_s": 0.5, "output_hz": 1.0},
                "seed": 11,
                "replicates": len(scores),
            },
            sort_keys=False,
        )
    )
    metrics = Metrics(
        **{
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        }
    )
    seeds = list(range(1, len(scores) + 1))
    artifact: dict[str, Any] = battery.build_artifact(
        scenario=str(scenario),
        cfg=battery.load_scenario(scenario),
        profile=battery.get_profile("fhwa_default"),
        seeds=seeds,
        dirs=[tmp_path / str(seed) for seed in seeds],
        metrics_list=[metrics] * len(scores),
        scores_list=scores,
        wave_speeds=[math.nan] * len(scores),
        insertion_list=[insertion_stats({"n_vehicles_planned": 7, "n_vehicles_departed": 7})]
        * len(scores),
        weave_exits=weave_exit_summary([{}] * len(scores)),
        observed=ObservedCorridor.from_dict(table_payload()),
        observations_path="artifacts/observations_table.json",
        criteria_rows=[],
        ring=None,
        x_offset_m=50.0,
        wall_s=1.0,
    )
    return artifact


def _without_tables(artifact: dict[str, Any]) -> dict[str, Any]:
    """The artifact as it was before the tables: their keys, their note and the
    creation time removed."""
    stripped = json.loads(json.dumps(json_safe(artifact), allow_nan=False))
    stripped.pop("created_at")
    for row in stripped["per_seed"]:
        row.pop("link_hours")
    stripped["geh"].pop("link_hours")
    stripped["notes"] = [n for n in stripped["notes"] if not n.startswith("geh.link_hours")]
    return stripped


def test_every_pooled_geh_value_carries_its_station_hour(tmp_path: Path) -> None:
    artifact = _build(tmp_path, _scores())
    per_seed = artifact["per_seed"]
    assert [r["geh"] for s in per_seed for r in s["link_hours"]] == artifact["geh"]["pooled_values"]
    for seed_row in per_seed:
        assert len(seed_row["link_hours"]) == seed_row["n_link_hours"]
        assert all(set(r) == ROW_KEYS for r in seed_row["link_hours"])
        assert [
            (r["station"], r["x_ref_m"], r["window_start_s"], r["clock"])
            for r in seed_row["link_hours"]
        ] == [row[:4] for row in TABLE_ROWS]
    # The artifact is what the battery writes: strict JSON, no NaN.
    json.dumps(json_safe(artifact), allow_nan=False)


def test_simulated_counts_and_geh_are_read_from_the_rows(tmp_path: Path) -> None:
    artifact = _build(tmp_path, _scores())
    for seed_row, sims in zip(artifact["per_seed"], SIM_BY_SEED, strict=True):
        rows = seed_row["link_hours"]
        assert [r["sim_veh_h"] for r in rows] == pytest.approx(sims, abs=1e-9)
        assert [r["obs_veh_h"] for r in rows] == [row[4] for row in TABLE_ROWS]
        for r in rows:
            assert round(geh(r["sim_veh_h"], r["obs_veh_h"]), 4) == r["geh"]
        # the per-seed pass fraction is the one of the labelled GEH values
        gehs = [r["geh"] for r in rows]
        assert seed_row["geh_pass_fraction"] == geh_pass_fraction(gehs, 5.0)


def test_the_pooled_block_summarises_the_per_seed_rows(tmp_path: Path) -> None:
    artifact = _build(tmp_path, _scores())
    block = artifact["geh"]["link_hours"]
    assert block["t0_local"] == "05:30"
    assert block["window_s"] == 3600.0
    assert block["n_seeds"] == 2
    assert "pooled_values" in block["definition"]
    rows = block["rows"]
    assert [(r["station"], r["window_start_s"], r["clock"]) for r in rows] == [
        (row[0], row[2], row[3]) for row in TABLE_ROWS
    ]
    for k, pooled in enumerate(rows):
        assert set(pooled) == POOLED_ROW_KEYS
        seeds = [s["link_hours"][k] for s in artifact["per_seed"]]
        sims = [r["sim_veh_h"] for r in seeds]
        gehs = [r["geh"] for r in seeds]
        assert pooled["n_seeds"] == 2
        assert pooled["obs_veh_h"] == seeds[0]["obs_veh_h"]
        assert pooled["sim_veh_h_mean"] == math.fsum(sims) / 2.0
        assert (pooled["sim_veh_h_min"], pooled["sim_veh_h_max"]) == (min(sims), max(sims))
        # recomputable from the stored per-seed GEH, to the stored precision
        assert pooled["geh_mean"] == round(math.fsum(gehs) / 2.0, 4)
        assert (pooled["geh_min"], pooled["geh_max"]) == (min(gehs), max(gehs))
    assert not any(n.startswith("geh.link_hours") for n in artifact["notes"])


def test_the_tables_change_nothing_that_was_there(tmp_path: Path) -> None:
    """Bit-identical GEH values, pass fractions and CI with and without tables.

    The same replicates written with their tables and as scores that carry
    none (the pre-WP-63 shape) give the same artifact once the new keys are
    removed — ``geh.pooled_values``, ``pass_fraction_pooled``,
    ``pass_fraction_ci``, every per-seed ``geh_pass_fraction`` and
    ``n_link_hours`` included — and those equal the hand-computed GEH.
    """
    scores = _scores()
    with_tables = _build(tmp_path / "new", scores)
    without = _build(tmp_path / "old", [dataclasses.replace(s, link_hours=None) for s in scores])
    a, b = _without_tables(with_tables), _without_tables(without)
    for artifact in (a, b):
        for row in artifact["per_seed"]:
            row.pop("run_dir")
        artifact.pop("scenario")
    assert a == b

    hand = [
        round(math.sqrt(2.0 * (sim - row[4]) ** 2 / (sim + row[4])), 4)
        for sims in SIM_BY_SEED
        for sim, row in zip(sims, TABLE_ROWS, strict=True)
    ]
    assert with_tables["geh"]["pooled_values"] == hand
    assert with_tables["geh"]["pass_fraction_pooled"] == geh_pass_fraction(
        [g for s in scores for g in s.geh_values], 5.0
    )


def test_scores_stored_before_the_table_give_null_tables(tmp_path: Path) -> None:
    """``--criteria-only`` over per-seed files written before WP-63."""
    fresh = _scores()
    stored = []
    for scores in fresh:
        raw = scores.to_dict()
        raw.pop("link_hours")  # the old writer never wrote the key
        stored.append(ObservedScores.from_dict(json.loads(json.dumps(raw))))
    artifact = _build(tmp_path, stored)
    assert [s["link_hours"] for s in artifact["per_seed"]] == [None, None]
    assert artifact["geh"]["link_hours"] is None
    assert any(n.startswith("geh.link_hours is null") for n in artifact["notes"])
    assert artifact["geh"]["pooled_values"] == _build(tmp_path, fresh)["geh"]["pooled_values"]
    json.dumps(json_safe(artifact), allow_nan=False)
