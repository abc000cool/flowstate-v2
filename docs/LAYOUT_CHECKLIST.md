# Checking a corridor's road layout against satellite imagery

Work package WP-102 (Stage 1, item 4 of the Frisco plan). Written 2026-10-04.

This is the procedure for the person who checks, by eye, that the road the
model drives on is the road that exists: the number of lanes, where the ramps
join and leave and on which side, where acceleration and deceleration lanes
are and how long they are, where lanes end, and the speed limit. You do not
need to read the code. You need a browser, ideally Google Earth Pro (free,
desktop), and an hour or two per 10 km of freeway.

## 1. Why this check exists

FlowState builds its road network from OpenStreetMap (OSM) with SUMO's
`netconvert`. The model inherits every error of the map and every guess the
converter makes. On the Minnesota corridor (I-94 westbound, St. Paul) two
things went wrong, and both looked fine in every number the model printed
(docs/ONBOARDING_MNDOT.md §6 and §9):

- The map drew no acceleration lanes, so cars entering from the on-ramps had
  to squeeze straight into a through lane. In the simulation they waited, and
  four on-ramps delivered 4–6 % of their traffic.
- At two exits the converter connected the exit to the **leftmost** lanes
  although the exits leave on the right. Cars going straight were trapped in a
  lane that only led off the freeway, and the whole corridor locked up.

The lane counts were right at every detector. Only a look at the actual road
would have shown the problem. The study protocol therefore allows map
corrections **only** for layout errors this checklist finds and verifies
(docs/FRISCO_PROTOCOL.md §7.3), each with the imagery or plan it rests on.

## 2. Producing the checklist

Someone with the code runs, from the repository root (on a cloud machine for
anything but a small test corridor):

```
uv run --no-sync python scripts/layout_audit.py \
    --scenario scenarios/<corridor>.yaml \
    --out runs/wp102/layout_<corridor>
```

or, for a corridor onboarded through the web service, `--onboarding-dir
<results>/corridors/<id>` instead of `--scenario`. The script builds the road
network exactly as a simulation run does and writes three files:

| file | what it is for |
|---|---|
| `layout_checklist.md` | the checklist you work through (open it in any Markdown viewer, or paste it into a document) |
| `layout_audit.csv` | the same lines as a spreadsheet; fill in the last three columns there if you prefer |
| `layout_audit.json` | the full record, including which code version and map file produced it; keep it with your results |

The checklist names the code version it came from. If it says "uncommitted
changes in the audited code", ask for a clean run before you start: the result
of your check must be tied to a reproducible network.

## 3. Reading the checklist

Everything is listed in **travel order**, with `x` the distance in kilometres
from the start of the corridor along the direction of travel.

**Events** (one line each) are the places where something happens:

| event | meaning |
|---|---|
| on ramp | a ramp joins the freeway |
| off ramp | a ramp leaves the freeway |
| lane drop / lane gain | the number of through lanes changes, or a lane ends and another begins |
| weave | an entrance and an exit joined by an extra lane, so entering and exiting cars cross each other |
| cd road | a collector–distributor road: a separate roadway beside the freeway that ramps use |
| speed change | the model's speed limit changes |

**Segments** are the pieces of freeway between those places, each with the
model's lane count next to the map's, and the model's speed limit next to the
map's.

Lanes are numbered the way the simulator numbers them: **lane 0 is the
rightmost lane**. "Right" and "left" are as seen by a driver travelling in the
corridor's direction.

Each line has three links: **satellite** (Google Maps aerial view at the
point), **alt** (Bing Maps aerial view, a second image usually taken on a
different date), and **street** (Google Street View at the point). The point
is the junction where the event happens; pan a little up- and downstream.

**Automatic flags.** The tool runs mechanical checks and attaches flags:

- **defect**: the model contradicts the map in a way known to break the
  simulation (a ramp connected on the wrong side). Check these first.
- **warning**: the model or the map is probably wrong here (no acceleration
  lane, a lane count that differs from the map, a missing speed limit).
- **note**: recorded so you know what the model assumes (a lane the converter
  guessed, a speed limit change, a lane that ends on the left).

