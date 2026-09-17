"""Lane use at the off-ramp gores (docs/MERGE_ROUND6_PLAN.md §2.5).

``scripts/i24_diverge_lanes.py`` measures, from the recording, the lane
distribution over the kilometre before each off-ramp gore and which lanes the
vehicles that leave by the ramp are in. These tests are synthetic and fast:
they never read the recording or any artifact. What is asserted:

* the gore's decision line is found where the auxiliary lane ends, and a
  stretch where it never ends is an error rather than a silent answer;
* the exit classification does what its docstring says on fragments whose
  truth is constructed — including the two cases the rule is built around (a
  fragment seen on the ramp past the nose, and one that disappears in the
  auxiliary lane at the nose) and the two it must not mistake for them (a
  fragment that returns to the mainline, and one that ends before the gore);
* the classification zone is closed at both ends, so a longer window cannot
  resolve more through vehicles than exits;
* the two share conventions are wired to the quantities they name: on lanes
  carrying equal flow at unequal speeds, vehicle-time is the speed ratio and
  the crossing share is even.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dl = _load("i24_diverge_lanes")

GORE = 1000.0
DIV = 1025.0  # the auxiliary lane's last 25 m bin starts here


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _frame(fragments: list[tuple[list[float], list[int]]]) -> dict[str, np.ndarray]:
    """``(xs, lanes)`` per fragment → arrays sorted by ``(veh, t)`` as loaded."""
    veh, x, lane, t = [], [], [], []
    for i, (xs, lanes) in enumerate(fragments):
        veh += [i] * len(xs)
        x += list(xs)
        lane += list(lanes)
        t += [0.2 * k for k in range(len(xs))]
    return {
        "veh": np.array(veh, np.int32),
        "x": np.array(x, np.float32),
        "lane": np.array(lane, np.int8),
        "t": np.array(t, np.float32),
        "v": np.full(len(x), 20.0, np.float32),
    }


def _aux_lane_field(end_x: float) -> dict[str, np.ndarray]:
    """Samples with a lane-5 auxiliary lane present up to ``end_x`` and lane 6 after."""
    xs = np.arange(GORE - 400.0, GORE + 200.0, 5.0)
    x = np.concatenate([xs, xs[xs < end_x], xs[xs >= end_x]])
    lane = np.concatenate(
        [
            np.full(len(xs), 4, np.int8),
            np.full(int((xs < end_x).sum()), 5, np.int8),
            np.full(int((xs >= end_x).sum()), 6, np.int8),
        ]
    )
    return {"x": x.astype(np.float32), "lane": lane}


# --------------------------------------------------------------------------
# the decision line
# --------------------------------------------------------------------------


def test_divergence_x_is_where_the_auxiliary_lane_ends() -> None:
    f = _aux_lane_field(end_x=DIV)
    div_x, rows = dl.divergence_x(f["x"], f["lane"], GORE)
    assert div_x == DIV
    # the reported bins cover the gore neighbourhood and show the step
    before = next(r for r in rows if r["x_lo_m"] == int(DIV - dl.DIVERGENCE_BIN_M))
    after = next(r for r in rows if r["x_lo_m"] == int(DIV))
    assert before["n_by_lane"]["5"] > 0 and after["n_by_lane"]["5"] == 0
    assert after["n_by_lane"]["6"] > 0


def test_divergence_x_refuses_a_lane_that_never_ends() -> None:
    f = _aux_lane_field(end_x=GORE + 1000.0)  # auxiliary lane runs past the search window
    with pytest.raises(ValueError, match="does not end"):
        dl.divergence_x(f["x"], f["lane"], GORE)


def test_divergence_x_refuses_a_stretch_without_an_auxiliary_lane() -> None:
    xs = np.arange(GORE - 400.0, GORE + 200.0, 5.0).astype(np.float32)
    with pytest.raises(ValueError, match="no auxiliary lane"):
        dl.divergence_x(xs, np.full(len(xs), 4, np.int8), GORE)


# --------------------------------------------------------------------------
# the exit rule
# --------------------------------------------------------------------------

#: One fragment per case, in the order the labels below assert.
CASES = {
    "exit_on_ramp": ([880.0, 940.0, 1000.0, 1060.0], [4, 5, 5, 6]),
    "exit_vanishing_at_the_nose": ([880.0, 940.0, 1000.0], [4, 5, 5]),
    "through": ([880.0, 940.0, 1000.0, 1060.0], [3, 3, 3, 3]),
    "vanishing_on_the_mainline": ([880.0, 940.0, 990.0], [2, 2, 2]),
    "never_reaches_the_gore": ([700.0, 800.0, 900.0], [4, 4, 4]),
    "returns_to_the_mainline": ([900.0, 950.0, 1000.0, 1100.0], [4, 5, 5, 4]),
    "both": ([1050.0, 1060.0, 1070.0], [5, 3, 3]),
    "resolved_only_past_the_zone": ([900.0, 1000.0, 1200.0], [4, 4, 4]),
}


@pytest.fixture(scope="module")
def labels() -> dict[str, np.ndarray]:
    d = _frame(list(CASES.values()))
    return dl.classify_exits(d["veh"], d["x"], d["lane"], n_veh=len(CASES), gore_x=GORE, div_x=DIV)


def _of(labels: dict[str, np.ndarray], key: str, case: str) -> bool:
    return bool(labels[key][list(CASES).index(case)])


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("exit_on_ramp", {"exit", "exit_extended", "reaches_gore"}),
        ("exit_vanishing_at_the_nose", {"exit_extended", "unresolved", "reaches_gore"}),
        ("through", {"through", "reaches_gore"}),
        ("vanishing_on_the_mainline", {"unresolved", "reaches_gore"}),
        ("never_reaches_the_gore", set()),
        ("returns_to_the_mainline", {"through", "reaches_gore"}),
        ("both", {"both", "reaches_gore"}),
        ("resolved_only_past_the_zone", {"unresolved", "reaches_gore"}),
    ],
)
def test_classify_exits_labels(
    labels: dict[str, np.ndarray], case: str, expected: set[str]
) -> None:
    keys = {"exit", "exit_extended", "through", "both", "unresolved", "reaches_gore"}
    assert {k for k in keys if _of(labels, k, case)} == expected


def test_classification_zone_is_closed_downstream(labels: dict[str, np.ndarray]) -> None:
    """A fragment first seen beyond the zone is unresolved, not a through.

    This is what keeps the exit share from drifting with the window: exits
    stop being tracked within ~50 m of the nose, so a zone open downstream
    would keep adding through vehicles and nothing else.
    """
    assert _of(labels, "unresolved", "resolved_only_past_the_zone")
    assert not _of(labels, "through", "resolved_only_past_the_zone")


def test_auxiliary_lane_entries_carry_their_source_lane_and_position() -> None:
    d = _frame(list(CASES.values()))
    out = dl.aux_entries(d["veh"], d["x"], d["lane"], lo=GORE - 250.0, hi=GORE)
    # two fragments move 4 → 5 inside the window, one of them back to lane 4
    # (past the window, so it is not counted as a return here)
    assert out["n_into_aux"] == 3
    assert out["into_aux_from_lane"] == {"1": 0, "2": 0, "3": 0, "4": 3}
    assert sum(out["into_aux_x_hist_100m"]["n"]) == 3
    assert out["n_back_to_mainline"] == 0


def test_lane_in_window_takes_the_last_lane_inside_the_mask() -> None:
    d = _frame([([100.0, 200.0, 300.0], [4, 5, 6])])
    mask = (d["x"] >= 100.0) & (d["x"] < 300.0)
    assert dl.lane_in_window(d["veh"], d["lane"], mask, 1).tolist() == [5]
    assert dl.last_lane_per_fragment(d["veh"], d["lane"], 1).tolist() == [6]


# --------------------------------------------------------------------------
# the two share conventions
# --------------------------------------------------------------------------


def test_time_and_flow_shares_are_the_quantities_they_name() -> None:
    """Equal flow at unequal speed: vehicle-time follows the speed ratio, flow does not.

    Lane 1 crawls at 5 m/s and lane 4 runs at 25 m/s, four vehicles each over
    the kilometre before the gore. Every vehicle crosses every section once,
    so the crossing share is even; lane 1 holds five times the samples, so the
    vehicle-time share is 5:1. Mixing the two is the §2.1 defect, one bin
    downstream.
    """
    frags = []
    for lane, speed in ((1, 5.0), (4, 25.0)):
        for _ in range(4):
            xs = np.arange(GORE - 1000.0, GORE + 60.0, speed * 0.2)
            frags.append((list(xs), [lane] * len(xs)))
    d = _frame(frags)
    order = np.lexsort((d["t"], d["veh"]))
    d = {k: v[order] for k, v in d.items()}
    d["v"] = np.where(d["lane"] == 1, 5.0, 25.0).astype(np.float32)

    import pandas as pd

    frame = pd.DataFrame(
        {"t": d["t"], "veh_id": d["veh"], "x": d["x"], "lane": d["lane"]}, copy=False
    )
    counts = dl.first_crossing_lane_counts(frame, dl.bin_sections(GORE), dl.LANES)
    labels = dl.classify_exits(
        d["veh"], d["x"], d["lane"], n_veh=len(frags), gore_x=GORE, div_x=DIV
    )
    rows = dl.bin_rows(d, GORE, labels, counts)

    assert [r["offset_to_gore_m"] for r in rows] == [
        [-1000.0, -750.0],
        [-750.0, -500.0],
        [-500.0, -250.0],
        [-250.0, 0.0],
    ]
    for r in rows:
        assert r["share"]["1"] == pytest.approx(5 / 6, abs=0.01)
        assert r["share"]["4"] == pytest.approx(1 / 6, abs=0.01)
        assert r["flow_share"]["1"] == pytest.approx(0.5, abs=1e-6)
        assert r["flow_share"]["4"] == pytest.approx(0.5, abs=1e-6)
        assert r["n_crossings"]["1"] == 4 * dl.SECTIONS_PER_BIN
        assert r["section_totals"] == [8] * dl.SECTIONS_PER_BIN
        assert r["speed_kmh"]["1"] == pytest.approx(18.0, abs=1e-6)
        assert r["speed_kmh"]["4"] == pytest.approx(90.0, abs=1e-6)
