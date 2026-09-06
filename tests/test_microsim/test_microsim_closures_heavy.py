"""Temporary lane closures and heavy vehicles (docs/CONTRACTS.md §2).

Closures: the listed lanes of the overlapped corridor edges refuse every
vehicle class for the window and are restored afterwards; the run is
labelled ``seeded=True``. Heavy vehicles: a Bernoulli share of the fleet
drawn from a second population, written as truck vTypes with their own
length and emission class, never tagged as controlled vehicles, and flagged
in the trajectory table.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

from flowstate_core.config import (
    AVSpec,
    FleetSpec,
    HeavyVehicleSpec,
    LaneClosureSpec,
    ScenarioConfig,
)
from flowstate_core.rng import make_rng
from microsim import run_micro
from microsim.vehicles import build_corridor_plan, write_corridor_routes

pytestmark = pytest.mark.integration

SEED = 11
HEAVY = {
    "fraction": 0.2,
    "length_m": 20.5,
    "emission_class": "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
    "v0": 28.0,
    "T": 1.8,
    "a_max": 0.5,
    "b": 1.5,
    "s0": 3.0,
}


class TestSchema:
    def test_closure_validation(self):
        LaneClosureSpec(start_m=100.0, end_m=300.0, lanes=[0], t_start_s=10.0, t_end_s=50.0)
        with pytest.raises(ValueError, match="end_m"):
            LaneClosureSpec(start_m=300.0, end_m=300.0, lanes=[0], t_start_s=10.0, t_end_s=50.0)
        with pytest.raises(ValueError, match="t_end_s"):
            LaneClosureSpec(start_m=100.0, end_m=300.0, lanes=[0], t_start_s=50.0, t_end_s=50.0)
        with pytest.raises(ValueError, match="distinct"):
            LaneClosureSpec(start_m=100.0, end_m=300.0, lanes=[0, 0], t_start_s=1.0, t_end_s=2.0)

    def test_closure_labels_the_run_seeded(self):
        base = {
            "name": "c",
            "network": {"kind": "corridor", "length_m": 2000.0, "lanes": 2, "inflow": [[0.0, 0.3]]},
            "sim": {"duration_s": 60.0},
        }
        assert ScenarioConfig.model_validate(base).seeded is False
        closed = ScenarioConfig.model_validate(
            {
                **base,
                "closures": [
                    {
                        "start_m": 1000.0,
                        "end_m": 1200.0,
                        "lanes": [0],
                        "t_start_s": 10.0,
                        "t_end_s": 40.0,
                    }
                ],
            }
        )
        assert closed.seeded is True

    def test_heavy_needs_provenance(self):
        with pytest.raises(ValueError, match="no built-in defaults"):
            HeavyVehicleSpec(fraction=0.1, length_m=18.0, emission_class="HBEFA4/x")
        HeavyVehicleSpec(
            fraction=0.1, length_m=18.0, emission_class="HBEFA4/x", idm_calibration="a.json"
        )
        HeavyVehicleSpec(**HEAVY)
        assert FleetSpec().heavy is None


class TestHeavyPlan:
    def test_draw_share_parameters_and_av_exclusion(self, tmp_path):
        fleet = FleetSpec(heavy=HeavyVehicleSpec(**HEAVY))
        plan = build_corridor_plan(
            [(0.0, 2.0)], 600.0, fleet, AVSpec(penetration=0.2), make_rng(SEED)
        )
        n_heavy = sum(plan.is_heavy)
        assert abs(n_heavy / plan.n - HEAVY["fraction"]) < 0.05
        for i in range(plan.n):
            if plan.heavy(i):
                assert not plan.is_av[i] and not plan.complied[i]
                assert plan.params[i]["a_max"] < 0.8  # drawn from the heavy population
        path = write_corridor_routes(
            ("e0",), plan, "IDM", 0.5, tmp_path / "h.rou.xml", lanes=2, heavy=fleet.heavy
        )
        vtypes = {v.get("id"): v for v in ET.parse(path).getroot().findall("vType")}
        for i in range(plan.n):
            vt = vtypes[f"t{i:05d}"]
            if plan.heavy(i):
                assert vt.get("vClass") == "truck"
                assert vt.get("length") == "20.5"
                assert vt.get("emissionClass") == HEAVY["emission_class"]
            else:
                assert vt.get("vClass") is None and vt.get("length") == "5.0"

    def test_no_heavy_block_reproduces_previous_draws(self, tmp_path):
        a = build_corridor_plan([(0.0, 1.0)], 120.0, FleetSpec(), AVSpec(), make_rng(SEED))
        b = build_corridor_plan(
            [(0.0, 1.0)], 120.0, FleetSpec(heavy=None), AVSpec(), make_rng(SEED)
        )
        assert a.params == b.params and a.is_heavy == () and b.is_heavy == ()
        pa_ = write_corridor_routes(("e0",), a, "IDM", 0.5, tmp_path / "a.rou.xml", lanes=2)
        pb = write_corridor_routes(
            ("e0",), b, "IDM", 0.5, tmp_path / "b.rou.xml", lanes=2, heavy=None
        )
        assert pa_.read_text() == pb.read_text()


@pytest.fixture(scope="module")
def closure_run(tmp_path_factory):
    cfg = ScenarioConfig.model_validate(
        {
            "name": "corridor_closure",
            "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 2, "inflow": [[0.0, 0.5]]},
            "fleet": {"heavy": HEAVY},
            "sim": {"duration_s": 300.0},
            # the span sits 0.2-0.7 km past the insertion buffer, reached ~75 s
            # after the first departures; the closure runs 120-210 s
            "closures": [
                {
                    "label": "work zone",
                    "start_m": 200.0,
                    "end_m": 700.0,
                    "lanes": [0],
                    "t_start_s": 120.0,
                    "t_end_s": 210.0,
                }
            ],
        }
    )
    paths = run_micro(cfg, SEED, tmp_path_factory.mktemp("closure"))
    return cfg, paths


class TestClosureRun:
    def test_meta_records_the_closure_and_the_heavy_share(self, closure_run):
        _cfg, paths = closure_run
        meta = json.loads(paths.meta.read_text())
        assert meta["seeded"] is True
        (c,) = meta["closures"]
        assert c["label"] == "work zone" and c["lanes"] == [0]
        assert c["lane_ids"] and all(lid.endswith("_0") for lid in c["lane_ids"])
        assert c["applied_at_s"] is not None and 120.0 <= c["applied_at_s"] < 121.0
        assert c["released_at_s"] is not None and 210.0 <= c["released_at_s"] < 211.0
        assert meta["n_heavy"] > 0
        assert abs(meta["heavy_fraction_realized"] - HEAVY["fraction"]) < 0.08

    def test_closed_lane_is_vacated_and_reused(self, closure_run):
        _cfg, paths = closure_run
        meta = json.loads(paths.meta.read_text())
        (c,) = meta["closures"]
        df = pd.read_parquet(paths.trajectories)
        # closure positions count from the start of the analysis corridor; the
        # runner records the span in the trajectories' linear x
        lo, hi = c["x_lo_m"], c["x_hi_m"]
        assert hi - lo == pytest.approx(500.0)
        on_lane0 = df[(df.lane == 0) & (df.x >= lo) & (df.x < hi)]
        # some traffic used lane 0 in the closed span before the closure ...
        before = on_lane0[(on_lane0.t > 90.0) & (on_lane0.t < 118.0)]
        assert len(before) > 0
        # ... and, once vehicles caught on it have left (30 s grace), none during it
        during = on_lane0[(on_lane0.t > 150.0) & (on_lane0.t < 210.0)]
        assert len(during) == 0, f"{len(during)} samples on the closed lane during the window"
        # ... and traffic returns to it after the window
        after = on_lane0[(on_lane0.t > 240.0)]
        assert len(after) > 0
        assert df["is_heavy"].any() and not df["is_heavy"].all()