Each flag says what to look for. A line without flags still has to be
checked: the flags only see what the map and the model say, not the road.
The checks, their rules and their thresholds are listed at the end of every
checklist.

## 4. Recording what you find

Fill in the last three columns of every event line and every segment line:

- **checked by**: your initials.
- **imagery date**: the date the image was *taken*, not the date you looked.
  Google Maps does not show it. Google Earth Pro shows "Imagery Date" at the
  bottom of the window, and its clock icon (historical imagery) lets you step
  through older images; Street View shows "Image capture: month year". Write
  the source with the date, for example `GE 2024-06-12` or `SV 2023-08`. If no
  date can be found, write `unknown (viewed 2026-10-04)`.
- **result**: one of
  - `matches`
  - `differs: <what the imagery shows>`, for example `differs: exit leaves
    from the right lane, not the left` or `differs: acceleration lane about
    320 m, model 250 m`
  - `cannot tell: <why>`, for example trees, shadows, a bridge deck, or
    construction in the image.

Measure lengths with Google Earth Pro's ruler, along the lane, from the
**gore** (the point where the painted lines of the ramp and the freeway meet
or separate) to the end of the **taper** (where the extra lane has fully
ended or fully opened).

## 5. What to check at each event

**On ramp.**
1. Which side does the ramp join on? Compare with the line ("map: right",
   "joins ... lane 0 of 3 (right)").
2. Is there an acceleration lane, a lane beside the freeway where the ramp
   traffic speeds up before merging? If yes, measure it from the gore to the
   end of the taper and compare with the model's "added lane ... m".
3. What happens to that lane: does it end (taper), continue as a through lane,
   or run on to the next exit (then there should also be a weave line)?
4. If the model says "no acceleration lane" and the imagery shows one, this
   is exactly the Minnesota error. Record the length.

**Off ramp.**
1. Which side does the exit leave from?
2. Which lanes can take the exit: an exit-only lane (arrows, "EXIT ONLY"
   signs) or a lane that can also go straight (an option lane)? Compare with
   the model's "from lane(s) ... (option lane ...)".
3. Is there a deceleration lane before the gore? How long?

**Lane drop / lane gain.**
1. Does a lane really end (or begin) here? Count the lanes before and after.
2. On which side? Left-side lane drops are uncommon on US freeways, so a
   "lane ends on the LEFT" note deserves a careful look.
3. Where does the taper end? A difference of a few tens of metres is normal
   (see §7); a lane that ends at a different interchange is not.
4. A line saying "source: ramp guessing" or "merge model" marks a lane the
   converter or the simulator added; the question is still whether the road
   has it.

**Weave.** Is the extra lane continuous from the entrance gore to the exit
gore, with a dashed line between it and the freeway (no solid line that
forbids changing lanes)? Measure gore to gore.

**C-D road.** Is there a separate roadway beside the freeway (often behind a
barrier or a painted island) that leaves at the first point and rejoins at the
second, and do the interchange ramps connect to it rather than to the
freeway?

**Speed change, and every segment's speed.** Use Street View to find the
speed-limit sign near the point (drive back upstream if needed) and record the
posted value. A segment flagged "no maxspeed tag" has a speed the converter
made up (for a motorway, 142 km/h / 88 mph); record the posted limit for it.

**Every segment.** Count the lanes in the middle of the segment (away from
ramps) and compare with "lanes model / map".

## 6. When you are done

Save the filled checklist (or CSV) next to `layout_audit.json` in the same
folder, and send both to the analyst. The study report states how many lines
were checked, how many differ, and which differences led to a correction.

## 7. What counts as a verified layout error

A difference becomes a correction only when all of these hold:

1. The result says `differs`, with what the imagery shows.
2. The imagery is dated, and no construction since that date is known to have
   changed this place (ask the agency, or check the newest image available).
