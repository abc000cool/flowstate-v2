"""The T.H.52 ramp-to-ramp share sensitivity: per-share summary of the five th52 arms.

A sensitivity of the locked section test's verdict to an unmeasured input
(docs/TH52_CROSSING_SHARE.md §10), not a calibration: no share is chosen from it.
usage (repository root): uv run --no-sync python artifacts/th52_crossing_share_2026-10-07/stats.py DIR
DIR holds th52_prop.json, th52_s040.json, ..., th52_s070.json (merge_model_selfcheck.py th52 --out).
Writes DIR/summary.json and prints a markdown table.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

D = Path(sys.argv[1])
ARMS = [("prop", None), ("s040", 0.40), ("s050", 0.50), ("s060", 0.60), ("s070", 0.70)]


def load(tag: str) -> dict[int, dict]:
    return {r["seed"]: r for r in json.loads((D / f"th52_{tag}.json").read_text())["rows"]}


def ci(d: list[float]) -> list[float]:
    a = np.asarray(d, float)
    h = stats.t.ppf(0.975, len(a) - 1) * a.std(ddof=1) / math.sqrt(len(a))
    return [float(a.mean()), float(a.mean() - h), float(a.mean() + h)]


rows = {tag: load(tag) for tag, _ in ARMS}
seeds = sorted(rows["prop"])
assert all(sorted(r) == seeds for r in rows.values()), "every arm runs the same seeds"
drawn = [rows["s050"][s]["ramp_to_ramp"][0]["share_drawn"] for s in seeds]
# the plan's counts are the same at every share (only who crosses changes); the
# unset arm's are read off the 0.50 arm's record at the same seed (the same draw)
ref = {s: rows["s050"][s]["ramp_to_ramp"][0] for s in seeds}


def crossers_vph(seed: int, n_rr: int) -> float:
    """Planned crossing vehicles per hour: entrants bound past the exit (v_RF)
    plus corridor-entry vehicles bound for it (v_FR), over the 1,200-s run."""
    rec = ref[seed]
    return (rec["n_entrants"] - n_rr + rec["n_exit"] - n_rr) * 3600.0 / 1200.0


out: dict[str, dict] = {}
for tag, share in ARMS:
    r = rows[tag]
    flow = [r[s]["exit_end_flow_vph"] for s in seeds]
    base = [rows["prop"][s]["exit_end_flow_vph"] for s in seeds]
    rec = [r[s]["ramp_to_ramp"] for s in seeds]
    realized = drawn if share is None else [x[0]["share_realized"] for x in rec]
    crit = lambda k: sum(int(r[s]["criteria"][k]) for s in seeds)  # noqa: E731
    out[tag] = {
        "share": share,
        "share_realized_mean": float(np.mean(realized)),
        "share_realized_range": [float(min(realized)), float(max(realized))],
        "n_swapped_to_exit_mean": None
        if share is None
        else float(np.mean([x[0]["n_swapped_to_exit"] for x in rec])),
        "n_entrants_mean": float(np.mean([r[s]["entrance_departed"][1] for s in seeds])),
        "crossers_vph_mean": float(
            np.mean(
                [
                    crossers_vph(
                        s,
                        ref[s]["n_ramp_to_ramp_drawn"]
                        if share is None
                        else r[s]["ramp_to_ramp"][0]["n_ramp_to_ramp"],
                    )
                    for s in seeds
                ]
            )
        ),
        "flow_mean": float(np.mean(flow)),
        "flow_sd": float(np.std(flow, ddof=1)),
        "flow_paired_vs_prop": None if share is None else ci(np.subtract(flow, base).tolist()),
        "observed_inflow_vph": r[seeds[0]]["observed_inflow_vph"],
        "geh_mean": float(np.mean([r[s]["exit_end_flow_geh"] for s in seeds])),
        "geh_lt5": crit("ii_a_flow_geh"),
        "station_speed_ok": crit("ii_b_station_speed"),
        "station_speed_min_mean": float(np.mean([r[s]["station_speed_min_ms"] for s in seeds])),
        "departed_ok": crit("i_departed"),
        "entrance_departed_share": float(
            np.mean([r[s]["entrance_departed"][0] / r[s]["entrance_departed"][1] for s in seeds])
        ),
        "collisions": sum(r[s]["n_collisions"] for s in seeds),
        "seeds_with_collision": sum(int(r[s]["n_collisions"] > 0) for s in seeds),
        "hard_brake_vehicle_steps": sum(r[s]["hard_brake_vehicle_steps"] for s in seeds),
        "exits_given_up": sum(r[s]["exits_given_up"][0] for s in seeds),
        "exits_reached": sum(r[s]["exits_given_up"][1] for s in seeds),
        "give_ups_ok": crit("iv_give_ups"),
        "locks": sum(int(r[s]["lock"]) for s in seeds),
        "all_criteria": sum(int(r[s]["pass"]) for s in seeds),
        "n_seeds": len(seeds),
        "config_hashes": sorted({r[s]["config_hash"] for s in seeds}),
    }
(D / "summary.json").write_text(json.dumps(out, indent=1) + "\n")
print(
    "| share | realized (mean) | planned crossers, veh/h | exit-end flow, veh/h (mean ± sd) | vs proportional, paired [95 %] "
    "| GEH < 5 | station speed > 20 m/s | lowest station speed, m/s (mean) | collisions "
    "| exits given up | locks | all criteria (locked test) |"
)
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for tag, share in ARMS:
    o = out[tag]
    p = o["flow_paired_vs_prop"]
    print(
        f"| {'proportional (unset)' if share is None else f'{share:.2f}'} "
        f"| {o['share_realized_mean']:.3f} | {o['crossers_vph_mean']:,.0f} "
        f"| {o['flow_mean']:,.0f} ± {o['flow_sd']:.0f} "
        f"| {'—' if p is None else f'{p[0]:+.0f} [{p[1]:+.0f}, {p[2]:+.0f}]'} "
        f"| {o['geh_lt5']} / {o['n_seeds']} | {o['station_speed_ok']} / {o['n_seeds']} "
        f"| {o['station_speed_min_mean']:.2f} | {o['collisions']} "
        f"| {o['exits_given_up']} of {o['exits_reached']} | {o['locks']} "
        f"| {o['all_criteria']} / {o['n_seeds']} |"
    )
