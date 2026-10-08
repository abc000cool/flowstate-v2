"""Follow-up 1 of decision A1: B2 on the k = 0 arm (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7), offline.

No simulation and no I-24 data: the harness ``artifacts/i24_discharge_2026-10-07/harness_b2_k0/corridor_b2_k0.py``
is read against committed files and synthetic batteries made from the committed step-3 k = 0 battery.

* The arm derived from committed files is the pre-registered one (policy-3 fa9eb07d549b; under policy 4
  9030f087e0af), differs from the k = 0 arm only in its name and the four ramps' values (stage p13's scenario-diff
  check), and those values are the builder's arithmetic; ``arm`` writes it and refuses a base that is not the one the
  committed ``_rc`` inputs record.
* The planned change per ramp and the committed readings §8.4.7 quotes; the committed expected artifact is what the
  harness computes now.
* The R1-R5 port reproduces stage p13's committed readout from p13's own inputs, value for value.
* ``check-ref``'s reproduction is exact and names the scenario under both hash policies.
* ``score`` on synthetic batteries: every criterion holding, the wave half failing, the speed half failing, and the
  problems that leave the reading undetermined.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DIR = REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07"
HARNESS = DIR / "harness_b2_k0" / "corridor_b2_k0.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


h = _load("b2_k0_harness_under_test", HARNESS)


def _json(path: str | Path) -> Any:
    return json.loads((REPO_ROOT / path).read_text())


@pytest.fixture(scope="module")
def derived() -> dict[str, Any]:
    return h.derive()


# --- the arm -------------------------------------------------------------------------------------


def test_the_derived_arm_is_the_preregistered_configuration(derived: dict[str, Any]) -> None:
    from flowstate_core.config import CONFIG_HASH_VERSION

    doc, ref = derived["doc"], derived["ref_doc"]
    assert h.v3_hash(doc) == h.ARM_HASH_V3 == "fa9eb07d549b"
    assert h.v3_hash(ref) == h.REF_HASH_V3 == "ae5861a4d906"
    assert h.v2_hash(ref) == "8075db417a5a"  # the k = 0 arm's header and its 2026-09-17 battery
    assert h.v3_hash(derived["base_doc"]) == "c1f91268f596"
    if CONFIG_HASH_VERSION == 4:
        assert h.current_hash(doc) == h.ARM_HASH_V4 == "9030f087e0af"
        assert h.current_hash(ref) == "5a8e079c4bfa"  # stage p12's P12_ARMS pin of the same file
    assert derived["scale"] == 0.8
    assert derived["fleet"] == "artifacts/idm_i24_capacity.json"
    assert doc["name"] == "i24_replica_flow_rc_speedcal"
    assert doc["fleet"]["idm_calibration"] == "artifacts/idm_i24_capacity.json"


def test_the_arm_differs_from_the_k0_arm_only_in_name_and_ramp_values(
    derived: dict[str, Any],
) -> None:
    """Stage p13's scenario-diff check (corridor_b2.same_configuration) on this arm, and its converse."""
    doc, ref = derived["doc"], derived["ref_doc"]
    assert h.cb2.same_configuration(doc, ref)
    assert derived["ramp_windows_changed"] == {
        "Old Hickory Blvd on-ramp": 24,
        "Hickory Hollow Pkwy off-ramp": 11,
        "Hickory Hollow Pkwy on-ramp": 24,
        "Bell Road off-ramp (collector road)": 13,
    }
    # the mainline demand and every non-ramp field are the k = 0 arm's
    assert doc["network"]["inflow"] == ref["network"]["inflow"]
    assert doc["network"]["boundary"] == ref["network"]["boundary"]
    # a change anywhere else is a different configuration
    changed = copy.deepcopy(doc)
    changed["network"]["inflow"][3][1] += 0.01
    assert not h.cb2.same_configuration(changed, ref)
    changed = copy.deepcopy(doc)
    changed["fleet"]["speed_factor"] = 1.05
    assert not h.cb2.same_configuration(changed, ref)
    # the ramp values are the builder's arithmetic on the corrected counts at s = 0.800
    assert h.cb2.ramp_values(doc) == h.cb2.expected_ramp_values(
        derived["rc_inputs"], 0.8, corrected=True
    )
    assert h.cb2.ramp_values(ref) == h.cb2.expected_ramp_values(
        derived["ref_inputs"], 0.8, corrected=False
    )


