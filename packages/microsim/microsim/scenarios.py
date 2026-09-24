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

:func:`corridor_from_bbox` is the *whole* pipeline in one call — the "any
freeway corridor from a bounding box" entry point: bbox → Overpass extract →
``netconvert`` → mainline chain and ramps discovered with :mod:`microsim.geo`
→ scenario config, plus the corridor geometry (length, lane profile, ramp
positions) and the linear-x position of each detector station given as
(lat, lon). It returns a :class:`CorridorBuild`; the demand, fleet and ramp
flows it carries are placeholders until the corridor is calibrated
(CLAUDE.md §6).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

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
from microsim.geo import (
    MAX_HEADING_DEV_DEG,
    MOTORWAY_TYPES,
    PointOnChain,
    RampCandidate,
    chain_length_m,
    chain_offsets,
    lanes_profile,
    mainline_chain,
    ramps_for_chain,
    x_of_lonlat,
)
from microsim.networks import RAMP_SPLIT_OFF, RAMP_SPLIT_ON, expand_ramp_splits, osm_import
from microsim.runner import RunPaths, run_micro
from microsim.split_audit import (
    SplitFinding,
    audit_splits,
    format_split_table,
    ramps_unset_edges,
    split_defects,
    split_patch_xml,
)

#: Repository ``scenarios/`` directory (this file sits at
#: ``packages/microsim/microsim/scenarios.py`` → three parents up is the root).
SCENARIOS_DIR: Path = Path(__file__).resolve().parents[3] / "scenarios"

#: Repository root (``scenarios/``'s parent): paths under it are recorded
#: repository-relative in generated scenario files, as the versioned
#: scenarios do (``osm_file: data/osm/…``).
REPO_ROOT: Path = SCENARIOS_DIR.parent

#: ``netconvert`` options every onboarding compiles with unless asked not to
#: (``ramp_guessing=False``; 2026-09-24). OSM rarely draws acceleration lanes —
#: a motorway is tagged three lanes straight through its merges — and a link
#: joining lane 0 at a plain junction starves under SUMO's yielding
#: (docs/ONBOARDING_MNDOT.md §6: four I-94 WB on-ramps delivered 4-6 % of
#: their demand). ``--ramps.guess`` adds a ``--ramps.ramp-length`` metre
#: acceleration / deceleration lane at every ramp; the values are the ones the
#: MnDOT scenarios use. Pairs of ``(option, value)``; ``None`` = a flag.
RAMP_GUESSING_OPTIONS: tuple[tuple[str, str | None], ...] = (
    ("--ramps.guess", None),
    ("--ramps.ramp-length", "250"),
)

#: Name of the connection patch the split fixes write beside the OSM extract:
#: ``<extract stem>.splits.con.xml`` (``data/osm/mndot_i94_wb_stpaul.splits.con.xml``
#: is the committed instance).
SPLIT_PATCH_SUFFIX: str = ".splits.con.xml"


def with_ramp_guessing(extra: Sequence[str], enabled: bool = True) -> tuple[str, ...]:
    """The ``netconvert_extra`` an onboarding compiles with.

    Adds :data:`RAMP_GUESSING_OPTIONS` in front of the caller's options —
    each one only when the caller did not already give it (as ``--opt`` or
    ``--opt=value``), so a scenario that names its own ramp length keeps it
    and nothing is passed to ``netconvert`` twice.

    Args:
        extra: The caller's own options, kept verbatim and in order.
        enabled: ``False`` returns ``extra`` unchanged (``--no-ramp-guessing``).

    Returns:
        The options, defaults first.
    """
    given = [str(a) for a in extra]
    if not enabled:
        return tuple(given)
    added: list[str] = []
    for option, value in RAMP_GUESSING_OPTIONS:
        if any(a == option or a.startswith(option + "=") for a in given):
            continue
        added.append(option)
        if value is not None:
            added.append(value)
    return (*added, *given)


def default_split_patch_path(osm_file: str | Path) -> Path:
    """Where the split fixes write their connection patch: beside the extract.

    ``data/osm/<name>.osm`` → ``data/osm/<name>.splits.con.xml``; a bbox
    download (``<workdir>/net/extract.osm``) → ``<workdir>/net/extract.splits.con.xml``.
    The patch lives with the map it corrects, so it is inside the data roots
    whenever the extract is and moves with it.
    """
    extract = Path(osm_file)
    return extract.with_name(extract.stem + SPLIT_PATCH_SUFFIX)


#: Largest perpendicular distance [m] a detector station may sit from the
#: corridor centreline and still be placed on it. Beyond it the station
#: belongs to the opposite carriageway, a frontage road or another route;
#: the same threshold the I-24 landmark projection uses
#: (``scripts/i24_geometry.py``).
MAX_STATION_OFFSET_M: float = 60.0

#: How close a discovered ramp must be to a station [m] for a lane-count
#: disagreement there to be read as ramp geometry — an acceleration or
#: auxiliary lane — rather than a plain map/inventory disagreement. It covers
#: ``netconvert``'s guessed ramp length (``--ramps.ramp-length``, 100 m by
#: default and 250 m on corridors onboarded with ramp guessing) plus the gore
#: area, so a station inside the widened stretch is attributed to it.
LANE_HINT_RAMP_WINDOW_M: float = 400.0

