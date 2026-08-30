"""M2 task 6 — figures for docs/M2_RESULTS.md (matplotlib Agg).

Every figure is generated from committed artifacts and the processed data
caches so the docs trace to computed outputs:

* ``episode_gap_trace.png`` — a held-out-style example episode: observed gap
  vs the gap re-simulated with the population-MEAN parameters from
  ``artifacts/idm_us101.json`` (plus leader/follower speeds).
* ``fd_scatter_triangle.png`` — the per-lane Edie flow-density observations
  with the fitted triangular FD from ``artifacts/fd_us101.json``.
* ``inflow_steps.png`` — the deduplicated 5-min upstream inflow steps from
  ``artifacts/demand_us101.json``.

Run: ``uv run --no-sync python scripts/make_m2_figures.py``
(after the extract/fit scripts).
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from calibration.idm_fit import gap_rmse, simulate_follower
from flowstate_core.artifacts import DemandProfile, FDCalibration, IDMCalibration
from flowstate_core.units import ms_to_kmh, veh_m_to_veh_km, veh_s_to_veh_h

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import PROCESSED_DIR, REPO_ROOT

FIG_DIR = REPO_ROOT / "docs" / "figures"
EXAMPLE_EPISODE_INDEX_RULE = "longest episode (max duration_s) in the extracted set"


def episode_figure() -> None:
    with open(PROCESSED_DIR / "us101_episodes.pkl", "rb") as f:
        episodes = pickle.load(f)
    cal = IDMCalibration.load(REPO_ROOT / "artifacts" / "idm_us101.json")
    ep = max(episodes, key=lambda e: e.duration_s)
    gap_sim, v_sim = simulate_follower(
        ep.dt, ep.v_leader, float(ep.gap_m[0]), float(ep.v_follower[0]), cal.mean
    )
    rmse = gap_rmse(ep, cal.mean)
    t = ep.t - ep.t[0]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(t, ep.gap_m, label="observed gap", lw=1.2)
    ax1.plot(t, gap_sim, label=f"IDM (population mean), RMSE {rmse:.2f} m", lw=1.2)
    ax1.set_ylabel("bumper-to-bumper gap [m]")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.set_title(
        f"US-101 episode {ep.veh_id} ({ep.duration_s:.0f} s, lane {ep.metadata['lane']}) — "
        "observed vs population-mean re-simulation"
    )
    ax2.plot(t, ep.v_leader, label="leader speed (recorded)", lw=1.0)
    ax2.plot(t, ep.v_follower, label="follower speed (recorded)", lw=1.0)
    ax2.plot(t, v_sim, label="follower speed (simulated)", lw=1.0, ls="--")
    ax2.set_xlabel("time in episode [s]")
    ax2.set_ylabel("speed [m/s]")
    ax2.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "episode_gap_trace.png", dpi=150)
    plt.close(fig)
    print(
        f"episode figure: {ep.veh_id} ({EXAMPLE_EPISODE_INDEX_RULE}), mean-param RMSE {rmse:.2f} m"
    )


def fd_figure() -> None:
    obs = pd.read_parquet(PROCESSED_DIR / "us101_fd_observations.parquet")
    cal = FDCalibration.load(REPO_ROOT / "artifacts" / "fd_us101.json")
    fd = cal.fd
    rho = np.linspace(0.0, fd.rho_jam, 400)
    q = np.array([fd.equilibrium_flow(r) for r in rho])

    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.scatter(
        veh_m_to_veh_km(obs["density_veh_m"].to_numpy()),
        veh_s_to_veh_h(obs["flow_veh_s"].to_numpy()),
        s=4,
        alpha=0.18,
        color="#4878a8",
        label="Edie 30 s × 50 m bins (per lane, lanes 1-5, both periods)",
    )
    ax.plot(veh_m_to_veh_km(rho), veh_s_to_veh_h(q), "r-", lw=2, label="fitted triangular FD")
    lab = (
        f"$v_f$ = {ms_to_kmh(fd.v_f):.1f} km/h, w = {ms_to_kmh(fd.w):.1f} km/h,\n"
        f"$\\rho_c$ = {veh_m_to_veh_km(fd.rho_c):.1f} veh/km, "
        f"$\\rho_{{jam}}$ = {veh_m_to_veh_km(fd.rho_jam):.1f} veh/km, "
        f"$q_{{max}}$ = {veh_s_to_veh_h(fd.q_max):.0f} veh/h"
    )
    ax.annotate(lab, xy=(0.35, 0.9), xycoords="axes fraction", fontsize=8)
    ax.set_xlabel("density [veh/km/lane]")
    ax.set_ylabel("flow [veh/h/lane]")
    ax.set_title("US-101 flow-density scatter and fitted triangular FD (congested site)")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_xlim(0, veh_m_to_veh_km(fd.rho_jam) * 1.05)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fd_scatter_triangle.png", dpi=150)
    plt.close(fig)
    print("fd figure written")


def inflow_figure() -> None:
    profile = DemandProfile.load(REPO_ROOT / "artifacts" / "demand_us101.json")
    steps = list(profile.steps)
    # Close the last step at the recorded span end (last window is partial).
    import json

    summary = json.loads((PROCESSED_DIR / "us101_demand_summary.json").read_text())
    end_s = summary["windows_continuous_dedup"][-1]["t_end_s"]
    ts = [t for t, _ in steps] + [end_s]
    qs = [veh_s_to_veh_h(q) for _, q in steps]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.stairs(qs, ts, fill=True, alpha=0.35, color="#4878a8")
    ax.stairs(qs, ts, color="#2f5f8f", lw=2)
    ax.axvline(952.8, color="gray", ls=":", lw=1)
    ax.annotate(
        "period-1 recording ends",
        xy=(952.8, max(qs) * 0.45),
        fontsize=8,
        rotation=90,
        xytext=(925, max(qs) * 0.18),
    )
    ax.set_xlabel("time since period-1 recording start (07:49:39.7 PDT) [s]")
    ax.set_ylabel("upstream mainline inflow [veh/h, total over 5 lanes]")
    ax.set_title("US-101 upstream boundary inflow, 5-min windows (deduplicated timeline)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "inflow_steps.png", dpi=150)
    plt.close(fig)
    print("inflow figure written")


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    episode_figure()
    fd_figure()
    inflow_figure()


if __name__ == "__main__":
    main()
