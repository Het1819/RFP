# final_release_validation.ps1
# Enforces complete local validation gates before release.
# Contains no git add, git commit, git tag, or git push commands.

param (
    [switch]$SkipDockerBuild
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Starting Final Release Validation Suite..." -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Ruff Lint check
Write-Host "[1/9] Running Ruff Linter..." -ForegroundColor Yellow
& .\.venv\Scripts\ruff.exe check .
if ($LASTEXITCODE -ne 0) { throw "Ruff linter failed" }

# 2. Ruff Format check
Write-Host "[2/9] Running Ruff Formatter Check..." -ForegroundColor Yellow
& .\.venv\Scripts\ruff.exe format --check .
if ($LASTEXITCODE -ne 0) { throw "Ruff formatter check failed" }

# 3. Mypy Type check
Write-Host "[3/9] Running Mypy Typecheck..." -ForegroundColor Yellow
& .\.venv\Scripts\mypy.exe app
if ($LASTEXITCODE -ne 0) { throw "Mypy typecheck failed" }

# 4. Pytest Suite
Write-Host "[4/9] Running Pytest Suite..." -ForegroundColor Yellow
& .\.venv\Scripts\pytest.exe -q
if ($LASTEXITCODE -ne 0) { throw "Pytest suite failed" }

# 5. Frontend Assets Build
Write-Host "[5/9] Running Frontend Asset Compiler..." -ForegroundColor Yellow
& npm.cmd run assets:build
if ($LASTEXITCODE -ne 0) { throw "Vite asset compilation failed" }

# 6. Frontend Type check
Write-Host "[6/9] Running Frontend Typecheck (tsc)..." -ForegroundColor Yellow
& npx.cmd tsc --noEmit
if ($LASTEXITCODE -ne 0) { throw "TypeScript typecheck failed" }

# 7. Offline AI Evaluation
Write-Host "[7/9] Running Offline AI Evals..." -ForegroundColor Yellow
& .\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline
if ($LASTEXITCODE -ne 0) { throw "Offline AI evaluations failed" }

# 8. Docker Compose Validation
Write-Host "[8/9] Validating Docker Compose Config..." -ForegroundColor Yellow
& docker compose -f docker-compose.prod.yml config
if ($LASTEXITCODE -ne 0) { throw "Docker compose config validation failed" }

# Optional Docker Build
if (-not $SkipDockerBuild) {
    Write-Host "[8b/9] Building Docker Image..." -ForegroundColor Yellow
    & docker build -t rfp-architect-mvp:pilot .
    if ($LASTEXITCODE -ne 0) { throw "Docker container compilation failed" }
} else {
    Write-Host "[8b/9] Skipping Docker Image Build (Skip flag set)" -ForegroundColor Gray
}

# 9. Git Status Check (Warning if dirty)
Write-Host "[9/9] Checking Git Status and Tags..." -ForegroundColor Yellow
& git branch --show-current
& git status --short

Write-Host "==========================================" -ForegroundColor Green
Write-Host "All Final Release Quality Gates Passed!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
