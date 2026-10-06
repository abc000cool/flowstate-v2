"""Write the measured merge model's parameter artifact from the committed artifacts.

docs/MERGE_MODEL.md §2 fixes, before any run, where every value of
``RampSpec.merge = "measured"`` comes from; this script reads those values
out of the committed artifacts — never typed by hand — and writes
``artifacts/merge_model_params.json``, the file the runner reads
(``microsim.merge_model.load_params``; its path and sha256 go into every
run's ``meta.json["measured_merge_model"]``). Values are global and never
tuned per corridor (protocol §7.4).

Every value carries its provenance: the source artifact's path, its sha256 at
the time of writing and the JSON path inside it. Every value the
specification quotes rounded is checked against the artifact (``spec_checks``:
the artifact value must round to the quoted one), so the artifact and the
specification cannot drift apart silently.

Sets (docs/MERGE_MODEL.md §2, "Pre-registered sensitivity arms"; B§5.11):

* ``central`` — critical gaps from the I-24 joint Troutbeck fits
  (``artifacts/i24_critical_gaps.json``: Old Hickory acceleration lane,
  entering, for acceleration lanes; the Hickory Hollow–Bell Road weave,
  entering and exiting, for weaving sections; the exiting movement has no lead
  time gate); δ from the I-24 partner speeds at the change
  (``artifacts/lane_change_relaxation_i24.json``); τ_r from the US-101
  leader-side fit (``artifacts/lane_change_relaxation_us101.json``);
* ``us101_gaps`` — the critical-gap medians of complete-coverage NGSIM US-101
  (``artifacts/coverage_thinning_us101.json`` ``results[...].reference``:
  entering 0.29 / 0.45 s for both zone kinds, exiting lag 0.54 s); the
  artifact holds medians only, so each distribution keeps the central set's σ
  (stated in the set's ``note``);
* ``delta_zero`` — δ = 0 for both zone kinds;
* ``tau_r_low`` / ``tau_r_high`` — τ_r at the ends of its 95 % interval.

Amendment A1.1 (2026-10-06) added an entering speed condition to every set;
amendment A2.1 (the same day, docs/MERGE_MODEL.md "Amendments") withdrew it
from the model, and it is no longer written here.

Run (from the repository root; reads only committed artifacts)::

    uv run --no-sync python scripts/merge_model_params.py
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "merge_model_params.json"
CRITICAL_GAPS = REPO / "artifacts" / "i24_critical_gaps.json"
RELAX_I24 = REPO / "artifacts" / "lane_change_relaxation_i24.json"
RELAX_US101 = REPO / "artifacts" / "lane_change_relaxation_us101.json"
THINNING_US101 = REPO / "artifacts" / "coverage_thinning_us101.json"

#: The values docs/MERGE_MODEL.md §2 quotes, with the precision it quotes them
#: at (decimals): each must equal the artifact's value rounded so.
SPEC_VALUES: dict[str, tuple[float, int]] = {
    "central.entering_merge.lead.median_s": (0.42, 2),
    "central.entering_merge.lead.sigma": (1.42, 2),
    "central.entering_merge.lag.median_s": (0.59, 2),
    "central.entering_merge.lag.sigma": (1.46, 2),
    "central.entering_weave.lead.median_s": (0.46, 2),
    "central.entering_weave.lead.sigma": (1.44, 2),
    "central.entering_weave.lag.median_s": (0.92, 2),
    "central.entering_weave.lag.sigma": (1.28, 2),
    "central.exiting_weave.lag.median_s": (1.11, 2),
    "central.exiting_weave.lag.sigma": (1.90, 2),
    "central.delta.merge": (0.6, 1),
    "central.delta.weave": (1.0, 1),
    "central.tau_r_s": (7.5, 1),
    "tau_r_low.tau_r_s": (5.2, 1),
    "tau_r_high.tau_r_s": (15.9, 1),
    "us101_gaps.entering_merge.lead.median_s": (0.29, 2),
    "us101_gaps.entering_merge.lag.median_s": (0.45, 2),
    "us101_gaps.exiting_weave.lag.median_s": (0.54, 2),
}

#: The relaxation floor (``0.5 · T_i``) and the restore point (4 τ_r) are
#: decisions of docs/MERGE_MODEL.md §2, not artifact values.
RELAX_FLOOR_FRACTION = 0.5
RELAX_RESTORE_TAU_R = 4.0
#: Truncation of every draw (B§5.3): [p2.5, p97.5].
TRUNCATION = (0.025, 0.975)


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(REPO))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


class Source:
    """A committed artifact, read once, with its provenance."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = json.loads(path.read_text())
        self.sha256 = _sha256(path)

    def prov(self, json_path: str) -> dict[str, str]:
        return {"artifact": _rel(self.path), "sha256": self.sha256, "json_path": json_path}


