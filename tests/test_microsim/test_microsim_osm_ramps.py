"""OSM corridors with interchange ramps and a measured downstream boundary.

A hand-written OSM fixture laid out like a real interchange: a three-way
mainline (two lanes, widening to three after the merge so the on-ramp feeds
an auxiliary lane as real gore geometry does — a ramp squeezed into a
same-width edge would face SUMO's priority-junction gap acceptance and never
merge against a steady stream), an off-ramp link leaving at the first
junction and an on-ramp link joining at the second (ramps sharing one node
would create a spurious ramp-to-ramp movement that keeps the merge minor). Exercises
``microsim.networks.osm_import(keep_edges=...)``, the ramp route machinery
(``RampSpec``, docs/CONTRACTS.md §2) and the OSM boundary edge end to end
through ``run_micro`` (real SUMO — integration marker).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
import sumolib

from flowstate_core.config import ScenarioConfig
from microsim import osm_import, run_micro
from microsim.paths import ROOTS_ENV_VAR

pytestmark = pytest.mark.integration

# Nodes along a line of latitude; 0.006° of longitude ≈ 510 m at 40° N. The
# ramp links leave/join at a shallow (~6°) angle like real gore geometry —
# SUMO caps turning speed by curvature, so a steep hand-drawn ramp would be
# an artificial 5 m/s bottleneck, not a merge.
RAMP_OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="hand-written-test-fixture">
  <node id="1" lat="40.0000" lon="-96.0000"/>
  <node id="2" lat="40.0000" lon="-95.9940"/>
  <node id="3" lat="40.0000" lon="-95.9880"/>
  <node id="4" lat="40.0000" lon="-95.9820"/>
  <node id="10" lat="39.9994" lon="-95.9960"/>
  <node id="11" lat="39.9994" lon="-95.9860"/>
  <way id="100">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="motorway"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="2"/>
  </way>
  <way id="101">
    <nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="motorway"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="2"/>
  </way>
  <way id="102">
    <nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="motorway"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="3"/>
  </way>
  <way id="200">
    <nd ref="10"/><nd ref="3"/>
    <tag k="highway" v="motorway_link"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="1"/>
  </way>
  <way id="201">
    <nd ref="2"/><nd ref="11"/>
    <tag k="highway" v="motorway_link"/>
    <tag k="oneway" v="yes"/>
    <tag k="lanes" v="1"/>
  </way>
</osm>
"""


@pytest.fixture
def osm_path(tmp_path):
    p = tmp_path / "ramps.osm"
    p.write_text(RAMP_OSM)
    return p


def _scenario(osm_path, *, ramps=True, boundary=False, duration_s=150.0) -> ScenarioConfig:
    net = {
        "kind": "osm",
        "osm_file": str(osm_path),
        "corridor_edges": ["100", "101", "102"],
        "inflow": [[0.0, 0.5]],
    }
    if ramps:
        net["ramps"] = [
            {
                "kind": "on",
                "edges": ["200"],
                "attach_edge": "102",
                "inflow": [[0.0, 0.2]],
                "name": "on",
            },
            {"kind": "off", "edges": ["201"], "attach_edge": "100", "exit_fraction": [[0.0, 0.15]]},
        ]
    if boundary:
        net["boundary"] = {"steps": [[0.0, 3.0]]}
    return ScenarioConfig.model_validate(
        {"name": "osm_ramps_smoke", "network": net, "sim": {"duration_s": duration_s}, "seed": 7}
    )


