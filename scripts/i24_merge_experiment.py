"""Is the Old Hickory merge the replica's remaining defect? Four one-seed variants.

docs/I24_CAPACITY.md §5: with the capacity-calibrated population and the
fitted demand level, the replica's first kilometre (the Old Hickory Boulevard
on-ramp merge) runs a third too slow and the next kilometre a third too
fast, while everything downstream of 2.2 km is within 4% of the recording.
This script runs the ``i24_replica_speedcal`` arm, one seed, in four
variants and reports the same segment-speed table:

* ``as_is`` — the arm unchanged;
* ``oh_tracked`` — the Old Hickory on-ramp inflow at its tracked (uncorrected)
  level, everything else unchanged (is the ramp's coverage correction the
  overshoot?);
* ``oh_closed`` — the Old Hickory on-ramp inflow set to zero (an upper bound
  on the merge's effect on the mainline);
* ``lc_default`` — ``lc_strategic`` at SUMO's default 1.0 instead of the
  replica's 5.0 (the diverge fix; does it hurt the merge?);
* ``coop_0.5`` / ``coop_0`` — mainline cooperation (``lcCooperative``) halved
  / off;
* ``assertive_2`` / ``assertive_4`` — ramp and mainline gap acceptance
  (``lcAssertive``) doubled / quadrupled;
* ``speedgain_0`` — tactical lane changes (``lcSpeedGain``) off;
* ``coop_0.5_assertive_2`` — both merge levers together.

Each variant reports the segment-speed RMSPE over the fitted first hour and
the held-out second hour separately, so a merge setting can be adopted the
way the demand level was.

Writes ``artifacts/i24_merge_experiment.json``. Diagnostic only: nothing here
is a calibration, and no variant is adopted by this script.

Run: ``uv run --no-sync python scripts/i24_merge_experiment.py --procs 2``
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_build_replica import crossings_per_window
from i24_validate import SECTIONS_M, WINDOW_S, _inputs, _segment_speeds, _sim_frame, _span

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import geh, rmspe

REPO = Path(__file__).resolve().parents[1]
ARM_YAML = REPO / "scenarios" / "i24_replica_speedcal.yaml"
TRACKED_YAML = REPO / "scenarios" / "i24_replica.yaml"
OBSERVED = REPO / "artifacts" / "i24_validation_observed.json"
OUT = REPO / "artifacts" / "i24_merge_experiment.json"
OH = "Old Hickory Blvd on-ramp"
VARIANTS = (
    "as_is",
    "oh_tracked",
    "oh_closed",
    "lc_default",
    "coop_0.5",
    "coop_0",
    "assertive_2",
    "assertive_4",
    "speedgain_0",
    "coop_0.5_assertive_2",
    "geometry_corrected",
    "geometry_corrected_lc1",
    "geometry_corrected_lc2",
    "geometry_corrected_assertive_1.5",
    "geometry_corrected_assertive_2",
    "geometry_corrected_assertive_3",
    "geometry_corrected_assertive_2_coop_0.5",
    "geometry_corrected_ramplc1",
    "geometry_corrected_ramplc1_assertive_1.5",
    "as_is_entrylanes",
    "geometry_corrected_entrylanes",
    "geometry_corrected_ramplc1_entrylanes",
    "geometry_corrected_ramplc1_entrylanes_sublane",
    "geometry_corrected_ramplc1_entrylanes_sublane_coop0.5",
    "as_is_sublane",
    "as_is_heavy",
    "geometry_corrected_ramplc1_entrylanes_zipper",
    "geometry_corrected_ramplc1_entrylanes_accel",
    "geometry_corrected_ramplc1_entrylanes_zipper_meter",
)
TRAIN_WINDOWS = range(0, 12)
TEST_WINDOWS = range(12, 24)


LANE_PROFILE = REPO / "artifacts" / "i24_lane_profile.json"


def observed_entry_lane_shares() -> list[float]:
    """Recorded share of mainline vehicle-time per lane (1–4, left to right) at
    data x in [0, 250) m over the study period (``artifacts/i24_lane_profile.json``)."""
    prof = json.loads(LANE_PROFILE.read_text())["observed"]
    row = next(r for r in prof["rows"] if r["x_lo_m"] == 0)
    raw = [float(row["share"][str(lane)]) for lane in (1, 2, 3, 4)]
    tot = sum(raw)
    return [round(v / tot, 4) for v in raw]


def variant_config(name: str) -> dict[str, Any]:
    raw = yaml.safe_load(ARM_YAML.read_text())
    merge_model = None
    meter_on = False
    jm_gap = None
    if "_jm" in name:
        name, jm_text = name.rsplit("_jm", 1)
        jm_gap = float(jm_text)
        raw["fleet"]["jm_timegap_minor_s"] = jm_gap
    if name.endswith("_meter"):
        meter_on = True
        name = name[: -len("_meter")]
    for suffix, model in (("_zipper", "zipper"), ("_accel", "acceleration_lane")):
        if name.endswith(suffix):
            merge_model = model
            name = name[: -len(suffix)]
    if name.endswith("_heavy"):
        # the recording's heavy vehicles (share, length) with their own fitted population
        obs_heavy = json.loads((REPO / "artifacts" / "i24_heavy_observed.json").read_text())
        raw["fleet"]["heavy"] = {
            "fraction": float(obs_heavy["fraction_fragments"]),
            "length_m": float(obs_heavy["length_median_m"]),
            "emission_class": "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
            "vclass": "truck",
            "idm_calibration": "artifacts/idm_i24_heavy.json",
        }
        name = name[: -len("_heavy")]
    if name.endswith("_sublane_coop0.5"):
        raw["fleet"]["lc_cooperative"] = 0.5
        name = name[: -len("_coop0.5")]
    if name.endswith("_sublane"):
        # SUMO sublane model (LC_SL2015): continuous lateral positions, gradual merges
        raw["sim"]["lateral_resolution_m"] = 0.8
        name = name[: -len("_sublane")]
    if name.endswith("_entrylanes"):
        # measured upstream lane distribution as the insertion boundary
        raw["network"]["entry_lane_shares"] = observed_entry_lane_shares()
        name = name[: -len("_entrylanes")]
    ramps = {r["name"]: r for r in raw["network"]["ramps"]}
    if name == "oh_tracked":
        tracked = {
            r["name"]: r for r in yaml.safe_load(TRACKED_YAML.read_text())["network"]["ramps"]
        }
        ramps[OH]["inflow"] = tracked[OH]["inflow"]
    elif name == "oh_closed":
        ramps[OH]["inflow"] = [[t, 0.0] for t, _ in ramps[OH]["inflow"]]
    elif name == "lc_default":
        raw["fleet"]["lc_strategic"] = 1.0
    elif name == "coop_0.5":
        raw["fleet"]["lc_cooperative"] = 0.5
    elif name == "coop_0":
        raw["fleet"]["lc_cooperative"] = 0.0
    elif name == "assertive_2":
        raw["fleet"]["lc_assertive"] = 2.0
    elif name == "assertive_4":
        raw["fleet"]["lc_assertive"] = 4.0
    elif name == "speedgain_0":
        raw["fleet"]["lc_speed_gain"] = 0.0
    elif name == "coop_0.5_assertive_2":
        raw["fleet"]["lc_cooperative"] = 0.5
        raw["fleet"]["lc_assertive"] = 2.0
    elif name == "geometry_corrected":
        # scripts/i24_correct_osm.py: auxiliary lanes at the landmark positions
        raw["network"]["osm_file"] = "data/osm/i24_motion_corrected.osm"
    elif name in ("geometry_corrected_lc1", "geometry_corrected_lc2"):
        # corrected map, and the strategic eagerness that the short diverge
        # pocket had forced to 5 returned toward SUMO's default
        raw["network"]["osm_file"] = "data/osm/i24_motion_corrected.osm"
        raw["fleet"]["lc_strategic"] = 1.0 if name.endswith("lc1") else 2.0
    elif name.startswith("geometry_corrected_ramplc1"):
        # corrected map; ramp-origin vehicles keep SUMO's default strategic
        # eagerness (use the acceleration lane), mainline keeps the diverge fix
        raw["network"]["osm_file"] = "data/osm/i24_motion_corrected.osm"
        raw["fleet"]["lc_strategic_ramp"] = 1.0
        if name.endswith("_assertive_1.5"):
            raw["fleet"]["lc_assertive"] = 1.5
    elif name.startswith("geometry_corrected_assertive_"):
        # corrected map + gap acceptance at the merge (SUMO lcAssertive), the
        # lever the original-map experiment found clears the entry queue
        raw["network"]["osm_file"] = "data/osm/i24_motion_corrected.osm"
        rest = name[len("geometry_corrected_assertive_") :]
        if "_coop_" in rest:
            a_str, c_str = rest.split("_coop_")
            raw["fleet"]["lc_cooperative"] = float(c_str)
        else:
            a_str = rest
        raw["fleet"]["lc_assertive"] = float(a_str)
    elif name != "as_is":
        raise ValueError(name)
    raw["name"] = (
        f"i24_merge_{name}"
        + ("_entrylanes" if raw["network"].get("entry_lane_shares") else "")
        + ("_sublane" if raw["sim"].get("lateral_resolution_m") else "")
        + ("_heavy" if raw["fleet"].get("heavy") else "")
        + (
            f"_coop{raw['fleet']['lc_cooperative']:g}"
            if raw["fleet"].get("lc_cooperative", 1.0) != 1.0
            else ""
        )
    )
    if merge_model is not None or meter_on:
        # RampSpec.merge / RampSpec.meter on the Old Hickory on-ramp; the
        # ALINEA target is the fitted diagram's critical density per lane
        for ramp in raw["network"]["ramps"]:
            if ramp["name"] == OH:
                if merge_model is not None:
                    ramp["merge"] = merge_model
                if meter_on:
                    fd = json.loads((REPO / "artifacts" / "fd_i24.json").read_text())
                    fd = fd.get("fd", fd)
                    rho_c = (
                        float(fd["rho_jam"])
                        * abs(float(fd["w"]))
                        / (float(fd["v_f"]) + abs(float(fd["w"])))
                    )
                    ramp["meter"] = {
                        "controller": "alinea",
                        "params": {"rho_target_veh_km": round(1000.0 * rho_c, 3)},
                        "interval_s": 30.0,
                        "stop_line_m": 30.0,
                    }
        raw["name"] += (f"_{merge_model}" if merge_model else "") + ("_meter" if meter_on else "")
    if jm_gap is not None:
        raw["name"] += f"_jm{jm_gap:g}"
    return raw


def _job(args: tuple[str, int, str | None]) -> dict[str, Any]:
    name, seed, keep_dir = args
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    _lo, span_hi = _span()
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    cfg = ScenarioConfig.model_validate(variant_config(name))
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        if keep_dir is not None:
            import shutil

            dest = Path(keep_dir) / name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(paths.run_dir, dest, ignore=shutil.ignore_patterns("net"))
        df = _sim_frame(paths.run_dir, a, b)
        seg = _segment_speeds(df, span_hi, obs.shape[0])
        counts = np.array(
            [crossings_per_window(df, s, 0.0, obs.shape[0] * WINDOW_S) for s in SECTIONS_M]
        )
    ok = np.isfinite(seg) & np.isfinite(obs)
    obs_rec = np.array(
        json.loads(OBSERVED.read_text())["hourly_flows_veh_h_recommended"], dtype=float
    )
    sim_hourly = counts * 3600.0 / WINDOW_S
    geh_vals = [
        geh(float(m), float(c)) for m, c in zip(sim_hourly.ravel(), obs_rec.ravel(), strict=True)
    ]

    def _agg(f: np.ndarray, k: int) -> np.ndarray:
        n = f.shape[0] // k * k
        return np.nanmean(f[:n].reshape(-1, k, f.shape[1]), axis=1)

    s15, o15 = _agg(seg, 3), _agg(obs, 3)
    ok15 = np.isfinite(s15) & np.isfinite(o15)

    def _win(windows: range) -> float:
        m = np.zeros_like(ok)
        m[list(windows)] = True
        sel = ok & m
        return round(float(rmspe(seg[sel], obs[sel])), 4)

    return {
        "variant": name,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "ramps": meta.get("ramps"),
        "rmspe_all": round(float(rmspe(seg[ok], obs[ok])), 4),
        "rmspe_15min": round(float(rmspe(s15[ok15], o15[ok15])), 4),
        "geh_under_5_vs_recommended": round(sum(1 for g in geh_vals if g < 5.0) / len(geh_vals), 4),
        "rmspe_train": _win(TRAIN_WINDOWS),
        "rmspe_test": _win(TEST_WINDOWS),
        "segment_mean_kmh": [round(float(v) * 3.6, 2) for v in np.nanmean(seg, axis=0)],
        "segment_rel_error": [round(float(v), 4) for v in np.nanmean((seg - obs) / obs, axis=0)],
        "hourly_flow_mean_by_section": [
            round(float(v)) for v in counts.mean(axis=1) * 3600.0 / WINDOW_S
        ],
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument(
        "--keep-dir", type=Path, default=None, help="copy each variant's run dir here (no net)"
    )
    a = ap.parse_args()
    base = ScenarioConfig.model_validate(variant_config("as_is"))
    seed = spawn_seeds(base.seed, base.replicates)[0]
    with mp.get_context("spawn").Pool(min(a.procs, len(a.variants))) as pool:
        keep = None if a.keep_dir is None else str(a.keep_dir)
        if a.keep_dir is not None:
            a.keep_dir.mkdir(parents=True, exist_ok=True)
        rows = pool.map(_job, [(v, seed, keep) for v in a.variants])
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    result = {
        "schema_version": 1,
        "versions": _versions(),
        "arm": str(ARM_YAML.relative_to(REPO)),
        "arm_config_hash": config_hash(base),
        "seed": seed,
        "observed_segment_mean_kmh": [round(float(v) * 3.6, 2) for v in np.nanmean(obs, axis=0)],
        "variants": rows,
    }
    a.out.write_text(json.dumps(result, indent=1))
    for r in rows:
        oh = next((x for x in (r["ramps"] or []) if x["name"] == OH), None)
        print(
            f"  {r['variant']:<38} inserted={r['inserted_fraction']:.3f} rmspe all/train/test="
            f"{r['rmspe_all']:.3f}/{r['rmspe_train']:.3f}/{r['rmspe_test']:.3f} 15min={r['rmspe_15min']:.3f} "
            f"GEH<5={r['geh_under_5_vs_recommended']:.2f} "
            f"OH {oh['n_departed'] if oh else '-'}/{oh['n_planned'] if oh else '-'} "
            f"seg km/h: {' '.join(f'{v:.0f}' for v in r['segment_mean_kmh'])}"
        )
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
