# CLAUDE.md — FlowState v2 Build Specification

> **Place this file at the repository root.** It is the authoritative technical
> specification for Claude Code sessions working on FlowState v2. Read it fully
> before making changes. When this file and old code disagree, this file wins.

---

## 0. Mission and non-negotiables

**FlowState v2** is a two-tier traffic simulation and analysis platform for
studying and dissipating stop-and-go ("phantom") traffic waves using sparse
controlled vehicles (AVs / speed-advised vehicles) and variable speed limits.

The end goal is a product: a **calibrated corridor digital twin** that a DOT,
MPO, or traffic-engineering consultancy can use to (a) reproduce observed
congestion waves on a real corridor, (b) evaluate smoothing controllers and
VSL strategies, and (c) auto-generate FHWA-style calibration and validation
reports. Everything built here must survive scrutiny by a professional traffic
engineer.

### Non-negotiables (never violate)

1. **No unvalidated claims.** Never write code, docs, or output that claims
   calibration or validation that has not actually been run. Every headline
   number must be reproducible from a seeded run.
2. **Emergent, not seeded.** The headline phenomenon is *emergent* string
   instability in the microscopic tier. Seeded-perturbation experiments are
   permitted but must be labeled `seeded=True` in outputs and reports.
3. **Standard metrics only** in headline results: throughput (veh/h), mean
   travel time, speed standard deviation σ_v, fuel/energy consumption, wave
   count and amplitude. The v1 "Efficient Frontier score" is retired; it may
   exist only as an optional diagnostic clearly marked non-standard.
4. **No consumer nav-app advisory-push features.** Waze CIFS cannot carry
   speed advisories; Google Routes `speedReadingIntervals` is read-only.
   Advisory delivery is out of scope; the product is simulation and
   decision support. Do not re-add these claims.
5. **Reproducibility.** Every simulation run takes an explicit RNG seed and
   records a full config snapshot. CI must reproduce golden results bit-stably
   at the summary-statistic level.
6. **Honest uncertainty.** Stochastic results are reported with confidence
   intervals from ≥ 20 seeded replicates, never as single-run point values.

---

## 1. Architecture Decision Record (read before proposing changes)

### ADR-1: Two-tier engine — SUMO microscopic (primary) + CTM macroscopic (secondary)

**Decision.** The primary physics engine is **Eclipse SUMO (≥ 1.27.1)** with
the **Intelligent Driver Model (IDM)** car-following model, controlled through
**libsumo/TraCI**. The v1 LWR/Godunov engine is ported into a tested package
(`macrosim`) and repurposed as a fast screening and (later) real-time
state-estimation tier. It is **no longer used to make claims about phantom-jam
formation or dissipation.**

**Why not keep LWR as primary.** LWR is a first-order scalar conservation law.
It is string-stable: entropy solutions dissipate perturbations and cannot
spontaneously grow stop-and-go waves. A first-order model "dissipating" a
hand-seeded jam demonstrates the model's built-in dissipation, not a
controller's merit. v1's ">15% AV penetration causes human gap exploitation"
result is physically impossible in LWR (no vehicles, no gaps, no lanes) and
must be treated as an implementation artifact.

**Why not ARZ (Aw–Rascle–Zhang) as primary.** ARZ fixes Payne–Whitham's
wrong-way-travel defects, but plain ARZ still does not produce emergent
stop-and-go waves; the relaxation-term variant that can is numerically
delicate (stiff source terms), has few reference implementations, and has no
recognition among DOT/consultant customers. It is a fine future research
track, not the platform.

**Why SUMO + IDM.**
- IDM is string-unstable in the right density band → *emergent* waves, the
  phenomenon we claim to study.
- It is the model family used by the CIRCLES program and the Stern et al.
  lineage → our results are directly comparable to the canonical literature.
- SUMO gives per-vehicle control (TraCI/libsumo), built-in emissions/energy
  models (HBEFA4, electric), OSM network import via `netconvert` (critical
  for onboarding arbitrary corridors), and industry recognition.
- pip-installable: `eclipse-sumo`, `libsumo`, `traci`, `sumolib`.

