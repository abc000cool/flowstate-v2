"""E11's readout harness (artifacts/i80_merge_2026-10-07/harness/corridor_e11.py) and its stage, offline.

No simulation: synthetic observed and simulated measure artifacts in the shapes
``scripts/i80_merge_measures.py`` writes. Covered: E1-E4 and the rescue rule as fixed before the
run (docs/PRE_FRISCO_PROGRAM.md, E11; Amendment M1), the six interval checks with their n, an
underpowered model interval, an unevaluable I-80 interval, the readings reported beside (speed
profile, gap ratios, departures), the blocking checks, the ``merge: measured`` copy of the kept
document (and its refusals) and the stage snippet's syntax and references.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "artifacts" / "i80_merge_2026-10-07" / "harness" / "corridor_e11.py"
STAGE = REPO_ROOT / "artifacts" / "i80_merge_2026-10-07" / "stage_p21_e11.sh.txt"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p21_harness_corridor_e11", HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


h = _load()
SEEDS = h.SEEDS
KEYS = ("0", "2", "5", "10")
WINDOWS = [
    {"period": "p2", "lo_s": 0.0, "hi_s": 850.0},
    {"period": "p3", "lo_s": 850.0, "hi_s": 1700.0},
]
ZONE = {"name": "z", "kind": "merge", "x_lo_m": 112.4, "x_hi_m": 318.7}


def observed(**over: Any) -> dict[str, Any]:
    ratios = {
        "follower": {
            k: {"ratio_pop": {"n": 300, "median": 0.7 + 0.03 * i, "ci95": [0.6, 0.9]}}
            for i, k in enumerate(KEYS)
        },
        "leader": {
            k: {"ratio_pop": {"n": 280, "median": 0.6 + 0.04 * i, "ci95": [0.5, 0.8]}}
            for i, k in enumerate(KEYS)
        },
    }
    m = {
        "n_changes": 400,
        "accepted_gap_s": {
            "lead": {"n": 400, "median": 0.8, "ci95": [0.7, 0.9]},
            "lag": {"n": 395, "median": 1.1, "ci95": [1.0, 1.2]},
        },
        "critical_gap_s": {
            "fitted": True,
            "n_used": 380,
            "n_with_rejection": 210,
            "n_inconsistent": 20,
            "lead": {"median": 0.5, "ci95": [0.4, 0.6], "at_bound": False},
            "lag": {"median": 0.7, "ci95": [0.6, 0.8], "at_bound": False},
        },
        "partner_speed_ms": {
            "follower": {"n": 300, "median": 0.7, "ci95": [0.5, 0.9]},
            "leader": {"n": 280, "median": -0.5, "ci95": [-0.7, -0.3]},
        },
        "gap_ratio": ratios,
        "accel_lane_speed_ms": [
            {
                "bin_lo_m": 0.0,
                "bin_hi_m": 50.0,
                "n": 900,
                "median": 8.0,
                "ci95": [7.8, 8.2],
                "mean": 8.1,
            },
            {
                "bin_lo_m": 50.0,
                "bin_hi_m": 100.0,
                "n": 800,
                "median": 9.0,
                "ci95": [8.8, 9.2],
                "mean": 9.1,
            },
        ],
    }
    for k, v in over.items():
        m[k] = v
    return {
        "data_hash": "d" * 64,
        "data_version": "raw NGSIM I-80",
        "block": ["p2", "p3"],
        "site_length_m": 503.0,
        "zone": ZONE,
        "windows": WINDOWS,
        "measures": m,
    }


def seed_entry(seed: int, i: int, **v: float) -> dict[str, Any]:
    """One seed's point values; ``v`` overrides the defaults (which sit inside I-80's intervals)."""
    j = (i % 5 - 2) * 0.01  # a small spread across seeds
    val = {
        "lead": 0.8 + j,
        "lag": 1.1 + j,
        "cg_lead": 0.5 + j,
        "cg_lag": 0.7 + j,
        "follower": 0.7 + j,
        "leader": -0.5 + j,
        **v,
    }
    return {
        "seed": seed,
        "accepted_gap_s": {"lead": {"median": val["lead"]}, "lag": {"median": val["lag"]}},
        "critical_gap_s": {
            "fitted": True,
            "lead": {"median": val["cg_lead"], "at_bound": bool(v.get("at_bound", False))},
            "lag": {"median": val["cg_lag"], "at_bound": False},
        },
        "partner_speed_ms": {
            "follower": {"median": val["follower"]},
            "leader": {"median": val["leader"]},
        },
        "gap_ratio": {
            s: {k: {"ratio_pop": {"median": 0.7 + 0.01 * int(k), "n": 20}} for k in KEYS}
            for s in ("follower", "leader")
        },
        "accel_lane_speed_ms": [{"median": 8.0 + j, "n": 40}, {"median": 9.0 + j, "n": 30}],
    }


def simulated(
    arm: str, *, per_seed=None, pooled=None, collisions=None, departed=None
) -> dict[str, Any]:
    name = h.SCENARIO_NAMES[arm]
    cfg_hash = "aaaaaaaaaaaa" if arm == "kept" else "bbbbbbbbbbbb"
    runs = [
        {
            "seed": s,
            "config_hash": cfg_hash,
            "scenario": name,
            "n_collisions": (collisions or {}).get(i, 0),
            "departed_share": (departed or {}).get(i, 0.99),
            "measured_merges": [{"kind": "merge"}] if arm == "measured" else None,
        }
        for i, s in enumerate(SEEDS)
    ]
    entries = [seed_entry(s, i) for i, s in enumerate(SEEDS)]
    for i, over in (per_seed or {}).items():
        entries[i] = seed_entry(SEEDS[i], i, **over)
    pooled_block = {
        "n_changes": 7000,
        "partner_speed_ms": {
            "follower": {"median": 0.6, "n": 6000},
            "leader": {"median": -0.4, "n": 5800},
        },
        "gap_ratio": {
            "follower": {
                k: {"ratio_pop": {"median": 0.8 + 0.02 * i, "n": 5000}} for i, k in enumerate(KEYS)
            },
            "leader": {
                k: {"ratio_pop": {"median": 0.7 + 0.03 * i, "n": 4800}} for i, k in enumerate(KEYS)
            },
        },
        "accel_lane_speed_ms": [{"median": 8.1, "n": 900}, {"median": 9.1, "n": 800}],
    }
    for k, v in (pooled or {}).items():
        pooled_block[k] = v
    return {
        "kind": "simulated",
        "arm": arm,
        "observed_artifact": {"path": "artifacts/i80_merge_observed.json", "data_hash": "d" * 64},
        "windows": WINDOWS,
        "zone_problems": [],
        "runs": runs,
        "per_seed": entries,
        "pooled": pooled_block,
    }


def _readout(
    tmp_path: Path, obs: dict[str, Any], kept: dict[str, Any], meas: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    paths = {}
    for name, doc in (("obs", obs), ("kept", kept), ("meas", meas)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(doc))
    out = tmp_path / "validation.json"
    rc = h.readout(paths["obs"], paths["kept"], paths["meas"], out)
    return rc, json.loads(out.read_text())


# --- the criteria and the rescue -------------------------------------------------------------------


def test_measured_rescued_when_it_meets_all_and_the_kept_fails_e3(tmp_path: Path) -> None:
    kept = simulated("kept", per_seed={i: {"lead": 1.5 + 0.001 * i} for i in range(20)})
    rc, doc = _readout(tmp_path, observed(), kept, simulated("measured"))
    assert rc == 0 and doc["problems"] == []
    meas, kc = doc["criteria"]["measured"], doc["criteria"]["kept"]
    assert all(meas[k]["verdict"] is True for k in ("E1", "E2", "E3", "E4"))
    assert kc["E3"]["verdict"] is False and kc["not_met"] == ["E3"]
    lead = kc["E3"]["checks"][0]
    assert (lead["quantity"], lead["side"], lead["overlaps"]) == ("accepted_gap_s", "lead", False)
    assert lead["model"]["n"] == 20 and lead["observed"]["n"] == 400
    assert doc["rescue"]["rescued"] is True and doc["reading"] is True
    assert doc["reading_text"] == "measured is rescued"


def test_not_rescued_when_both_meet_all(tmp_path: Path) -> None:
    rc, doc = _readout(tmp_path, observed(), simulated("kept"), simulated("measured"))
    assert rc == 0
    assert doc["criteria"]["kept"]["meets_all"] and doc["criteria"]["measured"]["meets_all"]
    assert doc["rescue"]["rescued"] is False and doc["reading"] is False


def test_a_collision_fails_e4_and_the_rescue(tmp_path: Path) -> None:
    kept = simulated("kept", per_seed={i: {"lead": 1.5} for i in range(20)})
    meas = simulated("measured", collisions={7: 1})
    _, doc = _readout(tmp_path, observed(), kept, meas)
    e4 = doc["criteria"]["measured"]["E4"]
    assert e4["verdict"] is False and e4["collisions_total"] == 1
    assert doc["rescue"]["rescued"] is False


def test_e1_reads_the_signs_and_e2_the_short_gap_and_its_recovery(tmp_path: Path) -> None:
    flipped = {"follower": {"median": -0.2, "n": 6000}, "leader": {"median": -0.4, "n": 5800}}
    bad_ratio = {
        "follower": {
            k: {"ratio_pop": {"median": v, "n": 5000}}
            for k, v in zip(KEYS, (0.95, 0.96, 0.97, 0.98), strict=True)
        },
        "leader": {
            k: {"ratio_pop": {"median": v, "n": 4800}}
            for k, v in zip(KEYS, (0.7, 0.7, 0.7, 0.69), strict=True)
        },
    }
    meas = simulated("measured", pooled={"partner_speed_ms": flipped, "gap_ratio": bad_ratio})
    _, doc = _readout(tmp_path, observed(), simulated("kept"), meas)
    e1 = doc["criteria"]["measured"]["E1"]
    assert e1["verdict"] is False
    assert (
        e1["sides"]["follower"]["signs_match"] is False
        and e1["sides"]["leader"]["signs_match"] is True
    )
    e2 = doc["criteria"]["measured"]["E2"]["sides"]
    assert (
        e2["follower"]["at_change_within_cap"] is False
        and e2["follower"]["recovers_by_10s"] is True
    )
    assert e2["leader"]["at_change_within_cap"] is True and e2["leader"]["recovers_by_10s"] is False
    assert e2["follower"]["observed_i80_at_0s"] == pytest.approx(0.7)
    assert doc["criteria"]["measured"]["E2"]["verdict"] is False


def test_an_underpowered_model_interval_does_not_overlap(tmp_path: Path) -> None:
    kept = simulated("kept", per_seed={i: {"lead": 1.5} for i in range(20)})
    meas = simulated("measured", per_seed={i: {"at_bound": True} for i in (0, 4, 9)})
    _, doc = _readout(tmp_path, observed(), kept, meas)
    cg = next(
        c
        for c in doc["criteria"]["measured"]["E3"]["checks"]
        if c["quantity"] == "critical_gap_s" and c["side"] == "lead"
    )
    assert cg["model"]["n"] == 17 and cg["model"]["underpowered"] is True
    assert cg["overlaps"] is False and "underpowered" in cg["reason"]
    assert cg["model"]["per_seed"][0] is None
    assert doc["rescue"]["rescued"] is False


def test_an_unfitted_i80_critical_gap_leaves_e3_unevaluable(tmp_path: Path) -> None:
    obs = observed(critical_gap_s={"fitted": False, "n_used": 12, "lead": None, "lag": None})
    _, doc = _readout(tmp_path, obs, simulated("kept"), simulated("measured"))
    for arm in ("kept", "measured"):
        e3 = doc["criteria"][arm]["E3"]
        assert e3["verdict"] is None
        assert [c["overlaps"] for c in e3["checks"]] == [True, True, None, None, True, True]
        assert doc["criteria"][arm]["not_met"] == ["E3"]
    # the measured arm cannot meet E3, so it is not rescued although the kept arm "fails" it too
    assert doc["rescue"]["rescued"] is False


def test_the_reported_readings(tmp_path: Path) -> None:
    meas = simulated("measured", departed={3: 0.5, 4: 0.85})
    _, doc = _readout(tmp_path, observed(), simulated("kept"), meas)
    rep = doc["reported"]
    prof = rep["accel_lane_speed_ms_by_50m"]
    assert [r["bin_lo_m"] for r in prof] == [0.0, 50.0]
    assert prof[0]["observed"]["median"] == 8.0
    assert (
        prof[0]["kept"]["n"] == 20 and prof[0]["kept"]["ci95"][0] < 8.0 < prof[0]["kept"]["ci95"][1]
    )
    assert prof[0]["measured"]["pooled_median"] == 8.1
    gr = rep["gap_ratios"]["follower"]["10"]
    assert gr["observed"]["median"] == pytest.approx(0.79) and gr["kept"][
        "pooled_median"
    ] == pytest.approx(0.86)
    dep = rep["departures"]["measured"]
    assert dep["no_locks"] is False and dep["lock_seeds"] == [SEEDS[3]]
    assert dep["breakdown_seeds"] == [SEEDS[3], SEEDS[4]]
    assert rep["departures"]["kept"]["no_locks"] is True


def test_inconsistent_inputs_block_the_reading(tmp_path: Path) -> None:
    kept = simulated("kept")
    kept["runs"][2]["config_hash"] = "cccccccccccc"
    kept["runs"][5]["measured_merges"] = [{"kind": "merge"}]
    meas = simulated("measured")
    meas["runs"] = meas["runs"][::-1]
    meas["windows"] = WINDOWS[:1]
    meas["zone_problems"] = [{"seed": 1}]
    rc, doc = _readout(tmp_path, observed(), kept, meas)
    assert rc == h.EXIT_BLOCKED and doc["reading"] is None
    text = " | ".join(doc["problems"])
    for needle in (
        "several config hashes",
        "kept: a run with a measured merge zone",
        "not spawn_seeds",
        "windows differ",
        "merge zone more than",
    ):
        assert needle in text
    other = simulated("measured")
    other["observed_artifact"]["data_hash"] = "e" * 64
    rc, doc = _readout(tmp_path, observed(), simulated("kept"), other)
    assert rc == h.EXIT_BLOCKED and "another observed artifact" in " ".join(doc["problems"])


def test_interval_helpers() -> None:
    assert h.overlaps([0.0, 1.0], [1.0, 2.0]) is True
    assert h.overlaps([0.0, 0.99], [1.0, 2.0]) is False
    assert h.overlaps(None, [1.0, 2.0]) is None
    iv = h.model_interval([1.0, 2.0, None, float("nan"), 3.0])
    assert iv["n"] == 3 and iv["underpowered"] and iv["mean"] == pytest.approx(2.0)
    assert h.model_interval([None])["ci95"] is None
    assert (
        h._all([True, None]) is None
        and h._all([True, False, None]) is False
        and h._all([True]) is True
    )


# --- the measured arm's document --------------------------------------------------------------------


def _kept_doc(tmp_path: Path) -> Path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "e11h_build", REPO_ROOT / "scripts" / "i80_build_replica.py"
    )
    assert spec is not None and spec.loader is not None
    b = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = b
    spec.loader.exec_module(b)
    raw = b.scenario_raw(
        osm_file="data/osm/i80_ngsim.osm",
        mainline_inflow=[[0.0, 2.0], [480.0, 2.1]],
        ramp_inflow=[[0.0, 0.2], [480.0, 0.25]],
        boundary=[[0.0, 5.0], [210.0, 4.5]],
        lane_shares=[0.15, 0.17, 0.17, 0.17, 0.17, 0.17],
        fleet=b.fleet_block(b.FLEET_FROM),
        duration_s=1980.0,
    )
    path = tmp_path / "scenarios" / "i80_replica.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("# kept\n" + yaml.safe_dump(raw, sort_keys=False))
    return path


def test_make_arm_changes_the_name_and_the_merge_only(tmp_path: Path) -> None:
    src = _kept_doc(tmp_path)
    out = tmp_path / "scenarios" / "i80_replica_measured.yaml"
    hashes = h.make_arm(src, out)
    a, b = yaml.safe_load(src.read_text()), yaml.safe_load(out.read_text())
    assert b["name"] == "i80_replica_measured"
    assert b["network"]["ramps"][0]["merge"] == "measured"
    assert h._without_arm_fields(a) == h._without_arm_fields(b)
    assert hashes["kept"] != hashes["measured"]
    assert hashes["measured"] in out.read_text().splitlines()[3]
    raw = yaml.safe_load(src.read_text())
    raw["network"]["ramps"][0]["merge"] = "measured"
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit, match="lane_change"):
        h.make_arm(bad, tmp_path / "x.yaml")
    raw = yaml.safe_load(src.read_text())
    raw["name"] = "other"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit, match="not 'i80_replica'"):
        h.make_arm(bad, tmp_path / "x.yaml")


def test_run_refuses_a_document_that_is_not_the_arms(tmp_path: Path) -> None:
    src = _kept_doc(tmp_path)
    with pytest.raises(SystemExit, match="not arm 'measured'"):
        h.run(src, "measured", 1, tmp_path / "runs")
    raw = yaml.safe_load(src.read_text())
    raw["replicates"] = 5
    src.write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit, match="not E11's"):
        h.run(src, "kept", 1, tmp_path / "runs")


# --- the stage -----------------------------------------------------------------------------------------


def test_the_stage_snippet_parses_and_names_what_exists() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    r = subprocess.run([bash, "-n", str(STAGE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = STAGE.read_text()
    pattern = r"(?<![\w/])(scripts/[\w./-]+\.py|artifacts/i80_merge_2026-10-07/harness/[\w.]+\.py)"
    for path in re.findall(pattern, text):
        assert (REPO_ROOT / path).is_file(), path
    flags = {
        "i80_data.py": ("fetch", "prepare", "--out"),
        "i80_merge_measures.py": (
            "observed",
            "simulated",
            "--data-summary",
            "--manifest",
            "--observed",
            "--inputs",
            "--procs",
        ),
        "i80_build_replica.py": ("build", "--data-summary", "--fleet-from", "--inputs-out"),
    }
    for script, used in flags.items():
        src = (REPO_ROOT / "scripts" / script).read_text()
        for f in used:
            assert f'"{f}"' in src, (script, f)
    hsrc = HARNESS.read_text()
    for f in ("make-arm", "run", "readout", "--kept", "--measured", "--label", "--scenario"):
        assert f'"{f}"' in hsrc, f
    assert "p21_i80_merge" in text and "--data-set none" in text and "--cap-min 120" in text
    assert (REPO_ROOT / "scenarios" / "i24_replica_flow_rc_speedcal_dc_refit.yaml").is_file()
    doc = copy.deepcopy(h.DEFINITIONS)
    assert "Amendment M1" in doc["E3"]
