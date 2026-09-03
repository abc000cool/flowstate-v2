"""Microscopic-tier simulate function for demand calibration (CLAUDE.md §6.3).

``calibration.demand.fit_inflow(scenario, counts)`` needs a simulator that
turns a candidate :class:`~flowstate_core.artifacts.DemandProfile` into
binned link counts. :func:`make_simulate_fn` builds one from a scenario: it
sets the network's inflow to the candidate profile, runs **one seeded
replicate** with :func:`microsim.runner.run_micro`, and bins the upward
crossings of a reference cross-section into the observed windows with the
shared crossing logic of ``validation.metrics.count_crossings`` (one crossing
per consecutive same-vehicle sample pair with ``x_prev < x_ref <= x_cur``,
stamped at the later sample). Counts are returned as SI flows [veh/s] per
bin; the fitter converts to hourly volumes for GEH.

``calibration`` imports this module lazily, so the calibration package never
depends on SUMO at import time; this module in turn imports ``validation``
inside the call so ``microsim``'s declared dependencies stay unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import CorridorNetwork, OSMNetwork, ScenarioConfig
from microsim.runner import CORRIDOR_INSERTION_BUFFER_M, RunPaths, run_micro


def corridor_x_offset_m(cfg: ScenarioConfig) -> float:
    """Trajectory ``x`` of the corridor-proper start (docs/CONTRACTS.md §3).

    Straight corridors are built with an upstream insertion buffer of
    ``min(CORRIDOR_INSERTION_BUFFER_M, length_m)`` (``microsim.runner``), and
    the linear ``x`` origin is the start of that buffer, so the corridor
    proper begins at the returned offset. OSM corridors have no such
    synthetic buffer here (0).

    Args:
        cfg: Scenario configuration.

    Returns:
        Offset [m].
    """
    net = cfg.network
    if isinstance(net, CorridorNetwork):
        return float(min(CORRIDOR_INSERTION_BUFFER_M, net.length_m))
    return 0.0


def read_trajectories(path: str | Path, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read a trajectories parquet through an open file object.

    Passing a bare path would make pyarrow construct a ``LocalFileSystem``,
    which fails once libsumo's bundled libarrow is loaded in the process
    (see ``microsim.runner._write_parquet``); a file object bypasses it.

    Args:
        path: Parquet file.
        columns: Optional column subset.

    Returns:
        The frame.
    """
    with open(path, "rb") as f:
        return pd.read_parquet(f, columns=list(columns) if columns is not None else None)


class MicrosimSimulator:
    """Callable ``DemandProfile → binned link counts`` backed by ``run_micro``.

    Satisfies ``calibration.demand.SimulateFn``. Every call runs one
    replicate under ``workdir/iterNNN/`` (``run_micro`` adds
    ``<config_hash>/<seed>/``) and records the run directory in
    :attr:`run_dirs` for provenance.

    Attributes:
        cfg: Scenario the candidate inflows are written into.
        workdir: Root of the per-iteration run trees.
        bins: ``(t_start_s, t_end_s)`` counting windows in simulation time.
        x_ref_m: Counting cross-section [m] in trajectory coordinates.
        seed: Replicate seed used for every call.
        n_calls: Number of simulations run so far.
        run_dirs: Run directory of each call, in order.
    """

    def __init__(
        self,
        cfg: ScenarioConfig,
        workdir: str | Path,
        *,
        bins: Sequence[tuple[float, float]],
        x_ref_m: float,
        seed: int,
        depart_edge_spread: int = 1,
    ) -> None:
        self.cfg = cfg
        self.workdir = Path(workdir)
        self.bins = [(float(a), float(b)) for a, b in bins]
        self.x_ref_m = float(x_ref_m)
        self.seed = int(seed)
        self.depart_edge_spread = int(depart_edge_spread)
        self.n_calls = 0
        self.run_dirs: list[Path] = []

    def run(self, profile: DemandProfile) -> RunPaths:
        """Run one replicate with ``profile`` as the network inflow."""
        run_cfg = self.cfg.model_copy(deep=True)
        assert isinstance(run_cfg.network, CorridorNetwork | OSMNetwork)
        run_cfg.network.inflow = [(float(t), float(q)) for t, q in profile.steps]
        self.n_calls += 1
        paths = run_micro(
            run_cfg,
            self.seed,
            self.workdir / f"iter{self.n_calls:03d}",
            depart_edge_spread=self.depart_edge_spread,
        )
        self.run_dirs.append(paths.run_dir)
        return paths

    def __call__(self, profile: DemandProfile) -> pd.DataFrame:
        """Simulate ``profile`` and return counts on :attr:`bins` [veh/s]."""
        from validation.metrics import count_crossings

        paths = self.run(profile)
        traj = read_trajectories(paths.trajectories, columns=["t", "veh_id", "x"])
        flows = [
            count_crossings(traj, self.x_ref_m, t_lo=a, t_hi=b) / (b - a) for a, b in self.bins
        ]
        return pd.DataFrame(
            {
                "t_start_s": [a for a, _ in self.bins],
                "t_end_s": [b for _, b in self.bins],
                "flow_veh_s": flows,
            }
        )


