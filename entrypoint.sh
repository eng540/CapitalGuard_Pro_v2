#!/bin/sh
set -e

echo "Entrypoint: Running database migrations..."
alembic upgrade head

echo "Entrypoint: Migrations complete. Starting supervisor..."
exec "$@"