def test_arm_writes_the_derived_arm_and_refuses_another_base(
    tmp_path: Path, derived: dict[str, Any]
) -> None:
    out = tmp_path / "arm.yaml"
    assert h.cmd_arm(argparse.Namespace(out=str(out), base=h.RC_BASE)) == 0
    text = out.read_text()
    assert text.startswith("# i24_replica_flow_rc_speedcal: amendment B2 on the k = 0 I-24 arm")
    assert "name: i24_replica_flow_rc_speedcal\n" in text
    back = yaml.safe_load(text)
    assert back == derived["doc"]
    assert h.v3_hash(back) == "fa9eb07d549b"
    # a base that is not the corrected arm the committed rc inputs record: refused, nothing written
    bad_base = tmp_path / "base.yaml"
    doc = yaml.safe_load((REPO_ROOT / h.RC_BASE).read_text())
    doc["network"]["inflow"][0][1] += 0.001
    bad_base.write_text(yaml.safe_dump(doc, sort_keys=False))
    out2 = tmp_path / "arm2.yaml"
    assert h.cmd_arm(argparse.Namespace(out=str(out2), base=str(bad_base))) == h.EXIT_REFUSED
    assert not out2.exists()
    # a base that matches but carries a ramp value the inputs do not: refused the same way
    doc = yaml.safe_load((REPO_ROOT / h.RC_BASE).read_text())
    doc["network"]["ramps"][2]["inflow"][5][1] += 0.001
    bad_base.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert h.cmd_arm(argparse.Namespace(out=str(out2), base=str(bad_base))) == h.EXIT_REFUSED
    assert not out2.exists()


# --- expected values -----------------------------------------------------------------------------


def test_the_planned_change_per_ramp_and_the_committed_readings() -> None:
    e = h.expected_summary()
    p = e["planned"]
    assert (
        p["mainline_veh_h"]["reference"]
        == p["mainline_veh_h"]["arm"]
        == pytest.approx(5061.46, abs=0.01)
    )
    r = p["ramps"]
    oh, hh_on = r["Old Hickory Blvd on-ramp"], r["Hickory Hollow Pkwy on-ramp"]
    assert (oh["reference"], oh["arm"]) == (
        pytest.approx(998.08, abs=0.01),
        pytest.approx(857.87, abs=0.01),
    )
    assert oh["change_rel"] == pytest.approx(-0.1405, abs=1e-4)
    assert (hh_on["reference"], hh_on["arm"]) == (
        pytest.approx(638.30, abs=0.01),
        pytest.approx(411.59, abs=0.01),
    )
    assert hh_on["change_rel"] == pytest.approx(-0.3552, abs=1e-4)
    assert r["Hickory Hollow Pkwy off-ramp"]["change_rel"] == pytest.approx(-0.0205, abs=1e-4)
    assert r["Bell Road off-ramp (collector road)"]["change_rel"] == pytest.approx(
        -0.0587, abs=1e-4
    )
    # R3's targets are p13's (the corrected counts at the pooled recommended coverage)
    targets = [r[n]["r3_target_corrected_pooled_veh_h"] for n in r]
    assert targets == pytest.approx([915.1, 581.2, 442.6, 309.0], abs=0.05)
    assert p["vehicles_study_period"]["change"] == pytest.approx(-733.85, abs=0.01)
    c = e["reference_committed_readings"]
    assert c["R2_realized"]["mean"] == pytest.approx(0.98677, abs=1e-5)
    assert c["R4_peak_sections"]["2200"]["geh"] == pytest.approx(9.83, abs=0.005)
    assert c["R4_peak_sections"]["3200"]["geh"] == pytest.approx(10.37, abs=0.005)
    assert c["R5_rmspe_15min"]["reference"] == pytest.approx(0.2303, abs=1e-4)
    assert c["R5_wave_row"] == {
        "value": pytest.approx(15.886, abs=1e-3),
        "passed": True,
        "evaluated": True,
    }
    s = e["peak_supply"]["sections"]["2200"]
    assert s["arm_planned_veh_h"] == pytest.approx(5919.33, abs=0.01)
    assert s["arm_share_needed_for_r4"] == pytest.approx(0.9882, abs=1e-4)
    b6 = e["b6_s1"]["rows"]
    assert b6["k0_j0"]["unstable_at_capacity_density"] is True
    assert b6["k1_j0"]["unstable_at_capacity_density"] is False
    assert e["k1_p13"]["all_pass"] is True


