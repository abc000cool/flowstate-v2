"""Lane use on the approach to the two off-ramp gores, from the recording.

The data half of docs/MERGE_ROUND6_PLAN.md §2.5. Downstream of the Old
Hickory taper the replica's lane split inverts the recording's (too little
traffic in the right lane, middle lanes too fast); §2.5 asks whether that is a
diverge-assignment defect — exiting vehicles staged in the wrong lane — or the
downstream face of the merge queue. This script measures, from the recording
only, what the approach to each off-ramp gore actually looks like:

* the lane distribution in 250 m bins over the 1 km upstream of each gore, in
  **both** conventions — share of vehicle-time (5 Hz samples) and share of
  first crossings (each vehicle counted once per section,
  :func:`~i24_build_replica.first_crossing_lane_counts`, the convention
  ``exit_fraction`` and ``entry_lane_shares`` are applied in);
* which lanes the vehicles that *leave* by each ramp are in, under an
  explicit, stated classification of fragments (see :func:`classify_exits`)
  whose reach is bounded by fragmentation (docs/I24_DATA.md §2) and reported
  bin by bin;
* the exit volume in the units the builder's ``exit_fraction`` is computed in,
  beside the committed fraction of ``artifacts/i24_replica_inputs_zip.json``;
* the downstream split at data x = 2000-2500 m quoted in §2.5, copied from
  ``artifacts/i24_lane_profile_zip.json`` so the comparison sits in one file.

Gore positions come from the ramp landmark layer through the committed
geometry fit (``artifacts/i24_replica_inputs.json``, ``geometry``), and are
checked against the recording itself: the auxiliary lane ends and the ramp
appears as lane 6 within a bin of the landmark (:func:`divergence_x`).

Nothing here is coverage-corrected: every count is a lower bound at the local
per-lane tracking coverage (docs/I24_DATA.md §4). Shares and exit *rates* are
ratios taken inside the same lane and window, so a uniform per-lane coverage
factor cancels; shares across lanes do not correct for the lane-dependent
coverage of docs/I24_DATA.md §4 and are labelled accordingly.

Outputs ``artifacts/i24_diverge_lanes.json``. Run from the repo root::

    uv run --no-sync python scripts/i24_diverge_lanes.py
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import (
    RAMPS,
    T_STUDY_HI_S,
    T_STUDY_LO_S,
    first_crossing_lane_counts,
)
from i24_data import REPO_ROOT, WB_DIR, data_hash

OUT = REPO_ROOT / "artifacts" / "i24_diverge_lanes.json"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
ZIP_INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs_zip.json"
LANE_PROFILE = REPO_ROOT / "artifacts" / "i24_lane_profile_zip.json"

BIN_M = 250.0
N_BINS = 4
"""Bins per ramp, upstream of the gore: 4 x 250 m = the 1 km of §2.5."""

SECTIONS_PER_BIN = 5
"""Count sections per bin, at 50 m spacing. One section per bin would put the
whole flow share of a bin on a single cross-section, and some sections of this
recording see almost nothing (docs/I24_DATA.md §4: ~90 veh/h at 2400 m against
3,500-4,300 at its neighbours). Counts are pooled over the five sections — a
vehicle crossing all five contributes five — and the per-section totals are
reported beside them so a hole stays visible."""

LANES = (1, 2, 3, 4, 5, 6)
"""1 = leftmost (HOV) .. 4 = rightmost mainline, 5 = auxiliary/deceleration
lane, 6 = the ramp pavement once it has diverged (``lane = floor(y/12 ft)``,
docs/I24_DATA.md §1)."""

DIVERGENCE_SEARCH_M = 200.0
"""Half-width of the window the auxiliary lane's end is looked for in."""

CLASSIFY_SPAN_M = 100.0
"""Length of the classification zone just past the nose (:func:`classify_exits`)."""

DIVERGENCE_BIN_M = 25.0
DIVERGENCE_DROP = 0.25
"""The auxiliary lane has ended at the first bin holding less than this
fraction of its median occupancy over the 400 m before the gore."""

MAINLINE_LANES = (1, 2, 3, 4)
QUOTED_X_M = (2000, 2250, 2500)
"""The bins §2.5 quotes the downstream split at (data x [m], 250 m bins)."""

DOWNSTREAM_X_M = tuple(range(1750, 4500, 250))
"""Bins the downstream split is carried over: the end of the Old Hickory
acceleration lane (1,899 m) to past the Hickory Hollow gore, so the right
lane's deficit can be read against distance from the merge."""

