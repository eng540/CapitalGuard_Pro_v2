#--- START OF FILE: Dockerfile ---
# Unified & final Dockerfile for CapitalGuard API
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source + Alembic
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

ENV PYTHONPATH=/app/src

# Apply migrations then start API (works locally and on PaaS that inject $PORT)
CMD ["sh", "-lc", "alembic upgrade head && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
#--- END OF FILE ---