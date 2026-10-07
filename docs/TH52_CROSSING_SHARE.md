# T.H.52 weave: how many US 52 entrants take exit 242B (2026-10-07)

The T.H.52 weave loses its capacity to the volume of crossing traffic
(docs/WEAVE_LOSS_DIAGNOSIS.md §3.11). With a quarter fewer crossers the fixture
recovers 275 veh/h, and with half fewer it carries the full demand. That volume
depends on one unobserved input: the share of US 52 entrants that leave at the
paired exit (ramp-to-ramp), with the mainline's share set by the exit count.
This note asks what public evidence says about that share, bounds it, and says
what the model should do with it.

Nothing was simulated. No code, scenario, fixture, test or golden was changed,
and nothing was committed. The only computation was a reading of the corridor's
committed 5-minute detector table (§4), run in session scratch.

Labels:
- **[published]**: a figure or statement from the linked public source.
- **[record]**: from the named repository document or artifact.
- **[computed]**: computed here from committed files, with the method stated.
- **[exploratory]**: computed here, not pre-registered, with known biases (§4). It is not a measurement.
- **[engineering default]**: an HCM default used because nothing is observed.
- **[assumed]**: a bound chosen here, with its reasoning.

## 0. In plain English

- **The movement.** US 52 northbound comes off the Lafayette Bridge and joins
  I-94 westbound. Some of those drivers leave 305 m later at exit 242B
  (I-35E North / US 10 West). The rest cross into the I-94 through lanes.
- **What the model assumes.** Every vehicle reaching exit 242B takes it with the
  same probability, whether it came from the mainline or the ramp. That is
  HCM 7.1's simple estimation method (the proportional split). It sends 29 % of
  US 52 entrants to 242B in the fixture's 05:30–05:50 window and 18 % in the
  06:30–07:30 peak hour.
- **No published figure exists.**
  - MnDOT studied exactly this weave from 2022 to 2024. Its final report is
    available only on request and is not online.
  - Nothing else public gives an origin–destination split for this movement:
    not the news coverage, not MnDOT's congestion plan, not the Met Council's
    interchange study, not the HCM.
  - Rethinking I-94 ends at Marion St, west of this weave.
- **What is published is qualitative, and it points one way.**
  - MnDOT names this weave as one of the two key problems at the interchange.
  - Its shortlisted fixes give US 52 drivers bound for I-35E North their own
    lane or bridge.
  - Its 2018 congestion plan classes I-94 at I-35E as a "ramp to ramp weaving"
    bottleneck.
  - None of this gives a number.
- **The corridor's own counts lean above the proportional split** [exploratory].
  - The method is the one a 1999 MnDOT study used to estimate ramp-to-ramp
    volumes at Twin Cities weaves from loop counts (§3, E7).
  - On the nine September 2026 days, seven of twelve readings put the
    ramp-to-ramp share at 0.49–0.69, against a proportional 0.27–0.30.
  - The readings move with the specification (0.26–0.97). Some biases push
    them upward. A 5 % day-to-day error in the ramp detector's count would be
    enough to make the most careful reading agree with the proportional split.
- **Bounded range.** The share lies between the proportional split (0.29 in the
  fixture window, 0.18 at the peak) and **0.70** [assumed]. That bound comes
  from the counts' least-biased reading and from keeping a real mainline flow
  to 242B at the peak. It is not measured.
  - Across this range the fixture moves from failing the flow criterion at
    19 of 20 seeds to passing it at all 20 (D1 [record]). So the T.H.52
    verdict depends on this input across the whole range.
- **Recommendation.**
  - Do not change the model's split as calibration now.
  - Carry the share as an uncertain input over [proportional, 0.70] in the
    §8.5 design.
  - That needs an amendment to §8.5 and a default-off key in the plan builder
    (§6).
  - To turn it into calibration:
    - ask MnDOT for the study's final report, the owner's call;
    - or pre-register a count-based estimate and accept it only if it is tight.
    - Either way, §7 needs an amendment before the share can be calibrated.
- **Cheapest test.** `merge_model_selfcheck.py` cannot vary the share. The
  diagnosis's D1 harness approximates it. A scratch "swap" wrapper that changes
  only who crosses, at shares {proportional, 0.40, 0.50, 0.60, 0.70}, costs
  about 100 fixture runs of about 2 s each (§7).

## 1. The movement, verified against OSM

