# Archived run summaries

Machine-readable result files that earlier experiments wrote next to their
(gitignored, regenerable) run trees under `runs/<experiment>/`. The run trees
themselves (12 GB of per-replicate trajectories) were deleted on 2026-09-02 to
make room for the I-24 MOTION processing (docs/ROADMAP.md §6 item 1); these
summaries are what the results documents cite, so they are kept here verbatim,
one directory per experiment, at the same relative paths:

| Experiment | Files | Cited by |
|---|---|---|
| `m3_us101/` | `results_no_boundary.json`, `results_with_boundary.json`, `observed_us101.json`, `us101_replica_with_boundary.yaml` | docs/M3_US101_VALIDATION.md, docs/reports/us101_replica/ |
| `m3_fluxcap/` | `results.json`, `fd_corridor10k_micro.json` | docs/M3_US101_VALIDATION.md §5 |
| `m3_sweep/` | `analysis.json`, `MANIFEST.json` | docs/M3_RESULTS.md, artifacts/m3_sweep_summary.json |
| `pi_retune/` | `analysis.json`, `MANIFEST.json` | docs/PI_CONTROLLER_FIX.md |
| `jad_oracle/` | `analysis.json`, `MANIFEST.json` | docs/JAD_ORACLE_RESULTS.md |
| `us101_penetration/` | `analysis.json`, `MANIFEST.json` | docs/US101_PENETRATION.md |

Re-running the driver scripts named in each document regenerates the run
trees and rewrites these files under `runs/`; the copies here are the
as-published versions. `scripts/us101_penetration_sweep.py` falls back to
re-deriving the boundary schedule from the NGSIM chunks when
`runs/m3_us101/us101_replica_with_boundary.yaml` is absent, or the archived
copy can be placed there.
