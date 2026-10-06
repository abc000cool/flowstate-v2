"""The measured merge model's pure functions (``RampSpec.merge = "measured"``).

docs/MERGE_MODEL.md is the specification; section numbers ``B§`` refer to its
research brief, docs/MERGE_MODEL_BRIEF.md. The model drives the **mandatory
lane changes inside merge zones** — acceleration lanes of on-ramps and weaving
sections, both movements — on three measured principles that ship together
(B§5.1):

1. **gap acceptance from measured critical gaps** — per driver, one lead and
   one lag critical time gap per movement, drawn once from the measured
   log-normal fits (B§1.1), compared with bumper-to-bumper gaps as
   ``calibration.lane_change_gaps`` defines them, plus brake guards on both
   sides (:func:`acceptance`); amended (A1.2, 2026-10-06): the changer's own
   model on the lead side (A1.1's speed condition was withdrawn by A2.1);
2. **matching the target lane's speed** — a speed *ceiling* at the chosen
   gap's speed plus the measured offset δ, approached kinematically, the
   desired speed ``speedFactor × lane limit`` capped by ``maxSpeed``
   (:func:`speed_ceiling`, :func:`desired_speed`); amended (A3, 2026-10-06):
   only towards a chosen gap with a leader — with no gap chosen no ceiling is
   set (:func:`gap_reference_speed`);
3. **relaxation after the crossing** — the entrant and its new follower get a
   temporary time headway equal to the accepted gap (floored), recovering to
   their own ``T`` with the measured time constant (:func:`relaxation_start`,
   :func:`relaxed_tau`).

Everything here is controller-style: plain numbers in, plain numbers out, no
SUMO, no I/O except :func:`load_params` (the parameter artifact). The runner
(``microsim.runner``, ``_measured_step``) reads SUMO, calls these and writes
SUMO back. SI units throughout (m, s, m/s, m/s²).

Gap conventions (B§5.5). SUMO's ``vehicle.getNeighbors`` / ``getLeader`` /
``getFollower`` report gaps **net of a minimum gap**: the leader side net of
the changer's ``minGap``, the follower side net of the follower's own
(probed on SUMO 1.27.1, 2026-10-05: ``getNeighbors`` leader gap + changer
``minGap`` and follower gap + follower ``minGap`` equal the bumper-to-bumper
distances to the metre's thousandth). The measured critical gaps are
bumper-to-bumper time gaps — the lead gap over the changer's speed, the lag
gap over the follower's speed (``calibration.lane_change_gaps``) — so the
time gates add the ``minGap`` back (:func:`lead_time_ok`,
:func:`lag_time_ok`). The brake guards read SUMO's net gaps directly: they
are the forced-change guard of the scripted merge (WP-93,
``microsim.runner._scripted_force_gap_ok``), at equal speeds SUMO's own
overlap test.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Final, Literal

import numpy as np

# --- Fixed constants of the model (docs/MERGE_MODEL.md §2, "Kept from the
# weave as fixed constants"; B§5.10). Each is the weave's load-bearing value
# on record, not a fitted one, and is never tuned per corridor (protocol
# §7.4). ----------------------------------------------------------------------

#: The forced zone: the last 80 m of a zone, after 4 s inside it (B§2.3:
#: removing forcing gave 16–22 give-ups; every short-section scaling was
#: rejected, WEAVE_MODEL_PLAN L3017 / L4139). A forced change executes only
#: through the brake guards (B§5.6: today's ``force_guard`` made
#: unconditional).
FORCE_WITHIN_M: Final[float] = 80.0
FORCE_AFTER_S: Final[float] = 4.0
#: Pair release: a standing changer–follower pair is released after two
#: reaction times (B§2.3, the fifth derivation; without it seeds 3–4 lock).
PAIR_RELEASE_S: Final[float] = 2.0
#: Exit give-up: a halted exiter within one vehicle length of the gore's end
#: is rerouted through (B§2.3, the exit-side derivation).
EXIT_GIVEUP_M: Final[float] = 5.0
#: Vacate window: through traffic in the weave lane is asked to move left
#: within 500 m of the section, bounded by the target lane's spare capacity
#: (B§2.3, the third derivation; HCM 150 m / MUTCD 800 m bracket it).
VACATE_AHEAD_M: Final[float] = 500.0
#: ``exit_prepare`` on: exiters asked into the lane feeding section lane 1
#: inside the vacate window (B§2.3, on in the I-94 reference configuration).
EXIT_PREPARE: Final[bool] = True
#: The lane-end give-up at every diverge the zones do not cover, always on
#: with the model (B§5.2): one vehicle's room, 5 m + the corridor
#: population's mean minimum gap 2.53 m, rounded (WP-71).
LANE_END_GIVEUP_M: Final[float] = 7.5
#: Creep: the ceiling's floor [m/s], so a stopped target lane never freezes a
#: zone (B§2.3, ``SCRIPTED_MERGE_CREEP_MS``).
CREEP_MS: Final[float] = 3.0
#: Lookahead: how far behind a changer its gap's follower may be, and the
#: ramp's last stretch in which an entrant is anticipated (B§2.3; the ramp
#: anticipation is load-bearing, removal −40…−86 entrants).
LOOKAHEAD_M: Final[float] = 120.0
#: Request re-issue [s]: the scripted merge's ``change_duration_s``. Kept as
#: the recorded constant (B§5.10); under the one-step execution of B§5.6 a
#: change is requested on every accepting step and no request lives longer
#: than one step, so the interval does not bind (docs/MERGE_MODEL.md
#: deviations, in the implementation report).
REQUEST_REISSUE_S: Final[float] = 2.0
#: The vacate rule's spare-capacity bound: one IDM lane at the fleet defaults
#: (B§2.3, ``microsim.runner.VACATE_LANE_CAPACITY_VEH_H``).
VACATE_LANE_CAPACITY_VEH_H: Final[float] = 2050.0

#: Relaxation floor as a fraction of the driver's own ``T`` (docs/MERGE_MODEL.md
#: §2: ``T_eff ≥ 0.5·T_i``; near the lower end of US-101's leader-side start
#: r0 0.54 [0.48, 0.61], B§5.11). SUMO's step length is the other floor.
RELAX_FLOOR_FRACTION: Final[float] = 0.5
#: Relaxation is restored at this many time constants (98 % relaxed;
#: docs/MERGE_MODEL.md §2).
RELAX_RESTORE_TAU_R: Final[float] = 4.0
#: The car-following range within which a new follower is relaxed: bumper
#: gap at most 10 m + 5 s × its speed (WP-88's bound, the one the relaxation
#: extraction walks with; WEAVE_MODEL_PLAN WP-90 "the pair").
CAR_FOLLOWING_GAP_M: Final[float] = 10.0
CAR_FOLLOWING_TIME_S: Final[float] = 5.0
#: Truncation of every critical-gap draw: [p2.5, p97.5] of its log-normal
#: (B§5.3, so the 0.07 s tail does not leave all the safety to the guard).
TRUNCATION_QUANTILE: Final[float] = 0.975
TRUNCATION_Z: Final[float] = NormalDist().inv_cdf(TRUNCATION_QUANTILE)
#: Tolerance [m/s] below which a follow speed is not read as a harder brake.
FOLLOW_SPEED_EPS_MS: Final[float] = 1e-9

#: The movements of the model (B§5.3): entering an acceleration lane's target
#: lane, entering from a weave's auxiliary lane, exiting into it.
Movement = Literal["entering_merge", "entering_weave", "exiting_weave"]
MOVEMENTS: Final[tuple[Movement, ...]] = ("entering_merge", "entering_weave", "exiting_weave")

#: Pre-registered parameter sets (docs/MERGE_MODEL.md §2): the central set and
#: the sensitivity arms — US-101 critical gaps, δ = 0, τ_r at its interval's
#: two ends. Written by ``scripts/merge_model_params.py``.
PARAMETER_SETS: Final[tuple[str, ...]] = (
    "central",
    "us101_gaps",
    "delta_zero",
    "tau_r_low",
    "tau_r_high",
)

#: The committed parameter artifact the runner reads (repo-relative).
PARAMS_ARTIFACT: Final[str] = "artifacts/merge_model_params.json"


def params_artifact_path() -> Path:
    """:data:`PARAMS_ARTIFACT` resolved against the repository root (this file's
    ``packages/microsim/microsim/`` is three levels below it), so a run launched
    from anywhere reads the committed file."""
    return Path(__file__).resolve().parents[3] / PARAMS_ARTIFACT


# --- Parameters ---------------------------------------------------------------


@dataclass(frozen=True)
class LogNormal:
    """A log-normal critical-gap distribution: ``ln t ~ N(mu, sigma²)`` [s]."""

    mu: float
    sigma: float

    @property
    def median_s(self) -> float:
        """The distribution's median ``exp(mu)`` [s]."""
        return math.exp(self.mu)

    def at(self, z: float) -> float:
        """The gap at standard-normal quantile ``z``: ``exp(mu + sigma·z)`` [s]."""
        return math.exp(self.mu + self.sigma * z)

    def bounds(self, z_max: float = TRUNCATION_Z) -> tuple[float, float]:
        """The truncation interval ``[at(−z_max), at(z_max)]`` [s]."""
        return self.at(-z_max), self.at(z_max)


