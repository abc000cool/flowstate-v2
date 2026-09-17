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

``--by-lane`` answers a different question — the first, data-only half of
candidate 2.4 of docs/MERGE_ROUND6_PLAN.md, which asks whether a heavy
population *placed by lane and by origin* could reproduce the recording's
right-lane speed collapse that the uniformly-spread heavy arms did not. It
reports the same two shares per lane (1–4 mainline plus 5, the auxiliary
lane) and for the Old Hickory ramp lane's fragments, and writes a separate
artifact, ``artifacts/i24_heavy_by_lane.json``; it never touches the
corridor artifact above::

    uv run --no-sync python scripts/i24_heavy_share.py --by-lane

The two ramp-lane fragment sets and the decider are defined on
:func:`by_lane`; the share definitions and the coverage caveat are on
``calibration.lanechange.heavy_share``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S
from i24_data import REPO_ROOT, SAMPLE_DT_S, WB_DIR, data_hash

from calibration.lanechange import ClassShare, aux_lane_fragments, heavy_share

OUT = REPO_ROOT / "artifacts" / "i24_heavy_observed.json"
OUT_BY_LANE = REPO_ROOT / "artifacts" / "i24_heavy_by_lane.json"
COVERAGE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_coverage.json"
LANE_PROFILE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_lane_profile_zip.json"
HEAVY_CLASSES = (4, 5)
CLASS_LABELS = {0: "sedan", 1: "midsize", 2: "van", 3: "pickup", 4: "semi", 5: "truck"}
SPAN_M = (0.0, 5492.0)

#: Lanes reported by ``--by-lane``: 1–4 mainline (1 = leftmost/HOV) plus 5,
#: the first outside band, which carries the auxiliary/ramp lanes
#: (``calibration.loaders.i24motion``: lane 0 is the median shoulder, ≥ 5 the
#: outside shoulder and ramps, −1 a homography artifact). Bands 6–7 hold
#: 0.3% of the span's samples and are not a travelled lane; they are left out
#: with lanes ≤ 0, and the excluded counts are recorded in the artifact.
BY_LANE_LANES = (1, 2, 3, 4, 5)
MAINLINE_LANES = (1, 2, 3, 4)

#: Data-x span of the Old Hickory acceleration lane, taken from the
#: recording's own lane-5 occupancy (docs/I24_VALIDATION.md §0.5(d): present
#: at 0.75–1.95 km and again at 3.45–3.95 km, the Hickory Hollow
#: deceleration pocket). The second range and the lane-5 samples near the
#: Hickory Hollow on-ramp are deliberately outside the ramp rule.
OH_AUX_X_M = (750.0, 1950.0)

#: The window the ramp rule is applied on: everything from the upstream end
#: of the measured span to the end of the acceleration lane, so a mainline
#: fragment that drifts into the lane has its upstream samples present
#: (``calibration.lanechange.aux_lane_fragments``).
RAMP_RULE_X_M = (SPAN_M[0], OH_AUX_X_M[1])

#: Ratios of heavy to car tracking coverage used for the sensitivity block.
#: None is measured — the recording has no coverage-by-class estimate — so
#: they are labelled assumptions, not a correction.
COVERAGE_RATIOS = (1.0, 1.2, 1.5, 2.0)

#: 250 m bins of the merge zone whose observed lane speeds a lane-placed
#: heavy population would have to reproduce (docs/MERGE_ROUND6_PLAN.md §2.4).
MERGE_ZONE_BINS_M = (1000.0, 1250.0, 1500.0)

#: A per-lane share must differ from the corridor share by at least this
#: many percentage points for candidate 2.4 to survive
#: (docs/MERGE_ROUND6_PLAN.md §2.4, fixed before the measurement).
DECIDER_POINTS = 3.0


def _read(columns: list[str], extra: list[tuple[str, str, object]]) -> pd.DataFrame:
    """Study-period, in-span trajectory rows, one filtered read at a time."""
    return pq.read_table(
        WB_DIR / "trajectories.parquet",
        columns=columns,
        filters=[
            ("t", ">=", T_STUDY_LO_S),
            ("t", "<", T_STUDY_HI_S),
            *extra,
        ],
    ).to_pandas()


def _true_share(observed: float, ratio: float) -> float:
    """Share implied by an observed share under a heavy/car coverage ratio.

    With every heavy vehicle tracked with probability ``c_h`` and every car
    with ``c_c``, an observed share ``s_o`` of a true share ``s`` satisfies
    ``s_o = s·r / (s·r + (1 − s))`` with ``r = c_h/c_c``; inverted,
    ``s = s_o / (r − s_o·(r − 1))``. No ``r`` is measured for this recording
    (:func:`by_lane`), so this is a sensitivity, not a correction.
    """
    return observed / (ratio - observed * (ratio - 1.0))