def test_the_committed_expected_artifact_is_what_the_harness_computes() -> None:
    committed = json.loads((REPO_ROOT / h.EXPECTED).read_text())
    now = h.expected_summary()
    volatile = ("created_at", "code", "vm_snapshot", "code_dirty_paths", "code_sha256")
    for key in volatile:
        committed.pop(key, None), now.pop(key, None)
    assert now["config_hash_policy"] == committed["config_hash_policy"], (
        "the hash policy moved since b2_k0_expected.json was written: regenerate it "
        "(corridor_b2_k0.py expected)"
    )
    assert json.loads(json.dumps(now)) == committed


# --- R1-R5 as stage p13 read them ----------------------------------------------------------------


def test_the_port_reproduces_p13s_committed_readout_from_p13s_inputs() -> None:
    reading = h.p13_reading(
        _json("artifacts/i24_validation_dc_refit_p13ref.json"),
        _json("artifacts/i24_validation_dc_refit_rc.json"),
        _json("artifacts/i24_b2_ramp_flows_dc_refit_p13ref.json"),
        _json("artifacts/i24_b2_ramp_flows_dc_refit_rc.json"),
        _json(h.COUNT_CHECK),
        _json("artifacts/i24_validation_dc_refit.json"),
        "artifacts/i24_validation_dc_refit.json",
    )
    p13 = _json(h.P13_READOUT)
    assert reading["problems"] == []
    for key in (
        "criteria",
        "arms",
        "paired_b2_minus_reference",
        "reference_reproduces_committed",
        "all_pass",
        "seeds",
        "same_seeds_as_step3",
    ):
        assert json.loads(json.dumps(reading[key], default=float)) == p13[key], key


# --- check-ref -----------------------------------------------------------------------------------


def _rerun(committed: dict[str, Any]) -> dict[str, Any]:
    """The committed battery as a same-code re-run writes it: today's hash, locks recorded."""
    here = copy.deepcopy(committed)
    ref_doc = yaml.safe_load((REPO_ROOT / h.REF_SCENARIO).read_text())
    here["config_hash"] = h.current_hash(ref_doc)
    here["simulated"]["locks_per_replicate"] = [{"locked": False} for _ in here["seeds"]]
    here["criteria"] = [
        *here["criteria"],
        {"name": "no_locks", "value": 0.0, "passed": True, "evaluated": True},
    ]
    here["zero_locks"] = True
    here["observed"]["wall_s"] = 1.0
    return here


def test_check_ref_is_exact_across_hash_policies() -> None:
    committed = _json(h.COMMITTED_REF)
    ref_doc = yaml.safe_load((REPO_ROOT / h.REF_SCENARIO).read_text())
    rep = h.reproduces(_rerun(committed), committed, ref_doc)
    assert rep["exact"], rep["differs"]
    assert rep["committed_hash_policy"] == "v3"
    # the realised fraction, one count, a criteria row: each breaks it
    for mutate, field in (
        (
            lambda d: d["simulated"]["demand_realized_fraction"].__setitem__(3, 0.5),
            "demand_realized_fraction",
        ),
        (
            lambda d: d["simulated"]["counts_per_replicate"][0][1].__setitem__(2, -1),
            "counts_per_replicate",
        ),
        (lambda d: d["criteria"][0].__setitem__("value", 0.9), "criteria_rows_but_no_locks"),
        (lambda d: d.__setitem__("config_hash", "000000000000"), "config_hash"),
    ):
        bad = _rerun(committed)
        mutate(bad)
        assert field in h.reproduces(bad, committed, ref_doc)["differs"], field


# --- score on synthetic batteries ----------------------------------------------------------------


def _flows(art: dict[str, Any], veh_h: dict[str, float]) -> dict[str, Any]:
    return {
        "config_hash": art["config_hash"],
        "seeds": art["simulated"]["seeds"],
        "replicates": [
            {
                "seed": s,
                "ramps": {
                    n: {"kind": "on" if "on-ramp" in n else "off", "veh_h": q}
                    for n, q in veh_h.items()
                },
            }
            for s in art["simulated"]["seeds"]
        ],
    }


REF_RAMPS = {
    "Old Hickory Blvd on-ramp": 990.0,
    "Hickory Hollow Pkwy off-ramp": 590.0,
    "Hickory Hollow Pkwy on-ramp": 630.0,
    "Bell Road off-ramp (collector road)": 320.0,
}
ARM_RAMPS = {
    "Old Hickory Blvd on-ramp": 860.0,
    "Hickory Hollow Pkwy off-ramp": 575.0,
    "Hickory Hollow Pkwy on-ramp": 410.0,
    "Bell Road off-ramp (collector road)": 300.0,
}


