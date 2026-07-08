#!/usr/bin/env bash
# final_release_validation.sh
# Enforces complete local validation gates before release.
# Contains no git add, git commit, git tag, or git push commands.

set -euo pipefail

SKIP_DOCKER_BUILD=false

for arg in "$@"; do
  if [ "$arg" == "--skip-docker-build" ] || [ "$arg" == "-SkipDockerBuild" ]; then
    SKIP_DOCKER_BUILD=true
  fi
done

echo "=========================================="
echo "Starting Final Release Validation Suite..."
echo "=========================================="

# 1. Ruff Lint check
echo "[1/9] Running Ruff Linter..."
./.venv/bin/ruff check .

# 2. Ruff Format check
echo "[2/9] Running Ruff Formatter Check..."
./.venv/bin/ruff format --check .

# 3. Mypy Type check
echo "[3/9] Running Mypy Typecheck..."
./.venv/bin/mypy app

# 4. Pytest Suite
echo "[4/9] Running Pytest Suite..."
./.venv/bin/pytest -q

# 5. Frontend Assets Build
echo "[5/9] Running Frontend Asset Compiler..."
npm run assets:build

# 6. Frontend Type check
echo "[6/9] Running Frontend Typecheck (tsc)..."
npx tsc --noEmit

# 7. Offline AI Evaluation
echo "[7/9] Running Offline AI Evals..."
./.venv/bin/python scripts/run_ai_eval.py --offline

# 8. Docker Compose Validation
echo "[8/9] Validating Docker Compose Config..."
docker compose -f docker-compose.prod.yml config

# Optional Docker Build
if [ "$SKIP_DOCKER_BUILD" = false ]; then
    echo "[8b/9] Building Docker Image..."
    docker build -t rfp-architect-mvp:pilot .
else
    echo "[8b/9] Skipping Docker Image Build (Skip flag set)"
fi

# 9. Git Status Check
echo "[9/9] Checking Git Status and Tags..."
git branch --show-current
git status --short

echo "=========================================="
echo "All Final Release Quality Gates Passed!"
echo "=========================================="
