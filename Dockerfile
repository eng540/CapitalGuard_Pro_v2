# ---------- Dockerfile ----------
FROM python:3.11-slim

# اكسر الكاش كل مرة تغيّر الرقم (زِدْه عند كل نشر)
ARG BUILD_REV=3
ENV BUILD_REV=${BUILD_REV}

# لا نكتب .pyc ونفعل إخراج غير مُخزَّن
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# مكان التشغيل
WORKDIR /app

# حزم نظام خفيفة (اختياري)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# تثبيت باقات بايثون
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir supervisor

# نضمن أنه لن يتم سحب باكج خارجي باسم capitalguard بالصدفة
RUN python - <<'PY'
import pkgutil
print("HAS_PYPI_CAPITALGUARD? ", any(m.name=="capitalguard" for m in pkgutil.iter_modules()))
PY

# نسخ السورس وملف المشرف
COPY src /app/src
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# مهم: تعريف جذر الكود ليُرى كحزمة
ENV PYTHONPATH=/app/src

# Railway يضبط PORT
EXPOSE 8080

# عملية الإقلاع
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
# ---------- end ----------