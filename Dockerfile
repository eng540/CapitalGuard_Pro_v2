# --- START OF FILE: Dockerfile ---
# ✅ إجبار منصة linux/amd64 لتفادي عدم توافق المعمارية (السبب رقم 4)
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# حزم نظامية لازمة + dos2unix لإصلاح الترميز + curl للـ healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev curl ca-certificates dos2unix \
 && rm -rf /var/lib/apt/lists/*

# مستخدم غير جذري
RUN useradd -m -u 10001 appuser
WORKDIR /app

# تثبيت المتطلبات أولًا
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# نسخ السورس وأليـمبك
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

# نسخ الـ entrypoint ومعالجة الترميز والصلاحيات
COPY entrypoint.sh /app/entrypoint.sh
# ✅ إصلاح الترميز (السبب رقم 2) ومنح صلاحيات التنفيذ (السبب رقم 1)
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# مسار بايثون + صلاحيات
ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

# المنفذ + Healthcheck
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

# تشغيل الترحيلات عبر الـ entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# CMD بصيغة shell-form للسماح بتوسعة متغير ${PORT} بواسطة الـ entrypoint
CMD uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
# --- END OF FILE ---```

#### 2. ملف: `entrypoint.sh`

**التغييرات المدمجة:**
*   **(`#!/bin/sh`)**: يؤكد على وجود Shebang الصحيح (السبب رقم 3).
*   **(`set -e`)**: يضمن توقف التنفيذ فورًا عند حدوث أي خطأ (أفضل ممارسة).
*   **(`exec sh -c "$@"`)**: الطريقة الصحيحة لتنفيذ `CMD` القادم من `Dockerfile` والذي يحتاج إلى توسيع متغيرات البيئة (مثل `${PORT}`).

```sh
# --- START OF FILE: entrypoint.sh ---
# ✅ تأكيد Shebang الصحيح (السبب رقم 3)
#!/bin/sh

# Fail fast: Exit immediately if a command exits with a non-zero status.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application..."
# مهم: استخدام sh -c يسمح بتوسعة المتغيرات مثل ${PORT} القادمة من CMD بصيغة shell
exec sh -c "$@"
# --- END OF FILE ---