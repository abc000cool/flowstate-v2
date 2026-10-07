"""Rebuild the I-94 corridor's observation-derived inputs on the calibration days only.

docs/I94_CALIBRATION_DAYS.md. The calibrated I-94 scenario
(``scenarios/mndot_i94_wb_stpaul_weave_dc.yaml``) carries a mainline inflow,
17 ramp profiles and a downstream speed schedule that were derived
(``scripts/corridor_demand.py``, 2026-09-24) from
``data/mndot/mndot_i94_wb_stpaul/observations.json``, the mean of all nine
fetched weekdays — four of which the corridor study protocol later made its
validation days (``artifacts/p1_rehearsal_2026-10-04/day_split.json``, seed
20261004). docs/FRISCO_PROTOCOL.md §3.5 and §7 allow calibration to use the
calibration days only. This script re-derives those series from the
calibration-day targets (``observations_calibration.json``: the five
calibration days, quality-masked) with the corridor's own demand method
(:func:`calibration.onboarding.calibrate_scenario`) under one balance rule,
``carry_residuals=False`` (§2.3: a ramp without a usable detector takes its
own segment's mainline difference, as :mod:`calibration.ramp_estimation`
does; nothing is carried into another segment's ramp), and writes:

* ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml`` — the source with its
  ``network.inflow``, every ramp's ``inflow`` / ``exit_fraction`` and
  ``network.boundary.steps`` replaced, and its name; nothing else changes
  (the fleet, the driver choice, the reference configuration ``xlsfg``,
  ``boundary.exit_buffer_m``, ``sim``);
* ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml`` — the same with
  the 6th Street left-exit remedy of
  ``scenarios/mndot_i94_wb_stpaul_weave_dc_netfix.yaml`` (its
  ``--ramps.unset`` list); the series are checked to be the ones the fixed
  network's own chain gives;
* ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_sf.yaml`` — ``_dc_cal`` with
  ``fleet.speed_factor`` set to the value the driver check recommends on the
  calibration days (``--transfer-check``, protocol §7.2), refused unless the
  check flags the free-flow mismatch, recommends the speed factor, finds it
  inside its measured range, and read exactly the calibration days over the
  study period with this scenario's population;
* ``artifacts/demand_mndot_i94_wb_stpaul_cal.json`` — the demand record
  (``flowstate.demand/1`` plus provenance: inputs with their sha256, the
  balance rules, what was transplanted).

The network is compiled with the runner's own build
(``microsim.runner._build_network``, netconvert only, about a second; no
simulation) into a temporary directory, or ``--net-workdir``.

``--check`` re-derives everything and compares the documents (not the header
comments) with the files on disk: exit 1 on a difference or a missing file.
``--table`` prints, per ramp and hour, the committed nine-day inputs, this
calibration-day build, the build with the residuals carried (the committed
method on calibration days), and the fix-1 variant that needs a protocol
amendment (T.H.61 NB without its detector, S792 out of the balance) — the
before/after numbers of the note — and writes nothing.

Run (repository root)::

    uv run --no-sync python scripts/i94_calibration_days.py
    uv run --no-sync python scripts/i94_calibration_days.py --check
    uv run --no-sync python scripts/i94_calibration_days.py --table

Exit codes: 0 written / checked / printed; 1 ``--check`` found a difference;
2 refused (the reason on stderr).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from calibration.observations import Observations
from calibration.onboarding import calibrate_scenario
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.units import ms_to_kmh

REPO = Path(__file__).resolve().parents[1]
SOURCE = "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml"
NETFIX_SOURCE = "scenarios/mndot_i94_wb_stpaul_weave_dc_netfix.yaml"
OBSERVATIONS = "artifacts/p1_rehearsal_2026-10-04/observations_calibration.json"
DAY_SPLIT = "artifacts/p1_rehearsal_2026-10-04/day_split.json"
STATIONS_X = "data/mndot/mndot_i94_wb_stpaul/stations_x.csv"
COMMITTED_DEMAND = "artifacts/demand_mndot_i94_wb_stpaul.json"
TRANSFER_CHECK = "artifacts/i94_calibration_days_2026-10-07/transfer_lanes/transfer_check.json"
OUT_CAL = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml"
OUT_NETFIX = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_netfix.yaml"
OUT_SF = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_sf.yaml"
DEMAND_OUT = "artifacts/demand_mndot_i94_wb_stpaul_cal.json"
UPSTREAM = "S1063"
DOWNSTREAM = "S97"
STUDY_START, STUDY_END = "05:30", "09:30"

#: The balance rule of the written scenarios (module docstring).
CARRY_RESIDUALS = False

#: Fix 1's two rules that need a protocol amendment (docs/I94_CALIBRATION_DAYS.md §3):
#: printed by ``--table`` only, never written into a scenario.
AMENDMENT_SKIP = {
    "S792": "two of three loops scaled x1.5 (loop 3240 excluded); reads 234-686 veh/h "
    "below S791 with only an exit between them (docs/I94_RESIDUALS.md 2.4)"
}
AMENDMENT_IGNORE = {
    "rnd_88807": "T.H.61 NB detector reads 214-475 veh/h above the mainline gain across "
    "S1069->S1070 (docs/I94_RESIDUALS.md 2.4)"
}

#: Ramps the note's table follows, in corridor order, with plain names.
TABLE_RAMPS = (
    ("off-ramp 18279036", "T.H.120 exit (rnd_88835, dead)"),
    ("off-ramp 18207390", "Hudson Rd exit (rnd_88833)"),
    ("on-ramp 18207436", "Hudson Rd entrance, scripted (rnd_88831)"),
    ("on-ramp 18207653", "second Hudson Rd entrance"),
    ("on-ramp 178547099", "McKnight Rd entrance, scripted"),
    ("on-ramp 745524613", "Ruth St entrance, weave (rnd_88819)"),
    ("C-D split 18208090", "White Bear C-D split (rnd_88817)"),
    ("C-D re-entry 745524608", "White Bear C-D re-entry"),
    ("off-ramp 18207880", "T.H.61 exit (rnd_88811)"),
    ("on-ramp 53062592", "T.H.61 NB entrance (rnd_88807)"),
    ("off-ramp 18207912", "Mounds Blvd exit"),
    ("off-ramp 42165869", "6th St left exit (rnd_87209, no data)"),
    ("on-ramp 40648744", "Mounds/Kittson entrance (loop 3244)"),
    ("on-ramp 769818012", "T.H.52 entrance, weave (rnd_91040)"),
    ("off-ramp 18207598", "T.H.52 weave exit"),
    ("off-ramp 82150350", "Jackson St exit (rnd_87221; 12th St in OSM)"),
)


def _path(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def sha256_of(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(_path(path).read_bytes()).hexdigest()


def split_header(text: str) -> tuple[list[str], str]:
    """``(header comment lines, YAML body)`` of a scenario file."""
    lines = text.splitlines(keepends=True)
    head = [ln.rstrip("\n") for ln in lines if ln.startswith("#")]
    body = "".join(ln for ln in lines if not ln.startswith("#"))
    return head, body


class _FlowMap(dict[str, Any]):
    """A mapping the source file writes in flow style (``{force_guard: 1.0}``)."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    _FlowMap,
    lambda dumper, data: dumper.represent_mapping(
        "tag:yaml.org,2002:map", dict(data), flow_style=True
    ),
)


