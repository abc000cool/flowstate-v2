"""I-24 MOTION trajectory loader (CLAUDE.md §6.2).

Parses the I-24 MOTION v1.x trajectory data product (Gloudemans et al. 2023,
*I-24 MOTION: An instrument for freeway traffic science*, Transp. Res. C
155:104311; data documentation v1.x at
https://github.com/I24-MOTION/I24M_documentation; i24motion.org). A run is
distributed as one MongoDB export: a JSON **array** of per-vehicle *trajectory
fragment* documents, zipped (the 30 Nov 2022 INCEPTION morning run is a
19.5 GB array of 816,694 documents in a 5.8 GB zip). The loader therefore
streams — :func:`iter_i24_documents` decompresses from the zip and decodes one
top-level document at a time with ``json.JSONDecoder.raw_decode`` on a sliding
text buffer, so the array is never materialized and the JSON is never
extracted to disk — and :func:`convert_i24_to_parquet` writes filtered,
decimated Parquet in row groups plus a per-vehicle table and a provenance
``meta.json``.

Schema (data documentation v1.x, "Data schema"; all verified against the
INCEPTION file):

* ``_id`` — BSON ObjectId, exported as ``{"$oid": "..."}`` (a bare string is
  also accepted).
* ``timestamp`` — ``[double]`` Unix seconds, nominally 25 Hz (0.04 s grid,
  shared across documents up to float noise).
* ``x_position`` — ``[double]`` **feet**, *back-center* longitudinal position
  along the roadway spline; MM 60 is exactly ``60 × 5280 = 316 800 ft``,
  other mile markers approximately ``MM × 5280``. Increases eastbound.
* ``y_position`` — ``[double]`` feet, lateral; **positive on the westbound
  side, negative eastbound** (right-hand rule; y = 0 near the median).
* ``length`` / ``width`` / ``height`` — feet.
* ``direction`` — ``+1`` eastbound, ``-1`` westbound.
* ``coarse_vehicle_class`` — 0 sedan, 1 midsize, 2 van, 3 pickup, 4 semi,
  5 truck (:data:`I24_COARSE_CLASS`).
* ``first_timestamp`` / ``last_timestamp`` / ``starting_x`` / ``ending_x``,
  ``merged_ids`` / ``fragment_ids`` (fragments combined by post-processing),
  and internal-use fields (``flags``, ``x_score``, ``y_score``,
  ``compute_node_id``, ...).

Conventions applied by this loader:

* **Travel-oriented x.** ``x`` increases in the direction of travel and is
  measured from the carriageway's upstream reference mile marker:
  westbound ``x = (x_ref_ft − x_position) × FEET_TO_M``, eastbound
  ``x = (x_position − x_ref_ft) × FEET_TO_M``; a leader always has larger
  ``x``. :func:`convert_i24_to_parquet` uses the testbed limits
  :data:`I24_MM_RANGE` (upstream end MM 62.7 for westbound, MM 58.7
  eastbound) as the reference.
* **Front bumper.** ``x_position`` is the *back*-center position, so ``x`` is
  shifted forward by the vehicle length: the loader's ``x`` is the front
  bumper, which is what :func:`calibration.episodes.extract_episodes`
  assumes when it subtracts the leader's length to obtain the
  bumper-to-bumper gap.
* **Lanes** follow the documented lateral bands for direction −1 — lane 1
  (HOV) at 12–24 ft, lane 2 at 24–36 ft, lane 3 at 36–48 ft, lane 4 at
  48–60 ft (:data:`I24_MAINLINE_LANES`): ``lane = floor(y_side / 12 ft)``
  where ``y_side`` is the lateral coordinate measured toward the
  carriageway's own side (``y`` westbound, ``−y`` eastbound). Lane 0 is the
  median shoulder, ≥ 5 the outside shoulder / ramps, and −1 the far side of
  the median (a homography artifact). The documentation tabulates the bands
  for westbound only; eastbound is treated as the mirror image.
* **Speed** is the finite-difference gradient of ``x`` at the native 25 Hz,
  computed **before** any decimation and clipped at zero (the released
  positions are already smoothed by the I-24 pipeline so that speed can be
  differentiated directly — Ji et al. 2024, arXiv:2311.10888 §II). Time
  stamps are snapped to the shared 0.04 s grid so rows from different
  vehicles align exactly for leader pairing; decimation keeps every
  ``downsample``-th grid slot, so decimated rows stay aligned too.
* **Fragmentation is preserved, not repaired.** Documents are fragments
  (occlusion under overpasses and by tall vehicles, tracker breaks); the
  data team notes the v1 trajectories are "not currently suitable for some
  types of analyses such as long-term vehicle" following (arXiv:2311.10888).
  Nothing here stitches fragments; episode cutting at recording gaps, lane
  and leader changes is :mod:`calibration.episodes`' job, and the
  conversion summary reports the fragment-duration distribution so that
  limitation is visible in every downstream artifact.

Data use: I-24 MOTION data may be used in academic and commercial work; any
published use must cite Gloudemans et al. (2023) — see
:data:`I24_CITATION`. Registration at i24motion.org is required to obtain
the files.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from io import TextIOWrapper
from pathlib import Path
from typing import IO, Any, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from calibration.episodes import (
    MIN_EPISODE_DURATION_S,
    LeaderFollowerEpisode,
    extract_episodes,
)
from calibration.loaders.ngsim import FEET_TO_M

FT_PER_MILE: Final[float] = 5280.0
"""Feet per mile — the documented ``x_position`` → mile-marker divisor."""

I24_SAMPLE_DT_S: Final[float] = 0.04
"""Native sampling interval [s] (25 Hz) of the I-24 MOTION trajectories."""

I24_LANE_WIDTH_FT: Final[float] = 12.0
"""Lateral band width [ft] of the documented lane delineation."""

LANE_WIDTH_FT_DEFAULT: Final[float] = I24_LANE_WIDTH_FT
"""Backward-compatible alias of :data:`I24_LANE_WIDTH_FT`."""

I24_MAINLINE_LANES: Final[tuple[int, int]] = (1, 4)
"""Inclusive mainline lane range (1 = HOV/leftmost … 4 = rightmost)."""

I24_MM_RANGE: Final[tuple[float, float]] = (58.7, 62.7)
"""Testbed mile-marker limits used as the travel-x reference (the 4-mile
span the I-24 MOTION VT-tools use: ``min_milemarker = 58.7``, 4 mi)."""

I24_COARSE_CLASS: Final[dict[int, str]] = {
    0: "sedan",
    1: "midsize",
    2: "van",
    3: "pickup",
    4: "semi",
    5: "truck",
}
"""``coarse_vehicle_class`` code → label (data documentation v1.x)."""

I24_PASSENGER_CLASSES: Final[frozenset[int]] = frozenset({0, 1, 2, 3})
"""Classes treated as passenger vehicles for car-following calibration
(sedan, midsize, van, pickup); 4 semi and 5 truck are heavy vehicles."""

I24_CITATION: Final[str] = (
    "Gloudemans, D., Wang, Y., Ji, J., Zachar, G., Barbour, W., Hall, E., Cebelak, M., "
    "Smith, L. and Work, D.B. (2023). I-24 MOTION: An instrument for freeway traffic "
    "science. Transportation Research Part C 155:104311."
)
"""Required citation for any published use of I-24 MOTION data."""

DURATION_HIST_EDGES_S: Final[tuple[float, ...]] = (0.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0)
"""Fragment-duration histogram edges [s] reported by the conversion (the last
bin is open-ended)."""

_TRAJ_SCHEMA: Final[pa.Schema] = pa.schema(
    [
        pa.field("t", pa.float64()),
        pa.field("veh_id", pa.string()),
        pa.field("x", pa.float64()),
        pa.field("y", pa.float64()),
        pa.field("lane", pa.int8()),
        pa.field("v", pa.float64()),
        pa.field("length", pa.float64()),
        pa.field("cls", pa.int8()),
    ]
)


# --------------------------------------------------------------------------
# Streaming document reader
# --------------------------------------------------------------------------


@contextmanager
def _open_json_text(path: str | Path) -> Iterator[IO[str]]:
    """Open ``path`` as a text stream: a ``.zip`` yields its JSON member."""
    p = Path(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            json_members = [m for m in members if m.filename.lower().endswith(".json")]
            if not json_members:
                raise ValueError(f"{p}: zip holds no .json member")
            with zf.open(json_members[0]) as raw:
                yield TextIOWrapper(raw, encoding="utf-8")
    else:
        with open(p, encoding="utf-8") as f:
            yield f


def iter_i24_documents(path: str | Path, *, chunk_chars: int = 1 << 24) -> Iterator[dict[str, Any]]:
    """Stream top-level JSON documents from an I-24 MOTION export.

    Accepts a ``.zip`` holding the JSON array (decompressed on the fly), a
    plain ``.json`` array, a single document, or line-delimited documents.
    Documents are decoded one at a time with ``JSONDecoder.raw_decode`` on a
    sliding text buffer of ``chunk_chars`` characters, so memory stays at a
    few chunks regardless of file size (the INCEPTION array is 19.5 GB).

    Args:
        path: ``.zip`` or ``.json`` path.
        chunk_chars: Read granularity in characters; a document larger than
            one chunk is handled by growing the buffer until it decodes.

    Yields:
        Each document as a ``dict``, in file order.

    Raises:
        ValueError: On a non-object element, a truncated file, or a zip
            without a JSON member.
    """
    if chunk_chars < 1:
        raise ValueError(f"chunk_chars must be >= 1, got {chunk_chars}")
    decoder = json.JSONDecoder()
    separators = " \t\r\n[,"
    with _open_json_text(path) as f:
        buf = f.read(chunk_chars)
        eof = not buf
        pos = 0
        while True:
            while pos < len(buf) and buf[pos] in separators:
                pos += 1
            if pos >= len(buf):
                if eof:
                    return
                buf = f.read(chunk_chars)
                eof = not buf
                pos = 0
                continue
            if buf[pos] == "]":
                return
            try:
                obj, end = decoder.raw_decode(buf, pos)
            except json.JSONDecodeError as exc:
                if eof:
                    raise ValueError(f"{path}: truncated or invalid JSON near char {pos}") from exc
                more = f.read(chunk_chars)
                if not more:
                    eof = True
                buf = buf[pos:] + more
                pos = 0
                continue
            pos = end
            if not isinstance(obj, dict):
                raise ValueError(f"{path}: expected JSON objects, got {type(obj).__name__}")
            yield obj


def i24_document_id(doc: dict[str, Any]) -> str:
    """Document id as a string (``{"$oid": ...}`` MongoDB export or bare)."""
    raw = doc.get("_id")
    if isinstance(raw, dict):
        raw = raw.get("$oid", raw)
    return str(raw)


def sha256_file(path: str | Path, *, chunk_bytes: int = 1 << 24) -> str:
    """sha256 hex digest of a file, streamed (provenance ``data_hash``)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_bytes)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Document → tidy frame
