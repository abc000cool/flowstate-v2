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

## The calibration-round pipeline (2026-09-06)

`launch_i24_pipeline.sh` creates one on-demand VM (the code goes up as a `git archive` snapshot of HEAD through `vm_setup.sh`, because the repository is private) (`n2-standard-32`,
`us-west1-b`, 120 GB) whose startup script arms a boot-time hard cap
(`shutdown -h +300`), ships the I-24 processed data and the observed-side
cache, clones the pushed commit and starts `pipeline_i24.sh` under
`systemd-run --user`. The pipeline is resumable (stage markers in `logs/`) and
powers the machine off when it exits, success or failure. Run
`watch_pipeline.sh` locally afterwards: it polls, fetches `/tmp/final.tgz`
when `logs/PIPELINE_DONE` appears, deletes the instance, never restarts a
powered-off instance at the working size (it shrinks to two vCPUs with a
20-minute cap to fetch), and deletes the instance unconditionally at its
own deadline (default 5.5 h). Expected: about two hours of machine time.

