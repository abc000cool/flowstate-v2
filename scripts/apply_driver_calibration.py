"""Write a corridor's calibrated scenario from its Amendment-1 driver calibration.

docs/FRISCO_PROTOCOL.md Amendment 1 lets two driver settings be calibrated —
the population's mean ``a_max`` (mean + k·sd of the measured population, a
derived population file) and ``lc_keep_right`` — and fixes that the
acceptance runs (the baseline gate) use the chosen values unchanged.
``scripts/calibrate_driver_grid.py`` writes the choice to
``artifacts/driver_calibration_<corridor>.json`` (``selection.chosen``: ``k``,
``lc_keep_right``). This script turns that choice into the scenario file the
full tests run (``scripts/gcp/pipeline_i24.sh`` stage 23), by minimal text
edits of the corridor's reference — every other byte preserved:

* ``i24`` — ``scenarios/i24_replica_flow_speedcal.yaml`` (the grid's
  reference) -> ``scenarios/i24_replica_flow_speedcal_dc.yaml``, name
  ``i24_replica_flow_speedcal_dc``.
* ``i94`` — the 4-hour weave scenario ``scenarios/mndot_i94_wb_stpaul_weave.yaml``
  under the corridor's reference configuration ``xlsfg`` (weave
  ``exit_prepare`` 1.0 on every weaving section, network ``lane_end_giveup_m``
  7.5, ``force_guard`` 1.0 on the two scripted merges: pipeline stage
  ``p1_mndot_ref``'s sed/awk recipe, line for line, whose battery and gate are
  on record as the uncalibrated comparison) ->
  ``scenarios/mndot_i94_wb_stpaul_weave_dc.yaml``, name
  ``mndot_i94_wb_stpaul_weave_xlsfg_dc``. The grid itself ran the corridor's
  35-minute slice under the same configuration; the fleet block is the same.

The edits: the top-level ``name``; ``fleet.idm_calibration`` -> the derived
population for k (k = 0 keeps ``artifacts/idm_i24_capacity.json``, the
population both references run); ``fleet.lc_keep_right`` -> the chosen value;
for I-94 the ``xlsfg`` lines. The source must run the current setting (that
population, keep-right 0) — the calibration is relative to it. The edited
document is checked against the data recipe the grid used
(``calibrate_driver_grid.xlsfg_variant`` / ``pair_config``): the same
document, or nothing is written. A comment header records the provenance: the
artifact (path, sha256, the rule's outcome) or the explicit values, the
population (path, sha256, mean ``a_max``), the source (path, sha256), the date
and the config hash; the source's own header follows it unchanged.

From the artifact, the script refuses an artifact of another schema or
corridor, an incomplete grid (no choice), a grid that is not the amendment's,
a choice off the grid, and a population file whose bytes changed since the
grid ran. ``--k K --keep-right V`` writes the same file for explicit values on
the grid (labelled as not the grid's choice). ``--check`` compares an existing
file with what would be written (the documents, not the header's date) and
exits 1 when they differ — the pipeline's guard for a committed file.

Run (repository root; text only, no simulation)::

    uv run --no-sync python scripts/apply_driver_calibration.py --corridor i24 \\
        --artifact artifacts/driver_calibration_i24.json
    uv run --no-sync python scripts/apply_driver_calibration.py --corridor i94 \\
        --artifact artifacts/driver_calibration_i94.json
    uv run --no-sync python scripts/apply_driver_calibration.py --corridor i94 --k 0.5 --keep-right 0.25 \\
        --out /tmp/x.yaml
    uv run --no-sync python scripts/apply_driver_calibration.py --corridor i24 \\
        --artifact artifacts/driver_calibration_i24.json --check

Exit codes: 0 written (or ``--check``: the same document); 1 ``--check``
found a different or missing file; 2 refused (the reason on stderr).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibrate_driver_grid import (
    SCHEMA,
    SPECS,
    check_populations,
    pair_config,
    population_for,
    xlsfg_variant,
)

from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    ScenarioConfig,
    config_hash,
    config_hash_v2,
    config_hash_v3,
)
from validation.driver_calibration import CURRENT, K_GRID, KEEP_RIGHT_GRID

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Suffix of every calibrated scenario's name.
SUFFIX = "_dc"
#: Suffix the reference configuration adds to the I-94 name (stage p1_mndot_ref).
XLSFG_SUFFIX = "_xlsfg"
#: The xlsfg recipe's literal lines (scripts/gcp/pipeline_i24.sh stage p1_mndot_ref).
WEAVE_EMPTY = "weave_params: {}"
WEAVE_XLSFG = "weave_params: {exit_prepare: 1.0}"
OSM_KIND_LINE = "  kind: osm"
LANE_END_LINE = "  lane_end_giveup_m: 7.5"
SCRIPTED_LINE = "    merge: scripted"
MERGE_EMPTY_LINE = "    merge_params: {}"
MERGE_GUARD_LINE = "    merge_params: {force_guard: 1.0}"
#: The recipe requires exactly this many guarded scripted merges (McKnight Rd, Hudson Rd).
N_SCRIPTED_MERGES = 2


@dataclass(frozen=True)
class Target:
    """Where a corridor's calibrated scenario comes from and goes.

    Attributes:
        corridor: ``i24`` or ``i94`` (a key of ``calibrate_driver_grid.SPECS``).
        source: The scenario the edits start from.
        out: The calibrated scenario file.
        xlsfg: Apply the I-94 reference configuration's lines.
    """

    corridor: str
    source: str
    out: str
    xlsfg: bool


TARGETS: dict[str, Target] = {
    "i24": Target(
        corridor="i24",
        source="scenarios/i24_replica_flow_speedcal.yaml",
        out="scenarios/i24_replica_flow_speedcal_dc.yaml",
        xlsfg=False,
    ),
    "i94": Target(
        corridor="i94",
        source="scenarios/mndot_i94_wb_stpaul_weave.yaml",
        out="scenarios/mndot_i94_wb_stpaul_weave_dc.yaml",
        xlsfg=True,
    ),
}


@dataclass(frozen=True)
class Choice:
    """The pair to apply and where it came from.

    Attributes:
        k: ``a_max`` shift in measured standard deviations.
        keep_right: ``lc_keep_right``.
        artifact: The driver-calibration artifact (repository-relative), or
            None for explicit values.
        artifact_sha256: Its sha256.
        outcome: The rule's outcome (``validation.driver_calibration.Selection``).
        population_sha256: The population's sha256 the grid recorded.
    """

    k: float
    keep_right: float
    artifact: str | None = None
    artifact_sha256: str | None = None
    outcome: str | None = None
    population_sha256: str | None = None


def _path(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO_ROOT / q


def _rel(p: str | Path) -> str:
    try:
        return str(_path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def sha256_of(path: str | Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(_path(path).read_bytes()).hexdigest()


def _grid_value(value: float, grid: tuple[float, ...]) -> float | None:
    """The grid's own value equal to ``value`` (to 1e-12), else None."""
    hits = [g for g in grid if math.isclose(float(value), g, rel_tol=0.0, abs_tol=1e-12)]
    return float(hits[0]) if hits else None