def _lane_coverage() -> dict[str, dict[str, list[float] | None]]:
    """Per-lane tracking coverage over the study-period windows, lanes 1–5.

    Both estimators of ``artifacts/i24_coverage.json`` are carried as
    ``[min, max]`` over the eight windows: ``gap_mixture``, the per-lane
    number docs/I24_DATA.md quotes, and ``recommended``, the one the
    corridor demand is corrected with. Lane 5 has never been estimated
    (docs/MERGE_ROUND6_PLAN.md §2.3) and is None.
    """
    cov = json.loads(COVERAGE_ARTIFACT.read_text())
    out: dict[str, dict[str, list[float] | None]] = {}
    for estimator in ("gap_mixture", "recommended"):
        acc: dict[int, list[float]] = {}
        for window in cov["windows"]:
            if not window["in_study_period"]:
                continue
            for lane in window["lanes"]:
                value = lane["estimators"].get(estimator)
                if value is not None:
                    acc.setdefault(int(lane["lane"]), []).append(float(value))
        out[estimator] = {
            str(k): [round(min(v), 3), round(max(v), 3)] for k, v in sorted(acc.items())
        }
        out[estimator].setdefault("5", None)
    return out


def _band_samples() -> dict[str, int]:
    """Samples per lateral band over the window and span, every band."""
    df = _read(["lane"], [("x", ">=", SPAN_M[0]), ("x", "<", SPAN_M[1])])
    return {str(int(k)): int(v) for k, v in sorted(df["lane"].value_counts().items())}


def _observed_lane_speeds() -> dict[str, Any]:
    """The recording's lane speeds through the merge zone, as committed.

    Copied from ``artifacts/i24_lane_profile_zip.json`` (observed rows, the
    250 m bins from 1.0 to 1.5 km) so this artifact carries the target the
    plan's simulation half is scored against: the right lane gives up a
    third of its speed there while lanes 1–3 hold 27–33 km/h
    (docs/MERGE_ROUND6_PLAN.md §2.4).
    """
    rows = json.loads(LANE_PROFILE_ARTIFACT.read_text())["observed"]["rows"]
    return {
        "source": f"artifacts/{LANE_PROFILE_ARTIFACT.name} (observed rows)",
        "bins_x_lo_m": MERGE_ZONE_BINS_M,
        "speed_kmh": {
            str(int(r["x_lo_m"])): r["speed_kmh"] for r in rows if r["x_lo_m"] in MERGE_ZONE_BINS_M
        },
        "vehicle_time_share": {
            str(int(r["x_lo_m"])): r["share"] for r in rows if r["x_lo_m"] in MERGE_ZONE_BINS_M
        },
    }


def _share_record(share: ClassShare, corridor: ClassShare) -> dict[str, Any]:
    """A :class:`ClassShare` plus its distance from the corridor share."""
    record = share.to_dict()
    record["by_class"] = {
        CLASS_LABELS.get(c, str(c)): {
            "fragments": share.fragments_by_class.get(c, 0),
            "share_fragments": round(share.fragments_by_class.get(c, 0) / share.n_fragments, 4)
            if share.n_fragments
            else None,
            "share_vehicle_time": round(share.samples_by_class.get(c, 0) / share.n_samples, 4)
            if share.n_samples
            else None,
            # Vehicle-time per fragment: the measured class-dependent piece
            # of the tracking, and why the two shares differ (see the
            # artifact's fragment_length_note).
            "mean_tracked_s": round(
                SAMPLE_DT_S
                * share.samples_by_class.get(c, 0)
                / max(share.fragments_by_class.get(c, 0), 1),
                2,
            )
            if share.fragments_by_class.get(c, 0)
            else None,
        }
        for c in sorted(set(share.fragments_by_class) | set(share.samples_by_class))
    }
    for key in ("share_fragments", "share_vehicle_time"):
        mine, base = record[key], getattr(corridor, key)
        record[key] = None if mine is None else round(mine, 4)
        record[f"{key}_minus_corridor_points"] = (
            None if mine is None else round(100.0 * (mine - base), 2)
        )
    record["sensitivity_true_share_fragments"] = {
        f"r={r:g}": round(_true_share(share.share_fragments, r), 4) for r in COVERAGE_RATIOS
    }
    return record


