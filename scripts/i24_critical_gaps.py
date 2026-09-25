"""Critical gaps of the weave's crossings: I-24 MOTION against the weave model (WP-78).

VM X (``artifacts/i24_lane_change_gaps.json``) found that the weave model's
acceptance would refuse 48.5 % of the real entering changes and 22.1 % of the
exiting ones in the I-24 MOTION Hickory Hollow–Bell Road weave. Fitting the
acceptance needs the gaps drivers let go by as well as the ones they took.
This driver reads, for every entering and exiting change in the ramp zones,
the target-lane gaps of the 10 s before it (every 1 s,
``calibration.lane_change_gaps.gap_sequences``), reduces them to each
driver's accepted and largest rejected gaps, fits the critical-gap
distributions (``calibration.critical_gap``: Troutbeck's maximum likelihood
per side and the joint lead–lag extension, log-normal, 95 % bootstrap
intervals) per zone, movement and changer-speed class, and maps the fitted
medians onto the acceptance's time gaps (``accept_gap_s``,
``exit_accept_gap_s``) as a proposal. ``WEAVE_DEFAULTS`` is not changed.

* **observed** (default; a cloud stage — it reads the 993 MB processed
  westbound table, which the laptop must not): the same chunks, span, zones,
  lanes and acceptance parameters as ``scripts/i24_lane_change_gaps.py``
  (VM X), with the load pad widened by the lookback. Writes
  ``artifacts/i24_critical_gaps.json`` and two gitignored tables that ride
  along in the pipeline archive:
  ``data/i24motion/processed/i24_wb_gap_sequences.parquet`` (the lookback
  samples) and ``data/i24motion/processed/i24_wb_critical_gap_drivers.parquet``
  (one row per sampled change).
* **simulated** (``--sim-run-dir DIR [DIR ...]``; small runs only locally):
  microsim run directories, read as ``scripts/i24_lane_change_gaps.py`` reads
  them; the records of all runs are pooled.

Run (VM):  ``uv run --no-sync python scripts/i24_critical_gaps.py``
Run (sim): ``uv run --no-sync python scripts/i24_critical_gaps.py
--sim-run-dir runs/x/<hash>/3 runs/x/<hash>/4 --out artifacts/<name>.json``
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from i24_lane_change_gaps import (
    AUX_LANES,
    CHUNK_S,
    I24_MAINLINE_LANES_TUPLE,
    I24_WB_LANE_SPEED_MS,
    PAD_S,
    POPULATION,
    REPO_ROOT,
    SAMPLE_DT_S,
    T_RANGE_S,
    WB_DIR,
    X_MARGIN_M,
    _sim_run_frame,
    git_head,
    i24_zones,
    population_means,
    rel,
)

from calibration.critical_gap import (
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    MIN_DRIVERS,
    acceptance_mapping,
    driver_gaps,
    fit_groups,
    rejected_points,
    select_drivers,
)
from calibration.lane_change_gaps import (
    DEFAULT_LOOKBACK_S,
    DEFAULT_SAME_VEHICLE_TOL_M,
    DEFAULT_SAMPLE_EVERY_S,
    SPEED_CLASSES_MS,
    AcceptanceParams,
    gap_sequences,
    lane_change_gaps,
)
from calibration.loaders.i24motion import I24_CITATION, load_i24_parquet

OUT = REPO_ROOT / "artifacts" / "i24_critical_gaps.json"
SEQ_OUT = REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_gap_sequences.parquet"
DRIVERS_OUT = REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_critical_gap_drivers.parquet"
PAD_CG_S = PAD_S + DEFAULT_LOOKBACK_S
"""Load pad per chunk side [s]: the lane-change stage's 8 s plus the lookback,
so a change at a chunk's start has its whole history in the frame."""
SENSITIVITY_LOOKBACK_S = 5.0
"""A shorter history (a sensitivity): rejected instants at most 5 s before
the change — the earlier ones are the likeliest to predate the driver's
decision to change."""
MOVEMENTS: tuple[str, ...] = ("entering", "exiting")
ZONE_KINDS: tuple[str, ...] = ("merge", "diverge", "weave")

