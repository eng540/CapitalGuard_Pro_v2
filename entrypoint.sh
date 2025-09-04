# --- START OF FILE: entrypoint.sh ---
#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
# This ensures that the container won't start if migrations fail.
set -e

# Run database migrations using Alembic.
echo "Running database migrations..."
alembic upgrade head

# Execute the CMD passed from the Dockerfile using a shell.
# This ensures that shell variables like ${PORT:-8000} are correctly expanded
# before the application starts.
echo "Starting application..."
exec sh -c "$@"
# --- END OF FIILE ---