From `data/osm/mndot_i94_wb_stpaul.osm`, the scenario's map [computed]:

| scenario name | OSM way | tags that identify it |
|---|---|---|
| `on-ramp 769818012` (edges `769818012`, `194037903`) | 769818012 | `bridge:name` Lafayette Bridge, `ref` US 52, `motorway_link`, 1 lane, advisory 30 mph, `start_date` 2015 |
| | 194037903 | `ref` US 52, `destination:ref` "I 94 West;US 10 West", `destination:ref:to` "I 35E", advisory 30 mph |
| the section, `attach_edge` of both ramps | 51388891 | I-94 / US 10, 4 lanes, 55 mph, `turn:lanes` none\|none\|none\|slight_right (the auxiliary lane feeds the exit) |
| `off-ramp 18207598`, the weave's `exit_ramp` | 18207598 | **`junction:ref` 242B**, `destination:ref` "I 35E North;US 10 West", `ref` US 10, 1 lane, advisory 40 mph |
| the next exit (the fixture's "jackson exit") | 82150350 | `junction:ref` 242A, `destination` "12th Street;State Capitol"; its detector rnd_87221 is labelled "Jackson St" by MnDOT |

- The ramp's own signs send US 52 traffic for I-35E North and US 10 West into
  this weave.
- No other freeway connection serves that movement, so every US 52 → I-35E N
  driver crosses nothing and exits at 242B. These are the ramp-to-ramp
  vehicles.
- The crossing movements are:
  - US 52 → I-94 W (ramp to freeway, v_RF);
  - I-94 WB → 242B (freeway to ramp, v_FR).

## 2. What the model assumes now

### 2.1 The code path [record]

- **Where destinations are drawn.**
  `packages/microsim/microsim/vehicles.py::build_corridor_plan` (from line 726)
  draws each vehicle's destination once, when the plan is built. For every
  vehicle and every off-ramp at or downstream of its entry, in corridor order,
  it draws one Bernoulli at the ramp's `exit_fraction`, read at the vehicle's
  departure time (`_step_value(ramps[j].exit_fraction, departs[i])`, line
  828).
- **Entrants draw too.** The skip is `attach_pos < entry` (line 826). Both T.H.52
  ramps attach to 51388891, so entrants draw at the same `exit_fraction` as
  mainline vehicles. Route ids are `on<k>_off<j>` for ramp-to-ramp and
  `main_off<j>` for freeway-to-ramp.
- **The runner only checks the pairing.**
  `packages/microsim/microsim/runner.py::_check_weave_pairs` (line 420) and the
  schema (`WeaveSpec.exit_ramp`, `flowstate_core/config.py` line 376) pair the
  weave ramp with its exit to validate the geometry. They do not assign
  destinations. The weave's rules act on whatever routes the plan drew.
- **The fixture does the same.** `_th52_corridor_config` and
  `TH52_OBSERVED_0530` (`tests/test_microsim/test_microsim_merge_managed_meter.py`
  lines 1072–1153) apply one exit fraction "to the mainline and the entrants
  alike". WP-76 identified this as HCM 7.1's simple weaving volume estimation
  method, Equations 13-2 to 13-6 (docs/WEAVE_MODEL_PLAN.md):

  P = v_OFF / v, v_RR = v_ON · P, v_RF = v_ON (1 − P), v_FR = v_OFF − v_RR, v_FF = v_F − v_FR.
- **Where the exit fraction comes from.** No detector sits on 242B, so the exit
  fraction is a conservation closure, `(S790 + rnd_91040 − S97 − rnd_87221) /
  (S790 + rnd_91040)` (`artifacts/demand_mndot_i94_wb_stpaul.json`, off-ramp
  18207598, `method: conservation`).
  - It runs from 0.33 at 05:30 to about 0.18 in the first peak hour.
  - Two windows are odd: 0.089 at 07:50 and 0.14 at 08:05, while S97 is
    congested (storage breaks conservation).
- **A note on timing.** Mainline vehicles draw the fraction at their departure
  time at the corridor's entry, 10 km upstream. On the fixture the entry is
  830 m upstream, so this does not matter there.

### 2.2 The implied weaving volumes

The HCM split applied to the observed nine-day means: S790 and rnd_91040 from
`data/mndot/mndot_i94_wb_stpaul/observations.json`, and the scenario's
`exit_fraction` [computed]. All values are in veh/h. "RR share" is v_RR / v_ON,
the quantity in question.

| window | v_F (S790) | v_ON (US 52) | v_OFF (242B) | RR share | v_RR | v_RF | v_FR | crossers v_RF + v_FR | v_FF |
|---|---|---|---|---|---|---|---|---|---|
| 05:30–05:50 (the fixture) | 3,594 | 1,228 | 1,405 | 0.29 | 357 | 871 | 1,048 | **1,919** | 2,546 |
| 05:30–06:30 | 3,794 | 1,216 | 1,332 | 0.27 | 324 | 892 | 1,008 | 1,899 | 2,787 |
| **06:30–07:30 (first peak hour)** | 4,911 | 1,297 | 1,144 | **0.18** | 239 | 1,058 | 905 | **1,962** | 4,006 |
| 07:30–08:30 | 4,065 | 1,354 | 905 | 0.17 | 226 | 1,129 | 680 | 1,809 | 3,386 |
| 08:30–09:30 | 4,119 | 1,200 | 1,103 | 0.21 | 248 | 951 | 855 | 1,806 | 3,264 |

- The fixture rows reproduce WP-76's per-window table and the diagnosis's
  "≈ 1,900 crossers an hour".
- The weaving volume is about the same in the fixture window and the peak hour
  (1,919 against 1,962). The peak's total demand is 1,390 veh/h higher.
- **Daily totals** [computed, `detectors.csv`, nine weekdays]:
  - the US 52 ramp (rnd_91040) carries 20,800–22,100 veh/day;
  - 242B carries 22,400–23,900 by conservation;
  - S790 carries 62,400–69,100.

## 3. Evidence

| # | source | figure | how directly it applies | label | link |
|---|---|---|---|---|---|
| E1 | MnDOT, *Hwy 52 Lafayette Bridge, I-94 and I-35E in St. Paul*, study January 2022 – October 2024 | **No split published.** It names two key issues: lane imbalance on northbound 52, and weaving on westbound I-94 between the northbound 52 and I-35E ramps. Volumes have been "relatively stable" since 2015 while crashes nearly tripled. The final report is available upon request | Same movement, same direction, same years. It is the one source likely to hold the split, but its report is not public | [published], qualitative | [project page](https://www.dot.state.mn.us/metro/projects/lafayettebridge/index.html); [contacts](https://www.dot.state.mn.us/metro/projects/lafayettebridge/contacts.html) |
| E2 | The same study's virtual public meeting, 2024-05-21 (YouTube, "Hwy 52 @ I 94 Phase 2") | Not read: a video, with no transcript reachable without signing in | Same | — | [meetings](https://www.dot.state.mn.us/metro/projects/lafayettebridge/meetings.html); [recording](https://www.youtube.com/watch?v=xLbatogW-Ps) |
| E3 | The study's shortlisted options, as reported in July 2024 (Villager; Star Tribune) | No number. One option adds a ramp with a separate lane to northbound I-35E. Another adds a single-lane bridge carrying drivers bound for northbound I-35E over the freeway. Both are meant to cut weaving on I-94 | The same movement treated as large enough to deserve its own connection. Qualitative only. Both pages returned HTTP 429; read through search-result excerpts | [published], qualitative | [Villager](https://www.myvillager.com/news/general_news/comments-sought-on-four-plans-to-redesign-lafayette-interchange/article_3aeaf52a-38b4-11ef-85c2-f7f3d5aac338.html); [Star Tribune](https://www.startribune.com/mndot-eyes-fixes-for-crash-prone-lafayette-bridge/600370563) |
| E4 | Star Tribune, "The Drive: Motorists seek fix on dangerous Lafayette Bridge" (date not read) | Nearly 7 in 10 northbound 52 drivers, about 26,000 a day, use the single-lane ramp to westbound I-94 / northbound I-35E | The entrance volume, not the split. The 2026 detector reads 20,800–22,100 per weekday (§2.2). HTTP 429; read through a search-result excerpt | [published] | [article](https://www.startribune.com/the-drive-motorists-seek-fix-on-dangerous-lafayette-bridge/473736483) |
| E5 | CBS Minnesota, 2024-05-22 | No number. The middle lane of northbound 52 serves both northbound I-35E and westbound I-94 traffic | Confirms the two movements share the single-lane ramp | [published], qualitative | [article](https://www.cbsnews.com/minnesota/news/improvements-planned-for-lafayette-bridge-in-st-paul/) |
| E6 | MnDOT Metro, CMSP Phase IV *System Problem Statement Technical Memorandum* (SRF / Sambatek, March 2018), appendix table | Three locations on I-94 here:<br>• **6065**, I-94 at I-94/I-35E, "Ramp to Ramp Weaving", annual total cost $2.28 M;<br>• 6064, I-94 at US 52, "Entering Traffic", $4.33 M;<br>• 6140, I-94 at I-94/I-35E, "Exit Capacity", $12.77 M.<br>The category means heavy weaving between an entrance and an exit ramp | A weave type assigned "using readily available traffic volume data and roadway geometry". It is not an origin–destination measurement, and the row does not state a direction | [published], classification | [PDF, 18 MB](https://www.dot.state.mn.us/metro/programmanagement/pdf/cmsp-phase4-tech-memo-system-problem-statement.pdf) |
| E7 | Kwon, *Estimation of the Capacity in Freeway Weaving Areas for Traffic Management and Operations*, MnDOT MN/RC-1999-40 (May 1999) | Appendix A lists I-94 WB, "52 NB → 35E NB/Jackson", weave type A, class L. Chapter V estimates the share of ramp-to-ramp volume in on-ramp volume from loop counts with a Kalman filter (after Nihan & Davis 1987) at two Twin Cities weaves, not this one | The same weave before the 2015 rebuild. No figure for it. The **method** is the one §4 applies | [published], method | [PDF](https://mdl.mndot.gov/_flysystem/fedora/2023-06/199940.pdf) |
| E8 | *Highway Capacity Manual*, Edition 7.1 (November 2025), Chapter 13 | Ramp-to-ramp demand is a required input from "field data, modeling" (Exhibit 13-9). Where it is not observed, the simple method assumes the off-ramp draws a similar proportion P from both legs (p. 13-18, Equations 13-2 to 13-6). Its Chapter 27 service-volume illustration uses v_RR = 8 % of v with v_RF 15 % and v_FR 12 %, about 35 % of the on-ramp (p. 27-22). That is an example, not a default | The only default there is; it is what the model uses | [engineering default] | [PDF](https://nap.nationalacademies.org/resource/26432/Highway_Capacity_Manual_Edition_7.1_Chapters.pdf) |
| E9 | MnDOT, *Rethinking I-94* | Study area: I-94 between Marion St in St. Paul and Hwy 55 / Hiawatha Ave in Minneapolis | Ends west of downtown St. Paul. **Does not cover this weave** | not applicable | [project page](https://talk.dot.state.mn.us/rethinking-i94) |
| E10 | Met Council, *Freeway System Interchange Study* (executive summary) | Its solution locations, including "downtown commons", were analysed with traffic volumes and origin–destination information. Neither is published | Possibly this area, but the inputs are not public | not usable | [study page](https://metrocouncil.org/Transportation/Planning-2/Transit-Plans,-Studies-Reports/Highways-Roads/Freeway-System-Interchange-Study.aspx) |
| E11 | Lafayette Bridge replacement environmental documents (FONSI 2009-09-17, per MnDOT's SP 6244-30 project summary, read through a search-result excerpt) | Would hold forecast ramp volumes for the rebuilt ramps | Not online. The MnDOT Digital Library search found no copy | not found | [project summary](https://www.dot.state.mn.us/pm/pdf/project-cost-estimate-summary.pdf) |

**Reading.**
- No public source gives the ramp-to-ramp share at this weave.
- The one study that measured this weave's operations is E1. Its report is
  available on request only.
- The published statements (E1, E3, E5, E6) all treat the US 52 → I-35E N
  movement as a large, problem-causing one. They support a share at least as
  high as the proportional split. They do not bound it.

## 4. The corridor's own counts [exploratory]

**Method.** The idea is Kwon's (E7), in a static form.
- With U = S790 (mainline into the section), O = rnd_91040 (the US 52 ramp)
  and D = S97 + rnd_87221 (everything that leaves the section other than by
  242B), conservation over the short section gives

  D = (1 − θ1) U + (1 − θ2) O,

  with θ1 the mainline's share to 242B and θ2 the ramp-to-ramp share.
- The fit is least squares without an intercept, on free-flow 5-minute windows
  (S790 and S97 above 20 m/s) of the nine weekdays in
  `data/mndot/mndot_i94_wb_stpaul/detectors.csv`.
- The 95 % intervals are a day-block bootstrap with 2,000 draws.
- **Two identifications.**
  - **Time of day** uses the variation across windows. It assumes the
    origin–destination pattern is constant within the window.
  - **Day-to-day** demeans each 5-minute slot across days (slot fixed
    effects). It uses only day-to-day variation.

| period | identification | window [min] | n | proportional P | θ1, mainline → 242B [95 %] | θ2, US 52 → 242B [95 %] |
|---|---|---|---|---|---|---|
| 05:30–06:30 (AM free flow) | time of day | 5 | 103 | 0.27 | 0.13 [0.08, 0.18] | **0.69 [0.54, 0.87]** |
| 05:30–06:30 | time of day | 15 | 33 | 0.27 | 0.04 [−0.16, 0.14] | 0.96 [0.64, 1.58] |
| 05:30–09:30 (free-flow windows; after 06:30 almost none) | day-to-day | 5 | 103 | 0.27 | 0.22 [0.09, 0.39] | 0.67 [0.43, 0.99] |
| 05:30–09:30 | day-to-day | 15 | 33 | 0.27 | 0.03 [−0.24, 0.25] | 0.81 [0.47, 1.60] |
| 09:30–15:00 | time of day | 15 | 101 | 0.28 | 0.15 [0.08, 0.22] | 0.67 [0.48, 0.87] |
| 09:30–15:00 | day-to-day | 15 | 82 | 0.29 | 0.15 [0.09, 0.29] | 0.49 [0.19, 0.77] |
| 15:00–19:00 | time of day | 15 | 39 | 0.28 | 0.29 [0.18, 0.35] | 0.26 [0.06, 0.58] |
| 15:00–19:00 | day-to-day | 15 | 31 | 0.28 | 0.27 [0.08, 0.40] | 0.33 [0.10, 0.86] |
| 05:00–22:00 | day-to-day | 5 | 989 | 0.29 | 0.30 [0.25, 0.34] | 0.65 [0.55, 0.77] |
| **05:00–22:00** | **day-to-day** | **15** | 270 | 0.30 | 0.20 [0.15, 0.26] | **0.58 [0.44, 0.71]** |
| 05:00–22:00, the five calibration days | day-to-day | 15 | 133 | 0.30 | 0.22 [0.15, 0.35] | 0.58 [0.41, 0.78] |
| 05:00–22:00 | time of day | 15 | 297 | 0.30 | 0.07 [0.05, 0.09] | 0.92 [0.85, 0.98] |

**What it says.**
- **Direction.** θ2 is above θ1 in every row except the two afternoon ones,
  where they overlap. In nine of the twelve rows the θ2 interval lies wholly
  above the proportional P.
- **Size.** The readings cannot pin the size:
  - the central values run from 0.26 to 0.96;
  - the AM-specific rows have intervals 0.3 to 1.1 wide.

**Why it is not a measurement.**
1. **Time-of-day rows are confounded.** The proportional exit share falls
   from 0.33 at 05:30 to 0.18 at the peak while S790 rises 37 %. That is
   consistent with a stable, high ramp-to-ramp share. It is equally consistent
   with an origin–destination pattern that shifts through the morning, which
   this regression cannot separate. The all-day time-of-day rows (0.92) are
   the most exposed.
2. **Errors in the counts bias θ2 upward.**
   - Count error in O, and the 30–60 s travel-time misalignment of 5-minute
     windows, attenuate O's coefficient. Both push θ2 up.
   - Within a slot, O's day-to-day standard deviation is only about 78 veh/h
     at 15 minutes.
   - A random day-level ramp-count error of 5 %, about 50 veh/h, would be about
     40 % of that variance. That alone could turn a true proportional 0.29 into
     the observed 0.58.
   - The data-quality artifact's recorded count error is ±5 %
     (`artifacts/uncertainty_mndot_i94_wb_stpaul_p1b_rehearsal.json`,
     `demand_scale`), though as a corridor-wide bias, not a day-level one.
3. **Not pre-registered.** The specifications were chosen while looking.
   Every row but one pools all nine days, the four validation days included.
   Only the calibration-days row respects the protocol's split.

**For WEAVE_LOSS_DIAGNOSIS §6.3 M2.**
- "Detector counts cannot give it" is too strong. Counts can estimate the
  share in principle (E7).
- On these nine days they are weakly identified. They lean above the
  proportional split.

## 5. A bounded range

| bound | ramp-to-ramp share s = v_RR / v_ON | basis | label |
|---|---|---|---|
| hard bounds from counts | fixture: 0 to 1.0; peak hour: 0 to 0.88 | 0 ≤ v_RR ≤ min(v_ON, v_OFF) | [computed], uninformative |
| published value | none | §3 | — |
| the model now | 0.29 (fixture window), 0.27 (05:30–06:30), **0.18** (06:30–07:30) | HCM 7.1 simple method, E8 | [engineering default] |
| exploratory count reading | 0.26–0.96 central, seven of twelve at 0.49–0.69; least-biased row 0.58 [0.44, 0.71] | §4 | [exploratory] |
| **working range for the uncertainty design** | **from the proportional split (time-varying, the current model) to 0.70**, clipped in each window to v_OFF / v_ON | below | **[assumed]** |

**Lower end: the proportional split.**
- Nothing found points below it. E1, E3, E5 and E6 all treat the movement as
  heavy, and the counts lean above it.
- None of that proves the share is not lower. A symmetric design could add a
  value below proportional at almost no cost, at the amendment's choice.
- Below proportional the weave only gets worse. That cannot move the
  diagnosis's conclusion that the model is capacity-short.

**Upper end: 0.70.**
1. It is the upper 95 % limit of the least-biased count reading: 0.71 on all
   days, 0.78 on the calibration days. That reading is itself biased upward
   (§4).
2. At 0.70 the peak hour's mainline-to-242B flow falls to 236 veh/h, a share
   of 4.8 %. Higher values would make the signed US 10 West movement from the
   east nearly vanish, and nothing supports that.
3. The diagnosis's provisional range [0.29, 0.50] (WEAVE_LOSS_DIAGNOSIS §6.3)
   lies inside this one. No source supports stopping at 0.50, and the counts
   point past it.

**Clipping.** In seven 5-minute windows between 07:30 and 08:05 the
conservation exit volume is below 0.70 · v_ON. The lowest is 0.34 at 07:50,
where S97's queue breaks conservation. There the share must be clipped so that
v_FR ≥ 0.

**What the range means for the weave.** Swapping destinations between pairs of
crossers keeps every leg's volume (§7):

| s | fixture: v_RR / v_RF / v_FR | fixture crossers [veh/h] | equivalent D1 p | peak: v_RR / v_FR | peak crossers |
|---|---|---|---|---|---|
| proportional (0.29 / 0.18) | 358 / 870 / 1,047 | 1,917 | 0 | 239 / 905 | 1,963 |
| 0.40 | 491 / 737 / 914 | 1,651 | 0.14 | 519 / 625 | 1,403 |
| 0.50 | 614 / 614 / 791 | 1,405 | 0.27 | 648 / 496 | 1,144 |
| 0.60 | 737 / 491 / 668 | 1,159 | 0.40 | 778 / 366 | 885 |
| 0.70 | 860 / 368 / 545 | 914 | 0.52 | 908 / 236 | 625 |

[computed; "equivalent D1 p" = 1 − crossers / crossers at proportional.]

**Fixture results** (D1, WEAVE_LOSS_DIAGNOSIS §3.11 [record]; D1 does not keep
leg volumes, so the mapping is approximate):

| D1 p | about s | GEH < 5 seeds | all-criteria seeds |
|---|---|---|---|
| 0 | proportional | 1 of 20 | 0 of 20 |
| 0.25 | 0.49 | 18 of 20 | 0 of 20 |
| 0.5 | 0.68 | 20 of 20 | 1 of 20 |

- The diagnosis's own mapping, p = 0.25 ≈ 49 %, agrees with this table.
- **The fixture's flow verdict changes inside the bounded range.** The all-criteria
  verdict stays mostly failing up to 0.70, because speed lags flow.
- **At the corridor's peak** the same shares remove relatively more crossers,
  because the proportional share there is only 0.18.

## 6. Recommendation

1. **Do not change the model's split as calibration now.**
   - There is no measured or published value.
   - The one quantitative reading (§4) is exploratory, and it is biased in the
     direction that passes the test.
   - The fixture's sensitivity to this input (D1) has already been seen.
     Adopting a share now would be the tuning that the protocol's change
     control and WEAVE_LOSS_DIAGNOSIS §6.1 rule out.
2. **Carry the share as an uncertain input** under
   docs/FRISCO_PROTOCOL.md §8.5. The protocol already asks for this case:
   - §2.3 says that where a split cannot be defended, the study uses a
     documented assumption and "tests how much the results depend on it (item
     11)".
   - The documented assumption is the HCM split. The dependence test is the
     §8.5 design.
   - **What needs an amendment** (a proposed Amendment 3, dated, fixed before
     any run that uses it):
     - **§8.5's list of varied inputs.** Today it is demand scale, mean T, mean
       v0 and truck share. Add the T.H.52 ramp-to-ramp share with the range
       [proportional, 0.70], basis `stated_assumption`, source this note.
       - A natural parameter is u ∈ [0, 1], with
         s(t) = P(t) + u · (0.70 − P(t)), clipped to v_OFF(t) / v_ON(t).
       - u = 0 is the current model.
       - The Ruth St weave has the same unobserved split. The amendment should
         say whether it gets the same treatment.
     - **The plan builder.** `build_corridor_plan` applies one exit fraction to
       every vehicle and has no key for this. It needs one, off by default and
       hash-neutral like the other weave keys, e.g. `weave.ramp_to_ramp_share`.
       - Entrants exit at s.
       - Mainline vehicles exit at (P (v_F + v_ON) − s · v_ON) / v_F per window,
         so the exit's volume is unchanged.
       - It changes `flowstate_core.config`, so it needs the usual contract
         note.
     - **`validation.uncertainty`.** A new parameter kind and its
       `KIND_MAPS_TO` entry.
   - **Reporting.** The T.H.52 and S790 results are then reported conditional
     on the share, as WEAVE_LOSS_DIAGNOSIS §6.3 M2 already proposes. The
     protocol's 90 % sign rule decides whether any strategy effect is robust
     to it.
3. **Routes to calibration** (an input with documented provenance). Each needs
   a §7 amendment: §7 lists what calibration may change, and an
   origin–destination split at a weave is not on it. Each result is then
   reported under both splits.
   - **(a) MnDOT's study report (E1)**, the most direct source.
     - It is offered on request through the study's contacts page.
     - If it gives an AM-peak volume or share for 52 NB → I-35E NB, that is
       "the agency's best available count" in §2.3's words.
     - Requesting it is the owner's call. Nothing was requested here.
   - **(b) A pre-registered count-based estimate** (E7's method).
     - The specification is fixed before running:
       - calibration days only;
       - 15-minute windows, slot fixed effects, free-flow windows;
       - an errors-in-variables correction using the data-quality artifact's
         count error.
     - So is the acceptance rule. For example: adopt only if the 95 % interval
       is narrower than 0.20; otherwise the share stays an uncertain input
       with its range set by that interval.
     - Given §4, expect it to fail that rule.
   - **(c) A direct count.** One AM peak of video at the gore, or an agency
     origin–destination product from MnDOT or the Met Council. This is beyond
     a desk study. It is the measurement that would settle the question.
4. **Unchanged from the diagnosis.**
   - Do not attribute the T.H.52 shortfall to the merge model until the share
     is bounded by data.
   - Do not pick the share that passes.

## 7. The cheapest test

**What exists.**
- `scripts/merge_model_selfcheck.py` cannot vary the share:
  - `th52` runs the fixture's proportional split;
  - `ceiling` relocates every crossing vehicle (p = 1);
  - `--weave-set` sets `weave_params` only.
- The diagnosis's D1 arm,
  `artifacts/weave_loss_2026-10-07/harness/arm.py NAME 3-22 --relocate-p p`,
  needs the HEAD snapshot at `harness/head`.
  - It relocates each crossing vehicle to the other leg with probability p.
  - That approximates a share. It also shifts p · (v_FR − v_RF), about
    p · 177 veh/h, from the mainline onto the ramp at the fixture window.

**The clean change** is a scratch wrapper. It needs no package edit. It works
like `th52_ceiling`, which wraps `runner._build_plan_and_routes` and rewrites
both the routes file and the plan's routes.

- **The swap.** In each 5-minute window of departure, pair entrants bound
  through or to 242A with mainline vehicles bound for 242B, in departure
  order:
  - entrant routes `on<k>` or `on<k>_off<m≠j>`;
  - mainline routes `main_off<j>`.
- **How many.** n_w = round(v_ON,w · (s − P_w) / 12) pairs per window.
- **What changes.** The entrant takes `on<k>_off<j>`, and the mainline vehicle
  takes the entrant's former suffix (`main` or `main_off<m>`).
- **What does not change.** Origins, departure times, departure lanes,
  `lcStrategic`, and the volume of every leg and exit. Only who crosses
  changes.
- **The arms.**
  - s ∈ {proportional, 0.40, 0.50, 0.60, 0.70}.
  - Seeds 3–22, `--fleet-from scenarios/mndot_i94_wb_stpaul_weave_dc.yaml`.
  - Read with `th52_criteria`, each arm paired against proportional.
- **Cost.** 100 fixture runs. D1's arms took about 2 s per run on macOS
  [record, `arms/d1_*.json` `wall_s`]. The fixture is allowed on the laptop.
- **To pre-register.** The arms are a sensitivity of the verdict to the share.
  No value is chosen from them.
- **The peak.** The same wrapper at the corridor's peak demand is the natural
  pairing with I94_RESIDUALS fix 3, which runs the fixture at the 06:30–07:30
  inflows. There, proportional is 0.18 and the swaps remove more crossers
  (§5).

## 8. Limits

- **Sources read only through search results.** E3, E4 and E11 were read
  through search-result excerpts: the pages returned HTTP 429, or the document
  was not reachable.
- **Not read at all.** E2 (video) and E1's report (on request only).
- **§4.** Nine September 2026 weekdays and one station pair. Exploratory and
  not pre-registered, with the biases stated there. The scratch script is not
  committed.
- **The D1 mapping is approximate.** D1 does not keep leg volumes.
- **One weave.** The Ruth St weave's split was not examined.
- **Nine-day demand.** §2.2 uses the nine-day scenario named in the task. The
  calibration-days rebuild in the working tree
  (`scenarios/mndot_i94_wb_stpaul_weave_dc_cal.yaml`, uncommitted) gives the
  same proportional shares: 0.29 in the fixture window and 0.18 at the peak.

## 9. Reproduce

The volumes of §2.2: `observations.json` S790 and rnd_91040, and the scenario's
`exit_fraction` for `off-ramp 18207598`, through HCM Equations 13-2 to 13-6,
averaged over the windows.

The readings of §4, from the repository root
(`uv run --no-sync python -`, about 1 s):

```python
import numpy as np, pandas as pd

df = pd.read_csv("data/mndot/mndot_i94_wb_stpaul/detectors.csv", parse_dates=["timestamp"])
w = df.pivot_table(index="timestamp", columns="station", values="flow_veh_h")
v = df.pivot_table(index="timestamp", columns="station", values="speed_ms")
d = pd.DataFrame(
    {"U": w.S790, "O": w.rnd_91040, "D": w.S97 + w.rnd_87221, "vU": v.S790, "vD": v.S97}
).dropna()
d["day"], d["slot"] = d.index.strftime("%Y%m%d"), d.index.hour * 12 + d.index.minute // 5
d["tod"] = d.index.hour + d.index.minute / 60
ff = d[(d.vU > 20) & (d.vD > 20)]


def agg(s, k):  # k consecutive 5-min windows, complete blocks only
    if k == 1:
        return s[["day", "slot", "U", "O", "D"]].copy()
    g = s.assign(slot=s.slot // k).groupby(["day", "slot"])
    return g[["U", "O", "D"]].mean()[g.size() == k].reset_index()


def est(t, fe):  # returns (theta1, theta2)
    t = t.copy()
    if fe:
        for c in "UOD":
            t[c] -= t.groupby("slot")[c].transform("mean")
    c = np.linalg.lstsq(t[["U", "O"]].values, t.D.values, rcond=None)[0]
    return 1 - c[0], 1 - c[1]


def run(sub, k, fe, nboot=2000):
    t = agg(sub, k)
    if fe:
        t = t[t.groupby("slot").U.transform("size") >= 4]
    days, rng = t.day.unique(), np.random.default_rng(7)
    bs = np.array(
        [
            est(pd.concat([t[t.day == x] for x in rng.choice(days, len(days))]), fe)
            for _ in range(nboot)
        ]
    )
    return est(t, fe), np.percentile(bs, [2.5, 97.5], axis=0)


print(
    run(ff[(ff.tod >= 5) & (ff.tod < 22)], k=3, fe=True)
)  # the bold row: theta2 0.58 [0.44, 0.71]
```

The other rows change the `tod` window, `k` (1 or 3) and `fe`. The
calibration-days row adds
`ff.day.isin({"20260902", "20260903", "20260908", "20260915", "20260916"})`.
