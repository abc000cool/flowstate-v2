"""scripts/i24_fit_demand_scale.py: the opt-in insertion constraint, the GEH objective
and the re-analysis of saved fits.

No simulation here (the laptop rule): the fit's grid rounds are replaced by
synthetic rows (``_run_jobs``), and the re-analysis reads JSON only. The
default invocation must write exactly the historical artifact; the committed
refits' re-analysis must reproduce the numbers quoted in
docs/FRISCO_PROTOCOL.md (Result of Amendment 2, the insertion re-analysis).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from flowstate_core.config import ScenarioConfig
from flowstate_core.rng import spawn_seeds

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
REFITS = {
    "dc": REPO_ROOT / "artifacts" / "demand_scale_i24_flow_dc.json",
    "dck025": REPO_ROOT / "artifacts" / "demand_scale_i24_flow_dck025.json",
    "dck05": REPO_ROOT / "artifacts" / "demand_scale_i24_flow_dck05.json",
    "flow": REPO_ROOT / "artifacts" / "demand_scale_i24_flow.json",
}
LEGACY_OBJECTIVE = "segment-speed RMSPE, windows 0-11 (06:30-07:30 CST); windows 12-23 held out"


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


fit = _load("i24_fit_demand_scale")


def _row(scale: float, inserted: float, rmspe_train: float, **extra: Any) -> dict[str, Any]:
    r = {
        "scale": scale,
        "inserted_fraction": inserted,
        "rmspe_train": rmspe_train,
        "rmspe_test": round(rmspe_train + 0.05, 4),
    }
    r.update(extra)
    return r


# A grid whose speed optimum (0.925) inserts only 0.92 — the Amendment-2 pattern.
TABLE = [
    _row(0.8, 0.995, 0.40),
    _row(0.85, 0.982, 0.38),
    _row(0.9, 0.95, 0.33),
    _row(0.925, 0.92, 0.32),
]


# --- the rule on a synthetic per-scale table --------------------------------------------------


def test_default_rule_is_unchanged() -> None:
    chosen, sel = fit.choose(TABLE)
    assert chosen is fit._best(TABLE)
    assert chosen["scale"] == 0.925
    assert sel["min_inserted"] is None and sel["qualifying_scales"] is None
    assert sel["constraint_unmet"] is False
    assert [p["meets_min_inserted"] for p in sel["per_scale"]] == [None] * 4


def test_constrained_choice_is_the_best_qualifying_scale() -> None:
    chosen, sel = fit.choose(TABLE, min_inserted=0.98)
    assert chosen["scale"] == 0.85
    assert sel["qualifying_scales"] == [0.8, 0.85]
    assert sel["unconstrained_best_scale"] == 0.925
    assert sel["constraint_unmet"] is False
    assert "s = 0.925, inserts 0.9200" in sel["reason"]
    # every scale's inserted fraction and verdict is recorded
    assert [
        (p["scale"], p["inserted_fraction"], p["meets_min_inserted"]) for p in sel["per_scale"]
    ] == [
        (0.8, 0.995, True),
        (0.85, 0.982, True),
        (0.9, 0.95, False),
        (0.925, 0.92, False),
    ]
    # a threshold the unconstrained best meets changes nothing
    chosen, sel = fit.choose(TABLE, min_inserted=0.9)
    assert chosen["scale"] == 0.925 and "unconstrained best qualifies" in sel["reason"]


def test_unmet_constraint_takes_the_highest_insertion_and_flags_it() -> None:
    chosen, sel = fit.choose(TABLE, min_inserted=0.999)
    assert chosen["scale"] == 0.8
    assert sel["constraint_unmet"] is True
    assert sel["qualifying_scales"] == []
    assert sel["reason"].startswith("CONSTRAINT UNMET")
    # equal insertion: the better objective, then the smaller scale
    tied = [_row(0.8, 0.99, 0.40), _row(0.85, 0.99, 0.35), _row(0.9, 0.99, 0.35)]
    assert fit.choose(tied, min_inserted=0.999)[0]["scale"] == 0.85


def test_ties_go_to_the_smaller_scale_and_bad_arguments_fail() -> None:
    rows = [_row(0.9, 0.99, 0.3), _row(0.85, 0.99, 0.3)]
    assert fit.choose(rows, min_inserted=0.98)[0]["scale"] == 0.85
    with pytest.raises(ValueError, match="min_inserted"):
        fit.choose(rows, min_inserted=0.0)
    with pytest.raises(ValueError, match="min_inserted"):
        fit.choose(rows, min_inserted=1.5)
    with pytest.raises(ValueError, match="unknown objective"):
        fit.choose(rows, objective="travel_time")
    with pytest.raises(ValueError, match="no GEH reading"):
        fit.choose(rows, objective="geh")
    with pytest.raises(ValueError, match="no grid rows"):
        fit.choose([])


# --- the GEH reading and objective ------------------------------------------------------------


def test_geh_reading_hand_computed() -> None:
    # 2 sections x 2 windows; 5-min counts x 12 = hourly flows
    counts = [[100, 100], [100, 50]]  # 1,200 / 1,200 / 1,200 / 600 veh/h
    obs = np.array([[1200.0, 1000.0], [1200.0, 600.0]])
    g = fit.geh_reading(counts, obs, [0, 1])
    g_1000 = math.sqrt(2 * 200.0**2 / 2200.0)  # 6.03: over 5
    assert g["n_bins"] == 4 and g["n_under_5"] == 3 and g["under_5"] == 0.75
    assert g["mean"] == round(g_1000 / 4, 3)
    assert fit.geh_reading(counts, obs, [1]) == {
        "n_bins": 2,
        "n_under_5": 1,
        "under_5": 0.5,
        "mean": round(g_1000 / 2, 3),
    }
    obs_nan = obs.copy()
    obs_nan[0, 1] = np.nan
    assert fit.geh_reading(counts, obs_nan, [0, 1])["n_bins"] == 3
    with pytest.raises(ValueError, match="shape"):
        fit.geh_reading(counts, obs[:1], [0])


def test_geh_objective_counts_bins_then_mean_geh() -> None:
    obs = np.full((1, 24), 1200.0)

    def counts(fit_hour: float) -> list[list[float]]:  # same 5-min count in every fit window
        return [[fit_hour] * 12 + [100.0] * 12]

    rows = [
        _row(0.8, 0.99, 0.30, counts_per_window=counts(100.0)),  # GEH 0 everywhere
        _row(0.85, 0.99, 0.20, counts_per_window=counts(80.0)),  # 960 veh/h: GEH 7.3
        _row(0.9, 0.99, 0.25, counts_per_window=counts(99.0)),  # GEH 0.35: same bins, worse mean
    ]
    fit.add_geh(rows, obs)
    assert fit.choose(rows, objective="speed_rmspe")[0]["scale"] == 0.85
    chosen, sel = fit.choose(rows, objective="geh")
    assert chosen["scale"] == 0.8
    assert sel["objective"] == "geh" and "GEH 5" in sel["objective_definition"]
    assert sel["per_scale"][1]["geh_under_5_train"] == 0.0


# --- the fit's CLI: default output byte-for-byte as before; the selection block when asked ----


def _fake_rows(scales: list[float], inserted: dict[float, float]) -> list[dict[str, Any]]:
    out = []
    for s in scales:
        speed_err = round(0.3 + abs(s - 0.925), 4)  # speed optimum at 0.925
        out.append(
            {
                "scale": s,
                "seed": 7,
                "config_hash": f"h{round(s * 1000):04d}",
                "inserted_fraction": inserted.get(s, round(min(1.0, 1.75 - s), 4)),
                "rmspe_train": speed_err,
                "rmspe_test": round(speed_err + 0.01, 4),
                "rmspe_all": round(speed_err + 0.005, 4),
                "segment_speeds_ms": [[20.0] * 10] * 24,
                "counts_per_window": [[round(500 * s)] * 24 for _ in range(6)],
                "wall_s": 1.0,
            }
        )
    return out


FAKE_INSERTED = {0.8: 0.995, 0.825: 0.99, 0.85: 0.982, 0.875: 0.975, 0.9: 0.95}


@pytest.fixture
def fake_grid(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the simulations with synthetic rows; record each round's scales.

    ``state["cap"]`` (None) caps every row's inserted fraction.
    """
    state: dict[str, Any] = {"rounds": [], "cap": None}

    def run_jobs(jobs: list[tuple], procs: int) -> list[dict[str, Any]]:
        scales = [j[0] for j in jobs]
        state["rounds"].append(scales)
        rows = _fake_rows(scales, FAKE_INSERTED)
        if state["cap"] is not None:
            for r in rows:
                r["inserted_fraction"] = min(r["inserted_fraction"], state["cap"])
        return rows

    monkeypatch.setattr(fit, "_run_jobs", run_jobs)
    return state


