2026-10-07 01:08 CDT
PRE-REGISTRATION (written before any hypothesis run; only baseline weave/ceiling seeds 3-7 run so far)

Mechanism found (baseline, seeds 3-7): breakdown is triggered by changers that reach the section end
unchanged and halt (exiters at the end of lane 1; entrants at the end of the auxiliary lane, which then
blocks every exit-bound vehicle behind them; crossing pairs). 64 % of halted changers (86 % of halted
time) spent >= 2 s in the gap-choice dead zone: a target-lane vehicle whose front lies within the
changer's own length behind the changer's front is neither a leader (counted as "passed") nor a follower
(overlap), so no gap is a candidate, no follower cooperates and the changer is not eased.

H1 (code; tested only as a scratch counterfactual, no knob reaches it): in _weave_choose_gap, a
target-lane vehicle beside the changer whose front is behind the changer's front (within its length) is
an admissible gap leader when it is NOT itself a driven changer heading into the changer's lane (a
crossing partner keeps the existing rear-one rule). The changer eases towards it under the existing
feasibility test (_weave_easing_ok, <= b_c). Prediction: fewer end-of-section halts and fewer
dead-zone steps; paired exit-end flow gain vs weave > 0 with 95 % lower bound > 0 over seeds 3-22;
collisions 0; -9 m/s^2 steps not above baseline + 2; given-up exits not above baseline.

H2 (code; scratch counterfactual): an entrant halted (< 0.1 m/s) within exit_giveup_m (5 m) of the
auxiliary lane's end with no acceptable or forced change this step takes the exit (rerouted to the
exit's last edge) -- the entering mirror of exit_giveup_m and of the corridor's lane_end_giveup_m rule
(which skips weaving sections). Prediction: removes the exit-lane blockage episodes (seed 4: 50 s);
gain smaller than H1's; entrants taking the exit <= 2 % of entrants.

