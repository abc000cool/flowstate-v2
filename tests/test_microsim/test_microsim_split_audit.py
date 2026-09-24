"""The split audit (``microsim.split_audit``, docs/ONBOARDING_MNDOT.md §9).

On the committed I-94 WB St. Paul extract netconvert compiled two right-hand
exits from the leftmost lanes — the 12th Street exit from a lane
``--ramps.guess`` added on the left of ``1001426896``, the Mounds/Kellogg exit
from lanes 3–4 of the five on ``45608485`` — and through traffic trapped there
locked the corridor. The audit compares the side OSM draws every exit on with
the lanes the compiled network feeds it from; these tests pin it on that
extract compiled without and with the committed fixes, on a synthetic
fixture with one right and one left exit, and through ``corridor_from_bbox``
and the onboarding CLI under their defaults (since 2026-09-24: ramp guessing
on, the fixes applied and the network re-audited; ``--no-ramp-guessing`` /
``--no-split-fixes`` opt out, ``--fail-on-split-defect`` judges the final
audit, ``--write-split-patch`` chooses where the patch goes).

The MnDOT extract is copied into ``tmp_path`` before every build whose
defaults would write the patch beside it: the committed
``data/osm/mndot_i94_wb_stpaul.splits.con.xml`` is never rewritten by a test.

``netconvert`` runs on the small committed extracts (well under a second
each); no SUMO simulation is started.
"""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
import sumolib
import yaml

from flowstate_core.config import OSMNetwork, ScenarioConfig
from microsim.networks import osm_import
from microsim.scenarios import (
    RAMP_GUESSING_OPTIONS,
    CorridorBuild,
    apply_split_fixes,
    corridor_from_bbox,
    default_split_patch_path,
    with_ramp_guessing,
)
from microsim.split_audit import (
    OSMGraph,
    OSMWay,
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


def _extract_copy(tmp_path: Path) -> Path:
    """The MnDOT extract under ``tmp_path``, so the default patch lands there."""
    copy = tmp_path / "osm" / OSM.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OSM, copy)
    return copy


