# --- START OF FILE: Dockerfile ---
# إنتاجي ومستقر: نستخدم مرآة Google لصورة بايثون لتفادي TLS timeouts في Docker Hub
# بإمكانك العودة لاحقًا لـ Docker Hub بتغيير سطر FROM الأول (المعلّق بالأسفل).
# FROM python:3.11.9-slim-bookworm
FROM mirror.gcr.io/library/python:3.11.9-slim-bookworm

# بيئة تشغيل
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# تثبيت حزم نظامية خفيفة لازمة للـ psycopg/sqlalchemy/httpx وغيرها
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev curl ca-certificates \
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

# نسخ سكربت نقطة الدخول وجعله قابلاً للتنفيذ
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# إعداد المسار + صلاحيات
ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

# Healthcheck بسيط (يتعامل مع متغير PORT بشكل صحيح)
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

# استخدام سكربت نقطة الدخول لتشغيل الترحيلات بأمان
ENTRYPOINT ["/app/entrypoint.sh"]

# الأمر الافتراضي الذي سيتم تشغيله بواسطة نقطة الدخول.
# تم التغيير إلى صيغة Shell للسماح بتوسيع متغير ${PORT}.
CMD uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
# --- END OF FILE ---