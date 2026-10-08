"""C7b's harness and stage (docs/I24_CONSISTENCY_C7B.md), offline: no simulation, no I-24 data.

* ``expected`` derives the arms from committed files: B2's own documents reproduce their policy-3 hashes
  (909b89f298c5, 219f7db55a74); rcs differs from B2 only in its name and the mainline step times (75.7 s
  early), rcc only in its name and inflow values, at B2's planned level (s = 1.080413); B2's planned /
  target ratios are C7's; the committed ``c7b_expected.json`` is what the harness computes now.
* ``arm`` writes an arm from a family that is the derivation and refuses one that is not.
* ``check-ref``'s reproduction test is exact, with ``no_locks`` and the lane block aside.
* R1-R5, the selection rule and the p15 hand-off, on synthetic readings.
* ``evaluate`` on four batteries made from the committed p13 one: no problem, every arm holds, the tie goes
  to B2; a battery without lane crossings blocks the selection.
* The stage snippet: valid bash, the harness's flags and labels, and the order of its calls (builds, B2,
  check-ref, the arms, the readout), with a failed check-ref or a refused arm stopping it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DIR = REPO_ROOT / "artifacts" / "i24_consistency_2026-10-07"
HARNESS = DIR / "harness" / "corridor_c7b.py"
SNIPPET = DIR / "stage_p23_c7b.sh.txt"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


h = _load("c7b_harness_under_test", HARNESS)
v = (
    _load("i24_validate", REPO_ROOT / "scripts" / "i24_validate.py")
    if "i24_validate" not in sys.modules
    else sys.modules["i24_validate"]
)


@pytest.fixture(scope="module")
def derived() -> dict[str, Any]:
    return h.derive("b2")


def _times(doc: dict[str, Any]) -> list[float]:
    return [t for t, _ in doc["network"]["inflow"]]


def _without(
    doc: dict[str, Any], *, name: bool = True, times: bool = False, values: bool = False
) -> dict[str, Any]:
    d = copy.deepcopy(doc)
    if name:
        d.pop("name")
    net = d["network"]
    for s in [net["inflow"]] + [r["inflow"] for r in net["ramps"] if r["kind"] == "on"]:
        for step in s:
            if times:
                step[0] = None
            if values:
                step[1] = None
    return d


# --- expected --------------------------------------------------------------------------------------


def test_the_derivation_reproduces_b2_and_changes_only_what_each_family_corrects(
    derived: dict[str, Any],
) -> None:
    d = derived
    assert h.v3_hash(d["b2"]["arm_doc"]) == "909b89f298c5"
    assert h.v3_hash(d["b2"]["base_doc"]) == "219f7db55a74"
    b2 = d["b2"]["arm_doc"]
    rcs, rcc, rccs = (d[c]["arm_doc"] for c in ("rcs", "rcc", "rccs"))
    assert d["shift"]["shift_s"] == 75.7
    assert d["shift"]["distance_m"] == pytest.approx(2452.504, abs=1e-3)
    assert d["shift"]["v0_ms"] == pytest.approx(32.3996, abs=1e-4)
    # rcs: B2 with the mainline steps after the first 75.7 s early
    assert _without(rcs, times=True) == _without(b2, times=True)
    assert _times(rcs) == [0.0] + [round(t - 75.7, 6) for t in _times(b2)[1:]]
    assert rcs["network"]["ramps"] == b2["network"]["ramps"]
    # rcc: B2 with new inflow values, at B2's planned level
    assert _without(rcc, values=True) == _without(b2, values=True)
    assert d["rcc"]["scale"] == pytest.approx(1.080413, abs=1e-6) == d["rccs"]["scale"]
    assert d["rcs"]["scale"] == 0.925
    assert h.planned_vehicles(rcc) == pytest.approx(h.planned_vehicles(b2), abs=0.05)
    # rccs: rcc's values on rcs's times
    assert _without(rccs, values=True, times=True) == _without(b2, values=True, times=True)
    assert _times(rccs) == _times(rcs)
    assert [q for _, q in rccs["network"]["inflow"]] == [q for _, q in rcc["network"]["inflow"]]


def test_the_planned_over_target_ratios(derived: dict[str, Any]) -> None:
    d = derived
    inputs = h.load_json(h.RC_INPUTS)
    c7 = json.loads((REPO_ROOT / "artifacts" / "i24_geh_diagnosis.json").read_text())
    want = [w["ratio"] for w in c7["inputs_vs_targets"]["by_coverage_window"]]
    b2 = h.planned_over_target(d["b2"]["arm_doc"], inputs, d["c_recommended"])
    assert [float(np.mean(b2[k * 3 : (k + 1) * 3])) for k in range(8)] == pytest.approx(
        want, abs=1e-3
    )
    for code in ("rcc", "rccs"):
        r = h.planned_over_target(d[code]["arm_doc"], inputs, d["c_recommended"])
        assert r == pytest.approx([d[code]["scale"]] * 24, abs=2e-4)
    literal = h.derive("s")
    assert literal["rcc"]["scale"] == 0.925
    assert h.planned_vehicles(literal["rcc"]["arm_doc"]) == pytest.approx(12534.3, abs=0.1)
    assert h.derive("target")["rcc"]["scale"] == 1.0


def test_the_committed_expected_artifact_is_what_the_harness_computes() -> None:
    committed = json.loads((DIR / "c7b_expected.json").read_text())
    now = h.expected_summary("b2")
    volatile = ("created_at", "code", "vm_snapshot", "code_dirty_paths", "code_sha256")
    for key in volatile:
        committed.pop(key, None), now.pop(key, None)
    assert now["config_hash_policy"] == committed["config_hash_policy"], (
        "the hash policy moved since c7b_expected.json was written: regenerate it (corridor_c7b.py expected)"
    )
    assert json.loads(json.dumps(now)) == committed


def test_the_lane_set_bounds_on_b2s_committed_row() -> None:
    """docs/I24_CONSISTENCY_C7B.md §3's figures: C7's 2-h like-for-like bounds carried to the row."""
    b = h.lane_set_bounds()
    assert b["sections_on_5_lane_edges_m"] == [1000.0, 4800.0]
    assert b["row_share_as_scored"] == pytest.approx(44 / 144)
    assert (
        b["row_bins_passing_by_section"]["1000"],
        b["row_bins_passing_by_section"]["4800"],
    ) == (7, 11)
    assert b["hard"] == {"row_share_min": 26 / 144, "row_share_max": 74 / 144}
    rows = sorted(m["row_bins_passing"] for m in b["mirror"].values())
    assert (rows[0], rows[-1]) == (37, 44)
    pooled = [m["station_hour_pooled_share"] for m in b["mirror"].values()]
    assert (round(min(pooled), 3), round(max(pooled), 3)) == (0.517, 0.683)
    rmean = [m["station_hour_replicate_mean_share"] for m in b["mirror"].values()]
    assert (min(rmean), max(rmean)) == (0.5, pytest.approx(8 / 12))
    assert b["station_hour_pooled_share_as_scored"] == pytest.approx(155 / 240)


# --- arm -------------------------------------------------------------------------------------------


def _built(tmp: Path, code: str, d: dict[str, Any]) -> tuple[Path, Path]:
    """The family the stage's builder and driver calibration would leave for ``code``."""
    fam = h.FAMILIES[code]
    inputs = copy.deepcopy(h.load_json(h.RC_INPUTS))
    if fam.coverage:
        for row, c in zip(inputs["coverage"]["rows"], d["c_recommended"], strict=True):
            row["coverage_equilibrium"] = row["coverage_used"]
            row["coverage_used"] = c
        inputs["demand_coverage"] = {"mode": "recommended"}
    if fam.shift:
        inputs["insertion_shift"] = {"mode": "free_flow", **d["shift"]}
    base = copy.deepcopy(d[code]["base_doc"])
    base["name"] = fam.base_name
    ip, bp = tmp / f"inputs_{code}.json", tmp / f"{fam.base_name}.yaml"
    ip.write_text(json.dumps(inputs))
    bp.write_text("# written by the test\n" + yaml.safe_dump(base, sort_keys=False))
    return ip, bp


