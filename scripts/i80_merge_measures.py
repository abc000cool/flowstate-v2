"""E11's merge measures: NGSIM I-80's on-ramp entering changes and a replica's, one extractor.

docs/PRE_FRISCO_PROGRAM.md, "E11". The measured merge model's quantities come from I-24 (critical
gaps, partner speeds) and US-101 (relaxation) (docs/MERGE_MODEL.md §2); E11 compares the kept
configuration and ``merge: measured`` with a third site, NGSIM I-80's Powell Street on-ramp, on
which nothing was ever fitted. This script measures, for the on-ramp's *entering* changes (lane 7
to lane 6 inside the acceleration lane), with the calibration package's committed extractors:

* **accepted gaps** — the lead and lag time gaps at the change
  (:func:`calibration.lane_change_gaps.lane_change_gaps`: bumper to bumper, the lead over the
  changer's speed, the lag over the new follower's), the changes sampled for the critical gaps;
* **critical gaps** — Troutbeck's maximum likelihood, the joint lead–lag estimator, log-normal,
  with 200-resample bootstrap 95 % intervals over the drivers
  (:func:`calibration.lane_change_gaps.gap_sequences`, :mod:`calibration.critical_gap`; the 10 s
  lookback at 1 s, bounded by the acceleration lane), the estimator the model's inputs come from;
* **partner speeds** — at the change, the changer's speed minus its new follower's (follower
  side) and the new leader's minus the changer's (leader side), on the sides car-following at the
  change (``rel_speed_ms`` of :func:`calibration.lane_change_relaxation.post_change_gaps`, the
  quantity MERGE_MODEL §2's offset δ and §3(b) read);
* **gap ratios** at 0 / 2 / 5 / 10 s after the change — each side's time gap over the
  population's normal time gap at the rear vehicle's speed (``ratio_pop``, §3(c)'s measure),
  the normal read off the same table (:func:`calibration.lane_change_relaxation.normal_time_gaps`
  on lanes 1-7 of the site); ``ratio_own`` beside;
* **acceleration-lane speed** by 50-m bin from the gore (every lane-7 sample in the lane).

The observed side has 95 % bootstrap intervals (200 resamples of the changes, percentile; the
critical gaps' resample the drivers inside the estimator). The simulated side gives each seed's
point values (the readout's 20-seed intervals are formed from them) and the 20 seeds pooled.

**Like for like.** Both sides read the site's span ``[0, L)`` on I-80's ``local_y`` axis (the
replica's coordinates shifted by its site start; rows outside the camera span are dropped, as
the camera drops them), the same lanes (the replica's SUMO lane indices mapped to I-80's band
numbers: 1 = leftmost, 7 = the acceleration lane), the same merge zone (the replica's attach edge
is built on the zone read off the data, ``scripts/i80_build_replica.py``), the same block
wall-clock windows (``scripts/i80_data.py analysis_windows``; sim time = wall + warm-up) and the
same extractor parameters. SUMO crosses some entrants in the step they reach the attach edge,
which the trajectory table (it starts a ramp vehicle on the corridor) would miss: WP-82's fix
restores those crossings (``scripts/lane_change_relaxation.py add_arrival_crossings``).

Subcommands (repository root)::

    # VM: the observed side (reads the prepared periods of scripts/i80_data.py)
    uv run --no-sync python scripts/i80_merge_measures.py observed \\
        --data-summary artifacts/i80_data.json --out artifacts/i80_merge_observed.json
    # VM: one arm's runs (corridor_e11.py run writes the manifest); trajectories never leave the VM
    uv run --no-sync python scripts/i80_merge_measures.py simulated \\
        --manifest runs/i80_merge/kept/MANIFEST.json --observed artifacts/i80_merge_observed.json \\
        --inputs artifacts/i80_replica_inputs.json --procs 14 --out artifacts/i80_merge_sim_kept.json
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import i80_data  # noqa: E402

from calibration.critical_gap import (  # noqa: E402
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    driver_gaps,
    fit_groups,
    rejected_points,
    select_drivers,
)
from calibration.lane_change_gaps import (  # noqa: E402
    DEFAULT_LOOKBACK_S,
    DEFAULT_SAMPLE_EVERY_S,
    Zone,
    gap_sequences,
    lane_change_gaps,
)
from calibration.lane_change_relaxation import (  # noqa: E402
    NormalTimeGaps,
    PostChangeGaps,
    concat_results,
    normal_time_gaps,
    post_change_gaps,
    with_population_ratio,
)
from flowstate_core.rng import make_rng, spawn_seeds  # noqa: E402

REPO_ROOT = SCRIPTS.parent
MAINLINE: Final[tuple[int, ...]] = i80_data.MAINLINE_LANES
AUX: Final[tuple[int, ...]] = (i80_data.RAMP_LANE,)
MOVEMENT: Final[str] = "entering"
ZONE_KIND: Final[str] = "merge"
ZONE_NAME: Final[str] = "I-80 EB Powell St acceleration lane"
OFFSETS_S: Final[tuple[float, ...]] = (0.0, 2.0, 5.0, 10.0)
"""Offsets after the change at which the gap ratios are read [s] (E11's 0 / 2 / 5 / 10 s)."""
STEP_S: Final[float] = 1.0
"""Walk interval of the post-change reading and of the population normal [s]."""
SPEED_BIN_M: Final[float] = 50.0
SIDES: Final[tuple[str, ...]] = ("follower", "leader")
OBSERVED_DT_S: Final[float] = i80_data.NGSIM_DT_S
N_BOOT: Final[int] = DEFAULT_N_BOOT
SEED: Final[int] = DEFAULT_SEED
STAT_NAMES: Final[tuple[str, ...]] = (
    "accepted_lead",
    "accepted_lag",
    "partner_follower",
    "partner_leader",
    *(f"ratio_{side}_{int(o)}" for side in SIDES for o in OFFSETS_S),
    "speed_profile",
    "critical_gaps",
)
"""The bootstrapped statistics, in the order their seeds are spawned from :data:`SEED`."""
ZONE_TOL_M: Final[float] = 2.0
"""Largest difference between a replica's merge zone (on the data axis) and the observed zone."""

METHOD: Final[dict[str, str]] = {
    "changes": "entering (lane 7 to lane 6) inside the merge zone, confirmed and not suspect "
    "(calibration.lane_change_gaps: debounce 1 s, a neighbour within 0.5 m marks a change "
    "suspect), made inside the block's analysis windows",
    "accepted_gap_s": "lead and lag time gaps at the change of the changes sampled for the "
    "critical gaps (calibration.critical_gap.driver_gaps a_lead_s / a_lag_s), finite values "
    "(a side with no vehicle within 200 m has none)",
    "critical_gap_s": "joint lead-lag Troutbeck maximum likelihood, log-normal "
    "(calibration.critical_gap.fit_groups by zone kind and movement, the 'all' speed class), "
    "rejected gaps over a 10 s lookback at 1 s inside the acceleration lane; the observed "
    "interval is the 200-resample driver bootstrap's percentile 95 % interval of the median",
    "partner_speed_ms": "rel_speed_ms at the change of calibration.lane_change_relaxation."
    "post_change_gaps: follower side = changer - new follower, leader side = new leader - "
    "changer, on the sides car-following at the change",
    "gap_ratio": "ratio_pop: the side's time gap over the population's median car-following "
    "time gap in the rear vehicle's 2-m/s speed bin, the population being the same table's "
    "lanes 1-7 over the site and the windows (normal_time_gaps, 1 s grid); ratio_own: over the "
    "rear vehicle's own median time gap 30 to 5 s before the change",
    "accel_lane_speed_ms": "lane-7 samples inside the merge zone and the windows, by 50-m bin "
    "from the gore",
    "bootstrap": "observed: 200 resamples with replacement of the changes (sides), percentile "
    "95 % interval of the median, seeds flowstate_core.rng.spawn_seeds(20260925, n) in a fixed "
    "order of the statistics",
}


# --- one table -------------------------------------------------------------------------------


@dataclass
class Part:
    """One table's extraction (a recording period, or a simulated run)."""

    records: pd.DataFrame
    mask: np.ndarray
    drivers: pd.DataFrame
    points: pd.DataFrame
    post: PostChangeGaps
    normal: NormalTimeGaps
    speeds: pd.DataFrame
    counts: dict[str, Any] = field(default_factory=dict)


def in_windows(t: np.ndarray, windows: Sequence[tuple[float, float]]) -> np.ndarray:
    """Whether each time lies in any half-open window ``[lo, hi)``."""
    tt = np.asarray(t, dtype=np.float64)
    out = np.zeros(tt.shape, dtype=bool)
    for lo, hi in windows:
        out |= (tt >= lo) & (tt < hi)
    return out


def _empty_drivers() -> pd.DataFrame:
    cols = ["change", "t", "veh_id", "zone", "zone_kind", "movement", "v", "lag_v", "confirmed"]
    cols += ["suspect", "lead_closing_ms", "lag_closing_ms", "a_lead_s", "a_lag_s", "r_lead_s"]
    cols += ["r_lag_s", "n_rejected_gaps", "n_rejected_samples", "lookback_s"]
    return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})


