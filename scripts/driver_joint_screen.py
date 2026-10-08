"""Screen S1 of the Amendment-7 joint driver grid: unstable at its own capacity density? ($0).

docs/PRE_FRISCO_PROGRAM.md B6, step 2 (fixed with the plan; proposed
docs/FRISCO_PROTOCOL.md Amendment 7, approved by the coordinator 2026-10-07):
a grid pair passes S1 when the population's **mean driver** is string-unstable
at its own capacity density, i.e. the lower edge of
``validation.string_stability.unstable_band`` is at or below the density of
maximum equilibrium flow — docs/DISCHARGE_CALIBRATION.md §4's measure, which
CLAUDE.md §3.1 makes a requirement ("verify that chosen defaults are unstable
near capacity").

The measure, closed form, no simulation:

* *Mean driver.* The population's mean vector (v0, T, a_max, b, s0) with
  δ = 4 (SUMO's IDM exponent, CLAUDE.md §3.1), vehicles 5 m long
  (``validation.string_stability.DEFAULT_VEHICLE_LENGTH_M``, the replica's
  passenger vType), as §4 evaluated it.
* *Capacity density.* ``q(v) = v / (s_e(v) + L)`` with the IDM equilibrium gap
  ``s_e(v) = (s0 + v·T)/sqrt(1 − (v/v0)^4)`` maximised over ``v ∈ (0, v0)``:
  first on ``scripts/i24_calibrate_capacity.py``'s 40,001-point grid (which
  wrote ``artifacts/idm_i24_capacity_equilibrium.json``: 1,985.5 veh/h/lane at
  18.912 m/s for the base, 29.2 veh/km), then refined by a bounded scalar
  maximisation between the grid neighbours. ``ρ* = q*/v* = 1/(s_e(v*) + L)``.
* *Unstable band.* ``unstable_band`` on densities 1.00, 1.01, … veh/km up to
  the mean driver's standstill density ``1/(s0 + L)``; its lower edge is then
  refined by Brent's method on the string-stability margin
  ``f_v²/2 − f_v·f_dv − f_s`` between the last stable and the first unstable
  grid density. Both the grid edge and the refined edge are recorded; the
  verdict uses the refined edge.
* *Verdict.* pass ⇔ refined lower edge ≤ ρ*. A mean driver stable at every
  density (no band) fails.

The plan's **Stop rule** (B6, fixed): "If S1 admits no pair with k > 0: then
no ``a_max`` gain is compatible with §3.1 at these T. A lower T raises
pre-breakdown capacity (Amendment 1's objection), and S2 is one-sided; the
report says so." It is evaluated here and recorded; ``--check-stop`` returns
:data:`EXIT_STOP` when it fires (the stage's guard).

Inputs: ``artifacts/driver_joint_grid_i24.json`` (``scripts/driver_joint_grid.py``)
and the 25 population files it lists, each checked against the sha256 recorded
there. Output: ``artifacts/driver_joint_screen_i24.json``.

Run (from the repository root; local, closed form)::

    uv run --no-sync python scripts/driver_joint_screen.py
    uv run --no-sync python scripts/driver_joint_screen.py --check-stop   # 0 go on, 10 Stop
    uv run --no-sync python scripts/driver_joint_screen.py --list-pass    # "k j population"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.units import veh_m_to_veh_km, veh_s_to_veh_h
from validation.string_stability import (
    DEFAULT_VEHICLE_LENGTH_M,
    equilibrium_speed,
    idm_partials,
    stability_criterion,
    unstable_band,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "flowstate.driver_joint_screen/1"
PROTOCOL = (
    "docs/PRE_FRISCO_PROGRAM.md B6 step 2 (S1); docs/FRISCO_PROTOCOL.md Amendment 7 (proposed; "
    "approved by the coordinator 2026-10-07 21:16 CDT)"
)
DEFAULT_GRID = "artifacts/driver_joint_grid_i24.json"
DEFAULT_OUT = "artifacts/driver_joint_screen_i24.json"

IDM_DELTA = 4.0
"""SUMO's IDM acceleration exponent (CLAUDE.md §3.1: fixed)."""
VEHICLE_LENGTH_M = DEFAULT_VEHICLE_LENGTH_M
"""Vehicle length [m] between spacing and gap (docs/DISCHARGE_CALIBRATION.md §4: 5 m)."""
CAPACITY_V_STEPS = 40001
"""The capacity grid of ``scripts/i24_calibrate_capacity.py`` (``EQ_V_STEPS``)."""
RHO_MIN_VEH_KM = 1.0
RHO_STEP_VEH_KM = 0.01
"""The band scan's density grid [veh/km]; the lower edge is refined between grid points."""
EXIT_STOP = 10
"""``--check-stop``: the plan's Stop rule fired."""

