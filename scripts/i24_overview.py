"""ROADMAP §1.1 follow-up — what the I-24 westbound day looks like.

Reads the processed Parquet (``scripts/i24_extract.py``) and produces the
facts the flagship replica is designed from:

* the 4-hour space-time speed field of the westbound mainline (60 s × 100 m
  bins, ``validation.fields.speed_field`` on the 5 Hz samples) — where and
  when congestion forms, so the study period is chosen from data;
* fragment-crossing counts at cross-sections every 200 m per 5-min window —
  the tracking-coverage picture (a mainline stretch without ramps must show
  the same count at every section up to travel-time lag, so dips are
  coverage losses, not traffic);
* per-15-min fragment statistics (count, share lasting ≥ 30 s, mean speed).

Outputs: ``artifacts/i24_wb_overview.json`` (every number quoted in
docs/I24_DATA.md) and ``docs/figures/i24_wb_overview.png``.

Run: ``uv run --no-sync python scripts/i24_overview.py``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, PowerNorm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    REPO_ROOT,
    TESTBED_LENGTH_M,
    clock,
    load_mainline,
    load_vehicles,
    meta,
)

from flowstate_core.units import ms_to_kmh
from validation.fields import speed_field

FIG_DIR = REPO_ROOT / "docs" / "figures"
OUT_JSON = REPO_ROOT / "artifacts" / "i24_wb_overview.json"

DT_FIELD_S = 60.0
DX_FIELD_M = 100.0
SECTION_STEP_M = 200.0
WINDOW_S = 300.0
STAT_WINDOW_S = 900.0

INK, INK2, GRID, SPINE, SURFACE = "#0b0b0b", "#52514e", "#e8e7e2", "#b5b4ae", "#ffffff"
SEQ_BLUES = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.5,
            "axes.edgecolor": SPINE,
            "axes.linewidth": 0.9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "font.size": 9,
        }
    )


def crossing_counts(sections: np.ndarray, n_windows: int) -> np.ndarray:
    """Fragment crossings per (section, 5-min window), lane by lane.

    A crossing is a consecutive sample pair of one fragment that brackets the
    section (``x_prev < s <= x``); its time is the bracketing pair's later
    sample (0.2 s resolution, irrelevant at 5-min windows).
    """
    counts = np.zeros((len(sections), n_windows), dtype=np.int64)
    for lane in range(1, 5):
        df = load_mainline(columns=["t", "veh_id", "x", "lane"])
        df = df[df["lane"] == lane].sort_values(["veh_id", "t"], kind="stable")
        same = df["veh_id"].to_numpy()[1:] == df["veh_id"].to_numpy()[:-1]
        x = df["x"].to_numpy()
        t = df["t"].to_numpy()
        x_prev, x_cur, t_cur = x[:-1][same], x[1:][same], t[1:][same]
        for i, s in enumerate(sections):
            hit = (x_prev < s) & (x_cur >= s)
            w = (t_cur[hit] // WINDOW_S).astype(np.int64)
            w = w[(w >= 0) & (w < n_windows)]
            counts[i] += np.bincount(w, minlength=n_windows)
        del df
    return counts


def main() -> None:
    t0 = time.perf_counter()
    m = meta()
    veh = load_vehicles()
    n_windows = int(np.ceil(4 * 3600 / WINDOW_S))

    # --- speed field over the mainline (all lanes pooled) ------------------
    df = load_mainline(x_range_m=(0.0, TESTBED_LENGTH_M), columns=["t", "x", "v"])
    field = speed_field(df, dt_bin=DT_FIELD_S, dx_bin=DX_FIELD_M)
    kmh = ms_to_kmh(1.0) * field.mean_speed
    n_rows_mainline = len(df)
    del df

    # per-15-min statistics
    stats = []
    n_stat = int(np.ceil(4 * 3600 / STAT_WINDOW_S))
    for w in range(n_stat):
        lo, hi = w * STAT_WINDOW_S, (w + 1) * STAT_WINDOW_S
        sel = veh[(veh["first_t"] >= lo) & (veh["first_t"] < hi)]
        rows = (field.t_edges[:-1] >= lo) & (field.t_edges[:-1] < hi)
        block = kmh[rows]
        stats.append(
            {
                "window": f"{clock(lo)}-{clock(hi)}",
                "t_lo_s": lo,
                "n_fragments": len(sel),
                "frac_ge_30s": float((sel["duration_s"] >= 30.0).mean()) if len(sel) else None,
                "median_span_m": float((sel["x_end"] - sel["x_start"]).abs().median())
                if len(sel)
                else None,
                "mean_speed_kmh": float(np.nanmean(block)) if np.isfinite(block).any() else None,
                "min_bin_speed_kmh": float(np.nanmin(block)) if np.isfinite(block).any() else None,
                "frac_bins_below_40": float(np.nanmean(block < 40.0))
                if np.isfinite(block).any()
                else None,
            }
        )

    # --- crossing counts (coverage picture) -------------------------------
    sections = np.arange(SECTION_STEP_M, TESTBED_LENGTH_M, SECTION_STEP_M)
    counts = crossing_counts(sections, n_windows)
    hourly = counts * 3600.0 / WINDOW_S

    # Per window: the spread of counts across ramp-free interior sections is
    # a coverage diagnostic (true mainline flow is conserved between ramps).
    per_window = []
    for w in range(n_windows):
        col = counts[:, w]
        per_window.append(
            {
                "t_lo_s": w * WINDOW_S,
                "window": clock(w * WINDOW_S),
                "count_min": int(col.min()),
                "count_median": float(np.median(col)),
                "count_max": int(col.max()),
                "section_of_max_m": float(sections[int(col.argmax())]),
            }
        )

    out = {
        "source": m["source"],
        "data_hash": m["data_hash"],
        "t_origin_unix": m["t_origin_unix"],
        "direction": m["direction"],
        "n_fragments": len(veh),
        "n_fragments_ge_30s": int((veh["duration_s"] >= 30.0).sum()),
        "fragment_duration_s": {
            "median": float(veh["duration_s"].median()),
            "p90": float(veh["duration_s"].quantile(0.9)),
            "max": float(veh["duration_s"].max()),
        },
        "fragment_span_m": {
            "median": float((veh["x_end"] - veh["x_start"]).abs().median()),
            "p90": float((veh["x_end"] - veh["x_start"]).abs().quantile(0.9)),
        },
        "n_rows_mainline_testbed": n_rows_mainline,
        "field": {
            "dt_s": DT_FIELD_S,
            "dx_m": DX_FIELD_M,
            "t_edges_s": [float(v) for v in field.t_edges],
            "x_edges_m": [float(v) for v in field.x_edges],
            "mean_speed_kmh": [
                [None if not np.isfinite(v) else round(float(v), 2) for v in row] for row in kmh
            ],
        },
        "stats_15min": stats,
        "sections_m": [float(s) for s in sections],
        "window_s": WINDOW_S,
        "crossings": counts.tolist(),
        "hourly_flow_veh_h": np.round(hourly, 1).tolist(),
        "crossings_per_window": per_window,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1))

    # --- figure -------------------------------------------------------------
    _style()
    cmap = LinearSegmentedColormap.from_list("blues", SEQ_BLUES)
    cmap.set_bad(SURFACE)
    norm = PowerNorm(gamma=0.6, vmin=0.0, vmax=120.0)
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [3, 2]}
    )
    im = ax1.imshow(
        kmh.T,
        origin="lower",
        aspect="auto",
        cmap=cmap,
        norm=norm,
        extent=(
            field.t_edges[0] / 3600.0 + 6.0,
            field.t_edges[-1] / 3600.0 + 6.0,
            field.x_edges[0] / 1000.0,
            field.x_edges[-1] / 1000.0,
        ),
    )
    ax1.grid(False)
    ax1.set_ylabel("distance along westbound travel from MM 62.7 [km]")
    ax1.set_title(
        "I-24 westbound, 30 Nov 2022 — mean speed of tracked fragments, 60 s × 100 m bins",
        fontsize=9.5,
    )
    cb = fig.colorbar(im, ax=ax1, pad=0.01, fraction=0.03)
    cb.set_label("speed [km/h]")
    cb.set_ticks([0, 20, 40, 60, 80, 100, 120])

    tw = (np.arange(n_windows) * WINDOW_S + WINDOW_S / 2) / 3600.0 + 6.0
    ax2.fill_between(
        tw, hourly.min(axis=0), hourly.max(axis=0), color="#cde2fb", label="range over sections"
    )
    ax2.plot(tw, np.median(hourly, axis=0), color="#2a78d6", lw=1.6, label="median section")
    ax2.set_ylabel("fragment crossings [veh/h, 4 lanes]")
    ax2.set_xlabel("time of day [h, CST]")
    ax2.set_title(
        "fragment crossings per 5 min at sections every 200 m — spread = tracking coverage",
        fontsize=9.5,
    )
    ax2.legend(loc="upper right")
    ax2.set_xlim(6.0, 10.0)
    fig.tight_layout()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / "i24_wb_overview.png", dpi=150)

    print(
        f"{out['n_fragments']} fragments, {out['n_fragments_ge_30s']} >= 30 s; rows {n_rows_mainline}"
    )
    for s in stats:
        print(
            f"  {s['window']}: {s['n_fragments']:6d} frags, {100 * (s['frac_ge_30s'] or 0):4.1f}% >=30 s, "
            f"mean {s['mean_speed_kmh'] or float('nan'):5.1f} km/h, "
            f"{100 * (s['frac_bins_below_40'] or 0):4.1f}% bins < 40 km/h"
        )
    print(f"-> {OUT_JSON}, {FIG_DIR / 'i24_wb_overview.png'} ({out['wall_s']} s)")


if __name__ == "__main__":
    main()
