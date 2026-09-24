"""``scripts/onboard_corridor.py``: the flags, and re-onboarding over a file.

The defaults of 2026-09-24 (ramp guessing on, split fixes on) and their
opt-outs as the parser sees them, plus the self-cancelling pair that is
refused before Overpass or netconvert is spent on it. What the flags do to a
real build is pinned on the MnDOT extract in
``tests/test_microsim/test_microsim_split_audit.py``.

``TestReonboardingKeepsTheFleet`` (block 3 of the same day) builds the
synthetic ``tests/fixtures/splits.osm`` corridor — ``netconvert`` only, no
simulation — and pins that a re-run over an existing scenario keeps its
fleet, sim, seed, replicates, ``fd_calibration`` and ``macro`` blocks
byte-for-byte, that ``--fresh-fleet`` resets them, and that a file that does
not parse is refused with exit 2 before anything is built.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from flowstate_core.config import ScenarioConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cli() -> ModuleType:
    """Import ``scripts/onboard_corridor.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "onboard_corridor.py"
    spec = importlib.util.spec_from_file_location("_onboard_corridor_flags", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _argv(*extra: str) -> list[str]:
    return [
        "--name",
        "x",
        "--bbox",
        "1",
        "2",
        "3",
        "4",
        "--bearing",
        "90",
        "--workdir",
        "w",
        "--out",
        "x.yaml",
        *extra,
    ]


class TestFlags:
    def test_the_defaults_are_guessing_on_and_fixes_on(self) -> None:
        cli = _load_cli()
        args = cli.parse_args(_argv())
        assert args.no_ramp_guessing is False and args.no_split_fixes is False
        assert args.write_split_patch is None and args.fail_on_split_defect is False
        assert args.netconvert_extra == ""

    def test_the_opt_outs_parse(self) -> None:
        cli = _load_cli()
        args = cli.parse_args(_argv("--no-ramp-guessing", "--no-split-fixes"))
        assert args.no_ramp_guessing is True and args.no_split_fixes is True
        args = cli.parse_args(_argv("--write-split-patch", "p.con.xml"))
        assert args.write_split_patch == Path("p.con.xml")

    def test_no_fixes_with_a_patch_path_is_refused_before_any_build(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        code = cli.main(_argv("--no-split-fixes", "--write-split-patch", "p.con.xml"))
        assert code == cli.BAD_USAGE_EXIT == 2
        out = capsys.readouterr().out
        assert out.strip() == cli.NO_FIXES_PATCH_MESSAGE
        assert "scenario" not in out and not Path("w").exists()

    def test_the_help_names_the_defaults(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli = _load_cli()
        with pytest.raises(SystemExit):
            cli.parse_args(["--help"])
        out = capsys.readouterr().out
        assert "--no-ramp-guessing" in out and "--no-split-fixes" in out
        assert "--ramps.guess --ramps.ramp-length 250" in out


#: The split-audit fixture: an eastbound three-lane mainline with two exits,
#: a corridor ``netconvert`` compiles in well under a second (no simulation).
SPLITS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "splits.osm"
SPLITS_BBOX = ("39.998", "-96.001", "40.002", "-95.973")

#: The I-94 corridor's deliberate lane-change settings, the ones the
#: regeneration of docs/ONBOARDING_MNDOT.md §11 silently reset.
I94_FLEET_EDITS: dict[str, object] = {
    "lc_strategic": 5.0,
    "lc_strategic_ramp": 1.0,
    "lc_keep_right": 0.0,
    "idm_calibration": "artifacts/idm_i24_capacity.json",
}


def _block(text: str, key: str) -> str:
    """The top-level YAML block ``key:`` of ``text``, verbatim (byte-for-byte)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if line.startswith(f"{key}:"):
            inside = True
        elif inside and line and not line[0].isspace():
            break
        if inside:
            out.append(line)
    assert out, f"no top-level block {key!r}"
    return "".join(out)


def _build_argv(tmp_path: Path, out: Path, *extra: str) -> list[str]:
    extract = tmp_path / "osm" / "splits.osm"
    if not extract.exists():
        extract.parent.mkdir(parents=True)
        shutil.copyfile(SPLITS_FIXTURE, extract)
    return [
        "--name",
        "keep_fleet",
        "--bbox",
        *SPLITS_BBOX,
        "--bearing",
        "90",
        "--workdir",
        str(tmp_path / "work"),
        "--out",
        str(out),
        "--osm-file",
        str(extract),
        *extra,
    ]


class TestReonboardingKeepsTheFleet:
    """Re-running over an existing scenario keeps what the network rebuild
    must not touch (docs/ONBOARDING_MNDOT.md §11 and its correction)."""

    def test_the_flag_parses_and_the_help_names_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        args = cli.parse_args(_argv())
        assert args.fresh_fleet is False
        assert args.seed is None and args.duration_s is None and args.replicates is None
        assert cli.parse_args(_argv("--fresh-fleet")).fresh_fleet is True
        with pytest.raises(SystemExit):
            cli.parse_args(["--help"])
        # argparse wraps the help text; compare on collapsed whitespace
        out = " ".join(capsys.readouterr().out.split())
        assert "--fresh-fleet" in out and "or the kept scenario's" in out
        assert "a file that does not parse is refused with exit 2" in out

    def test_an_existing_scenario_keeps_its_blocks_byte_for_byte(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        out_yaml = tmp_path / "keep.yaml"

        # a new file: the builder's defaults, and the report says so
        assert cli.main(_build_argv(tmp_path, out_yaml, "--duration-s", "60")) == 0
        out = capsys.readouterr().out
        assert f"  {cli.FLEET_DEFAULTS_LINE}\n" in out and "fleet block kept" not in out
        first = out_yaml.read_text()
        assert "lc_strategic: 1.0" in _block(first, "fleet")

        # the operator sets the corridor's fleet, sim, seed, replicates, FD and macro
        raw = yaml.safe_load(first)
        raw["fleet"].update(I94_FLEET_EDITS)
        raw["sim"]["output_hz"] = 1.0
        raw["seed"] = 7
        raw["replicates"] = 3
        raw["fd_calibration"] = "artifacts/fd_unit.json"
        raw["macro"] = {"dx_m": 50.0}
        ScenarioConfig.model_validate(raw).to_yaml(out_yaml)
        edited = out_yaml.read_text()

        # re-onboarding rebuilds the network and keeps every other block
        assert cli.main(_build_argv(tmp_path, out_yaml)) == 0
        out = capsys.readouterr().out
        kept_line = (
            f"  fleet block kept from {out_yaml} (model EIDM, heterogeneity_frac 0.15, "
            "idm_calibration artifacts/idm_i24_capacity.json, lc_strategic 5.0, "
            "lc_strategic_ramp 1.0, lc_keep_right 0.0)\n"
        )
        assert kept_line in out and cli.FLEET_DEFAULTS_LINE not in out
        rebuilt = out_yaml.read_text()
        for key in ("fleet", "sim", "seed", "replicates", "fd_calibration", "macro"):
            assert _block(rebuilt, key) == _block(edited, key), key
        assert "lc_strategic: 5.0" in _block(rebuilt, "fleet")
        assert "duration_s: 60.0" in _block(rebuilt, "sim")
        network = yaml.safe_load(rebuilt)["network"]
        assert network["corridor_edges"] == ["300", "301", "302", "303"]
        assert network["ramps"] and network["netconvert_extra"]

        # the command line still wins over the kept values
        assert cli.main(_build_argv(tmp_path, out_yaml, "--seed", "9", "--duration-s", "90")) == 0
        capsys.readouterr()
        again = yaml.safe_load(out_yaml.read_text())
        assert again["seed"] == 9 and again["sim"]["duration_s"] == 90.0
        assert again["sim"]["output_hz"] == 1.0 and again["replicates"] == 3
        assert again["fleet"]["lc_strategic"] == 5.0

    def test_fresh_fleet_resets_to_the_builder_defaults(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        out_yaml = tmp_path / "fresh.yaml"
        assert cli.main(_build_argv(tmp_path, out_yaml, "--duration-s", "60")) == 0
        capsys.readouterr()
        defaults = out_yaml.read_text()
        raw = yaml.safe_load(defaults)
        raw["fleet"].update(I94_FLEET_EDITS)
        raw["seed"] = 7
        ScenarioConfig.model_validate(raw).to_yaml(out_yaml)

        assert cli.main(_build_argv(tmp_path, out_yaml, "--fresh-fleet", "--duration-s", "60")) == 0
        out = capsys.readouterr().out
        assert f"  {cli.FLEET_DEFAULTS_LINE}\n" in out and "fleet block kept" not in out
        rebuilt = out_yaml.read_text()
        assert _block(rebuilt, "fleet") == _block(defaults, "fleet")
        assert _block(rebuilt, "seed") == "seed: 42\n"
        assert rebuilt == defaults

    def test_a_broken_existing_file_is_refused_before_any_build(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        broken = tmp_path / "broken.yaml"
        broken.write_text("fleet: [\n")
        code = cli.main(_build_argv(tmp_path, broken))
        out = capsys.readouterr().out
        assert code == cli.BAD_USAGE_EXIT == 2
        assert str(broken) in out and "does not parse as a ScenarioConfig" in out
        assert "--fresh-fleet" in out and "scenario  " not in out
        assert broken.read_text() == "fleet: [\n" and not (tmp_path / "work").exists()

        # valid YAML that is not a scenario is refused with the validation reason
        broken.write_text("name: x\n")
        code = cli.main(_build_argv(tmp_path, broken))
        out = capsys.readouterr().out
        assert code == 2 and "validation error" in out and not (tmp_path / "work").exists()

        # --fresh-fleet overrides: the file is rebuilt from the defaults
        assert cli.main(_build_argv(tmp_path, broken, "--fresh-fleet", "--duration-s", "60")) == 0
        out = capsys.readouterr().out
        assert f"  {cli.FLEET_DEFAULTS_LINE}\n" in out
        assert ScenarioConfig.from_yaml(broken).fleet.lc_strategic == 1.0
