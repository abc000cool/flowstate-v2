"""scripts/coverage_thinning.py (WP-91): the aggregation, the flat measure keys, VM X's acceptance
and an end-to-end run on a small synthetic stand-in of the US-101 loader's schema (the real
``data/ngsim`` is never read: ``us101_data.load_us101`` and ``data_hash`` are patched)."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_coverage_thinning", SCRIPTS / "coverage_thinning.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ct = _load()


def _period(seed: int, n_per_lane: int = 12) -> pd.DataFrame:
    """A 640 m, 10 Hz stand-in: platoons per mainline lane, entrants from lane 7 via lane 6 to 5,
    and a share of through changes between mainline lanes (the loader's columns)."""
    rng = np.random.default_rng(seed)
    parts = []
    vid = 0
    for lane in (1, 2, 3, 4, 5):
        v = 9.0 + lane * 0.5
        for j in range(n_per_lane):
            t0 = 4.0 * j + rng.uniform(0.0, 0.5)
            n = int(640.0 / v / 0.1)
            k = np.round(t0 / 0.1) + np.arange(n)
            t = np.round(k * 0.1, 1)
            x = v * (t - t[0])
            lanes = np.full(n, lane)
            if lane in (2, 3) and rng.random() < 0.4:
                lanes[x >= rng.uniform(200.0, 400.0)] = lane + int(rng.choice([-1, 1]))
            parts.append(
                pd.DataFrame(
                    {"t": t, "veh_id": str(vid), "x": x, "lane": lanes, "v": v, "length_m": 4.5}
                )
            )
            vid += 1
    for j in range(n_per_lane):
        v = 10.5
        t0 = 4.0 * j + 2.0
        n = int(640.0 / v / 0.1)
        t = np.round((np.round(t0 / 0.1) + np.arange(n)) * 0.1, 1)
        x = v * (t - t[0])
        lanes = np.where(x < 150.0, 7, np.where(x < rng.uniform(220.0, 420.0), 6, 5))
        parts.append(
            pd.DataFrame(
                {"t": t, "veh_id": str(vid), "x": x, "lane": lanes, "v": v, "length_m": 4.5}
            )
        )
        vid += 1
    return pd.concat(parts, ignore_index=True)


class TestAggregate:
    def test_mean_spread_interval_and_shift(self) -> None:
        agg = ct.aggregate([1.0, 2.0, 3.0, 4.0, 5.0], 2.0)
        assert agg["n"] == 5 and agg["mean"] == 3.0 and agg["min"] == 1.0 and agg["max"] == 5.0
        half = 2.7764 * math.sqrt(2.5) / math.sqrt(5)
        assert agg["ci95"] == pytest.approx([3.0 - half, 3.0 + half], abs=2e-4)
        assert agg["shift"] == 1.0
        assert agg["per_seed"] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_missing_values(self) -> None:
        agg = ct.aggregate([None, 0.5, None], None)
        assert agg["n"] == 1 and agg["mean"] == 0.5 and agg["ci95"] is None
        assert agg["shift"] is None and agg["per_seed"] == [None, 0.5, None]
        empty = ct.aggregate([None, None], 1.0)
        assert empty["mean"] is None and empty["shift"] is None

    def test_build_results_is_measure_major(self) -> None:
        ref = {"a": 1.0, "b": 2}
        conds = {("vehicle", 0.5): [{"a": 1.5}, {"a": 2.5, "c": 3.0}], ("fragment", 1.0): [{}]}
        res = ct.build_results(ref, conds)
        assert list(res) == ["a", "b", "c"]
        assert res["a"]["reference"] == 1.0
        assert res["a"]["vehicle"]["0.5"]["mean"] == 2.0
        assert res["a"]["vehicle"]["0.5"]["shift"] == 1.0
        assert res["c"]["reference"] is None and res["c"]["vehicle"]["0.5"]["n"] == 1
        assert res["b"]["fragment"]["1"]["n"] == 0


class TestFlatKeys:
    def test_relax_keys(self) -> None:
        offs = [0.0, 1.0, 5.0, 10.0]
        block = {
            "n": [9, 8, 7, 6],
            "p25": [1, 1, 1, 1],
            "p50": [0.8, 0.9, 1.0, 1.1],
            "p75": [2] * 4,
        }
        row = {
            "zone_kind": "weave",
            "movement": "entering",
            "speed_class": "all",
            "side": "follower",
            "offsets_s": offs,
            "n_changes": 10,
            "n_measured": 9,
            "n": [9, 8, 7, 6],
            "measures": {m: block for m in ct.RELAX_MEASURES},
            "rel_speed_ms": {"n": [9] * 4, "p25": [-1.0] * 4, "p50": [0.5] * 4, "p75": [2.0] * 4},
        }
        other = {**row, "zone_kind": "basic", "movement": "exiting"}
        slow = {**row, "speed_class": "v<10"}
        out = ct.relax_measures([row, other, slow])
        p = "relax.weave.entering.all.follower"
        assert out[f"{p}.ratio_pop.p50@0"] == 0.8 and out[f"{p}.ratio_eq.p50@5"] == 1.0
        assert out[f"{p}.n_read@10"] == 6 and out[f"{p}.ratio_pop.n@0"] == 9
        assert out[f"{p}.rel_speed_ms.p25@0"] == -1.0 and f"{p}.rel_speed_ms.p25@5" not in out
        assert not any(k.startswith("relax.basic.exiting") for k in out)
        assert not any(".v<10." in k for k in out)

    def test_gap_and_cg_keys(self) -> None:
        q = {"p10": 1.0, "p50": 2.0}
        grow = {
            "zone_kind": "weave",
            "movement": "entering",
            "speed_class": "all",
            "n": 5,
            **{f"share_no_{s}": 0.0 for s in ("lead", "lag")},
            **{f"{s}_{m}": q for s in ("lead", "lag") for m in ("gap_m", "time_gap_s")},
            **{f"{s}_closing_ms": q for s in ("lead", "lag")},
            "model": {"refused_share": 0.4, "refused_by": {t: 0.1 for t in ct.GAP_TERMS}},
        }
        g = ct.gap_measures([grow, {**grow, "speed_class": "v<10"}])
        assert g["gaps.weave.entering.refused_share"] == 0.4
        assert g["gaps.weave.entering.refused_by.lag_absorb"] == 0.1
        assert g["gaps.weave.entering.lag_time_gap_s.p10"] == 1.0
        fit = {
            "median_s": 0.9,
            "at_bound": False,
            "degenerate": False,
            "ci95": {"median_s": [1, 2]},
        }
        crow = {
            "zone_kind": "weave",
            "movement": "entering",
            "speed_class": "all",
            "n_drivers": 40,
            "share_with_rejected": 0.5,
            "joint": {"fitted": True, "n_used": 35, "lead": fit, "lag": {**fit, "at_bound": True}},
            "separate_lead": {"fitted": False, "n_used": 3},
            "separate_lag": {"fitted": True, "n_used": 30, **fit},
        }
        c = ct.cg_measures([crow])
        assert c["cg.entering.all.joint.lead.median_s"] == 0.9
        assert c["cg.entering.all.joint.lag.median_s"] is None  # at a bound
        assert c["cg.entering.all.separate.lead.median_s"] is None  # not fitted
        assert c["cg.entering.all.separate.lag.median_s"] == 0.9
        iv = ct.cg_intervals([crow])
        assert iv["cg.entering.all.joint.lead.median_s"] == [1, 2]
        assert "cg.entering.all.separate.lead.median_s" not in iv

    def test_vm_x_acceptance_is_the_artifact_block(self) -> None:
        acc = ct.vm_x_acceptance()
        block = json.loads(ct.ACCEPTANCE_ARTIFACT.read_text())["acceptance"]
        d = acc.to_dict()
        for k in ("accept_gap_s", "exit_accept_gap_s", "s0_m", "T_s", "a_max", "b", "v0_ms"):
            assert d[k] == block[k]
        assert "VM X" in d["source"]


class TestEndToEnd:
    def test_standin_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import us101_data

        periods = {"p1": _period(1), "p2": _period(2, n_per_lane=8)}
        monkeypatch.setattr(
            us101_data, "load_us101", lambda: {k: v.copy() for k, v in periods.items()}
        )
        monkeypatch.setattr(us101_data, "data_hash", lambda: "synthetic-standin")
        out = tmp_path / "thin.json"
        argv = ["--out", str(out), "--fractions", "1.0", "0.5", "--n-seeds", "2", "--n-boot", "0"]
        ct.main(argv)
        art = json.loads(out.read_text())
        assert art["kind"] == "observed_thinned" and art["data_hash"] == "synthetic-standin"
        assert art["parameters"]["thinning_seeds"] == ct.spawn_seeds(ct.DEFAULT_SEED, 2)
        assert art["acceptance"]["accept_gap_s"] == ct.vm_x_acceptance().accept_gap_s
        res = art["results"]
        kept = res["tracking.kept_time_fraction"]
        assert kept["reference"] == 1.0
        assert set(kept) == {"reference", "vehicle", "fragment"}
        assert set(kept["vehicle"]) == {"0.5"}  # vehicle-level F = 1 is the reference
        assert set(kept["fragment"]) == {"1", "0.5"}
        assert kept["fragment"]["1"]["per_seed"] == [1.0, 1.0]
        assert all(abs(v - 0.5) < 0.15 for v in kept["vehicle"]["0.5"]["per_seed"])
        assert all(abs(v - 0.5) < 0.15 for v in kept["fragment"]["0.5"]["per_seed"])
        frag1 = res["tracking.n_fragments"]["fragment"]["1"]["mean"]
        assert frag1 > res["tracking.n_vehicles_kept"]["fragment"]["1"]["mean"]
        # the changes: fewer under thinning, the relaxation and gap keys present
        n_changes = res["tracking.n_changes"]
        assert n_changes["reference"] > 0
        assert n_changes["vehicle"]["0.5"]["mean"] < n_changes["reference"]
        assert "gaps.weave.entering.refused_share" in res
        assert "relax.weave.entering.all.follower.rel_speed_ms.p50@0" in res
        assert art["reference_check_stage16"]["artifact"].endswith(
            "lane_change_relaxation_us101.json"
        )
        assert art["zones"][0]["kind"] == "weave"
        # seeded: a second run gives the same results
        out2 = tmp_path / "thin2.json"
        ct.main([*argv[:1], str(out2), *argv[2:]])
        assert json.loads(out2.read_text())["results"] == res