#: Off-ramps of the measured span, with the landmark that marks the gore and
#: the sections `scripts/i24_build_replica.py` takes the exit fraction at.
OFF_RAMPS = (
    {
        "name": "Hickory Hollow Pkwy off-ramp",
        "gore_landmark": "wb_HH_off_end",
        "taper_landmark": "wb_HH_off_start",
    },
    {
        "name": "Bell Road off-ramp (collector road)",
        "gore_landmark": "wb_BR_off_start",
        "taper_landmark": None,
    },
)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def landmark_data_x() -> dict[str, float]:
    """Ramp landmark positions in data x [m], from the committed geometry fit.

    ``artifacts/i24_replica_inputs.json`` holds the landmarks as chain
    positions and the affine chain <-> data-x fit of `scripts/i24_geometry.py`
    (mile-marker regression, residual RMS 58 m, docs/I24_DATA.md §6).
    """
    geo = json.loads(INPUTS.read_text())["geometry"]
    x0 = float(geo["data_x0_chain_m"])
    scale = float(geo["chain_m_per_data_m"])
    return {k: (v - x0) / scale for k, v in geo["ramp_landmarks_chain_m"].items()}


def divergence_x(x: np.ndarray, lane: np.ndarray, gore_x: float) -> tuple[float, list[dict]]:
    """Where the auxiliary lane leaves the mainline, from the recording.

    The decision line the exit classification uses: downstream of it a lane
    >= 5 sample is on the ramp pavement and a lane <= 4 sample is on the
    mainline. It is the first :data:`DIVERGENCE_BIN_M` bin at or after
    ``gore_x - DIVERGENCE_BIN_M`` whose lane-5 sample count falls below
    :data:`DIVERGENCE_DROP` of the lane's median count over the 400 m before
    the gore.

    Args:
        x: Sample positions [m] (any order), covering the gore neighbourhood.
        lane: Lane of each sample.
        gore_x: Gore position [m] from the landmark layer.

    Returns:
        ``(x_div, rows)`` — the decision line [m] and the per-bin lane counts
        around the gore that show it.
    """
    lo, hi = gore_x - 2.0 * DIVERGENCE_SEARCH_M, gore_x + DIVERGENCE_SEARCH_M
    sel = (x >= lo) & (x < hi)
    xb = (np.floor(x[sel] / DIVERGENCE_BIN_M) * DIVERGENCE_BIN_M).astype(np.int64)
    ln = lane[sel]
    edges = np.arange(
        np.floor(lo / DIVERGENCE_BIN_M) * DIVERGENCE_BIN_M, hi, DIVERGENCE_BIN_M, dtype=np.int64
    )
    rows = []
    for e in edges:
        m = xb == e
        rows.append(
            {
                "x_lo_m": int(e),
                "n_by_lane": {str(ln_): int(np.count_nonzero(m & (ln == ln_))) for ln_ in LANES},
            }
        )
    upstream = [r for r in rows if gore_x - 400.0 <= r["x_lo_m"] < gore_x - DIVERGENCE_BIN_M]
    ref = float(np.median([r["n_by_lane"]["5"] for r in upstream])) if upstream else 0.0
    if ref <= 0:
        raise ValueError(f"no auxiliary lane before the gore at x = {gore_x:.0f} m")
    for r in rows:
        if r["x_lo_m"] >= gore_x - DIVERGENCE_BIN_M and r["n_by_lane"]["5"] < DIVERGENCE_DROP * ref:
            return float(r["x_lo_m"]), rows
    raise ValueError(
        f"the auxiliary lane does not end within {DIVERGENCE_SEARCH_M:g} m of {gore_x}"
    )


# --------------------------------------------------------------------------
# fragment classification
# --------------------------------------------------------------------------


