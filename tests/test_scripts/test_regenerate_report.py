"""scripts/regenerate_report.py: an auto-report from a committed battery artifact plus one replicate.

Stage p22_reports (E13). Fixtures are synthetic: a three-replicate run set of a small ring scenario
(``meta.json`` and a constant-speed ``trajectories.parquet`` per run, as tests/test_validation's
report tests write them), a battery artifact built from it with the same validation functions the
batteries use, and the product report :func:`validation.report.generate_report` writes from the same
run set. Nothing is simulated except in the one integration test (the ring benchmark's seed).

* The plan: missing inputs are skipped, mismatches refused (hash, seed, an I-24 battery's scenario
  sha256, an existing report directory), each corridor arm pinned to its scenario's hash today.
* The report: the provenance line, the reproduction check, and the tables it shares with the
  product report (criteria, metrics, observed data, collisions, locks) verbatim; its contour is
  byte-identical to the product's for the same run; regenerating it gives the same bytes; an
  existing report is never written over; a replicate that does not reproduce the battery is said
  so in the report.
* I-24 and ring batteries; figures kept under the size limit; the record.
* Byte-identity: :func:`validation.report.generate_report` writes the same bytes as the committed
  module for the fixture run set (the adapter does not change the product report).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

from flowstate_core.config import config_hash
from microsim.scenarios import load_scenario
from tests.test_validation.test_validation_report import _traj
from validation.battery import (
    aggregate_insertion,
    collision_counts,
    collision_summary,
    insertion_stats,
)
from validation.criteria import CriteriaProfile, evaluate
from validation.locks import RunLocks, lock_summary
from validation.metrics import aggregate, compute_metrics
from validation.observed import DetectorWaveSpeed, ObservedProvenance
from validation.report import generate_report
from validation.ring_benchmark import DAMPENING_CONTROLLER, SINGLE_AV_PENETRATION

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "regenerate_report.py"
SEEDS = (11, 22, 33)
X_REF, SPAN = 500.0, (0.0, 2000.0)
CREATED = "2026-10-08T00:00:00Z"


def _load() -> ModuleType:
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    name = "regenerate_report_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rr = _load()


def _scenario(tmp: Path) -> Path:
    doc = yaml.safe_load((REPO_ROOT / "scenarios" / "ring_sugiyama.yaml").read_text())
    doc["name"] = "ring_fixture"
    doc["sim"]["duration_s"] = 100.0
    doc["sim"]["warmup_s"] = 0.0
    path = tmp / "ring_fixture.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def _replicate(run_dir: Path, seed: int, chash: str, cfg: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    _traj(5.0 + 0.1 * (seed % 7)).to_parquet(run_dir / "trajectories.parquet")
    meta = {
        "config": cfg,
        "config_hash": chash,
        "seed": seed,
        "tier": "micro",
        "seeded": False,
        "versions": {"eclipse-sumo": "1.27.1", "flowstate_core": "2.0.0"},
        "wall_time_s": 2.5,
        "n_vehicles_planned": 100,
        "n_vehicles_departed": 95 + seed % 3,
        "n_vehicles_arrived": 90,
        "n_collisions": 0,
        "collisions": [],
        "fleet_calibration": {"path": "artifacts/idm_fixture.json", "data_hash": "feedbeef"},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return run_dir


def _observed() -> ObservedProvenance:
    return ObservedProvenance(
        path="artifacts/observations_fixture.json",
        corridor="fixture corridor",
        provider="fixture provider",
        dates="20260901, 20260902",
        url="",
        aggregation="mean over dates per window",
        t0_local="06:00",
        window_s=300.0,
        n_stations=3,
        n_windows=4,
        n_windows_compared=4,
        flow_fraction=1.0,
        speed_fraction=0.9,
        n_link_hours=12,
        n_speed_cells=36,
        n_replicates=3,
        wave_speed=DetectorWaveSpeed(
            19.1, (18.0, 23.9), 13, 7, "2 too few events", 5, 18.5, 20.8, 5
        ),
    )


def _lock_free() -> RunLocks:
    return RunLocks(sources=("edges", "vehicles"), end_s=100.0)


class Fixture:
    """A run set, its corridor battery artifact and the product report written from it."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.scenario = _scenario(tmp)
        cfg = load_scenario(self.scenario)
        self.chash = config_hash(cfg)
        cfg_json = cfg.model_dump(mode="json")
        self.run_set = tmp / "battery_runs"
        self.dirs = [
            _replicate(self.run_set / self.chash / str(s), s, self.chash, cfg_json) for s in SEEDS
        ]
        self.metas = [json.loads((d / "meta.json").read_text()) for d in self.dirs]
        self.profile = CriteriaProfile()
        locks = [_lock_free() for _ in self.dirs]
        criteria = evaluate(
            self.profile,
            geh_values=None,
            rmspe_value=None,
            wave_speed_kmh=math.nan,
            ring_emergence=None,
            ring_dampening=None,
            n_seeds=len(SEEDS),
            wave_detector=self.profile.wave_detector,
            observations_supplied=True,
            collision_counts=collision_counts(self.metas),
            lock_records=locks,
            weave_releases=None,
        )
        agg = aggregate([compute_metrics(d, x_ref=X_REF, span=SPAN) for d in self.dirs])
        insertion = aggregate_insertion([insertion_stats(m) for m in self.metas])
        assert insertion is not None
        self.battery_doc: dict[str, Any] = rr._rt(
            {
                "schema": rr.CORRIDOR_SCHEMA,
                "created_at": "2026-10-07T00:00:00Z",
                "scenario": str(self.scenario),
                "corridor": "fixture corridor",
                "config_hash": self.chash,
                "seeds": list(SEEDS),
                "replicates": len(SEEDS),
                "versions": {"eclipse-sumo": "1.27.1", "python": "3.12.15"},
                "criteria_profile": {"name": self.profile.name, "source": self.profile.source},
                "criteria": [c.__dict__ for c in criteria],
                "observations": _observed().to_dict(),
                "insertion": insertion.to_dict(),
                "collisions": collision_summary(self.metas, labels=list(SEEDS)),
                "zero_collisions": True,
                "locks": lock_summary(locks, labels=list(SEEDS)),
                "zero_locks": True,
                "weave_releases": None,
                "metrics_ci": {
                    k: {
                        "mean": v.mean,
                        "lo95": v.lo95,
                        "hi95": v.hi95,
                        "n": v.n,
                        "underpowered": v.underpowered,
                    }
                    for k, v in agg.items()
                },
                "waiting": None,
                "per_seed": [
                    {
                        "seed": s,
                        "insertion": insertion_stats(m).to_dict(),
                        "n_collisions": 0,
                        "weave_releases": None,
                    }
                    for s, m in zip(SEEDS, self.metas, strict=True)
                ],
                "notes": ["A note the battery recorded."],
                "report_path": None,
            }
        )
        self.battery = tmp / "artifacts" / "validation_fixture.json"
        self.battery.parent.mkdir(parents=True, exist_ok=True)
        self.battery.write_text(json.dumps(self.battery_doc))
        self.product = tmp / "product" / "report.md"
        generate_report(
            self.run_set,
            self.product,
            profile=self.profile,
            created_at=CREATED,
            x_ref=X_REF,
            span=SPAN,
            observed=_observed(),
            wave_readings_by_run={d: math.nan for d in self.dirs},
            figure_runs=[self.dirs[0]],
            locks_by_run={d: _lock_free() for d in self.dirs},
        )
        self.out = tmp / "p22"
        self.reports = tmp / "reports"

    def plan(self, label: str = "e13_fix", seed: int = SEEDS[0], **arm: str) -> dict[str, Any]:
        spec = rr.Arm(
            label, arm.get("scenario", str(self.scenario)), arm.get("battery", str(self.battery))
        )
        return rr.plan_arm(spec, seed, self.out, self.reports)

    def figure_run(self, entry: dict[str, Any], src: int = 0) -> Path:
        run_dir = Path(entry["run_dir"])
        shutil.copytree(self.dirs[src], run_dir)
        return run_dir