def _arm(ip: Path, bp: Path, out: Path, code: str) -> int:
    return h.main(
        ["arm", "--family", code, "--base", str(bp), "--inputs", str(ip), "--out", str(out)]
    )


@pytest.mark.parametrize("code", ["rcs", "rcc", "rccs"])
def test_arm_writes_the_derived_arm(tmp_path: Path, derived: dict[str, Any], code: str) -> None:
    ip, bp = _built(tmp_path, code, derived)
    out = tmp_path / "arm.yaml"
    assert _arm(ip, bp, out, code) == 0
    doc = yaml.safe_load(out.read_text())
    assert doc["name"] == h.FAMILIES[code].name
    assert h.same_configuration(doc, derived[code]["arm_doc"])
    head = "".join(ln for ln in out.read_text().splitlines(keepends=True) if ln.startswith("#"))
    assert h.current_hash(doc) in head and "PROPOSED" in head
    assert "carries the B2 arm's planned vehicles over the study period" in head


def _mutate_inputs_coverage(ip: Path, bp: Path) -> None:
    d = json.loads(ip.read_text())
    d["coverage"]["rows"][0]["coverage_used"] += 0.01
    ip.write_text(json.dumps(d))


def _mutate_inputs_ramp(ip: Path, bp: Path) -> None:
    d = json.loads(ip.read_text())
    d["ramps"][0]["ramp_lane_crossings_corrected"][3] += 1
    ip.write_text(json.dumps(d))


