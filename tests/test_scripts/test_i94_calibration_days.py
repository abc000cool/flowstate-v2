"""The I-94 calibration-day inputs (``scripts/i94_calibration_days.py``).

The committed files are checked against what their header and
docs/I94_CALIBRATION_DAYS.md say they are, without netconvert: each new
scenario differs from ``scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`` only in
its name, the observation-derived series, and (for the two variants) the one
setting its name announces; the demand record holds the same series and the
hashes of the inputs it was built from; the speed factor is the driver check's.
The full re-derivation (two netconvert compiles) is the ``slow`` test.

D10 (docs/PRE_FRISCO_PROGRAM.md): each arm (``…_dc_cal_w1b_w2_rb`` / ``_rbc``) differs from
Phase A's base only in its name and the series its rule changes, its header states its base,
record and hash, its record carries the rules, their evidence and draft 1's screen, the
diff proof (artifacts/i94_d10_2026-10-07/scenario_diff.json) is current, and ``--check
--only d10`` (one netconvert compile) is the stage's own first step.
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
    """Each stated hash is checked under the policy its label names (docs/CONTRACTS.md section 2):
    the committed files were written under policy v3 on 2026-10-07 and are not rewritten to relabel them."""
    head = cd.split_header(_text(rel))[0]
    match = re.fullmatch(r"# config hash ([0-9a-f]{12}) \(policy v(\d+)\)\.", head[-1])
    assert match is not None
    assert match.group(1) == cd.hash_under_policy(_doc(rel), int(match.group(2)))
    assert cd.header_policy(_text(rel)) == int(match.group(2))
    assert cd.header_hash_problems(_text(rel)) == []  # the source's and _dc_cal's hashes too
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
    # written with _dc_cal, under the policy its header names
    policy = cd.header_policy(_text(cd.OUT_CAL))
    assert policy is not None and rec["config_hash"] == cd.hash_under_policy(cal, policy)
    assert cd.record_hash_problems(rec) == []
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


def _committed_header_inputs() -> tuple[list[str], str, str, Any]:
    """The committed ``_dc_cal`` header, its date, the record's sha256 and the header facts."""
    head = cd.split_header(_text(cd.OUT_CAL))[0]
    date = re.search(r"# Written (\S+) by", "\n".join(head))
    assert date is not None
    demand = json.loads(_text(cd.DEMAND_OUT))
    return head, date.group(1), cd.sha256_of(cd.DEMAND_OUT), cd.facts_of(demand)


def test_the_header_states_the_balance_rule_the_constant_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review 2026-10-07, finding 6: the shared header said ``carry_residuals=False``
    and that the T.H.61 NB excess "stays on the mainline" whatever
    ``CARRY_RESIDUALS`` held, while docs/I94_CALIBRATION_DAYS.md §7 item 3 tells
    the owner to flip it and rewrite. The header now reads the constant; under
    the committed rule (False) it is the committed files' text byte for byte."""
    head, date, demand_sha, facts = _committed_header_inputs()
    name = str(_doc(cd.OUT_CAL)["name"])
    assert cd.CARRY_RESIDUALS is False
    written = cd._common_header(name, date, demand_sha, facts)
    # all but the file's own config-hash line, and the source's hash, which the committed line states
    # under the policy it names (v3) and the writer under today's
    src = next(i for i, line in enumerate(head) if line.startswith("# Source: "))
    assert written[:src] + written[src + 1 :] == head[:src] + head[src + 1 : -1]
    stated = re.compile(r"config hash ([0-9a-f]{12}), policy v(\d+)\)")
    have, new = stated.search(head[src]), stated.search(written[src])
    assert have is not None and new is not None
    assert stated.sub("", head[src]) == stated.sub("", written[src])
    source = _doc(cd.SOURCE)
    assert have.group(1) == cd.hash_under_policy(source, int(have.group(2)))
    assert new.group(1) == cd.scenario_hash(source)
    assert f"policy v{new.group(2)}" == cd.POLICY == f"policy v{cd.CONFIG_HASH_VERSION}"
    assert any("with carry_residuals=False" in line for line in written)

    monkeypatch.setattr(cd, "CARRY_RESIDUALS", True)
    carried = cd._common_header(name, date, demand_sha, facts)
    text = "\n".join(carried)
    assert "carry_residuals=True" in text and "carry_residuals=False" not in text
    assert "stays" not in text and "on the mainline:" not in text
    assert "carried into the next bracket's closing ramp" in text
    # an explicit rule wins over the constant
    assert cd._common_header(name, date, demand_sha, facts, carry_residuals=False) == written


