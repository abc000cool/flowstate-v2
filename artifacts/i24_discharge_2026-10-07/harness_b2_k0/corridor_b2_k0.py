"""Follow-up 1 of decision A1: amendment B2 on the k = 0 I-24 arm (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7).

usage (repository root; stage p25_i24_b2_k0, artifacts/i24_discharge_2026-10-07/stage_p25_b2_k0.sh.txt, runs ``arm``,
``check-ref`` and ``score`` on the VM; ``expected`` runs locally at $0):
  corridor_b2_k0.py expected [--out artifacts/i24_discharge_2026-10-07/b2_k0_expected.json]
  corridor_b2_k0.py arm [--out scenarios/i24_replica_flow_rc_speedcal.yaml]
  corridor_b2_k0.py check-ref [--battery artifacts/i24_validation_flow_speedcal_p25ref.json]
  corridor_b2_k0.py score [--out artifacts/boundary_b2_k0_corridor.json]

Everything here was fixed in §8.4.7 before any run of the round; nothing is re-thresholded. The criteria are
R1-R5 of §8.4.3, read by stage p13's ``harness_b2/corridor_b2.py score`` (its functions are imported, its
arithmetic is ported line for line into :func:`p13_reading`, and a test checks that the port reproduces p13's
committed readout ``artifacts/boundary_b2_corridor.json`` from p13's own inputs), with the wave half binding
(docs/DECISIONS_2026-10-07.md §A1, §5 item 1; docs/FRISCO_PROTOCOL.md Amendment 5, follow-up 1).

* ``expected`` derives, from committed files only, the arm as the stage must write it, its hashes, the planned
  demand change per ramp, R3's targets, the reference's committed readings that R2, R4 and R5 are read against,
  the planned supply past the peak sections, B6's S1 rows and p13's k = 1 reading beside them; nothing is run.
* ``arm`` (VM) writes ``scenarios/i24_replica_flow_rc_speedcal.yaml``: the k = 0 arm's recipe
  (``scripts/i24_fit_demand_scale.py`` ``scaled_config`` at s = 0.800, ``best.scale`` of
  ``artifacts/demand_scale_i24_flow.json``, fleet ``artifacts/idm_i24_capacity.json``) on the committed ``_rc``
  family's corrected arm ``scenarios/i24_replica_flow_rc_corrected.yaml``, which stage p13 built with
  ``scripts/i24_build_replica.py --suffix flow_rc --osm corrected --lc-strategic 5 --lc-strategic-ramp 1
  --entry-lanes observed_flow --ramp-through-traffic exclude --count-consistency artifacts/i24_count_consistency.json``.
  It refuses (exit 2), writing nothing, unless (1) the recipe on the flow family's corrected arm reproduces the
  committed k = 0 arm ``scenarios/i24_replica_flow_speedcal.yaml`` (raw YAML, as p13 required of ``_dc_refit``)
  and that file is the one the committed battery ran (policy-3 hash ae5861a4d906); (2) the committed ``_rc`` inputs
  record mode ``exclude`` on the committed count check (sha256) and equal the flow family's inputs in everything B2
  does not touch (p13's comparison); (3) the committed ``_rc`` corrected arm is the one those inputs record
  (``corrected_config_hash``); (4) the arm differs from the k = 0 arm only in its name and the four ramps' values
  (p13's ``same_configuration``); (5) those values are the builder's arithmetic on the corrected counts, and the
  k = 0 arm's on the recorded counts, to the digit (p13's ``expected_ramp_values``); (6) the arm is the configuration
  pre-registered in §8.4.7 (policy-3 hash fa9eb07d549b; policy 4: 9030f087e0af).
* ``check-ref`` (VM, right after the reference battery) requires the same-code re-run of the k = 0 arm to reproduce
  the committed step-3 battery ``artifacts/i24_validation_flow_speedcal_ref.json`` exactly: the scenario named by
  both hashes (today's policy and 3), the seeds, per-replicate counts, realised fractions, collisions, segment
  speeds, standard and stripe fronts, every criteria row's value and verdict but ``no_locks`` (not recorded in
  step 3, recorded now) and the observed side but its build time. Exit 0 when it does, 3 when not (the stage then
  runs nothing more).
* ``score`` writes the readout: p13's R1-R5 of the B2 arm against the same-code reference on the same 20 seeds, the
  wave half binding, the reproduction row, the pairing over seeds, and (reported, not gating) the braking counts,
  locks, the coordinator's breakdown reading, both batteries' gate rows and p13's k = 1 reading side by side. A
  recorded problem leaves the reading undetermined (``holds`` None) and the exit status 3.

It never reads trajectories: per battery ``artifacts/i24_validation_<label>.json``, the ramp-flow reduction
``artifacts/i24_b2_ramp_flows_<label>.json`` (``harness_b2/corridor_b2.py reduce``, VM) and the braking counts
``artifacts/i24_hard_braking_<label>.json`` (``harness/hard_braking.py``, VM, before the trajectories are pruned).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO / "scripts"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


#: stage p13's harness: its arm guards, its reduction and its R1-R5 arithmetic
cb2 = _load(
    "b2k0_corridor_b2",
    REPO / "artifacts" / "i24_discharge_2026-10-07" / "harness_b2" / "corridor_b2.py",
)

# --- the round's fixed inputs (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7) ---------------------------
#: the k = 0 arm's demand fit (stage build_flow, 2026-09-17): best.scale 0.800 on the corrected base
FIT = "artifacts/demand_scale_i24_flow.json"
SCALE = 0.8
POP = "artifacts/idm_i24_capacity.json"
FLOW_BASE = "scenarios/i24_replica_flow_corrected.yaml"
#: the k = 0 arm (FRISCO_PROTOCOL Amendment 2's reference arm; p12's "canonical" arm)
REF_SCENARIO = "scenarios/i24_replica_flow_speedcal.yaml"
REF_HASH_V3 = "ae5861a4d906"
#: the committed _rc family (stage p13): inputs, demand and corrected arm, built with B2's exclusion
RC_BASE = "scenarios/i24_replica_flow_rc_corrected.yaml"
REF_INPUTS = "artifacts/i24_replica_inputs_flow.json"
RC_INPUTS = "artifacts/i24_replica_inputs_flow_rc.json"
COUNT_CHECK = "artifacts/i24_count_consistency.json"
COUNT_CHECK_SHA256 = "ea403bcf1b90b446688d2406c373ac0be6ec77944e385d2557985eedf81ab2a0"
#: the arm, pre-registered in §8.4.7 (computed 2026-10-08 from the committed files above)
ARM = "scenarios/i24_replica_flow_rc_speedcal.yaml"
NAME = "i24_replica_flow_rc_speedcal"
ARM_HASH_V3 = "fa9eb07d549b"
ARM_HASH_V4 = "9030f087e0af"
#: the committed step-3 battery of the k = 0 arm (stage p4_i24_ref; Amendment 2's reference; p12 reproduced it)
COMMITTED_REF = "artifacts/i24_validation_flow_speedcal_ref.json"
REF_LABEL = "flow_speedcal_p25ref"
ARM_LABEL = "flow_speedcal_rc"
#: p13's readout of B2 on the k = 1 arm (_dc_refit), read beside this one
P13_READOUT = "artifacts/boundary_b2_corridor.json"
B6_SCREEN = "artifacts/driver_joint_screen_i24.json"
OH_PROBE = "artifacts/i24_merge_experiment_ohlevel.json"
EXPECTED = "artifacts/i24_discharge_2026-10-07/b2_k0_expected.json"
OUT = "artifacts/boundary_b2_k0_corridor.json"
STAGE = "p25_i24_b2_k0"

PEAK_SECTIONS_M = cb2.PEAK_SECTIONS_M
WAVE_BAND_KMH = (14.0, 22.0)
#: the coordinator's breakdown reading (docs/PRE_FRISCO_PROGRAM.md, decisions of 2026-10-07): reported only
BREAKDOWN_REPLICATE_SHARE = 0.9
BREAKDOWN_BATTERY_MEAN = 0.95
#: FRISCO_PROTOCOL Amendment 2's clarification (one point), reported beside R2's literal reading
R2_TOLERANCE_REPORTED = 0.01
EMERGENCY = "-8.9"
OBSERVED_VOLATILE = ("wall_s",)
EXIT_REFUSED = 2
EXIT_BLOCKED = 3

#: The code a record rests on (``_code``): its sha256 is recorded, and a path that differs from the commit
#: named in ``code`` is listed under ``code_dirty_paths``.
CODE_PATHS = (
    "artifacts/i24_discharge_2026-10-07/harness_b2_k0/corridor_b2_k0.py",
    "artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py",
    "artifacts/i24_discharge_2026-10-07/harness/hard_braking.py",
    "scripts/i24_fit_demand_scale.py",
    "scripts/i24_validate.py",
    "packages/flowstate_core/flowstate_core/config.py",
)


class Refused(Exception):
    """An input is not what §8.4.7 names; nothing is written."""


# --- small helpers ------------------------------------------------------------------------------


def _path(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def rel(p: str | Path) -> str:
    q = _path(p).resolve()
    return str(q.relative_to(REPO)) if q.is_relative_to(REPO) else str(q)


def sha(p: str | Path) -> str:
    return hashlib.sha256(_path(p).read_bytes()).hexdigest()


def load_json(path: str | Path) -> Any:
    return json.loads(_path(path).read_text())


def load_yaml(path: str | Path) -> dict[str, Any]:
    doc = yaml.safe_load(_path(path).read_text())
    if not isinstance(doc, dict):
        raise Refused(f"{path}: not a scenario document")
    return doc


def _canon(x: Any) -> str:
    """A field as canonical JSON (NaN as ``NaN``), so equality is exact and NaN-safe."""
    return json.dumps(x, sort_keys=True)


def current_hash(doc: Mapping[str, Any]) -> str:
    from flowstate_core.config import ScenarioConfig, config_hash

    return config_hash(ScenarioConfig.model_validate(copy.deepcopy(dict(doc))))


def v3_hash(doc: Mapping[str, Any]) -> str:
    from flowstate_core.config import config_hash_v3

    return str(config_hash_v3(copy.deepcopy(dict(doc))))


def v2_hash(doc: Mapping[str, Any]) -> str:
    from flowstate_core.config import config_hash_v2

    return str(config_hash_v2(copy.deepcopy(dict(doc))))


def hash_policy(recorded: str | None, doc: Mapping[str, Any]) -> str | None:
    """The hash policy under which ``recorded`` is ``doc``'s config hash: ``current``, ``v3`` or None.

    The committed step-3 battery quotes a policy-3 hash (ae5861a4d906); a re-run under this checkout's
    policy quotes today's. I-24 has no weaving section, so Amendment 4's defaults do not act on these
    scenarios and policy 4 moved their hashes by the version alone (docs/CONTRACTS.md §2).
    """
    if recorded is None:
        return None
    if recorded == current_hash(doc):
        return "current"
    if recorded == v3_hash(doc):
        return "v3"
    return None


def _code() -> dict[str, Any]:
    """``code`` (``$FLOWSTATE_SOURCE_COMMIT``, else ``.source_commit``, else HEAD; None when ``CODE_PATHS``
    differ from HEAD), ``vm_snapshot`` (this checkout's HEAD), ``code_dirty_paths``, ``code_sha256`` and
    ``config_hash_policy`` (the policy every config hash here is computed under)."""
    from flowstate_core.config import CONFIG_HASH_VERSION

    head = None
    dirty: list[str] | None = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=30
        )
        head = r.stdout.strip() or None
        st = subprocess.run(
            ["git", "status", "--porcelain", "--", *CODE_PATHS],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if st.returncode == 0:
            dirty = sorted(line[3:] for line in st.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    source = os.environ.get("FLOWSTATE_SOURCE_COMMIT", "").strip() or None
    f = REPO / ".source_commit"
    if source is None and f.is_file():
        source = next(iter(f.read_text().split()), None)
    return {
        "code": None if dirty else source or head,
        "vm_snapshot": head,
        "code_dirty_paths": dirty,
        "code_sha256": {c: sha(c) for c in CODE_PATHS if (REPO / c).is_file()},
        "config_hash_policy": int(CONFIG_HASH_VERSION),
    }


def step_mean(steps: Sequence[Sequence[float]], t0: float, t1: float) -> float:
    """Time mean over [t0, t1) of a piecewise-constant schedule ``[[t, value], ...]`` (the last step to t1)."""
    tot = 0.0
    for k, (t, val) in enumerate(steps):
        lo = max(float(t), t0)
        hi = min(float(steps[k + 1][0]) if k + 1 < len(steps) else t1, t1)
        if hi > lo:
            tot += float(val) * (hi - lo)
    return tot / (t1 - t0)


def planned(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The planned demand over the study period [warmup_s, duration_s): mainline and on-ramp inflows as veh/h,
    off-ramp exit fractions as their time mean; vehicles planned over the period (mainline + on-ramps)."""
    sim = doc["sim"]
    t0, t1 = float(sim["warmup_s"]), float(sim["duration_s"])
    hours = (t1 - t0) / 3600.0
    main = step_mean(doc["network"]["inflow"], t0, t1) * 3600.0
    ramps: dict[str, dict[str, Any]] = {}
    vehicles = main * hours
    for r in doc["network"]["ramps"]:
        if r["kind"] == "on":
            q = step_mean(r["inflow"], t0, t1) * 3600.0
            ramps[r["name"]] = {"kind": "on", "veh_h": q}
            vehicles += q * hours
        else:
            ramps[r["name"]] = {
                "kind": "off",
                "exit_fraction_mean": step_mean(r["exit_fraction"], t0, t1),
            }
    return {
        "study_s": [t0, t1],
        "mainline_veh_h": main,
        "ramps": ramps,
        "vehicles_study_period": vehicles,
    }


# --- the arm, derived from committed files -------------------------------------------------------


def inputs_differ(ref_in: Mapping[str, Any], rc_in: Mapping[str, Any]) -> list[str]:
    """Stage p13's comparison (``corridor_b2.py arm``): the fields B2 does not touch that differ."""
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
    return [k for k, (x, y) in same.items() if not cb2.same_values(x, y)]


def ramp_windows_changed(doc: Mapping[str, Any], ref_doc: Mapping[str, Any]) -> dict[str, int]:
    """Per ramp, how many of its 5-min values differ between the two documents."""
    a, b = cb2.ramp_values(doc), cb2.ramp_values(ref_doc)
    return {n: sum(x != y for x, y in zip(a[n], b[n], strict=True)) for n in a}


def derive(rc_base: str | Path = RC_BASE) -> dict[str, Any]:
    """The arm as §8.4.7 fixes it, from committed files only; raises :class:`Refused` on any failed check."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    from i24_fit_demand_scale import scaled_config

    fit = load_json(FIT)
    s, fleet, kind = float(fit["best"]["scale"]), fit["fleet_artifact"], fit["base"]
    if (s, fleet, kind, fit["base_scenario"]) != (SCALE, POP, "corrected", FLOW_BASE):
        raise Refused(
            f"{FIT} is not the k = 0 fit §8.4.7 names (scale {s}, fleet {fleet}, base {kind} "
            f"{fit['base_scenario']})"
        )
    ref_doc = load_yaml(REF_SCENARIO)
    again = scaled_config(s, fleet, kind, _path(FLOW_BASE).resolve(), ref_doc["name"])
    if again != ref_doc:
        raise Refused(
            f"{REF_SCENARIO} is not {FIT}'s scale on {FLOW_BASE}: the recipe does not reproduce it"
        )
    if v3_hash(ref_doc) != REF_HASH_V3:
        raise Refused(f"{REF_SCENARIO} has policy-3 hash {v3_hash(ref_doc)}, not {REF_HASH_V3}")

    ref_in, rc_in = load_json(REF_INPUTS), load_json(RC_INPUTS)
    block = rc_in.get("ramp_through_traffic") or {}
    if block.get("mode") != "exclude":
        raise Refused(f"{RC_INPUTS} was not built with --ramp-through-traffic exclude")
    if block.get("artifact") != COUNT_CHECK or block.get("artifact_sha256") != COUNT_CHECK_SHA256:
        raise Refused(f"{RC_INPUTS} does not record the committed count check {COUNT_CHECK}")
    if sha(COUNT_CHECK) != COUNT_CHECK_SHA256:
        raise Refused(f"{COUNT_CHECK} is not the count check the rc family was built on (sha256)")
    differ = inputs_differ(ref_in, rc_in)
    if differ:
        raise Refused(
            f"{RC_INPUTS} differs from {REF_INPUTS} beyond the ramp correction: {', '.join(differ)}"
        )
    base_doc = load_yaml(rc_base)
    if v3_hash(base_doc) != rc_in["corrected_config_hash"]:
        raise Refused(
            f"{rel(rc_base)} (policy-3 hash {v3_hash(base_doc)}) is not the corrected arm {RC_INPUTS} records "
            f"({rc_in['corrected_config_hash']})"
        )

    doc = scaled_config(s, fleet, kind, _path(rc_base).resolve(), NAME)
    if not cb2.same_configuration(doc, ref_doc):
        raise Refused(f"the arm differs from {REF_SCENARIO} beyond its name and the ramp values")
    checks = {
        "reference ramp values": (
            cb2.ramp_values(ref_doc),
            cb2.expected_ramp_values(ref_in, s, corrected=False),
        ),
        "B2 ramp values": (
            cb2.ramp_values(doc),
            cb2.expected_ramp_values(rc_in, s, corrected=True),
        ),
    }
    for what, (got, want) in checks.items():
        if got != want:
            bad = [n for n in want if got.get(n) != want[n]]
            raise Refused(f"{what} are not the builder's arithmetic for {', '.join(bad)}")
    if v3_hash(doc) != ARM_HASH_V3:
        raise Refused(
            f"the arm's policy-3 hash is {v3_hash(doc)}, not the pre-registered {ARM_HASH_V3} (§8.4.7)"
        )
    return {
        "doc": doc,
        "ref_doc": ref_doc,
        "base_doc": base_doc,
        "scale": s,
        "fleet": fleet,
        "ref_inputs": ref_in,
        "rc_inputs": rc_in,
        "ramp_windows_changed": ramp_windows_changed(doc, ref_doc),
    }


# --- expected (local, $0) ------------------------------------------------------------------------


def _peak_readings(art: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """2-h model flow, target and GEH at the peak sections, as p13's ``arm_summary`` reads them."""
    out = {}
    for x in PEAK_SECTIONS_M:
        i = cb2.section_index(art, x)
        m = cb2.two_hour(art["simulated"]["hourly_flows_veh_h_mean"], i)
        t = cb2.two_hour(art["observed"]["hourly_flows_veh_h_recommended"], i)
        out[f"{x:g}"] = {"model_veh_h": m, "target_veh_h": t, "geh": cb2.geh(m, t)}
    return out


def _fronts(art: Mapping[str, Any]) -> dict[str, float]:
    sim = art["simulated"]
    return {
        "standard": float(np.mean([w["n_backward"] for w in sim["waves_per_replicate"]])),
        "stripe": float(np.mean([w["n_backward"] for w in sim["waves_stripe_per_replicate"]])),
    }


def _wave_row(art: Mapping[str, Any]) -> dict[str, Any]:
    row = next(r for r in art["criteria"] if r["name"] == "wave_speed")
    return {k: row.get(k) for k in ("value", "passed", "evaluated")}


def gate_rows(art: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {k: r.get(k) for k in ("name", "value", "passed", "evaluated", "threshold")}
        for r in art["criteria"]
    ]


def expected_summary() -> dict[str, Any]:
    """Every pre-run number of §8.4.7, from committed files; nothing is simulated."""
    d = derive()
    doc, ref_doc, base_doc = d["doc"], d["ref_doc"], d["base_doc"]
    cc = load_json(COUNT_CHECK)
    targets = cb2.ramp_targets(cc)
    p_ref, p_arm = planned(ref_doc), planned(doc)
    ramps = {}
    for n, r in p_ref["ramps"].items():
        a = p_arm["ramps"][n]
        key = "veh_h" if r["kind"] == "on" else "exit_fraction_mean"
        row: dict[str, Any] = {
            "kind": r["kind"],
            "unit": "veh/h" if r["kind"] == "on" else "mean exit fraction",
            "reference": r[key],
            "arm": a[key],
            "change": a[key] - r[key],
            "change_rel": a[key] / r[key] - 1.0,
            "windows_changed": d["ramp_windows_changed"][n],
            "r3_target_corrected_pooled_veh_h": targets[n]["corrected_pooled_veh_h"],
            "r3_recorded_pooled_veh_h": targets[n]["recorded_pooled_veh_h"],
        }
        if r["kind"] == "on":
            # an on-ramp's planned flow is what the model is asked to insert: its GEH to the target is
            # where R3 starts before any insertion loss (context, not a criterion)
            row["planned_geh_vs_corrected"] = {
                "reference": cb2.geh(r[key], targets[n]["corrected_pooled_veh_h"]),
                "arm": cb2.geh(a[key], targets[n]["corrected_pooled_veh_h"]),
            }
        ramps[n] = row

    rc_in = d["rc_inputs"]
    upstream = {
        f"{x:g}": [
            r["name"]
            for r in rc_in["ramps"]
            if r["kind"] == "on" and float(r["count_x_m"]) < float(x)
        ]
        for x in PEAK_SECTIONS_M
    }
    off_upstream = [
        r["name"]
        for r in rc_in["ramps"]
        if r["kind"] == "off" and float(r["count_x_m"]) < max(PEAK_SECTIONS_M)
    ]
    committed = load_json(COMMITTED_REF)
    peaks = _peak_readings(committed)
    supply = {}
    for x, names in upstream.items():
        s_ref = p_ref["mainline_veh_h"] + sum(p_ref["ramps"][n]["veh_h"] for n in names)
        s_arm = p_arm["mainline_veh_h"] + sum(p_arm["ramps"][n]["veh_h"] for n in names)
        m_ref = peaks[x]["model_veh_h"]
        supply[x] = {
            "on_ramps_upstream": names,
            "reference_planned_veh_h": s_ref,
            "arm_planned_veh_h": s_arm,
            "reference_model_veh_h": m_ref,
            "reference_share_of_planned": m_ref / s_ref,
            "arm_share_needed_for_r4": m_ref / s_arm,
            "arm_geh_if_every_planned_vehicle_passes": cb2.geh(s_arm, peaks[x]["target_veh_h"]),
            "reference_geh": peaks[x]["geh"],
        }
    sim = committed["simulated"]
    realized = [float(r) for r in sim["demand_realized_fraction"]]
    r15 = cb2.rmspe_15min(committed)

    p13 = load_json(P13_READOUT)
    b6 = load_json(B6_SCREEN)
    b6_rows = {
        f"k{r['k']:g}_j{r['j']:g}": {
            k: r[k]
            for k in (
                "a_max_mean_ms2",
                "T_mean_s",
                "population",
                "capacity_density_veh_km",
                "band_lo_veh_km",
                "capacity_density_minus_band_lo_veh_km",
                "criterion_at_capacity_density_s2",
                "unstable_at_capacity_density",
            )
        }
        for r in b6["rows"]
        if r["j"] == 0 and r["k"] in (0.0, 1.0)
    }
    probe = load_json(OH_PROBE)
    probe_rows = {
        v["variant"]: {
            "config_hash": v["config_hash"],
            "inserted_fraction": v["inserted_fraction"],
            "rmspe_15min": v["rmspe_15min"],
            "segment_mean_kmh": v["segment_mean_kmh"],
            "peak_sections_veh_h": v["hourly_flow_mean_by_section"][1:3],
        }
        for v in probe["variants"]
        if v["variant"] in ("flow_speedcal", "flow_speedcal_oh0.75")
    }
    return {
        "schema": "flowstate.b2_k0_expected/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7 (pre-registered 2026-10-08, not run)",
        "status": "computed from committed files only; nothing simulated",
        "arm": {
            "scenario": ARM,
            "name": NAME,
            "config_hash": current_hash(doc),
            "config_hash_v3": v3_hash(doc),
            "recipe": f"scripts/i24_fit_demand_scale.py scaled_config(s = {d['scale']}, fleet {d['fleet']}, "
            f"base corrected) on {RC_BASE}",
            "base": {
                "scenario": RC_BASE,
                "sha256": sha(RC_BASE),
                "config_hash": current_hash(base_doc),
                "config_hash_v3": v3_hash(base_doc),
            },
            "differs_from_reference_in": {
                "name": [ref_doc["name"], doc["name"]],
                "ramp_windows_changed": d["ramp_windows_changed"],
                "anything_else": False,
            },
        },
        "reference": {
            "scenario": REF_SCENARIO,
            "sha256": sha(REF_SCENARIO),
            "config_hash": current_hash(ref_doc),
            "config_hash_v3": v3_hash(ref_doc),
            "config_hash_v2": v2_hash(ref_doc),
            "committed_battery": {"path": COMMITTED_REF, "sha256": sha(COMMITTED_REF)},
            "committed_battery_config_hash": committed["config_hash"],
            "seeds": committed["seeds"],
        },
        "planned": {
            "study_s": p_ref["study_s"],
            "mainline_veh_h": {
                "reference": p_ref["mainline_veh_h"],
                "arm": p_arm["mainline_veh_h"],
            },
            "vehicles_study_period": {
                "reference": p_ref["vehicles_study_period"],
                "arm": p_arm["vehicles_study_period"],
                "change": p_arm["vehicles_study_period"] - p_ref["vehicles_study_period"],
            },
            "ramps": ramps,
        },
        "reference_committed_readings": {
            "R1_collisions": (committed.get("collisions") or {}).get("total"),
            "R2_realized": {
                "mean": float(np.mean(realized)),
                "min": min(realized),
                "max": max(realized),
            },
            "R4_peak_sections": peaks,
            "R5_rmspe_15min": {"reference": r15, "bound": r15 + cb2.RMSPE_SLACK},
            "R5_wave_row": _wave_row(committed),
            "backward_fronts_per_replicate": _fronts(committed),
            "gate_rows": gate_rows(committed),
        },
        "peak_supply": {
            "definition": "planned 2-h flow past each peak section: mainline plus the on-ramps whose count "
            "section lies upstream of it (no off-ramp lies upstream of either section); R4 holds iff the arm's "
            "2-h flow at both sections is not below the reference's (GEH is monotone below the targets)",
            "off_ramps_upstream": off_upstream,
            "sections": supply,
        },
        "k1_p13": {
            "readout": {"path": P13_READOUT, "sha256": sha(P13_READOUT)},
            "criteria": p13["criteria"],
            "all_pass": p13["all_pass"],
        },
        "b6_s1": {"artifact": {"path": B6_SCREEN, "sha256": sha(B6_SCREEN)}, "rows": b6_rows},
        "old_hickory_probe": {
            "artifact": {"path": OH_PROBE, "sha256": sha(OH_PROBE)},
            "note": "docs/I24_VALIDATION.md §0.11: one seed, the k = 0 arm with Old Hickory x 1 and x 0.75 "
            "(code of 2026-09-17); context for the prediction, not a criterion",
            "seed": probe["seed"],
            "observed_segment_mean_kmh": probe["observed_segment_mean_kmh"],
            "variants": probe_rows,
        },
    }


def cmd_expected(a: argparse.Namespace) -> int:
    doc = expected_summary()
    out = _path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {rel(out)}: arm {doc['arm']['config_hash']} (v3 {doc['arm']['config_hash_v3']})")
    return 0


# --- arm (VM) ------------------------------------------------------------------------------------


def cmd_arm(a: argparse.Namespace) -> int:
    try:
        d = derive(a.base)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    doc = d["doc"]
    h, h3 = current_hash(doc), v3_hash(doc)
    block = d["rc_inputs"]["ramp_through_traffic"]
    lines = [
        f"# {NAME}: amendment B2 on the k = 0 I-24 arm (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7; A1's follow-up 1,",
        "#   pre-registered 2026-10-08): the k = 0 arm's recipe on the committed _rc family. scripts/i24_fit_demand_scale.py",
        f"#   scaled_config at s = {d['scale']:.3f} (best.scale of {FIT}, sha256 {sha(FIT)[:12]}…; carried, not refit:",
        "#   mainline and on-ramp inflows x s, exit fractions and the boundary as built), fleet",
        f"#   {d['fleet']} (k = 0), on {rel(a.base)} (sha256 {sha(a.base)[:12]}…), the corrected arm of",
        "#   scripts/i24_build_replica.py --suffix flow_rc --osm corrected --lc-strategic 5 --lc-strategic-ramp 1",
        f"#   --entry-lanes observed_flow --ramp-through-traffic exclude ({block['artifact']}, sha256",
        f"#   {block['artifact_sha256'][:12]}…; stage p13).",
        f"#   Checked when written: the recipe reproduces {REF_SCENARIO} (policy-3 hash {REF_HASH_V3}); this",
        "#   file differs from it only in its name and the four ramps' inflow / exit-fraction values, which are the",
        f"#   builder's arithmetic on the corrected counts of {RC_INPUTS}.",
        f"#   Written by {rel(Path(__file__))} arm (stage {STAGE}); config hash {h} (policy-3 {h3});",
        "#   seeded=False.",
    ]
    out = _path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n" + yaml.safe_dump(doc, sort_keys=False))
    back = load_yaml(out)
    if back != doc or v3_hash(back) != ARM_HASH_V3:
        out.unlink()
        print(f"refused: {rel(out)} does not read back as the derived arm", file=sys.stderr)
        return EXIT_REFUSED
    print(f"-> {rel(out)} ({h}; policy-3 {h3}); reference {REF_SCENARIO}, scale {d['scale']:.3f}")
    return 0


# --- check-ref (VM) ------------------------------------------------------------------------------


def observed_differs(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """The ``observed`` fields two battery artifacts disagree on (exact, NaN-safe), ``wall_s`` aside."""
    oa, ob = a["observed"], b["observed"]
    keys = sorted((set(oa) | set(ob)) - set(OBSERVED_VOLATILE))
    return [k for k in keys if _canon(oa.get(k)) != _canon(ob.get(k))]


def _rows_but_locks(art: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (r["name"], r.get("value"), r.get("passed"), r.get("evaluated"))
        for r in art["criteria"]
        if r["name"] != "no_locks"
    ]


def _fronts_per_replicate(sim: Mapping[str, Any]) -> list[list[Any]]:
    return [
        [w["n_backward"] for w in sim[k]]
        for k in ("waves_per_replicate", "waves_stripe_per_replicate")
    ]


def reproduces(
    here: Mapping[str, Any], committed: Mapping[str, Any], ref_doc: Mapping[str, Any]
) -> dict[str, Any]:
    """The same-code re-run of the k = 0 arm against the committed step-3 battery, field by field."""
    hs, cs = here["simulated"], committed["simulated"]
    checks = {
        "config_hash": hash_policy(here.get("config_hash"), ref_doc) is not None
        and hash_policy(committed.get("config_hash"), ref_doc) is not None,
        "scenario": here.get("scenario") == committed.get("scenario"),
        "seeds": _canon(here["seeds"]) == _canon(committed["seeds"]),
        "counts_per_replicate": _canon(hs["counts_per_replicate"])
        == _canon(cs["counts_per_replicate"]),
        "demand_realized_fraction": _canon(hs["demand_realized_fraction"])
        == _canon(cs["demand_realized_fraction"]),
        "n_collisions_per_replicate": _canon(hs.get("n_collisions_per_replicate"))
        == _canon(cs.get("n_collisions_per_replicate")),
        "segment_speeds_ms_per_replicate": _canon(hs["segment_speeds_ms_per_replicate"])
        == _canon(cs["segment_speeds_ms_per_replicate"]),
        "backward_fronts": _canon(_fronts_per_replicate(hs)) == _canon(_fronts_per_replicate(cs)),
        "criteria_rows_but_no_locks": _canon(_rows_but_locks(here))
        == _canon(_rows_but_locks(committed)),
        "observed": not observed_differs(here, committed),
    }
    differs = [k for k, ok in checks.items() if not ok]
    return {
        "checks": checks,
        "exact": not differs,
        "differs": differs,
        "observed_fields_differing": observed_differs(here, committed),
        "here_config_hash": here.get("config_hash"),
        "here_hash_policy": hash_policy(here.get("config_hash"), ref_doc),
        "committed_config_hash": committed.get("config_hash"),
        "committed_hash_policy": hash_policy(committed.get("config_hash"), ref_doc),
        "no_locks_status": "not recorded in step 3, recorded here: reported, not part of the reproduction",
    }


def cmd_check_ref(a: argparse.Namespace) -> int:
    p = _path(a.battery)
    if not p.is_file():
        print(f"blocked: no battery at {a.battery}", file=sys.stderr)
        return EXIT_BLOCKED
    rep = reproduces(load_json(p), load_json(a.committed), load_yaml(REF_SCENARIO))
    print(json.dumps({"exact": rep["exact"], "differs": rep["differs"]}))
    return 0 if rep["exact"] else EXIT_BLOCKED


# --- score ---------------------------------------------------------------------------------------


def p13_reading(
    ref: Mapping[str, Any],
    b2: Mapping[str, Any],
    ref_fl: Mapping[str, Any],
    b2_fl: Mapping[str, Any],
    cc: Mapping[str, Any],
    committed: Mapping[str, Any] | None,
    committed_path: str | None,
) -> dict[str, Any]:
    """R1-R5 exactly as stage p13's ``corridor_b2.py score`` reads them (its arithmetic, line for line).

    p13's refusals (exit 2) are returned under ``problems`` instead; nothing else differs. The keys and values of
    ``criteria``, ``arms``, ``paired_b2_minus_reference``, ``reference_reproduces_committed`` and ``all_pass``
    are p13's (a test reproduces p13's committed readout from p13's inputs with this function).
    """
    problems: list[str] = []
    for fl, art, what in ((ref_fl, ref, "ref"), (b2_fl, b2, "b2")):
        if fl["config_hash"] != art["config_hash"] or fl["seeds"] != art["simulated"]["seeds"]:
            problems.append(
                f"the {what} flows are not that battery's (config hash or seeds differ)"
            )
    if (cc.get("checks") or {}).get("reproduces_committed_counts") is not True:
        problems.append("the count check is void (its counts did not reproduce the committed ones)")
    if (
        ref["observed"]["hourly_flows_veh_h_recommended"]
        != b2["observed"]["hourly_flows_veh_h_recommended"]
    ):
        problems.append("the two batteries were scored against different observed sides")
    seeds_equal = ref["seeds"] == b2["seeds"]
    step3 = committed["seeds"] if committed else None
    if not seeds_equal or (step3 is not None and ref["seeds"] != step3):
        problems.append("the arms did not run the same seeds (or not step 3's)")
    targets = cb2.ramp_targets(cc)
    r, b = cb2.arm_summary(ref, ref_fl, targets), cb2.arm_summary(b2, b2_fl, targets)

    by_seed = {s: i for i, s in enumerate(ref["simulated"]["seeds"])}
    paired: dict[str, Any] = {}
    if sorted(by_seed) != sorted(b2["simulated"]["seeds"]):
        # p13 would raise here (KeyError): the replicates cannot be paired; the reading is blocked
        problems.append("the batteries' replicate seeds differ: the arms cannot be paired")
    else:
        j = [by_seed[s] for s in b2["simulated"]["seeds"]]
        paired["realized"] = cb2.ci(
            [
                float(b2["simulated"]["demand_realized_fraction"][k])
                - float(ref["simulated"]["demand_realized_fraction"][j[k]])
                for k in range(len(j))
            ]
        )
        for x in PEAK_SECTIONS_M:
            i = cb2.section_index(ref, x)
            fr = [
                np.mean(np.asarray(c, float)[i]) * 3600.0 / cb2.WINDOW_S
                for c in ref["simulated"]["counts_per_replicate"]
            ]
            fb = [
                np.mean(np.asarray(c, float)[i]) * 3600.0 / cb2.WINDOW_S
                for c in b2["simulated"]["counts_per_replicate"]
            ]
            paired[f"flow_{x:g}_veh_h"] = cb2.ci([fb[k] - fr[j[k]] for k in range(len(j))])

    r1 = (
        bool(b["zero_collisions"])
        and b["collisions_total"] == 0
        and b["n_runs_recorded"] == b["n_runs"]
    )
    r2 = b["realized_mean"] >= r["realized_mean"]
    r3 = all(v["geh_vs_corrected"] < cb2.GEH_MAX for v in b["ramps"].values())
    r4 = all(
        b["peak_sections"][k]["geh"] <= r["peak_sections"][k]["geh"] for k in b["peak_sections"]
    )
    ref_wave, b2_wave = bool(r["wave_speed_row"]["passed"]), bool(b["wave_speed_row"]["passed"])
    r5_wave = b2_wave if ref_wave else True
    r5_rmspe = b["rmspe_15min"] <= r["rmspe_15min"] + cb2.RMSPE_SLACK
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
                "bound": r["rmspe_15min"] + cb2.RMSPE_SLACK,
                "passed": r5_rmspe,
            },
        },
    }
    repro = None
    if committed is not None:
        repro = {
            "committed": committed_path,
            "config_hash_equal": committed["config_hash"] == ref["config_hash"],
            "counts_per_replicate_equal": committed["simulated"]["counts_per_replicate"]
            == ref["simulated"]["counts_per_replicate"],
            "demand_realized_fraction_equal": committed["simulated"]["demand_realized_fraction"]
            == ref["simulated"]["demand_realized_fraction"],
        }
    return {
        "problems": problems,
        "seeds": ref["seeds"],
        "same_seeds_as_step3": step3 is not None and ref["seeds"] == step3,
        "reference_reproduces_committed": repro,
        "criteria": criteria,
        "all_pass": all(c["passed"] for c in criteria.values()),
        "arms": {"reference": r, "b2": b},
        "paired_b2_minus_reference": paired,
    }


