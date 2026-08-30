"""M2 task 3 — triangular fundamental diagram from NGSIM US-101 trajectories.

Builds per-lane flow-density observations with Edie's generalized definitions
(``validation.fields.density_field`` / ``flow_field``): the trajectory field
of each (recording period, mainline lane 1-5) is binned into 30 s x 50 m
space-time cells at the native 0.1 s sampling interval; density = vehicle-time
in the cell / cell area, flow = vehicle-distance / cell area. Binning each
lane separately yields per-lane quantities directly. Trailing partial bins in
t and x are dropped (their fixed-area normalization would dilute them);
zero-density (empty) bins carry no FD information and are excluded. All
vehicle classes are kept — the stream's FD includes trucks and motorcycles.

The fit is the package's ``fit_triangular_fd``: free branch through the
origin on bins with density <= 20 veh/km/lane (explicit cut, chosen from the
scatter — only ~4% of bins lie below it because the site is congested for
the whole recording), capacity = 95th-percentile flow, congested branch by
tau=0.9 quantile regression, 200 seeded bootstrap resamples for 95% CIs.

Outputs:
* ``data/processed/us101_fd_observations.parquet`` — the Edie observations
  (for the docs figure).
* ``artifacts/fd_us101.json`` — the FDCalibration artifact.

Run: ``uv run --no-sync python scripts/fit_fd_us101.py``
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from calibration.fd_fit import fit_triangular_fd
from flowstate_core.units import ms_to_kmh, veh_m_to_veh_km, veh_s_to_veh_h
from validation.fields import density_field, flow_field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import MAINLINE_LANES, PROCESSED_DIR, REPO_ROOT, data_hash, load_us101

DT_BIN_S = 30.0
DX_BIN_M = 50.0
SAMPLE_DT_S = 0.1
UNCONGESTED_MAX_DENSITY_VEH_M = 0.020  # 20 veh/km/lane, explicit free-branch cut
N_BOOTSTRAP = 200
SEED = 42

NOTES = (
    "Edie generalized flow/density from raw NGSIM US-101 trajectories, 30 s x 50 m bins "
    "per (recording period, mainline lane 1-5); per-lane quantities. The site is heavily "
    "congested for the entire recording (07:50-08:12 PDT): only ~4% of bins fall below "
    "the 20 veh/km free-branch cut and the fastest observed bin speeds are ~65-70 km/h, "
    "so the free-flow branch is data-poor and v_f reflects the fastest OBSERVED operation, "
    "not the facility's true free-flow speed — expect v_f to be biased low with wide CIs. "
    "The congested branch (w, rho_jam) is well populated. Trailing partial bins dropped; "
    "empty bins excluded."
)


def build_observations() -> pd.DataFrame:
    """Per-lane Edie flow-density observations over both recording periods."""
    periods = load_us101()
    rows = []
    for label, df in periods.items():
        for lane in range(MAINLINE_LANES[0], MAINLINE_LANES[1] + 1):
            sub = df.loc[df["lane"] == lane, ["t", "x", "v", "veh_id"]]
            if sub.empty:
                continue
            dens = density_field(sub, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M, sample_dt=SAMPLE_DT_S)
            flow = flow_field(sub, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M, sample_dt=SAMPLE_DT_S)
            rows.append(
                pd.DataFrame(
                    {
                        "density_veh_m": dens.density[:-1, :-1].ravel(),
                        "flow_veh_s": flow.flow[:-1, :-1].ravel(),
                        "period": label,
                        "lane": lane,
                    }
                )
            )
    obs = pd.concat(rows, ignore_index=True)
    return obs.loc[obs["density_veh_m"] > 0.0].reset_index(drop=True)


def main() -> None:
    obs = build_observations()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(PROCESSED_DIR / "us101_fd_observations.parquet", index=False)
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    cal = fit_triangular_fd(
        obs[["density_veh_m", "flow_veh_s"]],
        created_at=created_at,
        source=(
            "NGSIM US-101 raw trajectories (data.transportation.gov 8ect-6jqj), Edie "
            "30 s x 50 m bins per lane, mainline lanes 1-5, periods 1 + partial 2 "
            "(2005-06-15 07:50-08:12 PDT); per-lane quantities"
        ),
        data_hash=data_hash(),
        uncongested_max_density=UNCONGESTED_MAX_DENSITY_VEH_M,
        n_bootstrap=N_BOOTSTRAP,
        seed=SEED,
        notes=NOTES,
    )
    out = REPO_ROOT / "artifacts" / "fd_us101.json"
    cal.save(out)
    fd = cal.fd
    print(f"{len(obs)} observations -> {out}")
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
