# Running FlowState compute on a cloud VM

The heavy jobs (penetration sweeps, validation batteries) are many
independent single-threaded SUMO processes of ~350 MB each, so a single
many-core VM finishes in an hour what a laptop needs a day for. Results are
deterministic per SUMO version (pinned), so cloud and local runs agree at the
summary level.

## Create a VM (Google Cloud, Debian 12)

```sh
gcloud compute instances create flowstate-sweep \
  --zone us-central1-a --machine-type n2-standard-32 \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 50GB --boot-disk-type pd-balanced
```

`n2-standard-32` (32 vCPU, 128 GB) is about $1.55/h on demand; add
`--provisioning-model SPOT --instance-termination-action STOP` for roughly a
third of that, accepting that the VM may be preempted (the sweep is
resumable; rerun the bootstrap and it continues). A new project may need
`gcloud services enable compute.googleapis.com` first and a CPU quota above
the default 24 in the chosen region.

## Bootstrap and start the sweep

```sh
gcloud compute ssh flowstate-sweep --zone us-central1-a --command \
  'curl -fsSL https://raw.githubusercontent.com/abc000cool/flowstate-v2/main/scripts/gcp/bootstrap.sh | bash -s -- --scenario i24_replica_corrected'
```

The script waits for the image's first-boot apt lock, installs the X11,
fontconfig and libatomic runtime libraries the libsumo wheel links against
(a minimal cloud image has none of them), then uv,
Python 3.12, the workspace, then starts
`scripts/i24_penetration_sweep.py` with `nproc - 2` workers under `nohup`.
Follow progress with:

```sh
gcloud compute ssh flowstate-sweep --zone us-central1-a --command \
  'tail -n 5 ~/flowstate/logs/sweep.log; find ~/flowstate/runs/i24_sweep -mindepth 4 -maxdepth 4 -name meta.json | wc -l'
```

500 runs at ~8 min each on 30 workers is about 1.5 hours.

## Auto-stop

The bootstrap arms an automatic `shutdown -h +5` when the sweep finishes, so
an unattended VM stops billing compute on its own (the disk still bills a few
cents a day until the instance is deleted). Fetch results after
`gcloud compute instances start flowstate-sweep --zone us-west1-b`, or pass
`--no-auto-stop` to keep the VM up. For ad-hoc jobs launched by hand, arm the
same guard with `systemd-run --user --unit=autostop bash -c 'until [ -f
<done-marker> ]; do sleep 60; done; sudo shutdown -h +5'` (enable
`loginctl enable-linger $USER` first so user units survive logout).

## Fetch results and shut down

```sh
scripts/gcp/fetch_results.sh flowstate-sweep us-central1-a
uv run --no-sync python scripts/i24_penetration_analyze.py
gcloud compute instances delete flowstate-sweep --zone us-central1-a --quiet   # stops billing
```

## Inputs

Everything the sweep needs is in the repository: the scenario YAMLs, the
calibration artifacts, and the OpenStreetMap extract `data/osm/i24_motion.osm`
(89 KB, © OpenStreetMap contributors, ODbL). The 19.5 GB raw I-24 MOTION
export is only needed to re-extract trajectories and never leaves the
owner's machine.

## The calibration-round pipeline (2026-09-06, revised after the run)

`launch_i24_pipeline.sh` creates one on-demand VM (`n2-standard-32`,
`us-west1-b`, 120 GB; the code goes up as a `git archive` snapshot of HEAD
through `vm_setup.sh`, because the repository is private) whose startup script
arms a boot-time hard cap (`shutdown -h +$CAP_MIN`, default 480 min), ships
the I-24 processed data and the observed-side cache and starts
`pipeline_i24.sh` under `systemd-run --user`. The pipeline is resumable (stage
markers in `logs/`), rebuilds its results archive `~/final.tgz` atomically
after **every** stage (artifacts, scenarios, logs; the first-seed replicates
join at the end), copies it to `--bucket gs://…` when one is given, and powers
the machine off when it exits — success, failure, or SIGTERM. With `--bucket`
and `--self-delete` the VM deletes itself once the final archive is in the
bucket (the instance is created with the `compute-rw` and `storage-rw`
scopes), so no local machine has to be awake; without a bucket, run
`caffeinate -i watch_pipeline.sh` locally: it pulls the archive whenever it
changed, deletes the instance when the pipeline reports done, never restarts a
powered-off instance at the working size (a 2-vCPU restart with a 30-minute
cap re-armed before every fetch, only when the local archive is incomplete),
and deletes the instance unconditionally at its own deadline (default 7.5 h).
Results are installed with `ingest_pipeline_results.sh final.tgz`.

### What the 2026-09-06 run taught (docs/LESSONS.md rows 14–16)

The first run of this pipeline (commit 3a4b042, VM created 08:55 UTC) finished
its calibration stages and both batteries by 11:31 UTC and was killed by its
own 300-minute boot cap at 13:57 UTC, 2.4 h into the headway-cap sweep. The
archive was written only by the EXIT trap, to `/tmp`; a systemd stop gives
the unit seconds, and Debian clears `/tmp` at boot, so the 2-vCPU restart found
nothing and the interim archive it built was cut off by the restart's own
20-minute cap (the laptop running the watcher slept twice, stretching every
timer). Recovered: eight intact artifacts (the demand and ramp fits, the jm
sweep, the `zip_corrected` and `zip_ramps` batteries and the canonical
`speedcal_heavy` battery); lost: three battery artifacts (rerun on a second VM the same evening,
`--pipeline-args '--stages "battery_lost prune_lost cap_sweep rescore"'`), every scenario file (rebuilt bit-for-bit
from the fit artifacts with `--from-artifact`, config hashes checked against
the batteries that used them), the pipeline logs, and the cap sweep. Machine
time: 5.0 h at 32 vCPUs plus 0.4 h at 2 vCPUs, about eight dollars. Every
change above follows from that: archive in `$HOME`, after every stage, atomic;
bucket copy; SIGTERM trap; cap at twice the estimate; incremental fetch;
`caffeinate`; a describe timeout is not a deleted instance.

The second run the same evening (21:33–00:55 UTC, 3.4 h, about five
dollars) used every one of those changes: the archive reached the bucket
after each of its four stages, the instance deleted itself ninety seconds
after its final archive, and the watcher's own delete found nothing left to
delete. Two IAM grants were needed first — the project gives its default
compute service account no role, so the bucket write and the self-delete
both failed on a probe until `roles/storage.objectAdmin` (bucket) and
`roles/compute.instanceAdmin.v1` (that instance only) were added; the launch
script grants both now. The bucket is deleted after ingestion.
