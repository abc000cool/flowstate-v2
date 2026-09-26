# data/

What is tracked here, where it came from, and on what terms. The raw datasets
are not in the repository (`.gitignore`); only small derived inputs are.

| Path | Content | Source and terms |
|---|---|---|
| `osm/*.osm` | OpenStreetMap extracts of the corridors (I-24 Nashville, US-75 Dallas, MnDOT I-94 WB St. Paul) | © OpenStreetMap contributors, Open Database License (ODbL); see `osm/README.md` |
| `mndot/mndot_i94_wb_stpaul/` | station table, tidy 5-min detector frame, observations and demand inputs for the third corridor | Minnesota DOT Regional Transportation Management Center, public 30-second loop-detector archive (Mayfly API) and IRIS metro configuration; public data, no registration. `mndot/config/metro_config.xml.gz` is the inventory snapshot used |
| `mndot/cache/` (untracked) | raw per-detector 30-s JSON responses | same source; re-fetched by `scripts/mndot_fetch.py` |
| `fetch/` | fetch scripts for the datasets that stay out of the tree | — |
| `i24motion/` (untracked) | I-24 MOTION trajectory data | i24motion.org; obtained by the maintainer under the dataset's own terms and never committed. The calibration artifacts under `artifacts/` (IDM population fits, capacity and demand fits, validation records) are derived summary statistics computed from it and cite it |
| `ngsim/` (untracked) | raw NGSIM US-101 vehicle trajectories (not the Montanino & Punzo reconstruction; corrected 2026-09-25) | data.transportation.gov, Socrata resource `8ect-6jqj`, `location='us-101'`, exported in 200k-row chunks (`scripts/us101_data.py`); never committed |
| `processed/` (untracked) | local intermediates | — |

The repository's own code and documents are under the Apache License 2.0
(`LICENSE` at the root). Nothing under `data/` or `artifacts/` contains a
credential, an API key or personal data.
