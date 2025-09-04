# --- START OF FILE: Dockerfile ---
# ✅ FIX: Force the base image to be for the linux/amd64 platform.
# This is the first step to prevent CPU architecture mismatch issues.
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

# بيئة تشغيل
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# تثبيت حزم نظامية خفيفة لازمة للـ psycopg/sqlalchemy/httpx وغيرها
# (Adding 'dos2unix' to ensure script compatibility)
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev curl ca-certificates dos2unix \
 && rm -rf /var/lib/apt/lists/*

# إنشاء مستخدم غير جذري
RUN useradd -m -u 10001 appuser
WORKDIR /app

# تثبيت باكيجات بايثون أولاً للاستفادة من طبقات الكاش
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# نسخ المصدر وملفات الهجرة
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

# نسخ سكربت نقطة الدخول
COPY entrypoint.sh /app/entrypoint.sh

# ✅ FIX 2: Use dos2unix which is a more robust way to fix line endings, then chmod.
RUN dos2unix /app/entrypoint.sh \
 && chmod +x /app/entrypoint.sh

# إعداد المسار + صلاحيات
ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

# Healthcheck بسيط
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

# استخدام سكربت نقطة الدخول لتشغيل الترحيلات بأمان
ENTRYPOINT ["/app/entrypoint.sh"]

# الأمر الافتراضي الذي سيتم تشغيله بواسطة نقطة الدخول.
CMD uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
# --- END OF FILE ---