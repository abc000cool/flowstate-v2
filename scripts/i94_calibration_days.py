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

D10 (docs/PRE_FRISCO_PROGRAM.md; 2026-10-07, before any run of it): fix 1's
rules (b) and (c), whose wording (docs/I94_CALIBRATION_DAYS.md §3, drafts 1
and 2) was adopted as amendment text before the run, written as two arms of
Phase A's I-94 base ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml``
(``_dc_cal`` with W1b + W2 set explicitly on both weaves, stage p10):

* ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rb.yaml`` — rule (b):
  the T.H.61 NB entrance takes its segment's mainline difference
  (``ignore_ramp_detectors`` rnd_88807);
* ``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml`` — rules (b)
  and (c): S792 also out of the demand balance (``skip_stations``), still
  scored;
* ``artifacts/demand_mndot_i94_wb_stpaul_cal_{rb,rbc}.json`` — their demand
  records, with the rule's evidence and draft 1's screen of every segment.

Each arm is the base with the same method's series under its rule. The build
refuses unless the method without the rule reproduces every one of the base's
series exactly, and the arm differs from the base in its name and in exactly
the series its rule changes (``D10Arm.changes``), nothing else; the base is
written wide (``D10_WIDTH``) so its text round-trips byte for byte.

The network is compiled with the runner's own build
(``microsim.runner._build_network``, netconvert only, about a second; no
simulation) into a temporary directory, or ``--net-workdir``.

``--check`` re-derives everything and compares the documents (not the header
comments) with the files on disk: exit 1 on a difference or a missing file.
Every config hash a header or a demand record states is checked under the
policy it names (docs/CONTRACTS.md section 2; :func:`header_hash_problems`,
:func:`record_hash_problems`), so a file written under policy v3 still checks
under policy v4 without being rewritten; the writer labels every hash it
states with today's policy (:data:`POLICY`).
``--only cal`` / ``--only d10`` limits a write or a check to one set.
``--table`` prints, per ramp and hour, the committed nine-day inputs, this
calibration-day build, the build with the residuals carried (the committed
method on calibration days), and the fix-1 variant that needs a protocol
amendment (T.H.61 NB without its detector, S792 out of the balance) — the
before/after numbers of the note — and writes nothing.

Run (repository root)::

    uv run --no-sync python scripts/i94_calibration_days.py
    uv run --no-sync python scripts/i94_calibration_days.py --check
    uv run --no-sync python scripts/i94_calibration_days.py --table
    uv run --no-sync python scripts/i94_calibration_days.py --only d10 [--check]

Exit codes: 0 written / checked / printed; 1 ``--check`` found a difference;
2 refused (the reason on stderr).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from calibration.observations import Observations
from calibration.onboarding import calibrate_scenario
from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    ScenarioConfig,
    config_hash,
    config_hash_v2,
    config_hash_v3,
)
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

#: Fix 1's two rules that needed a protocol amendment (docs/I94_CALIBRATION_DAYS.md §3):
#: printed by ``--table``, never written into the ``_dc_cal`` family. D10 writes them into
#: its own two arms (``D10_ARMS``), with reasons that cite the adopted drafts.
AMENDMENT_SKIP = {
    "S792": "two of three loops scaled x1.5 (loop 3240 excluded); reads 234-686 veh/h "
    "below S791 with only an exit between them (docs/I94_RESIDUALS.md 2.4)"
}
AMENDMENT_IGNORE = {
    "rnd_88807": "T.H.61 NB detector reads 214-475 veh/h above the mainline gain across "
    "S1069->S1070 (docs/I94_RESIDUALS.md 2.4)"
}

# --- D10 (docs/PRE_FRISCO_PROGRAM.md): rules (b) and (c) as pre-registered rounds ----------------

#: Phase A's I-94 base: ``_dc_cal`` with W1b + W2 on both weaves (stage p10, arm B).
D10_BASE = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml"
D10_BASE_BATTERY = "artifacts/validation_mndot_i94_wb_stpaul_weave_xlsfg_dc_cal_w1b_w2.json"
D10_STAGE = "artifacts/i94_d10_2026-10-07/stage_p16_d10.sh.txt"
DATA_QUALITY = "artifacts/p1_rehearsal_2026-10-04/dq/data_quality.json"
OUT_RB = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rb.yaml"
OUT_RBC = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml"
DEMAND_RB = "artifacts/demand_mndot_i94_wb_stpaul_cal_rb.json"
DEMAND_RBC = "artifacts/demand_mndot_i94_wb_stpaul_cal_rbc.json"
#: Line width of the D10 writer: stage p10 wrote the base's weave_params on one line
#: (167 characters), which the default width of 80 would fold.
D10_WIDTH = 4096
#: W1b + W2 (FRISCO_PROTOCOL Amendment 4), set explicitly on both weaves of the base and
#: of each arm, so a later default in code cannot change what these files run.
D10_WEAVE_PARAMS = {
    "exit_prepare": 1.0,
    "entrant_giveup_m": 5.0,
    "entrant_giveup_dwell_s": 60.0,
    "weave_handback": 1.0,
    "weave_close_leader": 1.0,
    "weave_resolve_opposing": 1.0,
}
#: The segment rule (b) applies to, and the station rule (c) leaves out of the balance.
TH61 = {"up": "S1069", "down": "S1070", "detector": "rnd_88807", "ramp": "on-ramp 53062592"}
S792_PAIR = {"station": "S792", "downstream": "S791", "upstream": "S1948"}
#: docs/I94_CALIBRATION_DAYS.md §3, "What an amendment would say", drafts 1 and 2 verbatim;
#: adopted as amendment text on 2026-10-07 before the run (PRE_FRISCO_PROGRAM.md, coordinator's
#: decisions).
DRAFT_1 = (
    "A ramp detector whose segment residual has the same sign on every calibration day and "
    "leaves the quadrature count-error band on the calibration-day mean is not used; its ramp "
    "takes the mainline difference."
)
DRAFT_2 = (
    "S792 is left out of the I-94 demand balance (two of three loops; reads below S791 across "
    "an exit); it stays a scored station."
)


