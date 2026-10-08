"""Round p16 (docs/PRE_FRISCO_PROGRAM.md, D10): the I-94 ramp rules (b) and (c) as pre-registered rounds.

usage (repository root; stage p16_i94_d10 runs all three on the VM, artifacts/i94_d10_2026-10-07/stage_p16_d10.sh.txt):
  corridor_d10.py repro --out artifacts/i94_d10_repro.json
  corridor_d10.py evaluate --out artifacts/i94_d10_corridor.json
  corridor_d10.py select --readout artifacts/i94_d10_corridor.json

Everything here was fixed on 2026-10-07, before any run of the round; nothing is re-thresholded. It reads only
``artifacts/*.json`` — the battery artifacts of ``scripts/corridor_battery.py`` (``artifacts/validation_<label>.json``)
and the baseline gates of ``scripts/baseline_gate.py`` (``artifacts/baseline_gate_<label>.json``) — never a run tree, so
it re-runs locally after the ingest.

The reference is Phase A's base, ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml`` (stage p10's arm B; its
committed battery and gate under the label ``mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2``). The arms are ``_rbc``
(rules (b) and (c)) and ``_rb`` (rule (b)), written from it by ``scripts/i94_calibration_days.py --only d10``.

* ``repro``: the one-seed reproduction. The stage runs the reference's first seed (``spawn_seeds(42, 1)``, the first of
  ``spawn_seeds(42, 20)``) through the battery and the gate on today's code (label ``…_p16repro``). Reproduced when
  that replicate's ``per_seed`` record equals the committed battery's first one value for value — every field the
  committed record has, nested fields included, ``run_dir`` aside (a field the committed record lacks, from later
  code, is listed, not compared) — the observed side is the same, and the gate's per-replicate 5-, 15- and 60-min
  speed RMSPE and simulated bottlenecks of that seed equal the committed gate's on both day sets. The config hashes
  are reported, not compared: the battery writes ``--replicates 1`` into the one-seed run's configuration, and the
  committed battery records its policy's hash (v3). Exit 0 reproduced (the committed battery and gate are
  the reference), 10 not (the stage re-runs the reference's 20 seeds under the label ``…_p16ref``, which then is the
  reference), 3 when an input is missing.
* ``evaluate``: each arm against the reference on the same 20 seeds (``spawn_seeds(42, 20)``):
    - D1 zero collisions in every run (each run recording the counter) and no lock: no seed's departed share below
      0.8 of the battery's median (FRISCO_PROTOCOL §9.5, the program's common rule);
    - D2 realised demand (the battery's mean departed share) >= the reference's - 0.01;
    - D3 the gate's C1 on the calibration days (GEH < 5 share, pooled) not below the reference's;
    - D4 the gate's C3 on the calibration days (15-min station-speed RMSPE, mean over replicates) <= the reference's
      + 0.02.
  An arm holds when D1-D4 hold and nothing is recorded under ``problems`` (its own or the reference's: an artifact
  missing, a battery not of its scenario file, other seeds, another criteria profile or observed side, a run not
  recording collisions or departures, a gate not of its battery or scored on other day sets or thresholds, an
  undetermined reproduction); with a problem ``holds`` is None. Comparisons carry a 1e-12 allowance for binary
  floating point only. Reported, not gating (the reference's and each arm's ``summary``, each arm's ``paired``),
  with and without both rules (reference, ``_rb``, ``_rbc``): every gate row (C1-C6) on both day sets with the
  verdict, the 5- and 60-min RMSPE diagnostics, the validation days one by one, realised demand per seed, the
  battery's own lock reader, the breakdown reading fixed 2026-10-07 (a replicate realising less than 0.9 of its plan
  while the battery's mean is at least 0.95), missed weave exits and W1b releases, the stations the rules act on
  (simulated minus observed hourly flow, GEH), the T.H.61 NB entrance's departures, and the per-seed paired
  differences of C3 and of the departed share with 95 % t-intervals.
* ``select``: B5's I-94 arm by the rule fixed in D10: ``_rbc`` if it holds, else ``_rb``, else the reference. Exit 0
  ``rbc``, 10 ``rb``, 20 ``reference``, 3 undetermined (no readout; ``_rbc`` undetermined; or ``_rbc`` fails and
  ``_rb`` is undetermined).

``evaluate`` exits 3 after writing the readout when anything is recorded under ``problems``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]

MNDOT = "mndot_i94_wb_stpaul"
REF_SCENARIO = f"scenarios/{MNDOT}_weave_dc_cal_w1b_w2.yaml"
REF_NAME = f"{MNDOT}_weave_xlsfg_dc_cal_w1b_w2"
#: the committed reference battery's config_hash (stage p10, policy v3)
REF_RECORDED_HASH = "5080d84d4725"
REPRO_LABEL = f"{REF_NAME}_p16repro"
RERUN_LABEL = f"{REF_NAME}_p16ref"
#: in the selection's order of preference; hashes computed 2026-10-07 when the files were written, under config-hash
#: policy v4 (under v3: rbc be80c573b283, rb 2c25a75c0b88). Another hash is reported, not a problem; a battery whose
#: hash is not its scenario file's under today's policy or v3 (``scenario_hashes``) is a problem.
ARMS: dict[str, dict[str, str]] = {
    "rbc": {
        "scenario": f"scenarios/{MNDOT}_weave_dc_cal_w1b_w2_rbc.yaml",
        "label": f"{REF_NAME}_rbc",
        "expected_hash": "ad158ff561b1",
        "rules": "(b) T.H.61 NB from the mainline difference and (c) S792 out of the demand balance, still scored",
    },
    "rb": {
        "scenario": f"scenarios/{MNDOT}_weave_dc_cal_w1b_w2_rb.yaml",
        "label": f"{REF_NAME}_rb",
        "expected_hash": "1f4412f6b393",
        "rules": "(b) T.H.61 NB from the mainline difference",
    },
}
CAL_OBS = "artifacts/p1_rehearsal_2026-10-04/observations_calibration.json"
PROFILE = "fhwa_tat3_2004"
BATTERY_SCHEMA = "flowstate.corridor_validation/1"
GATE_SCHEMA = "flowstate.baseline_gate/1"
MASTER_SEED, N_SEEDS = 42, 20
REALISED_TOLERANCE = 0.01
C3_MARGIN = 0.02
LOCK_SHARE = 0.8
BREAKDOWN_SHARE, BREAKDOWN_MEAN = 0.9, 0.95
EPS = 1e-12
GATE_AGGS = ("300", "900", "3600")
DAY_SETS = ("calibration", "validation")
#: the observed-side fields of a battery that the arms and the reference must share; the one-seed reproduction
#: compares those that do not count replicates
OBSERVED_KEYS = (
    "path",
    "corridor",
    "dates",
    "aggregation",
    "t0_local",
    "window_s",
    "n_stations",
    "n_windows",
    "n_windows_compared",
    "n_link_hours",
    "n_speed_cells",
    "n_replicates",
    "detector_wave_speed",
)
OBSERVED_KEYS_ONE_SEED = tuple(
    k for k in OBSERVED_KEYS if k not in ("n_link_hours", "n_speed_cells", "n_replicates")
)
#: stations the rules act on (docs/I94_CALIBRATION_DAYS.md §4)
RULE_STATIONS = ("S1069", "S1070", "S1948", "S792", "S791", "S790", "S97")
TH61_RAMP = "on-ramp 53062592"
SELECT_CODES = {"rbc": 0, "rb": 10, "reference": 20}
UNDETERMINED = 3
EXIT_NOT_REPRODUCED = 10
EXIT_BLOCKED = 3
REPRO_OUT = "artifacts/i94_d10_repro.json"


# --------------------------------------------------------------------------- helpers


def battery_path(label: str) -> str:
    return f"artifacts/validation_{label}.json"


def gate_path(label: str) -> str:
    return f"artifacts/baseline_gate_{label}.json"


def load(rel: str) -> dict[str, Any] | None:
    p = REPO / rel
    return json.loads(p.read_text()) if p.is_file() else None


def seeds_of_record() -> list[int]:
    from flowstate_core.rng import spawn_seeds

    return [int(s) for s in spawn_seeds(MASTER_SEED, N_SEEDS)]


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True)


def compare_committed(committed: Any, new: Any, path: str = "") -> tuple[list[str], list[str]]:
    """``(differing paths, new paths)``: every field of ``committed`` against ``new``, nested, exact and NaN-safe.

    A field ``new`` has and ``committed`` lacks (later code adding a field) is listed under new paths, not compared.
    """
    if isinstance(committed, dict) and isinstance(new, dict):
        diff: list[str] = []
        extra = [f"{path}.{k}".lstrip(".") for k in new if k not in committed]
        for k, v in committed.items():
            sub = f"{path}.{k}".lstrip(".")
            if k not in new:
                diff.append(sub)
                continue
            d, e = compare_committed(v, new[k], sub)
            diff += d
            extra += e
        return diff, extra
    if isinstance(committed, list) and isinstance(new, list):
        if len(committed) != len(new):
            return [path], []
        diff, extra = [], []
        for i, (a, b) in enumerate(zip(committed, new, strict=True)):
            d, e = compare_committed(a, b, f"{path}[{i}]")
            diff += d
            extra += e
        return ([path] if diff else []), extra
    return ([] if _canon(committed) == _canon(new) else [path]), []


def scenario_hashes(rel: str, replicates: int | None = None) -> dict[str, str]:
    """The scenario file's config hash under today's policy and, when the code offers it, under policy v3.

    ``replicates`` hashes the file as ``scripts/corridor_battery.py --replicates N`` runs it (the battery
    writes N into the configuration, so a one-seed run hashes differently from the file; the physics of a
    seed does not depend on it).
    """
    import yaml

    from flowstate_core import config as fc

    doc = yaml.safe_load((REPO / rel).read_text())
    if replicates is not None:
        doc["replicates"] = replicates
    out = {f"v{fc.CONFIG_HASH_VERSION}": fc.config_hash(fc.ScenarioConfig.model_validate(doc))}
    v3 = getattr(fc, "config_hash_v3", None)
    if v3 is not None:
        out["v3"] = v3(doc)
    return out


def gate_row(gate: Mapping[str, Any], check: str, day_set: str) -> dict[str, Any] | None:
    return next(
        (
            r
            for r in gate.get("checks") or []
            if r.get("check") == check and r.get("day_set") == day_set
        ),
        None,
    )


def gate_value(gate: Mapping[str, Any], check: str, day_set: str) -> float | None:
    row = gate_row(gate, check, day_set)
    return None if row is None or row.get("value") is None else float(row["value"])


def departed(battery: Mapping[str, Any]) -> list[float | None]:
    return [
        None
        if (r.get("insertion") or {}).get("departed_fraction") is None
        else float(r["insertion"]["departed_fraction"])
        for r in battery["per_seed"]
    ]


def _ci(values: list[float]) -> dict[str, Any]:
    from validation.metrics import ci

    c = ci(values)
    return {"mean": c.mean, "lo95": c.lo95, "hi95": c.hi95, "n": c.n}


# --------------------------------------------------------------------------- problems


def battery_problems(
    battery: Mapping[str, Any] | None, rel: str, scenario: str, label: str
) -> list[str]:
    """What keeps a battery artifact from being read as the 20 seeds of record of ``scenario``."""
    if battery is None:
        return [f"{label}: {rel} is missing"]
    p: list[str] = []
    if battery.get("schema") != BATTERY_SCHEMA:
        p.append(f"{label}: schema {battery.get('schema')!r}, not {BATTERY_SCHEMA}")
    if battery.get("scenario") != scenario:
        p.append(f"{label}: the battery ran {battery.get('scenario')!r}, not {scenario}")
    elif battery.get("config_hash") not in scenario_hashes(scenario).values():
        p.append(
            f"{label}: config_hash {battery.get('config_hash')} is not {scenario}'s ({scenario_hashes(scenario)})"
        )
    seeds = [int(s) for s in battery.get("seeds") or []]
    per = battery.get("per_seed") or []
    if seeds != seeds_of_record() or [int(r["seed"]) for r in per] != seeds_of_record():
        p.append(
            f"{label}: not the 20 seeds of record, spawn_seeds({MASTER_SEED}, {N_SEEDS}), in order"
        )
    if (battery.get("criteria_profile") or {}).get("name") != PROFILE:
        p.append(
            f"{label}: criteria profile {(battery.get('criteria_profile') or {}).get('name')!r}, not {PROFILE}"
        )
    if (battery.get("observations") or {}).get("path") != CAL_OBS:
        p.append(
            f"{label}: scored against {(battery.get('observations') or {}).get('path')!r}, not {CAL_OBS}"
        )
    col = battery.get("collisions") or {}
    if not (
        col.get("n_runs") == col.get("n_runs_recorded") == len(per)
        and not col.get("runs_not_recorded")
    ):
        p.append(f"{label}: not every run records its collision counter")
    if per and any(v is None for v in departed(battery)):
        p.append(f"{label}: a run records no departed share")
    if (battery.get("insertion") or {}).get("mean_departed_fraction") is None:
        p.append(f"{label}: no realised demand (insertion.mean_departed_fraction)")
    return p


def gate_problems(
    gate: Mapping[str, Any] | None, battery: Mapping[str, Any] | None, rel: str, label: str
) -> list[str]:
    if gate is None:
        return [f"{label}: {rel} is missing"]
    p: list[str] = []
    if gate.get("schema") != GATE_SCHEMA:
        p.append(f"{label}: gate schema {gate.get('schema')!r}, not {GATE_SCHEMA}")
    if battery is not None and gate.get("config_hash") != battery.get("config_hash"):
        p.append(f"{label}: the gate's config_hash {gate.get('config_hash')} is not its battery's")
    if gate.get("n_replicates") != N_SEEDS:
        p.append(f"{label}: the gate scored {gate.get('n_replicates')} replicates, not {N_SEEDS}")
    for check in ("C1", "C3"):
        if gate_value(gate, check, "calibration") is None:
            p.append(f"{label}: the gate has no {check} value on the calibration days")
    return p


def pair_problems(
    arm_b: Mapping[str, Any],
    arm_g: Mapping[str, Any],
    ref_b: Mapping[str, Any],
    ref_g: Mapping[str, Any],
    label: str,
) -> tuple[list[str], list[str]]:
    """``(problems, notes)`` of reading an arm against the reference: the same observed side, day sets, thresholds."""
    p: list[str] = []
    notes: list[str] = []
    oa, ob = arm_b.get("observations") or {}, ref_b.get("observations") or {}
    differ = [k for k in OBSERVED_KEYS if _canon(oa.get(k)) != _canon(ob.get(k))]
    if differ:
        p.append(f"{label}: scored against another observed side than the reference ({differ})")
    if _canon(arm_g.get("split")) != _canon(ref_g.get("split")):
        p.append(f"{label}: the gate's day split is not the reference's")
    for ds in DAY_SETS:
        a, b = (
            (arm_g.get("day_sets") or {}).get(ds) or {},
            (ref_g.get("day_sets") or {}).get(ds) or {},
        )
        for k in ("observations_path", "dates"):
            if _canon(a.get(k)) != _canon(b.get(k)):
                p.append(f"{label}: the gate's {ds} {k} is not the reference's")
        if (a.get("quality") or {}).get("sha256") != (b.get("quality") or {}).get("sha256"):
            p.append(
                f"{label}: the gate's {ds} targets were masked with another data-quality artifact"
            )
    ta, tb = arm_g.get("thresholds") or {}, ref_g.get("thresholds") or {}
    for k in sorted(set(ta) & set(tb)):
        if _canon(ta[k]) != _canon(tb[k]):
            p.append(f"{label}: the gate's threshold {k} is not the reference's")
    only = sorted(set(ta) ^ set(tb))
    if only:
        notes.append(f"{label}: gate thresholds present in one of the two gates only: {only}")
    return p, notes


# --------------------------------------------------------------------------- the reproduction


def reproduction(
    one_b: Mapping[str, Any],
    one_g: Mapping[str, Any],
    com_b: Mapping[str, Any],
    com_g: Mapping[str, Any],
) -> dict[str, Any]:
    """The one-seed run against the committed reference's first seed (module docstring)."""
    differs: list[str] = []
    seed = seeds_of_record()[0]
    one_rows, com_rows = one_b.get("per_seed") or [], com_b.get("per_seed") or []
    if len(one_rows) != 1 or not com_rows:
        return {
            "reproduced": False,
            "differs": ["per_seed: not one replicate against a committed battery"],
        }
    one, com = one_rows[0], com_rows[0]
    if int(one["seed"]) != seed or int(com["seed"]) != seed:
        differs.append(f"seed: {one['seed']} / {com['seed']}, not {seed}")
    d, new_fields = compare_committed(
        {k: v for k, v in com.items() if k != "run_dir"},
        {k: v for k, v in one.items() if k != "run_dir"},
    )
    differs += [f"per_seed[0].{x}" for x in d]
    oa, ob = one_b.get("observations") or {}, com_b.get("observations") or {}
    differs += [
        f"observations.{k}"
        for k in OBSERVED_KEYS_ONE_SEED
        if _canon(oa.get(k)) != _canon(ob.get(k))
    ]
    gate_compared = []
    for ds in DAY_SETS:
        a, b = (
            (one_g.get("day_sets") or {}).get(ds) or {},
            (com_g.get("day_sets") or {}).get(ds) or {},
        )
        for agg in GATE_AGGS:
            ra = (((a.get("rmspe") or {}).get(agg) or {}).get("per_replicate") or [None])[0]
            rb = (((b.get("rmspe") or {}).get(agg) or {}).get("per_replicate") or [None])[0]
            gate_compared.append(f"{ds}.rmspe.{agg}")
            if ra is None or rb is None or _canon(ra) != _canon(rb):
                differs.append(f"gate.{ds}.rmspe.{agg}.per_replicate[0]: {ra} / {rb}")
        sa = ((a.get("bottlenecks") or {}).get("simulated") or [None])[0]
        sb = ((b.get("bottlenecks") or {}).get("simulated") or [None])[0]
        gate_compared.append(f"{ds}.bottlenecks.simulated")
        if _canon(sa) != _canon(sb):
            differs.append(f"gate.{ds}.bottlenecks.simulated[0]")
    ha, hb = one_b.get("config_hash"), com_b.get("config_hash")
    file_hashes = scenario_hashes(REF_SCENARIO)
    one_hashes = scenario_hashes(REF_SCENARIO, replicates=len(one_rows))
    return {
        "reproduced": not differs,
        "seed": seed,
        "differs": differs,
        "fields_new_in_todays_record": new_fields,
        "gate_fields_compared": gate_compared,
        "config_hash": {
            "one_seed_run": ha,
            "committed": hb,
            "scenario_file": file_hashes,
            "scenario_file_at_one_replicate": one_hashes,
            "consistent": ha in one_hashes.values() and hb in file_hashes.values(),
            "note": "not compared: the one-seed run records the file's configuration with replicates 1 "
            "(scripts/corridor_battery.py --replicates), and the committed battery its policy's hash; "
            "consistent says each is the scenario file's under some policy",
        },
    }