def explicit_choice(k: float, keep_right: float) -> Choice:
    """Explicit values, which must lie on the amendment's grids.

    Raises:
        ValueError: ``k`` or ``keep_right`` is off the grid.
    """
    kk = _grid_value(k, K_GRID)
    kr = _grid_value(keep_right, KEEP_RIGHT_GRID)
    if kk is None:
        raise ValueError(f"k = {k:g} is not on the Amendment-1 grid {list(K_GRID)}")
    if kr is None:
        raise ValueError(
            f"lc_keep_right = {keep_right:g} is not on the Amendment-1 grid {list(KEEP_RIGHT_GRID)}"
        )
    return Choice(k=kk, keep_right=kr)


def choice_from_artifact(path: str | Path, corridor: str) -> Choice:
    """The rule's choice from a ``flowstate.driver_calibration/1`` artifact.

    Raises:
        ValueError: Another schema or corridor, an incomplete grid (no
            choice), not the amendment's grid, a reference other than the
            corridor's, a choice off the grid, or a population file that
            changed since the grid ran.
        FileNotFoundError: The artifact or the population is missing.
    """
    art: dict[str, Any] = json.loads(_path(path).read_text())
    where = _rel(path)
    if art.get("schema") != SCHEMA:
        raise ValueError(f"{where}: schema {art.get('schema')!r}, not {SCHEMA!r}")
    if art.get("corridor") != corridor:
        raise ValueError(f"{where} is the {art.get('corridor')!r} grid, not {corridor!r}")
    sel = art.get("selection")
    if not art.get("complete") or not isinstance(sel, dict):
        raise ValueError(f"{where}: the grid is incomplete, no pair was chosen")
    if not (art.get("grid") or {}).get("is_amendment_grid"):
        raise ValueError(f"{where}: not the Amendment-1 grid (a test or partial grid)")
    spec = SPECS[corridor]
    ref = art.get("reference") or {}
    if ref.get("scenario") != spec.base_scenario or ref.get("transform") != spec.transform:
        raise ValueError(
            f"{where}: the grid's reference is {ref.get('scenario')!r} (transform "
            f"{ref.get('transform')!r}), not {spec.base_scenario!r} ({spec.transform!r})"
        )
    choice = explicit_choice(float(sel["chosen"]["k"]), float(sel["chosen"]["lc_keep_right"]))
    rec = (art.get("populations") or {}).get(str(choice.k))
    if not isinstance(rec, dict):
        raise ValueError(f"{where}: no population recorded for k = {choice.k:g}")
    expect = population_for(spec, choice.k)
    if rec.get("path") != expect:
        raise ValueError(f"{where}: k = {choice.k:g} ran {rec.get('path')!r}, expected {expect!r}")
    if not _path(expect).is_file():
        raise FileNotFoundError(f"{expect} (the population the grid ran) is missing")
    now = sha256_of(expect)
    if rec.get("sha256") != now:
        raise ValueError(
            f"{expect} changed since the grid ran (sha256 {now[:12]}, the grid recorded "
            f"{str(rec.get('sha256'))[:12]})"
        )
    return Choice(
        k=choice.k,
        keep_right=choice.keep_right,
        artifact=where,
        artifact_sha256=sha256_of(path),
        outcome=str(sel.get("outcome")),
        population_sha256=now,
    )


