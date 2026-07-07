#!/bin/bash
set -e

echo "Starting RFP Architect App..."

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start FastAPI application
echo "Starting Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
