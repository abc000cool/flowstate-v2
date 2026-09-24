# Golden regressions

Summary statistics of fixed-seed runs (CLAUDE.md §9). CI re-runs each case
and compares against the stored values; a drift means the physics pipeline
changed, whether or not anyone meant it to.

| File | Producer | Case | Marker |
|---|---|---|---|
| `ring_sugiyama.json` | `microsim.run_micro` + `validation.metrics.compute_metrics` | `scenarios/ring_sugiyama.yaml` as is (600 sim-s, seed 42) | `integration` |
| `corridor_10km_smoke.json` | same | `scenarios/corridor_10km.yaml` with `sim.duration_s = 120` (the §9 2-min smoke run), seed 42 | `integration` |
| `macro_corridor.json` | `macrosim.run_macro` (`v1_legacy` FD) + `api.results.macro_metrics` | `golden_config()` in `tests/test_macrosim/test_macrosim_golden.py`: 10 km corridor, `corridor_10km` demand steps, seeded 120 s capacity drop at 7 km, seed 42 | none (pure numpy/numba, runs in CI) |
| `macro_corridor_workzone.json` | same | `workzone_config()` in `tests/test_macrosim/test_macrosim_golden.py`: the micro closure case at `tier: macro` — 3 km two-lane corridor, 1.4 veh/s, 20 % heavy population (not represented on this tier), right lane closed 2.0–2.5 km for 120–210 s (`seeded=True`), 300 sim-s, seed 11 | none (pure numpy/numba, runs in CI) |
| `corridor_10km_workzone.json` | same | `scenarios/corridor_10km_workzone.yaml` with `sim.duration_s = 600` (right lane closed 6.0–6.5 km from 300 s; `seeded=True`), seed 42 | `integration` |
| `corridor_boundary_schedule.json` | same | inline `_corridor_boundary_schedule()`: 1 km single-lane corridor, 0.35 veh/s, exit-edge speed schedule 15 → 2 (120 s) → 12 m/s (180 s), 240 sim-s, seed 42 — the in-loop `BoundarySpec` advance every I-24 replica scenario relies on | `integration` |
| `corridor_workzone_heavy.json` | same | inline `_corridor_workzone_heavy()`: 3 km two-lane corridor, 0.5 veh/s, 20 % heavy population (`HeavyVehicleSpec`), right lane closed 0.2–0.7 km for 120–210 s (`seeded=True`), 300 sim-s, seed 11 | `integration` |
| `hov_corridor.json` | same | inline `_hov_corridor()`: 3 km two-lane corridor, 0.5 veh/s, `hov_fraction = 0.3`, left lane managed (`ManagedLaneSpec`) 0.2–0.9 km for 120–210 s, 300 sim-s, seed 5 | `integration` |
| `merge_zipper.json` | same | inline `_merge_config()` on `tests/fixtures/merge.osm`: mainline 0.6 veh/s + on-ramp 0.25 veh/s, `RampSpec.merge = zipper` (netconvert patch), 200 sim-s, seed 3 | `integration` |
| `merge_scripted.json` | same | same interchange, `RampSpec.merge = scripted` (runner-driven acceleration lane), 300 sim-s, seed 3 | `integration` |
| `merge_meter_alinea.json` | same | same interchange, `merge = lane_change` under an ALINEA `RampMeterSpec` (240–600 veh/h, 30 s interval, stop line 40 m), 240 sim-s, seed 3 | `integration` |
| `merge_weave.json` | same | inline `_weave_config()` on `tests/fixtures/weave.osm` (an on-ramp whose auxiliary lane leaves as an exit ~150 m downstream; edge `102` compiles to 152 m): mainline 0.55 veh/s + on-ramp 0.2 veh/s for 140 s, 30 % exiting, `RampSpec.merge = weave` (runner-driven two-sided gap acceptance), 300 sim-s, seed 3 | `integration` |

Tests: `tests/test_microsim/test_microsim_golden.py`,
`tests/test_macrosim/test_macrosim_golden.py`.

