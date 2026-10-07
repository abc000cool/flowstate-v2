"""Round p14 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.5): B1 on top of B2, then the demand re-sequence.

usage (repository root; stage p14_i24_b1b2 of scripts/gcp/pipeline_i24.sh runs both on the VM):
  corridor_b1b2.py evaluate --out artifacts/boundary_b1b2_corridor.json [--resequence {b1b2,b2}]
  corridor_b1b2.py select --readout artifacts/boundary_b1b2_corridor.json

Everything here was fixed in §8.4.5 before any run of the round; nothing is re-thresholded.

``evaluate`` writes the readout. Part (i) always; part (ii) with ``--resequence`` (the arm the stage
refit, which must be the one part (i)'s reading selects). It never reads trajectories: per battery
``artifacts/i24_validation_<label>.json``, every replicate's ``meta.json`` and ``edges.parquet``, the VM-side
braking counts ``runs/i24_validation/<label>/hard_braking.json`` and the ramp-flow reduction
``artifacts/i24_b2_ramp_flows_<label>.json`` (``harness_b2/corridor_b2.py reduce``).

* Part (i), the B1 + B2 arm (label ``p14_b1b2``: the committed B2 arm, hash 909b89f298c5, with
  ``network.boundary.limit_factor`` 1.2185) against B2 alone re-run on the same machine (``p14_b2_ref``).
  A1-A5 are §8.3's, read on this one arm against the B2 reference with ``harness/corridor_b1.py``'s
  estimators unchanged (``battery``, ``evaluate_arm``): A1 0 collisions in every B1 + B2 run and steps below
  -8.9 m/s^2 not above B2's; A2 the 5,400-m 2-h flow within 5,829-6,309 veh/h and the boundary-zone speed
  error smaller in magnitude than B2's; A3 realised demand >= B2's; A4 backward fronts not below B2's by more
  than a third (the wave-verdict half binds only where B2's wave row passes); A5 15-min segment-speed RMSPE
  <= B2's + 0.02. The reading holds when A1-A5 all hold and nothing is recorded under ``problems``: a
  replicate's files missing, a recorded factor or config hash that disagrees, different seeds or not step
  3's, the B2 re-run not reproducing the committed p13 battery exactly (config hash, seeds, per-replicate
  counts, realised fractions, collisions, segment speeds, fronts and every criteria row's value and
  verdict), the rc inputs' boundary zone or coordinates differing from the flow family's, the two batteries
  scored against different observed sides, a filled zone window or straddling cells. Then ``holds`` is
  None. Reported, not gating: peak sections against 6,626 / 6,639 with GEH, both batteries' gate rows,
  5-min RMSPE, ramp flows against the corrected counts, §8.4.3's own R1-R5 reading of this arm against
  the committed ``_dc_refit`` + B1 battery of stage p12 (not re-run here, so not a same-code pairing), the
  figures the write-up quotes beside the criteria (``quoted_figures``: paired per-replicate RMSPEs with and
  without the collapsed seed, that seed, zone speed and peak sections) and which estimator each is
  (``estimators``).
* ``select`` turns part (i)'s reading into the re-sequence's arm by the rule fixed in §8.4.5: exit 0 and
  ``b1b2`` when it holds, 10 and ``b2`` when it does not, 3 when it is undetermined (``holds`` None or no
  readout): then the stage does not run part (ii).
* Part (ii), the re-sequence (``RESEQ``): ``scripts/i24_fit_demand_scale.py`` exactly as stage
  ``p4_i24_refit`` ran it for ``_dc_refit`` (base ``corrected``, the same population, the speed objective,
  the default grid, the fit seed; no ``--min-inserted``, no ``--objective``) on the chosen arm's base, then
  one 20-seed battery of the refit arm. Read against the arm it was refit from (the part-(i) battery of
  that arm): C1 0 collisions in every run; C2 no winning by backlog, the refit's mean realised share at
  least the from-arm's minus 0.01 (FRISCO_PROTOCOL Amendment 2 clarification's one point; the literal >= is
  reported beside); C3 the hourly link-flow GEH < 5 share not below the from-arm's; C4 15-min segment-speed
  RMSPE <= the from-arm's + 0.02; C5 the wave verdict unchanged where the from-arm passes. ``candidate``
  (a candidate for the calibrated family's demand level, never a validation result) is True when C1-C5
  hold and nothing is under ``problems`` (the fit not run as p4 ran it, its base not the from-arm's
  family, the battery not the fit's configuration, a battery problem as in part (i), among them the two
  run on different seeds or scored against different observed sides (every field of ``observed`` but
  ``wall_s``), or an arm other than the one part (i) selects). Reported: every gate row of both, the peak
  sections against 6,626 / 6,639 with GEH, the fit's grid, the hour it was fit on against the held-out
  hour, and what the fitter's insertion constraint (``--min-inserted 0.98``) would have chosen from the
  same recorded runs (a re-analysis, not a calibration).

``evaluate`` exits with status 3 after writing the output when part (i), or the part (ii) it was asked
for, has recorded problems (the stage logs the failure and carries on to ``select``).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cb = _load("p14_corridor_b1", _HERE.parent / "harness" / "corridor_b1.py")
cb2 = _load("p14_corridor_b2", _HERE.parent / "harness_b2" / "corridor_b2.py")

FACTOR = 1.2185  # §7.5 / §8.3, scripts/boundary_limit_factor.py
# B2 alone, re-run here (scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml)
REF_LABEL = "p14_b2_ref"
# B1 + B2 (scenarios/i24_replica_flow_rc_speedcal_dc_refit_b1.yaml, written by the stage)
ARM_LABEL = "p14_b1b2"
B2_HASH = "909b89f298c5"
# expected (computed 2026-10-07 from the committed B2 arm); another hash is reported, not a problem
B1B2_HASH = "e19e5ab64186"
# the committed B2 battery (stage p13) the re-run must reproduce exactly
P13_BATTERY = "artifacts/i24_validation_dc_refit_rc.json"
# step 3's _dc_refit battery: the seeds of record
STEP3_BATTERY = "artifacts/i24_validation_dc_refit.json"
# _dc_refit + B1 (843b3b0c8634), stage p12: the reference of §8.4.3's R1-R5 for its second arm (reported)
P12_B1_BATTERY = "artifacts/i24_validation_p12_dc_refit_b1.json"
FLOW_INPUTS = "artifacts/i24_replica_inputs_flow.json"
RC_INPUTS = "artifacts/i24_replica_inputs_flow_rc.json"
COUNT_CHECK = "artifacts/i24_count_consistency.json"
# stage p4_i24_refit's fit (s = 0.925): the settings part (ii) repeats, and the carried scale
P4_FIT = "artifacts/demand_scale_i24_flow_dc.json"
POP = "artifacts/idm_i24_capacity_amax_k1.0.json"
PEAK_TARGETS = {"2200": 6626, "3200": 6639}  # §8.4.2: the pooled targets stand
A3_TOLERANCE = 0.01  # FRISCO_PROTOCOL Amendment 2 clarification: one percentage point
RMSPE_MARGIN = 0.02
SELECT = {True: (0, "b1b2"), False: (10, "b2")}
SELECT_UNDETERMINED = 3
EXIT_BLOCKED = 3
# observed-block fields that are not the observed side (the seconds the block took to build)
OBSERVED_VOLATILE = ("wall_s",)
# part (ii): what the stage writes for each arm (scripts/gcp/pipeline_i24.sh p14_steps; a test checks the two agree)
RESEQ: dict[str, dict[str, Any]] = {
    "b1b2": {
        "from_label": ARM_LABEL,
        "base": "scenarios/i24_replica_flow_rc_corrected_dc_b1.yaml",
        "fit": "artifacts/demand_scale_i24_flow_rc_b1.json",
        "scenario": "scenarios/i24_replica_flow_rc_speedcal_dc_refit2_b1.yaml",
        "label": "p14_refit2_b1b2",
        "factor": FACTOR,
    },
    "b2": {
        "from_label": REF_LABEL,
        "base": "scenarios/i24_replica_flow_rc_corrected_dc.yaml",
        "fit": "artifacts/demand_scale_i24_flow_rc.json",
        "scenario": "scenarios/i24_replica_flow_rc_speedcal_dc_refit2.yaml",
        "label": "p14_refit2_b2",
        "factor": None,
    },
}


# --------------------------------------------------------------------------- helpers


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(path: str) -> dict[str, Any] | None:
    p = REPO / path
    return json.loads(p.read_text()) if p.is_file() else None


def artifact(label: str) -> str:
    return f"artifacts/i24_validation_{label}.json"


def _fitmod() -> ModuleType:
    """scripts/i24_fit_demand_scale.py (the fitter itself: its grid, rule and scaled_config)."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import i24_fit_demand_scale

    return i24_fit_demand_scale


