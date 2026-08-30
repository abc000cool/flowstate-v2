"""M3 sweep analysis — per-cell metrics, CIs, and headline figures (CLAUDE.md §7, §11 M3).

Consumes the 540-run ``runs/m3_sweep`` battery written by ``scripts/m3_sweep.py``
(27 cells x 20 common-random-number seeds, MANIFEST.json) and produces:

* ``runs/m3_sweep/analysis.json`` — per cell: the ``validation.aggregate``
  95% t-CIs over the 20 replicates for every ``validation.metrics.Metrics``
  field, per-seed metric scalars, and paired per-seed deltas vs baseline
  (the sweep reuses ONE seed list in every cell, so cell − baseline
  differences are paired and their CIs are tighter than the marginal ones);
* ``artifacts/m3_sweep_summary.json`` — the committed compact copy:
  aggregates + paired deltas + provenance (config hashes, seed count,
  package versions), per-seed detail dropped;
* seven print-style figures under ``docs/figures/`` for docs/M3_RESULTS.md.

Metric conventions (recorded in both JSON outputs under ``metrics_args``):

* The route is a 2 km insertion-buffer edge followed by the 10 km main
  corridor, so trajectory ``x`` spans 0–12 km. Throughput is counted at
  ``x_ref`` = 7 000 m (mid main-corridor); travel times over the span
  2 000–11 500 m (main corridor minus a 500 m exit margin — the
  ``compute_metrics`` default span is degenerate here because only the
  single farthest vehicle reaches the global max x). Vehicles still en
  route at sim end are censored out of travel times (same censoring in
  every cell; contrasts stay paired).
* Metrics cover the full recorded run (t = 0–1200 s) including the
  configured 120 s warmup — the contract API ``compute_metrics(run_dir)``
  operates on the whole trajectory file; the transient is identical across
  cells under common random numbers.
* Paired deltas are cell − baseline per seed; ``*_reduction_pct`` is
  100·(baseline − cell)/baseline per seed (positive = improvement).
* The space-time figure pair uses the seed whose BASELINE temporal σ_v is
  closest to the 20-seed median (ties → smaller seed): a representative,
  deterministically chosen replicate, not a cherry-pick.

Sanity guards (hard errors, never skipped): all 540 meta.json files must
exist and parse, every run must have ``seeded == False`` and
``tier == "micro"``, and each meta's config_hash/seed must match MANIFEST.

Non-finite values (e.g. ``wave_speed_kmh`` when a replicate has no backward
front) are serialized as JSON ``null``; ``aggregate`` drops them before the
CI, so those CIs may carry n < 20 and an ``underpowered`` flag — reported
as-is, never hidden.

Usage (repo root)::

    uv run --no-sync python scripts/m3_analyze_sweep.py               # full pass
    uv run --no-sync python scripts/m3_analyze_sweep.py --figures-only
        # re-render figures from an existing analysis.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import multiprocessing
import os
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, PowerNorm, TwoSlopeNorm
from scipy.stats import t as student_t

from flowstate_core.units import ms_to_kmh
from validation.fields import speed_field
from validation.metrics import CI, Metrics, aggregate, compute_metrics

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "runs" / "m3_sweep"
SUMMARY_PATH = REPO_ROOT / "artifacts" / "m3_sweep_summary.json"
FIG_DIR = REPO_ROOT / "docs" / "figures"

PENETRATIONS = (0.01, 0.02, 0.05, 0.10, 0.15, 0.20)
COMPLIANCES = (0.25, 0.50, 0.80, 1.00)
BASELINE = "baseline"
FS_5_100 = "follower_stopper_p0.05_c1.00"
COMPARISON_CELLS = (BASELINE, FS_5_100, "pi_saturation_p0.05_c1.00", "jad_p0.05_c1.00")

#: Throughput reference cross-section [m]: middle of the 10 km main corridor
#: (which occupies x = 2000–12000 m after the 2 km insertion buffer).
X_REF_M = 7000.0
#: Travel-time measurement span [m]: main-corridor entry to 500 m before exit.
SPAN_M = (2000.0, 11500.0)

#: Metrics whose paired per-seed deltas vs baseline are recorded.
PAIRED_FIELDS = (
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "throughput_veh_h",
    "fuel_ml_per_veh_km",
    "wave_count",
    "mean_tt_s",
)
#: Metrics also recorded as paired percent reductions (positive = better).
REDUCTION_FIELDS = ("sigma_v_temporal_ms", "sigma_v_spatial_ms", "fuel_ml_per_veh_km")

# ---------------------------------------------------------------- print style
# Dataviz-skill reference palette (light mode), print figures on white.
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e8e7e2"
SPINE = "#b5b4ae"
SURFACE = "#ffffff"
COMPLIANCE_COLORS = {1.00: "#2a78d6", 0.80: "#eb6834", 0.50: "#1baf7a", 0.25: "#eda100"}
CONTROLLER_COLORS = {
    "baseline": "#8b8a85",
    "follower_stopper": "#2a78d6",
    "pi_saturation": "#eb6834",
    "jad": "#1baf7a",
}
CONTROLLER_LABELS = {
    "baseline": "baseline (no AVs)",
    "follower_stopper": "FollowerStopper",
    "pi_saturation": "PI-saturation",
    "jad": "JAD",
}
SEQ_BLUES = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
DIV_RED, DIV_MID, DIV_BLUE = "#e34948", "#f0efec", "#2a78d6"


def _style() -> None:
    """Consistent dark-on-white print style for every docs figure."""
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


# ------------------------------------------------------------- sanity guards
def load_manifest(root: Path) -> dict:
    """Parse MANIFEST.json and check the battery shape (hard errors)."""
    path = root / "MANIFEST.json"
    if not path.is_file():
        raise SystemExit(f"missing {path} — run scripts/m3_sweep.py first")
    manifest = json.loads(path.read_text())
    problems = []
    if len(manifest["cells"]) != 27:
        problems.append(f"expected 27 cells, MANIFEST has {len(manifest['cells'])}")
    if len(manifest["seeds"]) != manifest["replicates"]:
        problems.append("MANIFEST seed list length != replicates")
    if manifest["incomplete"]:
        problems.append(f"MANIFEST lists {len(manifest['incomplete'])} incomplete runs")
    if problems:
        raise SystemExit("MANIFEST sanity failure:\n  " + "\n  ".join(problems))
    return manifest


def guard_runs(root: Path, manifest: dict) -> dict[str, dict[int, Path]]:
    """Verify every (cell, seed) meta: parses, seeded=False, tier=micro.

    Any violation is collected and reported as one hard error — runs are
    never silently skipped (CLAUDE.md §0.1, §0.5).

    Returns:
        Mapping cell name → {seed → run directory}.
    """
    violations: list[str] = []
    run_dirs: dict[str, dict[int, Path]] = {}
    for cell in manifest["cells"]:
        name, chash = cell["name"], cell["config_hash"]
        run_dirs[name] = {}
        for seed in manifest["seeds"]:
            run_dir = root / name / chash / str(seed)
            meta_path = run_dir / "meta.json"
            tag = f"{name}/{seed}"
            if not meta_path.is_file():
                violations.append(f"{tag}: missing meta.json")
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                violations.append(f"{tag}: meta.json unreadable ({exc})")
                continue
            if meta.get("seeded") is not False:
                violations.append(f"{tag}: seeded={meta.get('seeded')!r}, expected False")
            if meta.get("tier") != "micro":
                violations.append(f"{tag}: tier={meta.get('tier')!r}, expected 'micro'")
            if meta.get("config_hash") != chash:
                violations.append(f"{tag}: config_hash {meta.get('config_hash')} != {chash}")
            if meta.get("seed") != seed:
                violations.append(f"{tag}: meta seed {meta.get('seed')} != dir seed {seed}")
            run_dirs[name][seed] = run_dir
    n_expected = len(manifest["cells"]) * len(manifest["seeds"])
    if violations:
        for v in violations:
            print(f"GUARD VIOLATION: {v}")
        raise SystemExit(f"{len(violations)} guard violations across {n_expected} runs — aborting")
    print(f"sanity guards passed: {n_expected} runs, all seeded=False, all tier=micro")
    return run_dirs


# ------------------------------------------------------------------- metrics
def _worker(payload: tuple[str, int, str]) -> tuple[str, int, dict[str, float]]:
    """Pool worker: standard metrics for one run directory."""
    cell_name, seed, run_dir = payload
    try:
        m = compute_metrics(run_dir, x_ref=X_REF_M, span=SPAN_M)
    except Exception as exc:  # hard error, identified — never skipped
        raise RuntimeError(f"compute_metrics failed for {cell_name}/{seed}: {exc}") from exc
    return cell_name, seed, dataclasses.asdict(m)


def compute_all_metrics(
    run_dirs: dict[str, dict[int, Path]], procs: int
) -> dict[str, dict[int, dict[str, float]]]:
    """Per-replicate metrics for every cell, in a spawn process pool."""
    jobs = [
        (cell, seed, str(run_dir))
        for cell, seeds in run_dirs.items()
        for seed, run_dir in seeds.items()
    ]
    out: dict[str, dict[int, dict[str, float]]] = {cell: {} for cell in run_dirs}
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=min(procs, len(jobs))) as pool:
        for i, (cell, seed, metrics) in enumerate(pool.imap_unordered(_worker, jobs), 1):
            out[cell][seed] = metrics
            if i % 100 == 0 or i == len(jobs):
                print(f"metrics {i}/{len(jobs)}", flush=True)
    return out


def _t_ci(values: np.ndarray) -> dict[str, float | int | None]:
    """95% t-CI dict over finite values (same formula as validation.aggregate)."""
    finite = values[np.isfinite(values)]
    n = int(finite.size)
    if n == 0:
        return {"mean": None, "lo95": None, "hi95": None, "n": 0}
    mean = float(finite.mean())
    if n == 1:
        return {"mean": mean, "lo95": None, "hi95": None, "n": 1}
    half = float(student_t.ppf(0.975, n - 1) * finite.std(ddof=1) / math.sqrt(n))
    return {"mean": mean, "lo95": mean - half, "hi95": mean + half, "n": n}


def _ci_dict(ci: CI) -> dict[str, float | int | bool | None]:
    d: dict[str, float | int | bool | None] = {
        k: (None if isinstance(v, float) and not math.isfinite(v) else v)
        for k, v in ci._asdict().items()
    }
    d["underpowered"] = ci.underpowered
    return d


def build_analysis(
    manifest: dict, per_seed: dict[str, dict[int, dict[str, float]]], root: Path
) -> dict:
    """Assemble the full analysis dict (aggregates, paired deltas, per-seed)."""
    seeds = list(manifest["seeds"])
    base = per_seed[BASELINE]
    cells_out: dict[str, dict] = {}
    for cell in manifest["cells"]:
        name = cell["name"]
        metrics_list = [Metrics(**per_seed[name][s]) for s in seeds]
        agg = {field: _ci_dict(ci) for field, ci in aggregate(metrics_list).items()}
        paired: dict[str, dict] | None = None
        if name != BASELINE:
            paired = {}
            for field in PAIRED_FIELDS:
                deltas = np.array(
                    [per_seed[name][s][field] - base[s][field] for s in seeds], dtype=np.float64
                )
                paired[f"{field}_delta"] = _t_ci(deltas)
            for field in REDUCTION_FIELDS:
                reds = np.array(
                    [
                        100.0 * (base[s][field] - per_seed[name][s][field]) / base[s][field]
                        for s in seeds
                    ],
                    dtype=np.float64,
                )
                paired[f"{field}_reduction_pct"] = _t_ci(reds)
        cells_out[name] = {
            "controller": cell["controller"],
            "penetration": cell["penetration"],
            "compliance": cell["compliance"],
            "config_hash": cell["config_hash"],
            "aggregate": agg,
            "paired_delta_vs_baseline": paired,
            "per_seed": {
                str(s): {
                    k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                    for k, v in per_seed[name][s].items()
                }
                for s in seeds
            },
        }

    sig = np.array([base[s]["sigma_v_temporal_ms"] for s in seeds])
    med = float(np.median(sig))
    order = sorted(range(len(seeds)), key=lambda i: (abs(sig[i] - med), seeds[i]))
    rep_seed = seeds[order[0]]
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "scripts/m3_analyze_sweep.py",
        "sweep_root": str(root.relative_to(REPO_ROOT)),
        "scenario": manifest["scenario"],
        "master_seed": manifest["master_seed"],
        "replicates": manifest["replicates"],
        "seeds": seeds,
        "versions": manifest["versions"],
        "metrics_args": {
            "x_ref_m": X_REF_M,
            "span_m": list(SPAN_M),
            "dt_bin_s": 15.0,
            "dx_bin_m": 75.0,
            "v_jam_thresh_ms": 40.0 / 3.6,
            "min_area_bins": 4,
            "note": "full recorded run 0-1200 s incl. 120 s warmup; x incl. 2 km entry buffer",
        },
        "conventions": {
            "delta": "cell - baseline, per seed (common random numbers), 95% t-CI",
            "reduction_pct": "100*(baseline - cell)/baseline per seed; positive = improvement",
        },
        "representative": {
            "rule": "seed whose baseline sigma_v_temporal_ms is closest to the 20-seed median",
            "seed": rep_seed,
            "baseline_sigma_v_temporal_ms": float(sig[order[0]]),
            "comparison_cell": FS_5_100,
        },
        "cells": cells_out,
    }


def write_outputs(analysis: dict, root: Path) -> None:
    """Persist full analysis (runs/) and the compact committed summary."""
    (root / "analysis.json").write_text(json.dumps(analysis, indent=1, allow_nan=False))
    summary = {k: v for k, v in analysis.items() if k != "cells"}
    summary["cells"] = {
        name: {k: v for k, v in cell.items() if k != "per_seed"}
        for name, cell in analysis["cells"].items()
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=1, allow_nan=False) + "\n")
    print(f"wrote {root / 'analysis.json'} and {SUMMARY_PATH}")


# ------------------------------------------------------------------- figures
def _cell_ci(analysis: dict, name: str, field: str) -> tuple[float, float, float]:
    ci = analysis["cells"][name]["aggregate"][field]
    return ci["mean"], ci["lo95"], ci["hi95"]


def _spread(ys: list[float], min_gap: float) -> list[float]:
    """Nudge label y-positions apart (ascending sweep, minimum spacing)."""
    out = np.asarray(ys, dtype=np.float64).copy()
    prev = -np.inf
    for i in np.argsort(out, kind="stable"):
        if out[i] - prev < min_gap:
            out[i] = prev + min_gap
        prev = out[i]
    return [float(v) for v in out]


def dose_response_figure(analysis: dict, field: str, ylabel: str, title: str, fname: str) -> None:
    """Metric vs penetration, one line per compliance, CI bands, baseline ref."""
    x = [p * 100 for p in PENETRATIONS]
    fig, ax = plt.subplots(figsize=(8, 5))
    b_mean, b_lo, b_hi = _cell_ci(analysis, BASELINE, field)
    ax.axhspan(b_lo, b_hi, color="#d9d8d3", alpha=0.5, lw=0, zorder=1)
    ax.axhline(b_mean, color=INK2, ls="--", lw=1.4, zorder=2)
    end_ys, labels, colors = [], [], []
    for comp in sorted(COMPLIANCE_COLORS, reverse=True):
        color = COMPLIANCE_COLORS[comp]
        cells = [f"follower_stopper_p{p:.2f}_c{comp:.2f}" for p in PENETRATIONS]
        means = [_cell_ci(analysis, c, field)[0] for c in cells]
        los = [_cell_ci(analysis, c, field)[1] for c in cells]
        his = [_cell_ci(analysis, c, field)[2] for c in cells]
        ax.fill_between(x, los, his, color=color, alpha=0.13, lw=0, zorder=2)
        ax.plot(x, means, "-o", color=color, lw=2, ms=5.5, zorder=3,
                label=f"{comp * 100:.0f}% compliance")  # fmt: skip
        end_ys.append(means[-1])
        labels.append(f"{comp * 100:.0f}%")
        colors.append(color)
    lo_ax, hi_ax = ax.get_ylim()  # post-autoscale axis span, so labels never collide
    for y_lab, y_end, lab, color in zip(_spread(end_ys, (hi_ax - lo_ax) * 0.05), end_ys, labels,
                                        colors, strict=True):  # fmt: skip
        ax.annotate(lab, xy=(x[-1], y_end), xytext=(x[-1] + 0.35, y_lab), fontsize=8,
                    color=color, va="center")  # fmt: skip
    ax.annotate("baseline (0% AVs), 95% CI", xy=(0.4, b_mean), fontsize=8, color=INK2,
                va="bottom")  # fmt: skip
    ax.set_xlim(0, 22.5)
    ax.set_xticks(list(x))
    ax.set_xlabel("AV penetration [% of fleet]")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(title="compliance", loc="best", title_fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / fname, dpi=150)
    plt.close(fig)
    print(f"figure {fname}")


def reduction_matrix_figure(analysis: dict, fname: str) -> None:
    """Penetration x compliance matrix of paired σ_v reduction [%], annotated."""
    comps = sorted(COMPLIANCE_COLORS, reverse=True)
    data = np.zeros((len(comps), len(PENETRATIONS)))
    for i, comp in enumerate(comps):
        for j, p in enumerate(PENETRATIONS):
            cell = analysis["cells"][f"follower_stopper_p{p:.2f}_c{comp:.2f}"]
            data[i, j] = cell["paired_delta_vs_baseline"]["sigma_v_temporal_ms_reduction_pct"][
                "mean"
            ]
    vmax = max(float(np.abs(data).max()), 1.0)
    cmap = LinearSegmentedColormap.from_list("div", [DIV_RED, DIV_MID, DIV_BLUE])
    norm = TwoSlopeNorm(vcenter=0.0, vmin=-vmax, vmax=vmax)
    fig, ax = plt.subplots(figsize=(8, 4.3))
    im = ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")
    ax.grid(False)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            color = SURFACE if abs(data[i, j]) > 0.62 * vmax else INK
            ax.text(j, i, f"{data[i, j]:+.1f}", ha="center", va="center", fontsize=9,
                    color=color)  # fmt: skip
    ax.set_xticks(range(len(PENETRATIONS)), [f"{p * 100:g}" for p in PENETRATIONS])
    ax.set_yticks(range(len(comps)), [f"{c * 100:.0f}" for c in comps])
    ax.set_xlabel("AV penetration [% of fleet]")
    ax.set_ylabel("compliance [%]")
    ax.set_title(
        "FollowerStopper: temporal σ_v reduction vs baseline [%]\n"
        "(paired per-seed mean over 20 common-random-number replicates; positive = calmer)"
    )
    cbar = fig.colorbar(im, ax=ax, shrink=0.9)
    cbar.set_label("σ_v reduction [%]", fontsize=9)
    cbar.outline.set_edgecolor(SPINE)
    fig.tight_layout()
    fig.savefig(FIG_DIR / fname, dpi=150)
    plt.close(fig)
    print(f"figure {fname}")


def controller_comparison_figure(analysis: dict, fname: str) -> None:
    """Bars with 95% CI whiskers at 5% penetration / 100% compliance."""
    panels = [
        ("sigma_v_temporal_ms", "temporal σ_v [m/s]", "{:.2f}"),
        ("throughput_veh_h", "throughput [veh/h]", "{:.0f}"),
        ("fuel_ml_per_veh_km", "fuel [ml/veh·km]", "{:.1f}"),
    ]
    keys = ["baseline", "follower_stopper", "pi_saturation", "jad"]
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 4.2))
    for ax, (field, ylabel, fmt) in zip(axes, panels, strict=True):
        for k, (key, cell) in enumerate(zip(keys, COMPARISON_CELLS, strict=True)):
            mean, lo, hi = _cell_ci(analysis, cell, field)
            ax.bar(k, mean, width=0.62, color=CONTROLLER_COLORS[key], zorder=3)
            ax.errorbar(k, mean, yerr=[[mean - lo], [hi - mean]], fmt="none", ecolor=INK,
                        elinewidth=1.2, capsize=3.5, zorder=4)  # fmt: skip
            ax.annotate(fmt.format(mean), xy=(k, hi), xytext=(0, 3),
                        textcoords="offset points", ha="center", fontsize=7.5,
                        color=INK)  # fmt: skip
        ax.set_xticks(range(len(keys)),
                      [CONTROLLER_LABELS[k].replace(" (no AVs)", "\n(no AVs)") for k in keys],
                      fontsize=7.5, rotation=20)  # fmt: skip
        ax.set_ylabel(ylabel)
        ax.margins(y=0.12)
    fig.suptitle(
        "Controller comparison at 5% penetration, 100% compliance (mean ± 95% CI, n=20 seeds)",
        fontsize=10.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIG_DIR / fname, dpi=150)
    plt.close(fig)
    print(f"figure {fname}")


def spacetime_figure(analysis: dict, root: Path, fname: str) -> None:
    """Baseline vs FollowerStopper 5%/100% space-time speed, same seed."""
    seed = analysis["representative"]["seed"]
    cmap = LinearSegmentedColormap.from_list("blues", SEQ_BLUES)
    cmap.set_bad(SURFACE)
    # gamma < 1 widens the sub-40 km/h (jam) band of the ramp; the colorbar's
    # tick spacing shows the warp, so the encoding stays honest.
    norm = PowerNorm(gamma=0.6, vmin=0.0, vmax=120.0)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    ims = []
    for ax, cell_name, title in zip(
        axes,
        (BASELINE, FS_5_100),
        ("baseline (no AVs)", "FollowerStopper, 5% penetration / 100% compliance"),
        strict=True,
    ):
        cell = analysis["cells"][cell_name]
        run_dir = root / cell_name / cell["config_hash"] / str(seed)
        field = speed_field(pd.read_parquet(run_dir / "trajectories.parquet"))
        kmh = np.vectorize(ms_to_kmh)(field.mean_speed)
        ims.append(
            ax.imshow(
                kmh.T,
                origin="lower",
                aspect="auto",
                cmap=cmap,
                norm=norm,
                extent=(
                    field.t_edges[0] / 60.0,
                    field.t_edges[-1] / 60.0,
                    field.x_edges[0] / 1000.0,
                    field.x_edges[-1] / 1000.0,
                ),
            )
        )
        ax.grid(False)
        ax.axhline(2.0, color=INK2, ls=":", lw=1)
        ax.set_xlabel("time [min]")
        ax.set_title(title, fontsize=9.5)
    axes[0].set_ylabel("position along route [km]")
    axes[0].annotate(
        "insertion buffer ends",
        xy=(19.6, 2.12),
        fontsize=7,
        color=INK,
        ha="right",
        bbox={"boxstyle": "round,pad=0.2", "fc": SURFACE, "ec": "none", "alpha": 0.75},
    )
    cbar = fig.colorbar(ims[1], ax=axes, shrink=0.9, pad=0.02)
    cbar.set_ticks([0, 10, 20, 40, 60, 80, 120])
    cbar.set_label("mean speed [km/h] (white = no vehicles in bin)", fontsize=9)
    cbar.outline.set_edgecolor(SPINE)
    fig.suptitle(
        f"Space-time speed, seed {seed} (baseline σ_v closest to median of 20 seeds); "
        "light = jammed, dark = free-flowing",
        fontsize=10,
    )
    fig.savefig(FIG_DIR / fname, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"figure {fname}")


def make_figures(analysis: dict, root: Path) -> None:
    _style()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    dose_response_figure(
        analysis, "sigma_v_temporal_ms", "temporal σ_v [m/s]",
        "Speed variation vs AV penetration — FollowerStopper on corridor_10km (EIDM)\n"
        "mean ± 95% CI over 20 seeds; per-vehicle speed std over time",
        "m3_sigma_v_vs_penetration.png",
    )  # fmt: skip
    dose_response_figure(
        analysis, "throughput_veh_h", "throughput at x = 7 km [veh/h]",
        "Throughput vs AV penetration — FollowerStopper on corridor_10km (EIDM)\n"
        "mean ± 95% CI over 20 seeds",
        "m3_throughput_vs_penetration.png",
    )  # fmt: skip
    dose_response_figure(
        analysis, "fuel_ml_per_veh_km", "fuel [ml/veh·km]",
        "Fuel consumption vs AV penetration — FollowerStopper on corridor_10km (EIDM)\n"
        "mean ± 95% CI over 20 seeds; SUMO HBEFA4 totals / VMT",
        "m3_fuel_vs_penetration.png",
    )  # fmt: skip
    dose_response_figure(
        analysis, "wave_count", "stop-and-go waves per 20-min run [count]",
        "Wave count vs AV penetration — FollowerStopper on corridor_10km (EIDM)\n"
        "mean ± 95% CI over 20 seeds; detection: <40 km/h components, Theil-Sen fronts",
        "m3_wave_count_vs_penetration.png",
    )  # fmt: skip
    reduction_matrix_figure(analysis, "m3_sigma_v_reduction_matrix.png")
    controller_comparison_figure(analysis, "m3_controller_comparison.png")
    spacetime_figure(analysis, root, "m3_spacetime_baseline_vs_fs.png")


# ---------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="sweep run-tree root")
    ap.add_argument(
        "--procs",
        type=int,
        default=int(os.environ.get("M3_PROCS", "6")),
        help="metrics process-pool size (env M3_PROCS, default 6)",
    )
    ap.add_argument(
        "--figures-only",
        action="store_true",
        help="re-render figures from an existing analysis.json (no metric recompute)",
    )
    args = ap.parse_args()

    manifest = load_manifest(args.root)
    if args.figures_only:
        analysis = json.loads((args.root / "analysis.json").read_text())
    else:
        run_dirs = guard_runs(args.root, manifest)
        per_seed = compute_all_metrics(run_dirs, args.procs)
        analysis = build_analysis(manifest, per_seed, args.root)
        write_outputs(analysis, args.root)
    make_figures(analysis, args.root)
    print("m3 analysis complete")


if __name__ == "__main__":
    main()
