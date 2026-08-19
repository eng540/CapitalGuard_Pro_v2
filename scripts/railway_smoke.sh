#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${RAILWAY_STAGING_URL:-}}"
if [[ -z "$BASE_URL" ]]; then
  echo "Usage: $0 https://service.example.com" >&2
  exit 2
fi

BASE_URL="${BASE_URL%/}"

health_code="$(curl -sS -o /tmp/capitalguard-health.json -w '%{http_code}' "$BASE_URL/health")"
if [[ "$health_code" != "200" ]]; then
  echo "health failed: HTTP $health_code" >&2
  cat /tmp/capitalguard-health.json >&2 || true
  exit 1
fi

curl -fsS "$BASE_URL/metrics" >/tmp/capitalguard-metrics.txt

portfolio_code="$(curl -sS -o /tmp/capitalguard-portfolio.json -w '%{http_code}' \
  "$BASE_URL/api/webapp/portfolio?initData=invalid")"
if [[ "$portfolio_code" != "200" ]]; then
  echo "portfolio auth smoke failed: HTTP $portfolio_code" >&2
  cat /tmp/capitalguard-portfolio.json >&2 || true
  exit 1
fi

python3 - <<'PY'
import json
with open('/tmp/capitalguard-portfolio.json', encoding='utf-8') as handle:
    payload = json.load(handle)
if payload.get('ok') is not False:
    raise SystemExit('invalid Telegram initData was not rejected')
PY

echo "Railway smoke passed: health=$health_code metrics=200 invalid_initData=rejected"