def _mutate_inputs_shift(ip: Path, bp: Path) -> None:
    d = json.loads(ip.read_text())
    if "insertion_shift" in d:
        d["insertion_shift"]["shift_s"] = 75.0
    else:
        d["insertion_shift"] = {"mode": "free_flow", "shift_s": 75.7}
    ip.write_text(json.dumps(d))


def _mutate_base_value(ip: Path, bp: Path) -> None:
    doc = yaml.safe_load(bp.read_text())
    doc["network"]["inflow"][5][1] += 0.001
    bp.write_text(yaml.safe_dump(doc, sort_keys=False))


def _mutate_base_name(ip: Path, bp: Path) -> None:
    doc = yaml.safe_load(bp.read_text())
    doc["name"] = "i24_replica_flow_rc_corrected_dc"
    bp.write_text(yaml.safe_dump(doc, sort_keys=False))


@pytest.mark.parametrize(
    ("code", "mutate", "message"),
    [
        ("rcc", _mutate_inputs_coverage, "coverage_used"),
        ("rcs", _mutate_inputs_ramp, "corrected ramp counts differ"),
        ("rcs", _mutate_inputs_shift, "is not free_flow 75.7 s"),
        ("rcc", _mutate_inputs_shift, "'insertion_shift' present"),
        ("rccs", _mutate_base_value, "is not the derived base"),
        ("rcc", _mutate_base_name, "is named"),
    ],
)
def test_arm_refuses_a_family_that_is_not_the_derivation(
    tmp_path: Path,
    derived: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    code: str,
    mutate: Any,
    message: str,
) -> None:
    ip, bp = _built(tmp_path, code, derived)
    mutate(ip, bp)
    out = tmp_path / "arm.yaml"
    assert _arm(ip, bp, out, code) == h.EXIT_REFUSED
    assert not out.exists()
    assert message in capsys.readouterr().err


# --- check-ref -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def p13() -> dict[str, Any]:
    return json.loads((REPO_ROOT / h.P13_BATTERY).read_text())


def test_the_reproduction_is_exact_with_no_locks_and_the_lane_block_aside(
    p13: dict[str, Any],
) -> None:
    rerun = copy.deepcopy(p13)
    rerun["simulated"]["lane_crossings"] = {"anything": 1}
    for r in rerun["criteria"]:
        if r["name"] == "no_locks":
            r.update(evaluated=True, passed=True, value=0.0)
    rerun["observed"]["wall_s"] = 1.0
    rep = h.reproduces(rerun, p13)
    assert rep["exact"] and rep["differs"] == []
    assert rep["no_locks"]["row"] == {"value": 0.0, "passed": True, "evaluated": True}
    bad = copy.deepcopy(rerun)
    bad["simulated"]["counts_per_replicate"][3][2][7] += 1
    bad["criteria"][0]["value"] = -1.0
    assert h.reproduces(bad, p13)["differs"] == [
        "counts_per_replicate",
        "criteria_rows_but_no_locks",
    ]
    other = copy.deepcopy(rerun)
    other["config_hash"] = "000000000000"
    assert h.reproduces(other, p13)["differs"] == ["config_hash"]


