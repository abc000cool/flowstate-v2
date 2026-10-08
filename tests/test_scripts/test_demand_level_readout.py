"""B5's readout (artifacts/pre_frisco_2026-10-07/harness_b5/demand_level_readout.py), offline.

No simulation and no real run tree. Pinned:

* the C1-C5 block adapted from round p14's readout reproduces p14's committed part (ii) reading
  (artifacts/boundary_b1b2_corridor.json) from the committed batteries, value for value;
* I-24 end to end in a temporary repository: a fit made by scripts/fit_demand_level.py on synthetic
  runs, the committed from-arm battery, and a refit battery equal to it under the fit's configuration
  (a candidate); then a backlog (C2 fails: not a candidate), a missing meta.json and other seeds
  (problems: undetermined), and a fit that stopped on constraint_unmet;
* I-94 end to end likewise, with the gate's C1 / C3 / C4 and the no-lock rule (a collapsed seed fails
  NL), and a missing gate (undetermined).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.test_scripts.test_fit_demand_level import (
    BATTERY,
    I94_BATTERY,
    I94_FROM,
    _fake_jobs,
    fdl,
    install_i94_fakes,
    readout,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
m = readout


def _art(name: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / "artifacts" / name).read_text())


# --------------------------------------------------------------------------- the copied criteria


def test_the_adapted_c1_c5_reproduce_p14s_committed_part_ii_reading() -> None:
    """Round p14's re-sequence (refit2_b2 against B2 alone) read by this harness's copy, from the
    committed batteries alone, equals what corridor_b1b2.py part_ii wrote, every value."""
    committed = _art("boundary_b1b2_corridor.json")["part_ii"]
    refit_art = _art("i24_validation_p14_refit2_b2.json")
    frm_art = _art("i24_validation_p14_b2_ref.json")
    crit = m.criteria_i24(m.battery_reading(refit_art), m.battery_reading(frm_art), refit_art)
    got = json.loads(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "rule"} for k, v in crit.items()})
    )
    want = {
        k: {kk: vv for kk, vv in v.items() if kk != "rule"}
        for k, v in committed["criteria"].items()
    }
    assert got == want
    assert m._verdicts(crit) == {"C1": True, "C2": False, "C3": False, "C4": True, "C5": None}


def test_the_battery_reading_is_the_artifact_fields_of_corridor_b1s() -> None:
    """The committed from-arm battery read by the copy: the figures the plan quotes (realised 0.967,
    hourly GEH < 5 30.6 %, 15-min RMSPE 0.254, the wave row failing)."""
    r = m.battery_reading(BATTERY)
    assert r["config_hash"] == "909b89f298c5" and len(r["seeds"]) == 20
    assert m.cb.ci(r["realised"])[0] == pytest.approx(0.96659)
    assert r["geh_lt5_share"] == pytest.approx(0.3055555555555556)
    assert r["rmspe_15min"] == pytest.approx(0.25412709845352993)
    assert r["wave_row"]["passed"] is False and r["collisions"] == [0] * 20


# --------------------------------------------------------------------------- I-24 end to end

I24_COPY = [
    "i24_validation_observed.json",
    "demand_scale_i24_flow_dc.json",
    "i24_validation_dc_refit_rc.json",
    "i24_count_consistency.json",
    "i24_b2_ramp_flows_dc_refit_rc.json",
]


@pytest.fixture
def i24_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary repository: the committed inputs, a fit by scripts/fit_demand_level.py on synthetic
    runs (chooses 0.875), and the refit battery: the from-arm's committed battery under the fit's
    configuration, with every replicate's meta.json."""
    root = tmp_path / "repo"
    (root / "artifacts").mkdir(parents=True)
    for name in I24_COPY:
        shutil.copy(REPO_ROOT / "artifacts" / name, root / "artifacts" / name)
    monkeypatch.setattr(fdl.fit24, "_run_jobs", _fake_jobs({"rounds": []}))
    fit_path = root / m.FIT["i24"]
    assert (
        fdl.main(
            [
                "--corridor",
                "i24",
                "--out",
                str(fit_path),
                "--write-scenario",
                "--scenario-out",
                str(tmp_path / "b5.yaml"),
            ]
        )
        == 0
    )
    fit = json.loads(fit_path.read_text())
    _refit_battery(root, fit["chosen"]["config_hash"])
    for mod in (m, m.cbb, m.cb):
        monkeypatch.setattr(mod, "REPO", root)
    return root


