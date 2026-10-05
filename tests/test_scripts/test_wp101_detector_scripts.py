"""scripts/data_quality_report.py and scripts/ramp_estimate.py (WP-101): CLI smoke tests on
tiny synthetic inputs written to ``tmp_path`` — a corridor directory, a generic per-lane
export, and a per-lane MnDOT 30-second cache read offline — plus the ``code_dirty`` scope.
No real detector data is read."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION_TESTS = REPO_ROOT / "tests" / "test_calibration"
MNDOT_FIXTURE = CALIBRATION_TESTS / "fixtures" / "mndot_metro_config_tiny.xml"


def _load(path: Path, name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dq = _load(SCRIPTS / "data_quality_report.py", "flowstate_wp101_dq_script")
ramps = _load(SCRIPTS / "ramp_estimate.py", "flowstate_wp101_ramp_script")
syn = _load(CALIBRATION_TESTS / "synthetic_detectors.py", "flowstate_wp101_syn_scripts")


def _write_tidy(frame: pd.DataFrame, path: Path) -> Path:
    out = frame.copy()
    out["timestamp"] = [t.isoformat() for t in out["timestamp"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def corridor_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A corridor directory like data/mndot/<corridor>/: detectors.csv + stations.csv."""
    base = tmp_path_factory.mktemp("corridor")
    frame = syn.daily_frame(seed=11)
    # plant a dead exit loop on one day
    frame = syn.set_values(
        frame, station="R2", date=syn.DATES[1], start_h=0.0, end_h=24.0,
        flow_veh_h=0.0, occupancy_pct=0.0, speed_ms=np.nan,
    )  # fmt: skip
    _write_tidy(frame, base / "detectors.csv")
    syn.stations_table().to_csv(base / "stations.csv", index=False)
    return base


