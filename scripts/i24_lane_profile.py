"""Lane-by-lane speed and share profiles along the I-24 span: recording vs replica.

The segment-speed criterion averages over lanes; this diagnostic keeps the
lanes apart, which is where the merge behaviour shows (docs/I24_VALIDATION.md
§0.5). For each 250 m bin of data x it reports, per lane, the share of
vehicle-time (``share``), the share of vehicles crossing the bin's count
section (``flow_share``, with the count ``n_vehicles``) and the mean speed,
for the recording (mainline lanes 1–4 plus the auxiliary lane 5, lane 1 =
leftmost) and for one replicate of each validation arm present (its first
seed under ``runs/i24_validation``).

The two shares differ wherever the lanes differ in speed — vehicle-time
over-represents slow lanes — and ``entry_lane_shares`` is applied by SUMO as
a share of flow, so ``flow_share`` is the boundary quantity and ``share`` the
occupancy diagnostic (docs/MERGE_ROUND6_PLAN.md §2.1). The counting rule is
``i24_build_replica.first_crossing_lane_counts``: each vehicle once per
section, in the lane it holds at its first crossing; the section is
``x_lo + FLOW_SECTION_OFFSET_M``, which puts the entry bin's section at the
corridor's mainline count section (data x = 200 m).

SUMO lane indices count from the right (0 = rightmost) per edge, so the
replica's lanes are mapped to the recording's numbering through the lane
count of the edge at that x, read from the arm's own map (``osm_file``).

Outputs ``artifacts/i24_lane_profile.json`` and
``docs/figures/i24_lane_profile.png``. Run from the repo root::

    uv run --no-sync python scripts/i24_lane_profile.py [--arms speedcal ramps ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import (
    CORRIDOR_EDGES,
    FLOW_COUNT_LANES,
    FLOW_SECTION_OFFSET_M,
    RAMPS,
    T_STUDY_HI_S,
    T_STUDY_LO_S,
    WARMUP_S,
    first_crossing_lane_counts,
)
from i24_data import REPO_ROOT

from microsim.networks import osm_import
from microsim.runner import is_run_complete

OUT = REPO_ROOT / "artifacts" / "i24_lane_profile.json"
FIG = REPO_ROOT / "docs" / "figures" / "i24_lane_profile.png"
BIN_M = 250.0
X_LO, X_HI = -500.0, 5500.0
MIN_SHARE = 0.03
LANES = FLOW_COUNT_LANES  # 1-4 mainline (1 = leftmost) + 5 = auxiliary/ramp lane
ARM_ORDER = ("tracked", "corrected", "speedcal", "ramps")


def _xy() -> tuple[float, float]:
    g = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())["geometry"]
    return float(g["sim_x_of_data_x"]["a"]), float(g["sim_x_of_data_x"]["b"])


def _frame(table: pa.Table) -> pd.DataFrame:
    """Arrow trajectory table → pandas, with ``veh_id`` as compact integer codes.

    The id is only ever compared for equality here (grouping a fragment's
    consecutive samples), and the recording's window holds ~2·10⁷ of them; as
    Python strings that is gigabytes, as dictionary indices it is 4 bytes each.
    """
    ids = table.column("veh_id").combine_chunks()
    codes = ids.dictionary_encode().indices.to_numpy(zero_copy_only=False)
    df = table.select([c for c in table.column_names if c != "veh_id"]).to_pandas()
    df["veh_id"] = codes
    return df


def _profile(df: pd.DataFrame) -> dict:
    """Shares, vehicle counts and mean speed per (bin, lane).

    ``share`` is the bin's share of vehicle-time (5 Hz samples, or the run's
    output samples); ``flow_share`` is its share of the vehicles crossing the
    bin's count section at ``x_lo + FLOW_SECTION_OFFSET_M``, each counted once
    (:func:`~i24_build_replica.first_crossing_lane_counts`), of which
    ``n_vehicles`` holds the per-lane counts themselves — shares recomputed
    from those counts are exact, the rounded ``flow_share`` is for reading.
    Lanes with share < MIN_SHARE report a blank speed. ``df`` needs
    ``t, veh_id, x, lane, v``.
    """
    df = df[(df["x"] >= X_LO) & (df["x"] < X_HI) & df["lane"].isin(LANES)]
    xb = (np.floor(df["x"] / BIN_M) * BIN_M).astype(int)
    tot = df.groupby(xb).size()
    share = df.groupby([xb, df["lane"]]).size().unstack(fill_value=0).div(tot, axis=0)
    spd = df.groupby([xb, df["lane"]])["v"].mean().unstack() * 3.6
    bins = sorted(tot.index)
    flow = first_crossing_lane_counts(df, [x + FLOW_SECTION_OFFSET_M for x in bins], LANES)
    rows = []
    for x in bins:
        n_lane = flow[float(x) + FLOW_SECTION_OFFSET_M]
        n_veh = sum(n_lane.values())
        rows.append(
            {
                "x_lo_m": int(x),
                "n": int(tot.loc[x]),
                "share": {
                    str(lane): round(float(share.loc[x].get(lane, 0.0)), 4) for lane in LANES
                },
                "speed_kmh": {
                    str(lane): (
                        round(float(spd.loc[x].get(lane)), 2)
                        if share.loc[x].get(lane, 0.0) >= MIN_SHARE
                        else None
                    )
                    for lane in LANES
                },
                "n_vehicles": {str(lane): int(n_lane[lane]) for lane in LANES},
                "flow_share": {
                    str(lane): (round(n_lane[lane] / n_veh, 4) if n_veh else 0.0) for lane in LANES
                },
            }
        )
    return {"bin_m": BIN_M, "flow_section_offset_m": FLOW_SECTION_OFFSET_M, "rows": rows}


def observed_profile() -> dict:
    t = pq.read_table(
        REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_20221130" / "trajectories.parquet",
        columns=["t", "veh_id", "x", "lane", "v"],
        filters=[
            ("t", ">=", T_STUDY_LO_S),
            ("t", "<", T_STUDY_HI_S),
            ("x", ">=", X_LO),
            ("x", "<", X_HI),
        ],
    )
    return _profile(_frame(t))


def _lanes_of_x(osm_file: str) -> list[tuple[float, float, int]]:
    """``(data_x_lo, data_x_hi, n_lanes)`` per corridor edge from the arm's map."""
    a, b = _xy()
    keep = [e for r in RAMPS for e in r["edges"]]
    with tempfile.TemporaryDirectory() as td:
        bundle = osm_import(
            osm_file=REPO_ROOT / osm_file,
            workdir=Path(td),
            corridor_edges=list(CORRIDOR_EDGES),
            keep_edges=keep,
            geometry_remove=False,
        )
        import sumolib

        net = sumolib.net.readNet(str(bundle.net_path))
        out = []
        for eid, off, length in zip(
            bundle.edge_ids, bundle.offsets, bundle.edge_lengths, strict=True
        ):
            n = net.getEdge(eid).getLaneNumber()
            out.append(((off - a) / b, (off + length - a) / b, int(n)))
    return out


