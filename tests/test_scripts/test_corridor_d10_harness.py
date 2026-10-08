"""Round p16's readout harness (artifacts/i94_d10_2026-10-07/harness/corridor_d10.py), offline.

No simulation and no run tree: synthetic battery and gate artifacts in a temporary repository
(docs/PRE_FRISCO_PROGRAM.md, D10). The four criteria and their boundaries; §9.5's lock reading and the
report-only breakdown; the B5 arm rule (``_rbc`` if it holds, else ``_rb``, else the reference, and when it is
undetermined); the one-seed reproduction (a field added by later code, a changed value, a changed gate row, a
hash-policy change) and the reference it selects (committed or re-run); problems that block a reading; and the
harness's constants against the committed scenario files.
"""

from __future__ import annotations

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
PATH = REPO_ROOT / "artifacts" / "i94_d10_2026-10-07" / "harness" / "corridor_d10.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p16_harness_corridor_d10", PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()
SEEDS = m.seeds_of_record()
HASH = {
    m.REF_SCENARIO: m.REF_RECORDED_HASH,
    **{a["scenario"]: a["expected_hash"] for a in m.ARMS.values()},
}
RERUN_HASH = "0123456789ab"  # the reference re-run under another hash policy


# --------------------------------------------------------------------------- synthetic artifacts


def battery(
    scenario: str,
    *,
    departed: list[float] | None = None,
    collisions: int = 0,
    config_hash: str | None = None,
    seeds: list[int] | None = None,
) -> dict[str, Any]:
    deps = departed if departed is not None else [0.98] * len(SEEDS)
    seeds = SEEDS if seeds is None else seeds
    per_seed = [
        {
            "seed": s,
            "run_dir": f"runs/x/{s}",
            "geh_pass_fraction": 0.4,
            "rmspe": 0.5,
            "metrics": {"throughput_veh_h": 2800.0, "mean_tt_s": 750.0},
            "insertion": {
                "planned": 1000,
                "departed": round(1000 * d),
                "departed_fraction": d,
                "ramps": [{"name": m.TH61_RAMP, "planned": 100, "departed": 95, "fraction": 0.95}],
            },
            "n_collisions": 0,
            "locks": {"locked": False, "n_locks": 0, "locks": []},
            "link_hours": [
                {"station": "S1070", "clock": "06:30", "obs_veh_h": 4986.0, "sim_veh_h": 5000.0}
            ],
        }
        for s, d in zip(seeds, deps, strict=True)
    ]
    return {
        "schema": m.BATTERY_SCHEMA,
        "scenario": scenario,
        "config_hash": config_hash or HASH[scenario],
        "seeds": list(seeds),
        "replicates": len(seeds),
        "criteria_profile": {"name": m.PROFILE},
        "observations": {
            "path": m.CAL_OBS,
            "corridor": "I-94 WB",
            "dates": "20260902, 20260903, 20260908, 20260915, 20260916",
            "aggregation": "mean over dates per window (weekday typical profile)",
            "t0_local": "05:30",
            "window_s": 300.0,
            "n_stations": 14,
            "n_windows": 48,
            "n_windows_compared": 42,
            "n_link_hours": 42 * len(seeds),
            "n_speed_cells": 588 * len(seeds),
            "n_replicates": len(seeds),
            "detector_wave_speed": {"median_kmh": 19.1},
        },
        "collisions": {
            "n_runs": len(seeds),
            "n_runs_recorded": len(seeds),
            "runs_not_recorded": [],
            "total": collisions,
            "runs_with_collisions": [str(seeds[0])] if collisions else [],
        },
        "insertion": {"mean_departed_fraction": sum(deps) / len(deps)},
        "locks": {"n_runs_locked": 0, "runs_locked": [], "by_section": []},
        "geh": {
            "link_hours": {
                "rows": [
                    {
                        "station": sid,
                        "clock": "06:30",
                        "obs_veh_h": 4000.0,
                        "sim_veh_h_mean": 3900.0,
                        "geh_mean": 1.6,
                    }
                    for sid in ("S1063", "S1070", "S791")
                ]
            }
        },
        "weave_exits": {"verdict": "ok"},
        "per_seed": per_seed,
    }


