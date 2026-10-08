"""Round p14's readout harness (artifacts/i24_discharge_2026-10-07/harness_b1b2/), offline.

No simulation and no run tree of a real run: the B1 copies and the refit base's recipe on the
committed scenarios in a temporary directory, the "as p4 ran it" checks on p4's own committed fit,
and the readout end to end on synthetic batteries in a temporary repository (docs/I24_DISCHARGE_
DIAGNOSIS.md §8.4.5): part (i)'s reading and the arm it selects for the re-sequence, a B2 re-run
that does not reproduce the committed p13 battery (blocks), rc inputs whose boundary zone differs
(blocks), the figures part (i) reports for the write-up (the collapsed seed, paired per-replicate
RMSPEs), part (ii)'s C1-C5 and candidate reading (a fit not run as p4 ran it, on another arm than part
(i) selects, or scored against another observed side than its from-arm, blocks), and the code
provenance (source commit and VM snapshot).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig, config_hash, config_hash_v3

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH = REPO_ROOT / "artifacts" / "i24_discharge_2026-10-07" / "harness_b1b2" / "corridor_b1b2.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p14_harness_corridor_b1b2", PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


# --------------------------------------------------------------------------- on the committed files


def test_the_b1_copies_and_the_refit_base_are_the_registered_configurations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The B1 + B2 arm is the committed B2 arm with the factor (582ba839b84c under config-hash policy v4;
    e19e5ab64186 under v3, the hash the committed p14 battery records, §8.4.5); the B1 copy of the rc base
    at _dc_refit's carried 0.925 is that arm's configuration, and the rc base at 0.925 is B2's. Stage p14
    ran under policy 3; the harness's ``*_HASH`` constants are the v4 values the stage now pins, and its
    ``*_HASH_V3`` constants the committed records' values, so both are checked here."""
    import yaml

    for stem, v4, v3 in (
        ("i24_replica_flow_rc_speedcal_dc_refit", m.B2_HASH, m.B2_HASH_V3),
        ("i24_replica_flow_rc_speedcal_dc_refit_b1", m.B1B2_HASH, m.B1B2_HASH_V3),
    ):
        doc = yaml.safe_load((REPO_ROOT / "scenarios" / f"{stem}.yaml").read_text())
        assert config_hash(ScenarioConfig.model_validate(doc)) == v4, stem
        assert config_hash_v3(doc) == v3, stem
    arm = tmp_path / "scenarios" / "i24_replica_flow_rc_speedcal_dc_refit_b1.yaml"
    base = tmp_path / "scenarios" / "i24_replica_flow_rc_corrected_dc_b1.yaml"
    arm.parent.mkdir(parents=True)
    h_arm = m.cb.make_copy(
        REPO_ROOT / "scenarios" / "i24_replica_flow_rc_speedcal_dc_refit.yaml",
        arm,
        m.FACTOR,
        m.B2_HASH,
        "p14_i24_b1b2",
    )
    assert h_arm == m.B1B2_HASH
    head = arm.read_text().splitlines()
    assert head[0].startswith("# i24_replica_flow_rc_speedcal_dc_refit_b1: ")
    assert any("stage p14_i24_b1b2" in ln for ln in head[:6])
    assert (
        m.cb.make_copy(
            REPO_ROOT / "scenarios" / "i24_replica_flow_rc_corrected_dc.yaml",
            base,
            m.FACTOR,
            "1a7797ea614d",
            "p14_i24_b1b2",
        )
        == "bb370800336e"
    )
    # B2 alone, on the committed files
    r = m.base_reproduces_from_arm(
        "scenarios/i24_replica_flow_rc_corrected_dc.yaml",
        m.B2_HASH,
        "i24_replica_flow_rc_speedcal_dc_refit",
    )
    assert r["ok"] and r["carried_scale"] == 0.925
    # B1 + B2, on the copy
    (tmp_path / "artifacts").mkdir()
    shutil.copy(REPO_ROOT / m.P4_FIT, tmp_path / m.P4_FIT)
    monkeypatch.setattr(m, "REPO", tmp_path)
    r = m.base_reproduces_from_arm(
        "scenarios/i24_replica_flow_rc_corrected_dc_b1.yaml",
        m.B1B2_HASH,
        "i24_replica_flow_rc_speedcal_dc_refit_b1",
    )
    assert r["ok"], r
    r = m.base_reproduces_from_arm(
        "scenarios/i24_replica_flow_rc_corrected_dc_b1.yaml",
        m.B2_HASH,
        "i24_replica_flow_rc_speedcal_dc_refit_b1",
    )
    assert not r["ok"]