def _refit_battery(root: Path, config: str, **change: Any) -> dict[str, Any]:
    art = json.loads(json.dumps(BATTERY))
    art["config_hash"] = config
    art["scenario"] = fdl.I24_NAME
    sim = art["simulated"]
    sim.update(change)
    run_dirs = []
    for seed, c in zip(sim["seeds"], sim["n_collisions_per_replicate"], strict=True):
        rd = root / "runs" / "i24_validation" / m.I24_LABEL / config / str(seed)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "meta.json").write_text(json.dumps({"config_hash": config, "n_collisions": c}))
        run_dirs.append(str(rd))
    sim["run_dirs"] = run_dirs
    (root / "artifacts" / f"i24_validation_{m.I24_LABEL}.json").write_text(json.dumps(art))
    return art


def _eval_i24(root: Path) -> dict[str, Any]:
    return m.evaluate_i24(
        root / "out.json", m.FIT["i24"], m.I24_LABEL, root / "runs" / "i24_validation"
    )


def test_i24_a_refit_equal_to_its_from_arm_is_a_candidate(i24_repo: Path) -> None:
    doc = _eval_i24(i24_repo)
    assert doc["problems"] == []
    assert doc["verdicts"] == {"C1": True, "C2": True, "C3": True, "C4": True, "C5": None}
    assert doc["candidate"] is True and doc["c1_c5_hold"] is True
    assert all(doc["fit"]["checks"].values())
    assert doc["fit"]["rederived"]["final"]["chosen_scale"] == 0.875
    assert len(doc["fit"]["per_scale"]) == 10
    assert all("counts_per_window" not in p for r in doc["fit"]["per_scale"] for p in r["per_seed"])
    rep = doc["reported"]
    assert rep["ramp_flows"]["from_arm"]["computed"] is True
    assert (
        rep["breakdown"]["refit"]["applies"] is True
        and rep["breakdown"]["refit"]["n_breakdowns"] == 0
    )
    assert set(rep["sections_2h"]) == {"2200", "3200", "5400"}
    assert "never validation" in doc["not_validation"]
    assert json.loads((i24_repo / "out.json").read_text())["candidate"] is True


def test_i24_a_backlog_fails_c2(i24_repo: Path) -> None:
    fit = json.loads((i24_repo / m.FIT["i24"]).read_text())
    _refit_battery(i24_repo, fit["chosen"]["config_hash"], demand_realized_fraction=[0.9] * 20)
    doc = _eval_i24(i24_repo)
    assert doc["problems"] == [] and doc["verdicts"]["C2"] is False
    assert doc["candidate"] is False
    assert doc["reported"]["breakdown"]["refit"]["applies"] is False  # mean 0.9 < 0.95


def test_i24_problems_leave_the_reading_undetermined(i24_repo: Path) -> None:
    fit = json.loads((i24_repo / m.FIT["i24"]).read_text())
    art = _refit_battery(i24_repo, fit["chosen"]["config_hash"])
    (Path(art["simulated"]["run_dirs"][3]) / "meta.json").unlink()
    doc = _eval_i24(i24_repo)
    assert doc["candidate"] is None and any("no meta.json" in p for p in doc["problems"])
    assert m.main(["i24", "--out", str(i24_repo / "o.json")]) == m.EXIT_BLOCKED
    art = _refit_battery(i24_repo, fit["chosen"]["config_hash"], seeds=list(range(20)))
    doc = _eval_i24(i24_repo)
    assert "the refit and the from-arm ran different seeds" in doc["problems"]
    _refit_battery(i24_repo, "000000000000")
    doc = _eval_i24(i24_repo)
    assert any("the refit battery ran 000000000000" in p for p in doc["problems"])


def test_i24_a_fit_that_stopped_reads_constraint_unmet(
    i24_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fdl.fit24, "_run_jobs", _fake_jobs({"rounds": [], "cap": 0.9}))
    assert fdl.main(["--corridor", "i24", "--out", str(i24_repo / m.FIT["i24"])]) == 3
    doc = _eval_i24(i24_repo)
    assert doc["candidate"] is None and doc["criteria"] is None
    assert doc["reading"].startswith("constraint_unmet")
    assert doc["problems"] == []  # the fit ran as fixed; it stopped by its own rule
    assert m.main(["i24", "--out", str(i24_repo / "o.json")]) == m.EXIT_BLOCKED


