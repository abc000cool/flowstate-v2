"""scripts/transfer_check.py (WP-103): CLI smoke tests on tiny synthetic inputs written to
``tmp_path`` — a corridor directory with an artifact population, a generic per-lane export with
classification counts, a scenario's fleet block with trucks — plus the discovery of the committed
capacity sidecars, the usage errors and the ``code_dirty`` scope. No real detector data is read;
the repository files read are small committed artifacts and scenario fleet blocks."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
import yaml

from flowstate_core.artifacts import IDMCalibration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION_TESTS = REPO_ROOT / "tests" / "test_calibration"
FAST = ["--draws", "1000", "--n-bootstrap", "100"]


def _load(path: Path, name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tc = _load(SCRIPTS / "transfer_check.py", "flowstate_wp103_transfer_script")
syn = _load(CALIBRATION_TESTS / "synthetic_transfer.py", "flowstate_wp103_syn_scripts")


@pytest.fixture(scope="module")
def population(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("pop") / "idm_synthetic.json"
    mean = {"v0": 33.0, "T": 1.4, "a_max": 1.0, "b": 1.7, "s0": 2.0}
    sd = {"v0": 3.0, "T": 0.3, "a_max": 0.3, "b": 0.5, "s0": 0.5}
    IDMCalibration(
        created_at="2026-10-04T00:00:00Z",
        source="synthetic",
        data_hash="0" * 64,
        mean=mean,
        cov=np.diag([sd[k] ** 2 for k in ("v0", "T", "a_max", "b", "s0")]).tolist(),
        n_episodes_fit=100,
        n_episodes_holdout=40,
        holdout_gap_rmse_m=4.0,
    ).save(path)
    return path


@pytest.fixture(scope="module")
def corridor_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A corridor directory like data/mndot/<corridor>/: detectors.csv + stations.csv."""
    base = tmp_path_factory.mktemp("corridor")
    syn.write_tidy(
        syn.corridor_frame(ff_speed=30.0, capacity=1750.0, dates=syn.DATES[:3]),
        base / "detectors.csv",
    )
    syn.stations_table(speed_limit_ms=65 * 0.44704).to_csv(base / "stations.csv", index=False)
    return base


