"""Figure: emergent backward wave speed vs prescribed ring density.

Draws ``docs/figures/wave_speed_vs_density.png`` from
``artifacts/wave_speed_sitelength.json`` (US-101 fleet) and
``artifacts/wave_speed_sitelength_i24.json`` (I-24 fleet): mean front speed
per density with both detectors (absolute 40 km/h threshold, relative
0.5 × p90), the empirical 14–22 km/h band, the fitted US-101 FD wave speed
and Newell's ``(s0 + L)/T`` for each fleet (docs/WAVE_SPEED_DIAGNOSIS.md).

Run: ``uv run --no-sync python scripts/make_wave_speed_figure.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
FIG = REPO / "docs" / "figures" / "wave_speed_vs_density.png"
INK, INK2, GRID, SPINE, SURFACE = "#0b0b0b", "#52514e", "#e8e7e2", "#b5b4ae", "#ffffff"
FLEET_COLORS = {"us101": "#eb6834", "i24": "#2a78d6"}
NEWELL_KMH = {
    "us101": (18.3, 19.7),
    "i24": (16.8, 17.9),
}  # docs/WAVE_SPEED_DIAGNOSIS.md, I24_DATA.md §5
FD_W_KMH = 14.6  # artifacts/fd_us101.json congested-branch wave speed


def main() -> None:
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
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ax.axhspan(14.0, 22.0, color="#1baf7a", alpha=0.12, lw=0)
    ax.text(
        101, 21.6, "empirical band 14–22 km/h", ha="right", va="top", fontsize=8.5, color="#1baf7a"
    )
    ax.axhline(FD_W_KMH, color="#8b8a85", ls=":", lw=1)
    ax.text(38.5, FD_W_KMH + 0.3, "US-101 FD  w = 14.6 km/h", fontsize=8, color="#52514e")
    for fleet, fname in (
        ("us101", "wave_speed_sitelength.json"),
        ("i24", "wave_speed_sitelength_i24.json"),
    ):
        d = json.loads((REPO / "artifacts" / fname).read_text())
        rows = sorted(d["per_density"].values(), key=lambda r: r["density_veh_km"])
        dens = [r["density_veh_km"] for r in rows]
        rel = [r["relative_detector"]["mean_kmh"] for r in rows]
        n_rel = [r["relative_detector"]["n_backward_fronts"] for r in rows]
        absd = [(r["density_veh_km"], r["mean_kmh"]) for r in rows if r["mean_kmh"] is not None]
        label = "US-101 fleet (NGSIM)" if fleet == "us101" else "I-24 fleet (I-24 MOTION)"
        ax.plot(
            dens,
            rel,
            "-o",
            color=FLEET_COLORS[fleet],
            lw=1.8,
            ms=5,
            label=f"{label}, relative detector",
        )
        for x, y, n in zip(dens, rel, n_rel, strict=True):
            ax.annotate(
                f"n={n}",
                (x, y),
                textcoords="offset points",
                xytext=(6, -10),
                fontsize=7,
                color=INK2,
            )
        if absd:
            ax.plot(
                [a for a, _ in absd],
                [b for _, b in absd],
                "s",
                mfc="none",
                mec=FLEET_COLORS[fleet],
                ms=7,
                label=f"{label}, absolute 40 km/h detector",
            )
        lo, hi = NEWELL_KMH[fleet]
        ax.hlines([(lo + hi) / 2], 95, 104, color=FLEET_COLORS[fleet], lw=3, alpha=0.5)
        ax.text(
            104.5,
            (lo + hi) / 2,
            f"Newell {lo:.1f}–{hi:.1f}",
            va="center",
            fontsize=7.5,
            color=FLEET_COLORS[fleet],
        )
    ax.set_xlabel("prescribed ring density [veh/km per lane]")
    ax.set_ylabel("mean backward front speed [km/h]")
    ax.set_xlim(35, 118)
    ax.set_ylim(0, 24)
    ax.set_title(
        "1500 m ring, 5 seeds per density: wave speed rises with density toward Newell's (s0+L)/T",
        fontsize=9.5,
    )
    ax.legend(loc="lower right", fontsize=7.5)
    fig.tight_layout()
    fig.savefig(FIG, dpi=150)
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()
