"""Amendment B1's boundary limit factor, computed from recorded inputs (not fitted).

docs/I24_DISCHARGE_DIAGNOSIS.md §7.5 (the L5 fixture) and §8.3 (amendment B1,
PROPOSED, not adopted). A measured downstream boundary (``BoundarySpec``) is a
schedule of observed mean speeds, which the runner posts as every vehicle's
speed limit, i.e. as its desired speed. An IDM driver whose desired speed is
the observed mean speed keeps the free-road term's longer gaps and carries
less than the real road carried there. B1 posts ``f × v_limit`` with one
factor ``f`` per corridor (``BoundarySpec.limit_factor``), chosen so that the
fleet population's mean driver, in IDM equilibrium at the schedule's
study-window mean speed ``v̄`` with desired speed ``f·v̄``, carries the recorded
flow per lane ``q̄`` at the last measured section::

    q_eq(v; v0) = v / (l + (s0 + v·T) / √(1 − (v/v0)⁴))      (IDM, δ = 4)
    q_eq(v̄; f·v̄) = q̄
    ⇒ f = (1 − (S / (v̄/q̄ − l))²)^(−1/4),   S = s0 + v̄·T

(closed form; a solution exists only while ``q̄ < v̄ / (l + S)``, the flow at
an unbounded desired speed). ``T`` and ``s0`` are the population artifact's
means (``IDMCalibration.mean``), ``l`` the vehicle length every generated vType
carries (``microsim.vehicles.VEHICLE_LENGTH_M``), ``v̄`` the schedule's
time-weighted mean over the study window ``[sim.warmup_s, sim.duration_s)``
and ``q̄`` the recorded flow at the last measured section over the same
window, divided by that section's lane count. ``f`` is reported to four
decimals from the flow as the record quotes it (whole veh/h; for I-24 the
§7.5 value 1.2185), with the unrounded flow's value beside it.

The rule is the IDM's equilibrium. It is not defined for an EIDM fleet: the
EIDM's car-following core is the improved IDM, whose equilibrium gap below the
desired speed is ``s0 + v·T`` whatever the desired speed (measured on this
package's EIDM fleet and read in SUMO's source, docs/WEAVE_MODEL_PLAN.md
WP-68 and WP-73), so no factor changes the equilibrium flow at ``v̄``.
Such a corridor is reported as ``applies: false`` with the
improved-IDM equilibrium flow at ``v̄`` beside the recorded flow, and the IDM
formula's value only for the record.

Corridors (every input git-tracked and small; this script never reads
``data/i24motion/processed/*`` or any ``runs/**/trajectories.parquet``):

* ``i24``: ``scenarios/i24_replica_flow_speedcal_dc_refit.yaml`` (schedule,
  study window, fleet); the canonical ``i24_replica_flow_speedcal`` and the
  calibrated ``_dc`` scenarios are checked to carry the same schedule and
  window, and their populations the same ``T`` and ``s0`` (one factor for
  all three); the recorded flow is the committed observed side of the I-24
  battery (``artifacts/i24_validation_observed.json``,
  ``hourly_flows_veh_h_recommended`` at its last section, data x 5,400 m: the
  tracked crossings divided by the recommended coverage, 6,009 veh/h); the
  lane count is that of the corridor edge at the section
  (``artifacts/i24_replica_inputs_flow.json``, ``geometry``).
* ``i94``: ``scenarios/mndot_i94_wb_stpaul_weave_dc.yaml``; the schedule is
  station S97's speeds (checked), and the recorded flow and lane count are
  S97's in ``data/mndot/mndot_i94_wb_stpaul/observations.json`` (the last
  mainline station; the scenario's last corridor edge, which hosts the
  schedule, starts there).

Also reported per corridor: the A2 band of §8.3, the recorded flow (as
quoted, whole veh/h) −3 % / +5 %, each end rounded to whole veh/h (I-24:
5,829–6,309 veh/h).

Usage (repo root)::

    uv run --no-sync python scripts/boundary_limit_factor.py              # both corridors, JSON to stdout
    uv run --no-sync python scripts/boundary_limit_factor.py --corridor i24 --out factor.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import BoundarySpec, ScenarioConfig, config_hash

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The factor §7.5 computed and the L5 fixture applied (prereg 06:45), fixed
#: in §8.3 before any corridor run.
REGISTERED_I24_FACTOR = 1.2185

#: The A2 band of §8.3: the recorded flow −3 % / +5 % (§7.5 criterion (b)).
A2_BAND = (0.97, 1.05)

#: IDM acceleration exponent δ (CLAUDE.md §3.1; fixed).
IDM_DELTA = 4.0


def sha256_file(path: Path) -> str:
    """Hex sha256 of a (small) file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    """``path`` relative to the repository root when inside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# The equilibrium relations
# --------------------------------------------------------------------------


def q_eq_idm(v: float, v0: float, T: float, s0: float, length: float) -> float:
    """IDM equilibrium flow per lane [veh/s] at speed ``v`` with desired speed ``v0``.

    The equilibrium gap is ``(s0 + v·T) / √(1 − (v/v0)^δ)`` (Treiber & Kesting
    2013, *Traffic Flow Dynamics*, ch. 11) and the flow is ``v`` over that gap
    plus the vehicle length; 0 at ``v ≥ v0``.
    """
    if v <= 0.0:
        return 0.0
    if v >= v0:
        return 0.0
    gap = (s0 + v * T) / math.sqrt(1.0 - (v / v0) ** IDM_DELTA)
    return v / (length + gap)


def q_eq_iidm(v: float, T: float, s0: float, length: float) -> float:
    """Improved-IDM equilibrium flow per lane [veh/s] at ``v`` below the desired speed.

    The improved IDM's equilibrium gap below the desired speed is ``s0 + v·T``,
    independent of the desired speed (Treiber & Kesting 2013, ch. 11); it is
    also the supremum of :func:`q_eq_idm` over the desired speed.
    """
    return v / (length + s0 + v * T) if v > 0.0 else 0.0


def solve_factor(v_bar: float, q_bar: float, T: float, s0: float, length: float) -> float:
    """``f`` with ``q_eq_idm(v_bar, f·v_bar) = q_bar`` (closed form, δ = 4).

    Args:
        v_bar: The speed [m/s] (> 0).
        q_bar: The flow per lane [veh/s] (> 0).
        T: Time headway [s].
        s0: Minimum gap [m].
        length: Vehicle length [m].

    Returns:
        The factor (> 1).

    Raises:
        ValueError: No desired speed carries ``q_bar`` at ``v_bar`` (it is at or
            above the improved-IDM flow ``v̄ / (l + s0 + v̄·T)``), or an input is
            not positive.
    """
    if v_bar <= 0.0 or q_bar <= 0.0:
        raise ValueError("v_bar and q_bar must be > 0")
    s_eq = s0 + v_bar * T
    room = v_bar / q_bar - length  # the equilibrium gap the flow asks for [m]
    if room <= s_eq:
        q_sup = q_eq_iidm(v_bar, T, s0, length)
        raise ValueError(
            f"no desired speed carries {q_bar * 3600.0:.1f} veh/h/lane at {v_bar:.4f} m/s: the "
            f"IDM equilibrium flow there is below {q_sup * 3600.0:.1f} veh/h/lane at any desired speed"
        )
    return float((1.0 - (s_eq / room) ** 2) ** (-1.0 / IDM_DELTA))


def schedule_mean(steps: Sequence[tuple[float, float]], t_lo: float, t_hi: float) -> float:
    """Time-weighted mean of a piecewise-constant schedule over ``[t_lo, t_hi)``.

    Each step holds until the next (the last to ``t_hi``); before the first
    step there is no value, so ``t_lo`` must not precede it.
    """
    if not steps or t_hi <= t_lo:
        raise ValueError("an empty schedule or window")
    if t_lo < steps[0][0]:
        raise ValueError(f"the window starts at {t_lo} s, before the schedule's first step")
    total = 0.0
    for i, (t, v) in enumerate(steps):
        t_next = steps[i + 1][0] if i + 1 < len(steps) else t_hi
        a, b = max(t, t_lo), min(t_next, t_hi)
        if b > a:
            total += v * (b - a)
    return total / (t_hi - t_lo)


def vehicle_length_m() -> float:
    """The length every generated passenger vType carries [m]."""
    from microsim.vehicles import VEHICLE_LENGTH_M  # imports the runner; kept local

    return float(VEHICLE_LENGTH_M)


# --------------------------------------------------------------------------
# Corridor inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedFlow:
    """The recorded flow at the last measured section over the study window."""

    q_total_veh_h: float
    lanes: int
    section: str
    source: dict[str, Any]


def _load_cfg(path: Path) -> ScenarioConfig:
    return ScenarioConfig.from_yaml(path)


def _boundary(cfg: ScenarioConfig) -> BoundarySpec:
    boundary = getattr(cfg.network, "boundary", None)
    if boundary is None:
        raise ValueError(f"scenario {cfg.name} has no BoundarySpec: B1 does not apply")
    return boundary


def _population(cfg: ScenarioConfig) -> tuple[Path, IDMCalibration]:
    if cfg.fleet.idm_calibration is None:
        raise ValueError(f"scenario {cfg.name} draws no population (fleet.idm_calibration unset)")
    path = Path(cfg.fleet.idm_calibration)
    path = path if path.is_absolute() else REPO_ROOT / path
    return path, IDMCalibration.load(path)


def _i24_flow() -> RecordedFlow:
    obs_path = REPO_ROOT / "artifacts" / "i24_validation_observed.json"
    obs = json.loads(obs_path.read_text())
    sections = [float(x) for x in obs["sections_m"]]
    k = max(range(len(sections)), key=lambda i: sections[i])
    window = [float(x) for x in obs["hourly_flows_veh_h_recommended"][k]]
    q_total = sum(window) / len(window)
    inputs_path = REPO_ROOT / "artifacts" / "i24_replica_inputs_flow.json"
    geo = json.loads(inputs_path.read_text())["geometry"]
    sim_x = geo["sim_x_of_data_x"]["a"] + geo["sim_x_of_data_x"]["b"] * sections[k]
    lanes = None
    start = 0.0
    for edge, length, n in zip(
        geo["corridor_edges"], geo["edge_lengths_m"], geo["edge_lanes"], strict=True
    ):
        if start <= sim_x < start + float(length):
            lanes = (str(edge), int(n), start)
            break
        start += float(length)
    if lanes is None:
        raise ValueError(f"data x {sections[k]} m (sim x {sim_x:.1f}) is on no corridor edge")
    return RecordedFlow(
        q_total_veh_h=q_total,
        lanes=lanes[1],
        section=f"data x {sections[k]:.0f} m (sim x {sim_x:.1f} m, edge {lanes[0]})",
        source={
            "flow": {
                "path": rel(obs_path),
                "sha256": sha256_file(obs_path),
                "field": f"hourly_flows_veh_h_recommended[{k}] (mean of {len(window)} 5-min windows, "
                f"{obs['period']}; tracked crossings / recommended coverage)",
            },
            "lanes": {
                "path": rel(inputs_path),
                "sha256": sha256_file(inputs_path),
                "field": "geometry.edge_lanes of the corridor edge at the section",
            },
        },
    )


def _i94_flow(cfg: ScenarioConfig, t_lo: float, t_hi: float) -> tuple[RecordedFlow, str]:
    obs_path = REPO_ROOT / "data" / "mndot" / "mndot_i94_wb_stpaul" / "observations.json"
    obs = json.loads(obs_path.read_text())
    mainline = [s for s in obs["stations"] if s["kind"] == "mainline"]
    last = max(mainline, key=lambda s: float(s["x_m"]))
    sid = str(last["id"])
    window_s = float(obs["window_s"])
    flows = obs["flows_veh_h"][sid]
    speeds = obs["speeds_ms"][sid]
    steps = _boundary(cfg).steps
    # the schedule is this station's speed series, one step per window
    if len(steps) != len(speeds) or any(
        abs(t - i * window_s) > 1e-9 or abs(v - float(s)) > 1e-9
        for i, ((t, v), s) in enumerate(zip(steps, speeds, strict=True))
    ):
        raise ValueError(f"the I-94 schedule is not station {sid}'s speed series")
    i_lo, i_hi = round(t_lo / window_s), round(t_hi / window_s)
    window = [flows[i] for i in range(i_lo, i_hi)]
    if any(q is None for q in window):
        raise ValueError(f"station {sid} has unmeasured windows in the study window")
    q_total = sum(float(q) for q in window) / len(window)
    return (
        RecordedFlow(
            q_total_veh_h=q_total,
            lanes=int(last["lanes"]),
            section=f"station {sid} ({last.get('label', '')}), x {float(last['x_m']):.1f} m",
            source={
                "flow": {
                    "path": rel(obs_path),
                    "sha256": sha256_file(obs_path),
                    "field": f"flows_veh_h[{sid}] windows {i_lo}-{i_hi - 1} "
                    f"({obs['aggregation']}; t0 {obs['t0_local']})",
                },
                "lanes": {
                    "path": rel(obs_path),
                    "sha256": sha256_file(obs_path),
                    "field": f"stations[{sid}].lanes",
                },
            },
        ),
        sid,
    )


CORRIDORS: dict[str, dict[str, Any]] = {
    "i24": {
        "scenario": "scenarios/i24_replica_flow_speedcal_dc_refit.yaml",
        "same_boundary": (
            "scenarios/i24_replica_flow_speedcal.yaml",
            "scenarios/i24_replica_flow_speedcal_dc.yaml",
        ),
    },
    "i94": {"scenario": "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml", "same_boundary": ()},
}


def corridor_factor(corridor: str) -> dict[str, Any]:
    """Every input, its provenance and the factor of one corridor."""
    spec = CORRIDORS[corridor]
    scn_path = REPO_ROOT / spec["scenario"]
    cfg = _load_cfg(scn_path)
    boundary = _boundary(cfg)
    t_lo, t_hi = float(cfg.sim.warmup_s), float(cfg.sim.duration_s)
    steps = [(float(t), float(v)) for t, v in boundary.steps]
    v_bar = schedule_mean(steps, t_lo, t_hi)
    pop_path, pop = _population(cfg)
    T, s0 = float(pop.mean["T"]), float(pop.mean["s0"])
    length = vehicle_length_m()
    if cfg.fleet.heavy is not None:
        raise ValueError(f"{cfg.name}: a heavy-vehicle share has no mean driver in this rule")

    shared: list[dict[str, Any]] = []
    for other in spec["same_boundary"]:
        o_path = REPO_ROOT / other
        o_cfg = _load_cfg(o_path)
        o_b = _boundary(o_cfg)
        o_pop_path, o_pop = _population(o_cfg)
        same = (
            [(float(t), float(v)) for t, v in o_b.steps] == steps
            and (float(o_cfg.sim.warmup_s), float(o_cfg.sim.duration_s)) == (t_lo, t_hi)
            and float(o_pop.mean["T"]) == T
            and float(o_pop.mean["s0"]) == s0
            and o_cfg.fleet.model == cfg.fleet.model
            and o_cfg.fleet.heavy is None
        )
        if not same:
            raise ValueError(
                f"{other} does not share {spec['scenario']}'s boundary, window and T, s0"
            )
        shared.append(
            {
                "path": other,
                "name": o_cfg.name,
                "config_hash": config_hash(o_cfg),
                "population": rel(o_pop_path),
            }
        )

    if corridor == "i24":
        flow = _i24_flow()
        station = None
    else:
        flow, station = _i94_flow(cfg, t_lo, t_hi)
    q_total = flow.q_total_veh_h
    q_quoted = float(round(q_total))
    q_lane = q_total / flow.lanes
    q_lane_quoted = q_quoted / flow.lanes

    out: dict[str, Any] = {
        "corridor": corridor,
        "scenario": {
            "path": spec["scenario"],
            "sha256": sha256_file(scn_path),
            "name": cfg.name,
            "config_hash": config_hash(cfg),
        },
        "scenarios_sharing_the_factor": shared,
        "fleet_model": cfg.fleet.model,
        "inputs": {
            "schedule": {
                "n_steps": len(steps),
                "study_window_sim_s": [t_lo, t_hi],
                "v_bar_ms": v_bar,
                "v_bar_kmh": v_bar * 3.6,
                "rule": "time-weighted mean of the schedule over [sim.warmup_s, sim.duration_s)",
                **({"station": station} if station else {}),
            },
            "recorded_flow": {
                "section": flow.section,
                "q_total_veh_h": q_total,
                "q_total_veh_h_as_quoted": q_quoted,
                "lanes": flow.lanes,
                "q_lane_veh_h": q_lane,
                "q_lane_veh_h_as_quoted": q_lane_quoted,
                "source": flow.source,
            },
            "mean_driver": {
                "T_s": T,
                "s0_m": s0,
                "population": {"path": rel(pop_path), "sha256": sha256_file(pop_path)},
                "vehicle_length_m": length,
                "vehicle_length_source": "microsim.vehicles.VEHICLE_LENGTH_M",
                "speed_factor": float(cfg.fleet.speed_factor),
                "speed_factor_note": (
                    "the runner posts f·v / speed_factor, so the mean driver's desired speed is "
                    "f·v whatever the speed factor"
                ),
            },
        },
        "equation": "q_eq(v; v0) = v / (l + (s0 + v T) / sqrt(1 - (v / v0)^4)); q_eq(v_bar; f v_bar) = q_bar",
        "a2_band_veh_h": [round(q_quoted * A2_BAND[0]), round(q_quoted * A2_BAND[1])],
        "a2_rule": "the recorded flow at the last measured section as quoted (whole veh/h) -3 % / +5 %, "
        "each end rounded to whole veh/h (docs/I24_DISCHARGE_DIAGNOSIS.md §7.5 (b), §8.3 A2)",
        "iidm_equilibrium_flow_at_v_bar_veh_h_lane": q_eq_iidm(v_bar, T, s0, length) * 3600.0,
    }

    def _solve(q_lane_h: float) -> float | None:
        try:
            return solve_factor(v_bar, q_lane_h / 3600.0, T, s0, length)
        except ValueError:
            return None

    f_quoted = _solve(q_lane_quoted)
    f_raw = _solve(q_lane)
    if cfg.fleet.model == "IDM":
        if f_quoted is None or f_raw is None:
            out["applies"] = False
            out["reason"] = (
                "no desired speed carries the recorded flow at the schedule's mean speed in IDM "
                "equilibrium (the recorded flow is at or above the improved-IDM flow at v_bar)"
            )
            out["limit_factor"] = None
            return out
        out["applies"] = True
        out["reason"] = "the fleet is IDM: the rule's equilibrium is the fleet's"
        out["limit_factor"] = round(f_quoted, 4)
        out["limit_factor_from_flow_as_quoted"] = f_quoted
        out["limit_factor_from_unrounded_flow"] = f_raw
        out["q_eq_check_veh_h_lane"] = (
            q_eq_idm(v_bar, round(f_quoted, 4) * v_bar, T, s0, length) * 3600.0
        )
        if corridor == "i24":
            out["registered"] = {
                "value": REGISTERED_I24_FACTOR,
                "source": "docs/I24_DISCHARGE_DIAGNOSIS.md §7.5 (prereg 06:45; the L5 fixture applied it) "
                "and §8.3 (fixed before any corridor run)",
                "reproduced_to_4_decimals": round(f_quoted, 4) == REGISTERED_I24_FACTOR,
                "unrounded_flow_within_4_significant_digits": abs(f_raw - REGISTERED_I24_FACTOR)
                < 5e-4,
                "note": (
                    f"the unrounded flow ({q_total:,.1f} veh/h) gives {f_raw:.5f}; the record quotes "
                    f"it as {q_quoted:,.0f}, which gives {f_quoted:.5f} -> {round(f_quoted, 4)}; the "
                    "difference is the flow's rounding alone. The stage applies the registered value"
                ),
            }
    else:
        out["applies"] = False
        out["reason"] = (
            f"the fleet is {cfg.fleet.model}, whose car-following core is the improved IDM: below the "
            "desired speed its equilibrium gap is s0 + v T whatever the desired speed (measured on this "
            "fleet and read in SUMO's MSCFModel_EIDM, docs/WEAVE_MODEL_PLAN.md WP-68 and WP-73)"
            ", so no factor moves its equilibrium flow at v_bar; at the unscaled "
            "limit (v0 = v_bar) it may stand at any gap from s0 + v T up, i.e. at any flow up to "
            "iidm_equilibrium_flow_at_v_bar_veh_h_lane, which the recorded flow is "
            + (
                "below (so the boundary as posted does not cap the recorded flow in equilibrium)"
                if q_lane < q_eq_iidm(v_bar, T, s0, length) * 3600.0
                else "not below"
            )
        )
        out["limit_factor"] = None
        out["idm_formula_value_not_applicable"] = f_quoted
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--corridor", choices=("i24", "i94", "all"), default="all")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON here (default stdout)")
    args = ap.parse_args(argv)
    names = ("i24", "i94") if args.corridor == "all" else (args.corridor,)
    doc = {
        "schema": "flowstate.boundary_limit_factor/1",
        "amendment": "B1 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.3), PROPOSED, not adopted",
        "corridors": {name: corridor_factor(name) for name in names},
    }
    text = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        for name in names:
            c = doc["corridors"][name]
            print(
                f"{name}: applies={c['applies']} limit_factor={c['limit_factor']} "
                f"A2 band {c['a2_band_veh_h'][0]}-{c['a2_band_veh_h'][1]} veh/h",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
