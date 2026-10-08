# data/osm

`i24_motion.osm`: OpenStreetMap extract of the I-24 westbound corridor at the
I-24 MOTION testbed (Nashville, mile markers ~58.7–62.7), used by
`scenarios/i24_replica*.yaml` and the gallery scenarios. Versioned because it
is small (89 KB) and the replica is not reproducible without it.

Map data © OpenStreetMap contributors, licensed under the Open Database
License (ODbL), https://www.openstreetmap.org/copyright.

`mndot_i94_wb_stpaul.osm` (2026-09-23): Overpass extract (motorway and
motorway_link ways only) of I-94 westbound through east St. Paul, bounding box
44.9425,-93.0990 to 44.9613,-92.9612, produced by
`scripts/onboard_corridor.py` for `scenarios/mndot_i94_wb_stpaul.yaml`; the
same licence applies.

- `i80_ngsim.osm` (2026-10-08): not an OpenStreetMap extract; a hand-built map of the NGSIM I-80 study site (Emeryville, CA; Powell St on-ramp) written by `scripts/i80_build_replica.py` from `artifacts/i80_replica_inputs.json` (geometry read off the trajectory data, corrected so netconvert compiles the measured lengths; sha256 checked by `write-osm`). No ODbL data.
