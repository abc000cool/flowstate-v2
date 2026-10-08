"""C7b's corridor round (docs/I24_CONSISTENCY_C7B.md): the arms, the B2 reference check, the readout.

usage (repository root; stage p23_c7b, artifacts/i24_consistency_2026-10-07/stage_p23_c7b.sh.txt, runs
``arm``, ``check-ref`` and ``evaluate`` on the VM; ``expected`` runs locally at $0):
  corridor_c7b.py expected [--out artifacts/i24_consistency_2026-10-07/c7b_expected.json]
  corridor_c7b.py arm --family rcc|rcs|rccs --base scenarios/i24_replica_flow_<f>_corrected_dc.yaml \\
      --inputs artifacts/i24_replica_inputs_flow_<f>.json --out scenarios/i24_replica_flow_<f>_speedcal_dc_refit.yaml
  corridor_c7b.py check-ref [--battery artifacts/i24_validation_dc_refit_rc_p23.json]
  corridor_c7b.py evaluate [--out artifacts/i24_consistency_c7b.json]

Everything here was fixed in docs/I24_CONSISTENCY_C7B.md before any run of the round; nothing is
re-thresholded. The only thresholds are R1-R5 of docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3 (as stage p13's
``harness_b2/corridor_b2.py score`` reads them) and GEH 5 (``fhwa_default``).

* ``expected`` derives, from committed files only, each arm's document as the stage must write it: the B2
  arm's base ``scenarios/i24_replica_flow_rc_corrected_dc.yaml`` (219f7db55a74 under hash policy 3) with
  the family's corrections applied by the builder's arithmetic to the committed ``_rc`` inputs
  (``artifacts/i24_replica_inputs_flow_rc.json``) — ``c``: mainline and on-ramp inflows divided by the
  recommended coverage (``artifacts/i24_coverage.json`` ``pooled.recommended_filled``) instead of the
  equilibrium one; ``s``: the mainline steps after the first moved earlier by the free-flow time from the
  entry to the count section at the fleet's mean v0, to 0.1 s — then the level (``LEVELS``; the
  pre-registered ``b2``: the B2 arm's planned vehicles over the study period carried, s = 0.925 x V_B2 /
  V_family) through ``scripts/i24_fit_demand_scale.py``'s ``scaled_config`` arithmetic. It writes their
  hashes (current policy and v3), levels, the shift and the per-window planned / target ratios.
* ``arm`` (VM) writes one arm from the builder's family and refuses, writing nothing, unless the rebuilt
  family inputs equal the committed ``_rc`` inputs in everything the correction does not touch and record
  exactly the correction, the driver-calibrated base equals the derived base, and the arm the fitter's own
  ``scaled_config`` writes equals the derived arm (as configurations: the config-hash payload).
* ``check-ref`` (VM, right after the B2 re-run) requires the re-run to reproduce the committed p13 battery
  ``artifacts/i24_validation_dc_refit_rc.json`` exactly — the scenario named by both hashes, the seeds,
  per-replicate counts, realised fractions, collisions, segment speeds, standard and stripe fronts, every
  criteria row's value and verdict but ``no_locks`` (not recorded by p13, recorded now) and the observed
  side but its build time. Exit 0 when it does, 3 when not (the stage then runs nothing more).
* ``evaluate`` writes the readout: R1-R5 of each arm against the re-run B2 on the same seeds; reported for
  all four batteries, the link-flow row and C1's station-hour form both ways (every lane of the section's
  edge, as scored; the observed lane set), the 2-h flows at the six sections both ways, the planned /
  target ratios, ``no_locks``, the coordinator's breakdown reading; then the selection by the rule fixed
  in the plan: among B2 and the arms that hold R1-R5, the best C1 station-hour share (pooled over the
  replicates, on the observed lane set), ties to the smaller change (``CHANGE_ORDER``). A recorded problem
  leaves the selection undetermined (None) and the exit status 3.

It never reads trajectories: per battery ``artifacts/i24_validation_<label>.json`` (with
``simulated.lane_crossings``, ``scripts/i24_validate.py --lane-crossings``) and the ramp-flow reduction
``artifacts/i24_b2_ramp_flows_<label>.json`` (``harness_b2/corridor_b2.py reduce``).
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
from dataclasses import dataclass
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


def _scripts_module(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return __import__(name)


cb2 = _load(
    "c7b_corridor_b2",
    REPO / "artifacts" / "i24_discharge_2026-10-07" / "harness_b2" / "corridor_b2.py",
)

# --- the round's fixed inputs (docs/I24_CONSISTENCY_C7B.md §5) ----------------------------------
B2_ARM = "scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml"
B2_BASE = "scenarios/i24_replica_flow_rc_corrected_dc.yaml"
#: the B2 arm and its base under hash policy 3 (stage p13; FRISCO_PROTOCOL Amendment 5)
B2_ARM_HASH_V3 = "909b89f298c5"
B2_BASE_HASH_V3 = "219f7db55a74"
RC_INPUTS = "artifacts/i24_replica_inputs_flow_rc.json"
COVERAGE = "artifacts/i24_coverage.json"
#: stage p4_i24_refit's fit: best.scale 0.925, the level B2 carries
FIT = "artifacts/demand_scale_i24_flow_dc.json"
#: the arms' population (Amendment 1, k = 1) and the builder's FLEET_ARTIFACT (the shift's v0)
POP = "artifacts/idm_i24_capacity_amax_k1.0.json"
BUILDER_FLEET = "artifacts/idm_i24_capacity.json"
#: the committed B2 battery (stage p13) the re-run must reproduce, and step 3's seeds of record
P13_BATTERY = "artifacts/i24_validation_dc_refit_rc.json"
STEP3_BATTERY = "artifacts/i24_validation_dc_refit.json"
#: p14's re-scored lock records of the same B2 run (scripts/i24_rescore_locks.py; reported beside)
LOCK_SIDECAR = "artifacts/i24_locks_p14_b2_ref.json"
COUNT_CHECK = "artifacts/i24_count_consistency.json"
OUT = "artifacts/i24_consistency_c7b.json"
EXPECTED = "artifacts/i24_consistency_2026-10-07/c7b_expected.json"
REF_LABEL = "dc_refit_rc_p23"
REF_NAME = "i24_replica_flow_rc_speedcal_dc_refit"

MAINLINE_COUNT_X_M = 200.0
WINDOW_S = 300.0
COVERAGE_WINDOW_S = 900.0
N_WIN = 24
START_HHMM = "06:30"
SHIFT_DECIMALS = 1
GEH_MAX = 5.0
RMSPE_SLACK = 0.02
#: the coordinator's breakdown reading (docs/PRE_FRISCO_PROGRAM.md, decisions of 2026-10-07): reported only
BREAKDOWN_REPLICATE_SHARE = 0.9
BREAKDOWN_BATTERY_MEAN = 0.95
EXIT_REFUSED = 2
EXIT_BLOCKED = 3


@dataclass(frozen=True)
class Family:
    """One C7b arm: its family code, corrections, names and the builder flags that make it."""

    code: str
    coverage: bool
    shift: bool
    builder_args: tuple[str, ...]

    @property
    def base_name(self) -> str:
        return f"i24_replica_flow_{self.code}_corrected_dc"

    @property
    def name(self) -> str:
        return f"i24_replica_flow_{self.code}_speedcal_dc_refit"

    @property
    def label(self) -> str:
        return f"dc_refit_{self.code}"

    @property
    def scenario(self) -> str:
        return f"scenarios/{self.name}.yaml"

    @property
    def base(self) -> str:
        return f"scenarios/{self.base_name}.yaml"


FAMILIES: dict[str, Family] = {
    "rcs": Family(
        "rcs", coverage=False, shift=True, builder_args=("--insertion-shift-s", "free_flow")
    ),
    "rcc": Family(
        "rcc", coverage=True, shift=False, builder_args=("--demand-coverage", "recommended")
    ),
    "rccs": Family(
        "rccs",
        coverage=True,
        shift=True,
        builder_args=("--demand-coverage", "recommended", "--insertion-shift-s", "free_flow"),
    ),
}
#: ties go to the smaller change: B2 itself, then the shift alone (moves no rate), then the coverage
#: alone (changes every window's rate), then both
CHANGE_ORDER = ("b2", "rcs", "rcc", "rccs")
#: the arms' level: ``b2`` (pre-registered) carries the B2 arm's planned vehicles over the study period;
#: ``s`` multiplies the family base by 0.925 as B2 does; ``target`` by 1 (planned / target = 1)
LEVELS = ("b2", "s", "target")


class Refused(Exception):
    """An input is not what docs/I24_CONSISTENCY_C7B.md names; nothing is written."""


# --- small helpers ------------------------------------------------------------------------------


def rel(p: Path) -> str:
    p = p.resolve()
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_json(path: str | Path) -> Any:
    p = Path(path)
    return json.loads((p if p.is_absolute() else REPO / p).read_text())


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    doc = yaml.safe_load((p if p.is_absolute() else REPO / p).read_text())
    if not isinstance(doc, dict):
        raise Refused(f"{path}: not a scenario document")
    return doc


def current_hash(doc: Mapping[str, Any]) -> str:
    from flowstate_core.config import ScenarioConfig, config_hash

    return config_hash(ScenarioConfig.model_validate(copy.deepcopy(dict(doc))))


def v3_hash(doc: Mapping[str, Any]) -> str | None:
    import flowstate_core.config as core

    f = getattr(core, "config_hash_v3", None)
    return None if f is None else str(f(copy.deepcopy(dict(doc))))


def hash_policy(recorded: str | None, doc: Mapping[str, Any]) -> str | None:
    """The hash policy under which ``recorded`` is ``doc``'s config hash: ``current``, ``v3`` or None.

    Records written from 2026-10-04 until policy 4 quote policy-3 hashes (909b89f298c5 for the B2 arm);
    whatever this checkout's policy is, a document is named by either.
    """
    if recorded is None:
        return None
    if recorded == current_hash(doc):
        return "current"
    if recorded == v3_hash(doc):
        return "v3"
    return None


def payload(doc: Mapping[str, Any]) -> dict[str, Any]:
    from flowstate_core.config import ScenarioConfig, config_hash_payload

    return config_hash_payload(ScenarioConfig.model_validate(copy.deepcopy(dict(doc))))


def same_configuration(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """The two documents are the same configuration (config-hash payload; defaults dropped)."""
    return payload(a) == payload(b)


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True)


def _coverage_k(i: int) -> int:
    return int((i * WINDOW_S) // COVERAGE_WINDOW_S)


#: The code a C7b record rests on: its sha256 is recorded, and a path that differs from the commit
#: named in ``code`` is listed under ``code_dirty_paths`` (a hash computed in a working tree whose
#: ``flowstate_core.config`` is not the commit's is that tree's policy, ``config_hash_policy``).
CODE_PATHS = (
    "artifacts/i24_consistency_2026-10-07/harness/corridor_c7b.py",
    "artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py",
    "scripts/i24_fit_demand_scale.py",
    "scripts/i24_geh_diagnosis.py",
    "scripts/i24_build_replica.py",
    "scripts/i24_validate.py",
    "packages/flowstate_core/flowstate_core/config.py",
)


def _code() -> dict[str, Any]:
    """``code`` (the source commit: ``$FLOWSTATE_SOURCE_COMMIT``, else ``.source_commit``, else HEAD;
    None when ``CODE_PATHS`` differ from HEAD, since no commit then holds the code that ran),
    ``vm_snapshot`` (this checkout's HEAD; on the VM a snapshot commit not in the repository),
    ``code_dirty_paths`` (``CODE_PATHS`` that differ from HEAD, None when git is unavailable),
    ``code_sha256`` and ``config_hash_policy`` (the policy every config hash here is computed under)."""
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
        # a tree that differs from its commit in CODE_PATHS names no commit: code_sha256 names the files
        "code": None if dirty else source or head,
        "vm_snapshot": head,
        "code_dirty_paths": dirty,
        "code_sha256": {c: sha(REPO / c) for c in CODE_PATHS if (REPO / c).is_file()},
        "config_hash_policy": int(CONFIG_HASH_VERSION),
    }


# --- the derivation from committed files --------------------------------------------------------


def coverage_rows(
    inputs: Mapping[str, Any], cov_art: Mapping[str, Any]
) -> tuple[list[float], list[float]]:
    """Per 15-min window of the study period: the family's equilibrium ``coverage_used`` and the
    recommended coverage (``pooled.recommended_filled`` of the window with the same start)."""
    rows = sorted(inputs["coverage"]["rows"], key=lambda r: float(r["t_lo_s"]))
    rec = {float(w["t_lo_s"]): w["pooled"].get("recommended_filled") for w in cov_art["windows"]}
    c_eq = [float(r.get("coverage_equilibrium", r["coverage_used"])) for r in rows]
    c_rec = []
    for r in rows:
        v = rec.get(float(r["t_lo_s"]))
        if v is None or not 0.0 < float(v) <= 1.0:
            raise Refused(f"{COVERAGE}: no recommended_filled for the window at t = {r['t_lo_s']}")
        c_rec.append(float(v))
    if len(rows) * COVERAGE_WINDOW_S != N_WIN * WINDOW_S:
        raise Refused(f"{len(rows)} coverage windows do not cover the 24 five-minute windows")
    return c_eq, c_rec


def mainline_rates(crossings: Sequence[int], cov: Sequence[float]) -> list[float]:
    """The builder's corrected mainline inflow: ``round((N / 300) / c, 6)`` per window."""
    return [round((float(n) / WINDOW_S) / cov[_coverage_k(i)], 6) for i, n in enumerate(crossings)]


def ramp_rates(crossings: Sequence[int], cov: Sequence[float]) -> list[float]:
    """The builder's corrected on-ramp inflow: ``round(round(N / 300, 6) / c, 6)`` per window."""
    return [
        round(round(float(n) / WINDOW_S, 6) / cov[_coverage_k(i)], 6)
        for i, n in enumerate(crossings)
    ]


def free_flow_shift(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The insertion shift ``scripts/i24_build_replica.py --insertion-shift-s free_flow`` computes."""
    geo = inputs["geometry"]["sim_x_of_data_x"]
    distance = float(geo["a"]) + float(geo["b"]) * MAINLINE_COUNT_X_M
    v0 = float(load_json(BUILDER_FLEET)["mean"]["v0"])
    v0_pop = float(load_json(POP)["mean"]["v0"])
    if v0 != v0_pop:
        raise Refused(
            f"{BUILDER_FLEET} mean v0 {v0} is not the arms' population's ({POP}: {v0_pop})"
        )
    return {
        "shift_s": round(distance / v0, SHIFT_DECIMALS),
        "exact_s": distance / v0,
        "distance_m": distance,
        "v0_ms": v0,
    }


def shifted_times(times: Sequence[float], shift_s: float) -> list[float]:
    """Step starts after the first moved ``shift_s`` earlier (to 1e-6 s); the first stays at 0."""
    return [float(times[0])] + [round(float(t) - shift_s, 6) for t in times[1:]]


def planned_vehicles(doc: Mapping[str, Any]) -> float:
    """Planned mainline + on-ramp vehicles over the study period: each stream's 24 window rates x 300 s
    (the step values; the warm-up, at the first window's rate, is not counted)."""
    net = doc["network"]
    streams = [net["inflow"]] + [r["inflow"] for r in net["ramps"] if r["kind"] == "on"]
    total = 0.0
    for s in streams:
        if len(s) != N_WIN:
            raise Refused(f"an inflow with {len(s)} steps, not one per 5-min window")
        total += sum(float(q) for _, q in s) * WINDOW_S
    return total


def scale_doc(doc: Mapping[str, Any], scale: float, fleet: str, name: str) -> dict[str, Any]:
    """``scripts/i24_fit_demand_scale.py`` ``scaled_config`` on a document instead of a file."""
    raw = copy.deepcopy(dict(doc))
    raw["fleet"]["idm_calibration"] = fleet
    net = raw["network"]
    net["inflow"] = [[t, round(q * scale, 6)] for t, q in net["inflow"]]
    for ramp in net.get("ramps", []):
        if ramp.get("kind") == "on" and ramp.get("inflow"):
            ramp["inflow"] = [[t, round(q * scale, 6)] for t, q in ramp["inflow"]]
    raw["name"] = name
    return raw


def on_ramp_crossings(inputs: Mapping[str, Any], doc: Mapping[str, Any]) -> dict[str, list[int]]:
    """Each on-ramp's corrected tracked crossings (B2), by the name the scenario gives it."""
    by_name = {r["name"]: r for r in inputs["ramps"]}
    out = {}
    for spec in doc["network"]["ramps"]:
        if spec["kind"] != "on":
            continue
        rec = by_name.get(spec["name"])
        if rec is None or "ramp_lane_crossings_corrected" not in rec:
            raise Refused(f"{RC_INPUTS} has no corrected crossings for {spec['name']!r}")
        out[spec["name"]] = [int(v) for v in rec["ramp_lane_crossings_corrected"]]
    return out


def derive(level: str = "b2") -> dict[str, Any]:
    """Every arm's expected base and arm document from committed files (module docstring, ``expected``)."""
    if level not in LEVELS:
        raise Refused(f"level {level!r} is not one of {LEVELS}")
    b2_base, b2_arm = load_yaml(B2_BASE), load_yaml(B2_ARM)
    for what, doc, want in (
        ("B2 base", b2_base, B2_BASE_HASH_V3),
        ("B2 arm", b2_arm, B2_ARM_HASH_V3),
    ):
        if hash_policy(want, doc) is None:
            raise Refused(f"the {what} does not hash to {want} under the current policy or v3")
    fit = load_json(FIT)
    s_b2 = float(fit["best"]["scale"])
    if not same_configuration(scale_doc(b2_base, s_b2, POP, REF_NAME), b2_arm):
        raise Refused(
            f"{B2_ARM} is not {B2_BASE} x {s_b2} (scaled_config): the recipe does not reproduce it"
        )
    inputs, cov_art = load_json(RC_INPUTS), load_json(COVERAGE)
    if cov_art.get("data_hash") != inputs.get("data_hash"):
        raise Refused(f"{COVERAGE} and {RC_INPUTS} were made from different recordings")
    c_eq, c_rec = coverage_rows(inputs, cov_art)
    n_main = [int(v) for v in inputs["mainline"]["crossings"]]
    ramps = on_ramp_crossings(inputs, b2_base)
    # B2's own base must be the builder's arithmetic on the committed inputs at the equilibrium coverage
    if [q for _, q in b2_base["network"]["inflow"]] != mainline_rates(n_main, c_eq):
        raise Refused(f"{B2_BASE}'s mainline inflow is not the builder's arithmetic on {RC_INPUTS}")
    for spec in b2_base["network"]["ramps"]:
        if spec["kind"] == "on" and [q for _, q in spec["inflow"]] != ramp_rates(
            ramps[spec["name"]], c_eq
        ):
            raise Refused(f"{B2_BASE}'s {spec['name']} inflow is not the builder's arithmetic")
    shift = free_flow_shift(inputs)
    v_b2 = planned_vehicles(b2_base)
    out: dict[str, Any] = {
        "level": level,
        "s_b2": s_b2,
        "shift": shift,
        "c_equilibrium": c_eq,
        "c_recommended": c_rec,
        "planned_vehicles_b2_base": v_b2,
        "b2": {
            "name": REF_NAME,
            "base_doc": b2_base,
            "arm_doc": b2_arm,
            "scale": s_b2,
            "coverage": c_eq,
        },
    }
    for code, fam in FAMILIES.items():
        base = copy.deepcopy(b2_base)
        base["name"] = fam.base_name
        cov = c_rec if fam.coverage else c_eq
        net = base["network"]
        times = [float(t) for t, _ in net["inflow"]]
        if fam.shift:
            times = shifted_times(times, shift["shift_s"])
        net["inflow"] = [[t, q] for t, q in zip(times, mainline_rates(n_main, cov), strict=True)]
        for spec in net["ramps"]:
            if spec["kind"] == "on":
                spec["inflow"] = [
                    [t, q]
                    for (t, _), q in zip(
                        spec["inflow"], ramp_rates(ramps[spec["name"]], cov), strict=True
                    )
                ]
        v = planned_vehicles(base)
        scale = {"b2": s_b2 * v_b2 / v, "s": s_b2, "target": 1.0}[level]
        out[code] = {
            "name": fam.name,
            "base_doc": base,
            "arm_doc": scale_doc(base, scale, POP, fam.name),
            "scale": scale,
            "coverage": cov,
            "planned_vehicles_base": v,
        }
    return out


def planned_over_target(
    arm_doc: Mapping[str, Any], inputs: Mapping[str, Any], c_rec: Sequence[float]
) -> list[float]:
    """Per 5-min window: the arm's planned mainline + on-ramp vehicles over the row's target for the same
    tracked counts, those counts over the recommended coverage (before insertion losses)."""
    n_main = [int(v) for v in inputs["mainline"]["crossings"]]
    ramps = on_ramp_crossings(inputs, arm_doc)
    net = arm_doc["network"]
    out = []
    for i in range(N_WIN):
        planned = float(net["inflow"][i][1]) * WINDOW_S
        tracked = float(n_main[i])
        for spec in net["ramps"]:
            if spec["kind"] == "on":
                planned += float(spec["inflow"][i][1]) * WINDOW_S
                tracked += float(ramps[spec["name"]][i])
        out.append(planned / (tracked / c_rec[_coverage_k(i)]))
    return out


def lane_set_bounds() -> dict[str, Any]:
    """What the lane-set correction can do to B2's committed row, from C7's bounds (no per-lane counts).

    The committed p13 battery's 5-min row and C1 station-hour shares, with the bins of the sections on
    5-lane edges (``_rc`` geometry) re-scored: ``hard`` assumes nothing (every re-scored bin failing, or
    passing); ``mirror`` is C7's like-for-like comparison (docs/I24_GEH_DIAGNOSIS.md §7.1), the model's
    all-lane count against the recording's lanes 1-4 plus its auxiliary-band flow
    (``i24_count_consistency.json`` ``sections[*].aux_band_tracked_veh_h``), spread over the windows
    uniformly or in proportion to the lanes-1-4 count, at coverage 1 (``lower``) or at the coverage the
    row's table applies there (``table``). Reported, not a criterion.
    """
    gd = _scripts_module("i24_geh_diagnosis")
    from validation.metrics import geh

    b, cc = load_json(P13_BATTERY), load_json(COUNT_CHECK)
    obs, sim_d = b["observed"], b["simulated"]
    secs = [float(x) for x in obs["sections_m"]]
    sim = np.asarray(sim_d["hourly_flows_veh_h_mean"], dtype=np.float64)
    rec = np.asarray(obs["hourly_flows_veh_h_recommended"], dtype=np.float64)
    trk = np.asarray(obs["hourly_flows_veh_h_tracked"], dtype=np.float64)
    cpr = np.asarray(sim_d["counts_per_replicate"], dtype=np.float64)
    stored = np.asarray(b["geh"]["vs_recommended_coverage_counts"]["values"]).reshape(len(secs), -1)
    n_bins = int(stored.size)
    lanes = edge_lanes_at_sections(load_json(RC_INPUTS), secs)
    five = [i for i, n in enumerate(lanes) if n > 4]
    aux = {float(s["x_m"]): float(s["aux_band_tracked_veh_h"]) for s in cc["sections"]}
    pass_now = {f"{secs[i]:g}": int((stored[i] < GEH_MAX).sum()) for i in range(len(secs))}
    pass_other = sum(v for i, v in enumerate(pass_now.values()) if i not in five)
    base = gd.station_hours(rec, cpr, sim, secs, WINDOW_S, START_HHMM)
    mirror = {}
    for spread in ("uniform", "proportional"):
        for cov in ("lower", "table"):
            o = rec.copy()
            for i in five:
                a_w = (
                    np.full(rec.shape[1], aux[secs[i]])
                    if spread == "uniform"
                    else aux[secs[i]] * trk[i] / trk[i].mean()
                )
                if cov == "table":
                    a_w = a_w / (trk[i].mean() / rec[i].mean())
                o[i] = rec[i] + a_w
            passing = pass_other + sum(
                int(
                    sum(
                        geh(float(sim[i, w]), float(o[i, w])) < GEH_MAX for w in range(rec.shape[1])
                    )
                )
                for i in five
            )
            sh = gd.station_hours(o, cpr, sim, secs, WINDOW_S, START_HHMM)
            mirror[f"{spread}_{cov}"] = {
                "row_bins_passing": passing,
                "row_share": passing / n_bins,
                "station_hour_pooled_share": sh["pooled_over_replicates"]["share"],
                "station_hour_replicate_mean_share": sh["replicate_mean"]["share"],
            }
    return {
        "battery": P13_BATTERY,
        "sections_on_5_lane_edges_m": [secs[i] for i in five],
        "row_bins": n_bins,
        "row_bins_passing_by_section": pass_now,
        "row_share_as_scored": sum(pass_now.values()) / n_bins,
        "station_hour_pooled_share_as_scored": base["pooled_over_replicates"]["share"],
        "station_hour_replicate_mean_share_as_scored": base["replicate_mean"]["share"],
        "hard": {
            "row_share_min": pass_other / n_bins,
            "row_share_max": (pass_other + rec.shape[1] * len(five)) / n_bins,
        },
        "mirror": mirror,
    }


def expected_summary(level: str = "b2") -> dict[str, Any]:
    """The ``expected`` artifact: hashes, levels, the shift and the planned / target ratios."""
    d = derive(level)
    inputs = load_json(RC_INPUTS)
    arms = {}
    for code in ("b2", *FAMILIES):
        a = d[code]
        ratios = planned_over_target(a["arm_doc"], inputs, d["c_recommended"])
        arms[code] = {
            "scenario": B2_ARM if code == "b2" else FAMILIES[code].scenario,
            "name": a["name"],
            "config_hash": current_hash(a["arm_doc"]),
            "config_hash_v3": v3_hash(a["arm_doc"]),
            "base": B2_BASE if code == "b2" else FAMILIES[code].base,
            "base_config_hash": current_hash(a["base_doc"]),
            "base_config_hash_v3": v3_hash(a["base_doc"]),
            "scale": a["scale"],
            "planned_vehicles_study_period": planned_vehicles(a["arm_doc"]),
            "planned_over_target_per_5min": ratios,
            "planned_over_target_per_15min": [
                float(np.mean(ratios[k * 3 : (k + 1) * 3])) for k in range(N_WIN // 3)
            ],
            "coverage_used": a["coverage"],
            "mainline_step_times_sim": [t for t, _ in a["arm_doc"]["network"]["inflow"]],
        }
    return {
        "schema": "flowstate.i24_c7b_expected/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/I24_CONSISTENCY_C7B.md (pre-registration; computed from committed files, no run)",
        "level": level,
        "level_rule": {
            "b2": "s = 0.925 x V(B2 base) / V(family base): the B2 arm's planned mainline + on-ramp vehicles "
            "over the study period carried (V: 24 window rates x 300 s per stream)",
            "s": "s = 0.925, the multiplier B2 carries",
            "target": "s = 1: planned = the row's target before insertion losses",
        }[level],
        "inputs": {
            k: {"path": p, "sha256": sha(REPO / p)}
            for k, p in (
                ("b2_arm", B2_ARM),
                ("b2_base", B2_BASE),
                ("rc_inputs", RC_INPUTS),
                ("coverage", COVERAGE),
                ("fit", FIT),
                ("population", POP),
                ("builder_fleet", BUILDER_FLEET),
            )
        },
        "shift": d["shift"],
        "c_equilibrium": d["c_equilibrium"],
        "c_recommended": d["c_recommended"],
        "c_recommended_over_c_equilibrium": [
            r / e for r, e in zip(d["c_recommended"], d["c_equilibrium"], strict=True)
        ],
        "arms": arms,
        "lane_set_bounds_b2": lane_set_bounds(),
    }


# --- arm (VM) -----------------------------------------------------------------------------------


def inputs_problems(
    fam: Family, built: Mapping[str, Any], committed: Mapping[str, Any], d: Mapping[str, Any]
) -> list[str]:
    """How the rebuilt family inputs differ from the committed ``_rc`` inputs beyond the correction."""
    out = []

    def ramp_field(doc: Mapping[str, Any], key: str) -> list[Any]:
        return [r.get(key) for r in doc["ramps"]]

    same = {
        "data_hash": (committed["data_hash"], built["data_hash"]),
        "mainline crossings": (committed["mainline"]["crossings"], built["mainline"]["crossings"]),
        "ramp counts": (
            ramp_field(committed, "ramp_lane_crossings"),
            ramp_field(built, "ramp_lane_crossings"),
        ),
        "corrected ramp counts": (
            ramp_field(committed, "ramp_lane_crossings_corrected"),
            ramp_field(built, "ramp_lane_crossings_corrected"),
        ),
        "ramp reference crossings": (
            ramp_field(committed, "mainline_ref_crossings"),
            ramp_field(built, "mainline_ref_crossings"),
        ),
        "exit fractions": (
            ramp_field(committed, "exit_fraction"),
            ramp_field(built, "exit_fraction"),
        ),
        "boundary": (
            committed["boundary"]["schedule_data_time"],
            built["boundary"]["schedule_data_time"],
        ),
        "entry lane shares": (committed["entry_lane_shares"], built["entry_lane_shares"]),
        "geometry": (
            committed["geometry"]["sim_x_of_data_x"],
            built["geometry"]["sim_x_of_data_x"],
        ),
        "B2's count check": (
            committed["ramp_through_traffic"]["artifact_sha256"],
            (built.get("ramp_through_traffic") or {}).get("artifact_sha256"),
        ),
        "equilibrium coverage": (
            [r["coverage_used"] for r in committed["coverage"]["rows"]],
            [r.get("coverage_equilibrium", r["coverage_used"]) for r in built["coverage"]["rows"]],
        ),
    }
    out += [
        f"{k} differ from {RC_INPUTS}" for k, (x, y) in same.items() if not cb2.same_values(x, y)
    ]
    used = [r["coverage_used"] for r in built["coverage"]["rows"]]
    want = d["c_recommended"] if fam.coverage else d["c_equilibrium"]
    if not cb2.same_values(used, want):
        out.append(
            f"coverage_used {used} is not the {'recommended' if fam.coverage else 'equilibrium'} coverage"
        )
    if fam.coverage != ("demand_coverage" in built):
        out.append(
            f"'demand_coverage' {'missing' if fam.coverage else 'present'} in the family inputs"
        )
    block = built.get("insertion_shift")
    if fam.shift != (block is not None):
        out.append(
            f"'insertion_shift' {'missing' if fam.shift else 'present'} in the family inputs"
        )
    if fam.shift and block is not None:
        sh = d["shift"]
        if block.get("mode") != "free_flow" or block.get("shift_s") != sh["shift_s"]:
            out.append(
                f"the recorded shift {block.get('mode')} {block.get('shift_s')} s is not free_flow {sh['shift_s']} s"
            )
        if not cb2.same_values(
            [block.get("distance_m"), block.get("v0_ms")], [sh["distance_m"], sh["v0_ms"]]
        ):
            out.append("the recorded shift's distance or v0 is not the derivation's")
    return out


def cmd_arm(a: argparse.Namespace) -> int:
    fit_mod = _scripts_module("i24_fit_demand_scale")
    fam = FAMILIES[a.family]
    base_p, inputs_p, out_p = (REPO / a.base, REPO / a.inputs, REPO / a.out)
    try:
        d = derive(a.level)
        exp = d[fam.code]
        probs = inputs_problems(fam, load_json(inputs_p), load_json(RC_INPUTS), d)
        built_base = load_yaml(base_p)
        want_base = copy.deepcopy(exp["base_doc"])
        want_base["name"] = built_base.get("name")
        if built_base.get("name") != fam.base_name:
            probs.append(f"{a.base} is named {built_base.get('name')!r}, not {fam.base_name!r}")
        if not same_configuration(built_base, want_base):
            probs.append(
                f"{a.base} is not the derived base (B2's base with the {fam.code} corrections)"
            )
        doc = fit_mod.scaled_config(exp["scale"], POP, "corrected", base_p.resolve(), fam.name)
        if not same_configuration(doc, exp["arm_doc"]):
            probs.append(f"scaled_config({exp['scale']!r}, {a.base}) is not the derived arm")
        if probs:
            raise Refused("; ".join(probs))
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    h = current_hash(doc)
    sh = d["shift"]
    lines = [
        f"# {fam.name} — C7b's {fam.code} arm (docs/I24_CONSISTENCY_C7B.md; PROPOSED, not adopted): the B2",
        f"# arm's recipe (scenarios/{REF_NAME}.yaml) on the {fam.code} family. scripts/i24_build_replica.py",
        "# --suffix flow_"
        + fam.code
        + " --osm corrected --lc-strategic 5 --lc-strategic-ramp 1 --entry-lanes observed_flow",
        f"# --ramp-through-traffic exclude {' '.join(fam.builder_args)}; scripts/apply_driver_calibration.py;",
        f"# scripts/i24_fit_demand_scale.py scaled_config at s = {exp['scale']!r} on {rel(base_p)}",
        f"# (sha256 {sha(base_p)[:12]}…), fleet {POP}.",
    ]
    if a.level == "b2":
        lines.append(
            f"# The level carries the B2 arm's planned vehicles over the study period: s = {d['s_b2']} x "
            f"{d['planned_vehicles_b2_base']:.3f} / {exp['planned_vehicles_base']:.3f} (not fitted)."
        )
    else:
        lines.append(
            f"# Level {a.level!r} (docs/I24_CONSISTENCY_C7B.md names 'b2' as pre-registered)."
        )
    if fam.coverage:
        lines.append(
            "# Inflows divided by artifacts/i24_coverage.json pooled.recommended_filled (the row's target coverage)."
        )
    if fam.shift:
        lines.append(
            f"# Mainline steps after the first {sh['shift_s']} s early: {sh['distance_m']:.1f} m / v0 "
            f"{sh['v0_ms']:.4f} m/s (computed, not fitted)."
        )
    lines += [
        f"# Checked when written: equal (config-hash payload) to the arm derived from committed files by {rel(Path(__file__))}",
        f"# expected; config hash {h}; seeded=False.",
    ]
    out_p.write_text("\n".join(lines) + "\n" + yaml.safe_dump(doc, sort_keys=False))
    print(f"-> {rel(out_p)} ({h}); level {a.level} s = {exp['scale']!r}")
    return 0


# --- check-ref (VM) -----------------------------------------------------------------------------


def observed_differs(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """The ``observed`` fields two battery artifacts disagree on (exact, NaN-safe), ``wall_s`` aside."""
    oa, ob = a["observed"], b["observed"]
    keys = sorted((set(oa) | set(ob)) - {"wall_s"})
    return [k for k in keys if _canon(oa.get(k)) != _canon(ob.get(k))]


def reproduces(here: Mapping[str, Any], committed: Mapping[str, Any]) -> dict[str, Any]:
    """The B2 re-run against the committed p13 battery, field by field (exact, NaN-safe)."""
    hs, cs = here["simulated"], committed["simulated"]
    b2_doc = load_yaml(B2_ARM)

    def rows(art: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        return [
            (r["name"], r.get("value"), r.get("passed"), r.get("evaluated"))
            for r in art["criteria"]
            if r["name"] != "no_locks"
        ]

    def fronts(sim: Mapping[str, Any]) -> list[list[Any]]:
        return [
            [w["n_backward"] for w in sim[k]]
            for k in ("waves_per_replicate", "waves_stripe_per_replicate")
        ]

    checks = {
        "config_hash": hash_policy(here.get("config_hash"), b2_doc) is not None
        and hash_policy(committed.get("config_hash"), b2_doc) is not None,
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
        "backward_fronts": _canon(fronts(hs)) == _canon(fronts(cs)),
        "criteria_rows_but_no_locks": _canon(rows(here)) == _canon(rows(committed)),
        "observed": not observed_differs(here, committed),
    }
    differs = [k for k, ok in checks.items() if not ok]
    lock_row = next((r for r in here["criteria"] if r["name"] == "no_locks"), {})
    sidecar = REPO / LOCK_SIDECAR
    locked_here = [bool((r or {}).get("locked")) for r in hs.get("locks_per_replicate") or []]
    locked_p14 = None
    if sidecar.is_file():
        side = json.loads(sidecar.read_text())
        per = side.get("locks_per_replicate") or (side.get("simulated") or {}).get(
            "locks_per_replicate"
        )
        if isinstance(per, list):
            locked_p14 = [bool((r or {}).get("locked")) for r in per]
    return {
        "committed": {"path": P13_BATTERY, "sha256": sha(REPO / P13_BATTERY)},
        "checks": checks,
        "exact": not differs,
        "differs": differs,
        "observed_fields_differing": observed_differs(here, committed),
        "no_locks": {
            "status": "not recorded by p13; recorded here (reported, not part of the reproduction)",
            "row": {k: lock_row.get(k) for k in ("value", "passed", "evaluated")},
            "locked_per_replicate": locked_here,
            "p14_sidecar": LOCK_SIDECAR if sidecar.is_file() else None,
            "same_as_p14_sidecar": None if locked_p14 is None else locked_p14 == locked_here,
        },
    }


def cmd_check_ref(a: argparse.Namespace) -> int:
    p = REPO / a.battery
    if not p.is_file():
        print(f"blocked: no battery at {a.battery}", file=sys.stderr)
        return EXIT_BLOCKED
    rep = reproduces(json.loads(p.read_text()), load_json(P13_BATTERY))
    print(
        json.dumps(
            {"exact": rep["exact"], "differs": rep["differs"], "no_locks": rep["no_locks"]["row"]}
        )
    )
    return 0 if rep["exact"] else EXIT_BLOCKED


# --- evaluate -----------------------------------------------------------------------------------


def edge_lanes_at_sections(inputs: Mapping[str, Any], sections: Sequence[float]) -> list[int]:
    """The lane count of the corridor edge each section lies on, by the family geometry (C7's rule,
    scripts/i24_geh_diagnosis.py ``section_lanes``: cumulative edge lengths, sim x = a + b x)."""
    geo = inputs["geometry"]
    a, b = float(geo["sim_x_of_data_x"]["a"]), float(geo["sim_x_of_data_x"]["b"])
    starts = np.concatenate([[0.0], np.cumsum([float(v) for v in geo["edge_lengths_m"]])])
    out = []
    for x in sections:
        k = int(np.searchsorted(starts, a + b * float(x), side="right") - 1)
        out.append(int(geo["edge_lanes"][k]))
    return out


def battery_reading(
    art: Mapping[str, Any], flows: Mapping[str, Any], targets: Mapping[str, Any]
) -> dict[str, Any]:
    """One battery's summary (R-criteria inputs) and its readings both ways (all lanes / lane set)."""
    gd = _scripts_module("i24_geh_diagnosis")
    from validation.metrics import geh

    out: dict[str, Any] = {"summary": cb2.arm_summary(dict(art), dict(flows), dict(targets))}
    obs, sim = art["observed"], art["simulated"]
    sections = [float(x) for x in obs["sections_m"]]
    obs_rec = np.asarray(obs["hourly_flows_veh_h_recommended"], dtype=np.float64)
    forms = {
        "all_lanes": (
            np.asarray(sim["counts_per_replicate"], dtype=np.float64),
            np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64),
            float(art["geh"]["vs_recommended_coverage_counts"]["fraction_under_5"]),
        )
    }
    lane = sim.get("lane_crossings")
    if lane is not None:
        forms["lane_set"] = (
            np.asarray(lane["counts_per_replicate_lane_set"], dtype=np.float64),
            np.asarray(lane["hourly_flows_veh_h_mean_lane_set"], dtype=np.float64),
            float(art["geh"]["lane_set"]["vs_recommended_coverage_counts"]["fraction_under_5"]),
        )
    for form, (cpr, mean, row) in forms.items():
        sh = gd.station_hours(obs_rec, cpr, mean, sections, WINDOW_S, START_HHMM)
        out[form] = {
            "row_5min_share": row,
            "station_hour_pooled_share": sh["pooled_over_replicates"]["share"],
            "station_hour_pooled_per_replicate": sh["pooled_over_replicates"][
                "per_replicate_share"
            ],
            "station_hour_replicate_mean_share": sh["replicate_mean"]["share"],
            "station_hour_table": sh["table"],
            "two_hour": [
                {
                    "section_m": x,
                    "obs_veh_h": float(obs_rec[i].mean()),
                    "sim_veh_h": float(mean[i].mean()),
                    "geh": float(geh(float(mean[i].mean()), float(obs_rec[i].mean()))),
                }
                for i, x in enumerate(sections)
            ],
        }
    realised = [float(v) for v in sim["demand_realized_fraction"]]
    mean_r = float(np.mean(realised))
    out["breakdown"] = {
        "rule": f"a replicate realising < {BREAKDOWN_REPLICATE_SHARE} of its planned demand while the battery's "
        f"mean realised share is >= {BREAKDOWN_BATTERY_MEAN} (reported only)",
        "seeds": [
            s
            for s, r in zip(sim["seeds"], realised, strict=True)
            if r < BREAKDOWN_REPLICATE_SHARE and mean_r >= BREAKDOWN_BATTERY_MEAN
        ],
    }
    row = next((r for r in art["criteria"] if r["name"] == "no_locks"), {})
    out["no_locks"] = {k: row.get(k) for k in ("value", "passed", "evaluated")}
    out["criteria"] = [
        {k: r.get(k) for k in ("name", "value", "passed", "evaluated")} for r in art["criteria"]
    ]
    return out


def r_criteria(b: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    """§8.4.3's R1-R5 of an arm summary ``b`` against the reference's ``r`` (corridor_b2.py score's reading)."""
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
    ref_wave, arm_wave = bool(r["wave_speed_row"]["passed"]), bool(b["wave_speed_row"]["passed"])
    r5_wave = arm_wave if ref_wave else True
    r5_rmspe = b["rmspe_15min"] <= r["rmspe_15min"] + RMSPE_SLACK
    return {
        "R1": {
            "passed": r1,
            "collisions": b["collisions_total"],
            "runs_recorded": b["n_runs_recorded"],
        },
        "R2": {"passed": r2, "realized_mean": b["realized_mean"], "ref": r["realized_mean"]},
        "R3": {
            "passed": r3,
            "geh_vs_corrected": {k: v["geh_vs_corrected"] for k, v in b["ramps"].items()},
        },
        "R4": {
            "passed": r4,
            "geh": {k: v["geh"] for k, v in b["peak_sections"].items()},
            "ref_geh": {k: v["geh"] for k, v in r["peak_sections"].items()},
        },
        "R5": {
            "passed": r5_wave and r5_rmspe,
            "wave": {
                "ref_passes": ref_wave,
                "arm_passes": arm_wave,
                "binds": ref_wave,
                "passed": r5_wave,
            },
            "rmspe_15min": {
                "arm": b["rmspe_15min"],
                "ref": r["rmspe_15min"],
                "bound": r["rmspe_15min"] + RMSPE_SLACK,
                "passed": r5_rmspe,
            },
        },
    }


def paired(arm: Mapping[str, Any], ref: Mapping[str, Any]) -> dict[str, Any]:
    """Per-seed arm minus reference, 95 % t intervals (context, not a criterion)."""
    a_s, r_s = arm["simulated"], ref["simulated"]
    j = {s: i for i, s in enumerate(r_s["seeds"])}
    idx = [j[s] for s in a_s["seeds"]]
    out = {
        "realized": cb2.ci(
            [
                float(a_s["demand_realized_fraction"][k])
                - float(r_s["demand_realized_fraction"][idx[k]])
                for k in range(len(idx))
            ]
        )
    }
    sections = [float(x) for x in arm["observed"]["sections_m"]]
    for i, x in enumerate(sections):
        fa = [
            float(np.mean(np.asarray(c, float)[i])) * 3600.0 / WINDOW_S
            for c in a_s["counts_per_replicate"]
        ]
        fr = [
            float(np.mean(np.asarray(c, float)[i])) * 3600.0 / WINDOW_S
            for c in r_s["counts_per_replicate"]
        ]
        out[f"flow_{x:g}_veh_h"] = cb2.ci([fa[k] - fr[idx[k]] for k in range(len(idx))])
    return out


def select(readings: Mapping[str, Mapping[str, Any]], holds: Mapping[str, bool]) -> dict[str, Any]:
    """The rule fixed in the plan (docs/I24_CONSISTENCY_C7B.md §6): among B2 and the arms holding R1-R5,
    the best pooled C1 station-hour share on the observed lane set; ties to the smaller change."""
    candidates = ["b2"] + [c for c in CHANGE_ORDER[1:] if holds.get(c)]
    share = {c: float(readings[c]["lane_set"]["station_hour_pooled_share"]) for c in candidates}
    best = max(share.values())
    chosen = next(c for c in CHANGE_ORDER if c in candidates and share[c] == best)
    return {
        "candidates": candidates,
        "metric": share,
        "chosen": chosen,
        "tie": sum(v == best for v in share.values()) > 1,
    }


def b5_handoff(code: str, d: Mapping[str, Any], art: Mapping[str, Any]) -> dict[str, Any]:
    """What stage p15 (B5) refits for the chosen arm: its ``--p15-arm FROM:BASE:LABEL[:SCALE]``.

    B2 is p15's default (its committed p13 battery ``dc_refit_rc``, which the re-run reproduced); an arm
    names its own scenario, base, battery and, when it is not the p4 fit's 0.925, its carried level
    (``repr``: the recipe check of scripts/fit_demand_level.py needs the exact float).
    """
    if code == "b2":
        from_stem, base_stem, label, scale = (
            Path(B2_ARM).stem,
            Path(B2_BASE).stem,
            Path(P13_BATTERY).stem.removeprefix("i24_validation_"),
            d["s_b2"],
        )
    else:
        fam = FAMILIES[code]
        from_stem, base_stem, label, scale = fam.name, fam.base_name, fam.label, d[code]["scale"]
    arm = f"{from_stem}:{base_stem}:{label}" + ("" if scale == d["s_b2"] else f":{scale!r}")
    return {
        "p15_arm": arm,
        "pipeline_args": f'--stages "p15_i24_b5" --p15-arm {arm}',
        "from_scenario": f"scenarios/{from_stem}.yaml",
        "from_hash": art["config_hash"],
        "base_yaml": f"scenarios/{base_stem}.yaml",
        "base_hash": current_hash(d[code]["base_doc"]),
        "carried_scale": scale,
        "from_battery": f"artifacts/i24_validation_{label}.json",
        "note": "p15's default"
        if code == "b2"
        else "commit the family's scenarios, inputs and battery from the archive before p15 launches",
    }


def evaluate(level: str = "b2") -> dict[str, Any]:
    labels = {"b2": REF_LABEL, **{c: f.label for c, f in FAMILIES.items()}}
    problems: list[str] = []
    arts: dict[str, dict[str, Any]] = {}
    flows: dict[str, dict[str, Any]] = {}
    for code, label in labels.items():
        bp, fp = (
            REPO / f"artifacts/i24_validation_{label}.json",
            REPO / f"artifacts/i24_b2_ramp_flows_{label}.json",
        )
        if not bp.is_file():
            problems.append(f"no battery artifacts/i24_validation_{label}.json")
            continue
        arts[code] = json.loads(bp.read_text())
        if not fp.is_file():
            problems.append(f"no ramp flows artifacts/i24_b2_ramp_flows_{label}.json")
            continue
        flows[code] = json.loads(fp.read_text())
    d = derive(level)
    committed = load_json(P13_BATTERY)
    step3 = load_json(STEP3_BATTERY)["seeds"]
    rc_inputs = load_json(RC_INPUTS)
    repro = reproduces(arts["b2"], committed) if "b2" in arts else None
    if repro is not None and not repro["exact"]:
        problems.append(
            f"the B2 re-run does not reproduce {P13_BATTERY}: {', '.join(repro['differs'])}"
        )
    for code, art in arts.items():
        lab = labels[code]
        if art["seeds"] != step3:
            problems.append(f"{lab}: seeds are not step 3's (spawn_seeds(42, 20))")
        if hash_policy(art.get("config_hash"), d[code]["arm_doc"]) is None:
            problems.append(
                f"{lab}: config hash {art.get('config_hash')} does not name the derived arm"
            )
        if code != "b2" and "b2" in arts and observed_differs(art, arts["b2"]):
            problems.append(f"{lab}: scored against another observed side than the B2 re-run")
        fl = flows.get(code)
        if fl is not None and (
            fl["config_hash"] != art["config_hash"] or fl["seeds"] != art["simulated"]["seeds"]
        ):
            problems.append(f"{lab}: its ramp flows are not this battery's")
        lane = art["simulated"].get("lane_crossings")
        if lane is None:
            problems.append(f"{lab}: no lane crossings (--lane-crossings)")
        else:
            if not lane.get("sums_equal_counts_per_replicate"):
                problems.append(f"{lab}: the per-lane crossings do not sum to the all-lane counts")
            want = edge_lanes_at_sections(rc_inputs, art["observed"]["sections_m"])
            got = [s["n_lanes"] for s in lane["sections"]]
            if got != want:
                problems.append(f"{lab}: lanes crossed per section {got} are not the edges' {want}")
    cc = load_json(COUNT_CHECK)
    if (cc.get("checks") or {}).get("reproduces_committed_counts") is not True:
        problems.append(f"{COUNT_CHECK} is void")
    targets = cb2.ramp_targets(cc)
    readings = {
        code: battery_reading(art, flows[code], targets)
        for code, art in arts.items()
        if code in flows
    }
    criteria: dict[str, Any] = {}
    holds: dict[str, bool] = {}
    if "b2" in readings:
        ref_sum = readings["b2"]["summary"]
        for code in FAMILIES:
            if code not in readings:
                continue
            rc = r_criteria(readings[code]["summary"], ref_sum)
            criteria[code] = {**rc, "all_pass": all(v["passed"] for v in rc.values())}
            holds[code] = criteria[code]["all_pass"]
    pairs = {
        code: paired(arts[code], arts["b2"]) for code in FAMILIES if code in arts and "b2" in arts
    }
    selection: dict[str, Any] | None = None
    if not problems:
        selection = select(readings, holds)
        selection["b5"] = b5_handoff(selection["chosen"], d, arts[selection["chosen"]])
    ratios = {
        code: {
            "per_5min": planned_over_target(d[code]["arm_doc"], rc_inputs, d["c_recommended"]),
            "scale": d[code]["scale"],
        }
        for code in ("b2", *FAMILIES)
    }
    return {
        "schema": "flowstate.i24_consistency_c7b/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/I24_CONSISTENCY_C7B.md (pre-registered 2026-10-07, before any run of the round)",
        "status": "PROPOSED corrections, none adopted; the selection names the arm B5 refits, adoption is the owner's",
        "level": level,
        "labels": labels,
        "inputs": {
            k: {"path": p, "sha256": sha(REPO / p)}
            for k, p in (
                ("p13_battery", P13_BATTERY),
                ("count_check", COUNT_CHECK),
                ("rc_inputs", RC_INPUTS),
                ("coverage", COVERAGE),
            )
        },
        "batteries": {
            code: {
                "path": f"artifacts/i24_validation_{labels[code]}.json",
                "sha256": sha(REPO / f"artifacts/i24_validation_{labels[code]}.json"),
            }
            for code in arts
        },
        "shift": d["shift"],
        "planned_over_target": ratios,
        "reference_reproduces_p13": repro,
        "readings": readings,
        "criteria": criteria,
        "paired_arm_minus_b2": pairs,
        "problems": problems,
        "selection": selection,
        "definitions": {
            "R1-R5": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3, read as stage p13's corridor_b2.py score reads them "
            "(R3 against the corrected counts at the pooled recommended coverage; R5's wave half binds only where "
            "the reference's wave row passes), each arm against the B2 re-run on the same 20 seeds",
            "station_hour": "C1's form (docs/FRISCO_PROTOCOL.md §4): hours anchored at 06:30 (windows 0-11, 12-23); a "
            "station-hour's flow is its twelve 5-min crossings summed; observed = the mean of the row's recommended-"
            "coverage table over the hour; GEH < 5 share pooled over the 20 replicates x 6 sections x 2 hours (240), "
            "the per-replicate share's t interval and the replicate-mean form (12) beside it "
            "(scripts/i24_geh_diagnosis.py station_hours)",
            "lane_set": "scripts/i24_validate.py --lane-crossings: the simulated count on the four highest SUMO lane "
            "indices of the section's edge (the observed lanes 1-4)",
            "selection": "among B2 and the arms holding R1-R5, the largest station_hour_pooled_share on the lane set; "
            "ties to the smaller change in the order " + " < ".join(CHANGE_ORDER),
        },
    }


# --- CLI ----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("expected")
    p.add_argument("--out", default=EXPECTED)
    p.add_argument("--level", choices=LEVELS, default="b2")
    p = sub.add_parser("arm")
    p.add_argument("--family", choices=tuple(FAMILIES), required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--inputs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--level", choices=LEVELS, default="b2")
    p = sub.add_parser("check-ref")
    p.add_argument("--battery", default=f"artifacts/i24_validation_{REF_LABEL}.json")
    p = sub.add_parser("evaluate")
    p.add_argument("--out", default=OUT)
    p.add_argument("--level", choices=LEVELS, default="b2")
    a = ap.parse_args(argv)
    if a.cmd == "arm":
        return cmd_arm(a)
    if a.cmd == "check-ref":
        return cmd_check_ref(a)
    if a.cmd == "expected":
        doc = expected_summary(a.level)
        out = REPO / a.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1) + "\n")
        for code, arm in doc["arms"].items():
            print(
                f"{code:5s} {arm['config_hash']} (policy v{doc['config_hash_policy']}; v3 {arm['config_hash_v3']}) "
                f"s = {arm['scale']:.6f} "
                f"planned/target per 15 min {[round(v, 3) for v in arm['planned_over_target_per_15min']]}"
            )
        print(
            f"shift {doc['shift']['shift_s']} s ({doc['shift']['distance_m']:.2f} m / {doc['shift']['v0_ms']:.4f} m/s) -> {a.out}"
        )
        return 0
    doc = evaluate(a.level)
    out = REPO / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    for code, c in doc["criteria"].items():
        print(
            code,
            {k: v["passed"] for k, v in c.items() if isinstance(v, dict)},
            "all_pass",
            c["all_pass"],
        )
    print("selection:", None if doc["selection"] is None else doc["selection"]["chosen"])
    if doc["problems"]:
        print("blocked:", "; ".join(doc["problems"]), file=sys.stderr)
        return EXIT_BLOCKED
    return 0


if __name__ == "__main__":
    sys.exit(main())
