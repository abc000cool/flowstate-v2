"""``scripts/onboard_corridor.py``: the flags, without a build.

The defaults of 2026-09-24 (ramp guessing on, split fixes on) and their
opt-outs as the parser sees them, plus the self-cancelling pair that is
refused before Overpass or netconvert is spent on it. What the flags do to a
real build is pinned on the MnDOT extract in
``tests/test_microsim/test_microsim_split_audit.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