@dataclass(frozen=True)
class D10Arm:
    """One D10 arm: its rules and the series those rules change (and must change)."""

    suffix: str
    out: str
    demand_out: str
    rules: tuple[str, ...]
    changes: tuple[str, ...]


D10_ARMS = (
    D10Arm("rb", OUT_RB, DEMAND_RB, ("b",), ("ramp on-ramp 53062592.inflow",)),
    D10Arm(
        "rbc",
        OUT_RBC,
        DEMAND_RBC,
        ("b", "c"),
        (
            "ramp on-ramp 53062592.inflow",
            "ramp off-ramp 18207912.exit_fraction",
            "ramp off-ramp 42165869.exit_fraction",
        ),
    ),
)

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


def render(doc: Mapping[str, Any], width: int | None = None) -> str:
    """The scenario document as the source files spell it.

    ``yaml.safe_dump`` (key order kept) except that a ramp's non-empty
    ``merge_params`` and a weave's non-empty ``weave_params`` are written in
    flow style, as the reference configuration's recipe wrote them;
    :func:`build_cal` refuses a source this does not reproduce byte for byte.
    ``width`` None is PyYAML's default line width (the ``_dc_cal`` family);
    :func:`build_d10` passes :data:`D10_WIDTH` and refuses its base likewise.
    """
    out = copy.deepcopy(dict(doc))
    for ramp in out.get("network", {}).get("ramps", []):
        if ramp.get("merge_params"):
            ramp["merge_params"] = _FlowMap(ramp["merge_params"])
        weave = ramp.get("weave")
        if isinstance(weave, dict) and weave.get("weave_params"):
            weave["weave_params"] = _FlowMap(weave["weave_params"])
    if width is None:
        return yaml.dump(out, Dumper=_Dumper, sort_keys=False)
    return yaml.dump(out, Dumper=_Dumper, sort_keys=False, width=width)


def scenario_hash(doc: Mapping[str, Any]) -> str:
    """``flowstate_core.config.config_hash`` of a scenario document."""
    return config_hash(ScenarioConfig.model_validate(copy.deepcopy(dict(doc))))


# --- config-hash policies (docs/CONTRACTS.md section 2) -------------------------------------------

#: The label every hash this script writes carries: the policy today's code hashes under.
POLICY = f"policy v{CONFIG_HASH_VERSION}"
#: The policies a stated hash is checked under, newest first: today's, and the two older ones
#: ``flowstate_core.config`` reproduces (``config_hash_v3``: records of 2026-10-04 to 2026-10-07;
#: ``config_hash_v2``: 2026-09-06 to 2026-10-03). Older hashes are checked, never written.
POLICIES = (CONFIG_HASH_VERSION, 3, 2)


def hash_under_policy(doc: Mapping[str, Any], version: int) -> str:
    """A scenario document's config hash under policy ``version`` (one of :data:`POLICIES`).

    Raises:
        ValueError: ``version`` is not a policy this code reproduces, or the
            document does not validate as that policy read it.
    """
    if version == CONFIG_HASH_VERSION:
        return scenario_hash(doc)
    if version == 3:
        return config_hash_v3(copy.deepcopy(dict(doc)))
    if version == 2:
        return config_hash_v2(copy.deepcopy(dict(doc)))
    raise ValueError(f"config-hash policy v{version} is not one this code reproduces {POLICIES}")


def recorded_policy(recorded: str, doc: Mapping[str, Any]) -> int | None:
    """The policy under which ``recorded`` is ``doc``'s config hash (newest first), or None."""
    for version in POLICIES:
        try:
            if hash_under_policy(doc, version) == recorded:
                return version
        except ValueError:  # a document that policy could not read
            continue
    return None


#: A written scenario's last header line: its own hash and the policy it was written under.
_OWN_HASH = re.compile(r"# config hash (?P<hash>[0-9a-f]{12}) \(policy v(?P<v>\d+)\)\.")
#: ``<file>.yaml (sha256 <hex>, config hash <h>, policy vN)``; the sha256 and the policy are optional
#: (the netfix and sf headers of 2026-10-07 quote ``_dc_cal``'s hash without one).
_YAML_HASH = re.compile(
    r"(?P<path>[\w./-]+\.yaml) \((?:sha256 [0-9a-f]{64}, )?config hash (?P<hash>[0-9a-f]{12})"
    r"(?:, policy v(?P<v>\d+))?\)"
)
#: ``<battery>.json (...) records config_hash <h> (policy vN)`` (the D10 headers).
_BATTERY_HASH = re.compile(
    r"(?P<path>[\w./-]+\.json) \([^)]*\) records config_hash (?P<hash>[0-9a-f]{12}) "
    r"\(policy v(?P<v>\d+)\)"
)
_ANY_HASH = re.compile(r"config[ _]hash [0-9a-f]{12}")


def _disk_doc(rel: str) -> dict[str, Any]:
    """A scenario file's document (its header comments aside)."""
    doc: dict[str, Any] = yaml.safe_load(split_header(_path(rel).read_text())[1])
    return doc


def header_policy(text: str) -> int | None:
    """The policy a written scenario's last header line names for the file's own hash, or None."""
    head = split_header(text)[0]
    m = _OWN_HASH.fullmatch(head[-1]) if head else None
    return int(m["v"]) if m else None


