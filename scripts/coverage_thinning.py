"""Coverage thinning on NGSIM US-101: how I-24-like tracking moves the lane-change measures (WP-91).

I-24 MOTION tracks about 0.5–0.65 of the peak vehicle-time, as fragments
(median 117 m, 9.9 s; docs/I24_DATA.md §2, §4). A review of the day's analysis
code (docs/WEAVE_MODEL_PLAN.md, "corrections from the review of the day's
analysis code", table (a)) found that under partial tracking only some
lane-change quantities are bounds: space gaps, lead time gaps, the leader
side's ``ratio_eq`` and the refusals of the lead time term. The others (lag
time gaps, the acceptance's overall refusal share and its other terms, the
fitted critical gaps, ``ratio_pop``) have only expected directions, with a
counterexample. NGSIM US-101 is complete in coverage. This driver thins it
to I-24-like coverage (``calibration.thinning``) and measures everything again,
so the shift of each quantity under a known coverage is measured, not argued.

**Conditions.** The reference (the table as held, no thinning), then for each
thinning model and kept fraction ``F`` (default 1.0, 0.65 and 0.5) five
thinning seeds (``spawn_seeds(seed, 5)``, the same five for every model and
``F``; each period's own seed is ``spawn_seeds(thinning seed, n_periods)[i]``):

* ``vehicle``: a fraction ``F`` of the vehicle ids, whole tracks. At
  ``F = 1.0`` it is the identity (the reference) and is not run again.
* ``fragment``: every track cut into I-24-like fragments (log-normal
  durations, median 9.9 s, σ 0.900 from I-24's committed statistics) with
  untracked spells between them, so the tracked share of vehicle-time is
  ``F``; each fragment gets a new tracker id. At ``F = 1.0`` the tracks are
  only cut (no vehicle-time is lost): it isolates fragmentation from coverage.

**What is measured, per condition and seed** (on each period, pooled; the
weaving zone is the reference's — the span of lane-6 positions is geometry,
not a measurement, so every condition uses the full table's zone):

(a) ``calibration.lane_change_relaxation`` exactly as stage 16
    (``scripts/lane_change_relaxation.py --source us101``) runs it — changes
    walked, ``s0 + vT`` at the ``idm_us101`` means, ``ratio_pop`` against the
    *thinned* table's own population normal (as I-24's is against its own) —
    per zone kind × movement (weave entering / exiting / through, basic
    through; all speeds, and 10–20 m/s for the weave's entering changes), both
    sides, at 0, 5 and 10 s: the sides read, and the medians of ``ratio_pop``,
    ``ratio_eq``, the time gap, the space gap and the partner speed
    ``rel_speed_ms`` (its quartiles at the change);
(b) ``calibration.lane_change_gaps`` records with the weave acceptance at the
    parameters VM X used (the ``acceptance`` block of
    ``artifacts/i24_lane_change_gaps.json``): per zone kind × movement the
    changes, the gap and time-gap p10 / p50 per side, the closing speeds, the
    share the acceptance refuses and the share each term refuses;
(c) ``calibration.critical_gap`` on the target-lane gaps of the 10 s before
    each entering and exiting change in the weaving zone
    (``gap_sequences``, as stage 13 reads I-24): the joint and the separate
    fitted medians per side, per changer-speed class. The reference's fits
    carry a bootstrap (``--n-boot``, 200 by default); the thinned ones are
    point fits (the spread over thinning seeds is their interval);

and the tracking achieved: the kept vehicle-time fraction (kept rows over
rows), vehicles and fragments kept, and the fragment durations.

**Output.** ``artifacts/coverage_thinning_us101.json`` (docs/CONTRACTS.md,
"Coverage thinning"): per measure the reference and, per model and ``F``, the
five seeds' values, their mean, min, max and 95 % t-interval (t over the
seeds, conditional on this one dataset), and the shift (mean − reference).
Summaries only; no trajectory, record or sample is written.

Run (VM):  ``uv run --no-sync python scripts/coverage_thinning.py``
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from i24_critical_gaps import MOVEMENTS as CG_MOVEMENTS  # noqa: E402
from i24_critical_gaps import ZONE_KINDS as CG_ZONE_KINDS  # noqa: E402
from i24_critical_gaps import sequence_mask  # noqa: E402
from lane_change_relaxation import (  # noqa: E402
    NGSIM_DT_S,
    US101_AUX,
    US101_MAINLINE,
    US101_POPULATION,
    _walk_kwargs,
    dumps_compact,
    git_dirty,
    git_head,
    peak_rss_mb,
    population_means,
    rel,
    us101_weave_zone,
    walk_mask,
)

from calibration.critical_gap import (  # noqa: E402
    DEFAULT_SEED as CG_SEED,
)
from calibration.critical_gap import (  # noqa: E402
    driver_gaps,
    fit_groups,
    rejected_points,
    select_drivers,
)
from calibration.lane_change_gaps import (  # noqa: E402
    AcceptanceParams,
    Zone,
    gap_sequences,
    lane_change_gaps,
    summarize_gaps,
)
from calibration.lane_change_relaxation import (  # noqa: E402
    NormalTimeGaps,
    PostChangeGaps,
    concat_results,
    normal_time_gaps,
    post_change_gaps,
    summarize_relaxation,
    with_population_ratio,
)
from calibration.thinning import (  # noqa: E402
    I24_FRAGMENT_MEDIAN_S,
    I24_FRAGMENT_SIGMA,
    THINNING_MODELS,
    ThinnedFrame,
    thin_frame,
)
from flowstate_core.rng import spawn_seeds  # noqa: E402

OUT = REPO_ROOT / "artifacts" / "coverage_thinning_us101.json"
ACCEPTANCE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_lane_change_gaps.json"
STAGE16_ARTIFACT = REPO_ROOT / "artifacts" / "lane_change_relaxation_us101.json"
FRACTIONS: tuple[float, ...] = (1.0, 0.65, 0.5)
N_SEEDS = 5
DEFAULT_SEED = 20260926
"""Master seed of the thinning (``spawn_seeds(seed, n_seeds)``)."""
DEFAULT_N_BOOT = 200
"""Bootstrap replicates of the reference's critical-gap fits (thinned fits: none)."""

