#!/usr/bin/env bash
set -e
alembic upgrade head
exec uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT}