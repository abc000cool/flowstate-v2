"""scripts/boundary_limit_factor.py: amendment B1's factor from tracked inputs.

docs/I24_DISCHARGE_DIAGNOSIS.md §7.5 computed f = 1.2185 for I-24 (the L5
fixture applied it) and §8.3 fixed it before any corridor run. The script must
reproduce it from the committed scenario, population and observed-side files
only (no I-24 MOTION table, no run tree), and must say that the rule does not
apply to the EIDM fleet of I-94.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

from flowstate_core.config import ScenarioConfig, config_hash, config_hash_v3

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "flowstate_boundary_limit_factor", SCRIPTS / "boundary_limit_factor.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


blf = _load()


@pytest.fixture(scope="module")
def i24() -> dict:
    return blf.corridor_factor("i24")


@pytest.fixture(scope="module")
def i94() -> dict:
    return blf.corridor_factor("i94")


def test_the_closed_form_inverts_the_idm_equilibrium() -> None:
    T, s0, length, v = 1.3, 2.5, 5.0, 14.0
    for f in (1.05, 1.2185, 1.6, 3.0):
        q = blf.q_eq_idm(v, f * v, T, s0, length)
        assert blf.solve_factor(v, q, T, s0, length) == pytest.approx(f, rel=1e-12)
    # no desired speed carries the improved-IDM flow or more (the supremum)
    with pytest.raises(ValueError, match="no desired speed"):
        blf.solve_factor(v, blf.q_eq_iidm(v, T, s0, length), T, s0, length)
    with pytest.raises(ValueError):
        blf.solve_factor(v, 0.0, T, s0, length)


def test_schedule_mean_is_time_weighted_over_the_window() -> None:
    steps = [(0.0, 10.0), (100.0, 20.0), (250.0, 5.0)]
    assert blf.schedule_mean(steps, 50.0, 300.0) == pytest.approx(
        (50 * 10.0 + 150 * 20.0 + 50 * 5.0) / 250.0
    )
    with pytest.raises(ValueError):
        blf.schedule_mean(steps, -1.0, 10.0)


def test_i24_reproduces_the_registered_factor(i24: dict) -> None:
    """1.2185 to four decimals from the flow as the record quotes it, and to four
    significant digits from the unrounded flow; inputs as §7.5 names them."""
    assert i24["applies"] is True and i24["fleet_model"] == "IDM"
    assert i24["limit_factor"] == 1.2185
    assert round(i24["limit_factor_from_flow_as_quoted"], 4) == 1.2185
    assert abs(i24["limit_factor_from_unrounded_flow"] - 1.2185) < 5e-4
    assert i24["registered"]["reproduced_to_4_decimals"] is True
    inp = i24["inputs"]
    assert inp["schedule"]["v_bar_ms"] == pytest.approx(13.869, abs=5e-4)
    assert inp["schedule"]["study_window_sim_s"] == [600.0, 7800.0]
    assert inp["recorded_flow"]["q_total_veh_h_as_quoted"] == 6009.0
    assert inp["recorded_flow"]["lanes"] == 4
    assert inp["recorded_flow"]["q_lane_veh_h_as_quoted"] == pytest.approx(1502.25)
    assert inp["mean_driver"]["T_s"] == pytest.approx(1.3222, abs=5e-5)
    assert inp["mean_driver"]["s0_m"] == pytest.approx(2.5327, abs=5e-5)
    assert inp["mean_driver"]["vehicle_length_m"] == 5.0
    # the factor reproduces the recorded flow in equilibrium
    assert i24["q_eq_check_veh_h_lane"] == pytest.approx(1502.25, abs=0.5)
    # one factor for the three arms of the stage (same schedule, window, T and s0);
    # the script reports the current policy's hashes, the stage registered them
    # under policy 3 (2026-10-07, before Amendment 4's bump)
    shared = {s["name"]: s["config_hash"] for s in i24["scenarios_sharing_the_factor"]}
    registered = {
        "i24_replica_flow_speedcal": "ae5861a4d906",
        "i24_replica_flow_speedcal_dc": "8976a1773674",
        "i24_replica_flow_speedcal_dc_refit": "ada3f406504b",
    }
    current = {
        name: config_hash(ScenarioConfig.from_yaml(REPO_ROOT / "scenarios" / f"{name}.yaml"))
        for name in registered
    }
    v3 = {
        name: config_hash_v3(ScenarioConfig.from_yaml(REPO_ROOT / "scenarios" / f"{name}.yaml"))
        for name in registered
    }
    assert v3 == registered
    assert shared == {k: current[k] for k in shared} and set(shared) == set(registered) - {
        "i24_replica_flow_speedcal_dc_refit"
    }
    assert i24["scenario"]["config_hash"] == current["i24_replica_flow_speedcal_dc_refit"]
    # §8.3's A2 band
    assert i24["a2_band_veh_h"] == [5829, 6309]


def test_i24_reads_only_tracked_small_inputs(i24: dict) -> None:
    import subprocess

    if (
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO_ROOT, capture_output=True
        ).returncode
        != 0
    ):
        pytest.skip("not a git work tree (the staged-tree gate exports without .git)")
    paths = {i24["scenario"]["path"], i24["inputs"]["mean_driver"]["population"]["path"]}
    paths |= {s["path"] for s in i24["inputs"]["recorded_flow"]["source"].values()}
    paths |= {s["path"] for s in i24["scenarios_sharing_the_factor"]}
    for p in paths:
        assert not p.startswith("data/i24motion/") and not p.startswith("runs/"), p
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", p], cwd=REPO_ROOT, capture_output=True
        )
        assert tracked.returncode == 0, f"{p} is not tracked"
        assert (REPO_ROOT / p).stat().st_size < 2_000_000, p


def test_i94_is_not_applicable_to_its_eidm_fleet(i94: dict) -> None:
    """I-94's fleet is EIDM (improved-IDM equilibrium gap s0 + vT, independent of the
    desired speed): no factor is defined; the inputs, the band and the IDM
    formula's value are still recorded."""
    assert i94["fleet_model"] == "EIDM"
    assert i94["applies"] is False and i94["limit_factor"] is None
    assert "improved IDM" in i94["reason"]
    inp = i94["inputs"]
    assert inp["schedule"]["station"] == "S97"
    assert inp["recorded_flow"]["lanes"] == 3
    assert inp["schedule"]["study_window_sim_s"] == [1800.0, 14400.0]
    q = inp["recorded_flow"]["q_total_veh_h_as_quoted"]
    assert i94["a2_band_veh_h"] == [round(q * 0.97), round(q * 1.05)]
    assert i94["iidm_equilibrium_flow_at_v_bar_veh_h_lane"] > inp["recorded_flow"]["q_lane_veh_h"]
    f_idm = i94["idm_formula_value_not_applicable"]
    assert f_idm is not None and 1.0 < f_idm < 2.0


def test_the_cli_writes_both_corridors(tmp_path: Path) -> None:
    out = tmp_path / "factor.json"
    assert blf.main(["--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert set(doc["corridors"]) == {"i24", "i94"}
    assert doc["corridors"]["i24"]["limit_factor"] == 1.2185
    assert not math.isnan(doc["corridors"]["i94"]["inputs"]["schedule"]["v_bar_ms"])