def test_p4s_own_fit_passes_the_as_p4_checks_and_a_changed_one_does_not() -> None:
    p4 = json.loads((REPO_ROOT / m.P4_FIT).read_text())
    plan = {"base": "scenarios/i24_replica_flow_corrected_dc.yaml"}
    assert m.fit_checks(p4, plan)["as_p4"]
    other = json.loads(json.dumps(p4))
    other["seed"] = 1
    other["grid"] = [r for r in other["grid"] if r["scale"] != 0.95]  # a refine scale missing
    other["selection"] = {"min_inserted": 0.98}
    fc = m.fit_checks(other, plan)
    assert not fc["as_p4"]
    assert set(fc["differs"]) == {"seed_as_p4", "grid_procedure", "no_selection_options"}
    assert m.fit_checks(p4, {"base": "scenarios/x.yaml"})["differs"] == ["base_scenario"]


def test_the_committed_b2_battery_reproduces_itself_and_not_another() -> None:
    a = json.loads((REPO_ROOT / m.P13_BATTERY).read_text())
    assert m.reproduces(a, m.P13_BATTERY)["exact"]
    b = json.loads((REPO_ROOT / m.STEP3_BATTERY).read_text())
    r = m.reproduces(b, m.P13_BATTERY)
    assert not r["exact"] and "counts_per_replicate" in r["differs"]
    # the committed rc inputs' zone and coordinates are the flow family's
    assert m.inputs_problems() == []


# --------------------------------------------------------------------------- synthetic batteries

SEEDS = [11, 22, 33]
SCHEDULE = [[0.0, 10.0], [630.0, 20.0]]  # study-window mean 15 m/s over sim 600-660 s
SECTIONS = [2200.0, 3200.0, 5400.0]


def _field(run: Path, v0: float, v1: float) -> None:
    """15-s x 100-m cells over sim 600-660 s: the zone's columns (150, 250 m; zone 100-300 m) at speed
    ``v0`` (density 0.06) in the first 30-s window and ``v1`` (0.02) in the second."""
    rows = []
    for t in (607.5, 622.5, 637.5, 652.5):
        k, v = (0.06, v0) if t < 630.0 else (0.02, v1)
        rows += [{"t_bin": t, "x_bin": x, "density": k, "flow": k * v} for x in (150.0, 250.0)]
    run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(run / "edges.parquet")