def classify_exits(
    veh: np.ndarray,
    x: np.ndarray,
    lane: np.ndarray,
    *,
    n_veh: int,
    gore_x: float,
    div_x: float,
) -> dict[str, np.ndarray]:
    """Label each fragment by what it does at the gore.

    The rule, in full. Decisions are taken in the *classification zone*
    ``[div_x, div_x + 100)`` — the 100 m just past the nose, where the ramp
    and the mainline are already separate lanes and both are still tracked.
    The zone is the same for both classes on purpose: a longer one would
    resolve more through vehicles (they stay in the cameras) without resolving
    more exits (the ramp leaves them), and the ratio would drift with it. A
    fragment (docs/I24_DATA.md §2 — documents are fragments and nothing is
    stitched) is

    * ``exit``: it holds a sample in the zone in a lane >= 5, i.e. it is seen
      on the ramp pavement past the nose, and none there on the mainline;
    * ``through``: it holds a sample in the zone in a lane <= 4;
    * ``both``: both of the above — an identity switch or a duplicate
      fragment (1.4-1.9% of spacings are duplicates, docs/I24_DATA.md); these
      are excluded from every share;
    * ``unresolved``: it reaches the gore bin (``x >= gore_x - 25``) but holds
      no sample in the zone — it disappears in the gore area.
      ``exit_extended`` adds the unresolved fragments whose last sample is in
      a lane >= 5, which is the "disappears there" half of the rule: at the
      nose the auxiliary lane is exit-only (the artifact reports how nearly).

    The rule's limits, all measured and carried in the artifact: tracking on
    the diverged ramp ends within ~50 m of the nose, so ``exit`` is
    under-counted relative to ``through`` and the extended set is the upper
    estimate; a fragment must survive from a bin to the gore to be classified
    at all, and the median fragment spans 117 m, so only the bin adjacent to
    the gore is resolved in practice.

    Args:
        veh: Fragment code per sample (dense integers ``0..n_veh-1``).
        x: Position [m] per sample.
        lane: Lane per sample.
        n_veh: Number of distinct fragment codes.
        gore_x: Gore position [m].
        div_x: Decision line [m] from :func:`divergence_x`.

    Returns:
        Boolean arrays of length ``n_veh`` keyed ``exit``, ``exit_extended``,
        ``through``, ``both``, ``unresolved``, ``reaches_gore``, plus the
        per-fragment ``last_lane`` (int8, 0 where absent).
    """
    past = (x >= div_x) & (x < div_x + CLASSIFY_SPAN_M)
    ramp = np.zeros(n_veh, bool)
    main = np.zeros(n_veh, bool)
    np.logical_or.at(ramp, veh[past & (lane >= 5)], True)
    np.logical_or.at(main, veh[past & (lane <= 4)], True)
    x_max = np.full(n_veh, -np.inf, np.float64)
    np.maximum.at(x_max, veh, x)
    reaches = x_max >= gore_x - DIVERGENCE_BIN_M
    last_lane = last_lane_per_fragment(veh, lane, n_veh)
    both = ramp & main
    exit_ = ramp & ~main
    through = main & ~ramp
    unresolved = reaches & ~ramp & ~main
    return {
        "exit": exit_,
        "exit_extended": exit_ | (unresolved & (last_lane >= 5)),
        "through": through,
        "both": both,
        "unresolved": unresolved,
        "reaches_gore": reaches,
        "last_lane": last_lane,
    }


def last_lane_per_fragment(veh: np.ndarray, lane: np.ndarray, n_veh: int) -> np.ndarray:
    """Lane of each fragment's last sample (samples must be sorted by ``(veh, t)``)."""
    out = np.zeros(n_veh, np.int8)
    is_last = np.empty(len(veh), bool)
    is_last[-1] = True
    is_last[:-1] = veh[1:] != veh[:-1]
    out[veh[is_last]] = lane[is_last]
    return out


def lane_in_window(veh: np.ndarray, lane: np.ndarray, mask: np.ndarray, n_veh: int) -> np.ndarray:
    """Lane held at the last sample inside ``mask`` (0 = the fragment is absent).

    Used to place a classified fragment in a bin: the lane it holds on leaving
    the bin is the one that matters for what it does at the gore downstream.
    Samples must be sorted by ``(veh, t)``.
    """
    out = np.zeros(n_veh, np.int8)
    idx = np.flatnonzero(mask)
    if idx.size:
        v = veh[idx]
        is_last = np.empty(idx.size, bool)
        is_last[-1] = True
        is_last[:-1] = v[1:] != v[:-1]
        out[v[is_last]] = lane[idx][is_last]
    return out


