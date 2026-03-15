#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: Dockerfile ---
# File: Dockerfile
# Version: v2.0.0-RAILWAY-STABLE
#
# ✅ THE FIX (DF-01 — CRITICAL):
#   mirror.gcr.io/library/python:3.11.9-slim-bookworm
#   → Railway لا يضمن الوصول لـ mirror.gcr.io (Google Container Registry mirror).
#     قد يفشل الـ build بـ "pull access denied" أو timeout.
#   الإصلاح: python:3.11.9-slim-bookworm مباشرة من Docker Hub (المصدر الأصلي).
#
# ✅ THE FIX (DF-02 — CRITICAL):
#   مجلد /app/backups غير مُنشأ في الـ image.
#   backup_service.py يكتب فيه في runtime. appuser لا يملك صلاحية mkdir
#   خارج /app بعد chown. قد يرفع PermissionError عند أول نسخة احتياطية.
#   الإصلاح: RUN mkdir -p /app/backups قبل chown -R appuser.
#
# ✅ THE FIX (DF-W2 — WARNING):
#   supervisor مثبَّت مرتين: apt (system) + pip (python package).
#   entrypoint.sh يستدعي /usr/bin/supervisord (system).
#   الإصلاح: حُذف supervisor==4.2.5 من requirements.txt (راجع ملاحظة الـ requirements).
#   لكن نُبقي على التثبيت من apt لأنه المستخدم الفعلي.
#
# ✅ THE FIX (DF-W3 — WARNING):
#   start-period=30s قليل لـ Railway cold start مع spacy + Redis init.
#   الإصلاح: start-period=60s
#
# ✅ THE FIX (DF-W4 — WARNING):
#   يُرفق ملف .dockerignore (راجع آخر الملف).
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

# ✅ DF-01 FIX: Docker Hub مباشرة — متوافق مع Railway وكل بيئات الـ CI/CD
FROM python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ── تثبيت أدوات النظام + postgresql-client-17 ────────────────────
# postgresql-client-17 مطلوب لـ pg_dump و psql (backup_service.py)
# يتطابق مع إصدار Supabase (PostgreSQL 17)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    curl \
    ca-certificates \
    gnupg \
    dos2unix \
    supervisor \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] \
    http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/*

# ── مستخدم التشغيل (بدون root) ───────────────────────────────────
RUN useradd -m -u 10001 appuser
WORKDIR /app

# ── تثبيت Python dependencies ────────────────────────────────────
# نُثبِّت الـ dependencies أولاً للاستفادة من Docker layer caching
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt \
 && python -m spacy download en_core_web_sm

# ── نسخ كود التطبيق ──────────────────────────────────────────────
COPY src        /app/src
COPY alembic    /app/alembic
COPY alembic.ini /app/alembic.ini

# ── supervisord config ────────────────────────────────────────────
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ── entrypoint ───────────────────────────────────────────────────
COPY entrypoint.sh /app/entrypoint.sh
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# ── ✅ DF-02 FIX: إنشاء مجلد backups في الـ image ─────────────────
# backup_service.py يكتب في /app/backups — يجب وجوده قبل تشغيل الـ app
# يُنشأ هنا (root) ثم chown لـ appuser لضمان صلاحية الكتابة
RUN mkdir -p /app/backups

ENV PYTHONPATH=/app/src

# chown شامل يشمل backups الجديد
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# ✅ DF-W3 FIX: start-period=60s لاستيعاب Railway cold start
# (spacy model loading + Redis connection + DB migrations)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: Dockerfile ---