FAMILY = ""  # scenario family suffix ("_zip"); set by --family


def replica_profile(arm: str) -> dict | None:
    art = REPO_ROOT / "artifacts" / f"i24_validation{FAMILY}_{arm}.json"
    if not art.is_file():
        return None
    res = json.loads(art.read_text())
    root = REPO_ROOT / "runs" / f"i24_validation{FAMILY}" / arm / res["config_hash"]
    run_dir = next(
        (root / str(s) for s in res["seeds"] if is_run_complete(root / str(s))),
        None,
    )
    if run_dir is None:
        print(f"[{arm}] no replicate with trajectories under {root}; skipped")
        return None
    scenario = yaml.safe_load((REPO_ROOT / "scenarios" / f"{res['scenario']}.yaml").read_text())
    prof = run_profile(run_dir, scenario["network"]["osm_file"])
    prof.update({"arm": arm, "config_hash": res["config_hash"]})
    return prof


def run_profile(run_dir: Path, osm_file: str) -> dict:
    """Lane profile of one run directory (its ``trajectories.parquet``) on ``osm_file``."""
    edges = _lanes_of_x(osm_file)
    a, b = _xy()
    df = _frame(
        pq.read_table(run_dir / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    )
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - WARMUP_S
    df = df[(df["t"] >= 0.0) & (df["t"] < T_STUDY_HI_S - T_STUDY_LO_S)]
    n_lanes = np.full(len(df), np.nan)
    x = df["x"].to_numpy()
    for lo, hi, n in edges:
        n_lanes[(x >= lo) & (x < hi)] = n
    df = df[np.isfinite(n_lanes)].copy()
    # SUMO lane 0 = rightmost; recording lane 1 = leftmost, lane 5 = auxiliary
    df["lane"] = (n_lanes[np.isfinite(n_lanes)] - df["lane"].to_numpy()).astype(int)
    prof = _profile(df)
    meta_path = run_dir / "meta.json"
    seed = json.loads(meta_path.read_text()).get("seed") if meta_path.is_file() else None
    prof.update(
        {
            "seed": seed if seed is not None else run_dir.name,
            "osm_file": osm_file,
            "lanes_by_x": [[round(lo), round(hi), n] for lo, hi, n in edges],
        }
    )
    return prof


def figure(obs: dict, arms: dict[str, dict], fig_path: Path = FIG) -> None:
    panels = [("recording (I-24 MOTION, lanes 1–5)", obs)] + [
        (f"replica, {arm} (seed {p['seed']})", p) for arm, p in arms.items()
    ]
    fig, axes = plt.subplots(2, len(panels), figsize=(4.4 * len(panels), 6.4), sharex=True)
    axes = np.atleast_2d(axes)
    colors = {1: "#0d366b", 2: "#2a78d6", 3: "#6da7ec", 4: "#eb6834", 5: "#8e44ad"}
    for j, (title, prof) in enumerate(panels):
        xs = [r["x_lo_m"] / 1000.0 + BIN_M / 2000.0 for r in prof["rows"]]
        for lane in LANES:
            sp = [r["speed_kmh"][str(lane)] for r in prof["rows"]]
            sh = [100.0 * r["share"][str(lane)] for r in prof["rows"]]
            axes[0, j].plot(
                xs,
                [np.nan if v is None else v for v in sp],
                color=colors[lane],
                lw=1.6,
                label=f"lane {lane}",
            )
            axes[1, j].plot(xs, sh, color=colors[lane], lw=1.6)
        axes[0, j].set_title(title, fontsize=9)
        axes[0, j].set_ylim(0, 80)
        axes[1, j].set_ylim(0, 45)
        axes[1, j].set_xlabel("data x [km] from MM 62.7")
        for ax in axes[:, j]:
            ax.grid(True, color="#e8e7e2", lw=0.6)
            ax.axvspan(0.751, 1.899, color="#eb6834", alpha=0.07, lw=0)
            ax.axvspan(3.427, 3.947, color="#2a78d6", alpha=0.07, lw=0)
    axes[0, 0].set_ylabel("mean speed [km/h]")
    axes[1, 0].set_ylabel("share of vehicle-time [%]")
    axes[0, 0].legend(fontsize=7.5, loc="upper right", ncol=2)
    fig.suptitle(
        "Lane-by-lane profiles, 06:30–08:30 CST; shaded: Old Hickory acceleration lane and Hickory Hollow deceleration lane (landmark positions)",
        fontsize=9,
    )
    fig.tight_layout()
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_path, dpi=150)
    print(f"-> {fig_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--arms", nargs="*", default=list(ARM_ORDER))
    ap.add_argument(
        "--run",
        nargs=3,
        action="append",
        metavar=("LABEL", "RUN_DIR", "OSM_FILE"),
        default=[],
        help="also profile an ad-hoc run directory (repeatable)",
    )
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--fig", type=Path, default=FIG)
    ap.add_argument("--family", default="", help="scenario family suffix (e.g. zip)")
    args = ap.parse_args()
    global FAMILY
    FAMILY = f"_{args.family}" if args.family else ""
    obs = observed_profile()
    arms = {}
    for arm in args.arms:
        p = replica_profile(arm)
        if p is not None:
            arms[arm] = p
    for label, run_dir, osm_file in args.run:
        arms[label] = run_profile(Path(run_dir), osm_file)
    out = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "period": "06:30-08:30 CST",
        "x_range_m": [X_LO, X_HI],
        "bin_m": BIN_M,
        "min_share_for_speed": MIN_SHARE,
        "lane_convention": "1 = leftmost (HOV) ... 4 = rightmost mainline, 5 = auxiliary (ramp) lane; replica lanes mapped through the edge lane count",
        "flow_section_offset_m": FLOW_SECTION_OFFSET_M,
        "share_convention": (
            "'share'/'n' are vehicle-time (5 Hz samples in the bin); 'n_vehicles' (per lane) and "
            "'flow_share' are vehicles, each counted once in the lane it holds at its first "
            "crossing of data x = x_lo_m + flow_section_offset_m, on a frame of lanes 1-5. "
            "entry_lane_shares is applied by SUMO as a share "
            "of flow, so the flow shares are the boundary quantity (docs/MERGE_ROUND6_PLAN.md "
            "§2.1); observed counts are lower bounds at the per-lane tracking coverage "
            "(docs/I24_DATA.md §4) and are not coverage-corrected"
        ),
        "observed": obs,
        "replica": arms,
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"-> {args.out}")
    figure(obs, arms, args.fig)


if __name__ == "__main__":
    main()
