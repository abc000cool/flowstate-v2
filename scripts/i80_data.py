"""NGSIM I-80 data access for E11 (docs/PRE_FRISCO_PROGRAM.md, "E11"): fetch, de-duplicate, split.

E11 measures merge gaps and speeds on a site the model was never calibrated on. This module is the
I-80 counterpart of ``scripts/us101_data.py`` and follows its recipe (docs/M2_RESULTS.md §1):

* **Fetch, on the VM only.** The raw NGSIM I-80 trajectories from data.transportation.gov (Socrata
  resource ``8ect-6jqj``, ``location='i-80'``) through the committed
  :func:`calibration.data_fetch.fetch_ngsim_i80`, as 200,000-row CSV chunks
  ``data/ngsim/i80_chunk_NN.csv`` (gitignored). Pages are ordered by Socrata's row id ``:id`` (the
  documented stable paging order; US-101 was exported ordered by ``(global_time, vehicle_id)``,
  which is not unique and can shift rows across page boundaries). The raw export only:
  reconstructed NGSIM is out of scope by owner rule. Never called from tests; the laptop never
  downloads it.
* **Duplicates.** The Socrata export holds exact duplicate rows (21.5 % of US-101's); they are
  dropped, as US-101's were, after the loader's parse. A residual second row of one vehicle in one
  frame of a period (two rows that differ) is counted and dropped (the first kept).
* **Recording periods.** ``vehicle_id`` and ``frame_id`` restart per recording period, so periods
  are split on the recording origin ``global_time - frame_id * 100 ms`` (US-101's rule, constant per
  period there). Origins within :data:`PERIOD_ORIGIN_GAP_MS` of each other are one period (on
  US-101 each period has exactly one origin; the count per period is recorded), labelled
  ``p1, p2, ...`` in recording order.
* **Blocks.** Periods whose wall-clock spans touch or overlap (gap at most :data:`BLOCK_MAX_GAP_S`)
  form a block; E11's replica models the longest block (FHWA-HRT-06-137: 16:00-16:15, 17:00-17:15
  and 17:15-17:30 PDT, so the 17:00-17:30 pair, if the data agree).
* **Stitching a block** (US-101's demand rule, docs/M2_RESULTS.md §5.1, applied to every use):
  NGSIM's period processing censors a period's last entries (a vehicle that cannot finish its
  traverse before the cutoff is left out), so within a block an observation from period ``i`` is
  used up to and including period ``i``'s last recorded upstream mainline entry (the *switch*),
  and period ``i + 1`` strictly after it. The same switch bounds the windows in which lane changes
  are read (:func:`analysis_windows`).

Site facts (FHWA-HRT-06-137, "Next Generation Simulation: Interstate 80 Freeway Dataset", 2006):
eastbound I-80 in Emeryville, CA, recorded on 13 April 2005; a study area of about 500 m (1,650 ft)
with six freeway lanes including a high-occupancy-vehicle lane (lane 1, the leftmost) and an
on-ramp (Powell Street, lane 7 in the trajectory data) inside the area; 10 Hz; positions in feet
(``Local_Y``, the front centre of the vehicle along travel), which the loader converts to metres.

Run (VM, repository root)::

    uv run --no-sync python scripts/i80_data.py fetch
    uv run --no-sync python scripts/i80_data.py prepare --out artifacts/i80_data.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from calibration.data_fetch import NGSIM_DATASET_ID, NGSIM_SODA_CSV_URL, fetch_ngsim_i80
from calibration.loaders.ngsim import NGSIM_DT_S, load_ngsim_trajectories

REPO_ROOT = Path(__file__).resolve().parents[1]
NGSIM_DIR = REPO_ROOT / "data" / "ngsim"
PERIODS_DIR = NGSIM_DIR / "i80_periods"
MANIFEST = NGSIM_DIR / "i80_fetch_manifest.json"
SUMMARY_OUT = REPO_ROOT / "artifacts" / "i80_data.json"

LOCATION: Final[str] = "i-80"
CHUNK_ROWS: Final[int] = 200_000
"""Rows per fetched chunk (US-101's 200,000)."""
MAX_CHUNKS: Final[int] = 80
"""Safety stop of the fetch loop (16 M rows, several times the I-80 subset)."""
FETCH_TRIES: Final[int] = 3
FETCH_TIMEOUT_S: Final[float] = 600.0

NGSIM_DT_MS: Final[int] = round(NGSIM_DT_S * 1000.0)
"""NGSIM frame interval in the source's epoch-millisecond clock."""
PERIOD_ORIGIN_GAP_MS: Final[int] = 60_000
"""Recording origins closer than this are one period (periods start at least 15 min apart)."""
BLOCK_MAX_GAP_S: Final[float] = 60.0
"""Periods whose spans are separated by at most this are one wall-clock block."""

MAINLINE_LANES: Final[tuple[int, ...]] = (1, 2, 3, 4, 5, 6)
"""Mainline lane ids, 1 = the leftmost (the HOV lane), 6 = the rightmost (FHWA-HRT-06-137)."""
RAMP_LANE: Final[int] = 7
"""The Powell Street on-ramp and its acceleration lane."""
EXTRA_LANE_MAX_SHARE: Final[float] = 0.005
"""Largest share of vehicles whose last sample is on a lane id above :data:`RAMP_LANE` (an exit
lane the replica does not have) before :func:`check_lanes` refuses the site layout."""

V_CLASS_TRUCK: Final[int] = 3
"""NGSIM ``v_Class`` code for trucks (1 motorcycle, 2 auto)."""

SITE_LENGTH_QUANTILE: Final[float] = 0.999
"""The site's end: this quantile of the mainline samples' positions (US-101's end was read as
the observed ``local_y`` maximum, docs/M2_RESULTS.md §1)."""
ZONE_LO_QUANTILE: Final[float] = 0.005
"""The acceleration lane's start (the gore): this quantile of the positions at which a vehicle
first appears in lane 6 straight after lane 7."""
ZONE_HI_QUANTILE: Final[float] = 0.995
"""The acceleration lane's end: this quantile of the positions of lane-7 samples (US-101's
auxiliary-lane rule, ``scripts/lane_change_relaxation.py`` ``US101_ZONE_QUANTILES``)."""
POPULATED_SHARE: Final[float] = 0.5
"""A period's analysis window opens at its first frame holding at least this share of the
period's median vehicles per frame (a period whose start lacks the vehicles already in the site
would otherwise read gaps that are too large)."""

SITE_TZ: Final[str] = "America/Los_Angeles"
CITATION: Final[str] = (
    "U.S. Department of Transportation Federal Highway Administration (2016). Next Generation "
    "Simulation (NGSIM) Vehicle Trajectories and Supporting Data. data.transportation.gov, "
    "dataset 8ect-6jqj, location 'i-80' (raw export). Site: FHWA-HRT-06-137 (2006), NGSIM "
    "Interstate 80 Freeway Dataset fact sheet."
)


# --- fetch ---------------------------------------------------------------------------------


def chunk_url(offset: int, limit: int = CHUNK_ROWS) -> str:
    """The SODA CSV URL of one page of the I-80 subset, ordered by Socrata's row id."""
    if offset < 0 or limit <= 0:
        raise ValueError(f"bad page: offset {offset}, limit {limit}")
    params = {
        "$where": f"location='{LOCATION}'",
        "$order": ":id",
        "$limit": str(limit),
        "$offset": str(offset),
    }
    return f"{NGSIM_SODA_CSV_URL}?{urllib.parse.urlencode(params)}"


def count_rows(path: Path) -> int:
    """Data rows of a CSV (lines less the header; NGSIM fields hold no newlines)."""
    n = 0
    last = b"\n"
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 22):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    if last != b"\n":
        n += 1
    return max(n - 1, 0)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def chunk_files(directory: Path = NGSIM_DIR) -> list[Path]:
    """Sorted list of the I-80 chunk CSVs."""
    files = sorted(directory.glob("i80_chunk_*.csv"))
    if not files:
        raise FileNotFoundError(f"no i80_chunk_*.csv under {directory} (run the fetch on the VM)")
    return files


