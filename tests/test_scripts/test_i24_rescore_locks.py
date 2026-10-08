"""scripts/i24_rescore_locks.py: an archived I-24 battery's locks, re-scored into a sidecar.

On a hand-built stage archive (the run tree ``runs/i24_validation/<label>/
<hash>/<seed>/`` with ``meta.json``, ``edges.parquet``, for one seed
``vehicles.parquet``, decoy ``trajectories.parquet`` / ``journeys.parquet``
members, a hard-link entry repeating a ``meta.json``, and another label's
tree) and a battery artifact standing in for the committed one:

* only ``meta.json`` / ``edges.parquet`` / ``vehicles.parquet`` of the
  battery's own seeds are extracted — never the trajectories — into a
  temporary directory that is gone afterwards;
* the sidecar carries the battery artifact's keys (``simulated.
  locks_per_replicate`` in seed order, ``locks``, ``zero_locks``) and the
  ``no_locks`` row they score: FAIL on a locked seed (named), PASS when every
  seed is completely recorded and unlocked, NOT RECORDED when a seed has only
  its ``meta.json`` (as 19 seeds of each stage-p13 battery do);
* the crawling breakdown (the p14 seed's shape) is no lock, and the standing
  diagnostic says how far it was from one;
* the battery artifact is never modified.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from tests.test_scripts.test_i24_validate_locks import (
    _meta,
    _vehicles,
    crawling_breakdown,
    free_flow,
    stopped_breakdown,
)
from validation.locks import LOCK_MIN_DURATION_S, RunLocks

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
HASH = "abc123def456"
LABEL = "lbl"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r = _load("i24_rescore_locks")


def _parquet(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    return buf.getvalue()


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _archive(path: Path, replicates: dict[int, dict[str, bytes]]) -> Path:
    """A stage archive holding ``replicates`` (seed -> {file: bytes}) under the
    battery's tree, decoys beside them and a hard-link entry repeating a meta.json."""
    with tarfile.open(path, "w:gz") as tar:
        for seed, files in replicates.items():
            for name, data in files.items():
                _add(tar, f"runs/i24_validation/{LABEL}/{HASH}/{seed}/{name}", data)
            _add(tar, f"runs/i24_validation/{LABEL}/{HASH}/{seed}/trajectories.parquet", b"TRAJ")
            _add(tar, f"runs/i24_validation/{LABEL}/{HASH}/{seed}/journeys.parquet", b"JRNY")
        first = next(iter(replicates))
        link = tarfile.TarInfo(f"runs/i24_validation/{LABEL}/{HASH}/{first}/meta.json")
        link.type = tarfile.LNKTYPE
        link.linkname = link.name
        tar.addfile(link)
        _add(tar, f"runs/i24_validation/other/{HASH}/{first}/edges.parquet", b"not parquet")
        _add(tar, f"runs/i24_validation/{LABEL}/ffffffffffff/{first}/edges.parquet", b"x")
    return path


def _replicate(
    seed: int, edges: pd.DataFrame | None, *, vehicles: bool = False
) -> dict[str, bytes]:
    files = {"meta.json": json.dumps(_meta(seed)).encode()}
    if edges is not None:
        files["edges.parquet"] = _parquet(edges)
    if vehicles:
        files["vehicles.parquet"] = _parquet(_vehicles())
    return files


def _battery(path: Path, seeds: list[int]) -> Path:
    path.write_text(
        json.dumps(
            {
                "scenario": "i24_replica_x",
                "config_hash": HASH,
                "seeds": seeds,
                "criteria": [
                    {"name": "no_collisions", "evaluated": True, "passed": True, "value": 0.0},
                    {"name": "no_locks", "evaluated": False, "passed": False, "value": None},
                ],
            }
        )
    )
    return path


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replicates: dict[int, dict[str, bytes]]
) -> tuple[dict[str, Any], list[str], Path]:
    archive = _archive(tmp_path / "stage_final.tgz", replicates)
    battery = _battery(tmp_path / "i24_validation_lbl.json", list(replicates))
    before = hashlib.sha256(battery.read_bytes()).hexdigest()
    scratch = tmp_path / "scratch"
    seen: list[str] = []
    score = r.score_replicates

    def spy(root: Path, seeds: Any) -> Any:
        seen.extend(sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()))
        return score(root, seeds)

    monkeypatch.setattr(r, "score_replicates", spy)
    out = tmp_path / "i24_locks_lbl.json"
    argv = ["--archive", str(archive), "--label", LABEL, "--artifact", str(battery)]
    assert r.main([*argv, "--out", str(out), "--scratch", str(scratch)]) == 0
    assert hashlib.sha256(battery.read_bytes()).hexdigest() == before
    assert list(scratch.iterdir()) == []  # the extraction is deleted
    return json.loads(out.read_text()), seen, battery