@pytest.fixture()
def fx(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


def _table(text: str, header: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(header)
    out: list[str] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        out.append(line)
    return out


def test_parse_arm_forms_and_errors() -> None:
    assert rr.parse_arm("a:s.yaml:b.json") == rr.Arm("a", "s.yaml", "b.json")
    assert rr.parse_arm("a::b.json") == rr.Arm("a", "", "b.json")
    for bad in ("a:b.json", ":s.yaml:b.json", "a:s.yaml:", "a/b:s.yaml:b.json", "a:b:c:d"):
        with pytest.raises(ValueError):
            rr.parse_arm(bad)


def test_the_default_arms_parse_and_name_distinct_new_report_directories() -> None:
    arms = [rr.parse_arm(a) for a in rr.DEFAULT_ARMS]
    labels = [a.label for a in arms]
    assert len(set(labels)) == len(labels)
    for label in labels:
        # never the directory of a report someone else wrote
        assert (
            not (REPO_ROOT / "docs" / "reports" / label).exists()
            or rr._report_dir_conflict(REPO_ROOT / "docs" / "reports" / label) is None
        )


def test_plan_skips_missing_inputs_and_refuses_mismatches(fx: Fixture) -> None:
    ok = fx.plan()
    assert ok["status"] == "planned", ok["reason"]
    assert ok["config_hash_today"] == fx.chash and ok["hash_policy"] == f"v{rr.CONFIG_HASH_VERSION}"
    assert ok["run_dir"] == str(fx.out / "e13_fix" / fx.chash / str(SEEDS[0]))
    assert fx.plan(battery=str(fx.tmp / "nope.json"))["status"] == "skipped"
    assert fx.plan(scenario=str(fx.tmp / "nope.yaml"))["status"] == "skipped"
    # the scenario the battery records is taken when none is given
    assert fx.plan(scenario="")["scenario"] == str(fx.scenario)
    assert fx.plan(seed=999)["status"] == "refused"
    other = yaml.safe_load(fx.scenario.read_text())
    other["sim"]["duration_s"] = 200.0
    drifted = fx.tmp / "drifted.yaml"
    drifted.write_text(yaml.safe_dump(other))
    refused = fx.plan(scenario=str(drifted))
    assert refused["status"] == "refused" and "ran" in refused["reason"]
    bad = dict(fx.battery_doc, config_hash="000000000000")
    (fx.tmp / "bad.json").write_text(json.dumps(bad))
    assert fx.plan(battery=str(fx.tmp / "bad.json"))["status"] == "refused"
    (fx.tmp / "ring_less.json").write_text(json.dumps(fx.battery_doc))
    assert (
        fx.plan(scenario="ring_sugiyama", battery=str(fx.tmp / "ring_less.json"))["status"]
        == "refused"
    )


def test_plan_writes_the_run_pairs_arguments(fx: Fixture) -> None:
    entries = [fx.plan(), {"label": "skip", "status": "skipped", "reason": "x"}]
    rr.write_plan(entries, SEEDS[0], fx.out)
    tokens = (fx.out / rr.PAIRS_FILE).read_text().splitlines()
    assert tokens == [
        "--expect-hash",
        f"e13_fix={fx.chash}",
        f"e13_fix={fx.scenario}:{SEEDS[0]}",
    ]
    plan = json.loads((fx.out / rr.PLAN_FILE).read_text())
    assert plan["schema"] == rr.PLAN_SCHEMA and plan["seed"] == SEEDS[0]
    assert [a["status"] for a in plan["arms"]] == ["planned", "skipped"]


def test_an_existing_report_is_never_written_over(fx: Fixture) -> None:
    existing = fx.reports / "e13_fix"
    existing.mkdir(parents=True)
    shutil.copy(fx.product, existing / "report.md")
    png = next(fx.product.parent.glob("*.png"))
    shutil.copy(png, existing / png.name)
    before = {p.name: p.read_bytes() for p in existing.iterdir()}
    refused = fx.plan()
    assert refused["status"] == "refused" and "never overwritten" in refused["reason"]
    entry = dict(refused, status="planned", kind="corridor")
    entry.update(fx.plan(label="e13_other"))
    entry["report_dir"] = str(existing)
    with pytest.raises(ValueError, match="never overwritten"):
        rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)
    assert {p.name: p.read_bytes() for p in existing.iterdir()} == before


def test_the_report_shares_the_product_reports_tables_and_contour(fx: Fixture) -> None:
    entry = fx.plan()
    fx.figure_run(entry)
    record = rr.render_arm(entry, created_at=CREATED, code="testcode", pdf=False)
    ours = Path(record["report"]).read_text()
    theirs = fx.product.read_text()
    lines = ours.splitlines()
    assert f"figures from replicate {SEEDS[0]}; tables from {fx.battery}" in lines
    assert record["reproduction"]["identical"] is True
    assert record["reproduction"]["compared"] == ["insertion", "n_collisions"]
    assert any(
        line.startswith("The figure replicate reproduces the battery's replicate") for line in lines
    )
    header = "| Criterion | Value | Threshold | Evaluated | Result |"
    assert _table(ours, header) == _table(theirs, header)
    metric_header = "| Metric | Mean | Lower | Upper | n | Underpowered |"
    assert _table(ours, metric_header) == _table(theirs, metric_header)
    assert _table(ours, "| Field | Value |") == _table(theirs, "| Field | Value |")
    for prefix in ("- Collisions: ", "- Locks: "):
        mine = [line for line in lines if line.startswith(prefix)]
        assert mine and mine == [line for line in theirs.splitlines() if line.startswith(prefix)]
    name = f"speed_contour_00_seed_{SEEDS[0]}.png"
    assert (Path(entry["report_dir"]) / "figures" / name).read_bytes() == (
        fx.product.parent / name
    ).read_bytes()
    assert f"![Space-time mean-speed contour, seed {SEEDS[0]}](figures/{name})" in lines
    assert record["status"] == "written" and record["tables_from"] == str(fx.battery)
    assert record["figures"][0]["downsampled"] is False
    # regenerated from the same inputs: the same bytes
    first = Path(record["report"]).read_bytes()
    again = rr.render_arm(entry, created_at=CREATED, code="testcode", pdf=False)
    assert Path(again["report"]).read_bytes() == first


def test_a_replicate_that_does_not_reproduce_is_said_so(fx: Fixture) -> None:
    entry = fx.plan()
    run_dir = fx.figure_run(entry)
    meta = json.loads((run_dir / "meta.json").read_text())
    meta["n_vehicles_departed"] = 50
    (run_dir / "meta.json").write_text(json.dumps(meta))
    record = rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)
    rep = record["reproduction"]
    assert rep["checked"] and not rep["reproduces"] and rep["differences"] == ["insertion"]
    text = Path(record["report"]).read_text()
    assert (
        text.count("does NOT reproduce the battery's replicate") == 2
    )  # provenance and limitations