RELAX_GROUPS: tuple[tuple[str, str], ...] = (
    ("weave", "entering"),
    ("weave", "exiting"),
    ("weave", "through"),
    ("basic", "through"),
)
RELAX_CLASSES: dict[tuple[str, str], tuple[str, ...]] = {("weave", "entering"): ("10<=v<20",)}
"""Speed classes read beside ``all`` (the class WP-88's rule names)."""
RELAX_OFFSETS: tuple[float, ...] = (0.0, 5.0, 10.0)
RELAX_MEASURES: tuple[str, ...] = ("ratio_pop", "ratio_eq", "time_gap_s", "space_gap_m")
GAP_GROUPS = RELAX_GROUPS
GAP_TERMS: tuple[str, ...] = ("lead_time", "lead_brake", "lag_time", "lag_absorb", "guard")

LIMITATIONS: tuple[str, ...] = (
    "One dataset: NGSIM US-101 as the repository holds it, the raw data.transportation.gov "
    "export (not the Montanino-Punzo reconstruction), congested throughout, 640 m, two "
    "recording periods. The shifts measured here are transferred to I-24 MOTION as estimates, "
    "not as corrections: the two sites differ in length, lanes, speeds and weave geometry.",
    "Neither thinning model reproduces I-24's losses as they are. I-24's are correlated in space "
    "(holes at camera boundaries and under overpasses) and by lane (lane 1 tracked at "
    "0.70-0.76, interior lane 3 at 0.40-0.54; docs/I24_DATA.md), and I-24 has duplicate "
    "fragments and position noise. Both models lose tracking independently of position, lane "
    "and neighbours; the untracked spells' durations are an assumption (log-normal with the "
    "fragments' sigma), as I-24 publishes no statistic of them.",
    "On the 640 m site a US-101 track lasts about a minute, so its fragments are also cut by "
    "the track's ends; the fragment statistics achieved are reported.",
    "The seed intervals are over five thinning draws of one dataset: they say whether a shift "
    "is distinguishable from the thinning's own noise, not how the reference itself would vary "
    "on another day. The reference's critical gaps carry a bootstrap over drivers; its "
    "relaxation medians carry none.",
    "The weaving zone is the reference's (the 0.5-99.5 % span of lane-6 positions in the full "
    "table) in every condition.",
)


# --- inputs -------------------------------------------------------------------------------