def data_hash(files: Sequence[Path] | None = None) -> str:
    """sha256 over the sorted chunk files' sha256 hex digests (US-101's provenance hash)."""
    outer = hashlib.sha256()
    for f in sorted(files if files is not None else chunk_files()):
        outer.update(file_sha256(f).encode())
    return outer.hexdigest()


def fetch(dest_dir: Path = NGSIM_DIR, *, chunk_rows: int = CHUNK_ROWS) -> dict[str, Any]:
    """Download the I-80 subset page by page (resumable: a chunk with its ``.ok`` marker is kept).

    Returns:
        The manifest (also written to ``dest_dir/i80_fetch_manifest.json``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict[str, Any]] = []
    for k in range(MAX_CHUNKS):
        path = dest_dir / f"i80_chunk_{k:02d}.csv"
        ok = path.with_suffix(".csv.ok")
        url = chunk_url(k * chunk_rows, chunk_rows)
        if not (path.is_file() and ok.is_file()):
            part = path.with_suffix(".csv.part")
            for attempt in range(1, FETCH_TRIES + 1):
                try:
                    fetch_ngsim_i80(part, url=url, timeout_s=FETCH_TIMEOUT_S)
                    break
                except OSError as exc:
                    print(f"chunk {k}: attempt {attempt} failed: {exc}", flush=True)
                    if attempt == FETCH_TRIES:
                        raise
                    time.sleep(30.0 * attempt)
            part.replace(path)
            ok.write_text(url + "\n")
        rows = count_rows(path)
        if rows == 0:
            path.unlink()
            ok.unlink(missing_ok=True)
            break
        chunks.append({"file": path.name, "url": url, "rows": rows, "sha256": file_sha256(path)})
        print(f"chunk {k}: {rows:,} rows", flush=True)
        if rows < chunk_rows:
            break
    else:
        raise RuntimeError(f"more than {MAX_CHUNKS} chunks: is the location filter applied?")
    manifest = {
        "dataset": NGSIM_DATASET_ID,
        "location": LOCATION,
        "order": ":id",
        "chunk_rows": chunk_rows,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_rows": int(sum(c["rows"] for c in chunks)),
        "chunks": chunks,
    }
    (dest_dir / MANIFEST.name).write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


# --- de-duplication and periods -------------------------------------------------------------


def period_index(origin_ms: Sequence[int] | np.ndarray) -> tuple[np.ndarray, list[list[int]]]:
    """Period of each row from its recording origin, and the origins that make each period.

    Origins closer than :data:`PERIOD_ORIGIN_GAP_MS` (sorted) are one period; periods are numbered
    in recording order from 0.
    """
    origin = np.asarray(origin_ms, dtype=np.int64)
    uniq = np.unique(origin)
    if uniq.size == 0:
        return np.zeros(0, dtype=np.int64), []
    group = np.concatenate([[0], np.cumsum(np.diff(uniq) > PERIOD_ORIGIN_GAP_MS)]).astype(np.int64)
    members: list[list[int]] = [[] for _ in range(int(group[-1]) + 1)]
    for o, g in zip(uniq.tolist(), group.tolist(), strict=True):
        members[g].append(int(o))
    return group[np.searchsorted(uniq, origin)], members


def dedupe_and_split(df: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Drop exact duplicates, split periods on the recording origin, drop residual slot clashes.

    Args:
        df: :func:`calibration.loaders.ngsim.load_ngsim_trajectories` output with
            ``global_time_ms``.

    Returns:
        ``({label: period frame}, counts)``: frames in recording order, each with an added
        ``origin_ms`` column; counts ``n_rows``, ``n_exact_duplicates`` and per period
        ``n_conflicting_slots`` and its origins.
    """
    if "global_time_ms" not in df.columns:
        raise ValueError("the I-80 rows lack global_time: cannot split recording periods")
    n_rows = len(df)
    df = df.drop_duplicates(ignore_index=True)
    origin = df["global_time_ms"].to_numpy(dtype=np.int64) - (
        df["frame"].to_numpy(dtype=np.int64) * NGSIM_DT_MS
    )
    idx, members = period_index(origin)
    periods: dict[str, pd.DataFrame] = {}
    per: dict[str, Any] = {}
    for i, origins in enumerate(members):
        label = f"p{i + 1}"
        sub = df.loc[idx == i].copy()
        sub["origin_ms"] = origin[idx == i]
        clash = sub.duplicated(["veh_id", "frame"], keep="first").to_numpy()
        sub = sub.loc[~clash].sort_values(["frame", "veh_id"], kind="stable")
        periods[label] = sub.reset_index(drop=True)
        per[label] = {"origins_ms": origins, "n_conflicting_slots": int(clash.sum())}
    counts = {
        "n_rows": n_rows,
        "n_exact_duplicates": n_rows - len(df),
        "n_rows_deduplicated": len(df),
        "periods": per,
    }
    return periods, counts