**Why keep the CTM tier at all.** (a) It preserves and dignifies the v1 work;
(b) CTM + Kalman filtering is the standard method for *real-time traffic state
estimation*, which is the eventual live-product tier; (c) a Numba-compiled CTM
sweeps parameter spaces ~1000× faster than SUMO for screening; (d) the v1
moving-bottleneck flux cap `F ← min(F, ρ·v*)` is a legitimate discretization of
the Delle Monache–Goatin PDE-ODE moving-constraint theory and stays.

### ADR-2: Hand-designed controllers first; RL deferred

Ship FollowerStopper, PI-with-saturation (both from Stern et al. 2018), and
Jam-Absorption Driving (He et al. 2016 lineage) as interpretable baselines.
Expose a **Gymnasium environment wrapper** so RL (PPO via Ray RLlib or
CleanRL) can be added later without refactoring. Do not build RL training
infrastructure in v2.0. Berkeley's Flow framework is unmaintained; do not
depend on it.

### ADR-3: Python monorepo, thin service layer

Python 3.12, `uv`-managed monorepo of packages, FastAPI service, job queue on
Redis + RQ (defer Kafka/Timescale until a real-time data product exists),
results as Parquet + SQLite metadata (defer Postgres until multi-user).
Frontend: Vite + React dashboard reading the API (upgrade of v1's
`index.html`). Docker for deployment. Rationale: solo-maintainer velocity;
every deferred component has a named upgrade path (§10).

---

## 2. Repository layout

```
flowstate/
├── CLAUDE.md                  # this file
├── NEXT_STEPS.md              # roadmap + business plan (companion doc)
├── pyproject.toml             # uv workspace root
├── packages/
│   ├── flowstate_core/        # shared types, config, units, RNG, constants
│   ├── microsim/              # SUMO scenario builder + runner (libsumo)
│   │   ├── scenarios.py       #   ring road, straight corridor, OSM import
│   │   ├── runner.py          #   stepping loop, controller dispatch
│   │   ├── vehicles.py        #   fleet spec, AV tagging, penetration control
│   │   └── outputs.py         #   trajectory capture → Parquet
│   ├── macrosim/              # ported v1 LWR/CTM engine (tested)
│   │   ├── fundamental.py     #   triangular FD, calibrated params
│   │   ├── ctm.py             #   Godunov/CTM update, supply-demand flux
│   │   ├── bottleneck.py      #   moving flux-cap actuation
│   │   └── estimator.py       #   (phase 4+) Kalman/EnKF state estimation
│   ├── controllers/           # pure-function controller library (shared)
│   │   ├── follower_stopper.py
│   │   ├── pi_saturation.py
│   │   ├── jad.py
│   │   ├── vsl.py             #   segment-level variable speed limits
│   │   └── gym_env.py         #   Gymnasium wrapper (hook only in v2.0)
│   ├── calibration/
│   │   ├── fd_fit.py          #   triangular/parabolic FD fit from loop data
│   │   ├── idm_mle.py         #   gap-based MLE / GA calibration of IDM
│   │   └── loaders/           #   pems.py, ngsim.py, i24motion.py, highd.py
│   ├── validation/
│   │   ├── metrics.py         #   GEH, RMSE/RMSPE, σ_v, throughput, fuel
│   │   ├── waves.py           #   wave detection & speed tracking
│   │   ├── criteria.py        #   FHWA acceptance thresholds
│   │   └── report.py          #   auto-generated calibration report (md/pdf)
│   └── api/
│       ├── main.py            #   FastAPI app
│       ├── schemas.py         #   Pydantic v2 models
│       └── jobs.py            #   RQ tasks
├── frontend/                  # Vite + React dashboard
├── scenarios/                 # versioned scenario configs (YAML)
│   ├── ring_sugiyama.yaml     #   230 m ring, 22 vehicles
│   ├── corridor_10km.yaml     #   v1-equivalent straight corridor
│   └── i24_replica.yaml       #   (phase 2+) I-24 westbound segment
├── data/                      # gitignored; fetch scripts in data/fetch/
├── tests/                     # mirrors packages/; golden/ for regressions
├── docs/
└── .github/workflows/ci.yml
```

Conventions: `ruff` (lint+format), `mypy --strict` on `flowstate_core`,
`controllers`, `validation`; Google-style docstrings; SI units internally
(m, s, veh/m) with explicit conversion helpers in `flowstate_core.units` —
**never** raw magic-number conversions inline. All public functions typed.