class TestOSMImportKeepEdges:
    def test_ramp_edges_survive_pruning(self, osm_path, tmp_path):
        bundle = osm_import(
            osm_file=osm_path,
            corridor_edges=("100", "101", "102"),
            keep_edges=("200", "201"),
            workdir=tmp_path / "w",
        )
        assert bundle.edge_ids == ("100", "101", "102")  # corridor order, ramps not in the chain
        net = sumolib.net.readNet(str(bundle.net_path))
        ids = {e.getID() for e in net.getEdges(withInternal=False)}
        assert {"100", "101", "102", "200", "201"} <= ids
        assert "102" in {e.getID() for e in net.getEdge("200").getOutgoing()}
        assert "201" in {e.getID() for e in net.getEdge("100").getOutgoing()}

    def test_missing_kept_edge_raises(self, osm_path, tmp_path):
        with pytest.raises(ValueError, match="not present after import"):
            osm_import(
                osm_file=osm_path,
                corridor_edges=("100", "101", "102"),
                keep_edges=("999",),
                workdir=tmp_path / "w",
            )


class TestRampRun:
    def test_ramp_vehicles_join_and_leave_the_corridor(self, osm_path, tmp_path):
        cfg = _scenario(osm_path)
        paths = run_micro(cfg, 7, tmp_path / "run")
        meta = json.loads(paths.meta.read_text())
        assert meta["seeded"] is False
        ramps = meta["ramps"]
        assert [r["kind"] for r in ramps] == ["on", "off"]
        on, off = ramps
        assert on["n_planned"] == 30 and on["n_departed"] > 20  # 150 s at 0.2 veh/s
        assert 3 <= off["n_planned_exiting"] <= 25  # ~15% of 75 mainline vehicles
        assert meta["n_vehicles_departed"] > 90

        df = pd.read_parquet(paths.trajectories)
        # Ramp vehicles appear in the corridor trajectories once they are on
        # a corridor edge — they are the last ids in the plan (v00075...).
        on_ids = {f"v{i:05d}" for i in range(75, 105)}
        seen_on = on_ids & set(df["veh_id"])
        assert len(seen_on) >= 15
        # ...and only downstream of the merge (edge 102 starts ~1020 m in).
        merged = df[df["veh_id"].isin(seen_on)]
        assert merged["x"].min() >= 950.0
        # Positions are within the corridor's linear extent (no ramp x leaks).
        assert df["x"].between(0.0, meta["config"]["sim"]["duration_s"] * 40.0).all()

    def test_ramp_connectivity_is_validated(self, osm_path, tmp_path):
        cfg = _scenario(osm_path)
        bad = cfg.model_dump(mode="json")
        bad["network"]["ramps"][0]["attach_edge"] = "101"  # link 200 joins at 102, not 101
        with pytest.raises(ValueError, match="does not connect"):
            run_micro(ScenarioConfig.model_validate(bad), 7, tmp_path / "bad")

    def test_osm_boundary_applies_to_last_corridor_edge(self, osm_path, tmp_path):
        free = run_micro(_scenario(osm_path, ramps=False), 7, tmp_path / "free")
        bound = run_micro(_scenario(osm_path, ramps=False, boundary=True), 7, tmp_path / "bound")
        meta = json.loads(bound.meta.read_text())
        assert meta["boundary"]["exit_edge"] == "102"
        assert meta["boundary"]["n_steps_applied"] == 1
        assert meta["boundary"]["exit_buffer_m"] == pytest.approx(
            sumolib.net.readNet(
                str(tmp_path / "bound" / meta["config_hash"] / "7" / "net" / "osm.net.xml")
            )
            .getEdge("102")
            .getLength()
        )
        df_free = pd.read_parquet(free.trajectories)
        df_bound = pd.read_parquet(bound.trajectories)
        on_exit = df_bound[(df_bound["x"] >= 1100.0) & (df_bound["t"] >= 60.0)]
        assert len(on_exit) > 0 and on_exit["v"].quantile(0.9) <= 3.5
        # Congestion spills back into the measured span (end of edge 101).
        tail = lambda d: d[(d["x"] >= 800.0) & (d["x"] < 1000.0) & (d["t"] >= 90.0)]["v"].mean()  # noqa: E731
        assert tail(df_bound) < tail(df_free) - 2.0


