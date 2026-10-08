"""scripts/i80_build_replica.py (E11): the I-80 replica from synthetic NGSIM periods, nothing simulated.

The US-101 recipe's arithmetic (5-min windows on the wall clock with a partial last one, steps
shifted by the 180-s warm-up, the 30-s boundary schedule held through the warm-up), the stitch
across periods, the measured entry-lane shares, the hand-built map compiled by netconvert as the
runner compiles it (corrected so the compiled edges have the measured lengths; the ramp joins
lane 0 of the acceleration-lane edge, which ends there), and ``build`` end to end on synthetic
periods: a scenario that reloads to its recorded config hash with the kept I-24 arm's fleet, the
inputs artifact, the map and its rebuild from the artifact.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.test_scripts.test_i80_data import T0_MS, ngsim_period

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"e11_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


b = _load("i80_build_replica")
d = b.i80_data


def _periods() -> dict[str, pd.DataFrame]:
    p2 = ngsim_period(T0_MS, 3600, seed=2)
    p3 = ngsim_period(T0_MS + 300_000, 3600, seed=3)
    periods, _ = d.dedupe_and_split(pd.concat([p2, p3], ignore_index=True))
    return periods


def test_the_insertion_buffer_is_the_runners() -> None:
    from microsim.runner import CORRIDOR_INSERTION_BUFFER_M

    assert b.INSERTION_BUFFER_M == CORRIDOR_INSERTION_BUFFER_M


def test_windows_and_steps_follow_the_us101_replica() -> None:
    times = np.array([1.0, 2.0, 299.9, 300.0, 650.0, 700.0])
    w = b.rate_windows(times, 760.0)
    assert [(r["t_start_s"], r["t_end_s"], r["n_entries"]) for r in w] == [
        (0.0, 300.0, 3),
        (300.0, 600.0, 1),
        (600.0, 760.0, 2),
    ]
    assert w[2]["inflow_veh_s"] == pytest.approx(2 / 160.0)
    # scenarios/us101_replica.yaml: [0.0, r0], [480.0, r1], [780.0, r2] with a 180-s warm-up
    assert [s[0] for s in b.inflow_steps(w, 180.0)] == [0.0, 480.0, 780.0]
    sched = [(0.0, 5.0), (30.0, 4.0), (60.0, 3.5)]
    assert b.boundary_steps(sched, 180.0) == [[0.0, 5.0], [210.0, 4.0], [240.0, 3.5]]
    assert b.entry_lane_shares(np.array([1, 1, 6, 6, 6, 3])) == pytest.approx(
        [2 / 6, 0.0, 1 / 6, 0.0, 0.0, 3 / 6], abs=1e-6
    )
    with pytest.raises(ValueError, match="no mainline entries"):
        b.entry_lane_shares(np.array([7, 7]))


def test_stitched_entries_and_the_boundary_schedule() -> None:
    periods = _periods()
    block = ["p1", "p2"]
    ent = b.stitched_entries(periods, block)
    sw = ent["mainline"]["switches_ms"][0]
    per = ent["mainline"]["per_period"]
    assert per[0]["n_used"] == per[0]["n_entries"]  # everything up to p1's own last entry
    assert per[1]["n_used"] < per[1]["n_entries"]  # p2's entries up to that instant are p1's
    t0 = d.block_t0_ms(periods, block)
    times = ent["mainline"]["times_s"]
    assert np.all(np.diff(np.sort(times)) > 0)  # no entry counted twice
    assert times.max() > (sw - t0) / 1000.0
    assert set(ent["mainline"]["lanes"].tolist()) <= set(range(1, 7))
    assert len(ent["ramp"]["times_s"]) > 0
    sched = b.boundary_schedule(periods, block, 299.0)
    span = d.block_span_s(periods, block)
    assert len(sched) == int(np.ceil(span / b.BOUNDARY_WINDOW_S))
    assert [t for t, _ in sched[:3]] == [0.0, 30.0, 60.0]
    # mainline cars drive 10 m/s, merged ramp cars 12 m/s
    assert all(10.0 <= v <= 12.0 for _, v in sched) and min(v for _, v in sched) < 11.0


def _geom() -> dict[str, Any]:
    return {
        "site_length_m": 503.0,
        "gore_m": 112.4,
        "accel_end_m": 318.7,
        "approach_m": 503.0,
        "exit_m": 200.0,
        "ramp_m": 200.0,
    }


def test_corrected_layout_moves_the_nodes_by_the_compiled_error() -> None:
    """A stand-in compiler that moves the gore 40 m upstream: one correction removes it."""
    calls: list[str] = []

    def fake(text: str) -> dict[str, float]:
        calls.append(text)
        lat = {}
        for line in text.splitlines():
            if "<node id=" in line and 'id="10"' not in line:
                nid = line.split('id="')[1].split('"')[0]
                lat[nid] = float(line.split('lat="')[1].split('"')[0])
        m_lat, _ = b._m_per_deg(b.ORIGIN_LATLON[0])
        s = {k: (v - b.ORIGIN_LATLON[0]) * m_lat for k, v in lat.items()}
        s["3"] -= 40.0
        return {e: s[str(i + 2)] - s[str(i + 1)] for i, e in enumerate(b.CORRIDOR_EDGES)}

    out = b.corrected_layout(_geom(), compile_fn=fake)
    assert out["passes"] == 2 and len(calls) == 2
    meas = out["measured_lengths_m"]
    assert meas == pytest.approx(
        {"9001": 503.0, "9002": 112.4, "9003": 206.3, "9004": 184.3, "9005": 200.0}
    )
    for e in b.CORRIDOR_EDGES:
        assert out["compiled_lengths_m"][e] == pytest.approx(meas[e], abs=b.LAYOUT_TOL_M)
    assert out["node_s_m"]["3"] == pytest.approx(503.0 + 112.4 + 40.0, abs=0.05)

    def stuck(text: str) -> dict[str, float]:  # a compiler the correction cannot move
        return dict(zip(b.CORRIDOR_EDGES, (503.0, 100.0, 218.7, 184.3, 200.0), strict=True))

    with pytest.raises(RuntimeError, match="did not converge"):
        b.corrected_layout(_geom(), compile_fn=stuck)

    def swallow(text: str) -> dict[str, float]:  # a junction longer than the acceleration lane
        return dict(
            zip(b.CORRIDOR_EDGES, (503.0, 112.4 - 300.0, 206.3 + 300.0, 184.3, 200.0), strict=True)
        )

    with pytest.raises(RuntimeError, match="cross"):
        b.corrected_layout(_geom(), compile_fn=swallow)


def test_the_map_compiles_to_the_measured_layout(tmp_path: Path) -> None:
    import sumolib

    from microsim.networks import accel_lane_end, osm_import

    layout = b.corrected_layout(_geom())
    for e in b.CORRIDOR_EDGES:
        assert layout["compiled_lengths_m"][e] == pytest.approx(
            layout["measured_lengths_m"][e], abs=b.LAYOUT_TOL_M
        )
    osm = tmp_path / "i80.osm"
    osm.write_text(b.osm_xml(layout["node_s_m"]))
    bundle = osm_import(
        osm_file=osm,
        corridor_edges=list(b.CORRIDOR_EDGES),
        workdir=tmp_path / "net",
        keep_edges=[b.EDGE_RAMP],
    )
    net = sumolib.net.readNet(str(bundle.net_path))
    lanes = {e: net.getEdge(e).getLaneNumber() for e in (*b.CORRIDOR_EDGES, b.EDGE_RAMP)}
    assert lanes == {"9001": 6, "9002": 6, "9003": 7, "9004": 6, "9005": 6, "9010": 1}
    assert net.getEdge("9002").getSpeed() == pytest.approx(65 * 0.44704, abs=0.01)
    assert net.getEdge("9010").getSpeed() == pytest.approx(22.22, abs=0.01)

    def outs(edge: str) -> list[tuple[int, str, int]]:
        return sorted(
            (lane.getIndex(), c.getTo().getID(), c.getToLane().getIndex())
            for lane in net.getEdge(edge).getLanes()
            for c in lane.getOutgoing()
        )

    assert outs("9010") == [(0, "9003", 0)]  # the ramp feeds the acceleration lane
    assert outs("9002") == [(i, "9003", i + 1) for i in range(6)]
    assert outs("9003") == [(i, "9004", i - 1) for i in range(1, 7)]  # lane 0 ends
    assert accel_lane_end(net, list(b.CORRIDOR_EDGES), "9003").ok
    starts = dict(zip(bundle.edge_ids, bundle.offsets, strict=True))
    acc = starts["9004"] - starts["9003"]
    assert acc == pytest.approx(318.7 - 112.4, abs=b.LAYOUT_TOL_M)


def test_build_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    periods = _periods()
    monkeypatch.setattr(d, "load_periods", lambda labels=None, directory=None: periods)
    summary = tmp_path / "i80_data.json"
    infos = [d.period_info(k, v) for k, v in periods.items()]
    summary.write_text(
        json.dumps(
            {
                "replica_block": ["p1", "p2"],
                "data_hash": "f" * 64,
                "data_version": "synthetic",
                "periods": infos,
            }
        )
    )
    out, inputs_out, osm_out = (
        tmp_path / "scenarios" / "i80_replica.yaml",
        tmp_path / "i80_replica_inputs.json",
        tmp_path / "osm" / "i80_ngsim.osm",
    )
    args = argparse.Namespace(
        data_summary=str(summary),
        block=None,
        fleet_from=str(b.FLEET_FROM),
        entry_lanes="observed",
        out=str(out),
        inputs_out=str(inputs_out),
        osm_out=str(osm_out),
    )
    inputs = b.build(args)
    raw = yaml.safe_load(out.read_text())
    cfg = b.ScenarioConfig.from_yaml(out)
    assert b.config_hash(cfg) == inputs["scenario"]["config_hash"]
    assert raw["name"] == "i80_replica" and raw["seed"] == 42 and raw["replicates"] == 20
    assert raw["fleet"] == yaml.safe_load(b.FLEET_FROM.read_text())["fleet"]
    net = raw["network"]
    assert net["corridor_edges"] == ["9001", "9002", "9003", "9004", "9005"]
    (ramp,) = net["ramps"]
    assert ramp["attach_edge"] == "9003" and "merge" not in ramp  # the kept configuration
    assert cfg.network.ramps[0].merge == "lane_change"
    assert net["inflow"][0][0] == 0.0 and net["inflow"][1][0] == 180.0 + 300.0
    assert net["boundary"]["steps"][0][0] == 0.0 and net["boundary"]["steps"][1][0] == 210.0
    assert len(net["entry_lane_shares"]) == 6 and sum(net["entry_lane_shares"]) == pytest.approx(
        1.0, abs=1e-5
    )
    span = inputs["block_span_s"]
    assert raw["sim"]["duration_s"] == float(np.ceil(180.0 + span))
    assert raw["sim"]["warmup_s"] == 180.0 and raw["sim"]["output_hz"] == 2.0
    geom = inputs["geometry"]
    assert 0 < geom["gore_m"] < geom["accel_end_m"] < geom["site_length_m"]
    assert inputs["corridor"]["edge_spans_data_m"]["9003"] == [geom["gore_m"], geom["accel_end_m"]]
    assert inputs["nothing_fitted"].startswith("every number below")
    assert inputs["fleet"]["block"] == raw["fleet"]
    assert [w["period"] for w in inputs["analysis_windows"]] == ["p1", "p2"]
    assert b.sha256_text(osm_out.read_text()) == inputs["osm"]["sha256"]
    # after the ingest the map is rebuilt from the artifact alone
    rebuilt = b.write_osm(inputs_out, tmp_path / "rebuilt.osm")
    assert rebuilt.read_text() == osm_out.read_text()
    broken = json.loads(inputs_out.read_text())
    broken["osm"]["layout"]["node_s_m"]["3"] += 1.0
    (tmp_path / "broken.json").write_text(json.dumps(broken))
    with pytest.raises(RuntimeError, match="sha256"):
        b.write_osm(tmp_path / "broken.json", tmp_path / "x.osm")
    head = out.read_text().splitlines()[:6]
    assert any(inputs["scenario"]["config_hash"] in line for line in head)
