"""Amendment B1's corridor criteria A1-A5 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.3), from paired I-24 batteries.

usage (repository root):
  corridor_b1.py make-copy --source scenarios/S.yaml --out scenarios/S_b1.yaml --factor F --source-hash H
  corridor_b1.py evaluate --arm NAME REF_LABEL B1_LABEL COMMITTED_ARTIFACT [--arm ...] --out OUT_JSON

``make-copy`` writes the B1 copy of a committed scenario: the source's text with its comment lines replaced by
a header, ``name`` suffixed ``_b1`` and one line ``limit_factor: F`` added to ``network.boundary``; nothing
else. It refuses a source whose config hash is not ``H``, then re-reads both files and refuses a copy whose
configuration differs from the source in anything but those two fields; it prints the copy's config hash.

``evaluate`` reads, per arm, two batteries of ``scripts/i24_validate.py`` run in the same stage on the same
code and seeds (``artifacts/i24_validation_<label>.json``), each replicate's ``meta.json`` and
``edges.parquet`` (the runner's Edie field, 15 s x 100 m, computed in-run from every sample), and the
VM-side braking counts ``runs/i24_validation/<label>/hard_braking.json`` (``hard_braking.py``). It never
reads trajectories. Criteria, as §8.3 fixed them before any run (not re-thresholded here):

* A1 safety: 0 collisions in every B1 run (``simulated.n_collisions_per_replicate``, checked against each
  ``meta.json``); vehicle-steps below -8.9 m/s^2 over the 20 runs not above the reference's (the braking
  counts; whole run). Read on every arm (the row says "every run").
* A2 the boundary state, read on ``dc_refit``: the 5,400-m 2-h flow (the replicate mean of
  ``simulated.counts_per_replicate`` at the 5,400-m section, as veh/h over the study window) within
  5,829-6,309 veh/h; and the boundary-zone mean speed error smaller in magnitude than the reference's. The
  zone is where the schedule was measured, data x 5,492-6,437 m (``artifacts/i24_replica_inputs_flow.json``
  ``boundary.x_range_m``, mapped with ``geometry.sim_x_of_data_x``). Per replicate, the ``edges.parquet``
  cells lying wholly in the zone and in the study window (sim t 600-7,800 s) are grouped into the schedule's
  30-s windows, each window's Edie speed (sum of flow over sum of density) is taken, and the window speeds
  are averaged unweighted, as the schedule's study-window mean averages its 30-s steps (``zone_speeds``);
  then the replicate mean; the error is that mean minus the schedule's study-window mean as written
  (49.93 km/h). Corrected 2026-10-07, before any p12 result was read: the first version took one Edie speed
  over the whole study window, which weights the windows by their vehicle-time; it is reported as
  ``edie_2h``. The verdict (B1's error smaller in magnitude than the reference's) is unchanged. Not computed
  when any replicate of either battery has no zone speed (no ``meta.json`` or ``edges.parquet``).
* A3 no winning by backlog: realised demand (mean ``simulated.demand_realized_fraction``) >= the
  reference's. The Amendment-2 tolerance reading (no more than 1 percentage point below) is reported beside.
* A4 emergent waves: the criteria profile's wave verdict (row ``wave_speed``) unchanged where the same-code
  reference passes; backward fronts per replicate (``n_backward``, standard and stripe detectors, the two
  that §7.5's criterion (d) read) not below the reference by more than a third (B1 mean >= 2/3 of the
  reference mean, each detector).
* A5 speeds: 15-min segment-speed RMSPE not above the reference's + 0.02. Both fields averaged over three
  consecutive 5-min windows per segment, then compared (the convention of docs/I24_VALIDATION.md §0.5(a)
  and scripts/i24_weighted_target.py), on the replicate-mean simulated field as the battery's headline
  RMSPE is; per-replicate values and their paired difference are reported.

Adoption reading (§8.3: "adopt only if A1-A5 hold on the I-24 canonical arm and on I-94"): A1 on every arm,
A2 on ``dc_refit``, A3-A5 on the canonical arm; the other arms' A3-A5 are reported. I-94 is not run: its
fleet is EIDM, for which B1's factor is not defined (scripts/boundary_limit_factor.py), so the I-94 half of
the rule needs the owner. Reported, not gating: the peak sections (2,200 / 3,200 m) against their GEH
thresholds, GEH < 5 share and 5-min RMSPE; the reference's reproduction of the committed step-3 battery.
Lane shares are not computed: only the trajectories carry per-lane section crossings. Every arm feeds the
reading (A1), so any arm with ``problems`` (a replicate's files missing, a recorded factor or config hash that
disagrees, different seeds, ``edges.parquet`` cells straddling the schedule's 30-s windows, a 30-s window of
the boundary zone without density, ...) blocks it: ``i24_holds`` is None, ``adoption.blocked_by_problems``
names each such arm's problems, and ``evaluate`` exits with status 3 after writing the output.
"""

import argparse
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from flowstate_core.config import ScenarioConfig, config_hash