K-screen (existing knobs, seeds 3-7, reported in full whatever the result): exit_prepare 1 (+ the
corridor's lane_end_giveup_m 7.5: the xlsfg reference config the corridor runs); lookahead_m 200;
accept_gap_s 0.46 (measured entering-lead critical-gap median); force_within_m 150; lc_assertive 1.5;
lc_strategic_ramp 5; vacate_ahead_m 800 / 300. Prediction: none recovers more than ~100 veh/h, because
none reaches the dead zone; any arm that looks promising (paired mean gain > 100 veh/h at 5 seeds) is
re-run at seeds 3-22.

2026-10-07 01:17 CDT
PRE-REGISTRATION, ADDENDUM (written after H1, H2 and the knob screen, before any H3 or speed-factor run)

Results so far (20 seeds, paired vs weave): H1 +6 [-38, +50] (prediction failed: H1 moved the first
end-of-section halt from 100-330 s to 790-900 s in 4 of 5 seeds, yet the gore broke down at 330-450 s
anyway); H2 +32 [+8, +57]; lookahead_m 200 +98 [+55, +141] (unmeasured parameter; sensitivity only).
The gore breaks down without any halt once demand nears ~4,800 veh/h while lane loads there are below
the ceiling's (lane 0 1,000-1,380 veh/h): a dynamic breakdown from the crossings themselves.

H3 (code; scratch counterfactual): the measured model's principle (iii), post-crossing relaxation
(docs/MERGE_MODEL.md section 2, unchanged constants: entering movement only; T_eff,0 = clamp((g-s0)/v,
max(step, 0.5 T_i), T_i) for the entrant and its new follower; T_eff(t) = T_i - (T_i - T_eff,0)
exp(-t/7.5 s); restored at 4 tau_r, on the next lane change or on leaving), applied to the weave's
every entering crossing in the section (runner-driven and LC2013 arrival-step alike); nothing else
changed. Basis: US-101 leader-side fit tau_r 7.5 s [5.2, 15.9], r0 0.54; I-24 follower tau 6.0 s;
measured lag gaps below the IDM absorption floor (MERGE_MODEL_BRIEF 1.1, 1.4). Prediction: later
or no gore breakdown; paired flow gain > 0 with 95 % lower bound > 0 (seeds 3-22); collisions 0;
-9 m/s^2 steps not above baseline + 2; given-up exits not above baseline.

S1 (sensitivity, not a model change): speed_factor 1.245 (the I-94 driver check's recommendation,
pre-registered as a T.H.52 sensitivity in docs/MERGE_MODEL.md section 2), weave and ceiling, seeds
3-22. Reported beside the locked factor-1 test, never as acceptance. Caveat on record: the weave's
runner logic assumes factor 1 (_weave_veh / _weave_lane_vmax).

2026-10-07 01:23 CDT
PRE-REGISTRATION, ADDENDUM 2 (after H3 +13 [-16, +43], H2+H3 +38 [+1, +76], S1 weave +25 [-8, +58];
before any H5 run)

Finding: logging the runner's own speed commands (behaviour verified bit-identical, seeds 3-7) shows
the weave's cooperation and easing brake vehicles at their full comfortable deceleration b on ~1,000-
2,000 vehicle-steps per run before breakdown and far more after; ~60 % of entering crossings reached
the change with a gap at or above the follower's normal one, so H3 (relaxation after the change) had
little to act on: the weave pre-opens a full own-T IDM gap before every crossing.

H5 (code; scratch counterfactual): H3 plus the measured model's "gap choice evaluated at the relaxed
T" (docs/MERGE_MODEL.md section 2; MERGE_MODEL_BRIEF 5.5): in the weave's gap choice (follower F's
virtual-leader IDM towards the changer, the changer's IDM towards L) and in its acceptance's
follower-absorption check, IDM is evaluated at T_rel = clamp((s - s0)/v, max(step, 0.5 T_i), T_i)
instead of T_i, so a follower opens (and absorbs) a gap down to half its normal headway and then
relaxes back (tau_r 7.5 s) after the change. Acceptance time gap (0.6 s), brake guards, forced zone,
vacate, pair release, give-ups unchanged. Basis: real followers start at 0.76 (US-101), 0.87 (I-24
weave), 0.90 (I-24 OH) of the normal gap and relax over 6-7.5 s. Prediction: fewer full-b commands,
later/no gore breakdown; paired gain > 0 with 95 % lower bound > 0 (seeds 3-22); collisions 0; -9
m/s^2 steps not above baseline + 2; given-up exits not above baseline.

2026-10-07 01:26 CDT
PRE-REGISTRATION, ADDENDUM 3 (after H5: H3+H5 -35 [-112, +43], H2+H3+H5 +68 [+29, +108] with 1 collision;
before any run below). Both are diagnostics, not proposed model changes.

D1 (demand sensitivity): relocate each crossing vehicle with probability p (seeded per run, the
ceiling's relocation applied to a random subset; p = 1 is the ceiling), p = 0.25 and 0.5, seeds 3-22.
This is what a larger ramp-to-ramp share does to the weaving flows at the same counts (v_RR up, v_FR and
v_RF down by the same amount); the share is unobserved (WP-76). Reads the model's weave flow as a
function of its crossing volume. Prediction: flow rises monotonically with p; GEH < 5 (4,535 veh/h)
reached somewhere between p = 0.25 and 1.

D2 (dose-response of the one knob that moved): lookahead_m 300, seeds 3-22. If the gain over 200 m is
again resolved, the lever is the gap search / anticipation reach, not a lucky value. Diagnostic only:
lookahead_m has no measurement, and no value of it is proposed as a calibration.
2026-10-07 01:30 CDT ADDENDUM 4: K-screen extension before running: lc_strategic 2 and 10 (mainline exiters' LC2013 look-ahead), lc_keep_right 0 (the pre-calibration value), seeds 3-7; diagnostic of whether LC2013 positioning or keep-right binds; prediction: none moves more than ~100 veh/h.