---

## 3. Microscopic tier specification (`microsim`)

### 3.1 Car-following model

Default `carFollowModel="IDM"` in SUMO vehicle types. IDM acceleration:

```
a(s, v, Δv) = a_max · [ 1 − (v/v0)^δ − (s*(v, Δv)/s)² ]
s*(v, Δv)   = s0 + max(0, v·T + v·Δv / (2·√(a_max·b)))
```

where `s` = bumper-to-bumper gap, `v` = own speed, `Δv` = approach rate
(v − v_leader), `v0` = desired speed, `T` = desired time headway, `a_max` =
max acceleration, `b` = comfortable deceleration, `s0` = minimum gap, `δ = 4`.

**Parameter table (defaults; ALL calibrated in Phase 2 — see §6):**

| Param | Symbol | Default | Calibration range | Notes |
|---|---|---|---|---|
| Desired speed | v0 | 33.3 m/s (120 km/h) | 25–38 m/s | per-driver draw |
| Time headway | T | 1.4 s | 0.8–2.2 s | key instability knob |
| Max accel | a_max | 0.73 m/s² | 0.3–1.5 m/s² | key instability knob |
| Comfort decel | b | 1.67 m/s² | 1.0–3.0 m/s² | |
| Min gap | s0 | 2.0 m | 1.0–3.0 m | |
| Accel exponent | δ | 4 | fixed | |

Heterogeneity: draw per-vehicle parameters from truncated normals
(σ ≈ 10–15% of mean) with a seeded RNG; homogeneous fleets rarely reproduce
observed wave amplitudes. SUMO's `EIDM` (extended IDM with estimation errors
and action points) is an allowed alternative vehicle type — expose as config,
benchmark both in Phase 1.

**String stability check (must be implemented in `validation/waves.py`):**
for a car-following law `a = f(s, v, Δv)` with partials `f_s > 0`, `f_v < 0`,
`f_Δv ≥ 0` at equilibrium, the platoon is string-stable iff

```
f_v²/2 − f_v·f_Δv − f_s ≥ 0
```

Implement this criterion symbolically for IDM (closed-form partials at the
equilibrium point) and **verify the sign conventions and formula against
Treiber & Kesting, *Traffic Flow Dynamics*, ch. 15 before trusting output.**
Use it to (a) locate the unstable density band for a calibrated parameter set
and (b) verify that chosen defaults are unstable near capacity — that is a
*requirement*, not a bug.

### 3.2 Scenarios

1. **`ring_sugiyama`** — canonical emergence benchmark. Single-lane ring,
   circumference ≈ 230 m, 22 vehicles, no on/off ramps, start from uniform
   spacing + tiny random perturbation. **Acceptance:** a stop-and-go wave
   emerges within simulated 3 min with no seeding, wave propagates backward,
   and one vehicle switched to FollowerStopper measurably reduces σ_v
   (reproduces Sugiyama et al. 2008 emergence + Stern et al. 2018 dampening).
   This scenario is a permanent CI integration test.
2. **`corridor_10km`** — single-lane (then 2-lane) 10 km corridor, inflow at
   upstream boundary set to produce density in the unstable band (calibrated;
   v1 used 38 veh/km), optional downstream speed-drop trigger. Used for
   penetration/compliance sweeps. Both `seeded=False` (waves from inflow
   noise) and `seeded=True` (controlled comparisons) modes.
3. **`i24_replica`** (Phase 2+) — OSM-imported I-24 westbound segment matching
   the I-24 MOTION instrumented miles; boundary flows from I-24 MOTION /
   RDS data. This is the validation flagship.
4. **`osm_generic`** — pipeline: bbox or OSM extract → `netconvert` →
   corridor pruning → scenario YAML. This is the "any city" onboarding path
   and a product feature; make it a first-class, tested function, not a
   script.

Scenario YAML schema (Pydantic-validated): network source, lanes, length,
demand profile (veh/h vs time), fleet spec (vtype distributions), AV
penetration ∈ [0, 0.3], compliance ∈ [0.1, 1.0], controller + params, sim
duration, step length (default 0.5 s; sub-step actions via SUMO
`actionStepLength`), seed, replicates.

### 3.3 Runner and controller dispatch

