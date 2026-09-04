"""Observed lane observables of the I-24 MOTION westbound day (ROADMAP §1).

The observed side of the lane-change calibration
(``scripts/i24_fit_lanechange.py``): lane-use shares, lane-change rates and
lane-change locations of the tracked mainline fragments (lanes 1–4) on the
measured span (data x ∈ [0, 5492) m, ``artifacts/i24_replica_inputs.json``)
over the study period 06:30–08:30 CST, by ``calibration.lanechange``
exactly as the simulated side is computed. Two partitions of the span are
written:

* **sections** — ten equal segments (549 m), the partition of the
  segment-speed criterion in ``scripts/i24_validate.py``; the fit objective
  is scored on this one;
* **ramp_zones** — the span cut at the ramp landmarks projected onto the
  data axis (``geometry.ramp_landmarks_chain_m`` of the inputs artifact):
  upstream of Old Hickory, the Old Hickory acceleration lane, the stretch to
  the Hickory Hollow diverge, the diverge, the stretch to the Hickory Hollow
  on-ramp, the Hickory Hollow-Bell Road weaving section, and the Bell Road
  diverge — the "lane changes relative to the ramps" table.

The study period is processed in 15-minute chunks, each loaded with an 8 s
pad on both sides so that run detection near the chunk edges sees the same
samples the whole record would (the counts are exact by construction; see
``lane_observables``), and the chunk records are written too, so any window
that is a union of chunks — the fitted first hour, the held-out second hour,
a smoke window — is a sum of them. Memory stays around 300 MB.

Coverage (docs/I24_DATA.md §4): counts are lower bounds; the shares and
rates are ratios and coverage-robust to first order.

Run: ``uv run --no-sync python scripts/i24_lanechange_observed.py``
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S
from i24_data import REPO_ROOT, SAMPLE_DT_S, clock, data_hash, load_mainline

from calibration.lanechange import (
    DEFAULT_HIST_DX_M,
    DEFAULT_LANES,
    DEFAULT_MAX_GAP_FACTOR,
    DEFAULT_MIN_DWELL_S,
    LaneObservables,
    lane_observables,
)
from flowstate_core.artifacts import LaneObservablesRecord

INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
OUT = REPO_ROOT / "artifacts" / "i24_lanechange_observed.json"

N_SECTIONS = 10
"""Equal sections of the measured span (the segment-speed partition)."""
LANES: tuple[int, ...] = DEFAULT_LANES
CHUNK_S = 900.0
"""Processing chunk [s]; fit/holdout windows are unions of chunks."""
FIT_WINDOW_S: tuple[float, float] = (0.0, 3600.0)
"""Study-relative window the lane-change parameters are fitted on (06:30–07:30)."""
HOLDOUT_WINDOW_S: tuple[float, float] = (3600.0, 7200.0)
"""Study-relative window held out (07:30–08:30), as for the demand level."""
MIN_DWELL_S = DEFAULT_MIN_DWELL_S
MAX_GAP_S = DEFAULT_MAX_GAP_FACTOR * SAMPLE_DT_S
HIST_DX_M = DEFAULT_HIST_DX_M
PAD_S = 2.0 * (MIN_DWELL_S + MAX_GAP_S) + 5.0
"""Load pad on each side of a chunk so run detection at its edges is exact."""
X_MARGIN_M = 100.0

#: Ramp-relative partition: (zone name, landmark that opens it). The first
#: zone opens at the span start; each zone runs to the next landmark.
RAMP_ZONES: tuple[tuple[str, str | None], ...] = (
    ("upstream_of_OH", None),
    ("OH_acceleration_lane", "wb_OH_on_start"),
    ("OH_to_HH_diverge", "wb_OH_on_end"),
    ("HH_diverge", "wb_HH_off_start"),
    ("HH_off_to_HH_on", "wb_HH_off_end"),
    ("HH_on_BR_off_weave", "wb_HH_on_start"),
    ("BR_diverge", "wb_BR_off_start"),
)


def inputs() -> dict[str, Any]:
    return json.loads(INPUTS.read_text())


def span() -> tuple[float, float]:
    lo, hi = inputs()["geometry"]["measured_span_data_x_m"]
    return float(lo), float(hi)


def section_edges() -> list[float]:
    """Edges of the ten equal sections on the measured span [data m]."""
    lo, hi = span()
    return [float(v) for v in np.linspace(lo, hi, N_SECTIONS + 1)]


def ramp_landmarks_data_x() -> dict[str, float]:
    """Ramp landmarks projected onto the data axis [m] (chain → data x)."""
    geo = inputs()["geometry"]
    x0 = float(geo["data_x0_chain_m"])
    scale = float(geo["chain_m_per_data_m"])
    return {k: (float(v) - x0) / scale for k, v in geo["ramp_landmarks_chain_m"].items()}


def zone_edges() -> tuple[list[str], list[float]]:
    """Names and edges [data m] of the ramp-relative partition of the span."""
    lo, hi = span()
    marks = ramp_landmarks_data_x()
    names = [name for name, _ in RAMP_ZONES]
    edges = [lo] + [marks[key] for _, key in RAMP_ZONES if key is not None] + [hi]
    if any(b <= a for a, b in pairwise(edges)):
        raise ValueError(f"ramp zone edges are not increasing: {edges}")
    return names, edges


def chunk_windows() -> list[tuple[float, float]]:
    """Study-relative chunk windows covering [0, T_STUDY_HI - T_STUDY_LO)."""
    total = T_STUDY_HI_S - T_STUDY_LO_S
    n = round(total / CHUNK_S)
    return [(k * CHUNK_S, min((k + 1) * CHUNK_S, total)) for k in range(n)]


def observed_chunk(
    window: tuple[float, float], edges: list[float], zones: list[float]
) -> tuple[LaneObservables, LaneObservables, set[str]]:
    """Both partitions' observables on one study-relative window, plus fragment ids."""
    lo, hi = window
    span_lo, span_hi = span()
    df = load_mainline(
        t_range_s=(T_STUDY_LO_S + lo - PAD_S, T_STUDY_LO_S + hi + PAD_S),
        x_range_m=(span_lo - X_MARGIN_M, span_hi + X_MARGIN_M),
        columns=["t", "veh_id", "x", "lane", "v"],
    )
    df["t"] = df["t"] - T_STUDY_LO_S
    kw: dict[str, Any] = {
        "lanes": LANES,
        "dt_s": SAMPLE_DT_S,
        "max_gap_s": MAX_GAP_S,
        "min_dwell_s": MIN_DWELL_S,
        "window_s": window,
        "hist_dx_m": HIST_DX_M,
    }
    sec = lane_observables(df, edges, **kw)
    zon = lane_observables(df, zones, **kw)
    inside = df[(df["t"] >= lo) & (df["t"] < hi) & (df["x"] >= span_lo) & (df["x"] < span_hi)]
    return sec, zon, set(inside["veh_id"].unique().tolist())