def make_simulate_fn(
    cfg: ScenarioConfig,
    workdir: str | Path,
    *,
    bins: Sequence[tuple[float, float]],
    x_ref_m: float | None = None,
    seed: int | None = None,
    depart_edge_spread: int = 1,
) -> MicrosimSimulator:
    """Build the microscopic-tier simulate function for ``fit_inflow``.

    Args:
        cfg: Scenario whose network carries an inflow (corridor or OSM).
            ``cfg.sim.duration_s`` must cover the last bin.
        workdir: Root directory for the per-iteration run trees.
        bins: ``(t_start_s, t_end_s)`` counting windows in simulation time,
            normally the observed-count bins; time-ordered, positive width.
        x_ref_m: Counting cross-section [m] in trajectory coordinates
            (docs/CONTRACTS.md §3). ``None`` selects the upstream end of the
            corridor proper (:func:`corridor_x_offset_m`) for straight
            corridors; OSM corridors need an explicit value.
        seed: Replicate seed; ``None`` uses ``cfg.seed``.
        depart_edge_spread: Forwarded to :func:`microsim.runner.run_micro`.

    Returns:
        A :class:`MicrosimSimulator`.

    Raises:
        ValueError: If the network has no inflow, the bins are malformed or
            exceed the simulated duration, or ``x_ref_m`` is missing for an
            OSM network.
    """
    net = cfg.network
    if not isinstance(net, CorridorNetwork | OSMNetwork):
        raise ValueError(
            f"make_simulate_fn: network kind {net.kind!r} has no inflow profile to calibrate"
        )
    if not bins:
        raise ValueError("make_simulate_fn: bins is empty")
    last_end = 0.0
    for a, b in bins:
        if b <= a:
            raise ValueError(f"make_simulate_fn: bin [{a}, {b}) has non-positive width")
        if a < last_end - 1e-9:
            raise ValueError("make_simulate_fn: bins must be time-ordered and non-overlapping")
        last_end = float(b)
    if last_end > cfg.sim.duration_s + 1e-9:
        raise ValueError(
            f"make_simulate_fn: last bin ends at {last_end} s but sim.duration_s is "
            f"{cfg.sim.duration_s} s"
        )
    if x_ref_m is None:
        if isinstance(net, OSMNetwork):
            raise ValueError("make_simulate_fn: x_ref_m is required for OSM networks")
        x_ref_m = corridor_x_offset_m(cfg)
    return MicrosimSimulator(
        cfg,
        workdir,
        bins=bins,
        x_ref_m=x_ref_m,
        seed=cfg.seed if seed is None else seed,
        depart_edge_spread=depart_edge_spread,
    )