- Use **libsumo** (in-process, ~10× faster than TCP TraCI) by default;
  keep a TraCI fallback flag for debugging with `sumo-gui`.
- Each step: read state for AV-tagged vehicles (own speed, leader gap/speed
  via `vehicle.getLeader`), compute `v_cmd` from the controller (pure
  function, no I/O), apply via `vehicle.setSpeed` with SUMO safety checks
  left ON (`speedMode` default) so controllers cannot command collisions.
- **Compliance model:** each AV-tagged vehicle draws compliance once per run
  (Bernoulli p = compliance); non-compliant vehicles ignore `v_cmd`. Sweep
  compliance ∈ {0.1 … 1.0}; v1's fixed 80% assumption is retired.
- Capture per-vehicle trajectories (t, id, x, lane, v, a) at 1–2 Hz to
  Parquet; compute fuel/energy via SUMO emission classes (HBEFA4) and record
  per-vehicle totals. Also record SUMO's `--fcd-output` equivalent through
  libsumo subscriptions rather than XML post-processing (XML parsing of large
  FCD files is a known performance trap).

### 3.4 Performance targets (laptop-class, no GPU)

- `corridor_10km`, 20 sim-min, ~1,500 vehicles: ≥ 5× real-time with libsumo.
- `ring_sugiyama`: ≥ 50× real-time.
- 20-replicate sweep of one config: ≤ 15 min wall-clock using
  `multiprocessing` pool (SUMO instances are process-parallel trivially).
- Macro CTM: 1,000 cells × 10,000 steps ≤ 1 s (Numba-jitted kernel).

---

## 4. Controller specifications (`controllers`)

All controllers are **pure functions** `(state, params, memory) → (v_cmd,
memory)` operating in SI units, shared verbatim between micro and macro tiers,
unit-tested against hand-computed cases. `memory` carries integrator state
(PI) or phase state (JAD).

### 4.1 FollowerStopper (Stern et al. 2018)

Piecewise command speed from gap `Δx` and negative approach rate
`Δv_− = min(Δv_leader − v, 0)` with three parabolic region boundaries:

```
Δx_k = Δx_k^0 + (Δv_−)² / (2·d_k),   k = 1, 2, 3
```

Command:
- Region 1 (Δx ≤ Δx_1): `v_cmd = 0`
- Region 2 (Δx_1 < Δx ≤ Δx_2): `v_cmd = v_lead · (Δx − Δx_1)/(Δx_2 − Δx_1)`
- Region 3 (Δx_2 < Δx ≤ Δx_3): `v_cmd = v_lead + (U − v_lead)·(Δx − Δx_2)/(Δx_3 − Δx_2)`
- Safe region (Δx > Δx_3): `v_cmd = U` (reference speed)

with `v_lead = min(max(v_leader, 0), U)`. Literature defaults:
`Δx_k^0 = (4.5, 5.25, 6.0) m`, `d_k = (1.5, 1.0, 0.5) m/s²`.
**Task for implementer: verify these constants against Stern et al. (2018),
Transportation Research Part C 89:205–221, Table/eqns, before freezing; cite
the equation numbers in the docstring.** Reference speed `U` is set from the
recent average platoon speed (rolling 30–60 s window), per the paper.
Unit tests must verify `v_cmd` continuity at every region boundary.

### 4.2 PI with saturation (Stern et al. 2018)

**Corrected 2026-08-30 against the source, per §13.** This section previously
specified `v_target = 0.75 · v̄_platoon`. That factor does not appear in
Stern et al. (2018); it was a simplification, and on an open corridor it is a
geometric ratchet — the AV depresses the platoon mean that sets its own target,
compounding to gridlock (M3 measured a 94% throughput collapse; see
`docs/PI_CONTROLLER_FIX.md`). The paper's controller (§3.2, Eqs. 3–5) is:

```
U          = temporal mean of the AV's OWN speed over ≈ 38 s
v_target   = U + v_catch · min(max((Δx − g_l)/(g_u − g_l), 0), 1)          (3)
v_cmd_{j+1} = β_j (α_j v_target_j + (1 − α_j) v_lead_j) + (1 − β_j) v_cmd_j (4)
α          = min(max((Δx − Δx_s)/γ, 0), 1),   β = 1 − α/2                  (5)
```

