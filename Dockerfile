FROM python:3.11-slim

WORKDIR /app

# تثبيت الاعتمادات
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# نسخ كود التطبيق
COPY src ./src
ENV PYTHONPATH=/app/src

# المنصة تمرّر المنفذ عبر $PORT
ENV PORT=8000
EXPOSE 8000

# شغّل واجهة FastAPI (البوت مدمج عبر Webhook)
CMD ["uvicorn", "capitalguard.interfaces.api.main:app", "--host", "0.0.0.0", "--port", "8000"]