def extract(
    df: pd.DataFrame,
    zone: Zone,
    *,
    dt_s: float,
    windows: Sequence[tuple[float, float]],
    x_range: tuple[float, float],
    arrival_keys: set[tuple[str, float]] | None = None,
) -> Part:
    """Every extractor on one table: changes, gap histories, post-change sides, normal, speeds.

    Args:
        df: ``t, veh_id, x, lane, v, length`` on one shared time grid, band lanes, I-80's axis.
        zone: The merge zone (the acceleration lane).
        dt_s: Sampling interval of ``df`` [s].
        windows: Half-open windows on ``df``'s ``t`` in which a change counts.
        x_range: The site span; a change outside it is dropped.
        arrival_keys: ``(veh_id, t)`` of changes restored by WP-82's fix: confirmed and marked
            ``arrival_crossing``.
    """
    gaps = lane_change_gaps(
        df, [zone], mainline_lanes=MAINLINE, aux_lanes=AUX, dt_s=dt_s, x_range_m=x_range
    )
    rec = gaps.records
    if arrival_keys:
        arrival = np.array(
            [
                (str(v), round(float(t), 6)) in arrival_keys
                for v, t in zip(rec["veh_id"], rec["t"], strict=True)
            ],
            dtype=bool,
        )
    else:
        arrival = np.zeros(len(rec), dtype=bool)
    rec["arrival_crossing"] = arrival
    if len(rec):
        rec.loc[arrival, "confirmed"] = True
        mask = (
            (rec["movement"].to_numpy() == MOVEMENT)
            & (rec["zone_kind"].to_numpy() == ZONE_KIND)
            & rec["confirmed"].astype(bool).to_numpy()
            & ~rec["suspect"].astype(bool).to_numpy()
            & in_windows(rec["t"].to_numpy(dtype=np.float64), windows)
        )
    else:
        mask = np.zeros(0, dtype=bool)
    seq = gap_sequences(df, rec, changes=mask, zones=[zone], dt_s=dt_s)
    if len(seq.samples):
        drivers = driver_gaps(rec, seq.samples)
        points = rejected_points(seq.samples)
    else:
        drivers = _empty_drivers()
        points = pd.DataFrame({"change": [], "lead_s": [], "lag_s": []})
    post = post_change_gaps(df, rec, changes=mask, offsets_s=OFFSETS_S, step_s=STEP_S, dt_s=dt_s)
    span = (min(lo for lo, _ in windows), max(hi for _, hi in windows))
    normal = normal_time_gaps(
        df,
        dt_s=dt_s,
        step_s=STEP_S,
        window_s=span,
        x_range_m=x_range,
        lanes=(*MAINLINE, *AUX),
    )
    t = df["t"].to_numpy(dtype=np.float64)
    x = df["x"].to_numpy(dtype=np.float64)
    on_aux = (
        (df["lane"].to_numpy(dtype=np.int64) == AUX[0])
        & (x >= zone.x_lo_m)
        & (x < zone.x_hi_m)
        & in_windows(t, windows)
    )
    speeds = pd.DataFrame(
        {"x_rel": x[on_aux] - zone.x_lo_m, "v": df["v"].to_numpy(dtype=np.float64)[on_aux]}
    )
    counts = {
        "rows": len(df),
        "extraction": gaps.counts,
        "n_entering_in_zone": int(
            np.sum(
                (rec["movement"].to_numpy() == MOVEMENT)
                & (rec["zone_kind"].to_numpy() == ZONE_KIND)
            )
        )
        if len(rec)
        else 0,
        "n_selected": int(mask.sum()),
        "n_arrival_crossings": int(arrival.sum()),
        "sequences": seq.counts,
        "post_change": post.counts,
    }
    return Part(rec, mask, drivers, points, post, normal, speeds, counts)


