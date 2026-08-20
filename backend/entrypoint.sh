#!/bin/bash
set -e

# Auto-migrate for local development only
if [ "${AUTO_MIGRATE:-false}" = "true" ]; then
    echo "Running database migrations (AUTO_MIGRATE=true)..."
    cd /app
    alembic upgrade head
    echo "Migrations complete."
fi

if [ $# -gt 0 ]; then
    exec "$@"
else
    echo "Starting OMEGA API server..."
    exec uvicorn omega.main:app --host 0.0.0.0 --port 8000 --reload
fi