with `g_l = 7 m`, `g_u = 30 m`, `v_catch = 1 m/s`, `γ = 2 m`, and safety
distance `Δx_s = max(2 s · Δv, 4 m)`. The gap correction is **additive and
non-negative**, so the target never falls below `U`; at short gaps `α → 0` and
the command follows the leader. Output range is therefore `[0, U + v_catch]`,
not `[0, U]`.

`controllers.pi_saturation` implements the above. The superseded simplification
is retained as `controllers.pi_meanfrac` — clearly labeled, used only to
reproduce the M3 failure result — and must never be presented as the
literature controller. Anti-windup remains required for any PI form that
carries an explicit integrator (v1 lacked it).

### 4.3 Jam-Absorption Driving (JAD, slow-in / fast-out)

Phases: `CRUISE → SLOW_IN → HOLD → FAST_OUT → CRUISE`.
- **Detection:** downstream wave oracle. In simulation the oracle reads the
  space-time speed field within a lookahead L (default 2 km) and flags a wave
  when mean speed in any 100 m bin drops below `v_wave_thresh` (default
  40 km/h) with a backward-moving front. The oracle interface must be
  swappable (perfect oracle vs. delayed/noisy oracle) — the delayed/noisy
  variant models realistic detection latency (parameter: 10–60 s delay,
  ±20% amplitude noise) and every headline JAD result must also be reported
  under the noisy oracle.
- **Slow-in:** decelerate at ≤ 1.0 m/s² to `v_slow = β · v_current`
  (β default 0.55, calibration range 0.4–0.8), timed so the AV's density
  shadow meets the wave front — compute intercept from wave-front position
  and measured wave speed.
- **Fast-out:** once local leader speed recovers above threshold, accelerate
  at `a_out ≤ a_max` back to reference; cap `a_out` at 1.5 m/s² to avoid
  seeding a secondary wave.
- Cite He, Liu & Liu (2016), Transportation Research Part B lineage in the
  docstring; JAD timing math must be derived in `docs/jad_derivation.md`
  with the geometry diagram, not embedded as bare constants.

### 4.4 VSL (variable speed limit) controller

Segment-level, gantry-style: corridor divided into 0.5–1.0 km segments; each
segment posts a speed from {90, 80, 70, 60, 50} km/h (configurable ladder)
based on downstream occupancy/speed (simple SPECIALIST-style or threshold
logic first). Applied in micro tier via per-edge `edge.setMaxSpeed` scaled by
compliance; in macro tier via capping `V_f` per cell. This is the
DOT-relevant product controller — it must exist even though the research
literature focuses on Lagrangian control.

### 4.5 Gymnasium hook (`gym_env.py`)

`FlowStateEnv(gym.Env)` wrapping `corridor_10km`: observation = ego speed,
gap, leader speed, downstream mean speeds (k bins); action = target speed;
reward = configurable (default: −fuel − λ·σ_v). Implement the interface and a
random-policy smoke test only; no training code in v2.0.

---

## 5. Macroscopic tier specification (`macrosim`) — port of v1

Port v1's engine with these corrections and freezes:

1. **CTM form.** Cell update `n_i^{t+1} = n_i^t + (Δt/Δx)(F_{i−1/2} − F_{i+1/2})`
   with Daganzo supply–demand flux `F = min(Λ(ρ_L), Σ(ρ_R))`,
   `Λ(ρ) = min(Q_e(ρ), q_max)`, `Σ(ρ) = min(q_max, Q_e_cong(ρ))`. Triangular
   FD retained but **parameters become calibrated per-corridor inputs**
   (from `calibration/fd_fit.py`), not constants. v1's (V_f=100, ρ_jam=160,
   w=−20) survive only as the documented default preset named
   `preset: v1_legacy`.
2. **CFL guard.** Assert `Δt ≤ Δx / max(V_f, |w|)` at construction; recompute
   if the FD changes. Hard error, not warning.
3. **Conservation + bounds tests.** Total vehicle count conserved to 1e−10
   per step (closed ring) / ledgered inflow−outflow (open corridor); density
   clamped-and-flagged if numerically outside [0, ρ_jam] (a clamp firing is
   a test failure, not a silent fix).
