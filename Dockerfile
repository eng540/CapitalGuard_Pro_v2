FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev curl ca-certificates dos2unix \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 appuser
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

COPY entrypoint.sh /app/entrypoint.sh
RUN dos2unix /app/entrypoint.sh \
 && sed -i '1s/^\xEF\xBB\xBF//' /app/entrypoint.sh \
 && chmod +x /app/entrypoint.sh

ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
CMD uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT:-8000}