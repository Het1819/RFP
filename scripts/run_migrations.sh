#!/bin/bash
set -e

echo "Running migrations..."
alembic upgrade head
echo "Migrations completed successfully!"
