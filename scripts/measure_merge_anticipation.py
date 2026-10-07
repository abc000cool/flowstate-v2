"""The merge anticipation reach on I-24 MOTION (M1; docs/MERGE_ANTICIPATION.md).

docs/WEAVE_LOSS_DIAGNOSIS.md §6.3 asks for a measurement of the weave's
``lookahead_m`` (``WEAVE_DEFAULTS``, 120 m, unmeasured) before any capacity
work uses it. This driver measures, with ``calibration.merge_anticipation``,
for every confirmed entering change of the I-24 MOTION westbound day in the
Old Hickory acceleration lane and the Hickory Hollow-Bell Road weave, how far
the entrant had been beside the gap it entered (definition ``gap``, the
primary) and since when its speed had stayed within ±1 / ±2 m/s of its new
leader's (``speed_1``, ``speed_2``, reported only), each censored where the
trajectories run out; the Kaplan-Meier distribution per zone and
changer-speed class with 95 % bootstrap intervals; and the value the
pre-registered rule of docs/MERGE_ANTICIPATION.md §6 proposes, if any.
``WEAVE_DEFAULTS`` is not changed.

A cloud stage (stage ``p6_i24_anticipation`` in ``scripts/gcp/pipeline_i24.sh``): it
reads the 993 MB processed westbound table, which the laptop must not, in
the 15-min chunks, span, ramp zones and lanes of
``scripts/i24_lane_change_gaps.py`` (stage 12, VM X), each chunk loaded with
a pad of that stage's 8 s plus the 120 s lookback. Writes
``artifacts/merge_anticipation_i24.json`` only (summaries, the proposal,
coverage, provenance and a compact per-event table).

Run (VM):  ``uv run --no-sync python scripts/measure_merge_anticipation.py``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from i24_critical_gaps import _peak_rss_mb, git_dirty
from i24_lane_change_gaps import (
    AUX_LANES,
    CHUNK_S,
    I24_MAINLINE_LANES_TUPLE,
    PAD_S,
    REPO_ROOT,
    SAMPLE_DT_S,
    T_RANGE_S,
    WB_DIR,
    X_MARGIN_M,
    git_head,
    i24_zones,
    rel,
)

from calibration.lane_change_gaps import lane_change_gaps
from calibration.loaders.i24motion import I24_CITATION, load_i24_parquet
from calibration.merge_anticipation import (
    CENSOR_REASONS,
    DEFAULT_LOOKBACK_S,
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    PRIMARY_DEFINITION,
    QUANTILES,
    AdoptionRule,
    Stratum,
    anticipation_events,
    change_positions,
    definitions,
    propose,
    stratum_summary,
    summarize_anticipation,
)
from flowstate_core.config import WEAVE_DEFAULTS

OUT = REPO_ROOT / "artifacts" / "merge_anticipation_i24.json"
COVERAGE = REPO_ROOT / "artifacts" / "i24_coverage.json"

MERGE_ZONE = "OH_acceleration_lane"
WEAVE_ZONE = "HH_on_BR_off_weave"
ZONES_MEASURED: tuple[str, ...] = (MERGE_ZONE, WEAVE_ZONE)
"""The ramp zones with an entering movement (``scripts/i24_lane_change_gaps.py`` ``I24_ZONES``)."""

RULE = AdoptionRule(
    strata=(
        Stratum("primary", (WEAVE_ZONE,), "v>=20"),
        Stratum("fallback", (WEAVE_ZONE, MERGE_ZONE), "v>=20"),
    ),
    current_value_m=float(WEAVE_DEFAULTS["lookahead_m"]),
)
"""docs/MERGE_ANTICIPATION.md §6, fixed 2026-10-07 before any run: the weaving section's
entering changes in free flow (changer at 20 m/s or more at the change, the T.H.52 fixture's
regime), else pooled with the acceleration lane's; ``gap`` definition; Kaplan-Meier median."""

SENSITIVITY_MIN_D_CHANGE_M = 200.0
"""Sensitivity: only changes made at least this far past the zone's start, so the zone start
(where ramp-lane fragments appear, docs/I24_DATA.md §6) cannot censor a reach below it."""

EVENT_COLUMNS: tuple[str, ...] = (
    "t",
    "veh_id",
    "zone",
    "speed_class",
    "v",
    "d_change_m",
    "lead_gap_m",
    "lag_gap_m",
    "lead_closing_ms",
    "changer_stitched",
    "partner_stitched",
    "gap_outcome",
    "gap_reach_m",
    "gap_time_s",
    "gap_onset_x_rel_m",
    "gap_onset_lag_dist_m",
    "gap_onset_lead_dv_ms",
    "gap_onset_lag_dv_ms",
    "speed_1_outcome",
    "speed_1_reach_m",
    "speed_2_outcome",
    "speed_2_reach_m",
)
"""Columns of the per-event table in the artifact (the full table is not written)."""