The inline micro cases are defined in `CASES` of
`tests/test_microsim/test_microsim_golden.py` (one config factory each);
they pin the engine features the pilot scenarios use — a multi-step
downstream boundary schedule, lane closures, the heavy-vehicle population,
managed lanes and the on-ramp merge / metering models — which no versioned
scenario exercises. The merge cases run on the checked-in interchange
fixture `tests/fixtures/merge.osm` (a copy of the hand-written fixture in
`tests/test_microsim/test_microsim_merge_managed_meter.py`). Its path is
written repo-relative because `osm_file` is part of the hashed config: an
absolute path would make the config hash machine-specific. The golden test
and the regenerate CLI therefore run from the repository root, and editing
the fixture is a physics change that needs a regeneration and a PR note.

## What a golden holds

Summary statistics only — never trajectories or fields:

- the `validation.metrics.Metrics` fields (throughput, travel times, σ_v
  spatial/temporal, VMT/VHT, fuel per veh-km, wave count / speed /
  amplitude; NaN is stored as `null`);
- run counts (micro: vehicles planned/departed, SUMO collisions — every
  golden run has zero — heavy and HOV draws, ramp-meter releases, scripted
  merges completed/forced, weave changes in/out, forced and exits (weave
  case only), and the fuel total; macro: grid, FD, ledger,
  clamp flag, and — when the config declares closures — the closure and
  capped-cell counts, the open-capacity share and the inflow queue);
- provenance: `config` snapshot, `config_hash`, `seed` (and `sumo_seed`),
  `tier`, `seeded`, the `versions` dict the runner recorded (including
  `eclipse-sumo` / `libsumo` for the micro tier), the regeneration command
  and a timestamp.

## Tolerances

- Config hash, seed, tier, `seeded`, `eclipse-sumo` and `libsumo` versions:
  exact. A hash mismatch means the scenario YAML or the config schema no
  longer describes the golden experiment; a SUMO mismatch means the golden
  does not apply (goldens are per SUMO version; `eclipse-sumo==1.27.1` is
  pinned in `packages/microsim/pyproject.toml`). Both fail the test with a
  message pointing here — they are never skipped.
- Integer counts (vehicles, collisions, heavy/HOV draws, meter releases,
  scripted merges, waves, cells, closures and capped cells): exact.
- Floats, micro tier: relative `1e-6`. SUMO is deterministic per version and
  the artifacts are byte-identical across repeats
  (`test_microsim_determinism.py`); the tolerance only absorbs
  floating-point summation-order differences in the pandas/numpy reductions
  that turn artifacts into statistics (~1e-15 across releases).
- Floats, macro tier: relative `1e-9`. The CTM step is deterministic numpy /
  numba arithmetic with no RNG in the PDE; the tolerance absorbs kernel
  summation order and nothing else.

## Regenerating

Only by running the pinned engine — values are never typed by hand:

```sh
uv run --no-sync python tests/test_microsim/test_microsim_golden.py --regenerate all
uv run --no-sync python tests/test_microsim/test_microsim_golden.py --regenerate ring_sugiyama
uv run --no-sync python tests/test_microsim/test_microsim_golden.py --regenerate merge_zipper merge_scripted merge_meter_alinea
uv run --no-sync python tests/test_macrosim/test_macrosim_golden.py --regenerate
uv run --no-sync python tests/test_macrosim/test_macrosim_golden.py --regenerate macro_corridor_workzone
```

Each command re-runs the case in a temporary run tree and rewrites the JSON
in place, recording the versions it ran on (run them from the repository
root; the micro CLI changes into it itself). Re-run the golden tests
afterwards; they must pass on the same machine.

## Update rule

**A golden update requires a PR note explaining the physics or code change
that moved the numbers** (CLAUDE.md §9). State what changed (car-following
parameters, scenario YAML, runner logic, metric definition, SUMO version
bump), why the new numbers are the right ones, and which fields moved by how
much. A golden that changes without such a note is a regression until proven
otherwise. Bumping SUMO regenerates every micro golden and the note must
name the old and new versions.