def _braking(art: dict[str, Any], n: int) -> dict[str, Any]:
    per = {
        str(s): {
            "below_ms2": {"-4.5": 10, "-7.0": 2, "-8.9": n},
            "below_ms2_study_window": {"-4.5": 9, "-7.0": 2, "-8.9": n},
            "every_step_sampled": True,
        }
        for s in art["simulated"]["seeds"]
    }
    return {"config_hash": art["config_hash"], "per_seed": per}


def _setup(tmp: Path, *, arm_edit=None, ref_edit=None) -> dict[str, Path]:
    committed = _json(h.COMMITTED_REF)
    ref = _rerun(committed)
    arm = copy.deepcopy(ref)
    arm["scenario"] = h.NAME
    arm["config_hash"] = h.current_hash(h.derive()["doc"])
    arm["scenario_file"] = {"path": h.ARM, "sha256": "x"}
    sim = arm["simulated"]
    sim["demand_realized_fraction"] = [min(1.0, r + 0.004) for r in sim["demand_realized_fraction"]]
    for x in (2200.0, 3200.0):  # the peak sections flow 40 veh/h more
        i = h.cb2.section_index(arm, x)
        sim["hourly_flows_veh_h_mean"][i] = [q + 40.0 for q in sim["hourly_flows_veh_h_mean"][i]]
    if arm_edit:
        arm_edit(arm)
    if ref_edit:
        ref_edit(ref)
    paths = {
        "ref": tmp / "ref.json",
        "b2": tmp / "b2.json",
        "ref_flows": tmp / "ref_flows.json",
        "b2_flows": tmp / "b2_flows.json",
        "ref_braking": tmp / "ref_brake.json",
        "b2_braking": tmp / "b2_brake.json",
        "committed": REPO_ROOT / h.COMMITTED_REF,
        "count_check": REPO_ROOT / h.COUNT_CHECK,
        "p13": REPO_ROOT / h.P13_READOUT,
    }
    for key, art in (
        ("ref", ref),
        ("b2", arm),
        ("ref_flows", _flows(ref, REF_RAMPS)),
        ("b2_flows", _flows(arm, ARM_RAMPS)),
        ("ref_braking", _braking(ref, 0)),
        ("b2_braking", _braking(arm, 1)),
    ):
        paths[key].write_text(json.dumps(art))
    return paths


def test_score_holds_when_every_criterion_holds(tmp_path: Path) -> None:
    doc, code = h.score(_setup(tmp_path))
    assert code == 0, doc["problems"]
    assert doc["problems"] == []
    assert doc["holds"] is True and doc["failed"] == []
    assert doc["reference_reproduces_committed"]["exact"]
    c = doc["criteria"]
    assert c["R3"]["b2_geh_vs_corrected"]["Hickory Hollow Pkwy on-ramp"] == pytest.approx(
        h.cb2.geh(410.0, 442.6168182212329)
    )
    assert c["R3"]["ref_geh_vs_corrected_reported"]["Hickory Hollow Pkwy on-ramp"] > 5
    assert c["R5"]["wave_verdict"] == {"ref_passes": True, "b2_passes": True, "passed": True}
    assert doc["wave_half"]["binds"] and doc["wave_half"]["passed"]
    assert "adoptable" in doc["reading"]
    rep = doc["reported"]
    assert rep["braking"]["b2"]["steps_below_emergency_whole_run"] == 20
    assert rep["braking"]["reference"]["steps_below_emergency_whole_run"] == 0
    assert rep["locks"]["b2"]["locked_seeds"] == []
    assert rep["locks"]["b2"]["breakdown"]["seeds"] == []
    k1 = rep["k1_side_by_side"]["k1_p13"]
    p13 = _json(h.P13_READOUT)["criteria"]
    assert k1["R4"]["b2"] == p13["R4"]["b2_geh"] and k1["R5"]["wave"] == p13["R5"]["wave_verdict"]
    assert doc["paired_b2_minus_reference"]["realized"]["mean"] > 0


def _wave_fails(art: dict[str, Any]) -> None:
    for r in art["criteria"]:
        if r["name"] == "wave_speed":
            r["passed"], r["value"] = False, None


def test_the_wave_half_binds(tmp_path: Path) -> None:
    doc, code = h.score(_setup(tmp_path, arm_edit=_wave_fails))
    assert code == 0, doc["problems"]
    assert doc["holds"] is False and doc["failed"] == ["R5"]
    assert doc["criteria"]["R5"]["rmspe_15min"]["passed"] is True
    assert doc["wave_half"]["passed"] is False
    assert "wave half fails" in doc["reading"]


