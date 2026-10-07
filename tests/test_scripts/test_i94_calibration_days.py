"""The I-94 calibration-day inputs (``scripts/i94_calibration_days.py``).

The committed files are checked against what their header and
docs/I94_CALIBRATION_DAYS.md say they are, without netconvert: each new
scenario differs from ``scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`` only in
its name, the observation-derived series, and (for the two variants) the one
setting its name announces; the demand record holds the same series and the
hashes of the inputs it was built from; the speed factor is the driver check's.
The full re-derivation (two netconvert compiles) is the ``slow`` test.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
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


cd = _load("i94_calibration_days")


def _text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _doc(rel: str) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(cd.split_header(_text(rel))[1])
    return doc


def _without_series(doc: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    out.pop("name")
    net = out["network"]
    net.pop("inflow")
    net["boundary"].pop("steps")
    for ramp in net["ramps"]:
        ramp.pop("inflow")
        ramp.pop("exit_fraction")
    return out


CAL_DATES = ["20260902", "20260903", "20260908", "20260915", "20260916"]


@pytest.mark.parametrize("rel", [cd.SOURCE, cd.NETFIX_SOURCE])
def test_the_writer_reproduces_the_committed_text(rel: str) -> None:
    body = cd.split_header(_text(rel))[1]
    assert cd.render(yaml.safe_load(body)) == body


def test_the_targets_are_the_splits_calibration_days_quality_masked() -> None:
    obs = json.loads(_text(cd.OBSERVATIONS))
    split = json.loads(_text(cd.DAY_SPLIT))
    assert cd.check_observations(obs, split) == CAL_DATES
    bad = copy.deepcopy(obs)
    bad["source"]["dates"] = [*CAL_DATES, "20260901"]
    with pytest.raises(ValueError, match="not the calibration days"):
        cd.check_observations(bad, split)
    bad = copy.deepcopy(obs)
    bad["source"]["quality"] = None
    with pytest.raises(ValueError, match="quality-masked"):
        cd.check_observations(bad, split)


@pytest.mark.parametrize(
    ("rel", "extra"),
    [
        (cd.OUT_CAL, {}),
        (cd.OUT_NETFIX, {"unset": "1001426896,45782590"}),
        (cd.OUT_SF, {"speed_factor": True}),
    ],
)
def test_only_the_series_and_the_named_setting_differ_from_the_source(
    rel: str, extra: dict[str, Any]
) -> None:
    source, new = _doc(cd.SOURCE), _doc(rel)
    assert new["name"].startswith(f"{source['name']}_cal")
    a, b = _without_series(source), _without_series(new)
    if "unset" in extra:
        assert cd.unset_list(new) == extra["unset"] == cd.unset_list(_doc(cd.NETFIX_SOURCE))
        ex = b["network"]["netconvert_extra"]
        ex[ex.index("--ramps.unset") + 1] = cd.unset_list(source)
    if "speed_factor" in extra:
        assert "speed_factor" not in source["fleet"]
        b["fleet"].pop("speed_factor")
    assert a == b
    # every series changed, and none changed shape
    for key, val in cd.series_paths(source).items():
        assert len(cd.series_paths(new)[key]) == len(val), key
    assert new["network"]["inflow"] != source["network"]["inflow"]
    assert (
        new["network"]["boundary"]["exit_buffer_m"]
        == source["network"]["boundary"]["exit_buffer_m"]
    )


@pytest.mark.parametrize("rel", [cd.OUT_CAL, cd.OUT_NETFIX, cd.OUT_SF])
def test_the_header_states_the_files_own_config_hash(rel: str) -> None:
    head = cd.split_header(_text(rel))[0]
    match = re.fullmatch(r"# config hash ([0-9a-f]{12}) \(policy v3\)\.", head[-1])
    assert match is not None
    assert match.group(1) == cd.scenario_hash(_doc(rel))
    assert any("NOT applied" in line for line in head)
    assert any("UNCERTAIN INPUTS" in line for line in head)


def test_the_variants_carry_the_cal_series() -> None:
    cal = cd.series_paths(_doc(cd.OUT_CAL))
    assert cd.series_paths(_doc(cd.OUT_NETFIX)) == cal
    assert cd.series_paths(_doc(cd.OUT_SF)) == cal


def test_the_demand_record_holds_the_written_series_and_its_inputs() -> None:
    rec = json.loads(_text(cd.DEMAND_OUT))
    cal = _doc(cd.OUT_CAL)
    assert rec["schema"] == "flowstate.demand/1"
    assert rec["scenario"] == cd.OUT_CAL and rec["observations"] == cd.OBSERVATIONS
    assert rec["config_hash"] == cd.scenario_hash(cal)
    assert [list(s) for s in rec["inflow_steps"]] == cal["network"]["inflow"]
    for ramp, r in zip(cal["network"]["ramps"], rec["ramps"], strict=True):
        assert ramp["name"] == r["name"]
        if ramp["kind"] == "on":
            assert [list(s) for s in r["inflow_steps"]] == ramp["inflow"]
        else:
            assert [list(s) for s in r["exit_fraction_steps"]] == ramp["exit_fraction"]
    assert rec["balance_rules"]["carry_residuals"] is False
    assert rec["balance_rules"]["skipped_stations"] == []
    assert rec["balance_rules"]["ignored_ramp_detectors"] == []
    prov = rec["provenance"]
    assert prov["calibration_dates"] == CAL_DATES
    for key, rel in (
        ("observations_sha256", cd.OBSERVATIONS),
        ("day_split_sha256", cd.DAY_SPLIT),
        ("stations_x_sha256", cd.STATIONS_X),
        ("source_scenario_sha256", cd.SOURCE),
        ("replaces_sha256", cd.COMMITTED_DEMAND),
    ):
        assert prov[key] == cd.sha256_of(rel), key
    # the T.H.61 NB detector is used (its bracket closes inside the data-quality band)
    th61 = next(r for r in rec["ramps"] if r["name"] == "on-ramp 53062592")
    assert th61["station"] == "rnd_88807" and "detector_not_used" not in th61


def test_the_speed_factor_is_the_calibration_day_driver_checks() -> None:
    sf = cd.speed_factor_from(cd.TRANSFER_CHECK, CAL_DATES, cd.SOURCE)
    assert _doc(cd.OUT_SF)["fleet"]["speed_factor"] == sf.value == round(sf.needed, 4)
    assert sf.low <= sf.value <= sf.high
    raw = json.loads(_text(cd.TRANSFER_CHECK))
    assert raw["provenance"]["code_dirty"] is False


def _doctored(tmp_path: Path, edit: Any) -> Path:
    raw = json.loads(_text(cd.TRANSFER_CHECK))
    edit(raw)
    path = tmp_path / "transfer_check.json"
    path.write_text(json.dumps(raw))
    return path


def _knob(raw: dict[str, Any]) -> dict[str, Any]:
    rec = next(r for r in raw["recommendations"] if r["quantity"] == "free_flow_speed")
    knob: dict[str, Any] = next(k for k in rec["knobs"] if k["name"] == "speed_factor")
    return knob


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda r: r["provenance"]["inputs"].update(dates=CAL_DATES[:4]), "not the calibration"),
        (lambda r: r["provenance"]["argv"].remove("--start"), "study period"),
        (lambda r: _knob(r).update(needed=1.6), "outside its measured range"),
        (
            lambda r: next(
                c for c in r["comparisons"] if c["quantity"] == "free_flow_speed"
            ).update(verdict="ok"),
            "not a mismatch",
        ),
    ],
)
def test_the_speed_factor_is_refused_off_its_rules(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cd.speed_factor_from(_doctored(tmp_path, edit), CAL_DATES, cd.SOURCE)


def test_transplant_refuses_ramps_that_do_not_line_up() -> None:
    source = _doc(cd.SOURCE)
    filled = copy.deepcopy(source)
    filled["network"]["ramps"][0]["name"] = "another ramp"
    with pytest.raises(ValueError, match="does not line up"):
        cd.transplant(source, filled, "x")


@pytest.mark.slow
def test_the_committed_files_are_what_the_recipe_gives() -> None:
    """Two netconvert compiles (about a second each), no simulation."""
    assert cd.main(["--check"]) == 0