class TestRampGuessingOptions:
    """``with_ramp_guessing``: the defaults added once, never twice, or not at all."""

    def test_the_defaults_are_the_mndot_values(self) -> None:
        assert with_ramp_guessing(()) == MNDOT_EXTRA
        assert tuple(o for o, _ in RAMP_GUESSING_OPTIONS) == (
            "--ramps.guess",
            "--ramps.ramp-length",
        )

    def test_options_already_given_are_not_duplicated(self) -> None:
        assert with_ramp_guessing(MNDOT_EXTRA) == MNDOT_EXTRA
        # the caller's own ramp length wins; only the missing flag is added
        assert with_ramp_guessing(("--ramps.ramp-length", "300", "--ramps.no-split")) == (
            "--ramps.guess",
            "--ramps.ramp-length",
            "300",
            "--ramps.no-split",
        )
        assert with_ramp_guessing(("--ramps.guess=true",)) == (
            "--ramps.ramp-length",
            "250",
            "--ramps.guess=true",
        )

    def test_disabled_keeps_the_options_verbatim(self) -> None:
        assert with_ramp_guessing((), enabled=False) == ()
        assert with_ramp_guessing(("--ramps.no-split",), enabled=False) == ("--ramps.no-split",)

    def test_the_patch_goes_beside_the_extract(self) -> None:
        assert default_split_patch_path(Path("data/osm/x.osm")) == Path("data/osm/x.splits.con.xml")
        assert default_split_patch_path("w/net/extract.osm") == Path("w/net/extract.splits.con.xml")


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
        # a left exit's patch puts the exit on the LEFTMOST lane of the
        # load-time edge (a guessed piece has one lane more than the way it
        # was split from) and the rest continue in order
        assert left.load_time_lanes == 3 and left.is_ramp_split_piece == bool(extra)
        assert connection_patch_lines(left)[-1].endswith('to="401" fromLane="2" toLane="0"/>')
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
        # split_fixes=False: the audit as compiled; the caller's own MNDOT_EXTRA
        # already carries the guessing defaults, which must not be added twice
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
            split_fixes=False,
        )
        assert build.config.network.netconvert_extra == list(MNDOT_EXTRA)
        assert [(d.from_edge, d.verdict) for d in build.split_defects()] == [
            ("45608485", "wrong_side"),
            ("1001426896", "added_lane_wrong_side"),
        ]
        assert build.split_audit_before_fixes is None and build.split_fixes_applied == 0
        assert build.applied_line() == "ramp guessing on; split fixes: off, 2 remaining"
        assert "  splits (8 exits audited against the extract; 2 defects)" in build.summary()
        assert "splits before fixes" not in build.summary()
        with pytest.raises(ValueError, match="connection patch"):
            apply_split_fixes(build, None)

        patch = tmp_path / "split_probe.splits.con.xml"
        fixed = apply_split_fixes(build, patch)
        assert fixed.split_defects() == [] and len(fixed.split_audit) == 8
        assert fixed.split_audit_before_fixes == build.split_audit
        assert fixed.split_fixes_applied == 2 and fixed.split_patch_file == patch
        assert fixed.applied_line() == "ramp guessing on; split fixes: 2 applied, 0 remaining"
        assert "  splits before fixes (8 exits audited against the extract; 2 defects)" in (
            fixed.summary()
        )
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

    def test_the_defaults_guess_ramps_and_apply_the_fixes(self, tmp_path: Path) -> None:
        """No options at all: ramp guessing on, both defects fixed, the patch
        beside the extract, both audits kept, the YAML naming the fixes."""
        extract = _extract_copy(tmp_path)
        build = corridor_from_bbox(
            "split_default",
            MNDOT_BBOX,
            MNDOT_BEARING,
            inflow=1.0,
            workdir=tmp_path / "work",
            osm_file=extract,
            duration_s=60.0,
            max_chain_m=MNDOT_CHAIN_CAP_M,
        )
        patch = tmp_path / "osm" / "mndot_i94_wb_stpaul.splits.con.xml"
        assert build.ramp_guessing and build.split_fixes
        assert build.split_fixes_applied == 2 and build.split_defects() == []
        assert build.split_patch_file == patch and patch.is_file()
        assert build.split_audit_before_fixes is not None
        assert [
            (d.from_edge, d.verdict) for d in split_defects(build.split_audit_before_fixes)
        ] == [
            ("45608485", "wrong_side"),
            ("1001426896", "added_lane_wrong_side"),
        ]
        assert len(build.split_audit) == 8
        network = build.config.network
        assert isinstance(network, OSMNetwork)
        assert network.netconvert_extra == [*MNDOT_EXTRA, "--ramps.unset", "1001426896"]
        assert network.patch_files == [str(patch)]
        committed = [
            line for line in COMMITTED_PATCH.read_text().splitlines() if "<connection " in line
        ]
        assert [
            line for line in patch.read_text().splitlines() if "<connection " in line
        ] == committed
        assert COMMITTED_PATCH.read_text().count("<connection ") == len(committed)  # untouched
        text = build.summary()
        assert "  splits before fixes (8 exits audited against the extract; 2 defects)" in text
        assert "  splits (8 exits audited against the extract; 0 defects)" in text
        assert "  applied   ramp guessing on; split fixes: 2 applied, 0 remaining" in text
        assert f"patch {patch}" in text and "--ramps.unset 1001426896" in text
        out_yaml = tmp_path / "split_default.yaml"
        build.to_yaml(out_yaml)
        dumped = yaml.safe_load(out_yaml.read_text())["network"]
        assert dumped["patch_files"] == [str(patch)]
        assert dumped["netconvert_extra"] == [*MNDOT_EXTRA, "--ramps.unset", "1001426896"]
        # the recorded scenario reloads and compiles the fixed network
        assert ScenarioConfig.from_yaml(out_yaml) == build.config

    def test_both_opt_outs(self, tmp_path: Path) -> None:
        """Without guessing only the Mounds/Kellogg defect exists (the 12th
        Street lane is one guessing adds); without fixes it stays, labelled."""
        extract = _extract_copy(tmp_path)
        build = corridor_from_bbox(
            "split_raw",
            MNDOT_BBOX,
            MNDOT_BEARING,
            inflow=1.0,
            workdir=tmp_path / "work",
            osm_file=extract,
            duration_s=60.0,
            max_chain_m=MNDOT_CHAIN_CAP_M,
            ramp_guessing=False,
            split_fixes=False,
        )
        assert not build.ramp_guessing and not build.split_fixes
        assert build.config.network.netconvert_extra == []
        assert build.config.network.patch_files == []
        assert [(d.from_edge, d.verdict) for d in build.split_defects()] == [
            ("45608485", "wrong_side")
        ]
        assert build.split_audit_before_fixes is None and build.split_patch_file is None
        assert build.applied_line() == "ramp guessing off; split fixes: off, 1 remaining"
        assert not list((tmp_path / "osm").glob("*.splits.con.xml"))
        # 12th Street compiles as the three lanes OSM tags; no guessed fourth
        assert build.lanes_profile[-1][2] == 3

    def test_the_cli_flags(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cli = _load_cli()
        extract = _extract_copy(tmp_path)
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
            str(extract),
            "--duration-s",
            "60",
            "--max-chain-m",
            f"{MNDOT_CHAIN_CAP_M:g}",
        ]
        default_patch = tmp_path / "osm" / "mndot_i94_wb_stpaul.splits.con.xml"
        fixed_extra = [*MNDOT_EXTRA, "--ramps.unset", "1001426896"]

        # the defaults: guessing on, fixes applied, the failure flag has nothing left
        assert cli.main([*argv, "--fail-on-split-defect"]) == 0
        out = capsys.readouterr().out
        assert "splits before fixes (8 exits audited against the extract; 2 defects)" in out
        assert "WRONG_SIDE" in out and "ADDED_LANE_WRONG_SIDE" in out
        assert "splits (8 exits audited against the extract; 0 defects)" in out
        assert "applied   ramp guessing on; split fixes: 2 applied, 0 remaining" in out
        assert "network re-imported and audited again" in out and "FAIL" not in out
        assert default_patch.is_file()
        network = yaml.safe_load(out_yaml.read_text())["network"]
        assert network["patch_files"] == [str(default_patch)]
        assert network["netconvert_extra"] == fixed_extra

        # the caller's own options are kept and not duplicated; the patch goes
        # where --write-split-patch says
        default_patch.unlink()
        patch = tmp_path / "elsewhere" / "split_cli.splits.con.xml"
        code = cli.main(
            [*argv, "--netconvert-extra", " ".join(MNDOT_EXTRA), "--write-split-patch", str(patch)]
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert patch.is_file() and not default_patch.exists()
        network = yaml.safe_load(out_yaml.read_text())["network"]
        assert network["patch_files"] == [str(patch)]
        assert network["netconvert_extra"] == fixed_extra

        # --no-split-fixes: reported, not fixed; the failure flag then fires
        assert cli.main([*argv, "--no-split-fixes"]) == 0
        out = capsys.readouterr().out
        assert "applied   ramp guessing on; split fixes: off, 2 remaining" in out
        assert "splits before fixes" not in out
        network = yaml.safe_load(out_yaml.read_text())["network"]
        assert network["netconvert_extra"] == list(MNDOT_EXTRA)
        assert network["patch_files"] == []
        assert cli.main([*argv, "--no-split-fixes", "--fail-on-split-defect"]) == (
            cli.SPLIT_DEFECT_EXIT
        )
        out = capsys.readouterr().out
        assert "FAIL: 2 exit(s) compiled on the wrong side of the mainline" in out
        assert "45608485 -> 18207912 (wrong_side, OSM right, compiled leftmost)" in out

        # --no-ramp-guessing: the map as drawn; the one defect that is not
        # guessing's is still fixed
        assert cli.main([*argv, "--no-ramp-guessing"]) == 0
        out = capsys.readouterr().out
        assert "applied   ramp guessing off; split fixes: 1 applied, 0 remaining" in out
        network = yaml.safe_load(out_yaml.read_text())["network"]
        assert network["netconvert_extra"] == []
        assert network["patch_files"] == [str(default_patch)]

        # self-cancelling flags are refused before anything is built
        code = cli.main([*argv, "--no-split-fixes", "--write-split-patch", str(patch)])
        out = capsys.readouterr().out
        assert code == cli.BAD_USAGE_EXIT == 2
        assert "--write-split-patch" in out and "--no-split-fixes" in out
        assert "scenario" not in out


def _fixture_variant(tmp_path: Path, name: str, *edits: tuple[str, str]) -> Path:
    """The synthetic fixture with string edits applied, written under ``tmp_path``."""
    text = FIXTURE.read_text()
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new)
    out = tmp_path / name
    out.write_text(text)
    return out


