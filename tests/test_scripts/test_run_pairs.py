"""scripts/run_pairs.py: named (scenario, seed) runs with their trajectories kept.

* Pair parsing and the duplicate guards.
* With a stand-in worker (no simulation): the run trees, the manifest, a failing pair
  that leaves the others running (exit 1), a hash mismatch that runs nothing (exit 2),
  a dry run, and the payload being the battery's (the scenario's JSON dump, the seed,
  ``<out>/<label>``).
* The committed calibration-day scenarios still hash as stage p8's runs did
  (``beaaa710e6b3``, ``182e3ec2f500``): the guard stage p8c relies on (load and hash
  only, no simulation).
* Integration: two 20-s ring runs through the real spawn pool keep their trajectories
  under ``<out>/<label>/<config hash>/<seed>/``, as ``run_micro`` writes them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = _load("run_pairs")


def _ring_yaml(tmp: Path, name: str = "ring_tiny", duration_s: float = 20.0) -> Path:
    doc = yaml.safe_load((REPO_ROOT / "scenarios" / "ring_sugiyama.yaml").read_text())
    doc["name"] = name
    doc["sim"]["duration_s"] = duration_s
    doc["sim"]["warmup_s"] = 0.0
    doc["replicates"] = 1
    path = tmp / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_parse_pair_forms_and_errors() -> None:
    p = rp.parse_pair("dc_cal=scenarios/x_dc_cal.yaml:134183728835869882")
    assert (p.label, p.scenario, p.seed) == (
        "dc_cal",
        Path("scenarios/x_dc_cal.yaml"),
        134183728835869882,
    )
    q = rp.parse_pair("scenarios/x_dc_cal.yaml:7")
    assert (q.label, q.seed) == ("x_dc_cal", 7)
    for bad in ("x.yaml", "a=x.yaml:", "a=x.yaml:seven", "a=x.yaml:-1", "a b=x.yaml:1", ":3"):
        with pytest.raises(ValueError):
            rp.parse_pair(bad)


def test_duplicates_are_refused() -> None:
    a = rp.parse_pair("a=x.yaml:1")
    with pytest.raises(ValueError, match="given twice"):
        rp.check_pairs([a, rp.parse_pair("a=x.yaml:1")])
    with pytest.raises(ValueError, match="names two scenarios"):
        rp.check_pairs([a, rp.parse_pair("a=y.yaml:2")])
    rp.check_pairs([a, rp.parse_pair("b=x.yaml:1"), rp.parse_pair("a=x.yaml:2")])


class _FakeWorker:
    """Stands in for ``microsim.runner._replicate_worker``: writes the run directory's
    ``meta.json`` (and an empty trajectories file); fails for one seed."""

    def __init__(self, fail_seed: int | None = None) -> None:
        self.payloads: list[tuple[dict[str, Any], int, str]] = []
        self.fail_seed = fail_seed

    def __call__(self, payload: tuple[dict[str, Any], int, str]) -> tuple[str, str, str, str]:
        from flowstate_core.config import ScenarioConfig, config_hash

        self.payloads.append(payload)
        cfg_json, seed, out_root = payload
        if seed == self.fail_seed:
            raise RuntimeError("worker died")
        chash = config_hash(ScenarioConfig.model_validate(cfg_json))
        run_dir = Path(out_root) / chash / str(seed)
        run_dir.mkdir(parents=True)
        (run_dir / "trajectories.parquet").write_bytes(b"")
        meta = {"config_hash": chash, "seed": seed, "n_collisions": seed % 2, "wall_time_s": 1.5}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        return str(run_dir), str(run_dir / "trajectories.parquet"), "", str(run_dir / "meta.json")


def test_pairs_run_in_order_and_a_failure_leaves_the_others(tmp_path: Path) -> None:
    from flowstate_core.config import config_hash
    from microsim.scenarios import load_scenario

    a, b = _ring_yaml(tmp_path, "ring_a"), _ring_yaml(tmp_path, "ring_b")
    pairs = [rp.parse_pair(f"A={a}:11"), rp.parse_pair(f"B={b}:12"), rp.parse_pair(f"A={a}:13")]
    out = tmp_path / "runs"
    worker = _FakeWorker(fail_seed=12)
    status, manifest = rp.run_pairs(pairs, out, procs=1, worker=worker)
    assert status == 1
    ha = config_hash(load_scenario(a))
    # the battery's payload: the scenario's JSON dump, the seed, <out>/<label>
    assert worker.payloads[0] == (load_scenario(a).model_dump(mode="json"), 11, str(out / "A"))
    assert [p[1] for p in worker.payloads] == [11, 12, 13]
    recs = {(r["label"], r["seed"]): r for r in manifest["pairs"]}
    assert recs["A", 11]["status"] == "ok" and recs["A", 11]["run_dir"] == str(
        out / "A" / ha / "11"
    )
    assert recs["A", 11]["n_collisions"] == 1 and recs["A", 11]["wall_s"] == 1.5
    assert recs["A", 11]["config_hash_run"] == ha
    assert recs["B", 12]["status"] == "failed" and "worker died" in recs["B", 12]["error"]
    assert recs["A", 13]["status"] == "ok" and recs["A", 13]["n_collisions"] == 1
    on_disk = json.loads((out / rp.MANIFEST).read_text())
    assert [r["status"] for r in on_disk["pairs"]] == ["ok", "failed", "ok"]


def test_a_hash_mismatch_runs_nothing(tmp_path: Path) -> None:
    from flowstate_core.config import config_hash
    from microsim.scenarios import load_scenario

    a = _ring_yaml(tmp_path)
    worker = _FakeWorker()
    pairs = [rp.parse_pair(f"A={a}:1")]
    status, manifest = rp.run_pairs(
        pairs, tmp_path / "o", procs=1, worker=worker, expect_hash={"A": "000000000000"}
    )
    assert status == 2 and worker.payloads == [] and manifest["refused"]
    good = config_hash(load_scenario(a))
    status, _ = rp.run_pairs(
        pairs, tmp_path / "o2", procs=1, worker=worker, expect_hash={"A": good}
    )
    assert status == 0 and len(worker.payloads) == 1
    with pytest.raises(ValueError, match="without a pair"):
        rp.run_pairs(pairs, tmp_path / "o3", procs=1, worker=worker, expect_hash={"Z": good})


def test_dry_run_and_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _ring_yaml(tmp_path)
    out = tmp_path / "dry"
    assert rp.main(["--out", str(out), "--dry-run", f"A={a}:5"]) == 0
    m = json.loads((out / rp.MANIFEST).read_text())
    assert m["dry_run"] and m["pairs"][0]["status"] == "planned"
    assert not any(out.glob("A/*/*/meta.json"))
    assert rp.main(["--out", str(out), "--dry-run", f"A={tmp_path / 'missing.yaml'}:5"]) == 2
    assert rp.main(["--out", str(out), "--dry-run", "--expect-hash", "A", f"A={a}:5"]) == 2
    assert "run_pairs:" in capsys.readouterr().err


def test_the_calibration_day_scenarios_hash_as_stage_p8s_runs(tmp_path: Path) -> None:
    """Stage p8c's guard (``--expect-hash``) on the committed scenarios: load and hash only."""
    stem = REPO_ROOT / "scenarios" / "mndot_i94_wb_stpaul_weave_dc_cal"
    pairs = [
        rp.parse_pair(f"dc_cal={stem}.yaml:134183728835869882"),
        rp.parse_pair(f"dc_cal_netfix={stem}_netfix.yaml:165503670820534583"),
    ]
    status, m = rp.run_pairs(
        pairs,
        tmp_path / "p8c",
        procs=7,
        dry_run=True,
        expect_hash={"dc_cal": "beaaa710e6b3", "dc_cal_netfix": "182e3ec2f500"},
    )
    assert status == 0, m.get("refused")
    assert [r["run_dir"] for r in m["pairs"]] == [
        str(tmp_path / "p8c" / "dc_cal" / "beaaa710e6b3" / "134183728835869882"),
        str(tmp_path / "p8c" / "dc_cal_netfix" / "182e3ec2f500" / "165503670820534583"),
    ]


@pytest.mark.integration
def test_two_ring_runs_through_the_spawn_pool_keep_their_trajectories(tmp_path: Path) -> None:
    a = _ring_yaml(tmp_path)
    out = tmp_path / "runs"
    status, m = rp.run_pairs(
        [rp.parse_pair(f"ring={a}:3"), rp.parse_pair(f"ring={a}:4")], out, procs=2
    )
    assert status == 0, m
    for r in m["pairs"]:
        run_dir = Path(r["run_dir"])
        meta = json.loads((run_dir / "meta.json").read_text())
        assert (
            meta["seed"] == r["seed"]
            and meta["config_hash"] == r["config_hash"] == r["config_hash_run"]
        )
        assert (run_dir / "trajectories.parquet").stat().st_size > 0
        assert r["n_collisions"] == meta["n_collisions"]