# --- the text edits ---------------------------------------------------------------------------


def _split(line: str) -> tuple[str, str]:
    """``(content, line ending)`` of one line kept with its ending."""
    body = line.rstrip("\r\n")
    return body, line[len(body) :]


def _fmt_float(v: float) -> str:
    """A float as the scenario files write it (``0.0``, ``0.25``, ``1.0``)."""
    return repr(float(v))


def edit_text(
    text: str,
    *,
    name: str,
    population: str,
    keep_right: float,
    xlsfg: bool,
    expect_population: str,
    expect_keep_right: float = CURRENT[1],
) -> tuple[str, dict[str, int]]:
    """The minimal line edits (module docstring); every other line kept byte for byte.

    Args:
        text: The source scenario's text.
        name: The new top-level name.
        population: The new ``fleet.idm_calibration``.
        keep_right: The new ``fleet.lc_keep_right``.
        xlsfg: Also apply stage p1_mndot_ref's lines.
        expect_population: The population the source must run.
        expect_keep_right: The keep-right the source must run.

    Returns:
        ``(text, counts)``; counts: ``weave_sections``, ``scripted_merges``,
        ``lane_end_giveup_m`` (I-94) or empty.

    Raises:
        ValueError: The source has no single top-level ``name`` / ``fleet``
            block, runs another population or keep-right, or (``xlsfg``) has no
            ``  kind: osm`` line, sets ``lane_end_giveup_m`` already, or does not
            have exactly two scripted merges with empty ``merge_params``.
    """
    lines = text.splitlines(keepends=True)
    names = [i for i, ln in enumerate(lines) if re.fullmatch(r"name: .*", _split(ln)[0])]
    if len(names) != 1:
        raise ValueError(f"expected one top-level 'name:' line, found {len(names)}")
    _, eol = _split(lines[names[0]])
    lines[names[0]] = f"name: {name}{eol}"
    fleet = [i for i, ln in enumerate(lines) if _split(ln)[0] == "fleet:"]
    if len(fleet) != 1:
        raise ValueError(f"expected one top-level 'fleet:' block, found {len(fleet)}")
    end = fleet[0] + 1
    while end < len(lines):
        body = _split(lines[end])[0]
        if body and not body.startswith((" ", "#")):
            break
        end += 1
    for key, new, want in (
        ("idm_calibration", population, expect_population),
        ("lc_keep_right", _fmt_float(keep_right), expect_keep_right),
    ):
        hits = [i for i in range(fleet[0] + 1, end) if _split(lines[i])[0].startswith(f"  {key}: ")]
        if len(hits) != 1:
            raise ValueError(f"expected one 'fleet.{key}' line, found {len(hits)}")
        body, eol = _split(lines[hits[0]])
        have = yaml.safe_load(body.split(": ", 1)[1])
        if have != want:
            raise ValueError(
                f"the source runs fleet.{key} = {have!r}, not the current setting {want!r}: the "
                "Amendment-1 calibration is relative to it"
            )
        lines[hits[0]] = f"  {key}: {new}{eol}"
    counts: dict[str, int] = {}
    if xlsfg:
        lines, counts = _xlsfg_lines(lines)
    return "".join(lines), counts


