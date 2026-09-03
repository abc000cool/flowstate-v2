"""Relative-threshold wave statistics for the I-24 validation arms (ROADMAP D1).

Re-detects backward fronts on the observed field and on every replicate of
both validation arms with the standard 40 km/h detector and the
relative-threshold variant (``detect_waves(relative_frac=0.5)``, jam below
0.5 × the field's p90 speed) on the same 15 s × 75 m site-clipped fields as
``scripts/i24_validate.py``. Writes ``artifacts/i24_validation_waves_relative.json``
(the source of the §4 table in docs/I24_VALIDATION.md).

Run after ``scripts/i24_validate.py``::

    uv run --no-sync python scripts/i24_waves_relative.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WARMUP_S
from i24_data import REPO_ROOT, load_mainline

from validation.fields import SpeedField, speed_field
from validation.waves import detect_waves, relative_jam_threshold

RELATIVE_FRAC = 0.5
DT_BIN_S, DX_BIN_M = 15.0, 75.0
BAND = (14.0, 22.0)


def front_stats(field: SpeedField, frac: float | None) -> dict:
    """Backward-front statistics of one field with one detector."""
    ws = detect_waves(field) if frac is None else detect_waves(field, relative_frac=frac)
    bw = np.array([-w.speed_ms * 3.6 for w in ws.waves if w.speed_ms < 0])
    thr = 40.0 if frac is None else relative_jam_threshold(field, frac) * 3.6
    return {
        "thr_kmh": float(thr),
        "n_backward": int(bw.size),
        "mean": float(bw.mean()) if bw.size else None,
        "median": float(np.median(bw)) if bw.size else None,
        "in_band": float(np.mean((bw >= BAND[0]) & (bw <= BAND[1]))) if bw.size else None,
        "frac_bins_below_40": float(np.nanmean(field.mean_speed < 40.0 / 3.6)),
        "p90_kmh": float(np.nanpercentile(field.mean_speed, 90) * 3.6),
        "speeds": bw.round(2).tolist(),
    }


def main() -> None:
    inputs = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    span_hi = float(inputs["geometry"]["measured_span_data_x_m"][1])
    out: dict = {"relative_frac": RELATIVE_FRAC, "field_bins": f"{DT_BIN_S:g} s x {DX_BIN_M:g} m"}

    obs = load_mainline(
        t_range_s=(T_STUDY_LO_S, T_STUDY_HI_S), x_range_m=(0.0, span_hi), columns=["t", "x", "v"]
    )
    obs["t"] = obs["t"] - T_STUDY_LO_S
    f_obs = speed_field(obs, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M)
    out["observed"] = {
        "absolute": front_stats(f_obs, None),
        "relative": front_stats(f_obs, RELATIVE_FRAC),
    }
    del obs

    for arm in ("tracked", "corrected"):
        res_path = REPO_ROOT / "artifacts" / f"i24_validation_{arm}.json"
        if not res_path.is_file():
            continue
        res = json.loads(res_path.read_text())
        per: dict[str, list[dict]] = {"absolute": [], "relative": []}
        for d in res["simulated"]["run_dirs"]:
            df = pd.read_parquet(Path(d) / "trajectories.parquet", columns=["t", "x", "v"])
            df["x"] = (df["x"] - a) / b
            df["t"] = df["t"] - WARMUP_S
            df = df[(df["t"] >= 0.0) & (df["x"] >= 0.0) & (df["x"] < span_hi)]
            f = speed_field(df, dt_bin=DT_BIN_S, dx_bin=DX_BIN_M)
            per["absolute"].append(front_stats(f, None))
            per["relative"].append(front_stats(f, RELATIVE_FRAC))
            del df
        summary = {}
        for key, lst in per.items():
            pooled = np.concatenate([np.asarray(s["speeds"]) for s in lst]) if lst else np.array([])
            rep_means = [s["mean"] for s in lst if s["mean"] is not None]
            summary[key] = {
                "n_reps_with_backward": len(rep_means),
                "mean_of_rep_means": float(np.mean(rep_means)) if rep_means else None,
                "all_fronts": int(pooled.size),
                "median_all": float(np.median(pooled)) if pooled.size else None,
                "in_band_all": (
                    float(np.mean((pooled >= BAND[0]) & (pooled <= BAND[1])))
                    if pooled.size
                    else None
                ),
                "mean_thr_kmh": float(np.mean([s["thr_kmh"] for s in lst])),
                "mean_frac_bins_below_40": float(np.mean([s["frac_bins_below_40"] for s in lst])),
                "mean_p90_kmh": float(np.mean([s["p90_kmh"] for s in lst])),
                "rep_means": rep_means,
            }
        out[arm] = {"config_hash": res["config_hash"], **summary}
        print(arm, {k: v["mean_of_rep_means"] for k, v in summary.items()})

    (REPO_ROOT / "artifacts" / "i24_validation_waves_relative.json").write_text(
        json.dumps(out, indent=1)
    )
    print("observed", {k: v["mean"] for k, v in out["observed"].items()})


if __name__ == "__main__":
    main()
