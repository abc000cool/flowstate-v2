"""B5's readout (docs/PRE_FRISCO_PROGRAM.md B5; docs/FRISCO_PROTOCOL.md Amendment 6): C1-C5 against the from-arm.

usage (repository root; stages p15_i24_b5 and p17_i94_b5 of scripts/gcp/pipeline_i24.sh run it on the VM):
  demand_level_readout.py i24 --out artifacts/demand_level_i24.json [--fit F] [--label p15_b5] [--runs-root R]
  demand_level_readout.py i94 --out artifacts/demand_level_i94.json [--fit F]

Everything here was fixed in the plan before any run; nothing is re-thresholded. The output carries the
fit's per-scale, per-seed readings (the fit artifact ``artifacts/demand_level_fit_<corridor>.json`` of
scripts/fit_demand_level.py keeps the per-run tables), the checks that the fit ran as B5 fixed it, and the
refit battery read against the from-arm battery on the same 20 seeds:

* C1 0 collisions in every run, each replicate recording the counter;
* C2 the mean realised share at least the from-arm's minus 0.01 (FRISCO_PROTOCOL Amendment 2
  clarification), the literal >= reported beside;
* C3 the GEH < 5 share not below the from-arm's (I-24: the battery's hourly link-flow row; I-94: the
  baseline gate's C1 on the calibration days);
* C4 15-min speed RMSPE at most the from-arm's + 0.02 (I-24: the replicate-mean segment field in 15-min
  windows; I-94: the gate's C3 on the calibration days, station point speeds);
* C5 the wave verdict unchanged where the from-arm passes (I-24: the battery's wave row; I-94: gate C4);
* I-94 adds NL, protocol §9.5's no-lock rule as the plan's common rules state it: no seed's departed
  share below 0.8 of the battery median.

``candidate`` (a candidate for the arm's demand level, never a validation result; adoption is the
owner's) is True when every criterion holds and nothing is recorded under ``problems``, False when one
fails, None when a problem leaves the reading undetermined: the fit not run as B5 fixed it (its grid,
seeds, floor, objective or choice not re-derived from its own per-seed records by
``calibration.demand_level``), its reproduction of the from-arm's first replicate not exact, the battery
not the fit's configuration, other seeds or another observed side than the from-arm's, a missing file;
or the fit stopped on ``constraint_unmet`` (no battery ran; the readout says so and ``candidate`` is
None). If the fit returns the from-arm's own level the refit is the from-arm under another name: its
battery must then reproduce the from-arm's per-replicate readings, else a problem.

Reported, not gating: every gate row of both batteries; the breakdown reading per replicate (the
coordinator's pre-registration of 2026-10-07: below 0.9 of the planned demand while the battery's mean
is at least 0.95) beside ``no_locks`` / ``validation.locks``; I-24's 2-h flows at 2,200 / 3,200 / 5,400 m
with paired CIs and GEH, fronts, hard-braking steps (the refit's VM-side counts and the B2 re-run's of
stage p14), ramp flows against the corrected counts (``corridor_b1b2.ramp_reading``); I-94's gate C1 /
C2 / C3 / C4 / C6 on both day sets, insertion, collisions, locks and weave exits.

The C1-C5 block is adapted from ``artifacts/i24_discharge_2026-10-07/harness_b1b2/corridor_b1b2.py``
``part_ii`` (round p14; that file is imported, never modified), with harness/corridor_b1.py's estimators
(``ci``, ``paired``, ``rmspe``, ``agg_windows``) used as they are. Its ``battery`` reader is not: it
needs every replicate's ``edges.parquet`` for A2's zone speed, which B5 does not read and the committed
from-arm battery (stage p13) did not archive; ``battery_reading`` below copies its artifact-derived
fields line for line.

``evaluate`` exits 3 after writing the output when anything is recorded under ``problems`` or the fit
stopped (the stage logs the failure and carries on).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[3]
_B1B2 = REPO / "artifacts" / "i24_discharge_2026-10-07" / "harness_b1b2" / "corridor_b1b2.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cbb = _load("b5_corridor_b1b2", _B1B2)  # round p14's readout (imported, never modified)
cb = cbb.cb  # harness/corridor_b1.py: ci, paired, rmspe, agg_windows, WINDOWS_15MIN

from calibration.demand_level import (  # noqa: E402
    BREAKDOWN_FLOOR,
    BREAKDOWN_MIN_MEAN,
    I24_COARSE,
    I94_GRID,
    INSERTION_TOLERANCE,
    LOCK_MEDIAN_RATIO,
    ObjectiveReading,
    ScaleReading,
    breakdown_flags,
    departed_share_locks,
    pooled_objective,
    refine_scales,
    replicate_mean_objective,
    select_scale,
    within_count_error,
)

SCHEMA = "flowstate.demand_level_readout/1"
SPEC = (
    "docs/PRE_FRISCO_PROGRAM.md B5 (criteria: docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.5 C1-C5, fixed "
    "2026-10-07 before any run; the rule: docs/FRISCO_PROTOCOL.md Amendment 6)"
)
STATUS = (
    "PROPOSED, not adopted: a candidate is a proposal for the arm's demand level; adoption is the "
    "owner's call"
)
RMSPE_MARGIN = 0.02  # §8.4.5 C4
EXIT_BLOCKED = 3
FIT = {"i24": "artifacts/demand_level_fit_i24.json", "i94": "artifacts/demand_level_fit_i94.json"}
OUT = {"i24": "artifacts/demand_level_i24.json", "i94": "artifacts/demand_level_i94.json"}
I24_LABEL = "p15_b5"
I24_OBSERVED = "artifacts/i24_validation_observed.json"
I24_OBSERVED_KEY = "hourly_flows_veh_h_recommended"
I24_WINDOW_S = 300.0
I24_FIT_WINDOWS = range(0, 12)
I24_TEST_WINDOWS = range(12, 24)
I24_CARRIED_FIT = "artifacts/demand_scale_i24_flow_dc.json"
# the from-arm's braking counts and locks: B2 re-run on the p14 VM, which reproduced the committed p13
# battery exactly (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.6); reported beside, never gating
I24_FROM_BRAKING = "artifacts/i24_discharge_2026-10-07/p14_hard_braking_b2_ref.json"
I24_FROM_LOCKS = "artifacts/i24_locks_p14_b2_ref.json"
I24_SECTIONS_2H = ("2200", "3200", "5400")
I94_DATA_QUALITY = "artifacts/p1_rehearsal_2026-10-04/dq/data_quality.json"
NOT_VALIDATION = {
    "i24": "a demand refit on the recording it is scored on (fit hour 06:30-07:30 inside the scored "
    "window; one morning, no holdout day): calibration, never validation",
    "i94": "a demand refit on the calibration days the battery is scored on; the validation days are "
    "read by the gate, unchanged (protocol §3): the gate's verdict, not this readout, is the validation "
    "reading",
}


# --------------------------------------------------------------------------- helpers


def load(path: str) -> dict[str, Any] | None:
    p = REPO / path
    return json.loads(p.read_text()) if p.is_file() else None


def sha(path: str) -> str | None:
    p = REPO / path
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _key(s: float) -> float:
    return round(float(s), 6)


def _objective(raw: dict[str, Any]) -> ObjectiveReading:
    mean = raw.get("mean_geh")
    return ObjectiveReading(
        str(raw["estimator"]),
        int(raw["n_bins"]),
        int(raw["n_under_5"]),
        math.nan if mean is None else float(mean),
    )


def _same_objective(a: ObjectiveReading, b: ObjectiveReading) -> bool:
    return (
        a.estimator == b.estimator
        and a.n_bins == b.n_bins
        and a.n_under == b.n_under
        and math.isclose(a.mean_geh, b.mean_geh, rel_tol=1e-9, abs_tol=1e-12)
    )


def rederive(fit: dict[str, Any], recompute: Any) -> dict[str, Any]:
    """The fit's choice re-derived by ``calibration.demand_level`` from its own per-seed records.

    ``recompute(record)`` rebuilds a scale's objective from the record's per-seed tables; the recorded
    objective must equal it and the rule over the rebuilt readings must return the recorded choice.
    """
    seeds = [int(s) for s in fit["seeds"]]
    readings: list[ScaleReading] = []
    differs: list[str] = []
    for rec in fit["per_scale"]:
        per = rec["per_seed"]
        if [int(p["seed"]) for p in per] != seeds:
            differs.append(f"scale {rec['scale']}: per-seed rows not on the fit seeds")
            continue
        obj = recompute(rec)
        if not _same_objective(obj, _objective(rec["objective"])):
            differs.append(f"scale {rec['scale']}: the recorded objective is not its runs'")
        readings.append(
            ScaleReading(
                scale=float(rec["scale"]),
                seeds=tuple(seeds),
                inserted=tuple(float(p["inserted_fraction"]) for p in per),
                objective=obj,
            )
        )
    sel = select_scale(
        readings,
        min_inserted=float(fit["min_inserted"]),
        reference_scale=float(fit["reference_scale"]),
        seeds=seeds,
    )
    recorded = fit["selection"]
    if sel.chosen_scale != recorded.get("chosen_scale"):
        differs.append(
            f"the rule chooses {sel.chosen_scale}, the fit recorded {recorded.get('chosen_scale')}"
        )
    if sel.constraint_unmet != bool(recorded.get("constraint_unmet")):
        differs.append("constraint_unmet differs from the rule's")
    return {
        "chosen_scale": sel.chosen_scale,
        "constraint_unmet": sel.constraint_unmet,
        "order": list(sel.order),
        "decided_by": sel.decided_by,
        "differs": differs,
        "ok": not differs,
        "_readings": readings,
    }


def _strip_scale(rec: dict[str, Any]) -> dict[str, Any]:
    """A fit record without its per-run tables (they stay in the fit artifact)."""
    drop = {"counts_per_window", "segment_speeds_ms", "calibration_station_hours"}
    return {
        **{k: v for k, v in rec.items() if k != "per_seed"},
        "per_seed": [{k: v for k, v in p.items() if k not in drop} for p in rec["per_seed"]],
    }


def breakdown_block(realised: list[float], locked: list[Any] | None) -> dict[str, Any]:
    """The coordinator's breakdown reading per replicate, beside the lock record (reported)."""
    flags = breakdown_flags(realised)
    return {
        "rule": f"a replicate realising less than {BREAKDOWN_FLOOR} of its planned demand while the "
        f"battery's mean realised share is at least {BREAKDOWN_MIN_MEAN} (coordinator's "
        "pre-registration 2026-10-07; reported, gating only by a later amendment)",
        "applies": flags is not None,
        "per_replicate": flags,
        "n_breakdowns": None if flags is None else sum(flags),
        "locked_per_replicate": locked,
    }