def _both_exits_at_node_2(tmp_path: Path) -> Path:
    """The fixture with its left exit moved to node 2, beside the right one."""
    return _fixture_variant(
        tmp_path,
        "both.osm",
        ('<nd ref="4"/><nd ref="30"/><nd ref="31"/>', '<nd ref="2"/><nd ref="32"/><nd ref="33"/>'),
        (
            '  <node id="31" lat="40.0015" lon="-95.9740"/>',
            '  <node id="31" lat="40.0015" lon="-95.9740"/>\n'
            '  <node id="32" lat="40.0006" lon="-95.9900"/>\n'
            '  <node id="33" lat="40.0015" lon="-95.9860"/>',
        ),
    )


def _connections(net_path: Path, from_edge: str) -> dict[str, list[tuple[int, int]]]:
    net = sumolib.net.readNet(str(net_path))
    return {
        to.getID(): sorted((c.getFromLane().getIndex(), c.getToLane().getIndex()) for c in cs)
        for to, cs in net.getEdge(from_edge).getOutgoing().items()
    }


def _synthetic_graph(bearing_deg: float, *, fork_first: bool) -> OSMGraph:
    """A mainline of three nodes along ``bearing_deg`` with a right link and a
    left link leaving the middle node; with ``fork_first`` a second MOTORWAY
    (not a link) leaves the node to the right and is listed before the
    continuing way in the extract."""
    lat0, lon0 = 40.0, -96.0
    m_lat = 110_574.0
    m_lon = 111_320.0 * math.cos(math.radians(lat0))
    th = math.radians(bearing_deg)
    fwd, right = (math.sin(th), math.cos(th)), (math.cos(th), -math.sin(th))

    def node(along: float, lateral_right: float) -> tuple[float, float]:
        x = along * fwd[0] + lateral_right * right[0]
        y = along * fwd[1] + lateral_right * right[1]
        return (lat0 + y / m_lat, lon0 + x / m_lon)

    nodes = {
        "1": node(0, 0),
        "2": node(500, 0),
        "3": node(1000, 0),
        "4": node(1500, 0),
        "20": node(600, 30),
        "21": node(800, 80),
        "30": node(600, -30),
        "31": node(800, -80),
        "40": node(600, 30),
        "41": node(800, 120),
    }
    motorway = {"highway": "motorway", "oneway": "yes", "lanes": "3"}
    link = {"highway": "motorway_link", "oneway": "yes", "lanes": "1"}
    ways: dict[str, OSMWay] = {}
    if fork_first:
        ways["299"] = OSMWay("299", ("2", "40", "41"), {**motorway, "lanes": "2"})
    ways["300"] = OSMWay("300", ("1", "2"), motorway)
    ways["301"] = OSMWay("301", ("2", "3", "4"), motorway)
    ways["400"] = OSMWay("400", ("2", "20", "21"), link)
    ways["401"] = OSMWay("401", ("2", "30", "31"), link)
    return OSMGraph(nodes=nodes, ways=ways)