def header_hash_problems(text: str) -> list[str]:
    """Every config hash a written scenario's header states, checked under the policy it names.

    The last line states the file's own hash and its policy. ``<file>.yaml (... config
    hash H, policy vN)`` states another scenario's (read from disk); one without a policy
    is read under the policy of the file's own line, the two having been written together.
    ``<battery>.json (...) records config_hash H (policy vN)`` must be the battery's
    ``config_hash`` and its scenario's hash under vN. A header is never rewritten to
    relabel a hash: a committed v3 statement is true for its date (docs/CONTRACTS.md §2).
    A hash statement none of these forms reads is itself a problem, so none is skipped.
    """
    head, body = split_header(text)
    own = _OWN_HASH.fullmatch(head[-1]) if head else None
    if own is None:
        return ["the last header line does not state the file's config hash and its policy"]
    problems: list[str] = []
    v_own = int(own["v"])

    def check(what: str, stated: str, doc: Mapping[str, Any], version: int) -> None:
        try:
            want = hash_under_policy(doc, version)
        except ValueError as exc:
            problems.append(f"{what}: {stated} cannot be checked under policy v{version} ({exc})")
            return
        if stated != want:
            problems.append(f"{what}: states {stated}, policy v{version} gives {want}")

    check("the file's own hash", own["hash"], yaml.safe_load(body), v_own)
    joined = " ".join(ln.lstrip("#").strip() for ln in head)
    n_read = 1
    for m in _YAML_HASH.finditer(joined):
        n_read += 1
        rel = m["path"]
        if not _path(rel).is_file():
            problems.append(f"{rel}: stated config hash {m['hash']}, but the file is missing")
            continue
        check(rel, m["hash"], _disk_doc(rel), int(m["v"]) if m["v"] else v_own)
    for m in _BATTERY_HASH.finditer(joined):
        n_read += 1
        rel = m["path"]
        if not _path(rel).is_file():
            problems.append(f"{rel}: stated config_hash {m['hash']}, but the file is missing")
            continue
        battery = json.loads(_path(rel).read_text())
        if battery.get("config_hash") != m["hash"]:
            problems.append(f"{rel}: records {battery.get('config_hash')}, not {m['hash']}")
        check(f"{rel}'s scenario", m["hash"], _disk_doc(str(battery["scenario"])), int(m["v"]))
    n_stated = len(_ANY_HASH.findall(joined))
    if n_stated != n_read:
        problems.append(f"{n_stated} config hash statements, {n_read} read")
    return problems


def record_hash_problems(rec: Mapping[str, Any]) -> list[str]:
    """A demand record's ``config_hash`` (and ``provenance.base_config_hash``), checked.

    The record carries no policy of its own; it was written with its scenario
    (``rec["scenario"]``), whose last header line names the policy both were
    written under, so both hashes are checked under that policy.
    """
    scen = str(rec["scenario"])
    if not _path(scen).is_file():
        return [f"{scen} is missing"]
    version = header_policy(_path(scen).read_text())
    if version is None:
        return [f"{scen}: its header names no config-hash policy"]
    prov = rec.get("provenance") or {}
    stated = [("config_hash", rec.get("config_hash"), scen)]
    if "base_config_hash" in prov:
        stated.append(
            ("provenance.base_config_hash", prov["base_config_hash"], str(prov["base_scenario"]))
        )
    problems: list[str] = []
    for key, h, rel in stated:
        want = hash_under_policy(_disk_doc(rel), version)
        if h != want:
            problems.append(
                f"{key} {h} is not {rel}'s config hash under policy v{version} ({want})"
            )
    return problems


def _without_hashes(rec: Mapping[str, Any]) -> dict[str, Any]:
    """A demand record without the hashes :func:`record_hash_problems` checks (for ``--check``)."""
    out = copy.deepcopy(dict(rec))
    out.pop("config_hash", None)
    (out.get("provenance") or {}).pop("base_config_hash", None)
    return out


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
    carry_residuals: bool | None = None,
    skip_stations: Mapping[str, str] | None = None,
    ignore_ramp_detectors: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Run the demand method on the calibration days; returns (filled doc, demand, summary).

    ``carry_residuals`` None is :data:`CARRY_RESIDUALS`, read at call time.
    """
    if carry_residuals is None:
        carry_residuals = CARRY_RESIDUALS
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


def _balance_lines(carry: bool, facts: Facts) -> tuple[list[str], list[str]]:
    """The header's method lines and its T.H.61 NB residual lines under the balance rule used.

    ``carry`` False is the protocol's §2.3 reading (the committed files);
    True the pre-protocol carry rule docs/I94_CALIBRATION_DAYS.md §7 item 3
    leaves to the owner (``CARRY_RESIDUALS``).
    """
    if not carry:
        method = [
            "#   = S1063, ramps close each station bracket, live ramp detectors used) with carry_residuals=False",
            "#   (protocol section 2.3: a ramp without a usable detector takes its own segment's mainline",
            "#   difference, as calibration.ramp_estimation does; nothing is carried into another segment).",
        ]
        th61 = [
            f"#   keeps the detector; its excess over the mainline gain ({facts.th61_residual:+,.0f} veh/h, "
            "4-h mean) stays",
            f"#   on the mainline: the plan runs {_r(facts.plan_excess)} veh/h above the counts at "
            "S1070-S792 (scored hours).",
        ]
        return method, th61
    method = [
        "#   = S1063, ramps close each station bracket, live ramp detectors used) with carry_residuals=True",
        "#   (the pre-protocol carry rule, docs/I94_CALIBRATION_DAYS.md section 7 item 3: a bracket's",
        "#   unexplained change is carried into the next bracket's closing ramp).",
    ]
    th61 = [
        f"#   keeps the detector; its excess over the mainline gain ({facts.th61_residual:+,.0f} veh/h, "
        "4-h mean, the residual",
        "#   recorded for S1069-S1070) is carried into the next bracket's closing ramp: the plan minus",
        f"#   the counts at S1070-S792 is {_r(facts.plan_excess)} veh/h (scored hours).",
    ]
    return method, th61


def _common_header(
    name: str, date: str, demand_sha: str, facts: Facts, carry_residuals: bool | None = None
) -> list[str]:
    """The provenance lines every written scenario shares.

    ``carry_residuals`` is the balance rule the series were built with; None
    reads :data:`CARRY_RESIDUALS` at call time, as :func:`derive` does, so the
    header states the method actually used (review 2026-10-07, finding 6).
    """
    carry = CARRY_RESIDUALS if carry_residuals is None else carry_residuals
    method, th61 = _balance_lines(carry, facts)
    src_hash = scenario_hash(yaml.safe_load(split_header(_path(SOURCE).read_text())[1]))
    return [
        f"# {name}: the calibrated 4-h weave scenario (Amendment-1 drivers, reference configuration",
        "#   xlsfg) with its observation-derived inputs rebuilt on the five CALIBRATION days only.",
        f"# Written {date} by scripts/i94_calibration_days.py; do not edit by hand.",
        f"# Source: {SOURCE} (sha256 {sha256_of(SOURCE)}, config hash {src_hash}, {POLICY});",
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
        *method,
        f"#   Record: {DEMAND_OUT} (sha256 {demand_sha}).",
        "# NOT applied (each needs a protocol amendment; docs/I94_CALIBRATION_DAYS.md section 3):",
        "#   T.H.61 NB entrance 53062592 from the mainline difference instead of rnd_88807. Its bracket",
        "#   closes inside calibration.data_quality's band on 4 of 5 calibration days, so section 2.3",
        *th61,
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
        f"# config hash {h} ({POLICY}).",
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
        f"#   {OUT_CAL} (config hash {scenario_hash(cal)}, {POLICY}): the fixed network's own chain gives the same.",
        "#   Probe: artifacts/i94_netfix_probe.json (stage p5, 4 seeds of the slice).",
        f"# config hash {h} ({POLICY}).",
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
        f"#   Otherwise {OUT_CAL} (config hash {scenario_hash(cal)}, {POLICY}).",
        f"# config hash {h} ({POLICY}).",
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


# --- D10: rules (b) and (c) on Phase A's base -----------------------------------------------------


def _obs_flow(obs_raw: Mapping[str, Any], sid: str) -> np.ndarray:
    """A station's or detector's 5-min flows [veh/h] in the targets (None as NaN)."""
    return np.array(
        [np.nan if v is None else float(v) for v in obs_raw["flows_veh_h"][sid]], dtype=float
    )


def _dq_dates(dates: list[str]) -> list[str]:
    """``20260902`` -> ``2026-09-02`` (the data-quality artifact's spelling)."""
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]


