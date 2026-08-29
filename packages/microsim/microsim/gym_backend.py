"""Real-physics ``EnvBackend`` for ``controllers.gym_env.FlowStateEnv``.

Implements the :class:`controllers.gym_env.EnvBackend` protocol (CLAUDE.md
§4.5) on a short single-lane corridor with one controlled ego vehicle driven
through libsumo — so the Gymnasium hook exercises real IDM traffic instead of
the synthetic toy backend. Interface + smoke test only; no training code
(ADR-2).

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
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Final

import numpy as np

from flowstate_core.config import AVSpec, FleetSpec
from flowstate_core.rng import make_rng, sumo_seed
from microsim.networks import EDGE_SPEED_LIMIT_MS, NetBundle, corridor
from microsim.runner import DOWNSTREAM_BIN_M, fuel_mg_to_ml
from microsim.vehicles import FleetPlan, build_corridor_plan, write_corridor_routes

#: Reported gap ceiling [m] (keeps the Box observation finite).
GAP_CAP_M: Final[float] = 500.0

_EGO_ID: Final[str] = "ego"


class MicrosimBackend:
    """SUMO-backed corridor episode with a single speed-controlled ego.

    Args:
        n_downstream: Number of downstream mean-speed bins in the observation
            (bin width 100 m, matching the runner's controller observation).
        corridor_length_m: Main corridor length [m].
        inflow_veh_s: Constant background demand [veh/s].
        episode_s: Episode horizon [s] (sim time after which ``done``).
        step_length_s: SUMO integration step [s].
        action_step_s: Control interval — one ``step()`` advances this far.
        ego_depart_s: Ego insertion time [s]; ``reset`` fast-forwards to it.
        workdir: Directory for network/route files (default: a fresh temp
            directory).
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
    ) -> None:
        if n_downstream < 0:
            raise ValueError(f"n_downstream must be >= 0, got {n_downstream}")
        self.n_downstream = n_downstream
        self.corridor_length_m = corridor_length_m
        self.inflow_veh_s = inflow_veh_s
        self.episode_s = episode_s
        self.step_length_s = step_length_s
        self.action_step_s = action_step_s
        self.ego_depart_s = ego_depart_s
        self.workdir = workdir or Path(mkdtemp(prefix="microsim_gym_"))
        self._bundle: NetBundle | None = None
        self._active = False
        self._t = 0.0
        self._ego_min_gap = 2.0

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
            self._bundle = corridor(self.corridor_length_m, lanes=1, workdir=self.workdir / "net")
        fleet = FleetSpec()
        plan = build_corridor_plan([(0.0, self.inflow_veh_s)], self.episode_s, fleet, AVSpec(), rng)
        routes = self.workdir / f"demand_{seed}.rou.xml"
        write_corridor_routes(self._bundle.edge_ids, plan, fleet.model, self.action_step_s, routes)
        self._append_ego(routes, plan, fleet)

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

    def _append_ego(self, routes: Path, plan: FleetPlan, fleet: FleetSpec) -> None:
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
