# Discovery interviews — kit (Track C1)

Ten conversations with people who calibrate or review traffic models for a
living, before any more product polish (NEXT_STEPS.md §3.4). No code depends
on this; it runs in parallel with everything else. The last question — "what is
missing?" — is the real product specification. Nothing here is a claim; it is
a script, a target list, and a place to record what was heard.

## 1. Who to talk to (roles, not names)

| Segment | Why they matter | Where they are found |
|---|---|---|
| Traffic-engineering consultants (2–3 people) who build Vissim/Aimsun/SUMO corridor models for DOT/MPO clients | They *do* the two weeks of calibration the wedge replaces; fastest procurement | Firm websites list "microsimulation" and "traffic operations" leads; ITE section meetings; LinkedIn |
| DOT / MPO reviewers (2–3) who accept or reject calibration reports | They set the acceptance bar; the report is written for them | State DOT traffic operations / modeling groups; MPO travel-model staff (NCTCOG for DFW) |
| University lab researchers on traffic flow / CAV control (2) | Credibility, the academic route, and the RL/controller collaboration path | TTI, UT-Austin CTR, Vanderbilt (I-24 MOTION), Berkeley (CIRCLES) |
| Software vendors' users or trainers (1) | They know the workflow pain and the pricing anchors | SUMO user list, PTV/Aimsun training alumni |
| Someone who has run a VSL or connected-vehicle pilot (1) | The actuation-side reality check for anything beyond simulation | DOT ITS groups, FHWA CV pilot participants |

Warm introductions beat cold outreach; the warm paths available are the NASA
mentor network, teachers and the I-24 MOTION data team (after §1 results are
sent to them, ROADMAP B3). Cold outreach template in §4.

## 2. The script (30 minutes; ask, then be quiet)

Open (2 min): who I am, what FlowState does in one sentence — "an open corridor
digital-twin tool that onboards a freeway from OpenStreetMap, calibrates to
public trajectory or detector data, runs seeded controller/VSL experiments and
generates an FHWA-style calibration report" — and that I am here to learn how
they work, not to sell.

1. **Current practice.** Walk me through the last corridor model you calibrated
   or reviewed. What tool, what data, how long did calibration take, and who
   did it?
2. **Cost.** What does a corridor study cost your client or agency, roughly, in
   hours and in licence fees?
3. **The acceptance bar.** What do reviewers actually check? GEH, speeds,
   queue lengths, visual contours? Which state or agency guideline do you cite?
4. **Rejection.** What gets a calibration report sent back? What is the most
   common reason?
5. **Data reality.** Which data do you actually have for a corridor — loop or
   radar detector counts, probe speeds, trajectory data? How do you handle
   detector coverage gaps or missing ramp counts?
6. **Emergent waves.** Do your models reproduce stop-and-go waves, or do you
   not need them to? Has a client ever asked about smoothing controllers,
   connected vehicles or variable speed limits?
7. **The auto-report.** (Show the US-101 or I-24 report for two minutes.) Would
   a report generated like this be usable in your workflow — as a draft, as an
   appendix, as the deliverable? What would a reviewer say about it?
8. **Trust.** What would make you trust an open-source engine's result enough
   to sign your name to it?
9. **Willingness.** If this existed as a hosted tool, would you trial it on a
   real corridor? What would it have to do first?
10. **What is missing?** If you had this tomorrow, what is the first thing you
    would need that it does not do?

Close: may I follow up with the report once the I-24 validation is written up;
who else should I talk to?

## 3. What to record (one page per interview, `docs/interviews/<date>-<role>.md`, not committed if confidential)

- Role, organisation type, tool(s), corridor types, years.
- Calibration duration and cost as stated (their numbers, their words).
- Acceptance criteria and guideline cited; rejection reasons.
- Data available; how coverage gaps are handled.
- Verbatim reaction to the report; the "missing" answer verbatim.
- Trial willingness: yes / maybe / no, and the condition attached.
- Introductions offered.

Tally after ten: count of "would trial" ≥ 2 keeps the product track alive
(NEXT_STEPS.md §4 kill-criterion); the most frequent "missing" answer becomes
the next roadmap item.

## 4. Outreach template (adapt; never send unread)

> Subject: 30 minutes on how you calibrate corridor models?
>
> I am a student building FlowState, an open-source corridor digital-twin tool
> (SUMO-based, calibrated to public trajectory data, auto-generated FHWA-style
> calibration reports; repository: github.com/abc000cool/flowstate-v2). Before
> I build more, I want to understand how practitioners actually calibrate and
> review corridor models today. Could I ask you ten questions in a 30-minute
> call? I will send the write-up of our I-24 MOTION validation afterwards
> either way.

## 5. What we can show, honestly

- The US-101 report (docs/reports/us101_replica/) and its honest 1-pass /
  5-fail criteria table.
- The I-24 MOTION replica: 3.4 miles of real geometry with ramps, 17,652-episode
  calibration, and the criteria battery in docs/I24_VALIDATION.md — including
  the tracking-coverage limitation that makes counts lower bounds.
- The `docker compose up` demo (Track A3).

Do not show or claim: a validated corridor pass, advisory delivery, or any
number without its confidence interval.