# --------------------------------------------------------------------------


def i24_document_to_frame(
    doc: dict[str, Any],
    *,
    direction: int,
    t_origin_unix: float,
    x_ref_ft: float,
    lane_width_ft: float = I24_LANE_WIDTH_FT,
    downsample: int = 1,
    grid_dt_s: float = I24_SAMPLE_DT_S,
) -> pd.DataFrame | None:
    """Convert one fragment document into tidy SI trajectory rows.

    See the module docstring for the coordinate conventions. Time stamps are
    snapped to the ``grid_dt_s`` grid anchored at ``t_origin_unix``; rows
    landing on the same grid slot are collapsed to the first; speed is the
    gradient of the back-bumper position on the full-resolution series;
    then every ``downsample``-th grid slot (``k % downsample == 0``) is kept.

    Args:
        doc: One I-24 MOTION document.
        direction: Carriageway to accept (+1 / −1); a document of the other
            direction returns ``None``.
        t_origin_unix: Unix time [s] mapped to ``t = 0``.
        x_ref_ft: Roadway coordinate [ft] mapped to ``x = 0`` (the upstream
            end of the carriageway's study span).
        lane_width_ft: Lateral band width [ft] for lane indices.
        downsample: Keep every k-th grid slot (1 = native 25 Hz).
        grid_dt_s: Native sampling grid [s].

    Returns:
        Frame with ``t`` [s], ``veh_id``, ``x`` [m, front bumper, travel
        oriented], ``y`` [m, lateral as recorded], ``lane`` (int8), ``v``
        [m/s], ``length`` [m], ``cls`` (int8) — or ``None`` when the
        document is skipped (wrong direction, < 2 usable samples).

    Raises:
        ValueError: Ragged position/timestamp arrays.
    """
    if int(doc.get("direction", 0)) != direction:
        return None
    if downsample < 1:
        raise ValueError(f"downsample must be >= 1, got {downsample}")
    ts = np.asarray(doc["timestamp"], dtype=np.float64)
    x_ft = np.asarray(doc["x_position"], dtype=np.float64)
    y_ft = np.asarray(doc["y_position"], dtype=np.float64)
    if not (ts.shape == x_ft.shape == y_ft.shape):
        raise ValueError(f"ragged arrays in document {i24_document_id(doc)}")
    finite = np.isfinite(ts) & np.isfinite(x_ft) & np.isfinite(y_ft)
    ts, x_ft, y_ft = ts[finite], x_ft[finite], y_ft[finite]
    if ts.shape[0] < 2:
        return None

    k = np.rint((ts - t_origin_unix) / grid_dt_s).astype(np.int64)
    order = np.argsort(k, kind="stable")
    k, x_ft, y_ft = k[order], x_ft[order], y_ft[order]
    keep = np.ones(k.shape[0], dtype=bool)
    keep[1:] = k[1:] != k[:-1]
    k, x_ft, y_ft = k[keep], x_ft[keep], y_ft[keep]
    if k.shape[0] < 2:
        return None

    t = k.astype(np.float64) * grid_dt_s
    sign = -1.0 if direction < 0 else 1.0
    x_back = sign * (x_ft - x_ref_ft) * FEET_TO_M
    v = np.maximum(np.gradient(x_back, t), 0.0)
    y_side = y_ft if direction < 0 else -y_ft
    lane = np.clip(np.floor(y_side / lane_width_ft), -1, 9).astype(np.int8)
    length_m = float(doc.get("length", 0.0) or 0.0) * FEET_TO_M

    if downsample > 1:
        sel = (k % downsample) == 0
        t, x_back, y_ft, v, lane = t[sel], x_back[sel], y_ft[sel], v[sel], lane[sel]
        if t.shape[0] == 0:
            return None

    return pd.DataFrame(
        {
            "t": t,
            "veh_id": i24_document_id(doc),
            "x": x_back + length_m,
            "y": y_ft * FEET_TO_M,
            "lane": lane,
            "v": v,
            "length": length_m,
            "cls": np.int8(doc.get("coarse_vehicle_class", -1)),
        }
    )