# --- R1-R5, the selection, the hand-off -------------------------------------------------------------


def _summary(**edits: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "zero_collisions": True,
        "collisions_total": 0,
        "n_runs": 20,
        "n_runs_recorded": 20,
        "realized_mean": 0.967,
        "ramps": {"OH": {"geh_vs_corrected": 1.8}, "HH": {"geh_vs_corrected": 1.3}},
        "peak_sections": {"2200": {"geh": 3.86}, "3200": {"geh": 4.63}},
        "wave_speed_row": {"passed": False},
        "rmspe_15min": 0.254,
    }
    s.update(edits)
    return s


def test_r_criteria_read_as_p13_read_them() -> None:
    ref = _summary()
    assert all(c["passed"] for c in h.r_criteria(_summary(), ref).values())
    cases = {
        "R1": _summary(collisions_total=1, zero_collisions=False),
        "R2": _summary(realized_mean=0.9669),
        "R3": _summary(ramps={"OH": {"geh_vs_corrected": 5.0}}),
        "R4": _summary(peak_sections={"2200": {"geh": 3.87}, "3200": {"geh": 1.0}}),
        "R5": _summary(rmspe_15min=0.2741),
    }
    for name, arm in cases.items():
        rc = h.r_criteria(arm, ref)
        assert [k for k, c in rc.items() if not c["passed"]] == [name]
    assert h.r_criteria(_summary(rmspe_15min=0.274), ref)["R5"]["passed"]
    # the wave half binds only where the reference's row passes
    ref_wave = _summary(wave_speed_row={"passed": True})
    assert not h.r_criteria(_summary(), ref_wave)["R5"]["passed"]
    assert h.r_criteria(_summary(wave_speed_row={"passed": True}), ref_wave)["R5"]["passed"]


def _readings(**shares: float) -> dict[str, Any]:
    return {c: {"lane_set": {"station_hour_pooled_share": s}} for c, s in shares.items()}


def test_the_selection_rule() -> None:
    every = {"rcs": True, "rcc": True, "rccs": True}
    tie = h.select(_readings(b2=0.6, rcs=0.6, rcc=0.6, rccs=0.6), every)
    assert tie["chosen"] == "b2" and tie["tie"]
    assert h.select(_readings(b2=0.6, rcs=0.6, rcc=0.65, rccs=0.65), every)["chosen"] == "rcc"
    assert h.select(_readings(b2=0.6, rcs=0.62, rcc=0.6, rccs=0.6), every)["chosen"] == "rcs"
    # an arm that fails R1-R5 is no candidate, however good its share
    held = {"rcs": True, "rcc": False, "rccs": True}
    pick = h.select(_readings(b2=0.6, rcs=0.6, rcc=0.9, rccs=0.61), held)
    assert pick["candidates"] == ["b2", "rcs", "rccs"] and pick["chosen"] == "rccs"
    assert h.select(_readings(b2=0.6, rcs=0.9, rcc=0.9, rccs=0.9), {})["chosen"] == "b2"


def test_the_p15_handoff(derived: dict[str, Any]) -> None:
    art = {"config_hash": "abc"}
    assert h.b5_handoff("b2", derived, art)["p15_arm"] == (
        "i24_replica_flow_rc_speedcal_dc_refit:i24_replica_flow_rc_corrected_dc:dc_refit_rc"
    )
    assert h.b5_handoff("rcs", derived, art)["p15_arm"] == (
        "i24_replica_flow_rcs_speedcal_dc_refit:i24_replica_flow_rcs_corrected_dc:dc_refit_rcs"
    )
    rcc = h.b5_handoff("rcc", derived, art)
    assert rcc["p15_arm"] == (
        "i24_replica_flow_rcc_speedcal_dc_refit:i24_replica_flow_rcc_corrected_dc:dc_refit_rcc:"
        f"{derived['rcc']['scale']!r}"
    )
    assert float(rcc["p15_arm"].rsplit(":", 1)[1]) == derived["rcc"]["scale"]
    assert rcc["pipeline_args"] == f'--stages "p15_i24_b5" --p15-arm {rcc["p15_arm"]}'