class TestReviewFindings:
    """The 2026-09-24 adversarial review of the audit and the fixes."""

    @pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315, 179, 181])
    def test_the_sign_convention_holds_in_every_heading(self, bearing: float) -> None:
        graph = _synthetic_graph(bearing, fork_first=False)
        assert osm_exit_side(graph, "300", "400") == ("right", (-30.0, -80.0))
        assert osm_exit_side(graph, "300", "401") == ("left", (30.0, 80.0))

    @pytest.mark.parametrize("bearing", [90, 270])
    def test_a_fork_listed_first_is_not_taken_for_the_mainline(self, bearing: float) -> None:
        """Continuing way unknown (the corridor ends at the node): the
        straightest same-class way is the mainline, not the first in file
        order — which was the fork branch, and flipped the right link to left."""
        graph = _synthetic_graph(bearing, fork_first=True)
        assert osm_exit_side(graph, "300", "400") == ("right", (-30.0, -80.0))
        assert osm_exit_side(graph, "300", "401") == ("left", (30.0, 80.0))
        # the fork branch itself audits as a right exit against the mainline
        assert osm_exit_side(graph, "300", "299") == ("right", (-30.0, -120.0))
        # and a named continuing way is still taken as given
        assert osm_exit_side(graph, "300", "400", continuing_way_id="301")[0] == "right"

    def test_the_patch_comment_never_carries_a_double_hyphen(self, tmp_path: Path) -> None:
        """netconvert refuses an XML comment containing ``--``; a scenario
        name is free text and reaches the comment through ``note``."""
        chain = ("300", "301", "302", "303")
        bundle = osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path / "plain",
            keep_edges=("400", "401"),
        )
        right, _left = audit_splits(bundle.net_path, FIXTURE, chain)
        forced = replace(right, verdict="wrong_side")
        xml = split_patch_xml([forced], note="scenario i94--wb --> x")
        assert xml.count("--") == 2  # the comment's own <!-- and -->
        assert "i94-wb" in xml and ">" not in xml.split("-->")[0][4:]
        patch = tmp_path / "note.con.xml"
        patch.write_text(xml)
        osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path / "noted",
            keep_edges=("400", "401"),
            patch_files=[patch],
        )

    def test_an_untagged_split_that_keeps_its_lanes_is_an_option_lane(self, tmp_path: Path) -> None:
        """Three lanes into three with a one-lane exit and no ``turn:lanes``:
        the exit lane also continues, as netconvert itself compiles it; a
        tagged exit-only lane (``none|none|slight_right``) stays exit-only."""
        chain = ("300", "301", "302", "303")
        bundle = osm_import(
            osm_file=FIXTURE, corridor_edges=chain, workdir=tmp_path, keep_edges=("400", "401")
        )
        right, left = audit_splits(bundle.net_path, FIXTURE, chain)
        assert connection_patch_lines(left) == [
            '  <connection from="302" to="303" fromLane="0" toLane="0"/>',
            '  <connection from="302" to="303" fromLane="1" toLane="1"/>',
            '  <connection from="302" to="303" fromLane="2" toLane="2"/>',
            '  <connection from="302" to="401" fromLane="2" toLane="0"/>',
        ]
        assert connection_patch_lines(right) == [
            '  <connection from="300" to="400" fromLane="0" toLane="0"/>',
            '  <connection from="300" to="301" fromLane="1" toLane="0"/>',
            '  <connection from="300" to="301" fromLane="2" toLane="1"/>',
        ]

    def test_a_restated_split_keeps_the_other_exit_of_the_same_edge(self, tmp_path: Path) -> None:
        """A right and a left exit at one node: restating the right one alone
        made netconvert drop every computed connection of the edge, so the
        left exit lost its way in (found by the review); the patch now repeats
        the sibling as compiled, and both survive the compile."""
        osm = _both_exits_at_node_2(tmp_path)
        chain = ("300", "301", "302", "303")
        bundle = osm_import(
            osm_file=osm,
            corridor_edges=chain,
            workdir=tmp_path / "plain",
            keep_edges=("400", "401"),
        )
        assert _connections(bundle.net_path, "300") == {
            "400": [(0, 0)],
            "301": [(0, 0), (1, 1), (2, 2)],
            "401": [(2, 0)],
        }
        findings = audit_splits(bundle.net_path, osm, chain)
        assert [(f.exit_edge, f.verdict, f.exit_connections) for f in findings] == [
            ("400", "ok", ((0, 0),)),
            ("401", "ok", ((2, 0),)),
        ]
        right, left = findings
        xml = split_patch_xml([replace(right, verdict="wrong_side"), left])
        assert '<connection from="300" to="401" fromLane="2" toLane="0"/>' in xml
        assert xml.count("<connection ") == 4
        patch = tmp_path / "both.con.xml"
        patch.write_text(xml)
        patched = osm_import(
            osm_file=osm,
            corridor_edges=chain,
            workdir=tmp_path / "patched",
            keep_edges=("400", "401"),
            patch_files=[patch],
        )
        assert _connections(patched.net_path, "300") == {
            "400": [(0, 0)],
            "301": [(1, 0), (2, 1)],
            "401": [(2, 0)],
        }
        assert [(f.exit_edge, f.verdict) for f in audit_splits(patched.net_path, osm, chain)] == [
            ("400", "ok"),
            ("401", "ok"),
        ]

    def test_a_wrong_side_piece_is_patched_at_load_time_and_unset(self, tmp_path: Path) -> None:
        """A ``wrong_side`` finding on a ``-AddedOffRampEdge`` piece (possible
        when the way has no ``lanes`` tag): the piece has the guessed lane on
        top of the load-time edge, so a patch with its lane count named a lane
        the edge does not have at load time and netconvert refused it
        ("Could not insert connection ... after build"); with the load-time
        count alone, guessing rebuilt the piece over the patch and moved the
        exit again. The patch uses the load-time count and the edge goes into
        ``--ramps.unset``; compiled together the exit sits where drawn."""
        chain = ("300", "301", "302", "303")
        guessed = osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path / "guess",
            keep_edges=("400", "401"),
            netconvert_extra=("--ramps.guess",),
        )
        right, left = audit_splits(guessed.net_path, FIXTURE, chain)
        assert left.from_edge == "302-AddedOffRampEdge" and left.compiled_lanes == 4
        forced = replace(left, verdict="wrong_side")
        assert ramps_unset_edges([forced, right]) == ["302"]
        lines = connection_patch_lines(forced)
        assert lines[-1] == '  <connection from="302" to="401" fromLane="2" toLane="0"/>'
        assert all('from="302"' in line for line in lines) and len(lines) == 4
        patch = tmp_path / "piece.con.xml"
        patch.write_text(split_patch_xml([forced, right]))
        # the patch alone: guessing rebuilds the piece over it and the exit
        # lands on the piece's lane 2 of 4 — still a defect
        alone = osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path / "patch_only",
            keep_edges=("400", "401"),
            patch_files=[patch],
            netconvert_extra=("--ramps.guess",),
        )
        after = audit_splits(alone.net_path, FIXTURE, chain)
        assert (after[1].from_edge, after[1].exit_from_lanes, after[1].verdict) == (
            "302-AddedOffRampEdge",
            (2,),
            "added_lane_wrong_side",
        )
        fixed = osm_import(
            osm_file=FIXTURE,
            corridor_edges=chain,
            workdir=tmp_path / "fixed",
            keep_edges=("400", "401"),
            patch_files=[patch],
            netconvert_extra=("--ramps.guess", "--ramps.unset", "302"),
        )
        assert [
            (f.from_edge, f.exit_edge, f.compiled_lanes, f.exit_from_lanes, f.verdict)
            for f in audit_splits(fixed.net_path, FIXTURE, chain)
        ] == [
            ("300-AddedOffRampEdge", "400", 4, (0,), "ok"),
            ("302", "401", 3, (2,), "ok"),
        ]

    def test_connection_patch_lines_on_one_lane_severs_nothing(self) -> None:
        """Not reachable from the audit (a one-lane edge feeding its exit is
        ``all``, never a defect) but a public function: the single lane both
        exits and continues rather than leaving the mainline unconnected."""
        finding = SplitFinding(
            from_edge="a",
            exit_edge="x",
            continuing_edge="b",
            x_m=0.0,
            osm_way="a",
            osm_lanes=1,
            turn_lanes=None,
            turn_lanes_side="unknown",
            osm_side="right",
            osm_offsets_m=(-5.0,),
            compiled_lanes=1,
            exit_from_lanes=(0,),
            compiled_side="all",
            option_lanes=(),
            added_lane=False,
            exit_lanes=1,
            continuing_lanes=1,
            verdict="ok",
            remedy="",
        )
        assert connection_patch_lines(finding) == [
            '  <connection from="a" to="x" fromLane="0" toLane="0"/>',
            '  <connection from="a" to="b" fromLane="0" toLane="0"/>',
        ]