def wall_clock(ms: int) -> str:
    """Epoch milliseconds as the site's local wall clock (tenths of a second)."""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=ZoneInfo(SITE_TZ))
    return f"{dt:%Y-%m-%d %H:%M:%S}.{dt.microsecond // 100_000} {dt:%Z}"


def period_info(label: str, df: pd.DataFrame) -> dict[str, Any]:
    """Span, size and lane use of one period."""
    g0, g1 = int(df["global_time_ms"].min()), int(df["global_time_ms"].max())
    last = df.sort_values("frame").groupby("veh_id", sort=False).tail(1)
    return {
        "label": label,
        "start_ms": g0,
        "end_ms": g1,
        "span_s": round((g1 - g0) / 1000.0, 1),
        "wall_clock": [wall_clock(g0), wall_clock(g1)],
        "rows": len(df),
        "vehicles": int(df["veh_id"].nunique()),
        "rows_by_lane": {str(k): int(v) for k, v in df["lane"].value_counts().sort_index().items()},
        "vehicles_ending_by_lane": {
            str(k): int(v) for k, v in last["lane"].value_counts().sort_index().items()
        },
        "truck_share": (
            round(float((df.groupby("veh_id")["v_class"].first() == V_CLASS_TRUCK).mean()), 4)
            if "v_class" in df.columns
            else None
        ),
    }