# --- statistics ---------------------------------------------------------------------------------


def _r(x: float | None, digits: int = 4) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), digits)


def median_ci(values: np.ndarray, *, n_boot: int, seed: int) -> list[float] | None:
    """Percentile 95 % interval of the median over ``n_boot`` resamples with replacement."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size < 2 or n_boot <= 0:
        return None
    rng = make_rng(seed)
    meds = np.median(v[rng.integers(0, v.size, size=(n_boot, v.size))], axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def stats(values: np.ndarray, *, n_boot: int = 0, seed: int = 0) -> dict[str, Any]:
    """``n``, quartiles and (with ``n_boot``) the bootstrap 95 % interval of the median."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    out: dict[str, Any] = {"n": int(v.size), "median": None, "p25": None, "p75": None}
    if v.size:
        q = np.quantile(v, [0.25, 0.5, 0.75])
        out.update({"p25": _r(q[0]), "median": _r(q[1]), "p75": _r(q[2])})
    if n_boot > 0:
        out["ci95"] = median_ci(v, n_boot=n_boot, seed=seed)
    return out


@dataclass
class Pooled:
    """Parts pooled: change ids made unique, the normals summed, ``ratio_pop`` filled."""

    drivers: pd.DataFrame
    points: pd.DataFrame
    post: PostChangeGaps
    normal: NormalTimeGaps
    speeds: pd.DataFrame


