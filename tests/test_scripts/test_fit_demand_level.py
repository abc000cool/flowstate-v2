"""scripts/fit_demand_level.py (B5, docs/PRE_FRISCO_PROGRAM.md; FRISCO_PROTOCOL Amendment 6), offline.

No simulation (the laptop rule): the I-24 fitter's ``_run_jobs`` and the I-94 path's ``i94_simulate`` /
``i94_score`` are replaced by synthetic runs on the committed inputs. Pinned here:

* the old fitter's defaults are byte-identical: its two committed fits (p4's ``_dc_refit``, p14's
  re-sequence) re-derive byte for byte from their own recorded runs, and their scenarios too, but for
  the config-hash line under hash policy 4 (the old hash names the same document under policy 3);
* the I-24 procedure: five seeds per scale, coarse 0.6-1.1, the refine round ±2 x 0.025 around the
  coarse constrained choice, the from-arm's reproduction run, the rule's choice, the scenario written
  by the old fitter's ``scaled_config``, and the readout's checks passing on the artifact;
* ``constraint_unmet`` stops after the coarse round (exit 3, no scenario); refused inputs run nothing;
* the I-94 procedure: one round 0.95-1.05 inside the count error, the pooled objective, f = 1 the
  from-arm's document exactly, the scenario and the readout's checks.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

from flowstate_core.config import config_hash
from microsim.scenarios import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
READOUT = (
    REPO_ROOT / "artifacts" / "pre_frisco_2026-10-07" / "harness_b5" / "demand_level_readout.py"
)


def _load(name: str, path: Path) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fdl = _load("fit_demand_level", SCRIPTS / "fit_demand_level.py")
fit24 = fdl.fit24
readout = _load("b5_demand_level_readout", READOUT)


def _strict(path: Path) -> dict[str, Any]:
    """Parse JSON refusing NaN / Infinity (the artifacts are strict JSON)."""

    def bad(x: str) -> None:
        raise ValueError(f"non-strict JSON constant {x}")

    return json.loads(path.read_text(), parse_constant=bad)


# --------------------------------------------------------------------------- the old fitter, byte for byte

COMMITTED_FITS = [
    # (fit artifact, the base it scaled, the scenario it wrote and that scenario's name): p14 and p4
    (
        "artifacts/demand_scale_i24_flow_rc.json",
        "scenarios/i24_replica_flow_rc_corrected_dc.yaml",
        "scenarios/i24_replica_flow_rc_speedcal_dc_refit2.yaml",
        "i24_replica_flow_rc_speedcal_dc_refit2",
    ),
    (
        "artifacts/demand_scale_i24_flow_dc.json",
        "scenarios/i24_replica_flow_corrected_dc.yaml",
        "scenarios/i24_replica_flow_speedcal_dc_refit.yaml",
        "i24_replica_flow_speedcal_dc_refit",
    ),
]


@pytest.mark.parametrize(("fit_path", "base", "scenario", "name"), COMMITTED_FITS)
def test_the_old_fitters_defaults_rederive_its_committed_fits_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fit_path: str,
    base: str,
    scenario: str,
    name: str,
) -> None:
    """With fit_demand_level imported, the old fitter run as stages p4 / p14 ran it (no --min-inserted,
    no --objective) on its own recorded runs writes the committed artifact byte for byte; its scenario
    is the committed one but for the config-hash line, which policy 4 moved by its version alone."""
    art = json.loads((REPO_ROOT / fit_path).read_text())
    rows = {round(float(r["scale"]), 3): r for r in art["grid"]}
    rounds: list[list[float]] = []

    def recorded(jobs: list[tuple[Any, ...]], procs: int) -> list[dict[str, Any]]:
        rounds.append([j[0] for j in jobs])
        assert all(j[1] == art["seed"] and j[2] == art["fleet_artifact"] for j in jobs)
        return [json.loads(json.dumps(rows[round(float(j[0]), 3)])) for j in jobs]

    monkeypatch.setattr(fit24, "_run_jobs", recorded)
    monkeypatch.setattr(fit24, "_versions", lambda: art["versions"])
    out = tmp_path / "fit.json"
    fit24.main(
        [
            "--base",
            "corrected",
            "--base-yaml",
            str(REPO_ROOT / base),
            "--fleet-artifact",
            art["fleet_artifact"],
            "--procs",
            "14",
            "--out",
            str(out),
        ]
    )
    assert rounds[0] == list(fit24.COARSE["corrected"]) and len(rounds) == 2
    assert out.read_bytes() == (REPO_ROOT / fit_path).read_bytes()
    scn = tmp_path / "scn.yaml"
    fit24.main(
        ["--from-artifact", str(REPO_ROOT / fit_path), "--scenario-out", str(scn), "--name", name]
    )
    want = (REPO_ROOT / scenario).read_text()
    old = re.search(r"config hash ([0-9a-f]{12}); seeded=False", want)
    assert old is not None
    doc = yaml.safe_load(want)
    # the committed header names this document (under policy 3 or the current one)
    assert fdl.names_document(old.group(1), doc) is not None
    now = config_hash(load_scenario(REPO_ROOT / scenario))
    assert scn.read_text() == want.replace(f"config hash {old.group(1)}", f"config hash {now}")


def test_importing_the_new_script_changes_nothing_in_the_old_one() -> None:
    assert fit24.COARSE == {
        "tracked": (1.0, 1.15, 1.3, 1.45, 1.6, 1.75, 1.9),
        "corrected": (0.6, 0.7, 0.8, 0.9, 1.0, 1.1),
    }
    assert (fit24.REFINE_STEP, fit24.REFINE_HALF_WIDTH) == (0.025, 3)
    assert fit24.TRAIN_WINDOWS == range(0, 12) and fit24.TEST_WINDOWS == range(12, 24)
    # B5's coarse round is the fitter's own
    assert tuple(fit24.COARSE["corrected"]) == fdl.I24_COARSE


def test_hash_policy_reading() -> None:
    doc = yaml.safe_load((REPO_ROOT / fdl.I24_FROM).read_text())
    assert fdl.names_document(fdl.I24_FROM_HASH, doc) in {"current", "v3"}
    assert fdl.names_document(fdl._hash_of(doc), doc) == "current"
    assert fdl.names_document("000000000000", doc) is None
    assert fdl.names_document(None, doc) is None


# --------------------------------------------------------------------------- I-24 on synthetic runs

FROM = yaml.safe_load((REPO_ROOT / fdl.I24_FROM).read_text())
BATTERY = json.loads((REPO_ROOT / fdl.I24_FROM_BATTERY).read_text())
OBS = np.asarray(json.loads(fit24.OBSERVED.read_text())[fit24.GEH_OBSERVED_KEY], float)
NOISE = (-0.002, -0.001, 0.0, 0.001, 0.002)  # per fit seed; mean zero


def _ins(scale: float) -> float:
    """Inserted fraction: whole below 0.8, then 0.4 per unit of scale held back (0.925 inserts 0.95)."""
    return 1.0 if scale <= 0.8 + 1e-9 else round(1.0 - 0.4 * (scale - 0.8), 4)


def _fake_jobs(state: dict[str, Any]) -> Any:
    """``_run_jobs`` stand-in: the observed flows x scale / 0.875, insertion by ``_ins``; the
    from-arm's reproduction run returns the battery's first replicate (``state['repro_bump']`` alters it)."""
    seeds = BATTERY["seeds"][:5]
    hashes: dict[tuple[float, str], str] = {}

    def run(jobs: list[tuple[Any, ...]], procs: int) -> list[dict[str, Any]]:
        state["rounds"].append(list(jobs))
        out = []
        for scale, seed, pop, base_kind, base_yaml, name in jobs:
            key = (round(scale, 6), name)
            if key not in hashes:
                hashes[key] = fdl._hash_of(
                    fit24.scaled_config(scale, pop, base_kind, Path(base_yaml), name)
                )
            if name == FROM["name"]:
                sim = BATTERY["simulated"]
                counts = json.loads(json.dumps(sim["counts_per_replicate"][0]))
                counts[0][0] += state.get("repro_bump", 0)
                seg = np.round(np.asarray(sim["segment_speeds_ms_per_replicate"][0], float), 3)
                ins = sim["demand_realized_fraction"][0]
            else:
                g = scale / 0.875
                counts = [
                    [round(q / 12.0 * g) if math.isfinite(q) else 0 for q in row] for row in OBS
                ]
                seg = np.full((24, 10), 20.0)
                ins = state.get("cap", 1.0) * _ins(scale) + NOISE[seeds.index(seed)]
                ins = round(min(1.0, ins), 4)
            out.append(
                {
                    "scale": scale,
                    "seed": seed,
                    "config_hash": hashes[key],
                    "inserted_fraction": ins,
                    "rmspe_train": 0.3,
                    "rmspe_test": 0.31,
                    "rmspe_all": 0.305,
                    "segment_speeds_ms": seg.tolist(),
                    "counts_per_window": counts,
                    "wall_s": 1.0,
                }
            )
        return out

    return run


@pytest.fixture
def i24_runs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"rounds": []}
    monkeypatch.setattr(fit24, "_run_jobs", _fake_jobs(state))
    return state


def test_i24_fit_runs_the_planned_procedure_and_the_rules_choice(
    tmp_path: Path, i24_runs: dict[str, Any]
) -> None:
    out, scn = tmp_path / "fit.json", tmp_path / "b5.yaml"
    rc = fdl.main(
        [
            "--corridor",
            "i24",
            "--procs",
            "8",
            "--out",
            str(out),
            "--write-scenario",
            "--scenario-out",
            str(scn),
        ]
    )
    assert rc == 0
    seeds = BATTERY["seeds"][:5]
    coarse, refine = i24_runs["rounds"]
    # the coarse round: every scale on the five fit seeds, under the output name, then the reproduction run
    assert [(j[0], j[1]) for j in coarse[:-1]] == [(s, x) for s in fdl.I24_COARSE for x in seeds]
    assert all(
        j[5] == fdl.I24_NAME and j[2] == FROM["fleet"]["idm_calibration"] for j in coarse[:-1]
    )
    assert coarse[-1][0] == 0.925 and coarse[-1][1] == seeds[0] and coarse[-1][5] == FROM["name"]
    # 0.9 is the coarse round's constrained choice (1.0 and 1.1 hold demand back); refine around it
    assert [(j[0], j[1]) for j in refine] == [
        (s, x) for s in (0.85, 0.875, 0.925, 0.95) for x in seeds
    ]
    fit = _strict(out)
    assert fit["schema"] == fdl.SCHEMA and fit["corridor"] == "i24" and "Amendment 6" in fit["spec"]
    assert fit["seeds"] == seeds and fit["reference_scale"] == 0.925
    assert fit["min_inserted"] == pytest.approx(
        np.mean(BATTERY["simulated"]["demand_realized_fraction"]) - 0.01
    )
    assert fit["grid"]["coarse_choice"] == 0.9 and fit["grid"]["refine"] == [
        0.85,
        0.875,
        0.925,
        0.95,
    ]
    assert fit["grid"]["n_runs"] == 51
    sel = fit["selection"]
    # 0.875 fits every bin, as 0.9 does: the smaller mean GEH decides; 0.925 and 0.95 fail the floor
    assert sel["chosen_scale"] == 0.875 and sel["decided_by"] == "mean_geh"
    assert sel["qualifying_scales"] == [0.6, 0.7, 0.8, 0.85, 0.875, 0.9]
    assert fit["coarse_selection"]["chosen_scale"] == 0.9
    assert fit["reproduction"]["exact"] is True
    assert len(fit["per_scale"]) == 10 and all(len(r["per_seed"]) == 5 for r in fit["per_scale"])
    assert all(
        "geh_test" in p and "counts_per_window" in p
        for r in fit["per_scale"]
        for p in r["per_seed"]
    )
    # the scenario is the old fitter's scaled_config at the chosen scale, with its provenance
    doc = fit24.scaled_config(
        0.875, FROM["fleet"]["idm_calibration"], "corrected", REPO_ROOT / fdl.I24_BASE, fdl.I24_NAME
    )
    h = fdl._hash_of(doc)
    text = scn.read_text()
    assert yaml.safe_load(text) == doc
    assert text.splitlines()[0].startswith(f"# {fdl.I24_NAME} — B5's demand level")
    assert f"config hash {h}; seeded=False." in text and "Selection: s = 0.875" in text
    assert fit["chosen"]["config_hash"] == h == fit["scenario_out"]["config_hash"]
    # the readout re-derives the fit from its own records and finds it run as B5 fixed it
    checks, rederived = readout.fit_checks_i24(fit, BATTERY)
    assert all(checks.values()), checks
    assert (
        rederived["final"]["chosen_scale"] == 0.875 and rederived["coarse"]["chosen_scale"] == 0.9
    )


def test_a_reproduction_that_differs_is_recorded_and_the_readout_sees_it(
    tmp_path: Path, i24_runs: dict[str, Any]
) -> None:
    i24_runs["repro_bump"] = 1
    out = tmp_path / "fit.json"
    assert fdl.main(["--corridor", "i24", "--out", str(out)]) == 0
    fit = _strict(out)
    assert fit["reproduction"]["exact"] is False
    assert fit["reproduction"]["differs"] == ["counts_per_window"]
    checks, _ = readout.fit_checks_i24(fit, BATTERY)
    assert [k for k, ok in checks.items() if not ok] == ["reproduction_exact"]


def test_constraint_unmet_stops_after_the_coarse_round(
    tmp_path: Path, i24_runs: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    i24_runs["cap"] = 0.9  # no scale inserts 0.9566
    out, scn = tmp_path / "fit.json", tmp_path / "b5.yaml"
    rc = fdl.main(
        ["--corridor", "i24", "--out", str(out), "--write-scenario", "--scenario-out", str(scn)]
    )
    assert rc == fdl.EXIT_CONSTRAINT_UNMET == 3
    assert len(i24_runs["rounds"]) == 1  # no refine round
    assert not scn.exists()
    fit = _strict(out)
    assert fit["constraint_unmet"] is True and fit["chosen"] is None and fit["grid"]["refine"] == []
    assert fit["selection"]["reason"].startswith("CONSTRAINT UNMET")
    assert "CONSTRAINT UNMET" in capsys.readouterr().err


def test_inputs_not_the_plans_are_refused_before_any_run(
    tmp_path: Path, i24_runs: dict[str, Any]
) -> None:
    assert (
        fdl.main(["--corridor", "i24", "--out", str(tmp_path / "a.json"), "--from-hash", "0" * 12])
        == 2
    )
    assert (
        fdl.main(["--corridor", "i24", "--out", str(tmp_path / "a.json"), "--carried-scale", "0.9"])
        == 2
    )
    other = json.loads(json.dumps(BATTERY))
    other["seeds"] = other["seeds"][::-1]
    (tmp_path / "battery.json").write_text(json.dumps(other))
    assert (
        fdl.main(
            [
                "--corridor",
                "i24",
                "--out",
                str(tmp_path / "a.json"),
                "--from-battery",
                str(tmp_path / "battery.json"),
            ]
        )
        == 2
    )
    other = json.loads(json.dumps(BATTERY))
    other["observed"][fit24.GEH_OBSERVED_KEY][0][0] += 1.0
    (tmp_path / "battery.json").write_text(json.dumps(other))
    assert (
        fdl.main(
            [
                "--corridor",
                "i24",
                "--out",
                str(tmp_path / "a.json"),
                "--from-battery",
                str(tmp_path / "battery.json"),
            ]
        )
        == 2
    )
    assert i24_runs["rounds"] == [] and not (tmp_path / "a.json").exists()


def test_another_from_arm_is_read_without_the_default_arms_hashes() -> None:
    """Stage p15 may run on the arm C7b selects: the p4 `_dc_refit` family stands in here. The plan's
    expected hashes guard the default files only; the recipe and battery checks guard every arm."""
    args = fdl.parse_args(
        [
            "--corridor",
            "i24",
            "--from-scenario",
            "scenarios/i24_replica_flow_speedcal_dc_refit.yaml",
            "--base-yaml",
            "scenarios/i24_replica_flow_corrected_dc.yaml",
            "--from-battery",
            "artifacts/i24_validation_dc_refit.json",
        ]
    )
    s = fdl.i24_setup(args)
    other = json.loads((REPO_ROOT / "artifacts/i24_validation_dc_refit.json").read_text())
    assert s.out_name == "i24_replica_flow_speedcal_dc_refit_b5" and s.carried == 0.925
    assert s.floor == pytest.approx(np.mean(other["simulated"]["demand_realized_fraction"]) - 0.01)
    # the default files keep the plan's hashes even when named explicitly
    args = fdl.parse_args(
        ["--corridor", "i24", "--from-scenario", fdl.I24_FROM, "--base-yaml", fdl.I24_BASE]
    )
    assert fdl.i24_setup(args).out_name == fdl.I24_NAME
    with pytest.raises(fdl.Refused, match="expected"):
        fdl.i24_setup(fdl.parse_args(["--corridor", "i24", "--base-hash", "0" * 12]))
    # a from-arm that is not the base at the level: refused
    with pytest.raises(fdl.Refused, match="not the from-arm"):
        fdl.i24_setup(
            fdl.parse_args(
                ["--corridor", "i24", "--base-yaml", "scenarios/i24_replica_flow_corrected_dc.yaml"]
            )
        )


# --------------------------------------------------------------------------- I-94 on synthetic runs

I94_FROM = yaml.safe_load((REPO_ROOT / fdl.I94_FROM).read_text())
I94_BATTERY = json.loads((REPO_ROOT / f"artifacts/validation_{I94_FROM['name']}.json").read_text())


def _ins94(f: float) -> float:
    return 0.985 if f <= 1.0 + 1e-9 else round(0.985 - 0.4 * (f - 1.0), 4)  # 1.05 inserts 0.965


def test_the_i94_document_scales_demand_only_and_is_the_from_arm_at_one() -> None:
    same = fdl.i94_document(I94_FROM, 1.0, I94_FROM["name"])
    assert same == I94_FROM and fdl._hash_of(same) == config_hash(
        load_scenario(REPO_ROOT / fdl.I94_FROM)
    )
    doc = fdl.i94_document(I94_FROM, 0.95, "x")
    assert doc["name"] == "x"
    assert [q for _, q in doc["network"]["inflow"]] == [
        q * 0.95 for _, q in I94_FROM["network"]["inflow"]
    ]
    for a, b in zip(doc["network"]["ramps"], I94_FROM["network"]["ramps"], strict=True):
        if b["kind"] == "on" and b["inflow"]:
            assert [q for _, q in a["inflow"]] == [q * 0.95 for _, q in b["inflow"]]
        assert a["exit_fraction"] == b["exit_fraction"] and a.get("weave") == b.get("weave")
    for key in ("fleet", "sim", "seed", "replicates"):
        assert doc[key] == I94_FROM[key]
    assert doc["network"]["boundary"] == I94_FROM["network"]["boundary"]


def install_i94_fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``i94_simulate`` records the pairs; ``i94_score`` returns synthetic runs: link flows = the battery's
    first replicate's observed hours x f / 1.025, insertion by ``_ins94``; the from-arm run is that replicate."""
    state: dict[str, Any] = {"pairs": None}
    ps = I94_BATTERY["per_seed"][0]
    obs = [float(h["obs_veh_h"]) for h in ps["link_hours"]]

    def simulate(pairs: list[Any], runs_root: Path, procs: int) -> None:
        state["pairs"] = list(pairs)
        state["procs"] = procs

    def score(dirs: list[Path], args: Any, setup: Any) -> list[Any]:
        seeds = setup.seeds
        out = []
        for d in dirs:
            label, seed = d.parent.parent.name, int(d.name)
            if label == "from_arm":
                ins = ps["insertion"]
                out.append(
                    fdl.I94Run(
                        label,
                        seed,
                        d,
                        setup.from_hash,
                        ins["planned"],
                        ins["departed"],
                        ins["arrived"],
                        ins["departed_fraction"],
                        ps["n_collisions"],
                        False,
                        [],
                        [],
                        ps["link_hours"],
                        ps["rmspe"],
                        0.4,
                        6.0,
                    )
                )
                continue
            f = next(x for x in setup.grid if fdl.scale_label(x) == label)
            pairs = [(q * f / 1.025, q) for q in obs]
            fr = round(_ins94(f) + NOISE[seeds.index(seed)] / 2, 4)
            out.append(
                fdl.I94Run(
                    label,
                    seed,
                    d,
                    d.parent.name,
                    32000,
                    round(32000 * fr),
                    None,
                    fr,
                    0,
                    False,
                    pairs,
                    pairs,
                    [],
                    0.5,
                    0.4,
                    6.0,
                )
            )
        return out

    monkeypatch.setattr(fdl, "i94_simulate", simulate)
    monkeypatch.setattr(fdl, "i94_score", score)
    return state


