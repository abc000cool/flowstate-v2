"""Real-physics ``EnvBackend`` for ``controllers.gym_env.FlowStateEnv``.

Implements the :class:`controllers.gym_env.EnvBackend` protocol (CLAUDE.md
§4.5) on a straight corridor with one speed-controlled ego vehicle driven
through libsumo — so the Gymnasium hook exercises real IDM/EIDM traffic
instead of the synthetic toy backend. Interface + smoke test only; no
training code (ADR-2).

Two ways to build one:

* :class:`MicrosimBackend` with explicit arguments — a short ad-hoc corridor
  for fast tests.
* :meth:`MicrosimBackend.from_scenario` — the §4.5 hook proper: corridor
  length, lane count, demand profile, fleet and step parameters are taken
  from a versioned :class:`~flowstate_core.config.ScenarioConfig` with a
  corridor network (``scenarios/corridor_10km.yaml`` is what
  :class:`~controllers.gym_env.FlowStateEnv` loads by default), and the
  scenario's name and config hash are kept on the backend for provenance.
  The background traffic is inserted exactly as :func:`microsim.runner.run_micro`
  inserts it (same entry-buffer length, lane round-robin and vType fields);
  only the ego and its observation are specific to this module.

Observation tuple: ``(ego speed [m/s], gap [m], leader speed [m/s],
k downstream mean bin speeds [m/s])``. Because the Gym observation space is a
non-negative Box, the unbounded/undefined cases are made finite: the gap is
capped at ``GAP_CAP_M`` (also used when there is no leader) and empty
downstream bins report the corridor speed limit (free flow — nothing
observable ahead). Per-step costs: ego fuel [ml] and the fleet speed
standard deviation σ_v [m/s].

libsumo is a per-process singleton, so at most one live backend (or any other
libsumo run) per process; ``close()`` (also called by ``reset``) releases it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Final

import numpy as np

from flowstate_core.config import AVSpec, CorridorNetwork, FleetSpec, ScenarioConfig, config_hash
from flowstate_core.rng import make_rng, sumo_seed
from microsim.networks import EDGE_SPEED_LIMIT_MS, ENTRY_EDGE_LENGTH_M, NetBundle, corridor
from microsim.runner import CORRIDOR_INSERTION_BUFFER_M, DOWNSTREAM_BIN_M, fuel_mg_to_ml
from microsim.vehicles import build_corridor_plan, write_corridor_routes

#: Reported gap ceiling [m] (keeps the Box observation finite).
GAP_CAP_M: Final[float] = 500.0

_EGO_ID: Final[str] = "ego"


def _validated_inflow(steps: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Check a piecewise-constant demand profile (time-ordered, non-negative)."""
    if not steps:
        raise ValueError("inflow needs at least one (t_start_s, veh/s) step")
    times = [t for t, _ in steps]
    if times != sorted(times):
        raise ValueError(f"inflow steps must be ordered by t_start: {list(steps)}")
    if any(q < 0.0 for _, q in steps):
        raise ValueError(f"inflow rates must be >= 0: {list(steps)}")
    return tuple((float(t), float(q)) for t, q in steps)