REPO = Path(__file__).resolve().parents[3]

A2_BAND = (5829.0, 6309.0)  # §8.3, fixed before any run (the recording's 6,009 -3 % / +5 %)
A2_SECTION_M = 5400.0
PEAK_SECTIONS_M = (2200.0, 3200.0)
FRONT_FLOOR = 2.0 / 3.0  # "not below the reference by more than a third"
RMSPE_MARGIN = 0.02
# FRISCO_PROTOCOL Amendment 2 clarification (reported beside A3's literal reading)
A3_TOLERANCE = 0.01
EMERGENCY = "-8.9"
WINDOWS_15MIN = 3
ZONE_WINDOW_S = 300.0
# the boundary schedule's step: scripts/i24_build_replica.py BOUNDARY_WINDOW_S, one observed mean speed per 30 s
# from the study window's start (zone_geometry checks it against the inputs artifact's boundary.window_s)
BOUNDARY_WINDOW_S = 30.0
EXIT_BLOCKED = 3


# --------------------------------------------------------------------------- helpers


def ci(d: list[float]) -> list[float] | None:
    """[mean, lo95, hi95] over replicate values (t-interval); None when empty."""
    a = np.asarray([v for v in d if v is not None and math.isfinite(v)], float)
    if len(a) == 0:
        return None
    sd = a.std(ddof=1) if len(a) > 1 else 0.0
    h = stats.t.ppf(0.975, len(a) - 1) * sd / math.sqrt(len(a)) if sd > 0 else 0.0
    return [float(a.mean()), float(a.mean() - h), float(a.mean() + h)]


def paired(b1: list[float | None], ref: list[float | None]) -> list[float] | None:
    """[mean, lo95, hi95] of the per-seed differences B1 - reference; a pair missing either value is skipped."""
    return ci([x - y for x, y in zip(b1, ref, strict=True) if x is not None and y is not None])


def geh(m: float, c: float) -> float:
    return math.sqrt(2.0 * (m - c) ** 2 / (m + c)) if m + c > 0 else 0.0


def rmspe(sim: np.ndarray, obs: np.ndarray) -> float:
    ok = np.isfinite(sim) & np.isfinite(obs) & (obs > 0)
    return float(np.sqrt(np.mean(((sim[ok] - obs[ok]) / obs[ok]) ** 2)))


def agg_windows(field: np.ndarray, k: int) -> np.ndarray:
    """Mean of ``k`` consecutive 5-min windows per segment (NaN-aware), as scripts/i24_weighted_target.py."""
    n = field.shape[0] // k
    with np.errstate(invalid="ignore"):
        return np.array([np.nanmean(field[i * k : (i + 1) * k], axis=0) for i in range(n)])


def resolve(run_dir: str) -> Path:
    """A battery's run_dir (absolute on the VM that wrote it) under this checkout."""
    p = Path(run_dir)
    if p.is_dir():
        return p
    parts = p.parts
    return REPO.joinpath(*parts[parts.index("runs") :]) if "runs" in parts else p


def schedule_mean(steps: list[list[float]], t_lo: float, t_hi: float) -> float:
    total = 0.0
    for i, (t, v) in enumerate(steps):
        t_next = steps[i + 1][0] if i + 1 < len(steps) else t_hi
        a, b = max(t, t_lo), min(t_next, t_hi)
        if b > a:
            total += v * (b - a)
    return total / (t_hi - t_lo)


def schedule_on_grid(steps: list[list[float]], t_lo: float, t_hi: float) -> bool:
    """Whether the schedule is one value per ``BOUNDARY_WINDOW_S`` window from ``t_lo`` over [t_lo, t_hi].

    That is scripts/i24_build_replica.py ``boundary_schedule`` shifted to sim time (data 1,800 + 30 i s ->
    sim 600 + 30 i s, its first step moved to sim 0): a step in force at ``t_lo``, every later step inside
    the window on the grid, and a whole number of windows. Then ``schedule_mean`` over the window is the
    unweighted mean of the windows' values, which A2's zone speed (``zone_speeds``) mirrors.
    """
    ts = [float(t) for t, _ in steps]
    n = (t_hi - t_lo) / BOUNDARY_WINDOW_S
    if not ts or ts[0] > t_lo + 1e-9 or abs(n - round(n)) > 1e-9:
        return False
    k = [(t - t_lo) / BOUNDARY_WINDOW_S for t in ts if t_lo < t < t_hi]
    return all(abs(x - round(x)) < 1e-9 for x in k)


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- make-copy


