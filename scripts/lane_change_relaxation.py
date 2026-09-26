"""The gaps after a lane change — real drivers against the model (WP-88).

WP-87 (docs/WEAVE_MODEL_PLAN.md) found the model's new follower at 1.01–1.07
of its own static gap ``s0 + vT`` right after a crossing: the model's followers
keep their full time gap at once. The lane-change relaxation literature
(Laval & Leclercq 2008; Schakel, Knoop & van Arem 2012; Zheng, Ahn, Chen &
Laval 2013 — cited in ``calibration.lane_change_relaxation``) describes real
followers accepting a shorter gap and returning to their normal gap over some
seconds. This driver measures, with ``calibration.lane_change_relaxation``,
the new follower's and the changer's time and space gaps at 0–30 s after
every lane change, their ratios to the vehicle's own pre-change time gap, to
the population's normal time gap at the same speed and to the static gap
``s0 + vT`` at a population's means, and an exponential relaxation time with
a bootstrap interval, the same way on three sources:

* ``--source i24`` (a cloud stage — it reads the 993 MB processed westbound
  table, which the laptop must not): the I-24 MOTION westbound day in 15-min
  chunks, with the span, ramp zones (WP-77's labels: the Old Hickory merge,
  the Hickory Hollow diverge, the Hickory Hollow–Bell Road weave, the Bell
  Road diverge), lanes and debounce of ``scripts/i24_lane_change_gaps.py``;
  each chunk is loaded with a 38 s pad (the 8 s debounce pad plus the 30 s
  reference window / last offset), so a change near a chunk edge has its whole
  history and future in the frame. ``s0 + vT`` at the means of
  ``artifacts/idm_i24_capacity.json``.
* ``--source us101`` (a cloud stage — it reads ``data/ngsim``): NGSIM US-101
  as the repository holds it — the raw data.transportation.gov export
  (Socrata ``8ect-6jqj``), NOT the Montanino–Punzo reconstruction
  (``scripts/us101_data.py``, docs/M2_RESULTS.md §7) — loaded, de-duplicated
  and split into its recording periods by ``scripts/us101_data.load_us101``;
  10 Hz, lanes 1–5 mainline (1 = leftmost), 6 the auxiliary lane between the
  Ventura on-ramp and the Cahuenga off-ramp, 7/8 the ramps (not read). The
  weaving zone is where lane 6 is occupied: the 0.5th to 99.5th percentile of
  the positions of lane-6 samples, computed from the data and recorded in the
  artifact. ``s0 + vT`` at the means of ``artifacts/idm_us101.json``.
* ``--source trajectories`` (``--sim-run-dir DIR [DIR ...]``; small runs only
  locally): microsim run directories, read as ``scripts/i24_lane_change_gaps.py``
  reads them (band lanes, zones from ``meta.json["ramps"]``), plus the fix of
  WP-82: an entrant from a weave on-ramp that SUMO moved off the auxiliary
  lane in the step it reached the section has its first corridor sample
  already in the target lane (the ramp is not in the trajectory table), so one
  sample is added on the auxiliary lane one step before (``x − v·dt``, its
  speed), and the change it makes is marked confirmed and
  ``arrival_crossing``. ``s0 + vT`` at the fleet's IDM population means.

Only confirmed, non-suspect changes of the ``entering``, ``exiting`` and
``through`` movements are walked (``through`` outside the ramp zones is the
ordinary discretionary change). The artifact
``artifacts/lane_change_relaxation_<source>.json`` holds summaries only
(docs/CONTRACTS.md, "Lane-change relaxation"), with a seeded sample of at
most 100 changes.

Run (VM):  ``uv run --no-sync python scripts/lane_change_relaxation.py --source i24``
           ``uv run --no-sync python scripts/lane_change_relaxation.py --source us101``
Run (sim): ``uv run --no-sync python scripts/lane_change_relaxation.py --source trajectories
--sim-run-dir runs/x/<hash>/3 runs/x/<hash>/4 --out <path>.json``
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from calibration.lane_change_gaps import SPEED_CLASSES_MS, Zone, lane_change_gaps
from calibration.lane_change_relaxation import (
    CENSOR_REASONS,
    DEFAULT_FOLLOW_GAP_M0,
    DEFAULT_MAX_FOLLOW_TIME_GAP_S,
    DEFAULT_MIN_N_FIT,
    DEFAULT_MIN_N_NORMAL,
    DEFAULT_MIN_OFFSETS_FIT,
    DEFAULT_MIN_REF_SAMPLES,
    DEFAULT_MIN_SPEED_MS,
    DEFAULT_N_BOOT,
    DEFAULT_OFFSETS_S,
    DEFAULT_PRE_WINDOW_S,
    DEFAULT_SEED,
    DEFAULT_STEP_S,
    MEASURES,
    NormalTimeGaps,
    PostChangeGaps,
    concat_results,
    normal_time_gaps,
    post_change_gaps,
    sample_events,
    summarize_relaxation,
    with_population_ratio,
)
from calibration.lanechange import DEFAULT_MAX_GAP_FACTOR, DEFAULT_MIN_DWELL_S

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SOURCES: tuple[str, ...] = ("i24", "us101", "trajectories")
MOVEMENTS: tuple[str, ...] = ("entering", "exiting", "through")
"""Movements walked and summarized (``unknown`` is left out and counted)."""

I24_POPULATION = REPO_ROOT / "artifacts" / "idm_i24_capacity.json"
US101_POPULATION = REPO_ROOT / "artifacts" / "idm_us101.json"
I24_PAD_S = (
    2.0 * (DEFAULT_MIN_DWELL_S + DEFAULT_MAX_GAP_FACTOR * 0.2)
    + 5.0
    + max(DEFAULT_PRE_WINDOW_S[0], max(DEFAULT_OFFSETS_S))
)
"""Load pad per chunk side [s]: the lane-change stage's 8 s plus the 30 s window."""