def aux_entries(
    veh: np.ndarray, x: np.ndarray, lane: np.ndarray, *, lo: float, hi: float
) -> dict[str, Any]:
    """Lane changes into (and out of) the auxiliary lane within ``[lo, hi)``.

    A transition is two consecutive samples of one fragment with
    ``lane_prev <= 4 <= 5 <= lane_cur`` (entry) or the reverse (return), the
    position taken at the later sample. Only transitions both of whose samples
    the tracker held survive, so this is a sample of the lane changes, not a
    census; it answers *which lane exiting vehicles come from and where they
    commit*, not how many.
    """
    same = veh[1:] == veh[:-1]
    prev, cur = lane[:-1][same], lane[1:][same]
    pos = x[1:][same]
    win = (pos >= lo) & (pos < hi)
    into = win & (prev <= 4) & (cur >= 5)
    back = win & (prev >= 5) & (cur <= 4)
    edges = np.arange(lo, hi + 1e-6, 100.0)
    return {
        "window_m": [round(lo, 1), round(hi, 1)],
        "n_into_aux": int(into.sum()),
        "n_back_to_mainline": int(back.sum()),
        "into_aux_from_lane": {
            str(ln): int(np.count_nonzero(into & (prev == ln))) for ln in MAINLINE_LANES
        },
        "into_aux_x_hist_100m": {
            "edges_m": [round(float(e), 1) for e in edges],
            "n": np.histogram(pos[into], bins=edges)[0].tolist(),
        },
    }


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------


def load_window(x_lo: float, x_hi: float) -> dict[str, np.ndarray]:
    """Recording samples in ``[x_lo, x_hi)`` over the study period, sorted by ``(veh, t)``.

    Column-pruned and filtered in the scan (the processed day is 993 MB of 5 Hz
    rows), ``veh_id`` kept as dense integer codes: it is only ever compared for
    equality here. ``x`` is scattered across every row group (the conversion
    writes fragments in document order), so the scan decodes the whole column
    whatever the filter; ``pre_buffer=False`` with no readahead holds one
    column chunk at a time instead of a whole row group's worth per thread,
    which is the difference between ~0.4 and ~1.2 GB of resident memory.
    """
    fmt = ds.ParquetFileFormat(
        default_fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False)
    )
    dataset = ds.dataset(WB_DIR / "trajectories.parquet", format=fmt)
    flt = (
        (ds.field("t") >= T_STUDY_LO_S)
        & (ds.field("t") < T_STUDY_HI_S)
        & (ds.field("x") >= x_lo)
        & (ds.field("x") < x_hi)
        & (ds.field("lane") >= 1)
    )
    cols: dict[str, list[np.ndarray]] = {k: [] for k in ("t", "x", "lane", "v")}
    ids: list[pa.Array] = []
    scanner = dataset.scanner(
        columns=["t", "veh_id", "x", "lane", "v"],
        filter=flt,
        batch_size=32768,
        use_threads=False,
        batch_readahead=0,
        fragment_readahead=0,
    )
    for batch in scanner.to_batches():
        if batch.num_rows == 0:
            continue
        cols["t"].append(batch.column("t").to_numpy().astype(np.float32))
        cols["x"].append(batch.column("x").to_numpy().astype(np.float32))
        cols["v"].append(batch.column("v").to_numpy().astype(np.float32))
        cols["lane"].append(batch.column("lane").to_numpy(zero_copy_only=False).astype(np.int8))
        ids.append(batch.column("veh_id").dictionary_encode())
    out = {k: np.concatenate(vs) for k, vs in cols.items()}
    cols.clear()
    codes = pa.chunked_array(ids).combine_chunks().dictionary_encode()
    ids.clear()
    out["veh"] = codes.indices.to_numpy(zero_copy_only=False).astype(np.int32)
    del codes
    order = np.lexsort((out["t"], out["veh"]))
    for k in list(out):
        out[k] = out[k][order]
    return out


def bin_edges(gore_x: float) -> list[float]:
    """Bin boundaries of the kilometre before the gore, upstream → gore."""
    return [gore_x - BIN_M * (N_BINS - i) for i in range(N_BINS + 1)]


def bin_sections(gore_x: float) -> list[float]:
    """The :data:`SECTIONS_PER_BIN` count sections of each bin, in bin order."""
    return [
        lo + BIN_M * (k + 0.5) / SECTIONS_PER_BIN
        for lo in bin_edges(gore_x)[:-1]
        for k in range(SECTIONS_PER_BIN)
    ]


