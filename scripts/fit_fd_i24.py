"""ROADMAP §1.2 task 3 — triangular fundamental diagram from I-24 MOTION.

Per-lane Edie flow-density observations (``validation.fields.density_field``
/ ``flow_field``) on 30 s × 50 m space-time cells over the westbound
mainline (lanes 1-4, 0-6437 m, 06:00-10:00 CST) at the 5 Hz sample weight
(0.2 s), then the package's ``fit_triangular_fd`` (free branch through the
origin on bins ≤ 20 veh/km/lane, capacity = 95th-percentile flow, congested
branch by τ = 0.9 quantile regression, 200 seeded bootstrap resamples).

**Coverage caveat, stated in the artifact notes.** The trajectories are
fragments with incomplete, locally variable tracking coverage. Edie's
vehicle-time (density) and vehicle-distance (flow) both scale with the
fraction of vehicles tracked, so a uniform coverage loss leaves the two
branch *slopes* (v_f and the wave speed w) unchanged while scaling q_max and
ρ_jam down by the same factor; duplicate fragments push the other way. The
slopes are the trustworthy outputs; q_max and ρ_jam are lower bounds at the
instrument's coverage, not facility capacity.

Outputs:
* ``data/i24motion/processed/i24_wb_fd_observations.parquet``
* ``artifacts/fd_i24.json``

Run: ``uv run --no-sync python scripts/fit_fd_i24.py``
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from calibration.fd_fit import fit_triangular_fd
from calibration.loaders.i24motion import I24_CITATION, load_i24_parquet
from flowstate_core.units import ms_to_kmh, veh_m_to_veh_km, veh_s_to_veh_h
from validation.fields import density_field, flow_field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    MAINLINE_LANES,
    PROCESSED_DIR,
    REPO_ROOT,
    SAMPLE_DT_S,
    TESTBED_LENGTH_M,
    WB_DIR,
    data_hash,
)

DT_BIN_S = 30.0
DX_BIN_M = 50.0
UNCONGESTED_MAX_DENSITY_VEH_M = 0.020  # 20 veh/km/lane, explicit free-branch cut
N_BOOTSTRAP = 200
SEED = 42
#: Bootstrap refits in parallel. Each exact-LP refit costs ~2 min and ~3 GB on
#: 237k bins; one worker is what fits beside a running sweep on a 16 GB machine.
N_PROCS = 1

NOTES = (
    "Edie generalized flow/density from I-24 MOTION INCEPTION v1.x westbound fragments "
    "(30 Nov 2022 06:00-10:00 CST), 30 s x 50 m bins per mainline lane 1-4 over MM 62.7 -> "
    "58.7; per-lane quantities; 5 Hz samples weighted 0.2 s; trailing partial bins dropped, "
    "empty bins excluded, all vehicle classes kept. COVERAGE: trajectories are unstitched "
    "fragments with incomplete tracking, so vehicle-time and vehicle-distance are both "
    "undercounted by the local coverage fraction (duplicate fragments overcount). The branch "
    "slopes v_f and w are invariant to a uniform coverage factor; q_max and rho_jam are "
    "lower bounds at the instrument's coverage and must not be quoted as facility capacity "
    "or jam density. The day spans genuine free flow (06:00-06:45) and heavy congestion, so "
    "unlike the US-101 fit the free branch is populated. Cite " + I24_CITATION
)


def build_observations() -> pd.DataFrame:
    """Per-lane Edie flow-density observations over the whole run."""
    rows = []
    for lane in range(MAINLINE_LANES[0], MAINLINE_LANES[1] + 1):
        sub = load_i24_parquet(
            WB_DIR,
            lanes=(lane, lane),
            x_range_m=(0.0, TESTBED_LENGTH_M),
            columns=["t", "x", "v", "veh_id"],
        )
        if sub.empty:
            continue
        dens = density_field(sub, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M, sample_dt=SAMPLE_DT_S)
        flow = flow_field(sub, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M, sample_dt=SAMPLE_DT_S)
        rows.append(
            pd.DataFrame(
                {
                    "density_veh_m": dens.density[:-1, :-1].ravel(),
                    "flow_veh_s": flow.flow[:-1, :-1].ravel(),
                    "lane": lane,
                }
            )
        )
        print(f"lane {lane}: {len(sub)} rows -> {rows[-1].shape[0]} bins", flush=True)
        del sub
    obs = pd.concat(rows, ignore_index=True)
    return obs.loc[obs["density_veh_m"] > 0.0].reset_index(drop=True)


def main() -> None:
    t0 = time.perf_counter()
    obs = build_observations()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(PROCESSED_DIR / "i24_wb_fd_observations.parquet", index=False)
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    cal = fit_triangular_fd(
        obs[["density_veh_m", "flow_veh_s"]],
        created_at=created_at,
        source=(
            "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound "
            "(6386d89efb3ff533c12df167__post10), Edie 30 s x 50 m bins per mainline lane "
            "1-4, MM 62.7-58.7, 06:00-10:00 CST; per-lane quantities"
        ),
        data_hash=data_hash(),
        uncongested_max_density=UNCONGESTED_MAX_DENSITY_VEH_M,
        n_bootstrap=N_BOOTSTRAP,
        seed=SEED,
        notes=NOTES,
        n_procs=N_PROCS,
    )
    out = REPO_ROOT / "artifacts" / "fd_i24.json"
    cal.save(out)
    fd = cal.fd
    print(f"{len(obs)} observations -> {out} ({time.perf_counter() - t0:.0f} s)")
    print(f"v_f     = {ms_to_kmh(fd.v_f):6.1f} km/h   ci95={_ci_kmh(fd, 'v_f')}")
    print(f"w       = {ms_to_kmh(fd.w):6.1f} km/h   ci95={_ci_kmh(fd, 'w')}")
    print(f"rho_jam = {veh_m_to_veh_km(fd.rho_jam):6.1f} veh/km ci95={_ci_veh_km(fd, 'rho_jam')}")
    print(f"rho_c   = {veh_m_to_veh_km(fd.rho_c):6.1f} veh/km ci95={_ci_veh_km(fd, 'rho_c')}")
    print(f"q_max   = {veh_s_to_veh_h(fd.q_max):6.0f} veh/h  ci95={_ci_veh_h(fd, 'q_max')}")
    print(f"r2_freeflow = {cal.r2_freeflow:.3f}")


def _ci_kmh(fd, key):
    lo, hi = fd.ci95.get(key, (float("nan"),) * 2)
    return f"({ms_to_kmh(lo):.1f}, {ms_to_kmh(hi):.1f}) km/h"


def _ci_veh_km(fd, key):
    lo, hi = fd.ci95.get(key, (float("nan"),) * 2)
    return f"({veh_m_to_veh_km(lo):.1f}, {veh_m_to_veh_km(hi):.1f}) veh/km"


def _ci_veh_h(fd, key):
    lo, hi = fd.ci95.get(key, (float("nan"),) * 2)
    return f"({veh_s_to_veh_h(lo):.0f}, {veh_s_to_veh_h(hi):.0f}) veh/h"


if __name__ == "__main__":
    main()