4. **Riemann exactness tests.** Shock speed equals Rankine–Hugoniot
   `s = (q_R − q_L)/(ρ_R − ρ_L)` within 2% at 150 cells; rarefaction fan
   matches the analytic self-similar solution.
5. **Moving bottleneck.** Keep `F_{i+1/2} ← min(F_{i+1/2}, ρ_i · v*)` at
   AV-occupied cells; document it as the discrete analog of the
   Delle Monache–Goatin (2014) moving flux constraint; add the
   reduced-capacity variant `F ← min(F, α·q_max(v*))` as an alternative and
   compare both against micro-tier ground truth in Phase 3 (this comparison
   is itself a small publishable result).
6. **Role restriction.** `macrosim` results are labeled `tier: "screening"`
   in all outputs. The API must refuse to generate a validation report from
   macro-only runs.
7. **Estimator (Phase 4+).** `estimator.py` implements a CTM-based Kalman /
   ensemble Kalman filter fusing simulated loop-detector measurements
   (30-s flow/occupancy) — the standard traffic-state-estimation stack and
   the seed of the future real-time product. Interface now, implementation
   Phase 4.

---

## 6. Calibration specification (`calibration`)

### 6.1 Fundamental diagram (macro tier + demand setup)

- **Source:** Caltrans PeMS 5-min station data (flow, occupancy, speed) for a
  chosen freeway segment; loaders must also accept generic CSV so TxDOT /
  other-state exports work.
- **Method:** fit triangular FD by constrained least squares on the
  flow–density scatter: free-flow branch slope V_f from uncongested bin
  regression; capacity q_max from the 95th-percentile flow; congested branch
  by quantile regression (τ ≈ 0.9) through congested points; report ρ_c,
  ρ_jam, w with bootstrap CIs. Persist as a versioned `FDCalibration`
  artifact (JSON) referenced by scenario configs.

### 6.2 IDM parameters (micro tier)

- **Sources (in onboarding order):** reconstructed NGSIM (Montanino–Punzo
  version — raw NGSIM accelerations are notoriously noisy), highD (request
  from leveldXdata), I-24 MOTION INCEPTION (register at i24motion.org; use
  the VT virtual-trajectory tools for its scale). Write one loader per
  dataset normalizing to a common `LeaderFollowerEpisode` schema
  (t, gap, v_f, v_l ≥ 30 s continuous car-following, no lane change).
- **Method:** per-episode calibration by minimizing gap error — either
  gap-based maximum likelihood (Kesting & Treiber 2008 approach) or global
  optimization (differential evolution) on RMSE(gap); **gap-based objectives,
  not speed/accel-based** (better identifiability). Fit per-driver, then fit
  the *population distribution* of parameters (mean, cov of a truncated
  multivariate normal); simulation draws drivers from this distribution.
- **Outputs:** `IDMCalibration` artifact with population stats, per-episode
  fit quality, and holdout validation (fit on 70% episodes, report gap RMSE
  on 30%).

### 6.3 Demand calibration (corridor scenarios)

Inflow profiles from detector/boundary counts (PeMS or I-24); iterate demand
scaling until simulated link flows meet the GEH criterion (§7.1). Automate as
`calibration.demand.fit_inflow(scenario, counts) → DemandProfile`.

---

## 7. Validation specification (`validation`) — the credibility core

### 7.1 Acceptance criteria (encode in `criteria.py`, report pass/fail)

| Check | Criterion | Source of standard |
|---|---|---|
| Link flows | GEH < 5 for ≥ 85% of link-hour comparisons; GEH = √(2(m−c)²/(m+c)), hourly veh flows | FHWA Traffic Analysis Toolbox Vol. III (2019) usage / DMRB |
| Speeds | RMSPE ≤ 15% on segment mean speeds; visual speed-contour comparison archived | common microsim practice; cite in report |
| Emergent wave speed | Backward wave-front speed in calibrated `i24_replica` / `corridor_10km` within 14–22 km/h **without seeding** | empirical stop-and-go literature (≈ 20 km/h) |
| Ring benchmark | Emergence + single-AV dampening reproduced (§3.2.1) | Sugiyama 2008; Stern 2018 |
| Stochastic reporting | Every headline metric: mean ± 95% CI over ≥ 20 seeds | internal standard |
| Sensitivity | Penetration {1,2,5,10,15,20}% × compliance {25,50,80,100}% grid published with CIs | internal standard |

