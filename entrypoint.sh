# --- START OF FILE: entrypoint.sh ---
#!/bin/sh

# Fail fast: Exit immediately if a command exits with a non-zero status.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application..."
# Important: Using 'sh -c' allows the expansion of variables like ${PORT}
# that are passed from the Dockerfile's shell-form CMD.
exec sh -c "$@"
# --- END OF FILE ---