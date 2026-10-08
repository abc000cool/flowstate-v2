"""Compare the two legs of cmp_a3.py and hashes_a3.py (docs/A3_RANGE_ROUND.md §3).

usage: compare_a3.py DIR OUT_JSON   (DIR holds identity_head.json, identity_change.json, scen_head.json, scen_change.json)

* every case run in both legs: identical when every Parquet sha256, the route file, the whole meta.json (wall time
  removed), compute_metrics and the config hash agree;
* the per-window form at u = 0 (change leg only) against the unset run of the same seed: which files differ, and
  the shares (the record's realized share against the unset plan's drawn one);
* the committed scenarios: config hash and full model_dump sha in both legs.
"""

import json
import sys
from pathlib import Path

D = Path(sys.argv[1])
head = json.loads((D / "identity_head.json").read_text())
change = json.loads((D / "identity_change.json").read_text())
KEYS = ("parquet", "routes_sha", "meta_sha", "metrics_sha", "config_hash")
both = sorted(k for k in head if not k.startswith("_") and "skipped" not in head[k] and k in change)
rows = {k: {f: head[k][f] == change[k][f] for f in KEYS} for k in both}
identical = [k for k in both if all(rows[k].values())]
out = {
    "head_packages": head["_packages"],
    "change_packages": change["_packages"],
    "n_both": len(both),
    "n_identical": len(identical),
    "not_identical": {k: v for k, v in rows.items() if not all(v.values())},
    "skipped_in_head": sorted(k for k in head if not k.startswith("_") and "skipped" in head[k]),
}
u0 = {}
for k in sorted(change):
    if k.startswith("th52_dc_u0:"):
        seed = k.split(":")[1]
        ref = change[f"th52_dc:{seed}"]
        rec = (change[k]["ramp_to_ramp_shares"] or [{}])[0]
        u0[k] = {
            "identical_to_unset": {
                f: change[k][f] == ref[f]
                for f in ("parquet", "routes_sha", "meta_wo_config_sha", "metrics_sha")
            },
            "parquet_files_differing": sorted(
                p for p in change[k]["parquet"] if change[k]["parquet"][p] != ref["parquet"].get(p)
            ),
            "share_drawn_unset_plan": rec.get("share_drawn"),
            "share_realized_u0": rec.get("share_realized"),
            "n_swapped_to_exit": rec.get("n_swapped_to_exit"),
            "n_swapped_from_exit": rec.get("n_swapped_from_exit"),
            "windows": [
                (
                    w["t0_s"],
                    w["n_entrants"],
                    round(w["p"], 4),
                    w["n_ramp_to_ramp_drawn"],
                    w["n_ramp_to_ramp"],
                )
                for w in rec.get("windows", [])
            ],
        }
out["u0_against_unset"] = u0
out["set_arms"] = {
    k: {
        f: (change[k]["ramp_to_ramp_shares"] or [{}])[0].get(f)
        for f in (
            "u",
            "s_max",
            "share_drawn",
            "share_realized",
            "n_clipped",
            "n_swapped_to_exit",
            "free_flow_s",
        )
    }
    for k in sorted(change)
    if k.startswith(("th52_dc_u05", "th52_dc_u1", "th52_dc_share05"))
}
sh = json.loads((D / "scen_head.json").read_text())
sc = json.loads((D / "scen_change.json").read_text())
scen = sorted(k for k in sh if not k.startswith("_"))
out["scenarios"] = {
    "n": len(scen),
    "same_hash_and_dump": sum(1 for k in scen if sh[k] == sc.get(k)),
    "differing": [k for k in scen if sh[k] != sc.get(k)],
    "weave_defaults_same": sh["_weave_defaults"] == sc["_weave_defaults"],
}
Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k not in ("u0_against_unset",)}, indent=1)[:3000])
print(json.dumps(out["u0_against_unset"], indent=1)[:2500])
