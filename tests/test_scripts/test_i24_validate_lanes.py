"""scripts/i24_validate.py's per-lane section crossings and lane-set scoring (docs/I24_CONSISTENCY_C7B.md §3).

No simulation and no I-24 data: synthetic frames for the crossing counts, the trajectory-reading parts of
the analysis worker stubbed, and the committed ``flow_speedcal`` battery for the artifact-level checks.
Pinned:

* :func:`section_lane_crossings` splits :func:`crossings_per_window`'s crossings by the later sample's
  lane (their sum is the all-lane count exactly) and counts lane transitions;
* :func:`lane_crossing_block` sets each section's lane set to the four highest indices and sums it;
* with ``--lane-crossings`` off the worker, ``micro_arm`` and ``build_results`` write what they wrote
  before; on, they only add ``lane_crossings`` (last in ``simulated``), ``geh.lane_set`` (last in ``geh``)
  and a note, and the criteria rows are unchanged;
* ``--section-lanes observed`` scores the link-flow row on the lane set, ``--criteria-only`` keeps that,
  and a re-score against another day refuses such a battery;
* the CLI keeps the options to batteries and passes them through.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
ARM = REPO_ROOT / "artifacts" / "i24_validation_flow_speedcal.json"
SCENARIO = REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal.yaml"


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


v = _load("i24_validate")

N_WIN = 4
T_HI = N_WIN * 300.0


def _canon(x: Any) -> str:
    """Canonical JSON (NaN as ``NaN``): equality that holds for equal fields with NaN cells."""
    return json.dumps(x, sort_keys=True)


def _vehicle(vid: str, t0: float, x0: float, x1: float, lanes: tuple[int, int]) -> list[tuple]:
    """Two samples 0.5 s apart, at ``x0`` in ``lanes[0]`` then ``x1`` in ``lanes[1]``."""
    return [(t0, vid, x0, lanes[0]), (t0 + 0.5, vid, x1, lanes[1])]


def _frame() -> pd.DataFrame:
    rows: list[tuple] = []
    # section 1000 m (a 5-lane edge): lanes 0 (auxiliary), 1, 4; one 0 -> 1 change across the section
    rows += _vehicle("a", 10.0, 995.0, 1005.0, (0, 0))
    rows += _vehicle("b", 310.0, 995.0, 1005.0, (1, 1))
    rows += _vehicle("c", 320.0, 990.0, 1000.0, (4, 4))  # x_cur == x_s counts
    rows += _vehicle("d", 620.0, 995.0, 1005.0, (0, 1))  # into the lane set at the crossing
    rows += _vehicle("e", 900.0, 1001.0, 1010.0, (2, 2))  # downstream of the section: no crossing
    rows += _vehicle("f", 1300.0, 995.0, 1005.0, (3, 3))  # after the period: no crossing
    # section 2200 m (a 4-lane edge): lanes 0 and 3, and a 3 -> 2 change
    rows += _vehicle("g", 50.0, 2195.0, 2205.0, (0, 0))
    rows += _vehicle("h", 60.0, 2195.0, 2205.0, (3, 2))
    rows += _vehicle("i", 70.0, 2195.0, 2205.0, (3, 3))
    return pd.DataFrame(rows, columns=["t", "veh_id", "x", "lane"])


def test_section_lane_crossings_split_the_all_lane_count_by_lane() -> None:
    df = _frame()
    out = v.section_lane_crossings(df, [1000.0, 2200.0], 0.0, T_HI)
    s1, s2 = out
    assert len(s1["by_lane"]) == 5 and len(s2["by_lane"]) == 4
    assert s1["by_lane"][0] == [1, 0, 0, 0]
    assert s1["by_lane"][1] == [0, 1, 1, 0]  # b, and d counted in the lane of its later sample
    assert s1["by_lane"][4] == [0, 1, 0, 0]
    assert s1["transitions"][0][1] == 1 and s1["transitions"][0][0] == 1
    assert s2["by_lane"][0] == [1, 0, 0, 0] and s2["by_lane"][2] == [1, 0, 0, 0]
    assert s2["by_lane"][3] == [1, 0, 0, 0]
    assert s2["transitions"][3][2] == 1 and s2["transitions"][3][3] == 1
    for x, sec in zip((1000.0, 2200.0), out, strict=True):
        all_lanes = v.crossings_per_window(df[["t", "veh_id", "x"]], x, 0.0, T_HI)
        assert np.array_equal(np.asarray(sec["by_lane"]).sum(axis=0), all_lanes)


def test_a_section_nobody_crosses_has_no_lanes() -> None:
    out = v.section_lane_crossings(_frame(), [4800.0], 0.0, T_HI)
    assert out == [{"by_lane": [], "transitions": []}]


def test_lane_set_is_the_four_highest_indices() -> None:
    assert v.lane_set(5) == [1, 2, 3, 4]
    assert v.lane_set(4) == [0, 1, 2, 3]
    assert v.lane_set(3) == [0, 1, 2]
    assert v.lane_set(0) == []


def test_lane_crossing_block_sums_the_lane_set_and_counts_the_edge_crossings() -> None:
    df = _frame()
    sections = [1000.0, 2200.0]
    reps = [v.section_lane_crossings(df, sections, 0.0, T_HI)] * 2
    counts = [[v.crossings_per_window(df, x, 0.0, T_HI).tolist() for x in sections] for _ in reps]
    block = v.lane_crossing_block(reps, counts, sections, N_WIN)
    s1, s2 = block["sections"]
    assert (s1["n_lanes"], s1["lane_set"]) == (5, [1, 2, 3, 4])
    assert (s2["n_lanes"], s2["lane_set"]) == (4, [0, 1, 2, 3])
    assert s1["crossings_outside_lane_set_total"] == 2  # a, twice
    assert (s1["into_set_total"], s1["out_of_set_total"]) == (2, 0)  # d, twice
    assert (s2["into_set_total"], s2["out_of_set_total"]) == (0, 0)
    assert block["counts_per_replicate_lane_set"][0] == [[0, 2, 1, 0], [3, 0, 0, 0]]
    assert block["sums_equal_counts_per_replicate"] is True
    assert np.allclose(
        np.asarray(block["hourly_flows_veh_h_mean_lane_set"]),
        np.asarray(block["counts_mean_lane_set"]) * 12.0,
    )
    counts[1][0][0] += 1
    assert (
        v.lane_crossing_block(reps, counts, sections, N_WIN)["sums_equal_counts_per_replicate"]
        is False
    )


# --- the worker and micro_arm -----------------------------------------------------------------------


def _stub_worker(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    import dataclasses
    import math

    from validation.metrics import Metrics

    nan_metrics = Metrics(**{f.name: math.nan for f in dataclasses.fields(Metrics)})
    wave = {"mean_backward_speed_kmh": None, "count": 0, "backward_speeds_kmh": []}
    monkeypatch.setattr(v, "_sim_frame", lambda run_dir, a, b: frame.copy())
    monkeypatch.setattr(v, "compute_metrics", lambda run_dir, **k: nan_metrics)
    monkeypatch.setattr(v, "replicate_locks", lambda run_dir, meta: {"locked": False})
    monkeypatch.setattr(
        v, "_wave_summaries", lambda df, span_hi: {n: dict(wave) for n in v.WAVE_DETECTORS}
    )


def _replicate(root: Path, seed: int) -> Path:
    d = root / str(seed)
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps({"seed": seed, "n_vehicles_planned": 10, "n_vehicles_departed": 9})
    )
    return d


def _sections_frame() -> pd.DataFrame:
    """One crossing of every validator section in each of its windows, in lanes 0 and 3."""
    rows: list[tuple] = []
    for i, x in enumerate(v.SECTIONS_M):
        for w in range(24):
            rows += _vehicle(f"v{i}-{w}", 300.0 * w + 5.0, x - 3.0, x + 3.0, (w % 2 * 3, w % 2 * 3))
    df = pd.DataFrame(rows, columns=["t", "veh_id", "x", "lane"])
    df["v"] = 20.0
    return df


def test_the_worker_adds_lane_crossings_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _sections_frame()
    _stub_worker(monkeypatch, frame)
    d = _replicate(tmp_path, 1)
    payload = (str(d), 0.0, 1.0, 0.0, 6000.0, 24)
    plain = v._analyze_replicate(payload)
    with_lanes = v._analyze_replicate((*payload, True))
    assert "lane_crossings" not in plain
    assert list(with_lanes) == [*plain, "lane_crossings"]
    assert _canon({k: val for k, val in with_lanes.items() if k != "lane_crossings"}) == _canon(
        plain
    )
    assert _canon(v._analyze_replicate((*payload, False))) == _canon(plain)
    for sec, counts in zip(with_lanes["lane_crossings"], plain["counts"], strict=True):
        assert np.asarray(sec["by_lane"]).sum(axis=0).tolist() == counts


@pytest.mark.filterwarnings("ignore:Mean of empty slice:RuntimeWarning")
def test_micro_arm_only_gains_the_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from microsim.runner import RunPaths

    frame = _sections_frame()
    _stub_worker(monkeypatch, frame)
    cfg = v.load_scenario(SCENARIO)
    seeds = v.spawn_seeds(cfg.seed, 2)
    paths = []
    for s in seeds:
        d = _replicate(tmp_path / "runs", s)
        paths.append(RunPaths(d, d / "trajectories.parquet", d / "edges.parquet", d / "meta.json"))
    monkeypatch.setattr(v, "run_replicates", lambda cfg, out_root, n_procs: paths)
    obs = {"n_windows": 24}
    plain = v.micro_arm(cfg, 2, tmp_path / "runs", 1, obs, analysis_procs=1)
    lanes = v.micro_arm(cfg, 2, tmp_path / "runs", 1, obs, analysis_procs=1, lane_crossings=True)
    for d_ in (plain, lanes):
        d_.pop("wall_s")
    assert list(lanes) == [*plain, "lane_crossings"]
    assert _canon({k: val for k, val in lanes.items() if k != "lane_crossings"}) == _canon(plain)
    block = lanes["lane_crossings"]
    assert block["sums_equal_counts_per_replicate"] is True
    assert [s["n_lanes"] for s in block["sections"]] == [4] * len(v.SECTIONS_M)
    assert block["counts_per_replicate_lane_set"] == plain["counts_per_replicate"]


# --- build_results, criteria-only, re-score --------------------------------------------------------


def _with_lanes(sim: dict[str, Any], aux_share: float = 0.1) -> dict[str, Any]:
    """The committed battery's simulated side with a lane block: at the two 5-lane sections a share
    of each window's crossings in lane 0, the rest in lane 1; elsewhere everything in lane 0 of four."""
    out = copy.deepcopy(sim)
    counts = np.asarray(sim["counts_per_replicate"], dtype=np.int64)
    reps = []
    for rep in counts:
        secs = []
        for si, c in enumerate(rep):
            if v.SECTIONS_M[si] in (1000.0, 4800.0):
                aux = (c * aux_share).astype(np.int64)
                by_lane = [aux, c - aux, 0 * c, 0 * c, 0 * c]
                trans = np.diag([int(aux.sum()), int((c - aux).sum()), 0, 0, 0])
            else:
                by_lane = [c, 0 * c, 0 * c, 0 * c]
                trans = np.diag([int(c.sum()), 0, 0, 0])
            secs.append({"by_lane": [r.tolist() for r in by_lane], "transitions": trans.tolist()})
        reps.append(secs)
    out["lane_crossings"] = v.lane_crossing_block(
        reps, counts.tolist(), v.SECTIONS_M, counts.shape[2]
    )
    return out


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(ARM.read_text())