class TestDataQualityScript:
    def test_corridor_directory(self, corridor_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "dq"
        assert dq.main(["--corridor-dir", str(corridor_dir), "--out", str(out)]) == 0
        payload = json.loads((out / "data_quality.json").read_text())
        assert payload["schema"] == "flowstate.data_quality/1"
        prov = payload["provenance"]
        assert prov["code_dirty"] in (True, False, None)
        assert prov["inputs"]["mode"] == "detector_csv"
        assert len(prov["inputs"]["detectors_sha256"]) == 64
        assert len(prov["inputs"]["stations_sha256"]) == 64
        dead = [
            sd
            for sd in payload["sensor_days"]
            if sd["sensor"] == "R2" and sd["date"] == syn.DATES[1]
        ]
        assert dead[0]["verdict"] == "exclude"
        assert payload["summary"]["n_exclude"] == 1
        # the count error the report assumed, at the stable key the uncertainty runner reads
        assert payload["parameters"]["count_error"] == 0.05
        assert (
            dq.main(
                ["--corridor-dir", str(corridor_dir), "--count-error", "0.08", "--out", str(out)]
            )
            == 0
        )
        assert (
            json.loads((out / "data_quality.json").read_text())["parameters"]["count_error"] == 0.08
        )
        text = (out / "data_quality.md").read_text()
        assert "| R2 | off_ramp | 1 of 4 |" in text

    def test_generic_per_lane_export_with_a_column_map(self, tmp_path: Path) -> None:
        frame = syn.daily_frame(seed=12, per_lane=True, dates=syn.DATES[:3])
        frame["speed_ms"] = frame["speed_ms"] / 0.44704  # the export is in mph
        frame = frame.rename(
            columns={"timestamp": "Time", "flow_veh_h": "Volume", "speed_ms": "Speed", "lane": "Ln"}
        )
        frame["Time"] = [t.isoformat() for t in frame["Time"]]
        path = tmp_path / "export.csv"
        frame.to_csv(path, index=False)
        stations = tmp_path / "stations.csv"
        syn.stations_table().to_csv(stations, index=False)
        out = tmp_path / "dq"
        code = dq.main(
            [
                "--detectors", str(path), "--stations", str(stations), "--lane-column", "Ln",
                "--column-map", json.dumps({"timestamp": "Time", "flow": "Volume", "speed": "Speed"}),
                "--speed-unit", "mph", "--start", "05:00", "--end", "22:00", "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "data_quality.json").read_text())
        assert payload["grid"]["per_lane"] is True
        assert payload["grid"]["start_local"] == "05:00"
        assert payload["grid"]["end_local"] == "22:00"
        assert {"A:1", "A:2", "A:3", "R1:1"} <= {s["sensor"] for s in payload["sensors"]}
        assert payload["summary"]["n_exclude"] == 0

    def test_no_input_is_a_usage_error(self, tmp_path: Path) -> None:
        assert dq.main(["--out", str(tmp_path / "x")]) == 2


class TestRampScript:
    def test_estimate_an_unmeasured_entrance(self, corridor_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "est"
        code = ramps.main(
            ["--corridor-dir", str(corridor_dir), "--unmeasured", "R1", "--out", str(out)]
        )
        assert code == 0
        payload = json.loads((out / "ramp_estimates.json").read_text())
        assert payload["schema"] == "flowstate.ramp_estimates/1"
        (r1,) = [e for e in payload["estimates"] if e["ramp"] == "R1"]
        assert r1["status"] == "estimated"
        assert payload["provenance"]["quality"] == {"applied": False}
        assert "## Assumptions" in (out / "ramp_estimates.md").read_text()

    def test_leave_one_out_with_quality_masking(self, corridor_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "loo"
        code = ramps.main(
            [
                "--corridor-dir", str(corridor_dir), "--apply-quality", "--leave-one-out",
                "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "ramp_leave_one_out.json").read_text())
        assert payload["schema"] == "flowstate.ramp_leave_one_out/1"
        quality = payload["provenance"]["quality"]
        assert quality["applied"] is True
        assert f"R2 {syn.DATES[1]}" in quality["excluded_sensor_days"]
        tested = {r["ramp"]: r for r in payload["ramps"]}
        assert tested["R1"]["status"] == tested["R2"]["status"] == "tested"
        # the dead day is masked, so it is neither truth nor an input
        assert tested["R2"]["overall"]["n"] == 3 * 94
        assert payload["pooled"]["overall"]["relative_error"] < 0.15

    def test_splits_file(self, tmp_path: Path) -> None:
        frame = syn.linear_frame(with_r0=True, r1_slope=0.0)
        _write_tidy(frame, tmp_path / "c" / "detectors.csv")
        syn.stations_table(with_r0=True).to_csv(tmp_path / "c" / "stations.csv", index=False)
        splits = tmp_path / "splits.json"
        splits.write_text(
            json.dumps(
                [{"ramp": "R0", "kind": "share_of_upstream", "value": 0.08, "source": "test"}]
            )
        )
        out = tmp_path / "est"
        code = ramps.main(
            [
                "--corridor-dir", str(tmp_path / "c"), "--unmeasured", "R0,R1",
                "--splits", str(splits), "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "ramp_estimates.json").read_text())
        assert {e["ramp"]: e["status"] for e in payload["estimates"]} == {
            "R0": "estimated",
            "R1": "estimated",
        }
        assert payload["splits"][0]["source"] == "test"


def _write_mndot_cache(cache: Path, date: str, seed: int = 0) -> None:
    """30-second JSON series for the S2105 → S2106 span of the tiny IRIS fixture."""
    rng = np.random.default_rng(seed)
    n = 2880
    exit_counts = rng.poisson(1.0, n)
    up = {d: rng.poisson(5.0, n) for d in ("9066", "9067", "9068")}
    down = {
        "9069": up["9066"] - np.minimum(exit_counts, up["9066"]),
        "9070": up["9067"],
        "9071": up["9068"],
    }
    series = {**up, **down, "5100": np.minimum(exit_counts, up["9066"])}
    folder = cache / date
    folder.mkdir(parents=True, exist_ok=True)
    for name, counts in series.items():
        occupancy = np.round(counts * 1.4 + rng.normal(0.0, 0.2, n), 2)
        speed = np.where(counts > 0, rng.integers(55, 66, n), 0)
        for endpoint, values in (
            ("counts", counts.tolist()),
            ("occupancy", occupancy.tolist()),
            ("speed", speed.tolist()),
        ):
            (folder / f"{name}.{endpoint}.json").write_text(json.dumps(values))


class TestMndotCache:
    def test_per_lane_quality_from_an_offline_cache(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        _write_mndot_cache(cache, "20260915")
        corridor = tmp_path / "corridor"
        corridor.mkdir()
        (corridor / "selection.json").write_text(
            json.dumps(
                {
                    "route": "I-94",
                    "dir": "WB",
                    "from_station": "S2105",
                    "to_station": "S2106",
                    "dates": ["20260915"],
                    "window_s": 300,
                }
            )
        )
        out = tmp_path / "dq"
        code = dq.main(
            [
                "--corridor-dir", str(corridor), "--lanes-from-cache", str(cache),
                "--metro-config", str(MNDOT_FIXTURE), "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "data_quality.json").read_text())
        sensors = {s["sensor"] for s in payload["sensors"]}
        assert sensors == {
            "S2105:9066", "S2105:9067", "S2105:9068",
            "S2106:9069", "S2106:9070", "S2106:9071", "rnd_88851:5100",
        }  # fmt: skip
        prov = payload["provenance"]["inputs"]
        assert prov["mode"] == "mndot_lanes_from_cache"
        assert prov["allow_fetch"] is False
        assert payload["mass_balance"]["segments"][0]["n_days_evaluated"] == 1
        assert payload["mass_balance"]["segment_days"][0]["verdict"] == "ok"

    def test_a_missing_cache_entry_is_not_fetched(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        _write_mndot_cache(cache, "20260915")
        (cache / "20260915" / "9070.speed.json").unlink()
        with pytest.raises(LookupError, match="not in the local cache"):
            dq.main(
                [
                    "--lanes-from-cache", str(cache),
                    "--metro-config", str(MNDOT_FIXTURE), "--mndot-corridor", "I-94 WB",
                    "--from-station", "S2105", "--to-station", "S2106", "--dates", "20260915",
                    "--out", str(tmp_path / "dq"),
                ]
            )  # fmt: skip
        assert not (cache / "20260915" / "9070.speed.json").exists()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("module", [dq, ramps], ids=["data_quality_report", "ramp_estimate"])
def test_code_dirty_counts_only_the_scripts_own_code(
    module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = Path(module.CODE_PATHS[0])
    files = [
        own,
        Path("scripts/other_script.py"),
        Path("packages/calibration/calibration/x.py"),
        Path("packages/microsim/microsim/y.py"),
        Path("artifacts/a.json"),
    ]
    for rel in files:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("0\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    assert module.git_dirty() is False
    for unrelated in files[1:2] + files[3:]:
        (tmp_path / unrelated).write_text("1\n")
    assert module.git_dirty() is False
    (tmp_path / files[2]).write_text("1\n")
    assert module.git_dirty() is True
    _git(tmp_path, "checkout", "--", str(files[2]))
    (tmp_path / own).write_text("1\n")
    assert module.git_dirty() is True