def test_floating_point_last_digits_reproduce_within_the_tolerance() -> None:
    near = rr._compare([("x", {"a": 1.0 + 1e-12, "n": 3}, {"a": 1.0, "n": 3})])
    assert near["reproduces"] and not near["identical"] and near["inexact"] == ["x"]
    far = rr._compare([("x", {"a": 1.1, "n": 3}, {"a": 1.0, "n": 3})])
    assert not far["reproduces"] and far["differences"] == ["x"]
    count = rr._compare([("x", {"n": 4}, {"n": 3})])
    assert not count["reproduces"]
    assert rr._compare([("x", 1, None)])["checked"] is False


def test_the_figure_run_must_be_the_planned_configuration(fx: Fixture) -> None:
    entry = fx.plan()
    with pytest.raises(FileNotFoundError):
        rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)
    run_dir = fx.figure_run(entry)
    meta = json.loads((run_dir / "meta.json").read_text())
    meta["config_hash"] = "000000000000"
    (run_dir / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="planned"):
        rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)


def _i24_battery(fx: Fixture) -> Path:
    sha = hashlib.sha256(fx.scenario.read_bytes()).hexdigest()
    doc = {
        "schema_version": 6,
        "criteria_profile": "fhwa_default",
        "created_at": "2026-10-07T00:00:00Z",
        "scenario": "ring_fixture",
        "arm": "fixture",
        "replicates": len(SEEDS),
        "seeds": list(SEEDS),
        "config_hash": fx.chash,
        "versions": {"eclipse-sumo": "1.27.1"},
        "observed": {
            "data_hash": "abc123",
            "period": "06:30-06:31 CST",
            "t_range_s": [1800.0, 1900.0],
            "span_data_x_m": [0.0, 1000.0],
            "sections_m": [200.0, 800.0],
            "window_s": 25.0,
            "n_windows": 4,
            "n_segments": 2,
            "segment_speeds_ms": [[20.0, 21.0], [22.0, 23.0], [21.0, 22.0], [20.0, 20.0]],
            "waves_by_detector": {"standard": {"mean_backward_speed_kmh": 14.0}},
            "n_fragments": 10,
        },
        "simulated": {
            "seeds": list(SEEDS),
            "ramps_per_replicate": [None] * len(SEEDS),
            "demand_realized_fraction": [
                round(m["n_vehicles_departed"] / m["n_vehicles_planned"], 4) for m in fx.metas
            ],
            "n_collisions_per_replicate": [0] * len(SEEDS),
            "segment_speeds_ms_mean": [[19.0, 20.0], [21.0, 22.0], [20.0, 21.0], [19.0, 19.0]],
            "wave_speed_by_detector": {
                "standard": {"mean_backward_speed_kmh": 9.0, "n_replicates_with_backward_waves": 3}
            },
        },
        "geh": {
            "primary": "recommended",
            "vs_tracked_counts": {"values": [], "fraction_under_5": 0.1, "n_bins": 8},
            "vs_recommended_coverage_counts": {"values": [], "fraction_under_5": 0.5, "n_bins": 8},
        },
        "criteria": fx.battery_doc["criteria"],
        "metrics_ci": fx.battery_doc["metrics_ci"],
        "notes": ["An I-24 note."],
        "scenario_file": {"path": str(fx.scenario), "sha256": sha},
        "collisions": fx.battery_doc["collisions"],
        "zero_collisions": True,
    }
    path = fx.tmp / "artifacts" / "i24_validation_fixture.json"
    path.write_text(json.dumps(doc))
    return path


