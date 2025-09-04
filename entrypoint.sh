#!/bin/sh
# This script ensures the database is up-to-date before starting the main application.
set -e

echo "API Entrypoint: Running database migrations..."
alembic upgrade head

echo "API Entrypoint: Migrations complete. Starting application..."
# The 'exec' command replaces the shell process with the command that follows.
# '$@' evaluates to all the arguments passed to the script, which in this case
# will be the CMD from the Dockerfile.
exec "$@"