@dataclass(frozen=True)
class MergeModelParams:
    """One parameter set of the model (``artifacts/merge_model_params.json``).

    Attributes:
        name: The set's name (:data:`PARAMETER_SETS`).
        lead: Lead critical-gap distribution per movement; ``None`` for a
            movement with no lead time gate (exiting: the lead brake guard
            only, docs/MERGE_MODEL.md §2).
        lag: Lag critical-gap distribution per movement.
        delta_merge_ms: Speed offset δ over the chosen gap's speed on an
            acceleration lane [m/s].
        delta_weave_ms: The same in a weaving section [m/s].
        tau_r_s: Relaxation time constant [s].
        relax_floor_fraction: ``T_eff ≥`` this × ``T_i`` (and ≥ the step).
        relax_restore_tau_r: Relaxation restored after this many ``tau_r``.
        truncation_z: Standard-normal truncation of every draw.
        artifact: Path the set was read from ("" when built in code).
        artifact_sha256: That file's sha256 ("" when built in code).
    """

    name: str
    lead: Mapping[str, LogNormal | None]
    lag: Mapping[str, LogNormal]
    delta_merge_ms: float
    delta_weave_ms: float
    tau_r_s: float
    relax_floor_fraction: float = RELAX_FLOOR_FRACTION
    relax_restore_tau_r: float = RELAX_RESTORE_TAU_R
    truncation_z: float = TRUNCATION_Z
    artifact: str = ""
    artifact_sha256: str = ""

    def delta(self, weave: bool) -> float:
        """δ of the zone kind [m/s]."""
        return self.delta_weave_ms if weave else self.delta_merge_ms

    def summary(self) -> dict[str, Any]:
        """The values in ``meta.json`` form."""

        def ln(d: LogNormal | None) -> dict[str, float] | None:
            if d is None:
                return None
            lo, hi = d.bounds(self.truncation_z)
            return {
                "mu": d.mu,
                "sigma": d.sigma,
                "median_s": d.median_s,
                "truncated_to_s": [lo, hi],
            }

        return {
            "set": self.name,
            "critical_gaps": {
                m: {"lead": ln(self.lead.get(m)), "lag": ln(self.lag.get(m))} for m in MOVEMENTS
            },
            "delta_ms": {"merge": self.delta_merge_ms, "weave": self.delta_weave_ms},
            "tau_r_s": self.tau_r_s,
            "relax_floor_fraction": self.relax_floor_fraction,
            "relax_restore_tau_r": self.relax_restore_tau_r,
            "truncation_z": self.truncation_z,
        }


