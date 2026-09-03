"""Micro-tier VSL: gantry segmentation of a ``NetBundle`` and compliance-scaled posting.

``NetBundle.segments`` is exercised on synthetic bundles (no SUMO). The
posting tests run real SUMO (marked ``integration``): a short corridor whose
threshold controller is forced down its ladder (escalation trigger ``v_on``
above every plausible speed), so the posted limits fall below the free-flow
cap within the 2-minute run and the compliance scaling is visible in what
SUMO holds on the edges (read back via ``lane.getMaxSpeed`` into meta.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from controllers import default_params
from controllers.vsl import effective_limit
from flowstate_core.config import ScenarioConfig
from microsim import run_micro
from microsim.networks import EDGE_SPEED_LIMIT_MS, NetBundle


def _bundle(
    lengths: list[float],
    *,
    ids: list[str] | None = None,
    kind: str = "corridor",
    entry_edge: str | None = None,
    exit_edge: str | None = None,
) -> NetBundle:
    """Synthetic bundle (no netconvert): offsets/lengths only."""
    ids = ids or [f"e{i}" for i in range(len(lengths))]
    offsets = [0.0]
    for elen in lengths[:-1]:
        offsets.append(offsets[-1] + elen)
    return NetBundle(
        net_path=Path("unused.net.xml"),
        edge_ids=tuple(ids),
        edge_lengths=tuple(lengths),
        offsets=tuple(offsets),
        total_length_m=float(sum(lengths)),
        workdir=Path("."),
        kind=kind,  # type: ignore[arg-type]
        entry_edge=entry_edge,
        exit_edge=exit_edge,
    )


class TestNetBundleSegments:
    def test_generated_corridor_one_segment_per_1km_edge(self):
        ids = ["entry", *[f"ce{i}" for i in range(10)]]
        b = _bundle([2000.0, *[1000.0] * 10], ids=ids, entry_edge="entry")
        assert b.segments() == [(e,) for e in b.main_edges]
        assert "entry" not in {e for seg in b.segments() for e in seg}

    def test_single_long_edge_is_one_segment(self):
        b = _bundle([600.0, 600.0], ids=["entry", "ce0"], entry_edge="entry")
        assert b.segments() == [("ce0",)]
        b2 = _bundle([2000.0, 2000.0], ids=["entry", "ce0"], entry_edge="entry")
        assert b2.segments() == [("ce0",)]

    def test_osm_short_ways_merge_never_split_total_preserved(self):
        lengths = [1500.0, 200.0, 300.0, 900.0, 100.0]
        b = _bundle(lengths, ids=["w1", "w2", "w3", "w4", "w5"], kind="osm")
        segs = b.segments()
        assert segs == [("w1",), ("w2", "w3", "w4", "w5")]
        length_by_id = dict(zip(b.edge_ids, b.edge_lengths, strict=True))
        seg_len = [sum(length_by_id[e] for e in seg) for seg in segs]
        assert all(sl >= 500.0 for sl in seg_len)
        assert sum(seg_len) == sum(lengths)
        assert [e for seg in segs for e in seg] == list(b.edge_ids)

    def test_exit_buffer_excluded(self):
        b = _bundle(
            [2000.0, 1000.0, 1000.0, 200.0],
            ids=["entry", "ce0", "ce1", "exit"],
            entry_edge="entry",
            exit_edge="exit",
        )
        assert b.segments() == [("ce0",), ("ce1",)]

    def test_sugiyama_ring_is_one_segment(self):
        b = _bundle([230.0 / 8.0] * 8, ids=[f"re{i}" for i in range(8)], kind="ring")
        assert b.segments() == [tuple(f"re{i}" for i in range(8))]

    def test_target_override_and_validation(self):
        b = _bundle([2000.0, *[500.0] * 4], ids=["entry", "a", "b", "c", "d"], entry_edge="entry")
        assert b.segments(target_m=500.0) == [("a",), ("b",), ("c",), ("d",)]
        with pytest.raises(ValueError, match="target_m"):
            b.segments(target_m=0.0)


# --- Integration: real SUMO ---------------------------------------------------

#: Escalation trigger far above any driven speed ⇒ the upstream segment steps
#: down the ladder at every dispatch once the downstream segment is occupied.
ALWAYS_CONGESTED = {"v_on": 40.0}


def _vsl_cfg(compliance: float) -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "name": "vsl_compliance",
            "network": {
                "kind": "corridor",
                "length_m": 2000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.45]],
            },
            "av": {
                "penetration": 0.0,
                "compliance": compliance,
                "vsl": "vsl_threshold",
                "vsl_params": ALWAYS_CONGESTED,
            },
            "sim": {"duration_s": 120.0},
        }
    )


@pytest.fixture(scope="module")
def dispatch_meta(tmp_path_factory: pytest.TempPathFactory) -> dict[float, dict[str, Any]]:
    out: dict[float, dict[str, Any]] = {}
    for compliance in (0.5, 1.0):
        paths = run_micro(_vsl_cfg(compliance), 7, tmp_path_factory.mktemp(f"vsl_{compliance}"))
        out[compliance] = json.loads(paths.meta.read_text())["vsl_dispatch"]
    return out


@pytest.mark.integration
class TestCompliancePosting:
    def test_segments_base_limits_and_cadence(self, dispatch_meta):
        d = dispatch_meta[0.5]
        assert d["controller"] == "vsl_threshold"
        assert d["compliance"] == 0.5
        assert d["segments"] == [["ce0"], ["ce1"]]
        assert d["segment_lengths_m"] == [1000.0, 1000.0]
        assert d["edges"] == ["ce0", "ce1"]
        assert d["base_limit_ms_by_edge"] == {
            "ce0": EDGE_SPEED_LIMIT_MS,
            "ce1": EDGE_SPEED_LIMIT_MS,
        }
        assert d["interval_s"] == 30.0
        assert d["n_dispatches"] == 4  # 120 s / 30 s

    def test_half_compliance_posts_strictly_between_limit_and_base(self, dispatch_meta):
        d = dispatch_meta[0.5]
        for h in d["history"]:
            assert len(h["posted_ms"]) == 2 and len(h["applied_ms"]) == 2
            for seg_idx, eid in enumerate(d["edges"]):
                posted = h["posted_ms"][seg_idx]
                base = d["base_limit_ms_by_edge"][eid]
                applied = h["applied_ms"][seg_idx]
                assert posted < base
                assert posted < applied < base
                assert applied == pytest.approx(effective_limit(posted, base, 0.5), abs=1e-9)
                assert applied == pytest.approx(0.5 * posted + 0.5 * base, abs=1e-9)

    def test_ladder_is_exercised_within_the_run(self, dispatch_meta):
        # Fixed seed + fixed SUMO version ⇒ deterministic: the upstream
        # segment escalates below the free-flow cap before 120 s.
        v_free = default_params("vsl_threshold")["v_free"]
        posted_seg0 = [h["posted_ms"][0] for h in dispatch_meta[0.5]["history"]]
        assert min(posted_seg0) < v_free
        assert posted_seg0 == sorted(posted_seg0, reverse=True)  # never re-escalates up

    def test_full_compliance_posts_the_raw_limit(self, dispatch_meta):
        """compliance = 1 ⇒ exactly the pre-scaling behaviour."""
        for h in dispatch_meta[1.0]["history"]:
            assert h["applied_ms"] == h["posted_ms"]
