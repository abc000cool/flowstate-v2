"""Figures for docs/I24_SWEEP.md from ``artifacts/i24_sweep_summary.json``.

Draws ``docs/figures/i24_sweep_dose_response.png``: throughput, mean travel
time, temporal speed spread and fuel against FollowerStopper penetration, one
line per compliance level, 95% CIs from the 20 seeds, baseline as a dashed
reference. Every value is read from the artifact.

Run: ``uv run --no-sync python scripts/i24_sweep_figures.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "artifacts" / "i24_sweep_summary.json"
FIG = REPO / "docs" / "figures" / "i24_sweep_dose_response.png"
INK, INK2, GRID, SPINE, SURFACE = "#0b0b0b", "#52514e", "#e8e7e2", "#b5b4ae", "#ffffff"
COLORS = {"1.00": "#2a78d6", "0.80": "#1baf7a", "0.50": "#eb6834", "0.25": "#8b8a85"}
PANELS = (
    ("throughput_veh_h", "throughput at the reference section [veh/h]"),
    ("mean_tt_s", "mean travel time over the span [s]"),
    ("sigma_v_temporal_ms", "temporal speed spread σ_v [m/s]"),
    ("fuel_ml_per_veh_km", "fuel [ml per vehicle-km]"),
)


def main() -> None:
    d = json.loads(SRC.read_text())
    cells = d["cells"]
    pens = [float(p) for p in d["penetrations"]]
    comps = [f"{float(c):.2f}" for c in d["compliances"]]
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.edgecolor": SPINE,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "legend.frameon": False,
            "font.size": 9,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(9.5, 7.0))
    for ax, (key, label) in zip(axes.ravel(), PANELS, strict=True):
        base = cells["baseline"]["aggregate"][key]
        ax.axhline(base["mean"], color=INK2, ls="--", lw=1, label="no control")
        for comp in comps:
            xs, ys, lo, hi = [], [], [], []
            for p in pens:
                c = cells.get(f"fs_p{p:.2f}_c{comp}")
                if c is None:
                    continue
                a = c["aggregate"][key]
                xs.append(100 * p)
                ys.append(a["mean"])
                lo.append(a["lo95"])
                hi.append(a["hi95"])
            ax.plot(
                xs,
                ys,
                "-o",
                ms=3.5,
                lw=1.6,
                color=COLORS[comp],
                label=f"compliance {float(comp):.0%}",
            )
            ax.fill_between(xs, lo, hi, color=COLORS[comp], alpha=0.15, lw=0)
        ax.set_xlabel("FollowerStopper penetration [%]")
        ax.set_ylabel(label)
        ax.set_xticks([1, 2, 5, 10, 15, 20])
    axes[0, 0].legend(loc="lower left", fontsize=8)
    fig.suptitle(
        f"I-24 replica ({d['scenario']}, unvalidated): FollowerStopper dose-response, 20 seeds per cell, 95% CIs",
        fontsize=9.5,
    )
    fig.tight_layout()
    fig.savefig(FIG, dpi=150)
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()
