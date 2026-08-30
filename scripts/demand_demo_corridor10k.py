"""M2 task 4b — demand-fitter convergence DEMONSTRATION on corridor_10km.

This is a *fitter demonstration*, not an observation: the "observed" link
counts are synthesized by running the MACRO screening tier (``run_macro``,
v1_legacy FD preset — deterministic, ~1000x faster than SUMO) with a KNOWN
truth inflow profile (the corridor_10km scenario's own inflow). The fitter
(``calibration.demand.fit_inflow``) then starts from a deliberately wrong
flat profile and must recover the truth via iterative proportional scaling
against the GEH criterion, with the same macro tier injected as
``simulate_fn``. The saved artifact is labeled accordingly and must never be
used as demand for a validation run.

Link counts are read at the cross-section x ≈ 500 m from ``edges.parquet``
(cell flow averaged per 300 s bin).

Output: ``artifacts/demand_corridor10k_demo.json``.
Run: ``uv run --no-sync python scripts/demand_demo_corridor10k.py``
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from calibration.demand import fit_inflow, geh
from flowstate_core.artifacts import DemandProfile
from flowstate_core.units import veh_s_to_veh_h
from macrosim.runner import run_macro
from microsim.scenarios import load_scenario

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import REPO_ROOT

SEED = 42
BIN_S = 300.0
X_REF_M = 500.0
TRUTH_STEPS = None  # filled from the scenario below
INITIAL_FLAT_VEH_S = 0.25


def _make_simulate_fn(cfg, workdir: Path):
    """Injected macro-tier simulator: profile → binned link flows at X_REF_M."""
    counter = {"n": 0}

    def simulate(profile: DemandProfile) -> pd.DataFrame:
        counter["n"] += 1
        run_cfg = cfg.model_copy(deep=True)
        run_cfg.network.inflow = [(float(t), float(q)) for t, q in profile.steps]
        run_dir = run_macro(run_cfg, SEED, workdir / f"iter{counter['n']:03d}")
        edges = pd.read_parquet(run_dir / "edges.parquet")
        x_cells = edges["x_bin"].unique()
        x_ref = x_cells[abs(x_cells - X_REF_M).argmin()]
        at_ref = edges.loc[edges["x_bin"] == x_ref].copy()
        at_ref["t_start_s"] = (at_ref["t_bin"] // BIN_S) * BIN_S
        binned = at_ref.groupby("t_start_s", as_index=False)["flow"].mean()
        binned = binned.loc[binned["t_start_s"] < cfg.sim.duration_s]
        return pd.DataFrame(
            {
                "t_start_s": binned["t_start_s"],
                "t_end_s": binned["t_start_s"] + BIN_S,
                "flow_veh_s": binned["flow"],
            }
        )

    return simulate, counter


def main() -> None:
    cfg = load_scenario("corridor_10km")
    cfg.tier = "macro"
    truth_steps = [(float(t), float(q)) for t, q in cfg.network.inflow]
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="demand_demo_") as tmp:
        simulate, counter = _make_simulate_fn(cfg, Path(tmp))
        truth_profile = DemandProfile(
            created_at=created_at, source="truth", data_hash="", steps=truth_steps
        )
        observed = simulate(truth_profile)
        initial = DemandProfile(
            created_at=created_at,
            source="deliberately wrong flat start",
            data_hash="",
            steps=[(t, INITIAL_FLAT_VEH_S) for t, _ in truth_steps],
        )
        fitted = fit_inflow(
            observed,
            initial,
            simulate,
            created_at=created_at,
            source=(
                "FITTER DEMONSTRATION (M2): corridor_10km on the MACRO screening tier "
                "(v1_legacy FD), synthetic observed counts generated from the scenario's "
                "own known inflow; fitter started from a flat 0.25 veh/s profile. NOT an "
                "observation of any real corridor — do not use as validation demand."
            ),
            data_hash="synthetic-macro-truth",
            geh_threshold=5.0,
            geh_pass_frac=0.85,
            max_iters=25,
        )
        sim_fitted = simulate(fitted)

    gehs = [
        geh(veh_s_to_veh_h(m), veh_s_to_veh_h(c))
        for m, c in zip(sim_fitted["flow_veh_s"], observed["flow_veh_s"], strict=True)
    ]
    out = REPO_ROOT / "artifacts" / "demand_corridor10k_demo.json"
    fitted.save(out)
    print(f"macro simulate_fn calls: {counter['n']} -> {out}")
    print(f"worst-bin GEH of returned profile: {fitted.geh_vs_counts:.3f}")
    print(f"per-bin GEH: {[round(g, 3) for g in gehs]}")
    print("step      truth   initial  fitted  (veh/s)")
    for (t, q_true), (_, q_fit) in zip(truth_steps, fitted.steps, strict=True):
        print(f"t={t:7.1f}  {q_true:.3f}   {INITIAL_FLAT_VEH_S:.3f}    {q_fit:.3f}")


if __name__ == "__main__":
    main()