def _verdicts(crit: dict[str, Any]) -> dict[str, Any]:
    return {k: v["verdict"] for k, v in crit.items()}


def _holds(crit: dict[str, Any]) -> bool:
    return all(v["verdict"] is not False for v in crit.values())


# --------------------------------------------------------------------------- I-24


def battery_reading(art: dict[str, Any]) -> dict[str, Any]:
    """What C1-C5 and the reported block read from one I-24 battery artifact.

    Copied from ``harness/corridor_b1.py`` ``battery`` (round p12, used by round p14's part (ii)), its
    artifact-derived fields only, line for line: no meta.json, edges.parquet or braking file is read
    here (``meta_check`` reads the meta.json files when they exist).
    """
    sim, obs = art["simulated"], art["observed"]
    n_win, win_s = int(obs["n_windows"]), float(obs["window_s"])
    sections = [float(x) for x in obs["sections_m"]]
    hours = n_win * win_s / 3600.0
    flows = {
        f"{s:.0f}": [float(sum(c[k])) / hours for c in sim["counts_per_replicate"]]
        for k, s in enumerate(sections)
    }
    crit = {r["name"]: r for r in art["criteria"]}
    obs_seg = np.asarray(obs["segment_speeds_ms"], float)
    sim_mean = np.asarray(sim["segment_speeds_ms_mean"], float)
    k15 = cb.WINDOWS_15MIN
    rm15 = cb.rmspe(cb.agg_windows(sim_mean, k15), cb.agg_windows(obs_seg, k15))
    rm15_rep = [
        cb.rmspe(cb.agg_windows(np.asarray(f, float), k15), cb.agg_windows(obs_seg, k15))
        for f in sim["segment_speeds_ms_per_replicate"]
    ]
    return {
        "scenario": art.get("scenario"),
        "config_hash": art["config_hash"],
        "seeds": [int(s) for s in sim["seeds"]],
        "flows_2h_veh_h": flows,
        "observed_2h_veh_h": {
            f"{s:.0f}": float(np.mean(obs["hourly_flows_veh_h_recommended"][k]))
            for k, s in enumerate(sections)
        },
        "collisions": [int(x) for x in sim.get("n_collisions_per_replicate") or []],
        "zero_collisions": art.get("zero_collisions"),
        "realised": [float(x) for x in sim["demand_realized_fraction"]],
        "fronts_standard": [int(w["n_backward"]) for w in sim["waves_per_replicate"]],
        "fronts_stripe": [int(w["n_backward"]) for w in sim["waves_stripe_per_replicate"]],
        "wave_row": {
            k: crit["wave_speed"].get(k) for k in ("value", "passed", "evaluated", "detail")
        },
        "geh_lt5_share": crit["link_flows_geh"]["value"],
        "rmspe_5min": art["rmspe"]["value"],
        "rmspe_15min": rm15,
        "rmspe_15min_per_replicate": rm15_rep,
        "no_locks_row": {
            k: crit.get("no_locks", {}).get(k) for k in ("value", "passed", "evaluated")
        },
        "locked_per_replicate": [
            None if r is None else r.get("locked")
            for r in (sim.get("locks_per_replicate") or [None] * len(sim["seeds"]))
        ],
    }