def bin_rows(
    d: dict[str, np.ndarray],
    gore_x: float,
    labels: dict[str, np.ndarray],
    counts: dict[float, dict[int, int]],
) -> list[dict]:
    """One row per 250 m bin of the kilometre before the gore."""
    veh, x, lane, v = d["veh"], d["x"], d["lane"], d["v"]
    n_veh = int(veh.max()) + 1
    edges = bin_edges(gore_x)
    sections = bin_sections(gore_x)
    rows = []
    for i, lo in enumerate(edges[:-1]):
        hi = edges[i + 1]
        m = (x >= lo) & (x < hi)
        ln, sp = lane[m], v[m]
        n_t = int(m.sum())
        secs = sections[i * SECTIONS_PER_BIN : (i + 1) * SECTIONS_PER_BIN]
        n_x = {ln_: sum(counts[s][ln_] for s in secs) for ln_ in LANES}
        n_x_tot = sum(n_x.values())
        present = np.zeros(n_veh, bool)
        np.logical_or.at(present, veh[m], True)
        bin_lane = lane_in_window(veh, lane, m, n_veh)
        ex, th = labels["exit_extended"] & present, labels["through"] & present
        rows.append(
            {
                "x_lo_m": round(float(lo), 1),
                "x_hi_m": round(float(hi), 1),
                "offset_to_gore_m": [
                    round(float(lo - gore_x), 1),
                    round(float(hi - gore_x), 1),
                ],
                "n_samples": n_t,
                "share": {str(k): round(float(np.count_nonzero(ln == k) / n_t), 4) for k in LANES},
                "speed_kmh": {
                    str(k): (
                        round(float(sp[ln == k].mean()) * 3.6, 2)
                        if np.count_nonzero(ln == k) >= 50
                        else None
                    )
                    for k in LANES
                },
                "n_crossings": {str(k): int(n_x[k]) for k in LANES},
                "flow_share": {
                    str(k): (round(n_x[k] / n_x_tot, 4) if n_x_tot else 0.0) for k in LANES
                },
                "section_x_m": [round(s, 1) for s in secs],
                "section_totals": [int(sum(counts[s].values())) for s in secs],
                "exit_analysis": {
                    "n_fragments_present": int(present.sum()),
                    "n_classified": int((ex | th).sum()),
                    "exit_by_lane": {
                        str(k): int(np.count_nonzero(ex & (bin_lane == k))) for k in LANES
                    },
                    "through_by_lane": {
                        str(k): int(np.count_nonzero(th & (bin_lane == k))) for k in LANES
                    },
                },
            }
        )
    return rows


