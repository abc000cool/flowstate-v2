"""The split audit (``microsim.split_audit``, docs/ONBOARDING_MNDOT.md §9).

On the committed I-94 WB St. Paul extract netconvert compiled two right-hand
exits from the leftmost lanes — the 12th Street exit from a lane
``--ramps.guess`` added on the left of ``1001426896``, the Mounds/Kellogg exit
from lanes 3–4 of the five on ``45608485`` — and through traffic trapped there
locked the corridor. The audit compares the side OSM draws every exit on with
the lanes the compiled network feeds it from; these tests pin it on that
extract compiled without and with the committed fixes, on a synthetic
fixture with one right and one left exit, and through the onboarding CLI's
``--write-split-patch`` / ``--fail-on-split-defect``.

``netconvert`` runs on the small committed extracts (well under a second
each); no SUMO simulation is started.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from flowstate_core.config import OSMNetwork, ScenarioConfig
from microsim.networks import osm_import
from microsim.scenarios import apply_split_fixes, corridor_from_bbox
from microsim.split_audit import (
    SplitFinding,
    audit_splits,
    compiled_side,
    connection_patch_lines,
    format_split_table,
    osm_exit_side,
    parse_osm,
    ramps_unset_edges,
    read_turn_lanes,
    side_of_offsets,
    split_defects,
    split_patch_xml,
)

SCENARIO = Path("scenarios/mndot_i94_wb_stpaul_weave.yaml")
OSM = Path("data/osm/mndot_i94_wb_stpaul.osm")
COMMITTED_PATCH = Path("data/osm/mndot_i94_wb_stpaul.splits.con.xml")
FIXTURE = Path("tests/fixtures/splits.osm")

#: The onboarding command of docs/ONBOARDING_MNDOT.md §3, minus the fixes.
MNDOT_BBOX = (44.9425, -93.099, 44.9613, -92.9612)
MNDOT_BEARING = 265.0
MNDOT_EXTRA = ("--ramps.guess", "--ramps.ramp-length", "250")
MNDOT_CHAIN_CAP_M = 11400.0

mndot_files = pytest.mark.skipif(
    not (SCENARIO.is_file() and OSM.is_file() and COMMITTED_PATCH.is_file()),
    reason="MnDOT files absent",
)


def _mndot_network() -> OSMNetwork:
    cfg = ScenarioConfig.model_validate(yaml.safe_load(SCENARIO.read_text()))
    assert isinstance(cfg.network, OSMNetwork)
    return cfg.network


def _compile(net: OSMNetwork, workdir: Path, *, fixed: bool) -> Path:
    """The committed corridor compiled with or without its two split fixes."""
    extra = list(net.netconvert_extra)
    patches = [Path(p) for p in net.patch_files]
    if not fixed:
        at = extra.index("--ramps.unset")
        del extra[at : at + 2]
        patches = []
    bundle = osm_import(
        osm_file=net.osm_file,
        corridor_edges=tuple(net.corridor_edges),
        workdir=workdir,
        keep_edges=tuple(e for r in net.ramps for e in r.edges),
        patch_files=patches,
        netconvert_extra=tuple(extra),
    )
    return bundle.net_path


def _by_exit(findings: list[SplitFinding]) -> dict[str, SplitFinding]:
    return {f.exit_edge: f for f in findings}


@mndot_files
class TestOnTheCommittedExtract:
    def test_without_the_fixes_exactly_the_two_known_defects(self, tmp_path: Path) -> None:
        net = _mndot_network()
        findings = audit_splits(_compile(net, tmp_path, fixed=False), OSM, net.corridor_edges)
        assert len(findings) == 8
        defects = split_defects(findings)
        assert [(d.from_edge, d.exit_edge, d.verdict) for d in defects] == [
            ("45608485", "18207912", "wrong_side"),
            ("1001426896", "82150350", "added_lane_wrong_side"),
        ]
        by_exit = _by_exit(findings)
        # Mounds / Kellogg: drawn 8-9 m to the right, tagged as the two right
        # lanes, compiled from the two LEFTMOST of five.
        mounds = by_exit["18207912"]
        assert mounds.osm_side == "right" and all(o < 0.0 for o in mounds.osm_offsets_m)
        assert 8.0 <= min(abs(o) for o in mounds.osm_offsets_m)
        assert max(abs(o) for o in mounds.osm_offsets_m) <= 9.0
        assert mounds.turn_lanes == "|||slight_right|slight_right"
        assert mounds.turn_lanes_side == "right"
        assert (mounds.compiled_lanes, mounds.exit_from_lanes, mounds.compiled_side) == (
            5,
            (3, 4),
            "leftmost",
        )
        assert not mounds.added_lane and mounds.continuing_edge == "45782590"
        # 12th Street: drawn to the right, an option lane in OSM, compiled from
        # a fourth lane ramp guessing added on the left.
        twelfth = by_exit["82150350"]
        assert twelfth.osm_side == "right" and twelfth.turn_lanes_side == "right"
        assert (twelfth.osm_lanes, twelfth.compiled_lanes, twelfth.exit_from_lanes) == (3, 4, (3,))
        assert twelfth.added_lane and twelfth.compiled_side == "leftmost"
        assert ramps_unset_edges(findings) == ["1001426896"]
        # 6th Street really is a left exit and is compiled on the left: fine.
        sixth = by_exit["42165869"]
        assert sixth.from_edge == "45782590-AddedOffRampEdge"
        assert sixth.osm_side == "left" and all(o > 0.0 for o in sixth.osm_offsets_m)
        assert sixth.turn_lanes == "slight_left;through|none|none"
        assert sixth.compiled_side == "leftmost" and sixth.added_lane
        assert sixth.verdict == "ok" and sixth.remedy == ""
        # every other exit is a right exit compiled from lane 0
        others = [f for f in findings if f.exit_edge not in {"18207912", "82150350", "42165869"}]
        assert len(others) == 5
        assert all(f.verdict == "ok" and f.exit_from_lanes == (0,) for f in others)
        assert all(f.osm_side == "right" == f.turn_lanes_side for f in others)

    def test_with_the_committed_fixes_no_defect(self, tmp_path: Path) -> None:
        net = _mndot_network()
        findings = audit_splits(_compile(net, tmp_path, fixed=True), OSM, net.corridor_edges)
        assert len(findings) == 8
        assert split_defects(findings) == []
        by_exit = _by_exit(findings)
        assert by_exit["18207912"].exit_from_lanes == (0, 1)
        assert by_exit["82150350"].exit_from_lanes == (0,)
        assert by_exit["82150350"].option_lanes == (0,)  # exit and through
        assert by_exit["82150350"].compiled_lanes == 3 and not by_exit["82150350"].added_lane

    def test_the_generated_patch_is_the_committed_one(self, tmp_path: Path) -> None:
        net = _mndot_network()
        findings = audit_splits(_compile(net, tmp_path, fixed=False), OSM, net.corridor_edges)
        mounds = _by_exit(findings)["18207912"]
        committed = [
            line for line in COMMITTED_PATCH.read_text().splitlines() if "<connection " in line
        ]
        assert connection_patch_lines(mounds) == committed
        # only the wrong_side finding is restated: the added lane is undone by
        # --ramps.unset, not by a connection list
        xml = split_patch_xml(findings)
        assert [line for line in xml.splitlines() if "<connection " in line] == committed
        assert (
            xml.startswith("<!--") and "<connections>" in xml and xml.endswith("</connections>\n")
        )
        assert "1001426896" not in xml

    def test_the_table_names_both_defects_and_their_remedies(self, tmp_path: Path) -> None:
        net = _mndot_network()
        findings = audit_splits(_compile(net, tmp_path, fixed=False), OSM, net.corridor_edges)
        text = "\n".join(format_split_table(findings))
        assert text.startswith("  splits (8 exits audited against the extract; 2 defects)")
        assert (
            "45608485 -> 18207912  OSM right (8-9 m; turn:lanes |||slight_right|slight_right)"
            in text
        )
        assert "compiled leftmost lane(s) 3,4 of 5  WRONG_SIDE" in text
        assert "1001426896 -> 82150350  OSM right (1-45 m;" in text
        assert "lane(s) 3 of 4 (OSM lanes=3: a lane was added)  ADDED_LANE_WRONG_SIDE" in text
        assert "fix: restate the split with an OSMNetwork.patch_files connection patch" in text
        assert "fix: the lane feeding the exit was added by ramp guessing" in text
        assert "42165869  OSM left (" in text and text.count("OK") == 6


class TestSideComputationOnTheFixture:
    """A mainline heading east: the right exit is south of it, the left exit north."""

    def test_offsets_and_sides_from_the_extract_alone(self) -> None:
        graph = parse_osm(FIXTURE)
        right_side, right_offsets = osm_exit_side(graph, "300", "400")
        left_side, left_offsets = osm_exit_side(graph, "302", "401")
        assert right_side == "right" and left_side == "left"
        # 0.0006 deg of latitude at the first link node = 66 m, signed
        assert right_offsets == (-66.3,) and left_offsets == (66.3,)
        # the continuing way may be named or found by walking the extract
        assert osm_exit_side(graph, "300", "400", continuing_way_id="301") == ("right", (-66.3,))
        assert osm_exit_side(graph, "302", "401", split_node="4") == ("left", (66.3,))

    def test_unknown_when_the_extract_lacks_the_link(self) -> None:
        graph = parse_osm(FIXTURE)
        assert osm_exit_side(graph, "300", "999") == ("unknown", ())

    @pytest.mark.parametrize("extra", [(), ("--ramps.guess",)])
    def test_the_compiled_fixture_puts_both_exits_on_their_side(
        self, tmp_path: Path, extra: tuple[str, ...]
    ) -> None:
        chain = ("300", "301", "302", "303")
        bundle = osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path,
            keep_edges=("400", "401"),
            netconvert_extra=extra,
        )
        findings = audit_splits(bundle.net_path, FIXTURE, chain)
        assert [(f.exit_edge, f.osm_side, f.compiled_side, f.verdict) for f in findings] == [
            ("400", "right", "rightmost", "ok"),
            ("401", "left", "leftmost", "ok"),
        ]
        right, left = findings
        assert right.turn_lanes_side == "right" and left.turn_lanes is None
        assert all(f.added_lane == bool(extra) for f in findings)
        # a left exit's patch puts the exit on the LEFTMOST lane and the rest
        # continue in order
        assert connection_patch_lines(left)[-1].endswith(
            f'to="401" fromLane="{left.compiled_lanes - 1}" toLane="0"/>'
        )
        assert connection_patch_lines(right)[0].endswith('to="400" fromLane="0" toLane="0"/>')


class TestReadings:
    def test_turn_lanes(self) -> None:
        assert read_turn_lanes(None) is None
        right = read_turn_lanes("none|none|through;slight_right")
        assert right is not None and (right.side, right.n_exit, right.n_option) == ("right", 1, 1)
        two = read_turn_lanes("|||slight_right|slight_right")
        assert two is not None and (two.side, two.n_exit, two.n_option) == ("right", 2, 0)
        left = read_turn_lanes("slight_left;through|none|none")
        assert left is not None and (left.side, left.n_exit, left.n_option) == ("left", 1, 1)
        both = read_turn_lanes("left|through|right")
        assert both is not None and both.side == "unknown"
        none = read_turn_lanes("through|through")
        assert none is not None and none.side == "unknown"

    def test_compiled_side(self) -> None:
        assert compiled_side([0], 3) == "rightmost"
        assert compiled_side([0, 1], 5) == "rightmost"
        assert compiled_side([2], 3) == "leftmost"
        assert compiled_side([3, 4], 5) == "leftmost"
        assert compiled_side([1], 3) == "middle"
        assert compiled_side([0, 1, 2], 3) == "all"
        assert compiled_side([0], 1) == "all"

    def test_side_of_offsets(self) -> None:
        assert side_of_offsets(()) == "unknown"
        assert side_of_offsets((0.3, -0.2)) == "unknown"  # inside the road's own width
        assert side_of_offsets((-2.0, -9.5)) == "right"
        assert side_of_offsets((0.5, 4.0)) == "left"


def _load_cli() -> ModuleType:
    """Import ``scripts/onboard_corridor.py`` by path (``scripts/`` is not a package)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "onboard_corridor.py"
    spec = importlib.util.spec_from_file_location("_onboard_corridor_split_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@mndot_files
class TestOnboardingPath:
    """``corridor_from_bbox`` on the committed extract, then the fixes, then the CLI."""

    def test_the_build_audits_and_the_fixes_leave_no_defect(self, tmp_path: Path) -> None:
        build = corridor_from_bbox(
            "split_probe",
            MNDOT_BBOX,
            MNDOT_BEARING,
            inflow=1.0,
            workdir=tmp_path / "work",
            osm_file=OSM,
            duration_s=60.0,
            netconvert_extra=MNDOT_EXTRA,
            max_chain_m=MNDOT_CHAIN_CAP_M,
        )
        assert [(d.from_edge, d.verdict) for d in build.split_defects()] == [
            ("45608485", "wrong_side"),
            ("1001426896", "added_lane_wrong_side"),
        ]
        assert "  splits (8 exits audited against the extract; 2 defects)" in build.summary()
        with pytest.raises(ValueError, match="connection patch"):
            apply_split_fixes(build, None)

        patch = tmp_path / "split_probe.splits.con.xml"
        fixed = apply_split_fixes(build, patch)
        assert fixed.split_defects() == [] and len(fixed.split_audit) == 8
        network = fixed.config.network
        assert isinstance(network, OSMNetwork)
        assert network.patch_files == [str(patch)]  # outside the repository: absolute
        assert network.netconvert_extra == [*MNDOT_EXTRA, "--ramps.unset", "1001426896"]
        committed = [
            line for line in COMMITTED_PATCH.read_text().splitlines() if "<connection " in line
        ]
        assert [
            line for line in patch.read_text().splitlines() if "<connection " in line
        ] == committed
        # the fixed build states the re-imported network: 1001426896 is back to
        # three lanes and the audit ran on that file
        assert fixed.net_path == build.net_path
        assert fixed.lanes_profile[-1][2] == 3 and fixed.lanes_profile != build.lanes_profile
        # a build without defects is returned as is
        assert apply_split_fixes(fixed, patch) is fixed

    def test_the_cli_flags(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cli = _load_cli()
        out_yaml = tmp_path / "split_cli.yaml"
        argv = [
            "--name",
            "split_cli",
            "--bbox",
            *[f"{v:.4f}" for v in MNDOT_BBOX],
            "--bearing",
            f"{MNDOT_BEARING:g}",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            str(out_yaml),
            "--osm-file",
            str(OSM),
            "--duration-s",
            "60",
            "--netconvert-extra",
            " ".join(MNDOT_EXTRA),
            "--max-chain-m",
            f"{MNDOT_CHAIN_CAP_M:g}",
        ]
        # reported, not enforced
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert "splits (8 exits audited against the extract; 2 defects)" in out
        assert "WRONG_SIDE" in out and "ADDED_LANE_WRONG_SIDE" in out
        assert "netconvert_extra" not in yaml.safe_load(out_yaml.read_text())["network"] or (
            "--ramps.unset"
            not in yaml.safe_load(out_yaml.read_text())["network"]["netconvert_extra"]
        )

        assert cli.main([*argv, "--fail-on-split-defect"]) == cli.SPLIT_DEFECT_EXIT == 4
        out = capsys.readouterr().out
        assert "FAIL: 2 exit(s) compiled on the wrong side of the mainline" in out
        assert "45608485 -> 18207912 (wrong_side, OSM right, compiled leftmost)" in out

        patch = tmp_path / "split_cli.splits.con.xml"
        code = cli.main([*argv, "--fail-on-split-defect", "--write-split-patch", str(patch)])
        out = capsys.readouterr().out
        assert code == 0, out
        assert "network re-imported and audited again:" in out
        assert "splits (8 exits audited against the extract; 0 defects)" in out
        assert patch.is_file()
        network = yaml.safe_load(out_yaml.read_text())["network"]
        assert network["patch_files"] == [str(patch)]
        assert network["netconvert_extra"] == [*MNDOT_EXTRA, "--ramps.unset", "1001426896"]