RULE_TEXT = (
    "S1 (docs/PRE_FRISCO_PROGRAM.md B6 step 2, fixed): the mean driver is unstable at its own "
    "capacity density, i.e. the unstable_band lower edge is at or below the density of maximum "
    "equilibrium flow (docs/DISCHARGE_CALIBRATION.md §4's measure)."
)
STOP_TEXT = (
    "If S1 admits no pair with k > 0: then no a_max gain is compatible with §3.1 at these T. A "
    "lower T raises pre-breakdown capacity (Amendment 1's objection), and S2 is one-sided; the "
    "report says so. (docs/PRE_FRISCO_PROGRAM.md B6, Stop)"
)
#: docs/DISCHARGE_CALIBRATION.md §4's quotes (veh/km), reproduced in the artifact.
QUOTED_S4 = {"band_lo_k0": 28.2, "band_lo_k0.5": 32.5, "band_lo_k1": 39.8, "capacity_density": 29.2}


def rel(path: str | Path) -> str:
    """``path`` relative to the repository when inside it, else as given."""
    p = Path(path)
    try:
        return str((p if p.is_absolute() else REPO_ROOT / p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def file_sha256(path: str | Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(_abs(path).read_bytes()).hexdigest()


def mean_driver(pop: IDMCalibration) -> dict[str, float]:
    """The population's mean IDM parameters with δ = 4 (``validation.string_stability`` keys)."""
    return {
        "v0": float(pop.mean["v0"]),
        "T": float(pop.mean["T"]),
        "a_max": float(pop.mean["a_max"]),
        "b": float(pop.mean["b"]),
        "s0": float(pop.mean["s0"]),
        "delta": IDM_DELTA,
    }


def equilibrium_flow(v: float | np.ndarray, p: Mapping[str, float], length_m: float) -> Any:
    """``v / (s_e(v) + L)`` [veh/s] with the IDM equilibrium gap (module docstring)."""
    s_e = (p["s0"] + v * p["T"]) / np.sqrt(1.0 - (v / p["v0"]) ** p["delta"])
    return v / (s_e + length_m)


def capacity_point(
    p: Mapping[str, float], length_m: float = VEHICLE_LENGTH_M
) -> tuple[float, float, float]:
    """``(q* [veh/s], v* [m/s], ρ* [veh/m])``: the mean driver's maximum equilibrium flow.

    The 40,001-point grid of ``scripts/i24_calibrate_capacity.py``, then a bounded
    maximisation between the grid neighbours of the grid maximum (``q`` is unimodal
    in ``v`` on ``(0, v0)``).
    """
    v0 = float(p["v0"])
    v = np.linspace(v0 / CAPACITY_V_STEPS, v0 * 0.999, CAPACITY_V_STEPS)
    i = int(np.argmax(equilibrium_flow(v, p, length_m)))
    lo, hi = float(v[max(i - 1, 0)]), float(v[min(i + 1, v.size - 1)])
    res = minimize_scalar(
        lambda x: -float(equilibrium_flow(x, p, length_m)),
        bounds=(lo, hi),
        method="bounded",
        options={"xatol": 1e-12},
    )
    v_star = float(res.x)
    q_star = float(equilibrium_flow(v_star, p, length_m))
    return q_star, v_star, q_star / v_star


def stability_margin(rho: float, p: Mapping[str, float], length_m: float) -> float:
    """``f_v²/2 − f_v·f_dv − f_s`` at the equilibrium of density ``rho`` [veh/m] (< 0: unstable).

    NaN where the density has no positive-speed equilibrium (gap ≤ s0).
    """
    gap = 1.0 / rho - length_m
    if gap <= p["s0"]:
        return math.nan
    v_e = equilibrium_speed(gap, p)
    if v_e <= 0.0:
        return math.nan
    return float(stability_criterion(idm_partials(v_e, p)))


def density_grid(p: Mapping[str, float], length_m: float = VEHICLE_LENGTH_M) -> np.ndarray:
    """Band-scan densities [veh/m]: 1.00, 1.01, … veh/km below the standstill density."""
    rho_jam_veh_km = veh_m_to_veh_km(1.0 / (p["s0"] + length_m))
    n = math.floor((rho_jam_veh_km - RHO_MIN_VEH_KM) / RHO_STEP_VEH_KM) + 1
    grid_veh_km = RHO_MIN_VEH_KM + RHO_STEP_VEH_KM * np.arange(n)
    return np.asarray(grid_veh_km / 1000.0, dtype=np.float64)


def band_edges(
    p: Mapping[str, float], length_m: float = VEHICLE_LENGTH_M
) -> dict[str, float | bool]:
    """The unstable band [veh/m]: grid edges and the refined lower edge (module docstring).

    Returns:
        ``lo_grid``, ``hi_grid`` (``unstable_band`` on :func:`density_grid`),
        ``lo`` (refined; NaN with no band) and ``lo_refined`` (False when the
        band starts at the grid's first density and cannot be refined).
    """
    rho = density_grid(p, length_m)
    lo_g, hi_g = unstable_band(p, rho, vehicle_length_m=length_m)
    if math.isnan(lo_g):
        return {"lo_grid": math.nan, "hi_grid": math.nan, "lo": math.nan, "lo_refined": False}
    i = int(np.searchsorted(rho, lo_g))
    if i == 0:
        return {"lo_grid": lo_g, "hi_grid": hi_g, "lo": lo_g, "lo_refined": False}
    below = float(rho[i - 1])
    if not stability_margin(below, p, length_m) > 0.0:
        return {"lo_grid": lo_g, "hi_grid": hi_g, "lo": lo_g, "lo_refined": False}
    lo = float(
        brentq(
            lambda r: stability_margin(r, p, length_m),
            below,
            float(lo_g),
            xtol=1e-14,
            rtol=1e-13,
        )
    )
    return {"lo_grid": lo_g, "hi_grid": hi_g, "lo": lo, "lo_refined": True}


def screen_driver(p: Mapping[str, float], length_m: float = VEHICLE_LENGTH_M) -> dict[str, Any]:
    """S1 on one mean driver: capacity point, band edges, verdict (units: veh/km, veh/h, m/s)."""
    q, v, rho_star = capacity_point(p, length_m)
    band = band_edges(p, length_m)
    lo = float(band["lo"])
    has_band = not math.isnan(lo)
    passes = bool(has_band and lo <= rho_star)
    margin = stability_margin(rho_star, p, length_m)
    return {
        "capacity_veh_h_lane": veh_s_to_veh_h(q),
        "v_at_capacity_ms": v,
        "capacity_density_veh_km": veh_m_to_veh_km(rho_star),
        "band_lo_veh_km": veh_m_to_veh_km(lo) if has_band else None,
        "band_lo_grid_veh_km": veh_m_to_veh_km(float(band["lo_grid"])) if has_band else None,
        "band_hi_grid_veh_km": veh_m_to_veh_km(float(band["hi_grid"])) if has_band else None,
        "band_lo_refined": bool(band["lo_refined"]),
        "capacity_density_minus_band_lo_veh_km": (
            veh_m_to_veh_km(rho_star - lo) if has_band else None
        ),
        "criterion_at_capacity_density_s2": None if math.isnan(margin) else margin,
        "unstable_at_capacity_density": bool(margin < 0.0),
        "passes": passes,
        "reason": (
            "no string-unstable equilibrium density: stable everywhere"
            if not has_band
            else (
                "band lower edge at or below the capacity density"
                if passes
                else "band lower edge above the capacity density: stable at capacity"
            )
        ),
    }


def stop_rule(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The plan's Stop rule on the screened rows (:data:`STOP_TEXT`)."""
    k_pos = [{"k": r["k"], "j": r["j"]} for r in rows if r["k"] > 0.0 and r["passes"]]
    fired = not k_pos
    return {
        "text": STOP_TEXT,
        "fired": fired,
        "pairs_passing_with_k_positive": k_pos,
        "reading": (
            "S1 admits no pair with k > 0: B6 stops before S2 (no a_max gain on this grid keeps "
            "the mean driver unstable at its capacity density). What S1 does admit moves mean T "
            "alone, the knob Amendment 1 set aside because a lower T raises pre-breakdown "
            "capacity, and S2 (capacity >= 1,775 veh/h/lane) is one-sided: it cannot refuse "
            "a capacity inflated by a lower T."
            if fired
            else "S1 admits a pair with k > 0: B6 goes on to S2."
        ),
    }


def _load_grid(grid_path: Path) -> dict[str, Any]:
    grid = json.loads(grid_path.read_text())
    if grid.get("schema") != "flowstate.driver_joint_grid/1":
        raise ValueError(f"{grid_path}: not a flowstate.driver_joint_grid/1 manifest")
    return grid


def screen(grid_path: str | Path, created_at: str) -> dict[str, Any]:
    """S1 on every pair of the grid manifest; the artifact (module docstring).

    Raises:
        ValueError: A population's sha256 or shifted means differ from the
            manifest's record (the lineage check).
    """
    gp = _abs(grid_path)
    grid = _load_grid(gp)
    rows: list[dict[str, Any]] = []
    for pair in grid["pairs"]:
        path = pair["path"]
        sha = file_sha256(path)
        if sha != pair["sha256"]:
            raise ValueError(f"{path}: sha256 {sha} differs from the manifest's {pair['sha256']}")
        pop = IDMCalibration.load(_abs(path))
        if (float(pop.mean["a_max"]), float(pop.mean["T"])) != (
            float(pair["a_max_mean_ms2"]),
            float(pair["T_mean_s"]),
        ):
            raise ValueError(f"{path}: mean a_max / T differ from the manifest's")
        p = mean_driver(pop)
        rows.append(
            {
                "k": float(pair["k"]),
                "j": int(pair["j"]),
                "a_max_mean_ms2": p["a_max"],
                "T_mean_s": p["T"],
                "population": path,
                "sha256": sha,
                **screen_driver(p),
            }
        )
    passing = [{"k": r["k"], "j": r["j"]} for r in rows if r["passes"]]
    by = {(r["k"], r["j"]): r for r in rows}
    reproduction: dict[str, Any] = {}
    if all(key in by for key in ((0.0, 0), (0.5, 0), (1.0, 0))):
        computed = {
            "band_lo_k0": by[(0.0, 0)]["band_lo_veh_km"],
            "band_lo_k0.5": by[(0.5, 0)]["band_lo_veh_km"],
            "band_lo_k1": by[(1.0, 0)]["band_lo_veh_km"],
            "capacity_density": by[(0.0, 0)]["capacity_density_veh_km"],
        }
        reproduction = {
            "source": "docs/DISCHARGE_CALIBRATION.md §4 (quoted to 0.1 veh/km)",
            "quoted_veh_km": QUOTED_S4,
            "computed_veh_km": computed,
            "computed_on_a_0.1_veh_km_grid": {
                key: (
                    None
                    if val is None
                    else (
                        round(val, 1)
                        if key == "capacity_density"
                        else math.ceil(round(val * 10.0, 9)) / 10.0
                    )
                )
                for key, val in computed.items()
            },
            "note": "computed: refined edges and the capacity density; on a 0.1-veh/km grid: the "
            "first unstable grid density (edges) and the rounding (capacity density), the "
            "resolution §4 quotes. Any difference from the quotes is grid resolution; no verdict "
            "of this screen depends on it",
        }
    return {
        "schema": SCHEMA,
        "screen": "S1",
        "protocol": PROTOCOL,
        "created_at": created_at,
        "script": "scripts/driver_joint_screen.py",
        "rule": RULE_TEXT,
        "measure": {
            "mean_driver": "the population's mean (v0, T, a_max, b, s0), delta 4",
            "vehicle_length_m": VEHICLE_LENGTH_M,
            "capacity": "q(v) = v / (s_e(v) + L), s_e(v) = (s0 + v T) / sqrt(1 - (v/v0)^4), "
            f"maximised over v in (0, v0): {CAPACITY_V_STEPS}-point grid "
            "(scripts/i24_calibrate_capacity.py) then bounded refinement; capacity density = q*/v*",
            "band": "validation.string_stability.unstable_band on densities "
            f"{RHO_MIN_VEH_KM:g} veh/km in {RHO_STEP_VEH_KM:g}-veh/km steps up to 1/(s0 + L); "
            "lower edge refined by Brent's method on f_v^2/2 - f_v f_dv - f_s between the last "
            "stable and the first unstable grid density",
            "verdict": "pass iff refined lower edge <= capacity density; no band = fail",
        },
        "grid": {"path": rel(gp), "sha256": file_sha256(gp)},
        "rows": rows,
        "summary": {
            "n_pairs": len(rows),
            "n_pass": len(passing),
            "passing": passing,
        },
        "stop_rule": stop_rule(rows),
        "reproduction": reproduction,
        "context_not_gating": [
            "capacity_veh_h_lane is the mean driver's closed-form equilibrium capacity, an index: "
            "scripts/i24_calibrate_capacity.py's committed grid puts SUMO's four-lane capacity at "
            "0.86-0.91 of it (artifacts/idm_i24_capacity_equilibrium.json model_factor). S2 is "
            "the simulated check.",
            "a_max does not enter the equilibrium, so the capacity point depends on j only; the "
            "j = 0 populations keep the base's T, whose simulated four-lane capacity was "
            "calibrated to the 1,775 veh/h/lane target itself (artifacts/idm_i24_capacity."
            "calibration.json), so S2 at j = 0 sits on its threshold by construction.",
        ],
    }


def _table(art: dict[str, Any]) -> str:
    lines = [
        f"{'k':>5s} {'j':>3s} {'a_max':>7s} {'T':>7s} {'band lo':>8s} {'rho*':>7s} "
        f"{'margin':>7s} {'q*':>7s}  S1"
    ]
    for r in art["rows"]:
        lo = r["band_lo_veh_km"]
        mg = r["capacity_density_minus_band_lo_veh_km"]
        lines.append(
            f"{r['k']:5.2f} {r['j']:+3d} {r['a_max_mean_ms2']:7.4f} {r['T_mean_s']:7.4f} "
            f"{('—' if lo is None else f'{lo:.2f}'):>8s} {r['capacity_density_veh_km']:7.2f} "
            f"{('—' if mg is None else f'{mg:+.2f}'):>7s} {r['capacity_veh_h_lane']:7.1f}  "
            f"{'pass' if r['passes'] else 'FAIL'}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Screen the grid (default), or read the artifact (``--check-stop``, ``--list-pass``)."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--grid", type=Path, default=REPO_ROOT / DEFAULT_GRID)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / DEFAULT_OUT)
    ap.add_argument(
        "--created-at",
        default=None,
        help="ISO-8601 timestamp recorded in the artifact (default: now, UTC)",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-stop",
        action="store_true",
        help=f"read --out: exit {EXIT_STOP} when the plan's Stop rule fired, 0 when B6 goes on",
    )
    mode.add_argument(
        "--list-pass",
        action="store_true",
        help="read --out: print 'k j population' for every pair that passes S1",
    )
    args = ap.parse_args(argv)
    if args.check_stop or args.list_pass:
        art = json.loads(args.out.read_text())
        if art.get("schema") != SCHEMA:
            ap.error(f"{args.out}: not a {SCHEMA} artifact")
        if args.list_pass:
            for r in art["rows"]:
                if r["passes"]:
                    print(f"{r['k']} {r['j']} {r['population']}")
            return 0
        fired = bool(art["stop_rule"]["fired"])
        print(art["stop_rule"]["reading"])
        return EXIT_STOP if fired else 0
    created = args.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    art = screen(args.grid, created)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(RULE_TEXT)
    print(_table(art))
    s = art["summary"]
    print(f"{s['n_pass']} of {s['n_pairs']} pairs pass S1: {s['passing']}")
    print(f"Stop rule fired: {art['stop_rule']['fired']}. {art['stop_rule']['reading']}")
    print(f"-> {rel(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