#: Most lanes an inventory row may claim at one mainline cross-section and
#: still be compared with the compiled map. No freeway carriageway in the
#: world runs more than about eight general-purpose lanes in one direction, so
#: a larger number is a unit error, a both-directions total or a stray column
#: — none of which says anything about this carriageway. Such a row is
#: unusable rather than a mismatch: reporting "map 3, inventory 40" as a lane
#: disagreement would put a data-entry slip into a pre-flight check that
#: exists to find map defects.
MAX_INVENTORY_LANES: int = 12

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
    download: Literal["osm_api", "overpass"] = "osm_api",
    netconvert_extra: Sequence[str] = (),
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
        download: Service a ``bbox`` is fetched from (``osm_import``):
            ``"osm_api"`` (default, unchanged) or ``"overpass"`` for windows
            larger than the OSM API allows.

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
        download=download,
        netconvert_extra=tuple(netconvert_extra),
    )
    # The compiled chain may carry netconvert's ramp-split pieces; the scenario
    # keeps the load-time ids (networks.expand_ramp_splits).
    _check_corridor_in_net(bundle.net_path, list(bundle.edge_ids), lanes)

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
        netconvert_extra=[str(a) for a in netconvert_extra],
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


def _ramp_placeholder(candidate: RampCandidate) -> RampSpec:
    """A discovered ramp as a zero-flow :class:`RampSpec` placeholder.

    Discovery knows where a ramp is, never how much it carries: the flows are
    a calibration input (ramp counts, CLAUDE.md §6.3). The placeholder is
    therefore explicit about carrying none — an on-ramp with a single
    ``(0 s, 0 veh/s)`` step and an off-ramp with a ``(0 s, 0)`` exit
    fraction, which is what ``RampSpec`` requires as a non-empty profile —
    so the scenario runs with the geometry in place and the operator has one
    number per ramp to fill in.

    The two ends of a collector–distributor road carry ``cd_road`` and their
    shared ``cd_pair`` into the spec, so the demand step can pair them. A
    candidate with ``attach_via_cd`` attaches to the C-D road, not to a
    corridor edge, and has no spec (:func:`corridor_from_bbox` lists it in
    the inventory only).

    Raises:
        ValueError: The candidate attaches via a C-D road.
    """
    if candidate.attach_via_cd:
        raise ValueError(
            f"ramp {candidate.edges[0]} attaches to C-D road {candidate.attach_via_cd}, "
            "not to a corridor edge; it cannot be a RampSpec"
        )
    if candidate.cd_road:
        role = "split" if candidate.kind == "off" else "re-entry"
        label = candidate.name or f"C-D {role} {candidate.edges[0]}"
    else:
        label = candidate.name or f"{candidate.kind}-ramp {candidate.edges[0]}"
    if candidate.kind == "on":
        return RampSpec(
            kind="on",
            edges=list(candidate.edges),
            attach_edge=candidate.attach_edge,
            inflow=[(0.0, 0.0)],
            name=label,
            cd_road=candidate.cd_road,
            cd_pair=candidate.cd_pair if candidate.cd_road else "",
        )
    return RampSpec(
        kind="off",
        edges=list(candidate.edges),
        attach_edge=candidate.attach_edge,
        exit_fraction=[(0.0, 0.0)],
        name=label,
        cd_road=candidate.cd_road,
        cd_pair=candidate.cd_pair if candidate.cd_road else "",
    )


