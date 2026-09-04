"""Figures for docs/I24_VALIDATION.md from the validation artifacts.

* ``docs/figures/i24_validation_fields.png`` — observed vs simulated
  space-time speed fields (60 s × 100 m) over the study period on the
  measured span: the tracked fragments, one replicate of the tracked-demand
  arm, one replicate of the coverage-corrected arm, one of the fitted arm
  (first seed of each).
* ``docs/figures/i24_validation_waves.png`` — backward wave-front speeds:
  observed fronts vs every simulated front of each arm (20 replicates), with
  the 14–22 km/h band.

Every value comes from ``artifacts/i24_validation_<arm>.json`` and the run
trees they name. Run: ``uv run --no-sync python scripts/i24_validation_figures.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, PowerNorm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WARMUP_S
from i24_data import REPO_ROOT, load_mainline

from validation.fields import speed_field

FIG_DIR = REPO_ROOT / "docs" / "figures"
INK, INK2, GRID, SPINE, SURFACE = "#0b0b0b", "#52514e", "#e8e7e2", "#b5b4ae", "#ffffff"
SEQ_BLUES = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
ARM_COLORS = {"tracked": "#eb6834", "corrected": "#2a78d6", "speedcal": "#1baf7a"}
ARM_LABELS = {
    "tracked": "demand as tracked",
    "corrected": "demand ÷ coverage",
    "speedcal": "coverage-shaped demand × 0.85 (fitted)",
}


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.titlesize": 10,
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


def _results(arm: str) -> dict:
    return json.loads((REPO_ROOT / "artifacts" / f"i24_validation_{arm}.json").read_text())


def _sim_frame(arm: str, res: dict) -> pd.DataFrame:
    inputs = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    # The artifact's run_dirs may point at the machine that ran the battery;
    # use the first replicate whose trajectories exist under this checkout.
    root = REPO_ROOT / "runs" / "i24_validation" / arm / res["config_hash"]
    candidates = [root / str(seed) for seed in res["seeds"]]
    candidates += [Path(d) for d in res["simulated"]["run_dirs"]]
    run_dir = next((d for d in candidates if (d / "trajectories.parquet").is_file()), None)
    if run_dir is None:
        raise FileNotFoundError(f"no replicate with trajectories for arm {arm!r} under {root}")
    res["_figure_seed"] = int(run_dir.name)
    df = pd.read_parquet(run_dir / "trajectories.parquet", columns=["t", "x", "v"])
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - WARMUP_S
    return df[(df["t"] >= 0.0)]


def fields_figure(arms: list[str]) -> None:
    span_hi = _results(arms[0])["observed"]["span_data_x_m"][1]
    obs = load_mainline(
        t_range_s=(T_STUDY_LO_S, T_STUDY_HI_S), x_range_m=(0.0, span_hi), columns=["t", "x", "v"]
    )
    obs["t"] = obs["t"] - T_STUDY_LO_S
    panels = [("observed\ntracked fragments (I-24 MOTION)", obs)]
    for arm in arms:
        res = _results(arm)
        df = _sim_frame(arm, res)
        df = df[(df["x"] >= 0.0) & (df["x"] < span_hi)]
        label = ARM_LABELS[arm]
        panels.append((f"i24_replica, seed {res['_figure_seed']}\n{label}", df))
    cmap = LinearSegmentedColormap.from_list("blues", SEQ_BLUES)
    cmap.set_bad(SURFACE)
    norm = PowerNorm(gamma=0.6, vmin=0.0, vmax=120.0)
    _style()
    fig, axes = plt.subplots(1, len(panels), figsize=(4.6 * len(panels), 6.2), sharey=True)
    im = None
    for ax, (title, df) in zip(axes, panels, strict=True):
        f = speed_field(df, dt_bin=60.0, dx_bin=100.0)
        im = ax.imshow(
            (f.mean_speed * 3.6).T,
            origin="lower",
            aspect="auto",
            cmap=cmap,
            norm=norm,
            extent=(
                f.t_edges[0] / 60.0,
                f.t_edges[-1] / 60.0,
                f.x_edges[0] / 1000.0,
                f.x_edges[-1] / 1000.0,
            ),
        )
        ax.grid(False)
        ax.set_title(title, fontsize=8.5)
        ax.set_xlabel("minutes after 06:30 CST")
    axes[0].set_ylabel("distance along westbound travel from MM 62.7 [km]")
    cb = fig.colorbar(im, ax=axes, pad=0.01, fraction=0.02)
    cb.set_label("speed [km/h]")
    cb.set_ticks([0, 20, 40, 60, 80, 100, 120])
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / "i24_validation_fields.png", dpi=150, bbox_inches="tight")
    print(f"-> {FIG_DIR / 'i24_validation_fields.png'}")


def waves_figure(arms: list[str]) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bins = np.arange(0.0, 40.0, 1.0)
    obs = _results(arms[0])["observed"]["waves"]["backward_speeds_kmh"]
    ax.hist(obs, bins=bins, color="#8b8a85", alpha=0.9, label=f"observed fronts (n={len(obs)})")
    for arm in arms:
        res = _results(arm)
        sp = res["simulated"]["all_backward_speeds_kmh"]
        weights = np.full(len(sp), 1.0 / res["replicates"])
        label = ARM_LABELS[arm]
        ax.hist(
            sp,
            bins=bins,
            weights=weights,
            histtype="step",
            lw=1.8,
            color=ARM_COLORS[arm],
            label=f"simulated, {label} (fronts per replicate, n={len(sp)}/{res['replicates']})",
        )
    ax.axvspan(14.0, 22.0, color="#1baf7a", alpha=0.12, lw=0)
    ax.text(
        18.0,
        ax.get_ylim()[1] * 0.95,
        "empirical band 14–22 km/h",
        ha="center",
        va="top",
        fontsize=8.5,
        color="#1baf7a",
    )
    ax.set_xlabel("backward wave-front speed [km/h] (15 s × 75 m field, 40 km/h threshold)")
    ax.set_ylabel("fronts")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "i24_validation_waves.png", dpi=150)
    print(f"-> {FIG_DIR / 'i24_validation_waves.png'}")


def main() -> None:
    arms = [
        a
        for a in ("tracked", "corrected", "speedcal")
        if (REPO_ROOT / "artifacts" / f"i24_validation_{a}.json").is_file()
    ]
    fields_figure(arms)
    waves_figure(arms)


if __name__ == "__main__":
    main()
