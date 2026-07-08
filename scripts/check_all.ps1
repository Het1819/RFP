# scripts/check_all.ps1
# Local CI quality gate validation helper for Windows.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Running Local CI Validation Gates..." -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Ruff check
Write-Host "[1/8] Running Ruff Lint..." -ForegroundColor Yellow
& .\.venv\Scripts\ruff.exe check .
if ($LASTEXITCODE -ne 0) { Throw "Ruff check failed"; exit 1 }

# 2. Ruff format check
Write-Host "[2/8] Running Ruff Format Check..." -ForegroundColor Yellow
& .\.venv\Scripts\ruff.exe format --check .
if ($LASTEXITCODE -ne 0) { Throw "Ruff format check failed"; exit 1 }

# 3. Mypy check
Write-Host "[3/8] Running Mypy Type Check..." -ForegroundColor Yellow
& .\.venv\Scripts\mypy.exe app
if ($LASTEXITCODE -ne 0) { Throw "Mypy type check failed"; exit 1 }

# 4. Pytest suite
Write-Host "[4/8] Running Test Suite (pytest)..." -ForegroundColor Yellow
& .\.venv\Scripts\pytest.exe -q
if ($LASTEXITCODE -ne 0) { Throw "Test suite failed"; exit 1 }

# 5. Frontend Build
Write-Host "[5/8] Building Frontend Assets..." -ForegroundColor Yellow
& npm.cmd run assets:build
if ($LASTEXITCODE -ne 0) { Throw "Frontend assets build failed"; exit 1 }

# 6. Frontend TypeScript Check
Write-Host "[6/8] Running Frontend Type Check (tsc)..." -ForegroundColor Yellow
& npx.cmd tsc --noEmit
if ($LASTEXITCODE -ne 0) { Throw "tsc type check failed"; exit 1 }

# 7. Offline AI Eval
Write-Host "[7/8] Running Offline AI Evals..." -ForegroundColor Yellow
& .\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline
if ($LASTEXITCODE -ne 0) { Throw "Offline AI eval failed"; exit 1 }

# 8. Docker Compose Validate
Write-Host "[8/8] Validating Production Docker Compose Config..." -ForegroundColor Yellow
$env:SESSION_SECRET_KEY = "dummy-session-secret-key-32-chars-minimum"
& docker compose -f docker-compose.prod.yml config
if ($LASTEXITCODE -ne 0) { Throw "Docker compose validation failed"; exit 1 }

Write-Host "==========================================" -ForegroundColor Green
Write-Host "All Local Quality Gates Passed Cleanly!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
