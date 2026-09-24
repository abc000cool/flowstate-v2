#!/bin/bash
# Deploy the hosted tester to Cloud Run from a clean export of HEAD (never the working tree).
#
# Usage (repo root):  FLOWSTATE_API_KEY=<key> scripts/gcp/deploy_cloud_run.sh [--project P] [--region R]
#
# What it sets (the 2026-09-24 configuration, docs/HOSTED_TESTER.md): the image is built by Cloud
# Build from the repository's Dockerfile (frontend built in-image); one request at a time per
# instance, at most two instances, 2 vCPU / 4 GiB, a 60-minute request timeout (the inline queue
# runs a simulation inside the request), results on a Cloud Storage bucket mounted at
# /mnt/results, public invoker (the API key is the only gate: every /api/ route needs it).
# The default compute service account needs roles/cloudbuild.builds.builder,
# roles/storage.objectViewer, roles/artifactregistry.writer and roles/logging.logWriter
# (granted 2026-09-23; a new project needs them again).
set -euo pipefail
PROJECT=project-357fa2a7-490c-4a4b-a71; REGION=us-west1; SERVICE=flowstate-tester; BUCKET=flowstate-tester-results
while [ $# -gt 0 ]; do case "$1" in
  --project) PROJECT="$2"; shift 2 ;; --region) REGION="$2"; shift 2 ;; --service) SERVICE="$2"; shift 2 ;;
  *) echo "unknown option $1" >&2; exit 2 ;; esac; done
: "${FLOWSTATE_API_KEY:?set FLOWSTATE_API_KEY (a long random key; it is never committed)}"
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
SRC="$(mktemp -d)/src"; mkdir -p "$SRC"; git archive HEAD | tar -x -C "$SRC"
echo "== deploying $(git rev-parse --short HEAD) to $SERVICE ($PROJECT, $REGION)"
gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --project "$PROJECT"
gcloud run deploy "$SERVICE" --source "$SRC" --region "$REGION" --project "$PROJECT" --quiet \
  --allow-unauthenticated --cpu 2 --memory 4Gi --concurrency 1 --max-instances 2 --timeout 3600 \
  --cpu-boost --execution-environment gen2 \
  --set-env-vars "FLOWSTATE_QUEUE=inline,FLOWSTATE_RESULTS_DIR=/mnt/results,FLOWSTATE_MAX_UPLOAD_MB=64,FLOWSTATE_API_KEY=$FLOWSTATE_API_KEY" \
  --add-volume "name=results,type=cloud-storage,bucket=$BUCKET" --add-volume-mount "volume=results,mount-path=/mnt/results"
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format="value(status.url)")
echo "== $URL"; curl -s -m 30 "$URL/health"; echo
rm -rf "$(dirname "$SRC")"
