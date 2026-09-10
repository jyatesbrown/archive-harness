#!/usr/bin/env bash
# Daily run with state in an S3-compatible bucket (Cloudflare R2). Intended for a
# stateless runner (a scheduled Devin session): pull the DB, run, push payloads
# and DB back. Nothing in the bucket is ever overwritten except the "current"
# DB pointer, and every day's DB is also kept as an immutable dated copy.
#
# Required env: R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT_URL R2_BUCKET
# Optional:     DATA (local state dir, default ./data)   BOOTSTRAP=1 (allow a
#               first run when the bucket holds no DB yet)
#
# Bucket layout:
#   harness.sqlite                 current DB
#   db-history/harness-<UTC-ts>.sqlite  one copy per run
#   payloads/<source>/<YYYY>/<MM>/<ts>-<sha12>.raw
#   summaries/<UTC-ts>.txt         run summary
#   logs/<UTC-ts>.log              this script's log
#
# Exit codes: 0 all sources ok and state pushed; 1 run completed with alerts;
# 2 state could not be pulled/pushed (the run must not be counted as done).
set -uo pipefail

: "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_ENDPOINT_URL:?}" "${R2_BUCKET:?}"
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" AWS_DEFAULT_REGION=auto
DATA="${DATA:-data}"
B="s3://$R2_BUCKET"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DATA/payloads" "$DATA/summaries" "$DATA/logs"
LOG="$DATA/logs/$TS.log"
exec > >(tee -a "$LOG") 2>&1
cd "$(dirname "$0")/.."

s3() { aws --endpoint-url "$R2_ENDPOINT_URL" s3 "$@"; }
s3api() { aws --endpoint-url "$R2_ENDPOINT_URL" s3api "$@"; }
push_log() { s3 cp "$LOG" "$B/logs/$TS.log" --only-show-errors || true; }

echo "== archive-harness daily $TS"

# ---- pull -------------------------------------------------------------------
if s3api head-object --bucket "$R2_BUCKET" --key harness.sqlite >/dev/null 2>&1; then
  s3 cp "$B/harness.sqlite" "$DATA/harness.sqlite" --only-show-errors || { echo "ALERT: could not pull DB"; push_log; exit 2; }
  echo "pulled DB ($(stat -c %s "$DATA/harness.sqlite") bytes)"
elif [ "${BOOTSTRAP:-0}" = "1" ]; then
  echo "no remote DB; BOOTSTRAP=1 so starting a new chain"
else
  echo "ALERT: no remote DB at $B/harness.sqlite and BOOTSTRAP not set; refusing to start a new chain"
  push_log; exit 2
fi

# ---- run --------------------------------------------------------------------
python3 -m harness --db "$DATA/harness.sqlite" --payloads "$DATA/payloads" init --sources sources.json || { echo "ALERT: init failed"; push_log; exit 2; }
python3 -m harness --db "$DATA/harness.sqlite" --payloads "$DATA/payloads" run --summary-file "$DATA/summaries/$TS.txt"
RUN_RC=$?
echo "run exit code $RUN_RC"

# ---- push -------------------------------------------------------------------
PUSH_RC=0
s3 sync "$DATA/payloads" "$B/payloads" --only-show-errors || PUSH_RC=1
s3 cp "$DATA/harness.sqlite" "$B/db-history/harness-$TS.sqlite" --only-show-errors || PUSH_RC=1
s3 cp "$DATA/harness.sqlite" "$B/harness.sqlite" --only-show-errors || PUSH_RC=1
s3 cp "$DATA/summaries/$TS.txt" "$B/summaries/$TS.txt" --only-show-errors || PUSH_RC=1
if [ $PUSH_RC -ne 0 ]; then
  echo "ALERT: push to $B failed; local state kept in $DATA"
  push_log; exit 2
fi

# ---- verify against what the bucket actually holds ---------------------------
s3 ls "$B/payloads/" --recursive > "$DATA/manifest.txt" || { echo "ALERT: could not list bucket"; push_log; exit 2; }
python3 -m harness --db "$DATA/harness.sqlite" --payloads "$DATA/payloads" verify --manifest "$DATA/manifest.txt" || { echo "ALERT: integrity check failed"; push_log; exit 2; }

echo "== summary"
cat "$DATA/summaries/$TS.txt"
push_log
[ $RUN_RC -eq 0 ] && exit 0 || exit 1