def _fit(src: Source, zone: str, movement: str) -> tuple[int, dict[str, Any]]:
    """The joint fit of one zone and movement (speed class ``all``) and its index."""
    for i, f in enumerate(src.data["fits"]):
        if f["zone"] == zone and f["movement"] == movement and f["speed_class"] == "all":
            if not f["joint"].get("fitted"):
                raise SystemExit(f"{src.path}: {zone}/{movement} is not fitted")
            return i, f
    raise SystemExit(f"{src.path}: no fit for {zone}/{movement}")


def _lognormal(src: Source, zone: str, movement: str, side: str) -> dict[str, Any]:
    i, f = _fit(src, zone, movement)
    d = f["joint"][side]
    if d.get("at_bound") or d.get("degenerate"):
        raise SystemExit(f"{src.path}: {zone}/{movement}/{side} is at a bound or degenerate")
    return {
        "mu": float(d["mu"]),
        "sigma": float(d["sigma"]),
        "median_s": math.exp(float(d["mu"])),
        "median_ci95_s": list(d["ci95"]["median_s"]),
        "n_drivers": int(f["n_drivers"]),
        "n_inconsistent": int(f["joint"]["n_inconsistent"]),
        "provenance": src.prov(f"fits[{i}].joint.{side}"),
    }


def _summary_row(src: Source, zone_kind: str, movement: str, side: str) -> tuple[int, dict]:
    for i, r in enumerate(src.data["summary_by_zone_kind"]):
        if (
            r["zone_kind"] == zone_kind
            and r["movement"] == movement
            and r["side"] == side
            and r["speed_class"] == "all"
        ):
            return i, r
    raise SystemExit(f"{src.path}: no summary row {zone_kind}/{movement}/{side}")


def _delta(src: Source, zone_kind: str) -> dict[str, Any]:
    """δ: the mean of the entrant's offsets over its new follower and under its new leader.

    ``rel_speed_ms`` is the front vehicle's speed minus the rear one's at the
    change (offset 0): on the follower side the entrant's over the follower,
    on the leader side the leader's over the entrant. The entrant crosses
    faster than both (B§1.3); δ, the offset over the chosen gap's speed, is
    their mean.
    """
    i_f, foll = _summary_row(src, zone_kind, "entering", "follower")
    i_l, lead = _summary_row(src, zone_kind, "entering", "leader")
    over_follower = float(foll["rel_speed_ms"]["p50"][0])
    under_leader = -float(lead["rel_speed_ms"]["p50"][0])
    return {
        "value": (over_follower + under_leader) / 2.0,
        "entrant_minus_follower_ms": over_follower,
        "entrant_minus_leader_ms": under_leader,
        "n": [int(foll["rel_speed_ms"]["n"][0]), int(lead["rel_speed_ms"]["n"][0])],
        "rule": "mean of the entrant's median speed over its new follower and over its new "
        "leader at the change (B§1.3; robust under coverage thinning, VM AE)",
        "provenance": [
            src.prov(f"summary_by_zone_kind[{i_f}].rel_speed_ms.p50[0]"),
            src.prov(f"summary_by_zone_kind[{i_l}].rel_speed_ms.p50[0]"),
        ],
    }