@pytest.fixture
def i94_runs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return install_i94_fakes(monkeypatch)


def test_i94_fit_runs_one_round_inside_the_count_error_and_writes_the_arm(
    tmp_path: Path, i94_runs: dict[str, Any]
) -> None:
    out, scn = tmp_path / "fit94.json", tmp_path / "arm_b5.yaml"
    rc = fdl.main(
        [
            "--corridor",
            "i94",
            "--procs",
            "10",
            "--out",
            str(out),
            "--runs-root",
            str(tmp_path / "runs"),
            "--write-scenario",
            "--scenario-out",
            str(scn),
        ]
    )
    assert rc == 0
    seeds = I94_BATTERY["seeds"][:5]
    pairs = i94_runs["pairs"]
    assert [(p.label, p.seed) for p in pairs[:-1]] == [
        (fdl.scale_label(f), s) for f in fdl.I94_GRID for s in seeds
    ]
    assert (pairs[-1].label, pairs[-1].seed) == ("from_arm", seeds[0]) and i94_runs["procs"] == 10
    assert pairs[-1].scenario == REPO_ROOT / fdl.I94_FROM
    # the per-factor scenarios the runs were made from
    s1 = yaml.safe_load((tmp_path / "runs" / "scenarios" / "s1.000.yaml").read_text())
    assert s1 == fdl.i94_document(I94_FROM, 1.0, f"{I94_FROM['name']}_b5")
    fit = _strict(out)
    assert fit["corridor"] == "i94" and fit["reference_scale"] == 1.0 and fit["seeds"] == seeds
    assert fit["min_inserted"] == pytest.approx(
        I94_BATTERY["insertion"]["mean_departed_fraction"] - 0.01
    )
    assert fit["grid"]["scales"] == list(fdl.I94_GRID) and fit["grid"]["count_error"] == 0.05
    assert fit["objective"]["estimator"] == "pooled"
    sel = fit["selection"]
    assert sel["chosen_scale"] == 1.025 and sel["qualifying_scales"] == [0.95, 0.975, 1.0, 1.025]
    assert fit["reproduction"]["exact"] is True
    name = f"{I94_FROM['name']}_b5"
    doc = fdl.i94_document(I94_FROM, 1.025, name)
    assert yaml.safe_load(scn.read_text()) == doc
    assert Path(fit["scenario_out"]["path"]).resolve() == scn.resolve()
    assert (fit["scenario_out"]["name"], fit["scenario_out"]["config_hash"]) == (
        name,
        fdl._hash_of(doc),
    )
    checks, rederived = readout.fit_checks_i94(fit, I94_BATTERY)
    assert all(checks.values()), checks
    assert rederived["final"]["chosen_scale"] == 1.025


