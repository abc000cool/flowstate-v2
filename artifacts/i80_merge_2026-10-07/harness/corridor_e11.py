"""Round p21 (E11, docs/PRE_FRISCO_PROGRAM.md): merge gaps and speeds on NGSIM I-80, never calibrated on.

usage (repository root; stage p21_i80_merge runs every step on the VM, artifacts/i80_merge_2026-10-07/
stage_p21_e11.sh.txt):
  corridor_e11.py make-arm --source scenarios/i80_replica.yaml --out scenarios/i80_replica_measured.yaml
  corridor_e11.py run --scenario scenarios/i80_replica.yaml --label kept [--procs N] [--root runs/i80_merge]
  corridor_e11.py readout --observed artifacts/i80_merge_observed.json \\
      --kept artifacts/i80_merge_sim_kept.json --measured artifacts/i80_merge_sim_measured.json \\
      --out artifacts/i80_merge_validation.json

Everything here was fixed before any run (E11's criteria, 2026-10-07; Amendment M1 to
docs/MERGE_MODEL.md §3, approved by the coordinator on 2026-10-07); nothing is re-thresholded.

Two arms on the replica ``scripts/i80_build_replica.py`` writes (geometry, counted demand and the
measured downstream boundary of I-80; the kept I-24 arm's fleet; nothing fitted on I-80), each 20
seeds (``spawn_seeds(42, 20)``):

* ``kept`` — the kept configuration at an acceleration lane: ``merge: lane_change`` (SUMO's LC2013).
  The site has no weaving section, so the adopted weave guards (W1b, W2) do not apply.
* ``measured`` — the same document with the on-ramp on ``merge: measured`` (the central set of
  ``artifacts/merge_model_params.json``), written by ``make-arm``, which refuses any other change.

The measures are ``scripts/i80_merge_measures.py``'s, the same extractors on I-80's trajectories
and on the simulated ones (which never leave the VM). For the on-ramp's entering changes:

* **E1** (MERGE_MODEL §3(b)): the partner-speed median signs match I-80's — on the follower side
  (changer minus new follower) and on the leader side (new leader minus changer), at the change,
  the 20 seeds pooled (as §3's self-check pools its seeds), against I-80's medians.
* **E2** (§3(c)): the new follower at <= 0.9 and the leader side at <= 0.8 of a normal gap at the
  change (``ratio_pop``, §3's self-check measure: the time gap over the population's normal time
  gap at the rear vehicle's speed, the 20 seeds pooled), recovering by 10 s (each side's median at
  10 s above its median at 0 s, the self-check's recovery rule at E2's horizon).
* **E3** (new, Amendment M1): for the accepted-gap (lead, lag), critical-gap (joint estimator:
  lead, lag) and partner-speed (follower, leader) medians, the model's 20-seed 95 % interval (the
  t-interval of the per-seed medians, ``validation.metrics.ci``) overlaps I-80's bootstrap 95 %
  interval (200 resamples; percentile), both closed. A model interval with fewer than 20 seed
  readings (a seed's critical-gap fit unfitted or at a bound) is underpowered (CLAUDE.md §0.6) and
  does not count as overlapping; an I-80 interval that does not exist (an unfitted or bounded fit)
  leaves the check unevaluable. Six checks; E3 holds when all six do.
* **E4**: zero collisions in the arm's 20 runs.

A criterion is ``True`` (met), ``False`` (not met) or ``None`` (could not be evaluated: not met).
**Rescue**: ``measured`` is rescued only if it meets E1-E4 and the kept configuration fails (does
not meet) at least one. Reported, not gating: the acceleration-lane speed profile by 50-m bin, the
gap ratios at 0 / 2 / 5 / 10 s, every interval's n, departed shares with the protocol's no-lock
reading (no seed below 0.8 of the arm's median, §9.5) and the coordinator's breakdown reading (a
replicate realising below 0.9 while the arm's mean is at least 0.95).

The reading is blocked (``reading`` null, exit 3 after writing) when the inputs disagree: an arm
not on ``spawn_seeds(42, 20)``, runs of one arm at different config hashes, the two arms' runs
not the two registered documents (``i80_replica`` on ``lane_change``, ``i80_replica_measured`` on
``measured``), a simulated artifact read against another observed artifact or other windows, or a
replica merge zone more than 2 m from I-80's.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[3]

from flowstate_core.config import ScenarioConfig, config_hash  # noqa: E402
from flowstate_core.rng import spawn_seeds  # noqa: E402
from validation.metrics import ci  # noqa: E402

MASTER_SEED: Final[int] = 42
N_SEEDS: Final[int] = 20
SEEDS: Final[list[int]] = spawn_seeds(MASTER_SEED, N_SEEDS)
ARMS: Final[tuple[str, ...]] = ("kept", "measured")
SCENARIO_NAMES: Final[dict[str, str]] = {"kept": "i80_replica", "measured": "i80_replica_measured"}
MERGE_OF_ARM: Final[dict[str, str]] = {"kept": "lane_change", "measured": "measured"}
E2_FOLLOWER_MAX: Final[float] = 0.9
E2_LEADER_MAX: Final[float] = 0.8
E2_RECOVERY_KEY: Final[str] = "10"
E2_CHANGE_KEY: Final[str] = "0"
E3_MEDIANS: Final[tuple[tuple[str, str], ...]] = (
    ("accepted_gap_s", "lead"),
    ("accepted_gap_s", "lag"),
    ("critical_gap_s", "lead"),
    ("critical_gap_s", "lag"),
    ("partner_speed_ms", "follower"),
    ("partner_speed_ms", "leader"),
)
SIDES: Final[tuple[str, ...]] = ("follower", "leader")
RATIO_OFFSETS: Final[tuple[str, ...]] = ("0", "2", "5", "10")
ZONE_TOL_M: Final[float] = 2.0
LOCK_SHARE: Final[float] = 0.8  # §9.5: no seed's departed share below 0.8 of the battery median
BREAKDOWN_REPLICATE: Final[float] = 0.9  # the coordinator's breakdown reading (report-only)
BREAKDOWN_ARM_MEAN: Final[float] = 0.95
EXIT_BLOCKED: Final[int] = 3
SOURCE_COMMIT_ENV: Final[str] = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE: Final[str] = ".source_commit"

DEFINITIONS: Final[dict[str, str]] = {
    "E1": "the sign of the pooled 20-seed median partner speed at the change equals the sign of "
    "I-80's median, on the follower side (changer - new follower) and on the leader side (new "
    "leader - changer)",
    "E2": f"pooled 20-seed ratio_pop median at the change <= {E2_FOLLOWER_MAX} (follower side) and "
    f"<= {E2_LEADER_MAX} (leader side), and each side's median at 10 s above its median at 0 s",
    "E3": "Amendment M1: for each of the six medians, the model's 20-seed 95 % t-interval of the "
    "per-seed medians (validation.metrics.ci; 20 seed readings required) overlaps I-80's 200-"
    "resample bootstrap 95 % interval, both closed: lo_model <= hi_obs and lo_obs <= hi_model",
    "E4": "no collision in any of the arm's 20 runs (meta.json n_collisions)",
    "verdicts": "True met; False not met; None not evaluable (not met)",
    "rescue": "measured is rescued only if it meets E1-E4 and the kept configuration does not "
    "meet at least one",
}


# --- small helpers ---------------------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(p: Path) -> str:
    r = p.resolve()
    return str(r.relative_to(REPO)) if r.is_relative_to(REPO) else str(p)


def _f(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _code() -> dict[str, str | None]:
    """The readout's source commit (``$FLOWSTATE_SOURCE_COMMIT`` or ``.source_commit``, written
    by the VM setup) and this checkout's HEAD (on the VM a snapshot commit)."""
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