def test_an_i24_battery_with_the_observed_field(fx: Fixture) -> None:
    battery = _i24_battery(fx)
    entry = fx.plan(label="e13_i24", battery=str(battery), scenario="")
    assert entry["status"] == "planned" and entry["kind"] == "i24", entry["reason"]
    fx.figure_run(entry)
    calls: list[tuple[Any, ...]] = []

    def loader(t: tuple[float, float], x: tuple[float, float], columns: list[str]) -> Any:
        import pandas as pd

        calls.append((t, x, tuple(columns)))
        rng = np.random.default_rng(0)
        n = 2000
        return pd.DataFrame(
            {"t": rng.uniform(*t, n), "x": rng.uniform(*x, n), "v": rng.uniform(5.0, 30.0, n)}
        )

    record = rr.render_arm(
        entry,
        created_at=CREATED,
        code="test",
        pdf=False,
        observed_loader=loader,
        geometry={"a": 0.0, "b": 1.0},
    )
    assert calls == [((1800.0, 1900.0), (0.0, 1000.0), ("t", "x", "v"))]
    text = Path(record["report"]).read_text()
    names = [Path(f["path"]).name for f in record["figures"]]
    assert names == [
        f"speed_contour_00_seed_{SEEDS[0]}.png",
        f"observed_vs_simulated_seed_{SEEDS[0]}.png",
    ]
    assert record["reproduction"]["compared"] == ["demand_realized_fraction", "n_collisions"]
    assert record["reproduction"]["identical"] is True
    assert "### Speed criterion by time aggregation" in text
    assert "| crossings / recommended coverage | 0.5 | 8 | yes |" in text
    assert "| standard | 14 | 9 | 3 |" in text
    assert "- Locks: not recorded — the battery artifact carries no lock records." in text
    # a scenario file that is not the one the battery recorded is refused
    changed = fx.tmp / "copy.yaml"
    changed.write_text(fx.scenario.read_text() + "\n")
    refused = fx.plan(label="e13_i24b", battery=str(battery), scenario=str(changed))
    assert refused["status"] == "refused"


