"""``scripts/layout_audit.py``: build the runner's network, audit it, write three files.

The CLI of WP-102 (docs/LAYOUT_CHECKLIST.md). Smoke runs on the committed
OSM fixtures (``netconvert`` only, no simulation): a scenario YAML and an
onboarding output directory in, ``layout_audit.json`` / ``layout_audit.csv``
/ ``layout_checklist.md`` out with the provenance block; usage errors exit 2;
``--fail-on-defect`` exits 4 on a planted wrong-side exit; a scripted merge
whose guessed acceleration lane the runner terminates is audited on the
runner's patched network; and ``code_dirty`` counts this script's code paths
only (the tests/test_scripts/test_code_dirty.py convention).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from flowstate_core.config import ScenarioConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _load_cli() -> ModuleType:
    """Import ``scripts/layout_audit.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "layout_audit.py"
    spec = importlib.util.spec_from_file_location("_layout_audit_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scenario(tmp_path: Path, fixture: str, network: dict[str, Any], name: str = "fx") -> Path:
    """A scenario YAML on a copy of an OSM fixture."""
    osm = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, osm)
    cfg = ScenarioConfig.model_validate(
        {
            "name": name,
            "network": {"kind": "osm", "osm_file": str(osm), "inflow": [[0.0, 0.5]], **network},
            "sim": {"duration_s": 60.0},
        }
    )
    path = tmp_path / f"{name}.yaml"
    cfg.to_yaml(path)
    return path


def _merge_scenario(tmp_path: Path) -> Path:
    return _scenario(
        tmp_path,
        "merge.osm",
        {
            "corridor_edges": ["100", "101", "102", "103"],
            "ramps": [
                {
                    "kind": "on",
                    "name": "on 200",
                    "edges": ["200"],
                    "attach_edge": "102",
                    "inflow": [[0.0, 0.1]],
                },
                {
                    "kind": "off",
                    "name": "off 201",
                    "edges": ["201"],
                    "attach_edge": "100",
                    "exit_fraction": [[0.0, 0.1]],
                },
            ],
        },
        name="merge_fixture",
    )


class TestSmoke:
    def test_scenario_in_three_files_out(self, tmp_path: Path) -> None:
        cli = _load_cli()
        out = tmp_path / "out"
        assert cli.main(["--scenario", str(_merge_scenario(tmp_path)), "--out", str(out)]) == 0
        data = json.loads((out / cli.JSON_NAME).read_text())
        assert data["schema"] == "flowstate.layout_audit/1"
        assert data["corridor"] == "merge_fixture"
        assert [e["kind"] for e in data["events"]] == [
            "off_ramp",
            "lane_gain",
            "on_ramp",
            "lane_drop",
        ]
        assert data["events"][2]["detail"]["ramp"] == "on 200"
        prov = data["provenance"]
        assert prov["script"] == "scripts/layout_audit.py"
        assert prov["code"] and prov["code_dirty"] in (True, False, None)
        assert len(prov["config_hash"]) >= 8 and len(prov["osm_sha256"]) == 64
        assert prov["versions"]["eclipse-sumo"]
        # the network audited is the one the runner compiles, in --out/net by default
        assert Path(data["net_path"]).parent == out / "net"
        with (out / cli.CSV_NAME).open() as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == data["counts"]["segments"] + data["counts"]["events"]
        assert rows[-1]["checked_by"] == ""
        text = (out / cli.MARKDOWN_NAME).read_text()
        assert text.startswith("# Layout checklist: merge_fixture")
        assert "config hash" in text

    def test_onboarding_directory(self, tmp_path: Path) -> None:
        cli = _load_cli()
        corridor_dir = tmp_path / "corridors" / "abc"
        corridor_dir.mkdir(parents=True)
        shutil.copyfile(_merge_scenario(tmp_path), corridor_dir / "scenario.yaml")
        (corridor_dir / "demand.json").write_text("{}")
        out = tmp_path / "out"
        assert cli.main(["--onboarding-dir", str(corridor_dir), "--out", str(out)]) == 0
        assert (out / cli.MARKDOWN_NAME).is_file()


class TestUsage:
    def test_a_directory_without_one_scenario_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cli.main(["--onboarding-dir", str(empty), "--out", str(tmp_path / "o")]) == 2
        assert "no scenario.yaml" in capsys.readouterr().out
        two = tmp_path / "two"
        two.mkdir()
        (two / "a.yaml").write_text("name: a\n")
        (two / "b.yaml").write_text("name: b\n")
        assert cli.main(["--onboarding-dir", str(two), "--out", str(tmp_path / "o")]) == 2
        assert "several scenario files" in capsys.readouterr().out

    def test_a_ring_scenario_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli = _load_cli()
        ring = REPO_ROOT / "scenarios" / "ring_sugiyama.yaml"
        assert cli.main(["--scenario", str(ring), "--out", str(tmp_path / "o")]) == 2
        assert "OSM corridor" in capsys.readouterr().out
        assert not (tmp_path / "o").exists()

    def test_one_source_is_required(self) -> None:
        cli = _load_cli()
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--out", "o"])
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["--scenario", "a", "--onboarding-dir", "b", "--out", "o"]
            )