def _battery(
    root: Path,
    label: str,
    factor: float | None,
    v: tuple[float, float],
    *,
    realised: float = 0.9,
    seg: float = 10.0,
    geh_share: float = 0.3,
    config: str | None = None,
    count: int = 34,
) -> dict[str, Any]:
    """A battery as scripts/i24_validate.py and the runner record it (artifact, meta.json, edges.parquet,
    braking counts); 100 vehicles per section in 60 s, 6,000 veh/h, unless ``count`` changes the first."""
    config = config or label
    run_dirs = []
    for seed in SEEDS:
        rd = root / "runs" / "i24_validation" / label / config / str(seed)
        _field(rd, *v)
        meta = {
            "config_hash": config,
            "n_collisions": 0,
            "boundary": {} if factor is None else {"limit_factor": factor},
            "config": {"network": {"boundary": {"steps": SCHEDULE}}},
        }
        (rd / "meta.json").write_text(json.dumps(meta))
        run_dirs.append(str(rd))
    field = [[seg, 20.0]] * 3
    obs_field = [[10.0, 20.0]] * 3
    counts = [[count, 33, 33], [34, 33, 33], [34, 33, 33]]
    art = {
        "config_hash": config,
        "scenario": label,
        "seeds": SEEDS,
        "zero_collisions": True,
        "simulated": {
            "seeds": SEEDS,
            "run_dirs": run_dirs,
            "counts_per_replicate": [counts for _ in SEEDS],
            "counts_mean": counts,
            "hourly_flows_veh_h_mean": [[c * 60.0 for c in row] for row in counts],
            "n_collisions_per_replicate": [0] * 3,
            "demand_realized_fraction": [realised] * 3,
            "waves_per_replicate": [{"n_backward": 3}] * 3,
            "waves_stripe_per_replicate": [{"n_backward": 2}] * 3,
            "segment_speeds_ms_mean": field,
            "segment_speeds_ms_per_replicate": [field] * 3,
        },
        "observed": {
            "t_range_s": [1800.0, 1860.0],
            "n_windows": 3,
            "window_s": 20.0,
            "sections_m": SECTIONS,
            "hourly_flows_veh_h_recommended": [[6000.0] * 3] * 3,
            "segment_speeds_ms": obs_field,
        },
        "criteria": [
            {"name": "link_flows_geh", "value": geh_share, "passed": False, "evaluated": True},
            {"name": "speeds_rmspe", "value": 0.0, "passed": True, "evaluated": True},
            {"name": "wave_speed", "value": None, "passed": False, "evaluated": True, "detail": ""},
            {"name": "no_collisions", "value": 0.0, "passed": True, "evaluated": True},
        ],
        "rmspe": {"value": 0.0},
    }
    (root / "artifacts" / f"i24_validation_{label}.json").write_text(json.dumps(art))
    per_seed = {str(s): {"below_ms2": {"-8.9": 0}} for s in SEEDS}
    (root / "runs" / "i24_validation" / label / "hard_braking.json").write_text(
        json.dumps({"per_seed": per_seed})
    )
    # the ramp-flow reduction (corridor_b2.py reduce): every ramp on its corrected count
    reps = [
        {"seed": s, "ramps": {"On A": {"veh_h": 960.0}, "Off B": {"veh_h": 480.0}}} for s in SEEDS
    ]
    (root / "artifacts" / f"i24_b2_ramp_flows_{label}.json").write_text(
        json.dumps({"config_hash": config, "seeds": SEEDS, "replicates": reps})
    )
    return art