def contiguous_blocks(
    infos: Sequence[Mapping[str, Any]], max_gap_s: float = BLOCK_MAX_GAP_S
) -> list[list[str]]:
    """Periods grouped into wall-clock blocks: a period joins the block when it starts at most
    ``max_gap_s`` after the block's end (overlaps included)."""
    ordered = sorted(infos, key=lambda r: int(r["start_ms"]))
    blocks: list[list[str]] = []
    end = -np.inf
    for r in ordered:
        if blocks and int(r["start_ms"]) <= end + max_gap_s * 1000.0:
            blocks[-1].append(str(r["label"]))
            end = max(end, float(r["end_ms"]))
        else:
            blocks.append([str(r["label"])])
            end = float(r["end_ms"])
    return blocks


def longest_block(infos: Sequence[Mapping[str, Any]]) -> list[str]:
    """The block spanning the most wall-clock time (ties: the earliest)."""
    by = {str(r["label"]): r for r in infos}
    best: list[str] = []
    best_span = -1.0
    for block in contiguous_blocks(infos):
        span = max(float(by[p]["end_ms"]) for p in block) - min(
            float(by[p]["start_ms"]) for p in block
        )
        if span > best_span:
            best, best_span = block, span
    return best


def load_raw(files: Sequence[Path]) -> pd.DataFrame:
    """Every chunk parsed by the committed loader (SI units)."""
    return pd.concat([load_ngsim_trajectories(f) for f in files], ignore_index=True)


def write_periods(periods: Mapping[str, pd.DataFrame], directory: Path | None = None) -> None:
    directory = PERIODS_DIR if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    for label, df in periods.items():
        df.to_parquet(directory / f"{label}.parquet", index=False)


def load_periods(
    labels: Sequence[str] | None = None, directory: Path | None = None
) -> dict[str, pd.DataFrame]:
    """The prepared period tables (``prepare``), read through an open handle."""
    directory = PERIODS_DIR if directory is None else directory
    files = sorted(directory.glob("p*.parquet"))
    if not files:
        raise FileNotFoundError(f"no prepared periods under {directory}: run 'prepare' first")
    out: dict[str, pd.DataFrame] = {}
    for f in files:
        if labels is not None and f.stem not in labels:
            continue
        with f.open("rb") as fh:
            out[f.stem] = pd.read_parquet(fh)
    if labels is not None and set(labels) - set(out):
        raise FileNotFoundError(f"periods {sorted(set(labels) - set(out))} not prepared")
    return dict(sorted(out.items(), key=lambda kv: int(kv[0][1:])))


