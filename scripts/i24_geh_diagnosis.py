"""C7 of docs/PRE_FRISCO_PROGRAM.md: why I-24's link-flow GEH share is about 31 % after B2.

Reads committed JSON only: the I-24 batteries (``artifacts/i24_validation_*.json``),
the observed side, the count-consistency and coverage artifacts, the replica
builder's inputs and the driver population. No simulation, no recording data
(nothing under ``data/``), no trajectories; it runs locally and costs $0. Writes
``artifacts/i24_geh_diagnosis.json``.

**The bins.** The battery's ``link_flows_geh`` row scores 144 bins: 6 sections
(data x = 200, 1000, 2200, 3200, 4800, 5400 m) × 24 five-minute windows of
06:30–08:30 CST, each window's crossings ×12 to an hourly-equivalent flow, the
replicate-mean simulated flow (``simulated.hourly_flows_veh_h_mean``) against
the tracked crossings divided by the recommended coverage
(``observed.hourly_flows_veh_h_recommended``); a bin fails at GEH ≥ 5 (the
row's stored ``geh.vs_recommended_coverage_counts.values``, which this script
recomputes and checks).

**The classes, in the order fixed by the plan** (each failing bin goes to the
first class it meets; the classes overlap and the order decides):

1. ``recording_noise`` — the observed bin is at GEH ≥ 5 from its own centred
   15-min mean (the mean of the window and its two neighbours, the period's
   first and last window padded with themselves, as the observed floor of
   ``validation.report.speed_aggregation_rows`` pads; the flow analogue of
   docs/I24_VALIDATION.md §0.5(a));
2. ``level`` — the section's 2-h flow (the mean of its 24 hourly-equivalent
   flows, simulated replicate mean against observed; R4's measure,
   docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3) is at GEH ≥ 5, and the bin's
   error has the same sign as the section's;
3. ``timing`` — a simulated bin of the same section within ±15 min
   (docs/FRISCO_PROTOCOL.md §5.2's timing tolerance; ±3 windows) is within
   GEH 5 (GEH < 5) of the observed bin;
4. ``shape`` — everything else.

**Also reported** (per battery unless stated):

* the share in C1's station-hour form (docs/FRISCO_PROTOCOL.md §4): hours
  anchored at the study period's start (06:30, window 0), so 06:30–07:30 is
  windows 0–11 and 07:30–08:30 windows 12–23; a station-hour's flow is its
  twelve 5-min counts summed, i.e. the mean of their twelve hourly-equivalent
  flows (``validation.observed.ObservedCorridor.hourly_link_flows``); the
  observed one is the mean of the row's recommended-coverage table over the
  hour. C1's own form pools every replicate's station-hours (20 × 12 = 240
  comparisons, as ``validation.baseline_gate`` pools them, with the
  per-replicate share's 95 % t interval); the replicate-mean form (12
  comparisons, the I-24 row's convention) is reported beside it. **Reported,
  not a criterion**: the I-24 criteria row stays the 144-bin form;
* each section's cross-correlation between the simulated and observed
  hourly-equivalent profiles at lags of up to ±15 min (the same tolerance),
  Pearson on the overlap, a positive lag meaning the simulated profile is
  later, with a three-point parabolic sub-window estimate when the peak is
  interior;
* whether the mainline inflow, counted at the count section (data x = 200 m,
  the inputs' ``mainline.count_x_m``) but inserted at the network entry
  (sim x = 0, ``departPos="base"`` on the first corridor edge,
  ``microsim.vehicles``), is stamped at the count window's own clock time
  (no travel-time shift), and the free-flow travel time from the entry to
  each section at the population's mean desired speed v0 (arm-independent);
* context, not classes: the recording against its own centred 15-min mean
  over all 144 bins; a leave-one-out floor (each seed against the mean of the
  other seeds, at 5 min and at station-hours; the flow analogue of the
  battery's ``rmspe.leave_one_out_floor``); and the GEH that one standard
  deviation of counting noise produces at the recording's coverage under a
  Poisson assumption (observed side, arm-independent);
* two input checks found while building this, outside the classes and the
  rule: the **lane set** each side counts at each section (the observed count
  reads lanes 1–4, ``scripts/i24_data.MAINLINE_LANES``; the simulated count
  reads every lane of the corridor edge the section lies on, from the
  builder inputs' ``corridor_edges`` / ``edge_lengths_m`` / ``edge_lanes``,
  and the recording's auxiliary-lane flow there from the count-consistency
  artifact's ``aux_band_tracked_veh_h``); and the **planned demand over the
  row's target** per window, ``s · c_rec / c_used`` (the corrected arms'
  mainline and on-ramp inflows are the tracked counts over the builder's
  ``coverage_used`` times the carried scale ``s``; the row's target is the
  tracked counts over the recommended coverage ``c_rec``).

**The rule, fixed in the plan before this run** (report-only; no amendment is
written here): timing largest → C8 runs; level largest → back to B5/B6;
recording noise largest → an amendment scoring I-24 on station-hours is
proposed and I-24 is reported both ways; an insertion offset, if found, is
corrected by a computed (not fitted) shift, proposed like B2, before C8. The
rule names no action for shape and no tie-break; a tie lists every tied
class's action.

Thresholds: GEH 5 (``validation.criteria`` profile ``fhwa_default``), ±15 min
(protocol §5.2), the 15-min centred mean (§0.5(a)). Nothing else is a
threshold.

Usage (repo root)::

    uv run --no-sync python scripts/i24_geh_diagnosis.py
    uv run --no-sync python scripts/i24_geh_diagnosis.py \\
        --battery new_arm=artifacts/i24_validation_new_arm.json --primary new_arm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from validation.criteria import get_profile
from validation.metrics import ci, geh, geh_pass_fraction

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "flowstate.i24_geh_diagnosis/1"
SPEC = "docs/PRE_FRISCO_PROGRAM.md, C7 (classes, order and rule fixed 2026-10-07, before this run)"

#: GEH acceptance bound of the link-flow row (strict ``<`` passes).
GEH_THRESHOLD = get_profile("fhwa_default").geh_threshold
#: Timing tolerance: docs/FRISCO_PROTOCOL.md §5.2 ("within 15 minutes").
TIMING_TOLERANCE_S = 900.0
#: Width of the centred mean the recording-noise test compares a bin with
#: (docs/I24_VALIDATION.md §0.5(a): the recording against its 15-min average).
NOISE_MEAN_S = 900.0
S_PER_HOUR = 3600.0

#: The classes, in the fixed order (first met wins).
CLASSES = ("recording_noise", "level", "timing", "shape")
ACTIONS = {
    "timing": "C8 runs (per-window demand timing)",
    "level": "the work goes back to B5/B6",
    "recording_noise": (
        "an amendment scoring I-24 on station-hours is proposed, and I-24 is reported both ways"
    ),
    "shape": "none: the rule names no action for shape",
}
OFFSET_ACTION = (
    "an insertion offset is corrected by a computed (not fitted) shift, proposed like B2, before C8"
)

#: (label, artifact, role): the B2 arm first (the primary), then the comparisons.
DEFAULT_BATTERIES: tuple[tuple[str, str, str], ...] = (
    (
        "dc_refit_rc",
        "artifacts/i24_validation_dc_refit_rc.json",
        "B2 arm (i24_replica_flow_rc_speedcal_dc_refit), Phase A's candidate; primary",
    ),
    (
        "dc_refit_p13ref",
        "artifacts/i24_validation_dc_refit_p13ref.json",
        "p13 reference: _dc_refit on the uncorrected ramp counts",
    ),
    (
        "p14_b2_ref",
        "artifacts/i24_validation_p14_b2_ref.json",
        "B2 alone re-run on p14's VM",
    ),
    ("p14_b1b2", "artifacts/i24_validation_p14_b1b2.json", "B1 on top of B2"),
    (
        "p14_refit2_b2",
        "artifacts/i24_validation_p14_refit2_b2.json",
        "B2 at the FHWA re-sequence's s = 1.125 (not adopted)",
    ),
    (
        "p12_dc_refit_b1",
        "artifacts/i24_validation_p12_dc_refit_b1.json",
        "B1 on _dc_refit (uncorrected ramp counts)",
    ),
    (
        "p12_canonical_b1",
        "artifacts/i24_validation_p12_canonical_b1.json",
        "B1 on the canonical flow arm",
    ),
    ("p12_dc_b1", "artifacts/i24_validation_p12_dc_b1.json", "B1 on _dc"),
)
DEFAULT_PRIMARY = "dc_refit_rc"
OBSERVED = "artifacts/i24_validation_observed.json"
COUNT_CONSISTENCY = "artifacts/i24_count_consistency.json"
COVERAGE = "artifacts/i24_coverage.json"
#: The validator's geometry (``scripts/i24_validate.py`` places every arm's
#: sections with it) and the B2 family's own builder inputs.
REPLICA_INPUTS = "artifacts/i24_replica_inputs.json"
FAMILY_INPUTS = "artifacts/i24_replica_inputs_flow_rc.json"
#: The B2 arm's fleet (its scenario file's ``fleet.idm_calibration``) and the
#: base population it shifts (``a_max`` only).
POPULATIONS = ("artifacts/idm_i24_capacity_amax_k1.0.json", "artifacts/idm_i24_capacity.json")
#: The demand scale the B2 arm carries (its scenario header: ``best.scale`` of
#: this fit, made on ``_dc_refit``, config hash ada3f406504b).
DEMAND_SCALE = "artifacts/demand_scale_i24_flow_dc.json"
OUT = "artifacts/i24_geh_diagnosis.json"
#: Lanes the observed count reads (``scripts/i24_data.MAINLINE_LANES``: 1-4).
OBSERVED_LANES = 4

#: The files whose sha256 the artifact records (the script and what it imports).
CODE_PATHS = (
    "scripts/i24_geh_diagnosis.py",
    "packages/validation/validation/metrics.py",
    "packages/validation/validation/criteria.py",
)


# --- small helpers ------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path) -> str:
    p = path.resolve()
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p)


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _read(path: str | Path) -> Any:
    return json.loads(_resolve(path).read_text())


def _code() -> dict[str, Any]:
    """The script's commit, whether its files differ from it, and their sha256."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    status = git("status", "--porcelain", "--", *CODE_PATHS)
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": None if status is None else bool(status),
        "sha256": {p: _sha256(REPO_ROOT / p) for p in CODE_PATHS if (REPO_ROOT / p).is_file()},
    }


