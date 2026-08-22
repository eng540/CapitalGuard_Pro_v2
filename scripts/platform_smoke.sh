#!/usr/bin/env bash
set -euo pipefail

CORE_URL="${1:-${CAPITALGUARD_CORE_URL:-}}"
WEB_URL="${2:-${CAPITALGUARD_WEB_URL:-}}"

if [[ -z "$CORE_URL" || -z "$WEB_URL" ]]; then
  echo "Usage: $0 https://core.example https://web.example" >&2
  exit 2
fi

CORE_URL="${CORE_URL%/}"
WEB_URL="${WEB_URL%/}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch_json() {
  local name="$1"
  local url="$2"
  local code
  code="$(curl -sS --connect-timeout 10 --max-time 20 -o "$TMP_DIR/$name.json" -w '%{http_code}' "$url")"
  if [[ "$code" != "200" ]]; then
    echo "$name failed: HTTP $code" >&2
    cat "$TMP_DIR/$name.json" >&2 || true
    exit 1
  fi
}

fetch_json core_health "$CORE_URL/health"
fetch_json web_health "$WEB_URL/health"
fetch_json core_v1_status "$CORE_URL/api/v1/status"

python3 - "$TMP_DIR" <<'PY'
import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
core = json.loads((tmp / "core_health.json").read_text())
web = json.loads((tmp / "web_health.json").read_text())
v1 = json.loads((tmp / "core_v1_status.json").read_text())

if core.get("status") != "ok":
    raise SystemExit("core health payload is not ok")
if web.get("status") != "ok" or web.get("service") != "capitalguard-web":
    raise SystemExit("web health payload is not the public web liveness contract")
expected = {"api_version": "v1", "service": "capitalguard-core", "status": "ok", "commercial_mode": "noncommercial"}
if v1 != expected:
    raise SystemExit("core v1 status contract mismatch")
PY

printf 'Platform smoke passed: core_health=ok web_health=ok v1_status=noncommercial\n'
