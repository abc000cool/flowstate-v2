"""``scripts/baseline_gate.py`` on a finished battery's stored run tree (no simulation).

The tree is the one ``tests/test_validation/test_validation_corridor_battery_gate.py``
writes: two scored replicates, observations on a five-minute grid. The script
must evaluate the gate from the stored files alone, through either spelling of
the run tree (``--battery-artifact`` or ``--runs`` with ``--scored-against``),
fail the validation-day checks it cannot or does not pass, and add its block to
the battery artifact without touching any other key. The calibration-day
artifact and the day split are required (the battery's all-dates artifact is
never taken for the calibration days), and ``--per-day`` scores each
validation day on its own, beside the gate.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_validation.test_validation_corridor_battery_gate import (
    FLOW,
    load_battery,
    write_observations,
    write_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "baseline_gate_script_test", REPO_ROOT / "scripts" / "baseline_gate.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = script
    spec.loader.exec_module(script)
    return script


def _split(path: Path, validation: list[str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "seed": 20261004,
                "calibration_dates": ["2026-09-01"],
                "validation_dates": validation,
                "underpowered": True,
                "underpowered_reason": "1 calibration day(s), fewer than 5",
            }
        )
    )
    return path


@pytest.mark.parametrize("validation", [False, True])
def test_the_gate_script_on_the_same_tree(tmp_path: Path, validation: bool) -> None:
    battery = load_battery()
    tree = write_tree(battery, tmp_path)
    script = _script()

    artifact = tmp_path / "battery.json"
    artifact.write_text(
        json.dumps(
            {
                "schema": "flowstate.corridor_validation/1",
                "scenario": str(tree["scenario"]),
                "observations": {"path": str(tree["observations"])},
                "per_seed": [{"run_dir": str(d)} for d in tree["dirs"]],
            }
        )
    )
    before = json.loads(artifact.read_text())
    out_json = tmp_path / "gate.json"
    split = _split(tmp_path / "split.json", ["2026-09-02"])
    required = [
        "--calibration-observations", str(tree["observations"]),
        "--day-split", str(split),
    ]  # fmt: skip
    argv = [
        "--battery-artifact", str(artifact),
        "--out-json", str(out_json),
        "--out-md", str(tmp_path / "gate.md"),
        "--write-into-artifact",
        *required,
    ]  # fmt: skip
    if validation:
        val = write_observations(tmp_path / "val.json", flow=FLOW * 2.0, dates=["20260902"])
        argv += ["--validation-observations", str(val)]
    assert script.main(argv) == 0
    gate = json.loads(out_json.read_text())
    statuses = {(c["check"], c["day_set"]): c["status"] for c in gate["checks"]}
    assert statuses[("C1", "calibration")] == "pass"
    assert statuses[("C1", "validation")] == ("fail" if validation else "not_evaluated")
    assert statuses[("days", "both day sets")] == ("pass" if validation else "not_evaluated")
    assert gate["passed"] is False and gate["per_day"] is None
    after = json.loads(artifact.read_text())
    assert after.pop("baseline_gate") == gate
    assert after == before
    # the --runs spelling reads the same tree
    again = tmp_path / "again.json"
    assert script.main(
        ["--runs", str(tree["dirs"][0].parent), "--scored-against", str(tree["observations"]),
         "--out-json", str(again), *required] + (argv[-2:] if validation else [])
    ) == 0  # fmt: skip
    assert json.loads(again.read_text())["checks"] == gate["checks"]


def test_the_calibration_artifact_and_the_split_are_required(tmp_path: Path) -> None:
    tree = write_tree(load_battery(), tmp_path)
    script = _script()
    base = [
        "--runs", str(tree["dirs"][0].parent),
        "--scored-against", str(tree["observations"]),
        "--out-json", str(tmp_path / "gate.json"),
    ]  # fmt: skip
    split = _split(tmp_path / "split.json", ["2026-09-02"])
    with pytest.raises(SystemExit) as no_cal:
        script.main([*base, "--day-split", str(split)])
    assert no_cal.value.code == 2
    with pytest.raises(SystemExit) as no_split:
        script.main([*base, "--calibration-observations", str(tree["observations"])])
    assert no_split.value.code == 2
    assert not (tmp_path / "gate.json").exists()


def test_per_day_scores_each_validation_day_beside_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = write_tree(load_battery(), tmp_path)
    script = _script()
    days = tmp_path / "days"
    days.mkdir()
    write_observations(days / "d0902.json", flow=FLOW * 1.01, dates=["20260902"])
    write_observations(days / "d0903.json", flow=FLOW * 2.0, dates=["20260903"])
    write_observations(days / "d0999.json", dates=["20260999"])  # not on the split
    split = _split(tmp_path / "split.json", ["2026-09-02", "2026-09-03", "2026-09-04"])
    val = write_observations(tmp_path / "val.json", flow=FLOW * 1.01, dates=["20260902"])
    out_json = tmp_path / "gate.json"
    argv = [
        "--runs", str(tree["dirs"][0].parent),
        "--scored-against", str(tree["observations"]),
        "--calibration-observations", str(tree["observations"]),
        "--validation-observations", str(val),
        "--day-split", str(split),
        "--per-day", str(days),
        "--out-json", str(out_json),
        "--out-md", str(tmp_path / "gate.md"),
    ]  # fmt: skip
    assert script.main(argv) == 0
    gate = json.loads(out_json.read_text())
    per_day = gate["per_day"]
    assert [r["date"] for r in per_day["rows"]] == ["20260902", "20260903"]
    c1 = {r["date"]: r["checks"][0] for r in per_day["rows"]}
    assert c1["20260902"]["status"] == "pass" and c1["20260903"]["status"] == "fail"
    assert all(not c["gating"] for r in per_day["rows"] for c in r["checks"])
    assert [Path(x["path"]).name for x in per_day["refused"]] == ["d0999.json"]
    assert per_day["missing_dates"] == ["20260904"]
    # the validation-day set (09-02 only) does not match the split's three days: the
    # per-day table is reported, the day-set precondition fails, the table never gates
    assert {c["check"]: c["status"] for c in gate["checks"] if c["check"] == "days"} == {
        "days": "fail"
    }
    md = (tmp_path / "gate.md").read_text()
    assert "## Validation days one by one (reported, not gating)" in md
    text = capsys.readouterr().out
    assert "validation days one by one (reported, not gating):" in text
    assert "  20260903: C1 fail" in text
    # an unreadable artifact in the directory is a usage error
    (days / "junk.json").write_text("{}")
    assert script.main(argv) == 2


def _scored_with_end(tree: dict[str, Any], end: float) -> None:
    """Mark the stored replicates as a battery run with ``--scored-end-s end`` writes them."""
    for d in tree["dirs"]:
        path = d / "metrics.json"
        stored = json.loads(path.read_text())
        stored["scored_end_s"] = end
        path.write_text(json.dumps(stored))


def _artifact(tmp_path: Path, tree: dict[str, Any], **extra: Any) -> Path:
    artifact = tmp_path / "battery.json"
    artifact.write_text(
        json.dumps(
            {
                "schema": "flowstate.corridor_validation/1",
                "scenario": str(tree["scenario"]),
                "observations": {"path": str(tree["observations"])},
                "per_seed": [{"run_dir": str(d)} for d in tree["dirs"]],
                **extra,
            }
        )
    )
    return artifact


def test_a_battery_scored_with_a_scored_end_is_gated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review 2026-10-07: the gate read every replicate as scored to the run's end, so a
    battery run with --scored-end-s (a cool-down, protocol section 8.2) crashed it. The
    artifact records the scored end; the gate reads the replicates with it, and refuses
    (exit 2, saying what to give) replicates scored with another value."""
    tree = write_tree(load_battery(), tmp_path)
    _scored_with_end(tree, 3000.0)
    script = _script()
    split = _split(tmp_path / "split.json", ["2026-09-02"])
    common = [
        "--calibration-observations", str(tree["observations"]),
        "--day-split", str(split),
    ]  # fmt: skip
    artifact = _artifact(tmp_path, tree, scored_end_s=3000.0)
    out_json = tmp_path / "gate.json"
    assert (
        script.main(["--battery-artifact", str(artifact), "--out-json", str(out_json), *common])
        == 0
    )
    statuses = {
        (c["check"], c["day_set"]): c["status"] for c in json.loads(out_json.read_text())["checks"]
    }
    assert statuses[("C1", "calibration")] == "pass"
    # an artifact recording no scored end (or another one): refused, with what to do
    for extra in ({}, {"scored_end_s": 3300.0}):
        refused = tmp_path / "refused.json"
        art = _artifact(tmp_path, tree, **extra)
        assert (
            script.main(["--battery-artifact", str(art), "--out-json", str(refused), *common]) == 2
        )
        err = capsys.readouterr().err
        assert "was scored with scored_end_s=3000.0" in err
        assert "--battery-artifact" in err and "--criteria-only --scored-end-s" in err
        assert not refused.exists()
    # --runs alone expects replicates scored to the run's end
    runs = [
        "--runs", str(tree["dirs"][0].parent), "--scored-against", str(tree["observations"]),
        "--out-json", str(tmp_path / "runs.json"), *common,
    ]  # fmt: skip
    assert script.main(runs) == 2
    assert "without --battery-artifact" in capsys.readouterr().err