def _record_path(path: Path) -> str:
    """Record a path repository-relative when it lies inside the repository."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _station_rows(
    stations: Sequence[Mapping[str, Any]],
) -> list[tuple[str, float, float]]:
    """Normalize station dicts to ``(id, lon, lat)``.

    Accepts ``id`` or ``station`` for the identifier (the column name a
    detector-inventory CSV usually carries), and requires ``lat``/``lon``.
    """
    rows: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    for i, row in enumerate(stations):
        raw_id = row.get("id", row.get("station", ""))
        station_id = str(raw_id).strip()
        if not station_id:
            raise ValueError(f"station #{i} has no 'id' (or 'station') field: {dict(row)}")
        if station_id in seen:
            raise ValueError(f"duplicate station id {station_id!r}")
        seen.add(station_id)
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"station {station_id!r} needs numeric 'lat'/'lon': {exc}") from exc
        rows.append((station_id, lon, lat))
    return rows


@dataclass(frozen=True)
class LaneMismatch:
    """One station where the compiled network and the detector inventory disagree.

    The compiled lane count is what SUMO will actually simulate at that
    position; the inventory count is how many lanes the agency's detector
    station covers. They must agree before a battery runs: a corridor whose
    map is a lane short at a merge starves the on-ramps it was calibrated
    with, and nothing downstream of that — flows, GEH, wave speeds — means
    anything (docs/ONBOARDING_MNDOT.md §7).

    Attributes:
        station: Inventory station id.
        x_m: Position along the corridor chain [m] (docs/CONTRACTS.md §3).
        compiled_lanes: Lane count of the compiled network there.
        inventory_lanes: Lane count the detector inventory reports.
        hint: One line naming the likeliest cause. It is a triage aid, not a
            diagnosis: the network and the inventory are both evidence and
            which one is wrong is for the operator to decide.
    """

    station: str
    x_m: float
    compiled_lanes: int
    inventory_lanes: int
    hint: str

    @property
    def delta(self) -> int:
        """Compiled minus inventory lanes (positive: the map has more)."""
        return self.compiled_lanes - self.inventory_lanes


def lanes_at_x(profile: Sequence[tuple[float, float, int]], x_m: float) -> int | None:
    """Compiled lane count at a chain position.

    Args:
        profile: ``(x_start_m, x_end_m, lanes)`` runs, as
            :attr:`CorridorBuild.lanes_profile` carries them.
        x_m: Position along the chain [m].

    Returns:
        The lane count of the run containing ``x_m`` (the last run at the
        chain's exact end), or ``None`` when ``x_m`` lies off the chain.
    """
    for x0, x1, lanes in profile:
        if x0 <= x_m < x1:
            return int(lanes)
    if profile and x_m == profile[-1][1]:
        return int(profile[-1][2])
    return None


def _lane_hint(delta: int, x_m: float, ramps: Sequence[RampCandidate]) -> str:
    """One line naming the likeliest cause of a lane-count disagreement.

    Args:
        delta: Compiled minus inventory lanes (non-zero).
        x_m: Station position along the chain [m].
        ramps: Discovered ramps, for their positions.

    Returns:
        A short hint. Near a ramp the disagreement is read as ramp geometry:
        an extra compiled lane is the acceleration lane ``netconvert``'s
        ``--ramps.guess`` builds. A *missing* compiled lane has two readings
        and the hint names both, because one of them is the defect this
        check exists for: an auxiliary/deceleration lane the detector
        station covers but the map does not carry, **or** an acceleration
        lane the map does not carry at all — a mainline tagged straight
        through its merge, which starves the on-ramp. Away from every ramp
        neither story applies and the hint says only that the two sources
        differ.
    """
    near_ramp = any(abs(float(r.x_m) - x_m) <= LANE_HINT_RAMP_WINDOW_M for r in ramps)
    if near_ramp and delta > 0:
        return "acceleration lane added by ramp guessing"
    if near_ramp and delta < 0:
        return "auxiliary lane in the inventory, or an acceleration lane the map does not carry"
    return "map lane count differs from the inventory"


@dataclass(frozen=True)
class CorridorBuild:
    """A corridor onboarded from a bounding box (CLAUDE.md §3.2.4).

    Everything :func:`corridor_from_bbox` learned about the corridor: the
    runnable scenario, the geometry needed to read its outputs (linear x is
    measured along ``chain_edges``, docs/CONTRACTS.md §3), and where the
    detector stations fall on it.

    Attributes:
        config: The validated scenario. Its demand, fleet and ramp flows are
            defaults and placeholders — the corridor is NOT calibrated
            (CLAUDE.md §6) and nothing computed from this scenario is a
            claim about the real road until it is.
        chain_edges: Mainline edge ids, upstream → downstream (also
            ``config.network.corridor_edges``).
        length_m: Chain length [m] = the corridor's linear-x extent.
        lanes_profile: ``(x_start_m, x_end_m, lanes)`` runs along the chain.
        ramps: Ramps discovered beside the chain, in position order; they
            appear in ``config`` as zero-flow :class:`RampSpec` placeholders.
        net_path: The compiled ``.net.xml`` the geometry was measured on.
        osm_file: The persisted OSM extract the scenario rebuilds from.
        bbox: The ``(south, west, north, east)`` window it came from.
        bearing_deg: Requested travel direction (compass degrees).
        station_x: Accepted stations → their :class:`PointOnChain`.
        stations_rejected: Stations whose perpendicular offset exceeded
            ``max_station_offset_m`` — they sit on another carriageway or
            another road and must not be compared against this corridor.
        max_station_offset_m: The offset threshold used [m].
        split_audit: Every exit leaving the chain audited against the
            extract, for the network the scenario will compile (after the
            split fixes, when any were applied).
        split_audit_before_fixes: The audit the fixes were derived from;
            ``None`` when no fix was applied (the two audits are then one).
        split_fixes_applied: Defects the fixes addressed (``wrong_side`` by
            the connection patch, ``added_lane_wrong_side`` by
            ``--ramps.unset``); 0 when none were needed or fixes were off.
        split_fixes: Whether fixes were asked for (``corridor_from_bbox``'s
            ``split_fixes``); the summary says ``off`` when not.
        split_patch_file: The connection patch written, when a
            ``wrong_side`` finding needed one.
    """

    config: ScenarioConfig
    chain_edges: tuple[str, ...]
    length_m: float
    lanes_profile: tuple[tuple[float, float, int], ...]
    ramps: tuple[RampCandidate, ...]
    net_path: Path
    osm_file: Path
    bbox: tuple[float, float, float, float]
    bearing_deg: float
    station_x: dict[str, PointOnChain] = field(default_factory=dict)
    stations_rejected: dict[str, PointOnChain] = field(default_factory=dict)
    max_station_offset_m: float = MAX_STATION_OFFSET_M
    split_audit: tuple[SplitFinding, ...] = ()
    """Every exit leaving the chain, audited against the extract
    (:func:`microsim.split_audit.audit_splits`, docs/ONBOARDING_MNDOT.md §9):
    the side OSM draws it on versus the lanes the compiled net feeds it from."""
    split_audit_before_fixes: tuple[SplitFinding, ...] | None = None
    split_fixes_applied: int = 0
    split_fixes: bool = True
    split_patch_file: Path | None = None

    def to_yaml(self, path: str | Path) -> None:
        """Write the scenario YAML (``ScenarioConfig.to_yaml``)."""
        self.config.to_yaml(path)

    def split_defects(self) -> list[SplitFinding]:
        """The audited splits compiled on the wrong side (``wrong_side`` / ``added_lane_wrong_side``)."""
        return split_defects(self.split_audit)

    @property
    def ramp_guessing(self) -> bool:
        """Whether the scenario compiles with ``--ramps.guess`` (``network.netconvert_extra``)."""
        extra = getattr(self.config.network, "netconvert_extra", [])
        return any(a == "--ramps.guess" or a.startswith("--ramps.guess=") for a in extra)

    def applied_line(self) -> str:
        """What the onboarding applied, in one line of the inventory.

        ``ramp guessing on; split fixes: 2 applied, 0 remaining`` — the
        guessing state, how many defects the fixes addressed and how many the
        network the scenario compiles still has (``off`` when fixes were not
        asked for, so a remaining defect is read as a choice, not a failure).
        """
        guessing = "on" if self.ramp_guessing else "off"
        remaining = len(self.split_defects())
        if self.split_fixes or self.split_fixes_applied:
            fixes = f"{self.split_fixes_applied} applied, {remaining} remaining"
        else:
            fixes = f"off, {remaining} remaining"
        return f"ramp guessing {guessing}; split fixes: {fixes}"

    def _lane_scan(
        self, stations: Sequence[Mapping[str, Any]]
    ) -> tuple[list[tuple[str, float, int]], list[tuple[str, str]]]:
        """Split the inventory into comparable rows and unusable lane counts.

        A row is comparable when it names a station, is mainline (``kind``
        absent or ``"mainline"``), carries an integer ``lanes`` between 1 and
        :data:`MAX_INVENTORY_LANES`, and
        has a position on this chain — the build's own projection
        (:attr:`station_x`) when it placed the station, else a numeric
        ``x_m`` already in the row. Stations the projection rejected, and
        positions off the chain, are dropped: they are not on this
        carriageway, so their lane count says nothing about it.

        A mainline row that *states* a lane count the contract cannot use —
        not a number, not integral, or outside 1 …
        :data:`MAX_INVENTORY_LANES` — is unusable rather than comparable, and
        is collected so the summary can say so. A row that states no count at
        all is neither: nothing was claimed about it.

        Args:
            stations: The onboarding station table.

        Returns:
            ``(comparable, unusable)``: the comparable rows as
            ``(id, chain x [m], inventory lanes)``, and the unusable ones as
            ``(id, the lane count as written)``.
        """
        rows: list[tuple[str, float, int]] = []
        unusable: list[tuple[str, str]] = []
        for row in stations:
            station_id = str(row.get("station", row.get("id", ""))).strip()
            if not station_id or station_id in self.stations_rejected:
                continue
            kind = str(row.get("kind") or "mainline").strip().lower()
            if kind != "mainline":
                continue
            raw = row.get("lanes")
            written = "" if raw is None else str(raw).strip()
            if not written:
                continue  # no lane count stated; nothing to agree or disagree with
            try:
                lane_value = float(written)
            except ValueError:
                unusable.append((station_id, written))
                continue
            # A non-integral count is not a lane count: "3.7" truncated to 3
            # invented an agreement (or a disagreement) the inventory never
            # stated. Drop the row as unusable rather than guess.
            if not math.isfinite(lane_value) or lane_value != int(lane_value):
                unusable.append((station_id, written))
                continue
            lanes = int(lane_value)
            if lanes <= 0 or lanes > MAX_INVENTORY_LANES:
                unusable.append((station_id, written))
                continue
            point = self.station_x.get(station_id)
            if point is not None:
                x_m = float(point.x_m)
            else:
                try:
                    x_m = float(str(row.get("x_m")).strip())
                except (TypeError, ValueError):
                    continue
            if lanes_at_x(self.lanes_profile, x_m) is None:
                continue
            rows.append((station_id, x_m, lanes))
        return rows, unusable

    def _lane_rows(self, stations: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, int]]:
        """The comparable rows of :meth:`_lane_scan`."""
        return self._lane_scan(stations)[0]

    def lanes_unusable(self, stations: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
        """Mainline rows whose stated lane count cannot be compared.

        The rows :meth:`lane_check` neither matched nor reported: a lane
        count that is not a whole number between 1 and
        :data:`MAX_INVENTORY_LANES` is a data-entry slip, not a lane
        disagreement, and putting "map 3, inventory 40" in a pre-flight check
        for map defects would bury the real ones. Dropping such a row in
        silence is the other failure — "7 of 7 stations match" then counts 7
        of 8 — so :meth:`summary` names how many there were.

        Args:
            stations: The onboarding station table.

        Returns:
            ``(station id, the lane count as written)`` per unusable row, in
            table order.
        """
        return self._lane_scan(stations)[1]

    def lane_check(
        self, stations: Sequence[Mapping[str, Any]], *, tolerance: int = 0
    ) -> list[LaneMismatch]:
        """Compare the compiled lane profile with the detector inventory.

        The pre-flight check of docs/ONBOARDING_MNDOT.md §7: at every
        mainline station, how many lanes the compiled network carries versus
        how many the agency's inventory says are there. It costs one build
        and catches, before a 20-seed battery is spent on it, the two
        onboarding defects that silently ruin one — a map that tags the
        mainline straight through its merges (no acceleration lane, so the
        on-ramps starve) and a map whose lane count is simply wrong.

        Args:
            stations: The onboarding station table: mappings with
                ``station`` (or ``id``), ``lanes``, an optional ``kind``
                (only ``mainline`` rows are compared) and, for a station
                this build did not place itself, a numeric ``x_m``.
            tolerance: Largest lane difference not reported; ``0`` (the
                default) reports every disagreement.

        Returns:
            The mismatches, ordered by position along the corridor. An empty
            list means every comparable mainline station agrees — it does
            NOT mean the map is right where no station stands.
        """
        mismatches = []
        for station_id, x_m, inventory in self._lane_rows(stations):
            compiled = lanes_at_x(self.lanes_profile, x_m)
            assert compiled is not None  # _lane_rows dropped the off-chain rows
            if abs(compiled - inventory) <= tolerance:
                continue
            mismatches.append(
                LaneMismatch(
                    station=station_id,
                    x_m=x_m,
                    compiled_lanes=compiled,
                    inventory_lanes=inventory,
                    hint=_lane_hint(compiled - inventory, x_m, self.ramps),
                )
            )
        return sorted(mismatches, key=lambda m: m.x_m)

    def lanes_compared(self, stations: Sequence[Mapping[str, Any]]) -> int:
        """How many mainline stations :meth:`lane_check` was able to compare.

        The denominator of the lanes-vs-inventory line: rows that are not
        mainline, carry no usable ``lanes``, or sit off this chain are not
        compared and are not counted here.
        """
        return len(self._lane_rows(stations))

    def summary(self, stations: Sequence[Mapping[str, Any]] | None = None) -> str:
        """A plain-text report of what was discovered (for a CLI or a log).

        Args:
            stations: The onboarding station table. When given, the report
                ends with the lanes-vs-inventory block
                (:meth:`lane_check`); without it that check is not run and
                the block is omitted. An empty table, or one with no
                comparable mainline row, says so rather than reporting
                "0 of 0 ... match", which reads as a check that passed, and
                mainline rows whose stated lane count could not be used are
                counted beside the tally (:meth:`lanes_unusable`) rather than
                dropped in silence.
        """
        south, west, north, east = self.bbox
        lines = [
            f"corridor {self.config.name}: {self.length_m / 1000.0:.2f} km along "
            f"{len(self.chain_edges)} edges, bearing {self.bearing_deg:g}°",
            f"  bbox      {south:.5f},{west:.5f} .. {north:.5f},{east:.5f}",
            f"  extract   {self.osm_file}",
            f"  network   {self.net_path}",
            f"  chain     {' '.join(self.chain_edges)}",
            "  lanes",
        ]
        lines += [
            f"    {x0 / 1000.0:7.3f} - {x1 / 1000.0:7.3f} km: {lanes} lanes"
            for x0, x1, lanes in self.lanes_profile
        ]
        inventory_only = sum(1 for r in self.ramps if r.attach_via_cd)
        lines.append(
            f"  ramps ({len(self.ramps)}"
            + (f"; {inventory_only} attach to a C-D road, inventory only" if inventory_only else "")
            + "; flows are 0 placeholders)"
        )
        lines += [
            f"    {r.kind:>3s} x={r.x_m / 1000.0:7.3f} km  attach {r.attach_edge:>12s}  "
            f"edges {','.join(r.edges)}"
            + (f'  "{r.name}"' if r.name else "")
            + f"  [{_ramp_discovery_note(r)}]"
            for r in self.ramps
        ]
        if self.split_audit_before_fixes is not None:
            lines += format_split_table(self.split_audit_before_fixes, label="splits before fixes")
        if self.split_audit:
            lines += format_split_table(self.split_audit)
        lines.append(f"  applied   {self.applied_line()}")
        if self.split_fixes_applied:
            network = self.config.network
            detail = f"netconvert_extra {' '.join(getattr(network, 'netconvert_extra', []))}"
            if self.split_patch_file is not None:
                detail = f"patch {self.split_patch_file}; " + detail
            lines.append(f"            {detail}; network re-imported and audited again")
        if self.station_x or self.stations_rejected:
            total = len(self.station_x) + len(self.stations_rejected)
            lines.append(
                f"  stations  {len(self.station_x)}/{total} on the corridor "
                f"(offset <= {self.max_station_offset_m:g} m)"
            )
            for sid, p in sorted(self.station_x.items(), key=lambda kv: kv[1].x_m):
                lines.append(
                    f"    {sid:>12s} x={p.x_m / 1000.0:7.3f} km  offset {p.offset_m:5.1f} m  "
                    f"{p.edge_id}@{p.lane_pos:.0f}"
                )
            for sid, p in sorted(self.stations_rejected.items(), key=lambda kv: kv[1].offset_m):
                lines.append(
                    f"    {sid:>12s} REJECTED offset {p.offset_m:8.1f} m "
                    f"(nearest x={p.x_m / 1000.0:.3f} km)"
                )
        if stations is not None:
            comparable, unusable = self._lane_scan(stations)
            compared = len(comparable)
            mismatches = self.lane_check(stations)
            # A row whose lane count the check could not use is not part of
            # "n of n match": without this clause a table of eight stations
            # one of which says "lanes=14" reports "7 of 7 match", which reads
            # as a clean inventory rather than as one that was not all read.
            suffix = ""
            if unusable:
                plural = "row" if len(unusable) == 1 else "rows"
                listed = ", ".join(f"lanes={value}" for _, value in unusable)
                suffix = f"; {len(unusable)} {plural} unusable ({listed})"
            if not stations:
                # "0 of 0 match" reads as a check that passed; nothing was
                # checked at all.
                lines.append("  lanes vs inventory: no station table given")
            elif compared == 0:
                lines.append(f"  lanes vs inventory: no comparable mainline stations{suffix}")
            else:
                lines.append(
                    f"  lanes vs inventory: {compared - len(mismatches)} of "
                    f"{compared} mainline stations match{suffix}"
                )
            lines += [
                f"    {m.station:>12s} x={m.x_m / 1000.0:7.3f} km  map {m.compiled_lanes} lanes, "
                f"inventory {m.inventory_lanes} lanes  ({m.hint})"
                for m in mismatches
            ]
        return "\n".join(lines)


def _ramp_discovery_note(r: RampCandidate) -> str:
    """How a ramp was found, for the inventory print (``RampCandidate.discovery``)."""
    note = str(r.discovery)
    if r.cd_road:
        note += f", C-D pair {r.cd_pair}"
        if r.kind == "off" and r.rejoin_x_m is not None:
            note += f", rejoins {r.rejoin_edge} at x={r.rejoin_x_m / 1000.0:.3f} km"
    if r.attach_via_cd:
        note += f", via C-D road {r.attach_via_cd}: inventory only"
    return note


def _ramp_x(net: Any, offsets: Mapping[str, float], attach_edge: str, kind: str) -> float:
    """Chain x of a ramp: an entrance at the start of its (split) attach piece, an exit at the end."""
    if kind == "on":
        piece = attach_edge + RAMP_SPLIT_ON
        return float(offsets[piece] if piece in offsets else offsets[attach_edge])
    piece = attach_edge + RAMP_SPLIT_OFF
    edge_id = piece if piece in offsets else attach_edge
    return float(offsets[edge_id] + net.getEdge(edge_id).getLength())


def corridor_from_bbox(
    name: str,
    bbox: tuple[float, float, float, float],
    bearing_deg: float,
    *,
    inflow: Sequence[tuple[float, float]] | float,
    workdir: str | Path,
    start_near: tuple[float, float] | None = None,
    stations: Sequence[Mapping[str, Any]] | None = None,
    duration_s: float = 1200.0,
    seed: int = 0,
    osm_file: str | Path | None = None,
    download: Literal["osm_api", "overpass"] = "overpass",
    highway_types: Sequence[str] = MOTORWAY_TYPES,
    max_heading_dev_deg: float = MAX_HEADING_DEV_DEG,
    max_station_offset_m: float = MAX_STATION_OFFSET_M,
    discover_ramps: bool = True,
    fleet: FleetSpec | None = None,
    av: AVSpec | None = None,
    boundary: BoundarySpec | None = None,
    warmup_s: float | None = None,
    replicates: int | None = None,
    netconvert_extra: Sequence[str] = (),
    max_chain_m: float | None = None,
    ramp_guessing: bool = True,
    split_fixes: bool = True,
    split_patch_path: str | Path | None = None,
) -> CorridorBuild:
    """Onboard any freeway corridor from a bounding box (CLAUDE.md §3.2.4).

    The whole ``osm_generic`` path in one call:

    1. **Extract** — the bbox is fetched through the Overpass API filtered to
       motorway ways and persisted at ``<workdir>/net/extract.osm``; an
       extract already there (or one passed as ``osm_file``) is reused, so
       the build is repeatable even though the map is not.
    2. **Discovery import** — ``netconvert`` with ``geometry_remove=False``,
       i.e. raw OSM way ids, the granularity corridor edges must be named at
       (:func:`microsim.networks.osm_import`).
    3. **Chain** — :func:`microsim.geo.mainline_chain` follows the motorway
       edges travelling in ``bearing_deg`` (270 = westbound), seeded at
       ``start_near`` when given, and stops at the window's edge.
    4. **Ramps** — :func:`microsim.geo.ramps_for_chain` finds the
       ``motorway_link`` chains joining and leaving it; they enter the
       scenario as **zero-flow placeholders** (:func:`_ramp_placeholder`).
    5. **Scenario** — :func:`scenario_from_osm` re-imports with the corridor
       and ramp edges pinned, checks the chain against the compiled net and
       returns the validated config. The import carries ramp guessing
       (:data:`RAMP_GUESSING_OPTIONS`) unless ``ramp_guessing=False``:
       without it an entrance the map draws without an acceleration lane
       starves (docs/ONBOARDING_MNDOT.md §6).
    6. **Geometry** — chain length, lane profile, ramp positions and each
       station's linear x are measured on that compiled net.
    7. **Split audit and fixes** — every exit leaving the chain is audited
       for the side it was compiled on (:func:`microsim.split_audit.audit_splits`);
       with ``split_fixes`` (the default) a ``wrong_side`` /
       ``added_lane_wrong_side`` finding is fixed by :func:`apply_split_fixes`
       — the connection patch written beside the extract
       (:func:`default_split_patch_path`, or ``split_patch_path``) and
       ``--ramps.unset`` added — the network re-imported and audited again.
       Both audits are kept (``split_audit_before_fixes``, ``split_audit``).

    What comes back is runnable, not calibrated: the demand is whatever
    ``inflow`` says, the ramps carry nothing, and the fleet is the
    ``corridor_10km`` default population. Calibration (FD, IDM population,
    demand) is CLAUDE.md §6 and happens after onboarding.

    Args:
        name: Scenario name.
        bbox: ``(south, west, north, east)`` WGS84 window around the corridor.
        bearing_deg: Direction of travel, compass degrees (0 = north,
            90 = east, 180 = south, 270 = west).
        inflow: Mainline demand — constant [veh/s] or ``(t_start_s, veh/s)``
            steps, total across lanes (``flowstate_core.units.veh_h_to_veh_s``
            converts from veh/h).
        workdir: Build directory; the extract and the compiled network land
            under ``<workdir>/net/``.
        start_near: ``(lon, lat)`` anchor picking which corridor the chain
            follows when the window holds more than one motorway in that
            direction.
        stations: Detector stations as mappings with ``id`` (or ``station``),
            ``lat`` and ``lon``. Each is projected onto the chain; those
            farther than ``max_station_offset_m`` from it are reported
            separately instead of being silently placed.
        duration_s: Simulated duration [s].
        seed: Scenario master seed.
        osm_file: Use this extract instead of downloading (the bbox is still
            recorded for provenance).
        download: ``"overpass"`` (default here — the OSM API refuses windows
            this size) or ``"osm_api"``.
        highway_types: SUMO edge types treated as mainline.
        max_heading_dev_deg: Heading tolerance for the chain walk [deg].
        max_station_offset_m: Station acceptance threshold [m].
        discover_ramps: Set ``False`` for a mainline-only scenario (the
            gallery convention); the corridor then conserves no ramp flow.
        fleet: Human-driver fleet; default: the ``corridor_10km`` fleet.
        av: Controlled-vehicle deployment; default: none.
        boundary: Optional measured downstream boundary schedule.
        warmup_s: Metrics warm-up [s]; default: the ``corridor_10km`` value
            when it fits inside ``duration_s``.
        replicates: Seeded replicates; default: the ``corridor_10km`` value.
        netconvert_extra: The caller's own ``netconvert`` options, recorded
            in the scenario; the ramp-guessing defaults are added in front
            of them unless already given (:func:`with_ramp_guessing`).
        max_chain_m: Drop chain edges starting beyond this length [m].
        ramp_guessing: Compile with :data:`RAMP_GUESSING_OPTIONS` (default).
        split_fixes: Apply the split audit's remedies and re-audit (default);
            ``False`` reports the defects and leaves the network as compiled.
        split_patch_path: Where the connection patch goes when a
            ``wrong_side`` finding needs one; default: beside the extract.

    Returns:
        The :class:`CorridorBuild`.

    Raises:
        ValueError: Degenerate or out-of-range bbox, no motorway edge
            heading that way, a malformed station row, or any of the
            :func:`scenario_from_osm` validation failures (corridor edge
            missing from the compiled net, broken chain, bad demand).
        RuntimeError: ``netconvert`` or the Overpass download failed.
    """
    south, west, north, east = (float(v) for v in bbox)
    if not (south < north and west < east):
        raise ValueError(
            f"bbox must be (south, west, north, east) with south<north, west<east: {bbox}"
        )
    if not (-90.0 <= south and north <= 90.0 and -180.0 <= west and east <= 180.0):
        raise ValueError(f"bbox outside WGS84 range: {bbox}")
    box = (south, west, north, east)
    work = Path(workdir)
    net_dir = work / "net"

    # Discovery pass: raw way ids (a geometry-joined id would prune to one way).
    raw = osm_import(
        osm_file=osm_file,
        bbox=box,
        workdir=net_dir,
        geometry_remove=False,
        download=download,
    )
    raw_net = sumolib.net.readNet(str(raw.net_path))
    chain = mainline_chain(
        raw_net,
        bearing_deg,
        start_near=start_near,
        highway_types=highway_types,
        max_heading_dev_deg=max_heading_dev_deg,
    )
    if max_chain_m is not None:
        # Keep the edges that START before the cap, so the corridor ends on a
        # chosen edge (e.g. before a downstream widening that would defeat the
        # exit-speed boundary).
        kept: list[str] = []
        x = 0.0
        for edge_id in chain:
            if x >= max_chain_m:
                break
            kept.append(edge_id)
            x += float(raw_net.getEdge(edge_id).getLength())
        chain = kept
    candidates = ramps_for_chain(raw_net, chain) if discover_ramps else []
    extra = with_ramp_guessing(netconvert_extra, ramp_guessing)

    extract = Path(osm_file) if osm_file is not None else net_dir / "extract.osm"
    cfg = scenario_from_osm(
        name=name,
        osm_file=extract,
        bbox=box,
        corridor_edges=chain,
        inflow=inflow,
        workdir=work,
        fleet=fleet,
        duration_s=duration_s,
        seed=seed,
        ramps=[_ramp_placeholder(c) for c in candidates if not c.attach_via_cd],
        boundary=boundary,
        av=av,
        warmup_s=warmup_s,
        replicates=replicates,
        netconvert_extra=extra,
    )
    recorded = _record_path(extract)
    if recorded != cfg.network.osm_file:
        dumped = cfg.model_dump(mode="json")
        dumped["network"]["osm_file"] = recorded
        cfg = ScenarioConfig.model_validate(dumped)

    # Measure on the pruned network the runner will rebuild, not the raw one:
    # scenario_from_osm re-imported into the same file, pinning the chain ids.
    net_path = net_dir / "osm.net.xml"
    net = sumolib.net.readNet(str(net_path))
    # Measure on the compiled chain (ramp-split pieces included); the scenario
    # names the load-time ids.
    chain = expand_ramp_splits(chain, [e.getID() for e in net.getEdges(withInternal=False)])
    offsets = dict(zip(chain, chain_offsets(net, chain), strict=True))
    # A ramp attaching to a C-D road keeps its raw-net position (its attach
    # edge is not on the chain); a C-D split also re-measures its rejoin.
    ramps = tuple(
        c
        if c.attach_via_cd
        else replace(
            c,
            x_m=_ramp_x(net, offsets, c.attach_edge, c.kind),
            rejoin_x_m=(
                _ramp_x(net, offsets, c.rejoin_edge, "on") if c.rejoin_edge else c.rejoin_x_m
            ),
        )
        for c in candidates
    )
    accepted: dict[str, PointOnChain] = {}
    rejected: dict[str, PointOnChain] = {}
    for station_id, lon, lat in _station_rows(stations or ()):
        point = x_of_lonlat(net, chain, lon, lat)
        target = accepted if point.offset_m <= max_station_offset_m else rejected
        target[station_id] = point
    build = CorridorBuild(
        config=cfg,
        chain_edges=tuple(chain),
        length_m=chain_length_m(net, chain),
        lanes_profile=tuple(lanes_profile(net, chain)),
        ramps=ramps,
        net_path=net_path,
        osm_file=extract,
        bbox=box,
        bearing_deg=float(bearing_deg),
        station_x=accepted,
        stations_rejected=rejected,
        max_station_offset_m=float(max_station_offset_m),
        # Every exit's side, compiled versus drawn (docs/ONBOARDING_MNDOT.md §9):
        # a lane count can be right at every station while an exit is fed from
        # the wrong side of the road, and that traps through traffic.
        split_audit=tuple(audit_splits(net_path, extract, cfg.network.corridor_edges)),
        split_fixes=split_fixes,
    )
    if split_fixes and build.split_defects():
        # The remedy is applied and the network re-imported before the build
        # goes out, so the scenario names what it will compile; the audit it
        # was derived from stays on the build.
        if split_patch_path:
            build = apply_split_fixes(build, Path(split_patch_path))
        else:
            # Two corridors onboarded from one extract (``--osm-file``) share
            # the default path; a patch already there that says something
            # else belongs to the other corridor and is not overwritten.
            build = apply_split_fixes(
                build,
                default_split_patch_path(extract),
                fallback_path=extract.with_name(f"{extract.stem}.{name}{SPLIT_PATCH_SUFFIX}"),
            )
    return build


def _connection_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "<connection " in line]


def apply_split_fixes(
    build: CorridorBuild, patch_path: Path | None, *, fallback_path: Path | None = None
) -> CorridorBuild:
    """Write the remedies the split audit named into the scenario and re-audit.

    ``wrong_side`` findings become a connection patch
    (:func:`microsim.split_audit.split_patch_xml`) written at ``patch_path``
    and added to ``OSMNetwork.patch_files``; ``added_lane_wrong_side``
    findings add ``--ramps.unset <edge>`` to ``netconvert_extra``. The
    network is then re-imported with the fixes into the build's own net
    directory (the one the runner rebuilds from the scenario anyway) and
    audited again, so the returned build states what the scenario will
    actually compile — a fix that leaves a defect is reported, not assumed.

    Args:
        build: The onboarded corridor.
        patch_path: Where the connection patch goes when one is needed
            (recorded repository-relative in the scenario when it lies inside
            the repository). ``None`` refuses a ``wrong_side`` finding with
            :class:`ValueError`, since there is nowhere to write its fix.
        fallback_path: Written instead when ``patch_path`` already holds a
            patch whose connection lines differ (another corridor's, from
            the same extract); ``None`` overwrites.

    Returns:
        ``build`` unchanged when the audit found no defect; otherwise a new
        build with the fixed config, the re-imported network's lane profile
        and length, and the post-fix audit, with the audit it started from
        as ``split_audit_before_fixes`` (the first one, when applied twice),
        the defects addressed added to ``split_fixes_applied`` and the patch
        written as ``split_patch_file``. Ramp and station positions are
        kept: the fixes change lanes and connections, not the chain's length.

    Raises:
        ValueError: A ``wrong_side`` finding and no ``patch_path``, or a
            network that is not an :class:`OSMNetwork`.
    """
    defects = build.split_defects()
    if not defects:
        return build
    net = build.config.network
    if not isinstance(net, OSMNetwork):
        raise ValueError("split fixes apply to an OSM corridor only")
    dumped = build.config.model_dump(mode="json")
    network = dumped["network"]

    patch_xml = split_patch_xml(defects, note=f"scenario {build.config.name}")
    patches = [Path(p) for p in net.patch_files]
    if patch_xml:
        if patch_path is None:
            raise ValueError(
                "a wrong_side split needs a connection patch: give a path to write it to"
            )
        if (
            fallback_path is not None
            and patch_path.is_file()
            and _connection_lines(patch_path.read_text()) != _connection_lines(patch_xml)
        ):
            patch_path = fallback_path
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch_xml)
        recorded = _record_path(patch_path)
        if recorded not in network["patch_files"]:
            network["patch_files"].append(recorded)
        if patch_path not in patches:
            patches.append(patch_path)

    extra = [str(a) for a in net.netconvert_extra]
    unset = ramps_unset_edges(defects)
    if unset:
        # netconvert refuses a repeated option ("a value for the option
        # 'ramps.unset' was already set"), so an existing list — given as
        # ``--ramps.unset a,b`` or ``--ramps.unset=a,b`` — is extended in place.
        at = next(
            (
                i
                for i, a in enumerate(extra)
                if a == "--ramps.unset" or a.startswith("--ramps.unset=")
            ),
            None,
        )
        if at is None:
            extra += ["--ramps.unset", ",".join(unset)]
        elif extra[at] == "--ramps.unset" and at + 1 < len(extra):
            present = extra[at + 1].split(",")
            extra[at + 1] = ",".join([*present, *[e for e in unset if e not in present]])
        else:
            present = extra[at].partition("=")[2].split(",") if "=" in extra[at] else []
            merged = ",".join([*[p for p in present if p], *[e for e in unset if e not in present]])
            extra[at : at + 1] = [f"--ramps.unset={merged}"]
    network["netconvert_extra"] = extra
    cfg = ScenarioConfig.model_validate(dumped)

    bundle = osm_import(
        osm_file=build.osm_file,
        corridor_edges=tuple(net.corridor_edges),
        workdir=build.net_path.parent,
        keep_edges=tuple(e for r in net.ramps for e in r.edges),
        patch_files=patches,
        netconvert_extra=tuple(extra),
    )
    compiled = sumolib.net.readNet(str(bundle.net_path))
    chain = list(bundle.edge_ids)
    return replace(
        build,
        config=cfg,
        chain_edges=tuple(chain),
        length_m=chain_length_m(compiled, chain),
        lanes_profile=tuple(lanes_profile(compiled, chain)),
        net_path=bundle.net_path,
        split_audit=tuple(audit_splits(bundle.net_path, build.osm_file, net.corridor_edges)),
        split_audit_before_fixes=(
            build.split_audit
            if build.split_audit_before_fixes is None
            else build.split_audit_before_fixes
        ),
        split_fixes_applied=build.split_fixes_applied + len(defects),
        split_patch_file=patch_path if patch_xml else build.split_patch_file,
    )
