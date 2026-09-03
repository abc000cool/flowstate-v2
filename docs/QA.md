# Anticipated questions (Track A5)

Answers a judge, reviewer or engineer is likely to ask, each pointing at the
document that carries the evidence. Nothing here introduces a number that is
not already in a results document.

**Why not LWR? The first version used it.**
LWR (Lighthill–Whitham–Richards) is a first-order scalar conservation law. Its
entropy solutions dissipate perturbations; it is string-stable by construction
and cannot grow stop-and-go waves. A first-order model "dissipating" a
hand-seeded jam demonstrates the model's built-in dissipation, not a
controller's merit. That is why v1's headline was retracted and the engine was
demoted to a labeled screening tier (CLAUDE.md ADR-1; docs/LESSONS.md #1).

**Why SUMO with the Intelligent Driver Model?**
IDM is string-unstable in the right density band, so waves are *emergent*: the
analytic criterion in `validation.string_stability` locates the unstable band
and the 230 m Sugiyama ring reproduces emergence and single-vehicle dampening
as a permanent CI test. It is the model family of the CIRCLES program and the
Stern et al. lineage, so results are comparable to the field literature, and
SUMO gives per-vehicle control, emission models and OpenStreetMap import
(CLAUDE.md ADR-1).

**What does "GEH < 5 for 85% of link-hours" mean, and is it a good test?**
GEH = √(2(m−c)²/(m+c)) compares a modeled hourly volume m with a counted one c;
it behaves like a percentage error for large volumes and like an absolute one
for small volumes. FHWA's Traffic Analysis Toolbox Vol. III uses it as the
standard link-flow acceptance statistic; a corridor passes when at least 85% of
its link-hour comparisons score below 5 (`validation.criteria`). It is a
necessary check, not a sufficient one: a model can match counts and still have
the wrong speeds, which is why RMSPE on segment speeds and the wave-speed
criterion sit next to it.

**Why do some criteria fail?**
On the 640 m US-101 site: the site is a third of a wave's wavelength, the
recording starts congested while the model warms up from empty, the replica
has no on-ramp merge, and the IDM population discharges the queue slower than
the real crowd (docs/M3_US101_VALIDATION.md §6). The wave-speed failure there
turned out to be a site-length and density artifact rather than a calibration
defect (docs/WAVE_SPEED_DIAGNOSIS.md). On the I-24 flagship the dominant
limitation is different: the trajectory instrument tracks only about half of
the vehicle-time in the peak, so every count is a lower bound and demand is
ambiguous (docs/I24_DATA.md §4). The criteria table for I-24, with every
failure and its cause, is docs/I24_VALIDATION.md. A documented failure with a
cause is a result; a fabricated pass would end the project (CLAUDE.md §0.1).

**Why are counts from a camera system a "lower bound"?**
Every I-24 MOTION document is a trajectory fragment; fragments break at camera
boundaries, under overpasses and when tall vehicles occlude interior lanes.
Speeds, computed as distance travelled over time tracked, are insensitive to
that; counts and densities are not. The tracked Edie density in the peak is
52–67% of the density the calibrated car-following spacing implies at the
observed speed (docs/I24_DATA.md §4). The fix is the testbed's radar detector
counts, requested from the data owner.

**What does "1% penetration" mean in practice?**
One vehicle in a hundred running a gap-based controller such as FollowerStopper.
On the synthetic corridor that was already enough for a resolved 24.5%
reduction in temporal speed variance with 20 seeds (docs/M3_RESULTS.md); on
real 5-lane US-101 geometry the σ_v dose-response replicated (−8.2% at 1%) but
carried a small resolved throughput and fuel cost (docs/US101_PENETRATION.md).
Practically: the effect is real at penetrations today's adaptive-cruise fleets
already exceed, and whether it is "free" depends on the corridor.

**Aren't you just seeding the jams you then dissipate?**
No. Every headline run is `seeded=False`: waves grow from calibrated
heterogeneity and insertion jitter. Seeded-perturbation experiments exist for
controlled comparisons and are labeled `seeded=True` in every artifact and
report. Measured boundary conditions and ramp demand derived from data are
calibration inputs (standard FHWA practice), not shocks (docs/CONTRACTS.md §2).

**How do you know the controller comparison is fair?**
Every cell of a sweep runs the same scenario with the same list of 20 seeds
(common random numbers), so per-seed paired differences are reported with
t-distribution 95% confidence intervals; an effect is called "resolved" only
when its interval excludes zero (docs/CONTROLLER_COMPARISON.md). The
uncontrolled baseline is numerically identical across experiments.

**Why does JAD need a *worse* sensor to work?**
With a perfect oracle it fires the instant any bin qualifies, finishes its
slow-in/hold/fast-out before the front arrives, re-triggers, and each abrupt
fast-out can seed a secondary wave; 5 of 20 seeds ended worse than no control.
With 30–60 s latency and ±20% noise no seed is worse than baseline
(docs/JAD_ORACLE_RESULTS.md). Realistic detection defers commitment; making
that deferral explicit is roadmap item B4.

**Is the calibration any good?**
Two independent estimation paths agree: the IDM population fitted per episode
on gaps and the macroscopic fundamental diagram fitted on flow–density bins
give the same congested wave speed on US-101 (14.6 km/h both ways). The I-24
fit uses 17,652 episodes with a held-out gap RMSE of 5.29 m against 6.44 m on
NGSIM, and the two populations put emergent ring waves in the empirical
14–22 km/h band once density is above ~60 veh/km (docs/I24_DATA.md §5,
docs/WAVE_SPEED_DIAGNOSIS.md).

**Could a reviewer rerun this?**
Yes, that is the product: every run takes an explicit seed and records its
config hash, package versions and calibration provenance; the auto-generated
report lists them (docs/reports/us101_replica/report.md); goldens in CI compare
summary statistics of fixed-seed runs bit-stably per SUMO version
(CLAUDE.md §9).

**What is the business, if not advisories to drivers?**
Simulation and decision support for the people who already buy corridor
studies: consultants and agencies who spend weeks calibrating a model to FHWA
criteria. Advisory delivery through consumer navigation apps is out of scope by
policy — Waze CIFS cannot carry speed advisories and Google's speed fields are
read-only (CLAUDE.md §0.4; NEXT_STEPS.md §3).