def render(doc: Mapping[str, Any]) -> str:
    """The scenario document as the source files spell it.

    ``yaml.safe_dump`` (key order kept) except that a ramp's non-empty
    ``merge_params`` and a weave's non-empty ``weave_params`` are written in
    flow style, as the reference configuration's recipe wrote them;
    :func:`build` refuses a source this does not reproduce byte for byte.
    """
    out = copy.deepcopy(dict(doc))
    for ramp in out.get("network", {}).get("ramps", []):
        if ramp.get("merge_params"):
            ramp["merge_params"] = _FlowMap(ramp["merge_params"])
        weave = ramp.get("weave")
        if isinstance(weave, dict) and weave.get("weave_params"):
            weave["weave_params"] = _FlowMap(weave["weave_params"])
    return yaml.dump(out, Dumper=_Dumper, sort_keys=False)


def scenario_hash(doc: Mapping[str, Any]) -> str:
    """``flowstate_core.config.config_hash`` of a scenario document."""
    return config_hash(ScenarioConfig.model_validate(copy.deepcopy(dict(doc))))


def read_stations_x(path: str | Path) -> dict[str, float]:
    """Station id -> chain position [m] (``scripts/corridor_demand.py``'s reader)."""
    sys.path.insert(0, str(REPO / "scripts"))
    from corridor_demand import read_stations_x as _read

    return _read(_path(path))


def compile_net(doc: Mapping[str, Any], workdir: Path) -> Path:
    """Compile the scenario's network as the runner does (netconvert only)."""
    from microsim.runner import _build_network

    cfg = ScenarioConfig.model_validate(copy.deepcopy(dict(doc)))
    return Path(_build_network(cfg, workdir).net_path)