def _expected_legacy(rows: list[dict[str, Any]], best: dict[str, Any]) -> dict[str, Any]:
    base = ScenarioConfig.model_validate(fit.scaled_config(1.0, fit.FLEET_ARTIFACT, "corrected"))
    return {
        "schema_version": 1,
        "versions": fit._versions(),
        "base": "corrected",
        "base_scenario": "scenarios/i24_replica_corrected.yaml",
        "fleet_artifact": fit.FLEET_ARTIFACT,
        "objective": LEGACY_OBJECTIVE,
        "seed": spawn_seeds(base.seed, base.replicates)[0],
        "grid": sorted(rows, key=lambda r: r["scale"]),
        "best": {
            k: best[k]
            for k in (
                "scale",
                "config_hash",
                "inserted_fraction",
                "rmspe_train",
                "rmspe_test",
                "rmspe_all",
            )
        },
    }


def test_default_fit_writes_the_historical_artifact(
    tmp_path: Path, fake_grid: dict[str, Any]
) -> None:
    out = tmp_path / "fit.json"
    fit.main(["--base", "corrected", "--out", str(out)])
    coarse = list(fit.COARSE["corrected"])
    rounds = fake_grid["rounds"]
    assert rounds[0] == coarse
    assert rounds[1] == fit.refine_scales(0.9)  # the unconstrained coarse optimum
    rows = _fake_rows(coarse, FAKE_INSERTED) + _fake_rows(rounds[1], FAKE_INSERTED)
    best = min(rows, key=lambda r: (r["rmspe_train"], r["scale"]))
    assert best["scale"] == 0.925
    expected = json.dumps(_expected_legacy(rows, best), indent=1, default=fit._json_default)
    assert out.read_text() == expected


