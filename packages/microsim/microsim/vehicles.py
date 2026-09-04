"""Fleet generation: heterogeneous vTypes, AV tagging, route/demand XML.

Implements CLAUDE.md §3.1 (per-vehicle IDM parameter heterogeneity via
seeded truncated normals), §3.3 (AV tagging + once-per-run Bernoulli
compliance draws), and the scenario demand paths of §3.2: explicit uniformly
spaced ring departures with a tiny seeded position perturbation
(Sugiyama et al. 2008), and piecewise-constant corridor inflow with seeded
insertion jitter.

SUMO vType support notes (SUMO 1.27, verified against the installed binary):

* ``carFollowModel="IDM"`` exposes ``accel`` (=a_max), ``decel`` (=b),
  ``tau`` (=T), ``minGap`` (=s0) and ``maxSpeed`` (=v0). The IDM acceleration
  exponent **δ is fixed at 4 inside SUMO** and is not a vType attribute; a
  scenario requesting ``delta != 4`` still runs with δ = 4 and the runner
  records a note in ``meta.json``.
* ``carFollowModel="EIDM"`` (extended IDM with estimation errors and action
  points, CLAUDE.md §3.1) accepts the same core attributes.
* ``speedFactor="1.0" speedDev="0"`` disables SUMO's own desired-speed
  randomization — heterogeneity comes exclusively from our seeded per-vehicle
  parameter draws (RNG discipline, CLAUDE.md §0.5).
* ``emissionClass="HBEFA4/PC_petrol_Euro-4"`` selects the HBEFA4 petrol
  passenger-car energy model used for fuel accounting (CLAUDE.md §3.3).

RNG consumption order is fixed and documented per builder so that a given
``(config, seed)`` always produces byte-identical route files.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import AVSpec, FleetSpec, RampSpec, RingNetwork
from flowstate_core.rng import truncated_normal

#: Hard physical lower bounds for per-vehicle IDM draws (task spec §3.1):
#: draws are truncated at ±3σ AND clipped to stay physical.
IDM_HARD_LOWER: Final[dict[str, float]] = {
    "v0": 5.0,  # [m/s]
    "T": 0.4,  # [s]
    "a_max": 0.2,  # [m/s²]
    "b": 0.5,  # [m/s²]
    "s0": 0.5,  # [m]
}

#: Parameter draw order (fixed for reproducibility; matches the
#: flowstate_core IDMCalibration ordering).
IDM_PARAM_ORDER: Final[tuple[str, ...]] = ("v0", "T", "a_max", "b", "s0")

#: Vehicle length [m] written on every generated vType (standard passenger
#: car; Sugiyama et al. 2008 used comparable vehicles).
VEHICLE_LENGTH_M: Final[float] = 5.0

#: HBEFA4 petrol passenger-car emission class (fuel model, CLAUDE.md §3.3).
EMISSION_CLASS: Final[str] = "HBEFA4/PC_petrol_Euro-4"

#: Ring initial-position jitter half-range [m] — the "tiny random
#: perturbation" of CLAUDE.md §3.2.1.
RING_JITTER_M: Final[float] = 0.5

#: Corridor insertion jitter as a fraction of the nominal headway.
DEPART_JITTER_FRAC: Final[float] = 0.3


@dataclass(frozen=True)
class FleetPlan:
    """Per-vehicle draw results for one run.

    Attributes:
        params: Per-vehicle IDM parameter dicts (keys ``IDM_PARAM_ORDER``).
        is_av: AV tag per vehicle.
        complied: Once-per-run compliance draw per vehicle (always ``False``
            for non-AVs; an AV with ``complied=False`` ignores commands,
            CLAUDE.md §3.3).
        depart_s: Departure time per vehicle [s].
        depart_pos_m: Linear-x departure position [m] (ring: initial arc
            position; corridor: entry-edge insertion at 0).
        route: Route id per vehicle (``"main"``, ``"on<k>"``,
            ``"main_off<j>"``, ``"on<k>_off<j>"``; see
            :func:`build_corridor_plan`). Empty ⇒ every vehicle drives
            ``"main"``.
    """

    params: tuple[dict[str, float], ...]
    is_av: tuple[bool, ...]
    complied: tuple[bool, ...]
    depart_s: tuple[float, ...]
    depart_pos_m: tuple[float, ...]
    route: tuple[str, ...] = ()

    @property
    def n(self) -> int:
        """Number of vehicles in the plan."""
        return len(self.params)

    def route_of(self, i: int) -> str:
        """Route id of vehicle ``i`` (``"main"`` when no routes were assigned)."""
        return self.route[i] if self.route else "main"

    def vehicle_id(self, i: int) -> str:
        """Zero-padded vehicle id (lexicographic == numeric ordering)."""
        return f"v{i:05d}"

    @property
    def av_ids(self) -> tuple[str, ...]:
        """Ids of AV-tagged vehicles."""
        return tuple(self.vehicle_id(i) for i, a in enumerate(self.is_av) if a)

    @property
    def complied_ids(self) -> tuple[str, ...]:
        """Ids of AVs whose compliance draw succeeded."""
        return tuple(self.vehicle_id(i) for i, c in enumerate(self.complied) if c)


def resolve_calibration_path(path: str) -> Path:
    """Resolve an ``IDMCalibration`` artifact path (as given, else repo-root).

    Scenario YAMLs reference artifacts with repo-relative paths like
    ``artifacts/idm_us101.json``; runs launched from elsewhere still find
    them via the repository root (this file sits at
    ``packages/microsim/microsim/vehicles.py``).

    Raises:
        FileNotFoundError: Neither candidate exists.
    """
    p = Path(path)
    if p.is_file():
        return p
    candidate = Path(__file__).resolve().parents[3] / path
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"IDMCalibration artifact not found: {path!r} (also tried {candidate})")


def load_idm_calibration(path: str) -> IDMCalibration:
    """Load the ``IDMCalibration`` artifact referenced by a fleet spec."""
    return IDMCalibration.load(resolve_calibration_path(path))


def _draw_from_calibration(
    cal: IDMCalibration, n: int, rng: np.random.Generator
) -> list[dict[str, float]]:
    """Per-vehicle draws from a calibrated population (docs/CONTRACTS.md §2).

    Truncated multivariate normal: candidate vectors come from
    ``rng.multivariate_normal(mean, cov)`` and are accepted when every
    marginal lies within ±3σ of its mean (σ from the covariance diagonal)
    AND at or above the hard physical floors ``IDM_HARD_LOWER``. Vehicles are
    filled in index order; after 1000 batch attempts remaining draws fall
    back to the clipped mean (practically unreachable for a sane artifact).

    Args:
        cal: Loaded calibration artifact (order v0, T, a_max, b, s0).
        n: Number of vehicles.
        rng: Seeded generator (``flowstate_core.rng.make_rng``).
    """
    names = list(cal.param_names)
    mean = np.array([cal.mean[name] for name in names])
    cov = np.array(cal.cov, dtype=float)
    sigma = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    lo = np.maximum(mean - 3.0 * sigma, np.array([IDM_HARD_LOWER[k] for k in names]))
    hi = mean + 3.0 * sigma
    out: list[dict[str, float]] = []
    for _ in range(1000):
        if len(out) >= n:
            break
        batch = rng.multivariate_normal(mean, cov, size=n - len(out))
        ok = np.all((batch >= lo) & (batch <= hi), axis=1)
        out.extend(dict(zip(names, map(float, row), strict=True)) for row in batch[ok])
    fallback = np.clip(mean, lo, hi)
    while len(out) < n:  # pragma: no cover - degenerate artifact only
        out.append(dict(zip(names, map(float, fallback), strict=True)))
    return out[:n]


def draw_vehicle_params(
    fleet: FleetSpec, n: int, rng: np.random.Generator
) -> list[dict[str, float]]:
    """Draw heterogeneous per-vehicle IDM parameters (CLAUDE.md §3.1).

    Default path: each parameter is drawn from a truncated normal with
    ``σ = heterogeneity_frac · mean`` (±3σ truncation via
    :func:`flowstate_core.rng.truncated_normal`) and clipped at the hard
    physical lower bounds ``IDM_HARD_LOWER``. Draw order: vehicles outermost,
    ``IDM_PARAM_ORDER`` innermost — fixed for reproducibility.

    Calibrated path (docs/CONTRACTS.md §2): when ``fleet.idm_calibration``
    is set, the referenced ``IDMCalibration`` artifact's population
    mean/covariance OVERRIDE the scalar fields and ``heterogeneity_frac``,
    and vehicles draw from the truncated multivariate normal
    (:func:`_draw_from_calibration`) with the same run RNG.

    Args:
        fleet: Fleet spec carrying the population means and heterogeneity.
        n: Number of vehicles.
        rng: Seeded generator (``flowstate_core.rng.make_rng``).

    Returns:
        ``n`` parameter dicts with keys ``v0, T, a_max, b, s0`` (SI).
    """
    if fleet.idm_calibration is not None:
        return _draw_from_calibration(load_idm_calibration(fleet.idm_calibration), n, rng)
    means = {"v0": fleet.v0, "T": fleet.T, "a_max": fleet.a_max, "b": fleet.b, "s0": fleet.s0}
    out: list[dict[str, float]] = []
    for _ in range(n):
        p = {
            key: truncated_normal(
                rng,
                means[key],
                fleet.heterogeneity_frac * means[key],
                low=IDM_HARD_LOWER[key],
            )
            for key in IDM_PARAM_ORDER
        }
        out.append(p)
    return out


def tag_avs(n: int, av: AVSpec, rng: np.random.Generator) -> tuple[list[bool], list[bool]]:
    """Seeded AV tagging + once-per-run compliance draws (CLAUDE.md §3.3).

    ``round(penetration · n)`` vehicles are chosen without replacement; each
    chosen AV draws compliance once (Bernoulli ``p = av.compliance``). RNG
    order: one ``choice`` call, then one uniform per AV in vehicle-index
    order.

    Args:
        n: Fleet size.
        av: AV deployment spec.
        rng: Seeded generator.

    Returns:
        ``(is_av, complied)`` flag lists of length ``n``; ``complied[i]`` is
        ``False`` whenever ``is_av[i]`` is.
    """
    is_av = [False] * n
    complied = [False] * n
    n_avs = round(av.penetration * n)
    if n_avs <= 0:
        return is_av, complied
    chosen = sorted(int(i) for i in rng.choice(n, size=n_avs, replace=False))
    for i in chosen:
        is_av[i] = True
        complied[i] = bool(rng.uniform() < av.compliance)
    return is_av, complied


def build_ring_plan(
    network: RingNetwork, fleet: FleetSpec, av: AVSpec, rng: np.random.Generator
) -> FleetPlan:
    """Fleet plan for a ring: uniform spacing + tiny seeded jitter, depart 0.

    RNG order: (1) per-vehicle params, (2) AV tags + compliance,
    (3) per-vehicle position jitter ``U(−RING_JITTER_M, +RING_JITTER_M)``.
    """
    n = network.n_vehicles
    params = draw_vehicle_params(fleet, n, rng)
    is_av, complied = tag_avs(n, av, rng)
    spacing = network.circumference_m / n
    positions = [
        (i * spacing + float(rng.uniform(-RING_JITTER_M, RING_JITTER_M))) % network.circumference_m
        for i in range(n)
    ]
    return FleetPlan(
        params=tuple(params),
        is_av=tuple(is_av),
        complied=tuple(complied),
        depart_s=tuple(0.0 for _ in range(n)),
        depart_pos_m=tuple(positions),
    )


def corridor_departures(
    inflow: list[tuple[float, float]], duration_s: float, rng: np.random.Generator
) -> list[float]:
    """Departure times for a piecewise-constant inflow profile with jitter.

    Within each step of rate ``q`` [veh/s], nominal departures are equally
    spaced at headway ``1/q`` and each is jittered by
    ``U(−0.3, 0.3) · headway`` (seeded), keeping the realized flow equal to
    the profile while breaking metronomic insertion (CLAUDE.md §3.2.2 "waves
    from inflow noise").

    Args:
        inflow: Time-ordered ``(t_start [s], rate [veh/s])`` steps
            (``CorridorNetwork.inflow``).
        duration_s: Demand horizon [s]; the last step extends to it.
        rng: Seeded generator.

    Returns:
        Sorted departure times [s] in ``[0, duration_s)``.
    """
    times: list[float] = []
    for k, (t0, q) in enumerate(inflow):
        t1 = inflow[k + 1][0] if k + 1 < len(inflow) else duration_s
        t1 = min(t1, duration_s)
        if q <= 0.0 or t1 <= t0:
            continue
        headway = 1.0 / q
        n_veh = math.floor((t1 - t0) * q)
        for j in range(n_veh):
            t = t0 + (j + 0.5) * headway
            t += float(rng.uniform(-DEPART_JITTER_FRAC, DEPART_JITTER_FRAC)) * headway
            if 0.0 <= t < t1:
                times.append(t)
    return sorted(times)


def _step_value(steps: Sequence[tuple[float, float]], t: float) -> float:
    """Value of a time-ordered piecewise-constant step profile at ``t``."""
    value = 0.0
    for t_start, v in steps:
        if t >= t_start:
            value = v
        else:
            break
    return value


def build_corridor_plan(
    inflow: list[tuple[float, float]],
    duration_s: float,
    fleet: FleetSpec,
    av: AVSpec,
    rng: np.random.Generator,
    *,
    ramps: Sequence[RampSpec] = (),
    corridor_edges: Sequence[str] = (),
) -> FleetPlan:
    """Fleet plan for a corridor/OSM demand profile, optionally with ramps.

    Mainline vehicles depart on the corridor entry edge per ``inflow``;
    every on-ramp in ``ramps`` adds its own jittered departures
    (:func:`corridor_departures` on the ramp's ``inflow``); every vehicle
    that passes an off-ramp's ``attach_edge`` exits there with the ramp's
    ``exit_fraction`` at its departure time (one Bernoulli draw per
    off-ramp, in corridor order; the first success wins). Route ids:
    ``"main"``, ``"on<k>"`` (k-th ramp in config order), ``"main_off<j>"``,
    ``"on<k>_off<j>"``. Vehicle ids follow the concatenation order
    (mainline departures sorted, then each on-ramp's sorted departures).

    RNG order: (1) mainline departure times with jitter, (2) each on-ramp's
    departure times in config order, (3) per-vehicle params, (4) AV tags +
    compliance, (5) off-ramp exit draws per vehicle in id order, per
    reachable off-ramp in corridor order (skipped entirely when there are no
    ramps, so plans without ramps consume the RNG exactly as before).

    Args:
        inflow: Mainline ``(t_start [s], veh/s)`` steps.
        duration_s: Demand horizon [s].
        fleet: Human-fleet spec.
        av: AV deployment spec.
        rng: Seeded generator.
        ramps: ``RampSpec`` list (``OSMNetwork.ramps``).
        corridor_edges: Corridor edge ids in driving order (needed to order
            ramps along the corridor; required when ``ramps`` is non-empty).

    Returns:
        The :class:`FleetPlan` (``route`` populated iff ``ramps`` is given).

    Raises:
        ValueError: A ramp's ``attach_edge`` is not a corridor edge.
    """
    departs = corridor_departures(inflow, duration_s, rng)
    origin_idx = [-1] * len(departs)  # -1 = mainline, k = on-ramp k
    for k, ramp in enumerate(ramps):
        if ramp.kind != "on":
            continue
        d = corridor_departures(list(ramp.inflow), duration_s, rng)
        departs += d
        origin_idx += [k] * len(d)
    n = len(departs)
    params = draw_vehicle_params(fleet, n, rng)
    is_av, complied = tag_avs(n, av, rng)

    routes: tuple[str, ...] = ()
    if ramps:
        pos = {e: i for i, e in enumerate(corridor_edges)}
        for ramp in ramps:
            if ramp.attach_edge not in pos:
                raise ValueError(f"ramp attach_edge {ramp.attach_edge!r} not in corridor_edges")
        entry_idx = {k: pos[r.attach_edge] for k, r in enumerate(ramps) if r.kind == "on"}
        offs = sorted((pos[r.attach_edge], j) for j, r in enumerate(ramps) if r.kind == "off")
        route_list: list[str] = []
        for i in range(n):
            k = origin_idx[i]
            base = "main" if k < 0 else f"on{k}"
            entry = -1 if k < 0 else entry_idx[k]
            chosen = ""
            for attach_pos, j in offs:
                if attach_pos < entry:
                    continue  # off-ramp upstream of this vehicle's entry point
                frac = _step_value(ramps[j].exit_fraction, departs[i])
                if float(rng.uniform()) < frac:
                    chosen = f"_off{j}"
                    break
            route_list.append(base + chosen)
        routes = tuple(route_list)
    return FleetPlan(
        params=tuple(params),
        is_av=tuple(is_av),
        complied=tuple(complied),
        depart_s=tuple(departs),
        depart_pos_m=tuple(0.0 for _ in range(n)),
        route=routes,
    )


def ramp_routes(
    corridor_edges: Sequence[str], ramps: Sequence[RampSpec]
) -> dict[str, tuple[str, ...]]:
    """Edge lists for every route id :func:`build_corridor_plan` can assign.

    ``"main"`` is the corridor; ``"on<k>"`` is the on-ramp's edges followed
    by the corridor from its ``attach_edge``; ``"main_off<j>"`` is the
    corridor up to and including the off-ramp's ``attach_edge`` followed by
    the off-ramp's edges; ``"on<k>_off<j>"`` combines both (only when the
    off-ramp is at or downstream of the on-ramp's attach edge).
    """
    corridor = list(corridor_edges)
    pos = {e: i for i, e in enumerate(corridor)}
    routes: dict[str, tuple[str, ...]] = {"main": tuple(corridor)}
    ons = [(k, r) for k, r in enumerate(ramps) if r.kind == "on"]
    offs = [(j, r) for j, r in enumerate(ramps) if r.kind == "off"]
    for j, off in offs:
        routes[f"main_off{j}"] = tuple(corridor[: pos[off.attach_edge] + 1] + list(off.edges))
    for k, on in ons:
        start = pos[on.attach_edge]
        routes[f"on{k}"] = tuple(list(on.edges) + corridor[start:])
        for j, off in offs:
            end = pos[off.attach_edge]
            if end >= start:
                routes[f"on{k}_off{j}"] = tuple(
                    list(on.edges) + corridor[start : end + 1] + list(off.edges)
                )
    return routes


def _vtype_xml(
    type_id: str,
    p: dict[str, float],
    model: str,
    action_step_s: float,
    lc_strategic: float = 1.0,
    lc_keep_right: float = 1.0,
    lc_cooperative: float = 1.0,
    lc_assertive: float = 1.0,
    lc_speed_gain: float = 1.0,
) -> str:
    """One ``<vType>`` element (see module docstring for attribute notes).

    The lane-change attributes (``lcStrategic``, ``lcKeepRight``,
    ``lcCooperative``, ``lcAssertive``, ``lcSpeedGain`` from the matching
    ``FleetSpec.lc_*`` fields) are written only when they differ from SUMO's
    default 1.0, so route files of existing scenarios stay byte-identical.
    """
    lc = "" if lc_strategic == 1.0 else f' lcStrategic="{lc_strategic:g}"'
    lc += "" if lc_keep_right == 1.0 else f' lcKeepRight="{lc_keep_right:g}"'
    lc += "" if lc_cooperative == 1.0 else f' lcCooperative="{lc_cooperative:g}"'
    lc += "" if lc_assertive == 1.0 else f' lcAssertive="{lc_assertive:g}"'
    lc += "" if lc_speed_gain == 1.0 else f' lcSpeedGain="{lc_speed_gain:g}"'
    return (
        f'  <vType id="{type_id}" carFollowModel="{model}" accel="{p["a_max"]:.6f}" '
        f'decel="{p["b"]:.6f}" tau="{p["T"]:.6f}" minGap="{p["s0"]:.6f}" '
        f'maxSpeed="{p["v0"]:.6f}" length="{VEHICLE_LENGTH_M}" speedFactor="1.0" '
        f'speedDev="0" emissionClass="{EMISSION_CLASS}" '
        f'actionStepLength="{action_step_s}"{lc}/>'
    )


def write_ring_routes(
    bundle_edge_ids: tuple[str, ...],
    bundle_offsets: tuple[float, ...],
    circumference_m: float,
    plan: FleetPlan,
    model: str,
    action_step_s: float,
    duration_s: float,
    path: Path,
) -> Path:
    """Write ring routes: explicit depart-at-0 vehicles at planned positions.

    Each vehicle's route is the ring edge cycle (rotated to start at its
    departure edge) repeated enough times to cover the whole simulation at
    the fastest drawn desired speed — SUMO routes cannot loop, so the loop is
    unrolled (standard ring-benchmark practice).

    Args:
        bundle_edge_ids: Ring edge ids in loop order.
        bundle_offsets: Cumulative offsets matching ``bundle_edge_ids``.
        circumference_m: Ring circumference [m].
        plan: Fleet plan from :func:`build_ring_plan`.
        model: ``"IDM"`` or ``"EIDM"``.
        action_step_s: vType ``actionStepLength`` [s].
        duration_s: Simulation duration [s] (sizes the route unroll).
        path: Output ``.rou.xml`` path.

    Returns:
        ``path``.
    """
    v_max = max(p["v0"] for p in plan.params)
    laps = math.ceil(duration_s * v_max / circumference_m) + 2
    n_seg = len(bundle_edge_ids)
    seg_len = circumference_m / n_seg

    lines = ["<routes>"]
    for i, p in enumerate(plan.params):
        lines.append(_vtype_xml(f"t{i:05d}", p, model, action_step_s))
    for i in range(plan.n):
        pos = plan.depart_pos_m[i] % circumference_m
        e_idx = min(int(pos // seg_len), n_seg - 1)
        lane_pos = pos - bundle_offsets[e_idx]
        cycle = list(bundle_edge_ids[e_idx:]) + list(bundle_edge_ids[:e_idx])
        route = " ".join(cycle * laps)
        lines.append(
            f'  <vehicle id="{plan.vehicle_id(i)}" type="t{i:05d}" depart="0.00" '
            f'departPos="{lane_pos:.4f}" departSpeed="0" departLane="0">'
        )
        lines.append(f'    <route edges="{route}"/>')
        lines.append("  </vehicle>")
    lines.append("</routes>")
    path.write_text("\n".join(lines))
    return path


def write_corridor_routes(
    route_edge_ids: tuple[str, ...],
    plan: FleetPlan,
    model: str,
    action_step_s: float,
    path: Path,
    depart_edge_spread: int = 1,
    lanes: int = 1,
    routes: Mapping[str, Sequence[str]] | None = None,
    lc_strategic: float = 1.0,
    lc_keep_right: float = 1.0,
    lc_cooperative: float = 1.0,
    lc_assertive: float = 1.0,
    lc_speed_gain: float = 1.0,
) -> Path:
    """Write corridor demand: explicit jittered departures.

    Vehicles are sorted by departure time (SUMO requirement) and enter on the
    first route edge. The insertion attributes depend on the lane count:

    * ``lanes == 1`` — ``departLane="free" departPos="free"
      departSpeed="max"``. Free positioning on a long entry edge is what lets
      a single lane sustain near-capacity demand: with a fixed insertion
      point the realized inflow collapses to ~1200 veh/h under oversaturated
      demand, while free insertion on a 2 km entry reaches ~1960 veh/h
      (measured, SUMO 1.27.1) — the runner sizes the entry edge accordingly.
    * ``lanes > 1`` (and ``depart_edge_spread == 1``, the physical
      boundary-inflow path) — per-vehicle ``departLane`` assigned round-robin
      across lanes in departure order, ``departPos="base"`` (edge start, so
      every vehicle traverses the full insertion buffer and the demand timing
      profile is preserved up to a near-constant lag) and
      ``departSpeed="avg"`` (prevailing lane speed). This is the M3 fix for
      the multi-lane insertion-throughput ceiling documented in
      docs/M2_RESULTS.md §6/§7.7: full-speed free-position insertion needs a
      leader+follower secure-gap slot of ~2·(s0 + v·T) ≈ 85 m per vehicle,
      which caps realized inflow at ~73–86% of the planned 2.29 veh/s on the
      5-lane US-101 replica, while lane-pinned edge-start insertion at the
      prevailing speed realizes 100.0% of planned insertions (2591–2592 of
      2592, seed 42, SUMO 1.27.1, all insertion safety checks ON, zero
      collisions) with per-5-min span-entry counts tracking the planned
      profile. All insertion happens on the entry buffer edge, outside the
      measurement span, so measured-span physics is unchanged. When
      ``depart_edge_spread != 1`` (the smoke/initial-condition fill path,
      not a physical boundary inflow) free positioning fills the corridor
      better and the single-lane scheme is kept.

    Args:
        route_edge_ids: Full route, entry edge first, upstream → downstream.
        plan: Fleet plan from :func:`build_corridor_plan`.
        model: ``"IDM"`` or ``"EIDM"``.
        action_step_s: vType ``actionStepLength`` [s].
        path: Output ``.rou.xml`` path.
        depart_edge_spread: Number of leading route edges vehicles enter on,
            assigned round-robin in departure order via SUMO's ``departEdge``
            (0 ⇒ all route edges). The default 1 keeps every insertion on the
            upstream entry edge (the physical boundary-inflow scenario);
            larger values fill the corridor from many points at once, which
            is how a smoke/initial-condition run gets past SUMO's per-edge
            insertion throughput (~1.2–2.4 veh/s under backlog, measured).
        lanes: Lane count of the corridor (selects the insertion attribute
            scheme above; the caller passes ``CorridorNetwork.lanes``).
        routes: Named routes (id → edge ids) when the plan carries ramp
            routes (:func:`ramp_routes`); ``None`` ⇒ only ``"main"`` =
            ``route_edge_ids``. Vehicles on an on-ramp route (``"on…"``)
            are inserted on the ramp's first edge with ``departPos="base"
            departSpeed="avg" departLane="free"``; mainline insertion
            ranks (lane round-robin) count mainline vehicles only.
        lc_strategic: ``FleetSpec.lc_strategic`` (SUMO ``lcStrategic``),
            written on every vType when it differs from 1.0.
        lc_keep_right: ``FleetSpec.lc_keep_right`` (SUMO ``lcKeepRight``),
        lc_cooperative: ``FleetSpec.lc_cooperative`` (SUMO ``lcCooperative``).
        lc_assertive: ``FleetSpec.lc_assertive`` (SUMO ``lcAssertive``).
        lc_speed_gain: ``FleetSpec.lc_speed_gain`` (SUMO ``lcSpeedGain``).
            likewise.

    Returns:
        ``path``.

    Raises:
        ValueError: A plan route id has no entry in ``routes``.
    """
    n_route = len(route_edge_ids)
    spread = n_route if depart_edge_spread == 0 else min(max(depart_edge_spread, 1), n_route)
    order = sorted(range(plan.n), key=lambda i: plan.depart_s[i])
    named: dict[str, tuple[str, ...]] = {"main": tuple(route_edge_ids)}
    if routes is not None:
        named.update({rid: tuple(edges) for rid, edges in routes.items()})
    lines = ["<routes>"]
    for i, p in enumerate(plan.params):
        lines.append(
            _vtype_xml(
                f"t{i:05d}",
                p,
                model,
                action_step_s,
                lc_strategic,
                lc_keep_right,
                lc_cooperative,
                lc_assertive,
                lc_speed_gain,
            )
        )
    for rid, edges in named.items():
        lines.append(f'  <route id="{rid}" edges="{" ".join(edges)}"/>')
    rank_main = 0
    for i in order:
        rid = plan.route_of(i)
        if rid not in named:
            raise ValueError(f"vehicle {plan.vehicle_id(i)} has unknown route {rid!r}")
        if rid.startswith("on"):
            depart_edge = ""
            depart_attrs = 'departPos="base" departSpeed="avg" departLane="free"'
        else:
            rank = rank_main
            rank_main += 1
            depart_edge = "" if spread == 1 else f'departEdge="{rank % spread}" '
            if lanes > 1 and spread == 1:
                depart_attrs = f'departPos="base" departSpeed="avg" departLane="{rank % lanes}"'
            else:
                depart_attrs = 'departPos="free" departSpeed="max" departLane="free"'
        lines.append(
            f'  <vehicle id="{plan.vehicle_id(i)}" type="t{i:05d}" '
            f'depart="{plan.depart_s[i]:.3f}" {depart_edge}{depart_attrs} route="{rid}"/>'
        )
    lines.append("</routes>")
    path.write_text("\n".join(lines))
    return path