def ramp_record(spec: dict, landmarks: dict[str, float], committed: dict) -> dict:
    """Everything §2.5 asks for, for one off-ramp."""
    gore_x = landmarks[spec["gore_landmark"]]
    taper_x = landmarks[spec["taper_landmark"]] if spec["taper_landmark"] else None
    build = next(r for r in RAMPS if r["name"] == spec["name"])
    d = load_window(gore_x - BIN_M * N_BINS, gore_x + DIVERGENCE_SEARCH_M + CLASSIFY_SPAN_M)
    x, lane, veh = d["x"], d["lane"], d["veh"]
    n_veh = int(veh.max()) + 1
    div_x, div_rows = divergence_x(x, lane, gore_x)
    labels = classify_exits(veh, x, lane, n_veh=n_veh, gore_x=gore_x, div_x=div_x)
    ex, exx = labels["exit"], labels["exit_extended"]
    th, un = labels["through"], labels["unresolved"]

    # One crossing pass for every section this record needs: the bins' own
    # sections, and (the exit volume in the units scripts/i24_build_replica.py
    # works in) the ramp-lane count section and the mainline reference section.
    frame = pd.DataFrame({"t": d["t"], "veh_id": veh, "x": x, "lane": lane}, copy=False)
    counts = first_crossing_lane_counts(
        frame, [*bin_sections(gore_x), build["count_x_m"], build["ref_x_m"]], LANES
    )
    del frame
    gc.collect()
    n_ramp = sum(counts[build["count_x_m"]][k] for k in LANES if k >= 5)
    n_ref = sum(counts[build["ref_x_m"]][k] for k in MAINLINE_LANES)
    committed_fraction = next(
        r["exit_fraction"] for r in committed["ramps"] if r["name"] == spec["name"]
    )

    # is the auxiliary lane exit-only? (resolved fragments seen in it before the gore)
    aux_up = np.zeros(n_veh, bool)
    np.logical_or.at(aux_up, veh[(lane >= 5) & (x < gore_x) & (x >= gore_x - BIN_M * N_BINS)], True)
    rec = {
        "name": spec["name"],
        "gore_landmark": spec["gore_landmark"],
        "gore_x_m": round(gore_x, 1),
        "taper_landmark": spec["taper_landmark"],
        "taper_x_m": round(taper_x, 1) if taper_x is not None else None,
        "divergence_x_m": div_x,
        "classification_zone_m": [div_x, div_x + CLASSIFY_SPAN_M],
        "divergence_check": {
            "rule": (
                f"first {DIVERGENCE_BIN_M:g} m bin at or after the gore whose lane-5 sample count "
                f"is below {DIVERGENCE_DROP:g} of its median over the 400 m before the gore"
            ),
            "offset_to_gore_m": round(div_x - gore_x, 1),
            "bins": div_rows,
        },
        "bins": bin_rows(d, gore_x, labels, counts),
        "fragments": {
            "n_in_window": n_veh,
            "n_reaching_gore": int(labels["reaches_gore"].sum()),
            "exit": int(ex.sum()),
            "exit_extended": int(exx.sum()),
            "through": int(th.sum()),
            "both_ramp_and_mainline": int(labels["both"].sum()),
            "unresolved": int(un.sum()),
            "unresolved_last_lane": {
                str(k): int(np.count_nonzero(un & (labels["last_lane"] == k))) for k in LANES
            },
        },
        "exit_share": {
            "resolved_strict": round(float(ex.sum() / max(ex.sum() + th.sum(), 1)), 4),
            "resolved_extended": round(float(exx.sum() / max(exx.sum() + th.sum(), 1)), 4),
            "unresolved_counted_as_through": round(
                float(exx.sum() / max(exx.sum() + th.sum() + un.sum() - (exx & un).sum(), 1)), 4
            ),
            "builder_units": {
                "definition": (
                    "ramp-lane (>=5) first crossings at count_x_m over mainline (1-4) first "
                    "crossings at ref_x_m, study period pooled; the builder's own estimator "
                    "(scripts/i24_build_replica.py) per 5-min window, on fragment crossings"
                ),
                "count_x_m": build["count_x_m"],
                "ref_x_m": build["ref_x_m"],
                "n_ramp_lane_crossings": int(n_ramp),
                "n_mainline_crossings": int(n_ref),
                "ratio": round(n_ramp / n_ref, 4) if n_ref else None,
                "committed_exit_fraction_mean": round(float(np.mean(committed_fraction)), 4),
                "committed_source": "artifacts/i24_replica_inputs_zip.json",
            },
        },
        "auxiliary_lane": {
            "n_users_before_gore": int(aux_up.sum()),
            "n_users_resolved": int((aux_up & (exx | th)).sum()),
            "n_users_exiting": int((aux_up & exx).sum()),
            "n_users_through": int((aux_up & th).sum()),
            "exit_only_rate": (
                round(float((aux_up & exx).sum() / (aux_up & (exx | th)).sum()), 4)
                if (aux_up & (exx | th)).sum()
                else None
            ),
        },
        "aux_entries": aux_entries(veh, x, lane, lo=gore_x - BIN_M * N_BINS, hi=gore_x),
    }
    del d, x, lane, veh, labels
    gc.collect()
    return rec