def _json_safe(obj: object) -> object:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _r(x: float, nd: int) -> float | None:
    return round(float(x), nd) if math.isfinite(float(x)) else None


def clock(start_hhmm: str, seconds: float) -> str:
    """``HH:MM`` of ``start_hhmm`` plus ``seconds``."""
    h, m = (int(v) for v in start_hhmm.split(":"))
    total = h * 60 + m + round(seconds / 60.0)
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def n_windows(span_s: float, window_s: float) -> int:
    """Whole windows in ``span_s``; refuses a span that is not a whole number of them."""
    k = round(span_s / window_s)
    if k < 1 or abs(k * window_s - span_s) > 1e-9:
        raise ValueError(f"{span_s:g} s is not a whole number of {window_s:g} s windows")
    return k


# --- the four classes -----------------------------------------------------------


def geh_matrix(sim: np.ndarray, obs: np.ndarray) -> np.ndarray:
    """GEH of every (section, window) bin, hourly-equivalent flows [veh/h]."""
    if sim.shape != obs.shape:
        raise ValueError(f"shapes differ: {sim.shape} vs {obs.shape}")
    out = np.empty(sim.shape)
    for idx in np.ndindex(sim.shape):
        out[idx] = geh(float(sim[idx]), float(obs[idx]))
    return out


def centred_mean(flows: np.ndarray, k: int) -> np.ndarray:
    """Centred ``k``-window mean along the time axis (axis 1), edges padded with themselves.

    The padding is ``validation.report.speed_aggregation_rows``' observed floor's
    (``np.pad(..., mode="edge")``): the first window's mean is ``(2 q_0 + q_1) / 3``
    for ``k = 3``.
    """
    if k < 1 or k % 2 == 0:
        raise ValueError(f"the centred mean needs an odd window count, got {k}")
    h = k // 2
    pad = np.pad(np.asarray(flows, dtype=np.float64), ((0, 0), (h, h)), mode="edge")
    n = flows.shape[1]
    return np.mean([pad[:, j : j + n] for j in range(k)], axis=0)


def classify(
    sim: np.ndarray,
    obs: np.ndarray,
    window_s: float,
    *,
    failing: np.ndarray | None = None,
) -> dict[str, Any]:
    """Every bin's tests and every failing bin's class (module docstring).

    Args:
        sim: Simulated hourly-equivalent flows ``[section][window]`` [veh/h].
        obs: Observed hourly-equivalent flows on the same bins [veh/h].
        window_s: Window length [s].
        failing: Which bins fail; default GEH ≥ 5 recomputed from ``sim``/``obs``
            (the battery passes its row's stored values).

    Returns:
        ``geh``, ``failing``, ``centred``, ``noise_geh``, ``noise``, ``level_2h``
        (per section: simulated, observed, GEH, sign, fails), ``level``,
        ``timing_shift`` / ``timing_geh`` (the best simulated bin within the
        tolerance, in windows and its GEH), ``timing`` and ``labels`` (the class
        of each failing bin, ``None`` for a passing one), all ``[section][window]``.
    """
    sim = np.asarray(sim, dtype=np.float64)
    obs = np.asarray(obs, dtype=np.float64)
    g = geh_matrix(sim, obs)
    fails = g >= GEH_THRESHOLD if failing is None else np.asarray(failing, dtype=bool)
    n_sec, n_win = obs.shape
    k_noise = n_windows(NOISE_MEAN_S, window_s)
    centred = centred_mean(obs, k_noise)
    noise_geh = geh_matrix(obs, centred)
    noise = noise_geh >= GEH_THRESHOLD

    sim_2h = sim.mean(axis=1)
    obs_2h = obs.mean(axis=1)
    level_2h = []
    level = np.zeros(obs.shape, dtype=bool)
    for s in range(n_sec):
        g2 = geh(float(sim_2h[s]), float(obs_2h[s]))
        sign = int(np.sign(sim_2h[s] - obs_2h[s]))
        level_2h.append(
            {
                "sim_veh_h": float(sim_2h[s]),
                "obs_veh_h": float(obs_2h[s]),
                "geh": g2,
                "sign": sign,
                "fails": g2 >= GEH_THRESHOLD,
            }
        )
        if g2 >= GEH_THRESHOLD:
            level[s] = np.sign(sim[s] - obs[s]) == sign

    k_time = n_windows(TIMING_TOLERANCE_S, window_s)
    timing_shift = np.zeros(obs.shape, dtype=np.int64)
    timing_geh = np.full(obs.shape, np.nan)
    for s in range(n_sec):
        for w in range(n_win):
            cands = [
                (geh(float(sim[s, w + k]), float(obs[s, w])), abs(k), k)
                for k in range(-k_time, k_time + 1)
                if k != 0 and 0 <= w + k < n_win
            ]
            if cands:
                best = min(cands)
                timing_geh[s, w] = best[0]
                timing_shift[s, w] = best[2]
    timing = timing_geh < GEH_THRESHOLD

    labels: list[list[str | None]] = []
    for s in range(n_sec):
        row: list[str | None] = []
        for w in range(n_win):
            if not fails[s, w]:
                row.append(None)
            elif noise[s, w]:
                row.append("recording_noise")
            elif level[s, w]:
                row.append("level")
            elif timing[s, w]:
                row.append("timing")
            else:
                row.append("shape")
        labels.append(row)
    return {
        "geh": g,
        "failing": fails,
        "centred": centred,
        "noise_geh": noise_geh,
        "noise": noise,
        "level_2h": level_2h,
        "level": level,
        "timing_shift": timing_shift,
        "timing_geh": timing_geh,
        "timing": timing,
        "labels": labels,
    }


