"""scripts/i24_geh_diagnosis.py: C7's classes, order and rule on synthetic batteries.

Each class is pinned on a hand-built bin (docs/PRE_FRISCO_PROGRAM.md C7):

* recording noise: an observed spike at GEH >= 5 from its own centred 15-min
  mean, edges padded with themselves;
* level: the section's 2-h flow at GEH >= 5 and the bin's error of the same
  sign (a bin of the opposite sign falls through);
* timing: a simulated bin within +/-15 min (3 windows, not 4) at GEH < 5;
* shape: the rest; the order decides where the classes overlap.

The rule (timing -> C8, level -> B5/B6, noise -> station-hour amendment, an
insertion offset -> a computed shift) is pinned on class counts, ties
included; the station-hour form is C1's (hours anchored at window 0, every
replicate's station-hours pooled); the insertion offset, the lane sets and the
planned-demand ratio are pinned on synthetic inputs; and the script runs end to
end on JSON files alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from validation.metrics import geh

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SECTIONS = [200.0, 1000.0, 2200.0, 3200.0, 4800.0, 5400.0]
N_WIN = 24


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


D = _load("i24_geh_diagnosis")


# --- synthetic tables ---------------------------------------------------------------


def _profiles() -> tuple[np.ndarray, np.ndarray]:
    """Six sections, one class pinned per section (module docstring)."""
    obs = np.full((6, N_WIN), 6000.0)
    sim = np.full((6, N_WIN), 6000.0)
    # section 0: an observed spike at window 10 (recording noise)
    obs[0, 10] = 9000.0
    # section 1: the model 1,000 veh/h low throughout (level), but high at window 5
    sim[1, :] = 5000.0
    sim[1, 5] = 7000.0
    # section 2: a triangular profile, the model two windows late (timing)
    tri = np.array(
        [4000.0 + 250.0 * w if w <= 12 else 7000.0 - 250.0 * (w - 12) for w in range(N_WIN)]
    )
    obs[2] = tri
    sim[2] = np.array([tri[max(w - 2, 0)] for w in range(N_WIN)])
    # section 3: a 7-window dip and a 7-window hump of equal size (shape inside each)
    sim[3, 7:14] = 3000.0
    sim[3, 17:24] = 9000.0
    return obs, sim


def _battery(
    obs: np.ndarray, sim: np.ndarray, counts: np.ndarray | None = None, *, config_hash: str = "h"
) -> dict[str, Any]:
    vals = [
        round(geh(float(m), float(c)), 3) for m, c in zip(sim.ravel(), obs.ravel(), strict=True)
    ]
    if counts is None:
        counts = np.repeat((sim / 12.0)[None], 2, axis=0)
    return {
        "scenario": "synthetic",
        "config_hash": config_hash,
        "replicates": int(counts.shape[0]),
        "observed": {
            "period": "06:30-08:30 CST",
            "window_s": 300.0,
            "sections_m": SECTIONS[: obs.shape[0]],
            "t_range_s": [1800.0, 1800.0 + 300.0 * obs.shape[1]],
            "hourly_flows_veh_h_recommended": obs.tolist(),
            "data_hash": "d",
        },
        "simulated": {
            "hourly_flows_veh_h_mean": sim.tolist(),
            "counts_per_replicate": counts.tolist(),
            "demand_realized_fraction": [1.0] * int(counts.shape[0]),
        },
        "geh": {
            "vs_recommended_coverage_counts": {
                "values": vals,
                "fraction_under_5": sum(1 for v in vals if v < 5.0) / len(vals),
            }
        },
    }


# --- the four classes ---------------------------------------------------------------


def test_centred_mean_pads_the_edges_with_themselves() -> None:
    out = D.centred_mean(np.array([[1.0, 2.0, 3.0, 4.0]]), 3)
    assert out[0].tolist() == pytest.approx([4.0 / 3.0, 2.0, 3.0, 11.0 / 3.0])
    with pytest.raises(ValueError):
        D.centred_mean(np.zeros((1, 4)), 2)


def test_each_class_is_pinned_on_its_bin() -> None:
    obs, sim = _profiles()
    cls = D.classify(sim, obs, 300.0)
    lab = cls["labels"]
    # recording noise: the spike is 2,000 veh/h above its centred mean 7,000
    assert cls["noise_geh"][0, 10] == pytest.approx(geh(9000.0, 7000.0))
    assert lab[0][10] == "recording_noise"
    # its neighbours are noisy too, but pass: no class
    assert cls["noise"][0, 9] and lab[0][9] is None
    # level: the section is low on 2-h flows; the low bins are level ...
    assert cls["level_2h"][1]["fails"] and cls["level_2h"][1]["sign"] == -1
    assert lab[1][0] == "level" and lab[1][23] == "level"
    # ... the one high bin has the opposite sign and no simulated bin near it fits: shape
    assert lab[1][5] == "shape"
    # timing: two windows late on a ramp; the 2-h flow passes
    assert not cls["level_2h"][2]["fails"]
    assert lab[2][6] == "timing"
    assert int(cls["timing_shift"][2, 6]) == 2
    # shape: in the middle of the dip every simulated bin within 15 min is off
    assert lab[3][10] == "shape" and lab[3][20] == "shape"
    # sections without misses
    assert all(v is None for v in lab[4]) and all(v is None for v in lab[5])


def test_the_timing_tolerance_is_three_windows_not_four() -> None:
    obs, sim = _profiles()
    cls = D.classify(sim, obs, 300.0)
    # window 7 opens the dip: window 6 (one before) fits -> timing
    assert cls["labels"][3][7] == "timing"
    # window 9: window 6 is three windows away and fits -> timing
    assert cls["labels"][3][9] == "timing"
    assert int(cls["timing_shift"][3, 9]) == -3
    # window 10: the nearest fitting simulated bins are four windows away -> shape
    assert cls["labels"][3][10] == "shape"
    assert cls["timing_geh"][3, 10] == pytest.approx(geh(3000.0, 6000.0))


def test_the_order_decides_where_classes_overlap() -> None:
    obs = np.full((1, N_WIN), 6000.0)
    obs[0, 10] = 9000.0
    sim = np.full((1, N_WIN), 5000.0)
    sim[0, 13] = 9000.0  # a simulated bin three windows later fits the spike
    cls = D.classify(sim, obs, 300.0)
    assert cls["noise"][0, 10] and cls["level"][0, 10] and cls["timing"][0, 10]
    assert cls["labels"][0][10] == "recording_noise"
    m = D.membership(cls)
    assert m["patterns"]["recording_noise+level+timing"] == 1
    assert m["meets"]["recording_noise"] >= 1


def test_a_verdict_is_order_robust_only_when_one_class_alone_outnumbers_the_rest() -> None:
    obs, sim = _profiles()
    m = D.membership(D.classify(sim, obs, 300.0))
    # no bin meets two tests here: 29 timing-only bins against 23 level, 7 shape, 1 noise
    assert m["least_under_any_order"] == m["most_under_any_order"]
    assert m["least_under_any_order"]["timing"] == 29 and m["most_under_any_order"]["level"] == 23
    assert m["largest_under_every_order"] == "timing"
    obs = np.full((1, N_WIN), 6000.0)
    obs[0, 10] = 9000.0
    sim = np.full((1, N_WIN), 5000.0)
    sim[0, 13] = 9000.0
    m = D.membership(D.classify(sim, obs, 300.0))
    # 23 failing bins are level, 3 of them noise too: level-only 20 > noise at most 3
    assert m["most_under_any_order"]["recording_noise"] == 3
    assert m["largest_under_every_order"] == "level"
    tie = D.membership(
        {
            "failing": np.array([[True, True]]),
            "noise": np.array([[True, False]]),
            "level": np.array([[False, False]]),
            "timing": np.array([[False, True]]),
        }
    )
    assert tie["largest_under_every_order"] is None


def test_tally_counts_per_section_and_hour() -> None:
    obs, sim = _profiles()
    cls = D.classify(sim, obs, 300.0)
    t = D.tally(cls["labels"], SECTIONS, 300.0)
    assert t["n_fail"] == int(cls["failing"].sum())
    assert sum(t["counts"].values()) == t["n_fail"]
    assert [h["windows"] for h in t["by_hour"]] == [[0, 11], [12, 23]]
    assert sum(h["n_fail"] for h in t["by_hour"]) == t["n_fail"]
    assert t["by_section"][1]["level"] == 23 and t["by_section"][1]["shape"] == 1
    assert t["by_section"][0] == {
        "section_m": 200.0,
        "n_fail": 1,
        "recording_noise": 1,
        "level": 0,
        "timing": 0,
        "shape": 0,
    }


def test_the_battery_row_decides_which_bins_fail() -> None:
    obs, sim = _profiles()
    b = _battery(obs, sim)
    arm = D.diagnose_battery(b, offset_found=None)
    assert arm["row"]["n_fail"] == sum(
        1 for v in b["geh"]["vs_recommended_coverage_counts"]["values"] if v >= 5.0
    )
    assert arm["row"]["recomputed_max_abs_diff"] <= 1e-3
    b["geh"]["vs_recommended_coverage_counts"]["values"][0] = 99.0  # a row that disagrees
    with pytest.raises(ValueError, match="differs from the stored row"):
        D.diagnose_battery(b, offset_found=None)


# --- the rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "flag"),
    [
        ({"recording_noise": 5, "level": 1, "timing": 2, "shape": 0}, "station_hour_amendment"),
        ({"recording_noise": 1, "level": 1, "timing": 4, "shape": 0}, "c8"),
        ({"recording_noise": 1, "level": 6, "timing": 4, "shape": 2}, "back_to_b5_b6"),
    ],
)
def test_the_rule_selects_by_the_largest_class(counts: dict[str, int], flag: str) -> None:
    r = D.apply_rule(counts, offset_found=False)
    flags = ("station_hour_amendment", "c8", "back_to_b5_b6")
    assert [r[f] for f in flags] == [f == flag for f in flags]
    assert not r["tie"] and not r["insertion_offset_correction"]
    assert r["text"].endswith("no insertion offset found")


def test_shape_largest_names_no_action_and_a_tie_lists_both() -> None:
    r = D.apply_rule(
        {"recording_noise": 1, "level": 0, "timing": 0, "shape": 3}, offset_found=False
    )
    assert r["largest"] == ["shape"]
    assert not (r["c8"] or r["back_to_b5_b6"] or r["station_hour_amendment"])
    assert r["actions"] == [D.ACTIONS["shape"]]
    tie = D.apply_rule(
        {"recording_noise": 3, "level": 0, "timing": 3, "shape": 1}, offset_found=False
    )
    assert tie["tie"] and tie["largest"] == ["recording_noise", "timing"]
    assert tie["c8"] and tie["station_hour_amendment"]
    none = D.apply_rule(dict.fromkeys(D.CLASSES, 0), offset_found=None)
    assert none["largest"] == [] and "not assessed" in none["text"]


def test_an_insertion_offset_adds_the_computed_shift_whatever_class_is_largest() -> None:
    r = D.apply_rule({"recording_noise": 0, "level": 0, "timing": 0, "shape": 2}, offset_found=True)
    assert r["insertion_offset_correction"]
    assert r["actions"][-1] == D.OFFSET_ACTION
    assert "before C8" in r["text"]


# --- also reported --------------------------------------------------------------------


def test_station_hours_are_c1s_form() -> None:
    obs = np.full((1, N_WIN), 6000.0)
    counts = np.stack(
        [np.full((1, N_WIN), 500.0), np.full((1, N_WIN), 400.0)]
    )  # 6,000 / 4,800 veh/h
    sim_mean = counts.mean(axis=0) * 12.0
    sh = D.station_hours(obs, counts, sim_mean, [200.0], 300.0, "06:30")
    assert [h["windows"] for h in sh["hours"]] == [[0, 11], [12, 23]]
    assert [h["clock"] for h in sh["hours"]] == ["06:30-07:30", "07:30-08:30"]
    pooled = sh["pooled_over_replicates"]
    assert pooled["n_comparisons"] == 4 and pooled["share"] == 0.5
    assert (
        pooled["per_replicate_share"]["min"] == 0.0 and pooled["per_replicate_share"]["max"] == 1.0
    )
    assert sh["replicate_mean"] == {"n_comparisons": 2, "share": 0.0}  # 5,400 vs 6,000: GEH 7.9
    assert sh["table"][0]["sim_mean_veh_h"] == 5400.0 and sh["table"][0]["replicates_passing"] == 1
    assert "not a criterion" in sh["status"]


def test_the_station_hour_mean_is_the_hours_counts_summed() -> None:
    obs = np.tile(np.arange(N_WIN, dtype=np.float64) * 100.0 + 3000.0, (1, 1))
    counts = np.tile(np.arange(N_WIN, dtype=np.float64)[None, None, :] + 250.0, (3, 1, 1))
    sh = D.station_hours(obs, counts, counts.mean(axis=0) * 12.0, [200.0], 300.0, "06:30")
    assert sh["table"][0]["obs_veh_h"] == pytest.approx(obs[0, :12].mean())
    assert sh["table"][1]["sim_mean_veh_h"] == pytest.approx(counts[0, 0, 12:].sum())


def test_cross_correlation_finds_a_one_window_delay() -> None:
    w = np.arange(N_WIN, dtype=np.float64)
    obs = (6000.0 + 1500.0 * np.exp(-((w - 10.0) ** 2) / 8.0))[None, :]
    sim = np.concatenate([[obs[0, 0]], obs[0, :-1]])[None, :]
    (row,) = D.cross_correlation(sim, obs, [200.0], 300.0)
    assert row["best_lag_windows"] == 1 and row["best_lag_min"] == 5.0
    assert not row["at_search_edge"]
    assert row["lag_parabolic_s"] == pytest.approx(300.0, abs=60.0)
    assert [r["lag_windows"] for r in row["r_by_lag"]] == list(range(-3, 4))


def test_leave_one_out_and_recording_vs_own_mean() -> None:
    same = np.full((3, 1, N_WIN), 500.0)
    loo = D.leave_one_out(same, 300.0, [200.0])
    assert loo["five_min"]["min"] == 1.0 and loo["station_hour"]["max"] == 1.0
    apart = np.stack(
        [np.full((1, N_WIN), 500.0), np.full((1, N_WIN), 500.0), np.full((1, N_WIN), 400.0)]
    )
    loo = D.leave_one_out(apart, 300.0, [200.0])
    assert loo["five_min"]["max"] == 0.0 and loo["station_hour"]["max"] == 0.0
    assert D.leave_one_out(same[:1], 300.0, [200.0])["formed"] is False
    obs, sim = _profiles()
    rv = D.recording_vs_own_mean(D.classify(sim, obs, 300.0), SECTIONS)
    assert rv["by_section"][0]["share_geh_under_5"] == pytest.approx(21 / 24)


def test_counting_noise_scale_is_independent_of_the_count() -> None:
    n = np.array([[100.0] * N_WIN, [400.0] * N_WIN])
    out = D.counting_noise(n, [0.6] * N_WIN, 300.0)
    assert out["five_min"]["geh_one_sd_min"] == pytest.approx(math.sqrt(12.0 / 0.6))
    assert out["five_min"]["geh_one_sd_max"] == pytest.approx(math.sqrt(12.0 / 0.6))
    assert out["station_hour"]["geh_one_sd_max"] == pytest.approx(math.sqrt(1.0 / 0.6))
    assert out["five_min"]["pass_share_if_model_exact"] == pytest.approx(
        math.erf(5.0 / (math.sqrt(2.0) * math.sqrt(20.0)))
    )


def _inputs(shift_s: float = 0.0) -> dict[str, Any]:
    steps = [[0.0, 1.0]] + [[600.0 + 300.0 * i + shift_s, 1.0] for i in range(1, N_WIN)]
    return {
        "geometry": {
            "sim_x_of_data_x": {"a": 1000.0, "b": 1.0},
            "corridor_edges": ["e0", "e1", "e2", "e3"],
            "edge_lengths_m": [1100.0, 900.0, 2000.0, 4000.0],
            "edge_lanes": [4, 5, 4, 4],
        },
        "mainline": {"count_x_m": 200.0, "inflow_steps_sim": steps},
        "study_period": {"warmup_s": 600.0},
        "coverage": {
            "window_s": 900.0,
            "source": {"estimator": "equilibrium", "artifact": None},
            "rows": [{"t_lo_s": 1800.0 + 900.0 * k, "coverage_used": 0.5} for k in range(8)],
        },
    }


def test_the_insertion_offset_is_found_on_an_unshifted_stamp() -> None:
    off = D.insertion_offset(_inputs(), 30.0, [200.0, 1000.0], 300.0)
    assert off["stamping"]["stamped_without_shift"] and off["found"]
    assert off["distance_entry_to_count_section_m"] == 1200.0
    assert off["offset_s"] == pytest.approx(40.0)
    assert off["per_section"][1]["free_flow_from_entry_s"] == pytest.approx(2000.0 / 30.0)
    assert off["per_section"][1]["free_flow_from_count_section_s"] == pytest.approx(800.0 / 30.0)
    shifted = D.insertion_offset(_inputs(-40.0), 30.0, [200.0], 300.0, family_inputs=_inputs())
    assert not shifted["found"] and shifted["stamping"]["max_abs_shift_s"] == pytest.approx(40.0)
    assert shifted["family_check"]["stamped_without_shift"]


def test_lane_sets_flag_a_section_on_a_wider_edge() -> None:
    inp = _inputs()
    obs_rec = np.full((3, N_WIN), 6000.0)
    obs_trk = obs_rec * 0.6
    lanes = D.section_lanes(
        inp["geometry"]["sim_x_of_data_x"],
        inp["geometry"],
        [0.0, 500.0, 2000.0],
        [10.0, 600.0, None],
        obs_trk,
        obs_rec,
    )
    assert [r["edge"] for r in lanes] == ["e0", "e1", "e2"]
    assert [r["sim_counts_extra_lanes"] for r in lanes] == [False, True, False]
    assert lanes[1]["table_coverage"] == pytest.approx(0.6)
    assert lanes[1]["obs_2h_all_lanes_lower_veh_h"] == pytest.approx(6600.0)
    assert lanes[1]["obs_2h_all_lanes_at_table_coverage_veh_h"] == pytest.approx(7000.0)
    (row,) = D.like_for_like(lanes, [5000.0, 7000.0, 6000.0])
    assert row["section_m"] == 500.0
    assert row["geh_all_lanes_lower"] == pytest.approx(round(geh(7000.0, 6600.0), 3))
    assert row["sign_all_lanes_lower"] == 1 and row["sign_all_lanes_at_table_coverage"] == 0
    with pytest.raises(ValueError, match="off the corridor"):
        D.section_lanes(
            inp["geometry"]["sim_x_of_data_x"],
            inp["geometry"],
            [9000.0],
            [None],
            obs_trk[:1],
            obs_rec[:1],
        )


def test_demand_vs_target_is_scale_times_coverage_ratio() -> None:
    rows = [{"t_lo_s": 1800.0, "coverage_used": 0.5}, {"t_lo_s": 2700.0, "coverage_used": 0.6}]
    out = D.demand_vs_target(rows, 900.0, [0.6] * 6, 0.9, 300.0, 1800.0, "06:30")
    assert [r["ratio"] for r in out["by_coverage_window"]] == pytest.approx([0.9 * 0.6 / 0.5, 0.9])
    assert [r["from"] for r in out["by_coverage_window"]] == ["06:30", "06:45"]
    with pytest.raises(ValueError, match="no coverage row"):
        D.demand_vs_target(rows, 900.0, [0.6] * 7, 0.9, 300.0, 1800.0, "06:30")


# --- end to end on JSON files ------------------------------------------------------------


def _write(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload))
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_script_runs_on_json_files_alone(tmp_path: Path) -> None:
    obs = np.full((6, N_WIN), 6000.0)
    for s in range(6):
        obs[s, 4 + 3 * s] = 9500.0  # one spike per section: recording noise dominates
    sim = np.full((6, N_WIN), 6000.0)
    cov = [0.6] * N_WIN
    trk = obs * 0.6
    observed = {
        "period": "06:30-08:30 CST",
        "window_s": 300.0,
        "sections_m": SECTIONS,
        "t_range_s": [1800.0, 9000.0],
        "hourly_flows_veh_h_recommended": obs.tolist(),
        "hourly_flows_veh_h_tracked": trk.tolist(),
        "counts_tracked": (trk / 12.0).tolist(),
        "coverage_recommended_per_window": cov,
        "coverage_recommended_source": "synthetic",
    }
    a = _battery(obs, sim, config_hash="cfgA")
    a["observed"] = dict(observed)
    b = json.loads(json.dumps(a))  # an exact re-run of a
    paths = {
        "a": _write(tmp_path / "a.json", a),
        "b": _write(tmp_path / "b.json", b),
        "observed": _write(tmp_path / "obs.json", observed),
        "cc": _write(
            tmp_path / "cc.json",
            {
                "sections": [
                    {
                        "x_m": x,
                        "flow_veh_h": {
                            "pooled": {"mean": float(obs[i].mean()), "ci_block": [1.0, 2.0]}
                        },
                        "aux_band_tracked_veh_h": 100.0,
                    }
                    for i, x in enumerate(SECTIONS)
                ],
                "verdict": {
                    "outcome": "synthetic",
                    "model": {"config_hash": "cfgA", "artifact": "a.json"},
                    "model_vs_targets": [
                        {"x_m": 2200.0, "geh_2h": geh(float(sim[2].mean()), float(obs[2].mean()))}
                    ],
                },
            },
        ),
        "coverage": _write(
            tmp_path / "cov.json",
            {
                "parameters": {"window_s": 900.0},
                "windows": [
                    {"t_lo_s": 1800.0 + 900.0 * k, "pooled": {"recommended_filled": 0.6}}
                    for k in range(8)
                ],
            },
        ),
        "inputs": _write(tmp_path / "inputs.json", _inputs()),
        "pop": _write(tmp_path / "pop.json", {"mean": {"v0": 30.0}}),
        "scale": _write(tmp_path / "scale.json", {"best": {"scale": 0.9, "config_hash": "cfg0"}}),
    }
    out = tmp_path / "out" / "diag.json"
    rc = D.main(
        [
            "--battery", f"a={paths['a']}",
            "--battery", f"b={paths['b']}",
            "--primary", "a",
            "--out", str(out),
            "--observed", str(paths["observed"]),
            "--count-consistency", str(paths["cc"]),
            "--coverage", str(paths["coverage"]),
            "--replica-inputs", str(paths["inputs"]),
            "--family-inputs", str(paths["inputs"]),
            "--population", str(paths["pop"]),
            "--demand-scale", str(paths["scale"]),
        ]
    )  # fmt: skip
    assert rc == 0
    art = json.loads(out.read_text())
    assert art["schema"] == D.SCHEMA and art["primary"] == "a"
    v = art["verdict"]
    assert v["class_counts"] == {"recording_noise": 6, "level": 0, "timing": 0, "shape": 0}
    assert v["station_hour_amendment"] and not v["c8"] and v["insertion_offset_correction"]
    assert v["insertion_offset_s"] == pytest.approx(40.0)
    assert art["arms"]["a"]["reproduces"] == ["b"] and art["arms"]["b"]["reproduces"] == ["a"]
    assert art["inputs"]["observed"]["sha256"] == _sha(paths["observed"])
    assert art["inputs"]["batteries"][0]["sha256"] == _sha(paths["a"])
    checks = art["checks"]
    assert checks["every_battery_observed_equals_committed"]
    assert checks["coverage_equals_coverage_artifact_4dp"]
    assert checks["r4_measure_reproduced"]["arm"] == "a"
    assert checks["r4_measure_reproduced"]["max_abs_geh_diff"] == pytest.approx(0.0, abs=1e-3)
    assert [r["sim_counts_extra_lanes"] for r in art["lane_sets"]["sections"]] == [
        True, False, False, False, False, False,
    ]  # fmt: skip
    assert art["inputs_vs_targets"]["mean_ratio"] == pytest.approx(0.9 * 0.6 / 0.5)
    assert art["code"]["sha256"]["scripts/i24_geh_diagnosis.py"] == _sha(
        SCRIPTS / "i24_geh_diagnosis.py"
    )
    assert "_counts" not in art["arms"]["a"]
