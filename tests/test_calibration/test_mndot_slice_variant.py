"""The I-94 WB St. Paul 35-minute slice is the weave scenario shifted by 5,400 s.

``scenarios/mndot_i94_wb_stpaul_weave_slice.yaml`` is cut from
``scenarios/mndot_i94_wb_stpaul_weave.yaml`` by hand (docs/ONBOARDING_MNDOT.md
§10, §11): its ``t = 0`` is the weave's ``t = 5400 s`` (07:00 local), and every
time series — the entry inflow, every ramp's inflow or exit fraction and the
downstream speed boundary — must carry the same offset. The 2026-09-24 rebuild
shifted the inflow and the ramps but left the boundary schedule at the weave's
clock, so the slice's exit was throttled by the 05:30 speeds while its demand
was the 07:00 peak; this test pins all three to one offset.

Data-only and fast: no SUMO, no simulation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WEAVE = REPO / "scenarios" / "mndot_i94_wb_stpaul_weave.yaml"
SLICE = REPO / "scenarios" / "mndot_i94_wb_stpaul_weave_slice.yaml"
OFFSET_S = 5400.0


@pytest.fixture(scope="module")
def weave() -> dict[str, Any]:
    return yaml.safe_load(WEAVE.read_text())


@pytest.fixture(scope="module")
def sliced() -> dict[str, Any]:
    return yaml.safe_load(SLICE.read_text())


def _series(record: dict[str, Any]) -> dict[str, list[list[float]]]:
    """Every ``[[t, v], ...]`` series of a mapping, by key."""
    return {
        key: value
        for key, value in record.items()
        if isinstance(value, list) and value and isinstance(value[0], list) and len(value[0]) == 2
    }


def _assert_shifted(name: str, sliced: list[list[float]], full: list[list[float]]) -> None:
    by_t = {float(t): float(v) for t, v in full}
    assert sliced, f"{name}: the slice carries no steps"
    for t, v in sliced:
        assert float(t) + OFFSET_S in by_t, f"{name}: no weave step at t={t} + {OFFSET_S:.0f}"
        assert float(v) == pytest.approx(by_t[float(t) + OFFSET_S]), (
            f"{name}: value at t={t} is not the weave's at t={float(t) + OFFSET_S:.0f}"
        )


def test_every_series_is_the_weave_shifted_by_the_same_offset(
    weave: dict[str, Any], sliced: dict[str, Any]
) -> None:
    w_net, s_net = weave["network"], sliced["network"]
    _assert_shifted("inflow", s_net["inflow"], w_net["inflow"])
    _assert_shifted("boundary", s_net["boundary"]["steps"], w_net["boundary"]["steps"])
    assert s_net["boundary"]["exit_buffer_m"] == w_net["boundary"]["exit_buffer_m"]
    w_ramps = {r["name"]: r for r in w_net["ramps"]}
    assert [r["name"] for r in s_net["ramps"]] == list(w_ramps)
    for ramp in s_net["ramps"]:
        for key, series in _series(ramp).items():
            _assert_shifted(f"{ramp['name']} {key}", series, _series(w_ramps[ramp["name"]])[key])


def test_the_slice_window_covers_the_run_and_the_rest_is_the_weave(
    weave: dict[str, Any], sliced: dict[str, Any]
) -> None:
    sim = sliced["sim"]
    assert sim == {**weave["sim"], "duration_s": 2100.0, "warmup_s": 300.0}
    assert sliced["seed"] == weave["seed"] == 42
    assert sliced["replicates"] == 1
    last_t = max(float(t) for t, _ in sliced["network"]["boundary"]["steps"])
    assert last_t >= sim["duration_s"] - 300.0  # the last step holds to the end of the run
    for key in ("fleet", "av", "closures", "managed_lanes", "perturbation"):
        assert sliced[key] == weave[key], key
    for key in ("corridor_edges", "netconvert_extra", "patch_files", "entry_lane_shares"):
        assert sliced["network"][key] == weave["network"][key], key
    # the non-series part of every ramp (attach edge, merge model, weave block) is unchanged
    for s_ramp, w_ramp in zip(sliced["network"]["ramps"], weave["network"]["ramps"], strict=True):
        s_rest = {k: v for k, v in s_ramp.items() if k not in _series(s_ramp)}
        w_rest = {k: v for k, v in w_ramp.items() if k not in _series(w_ramp)}
        assert s_rest == w_rest, s_ramp["name"]
