"""Calibrate an onboarded corridor's demand from its detector observations.

This is the step between ``microsim.scenarios.corridor_from_bbox`` (geometry
from a bounding box: mainline chain, lane profile, discovered ramps, station
positions) and a runnable corridor: it fills the scenario the onboarding step
wrote — mainline inflow, every discovered ramp, the downstream speed boundary,
warm-up and the driver population — from a ``flowstate.observations/1``
artifact, and builds the ``flowstate.demand/1`` artifact that records how each
number was derived.

:func:`calibrate_scenario` is the whole glue in one call. It is used by
``scripts/corridor_demand.py`` (the CLI) and by ``POST /api/v1/corridors``
(``api.onboarding_jobs``), which is why it takes and returns plain data —
a scenario ``dict``, an :class:`~calibration.observations.Observations`, and
the demand artifact as a ``dict`` — and writes no files.

**Positions.** The observations artifact carries detector positions measured
along the source's own inventory (for MnDOT, haversine between IRIS nodes);
the simulation measures ``x`` along the SUMO chain. ``stations_x`` — the
station table the onboarding step produces — is the bridge: every observed
station's ``x_m`` is rewritten to its chain position so a report compares
crossings at the same cross-section, and the inventory values are kept under
``source["x_m_inventory"]``.

**Ramp flows close the mainline balance bracket by bracket.** Between two
consecutive mainline stations the observed change ``q_down − q_up`` (per
window) is assigned to the discovered ramps of that bracket, in ``x`` order:
live ramp detectors (mean flow over the span ≥ ``alive_veh_h`` and below 90 %
of the arriving mainline flow) fix the split between ramps of the same kind
and the flow of the kind that is not the closing one; the closing kind absorbs
the remainder so the simulated mainline flow reproduces every station count in
free flow. A bracket with no ramp of the needed kind carries its residual into
the next bracket (listed in the artifact); ramps outside the observed span are
set to zero (their traffic is inside the nearest mainline count already).
Discovered ramps are matched to observed ramp detectors of the same kind by
global nearest distance within ``match_radius_m``, each detector used once.

Nothing here invents a measurement: a window no detector reported carries the
previous window's value and says so, an unexplained change is recorded as a
residual rather than smeared, and the artifact counts how many ramps came from
a detector and how many from conservation (CLAUDE.md §0.1, §6.3).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from calibration.demand import DemandArtifact, demand_from_observations
from calibration.observations import Observations, ObservedStation
from flowstate_core.config import ScenarioConfig, config_hash

#: Ramps farther than this from an observed ramp detector of the same kind are
#: never matched to it [m].
DEFAULT_MATCH_RADIUS_M: float = 350.0

#: A ramp detector whose mean flow over the analysed span is below this reads
#: as dead — a real on-ramp carrying 20 veh/h all morning is a broken loop,
#: and trusting it would push its traffic onto the conservation term [veh/h].
DEFAULT_ALIVE_VEH_H: float = 30.0

#: Fall-back downstream speed limit when the observations name none [m/s]
#: (55 mph — the value the MnDOT corridor's stations carry).
_FALLBACK_SPEED_LIMIT_MS: float = 24.6

#: A live ramp detector may not read more than this share of the mainline flow
#: arriving at its bracket; above it the "ramp" is a collector–distributor
#: split counting the mainline, not a ramp.
_MAX_RAMP_SHARE: float = 0.9

#: Largest exit fraction written for an off-ramp: a diverge that takes every
#: vehicle is a corridor end, not a ramp.
_MAX_EXIT_FRACTION: float = 0.95

_S_PER_HOUR: float = 3600.0

#: How the demand artifact describes the boundary it was given.
BOUNDARY_METHOD: str = (
    "speed_schedule from the downstream station's observed mean speed; NaN windows carry"
)

#: How the demand artifact describes the way the numbers were derived.
DERIVATION_METHOD: str = (
    "entry inflow = upstream station count; ramp flows close the mainline balance bracket "
    "by bracket (live ramp detectors fix shares; the closing kind absorbs the remainder; "
    "residuals carried)"
)

#: What ``source["x_reference"]`` says on the rewritten observations.
X_REFERENCE_NOTE: str = (
    "x_m along the SUMO corridor chain (scripts/onboard_corridor.py projection); "
    "inventory (IRIS r_node haversine) positions kept under x_m_inventory"
)


@dataclass(frozen=True)
class OnboardingResult:
    """Everything :func:`calibrate_scenario` derived, as plain data.

    Attributes:
        scenario: The scenario dict, filled in place (mainline inflow, every
            ramp's profile, the downstream boundary, warm-up and — when one
            was given — the driver population).
        demand: The ``flowstate.demand/1`` artifact as a JSON-ready dict. Its
            ``observations`` and ``scenario`` fields are empty strings: only
            the caller knows where it will write the two files, and replacing
            an existing key leaves the JSON key order untouched.
        observations: The observations with every station's ``x_m`` rewritten
            to its chain position (the inventory values kept under
            ``source["x_m_inventory"]``).
        config_hash: ``flowstate_core.config.config_hash`` of the filled
            scenario — the hash the run will carry.
        chain_length_m: Corridor chain length [m], measured on ``net_path``.
        summary: Plain report lines (what the CLI prints, what the API
            returns) — inflow peak, one line per ramp, zeroed ramps,
            unmatched detectors, carried residuals, the boundary.
        residuals: One record per bracket that could not be closed
            (``from``, ``to``, ``mean_residual_veh_h``, ``note``).
        zeroed_ramps: Sentences naming the ramps set to zero because they sit
            outside the observed span.
        unmatched_detectors: Observed ramp detectors that matched no
            discovered ramp (sorted ids) — their flow is in the conservation
            term, not in a ramp profile.
        stations_without_chain_x: Observed stations absent from ``stations_x``;
            they keep their inventory position, which is not comparable with
            the simulation's ``x``.
    """

    scenario: dict[str, Any]
    demand: dict[str, Any]
    observations: Observations
    config_hash: str
    chain_length_m: float
    summary: list[str] = field(default_factory=list)
    residuals: list[dict[str, Any]] = field(default_factory=list)
    zeroed_ramps: list[str] = field(default_factory=list)
    unmatched_detectors: list[str] = field(default_factory=list)
    stations_without_chain_x: list[str] = field(default_factory=list)


def chain_edge_x(net_path: str | Path, chain: list[str]) -> dict[str, tuple[float, float]]:
    """Start and end ``x`` [m] of every chain edge, walking the SUMO net in order.

    Args:
        net_path: Compiled ``.net.xml`` the corridor was built into.
        chain: Corridor edge ids, upstream → downstream.

    Returns:
        Edge id → ``(x_start_m, x_end_m)``.

    Raises:
        KeyError: An edge of the chain is not in the network.
    """
    import sumolib  # deferred: only this function needs SUMO's net reader

    net = sumolib.net.readNet(str(net_path))
    # ramp-guess split pieces (networks.expand_ramp_splits) carry the length
    from microsim.networks import expand_ramp_splits

    chain = expand_ramp_splits(chain, [e.getID() for e in net.getEdges(withInternal=False)])
    out: dict[str, tuple[float, float]] = {}
    x = 0.0
    for edge_id in chain:
        length = float(net.getEdge(edge_id).getLength())
        out[edge_id] = (x, x + length)
        x += length
    return out


def speed_steps(values: list[float], step_s: float, fallback: float) -> list[list[float]]:
    """``[[t, v], ...]`` from a per-window series; NaN carries the previous value.

    Args:
        values: Per-window measurements (NaN where unmeasured).
        step_s: Window length [s].
        fallback: Value used until the first measured window.

    Returns:
        Time-ordered ``[t_start_s, value]`` steps, one per window.
    """
    steps: list[list[float]] = []
    last = fallback
    for k, v in enumerate(values):
        if v is not None and not math.isnan(v):
            last = float(v)
        steps.append([k * step_s, last])
    return steps


def calibrate_scenario(
    scenario: dict[str, Any],
    *,
    observations: Observations,
    stations_x: Mapping[str, float],
    net_path: str | Path,
    upstream: str,
    downstream: str,
    idm_calibration: str | None = None,
    warmup_s: float = 1800.0,
    match_radius_m: float = DEFAULT_MATCH_RADIUS_M,
    alive_veh_h: float = DEFAULT_ALIVE_VEH_H,
) -> OnboardingResult:
    """Fill an onboarded corridor's demand from its detector observations.

    The module docstring states the method. In order: the observed stations
    are moved onto the chain, the discovered ramps are matched to observed
    ramp detectors, the mainline inflow is taken from the upstream station,
    the ramp profiles close the station-to-station balance bracket by bracket,
    the downstream station's observed speed becomes the exit boundary, and the
    whole thing is recorded in a ``flowstate.demand/1`` artifact.

    The scenario dict is filled **in place** and also returned on the result;
    no file is written and no path is recorded (the caller owns both).

    Args:
        scenario: Scenario mapping as ``corridor_from_bbox`` wrote it —
            ``name``, ``network.corridor_edges``, ``network.ramps``,
            ``fleet``, ``sim``.
        observations: The corridor's ``flowstate.observations/1`` artifact.
        stations_x: Station id → position along the corridor chain [m], from
            the onboarding step's station table (``CorridorBuild.station_x``).
            Stations it omits keep their inventory position and are reported.
        net_path: The compiled ``.net.xml`` the chain was measured on.
        upstream: Mainline station supplying the entry inflow.
        downstream: Mainline station supplying the exit speed boundary.
        idm_calibration: ``IDMCalibration`` artifact path for the driver
            population, or ``None`` to leave the fleet as it is.
        warmup_s: Metrics warm-up [s] written to ``sim.warmup_s``.
        match_radius_m: Largest distance between a discovered ramp and the
            observed ramp detector it is matched to [m].
        alive_veh_h: Mean-flow threshold below which a ramp detector reads as
            dead [veh/h].

    Returns:
        The :class:`OnboardingResult`.

    Raises:
        KeyError: A chain edge or a ramp's attach edge is missing from the
            network.
        ValueError: ``upstream``/``downstream`` is not a mainline station with
            a known position, or they are the wrong way round.
    """
    chain = [str(e) for e in scenario["network"]["corridor_edges"]]
    edge_x = chain_edge_x(net_path, chain)
    length_m = max(end for _, end in edge_x.values())

    obs = observations
    step_s = float(obs.window_s)

    # 1. Observed stations onto the chain (inventory positions kept).
    obs, missing_x = _stations_onto_chain(obs, stations_x, length_m)
    if downstream not in stations_x:
        raise ValueError(
            f"downstream station {downstream!r} has no position on the corridor chain, so the "
            f"exit boundary cannot be placed; stations on the chain: {sorted(stations_x)}"
        )

    # 2. Discovered ramps with chain x, matched to observed ramp detectors.
    descriptors, unmatched_detectors = _ramp_descriptors(
        obs, scenario["network"]["ramps"], edge_x, match_radius_m
    )

    # 3. Profiles: entry inflow from the upstream station; ramps close the
    #    bracket balance. The span is resolved first so a mis-named boundary
    #    station is a plain message about the corridor, not a KeyError from
    #    the inflow step.
    mainline = _observed_span(obs, upstream, downstream)
    inflow_steps = demand_from_observations(obs, upstream, step_s=step_s)
    n_steps = int(obs.n_windows)  # the window grid; steps with no observation are omitted upstream
    first_x = float(mainline[0].x_m or 0.0)
    last_x = float(mainline[-1].x_m or 0.0)
    zeroed, residual_log = _close_balance(
        obs,
        descriptors,
        mainline,
        n_steps=n_steps,
        span=(first_x, last_x),
        alive_veh_h=alive_veh_h,
    )
    ramp_records = _ramp_records(descriptors, n_steps, step_s)

    # 4. Fill the scenario.
    scenario["network"]["inflow"] = [[float(t), float(v)] for t, v in inflow_steps]
    for ramp, rec in zip(scenario["network"]["ramps"], ramp_records, strict=True):
        if rec["kind"] == "on":
            ramp["inflow"] = [[float(t), float(v)] for t, v in rec["inflow_steps"]]
            ramp["exit_fraction"] = []
        else:
            ramp["exit_fraction"] = [[float(t), float(v)] for t, v in rec["exit_fraction_steps"]]
            ramp["inflow"] = []
    limit = next((s.speed_limit_ms for s in obs.stations if s.id == downstream), None)
    scenario["network"]["boundary"] = {
        "kind": "speed_schedule",
        "steps": speed_steps(
            list(obs.speeds_ms[downstream]), step_s, float(limit or _FALLBACK_SPEED_LIMIT_MS)
        ),
        "exit_buffer_m": max(50.0, round(length_m - float(stations_x[downstream]), 1)),
    }
    if idm_calibration:
        scenario["fleet"]["idm_calibration"] = idm_calibration
    scenario["sim"]["warmup_s"] = float(warmup_s)
    chash = config_hash(ScenarioConfig.model_validate(scenario))

    # 5. Demand artifact (paths left to the caller, see OnboardingResult).
    payload = _demand_payload(
        scenario,
        inflow_steps=inflow_steps,
        ramp_records=ramp_records,
        step_s=step_s,
        upstream=upstream,
        downstream=downstream,
        config_hash_value=chash,
        idm_calibration=idm_calibration,
        zeroed=zeroed,
        unmatched_detectors=unmatched_detectors,
        residual_log=residual_log,
    )
    summary = _summary_lines(
        config_hash_value=chash,
        length_m=length_m,
        upstream=upstream,
        downstream=downstream,
        inflow_steps=inflow_steps,
        ramp_records=ramp_records,
        zeroed=zeroed,
        unmatched_detectors=unmatched_detectors,
        residual_log=residual_log,
        boundary=scenario["network"]["boundary"],
    )
    return OnboardingResult(
        scenario=scenario,
        demand=payload,
        observations=obs,
        config_hash=chash,
        chain_length_m=length_m,
        summary=summary,
        residuals=residual_log,
        zeroed_ramps=zeroed,
        unmatched_detectors=unmatched_detectors,
        stations_without_chain_x=missing_x,
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _stations_onto_chain(
    obs: Observations, stations_x: Mapping[str, float], length_m: float
) -> tuple[Observations, list[str]]:
    """Rewrite every station's ``x_m`` to its chain position.

    Returns:
        The rewritten artifact and the ids that had no chain position (they
        keep the inventory one, which the simulation cannot be compared at).
    """
    inventory_x: dict[str, float | None] = {}
    placed = []
    missing: list[str] = []
    for st in obs.stations:
        inventory_x[st.id] = st.x_m
        if st.id not in stations_x:
            missing.append(st.id)
            placed.append(st)
            continue
        placed.append(dataclasses.replace(st, x_m=float(stations_x[st.id])))
    out = dataclasses.replace(obs, stations=tuple(placed))
    out.source["x_reference"] = X_REFERENCE_NOTE
    out.source["x_m_inventory"] = inventory_x
    out.source["chain_length_m"] = length_m
    return out, missing


def _ramp_descriptors(
    obs: Observations,
    ramps_yaml: list[dict[str, Any]],
    edge_x: Mapping[str, tuple[float, float]],
    match_radius_m: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Discovered ramps with chain ``x``, matched to observed ramp detectors.

    An on-ramp is placed at the start of the edge it joins and an off-ramp at
    the end of the edge it leaves — the cross-section at which the mainline
    count changes. Matching is global nearest first, each detector used once,
    within ``match_radius_m`` and only between a ramp and a detector of the
    same kind.

    Returns:
        The descriptors in scenario order, and the ids of observed ramp
        detectors that matched nothing.
    """
    observed_ramps = {st.id: st for st in obs.stations if st.kind in ("on_ramp", "off_ramp")}
    descriptors: list[dict[str, Any]] = []
    for ramp in ramps_yaml:
        kind = "on" if str(ramp["kind"]) == "on" else "off"
        attach = str(ramp["attach_edge"])

        # a ramp-guess split: the on-ramp joins the acceleration piece, the exit leaves the deceleration piece

        piece = attach + ("-AddedOnRampEdge" if kind == "on" else "-AddedOffRampEdge")

        x0, x1 = edge_x[piece] if piece in edge_x else edge_x[attach]
        descriptors.append({"name": ramp["name"], "kind": kind, "x_m": x0 if kind == "on" else x1})
    pairs = sorted(
        (abs(st.x_m - d["x_m"]), i, sid)
        for i, d in enumerate(descriptors)
        for sid, st in observed_ramps.items()
        if st.x_m is not None
        and st.kind == ("on_ramp" if d["kind"] == "on" else "off_ramp")
        and abs(st.x_m - d["x_m"]) <= match_radius_m
    )
    used: set[str] = set()
    for dist, i, sid in pairs:
        if sid in used or "station" in descriptors[i]:
            continue
        used.add(sid)
        descriptors[i]["station"] = sid
        descriptors[i]["match_distance_m"] = round(dist, 1)
    return descriptors, sorted(set(observed_ramps) - used)