LIMITATIONS: tuple[str, ...] = (
    "Coverage: I-24 MOTION tracks about half of the peak vehicle-time (docs/I24_DATA.md s. 4; "
    "coverage_context below). An untracked vehicle inside the observed gap merges two true gaps "
    "into one, so an entrant can pass from one to the other unseen: the 'gap' reach is expected "
    "to read long, but it is not an upper bound (an untracked true partner also shortens what "
    "can be seen). The speed definitions read speeds, which are coverage-robust, of a recorded "
    "leader that may not be the true one.",
    "Fragments (median 117 m, 9.9 s): a reach is censored where the entrant's or a partner's "
    "track begins; tracker fragment switches are followed by position continuity (2 m) and "
    "absences up to 1 s are bridged (sensitivity: no bridging). The Kaplan-Meier estimate "
    "assumes the censoring is independent of the reach within a stratum: camera boundaries are, "
    "but censoring at the zone start (ramp-lane fragments appear at the gore) may not be - an "
    "entrant that changes soon after the gore and lined up on the ramp is censored early; the "
    "sensitivity restricted to changes at least 200 m past the zone start bounds that.",
    "The anticipation is read from positions: being beside the gap is the model's gap choice "
    "(microsim.runner._weave_choose_gap), not an observed intention. Real drivers who hold a "
    "gap without choosing it (matched speeds by chance) count as anticipating; the speed "
    "definitions and the onset diagnostics show how the gap was reached.",
    "Changes are timed when the vehicle's centre crosses the band edge (mid-manoeuvre); lanes are "
    "lateral bands floor(y / 12 ft) with the 1 s A-B-A debounce; movements are read from lanes "
    "and zones (no routes).",
    "One day (30 Nov 2022), one direction, one weaving section (Hickory Hollow-Bell Road, "
    "585 m) and one acceleration lane (Old Hickory, 1.15 km), congested for most of the "
    "morning; the free-flow class holds the fewest events.",
)