def gate(
    config_hash: str,
    *,
    c1: float = 0.36,
    c3: float = 0.38,
    n: int = 20,
    c3_rows: list[float] | None = None,
) -> dict[str, Any]:
    rows = c3_rows if c3_rows is not None else [c3] * n
    checks = []
    for ds in m.DAY_SETS:
        checks += [
            {"check": "C1", "day_set": ds, "value": c1, "status": "fail", "gating": True},
            {"check": "C2", "day_set": ds, "value": 0.25, "status": "fail", "gating": False},
            {"check": "C3", "day_set": ds, "value": c3, "status": "fail", "gating": True},
            {"check": "C6", "day_set": ds, "value": 0.0, "status": "pass", "gating": True},
        ]
    checks += [
        {"check": "C4", "day_set": "calibration", "value": 5.7, "status": "fail", "gating": True},
        {"check": "C5", "day_set": "all runs", "value": 0.0, "status": "pass", "gating": True},
    ]
    return {
        "schema": m.GATE_SCHEMA,
        "config_hash": config_hash,
        "n_replicates": n,
        "verdict": "failed",
        "split": {"path": "artifacts/p1_rehearsal_2026-10-04/day_split.json", "seed": 20261004},
        "checks": checks,
        "day_sets": {
            ds: {
                "observations_path": f"artifacts/p1_rehearsal_2026-10-04/observations_{ds}.json",
                "dates": [ds],
                "quality": {"sha256": "8cae907dec40"},
                "rmspe": {
                    agg: {"ci": {"mean": c3}, "per_replicate": list(rows)} for agg in m.GATE_AGGS
                },
                "bottlenecks": {"simulated": [[] for _ in range(n)]},
            }
            for ds in m.DAY_SETS
        },
        "per_day": {
            "rows": [
                {
                    "date": "20260901",
                    "checks": [{"check": "C1", "value": 0.29, "status": "fail"}],
                }
            ]
        },
        "thresholds": {"speed_rmspe_max": 0.15, "wave_band_kmh": [14.0, 22.0]},
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(m, "REPO", tmp_path)
    monkeypatch.setattr(
        m, "scenario_hashes", lambda rel, replicates=None: {"v4": HASH[rel], "v3": "fedcba987654"}
    )
    return tmp_path


def put(repo: Path, rel: str, doc: dict[str, Any]) -> None:
    (repo / rel).write_text(json.dumps(doc))


def setup_round(
    repo: Path,
    *,
    reproduced: bool | None = True,
    ref: tuple[dict[str, Any], dict[str, Any]] | None = None,
    rbc: tuple[dict[str, Any], dict[str, Any]] | None = None,
    rb: tuple[dict[str, Any], dict[str, Any]] | None = None,
) -> None:
    """The committed reference, the reproduction record and both arms (defaults: arms equal to the reference)."""
    ref_b, ref_g = ref or (battery(m.REF_SCENARIO), gate(m.REF_RECORDED_HASH))
    put(repo, m.battery_path(m.REF_NAME), ref_b)
    put(repo, m.gate_path(m.REF_NAME), ref_g)
    if reproduced is not None:
        put(repo, m.REPRO_OUT, {"reproduced": reproduced})
    for key, pair in (("rbc", rbc), ("rb", rb)):
        arm = m.ARMS[key]
        b, g = pair or (battery(arm["scenario"]), gate(arm["expected_hash"]))
        put(repo, m.battery_path(arm["label"]), b)
        put(repo, m.gate_path(arm["label"]), g)


def arm_pair(key: str, **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    gate_kw = {k: kw.pop(k) for k in ("c1", "c3", "c3_rows") if k in kw}
    arm = m.ARMS[key]
    return battery(arm["scenario"], **kw), gate(arm["expected_hash"], **gate_kw)


def run(repo: Path) -> tuple[dict[str, Any], int]:
    out = repo / "artifacts" / "i94_d10_corridor.json"
    doc = m.evaluate(out)
    return doc, m.cmd_select(out)


# --------------------------------------------------------------------------- the criteria and the B5 arm rule


def test_rbc_is_b5s_arm_when_it_holds(repo: Path) -> None:
    setup_round(repo, rbc=arm_pair("rbc", c1=0.40, c3=0.37), rb=arm_pair("rb", c1=0.38))
    doc, code = run(repo)
    assert doc["arms"]["rbc"]["holds"] is True and doc["arms"]["rb"]["holds"] is True
    assert doc["b5_arm"]["arm"] == "rbc" and code == 0
    assert doc["b5_arm"]["scenario"] == m.ARMS["rbc"]["scenario"]
    assert (
        doc["b5_arm"]["p17_arm"] == "mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc"
    )  # stage p17's --p17-arm
    crit = doc["arms"]["rbc"]["criteria"]
    assert crit["D3"]["difference"] == pytest.approx(0.04)
    assert crit["D4"]["difference"] == pytest.approx(-0.01)
    assert doc["reference"]["label"] == m.REF_NAME and doc["reference"]["problems"] == []
    # reported with and without both rules
    for entry in (doc["reference"]["summary"], doc["arms"]["rbc"]["summary"]):
        assert {
            "C4 calibration",
            "C6 calibration",
            "C1 validation",
            "C3 validation",
            "C6 validation",
        } <= set(entry["gate_rows"])
        assert entry["validation_days"]["20260901"]["C1"]["value"] == 0.29
        assert set(entry["stations"]) == {"S1070", "S791"}  # S1063 is not a rule station
        assert entry["th61_nb_departures"]["share"] == pytest.approx(0.95)
    paired = doc["arms"]["rbc"]["paired"]["C3 15-min RMSPE, calibration days"]
    assert paired["mean"] == pytest.approx(-0.01) and paired["n"] == 20


def test_rb_is_b5s_arm_when_rbc_fails_and_rb_holds(repo: Path) -> None:
    setup_round(repo, rbc=arm_pair("rbc", c1=0.36 - 1 / 840), rb=arm_pair("rb"))
    doc, code = run(repo)
    assert doc["arms"]["rbc"]["criteria"]["D3"]["verdict"] is False
    assert doc["arms"]["rbc"]["holds"] is False and doc["arms"]["rb"]["holds"] is True
    assert doc["b5_arm"]["arm"] == "rb" and code == 10


def test_the_reference_is_b5s_arm_when_both_fail(repo: Path) -> None:
    setup_round(repo, rbc=arm_pair("rbc", collisions=1), rb=arm_pair("rb", departed=[0.96] * 20))
    doc, code = run(repo)
    assert doc["arms"]["rbc"]["criteria"]["D1"]["verdict"] is False
    assert doc["arms"]["rb"]["criteria"]["D2"]["verdict"] is False
    assert doc["b5_arm"]["arm"] == "reference" and code == 20
    assert doc["b5_arm"]["scenario"] == m.REF_SCENARIO


def test_a_seed_below_four_fifths_of_the_median_is_a_lock_and_a_breakdown(repo: Path) -> None:
    deps = [0.98] * 20
    deps[7] = 0.70  # below 0.8 x 0.98 = 0.784; the battery's mean stays 0.966 >= 0.95
    setup_round(repo, rbc=arm_pair("rbc", departed=deps))
    doc, _ = run(repo)
    d1 = doc["arms"]["rbc"]["criteria"]["D1"]
    assert d1["verdict"] is False and d1["seeds_below_floor"] == [SEEDS[7]]
    assert d1["lock_floor"] == pytest.approx(0.784)
    bd = doc["arms"]["rbc"]["summary"]["breakdowns"]
    assert bd["applies"] and bd["replicates"] == [{"seed": SEEDS[7], "departed_share": 0.70}]
    # 0.79 is not a lock (above 0.784) but is a breakdown (below 0.9 while the mean is >= 0.95)
    deps[7] = 0.79
    setup_round(repo, rbc=arm_pair("rbc", departed=deps))
    doc, _ = run(repo)
    assert doc["arms"]["rbc"]["criteria"]["D1"]["verdict"] is True
    assert [r["seed"] for r in doc["arms"]["rbc"]["summary"]["breakdowns"]["replicates"]] == [
        SEEDS[7]
    ]


@pytest.mark.parametrize(
    ("kw", "criterion", "verdict"),
    [
        ({"departed": [0.97] * 20}, "D2", True),  # the reference's 0.98 - 0.01 exactly
        ({"departed": [0.9699] * 20}, "D2", False),
        ({"c1": 0.36}, "D3", True),  # equal is not below
        ({"c1": 0.36 - 1 / 840}, "D3", False),  # one station-hour fewer
        ({"c3": 0.40}, "D4", True),  # the reference's 0.38 + 0.02 exactly
        ({"c3": 0.4001}, "D4", False),
    ],
)
def test_the_criteria_at_their_boundaries(
    repo: Path, kw: dict[str, Any], criterion: str, verdict: bool
) -> None:
    setup_round(repo, rbc=arm_pair("rbc", **kw))
    doc, _ = run(repo)
    assert doc["arms"]["rbc"]["criteria"][criterion]["verdict"] is verdict


def test_rbc_undetermined_leaves_the_arm_undetermined(repo: Path) -> None:
    """A problem in _rbc blocks the selection even when _rb holds: _rbc might have held."""
    b, g = arm_pair("rbc")
    b["seeds"] = b["seeds"][::-1]
    setup_round(repo, rbc=(b, g))
    doc, code = run(repo)
    assert doc["arms"]["rbc"]["holds"] is None and any(
        "seeds of record" in p for p in doc["arms"]["rbc"]["problems"]
    )
    assert doc["arms"]["rb"]["holds"] is True
    assert doc["b5_arm"]["arm"] is None and code == m.UNDETERMINED


def test_rbc_failing_and_rb_undetermined_leaves_the_arm_undetermined(repo: Path) -> None:
    b, g = arm_pair("rb")
    g["n_replicates"] = 19
    setup_round(repo, rbc=arm_pair("rbc", collisions=2), rb=(b, g))
    doc, code = run(repo)
    assert doc["arms"]["rbc"]["holds"] is False and doc["arms"]["rb"]["holds"] is None
    assert code == m.UNDETERMINED


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda b, g: b["observations"].update(path="artifacts/other.json"), "scored against"),
        (lambda b, g: b["observations"].update(n_windows_compared=41), "another observed side"),
        (lambda b, g: b["collisions"].update(n_runs_recorded=19), "collision counter"),
        (
            lambda b, g: b["per_seed"][3]["insertion"].update(departed_fraction=None),
            "departed share",
        ),
        (lambda b, g: b.update(config_hash="aaaaaaaaaaaa"), "is not scenarios/"),
        (lambda b, g: g.update(config_hash="bbbbbbbbbbbb"), "not its battery's"),
        (lambda b, g: g["day_sets"]["validation"].update(dates=["x"]), "validation dates"),
        (lambda b, g: g["thresholds"].update(speed_rmspe_max=0.2), "threshold speed_rmspe_max"),
    ],
)
def test_a_problem_blocks_the_arms_reading(repo: Path, edit: Any, message: str) -> None:
    b, g = arm_pair("rbc")
    edit(b, g)
    setup_round(repo, rbc=(b, g))
    doc, _ = run(repo)
    problems = doc["arms"]["rbc"]["problems"]
    assert any(message in p for p in problems), problems
    assert doc["arms"]["rbc"]["holds"] is None