class MicrosimBackend:
    """SUMO-backed corridor episode with a single speed-controlled ego.

    Args:
        n_downstream: Number of downstream mean-speed bins in the observation
            (bin width 100 m, matching the runner's controller observation).
        corridor_length_m: Main corridor length [m].
        inflow_veh_s: Constant background demand [veh/s]; ignored when
            ``inflow`` is given.
        episode_s: Episode horizon [s] (sim time after which ``done``).
        step_length_s: SUMO integration step [s].
        action_step_s: Control interval — one ``step()`` advances this far.
        ego_depart_s: Ego insertion time [s]; ``reset`` fast-forwards to it.
            Must lie inside the episode.
        workdir: Directory for network/route files (default: a fresh temp
            directory).
        inflow: Piecewise-constant demand profile ``(t_start_s, veh/s)``
            steps (docs/CONTRACTS.md §2 convention) — the general form of
            ``inflow_veh_s``.
        lanes: Corridor lane count.
        fleet: Human-driver fleet spec; default ``FleetSpec()`` (CLAUDE.md
            §3.1 IDM defaults).
        entry_m: Upstream insertion-edge length [m]. The runner uses a 2 km
            buffer on scenario corridors (``CORRIDOR_INSERTION_BUFFER_M``);
            the ad-hoc default keeps the short test corridor short.
        scenario_name: Provenance — name of the scenario this backend was
            built from (set by :meth:`from_scenario`).
        scenario_hash: Provenance — ``config_hash`` of that scenario.

    Raises:
        ValueError: Non-positive dimensions/steps, an ego departure outside
            the episode, or a malformed inflow profile.
    """

    def __init__(
        self,
        n_downstream: int = 10,
        corridor_length_m: float = 2000.0,
        inflow_veh_s: float = 0.25,
        episode_s: float = 120.0,
        step_length_s: float = 0.5,
        action_step_s: float = 1.0,
        ego_depart_s: float = 20.0,
        workdir: Path | None = None,
        *,
        inflow: Sequence[tuple[float, float]] | None = None,
        lanes: int = 1,
        fleet: FleetSpec | None = None,
        entry_m: float = ENTRY_EDGE_LENGTH_M,
        scenario_name: str | None = None,
        scenario_hash: str | None = None,
    ) -> None:
        if n_downstream < 0:
            raise ValueError(f"n_downstream must be >= 0, got {n_downstream}")
        if corridor_length_m <= 0.0:
            raise ValueError(f"corridor_length_m must be > 0, got {corridor_length_m}")
        if episode_s <= 0.0:
            raise ValueError(f"episode_s must be > 0, got {episode_s}")
        if step_length_s <= 0.0 or action_step_s <= 0.0:
            raise ValueError(
                f"step_length_s and action_step_s must be > 0, got {step_length_s}, {action_step_s}"
            )
        if not 0.0 <= ego_depart_s < episode_s:
            raise ValueError(
                f"ego_depart_s must lie in [0, episode_s={episode_s}), got {ego_depart_s}"
            )
        if lanes < 1:
            raise ValueError(f"lanes must be >= 1, got {lanes}")
        if entry_m <= 0.0:
            raise ValueError(f"entry_m must be > 0, got {entry_m}")
        self.n_downstream = n_downstream
        self.corridor_length_m = corridor_length_m
        self.inflow_steps: tuple[tuple[float, float], ...] = _validated_inflow(
            inflow if inflow is not None else [(0.0, inflow_veh_s)]
        )
        self.inflow_veh_s = self.inflow_steps[0][1]
        self.episode_s = episode_s
        self.step_length_s = step_length_s
        self.action_step_s = action_step_s
        self.ego_depart_s = ego_depart_s
        self.workdir = workdir or Path(mkdtemp(prefix="microsim_gym_"))
        self.lanes = lanes
        self.fleet = fleet if fleet is not None else FleetSpec()
        self.entry_m = entry_m
        self.scenario_name = scenario_name
        self.scenario_hash = scenario_hash
        self._bundle: NetBundle | None = None
        self._active = False
        self._t = 0.0
        self._ego_min_gap = self.fleet.s0

    @classmethod
    def from_scenario(
        cls,
        cfg: ScenarioConfig,
        *,
        episode_s: float,
        ego_depart_s: float = 20.0,
        workdir: Path | None = None,
        n_downstream: int = 10,
    ) -> MicrosimBackend:
        """Build the backend from a versioned corridor scenario (CLAUDE.md §4.5).

        Corridor length and lane count, the demand profile, the fleet spec
        (including an ``idm_calibration`` artifact reference) and the
        step/action lengths are taken from ``cfg``; the runner's insertion
        buffer (``CORRIDOR_INSERTION_BUFFER_M``, capped at the corridor
        length) is used so the background traffic matches a ``run_micro``
        replicate of the same scenario. The episode is the RL horizon and
        may be much shorter than the scenario's ``sim.duration_s``.

        The backend controls exactly one vehicle (the ego). Scenario blocks
        it cannot honor are rejected rather than silently dropped: AV
        deployments (``av.penetration > 0``, ``av.controller``, ``av.vsl``)
        are not dispatched here, a seeded ``perturbation`` is not applied
        (and would have to be labeled, CLAUDE.md §0.2), and a downstream
        ``boundary`` schedule has no exit buffer to act on.

        Args:
            cfg: Scenario with a ``CorridorNetwork``.
            episode_s: Episode horizon [s], ``<= cfg.sim.duration_s``.
            ego_depart_s: Ego insertion time [s] inside the episode.
            workdir: Directory for network/route files.
            n_downstream: Downstream bins in the observation.

        Returns:
            A backend whose ``scenario_name``/``scenario_hash`` record ``cfg``.

        Raises:
            ValueError: ``cfg.network`` is not a corridor, an unsupported
                block is set, or the episode/ego timing is inconsistent.
        """
        net = cfg.network
        if not isinstance(net, CorridorNetwork):
            raise ValueError(
                f"from_scenario needs a corridor network, got kind={net.kind!r} "
                f"(scenario {cfg.name!r})"
            )
        if net.boundary is not None:
            raise ValueError(
                f"scenario {cfg.name!r} carries a downstream boundary schedule, which the "
                "gym backend does not apply"
            )
        if cfg.av.penetration > 0.0 or cfg.av.controller is not None or cfg.av.vsl is not None:
            raise ValueError(
                f"scenario {cfg.name!r} deploys controlled vehicles/VSL; the gym backend "
                "controls only the ego and does not dispatch scenario controllers"
            )
        if cfg.perturbation is not None:
            raise ValueError(
                f"scenario {cfg.name!r} has a seeded perturbation, which the gym backend "
                "does not apply"
            )
        if episode_s > cfg.sim.duration_s:
            raise ValueError(
                f"episode_s={episode_s} exceeds the scenario duration "
                f"{cfg.sim.duration_s} s that defines its demand profile"
            )
        return cls(
            n_downstream=n_downstream,
            corridor_length_m=net.length_m,
            episode_s=episode_s,
            step_length_s=cfg.sim.step_length_s,
            action_step_s=cfg.sim.action_step_s,
            ego_depart_s=ego_depart_s,
            workdir=workdir,
            inflow=net.inflow,
            lanes=net.lanes,
            fleet=cfg.fleet,
            entry_m=min(CORRIDOR_INSERTION_BUFFER_M, net.length_m),
            scenario_name=cfg.name,
            scenario_hash=config_hash(cfg),
        )

    # -- EnvBackend protocol ----------------------------------------------

    def reset(self, seed: int) -> tuple[float, ...]:
        """Build the scenario for ``seed``, start SUMO, insert the ego.

        Advances the simulation until the ego has departed, then returns the
        initial observation tuple (length ``3 + n_downstream``).
        """
        import libsumo

        self.close()
        rng = make_rng(seed)
        if self._bundle is None:
            self._bundle = corridor(
                self.corridor_length_m,
                lanes=self.lanes,
                workdir=self.workdir / "net",
                entry_m=self.entry_m,
            )
        fleet = self.fleet
        plan = build_corridor_plan(list(self.inflow_steps), self.episode_s, fleet, AVSpec(), rng)
        routes = self.workdir / f"demand_{seed}.rou.xml"
        write_corridor_routes(
            self._bundle.edge_ids,
            plan,
            fleet.model,
            self.action_step_s,
            routes,
            lanes=self.lanes,
            lc_strategic=fleet.lc_strategic,
            lc_keep_right=fleet.lc_keep_right,
        )
        self._append_ego(routes, fleet)

        libsumo.start(
            [
                "sumo",
                "-n",
                str(self._bundle.net_path),
                "-r",
                str(routes),
                "--step-length",
                str(self.step_length_s),
                "--seed",
                str(sumo_seed(seed)),
                "--time-to-teleport",
                "-1",
                "--no-warnings",
                "--collision.action",
                "warn",
                "--no-step-log",
            ]
        )
        self._active = True
        self._t = 0.0
        while self._t < self.episode_s:
            libsumo.simulationStep()
            self._t = float(libsumo.simulation.getTime())
            if _EGO_ID in libsumo.vehicle.getIDList():
                break
        return self._obs(libsumo)

    def step(self, v_cmd: float) -> tuple[tuple[float, ...], float, float, bool]:
        """Apply ``v_cmd`` to the ego and advance one control interval.

        SUMO's safety layer stays ON (default ``speedMode``), so unsafe
        commands are attenuated rather than causing collisions
        (CLAUDE.md §3.3).

        Returns:
            ``(obs, fuel_ml, sigma_v, done)`` — ego fuel this interval [ml],
            fleet speed std σ_v [m/s], and episode end (ego arrived or
            horizon reached).
        """
        import libsumo

        if not self._active:
            raise RuntimeError("step() before reset(), or after close()")
        n_sub = max(round(self.action_step_s / self.step_length_s), 1)
        fuel_mg = 0.0
        ego_present = _EGO_ID in libsumo.vehicle.getIDList()
        if ego_present:
            libsumo.vehicle.setSpeed(_EGO_ID, max(float(v_cmd), 0.0))
        for _ in range(n_sub):
            libsumo.simulationStep()
            self._t = float(libsumo.simulation.getTime())
            if _EGO_ID in libsumo.vehicle.getIDList():
                fuel_mg += libsumo.vehicle.getFuelConsumption(_EGO_ID) * self.step_length_s
        obs = self._obs(libsumo)
        speeds = [libsumo.vehicle.getSpeed(v) for v in libsumo.vehicle.getIDList()]
        sigma_v = float(np.std(speeds)) if len(speeds) >= 2 else 0.0
        done = self._t >= self.episode_s or _EGO_ID not in libsumo.vehicle.getIDList()
        return obs, fuel_mg_to_ml(fuel_mg), sigma_v, done

    # -- helpers -----------------------------------------------------------

    def close(self) -> None:
        """Release the libsumo singleton (idempotent)."""
        if self._active:
            import libsumo

            libsumo.close()
            self._active = False

    def _append_ego(self, routes: Path, fleet: FleetSpec) -> None:
        """Insert the ego vehicle element into the written route file."""
        assert self._bundle is not None
        self._ego_min_gap = fleet.s0
        ego_xml = (
            f'  <vType id="t_ego" carFollowModel="{fleet.model}" accel="{fleet.a_max}" '
            f'decel="{fleet.b}" tau="{fleet.T}" minGap="{fleet.s0}" maxSpeed="{fleet.v0}" '
            f'length="5.0" speedFactor="1.0" speedDev="0" '
            f'actionStepLength="{self.action_step_s}"/>\n'
            f'  <vehicle id="{_EGO_ID}" type="t_ego" depart="{self.ego_depart_s:.2f}" '
            f'departPos="base" departSpeed="max" departLane="free" route="main"/>\n'
        )
        text = routes.read_text()
        # Keep vehicles sorted by depart time: SUMO requires nondecreasing order.
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        inserted = False
        for line in lines:
            if not inserted and 'depart="' in line and "<vehicle" in line:
                depart = float(line.split('depart="')[1].split('"')[0])
                if depart > self.ego_depart_s:
                    out.append(ego_xml)
                    inserted = True
            if not inserted and line.strip() == "</routes>":
                out.append(ego_xml)
                inserted = True
            out.append(line)
        routes.write_text("".join(out))

    def _obs(self, libsumo: Any) -> tuple[float, ...]:
        """Observation tuple; free-flow fill for undefined entries."""
        assert self._bundle is not None
        free = EDGE_SPEED_LIMIT_MS
        if _EGO_ID not in libsumo.vehicle.getIDList():
            return (0.0, GAP_CAP_M, free, *([free] * self.n_downstream))
        v = float(libsumo.vehicle.getSpeed(_EGO_ID))
        lead = libsumo.vehicle.getLeader(_EGO_ID, GAP_CAP_M)
        if lead is None or lead[0] == "" or lead[1] < 0.0:
            gap, v_leader = GAP_CAP_M, free
        else:
            gap = min(lead[1] + self._ego_min_gap, GAP_CAP_M)
            v_leader = float(libsumo.vehicle.getSpeed(lead[0]))
        ego_x = self._bundle.linear_x(
            libsumo.vehicle.getRoadID(_EGO_ID), libsumo.vehicle.getLanePosition(_EGO_ID)
        )
        bins = [free] * self.n_downstream
        sums = [0.0] * self.n_downstream
        counts = [0] * self.n_downstream
        for vid in libsumo.vehicle.getIDList():
            if vid == _EGO_ID:
                continue
            x = self._bundle.linear_x(
                libsumo.vehicle.getRoadID(vid), libsumo.vehicle.getLanePosition(vid)
            )
            ahead = x - ego_x
            if ahead <= 0.0:
                continue
            b = int(ahead // DOWNSTREAM_BIN_M)
            if b < self.n_downstream:
                sums[b] += float(libsumo.vehicle.getSpeed(vid))
                counts[b] += 1
        for b in range(self.n_downstream):
            if counts[b]:
                bins[b] = sums[b] / counts[b]
        if math.isnan(v):  # pragma: no cover - defensive
            v = 0.0
        return (v, gap, v_leader, *bins)