def _observed_span(obs: Observations, upstream: str, downstream: str) -> list[ObservedStation]:
    """The mainline stations from ``upstream`` to ``downstream``, in ``x`` order.

    Raises:
        ValueError: Either boundary station is missing, is not mainline, has
            no position, or they are given the wrong way round.
    """
    mainline = sorted(
        (st for st in obs.stations if st.kind == "mainline" and st.x_m is not None),
        key=lambda st: float(st.x_m or 0.0),
    )
    by_id = {st.id: st for st in mainline}
    for role, sid in (("upstream", upstream), ("downstream", downstream)):
        if sid not in by_id:
            raise ValueError(
                f"{role} station {sid!r} is not a mainline station with a known position in the "
                f"observations; available: {sorted(by_id)}"
            )
    first_x = float(by_id[upstream].x_m or 0.0)
    last_x = float(by_id[downstream].x_m or 0.0)
    if not first_x < last_x:
        raise ValueError(
            f"upstream station {upstream!r} (x={first_x:.0f} m) must sit before downstream "
            f"station {downstream!r} (x={last_x:.0f} m) along the corridor"
        )
    return [st for st in mainline if first_x <= float(st.x_m or 0.0) <= last_x]


def _close_balance(
    obs: Observations,
    descriptors: list[dict[str, Any]],
    mainline: list[ObservedStation],
    *,
    n_steps: int,
    span: tuple[float, float],
    alive_veh_h: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Assign the observed station-to-station flow change to the ramps.

    Writes ``on_veh_h``, ``exit_frac`` and ``method`` onto each descriptor
    (module docstring). Returns the zeroed-ramp sentences and the residual log.
    """
    first_x, last_x = span

    def series(sid: str) -> list[float]:
        return [float("nan") if v is None else float(v) for v in obs.flows_veh_h[sid]]

    def alive(desc: dict[str, Any], q_arrive: list[float]) -> bool:
        sid = desc.get("station")
        if sid is None:
            return False
        q = series(sid)
        vals = [v for v in q if not math.isnan(v)]
        if not vals or sum(vals) / len(vals) < alive_veh_h:
            return False
        ratio = [
            v / a
            for v, a in zip(q, q_arrive, strict=True)
            if not math.isnan(v) and not math.isnan(a) and a > 0
        ]
        return bool(ratio) and (sum(ratio) / len(ratio)) < _MAX_RAMP_SHARE

    for d in descriptors:
        d["on_veh_h"] = [0.0] * n_steps
        d["exit_frac"] = [0.0] * n_steps
        d["method"] = "zero_outside_observed_span"
    zeroed = [
        f"{d['name']} (x={d['x_m']:.0f} m): outside the observed span "
        f"[{first_x:.0f}, {last_x:.0f}] m"
        for d in descriptors
        if not (first_x < d["x_m"] <= last_x)
    ]
    residual_log: list[dict[str, Any]] = []
    carried = [0.0] * n_steps
    for up, down in pairwise(mainline):
        q_up, q_down = series(up.id), series(down.id)
        bracket = sorted(
            (d for d in descriptors if up.x_m < d["x_m"] <= down.x_m), key=lambda d: d["x_m"]
        )
        ons = [d for d in bracket if d["kind"] == "on"]
        offs = [d for d in bracket if d["kind"] == "off"]
        live_on = {id(d) for d in ons if alive(d, q_up)}
        live_off = {id(d) for d in offs if alive(d, q_up)}
        for d in bracket:
            d["method"] = "detector" if id(d) in live_on | live_off else "conservation"
        last_on = [0.0] * len(ons)
        last_off = [0.0] * len(offs)
        for k in range(n_steps):
            if math.isnan(q_up[k]) or math.isnan(q_down[k]):
                for d in bracket:
                    d["n_carried"] = int(d.get("n_carried", 0)) + 1
                for j, d in enumerate(ons):
                    d["on_veh_h"][k] = last_on[j]
                for j, d in enumerate(offs):
                    d["exit_frac"][k] = last_off[j]
                continue
            delta = q_down[k] - q_up[k] + carried[k]
            on_total = [
                max(series(d["station"])[k], 0.0)
                if id(d) in live_on and not math.isnan(series(d["station"])[k])
                else 0.0
                for d in ons
            ]
            off_total = [
                max(series(d["station"])[k], 0.0)
                if id(d) in live_off and not math.isnan(series(d["station"])[k])
                else 0.0
                for d in offs
            ]
            # what the live detectors leave unexplained
            r = delta - (sum(on_total) - sum(off_total))
            dead_on = [j for j, d in enumerate(ons) if id(d) not in live_on]
            dead_off = [j for j, d in enumerate(offs) if id(d) not in live_off]
            leftover = 0.0
            if r > 0 and dead_on:
                for j in dead_on:
                    on_total[j] += r / len(dead_on)
            elif r < 0 and dead_off:
                for j in dead_off:
                    off_total[j] += -r / len(dead_off)
            elif r > 0 and ons:
                shares = on_total if sum(on_total) > 0 else [1.0] * len(ons)
                for j in range(len(ons)):
                    on_total[j] += r * shares[j] / sum(shares)
                    ons[j]["method"] = "detector_scaled"
            elif r < 0 and offs:
                shares = off_total if sum(off_total) > 0 else [1.0] * len(offs)
                for j in range(len(offs)):
                    off_total[j] += -r * shares[j] / sum(shares)
                    offs[j]["method"] = "detector_scaled"
            else:
                leftover = r
            # write per-ramp values in x order, exit fractions relative to the
            # arriving flow
            q_cur = q_up[k]
            for d in bracket:
                if d["kind"] == "on":
                    j = ons.index(d)
                    d["on_veh_h"][k] = on_total[j]
                    q_cur += on_total[j]
                else:
                    j = offs.index(d)
                    frac = off_total[j] / q_cur if q_cur > 0 else 0.0
                    clipped = min(max(frac, 0.0), _MAX_EXIT_FRACTION)
                    if clipped < frac:
                        # an exit takes at most _MAX_EXIT_FRACTION of what arrives;
                        # the rest stays unexplained and is carried, never dropped
                        leftover -= (frac - clipped) * q_cur
                    frac = clipped
                    d["exit_frac"][k] = frac
                    q_cur -= frac * q_cur
            carried[k] = leftover
            for j, d in enumerate(ons):
                last_on[j] = d["on_veh_h"][k]
            for j, d in enumerate(offs):
                last_off[j] = d["exit_frac"][k]
        if [v for v in carried if v != 0.0]:
            residual_log.append(
                {
                    "from": up.id,
                    "to": down.id,
                    "mean_residual_veh_h": round(sum(carried) / n_steps, 1),
                    "note": (
                        "unexplained change with no ramp of the needed kind (or sign) in this "
                        "bracket; carried into the next bracket"
                    ),
                }
            )
    return zeroed, residual_log


def _ramp_records(
    descriptors: list[dict[str, Any]], n_steps: int, step_s: float
) -> list[dict[str, Any]]:
    """Descriptors → the artifact's ramp records (profiles in SI)."""
    records: list[dict[str, Any]] = []
    for d in descriptors:
        rec: dict[str, Any] = {k: d[k] for k in ("name", "kind", "x_m", "method") if k in d}
        if d.get("station"):
            rec["station"] = d["station"]
            rec["match_distance_m"] = d.get("match_distance_m")
        rec["n_steps"] = n_steps
        rec["n_steps_carried"] = int(d.get("n_carried", 0))
        if d["kind"] == "on":
            rec["inflow_steps"] = [
                [k * step_s, v / _S_PER_HOUR] for k, v in enumerate(d["on_veh_h"])
            ]
        else:
            rec["exit_fraction_steps"] = [[k * step_s, v] for k, v in enumerate(d["exit_frac"])]
        records.append(rec)
    return records


def _demand_payload(
    scenario: Mapping[str, Any],
    *,
    inflow_steps: list[tuple[float, float]],
    ramp_records: list[dict[str, Any]],
    step_s: float,
    upstream: str,
    downstream: str,
    config_hash_value: str,
    idm_calibration: str | None,
    zeroed: list[str],
    unmatched_detectors: list[str],
    residual_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """The ``flowstate.demand/1`` artifact plus this pipeline's provenance keys.

    ``observations`` and ``scenario`` are left empty for the caller to fill
    (see :class:`OnboardingResult`); ``method`` is re-stated in place, so the
    key order is the artifact's own.
    """
    artifact = DemandArtifact(
        corridor=str(scenario["name"]),
        observations="",
        upstream_station=upstream,
        inflow_steps=[(float(t), float(v)) for t, v in inflow_steps],
        ramps=ramp_records,
        step_s=step_s,
        coverage={
            "n_steps": float(len(inflow_steps)),
            "n_ramps": float(len(ramp_records)),
            "n_ramps_detector": float(
                sum(1 for r in ramp_records if r.get("method") == "detector")
            ),
            "n_ramps_conservation": float(
                sum(1 for r in ramp_records if r.get("method") == "conservation")
            ),
            "n_ramps_zeroed": float(len(zeroed)),
            "n_brackets_with_residual": float(len(residual_log)),
        },
    )
    # The round trip is the honesty guard the artifact's own writer applies:
    # a NaN anywhere in a profile raises here rather than reaching a file.
    payload: dict[str, Any] = json.loads(json.dumps(artifact.to_dict(), allow_nan=False))
    payload["scenario"] = ""
    payload["config_hash"] = config_hash_value
    payload["downstream_station"] = downstream
    payload["boundary"] = BOUNDARY_METHOD
    payload["idm_calibration"] = idm_calibration
    payload["zeroed_ramps"] = zeroed
    payload["unmatched_ramp_detectors"] = unmatched_detectors
    payload["bracket_residuals"] = residual_log
    payload["method"] = DERIVATION_METHOD
    return payload


def _summary_lines(
    *,
    config_hash_value: str,
    length_m: float,
    upstream: str,
    downstream: str,
    inflow_steps: list[tuple[float, float]],
    ramp_records: list[dict[str, Any]],
    zeroed: list[str],
    unmatched_detectors: list[str],
    residual_log: list[dict[str, Any]],
    boundary: Mapping[str, Any],
) -> list[str]:
    """The plain report: what was derived and from which detector."""
    peak_veh_h = max(v for _, v in inflow_steps) * _S_PER_HOUR
    lines = [
        f"config_hash {config_hash_value}  chain {length_m:.0f} m",
        f"  inflow from {upstream}: {len(inflow_steps)} steps, peak {peak_veh_h:.0f} veh/h",
    ]
    for r in ramp_records:
        key = "inflow_steps" if r["kind"] == "on" else "exit_fraction_steps"
        peak = max(v for _, v in r[key])
        unit = "veh/h" if r["kind"] == "on" else "frac"
        if r["kind"] == "on":
            peak *= _S_PER_HOUR
        lines.append(
            f"  {r['kind']:3} x={r['x_m']:7.0f} m  {r.get('method'):28} peak {peak:8.2f} "
            f"{unit}  {r['name']}"
            + (f"  ← {r['station']} ({r.get('match_distance_m')} m)" if r.get("station") else "")
        )
    if zeroed:
        lines.append("  zeroed (outside observed span):")
        lines += [f"    {z}" for z in zeroed]
    if unmatched_detectors:
        lines.append(
            "  observed ramp detectors not matched to any discovered ramp: "
            + ", ".join(unmatched_detectors)
        )
    lines += [
        f"  residual carried {r['from']}→{r['to']}: mean {r['mean_residual_veh_h']:+.0f} veh/h"
        for r in residual_log
    ]
    lines.append(
        f"  boundary: {len(boundary['steps'])} speed steps from {downstream}, "
        f"exit buffer {boundary['exit_buffer_m']} m"
    )
    return lines