US101_MAINLINE: tuple[int, ...] = (1, 2, 3, 4, 5)
US101_AUX: tuple[int, ...] = (6,)
US101_ZONE_QUANTILES: tuple[float, float] = (0.005, 0.995)
"""The weaving zone's ends: these quantiles of the positions of lane-6 samples."""
NGSIM_DT_S = 0.1

SAMPLE_N = 100
SAMPLE_SEED = 20260925

METHOD: dict[str, str] = {
    "module": "calibration.lane_change_relaxation",
    "sides": "follower: the changer's new follower F (the nearest vehicle in the target lane "
    "whose front is at or behind the changer's front at the change) behind the changer C; "
    "leader: C behind its new leader L (the nearest whose front is strictly ahead)",
    "offsets": "the change sample (the first sample in the new debounced lane) plus whole "
    "seconds; the pair's identity and car-following are checked at every walk step (1 s)",
    "space_gap_m": "bumper to bumper, rear to front",
    "time_gap_s": "space gap over the rear vehicle's speed; not read below min_speed_ms",
    "ratio_own": "time gap over the rear vehicle's own median time gap to its leader (in its "
    "own lane) at the walk instants 30 s to 5 s before the change when car-following, at "
    "least min_ref_samples instants; the last 5 s are left out (the follower's anticipation)",
    "ratio_pop": "time gap over the population's median car-following time gap in the rear "
    "vehicle's 2-m/s speed bin, over the same table on the walk grid (normal_time_gaps; "
    "samples not screened for recent lane changes)",
    "ratio_eq": "space gap over s0 + v T at the rear vehicle's speed, (s0, T) the population "
    "means named in 'equilibrium' (WP-87's measure, which read the follower's own s0 and T)",
    "ratio_own_eq": "ratio_eq over the rear vehicle's own median ratio_eq at the ratio_own "
    "reference instants: the own normal with the speed taken out (a time gap at equilibrium "
    "is T + s0 / v, so ratio_own moves with any speed change across the crossing)",
    "car_following": "bumper gap in [min_gap_m, min(max_range_m, follow_gap_m0 + "
    "max_follow_time_gap_s * v_rear)]; a side enters only if car-following at the change",
    "censoring": "a side is read at an offset only while, at every walk step up to it, the "
    "changer is tracked in the target lane without a further change, the partner is still "
    "its immediate lag / lead there (id or position continuity within 2 m: a tracker "
    "fragment switch continues), and the pair is car-following; the first failure ends the "
    "side and is its censor reason",
    "fit": "r(tau) = r_inf + (r0 - r_inf) exp(-tau / tau_r), weighted least squares on the "
    "per-offset medians (weights: sides read), offsets read by at least min_n_fit sides; "
    "bootstrap over sides; supported only if tau_r lies between the first positive and the "
    "last offset used, 80 % of the refits agree, the amplitude's 95 % interval excludes 0 and "
    "the weighted RMS residual is at most a quarter of the amplitude; r0 < r_inf with r0 "
    "below 1 is the literature's relaxation (a short gap that opens)",
    "summaries_exclude": "unconfirmed and suspect changes; the unknown movement",
}

