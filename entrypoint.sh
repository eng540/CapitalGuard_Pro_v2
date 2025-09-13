#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Run database migrations
echo "Entrypoint: Running database migrations..."
# ✅ --- الإصلاح: تم إزالة '&' لجعل الأمر متزامنًا ---
# هذا يضمن أن السكربت سينتظر حتى يكتمل الترحيل قبل المتابعة.
alembic upgrade head

# Start the main application using supervisord
echo "Entrypoint: Migrations complete. Starting supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf