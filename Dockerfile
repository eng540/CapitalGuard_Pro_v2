# Use a specific, well-supported base image for reproducibility.
FROM --platform=linux/amd64 python:3.11.9-slim-bookworm

# Set environment variables to optimize Python and pip.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Set the default path for Python modules.
    PYTHONPATH=/app/src

# Install system dependencies required for building Python packages and running the app.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
      libpq-dev \
      curl \
      ca-certificates \
      supervisor \
 && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security best practices.
RUN useradd -m -u 10001 appuser
WORKDIR /app

# Copy and install Python dependencies.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# Copy the application source code and related files.
COPY src /app/src
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Change ownership of the app directory to the non-root user.
RUN chown -R appuser:appuser /app
USER appuser

# Expose the port the application will run on.
EXPOSE 8000

# Define a health check to ensure the container is running correctly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

# No ENTRYPOINT is defined here. The command will be fully specified in docker-compose.