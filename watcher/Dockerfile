# --- START OF FILE: Dockerfile.watcher ---
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq-dev curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
ENV PYTHONPATH=/app/src

RUN useradd -m -u 10002 appuser-watcher
RUN chown -R appuser-watcher:appuser-watcher /app
USER appuser-watcher

# الأمر الرئيسي لتشغيل سكربت المراقب
CMD ["python", "-m", "capitalguard.infrastructure.sched.watcher_ws"]
# --- END OF FILE ---