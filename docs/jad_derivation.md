# Jam-Absorption Driving: intercept-timing derivation

Required by CLAUDE.md §4.3 ("JAD timing math must be derived in
`docs/jad_derivation.md` with the geometry diagram, not embedded as bare
constants"). Implementation: `packages/controllers/controllers/jad.py`.
Lineage: He, Liu & Liu (2016), *Transportation Research Part B*.

## 1. The idea

A stop-and-go wave is a region of low speed whose upstream front travels
*backward* through traffic at a roughly constant speed `w_wave < 0` (empirically
−14 to −22 km/h; the M2 fundamental-diagram fit gives −14.6 km/h for US-101).
An absorbing vehicle slows *early* — "slow-in" — so that the low-density gap it
opens behind it arrives at the wave front just as the front reaches it. The wave
dissipates into that gap instead of propagating further upstream. The vehicle
then accelerates back — "fast-out" — but gently, because an abrupt recovery
compresses the platoon behind it and can seed a *new* wave.

## 2. Geometry

Position `x` increases in the direction of travel; the wave front moves toward
smaller `x` relative to the road, i.e. upstream, toward the AV. Take `t₀` as the
moment the AV commits to slowing, and place the AV at the origin.

```
        upstream                                            downstream
   (behind the AV)                                        (ahead of the AV)
        ────────────────────────────────────────────────────────────────►  x

   AV at x = 0                                    wave front at x = x_w
        │                                                   │
        ├── v_slow ──────────────►                 ◄──────── │ w_wave  (< 0)
        │   AV creeps forward                       front marches back
        │                                                   │
        └───────────── closing at (v_slow − w_wave) ─────────┘
                                                    meet at t = t_int
```

The AV advances at the held speed `v_slow > 0`; the front recedes at
`w_wave < 0`. Their separation closes at the **sum** of the magnitudes:

```
x_AV(t)    = v_slow · (t − t₀)
x_front(t) = x_w + w_wave · (t − t₀)
```

Setting `x_AV = x_front` gives the intercept time

```
t_int = x_w / (v_slow − w_wave)        (positive, since w_wave < 0)      (JAD-1)
```

The hold phase therefore ends no later than `t₀ + t_int`. Because
`v_slow − w_wave > v_slow`, the intercept is always sooner than a naive
"catch the wave at my own speed" estimate — using `x_w / v_slow` would overshoot
the meeting point and slow the AV for longer than necessary.

## 3. Slow-in depth and rate

The held speed is a fraction of the speed at commitment,

```
v_slow = β · v(t₀),        β default 0.55, calibration range 0.4–0.8       (JAD-2)
```

approached at no more than `a_slow = 1.0 m/s²` of deceleration. The gap opened
behind the AV over the hold is approximately `(v(t₀) − v_slow) · t_int`, so
deeper slow-ins (smaller β) absorb larger waves at the cost of more delay to
followers. β is a tunable parameter, not a derived constant.

## 4. Fast-out

Recovery is capped at `a_out = 1.5 m/s²`. The cap is the whole point: an
unbounded fast-out compresses the following platoon and can seed a secondary
wave, converting the absorber into an emitter. §6 below shows this failure mode
occurring in practice.

## 5. Approximations, stated plainly

`JAD-1` is a first-order estimate and the implementation treats it as a ceiling
on the hold, not as a precise schedule. It neglects:

* **the slow-in transient** — the AV needs `(v(t₀) − v_slow)/a_slow` seconds to
  reach `v_slow`, during which it covers more ground than `v_slow · Δt`;
* **wave-speed variability** — `w_wave` is a fixed parameter (default −18 km/h),
  not measured per wave, and real fronts accelerate and decelerate;
* **front-position quantisation** — `x_w` is resolved only to the oracle's bin
  width (`obs.downstream_dx`, default 100 m);
* **multi-wave interference** — the oracle reports the nearest qualifying front
  only.

Because of these, the controller also ends the hold on *observed* recovery of
the nearest bins, whichever comes first.

## 6. Why detection latency helps (measured)

`JAD-1` assumes the AV commits when the wave is at `x_w`. A **perfect** oracle
reports a wave the instant any bin within the 2 km lookahead qualifies — which
can be far earlier than the geometry assumes is useful. The AV then completes
slow-in, hold and fast-out before the front arrives, recovers, re-detects the
same wave, and repeats.

That chattering is measurable. Over 20 seeds on `corridor_10km` at 5%
penetration, AV acceleration reverses sign **30.7 times per run** under a
perfect oracle versus **16.6** under a 30 s delayed oracle, with AV speed
standard deviation 1.46 m/s versus 0.75 m/s. Each abrupt fast-out is a
candidate secondary-wave seed, and the outcome matches: with a perfect oracle,
5 of 20 seeds end with *more* waves than the uncontrolled baseline (one seed
goes from 1 wave to 11), and the wave-count benefit is not statistically
resolved. With 30–60 s of latency, **no seed is worse than baseline** and the
benefit is resolved.

Detection latency, in other words, accidentally supplies the lead time that
`JAD-1` assumes. This is a property of the current commit rule, not a law of
nature: a controller that used `JAD-1` to *defer* commitment until the front is
within `v_slow · t_int` of the AV should recover the same benefit without
needing a laggy sensor. That is the natural next iteration and has not been
built or tested — see [JAD_ORACLE_RESULTS.md](JAD_ORACLE_RESULTS.md) §5.

## 7. Symbols

| Symbol | Meaning | Default |
|---|---|---|
| `x_w` | distance from AV to the wave front at commitment [m] | measured from oracle bins |
| `w_wave` | wave-front propagation speed [m/s], negative | −5.0 m/s (−18 km/h) |
| `v_slow` | held speed during absorption [m/s] | `β · v(t₀)` |
| `β` | slow-in depth [-] | 0.55 (range 0.4–0.8) |
| `a_slow` | max deceleration into the hold [m/s²] | 1.0 |
| `a_out` | max acceleration out of the hold [m/s²] | 1.5 |
| `t_int` | estimated intercept time [s] | `x_w / (v_slow − w_wave)` |
| `lookahead_m` | detection horizon [m] | 2000 |
| `v_wave_thresh` | bin speed below which a bin is "jammed" [m/s] | 11.11 (40 km/h) |
