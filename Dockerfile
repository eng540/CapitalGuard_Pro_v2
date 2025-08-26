FROM python:3.11-slim

WORKDIR /app

# تثبيت الاعتمادات
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# نسخ الكود وملفات Alembic
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

ENV PYTHONPATH=/app/src

# شغّل: preflight ثم الهجرات ثم السيرفر
CMD ["sh", "-lc", "python -m capitalguard.db_preflight && alembic upgrade head && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT}"]