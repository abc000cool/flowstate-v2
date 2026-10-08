# Two more I-24 MOTION mornings: what to download (C9)

**For:** the owner · **Written:** 2026-10-07, before any new file was read · **Plan:**
docs/PRE_FRISCO_PROGRAM.md C9 (plan item 15) · **Stage:** `p20_i24_days`
(`artifacts/i24_days_2026-10-07/stage_p20_c9.sh.txt`)

I-24 has one recorded morning (30 Nov 2022) and no holdout: every I-24 calibration used that
morning. Two more westbound mornings give the first holdout. Under the approved C9 amendment
(Amendment 9), 30 Nov stays on the calibration side and both new mornings become validation
days. Three days is still below the protocol's minimum of 5 calibration and 3 validation days
(docs/FRISCO_PROTOCOL.md §3.3), so the result will be reported as underpowered.

## Which two mornings

The rule (protocol §3.1, applied before downloading):
- a Tuesday, Wednesday or Thursday;
- not a federal holiday: Thanksgiving, Thursday 24 Nov 2022, is out;
- not in the CIRCLES MegaVanderTest week, 14–18 Nov 2022. The test fleet on the road counts as
  an event (Amendment 9; CLAUDE.md §13);
- no incident or weather event on I-24 westbound, MM 58–63, between 06:00 and 10:00 CST. Check
  TDOT's incident records for the stretch and NOAA's hourly observations at Nashville
  International (BNA).

The INCEPTION v1.0.0 release has ten mornings. Six of them are listed by date in arXiv:2409.00326,
Table 4: 22 Nov (Tue), 28 Nov (Mon), 29 Nov (Tue), 30 Nov (Wed, already in hand), 1 Dec (Thu) and
2 Dec (Fri), all in 2022. I have not confirmed the other four. Of the confirmed dates, three pass
the weekday rule:

| Date (2022) | Weekday | Use |
|---|---|---|
| **Tue 29 Nov** | Tuesday | first choice: the day before the calibration day, after the holiday week |
| **Thu 1 Dec** | Thursday | first choice: the day after the calibration day |
| Tue 22 Nov | Tuesday | alternate: allowed, but it falls in Thanksgiving week. The I-24 MOTION team use it as "a typical morning commute" (arXiv:2311.10888) |

If one of the first choices fails the incident or weather check, take 22 Nov instead. If the
portal lists other Tuesday–Thursday mornings, they are further alternates, provided they fall
outside 14–18 Nov and are not 24 Nov. 23 Nov (the Wednesday before Thanksgiving) passes the rule
but is a holiday-travel morning, so use it only as a last resort. Write down what each check found
and send it with the file names.

## How the files are named

Each INCEPTION export is one zip, about 5.8 GB, named `<run id>__post<N>.zip`; the 30 Nov file is
`6386d89efb3ff533c12df167__post10.zip`. The run id is a 24-hex MongoDB ObjectId, and its first
8 hex digits are the time the id was created, not the date of the morning. For 30 Nov,
`6386d89e` is 22:14 CST on 29 Nov, the evening before. Take each file's date from the portal's
listing.

The stage checks the date anyway. It converts the file with 06:00 CST of the stated date as
`t = 0`, then refuses the file unless its first sample falls between 05:00 and 06:29 CST and its
samples run past 08:31 (`scripts/i24_data.py check`). A file from the wrong date therefore fails
by whole days.

## Where to put them

1. Download each zip from i24motion.org while signed in under your registration.
2. **Do not open, double-click or unzip it on the laptop.** macOS Archive Utility would write a
   19.5 GB JSON file to disk. Do not move it into the repository either.
3. Upload it unchanged to the launch bucket and confirm the size:
   `gcloud storage cp ~/Downloads/<run id>__post10.zip gs://<bucket>/i24motion/`, then
   `gcloud storage ls -l gs://<bucket>/i24motion/`.
4. Delete the laptop copy once the upload is confirmed.
5. Send the coordinator the two dates, object names and screen results. They go into the
   stage's `P20_DAYS` before the launch commit.

On the VM, the stage downloads each zip, converts it by streaming (nothing is extracted), checks
it, and deletes the zip. The VM then deletes itself.

**Cost:** about $0.7 for the VM; the `--cap-min 120` cap limits it to about $1.6. Keeping the two
zips in the bucket costs about $0.23 a month (11.6 GB at standard storage).

## The data agreement

- The mornings are free under your I-24 MOTION registration and are governed by its data
  agreement. Keep the zips in the private bucket: never make it public, and never put
  trajectories in git (`data/` is gitignored).
- The repository holds only aggregates derived from the data, as it does for 30 Nov:
  - 5-minute counts and speeds at six sections;
  - coverage estimates;
  - the day split.
- Any published use cites Gloudemans et al. (2023), *Transp. Res. C* 155:104311
  (docs/I24_DATA.md).
- Before any derived data leave the project, check the agreement's terms on redistribution.
