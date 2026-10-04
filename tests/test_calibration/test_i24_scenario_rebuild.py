"""The fitted I-24 scenarios are functions of their fit artifacts.

``scripts/i24_fit_demand_scale.py --from-artifact`` and
``scripts/i24_fit_boundary_ramps.py --from-artifact`` rebuild a scenario file
from a saved fit without simulating. The rebuilt files must hash to what the
batteries that used them recorded (the zip family of 2026-09-06, whose
scenario files were lost with the VM and rebuilt this way).

Those records are dated before 2026-10-04 and quote config-hash policy v2
(docs/CONTRACTS.md §2), so the rebuilt documents are compared under v2
(``config_hash_v2``); the header line a rebuild writes states the current
(v3) hash, the committed file's the v2 one, and the files are otherwise
byte-identical.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from flowstate_core.config import ScenarioConfig, config_hash, config_hash_v2

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
DEMAND_ARTIFACT = REPO / "artifacts" / "demand_scale_i24_zip.json"
RAMPS_ARTIFACT = REPO / "artifacts" / "i24_boundary_ramps_fit_zip.json"
RAMPS_BATTERY = REPO / "artifacts" / "i24_validation_zip_ramps.json"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _hash_of(path: Path) -> str:
    """The policy-v2 hash of a scenario file: the policy its records quote."""
    return config_hash_v2(yaml.safe_load(path.read_text()))


_HASH_LINE = re.compile(r"^# config hash ([0-9a-f]{12}); ", re.M)


def _assert_same_file(rebuilt: Path, committed: Path) -> None:
    """Byte-identical but for the header's hash: the rebuild states the
    current (v3) hash of the document, the committed file its v2 hash."""
    new, old = rebuilt.read_text(), committed.read_text()
    (new_hash,) = _HASH_LINE.findall(new)
    (old_hash,) = _HASH_LINE.findall(old)
    assert new_hash == config_hash(ScenarioConfig.model_validate(yaml.safe_load(new)))
    assert old_hash == _hash_of(committed)
    assert _HASH_LINE.sub("# config hash <h>; ", new) == _HASH_LINE.sub("# config hash <h>; ", old)


@pytest.mark.skipif(not DEMAND_ARTIFACT.is_file(), reason="zip family artifacts absent")
def test_demand_scenario_rebuilds_to_recorded_hash(tmp_path: Path) -> None:
    mod = _load("i24_fit_demand_scale")
    art = json.loads(DEMAND_ARTIFACT.read_text())
    out = tmp_path / "speedcal.yaml"
    args = argparse.Namespace(
        name="i24_replica_zip_speedcal", scenario_out=out, from_artifact=DEMAND_ARTIFACT, out=None
    )
    mod.write_scenario(
        art["best"], art["fleet_artifact"], art["base"], REPO / art["base_scenario"], args
    )
    assert _hash_of(out) == art["best"]["config_hash"]
    # the ramp fit of the same family was run on exactly this scenario
    prov = json.loads(RAMPS_ARTIFACT.read_text())["provenance"]
    assert _hash_of(out) == prov["base_config_hash"]
    _assert_same_file(out, REPO / "scenarios" / "i24_replica_zip_speedcal.yaml")


@pytest.mark.skipif(not RAMPS_ARTIFACT.is_file(), reason="zip family artifacts absent")
def test_ramps_scenario_rebuilds_to_battery_hash(tmp_path: Path) -> None:
    mod = _load("i24_fit_boundary_ramps")
    out = tmp_path / "ramps.yaml"
    mod.SCENARIO_OUT = out
    mod.SCENARIO_NAME = "i24_replica_zip_speedcal_ramps"
    mod.write_from_artifact(RAMPS_ARTIFACT)
    battery = json.loads(RAMPS_BATTERY.read_text())
    assert _hash_of(out) == battery["config_hash"]
    _assert_same_file(out, REPO / "scenarios" / "i24_replica_zip_speedcal_ramps.yaml")


@pytest.mark.skipif(not RAMPS_ARTIFACT.is_file(), reason="zip family artifacts absent")
def test_ramps_rebuild_refuses_a_foreign_base(tmp_path: Path) -> None:
    mod = _load("i24_fit_boundary_ramps")
    mod.SCENARIO_OUT = tmp_path / "ramps.yaml"
    mod.BASE_YAML = REPO / "scenarios" / "i24_replica_corrected.yaml"  # not the recorded base
    with pytest.raises(SystemExit, match="fitted on"):
        mod.write_from_artifact(RAMPS_ARTIFACT)