INPUTS = {
    "geometry": {"sim_x_of_data_x": {"a": 0.0, "b": 1.0}},
    "boundary": {
        "x_range_m": [100.0, 300.0],
        "window_s": 30.0,
        "schedule_data_time": [[1800, 10.0]],
    },
}
COUNT_CHECK = {
    "parameters": {"pooled_coverage_per_window": [0.5, 0.5], "window_s": 300.0},
    "ramps": [
        # corrected: (50 - 10) x 12 / 0.5 = 960 veh/h; recorded 1,200
        {
            "name": "On A",
            "kind": "on",
            "counts_per_window": [50, 50],
            "prior_mainline_per_window": [10, 10],
        },
        {
            "name": "Off B",
            "kind": "off",
            "counts_per_window": [20, 20],
            "later_mainline_per_window": [0, 0],
        },
    ],
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """B2 alone (zone 5 then 15 m/s: 10 m/s, error -5 against the schedule's 15) and B1 + B2 (12 then
    20 m/s: 16 m/s, error +1), both at 6,000 veh/h at 5,400 m; B1 + B2 realises 0.95 against 0.9. The
    committed p13 battery is B2's own artifact, step 3's seeds are the batteries', and _dc_refit + B1
    (stage p12) is a battery like B2's: part (i) holds and selects B1 + B2."""
    root = tmp_path / "repo"
    (root / "artifacts").mkdir(parents=True)
    for name in ("i24_replica_inputs_flow.json", "i24_replica_inputs_flow_rc.json"):
        (root / "artifacts" / name).write_text(json.dumps(INPUTS))
    (root / "artifacts" / "i24_count_consistency.json").write_text(json.dumps(COUNT_CHECK))
    b2 = _battery(root, m.REF_LABEL, None, (5.0, 15.0), config=m.B2_HASH)
    _battery(root, m.ARM_LABEL, m.FACTOR, (12.0, 20.0), realised=0.95, config=m.B1B2_HASH)
    (root / m.P13_BATTERY).write_text(json.dumps(b2))
    (root / m.STEP3_BATTERY).write_text(json.dumps({"seeds": SEEDS}))
    p12 = _battery(root, "p12_ref_stub", m.FACTOR, (12.0, 20.0), realised=0.93)
    (root / m.P12_B1_BATTERY).write_text(json.dumps(p12))
    monkeypatch.setattr(m, "REPO", root)
    monkeypatch.setattr(m.cb, "REPO", root)
    return root


def _evaluate(root: Path, resequence: str | None = None) -> dict[str, Any]:
    return m.evaluate(root / "out.json", root / "runs" / "i24_validation", resequence)


def test_part_i_reads_a1_to_a5_against_b2_alone_and_selects_b1b2(repo: Path) -> None:
    doc = _evaluate(repo)
    p1 = doc["part_i"]
    assert p1["problems"] == []
    assert p1["reading"] == {
        "A1": True,
        "A2": True,
        "A3": True,
        "A4": True,
        "A5": True,
        "holds": True,
        "rule": p1["reading"]["rule"],
    }
    zone = p1["criteria"]["A2"]["zone"]
    assert zone["reference_error_kmh"] == pytest.approx(-5.0 * 3.6)
    assert zone["b1_error_kmh"] == pytest.approx(1.0 * 3.6)
    assert (
        p1["criteria"]["A4"]["wave_verdict"]["ok"] is None
    )  # B2 fails the wave row: the half does not bind
    assert p1["reference_reproduces_p13"]["exact"] and p1["same_seeds_as_step3"]
    assert p1["expected_hashes"]["b1b2"]["matches"] and p1["expected_hashes"]["b2"]["matches"]
    ramps = p1["reported"]["ramp_flows"]["b1b2"]
    assert ramps["computed"] and ramps["On A"]["geh_vs_corrected"] == pytest.approx(0.0)
    assert ramps["On A"]["recorded_pooled_veh_h"] == pytest.approx(1200.0)
    r = p1["reported"]["r1_r5_second_arm"]
    assert r["criteria"] == {"R1": True, "R2": True, "R3": True, "R4": True, "R5": True}
    assert (
        p1["reported"]["peak_targets"]["as_recorded"] is False
    )  # synthetic targets are not 6,626 / 6,639
    assert doc["part_ii"]["status"].startswith("not in this readout")
    assert m.cmd_select(repo / "out.json") == 0


def test_a_reading_that_does_not_hold_selects_b2_alone(repo: Path) -> None:
    """B1 + B2 at 30 m/s on the first segment against the recording's 10: A5 fails."""
    _battery(repo, m.ARM_LABEL, m.FACTOR, (12.0, 20.0), realised=0.95, seg=30.0, config=m.B1B2_HASH)
    doc = _evaluate(repo)
    assert doc["part_i"]["reading"]["A5"] is False and doc["part_i"]["reading"]["holds"] is False
    assert m.cmd_select(repo / "out.json") == 10


def test_a_b2_rerun_that_does_not_reproduce_p13_blocks_the_reading(repo: Path) -> None:
    committed = json.loads((repo / m.P13_BATTERY).read_text())
    committed["simulated"]["counts_per_replicate"][1][0][0] += 1
    (repo / m.P13_BATTERY).write_text(json.dumps(committed))
    doc = _evaluate(repo)
    p1 = doc["part_i"]
    assert p1["reference_reproduces_p13"]["differs"] == ["counts_per_replicate"]
    assert all(p1["reading"][k] for k in ("A1", "A2", "A3", "A4", "A5"))
    assert p1["reading"]["holds"] is None
    assert any("does not reproduce" in p for p in p1["problems"])
    assert m.cmd_select(repo / "out.json") == m.SELECT_UNDETERMINED


def test_rc_inputs_with_another_boundary_zone_block_the_reading(repo: Path) -> None:
    rc = json.loads(json.dumps(INPUTS))
    rc["boundary"]["x_range_m"] = [100.0, 400.0]
    (repo / m.RC_INPUTS).write_text(json.dumps(rc))
    p1 = _evaluate(repo)["part_i"]
    assert p1["reading"]["holds"] is None
    assert any("boundary.x_range_m" in p for p in p1["problems"])


def test_select_without_a_readout_is_undetermined(tmp_path: Path) -> None:
    assert m.cmd_select(tmp_path / "none.json") == m.SELECT_UNDETERMINED


# --------------------------------------------------------------------------- part (ii)


def _fit(root: Path, plan: dict[str, Any], chosen: float, *, seed: int = 7) -> None:
    """A fit artifact as scripts/i24_fit_demand_scale.py writes one, on the p4 procedure: the coarse grid,
    best 0.9, then 0.825-0.975 around it; ``chosen`` has the smallest fit-hour RMSPE."""
    coarse = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1]
    refine = [0.825, 0.85, 0.875, 0.925, 0.95, 0.975]

    def rmspe(s: float) -> float:
        return 0.1 if s == chosen else (0.2 if s == 0.9 else 0.3 + abs(s - 0.9))

    grid = [
        {"scale": s, "inserted_fraction": 1.0 - 0.1 * s, "rmspe_train": rmspe(s), "rmspe_test": 0.4}
        for s in sorted(coarse + refine)
    ]
    p4 = {
        "base": "corrected",
        "fleet_artifact": m.POP,
        "seed": 7,
        "objective": "speed objective",
        "best": {"scale": 0.925},
    }
    (root / m.P4_FIT).write_text(json.dumps(p4))
    fit = {
        "base": "corrected",
        "base_scenario": plan["base"],
        "fleet_artifact": m.POP,
        "seed": seed,
        "objective": "speed objective",
        "grid": grid,
        "best": {"scale": chosen, "config_hash": "refit_hash"},
    }
    (root / plan["fit"]).write_text(json.dumps(fit))