def sha256_file(path: Path, chunk: int = 1 << 24) -> str:
    """The file's sha256 (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def window_label(t_s: float) -> str:
    """The 15-min window (``HH:MM`` CST) of ``t`` seconds after 06:00."""
    k = math.floor(t_s / 900.0)
    minutes = 6 * 60 + 15 * k
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def coverage_context(events: pd.DataFrame, path: Path = COVERAGE) -> dict[str, Any]:
    """Events per 15-min window beside the instrument's estimated coverage in that window.

    The pooled ``recommended`` coverage and lane 4's ``gap_mixture`` (the target lane of
    both zones' entering changes) are read from ``artifacts/i24_coverage.json`` when it exists.
    """
    by_window: dict[str, dict[str, Any]] = {}
    if len(events):
        labels = [window_label(float(t)) for t in events["t"]]
        for w, n in pd.Series(labels).value_counts().sort_index().items():
            by_window[str(w)] = {"n_events": int(n)}
    out: dict[str, Any] = {"source": None, "windows": []}
    if path.exists():
        cov = json.loads(path.read_text())
        out["source"] = rel(path)
        out["sha256"] = sha256_file(path)
        for w in cov.get("windows", []):
            lane4 = next((ln for ln in w.get("lanes", []) if ln.get("lane") == 4), None)
            gm = (lane4 or {}).get("estimators", {}).get("gap_mixture")
            rec = (w.get("pooled") or {}).get("recommended")
            row = by_window.setdefault(str(w["window"]), {"n_events": 0})
            row["coverage_recommended_pooled"] = None if rec is None else round(float(rec), 3)
            row["coverage_gap_mixture_lane4"] = None if gm is None else round(float(gm), 3)
    out["windows"] = [{"window": k, **v} for k, v in sorted(by_window.items())]
    return out


def zone_coverage(events: pd.DataFrame) -> list[dict[str, Any]]:
    """Per zone: events, partners present, stitched shares and censoring per definition."""
    rows: list[dict[str, Any]] = []
    for zone, sub in events.groupby("zone", sort=True):
        row: dict[str, Any] = {
            "zone": str(zone),
            "n_events": len(sub),
            "share_changer_stitched": round(float(sub["changer_stitched"].astype(bool).mean()), 4),
            "share_partner_stitched": round(float(sub["partner_stitched"].astype(bool).mean()), 4),
        }
        for d in definitions():
            oc = sub[f"{d}_outcome"].astype(str)
            ev = oc != "no_partner"
            row[d] = {
                "n_evaluated": int(ev.sum()),
                "n_no_partner": int((~ev).sum()),
                "share_censored": round(float(oc[ev].isin(CENSOR_REASONS).mean()), 4)
                if ev.any()
                else None,
                "outcomes": {str(k): int(v) for k, v in oc.value_counts().sort_index().items()},
            }
        rows.append(row)
    return rows


def event_table(events: pd.DataFrame) -> dict[str, Any]:
    """The compact per-event table: ``columns`` and rounded ``rows``."""
    cols = [c for c in EVENT_COLUMNS if c in events.columns]
    rows: list[list[Any]] = []
    for rec in events[cols].itertuples(index=False, name=None):
        row: list[Any] = []
        for val in rec:
            if isinstance(val, bool | np.bool_):
                row.append(bool(val))
            elif isinstance(val, float | np.floating):
                row.append(None if not math.isfinite(float(val)) else round(float(val), 2))
            elif isinstance(val, np.integer):
                row.append(int(val))
            else:
                row.append(None if val is None else str(val))
        rows.append(row)
    return {"n": len(rows), "columns": cols, "rows": rows}


def sensitivities(
    events: pd.DataFrame, no_bridge: pd.DataFrame, *, n_boot: int, seed: int
) -> list[dict[str, Any]]:
    """The pre-registered sensitivities of the primary strata (reported, never adopted)."""
    from flowstate_core.rng import spawn_seeds

    plan: list[tuple[str, str, pd.DataFrame, str]] = []
    for stratum in RULE.strata:
        sel = stratum.select(events)
        far = sel[sel["d_change_m"] >= SENSITIVITY_MIN_D_CHANGE_M]
        plan.append(("change_at_least_200m_past_zone_start", stratum.name, far, PRIMARY_DEFINITION))
        plan.append(("no_bridging", stratum.name, stratum.select(no_bridge), PRIMARY_DEFINITION))
        for d in definitions()[1:]:
            plan.append((f"definition_{d}", stratum.name, sel, d))
        every = Stratum(stratum.name, stratum.zones, "all").select(events)
        plan.append(("all_speed_classes", stratum.name, every, PRIMARY_DEFINITION))
    seeds = spawn_seeds(seed + 1, len(plan))
    return [
        {
            "sensitivity": name,
            "stratum": sname,
            **stratum_summary(sub, d, qs=(0.25, 0.5, 0.75), n_boot=n_boot, seed=int(s)),
        }
        for (name, sname, sub, d), s in zip(plan, seeds, strict=True)
    ]


def run(args: argparse.Namespace) -> None:
    """The I-24 MOTION westbound day, chunked."""
    t_start = time.time()
    wb_dir = Path(args.wb_dir)
    table = wb_dir / "trajectories.parquet"
    if not table.exists():
        raise SystemExit(
            f"{rel(table)} is missing: this script reads the processed I-24 MOTION table "
            "(a cloud stage; launch the VM with --data-set i24)"
        )
    meta = json.loads((wb_dir / "meta.json").read_text())
    zones, span = i24_zones()
    pad = PAD_S + float(args.lookback_s)
    t_lo, t_hi = args.t_range
    parts: list[pd.DataFrame] = []
    parts_nb: list[pd.DataFrame] = []
    counts: dict[str, int] = {}
    counts_lc: dict[str, int] = {}
    params: dict[str, Any] = {}
    n_chunks = math.ceil((t_hi - t_lo) / CHUNK_S)
    for k in range(n_chunks):
        lo = t_lo + k * CHUNK_S
        hi = min(lo + CHUNK_S, t_hi)
        df = load_i24_parquet(
            wb_dir,
            t_range_s=(lo - pad, hi + pad),
            x_range_m=(span[0] - X_MARGIN_M, span[1] + X_MARGIN_M),
            columns=["t", "veh_id", "x", "lane", "v", "length"],
        )
        lc = lane_change_gaps(
            df,
            zones,
            mainline_lanes=I24_MAINLINE_LANES_TUPLE,
            aux_lanes=AUX_LANES,
            dt_s=SAMPLE_DT_S,
            window_s=(lo, hi),
            x_range_m=span,
        )
        common = {
            "mainline_lanes": I24_MAINLINE_LANES_TUPLE,
            "zone_names": ZONES_MEASURED,
            "dt_s": SAMPLE_DT_S,
            "lookback_s": float(args.lookback_s),
        }
        out = anticipation_events(df, lc.records, zones, **common)  # type: ignore[arg-type]
        nb = anticipation_events(df, lc.records, zones, max_bridge_s=0.0, **common)  # type: ignore[arg-type]
        if len(out.events):
            parts.append(out.events.assign(chunk=k))
            parts_nb.append(nb.events.assign(chunk=k))
        for key, val in out.counts.items():
            counts[key] = counts.get(key, 0) + int(val)
        for key, val in lc.counts.items():
            counts_lc[key] = counts_lc.get(key, 0) + int(val)
        params = {**lc.parameters, **out.parameters}
        print(
            f"chunk {k + 1}/{n_chunks} t [{lo:.0f}, {hi:.0f}) rows {len(df):,} "
            f"changes {len(lc.records):,} events {len(out.events):,} "
            f"({time.time() - t_start:.0f} s, peak {_peak_rss_mb():.0f} MB)",
            flush=True,
        )
        del df, lc, out, nb
    events = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    no_bridge = pd.concat(parts_nb, ignore_index=True) if parts_nb else pd.DataFrame()
    t_fit = time.time()
    n_boot, seed = int(args.n_boot), int(args.seed)
    summary = summarize_anticipation(events, n_boot=n_boot, seed=seed) if len(events) else []
    proposal = (
        propose(events, RULE, n_boot=n_boot, seed=seed)
        if len(events)
        else {
            "rule": RULE.to_dict(),
            "strata": [],
            "selected": None,
            "proposed_value_m": None,
            "current_value_m": RULE.current_value_m,
        }
    )
    sens = sensitivities(events, no_bridge, n_boot=n_boot, seed=seed) if len(events) else []
    print(f"summaries: {time.time() - t_fit:.0f} s", flush=True)
    art: dict[str, Any] = {
        "schema_version": 1,
        "kind": "observed",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/measure_merge_anticipation.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "data_hash": meta.get("data_hash"),
        "table_sha256": sha256_file(table),
        "data": rel(wb_dir),
        "time_origin": "t = seconds after 06:00:00 CST, 30 Nov 2022",
        "x_axis": "data x [m], front bumper, 0 at MM 62.7, westbound",
        "span_data_x_m": list(span),
        "citation": I24_CITATION,
        "spec": "docs/MERGE_ANTICIPATION.md",
        "model_constant": {
            "key": "WEAVE_DEFAULTS['lookahead_m']",
            "value_m": float(WEAVE_DEFAULTS["lookahead_m"]),
            "what_it_sets": "the ramp anticipation (gap choice, the chosen follower's "
            "cooperation and the entrant's easing begin lookahead_m before the weaving "
            "section's start) and the follower search reach (microsim.runner._weave_step, "
            "_weave_choose_gap)",
        },
        "method": {
            **params,
            "module": "calibration.merge_anticipation",
            "window_s": [t_lo, t_hi],
            "chunk_s": CHUNK_S,
            "pad_s": pad,
            "quantiles": list(QUANTILES),
            "n_boot": n_boot,
            "seed": seed,
            "definition_gap": "the distance the entrant travelled from the start of its last "
            "run beside the gap it entered (its target-lane lead and lag at its front were its "
            "new leader and follower, followed through fragment switches) to the change; a run "
            "ends at a failure lasting min_break_s through the vehicles' own motion",
            "definition_speed": "the distance from the start of the last run in which the "
            "entrant's speed stayed within the band of its new leader's",
            "estimator": "Kaplan-Meier under right censoring; 95 % percentile bootstrap over "
            "events, an unidentified resample counted as +inf",
        },
        "zones": [z.to_dict() for z in zones],
        "counts": {"lane_changes": counts_lc, "events": counts},
        "coverage": {
            "by_zone": zone_coverage(events) if len(events) else [],
            "coverage_context": coverage_context(events),
        },
        "summary": summary,
        "change_position_m": change_positions(events) if len(events) else [],
        "preregistered_proposal": proposal,
        "sensitivities": sens,
        "events": event_table(events) if len(events) else {"n": 0, "columns": [], "rows": []},
        "limitations": list(LIMITATIONS),
        "wall_s": round(time.time() - t_start, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    sel = proposal.get("selected")
    print(
        f"wrote {args.out} ({len(events):,} events; proposal: "
        f"{proposal.get('proposed_value_m')} m from {sel})",
        flush=True,
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(OUT), help=f"artifact path (default {rel(OUT)})")
    ap.add_argument("--wb-dir", default=str(WB_DIR), help="the processed westbound table's dir")
    ap.add_argument(
        "--t-range",
        nargs=2,
        type=float,
        default=list(T_RANGE_S),
        metavar=("LO", "HI"),
        help="window of change times [s after 06:00 CST] (default: the whole recording)",
    )
    ap.add_argument("--lookback-s", type=float, default=DEFAULT_LOOKBACK_S)
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run(ap.parse_args(argv))


if __name__ == "__main__":
    main()