REJECTED_GAP = (
    "a distinct lag-lead pair of consecutive target-lane vehicles that was beside the "
    "changer (lead: nearest vehicle with its front strictly ahead of the changer's front; "
    "lag: nearest at or behind it; the extraction's 200 m range) at one or more lookback "
    "instants while the changer stayed in its origin lane, and is not the pair it entered; "
    "a new pair begins when the lead or the lag is a different vehicle (id, or position "
    "continuity within same_vehicle_tol_m, so a tracker fragment switch is not a new gap). "
    "The gap-acceptance definition (Troutbeck 1992; Brilon, Koenig & Troutbeck 1999, "
    "Transp. Res. A 33:161-186; Tian et al. 1999, Transp. Res. A 33:187-197) transposed to "
    "a lane change as Marczak, Daamen & Buisson 2013 (Transp. Res. C 36:530-546, s. 6) "
    "define it at a freeway merge: gaps passed by vehicles that merge further downstream."
)

LIMITATIONS: tuple[str, ...] = (
    "Coverage: I-24 MOTION tracks about half of the peak vehicle-time (docs/I24_DATA.md s. 4). "
    "An untracked vehicle inside a gap makes the observed gap larger (the accepted gap is the "
    "true one or larger; a rejected gap may be larger, or lost into the accepted one), so "
    "the fitted critical gaps are biased upward and the proposed time gaps are upper bounds: "
    "real drivers accept gaps at least this small in expectation.",
    "Consistency: the estimators assume a driver rejects every gap below its critical gap "
    "and takes the first above it; freeway merging studies (Daamen et al. 2010; Marczak et "
    "al. 2013) find inconsistent drivers common. Inconsistent drivers are excluded and "
    "counted; gaps passed before the driver was trying to change (not separable in "
    "trajectories, which carry no intent) inflate the rejected gaps - the 5 s lookback "
    "sensitivity bounds that.",
    "The lookback is sampled every 1 s: a gap that passes the changer in less than a second "
    "(short and fast) can be missed; it is small, so it is rarely a driver's largest "
    "rejected gap. A change is timed when the vehicle's centre crosses the band edge "
    "(mid-manoeuvre) on I-24, while SUMO's changes are instantaneous.",
    "Movements are read from lanes and zones (no routes); one day, one direction; the "
    "mapping reads the acceptance at the fleet's population means and at speed parity "
    "(the brake-gap terms vanish), per changer-speed class at the class's median speed.",
)


def git_dirty() -> bool | None:
    """Whether the working tree had uncommitted changes when the artifact was written."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(out.stdout.strip())


def _peak_rss_mb() -> float:
    """Peak resident set size of this process [MB] (ru_maxrss: bytes on macOS, KiB on Linux)."""
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10


def sequence_mask(records: pd.DataFrame) -> np.ndarray:
    """The changes whose history is sampled: entering / exiting in a ramp zone."""
    if len(records) == 0:
        return np.zeros(0, dtype=bool)
    return (
        records["movement"].isin(list(MOVEMENTS)) & records["zone_kind"].isin(list(ZONE_KINDS))
    ).to_numpy(dtype=bool)


class Pool:
    """Per-chunk (or per-run) outputs pooled with globally unique change ids."""

    def __init__(self) -> None:
        self.samples: list[pd.DataFrame] = []
        self.drivers: list[pd.DataFrame] = []
        self.points: list[pd.DataFrame] = []
        self.drivers_short: list[pd.DataFrame] = []
        self.points_short: list[pd.DataFrame] = []
        self.counts: dict[str, int] = {}
        self.seq_counts: dict[str, int] = {}
        self.offset = 0

    def add(self, records: pd.DataFrame, samples: pd.DataFrame, seq_counts: dict[str, int]) -> None:
        off = self.offset
        drv = driver_gaps(records, samples)
        drv_s = driver_gaps(records, samples, max_lookback_s=SENSITIVITY_LOOKBACK_S)
        pts = rejected_points(samples)
        pts_s = rejected_points(samples, max_lookback_s=SENSITIVITY_LOOKBACK_S)
        for frame, dest in (
            (samples, self.samples),
            (drv, self.drivers),
            (pts, self.points),
            (drv_s, self.drivers_short),
            (pts_s, self.points_short),
        ):
            if len(frame):
                f = frame.copy()
                f["change"] = f["change"].to_numpy(dtype=np.int64) + off
                dest.append(f)
        self.offset += len(records)
        for k, val in seq_counts.items():
            self.seq_counts[k] = self.seq_counts.get(k, 0) + int(val)

    @staticmethod
    def cat(parts: list[pd.DataFrame]) -> pd.DataFrame:
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fits_and_mapping(
    pool: Pool, acceptance: AcceptanceParams, *, n_boot: int, seed: int, kind: str
) -> dict[str, Any]:
    """Fits per zone × movement × speed class, the mapping, the weave's time gaps and the
    sensitivity. On observed data the weave's time gaps are a ``proposal``; on a simulated
    run they are a ``self_check`` (what the model's own drivers imply)."""
    key_out = "proposal" if kind == "observed" else "self_check"
    drivers = Pool.cat(pool.drivers)
    points = Pool.cat(pool.points)
    if drivers.empty:
        return {"fits": [], "mapping": [], key_out: None, "sensitivity_lookback_5s": []}
    sel = select_drivers(drivers, movements=MOVEMENTS, zone_kinds=ZONE_KINDS)
    rows, boots = fit_groups(sel, points, n_boot=n_boot, seed=seed)
    mapping: list[dict[str, Any]] = []
    proposal: dict[str, Any] = {}
    groups: dict[tuple[str, str, str], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r["zone"], r["zone_kind"], r["movement"]), []).append(i)
    for (zone, zone_kind, movement), idx in groups.items():
        entry: dict[str, Any] = {"zone": zone, "zone_kind": zone_kind, "movement": movement}
        for est in ("joint", "separate"):
            entry[est] = acceptance_mapping(
                [rows[i] for i in idx],
                [boots[i] for i in idx],
                acceptance,
                movement=movement,
                estimator=est,
            )
        mapping.append(entry)
        if zone_kind == "weave":
            key = entry["joint"]["parameter"]
            proposal[key] = {
                "zone": zone,
                "movement": movement,
                "current": entry["joint"]["current"],
                "proposed" if kind == "observed" else "implied": entry["joint"]["accept_s"],
                "lead_side_only": entry["joint"]["accept_s_lead"],
                "lag_side_only": entry["joint"]["accept_s_lag"],
                "separate_estimator": entry["separate"]["accept_s"],
                "provenance": "this artifact (joint estimator, speed-class medians, "
                "n-weighted least squares over both sides at speed parity)",
            }
    short = select_drivers(Pool.cat(pool.drivers_short), movements=MOVEMENTS, zone_kinds=ZONE_KINDS)
    short_rows, _ = fit_groups(short, Pool.cat(pool.points_short), n_boot=0, seed=seed)
    return {
        "fits": rows,
        "mapping": mapping,
        key_out: proposal
        | {
            "status": (
                "proposal only: WEAVE_DEFAULTS is unchanged; a candidate for the weave's "
                "acceptance, to be tested on the fixtures before any adoption"
                if kind == "observed"
                else "self-check, not a proposal: the time gaps the model's own drivers imply, "
                "to be compared with the acceptance's current values and with the observed "
                "artifact's proposal"
            ),
        },
        "sensitivity_lookback_5s": short_rows,
    }