def segment_days(
    dq_raw: Mapping[str, Any], up: str, down: str, dates: list[str]
) -> list[dict[str, Any]]:
    """The data-quality artifact's rows of segment ``up`` -> ``down`` on ``dates``, in date order."""
    rows = {
        r["date"]: r
        for r in dq_raw["mass_balance"]["segment_days"]
        if (r["upstream"], r["downstream"]) == (up, down)
    }
    return [rows[d] for d in _dq_dates(dates) if d in rows]


def segment_residual(
    obs_raw: Mapping[str, Any],
    up: str,
    down: str,
    ons: list[str],
    offs: list[str],
    count_error: float,
) -> tuple[float, float]:
    """``(mean residual, quadrature band)`` of a segment on the targets' study period [veh/h].

    The residual is ``q_down - q_up - sum(ons) + sum(offs)`` per 5-min window,
    averaged over the windows where every count is present; the band combines
    ``count_error`` times each count's own period mean in quadrature
    (independent detectors), as docs/I94_CALIBRATION_DAYS.md §3 reads draft 1.
    """
    series = {s: _obs_flow(obs_raw, s) for s in (up, down, *ons, *offs)}
    res = series[down] - series[up]
    for s in ons:
        res = res - series[s]
    for s in offs:
        res = res + series[s]
    band = count_error * float(np.sqrt(sum(float(np.nanmean(v)) ** 2 for v in series.values())))
    return float(np.nanmean(res)), band


@dataclass(frozen=True)
class D10Facts:
    """Rules (b) and (c)'s evidence on the calibration days [veh/h], as the headers state it."""

    count_error: float
    th61_up_mean: float
    th61_down_mean: float
    th61_detector_mean: float
    th61_residual: float
    th61_band: float
    th61_day_residuals: tuple[float, ...]
    th61_day_verdicts: tuple[str, ...]
    s792_gap_hours: tuple[float, ...]
    s792_gap_mean: float

    def record(self) -> dict[str, Any]:
        """The facts as the demand record carries them (rounded to 0.1 veh/h)."""
        return {
            "count_error": self.count_error,
            "th61_segment": f"{TH61['up']}->{TH61['down']}",
            "th61_detector": TH61["detector"],
            "th61_4h_means_veh_h": {
                TH61["up"]: round(self.th61_up_mean, 1),
                TH61["down"]: round(self.th61_down_mean, 1),
                TH61["detector"]: round(self.th61_detector_mean, 1),
            },
            "th61_residual_4h_mean_veh_h": round(self.th61_residual, 1),
            "th61_quadrature_band_veh_h": round(self.th61_band, 1),
            "th61_note": (
                "docs/I94_CALIBRATION_DAYS.md section 3 quotes -317 against +-261: the "
                "difference of the rounded 4-h means (1,165 - 1,482)"
            ),
            "th61_day_residuals_veh_h": list(self.th61_day_residuals),
            "th61_day_verdicts": list(self.th61_day_verdicts),
            "s792_gap_s791_minus_s792_veh_h": {
                "06:30": round(self.s792_gap_hours[0], 1),
                "07:30": round(self.s792_gap_hours[1], 1),
                "08:30": round(self.s792_gap_hours[2], 1),
                "05:30-09:30": round(self.s792_gap_mean, 1),
            },
        }


def d10_facts(obs_raw: Mapping[str, Any], dq_raw: Mapping[str, Any], dates: list[str]) -> D10Facts:
    """:class:`D10Facts` from the calibration-day targets and the data-quality artifact.

    Raises:
        ValueError: The data-quality artifact lacks T.H.61 NB's segment on a
            calibration day.
    """
    ce = float(dq_raw["parameters"]["count_error"])
    up, down, det = TH61["up"], TH61["down"], TH61["detector"]
    res, band = segment_residual(obs_raw, up, down, [det], [], ce)
    days = segment_days(dq_raw, up, down, dates)
    if len(days) != len(dates):
        raise ValueError(f"{DATA_QUALITY}: {up}->{down} is not judged on every calibration day")
    gap = _obs_flow(obs_raw, S792_PAIR["downstream"]) - _obs_flow(obs_raw, S792_PAIR["station"])
    h = hours(gap)
    return D10Facts(
        count_error=ce,
        th61_up_mean=float(np.nanmean(_obs_flow(obs_raw, up))),
        th61_down_mean=float(np.nanmean(_obs_flow(obs_raw, down))),
        th61_detector_mean=float(np.nanmean(_obs_flow(obs_raw, det))),
        th61_residual=res,
        th61_band=band,
        th61_day_residuals=tuple(float(r["residual_veh_h"]) for r in days),
        th61_day_verdicts=tuple(str(r["verdict"]) for r in days),
        s792_gap_hours=tuple(h[:3]),
        s792_gap_mean=h[3],
    )