LIMITATIONS_COMMON: tuple[str, ...] = (
    "Censoring is not independent of the outcome: a follower that drops far back ends as "
    "gap_bound, one that changes lane or is cut in on ends early. Each offset's n and the "
    "censor reasons are reported, with a complete-case curve (the sides read at every offset "
    "to 10 s) beside the per-offset one.",
    "The references are themselves traffic: a vehicle's own pre-change gap and the "
    "population's normal both include whatever share of the traffic was relaxing from an "
    "earlier change at the time.",
    "Time gaps are not read below 2 m/s; in stop-and-go a side can be followed with few "
    "time-gap readings.",
)
LIMITATIONS: dict[str, tuple[str, ...]] = {
    "i24": (
        "I-24 MOTION tracks about half of the peak vehicle-time (docs/I24_DATA.md s. 4): the "
        "nearest tracked vehicle may not be the nearest vehicle, so a space gap is the true gap "
        "or larger and a cut-in by an untracked vehicle is not seen. On the leader side the rear "
        "vehicle is the changer, so its time gap and ratio_eq are upper bounds. On the follower "
        "side the recorded follower can be a different vehicle at a different speed, so its "
        "time gap and ratio_eq are expected to read long but are not bounds. The references of "
        "ratio_pop and ratio_own pair each vehicle with its nearest tracked leader and are "
        "inflated the same way, so those two ratios are not bounded in either direction.",
        "Documents are fragments (median 9.9 s): a side survives a fragment switch only when the "
        "next fragment continues within 2 m; long offsets are read on few sides.",
        "Lanes are lateral bands floor(y / 12 ft); a change is timed when the vehicle's centre "
        "crosses the band edge, mid-manoeuvre, while SUMO's changes are instantaneous.",
        "Movements are read from lanes and zones (no routes); one day, one direction.",
    ),
    "us101": (
        "NGSIM US-101 as held here is the raw export, not the Montanino-Punzo reconstruction: "
        "positions and speeds carry the raw data's noise (docs/M2_RESULTS.md s. 7); gaps are "
        "measured positions, time gaps divide by the noisy v_vel.",
        "The site is congested throughout (docs/M2_RESULTS.md): few changes are made at 20 m/s "
        "or more.",
        "The weaving zone is read off the data (the span of lane-6 samples), not a survey.",
        "The dump holds period 1 and 8.8 min of period 2 (docs/M2_RESULTS.md s. 1).",
    ),
    "trajectories": (
        "SUMO's changes are instantaneous and its trajectory table starts a ramp vehicle on the "
        "corridor: arrival-step crossings are restored by one added sample (WP-82's fix).",
        "Simulated runs: one scenario configuration and seed set; macOS records unless stated.",
    ),
}