def test_the_ring_arm_renders_the_baseline_and_controller_pair(fx: Fixture) -> None:
    ring_cfg = load_scenario("ring_sugiyama")
    damped = ring_cfg.model_copy(deep=True)
    damped.av.penetration = SINGLE_AV_PENETRATION
    damped.av.compliance = 1.0
    damped.av.controller = DAMPENING_CONTROLLER
    hb, hd = config_hash(ring_cfg), config_hash(damped)
    seed = SEEDS[0]
    record_seed = {
        "seed": seed,
        "emergence": {"sigma_v_ms": 2.25, "passed": True},
        "dampening": {"sigma_v_damped_ms": 5.7e-07, "passed": True},
    }
    stats = {
        "mean": 1.0,
        "lo95": 0.9,
        "hi95": 1.1,
        "n": 3,
        "underpowered": True,
        "min": 0.8,
        "max": 1.2,
    }
    doc = dict(
        fx.battery_doc,
        ring={
            "scenario": "ring_sugiyama",
            "seeds": list(SEEDS),
            "config_hash_baseline": hb,
            "config_hash_damped": hd,
            "thresholds": {"sigma_v_min_ms": 1.5},
            "emergence": {
                "passed": True,
                "n_pass": 3,
                "n_seeds": 3,
                "rule": "every seed",
                "sigma_v_ms": stats,
            },
            "dampening": {
                "passed": True,
                "n_pass": 3,
                "n_seeds": 3,
                "rule": "every seed",
                "reduction_frac": stats,
            },
            "per_seed": [dict(record_seed, seed=s) for s in SEEDS],
        },
    )
    battery = fx.tmp / "artifacts" / "with_ring.json"
    battery.write_text(json.dumps(doc))
    entry = fx.plan(label="e13_ring", scenario="ring_sugiyama", battery=str(battery))
    assert entry["status"] == "planned" and entry["kind"] == "ring", entry["reason"]
    assert entry["scenario_file"] == "scenarios/ring_sugiyama.yaml"
    root = Path(entry["run_root"])
    run_dirs = []
    for chash in (hb, hd):
        d = root / chash / str(seed)
        shutil.copytree(fx.dirs[0], d)
        meta = json.loads((d / "meta.json").read_text())
        meta["config_hash"] = chash
        (d / "meta.json").write_text(json.dumps(meta))
        run_dirs.append(d)
    (root / rr.RING_RECORD).write_text(
        json.dumps(
            {
                "per_seed": [
                    dict(
                        record_seed,
                        run_dir_baseline=str(run_dirs[0]),
                        run_dir_damped=str(run_dirs[1]),
                    )
                ]
            }
        )
    )
    record = rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)
    text = Path(record["report"]).read_text()
    assert record["reproduction"]["identical"] is True
    assert [Path(f["path"]).name for f in record["figures"]] == [
        f"speed_contour_pair_01_seed_{seed}.png"
    ]
    assert "## Ring benchmark" in text and "### Emergence (3 of 3 seeds pass; every seed)" in text
    assert "## Model integrity" not in text
    rows = [
        r.split("|")[1].strip()
        for r in _table(text, "| Criterion | Value | Threshold | Evaluated | Result |")[2:]
    ]
    assert rows == ["ring_emergence", "ring_dampening"]  # only the ring's rows


