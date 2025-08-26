FROM python:3.11-slim

WORKDIR /app

# deps
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# code + alembic
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

ENV PYTHONPATH=/app/src

# run preflight (resize alembic_version + ensure columns) then alembic, then uvicorn
CMD ["sh", "-lc", "python -m capitalguard.db_preflight && alembic upgrade head && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT}"]