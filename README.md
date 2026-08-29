# FlowState v2

**Calibrated corridor digital twins for studying — and dissipating — stop-and-go
("phantom") traffic waves with sparse controlled vehicles and variable speed
limits.**

FlowState v2 is a two-tier traffic simulation and analysis platform:

- **Microscopic tier (primary):** Eclipse SUMO (1.27.1) with the Intelligent
  Driver Model. String instability is *emergent* — stop-and-go waves grow out
  of calibrated car-following dynamics, not hand-seeded shocks — reproducing
  the Sugiyama et al. (2008) ring-road experiment and the Stern et al. (2018)
  single-AV dampening result.
- **Macroscopic tier (screening):** the v1 LWR/CTM Godunov engine, ported with
  a full test battery (Riemann exactness, conservation, CFL guards) and
  repurposed for what first-order models are actually good at: fast parameter
  screening and, later, real-time state estimation. It makes no claims about
  phantom-jam formation.

> **On v1:** FlowState v1 studied controlled dissipation of *seeded* shocks in
> a first-order (LWR) model — a model class that is string-stable by
> construction and cannot form phantom jams. v2 studies *emergent* stop-and-go
> waves in a calibrated microscopic model. v1 remains available at
> [abc000cool/FlowState](https://github.com/abc000cool/FlowState) as motivated
> preliminary work.

## Status

Under active construction. Milestone tracker:

- [ ] **M0** — monorepo + macro tier ported with test battery, CI green
- [ ] **M1** — ring-road emergence + single-AV dampening reproduced in CI
- [ ] **M2** — FD + IDM population calibration from public data
- [ ] **M3** — corridor validation vs FHWA-style criteria, full sweep with CIs
- [ ] **M4** — FastAPI service + dashboard + Docker
- [ ] **M5** — hardening, docs, versioned release

## Non-negotiables

1. No unvalidated claims — every headline number reproduces from a seeded run.
2. Emergent, not seeded — seeded experiments are always labeled `seeded=True`.
3. Standard metrics only: throughput, travel time, σ_v, fuel/energy, wave
   count/speed/amplitude.
4. No consumer nav-app advisory features — this is simulation and decision
   support.
5. Reproducibility: explicit seeds, config hashes, golden regression tests.
6. Honest uncertainty: headline metrics are mean ± 95% CI over ≥ 20 replicates.

## License

Apache-2.0. See [LICENSE](LICENSE).
