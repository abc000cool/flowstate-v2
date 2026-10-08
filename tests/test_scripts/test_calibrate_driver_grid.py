"""scripts/calibrate_driver_grid.py: the Amendment-1 grid's variants, readings, analysis and runner.

Data-only and fast except the last test (``integration``: a 2 x 2 grid on a
60-s synthetic two-lane corridor, run, analysed, resumed). No corridor runs
here (the laptop rule): the I-24 and I-94 grids are checked up to their
scenario variants, config hashes and seeds (``--plan-only``), their observed
targets from committed data, and a faked run tree.

* The I-94 reference is the pipeline's ``xlsfg`` recipe (stage p2_gate_b's
  sed/awk), reproduced as data: the same dict and the hash the phase-1 tuning
  rehearsal recorded for it (1dc4729644dd).
* The I-24 reference is ``scenarios/i24_replica_flow_speedcal.yaml`` itself
  (hash ae5861a4d906, gate A's ``base_config_hash``); pair (0, 0) is the
  reference in both grids, and every pair differs from it only in the
  population and keep-right.
* The I-24 observed lane shares read back as the amendment's 30/24/20/26 % and
  the discharge targets as 6,626 / 6,639 veh/h.
* The I-94 observed lane shares are built from a per-lane cache (the tiny IRIS
  fixture), quality-masked; a station is compared only where the simulated
  lane count matches; IRIS lane n is SUMO lane n - 1.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import ScenarioConfig, config_hash, config_hash_v3
from flowstate_core.rng import spawn_seeds
from validation.driver_calibration import K_GRID
from validation.lane_use import LaneSegment

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SLICE = REPO_ROOT / "scenarios" / "mndot_i94_wb_stpaul_weave_slice.yaml"
MNDOT_FIXTURE = (
    REPO_ROOT / "tests" / "test_calibration" / "fixtures" / "mndot_metro_config_tiny.xml"
)


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


g = _load("calibrate_driver_grid")
dp = _load("derive_population")


# --- variants and plans ---------------------------------------------------------------------


def test_the_i94_reference_is_the_pipeline_recipe(tmp_path: Path) -> None:
    out = tmp_path / "slice_xlsfg.yaml"
    mndot = "mndot_i94_wb_stpaul"
    recipe = (
        f"sed -e 's#^name: {mndot}_weave_slice$#name: {mndot}_weave_slice_xlsfg#' "
        "-e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' " + str(SLICE) + " "
        "| awk '{print} /^  kind: osm$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' "
        "| awk '/^    merge: scripted$/ {s=1; print; next} s && /^    merge_params: \\{\\}$/ "
        '{print "    merge_params: {force_guard: 1.0}"; s=0; next} {s=0; print}\' > ' + str(out)
    )
    subprocess.run(["bash", "-c", recipe], check=True)
    shell = yaml.safe_load(out.read_text())
    ours = g.reference_raw(g.SPECS["i94"])
    assert ours == shell
    # the rehearsal (2026-10-04/05) recorded its base under config-hash policy 3
    h = config_hash_v3(ScenarioConfig.model_validate(ours))
    tune = json.loads(
        (
            REPO_ROOT / "artifacts" / "tune_mndot_i94_wb_stpaul_weave_slice_xlsfg_p1_rehearsal.json"
        ).read_text()
    )
    assert h == tune["base_config_hash"] == "1dc4729644dd"
    raw = yaml.safe_load(SLICE.read_text())
    one = next(r for r in raw["network"]["ramps"] if r.get("merge") == "scripted")
    one["merge_params"] = {"accept_gap_s": 0.6}
    with pytest.raises(ValueError, match="expected 2 scripted merges"):
        g.xlsfg_variant(raw)


@pytest.mark.parametrize("corridor", ["i24", "i94"])
def test_the_plan_varies_only_the_two_settings(corridor: str) -> None:
    spec = g.SPECS[corridor]
    plan = g.build_plan(spec)
    assert len(plan.pairs) == 25 and plan.n_runs == 25 * spec.n_seeds
    assert plan.hashes["k0.0_kr0.0"] == plan.reference_hash
    assert len(set(plan.hashes.values())) == 25
    assert plan.seeds == spawn_seeds(42, spec.n_seeds)
    if corridor == "i24":
        assert plan.seeds == [spawn_seeds(42, 20)[0]]
        gate_a = json.loads(
            (REPO_ROOT / "artifacts" / "i24_merge_experiment_measured.json").read_text()
        )
        # the plan carries the current policy's hash; gate A recorded policy 3's
        assert plan.reference_hash == config_hash(ScenarioConfig.model_validate(plan.reference))
        assert config_hash_v3(plan.reference) == gate_a["base_config_hash"] == "ae5861a4d906"
    for name, (k, kr) in zip(plan.names, plan.pairs, strict=True):
        cfg = plan.configs[name]
        assert cfg["fleet"]["lc_keep_right"] == kr
        assert cfg["fleet"]["idm_calibration"] == g.population_for(spec, k)
        stripped = copy.deepcopy(cfg)
        for key in ("idm_calibration", "lc_keep_right"):
            stripped["fleet"][key] = plan.reference["fleet"][key]
        assert stripped == plan.reference


def test_the_committed_populations_pass_the_check() -> None:
    pops, measured = g.check_populations(g.SPECS["i24"], list(K_GRID))
    assert set(pops) == {str(float(k)) for k in K_GRID}
    assert pops["0.0"]["path"] == "artifacts/idm_i24_capacity.json"
    assert pops["1.0"]["a_max_mean"] == pytest.approx(measured["a_max_mean"] + measured["a_max_sd"])
    assert measured["a_max_sd"] == pytest.approx(0.4284, abs=1e-4)


def test_a_wrong_or_missing_population_is_refused(tmp_path: Path) -> None:
    base = IDMCalibration.load(REPO_ROOT / "artifacts" / "idm_i24_capacity.json")
    spec = g.CorridorSpec(
        key="t",
        base_scenario="scenarios/i24_replica_flow_speedcal.yaml",
        transform=None,
        n_seeds=1,
        mem_gb=1.0,
        mode="i24",
        population_dir=str(tmp_path),
    )
    with pytest.raises(FileNotFoundError, match="derive_population"):
        g.check_populations(spec, [0.0, 0.5])
    base.model_copy(update={"mean": {**base.mean, "a_max": 1.3}}).save(
        tmp_path / dp.out_name(spec.population_stem, 0.5)
    )
    with pytest.raises(ValueError, match="mean a_max"):
        g.check_populations(spec, [0.5])


def test_plan_only_prints_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = g.main(
        [
            "--corridor",
            "i94",
            "--plan-only",
            "--out",
            str(tmp_path / "runs"),
            "--artifact",
            str(tmp_path / "a.json"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "25 pairs" in out and "50 runs" in out and "nothing written" in out
    reference = g.build_plan(g.SPECS["i94"]).reference_hash  # policy 4; 1dc4729644dd under 3
    assert "k1.0_kr1.0" in out and reference in out
    assert list(tmp_path.iterdir()) == []


def test_a_small_machine_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(g, "memory_gb", lambda: (16.0, 10.0))
    code = g.main(
        [
            "--corridor",
            "i24",
            "--out",
            str(tmp_path / "runs"),
            "--artifact",
            str(tmp_path / "a.json"),
        ]
    )
    assert code == 2
    assert "cloud stage" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_the_pool_is_capped_by_memory() -> None:
    assert g.procs_for(12, 25, 9.0, 120.0) == 11  # 120 x 0.85 // 9
    assert g.procs_for(12, 25, 9.0, None) == 12
    assert g.procs_for(12, 3, 9.0, 120.0) == 3
    assert g.procs_for(4, 25, 9.0, 5.0) == 1


# --- observed sides ------------------------------------------------------------------------


def test_the_i24_targets_read_back_as_the_amendment_quotes() -> None:
    obs = g.observed_i24()
    lu = obs["lane_use"]
    assert lu["matches_amendment_quote"] and lu["shares_quoted_pct"] == [30, 24, 20, 26]
    assert sum(lu["shares"].values()) == pytest.approx(1.0)
    assert lu["merge_area_diagnostic"]["shares"]["4"] == pytest.approx(0.2956, abs=1e-3)
    assert obs["discharge"]["matches_amendment_quote"]
    assert obs["discharge"]["flows_veh_h"]["2200"] == pytest.approx(6626.2, abs=0.1)


def test_the_i94_window_is_the_slices_scored_half_hour() -> None:
    cfg = ScenarioConfig.model_validate(g.reference_raw(g.SPECS["i94"]))
    win = g.detector_window(g.SPECS["i94"], cfg)
    assert win["local"] == "07:05-07:35"
    assert win["sim_windows"] == [1, 2, 3, 4, 5, 6]
    assert win["obs_windows"] == [19, 20, 21, 22, 23, 24]


def test_the_i94_discharge_target_is_the_calibration_days_s97(tmp_path: Path) -> None:
    spec = g.SPECS["i94"]
    cfg = ScenarioConfig.model_validate(g.reference_raw(spec))
    win = g.detector_window(spec, cfg)
    ol = tmp_path / "ol.json"
    ol.write_text(
        json.dumps(
            {
                "schema": g.OBSERVED_LANES_SCHEMA,
                "window": {k: win[k] for k in ("local", "local_start_s", "local_end_s")},
                "stations": [],
            }
        )
    )
    s97 = g.observed_detectors(spec, cfg, None, ol)["discharge"]["stations"]["S97"]
    flows = [w["obs_veh_h"] for w in s97["windows"]]
    assert flows == [4668.0, 4692.0, 4637.6, 4538.4, 4312.8, 4094.4]
    assert s97["flow_veh_h"] == pytest.approx(4490.53, abs=0.01)
    # S790 is below 40 mph in every scored window (a queue upstream); the day-mean speeds do not
    # meet the 20-mph difference of the section 5 rule (reported, not used)
    assert all(w["upstream_queued"] for w in s97["windows"])
    assert not any(w["active_bottleneck_window"] for w in s97["windows"])


# --- readings and scoring ------------------------------------------------------------------


def test_i24_readings_on_a_synthetic_run(tmp_path: Path) -> None:
    """Two vehicles on a 4-lane road: SUMO lane 0 is left-numbered 4, SUMO lane 3 is 1."""
    t = np.arange(0.0, 100.0, 0.5)
    rows = []
    for vid, lane, speed in (("a", 0, 10.0), ("b", 3, 20.0)):
        rows.append(pd.DataFrame({"t": t, "veh_id": vid, "x": speed * t, "lane": lane}))
    # one sample in a lane the edge does not have (left out and counted)
    rows.append(pd.DataFrame({"t": [0.0], "veh_id": ["c"], "x": [10.0], "lane": [4]}))
    df = pd.concat(rows, ignore_index=True)
    df.to_parquet(tmp_path / "trajectories.parquet")
    ctx = {
        "mode": "i24",
        "lanes": [{"edge": "e", "x_lo": 0.0, "x_hi": 5000.0, "n_lanes": 4}],
        "a": 0.0,
        "b": 1.0,
        "warmup_s": 0.0,
        "study_s": [0.0, 600.0],
        "span": [0.0, 500.0],
        "merge_area": [0.0, 250.0],
        "sections": [100.0, 900.0, 2500.0],
    }
    rd = g.readings_i24(tmp_path, ctx)
    assert rd["n_samples_unmapped"] == 1
    # a (10 m/s) spends 50 s = 100 samples in [0, 500); b (20 m/s) 25 s = 50 samples
    assert rd["lane_time_counts"]["span"] == {"1": 50, "2": 0, "3": 0, "4": 100, "5": 0}
    assert rd["lane_time_counts"]["merge_area"] == {"1": 25, "2": 0, "3": 0, "4": 50, "5": 0}
    # both cross 100 m and 900 m inside the first of two 5-min windows: 2 / 2 windows -> 12 veh/h;
    # 2,500 m is beyond either within 100 s
    assert rd["discharge_veh_h"] == {
        "100": pytest.approx(12.0),
        "900": pytest.approx(12.0),
        "2500": 0.0,
    }


def test_detector_scoring_maps_iris_lanes_to_sumo_lanes() -> None:
    observed = {
        "lane_use": {
            "stations_compared": [
                {"id": "S1", "lanes": 3, "shares": {"1": 0.1, "2": 0.3, "3": 0.6}}
            ]
        },
        "discharge": {
            "stations": {
                "S1": {
                    "windows": [
                        {"sim_window": 1, "obs_window": 19, "obs_veh_h": 1000.0},
                        {"sim_window": 2, "obs_window": 20, "obs_veh_h": None},
                    ],
                    "flow_veh_h": 1000.0,
                }
            }
        },
    }
    reads = [
        {
            "crossings_by_station": {"S1": {"0": 10, "1": 30, "2": 60}},
            "discharge_windows": {"S1": {"sim_veh_h": [0.0, 900.0, 5000.0]}},
        },
        {
            "crossings_by_station": {"S1": {"0": 20, "1": 60, "2": 120}},
            "discharge_windows": {"S1": {"sim_veh_h": [0.0, 1100.0, 0.0]}},
        },
    ]
    sc = g._score_detectors(reads, observed)
    assert sc["lane_rmse_pp"] == pytest.approx(0.0, abs=1e-12)
    # the window without an observation is left out on both sides: seeds 900 and 1100 -> 1000
    assert sc["discharge_veh_h"] == {"S1": pytest.approx(1000.0)}
    assert sc["discharge_error"] == pytest.approx(0.0)
    # the reversed reading (SUMO 0 as the leftmost) would not score 0
    flipped = [
        {**r, "crossings_by_station": {"S1": {"0": 60, "1": 30, "2": 10}}} for r in reads[:1]
    ]
    assert g._score_detectors(flipped, observed)["lane_rmse_pp"] > 30.0
    assert sc["discharge_stations_not_scored"] == []


def test_a_discharge_station_without_observed_flow_is_skipped_with_the_reason() -> None:
    """observed_detectors gives flow_veh_h None when every scored window is null (a stricter
    quality mask, a new corridor): the station is left out with the reason, not a TypeError;
    with no station left the pair is unscored (ValueError, recorded by analyze)."""
    null_windows = [
        {"sim_window": 1, "obs_window": 19, "obs_veh_h": None},
        {"sim_window": 2, "obs_window": 20, "obs_veh_h": None},
    ]
    observed = {
        "lane_use": {
            "stations_compared": [{"id": "S1", "lanes": 2, "shares": {"1": 0.5, "2": 0.5}}]
        },
        "discharge": {
            "stations": {
                "S1": {
                    "windows": [
                        {"sim_window": 1, "obs_window": 19, "obs_veh_h": 1000.0},
                        {"sim_window": 2, "obs_window": 20, "obs_veh_h": 1200.0},
                    ],
                    "flow_veh_h": 1100.0,
                },
                "S2": {"windows": null_windows, "flow_veh_h": None},
            }
        },
    }
    reads = [
        {
            "crossings_by_station": {"S1": {"0": 50, "1": 50}},
            "discharge_windows": {
                "S1": {"sim_veh_h": [0.0, 990.0, 1210.0]},
                "S2": {"sim_veh_h": [0.0, 500.0, 500.0]},
            },
        }
    ]
    sc = g._score_detectors(reads, observed)
    assert sc["discharge_veh_h"] == {"S1": pytest.approx(1100.0)}
    assert sc["discharge_veh_h_by_seed"] == {"S1": [pytest.approx(1100.0)]}
    assert sc["discharge_error"] == pytest.approx(0.0)
    assert sc["discharge_stations_not_scored"] == [
        {"id": "S2", "reason": "observed: no flow in any scored window"}
    ]
    # the only discharge station without a target: unscored, inside analyze's except tuple
    only_null = copy.deepcopy(observed)
    del only_null["discharge"]["stations"]["S1"]
    with pytest.raises(ValueError, match=r"no discharge station has an observed flow.*S2"):
        g._score_detectors(reads, only_null)


def _synthetic_observations(tmp_path: Path, stations: list[dict[str, Any]], t0: str) -> Path:
    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "corridor": "I-94 WB",
                "t0_local": t0,
                "window_s": 300.0,
                "stations": stations,
                "flows_veh_h": {s["id"]: [1000.0] * 48 for s in stations},
            }
        )
    )
    return path


def _synthetic_population(path: Path) -> Path:
    IDMCalibration(
        created_at="2026-10-06T00:00:00Z",
        source="synthetic",
        data_hash="0" * 64,
        mean={"v0": 30.0, "T": 1.4, "a_max": 1.0, "b": 1.7, "s0": 2.0},
        cov=np.diag([4.0**2, 0.3**2, 0.3**2, 0.4**2, 0.4**2]).tolist(),
        n_episodes_fit=10,
        n_episodes_holdout=5,
        holdout_gap_rmse_m=4.0,
    ).save(path)
    return path


def _synthetic_scenario(
    tmp_path: Path, pop: Path, *, lanes: int, duration: float, warmup: float, inflow: float = 0.6
) -> Path:
    path = tmp_path / "synthetic.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "synthetic_driver_grid",
                "network": {
                    "kind": "corridor",
                    "length_m": 600.0,
                    "lanes": lanes,
                    "inflow": [[0.0, inflow]],
                },
                "fleet": {"model": "IDM", "idm_calibration": str(pop), "lc_keep_right": 0.0},
                "sim": {"duration_s": duration, "warmup_s": warmup},
                "seed": 7,
                "replicates": 1,
            }
        )
    )
    return path


def _detector_spec(
    tmp_path: Path, scenario: Path, pop: Path, obs: Path, ol: Path | None, **kw: Any
) -> Any:
    return g.CorridorSpec(
        key="synthetic",
        base_scenario=str(scenario),
        transform=None,
        n_seeds=1,
        mem_gb=0.25,
        mode="detectors",
        guard_small_machine=False,
        population_base=str(pop),
        population_dir=str(tmp_path / "pops"),
        population_stem="syn_amax",
        measured_population=str(pop),
        observations=str(obs),
        discharge_stations=("SX",),
        observed_lanes=None if ol is None else str(ol),
        **kw,
    )


def test_stations_are_compared_only_on_matching_cross_sections(tmp_path: Path) -> None:
    pop = _synthetic_population(tmp_path / "pop.json")
    scenario = _synthetic_scenario(tmp_path, pop, lanes=3, duration=2100.0, warmup=300.0)
    stations = [
        {"id": s, "x_m": x} for s, x in (("SX", 100.0), ("SA", 500.0), ("SB", 990.0), ("SC", 300.0))
    ]
    obs = _synthetic_observations(tmp_path, stations, "05:30")
    spec = _detector_spec(tmp_path, scenario, pop, obs, None, clock_offset_s=5400.0)
    cfg = ScenarioConfig.from_yaml(scenario)
    win = g.detector_window(spec, cfg)
    ol = tmp_path / "ol.json"
    ol.write_text(
        json.dumps(
            {
                "schema": g.OBSERVED_LANES_SCHEMA,
                "window": {k: win[k] for k in ("local", "local_start_s", "local_end_s")},
                "dates": ["2026-09-15"],
                "stations": [
                    {"id": "SX", "x_m": 100.0, "lanes": 3, "usable": True, "reason": None, "shares": {"1": 0.2, "2": 0.3, "3": 0.5}},
                    {"id": "SA", "x_m": 500.0, "lanes": 3, "usable": True, "reason": None, "shares": {"1": 0.2, "2": 0.3, "3": 0.5}},
                    {"id": "SB", "x_m": 990.0, "lanes": 3, "usable": True, "reason": None, "shares": {"1": 0.2, "2": 0.3, "3": 0.5}},
                    {"id": "SC", "x_m": 300.0, "lanes": 3, "usable": False, "reason": "detector lanes [1, 2] do not cover lanes [1, 2, 3]", "shares": {}},
                ],
            }
        )
    )  # fmt: skip
    segs = [LaneSegment(0.0, 400.0, 3), LaneSegment(400.0, 1000.0, 4)]
    res = g.observed_detectors(spec, cfg, segs, ol)
    assert [s["id"] for s in res["lane_use"]["stations_compared"]] == ["SX"]
    reasons = {s["id"]: s["reason"] for s in res["lane_use"]["stations_not_compared"]}
    assert "4 lanes" in reasons["SA"]
    assert "4 lanes" in reasons["SB"] or "lane-count change" in reasons["SB"]
    assert reasons["SC"].startswith("observed:")
    assert res["discharge"]["stations"]["SX"]["flow_veh_h"] == pytest.approx(1000.0)
    # built for another window -> refused
    bad = json.loads(ol.read_text())
    bad["window"]["local_start_s"] += 300.0
    ol.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="built for"):
        g.observed_detectors(spec, cfg, segs, ol)


def _write_iris_cache(
    cache: Path, date: str, rates: dict[str, float], seed: int = 0
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    folder = cache / date
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, rate in rates.items():
        counts = rng.poisson(rate, 2880)
        out[name] = counts
        occupancy = np.round(counts * 1.4 + rng.normal(0.0, 0.2, 2880), 2)
        speed = np.where(counts > 0, rng.integers(55, 66, 2880), 0)
        for endpoint, values in (("counts", counts), ("occupancy", occupancy), ("speed", speed)):
            (folder / f"{name}.{endpoint}.json").write_text(json.dumps(values.tolist()))
    return out


def test_observed_lane_shares_from_a_per_lane_cache(tmp_path: Path) -> None:
    """The tiny IRIS fixture (I-94 WB S2105 / S2106, lanes 1-3, 1 = rightmost), 07:05-07:35."""
    rates = {
        "9066": 2.0,
        "9067": 4.0,
        "9068": 6.0,
        "9069": 3.0,
        "9070": 3.0,
        "9071": 6.0,
        "5100": 0.5,
    }
    series = _write_iris_cache(tmp_path / "cache", "20260915", rates)
    pop = _synthetic_population(tmp_path / "pop.json")
    scenario = _synthetic_scenario(tmp_path, pop, lanes=3, duration=2100.0, warmup=300.0)
    obs = _synthetic_observations(
        tmp_path, [{"id": "S2105", "x_m": 100.0}, {"id": "S2106", "x_m": 800.0}], "05:30"
    )
    spec = _detector_spec(tmp_path, scenario, pop, obs, None, clock_offset_s=5400.0)
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps({"calibration_dates": ["2026-09-15"], "selected_stations": ["S2105", "S2106"]})
    )
    cfg = ScenarioConfig.from_yaml(scenario)
    kw = {
        "cache_dir": tmp_path / "cache",
        "metro_config": MNDOT_FIXTURE,
        "day_split": split,
        "allow_fetch": False,
    }
    res = g.build_observed_lanes(spec, cfg, quality=None, exclude_detectors=[], **kw)
    assert res["window"]["local"] == "07:05-07:35"
    lo, hi = (7 * 3600 + 5 * 60) // 30, (7 * 3600 + 35 * 60) // 30  # 30-s bins of the window
    for sid, dets in (("S2105", ("9066", "9067", "9068")), ("S2106", ("9069", "9070", "9071"))):
        st = next(s for s in res["stations"] if s["id"] == sid)
        assert st["usable"] and st["n_date_windows"] == 6
        tot = {str(i + 1): float(series[d][lo:hi].sum()) for i, d in enumerate(dets)}
        s = sum(tot.values())
        assert st["shares"] == {k: pytest.approx(v / s) for k, v in tot.items()}
    # one lane's only detector excluded: the station is not usable, with the reason
    res2 = g.build_observed_lanes(spec, cfg, quality=None, exclude_detectors=["9070"], **kw)
    s2106 = next(s for s in res2["stations"] if s["id"] == "S2106")
    assert not s2106["usable"] and "do not cover lanes [1, 2, 3]" in s2106["reason"]
    # a quality report's excluded detector-day masks the lane: no complete cross-section left
    dq = _load("data_quality_report")
    corridor = tmp_path / "corridor"
    corridor.mkdir()
    (corridor / "selection.json").write_text(
        json.dumps({"route": "I-94", "dir": "WB", "from_station": "S2105", "to_station": "S2106", "dates": ["20260915"], "window_s": 300})
    )  # fmt: skip
    assert dq.main(
        ["--corridor-dir", str(corridor), "--lanes-from-cache", str(tmp_path / "cache"),
         "--metro-config", str(MNDOT_FIXTURE), "--start", "05:30", "--end", "09:30", "--out", str(tmp_path / "dq")]
    ) == 0  # fmt: skip
    qpath = tmp_path / "dq" / "data_quality.json"
    report = json.loads(qpath.read_text())
    for sd in report["sensor_days"]:
        if sd["sensor"] == "S2106:9070":
            sd["verdict"] = "exclude"
    qpath.write_text(json.dumps(report))
    res3 = g.build_observed_lanes(spec, cfg, quality=qpath, exclude_detectors=[], **kw)
    by_id = {s["id"]: s for s in res3["stations"]}
    assert by_id["S2105"]["usable"]
    assert not by_id["S2106"]["usable"] and "every lane" in by_id["S2106"]["reason"]
    assert res3["quality"]["n_masked_sensor_days"] >= 1


def test_observed_lane_shares_read_a_station_reversed_only_when_asked(tmp_path: Path) -> None:
    """A station's IRIS labels are read reversed (lane k as n + 1 - k) when a reviewer
    names it, or when the data-quality report's lane-order check found it reversed and
    the remap is asked for; otherwise the labels stand and the flag is recorded
    (docs/I94_LANE_SHARES.md section 3)."""
    rates = {
        "9066": 2.0,
        "9067": 4.0,
        "9068": 6.0,
        "9069": 3.0,
        "9070": 3.0,
        "9071": 6.0,
        "5100": 0.5,
    }
    _write_iris_cache(tmp_path / "cache", "20260915", rates)
    pop = _synthetic_population(tmp_path / "pop.json")
    scenario = _synthetic_scenario(tmp_path, pop, lanes=3, duration=2100.0, warmup=300.0)
    obs = _synthetic_observations(
        tmp_path, [{"id": "S2105", "x_m": 100.0}, {"id": "S2106", "x_m": 800.0}], "05:30"
    )
    spec = _detector_spec(tmp_path, scenario, pop, obs, None, clock_offset_s=5400.0)
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps({"calibration_dates": ["2026-09-15"], "selected_stations": ["S2105", "S2106"]})
    )
    cfg = ScenarioConfig.from_yaml(scenario)
    kw: dict[str, Any] = {
        "cache_dir": tmp_path / "cache",
        "metro_config": MNDOT_FIXTURE,
        "day_split": split,
        "allow_fetch": False,
        "exclude_detectors": [],
    }
    base = g.build_observed_lanes(spec, cfg, quality=None, **kw)
    by_id = {s["id"]: s for s in base["stations"]}
    assert by_id["S2106"]["lane_order"] == {
        "iris_labels_reversed": False, "by": None, "quality_check": None,
    }  # fmt: skip
    assert base["lane_order"]["flagged_not_remapped"] == []

    rev = g.build_observed_lanes(spec, cfg, quality=None, reverse_lane_order=["S2106"], **kw)
    r_by = {s["id"]: s for s in rev["stations"]}
    flipped = {k: by_id["S2106"]["shares"][str(4 - int(k))] for k in ("1", "2", "3")}
    assert r_by["S2106"]["shares"] == pytest.approx(flipped)
    assert r_by["S2105"]["shares"] == by_id["S2105"]["shares"]
    assert r_by["S2106"]["detectors_by_lane"] == {"1": ["9071"], "2": ["9070"], "3": ["9069"]}
    assert r_by["S2106"]["lane_order"]["iris_labels_reversed"] is True
    assert r_by["S2106"]["lane_order"]["by"].startswith("reviewer")
    assert rev["lane_order"]["reversed_by_reviewer"] == ["S2106"]
    assert rev["provenance"]["reverse_lane_order"] == ["S2106"]
    with pytest.raises(ValueError, match="not stations of the selection"):
        g.build_observed_lanes(spec, cfg, quality=None, reverse_lane_order=["S9"], **kw)
    with pytest.raises(ValueError, match="needs the data-quality report"):
        g.build_observed_lanes(spec, cfg, quality=None, remap_reversed=True, **kw)

    # a report whose lane-order check found S2106 reversed (written into a real report)
    dq = _load("data_quality_report")
    corridor = tmp_path / "corridor"
    corridor.mkdir()
    (corridor / "selection.json").write_text(
        json.dumps({"route": "I-94", "dir": "WB", "from_station": "S2105", "to_station": "S2106", "dates": ["20260915"], "window_s": 300})
    )  # fmt: skip
    assert dq.main(
        ["--corridor-dir", str(corridor), "--lanes-from-cache", str(tmp_path / "cache"),
         "--metro-config", str(MNDOT_FIXTURE), "--start", "05:30", "--end", "09:30", "--out", str(tmp_path / "dq")]
    ) == 0  # fmt: skip
    qpath = tmp_path / "dq" / "data_quality.json"
    report = json.loads(qpath.read_text())
    for rec in report["lane_order"]["stations"]:
        if rec["station"] == "S2106":
            rec.update(verdict="reversed", lanes_reversed=True)
    qpath.write_text(json.dumps(report))
    kept = g.build_observed_lanes(spec, cfg, quality=qpath, **kw)
    k_by = {s["id"]: s for s in kept["stations"]}
    assert k_by["S2106"]["shares"] == pytest.approx(by_id["S2106"]["shares"])
    assert k_by["S2106"]["lane_order"]["quality_check"] == "reversed"
    assert kept["lane_order"]["flagged_not_remapped"] == ["S2106 (reversed)"]
    remapped = g.build_observed_lanes(spec, cfg, quality=qpath, remap_reversed=True, **kw)
    m_by = {s["id"]: s for s in remapped["stations"]}
    assert m_by["S2106"]["shares"] == pytest.approx(flipped)
    assert m_by["S2106"]["lane_order"]["by"].startswith("data-quality report")
    assert remapped["lane_order"]["reversed_by_report"] == ["S2106"]
    assert remapped["lane_order"]["flagged_not_remapped"] == []


# --- analysis on a faked I-24 tree -----------------------------------------------------------


def _fake_i24_tree(root: Path) -> tuple[Any, dict[str, Any]]:
    spec = g.SPECS["i24"]
    plan = g.build_plan(spec)
    pops, measured = g.check_populations(spec, list(K_GRID))
    root.mkdir(parents=True)
    (root / g.MANIFEST).write_text(json.dumps(g._manifest(plan, pops, measured)))
    (root / g.LANES).write_text(
        json.dumps({"lanes": [{"edge": "e", "x_lo": 0.0, "x_hi": 1e4, "n_lanes": 4}]})
    )
    obs = g.observed_i24()
    o = np.array([obs["lane_use"]["shares"][str(n)] for n in (1, 2, 3, 4)])
    q_obs = obs["discharge"]["flows_veh_h"]
    # designed scores: (rmse pp, discharge relative shortfall); the rest RMSE 5, shortfall 0.2
    design = {
        (0.0, 0.0): (2.0, 0.12),
        (0.5, 0.0): (1.5, 0.03),
        (1.0, 0.0): (1.2, 0.01),
        (1.0, 1.0): (4.0, 0.0),
    }
    for name, pair in zip(plan.names, plan.pairs, strict=True):
        rmse, short = design.get(pair, (5.0, 0.2))
        sim = o + rmse / 100.0 * np.array([1.0, -1.0, 1.0, -1.0])
        counts = {str(n): float(1e6 * sim[n - 1]) for n in (1, 2, 3, 4)} | {"5": 0.0}
        rd = {
            "lane_time_counts": {"span": counts, "merge_area": counts},
            "discharge_veh_h": {s: q * (1.0 - short) for s, q in q_obs.items()},
            "run": {"seed": plan.seeds[0], "config_hash": plan.hashes[name], "n_vehicles_planned": 100,
                    "n_vehicles_departed": 98, "departed_share": 0.98, "n_collisions": 0, "wall_time_s": 1.0},
        }  # fmt: skip
        d = root / name / plan.hashes[name] / str(plan.seeds[0])
        d.mkdir(parents=True)
        (d / g.READINGS).write_text(json.dumps(rd))
    return plan, design


def test_analysis_applies_the_rule_to_a_complete_grid(tmp_path: Path) -> None:
    root = tmp_path / "grid"
    plan, _ = _fake_i24_tree(root)
    art = tmp_path / "driver_calibration_i24.json"
    res = g.analyze(g.SPECS["i24"], root, art, argv=["--analyze-only"])
    assert art.is_file() and json.loads(art.read_text())["schema"] == g.SCHEMA
    assert res["complete"] and res["grid"]["is_amendment_grid"]
    by = {(r["k"], r["lc_keep_right"]): r for r in res["pairs"]}
    assert by[(0.0, 0.0)]["lane_rmse_pp"] == pytest.approx(2.0, rel=1e-3)
    assert by[(1.0, 0.0)]["discharge_error"] == pytest.approx(0.01, rel=1e-6)
    sel = res["selection"]
    # band: 1.2 + 1 = 2.2 holds (0,0), (0.5,0), (1,0); the smallest discharge error there is (1, 0)
    assert (sel["chosen"]["k"], sel["chosen"]["lc_keep_right"]) == (1.0, 0.0)
    assert sel["outcome"] == "rule_chose_other"
    assert any("no holdout" in n for n in res["notes"])
    assert res["reference"]["config_hash"] == plan.reference_hash
    # one missing run: no choice is made
    (root / "k0.5_kr0.5" / plan.hashes["k0.5_kr0.5"] / str(plan.seeds[0]) / g.READINGS).unlink()
    res2 = g.analyze(g.SPECS["i24"], root, art)
    assert not res2["complete"] and res2["selection"] is None
    assert any("incomplete" in n for n in res2["notes"])


def test_analysis_refuses_another_corridors_tree(tmp_path: Path) -> None:
    root = tmp_path / "grid"
    _fake_i24_tree(root)
    with pytest.raises(ValueError, match="holds the i24 grid"):
        g.analyze(g.SPECS["i94"], root, tmp_path / "a.json")


# --- the runner on a synthetic corridor ------------------------------------------------------


@pytest.mark.integration
def test_smoke_a_2x2_grid_runs_analyses_and_resumes(tmp_path: Path) -> None:
    pop = _synthetic_population(tmp_path / "pop.json")
    pdir = tmp_path / "pops"
    dp.derive_shifted(
        IDMCalibration.load(pop),
        IDMCalibration.load(pop),
        param="a_max",
        k=0.5,
        base_label="pop",
        measured_label="pop",
        created_at="2026-10-06T00:00:00Z",
    ).save(pdir / dp.out_name("syn_amax", 0.5))
    scenario = _synthetic_scenario(tmp_path, pop, lanes=2, duration=60.0, warmup=0.0)
    obs = tmp_path / "observations.json"
    obs.write_text(
        json.dumps(
            {
                "corridor": "synthetic",
                "t0_local": "00:00",
                "window_s": 30.0,
                "stations": [{"id": "SX", "x_m": 400.0}],
                "flows_veh_h": {"SX": [1500.0, 1500.0]},
            }
        )
    )
    ol = tmp_path / "observed_lanes.json"
    ol.write_text(
        json.dumps(
            {
                "schema": g.OBSERVED_LANES_SCHEMA,
                "window": {"local": "00:00-00:01", "local_start_s": 0.0, "local_end_s": 60.0},
                "dates": ["synthetic"],
                "stations": [
                    {"id": "SX", "x_m": 400.0, "lanes": 2, "usable": True, "reason": None, "shares": {"1": 0.4, "2": 0.6}}
                ],
            }
        )
    )  # fmt: skip
    spec = _detector_spec(tmp_path, scenario, pop, obs, ol)
    plan = g.build_plan(spec, k_grid=(0.0, 0.5), keep_right_grid=(0.0, 1.0))
    root = tmp_path / "grid"
    n_run, n_fail = g.run_grid(spec, plan, root, procs=2, mem_per_run_gb=0.25)
    assert (n_run, n_fail) == (4, 0)
    for name in plan.names:
        run = root / name / plan.hashes[name] / str(plan.seeds[0])
        assert (run / g.READINGS).is_file() and (run / "meta.json").is_file()
        assert not (run / "trajectories.parquet").exists()
    art = tmp_path / "artifact.json"
    res = g.analyze(spec, root, art)
    assert res["complete"] and res["selection"] is not None
    assert not res["grid"]["is_amendment_grid"]
    assert any("NOT the Amendment-1 grid" in n for n in res["notes"])
    assert [s["id"] for s in res["targets"]["lane_use"]["stations_compared"]] == ["SX"]
    for row in res["pairs"]:
        assert row["n_collisions"] == 0
        assert row["lane_rmse_pp"] is not None and row["discharge_error"] is not None
        assert sum(row["lane_shares_by_station"]["SX"]["counts"].values()) > 0
    assert res["pairs"][0]["config_hash"] == plan.reference_hash
    # resumed: nothing pending
    assert g.run_grid(spec, plan, root, procs=2, mem_per_run_gb=0.25) == (0, 0)