@pytest.mark.slow
def test_the_committed_files_are_what_the_recipe_gives() -> None:
    """Two netconvert compiles (about a second each), no simulation."""
    assert cd.main(["--check"]) == 0


# --- D10 (docs/PRE_FRISCO_PROGRAM.md): rules (b) and (c) on Phase A's base -------------------------

D10_DOCS = [(arm, _doc(arm.out)) for arm in cd.D10_ARMS]


def test_the_d10_writer_reproduces_the_base_text_and_its_weave_params_are_explicit() -> None:
    body = cd.split_header(_text(cd.D10_BASE))[1]
    base = yaml.safe_load(body)
    assert cd.render(base, width=cd.D10_WIDTH) == body
    assert cd.render(base) != body  # the default width folds stage p10's one-line weave_params
    assert cd._weave_params_explicit(base)


@pytest.mark.parametrize(("arm", "doc"), D10_DOCS, ids=[a.suffix for a, _ in D10_DOCS])
def test_each_d10_arm_differs_from_the_base_only_in_its_rules_series(
    arm: Any, doc: dict[str, Any]
) -> None:
    base = _doc(cd.D10_BASE)
    assert doc["name"] == f"{base['name']}_{arm.suffix}"
    assert _without_series(doc) == _without_series(base)
    assert cd._changed(base, doc) == arm.changes
    assert cd._weave_params_explicit(doc)
    for key, val in cd.series_paths(base).items():
        assert len(cd.series_paths(doc)[key]) == len(val), key
    # the text, too: header aside, the same lines but the name and the changed series' values
    a = cd.split_header(_text(cd.D10_BASE))[1].splitlines()
    b = cd.split_header(_text(arm.out))[1].splitlines()
    assert len(a) == len(b)
    assert cd.render(doc, width=cd.D10_WIDTH) == cd.split_header(_text(arm.out))[1]


@pytest.mark.parametrize(("arm", "doc"), D10_DOCS, ids=[a.suffix for a, _ in D10_DOCS])
def test_the_d10_header_states_its_name_base_record_and_hash(arm: Any, doc: dict[str, Any]) -> None:
    from flowstate_core.config import CONFIG_HASH_VERSION

    head = cd.split_header(_text(arm.out))[0]
    assert head[0].startswith(f"# {doc['name']}: ")  # the ingest's provenance check reads this
    match = re.fullmatch(r"# config hash ([0-9a-f]{12}) \(policy v(\d+)\)\.", head[-1])
    assert match is not None
    assert match.group(1) == cd.scenario_hash(doc) and int(match.group(2)) == CONFIG_HASH_VERSION
    text = " ".join(line.lstrip("# ") for line in head)
    assert cd.sha256_of(cd.D10_BASE) in text and cd.sha256_of(arm.demand_out) in text
    assert cd.scenario_hash(_doc(cd.D10_BASE)) in text
    assert "records config_hash 5080d84d4725 (policy v3)" in text  # stage p10's battery
    assert cd.header_hash_problems(_text(arm.out)) == []  # every stated hash under its own label
    assert cd.DRAFT_1 in text and (cd.DRAFT_2 in text) == ("c" in arm.rules)
    assert "-316.2 veh/h" in text and "+-261.4 veh/h" in text and "rounds it to -317" in text
    assert "1,716 / 1,683 / 1,321 / 1,486 -> 1,255 / 1,208 / 1,101 / 1,165" in text
    assert "S1064->S1065" in text  # draft 1's test is also met there: said, not applied
    if "c" in arm.rules:
        assert "946 / 1,066 / 762 / 733 (0.167) -> 268 / 136 / 182 / 220 (0.053)" in text
        assert "84 / 0 / 12 / 137 (0.033) -> 268 / 136 / 182 / 220 (0.057)" in text
        assert "328 / 686 / 361 veh/h below S791" in text