def file_sha256(path: str | Path) -> str:
    """sha256 of a file's bytes (hex)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def params_from_set(name: str, raw: Mapping[str, Any]) -> MergeModelParams:
    """A :class:`MergeModelParams` from one set of the artifact's ``sets``."""
    gaps = raw["critical_gaps"]

    def ln(d: Mapping[str, Any] | None) -> LogNormal | None:
        return None if d is None else LogNormal(float(d["mu"]), float(d["sigma"]))

    lead: dict[str, LogNormal | None] = {m: ln(gaps[m]["lead"]) for m in MOVEMENTS}
    lag: dict[str, LogNormal] = {}
    for m in MOVEMENTS:
        d = ln(gaps[m]["lag"])
        if d is None:
            raise ValueError(f"parameter set {name!r}: movement {m} needs a lag distribution")
        lag[m] = d
    relax = raw["relaxation"]
    return MergeModelParams(
        name=name,
        lead=lead,
        lag=lag,
        delta_merge_ms=float(raw["delta_ms"]["merge"]["value"]),
        delta_weave_ms=float(raw["delta_ms"]["weave"]["value"]),
        tau_r_s=float(relax["tau_r_s"]["value"]),
        relax_floor_fraction=float(relax["floor_fraction"]["value"]),
        relax_restore_tau_r=float(relax["restore_after_tau_r"]["value"]),
        truncation_z=NormalDist().inv_cdf(float(raw["truncation"]["upper_quantile"])),
    )


def load_params(path: str | Path, set_name: str = "central") -> MergeModelParams:
    """Read one parameter set from the artifact ``scripts/merge_model_params.py`` writes.

    Args:
        path: The artifact (``artifacts/merge_model_params.json``).
        set_name: One of the artifact's ``sets`` (:data:`PARAMETER_SETS`).

    Returns:
        The set, with the artifact's path and sha256 recorded on it.

    Raises:
        ValueError: The artifact has no such set.
    """
    p = Path(path)
    raw = json.loads(p.read_text())
    sets = raw.get("sets") or {}
    if set_name not in sets:
        raise ValueError(f"{p}: no parameter set {set_name!r} (has {sorted(sets)})")
    return dataclasses.replace(
        params_from_set(set_name, sets[set_name]),
        artifact=str(path),
        artifact_sha256=file_sha256(p),
    )