def meta_check(art: dict[str, Any], runs_root: Path) -> list[str]:
    """Each replicate's meta.json against the artifact (collisions, configuration); a missing file is a problem."""
    sim = art["simulated"]
    out = []
    coll = sim.get("n_collisions_per_replicate") or [None] * len(sim["seeds"])
    for seed, rd, c in zip(sim["seeds"], sim["run_dirs"], coll, strict=True):
        p = cb.resolve(rd)
        if not p.is_dir():
            p = runs_root / Path(rd).parent.parent.name / Path(rd).parent.name / str(seed)
        meta = p / "meta.json"
        if not meta.is_file():
            out.append(f"{seed}: no meta.json under {p}")
            continue
        m = json.loads(meta.read_text())
        if m.get("config_hash") != art["config_hash"]:
            out.append(f"{seed}: meta config_hash {m.get('config_hash')} != {art['config_hash']}")
        if c is not None and m.get("n_collisions") is not None and int(m["n_collisions"]) != int(c):
            out.append(
                f"{seed}: n_collisions {m['n_collisions']} in meta.json, {c} in the artifact"
            )
    return out


def criteria_i24(
    refit: dict[str, Any], frm: dict[str, Any], refit_art: dict[str, Any]
) -> dict[str, Any]:
    """C1-C5, adapted from corridor_b1b2.part_ii (round p14), on battery_reading's fields."""
    coll = refit["collisions"]
    c1 = (
        bool(refit_art.get("zero_collisions"))
        and len(coll) == len(refit["seeds"])
        and all(c == 0 for c in coll)
    )
    real_r, real_f = cb.ci(refit["realised"]), cb.ci(frm["realised"])
    assert real_r is not None and real_f is not None
    c2 = real_r[0] >= real_f[0] - INSERTION_TOLERANCE
    c3 = float(refit["geh_lt5_share"]) >= float(frm["geh_lt5_share"])
    c4 = refit["rmspe_15min"] <= frm["rmspe_15min"] + RMSPE_MARGIN
    from_wave = bool(frm["wave_row"]["passed"])
    c5 = bool(refit["wave_row"]["passed"]) if from_wave else None
    return {
        "C1": {"rule": "0 collisions in every run", "collisions": coll, "verdict": bool(c1)},
        "C2": {
            "rule": f"mean realised share >= the from-arm's - {INSERTION_TOLERANCE} (FRISCO_PROTOCOL "
            "Amendment 2 clarification: no winning by backlog)",
            "refit": real_r,
            "from_arm": real_f,
            "paired_refit_minus_from": cb.paired(refit["realised"], frm["realised"]),
            "literal_reading_ge_from_arm": bool(real_r[0] >= real_f[0]),
            "verdict": bool(c2),
        },
        "C3": {
            "rule": "hourly link-flow GEH < 5 share not below the from-arm's",
            "refit": refit["geh_lt5_share"],
            "from_arm": frm["geh_lt5_share"],
            "verdict": bool(c3),
        },
        "C4": {
            "rule": f"15-min segment-speed RMSPE <= the from-arm's + {RMSPE_MARGIN}",
            "refit": refit["rmspe_15min"],
            "from_arm": frm["rmspe_15min"],
            "limit": frm["rmspe_15min"] + RMSPE_MARGIN,
            "per_replicate_paired_refit_minus_from": cb.paired(
                refit["rmspe_15min_per_replicate"], frm["rmspe_15min_per_replicate"]
            ),
            "verdict": bool(c4),
        },
        "C5": {
            "rule": "the wave verdict unchanged where the from-arm passes",
            "from_arm_passes": from_wave,
            "refit_passes": bool(refit["wave_row"]["passed"]),
            "verdict": c5,
            "note": None if from_wave else "the from-arm fails the wave row; C5 does not bind",
        },
    }