def dwell_sensitivity(
    window: tuple[float, float],
    edges: list[float],
    guards: tuple[float, ...] = (0.0, 1.0, 3.0, 5.0),
) -> list[tuple[float, float]]:
    """Span-wide changes per veh-km on one chunk for several flicker guards."""
    lo, hi = window
    span_lo, span_hi = span()
    df = load_mainline(
        t_range_s=(T_STUDY_LO_S + lo - PAD_S, T_STUDY_LO_S + hi + PAD_S),
        x_range_m=(span_lo - X_MARGIN_M, span_hi + X_MARGIN_M),
        columns=["t", "veh_id", "x", "lane", "v"],
    )
    df["t"] = df["t"] - T_STUDY_LO_S
    out = []
    for guard in guards:
        o = lane_observables(
            df,
            edges,
            lanes=LANES,
            dt_s=SAMPLE_DT_S,
            max_gap_s=MAX_GAP_S,
            min_dwell_s=guard,
            window_s=window,
        )
        out.append((guard, float(o.n_changes.sum() / o.veh_km.sum())))
    return out


def sum_windows(parts: list[LaneObservables], window: tuple[float, float]) -> LaneObservables:
    """Sum the chunk observables lying inside ``window`` (must tile it)."""
    inside = [p for p in parts if p.window_s is not None and window[0] <= p.window_s[0] < window[1]]
    if not inside:
        raise ValueError(f"no chunks inside {window}")
    covered = sum(p.window_s[1] - p.window_s[0] for p in inside if p.window_s is not None)
    if abs(covered - (window[1] - window[0])) > 1e-6:
        raise ValueError(f"chunks cover {covered} s of the {window} window")
    total = inside[0]
    for p in inside[1:]:
        total = total + p
    return total


