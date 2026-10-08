"""The range round's readout (artifacts/a3_range_2026-10-07/harness/corridor_a3.py) on synthetic batteries.

docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, items 3-6; docs/A3_RANGE_ROUND.md. Nothing is simulated.

* ``expected``: item 3's figures reproduced from the tracked calibration-day inputs, and the committed
  ``a3_expected.json`` reproduced by the code (its count-data part and, from a netconvert compile, its model part).
* the rule M1-M4 and its "not material" / "inconclusive" branches on constructed contrasts;
* ``evaluate`` end to end on six synthetic batteries (two families, three arms) with their run directories, gates
  and the kept trajectory's lanes; the p10 comparison; problems;
* ``lanes``: per-lane crossings of a cross-section from a synthetic trajectory.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "artifacts" / "a3_range_2026-10-07" / "harness" / "corridor_a3.py"
EXPECTED = REPO_ROOT / "artifacts" / "a3_range_2026-10-07" / "a3_expected.json"


@pytest.fixture()
def a3(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("corridor_a3_under_test", HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "corridor_a3_under_test", mod)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------- expected (item 3)


def test_item3_figures_are_reproduced_from_the_calibration_day_inputs(a3: Any) -> None:
    demand = json.loads((REPO_ROOT / a3.DEMAND).read_text())
    obs = json.loads((REPO_ROOT / a3.CAL_OBS).read_text())
    ramps = {r["name"]: r for r in demand["ramps"]}
    v_on = [q * 3600.0 for _, q in ramps[a3.TH52["entrance"]]["inflow_steps"]]
    p = [f for _, f in ramps[a3.TH52["exit"]]["exit_fraction_steps"]]
    counts = a3.expected_from_counts(v_on, p, obs["flows_veh_h"]["S790"])
    check = a3.against_protocol(counts)
    assert check["all_same"], check
    assert counts["arms"]["u1"]["clipped_clocks"] == [
        "07:30", "07:35", "07:40", "07:45", "07:50", "07:55", "08:05", "08:10",
    ]  # fmt: skip
    assert counts["arms"]["u05"]["clipped_clocks"] == ["07:50"]
    assert counts["lowest_ratio_clock"] == "07:50"
    # monotone in u in every window, u0 is the proportional split
    for w in range(48):
        s = [counts["arms"][a]["s_w"][w] for a in ("u0", "u05", "u1")]
        assert s == sorted(s)
        assert s[0] == pytest.approx(p[w])


def test_the_committed_expected_file_is_what_the_code_gives(a3: Any) -> None:
    doc = json.loads(EXPECTED.read_text())
    assert doc["against_protocol"]["all_same"] is True
    demand = json.loads((REPO_ROOT / a3.DEMAND).read_text())
    obs = json.loads((REPO_ROOT / a3.CAL_OBS).read_text())
    ramps = {r["name"]: r for r in demand["ramps"]}
    counts = a3.expected_from_counts(
        [q * 3600.0 for _, q in ramps[a3.TH52["entrance"]]["inflow_steps"]],
        [f for _, f in ramps[a3.TH52["exit"]]["exit_fraction_steps"]],
        obs["flows_veh_h"]["S790"],
    )
    assert json.loads(json.dumps(counts)) == doc["from_counts"]
    # the model part: a netconvert compile of F2's network (no simulation), both timing rules
    cfg, geom, corridor, v0, sf = a3.network_inputs(f"scenarios/{a3.FAMILIES['F2']['stem']}.yaml")
    for rule in ("arrival", "departure"):
        got = json.loads(json.dumps(a3.expected_under_timing(cfg, geom, corridor, v0, sf, rule)))
        assert got == doc["plan_on_model_volumes"][rule], rule
    arrival = doc["plan_on_model_volumes"]["arrival"]
    assert arrival["free_flow_s"]["corridor entry"] == pytest.approx(424.0, abs=0.1)
    assert arrival["free_flow_s"][a3.TH52["entrance"]] == pytest.approx(37.8, abs=0.1)


# ------------------------------------------------------------------------- the rule (item 5)


def _paired(mean: float, half: float) -> dict[str, float]:
    return {"mean": mean, "lo95": mean - half, "hi95": mean + half, "n": 20, "df": 19}


def _con(
    s790: tuple[float, float] = (0.0, 20.0),
    dep: float = 0.0,
    c1: tuple[float, float] = (0.0, 0.005),
    c3: tuple[float, float] = (0.0, 0.005),
    geh: dict[tuple[str, str], tuple[int, int]] | None = None,
) -> dict[str, Any]:
    stations: dict[str, dict[str, Any]] = {"S790": {}, "S97": {}}
    for st in stations:
        for hour in ("06:30", "07:30", "08:30"):
            arm, ref = (geh or {}).get((st, hour), (3, 3))
            stations[st][hour] = {
                "flow_veh_h": _paired(*s790) if (st, hour) == ("S790", "06:30") else _paired(0, 10),
                "geh_lt5_seeds": {"arm": arm, "ref": ref},
            }
    return {
        "stations": stations,
        "departed": _paired(dep, 0.001),
        "c1_calibration": _paired(*c1),
        "c3_calibration_900": _paired(*c3),
    }


def _arm(collisions: int = 0, front: list[str] | None = None, battery: list[int] | None = None):
    return {
        "collisions": {"total": collisions},
        "locks_front_row": front or [],
        "locks_battery": battery or [],
    }


def test_m1_needs_the_lower_bound_and_the_demand(a3: Any) -> None:
    v = a3.materiality(_arm(), _arm(), _con(s790=(250.0, 40.0)))
    assert v["verdict"] == "material" and v["M1"]["holds"]
    # realised demand more than 1 pp lower: M1 does not hold, and the interval is not within +-100
    v = a3.materiality(_arm(), _arm(), _con(s790=(250.0, 40.0), dep=-0.02))
    assert not v["M1"]["holds"] and v["verdict"] == "inconclusive"
    # the lower bound exactly at +100 holds (>=)
    assert a3.materiality(_arm(), _arm(), _con(s790=(140.0, 40.0)))["M1"]["holds"]
    assert not a3.materiality(_arm(), _arm(), _con(s790=(139.9, 40.0)))["M1"]["holds"]


def test_m2_counts_geh_seeds_per_station_hour(a3: Any) -> None:
    v = a3.materiality(_arm(), _arm(), _con(geh={("S97", "08:30"): (9, 4)}))
    assert v["verdict"] == "material" and v["M2"]["rows"] == [
        {"station": "S97", "hour": "08:30", "u1": 9, "u0": 4}
    ]
    assert not a3.materiality(_arm(), _arm(), _con(geh={("S790", "07:30"): (8, 4)}))["M2"]["holds"]


def test_m3_needs_the_interval_wholly_beyond_two_points(a3: Any) -> None:
    assert a3.materiality(_arm(), _arm(), _con(c1=(0.05, 0.02)))["M3"]["c1_beyond"]
    assert a3.materiality(_arm(), _arm(), _con(c3=(-0.04, 0.01)))["M3"]["c3_beyond"]
    v = a3.materiality(_arm(), _arm(), _con(c1=(0.03, 0.02)))  # 0.01 .. 0.05 straddles 0.02
    assert not v["M3"]["holds"] and v["verdict"] == "not material"


def test_m4_a_collision_or_a_lock_in_one_arm_only(a3: Any) -> None:
    assert a3.materiality(_arm(collisions=1), _arm(), _con())["M4"]["one_arm_only"]["collisions"]
    assert a3.materiality(_arm(), _arm(front=["1:x"]), _con())["verdict"] == "material"
    assert a3.materiality(_arm(battery=[3]), _arm(), _con())["M4"]["holds"]
    # in both arms: not "one arm only"
    v = a3.materiality(_arm(collisions=2), _arm(collisions=1), _con())
    assert not v["M4"]["holds"] and v["verdict"] == "not material"


def test_not_material_needs_the_interval_within_100(a3: Any) -> None:
    assert a3.materiality(_arm(), _arm(), _con(s790=(30.0, 60.0)))["verdict"] == "not material"
    assert a3.materiality(_arm(), _arm(), _con(s790=(60.0, 60.0)))["verdict"] == "inconclusive"
    assert a3.materiality(_arm(), _arm(), _con(s790=(-60.0, 60.0)))["verdict"] == "inconclusive"
    # an input missing: undetermined, never a verdict
    con = _con()
    con["c1_calibration"] = None
    assert a3.materiality(_arm(), _arm(), con)["verdict"] is None


# ------------------------------------------------------------------------- evaluate end to end


TH52 = ("on-ramp 769818012", "off-ramp 18207598")
RUTH = ("on-ramp 745524613", "C-D split 18208090")
GORE = 10700.0


def _write_run(
    root: Path, rel: str, *, share: float, locked: bool, collisions: int, u: float | None
):
    rd = root / rel
    rd.mkdir(parents=True, exist_ok=True)
    ramps = [
        {
            "name": RUTH[0],
            "kind": "on",
            "attach_x_m": 5180.0,
            "attach_end_x_m": 5450.0,
            "n_departed": 1000,
        },
        {"name": RUTH[1], "kind": "off", "attach_x_m": 5300.0, "attach_end_x_m": None},
        {
            "name": TH52[0],
            "kind": "on",
            "attach_x_m": 10400.0,
            "attach_end_x_m": GORE,
            "n_departed": 5000,
        },
        {"name": TH52[1], "kind": "off", "attach_x_m": 10700.0, "attach_end_x_m": None},
    ]
    weave = {
        "n_missed_exit": 3,
        "n_entrant_took_exit": 12,
        "n_changed_in": 900,
        "n_changed_out": 400,
        "n_handback_skips": 1,
        "n_close_leader_withheld": 2,
        "n_opposing_deferred": 3,
        "n_opposing_vetoed": 4,
    }
    meta: dict[str, Any] = {
        "config": {
            "sim": {"duration_s": 14400.0},
            "network": {"ramps": [{"name": r["name"]} for r in ramps]},
        },
        "ramps": ramps,
        "weave_sections": [
            {"ramp": RUTH[0], "exit": RUTH[1], **weave},
            {"ramp": TH52[0], "exit": TH52[1], **weave},
        ],
        "collisions": [{"lane": "51388891_0"}] * collisions,
    }
    if u is not None:
        meta["ramp_to_ramp_shares"] = [
            {"ramp": TH52[0], "form": "per_window", "u": u, "s_max": 0.7, "share_drawn": 0.2,
             "share_realized": share, "n_entrants": 100, "n_ramp_to_ramp": round(100 * share),
             "n_swapped_to_exit": 10, "n_swapped_from_exit": 0, "n_clipped": 8 if u == 1.0 else 0,
             "clipped_windows_t0_s": [], "free_flow_s": {}}
        ]  # fmt: skip
    (rd / "meta.json").write_text(json.dumps(meta))
    n_rr = round(100 * share)
    rows = [
        {"veh_id": f"t{i}", "origin": TH52[0], "destination": TH52[1] if i < n_rr else "corridor_end",
         "arrived": True, "last_t_s": 100.0, "last_x_m": 11000.0, "last_lane": 1}
        for i in range(100)
    ]  # fmt: skip
    if locked:  # a front at the gore and nobody past it at the end
        rows.append({"veh_id": "L", "origin": TH52[0], "destination": "corridor_end", "arrived": False,
                     "last_t_s": 14400.0, "last_x_m": GORE - 5.0, "last_lane": 0})  # fmt: skip
    else:
        rows.append({"veh_id": "P", "origin": "mainline", "destination": "corridor_end", "arrived": False,
                     "last_t_s": 14400.0, "last_x_m": GORE + 50.0, "last_lane": 1})  # fmt: skip
    pd.DataFrame(rows).to_parquet(rd / "vehicles.parquet")
    routes = ["on2_off3"] * n_rr + ["on2"] * (100 - n_rr) + ["main"] * 50
    pd.DataFrame({"route": routes}).to_parquet(rd / "journeys.parquet")


def _battery(
    a3: Any, root: Path, label: str, scenario: str, *, s790: list[float], u: float | None,
    share: float = 0.18, collisions: dict[int, int] | None = None, locked: tuple[int, ...] = (),
    config_hash: str = "aaaaaaaaaaaa",
) -> list[int]:  # fmt: skip
    seeds = a3.seeds_of_record()
    per = []
    for i, seed in enumerate(seeds):
        rel = f"runs/{label}/baseline/{config_hash}/{seed}"
        n_col = (collisions or {}).get(i, 0)
        _write_run(root, rel, share=share, locked=i in locked, collisions=n_col, u=u)
        hours = []
        for h, clock in enumerate(("06:30", "07:30", "08:30")):
            for st, x in (("S790", 10300.0), ("S97", 11000.0)):
                sim = s790[i] + 100 * h if st == "S790" else 3000.0 + 7 * i
                hours.append({"station": st, "x_ref_m": x, "window_start_s": 3600.0 * (h + 1),
                              "clock": clock, "obs_veh_h": 4846.0, "sim_veh_h": sim,
                              "geh": abs(sim - 4846.0) / 30.0})  # fmt: skip
        per.append({
            "seed": seed, "run_dir": rel, "n_collisions": n_col, "link_hours": hours,
            "insertion": {"departed_fraction": 0.98, "ramps": [{"name": TH52[0], "fraction": 0.9}]},
            "locks": {"locked": i in locked, "n_locks": int(i in locked)},
        })  # fmt: skip
    art = {
        "schema": "flowstate.corridor_validation/1", "scenario": scenario, "config_hash": config_hash,
        "seeds": seeds, "per_seed": per, "observations": {"path": a3.CAL_OBS},
        "criteria_profile": {"name": "fhwa_tat3_2004"},
        "insertion": {"mean_departed_fraction": 0.98, "min_departed_fraction": 0.97},
        "collisions": {"total": sum((collisions or {}).values())},
    }  # fmt: skip
    (root / "artifacts").mkdir(exist_ok=True)
    (root / "artifacts" / f"validation_{label}.json").write_text(json.dumps(art))
    return seeds


def _gate(root: Path, label: str, config_hash: str, c1: list[float], c3: list[float]) -> None:
    checks = [
        {"check": c, "day_set": d, "value": v, "status": "fail"}
        for c, d, v in (("C1", "calibration", 0.36), ("C3", "calibration", 0.38), ("C4", "calibration", 5.7),
                        ("C6", "calibration", 0.0), ("C1", "validation", 0.33), ("C3", "validation", 0.37),
                        ("C6", "validation", 0.0))
    ]  # fmt: skip
    gate = {
        "schema": "flowstate.baseline_gate/1", "config_hash": config_hash, "n_replicates": 20, "checks": checks,
        "day_sets": {"calibration": {"geh": {"pass_fraction_ci": {"mean": sum(c1) / len(c1)}},
                                     "rmspe": {"900": {"per_replicate": c3}}}},
    }  # fmt: skip
    (root / "artifacts" / f"baseline_gate_{label}.json").write_text(json.dumps(gate))


@pytest.fixture()
def world(a3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, dict]:
    """Six batteries: F2 with a +300 veh/h shift at u1 (material by M1), F1 with none (not material)."""
    monkeypatch.setattr(a3, "REPO", tmp_path)
    monkeypatch.chdir(tmp_path)
    c1c3: dict[str, dict[str, list[float]]] = {}
    noise = [((i * 37) % 11 - 5) * 4.0 for i in range(20)]
    for fam, shift in (("F2", 300.0), ("F1", 0.0)):
        for arm, u in a3.ARMS.items():
            lab = a3.label_of(fam, arm)
            h = a3.FAMILIES["F2"]["source_hash_v4"] if (fam, arm) == ("F2", "u0") else "b" * 12
            base = [3880.0 + noise[i] for i in range(20)]
            s790 = [x + shift * u + (noise[(i + 3) % 20] if u else 0.0) for i, x in enumerate(base)]
            seeds = _battery(a3, tmp_path, lab, a3.scenario_of(fam, arm), s790=s790,
                             u=None if arm == "u0" else u, share=0.18 + 0.52 * u, config_hash=h)  # fmt: skip
            c1 = [0.36 + 0.001 * i for i in range(20)]
            c3 = [0.38 + 0.001 * ((i * 7) % 5) for i in range(20)]
            c1c3[lab] = {"seeds": seeds, "c1": c1, "c3_900": c3}
            _gate(tmp_path, lab, h, c1, c3)
        # p10's battery of the same physics (the u0 arm is compared with it seed by seed)
        p10 = a3.FAMILIES[fam]["p10"]
        _battery(a3, tmp_path, p10, "scenarios/x.yaml", s790=[3880.0 + n for n in noise], u=None)
    monkeypatch.setattr(
        a3, "c1_c3_per_replicate", lambda bat: c1c3[bat["per_seed"][0]["run_dir"].split("/")[1]]
    )
    return a3, tmp_path, c1c3


def test_evaluate_reads_every_arm_and_decides_each_family(world: tuple[Any, Path, dict]) -> None:
    a3, root, _ = world
    rc = a3.main(["evaluate", "--out", str(root / "artifacts" / "a3_range.json")])
    doc = json.loads((root / "artifacts" / "a3_range.json").read_text())
    assert doc["problems"] == [] and rc == 0
    f2, f1 = doc["families"]["F2"], doc["families"]["F1"]
    assert f2["verdict_u1"]["verdict"] == "material" and f2["verdict_u1"]["M1"]["holds"]
    assert f1["verdict_u1"]["verdict"] == "not material"
    # u05 has contrasts and no verdict
    assert set(f2["contrasts"]) == {"u05", "u1"}
    c = f2["contrasts"]["u05"]["stations"]["S790"]["06:30"]["flow_veh_h"]
    assert c["n"] == 20 and c["df"] == 19 and c["mean"] == pytest.approx(150.0, abs=5.0)
    # the readings of item 4
    u1 = f2["arms"]["u1"]
    assert (
        u1["collisions"]["total"] == 0 and u1["locks_front_row"] == [] and u1["locks_battery"] == []
    )
    th = u1["weaves"][TH52[0]]
    assert th["n_entrant_took_exit"] == 12 * 20 and th["entrance_departed"] == 5000 * 20
    assert th["w1b_release_share"] == pytest.approx(12 / 5000)
    assert th["n_changed_in"] == 900 * 20 and th["n_opposing_vetoed"] == 4 * 20
    share = u1["share"]
    assert share["planned_share"]["mean"] == pytest.approx(0.70)
    assert share["departed_share"]["mean"] == pytest.approx(0.70)
    assert share["clipped_windows"]["mean"] == 8
    assert f2["arms"]["u0"]["share"]["planned_share"]["mean"] == pytest.approx(0.18)
    assert u1["c1_c3_against_gate"]["consistent"] is True
    assert u1["gate"]["C4 calibration"]["value"] == 5.7
    # the range reading beside every headline
    rng = f2["range"]
    assert rng["label"].startswith("range over the T.H.52 ramp-to-ramp share")
    assert rng["u0_u1"]["S790 06:30 veh/h"][1] - rng["u0_u1"]["S790 06:30 veh/h"][
        0
    ] == pytest.approx(300.0, abs=5.0)
    # u0 against p10's battery: identical except what the synthetic batteries made differ
    cmp = f2["u0_against_p10"]
    assert cmp["departed_unequal"] == [] and len(cmp["per_seed"]) == 20
    assert "fields_new" in cmp["per_seed"][0]


def test_a_collision_in_one_arm_is_material_and_a_lock_is_read_by_both_readers(
    world: tuple[Any, Path, dict],
) -> None:
    a3, root, c1c3 = world
    lab = a3.label_of("F1", "u1")
    seeds = _battery(a3, root, lab, a3.scenario_of("F1", "u1"), s790=[3880.0] * 20, u=1.0,
                     share=0.7, collisions={4: 1}, locked=(7,), config_hash="b" * 12)  # fmt: skip
    _gate(root, lab, "b" * 12, c1c3[lab]["c1"], c1c3[lab]["c3_900"])
    assert seeds == a3.seeds_of_record()
    doc = a3.evaluate()
    v = doc["families"]["F1"]["verdict_u1"]
    assert v["verdict"] == "material" and v["M4"]["one_arm_only"] == {
        "collisions": True,
        "locks_front_row": True,
        "locks_battery": True,
    }
    arm = doc["families"]["F1"]["arms"]["u1"]
    assert arm["locks_front_row"] == [f"{seeds[7]}:{TH52[0]}"]
    assert arm["collisions"]["by_section"][4] == {"T.H.52": 1}


def test_problems_leave_the_verdict_undetermined(world: tuple[Any, Path, dict]) -> None:
    a3, root, _ = world
    (root / "artifacts" / f"validation_{a3.label_of('F2', 'u1')}.json").unlink()
    gate = root / "artifacts" / f"baseline_gate_{a3.label_of('F1', 'u0')}.json"
    g = json.loads(gate.read_text())
    g["day_sets"]["calibration"]["geh"]["pass_fraction_ci"]["mean"] = 0.5
    gate.write_text(json.dumps(g))
    rc = a3.main(["evaluate", "--out", str(root / "a3_range.json")])
    doc = json.loads((root / "a3_range.json").read_text())
    assert rc == 3
    assert doc["families"]["F2"]["verdict_u1"]["verdict"] is None
    assert doc["families"]["F1"]["verdict_u1"]["verdict"] is None
    assert any("is missing" in p for p in doc["problems"])
    assert any("do not reproduce the gate" in p for p in doc["problems"])


def test_the_committed_u0_must_hash_as_committed(world: tuple[Any, Path, dict]) -> None:
    a3, root, _ = world
    lab = a3.label_of("F2", "u0")
    art = root / "artifacts" / f"validation_{lab}.json"
    d = json.loads(art.read_text())
    d["config_hash"] = "c" * 12
    art.write_text(json.dumps(d))
    doc = a3.evaluate()
    assert any("not the committed file's 395a111cb991" in p for p in doc["problems"])


# ------------------------------------------------------------------------- lanes


def test_lane_crossings_count_each_vehicle_once_in_its_hour_and_lane(a3: Any) -> None:
    rows = []
    for v, (t0, lane) in enumerate(
        [(3590.0, 0), (3605.0, 0), (3610.0, 1), (7300.0, 2), (100.0, 1)]
    ):
        for k in range(4):  # 1 Hz, 25 m/s, crossing x = 1,000 m between the second and third sample
            rows.append({"t": t0 + k, "veh_id": f"v{v}", "x": 960.0 + 25.0 * k, "lane": lane})
    traj = pd.DataFrame(rows)
    got = a3.lane_crossings(traj, 1000.0, [("06:30", 3600.0), ("07:30", 7200.0)])
    # v0 crosses at 3591.6 s (before 06:30), v4 in the warm-up: neither counted
    assert got == {"06:30": {"0": 1, "1": 1}, "07:30": {"2": 1}}
    assert math.isclose(sum(sum(h.values()) for h in got.values()), 3)


def test_lanes_reads_the_kept_seed_and_refuses_another(
    a3: Any, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(a3, "REPO", tmp_path)
    monkeypatch.chdir(tmp_path)
    lab = "lab"
    _battery(a3, tmp_path, lab, "scenarios/x.yaml", s790=[4000.0] * 20, u=None)
    rd = (
        tmp_path
        / json.loads((tmp_path / "artifacts" / f"validation_{lab}.json").read_text())["per_seed"][
            0
        ]["run_dir"]
    )
    rows = []
    for v in range(6):
        for k in range(30):
            rows.append({"t": 3600.0 + 10 * v + k, "veh_id": f"v{v}", "x": 10290.0 + 25.0 * k,
                         "lane": v % 3, "v": 25.0, "a": 0.0})  # fmt: skip
    pd.DataFrame(rows).to_parquet(rd / "trajectories.parquet")
    assert a3.main(["lanes", "--label", lab]) == 0
    rec = json.loads((tmp_path / "artifacts" / f"a3_lanes_{lab}.json").read_text())
    assert rec["seed"] == a3.KEPT_SEED
    assert rec["x_ref_m"] == {"S790": 10300.0, "gore": GORE - 1.0}
    assert rec["veh_h_by_lane"]["S790"]["06:30"] == {"0": 2, "1": 2, "2": 2}
    assert rec["veh_h_by_lane"]["gore"]["06:30"] == {"0": 2, "1": 2, "2": 2}
    (rd / "trajectories.parquet").unlink()
    assert a3.main(["lanes", "--label", lab]) == 3
    rec = json.loads((tmp_path / "artifacts" / f"a3_lanes_{lab}.json").read_text())
    assert any("missing" in p for p in rec["problems"])
