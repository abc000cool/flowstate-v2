# Corridor onboarding time-to-value (Track C4)

The product claim under test is "any corridor in under a day"
(NEXT_STEPS.md §3.3). This records what onboarding the I-24 westbound corridor
actually cost on 2026-09-02/03, measured on a 10-core / 16 GB laptop, split
into machine time (reproducible, from the scripts' own timers) and engineering
time (one person-session, from the session record). Nothing here is a
projection.

## Machine time, I-24 MOTION corridor

| Step | Script | Wall time | Notes |
|---|---|---|---|
| Stream 19.5 GB export → 5 Hz Parquet (westbound) | `scripts/i24_extract.py` | 309 s | single process |
| Day overview (speed field, coverage counts) | `scripts/i24_overview.py` | 23 s | |
| Episode extraction (17,652 episodes) | `scripts/i24_extract_episodes.py` | 95 s | |
| IDM population fit, all episodes | `scripts/fit_idm_i24.py` | 1,312 s | 8 processes |
| OSM extract (Overpass) + `netconvert` + landmark mapping | `scripts/i24_geometry.py` | ~60 s | network access |
| Replica build (demand, ramps, boundary, two scenario files) | `scripts/i24_build_replica.py` | ~90 s | |
| Validation, tracked arm, 20 replicates | `scripts/i24_validate.py` | 627 s runs + 184 s analysis | 4–8 processes |
| Validation, corrected arm, 20 replicates | `scripts/i24_validate.py` | 1,403 s runs + 443 s analysis | 4 processes |
| Auto-reports (2 arms) | `scripts/i24_report.py` | ~10 min each | single process |
| Triangular FD with 200 bootstrap refits | `scripts/fit_fd_i24.py` | ~3 h at 2 processes (not finished at the time of writing) | exact-LP quantile regression on 237k bins |

Total machine time to a validated-or-not criteria table for one corridor and
one day of data: **about 1.5 hours** excluding the FD bootstrap, on a laptop.

## Engineering time

The same corridor took one working session (roughly 8 hours of active work)
from "data on disk" to "criteria table in a document", and most of it was not
compute:

| Work | Approx. share | Reusable next time? |
|---|---|---|
| Reconciling the loader with the real export schema (fragments, back-center feet, `$oid`) | 1 h | yes — the loader now matches the v1.x documentation |
| Discovering and quantifying the tracking-coverage limitation | 1.5 h | yes — the coverage check is a function of the builder |
| Geometry: raw-way ids, landmark projection, chain/data scale | 1 h | yes — `scripts/i24_geometry.py` |
| Ramp modeling: schema, routes, connectivity checks, fixture | 2 h | yes — `RampSpec` is now a feature |
| SUMO lane-change defaults (diverge stall, keep-right) | 1 h | yes — `FleetSpec.lc_strategic`, `lc_keep_right` |
| Validation driver, figures, report, documents | 1.5 h | mostly — the I-24 driver is a template |

Everything in the "reusable" column was engineering the platform did not have
before this corridor; a second corridor with a similar data product should
cost the compute above plus the corridor-specific work: naming the mainline
ways and ramps, choosing count sections, and reading the data's coverage.
A defensible statement today is therefore: **a corridor with an existing
loader is a same-day job; a corridor with a new data product is a two-day
job**, and the honest bottleneck is understanding the data, not running the
model.

## What would shrink it

1. A ramp-discovery helper that lists on/off links attached to a chain with
   their positions (the logic exists in `scripts/i24_geometry.py`'s output but
   is read by eye).
2. A loader for radar/loop detector counts alongside trajectories, so demand
   does not have to be reconstructed from fragments.
3. Insertion capacity: the corridor could not insert the corrected demand
   through one entry edge (docs/I24_VALIDATION.md §3); a multi-edge or
   longer buffer is a runner change, not per-corridor work.
