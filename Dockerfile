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

# ✅ FIX: Copy the new entrypoint script and make it executable
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# إعداد المسار + صلاحيات
ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

# Healthcheck بسيط
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

# ✅ FIX: Use an entrypoint script to safely run migrations before starting the server.
# This prevents the container from crash-looping if migrations fail.
ENTRYPOINT ["/app/entrypoint.sh"]

# The CMD now specifies the default command that the entrypoint will execute.
# It respects the PORT variable if it exists.
CMD ["uvicorn", "capitalguard.interfaces.api.main:app", "--host", "0.0.0.0", "--port", "${PORT:-8000}"]
# --- END OF FILE ---