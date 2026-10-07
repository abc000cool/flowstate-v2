"""Amendment B2's corridor round (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3): the arm, its ramp flows, R1-R5.

usage (repository root; stage p13_i24_b2 of scripts/gcp/pipeline_i24.sh runs all three on the VM):
  corridor_b2.py arm --base scenarios/i24_replica_flow_rc_corrected_dc.yaml --rc-inputs artifacts/i24_replica_inputs_flow_rc.json \\
      --out scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml
  corridor_b2.py reduce --battery artifacts/i24_validation_<label>.json --out artifacts/i24_b2_ramp_flows_<label>.json
  corridor_b2.py score --ref <battery> --b2 <battery> --ref-flows <reduce> --b2-flows <reduce> \\
      [--committed-ref artifacts/i24_validation_dc_refit.json] --out artifacts/boundary_b2_corridor.json

* ``arm`` writes the B2 arm: ``_dc_refit``'s recipe on the ``_rc`` family. ``_dc_refit`` is
  scripts/i24_fit_demand_scale.py's ``scaled_config`` at its fitted scale (``best.scale`` of
  artifacts/demand_scale_i24_flow_dc.json, 0.925: mainline and on-ramp inflows x s, exit fractions and
  boundary unchanged) on scenarios/i24_replica_flow_corrected_dc.yaml. The B2 arm carries that scale
  (not refit: the mainline entry demand stays the reference's) onto the rc family's driver-calibrated
  corrected arm. Refused, writing nothing, unless (1) the recipe on the flow base reproduces the
  committed _dc_refit document; (2) the rc inputs equal the flow family's in everything B2 does not
  touch (data hash, mainline crossings, coverage, the ramps' recorded counts and reference crossings,
  boundary, entry lane shares) and record mode ``exclude``; (3) the new document differs from _dc_refit
  only in its name and the ramps' inflow / exit-fraction values; (4) those values are the builder's
  arithmetic on the corrected counts and the _dc_refit ones on the recorded counts, to the digit.
* ``reduce`` reads each replicate's meta.json and vehicles.parquet (never trajectories) and counts the
  modelled vehicles of every ramp (``REDUCE_DEFINITION``). Output beside the battery artifacts so the
  score can be re-read from the archive.
* ``score`` applies R1-R5 as fixed in §8.4.3 (the reading of each is in the output's
  ``definitions``) and writes ``artifacts/boundary_b2_corridor.json``.
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy import stats

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

FIT = "artifacts/demand_scale_i24_flow_dc.json"
REF_SCENARIO = "scenarios/i24_replica_flow_speedcal_dc_refit.yaml"
REF_INPUTS = "artifacts/i24_replica_inputs_flow.json"
COUNT_CHECK = "artifacts/i24_count_consistency.json"
NAME = "i24_replica_flow_rc_speedcal_dc_refit"
PEAK_SECTIONS_M = (2200.0, 3200.0)
WINDOW_S = 300.0
COVERAGE_WINDOW_S = 900.0
RMSPE_SLACK = 0.02
GEH_MAX = 5.0
SERIES = {"on": "prior_mainline_per_window", "off": "later_mainline_per_window"}
REDUCE_DEFINITION = (
    "per ramp, the modelled vehicles over the study period [warmup_s, duration_s) of the run's config: an "
    "on-ramp's entrants that reached the corridor (vehicles.parquet origin = the ramp, entry_t_s in the "
    "period), an off-ramp's vehicles that left the corridor for it (destination_final = the ramp, last_t_s "
    "in the period and more than 1 s before the run's end, i.e. not still on the corridor); veh_h = n / "
    "the period's hours"
)


def rel(p: Path) -> str:
    p = p.resolve()
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def resolve(run_dir: str) -> Path:
    """A battery's run_dir (absolute on the VM that wrote it) under this checkout."""
    p = Path(run_dir)
    if p.is_dir():
        return p
    parts = p.parts
    return REPO.joinpath(*parts[parts.index("runs") :]) if "runs" in parts else p


def ci(d: list[float]) -> dict[str, float]:
    a = np.asarray(d, float)
    sd = a.std(ddof=1) if len(a) > 1 else 0.0
    h = stats.t.ppf(0.975, len(a) - 1) * sd / math.sqrt(len(a)) if sd > 0 else 0.0
    return {
        "mean": float(a.mean()),
        "lo95": float(a.mean() - h),
        "hi95": float(a.mean() + h),
        "n": len(a),
    }