def test_a_locked_seed_fails_and_only_the_readers_inputs_are_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replicates = {
        11: _replicate(11, free_flow(), vehicles=True),
        22: _replicate(22, stopped_breakdown()),
        33: _replicate(33, crawling_breakdown()),
    }
    side, seen, _ = _run(tmp_path, monkeypatch, replicates)
    assert seen == [
        "11/edges.parquet",
        "11/meta.json",
        "11/vehicles.parquet",
        "22/edges.parquet",
        "22/meta.json",
        "33/edges.parquet",
        "33/meta.json",
    ]
    assert side["source"]["files_read"] == {
        "11": ["edges.parquet", "meta.json", "vehicles.parquet"],
        "22": ["edges.parquet", "meta.json"],
        "33": ["edges.parquet", "meta.json"],
    }
    assert side["source"]["run_tree"] == f"runs/i24_validation/{LABEL}/{HASH}"
    assert side["seeds"] == [11, 22, 33] and side["config_hash"] == HASH
    records = [RunLocks.from_dict(x) for x in side["simulated"]["locks_per_replicate"]]
    assert [x.locked for x in records] == [False, True, False]
    assert records[0].sources == ("edges", "vehicles")
    assert [x["run"] for x in side["locks"]["runs_locked"]] == [22]
    assert side["locks"]["by_section"][0]["section"] == "OH-ON"
    assert side["zero_locks"] is False
    (row,) = side["criteria"]
    assert row["name"] == "no_locks" and row["evaluated"] and not row["passed"]
    assert row["value"] == 1.0
    assert side["battery_artifact"]["no_locks_row"]["evaluated"] is False
    standing = side["standing_per_replicate"]
    assert standing[0]["longest_standing_s"] == 0.0 and standing[0]["longest_x_lo_m"] is None
    assert standing[1]["longest_standing_s"] >= LOCK_MIN_DURATION_S
    assert standing[2]["longest_standing_s"] == 180.0 < standing[2]["lock_duration_s"]
    assert side["code"]["sha256"]["scripts/i24_rescore_locks.py"]


def test_every_seed_recorded_and_unlocked_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    side, _, _ = _run(
        tmp_path,
        monkeypatch,
        {11: _replicate(11, free_flow()), 33: _replicate(33, crawling_breakdown())},
    )
    (row,) = side["criteria"]
    assert row["evaluated"] and row["passed"] and row["value"] == 0.0
    assert side["zero_locks"] is True and side["locks"]["n_runs_recorded"] == 2


def test_a_seed_with_its_meta_alone_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    side, _, _ = _run(
        tmp_path, monkeypatch, {11: _replicate(11, free_flow()), 44: _replicate(44, None)}
    )
    assert side["source"]["files_read"]["44"] == ["meta.json"]
    assert side["simulated"]["locks_per_replicate"][1]["locked"] is None
    assert side["locks"]["runs_not_recorded"] == [44]
    assert side["zero_locks"] is None and side["standing_per_replicate"][1] is None
    (row,) = side["criteria"]
    assert not row["evaluated"] and row["value"] is None
    assert row["detail"].startswith("not recorded")


def test_a_label_absent_from_the_archive_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _archive(tmp_path / "a.tgz", {11: _replicate(11, free_flow())})
    battery = _battery(tmp_path / "b.json", [11])
    with pytest.raises(SystemExit) as exc:
        r.main(["--archive", str(archive), "--label", "nolabel", "--artifact", str(battery)])
    assert exc.value.code == 2
    assert "no replicate files" in capsys.readouterr().err


def test_the_member_pattern() -> None:
    pat = r.member_pattern("runs/i24_validation", LABEL, HASH)
    base = f"runs/i24_validation/{LABEL}/{HASH}"
    assert pat.match(f"{base}/1/meta.json") and pat.match(f"./{base}/1/edges.parquet")
    assert pat.match(f"{base}/1/vehicles.parquet")
    for name in (
        f"{base}/1/trajectories.parquet",
        f"{base}/1/journeys.parquet",
        f"{base}/x/meta.json",
        f"{base}/1/../../meta.json",
        f"runs/i24_validation/other/{HASH}/1/meta.json",
        f"runs/i24_validation/{LABEL}/{HASH}0/1/meta.json",
    ):
        assert pat.match(name) is None, name
