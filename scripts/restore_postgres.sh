#!/usr/bin/env bash
# restore_postgres.sh
# Safely restores a PostgreSQL database backup for RFP Architect MVP.
#
# Usage:
#   BACKUP_FILE=./backups/postgres_backup_rfp_architect_xxx.dump ./restore_postgres.sh
#
# DO NOT hardcode passwords or credentials in this script.

set -euo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-rfp_architect}"

if [ -z "${BACKUP_FILE:-}" ]; then
  echo "Error: BACKUP_FILE environment variable is required."
  echo "Usage: BACKUP_FILE=/path/to/backup.dump ./restore_postgres.sh"
  exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "Error: Backup file not found at ${BACKUP_FILE}"
  exit 1
fi

echo "Starting PostgreSQL restore for database: ${DB_NAME} from ${BACKUP_FILE}..."

# Run pg_restore
pg_restore \
  -h "${DB_HOST}" \
  -p "${DB_PORT}" \
  -U "${DB_USER}" \
  -d "${DB_NAME}" \
  --clean \
  --if-exists \
  -v \
  "${BACKUP_FILE}"

echo "Database restore completed successfully!"
