FROM python:3.11-slim

WORKDIR /app

# 1) تثبيت الاعتمادات
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 2) نسخ الكود وملفات Alembic
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# 3) إعداد PYTHONPATH
ENV PYTHONPATH=/app/src

# 4) شغّل الهجرة ثم شغّل Uvicorn بالمنفذ اللي توفره Railway
CMD ["sh", "-lc", "alembic upgrade head && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT}"]