# --- a block on one wall clock ---------------------------------------------------------------


def first_samples(df: pd.DataFrame) -> pd.DataFrame:
    """Each vehicle's first sample (by frame)."""
    return df.sort_values(["frame", "veh_id"], kind="stable").groupby("veh_id", sort=False).head(1)


def stream_entries(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """A period's entries by stream: vehicles first seen after the period's first frame.

    A vehicle present in the first frame is the initial state, not an entry. ``mainline``: first
    seen on a mainline lane; ``ramp``: first seen on :data:`RAMP_LANE`. Also returned:
    ``mainline_spatial``, US-101's rule (first ``local_y`` at most 30 m on a mainline lane,
    ``scripts/extract_demand_us101.py``), which the builder reports beside.
    """
    first = first_samples(df)
    f0 = int(df["frame"].min())
    new = first[first["frame"].to_numpy() > f0]
    main = new["lane"].isin(MAINLINE_LANES).to_numpy()
    spatial = first[first["lane"].isin(MAINLINE_LANES).to_numpy() & (first["x"].to_numpy() <= 30.0)]
    return {
        "mainline": new.loc[main],
        "ramp": new.loc[new["lane"].to_numpy() == RAMP_LANE],
        "mainline_spatial": spatial,
    }


def switch_times_ms(
    periods: Mapping[str, pd.DataFrame], block: Sequence[str], stream: str = "mainline"
) -> list[float]:
    """Per period of the block, the instant (epoch ms) after which its ``stream`` entries are
    censored: its last recorded entry; ``inf`` for the block's last period (its own last entry
    bounds only the analysis windows, :func:`analysis_windows`)."""
    out: list[float] = []
    for i, label in enumerate(block):
        if i == len(block) - 1:
            out.append(float("inf"))
            continue
        ent = stream_entries(periods[label])[stream]
        out.append(float(ent["global_time_ms"].max()) if len(ent) else float("-inf"))
    return out


def stitched_mask(t_ms: np.ndarray, period_pos: int, switches_ms: Sequence[float]) -> np.ndarray:
    """Rows of the block's period ``period_pos`` that the stitch uses: after the previous period's
    switch (exclusive) and up to its own (inclusive)."""
    lo = switches_ms[period_pos - 1] if period_pos > 0 else float("-inf")
    hi = switches_ms[period_pos]
    t = np.asarray(t_ms, dtype=np.float64)
    return np.asarray((t > lo) & (t <= hi), dtype=bool)


def block_t0_ms(periods: Mapping[str, pd.DataFrame], block: Sequence[str]) -> int:
    """The block's wall-clock origin: its first period's first sample."""
    return min(int(periods[p]["global_time_ms"].min()) for p in block)


def block_span_s(periods: Mapping[str, pd.DataFrame], block: Sequence[str]) -> float:
    """Wall-clock span of the block [s]."""
    end = max(int(periods[p]["global_time_ms"].max()) for p in block)
    return (end - block_t0_ms(periods, block)) / 1000.0


def populated_start_ms(df: pd.DataFrame) -> int:
    """The first frame holding at least :data:`POPULATED_SHARE` of the period's median vehicles
    per frame (epoch ms)."""
    per = df.groupby("global_time_ms").size().sort_index()
    need = POPULATED_SHARE * float(per.median())
    ok = per.index[per.to_numpy() >= need]
    return int(ok[0]) if len(ok) else int(per.index[0])


def analysis_windows(
    periods: Mapping[str, pd.DataFrame], block: Sequence[str]
) -> list[dict[str, Any]]:
    """Per period, the block wall-clock window [s] in which its lane changes are read.

    ``[lo, hi)`` with ``hi`` the period's last recorded upstream mainline entry (after it the
    period lacks the vehicles that entered later) and ``lo`` the later of the previous period's
    ``hi`` and the period's populated start (:func:`populated_start_ms`). The simulated side reads
    the same windows (sim time = wall + warm-up).
    """
    t0 = block_t0_ms(periods, block)
    out: list[dict[str, Any]] = []
    prev_hi = float("-inf")
    for label in block:
        df = periods[label]
        ent = stream_entries(df)["mainline"]
        hi_ms = (
            float(ent["global_time_ms"].max()) if len(ent) else float(df["global_time_ms"].min())
        )
        lo_ms = max(prev_hi, float(populated_start_ms(df)))
        out.append(
            {
                "period": label,
                "lo_s": round((lo_ms - t0) / 1000.0, 1),
                "hi_s": round((hi_ms - t0) / 1000.0 + NGSIM_DT_S, 1),
                "origin_offset_s": round((int(df["origin_ms"].min()) - t0) / 1000.0, 3),
                "populated_start_s": round((populated_start_ms(df) - t0) / 1000.0, 1),
            }
        )
        prev_hi = max(prev_hi, hi_ms + NGSIM_DT_MS)
    return out


# --- geometry -------------------------------------------------------------------------------


def merge_zone_bounds(frames: Sequence[pd.DataFrame]) -> tuple[float, float, dict[str, int]]:
    """The acceleration lane ``[gore, end)`` read off the data [m on ``local_y``].

    The gore: the :data:`ZONE_LO_QUANTILE` quantile of the positions at which vehicles appear in
    lane 6 in the sample straight after one in lane 7 (raw lanes, consecutive frames of one
    vehicle); the end: the :data:`ZONE_HI_QUANTILE` quantile of the lane-7 samples' positions.
    """
    xs_cross: list[np.ndarray] = []
    xs_ramp: list[np.ndarray] = []
    for df in frames:
        d = df.sort_values(["veh_id", "frame"], kind="stable")
        veh = d["veh_id"].to_numpy()
        frame = d["frame"].to_numpy(dtype=np.int64)
        lane = d["lane"].to_numpy(dtype=np.int64)
        x = d["x"].to_numpy(dtype=np.float64)
        same = (veh[1:] == veh[:-1]) & (frame[1:] - frame[:-1] == 1)
        cross = same & (lane[:-1] == RAMP_LANE) & (lane[1:] == MAINLINE_LANES[-1])
        xs_cross.append(x[1:][cross])
        xs_ramp.append(x[lane == RAMP_LANE])
    xc = np.concatenate(xs_cross) if xs_cross else np.zeros(0)
    xr = np.concatenate(xs_ramp) if xs_ramp else np.zeros(0)
    if xc.size == 0 or xr.size == 0:
        raise ValueError("no lane-7 samples or no 7 -> 6 transitions: cannot place the merge zone")
    lo = float(np.quantile(xc, ZONE_LO_QUANTILE))
    hi = float(np.quantile(xr, ZONE_HI_QUANTILE))
    if not hi > lo:
        raise ValueError(f"merge zone is empty: gore {lo:.1f} m, lane end {hi:.1f} m")
    return lo, hi, {"n_transitions_7_to_6": int(xc.size), "n_lane7_samples": int(xr.size)}


def site_length_m(frames: Sequence[pd.DataFrame]) -> float:
    """The site's measured length [m]: :data:`SITE_LENGTH_QUANTILE` of the mainline positions,
    to the metre below."""
    x = np.concatenate(
        [df.loc[df["lane"].isin(MAINLINE_LANES), "x"].to_numpy(dtype=np.float64) for df in frames]
    )
    return float(np.floor(np.quantile(x, SITE_LENGTH_QUANTILE)))


def check_lanes(frames: Sequence[pd.DataFrame]) -> dict[str, Any]:
    """The layout the replica assumes: lanes 1-6 and 7 present, no exit lane in the span.

    Raises:
        ValueError: A mainline lane or the ramp lane has no samples, or more than
            :data:`EXTRA_LANE_MAX_SHARE` of the vehicles end on a lane above 7.
    """
    lanes = np.unique(np.concatenate([df["lane"].to_numpy(dtype=np.int64) for df in frames]))
    missing = sorted((set(MAINLINE_LANES) | {RAMP_LANE}) - {int(v) for v in lanes})
    if missing:
        raise ValueError(f"lanes {missing} have no samples: not the documented I-80 layout")
    last = pd.concat([df.sort_values("frame").groupby("veh_id").tail(1) for df in frames])
    extra = last["lane"].to_numpy(dtype=np.int64) > RAMP_LANE
    share = float(extra.mean()) if len(last) else 0.0
    if share > EXTRA_LANE_MAX_SHARE:
        raise ValueError(
            f"{share:.2%} of vehicles end on a lane above {RAMP_LANE} (an exit lane): the replica "
            "has one on-ramp and no off-ramp (FHWA-HRT-06-137); refusing to build it"
        )
    return {
        "lanes_present": [int(v) for v in lanes],
        "share_ending_above_ramp_lane": round(share, 5),
    }


# --- provenance -------------------------------------------------------------------------------


SOURCE_COMMIT_ENV: Final[str] = "FLOWSTATE_SOURCE_COMMIT"
SOURCE_COMMIT_FILE: Final[str] = ".source_commit"


def git_head() -> str:
    """The code's source commit: ``$FLOWSTATE_SOURCE_COMMIT``, else the commit the VM setup wrote
    to ``.source_commit`` (the VM's own HEAD is a snapshot commit, scripts/gcp/vm_setup.sh), else
    this checkout's HEAD."""
    env = os.environ.get(SOURCE_COMMIT_ENV, "").strip()
    if env:
        return env
    f = REPO_ROOT / SOURCE_COMMIT_FILE
    if f.is_file():
        first = next(iter(f.read_text().split()), "")
        if first:
            return first
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty() -> bool | None:
    """Uncommitted changes under the code paths only (a VM rewrites artifacts stage by stage)."""
    try:
        out = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=no",
                "--",
                "packages",
                "scripts",
                "scenarios",
                "pyproject.toml",
                "uv.lock",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def prepare(out: Path) -> dict[str, Any]:
    """Load the chunks, de-duplicate, split, write the period tables and the summary artifact."""
    t_start = time.time()
    files = chunk_files()
    raw = load_raw(files)
    periods, counts = dedupe_and_split(raw)
    del raw
    write_periods(periods)
    infos = [period_info(label, df) for label, df in periods.items()]
    for info in infos:
        info.update(counts["periods"][info["label"]])
    block = longest_block(infos)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else None
    summary = {
        "schema_version": 1,
        "kind": "i80_data",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i80_data.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "data_hash": data_hash(files),
        "data_version": "raw NGSIM I-80 (data.transportation.gov Socrata 8ect-6jqj, "
        "location 'i-80'), not the Montanino-Punzo reconstruction",
        "citation": CITATION,
        "fetch": (
            {k: manifest[k] for k in ("dataset", "location", "order", "chunk_rows", "fetched_at")}
            | {"n_rows": manifest["n_rows"], "n_chunks": len(manifest["chunks"])}
            if manifest
            else None
        ),
        "counts": {k: v for k, v in counts.items() if k != "periods"},
        "periods": infos,
        "blocks": contiguous_blocks(infos),
        "replica_block": block,
        "rules": {
            "duplicates": "exact duplicate rows dropped after the loader's parse (US-101's rule); "
            "a residual second row of one vehicle in one frame of a period dropped, first kept",
            "periods": f"recording origin global_time - frame_id x {NGSIM_DT_MS} ms; origins "
            f"within {PERIOD_ORIGIN_GAP_MS} ms are one period",
            "blocks": f"periods separated by at most {BLOCK_MAX_GAP_S:g} s (or overlapping) are "
            "one block; the replica models the longest",
        },
        "wall_s": round(time.time() - t_start, 1),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, allow_nan=False) + "\n")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="download the I-80 subset (VM only)")
    p = sub.add_parser("prepare", help="de-duplicate, split periods, write tables and summary")
    p.add_argument("--out", default=str(SUMMARY_OUT))
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        m = fetch()
        print(f"fetched {m['n_rows']:,} rows in {len(m['chunks'])} chunks", flush=True)
    else:
        s = prepare(Path(args.out))
        print(
            f"{s['counts']['n_rows']:,} rows, {s['counts']['n_exact_duplicates']:,} duplicates; "
            f"periods {[p['label'] for p in s['periods']]}; replica block {s['replica_block']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