def test_constrained_fit_records_the_rule_and_recentres_the_refine(
    tmp_path: Path, fake_grid: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    out, scn = tmp_path / "fit.json", tmp_path / "scn.yaml"
    fit.main(
        [
            "--base",
            "corrected",
            "--out",
            str(out),
            "--min-inserted",
            "0.98",
            "--write-scenario",
            "--scenario-out",
            str(scn),
            "--name",
            "i24_test_constrained",
        ]
    )
    assert fake_grid["rounds"][1] == fit.refine_scales(0.8)  # the constrained coarse choice
    art = json.loads(out.read_text())
    sel = art["selection"]
    assert art["objective"] == LEGACY_OBJECTIVE
    assert sel["min_inserted"] == 0.98 and sel["constraint_unmet"] is False
    assert sel["coarse_choice_scale"] == 0.8 and sel["refine_centre_scale"] == 0.8
    # best speed fit among 0.6, 0.7, 0.725, 0.75, 0.775, 0.8, 0.825, 0.85 (inserted >= 0.98)
    assert art["best"]["scale"] == 0.85 == sel["chosen_scale"]
    assert art["best"]["constraint_unmet"] is False and "geh" in art["best"]
    assert {p["scale"] for p in sel["per_scale"]} == {r["scale"] for r in art["grid"]}
    assert all("geh" in r for r in art["grid"])
    header = scn.read_text().split("\n")
    assert any(line.startswith("# Selection: best speed_rmspe among") for line in header)
    assert "selection: best speed_rmspe" in capsys.readouterr().out


def test_unmet_constraint_is_flagged_in_artifact_scenario_and_console(
    tmp_path: Path, fake_grid: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_grid["cap"] = 0.9  # no scale inserts more than 0.9
    out, scn = tmp_path / "fit.json", tmp_path / "scn.yaml"
    fit.main(
        [
            "--base",
            "corrected",
            "--out",
            str(out),
            "--min-inserted",
            "0.95",
            "--write-scenario",
            "--scenario-out",
            str(scn),
            "--name",
            "i24_test_unmet",
        ]
    )
    art = json.loads(out.read_text())
    sel = art["selection"]
    # highest inserted fraction (0.9: every scale up to 0.9), then the best speed fit
    assert sel["constraint_unmet"] is True and art["best"]["constraint_unmet"] is True
    assert sel["qualifying_scales"] == []
    assert art["best"]["scale"] == 0.9 == sel["chosen_scale"]
    assert sel["reason"].startswith("CONSTRAINT UNMET")
    assert "# Selection: CONSTRAINT UNMET" in scn.read_text()
    assert "WARNING: insertion constraint unmet" in capsys.readouterr().err


def test_geh_objective_selects_on_flows_end_to_end(
    tmp_path: Path, fake_grid: dict[str, Any]
) -> None:
    out, scn = tmp_path / "fit.json", tmp_path / "scn.yaml"
    fit.main(
        [
            "--base",
            "corrected",
            "--out",
            str(out),
            "--objective",
            "geh",
            "--coarse-only",
            "--write-scenario",
            "--scenario-out",
            str(scn),
            "--name",
            "i24_test_geh",
        ]
    )
    art = json.loads(out.read_text())
    assert art["objective"] == fit.OBJECTIVE_TEXT["geh"]
    assert art["selection"]["objective"] == "geh" and art["selection"]["min_inserted"] is None
    assert art["selection"]["refine_centre_scale"] is None
    assert art["best"]["scale"] == fit.choose(art["grid"], "geh")[0]["scale"]
    assert "# on observed link flows (GEH) over 06:30-07:30" in scn.read_text()


def test_fit_refuses_two_thresholds_and_a_stray_analysis_out(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        fit.main(["--min-inserted", "0.97", "--min-inserted", "0.98"])
    with pytest.raises(SystemExit):
        fit.main(["--analysis-out", str(tmp_path / "a.json")])
    with pytest.raises(SystemExit):
        fit.main(["--min-inserted", "1.2"])


# --- the re-analysis of saved fits ------------------------------------------------------------


def _synthetic_saved_fit() -> dict[str, Any]:
    """The structure of a committed refit: the corrected coarse grid, refined around 0.9."""
    scales = list(fit.COARSE["corrected"]) + fit.refine_scales(0.9)
    inserted = {0.8: 0.995, 0.825: 0.994, 0.85: 0.982, 0.875: 0.978, 0.9: 0.944, 0.925: 0.918}
    rows = sorted(_fake_rows(scales, inserted), key=lambda r: r["scale"])
    best = min(rows, key=lambda r: (r["rmspe_train"], r["scale"]))
    return {
        "schema_version": 1,
        "base": "corrected",
        "base_scenario": "scenarios/i24_replica_flow_corrected_dc.yaml",
        "fleet_artifact": "artifacts/idm_i24_capacity_amax_k1.0.json",
        "objective": LEGACY_OBJECTIVE,
        "seed": 7,
        "grid": rows,
        "best": {k: best[k] for k in ("scale", "config_hash", "inserted_fraction", "rmspe_train")},
    }


def test_analysis_of_a_synthetic_saved_fit() -> None:
    art = _synthetic_saved_fit()
    before = json.dumps(art)
    res = fit.analyze_fit(art, [None, 0.97, 0.98])
    assert json.dumps(art) == before  # the saved fit is not touched
    assert res["recorded_choice"]["scale"] == 0.925 and res["recorded_choice_reproduced"]
    assert "not a calibration" in res["label"]
    none, f97, f98 = res["by_threshold"]
    assert none["chosen"]["scale"] == 0.925 and not none["differs_from_recorded"]
    assert none["procedure"]["complete"] and none["procedure"]["chosen_scale"] == 0.925
    # >= 0.97: 0.875 (0.978) is the best qualifying speed fit
    assert f97["chosen"]["scale"] == 0.875 and f97["differs_from_recorded"]
    # >= 0.98: 0.85; the constrained coarse choice is 0.8, so its refine would also have run
    # 0.725-0.775, which the saved grid lacks
    assert f98["chosen"]["scale"] == 0.85
    assert f98["procedure"]["coarse_choice_scale"] == 0.8
    assert f98["procedure"]["refine_missing"] == [0.725, 0.75, 0.775]
    assert f98["procedure"]["complete"] is False
    assert f98["procedure"]["chosen_scale"] == 0.85


def test_analysis_cli_on_a_copy_of_a_committed_refit(tmp_path: Path) -> None:
    src = REFITS["dc"]
    if src.is_file():
        copy = tmp_path / src.name
        shutil.copyfile(src, copy)
    else:  # the committed structure, synthesised
        copy = tmp_path / "demand_scale_synthetic.json"
        copy.write_text(json.dumps(_synthetic_saved_fit()))
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    out = tmp_path / "analysis.json"
    fit.main(
        [
            "--analyze-artifact",
            str(copy),
            "--min-inserted",
            "0.97",
            "--min-inserted",
            "0.98",
            "--analysis-out",
            str(out),
        ]
    )
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == digest
    payload = json.loads(out.read_text())
    assert "no simulation" in payload["label"]
    (res,) = payload["analyses"]
    assert [b["min_inserted"] for b in res["by_threshold"]] == [0.97, 0.98]
    assert res["recorded_choice_reproduced"] is True
    for b in res["by_threshold"]:
        assert b["chosen"]["inserted_fraction"] >= b["min_inserted"]
        assert b["chosen"]["meets_min_inserted"] is True


needs_refits = pytest.mark.skipif(
    not all(p.is_file() for p in REFITS.values()), reason="committed demand-scale fits absent"
)


@needs_refits
def test_committed_refits_reanalysis_matches_the_protocol_note() -> None:
    """docs/FRISCO_PROTOCOL.md, Result of Amendment 2: the insertion re-analysis table."""
    obs = fit.observed_hourly_flows()
    expected = {  # recorded scale, scale at >= 0.97, scale at >= 0.98
        "dc": (0.925, 0.825, 0.825),
        "dck025": (0.9, 0.825, 0.8),
        "dck05": (0.9, 0.85, 0.825),
        "flow": (0.8, 0.8, 0.8),
    }
    for name, (recorded, at97, at98) in expected.items():
        art = json.loads(REFITS[name].read_text())
        res = fit.analyze_fit(art, [0.97, 0.98], obs_hourly=obs)
        assert res["recorded_choice_reproduced"], name
        assert res["recorded_choice"]["scale"] == recorded, name
        b97, b98 = res["by_threshold"]
        assert (b97["chosen"]["scale"], b98["chosen"]["scale"]) == (at97, at98), name
        assert not b97["constraint_unmet"] and not b98["constraint_unmet"]
        for b in (b97, b98):  # the two-round procedure lands on the same scale
            assert b["procedure"]["chosen_scale"] == b["chosen"]["scale"], name
        missing = b98["procedure"]["refine_missing"]
        assert missing == ([] if name == "flow" else [0.725, 0.75, 0.775]), name
    dc = fit.analyze_fit(json.loads(REFITS["dc"].read_text()), [0.98], obs_hourly=obs)
    c = dc["by_threshold"][0]["chosen"]
    assert (c["inserted_fraction"], c["rmspe_train"], c["rmspe_test"]) == (0.9949, 0.3832, 0.5097)