class TestDefectsAndMergeModels:
    def test_fail_on_defect_exits_4_on_a_wrong_side_exit(self, tmp_path: Path) -> None:
        cli = _load_cli()
        patch = tmp_path / "split_wrong.con.xml"
        patch.write_text(
            "<connections>\n"
            '  <connection from="300" to="400" fromLane="2" toLane="0"/>\n'
            '  <connection from="300" to="301" fromLane="0" toLane="0"/>\n'
            '  <connection from="300" to="301" fromLane="1" toLane="1"/>\n'
            '  <connection from="300" to="301" fromLane="2" toLane="2"/>\n'
            "</connections>\n"
        )
        scenario = _scenario(
            tmp_path,
            "splits.osm",
            {
                "corridor_edges": ["300", "301", "302", "303"],
                "patch_files": [str(patch)],
                "ramps": [
                    {
                        "kind": "off",
                        "name": "right exit",
                        "edges": ["400"],
                        "attach_edge": "300",
                        "exit_fraction": [[0.0, 0.1]],
                    }
                ],
            },
        )
        argv = ["--scenario", str(scenario), "--out", str(tmp_path / "o")]
        assert cli.main(argv) == 0
        assert cli.main([*argv, "--fail-on-defect"]) == cli.DEFECT_EXIT == 4
        data = json.loads((tmp_path / "o" / cli.JSON_NAME).read_text())
        assert data["counts"]["flags_by_severity"]["defect"] == 1
        assert str(patch) in data["patch_files"]
        assert data["provenance"]["patch_sha256"][str(patch)]

    def test_the_runner_network_with_a_terminated_acceleration_lane(self, tmp_path: Path) -> None:
        """A scripted merge on a short attach edge: the runner terminates the guessed lane."""
        lons = (-96.0, -95.994, -95.988, -95.98624, -95.98, -95.974)

        def way(wid: int, refs: list[int], highway: str, lanes: int) -> str:
            nds = "".join(f'<nd ref="{r}"/>' for r in refs)
            return (
                f'  <way id="{wid}">\n    {nds}\n    <tag k="highway" v="{highway}"/>\n'
                f'    <tag k="oneway" v="yes"/>\n    <tag k="lanes" v="{lanes}"/>\n'
                '    <tag k="maxspeed" v="70 mph"/>\n  </way>'
            )

        nodes = [
            f'  <node id="{i}" lat="40.0000" lon="{lon:.5f}"/>' for i, lon in enumerate(lons, 1)
        ]
        nodes.append('  <node id="10" lat="39.9988" lon="-95.99300"/>')
        ways = [way(100 + k, [k + 1, k + 2], "motorway", 3) for k in range(5)]
        ways.append(way(200, [10, 3], "motorway_link", 1))
        osm = tmp_path / "spill.osm"
        osm.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
            + "\n".join(nodes)
            + "\n"
            + "\n".join(ways)
            + "\n</osm>\n"
        )
        cfg = ScenarioConfig.model_validate(
            {
                "name": "spill_scripted",
                "network": {
                    "kind": "osm",
                    "osm_file": str(osm),
                    "corridor_edges": ["100", "101", "102", "103", "104"],
                    "inflow": [[0.0, 0.5]],
                    "netconvert_extra": ["--ramps.guess", "--ramps.ramp-length", "250"],
                    "ramps": [
                        {
                            "kind": "on",
                            "name": "spill on-ramp",
                            "edges": ["200"],
                            "attach_edge": "102",
                            "inflow": [[0.0, 0.2]],
                            "merge": "scripted",
                        }
                    ],
                },
                "sim": {"duration_s": 60.0},
            }
        )
        cli = _load_cli()
        audit = cli.build_and_audit(cfg, tmp_path / "net")
        assert audit.terminated_lanes == ("102",)
        assert Path(audit.net_path).name == "osm_accel_end.net.xml"
        (first_drop, *_) = audit.events_of("lane_drop")
        assert first_drop.detail.from_edge == "102"
        assert first_drop.detail.source == "merge_model_patch"
        assert "merge_model_lane_end" in {f.check for f in first_drop.flags}
        (on,) = audit.events_of("on_ramp")
        assert on.detail.aux_guessed and on.detail.aux_end == "drop"
        assert on.detail.aux_length_m == pytest.approx(audit.segments[2].length_m, abs=0.1)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
class TestCodeDirty:
    @staticmethod
    def _git(repo: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def test_only_the_audited_code_paths_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = (
            "scripts/layout_audit.py",
            "scripts/other.py",
            "packages/microsim/m.py",
            "packages/calibration/c.py",
            "scenarios/s.yaml",
            "artifacts/a.json",
        )
        for rel in files:
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("0\n")
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "add", ".")
        self._git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")
        cli = _load_cli()
        monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
        assert cli.git_dirty() is False
        for rel in (
            "scenarios/s.yaml",
            "artifacts/a.json",
            "scripts/other.py",
            "packages/calibration/c.py",
        ):
            (tmp_path / rel).write_text("1\n")
        assert cli.git_dirty() is False  # data, a scenario, other code: not this audit's code
        (tmp_path / "packages/microsim/m.py").write_text("1\n")
        assert cli.git_dirty() is True
        self._git(tmp_path, "checkout", "--", "packages/microsim/m.py")
        (tmp_path / "scripts/layout_audit.py").write_text("1\n")
        assert cli.git_dirty() is True
        assert len(cli.git_head().split()[0]) == 40
