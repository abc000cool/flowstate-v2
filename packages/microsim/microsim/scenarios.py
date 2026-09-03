"""Scenario loading, one-call runs, and OSM onboarding (CLAUDE.md §3.2).

Named scenarios live as versioned YAML under the repository's ``scenarios/``
directory (``ring_sugiyama``, ``corridor_10km``, …) and validate through
``flowstate_core.config.ScenarioConfig`` (docs/CONTRACTS.md §2).
:func:`run_scenario` resolves a name or path, loads the config, and runs one
micro-tier replicate via :func:`microsim.runner.run_micro`.

:func:`scenario_from_osm` is the output stage of the ``osm_generic`` "any
city" onboarding pipeline (CLAUDE.md §3.2.4): OSM extract → ``netconvert``
(:func:`microsim.networks.osm_import`) → pruned corridor → a validated,
hashable :class:`ScenarioConfig` whose ``to_yaml`` writes the versioned
scenario file. The compiled network is checked with ``sumolib`` before the
config is returned, so a scenario that comes out of here is one the runner
can start.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

import sumolib

from flowstate_core.config import (
    AVSpec,
    BoundarySpec,
    FleetSpec,
    OSMNetwork,
    RampSpec,
    ScenarioConfig,
    SimSpec,
)
from microsim.networks import osm_import
from microsim.runner import RunPaths, run_micro

#: Repository ``scenarios/`` directory (this file sits at
#: ``packages/microsim/microsim/scenarios.py`` → three parents up is the root).
SCENARIOS_DIR: Path = Path(__file__).resolve().parents[3] / "scenarios"

#: Named scenario whose fleet, time-discretization and replicate settings seed
#: the defaults of an OSM-onboarded scenario: ``corridor_10km`` carries the
#: Phase-1 tuning record (EIDM, heterogeneity 0.15, 0.5 s steps) that makes an
#: open corridor grow emergent waves (CLAUDE.md §3.2.2).
OSM_DEFAULTS_SCENARIO: str = "corridor_10km"


def resolve_scenario(name_or_path: str | Path) -> Path:
    """Resolve a scenario name or YAML path to a concrete file.

    Args:
        name_or_path: Either an existing YAML file path, or a bare scenario
            name (with or without ``.yaml``) looked up in ``scenarios/``.

    Returns:
        Path of the scenario YAML.

    Raises:
        FileNotFoundError: Nothing matches; the message lists the available
            named scenarios.
    """
    p = Path(name_or_path)
    if p.is_file():
        return p
    stem = p.name.removesuffix(".yaml").removesuffix(".yml")
    for suffix in (".yaml", ".yml"):
        candidate = SCENARIOS_DIR / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    available = sorted(f.stem for f in SCENARIOS_DIR.glob("*.y*ml"))
    raise FileNotFoundError(
        f"scenario {name_or_path!r} not found (looked in {SCENARIOS_DIR}); available: {available}"
    )


def load_scenario(name_or_path: str | Path) -> ScenarioConfig:
    """Load and validate a scenario config by name or path."""
    return ScenarioConfig.from_yaml(resolve_scenario(name_or_path))


def run_scenario(
    name_or_path: str | Path,
    out_dir: str | Path,
    seed: int | None = None,
    *,
    gui: bool = False,
    use_traci: bool = False,
) -> RunPaths:
    """Load a scenario and run one micro-tier replicate.

    Args:
        name_or_path: Scenario name (``scenarios/<name>.yaml``) or YAML path.
        out_dir: Run-tree root (artifacts under ``<hash>/<seed>/``).
        seed: Replicate seed; defaults to the scenario's own ``seed``.
        gui: Launch ``sumo-gui`` (debugging; forces TraCI).
        use_traci: TCP TraCI fallback instead of libsumo.

    Returns:
        :class:`microsim.runner.RunPaths` for the completed replicate.
    """
    cfg = load_scenario(name_or_path)
    return run_micro(
        cfg,
        cfg.seed if seed is None else seed,
        out_dir,
        gui=gui,
        use_traci=use_traci,
    )


def _inflow_steps(inflow: Sequence[tuple[float, float]] | float) -> list[tuple[float, float]]:
    """Normalize a demand spec to validated ``(t_start_s, veh/s)`` steps.

    ``OSMNetwork`` does not validate its ``inflow`` (a corridor network
    does), so the checks live here: non-empty, time-ordered, non-negative.
    """
    steps = [(0.0, float(inflow))] if isinstance(inflow, int | float) else list(inflow)
    if not steps:
        raise ValueError("inflow needs at least one (t_start_s, veh/s) step")
    times = [t for t, _ in steps]
    if times != sorted(times):
        raise ValueError(f"inflow steps must be ordered by t_start: {steps}")
    if any(q < 0.0 for _, q in steps):
        raise ValueError(f"inflow rates must be >= 0 veh/s: {steps}")
    return [(float(t), float(q)) for t, q in steps]


def _check_corridor_in_net(
    net_path: Path, corridor_edges: Sequence[str], lanes: int | None
) -> None:
    """Verify the corridor chain against the compiled ``.net.xml``.

    Every named edge must exist, consecutive edges must be connected
    (``a`` → ``b`` is an outgoing connection of ``a``), and — when ``lanes``
    is given — the first corridor edge must carry that many lanes.

    Raises:
        ValueError: With the offending ids, the broken link, or every
            corridor edge's lane count on a lane mismatch.
    """
    net = sumolib.net.readNet(str(net_path))
    by_id = {e.getID(): e for e in net.getEdges(withInternal=False)}
    missing = [e for e in corridor_edges if e not in by_id]
    if missing:
        raise ValueError(
            f"corridor edges missing from the compiled net {net_path}: {missing}; "
            f"available: {sorted(by_id)}"
        )
    for a, b in pairwise(corridor_edges):
        if b not in {e.getID() for e in by_id[a].getOutgoing()}:
            raise ValueError(
                f"corridor edges {a!r} -> {b!r} are not connected in the compiled net; "
                "corridor_edges must be a driving-order chain"
            )
    if lanes is not None:
        counts = {e: int(by_id[e].getLaneNumber()) for e in corridor_edges}
        entry = corridor_edges[0]
        if counts[entry] != lanes:
            raise ValueError(
                f"expected {lanes} lanes on the entry edge {entry!r}, the compiled net has "
                f"{counts[entry]}; corridor lane counts: {counts}"
            )


def scenario_from_osm(
    *,
    name: str,
    osm_file: str | Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    corridor_edges: Sequence[str],
    inflow: Sequence[tuple[float, float]] | float,
    workdir: str | Path,
    fleet: FleetSpec | None = None,
    duration_s: float = 1200.0,
    seed: int = 0,
    lanes: int | None = None,
    ramps: Sequence[RampSpec] = (),
    boundary: BoundarySpec | None = None,
    av: AVSpec | None = None,
    warmup_s: float | None = None,
    replicates: int | None = None,
) -> ScenarioConfig:
    """Onboard an OSM corridor as a runnable, hashable scenario (CLAUDE.md §3.2.4).

    Runs the ``osm_generic`` pipeline — :func:`microsim.networks.osm_import`
    (OSM extract → ``netconvert`` with the highway typemap → pruning to the
    named corridor and ramp edges) — then verifies the corridor against the
    compiled net with ``sumolib`` (every id present, consecutive edges
    connected, optional lane check) and returns a validated
    :class:`ScenarioConfig`. Write it with ``cfg.to_yaml(path)``; the file
    reloads through :func:`load_scenario` with an identical ``config_hash``.
    The compiled network stays at ``<workdir>/net/osm.net.xml`` for
    inspection; the runner rebuilds it from the scenario at run time.

    Defaults not given here are taken from the versioned
    ``scenarios/corridor_10km.yaml`` (``OSM_DEFAULTS_SCENARIO``): the fleet
    block, the ``sim`` time discretization and output cadence, and the
    replicate count. They are corridor tuning results, not calibration —
    an onboarded corridor still needs its own FD/IDM/demand calibration
    (CLAUDE.md §6) before any claim is made about it.

    Args:
        name: Scenario name.
        osm_file: ``.osm`` XML extract. Recorded in the config as given —
            keep it repository-relative (``data/osm/<corridor>.osm``) for a
            scenario that will be versioned.
        bbox: ``(south, west, north, east)`` WGS84 download window, used
            only when ``osm_file`` is ``None``. The download goes through
            the OSM API (network access; ``osm_import``), the extract is
            persisted under ``<workdir>/net/`` and recorded as ``osm_file``
            (the map changes over time, so a re-download is not
            reproducible); ``bbox`` is kept in the config for provenance.
        corridor_edges: SUMO edge ids of the analysis corridor in driving
            order (load-time ids — raw OSM way ids, ``#``-split at
            junctions; see ``osm_import``). At least one.
        inflow: Mainline demand — a constant rate [veh/s] or time-ordered
            ``(t_start_s, veh/s)`` steps (docs/CONTRACTS.md §2), total across
            lanes. Use ``flowstate_core.units.veh_h_to_veh_s`` for veh/h.
        workdir: Directory for the netconvert inputs/outputs.
        fleet: Human-driver fleet; default: the ``corridor_10km`` fleet.
        duration_s: Simulated duration [s].
        seed: Scenario master seed.
        lanes: Expected lane count of the first corridor edge (the
            insertion edge, whose real lane count sets the departure
            scheme — docs/CONTRACTS.md §2). ``None`` skips the check; a
            mismatch raises with every corridor edge's lane count. Not
            stored: an OSM corridor's lanes come from the map.
        ramps: Interchange ramps (:class:`RampSpec`); their edges are kept
            through pruning. Ramp-to-corridor connectivity is checked by the
            runner before SUMO starts.
        boundary: Optional measured downstream boundary schedule; applied
            to the last corridor edge (needs ≥ 2 corridor edges).
        av: Controlled-vehicle deployment; default: none.
        warmup_s: Metrics warm-up [s]; default: the ``corridor_10km``
            warm-up when it fits inside ``duration_s``, else 0 (a warm-up
            longer than the run would discard every sample).
        replicates: Seeded replicates; default: the ``corridor_10km`` value.

    Returns:
        A ``tier="micro"`` scenario with an :class:`OSMNetwork` and no
        perturbation (``seeded=False``).

    Raises:
        ValueError: No corridor edges, neither source, a malformed demand
            profile, a corridor edge absent from the compiled net, an
            unconnected chain, or a lane mismatch.
        RuntimeError: ``netconvert`` failure, or a bbox download that left
            no single extract to record.
    """
    if not corridor_edges:
        raise ValueError("scenario_from_osm needs at least one corridor edge")
    if osm_file is None and bbox is None:
        raise ValueError("scenario_from_osm needs osm_file or bbox")
    if duration_s <= 0.0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")
    steps = _inflow_steps(inflow)
    edges = [str(e) for e in corridor_edges]
    net_dir = Path(workdir) / "net"

    bundle = osm_import(
        osm_file=osm_file,
        bbox=bbox,
        corridor_edges=tuple(edges),
        workdir=net_dir,
        keep_edges=tuple(e for r in ramps for e in r.edges),
    )
    _check_corridor_in_net(bundle.net_path, edges, lanes)

    if osm_file is not None:
        source = str(osm_file)
    else:
        extracts = sorted(net_dir.glob("*.osm"))
        if len(extracts) != 1:
            raise RuntimeError(
                f"expected exactly one downloaded extract under {net_dir}, found {extracts}"
            )
        source = str(extracts[0])

    base = load_scenario(OSM_DEFAULTS_SCENARIO)
    if warmup_s is None:
        warmup_s = base.sim.warmup_s if base.sim.warmup_s < duration_s else 0.0
    sim = SimSpec.model_validate(
        {**base.sim.model_dump(mode="json"), "duration_s": duration_s, "warmup_s": warmup_s}
    )
    network = OSMNetwork(
        osm_file=source,
        bbox=bbox,
        corridor_edges=edges,
        inflow=steps,
        boundary=boundary,
        ramps=list(ramps),
    )
    return ScenarioConfig(
        name=name,
        tier="micro",
        network=network,
        fleet=fleet if fleet is not None else base.fleet,
        av=av if av is not None else AVSpec(),
        sim=sim,
        perturbation=None,
        seed=seed,
        replicates=replicates if replicates is not None else base.replicates,
    )