def test_build_results_only_gains_the_lane_set_tables(
    committed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(v, "FAMILY", "_flow")
    d = committed
    cfg = v.load_scenario(SCENARIO)
    before = v.build_results("speedcal", cfg, d["simulated"], d["observed"], 20, d["ring"])
    again = v.build_results(
        "speedcal", cfg, d["simulated"], d["observed"], 20, d["ring"], section_lanes="all"
    )
    before.pop("created_at"), again.pop("created_at")
    assert again == before
    sim = _with_lanes(d["simulated"])
    after = v.build_results("speedcal", cfg, sim, d["observed"], 20, d["ring"])
    after.pop("created_at")
    assert list(after) == list(before)
    assert list(after["geh"]) == [*before["geh"], "lane_set"]
    assert {k: val for k, val in after["geh"].items() if k != "lane_set"} == before["geh"]
    assert after["criteria"] == before["criteria"]
    assert after["notes"][:-1] == before["notes"] and "--lane-crossings" in after["notes"][-1]
    lane = after["geh"]["lane_set"]["vs_recommended_coverage_counts"]
    hourly = np.asarray(sim["lane_crossings"]["hourly_flows_veh_h_mean_lane_set"])
    obs = np.asarray(d["observed"]["hourly_flows_veh_h_recommended"])
    assert lane == v._geh_table(hourly, obs)
    # the 4-lane sections read the same either way; the 5-lane ones lose the auxiliary lane
    all_lanes = np.asarray(sim["hourly_flows_veh_h_mean"])
    for i, x in enumerate(v.SECTIONS_M):
        assert (hourly[i] < all_lanes[i]).any() == (x in (1000.0, 4800.0))


def test_section_lanes_observed_scores_the_row_on_the_lane_set(committed: dict[str, Any]) -> None:
    d = committed
    cfg = v.load_scenario(SCENARIO)
    sim = _with_lanes(d["simulated"], aux_share=0.3)
    res = v.build_results(
        "speedcal", cfg, sim, d["observed"], 20, d["ring"], section_lanes="observed"
    )
    (row,) = [r for r in res["criteria"] if r["name"] == "link_flows_geh"]
    assert res["geh"]["primary"] == v.LANE_SET_PRIMARY
    assert row["value"] == pytest.approx(
        res["geh"]["lane_set"]["vs_recommended_coverage_counts"]["fraction_under_5"]
    )
    assert v.LANE_SET_RULE in res["geh"]["primary_rule"]
    with pytest.raises(ValueError, match="needs the battery's lane crossings"):
        v.build_results(
            "speedcal", cfg, d["simulated"], d["observed"], 20, d["ring"], section_lanes="observed"
        )
    with pytest.raises(ValueError, match="section_lanes"):
        v.build_results("speedcal", cfg, sim, d["observed"], 20, d["ring"], section_lanes="lanes")


def test_criteria_only_keeps_the_lane_set_row_and_rescore_refuses_it(
    committed: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = committed
    cfg = v.load_scenario(SCENARIO)
    sim = _with_lanes(d["simulated"], aux_share=0.3)
    res = v._json_safe(
        v.build_results(
            "speedcal", cfg, sim, d["observed"], 20, d["ring"], section_lanes="observed"
        )
    )
    path = tmp_path / "i24_validation_lk.json"
    path.write_text(json.dumps(res))
    monkeypatch.setattr(v, "artifact_path", lambda arm: path)
    v.refresh_criteria("lk")
    again = json.loads(path.read_text())
    assert again["geh"]["primary"] == v.LANE_SET_PRIMARY
    (row,) = [r for r in again["criteria"] if r["name"] == "link_flows_geh"]
    assert row["value"] == pytest.approx(
        again["geh"]["lane_set"]["vs_recommended_coverage_counts"]["fraction_under_5"]
    )
    with pytest.raises(ValueError, match="observed lane set"):
        v.rescore(again, again["observed"])
    # an all-lane battery still re-scores, and criteria-only leaves its row on every lane
    plain = v._json_safe(v.build_results("speedcal", cfg, sim, d["observed"], 20, d["ring"]))
    path.write_text(json.dumps(plain))
    v.refresh_criteria("lk")
    assert json.loads(path.read_text())["geh"]["primary"] == "recommended"
    assert v.rescore(plain, plain["observed"])["criteria"]


# --- the CLI --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--lane-crossings", "--criteria-only", "--label", "x"],
        ["--section-lanes", "observed", "--ring-only"],
        ["--lane-crossings", "--observed-only", "o.json"],
    ],
)
def test_the_lane_options_go_with_a_battery(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        v.main(argv)


def test_the_explicit_path_passes_the_options_through(
    committed: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = committed
    seen: dict[str, Any] = {}

    def fake_micro_arm(cfg: Any, n: int, out_root: Path, *a: Any, **k: Any) -> dict[str, Any]:
        seen.update(k)
        sim = (
            _with_lanes(d["simulated"], aux_share=0.3)
            if k.get("lane_crossings")
            else copy.deepcopy(d["simulated"])
        )
        sim["run_dirs"] = []
        return sim

    arts = tmp_path / "artifacts"
    arts.mkdir()
    monkeypatch.setattr(v, "OUT_ROOT", tmp_path / "runs")
    monkeypatch.setattr(v, "artifact_path", lambda arm: arts / f"i24_validation_{arm}.json")
    monkeypatch.setattr(v, "observed_side", lambda cache: d["observed"])
    monkeypatch.setattr(v, "micro_arm", fake_micro_arm)
    monkeypatch.setattr(v, "load_meta", lambda run_dir: {})
    monkeypatch.setattr(v, "collision_counts", lambda metas: [0] * 20)
    monkeypatch.setattr(v, "collision_summary", lambda metas, labels: None)
    monkeypatch.setattr(v, "collision_free", lambda metas: True)
    v.main(["--scenario", str(SCENARIO), "--label", "lk", "--ring-seeds", "0", "--lane-crossings"])
    assert seen["lane_crossings"] is True
    res = json.loads((arts / "i24_validation_lk.json").read_text())
    assert res["geh"]["primary"] == "recommended" and "lane_set" in res["geh"]
    seen.clear()
    v.main(
        [
            "--scenario",
            str(SCENARIO),
            "--label",
            "lk2",
            "--ring-seeds",
            "0",
            "--section-lanes",
            "observed",
        ]
    )
    assert seen["lane_crossings"] is True
    res = json.loads((arts / "i24_validation_lk2.json").read_text())
    assert res["geh"]["primary"] == v.LANE_SET_PRIMARY
    seen.clear()
    v.main(["--scenario", str(SCENARIO), "--label", "lk3", "--ring-seeds", "0"])
    assert seen["lane_crossings"] is False
    res = json.loads((arts / "i24_validation_lk3.json").read_text())
    assert "lane_set" not in res["geh"] and "lane_crossings" not in res["simulated"]