# --- Driver draws (B§5.3) -------------------------------------------------------


def driver_quantiles(
    rng: np.random.Generator, n: int, z_max: float = TRUNCATION_Z
) -> tuple[np.ndarray, np.ndarray]:
    """Per driver, one lead and one lag standard-normal quantile, truncated to ±``z_max``.

    Consistent drivers (B§5.3): one draw per driver and side, matching the
    estimator (WP-78), mapped through each movement's log-normal
    (:func:`driver_gaps`) — a driver who accepts short gaps at a merge also
    does in a weave. Inverse-CDF sampling: ``z = Φ⁻¹(u)`` with ``u`` uniform
    on ``[Φ(−z_max), Φ(z_max)]``, two uniforms per vehicle in index order
    (lead, lag), so the draw is exactly truncated and uses a fixed number of
    variates per vehicle. ``rng`` must be the model's own stream
    (``microsim.vehicles.merge_gap_stream``) so no existing draw moves.

    Args:
        rng: The model's generator.
        n: Number of vehicles.
        z_max: Truncation quantile (default [p2.5, p97.5]).

    Returns:
        ``(z_lead, z_lag)``, each of length ``n``.
    """
    nd = NormalDist()
    lo, hi = nd.cdf(-z_max), nd.cdf(z_max)
    u = rng.uniform(lo, hi, size=(n, 2))
    z = np.vectorize(nd.inv_cdf, otypes=[float])(u) if n else np.zeros((0, 2))
    return np.asarray(z[:, 0], dtype=float), np.asarray(z[:, 1], dtype=float)


@dataclass(frozen=True)
class DriverGaps:
    """One driver's critical time gaps [s] per movement (``None``: no lead time gate)."""

    lead: Mapping[str, float | None]
    lag: Mapping[str, float]


def driver_gaps(params: MergeModelParams, z_lead: float, z_lag: float) -> DriverGaps:
    """A driver's critical gaps from its two quantiles (:func:`driver_quantiles`)."""
    lead: dict[str, float | None] = {}
    for m in MOVEMENTS:
        d = params.lead.get(m)
        lead[m] = None if d is None else d.at(z_lead)
    lag = {m: params.lag[m].at(z_lag) for m in MOVEMENTS}
    return DriverGaps(lead=lead, lag=lag)


# --- Acceptance (B§5.5) ---------------------------------------------------------


def lead_time_ok(g_lead_net: float, s0_changer: float, v_changer: float, t_c: float) -> bool:
    """Lead critical gap: ``g_L ≥ t_c · v_C`` on the bumper-to-bumper gap.

    ``g_lead_net`` is SUMO's leader-side gap (net of the changer's
    ``minGap``); the changer's ``minGap`` is added back (module docstring).
    ``inf`` (no leader) passes.
    """
    if g_lead_net == math.inf:
        return True
    return g_lead_net + s0_changer >= t_c * v_changer


def lag_time_ok(g_foll_net: float, s0_follower: float, v_follower: float, t_c: float) -> bool:
    """Lag critical gap: ``g_F ≥ t_c · v_F`` on the bumper-to-bumper gap.

    ``g_foll_net`` is SUMO's follower-side gap (net of the follower's own
    ``minGap``, which is added back). ``inf`` (no follower) passes.
    """
    if g_foll_net == math.inf:
        return True
    return g_foll_net + s0_follower >= t_c * v_follower


def brake_guard_ok(
    g_net: float, v_rear: float, v_front: float, b_rear: float, step_s: float
) -> bool:
    """The brake guard of one side: the party behind can brake for the party in front at its own ``b``.

    ``g − c·Δt > c² / (2·b_rear)`` with ``c = (v_rear − v_front)⁺``, on SUMO's
    net gap: the scripted merge's forced-change guard (WP-93,
    ``microsim.runner._scripted_force_gap_ok``), whose ``c·Δt`` is the step
    order — the guard reads the state after a step, SUMO executes the change
    after the next step's movement. At equal speeds it is SUMO's own overlap
    test (a positive net gap). ``inf`` (no vehicle) passes.
    """
    if g_net == math.inf:
        return True
    c = max(v_rear - v_front, 0.0)
    return g_net - c * step_s > c * c / (2.0 * b_rear)