def test_a_large_figure_is_quantised_and_shrunk_under_the_limit(tmp_path: Path) -> None:
    from PIL import Image

    rng = np.random.default_rng(0)
    path = tmp_path / "noise.png"
    Image.fromarray(rng.integers(0, 255, (400, 600, 3), dtype=np.uint8)).save(path)
    limit = 120_000
    assert path.stat().st_size > limit
    out = rr.fit_png(path, max_bytes=limit)
    assert out["downsampled"] and out["bytes"] == path.stat().st_size <= limit
    assert out["bytes_rendered"] > limit and out["scale"] < 1.0
    small = tmp_path / "small.png"
    Image.fromarray(np.zeros((10, 10, 3), dtype=np.uint8)).save(small)
    before = small.read_bytes()
    assert rr.fit_png(small, max_bytes=limit)["downsampled"] is False
    assert small.read_bytes() == before


def test_the_template_carries_no_numerals() -> None:
    import re

    text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", rr.TEMPLATE, flags=re.S)
    assert not re.search(r"\d", text), re.findall(r".{0,20}\d.{0,20}", text)


def test_render_writes_the_record_with_every_arm(fx: Fixture) -> None:
    entries = [
        fx.plan(),
        fx.plan(label="e13_missing", battery=str(fx.tmp / "none.json")),
        fx.plan(label="e13_norun"),
    ]
    rr.write_plan(entries, SEEDS[0], fx.out)
    fx.figure_run(entries[0])
    record = fx.tmp / "artifacts" / "p22_reports.json"
    rc = rr.main(
        [
            "render",
            "--plan",
            str(fx.out / rr.PLAN_FILE),
            "--record",
            str(record),
            "--created-at",
            CREATED,
            "--pdf",
            "no",
        ]
    )
    assert rc == 1  # e13_norun has no figure run
    doc = json.loads(record.read_text())
    assert doc["schema"] == rr.RECORD_SCHEMA and doc["complete"] is True
    assert doc["seed"] == SEEDS[0] and doc["max_figure_bytes"] == rr.MAX_FIGURE_BYTES
    assert [(a["label"], a["status"]) for a in doc["arms"]] == [
        ("e13_fix", "written"),
        ("e13_missing", "skipped"),
        ("e13_norun", "failed"),
    ]
    written = doc["arms"][0]
    assert written["figures_from"] == f"replicate {SEEDS[0]}" and written["tables_from"] == str(
        fx.battery
    )
    assert written["scenario_sha256"] == hashlib.sha256(fx.scenario.read_bytes()).hexdigest()


