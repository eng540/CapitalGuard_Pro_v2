# --- START OF FILE: Dockerfile ---
# Use Google's mirror for the python image to improve reliability.
FROM --platform=linux/amd64 mirror.gcr.io/library/python:3.11.9-slim-bookworm

# Setup environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies required for psycopg, httpx, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev curl ca-certificates dos2unix \
 && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
RUN useradd -m -u 10001 appuser
WORKDIR /app

# Install Python packages first to leverage Docker's layer caching
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# Copy source code and migration scripts
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

# Copy the entrypoint script and make it executable
COPY entrypoint.sh /app/entrypoint.sh
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Set Python path and grant ownership to the app user
ENV PYTHONPATH=/app/src
RUN chown -R appuser:appuser /app
USER appuser

# Expose the port and set up a health check
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/ || exit 1

# Define the entrypoint and the default command
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "capitalguard.interfaces.api.main:app", "--host", "0.0.0.0", "--port", "${PORT:-8000}"]
# --- END OF FILE ---