def print_table(title: str, rec: LaneObservablesRecord, names: list[str] | None = None) -> None:
    print(title)
    print(
        "  section            x [m]         share L1  L2    L3    L4    veh-km   changes  LC/veh-km"
    )
    for k in range(len(rec.x_edges_m) - 1):
        label = names[k] if names is not None else f"{k}"
        shares = rec.lane_share[k]
        share_txt = "  ".join("  -- " if s is None else f"{s:.3f}" for s in shares)
        rate = rec.changes_per_veh_km[k]
        rate_txt = "    --" if rate is None else f"{rate:6.3f}"
        print(
            f"  {label:18s} {rec.x_edges_m[k]:6.0f}-{rec.x_edges_m[k + 1]:5.0f}   {share_txt}"
            f"  {rec.veh_km[k]:8.1f}  {rec.n_changes[k]:7d}  {rate_txt}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    t0 = time.perf_counter()
    dh = data_hash()
    edges = section_edges()
    names, zones = zone_edges()
    windows = chunk_windows()
    sec_parts: list[LaneObservables] = []
    zone_parts: list[LaneObservables] = []
    fragments: set[str] = set()
    for w in windows:
        tc = time.perf_counter()
        sec, zon, ids = observed_chunk(w, edges, zones)
        sec_parts.append(sec)
        zone_parts.append(zon)
        fragments |= ids
        print(
            f"chunk {clock(T_STUDY_LO_S + w[0])}-{clock(T_STUDY_LO_S + w[1])}: "
            f"{sec.n_samples} samples, {int(sec.n_changes.sum())} changes, "
            f"{sec.veh_km.sum():.0f} veh-km, {len(ids)} fragments ({time.perf_counter() - tc:.0f} s)",
            flush=True,
        )
    sensitivity = dwell_sensitivity(windows[0], edges)
    print(
        "flicker-guard sensitivity (first chunk, changes per veh-km): "
        + ", ".join(f"{d:g} s -> {r:.3f}" for d, r in sensitivity)
    )
    study = (0.0, T_STUDY_HI_S - T_STUDY_LO_S)
    sec_by_window = {
        "study": sum_windows(sec_parts, study),
        "fit": sum_windows(sec_parts, FIT_WINDOW_S),
        "holdout": sum_windows(sec_parts, HOLDOUT_WINDOW_S),
    }
    zone_by_window = {
        "study": sum_windows(zone_parts, study),
        "fit": sum_windows(zone_parts, FIT_WINDOW_S),
        "holdout": sum_windows(zone_parts, HOLDOUT_WINDOW_S),
    }
    out: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "I-24 MOTION INCEPTION westbound, 30 Nov 2022, mainline lanes 1-4 fragments",
        "data_hash": dh,
        "period": f"{clock(T_STUDY_LO_S)}-{clock(T_STUDY_HI_S)} CST",
        "t_range_s": [T_STUDY_LO_S, T_STUDY_HI_S],
        "time_origin": "study-relative seconds (data t - 1800); sim t = study t + 600",
        "span_data_x_m": list(span()),
        "lanes": list(LANES),
        "parameters": {
            "dt_s": SAMPLE_DT_S,
            "max_gap_s": MAX_GAP_S,
            "min_dwell_s": MIN_DWELL_S,
            "hist_dx_m": HIST_DX_M,
            "chunk_s": CHUNK_S,
            "pad_s": PAD_S,
        },
        "fit_window_s": list(FIT_WINDOW_S),
        "holdout_window_s": list(HOLDOUT_WINDOW_S),
        "n_fragments": len(fragments),
        "sections": {
            "x_edges_m": edges,
            **{k: v.to_record().model_dump(mode="json") for k, v in sec_by_window.items()},
            "chunks": [p.to_record().model_dump(mode="json") for p in sec_parts],
        },
        "ramp_zones": {
            "names": names,
            "x_edges_m": zones,
            **{k: v.to_record().model_dump(mode="json") for k, v in zone_by_window.items()},
            "chunks": [p.to_record().model_dump(mode="json") for p in zone_parts],
        },
        "ramp_landmarks_data_x_m": {k: round(v, 1) for k, v in ramp_landmarks_data_x().items()},
        "min_dwell_sensitivity_first_chunk": [
            {"min_dwell_s": d, "changes_per_veh_km": round(r, 4)} for d, r in sensitivity
        ],
        "wall_s": round(time.perf_counter() - t0, 1),
        "notes": [
            "Definitions: calibration.lanechange.lane_observables (vehicle-time shares per lane; held mainline-to-mainline lane changes per vehicle-km; change locations at the midpoint of the pair they occur on).",
            "Fragments are used as delivered (docs/I24_DATA.md §2); a lane change during an untracked gap is lost together with the vehicle-km around it, so rates are ratios of lower bounds.",
            "Rows in lanes 0 and >= 5 (median shoulder, auxiliary and ramp lanes) are excluded before run detection; a mainline vehicle entering the auxiliary lane appears as a sampling gap on both the observed and the simulated side.",
            "Flicker guard: a stay shorter than 1.0 s that returns to the previous lane is a lane-line excursion of the int8 band index, not a change; min_dwell_sensitivity_first_chunk shows how the span-wide rate moves with the guard (returns are a minority of transitions).",
            "Sections: the ten equal segments of scripts/i24_validate.py; ramp_zones: the span cut at the projected ramp landmarks (geometry.ramp_landmarks_chain_m).",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, allow_nan=False))
    for key, title in (
        ("study", "study 06:30-08:30"),
        ("fit", "fit 06:30-07:30"),
        ("holdout", "holdout 07:30-08:30"),
    ):
        print_table(f"[sections] {title}", sec_by_window[key].to_record())
    print_table("[ramp zones] study 06:30-08:30", zone_by_window["study"].to_record(), names)
    print(f"{len(fragments)} fragments; wrote {args.out} in {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