def _vehicle_record(
    doc: dict[str, Any], *, direction: int, t_origin_unix: float, x_ref_ft: float
) -> dict[str, Any]:
    """One row of the per-vehicle (per-fragment) table."""
    ts = np.asarray(doc["timestamp"], dtype=np.float64)
    sign = -1.0 if direction < 0 else 1.0
    x0 = float(doc.get("starting_x", doc["x_position"][0]))
    x1 = float(doc.get("ending_x", doc["x_position"][-1]))
    first = float(doc.get("first_timestamp", ts[0]))
    last = float(doc.get("last_timestamp", ts[-1]))
    flags = doc.get("flags") or []
    return {
        "veh_id": i24_document_id(doc),
        "direction": int(direction),
        "cls": int(doc.get("coarse_vehicle_class", -1)),
        "length": float(doc.get("length", 0.0) or 0.0) * FEET_TO_M,
        "width": float(doc.get("width", 0.0) or 0.0) * FEET_TO_M,
        "height": float(doc.get("height", 0.0) or 0.0) * FEET_TO_M,
        "first_t": first - t_origin_unix,
        "last_t": last - t_origin_unix,
        "duration_s": last - first,
        "x_start": sign * (x0 - x_ref_ft) * FEET_TO_M,
        "x_end": sign * (x1 - x_ref_ft) * FEET_TO_M,
        "n_samples": int(ts.shape[0]),
        "n_merged": int(sum(len(m) for m in (doc.get("merged_ids") or []))),
        "n_fragments": len(doc.get("fragment_ids") or []),
        "flags": ",".join(str(f) for f in flags),
        "compute_node": str(doc.get("compute_node_id", "")),
    }