# --- evaluate ------------------------------------------------------------------------------------------

COPIED = (
    "B2_ARM",
    "B2_BASE",
    "RC_INPUTS",
    "COVERAGE",
    "FIT",
    "POP",
    "BUILDER_FLEET",
    "P13_BATTERY",
    "STEP3_BATTERY",
    "LOCK_SIDECAR",
    "COUNT_CHECK",
)


def _lane_block(sim: dict[str, Any], aux_share: float) -> dict[str, Any]:
    counts = np.asarray(sim["counts_per_replicate"], dtype=np.int64)
    reps = []
    for rep in counts:
        secs = []
        for si, c in enumerate(rep):
            five = v.SECTIONS_M[si] in (1000.0, 4800.0)
            aux = (c * aux_share).astype(np.int64) if five else 0 * c
            rows = [aux, c - aux, 0 * c, 0 * c] + ([0 * c] if five else [])
            n = len(rows)
            trans = np.zeros((n, n), dtype=np.int64)
            trans[0, 0], trans[1, 1] = int(aux.sum()), int((c - aux).sum())
            secs.append({"by_lane": [r.tolist() for r in rows], "transitions": trans.tolist()})
        reps.append(secs)
    return v.lane_crossing_block(reps, counts.tolist(), v.SECTIONS_M, counts.shape[2])


