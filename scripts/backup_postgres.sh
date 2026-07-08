#!/usr/bin/env bash
# backup_postgres.sh
# Safely backs up PostgreSQL database for RFP Architect MVP.
#
# Usage:
#   DB_HOST=localhost DB_PORT=5432 DB_USER=postgres DB_NAME=rfp_architect ./backup_postgres.sh
#
# DO NOT hardcode passwords or credentials in this script.

set -euo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-rfp_architect}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_FILE="${BACKUP_DIR}/postgres_backup_${DB_NAME}_${TIMESTAMP}.dump"

echo "Starting PostgreSQL backup for database: ${DB_NAME}..."
mkdir -p "${BACKUP_DIR}"

pg_dump \
  -h "${DB_HOST}" \
  -p "${DB_PORT}" \
  -U "${DB_USER}" \
  -F c \
  -b \
  -v \
  -f "${BACKUP_FILE}" \
  "${DB_NAME}"

echo "Backup completed successfully! Saved to: ${BACKUP_FILE}"
