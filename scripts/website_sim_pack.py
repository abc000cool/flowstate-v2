"""Data pack for the embeddable ring simulation (``embed/``).

Runs the CI-gated ``ring_sugiyama`` scenario (CLAUDE.md §3.2.1) across a
small grid — vehicles on the ring × controlled vehicles × controller
switch-on time × seed — with Eclipse SUMO + IDM through libsumo, and writes
every run as a compact binary trajectory file plus one JSON index, so a
static web page can replay real engine output frame by frame. No frame is
synthesized: the browser only interpolates linearly between the 0.5 s samples.

Layout (``embed/public/data/``):

* ``index.json`` — grid, per-run provenance (config hash, seed, SUMO
  version), the vehicle order and which indices are controlled, and
  per-minute summary metrics computed here from the same trajectories.
* ``runs/<id>.bin`` — little-endian ``uint16`` ``x`` in decimetres
  (``n_samples × n_vehicles``, row-major, wrapped ring position) followed by
  ``uint16`` ``v`` in cm/s of the same shape.
* ``i24_observed.json`` — the observed I-24 westbound speed field of
  30 Nov 2022, copied from ``artifacts/i24_wb_overview.json``.

Run: ``uv run --no-sync python scripts/website_sim_pack.py [--workers 1]``
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from microsim import load_scenario, run_micro
from microsim.runner import _versions

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "embed" / "public" / "data"
RING_YAML = REPO / "scenarios" / "ring_sugiyama.yaml"
OVERVIEW = REPO / "artifacts" / "i24_wb_overview.json"

N_VEHICLES = (18, 22, 26)
N_AV = (0, 1, 2)
ACTIVATION_S = (0.0, 300.0)
SEEDS = (42, 7, 123)
CONTROLLER = "follower_stopper"
VEHICLE_LENGTH_M = 5.0  # SUMO default passenger length used by the ring fleet
X_SCALE = 10.0  # decimetres
V_SCALE = 100.0  # cm/s
LAST_WINDOW_S = 300.0
STOPPED_MS = 0.5


def grid() -> list[dict[str, Any]]:
    """Enumerate the run grid (activation is meaningless with no AV)."""
    combos: list[dict[str, Any]] = []
    for n in N_VEHICLES:
        for n_av in N_AV:
            acts: tuple[float, ...] = ACTIVATION_S if n_av > 0 else (0.0,)
            for act in acts:
                for seed in SEEDS:
                    combos.append(
                        {"n_vehicles": n, "n_av": n_av, "activation_s": act, "seed": seed}
                    )
    return combos


def run_id(c: dict[str, Any]) -> str:
    return f"n{c['n_vehicles']}_av{c['n_av']}_t{int(c['activation_s'])}_s{c['seed']}"


def _per_minute(df: pd.DataFrame, col: str, fn: str) -> list[float]:
    minute = (df["t"] // 60).astype(int)
    g = df.groupby(minute)[col]
    s = getattr(g, fn)()
    return [round(float(v), 3) for v in s.sort_index().to_numpy()]


def one_run(c: dict[str, Any]) -> dict[str, Any]:
    """Run one grid cell and write its binary; return the index record."""
    cfg = load_scenario(RING_YAML).model_copy(deep=True)
    cfg.network.n_vehicles = c["n_vehicles"]
    if c["n_av"] > 0:
        cfg.av.penetration = c["n_av"] / c["n_vehicles"]
        cfg.av.controller = CONTROLLER
    with tempfile.TemporaryDirectory() as td:
        paths = run_micro(cfg, c["seed"], Path(td), controller_start_s=c["activation_s"])
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v", "is_av"])
    ids = sorted(df["veh_id"].unique())
    t_grid = np.sort(df["t"].unique())
    piv_x = df.pivot(index="t", columns="veh_id", values="x").reindex(index=t_grid, columns=ids)
    piv_v = df.pivot(index="t", columns="veh_id", values="v").reindex(index=t_grid, columns=ids)
    if piv_x.isna().any().any() or piv_v.isna().any().any():
        raise RuntimeError(f"{run_id(c)}: missing samples on a closed ring")
    x = np.rint(piv_x.to_numpy() * X_SCALE)
    v = np.rint(np.clip(piv_v.to_numpy(), 0.0, None) * V_SCALE)
    if x.max() >= 65536 or v.max() >= 65536:
        raise RuntimeError(f"{run_id(c)}: value outside uint16 range")
    blob = np.concatenate([x.astype("<u2").ravel(), v.astype("<u2").ravel()]).tobytes()
    (OUT / "runs" / f"{run_id(c)}.bin").write_bytes(blob)
    av_index = [i for i, vid in enumerate(ids) if bool(df.loc[df["veh_id"] == vid, "is_av"].any())]
    last = df[df["t"] >= float(t_grid[-1]) - LAST_WINDOW_S]
    dt = float(np.median(np.diff(t_grid))) if len(t_grid) > 1 else math.nan
    return {
        "id": run_id(c),
        **c,
        "controller": CONTROLLER if c["n_av"] > 0 else None,
        "config_hash": meta["config_hash"],
        "file": f"runs/{run_id(c)}.bin",
        "t0_s": float(t_grid[0]),
        "dt_s": dt,
        "n_samples": len(t_grid),
        "av_index": av_index,
        "sigma_v_per_minute_ms": _per_minute(df, "v", "std"),
        "mean_v_per_minute_ms": _per_minute(df, "v", "mean"),
        "min_v_per_minute_ms": _per_minute(df, "v", "min"),
        "last300": {
            "sigma_v_ms": round(float(last["v"].std()), 3),
            "mean_v_ms": round(float(last["v"].mean()), 3),
            "stopped_fraction": round(float((last["v"] < STOPPED_MS).mean()), 4),
        },
    }


def write_observed() -> dict[str, Any]:
    ov = json.loads(OVERVIEW.read_text())
    f = ov["field"]
    rows = [
        [
            None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, 1)
            for v in row
        ]
        for row in f["mean_speed_kmh"]
    ]
    payload = {
        "source": "I-24 MOTION INCEPTION v1.x, westbound, 30 Nov 2022 (artifacts/i24_wb_overview.json)",
        "data_hash": ov["data_hash"],
        "t_origin_unix": ov["t_origin_unix"],
        "dt_s": f["dt_s"],
        "dx_m": f["dx_m"],
        "t_edges_s": f["t_edges_s"],
        "x_edges_m": f["x_edges_m"],
        "mean_speed_kmh": rows,
        "coverage_note": "Fragmented tracking covers ~0.5-0.65 of vehicle-time at the peak; "
        "speeds are robust, counts are lower bounds (docs/I24_DATA.md).",
    }
    (OUT / "i24_observed.json").write_text(json.dumps(payload, separators=(",", ":")))
    return {"file": "i24_observed.json", "n_t": len(rows), "n_x": len(rows[0]) if rows else 0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--workers", type=int, default=1, help="process pool size (keep small)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "runs").mkdir(exist_ok=True)
    combos = grid()
    print(f"{len(combos)} ring runs -> {OUT}")
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            records = list(ex.map(one_run, combos))
    else:
        records = []
        for i, c in enumerate(combos, 1):
            rec = one_run(c)
            records.append(rec)
            print(
                f"  [{i}/{len(combos)}] {rec['id']} sigma_v(last 5 min)={rec['last300']['sigma_v_ms']:.2f} m/s"
            )
    base = load_scenario(RING_YAML)
    index = {
        "schema": "flowstate-embed-data/1",
        "generated_by": "scripts/website_sim_pack.py",
        "engine": {
            "sumo": _versions().get("eclipse-sumo"),
            "model": base.fleet.model,
            "step_s": base.sim.step_length_s,
        },
        "scenario": {
            "name": base.name,
            "yaml": "scenarios/ring_sugiyama.yaml",
            "circumference_m": base.network.circumference_m,
            "vehicle_length_m": VEHICLE_LENGTH_M,
            "duration_s": base.sim.duration_s,
            "output_hz": base.sim.output_hz,
            "idm": {
                "v0": base.fleet.v0,
                "T": base.fleet.T,
                "a_max": base.fleet.a_max,
                "b": base.fleet.b,
                "s0": base.fleet.s0,
                "heterogeneity_frac": base.fleet.heterogeneity_frac,
            },
            "controller": CONTROLLER,
            "seeded_perturbation": False,
        },
        "encoding": {
            "x_unit": "0.1 m (uint16, little-endian)",
            "v_unit": "0.01 m/s (uint16, little-endian)",
            "layout": "x[n_samples][n_vehicles] then v[n_samples][n_vehicles]",
        },
        "grid": {
            "n_vehicles": list(N_VEHICLES),
            "n_av": list(N_AV),
            "activation_s": list(ACTIVATION_S),
            "seeds": list(SEEDS),
        },
        "runs": records,
        "observed": write_observed(),
    }
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    total = sum(p.stat().st_size for p in (OUT / "runs").glob("*.bin"))
    print(f"-> {OUT / 'index.json'}; {len(records)} runs, {total / 1e6:.1f} MB of trajectories")


if __name__ == "__main__":
    main()