def pool(parts: Sequence[Part]) -> Pooled:
    """One table from several (periods or seeds), change ids offset by each part's records."""
    if not parts:
        raise ValueError("nothing to pool")
    drivers, points, off = [], [], 0
    for p in parts:
        if len(p.drivers):
            d = p.drivers.copy()
            d["change"] = d["change"].to_numpy(dtype=np.int64) + off
            drivers.append(d)
        if len(p.points):
            q = p.points.copy()
            q["change"] = q["change"].to_numpy(dtype=np.int64) + off
            points.append(q)
        off += len(p.records)
    normal = parts[0].normal
    for p in parts[1:]:
        normal = normal + p.normal
    post = with_population_ratio(concat_results([p.post for p in parts]), normal)
    return Pooled(
        pd.concat(drivers, ignore_index=True) if drivers else _empty_drivers(),
        pd.concat(points, ignore_index=True)
        if points
        else pd.DataFrame({"change": [], "lead_s": [], "lag_s": []}),
        post,
        normal,
        pd.concat([p.speeds for p in parts], ignore_index=True),
    )


def critical_gaps(
    drivers: pd.DataFrame, points: pd.DataFrame, *, n_boot: int, seed: int
) -> dict[str, Any]:
    """The joint estimator's lead and lag critical gaps of the entering changes (all speeds)."""
    sel = (
        select_drivers(drivers, movements=(MOVEMENT,), zone_kinds=(ZONE_KIND,))
        if len(drivers)
        else drivers
    )
    if len(sel) == 0:
        return {"n_drivers": 0, "fitted": False, "lead": None, "lag": None, "rows": []}
    rows, _ = fit_groups(sel, points, by=("zone_kind", "movement"), n_boot=n_boot, seed=seed)
    row = next(r for r in rows if r["speed_class"] == "all")
    joint = row.get("joint") or {}
    fitted = bool(joint.get("fitted"))

    def side(name: str) -> dict[str, Any] | None:
        if not fitted:
            return None
        j = joint[name]
        ci = (j.get("ci95") or {}).get("median_s")
        return {
            "median": j["median_s"],
            "ci95": ci,
            "at_bound": bool(j["at_bound"]),
            "degenerate": bool(j["degenerate"]),
            "mu": j["mu"],
            "sigma": j["sigma"],
            "n_boot": j.get("n_boot", 0),
        }

    return {
        "n_drivers": int(row["n_drivers"]),
        "fitted": fitted,
        "n_used": joint.get("n_used"),
        "n_with_rejection": joint.get("n_with_rejection"),
        "n_inconsistent": joint.get("n_inconsistent"),
        "converged": joint.get("converged"),
        "lead": side("lead"),
        "lag": side("lag"),
        "rows": rows,
    }


def speed_profile(
    speeds: pd.DataFrame, zone_length_m: float, *, n_boot: int, seed: int
) -> list[dict[str, Any]]:
    """Acceleration-lane speeds by :data:`SPEED_BIN_M` bin from the gore."""
    n_bins = max(math.ceil(zone_length_m / SPEED_BIN_M - 1e-9), 1)
    x = speeds["x_rel"].to_numpy(dtype=np.float64)
    v = speeds["v"].to_numpy(dtype=np.float64)
    seeds = spawn_seeds(seed, n_bins)
    out = []
    for k in range(n_bins):
        lo, hi = k * SPEED_BIN_M, (k + 1) * SPEED_BIN_M
        sel = (x >= lo) & (x < hi)
        vv = v[sel]
        row = {"bin_lo_m": lo, "bin_hi_m": hi, **stats(vv, n_boot=n_boot, seed=seeds[k])}
        row["mean"] = _r(float(vv.mean())) if vv.size else None
        out.append(row)
    return out


