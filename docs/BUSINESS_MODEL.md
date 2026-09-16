# FlowState business model: two paths, one wedge

*Written 2026-09-15 for the investor conversations. Every figure is either a
measured cost from this repository, a public anchor named in the Sources
section, or a hypothesis labelled as one. The spreadsheets under
`docs/business/` carry the arithmetic with live formulas; change an input on
the Assumptions sheet and everything downstream moves.*

## 1. What we sell, in one paragraph

FlowState sells the answer to one question that every transport agency,
planning body and consultancy has to answer before it changes a busy road:
*what will this do to the traffic?* Today that answer is a microsimulation
model built by hand over weeks, calibrated against field data by a senior
engineer, and reviewed against federal or state acceptance criteria. FlowState
automates the building, the calibration and the report, from public map data
and the agency's own traffic data, and it is the only tool whose model
reproduces the stop-and-go waves that make congestion expensive. That is Path
A. Path B is the same calibrated twin kept alive with live sensor data, giving
the agency a recommended speed per segment while a wave is on the road.

## 2. Path A: corridor studies and the digital-twin workspace

**The customer's problem.** A corridor study needs a calibrated model. The
commercial simulators (PTV Vissim, Aimsun, TransModeler) are licensed at five
figures per seat per year, the calibration is manual expert work measured in
weeks per corridor, and a rejected calibration report is a common, expensive
event. Consultants bill that work at senior rates: HNTB's published schedule
runs $115 to $155 per hour for Engineer I/II and $226 to $250 for a chief
planner; $200 per hour is a fair blended senior rate. Two weeks of that is
$16,000 per corridor before the study itself starts.

**What we sell.** Per corridor: onboarding from OpenStreetMap, driver-model
and demand calibration to the customer's data, the scenario runs (smoothing
vehicles, variable speed limits, ramp metering, closures, trucks, HOV rules,
twenty seeds each with confidence intervals) and the FHWA-style calibration
and validation report. Then a yearly workspace for firms that do this every
month.

**How we sell it (B2B, in this order).**

1. *Consulting firms first.* They do the weeks of hand work today, they buy
   fast, and the pain is acute. Sell per study at $1,500 to $5,000, then move
   the repeat buyers to a $10,000 to $30,000 yearly workspace. Channel: ITE
   section meetings, the TRB Annual Meeting, direct outreach to the modeling
   leads (the outreach list has 35 firms).
2. *University labs* at free to $5,000 a year for credibility, the preprint
   and introductions; the CIRCLES and I-24 MOTION groups are the warm path.
3. *Agency pilots* at $25,000 to $75,000: their corridor, their detector data,
   a report they can submit. Slower procurement, larger contracts, and the
   only route to the data that makes a validated corridor possible.
4. *Vehicle fleets and OEMs* once one pilot is running: the dose-response on
   a calibrated real corridor is the result they need before deploying
   smoothing at scale.

**Unit economics (base inputs).** A study costs us about $8 of cloud time
(five machine-hours at $1.55), a day of corridor-specific engineering, four
hours of contract review and a few hours of selling: roughly $1,300 of cost
against a $3,500 price at founder-stipend labour rates, a gross margin around
60 percent that rises as the corridor-specific engineering keeps turning into
code. A pilot at $50,000 carries about $6,000 of delivery cost. A workspace
year at $20,000 costs about $1,900 to host and support. The customer's
alternative is $16,000 of senior time per corridor, so the study price is a
fraction of what it replaces; that gap is the wedge.

**Cost to build the product path.** From the dossier's timeline: a validated
corridor (4 to 8 weeks plus compute), the capacity-aware controller (3 to 6
weeks), pilot readiness (3 to 5 weeks), the first pilot (6 to 10 weeks) and
the multi-tenant product. At founder-stipend cost that is a low six-figure
build; at market salaries for three engineers, three to four times that.
Cloud is a rounding error: the September 2026 calibration round cost about
$13 across two VMs.

**Year one, sized to the evidence.** Three to five consultancy studies, one
agency pilot around I-24, two university users and the preprint: tens of
thousands of dollars of revenue. The P&L sheet carries a base case of
4 / 15 / 40 studies, 1 / 3 / 6 pilots and 0 / 3 / 10 workspaces over three
years; halve everything for the conservative case.

## 3. Path B: live speed advisory

**The idea.** Keep the calibrated twin running against live data from the
corridor (roadside radar or thermal detectors, or the probe-speed feed the
agency already licenses), estimate the traffic state in real time, and post a
recommended speed per segment until the wave has cleared, through the
agency's own signs or a connected-vehicle channel run by a partner. It is the
variable-speed-limit product controller of the specification, driven by a
calibrated model instead of a fixed threshold table.

