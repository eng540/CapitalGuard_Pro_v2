#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Run database migrations synchronously
echo "Entrypoint: Running database migrations..."
# ✅ FIX: Removed the '&' to make the command blocking.
# This ensures the script waits for migrations to complete before proceeding.
alembic upgrade head

# Start the main application using supervisord
echo "Entrypoint: Migrations complete. Starting supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf