# Python slim
FROM python:3.11-slim

# منع البايثون من كتابة .pyc وتسريب البافر
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# مكان التشغيل داخل الحاوية
WORKDIR /app

# تثبيت متطلبات النظام الخفيفة (اختياري حسب احتياجك)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# نسخ المتطلبات قبل السورس لزيادة كفاءة الكاش
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir supervisor

# نسخ السورس وملفات الإعداد
COPY src /app/src
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# مهم: تعريف ROOT للكود حتى تعمل import بالحزمة capitalguard.*
ENV PYTHONPATH=/app/src

# Railway يضبط PORT تلقائيًا، نُظهره فقط
EXPOSE 8080

# نقطة الدخول: Supervisor يدير api + bot
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]