def test_another_hash_than_recorded_is_a_note_not_a_problem(repo: Path) -> None:
    """A battery whose hash is its scenario file's under the run's policy, but not the one recorded."""
    b, g = arm_pair("rb", config_hash="fedcba987654")
    g["config_hash"] = "fedcba987654"
    setup_round(repo, rb=(b, g))
    doc, _ = run(repo)
    assert doc["arms"]["rb"]["holds"] is True
    assert any("not the 1f4412f6b393" in n for n in doc["notes"])


# --------------------------------------------------------------------------- the reference and its reproduction


def _one_seed(
    ref_b: dict[str, Any], ref_g: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The committed reference's first seed as a one-replicate battery and gate would record it."""
    b = copy.deepcopy(ref_b)
    b["per_seed"] = b["per_seed"][:1]
    b["per_seed"][0]["run_dir"] = "runs/elsewhere/1"
    b["seeds"] = b["seeds"][:1]
    for k in ("n_link_hours", "n_speed_cells", "n_replicates"):
        b["observations"][k] = 1
    g = copy.deepcopy(ref_g)
    for ds in m.DAY_SETS:
        for agg in m.GATE_AGGS:
            rm = g["day_sets"][ds]["rmspe"][agg]
            rm["per_replicate"] = rm["per_replicate"][:1]
        g["day_sets"][ds]["bottlenecks"]["simulated"] = g["day_sets"][ds]["bottlenecks"][
            "simulated"
        ][:1]
    return b, g


@pytest.mark.parametrize(
    ("edit", "reproduced", "differs"),
    [
        (lambda b, g: None, True, []),
        (lambda b, g: b["per_seed"][0].update(weave_releases=[{"n": 3}]), True, []),
        (lambda b, g: b.update(config_hash="395a111cb991"), True, []),
        (
            lambda b, g: b["per_seed"][0]["insertion"].update(departed=981),
            False,
            ["per_seed[0].insertion.departed"],
        ),
        (
            lambda b, g: b["per_seed"][0]["metrics"].pop("mean_tt_s"),
            False,
            ["per_seed[0].metrics.mean_tt_s"],
        ),
        (
            lambda b, g: g["day_sets"]["validation"]["rmspe"]["900"]["per_replicate"].__setitem__(
                0, 0.39
            ),
            False,
            ["gate.validation.rmspe.900.per_replicate[0]: 0.39 / 0.38"],
        ),
        (lambda b, g: b["observations"].update(window_s=900.0), False, ["observations.window_s"]),
    ],
)
def test_the_one_seed_reproduction(edit: Any, reproduced: bool, differs: list[str]) -> None:
    ref_b, ref_g = battery(m.REF_SCENARIO), gate(m.REF_RECORDED_HASH)
    one_b, one_g = _one_seed(ref_b, ref_g)
    edit(one_b, one_g)
    rec = m.reproduction(one_b, one_g, ref_b, ref_g)
    assert rec["reproduced"] is reproduced
    assert rec["differs"] == differs
    if "weave_releases" in one_b["per_seed"][0]:
        assert rec["fields_new_in_todays_record"] == ["weave_releases"]


def test_the_reproduction_reports_the_hashes_without_comparing_them() -> None:
    """The one-seed run hashes as the file at replicates 1; the committed battery as the file under v3."""
    ref_b, ref_g = battery(m.REF_SCENARIO), gate(m.REF_RECORDED_HASH)
    one_b, one_g = _one_seed(ref_b, ref_g)
    one_b["config_hash"] = m.scenario_hashes(m.REF_SCENARIO, replicates=1)["v3"]
    rec = m.reproduction(one_b, one_g, ref_b, ref_g)
    assert rec["reproduced"] is True and rec["config_hash"]["consistent"] is True
    one_b["config_hash"] = (
        m.REF_RECORDED_HASH
    )  # the file's own hash: not what a one-seed battery writes
    rec = m.reproduction(one_b, one_g, ref_b, ref_g)
    assert rec["reproduced"] is True and rec["config_hash"]["consistent"] is False


def test_cmd_repro_exit_codes(repo: Path) -> None:
    ref_b, ref_g = battery(m.REF_SCENARIO), gate(m.REF_RECORDED_HASH)
    out = repo / m.REPRO_OUT
    assert m.cmd_repro(out) == 3 and json.loads(out.read_text())["reproduced"] is None
    put(repo, m.battery_path(m.REF_NAME), ref_b)
    put(repo, m.gate_path(m.REF_NAME), ref_g)
    one_b, one_g = _one_seed(ref_b, ref_g)
    put(repo, m.battery_path(m.REPRO_LABEL), one_b)
    put(repo, m.gate_path(m.REPRO_LABEL), one_g)
    assert m.cmd_repro(out) == 0
    rec = json.loads(out.read_text())
    assert rec["reproduced"] is True and rec["then"].startswith("the committed battery")
    one_b["per_seed"][0]["geh_pass_fraction"] = 0.41
    put(repo, m.battery_path(m.REPRO_LABEL), one_b)
    assert m.cmd_repro(out) == m.EXIT_NOT_REPRODUCED
    assert json.loads(out.read_text())["differs"] == ["per_seed[0].geh_pass_fraction"]


def test_the_rerun_is_the_reference_when_the_seed_does_not_reproduce(repo: Path) -> None:
    setup_round(repo, reproduced=False)
    doc, code = run(repo)
    assert doc["reference"]["label"] == m.RERUN_LABEL
    assert any(m.battery_path(m.RERUN_LABEL) in p for p in doc["reference"]["problems"])
    assert all(a["holds"] is None for a in doc["arms"].values()) and code == m.UNDETERMINED
    # the re-run, under another hash policy than the committed battery's, is read
    HASH[m.REF_SCENARIO] = RERUN_HASH
    try:
        put(repo, m.battery_path(m.RERUN_LABEL), battery(m.REF_SCENARIO, departed=[0.99] * 20))
        put(repo, m.gate_path(m.RERUN_LABEL), gate(RERUN_HASH, c1=0.30))
        doc, code = run(repo)
    finally:
        HASH[m.REF_SCENARIO] = m.REF_RECORDED_HASH
    assert doc["reference"]["problems"] == [] and doc["reference"]["source"].startswith("re-run")
    rb = doc["arms"]["rb"]["criteria"]
    assert (
        rb["D2"]["reference"] == pytest.approx(0.99) and rb["D2"]["verdict"] is True
    )  # 0.98 >= 0.98
    assert rb["D3"]["reference"] == 0.30 and code == 0


def test_no_reproduction_record_leaves_everything_undetermined(repo: Path) -> None:
    setup_round(repo, reproduced=None)
    doc, code = run(repo)
    assert doc["reference"]["label"] is None
    assert any("no reproduction record" in p for p in doc["reference"]["problems"])
    assert code == m.UNDETERMINED
    assert m.main(["evaluate", "--out", str(repo / "x.json")]) == m.EXIT_BLOCKED


def test_select_without_a_readout_is_undetermined(tmp_path: Path) -> None:
    assert m.cmd_select(tmp_path / "missing.json") == m.UNDETERMINED


# --------------------------------------------------------------------------- the constants


def test_the_constants_name_the_committed_files() -> None:
    """Each arm's label is its scenario's name (the stage labels a battery by it), its recorded hash is the
    file's under today's policy or v3, and the reference is the committed p10 battery's scenario and hash."""
    from flowstate_core import config as fc

    v3 = {"rb": "2c25a75c0b88", "rbc": "be80c573b283"}
    for key, arm in m.ARMS.items():
        doc = yaml.safe_load((REPO_ROOT / arm["scenario"]).read_text())
        assert doc["name"] == arm["label"] == f"{m.REF_NAME}_{key}"
        now = fc.config_hash(fc.ScenarioConfig.model_validate(doc))
        assert now in (arm["expected_hash"], v3[key])
        # each hash under the policy it was computed under (docs/CONTRACTS.md section 2)
        assert fc.config_hash_v3(doc) == v3[key]
        assert m.scenario_hashes(arm["scenario"]) == {
            f"v{fc.CONFIG_HASH_VERSION}": now,
            "v3": v3[key],
        }
    ref = json.loads((REPO_ROOT / m.battery_path(m.REF_NAME)).read_text())
    assert ref["scenario"] == m.REF_SCENARIO and ref["config_hash"] == m.REF_RECORDED_HASH
    # the recorded reference hash is the scenario's under policy v3 (stage p10), which scenario_hashes offers
    ref_doc = yaml.safe_load((REPO_ROOT / m.REF_SCENARIO).read_text())
    assert fc.config_hash_v3(ref_doc) == m.REF_RECORDED_HASH
    assert m.scenario_hashes(m.REF_SCENARIO)["v3"] == m.REF_RECORDED_HASH
    assert [int(s) for s in ref["seeds"]] == SEEDS
    assert ref["observations"]["path"] == m.CAL_OBS
    assert ref["criteria_profile"]["name"] == m.PROFILE
    assert yaml.safe_load((REPO_ROOT / m.REF_SCENARIO).read_text())["name"] == m.REF_NAME
    gate_doc = json.loads((REPO_ROOT / m.gate_path(m.REF_NAME)).read_text())
    assert m.gate_problems(gate_doc, ref, m.gate_path(m.REF_NAME), "reference") == []
    assert m.battery_problems(ref, m.battery_path(m.REF_NAME), m.REF_SCENARIO, "reference") == []
