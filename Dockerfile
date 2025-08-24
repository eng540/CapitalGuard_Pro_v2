FROM python:3.11-slim

WORKDIR /app

# تثبيت الاعتمادات
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY src ./src
ENV PYTHONPATH=/app/src

# Railway يمرر PORT تلقائياً
EXPOSE 8000

# شغل Uvicorn بالمنفذ اللي توفره Railway
CMD ["sh", "-c", "uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port $PORT"]