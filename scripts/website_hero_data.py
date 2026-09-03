"""Data pack for the FlowState website hero animation (docs/WEBSITE_BRIEF.md).

Writes ``docs/website/hero_data.json`` from real, reproducible sources:

* ``observed``: the I-24 MOTION westbound space-time mean-speed field of
  30 Nov 2022 (60 s × 100 m bins) copied verbatim from
  ``artifacts/i24_wb_overview.json`` (``scripts/i24_overview.py``).
* ``ring``: three seeded ``ring_sugiyama`` runs (CLAUDE.md §3.2.1) at 2 Hz —
  the uncontrolled baseline, one FollowerStopper vehicle active from the start,
  and the hero run in which the same vehicle is switched on at
  ``ACTIVATE_S`` after the stop-and-go wave has emerged (Stern et al. 2018
  shape). Positions are wrapped ring coordinates in metres; speeds in m/s.

Nothing here is hand-drawn or tuned for the website: the ring runs use the
committed scenario unchanged, and every block carries its config hash, seed
and source data hash.

Run: ``uv run --no-sync python scripts/website_hero_data.py``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from microsim import load_scenario, run_micro

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "website" / "hero_data.json"
OVERVIEW = REPO / "artifacts" / "i24_wb_overview.json"
RING_YAML = REPO / "scenarios" / "ring_sugiyama.yaml"
ACTIVATE_S = 300.0  # controller switch-on time in the hero run
PENETRATION = (
    0.045  # round(0.045 · 22) = 1 AV, as in tests/test_microsim/test_microsim_ring_gate.py
)


def _ring_run(name: str, *, controlled: bool, start_s: float, tmp: Path) -> dict[str, Any]:
    cfg = load_scenario(RING_YAML)
    if controlled:
        cfg = cfg.model_copy(deep=True)
        cfg.av.penetration = PENETRATION
        cfg.av.controller = "follower_stopper"
    paths = run_micro(cfg, cfg.seed, tmp / name, controller_start_s=start_s)
    meta = json.loads(paths.meta.read_text())
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "v", "is_av"])
    df = df.sort_values(["veh_id", "t"])
    t_grid = np.sort(df["t"].unique())
    vehicles = []
    for vid, g in df.groupby("veh_id", sort=True):
        g = g.set_index("t").reindex(t_grid)
        vehicles.append(
            {
                "id": vid,
                "is_av": bool(g["is_av"].fillna(False).any()),
                "x_m": [round(float(v), 2) for v in g["x"].to_numpy()],
                "v_ms": [round(float(v), 3) for v in g["v"].to_numpy()],
            }
        )
    # Per-minute speed standard deviation across the fleet, for captions.
    minute = (df["t"] // 60).astype(int)
    sigma = df.groupby(minute)["v"].std().round(3)
    return {
        "name": name,
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "controller": meta.get("controller"),
        "controller_start_s": start_s if controlled else None,
        "circumference_m": cfg.network.circumference_m,
        "n_vehicles": cfg.network.n_vehicles,
        "output_hz": cfg.sim.output_hz,
        "t_s": [round(float(t), 2) for t in t_grid],
        "vehicles": vehicles,
        "sigma_v_per_minute_ms": {str(int(k)): float(v) for k, v in sigma.items()},
    }


def main() -> None:
    ov = json.loads(OVERVIEW.read_text())
    field = ov["field"]
    observed = {
        "source": "I-24 MOTION INCEPTION v1.x, westbound, 30 Nov 2022 (artifacts/i24_wb_overview.json)",
        "data_hash": ov["data_hash"],
        "t_origin_unix": ov["t_origin_unix"],
        "dt_s": field["dt_s"],
        "dx_m": field["dx_m"],
        "t_edges_s": field["t_edges_s"],
        "x_edges_m": field["x_edges_m"],
        "mean_speed_kmh": [
            [
                None if v is None or (isinstance(v, float) and np.isnan(v)) else round(v, 1)
                for v in row
            ]
            for row in field["mean_speed_kmh"]
        ],
        "note": "x increases in the direction of travel from the upstream end of the "
        "instrumented testbed; null = no tracked vehicle in the bin. Tracking "
        "coverage is ~0.5-0.65 of vehicle-time at the peak (docs/I24_DATA.md).",
    }
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ring = [
            _ring_run("baseline", controlled=False, start_s=0.0, tmp=tmp),
            _ring_run("follower_stopper_from_start", controlled=True, start_s=0.0, tmp=tmp),
            _ring_run("follower_stopper_activated", controlled=True, start_s=ACTIVATE_S, tmp=tmp),
        ]
    pack = {
        "schema": "flowstate-website-hero-data/1",
        "observed": observed,
        "ring": ring,
        "generator": "scripts/website_hero_data.py",
    }
    OUT.write_text(json.dumps(pack, separators=(",", ":")))
    for r in ring:
        s = r["sigma_v_per_minute_ms"]
        print(
            f"{r['name']:<30} hash={r['config_hash']} sigma_v/min:",
            " ".join(f"{s[k]:.2f}" for k in sorted(s, key=int)),
        )
    print(f"-> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