@dataclass(frozen=True)
class Acceptance:
    """The acceptance of one change, side by side (:func:`acceptance`).

    ``lead_model`` / ``lag_model`` are ``None`` until the changer's / the
    follower's own model has been asked (the runner asks SUMO only when every
    other condition passes, the changer first).
    """

    lead_time: bool
    lead_guard: bool
    lag_time: bool
    lag_guard: bool
    lead_model: bool | None = None
    lag_model: bool | None = None

    @property
    def static_ok(self) -> bool:
        """Every condition the model reads without asking SUMO."""
        return self.lead_time and self.lead_guard and self.lag_time and self.lag_guard

    @property
    def accepted(self) -> bool:
        """The change is accepted (both models' checks included)."""
        return self.static_ok and self.lead_model is not False and self.lag_model is not False

    @property
    def guards_ok(self) -> bool:
        """Both brake guards: what a forced change needs (B§5.6; exempt from A1.2)."""
        return self.lead_guard and self.lag_guard

    def refusal(self) -> str | None:
        """The first refused condition, in the order evaluated (``None`` if accepted)."""
        for name in ("lead_time", "lead_guard", "lag_time", "lag_guard"):
            if not getattr(self, name):
                return name
        if self.lead_model is False:
            return "lead_model"
        if self.lag_model is False:
            return "lag_model"
        return None


def acceptance(
    *,
    v_c: float,
    s0_c: float,
    b_c: float,
    t_c_lead: float | None,
    t_c_lag: float,
    g_lead: float,
    v_lead: float,
    g_foll: float,
    v_foll: float,
    s0_f: float,
    b_f: float,
    step_s: float,
) -> Acceptance:
    """The static acceptance of one change from SUMO's target-lane gaps (B§5.5).

    * lead: ``g_L ≥ t_cL · v_C`` (bumper to bumper; skipped when
      ``t_c_lead`` is ``None`` — the exiting movement) and the brake guard
      of the changer on the leader at ``b_C``;
    * lag: ``g_F ≥ t_cG · v_F`` and the brake guard of the follower on the
      changer at ``b_F``.

    The two model conditions — the changer's own model at its relaxed ``T``
    (its own when exiting) does not brake harder than ``b_C`` behind the new
    leader (amendment A1.2), and the follower's own model at its relaxed
    ``T`` does not brake harder than ``b_F`` behind the changer — are SUMO's
    to answer (``vehicle.getFollowSpeed``) and are added by the caller with
    :func:`follow_speed_ok`. A forced change reads the brake guards alone
    (:attr:`Acceptance.guards_ok`).

    Args:
        v_c: The changer's speed [m/s].
        s0_c: Its ``minGap`` [m].
        b_c: Its comfortable deceleration [m/s²].
        t_c_lead: Its lead critical gap for the movement [s]; ``None``: no gate.
        t_c_lag: Its lag critical gap for the movement [s].
        g_lead: SUMO's net leader gap [m] (``inf`` without a leader).
        v_lead: The leader's speed [m/s] (``nan`` without one).
        g_foll: SUMO's net follower gap [m] (``inf`` without a follower).
        v_foll: The follower's speed [m/s] (``nan`` without one).
        s0_f: The follower's ``minGap`` [m] (ignored without one).
        b_f: Its comfortable deceleration [m/s²] (ignored without one).
        step_s: The simulation step [s].
    """
    lead_time = True if t_c_lead is None else lead_time_ok(g_lead, s0_c, v_c, t_c_lead)
    lead_guard = brake_guard_ok(g_lead, v_c, v_lead, b_c, step_s)
    lag_time = lag_time_ok(g_foll, s0_f, v_foll, t_c_lag)
    lag_guard = brake_guard_ok(g_foll, v_foll, v_c, b_f, step_s)
    return Acceptance(lead_time, lead_guard, lag_time, lag_guard)


def follow_speed_ok(
    v_follow: float, v_f: float, b_f: float, step_s: float, eps: float = FOLLOW_SPEED_EPS_MS
) -> bool:
    """A vehicle's own model asks no harder a brake than its ``b`` behind a leader.

    ``v_follow`` is SUMO's ``vehicle.getFollowSpeed`` for the vehicle — the
    follower at its relaxed ``T`` with the changer as leader (B§5.5), or the
    changer at its relaxed ``T`` (entering) / its own (exiting) behind its
    new leader (amendment A1.2) — read from SUMO itself, so the IDM / EIDM
    closed forms cannot disagree (WP-68).
    """
    return v_follow >= v_f - b_f * step_s - eps


# --- Speed (B§5.4) --------------------------------------------------------------


def desired_speed(speed_factor: float, lane_limit_ms: float, max_speed_ms: float) -> float:
    """SUMO's desired free speed of a vehicle on a lane: ``min(maxSpeed, speedFactor × limit)``.

    Honours ``FleetSpec.speed_factor`` / ``speed_dev`` (B§5.4; SUMO
    documentation, "Speed Distributions"), where the scripted merge and the
    weave assumed factor 1.
    """
    return min(max_speed_ms, speed_factor * lane_limit_ms)