def fit_checks_i24(
    fit: dict[str, Any], frm_art: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Whether the fit ran as B5 fixed it, and the rule re-derived from its per-seed records."""
    obs_doc = load(I24_OBSERVED) or {}
    obs_hourly = np.asarray(obs_doc.get(I24_OBSERVED_KEY, []), float)
    fit_w = list(I24_FIT_WINDOWS)

    def recompute(rec: dict[str, Any]) -> ObjectiveReading:
        sims = [
            (np.asarray(p["counts_per_window"], float) * (3600.0 / I24_WINDOW_S))[:, fit_w]
            .ravel()
            .tolist()
            for p in rec["per_seed"]
        ]
        return replicate_mean_objective(sims, obs_hourly[:, fit_w].ravel().tolist())

    grid = fit.get("grid") or {}
    scales = sorted(_key(r["scale"]) for r in fit["per_scale"])
    coarse = [_key(s) for s in grid.get("coarse", [])]
    choice = grid.get("coarse_choice")
    refine = (
        [s for s in refine_scales(choice) if _key(s) not in coarse] if choice is not None else []
    )
    realised = [float(x) for x in frm_art["simulated"]["demand_realized_fraction"]]
    floor = math.fsum(realised) / len(realised) - INSERTION_TOLERANCE
    carried = float(((load(I24_CARRIED_FIT) or {}).get("best") or {}).get("scale", math.nan))
    from_p4 = I24_CARRIED_FIT in str(fit.get("reference_scale_source", ""))
    rd = rederive(fit, recompute)
    coarse_only = {
        **fit,
        "per_scale": [r for r in fit["per_scale"] if _key(r["scale"]) in coarse],
        "selection": fit.get("coarse_selection") or {},
    }
    rd_coarse = rederive(coarse_only, recompute)
    checks = {
        "corridor": fit.get("corridor") == "i24",
        "seeds_are_the_from_arm_batterys_first_five": [int(s) for s in fit.get("seeds", [])]
        == [int(s) for s in frm_art["seeds"]][:5],
        "floor_is_from_arm_minus_0.01": math.isclose(
            float(fit["min_inserted"]), floor, abs_tol=1e-12
        ),
        # the fit refuses unless base x reference = the from-arm; the p4 fit's level when it is the source
        "reference_is_the_from_arms_level": bool((fit.get("recipe_check") or {}).get("ok"))
        and (not from_p4 or math.isclose(float(fit["reference_scale"]), carried)),
        "coarse_grid": coarse == [_key(s) for s in I24_COARSE],
        "coarse_choice_is_the_rule": rd_coarse["ok"] and rd_coarse["chosen_scale"] == choice,
        "refine_around_the_coarse_choice": [_key(s) for s in grid.get("refine", [])]
        == [_key(s) for s in refine],
        "grid_is_coarse_plus_refine": scales == sorted({*coarse, *(_key(s) for s in refine)}),
        "objectives_and_choice_rederived": rd["ok"],
        "reproduction_exact": bool((fit.get("reproduction") or {}).get("exact")),
    }
    return checks, {
        "final": {k: v for k, v in rd.items() if not k.startswith("_")},
        "coarse": {k: v for k, v in rd_coarse.items() if not k.startswith("_")},
    }


def evaluate_i24(out: Path, fit_path: str, label: str, runs_root: Path) -> dict[str, Any]:
    fit = load(fit_path)
    problems: list[str] = []
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": _now(),
        **cbb._code(),
        "spec": SPEC,
        "status": STATUS,
        "corridor": "i24",
        "fit": {"path": fit_path, "sha256": sha(fit_path)},
    }
    if fit is None:
        doc.update(problems=[f"no fit artifact {fit_path}"], candidate=None)
        return _write(out, doc)
    frm_path = fit["from_arm"]["battery"]["path"]
    frm_art = load(frm_path)
    if frm_art is None:
        doc.update(problems=[f"no from-arm battery {frm_path}"], candidate=None)
        return _write(out, doc)
    if sha(frm_path) != fit["from_arm"]["battery"]["sha256"]:
        problems.append(f"{frm_path} is not the battery the fit read (sha256 differs)")
    checks, rederived = fit_checks_i24(fit, frm_art)
    problems += [f"the fit was not run as B5 fixed it: {k}" for k, ok in checks.items() if not ok]
    doc["fit"].update(
        {
            "from_arm": fit["from_arm"],
            "reference_scale": fit["reference_scale"],
            "min_inserted": fit["min_inserted"],
            "seeds": fit["seeds"],
            "grid": fit["grid"],
            "checks": checks,
            "rederived": rederived,
            "selection": fit["selection"],
            "coarse_selection": fit.get("coarse_selection"),
            "chosen": fit.get("chosen"),
            "constraint_unmet": fit.get("constraint_unmet"),
            "reproduction": fit.get("reproduction"),
            "per_scale": [_strip_scale(r) for r in fit["per_scale"]],
        }
    )
    if fit.get("constraint_unmet"):
        doc.update(
            problems=problems,
            criteria=None,
            candidate=None,
            reading="constraint_unmet: no scale's mean inserted fraction reached the floor; the fit "
            "stopped and no battery ran (docs/PRE_FRISCO_PROGRAM.md B5, Stop)",
        )
        return _write(out, doc)
    art_path = f"artifacts/i24_validation_{label}.json"
    refit_art = load(art_path)
    if refit_art is None:
        doc.update(problems=[*problems, f"no refit battery {art_path}"], candidate=None)
        return _write(out, doc)
    refit, frm = battery_reading(refit_art), battery_reading(frm_art)
    chosen = fit.get("chosen") or {}
    scn = fit.get("scenario_out") or {}
    if refit_art["config_hash"] != chosen.get("config_hash") or refit_art["config_hash"] != scn.get(
        "config_hash"
    ):
        problems.append(
            f"the refit battery ran {refit_art['config_hash']}, the fit chose {chosen.get('config_hash')} "
            f"and wrote {scn.get('config_hash')}"
        )
    if refit["seeds"] != frm["seeds"]:
        problems.append("the refit and the from-arm ran different seeds")
    obs_differs = cbb.observed_differs(refit_art, frm_art)
    if obs_differs:
        problems.append(
            "the refit and the from-arm were scored against different observed sides: "
            + ", ".join(obs_differs)
        )
    problems += meta_check(refit_art, runs_root)
    level_unchanged = _key(chosen.get("scale", math.nan)) == _key(fit["reference_scale"])
    same_level_repro = None
    if level_unchanged:
        same_level_repro = cbb._canon(refit_art["simulated"]["counts_per_replicate"]) == cbb._canon(
            frm_art["simulated"]["counts_per_replicate"]
        )
        if not same_level_repro:
            problems.append(
                "the fit returned the from-arm's level, but the refit battery does not reproduce its counts"
            )
    crit = criteria_i24(refit, frm, refit_art)
    holds = _holds(crit)
    peaks = {}
    for k in I24_SECTIONS_2H:
        mr, mf = cb.ci(refit["flows_2h_veh_h"][k]), cb.ci(frm["flows_2h_veh_h"][k])
        o = refit["observed_2h_veh_h"][k]
        assert mr is not None and mf is not None
        peaks[k] = {
            "observed": o,
            "refit": mr,
            "from_arm": mf,
            "paired_refit_minus_from": cb.paired(
                refit["flows_2h_veh_h"][k], frm["flows_2h_veh_h"][k]
            ),
            "geh_refit": cb.geh(mr[0], o),
            "geh_from_arm": cb.geh(mf[0], o),
        }
    braking = _braking_file(runs_root / label / "hard_braking.json")
    # stage p14's B2 re-run sidecars belong to the from-arm only when it is the B2 arm
    from_braking = _own(load(I24_FROM_BRAKING), frm_art)
    from_locks = _own(load(I24_FROM_LOCKS), frm_art)
    targets = cbb._targets()
    doc.update(
        {
            "refit_battery": {
                "label": label,
                "path": art_path,
                "sha256": sha(art_path),
                "scenario": refit_art.get("scenario"),
                "config_hash": refit_art["config_hash"],
            },
            "from_battery": {
                "path": frm_path,
                "sha256": sha(frm_path),
                "scenario": frm_art.get("scenario"),
                "config_hash": frm_art["config_hash"],
            },
            "level_unchanged": level_unchanged,
            "level_unchanged_reproduces_from_arm": same_level_repro,
            "criteria": crit,
            "verdicts": _verdicts(crit),
            "c1_c5_hold": holds,
            "candidate": None if problems else holds,
            "problems": problems,
            "reported": {
                "gate_rows": {
                    "refit": cbb.gate_rows(refit_art),
                    "from_arm": cbb.gate_rows(frm_art),
                },
                "rmspe_5min": {"refit": refit["rmspe_5min"], "from_arm": frm["rmspe_5min"]},
                "sections_2h": peaks,
                "fronts": {
                    det: {
                        "refit": cb.ci(refit[f"fronts_{det}"]),
                        "from_arm": cb.ci(frm[f"fronts_{det}"]),
                    }
                    for det in ("standard", "stripe")
                },
                "hard_braking_below_8p9": {
                    "refit": _braking_total(braking, refit["seeds"]),
                    "from_arm_p14_rerun": _braking_total(from_braking, frm["seeds"]),
                },
                "locks": {
                    "refit": {
                        "no_locks_row": refit["no_locks_row"],
                        "zero_locks": refit_art.get("zero_locks"),
                    },
                    "from_arm": None
                    if from_locks is None
                    else {"source": I24_FROM_LOCKS, "zero_locks": from_locks.get("zero_locks")},
                },
                "breakdown": {
                    "refit": breakdown_block(refit["realised"], refit["locked_per_replicate"]),
                    "from_arm": breakdown_block(frm["realised"], None),
                },
                "ramp_flows": {
                    "refit": cbb.ramp_reading(label, refit_art, targets),
                    "from_arm": cbb.ramp_reading(
                        Path(frm_path).stem.removeprefix("i24_validation_"), frm_art, targets
                    ),
                },
                "estimators": cbb.ESTIMATORS,
            },
            "not_validation": NOT_VALIDATION["i24"],
        }
    )
    return _write(out, doc)


def _own(side: dict[str, Any] | None, art: dict[str, Any]) -> dict[str, Any] | None:
    """A sidecar of a battery, kept only when it records the battery's configuration."""
    return side if side is not None and side.get("config_hash") == art.get("config_hash") else None


def _braking_file(p: Path) -> dict[str, Any] | None:
    return json.loads(p.read_text()) if p.is_file() else None


def _braking_total(brk: dict[str, Any] | None, seeds: list[int]) -> int | None:
    if brk is None:
        return None
    per = [brk.get("per_seed", {}).get(str(s), {}) for s in seeds]
    if not all("below_ms2" in p for p in per):
        return None
    return int(sum(int(p["below_ms2"]["-8.9"]) for p in per))


# --------------------------------------------------------------------------- I-94


def gate_value(gate: dict[str, Any], check: str, day_set: str) -> dict[str, Any] | None:
    for c in gate.get("checks", []):
        if c.get("check") == check and c.get("day_set") == day_set:
            return {k: c.get(k) for k in ("status", "value", "satisfied", "gating", "target")}
    return None


def criteria_i94(
    refit: dict[str, Any], frm: dict[str, Any], gate_r: dict[str, Any], gate_f: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """C1-C5 and NL on the corridor batteries and their gates (C3, C4, C5 read the gate on the calibration days)."""
    problems: list[str] = []
    coll = [r.get("n_collisions") for r in refit["per_seed"]]
    c1 = bool(refit.get("zero_collisions")) and all(c == 0 for c in coll)
    dep_r = [float(r["insertion"]["departed_fraction"]) for r in refit["per_seed"]]
    dep_f = [float(r["insertion"]["departed_fraction"]) for r in frm["per_seed"]]
    real_r, real_f = cb.ci(dep_r), cb.ci(dep_f)
    assert real_r is not None and real_f is not None
    c2 = real_r[0] >= real_f[0] - INSERTION_TOLERANCE
    g = {
        k: (gate_value(gate_r, *k.split("|")), gate_value(gate_f, *k.split("|")))
        for k in ("C1|calibration", "C3|calibration", "C4|calibration")
    }
    for k, (a, b) in g.items():
        if a is None or b is None:
            problems.append(f"gate check {k} missing from a gate artifact")
    c1g, c3g, c4g = g["C1|calibration"], g["C3|calibration"], g["C4|calibration"]
    c3 = None if None in c1g else float(c1g[0]["value"]) >= float(c1g[1]["value"])
    c4 = None if None in c3g else float(c3g[0]["value"]) <= float(c3g[1]["value"]) + RMSPE_MARGIN
    from_wave = None if c4g[1] is None else c4g[1]["status"] == "pass"
    c5 = (None if c4g[0] is None else c4g[0]["status"] == "pass") if from_wave else None
    locks = departed_share_locks(dep_r)
    nl = not any(locks)
    crit = {
        "C1": {"rule": "0 collisions in every run", "collisions": coll, "verdict": bool(c1)},
        "C2": {
            "rule": f"mean realised share (insertion departed fraction) >= the from-arm's - {INSERTION_TOLERANCE}",
            "refit": real_r,
            "from_arm": real_f,
            "paired_refit_minus_from": cb.paired(dep_r, dep_f),
            "literal_reading_ge_from_arm": bool(real_r[0] >= real_f[0]),
            "verdict": bool(c2),
        },
        "C3": {
            "rule": "calibration-day GEH < 5 share (baseline gate C1, anchored hours) not below the from-arm's",
            "refit": None if c1g[0] is None else c1g[0]["value"],
            "from_arm": None if c1g[1] is None else c1g[1]["value"],
            "verdict": c3,
        },
        "C4": {
            "rule": f"calibration-day 15-min station-speed RMSPE (baseline gate C3) <= the from-arm's + {RMSPE_MARGIN}",
            "refit": None if c3g[0] is None else c3g[0]["value"],
            "from_arm": None if c3g[1] is None else c3g[1]["value"],
            "verdict": c4,
        },
        "C5": {
            "rule": "the wave verdict (baseline gate C4) unchanged where the from-arm passes",
            "from_arm_status": None if c4g[1] is None else c4g[1]["status"],
            "refit_status": None if c4g[0] is None else c4g[0]["status"],
            "verdict": c5,
            "note": None if from_wave else "the from-arm does not pass C4; C5 does not bind",
        },
        "NL": {
            "rule": f"no seed's departed share below {LOCK_MEDIAN_RATIO} of the battery median "
            "(protocol §9.5, the plan's common rules)",
            "per_seed_collapsed": locks,
            "verdict": nl,
        },
    }
    return crit, problems


def fit_checks_i94(
    fit: dict[str, Any], frm_art: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    def recompute(rec: dict[str, Any]) -> ObjectiveReading:
        return pooled_objective(
            [
                [(float(m), float(c)) for m, c in p["calibration_station_hours"]]
                for p in rec["per_seed"]
            ]
        )

    dq = load(I94_DATA_QUALITY) or {}
    count_error = float((dq.get("parameters") or {}).get("count_error", math.nan))
    grid = fit.get("grid") or {}
    scales = sorted(_key(r["scale"]) for r in fit["per_scale"])
    floor = float(frm_art["insertion"]["mean_departed_fraction"]) - INSERTION_TOLERANCE
    rd = rederive(fit, recompute)
    checks = {
        "corridor": fit.get("corridor") == "i94",
        "seeds_are_the_from_arm_batterys_first_five": [int(s) for s in fit.get("seeds", [])]
        == [int(s) for s in frm_art["seeds"]][:5],
        "floor_is_from_arm_minus_0.01": math.isclose(
            float(fit["min_inserted"]), floor, abs_tol=1e-12
        ),
        "reference_is_one": float(fit["reference_scale"]) == 1.0,
        "grid": scales == [_key(s) for s in I94_GRID] == [_key(s) for s in grid.get("scales", [])],
        "grid_inside_count_error": math.isfinite(count_error)
        and within_count_error(scales, count_error),
        "objectives_and_choice_rederived": rd["ok"],
        "reproduction_exact": bool((fit.get("reproduction") or {}).get("exact")),
    }
    return checks, {"final": {k: v for k, v in rd.items() if not k.startswith("_")}}


def evaluate_i94(out: Path, fit_path: str) -> dict[str, Any]:
    fit = load(fit_path)
    problems: list[str] = []
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": _now(),
        **cbb._code(),
        "spec": SPEC,
        "status": STATUS,
        "corridor": "i94",
        "fit": {"path": fit_path, "sha256": sha(fit_path)},
    }
    if fit is None:
        doc.update(problems=[f"no fit artifact {fit_path}"], candidate=None)
        return _write(out, doc)
    frm_path = fit["from_arm"]["battery"]["path"]
    frm_art = load(frm_path)
    if frm_art is None:
        doc.update(problems=[f"no from-arm battery {frm_path}"], candidate=None)
        return _write(out, doc)
    if sha(frm_path) != fit["from_arm"]["battery"]["sha256"]:
        problems.append(f"{frm_path} is not the battery the fit read (sha256 differs)")
    checks, rederived = fit_checks_i94(fit, frm_art)
    problems += [f"the fit was not run as B5 fixed it: {k}" for k, ok in checks.items() if not ok]
    doc["fit"].update(
        {
            "from_arm": fit["from_arm"],
            "min_inserted": fit["min_inserted"],
            "seeds": fit["seeds"],
            "grid": fit["grid"],
            "checks": checks,
            "rederived": rederived,
            "selection": fit["selection"],
            "chosen": fit.get("chosen"),
            "constraint_unmet": fit.get("constraint_unmet"),
            "reproduction": fit.get("reproduction"),
            "per_scale": [_strip_scale(r) for r in fit["per_scale"]],
        }
    )
    if fit.get("constraint_unmet"):
        doc.update(
            problems=problems,
            criteria=None,
            candidate=None,
            reading="constraint_unmet: no factor's mean inserted fraction reached the floor; the fit "
            "stopped and no battery ran (docs/PRE_FRISCO_PROGRAM.md B5, Stop)",
        )
        return _write(out, doc)
    from_name = fit["from_arm"]["name"]
    scn = fit.get("scenario_out") or {}
    refit_name = scn.get("name")
    paths = {
        "refit": f"artifacts/validation_{refit_name}.json",
        "refit_gate": f"artifacts/baseline_gate_{refit_name}.json",
        "refit_gated": f"artifacts/validation_{refit_name}_gated.json",
        "from_gate": f"artifacts/baseline_gate_{from_name}.json",
    }
    docs = {k: load(v) for k, v in paths.items()}
    missing = [paths[k] for k in ("refit", "refit_gate", "from_gate") if docs[k] is None]
    if missing:
        doc.update(problems=[*problems, *(f"missing {m}" for m in missing)], candidate=None)
        return _write(out, doc)
    refit, gate_r, gate_f = docs["refit"], docs["refit_gate"], docs["from_gate"]
    assert refit is not None and gate_r is not None and gate_f is not None
    if refit.get("config_hash") != scn.get("config_hash") or refit.get("config_hash") != (
        fit.get("chosen") or {}
    ).get("config_hash"):
        problems.append(
            f"the refit battery ran {refit.get('config_hash')}, the fit wrote {scn.get('config_hash')}"
        )
    if gate_r.get("config_hash") != refit.get("config_hash"):
        problems.append("the refit's gate is not its battery's")
    if gate_f.get("config_hash") != frm_art.get("config_hash"):
        problems.append("the from-arm's gate is not its battery's")
    if [int(s) for s in refit["seeds"]] != [int(s) for s in frm_art["seeds"]]:
        problems.append("the refit and the from-arm ran different seeds")
    if (refit.get("observations") or {}).get("path") != (frm_art.get("observations") or {}).get(
        "path"
    ):
        problems.append("the refit and the from-arm were scored against different observations")
    crit, gate_problems = criteria_i94(refit, frm_art, gate_r, gate_f)
    problems += gate_problems
    if any(v["verdict"] is None and k != "C5" for k, v in crit.items()):
        problems.append("a criterion could not be read")
    holds = _holds(crit)
    level_unchanged = _key((fit.get("chosen") or {}).get("scale", math.nan)) == 1.0
    same_level_repro = None
    if level_unchanged:

        def ins(a: dict[str, Any]) -> list[Any]:
            return [
                [r["insertion"].get(k) for k in ("planned", "departed", "arrived")]
                for r in a["per_seed"]
            ]

        same_level_repro = cbb._canon(ins(refit)) == cbb._canon(ins(frm_art))
        if not same_level_repro:
            problems.append(
                "the fit returned the from-arm's level, but the refit battery does not reproduce its insertion"
            )
    dep_r = [float(r["insertion"]["departed_fraction"]) for r in refit["per_seed"]]
    dep_f = [float(r["insertion"]["departed_fraction"]) for r in frm_art["per_seed"]]
    gate_report = {
        f"{c}|{d}": {"refit": gate_value(gate_r, c, d), "from_arm": gate_value(gate_f, c, d)}
        for c in ("C1", "C2", "C3", "C4", "C6")
        for d in ("calibration", "validation")
        if gate_value(gate_r, c, d) is not None or gate_value(gate_f, c, d) is not None
    }
    doc.update(
        {
            "refit_battery": {
                "path": paths["refit"],
                "sha256": sha(paths["refit"]),
                "scenario": refit.get("scenario"),
                "config_hash": refit.get("config_hash"),
                "gate": {"path": paths["refit_gate"], "sha256": sha(paths["refit_gate"])},
                "gated_report": {
                    "path": paths["refit_gated"],
                    "present": docs["refit_gated"] is not None,
                },
            },
            "from_battery": {
                "path": frm_path,
                "sha256": sha(frm_path),
                "scenario": frm_art.get("scenario"),
                "config_hash": frm_art.get("config_hash"),
                "gate": {"path": paths["from_gate"], "sha256": sha(paths["from_gate"])},
            },
            "level_unchanged": level_unchanged,
            "level_unchanged_reproduces_from_arm": same_level_repro,
            "criteria": crit,
            "verdicts": _verdicts(crit),
            "criteria_hold": holds,
            "candidate": None if problems else holds,
            "problems": problems,
            "reported": {
                "gate": gate_report,
                "gate_verdict": {"refit": gate_r.get("verdict"), "from_arm": gate_f.get("verdict")},
                "insertion": {
                    "refit": refit.get("insertion"),
                    "from_arm": frm_art.get("insertion"),
                },
                "collisions": {
                    "refit": refit.get("collisions"),
                    "from_arm": frm_art.get("collisions"),
                },
                "locks": {
                    "refit": {"zero_locks": refit.get("zero_locks"), "locks": refit.get("locks")},
                    "from_arm": {"zero_locks": frm_art.get("zero_locks")},
                },
                "breakdown": {
                    "refit": breakdown_block(
                        dep_r, [(r.get("locks") or {}).get("locked") for r in refit["per_seed"]]
                    ),
                    "from_arm": breakdown_block(
                        dep_f, [(r.get("locks") or {}).get("locked") for r in frm_art["per_seed"]]
                    ),
                },
                "weave_exits": {
                    "refit": refit.get("weave_exits"),
                    "from_arm": frm_art.get("weave_exits"),
                },
                "battery_criteria_rows": {
                    "refit": cbb.gate_rows(refit),
                    "from_arm": cbb.gate_rows(frm_art),
                },
            },
            "not_validation": NOT_VALIDATION["i94"],
        }
    )
    return _write(out, doc)


# --------------------------------------------------------------------------- commands


def _write(out: Path, doc: dict[str, Any]) -> dict[str, Any]:
    doc.setdefault("problems", [])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_safe(doc), indent=1, allow_nan=False) + "\n")
    return doc