def _brackets(
    demand: Mapping[str, Any], station_x: Mapping[str, float]
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """``(up, down, ramps)`` per pair of consecutive stations, ramps in ``(x_up, x_down]``."""
    order = sorted(station_x, key=lambda s: station_x[s])
    out = []
    for up, down in pairwise(order):
        ramps = [r for r in demand["ramps"] if station_x[up] < float(r["x_m"]) <= station_x[down]]
        out.append((up, down, sorted(ramps, key=lambda r: float(r["x_m"]))))
    return out


def draft1_screen(
    obs_raw: Mapping[str, Any],
    dq_raw: Mapping[str, Any],
    dates: list[str],
    demand: Mapping[str, Any],
    station_x: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Draft 1's test on every bracket of the method with a ramp detector it uses.

    ``demand`` is the base's record (no rule): a ramp "has a detector" when the
    method used one (``detector`` / ``detector_scaled``). A bracket with a ramp
    that has none is closed by that ramp, so its residual is that ramp's volume,
    not a detector's error, and the test does not apply. Otherwise the test is
    the draft's: the segment residual (data-quality artifact, per calibration
    day) has one sign on every day, and its calibration-day mean leaves the
    quadrature count-error band (:func:`segment_residual`). ``sole_ramp`` says
    whether the mainline difference identifies the ramp (the only ramp of its
    bracket). Recorded, never applied by itself: D10 applies rule (b) to the
    detector it names (``TH61``).
    """
    ce = float(dq_raw["parameters"]["count_error"])
    rows: list[dict[str, Any]] = []
    for up, down, ramps in _brackets(demand, station_x):
        used = [r for r in ramps if r["method"] in ("detector", "detector_scaled")]
        if not used:
            continue
        row: dict[str, Any] = {
            "segment": f"{up}->{down}",
            "ramps": [
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "method": r["method"],
                    "detector": r.get("station") if r in used else None,
                }
                for r in ramps
            ],
            "sole_ramp": len(ramps) == 1,
        }
        if len(used) < len(ramps):
            row.update(
                applies=False,
                meets_draft_1=False,
                note="a ramp without a used detector closes this bracket: the residual is its "
                "volume, not a detector's error",
            )
            rows.append(row)
            continue
        ons = [str(r["station"]) for r in used if r["kind"] == "on"]
        offs = [str(r["station"]) for r in used if r["kind"] == "off"]
        mean, band = segment_residual(obs_raw, up, down, ons, offs, ce)
        days = segment_days(dq_raw, up, down, dates)
        vals = [float(d["residual_veh_h"]) for d in days]
        dq_ramps = sorted({str(x) for d in days for x in d["measured_ramps"]})
        same = len(vals) == len(dates) and (all(v < 0 for v in vals) or all(v > 0 for v in vals))
        row.update(
            applies=True,
            residual_4h_mean_veh_h=round(mean, 1),
            quadrature_band_veh_h=round(band, 1),
            day_residuals_veh_h=vals,
            day_verdicts=[str(d["verdict"]) for d in days],
            same_sign_every_day=same,
            data_quality_ramps=dq_ramps,
            data_quality_ramps_agree=dq_ramps == sorted(ons + offs),
            meets_draft_1=bool(same and abs(mean) > band),
        )
        rows.append(row)
    return rows


def _plan_hours(
    demand: Mapping[str, Any], station_x: Mapping[str, float], ramp: str
) -> list[float]:
    ramps, _ = plan_volumes(demand, station_x)
    return [round(v, 1) for v in hours(ramps[ramp])]


def _screen_if_applied(
    base_doc: Mapping[str, Any],
    net_path: Path,
    screen: list[dict[str, Any]],
    station_x: Mapping[str, float],
    ignore_base: Mapping[str, str],
) -> None:
    """For each bracket that meets draft 1 but is not rule (b)'s, what applying it would plan.

    Adds ``if_applied`` to the screen row: the bracket's ramps' planned 4-h
    volumes (free flow) with no detector ignored, each detector ignored alone,
    and all of them ignored, on top of rule (b). A reading aid for the scope of
    draft 1's wording; nothing here is written into a scenario.
    """
    for row in screen:
        if not row.get("meets_draft_1") or row["segment"] == f"{TH61['up']}->{TH61['down']}":
            continue
        dets = [r["detector"] for r in row["ramps"] if r["detector"]]
        names = [r["name"] for r in row["ramps"]]
        variants: dict[str, dict[str, str]] = {"rule (b) only": {}}
        for d in dets:
            variants[f"{d} ignored"] = {d: "draft 1 screen"}
        if len(dets) > 1:
            variants["all ignored"] = dict.fromkeys(dets, "draft 1 screen")
        out: dict[str, Any] = {}
        for label, extra in variants.items():
            demand = derive(
                base_doc,
                net_path,
                carry_residuals=False,
                ignore_ramp_detectors={**ignore_base, **extra},
            )[1]
            out[label] = {n: _plan_hours(demand, station_x, n)[3] for n in names}
        row["if_applied"] = {
            "planned_4h_mean_veh_h": out,
            "note": "free-flow plan volumes of this bracket's ramps; not written into any scenario",
        }


def _changed(base: Mapping[str, Any], doc: Mapping[str, Any]) -> tuple[str, ...]:
    a, b = series_paths(base), series_paths(doc)
    return tuple(k for k in a if a[k] != b[k])


def _weave_params_explicit(doc: Mapping[str, Any]) -> bool:
    weaves = [r["weave"] for r in doc["network"]["ramps"] if r.get("weave")]
    return len(weaves) == 2 and all(w.get("weave_params") == D10_WEAVE_PARAMS for w in weaves)


def _wrap(text: str, first: str, rest: str = "#   ", width: int = 118) -> list[str]:
    """A header paragraph as comment lines (never broken inside a word or at its hyphens)."""
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=False,
        break_on_hyphens=False,
    )


_NUMBER = {2: "two", 3: "three", 4: "four"}


def _hours_txt(vals: list[float]) -> str:
    return " / ".join(f"{v:,.0f}" for v in vals)


def _recorded_policy(recorded: str, doc: Mapping[str, Any]) -> str:
    """``policy vN``: the policy under which ``recorded`` (a battery's record) is ``doc``'s hash.

    Raises:
        ValueError: ``recorded`` is ``doc``'s hash under none of :data:`POLICIES`.
    """
    version = recorded_policy(recorded, doc)
    if version is None:
        raise ValueError(
            f"{D10_BASE_BATTERY} records config_hash {recorded}, the base's under none of the "
            f"policies {POLICIES}; refusing"
        )
    return f"policy v{version}"


def header_d10(
    date: str,
    arm: D10Arm,
    doc: Mapping[str, Any],
    base_doc: Mapping[str, Any],
    demand_sha: str,
    facts: D10Facts,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    screen: list[dict[str, Any]],
) -> list[str]:
    """The provenance block of a D10 arm (module docstring); the last line states its hash."""
    name = str(doc["name"])
    rules = "rule (b)" if arm.rules == ("b",) else "rules (b) and (c)"
    changed = "; ".join(
        c.removeprefix("ramp ")
        .replace(".inflow", " inflow")
        .replace(".exit_fraction", " exit_fraction")
        for c in arm.changes
    )
    battery = json.loads(_path(D10_BASE_BATTERY).read_text())
    recorded = str(battery["config_hash"])
    paragraphs = [
        f"{name}: Phase A's I-94 base with D10's {rules} (docs/PRE_FRISCO_PROGRAM.md, D10).",
        f"Written {date} by scripts/i94_calibration_days.py --only d10; do not edit by hand.",
        f"Base: {D10_BASE} (sha256 {sha256_of(D10_BASE)}, config hash {scenario_hash(base_doc)}, "
        f"{POLICY}); its battery {D10_BASE_BATTERY} (stage p10) records config_hash {recorded} "
        f"({_recorded_policy(recorded, base_doc)}). Its header and that of {OUT_CAL} apply unchanged: the five "
        "calibration days, carry_residuals=False, the Amendment-1 drivers, the reference configuration xlsfg, and "
        "W1b + W2 set explicitly on both weaves (weave_params; FRISCO_PROTOCOL Amendment 4).",
        f"Changed, nothing else: name; {changed}. The same demand method on the same targets without the "
        f"rule{'s' if len(arm.rules) > 1 else ''} reproduces every one of the base's "
        f"{len(series_paths(base_doc))} series exactly; the build refuses otherwise, and refuses any other change.",
        "Rules adopted as amendment text on 2026-10-07, before the run (docs/I94_CALIBRATION_DAYS.md section 3, "
        f'drafts 1 and 2): (b) "{DRAFT_1}"' + (f' (c) "{DRAFT_2}"' if "c" in arm.rules else ""),
    ]
    days = facts.th61_day_residuals
    th = TH61["ramp"]
    paragraphs.append(
        f"(b) The T.H.61 NB entrance 53062592 closes its bracket {TH61['up']}-{TH61['down']} by conservation "
        f"instead of using {TH61['detector']} (calibrate_scenario ignore_ramp_detectors). On the calibration days "
        f"the segment's residual has the same sign on all {len(days)} ({min(days):,.1f} to {max(days):,.1f} veh/h, "
        f"{DATA_QUALITY}), and its 4-h mean, {facts.th61_residual:+,.1f} veh/h ({TH61['down']} - {TH61['up']} = "
        f"{facts.th61_down_mean - facts.th61_up_mean:,.1f} against {TH61['detector']}'s "
        f"{facts.th61_detector_mean:,.1f}; section 3 rounds it to -317), is outside the quadrature count-error "
        f"band of +-{facts.th61_band:,.1f} veh/h (count error {facts.count_error:g} on each of the three 4-h means "
        f"{facts.th61_up_mean:,.1f}, {facts.th61_down_mean:,.1f} and {facts.th61_detector_mean:,.1f})."
    )
    literal: dict[int, list[str]] = {
        len(paragraphs): [
            "#   T.H.61 NB inflow, veh/h, hours from 06:30 / 07:30 / 08:30 / 05:30-09:30 mean:",
            f"#     {_hours_txt(before[th])} -> {_hours_txt(after[th])}.",
        ]
    }
    for r in (r for r in screen if r.get("meets_draft_1") and not r["sole_ramp"]):
        dets = " and ".join(str(x["detector"]) for x in r["ramps"] if x["detector"])
        paragraphs.append(
            f"Draft 1's test is also met at {r['segment']} ({dets}: {r['residual_4h_mean_veh_h']:+,.1f} against "
            f"+-{r['quadrature_band_veh_h']:,.1f} veh/h, the same sign on every calibration day), where one mainline "
            f"difference cannot identify {_NUMBER.get(len(r['ramps']), len(r['ramps']))} ramps. Not applied here: "
            "D10 names rule (b) for T.H.61 "
            "NB; the record's draft_1_screen gives what applying it would plan."
        )
    if "c" in arm.rules:
        g = facts.s792_gap_hours
        paragraphs.append(
            "(c) S792 is out of the demand balance (calibrate_scenario skip_stations): the brackets S1948-S792 "
            "and S792-S791 merge into S1948-S791; S792 stays in the observations, so the battery and the gate "
            "still score it. S792 counts two of its three loops x1.5 (loop 3240 excluded) and reads "
            f"{_hours_txt(list(g))} veh/h below S791 in the scored hours ({facts.s792_gap_mean:,.1f} on the 4-h "
            "mean) with only an exit between them. The merged bracket's two exits have no usable detector: the "
            "method splits S1948 - S791 equally between them (protocol section 2.3's documented assumption); both "
            "stay uncertain inputs (sections 2.3 and 8.5; the sensitivity runs are not made)."
        )
        exits = [
            "#   Exit volume in the plan's free flow, veh/h, 06:30 / 07:30 / 08:30 / 4-h mean (mean exit fraction):"
        ]
        for ramp, label in (
            ("off-ramp 18207912", "Mounds Blvd exit 18207912"),
            ("off-ramp 42165869", "6th St left exit 42165869"),
        ):
            exits.append(
                f"#     {label}: {_hours_txt(before[ramp])} ({before[ramp + ' fraction']:.3f}) -> "
                f"{_hours_txt(after[ramp])} ({after[ramp + ' fraction']:.3f})."
            )
        literal[len(paragraphs)] = exits
    paragraphs += [
        f"Status: a cloud battery input (stage p16_i94_d10, {D10_STAGE}); not run (the laptop rule). Nothing is "
        "calibrated or validated on it yet. seeded=False.",
        f"Record: {arm.demand_out} (sha256 {demand_sha}).",
    ]
    lines: list[str] = []
    for i, p in enumerate(paragraphs):
        lines += literal.get(i, [])
        lines += _wrap(p, "# ")
    return [*lines, f"# config hash {scenario_hash(doc)} ({POLICY})."]


def _plan_summary(
    demand: Mapping[str, Any], station_x: Mapping[str, float], arm: D10Arm
) -> dict[str, Any]:
    """Planned free-flow hours of the series ``arm`` changes, and each exit's mean fraction."""
    ramps, _ = plan_volumes(demand, station_x)
    out: dict[str, Any] = {}
    for key in arm.changes:
        name = key.removeprefix("ramp ").rpartition(".")[0]
        out[name] = [round(v, 1) for v in hours(ramps[name])]
        rec = next(r for r in demand["ramps"] if r["name"] == name)
        if rec["kind"] == "off":
            out[name + " fraction"] = float(np.mean([s for _, s in rec["exit_fraction_steps"]]))
    return out


def _stations_minus_counts(
    demand: Mapping[str, Any], obs_raw: Mapping[str, Any], station_x: Mapping[str, float]
) -> dict[str, list[float]]:
    """Planned free-flow station flow minus the calibration-day count, hours 06:30 / 07:30 / 08:30 / 4-h."""
    _, st = plan_volumes(demand, station_x)
    return {
        sid: [
            round(p - o, 1)
            for p, o in zip(hours(st[sid]), hours(_obs_flow(obs_raw, sid)), strict=True)
        ]
        for sid in ("S1069", "S1070", "S1948", "S792", "S791", "S790", "S97")
    }


def build_d10(date: str, workdir: Path) -> Built:
    """Derive D10's two arms and their demand records from Phase A's base (module docstring).

    Raises:
        ValueError: The writer does not reproduce the base's text, the base does
            not set W1b + W2 explicitly on both weaves, the method without the
            rules does not reproduce the base's series, or an arm changes other
            series than its rules do.
    """
    body = split_header(_path(D10_BASE).read_text())[1]
    base = yaml.safe_load(body)
    if render(base, width=D10_WIDTH) != body:
        raise ValueError(f"{D10_BASE}: the writer does not reproduce its text; refusing")
    if not _weave_params_explicit(base):
        raise ValueError(f"{D10_BASE}: W1b + W2 are not set explicitly on both weaves")
    obs_raw = json.loads(_path(OBSERVATIONS).read_text())
    dates = check_observations(obs_raw, json.loads(_path(DAY_SPLIT).read_text()))
    dq_raw = json.loads(_path(DATA_QUALITY).read_text())
    obs = Observations.from_json(_path(OBSERVATIONS))
    station_x = {s.id: float(s.x_m or 0.0) for s in obs.stations if s.kind == "mainline"}
    net = compile_net(base, workdir / "d10")
    filled0, demand0, _ = derive(base, net, carry_residuals=False)
    if _changed(base, transplant(base, filled0, str(base["name"]))):
        raise ValueError(
            f"{D10_BASE}: its series are not what the demand method gives on the calibration days "
            "without the rules; refusing to write arms that would differ from it in more than a rule"
        )
    facts = d10_facts(obs_raw, dq_raw, dates)
    screen = draft1_screen(obs_raw, dq_raw, dates, demand0, station_x)
    th61_row = next(r for r in screen if r["segment"] == f"{TH61['up']}->{TH61['down']}")
    if not (th61_row["meets_draft_1"] and th61_row["sole_ramp"]):
        raise ValueError(
            f"{TH61['up']}->{TH61['down']} does not meet draft 1's test; rule (b) not applied"
        )
    g = facts.s792_gap_hours
    if not all(x > 0 for x in (*g, facts.s792_gap_mean)):
        raise ValueError("S792 does not read below S791 in every scored hour; rule (c) not applied")
    reasons = {
        "b": {
            TH61["detector"]: (
                "rule (b), docs/I94_CALIBRATION_DAYS.md section 3 draft 1, adopted as amendment text "
                f"2026-10-07 (docs/PRE_FRISCO_PROGRAM.md D10): {TH61['up']}->{TH61['down']}'s residual "
                f"has the same sign on all {len(facts.th61_day_residuals)} calibration days and its 4-h "
                f"mean, {facts.th61_residual:+.1f} veh/h, leaves the quadrature count-error band "
                f"+-{facts.th61_band:.1f}; the ramp takes the mainline difference"
            )
        },
        "c": {
            S792_PAIR["station"]: (
                "rule (c), docs/I94_CALIBRATION_DAYS.md section 3 draft 2, adopted as amendment text "
                "2026-10-07: two of three loops scaled x1.5 (loop 3240 excluded); reads "
                f"{g[0]:.0f} / {g[1]:.0f} / {g[2]:.0f} veh/h below S791 across an exit (scored hours; "
                f"{facts.s792_gap_mean:.0f} on the 4-h mean); out of the demand balance, still scored"
            )
        },
    }
    _screen_if_applied(base, net, screen, station_x, reasons["b"])
    files: dict[str, str] = {}
    docs: dict[str, dict[str, Any]] = {}
    summary: list[str] = []
    for arm in D10_ARMS:
        filled, demand, _ = derive(
            base,
            net,
            carry_residuals=False,
            skip_stations=reasons["c"] if "c" in arm.rules else None,
            ignore_ramp_detectors=reasons["b"],
        )
        doc = transplant(base, filled, f"{base['name']}_{arm.suffix}")
        changed = _changed(base, doc)
        if changed != arm.changes:
            raise ValueError(
                f"{arm.out}: the rules change {list(changed)}, not {list(arm.changes)}; refusing"
            )
        before = _plan_summary(demand0, station_x, arm)
        after = _plan_summary(demand, station_x, arm)
        demand = dict(demand)
        demand["scenario"] = arm.out
        demand["observations"] = OBSERVATIONS
        demand["config_hash"] = scenario_hash(doc)
        demand["provenance"] = {
            "script": "scripts/i94_calibration_days.py (--only d10)",
            "program": "docs/PRE_FRISCO_PROGRAM.md, D10",
            "note": "docs/I94_CALIBRATION_DAYS.md sections 3 and 4",
            "stage": D10_STAGE,
            "rules": {r: {"b": DRAFT_1, "c": DRAFT_2}[r] for r in arm.rules},
            "rules_adopted": "as amendment text, 2026-10-07, before the run (PRE_FRISCO_PROGRAM.md, "
            "coordinator's decisions under the owner's delegation)",
            "observations_sha256": sha256_of(OBSERVATIONS),
            "day_split": DAY_SPLIT,
            "day_split_sha256": sha256_of(DAY_SPLIT),
            "calibration_dates": dates,
            "stations_x": STATIONS_X,
            "stations_x_sha256": sha256_of(STATIONS_X),
            "data_quality": DATA_QUALITY,
            "data_quality_sha256": sha256_of(DATA_QUALITY),
            "base_scenario": D10_BASE,
            "base_scenario_sha256": sha256_of(D10_BASE),
            "base_config_hash": scenario_hash(base),
            "base_battery": D10_BASE_BATTERY,
            "base_series_reproduced": (
                "the method without the rules gives every one of the base's series exactly"
            ),
            "transplanted": [
                "network.inflow",
                "ramps[*].inflow / exit_fraction",
                "network.boundary.steps",
            ],
            "changed": list(arm.changes),
            "kept_from_base": "everything else, including network.boundary.exit_buffer_m and the "
            "explicit W1b + W2 weave_params",
            "net": "microsim.runner._build_network of the base's network block (netconvert only)",
            "facts": facts.record(),
            "planned_free_flow_veh_h": {
                "hours": ["06:30", "07:30", "08:30", "05:30-09:30"],
                "base": before,
                "arm": after,
            },
            "station_plan_minus_count_veh_h": {
                "hours": ["06:30", "07:30", "08:30", "05:30-09:30"],
                "base": _stations_minus_counts(demand0, obs_raw, station_x),
                "arm": _stations_minus_counts(demand, obs_raw, station_x),
            },
            "draft_1_screen": screen,
            "draft_1_scope": (
                "D10 applies rule (b) to the detector it names, rnd_88807, the only ramp of its "
                "bracket; the screen records every bracket whose ramps all have a used detector"
            ),
        }
        demand_text = json.dumps(demand, indent=1) + "\n"
        demand_sha = hashlib.sha256(demand_text.encode()).hexdigest()
        head = header_d10(date, arm, doc, base, demand_sha, facts, before, after, screen)
        files[arm.out] = "\n".join(head) + "\n" + render(doc, width=D10_WIDTH)
        files[arm.demand_out] = demand_text
        docs[arm.out] = doc
        summary.append(f"{arm.suffix}: changed {', '.join(changed)}")
    return Built(files=files, docs=docs, summary=summary)


# --- main -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    """Everything the script writes, as text."""

    files: dict[str, str]
    docs: dict[str, dict[str, Any]]
    summary: list[str]


def build(
    date: str, workdir: Path, transfer_check: str, sets: tuple[str, ...] = ("cal", "d10")
) -> Built:
    """Derive the requested sets: ``cal`` (:func:`build_cal`) and ``d10`` (:func:`build_d10`)."""
    files: dict[str, str] = {}
    docs: dict[str, dict[str, Any]] = {}
    summary: list[str] = []
    if "cal" in sets:
        b = build_cal(date, workdir, transfer_check)
        files.update(b.files)
        docs.update(b.docs)
        summary += b.summary
    if "d10" in sets:
        b = build_d10(date, workdir)
        files.update(b.files)
        docs.update(b.docs)
        summary += b.summary
    return Built(files=files, docs=docs, summary=summary)


def build_cal(date: str, workdir: Path, transfer_check: str) -> Built:
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
    p.add_argument(
        "--only",
        choices=("cal", "d10"),
        help="write or check one set: cal (the _dc_cal family and its record) or d10 (D10's arms "
        "and their records); default both",
    )
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
            sets = (args.only,) if args.only else ("cal", "d10")
            built = build(date, workdir, args.transfer_check, sets)
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
            # a stated hash is checked under the policy it names, never against today's alone
            if rel.endswith(".json"):
                rec = json.loads(have)
                problems = record_hash_problems(rec)
                same = _without_hashes(rec) == _without_hashes(json.loads(text))
            else:
                problems = header_hash_problems(have)
                same = yaml.safe_load(split_header(have)[1]) == built.docs[rel]
            for problem in problems:
                print(f"check: {rel}: {problem}", file=sys.stderr)
            if not same:
                print(f"check: {rel} differs from what the recipe gives", file=sys.stderr)
            if problems or not same:
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
            tag = f"  {built.docs[rel]['name']}  config hash {scenario_hash(built.docs[rel])} ({POLICY})"
        print(f"-> {rel}{tag}")
    print("the demand method's record (its filled scenario, not the files written):")
    for line in built.summary:
        print(line)
    print(
        "the files written keep their source's boundary.exit_buffer_m and everything else but the "
        "series and the name (module docstring)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
