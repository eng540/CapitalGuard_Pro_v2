#- START OF FILE: entrypoint.sh ---
#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application..."
# نحتاج توسعة ${PORT} من CMD بصيغة shell-form
exec sh -c "$@"
#--- END OF FILE: entrypoint.sh ---