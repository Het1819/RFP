# scripts/smoke_test.ps1
# Staging/Pilot Deployment Smoke Test Script for Windows
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -BaseUrl "http://localhost:8000"

param (
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Running Staging Deployment Smoke Tests..." -ForegroundColor Cyan
Write-Host "Target Base URL: $BaseUrl" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Helper to verify HTTP status and location header
function Test-Endpoint {
    param (
        [string]$Path,
        [int]$ExpectedStatus,
        [string]$ExpectedRedirectPath = $null
    )

    $Url = "$BaseUrl$Path"
    Write-Host "Checking $Url ... " -NoNewline -ForegroundColor Yellow

    try {
        # We use Invoke-WebRequest but skip auto redirect to check redirect codes
        $Response = Invoke-WebRequest -Uri $Url -MaximumRedirection 0 -UseBasicParsing -Headers @{ "Accept" = "text/html" } -ErrorAction SilentlyContinue

        $Status = $Response.StatusCode
        if ($null -eq $Status) {
            # In PowerShell, if status is a redirect, it might throw or set in $error
            $Status = $Response.BaseResponse.StatusCode
        }
    }
    catch {
        $Status = $_.Exception.Response.StatusCode.value__
        $Response = $_.Exception.Response
    }

    if ($Status -eq $ExpectedStatus) {
        if ($ExpectedRedirectPath) {
            $Location = $Response.Headers["Location"]
            if ($Location -and ($Location -match $ExpectedRedirectPath -or $Location.EndsWith($ExpectedRedirectPath))) {
                Write-Host "PASS (Status: $Status, Redirect to: $Location)" -ForegroundColor Green
                return $true
            } else {
                Write-Host "FAIL (Status: $Status, Redirect location '$Location' does not match '$ExpectedRedirectPath')" -ForegroundColor Red
                return $false
            }
        } else {
            Write-Host "PASS (Status: $Status)" -ForegroundColor Green
            return $true
        }
    } else {
        Write-Host "FAIL (Expected Status: $ExpectedStatus, Got: $Status)" -ForegroundColor Red
        return $false
    }
}

# 1. Verify Liveness Endpoint
$Liveness = Test-Endpoint -Path "/healthz" -ExpectedStatus 200
if (-not $Liveness) { Throw "Smoke test failed at /healthz check" }

# 2. Verify Readiness Endpoint
$Readiness = Test-Endpoint -Path "/readyz" -ExpectedStatus 200
if (-not $Readiness) { Throw "Smoke test failed at /readyz check" }

# 3. Verify Login Page Loads
$LoginLoad = Test-Endpoint -Path "/login" -ExpectedStatus 200
if (-not $LoginLoad) { Throw "Smoke test failed at /login load check" }

# 4. Verify Unauthenticated Route Redirects to Login
$RootRedirect = Test-Endpoint -Path "/projects" -ExpectedStatus 303 -ExpectedRedirectPath "/login"
if (-not $RootRedirect) { Throw "Smoke test failed at unauthenticated root check" }

# 5. Verify Unauthenticated KPI Dashboard Redirects to Login
$DashboardRedirect = Test-Endpoint -Path "/projects/ops/dashboard" -ExpectedStatus 303 -ExpectedRedirectPath "/login"
if (-not $DashboardRedirect) { Throw "Smoke test failed at unauthenticated KPI dashboard check" }

# 6. Verify Prometheus Metrics Requires Session/DB access (returns 200 when local, but let's just make sure it loads)
$MetricsLoad = Test-Endpoint -Path "/metrics" -ExpectedStatus 200
if (-not $MetricsLoad) { Throw "Smoke test failed at /metrics check" }

# 7. Check Static Assets Bundle
$ManifestPath = "app/static/dist/marketing.js"
Write-Host "Checking static assets bundle existence ... " -NoNewline -ForegroundColor Yellow
if (Test-Path $ManifestPath) {
    Write-Host "PASS (Bundle exists at $ManifestPath)" -ForegroundColor Green
} else {
    Write-Host "FAIL (Bundle missing)" -ForegroundColor Red
    Throw "Smoke test failed: static assets bundle does not exist. Run npm run assets:build first."
}

Write-Host "==========================================" -ForegroundColor Green
Write-Host "All Staging Smoke Tests Passed Cleanly!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