**What it is not.** Not a consumer app that pushes advisories to drivers'
phones. The specification rules that out (CLAUDE.md section 0.4) and the
agencies we sell to own the signs; the advisory reaches drivers through them
or through an OEM partner.

**What it costs the agency.** Sensing dominates. The USDOT ITS Costs Database
puts a roadside microwave (radar) detector site at $5,000 on an existing pole
to $11,500 with a new one; at half-mile spacing in both directions a ten-mile
corridor is about 40 sites, or $200,000 to $460,000 of capital. Where the
agency already buys probe speeds, the I-95 Corridor Coalition's original
contract was about $750 per centreline mile per year and Michigan's statewide
feed about $250,000 a year, so a ten-mile corridor's data can cost under
$10,000 a year with no hardware. New overhead gantries are the expensive
part: Tennessee's I-24 SMART Corridor installed 67 gantries at half-mile
spacing for a reported $45 to $64 million, roughly $0.7 to $1 million each.
Path B therefore sells into corridors that already have signs, or through
connected-vehicle channels, and never proposes to build gantries.

**What we charge.** A subscription per corridor, hypothesis $60,000 a year,
priced under the agency's probe-data spend plus one engineer-month. Our cost
to serve is hosting and about a week of engineering a quarter, a gross margin
above 90 percent at scale.

**What it needs first.** The real-time state-estimation tier (a cell
transmission model with a Kalman filter, designed in the specification and
not yet built), a validated corridor twin, sensor and probe-data ingestion,
the advisory interface, and one agency willing to connect its sensors. About
nine engineer-months on our side.

**Why keep both paths open.** Path A is sellable now and is the only route to
the data and the relationships Path B needs. Path B is where the recurring
revenue and the moat are: a live, calibrated model of a specific road that an
agency runs its operations on is something a customer cannot rip out. The
conversations with investors and agencies decide the order; the model runs
both.

## 4. Path A versus Path B

| | Path A: studies and workspace | Path B: live speed advisory |
|---|---|---|
| Buyer | Consultancies, then agencies | Agency operations (TMC), OEM partner |
| Revenue shape | Per study, then annual workspace | Annual subscription per corridor |
| Price hypothesis | $1,500 to $5,000 per study; $10,000 to $30,000 per year | $60,000 per corridor per year |
| Our cost to serve | About $1,300 per study; about $1,900 per workspace-year | About $4,700 per corridor-year |
| Customer's capital | None | $0 (probe data) to $460,000 (radar on a ten-mile corridor); gantries excluded |
| Time to first revenue | Weeks | Nine engineer-months plus an agency partner |
| Evidence status | Pipeline runs end to end; corridor not yet validated | Estimator tier designed, not built |
| Moat | Published validation record, calibration automation | A live model the agency operates on |

## 5. What would change the numbers

A validated corridor turns a method into a prediction an agency can act on
and moves every price up a tier. A capacity-aware smoothing controller with a
published dose-response is the result fleets and agencies both want. Hosted
multi-tenant turns studies into subscriptions. The interview kit
(`docs/INTERVIEWS.md`) exists to test the price hypotheses; none has been
tested yet, and the model says so.

## 6. Files

- `docs/business/flowstate_cost_model.xlsx`: Assumptions (all inputs), Cost
  to build, Unit economics A, Path B live advisory, P&L 3 years, Sources.
  Generated by `scripts/business_cost_model.py`.
- `docs/business/outreach_targets.xlsx` and `.csv`: 200 organisations in
  seven segments with the role to reach, the channel and a pitch angle.
  Organisations only; no personal e-mail addresses are invented. Generated by
  `scripts/business_outreach_list.py`.
- The website's Business page (`business.html` in the site repository)
  summarises this document and links to the spreadsheets.

## Sources

- Consultant rates: HNTB Corporation fee schedule filed with the Wisconsin
  Division of Facilities Development; Kimley-Horn 2024 rate schedule filed
  with the City of Chula Vista.
- Calibration effort and acceptance: FHWA Traffic Analysis Toolbox Volume
  III, 2019 update, FHWA-HOP-18-036; FlowState dossier section 7.
- Radar detector unit costs: USDOT ITS Costs Database, Roadside Detection,
  Remote Traffic Microwave Sensor on Corridor.
- Probe data costs: USDOT ITS Knowledge Resources 2014-SC00327 (I-95 Corridor
  Coalition, Michigan, NCDOT comparison); INRIX press releases.
- I-24 SMART Corridor gantries and cost: Tennessee DOT project page;
  NewsChannel 5, WKRN and Roads and Bridges reports (2023 to 2025).
- Market counts: AMPO MPO 101 brief (400+ MPOs); 52 state DOTs.
- Cloud costs and machine hours: this repository, `scripts/gcp/README.md`
  and `docs/I24_SWEEP.md`.
- Price hypotheses: FlowState dossier section 8.1.
