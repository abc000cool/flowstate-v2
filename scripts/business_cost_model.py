"""FlowState cost, price and revenue model as a spreadsheet with live formulas.

Writes ``docs/business/flowstate_cost_model.xlsx``: Assumptions, Cost to build,
Unit economics (path A), Path B live advisory, P&L scenarios (three years),
Sources. Every number on the Assumptions sheet is an input the reader can change;
the other sheets are formulas. Anchors come from public sources listed on the
Sources sheet and from the repository's own measured compute costs. Run::

    uv run --no-sync --with openpyxl python scripts/business_cost_model.py
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "business" / "flowstate_cost_model.xlsx"

HEAD = PatternFill("solid", fgColor="123B6D")
INPUT = PatternFill("solid", fgColor="FFF4D6")
CALC = PatternFill("solid", fgColor="EEF4FB")
BOLD = Font(bold=True)
WHITE = Font(bold=True, color="FFFFFF")
MONEY = '"$"#,##0'
PCT = "0%"


def header(ws, row, labels):
    for j, v in enumerate(labels, 1):
        c = ws.cell(row=row, column=j, value=v)
        c.font = WHITE
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")


def widths(ws, ws_widths):
    for i, w in enumerate(ws_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


wb = Workbook()

# ------------------------------------------------------------------ Assumptions
A = wb.active
A.title = "Assumptions"
header(A, 1, ["Input", "Value", "Unit", "Basis (see Sources)"])
assumptions = [
    ("PEOPLE", None, None, None),
    (
        "Founder / engineer fully loaded cost",
        8000,
        "$ per person-month",
        "Early-stage founder stipend plus tools; replace with real salaries when hiring",
    ),
    (
        "Engineers on the product (year 1)",
        2,
        "people",
        "Two of the three founders on engineering, one on customers",
    ),
    ("Customer-facing founder", 1, "people", ""),
    (
        "Contract traffic engineer (review)",
        150,
        "$ per hour",
        "HNTB 2024 rate schedule: Engineer I/II $115 to $155 per hour",
    ),
    ("COMPUTE AND HOSTING", None, None, None),
    (
        "Cloud VM, 32 vCPU on demand",
        1.55,
        "$ per hour",
        "Measured: n2-standard-32, us-west1, September 2026 runs",
    ),
    (
        "Machine hours per corridor study (calibration + 20-seed battery)",
        5.0,
        "hours",
        "Measured: I-24 battery 1.5 to 3.4 h; allow 5 h with reruns",
    ),
    (
        "Machine hours per controller sweep (add-on)",
        4.0,
        "hours",
        "Measured: headway-cap sweep 2.8 h; 500-run sweep about 7 h",
    ),
    (
        "Hosting per customer workspace",
        60,
        "$ per month",
        "Small always-on VM plus object storage; single-tenant",
    ),
    ("Shared infrastructure (CI, domains, monitoring)", 150, "$ per month", "Estimate"),
    (
        "Storage per study kept for a year",
        1.0,
        "$ per study",
        "About 2 GB per study at object-storage prices",
    ),
    ("LABOUR PER STUDY", None, None, None),
    (
        "Engineer hours per study, corridor-specific decisions",
        8,
        "hours",
        "Measured on I-24: about one working day; the reusable part is now code",
    ),
    (
        "Engineer hours per new data format (loader)",
        8,
        "hours",
        "About a day of engineering (dossier)",
    ),
    ("Review hours per study (contract engineer)", 4, "hours", "Estimate"),
    ("Sales hours per closed study", 6, "hours", "Estimate: two calls and a proposal"),
    ("PRICES, PATH A (studies and workspace)", None, None, None),
    (
        "Price per corridor study, consultancy",
        3500,
        "$",
        "Dossier hypothesis: $1,500 to $5,000; mid-point",
    ),
    ("Price per agency pilot", 50000, "$", "Dossier hypothesis: $25,000 to $75,000"),
    (
        "Annual workspace, consultancy",
        20000,
        "$ per year",
        "Dossier hypothesis: $10,000 to $30,000",
    ),
    ("Annual lab licence", 2500, "$ per year", "Free to $5,000"),
    ("Data onboarding, new format", 3500, "$", "$2,000 to $5,000 per format"),
    ("Controller sweep add-on", 2500, "$", "Priced on compute plus review"),
    (
        "What the study replaces (engineer weeks by hand)",
        2,
        "weeks",
        "Dossier: weeks of expert time per corridor",
    ),
    ("Hours per week billed", 40, "hours", ""),
    (
        "Consultant billing rate, senior modeler",
        200,
        "$ per hour",
        "HNTB Chief Planner $226 to $250; Engineer II $141 to $155; $200 as a blended senior rate",
    ),
    ("PRICES AND COSTS, PATH B (live speed advisory)", None, None, None),
    (
        "Corridor length",
        10,
        "miles",
        "A study corridor like the I-24 SMART Corridor section (17 miles) or half of it",
    ),
    (
        "Radar detector sites per mile (both directions)",
        2,
        "sites per mile",
        "Half-mile spacing like the SMART Corridor gantries",
    ),
    (
        "Radar detector installed cost per site",
        11500,
        "$ per site",
        "ITS Costs Database: $5,000 (existing pole) to $11,500 (new pole) per site",
    ),
    ("Radar detector O&M per site per year", 800, "$ per year", "Estimate: 7 percent of capital"),
    (
        "Probe speed data feed, per centreline mile per year",
        750,
        "$ per mile-year",
        "I-95 Corridor Coalition contract about $750 per centreline mile; Michigan $250,000 statewide",
    ),
    (
        "Edge compute and comms per corridor per month",
        400,
        "$ per month",
        "One small server or cloud instance plus cellular backhaul",
    ),
    (
        "New VSL gantry, all-in",
        900000,
        "$ per gantry",
        "TDOT I-24 SMART Corridor: 67 gantries, reported $45M to $64M",
    ),
    (
        "Gantries needed if the corridor has none",
        0,
        "gantries",
        "Set to 0 to sell into corridors that already have signs or use connected-vehicle channels",
    ),
    (
        "Annual subscription per corridor, live advisory",
        60000,
        "$ per year",
        "Hypothesis: priced under an agency's probe-data spend plus one engineer-month",
    ),
    (
        "Engineering months to a first live corridor",
        9,
        "person-months",
        "Estimator tier (macro CTM plus Kalman) is designed but not built; sensing integration; agency approvals",
    ),
    ("VOLUMES BY YEAR (base case)", None, None, None),
    ("Studies sold, year 1", 4, "studies", "Dossier year-one plan: three to five"),
    ("Studies sold, year 2", 15, "studies", ""),
    ("Studies sold, year 3", 40, "studies", ""),
    ("Agency pilots, year 1", 1, "pilots", "One pilot around I-24"),
    ("Agency pilots, year 2", 3, "pilots", ""),
    ("Agency pilots, year 3", 6, "pilots", ""),
    ("Workspaces, year 1", 0, "subscriptions", ""),
    ("Workspaces, year 2", 3, "subscriptions", ""),
    ("Workspaces, year 3", 10, "subscriptions", ""),
    ("Lab licences, year 1", 2, "licences", "Dossier: two university users"),
    ("Lab licences, year 2", 4, "licences", ""),
    ("Lab licences, year 3", 6, "licences", ""),
    (
        "Live-advisory corridors, year 1",
        0,
        "corridors",
        "Path B needs the estimator tier and an agency partner first",
    ),
    ("Live-advisory corridors, year 2", 1, "corridors", ""),
    ("Live-advisory corridors, year 3", 3, "corridors", ""),
]
name_to_cell: dict[str, str] = {}
r = 2
for label, val, unit, basis in assumptions:
    if val is None:
        c = A.cell(row=r, column=1, value=label)
        c.font = BOLD
        c.fill = CALC
        r += 1
        continue
    A.cell(row=r, column=1, value=label)
    v = A.cell(row=r, column=2, value=val)
    v.fill = INPUT
    if isinstance(val, (int, float)) and (unit or "").startswith("$"):
        v.number_format = MONEY if float(val).is_integer() else '"$"#,##0.00'
    A.cell(row=r, column=3, value=unit)
    A.cell(row=r, column=4, value=basis)
    name_to_cell[label] = f"Assumptions!$B${r}"
    r += 1
widths(A, [58, 14, 20, 90])
A.freeze_panes = "A2"


def ref(label: str) -> str:
    return name_to_cell[label]


# ------------------------------------------------------------------ Cost to build
B = wb.create_sheet("Cost to build")
header(
    B,
    1,
    [
        "Phase (dossier timeline)",
        "Person-months",
        "Cloud hours",
        "People cost",
        "Cloud cost",
        "Other",
        "Total",
        "Depends on",
    ],
)
phases = [
    (
        "Validated corridor: radar counts, merge behaviour, battery rerun, second corridor",
        2.0,
        20,
        0,
        "Radar counts; PeMS account",
    ),
    ("Capacity-aware controller and its sweep", 1.5, 12, 0, "Calibrated corridor"),
    (
        "Pilot readiness: auth, per-user projects, hosted deployment, versioned release",
        1.5,
        5,
        1500,
        "Hosting and identity decisions",
    ),
    (
        "First agency pilot: their corridor, their data, a submittable report",
        2.5,
        10,
        2000,
        "A champion at the agency",
    ),
    (
        "Product: multi-tenant hosting, billing, onboarding, documentation site",
        3.0,
        10,
        3000,
        "Paying users",
    ),
    (
        "Path B foundation: state estimator tier (CTM + Kalman), sensor and probe-data ingestion, advisory interface",
        9.0,
        60,
        15000,
        "An agency partner with sensors or signs",
    ),
]
for i, (name, pm, hrs, other, dep) in enumerate(phases, start=2):
    B.cell(row=i, column=1, value=name)
    B.cell(row=i, column=2, value=pm).fill = INPUT
    B.cell(row=i, column=3, value=hrs).fill = INPUT
    B.cell(
        row=i, column=4, value=f"=B{i}*{ref('Founder / engineer fully loaded cost')}"
    ).number_format = MONEY
    B.cell(
        row=i, column=5, value=f"=C{i}*{ref('Cloud VM, 32 vCPU on demand')}"
    ).number_format = MONEY
    B.cell(row=i, column=6, value=other).number_format = MONEY
    B.cell(row=i, column=6).fill = INPUT
    B.cell(row=i, column=7, value=f"=D{i}+E{i}+F{i}").number_format = MONEY
    B.cell(row=i, column=8, value=dep)
last = len(phases) + 1
B.cell(row=last + 1, column=1, value="Path A to product (first five phases)").font = BOLD
B.cell(row=last + 1, column=2, value=f"=SUM(B2:B{last - 1})")
B.cell(row=last + 1, column=7, value=f"=SUM(G2:G{last - 1})").number_format = MONEY
B.cell(row=last + 2, column=1, value="Path A plus Path B foundation").font = BOLD
B.cell(row=last + 2, column=2, value=f"=SUM(B2:B{last})")
B.cell(row=last + 2, column=7, value=f"=SUM(G2:G{last})").number_format = MONEY
B.cell(
    row=last + 4,
    column=1,
    value="Reading it: at founder-stipend cost the whole software path is a low six-figure build; the same plan with three market-rate engineers is roughly three to four times that. Cloud is a rounding error: the measured runs cost single-digit dollars each.",
)
widths(B, [80, 14, 12, 14, 12, 12, 14, 40])

# ------------------------------------------------------------------ Unit economics, path A
U = wb.create_sheet("Unit economics A")
header(U, 1, ["Item", "Per corridor study", "Per agency pilot", "Per workspace-year", "Note"])
rows_u = [
    (
        "Price",
        f"={ref('Price per corridor study, consultancy')}",
        f"={ref('Price per agency pilot')}",
        f"={ref('Annual workspace, consultancy')}",
        "Assumptions",
    ),
    (
        "Cloud compute",
        f"={ref('Machine hours per corridor study (calibration + 20-seed battery)')}*{ref('Cloud VM, 32 vCPU on demand')}",
        f"=3*{ref('Machine hours per corridor study (calibration + 20-seed battery)')}*{ref('Cloud VM, 32 vCPU on demand')}+{ref('Machine hours per controller sweep (add-on)')}*{ref('Cloud VM, 32 vCPU on demand')}",
        f"=12*{ref('Hosting per customer workspace')}+10*{ref('Machine hours per corridor study (calibration + 20-seed battery)')}*{ref('Cloud VM, 32 vCPU on demand')}",
        "A pilot is about three studies plus a sweep; a workspace assumes ten studies a year",
    ),
    (
        "Storage",
        f"={ref('Storage per study kept for a year')}",
        f"=3*{ref('Storage per study kept for a year')}",
        f"=10*{ref('Storage per study kept for a year')}",
        "",
    ),
    (
        "Engineer time (founder cost)",
        f"={ref('Engineer hours per study, corridor-specific decisions')}/160*{ref('Founder / engineer fully loaded cost')}",
        f"=(3*{ref('Engineer hours per study, corridor-specific decisions')}+{ref('Engineer hours per new data format (loader)')}+40)/160*{ref('Founder / engineer fully loaded cost')}",
        f"=(20)/160*{ref('Founder / engineer fully loaded cost')}",
        "Pilot adds a loader and about a week of on-site and report work; workspace support 20 h a year",
    ),
    (
        "Review (contract engineer)",
        f"={ref('Review hours per study (contract engineer)')}*{ref('Contract traffic engineer (review)')}",
        f"=3*{ref('Review hours per study (contract engineer)')}*{ref('Contract traffic engineer (review)')}",
        "=0",
        "Workspace customers review their own studies",
    ),
    (
        "Sales time (founder cost)",
        f"={ref('Sales hours per closed study')}/160*{ref('Founder / engineer fully loaded cost')}",
        f"=40/160*{ref('Founder / engineer fully loaded cost')}",
        f"=16/160*{ref('Founder / engineer fully loaded cost')}",
        "A pilot takes about a week of selling across the procurement cycle",
    ),
]
for i, (name, a, b, c, note) in enumerate(rows_u, start=2):
    U.cell(row=i, column=1, value=name)
    for j, f in enumerate((a, b, c), start=2):
        cell = U.cell(row=i, column=j, value=f)
        cell.number_format = MONEY
    U.cell(row=i, column=5, value=note)
n = len(rows_u) + 1
U.cell(row=n + 1, column=1, value="Cost of delivery").font = BOLD
for j in (2, 3, 4):
    col = get_column_letter(j)
    U.cell(row=n + 1, column=j, value=f"=SUM({col}3:{col}{n})").number_format = MONEY
U.cell(row=n + 2, column=1, value="Gross margin").font = BOLD
for j in (2, 3, 4):
    col = get_column_letter(j)
    U.cell(row=n + 2, column=j, value=f"=({col}2-{col}{n + 1})/{col}2").number_format = PCT
U.cell(row=n + 3, column=1, value="What the customer pays today for the same work").font = BOLD
U.cell(
    row=n + 3,
    column=2,
    value=f"={ref('What the study replaces (engineer weeks by hand)')}*{ref('Hours per week billed')}*{ref('Consultant billing rate, senior modeler')}",
).number_format = MONEY
U.cell(
    row=n + 3,
    column=5,
    value="Two weeks of a senior modeler at a blended $200 per hour; the study price is a fraction of it, which is the wedge",
)
U.cell(row=n + 4, column=1, value="Customer saving per study").font = BOLD
U.cell(row=n + 4, column=2, value=f"=B{n + 3}-B2").number_format = MONEY
widths(U, [44, 18, 18, 18, 90])

# ------------------------------------------------------------------ Path B
P = wb.create_sheet("Path B live advisory")
header(P, 1, ["Item", "Per corridor", "Note"])
rows_p = [
    ("Corridor length (miles)", f"={ref('Corridor length')}", ""),
    (
        "Radar sites",
        f"=B2*{ref('Radar detector sites per mile (both directions)')}",
        "Half-mile spacing, both directions",
    ),
    (
        "Sensing capital (radar, installed)",
        f"=B3*{ref('Radar detector installed cost per site')}",
        "ITS Costs Database range; existing poles cut it in half",
    ),
    ("Sensing O&M per year", f"=B3*{ref('Radar detector O&M per site per year')}", ""),
    (
        "Probe data feed per year (alternative or complement to radar)",
        f"=B2*{ref('Probe speed data feed, per centreline mile per year')}",
        "Buy speeds instead of installing sensors where the agency already licenses probe data",
    ),
    (
        "Edge compute and comms per year",
        f"=12*{ref('Edge compute and comms per corridor per month')}",
        "",
    ),
    (
        "New gantries (only if the corridor has none)",
        f"={ref('Gantries needed if the corridor has none')}*{ref('New VSL gantry, all-in')}",
        "The SMART Corridor spent about $0.7M to $1M per gantry; the software path avoids this by using existing signs or connected-vehicle channels",
    ),
    ("Capital, corridor with existing signs", "=B4+B8", ""),
    (
        "Operating cost per year",
        "=B5+B7+B6",
        "Radar O&M plus probe feed plus compute (drop the probe feed if radar alone)",
    ),
    (
        "FlowState subscription per year",
        f"={ref('Annual subscription per corridor, live advisory')}",
        "Hypothesis",
    ),
    (
        "FlowState cost to serve per year (hosting, estimator, one engineer-week a quarter)",
        f"=12*{ref('Hosting per customer workspace')}+4*40/160*{ref('Founder / engineer fully loaded cost')}",
        "",
    ),
    ("Gross margin on the subscription", "=(B11-B12)/B11", ""),
    (
        "Agency's first-year outlay (capital + operating + subscription)",
        "=B9+B10+B11",
        "Who pays for sensing is the deal structure question",
    ),
    (
        "One-time build to reach a first live corridor (FlowState side)",
        f"={ref('Engineering months to a first live corridor')}*{ref('Founder / engineer fully loaded cost')}",
        "Estimator tier, ingestion, advisory interface, approvals",
    ),
]
for i, (name, f, note) in enumerate(rows_p, start=2):
    P.cell(row=i, column=1, value=name)
    c = P.cell(row=i, column=2, value=f)
    c.number_format = PCT if "margin" in name else MONEY
    P.cell(row=i, column=3, value=note)
P.cell(
    row=len(rows_p) + 3,
    column=1,
    value="What Path B is: sensors or probe data feed a live state estimate of the corridor; the calibrated twin turns it into a recommended speed per segment until the wave clears; the advisory reaches drivers through the agency's signs or a connected-vehicle channel run by a partner, never a consumer app of ours (CLAUDE.md section 0.4). It is a subscription, not a study, and its cost is dominated by the agency's sensing, not by us.",
)
P.cell(
    row=len(rows_p) + 4,
    column=1,
    value="What it needs first: the state-estimation tier (designed, not built), a validated corridor twin, and one agency willing to connect its sensors. Price it under the agency's probe-data spend and one engineer-month a year.",
)
widths(P, [70, 18, 100])

# ------------------------------------------------------------------ P&L
L = wb.create_sheet("P&L 3 years")
header(L, 1, ["Line", "Year 1", "Year 2", "Year 3", "Note"])
years = ["year 1", "year 2", "year 3"]


def vol(kind: str, y: str) -> str:
    return ref(f"{kind}, {y}")


pl_rows = [
    ("REVENUE, PATH A", None),
    (
        "Corridor studies",
        [
            f"={vol('Studies sold', y)}*{ref('Price per corridor study, consultancy')}"
            for y in years
        ],
    ),
    (
        "Agency pilots",
        [f"={vol('Agency pilots', y)}*{ref('Price per agency pilot')}" for y in years],
    ),
    (
        "Workspaces",
        [f"={vol('Workspaces', y)}*{ref('Annual workspace, consultancy')}" for y in years],
    ),
    ("Lab licences", [f"={vol('Lab licences', y)}*{ref('Annual lab licence')}" for y in years]),
    ("REVENUE, PATH B", None),
    (
        "Live-advisory subscriptions",
        [
            f"={vol('Live-advisory corridors', y)}*{ref('Annual subscription per corridor, live advisory')}"
            for y in years
        ],
    ),
    ("Total revenue", ["=SUM(B3:B6)+B8", "=SUM(C3:C6)+C8", "=SUM(D3:D6)+D8"]),
    ("COST OF DELIVERY", None),
    (
        "Studies (cloud, storage, review, engineer time)",
        [f"={vol('Studies sold', y)}*'Unit economics A'!$B$8" for y in years],
    ),
    ("Pilots", [f"={vol('Agency pilots', y)}*'Unit economics A'!$C$8" for y in years]),
    ("Workspaces", [f"={vol('Workspaces', y)}*'Unit economics A'!$D$8" for y in years]),
    (
        "Live-advisory cost to serve",
        [f"={vol('Live-advisory corridors', y)}*'Path B live advisory'!$B$12" for y in years],
    ),
    ("Gross profit", ["=B9-SUM(B11:B14)", "=C9-SUM(C11:C14)", "=D9-SUM(D11:D14)"]),
    ("OPERATING COST", None),
    (
        "People (engineering + customer founder)",
        [
            f"=12*({ref('Engineers on the product (year 1)')}+{ref('Customer-facing founder')})*{ref('Founder / engineer fully loaded cost')}",
            f"=12*({ref('Engineers on the product (year 1)')}+{ref('Customer-facing founder')}+1)*{ref('Founder / engineer fully loaded cost')}",
            f"=12*({ref('Engineers on the product (year 1)')}+{ref('Customer-facing founder')}+3)*{ref('Founder / engineer fully loaded cost')}",
        ],
    ),
    (
        "Shared infrastructure",
        [f"=12*{ref('Shared infrastructure (CI, domains, monitoring)')}"] * 3,
    ),
    (
        "Build programme cloud (from Cost to build)",
        [
            "='Cost to build'!E2+'Cost to build'!E3+'Cost to build'!E4",
            "='Cost to build'!E5+'Cost to build'!E6+'Cost to build'!E7*0.5",
            "='Cost to build'!E7*0.5",
        ],
    ),
    ("Travel, conferences, legal, insurance", [6000, 15000, 30000]),
    ("Operating profit", ["=B15-SUM(B17:B20)", "=C15-SUM(C17:C20)", "=D15-SUM(D17:D20)"]),
    ("Cumulative cash need", ["=MIN(0,B21)", "=B22+MIN(0,C21)", "=C22+MIN(0,D21)"]),
]
row = 2
for name, vals in pl_rows:
    L.cell(row=row, column=1, value=name)
    if vals is None:
        L.cell(row=row, column=1).font = BOLD
        L.cell(row=row, column=1).fill = CALC
    else:
        for j, v in enumerate(vals, start=2):
            c = L.cell(row=row, column=j, value=v)
            c.number_format = MONEY
            if isinstance(v, (int, float)):
                c.fill = INPUT
        if name in ("Total revenue", "Gross profit", "Operating profit", "Cumulative cash need"):
            L.cell(row=row, column=1).font = BOLD
    row += 1
L.cell(
    row=row + 1,
    column=1,
    value="Base case volumes live on the Assumptions sheet; change them there. Year-one revenue is tens of thousands of dollars, which matches the dossier's own sizing; the model is not a forecast, it is the arithmetic the conversation should test.",
)
L.cell(
    row=row + 2,
    column=1,
    value="Scenario guide: conservative = halve every volume; upside = double year-2 and year-3 studies and add two live corridors in year 3. Both are one edit each on the Assumptions sheet.",
)
widths(L, [56, 16, 16, 16, 90])

# ------------------------------------------------------------------ Sources
Src = wb.create_sheet("Sources")
header(Src, 1, ["Anchor", "Source"])
sources = [
    (
        "Cloud cost $1.55 per hour, 32 vCPUs; studies 1.5 to 3.4 machine hours; sweeps 2.8 to 7 hours",
        "FlowState repository, scripts/gcp/README.md and docs/I24_SWEEP.md (measured, September 2026)",
    ),
    (
        "Consultant rates: Engineer I $115 to $127, Engineer II $141 to $155, Chief Planner $226 to $250 per hour",
        "HNTB Corporation consultant fee schedule (Wisconsin Division of Facilities Development, 2009-10 rate sheet; 2024 schedules on file with California cities such as Chula Vista for Kimley-Horn)",
    ),
    (
        "Two weeks of expert calibration per corridor; rejected reports are common",
        "FlowState technical dossier section 7.1; FHWA Traffic Analysis Toolbox Volume III, 2019 update (FHWA-HOP-18-036)",
    ),
    (
        "Radar detector site $5,000 (existing pole) to $11,500 (new pole)",
        "USDOT ITS Costs Database, Roadside Detection, Remote Traffic Microwave Sensor on Corridor (itscosts.its.dot.gov / itskrs.its.dot.gov)",
    ),
    (
        "Probe data: about $750 per centreline mile (I-95 Corridor Coalition original contract); $250,000 statewide (Michigan); INRIX at about 25 percent of NCDOT's previous $50,000 per mile life-cycle cost",
        "USDOT ITS Knowledge Resources 2014-SC00327; INRIX press releases",
    ),
    (
        "I-24 SMART Corridor: 67 gantries at half-mile spacing between mile markers 53 and 70; reported cost $45M to $64M",
        "Tennessee DOT project page; NewsChannel 5 and WKRN reports; Roads and Bridges",
    ),
    ("400+ MPOs; 52 state DOTs", "AMPO MPO 101 brief; FHWA"),
    (
        "Price hypotheses: $1,500 to $5,000 per study; $10,000 to $30,000 workspace; $25,000 to $75,000 pilot; $2,000 to $5,000 per data format",
        "FlowState technical dossier section 8.1 (hypotheses to test in interviews)",
    ),
    (
        "Commercial simulator licences are five figures per seat",
        "Vendor pricing is not public (PTV pricing page); dossier estimate from practitioner reports",
    ),
]
for i, (a, b) in enumerate(sources, start=2):
    Src.cell(row=i, column=1, value=a)
    Src.cell(row=i, column=2, value=b)
widths(Src, [90, 110])
for ws in wb.worksheets:
    for row_cells in ws.iter_rows():
        for c in row_cells:
            if c.alignment is None or not c.alignment.wrap_text:
                c.alignment = Alignment(wrap_text=True, vertical="top")

OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print(f"-> {OUT}")