def speed_ceiling(
    v_gap: float,
    delta_ms: float,
    b: float,
    d_gap_m: float,
    v0_target: float,
    creep_ms: float = CREEP_MS,
    v_now: float | None = None,
    step_s: float | None = None,
) -> float:
    """The changer's desired-speed ceiling (B§5.4): matched to the gap, approached kinematically.

    ``min(v0_target, max(creep, √((v_gap + δ)² + 2·b·d_gap)))``: the changer
    keeps its own speed until it is within its braking distance at its own
    ``b`` of the chosen gap's position (``d_gap``, the bumper-to-bumper
    distance from its front to the gap leader's rear, 0 once there), and
    from there its desired speed is the gap's speed plus δ. Applied through
    ``vehicle.setMaxSpeed`` — a ceiling, never ``setSpeed``: the vehicle's own
    model keeps every gap and the lane end (LESSONS row 17).

    Approached in time as well (``v_now``, ``step_s``): the ceiling is never
    below ``v_now − b·Δt``. SUMO caps a vehicle's next speed at its
    ``maxSpeed`` outright, so a ceiling more than one comfortable step under
    the current speed is an emergency brake (measured 2026-10-05 on the
    corridor's T.H.52 section: every −9 m/s² vehicle-step of the first
    implementation was a changer whose gap's leader was already abreast,
    ``d_gap = 0``; the weave's record has the same finding for its first
    speed matching, docs/CONTRACTS.md §2). With the bound the changer slows
    to the gap's speed at its own ``b``, which is what "approached
    kinematically" asks.

    Args:
        v_gap: The gap's reference speed [m/s] (:func:`gap_reference_speed`).
        delta_ms: δ [m/s].
        b: The changer's comfortable deceleration [m/s²].
        d_gap_m: Distance to the gap's position [m] (negative counts as 0).
        v0_target: The changer's desired speed on the target lane
            (:func:`desired_speed`) [m/s].
        creep_ms: Floor [m/s].
        v_now: The changer's current speed [m/s]; ``None``: no time bound.
        step_s: The simulation step [s] (with ``v_now``).

    Returns:
        The ceiling [m/s].
    """
    v_match = max(v_gap + delta_ms, 0.0)
    v = math.sqrt(v_match * v_match + 2.0 * b * max(d_gap_m, 0.0))
    ceiling = min(v0_target, max(creep_ms, v))
    if v_now is not None and step_s is not None:
        ceiling = max(ceiling, v_now - b * step_s)
    return ceiling


def gap_reference_speed(v_leader: float | None) -> float | None:
    """The speed the ceiling matches (B§5.4 as amended by A3, 2026-10-06).

    The chosen gap's leader's speed; with no leader to match — a gap with open
    road ahead, or no gap chosen — ``None``: no ceiling is set and the
    vehicle keeps its own desired speed. Stage 1 also matched, with no gap
    chosen, the mean speed of the target-lane vehicles within 50 m; at a
    weaving section's start that is the slow auxiliary lane's, which capped
    exiters still in the through lane and slowed it (docs/MERGE_MODEL.md,
    amendment A3), so A3 withdrew it.
    """
    return v_leader


# --- Relaxation (B§5.7) ---------------------------------------------------------


def tau_floor(t_own: float, step_s: float, floor_fraction: float = RELAX_FLOOR_FRACTION) -> float:
    """The lowest headway the model sets: ``max(step, floor_fraction · T_i)`` [s].

    SUMO warns that a ``tau`` below the step length may cause collisions
    (B§5.6 invariant); ``0.5 · T_i`` is docs/MERGE_MODEL.md §2's floor.
    """
    return max(step_s, floor_fraction * t_own)


def relaxation_start(
    gap_net: float,
    v: float,
    t_own: float,
    step_s: float,
    floor_fraction: float = RELAX_FLOOR_FRACTION,
) -> float:
    """``T_eff,0 = clamp((g − s0)/v, floor, T_i)``: the equilibrium gap equals the accepted gap.

    ``gap_net`` is the gap net of the rear vehicle's ``minGap`` (SUMO's
    leader / follower gap), i.e. ``g − s0`` of docs/MERGE_MODEL.md §2: the
    model's equilibrium net gap at speed ``v`` is ``v·T``. At (near) rest the
    headway does not set the gap and ``T_i`` is kept.

    Args:
        gap_net: The rear vehicle's net gap to its leader [m].
        v: The rear vehicle's speed [m/s].
        t_own: Its own drawn ``T`` [s].
        step_s: The simulation step [s].
        floor_fraction: Floor as a fraction of ``T_i``.

    Returns:
        The starting headway [s], in ``[tau_floor, T_i]``.
    """
    floor = tau_floor(t_own, step_s, floor_fraction)
    if v <= 1e-6:
        return t_own
    return min(max(gap_net / v, floor), max(t_own, floor))