@mndot_files
class TestReviewFindingsOnTheCommittedExtract:
    def test_a_callers_ramps_unset_in_equals_form_is_merged_not_repeated(
        self, tmp_path: Path
    ) -> None:
        """netconvert refuses a second ``--ramps.unset`` ("a value for the
        option 'ramps.unset' was already set"); the fixes merge into the
        caller's list whichever form it came in."""
        extract = _extract_copy(tmp_path)
        build = corridor_from_bbox(
            "split_eq",
            MNDOT_BBOX,
            MNDOT_BEARING,
            inflow=1.0,
            workdir=tmp_path / "work",
            osm_file=extract,
            duration_s=60.0,
            netconvert_extra=("--ramps.unset=45608485",),
            max_chain_m=MNDOT_CHAIN_CAP_M,
        )
        network = build.config.network
        assert isinstance(network, OSMNetwork)
        assert network.netconvert_extra == [*MNDOT_EXTRA, "--ramps.unset=45608485,1001426896"]
        assert build.split_fixes_applied == 2 and build.split_defects() == []

    def test_two_corridors_from_one_extract_do_not_overwrite_each_others_patch(
        self, tmp_path: Path
    ) -> None:
        """The default patch path is named after the extract, so a second
        corridor onboarded from the same ``--osm-file`` would have replaced
        the first one's patch and silently changed what the first scenario
        compiles. A patch already there that states other connections is left
        alone and the new one is named by corridor; one stating the same
        connections is shared."""
        extract = _extract_copy(tmp_path)
        default = tmp_path / "osm" / "mndot_i94_wb_stpaul.splits.con.xml"
        foreign = (
            "<!-- corridor A -->\n<connections>\n"
            '  <connection from="1" to="2" fromLane="0" toLane="0"/>\n'
            "</connections>\n"
        )
        default.write_text(foreign)

        def _build(name: str) -> CorridorBuild:
            return corridor_from_bbox(
                name,
                MNDOT_BBOX,
                MNDOT_BEARING,
                inflow=1.0,
                workdir=tmp_path / "work" / name,
                osm_file=extract,
                duration_s=60.0,
                max_chain_m=MNDOT_CHAIN_CAP_M,
            )

        build_b = _build("split_b")
        by_name = tmp_path / "osm" / "mndot_i94_wb_stpaul.split_b.splits.con.xml"
        assert default.read_text() == foreign
        assert build_b.split_patch_file == by_name and by_name.is_file()
        network = build_b.config.network
        assert isinstance(network, OSMNetwork)
        assert network.patch_files == [str(by_name)] and build_b.split_defects() == []

        # the same connections already there: shared, not renamed
        default.unlink()
        shutil.copyfile(by_name, default)
        build_c = _build("split_c")
        assert build_c.split_patch_file == default
        assert build_c.config.network.patch_files == [str(default)]
        assert not (tmp_path / "osm" / "mndot_i94_wb_stpaul.split_c.splits.con.xml").exists()