def test_the_pdf_takes_the_figures_from_their_directory(fx: Fixture) -> None:
    pytest.importorskip("fpdf")
    entry = fx.plan()
    fx.figure_run(entry)
    record = rr.render_arm(entry, created_at=CREATED, code="test", pdf=True)
    assert Path(record["pdf"]).is_file() and Path(record["pdf"]).stat().st_size > 0


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)


def test_generate_report_writes_what_the_committed_module_writes(
    fx: Fixture, tmp_path: Path
) -> None:
    """Byte-identity: the product report of the fixture run set, from the working tree's
    validation.report and from the module and template committed at HEAD, are the same bytes."""
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git work tree (the staged-tree gate exports without .git)")
    head = tmp_path / "head_report"
    (head / "templates").mkdir(parents=True)
    for rel, dest in (
        ("packages/validation/validation/report.py", head / "report_head.py"),
        (
            "packages/validation/validation/templates/report.md.j2",
            head / "templates" / "report.md.j2",
        ),
    ):
        shown = _git("show", f"HEAD:{rel}")
        if shown.returncode != 0:
            pytest.skip(f"{rel} is not committed at HEAD")
        dest.write_text(shown.stdout)
    spec = importlib.util.spec_from_file_location(
        "validation_report_at_head", head / "report_head.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    out = tmp_path / "head_out" / "report.md"
    mod.generate_report(
        fx.run_set,
        out,
        profile=fx.profile,
        created_at=CREATED,
        x_ref=X_REF,
        span=SPAN,
        observed=_observed(),
        wave_readings_by_run={d: math.nan for d in fx.dirs},
        figure_runs=[fx.dirs[0]],
        locks_by_run={d: _lock_free() for d in fx.dirs},
    )
    assert out.read_bytes() == fx.product.read_bytes()
    for png in fx.product.parent.glob("*.png"):
        assert (out.parent / png.name).read_bytes() == png.read_bytes()


@pytest.mark.integration
def test_the_ring_seed_runs_and_renders_against_the_committed_battery(tmp_path: Path) -> None:
    """The real ring benchmark's kept seed (two 600-s ring runs) against the committed B2 battery's ring
    block: the plan, ``ring-run`` and the report; the reproduction is checked (its outcome can differ
    in the last digits between platforms, so only that it was checked is asserted)."""
    battery = REPO_ROOT / "artifacts" / "i24_validation_dc_refit_rc.json"
    if not battery.is_file():
        pytest.skip("the committed B2 battery is not in this checkout")
    out, reports = tmp_path / "p22", tmp_path / "reports"
    entry = rr.plan_arm(
        rr.Arm("e13_ring", "ring_sugiyama", str(battery)), rr.KEPT_SEED, out, reports
    )
    assert entry["status"] == "planned", entry["reason"]
    rr.write_plan([entry], rr.KEPT_SEED, out)
    assert rr.main(["ring-run", "--plan", str(out / rr.PLAN_FILE)]) == 0
    record = rr.render_arm(entry, created_at=CREATED, code="test", pdf=False)
    assert record["reproduction"]["checked"] is True
    text = Path(record["report"]).read_text()
    assert f"figures from replicate {rr.KEPT_SEED}; tables from {battery}" in text.splitlines()
    assert "| ring_emergence | 1 |" in text and "| ring_dampening | 1 |" in text
