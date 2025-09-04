#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Now, execute the command passed to this script (the Docker CMD)
# The 'exec' command is important because it replaces the shell process with the new process,
# allowing signals (like SIGTERM from 'docker stop') to be passed directly to the application.
echo "Starting application..."
exec "$@"