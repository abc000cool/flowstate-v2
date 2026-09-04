"""Fit one demand level on observed segment speeds (FHWA Vol. III step 2), any corridor.

Corridor-agnostic form of ``scripts/i24_fit_demand_scale.py``, whose
procedure is documented in ``docs/I24_CAPACITY.md`` §5. The I-24 script
stays as the record of that run; this one takes the scenario, the observed
table, the comparison geometry and the fit/holdout split as arguments.

The procedure. With the capacity-calibrated population (step 1,
``scripts/calibrate_capacity.py``) in place, a single scale ``s`` applied to
the scenario's mainline inflow and on-ramp inflows (exit fractions and any
measured boundary unchanged) is chosen to minimise the segment-speed RMSPE
over the FIT windows — by default the first half of the observed windows —
with the remaining windows held out and reported as an out-of-sample check.
Two rounds of a parallel grid (coarse, then refined around the best), ties
broken toward the smaller scale, one seeded replicate per grid point unless
``--seeds-per-point`` says otherwise. Nothing else is tuned.

Inputs:

* ``--scenario``: the replica YAML to scale (a corridor or OSM network; an
  embedded ``network.boundary`` is kept as is).
* ``--observed``: a JSON file holding the observed segment-speed table under
  ``--observed-key`` (shape ``[n_windows, n_segments]``, m/s, NaN allowed);
  its ``data_hash`` is recorded when present.
* the sim-to-observed mapping: observed ``t = sim t − warmup_s`` and observed
  ``x = (sim x − x_offset_m) / x_scale``; segments tile ``[0, span_m)`` in
  ``n_segments`` bins and windows tile ``[0, n_windows × window_s)``.

Outputs the fit artifact (grid, per-run tables, best level, the observed
table it was fitted to) and, with ``--write-scenario``, a scenario YAML at
the fitted level.

Run (US-101, observed table and boundary base from
``scripts/m3_us101_validate.py``)::

    uv run --no-sync python scripts/fit_demand_scale.py \\
        --scenario runs/m3_us101/us101_replica_with_boundary.yaml \\
        --fleet-artifact artifacts/idm_us101_capacity.json \\
        --observed runs/m3_us101/observed_us101.json --observed-key segment_speeds_fine_ms \\
        --window-s 150 --span-m 640 --n-segments 4 --x-offset-m 640 \\
        --out artifacts/demand_scale_us101.json \\
        --write-scenario scenarios/us101_replica_calibrated.yaml --name us101_replica_calibrated

The I-24 corrected-arm run corresponds to ``--scenario
scenarios/i24_replica_corrected.yaml --fleet-artifact
artifacts/idm_i24_capacity.json --observed artifacts/i24_validation_observed.json
--window-s 300 --span-m <span> --n-segments 10 --x-offset-m <a> --x-scale <b>
--grid 0.6 0.7 0.8 0.9 1.0 1.1`` (geometry from
``artifacts/i24_replica_inputs.json``); the default split is its windows
0-11 / 12-23.
"""

from __future__ import annotations

import argparse
import copy
import json
import multiprocessing as mp
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import rmspe

REPO = Path(__file__).resolve().parents[1]

DEFAULT_GRID: tuple[float, ...] = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3)
"""Coarse grid of demand levels (the I-24 corrected-arm grid, extended to 1.3
for corridors whose counts are complete and need no coverage correction)."""
REFINE_STEP = 0.025
REFINE_HALF_WIDTH = 3
"""± steps around the coarse optimum in the refinement round (I-24 values)."""


def scale_inflows(raw: dict[str, Any], scale: float) -> dict[str, Any]:
    """The scenario dict with mainline and on-ramp inflows multiplied by ``scale``.

    Exit fractions, off-ramps, the boundary and everything else are untouched;
    inflows are rounded to 6 decimals as in the I-24 script. The input is not
    modified.

    Args:
        raw: Scenario mapping as loaded from YAML.
        scale: Demand level ``s`` (> 0).

    Returns:
        A deep copy with the scaled inflows.
    """
    if scale <= 0.0:
        raise ValueError("scale must be > 0")
    out = copy.deepcopy(raw)
    net = out["network"]
    net["inflow"] = [[t, round(q * scale, 6)] for t, q in net["inflow"]]
    for ramp in net.get("ramps", []) or []:
        if ramp.get("kind") == "on" and ramp.get("inflow"):
            ramp["inflow"] = [[t, round(q * scale, 6)] for t, q in ramp["inflow"]]
    return out