3. A second, independent source agrees: a second image from another date or
   provider (the "alt" link, Google Earth's historical imagery, Street View),
   or the agency's plans or lane diagrams. Record both sources.
4. The difference matters to the model. It does when it changes: the number
   of lanes, the side a ramp joins or leaves on, which lanes can take an
   exit, whether an acceleration, deceleration or auxiliary lane exists, where
   a lane drop is (which interchange, which side), or the speed limit. It
   does not when it is only lane width, shoulder width, curvature, or a gore
   or taper a few tens of metres from where the map put it while the lane
   arrangement is the same (the map's points are approximate and the model
   cannot use that precision).

A difference that fails (2) or (3) is listed in the study report as an
unverified difference and the map is left alone. **Never** change the map because the simulation results look wrong
(CLAUDE.md §0; docs/FRISCO_PROTOCOL.md: "never tune a scenario to force a
pass"); a correction rests on the imagery, not on the fit.

## 8. How a correction is made (for the analyst)

Corrections are made in the engine's own terms, never by editing the
downloaded map file in place. Each one is recorded with its date, the
imagery (source and date) or plan it rests on, and who verified it.

| finding | correction |
|---|---|
| exit connected on the wrong side (`split_side` defect, verdict `wrong_side`) | a connection patch restating the split, listed in the scenario's `network.patch_files` (`scripts/onboard_corridor.py --write-split-patch PATH` writes it; `data/osm/mndot_i94_wb_stpaul.splits.con.xml` is the Minnesota example) |
| wrong-side lane added by the converter's ramp guessing (`added_lane_wrong_side`) | add `--ramps.unset <edge>` to `network.netconvert_extra` (the edge is named in the flag) |
| entrance connected on the wrong side (`on_ramp_side` defect) | a connection patch (`*.con.xml` in `network.patch_files`) joining the ramp to the rightmost lane for a right-hand entrance: `<connection from="<ramp edge>" to="<freeway edge>" fromLane="0" toLane="0"/>`; netconvert drops every connection it computed for an edge a patch names, so list all of that edge's connections. `--ramps.unset` instead when the lane was a guessed one |
| acceleration lane missing in the model, present on the road | ramp guessing (`--ramps.guess`, on by default in onboarding) with `--ramps.ramp-length <metres>`; this sets one length for every ramp of the corridor, so a lane that differs a lot from the rest is drawn in a corrected copy of the map instead (below) |
| a lane the converter guessed that the road does not have | `--ramps.unset <edge>` |
| wrong lane count, a lane drop at the wrong place, an acceleration or deceleration lane of the wrong length | a corrected copy of the OSM extract, `data/osm/<name>_corrected.osm`, produced by a script that changes only the tags or way boundaries in question and writes a provenance file beside it (`scripts/i24_correct_osm.py` and `data/osm/i24_motion_corrected.provenance.json` are the pattern); the scenario then names the corrected file. For a single edge, a `*.edg.xml` patch with `numLanes` in `network.patch_files` also works |
| a lane drop on the wrong side | a connection patch listing which lanes continue (`tests/fixtures/weave_th61_lane_end_left_drop.con.xml` shows the format) |
| speed limit missing or wrong | the `maxspeed` tag in the corrected copy of the map, or a `*.edg.xml` patch with `speed` (m/s) |

After any correction:

1. Run `scripts/layout_audit.py` again. The flag at that place should be gone,
   and the line should now describe what the imagery shows.
2. Check the new line against the imagery once more and record it.
3. The scenario's config hash changes (patches and options are part of it);
   results from before the correction are not compared with results after it
   as if they were the same model.

## 9. What the audit cannot see

The tool reads the map and the compiled network. It cannot see:

- lane markings: solid or dashed lines, exit-only arrows, painted gores,
  HOV or managed-lane restrictions;
- the real length of an acceleration, deceleration or auxiliary lane when the
  map does not draw it (the model's length is then the converter's guess, or
  the length of whatever piece of map happens to carry the extra lane);
- exact positions: mappers place gores and lane ends within tens of metres;
- which side the **map** puts a lane drop on (the map stores a count; only
  ramp geometry and turn-lane tags give a side), so the side shown is the
  model's;
- lane and shoulder widths, grades, curves and sight distances (none are in
  the model);
- ramp meters, signals, signs other than the speed limit tag, advisory or
  variable speed limits, work zones;
- ramps outside the map extract or not kept in the scenario, and ramps that
  connect to a collector–distributor road rather than the freeway;
- anything built after the imagery was taken.

Those are what your eyes are for.