**Verify the exact FHWA Vol. III (FHWA-HOP-18-036) threshold wording when
implementing `criteria.py` and cite it in the generated report; state DOT
variants (e.g., TxDOT microsimulation acceptability criteria) may be offered
as selectable profiles.**

### 7.2 Wave detection (`waves.py`)

From the space-time mean-speed field (bins: 50–100 m × 10–30 s): threshold
`v < v_jam_thresh` (default 40 km/h, configurable), connected-component
labeling of jam regions, front extraction, robust line fit per front →
wave speed distribution, wave count, amplitude (Δv), duration. This module
serves both validation (wave-speed criterion) and product analytics
(before/after controller comparisons). Unit-test against synthetic fields
with known planted waves.

### 7.3 Metrics (`metrics.py`)

Throughput (veh/h at reference cross-sections), VMT/VHT, mean + p90 travel
time, σ_v (spatial and temporal variants — define both precisely in
docstrings), fuel/energy per vehicle-km from SUMO emission output, and
`waves.py` outputs. All functions take Parquet trajectory/edge data, are
deterministic, and are covered by tests with hand-computed fixtures.

### 7.4 Auto-report (`report.py`) — this is a product feature

`generate_report(run_set) → report.md (+ optional PDF)` containing: scenario
provenance (config hash, seeds, package versions), calibration artifacts used,
criteria table with pass/fail, speed contour figures (baseline vs controller),
metric tables with CIs, and an explicit limitations section (auto-included
boilerplate: single corridor, model-form uncertainty, compliance assumption
sweep). Nothing in the report may be free-text-generated numbers; every value
traces to computed artifacts.

---

## 8. API specification (`api`)

FastAPI, Pydantic v2, OpenAPI served at `/docs`. Async job model (RQ):

- `POST /scenarios` — validate + store scenario YAML → `scenario_id`
- `POST /runs` — `{scenario_id, overrides, replicates, tier}` → `run_id`
  (job enqueued); tier ∈ {micro, macro}
- `GET /runs/{id}` — status, progress, seed list
- `GET /runs/{id}/metrics` — computed metrics JSON (with CIs)
- `GET /runs/{id}/heatmap?field=speed|density` — binned space-time array
  (JSON or PNG)
- `POST /sweeps` — grid spec (penetration × compliance × controller) →
  child runs + aggregate
- `POST /calibrations/fd`, `POST /calibrations/idm` — upload data ref, run
  fit, return artifact id
- `POST /reports` — `{run_ids}` → report artifact
- Auth: single API key middleware now; real auth is a Phase 4 concern.

Rules: no endpoint executes simulations synchronously; all long work goes
through RQ; every response carries `config_hash` for reproducibility. The v1
Flask endpoints are retired; keep one legacy-compat route only if the old
frontend is temporarily reused.

---

## 9. Testing & CI

- **Framework:** pytest + hypothesis (property tests) + coverage gate ≥ 85%
  on `controllers`, `macrosim`, `validation`, `calibration`.
- **Unit tests:** listed inline above (Riemann exactness, conservation,
  region-boundary continuity, IDM equilibrium gap closed-form
  `s_eq = (s0 + v·T)/√(1 − (v/v0)^δ)` — verify formula symbolically,
  metric fixtures, wave detection on synthetic fields).
- **Property tests:** densities ∈ [0, ρ_jam]; speeds ≥ 0; controller outputs
  ∈ [0, U]; scenario schema round-trips.
- **Integration tests (CI, headless SUMO):** `ring_sugiyama` emergence +
  dampening (§3.2.1) with fixed seed; `corridor_10km` 2-min smoke run;
  macro-vs-micro sanity (both tiers produce backward-propagating congestion
  under the same seeded shock; qualitative agreement assertion only).
- **Golden regressions:** summary statistics (not full trajectories) of fixed
  seed runs stored in `tests/golden/`; CI compares within tolerances;
  updating goldens requires a PR note explaining the physics/code change.
- **CI:** GitHub Actions — lint, typecheck, unit, integration (SUMO installed
  via pip in the runner), build Docker image on tag.
- **Determinism note:** SUMO with a fixed `--seed` and fixed step length is
  deterministic per version; goldens are per-SUMO-version — pin
  `eclipse-sumo==1.27.1` and bump deliberately.