# --------------------------------------------------------------------------- I-94 end to end

FROM_NAME = I94_FROM["name"]


@pytest.fixture
def i94_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "artifacts" / "p1_rehearsal_2026-10-04" / "dq").mkdir(parents=True)
    for name in (f"validation_{FROM_NAME}.json", f"baseline_gate_{FROM_NAME}.json"):
        shutil.copy(REPO_ROOT / "artifacts" / name, root / "artifacts" / name)
    shutil.copy(REPO_ROOT / m.I94_DATA_QUALITY, root / m.I94_DATA_QUALITY)
    install_i94_fakes(monkeypatch)
    fit_path = root / m.FIT["i94"]
    assert (
        fdl.main(
            [
                "--corridor",
                "i94",
                "--out",
                str(fit_path),
                "--runs-root",
                str(tmp_path / "runs"),
                "--write-scenario",
                "--scenario-out",
                str(tmp_path / "arm_b5.yaml"),
            ]
        )
        == 0
    )
    fit = json.loads(fit_path.read_text())
    _refit_i94(root, fit["scenario_out"]["config_hash"])
    for mod in (m, m.cbb, m.cb):
        monkeypatch.setattr(mod, "REPO", root)
    return root


def _refit_i94(
    root: Path, config: str, *, departed: dict[int, float] | None = None, gate: bool = True
) -> None:
    art = json.loads(json.dumps(I94_BATTERY))
    art["config_hash"] = config
    for i, f in (departed or {}).items():
        art["per_seed"][i]["insertion"]["departed_fraction"] = f
    (root / "artifacts" / f"validation_{FROM_NAME}_b5.json").write_text(json.dumps(art))
    g = _art(f"baseline_gate_{FROM_NAME}.json")
    g["config_hash"] = config
    path = root / "artifacts" / f"baseline_gate_{FROM_NAME}_b5.json"
    if gate:
        path.write_text(json.dumps(g))
    elif path.exists():
        path.unlink()


def test_i94_a_refit_equal_to_its_from_arm_is_a_candidate(i94_repo: Path) -> None:
    doc = m.evaluate_i94(i94_repo / "out.json", m.FIT["i94"])
    assert doc["problems"] == []
    assert doc["verdicts"] == {
        "C1": True,
        "C2": True,
        "C3": True,
        "C4": True,
        "C5": None,
        "NL": True,
    }
    assert doc["candidate"] is True
    assert all(doc["fit"]["checks"].values())
    gate = doc["reported"]["gate"]
    assert {
        "C1|calibration",
        "C1|validation",
        "C3|calibration",
        "C3|validation",
        "C4|calibration",
        "C6|calibration",
        "C6|validation",
    } <= set(gate)
    assert doc["refit_battery"]["gated_report"]["present"] is False
    assert m.main(["i94", "--out", str(i94_repo / "o.json")]) == 0


def test_i94_a_collapsed_seed_fails_the_no_lock_rule(i94_repo: Path) -> None:
    fit = json.loads((i94_repo / m.FIT["i94"]).read_text())
    _refit_i94(i94_repo, fit["scenario_out"]["config_hash"], departed={7: 0.5})
    doc = m.evaluate_i94(i94_repo / "out.json", m.FIT["i94"])
    assert doc["verdicts"]["NL"] is False and doc["criteria"]["NL"]["per_seed_collapsed"][7] is True
    assert doc["candidate"] is False
    assert doc["reported"]["breakdown"]["refit"]["per_replicate"][7] is True


def test_i94_a_missing_gate_leaves_the_reading_undetermined(i94_repo: Path) -> None:
    fit = json.loads((i94_repo / m.FIT["i94"]).read_text())
    _refit_i94(i94_repo, fit["scenario_out"]["config_hash"], gate=False)
    doc = m.evaluate_i94(i94_repo / "out.json", m.FIT["i94"])
    assert doc["candidate"] is None and any("missing" in p for p in doc["problems"])
