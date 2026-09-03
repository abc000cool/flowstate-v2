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

The script installs `libgl1` (libsumo links against it even headless), uv,
Python 3.12, the workspace, then starts
`scripts/i24_penetration_sweep.py` with `nproc - 2` workers under `nohup`.
Follow progress with:

```sh
gcloud compute ssh flowstate-sweep --zone us-central1-a --command \
  'tail -n 5 ~/flowstate/logs/sweep.log; find ~/flowstate/runs/i24_sweep -mindepth 4 -maxdepth 4 -name meta.json | wc -l'
```

500 runs at ~8 min each on 30 workers is about 1.5 hours.

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