def parse_windows(spec: str) -> list[int]:
    """Window indices from ``"0-2"``, ``"0,1,2"`` or a mix (``"0-2,5"``), sorted, unique."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                raise ValueError(f"bad window range {part!r}")
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    if not out or min(out) < 0:
        raise ValueError(f"no valid window indices in {spec!r}")
    return sorted(out)


def default_split(n_windows: int) -> tuple[list[int], list[int]]:
    """The procedure's default split: first half fitted, second half held out."""
    if n_windows < 2:
        raise ValueError("need at least two windows to split")
    half = n_windows // 2
    return list(range(0, half)), list(range(half, n_windows))


def segment_speeds(
    t: np.ndarray,
    x: np.ndarray,
    v: np.ndarray,
    *,
    window_s: float,
    n_windows: int,
    span_m: float,
    n_segments: int,
) -> np.ndarray:
    """Mean sampled speed per (window, segment); NaN where a bin is empty.

    Arithmetic mean of the uniformly sampled speeds inside each
    ``window_s × (span_m / n_segments)`` bin — the same operation the
    validation drivers apply to the observed and simulated sides
    (``scripts/m3_us101_validate.py``, ``scripts/i24_validate.py``).

    Args:
        t: Sample times in observed coordinates [s].
        x: Sample positions in observed coordinates [m].
        v: Sample speeds [m/s].
        window_s: Window length [s].
        n_windows: Number of windows from ``t = 0``.
        span_m: Length of the measured span from ``x = 0`` [m].
        n_segments: Number of equal segments tiling the span.

    Returns:
        Array of shape ``[n_windows, n_segments]``.
    """
    seg_m = span_m / n_segments
    out = np.full((n_windows, n_segments), np.nan)
    t = np.asarray(t, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    ok = (t >= 0.0) & (t < n_windows * window_s) & (x >= 0.0) & (x < span_m)
    wi = (t[ok] // window_s).astype(np.int64)
    si = np.minimum((x[ok] // seg_m).astype(np.int64), n_segments - 1)
    sums = np.zeros((n_windows, n_segments))
    cnts = np.zeros((n_windows, n_segments))
    np.add.at(sums, (wi, si), v[ok])
    np.add.at(cnts, (wi, si), 1.0)
    np.divide(sums, cnts, out=out, where=cnts > 0)
    return out


def rmspe_windows(sim: np.ndarray, obs: np.ndarray, windows: list[int] | range) -> float:
    """Segment-speed RMSPE over the given windows, skipping empty/zero observed bins."""
    s = np.asarray(sim, dtype=np.float64)[list(windows)].ravel()
    o = np.asarray(obs, dtype=np.float64)[list(windows)].ravel()
    ok = np.isfinite(s) & np.isfinite(o) & (o != 0.0)
    if not ok.any():
        return float("nan")
    return float(rmspe(s[ok], o[ok]))


def refine_grid(
    centre: float, step: float = REFINE_STEP, half_width: int = REFINE_HALF_WIDTH
) -> list[float]:
    """Refinement levels around ``centre`` (excluded), positive only, rounded to 3 decimals."""
    return [
        round(centre + k * step, 3)
        for k in range(-half_width, half_width + 1)
        if k != 0 and centre + k * step > 0.0
    ]


def best_row(table: list[dict[str, Any]]) -> dict[str, Any]:
    """The grid point with the smallest fit RMSPE; ties go to the smaller scale."""
    finite = [r for r in table if np.isfinite(r["rmspe_train"])]
    if not finite:
        raise ValueError("no grid point has a finite fit RMSPE")
    return min(finite, key=lambda r: (r["rmspe_train"], r["scale"]))


def sim_frame(
    trajectories: Path, *, warmup_s: float, x_offset_m: float, x_scale: float
) -> pd.DataFrame:
    """One replicate's trajectories mapped into observed coordinates."""
    df = pd.read_parquet(trajectories, columns=["t", "x", "v"])
    df["t"] = df["t"] - warmup_s
    df["x"] = (df["x"] - x_offset_m) / x_scale
    return df[df["t"] >= 0.0]


def _job(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one (scale, seed) grid point and score it against the observed table."""
    raw = scale_inflows(payload["raw"], payload["scale"])
    cfg = ScenarioConfig.model_validate(raw)
    obs = np.asarray(payload["observed"], dtype=np.float64)
    geo = payload["geometry"]
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, payload["seed"], Path(td))
        meta = json.loads(paths.meta.read_text())
        df = sim_frame(
            paths.trajectories,
            warmup_s=geo["warmup_s"],
            x_offset_m=geo["x_offset_m"],
            x_scale=geo["x_scale"],
        )
        seg = segment_speeds(
            df["t"].to_numpy(),
            df["x"].to_numpy(),
            df["v"].to_numpy(),
            window_s=geo["window_s"],
            n_windows=geo["n_windows"],
            span_m=geo["span_m"],
            n_segments=geo["n_segments"],
        )
        wall = time.perf_counter() - t0
    return {
        "scale": payload["scale"],
        "seed": payload["seed"],
        "config_hash": meta["config_hash"],
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "rmspe_train": round(rmspe_windows(seg, obs, payload["fit_windows"]), 4),
        "rmspe_test": round(rmspe_windows(seg, obs, payload["holdout_windows"]), 4),
        "rmspe_all": round(rmspe_windows(seg, obs, range(obs.shape[0])), 4),
        "segment_speeds_ms": np.round(seg, 3).tolist(),
        "wall_s": round(wall, 1),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-scale means over seeds of the run rows, sorted by scale."""
    table = []
    for scale in sorted({r["scale"] for r in rows}):
        rs = [r for r in rows if r["scale"] == scale]
        table.append(
            {
                "scale": scale,
                "n_seeds": len(rs),
                "inserted_fraction": round(float(np.mean([r["inserted_fraction"] for r in rs])), 4),
                "rmspe_train": round(float(np.mean([r["rmspe_train"] for r in rs])), 4),
                "rmspe_test": round(float(np.mean([r["rmspe_test"] for r in rs])), 4),
                "rmspe_all": round(float(np.mean([r["rmspe_all"] for r in rs])), 4),
                "config_hash": rs[0]["config_hash"] if len(rs) == 1 else None,
            }
        )
    return table


def _json_default(o: Any) -> Any:
    """numpy scalars/arrays → plain Python for json.dumps."""
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _rel(path: Path) -> str:
    """Repo-relative path when inside the repo, else as given."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _print_table(table: list[dict[str, Any]]) -> None:
    for r in table:
        print(
            f"  s={r['scale']:.3f} inserted={r['inserted_fraction']:.3f} "
            f"rmspe fit={r['rmspe_train']:.3f} holdout={r['rmspe_test']:.3f} (n={r['n_seeds']})"
        )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--scenario", type=Path, required=True, help="replica YAML to scale")
    ap.add_argument("--fleet-artifact", default=None, help="override fleet.idm_calibration")
    ap.add_argument("--observed", type=Path, required=True, help="JSON with the observed table")
    ap.add_argument("--observed-key", default="segment_speeds_ms")
    ap.add_argument("--window-s", type=float, required=True, help="observed window length [s]")
    ap.add_argument("--span-m", type=float, required=True, help="measured span length [m]")
    ap.add_argument("--n-segments", type=int, default=None, help="default: table width")
    ap.add_argument("--x-offset-m", type=float, default=0.0, help="sim x of observed x = 0")
    ap.add_argument("--x-scale", type=float, default=1.0, help="sim metres per observed metre")
    ap.add_argument("--warmup-s", type=float, default=None, help="default: scenario sim.warmup_s")
    ap.add_argument("--fit-windows", default=None, help='e.g. "0-2"; default: first half')
    ap.add_argument("--holdout-windows", default=None, help='e.g. "3-5"; default: second half')
    ap.add_argument("--grid", type=float, nargs="+", default=list(DEFAULT_GRID))
    ap.add_argument("--refine-step", type=float, default=REFINE_STEP)
    ap.add_argument("--refine-half-width", type=int, default=REFINE_HALF_WIDTH)
    ap.add_argument("--coarse-only", action="store_true")
    ap.add_argument("--seeds-per-point", type=int, default=1)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True, help="fit artifact (JSON)")
    ap.add_argument("--write-scenario", type=Path, default=None, help="scenario YAML at the fit")
    ap.add_argument("--name", default=None, help="name of the written scenario")
    args = ap.parse_args(argv)

    raw = yaml.safe_load(args.scenario.read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"{args.scenario}: expected a mapping at top level")
    if args.fleet_artifact:
        raw.setdefault("fleet", {})["idm_calibration"] = args.fleet_artifact
    if args.name:
        raw["name"] = args.name
    base = ScenarioConfig.model_validate(scale_inflows(raw, 1.0))
    obs_doc = json.loads(args.observed.read_text())
    obs = np.asarray(obs_doc[args.observed_key], dtype=np.float64)
    if obs.ndim != 2:
        raise SystemExit(f"{args.observed}[{args.observed_key}] must be 2-D [windows, segments]")
    n_windows, n_segments = obs.shape
    if args.n_segments is not None and args.n_segments != n_segments:
        raise SystemExit(f"--n-segments {args.n_segments} != table width {n_segments}")
    if (args.fit_windows is None) != (args.holdout_windows is None):
        ap.error("give both --fit-windows and --holdout-windows or neither")
    if args.fit_windows is None:
        fit_w, hold_w = default_split(n_windows)
    else:
        fit_w, hold_w = parse_windows(args.fit_windows), parse_windows(args.holdout_windows)
    if max(fit_w + hold_w) >= n_windows:
        raise SystemExit(f"window index beyond the {n_windows} observed windows")
    if set(fit_w) & set(hold_w):
        raise SystemExit("fit and holdout windows overlap")
    geometry = {
        "warmup_s": float(args.warmup_s if args.warmup_s is not None else base.sim.warmup_s),
        "x_offset_m": float(args.x_offset_m),
        "x_scale": float(args.x_scale),
        "window_s": float(args.window_s),
        "n_windows": int(n_windows),
        "span_m": float(args.span_m),
        "n_segments": int(n_segments),
    }
    seeds = [int(s) for s in spawn_seeds(base.seed, max(args.seeds_per_point, 1))]
    print(
        f"scenario {base.name} ({_rel(args.scenario)}), fleet {base.fleet.idm_calibration}; "
        f"observed {obs.shape[0]} x {obs.shape[1]} bins; fit windows {fit_w}, holdout {hold_w}; "
        f"seeds {seeds}"
    )

    def _payloads(scales: list[float]) -> list[dict[str, Any]]:
        return [
            {
                "raw": raw,
                "scale": float(s),
                "seed": seed,
                "observed": obs.tolist(),
                "geometry": geometry,
                "fit_windows": fit_w,
                "holdout_windows": hold_w,
            }
            for s in scales
            for seed in seeds
        ]

    ctx = mp.get_context("spawn")
    rows: list[dict[str, Any]] = []
    coarse = sorted({round(float(s), 3) for s in args.grid})
    with ctx.Pool(min(args.procs, len(coarse) * len(seeds))) as pool:
        rows += pool.map(_job, _payloads(coarse))
    table = summarize(rows)
    _print_table(table)
    best = best_row(table)
    fine: list[float] = []
    if not args.coarse_only:
        fine = [
            s
            for s in refine_grid(best["scale"], args.refine_step, args.refine_half_width)
            if s not in coarse
        ]
        if fine:
            with ctx.Pool(min(args.procs, len(fine) * len(seeds))) as pool:
                rows += pool.map(_job, _payloads(fine))
            table = summarize(rows)
            _print_table(table)
            best = best_row(table)
    print(
        f"best level {best['scale']:.3f}: rmspe fit {best['rmspe_train']:.3f}, "
        f"holdout {best['rmspe_test']:.3f}, inserted {best['inserted_fraction']:.3f}"
    )
    best_cfg = ScenarioConfig.model_validate(scale_inflows(raw, best["scale"]))
    best_hash = config_hash(best_cfg)
    result = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": _versions(),
        "script": "scripts/fit_demand_scale.py",
        "scenario": _rel(args.scenario),
        "scenario_name": base.name,
        "fleet_artifact": base.fleet.idm_calibration,
        "observed": {
            "path": _rel(args.observed),
            "key": args.observed_key,
            "data_hash": obs_doc.get("data_hash"),
            "segment_speeds_ms": obs.tolist(),
        },
        "geometry": geometry,
        "objective": (
            f"segment-speed RMSPE over fit windows {fit_w} "
            f"({fit_w[0] * geometry['window_s']:g}-{(fit_w[-1] + 1) * geometry['window_s']:g} s); "
            f"windows {hold_w} held out"
        ),
        "fit_windows": fit_w,
        "holdout_windows": hold_w,
        "seeds": seeds,
        "grid_coarse": coarse,
        "grid_fine": fine,
        "refine": {"step": args.refine_step, "half_width": args.refine_half_width},
        "table": table,
        "runs": sorted(rows, key=lambda r: (r["scale"], r["seed"])),
        "best": {**best, "config_hash": best_hash},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, default=_json_default, allow_nan=True))
    print(f"-> {args.out}")
    if args.write_scenario is not None:
        raw_best = scale_inflows(raw, best["scale"])
        header = (
            f"# {best_cfg.name} — {_rel(args.scenario)} with mainline and on-ramp inflows\n"
            f"# multiplied by s = {best['scale']:.3f}, fitted by scripts/fit_demand_scale.py on the\n"
            f"# observed segment speeds of {_rel(args.observed)} [{args.observed_key}] over windows\n"
            f"# {fit_w} ({geometry['window_s']:g} s each) only; windows {hold_w} are held out.\n"
            f"# Fleet: {base.fleet.idm_calibration}. Exit fractions and any boundary unchanged.\n"
            f"# See {_rel(args.out)} (rmspe fit {best['rmspe_train']:.3f}, holdout "
            f"{best['rmspe_test']:.3f}, inserted {best['inserted_fraction']:.3f}).\n"
            f"# config hash {best_hash}; seeded={'True' if best_cfg.seeded else 'False'}.\n"
        )
        args.write_scenario.parent.mkdir(parents=True, exist_ok=True)
        args.write_scenario.write_text(header + yaml.safe_dump(raw_best, sort_keys=False))
        print(f"-> {args.write_scenario} ({best_hash})")


if __name__ == "__main__":
    main()
