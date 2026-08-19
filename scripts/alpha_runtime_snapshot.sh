#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${CAPITALGUARD_BASE_URL:-https://capitalguardprov2-production-b4ea.up.railway.app}"
OUTPUT_DIR="${ALPHA_SNAPSHOT_DIR:-/tmp/capitalguard-alpha-snapshots}"
mkdir -p "$OUTPUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUTPUT_DIR/alpha_snapshot_${STAMP}.md"

health="$(curl -fsS --max-time 20 "$BASE_URL/health")"
metrics="$(curl -fsS --max-time 20 "$BASE_URL/metrics")"

{
  echo "# CapitalGuard Alpha Runtime Snapshot"
  echo
  echo "- Timestamp UTC: $STAMP"
  echo "- Base URL: $BASE_URL"
  echo
  echo "## Health"
  echo
  echo '```json'
  echo "$health"
  echo '```'
  echo
  echo "## Outbox"
  echo
  echo '```text'
  printf '%s\n' "$metrics" | grep -E '^cg_publication_outbox_(attempts_total|deliveries_total|queue_size)' || true
  echo '```'
  echo
  echo "## Operator fields"
  echo
  echo "- Invited users: TODO"
  echo "- Active users: TODO"
  echo "- Lifecycle smoke-test Recommendation Ref: TODO"
  echo "- Lifecycle smoke-test UserTrade Ref: TODO"
  echo "- Incidents / retries / failures: TODO"
} > "$OUT"

printf '%s\n' "$OUT"
