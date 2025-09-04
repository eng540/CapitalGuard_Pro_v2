#!/bin/sh
set -e

echo "Entrypoint: Running database migrations..."
alembic upgrade head

# Export all existing environment variables so supervisor can see them
export

echo "Entrypoint: Migrations complete. Starting supervisor..."
exec "$@"

#```إضافة `export` قد لا تكون ضرورية في Railway،