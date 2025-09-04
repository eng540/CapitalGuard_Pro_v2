#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

# إعداد المنفذ للـAPI
PORT="${PORT:-8000}"

echo "Starting Telegram bot (background)..."
python -m capitalguard.interfaces.telegram.bot &

echo "Starting API server (foreground) on port ${PORT}..."
if [ "$1" = "uvicorn" ]; then
  shift
  exec uvicorn "$@" --host 0.0.0.0 --port "$PORT"
else
  exec sh -c "$@"
fi