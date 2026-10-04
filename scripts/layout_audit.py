"""Layout audit of a corridor, for a check against satellite imagery (WP-102).

Stage 1, item 4 of the Frisco plan: "Check the road layout by hand against
satellite imagery: lanes, ramps, acceleration lanes, lane drops. The map
source got Minnesota's ramps wrong, and the model inherits every map error."

The network is built exactly as the runner builds it
(``microsim.runner._build_network``: the scenario's OSM extract, its
``netconvert_extra``, its ``patch_files`` and the merge models' patches), so
the audit sees what is simulated. :func:`microsim.layout_audit.audit_layout`
then lists every segment and event in travel order with satellite links and
runs the automatic checks. Three files go to ``--out``:

* ``layout_audit.json`` — the full record (schema ``flowstate.layout_audit/1``)
  with provenance: the commit, ``code_dirty`` over this script and the code it
  runs (tests/test_scripts/test_code_dirty.py convention), the scenario's
  config hash, and the sha256 of the OSM extract and of every patch;
* ``layout_audit.csv`` — segments and events interleaved in travel order, with
  empty ``checked_by`` / ``imagery_date`` / ``result`` columns;
* ``layout_checklist.md`` — the checklist a person works through
  (docs/LAYOUT_CHECKLIST.md is the procedure).

Run from the repository root (the scenario's paths are repository-relative).
On a VM, the MnDOT corridor:

    uv run --no-sync python scripts/layout_audit.py \\
        --scenario scenarios/mndot_i94_wb_stpaul_weave.yaml \\
        --out runs/wp102/layout_mndot_i94_wb_stpaul_weave

An onboarding output directory (the API job's ``<results>/corridors/<id>/``,
which holds ``scenario.yaml``; or any directory holding exactly one scenario
YAML):

    uv run --no-sync python scripts/layout_audit.py \\
        --onboarding-dir <results>/corridors/<id> --out runs/wp102/layout_<name>

Exit status: 0; 2 for a usage error (no scenario, not an OSM corridor); with
``--fail-on-defect``, 4 when an automatic check reports a ``defect`` (an
entrance or exit compiled on the wrong side of the mainline).

Nothing here changes the network. A difference is corrected only after the
imagery confirms it (docs/FRISCO_PROTOCOL.md §7.3).
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from flowstate_core.config import OSMNetwork, ScenarioConfig, config_hash
from microsim.layout_audit import LayoutAudit, audit_layout

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The paths whose uncommitted changes make the record's ``code_dirty`` true:
#: this script and the code it runs (a rewritten data file or scenario is not
#: dirty code; tests/test_scripts/test_code_dirty.py convention).
CODE_PATHS = (
    "scripts/layout_audit.py",
    "packages/microsim",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

JSON_NAME = "layout_audit.json"
CSV_NAME = "layout_audit.csv"
MARKDOWN_NAME = "layout_checklist.md"

#: The file an API onboarding job writes its scenario to
#: (``api.onboarding_jobs.SCENARIO_FILENAME``).
ONBOARDING_SCENARIO = "scenario.yaml"

#: Exit status of a usage error.
BAD_USAGE_EXIT = 2

#: Exit status of ``--fail-on-defect`` when a defect is reported (the same code
#: ``scripts/onboard_corridor.py --fail-on-split-defect`` uses).
DEFECT_EXIT = 4


def git_head() -> str:
    """The code's commit (``"<sha> <subject>"``), or ``"unknown"``."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty() -> bool | None:
    """Whether :data:`CODE_PATHS` had uncommitted changes (None without git)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def sha256_of(path: Path) -> str | None:
    """sha256 of a file's bytes, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def find_scenario(directory: Path) -> Path:
    """The scenario YAML of an onboarding output directory.

    Args:
        directory: The API job's ``<results>/corridors/<id>/`` (holds
            ``scenario.yaml``), or a directory holding exactly one ``*.yaml``.

    Returns:
        The scenario path.

    Raises:
        ValueError: No scenario, or more than one candidate.
    """
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory")
    named = directory / ONBOARDING_SCENARIO
    if named.is_file():
        return named
    candidates = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"{directory} holds no {ONBOARDING_SCENARIO} and no other *.yaml")
    names = ", ".join(p.name for p in candidates)
    raise ValueError(f"{directory} holds several scenario files ({names}); pass --scenario")