---

## 10. Deferred components (do not build early; documented upgrade paths)

| Deferred | Trigger to build | Upgrade path |
|---|---|---|
| Postgres/Timescale | multi-user product pilot | SQLite metadata → Postgres via SQLAlchemy already in place |
| Kafka ingestion | live detector feed contract | RQ jobs → Kafka consumers, estimator service |
| CTM Kalman estimator (full) | live-data phase | interface stubbed in `estimator.py` |
| RL controllers | after Phase 3 validation | `gym_env.py` ready |
| ARZ-with-relaxation macro | publication ambition only | new module beside `ctm.py`; never blocks product |
| Multi-lane macro / lane-change modeling | if micro-macro gap too large in Phase 3 | SUMO already multi-lane; macro stays effective-single-pipe |
| Real advisory delivery | never unilaterally; only with DOT/OEM partner | out of scope by policy (§0.4) |

---

## 11. Definition of done per milestone

- **M0 (repo + macro port):** layout above exists; `macrosim` passes Riemann,
  conservation, CFL tests; v1 behavior reproduced under `preset: v1_legacy`;
  CI green.
- **M1 (micro core):** `ring_sugiyama` emergence + Stern dampening test
  green; all four controllers implemented, unit-tested, runnable on ring and
  corridor; performance targets (§3.4) met.
- **M2 (calibration):** FD fit from real PeMS pull with CIs; IDM population
  fit from reconstructed NGSIM with holdout RMSE reported; demand fitter
  working on `corridor_10km`.
- **M3 (validation):** `i24_replica` (or best-available corridor) passes GEH
  and wave-speed criteria; full penetration×compliance sweep with CIs;
  auto-report generates end-to-end; results honestly documented including
  failures.
- **M4 (product layer):** API + dashboard: upload/pick corridor → run sweep →
  view heatmaps → download report; Dockerized; API-key auth.
- **M5 (hardening):** load test (10 concurrent sweep jobs), docs site,
  versioned release, demo corridor gallery.

---

## 12. Known v1 defects — do not reintroduce

1. Claiming phantom-jam *formation/dissipation* physics from LWR.
2. The ">15% penetration → human gap exploitation" claim (impossible in
   macro; only assertable if it emerges in the calibrated micro tier —
   in which case document the mechanism with lane-change statistics).
3. The Efficient Frontier score as a headline metric.
4. Fixed 80% compliance as an assumption instead of a swept variable.
5. Waze CIFS / Google Routes / MapKit advisory-push roadmap items.
6. Single-run results without seeds/CIs; silent parameter constants without
   calibration provenance.
7. `sys.path` hacks for imports (use the package layout).

---

## 13. Reference list to keep at hand (cite in docstrings/report)

- Lighthill & Whitham (1955); Richards (1956) — LWR.
- Daganzo (1994, 1995) — CTM; "Requiem" for PW-type second-order models.
- Aw & Rascle (2000); Zhang (2002) — ARZ.
- Treiber, Hennecke & Helbing (2000); Treiber & Kesting (2013 book) — IDM,
  string stability.
- Sugiyama et al. (2008), New J. Phys. 10:033001 — ring-road emergence.
- Stern et al. (2018), Transp. Res. C 89:205–221 — FollowerStopper, PI-sat,
  field dampening.
- He, Liu & Liu (2016), Transp. Res. B — Jam-Absorption Driving.
- Delle Monache & Goatin (2014), J. Diff. Eq. 257 — moving flux constraint;
  Liard & Piccoli (2019), SIAM J. Appl. Math. 79.
- CIRCLES MegaVanderTest (I-24, Nov 2022; BAIR blog 2025-03-25) — 100-AV
  field test, ~15–20% energy-savings trend near AVs.
- Kesting & Treiber (2008) — car-following calibration methodology.
- FHWA Traffic Analysis Toolbox Vol. III (FHWA-HOP-18-036, 2019) — GEH /
  calibration acceptance.
- Montanino & Punzo — reconstructed NGSIM.
- I-24 MOTION: arXiv:2302.12308 (testbed), arXiv:2311.10888 (VT tools),
  i24motion.org.

When any implementation detail conflicts with one of these sources, the
source wins — fix the spec via PR to this file with a citation.