def fail(msg: str) -> None:
    print(f"refused: {msg}", file=sys.stderr)
    sys.exit(2)


# --- arm ---------------------------------------------------------------------------------------------------


def expected_ramp_values(inputs: dict, scale: float, corrected: bool) -> dict[str, list[float]]:
    """Each ramp's inflow (on) or exit-fraction (off) values as builder + driver step + scaled_config give them."""
    cov = [float(r["coverage_used"]) for r in inputs["coverage"]["rows"]]
    out = {}
    for r in inputs["ramps"]:
        cnt = r["ramp_lane_crossings_corrected"] if corrected else r["ramp_lane_crossings"]
        if r["kind"] == "on":
            out[r["name"]] = [
                round(
                    round(
                        round(c / WINDOW_S, 6)
                        / cov[min(i * int(WINDOW_S) // int(COVERAGE_WINDOW_S), len(cov) - 1)],
                        6,
                    )
                    * scale,
                    6,
                )
                for i, c in enumerate(cnt)
            ]
        else:
            ref = r["mainline_ref_crossings"]
            out[r["name"]] = [
                round(min(max(c / m, 0.0), 1.0), 6) if m > 0 else 0.0
                for c, m in zip(cnt, ref, strict=True)
            ]
    return out


def ramp_values(doc: dict) -> dict[str, list[float]]:
    return {
        s["name"]: [v for _, v in s["inflow" if s["kind"] == "on" else "exit_fraction"]]
        for s in doc["network"]["ramps"]
    }


def masked(doc: dict) -> dict:
    """The document without its name and with each ramp's inflow / exit-fraction values reduced to their time grid."""
    d = json.loads(json.dumps(doc))
    d.pop("name", None)
    for s in d["network"]["ramps"]:
        key = "inflow" if s["kind"] == "on" else "exit_fraction"
        s[key] = [t for t, _ in s[key]]
    return d


def cmd_arm(a: argparse.Namespace) -> None:
    from i24_fit_demand_scale import scaled_config

    from flowstate_core.config import ScenarioConfig, config_hash

    fit_p, ref_p, base_p, out_p = (REPO / a.fit, REPO / a.ref_scenario, REPO / a.base, REPO / a.out)
    fit = json.loads(fit_p.read_text())
    s, fleet, kind = float(fit["best"]["scale"]), fit["fleet_artifact"], fit["base"]
    ref_doc = yaml.safe_load(ref_p.read_text())
    again = scaled_config(s, fleet, kind, (REPO / fit["base_scenario"]).resolve(), ref_doc["name"])
    if again != ref_doc:
        fail(
            f"{rel(ref_p)} is not {rel(fit_p)}'s scale on {fit['base_scenario']}: the recipe does not reproduce it"
        )
    ref_hash = config_hash(ScenarioConfig.model_validate(ref_doc))

    ref_in, rc_in = (
        json.loads((REPO / a.ref_inputs).read_text()),
        json.loads((REPO / a.rc_inputs).read_text()),
    )
    if (rc_in.get("ramp_through_traffic") or {}).get("mode") != "exclude":
        fail(f"{a.rc_inputs} was not built with --ramp-through-traffic exclude")
    same = {
        "data_hash": (ref_in["data_hash"], rc_in["data_hash"]),
        "mainline crossings": (ref_in["mainline"]["crossings"], rc_in["mainline"]["crossings"]),
        "coverage": (
            [r["coverage_used"] for r in ref_in["coverage"]["rows"]],
            [r["coverage_used"] for r in rc_in["coverage"]["rows"]],
        ),
        "ramp counts": (
            [r["ramp_lane_crossings"] for r in ref_in["ramps"]],
            [r["ramp_lane_crossings"] for r in rc_in["ramps"]],
        ),
        "ramp reference crossings": (
            [r.get("mainline_ref_crossings") for r in ref_in["ramps"]],
            [r.get("mainline_ref_crossings") for r in rc_in["ramps"]],
        ),
        "boundary": (
            ref_in["boundary"]["schedule_data_time"],
            rc_in["boundary"]["schedule_data_time"],
        ),
        "entry lane shares": (ref_in["entry_lane_shares"], rc_in["entry_lane_shares"]),
    }
    differ = [k for k, (x, y) in same.items() if x != y]
    if differ:
        fail(
            f"{a.rc_inputs} differs from {a.ref_inputs} beyond the ramp correction: {', '.join(differ)}"
        )

    doc = scaled_config(s, fleet, kind, base_p.resolve(), a.name)
    if masked(doc) != masked(ref_doc):
        fail(f"the arm differs from {rel(ref_p)} beyond its name and the ramp values")
    checks = {
        "reference ramp values": (
            ramp_values(ref_doc),
            expected_ramp_values(ref_in, s, corrected=False),
        ),
        "B2 ramp values": (ramp_values(doc), expected_ramp_values(rc_in, s, corrected=True)),
    }
    for what, (got, want) in checks.items():
        if got != want:
            bad = [n for n in want if got.get(n) != want[n]]
            fail(f"{what} are not the builder's arithmetic for {', '.join(bad)}")
    cfg = ScenarioConfig.model_validate(doc)
    h = config_hash(cfg)
    block = rc_in["ramp_through_traffic"]
    lines = [
        f"# {a.name} — amendment B2's corridor arm (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3; PROPOSED, not",
        "# adopted): _dc_refit's recipe on the _rc family. scripts/i24_fit_demand_scale.py scaled_config at",
        f"# _dc_refit's fitted scale s = {s:.3f} (best.scale of {rel(fit_p)}, sha256 {sha(fit_p)[:12]}…;",
        "# carried, not refit: mainline and on-ramp inflows x s, exit fractions and the boundary as built), fleet",
        f"# {fleet}, on {rel(base_p)} (sha256 {sha(base_p)[:12]}…),",
        "# scripts/apply_driver_calibration.py's edit of the builder's flow_rc corrected arm",
        "# (scripts/i24_build_replica.py --suffix flow_rc --osm corrected --lc-strategic 5 --lc-strategic-ramp 1",
        f"# --entry-lanes observed_flow --ramp-through-traffic exclude; {block['artifact']}, data hash",
        f"# {block['artifact_data_hash'][:12]}…, sha256 {block['artifact_sha256'][:12]}…).",
        f"# Checked when written: the recipe reproduces {rel(ref_p)} (config hash {ref_hash});",
        "# this file differs from it only in its name and the ramps' inflow / exit-fraction values, which are the",
        f"# builder's arithmetic on the corrected counts of {a.rc_inputs}.",
        f"# Written by {rel(Path(__file__))} arm; config hash {h}; seeded=False.",
    ]
    out_p.write_text("\n".join(lines) + "\n" + yaml.safe_dump(doc, sort_keys=False))
    print(f"-> {rel(out_p)} ({h}); reference {rel(ref_p)} ({ref_hash}), scale {s:.3f}")


# --- reduce ------------------------------------------------------------------------------------------------


def ramp_flows(run_dir: Path) -> dict[str, Any]:
    """One replicate's modelled ramp vehicles over the study period (``REDUCE_DEFINITION``)."""
    from validation.vehicles import read_vehicles

    meta = json.loads((run_dir / "meta.json").read_text())
    sim = meta["config"]["sim"]
    t0, t1 = float(sim["warmup_s"]), float(sim["duration_s"])
    veh = read_vehicles(run_dir, columns=["origin", "destination_final", "entry_t_s", "last_t_s"])
    entry, last = veh["entry_t_s"].astype(float), veh["last_t_s"].astype(float)
    out = {}
    for r in meta["ramps"]:
        if r["kind"] == "on":
            sel = (veh["origin"] == r["name"]) & (entry >= t0) & (entry < t1)
        else:
            sel = (veh["destination_final"] == r["name"]) & (last >= t0) & (last < t1 - 1.0)
        n = int(sel.sum())
        out[r["name"]] = {"kind": r["kind"], "n_study": n, "veh_h": n * 3600.0 / (t1 - t0)}
    return {"study_s": [t0, t1], "n_collisions": meta.get("n_collisions"), "ramps": out}


def cmd_reduce(a: argparse.Namespace) -> None:
    bat_p = REPO / a.battery
    bat = json.loads(bat_p.read_text())
    sim = bat["simulated"]
    reps = []
    for seed, rd in zip(sim["seeds"], sim["run_dirs"], strict=True):
        d = resolve(rd)
        if d.name != str(seed):
            fail(f"{rd} is not seed {seed}'s run directory")
        reps.append({"seed": seed, "run_dir": rd, **ramp_flows(d)})
    out = {
        "battery": rel(bat_p),
        "battery_sha256": sha(bat_p),
        "scenario": bat["scenario"],
        "config_hash": bat["config_hash"],
        "seeds": sim["seeds"],
        "definition": REDUCE_DEFINITION,
        "replicates": reps,
    }
    (REPO / a.out).write_text(json.dumps(out, indent=1))
    print(f"-> {a.out}: {len(reps)} replicates of {bat['scenario']}")


# --- score -------------------------------------------------------------------------------------------------


def geh(m: float, c: float) -> float:
    from validation.metrics import geh as _geh

    return float(_geh(m, c))


def rmspe_15min(art: dict) -> float:
    """Segment-speed RMSPE after averaging 3 consecutive 5-min windows on both sides (scripts/i24_weighted_target.py)."""
    from validation.metrics import rmspe

    def agg(f: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.array(
                [np.nanmean(f[i * 3 : (i + 1) * 3], axis=0) for i in range(f.shape[0] // 3)]
            )

    s = agg(np.asarray(art["simulated"]["segment_speeds_ms_mean"], float))
    o = agg(np.asarray(art["observed"]["segment_speeds_ms"], float))
    ok = np.isfinite(s) & np.isfinite(o)
    return float(rmspe(s[ok], o[ok]))


def section_index(art: dict, x: float) -> int:
    return [float(v) for v in art["observed"]["sections_m"]].index(x)


def two_hour(rows: Any, i: int) -> float:
    return float(np.mean(np.asarray(rows, float)[i]))


def ramp_targets(cc: dict) -> dict[str, dict[str, float]]:
    """Each ramp's 2-h flow at the pooled recommended coverage, recorded and corrected (veh/h)."""
    c = np.asarray(cc["parameters"]["pooled_coverage_per_window"], float)
    f = 3600.0 / float(cc["parameters"]["window_s"])
    out = {}
    for r in cc["ramps"]:
        n = np.asarray(r["counts_per_window"], float)
        k = np.maximum(n - np.asarray(r[SERIES[r["kind"]]], float), 0.0)
        out[r["name"]] = {
            "kind": r["kind"],
            "recorded_pooled_veh_h": float(np.mean(n * f / c)),
            "corrected_pooled_veh_h": float(np.mean(k * f / c)),
            "corrected_tracked_veh_h": float(np.mean(k * f)),
        }
    return out


def arm_summary(art: dict, flows: dict, targets: dict) -> dict[str, Any]:
    sim = art["simulated"]
    col = art.get("collisions") or {}
    crit = {r["name"]: r for r in art["criteria"]}
    realized = [float(v) for v in sim["demand_realized_fraction"]]
    peaks = {}
    for x in PEAK_SECTIONS_M:
        i = section_index(art, x)
        m = two_hour(sim["hourly_flows_veh_h_mean"], i)
        t = two_hour(art["observed"]["hourly_flows_veh_h_recommended"], i)
        peaks[f"{x:g}"] = {"model_veh_h": m, "target_veh_h": t, "geh": geh(m, t)}
    ramps = {}
    for name, tg in targets.items():
        per = [rep["ramps"][name]["veh_h"] for rep in flows["replicates"]]
        m = float(np.mean(per))
        ramps[name] = {
            "kind": tg["kind"],
            "model_veh_h": m,
            "model_veh_h_ci": ci(per),
            "target_corrected_pooled_veh_h": tg["corrected_pooled_veh_h"],
            "geh_vs_corrected": geh(m, tg["corrected_pooled_veh_h"]),
            "recorded_pooled_veh_h": tg["recorded_pooled_veh_h"],
            "geh_vs_recorded": geh(m, tg["recorded_pooled_veh_h"]),
        }
    return {
        "scenario": art["scenario"],
        "config_hash": art["config_hash"],
        "scenario_file": art.get("scenario_file"),
        "collisions_total": col.get("total"),
        "n_runs": col.get("n_runs"),
        "n_runs_recorded": col.get("n_runs_recorded"),
        "zero_collisions": art.get("zero_collisions"),
        "realized_mean": float(np.mean(realized)),
        "realized_min": float(np.min(realized)),
        "peak_sections": peaks,
        "ramps": ramps,
        "wave_speed_row": {
            k: crit["wave_speed"].get(k) for k in ("value", "passed", "evaluated", "detail")
        },
        "rmspe_5min": float(art["rmspe"]["value"]),
        "rmspe_15min": rmspe_15min(art),
    }


def cmd_score(a: argparse.Namespace) -> None:
    paths = {k: REPO / getattr(a, k) for k in ("ref", "b2", "ref_flows", "b2_flows", "count_check")}
    ref, b2, ref_fl, b2_fl, cc = (json.loads(p.read_text()) for p in paths.values())
    committed = json.loads((REPO / a.committed_ref).read_text()) if a.committed_ref else None
    for fl, art, what in ((ref_fl, ref, "ref"), (b2_fl, b2, "b2")):
        if fl["config_hash"] != art["config_hash"] or fl["seeds"] != art["simulated"]["seeds"]:
            fail(f"the {what} flows are not that battery's (config hash or seeds differ)")
    if (cc.get("checks") or {}).get("reproduces_committed_counts") is not True:
        fail(f"{a.count_check} is void (its counts did not reproduce the committed ones)")
    if (
        ref["observed"]["hourly_flows_veh_h_recommended"]
        != b2["observed"]["hourly_flows_veh_h_recommended"]
    ):
        fail("the two batteries were scored against different observed sides")
    seeds_equal = ref["seeds"] == b2["seeds"]
    step3 = committed["seeds"] if committed else None
    if not seeds_equal or (step3 is not None and ref["seeds"] != step3):
        fail("the arms did not run the same seeds (or not step 3's)")
    targets = ramp_targets(cc)
    r, b = arm_summary(ref, ref_fl, targets), arm_summary(b2, b2_fl, targets)

    by_seed = {s: i for i, s in enumerate(ref["simulated"]["seeds"])}
    j = [by_seed[s] for s in b2["simulated"]["seeds"]]
    paired = {
        "realized": ci(
            [
                float(b2["simulated"]["demand_realized_fraction"][k])
                - float(ref["simulated"]["demand_realized_fraction"][j[k]])
                for k in range(len(j))
            ]
        )
    }
    for x in PEAK_SECTIONS_M:
        i = section_index(ref, x)
        fr = [
            np.mean(np.asarray(c, float)[i]) * 3600.0 / WINDOW_S
            for c in ref["simulated"]["counts_per_replicate"]
        ]
        fb = [
            np.mean(np.asarray(c, float)[i]) * 3600.0 / WINDOW_S
            for c in b2["simulated"]["counts_per_replicate"]
        ]
        paired[f"flow_{x:g}_veh_h"] = ci([fb[k] - fr[j[k]] for k in range(len(j))])

    r1 = (
        bool(b["zero_collisions"])
        and b["collisions_total"] == 0
        and b["n_runs_recorded"] == b["n_runs"]
    )
    r2 = b["realized_mean"] >= r["realized_mean"]
    r3 = all(v["geh_vs_corrected"] < GEH_MAX for v in b["ramps"].values())
    r4 = all(
        b["peak_sections"][k]["geh"] <= r["peak_sections"][k]["geh"] for k in b["peak_sections"]
    )
    ref_wave, b2_wave = bool(r["wave_speed_row"]["passed"]), bool(b["wave_speed_row"]["passed"])
    r5_wave = b2_wave if ref_wave else True
    r5_rmspe = b["rmspe_15min"] <= r["rmspe_15min"] + RMSPE_SLACK
    criteria = {
        "R1": {
            "name": "safety",
            "passed": r1,
            "b2_collisions": b["collisions_total"],
            "ref_collisions": r["collisions_total"],
        },
        "R2": {
            "name": "no winning by backlog",
            "passed": r2,
            "b2_realized_mean": b["realized_mean"],
            "ref_realized_mean": r["realized_mean"],
        },
        "R3": {
            "name": "ramp-lane flows",
            "passed": r3,
            "b2_geh_vs_corrected": {k: v["geh_vs_corrected"] for k, v in b["ramps"].items()},
            "ref_geh_vs_corrected_reported": {
                k: v["geh_vs_corrected"] for k, v in r["ramps"].items()
            },
        },
        "R4": {
            "name": "peak sections (reported either way)",
            "passed": r4,
            "b2_geh": {k: v["geh"] for k, v in b["peak_sections"].items()},
            "ref_geh": {k: v["geh"] for k, v in r["peak_sections"].items()},
        },
        "R5": {
            "name": "emergent waves and speeds",
            "passed": r5_wave and r5_rmspe,
            "wave_verdict": {"ref_passes": ref_wave, "b2_passes": b2_wave, "passed": r5_wave},
            "rmspe_15min": {
                "ref": r["rmspe_15min"],
                "b2": b["rmspe_15min"],
                "bound": r["rmspe_15min"] + RMSPE_SLACK,
                "passed": r5_rmspe,
            },
        },
    }
    repro = None
    if committed is not None:
        same_counts = (
            committed["simulated"]["counts_per_replicate"]
            == ref["simulated"]["counts_per_replicate"]
        )
        same_real = (
            committed["simulated"]["demand_realized_fraction"]
            == ref["simulated"]["demand_realized_fraction"]
        )
        repro = {
            "committed": a.committed_ref,
            "config_hash_equal": committed["config_hash"] == ref["config_hash"],
            "counts_per_replicate_equal": same_counts,
            "demand_realized_fraction_equal": same_real,
        }
    code = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    out = {
        "schema_version": 1,
        "kind": "corridor_round",
        "amendment": "B2 — ramp counts without through traffic",
        "spec": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3 (criteria fixed 2026-10-07 before any corridor run)",
        "status": "PROPOSED, not adopted; adoption is the owner's call and requires R1-R5 to hold",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": code,
        "inputs": {k: {"path": rel(p), "sha256": sha(p)} for k, p in paths.items()},
        "seeds": ref["seeds"],
        "same_seeds_as_step3": step3 is not None and ref["seeds"] == step3,
        "reference_reproduces_committed": repro,
        "criteria": criteria,
        "all_pass": all(c["passed"] for c in criteria.values()),
        "arms": {"reference": r, "b2": b},
        "paired_b2_minus_reference": paired,
        "not_run": "_dc_refit + B1 + B2 (§8.4.3's second arm) is not part of this stage",
        "definitions": {
            "R1": "every B2 replicate records the collision counter and the battery's total is 0 (zero_collisions)",
            "R2": "mean over the 20 replicates of simulated.demand_realized_fraction (departed / planned), B2 >= reference (docs/FRISCO_PROTOCOL.md Amendment 2 clarification)",
            "R3": "per ramp, GEH(modelled 2-h flow, corrected count) < 5 for the B2 arm; modelled flow = mean over replicates of the reduce counts / 2 h; corrected count = mean over the 24 windows of max(counted - flagged, 0) x 12 / the window's pooled recommended coverage (parameters.pooled_coverage_per_window of the count check: the coverage the §8.4.2 section targets stand at; ramp-lane coverage cannot be measured, so ramps take the pooled one, as in §8.4.1)",
            "R4": "at 2,200 and 3,200 m, GEH(2-h mean of simulated.hourly_flows_veh_h_mean, 2-h mean of observed.hourly_flows_veh_h_recommended) of B2 <= the reference's (the pooled targets of §8.4.2)",
            "R5": "wave: the criteria row wave_speed of B2 passes wherever the reference's passes; speeds: 15-min segment-speed RMSPE (3 consecutive 5-min windows averaged on both sides, then compared) of B2 <= the reference's + 0.02",
            "paired": "B2 minus reference per seed, two-sided 95 % t-interval; context, not a criterion",
        },
    }
    (REPO / a.out).write_text(json.dumps(out, indent=1))
    print(
        json.dumps({k: {"passed": v["passed"]} for k, v in criteria.items()}),
        "all_pass",
        out["all_pass"],
    )
    print(f"-> {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("arm")
    p.add_argument("--base", required=True)
    p.add_argument("--rc-inputs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--name", default=NAME)
    p.add_argument("--fit", default=FIT)
    p.add_argument("--ref-scenario", default=REF_SCENARIO)
    p.add_argument("--ref-inputs", default=REF_INPUTS)
    p = sub.add_parser("reduce")
    p.add_argument("--battery", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("score")
    for k in ("--ref", "--b2", "--ref-flows", "--b2-flows", "--out"):
        p.add_argument(k, required=True)
    p.add_argument("--count-check", default=COUNT_CHECK)
    p.add_argument("--committed-ref", default=None)
    a = ap.parse_args()
    {"arm": cmd_arm, "reduce": cmd_reduce, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