class TestControllerWithRamps:
    def test_avs_entering_from_a_ramp_do_not_crash_dispatch(self, osm_path, tmp_path):
        """A compliant AV on a ramp edge has no corridor position; the
        controller loop must skip it until it reaches the corridor (the
        I-24 sweep failed on exactly this before the fix)."""
        cfg = _scenario(osm_path, duration_s=150.0)
        d = cfg.model_dump(mode="json")
        d["av"] = {"penetration": 0.3, "compliance": 1.0, "controller": "follower_stopper"}
        cfg = ScenarioConfig.model_validate(d)
        paths = run_micro(cfg, 7, tmp_path / "ctrl")
        meta = json.loads(paths.meta.read_text())
        assert meta["controller"] == "follower_stopper"
        assert meta["ramps"][0]["n_departed"] > 20
        # Some AVs are ramp-origin vehicles (ids >= 75) and appear on the corridor.
        df = pd.read_parquet(paths.trajectories)
        av_ramp = df[df["is_av"] & (df["veh_id"].str[1:].astype(int) >= 75)]
        assert len(av_ramp) > 0


class TestOSMImportPathRoots:
    """``network.osm_file`` confinement (:mod:`microsim.paths`).

    Defence in depth behind the API's HTTP 422 (``path_outside_roots``):
    ``osm_import`` refuses a file resolving outside the allow-listed roots
    before it creates the workdir or calls netconvert, so a config that
    reached the worker unvalidated cannot hand an arbitrary server file to
    the importer. (``bbox`` downloads and ``patch_files`` are written by
    ``osm_import`` itself into the workdir and are not confined.)
    """

    def test_file_inside_a_root_imports(self, osm_path, tmp_path):
        bundle = osm_import(
            osm_file=osm_path,
            corridor_edges=("100", "101", "102"),
            workdir=tmp_path / "w",
            allowed_roots=(tmp_path,),
        )
        assert bundle.edge_ids == ("100", "101", "102")

    def test_file_outside_the_roots_is_refused_before_any_work(self, osm_path, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        workdir = tmp_path / "w"
        with pytest.raises(ValueError) as exc:
            osm_import(osm_file=osm_path, workdir=workdir, allowed_roots=(allowed,))
        msg = str(exc.value)
        assert str(osm_path) in msg and str(allowed) in msg
        assert "hand-written-test-fixture" not in msg  # nothing read from the file
        assert not workdir.exists()  # refused before the workdir is created

    def test_parent_traversal_is_resolved_before_the_check(self, osm_path, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        sneaky = allowed / ".." / osm_path.name
        with pytest.raises(ValueError, match="outside the allowed data roots"):
            osm_import(osm_file=sneaky, workdir=tmp_path / "w", allowed_roots=(allowed,))

    def test_symlink_escape_is_refused(self, osm_path, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        link = allowed / "ramps.osm"
        link.symlink_to(osm_path)
        assert link.is_file()  # lexically inside the root, really outside it
        with pytest.raises(ValueError, match="outside the allowed data roots"):
            osm_import(osm_file=link, workdir=tmp_path / "w", allowed_roots=(allowed,))

    def test_environment_confines_when_no_argument_is_given(self, osm_path, tmp_path, monkeypatch):
        """The worker hook: ``api.jobs`` publishes the roots in the environment."""
        monkeypatch.setenv(ROOTS_ENV_VAR, str(tmp_path / "allowed"))
        with pytest.raises(ValueError, match="outside the allowed data roots"):
            osm_import(osm_file=osm_path, workdir=tmp_path / "w")
        # Inside the published root the check passes and the honest
        # "not found" of a missing extract is what surfaces.
        monkeypatch.setenv(ROOTS_ENV_VAR, str(tmp_path))
        with pytest.raises(ValueError, match="osm_file not found"):
            osm_import(osm_file=tmp_path / "missing.osm", workdir=tmp_path / "w")

    def test_unrestricted_when_neither_argument_nor_environment_is_set(self, tmp_path, monkeypatch):
        """The library default for scripts: no allow-list, no check."""
        monkeypatch.delenv(ROOTS_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="osm_file not found"):
            osm_import(osm_file=tmp_path / "missing.osm", workdir=tmp_path / "w")