def cmd_repro(out: Path) -> int:
    com_b, com_g = load(battery_path(REF_NAME)), load(gate_path(REF_NAME))
    one_b, one_g = load(battery_path(REPRO_LABEL)), load(gate_path(REPRO_LABEL))
    missing = [
        rel
        for rel, x in (
            (battery_path(REF_NAME), com_b),
            (gate_path(REF_NAME), com_g),
            (battery_path(REPRO_LABEL), one_b),
            (gate_path(REPRO_LABEL), one_g),
        )
        if x is None
    ]
    rec: dict[str, Any]
    if missing:
        rec = {"reproduced": None, "missing": missing}
    else:
        assert com_b is not None and com_g is not None and one_b is not None and one_g is not None
        rec = reproduction(one_b, one_g, com_b, com_g)
    doc = {
        "schema": "flowstate.i94_d10_repro/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/PRE_FRISCO_PROGRAM.md D10, step 1 (fixed 2026-10-07 before any run of the round)",
        "reference": {
            "scenario": REF_SCENARIO,
            "battery": battery_path(REF_NAME),
            "gate": gate_path(REF_NAME),
        },
        "one_seed": {"battery": battery_path(REPRO_LABEL), "gate": gate_path(REPRO_LABEL)},
        **rec,
        "then": "the committed battery and gate are the reference"
        if rec.get("reproduced")
        else f"the stage re-runs the reference's 20 seeds (label {RERUN_LABEL}), which is then the reference"
        if rec.get("reproduced") is False
        else "nothing is decided: an input is missing",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    print(
        json.dumps(
            {k: doc.get(k) for k in ("reproduced", "differs", "missing", "then")}, default=float
        )
    )
    if rec.get("reproduced") is None:
        return EXIT_BLOCKED
    return 0 if rec["reproduced"] else EXIT_NOT_REPRODUCED


# --------------------------------------------------------------------------- the criteria


def safety(battery: Mapping[str, Any]) -> dict[str, Any]:
    """D1's reading: collisions in every run, and §9.5's lock reading on departed shares."""
    col = battery.get("collisions") or {}
    deps = [d for d in departed(battery) if d is not None]
    med = statistics.median(deps) if deps else math.nan
    floor = LOCK_SHARE * med
    seeds = [int(r["seed"]) for r in battery["per_seed"]]
    below = [
        s for s, d in zip(seeds, departed(battery), strict=True) if d is not None and d < floor
    ]
    total = col.get("total")
    return {
        "collisions": total,
        "runs_with_collisions": col.get("runs_with_collisions"),
        "median_departed_share": med,
        "lock_floor": floor,
        "seeds_below_floor": below,
        "lowest_departed_share": min(deps) if deps else None,
        "pass": total == 0 and not below,
    }


def breakdowns(battery: Mapping[str, Any]) -> dict[str, Any]:
    """The breakdown reading fixed 2026-10-07 (report-only; PRE_FRISCO_PROGRAM.md, coordinator's decisions)."""
    mean = (battery.get("insertion") or {}).get("mean_departed_fraction")
    applies = mean is not None and float(mean) >= BREAKDOWN_MEAN
    rows = [
        {"seed": int(r["seed"]), "departed_share": d}
        for r, d in zip(battery["per_seed"], departed(battery), strict=True)
        if applies and d is not None and d < BREAKDOWN_SHARE
    ]
    return {"battery_mean": mean, "applies": applies, "replicates": rows}


def criteria(
    arm_b: Mapping[str, Any],
    arm_g: Mapping[str, Any],
    ref_b: Mapping[str, Any],
    ref_g: Mapping[str, Any],
) -> dict[str, Any]:
    s = safety(arm_b)
    ra = float(arm_b["insertion"]["mean_departed_fraction"])
    rr = float(ref_b["insertion"]["mean_departed_fraction"])
    c1a, c1r = gate_value(arm_g, "C1", "calibration"), gate_value(ref_g, "C1", "calibration")
    c3a, c3r = gate_value(arm_g, "C3", "calibration"), gate_value(ref_g, "C3", "calibration")
    assert c1a is not None and c1r is not None and c3a is not None and c3r is not None
    return {
        "D1": {**s, "verdict": s["pass"]},
        "D2": {
            "arm": ra,
            "reference": rr,
            "floor": rr - REALISED_TOLERANCE,
            "verdict": ra >= rr - REALISED_TOLERANCE - EPS,
        },
        "D3": {"arm": c1a, "reference": c1r, "difference": c1a - c1r, "verdict": c1a >= c1r - EPS},
        "D4": {
            "arm": c3a,
            "reference": c3r,
            "ceiling": c3r + C3_MARGIN,
            "difference": c3a - c3r,
            "verdict": c3a <= c3r + C3_MARGIN + EPS,
        },
    }


# --------------------------------------------------------------------------- reported, not gating


def gate_rows(gate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        f"{r['check']} {r['day_set']}": {
            "value": r.get("value"),
            "status": r.get("status"),
            "gating": r.get("gating"),
        }
        for r in gate.get("checks") or []
    }


def per_day(gate: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in (gate.get("per_day") or {}).get("rows") or []:
        out[str(row.get("date"))] = {
            c["check"]: {"value": c.get("value"), "status": c.get("status")}
            for c in row.get("checks") or []
        }
    return out


def diagnostics(gate: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ds in DAY_SETS:
        rm = ((gate.get("day_sets") or {}).get(ds) or {}).get("rmspe") or {}
        out[ds] = {
            f"{int(agg) // 60} min": ((rm.get(agg) or {}).get("ci") or {}).get("mean")
            for agg in GATE_AGGS
        }
    return out


def stations(battery: Mapping[str, Any]) -> dict[str, Any]:
    rows = ((battery.get("geh") or {}).get("link_hours") or {}).get("rows") or []
    out: dict[str, Any] = {}
    for r in rows:
        if r.get("station") in RULE_STATIONS:
            out.setdefault(r["station"], {})[r["clock"]] = {
                "obs_veh_h": r.get("obs_veh_h"),
                "sim_minus_obs_veh_h": None
                if r.get("sim_veh_h_mean") is None or r.get("obs_veh_h") is None
                else r["sim_veh_h_mean"] - r["obs_veh_h"],
                "geh_mean": r.get("geh_mean"),
            }
    return out


def th61_departures(battery: Mapping[str, Any]) -> dict[str, Any]:
    planned = departed_n = 0
    for r in battery["per_seed"]:
        for ramp in (r.get("insertion") or {}).get("ramps") or []:
            if ramp.get("name") == TH61_RAMP:
                planned += int(ramp.get("planned") or 0)
                departed_n += int(ramp.get("departed") or 0)
    return {
        "planned": planned,
        "departed": departed_n,
        "share": departed_n / planned if planned else None,
    }


def summary(battery: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    deps = departed(battery)
    return {
        "config_hash": battery.get("config_hash"),
        "gate_verdict": gate.get("verdict"),
        "gate_rows": gate_rows(gate),
        "rmspe_diagnostics": diagnostics(gate),
        "validation_days": per_day(gate),
        "realised": {
            "mean": (battery.get("insertion") or {}).get("mean_departed_fraction"),
            "lowest": min((d for d in deps if d is not None), default=None),
            "per_seed": {str(r["seed"]): d for r, d in zip(battery["per_seed"], deps, strict=True)},
        },
        "safety": safety(battery),
        "battery_locks": {
            k: (battery.get("locks") or {}).get(k)
            for k in ("n_runs_locked", "runs_locked", "by_section")
        },
        "breakdowns": breakdowns(battery),
        "weave_exits": battery.get("weave_exits"),
        "weave_releases": battery.get("weave_releases"),
        "stations": stations(battery),
        "th61_nb_departures": th61_departures(battery),
    }


def paired(
    arm_b: Mapping[str, Any],
    arm_g: Mapping[str, Any],
    ref_b: Mapping[str, Any],
    ref_g: Mapping[str, Any],
) -> dict[str, Any]:
    """Per-seed paired differences arm - reference (report-only; same seeds in the same order)."""
    out: dict[str, Any] = {}
    for ds in DAY_SETS:
        a = (
            (((arm_g.get("day_sets") or {}).get(ds) or {}).get("rmspe") or {}).get("900") or {}
        ).get("per_replicate") or []
        b = (
            (((ref_g.get("day_sets") or {}).get(ds) or {}).get("rmspe") or {}).get("900") or {}
        ).get("per_replicate") or []
        if len(a) == len(b) == N_SEEDS:
            out[f"C3 15-min RMSPE, {ds} days"] = _ci(
                [x - y for x, y in zip(a, b, strict=True) if x is not None and y is not None]
            )
    da, db = departed(arm_b), departed(ref_b)
    pairs = [(x, y) for x, y in zip(da, db, strict=True) if x is not None and y is not None]
    if len(da) == len(db) == len(pairs) == N_SEEDS:
        out["departed share"] = _ci([x - y for x, y in pairs])
    out["estimator"] = (
        "mean of the per-seed differences with a 95 % t-interval (validation.metrics.ci); the gate's C3 "
        "is the mean of the per-seed values, so with every replicate scored the mean difference is the "
        "difference of the gated values"
    )
    return out


# --------------------------------------------------------------------------- the readout


def reference_choice() -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """``(label, reproduction record, problems)``: the committed reference or the re-run (module docstring)."""
    rec = load(REPRO_OUT)
    if rec is None:
        return None, None, [f"no reproduction record ({REPRO_OUT}): the reference is undetermined"]
    if rec.get("reproduced") is True:
        return REF_NAME, rec, []
    if rec.get("reproduced") is False:
        return RERUN_LABEL, rec, []
    return None, rec, ["the one-seed reproduction is undetermined (an input was missing)"]


def select_arm(holds: Mapping[str, bool | None]) -> str | None:
    """D10's rule: ``_rbc`` if it holds, else ``_rb``, else the reference; None when undetermined."""
    if holds.get("rbc") is True:
        return "rbc"
    if holds.get("rbc") is None:
        return None
    if holds.get("rb") is True:
        return "rb"
    if holds.get("rb") is None:
        return None
    return "reference"


def evaluate(out: Path) -> dict[str, Any]:
    ref_label, rec, ref_problems = reference_choice()
    ref_b = ref_g = None
    notes: list[str] = []
    if ref_label is not None:
        ref_b, ref_g = load(battery_path(ref_label)), load(gate_path(ref_label))
        ref_problems += battery_problems(ref_b, battery_path(ref_label), REF_SCENARIO, "reference")
        ref_problems += gate_problems(ref_g, ref_b, gate_path(ref_label), "reference")
        if (
            ref_label == REF_NAME
            and ref_b is not None
            and ref_b.get("config_hash") != REF_RECORDED_HASH
        ):
            ref_problems.append(
                f"reference: the committed battery's config_hash is not {REF_RECORDED_HASH}"
            )
    arms: dict[str, Any] = {}
    for key, arm in ARMS.items():
        b, g = load(battery_path(arm["label"])), load(gate_path(arm["label"]))
        problems = battery_problems(b, battery_path(arm["label"]), arm["scenario"], key)
        problems += gate_problems(g, b, gate_path(arm["label"]), key)
        entry: dict[str, Any] = {
            "scenario": arm["scenario"],
            "label": arm["label"],
            "rules": arm["rules"],
        }
        if b is not None and b.get("config_hash") != arm["expected_hash"]:
            notes.append(
                f"{key}: config_hash {b.get('config_hash')}, not the {arm['expected_hash']} computed when the file was written"
            )
        reading = None
        if (
            not problems
            and not ref_problems
            and b is not None
            and g is not None
            and ref_b is not None
            and ref_g is not None
        ):
            pp, nn = pair_problems(b, g, ref_b, ref_g, key)
            problems += pp
            notes += nn
            if not pp:
                reading = criteria(b, g, ref_b, ref_g)
                entry["paired"] = paired(b, g, ref_b, ref_g)
        entry["problems"] = problems
        entry["criteria"] = reading
        entry["holds"] = (
            None
            if reading is None or problems or ref_problems
            else all(reading[d]["verdict"] for d in ("D1", "D2", "D3", "D4"))
        )
        if b is not None and g is not None:
            entry["summary"] = summary(b, g)
        arms[key] = entry
    chosen = select_arm({k: v["holds"] for k, v in arms.items()})
    doc = {
        "schema": "flowstate.i94_d10_corridor/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_code(),
        "spec": "docs/PRE_FRISCO_PROGRAM.md D10 (criteria and the B5 arm rule fixed 2026-10-07 before any run of the round)",
        "status": "pre-registered rounds of rules (b) and (c), adopted as amendment text before the run; an arm that "
        "holds is a calibration candidate for B5's I-94 fit, not a validation result; the gate still decides validation",
        "reference": {
            "scenario": REF_SCENARIO,
            "label": ref_label,
            "source": None
            if ref_label is None
            else "committed (stage p10), reproduced on today's code at one seed"
            if ref_label == REF_NAME
            else "re-run here (the one-seed reproduction failed)",
            "reproduction": rec,
            "problems": ref_problems,
            "summary": summary(ref_b, ref_g) if ref_b is not None and ref_g is not None else None,
        },
        "arms": arms,
        "b5_arm": {
            "rule": "_rbc if it holds, else _rb, else the reference (D10, fixed 2026-10-07)",
            "arm": chosen,
            "scenario": None
            if chosen is None
            else REF_SCENARIO
            if chosen == "reference"
            else ARMS[chosen]["scenario"],
            "p17_arm": None
            if chosen is None
            else Path(REF_SCENARIO if chosen == "reference" else ARMS[chosen]["scenario"]).stem,
        },
        "notes": notes,
        "definitions": {
            "D1": "zero collisions in every run (battery collisions.total, every run recording the counter) and no seed's "
            f"departed share below {LOCK_SHARE} of the battery's median (FRISCO_PROTOCOL 9.5)",
            "D2": f"insertion.mean_departed_fraction >= the reference's - {REALISED_TOLERANCE}",
            "D3": "baseline gate C1 on the calibration days (GEH < 5 share of station-hours, pooled over 20 replicates) "
            ">= the reference's",
            "D4": f"baseline gate C3 on the calibration days (15-min station-speed RMSPE, mean over replicates) <= the "
            f"reference's + {C3_MARGIN}",
            "allowance": f"{EPS} on every comparison, for binary floating point only",
            "reported": "C4, C6, the validation-day rows and everything under summary are reported with and without both "
            "rules (the reference, _rb, _rbc), never gating",
            "breakdown": f"report-only (2026-10-07): a replicate realising less than {BREAKDOWN_SHARE} of its plan while "
            f"the battery's mean realised share is at least {BREAKDOWN_MEAN}",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    return doc


def cmd_select(readout: Path) -> int:
    if not readout.is_file():
        print(f"undetermined: no readout {readout}")
        return UNDETERMINED
    arm = (json.loads(readout.read_text()).get("b5_arm") or {}).get("arm")
    if arm is None:
        print("undetermined: _rbc is undetermined, or it fails and _rb is undetermined")
        return UNDETERMINED
    print(arm)
    return SELECT_CODES[arm]


# --------------------------------------------------------------------------- commands

SOURCE_COMMIT_ENV = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE = ".source_commit"


def _code() -> dict[str, str | None]:
    """``code``, the source commit of the readout's code, and ``vm_snapshot``, this checkout's HEAD.

    On the VM the checkout is a ``git archive`` of the source commit committed afresh (scripts/gcp/vm_setup.sh), so
    its HEAD names no commit of the repository; the source commit is ``$FLOWSTATE_SOURCE_COMMIT``, else the one in
    ``.source_commit``; without either, ``code`` is the HEAD (a local checkout).
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("repro", "evaluate"):
        sub.add_parser(name).add_argument("--out", type=Path, required=True)
    sub.add_parser("select").add_argument("--readout", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "select":
        return cmd_select(args.readout if args.readout.is_absolute() else REPO / args.readout)
    out = args.out if args.out.is_absolute() else REPO / args.out
    if args.cmd == "repro":
        return cmd_repro(out)
    doc = evaluate(out)
    for key, arm in doc["arms"].items():
        verdicts = {
            d: (arm["criteria"] or {}).get(d, {}).get("verdict") for d in ("D1", "D2", "D3", "D4")
        }
        print(f"{key}: {verdicts} holds: {arm['holds']}")
    print(f"B5's I-94 arm: {doc['b5_arm']['arm']}")
    blocked = list(doc["reference"]["problems"]) + [
        p for a in doc["arms"].values() for p in a["problems"]
    ]
    for p in blocked:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return EXIT_BLOCKED if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
