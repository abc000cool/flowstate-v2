"""M2 task 4a — upstream boundary inflow from NGSIM US-101 first appearances.

A vehicle is an *upstream mainline entry* when its first recorded sample lies
near the section's upstream boundary (first ``local_y`` <= 30 m — period 1
entries appear at < 20 m, period 2 tracking starts at ~21-26 m, while on-ramp
entries appear at ~150-165 m) on a mainline lane (1-5). Entries are counted
in 5-minute windows two ways:

* **per period** (recording-relative windows; reported in the processed JSON
  summary and docs) — periods overlap by ~90 s of wall clock, so these tables
  double-count the overlap and are for inspection only;
* **continuous wall-clock timeline** (t = 0 at the period-1 recording start)
  — this deduplicated series becomes the ``DemandProfile`` artifact steps.

Boundary handling on the continuous timeline: NGSIM period processing
censors entries near a period's end (a vehicle that cannot complete its
traverse before the period cutoff is excluded from that period and shows up
in the next one instead) — period 1 records its LAST upstream entry at wall
~894 s even though its rows run to 952.8 s, while period 2 (recording from
wall 863.3 s) detects entries steadily from ~880 s. Entries are therefore
counted from period 1 up to and including its last recorded entry and from
period 2 strictly after that instant. Because period-2 tracking starts
~21-26 m downstream of period-1 tracking, the same physical entry registers
~2-5 s later in period 2; the sharp switch can mis-assign entries within
that fuzz (order 10 vehicles, < 0.5% of the total — noted in the artifact).
The last window of the timeline is partial (the dump truncates period 2 at
08:12:50); its rate uses the actual window duration.

Outputs:
* ``artifacts/demand_us101.json`` — DemandProfile artifact (veh/s totals
  across all 5 mainline lanes).
* ``data/processed/us101_demand_summary.json`` — per-period window tables and
  entry statistics for the docs.

Run: ``uv run --no-sync python scripts/extract_demand_us101.py``
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from flowstate_core.artifacts import DemandProfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import MAINLINE_LANES, PROCESSED_DIR, REPO_ROOT, data_hash, load_us101

ENTRY_MAX_X_M = 30.0
WINDOW_S = 300.0


def _entries(period_df: pd.DataFrame) -> pd.DataFrame:
    """First-appearance rows that qualify as upstream mainline entries."""
    first = period_df.sort_values("frame").groupby("veh_id").first().reset_index()
    ok = (first["x"] <= ENTRY_MAX_X_M) & first["lane"].between(*MAINLINE_LANES)
    return first.loc[ok]


def _windows(times_s: pd.Series, span_s: float) -> list[dict[str, float]]:
    """Count entries in 5-min windows over [0, span_s]; last window partial."""
    out = []
    start = 0.0
    while start < span_s:
        end = min(start + WINDOW_S, span_s)
        n = int(((times_s >= start) & (times_s < end)).sum())
        out.append(
            {
                "t_start_s": start,
                "t_end_s": end,
                "n_entries": n,
                "inflow_veh_s": n / (end - start),
            }
        )
        start += WINDOW_S
    return out


def main() -> None:
    periods = load_us101()
    t0_ms = int(periods["p1"]["global_time_ms"].min())
    end_ms = max(int(df["global_time_ms"].max()) for df in periods.values())
    # Source switch-over: period 1's last recorded upstream entry (see module
    # docstring — later entries are censored out of period 1's processing).
    p1_last_entry_ms = int(_entries(periods["p1"])["global_time_ms"].max())

    summary: dict[str, object] = {
        "periods": {},
        "entry_max_x_m": ENTRY_MAX_X_M,
        "switch_over_wall_s": (p1_last_entry_ms - t0_ms) / 1000.0,
    }
    dedup_times = []
    for label, df in periods.items():
        ent = _entries(df)
        rel_s = (ent["global_time_ms"] - int(df["global_time_ms"].min())) / 1000.0
        summary["periods"][label] = {  # type: ignore[index]
            "n_vehicles": int(df["veh_id"].nunique()),
            "n_upstream_mainline_entries": len(ent),
            "windows_period_relative": _windows(
                rel_s, (int(df["global_time_ms"].max()) - int(df["global_time_ms"].min())) / 1000.0
            ),
        }
        wall_s = (ent["global_time_ms"] - t0_ms) / 1000.0
        if label == "p1":
            wall_s = wall_s[ent["global_time_ms"] <= p1_last_entry_ms]
        else:
            wall_s = wall_s[ent["global_time_ms"] > p1_last_entry_ms]
        dedup_times.append(wall_s)

    all_times = pd.concat(dedup_times)
    span_s = (end_ms - t0_ms) / 1000.0
    windows = _windows(all_times, span_s)
    summary["windows_continuous_dedup"] = windows
    summary["n_entries_dedup"] = len(all_times)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / "us101_demand_summary.json").write_text(json.dumps(summary, indent=2))

    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    profile = DemandProfile(
        created_at=created_at,
        source=(
            "NGSIM US-101 upstream mainline boundary inflow (first appearance at local_y <= "
            "30 m on lanes 1-5), 5-min windows on the continuous wall clock from the period-1 "
            "recording start (2005-06-15 07:49:39.7 PDT). Periods overlap and censor entries "
            "near their ends: entries come from period 1 through its last recorded entry "
            "(~wall 894 s) and from period 2 strictly after (sharp switch; the ~2-5 s "
            "cross-period tracking offset can mis-assign order-10 vehicles, <0.5%). Last "
            "window partial (dump truncates at 08:12:50). Total across 5 lanes; on-ramp "
            "inflow (entries at ~150-165 m) deliberately excluded."
        ),
        data_hash=data_hash(),
        steps=[(w["t_start_s"], w["inflow_veh_s"]) for w in windows],
        geh_vs_counts=None,
    )
    out = REPO_ROOT / "artifacts" / "demand_us101.json"
    profile.save(out)
    print(f"{len(all_times)} deduplicated upstream entries over {span_s:.1f} s -> {out}")
    for w in windows:
        print(
            f"  [{w['t_start_s']:6.1f}, {w['t_end_s']:6.1f}) s: {w['n_entries']:4d} entries"
            f" = {w['inflow_veh_s']:.3f} veh/s ({w['inflow_veh_s'] * 3600:.0f} veh/h)"
        )


if __name__ == "__main__":
    main()
