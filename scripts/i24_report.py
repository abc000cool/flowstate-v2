"""I-24 auto-report driver: FHWA-style report per validation arm (§7.4).

Feeds each 20-replicate ``i24_replica`` run set produced by
``scripts/i24_validate.py`` through the product report generator
``validation.report.generate_report``: provenance (config hash, seeds,
versions, calibration artifacts), the acceptance-criteria table with its
honest pass/fail mix, replicate metric CIs on the measured span, and
per-replicate speed-contour figures. GEH and RMSPE inputs come from
``artifacts/i24_validation_<arm>.json`` so every number in the report traces
to the computed comparison artifacts (CLAUDE.md §7.4 — no free-text numbers);
the GEH row uses the table the arm is scored on (tracked counts for the
tracked arm, coverage-corrected counts for the corrected arm — both are in the
artifact). The ring benchmarks are CI-gated integration tests and enter as
not-evaluated rows (never a pass, CLAUDE.md §0.1).

Output: ``docs/reports/i24_replica/<arm>/report.md`` + figures alongside.

Usage (repo root)::

    uv run --no-sync python scripts/i24_report.py [--arms both|tracked|corrected]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validation.report import generate_report

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = REPO_ROOT / "runs" / "i24_validation"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
TITLES = {
    "tracked": "demand as tracked (lower bound at the instrument's coverage)",
    "corrected": "demand divided by the apparent tracking coverage",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--arms", choices=("both", "tracked", "corrected"), default="both")
    args = ap.parse_args()
    inputs = json.loads(INPUTS.read_text())
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    lo, hi = inputs["geometry"]["measured_span_data_x_m"]
    for arm in ("tracked", "corrected"):
        if args.arms not in ("both", arm):
            continue
        results = json.loads((REPO_ROOT / "artifacts" / f"i24_validation_{arm}.json").read_text())
        assert results["arm"] == arm
        geh_key = "vs_coverage_corrected_counts" if arm == "corrected" else "vs_tracked_counts"
        run_set = RUN_ROOT / arm / results["config_hash"]
        out = generate_report(
            run_set,
            REPO_ROOT / "docs" / "reports" / "i24_replica" / arm / "report.md",
            geh_values=results["geh"][geh_key]["values"],
            rmspe_value=results["rmspe"]["value"],
            ring_emergence=None,  # CI-gated; not re-run here (honest not-evaluated)
            ring_dampening=None,
            title=(
                f"FlowState v2 — i24_replica validation report, {arm} arm: {TITLES[arm]} "
                "(I-24 MOTION westbound, 30 Nov 2022 06:30-08:30 CST, measured downstream boundary)"
            ),
            created_at=results["created_at"],
            x_ref=a + b * 2200.0,
            span=(a + b * lo, a + b * hi),
        )
        print(f"[{arm}] report -> {out}")


if __name__ == "__main__":
    main()