def tally(
    labels: Sequence[Sequence[str | None]], sections_m: Sequence[float], window_s: float
) -> dict[str, Any]:
    """Failing bins per class, overall, per section and per hour (anchored at window 0)."""
    per_hour = n_windows(S_PER_HOUR, window_s)
    n_win = len(labels[0]) if labels else 0
    counts = dict.fromkeys(CLASSES, 0)
    by_section = []
    for s, row in enumerate(labels):
        c = dict.fromkeys(CLASSES, 0)
        for lab in row:
            if lab is not None:
                c[lab] += 1
                counts[lab] += 1
        by_section.append({"section_m": float(sections_m[s]), "n_fail": sum(c.values()), **c})
    by_hour = []
    for h0 in range(0, n_win, per_hour):
        c = dict.fromkeys(CLASSES, 0)
        for row in labels:
            for lab in row[h0 : h0 + per_hour]:
                if lab is not None:
                    c[lab] += 1
        by_hour.append(
            {
                "windows": [h0, min(h0 + per_hour, n_win) - 1],
                "n_fail": sum(c.values()),
                **c,
            }
        )
    return {
        "counts": counts,
        "n_fail": sum(counts.values()),
        "by_section": by_section,
        "by_hour": by_hour,
    }


def membership(cls: Mapping[str, Any]) -> dict[str, Any]:
    """Which tests each failing bin meets regardless of the order (the overlap).

    Under any order of the four classes a class collects at least the bins that
    meet only its test and at most every bin that meets it (shape, meeting
    none, is fixed). ``largest_under_every_order`` names the class whose least
    exceeds every other class's most, else ``None``: the fixed order then
    decides the verdict.
    """
    fails = cls["failing"]
    flags = {"recording_noise": cls["noise"], "level": cls["level"], "timing": cls["timing"]}
    meets = {name: int((f & fails).sum()) for name, f in flags.items()}
    patterns: dict[str, int] = {}
    only = dict.fromkeys(CLASSES, 0)
    for idx in zip(*np.nonzero(fails), strict=True):
        met = [name for name, f in flags.items() if f[idx]]
        key = "+".join(met) if met else "none (shape)"
        patterns[key] = patterns.get(key, 0) + 1
        if len(met) == 1:
            only[met[0]] += 1
        elif not met:
            only["shape"] += 1
    most = {**meets, "shape": only["shape"]}
    robust = [
        c for c in CLASSES if all(only[c] > most[o] for o in CLASSES if o != c) and only[c] > 0
    ]
    return {
        "meets": meets,
        "patterns": dict(sorted(patterns.items())),
        "least_under_any_order": only,
        "most_under_any_order": most,
        "largest_under_every_order": robust[0] if robust else None,
    }


def apply_rule(counts: Mapping[str, int], offset_found: bool | None) -> dict[str, Any]:
    """The plan's fixed rule on the class counts (report-only).

    Args:
        counts: Failing bins per class.
        offset_found: Whether the insertion offset was found (``None``: not assessed).

    Returns:
        ``largest`` (the class or classes with the most failing bins), ``tie``,
        the flags ``c8`` / ``back_to_b5_b6`` / ``station_hour_amendment`` /
        ``insertion_offset_correction``, ``actions`` and a one-line ``text``.
    """
    total = sum(counts.get(c, 0) for c in CLASSES)
    if total == 0:
        largest: list[str] = []
    else:
        top = max(counts.get(c, 0) for c in CLASSES)
        largest = [c for c in CLASSES if counts.get(c, 0) == top]
    actions = [ACTIONS[c] for c in largest]
    if offset_found:
        actions.append(OFFSET_ACTION)
    if not largest:
        head = "no failing bins: no class is largest"
    else:
        head = " and ".join(f"{c} ({counts[c]} of {total})" for c in largest)
        head = ("largest classes tie: " if len(largest) > 1 else "largest class: ") + head
    text = head + "; " + "; ".join(actions) if actions else head
    if offset_found is None:
        text += "; insertion offset not assessed"
    elif not offset_found:
        text += "; no insertion offset found"
    return {
        "largest": largest,
        "tie": len(largest) > 1,
        "c8": "timing" in largest,
        "back_to_b5_b6": "level" in largest,
        "station_hour_amendment": "recording_noise" in largest,
        "insertion_offset_correction": bool(offset_found),
        "actions": actions,
        "text": text,
        "report_only": "no amendment is written by this diagnosis",
    }


# --- also reported ----------------------------------------------------------------


def station_hours(
    obs: np.ndarray,
    counts_per_replicate: np.ndarray | None,
    sim_mean: np.ndarray,
    sections_m: Sequence[float],
    window_s: float,
    start_hhmm: str,
    anchor_window: int = 0,
) -> dict[str, Any]:
    """The GEH share in C1's station-hour form (module docstring); reported, not a criterion.

    Args:
        obs: Observed hourly-equivalent flows ``[section][window]`` (the row's table).
        counts_per_replicate: Simulated crossings ``[replicate][section][window]``,
            or ``None`` (then only the replicate-mean form is formed).
        sim_mean: Replicate-mean hourly-equivalent flows on the same bins.
        sections_m: Section positions [m].
        window_s: Window length [s].
        start_hhmm: Clock time of window 0.
        anchor_window: Window the first hour starts at (C1: the study period's start).
    """
    per_hour = n_windows(S_PER_HOUR, window_s)
    n_sec, n_win = obs.shape
    starts = list(range(anchor_window, n_win - per_hour + 1, per_hour))
    obs_h = np.array([[obs[s, k : k + per_hour].mean() for k in starts] for s in range(n_sec)])
    mean_h = np.array(
        [[sim_mean[s, k : k + per_hour].mean() for k in starts] for s in range(n_sec)]
    )
    g_mean = geh_matrix(mean_h, obs_h)
    hours = [
        {
            "windows": [k, k + per_hour - 1],
            "clock": f"{clock(start_hhmm, k * window_s)}-{clock(start_hhmm, (k + per_hour) * window_s)}",
        }
        for k in starts
    ]
    table = []
    pooled: dict[str, Any] = {"formed": False, "reason": "no per-replicate counts"}
    per_rep_pass = None
    if counts_per_replicate is not None:
        c = np.asarray(counts_per_replicate, dtype=np.float64)
        scale = S_PER_HOUR / (per_hour * window_s)
        rep_h = np.stack(
            [c[:, :, k : k + per_hour].sum(axis=2) * scale for k in starts], axis=2
        )  # [replicate][section][hour]
        g_rep = np.empty(rep_h.shape)
        for idx in np.ndindex(rep_h.shape):
            g_rep[idx] = geh(float(rep_h[idx]), float(obs_h[idx[1], idx[2]]))
        values = g_rep.ravel().tolist()
        shares = [geh_pass_fraction(g_rep[r].ravel().tolist()) for r in range(g_rep.shape[0])]
        interval = ci(shares)
        pooled = {
            "formed": True,
            "n_comparisons": len(values),
            "n_replicates": int(g_rep.shape[0]),
            "share": geh_pass_fraction(values),
            "per_replicate_share": {
                "mean": interval.mean,
                "lo95": interval.lo95,
                "hi95": interval.hi95,
                "min": float(min(shares)),
                "max": float(max(shares)),
            },
        }
        per_rep_pass = (g_rep < GEH_THRESHOLD).sum(axis=0)
    for s in range(n_sec):
        for h in range(len(starts)):
            row = {
                "section_m": float(sections_m[s]),
                "hour": hours[h]["clock"],
                "obs_veh_h": _r(obs_h[s, h], 1),
                "sim_mean_veh_h": _r(mean_h[s, h], 1),
                "geh_replicate_mean": _r(g_mean[s, h], 3),
            }
            if per_rep_pass is not None:
                row["replicates_passing"] = int(per_rep_pass[s, h])
            table.append(row)
    return {
        "status": "reported, not a criterion: the I-24 criteria row stays the 144-bin 5-min form",
        "hours": hours,
        "pooled_over_replicates": pooled,
        "replicate_mean": {
            "n_comparisons": int(g_mean.size),
            "share": geh_pass_fraction(g_mean.ravel().tolist()),
        },
        "table": table,
    }