# --- the measured arm's document -----------------------------------------------------------------


def _on_ramps(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [r for r in raw["network"].get("ramps") or [] if r.get("kind") == "on"]


def _without_arm_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(dict(raw))
    d.pop("name", None)
    for r in d["network"].get("ramps") or []:
        r.pop("merge", None)
    return d


def make_arm(source: Path, out: Path) -> dict[str, str]:
    """Write the ``merge: measured`` copy of the kept document; refuse anything else.

    Returns:
        The two config hashes.
    """
    raw = yaml.safe_load(source.read_text())
    if raw.get("name") != SCENARIO_NAMES["kept"]:
        raise SystemExit(f"{source}: name {raw.get('name')!r}, not {SCENARIO_NAMES['kept']!r}")
    ons = _on_ramps(raw)
    if len(ons) != 1 or ons[0].get("merge", "lane_change") != "lane_change":
        raise SystemExit(f"{source}: expected one on-ramp on lane_change, found {ons}")
    if any(r.get("kind") == "off" for r in raw["network"].get("ramps") or []):
        raise SystemExit(f"{source}: an off-ramp: the I-80 replica has none")
    new = copy.deepcopy(raw)
    new["name"] = SCENARIO_NAMES["measured"]
    _on_ramps(new)[0]["merge"] = MERGE_OF_ARM["measured"]
    if _without_arm_fields(new) != _without_arm_fields(raw):
        raise SystemExit("the copy differs from the kept document beyond its name and merge")
    h_kept = config_hash(ScenarioConfig.model_validate(raw))
    h_meas = config_hash(ScenarioConfig.model_validate(new))
    header = (
        f"# {SCENARIO_NAMES['measured']}: {_rel(source)} "
        f"(config hash {h_kept}) with the on-ramp on merge: measured\n"
        "#   (docs/MERGE_MODEL.md; the central set of artifacts/merge_model_params.json). Written by\n"
        "#   artifacts/i80_merge_2026-10-07/harness/corridor_e11.py make-arm (E11, stage p21_i80_merge);\n"
        f"#   changed, nothing else: name and the ramp's merge. config hash {h_meas}; seeded=False.\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + yaml.safe_dump(new, sort_keys=False))
    if config_hash(ScenarioConfig.from_yaml(out)) != h_meas:
        raise SystemExit(f"{out} does not reload to config hash {h_meas}")
    return {"kept": h_kept, "measured": h_meas}


# --- the runs ----------------------------------------------------------------------------------------


def run(scenario: Path, label: str, procs: int, root: Path) -> Path:
    """20 seeds of one arm; the manifest the extraction reads (a complete earlier set is kept)."""
    from microsim.runner import is_run_complete, run_replicates

    cfg = ScenarioConfig.from_yaml(scenario)
    if cfg.seed != MASTER_SEED or cfg.replicates != N_SEEDS:
        raise SystemExit(f"{scenario}: seed {cfg.seed}, replicates {cfg.replicates}: not E11's")
    if cfg.name != SCENARIO_NAMES.get(label):
        raise SystemExit(f"{scenario}: name {cfg.name!r} is not arm {label!r}'s")
    out = root / label
    manifest = out / "MANIFEST.json"
    h = config_hash(cfg)
    if manifest.is_file():
        man = json.loads(manifest.read_text())
        if man.get("config_hash") == h and all(is_run_complete(r["run_dir"]) for r in man["runs"]):
            print(f"{label}: {len(man['runs'])} runs done earlier, not repeated", flush=True)
            return manifest
    paths = run_replicates(cfg, out, n_procs=procs)
    runs = [{"seed": int(p.run_dir.name), "run_dir": str(p.run_dir)} for p in paths]
    if [r["seed"] for r in runs] != SEEDS:
        raise SystemExit(f"{label}: the runs are not spawn_seeds(42, 20) in order")
    manifest.write_text(
        json.dumps(
            {
                "label": label,
                "scenario": str(scenario),
                "scenario_sha256": _sha(scenario),
                "config_hash": h,
                "seeds": SEEDS,
                "runs": runs,
            },
            indent=1,
        )
        + "\n"
    )
    return manifest


# --- intervals -------------------------------------------------------------------------------------


def observed_interval(observed: Mapping[str, Any], quantity: str, side: str) -> dict[str, Any]:
    """I-80's median of one quantity and side, its bootstrap 95 % interval and n."""
    m = observed["measures"][quantity]
    if quantity == "critical_gap_s":
        s = m.get(side)
        if not m.get("fitted") or s is None:
            return {"median": None, "ci95": None, "n": m.get("n_used"), "readable": False}
        ok = not s["at_bound"] and s.get("ci95") is not None
        return {
            "median": _f(s["median"]),
            "ci95": s.get("ci95"),
            "n": m.get("n_used"),
            "n_with_rejection": m.get("n_with_rejection"),
            "n_inconsistent": m.get("n_inconsistent"),
            "at_bound": bool(s["at_bound"]),
            "readable": ok,
        }
    s = m[side]
    return {
        "median": _f(s.get("median")),
        "ci95": s.get("ci95"),
        "n": s.get("n"),
        "readable": s.get("ci95") is not None,
    }


def seed_value(entry: Mapping[str, Any], quantity: str, side: str) -> float | None:
    """One seed's median of one quantity and side (None: no reading)."""
    m = entry.get(quantity) or {}
    if quantity == "critical_gap_s":
        s = m.get(side)
        if not m.get("fitted") or s is None or s.get("at_bound"):
            return None
        return _f(s.get("median"))
    s = m.get(side) or {}
    return _f(s.get("median"))


def model_interval(values: Sequence[float | None]) -> dict[str, Any]:
    """The 20-seed 95 % t-interval of per-seed values (``validation.metrics.ci``)."""
    vals = [v for v in values if v is not None and math.isfinite(v)]
    c = ci(vals) if vals else None
    if c is None or c.n == 0:
        return {"mean": None, "ci95": None, "n": 0, "underpowered": True}
    lo, hi = _f(c.lo95), _f(c.hi95)
    return {
        "mean": _f(c.mean),
        "ci95": None if lo is None or hi is None else [round(lo, 4), round(hi, 4)],
        "n": int(c.n),
        "underpowered": bool(c.underpowered),
    }


def overlaps(a: Sequence[float] | None, b: Sequence[float] | None) -> bool | None:
    """Whether two closed intervals overlap (None when either is missing)."""
    if a is None or b is None:
        return None
    return bool(float(a[0]) <= float(b[1]) and float(b[0]) <= float(a[1]))


def _by_seed(sim: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {int(e["seed"]): e for e in sim["per_seed"]}


# --- the criteria ----------------------------------------------------------------------------------


def _sign(x: float) -> int:
    return int(np.sign(x))


def e1(observed: Mapping[str, Any], sim: Mapping[str, Any]) -> dict[str, Any]:
    sides: dict[str, Any] = {}
    verdicts: list[bool | None] = []
    for side in SIDES:
        obs = _f(observed["measures"]["partner_speed_ms"][side].get("median"))
        mod = _f(
            ((sim.get("pooled") or {}).get("partner_speed_ms") or {}).get(side, {}).get("median")
        )
        n_mod = ((sim.get("pooled") or {}).get("partner_speed_ms") or {}).get(side, {}).get("n")
        ok = None if obs is None or mod is None else _sign(obs) == _sign(mod)
        verdicts.append(ok)
        sides[side] = {
            "observed_median_ms": obs,
            "observed_n": observed["measures"]["partner_speed_ms"][side].get("n"),
            "model_pooled_median_ms": mod,
            "model_pooled_n": n_mod,
            "signs_match": ok,
        }
    return {"verdict": _all(verdicts), "sides": sides}


def _ratio(block: Mapping[str, Any] | None, side: str, key: str) -> tuple[float | None, int | None]:
    r = (((block or {}).get("gap_ratio") or {}).get(side) or {}).get(key) or {}
    rp = r.get("ratio_pop") or {}
    return _f(rp.get("median")), rp.get("n")


def e2(observed: Mapping[str, Any], sim: Mapping[str, Any]) -> dict[str, Any]:
    pooled = sim.get("pooled")
    sides: dict[str, Any] = {}
    verdicts: list[bool | None] = []
    for side, cap in (("follower", E2_FOLLOWER_MAX), ("leader", E2_LEADER_MAX)):
        at0, n0 = _ratio(pooled, side, E2_CHANGE_KEY)
        at10, n10 = _ratio(pooled, side, E2_RECOVERY_KEY)
        short = None if at0 is None else at0 <= cap
        recovers = None if at0 is None or at10 is None else at10 > at0
        ok = _all([short, recovers])
        verdicts.append(ok)
        o0, on0 = _ratio(observed["measures"], side, E2_CHANGE_KEY)
        o10, on10 = _ratio(observed["measures"], side, E2_RECOVERY_KEY)
        sides[side] = {
            "cap": cap,
            "model_pooled_at_0s": at0,
            "model_n_at_0s": n0,
            "model_pooled_at_10s": at10,
            "model_n_at_10s": n10,
            "at_change_within_cap": short,
            "recovers_by_10s": recovers,
            "holds": ok,
            "observed_i80_at_0s": o0,
            "observed_n_at_0s": on0,
            "observed_i80_at_10s": o10,
            "observed_n_at_10s": on10,
        }
    return {"verdict": _all(verdicts), "sides": sides}


def e3(observed: Mapping[str, Any], sim: Mapping[str, Any]) -> dict[str, Any]:
    per = _by_seed(sim)
    checks: list[dict[str, Any]] = []
    verdicts: list[bool | None] = []
    for quantity, side in E3_MEDIANS:
        obs = observed_interval(observed, quantity, side)
        vals = [seed_value(per[s], quantity, side) if s in per else None for s in SEEDS]
        mod = model_interval(vals)
        if not obs["readable"]:
            ok: bool | None = None
            why = "I-80's interval does not exist (unfitted, at a bound, or no bootstrap)"
        elif mod["ci95"] is None or mod["underpowered"]:
            ok = False
            why = f"the model interval has {mod['n']} seed readings of {N_SEEDS} (underpowered)"
        else:
            ok = overlaps(mod["ci95"], obs["ci95"])
            why = "overlap" if ok else "disjoint"
        verdicts.append(ok)
        checks.append(
            {
                "quantity": quantity,
                "side": side,
                "observed": obs,
                "model": {**mod, "per_seed": vals},
                "overlaps": ok,
                "reason": why,
            }
        )
    return {"verdict": _all(verdicts), "checks": checks}


def e4(sim: Mapping[str, Any]) -> dict[str, Any]:
    cols = [r.get("n_collisions") for r in sim["runs"]]
    if any(c is None for c in cols) or len(cols) != N_SEEDS:
        return {"verdict": None, "collisions": cols, "n_runs": len(cols)}
    total = int(sum(int(c) for c in cols))
    return {"verdict": total == 0, "collisions_total": total, "per_run": cols, "n_runs": len(cols)}


def _all(verdicts: Sequence[bool | None]) -> bool | None:
    """False when any is False; else None when any is None; else True."""
    if any(v is False for v in verdicts):
        return False
    if any(v is None for v in verdicts):
        return None
    return True


def criteria(observed: Mapping[str, Any], sim: Mapping[str, Any]) -> dict[str, Any]:
    out = {"E1": e1(observed, sim), "E2": e2(observed, sim), "E3": e3(observed, sim), "E4": e4(sim)}
    out["meets_all"] = all(out[k]["verdict"] is True for k in ("E1", "E2", "E3", "E4"))
    out["not_met"] = [k for k in ("E1", "E2", "E3", "E4") if out[k]["verdict"] is not True]
    return out


def rescue(by_arm: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    measured_all = bool(by_arm["measured"]["meets_all"])
    kept_fails = bool(by_arm["kept"]["not_met"])
    return {
        "measured_meets_E1_E4": measured_all,
        "kept_fails_at_least_one": kept_fails,
        "kept_not_met": list(by_arm["kept"]["not_met"]),
        "measured_not_met": list(by_arm["measured"]["not_met"]),
        "rescued": measured_all and kept_fails,
        "rule": DEFINITIONS["rescue"],
    }


# --- reported, not gating ---------------------------------------------------------------------------


def speed_profile(
    observed: Mapping[str, Any], sims: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for k, ob in enumerate(observed["measures"]["accel_lane_speed_ms"]):
        row: dict[str, Any] = {
            "bin_lo_m": ob["bin_lo_m"],
            "bin_hi_m": ob["bin_hi_m"],
            "observed": {key: ob.get(key) for key in ("n", "median", "ci95", "mean")},
        }
        for arm, sim in sims.items():
            per = _by_seed(sim)
            vals = []
            for s in SEEDS:
                prof = (per.get(s) or {}).get("accel_lane_speed_ms") or []
                vals.append(_f(prof[k]["median"]) if k < len(prof) else None)
            pooled = (sim.get("pooled") or {}).get("accel_lane_speed_ms") or []
            row[arm] = {
                **model_interval(vals),
                "pooled_median": _f(pooled[k]["median"]) if k < len(pooled) else None,
                "pooled_n": pooled[k]["n"] if k < len(pooled) else None,
            }
        rows.append(row)
    return rows


def gap_ratios(
    observed: Mapping[str, Any], sims: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in SIDES:
        out[side] = {}
        for key in RATIO_OFFSETS:
            ob = ((observed["measures"]["gap_ratio"].get(side) or {}).get(key) or {}).get(
                "ratio_pop"
            ) or {}
            row: dict[str, Any] = {"observed": {k: ob.get(k) for k in ("n", "median", "ci95")}}
            for arm, sim in sims.items():
                per = _by_seed(sim)
                vals = [_ratio(per.get(s), side, key)[0] for s in SEEDS]
                pm, pn = _ratio(sim.get("pooled"), side, key)
                row[arm] = {**model_interval(vals), "pooled_median": pm, "pooled_n": pn}
            out[side][key] = row
    return out


def departures(sim: Mapping[str, Any]) -> dict[str, Any]:
    shares = [_f(r.get("departed_share")) for r in sim["runs"]]
    vals = [s for s in shares if s is not None]
    if not vals:
        return {"per_run": shares, "no_locks": None, "breakdowns": None}
    med = float(np.median(vals))
    mean = float(np.mean(vals))
    locks = [
        r["seed"]
        for r, s in zip(sim["runs"], shares, strict=True)
        if s is not None and s < LOCK_SHARE * med
    ]
    breakdowns = (
        [
            r["seed"]
            for r, s in zip(sim["runs"], shares, strict=True)
            if s is not None and s < BREAKDOWN_REPLICATE
        ]
        if mean >= BREAKDOWN_ARM_MEAN
        else []
    )
    ramp = []
    for r in sim["runs"]:
        o = r.get("on_ramp") or {}
        planned, departed = o.get("n_planned"), o.get("n_departed")
        ramp.append(round(departed / planned, 5) if planned and departed is not None else None)
    return {
        "per_run": shares,
        "on_ramp_departed_share_per_run": ramp,
        "median": round(med, 5),
        "mean": round(mean, 5),
        "no_locks": not locks,
        "lock_seeds": locks,
        "breakdown_seeds": breakdowns,
        "readings": "no_locks: no seed below 0.8 of the arm's median (protocol §9.5); breakdown "
        "(report-only): a replicate below 0.9 while the arm's mean is at least 0.95",
    }


# --- consistency --------------------------------------------------------------------------------------


def problems(observed: Mapping[str, Any], sims: Mapping[str, Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    obs_windows = [(w["lo_s"], w["hi_s"]) for w in observed["windows"]]
    for arm, sim in sims.items():
        seeds = [int(r["seed"]) for r in sim["runs"]]
        if seeds != SEEDS:
            out.append(f"{arm}: seeds {seeds} are not spawn_seeds(42, 20) in order")
        hashes = {r["config_hash"] for r in sim["runs"]}
        if len(hashes) != 1:
            out.append(f"{arm}: runs at several config hashes {sorted(hashes)}")
        names = {r["scenario"] for r in sim["runs"]}
        if names != {SCENARIO_NAMES[arm]}:
            out.append(f"{arm}: scenarios {sorted(names)}, not {SCENARIO_NAMES[arm]!r}")
        measured_zones = [bool(r.get("measured_merges")) for r in sim["runs"]]
        if arm == "measured" and not all(measured_zones):
            out.append("measured: a run without a measured merge zone in its meta")
        if arm == "kept" and any(measured_zones):
            out.append("kept: a run with a measured merge zone in its meta")
        if sim.get("observed_artifact", {}).get("data_hash") != observed.get("data_hash"):
            out.append(f"{arm}: read against another observed artifact (data hash)")
        if [(w["lo_s"], w["hi_s"]) for w in sim.get("windows", [])] != obs_windows:
            out.append(f"{arm}: windows differ from the observed artifact's")
        if sim.get("zone_problems"):
            out.append(
                f"{arm}: merge zone more than {ZONE_TOL_M} m from I-80's: {sim['zone_problems']}"
            )
        if len(sim.get("per_seed", [])) != len(sim["runs"]):
            out.append(
                f"{arm}: per-seed readings for {len(sim.get('per_seed', []))} of {len(sim['runs'])} runs"
            )
    hk = {r["config_hash"] for r in sims["kept"]["runs"]}
    hm = {r["config_hash"] for r in sims["measured"]["runs"]}
    if hk & hm:
        out.append("the two arms ran the same configuration")
    return out


def readout(observed_path: Path, kept_path: Path, measured_path: Path, out: Path) -> int:
    observed = json.loads(observed_path.read_text())
    sims = {
        "kept": json.loads(kept_path.read_text()),
        "measured": json.loads(measured_path.read_text()),
    }
    probs = problems(observed, sims)
    by_arm = {arm: criteria(observed, sim) for arm, sim in sims.items()}
    res = rescue(by_arm)
    reading = None if probs else res["rescued"]
    doc = {
        "schema_version": 1,
        "kind": "i80_merge_validation",
        "round": "E11 (docs/PRE_FRISCO_PROGRAM.md), stage p21_i80_merge",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "artifacts/i80_merge_2026-10-07/harness/corridor_e11.py readout",
        **_code(),
        "inputs": {
            name: {"path": _rel(p), "sha256": _sha(p)}
            for name, p in (
                ("observed", observed_path),
                ("kept", kept_path),
                ("measured", measured_path),
            )
        },
        "site": {
            "data_hash": observed.get("data_hash"),
            "data_version": observed.get("data_version"),
            "block": observed.get("block"),
            "site_length_m": observed.get("site_length_m"),
            "zone": observed.get("zone"),
            "windows": observed.get("windows"),
            "n_entering_changes_observed": observed["measures"].get("n_changes"),
        },
        "arms": {
            arm: {
                "scenario": SCENARIO_NAMES[arm],
                "merge": MERGE_OF_ARM[arm],
                "config_hash": sorted({r["config_hash"] for r in sims[arm]["runs"]}),
                "n_runs": len(sims[arm]["runs"]),
                "n_entering_changes_pooled": (sims[arm].get("pooled") or {}).get("n_changes"),
            }
            for arm in ARMS
        },
        "definitions": DEFINITIONS,
        "criteria": by_arm,
        "rescue": res,
        "problems": probs,
        "reading": reading,
        "reading_text": (
            "blocked: the inputs disagree (see problems)"
            if probs
            else ("measured is rescued" if res["rescued"] else "measured is not rescued")
        ),
        "reported": {
            "accel_lane_speed_ms_by_50m": speed_profile(observed, sims),
            "gap_ratios": gap_ratios(observed, sims),
            "departures": {arm: departures(sim) for arm, sim in sims.items()},
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, allow_nan=False) + "\n")
    print(f"wrote {out}: {doc['reading_text']}", flush=True)
    return EXIT_BLOCKED if probs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-arm")
    m.add_argument("--source", default=str(REPO / "scenarios" / "i80_replica.yaml"))
    m.add_argument("--out", default=str(REPO / "scenarios" / "i80_replica_measured.yaml"))
    r = sub.add_parser("run")
    r.add_argument("--scenario", required=True)
    r.add_argument("--label", required=True, choices=ARMS)
    r.add_argument("--procs", type=int, default=max((os.cpu_count() or 2) - 2, 1))
    r.add_argument("--root", default=str(REPO / "runs" / "i80_merge"))
    o = sub.add_parser("readout")
    o.add_argument("--observed", required=True)
    o.add_argument("--kept", required=True)
    o.add_argument("--measured", required=True)
    o.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "make-arm":
        print(make_arm(Path(args.source), Path(args.out)))
        return 0
    if args.cmd == "run":
        print(run(Path(args.scenario), args.label, args.procs, Path(args.root)))
        return 0
    return readout(Path(args.observed), Path(args.kept), Path(args.measured), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