def _rmspe_15min_field(field: Any, obs: Any) -> float:
    """``corridor_b2.rmspe_15min`` on one replicate's segment-speed field (context, not a criterion)."""
    from validation.metrics import rmspe

    def agg(f: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.array(
                [np.nanmean(f[i * 3 : (i + 1) * 3], axis=0) for i in range(f.shape[0] // 3)]
            )

    s, o = agg(np.asarray(field, float)), agg(np.asarray(obs, float))
    ok = np.isfinite(s) & np.isfinite(o)
    return float(rmspe(s[ok], o[ok]))


def paired_context(ref: Mapping[str, Any], b2: Mapping[str, Any]) -> dict[str, Any]:
    """Per-seed B2 minus reference beyond p13's pairing: fronts and per-replicate 15-min RMSPE (reported)."""
    rs, bs = ref["simulated"], b2["simulated"]
    by_seed = {s: i for i, s in enumerate(rs["seeds"])}
    if sorted(by_seed) != sorted(bs["seeds"]):
        return {"note": "not computed: the batteries' replicate seeds differ"}
    j = [by_seed[s] for s in bs["seeds"]]
    out: dict[str, Any] = {}
    for key, name in (
        ("waves_per_replicate", "fronts_standard"),
        ("waves_stripe_per_replicate", "fronts_stripe"),
    ):
        fr = [float(w["n_backward"]) for w in rs[key]]
        fb = [float(w["n_backward"]) for w in bs[key]]
        out[name] = cb2.ci([fb[k] - fr[j[k]] for k in range(len(j))])
    obs = ref["observed"]["segment_speeds_ms"]
    rr = [_rmspe_15min_field(f, obs) for f in rs["segment_speeds_ms_per_replicate"]]
    rb = [_rmspe_15min_field(f, obs) for f in bs["segment_speeds_ms_per_replicate"]]
    out["rmspe_15min_per_replicate"] = cb2.ci([rb[k] - rr[j[k]] for k in range(len(j))])
    out["note"] = (
        "two-sided 95 % t-intervals of per-seed differences; the gated RMSPE (R5) is of the replicate-mean "
        "field, these are per-replicate RMSPEs (context, not a criterion)"
    )
    return out


def braking(path: str | Path, art: Mapping[str, Any]) -> dict[str, Any]:
    """One battery's braking counts (``harness/hard_braking.py``), summed over its replicates (reported)."""
    p = _path(path)
    if not p.is_file():
        return {"path": rel(p), "available": False}
    doc = load_json(p)
    per = doc.get("per_seed") or {}
    missing = sorted(s for s, v in per.items() if "missing" in v)
    seeds_ok = sorted(per) == sorted(str(s) for s in art["simulated"]["seeds"])
    every = [v.get("every_step_sampled") for v in per.values()]
    return {
        "path": rel(p),
        "available": True,
        "config_hash_matches_battery": doc.get("config_hash") == art.get("config_hash"),
        "seeds_match_battery": seeds_ok,
        "replicates_missing_trajectories": missing,
        "every_step_sampled": all(e is True for e in every) if every else None,
        "steps_below_emergency_whole_run": sum(
            int(v["below_ms2"][EMERGENCY]) for v in per.values() if "below_ms2" in v
        ),
        "steps_below_emergency_study_window": sum(
            int(v["below_ms2_study_window"][EMERGENCY]) for v in per.values() if "below_ms2" in v
        ),
    }


def locks_and_breakdown(art: Mapping[str, Any]) -> dict[str, Any]:
    """The ``no_locks`` row, the locked seeds and the coordinator's breakdown reading (all reported only)."""
    sim = art["simulated"]
    row = next((r for r in art["criteria"] if r["name"] == "no_locks"), {})
    per = sim.get("locks_per_replicate")
    locked = (
        None
        if per is None
        else [s for s, rec in zip(sim["seeds"], per, strict=True) if (rec or {}).get("locked")]
    )
    realized = [float(x) for x in sim["demand_realized_fraction"]]
    mean_r = float(np.mean(realized))
    return {
        "no_locks_row": {k: row.get(k) for k in ("value", "passed", "evaluated")},
        "zero_locks": art.get("zero_locks"),
        "locked_seeds": locked,
        "breakdown": {
            "rule": f"a replicate realising < {BREAKDOWN_REPLICATE_SHARE} of its planned demand while the "
            f"battery's mean realised share is >= {BREAKDOWN_BATTERY_MEAN} (reported only)",
            "seeds": [
                s
                for s, r in zip(sim["seeds"], realized, strict=True)
                if r < BREAKDOWN_REPLICATE_SHARE and mean_r >= BREAKDOWN_BATTERY_MEAN
            ],
            "min_realized": min(realized),
        },
    }


def side_by_side(reading: Mapping[str, Any], p13: Mapping[str, Any]) -> dict[str, Any]:
    """This round's R1-R5 beside p13's on the k = 1 arm (_dc_refit), value for value."""
    k0, k1 = reading["criteria"], p13["criteria"]

    def row(c: Mapping[str, Any], crit: str) -> dict[str, Any]:
        if crit == "R1":
            return {"ref": c["ref_collisions"], "b2": c["b2_collisions"], "passed": c["passed"]}
        if crit == "R2":
            return {
                "ref": c["ref_realized_mean"],
                "b2": c["b2_realized_mean"],
                "passed": c["passed"],
            }
        if crit == "R3":
            return {
                "ref": c["ref_geh_vs_corrected_reported"],
                "b2": c["b2_geh_vs_corrected"],
                "passed": c["passed"],
            }
        if crit == "R4":
            return {"ref": c["ref_geh"], "b2": c["b2_geh"], "passed": c["passed"]}
        return {
            "wave": c["wave_verdict"],
            "rmspe_15min": c["rmspe_15min"],
            "passed": c["passed"],
        }

    return {
        "k0_this_round": {k: row(k0[k], k) for k in k0},
        "k1_p13": {k: row(k1[k], k) for k in k1},
        "k1_p13_all_pass": p13["all_pass"],
        "k1_p13_readout": P13_READOUT,
    }


DEFINITIONS = {
    "R1": "every B2 replicate records the collision counter and the battery's total is 0 (zero_collisions)",
    "R2": "mean over the 20 replicates of simulated.demand_realized_fraction (departed / planned), B2 >= "
    "reference (docs/FRISCO_PROTOCOL.md Amendment 2 clarification); the one-point tolerance is reported beside",
    "R3": "per ramp, GEH(modelled 2-h flow, corrected count) < 5 for the B2 arm; modelled flow = mean over "
    "replicates of the reduce counts / 2 h; corrected count = mean over the 24 windows of max(counted - "
    "flagged, 0) x 12 / the window's pooled recommended coverage (as stage p13 read it)",
    "R4": "at 2,200 and 3,200 m, GEH(2-h mean of simulated.hourly_flows_veh_h_mean, 2-h mean of "
    "observed.hourly_flows_veh_h_recommended) of B2 <= the reference's (the pooled targets of §8.4.2)",
    "R5": "wave: the criteria row wave_speed of B2 passes wherever the reference's passes; speeds: 15-min "
    "segment-speed RMSPE (3 consecutive 5-min windows averaged on both sides, then compared) of B2 <= the "
    "reference's + 0.02",
    "wave_half": "binding on this arm (docs/DECISIONS_2026-10-07.md §A1, §5 item 1; FRISCO_PROTOCOL Amendment 5, "
    "follow-up 1): the k = 0 reference passes the wave row (stack detector, 14-22 km/h; committed 15.886 "
    "km/h), so R5 holds only if the B2 arm's wave_speed row passes too; a reference whose row does not pass is "
    "a problem (the binding was pre-registered on its pass), never a waiver",
    "paired": "B2 minus reference per seed, two-sided 95 % t-interval; context, not a criterion",
    "holds": "R1-R5 all hold (R5's wave half binding) and nothing is recorded under problems; None when a "
    "problem blocks the reading",
}


def _reading_text(holds: bool | None, failed: list[str], wave_failed: bool) -> str:
    """The reading rule of §8.4.7, fixed before the run."""
    if holds is None:
        return (
            "undetermined: a recorded problem blocks the reading (see problems); nothing is read and the owner "
            "decides"
        )
    if holds:
        return (
            "B2 holds on the k = 0 arm by its own rule, the wave half included: on corrected ramp counts the k = 0 "
            "arm keeps the emergent-wave row. The k = 0 arm on corrected counts "
            f"({NAME}) is adoptable as the k = 0 arm's inputs; adopting it is the coordinator's or owner's call. "
            "It is not validation: the gate rows still fail."
        )
    if wave_failed:
        return (
            "B2 does not hold on the k = 0 arm: its wave half fails. On corrected ramp counts no I-24 arm passes "
            "the emergent-wave row; the k = 0 arm's wave pass in the record rests on the uncorrected ramp counts and "
            "is to be labelled so. This round does not revert Amendment 5 (its basis is the count check, which it "
            f"does not test); the conflict goes to the owner. Failing: {', '.join(failed)}."
        )
    return (
        "B2 does not hold on the k = 0 arm by its own rule, the wave half holding; the k = 0 arm stays on the "
        "uncorrected counts, and B2's provisional adoption on the k = 1 candidate is not re-read here. Failing: "
        f"{', '.join(failed)}."
    )


def score(paths: Mapping[str, str | Path]) -> tuple[dict[str, Any], int]:
    """The readout of §8.4.7 and the exit status (0, or 3 when a problem blocks the reading)."""
    need = ("ref", "b2", "ref_flows", "b2_flows", "committed", "count_check", "p13")
    missing = [k for k in need if not _path(paths[k]).is_file()]
    base: dict[str, Any] = {
        "schema": "flowstate.boundary_b2_k0_corridor/1",
        "kind": "corridor_round",
        "amendment": "B2 — ramp counts without through traffic, on the k = 0 arm (A1 follow-up 1)",
        "spec": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.7 (criteria fixed 2026-10-08 before any run; R1-R5 of "
        "§8.4.3 as stage p13 read them, the wave half binding)",
        "status": "adopted provisionally as a §2.3 ramp-count correction on the k = 1 candidate (FRISCO_PROTOCOL "
        "Amendment 5); its adoption on the k = 0 arm is the coordinator's or owner's call and requires holds",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "inputs": {
            k: {"path": rel(p), "sha256": sha(p)} for k, p in paths.items() if _path(p).is_file()
        },
        "labels": {"reference": REF_LABEL, "b2": ARM_LABEL},
    }
    if missing:
        doc = {
            **base,
            "problems": [f"missing input: {k} ({rel(paths[k])})" for k in missing],
            "holds": None,
        }
        doc["reading"] = _reading_text(None, [], False)
        return doc, EXIT_BLOCKED

    ref, b2 = load_json(paths["ref"]), load_json(paths["b2"])
    ref_fl, b2_fl = load_json(paths["ref_flows"]), load_json(paths["b2_flows"])
    committed, cc, p13 = (
        load_json(paths["committed"]),
        load_json(paths["count_check"]),
        load_json(paths["p13"]),
    )
    reading = p13_reading(ref, b2, ref_fl, b2_fl, cc, committed, rel(paths["committed"]))
    problems = list(reading["problems"])

    ref_doc = load_yaml(REF_SCENARIO)
    try:
        arm_doc: Mapping[str, Any] | None = derive()["doc"]
    except Refused as e:
        arm_doc = None
        problems.append(f"the pre-registered arm cannot be derived from committed files: {e}")
    if hash_policy(ref.get("config_hash"), ref_doc) is None:
        problems.append(
            f"the reference battery's config hash {ref.get('config_hash')} does not name {REF_SCENARIO}"
        )
    if arm_doc is not None and hash_policy(b2.get("config_hash"), arm_doc) is None:
        problems.append(
            f"the B2 battery's config hash {b2.get('config_hash')} does not name the derived arm"
        )
    if b2.get("scenario") != NAME or ref.get("scenario") != ref_doc["name"]:
        problems.append("a battery ran another scenario than the round's two")
    obs = observed_differs(ref, b2)
    if obs:
        problems.append(
            f"the two batteries were scored against different observed sides: {', '.join(obs)}"
        )
    repro = reproduces(ref, committed, ref_doc)
    if not repro["exact"]:
        problems.append(
            f"the same-code reference does not reproduce {rel(paths['committed'])}: {', '.join(repro['differs'])}"
        )

    ref_wave = bool(reading["criteria"]["R5"]["wave_verdict"]["ref_passes"])
    b2_wave = bool(reading["criteria"]["R5"]["wave_verdict"]["b2_passes"])
    if not ref_wave:
        problems.append(
            "the reference's wave row does not pass: the wave half was pre-registered as binding on its pass"
        )
    wave_half = {
        "binds": True,
        "source": "docs/DECISIONS_2026-10-07.md §A1 §5 item 1; FRISCO_PROTOCOL Amendment 5, follow-up 1",
        "reference_passes": ref_wave,
        "b2_passes": b2_wave,
        "passed": b2_wave,
        "reference_value_kmh": reading["arms"]["reference"]["wave_speed_row"]["value"],
        "b2_value_kmh": reading["arms"]["b2"]["wave_speed_row"]["value"],
        "band_kmh": list(WAVE_BAND_KMH),
    }
    failed = [k for k, c in reading["criteria"].items() if not c["passed"]]
    holds: bool | None = None if problems else not failed
    b2_r, ref_r = (
        reading["arms"]["b2"]["realized_mean"],
        reading["arms"]["reference"]["realized_mean"],
    )
    doc = {
        **base,
        "problems": problems,
        "seeds": reading["seeds"],
        "same_seeds_as_step3": reading["same_seeds_as_step3"],
        "reference_reproduces_committed": {
            **repro,
            "p13_form": reading["reference_reproduces_committed"],
        },
        "criteria": reading["criteria"],
        "wave_half": wave_half,
        "all_pass": reading["all_pass"],
        "holds": holds,
        "failed": failed,
        "reading": _reading_text(holds, failed, not b2_wave),
        "arms": reading["arms"],
        "paired_b2_minus_reference": reading["paired_b2_minus_reference"],
        "reported": {
            "R2_one_point_tolerance": {
                "rule": f"B2 realised mean >= reference's - {R2_TOLERANCE_REPORTED} (Amendment 2's clarification)",
                "passed": b2_r >= ref_r - R2_TOLERANCE_REPORTED,
            },
            "paired_context": paired_context(ref, b2),
            "braking": {
                "reference": braking(paths["ref_braking"], ref),
                "b2": braking(paths["b2_braking"], b2),
                "definition": "vehicle-steps below -8.9 m/s^2 summed over the 20 replicates "
                "(harness/hard_braking.py; whole run and sim t >= 600 s); reported, not gating",
            },
            "locks": {"reference": locks_and_breakdown(ref), "b2": locks_and_breakdown(b2)},
            "gate_rows": {"reference": gate_rows(ref), "b2": gate_rows(b2)},
            "backward_fronts_per_replicate": {"reference": _fronts(ref), "b2": _fronts(b2)},
            "k1_side_by_side": side_by_side(reading, p13),
        },
        "definitions": DEFINITIONS,
    }
    return doc, (EXIT_BLOCKED if problems else 0)


def cmd_score(a: argparse.Namespace) -> int:
    paths = {
        "ref": a.ref,
        "b2": a.b2,
        "ref_flows": a.ref_flows,
        "b2_flows": a.b2_flows,
        "ref_braking": a.ref_braking,
        "b2_braking": a.b2_braking,
        "committed": a.committed,
        "count_check": a.count_check,
        "p13": a.p13,
    }
    doc, code = score(paths)
    out = _path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    crit = doc.get("criteria") or {}
    print(
        json.dumps({k: {"passed": v["passed"]} for k, v in crit.items()}),
        "holds",
        doc.get("holds"),
        "problems",
        len(doc.get("problems") or []),
    )
    print(f"-> {rel(out)}")
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("expected")
    p.add_argument("--out", default=EXPECTED)
    p = sub.add_parser("arm")
    p.add_argument("--out", default=ARM)
    p.add_argument("--base", default=RC_BASE)
    p = sub.add_parser("check-ref")
    p.add_argument("--battery", default=f"artifacts/i24_validation_{REF_LABEL}.json")
    p.add_argument("--committed", default=COMMITTED_REF)
    p = sub.add_parser("score")
    p.add_argument("--ref", default=f"artifacts/i24_validation_{REF_LABEL}.json")
    p.add_argument("--b2", default=f"artifacts/i24_validation_{ARM_LABEL}.json")
    p.add_argument("--ref-flows", default=f"artifacts/i24_b2_ramp_flows_{REF_LABEL}.json")
    p.add_argument("--b2-flows", default=f"artifacts/i24_b2_ramp_flows_{ARM_LABEL}.json")
    p.add_argument("--ref-braking", default=f"artifacts/i24_hard_braking_{REF_LABEL}.json")
    p.add_argument("--b2-braking", default=f"artifacts/i24_hard_braking_{ARM_LABEL}.json")
    p.add_argument("--committed", default=COMMITTED_REF)
    p.add_argument("--count-check", default=COUNT_CHECK)
    p.add_argument("--p13", default=P13_READOUT)
    p.add_argument("--out", default=OUT)
    a = ap.parse_args()
    cmds = {
        "expected": cmd_expected,
        "arm": cmd_arm,
        "check-ref": cmd_check_ref,
        "score": cmd_score,
    }
    return cmds[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