@pytest.mark.parametrize(("arm", "doc"), D10_DOCS, ids=[a.suffix for a, _ in D10_DOCS])
def test_the_d10_record_holds_the_series_the_rules_and_their_evidence(
    arm: Any, doc: dict[str, Any]
) -> None:
    rec = json.loads(_text(arm.demand_out))
    assert rec["scenario"] == arm.out and rec["config_hash"] == cd.scenario_hash(doc)
    assert cd.record_hash_problems(rec) == []
    assert [list(s) for s in rec["inflow_steps"]] == doc["network"]["inflow"]
    for ramp, r in zip(doc["network"]["ramps"], rec["ramps"], strict=True):
        fields = (
            ("inflow", "inflow_steps")
            if ramp["kind"] == "on"
            else ("exit_fraction", "exit_fraction_steps")
        )
        assert [list(s) for s in r[fields[1]]] == ramp[fields[0]], ramp["name"]
    rules = rec["balance_rules"]
    assert rules["carry_residuals"] is False
    assert [d["detector"] for d in rules["ignored_ramp_detectors"]] == ["rnd_88807"]
    assert [s["station"] for s in rules["skipped_stations"]] == (
        ["S792"] if "c" in arm.rules else []
    )
    th61 = next(r for r in rec["ramps"] if r["name"] == "on-ramp 53062592")
    assert th61["method"] == "conservation" and "draft 1" in th61["detector_not_used"]
    brackets = {(b["from"], b["to"]) for b in rec["bracket_residuals"]}
    assert ("S1069", "S1070") not in brackets  # the T.H.61 NB residual is gone, not recorded
    assert (("S1948", "S791") in brackets) == ("c" in arm.rules)
    prov = rec["provenance"]
    for key, rel in (
        ("observations_sha256", cd.OBSERVATIONS),
        ("day_split_sha256", cd.DAY_SPLIT),
        ("stations_x_sha256", cd.STATIONS_X),
        ("data_quality_sha256", cd.DATA_QUALITY),
        ("base_scenario_sha256", cd.D10_BASE),
    ):
        assert prov[key] == cd.sha256_of(rel), key
    assert prov["base_config_hash"] == cd.scenario_hash(_doc(cd.D10_BASE))
    assert prov["calibration_dates"] == CAL_DATES and prov["changed"] == list(arm.changes)
    assert prov["rules"] == {r: {"b": cd.DRAFT_1, "c": cd.DRAFT_2}[r] for r in arm.rules}
    facts = prov["facts"]
    assert facts["th61_residual_4h_mean_veh_h"] == -316.2
    assert facts["th61_quadrature_band_veh_h"] == 261.4
    assert all(v < 0 for v in facts["th61_day_residuals_veh_h"])
    assert facts["s792_gap_s791_minus_s792_veh_h"]["05:30-09:30"] == 234.0
    # planned station flows: with (b) and (c) every station but S792 itself within 125 veh/h of its count
    plan = prov["station_plan_minus_count_veh_h"]
    if "c" in arm.rules:
        for sid, vals in plan["arm"].items():
            assert sid == "S792" or max(abs(v) for v in vals) < 125.0, (sid, vals)
    # draft 1's screen: met at T.H.61 NB's bracket and at Hudson Rd's, applied only where its ramp is alone
    screen = {r["segment"]: r for r in prov["draft_1_screen"]}
    assert sorted(s for s, r in screen.items() if r["meets_draft_1"]) == [
        "S1064->S1065",
        "S1069->S1070",
    ]
    assert [s for s, r in screen.items() if r["meets_draft_1"] and r["sole_ramp"]] == [
        "S1069->S1070"
    ]
    hudson = screen["S1064->S1065"]["if_applied"]["planned_4h_mean_veh_h"]
    assert (
        hudson["rnd_88833 ignored"] == hudson["rule (b) only"]
    )  # the exit already closes the bracket
    assert hudson["all ignored"]["on-ramp 18207436"] == 0.0  # both ignored: the entrance is lost
    assert screen["S790->S97"]["applies"] is False  # the T.H.52 weave exit has no detector


