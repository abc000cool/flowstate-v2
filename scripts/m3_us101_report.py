"""M3 auto-report driver: FHWA-style report for the US-101 replica (§7.4).

Feeds the 20-replicate WITH-BOUNDARY ``us101_replica`` run set (produced by
``scripts/m3_us101_validate.py``) through the product report generator
``validation.report.generate_report``: provenance (config hash, seeds,
versions, calibration artifacts), the acceptance-criteria table with its
honest pass/fail mix, replicate metric CIs, and per-replicate speed-contour
figures. GEH and RMSPE inputs come from ``runs/m3_us101/
results_with_boundary.json`` so every number in the report traces to the
computed comparison artifacts (CLAUDE.md §7.4 — no free-text numbers).

The ring benchmarks are CI-gated integration tests and are NOT re-run here;
they enter as not-evaluated rows (an unevaluated criterion is never a pass,
CLAUDE.md §0.1).

Output: ``docs/reports/us101_replica/report.md`` + figures alongside.

Usage (repo root)::

    uv run --no-sync python scripts/m3_us101_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

from validation.report import generate_report

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "runs" / "m3_us101" / "results_with_boundary.json"
RUN_SET = REPO_ROOT / "runs" / "m3_us101" / "micro_with_boundary"
OUT = REPO_ROOT / "docs" / "reports" / "us101_replica" / "report.md"

ENTRY_BUFFER_M = 640.0
SITE_LENGTH_M = 640.0


def main() -> None:
    results = json.loads(RESULTS.read_text())
    assert results["arm"] == "with_boundary"
    # Scope discovery to the exact configuration the results describe —
    # run trees may hold earlier smoke runs under other config hashes.
    run_set = RUN_SET / results["config_hash"]
    out = generate_report(
        run_set,
        OUT,
        geh_values=results["geh"]["values"],
        rmspe_value=results["rmspe"]["value"],
        ring_emergence=None,  # CI-gated; not re-run here (honest not-evaluated)
        ring_dampening=None,
        title=(
            "FlowState v2 — us101_replica validation report "
            "(NGSIM US-101 p1, measured downstream boundary)"
        ),
        created_at=results["created_at"],
        x_ref=ENTRY_BUFFER_M + 320.0,
        span=(ENTRY_BUFFER_M, ENTRY_BUFFER_M + SITE_LENGTH_M),
    )
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
