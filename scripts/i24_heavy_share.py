"""Observed heavy-vehicle share and length on the I-24 span (for FleetSpec.heavy).

Counts the mainline fragments (lanes 1–4, data x in [0, 5492) m, 06:30–08:30
CST) by coarse vehicle class and reports the share of fragments (the
count-based quantity that matches the demand input), the share of
vehicle-time, and the median length of the heavy classes (4 = semi,
5 = truck, data documentation v1.x). Fragment counts are coverage-limited
like every I-24 count (docs/I24_DATA.md §4); the share is a ratio of two
such counts and is reported with that caveat. Output:
``artifacts/i24_heavy_observed.json``. Run from the repo root::

    uv run --no-sync python scripts/i24_heavy_share.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S
from i24_data import REPO_ROOT, WB_DIR, data_hash

OUT = REPO_ROOT / "artifacts" / "i24_heavy_observed.json"
HEAVY_CLASSES = (4, 5)
CLASS_LABELS = {0: "sedan", 1: "midsize", 2: "van", 3: "pickup", 4: "semi", 5: "truck"}
SPAN_M = (0.0, 5492.0)


def main() -> None:
    t = pq.read_table(
        WB_DIR / "trajectories.parquet",
        columns=["veh_id", "cls", "length"],
        filters=[
            ("t", ">=", T_STUDY_LO_S),
            ("t", "<", T_STUDY_HI_S),
            ("x", ">=", SPAN_M[0]),
            ("x", "<", SPAN_M[1]),
            ("lane", ">=", 1),
            ("lane", "<=", 4),
        ],
    ).to_pandas()
    by = t.groupby("cls").agg(rows=("veh_id", "size"), fragments=("veh_id", "nunique"))
    frag = t.drop_duplicates("veh_id")
    n_frag = int(frag.shape[0])
    heavy = frag[frag["cls"].isin(HEAVY_CLASSES)]
    out = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_hash": data_hash(),
        "period": "06:30-08:30 CST",
        "span_data_x_m": list(SPAN_M),
        "lanes": [1, 4],
        "heavy_classes": list(HEAVY_CLASSES),
        "n_fragments": n_frag,
        "fraction_fragments": round(float(len(heavy) / n_frag), 4),
        "fraction_vehicle_time": round(
            float(by.loc[list(HEAVY_CLASSES), "rows"].sum() / by["rows"].sum()), 4
        ),
        "length_median_m": round(float(heavy["length"].median()), 2),
        "length_p10_p90_m": [round(float(q), 2) for q in heavy["length"].quantile([0.1, 0.9])],
        "by_class": {
            CLASS_LABELS.get(int(c), str(c)): {
                "fragments": int(by.loc[c, "fragments"]),
                "share_fragments": round(float(by.loc[c, "fragments"] / n_frag), 4),
                "share_vehicle_time": round(float(by.loc[c, "rows"] / by["rows"].sum()), 4),
                "length_median_m": round(float(frag[frag["cls"] == c]["length"].median()), 2),
            }
            for c in by.index
        },
        "note": "fragment shares are ratios of coverage-limited counts (docs/I24_DATA.md §4); "
        "heavy vehicles are easier to track than cars, so the share is, if anything, high; "
        "the fraction_fragments value is the count-based quantity matching the demand input",
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "n_fragments",
                    "fraction_fragments",
                    "fraction_vehicle_time",
                    "length_median_m",
                )
            }
        )
    )
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