def test_i94_inputs_not_the_plans_are_refused(tmp_path: Path, i94_runs: dict[str, Any]) -> None:
    other = json.loads(json.dumps(I94_BATTERY))
    other["observations"]["path"] = "artifacts/p1_rehearsal_2026-10-04/observations_validation.json"
    (tmp_path / "b.json").write_text(json.dumps(other))
    args = [
        "--corridor",
        "i94",
        "--out",
        str(tmp_path / "f.json"),
        "--runs-root",
        str(tmp_path / "runs"),
    ]
    assert fdl.main([*args, "--from-battery", str(tmp_path / "b.json")]) == 2
    dq = json.loads((REPO_ROOT / fdl.I94_DATA_QUALITY).read_text())
    dq["parameters"]["count_error"] = 0.02
    (tmp_path / "dq.json").write_text(json.dumps(dq))
    assert fdl.main([*args, "--data-quality", str(tmp_path / "dq.json")]) == 2
    assert fdl.main([*args, "--from-hash", "0" * 12]) == 2
    assert i94_runs["pairs"] is None and not (tmp_path / "f.json").exists()


def test_the_fit_artifacts_name_their_source() -> None:
    """The fitter's own provenance helpers: a committed file's sha256 and the code commit."""
    p = REPO_ROOT / fdl.I24_FROM_BATTERY
    assert fdl._sha(p) == hashlib.sha256(p.read_bytes()).hexdigest()
    code = fdl.source_commit()
    assert set(code) == {"code", "checkout_head"}