@pytest.fixture
def resequenced(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fit on B1 + B2 chose 0.95; its battery realises 0.948 (0.2 points below B1 + B2's 0.95, inside
    the one-point tolerance) with a better GEH share and the same speeds."""
    plan = m.RESEQ["b1b2"]
    _fit(repo, plan, 0.95)
    _battery(
        repo,
        plan["label"],
        m.FACTOR,
        (12.0, 20.0),
        realised=0.948,
        geh_share=0.4,
        config="refit_hash",
    )
    monkeypatch.setattr(
        m, "base_reproduces_from_arm", lambda base, h, name: {"checked": True, "ok": True}
    )
    return repo


def test_part_ii_reads_c1_to_c5_against_the_arm_it_was_refit_from(resequenced: Path) -> None:
    doc = _evaluate(resequenced, "b1b2")
    p2 = doc["part_ii"]
    assert p2["problems"] == []
    assert {k: v["verdict"] for k, v in p2["criteria"].items()} == {
        "C1": True,
        "C2": True,
        "C3": True,
        "C4": True,
        "C5": None,
    }
    assert p2["criteria"]["C2"]["literal_reading_ge_from_arm"] is False
    assert p2["candidate"] is True
    assert p2["fit"]["checks"]["as_p4"] and p2["fit"]["chosen_scale"] == 0.95
    assert p2["fit"]["level_unchanged"] is False
    assert p2["fit"]["insertion_reanalysis_reported"]["by_threshold"][0]["min_inserted"] == 0.98
    assert p2["reported"]["sections_2h"]["5400"]["refit"][0] == pytest.approx(6000.0)
    assert "never validation" in p2["not_validation"]


def test_a_backlog_beyond_one_point_is_not_a_candidate(resequenced: Path) -> None:
    plan = m.RESEQ["b1b2"]
    _battery(resequenced, plan["label"], m.FACTOR, (12.0, 20.0), realised=0.93, config="refit_hash")
    p2 = _evaluate(resequenced, "b1b2")["part_ii"]
    assert p2["criteria"]["C2"]["verdict"] is False
    assert p2["problems"] == [] and p2["candidate"] is False


def test_a_fit_not_run_as_p4_or_on_the_wrong_arm_blocks_the_reading(resequenced: Path) -> None:
    _fit(resequenced, m.RESEQ["b1b2"], 0.95, seed=8)
    p2 = _evaluate(resequenced, "b1b2")["part_ii"]
    assert p2["candidate"] is None
    assert any("seed_as_p4" in p for p in p2["problems"])
    p2 = _evaluate(resequenced, "b2")["part_ii"]  # part (i) selects b1b2
    assert p2["candidate"] is None
    assert any("part (i)'s reading selects b1b2" in p for p in p2["problems"])


def test_a_refit_scored_against_another_observed_side_blocks_the_candidate(
    resequenced: Path,
) -> None:
    """Part (ii), as part (i): the refit and the from-arm must be scored against the same observed side
    (hourly flows for C3 and the peaks, segment speeds for C4); the block's build time is not part of it."""
    plan = m.RESEQ["b1b2"]
    path = resequenced / m.artifact(plan["label"])
    art = json.loads(path.read_text())
    art["observed"]["wall_s"] = 99.0  # the build time alone: not another observed side
    path.write_text(json.dumps(art))
    p2 = _evaluate(resequenced, "b1b2")["part_ii"]
    assert p2["problems"] == [] and p2["candidate"] is True
    art["observed"]["hourly_flows_veh_h_recommended"][0] = [5000.0] * 3
    art["observed"]["segment_speeds_ms"][0] = [10.1, 20.0]
    path.write_text(json.dumps(art))
    p2 = _evaluate(resequenced, "b1b2")["part_ii"]
    assert p2["candidate"] is None and p2["c1_c5_hold"] is True
    assert p2["problems"] == [
        "the refit and the from-arm were scored against different observed sides: "
        "hourly_flows_veh_h_recommended, segment_speeds_ms"
    ]


def test_the_quoted_figures_trace_the_collapsed_seed(repo: Path) -> None:
    """Part (i) reports the figures the write-up quotes: the paired per-replicate 5-min RMSPE with and
    without the lowest-realisation seed of B1 + B2, that seed against the others, and the estimators."""
    q = _evaluate(repo)["part_i"]["reported"]["quoted_figures"]
    assert q["rmspe_5min_per_replicate_paired"]["computed"] is False  # not recorded per seed
    for label, realised, per_rep in (
        (m.ARM_LABEL, [0.95, 0.66, 0.97], [0.3, 0.6, 0.3]),
        (m.REF_LABEL, [0.9, 0.9, 0.9], [0.2, 0.2, 0.2]),
    ):
        path = repo / m.artifact(label)
        art = json.loads(path.read_text())
        art["simulated"]["demand_realized_fraction"] = realised
        art["rmspe"]["per_replicate_vs_observed"] = per_rep
        path.write_text(json.dumps(art))
    p1 = _evaluate(repo)["part_i"]
    q = p1["reported"]["quoted_figures"]
    c = q["collapsed_seed"]
    assert (c["seed"], c["realised_b1b2"], c["realised_b2"]) == (22, 0.66, 0.9)
    assert c["also_largest_paired_rmspe_5min"] is True
    assert q["other_seeds"]["n"] == 2
    assert q["other_seeds"]["realised_b1b2"][0] == pytest.approx(0.96)
    assert q["other_seeds"]["realised_paired"][0] == pytest.approx(0.06)
    rm5 = q["rmspe_5min_per_replicate_paired"]
    assert rm5["all_seeds"][0] == pytest.approx(0.2)
    assert rm5["without_collapsed_seed"] == pytest.approx([0.1, 0.1, 0.1])
    rm15 = q["rmspe_15min_per_replicate_paired"]["all_seeds"]
    assert rm15 == p1["criteria"]["A5"]["per_replicate_paired_b1_minus_ref"]
    zone = q["zone_speed_paired_kmh"]["all_seeds"]
    assert zone == p1["criteria"]["A2"]["zone"]["paired_zone_speed_b1_minus_ref_kmh"]
    peaks = q["peak_sections_paired_veh_h"]["all_seeds"]
    assert peaks == {
        k: v["paired_b1_minus_ref"] for k, v in p1["reported"]["peak_sections"].items()
    }
    assert "replicate-mean field" in p1["reported"]["estimators"]["gated"]


def test_code_records_the_source_commit_and_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``code`` is the source commit when the environment or ``.source_commit`` names it; ``vm_snapshot``
    is the checkout's HEAD (on the VM, the snapshot vm_setup.sh commits)."""
    import subprocess

    # a checkout of its own, so the test does not depend on the suite running inside a git
    # work tree (the staged-tree gate exports the repository without .git)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(
            cmd, cwd=checkout, check=True, env={**os.environ, **env}, capture_output=True
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.setattr(m, "REPO", checkout)
    monkeypatch.delenv(m.SOURCE_COMMIT_ENV, raising=False)
    assert m._code() == {"code": head, "vm_snapshot": head}  # a local checkout: its own HEAD
    monkeypatch.setenv(m.SOURCE_COMMIT_ENV, "a" * 40)
    assert m._code() == {"code": "a" * 40, "vm_snapshot": head}
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setattr(m, "REPO", bare)  # not a git checkout: no HEAD
    (bare / m.SOURCE_COMMIT_FILE).write_text("b" * 40 + "\n")
    assert m._code() == {"code": "a" * 40, "vm_snapshot": None}  # the environment first
    monkeypatch.delenv(m.SOURCE_COMMIT_ENV)
    assert m._code() == {"code": "b" * 40, "vm_snapshot": None}


def test_a_v3_record_of_the_same_file_counts_as_reproduced_under_policy_v4() -> None:
    """The re-run quotes today's hash, the committed p13 battery its policy-3 hash
    (docs/CONTRACTS.md §2, policy v4); ``reproduces()`` must not call that a
    changed configuration. Another file, another hash or a missing scenario
    stays unreproduced."""
    import yaml

    from flowstate_core.config import ScenarioConfig, config_hash

    m = _load()
    stem = "i24_replica_flow_rc_speedcal_dc_refit"
    doc = yaml.safe_load((REPO_ROOT / "scenarios" / f"{stem}.yaml").read_text())
    here = {"scenario": stem, "config_hash": config_hash(ScenarioConfig.model_validate(doc))}
    committed = {"scenario": stem, "config_hash": config_hash_v3(doc)}
    assert here["config_hash"] != committed["config_hash"]
    assert m._same_file_across_policies(here, committed)
    assert not m._same_file_across_policies(here, {"scenario": stem, "config_hash": "000000000000"})
    assert not m._same_file_across_policies(
        here, {"scenario": "i24_replica_flow_speedcal", "config_hash": committed["config_hash"]}
    )
    assert not m._same_file_across_policies(
        {"scenario": "no_such_scenario", "config_hash": "x"},
        {"scenario": "no_such_scenario", "config_hash": "y"},
    )
