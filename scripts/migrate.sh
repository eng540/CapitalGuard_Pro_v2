#!/usr/bin/env bash
set -euo pipefail
alembic upgrade head || alembic revision --autogenerate -m "init" && alembic upgrade head