def git_head() -> str:
    """The code's commit (the VM's snapshot commit message names the source SHA)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


#: The paths whose uncommitted changes make an artifact's ``code_dirty`` true.
CODE_PATHS = ("packages", "scripts", "scenarios", "pyproject.toml", "uv.lock")


def git_dirty() -> bool | None:
    """Whether the code had uncommitted changes when the artifact was written.

    Only the code paths count (``packages``, ``scripts``, ``scenarios``,
    ``pyproject.toml``, ``uv.lock``): a pipeline VM rewrites tracked artifacts
    under ``artifacts/`` stage by stage, and an earlier stage's output must not
    mark a later stage's code as dirty (VM AE, 2026-09-25).
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def rel(path: Path) -> str:
    """``path`` relative to the repository when it lies inside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def peak_rss_mb() -> float:
    """Peak resident set size of this process [MB] (ru_maxrss: bytes on macOS, KiB on Linux)."""
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10


def population_means(path: Path) -> dict[str, float]:
    """``mean`` of an IDM population artifact (keys v0, T, a_max, b, s0)."""
    return {k: float(v) for k, v in json.loads(path.read_text())["mean"].items()}


def walk_mask(records: pd.DataFrame) -> np.ndarray:
    """The changes walked: confirmed, not suspect, of a summarized movement."""
    if len(records) == 0:
        return np.zeros(0, dtype=bool)
    return (
        records["confirmed"].astype(bool).to_numpy()
        & ~records["suspect"].astype(bool).to_numpy()
        & records["movement"].isin(list(MOVEMENTS)).to_numpy()
    )


def movement_counts(records: pd.DataFrame, mask: np.ndarray) -> list[dict[str, Any]]:
    """Changes per zone kind × movement: all, and walked."""
    if len(records) == 0:
        return []
    rows = []
    rec = records.assign(_walked=mask)
    for (zk, mv), sub in rec.groupby(["zone_kind", "movement"], sort=True):
        rows.append(
            {
                "zone_kind": str(zk),
                "movement": str(mv),
                "n_all": len(sub),
                "n_walked": int(sub["_walked"].sum()),
            }
        )
    return rows


def _merge_movement_counts(parts: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    tot: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in parts:
        for r in rows:
            key = (r["zone_kind"], r["movement"])
            cur = tot.setdefault(
                key, {"zone_kind": key[0], "movement": key[1], "n_all": 0, "n_walked": 0}
            )
            cur["n_all"] += r["n_all"]
            cur["n_walked"] += r["n_walked"]
    return [tot[k] for k in sorted(tot)]


def us101_weave_zone(x_lane6: np.ndarray) -> Zone:
    """The US-101 weaving zone: where lane 6 is occupied (quantiles of its samples' x)."""
    x = np.asarray(x_lane6, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        raise ValueError("no lane-6 samples: cannot place the weaving zone")
    lo, hi = np.quantile(x, US101_ZONE_QUANTILES)
    return Zone("US101_aux_lane_6", "weave", float(lo), float(hi))


def add_arrival_crossings(
    df: pd.DataFrame,
    first: pd.DataFrame,
    ramps: list[dict[str, Any]],
    *,
    dt_s: float,
) -> tuple[pd.DataFrame, set[tuple[str, float]]]:
    """WP-82's fix: restore the crossings SUMO makes in the step a ramp vehicle arrives.

    ``first`` holds each vehicle's first corridor sample (``veh_id, t, x,
    lane, v, length``) and its ``origin``. ``ramps`` lists the weave on-ramps as
    ``{name, x_lo_m, x_hi_m, aux_band}`` (the attach edge's span and the band
    of its lane 0, where the ramp joins). A vehicle from such a ramp whose first
    sample lies on the attach edge in another band was moved off the
    auxiliary lane on arrival: one sample is added one step before it on the
    auxiliary band (``x − v · dt_s``, its speed and length).

    Returns:
        The frame with the added rows, and the ``(veh_id, t)`` of each restored
        change (its first sample, where :func:`lane_change_gaps` will time it).
    """
    added: list[dict[str, Any]] = []
    keys: set[tuple[str, float]] = set()
    by_name = {r["name"]: r for r in ramps}
    cols = {c: first[c].to_numpy() for c in ("veh_id", "origin", "t", "x", "lane", "v", "length")}
    for i in range(len(first)):
        r = by_name.get(str(cols["origin"][i]))
        if r is None:
            continue
        x, t, v = float(cols["x"][i]), float(cols["t"][i]), float(cols["v"][i])
        if not (r["x_lo_m"] <= x < r["x_hi_m"]) or int(cols["lane"][i]) == int(r["aux_band"]):
            continue
        added.append(
            {
                "t": t - dt_s,
                "veh_id": cols["veh_id"][i],
                "x": x - v * dt_s,
                "lane": int(r["aux_band"]),
                "v": v,
                "length": float(cols["length"][i]),
            }
        )
        keys.add((str(cols["veh_id"][i]), round(t, 6)))
    if not added:
        return df, keys
    extra = pd.DataFrame(added).astype({c: df[c].dtype for c in added[0] if c in df.columns})
    return pd.concat([df, extra], ignore_index=True), keys


def build_artifact(
    result: PostChangeGaps,
    normal: NormalTimeGaps,
    *,
    source: str,
    kind: str,
    zones: list[Zone],
    equilibrium: dict[str, Any],
    extraction_counts: dict[str, int],
    movement_rows: list[dict[str, Any]],
    provenance: dict[str, Any],
    n_boot: int,
    seed: int,
    extra_by: tuple[str, ...],
    t_start: float,
) -> dict[str, Any]:
    """The JSON artifact (docs/CONTRACTS.md, "Lane-change relaxation")."""
    by = ("zone_kind", "movement")
    out: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "data_source": source,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/lane_change_relaxation.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        **provenance,
        "method": METHOD,
        "parameters": {
            **result.parameters,
            "movements": list(MOVEMENTS),
            "speed_classes_ms": [
                [lab, lo, hi if np.isfinite(hi) else None] for lab, lo, hi in SPEED_CLASSES_MS
            ],
            "min_n_fit": DEFAULT_MIN_N_FIT,
            "min_offsets_fit": DEFAULT_MIN_OFFSETS_FIT,
            "ratio_pop_min_n": DEFAULT_MIN_N_NORMAL,
            "n_boot": n_boot,
            "seed": seed,
        },
        "zones": [z.to_dict() for z in zones],
        "equilibrium": equilibrium,
        "counts": {"extraction": extraction_counts, "relaxation": result.counts},
        "counts_by_zone_kind": movement_rows,
        "normal_time_gaps": normal.to_dict(),
        "summary_by_zone_kind": summarize_relaxation(result, by=by, n_boot=n_boot, seed=seed),
        "sample": sample_events(
            result, SAMPLE_N, seed=SAMPLE_SEED, measures=("time_gap_s", "ratio_pop")
        ),
        "censor_reasons": list(CENSOR_REASONS),
        "measures": list(MEASURES),
        "limitations": [*LIMITATIONS_COMMON, *LIMITATIONS[source]],
    }
    for col in extra_by:
        if col in result.events.columns:
            # the split is of the entering movement (the only one a ramp arrival makes); all speeds
            out[f"summary_by_zone_kind_{col}"] = summarize_relaxation(
                result,
                by=(*by, col),
                movements=("entering",),
                speed_classes=(),
                n_boot=n_boot,
                seed=seed,
            )
    out["wall_s"] = round(time.time() - t_start, 1)
    out["peak_rss_mb"] = round(peak_rss_mb(), 1)
    return out


def _walk_kwargs() -> dict[str, Any]:
    return dict(
        offsets_s=DEFAULT_OFFSETS_S,
        step_s=DEFAULT_STEP_S,
        pre_window_s=DEFAULT_PRE_WINDOW_S,
        follow_gap_m0=DEFAULT_FOLLOW_GAP_M0,
        max_follow_time_gap_s=DEFAULT_MAX_FOLLOW_TIME_GAP_S,
        min_speed_ms=DEFAULT_MIN_SPEED_MS,
        min_ref_samples=DEFAULT_MIN_REF_SAMPLES,
    )


def _counts_add(total: dict[str, int], part: dict[str, int]) -> None:
    for k, val in part.items():
        total[k] = total.get(k, 0) + int(val)


def dumps_compact(obj: Any, level: int = 0) -> str:
    """JSON with one key per line and every list of scalars on one line (``allow_nan=False``).

    The per-offset arrays are most of an artifact; ``indent=1`` would put each
    number on a line of its own.
    """
    pad = " " * (level + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [
            f"{pad}{json.dumps(str(k))}: {dumps_compact(v, level + 1)}" for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + " " * level + "}"
    if isinstance(obj, list | tuple):
        if all(not isinstance(v, dict | list | tuple) for v in obj):
            return json.dumps(list(obj), allow_nan=False)
        items = [f"{pad}{dumps_compact(v, level + 1)}" for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + " " * level + "]"
    return json.dumps(obj, allow_nan=False)


def _write(path: Path, art: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_compact(art) + "\n"
    assert json.loads(text) == json.loads(json.dumps(art, allow_nan=False))
    path.write_text(text)


def run_i24(args: argparse.Namespace) -> None:
    """The I-24 MOTION westbound day, chunked (a cloud stage)."""
    from i24_lane_change_gaps import (
        AUX_LANES,
        CHUNK_S,
        I24_MAINLINE_LANES_TUPLE,
        SAMPLE_DT_S,
        WB_DIR,
        X_MARGIN_M,
        i24_zones,
    )

    from calibration.loaders.i24motion import I24_CITATION, load_i24_parquet

    t_start = time.time()
    if not (WB_DIR / "trajectories.parquet").exists():
        raise SystemExit(
            f"{rel(WB_DIR)}/trajectories.parquet is missing: this mode reads the processed "
            "I-24 MOTION table (a cloud stage; launch the VM with --data-set i24)"
        )
    meta = json.loads((WB_DIR / "meta.json").read_text())
    zones, span = i24_zones()
    means = population_means(I24_POPULATION)
    eq = (means["s0"], means["T"])
    lanes = (*I24_MAINLINE_LANES_TUPLE, *AUX_LANES)
    t_lo, t_hi = args.t_range
    parts: list[PostChangeGaps] = []
    normal: NormalTimeGaps | None = None
    counts: dict[str, int] = {}
    mv_parts: list[list[dict[str, Any]]] = []
    n_chunks = int(np.ceil((t_hi - t_lo) / CHUNK_S))
    for k in range(n_chunks):
        lo = t_lo + k * CHUNK_S
        hi = min(lo + CHUNK_S, t_hi)
        limits = (lo - I24_PAD_S, hi + I24_PAD_S)
        df = load_i24_parquet(
            WB_DIR,
            t_range_s=limits,
            x_range_m=(span[0] - X_MARGIN_M, span[1] + X_MARGIN_M),
            columns=["t", "veh_id", "x", "lane", "v", "length"],
        )
        gaps = lane_change_gaps(
            df,
            zones,
            mainline_lanes=I24_MAINLINE_LANES_TUPLE,
            aux_lanes=AUX_LANES,
            dt_s=SAMPLE_DT_S,
            window_s=(lo, hi),
            x_range_m=span,
        )
        rec = gaps.records
        mask = walk_mask(rec)
        mv_parts.append(movement_counts(rec, mask))
        _counts_add(counts, gaps.counts)
        res = post_change_gaps(
            df,
            rec,
            changes=mask,
            dt_s=SAMPLE_DT_S,
            t_limits_s=limits,
            equilibrium=eq,
            **_walk_kwargs(),
        )
        parts.append(res)
        nt = normal_time_gaps(df, dt_s=SAMPLE_DT_S, window_s=(lo, hi), x_range_m=span, lanes=lanes)
        normal = nt if normal is None else normal + nt
        print(
            f"chunk {k + 1}/{n_chunks} t [{lo:.0f}, {hi:.0f}) rows {len(df):,} changes "
            f"{len(rec):,} walked {int(mask.sum()):,} ({time.time() - t_start:.0f} s, "
            f"peak {peak_rss_mb():.0f} MB)",
            flush=True,
        )
        del df
    assert normal is not None
    result = with_population_ratio(concat_results(parts), normal)
    art = build_artifact(
        result,
        normal,
        source="i24",
        kind="observed",
        zones=zones,
        equilibrium={"s0_m": eq[0], "T_s": eq[1], "source": f"{rel(I24_POPULATION)} means"},
        extraction_counts=counts,
        movement_rows=_merge_movement_counts(mv_parts),
        provenance={
            "data_hash": meta["data_hash"],
            "data": rel(WB_DIR),
            "time_origin": "t = seconds after 06:00:00 CST, 30 Nov 2022",
            "x_axis": "data x [m], front bumper, 0 at MM 62.7, westbound",
            "span_data_x_m": list(span),
            "window_s": [t_lo, t_hi],
            "chunk_s": CHUNK_S,
            "pad_s": I24_PAD_S,
            "citation": I24_CITATION,
        },
        n_boot=args.n_boot,
        seed=args.seed,
        extra_by=(),
        t_start=t_start,
    )
    _write(Path(args.out), art)
    print(f"wrote {args.out} ({len(result.events):,} changes walked)", flush=True)


def run_us101(args: argparse.Namespace) -> None:
    """NGSIM US-101 (raw export), per recording period (a cloud stage)."""
    from us101_data import NGSIM_DIR, data_hash, load_us101

    t_start = time.time()
    periods = load_us101()
    x6 = np.concatenate(
        [p.loc[p["lane"] == US101_AUX[0], "x"].to_numpy(dtype=float) for p in periods.values()]
    )
    zone = us101_weave_zone(x6)
    means = population_means(US101_POPULATION)
    eq = (means["s0"], means["T"])
    parts: list[PostChangeGaps] = []
    normal: NormalTimeGaps | None = None
    counts: dict[str, int] = {}
    mv_parts: list[list[dict[str, Any]]] = []
    per_period: list[dict[str, Any]] = []
    for label, p in periods.items():
        df = pd.DataFrame(
            {
                "t": p["t"].to_numpy(dtype=float),
                "veh_id": label + "-" + p["veh_id"].astype(str),
                "x": p["x"].to_numpy(dtype=float),
                "lane": p["lane"].to_numpy(dtype=np.int64),
                "v": p["v"].to_numpy(dtype=float),
                "length": p["length_m"].to_numpy(dtype=float),
            }
        )
        gaps = lane_change_gaps(
            df, [zone], mainline_lanes=US101_MAINLINE, aux_lanes=US101_AUX, dt_s=NGSIM_DT_S
        )
        rec = gaps.records
        rec["period"] = label
        mask = walk_mask(rec)
        mv_parts.append(movement_counts(rec, mask))
        _counts_add(counts, gaps.counts)
        res = post_change_gaps(
            df, rec, changes=mask, dt_s=NGSIM_DT_S, equilibrium=eq, **_walk_kwargs()
        )
        parts.append(res)
        nt = normal_time_gaps(df, dt_s=NGSIM_DT_S, lanes=(*US101_MAINLINE, *US101_AUX))
        normal = nt if normal is None else normal + nt
        per_period.append(
            {
                "period": label,
                "rows": len(df),
                "vehicles": int(df["veh_id"].nunique()),
                "t_span_s": [float(df["t"].min()), float(df["t"].max())],
                "extraction_counts": gaps.counts,
                "relaxation_counts": res.counts,
            }
        )
        print(
            f"{label}: rows {len(df):,} changes {len(rec):,} walked {int(mask.sum()):,} "
            f"({time.time() - t_start:.0f} s, peak {peak_rss_mb():.0f} MB)",
            flush=True,
        )
    assert normal is not None
    result = with_population_ratio(concat_results(parts), normal)
    art = build_artifact(
        result,
        normal,
        source="us101",
        kind="observed",
        zones=[zone],
        equilibrium={"s0_m": eq[0], "T_s": eq[1], "source": f"{rel(US101_POPULATION)} means"},
        extraction_counts=counts,
        movement_rows=_merge_movement_counts(mv_parts),
        provenance={
            "data_hash": data_hash(),
            "data": rel(NGSIM_DIR),
            "data_version": "raw NGSIM US-101 (data.transportation.gov Socrata 8ect-6jqj), "
            "not the Montanino-Punzo reconstruction",
            "time_origin": "t = frame x 0.1 s, per recording period",
            "x_axis": "local_y [m], front centre, along travel",
            "lanes": "1-5 mainline (1 = leftmost), 6 auxiliary (Ventura on-ramp to Cahuenga "
            "off-ramp), 7 / 8 ramps (not read)",
            "zone_rule": f"weave zone = quantiles {list(US101_ZONE_QUANTILES)} of lane-6 x",
            "periods": per_period,
        },
        n_boot=args.n_boot,
        seed=args.seed,
        extra_by=(),
        t_start=t_start,
    )
    _write(Path(args.out), art)
    print(f"wrote {args.out} ({len(result.events):,} changes walked)", flush=True)


def _weave_on_ramps(meta: dict[str, Any], prov: dict[str, Any]) -> list[dict[str, Any]]:
    """The weave on-ramps of a run: name, attach edge span and the band of its lane 0."""
    cfg_ramps = meta["config"]["network"].get("ramps") or []
    out = []
    for r in meta.get("ramps") or []:
        cfg = cfg_ramps[int(r["index"])] if int(r["index"]) < len(cfg_ramps) else {}
        if r["kind"] != "on" or cfg.get("merge") != "weave" or r.get("attach_x_m") is None:
            continue
        edges = [str(e) for e in prov["edges"]]
        n_lanes = int(prov["edge_lanes"][edges.index(str(r["attach_edge"]))])
        out.append(
            {
                "name": str(r["name"]),
                "x_lo_m": float(r["attach_x_m"]),
                "x_hi_m": float(r["attach_end_x_m"]),
                "aux_band": n_lanes,  # band = n_lanes(edge) - index; the ramp joins index 0
            }
        )
    return out


def run_trajectories(args: argparse.Namespace) -> None:
    """Pooled microsim run directories."""
    from i24_lane_change_gaps import _sim_run_frame

    t_start = time.time()
    parts: list[PostChangeGaps] = []
    normal: NormalTimeGaps | None = None
    counts: dict[str, int] = {}
    mv_parts: list[list[dict[str, Any]]] = []
    runs: list[dict[str, Any]] = []
    zones: list[Zone] = []
    eq_info: dict[str, Any] = {}
    for d in args.sim_run_dir:
        run_dir = Path(d)
        df, zones, mainline, aux, prov = _sim_run_frame(run_dir, float(args.sim_length_m))
        meta = json.loads((run_dir / "meta.json").read_text())
        pop_path = REPO_ROOT / str(prov["fleet_idm_calibration"] or rel(I24_POPULATION))
        means = population_means(pop_path)
        eq = (means["s0"], means["T"])
        eq_info = {"s0_m": eq[0], "T_s": eq[1], "source": f"{rel(pop_path)} means"}
        veh = pd.read_parquet(
            run_dir / "vehicles.parquet", columns=["veh_id", "origin", "destination"]
        )
        groups = {
            str(i): f"{o}->{dst}"
            for i, o, dst in zip(veh["veh_id"], veh["origin"], veh["destination"], strict=True)
        }
        dt_s = float(np.median(np.diff(np.sort(df["t"].unique()))))
        keys: set[tuple[str, float]] = set()
        ramps = _weave_on_ramps(meta, prov)
        if ramps and not args.no_arrival_crossings:
            first = df.sort_values(["veh_id", "t"]).groupby("veh_id", sort=False).head(1)
            first = first.merge(veh[["veh_id", "origin"]].astype({"veh_id": str}), on="veh_id")
            df, keys = add_arrival_crossings(df, first, ramps, dt_s=dt_s)
        gaps = lane_change_gaps(
            df, zones, mainline_lanes=mainline, aux_lanes=aux, dt_s=dt_s, groups=groups
        )
        rec = gaps.records
        rec.insert(0, "seed", prov["seed"])
        arrival = np.array(
            [
                (str(v), round(float(t), 6)) in keys
                for v, t in zip(rec["veh_id"], rec["t"], strict=True)
            ],
            dtype=bool,
        )
        rec["arrival_crossing"] = arrival
        rec.loc[arrival, "confirmed"] = True
        mask = walk_mask(rec)
        mv_parts.append(movement_counts(rec, mask))
        _counts_add(counts, gaps.counts)
        res = post_change_gaps(df, rec, changes=mask, dt_s=dt_s, equilibrium=eq, **_walk_kwargs())
        parts.append(res)
        nt = normal_time_gaps(df, dt_s=dt_s)
        normal = nt if normal is None else normal + nt
        runs.append(
            {
                **{
                    k: prov[k]
                    for k in ("run_dir", "config_hash", "seed", "scenario", "n_collisions")
                },
                "weave_on_ramps": ramps,
                "n_arrival_crossings_added": len(keys),
                "n_arrival_crossings_recorded": int(arrival.sum()),
                "extraction_counts": gaps.counts,
                "relaxation_counts": res.counts,
            }
        )
        print(
            f"{run_dir}: changes {len(rec):,} walked {int(mask.sum()):,} arrival crossings "
            f"{int(arrival.sum())} of {len(keys)} added",
            flush=True,
        )
    if not parts or normal is None:
        raise SystemExit("no run directories given")
    result = with_population_ratio(concat_results(parts), normal)
    art = build_artifact(
        result,
        normal,
        source="trajectories",
        kind="simulated",
        zones=zones,
        equilibrium=eq_info,
        extraction_counts=counts,
        movement_rows=_merge_movement_counts(mv_parts),
        provenance={"runs": runs, "arrival_crossings": not args.no_arrival_crossings},
        n_boot=args.n_boot,
        seed=args.seed,
        extra_by=("arrival_crossing",),
        t_start=t_start,
    )
    _write(Path(args.out), art)
    print(f"wrote {args.out} ({len(result.events):,} changes walked)", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--source", choices=SOURCES, required=True)
    ap.add_argument(
        "--out",
        default=None,
        help="artifact path (default artifacts/lane_change_relaxation_<source>.json)",
    )
    ap.add_argument(
        "--t-range",
        nargs=2,
        type=float,
        default=[0.0, 14400.0],
        metavar=("LO", "HI"),
        help="i24: window [s after 06:00 CST] (default: the whole recording)",
    )
    ap.add_argument(
        "--sim-run-dir", nargs="+", default=None, help="trajectories: microsim run dirs"
    )
    ap.add_argument(
        "--sim-length-m",
        type=float,
        default=None,
        help="trajectories: passenger vehicle length [m] (default microsim.vehicles.VEHICLE_LENGTH_M)",
    )
    ap.add_argument(
        "--no-arrival-crossings",
        action="store_true",
        help="trajectories: do not restore SUMO's arrival-step crossings (WP-82's fix)",
    )
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    if args.out is None:
        args.out = str(REPO_ROOT / "artifacts" / f"lane_change_relaxation_{args.source}.json")
    if args.source == "trajectories":
        if not args.sim_run_dir:
            ap.error("--sim-run-dir is required with --source trajectories")
        if args.sim_length_m is None:
            from microsim.vehicles import VEHICLE_LENGTH_M

            args.sim_length_m = float(VEHICLE_LENGTH_M)
        run_trajectories(args)
    elif args.source == "i24":
        run_i24(args)
    else:
        run_us101(args)


if __name__ == "__main__":
    main()