def method_block(parameters: dict[str, Any], *, n_boot: int, seed: int) -> dict[str, Any]:
    """The artifact's ``method``."""
    return {
        **parameters,
        "modules": ["calibration.lane_change_gaps.gap_sequences", "calibration.critical_gap"],
        "lookback_s": DEFAULT_LOOKBACK_S,
        "lookback_bounds": "the held run of the origin lane before the change, the track, a "
        "missing sample slot, and the change's own zone (an auxiliary lane exists only there)",
        "sample_every_s": DEFAULT_SAMPLE_EVERY_S,
        "same_vehicle_tol_m": DEFAULT_SAME_VEHICLE_TOL_M,
        "sensitivity_lookback_s": SENSITIVITY_LOOKBACK_S,
        "rejected_gap": REJECTED_GAP,
        "accepted_gap": "the lead and lag gaps at the change moment (the first sample in the "
        "new debounced lane), as calibration.lane_change_gaps records them",
        "empty_instant": "no vehicle within range on either side: never a rejection",
        "time_gaps": "lead: bumper gap over the changer's speed; lag: bumper gap over the "
        "lag's speed",
        "drivers": "confirmed, non-suspect entering and exiting changes in merge, diverge and "
        "weave zones; suspect lookback instants (a gap below 0.5 m) left out",
        "separate_estimator": "Troutbeck's maximum likelihood per side, log-normal: "
        "L = prod [F(a_i) - F(r_i)], a_i the accepted and r_i the largest rejected time gap "
        "of that side (0 when none); inconsistent drivers (r_i >= a_i) excluded, counted",
        "joint_estimator": "a driver accepts only when both sides clear independent log-normal "
        "critical gaps; likelihood: the probability that the driver's critical pair lies in "
        "the accepted pair's rectangle and outside the union of its rejected combinations' "
        "rectangles (exact staircase); reduces to the separate estimator when a side never "
        "binds; drivers with a rejected combination at least as large on both sides excluded",
        "no_rejection": "include (r = 0); the sensitivity_no_rejection_excluded entries drop "
        "those drivers (Weinert 2000's choice)",
        "speed_classes_ms": [
            [label, lo, hi if np.isfinite(hi) else None] for label, lo, hi in SPEED_CLASSES_MS
        ],
        "min_drivers": MIN_DRIVERS,
        "n_boot": n_boot,
        "bootstrap": "drivers resampled with replacement; percentile 95 % intervals; seeds "
        "flowstate_core.rng.spawn_seeds(seed, n_rows)",
        "seed": seed,
        "mapping": "at speed parity the acceptance's leader side needs 2 s0 + A v (critical "
        "time gap A + 2 s0 / v over the changer's speed), its follower side the larger of "
        "2 s0 + A v_F and the absorption gap (s0 + v_F T) / sqrt(1 - (v_F/v0)^4 + b/a_max); "
        "A implied per class = fitted median - 2 s0 / (class median speed); proposal = the "
        "n-weighted mean over classes and sides, floored at 0",
        "citations": [
            "Troutbeck, R.J. (1992). Estimating the critical acceptance gap from traffic "
            "movements. Physical Infrastructure Centre Research Report 92-5, QUT.",
            "Brilon, W., Koenig, R., Troutbeck, R.J. (1999). Useful estimation procedures "
            "for critical gaps. Transportation Research Part A 33(3-4):161-186.",
            "Tian, Z. et al. (1999). Implementing the maximum likelihood methodology to "
            "measure a driver's critical gap. Transportation Research Part A 33(3-4):187-197.",
            "Weinert, A. (2000). Estimation of critical gaps and follow-up times at rural "
            "unsignalized intersections in Germany. TRB Circular E-C018, 409-421.",
            "Troutbeck, R.J. (2014). Estimating the mean critical gap. Transportation "
            "Research Record 2461:76-84.",
            "Daamen, W., Loot, M., Hoogendoorn, S.P. (2010). Empirical analysis of merging "
            "behavior at freeway on-ramp. Transportation Research Record 2188:108-118.",
            "Marczak, F., Daamen, W., Buisson, C. (2013). Merging behaviour: empirical "
            "comparison between two sites and new theory development. Transportation "
            "Research Part C 36:530-546.",
        ],
    }


def run_observed(args: argparse.Namespace) -> None:
    """The I-24 MOTION westbound day, chunked."""
    t_start = time.time()
    wb_dir = Path(args.wb_dir)
    if not (wb_dir / "trajectories.parquet").exists():
        raise SystemExit(
            f"{rel(wb_dir)}/trajectories.parquet is missing: this mode reads the processed "
            "I-24 MOTION table (a cloud stage; launch the VM with --data-set i24)"
        )
    meta = json.loads((wb_dir / "meta.json").read_text())
    zones, span = i24_zones()
    acceptance = AcceptanceParams.from_population(
        population_means(POPULATION),
        v0_cap_ms=I24_WB_LANE_SPEED_MS,
        source=f"{rel(POPULATION)} means; v0 capped at the weave edge's "
        f"lane speed {I24_WB_LANE_SPEED_MS} m/s; WEAVE_DEFAULTS time gaps",
    )
    t_lo, t_hi = args.t_range
    pool = Pool()
    params: dict[str, Any] = {}
    n_chunks = int(np.ceil((t_hi - t_lo) / CHUNK_S))
    for k in range(n_chunks):
        lo = t_lo + k * CHUNK_S
        hi = min(lo + CHUNK_S, t_hi)
        df = load_i24_parquet(
            wb_dir,
            t_range_s=(lo - PAD_CG_S, hi + PAD_CG_S),
            x_range_m=(span[0] - X_MARGIN_M, span[1] + X_MARGIN_M),
            columns=["t", "veh_id", "x", "lane", "v", "length"],
        )
        out = lane_change_gaps(
            df,
            zones,
            mainline_lanes=I24_MAINLINE_LANES_TUPLE,
            aux_lanes=AUX_LANES,
            dt_s=SAMPLE_DT_S,
            window_s=(lo, hi),
            x_range_m=span,
            acceptance=acceptance,
        )
        seq = gap_sequences(
            df, out.records, changes=sequence_mask(out.records), zones=zones, dt_s=SAMPLE_DT_S
        )
        pool.add(out.records, seq.samples, seq.counts)
        for key, val in out.counts.items():
            pool.counts[key] = pool.counts.get(key, 0) + int(val)
        params = {**out.parameters, **seq.parameters}
        print(
            f"chunk {k + 1}/{n_chunks} t [{lo:.0f}, {hi:.0f}) rows {len(df):,} "
            f"changes {len(out.records):,} sampled {seq.counts['n_changes']:,} "
            f"({time.time() - t_start:.0f} s, peak {_peak_rss_mb():.0f} MB)",
            flush=True,
        )
        del df, out, seq
    params = {**params, "window_s": [t_lo, t_hi], "chunk_s": CHUNK_S, "pad_s": PAD_CG_S}
    t_fit = time.time()
    fitted = fits_and_mapping(pool, acceptance, n_boot=args.n_boot, seed=args.seed, kind="observed")
    print(f"fits: {time.time() - t_fit:.0f} s", flush=True)
    seq_out, drv_out = Path(args.sequences_out), Path(args.drivers_out)
    seq_out.parent.mkdir(parents=True, exist_ok=True)
    Pool.cat(pool.samples).to_parquet(seq_out, index=False)
    Pool.cat(pool.drivers).to_parquet(drv_out, index=False)
    art = {
        "schema_version": 1,
        "kind": "observed",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i24_critical_gaps.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "data_hash": meta.get("data_hash"),
        "data": rel(wb_dir),
        "time_origin": "t = seconds after 06:00:00 CST, 30 Nov 2022",
        "x_axis": "data x [m], front bumper, 0 at MM 62.7, westbound",
        "span_data_x_m": list(span),
        "citation": I24_CITATION,
        "sequences_file": rel(seq_out),
        "drivers_file": rel(drv_out),
        "method": method_block(params, n_boot=args.n_boot, seed=args.seed),
        "zones": [z.to_dict() for z in zones],
        "acceptance": acceptance.to_dict(),
        "counts": {"extraction": pool.counts, "sequences": pool.seq_counts},
        **fitted,
        "limitations": list(LIMITATIONS),
        "wall_s": round(time.time() - t_start, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
    }
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out}, {seq_out}, {drv_out}", flush=True)


def run_simulated(args: argparse.Namespace) -> None:
    """Pooled critical gaps of microsim run directories."""
    t_start = time.time()
    pool = Pool()
    runs: list[dict[str, Any]] = []
    zones: list[Any] = []
    params: dict[str, Any] = {}
    acceptance: AcceptanceParams | None = None
    for d in args.sim_run_dir:
        run_dir = Path(d)
        df, zones, mainline, aux, prov = _sim_run_frame(run_dir, float(args.sim_length_m))
        pop_path = REPO_ROOT / str(
            prov["fleet_idm_calibration"] or POPULATION.relative_to(REPO_ROOT)
        )
        acceptance = AcceptanceParams.from_population(
            population_means(pop_path),
            v0_cap_ms=float(prov["v0_cap_ms"]),
            source=f"{rel(pop_path)} means; v0 capped at the weave edge's lane "
            f"speed {prov['v0_cap_ms']} m/s; WEAVE_DEFAULTS time gaps",
        )
        veh = pd.read_parquet(
            run_dir / "vehicles.parquet", columns=["veh_id", "origin", "destination"]
        )
        groups = {
            str(i): f"{o}->{dst}"
            for i, o, dst in zip(veh["veh_id"], veh["origin"], veh["destination"], strict=True)
        }
        out = lane_change_gaps(
            df, zones, mainline_lanes=mainline, aux_lanes=aux, acceptance=acceptance, groups=groups
        )
        rec = out.records
        rec.insert(0, "seed", prov["seed"])
        seq = gap_sequences(df, rec, changes=sequence_mask(rec), zones=zones, dt_s=out.dt_s)
        pool.add(rec, seq.samples, seq.counts)
        for key, val in out.counts.items():
            pool.counts[key] = pool.counts.get(key, 0) + int(val)
        params = {**out.parameters, **seq.parameters, "default_length_m": args.sim_length_m}
        runs.append({**prov, "counts": out.counts, "sequence_counts": seq.counts})
        print(f"{run_dir}: {len(rec):,} changes, {seq.counts['n_changes']:,} sampled", flush=True)
    if acceptance is None:
        raise SystemExit("no run directories given")
    fitted = fits_and_mapping(
        pool, acceptance, n_boot=args.n_boot, seed=args.seed, kind="simulated"
    )
    art = {
        "schema_version": 1,
        "kind": "simulated",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i24_critical_gaps.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "runs": runs,
        "method": method_block(params, n_boot=args.n_boot, seed=args.seed),
        "zones": [z.to_dict() for z in zones],
        "acceptance": acceptance.to_dict(),
        "counts": {"extraction": pool.counts, "sequences": pool.seq_counts},
        **fitted,
        "limitations": [
            "The model's own drivers change lanes by three mechanisms the trajectories do not "
            "separate: the weave's acceptance, its forced changes (which see only the guard) "
            "and SUMO's LC2013; the fitted critical gaps describe their mixture.",
            *LIMITATIONS[1:],
        ],
        "wall_s": round(time.time() - t_start, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
    }
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out}", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--out",
        default=None,
        help=f"artifact path (observed default: {rel(OUT)}; required with --sim-run-dir)",
    )
    ap.add_argument(
        "--t-range",
        nargs=2,
        type=float,
        default=list(T_RANGE_S),
        metavar=("LO", "HI"),
        help="observed: window [s after 06:00 CST] (default: the whole recording)",
    )
    ap.add_argument(
        "--wb-dir",
        default=str(WB_DIR),
        help="observed: the processed table's directory (a synthetic stand-in for timing)",
    )
    ap.add_argument("--sequences-out", default=str(SEQ_OUT), help="observed: lookback samples")
    ap.add_argument("--drivers-out", default=str(DRIVERS_OUT), help="observed: driver table")
    ap.add_argument("--sim-run-dir", nargs="+", default=None, help="simulated: microsim run dirs")
    ap.add_argument(
        "--sim-length-m",
        type=float,
        default=None,
        help="simulated: passenger vehicle length [m] (default: microsim.vehicles.VEHICLE_LENGTH_M)",
    )
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT, help="bootstrap replicates")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="bootstrap master seed")
    args = ap.parse_args(argv)
    if args.sim_run_dir:
        if args.out is None:
            ap.error("--out is required with --sim-run-dir")
        if args.sim_length_m is None:
            from microsim.vehicles import VEHICLE_LENGTH_M

            args.sim_length_m = float(VEHICLE_LENGTH_M)
        run_simulated(args)
    else:
        if args.out is None:
            args.out = str(OUT)
        run_observed(args)


if __name__ == "__main__":
    main()