def test_the_d10_facts_are_the_targets_own() -> None:
    obs = json.loads(_text(cd.OBSERVATIONS))
    dq = json.loads(_text(cd.DATA_QUALITY))
    facts = cd.d10_facts(obs, dq, CAL_DATES)
    assert facts.count_error == 0.05
    assert round(facts.th61_residual, 1) == -316.2 and round(facts.th61_band, 1) == 261.4
    assert (
        round(facts.th61_down_mean - facts.th61_up_mean) - round(facts.th61_detector_mean) == -317
    )
    assert facts.th61_day_verdicts == ("ok", "ok", "ok", "suspect", "ok")
    bad = copy.deepcopy(dq)
    bad["mass_balance"]["segment_days"] = [
        r for r in bad["mass_balance"]["segment_days"] if r["date"] != "2026-09-08"
    ]
    with pytest.raises(ValueError, match="every calibration day"):
        cd.d10_facts(obs, bad, CAL_DATES)


def test_the_scenario_diff_proof_is_current(tmp_path: Path) -> None:
    """artifacts/i94_d10_2026-10-07/harness/scenario_diff.py: only the expected places differ, and the committed
    proof is what it writes today."""
    path = REPO_ROOT / "artifacts" / "i94_d10_2026-10-07" / "harness" / "scenario_diff.py"
    spec = importlib.util.spec_from_file_location("d10_scenario_diff", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    out = tmp_path / "diff.json"
    assert mod.main(["--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert json.loads(_text(mod.OUT)) == doc
    for arm in cd.D10_ARMS:
        r = doc["arms"][arm.suffix]
        assert r["only_expected_differ"] and r["scenario"] == arm.out
        assert sorted(r["values_differing"]) == sorted(
            [
                "name",
                *(
                    " / ".join(("ramp", *c.removeprefix("ramp ").rsplit(".", 1)))
                    for c in arm.changes
                ),
            ]
        )
    ev = doc["evidence"]["th61"]
    assert ev["residual_4h_mean_veh_h"] == -316.2 and ev["residual_of_rounded_means_veh_h"] == -317
    assert ev["quadrature_band_veh_h"] == 261.4 and ev["same_sign_every_calibration_day"]


@pytest.mark.slow
def test_the_d10_files_are_what_the_recipe_gives() -> None:
    """One netconvert compile (about a second), no simulation; the stage's own first step."""
    assert cd.main(["--check", "--only", "d10"]) == 0


def test_the_d10_refusal_conditions() -> None:
    """What build_d10 refuses: a base without W1b + W2 set explicitly (``_dc_cal`` sets exit_prepare alone),
    and an arm whose series differ from the base's anywhere but where its rules act."""
    assert not cd._weave_params_explicit(_doc(cd.OUT_CAL))
    base = _doc(cd.D10_BASE)
    arm = copy.deepcopy(_doc(cd.OUT_RB))
    assert cd._changed(base, arm) == cd.D10_ARMS[0].changes
    arm["network"]["boundary"]["steps"][0][1] += 0.1
    assert cd._changed(base, arm) != cd.D10_ARMS[0].changes


# --- config-hash policies (docs/CONTRACTS.md section 2; policy v4, 2026-10-07) --------------------


def test_a_recorded_hash_is_read_under_the_policy_that_gives_it() -> None:
    """Phase A's base under today's policy, v3 (stage p10's battery records it) and v2; nothing else."""
    base = _doc(cd.D10_BASE)
    battery = json.loads(_text(cd.D10_BASE_BATTERY))
    assert battery["config_hash"] == "5080d84d4725" == cd.hash_under_policy(base, 3)
    assert cd.recorded_policy("5080d84d4725", base) == 3
    assert cd.recorded_policy(cd.scenario_hash(base), base) == cd.CONFIG_HASH_VERSION
    assert cd.recorded_policy(cd.hash_under_policy(base, 2), base) == 2
    assert cd.recorded_policy("0123456789ab", base) is None
    assert cd._recorded_policy("5080d84d4725", base) == "policy v3"
    with pytest.raises(ValueError, match="none of the policies"):
        cd._recorded_policy("0123456789ab", base)
    with pytest.raises(ValueError, match="not one this code reproduces"):
        cd.hash_under_policy(base, 1)


def test_the_header_check_reads_each_hash_under_its_own_label() -> None:
    """A v3 hash relabelled v4, a hash of another document, and a statement the check cannot read are
    each a problem; ``--check`` reports them (exit 1)."""
    text = _text(cd.OUT_CAL)
    assert cd.header_hash_problems(text) == []
    relabelled = text.replace("beaaa710e6b3 (policy v3)", "beaaa710e6b3 (policy v4)")
    assert any("policy v4 gives" in p for p in cd.header_hash_problems(relabelled))
    sf = _text(cd.OUT_SF)
    # _dc_cal's hash without a label is read under the file's own policy (v3, written together)
    other = sf.replace(
        "(config hash beaaa710e6b3).", f"(config hash {cd.scenario_hash(_doc(cd.OUT_CAL))})."
    )
    assert any(cd.OUT_CAL in p for p in cd.header_hash_problems(other))
    unread = text.replace("# Status:", "# config hash 0123456789ab somewhere. Status:")
    assert any("read" in p for p in cd.header_hash_problems(unread))
    assert cd.header_hash_problems(text.replace("(policy v3).", "."))[0].startswith(
        "the last header"
    )
    for arm in cd.D10_ARMS:
        wrong = _text(arm.out).replace("5080d84d4725 (policy v3)", "5080d84d4725 (policy v4)")
        assert any("scenario" in p for p in cd.header_hash_problems(wrong))


def test_the_record_check_reads_its_hashes_under_its_scenarios_policy() -> None:
    rec = json.loads(_text(cd.DEMAND_OUT))
    assert cd.record_hash_problems(rec) == []
    today = copy.deepcopy(rec)
    # today's hash under the v3 header: wrong
    today["config_hash"] = cd.scenario_hash(_doc(cd.OUT_CAL))
    assert any("policy v3" in p for p in cd.record_hash_problems(today))
    d10 = json.loads(_text(cd.DEMAND_RB))
    d10["provenance"]["base_config_hash"] = "5080d84d4725"  # the base's v3 hash under a v4 header
    assert any("base_config_hash" in p for p in cd.record_hash_problems(d10))
    assert cd._without_hashes(rec) == cd._without_hashes(today)


def test_the_writer_labels_every_hash_with_todays_policy() -> None:
    """New headers state the current hash and ``policy v<CONFIG_HASH_VERSION>``, never a literal v3."""
    _, date, demand_sha, facts = _committed_header_inputs()
    cal, source = _doc(cd.OUT_CAL), _doc(cd.SOURCE)
    nf = cd.netfix_doc(cal, source, _doc(cd.NETFIX_SOURCE))
    lines = [
        *cd.header_cal(date, cal, demand_sha, facts),
        *cd.header_netfix(date, nf, cal, demand_sha, facts),
    ]
    text = "\n".join(lines)
    assert "policy v3" not in text
    assert f"config hash {cd.scenario_hash(source)}, {cd.POLICY})" in text
    assert f"config hash {cd.scenario_hash(cal)}, {cd.POLICY})" in text  # netfix quotes _dc_cal's
    assert lines[-1] == f"# config hash {cd.scenario_hash(nf)} ({cd.POLICY})."
    head = cd.header_cal(date, cal, demand_sha, facts)
    assert cd.header_hash_problems("\n".join(head) + "\n" + cd.render(cal)) == []
