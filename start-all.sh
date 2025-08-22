#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
# تشغيل الهجرات ثم الخروج فورًا (supervisor سينتقل لتشغيل البرامج الأخرى)
alembic upgrade head || (alembic revision --autogenerate -m "init" && alembic upgrade head)