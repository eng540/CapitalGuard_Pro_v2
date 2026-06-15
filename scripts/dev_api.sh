#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
uvicorn capitalguard.interfaces.api.main:app --reload --port 8000