def _canon(x: Any) -> str:
    """A field as canonical JSON (NaN as ``NaN``), so equality is exact and NaN-safe."""
    return json.dumps(x, sort_keys=True)


def observed_differs(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """The ``observed`` fields two battery artifacts disagree on (exact, NaN-safe), ``wall_s`` aside.

    ``hourly_flows_veh_h_recommended`` first (the field part (i) compares), then every other field of
    the block in name order; ``OBSERVED_VOLATILE`` (the build time) is not the observed side.
    """
    oa, ob = a["observed"], b["observed"]
    first = "hourly_flows_veh_h_recommended"
    keys = [first, *sorted((set(oa) | set(ob)) - {first} - set(OBSERVED_VOLATILE))]
    return [k for k in keys if _canon(oa.get(k)) != _canon(ob.get(k))]


def gate_rows(art: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {k: r.get(k) for k in ("name", "value", "passed", "evaluated", "threshold")}
        for r in art["criteria"]
    ]


def reproduces(here: dict[str, Any], committed_path: str) -> dict[str, Any]:
    """The B2 re-run against the committed p13 battery, field by field (exact, NaN-safe)."""
    c = load(committed_path)
    if c is None:
        return {
            "committed": committed_path,
            "available": False,
            "exact": False,
            "differs": ["missing"],
        }
    hs, cs = here["simulated"], c["simulated"]

    def rows(a: dict[str, Any]) -> list[tuple[Any, ...]]:
        return [
            (r["name"], r.get("value"), r.get("passed"), r.get("evaluated")) for r in a["criteria"]
        ]

    checks = {
        "config_hash": here["config_hash"] == c["config_hash"],
        "seeds": _canon(here["seeds"]) == _canon(c["seeds"]),
        "counts_per_replicate": _canon(hs["counts_per_replicate"])
        == _canon(cs["counts_per_replicate"]),
        "demand_realized_fraction": _canon(hs["demand_realized_fraction"])
        == _canon(cs["demand_realized_fraction"]),
        "n_collisions_per_replicate": _canon(hs.get("n_collisions_per_replicate"))
        == _canon(cs.get("n_collisions_per_replicate")),
        "segment_speeds_ms_per_replicate": _canon(hs["segment_speeds_ms_per_replicate"])
        == _canon(cs["segment_speeds_ms_per_replicate"]),
        "backward_fronts": _canon(
            [
                [w["n_backward"] for w in hs[k]]
                for k in ("waves_per_replicate", "waves_stripe_per_replicate")
            ]
        )
        == _canon(
            [
                [w["n_backward"] for w in cs[k]]
                for k in ("waves_per_replicate", "waves_stripe_per_replicate")
            ]
        ),
        "criteria_rows": _canon(rows(here)) == _canon(rows(c)),
    }
    differs = [k for k, ok in checks.items() if not ok]
    return {
        "committed": {"path": committed_path, "sha256": sha(REPO / committed_path)},
        "available": True,
        "checks": checks,
        "exact": not differs,
        "differs": differs,
    }


def inputs_problems() -> list[str]:
    """The zone and coordinates A2 reads come from the flow family's inputs; the rc family's must be the same."""
    flow, rc = load(FLOW_INPUTS), load(RC_INPUTS)
    if flow is None or rc is None:
        return [f"{FLOW_INPUTS} or {RC_INPUTS} is missing"]
    out = []
    for what, f in (
        ("geometry.sim_x_of_data_x", lambda d: d["geometry"]["sim_x_of_data_x"]),
        ("boundary.x_range_m", lambda d: d["boundary"]["x_range_m"]),
        ("boundary.window_s", lambda d: d["boundary"]["window_s"]),
        ("boundary.schedule_data_time", lambda d: d["boundary"].get("schedule_data_time")),
    ):
        if not cb2.same_values(f(flow), f(rc)):
            out.append(
                f"{RC_INPUTS} {what} differs from {FLOW_INPUTS}'s (A2's zone is the flow family's)"
            )
    return out


def ramp_reading(label: str, art: dict[str, Any], targets: dict[str, Any] | None) -> dict[str, Any]:
    """Each ramp's modelled 2-h flow (corridor_b2.py reduce) against its corrected and recorded counts (reported)."""
    path = f"artifacts/i24_b2_ramp_flows_{label}.json"
    fl = load(path)
    if targets is None:
        return {"computed": False, "why": f"{COUNT_CHECK} missing"}
    if fl is None:
        return {"computed": False, "why": f"{path} missing"}
    if fl["config_hash"] != art["config_hash"] or [int(s) for s in fl["seeds"]] != [
        int(s) for s in art["simulated"]["seeds"]
    ]:
        return {
            "computed": False,
            "why": f"{path} is not this battery's (config hash or seeds differ)",
        }
    out: dict[str, Any] = {"computed": True, "reduction": path}
    for name, tg in targets.items():
        per = [rep["ramps"][name]["veh_h"] for rep in fl["replicates"]]
        m = float(np.mean(per))
        out[name] = {
            "kind": tg["kind"],
            "model_veh_h": cb.ci(per),
            "corrected_pooled_veh_h": tg["corrected_pooled_veh_h"],
            "geh_vs_corrected": cb2.geh(m, tg["corrected_pooled_veh_h"]),
            "recorded_pooled_veh_h": tg["recorded_pooled_veh_h"],
            "geh_vs_recorded": cb2.geh(m, tg["recorded_pooled_veh_h"]),
        }
    return out


def _targets() -> dict[str, Any] | None:
    cc = load(COUNT_CHECK)
    return None if cc is None else cb2.ramp_targets(cc)


def _two_hour_peaks(art: dict[str, Any]) -> dict[str, dict[str, float]]:
    """§8.4.4's R4 quantities: 2-h means of the simulated and observed hourly flows at the peak sections."""
    out = {}
    for x in cb2.PEAK_SECTIONS_M:
        i = cb2.section_index(art, x)
        m = cb2.two_hour(art["simulated"]["hourly_flows_veh_h_mean"], i)
        t = cb2.two_hour(art["observed"]["hourly_flows_veh_h_recommended"], i)
        out[f"{x:g}"] = {"model_veh_h": m, "target_veh_h": t, "geh": cb2.geh(m, t)}
    return out


def r_reading_second_arm(
    arm: dict[str, Any], ref: dict[str, Any] | None, targets: dict[str, Any] | None
) -> dict[str, Any]:
    """§8.4.3's R1-R5 for its second arm (_dc_refit + B1 + B2) against the committed _dc_refit + B1 battery.

    Reported, not gating: the reference is stage p12's battery, not re-run on this code tree (stage
    p12, p13 and this stage's B2 check reproduced committed batteries exactly across code trees and
    machines, but a same-code pairing is what §8.4.3 names). R3 reads the arm's ramps only (§8.4.3:
    the reference's R3 is reported, and p12 archived no vehicles.parquet).
    """
    if ref is None:
        return {"computed": False, "why": f"{P12_B1_BATTERY} missing"}
    sim = arm["simulated"]
    coll = [int(x) for x in sim.get("n_collisions_per_replicate") or []]
    real_a = float(np.mean(sim["demand_realized_fraction"]))
    real_r = float(np.mean(ref["simulated"]["demand_realized_fraction"]))
    pa, pr = _two_hour_peaks(arm), _two_hour_peaks(ref)
    ramps = ramp_reading(ARM_LABEL, arm, targets)
    r3 = (
        None
        if not ramps.get("computed")
        else all(
            v["geh_vs_corrected"] < cb2.GEH_MAX for k, v in ramps.items() if isinstance(v, dict)
        )
    )
    wave = {r["name"]: r for r in arm["criteria"]}["wave_speed"]
    wave_ref = {r["name"]: r for r in ref["criteria"]}["wave_speed"]
    r5_wave = bool(wave["passed"]) if wave_ref.get("passed") else None
    rm_a, rm_r = cb2.rmspe_15min(arm), cb2.rmspe_15min(ref)
    crit = {
        "R1": bool(arm.get("zero_collisions")) and bool(coll) and all(c == 0 for c in coll),
        "R2": real_a >= real_r,
        "R3": r3,
        "R4": all(pa[k]["geh"] <= pr[k]["geh"] for k in pa),
        "R5": (r5_wave is not False) and rm_a <= rm_r + cb2.RMSPE_SLACK,
    }
    return {
        "computed": True,
        "status": "reported, not gating: the reference is the committed stage-p12 battery, not re-run here",
        "reference": {"path": P12_B1_BATTERY, "sha256": sha(REPO / P12_B1_BATTERY)},
        "same_seeds": [int(s) for s in arm["seeds"]] == [int(s) for s in ref["seeds"]],
        "criteria": crit,
        "all_hold": None if any(v is None for v in crit.values()) else all(crit.values()),
        "realized_mean": {"arm": real_a, "reference": real_r},
        "peak_sections": {"arm": pa, "reference": pr},
        "rmspe_15min": {"arm": rm_a, "reference": rm_r, "bound": rm_r + cb2.RMSPE_SLACK},
        "wave_row": {
            "arm_passes": bool(wave["passed"]),
            "reference_passes": bool(wave_ref.get("passed")),
        },
        "ramps_r3": ramps,
        "r3_reference_half": "not computed: stage p12 archived no vehicles.parquet",
    }


def _strip(bt: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bt.items() if not k.startswith("_") and k != "zone_per_window_ms"}


def _rel_targets(bt: dict[str, Any]) -> dict[str, Any]:
    """The peak sections' observed 2-h targets as the battery carries them, and whether they are §8.4.2's."""
    got = {k: bt["observed_2h_veh_h"][k] for k in PEAK_TARGETS}
    return {
        "observed": got,
        "as_recorded": all(round(got[k]) == v for k, v in PEAK_TARGETS.items()),
    }


# --------------------------------------------------------------------------- part (i)

ESTIMATORS = {
    "gated": "A5's values (and part (ii)'s C4's) are RMSPEs of the replicate-mean field: the replicates' "
    "mean 5-min segment-speed field, in 15-min windows, against the observed field (harness/corridor_b1.py "
    "battery 'rmspe_15min'); a battery's 'rmspe.value' (reported.rmspe_5min) is the same estimator in 5-min "
    "windows",
    "intervals": "a bracketed [mean, lo95, hi95] of RMSPEs is a 95% t-interval (harness/corridor_b1.py ci, "
    "paired) of the per-seed differences of per-replicate RMSPEs (each replicate's own field against the "
    "observed one: 'rmspe_15min_per_replicate', or the battery's 'rmspe.per_replicate_vs_observed' in 5-min "
    "windows), B1 + B2 minus B2 at the same seed; its mean is not the difference of the gated values (the "
    "RMSPE of a mean field is not the mean of the replicates' RMSPEs)",
}


def quoted_figures(ref: dict[str, Any], arm: dict[str, Any]) -> dict[str, Any]:
    """The part-(i) figures the §8.4.5 write-up quotes beside the criteria, traced to the batteries.

    The collapsed seed is the B1 + B2 replicate with the lowest realised demand share; "without" drops
    that seed from both batteries (the pairs stay seed by seed). Every [mean, lo95, hi95] is
    ``cb.ci`` / ``cb.paired`` (95% t-interval over seeds), paired values B1 + B2 minus B2. A
    replicate's mean segment speed is the unweighted mean of its 5-min segment-speed field
    (``segment_speeds_ms_per_replicate``, NaN cells skipped). Reported, not gating.
    """
    seeds = arm["seeds"]
    if not seeds or seeds != ref["seeds"]:
        return {"computed": False, "why": "the two batteries ran different seeds (or none)"}
    i = int(np.argmin(arm["realised"]))
    keep = [k for k in range(len(seeds)) if k != i]

    def drop(xs: list[Any]) -> list[Any]:
        return [xs[k] for k in keep]

    def seg_mean(bt: dict[str, Any]) -> list[float]:
        fields = bt["_art"]["simulated"]["segment_speeds_ms_per_replicate"]
        return [float(np.nanmean(np.asarray(f, float))) for f in fields]

    def kmh(v: list[float] | None) -> list[float] | None:
        return None if v is None else [x * 3.6 for x in v]

    def largest(a: list[float], b: list[float]) -> bool:
        return int(np.argmax([x - y for x, y in zip(a, b, strict=True)])) == i

    msa, msr = seg_mean(arm), seg_mean(ref)
    r15a, r15r = arm["rmspe_15min_per_replicate"], ref["rmspe_15min_per_replicate"]
    r5a = (arm["_art"].get("rmspe") or {}).get("per_replicate_vs_observed")
    r5r = (ref["_art"].get("rmspe") or {}).get("per_replicate_vs_observed")
    r5_ok = r5a is not None and r5r is not None and len(r5a) == len(r5r) == len(seeds)
    rm5: dict[str, Any] = {
        "source": "each battery's rmspe.per_replicate_vs_observed (one seed's 5-min field against the "
        "observed one, as scripts/i24_validate.py records it)",
    }
    big5 = None
    if r5_ok:
        assert r5a is not None and r5r is not None
        rm5 |= {
            "all_seeds": cb.paired(r5a, r5r),
            "without_collapsed_seed": cb.paired(drop(r5a), drop(r5r)),
        }
        big5 = largest(r5a, r5r)
    else:
        rm5 |= {"computed": False, "why": "a battery records no per-replicate 5-min RMSPE per seed"}
    return {
        "computed": True,
        "what": "figures the §8.4.5 write-up quotes beside the criteria, from the batteries read above; "
        "reported, not gating; see estimators",
        "collapsed_seed": {
            "rule": "the B1 + B2 replicate with the lowest realised demand share",
            "seed": seeds[i],
            "realised_b1b2": arm["realised"][i],
            "realised_b2": ref["realised"][i],
            "mean_segment_speed_ms_b1b2": msa[i],
            "mean_segment_speed_ms_b2": msr[i],
            "also_largest_paired_rmspe_5min": big5,
            "also_largest_paired_rmspe_15min": largest(r15a, r15r),
            "also_lowest_mean_segment_speed_b1b2": int(np.argmin(msa)) == i,
        },
        "other_seeds": {
            "n": len(keep),
            "realised_b1b2": cb.ci(drop(arm["realised"])),
            "realised_b2": cb.ci(drop(ref["realised"])),
            "realised_paired": cb.paired(drop(arm["realised"]), drop(ref["realised"])),
            "mean_segment_speed_ms_b1b2": cb.ci(drop(msa)),
            "mean_segment_speed_ms_b2": cb.ci(drop(msr)),
        },
        "rmspe_5min_per_replicate_paired": rm5,
        "rmspe_15min_per_replicate_paired": {
            "source": "rmspe_15min_per_replicate (harness/corridor_b1.py battery); all_seeds is criteria.A5's "
            "per_replicate_paired_b1_minus_ref",
            "all_seeds": cb.paired(r15a, r15r),
            "without_collapsed_seed": cb.paired(drop(r15a), drop(r15r)),
        },
        "zone_speed_paired_kmh": {
            "source": "zone_speed_ms (A2's estimator); all_seeds is criteria.A2.zone's "
            "paired_zone_speed_b1_minus_ref_kmh",
            "all_seeds": kmh(cb.paired(arm["zone_speed_ms"], ref["zone_speed_ms"])),
        },
        "peak_sections_paired_veh_h": {
            "source": "flows_2h_veh_h; as reported.peak_sections' paired_b1_minus_ref",
            "all_seeds": {
                f"{s:.0f}": cb.paired(
                    arm["flows_2h_veh_h"][f"{s:.0f}"], ref["flows_2h_veh_h"][f"{s:.0f}"]
                )
                for s in cb.PEAK_SECTIONS_M
            },
        },
    }


def part_i(runs_root: Path, zone: dict[str, Any]) -> dict[str, Any]:
    missing = [lb for lb in (REF_LABEL, ARM_LABEL) if not (REPO / artifact(lb)).is_file()]
    if missing:
        return {
            "missing": missing,
            "problems": [f"no battery artifact {artifact(lb)}" for lb in missing],
            "reading": {"holds": None, "why": "a battery is missing"},
        }
    ref = cb.battery(REF_LABEL, runs_root, zone, None)
    arm = cb.battery(ARM_LABEL, runs_root, zone, FACTOR)
    ev = cb.evaluate_arm("b1b2_vs_b2", ref, arm, REPO / P13_BATTERY)
    ref_art, arm_art = ref["_art"], arm["_art"]
    problems = list(ev["problems"]) + inputs_problems()
    repro = reproduces(ref_art, P13_BATTERY)
    if not repro["exact"]:
        problems.append(
            f"the B2 re-run ({REF_LABEL}) does not reproduce the committed battery {P13_BATTERY} "
            f"exactly: {', '.join(repro['differs'])}"
        )
    step3 = load(STEP3_BATTERY)
    same_step3 = step3 is not None and [int(s) for s in ref_art["seeds"]] == [
        int(s) for s in step3["seeds"]
    ]
    if not same_step3:
        problems.append("the batteries did not run step 3's seeds")
    if _canon(ref_art["observed"]["hourly_flows_veh_h_recommended"]) != _canon(
        arm_art["observed"]["hourly_flows_veh_h_recommended"]
    ):
        problems.append("the two batteries were scored against different observed sides")
    crit = ev["criteria"]
    verdicts = {k: crit[k]["verdict"] for k in ("A1", "A2", "A3", "A4", "A5")}
    holds = (
        None if problems or any(v is None for v in verdicts.values()) else all(verdicts.values())
    )
    targets = _targets()
    return {
        "arm": {
            "label": ARM_LABEL,
            "scenario": arm_art.get("scenario"),
            "config_hash": arm_art["config_hash"],
        },
        "reference": {
            "label": REF_LABEL,
            "scenario": ref_art.get("scenario"),
            "config_hash": ref_art["config_hash"],
        },
        "keys": "criteria and reported as harness/corridor_b1.py evaluate_arm writes them: 'reference' is B2 "
        "alone, 'b1' is B1 + B2",
        "expected_hashes": {
            "b2": {"expected": B2_HASH, "matches": ref_art["config_hash"] == B2_HASH},
            "b1b2": {"expected": B1B2_HASH, "matches": arm_art["config_hash"] == B1B2_HASH},
        },
        "same_seeds_as_step3": same_step3,
        "reference_reproduces_p13": repro,
        "criteria": crit,
        "reported": {
            **ev["reported"],
            "gate_rows": {"b2": gate_rows(ref_art), "b1b2": gate_rows(arm_art)},
            "peak_targets": _rel_targets(arm),
            "ramp_flows": {
                "b2": ramp_reading(REF_LABEL, ref_art, targets),
                "b1b2": ramp_reading(ARM_LABEL, arm_art, targets),
            },
            "r1_r5_second_arm": r_reading_second_arm(arm_art, load(P12_B1_BATTERY), targets),
            "quoted_figures": quoted_figures(ref, arm),
            "estimators": ESTIMATORS,
        },
        "batteries": {"b2": _strip(ref), "b1b2": _strip(arm)},
        "problems": problems,
        "reading": {
            **verdicts,
            "holds": holds,
            "rule": "A1-A5 all hold on B1 + B2 against B2 alone, and nothing recorded under problems",
        },
    }


# --------------------------------------------------------------------------- part (ii)


def fit_checks(fit: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Whether the fit was run as stage p4_i24_refit ran it for _dc_refit (scripts/i24_fit_demand_scale.py)."""
    fm = _fitmod()
    p4 = load(P4_FIT) or {}
    rows = fit.get("grid") or []
    scales = {round(float(r["scale"]), 3) for r in rows}
    coarse_scales = {round(float(s), 3) for s in fm.COARSE["corrected"]}
    coarse = [r for r in rows if round(float(r["scale"]), 3) in coarse_scales]
    procedure = False
    if rows and len(coarse) == len(coarse_scales):
        centre = fm._best(coarse)["scale"]
        procedure = scales == coarse_scales | {round(s, 3) for s in fm.refine_scales(centre)}
    best_rule = fm._best(rows)["scale"] if rows else None
    checks = {
        "base_corrected": fit.get("base") == "corrected",
        "base_scenario": fit.get("base_scenario") == plan["base"],
        "fleet_artifact": fit.get("fleet_artifact") == POP == p4.get("fleet_artifact"),
        "seed_as_p4": fit.get("seed") is not None and fit.get("seed") == p4.get("seed"),
        "objective_as_p4": fit.get("objective") is not None
        and fit.get("objective") == p4.get("objective"),
        "no_selection_options": "selection" not in fit,
        "grid_procedure": procedure,
        "choice_is_the_rule": best_rule is not None and fit["best"]["scale"] == best_rule,
    }
    return {
        "checks": checks,
        "as_p4": all(checks.values()),
        "differs": [k for k, v in checks.items() if not v],
    }


def base_reproduces_from_arm(base: str, from_hash: str, from_name: str) -> dict[str, Any]:
    """The refit's base at the carried scale (_dc_refit's 0.925, p4's fit) is the from-arm's configuration."""
    from flowstate_core.config import ScenarioConfig, config_hash

    fm = _fitmod()
    p4 = load(P4_FIT)
    if p4 is None or not (REPO / base).is_file():
        return {"checked": False, "ok": False, "why": f"{P4_FIT} or {base} missing"}
    s = float(p4["best"]["scale"])
    doc = fm.scaled_config(s, POP, "corrected", (REPO / base).resolve(), from_name)
    h = config_hash(ScenarioConfig.model_validate(doc))
    return {
        "checked": True,
        "carried_scale": s,
        "recipe_hash": h,
        "from_hash": from_hash,
        "ok": h == from_hash,
    }


def part_ii(
    choice: str, p1: dict[str, Any], runs_root: Path, zone: dict[str, Any]
) -> dict[str, Any]:
    plan = RESEQ[choice]
    holds = p1["reading"]["holds"]
    selected = None if holds is None else SELECT[bool(holds)][1]
    out: dict[str, Any] = {"arm": choice, "selected_by_part_i": selected, "plan": plan}
    problems: list[str] = []
    if selected != choice:
        problems.append(
            f"the re-sequence ran on {choice}, but part (i)'s reading selects {selected}"
        )
    fit = load(plan["fit"])
    missing = [
        p
        for p in (plan["fit"], artifact(plan["label"]), artifact(plan["from_label"]))
        if not (REPO / p).is_file()
    ]
    if missing:
        out.update(problems=problems + [f"missing {m}" for m in missing], candidate=None)
        return out
    assert fit is not None
    fc = fit_checks(fit, plan)
    if not fc["as_p4"]:
        problems.append(f"the fit was not run as p4_i24_refit ran it: {', '.join(fc['differs'])}")
    refit = cb.battery(plan["label"], runs_root, zone, plan["factor"])
    frm = cb.battery(plan["from_label"], runs_root, zone, plan["factor"])
    ra, fa = refit["_art"], frm["_art"]
    bc = base_reproduces_from_arm(plan["base"], fa["config_hash"], fa["scenario"])
    if not bc["ok"]:
        problems.append(
            f"the refit's base at the carried scale is not the from-arm's configuration: {bc}"
        )
    if ra["config_hash"] != fit["best"]["config_hash"]:
        problems.append(
            f"the refit battery ran {ra['config_hash']}, the fit chose {fit['best']['config_hash']}"
        )
    if refit["seeds"] != frm["seeds"]:
        problems.append("the refit and the from-arm ran different seeds")
    # as part (i): both scored against the same observed side (C3 and the peaks read its hourly flows,
    # C4 its segment speeds); every field of the block but the time it took to build
    obs_differs = observed_differs(ra, fa)
    if obs_differs:
        problems.append(
            "the refit and the from-arm were scored against different observed sides: "
            + ", ".join(obs_differs)
        )
    problems += refit["problems"] + frm["problems"]

    coll = refit["collisions"]
    c1 = (
        bool(ra.get("zero_collisions"))
        and len(coll) == len(refit["seeds"])
        and all(c == 0 for c in coll)
    )
    real_r, real_f = cb.ci(refit["realised"]), cb.ci(frm["realised"])
    assert real_r is not None and real_f is not None
    c2 = real_r[0] >= real_f[0] - A3_TOLERANCE
    c3 = float(refit["geh_lt5_share"]) >= float(frm["geh_lt5_share"])
    c4 = refit["rmspe_15min"] <= frm["rmspe_15min"] + RMSPE_MARGIN
    from_wave = bool(frm["wave_row"]["passed"])
    c5 = bool(refit["wave_row"]["passed"]) if from_wave else None
    crit = {
        "C1": {"rule": "0 collisions in every run", "collisions": coll, "verdict": c1},
        "C2": {
            "rule": f"mean realised share >= the from-arm's - {A3_TOLERANCE} (FRISCO_PROTOCOL Amendment 2 "
            "clarification: no winning by backlog)",
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
    holds_c = bool(c1 and c2 and c3 and c4 and c5 is not False)
    candidate = None if problems else holds_c
    peaks = {}
    for k in ("2200", "3200", "5400"):
        mr, mf = cb.ci(refit["flows_2h_veh_h"][k]), cb.ci(frm["flows_2h_veh_h"][k])
        obs = refit["observed_2h_veh_h"][k]
        assert mr is not None and mf is not None
        peaks[k] = {
            "observed": obs,
            "refit": mr,
            "from_arm": mf,
            "paired_refit_minus_from": cb.paired(
                refit["flows_2h_veh_h"][k], frm["flows_2h_veh_h"][k]
            ),
            "geh_refit": cb.geh(mr[0], obs),
            "geh_from_arm": cb.geh(mf[0], obs),
        }
    fm = _fitmod()
    rows = fit["grid"]
    obs_hourly = fm.observed_hourly_flows() if all("counts_per_window" in r for r in rows) else None
    reanalysis = fm.analyze_fit(fit, [0.98], "speed_rmspe", obs_hourly)
    carried = float((load(P4_FIT) or {}).get("best", {}).get("scale", math.nan))
    same_level = fit["best"]["scale"] == carried
    same_level_repro = None
    if same_level:
        same_level_repro = _canon(ra["simulated"]["counts_per_replicate"]) == _canon(
            fa["simulated"]["counts_per_replicate"]
        )
        if not same_level_repro:
            problems.append(
                "the fit returned the carried scale, but the refit battery does not reproduce the from-arm's counts"
            )
            candidate = None
    out.update(
        {
            "fit": {
                "path": plan["fit"],
                "sha256": sha(REPO / plan["fit"]),
                "checks": fc,
                "base_check": bc,
                "chosen_scale": fit["best"]["scale"],
                "carried_scale": carried,
                "level_unchanged": same_level,
                "level_unchanged_reproduces_from_arm": same_level_repro,
                "chosen": fit["best"],
                "grid": [
                    {
                        k: r.get(k)
                        for k in (
                            "scale",
                            "inserted_fraction",
                            "rmspe_train",
                            "rmspe_test",
                            "rmspe_all",
                        )
                    }
                    for r in sorted(rows, key=lambda r: r["scale"])
                ],
                "insertion_reanalysis_reported": {
                    "what": "what the fitter's --min-inserted 0.98 rule would choose from the same recorded runs "
                    "(scripts/i24_fit_demand_scale.py analyze_fit): a re-analysis, not a calibration; the "
                    "stage ran the fit as p4 did",
                    "by_threshold": reanalysis["by_threshold"],
                },
            },
            "refit_battery": {
                "label": plan["label"],
                "scenario": ra.get("scenario"),
                "config_hash": ra["config_hash"],
            },
            "from_battery": {
                "label": plan["from_label"],
                "scenario": fa.get("scenario"),
                "config_hash": fa["config_hash"],
            },
            "criteria": crit,
            "c1_c5_hold": holds_c,
            "candidate": candidate,
            "problems": problems,
            "reported": {
                "gate_rows": {"refit": gate_rows(ra), "from_arm": gate_rows(fa)},
                "rmspe_5min": {"refit": refit["rmspe_5min"], "from_arm": frm["rmspe_5min"]},
                "sections_2h": peaks,
                "peak_targets": _rel_targets(refit),
                "a2_band_5400": {
                    "band": list(cb.A2_BAND),
                    "refit_in_band": cb.A2_BAND[0] <= peaks["5400"]["refit"][0] <= cb.A2_BAND[1],
                },
                "fronts": {
                    det: {
                        "refit": cb.ci(refit[f"fronts_{det}"]),
                        "from_arm": cb.ci(frm[f"fronts_{det}"]),
                    }
                    for det in ("standard", "stripe")
                },
                "hard_braking_below_8p9": {
                    "refit": None
                    if refit["emergency_steps"] is None
                    else sum(refit["emergency_steps"]),
                    "from_arm": None
                    if frm["emergency_steps"] is None
                    else sum(frm["emergency_steps"]),
                },
                "zone_speed_kmh": {
                    "schedule": refit["schedule_mean_ms"] * 3.6,
                    "refit": None
                    if any(v is None for v in refit["zone_speed_ms"])
                    else [x * 3.6 for x in cb.ci(refit["zone_speed_ms"]) or []],
                    "from_arm": None
                    if any(v is None for v in frm["zone_speed_ms"])
                    else [x * 3.6 for x in cb.ci(frm["zone_speed_ms"]) or []],
                },
                "ramp_flows": ramp_reading(plan["label"], ra, _targets()),
            },
            "batteries": {"refit": _strip(refit), "from_arm": _strip(frm)},
            "not_validation": "a demand refit on the recording it is scored on (fit hour 06:30-07:30 inside the "
            "scored window; one morning, no holdout day): calibration, never validation",
        }
    )
    return out


# --------------------------------------------------------------------------- commands


SOURCE_COMMIT_ENV = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE = ".source_commit"


def _code() -> dict[str, str | None]:
    """``code``, the source commit of the readout's code, and ``vm_snapshot``, this checkout's HEAD.

    On the VM the checkout is a ``git archive`` of the source commit committed afresh
    (scripts/gcp/vm_setup.sh), so its HEAD (``vm_snapshot``) names no commit of the repository. The
    source commit is ``$FLOWSTATE_SOURCE_COMMIT``, else the commit written in ``.source_commit`` at the
    repository root; without either, ``code`` is the HEAD as before (a local checkout's own commit).
    """
    head = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=30
        )
        head = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    source = os.environ.get(SOURCE_COMMIT_ENV, "").strip() or None
    f = REPO / SOURCE_COMMIT_FILE
    if source is None and f.is_file():
        source = next(iter(f.read_text().split()), None)
    return {"code": source or head, "vm_snapshot": head}


def evaluate(out: Path, runs_root: Path, resequence: str | None) -> dict[str, Any]:
    zone = cb.zone_geometry()
    p1 = part_i(runs_root, zone)
    p2: dict[str, Any] = (
        part_ii(resequence, p1, runs_root, zone)
        if resequence is not None
        else {"status": "not in this readout (evaluate without --resequence)"}
    )
    doc = {
        "schema": "flowstate.boundary_b1b2_corridor/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.5 (criteria fixed 2026-10-07 before any run of the round)",
        "status": "PROPOSED, not adopted; adoption of B1, of the refit level, or of either family is the owner's call",
        "limit_factor_applied": FACTOR,
        "zone": zone,
        "part_i": p1,
        "part_ii_rule": "the re-sequence runs on B1 + B2 when part (i)'s reading holds, on B2 alone when it does "
        "not, and not at all when it is undetermined (holds None)",
        "part_ii": p2,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    return doc


def cmd_select(readout: Path) -> int:
    if not readout.is_file():
        print(f"undetermined: no readout {readout}")
        return SELECT_UNDETERMINED
    holds = json.loads(readout.read_text())["part_i"]["reading"]["holds"]
    if holds is None:
        print("undetermined: part (i)'s reading is blocked or not computed")
        return SELECT_UNDETERMINED
    code, arm = SELECT[bool(holds)]
    print(arm)
    return code


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, required=True)
    ev.add_argument("--runs-root", type=Path, default=None)
    ev.add_argument("--resequence", choices=tuple(RESEQ), default=None)
    se = sub.add_parser("select")
    se.add_argument("--readout", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "select":
        p = args.readout if args.readout.is_absolute() else REPO / args.readout
        sys.exit(cmd_select(p))
    out = args.out if args.out.is_absolute() else REPO / args.out
    runs_root = args.runs_root or REPO / "runs" / "i24_validation"
    doc = evaluate(out, runs_root, args.resequence)
    p1, p2 = doc["part_i"], doc["part_ii"]
    print("part (i):", {k: p1["reading"].get(k) for k in ("A1", "A2", "A3", "A4", "A5", "holds")})
    if "candidate" in p2:
        print(
            "part (ii):",
            p2["arm"],
            {k: v["verdict"] for k, v in (p2.get("criteria") or {}).items()},
            "candidate:",
            p2["candidate"],
        )
    blocked = list(p1.get("problems") or []) + list(p2.get("problems") or [])
    if blocked:
        for p in blocked:
            print(f"PROBLEM: {p}", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)


if __name__ == "__main__":
    main()
