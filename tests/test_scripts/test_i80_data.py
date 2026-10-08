"""scripts/i80_data.py (E11, docs/PRE_FRISCO_PROGRAM.md): the I-80 recipe on synthetic rows.

Nothing is downloaded: the fetch is exercised only through its URL builder and its row counter.
Synthetic NGSIM-schema periods (``ngsim_period`` below, shared with the builder's and the
measures' tests) stand in for the recording: two periods that overlap on the wall clock and a third
an hour earlier, exact duplicate rows, a slot clash, vehicles present in the first frame, censored
late entries, ramp vehicles that change from lane 7 to lane 6 in an acceleration lane.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.parse
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

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


d = _load("i80_data")

SITE_L = 300.0
GORE, LANE7_END = 100.0, 180.0
VM, VR = 10.0, 12.0
T0_MS = 1_113_436_800_000  # 2005-04-13 17:00:00 PDT


def ngsim_period(
    origin_ms: int,
    n_frames: int,
    *,
    seed: int,
    entry_every_s: float = 1.0,
    ramp_every_s: float = 6.0,
    initial: int = 30,
) -> pd.DataFrame:
    """One synthetic recording period in the loader's schema.

    Mainline vehicles enter at ``x = 0`` on lanes 1-6 every ``entry_every_s`` and drive at 10 m/s
    to the site's end (300 m, 30 s); ramp vehicles appear on lane 7 at ``x = 40`` every
    ``ramp_every_s``, drive at 12 m/s and change to lane 6 at 120-160 m; ``initial`` vehicles are in
    the site at the first frame. NGSIM's censoring: a vehicle that cannot finish its traverse
    before the period's end is left out.
    """
    rng = np.random.default_rng(seed)
    dt = 0.1
    t_end = (n_frames - 1) * dt
    rows: list[pd.DataFrame] = []
    vid = 0

    def add(t0: float, x0: float, v: float, lanes_fn, length: float = 4.5, cls: int = 2) -> None:
        nonlocal vid
        f0 = round(t0 / dt)
        x_end_t = t0 + (SITE_L - x0) / v
        if x_end_t > t_end:
            return  # censored: cannot finish before the cut
        f1 = int(np.floor(x_end_t / dt))
        frames = np.arange(f0, f1 + 1)
        t = frames * dt
        x = x0 + v * (t - t0)
        vid += 1
        rows.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": str(vid),
                    "frame": frames,
                    "lane": lanes_fn(x),
                    "v": v,
                    "leader_id": "0",
                    "spacing_m": 0.0,
                    "length_m": length,
                    "x": x,
                    "global_time_ms": origin_ms + frames * 100,
                    "v_class": cls,
                }
            )
        )

    for k in range(initial):  # present at the first frame, spread over the site
        lane = 1 + k % 6
        add(0.0, 5.0 + k * 285.0 / initial, VM, lambda x, lane=lane: np.full(x.shape, lane))
    t = 0.5
    while t < t_end:
        lane = int(rng.integers(1, 7))
        add(t, 0.0, VM, lambda x, lane=lane: np.full(x.shape, lane), cls=3 if lane == 6 else 2)
        t += entry_every_s
    t = 2.0
    while t < t_end:
        xc = float(rng.uniform(GORE + 20.0, LANE7_END - 20.0))
        add(t, 40.0, VR, lambda x, xc=xc: np.where(x < xc, 7, 6))
        t += ramp_every_s
    return pd.concat(rows, ignore_index=True)


def two_period_block() -> dict[str, pd.DataFrame]:
    """p1 an hour early; p2 and p3 overlap by 20 s (ids and frames restart per period)."""
    p1 = ngsim_period(T0_MS - 3_600_000, 1200, seed=1)
    p2 = ngsim_period(T0_MS, 1800, seed=2)
    p3 = ngsim_period(T0_MS + 160_000, 1800, seed=3)
    raw = pd.concat([p1, p2, p3], ignore_index=True)
    periods, _ = d.dedupe_and_split(raw)
    return periods


# --- fetch helpers -----------------------------------------------------------------------------


def test_chunk_url_pages_the_i80_subset_by_row_id() -> None:
    url = d.chunk_url(400_000, 200_000)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith("https://data.transportation.gov/resource/8ect-6jqj.csv?")
    assert q == {
        "$where": "location='i-80'",
        "$order": ":id",
        "$limit": "200000",
        "$offset": "400000",
    }
    with pytest.raises(ValueError):
        d.chunk_url(-1)


def test_count_rows_excludes_the_header_with_or_without_a_final_newline(tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    a.write_text("h1,h2\n1,2\n3,4\n")
    b = tmp_path / "b.csv"
    b.write_text("h1,h2\n1,2\n3,4")
    c = tmp_path / "c.csv"
    c.write_text("h1,h2\n")
    assert d.count_rows(a) == 2
    assert d.count_rows(b) == 2
    assert d.count_rows(c) == 0


def test_data_hash_is_the_sha256_of_the_sorted_chunk_hashes(tmp_path: Path) -> None:
    import hashlib

    f1, f0 = tmp_path / "i80_chunk_01.csv", tmp_path / "i80_chunk_00.csv"
    f0.write_text("a\n")
    f1.write_text("b\n")
    inner = [hashlib.sha256(f.read_bytes()).hexdigest() for f in (f0, f1)]
    outer = hashlib.sha256("".join(inner).encode()).hexdigest()
    assert d.data_hash([f1, f0]) == outer
    assert d.chunk_files(tmp_path) == [f0, f1]


# --- periods ----------------------------------------------------------------------------------


def test_duplicates_dropped_periods_split_on_the_recording_origin() -> None:
    p2 = ngsim_period(T0_MS, 600, seed=2)
    p3 = ngsim_period(T0_MS + 200_000, 600, seed=3)
    raw = pd.concat([p2, p3, p2.iloc[:500], p3.iloc[100:300]], ignore_index=True)
    clash = p3.iloc[[10]].copy()
    clash["v"] = 99.0  # a second, different row of one vehicle in one frame
    raw = pd.concat([raw, clash], ignore_index=True)
    periods, counts = d.dedupe_and_split(raw)
    assert list(periods) == ["p1", "p2"]
    assert counts["n_exact_duplicates"] == 700
    assert counts["periods"]["p1"]["n_conflicting_slots"] == 0
    assert counts["periods"]["p2"]["n_conflicting_slots"] == 1
    assert counts["periods"]["p1"]["origins_ms"] == [T0_MS]
    assert len(periods["p1"]) == len(p2) and len(periods["p2"]) == len(p3)
    # ids restart per period: the same id is a different vehicle in each
    assert set(periods["p1"]["veh_id"]) & set(periods["p2"]["veh_id"])
    assert (periods["p2"]["origin_ms"] == T0_MS + 200_000).all()


def test_origins_a_few_ms_apart_are_one_period() -> None:
    idx, members = d.period_index([1000, 1003, 1000, 1000 + 900_000, 1001 + 900_000])
    assert idx.tolist() == [0, 0, 0, 1, 1]
    assert members == [[1000, 1003], [901000, 901001]]


def test_contiguous_blocks_and_the_longest() -> None:
    periods = two_period_block()
    infos = [d.period_info(k, v) for k, v in periods.items()]
    assert d.contiguous_blocks(infos) == [["p1"], ["p2", "p3"]]
    assert d.longest_block(infos) == ["p2", "p3"]
    # a gap above the bound splits a block
    far = [dict(r) for r in infos]
    far[2]["start_ms"] = far[1]["end_ms"] + 61_000
    assert d.contiguous_blocks(far) == [["p1"], ["p2"], ["p3"]]
    assert d.period_info("p2", periods["p2"])["wall_clock"][0].startswith(
        "2005-04-13 17:00:00.0 PDT"
    )


def test_entries_exclude_the_initial_state_and_split_by_stream() -> None:
    p = ngsim_period(T0_MS, 1200, seed=4, initial=12)
    ent = d.stream_entries(p)
    first = d.first_samples(p)
    present = first[first["frame"] == p["frame"].min()]
    assert len(present) == 12
    assert set(ent["mainline"]["veh_id"]).isdisjoint(set(present["veh_id"]))
    assert (ent["mainline"]["lane"].between(1, 6)).all()
    assert (ent["ramp"]["lane"] == 7).all()
    assert len(ent["ramp"]) > 0
    # US-101's spatial rule counts the same new mainline vehicles here (all enter at x = 0) and
    # the initial ones within 30 m
    assert set(ent["mainline"]["veh_id"]) <= set(ent["mainline_spatial"]["veh_id"])


def test_switches_stitch_and_windows() -> None:
    periods = two_period_block()
    block = ["p2", "p3"]
    sw = d.switch_times_ms(periods, block)
    last_p2 = float(d.stream_entries(periods["p2"])["mainline"]["global_time_ms"].max())
    assert sw == [last_p2, float("inf")]
    t = np.array([last_p2 - 100, last_p2, last_p2 + 100])
    assert d.stitched_mask(t, 0, sw).tolist() == [True, True, False]
    assert d.stitched_mask(t, 1, sw).tolist() == [False, False, True]
    win = d.analysis_windows(periods, block)
    assert [w["period"] for w in win] == block
    t0 = d.block_t0_ms(periods, block)
    assert win[0]["lo_s"] == 0.0
    assert win[0]["hi_s"] == pytest.approx((last_p2 - t0) / 1000.0 + 0.1, abs=0.05)
    # p3 opens at the later of p2's switch and its own first (populated) frame: here p2's last
    # entry (149.5 s) comes before p3's recording starts (160 s), and the gap is read by neither
    assert win[0]["hi_s"] < 160.0
    assert win[1]["lo_s"] == pytest.approx(160.0)
    assert win[1]["origin_offset_s"] == pytest.approx(160.0)
    assert d.block_span_s(periods, block) == pytest.approx(160.0 + 179.9, abs=0.5)


def test_an_empty_start_opens_the_window_when_the_site_fills() -> None:
    p = ngsim_period(T0_MS, 1200, seed=5, initial=0)
    start = d.populated_start_ms(p)
    assert start > int(p["global_time_ms"].min())
    full = ngsim_period(T0_MS, 1200, seed=5, initial=30)
    assert d.populated_start_ms(full) == int(full["global_time_ms"].min())


# --- geometry ------------------------------------------------------------------------------------


def test_merge_zone_site_length_and_the_lane_check() -> None:
    periods = two_period_block()
    frames = [periods["p2"], periods["p3"]]
    lo, hi, counts = d.merge_zone_bounds(frames)
    assert GORE + 20.0 - 0.5 <= lo <= GORE + 25.0  # the earliest changes (uniform 120-160 m)
    assert GORE + 40.0 <= hi <= LANE7_END - 20.0 + 0.5  # the latest lane-7 positions
    assert counts["n_transitions_7_to_6"] > 30
    assert d.site_length_m(frames) == pytest.approx(SITE_L - 1.0, abs=1.0)
    assert d.check_lanes(frames)["lanes_present"] == [1, 2, 3, 4, 5, 6, 7]
    exit_lane = periods["p2"].copy()
    ids = exit_lane["veh_id"].unique()[:20]
    exit_lane.loc[exit_lane["veh_id"].isin(ids) & (exit_lane["x"] > 250.0), "lane"] = 8
    with pytest.raises(ValueError, match="exit lane"):
        d.check_lanes([exit_lane])
    with pytest.raises(ValueError, match="no samples"):
        d.check_lanes([periods["p2"][periods["p2"]["lane"] != 7]])