class TestTransferCheckScript:
    def test_corridor_directory_and_an_artifact_population(
        self, corridor_dir: Path, population: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "tc"
        code = tc.main(
            [
                "--corridor-dir", str(corridor_dir), "--population", str(population),
                "--no-sidecar-discovery", "--start", "05:00", "--end", "11:00",
                *FAST, "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "transfer_check.json").read_text())
        assert payload["schema"] == "flowstate.transfer_check/1"
        prov = payload["provenance"]
        assert prov["code_dirty"] in (True, False, None)
        assert prov["script"] == "scripts/transfer_check.py"
        assert prov["inputs"]["mode"] == "detector_csv"
        assert len(prov["inputs"]["detectors_sha256"]) == 64
        assert len(prov["inputs"]["stations_sha256"]) == 64
        assert len(prov["population"]["idm_calibration_sha256"]) == 64
        assert prov["classification"] == {"given": False}
        obs = payload["observed"]
        assert obs["speed_limit_source"].startswith("stations table")
        assert obs["capacity"]["basis"] == "bottleneck_discharge"
        assert obs["free_flow"]["median_ms"] == pytest.approx(30.0, rel=0.01)
        assert obs["quality"]["applied"] is True
        verdicts = {c["quantity"]: c["verdict"] for c in payload["comparisons"]}
        assert verdicts["truck_share"] == "not_available"
        assert payload["model"]["sidecars"] == []
        text = (out / "transfer_check.md").read_text()
        assert "## Recommendations" in text and "Driver population" in text

    def test_generic_per_lane_export_with_classification_counts(
        self, population: Path, tmp_path: Path
    ) -> None:
        frame = syn.corridor_frame(
            ff_speed=30.0, capacity=1750.0, per_lane=True, dates=syn.DATES[:3]
        )
        frame["speed_ms"] = frame["speed_ms"] / 0.44704  # the export is in mph
        frame = frame.rename(
            columns={"timestamp": "Time", "flow_veh_h": "Volume", "speed_ms": "Speed", "lane": "Ln"}
        )
        frame["Time"] = [t.isoformat() for t in frame["Time"]]
        path = tmp_path / "export.csv"
        frame.to_csv(path, index=False)
        stations = tmp_path / "stations.csv"
        syn.stations_table().to_csv(stations, index=False)
        classes = tmp_path / "classes.csv"
        syn.classification_frame(0.12, dates=syn.DATES[:3]).to_csv(classes, index=False)
        out = tmp_path / "tc"
        code = tc.main(
            [
                "--detectors", str(path), "--stations", str(stations), "--lane-column", "Ln",
                "--column-map", json.dumps({"timestamp": "Time", "flow": "Volume", "speed": "Speed"}),
                "--speed-unit", "mph", "--speed-limit", "65",
                "--classification", str(classes),
                "--classification-columns",
                json.dumps({"station": "Site", "date": "Day", "heavy_count": "Trucks", "total_count": "Volume"}),
                "--heavy-definition", "FHWA classes 5-13",
                "--population", str(population), "--no-sidecar-discovery", *FAST,
                "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "transfer_check.json").read_text())
        obs = payload["observed"]
        assert obs["grid"]["per_lane"] is True
        assert obs["speed_limit_source"] == "given"
        assert obs["speed_limit_ms"] == pytest.approx(65 * 0.44704, abs=1e-4)
        assert obs["heavy"]["share"] == pytest.approx(0.12)
        assert obs["heavy"]["definition"] == "FHWA classes 5-13"
        assert payload["model"]["speed_limit_ms"] == pytest.approx(65 * 0.44704, abs=1e-4)
        cls = payload["provenance"]["classification"]
        assert cls["given"] is True and len(cls["sha256"]) == 64
        recs = {r["quantity"]: r for r in payload["recommendations"]}
        assert recs["truck_share"]["action"] == "adjust"
        assert "12.0%" in (out / "transfer_check.md").read_text()

    def test_a_scenario_fleet_block_with_trucks(
        self, corridor_dir: Path, population: Path, tmp_path: Path
    ) -> None:
        scenario = tmp_path / "scenario.yaml"
        scenario.write_text(
            yaml.safe_dump(
                {
                    "name": "synthetic",
                    "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 3,
                                "inflow": [[0.0, 1.0]]},
                    "fleet": {
                        "model": "IDM",
                        "idm_calibration": str(population),
                        "heavy": {
                            "fraction": 0.09,
                            "length_m": 20.27,
                            "emission_class": "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
                            "vclass": "truck",
                            "idm_calibration": "artifacts/idm_i24_heavy.json",
                        },
                    },
                }
            )
        )  # fmt: skip
        out = tmp_path / "tc"
        code = tc.main(
            [
                "--corridor-dir", str(corridor_dir), "--scenario", str(scenario),
                "--no-sidecar-discovery", *FAST, "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "transfer_check.json").read_text())
        assert payload["model"]["heavy_fraction"] == pytest.approx(0.09)
        # the scenario's generated road caps no driver, whatever the real road posts
        assert payload["observed"]["speed_limit_ms"] == pytest.approx(65 * 0.44704)
        assert payload["model"]["speed_limit_ms"] is None
        assert any("generated road" in n for n in payload["model"]["notes"])
        pop = payload["provenance"]["population"]
        assert len(pop["scenario_sha256"]) == 64
        assert pop["heavy_idm_calibration"] == "artifacts/idm_i24_heavy.json"
        assert "the model assumes 9.0%" in (out / "transfer_check.md").read_text()

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("IDM", "artifacts/idm_capacity_probe_i24fleet_3l.calibration.json"),
            ("EIDM", "artifacts/idm_mndot_i94_wb_stpaul_capacity.calibration.json"),
        ],
    )
    def test_committed_capacity_sidecars_are_discovered(
        self, corridor_dir: Path, tmp_path: Path, model: str, expected: str
    ) -> None:
        """The I-24 capacity population (T x 0.875 of idm_i24.json) has simulated
        measurements under IDM (the I-24 fleet block on 3 lanes) and EIDM (the I-94
        fleet block on 3 and 4 lanes); the original I-24 sidecar does not record its
        model and is only used when named."""
        out = tmp_path / "tc"
        code = tc.main(
            [
                "--corridor-dir", str(corridor_dir),
                "--population", "artifacts/idm_i24_capacity.json", "--cf-model", model,
                *FAST, "--out", str(out),
            ]
        )  # fmt: skip
        assert code == 0
        payload = json.loads((out / "transfer_check.json").read_text())
        mod = payload["model"]
        assert mod["capacity_basis"] == "simulated"
        sim = mod["capacity_simulated"]
        assert sim["sidecar"]["path"] == expected
        sidecar = json.loads((REPO_ROOT / expected).read_text())
        rows = sorted((r["T_scale"], r["capacity_veh_h_lane"]) for r in sidecar["table"])
        f = sim["sidecar"]["t_scale_current"]
        assert f == pytest.approx(1.3221695251256709 / 1.5112009904800698, abs=1e-4)
        assert sim["raw_veh_h_lane"] == pytest.approx(
            float(np.interp(f, [r[0] for r in rows], [r[1] for r in rows])), abs=0.1
        )
        reasons = {s["path"]: s for s in mod["sidecars"]}
        unrecorded = reasons["artifacts/idm_i24_capacity.calibration.json"]
        assert not unrecorded["accepted"] and "not recorded" in unrecorded["reason"]
        assert not reasons["artifacts/idm_us101_capacity.calibration.json"]["accepted"]

    def test_usage_errors(self, corridor_dir: Path, population: Path, tmp_path: Path) -> None:
        out = str(tmp_path / "x")
        assert tc.main(["--population", str(population), "--out", out]) == 2
        assert tc.main(["--corridor-dir", str(corridor_dir), "--out", out]) == 2
        both = ["--scenario", "s.yaml", "--population", str(population)]
        assert tc.main(["--corridor-dir", str(corridor_dir), *both, "--out", out]) == 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_code_dirty_counts_code_paths_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for rel in ("artifacts/a.json", "scripts/transfer_check.py", "packages/calibration/p.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("0\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(tc, "REPO_ROOT", tmp_path)
    assert tc.git_dirty() is False
    (tmp_path / "artifacts/a.json").write_text("1\n")
    assert tc.git_dirty() is False
    (tmp_path / "packages/calibration/p.py").write_text("1\n")
    assert tc.git_dirty() is True
