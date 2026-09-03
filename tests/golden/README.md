# Golden regressions

Summary statistics of fixed-seed runs (CLAUDE.md §9). CI re-runs each case
and compares against the stored values; a drift means the physics pipeline
changed, whether or not anyone meant it to.

| File | Producer | Case | Marker |
|---|---|---|---|
| `ring_sugiyama.json` | `microsim.run_micro` + `validation.metrics.compute_metrics` | `scenarios/ring_sugiyama.yaml` as is (600 sim-s, seed 42) | `integration` |
| `corridor_10km_smoke.json` | same | `scenarios/corridor_10km.yaml` with `sim.duration_s = 120` (the §9 2-min smoke run), seed 42 | `integration` |
| `macro_corridor.json` | `macrosim.run_macro` (`v1_legacy` FD) + `api.results.macro_metrics` | `golden_config()` in `tests/test_macrosim/test_macrosim_golden.py`: 10 km corridor, `corridor_10km` demand steps, seeded 120 s capacity drop at 7 km, seed 42 | none (pure numpy/numba, runs in CI) |

Tests: `tests/test_microsim/test_microsim_golden.py`,
`tests/test_macrosim/test_macrosim_golden.py`.

## What a golden holds

Summary statistics only — never trajectories or fields:

- the `validation.metrics.Metrics` fields (throughput, travel times, σ_v
  spatial/temporal, VMT/VHT, fuel per veh-km, wave count / speed /
  amplitude; NaN is stored as `null`);
- run counts (vehicles planned/departed and fuel total for micro; grid, FD,
  ledger and clamp flag for macro);
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
- Integer counts (vehicles, waves, cells): exact.
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
uv run --no-sync python tests/test_macrosim/test_macrosim_golden.py --regenerate
```

Each command re-runs the case in a temporary run tree and rewrites the JSON
in place, recording the versions it ran on. Re-run the golden tests
afterwards; they must pass on the same machine.

## Update rule

**A golden update requires a PR note explaining the physics or code change
that moved the numbers** (CLAUDE.md §9). State what changed (car-following
parameters, scenario YAML, runner logic, metric definition, SUMO version
bump), why the new numbers are the right ones, and which fields moved by how
much. A golden that changes without such a note is a regression until proven
otherwise. Bumping SUMO regenerates every micro golden and the note must
name the old and new versions.