def net_chain_m(net_path: Path) -> float:
    """Length of the source's corridor chain on a compiled network [m]."""
    from calibration.onboarding import chain_edge_x

    doc = yaml.safe_load(split_header(_path(SOURCE).read_text())[1])
    edges = chain_edge_x(net_path, [str(e) for e in doc["network"]["corridor_edges"]])
    return max(end for _, end in edges.values())


def check_observations(obs_raw: Mapping[str, Any], split_raw: Mapping[str, Any]) -> list[str]:
    """The calibration dates, after checking the targets are the split's, quality-masked.

    Raises:
        ValueError: The artifact is not the calibration-day set of the split,
            or was built without the data-quality masking.
    """
    want = sorted(str(d).replace("-", "") for d in split_raw["calibration_dates"])
    have = sorted(str(d).replace("-", "") for d in obs_raw["source"]["dates"])
    if have != want:
        raise ValueError(f"{OBSERVATIONS} holds dates {have}, not the calibration days {want}")
    if not obs_raw["source"].get("quality"):
        raise ValueError(f"{OBSERVATIONS} was not quality-masked (source.quality is null)")
    sub = obs_raw["source"].get("subset") or {}
    if sub.get("set") != "calibration":
        raise ValueError(f"{OBSERVATIONS} is not the split's calibration set ({sub.get('set')!r})")
    return want


