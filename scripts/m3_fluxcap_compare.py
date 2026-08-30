"""M3 flux-cap-variant comparison with a BINDING constraint (CLAUDE.md §5.5).

Compares the two moving-bottleneck variants of the CTM screening tier —
``flux_cap`` (``F ← min(F, ρ_i·v*)``, the discrete Delle Monache–Goatin
moving flux constraint) and ``capacity`` (``F ← min(F, α(v*)·q_max)``) —
against MICRO-tier ground truth, with the constraint actually binding.

Why JAD: the earlier follower_stopper arm produced an identical-variants
null — that controller never commands below the local equilibrium speed, so
neither cap ever bound. JAD's slow-in phase genuinely commands
``v_slow = β·v`` below the prevailing speed, so its AVs are real moving
bottlenecks.

Design (per replicate, default 20 seeded replicates of ``corridor_10km``
with ``jad`` at 5% penetration / 100% compliance):

1. Run the micro tier (SUMO/EIDM — the ground truth).
2. Extract every complied AV's recorded trajectory ``(t, x, v)`` and play
   it back through ``run_macro`` via the v*-trajectory entry point
   (``macrosim.bottleneck.VStarTrajectory``) — once per bottleneck variant.
   Both variants therefore see the SAME v* signal; only the constraint
   form differs.
3. Compare each macro field against the micro Edie fields on a common
   30 s × 500 m grid (post-warmup, corridor proper): speed RMSE, density
   RMSE, and the upstream-shadow shape (mean speed profile in AV-relative
   coordinates, offsets −2000 … +1000 m).

The macro FD is fitted from micro-tier data of the same corridor (pooled
Edie bins of 3 BASELINE no-AV replicates via
``calibration.fd_fit.fit_triangular_fd`` — the JAD runs themselves are too
dampened to populate the congested branch) — the comparison isolates the
bottleneck-variant question, not absolute macro fidelity, and says so.

Outputs: ``runs/m3_fluxcap/results.json`` + ``docs/figures/
fluxcap_comparison.png``. All macro rows are ``tier="screening"`` and can
never back a validation report (CLAUDE.md §5.6).

Usage (repo root)::

    uv run --no-sync python scripts/m3_fluxcap_compare.py --replicates 2  # smoke
    M3_PROCS=8 uv run --no-sync python scripts/m3_fluxcap_compare.py      # full 20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from calibration.fd_fit import fit_triangular_fd
from flowstate_core.artifacts import FDCalibration
from flowstate_core.config import config_hash
from flowstate_core.rng import spawn_seeds
from flowstate_core.units import veh_m_to_veh_km
from macrosim.bottleneck import VStarTrajectory
from macrosim.runner import run_macro
from microsim.runner import _versions, run_replicates
from microsim.scenarios import load_scenario
from validation.fields import density_field, flow_field

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "runs" / "m3_fluxcap"
FIG_PATH = REPO_ROOT / "docs" / "figures" / "fluxcap_comparison.png"

PENETRATION = 0.05
COMPLIANCE = 1.0
CONTROLLER = "jad"
VARIANTS = ("flux_cap", "capacity")

ENTRY_BUFFER_M = 2000.0  # corridor_10km: min(2000, 10000)
CORRIDOR_M = 10_000.0
WARMUP_S = 120.0

GRID_DT_S = 30.0
GRID_DX_M = 500.0
MACRO_DX_M = 100.0

SHADOW_OFFSETS_M = np.arange(-2000.0, 1000.0 + 1.0, 250.0)
"""AV-relative offsets for the upstream-shadow profile [m]; negative =
behind (upstream of) the AV."""

MIN_MICRO_DENSITY = 1e-4
"""Micro Edie density [veh/m] below which a comparison bin is skipped
(essentially empty road — speed undefined)."""

FD_BASELINE_REPLICATES = 3
"""No-AV baseline replicates pooled for the macro FD fit (the JAD runs are
too dampened to populate the congested branch)."""


def _ci(values: list[float]) -> dict:
    """t-distribution 95% CI dict over finite replicate values."""
    from scipy.stats import t as student_t

    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    n = int(finite.size)
    if n == 0:
        return {"mean": None, "lo95": None, "hi95": None, "n": 0, "underpowered": True}
    mean = float(finite.mean())
    if n == 1:
        return {"mean": mean, "lo95": None, "hi95": None, "n": 1, "underpowered": True}
    half = float(student_t.ppf(0.975, n - 1) * finite.std(ddof=1) / math.sqrt(n))
    return {
        "mean": mean,
        "lo95": mean - half,
        "hi95": mean + half,
        "n": n,
        "underpowered": n < 20,
    }


def _micro_frame(run_dir: Path) -> pd.DataFrame:
    """Corridor-proper micro samples in macro coordinates (x − entry buffer)."""
    df = pd.read_parquet(run_dir / "trajectories.parquet").copy()
    df["x"] = df["x"] - ENTRY_BUFFER_M
    return df


def _micro_fields(df: pd.DataFrame, sample_dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Micro Edie (density, speed) on the fixed comparison grid.

    Returns arrays of shape ``[nt, nx]`` over t ∈ [WARMUP, 1200) ×
    x ∈ [0, 10 km); speed is TTD/TTS (NaN where the bin is essentially
    empty).
    """
    sel = df[(df["x"] >= 0.0) & (df["x"] < CORRIDOR_M) & (df["t"] >= WARMUP_S)]
    dens = density_field(sel, dt_bin=GRID_DT_S, dx_bin=GRID_DX_M, sample_dt=sample_dt)
    flow = flow_field(sel, dt_bin=GRID_DT_S, dx_bin=GRID_DX_M, sample_dt=sample_dt)
    with np.errstate(invalid="ignore", divide="ignore"):
        speed = np.where(dens.density > MIN_MICRO_DENSITY, flow.flow / dens.density, np.nan)
    return dens.density, speed