def _repo(tmp: Path, derived: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp / "repo"
    for name in COPIED:
        rel = getattr(h, name)
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    p13 = json.loads((REPO_ROOT / h.P13_BATTERY).read_text())
    flows = json.loads((REPO_ROOT / "artifacts" / "i24_b2_ramp_flows_dc_refit_rc.json").read_text())
    labels = {"b2": h.REF_LABEL, **{c: f.label for c, f in h.FAMILIES.items()}}
    for code, label in labels.items():
        art = copy.deepcopy(p13)
        art["config_hash"] = h.current_hash(derived[code]["arm_doc"])
        art["simulated"]["config_hash"] = art["config_hash"]
        art["scenario"] = derived[code]["name"]
        art["simulated"]["lane_crossings"] = _lane_block(art["simulated"], 0.1)
        art["geh"]["lane_set"] = v.lane_set_geh(art["simulated"], art["observed"])
        (root / "artifacts" / f"i24_validation_{label}.json").write_text(json.dumps(art))
        fl = copy.deepcopy(flows)
        fl["config_hash"] = art["config_hash"]
        (root / "artifacts" / f"i24_b2_ramp_flows_{label}.json").write_text(json.dumps(fl))
    monkeypatch.setattr(h, "REPO", root)
    return root


def test_evaluate_reads_four_batteries_and_selects_by_the_rule(
    tmp_path: Path, derived: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, derived, monkeypatch)
    doc = h.evaluate("b2")
    assert doc["problems"] == []
    assert doc["reference_reproduces_p13"]["exact"]
    assert all(c["all_pass"] for c in doc["criteria"].values())
    sel = doc["selection"]
    assert (
        sel["candidates"] == ["b2", "rcs", "rcc", "rccs"] and sel["chosen"] == "b2" and sel["tie"]
    )
    assert sel["b5"]["p15_arm"].endswith(":dc_refit_rc")
    b2 = doc["readings"]["b2"]
    # the lane set changes only the two 5-lane sections
    for all_, lane in zip(b2["all_lanes"]["two_hour"], b2["lane_set"]["two_hour"], strict=True):
        assert (lane["sim_veh_h"] < all_["sim_veh_h"]) == (all_["section_m"] in (1000.0, 4800.0))
    assert b2["all_lanes"]["station_hour_pooled_share"] == pytest.approx(0.6458, abs=1e-4)
    assert b2["all_lanes"]["row_5min_share"] == pytest.approx(0.3056, abs=1e-4)
    # a battery without its lane block blocks the selection
    p = root / "artifacts" / f"i24_validation_{h.FAMILIES['rcc'].label}.json"
    art = json.loads(p.read_text())
    art["simulated"].pop("lane_crossings")
    p.write_text(json.dumps(art))
    assert h.main(["evaluate", "--out", str(tmp_path / "out.json")]) == h.EXIT_BLOCKED
    blocked = json.loads((tmp_path / "out.json").read_text())
    assert blocked["selection"] is None
    assert any("no lane crossings" in pr for pr in blocked["problems"])


# --- the stage snippet -----------------------------------------------------------------------------


def test_the_snippet_is_bash_and_names_what_the_harness_names() -> None:
    subprocess.run(["bash", "-n", str(SNIPPET)], check=True)
    text = SNIPPET.read_text()
    assert f"P23_H={HARNESS.relative_to(REPO_ROOT)}" in text
    assert f"P23_B2={h.B2_ARM}" in text and f"P23_REF={h.REF_LABEL}" in text
    assert re.search(r'P23_FAMILIES="rcs rcc rccs"', text)
    assert list(h.FAMILIES) == ["rcs", "rcc", "rccs"]
    for code, fam in h.FAMILIES.items():
        assert f'{code}) echo "{" ".join(fam.builder_args)}" ;;' in text
    assert '"dc_refit_$f"' in text and all(
        f.label == f"dc_refit_{c}" for c, f in h.FAMILIES.items()
    )
    assert (
        "--lane-crossings" in text
        and "check-ref" in text
        and "evaluate --out artifacts/i24_consistency_c7b.json" in text
    )
    assert "--cap-min 210" in text and '--stages "p23_c7b"' in text


STUB = r"""#!/bin/bash
echo "$*" >> "$CALLS"
if [ -n "${FAIL_ON:-}" ] && [[ "$*" == *"$FAIL_ON"* ]]; then exit "${FAIL_CODE:-3}"; fi
exit 0
"""

DRIVER = r"""set -u
STAGES="p23_c7b"; REPS=20; RING=20; PROCS=14
say() { echo "say $*" >> "$CALLS"; }
stage() { shift; "$@"; }
p4_prune() { echo "prune $1" >> "$CALLS"; }
make_archive() { echo "archive $1" >> "$CALLS"; }
source "$SNIPPET"
"""


def _dry_run(tmp: Path, **env: str) -> list[str]:
    stub = tmp / "run"
    stub.write_text(STUB)
    stub.chmod(0o755)
    (tmp / "logs").mkdir(exist_ok=True)
    calls = tmp / "calls.log"
    calls.write_text("")
    drv = tmp / "driver.sh"
    drv.write_text(f'RUN="{stub}"\n' + DRIVER)
    subprocess.run(
        ["bash", str(drv)],
        cwd=tmp,
        env={**os.environ, "CALLS": str(calls), "SNIPPET": str(SNIPPET), **env},
        check=True,
    )
    return calls.read_text().splitlines()


def _kind(call: str) -> str:
    for key, kind in (
        ("i24_build_replica.py", "build"),
        ("apply_driver_calibration.py", "calibrate"),
        (" arm --family", "arm"),
        ("i24_validate.py", "battery"),
        (" reduce ", "reduce"),
        ("check-ref", "check-ref"),
        (" evaluate ", "evaluate"),
    ):
        if key in call:
            return kind
    return call.split()[0]


def test_the_stage_runs_builds_then_b2_then_the_arms_then_the_readout(tmp_path: Path) -> None:
    calls = _dry_run(tmp_path)
    kinds = [_kind(c) for c in calls]
    assert kinds == (
        ["build", "calibrate", "arm"] * 3
        + ["battery", "reduce", "prune", "check-ref", "archive"]
        + ["battery", "reduce", "prune", "archive"] * 3
        + ["evaluate"]
    )
    builds = [c for c in calls if _kind(c) == "build"]
    for code, call in zip(h.FAMILIES, builds, strict=True):
        assert f"--suffix flow_{code} " in call and call.endswith(
            " ".join(h.FAMILIES[code].builder_args)
        )
        assert (
            "--ramp-through-traffic exclude --count-consistency artifacts/i24_count_consistency.json"
            in call
        )
    batteries = [c for c in calls if _kind(c) == "battery"]
    assert f"--scenario {h.B2_ARM} --label {h.REF_LABEL}" in batteries[0]
    for code, call in zip(h.FAMILIES, batteries[1:], strict=True):
        assert f"--scenario {h.FAMILIES[code].scenario} --label {h.FAMILIES[code].label}" in call
    assert all("--lane-crossings --" not in c and c.endswith("--lane-crossings") for c in batteries)
    assert all(
        "--replicates 20 --procs 14 --analysis-procs 8 --ring-seeds 20" in c for c in batteries
    )


def test_a_failed_check_ref_runs_no_arm(tmp_path: Path) -> None:
    calls = _dry_run(tmp_path, FAIL_ON="check-ref")
    kinds = [_kind(c) for c in calls]
    after = kinds[kinds.index("check-ref") + 1 :]
    assert after == ["say", "say"] and "does not reproduce" in calls[-2]
    assert calls[-1] == "say p23_c7b failed; continuing"
    assert kinds.count("battery") == 1 and "evaluate" not in kinds


def test_a_refused_arm_runs_nothing(tmp_path: Path) -> None:
    calls = _dry_run(tmp_path, FAIL_ON="arm --family rcc", FAIL_CODE="2")
    kinds = [_kind(c) for c in calls]
    assert "battery" not in kinds and kinds[-2:] == ["say", "say"]
    assert "the rcc arm was refused" in calls[-2]
    assert kinds.count("arm") == 2  # rcs written, rcc refused, rccs never tried


# --- the pipeline and the ingest -------------------------------------------------------------------


def test_the_pipeline_carries_the_snippet_verbatim_before_the_done_marker() -> None:
    text = SNIPPET.read_text()
    block = text[text.index("# p23 (opt-in;") :]
    pipeline = (REPO_ROOT / "scripts" / "gcp" / "pipeline_i24.sh").read_text()
    assert pipeline.count(block) == 1
    at = pipeline.index(block)
    assert at < pipeline.index("# 9. Done marker") and at > pipeline.index(
        "stage p21_i80_merge p21_steps"
    )
    subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "gcp" / "pipeline_i24.sh")], check=True
    )