def derive(
    source_doc: Mapping[str, Any],
    net_path: Path,
    *,
    carry_residuals: bool = CARRY_RESIDUALS,
    skip_stations: Mapping[str, str] | None = None,
    ignore_ramp_detectors: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Run the demand method on the calibration days; returns (filled doc, demand, summary)."""
    res = calibrate_scenario(
        copy.deepcopy(dict(source_doc)),
        observations=Observations.from_json(_path(OBSERVATIONS)),
        stations_x=read_stations_x(STATIONS_X),
        net_path=net_path,
        upstream=UPSTREAM,
        downstream=DOWNSTREAM,
        idm_calibration=None,
        warmup_s=float(source_doc["sim"]["warmup_s"]),
        carry_residuals=carry_residuals,
        skip_stations=skip_stations,
        ignore_ramp_detectors=ignore_ramp_detectors,
    )
    return res.scenario, res.demand, res.summary


def transplant(
    source_doc: Mapping[str, Any], filled: Mapping[str, Any], name: str
) -> dict[str, Any]:
    """The source with only the observation-derived series (and the name) taken from ``filled``.

    Raises:
        ValueError: The ramps of the two documents do not line up.
    """
    out = copy.deepcopy(dict(source_doc))
    out["name"] = name
    net, new = out["network"], filled["network"]
    net["inflow"] = copy.deepcopy(new["inflow"])
    if len(net["ramps"]) != len(new["ramps"]):
        raise ValueError("ramp lists differ in length")
    for ramp, fresh in zip(net["ramps"], new["ramps"], strict=True):
        if (ramp["name"], ramp["kind"]) != (fresh["name"], fresh["kind"]):
            raise ValueError(f"ramp {ramp['name']!r} does not line up with {fresh['name']!r}")
        key = "inflow" if ramp["kind"] == "on" else "exit_fraction"
        ramp[key] = copy.deepcopy(fresh[key])
    net["boundary"]["steps"] = copy.deepcopy(new["boundary"]["steps"])
    return out


def series_paths(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Every transplanted series of a document, keyed by a readable path."""
    net = doc["network"]
    out: dict[str, Any] = {"network.inflow": net["inflow"]}
    for ramp in net["ramps"]:
        key = "inflow" if ramp["kind"] == "on" else "exit_fraction"
        out[f"ramp {ramp['name']}.{key}"] = ramp[key]
    out["network.boundary.steps"] = net["boundary"]["steps"]
    return out


def unset_list(doc: Mapping[str, Any]) -> str:
    """The value following ``--ramps.unset`` in ``network.netconvert_extra``."""
    extra = [str(x) for x in doc["network"]["netconvert_extra"]]
    return extra[extra.index("--ramps.unset") + 1]


def netfix_doc(
    cal: Mapping[str, Any], source: Mapping[str, Any], netfix: Mapping[str, Any]
) -> dict[str, Any]:
    """``_dc_cal`` with the netfix scenario's ``--ramps.unset`` list.

    Raises:
        ValueError: The committed netfix scenario differs from its source in
            anything but the name and that list.
    """
    a, b = copy.deepcopy(dict(source)), copy.deepcopy(dict(netfix))
    for d in (a, b):
        d.pop("name")
        extra = d["network"]["netconvert_extra"]
        extra[extra.index("--ramps.unset") + 1] = "<unset>"
    if a != b:
        raise ValueError(f"{NETFIX_SOURCE} differs from {SOURCE} beyond its name and --ramps.unset")
    out = copy.deepcopy(dict(cal))
    out["name"] = f"{cal['name']}_netfix"
    extra = out["network"]["netconvert_extra"]
    extra[extra.index("--ramps.unset") + 1] = unset_list(netfix)
    return out


@dataclass(frozen=True)
class SpeedFactor:
    """The driver check's speed-factor recommendation, checked."""

    value: float
    needed: float
    low: float
    high: float
    observed_ms: float
    observed_lo_ms: float
    observed_hi_ms: float
    model_ms: float
    path: str
    sha256: str


def speed_factor_from(path: str | Path, dates: list[str], source: str) -> SpeedFactor:
    """Read and check the driver check's free-flow recommendation (module docstring).

    Raises:
        ValueError: Any of the refusals of the module docstring.
    """
    raw = json.loads(_path(path).read_text())
    if raw.get("schema") != "flowstate.transfer_check/1":
        raise ValueError(f"{path}: not a flowstate.transfer_check/1 report")
    prov = raw["provenance"]
    got = sorted(str(d).replace("-", "") for d in prov["inputs"].get("dates") or [])
    if got != sorted(dates):
        raise ValueError(f"{path}: read dates {got}, not the calibration days {sorted(dates)}")
    argv = list(prov.get("argv") or [])

    def opt(flag: str) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else None

    if (opt("--start"), opt("--end")) != (STUDY_START, STUDY_END):
        raise ValueError(f"{path}: not read over the study period {STUDY_START}-{STUDY_END}")
    if opt("--scenario") != source:
        raise ValueError(f"{path}: checked population {opt('--scenario')!r}, not {source}")
    comp = next(c for c in raw["comparisons"] if c["quantity"] == "free_flow_speed")
    if comp["verdict"] != "mismatch":
        raise ValueError(f"{path}: free-flow verdict {comp['verdict']!r}, not a mismatch")
    rec = next(r for r in raw["recommendations"] if r["quantity"] == "free_flow_speed")
    if rec["action"] != "adjust" or rec["chosen"] != "speed_factor":
        raise ValueError(
            f"{path}: recommends {rec['action']} / {rec['chosen']}, not the speed factor"
        )
    knob = next(k for k in rec["knobs"] if k["name"] == "speed_factor")
    low, high = (float(x) for x in knob["range"])
    needed = float(knob["needed"])
    if not (knob["available"] and knob["fits"] and low <= needed <= high):
        raise ValueError(f"{path}: speed factor {needed} outside its measured range {low}-{high}")
    interval = comp["observed_interval"]
    return SpeedFactor(
        value=round(needed, 4),
        needed=needed,
        low=low,
        high=high,
        observed_ms=float(comp["observed"]),
        observed_lo_ms=float(interval["lo"]),
        observed_hi_ms=float(interval["hi"]),
        model_ms=float(comp["model"]),
        path=str(path),
        sha256=sha256_of(path),
    )


# --- headers ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """The numbers the header states, computed from the inputs (veh/h).

    Ranges are over the three scored hours (06:30, 07:30, 08:30) and, where
    said, the 05:30-09:30 mean.
    """

    th61_residual: float
    plan_excess: tuple[float, float]
    s792_gap: tuple[float, float]
    exits_total: tuple[float, float]
    sixth_st: tuple[float, float]


def _span(vals: list[float]) -> tuple[float, float]:
    return (min(vals), max(vals))


def facts_of(demand: Mapping[str, Any]) -> Facts:
    """:class:`Facts` of a calibration-day demand record."""
    obs = Observations.from_json(_path(OBSERVATIONS))
    mainline = {s.id: float(s.x_m or 0.0) for s in obs.stations if s.kind == "mainline"}
    ramps, stations = plan_volumes(demand, mainline)

    def obs_h(sid: str) -> list[float]:
        return hours(np.array([np.nan if v is None else v for v in obs.flows_veh_h[sid]], float))

    excess = [
        p - o
        for sid in ("S1070", "S1948", "S792")
        for p, o in zip(hours(stations[sid])[:3], obs_h(sid)[:3], strict=True)
    ]
    gap = [b - a for a, b in zip(obs_h("S792"), obs_h("S791"), strict=True)]
    total = [a - b for a, b in zip(obs_h("S1948")[:3], obs_h("S791")[:3], strict=True)]
    res = next(
        r["mean_residual_veh_h"]
        for r in demand["bracket_residuals"]
        if (r["from"], r["to"]) == ("S1069", "S1070")
    )
    return Facts(
        th61_residual=float(res),
        plan_excess=_span(excess),
        s792_gap=_span(gap),
        exits_total=_span(total),
        sixth_st=_span(hours(ramps["off-ramp 42165869"])[:3]),
    )


def _r(span: tuple[float, float]) -> str:
    return f"{span[0]:,.0f}-{span[1]:,.0f}"


def _common_header(name: str, date: str, demand_sha: str, facts: Facts) -> list[str]:
    src_hash = scenario_hash(yaml.safe_load(split_header(_path(SOURCE).read_text())[1]))
    return [
        f"# {name}: the calibrated 4-h weave scenario (Amendment-1 drivers, reference configuration",
        "#   xlsfg) with its observation-derived inputs rebuilt on the five CALIBRATION days only.",
        f"# Written {date} by scripts/i94_calibration_days.py; do not edit by hand.",
        f"# Source: {SOURCE} (sha256 {sha256_of(SOURCE)}, config hash {src_hash}, policy v3);",
        "#   its header (driver choice k = 1, keep-right 0.1; population; xlsfg recipe) applies unchanged.",
        "#   Changed, nothing else: name; network.inflow; every ramp's inflow / exit_fraction series;",
        "#   network.boundary.steps (S97's speed schedule). boundary.exit_buffer_m, fleet, sim, merges and",
        "#   weaves are the source's.",
        "# Why: the source's series came from data/mndot/mndot_i94_wb_stpaul/observations.json, the mean of",
        "#   all nine fetched weekdays, four of them the protocol's validation days (09-01, 09-09, 09-10,",
        "#   09-17); docs/FRISCO_PROTOCOL.md sections 3.5 and 7 calibrate on calibration days only",
        "#   (docs/I94_CALIBRATION_DAYS.md).",
        f"# Targets: {OBSERVATIONS} (sha256 {sha256_of(OBSERVATIONS)}):",
        f"#   2026-09-02, 09-03, 09-08, 09-15, 09-16 of {DAY_SPLIT} (seed 20261004), quality-masked.",
        "# Method: calibration.onboarding.calibrate_scenario (the corridor's own demand method: entry inflow",
        "#   = S1063, ramps close each station bracket, live ramp detectors used) with carry_residuals=False",
        "#   (protocol section 2.3: a ramp without a usable detector takes its own segment's mainline",
        "#   difference, as calibration.ramp_estimation does; nothing is carried into another segment).",
        f"#   Record: {DEMAND_OUT} (sha256 {demand_sha}).",
        "# NOT applied (each needs a protocol amendment; docs/I94_CALIBRATION_DAYS.md section 3):",
        "#   T.H.61 NB entrance 53062592 from the mainline difference instead of rnd_88807. Its bracket",
        "#   closes inside calibration.data_quality's band on 4 of 5 calibration days, so section 2.3",
        f"#   keeps the detector; its excess over the mainline gain ({facts.th61_residual:+,.0f} veh/h, "
        "4-h mean) stays",
        f"#   on the mainline: the plan runs {_r(facts.plan_excess)} veh/h above the counts at "
        "S1070-S792 (scored hours).",
        "#   S792 out of the demand balance (judged ok on every calibration day).",
        "# UNCERTAIN INPUTS (protocol sections 2.3 and 8.5; the sensitivity runs are not made yet):",
        "#   Mounds Blvd exit 18207912 = S1948 - S792 by window: S792 counts two of its three lanes x1.5",
        f"#   (loop 3240 excluded) and reads {_r(facts.s792_gap)} veh/h below S791 with only an exit "
        "between",
        "#   them, so this exit may be overstated by up to that much;",
        "#   6th St left exit 42165869 = max(S792 - S791, 0), small "
        f"({_r(facts.sixth_st)} veh/h in the scored hours):",
        "#   its detector rnd_87209 delivers no data while OSM and IRIS show a real exit. The two exits'",
        "#   total and split are not identified by these counts "
        f"(S1948 - S791 = {_r(facts.exits_total)} veh/h by hour).",
        "# Status: a cloud battery input (scripts/gcp/pipeline_i24.sh stage p8_i94_cal); not run locally",
        "#   (the laptop rule). Nothing is calibrated or validated on it yet. seeded=False.",
    ]


def header_cal(date: str, doc: Mapping[str, Any], demand_sha: str, facts: Facts) -> list[str]:
    """The ``_dc_cal`` provenance block."""
    h = scenario_hash(doc)
    return [
        *_common_header(str(doc["name"]), date, demand_sha, facts),
        f"# config hash {h} (policy v3).",
    ]


def header_netfix(
    date: str, doc: Mapping[str, Any], cal: Mapping[str, Any], demand_sha: str, facts: Facts
) -> list[str]:
    """The ``_dc_cal_netfix`` provenance block."""
    h = scenario_hash(doc)
    return [
        *_common_header(str(doc["name"]), date, demand_sha, facts),
        f"# Plus the 6th Street left-exit remedy of {NETFIX_SOURCE} (sha256 {sha256_of(NETFIX_SOURCE)}):",
        f"#   network.netconvert_extra --ramps.unset {unset_list(doc)} (docs/I94_LANE_SHARES.md sections 4",
        "#   and 6; a map correction backed by OSM and IRIS, protocol section 7.3). The series are those of",
        f"#   {OUT_CAL} (config hash {scenario_hash(cal)}): the fixed network's own chain gives the same.",
        "#   Probe: artifacts/i94_netfix_probe.json (stage p5, 4 seeds of the slice).",
        f"# config hash {h} (policy v3).",
    ]


def header_sf(
    date: str,
    doc: Mapping[str, Any],
    cal: Mapping[str, Any],
    sf: SpeedFactor,
    demand_sha: str,
    facts: Facts,
) -> list[str]:
    """The ``_dc_cal_sf`` provenance block."""
    h = scenario_hash(doc)
    return [
        *_common_header(str(doc["name"]), date, demand_sha, facts),
        f"# Plus fleet.speed_factor {sf.value:g} (passenger vehicles; heavy vehicles keep 1.0), protocol",
        "#   section 7.2: the driver check re-run on the calibration days over the study period",
        f"#   {STUDY_START}-{STUDY_END} ({sf.path}, sha256 {sf.sha256}):",
        f"#   light-traffic free-flow speed {ms_to_kmh(sf.observed_ms):.1f} km/h observed (95 % "
        f"interval {ms_to_kmh(sf.observed_lo_ms):.1f}-{ms_to_kmh(sf.observed_hi_ms):.1f}) against",
        f"#   {ms_to_kmh(sf.model_ms):.1f} km/h for the model at factor 1; needed {sf.needed:g}, inside "
        f"the measured range {sf.low:g}-{sf.high:g}.",
        "#   The weave and scripted merges still take a vehicle's desired speed at factor 1 (WP-109; each",
        "#   run's meta.json says so). The boundary schedule is posted divided by the factor (engine).",
        f"#   Otherwise {OUT_CAL} (config hash {scenario_hash(cal)}).",
        f"# config hash {h} (policy v3).",
    ]


# --- the before/after table ---------------------------------------------------------------------


def plan_volumes(
    demand: Mapping[str, Any], station_x: Mapping[str, float]
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Free-flow propagation of a demand record: per-ramp and per-station veh/h per window.

    On-ramps add their inflow; an exit takes its fraction of the flow that
    arrives in the plan (not of the observed count), so a residual carried
    or left on the mainline shows up downstream as it would in a run
    without congestion.
    """
    q = np.array([v * 3600.0 for _, v in demand["inflow_steps"]], dtype=float)
    events: list[tuple[float, int, str, Any]] = [(x, 0, sid, None) for sid, x in station_x.items()]
    events += [(float(r["x_m"]), 1, str(r["name"]), r) for r in demand["ramps"]]
    ramps: dict[str, np.ndarray] = {}
    stations: dict[str, np.ndarray] = {}
    for _, _, key, ramp in sorted(events, key=lambda e: (e[0], e[1])):
        if ramp is None:
            stations[key] = q.copy()
        elif ramp["kind"] == "on":
            v = np.array([s * 3600.0 for _, s in ramp["inflow_steps"]], dtype=float)
            ramps[key], q = v, q + v
        else:
            f = np.array([s for _, s in ramp["exit_fraction_steps"]], dtype=float)
            ramps[key], q = f * q, q - f * q
    return ramps, stations


def hours(series: np.ndarray) -> list[float]:
    """Means over 06:30, 07:30 and 08:30 hours (windows from 05:30) and over 05:30-09:30."""
    a = np.asarray(series, dtype=float)
    return [float(np.nanmean(a[i : i + 12])) for i in (12, 24, 36)] + [float(np.nanmean(a))]


def print_table(source_doc: Mapping[str, Any], net_path: Path) -> None:
    """The note's before/after table (module docstring)."""
    obs = Observations.from_json(_path(OBSERVATIONS))
    mainline = {s.id: float(s.x_m or 0.0) for s in obs.stations if s.kind == "mainline"}
    builds = {
        "committed (9 days)": json.loads(_path(COMMITTED_DEMAND).read_text()),
        "cal, carried": derive(source_doc, net_path, carry_residuals=True)[1],
        "cal, written": derive(source_doc, net_path)[1],
        "cal, fix-1 (amendment)": derive(
            source_doc,
            net_path,
            skip_stations=AMENDMENT_SKIP,
            ignore_ramp_detectors=AMENDMENT_IGNORE,
        )[1],
    }
    plans = {k: plan_volumes(v, mainline) for k, v in builds.items()}

    def obs_h(sid: str) -> list[float]:
        return hours(np.array([np.nan if v is None else v for v in obs.flows_veh_h[sid]], float))

    def f(vals: list[float]) -> str:
        return " / ".join(f"{v:,.0f}" for v in vals)

    print(
        "veh/h; hours from 06:30 / 07:30 / 08:30 / mean 05:30-09:30; exits in the plan's free flow"
    )
    for key, label in TABLE_RAMPS:
        print(f"\n{label}  [{key}]")
        for name, (ramps, _) in plans.items():
            method = next(r["method"] for r in builds[name]["ramps"] if r["name"] == key)
            print(f"  {name:24s} {f(hours(ramps[key])):32s} {method}")
    print("\nobserved (calibration days)")
    for a, b in (
        ("S1069", "S1070"),
        ("S1948", "S792"),
        ("S792", "S791"),
        ("S1948", "S791"),
        ("S791", "S790"),
    ):
        print(
            f"  {a}->{b} change          {f([y - x for x, y in zip(obs_h(a), obs_h(b), strict=True)])}"
        )
    print(f"  rnd_88807 (T.H.61 NB)       {f(obs_h('rnd_88807'))}")
    print("\nplanned station flow minus the calibration-day count")
    for sid in ("S1069", "S1070", "S1948", "S792", "S791", "S790", "S97"):
        print(f"  {sid:6s} count {f(obs_h(sid))}")
        for name, (_, st) in plans.items():
            print(
                f"     {name:24s} {f([p - o for p, o in zip(hours(st[sid]), obs_h(sid), strict=True)])}"
            )
    for name, d in builds.items():
        res = [(r["from"], r["to"], r["mean_residual_veh_h"]) for r in d["bracket_residuals"]]
        print(f"\nresiduals, {name}: {res}")


# --- main -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    """Everything the script writes, as text."""

    files: dict[str, str]
    docs: dict[str, dict[str, Any]]
    summary: list[str]


def build(date: str, workdir: Path, transfer_check: str) -> Built:
    """Derive the three scenarios and the demand record (module docstring)."""
    body = split_header(_path(SOURCE).read_text())[1]
    source_doc = yaml.safe_load(body)
    if render(source_doc) != body:
        raise ValueError(
            f"{SOURCE}: the writer does not reproduce its text; refusing to transplant"
        )
    obs_raw = json.loads(_path(OBSERVATIONS).read_text())
    dates = check_observations(obs_raw, json.loads(_path(DAY_SPLIT).read_text()))
    net = compile_net(source_doc, workdir / "dc")
    filled, demand, summary = derive(source_doc, net)
    cal = transplant(source_doc, filled, f"{source_doc['name']}_cal")

    netfix_raw = yaml.safe_load(split_header(_path(NETFIX_SOURCE).read_text())[1])
    nf = netfix_doc(cal, source_doc, netfix_raw)
    nf_filled = derive(nf, compile_net(nf, workdir / "netfix"))[0]
    if series_paths(transplant(source_doc, nf_filled, "x")) != series_paths(cal):
        raise ValueError(
            "the fixed network's chain gives other series; the netfix file needs its own"
        )

    sf = speed_factor_from(transfer_check, dates, SOURCE)
    sfd = copy.deepcopy(cal)
    sfd["name"] = f"{cal['name']}_sf"
    sfd["fleet"]["speed_factor"] = sf.value

    demand = dict(demand)
    demand["scenario"] = OUT_CAL
    demand["observations"] = OBSERVATIONS
    # the hash of the file written (the method's own filled scenario recomputes
    # boundary.exit_buffer_m from today's chain, which the transplant does not take)
    demand["config_hash"] = scenario_hash(cal)
    demand["provenance"] = {
        "script": "scripts/i94_calibration_days.py",
        "note": "docs/I94_CALIBRATION_DAYS.md",
        "observations_sha256": sha256_of(OBSERVATIONS),
        "day_split": DAY_SPLIT,
        "day_split_sha256": sha256_of(DAY_SPLIT),
        "calibration_dates": dates,
        "stations_x": STATIONS_X,
        "stations_x_sha256": sha256_of(STATIONS_X),
        "source_scenario": SOURCE,
        "source_scenario_sha256": sha256_of(SOURCE),
        "replaces": COMMITTED_DEMAND,
        "replaces_sha256": sha256_of(COMMITTED_DEMAND),
        "replaces_observations": "data/mndot/mndot_i94_wb_stpaul/observations.json (nine days)",
        "transplanted": [
            "network.inflow",
            "ramps[*].inflow / exit_fraction",
            "network.boundary.steps",
        ],
        "kept_from_source": "everything else, including network.boundary.exit_buffer_m",
        "net": "microsim.runner._build_network of the source's network block (netconvert only)",
        "x_m_note": (
            "ramp x_m are on today's compile of the source network (chain "
            f"{net_chain_m(net):.1f} m); the replaced nine-day record's came from an older "
            "compile (chain 11,422 m). Every ramp sits in the same station bracket in both, so "
            "the bracket balance, and with it every series, does not depend on the difference"
        ),
        "amendment_rules_not_applied": {
            "skip_stations": AMENDMENT_SKIP,
            "ignore_ramp_detectors": AMENDMENT_IGNORE,
        },
    }
    demand_text = json.dumps(demand, indent=1) + "\n"
    demand_sha = hashlib.sha256(demand_text.encode()).hexdigest()
    facts = facts_of(demand)

    files = {
        OUT_CAL: "\n".join(header_cal(date, cal, demand_sha, facts)) + "\n" + render(cal),
        OUT_NETFIX: "\n".join(header_netfix(date, nf, cal, demand_sha, facts)) + "\n" + render(nf),
        OUT_SF: "\n".join(header_sf(date, sfd, cal, sf, demand_sha, facts)) + "\n" + render(sfd),
        DEMAND_OUT: demand_text,
    }
    return Built(files=files, docs={OUT_CAL: cal, OUT_NETFIX: nf, OUT_SF: sfd}, summary=summary)


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--check", action="store_true", help="compare with the files on disk; write nothing"
    )
    p.add_argument(
        "--table", action="store_true", help="print the before/after table; write nothing"
    )
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.add_argument(
        "--transfer-check", default=TRANSFER_CHECK, help="the calibration-day driver check"
    )
    p.add_argument("--net-workdir", type=Path, help="where netconvert writes (default: a temp dir)")
    p.add_argument("--date", help="header date (default: today, UTC)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Write, check or print; returns the exit status."""
    args = build_parser().parse_args(argv)
    date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory(prefix="i94_cal_net_") as tmp:
        workdir = args.net_workdir or Path(tmp)
        try:
            if args.table:
                source_doc = yaml.safe_load(split_header(_path(SOURCE).read_text())[1])
                print_table(source_doc, compile_net(source_doc, workdir / "dc"))
                return 0
            built = build(date, workdir, args.transfer_check)
        except (ValueError, FileNotFoundError, KeyError, StopIteration) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    if args.check:
        bad = 0
        for rel, text in built.files.items():
            path = _path(rel)
            if not path.is_file():
                print(f"check: {rel} is missing", file=sys.stderr)
                bad += 1
                continue
            have = path.read_text()
            if rel.endswith(".json"):
                same = json.loads(have) == json.loads(text)
            else:
                same = yaml.safe_load(split_header(have)[1]) == built.docs[rel]
            if not same:
                print(f"check: {rel} differs from what the recipe gives", file=sys.stderr)
                bad += 1
            else:
                print(f"check: {rel} matches")
        return 1 if bad else 0
    existing = [rel for rel in built.files if _path(rel).exists()]
    if existing and not args.force:
        print(
            f"refused: {', '.join(existing)} exist (--check compares, --force overwrites)",
            file=sys.stderr,
        )
        return 2
    for rel, text in built.files.items():
        path = _path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(text)
        part.replace(path)
        tag = ""
        if rel in built.docs:
            tag = f"  {built.docs[rel]['name']}  config hash {scenario_hash(built.docs[rel])}"
        print(f"-> {rel}{tag}")
    print("the demand method's record (its filled scenario, not the files written):")
    for line in built.summary:
        print(line)
    print(
        "the files written keep the source's boundary.exit_buffer_m and everything else but the "
        "series and the name (module docstring)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