def _safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return _safe(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="corridor", required=True)
    a24 = sub.add_parser("i24")
    a24.add_argument("--out", type=Path, default=Path(OUT["i24"]))
    a24.add_argument("--fit", default=FIT["i24"])
    a24.add_argument("--label", default=I24_LABEL)
    a24.add_argument("--runs-root", type=Path, default=None)
    a94 = sub.add_parser("i94")
    a94.add_argument("--out", type=Path, default=Path(OUT["i94"]))
    a94.add_argument("--fit", default=FIT["i94"])
    args = ap.parse_args(argv)
    out = args.out if args.out.is_absolute() else REPO / args.out
    if args.corridor == "i24":
        runs_root = args.runs_root or REPO / "runs" / "i24_validation"
        doc = evaluate_i24(out, args.fit, args.label, runs_root)
    else:
        doc = evaluate_i94(out, args.fit)
    print(
        f"{args.corridor}: "
        + (
            "criteria " + json.dumps(doc.get("verdicts"))
            if doc.get("verdicts")
            else str(doc.get("reading", ""))
        )
        + f"; candidate: {doc.get('candidate')} -> {out}"
    )
    for p in doc.get("problems") or []:
        print(f"PROBLEM: {p}", file=sys.stderr)
    blocked = bool(doc.get("problems")) or doc.get("criteria") is None
    return EXIT_BLOCKED if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
