"""``scripts/baseline_gate.py`` on a finished battery's stored run tree (no simulation).

The tree is the one ``tests/test_validation/test_validation_corridor_battery_gate.py``
writes: two scored replicates, observations on a five-minute grid. The script
must evaluate the gate from the stored files alone, through either spelling of
the run tree (``--battery-artifact`` or ``--runs`` with ``--scored-against``),
fail the validation-day checks it cannot or does not pass, and add its block to
the battery artifact without touching any other key.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.test_validation.test_validation_corridor_battery_gate import (
    FLOW,
    load_battery,
    write_observations,
    write_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("validation", [False, True])
def test_the_gate_script_on_the_same_tree(tmp_path: Path, validation: bool) -> None:
    battery = load_battery()
    tree = write_tree(battery, tmp_path)
    spec = importlib.util.spec_from_file_location(
        "baseline_gate_script_test", REPO_ROOT / "scripts" / "baseline_gate.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = script
    spec.loader.exec_module(script)

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
    argv = [
        "--battery-artifact", str(artifact),
        "--out-json", str(out_json),
        "--out-md", str(tmp_path / "gate.md"),
        "--write-into-artifact",
    ]  # fmt: skip
    if validation:
        val = write_observations(tmp_path / "val.json", flow=FLOW * 2.0, dates=["20260902"])
        argv += ["--validation-observations", str(val)]
    assert script.main(argv) == 0
    gate = json.loads(out_json.read_text())
    statuses = {(c["check"], c["day_set"]): c["status"] for c in gate["checks"]}
    assert statuses[("C1", "calibration")] == "pass"
    assert statuses[("C1", "validation")] == ("fail" if validation else "not_evaluated")
    assert gate["passed"] is False
    after = json.loads(artifact.read_text())
    assert after.pop("baseline_gate") == gate
    assert after == before
    # the --runs spelling reads the same tree
    again = tmp_path / "again.json"
    assert script.main(
        ["--runs", str(tree["dirs"][0].parent), "--scored-against", str(tree["observations"]),
         "--out-json", str(again)] + (argv[-2:] if validation else [])
    ) == 0  # fmt: skip
    assert json.loads(again.read_text())["checks"] == gate["checks"]