def summarize(
    pooled: Pooled, zone: Zone, *, n_boot: int, seed: int, with_critical_ci: bool | None = None
) -> dict[str, Any]:
    """Every E11 measure of a pooled table.

    Args:
        pooled: :func:`pool` output.
        zone: The merge zone (for the speed profile's bins).
        n_boot: Bootstrap resamples of the medians (0: point values only).
        seed: Master seed of the bootstraps (:data:`STAT_NAMES` order).
        with_critical_ci: Bootstrap the critical gaps too (default: when ``n_boot`` > 0).
    """
    seeds = dict(zip(STAT_NAMES, spawn_seeds(seed, len(STAT_NAMES)), strict=True))
    drv = (
        select_drivers(pooled.drivers, movements=(MOVEMENT,), zone_kinds=(ZONE_KIND,))
        if len(pooled.drivers)
        else pooled.drivers
    )
    a_lead = drv["a_lead_s"].to_numpy(dtype=np.float64)
    a_lag = drv["a_lag_s"].to_numpy(dtype=np.float64)
    vals = pooled.post.values
    partner = {
        side: stats(vals[side]["rel_speed_ms"][:, 0], n_boot=n_boot, seed=seeds[f"partner_{side}"])
        for side in SIDES
    }
    ratios: dict[str, dict[str, Any]] = {}
    for side in SIDES:
        ratios[side] = {}
        for k, o in enumerate(pooled.post.offsets_s):
            key = f"{float(o):g}"
            ratios[side][key] = {
                "ratio_pop": stats(
                    vals[side]["ratio_pop"][:, k],
                    n_boot=n_boot,
                    seed=seeds.get(f"ratio_{side}_{int(o)}", seed),
                ),
                "ratio_own": stats(vals[side]["ratio_own"][:, k]),
                "time_gap_s": stats(vals[side]["time_gap_s"][:, k]),
            }
    boot_cg = n_boot if (with_critical_ci is None or with_critical_ci) else 0
    return {
        "n_changes": len(drv),
        "accepted_gap_s": {
            "lead": stats(a_lead, n_boot=n_boot, seed=seeds["accepted_lead"]),
            "lag": stats(a_lag, n_boot=n_boot, seed=seeds["accepted_lag"]),
            "share_no_lead": _r(float(np.mean(np.isinf(a_lead)))) if a_lead.size else None,
            "share_no_lag": _r(float(np.mean(np.isinf(a_lag)))) if a_lag.size else None,
        },
        "critical_gap_s": critical_gaps(
            pooled.drivers, pooled.points, n_boot=boot_cg, seed=seeds["critical_gaps"]
        ),
        "partner_speed_ms": partner,
        "gap_ratio": ratios,
        "accel_lane_speed_ms": speed_profile(
            pooled.speeds,
            zone.x_hi_m - zone.x_lo_m,
            n_boot=n_boot,
            seed=seeds["speed_profile"],
        ),
        "post_change_counts": pooled.post.counts,
    }


# --- the observed side ------------------------------------------------------------------------


def observed_frame(period: pd.DataFrame, label: str, site_length_m: float) -> pd.DataFrame:
    """One prepared I-80 period as the extractors' table: period-local ``t`` (frame x 0.1 s),
    ids prefixed by the period, rows on the site's span."""
    x = period["x"].to_numpy(dtype=np.float64)
    keep = (x >= 0.0) & (x < site_length_m)
    p = period.loc[keep]
    return pd.DataFrame(
        {
            "t": p["t"].to_numpy(dtype=np.float64),
            "veh_id": label + "-" + p["veh_id"].astype(str),
            "x": p["x"].to_numpy(dtype=np.float64),
            "lane": p["lane"].to_numpy(dtype=np.int64),
            "v": p["v"].to_numpy(dtype=np.float64),
            "length": p["length_m"].to_numpy(dtype=np.float64),
        }
    )