# --------------------------------------------------------------------------
# Streaming conversion to Parquet
# --------------------------------------------------------------------------


@dataclass
class I24ConversionSummary:
    """Provenance and statistics of one :func:`convert_i24_to_parquet` run.

    Every count is over the documents actually read; ``duration_hist``
    counts kept documents per :data:`DURATION_HIST_EDGES_S` bin.
    """

    source: str
    data_hash: str
    direction: int
    t_origin_unix: float
    x_ref_ft: float
    mm_range: tuple[float, float]
    downsample: int
    grid_dt_s: float
    lane_width_ft: float
    t_range_s: tuple[float, float] | None
    n_docs_read: int = 0
    n_docs_direction: int = 0
    n_docs_kept: int = 0
    n_rows: int = 0
    n_samples_native: int = 0
    t_min_s: float = math.inf
    t_max_s: float = -math.inf
    x_min_m: float = math.inf
    x_max_m: float = -math.inf
    class_counts: dict[str, int] = field(default_factory=dict)
    duration_hist_edges_s: tuple[float, ...] = DURATION_HIST_EDGES_S
    duration_hist: list[int] = field(default_factory=list)
    n_docs_ge_30s: int = 0
    citation: str = I24_CITATION
    notes: list[str] = field(default_factory=list)


def convert_i24_to_parquet(
    path: str | Path,
    out_dir: str | Path,
    *,
    direction: int = -1,
    t_origin_unix: float | None = None,
    t_range_s: tuple[float, float] | None = None,
    mm_range: tuple[float, float] = I24_MM_RANGE,
    downsample: int = 5,
    lane_width_ft: float = I24_LANE_WIDTH_FT,
    row_group_rows: int = 2_000_000,
    data_hash: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> I24ConversionSummary:
    """Stream an I-24 MOTION export into filtered, decimated Parquet.

    Writes under ``out_dir``:

    * ``trajectories.parquet`` — rows per :func:`i24_document_to_frame`
      (``t, veh_id, x, y, lane, v, length, cls``), row groups of
      ``row_group_rows`` rows in file (≈ time) order so time-range reads
      can skip row groups by statistics;
    * ``vehicles.parquet`` — one row per kept fragment (id, class,
      dimensions, first/last time, start/end x, sample count, merge
      counts, flags, compute node);
    * ``meta.json`` — the :class:`I24ConversionSummary`.

    Args:
        path: The ``.zip`` (or ``.json``) export.
        out_dir: Output directory (created).
        direction: Carriageway to keep (−1 westbound is the instrumented
            congestion direction).
        t_origin_unix: Unix time mapped to ``t = 0``; ``None`` uses the
            first document's ``first_timestamp`` floored to the hour.
        t_range_s: Optional ``(t_lo, t_hi)`` relative to the origin; a
            document is kept when its span overlaps the range.
        mm_range: Study span in mile markers; the upstream end (max for
            westbound, min for eastbound) is the ``x = 0`` reference.
        downsample: Keep every k-th 25 Hz grid slot (5 → 5 Hz).
        lane_width_ft: Lateral band width for lane indices.
        row_group_rows: Parquet row-group size.
        data_hash: Precomputed sha256 of ``path``; computed when ``None``.
        progress: Optional callback ``(n_docs_read, n_docs_kept)`` invoked
            every 10,000 documents.

    Returns:
        The :class:`I24ConversionSummary` (also written as ``meta.json``).

    Raises:
        ValueError: Bad direction, empty export, or invalid ranges.
    """
    if direction not in (-1, 1):
        raise ValueError(f"direction must be +1 or -1, got {direction}")
    if mm_range[0] >= mm_range[1]:
        raise ValueError(f"mm_range must be increasing, got {mm_range}")
    if t_range_s is not None and t_range_s[0] >= t_range_s[1]:
        raise ValueError(f"t_range_s must be increasing, got {t_range_s}")
    if row_group_rows < 1:
        raise ValueError(f"row_group_rows must be >= 1, got {row_group_rows}")
    src = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x_ref_ft = (mm_range[1] if direction < 0 else mm_range[0]) * FT_PER_MILE
    digest = data_hash if data_hash is not None else sha256_file(src)

    summary = I24ConversionSummary(
        source=src.name,
        data_hash=digest,
        direction=direction,
        t_origin_unix=t_origin_unix if t_origin_unix is not None else math.nan,
        x_ref_ft=x_ref_ft,
        mm_range=(float(mm_range[0]), float(mm_range[1])),
        downsample=downsample,
        grid_dt_s=I24_SAMPLE_DT_S,
        lane_width_ft=lane_width_ft,
        t_range_s=t_range_s,
        duration_hist=[0] * len(DURATION_HIST_EDGES_S),
    )
    edges = np.asarray(DURATION_HIST_EDGES_S, dtype=np.float64)
    class_counts: dict[int, int] = {}
    vehicles: list[dict[str, Any]] = []
    buffer: list[pd.DataFrame] = []
    buffered = 0
    origin: float | None = t_origin_unix
    traj_path = out / "trajectories.parquet"

    def flush(writer: pq.ParquetWriter) -> None:
        nonlocal buffer, buffered
        if not buffer:
            return
        table = pa.Table.from_pandas(
            pd.concat(buffer, ignore_index=True), schema=_TRAJ_SCHEMA, preserve_index=False
        )
        writer.write_table(table)
        buffer = []
        buffered = 0

    with open(traj_path, "wb") as sink:
        writer = pq.ParquetWriter(sink, _TRAJ_SCHEMA, compression="zstd")
        try:
            for doc in iter_i24_documents(src):
                summary.n_docs_read += 1
                if progress is not None and summary.n_docs_read % 10_000 == 0:
                    progress(summary.n_docs_read, summary.n_docs_kept)
                if int(doc.get("direction", 0)) != direction:
                    continue
                summary.n_docs_direction += 1
                ts = doc.get("timestamp") or []
                if len(ts) < 2:
                    continue
                first = float(doc.get("first_timestamp", ts[0]))
                last = float(doc.get("last_timestamp", ts[-1]))
                if origin is None:
                    origin = math.floor(first / 3600.0) * 3600.0
                    summary.t_origin_unix = origin
                if t_range_s is not None and (
                    last - origin < t_range_s[0] or first - origin >= t_range_s[1]
                ):
                    continue
                frame = i24_document_to_frame(
                    doc,
                    direction=direction,
                    t_origin_unix=origin,
                    x_ref_ft=x_ref_ft,
                    lane_width_ft=lane_width_ft,
                    downsample=downsample,
                )
                if frame is None:
                    continue
                summary.n_docs_kept += 1
                summary.n_samples_native += len(ts)
                cls = int(doc.get("coarse_vehicle_class", -1))
                class_counts[cls] = class_counts.get(cls, 0) + 1
                dur = last - first
                summary.duration_hist[int(np.searchsorted(edges, dur, side="right")) - 1] += 1
                if dur >= 30.0:
                    summary.n_docs_ge_30s += 1
                vehicles.append(
                    _vehicle_record(
                        doc, direction=direction, t_origin_unix=origin, x_ref_ft=x_ref_ft
                    )
                )
                summary.t_min_s = min(summary.t_min_s, float(frame["t"].iloc[0]))
                summary.t_max_s = max(summary.t_max_s, float(frame["t"].iloc[-1]))
                summary.x_min_m = min(summary.x_min_m, float(frame["x"].min()))
                summary.x_max_m = max(summary.x_max_m, float(frame["x"].max()))
                summary.n_rows += len(frame)
                buffer.append(frame)
                buffered += len(frame)
                if buffered >= row_group_rows:
                    flush(writer)
            flush(writer)
        finally:
            writer.close()

    if summary.n_docs_read == 0:
        raise ValueError(f"{src}: no documents found")
    if origin is None:
        summary.t_origin_unix = math.nan
    summary.class_counts = {
        I24_COARSE_CLASS.get(c, str(c)): n for c, n in sorted(class_counts.items())
    }
    if summary.n_docs_kept == 0:
        summary.t_min_s = summary.t_max_s = math.nan
        summary.x_min_m = summary.x_max_m = math.nan
    summary.notes = [
        "documents are trajectory FRAGMENTS (I-24 MOTION v1.x); no stitching applied",
        "x = front bumper, travel-oriented, 0 at the upstream mm_range end; "
        "x_position is back-center in the source, shifted by length",
        "lane = floor(y_side / lane_width_ft): 1-4 mainline (1 = HOV), 0 median "
        "shoulder, >= 5 outside shoulder/ramps, -1 far side of the median",
        "v = gradient of x at native 25 Hz, clipped at 0, before decimation",
        f"decimated to every {downsample}-th 0.04 s grid slot",
    ]

    vehicles_df = pd.DataFrame(
        vehicles,
        columns=[
            "veh_id",
            "direction",
            "cls",
            "length",
            "width",
            "height",
            "first_t",
            "last_t",
            "duration_s",
            "x_start",
            "x_end",
            "n_samples",
            "n_merged",
            "n_fragments",
            "flags",
            "compute_node",
        ],
    )
    with open(out / "vehicles.parquet", "wb") as sink:
        pq.write_table(pa.Table.from_pandas(vehicles_df, preserve_index=False), sink)
    (out / "meta.json").write_text(json.dumps(_json_ready(asdict(summary)), indent=2))
    return summary


def _json_ready(obj: Any) -> Any:
    """Replace non-finite floats with ``None`` so the JSON is strictly valid."""
    if isinstance(obj, dict):
        return {k: _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def load_i24_parquet(
    out_dir: str | Path,
    *,
    t_range_s: tuple[float, float] | None = None,
    x_range_m: tuple[float, float] | None = None,
    lanes: tuple[int, int] | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Read (a slice of) a :func:`convert_i24_to_parquet` trajectory table.

    Filters are pushed down to Parquet row groups / rows. Ranges are
    half-open ``[lo, hi)``; ``lanes`` is inclusive.

    Args:
        out_dir: Conversion output directory.
        t_range_s: Time slice [s] relative to the conversion origin.
        x_range_m: Position slice [m] (front bumper, travel oriented).
        lanes: Inclusive lane range, e.g. :data:`I24_MAINLINE_LANES`.
        columns: Subset of columns to read (all when ``None``).

    Returns:
        Tidy trajectory frame (contract §3 columns plus ``y``, ``length``,
        ``cls``).
    """
    filters: list[tuple[str, str, float | int]] = []
    if t_range_s is not None:
        filters += [("t", ">=", float(t_range_s[0])), ("t", "<", float(t_range_s[1]))]
    if x_range_m is not None:
        filters += [("x", ">=", float(x_range_m[0])), ("x", "<", float(x_range_m[1]))]
    if lanes is not None:
        filters += [("lane", ">=", int(lanes[0])), ("lane", "<=", int(lanes[1]))]
    with open(Path(out_dir) / "trajectories.parquet", "rb") as f:
        table = pq.read_table(f, columns=columns, filters=filters or None)
    return table.to_pandas()


def load_i24_vehicles(out_dir: str | Path) -> pd.DataFrame:
    """Read the per-fragment table written by :func:`convert_i24_to_parquet`."""
    with open(Path(out_dir) / "vehicles.parquet", "rb") as f:
        return pq.read_table(f).to_pandas()


# --------------------------------------------------------------------------
# Small-file convenience API (fixtures, spot checks)
# --------------------------------------------------------------------------


def load_i24_trajectories(
    path: str | Path,
    *,
    direction: int = -1,
    lane_width_ft: float = I24_LANE_WIDTH_FT,
    x_ref_ft: float = 0.0,
    t_origin_unix: float | None = None,
    downsample: int = 1,
) -> pd.DataFrame:
    """Load a small I-24 MOTION file into one tidy SI trajectory table.

    In-memory counterpart of :func:`convert_i24_to_parquet` for fixtures
    and spot checks — the same per-document conversion
    (:func:`i24_document_to_frame`), concatenated. Use the Parquet path for
    a full export.

    Args:
        path: ``.zip`` / ``.json`` export (array, single document, or
            line-delimited).
        direction: Carriageway to keep (+1 eastbound, −1 westbound).
        lane_width_ft: Lateral band width [ft] for lane indices.
        x_ref_ft: Roadway coordinate mapped to ``x = 0``; the default 0
            keeps ``x`` as the (front-bumper) roadway coordinate oriented
            with travel.
        t_origin_unix: Time origin; ``None`` uses the earliest time stamp
            among kept documents (the grid then starts at ``t = 0``).
        downsample: Keep every k-th 25 Hz grid slot.

    Returns:
        Frame with ``t``, ``veh_id``, ``x`` [m, front bumper], ``y`` [m],
        ``lane``, ``v`` [m/s], ``length`` [m], ``cls``.

    Raises:
        ValueError: On schema violations or no usable documents for
            ``direction``.
    """
    if lane_width_ft <= 0:
        raise ValueError(f"lane_width_ft must be > 0, got {lane_width_ft}")
    docs = [d for d in iter_i24_documents(path) if int(d.get("direction", 0)) == direction]
    if not docs:
        raise ValueError(f"{path}: no documents with direction {direction}")
    if t_origin_unix is None:
        starts = [
            float(np.min(np.asarray(d["timestamp"], dtype=float))) for d in docs if d["timestamp"]
        ]
        if not starts:
            raise ValueError(f"{path}: no document has time stamps")
        t_origin_unix = min(starts)
    frames = [
        f
        for d in docs
        if (
            f := i24_document_to_frame(
                d,
                direction=direction,
                t_origin_unix=t_origin_unix,
                x_ref_ft=x_ref_ft,
                lane_width_ft=lane_width_ft,
                downsample=downsample,
            )
        )
        is not None
    ]
    if not frames:
        raise ValueError(f"{path}: no usable documents")
    return pd.concat(frames, ignore_index=True)


def load_i24_episodes(
    path: str | Path,
    *,
    direction: int = -1,
    lane_width_ft: float = I24_LANE_WIDTH_FT,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
    downsample: int = 1,
) -> list[LeaderFollowerEpisode]:
    """Load a small I-24 MOTION file and extract leader-follower episodes.

    Leaders are derived by per-lane position ordering (the schema publishes
    none) and gaps are bumper-to-bumper — ``x`` is the front bumper and
    :func:`calibration.episodes.extract_episodes` subtracts each leader's
    ``length``. Fragment boundaries, lane changes and leader changes cut
    episodes.

    Args:
        path: ``.zip`` / ``.json`` export.
        direction: Carriageway filter (+1 / −1).
        lane_width_ft: Lane width [ft] for lane binning.
        min_duration_s: Minimum episode duration [s] (contract default 30 s).
        downsample: Keep every k-th 25 Hz grid slot before pairing.

    Returns:
        List of validated episodes with ``metadata['dataset'] == 'i24motion'``.
    """
    df = load_i24_trajectories(
        path, direction=direction, lane_width_ft=lane_width_ft, downsample=downsample
    )
    return extract_episodes(df, dataset="i24motion", min_duration_s=min_duration_s)