def make_copy(source: Path, out: Path, factor: float, source_hash: str) -> str:
    """Write the B1 copy of ``source``; return its config hash (see the module docstring)."""
    src_cfg = ScenarioConfig.from_yaml(source)
    if config_hash(src_cfg) != source_hash:
        raise SystemExit(f"{source}: config hash {config_hash(src_cfg)}, expected {source_hash}")
    if getattr(src_cfg.network, "boundary", None) is None:
        raise SystemExit(f"{source}: no network.boundary")
    lines = [ln for ln in source.read_text().splitlines(keepends=True) if not ln.startswith("#")]
    if any("limit_factor" in ln for ln in lines):
        raise SystemExit(f"{source}: already sets limit_factor")
    new_name = f"{src_cfg.name}_b1"
    names = [i for i, ln in enumerate(lines) if re.fullmatch(r"name: .+\n?", ln)]
    if len(names) != 1:
        raise SystemExit(f"{source}: {len(names)} top-level name lines")
    lines[names[0]] = f"name: {new_name}\n"
    starts = [i for i, ln in enumerate(lines) if re.fullmatch(r"  boundary:\s*\n?", ln)]
    if len(starts) != 1:
        raise SystemExit(f"{source}: {len(starts)} network.boundary blocks")
    end = starts[0] + 1
    while end < len(lines) and lines[end].startswith("    "):
        end += 1  # the block's body: its keys and the steps list's items, all indented 4+
    lines.insert(end, f"    limit_factor: {factor!r}\n")
    header = [
        f"# {new_name}: {source.relative_to(REPO) if source.is_relative_to(REPO) else source} "
        f"(config hash {source_hash}) with amendment B1\n",
        f"#   (docs/I24_DISCHARGE_DIAGNOSIS.md section 8.3): network.boundary.limit_factor {factor!r}\n",
        "#   (scripts/boundary_limit_factor.py). Written by scripts/gcp/pipeline_i24.sh stage p12_i24_b1\n",
        "#   (artifacts/i24_discharge_2026-10-07/harness/corridor_b1.py make-copy); changed, nothing else: name\n",
        "#   and that one line. The source header applies otherwise; this file's config hash is recorded in\n",
        "#   its battery artifact (config_hash).\n",
    ]
    out.write_text("".join(header + lines))
    copy_cfg = ScenarioConfig.from_yaml(out)
    expected = json.loads(json.dumps(src_cfg.model_dump(mode="json")))
    expected["name"] = new_name
    expected["network"]["boundary"]["limit_factor"] = factor
    if copy_cfg.model_dump(mode="json") != ScenarioConfig.model_validate(expected).model_dump(
        mode="json"
    ):
        out.unlink()
        raise SystemExit(
            f"{out}: the copy differs from {source} in more than its name and limit_factor"
        )
    h = config_hash(copy_cfg)
    if h == source_hash:
        raise SystemExit(f"{out}: same config hash as its source")
    return h


# --------------------------------------------------------------------------- evaluate


def zone_geometry() -> dict[str, Any]:
    """The boundary zone in sim x (where the schedule was measured)."""
    path = REPO / "artifacts" / "i24_replica_inputs_flow.json"
    inp = json.loads(path.read_text())
    a, b = inp["geometry"]["sim_x_of_data_x"]["a"], inp["geometry"]["sim_x_of_data_x"]["b"]
    lo, hi = (float(x) for x in inp["boundary"]["x_range_m"])
    w = float(inp["boundary"]["window_s"])
    if w != BOUNDARY_WINDOW_S:
        raise SystemExit(
            f"{path}: boundary.window_s {w}, but A2's zone speed groups by {BOUNDARY_WINDOW_S} s"
        )
    return {
        "data_x_m": [lo, hi],
        "sim_x_m": [a + b * lo, a + b * hi],
        "schedule_window_s": w,
        "source": f"{path.relative_to(REPO)} boundary.x_range_m, boundary.window_s, "
        "geometry.sim_x_of_data_x",
    }


def zone_speeds(run_dir: Path, zone: dict, t_lo: float, t_hi: float) -> dict[str, Any]:
    """Boundary-zone speeds of one replicate, from the ``edges.parquet`` cells wholly in the zone and [t_lo, t_hi].

    ``speed_ms`` (A2's zone speed) is built as the schedule's study-window mean is. The schedule
    (scripts/i24_build_replica.py ``boundary_schedule``) is one observed mean speed per 30-s window from
    ``t_lo``, each the mean over every sample in the zone and the window (a per-window Edie speed), and
    ``schedule_mean`` weights every window equally. So the cells are grouped into those windows
    (``(t_bin - t_lo) // BOUNDARY_WINDOW_S``), each window's Edie speed is its sum of flow over its sum of
    density, a window without density is filled as the schedule fills one without samples (forward, then
    backward), and ``speed_ms`` is the unweighted mean of the window speeds. The schedule fills a window
    the detectors missed, but a simulated zone has no observation gaps: a window without density means the
    zone was empty, so ``schedule_windows.filled`` > 0 is a problem (``battery`` records it, which blocks
    the adoption reading, as straddling cells do; review 2026-10-07). Corrected 2026-10-07, before any
    p12 result was read: the first version took one Edie speed over all the cells, which weights each window
    by its vehicle-time (dense, slow windows count more than in the schedule's mean); that value is reported
    as ``edie_2h_ms``. ``per_window_ms`` is the Edie speed per 5-min window (reported).
    """
    e = pd.read_parquet(run_dir / "edges.parquet", columns=["t_bin", "x_bin", "density", "flow"])
    xs, ts = np.unique(e["x_bin"].to_numpy()), np.unique(e["t_bin"].to_numpy())
    dx, dt = float(np.median(np.diff(xs))), float(np.median(np.diff(ts)))
    x0, x1 = zone["sim_x_m"]
    sel = e[
        (e["x_bin"] - dx / 2 >= x0 - 1e-9)
        & (e["x_bin"] + dx / 2 <= x1 + 1e-9)
        & (e["t_bin"] - dt / 2 >= t_lo - 1e-9)
        & (e["t_bin"] + dt / 2 <= t_hi + 1e-9)
    ]
    dens = float(sel["density"].sum())
    whole = float(sel["flow"].sum()) / dens if dens > 0 else math.nan

    def edie(width: float) -> pd.Series:
        """Edie speed per ``width``-s window from ``t_lo`` (NaN where a window has no density)."""
        win = ((sel["t_bin"] - t_lo) // width).astype(int)
        g = sel.groupby(win)[["flow", "density"]].sum()
        return (g["flow"] / g["density"].where(g["density"] > 0)).reindex(
            range(round((t_hi - t_lo) / width))
        )

    sched_win = edie(BOUNDARY_WINDOW_S)
    n_filled = int(sched_win.isna().sum())
    speed = float(sched_win.ffill().bfill().mean())
    # every cell must lie in one schedule window (its start and its end in the same one)
    tb = sel["t_bin"].to_numpy(float)
    first = np.floor((tb - dt / 2 - t_lo) / BOUNDARY_WINDOW_S + 1e-9)
    last = np.floor((tb + dt / 2 - t_lo) / BOUNDARY_WINDOW_S - 1e-9)
    per_window = edie(ZONE_WINDOW_S)
    return {
        "speed_ms": speed,
        "edie_2h_ms": whole,
        "schedule_windows": {
            "window_s": BOUNDARY_WINDOW_S,
            "n": len(sched_win),
            "filled": n_filled,
            "cells_inside_one_window": bool(np.array_equal(first, last)),
        },
        "per_window_ms": [
            None if not math.isfinite(v) else float(v) for v in per_window.to_numpy()
        ],
        "cells": {
            "n": len(sel),
            "dx_m": dx,
            "dt_s": dt,
            "x_bins_m": [float(sel.x_bin.min()), float(sel.x_bin.max())],
        },
    }


def load_braking(label: str, runs_root: Path) -> dict | None:
    p = runs_root / label / "hard_braking.json"
    return json.loads(p.read_text()) if p.is_file() else None


def battery(label: str, runs_root: Path, zone: dict, factor: float | None) -> dict[str, Any]:
    """Everything the criteria read from one battery (artifact, meta.json, edges.parquet, braking counts)."""
    path = REPO / "artifacts" / f"i24_validation_{label}.json"
    art = json.loads(path.read_text())
    sim, obs = art["simulated"], art["observed"]
    t_lo, t_hi = (
        float(obs["t_range_s"][0]) - 1800.0 + 600.0,
        float(obs["t_range_s"][1]) - 1800.0 + 600.0,
    )
    n_win, win_s = int(obs["n_windows"]), float(obs["window_s"])
    sections = [float(x) for x in obs["sections_m"]]
    hours = n_win * win_s / 3600.0
    flows = {
        f"{s:.0f}": [float(sum(c[k])) / hours for c in sim["counts_per_replicate"]]
        for k, s in enumerate(sections)
    }
    metas, zones, problems = [], [], []
    steps = None
    for seed, rd in zip(sim["seeds"], sim["run_dirs"], strict=True):
        run_dir = resolve(rd)
        meta_path = run_dir / "meta.json"
        if not meta_path.is_file():
            problems.append(f"{seed}: no meta.json under {run_dir}")
            metas.append(None)
            zones.append(None)
            continue
        meta = json.loads(meta_path.read_text())
        metas.append(meta)
        b = meta.get("boundary") or {}
        if factor is None and "limit_factor" in b:
            problems.append(f"{seed}: the reference run records limit_factor {b['limit_factor']}")
        if factor is not None and b.get("limit_factor") != factor:
            problems.append(
                f"{seed}: limit_factor {b.get('limit_factor')} recorded, {factor} expected"
            )
        if meta.get("config_hash") != art["config_hash"]:
            problems.append(
                f"{seed}: meta config_hash {meta.get('config_hash')} != {art['config_hash']}"
            )
        if steps is None:
            steps = meta["config"]["network"]["boundary"]["steps"]
        z = (
            zone_speeds(run_dir, zone, t_lo, t_hi)
            if (run_dir / "edges.parquet").is_file()
            else None
        )
        zones.append(z)
        if z is None:
            problems.append(f"{seed}: no edges.parquet under {run_dir}")
        else:
            if not z["schedule_windows"]["cells_inside_one_window"]:
                problems.append(f"{seed}: edges.parquet cells straddle the schedule's 30-s windows")
            # a simulated zone has no observation gaps: a window without density means the zone was
            # empty, which the forward/backward fill would hide (review 2026-10-07)
            if z["schedule_windows"]["filled"]:
                problems.append(
                    f"{seed}: {z['schedule_windows']['filled']} of the boundary zone's "
                    f"{z['schedule_windows']['n']} 30-s windows have no density (the zone was empty; "
                    "the zone speed filled them)"
                )
    coll = [int(x) for x in sim.get("n_collisions_per_replicate") or []]
    coll_meta = [None if m is None else m.get("n_collisions") for m in metas]
    if (
        coll
        and coll_meta
        and any(c is not None and c != a for c, a in zip(coll_meta, coll, strict=True))
    ):
        problems.append("n_collisions_per_replicate disagrees with meta.json n_collisions")
    brk = load_braking(label, runs_root)
    emergency = None
    if brk is not None:
        per = [brk["per_seed"].get(str(s), {}) for s in sim["seeds"]]
        if all("below_ms2" in p for p in per):
            emergency = [int(p["below_ms2"][EMERGENCY]) for p in per]
    crit = {r["name"]: r for r in art["criteria"]}
    obs_seg = np.asarray(obs["segment_speeds_ms"], float)
    sim_mean = np.asarray(sim["segment_speeds_ms_mean"], float)
    rm15 = rmspe(agg_windows(sim_mean, WINDOWS_15MIN), agg_windows(obs_seg, WINDOWS_15MIN))
    rm15_rep = [
        rmspe(agg_windows(np.asarray(f, float), WINDOWS_15MIN), agg_windows(obs_seg, WINDOWS_15MIN))
        for f in sim["segment_speeds_ms_per_replicate"]
    ]
    if steps and not schedule_on_grid(steps, t_lo, t_hi):
        problems.append(
            "the boundary schedule is not one step per 30 s from the study window's start: A2's zone "
            "speed (30-s window means) does not mirror its study-window mean"
        )
    v_sched = schedule_mean(steps, t_lo, t_hi) if steps else math.nan
    sched_win = (
        [
            schedule_mean(steps, t_lo + i * ZONE_WINDOW_S, t_lo + (i + 1) * ZONE_WINDOW_S)
            for i in range(int((t_hi - t_lo) // ZONE_WINDOW_S))
        ]
        if steps
        else []
    )
    return {
        "label": label,
        "artifact": {
            "path": str(path.relative_to(REPO)),
            "sha256": sha256(path),
            "created_at": art.get("created_at"),
        },
        "scenario": art.get("scenario"),
        "config_hash": art["config_hash"],
        "seeds": [int(s) for s in sim["seeds"]],
        "problems": problems,
        "study_window_sim_s": [t_lo, t_hi],
        "flows_2h_veh_h": flows,
        "observed_2h_veh_h": {
            f"{s:.0f}": float(np.mean(obs["hourly_flows_veh_h_recommended"][k]))
            for k, s in enumerate(sections)
        },
        "collisions": coll,
        "emergency_steps": emergency,
        "braking_file": None if brk is None else str(runs_root / label / "hard_braking.json"),
        "zone_speed_ms": [None if z is None else z["speed_ms"] for z in zones],
        "zone_edie_2h_ms": [None if z is None else z["edie_2h_ms"] for z in zones],
        "zone_windows_filled": [
            None if z is None else z["schedule_windows"]["filled"] for z in zones
        ],
        "zone_per_window_ms": [None if z is None else z["per_window_ms"] for z in zones],
        "zone_cells": next((z["cells"] for z in zones if z is not None), None),
        "schedule_mean_ms": v_sched,
        "schedule_per_window_ms": sched_win,
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
        "_art": art,
    }


def reproduction(ref: dict, committed_path: Path) -> dict[str, Any]:
    """The same-code reference against the committed step-3 battery of the same scenario (reported)."""
    if not committed_path.is_file():
        return {"committed": str(committed_path), "available": False}
    c = json.loads(committed_path.read_text())
    a = ref["_art"]
    cm = np.asarray(c["simulated"]["counts_mean"], float)
    am = np.asarray(a["simulated"]["counts_mean"], float)
    cs = np.asarray(c["simulated"]["segment_speeds_ms_mean"], float)
    as_ = np.asarray(a["simulated"]["segment_speeds_ms_mean"], float)
    return {
        "committed": {
            "path": str(committed_path.relative_to(REPO)),
            "sha256": sha256(committed_path),
        },
        "same_config_hash": c["config_hash"] == a["config_hash"],
        "same_seeds": [int(s) for s in c["seeds"]] == [int(s) for s in a["seeds"]],
        "counts_identical": bool(np.array_equal(cm, am)),
        "max_abs_count_diff": float(np.max(np.abs(cm - am))) if cm.shape == am.shape else None,
        "segment_speeds_identical": bool(
            np.array_equal(np.nan_to_num(cs, nan=-1), np.nan_to_num(as_, nan=-1))
        ),
        "realised_identical": c["simulated"]["demand_realized_fraction"]
        == a["simulated"]["demand_realized_fraction"],
        "rmspe_5min": {"committed": c["rmspe"]["value"], "here": a["rmspe"]["value"]},
    }


def evaluate_arm(name: str, ref: dict, b1: dict, committed: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"arm": name, "reference": None, "b1": None}
    for key, bt in (("reference", ref), ("b1", b1)):
        out[key] = {
            k: v
            for k, v in bt.items()
            if not k.startswith("_") and k not in ("zone_per_window_ms",)
        }
    same_seeds = ref["seeds"] == b1["seeds"]
    out["same_seeds"] = same_seeds
    out["problems"] = (
        ref["problems"]
        + b1["problems"]
        + ([] if same_seeds else ["the two batteries ran different seeds"])
    )

    # A1
    coll_ok = bool(b1["collisions"]) and all(c == 0 for c in b1["collisions"])
    if ref["emergency_steps"] is None or b1["emergency_steps"] is None:
        brake = {"verdict": None, "not_computed": "braking counts missing (hard_braking.py output)"}
    else:
        rt, bt = sum(ref["emergency_steps"]), sum(b1["emergency_steps"])
        brake = {
            "verdict": bt <= rt,
            "b1_total": bt,
            "reference_total": rt,
            "per_run_paired_b1_minus_ref": paired(b1["emergency_steps"], ref["emergency_steps"]),
        }
    a1_verdict = None if brake["verdict"] is None else bool(coll_ok and brake["verdict"])
    if not coll_ok:
        a1_verdict = False
    a1 = {
        "rule": "0 collisions in every run; steps below -8.9 m/s^2 not above the reference",
        "collisions_b1": b1["collisions"],
        "collisions_reference": ref["collisions"],
        "collisions_ok": coll_ok,
        "hard_braking": brake,
        "verdict": a1_verdict,
    }

    # A2
    k = f"{A2_SECTION_M:.0f}"
    f_b1, f_ref = ci(b1["flows_2h_veh_h"][k]), ci(ref["flows_2h_veh_h"][k])
    flow_ok = None if f_b1 is None else bool(A2_BAND[0] <= f_b1[0] <= A2_BAND[1])
    v_sched = b1["schedule_mean_ms"]
    gaps = {
        side: [
            int(s)
            for s, v in zip(bt["seeds"], bt["zone_speed_ms"], strict=True)
            if v is None or not math.isfinite(v)
        ]
        for side, bt in (("b1", b1), ("reference", ref))
    }
    if any(gaps.values()):
        zone = {
            "verdict": None,
            "not_computed": "no zone speed for some replicates (meta.json or edges.parquet missing, or no "
            "cell in the zone): A2 reads every replicate of both batteries",
            "seeds_without_zone_speed": gaps,
        }
    elif not math.isfinite(v_sched):
        zone = {"verdict": None, "not_computed": "no boundary schedule recorded in meta.json"}
    else:
        zb, zr = ci(b1["zone_speed_ms"]), ci(ref["zone_speed_ms"])
        assert zb is not None and zr is not None
        eb, er = (zb[0] - v_sched) * 3.6, (zr[0] - v_sched) * 3.6
        eb2, er2 = ci(b1["zone_edie_2h_ms"]), ci(ref["zone_edie_2h_ms"])
        zone = {
            "verdict": bool(abs(eb) < abs(er)),
            "estimator": "per replicate, the unweighted mean of the 30-s window Edie speeds on the schedule's "
            "grid (as the schedule's study-window mean); then the replicate mean",
            "schedule_mean_kmh": v_sched * 3.6,
            "b1_zone_kmh": [x * 3.6 for x in zb],
            "reference_zone_kmh": [x * 3.6 for x in zr],
            "b1_error_kmh": eb,
            "reference_error_kmh": er,
            "paired_zone_speed_b1_minus_ref_kmh": [
                x * 3.6 for x in paired(b1["zone_speed_ms"], ref["zone_speed_ms"]) or []
            ],
            "window_rmspe_reported": {
                "b1": _window_rmspe(b1),
                "reference": _window_rmspe(ref),
            },
            "edie_2h_reported": {
                "what": "one Edie speed over the zone's cells in the whole study window (vehicle-time "
                "weighted; A2's first estimator, replaced 2026-10-07 before any p12 result was read)",
                "b1_kmh": None if eb2 is None else [x * 3.6 for x in eb2],
                "reference_kmh": None if er2 is None else [x * 3.6 for x in er2],
                "b1_error_kmh": None if eb2 is None else (eb2[0] - v_sched) * 3.6,
                "reference_error_kmh": None if er2 is None else (er2[0] - v_sched) * 3.6,
            },
            "windows_filled": {
                "b1": b1["zone_windows_filled"],
                "reference": ref["zone_windows_filled"],
            },
        }
    a2 = {
        "rule": f"5,400-m 2-h flow within {A2_BAND[0]:,.0f}-{A2_BAND[1]:,.0f} veh/h; boundary-zone mean speed "
        "error smaller in magnitude than the reference's",
        "flow_5400_b1": f_b1,
        "flow_5400_reference": f_ref,
        "flow_5400_paired_b1_minus_ref": paired(b1["flows_2h_veh_h"][k], ref["flows_2h_veh_h"][k]),
        "flow_ok": flow_ok,
        "zone": zone,
        "verdict": None
        if flow_ok is None or zone["verdict"] is None
        else bool(flow_ok and zone["verdict"]),
    }

    # A3
    rb, rr = ci(b1["realised"]), ci(ref["realised"])
    a3 = {
        "rule": "realised demand >= the reference's (FRISCO_PROTOCOL Amendment 2 clarification)",
        "b1": rb,
        "reference": rr,
        "paired_b1_minus_ref": paired(b1["realised"], ref["realised"]),
        "verdict": bool(rb[0] >= rr[0]),
        "amendment2_tolerance_reading": {
            "rule": f"b1 mean >= reference mean - {A3_TOLERANCE}",
            "verdict": bool(rb[0] >= rr[0] - A3_TOLERANCE),
        },
    }

    # A4
    ref_pass = bool(ref["wave_row"]["passed"])
    verdict_ok = bool(b1["wave_row"]["passed"]) if ref_pass else None
    fronts = {}
    for det in ("standard", "stripe"):
        mb, mr = ci(b1[f"fronts_{det}"]), ci(ref[f"fronts_{det}"])
        fronts[det] = {
            "b1": mb,
            "reference": mr,
            "floor": mr[0] * FRONT_FLOOR,
            "paired_b1_minus_ref": paired(b1[f"fronts_{det}"], ref[f"fronts_{det}"]),
            "ok": bool(mb[0] >= mr[0] * FRONT_FLOOR),
        }
    fronts_ok = all(f["ok"] for f in fronts.values())
    a4 = {
        "rule": "the profile's wave verdict unchanged where it passes today; backward fronts per replicate not "
        "below the reference by more than a third (standard and stripe detectors)",
        "wave_verdict": {
            "reference": ref["wave_row"],
            "b1": b1["wave_row"],
            "reference_passes": ref_pass,
            "ok": verdict_ok,
            "note": None
            if ref_pass
            else "the reference fails the wave criterion here; the verdict half does not apply",
        },
        "fronts": fronts,
        "verdict": bool(fronts_ok and (verdict_ok is not False)),
    }

    # A5
    a5 = {
        "rule": f"15-min segment-speed RMSPE not above the reference + {RMSPE_MARGIN}",
        "b1": b1["rmspe_15min"],
        "reference": ref["rmspe_15min"],
        "limit": ref["rmspe_15min"] + RMSPE_MARGIN,
        "per_replicate_paired_b1_minus_ref": paired(
            b1["rmspe_15min_per_replicate"], ref["rmspe_15min_per_replicate"]
        ),
        "verdict": bool(b1["rmspe_15min"] <= ref["rmspe_15min"] + RMSPE_MARGIN),
    }

    # reported, not gating
    peaks = {}
    for s in PEAK_SECTIONS_M:
        kk = f"{s:.0f}"
        obs_q = b1["observed_2h_veh_h"][kk]
        mb = ci(b1["flows_2h_veh_h"][kk])
        mr = ci(ref["flows_2h_veh_h"][kk])
        peaks[kk] = {
            "observed_recommended": obs_q,
            "geh5_threshold_veh_h": _geh_threshold(obs_q),
            "b1": mb,
            "reference": mr,
            "paired_b1_minus_ref": paired(b1["flows_2h_veh_h"][kk], ref["flows_2h_veh_h"][kk]),
            "geh_b1": geh(mb[0], obs_q),
            "geh_reference": geh(mr[0], obs_q),
        }
    out["criteria"] = {"A1": a1, "A2": a2, "A3": a3, "A4": a4, "A5": a5}
    out["reported"] = {
        "peak_sections": peaks,
        "geh_lt5_share": {"b1": b1["geh_lt5_share"], "reference": ref["geh_lt5_share"]},
        "rmspe_5min": {"b1": b1["rmspe_5min"], "reference": ref["rmspe_5min"]},
        "reference_reproduces_step3": reproduction(ref, committed),
        "lane_shares": {
            "computed": False,
            "why": "per-lane section crossings are recorded only in the trajectories (lane at each section); "
            "meta.json, vehicles.parquet and the battery artifact do not carry them",
        },
    }
    return out


def _window_rmspe(bt: dict) -> float | None:
    """RMSPE of the replicate-mean 5-min zone speeds against the schedule's 5-min means (reported)."""
    rows = [w for w in bt["zone_per_window_ms"] if w is not None]
    if not rows or not bt["schedule_per_window_ms"]:
        return None
    arr = np.asarray([[np.nan if v is None else v for v in w] for w in rows], float)
    mean = np.nanmean(arr, axis=0)
    return rmspe(mean, np.asarray(bt["schedule_per_window_ms"], float))


def _geh_threshold(obs_q: float) -> float:
    """The simulated flow below the observed one at which GEH = 5 (the reported thresholds 6,225 / 6,238)."""
    lo, hi = 0.0, obs_q
    for _ in range(100):
        mid = (lo + hi) / 2
        if geh(mid, obs_q) > 5.0:
            lo = mid
        else:
            hi = mid
    return hi


def factor_provenance() -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "b1_factor", REPO / "scripts" / "boundary_limit_factor.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return {name: mod.corridor_factor(name) for name in ("i24", "i94")}


def evaluate(arms: list[list[str]], out: Path, runs_root: Path, factor: float) -> dict[str, Any]:
    zone = zone_geometry()
    results: dict[str, Any] = {}
    for name, ref_label, b1_label, committed in arms:
        missing = [
            lb
            for lb in (ref_label, b1_label)
            if not (REPO / "artifacts" / f"i24_validation_{lb}.json").is_file()
        ]
        if missing:
            results[name] = {"arm": name, "missing": missing}
            continue
        ref = battery(ref_label, runs_root, zone, None)
        b1 = battery(b1_label, runs_root, zone, factor)
        results[name] = evaluate_arm(name, ref, b1, REPO / committed)

    def crit(arm: str, c: str) -> bool | None:
        r = results.get(arm) or {}
        return None if "criteria" not in r else r["criteria"][c]["verdict"]

    a1_all = [crit(a, "A1") for a in results]
    a1 = None if any(v is None for v in a1_all) or not a1_all else all(a1_all)
    i24 = {
        "A1": a1,
        "A2": crit("dc_refit", "A2"),
        "A3": crit("canonical", "A3"),
        "A4": crit("canonical", "A4"),
        "A5": crit("canonical", "A5"),
    }
    # A1 reads every arm, so every arm feeds the reading: an arm with problems blocks it
    blocked = {a: list(r["problems"]) for a, r in results.items() if r.get("problems")}
    holds = None if blocked or any(v is None for v in i24.values()) else all(i24.values())
    doc = {
        "schema": "flowstate.boundary_b1_corridor/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "amendment": "B1 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.3), PROPOSED, not adopted; criteria fixed before any run",
        "limit_factor_applied": factor,
        "factor": factor_provenance(),
        "zone": zone,
        "arms": results,
        "adoption": {
            "reading": "A1 on every B1 run of every arm (the row says 'every run'); A2 on dc_refit (§8.3); "
            "A3-A5 on the canonical arm (§8.3: 'hold on the I-24 canonical arm'); the other arms' A3-A5 are "
            "reported under arms",
            "i24": i24,
            "i24_holds": holds,
            "blocked_by_problems": blocked,
            "i94": "not run: the I-94 fleet is EIDM, for which B1's factor is not defined "
            "(scripts/boundary_limit_factor.py: applies false); §8.3 names I-94 in the adoption rule, so "
            "whether B1 can be adopted on I-24 alone is the owner's call",
        },
        "follow_up": "only if A1-A5 hold: the FHWA re-sequence of the demand level on the adopted arm (§8.3); "
        "not part of this stage",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mc = sub.add_parser("make-copy")
    mc.add_argument("--source", type=Path, required=True)
    mc.add_argument("--out", type=Path, required=True)
    mc.add_argument("--factor", type=float, required=True)
    mc.add_argument("--source-hash", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument(
        "--arm", nargs=4, action="append", required=True, metavar=("NAME", "REF", "B1", "COMMITTED")
    )
    ev.add_argument("--out", type=Path, required=True)
    ev.add_argument("--runs-root", type=Path, default=REPO / "runs" / "i24_validation")
    ev.add_argument("--factor", type=float, default=1.2185)
    args = ap.parse_args()
    if args.cmd == "make-copy":
        src = args.source if args.source.is_absolute() else REPO / args.source
        dst = args.out if args.out.is_absolute() else REPO / args.out
        print(make_copy(src, dst, args.factor, args.source_hash))
        return
    doc = evaluate(args.arm, args.out, args.runs_root, args.factor)
    for name, r in doc["arms"].items():
        if "criteria" not in r:
            print(f"{name}: missing {r.get('missing')}")
            continue
        print(
            name,
            {c: v["verdict"] for c, v in r["criteria"].items()},
            "problems:",
            len(r["problems"]),
        )
    print("I-24 adoption reading:", doc["adoption"]["i24"], "holds:", doc["adoption"]["i24_holds"])
    blocked = doc["adoption"]["blocked_by_problems"]
    if blocked:
        for name, problems in blocked.items():
            print(f"BLOCKED by {name}: " + "; ".join(problems), file=sys.stderr)
        sys.exit(EXIT_BLOCKED)


if __name__ == "__main__":
    main()