def run_observed(args: argparse.Namespace) -> dict[str, Any]:
    """I-80's block: the zone, the windows, the extraction per period, the pooled summary."""
    t_start = time.time()
    summary = json.loads(Path(args.data_summary).read_text())
    block = list(args.block or summary["replica_block"])
    periods = i80_data.load_periods(block)
    frames = [periods[p] for p in block]
    lanes = i80_data.check_lanes(frames)
    length = i80_data.site_length_m(frames)
    lo, hi, zcounts = i80_data.merge_zone_bounds(frames)
    zone = Zone(ZONE_NAME, ZONE_KIND, lo, hi)
    windows = i80_data.analysis_windows(periods, block)
    parts: list[Part] = []
    per_period = []
    for w in windows:
        df = observed_frame(periods[w["period"]], w["period"], length)
        off = float(w["origin_offset_s"])
        part = extract(
            df,
            zone,
            dt_s=OBSERVED_DT_S,
            windows=[(float(w["lo_s"]) - off, float(w["hi_s"]) - off)],
            x_range=(0.0, length),
        )
        parts.append(part)
        per_period.append({**w, "counts": part.counts})
        print(
            f"{w['period']}: rows {len(df):,}, entering changes selected {part.counts['n_selected']}"
            f" ({time.time() - t_start:.0f} s)",
            flush=True,
        )
    pooled = pool(parts)
    measures = summarize(pooled, zone, n_boot=args.n_boot, seed=args.seed)
    art = {
        "schema_version": 1,
        "kind": "observed",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i80_merge_measures.py",
        "code": i80_data.git_head(),
        "code_dirty": i80_data.git_dirty(),
        "data_hash": summary["data_hash"],
        "data_version": summary["data_version"],
        "citation": i80_data.CITATION,
        "block": block,
        "site_length_m": length,
        "zone": zone.to_dict(),
        "zone_counts": zcounts,
        "lanes": {**lanes, "mainline": list(MAINLINE), "aux": list(AUX)},
        "windows": [{"period": w["period"], "lo_s": w["lo_s"], "hi_s": w["hi_s"]} for w in windows],
        "periods": per_period,
        "parameters": {
            "dt_s": OBSERVED_DT_S,
            "offsets_s": list(OFFSETS_S),
            "step_s": STEP_S,
            "lookback_s": DEFAULT_LOOKBACK_S,
            "sample_every_s": DEFAULT_SAMPLE_EVERY_S,
            "speed_bin_m": SPEED_BIN_M,
            "n_boot": args.n_boot,
            "seed": args.seed,
        },
        "method": METHOD,
        "measures": measures,
        "limitations": [
            "Raw NGSIM (not the Montanino-Punzo reconstruction): positions and speeds carry the "
            "raw data's noise; gaps are measured positions and time gaps divide by the noisy "
            "v_Vel (reconstructed NGSIM is out of scope by owner rule).",
            "Lane 1 is an HOV lane in the recording; the replica has no HOV rule.",
            "The block's periods are stitched at the switch (each period's last recorded "
            "upstream entry); changes after a period's switch are read from the next period only.",
            "One site, one afternoon; n is reported with every interval.",
        ],
        "wall_s": round(time.time() - t_start, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out}", flush=True)
    return art


# --- the simulated side -----------------------------------------------------------------------


def data_axis(
    x_sim: np.ndarray, prov: Mapping[str, Any], edge_spans_data: Mapping[str, Sequence[float]]
) -> np.ndarray:
    """Run positions mapped onto I-80's ``local_y`` axis, edge by edge.

    The knots are the corridor edges' starts: on the run's axis (``prov["edge_offsets_m"]``, the
    compiled lengths) and on the data axis (the builder's spans, the measured lengths). Between
    knots the map is linear (netconvert's projected lengths differ from the measured ones by a
    fraction of a metre); past the last knot it continues at slope 1.
    """
    edges = [str(e) for e in prov["edges"]]
    missing = [e for e in edge_spans_data if e not in edges]
    if missing:
        raise ValueError(f"edges {missing} of the replica's layout are not on the run's corridor")
    order = [e for e in edges if e in edge_spans_data]
    sim_k = np.array([float(prov["edge_offsets_m"][edges.index(e)]) for e in order])
    data_k = np.array([float(edge_spans_data[e][0]) for e in order])
    if np.any(np.diff(sim_k) <= 0) or np.any(np.diff(data_k) <= 0):
        raise ValueError("the edge knots are not increasing")
    x = np.asarray(x_sim, dtype=np.float64)
    out = np.interp(x, sim_k, data_k)
    past = x > sim_k[-1]
    out[past] = data_k[-1] + (x[past] - sim_k[-1])
    before = x < sim_k[0]
    out[before] = data_k[0] + (x[before] - sim_k[0])
    return out


def sim_on_data_axis(
    df: pd.DataFrame,
    zones: Sequence[Zone],
    prov: Mapping[str, Any],
    *,
    edge_spans_data: Mapping[str, Sequence[float]],
    site_length_m: float,
) -> tuple[pd.DataFrame, Zone]:
    """A band-lane simulated table moved onto I-80's axis (:func:`data_axis`), cut to the camera
    span ``[0, site_length_m)``, with the replica's merge zone on the same axis."""
    merges = [z for z in zones if z.kind == ZONE_KIND]
    if len(merges) != 1:
        raise ValueError(f"expected one merge zone, found {[z.to_dict() for z in zones]}")
    z = merges[0]
    out = df.copy()
    out["x"] = data_axis(out["x"].to_numpy(dtype=np.float64), prov, edge_spans_data)
    x = out["x"].to_numpy(dtype=np.float64)
    out = out.loc[(x >= 0.0) & (x < site_length_m)].reset_index(drop=True)
    lo, hi = data_axis(np.array([z.x_lo_m, z.x_hi_m]), prov, edge_spans_data)
    return out, Zone(ZONE_NAME, ZONE_KIND, float(lo), float(hi))


def on_ramp_for_arrivals(meta: Mapping[str, Any], prov: Mapping[str, Any]) -> dict[str, Any]:
    """The on-ramp as ``add_arrival_crossings`` reads it: its attach span and auxiliary band."""
    ons = [r for r in meta.get("ramps") or [] if r.get("kind") == "on"]
    if len(ons) != 1:
        raise ValueError(f"expected one on-ramp in the run, found {len(ons)}")
    r = ons[0]
    edges = [str(e) for e in prov["edges"]]
    return {
        "name": str(r["name"]),
        "x_lo_m": float(r["attach_x_m"]),
        "x_hi_m": float(r["attach_end_x_m"]),
        "aux_band": int(prov["edge_lanes"][edges.index(str(r["attach_edge"]))]),
    }


def sim_one(
    run_dir: str,
    *,
    edge_spans_data: Mapping[str, Sequence[float]],
    site_length_m: float,
    windows_wall: Sequence[tuple[float, float]],
    length_m: float,
) -> dict[str, Any]:
    """One replicate: the table on I-80's axis, the extraction, its point summary."""
    from i24_lane_change_gaps import _sim_run_frame
    from lane_change_relaxation import add_arrival_crossings

    path = Path(run_dir)
    meta = json.loads((path / "meta.json").read_text())
    df, zones, mainline, aux, prov = _sim_run_frame(path, length_m)
    if tuple(mainline) != MAINLINE or tuple(aux) != AUX:
        raise ValueError(f"{run_dir}: lanes {mainline} / {aux}, not I-80's {MAINLINE} / {AUX}")
    dt = 1.0 / float(meta["config"]["sim"]["output_hz"])
    warm = float(meta["config"]["sim"]["warmup_s"])
    ramp = on_ramp_for_arrivals(meta, prov)
    with (path / "vehicles.parquet").open("rb") as fh:
        veh = pd.read_parquet(fh, columns=["veh_id", "origin"])
    first = df.sort_values(["veh_id", "t"]).groupby("veh_id", sort=False).head(1)
    first = first.merge(veh.astype({"veh_id": str}), on="veh_id", how="left")
    df, keys = add_arrival_crossings(df, first, [ramp], dt_s=dt)
    df, zone = sim_on_data_axis(
        df, zones, prov, edge_spans_data=edge_spans_data, site_length_m=site_length_m
    )
    windows = [(warm + lo, warm + hi) for lo, hi in windows_wall]
    part = extract(
        df, zone, dt_s=dt, windows=windows, x_range=(0.0, site_length_m), arrival_keys=keys
    )
    point = summarize(pool([part]), zone, n_boot=0, seed=SEED)
    planned = meta.get("n_vehicles_planned")
    departed = meta.get("n_vehicles_departed")
    return {
        "seed": int(prov["seed"]),
        "run": {
            "run_dir": prov["run_dir"],
            "config_hash": prov["config_hash"],
            "scenario": prov["scenario"],
            "seed": int(prov["seed"]),
            "n_collisions": prov.get("n_collisions"),
            "n_vehicles_planned": planned,
            "n_vehicles_departed": departed,
            "departed_share": (
                round(float(departed) / float(planned), 5)
                if planned and departed is not None
                else None
            ),
            "edges": [str(e) for e in prov["edges"]],
            "edge_offsets_m": [float(v) for v in prov["edge_offsets_m"]],
            "edge_lanes": [int(v) for v in prov["edge_lanes"]],
            "zone_data_axis": zone.to_dict(),
            "measured_merges": meta.get("measured_merges"),
            "n_arrival_crossings_added": len(keys),
            "on_ramp": {
                k: r.get(k)
                for r in meta.get("ramps") or []
                if r.get("kind") == "on"
                for k in ("name", "n_planned", "n_departed")
            },
        },
        "counts": part.counts,
        "point": point,
        "part": part,
    }


def run_simulated(args: argparse.Namespace) -> dict[str, Any]:
    """An arm's runs: per seed, and pooled."""
    t_start = time.time()
    observed = json.loads(Path(args.observed).read_text())
    inputs = json.loads(Path(args.inputs).read_text())
    if args.manifest:
        man = json.loads(Path(args.manifest).read_text())
        run_dirs = [str(r["run_dir"]) for r in man["runs"]]
        label = man.get("label")
    else:
        run_dirs, label = list(args.run_dir), args.label
    length = float(observed["site_length_m"])
    windows = [(float(w["lo_s"]), float(w["hi_s"])) for w in observed["windows"]]
    spans = {str(k): v for k, v in inputs["corridor"]["edge_spans_data_m"].items()}
    if args.sim_length_m is None:
        from microsim.vehicles import VEHICLE_LENGTH_M

        args.sim_length_m = float(VEHICLE_LENGTH_M)
    kw = dict(
        edge_spans_data=spans,
        site_length_m=length,
        windows_wall=windows,
        length_m=float(args.sim_length_m),
    )
    results: list[dict[str, Any]] = []
    if args.procs > 1 and len(run_dirs) > 1:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(args.procs, len(run_dirs)), mp_context=ctx) as ex:
            futs = [ex.submit(sim_one, d, **kw) for d in run_dirs]
            for d, f in zip(run_dirs, futs, strict=True):
                results.append(f.result())
                print(f"{d}: done ({time.time() - t_start:.0f} s)", flush=True)
    else:
        for d in run_dirs:
            results.append(sim_one(d, **kw))
            print(f"{d}: done ({time.time() - t_start:.0f} s)", flush=True)
    zone_obs = observed["zone"]
    zone_problems = []
    for r in results:
        z = r["run"]["zone_data_axis"]
        if (
            abs(float(z["x_lo_m"]) - float(zone_obs["x_lo_m"])) > ZONE_TOL_M
            or abs(float(z["x_hi_m"]) - float(zone_obs["x_hi_m"])) > ZONE_TOL_M
        ):
            zone_problems.append({"seed": r["seed"], "zone": z, "observed": zone_obs})
    pooled = None
    if results:
        z0 = results[0]["run"]["zone_data_axis"]
        zone = Zone(ZONE_NAME, ZONE_KIND, float(z0["x_lo_m"]), float(z0["x_hi_m"]))
        pooled = summarize(pool([r["part"] for r in results]), zone, n_boot=0, seed=SEED)
    art = {
        "schema_version": 1,
        "kind": "simulated",
        "arm": label,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i80_merge_measures.py",
        "code": i80_data.git_head(),
        "code_dirty": i80_data.git_dirty(),
        "observed_artifact": {"path": str(args.observed), "data_hash": observed["data_hash"]},
        "site_length_m": length,
        "windows": observed["windows"],
        "zone_problems": zone_problems,
        "parameters": {
            "offsets_s": list(OFFSETS_S),
            "step_s": STEP_S,
            "lookback_s": DEFAULT_LOOKBACK_S,
            "sample_every_s": DEFAULT_SAMPLE_EVERY_S,
            "speed_bin_m": SPEED_BIN_M,
            "sim_length_m": args.sim_length_m,
            "arrival_crossings": "WP-82's fix (scripts/lane_change_relaxation.py)",
        },
        "method": METHOD,
        "runs": [r["run"] for r in results],
        "per_seed": [{"seed": r["seed"], "counts": r["counts"], **r["point"]} for r in results],
        "pooled": pooled,
        "wall_s": round(time.time() - t_start, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(_clean(art), indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out} ({len(results)} runs)", flush=True)
    return art


def _clean(obj: Any) -> Any:
    """JSON-ready: numpy scalars to Python, non-finite floats to None."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.bool_ | bool):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, float | np.floating):
        return None if not math.isfinite(float(obj)) else float(obj)
    return obj


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("observed", help="I-80's block (VM: reads the prepared periods)")
    o.add_argument("--data-summary", default=str(i80_data.SUMMARY_OUT))
    o.add_argument("--block", nargs="+", default=None, help="periods (default: the replica block)")
    o.add_argument("--n-boot", type=int, default=N_BOOT)
    o.add_argument("--seed", type=int, default=SEED)
    o.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "i80_merge_observed.json"))
    s = sub.add_parser("simulated", help="an arm's replicates (VM)")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", default=None, help="corridor_e11.py run's MANIFEST.json")
    src.add_argument("--run-dir", nargs="+", default=None)
    s.add_argument("--label", default=None, help="with --run-dir: the arm's label")
    s.add_argument("--observed", required=True)
    s.add_argument("--inputs", required=True, help="artifacts/i80_replica_inputs.json")
    s.add_argument("--procs", type=int, default=1)
    s.add_argument("--sim-length-m", type=float, default=None)
    s.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "observed":
        run_observed(args)
    else:
        run_simulated(args)


if __name__ == "__main__":
    main()