def _xlsfg_lines(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """Stage p1_mndot_ref's sed/awk recipe on a line list (name excepted)."""
    if any(_split(ln)[0].startswith("  lane_end_giveup_m:") for ln in lines):
        raise ValueError("the source sets network lane_end_giveup_m already")
    out: list[str] = []
    n_weave = n_merge = 0
    inserted = False
    scripted = False
    for line in lines:
        body, eol = _split(line)
        if WEAVE_EMPTY in body:  # sed 's#weave_params: {}#...#': the first one on the line
            body = body.replace(WEAVE_EMPTY, WEAVE_XLSFG, 1)
            n_weave += 1
        if scripted and body == MERGE_EMPTY_LINE:  # the line right after a scripted merge
            body = MERGE_GUARD_LINE
            n_merge += 1
        scripted = body == SCRIPTED_LINE
        out.append(body + eol)
        if body == OSM_KIND_LINE and not inserted:
            out.append(LANE_END_LINE + (eol or "\n"))
            inserted = True
    if not inserted:
        raise ValueError(f"no {OSM_KIND_LINE.strip()!r} line: not an OSM corridor")
    if n_merge != N_SCRIPTED_MERGES:
        raise ValueError(
            f"expected {N_SCRIPTED_MERGES} scripted merges with empty merge_params, found {n_merge}"
        )
    return out, {"weave_sections": n_weave, "scripted_merges": n_merge, "lane_end_giveup_m": 1}


def expected_document(
    source_text: str, *, name: str, population: str, keep_right: float, xlsfg: bool
) -> dict[str, Any]:
    """The same scenario built the grid's way, as data (the check on the text edits)."""
    raw: dict[str, Any] = yaml.safe_load(source_text)
    if xlsfg:
        raw = xlsfg_variant(raw)
    doc = pair_config(raw, population, keep_right)
    doc["name"] = name
    return doc


def default_name(source_text: str, xlsfg: bool) -> str:
    """``<source name>[_xlsfg]_dc``."""
    raw = yaml.safe_load(source_text)
    return f"{raw['name']}{XLSFG_SUFFIX if xlsfg else ''}{SUFFIX}"


@dataclass(frozen=True)
class Written:
    """What :func:`build` produced.

    Attributes:
        text: The file's full text (header + edited source).
        document: The parsed scenario.
        config_hash: Its config hash under today's policy (``CONFIG_HASH_VERSION``).
        population: The population path it runs.
        counts: The xlsfg edit counts (I-94) or empty.
    """

    text: str
    document: dict[str, Any]
    config_hash: str
    population: str
    counts: dict[str, int]


def build(target: Target, choice: Choice, *, source: str, name: str | None, date: str) -> Written:
    """The calibrated scenario's text for ``choice`` (module docstring).

    Raises:
        ValueError: Any refusal of :func:`edit_text`, a population that is not
            the amendment's derivation, or an edit that is not the grid's
            data recipe.
    """
    spec = SPECS[target.corridor]
    src_text = _path(source).read_text()
    name = name or default_name(src_text, target.xlsfg)
    pops, measured = check_populations(spec, [choice.k])
    pop = pops[str(choice.k)]
    if choice.population_sha256 is not None and pop["sha256"] != choice.population_sha256:
        raise ValueError(f"{pop['path']}: sha256 differs from the artifact's record")
    body, counts = edit_text(
        src_text,
        name=name,
        population=pop["path"],
        keep_right=choice.keep_right,
        xlsfg=target.xlsfg,
        expect_population=spec.population_base,
    )
    doc = yaml.safe_load(body)
    want = expected_document(
        src_text,
        name=name,
        population=pop["path"],
        keep_right=choice.keep_right,
        xlsfg=target.xlsfg,
    )
    if doc != want:
        raise ValueError("the edited text is not the grid's data recipe (a source-format surprise)")
    h = config_hash(ScenarioConfig.model_validate(doc))
    header = header_lines(
        name=name,
        target=target,
        choice=choice,
        population=pop,
        measured=measured,
        source=source,
        counts=counts,
        own_config_hash=h,
        date=date,
        source_has_header=src_text.startswith("#"),
    )
    return Written(
        text="".join(f"{ln}\n" for ln in header) + body,
        document=doc,
        config_hash=h,
        population=pop["path"],
        counts=counts,
    )


def header_lines(
    *,
    name: str,
    target: Target,
    choice: Choice,
    population: dict[str, Any],
    measured: dict[str, Any],
    source: str,
    counts: dict[str, int],
    own_config_hash: str,
    date: str,
    source_has_header: bool,
) -> list[str]:
    """The provenance comment block (every line starts with ``#``).

    ``own_config_hash`` is the file's hash under today's policy; the block states it with
    that policy's label (docs/CONTRACTS.md §2), never a fixed one.
    """
    src = _rel(source)
    out = [
        f"# {name}: {src} with the Amendment-1 driver calibration applied",
        "#   (docs/FRISCO_PROTOCOL.md Amendment 1, docs/DISCHARGE_CALIBRATION.md section 3).",
        f"# Written {date} by scripts/apply_driver_calibration.py; do not edit by hand.",
    ]
    pair = f"k = {choice.k:g}, lc_keep_right = {choice.keep_right:g}"
    if choice.artifact is not None:
        out += [
            f"# Choice: {choice.artifact} (sha256 {choice.artifact_sha256})",
            f"#   selection.chosen {pair} (outcome {choice.outcome}).",
        ]
    else:
        out.append(f"# Choice: explicit --k / --keep-right, {pair}: NOT the grid's choice.")
    out += [
        f"# Population: {population['path']} (sha256 {population['sha256']})",
        f"#   mean a_max {population['a_max_mean']:.4f} m/s^2 = measured mean "
        f"{measured['a_max_mean']:.4f} + {choice.k:g} x sd {measured['a_max_sd']:.4f}.",
        f"# Source: {src} (sha256 {sha256_of(source)})",
        "#   changed, nothing else: name, fleet.idm_calibration, fleet.lc_keep_right"
        + (", and the reference" if target.xlsfg else "."),
    ]
    if target.xlsfg:
        out += [
            "#   configuration xlsfg (pipeline stage p1_mndot_ref): weave_params {exit_prepare: 1.0}",
            f"#   on {counts['weave_sections']} weaving section(s), network lane_end_giveup_m 7.5, "
            f"merge_params {{force_guard: 1.0}} on the {counts['scripted_merges']} scripted merges.",
        ]
    out.append(f"# config hash {own_config_hash} (policy v{CONFIG_HASH_VERSION}).")
    if source_has_header:
        out.append("# The source's own header follows; it describes the source, not this file.")
    return out


#: The header line :func:`header_lines` writes: the file's hash and the policy it was written under.
_OWN_HASH_LINE = re.compile(r"^# config hash ([0-9a-f]{12}) \(policy v(\d+)\)\.$", re.M)


def stated_hash_problem(text: str, document: dict[str, Any]) -> str | None:
    """Why the header's ``# config hash H (policy vN).`` line does not name ``document``, or None.

    The hash is checked under the policy the line names (docs/CONTRACTS.md §2): a file written
    under policy 3 states its version-3 hash, true for its date, and is never rewritten to
    relabel it. Today's policy, 3 and 2 are the ones this code reproduces.
    """
    m = _OWN_HASH_LINE.search(text)
    if m is None:
        return "the header states no config hash with its policy"
    stated, version = m.group(1), int(m.group(2))
    if version == CONFIG_HASH_VERSION:
        want = config_hash(ScenarioConfig.model_validate(document))
    elif version == 3:
        want = config_hash_v3(document)
    elif version == 2:
        want = config_hash_v2(document)
    else:
        return f"the header's config hash {stated} is under policy v{version}, which this code does not reproduce"
    if stated != want:
        return (
            f"the header states config hash {stated} (policy v{version}); that policy gives {want}"
        )
    return None


# --- command line ------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python scripts/apply_driver_calibration.py",
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--corridor", required=True, choices=sorted(TARGETS))
    ap.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="driver-calibration artifact (default artifacts/driver_calibration_<corridor>.json)",
    )
    ap.add_argument(
        "--k", type=float, default=None, help="explicit a_max shift (with --keep-right)"
    )
    ap.add_argument("--keep-right", type=float, default=None, help="explicit lc_keep_right")
    ap.add_argument("--source", type=Path, default=None, help="default: the corridor's reference")
    ap.add_argument("--out", type=Path, default=None, help="default: the corridor's _dc file")
    ap.add_argument("--name", default=None, help="default <source name>[_xlsfg]_dc")
    ap.add_argument("--date", default=None, help="header date (default today, UTC)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing --out")
    ap.add_argument(
        "--check",
        action="store_true",
        help="compare an existing --out with what would be written; write nothing",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """Write (or check) the calibrated scenario; returns the exit status."""
    args = build_parser().parse_args(argv)
    target = TARGETS[args.corridor]
    if (args.k is None) != (args.keep_right is None):
        print("give both --k and --keep-right, or neither", file=sys.stderr)
        return 2
    if args.k is not None and args.artifact is not None:
        print("give --artifact or --k/--keep-right, not both", file=sys.stderr)
        return 2
    if args.source is not None and args.out is None:
        print(
            "--source needs --out (the default --out is the reference's _dc file)", file=sys.stderr
        )
        return 2
    source = str(args.source) if args.source is not None else target.source
    out = _path(args.out if args.out is not None else target.out)
    if out.resolve() == _path(source).resolve():
        print("--out is the source", file=sys.stderr)
        return 2
    try:
        if args.k is not None:
            choice = explicit_choice(args.k, args.keep_right)
        else:
            art = args.artifact or Path(f"artifacts/driver_calibration_{target.corridor}.json")
            choice = choice_from_artifact(art, target.corridor)
        date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
        res = build(target, choice, source=source, name=args.name, date=date)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    pair = f"k = {choice.k:g}, lc_keep_right = {choice.keep_right:g}"
    if args.check:
        if not out.is_file():
            print(f"check: {_rel(out)} is missing", file=sys.stderr)
            return 1
        have = yaml.safe_load(out.read_text())
        if have != res.document:
            print(
                f"check: {_rel(out)} is not the scenario for {pair} "
                f"(config hash {res.config_hash}, policy v{CONFIG_HASH_VERSION}); rewrite it with --force",
                file=sys.stderr,
            )
            return 1
        if choice.artifact_sha256 and choice.artifact_sha256 not in out.read_text():
            print(f"check: note: the header does not quote {choice.artifact}'s current sha256")
        stated = stated_hash_problem(out.read_text(), have)
        if stated:
            print(f"check: note: {stated}")
        print(
            f"check: {_rel(out)} is the scenario for {pair} "
            f"(config hash {res.config_hash}, policy v{CONFIG_HASH_VERSION})"
        )
        return 0
    if out.exists() and not args.force:
        print(
            f"refused: {_rel(out)} exists (--check compares, --force overwrites)", file=sys.stderr
        )
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    part.write_text(res.text)
    part.replace(out)
    print(
        f"-> {_rel(out)}: {res.document['name']}, {pair}, population {res.population}, "
        f"config hash {res.config_hash} (policy v{CONFIG_HASH_VERSION})"
        + (f"; xlsfg {res.counts}" if res.counts else "")
    )
    if (choice.k, choice.keep_right) == CURRENT:
        print(
            "note: the chosen pair is the current setting: this scenario differs from the "
            "reference only in its name (and, I-94, the reference configuration it already ran)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
