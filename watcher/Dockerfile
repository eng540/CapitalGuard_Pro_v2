# --- watcher/Dockerfile ---
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY watcher/entrypoint_watcher.sh /app/entrypoint_watcher.sh

ENV PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends dos2unix \
 && dos2unix /app/entrypoint_watcher.sh \
 && chmod +x /app/entrypoint_watcher.sh \
 && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/bin/sh", "/app/entrypoint_watcher.sh"]