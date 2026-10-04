# Data request for a FlowState corridor study (Frisco pilot)

Written 2026-10-04 (Stage 1 of the Frisco plan, item 3 and the data half of
items 2 and 8). This is the list to send to a traffic agency before a pilot
study. It is written so that agency staff can answer it item by item. Items
marked **required** are needed for any study; the others make the study
more accurate, and we say what we do without each.

Jurisdiction note for our side: the Dallas North Tollway and SH 121 (Sam
Rayburn Tollway) are operated by the North Texas Tollway Authority (NTTA);
US 380 and the state highways by TxDOT (Dallas District); the City of Frisco
operates its arterials and signals; NCTCOG is the region's metropolitan
planning organization and holds regional data sets. The detector data for a
tollway stretch will most likely come from NTTA, possibly through the City.

---

## What the study is

FlowState builds a vehicle-by-vehicle simulation of one highway stretch,
checks that it reproduces the stretch's real traffic (counts, speeds, where
and when slowdowns form), and only then compares strategies such as ramp
metering or variable speed limits. Everything is run in our cloud; nothing is
installed at the agency. The data below is used only for this study, is
never published without the agency's written permission, and contains no
personal information. We will sign the agency's data-use agreement.

## 1. The stretch and the question (required)

- The highway stretch (start and end points), the direction, and the time of
  day the agency is concerned about (for example the weekday evening peak).
- The question the agency wants answered, in one or two sentences (for
  example: where does the evening queue start, and would metering the busiest
  on-ramps or lowering the speed limit upstream reduce delay?).
- Any construction, lane closures or layout changes on the stretch in the
  last 12 months, with dates.

## 2. Mainline traffic sensors (required)

For every sensor station on the stretch (and one station beyond each end, if
available):

- Vehicle counts, speeds and, if available, occupancy.
- Per lane if possible; per station (all lanes together) is acceptable.
- In intervals of 5 minutes or shorter (20-second, 30-second and 1-minute
  data are all fine; we aggregate).
- For at least 15 weekdays, ideally Tuesday to Thursday, ideally recent and
  outside school holidays. More days are better: we calibrate the model on
  some days and test it on days it has never seen.
- The sensor inventory: station ID, location (latitude/longitude or
  milepost), direction, lanes covered, sensor type (loop, radar, video), and
  any known outages or maintenance during the period.

Any format is fine (CSV, Excel, database export). We check every sensor for
missing data, stuck readings and impossible values before using it, and we
tell the agency which sensors we excluded and why.

## 3. On-ramp and off-ramp volumes (strongly requested)

- Counts for every on-ramp and off-ramp on the stretch, for the same days and
  intervals as item 2.
- If ramps have no sensors: toll-gantry transaction counts on the ramps (by
  interval), any manual or tube counts, or turning-movement counts at the
  ramp ends.
- If no ramp counts exist at all, tell us; we then estimate ramp volumes from
  the differences between neighbouring mainline stations, write the method
  down before we start, and state in the report which ramp volumes are
  estimates and how uncertain they are. (A previous corridor study failed
  partly because its ramps carried too little traffic in the model, so this
  item matters.)

## 4. Vehicle mix (requested)

- The share of trucks and other heavy vehicles, by location and time of day
  if available: classification counts, or toll transactions by vehicle class
  or axle count.
- Without it we use a documented default and test how much the results
  depend on it.

## 5. Road layout (requested)

- Lane configuration of the stretch: number of lanes, auxiliary and
  acceleration lanes and their lengths, lane drops, ramp locations, posted
  speed limits, toll gantry locations.
- As-built plans or a lane schematic if available. We also check the layout
  by hand against satellite imagery, because map data is often wrong at
  ramps.

## 6. Travel times or probe speeds (helpful)

- Any travel-time or speed data that covers the stretch: toll-tag or
  Bluetooth reader travel times, or probe-vehicle speed data (for example
  INRIX or HERE, which NCTCOG or TxDOT may license).
- These give an independent check on the sensors.

## 7. Incidents and weather (helpful)

- The incident log (crashes, stalled vehicles, closures) and any weather
  events for the requested days, so we can leave out days that were not
  typical.

## 8. Operations already in place (helpful)

- Any ramp meters, variable speed limits, lane controls, managed lanes or
  signal timing at the ramp ends that affect the stretch, and when they
  operate.

---

## What the agency gets back

- A short plain-language report: where and when the congestion forms, how
  well the model reproduces the real traffic (with the standard checks used
  by FHWA and TxDOT), and, only if the model passes those checks, how the
  strategies compare, with ranges rather than single numbers.
- A clear statement of what we are confident about and what we are not.
  Fuel figures are model estimates and are labelled as such.
- A list of every sensor used or excluded and every assumption made.