def by_lane() -> dict[str, Any]:
    """Heavy share per lane and for the Old Hickory ramp lane's fragments.

    Candidate 2.4 of docs/MERGE_ROUND6_PLAN.md asks whether the heavy
    population should be *placed* — the arms that carry it spread it over
    every lane and moved neither failing acceptance row — and fixes the
    decider before the measurement: the candidate is **excluded** if every
    per-lane and ramp-origin share is within :data:`DECIDER_POINTS`
    percentage points of the corridor share, because the uniform arms have
    then already answered it.

    Definitions (shares: ``calibration.lanechange.heavy_share``):

    * **Per lane.** A fragment counts in every lane it has a sample in, so a
      fragment that changes lane is counted more than once and the per-lane
      fragment counts sum above the corridor total; the *share* inside a
      lane is unaffected. Vehicle-time is that lane's samples only.
    * **Ramp-lane fragments**, two nested sets over
      :data:`OH_AUX_X_M`, the recording's own lane-5 occupancy at the Old
      Hickory acceleration lane: ``on_aux`` is every fragment with at least
      one sample on lane 5 inside that span, and ``aux_origin`` those whose
      *first* sample anywhere in :data:`RAMP_RULE_X_M` is itself on lane 5
      inside the span — the fragment begins in the acceleration lane instead
      of arriving there from a mainline lane. I-24 MOTION documents are
      fragments, not trips (median 117 m, 6 s), so ``aux_origin`` is a proxy
      for ramp origin and ``on_aux`` its wider bound; both are stated in the
      artifact. Their vehicle-time is counted over the whole span, not only
      over the rule's window.

    Coverage (docs/I24_DATA.md §4): every count here is a lower bound at the
    local tracking rate, and the per-lane rates differ (lane 1 tracks at
    0.70–0.76, lane 3 at 0.40–0.54). A share *within* a lane is invariant to
    a thinning that is uniform inside that lane, so the per-lane rates do
    not bias the numbers the decider reads; what would bias them is a
    coverage that differs *by class* inside a lane, and no estimate of that
    exists for this recording (``artifacts/i24_coverage.json`` estimates
    coverage per lane and per speed class, never per vehicle class), so it
    cannot be corrected. The known direction — heavy vehicles are taller and
    easier to track, so every share here is if anything high — is carried
    with a sensitivity block over assumed heavy/car coverage ratios
    (:func:`_true_share`), which is monotone and therefore shrinks the
    lane-to-corridor gaps without changing their sign.

    Returns:
        The artifact dict written to ``artifacts/i24_heavy_by_lane.json``.
    """
    per_lane: dict[str, ClassShare] = {}
    ids_by_cls: dict[int, set[str]] = {}
    for lane in BY_LANE_LANES:
        df = _read(
            ["veh_id", "cls"],
            [("x", ">=", SPAN_M[0]), ("x", "<", SPAN_M[1]), ("lane", "==", lane)],
        )
        per_lane[str(lane)] = heavy_share(df, heavy_classes=HEAVY_CLASSES)
        if lane in MAINLINE_LANES:
            # A fragment can appear in several lanes, so the corridor totals
            # are unions over lanes, not sums (that is what reproduces the
            # committed artifact's 288,827).
            for cls_code, group in df.groupby("cls", sort=False)["veh_id"]:
                ids_by_cls.setdefault(int(cls_code), set()).update(group.unique().tolist())
        del df
    mainline_ids = set().union(*ids_by_cls.values())
    heavy_ids = set().union(*(ids_by_cls.get(c, set()) for c in HEAVY_CLASSES))
    n_samples = sum(per_lane[str(x)].n_samples for x in MAINLINE_LANES)
    n_heavy_samples = sum(per_lane[str(x)].n_heavy_samples for x in MAINLINE_LANES)
    corridor = ClassShare(
        n_samples=n_samples,
        n_fragments=len(mainline_ids),
        n_heavy_samples=n_heavy_samples,
        n_heavy_fragments=len(heavy_ids),
        share_fragments=len(heavy_ids) / len(mainline_ids),
        share_vehicle_time=n_heavy_samples / n_samples,
        fragments_by_class={c: len(v) for c, v in sorted(ids_by_cls.items())},
        samples_by_class={
            c: sum(per_lane[str(x)].samples_by_class.get(c, 0) for x in MAINLINE_LANES)
            for c in sorted(ids_by_cls)
        },
        n_fragments_mixed_class=sum(len(v) for v in ids_by_cls.values()) - len(mainline_ids),
    )
    del mainline_ids, heavy_ids, ids_by_cls

    ramp_df = _read(
        ["t", "veh_id", "x", "lane"],
        [
            ("x", ">=", RAMP_RULE_X_M[0]),
            ("x", "<", RAMP_RULE_X_M[1]),
            ("lane", ">=", min(BY_LANE_LANES)),
            ("lane", "<=", max(BY_LANE_LANES)),
        ],
    )
    sets = aux_lane_fragments(ramp_df, aux_lane=5, x_range_m=OH_AUX_X_M)
    del ramp_df
    ramp: dict[str, ClassShare] = {}
    for name, ids in (("on_aux", sets.on_aux), ("aux_origin", sets.aux_origin)):
        df = _read(
            ["veh_id", "cls"],
            [
                ("x", ">=", SPAN_M[0]),
                ("x", "<", SPAN_M[1]),
                ("lane", ">=", min(BY_LANE_LANES)),
                ("lane", "<=", max(BY_LANE_LANES)),
                ("veh_id", "in", set(ids)),
            ],
        )
        ramp[name] = heavy_share(df, heavy_classes=HEAVY_CLASSES)
        del df

    rows = {
        **{f"lane_{k}": v for k, v in per_lane.items()},
        **{f"ramp_{k}": v for k, v in ramp.items()},
    }
    gaps = {
        name: max(
            abs(100.0 * (share.share_fragments - corridor.share_fragments)),
            abs(100.0 * (share.share_vehicle_time - corridor.share_vehicle_time)),
        )
        for name, share in rows.items()
    }
    worst = max(gaps, key=lambda k: gaps[k])
    excluded = gaps[worst] < DECIDER_POINTS
    committed = json.loads(OUT.read_text())
    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "script": "scripts/i24_heavy_share.py --by-lane",
        "data_hash": data_hash(),
        "source": str(WB_DIR.relative_to(REPO_ROOT) / "trajectories.parquet"),
        "period": "06:30-08:30 CST",
        "span_data_x_m": list(SPAN_M),
        "lanes": list(BY_LANE_LANES),
        "heavy_classes": list(HEAVY_CLASSES),
        "lane_convention": (
            "1 = leftmost (HOV) ... 4 = rightmost mainline, 5 = first outside band, which "
            "pools every auxiliary section along the span (the Old Hickory acceleration lane at "
            "0.75-1.95 km, the Hickory Hollow deceleration pocket at 3.45-3.95 km and the "
            "Hickory Hollow on-ramp / Bell Road lanes beyond 4.5 km); the ramp block below "
            "isolates the Old Hickory one. Bands <= 0 (median shoulder, homography artifact) "
            "and 6-7 (outside shoulder, 0.3% of the span's samples) are excluded; "
            "samples_by_band records all of them"
        ),
        "share_convention": (
            "share_fragments = distinct heavy veh_id / distinct veh_id in the set (a fragment "
            "counts once per lane it has a sample in, so the per-lane fragment counts sum above "
            "the corridor total); share_vehicle_time = heavy 5 Hz samples / samples in the set. "
            "Both are ratios of coverage-limited counts (docs/I24_DATA.md §4)"
        ),
        "ramp_fragment_rule": {
            "aux_lane": sets.aux_lane,
            "aux_x_range_m": list(sets.x_range_m),
            "applied_over_x_range_m": list(RAMP_RULE_X_M),
            "on_aux": (
                "at least one sample on lane 5 with x in the acceleration lane's span "
                "[750, 1950) m, the recording's own lane-5 occupancy there "
                "(docs/I24_VALIDATION.md §0.5(d))"
            ),
            "aux_origin": (
                "on_aux and the fragment's first sample anywhere in x in [0, 1950) m is itself "
                "on lane 5 inside that span, i.e. the fragment begins in the acceleration lane "
                "rather than arriving from a mainline lane"
            ),
            "caveat": (
                "I-24 MOTION documents are fragments, not trips (median 117 m, 6 s; "
                "docs/I24_DATA.md §2), so aux_origin is a proxy for ramp origin - a mainline "
                "vehicle whose track breaks inside the lane satisfies it too - and on_aux is "
                "the wider bound"
            ),
            "n_on_aux": len(sets.on_aux),
            "n_aux_origin": len(sets.aux_origin),
        },
        "corridor": {
            **_share_record(corridor, corridor),
            "lanes": list(MAINLINE_LANES),
            "committed_artifact": "artifacts/i24_heavy_observed.json",
            "committed_n_fragments": committed["n_fragments"],
            "committed_share_fragments": committed["fraction_fragments"],
            "committed_share_vehicle_time": committed["fraction_vehicle_time"],
            "reproduces_committed": (
                corridor.n_fragments == committed["n_fragments"]
                and round(corridor.share_fragments, 4) == committed["fraction_fragments"]
                and round(corridor.share_vehicle_time, 4) == committed["fraction_vehicle_time"]
            ),
        },
        "by_lane": {k: _share_record(v, corridor) for k, v in per_lane.items()},
        "ramp": {k: _share_record(v, corridor) for k, v in ramp.items()},
        "samples_by_band": _band_samples(),
        "lane_tracking_coverage": _lane_coverage(),
        "observed_lane_speed_context": _observed_lane_speeds(),
        "coverage_note": (
            "lane_tracking_coverage gives [min, max] over the eight study-period windows of "
            "artifacts/i24_coverage.json's per-lane estimators; lane 5's has never been "
            "estimated (docs/MERGE_ROUND6_PLAN.md §2.3). A share INSIDE one lane is invariant "
            "to a thinning that is uniform within that lane, so the per-lane rates - which "
            "differ by 30 points between lane 1 and lane 3 - do not bias the shares below; what "
            "would bias them is a coverage that differs BY CLASS inside a lane, and no "
            "coverage-by-class estimate exists for this recording (i24_coverage.json estimates "
            "per lane and per SPEED class, never per vehicle class), so it cannot be corrected. "
            "The direction is known - heavy vehicles are taller and easier to track, so every "
            "share here is if anything high - and sensitivity_true_share_fragments inverts an "
            "ASSUMED heavy/car coverage ratio r (s = s_obs / (r - s_obs*(r-1))). That map is "
            "monotone and applies to every row alike, so it shrinks the lane-to-corridor gaps "
            "without changing their sign or order: it is a sensitivity, not a correction"
        ),
        "fragment_length_note": (
            "the one class-dependent tracking statistic that IS measured here: mean_tracked_s "
            "per class. Corridor-wide a semi fragment lasts "
            f"{SAMPLE_DT_S * corridor.samples_by_class.get(4, 0) / max(corridor.fragments_by_class.get(4, 1), 1):.1f} s "
            "against a sedan's "
            f"{SAMPLE_DT_S * corridor.samples_by_class.get(0, 0) / max(corridor.fragments_by_class.get(0, 1), 1):.1f} s, "
            "which is why share_fragments is ~1.7x share_vehicle_time everywhere. It also means "
            "the two shares carry the tracking bias differently: shorter fragments per vehicle "
            "inflate a heavy share of FRAGMENTS (more documents per truck) while the share of "
            "VEHICLE-TIME is unaffected by where a track breaks. share_vehicle_time is "
            "therefore the more robust of the two for reading the lane contrast, and "
            "share_fragments the quantity a fleet-composition input takes"
        ),
        "decider": {
            "source": "docs/MERGE_ROUND6_PLAN.md §2.4",
            "rule": (
                "excluded if every per-lane and ramp-origin share is within "
                f"{DECIDER_POINTS:g} percentage points of the corridor share "
                "(on either share); the threshold was fixed before the measurement"
            ),
            "max_gap_points": {k: round(v, 2) for k, v in sorted(gaps.items())},
            "largest_gap_row": worst,
            "largest_gap_points": round(gaps[worst], 2),
            "excluded": excluded,
            "verdict": (
                "excluded: the uniform heavy arms already answered it"
                if excluded
                else "proceed to a lane-placed heavy population (simulation half of 2.4)"
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-lane",
        action="store_true",
        help="report the share per lane and for the Old Hickory ramp lane's fragments, "
        f"writing {OUT_BY_LANE.relative_to(REPO_ROOT)} (leaves {OUT.relative_to(REPO_ROOT)} alone)",
    )
    args = parser.parse_args()
    if args.by_lane:
        out = by_lane()
        OUT_BY_LANE.write_text(json.dumps(out, indent=2))
        head = ["lane/set  frag%  d_pts   vtime%  d_pts   n_frag"]
        for key, rec in [
            *((f"lane {k}", v) for k, v in out["by_lane"].items()),
            *((f"ramp {k}", v) for k, v in out["ramp"].items()),
        ]:
            head.append(
                f"{key:9s} {100 * rec['share_fragments']:5.1f}"
                f" {rec['share_fragments_minus_corridor_points']:+6.1f}"
                f"  {100 * rec['share_vehicle_time']:6.1f}"
                f" {rec['share_vehicle_time_minus_corridor_points']:+6.1f}"
                f"  {rec['n_fragments']:7d}"
            )
        print("\n".join(head))
        print(json.dumps(out["decider"], indent=2))
        print(f"-> {OUT_BY_LANE}")
        return
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
