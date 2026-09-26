"""The corridor battery artifact's collision keys (WP-94).

The I-94 WB reference battery recorded 15 collisions over 20 seeds in its
``meta.json`` files and nothing downstream printed them. The artifact now
carries each seed's ``n_collisions`` and a pooled ``collisions`` block
(:func:`validation.battery.collision_summary`), additively: every key the
artifact had before is byte-identical with and without them. No SUMO here:
``build_artifact`` is fed the hand-counted observed fixture of
``test_validation_observed`` and hand-written metas, and ``--criteria-only``
runs on a stored tree without a trajectory.
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
from scipy.stats import t as student_t

from tests.test_validation.test_validation_corridor_battery import (
    _load_script as _load_named_script,
)
from tests.test_validation.test_validation_corridor_battery import (
    _observations,
    _scenario,
    _write_scored_replicate,
)
from tests.test_validation.test_validation_observed import table_payload, table_scores
from validation.battery import COLLISION_DEFINITION, insertion_stats, json_safe, weave_exit_summary
from validation.metrics import Metrics
from validation.observed import ObservedCorridor

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Top-level keys of the artifact before WP-94 (``report_path`` is added by
#: ``main`` after ``build_artifact``).
PRE_WP94_KEYS = {
    "schema",
    "created_at",
    "scenario",
    "corridor",
    "config_hash",
    "seeds",
    "replicates",
    "wall_s",
    "versions",
    "criteria_profile",
    "criteria",
    "observations",
    "x_offset_m",
    "insertion",
    "weave_exits",
    "geh",
    "rmspe",
    "wave_speed",
    "metrics_ci",
    "per_seed",
    "ring",
    "notes",
}

#: Per-seed keys before WP-94.
PRE_WP94_SEED_KEYS = {
    "seed",
    "run_dir",
    "geh_pass_fraction",
    "n_link_hours",
    "rmspe",
    "n_speed_cells",
    "criterion_wave_speed_kmh",
    "metrics",
    "insertion",
    "link_hours",
}

SEEDS = (101, 102, 103)


def _load_script() -> ModuleType:
    """Import ``scripts/corridor_battery.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "corridor_battery.py"
    spec = importlib.util.spec_from_file_location("_corridor_battery_collisions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _event(lane: str, pos_m: float) -> dict[str, Any]:
    """A ``meta.json["collisions"][i]`` event as the runner logs it."""
    return {
        "t": 60.0,
        "collider": "f",
        "victim": "l",
        "type": "collision",
        "lane": lane,
        "pos_m": pos_m,
    }


def _metas(with_counter: bool = True) -> list[dict[str, Any]]:
    """Three seeds: 3 collisions on two lanes, none, 1 on a third lane."""
    counts = (3, 0, 1)
    events = (
        [_event("e1_0", 40.5), _event("e1_0", 10.0), _event(":J3_0_0", 2.0)],
        [],
        [_event("e2_1", 12.0)],
    )
    metas: list[dict[str, Any]] = []
    for seed, n, ev, departed in zip(SEEDS, counts, events, (1000, 1000, 500), strict=True):
        meta: dict[str, Any] = {
            "seed": seed,
            "n_vehicles_planned": 1000,
            "n_vehicles_departed": departed,
        }
        if with_counter:
            meta["n_collisions"] = n
            meta["collisions"] = ev
        metas.append(meta)
    return metas


def _build(tmp_path: Path, metas: list[dict[str, Any]] | None) -> dict[str, Any]:
    """``build_artifact`` on the observed fixture with fixed everything else."""
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
                "replicates": len(SEEDS),
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
    artifact: dict[str, Any] = battery.build_artifact(
        scenario="table_corridor",
        cfg=battery.load_scenario(scenario),
        profile=battery.get_profile("fhwa_default"),
        seeds=list(SEEDS),
        dirs=[Path("runs") / str(seed) for seed in SEEDS],
        metrics_list=[metrics] * len(SEEDS),
        scores_list=[table_scores()] * len(SEEDS),
        wave_speeds=[math.nan] * len(SEEDS),
        insertion_list=[insertion_stats({"n_vehicles_planned": 7, "n_vehicles_departed": 7})]
        * len(SEEDS),
        weave_exits=weave_exit_summary([{}] * len(SEEDS)),
        observed=ObservedCorridor.from_dict(table_payload()),
        observations_path="artifacts/observations_table.json",
        criteria_rows=[],
        ring=None,
        x_offset_m=50.0,
        wall_s=1.0,
        **({} if metas is None else {"metas": metas}),
    )
    return artifact


def _strict(artifact: dict[str, Any]) -> dict[str, Any]:
    """The artifact as the battery writes it (strict JSON), without ``created_at``."""
    out: dict[str, Any] = json.loads(json.dumps(json_safe(artifact), allow_nan=False))
    out.pop("created_at")
    return out


def test_per_seed_counts_and_the_pooled_block(tmp_path: Path) -> None:
    artifact = _build(tmp_path, _metas())
    assert [row["n_collisions"] for row in artifact["per_seed"]] == [3, 0, 1]
    block = artifact["collisions"]
    assert block["n_runs"] == 3 and block["n_runs_recorded"] == 3
    assert block["runs_not_recorded"] == []
    assert block["total"] == 4
    assert block["n_runs_with_collisions"] == 2
    assert block["runs_with_collisions"] == [{"run": 101, "n": 3}, {"run": 103, "n": 1}]
    # counts (3, 0, 1): mean 4/3, s² = 7/3, t(0.975, 2)
    half = student_t.ppf(0.975, 2) * math.sqrt(7.0 / 3.0) / math.sqrt(3.0)
    assert block["per_run"]["mean"] == pytest.approx(4.0 / 3.0)
    assert block["per_run"]["lo95"] == pytest.approx(4.0 / 3.0 - half)
    assert block["per_run"]["hi95"] == pytest.approx(4.0 / 3.0 + half)
    assert block["per_run"]["n"] == 3 and block["per_run"]["underpowered"] is True
    # 4 collisions over 1000 + 1000 + 500 departed vehicles
    assert block["rate"] == {
        "per_vehicles": 1000,
        "value": pytest.approx(1.6),
        "n_collisions": 4,
        "n_departed": 2500,
        "n_runs": 3,
    }
    assert block["n_logged"] == 4
    assert [
        (r["lane"], r["edge"], r["n"], r["pos_m_min"], r["pos_m_max"], r["runs"])
        for r in block["locations"]
    ] == [
        ("e1_0", "e1", 2, 10.0, 40.5, [101]),
        (":J3_0_0", ":J3_0", 1, 2.0, 2.0, [101]),
        ("e2_1", "e2", 1, 12.0, 12.0, [103]),
    ]
    assert block["definition"] == COLLISION_DEFINITION
    # beside the other run-health blocks
    keys = list(artifact)
    assert keys.index("collisions") == keys.index("weave_exits") + 1


def test_the_keys_are_additive_and_every_existing_value_is_unchanged(tmp_path: Path) -> None:
    """Byte-identical existing keys with and without collision data.

    The same replicates built without metas (the pre-WP-94 call), with metas
    that carry no counter (runs written before 2026-09-16) and with metas
    that carry collisions give the same JSON for every key the artifact had,
    key by key; the only new keys are ``collisions`` and each seed's
    ``n_collisions``.
    """
    without = _strict(_build(tmp_path / "none", None))
    unrecorded = _strict(_build(tmp_path / "old", _metas(with_counter=False)))
    recorded = _strict(_build(tmp_path / "new", _metas()))
    for artifact in (without, unrecorded, recorded):
        assert set(artifact) == (PRE_WP94_KEYS - {"created_at"}) | {"collisions"}
        for row in artifact["per_seed"]:
            assert set(row) == PRE_WP94_SEED_KEYS | {"n_collisions"}
    for key in PRE_WP94_KEYS - {"created_at", "per_seed"}:
        reference = json.dumps(without[key], indent=2, allow_nan=False)
        assert json.dumps(unrecorded[key], indent=2, allow_nan=False) == reference, key
        assert json.dumps(recorded[key], indent=2, allow_nan=False) == reference, key
    for a, b, c in zip(
        without["per_seed"], unrecorded["per_seed"], recorded["per_seed"], strict=True
    ):
        for key in PRE_WP94_SEED_KEYS:
            reference = json.dumps(a[key], indent=2, allow_nan=False)
            assert json.dumps(b[key], indent=2, allow_nan=False) == reference, key
            assert json.dumps(c[key], indent=2, allow_nan=False) == reference, key
    # the schema is unchanged: the keys are additive (docs/CONTRACTS.md precedent)
    assert recorded["schema"] == "flowstate.corridor_validation/1"


def test_runs_without_the_counter_are_null_never_zero(tmp_path: Path) -> None:
    for metas in (None, _metas(with_counter=False)):
        artifact = _strict(_build(tmp_path / str(metas is None), metas))
        assert artifact["collisions"] is None
        assert [row["n_collisions"] for row in artifact["per_seed"]] == [None, None, None]


def test_a_seed_without_the_counter_is_named_in_the_block(tmp_path: Path) -> None:
    metas = _metas()
    for key in ("n_collisions", "collisions"):
        metas[1].pop(key)
    artifact = _strict(_build(tmp_path, metas))
    assert [row["n_collisions"] for row in artifact["per_seed"]] == [3, None, 1]
    block = artifact["collisions"]
    assert block["n_runs_recorded"] == 2 and block["runs_not_recorded"] == [102]
    assert block["rate"]["n_departed"] == 1500 and block["rate"]["n_runs"] == 2


def test_an_undefined_rate_is_written_as_null(tmp_path: Path) -> None:
    metas = _metas()
    for meta in metas:
        meta["n_vehicles_departed"] = 0
    block = _strict(_build(tmp_path, metas))["collisions"]
    assert block["rate"]["value"] is None and block["rate"]["n_departed"] == 0


def _criteria_only(
    tmp_path: Path, tree: str, metas: list[dict[str, Any]] | None
) -> tuple[ModuleType, list[str]]:
    """A stored, pruned two-seed tree ready for ``--criteria-only``, metas patched in.

    The scenario and observations files are shared by every tree under
    ``tmp_path``, so two trees differ only in their stored run files.
    """
    battery = _load_named_script()
    scenario = tmp_path / "battery_smoke_corridor.yaml"
    if not scenario.is_file():
        _scenario(scenario)
    observations = tmp_path / "observations.json"
    if not observations.is_file():
        _observations(observations)
    out = tmp_path / tree
    cfg = battery.load_scenario(scenario).model_copy(update={"replicates": 2})
    seeds = battery.spawn_seeds(cfg.seed, 2)
    dirs = battery.seed_dirs(out / "runs", cfg, seeds)
    for k, (run_dir, seed) in enumerate(zip(dirs, seeds, strict=True)):
        _write_scored_replicate(
            run_dir, seed=seed, config_hash=battery.config_hash(cfg), weave_sections=[]
        )
        if metas is not None:
            meta = json.loads((run_dir / "meta.json").read_text())
            meta.update(metas[k])
            (run_dir / "meta.json").write_text(json.dumps(meta))

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--criteria-only must not re-simulate")

    battery.run_replicates = _refuse
    argv = [
        "--scenario",
        str(scenario),
        "--observations",
        str(observations),
        "--replicates",
        "2",
        "--procs",
        "1",
        "--out",
        str(out / "runs"),
        "--artifact",
        str(out / "artifacts" / "validation.json"),
        "--report-dir",
        str(out / "report"),
        "--ring-seeds",
        "0",
        "--criteria-only",
    ]
    return battery, argv


def test_criteria_only_reads_collisions_from_the_stored_metas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    metas = [
        {"n_collisions": 2, "collisions": [_event("e1_0", 5.0), _event("e1_0", 9.0)]},
        {"n_collisions": 0, "collisions": []},
    ]
    battery, argv = _criteria_only(tmp_path, "new", metas)
    assert battery.main(argv) == 0
    console = capsys.readouterr().out
    artifact = json.loads((tmp_path / "new" / "artifacts" / "validation.json").read_text())
    assert [row["n_collisions"] for row in artifact["per_seed"]] == [2, 0]
    block = artifact["collisions"]
    assert block["total"] == 2
    seeds = artifact["seeds"]
    assert block["runs_with_collisions"] == [{"run": seeds[0], "n": 2}]
    # _write_scored_replicate departs 500 vehicles a seed: 2 over 1000
    assert block["rate"]["value"] == pytest.approx(2.0)
    assert block["locations"][0]["runs"] == [seeds[0]]
    line = next(ln for ln in console.splitlines() if ln.strip().startswith("collisions"))
    assert "2 over 2 replicate(s), 1 with any, 2 per 1,000 departed" in line
    assert "most on lane e1_0 (2 logged)" in line

    # The same stored tree without the counters: every existing key unchanged.
    battery_old, argv_old = _criteria_only(tmp_path, "old", None)
    assert battery_old.main(argv_old) == 0
    console_old = capsys.readouterr().out
    old = json.loads((tmp_path / "old" / "artifacts" / "validation.json").read_text())
    assert old["collisions"] is None
    assert [row["n_collisions"] for row in old["per_seed"]] == [None, None]
    assert "not recorded (no replicate's meta.json carries n_collisions)" in console_old
    assert set(artifact) == PRE_WP94_KEYS | {"collisions", "report_path"}
    for key in (PRE_WP94_KEYS | {"report_path"}) - {"created_at", "wall_s", "per_seed"}:
        assert json.dumps(old[key]) == json.dumps(artifact[key]), key
    for a, b in zip(old["per_seed"], artifact["per_seed"], strict=True):
        for key in PRE_WP94_SEED_KEYS - {"run_dir"}:
            assert json.dumps(a[key]) == json.dumps(b[key]), key