def cross_correlation(
    sim: np.ndarray, obs: np.ndarray, sections_m: Sequence[float], window_s: float
) -> list[dict[str, Any]]:
    """Per section, Pearson r of ``sim[w + L]`` with ``obs[w]`` at |L| ≤ the timing tolerance.

    A positive lag means the simulated profile is later than the observed one.
    The best lag maximises r (ties to the smaller |L|); when it is interior,
    ``lag_parabolic_s`` refines it by a three-point parabola through r around
    the peak (approximate; 5-min windows cannot resolve less).
    """
    k_max = n_windows(TIMING_TOLERANCE_S, window_s)
    n_win = obs.shape[1]
    out = []
    for s in range(obs.shape[0]):
        rs: dict[int, float] = {}
        for lag in range(-k_max, k_max + 1):
            a = sim[s, max(lag, 0) : n_win + min(lag, 0)]
            b = obs[s, max(-lag, 0) : n_win - max(lag, 0)]
            if a.size < 3 or np.std(a) == 0.0 or np.std(b) == 0.0:
                rs[lag] = math.nan
            else:
                rs[lag] = float(np.corrcoef(a, b)[0, 1])
        finite = [(r, -abs(lag), lag) for lag, r in rs.items() if math.isfinite(r)]
        best = max(finite)[2] if finite else None
        parabolic = None
        if best is not None and -k_max < best < k_max:
            r0, rm, rp = rs[best], rs[best - 1], rs[best + 1]
            den = rm - 2.0 * r0 + rp
            if all(math.isfinite(v) for v in (r0, rm, rp)) and den < 0.0:
                parabolic = (best + 0.5 * (rm - rp) / den) * window_s
        out.append(
            {
                "section_m": float(sections_m[s]),
                "r_by_lag": [
                    {"lag_windows": lag, "lag_min": lag * window_s / 60.0, "r": _r(r, 4)}
                    for lag, r in rs.items()
                ],
                "best_lag_windows": best,
                "best_lag_min": None if best is None else best * window_s / 60.0,
                "r_best": None if best is None else _r(rs[best], 4),
                "r_lag0": _r(rs[0], 4),
                "at_search_edge": best is not None and abs(best) == k_max,
                "lag_parabolic_s": None if parabolic is None else _r(parabolic, 1),
            }
        )
    return out


def leave_one_out(
    counts_per_replicate: np.ndarray | None, window_s: float, sections_m: Sequence[float]
) -> dict[str, Any]:
    """Each seed against the mean of the other seeds: GEH < 5 shares at 5 min and station-hours.

    The flow analogue of the battery's ``rmspe.leave_one_out_floor``: how well one
    simulated day matches the ensemble mean at each resolution (full counting,
    no tracking coverage). Context, not a class.
    """
    if counts_per_replicate is None:
        return {"formed": False, "reason": "no per-replicate counts"}
    c = np.asarray(counts_per_replicate, dtype=np.float64)
    n_rep = c.shape[0]
    if n_rep < 2:
        return {"formed": False, "reason": "fewer than two replicates"}
    per_hour = n_windows(S_PER_HOUR, window_s)
    scale = S_PER_HOUR / window_s
    starts = list(range(0, c.shape[2] - per_hour + 1, per_hour))
    five, hourly = [], []
    for r in range(n_rep):
        others = np.delete(c, r, axis=0).mean(axis=0)
        five.append(geh_pass_fraction(geh_matrix(c[r] * scale, others * scale).ravel().tolist()))
        mine_h = np.array(
            [[c[r, s, k : k + per_hour].sum() for k in starts] for s in range(c.shape[1])]
        )
        oth_h = np.array(
            [[others[s, k : k + per_hour].sum() for k in starts] for s in range(c.shape[1])]
        )
        hourly.append(geh_pass_fraction(geh_matrix(mine_h, oth_h).ravel().tolist()))

    def summary(v: list[float]) -> dict[str, Any]:
        i = ci(v)
        return {"mean": i.mean, "lo95": i.lo95, "hi95": i.hi95, "min": min(v), "max": max(v)}

    return {
        "formed": True,
        "n_replicates": n_rep,
        "five_min": summary(five),
        "station_hour": summary(hourly),
        "n_sections": len(sections_m),
    }


def recording_vs_own_mean(cls: Mapping[str, Any], sections_m: Sequence[float]) -> dict[str, Any]:
    """The recording's 144 bins against their own centred 15-min mean (context)."""
    ok = cls["noise_geh"] < GEH_THRESHOLD
    return {
        "share_geh_under_5": float(ok.mean()),
        "n_bins": int(ok.size),
        "by_section": [
            {"section_m": float(x), "share_geh_under_5": float(ok[s].mean())}
            for s, x in enumerate(sections_m)
        ],
    }


def counting_noise(
    counts_tracked: np.ndarray, coverage: Sequence[float], window_s: float
) -> dict[str, Any]:
    """GEH of one standard deviation of counting noise at the recording's coverage.

    Assumption (stated, not tested): each 5-min tracked count ``N`` is Poisson,
    so the recommended-coverage hourly-equivalent flow ``Q = (3600 / window) N / c``
    has standard deviation ``Q / √N`` and, to first order (GEH ≈ |ΔQ| / √Q near
    agreement), one standard deviation is GEH ``√((3600 / window) / c)``,
    independent of ``N``. A station-hour sums twelve such windows:
    ``√(Σ N/c²) / √(Σ N/c)``. ``pass_share_if_model_exact`` is the share of bins
    a model equal to the expected count would pass under that assumption
    (``erf(5 / (√2 · GEH_1σ))``, averaged over the bins). Traffic counts are not
    Poisson in congestion; this is a scale, not a floor.
    """
    n = np.asarray(counts_tracked, dtype=np.float64)
    cov = np.asarray(coverage, dtype=np.float64)
    per_hour = n_windows(S_PER_HOUR, window_s)
    k = S_PER_HOUR / window_s
    g5 = np.sqrt(k / cov)
    starts = list(range(0, n.shape[1] - per_hour + 1, per_hour))
    gh = []
    for s in range(n.shape[0]):
        for k0 in starts:
            nn = n[s, k0 : k0 + per_hour]
            cc = cov[k0 : k0 + per_hour]
            q = float((nn / cc).sum())
            gh.append(math.sqrt(float((nn / cc**2).sum())) / math.sqrt(q) if q > 0 else math.nan)

    def pass_share(scales: Sequence[float]) -> float:
        return float(np.mean([math.erf(GEH_THRESHOLD / (math.sqrt(2.0) * g)) for g in scales]))

    g5_bins = np.broadcast_to(g5, n.shape).ravel().tolist()
    return {
        "assumption": "tracked 5-min counts are Poisson (not tested; congested counts are not)",
        "five_min": {
            "geh_one_sd_min": float(g5.min()),
            "geh_one_sd_max": float(g5.max()),
            "pass_share_if_model_exact": pass_share(g5_bins),
        },
        "station_hour": {
            "geh_one_sd_min": float(np.nanmin(gh)),
            "geh_one_sd_max": float(np.nanmax(gh)),
            "pass_share_if_model_exact": pass_share([g for g in gh if math.isfinite(g)]),
        },
        "coverage_per_window": [float(c) for c in cov],
    }


