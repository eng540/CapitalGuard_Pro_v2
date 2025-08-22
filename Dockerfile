FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip install supervisor

# كود التطبيق
COPY src ./src
ENV PYTHONPATH=/app/src

# ملفات المشرف
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start-all.sh /app/start-all.sh
RUN chmod +x /app/start-all.sh

# Railway يمرّر المنفذ عبر $PORT
ENV PORT=8000
EXPOSE 8000

CMD ["/usr/local/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]