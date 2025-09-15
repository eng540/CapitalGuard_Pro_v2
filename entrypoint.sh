#!/bin/bash
set -e

# This script is the robust entrypoint for both the API and Watcher services.
# It ensures that the database is ready before the application starts.

# Parameters:
# $1: The host of the database (e.g., "db")
# $2: The port of the database (e.g., "5432")
# $3 and onwards: The command to execute after the DB is ready (e.g., "supervisorctl", "python -m ...")

DB_HOST=$1
DB_PORT=$2
# Shift the first two arguments ($1 and $2) so that $@ contains the command to run.
shift 2
CMD="$@"

# --- Wait for Database ---
# We will use a small Python script to check the DB connection,
# as it's the most reliable method within this container.
echo "Entrypoint: Waiting for database at ${DB_HOST}:${DB_PORT}..."
python << END
import socket
import time
import os

host = os.getenv("DB_HOST", "${DB_HOST}")
port = int(os.getenv("DB_PORT", "${DB_PORT}"))
timeout = 20  # seconds

start_time = time.time()
while True:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print("Database is reachable.")
            break
    except (socket.timeout, ConnectionRefusedError, OSError) as ex:
        print(f"Database not yet available, waiting... ({ex})")
        if time.time() - start_time >= timeout:
            print(f"Error: Could not connect to database within {timeout} seconds.")
            exit(1)
        time.sleep(1)
END

# --- Run Migrations ---
echo "Entrypoint: Running database migrations..."
alembic upgrade head

# --- Start the Main Application ---
echo "Entrypoint: Migrations complete. Starting main command: ${CMD}"
exec $CMD
```# END