def insertion_offset(
    inputs: Mapping[str, Any],
    v0_ms: float,
    sections_m: Sequence[float],
    window_s: float,
    *,
    family_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Whether the mainline inflow is stamped without a travel-time shift, and its size.

    The builder (``scripts/i24_build_replica.py``) counts the mainline inflow at
    ``mainline.count_x_m`` per window and gives window ``i``'s rate the step time
    ``to_sim_time(t_lo + i·window)`` = ``warmup + i·window`` (the first step at 0 also
    covers the warm-up); the vehicles depart at the network entry (sim x = 0). A
    step time equal to that is a stamp with no travel-time shift. The free-flow
    time from the entry to data x is ``(a + b·x) / v0`` with the validator's
    mapping sim x = a + b·data x.

    Args:
        inputs: Builder inputs (``geometry``, ``mainline``, ``study_period``) whose
            mapping places the sections (the validator's).
        v0_ms: The population's mean desired speed [m/s].
        sections_m: Section positions, data x [m].
        window_s: Window length [s].
        family_inputs: Another family's builder inputs, checked the same way.
    """

    def stamping(inp: Mapping[str, Any]) -> dict[str, Any]:
        steps = inp["mainline"]["inflow_steps_sim"]
        warm = float(inp["study_period"]["warmup_s"])
        shifts = [float(steps[i][0]) - (warm + i * window_s) for i in range(1, len(steps))]
        return {
            "n_steps": len(steps),
            "first_step_s": float(steps[0][0]),
            "max_abs_shift_s": max((abs(x) for x in shifts), default=0.0),
            "stamped_without_shift": all(abs(x) < 1e-6 for x in shifts)
            and float(steps[0][0]) == 0.0,
            "count_x_m": float(inp["mainline"]["count_x_m"]),
            "sim_x_of_data_x": dict(inp["geometry"]["sim_x_of_data_x"]),
        }

    geo = inputs["geometry"]["sim_x_of_data_x"]
    a, b = float(geo["a"]), float(geo["b"])
    count_x = float(inputs["mainline"]["count_x_m"])
    own = stamping(inputs)
    d_count = a + b * count_x
    offset_s = d_count / v0_ms
    per_section = [
        {
            "section_m": float(x),
            "distance_from_entry_m": a + b * float(x),
            "free_flow_from_entry_s": (a + b * float(x)) / v0_ms,
            "free_flow_from_count_section_s": b * (float(x) - count_x) / v0_ms,
        }
        for x in sections_m
    ]
    out: dict[str, Any] = {
        "entry": "sim x = 0, the first corridor edge's start (departPos='base', microsim.vehicles)",
        "count_section_m": count_x,
        "v0_ms": v0_ms,
        "stamping": own,
        "distance_entry_to_count_section_m": d_count,
        "offset_s": offset_s,
        "offset_windows": offset_s / window_s,
        "offset_vs_timing_tolerance": offset_s / TIMING_TOLERANCE_S,
        "per_section": per_section,
        "found": bool(own["stamped_without_shift"] and offset_s > 0.0),
        "reading": (
            "every section, the count section included, sees the simulated inflow later than the "
            "recorded one by the entry-to-count-section travel time; downstream of the count section "
            "both sides propagate, so this part of the offset is common to all six sections"
        ),
        "bounds": (
            "free flow at the population's mean v0: the fleet's mean time is at least this (Jensen, "
            "heterogeneous v0), and an edge limit below v0 (SUMO's desired speed is min(v0, "
            "speedFactor x limit); the network is not read here), insertion at departSpeed='avg' or "
            "congestion only lengthen it"
        ),
    }
    if family_inputs is not None:
        fam = stamping(family_inputs)
        fa = family_inputs["geometry"]["sim_x_of_data_x"]
        out["family_check"] = {
            **fam,
            "max_abs_section_distance_difference_m": max(
                abs((float(fa["a"]) + float(fa["b"]) * float(x)) - (a + b * float(x)))
                for x in sections_m
            ),
        }
    return out


def section_lanes(
    mapping: Mapping[str, Any],
    network: Mapping[str, Any],
    sections_m: Sequence[float],
    aux_tracked_veh_h: Sequence[float | None],
    obs_tracked: np.ndarray,
    obs_rec: np.ndarray,
) -> list[dict[str, Any]]:
    """The lane set each side counts at each section (an input check, outside the classes).

    The simulated trajectories carry every vehicle on a corridor edge, in any lane
    (``microsim.runner``: x = edge offset + lane position), and the validator counts
    their crossings without a lane filter; the observed count reads lanes 1-4. A
    section on an edge with more than four lanes therefore counts the auxiliary
    lane on the simulated side only. The recording's flow in that lane is the
    count-consistency artifact's tracked auxiliary-band crossings; it is bounded
    here below by itself (coverage 1) and given at the coverage the row's table
    applies to that section over the period (``mean(tracked) / mean(recommended)``,
    the builder's assumption for ramp lanes; lane-5 coverage is bounded only to
    [0.33, 1], ``artifacts/i24_coverage_lane5.json``).

    Args:
        mapping: ``sim_x_of_data_x`` (``a``, ``b``), the validator's.
        network: Builder ``geometry`` with ``corridor_edges``, ``edge_lengths_m``,
            ``edge_lanes`` (the arm's network).
        sections_m: Section positions, data x [m].
        aux_tracked_veh_h: Tracked auxiliary-band flow per section (``None``: unknown).
        obs_tracked: Observed tracked hourly-equivalent flows ``[section][window]``.
        obs_rec: Observed recommended-coverage flows on the same bins.
    """
    a, b = float(mapping["a"]), float(mapping["b"])
    lengths = [float(v) for v in network["edge_lengths_m"]]
    starts = np.concatenate([[0.0], np.cumsum(lengths)])
    out = []
    for i, x in enumerate(sections_m):
        sx = a + b * float(x)
        k = int(np.searchsorted(starts, sx, side="right") - 1)
        if not 0 <= k < len(lengths):
            raise ValueError(f"section {x} m (sim x {sx:.1f}) is off the corridor")
        lanes = int(network["edge_lanes"][k])
        obs_2h = float(obs_rec[i].mean())
        c_eff = float(obs_tracked[i].mean()) / obs_2h if obs_2h > 0 else math.nan
        aux = aux_tracked_veh_h[i]
        row: dict[str, Any] = {
            "section_m": float(x),
            "sim_x_m": sx,
            "edge": str(network["corridor_edges"][k]),
            "edge_lanes": lanes,
            "into_edge_m": sx - float(starts[k]),
            "sim_counts_extra_lanes": lanes > OBSERVED_LANES,
            "obs_2h_lanes_1_4_veh_h": obs_2h,
            "table_coverage": c_eff,
            "aux_band_tracked_veh_h": aux,
        }
        if lanes > OBSERVED_LANES and aux is not None:
            row["obs_2h_all_lanes_lower_veh_h"] = obs_2h + float(aux)
            row["obs_2h_all_lanes_at_table_coverage_veh_h"] = obs_2h + float(aux) / c_eff
        out.append(row)
    return out


def like_for_like(
    lanes: Sequence[Mapping[str, Any]], sim_2h: Sequence[float]
) -> list[dict[str, Any]]:
    """An arm's 2-h GEH at the sections whose simulated count reads extra lanes, both bounds."""
    out = []
    for row, q in zip(lanes, sim_2h, strict=True):
        if "obs_2h_all_lanes_lower_veh_h" not in row:
            continue
        lo = row["obs_2h_all_lanes_lower_veh_h"]
        hi = row["obs_2h_all_lanes_at_table_coverage_veh_h"]
        out.append(
            {
                "section_m": row["section_m"],
                "sim_2h_veh_h": _r(q, 1),
                "geh_as_scored_lanes_1_4": _r(geh(q, row["obs_2h_lanes_1_4_veh_h"]), 3),
                "geh_all_lanes_lower": _r(geh(q, lo), 3),
                "sign_all_lanes_lower": int(np.sign(q - lo)),
                "geh_all_lanes_at_table_coverage": _r(geh(q, hi), 3),
                "sign_all_lanes_at_table_coverage": int(np.sign(q - hi)),
            }
        )
    return out


def demand_vs_target(
    coverage_rows: Sequence[Mapping[str, Any]],
    coverage_window_s: float,
    c_rec: Sequence[float],
    scale: float,
    window_s: float,
    t_lo: float,
    start_hhmm: str,
) -> dict[str, Any]:
    """Planned inflow over the row's target per window: ``s · c_rec / c_used``.

    The corrected arms divide the tracked mainline and on-ramp counts by the
    builder's ``coverage_used`` and the fitted arms multiply them by ``s``
    (``scripts/i24_build_replica.py``, ``corrected``; the scale fit); the row's
    target divides the same tracked counts by the recommended coverage. Their
    ratio is the demand planned per unit of target, before insertion losses.
    """
    rows = sorted(coverage_rows, key=lambda r: float(r["t_lo_s"]))
    per_hour = n_windows(S_PER_HOUR, window_s)
    ratios, table = [], []
    for i, cr in enumerate(c_rec):
        t = t_lo + i * window_s
        row = next(
            (r for r in rows if float(r["t_lo_s"]) <= t < float(r["t_lo_s"]) + coverage_window_s),
            None,
        )
        if row is None:
            raise ValueError(f"no coverage row contains t = {t}")
        used = float(row["coverage_used"])
        ratio = scale * float(cr) / used
        ratios.append(ratio)
        if not table or table[-1]["c_used"] != used or table[-1]["c_rec"] != float(cr):
            table.append(
                {
                    "from": clock(start_hhmm, i * window_s),
                    "c_used": used,
                    "c_rec": float(cr),
                    "ratio": ratio,
                }
            )
    hours = [
        {
            "hour": f"{clock(start_hhmm, k * window_s)}-{clock(start_hhmm, (k + per_hour) * window_s)}",
            "mean_ratio": float(np.mean(ratios[k : k + per_hour])),
        }
        for k in range(0, len(ratios) - per_hour + 1, per_hour)
    ]
    return {
        "scale": scale,
        "by_coverage_window": table,
        "by_hour": hours,
        "mean_ratio": float(np.mean(ratios)),
        "min_ratio": float(min(ratios)),
        "max_ratio": float(max(ratios)),
    }


# --- one battery ----------------------------------------------------------------


def diagnose_battery(
    battery: Mapping[str, Any],
    *,
    offset_found: bool | None,
    observed_committed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classes, station-hours, lags and context for one battery artifact."""
    o = battery["observed"]
    sim_d = battery["simulated"]
    window_s = float(o["window_s"])
    sections = [float(x) for x in o["sections_m"]]
    obs = np.asarray(o["hourly_flows_veh_h_recommended"], dtype=np.float64)
    sim = np.asarray(sim_d["hourly_flows_veh_h_mean"], dtype=np.float64)
    stored = np.asarray(
        battery["geh"]["vs_recommended_coverage_counts"]["values"], dtype=np.float64
    )
    if stored.size != obs.size:
        raise ValueError(f"the row has {stored.size} bins, the tables {obs.size}")
    stored = stored.reshape(obs.shape)  # the scorer ravels section-major
    cls = classify(sim, obs, window_s, failing=stored >= GEH_THRESHOLD)
    recomputed_diff = float(np.abs(cls["geh"].round(3) - stored).max())
    if recomputed_diff > 1e-3 + 1e-9:
        raise ValueError(f"recomputed GEH differs from the stored row by {recomputed_diff}")
    start = str(o["period"]).split("-")[0].strip()
    per_hour = n_windows(S_PER_HOUR, window_s)
    counts_rep = sim_d.get("counts_per_replicate")
    counts_arr = None if counts_rep is None else np.asarray(counts_rep, dtype=np.float64)

    bins = []
    for s, x in enumerate(sections):
        for w in range(obs.shape[1]):
            bins.append(
                {
                    "section_m": x,
                    "window": w,
                    "clock": clock(start, w * window_s),
                    "hour": w // per_hour,
                    "obs_veh_h": _r(obs[s, w], 1),
                    "sim_veh_h": _r(sim[s, w], 1),
                    "error_veh_h": _r(sim[s, w] - obs[s, w], 1),
                    "geh": _r(stored[s, w], 3),
                    "fails": bool(cls["failing"][s, w]),
                    "obs_centred_mean_veh_h": _r(cls["centred"][s, w], 1),
                    "noise_geh": _r(cls["noise_geh"][s, w], 3),
                    "level": bool(cls["level"][s, w]),
                    "timing_best_shift_min": int(cls["timing_shift"][s, w]) * window_s / 60.0,
                    "timing_best_geh": _r(cls["timing_geh"][s, w], 3),
                    "class": cls["labels"][s][w],
                }
            )
    t = tally(cls["labels"], sections, window_s)
    for row in t["by_hour"]:
        w0, w1 = row["windows"]
        row["clock"] = f"{clock(start, w0 * window_s)}-{clock(start, (w1 + 1) * window_s)}"
    same_obs = None
    if observed_committed is not None:
        same_obs = all(
            o.get(k) == observed_committed.get(k)
            for k in ("hourly_flows_veh_h_recommended", "sections_m", "window_s", "t_range_s")
        )
    realized = sim_d.get("demand_realized_fraction") or []
    return {
        "scenario": battery.get("scenario"),
        "config_hash": battery.get("config_hash"),
        "replicates": battery.get("replicates"),
        "demand_realized_mean": float(np.mean(realized)) if realized else None,
        "observed_equals_committed": same_obs,
        "observed_data_hash": o.get("data_hash"),
        "row": {
            "share": float(battery["geh"]["vs_recommended_coverage_counts"]["fraction_under_5"]),
            "n_bins": int(stored.size),
            "n_fail": int(cls["failing"].sum()),
            "recomputed_max_abs_diff": recomputed_diff,
        },
        "sections_2h": [
            {
                "section_m": x,
                "obs_veh_h": _r(lv["obs_veh_h"], 1),
                "sim_veh_h": _r(lv["sim_veh_h"], 1),
                "geh": _r(lv["geh"], 3),
                "sign": lv["sign"],
                "fails": bool(lv["fails"]),
            }
            for x, lv in zip(sections, cls["level_2h"], strict=True)
        ],
        "classes": t,
        "membership": membership(cls),
        "rule": apply_rule(t["counts"], offset_found),
        "station_hours": station_hours(obs, counts_arr, sim, sections, window_s, start),
        "cross_correlation": cross_correlation(sim, obs, sections, window_s),
        "recording_vs_own_mean": recording_vs_own_mean(cls, sections),
        "leave_one_out": leave_one_out(counts_arr, window_s, sections),
        "bins": bins,
        "_counts": None if counts_arr is None else counts_arr,
    }


# --- the whole run ------------------------------------------------------------------


def parse_battery(spec: str) -> tuple[str, str]:
    """``LABEL=PATH`` → (label, path)."""
    label, sep, path = spec.partition("=")
    if not sep or not label or not path:
        raise argparse.ArgumentTypeError(f"--battery wants LABEL=PATH, got {spec!r}")
    return label, path


def build(
    batteries: Sequence[tuple[str, str, str]],
    primary: str,
    *,
    observed: str | Path = OBSERVED,
    count_consistency: str | Path = COUNT_CONSISTENCY,
    coverage: str | Path = COVERAGE,
    replica_inputs: str | Path = REPLICA_INPUTS,
    family_inputs: str | Path | None = FAMILY_INPUTS,
    populations: Sequence[str | Path] = POPULATIONS,
    demand_scale: str | Path | None = DEMAND_SCALE,
) -> dict[str, Any]:
    """The artifact (module docstring)."""
    labels = [b[0] for b in batteries]
    if len(set(labels)) != len(labels):
        raise ValueError(f"battery labels repeat: {labels}")
    if primary not in labels:
        raise ValueError(f"primary {primary!r} is not among the batteries {labels}")
    inputs_rec: dict[str, Any] = {}

    def record(key: str, path: str | Path) -> Any:
        p = _resolve(path)
        inputs_rec[key] = {"path": _rel(p), "sha256": _sha256(p)}
        return json.loads(p.read_text())

    obs_c = record("observed", observed)
    cc = record("count_consistency", count_consistency)
    cov = record("coverage", coverage)
    rep_in = record("replica_inputs", replica_inputs)
    fam_in = record("family_inputs", family_inputs) if family_inputs is not None else None
    pops = []
    for i, p in enumerate(populations):
        pop = record(f"population_{i}", p)
        pops.append(
            {"path": inputs_rec[f"population_{i}"]["path"], "v0_ms": float(pop["mean"]["v0"])}
        )
    v0 = pops[0]["v0_ms"]

    window_s = float(obs_c["window_s"])
    sections = [float(x) for x in obs_c["sections_m"]]
    offset = insertion_offset(rep_in, v0, sections, window_s, family_inputs=fam_in)
    offset["populations"] = pops
    offset["v0_source"] = pops[0]["path"]

    obs_tab = np.asarray(obs_c["hourly_flows_veh_h_recommended"], dtype=np.float64)
    obs_trk = np.asarray(obs_c["hourly_flows_veh_h_tracked"], dtype=np.float64)
    cc_sections = {float(s["x_m"]): s for s in cc["sections"]}
    aux = [
        None if cc_sections.get(x) is None else cc_sections[x].get("aux_band_tracked_veh_h")
        for x in sections
    ]
    network = (fam_in if fam_in is not None else rep_in)["geometry"]
    lanes = section_lanes(
        rep_in["geometry"]["sim_x_of_data_x"], network, sections, aux, obs_trk, obs_tab
    )

    arms: dict[str, Any] = {}
    bat_paths = []
    for label, path, role in batteries:
        p = _resolve(path)
        sha = _sha256(p)
        bat_paths.append({"label": label, "path": _rel(p), "sha256": sha, "role": role})
        battery = json.loads(p.read_text())
        arm = diagnose_battery(battery, offset_found=offset["found"], observed_committed=obs_c)
        sim_2h = np.asarray(battery["simulated"]["hourly_flows_veh_h_mean"], dtype=np.float64)
        arm["lane_set_check"] = like_for_like(lanes, sim_2h.mean(axis=1).tolist())
        arms[label] = {"role": role, "path": _rel(p), "sha256": sha, **arm}
    inputs_rec["batteries"] = bat_paths

    # arms whose replicates reproduce another's exactly (same hash, same counts)
    for label, arm in arms.items():
        same = [
            other
            for other, o in arms.items()
            if other != label
            and o["config_hash"] == arm["config_hash"]
            and arm["_counts"] is not None
            and o["_counts"] is not None
            and np.array_equal(arm["_counts"], o["_counts"])
        ]
        arm["reproduces"] = same
    for arm in arms.values():
        arm.pop("_counts")

    # observed-side checks against the count-consistency and coverage artifacts
    targets = []
    for i, x in enumerate(sections):
        s = cc_sections.get(x)
        pooled = None if s is None else s["flow_veh_h"]["pooled"]
        targets.append(
            {
                "section_m": x,
                "obs_2h_veh_h": _r(obs_tab[i].mean(), 1),
                "count_consistency_pooled_veh_h": None if pooled is None else _r(pooled["mean"], 1),
                "count_consistency_ci_block": None
                if pooled is None
                else [_r(v, 1) for v in pooled["ci_block"]],
            }
        )
    cov_rows = sorted(cov["windows"], key=lambda w: float(w["t_lo_s"]))
    cov_win = float(cov["parameters"]["window_s"])
    t_lo = float(obs_c["t_range_s"][0])
    cov_expected = []
    for i in range(len(obs_c["coverage_recommended_per_window"])):
        t = t_lo + i * window_s
        row = next(w for w in cov_rows if float(w["t_lo_s"]) <= t < float(w["t_lo_s"]) + cov_win)
        cov_expected.append(round(float(row["pooled"]["recommended_filled"]), 4))
    model = cc["verdict"]["model"]
    r4 = None
    for label, arm in arms.items():
        if arm["config_hash"] == model["config_hash"]:
            mine = {lv["section_m"]: lv["geh"] for lv in arm["sections_2h"]}
            r4 = {
                "arm": label,
                "count_consistency_model": model["artifact"],
                "max_abs_geh_diff": max(
                    abs(mine[float(m["x_m"])] - float(m["geh_2h"]))
                    for m in cc["verdict"]["model_vs_targets"]
                ),
            }
            break
    checks = {
        "every_battery_observed_equals_committed": all(
            a["observed_equals_committed"] for a in arms.values()
        ),
        "coverage_equals_coverage_artifact_4dp": cov_expected
        == [round(float(c), 4) for c in obs_c["coverage_recommended_per_window"]],
        "targets_2h": targets,
        "r4_measure_reproduced": r4,
        "count_consistency_outcome": cc["verdict"]["outcome"],
    }
    noise = counting_noise(
        np.asarray(obs_c["counts_tracked"], dtype=np.float64),
        obs_c["coverage_recommended_per_window"],
        window_s,
    )
    prim = arms[primary]
    inputs_vs_targets: dict[str, Any] = {"formed": False, "reason": "no family inputs or scale"}
    if fam_in is not None and demand_scale is not None:
        ds = record("demand_scale", demand_scale)
        start = str(obs_c["period"]).split("-")[0].strip()
        count_rows = [
            r
            for r in prim["station_hours"]["table"]
            if r["section_m"] == float(rep_in["mainline"]["count_x_m"])
        ]
        inputs_vs_targets = {
            "formed": True,
            "applies_to": primary,
            "scale_source": inputs_rec["demand_scale"]["path"],
            "scale_fit_config_hash": ds["best"].get("config_hash"),
            "coverage_used_source": fam_in["coverage"].get("source"),
            "c_rec_source": obs_c.get("coverage_recommended_source"),
            **demand_vs_target(
                fam_in["coverage"]["rows"],
                float(fam_in["coverage"]["window_s"]),
                obs_c["coverage_recommended_per_window"],
                float(ds["best"]["scale"]),
                window_s,
                t_lo,
                start,
            ),
            "count_section_sim_over_obs_by_hour": [
                {"hour": r["hour"], "ratio": r["sim_mean_veh_h"] / r["obs_veh_h"]}
                for r in count_rows
            ],
            "realised_demand_mean": prim["demand_realized_mean"],
        }
    lane_findings = {
        "observed_lanes": "1-4 (scripts/i24_data.MAINLINE_LANES)",
        "simulated_lanes": "every lane of the corridor edge the section lies on (no lane filter)",
        "network_source": inputs_rec["family_inputs" if fam_in is not None else "replica_inputs"][
            "path"
        ],
        "sections": lanes,
    }
    verdict = {
        "arm": primary,
        "class_counts": prim["classes"]["counts"],
        "n_fail": prim["classes"]["n_fail"],
        "row_share": prim["row"]["share"],
        "station_hour_share_pooled": prim["station_hours"]["pooled_over_replicates"].get("share"),
        "station_hour_share_replicate_mean": prim["station_hours"]["replicate_mean"]["share"],
        "insertion_offset_found": offset["found"],
        "insertion_offset_s": offset["offset_s"],
        **prim["rule"],
    }
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spec": SPEC,
        "question": (
            "after B2 the peak sections pass on 2-h flows (GEH 3.86 / 4.63) yet the battery's "
            "link-flow row is 30.6 % against 85 %: which bins fail, and why"
        ),
        "code": _code(),
        "inputs": inputs_rec,
        "reads": "committed JSON only; nothing under data/, no trajectories, no simulation",
        "definitions": {
            "bins": (
                "6 sections x 24 five-min windows of 06:30-08:30 CST, crossings x12 to hourly-"
                "equivalent flows; replicate-mean simulated against the tracked crossings over the "
                "recommended coverage; a bin fails at the row's stored GEH >= 5"
            ),
            "order": list(CLASSES),
            "recording_noise": (
                "the observed bin at GEH >= 5 from its own centred 15-min mean (the window and its "
                "two neighbours; the first and last window padded with themselves)"
            ),
            "level": (
                "the section's 2-h flow (mean of its 24 hourly-equivalent flows, replicate mean vs "
                "observed; R4's measure) at GEH >= 5, the bin's error of the same sign"
            ),
            "timing": (
                "a simulated bin of the same section within +/-15 min (3 windows) at GEH < 5 from "
                "the observed bin"
            ),
            "shape": "every other failing bin",
            "station_hour": (
                "hours anchored at the study period's start (window 0, 06:30): windows 0-11 and "
                "12-23; a station-hour's flow is its twelve 5-min counts summed (the mean of their "
                "hourly-equivalent flows); observed = mean of the row's recommended table over the "
                "hour; C1 pools every replicate's station-hours; the replicate-mean form is beside it"
            ),
            "cross_correlation": (
                "Pearson r of sim[w + L] with obs[w] on the overlap, |L| <= 3 windows; positive L = "
                "simulated later; parabolic sub-window estimate when the peak is interior"
            ),
        },
        "thresholds": {
            "geh": GEH_THRESHOLD,
            "geh_source": "validation.criteria profile fhwa_default (FHWA TAT Vol. III 2004)",
            "timing_tolerance_s": TIMING_TOLERANCE_S,
            "timing_source": "docs/FRISCO_PROTOCOL.md §5.2 (within 15 minutes)",
            "noise_mean_s": NOISE_MEAN_S,
            "noise_source": "docs/I24_VALIDATION.md §0.5(a) (the recording against its 15-min average)",
            "level_source": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3 R4 (2-h flow GEH)",
        },
        "primary": primary,
        "verdict": verdict,
        "insertion_offset": offset,
        "lane_sets": lane_findings,
        "inputs_vs_targets": inputs_vs_targets,
        "counting_noise": noise,
        "checks": checks,
        "arms": arms,
        "limits": [
            "The classes overlap and the order decides: a bin that is recording noise may also be a "
            "level, timing or shape miss (membership gives the overlap).",
            "One recorded day (30 Nov 2022), one direction; the observed side is one realisation, "
            "the simulated side a 20-seed mean.",
            "The recording-noise test reads the recording's variability below 15 min; it does not "
            "separate tracking noise from real 5-min traffic structure, which a stochastic model's "
            "ensemble mean cannot reproduce in phase either way.",
            "The insertion offset is a free-flow time at the mean v0; congestion, edge limits and "
            "insertion speed lengthen it, and the on-ramp insertion offsets are not computed (the "
            "ramp edges' lengths are in the network, which is not read).",
            "The station-hour share is reported, not a criterion; no amendment is written here.",
            "The lane-set check and the planned-demand ratio are input checks outside the classes "
            "and the rule; the like-for-like flows bound the recording's auxiliary lane by its "
            "tracked count (coverage 1) and by the row's mainline coverage, and lane-5 coverage "
            "is not measured.",
        ],
    }


def _print(result: Mapping[str, Any]) -> None:
    for label, arm in result["arms"].items():
        c = arm["classes"]["counts"]
        sh = arm["station_hours"]
        pooled = sh["pooled_over_replicates"].get("share")
        print(
            f"[{label}] row {arm['row']['share']:.1%} ({arm['row']['n_fail']} of "
            f"{arm['row']['n_bins']} fail): noise {c['recording_noise']}, level {c['level']}, "
            f"timing {c['timing']}, shape {c['shape']}; station-hours "
            f"{'n/a' if pooled is None else f'{pooled:.1%}'} pooled, "
            f"{sh['replicate_mean']['share']:.1%} replicate mean"
        )
    off = result["insertion_offset"]
    print(
        f"insertion offset: stamped without shift = {off['stamping']['stamped_without_shift']}, "
        f"entry to {off['count_section_m']:g} m = {off['distance_entry_to_count_section_m']:.1f} m, "
        f"{off['offset_s']:.1f} s at v0 = {off['v0_ms']:.2f} m/s ({off['offset_windows']:.2f} window)"
    )
    for row in result["lane_sets"]["sections"]:
        if row["sim_counts_extra_lanes"]:
            print(
                f"lane sets differ at {row['section_m']:g} m: simulated edge {row['edge']} has "
                f"{row['edge_lanes']} lanes, observed counts lanes 1-4 (recorded aux band "
                f"{row['aux_band_tracked_veh_h']} tracked veh/h)"
            )
    iv = result["inputs_vs_targets"]
    if iv.get("formed"):
        hours = ", ".join(f"{h['hour']} {h['mean_ratio']:.3f}" for h in iv["by_hour"])
        print(f"planned demand / row target on {iv['applies_to']} (s = {iv['scale']:g}): {hours}")
    v = result["verdict"]
    print(f"C7 rule on {v['arm']}: {v['text']} ({v['report_only']})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/i24_geh_diagnosis.py",
        description="C7: classify the failing GEH bins of the I-24 batteries (JSON only, $0).",
    )
    parser.add_argument(
        "--battery",
        action="append",
        type=parse_battery,
        metavar="LABEL=PATH",
        help="a battery artifact (repeatable; replaces the default list)",
    )
    parser.add_argument("--primary", default=None, help="the battery the verdict reads")
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--observed", default=OBSERVED)
    parser.add_argument("--count-consistency", default=COUNT_CONSISTENCY)
    parser.add_argument("--coverage", default=COVERAGE)
    parser.add_argument("--replica-inputs", default=REPLICA_INPUTS)
    parser.add_argument("--family-inputs", default=FAMILY_INPUTS)
    parser.add_argument("--population", action="append", default=None)
    parser.add_argument("--demand-scale", default=DEMAND_SCALE)
    args = parser.parse_args(argv)
    if args.battery:
        batteries = [(lab, path, "given on the command line") for lab, path in args.battery]
        primary = args.primary or batteries[0][0]
    else:
        batteries = list(DEFAULT_BATTERIES)
        primary = args.primary or DEFAULT_PRIMARY
    result = build(
        batteries,
        primary,
        observed=args.observed,
        count_consistency=args.count_consistency,
        coverage=args.coverage,
        replica_inputs=args.replica_inputs,
        family_inputs=args.family_inputs,
        populations=args.population or POPULATIONS,
        demand_scale=args.demand_scale,
    )
    out = _resolve(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_json_safe(result), indent=1, allow_nan=False) + "\n")
    _print(result)
    print(f"wrote {_rel(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
