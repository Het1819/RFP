#!/usr/bin/env bash
# scripts/check_all.sh
# Local CI quality gate validation helper for Linux / macOS.
#
# Usage:
#   ./scripts/check_all.sh

set -euo pipefail

echo "=========================================="
echo "Running Local CI Validation Gates..."
echo "=========================================="

echo "[1/8] Running Ruff Lint..."
.venv/bin/ruff check .

echo "[2/8] Running Ruff Format Check..."
.venv/bin/ruff format --check .

echo "[3/8] Running Mypy Type Check..."
.venv/bin/mypy app

echo "[4/8] Running Test Suite (pytest)..."
.venv/bin/pytest -q

echo "[5/8] Building Frontend Assets..."
npm run assets:build

echo "[6/8] Running Frontend Type Check (tsc)..."
npx tsc --noEmit

echo "[7/8] Running Offline AI Evals..."
.venv/bin/python scripts/run_ai_eval.py --offline

echo "[8/8] Validating Production Docker Compose Config..."
SESSION_SECRET_KEY="dummy-session-secret-key-32-chars-minimum" docker compose -f docker-compose.prod.yml config

echo "=========================================="
echo "All Local Quality Gates Passed Cleanly!"
echo "=========================================="