def test_the_speed_half_fails_with_the_wave_half_holding(tmp_path: Path) -> None:
    def slower(art: dict[str, Any]) -> None:
        sim = art["simulated"]
        sim["segment_speeds_ms_mean"] = [
            [None if v is None else v * 0.7 for v in row] for row in sim["segment_speeds_ms_mean"]
        ]

    doc, code = h.score(_setup(tmp_path, arm_edit=slower))
    assert code == 0
    assert doc["holds"] is False and doc["failed"] == ["R5"]
    assert doc["wave_half"]["passed"] is True
    assert doc["criteria"]["R5"]["rmspe_15min"]["passed"] is False
    assert "the wave half holding" in doc["reading"]


def test_r2_and_r4_read_against_the_reference(tmp_path: Path) -> None:
    def backlog(art: dict[str, Any]) -> None:
        sim = art["simulated"]
        sim["demand_realized_fraction"] = [r - 0.02 for r in sim["demand_realized_fraction"]]
        i = h.cb2.section_index(art, 2200.0)
        sim["hourly_flows_veh_h_mean"][i] = [q - 100.0 for q in sim["hourly_flows_veh_h_mean"][i]]

    doc, _ = h.score(_setup(tmp_path, arm_edit=backlog))
    assert doc["holds"] is False and doc["failed"] == ["R2", "R4"]
    assert doc["reported"]["R2_one_point_tolerance"]["passed"] is False


def test_problems_leave_the_reading_undetermined(tmp_path: Path) -> None:
    # the reference's wave row does not pass: the binding was pre-registered on its pass
    (tmp_path / "a").mkdir()
    doc, code = h.score(_setup(tmp_path / "a", ref_edit=_wave_fails))
    assert code == h.EXIT_BLOCKED and doc["holds"] is None
    assert any("pre-registered as binding" in p for p in doc["problems"])
    assert any("does not reproduce" in p for p in doc["problems"])
    # the arm ran other seeds
    (tmp_path / "b").mkdir()

    def other_seeds(art: dict[str, Any]) -> None:
        art["seeds"] = list(reversed(art["seeds"]))

    doc, code = h.score(_setup(tmp_path / "b", arm_edit=other_seeds))
    assert code == h.EXIT_BLOCKED and doc["holds"] is None
    assert any("seeds" in p for p in doc["problems"])
    # the arm's battery is not the derived arm
    (tmp_path / "c").mkdir()
    doc, code = h.score(
        _setup(tmp_path / "c", arm_edit=lambda a: a.__setitem__("config_hash", "0123456789ab"))
    )
    assert code == h.EXIT_BLOCKED
    assert any("does not name the derived arm" in p for p in doc["problems"])
    # the reference does not reproduce the committed battery
    (tmp_path / "d").mkdir()

    def drift(art: dict[str, Any]) -> None:
        art["simulated"]["demand_realized_fraction"][0] = 0.5

    doc, code = h.score(_setup(tmp_path / "d", ref_edit=drift))
    assert code == h.EXIT_BLOCKED
    assert any("does not reproduce" in p for p in doc["problems"])
    # a missing input
    (tmp_path / "e").mkdir()
    paths = _setup(tmp_path / "e")
    paths["b2_flows"].unlink()
    doc, code = h.score(paths)
    assert code == h.EXIT_BLOCKED and doc["holds"] is None
    assert len(doc["problems"]) == 1 and doc["problems"][0].startswith("missing input: b2_flows")
    # replicates on other seeds: nothing is paired (p13 would have raised), the reading is blocked
    (tmp_path / "f").mkdir()

    def other_replicates(art: dict[str, Any]) -> None:
        art["simulated"]["seeds"] = [1, *art["simulated"]["seeds"][1:]]

    doc, code = h.score(_setup(tmp_path / "f", arm_edit=other_replicates))
    assert code == h.EXIT_BLOCKED and doc["holds"] is None
    assert any("cannot be paired" in p for p in doc["problems"])
    assert doc["paired_b2_minus_reference"] == {}
    assert doc["reported"]["paired_context"]["note"].startswith("not computed")


def test_score_cli_writes_the_readout(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    out = tmp_path / "readout.json"
    ns = argparse.Namespace(
        **{k: str(v) for k, v in paths.items()},
        out=str(out),
    )
    assert h.cmd_score(ns) == 0
    doc = json.loads(out.read_text())
    assert doc["holds"] is True
    assert doc["inputs"]["committed"]["path"] == h.COMMITTED_REF