def relaxed_tau(
    t_own: float,
    t_start: float,
    elapsed_s: float,
    tau_r_s: float,
    step_s: float,
    floor_fraction: float = RELAX_FLOOR_FRACTION,
) -> float:
    """``T_eff(τ) = T_i − (T_i − T_eff,0)·exp(−τ/τ_r)``, never below the floor [s]."""
    t = t_own - (t_own - t_start) * math.exp(-max(elapsed_s, 0.0) / tau_r_s)
    return min(max(t, tau_floor(t_own, step_s, floor_fraction)), max(t_own, t_start))


def relaxation_expired(
    elapsed_s: float, tau_r_s: float, restore_after: float = RELAX_RESTORE_TAU_R
) -> bool:
    """Whether a relaxation is restored: ``τ ≥ restore_after · τ_r`` (98 % at 4)."""
    return elapsed_s >= restore_after * tau_r_s


def regrant(t_now: float, t_start_new: float) -> bool:
    """A new crossing re-grants from the new gap only if that gives the smaller ``T`` (B§5.7)."""
    return t_start_new < t_now


def in_car_following_range(gap_bb: float, v_rear: float) -> bool:
    """WP-88's car-following bound: bumper gap ≤ 10 m + 5 s · v of the rear vehicle."""
    return gap_bb <= CAR_FOLLOWING_GAP_M + CAR_FOLLOWING_TIME_S * max(v_rear, 0.0)


# --- Mandatory changers (B§5.2) -------------------------------------------------


def lane_reach(
    zone_edges: Sequence[str],
    outgoing: Callable[[str, int], Iterable[tuple[str, int]]],
    n_lanes: Callable[[str], int],
) -> dict[str, dict[int, frozenset[str]]]:
    """Per zone edge and lane, the edges outside the zone a vehicle reaches by staying in lane.

    Computed backwards over the zone's edges from the compiled connections
    (B§5.2: "from ``lane.getOutgoing()`` against the route, not from lane
    indices"): a connection into the next zone edge carries that lane's
    reach, any other connection adds its target edge. A lane with no
    connection (an acceleration lane's dead end) reaches nothing.

    Args:
        zone_edges: The zone's edges in driving order.
        outgoing: ``(edge, lane) → [(to edge, to lane), ...]``.
        n_lanes: Lane count of an edge.

    Returns:
        ``{edge: {lane: frozenset of edges}}``.
    """
    reach: dict[str, dict[int, frozenset[str]]] = {}
    zone = set(zone_edges)
    for e in reversed(list(zone_edges)):
        reach[e] = {}
        for j in range(n_lanes(e)):
            out: set[str] = set()
            for to_e, to_l in outgoing(e, j):
                if to_e in zone and to_e in reach:
                    out |= reach[to_e].get(to_l, frozenset())
                else:
                    out.add(to_e)
            reach[e][j] = frozenset(out)
    return reach


def mandatory_direction(reach_by_lane: Mapping[int, frozenset[str]], lane: int, target: str) -> int:
    """The direction a vehicle on ``lane`` must change to reach ``target``: +1 left, −1 right, 0 none.

    0 when its own lane reaches ``target`` or no lane of the edge does (its
    route is not this zone's to drive). Otherwise the sign towards the
    nearest lane that does; at a tie the lower lane (right).
    """
    if target in reach_by_lane.get(lane, frozenset()):
        return 0
    good = [k for k, r in reach_by_lane.items() if target in r]
    if not good:
        return 0
    k = min(good, key=lambda j: (abs(j - lane), j))
    return 1 if k > lane else -1


# --- Opposing entries into one lane (B§5.6, WP-92's ordering) -------------------


@dataclass(frozen=True)
class ChangeRequest:
    """A change the runner would request this step (:func:`resolve_opposing`).

    Attributes:
        vid: Vehicle id.
        x: Front-bumper position on the zone's axis [m].
        lane: Its section lane.
        target: The section lane it changes into.
        due: Its forced change is due (the forced zone's delay spent, or a
            released pair's): its lane end is its deadline.
        accept_s: The movement's leader-side time gap (the conflict
            distance's time term) [s].
        v: Speed [m/s].
        s0: ``minGap`` [m].
        b: Comfortable deceleration [m/s²].
    """

    vid: str
    x: float
    lane: int
    target: int
    due: bool
    accept_s: float
    v: float
    s0: float
    b: float


@dataclass(frozen=True)
class LaneVehicle:
    """A vehicle in the lane an opposing entry comes from (front position, length, speed)."""

    vid: str
    x: float
    length: float
    v: float