def build_and_audit(cfg: ScenarioConfig, workdir: Path) -> LayoutAudit:
    """Build the scenario's network the runner's way and audit it.

    The build is ``microsim.runner._build_network`` itself — the same
    ``osm_import`` call, ``netconvert_extra``, ``patch_files`` and merge-model
    patches as every run — so no netconvert option is restated here.

    Args:
        cfg: An OSM-corridor scenario.
        workdir: Where the network is compiled.

    Returns:
        The audit (its ``provenance`` is filled by :func:`main`).
    """
    from microsim.runner import _build_network

    net = cfg.network
    if not isinstance(net, OSMNetwork):
        raise ValueError(f"the layout audit needs an OSM corridor, not {net.kind!r}")
    workdir.mkdir(parents=True, exist_ok=True)
    bundle = _build_network(cfg, workdir)
    osm_path = Path(net.osm_file) if net.osm_file else workdir / "extract.osm"
    return audit_layout(
        bundle.net_path,
        osm_path,
        tuple(net.corridor_edges),
        ramps=list(net.ramps),
        corridor=cfg.name,
        netconvert_extra=tuple(net.netconvert_extra),
        patch_files=[*net.patch_files, *bundle.patch_files],
        terminated_lanes=bundle.terminated_lanes,
    )


def _package_version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    """The command line (module docstring)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/layout_audit.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario", type=Path, help="an OSM-corridor scenario YAML")
    source.add_argument(
        "--onboarding-dir",
        type=Path,
        help="an onboarding output directory (holds scenario.yaml, or exactly one *.yaml)",
    )
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="where the network is compiled (default: <out>/net)",
    )
    parser.add_argument(
        "--fail-on-defect",
        action="store_true",
        help=f"exit {DEFECT_EXIT} when an automatic check reports a defect",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build, audit, write the three files; returns the exit status."""
    args = build_parser().parse_args(argv)
    try:
        scenario_path = args.scenario or find_scenario(args.onboarding_dir)
        cfg = ScenarioConfig.from_yaml(scenario_path)
        if not isinstance(cfg.network, OSMNetwork):
            raise ValueError(
                f"{scenario_path}: the layout audit needs an OSM corridor "
                f"(network.kind 'osm'), not {cfg.network.kind!r}"
            )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__)
        return BAD_USAGE_EXIT
    workdir = args.workdir or args.out / "net"
    audit = build_and_audit(cfg, workdir)

    patches = [Path(p) for p in audit.patch_files]
    provenance: dict[str, Any] = {
        "script": "scripts/layout_audit.py",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": git_head(),
        "code_dirty": git_dirty(),
        "argv": list(sys.argv[1:] if argv is None else argv),
        "scenario": str(scenario_path),
        "config_hash": config_hash(cfg),
        "osm_file": audit.osm_path,
        "osm_sha256": sha256_of(Path(audit.osm_path)),
        "patch_sha256": {str(p): sha256_of(p) for p in patches},
        "versions": {
            "eclipse-sumo": _package_version("eclipse-sumo"),
            "sumolib": _package_version("sumolib"),
        },
    }
    audit = replace(audit, provenance=provenance)
    args.out.mkdir(parents=True, exist_ok=True)
    audit.to_json(args.out / JSON_NAME)
    audit.to_csv(args.out / CSV_NAME)
    (args.out / MARKDOWN_NAME).write_text(audit.to_markdown())

    print("\n".join(audit.summary_lines()))
    print(f"wrote {args.out / JSON_NAME}, {args.out / CSV_NAME} and {args.out / MARKDOWN_NAME}")
    if provenance["code_dirty"]:
        print("NOTE: the audited code has uncommitted changes (code_dirty: true)")
    if args.fail_on_defect and audit.defects():
        print(f"FAIL: {len(audit.defects())} defect(s) reported by the automatic checks")
        return DEFECT_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