def _macro_fields(run_dir: Path, duration_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Macro (density, speed) averaged onto the same comparison grid.

    Cell densities are averaged per bin (equal 100 m cells); bin speed is
    the density-weighted mean of cell speeds (≈ flow/density, matching the
    micro Edie speed definition).
    """
    e = pd.read_parquet(run_dir / "edges.parquet")
    e = e[(e["t_bin"] >= WARMUP_S) & (e["t_bin"] < duration_s)]
    nt = math.ceil((duration_s - WARMUP_S) / GRID_DT_S)
    nx = round(CORRIDOR_M / GRID_DX_M)
    ti = np.minimum(((e["t_bin"].to_numpy() - WARMUP_S) / GRID_DT_S).astype(np.int64), nt - 1)
    xi = np.minimum((e["x_bin"].to_numpy() / GRID_DX_M).astype(np.int64), nx - 1)
    rho = e["density"].to_numpy(dtype=np.float64)
    v = e["mean_speed"].to_numpy(dtype=np.float64)
    rho_sum = np.zeros((nt, nx))
    rho_cnt = np.zeros((nt, nx))
    rho_v = np.zeros((nt, nx))
    np.add.at(rho_sum, (ti, xi), rho)
    np.add.at(rho_cnt, (ti, xi), 1.0)
    np.add.at(rho_v, (ti, xi), rho * v)
    with np.errstate(invalid="ignore", divide="ignore"):
        dens = np.where(rho_cnt > 0, rho_sum / rho_cnt, np.nan)
        speed = np.where(rho_sum > 0, rho_v / rho_sum, np.nan)
    return dens, speed


def _extract_av_trajectories(df: pd.DataFrame, meta: dict) -> list[VStarTrajectory]:
    """Complied-AV playback trajectories in macro coordinates."""
    complied = set(meta["complied_ids"])
    out: list[VStarTrajectory] = []
    for _vid, g in df[df["veh_id"].isin(complied)].groupby("veh_id", sort=True):
        g = g.sort_values("t")
        if len(g) < 2:
            continue
        out.append(
            VStarTrajectory(
                t_s=g["t"].to_numpy(dtype=np.float64),
                x_m=g["x"].to_numpy(dtype=np.float64),
                v_ms=g["v"].to_numpy(dtype=np.float64),
            )
        )
    return out


def _shadow_profile(
    speed: np.ndarray, trajs: list[VStarTrajectory], duration_s: float
) -> np.ndarray:
    """Mean speed at AV-relative offsets, averaged over AVs and time.

    For each comparison-grid time row and each AV active in it, samples the
    field speed at ``x_AV + offset`` for every offset in
    :data:`SHADOW_OFFSETS_M`; NaN field bins are skipped. The resulting
    profile is the *upstream-shadow shape*: how far and how deeply the
    moving bottleneck depresses speeds behind itself.
    """
    nt, nx = speed.shape
    sums = np.zeros(len(SHADOW_OFFSETS_M))
    cnts = np.zeros(len(SHADOW_OFFSETS_M))
    for ti in range(nt):
        t_mid = WARMUP_S + (ti + 0.5) * GRID_DT_S
        if t_mid > duration_s:
            break
        for traj in trajs:
            state = traj.state_at(t_mid)
            if state is None:
                continue
            x_av = state[0]
            if not 0.0 <= x_av < CORRIDOR_M:
                continue
            for oi, off in enumerate(SHADOW_OFFSETS_M):
                x = x_av + off
                if not 0.0 <= x < CORRIDOR_M:
                    continue
                v = speed[ti, min(int(x / GRID_DX_M), nx - 1)]
                if np.isfinite(v):
                    sums[oi] += v
                    cnts[oi] += 1.0
    with np.errstate(invalid="ignore"):
        return np.where(cnts > 0, sums / np.maximum(cnts, 1.0), np.nan)


def _rmse(a: np.ndarray, b: np.ndarray) -> float:
    both = np.isfinite(a) & np.isfinite(b)
    if not both.any():
        return math.nan
    return float(np.sqrt(np.mean((a[both] - b[both]) ** 2)))


def _fit_micro_fd(frames: list[pd.DataFrame], sample_dt: float, provenance: str) -> FDCalibration:
    """Triangular FD from pooled micro Edie bins (30 s × 100 m, post-warmup)."""
    rows = []
    for df in frames:
        sel = df[(df["x"] >= 0.0) & (df["x"] < CORRIDOR_M) & (df["t"] >= WARMUP_S)]
        dens = density_field(sel, dt_bin=30.0, dx_bin=100.0, sample_dt=sample_dt)
        flow = flow_field(sel, dt_bin=30.0, dx_bin=100.0, sample_dt=sample_dt)
        d = dens.density.ravel()
        q = flow.flow.ravel()
        keep = d > MIN_MICRO_DENSITY
        rows.append(pd.DataFrame({"density_veh_m": d[keep], "flow_veh_s": q[keep]}))
    pooled = pd.concat(rows, ignore_index=True)
    return fit_triangular_fd(
        pooled,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=provenance,
        data_hash=provenance,
        uncongested_max_density=0.02,  # 20 veh/km, same cut as the US-101 fit
        seed=42,
        notes=(
            "FD fitted from the micro ground-truth runs themselves so the "
            "macro-vs-micro comparison isolates the bottleneck-variant question; "
            "screening use only, not a field calibration."
        ),
    )


def _render_figure(
    micro_speed: np.ndarray,
    macro_speeds: dict[str, np.ndarray],
    shadows: dict[str, np.ndarray],
    rmse_ci: dict[str, dict],
    seed: int,
) -> None:
    """4-panel comparison figure: three speed fields + shadow profiles."""
    fig, axes = plt.subplots(2, 2, figsize=(12.0, 8.0))
    extent = (0.0, CORRIDOR_M / 1000.0, WARMUP_S, WARMUP_S + micro_speed.shape[0] * GRID_DT_S)
    panels = [
        (axes[0][0], micro_speed, f"micro ground truth (SUMO/EIDM, seed {seed})"),
        (axes[0][1], macro_speeds["flux_cap"], "macro CTM — flux_cap (F ≤ ρ·v*)"),
        (axes[1][0], macro_speeds["capacity"], "macro CTM — capacity (F ≤ α(v*)·q_max)"),
    ]
    for ax, field, title in panels:
        im = ax.imshow(
            field,
            origin="lower",
            aspect="auto",
            extent=extent,
            vmin=0.0,
            vmax=34.0,
            cmap="RdYlGn",
        )
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("x [km]")
        ax.set_ylabel("t [s]")
        fig.colorbar(im, ax=ax, label="speed [m/s]")
    ax = axes[1][1]
    styles = {
        "micro": ("k-", "micro"),
        "flux_cap": ("C0--", "flux_cap"),
        "capacity": ("C1:", "capacity"),
    }
    for key, (style, label) in styles.items():
        ax.plot(SHADOW_OFFSETS_M, shadows[key], style, label=label)
    ax.axvline(0.0, color="grey", lw=0.8)
    ax.set_xlabel("offset from AV [m] (negative = upstream)")
    ax.set_ylabel("mean speed [m/s]")
    fc, cap = rmse_ci["flux_cap"]["speed_rmse_ms"], rmse_ci["capacity"]["speed_rmse_ms"]
    ax.set_title(
        f"upstream-shadow profile (this seed)\nspeed RMSE vs micro, mean over "
        f"{fc['n']} reps: flux_cap {fc['mean']:.2f}, capacity {cap['mean']:.2f} m/s",
        fontsize=10,
    )
    ax.legend()
    fig.suptitle(
        "Moving-bottleneck variants vs micro ground truth — corridor_10km, JAD 5%/100% "
        "(macro tier = screening)",
        fontsize=11,
    )
    fig.tight_layout()
    FIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_PATH, dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=int(os.environ.get("M3_PROCS", "6")))
    args = ap.parse_args()
    t0 = time.perf_counter()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    cfg = load_scenario("corridor_10km").model_copy(deep=True)
    cfg.av.penetration = PENETRATION
    cfg.av.compliance = COMPLIANCE
    cfg.av.controller = CONTROLLER
    cfg = cfg.model_copy(update={"replicates": args.replicates})
    seeds = spawn_seeds(cfg.seed, args.replicates)
    sample_dt = 1.0 / cfg.sim.output_hz
    duration = cfg.sim.duration_s

    print(f"micro JAD arm: {args.replicates} replicates of corridor_10km ...", flush=True)
    t_micro = time.perf_counter()
    paths = run_replicates(cfg, OUT_ROOT / "micro_jad", n_procs=min(args.procs, args.replicates))
    micro_wall = time.perf_counter() - t_micro

    frames = [_micro_frame(p.run_dir) for p in paths]
    metas = [json.loads(p.meta.read_text()) for p in paths]

    # FD for the macro tier: fitted from BASELINE (no-AV) micro runs of the
    # same corridor. The JAD runs themselves are too dampened to populate the
    # congested branch (their whole point is absorbing the jams), so the
    # baseline supplies the congested-branch data; same fleet, same demand.
    chash = config_hash(cfg)
    cfg_fd = cfg.model_copy(deep=True)
    cfg_fd.av.penetration = 0.0
    cfg_fd.av.controller = None
    cfg_fd = cfg_fd.model_copy(update={"replicates": FD_BASELINE_REPLICATES})
    print(f"FD baseline arm: {FD_BASELINE_REPLICATES} no-AV replicates ...", flush=True)
    fd_paths = run_replicates(
        cfg_fd, OUT_ROOT / "micro_baseline_fd", n_procs=min(args.procs, FD_BASELINE_REPLICATES)
    )
    fd_cal = _fit_micro_fd(
        [_micro_frame(p.run_dir) for p in fd_paths],
        sample_dt,
        provenance=(
            f"micro corridor_10km baseline (no AVs) {config_hash(cfg_fd)} "
            f"seeds {spawn_seeds(cfg_fd.seed, FD_BASELINE_REPLICATES)}"
        ),
    )
    fd_path = OUT_ROOT / "fd_corridor10k_micro.json"
    fd_cal.save(fd_path)
    print(
        f"fitted micro FD: v_f {fd_cal.fd.v_f * 3.6:.1f} km/h, w {fd_cal.fd.w * 3.6:.1f} km/h, "
        f"rho_jam {veh_m_to_veh_km(fd_cal.fd.rho_jam):.0f} veh/km",
        flush=True,
    )

    macro_cfg = cfg.model_copy(deep=True)
    macro_cfg.av.penetration = 0.0
    macro_cfg.av.controller = None

    per_rep: list[dict] = []
    fig_payloads: list[dict] = []
    for _i, (_p, meta, df) in enumerate(zip(paths, metas, frames, strict=True)):
        seed = meta["seed"]
        trajs = _extract_av_trajectories(df, meta)
        micro_dens, micro_speed = _micro_fields(df, sample_dt)
        rep: dict = {
            "seed": seed,
            "n_av_trajectories": len(trajs),
            "variants": {},
        }
        macro_speed_fields: dict[str, np.ndarray] = {}
        shadows: dict[str, np.ndarray] = {"micro": _shadow_profile(micro_speed, trajs, duration)}
        for variant in VARIANTS:
            run_dir = run_macro(
                macro_cfg,
                seed,
                OUT_ROOT / f"macro_{variant}",
                fd=fd_cal.fd,
                dx_m=MACRO_DX_M,
                bottleneck_variant=variant,  # type: ignore[arg-type]
                prescribed_avs=trajs,
            )
            m_meta = json.loads((run_dir / "meta.json").read_text())
            m_dens, m_speed = _macro_fields(run_dir, duration)
            macro_speed_fields[variant] = m_speed
            shadows[variant] = _shadow_profile(m_speed, trajs, duration)
            rep["variants"][variant] = {
                "run_dir": str(run_dir),
                "tier": m_meta["tier"],
                "binding_fraction": m_meta["av"]["prescribed"]["binding_fraction"],
                "speed_rmse_ms": _rmse(m_speed, micro_speed),
                "density_rmse_veh_km": veh_m_to_veh_km(_rmse(m_dens, micro_dens)),
                "shadow_rmse_ms": _rmse(shadows[variant], shadows["micro"]),
            }
        a = macro_speed_fields["flux_cap"]
        b = macro_speed_fields["capacity"]
        rep["variants_identical"] = bool(np.array_equal(a, b, equal_nan=True))
        per_rep.append(rep)
        fig_payloads.append(
            {
                "micro_speed": micro_speed,
                "macro_speeds": macro_speed_fields,
                "shadows": shadows,
                "seed": seed,
            }
        )
        print(
            f"  seed {seed}: {len(trajs)} AVs | binding "
            + " ".join(f"{v}={rep['variants'][v]['binding_fraction']:.2f}" for v in VARIANTS)
            + " | speed RMSE "
            + " ".join(f"{v}={rep['variants'][v]['speed_rmse_ms']:.2f}" for v in VARIANTS)
            + ("" if not rep["variants_identical"] else " | IDENTICAL"),
            flush=True,
        )

    rmse_ci = {
        v: {
            "speed_rmse_ms": _ci([r["variants"][v]["speed_rmse_ms"] for r in per_rep]),
            "density_rmse_veh_km": _ci([r["variants"][v]["density_rmse_veh_km"] for r in per_rep]),
            "shadow_rmse_ms": _ci([r["variants"][v]["shadow_rmse_ms"] for r in per_rep]),
            "binding_fraction": _ci([r["variants"][v]["binding_fraction"] for r in per_rep]),
        }
        for v in VARIANTS
    }
    n_identical = sum(1 for r in per_rep if r["variants_identical"])
    speed_deltas = [
        r["variants"]["capacity"]["speed_rmse_ms"] - r["variants"]["flux_cap"]["speed_rmse_ms"]
        for r in per_rep
    ]
    delta_ci = _ci(speed_deltas)

    if n_identical == len(per_rep):
        verdict = (
            "NULL: the constraint still never bound — both variants produced identical "
            "fields in every replicate"
        )
    else:
        winner = "flux_cap" if delta_ci["mean"] > 0 else "capacity"
        sig = delta_ci["lo95"] is not None and (delta_ci["lo95"] > 0 or delta_ci["hi95"] < 0)
        verdict = (
            f"constraint binds (variants differ in {len(per_rep) - n_identical}/"
            f"{len(per_rep)} replicates); {winner} tracks micro better on speed RMSE "
            f"(capacity − flux_cap = {delta_ci['mean']:.3f} m/s, 95% CI "
            f"[{delta_ci['lo95']:.3f}, {delta_ci['hi95']:.3f}]); "
            + ("difference is statistically resolved" if sig else "difference is NOT resolved")
        )
    print("verdict:", verdict, flush=True)

    # Illustrate with the replicate where the micro field is waviest / the
    # variant contrast largest (max flux_cap speed RMSE) — the mildest seed
    # shows nearly uniform fields and hides the mechanism.
    sel = int(np.argmax([r["variants"]["flux_cap"]["speed_rmse_ms"] for r in per_rep]))
    fig_payload = fig_payloads[sel]
    _render_figure(
        fig_payload["micro_speed"],
        fig_payload["macro_speeds"],
        fig_payload["shadows"],
        rmse_ci,
        fig_payload["seed"],
    )
    print(f"figure -> {FIG_PATH}", flush=True)

    results = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": "corridor_10km",
        "arm": "jad_prescribed_vstar",
        "tier_note": "macro rows are tier='screening'; this comparison can never back a "
        "validation report (CLAUDE.md §5.6)",
        "replicates": args.replicates,
        "seeds": seeds,
        "config_hash_micro": chash,
        "config_hash_macro": config_hash(macro_cfg),
        "versions": _versions(),
        "penetration": PENETRATION,
        "compliance": COMPLIANCE,
        "controller": CONTROLLER,
        "fd_artifact": str(fd_path.relative_to(REPO_ROOT)),
        "fd": {
            "v_f_ms": fd_cal.fd.v_f,
            "w_ms": fd_cal.fd.w,
            "rho_jam_veh_m": fd_cal.fd.rho_jam,
            "source": "fitted from micro baseline (no-AV) Edie bins, 3 replicates",
        },
        "comparison_grid": {
            "dt_s": GRID_DT_S,
            "dx_m": GRID_DX_M,
            "t_range_s": [WARMUP_S, duration],
            "x_range_m": [0.0, CORRIDOR_M],
        },
        "shadow_offsets_m": SHADOW_OFFSETS_M.tolist(),
        "per_replicate": per_rep,
        "rmse_ci": rmse_ci,
        "speed_rmse_delta_capacity_minus_flux_cap_ms": delta_ci,
        "n_replicates_variants_identical": n_identical,
        "verdict": verdict,
        "micro_wall_s": round(micro_wall, 1),
        "figure": str(FIG_PATH.relative_to(REPO_ROOT)),
        "notes": [
            "Both variants are driven by the SAME per-replicate complied-AV (t, x, v) "
            "trajectories extracted from the micro run (VStarTrajectory playback), so "
            "the only difference is the constraint form.",
            "binding_fraction = fraction of active AV-steps with v* < V_e(rho) at the "
            "AV's macro cell — the precondition for the flux_cap form to bind.",
            "The FD is fitted from no-AV baseline micro runs of the same corridor "
            "(the JAD arm is too dampened to populate the congested branch); absolute "
            "macro-vs-micro RMSE therefore mostly reflects LWR model form (no emergent "
            "waves), not FD mis-calibration.",
            "The earlier follower_stopper arm (runs/m3_us101 history) was an "
            "identical-variants null: that controller never commands below local "
            "equilibrium speed.",
        ],
    }

    def _json_safe(obj):
        if isinstance(obj, dict):
            return {k: _json_safe(v) for k, v in obj.items()}
        if isinstance(obj, list | tuple):
            return [_json_safe(v) for v in obj]
        if isinstance(obj, float) and not math.isfinite(obj):
            return None
        return obj

    out_path = OUT_ROOT / "results.json"
    out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
    print(f"done in {time.perf_counter() - t0:.1f} s -> {out_path}")


if __name__ == "__main__":
    main()