#: What an opposing vehicle that is not itself requesting can do this step:
#: ``"open"`` — driven, with last step's request still open into the target
#: lane (cannot be taken back); ``"driven"`` — driven with no request (cannot
#: change: its model bits are cleared); ``"model"`` — not driven, its model
#: may change it (vetoable for one step); ``"held"`` — not driven and under
#: another rule's mode with no model bits (its change cannot be read).
OpponentState = Literal["open", "driven", "model", "held"]


def opposing_conflict(rq: ChangeRequest, front: LaneVehicle) -> bool:
    """Whether ``rq``, landing behind ``front`` in its target lane, would be inside the forced minimum.

    WP-92's conflict: the net gap ``x_P − len_P − x_R − s0_R`` at most
    ``s0_R + max(A·c, c²/(2·b_R))``, ``c = (v_R − v_P)⁺`` — fronts within
    ``len_P + 2·s0_R`` at speed parity; an overlap is a conflict.
    """
    g = front.x - front.length - rq.x - rq.s0
    c = max(rq.v - front.v, 0.0)
    return g <= rq.s0 + max(rq.accept_s * c, c * c / (2.0 * rq.b))


def resolve_opposing(
    requests: Sequence[ChangeRequest],
    lanes: Mapping[int, Sequence[LaneVehicle]],
    state_of: Callable[[str], OpponentState],
) -> tuple[set[str], set[str]]:
    """Two changes into one lane from opposite sides in one step: the one with priority goes (WP-92).

    SUMO executes an edge's lane changes front vehicle first, and a change
    under mode 256 refuses only an overlap, so a runner change lands at any
    gap behind a vehicle ahead that entered the same lane from the other
    side in the same step (B§3.10; the cause of every 9 m/s² stop of WP-90).
    Each request R into lane k from lane k − d is read against the vehicles P
    of lane k + d whose front is ahead of R's (level counts as ahead when P
    is in the lower lane: SUMO's order at a tie), front first; a conflicting
    pair (:func:`opposing_conflict`) is resolved by priority — (0) a due
    forced change, (1) any other runner change, (2) a model-driven change of
    a vehicle not driven — the lower class going and, between equals, the
    one ahead. The loser is deferred by one step: a runner request withheld,
    or a model-driven vehicle vetoed (its model bits cleared for the step). A
    driven P with last step's request still open wins (it cannot be taken
    back); a driven P with none cannot change; a ``held`` P cannot be read,
    so R is withheld. A withheld request is no longer a front.

    Args:
        requests: The runner's requests this step.
        lanes: Section lane → its vehicles ``(x, length, v)``, ascending ``x``.
        state_of: The state of a vehicle that is not among ``requests``
            (:data:`OpponentState`), asked only for conflicting ones.

    Returns:
        ``(withheld, vetoed)``: the request ids deferred and the undriven
        vehicles whose model changes are suspended for the step.
    """
    by_id = {rq.vid: rq for rq in requests}
    withheld: set[str] = set()
    vetoed: set[str] = set()
    for rq in sorted(requests, key=lambda r: (-r.x, r.lane, r.vid)):
        if rq.vid in withheld:
            continue
        d = rq.target - rq.lane
        opp = lanes.get(rq.target + d) or ()
        prio_r = 0 if rq.due else 1
        for p in opp:
            ahead = p.x >= rq.x if d < 0 else p.x > rq.x
            if not ahead or not opposing_conflict(rq, p):
                continue
            rp = by_id.get(p.vid)
            if rp is not None:
                if rp.target != rq.target or p.vid in withheld:
                    continue
                if prio_r < (0 if rp.due else 1):
                    withheld.add(p.vid)
                    continue
                withheld.add(rq.vid)
                break
            if p.vid in vetoed:
                continue
            st = state_of(p.vid)
            if st == "open":
                withheld.add(rq.vid)
                break
            if st == "driven":
                continue
            if st == "model":
                vetoed.add(p.vid)
                continue
            withheld.add(rq.vid)
            break
    return withheld, vetoed


@dataclass
class RelaxationState:
    """One vehicle's relaxation (B§5.7): per vehicle, a driver state, not per pair.

    Attributes:
        t_own: Its own drawn ``T`` [s].
        t_start: ``T_eff,0`` of the current grant [s].
        granted_s: Simulation time of the current grant [s].
        tau_set: The headway last written to SUMO [s].
        role: ``"entrant"`` or ``"follower"`` (of the current grant).
        zone: Index of the zone that granted it.
    """

    t_own: float
    t_start: float
    granted_s: float
    tau_set: float
    role: str
    zone: int

    def tau_at(self, t: float, params: MergeModelParams, step_s: float) -> float:
        """The headway at time ``t`` under this grant [s]."""
        return relaxed_tau(
            self.t_own,
            self.t_start,
            t - self.granted_s,
            params.tau_r_s,
            step_s,
            params.relax_floor_fraction,
        )
