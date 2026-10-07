"""scripts/i24_validate.py --scenario PATH --label L: an explicit scenario file's battery.

No simulation and no I-24 data (the laptop rule): the runner and the observed
side are stubbed with the committed flow-family fitted arm's own stored sides
(``artifacts/i24_validation_flow_speedcal.json``), so the explicit path is
checked end to end on real numbers:

* the arguments are validated before anything runs (an arm's name is refused:
  it would overwrite that arm's committed artifact);
* from the same simulated and observed sides the explicit path reproduces the
  committed arm's seven criteria rows, and scores the ``no_collisions`` row
  from the replicates' collision counters (an arm's row stays not recorded);
* the artifact records the scenario file and the collisions, lands at
  ``artifacts/i24_validation_<label>.json`` (here redirected), the run tree
  under ``<out root>/<label>``, and the committed observed side is not
  rewritten; ``--criteria-only --label`` re-scores it with the counters.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
ARM = REPO_ROOT / "artifacts" / "i24_validation_flow_speedcal.json"
OBSERVED = REPO_ROOT / "artifacts" / "i24_validation_observed.json"
SCENARIO = REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal.yaml"


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


v = _load("i24_validate")


def _rows(results: dict[str, Any]) -> list[tuple[str, bool, bool, Any]]:
    return [(r["name"], r["passed"], r["evaluated"], r["value"]) for r in results["criteria"]]


def test_labels() -> None:
    assert v.label_error("dc") is None and v.label_error("dc_refit") is None
    for bad in ("speedcal", "ramps", "ring", "all", "Dc", "dc-1", "", "_x"):
        assert v.label_error(bad) is not None


@pytest.mark.parametrize(
    ("argv", "match"),
    [
        (["--scenario", str(SCENARIO)], "--scenario needs --label"),
        (["--label", "dc"], "--label needs --scenario"),
        (["--scenario", str(SCENARIO), "--label", "speedcal"], "arm's (or reserved) name"),
        (["--scenario", str(SCENARIO), "--label", "dc", "--arms", "all"], "exclusive"),
        (["--scenario", "no/such.yaml", "--label", "dc"], "no such file"),
    ],
)
def test_bad_arguments_stop_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    match: str,
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("ran")

    monkeypatch.setattr(v, "ring_benchmark_block", boom)
    monkeypatch.setattr(v, "observed_side", boom)
    with pytest.raises(SystemExit) as exc:
        v.main(argv)
    assert exc.value.code == 2
    assert match in capsys.readouterr().err


def test_the_explicit_path_reproduces_the_arms_rows_and_adds_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v, "FAMILY", "_flow")  # the committed arm is the flow family's
    d = json.loads(ARM.read_text())
    cfg = v.load_scenario(SCENARIO)
    arm = v.build_results("speedcal", cfg, d["simulated"], d["observed"], 20, d["ring"])
    assert _rows(arm)[:7] == [(r["name"], r["passed"], r["evaluated"], r["value"]) for r in d["criteria"]]  # fmt: skip
    assert _rows(arm)[7][:3] == ("no_collisions", False, False)  # not recorded, as before
    assert arm["scenario"] == d["scenario"] and arm["demand_arm"] == d["demand_arm"]
    explicit = v.build_results(
        "dc",
        cfg,
        d["simulated"],
        d["observed"],
        20,
        d["ring"],
        scenario="i24_replica_flow_speedcal_dc",
        demand_arm="as written",
        collisions=[0] * 20,
    )
    assert _rows(explicit)[:7] == _rows(arm)[:7]
    assert _rows(explicit)[7][:3] == ("no_collisions", True, True)
    assert explicit["scenario"] == "i24_replica_flow_speedcal_dc" and explicit["arm"] == "dc"
    one = v.build_results("dc", cfg, d["simulated"], d["observed"], 20, d["ring"], scenario="x", collisions=[0] * 19 + [2])  # fmt: skip
    assert _rows(one)[7][:3] == ("no_collisions", False, True)


def _fake_runs(root: Path, seeds: list[int], counts: list[int]) -> list[str]:
    dirs = []
    for s, n in zip(seeds, counts, strict=True):
        d = root / "fake" / str(s)
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps(
                {"seed": s, "n_collisions": n, "n_vehicles_departed": 10_000, "collisions": []}
            )
        )
        dirs.append(str(d))
    return dirs


def test_main_runs_an_explicit_scenario_without_touching_the_committed_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = json.loads(ARM.read_text())
    sim = copy.deepcopy(d["simulated"])
    counts = [0] * 19 + [3]
    sim["run_dirs"] = _fake_runs(tmp_path, sim["seeds"], counts)
    calls: list[Path] = []

    def fake_micro_arm(cfg: Any, n: int, out_root: Path, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(out_root)
        assert n == 20 and cfg.name == "i24_replica_flow_speedcal"
        return copy.deepcopy(sim)

    out_root = tmp_path / "runs"
    arts = tmp_path / "artifacts"
    arts.mkdir()
    monkeypatch.setattr(v, "OUT_ROOT", out_root)
    monkeypatch.setattr(v, "artifact_path", lambda arm: arts / f"i24_validation_{arm}.json")
    monkeypatch.setattr(v, "observed_side", lambda cache: d["observed"])
    monkeypatch.setattr(v, "micro_arm", fake_micro_arm)
    before = hashlib.sha256(OBSERVED.read_bytes()).hexdigest()
    v.main(["--scenario", str(SCENARIO), "--label", "dc", "--ring-seeds", "0"])
    assert hashlib.sha256(OBSERVED.read_bytes()).hexdigest() == before
    assert calls == [out_root / "dc"]
    res = json.loads((arts / "i24_validation_dc.json").read_text())
    assert res["arm"] == "dc" and res["scenario"] == "i24_replica_flow_speedcal"
    assert res["scenario_file"] == {
        "path": "scenarios/i24_replica_flow_speedcal.yaml",
        "sha256": hashlib.sha256(SCENARIO.read_bytes()).hexdigest(),
    }
    assert res["simulated"]["n_collisions_per_replicate"] == counts
    assert res["collisions"]["total"] == 3 and res["collisions"]["n_runs_with_collisions"] == 1
    assert res["zero_collisions"] is False
    rows = {r["name"]: r for r in res["criteria"]}
    assert rows["no_collisions"]["evaluated"] and not rows["no_collisions"]["passed"]
    assert rows["link_flows_geh"]["value"] == pytest.approx(d["criteria"][0]["value"])
    assert not rows["ring_emergence"]["evaluated"]  # --ring-seeds 0
    assert (out_root / "i24_replica_flow_speedcal.yaml").is_file()
    out = capsys.readouterr().out
    assert "collisions         3 over 20 replicate(s), 1 with any" in out
    # --criteria-only --label re-scores the artifact, the counters included
    v.main(["--label", "dc", "--criteria-only", "--ring-seeds", "0"])
    again = {
        r["name"]: r for r in json.loads((arts / "i24_validation_dc.json").read_text())["criteria"]
    }
    assert again["no_collisions"]["evaluated"] and not again["no_collisions"]["passed"]
    assert hashlib.sha256(OBSERVED.read_bytes()).hexdigest() == before