def downstream_comparison() -> dict:
    """The §2.5 rows at data x = 2000-2500 m, copied from the lane-profile artifact."""
    prof = json.loads(LANE_PROFILE.read_text())

    def rows(p: dict) -> list[dict]:
        return [
            {
                "x_lo_m": r["x_lo_m"],
                "share": r["share"],
                "flow_share": r["flow_share"],
                "speed_kmh": r["speed_kmh"],
            }
            for r in p["rows"]
            if r["x_lo_m"] in DOWNSTREAM_X_M
        ]

    obs = rows(prof["observed"])
    return {
        "source": "artifacts/i24_lane_profile_zip.json",
        "created_at": prof["created_at"],
        "quoted_x_m": list(QUOTED_X_M),
        "note": (
            "the split docs/MERGE_ROUND6_PLAN.md §2.5 quotes sits at quoted_x_m; 'share' is "
            "vehicle-time (the quoted 38/26/22/14 to 38/22/23/17 and 29/23/18/30 to "
            "31/24/18/27). The range is carried from the end of the Old Hickory "
            "acceleration lane (data x = 1899 m) to past the Hickory Hollow gore, so the "
            "right lane's deficit can be read against distance from the merge; "
            "'right_lane_deficit_points' is observed minus replica, in points of "
            "share of lane 4. Read the vehicle-time column: the lane-profile artifact "
            "counts crossings at one section per bin, and two of the observed sections in "
            "this range are tracking holes (the 2750 bin's section totals 944 vehicles and "
            "the 3250 bin's lane 1 counts 921 against ~2,400 at its neighbours), which is why "
            "this script pools five sections per bin in its own approach bins."
        ),
        "observed": obs,
        "replica": {
            arm: {
                "config_hash": p["config_hash"],
                "seed": p["seed"],
                "rows": rows(p),
                "right_lane_deficit_points": [
                    {
                        "x_lo_m": o["x_lo_m"],
                        "share": round(100.0 * (o["share"]["4"] - r["share"]["4"]), 1),
                        "flow_share": round(
                            100.0 * (o["flow_share"]["4"] - r["flow_share"]["4"]), 1
                        ),
                    }
                    for o, r in zip(obs, rows(p), strict=True)
                ],
            }
            for arm, p in prof["replica"].items()
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    landmarks = landmark_data_x()
    committed = json.loads(ZIP_INPUTS.read_text())
    ramps = []
    for spec in OFF_RAMPS:
        print(f"[{spec['name']}] ...")
        ramps.append(ramp_record(spec, landmarks, committed))
        gc.collect()
    out = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_hash": data_hash(),
        "period": "06:30-08:30 CST",
        "study_period_s": [T_STUDY_LO_S, T_STUDY_HI_S],
        "bin_m": BIN_M,
        "n_bins": N_BINS,
        "sections_per_bin": SECTIONS_PER_BIN,
        "lane_convention": (
            "1 = leftmost (HOV) ... 4 = rightmost mainline, 5 = auxiliary "
            "(deceleration/acceleration) lane, 6 = the ramp pavement once diverged; "
            "lane = floor(y / 12 ft) as in docs/I24_DATA.md §1"
        ),
        "share_convention": (
            "'share' is vehicle-time (5 Hz samples in the bin, over lanes 1-6); 'flow_share' "
            "and 'n_crossings' are vehicles, each counted once per section in the lane it holds "
            "at its first crossing (i24_build_replica.first_crossing_lane_counts), pooled over "
            f"the {SECTIONS_PER_BIN} sections of the bin. Counts are lower bounds at the local "
            "per-lane tracking coverage (docs/I24_DATA.md §4) and are not coverage-corrected, so "
            "shares across lanes inherit the lane-dependent coverage; ratios taken within one "
            "lane (the exit rates) do not."
        ),
        "geometry_source": (
            "artifacts/i24_replica_inputs.json geometry.ramp_landmarks_chain_m, through the "
            "mile-marker fit of scripts/i24_geometry.py (residual RMS 58 m)"
        ),
        "exit_rule": classify_exits.__doc__,
        "limits": [
            "Fragments are not stitched (docs/I24_DATA.md §2, median span 117 m): a fragment "
            "must survive from a bin to the gore to be classified, so 'n_classified' collapses "
            "beyond the bin adjacent to the gore and the exiting-vehicle lane use is resolved "
            "only there. The bins' shares do not depend on this — they are taken on all "
            "samples and all crossings in the bin.",
            "Tracking on the diverged ramp ends within ~50 m of the nose, so the strict exit "
            "set is under-counted relative to the through set; 'exit_share' reports the strict "
            "rate, the extended rate (adding fragments that vanish in the auxiliary lane at "
            "the nose) and the rate with every unresolved fragment counted as a through, which "
            "brackets it.",
            "The auxiliary lane's own tracking rate is not estimated: the per-lane coverage "
            "table of artifacts/i24_coverage.json covers lanes 1-4 only (docs/MERGE_ROUND6_PLAN.md "
            "§2.3). The exit shares are ratios of tracked counts whose numerator is a lane-5 "
            "count and whose denominator is a mainline count, so they carry that unknown "
            "factor; if the auxiliary lane tracks worse than the mainline they are low.",
            "'aux_entries' counts only lane changes both of whose samples one fragment held, "
            "so it is a sample of the transitions, not a census: it says which lane exiting "
            "vehicles come from and where they commit, not how many do.",
            "One day, one direction, 06:30-08:30 CST (docs/I24_DATA.md §7).",
        ],
        "ramps": ramps,
        "downstream_split": downstream_comparison(),
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
