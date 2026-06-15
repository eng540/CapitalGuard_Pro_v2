# ai_service/Dockerfile
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# تثبيت التبعيات الأساسية فقط
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10002 aiservice
WORKDIR /app

# نسخ التبعيات وتثبيتها
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# نسخ كود الخدمة
COPY . /app

ENV PYTHONPATH=/app
RUN chown -R aiservice:aiservice /app
USER aiservice

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/health || exit 1

# تشغيل الخادم على المنفذ 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]