def vm_x_acceptance(path: Path = ACCEPTANCE_ARTIFACT) -> AcceptanceParams:
    """The weave acceptance at the parameters VM X used (the artifact's ``acceptance`` block)."""
    block = json.loads(path.read_text())["acceptance"]
    lag_in = block.get("accept_lag_gap_s")
    lag_out = block.get("exit_accept_lag_gap_s")
    return AcceptanceParams(
        accept_gap_s=float(block["accept_gap_s"]),
        exit_accept_gap_s=float(block["exit_accept_gap_s"]),
        s0_m=float(block["s0_m"]),
        T_s=float(block["T_s"]),
        a_max=float(block["a_max"]),
        b=float(block["b"]),
        v0_ms=float(block["v0_ms"]),
        source=f"{rel(path)} acceptance block (VM X): {block.get('source', '')}",
        accept_lag_gap_s=None if lag_in is None else float(lag_in),
        exit_accept_lag_gap_s=None if lag_out is None else float(lag_out),
    )


def us101_frames(periods: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Per period the frame stage 16 reads (``t, veh_id, x, lane, v, length``; period-prefixed ids)."""
    return {
        label: pd.DataFrame(
            {
                "t": p["t"].to_numpy(dtype=float),
                "veh_id": label + "-" + p["veh_id"].astype(str),
                "x": p["x"].to_numpy(dtype=float),
                "lane": p["lane"].to_numpy(dtype=np.int64),
                "v": p["v"].to_numpy(dtype=float),
                "length": p["length_m"].to_numpy(dtype=float),
            }
        )
        for label, p in periods.items()
    }


# --- one condition ------------------------------------------------------------------------


def _num(v: Any) -> float | int | None:
    """A JSON-ready number: finite floats rounded to 4 places, ints kept, the rest None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | np.integer):
        return int(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if math.isfinite(f) else None


def relax_measures(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    """Flat ``relax.*`` keys from :func:`summarize_relaxation` rows (module docstring, (a))."""
    out: dict[str, float | int | None] = {}
    for r in rows:
        group = (str(r.get("zone_kind")), str(r.get("movement")))
        cls = str(r["speed_class"])
        if group not in RELAX_GROUPS or (cls != "all" and cls not in RELAX_CLASSES.get(group, ())):
            continue
        p = f"relax.{group[0]}.{group[1]}.{cls}.{r['side']}"
        out[f"{p}.n_changes"] = _num(r["n_changes"])
        out[f"{p}.n_measured"] = _num(r["n_measured"])
        offs = [float(o) for o in r["offsets_s"]]
        rs = r.get("rel_speed_ms") or {}
        for off in RELAX_OFFSETS:
            if off not in offs:
                continue
            i = offs.index(off)
            at = f"@{off:g}"
            out[f"{p}.n_read{at}"] = _num(r["n"][i])
            for m in RELAX_MEASURES:
                out[f"{p}.{m}.p50{at}"] = _num(r["measures"][m]["p50"][i])
            out[f"{p}.ratio_pop.n{at}"] = _num(r["measures"]["ratio_pop"]["n"][i])
            if rs:
                out[f"{p}.rel_speed_ms.p50{at}"] = _num(rs["p50"][i])
                if off == 0.0:
                    out[f"{p}.rel_speed_ms.p25{at}"] = _num(rs["p25"][i])
                    out[f"{p}.rel_speed_ms.p75{at}"] = _num(rs["p75"][i])
    return out


def gap_measures(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    """Flat ``gaps.*`` keys from :func:`summarize_gaps` rows (all speeds; module docstring, (b))."""
    out: dict[str, float | int | None] = {}
    for r in rows:
        group = (str(r.get("zone_kind")), str(r.get("movement")))
        if group not in GAP_GROUPS or r["speed_class"] != "all":
            continue
        p = f"gaps.{group[0]}.{group[1]}"
        out[f"{p}.n"] = _num(r["n"])
        if not r["n"]:
            continue
        for side in ("lead", "lag"):
            out[f"{p}.share_no_{side}"] = _num(r[f"share_no_{side}"])
            for q in ("p10", "p50"):
                out[f"{p}.{side}_gap_m.{q}"] = _num(r[f"{side}_gap_m"][q])
                out[f"{p}.{side}_time_gap_s.{q}"] = _num(r[f"{side}_time_gap_s"][q])
            out[f"{p}.{side}_closing_ms.p50"] = _num(r[f"{side}_closing_ms"]["p50"])
        model = r.get("model")
        if model:
            out[f"{p}.refused_share"] = _num(model["refused_share"])
            for term in GAP_TERMS:
                out[f"{p}.refused_by.{term}"] = _num(model["refused_by"][term])
    return out


def _fit_median(block: dict[str, Any] | None) -> float | int | None:
    """A fitted median [s], or None when the fit is absent, at a bound or degenerate."""
    if not block or block.get("at_bound") or block.get("degenerate"):
        return None
    return _num(block.get("median_s"))


def cg_measures(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    """Flat ``cg.*`` keys from :func:`fit_groups` rows (weave zone; module docstring, (c))."""
    out: dict[str, float | int | None] = {}
    for r in rows:
        if r.get("zone_kind") != "weave" or r.get("movement") not in CG_MOVEMENTS:
            continue
        p = f"cg.{r['movement']}.{r['speed_class']}"
        out[f"{p}.n_drivers"] = _num(r["n_drivers"])
        out[f"{p}.share_with_rejected"] = _num(r.get("share_with_rejected"))
        joint = r.get("joint") or {}
        fitted = bool(joint.get("fitted"))
        out[f"{p}.joint.n_used"] = _num(joint.get("n_used"))
        out[f"{p}.joint.n_inconsistent"] = _num(joint.get("n_inconsistent"))
        out[f"{p}.joint.lead.median_s"] = _fit_median(joint.get("lead")) if fitted else None
        out[f"{p}.joint.lag.median_s"] = _fit_median(joint.get("lag")) if fitted else None
        for side in ("lead", "lag"):
            sep = r.get(f"separate_{side}") or {}
            out[f"{p}.separate.{side}.n_used"] = _num(sep.get("n_used"))
            out[f"{p}.separate.{side}.median_s"] = _fit_median(sep) if sep.get("fitted") else None
    return out


def cg_intervals(rows: list[dict[str, Any]]) -> dict[str, list[float] | None]:
    """The bootstrap intervals of the fitted medians (the reference's), keyed as :func:`cg_measures`."""
    out: dict[str, list[float] | None] = {}
    for r in rows:
        if r.get("zone_kind") != "weave" or r.get("movement") not in CG_MOVEMENTS:
            continue
        p = f"cg.{r['movement']}.{r['speed_class']}"
        joint = r.get("joint") or {}
        for side in ("lead", "lag"):
            if joint.get("fitted"):
                out[f"{p}.joint.{side}.median_s"] = joint[side]["ci95"]["median_s"]
            sep = r.get(f"separate_{side}") or {}
            if sep.get("fitted"):
                out[f"{p}.separate.{side}.median_s"] = sep["ci95"]["median_s"]
    return out


def tracking_measures(thinned: list[ThinnedFrame]) -> dict[str, float | int | None]:
    """Flat ``tracking.*`` keys: the coverage achieved over the periods."""
    rows = sum(th.counts["n_rows"] for th in thinned)
    kept = sum(th.counts["n_rows_kept"] for th in thinned)
    frags = pd.concat([th.fragments for th in thinned], ignore_index=True)
    inner = frags[~frags["at_track_start"].astype(bool) & ~frags["at_track_end"].astype(bool)]
    dur = frags["duration_s"].to_numpy(dtype=float)
    dur_in = inner["duration_s"].to_numpy(dtype=float)
    return {
        "tracking.kept_time_fraction": _num(kept / rows) if rows else None,
        "tracking.n_vehicles_kept": sum(th.counts["n_vehicles_kept"] for th in thinned),
        "tracking.n_fragments": len(frags),
        "tracking.fragment_duration_s.p50": _num(np.median(dur)) if dur.size else None,
        "tracking.fragment_duration_s.p50_untruncated": _num(np.median(dur_in))
        if dur_in.size
        else None,
        **{
            f"tracking.kept_time_fraction.{th.parameters.get('period', i)}": _num(
                th.kept_time_fraction
            )
            for i, th in enumerate(thinned)
        },
    }


def measure(
    frames: dict[str, pd.DataFrame],
    zone: Zone,
    eq: tuple[float, float],
    acceptance: AcceptanceParams,
    *,
    n_boot: int,
    cg_seed: int = CG_SEED,
) -> tuple[dict[str, float | int | None], dict[str, list[float] | None]]:
    """Every measure of one condition (module docstring (a)–(c)) on its per-period frames."""
    parts: list[PostChangeGaps] = []
    normal: NormalTimeGaps | None = None
    recs: list[pd.DataFrame] = []
    drivers: list[pd.DataFrame] = []
    points: list[pd.DataFrame] = []
    counts: dict[str, int] = {"n_changes": 0, "n_walked": 0, "n_sequenced": 0}
    offset = 0
    for label, df in frames.items():
        gaps = lane_change_gaps(
            df,
            [zone],
            mainline_lanes=US101_MAINLINE,
            aux_lanes=US101_AUX,
            dt_s=NGSIM_DT_S,
            acceptance=acceptance,
        )
        rec = gaps.records
        rec["period"] = label
        mask = walk_mask(rec)
        parts.append(
            post_change_gaps(
                df, rec, changes=mask, dt_s=NGSIM_DT_S, equilibrium=eq, **_walk_kwargs()
            )
        )
        nt = normal_time_gaps(df, dt_s=NGSIM_DT_S, lanes=(*US101_MAINLINE, *US101_AUX))
        normal = nt if normal is None else normal + nt
        seq_mask = sequence_mask(rec)
        counts["n_changes"] += len(rec)
        counts["n_walked"] += int(mask.sum())
        counts["n_sequenced"] += int(seq_mask.sum())
        if len(rec):
            recs.append(rec)
        if seq_mask.any():
            seq = gap_sequences(df, rec, changes=seq_mask, zones=[zone], dt_s=NGSIM_DT_S)
            if len(seq.samples):
                drv = driver_gaps(rec, seq.samples)
                pts = rejected_points(seq.samples)
                drv["change"] = drv["change"].to_numpy(dtype=np.int64) + offset
                pts["change"] = pts["change"].to_numpy(dtype=np.int64) + offset
                drivers.append(drv)
                points.append(pts)
        offset += len(rec)
    assert normal is not None
    result = with_population_ratio(concat_results(parts), normal)
    relax_rows = summarize_relaxation(
        result,
        by=("zone_kind", "movement"),
        speed_classes=(("10<=v<20", 10.0, 20.0),),
        fit_measures=(),
        n_boot=0,
    )
    out: dict[str, float | int | None] = {f"tracking.{k}": v for k, v in counts.items()}
    out.update(relax_measures(relax_rows))
    if recs:
        out.update(gap_measures(summarize_gaps(pd.concat(recs, ignore_index=True))))
    intervals: dict[str, list[float] | None] = {}
    if drivers:
        sel = select_drivers(
            pd.concat(drivers, ignore_index=True),
            movements=CG_MOVEMENTS,
            zone_kinds=CG_ZONE_KINDS,
        )
        cg_rows, _ = fit_groups(
            sel, pd.concat(points, ignore_index=True), n_boot=n_boot, seed=cg_seed
        )
        out.update(cg_measures(cg_rows))
        intervals = cg_intervals(cg_rows) if n_boot > 0 else {}
    return out, intervals


# --- aggregation --------------------------------------------------------------------------


def aggregate(values: list[float | int | None], reference: float | int | None) -> dict[str, Any]:
    """The seeds' values, their mean, min, max, 95 % t-interval and the shift from the reference."""
    fin = np.array([float(v) for v in values if v is not None and math.isfinite(float(v))])
    out: dict[str, Any] = {"per_seed": [_num(v) for v in values], "n": int(fin.size)}
    if fin.size == 0:
        out.update(mean=None, min=None, max=None, ci95=None, shift=None)
        return out
    mean = float(fin.mean())
    out.update(mean=_num(mean), min=_num(fin.min()), max=_num(fin.max()))
    if fin.size >= 2:
        t975 = float(student_t.ppf(0.975, fin.size - 1))
        half = t975 * float(fin.std(ddof=1)) / math.sqrt(fin.size)
        out["ci95"] = [_num(mean - half), _num(mean + half)]
    else:
        out["ci95"] = None
    out["shift"] = _num(mean - float(reference)) if reference is not None else None
    return out


def build_results(
    reference: dict[str, float | int | None],
    conditions: dict[tuple[str, float], list[dict[str, float | int | None]]],
) -> dict[str, dict[str, Any]]:
    """Measure-major results: per key the reference and, per model and F, :func:`aggregate`."""
    keys = set(reference)
    for runs in conditions.values():
        for r in runs:
            keys |= set(r)
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        ref = reference.get(key)
        entry: dict[str, Any] = {"reference": ref}
        for (model, frac), runs in conditions.items():
            entry.setdefault(model, {})[f"{frac:g}"] = aggregate([r.get(key) for r in runs], ref)
        out[key] = entry
    return out


def stage16_check(reference: dict[str, float | int | None]) -> dict[str, Any] | None:
    """The reference against stage 16's committed artifact (same module, same table)."""
    if not STAGE16_ARTIFACT.exists():
        return None
    art = json.loads(STAGE16_ARTIFACT.read_text())
    rows = [r for r in art.get("summary_by_zone_kind", []) if r["speed_class"] == "all"]
    committed = relax_measures(rows)
    keys = [k for k in committed if k.endswith("ratio_pop.p50@0") or k.endswith("ratio_eq.p50@0")]
    pairs = {k: [reference.get(k), committed[k]] for k in sorted(keys)}
    return {
        "artifact": rel(STAGE16_ARTIFACT),
        "artifact_code": art.get("code"),
        "reference_vs_artifact": pairs,
        "all_equal": all(a == b for a, b in pairs.values()),
    }


# --- the run ------------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Load US-101, measure the reference and every thinned condition, build the artifact."""
    from us101_data import NGSIM_DIR, data_hash, load_us101

    t_start = time.time()
    frames = us101_frames(load_us101())
    x6 = np.concatenate(
        [f.loc[f["lane"] == US101_AUX[0], "x"].to_numpy(dtype=float) for f in frames.values()]
    )
    zone = us101_weave_zone(x6)
    means = population_means(US101_POPULATION)
    eq = (means["s0"], means["T"])
    acceptance = vm_x_acceptance(Path(args.acceptance_artifact))
    n_rows = {label: len(f) for label, f in frames.items()}
    print(f"loaded {sum(n_rows.values()):,} rows ({time.time() - t_start:.0f} s)", flush=True)

    t0 = time.time()
    reference, ref_intervals = measure(frames, zone, eq, acceptance, n_boot=args.n_boot)
    reference["tracking.kept_time_fraction"] = 1.0
    print(f"reference: {time.time() - t0:.0f} s, peak {peak_rss_mb():.0f} MB", flush=True)

    seeds = spawn_seeds(args.seed, args.n_seeds)
    labels = list(frames)
    conditions: dict[tuple[str, float], list[dict[str, float | int | None]]] = {}
    walls: dict[str, float] = {}
    for model in args.models:
        for frac in args.fractions:
            if model == "vehicle" and frac >= 1.0:
                continue  # the identity: the reference
            runs: list[dict[str, float | int | None]] = []
            t_c = time.time()
            for s in seeds:
                per = spawn_seeds(s, len(labels))
                thinned: list[ThinnedFrame] = []
                for label, ps in zip(labels, per, strict=True):
                    opts = {"id_prefix": f"{label}-frag-"} if model == "fragment" else None
                    th = thin_frame(frames[label], model, frac, seed=ps, options=opts)
                    th.parameters["period"] = label
                    thinned.append(th)
                vals, _ = measure(
                    {th.parameters["period"]: th.frame for th in thinned},
                    zone,
                    eq,
                    acceptance,
                    n_boot=0,
                )
                vals.update(tracking_measures(thinned))
                runs.append(vals)
                del thinned
            conditions[(model, frac)] = runs
            walls[f"{model}_{frac:g}"] = round(time.time() - t_c, 1)
            kept = [r.get("tracking.kept_time_fraction") for r in runs]
            print(
                f"{model} F={frac:g}: kept {kept} ({walls[f'{model}_{frac:g}']:.0f} s, "
                f"peak {peak_rss_mb():.0f} MB)",
                flush=True,
            )

    art: dict[str, Any] = {
        "schema_version": 1,
        "kind": "observed_thinned",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/coverage_thinning.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "data_hash": data_hash(),
        "data": rel(NGSIM_DIR),
        "data_version": "raw NGSIM US-101 (data.transportation.gov Socrata 8ect-6jqj), "
        "not the Montanino-Punzo reconstruction",
        "rows": n_rows,
        "method": {
            "modules": [
                "calibration.thinning",
                "calibration.lane_change_relaxation",
                "calibration.lane_change_gaps",
                "calibration.critical_gap",
            ],
            "conditions": "the reference (no thinning), then per thinning model and kept fraction "
            "F the same thinning seeds; vehicle-level at F = 1.0 is the identity and is not re-run",
            "vehicle_model": "keep round(F n) vehicle ids, whole tracks, own ids",
            "fragment_model": "per vehicle an alternating renewal process of tracked spells "
            f"(log-normal, median {I24_FRAGMENT_MEDIAN_S} s, sigma {I24_FRAGMENT_SIGMA:.4f}: "
            "I-24 MOTION's committed median and share of fragments >= 30 s, docs/I24_DATA.md "
            "s. 2) and untracked spells (log-normal, the same sigma, mean E[on] (1 - F) / F); "
            "each tracked spell a new tracker id",
            "relaxation": "calibration.lane_change_relaxation as stage 16 runs it "
            "(scripts/lane_change_relaxation.py --source us101): confirmed, non-suspect entering, "
            "exiting and through changes walked; ratio_pop against the thinned table's own "
            "population normal; ratio_eq at the idm_us101 means; medians per zone kind x "
            "movement, all speeds (and 10-20 m/s for weave entering), at 0 / 5 / 10 s; "
            "rel_speed_ms = the front vehicle's speed minus the rear one's (leader side: the new "
            "leader's minus the changer's; follower side: the changer's minus the new "
            "follower's)",
            "gaps": "calibration.lane_change_gaps records with the weave acceptance at VM X's "
            "parameters (the acceptance block below); summarize_gaps per zone kind x movement, "
            "all speeds (confirmed, non-suspect)",
            "critical_gaps": "gap_sequences over the 10 s before each entering and exiting change "
            "in the weaving zone, driver_gaps / rejected_points, select_drivers, fit_groups "
            "(joint and separate log-normal ML fits, per changer-speed class); a median is null "
            "when unfitted, at a bound or degenerate; bootstrap on the reference only",
            "keys": "relax.<zone_kind>.<movement>.<speed_class>.<side>.<measure>.<stat>@<offset>; "
            "gaps.<zone_kind>.<movement>.<statistic>; cg.<movement>.<speed_class>.<estimator>."
            "<side>.<statistic>; tracking.<statistic>",
            "aggregation": "per model and F over the thinning seeds: per_seed, n (finite), mean, "
            "min, max, ci95 (Student t over the seeds; conditional on this dataset), shift = "
            "mean - reference",
        },
        "parameters": {
            "models": list(args.models),
            "fractions": list(args.fractions),
            "n_seeds": args.n_seeds,
            "master_seed": args.seed,
            "thinning_seeds": seeds,
            "period_seeds": "spawn_seeds(thinning seed, n_periods)[i], periods in load order",
            "periods": labels,
            "n_boot_reference": args.n_boot,
            "critical_gap_seed": CG_SEED,
        },
        "zones": [zone.to_dict()],
        "equilibrium": {"s0_m": eq[0], "T_s": eq[1], "source": f"{rel(US101_POPULATION)} means"},
        "acceptance": acceptance.to_dict(),
        "reading_rule": "docs/WEAVE_MODEL_PLAN.md, WP-91 section (written before the numbers)",
        "reference_check_stage16": stage16_check(reference),
        "reference_intervals": ref_intervals,
        "results": build_results(reference, conditions),
        "limitations": list(LIMITATIONS),
        "wall_s_by_condition": walls,
        "wall_s": round(time.time() - t_start, 1),
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }
    return art


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", default=str(OUT), help=f"artifact path (default {rel(OUT)})")
    ap.add_argument("--fractions", nargs="+", type=float, default=list(FRACTIONS))
    ap.add_argument("--models", nargs="+", choices=THINNING_MODELS, default=list(THINNING_MODELS))
    ap.add_argument("--n-seeds", type=int, default=N_SEEDS, help="thinning seeds per condition")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="master thinning seed")
    ap.add_argument(
        "--n-boot", type=int, default=DEFAULT_N_BOOT, help="reference critical-gap bootstrap"
    )
    ap.add_argument(
        "--acceptance-artifact",
        default=str(ACCEPTANCE_ARTIFACT),
        help="the artifact whose acceptance block sets the weave acceptance (VM X's)",
    )
    args = ap.parse_args(argv)
    if any(not 0.0 < f <= 1.0 for f in args.fractions):
        ap.error("--fractions must lie in (0, 1]")
    if args.n_seeds < 1:
        ap.error("--n-seeds must be >= 1")
    art = run(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_compact(art) + "\n"
    assert json.loads(text) == json.loads(json.dumps(art, allow_nan=False))
    out.write_text(text)
    print(f"wrote {out} ({len(art['results'])} measures, {art['wall_s']:.0f} s)", flush=True)


if __name__ == "__main__":
    main()