def test_the_ingest_installs_every_p23_output(tmp_path: Path) -> None:
    from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    seed = "6914975401685141156"
    artifacts = ["i24_consistency_c7b.json", f"i24_validation_{h.REF_LABEL}.json"]
    scenarios = {}
    runs = {}
    for code, fam in h.FAMILIES.items():
        artifacts += [
            f"i24_replica_inputs_flow_{code}.json",
            f"demand_i24_flow_{code}.json",
            f"i24_validation_{fam.label}.json",
            f"i24_b2_ramp_flows_{fam.label}.json",
        ]
        for stem in (
            f"i24_replica_flow_{code}",
            f"i24_replica_flow_{code}_corrected",
            fam.base_name,
            fam.name,
        ):
            scenarios[f"scenarios/{stem}.yaml"] = f"name: {stem}\n"
        runs[f"runs/i24_validation/{fam.label}/abc/{seed}/meta.json"] = '{"run": 1}'
    files = {f"artifacts/{a}": f'{{"name": "{a}"}}' for a in artifacts}
    files |= scenarios | runs | {"logs/PIPELINE_EXIT": "rc=0\n"}
    tgz = _tgz(tmp_path / "final.tgz", files)
    r = subprocess.run(
        [BASH, str(GCP / "ingest_pipeline_results.sh"), str(tgz)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for a in artifacts:
        assert f"artifact {a}\n" in r.stdout, a
    for s in scenarios:
        assert f"scenario {Path(s).name}\n" in r.stdout, s
    for fam in h.FAMILIES.values():
        assert f"runs: i24_validation/{fam.label}" in r.stdout
    assert "NOT ingested" not in r.stdout, r.stdout