def _tau_r(src: Source) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """τ_r: the US-101 weave entering leader-side ``ratio_pop`` fit, and its interval's ends."""
    i, row = _summary_row(src, "weave", "entering", "leader")
    fit = row["fits"]["ratio_pop"]
    if not fit.get("supported"):
        raise SystemExit(f"{src.path}: the leader-side ratio_pop fit is not supported")
    path = f"summary_by_zone_kind[{i}].fits.ratio_pop"
    lo, hi = (float(v) for v in fit["tau_ci95"])
    central = {
        "value": float(fit["tau_s"]),
        "ci95": [lo, hi],
        "r0": float(fit["r0"]),
        "r0_ci95": list(fit["r0_ci95"]),
        "provenance": src.prov(f"{path}.tau_s"),
    }
    low = {"value": lo, "provenance": src.prov(f"{path}.tau_ci95[0]")}
    high = {"value": hi, "provenance": src.prov(f"{path}.tau_ci95[1]")}
    return central, low, high


def build() -> dict[str, Any]:
    gaps_src = Source(CRITICAL_GAPS)
    relax_i24 = Source(RELAX_I24)
    relax_us = Source(RELAX_US101)
    thin_us = Source(THINNING_US101)

    oh, weave = "OH_acceleration_lane", "HH_on_BR_off_weave"
    tau_c, tau_lo, tau_hi = _tau_r(relax_us)
    central: dict[str, Any] = {
        "note": "docs/MERGE_MODEL.md §2: the central values",
        "critical_gaps": {
            "entering_merge": {
                "lead": _lognormal(gaps_src, oh, "entering", "lead"),
                "lag": _lognormal(gaps_src, oh, "entering", "lag"),
            },
            "entering_weave": {
                "lead": _lognormal(gaps_src, weave, "entering", "lead"),
                "lag": _lognormal(gaps_src, weave, "entering", "lag"),
            },
            "exiting_weave": {
                # no lead time gate: the lead brake guard only (WP-79/80: the
                # I-24 lead 2.89 s made exiters wait and give up; US-101's
                # complete-coverage lead is 0.42 s; B§5.8)
                "lead": None,
                "lag": _lognormal(gaps_src, weave, "exiting", "lag"),
            },
        },
        "delta_ms": {"merge": _delta(relax_i24, "merge"), "weave": _delta(relax_i24, "weave")},
        "relaxation": {
            "tau_r_s": tau_c,
            "floor_fraction": {
                "value": RELAX_FLOOR_FRACTION,
                "provenance": "docs/MERGE_MODEL.md §2 (decision): T_eff >= max(step, 0.5 T_i); "
                "US-101 leader-side r0 0.54 [0.48, 0.61]",
            },
            "restore_after_tau_r": {
                "value": RELAX_RESTORE_TAU_R,
                "provenance": "docs/MERGE_MODEL.md §2 (decision): restored at 4 tau_r (98 %)",
            },
            "movement": "entering only (real exiters do not start short, B§1.4)",
        },
        "truncation": {
            "lower_quantile": TRUNCATION[0],
            "upper_quantile": TRUNCATION[1],
            "provenance": "B§5.3 (decision): [p2.5, p97.5] of each log-normal",
        },
    }

    def us101_median(key: str) -> tuple[float, dict[str, str]]:
        res = thin_us.data["results"][key]
        return float(res["reference"]), thin_us.prov(f"results['{key}'].reference")

    us101 = copy.deepcopy(central)
    us101["note"] = (
        "docs/MERGE_MODEL.md §2 sensitivity arm: US-101 critical gaps (complete coverage, raw "
        "NGSIM lane-6 weave). The artifact records medians only; each distribution keeps the "
        "central set's sigma for its movement and side"
    )
    for movement in ("entering_merge", "entering_weave"):
        for side, key in (
            ("lead", "cg.entering.all.joint.lead.median_s"),
            ("lag", "cg.entering.all.joint.lag.median_s"),
        ):
            med, prov = us101_median(key)
            d = us101["critical_gaps"][movement][side]
            d.update(
                {
                    "mu": math.log(med),
                    "median_s": med,
                    "median_ci95_s": None,
                    "provenance": prov,
                    "sigma_provenance": central["critical_gaps"][movement][side]["provenance"],
                }
            )
            d.pop("n_drivers", None)
            d.pop("n_inconsistent", None)
    med, prov = us101_median("cg.exiting.all.joint.lag.median_s")
    d = us101["critical_gaps"]["exiting_weave"]["lag"]
    d.update(
        {
            "mu": math.log(med),
            "median_s": med,
            "median_ci95_s": None,
            "provenance": prov,
            "sigma_provenance": central["critical_gaps"]["exiting_weave"]["lag"]["provenance"],
        }
    )
    d.pop("n_drivers", None)
    d.pop("n_inconsistent", None)

    delta0 = copy.deepcopy(central)
    delta0["note"] = "docs/MERGE_MODEL.md §2 sensitivity arm: delta = 0 (no speed offset)"
    for kind in ("merge", "weave"):
        delta0["delta_ms"][kind] = {
            "value": 0.0,
            "provenance": "docs/MERGE_MODEL.md §2 (pre-registered arm)",
        }

    tau_low = copy.deepcopy(central)
    tau_low["note"] = "docs/MERGE_MODEL.md §2 sensitivity arm: tau_r at its interval's low end"
    tau_low["relaxation"]["tau_r_s"] = tau_lo
    tau_high = copy.deepcopy(central)
    tau_high["note"] = "docs/MERGE_MODEL.md §2 sensitivity arm: tau_r at its interval's high end"
    tau_high["relaxation"]["tau_r_s"] = tau_hi

    sets = {
        "central": central,
        "us101_gaps": us101,
        "delta_zero": delta0,
        "tau_r_low": tau_low,
        "tau_r_high": tau_high,
    }

    def value(path: str) -> float:
        set_name, *rest = path.split(".")
        s = sets[set_name]
        if rest[0] == "delta":
            return float(s["delta_ms"][rest[1]]["value"])
        if rest[0] == "tau_r_s":
            return float(s["relaxation"]["tau_r_s"]["value"])
        movement, side, key = rest
        return float(s["critical_gaps"][movement][side][key])

    checks = []
    for path, (quoted, decimals) in SPEC_VALUES.items():
        actual = value(path)
        checks.append(
            {
                "value": path,
                "spec": quoted,
                "artifact": actual,
                "rounded": round(actual, decimals),
                "ok": round(actual, decimals) == quoted,
            }
        )
    bad = [c for c in checks if not c["ok"]]
    if bad:
        raise SystemExit(f"the artifacts disagree with docs/MERGE_MODEL.md §2: {bad}")

    return {
        "schema_version": 1,
        "kind": "merge_model_params",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/merge_model_params.py",
        "code": _git_head(),
        "spec": "docs/MERGE_MODEL.md §2 (decisions fixed 2026-10-05 before any run)",
        "inputs": [
            {"artifact": _rel(s.path), "sha256": s.sha256}
            for s in (gaps_src, relax_i24, relax_us, thin_us)
        ],
        "units": "critical gaps: log-normal of the bumper-to-bumper time gap [s] (lead over "
        "the changer's speed, lag over the follower's, calibration.lane_change_gaps); delta "
        "[m/s]; tau_r [s]",
        "amendments": [
            "A1.1 (2026-10-06) added speed_condition_ms to every set; A2.1 (2026-10-06) "
            "withdrew it from the model and from this artifact"
        ],
        "sets": sets,
        "spec_checks": checks,
        "limitations": [
            "I-24 critical gaps come from partial tracking (about 0.5-0.65 of peak vehicle-time); "
            "under coverage thinning of complete US-101 the critical gaps are undetermined "
            "(lead +0.10 s, lag +0.17 / +0.39 s at F = 0.65 / 0.5; B§1 coverage rules). The "
            "us101_gaps arm is the complete-coverage check.",
            "25-35 % of I-24 drivers are inconsistent with the one-threshold driver model the "
            "joint estimator assumes (WP-78); the fits are central values, not thresholds.",
            "tau_r is the only supported relaxation fit (176 US-101 leader sides); no follower "
            "side fit is supported in either dataset (B§1.4).",
            "delta rests on the entering partner speeds; the exiting movement uses the weave's "
            "delta (docs/MERGE_MODEL.md §2 gives one delta per zone kind).",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    art = build()
    args.out.write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {_rel(args.out)} ({len(art['sets'])} sets, {len(art['spec_checks'])} checks ok)")


if __name__ == "__main__":
    main()
