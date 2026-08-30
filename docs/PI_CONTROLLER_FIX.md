# PI-with-saturation: a specification error, not a controller failure

**Date:** 2026-08-30 · **Scenario:** `corridor_10km` (EIDM, emergent/unseeded) ·
**Cells:** 3 × 20 common-random-number seeds · **Artifacts:**
`artifacts/pi_retune_summary.json`, `runs/pi_retune/analysis.json` ·
**Scripts:** `scripts/pi_retune_experiment.py`, `scripts/pi_retune_analyze.py`

## 1. What M3 found, and why it was misleading

The M3 sweep ([M3_RESULTS.md](M3_RESULTS.md) §4.5) reported that the controller
registered as `pi_saturation` **gridlocked** the open corridor at 5% penetration:
throughput collapsed 93.6%, fuel rose 269%. That result is real and reproduces —
but it was a result about the **CLAUDE.md §4.2 simplification**, not about the
controller in Stern et al. (2018).

The §4.2 target was `v_target = 0.75 · v̄_platoon`, a *multiplicative* fraction of
the rolling platoon mean. On a closed ring the fixed vehicle count and periodic
boundary arrest the loop. On an open corridor it is a geometric ratchet with no
floor: the AV drives at 0.75× the mean → traffic behind it slows → the mean falls
→ the target falls → repeat, to standstill.

## 2. What the paper actually specifies

Stern et al. (2018), *Transportation Research Part C* 89:205–221 (arXiv:1705.01693)
§3.2 contains no such factor. `U` is the temporal mean of the **AV's own** speed
over ≈38 s, and the target is `U` **plus** a bounded, non-negative gap term:

```
v_target    = U + v_catch · min(max((Δx − g_l)/(g_u − g_l), 0), 1)            (3)
v_cmd_{j+1} = β_j (α_j v_target_j + (1 − α_j) v_lead_j) + (1 − β_j) v_cmd_j   (4)
α           = min(max((Δx − Δx_s)/γ, 0), 1),      β = 1 − α/2                 (5)
```

with `g_l = 7 m`, `g_u = 30 m`, `v_catch = 1 m/s`, `γ = 2 m`,
`Δx_s = max(2 s · Δv, 4 m)`. Two structural properties prevent the ratchet:
the gap correction is **additive and non-negative**, so the target never falls
below the vehicle's own running mean; and at short gaps `α → 0`, so the command
defers entirely to the leader rather than imposing a slower speed.

`controllers.pi_saturation` now implements Eqs. (3)–(5). The simplification is
retained as `controllers.pi_meanfrac`, labeled as superseded, so the M3 result
stays reproducible. CLAUDE.md §4.2 has been corrected against the source, as
§13 of that document requires.

## 3. Head-to-head, same scenario and seeds

Marginal means with 95% t-CIs over 20 seeds; deltas are **paired per seed**
against the baseline (valid under common random numbers).

| Metric | baseline | `pi_meanfrac` (§4.2 simplification) | `pi_saturation` (Stern Eqs. 3–5) |
|---|---|---|---|
| Throughput [veh/h] | 1246.65 [1226.90, 1266.40] | 79.47 [29.02, 129.92] | 1237.93 [1209.22, 1266.65] |
| σ_v temporal [m/s] | 3.39 [2.83, 3.94] | 2.14 [1.96, 2.33] | 2.38 [2.12, 2.64] |
| σ_v spatial [m/s] | 3.50 [3.06, 3.94] | 2.11 [1.93, 2.29] | 2.88 [2.54, 3.21] |
| Fuel [ml/veh-km] | 65.44 [64.57, 66.32] | 241.43 [196.40, 286.46] | 63.57 [63.12, 64.02] |
| Waves per run | 3.85 [2.59, 5.11] | 2.25 [1.75, 2.75] | 2.25 [1.59, 2.91] |

Paired deltas vs baseline:

| Metric | `pi_meanfrac` | `pi_saturation` |
|---|---|---|
| Throughput | -1167.18 [-1220.45, -1113.91] (-93.6%, **resolved**) | -8.71 [-29.23, +11.80] (-0.7%, not resolved) |
| σ_v temporal | -1.24 [-1.89, -0.59] (-36.7%, **resolved**) | -1.01 [-1.35, -0.66] (-29.7%, **resolved**) |
| σ_v spatial | -1.39 [-1.91, -0.88] (-39.8%, **resolved**) | -0.62 [-0.81, -0.44] (-17.8%, **resolved**) |
| Mean travel time | -99.92 [-119.88, -79.97] (-21.5%, **resolved**) | -0.80 [-3.00, +1.39] (-0.2%, not resolved) |
| Fuel | +175.98 [+131.17, +220.80] (+268.9%, **resolved**) | -1.87 [-2.45, -1.30] (-2.9%, **resolved**) |
| Wave count | -1.60 [-3.05, -0.15] (-41.6%, **resolved**) | -1.60 [-2.61, -0.59] (-41.6%, **resolved**) |

**Reading these honestly.** `pi_meanfrac`'s σ_v, wave-count and travel-time
"improvements" are standstill artifacts: throughput fell 93.6% and fuel rose
269%, and its travel-time figure additionally suffers survivorship bias, since
only the vehicles that cleared the measurement span are timed. `pi_saturation`
calms the corridor at no resolved throughput or travel-time cost — the two
"not resolved" entries are the desired outcome for a cost metric, not a weak
finding.

## 4. Ring regression

The paper's controller was designed and field-tested on a ring, so it must still
work there. On `ring_sugiyama` (1 AV of 22, 3 seeds), temporal σ_v falls from
**2.07 m/s** (baseline) to **0.46 m/s** — a 78% reduction. FollowerStopper
remains stronger at **0.17 m/s**, consistent with Stern et al.'s own field
comparison. This is a sanity check at 3 seeds, not a headline result; the
CI-gated ring benchmark remains the FollowerStopper integration test.

## 5. Limitations

* One corridor (`corridor_10km`), one penetration/compliance point (5% / 100%),
  EIDM fleet with screening-calibrated demand — the caveats of
  [M3_RESULTS.md](M3_RESULTS.md) §1 apply unchanged.
* Gains were **not** tuned: the constants are the paper's published values. No
  claim is made that they are optimal for this corridor.
* `U` uses an exponential moving average (τ = 38 s) rather than the paper's
  boxcar window over ≈38 s of measurements; steady states agree and the
  effective averaging length is comparable, but the transient response differs
  slightly.
* The ring figures in §4 are a 3-seed regression check and carry no CIs.
