#!/usr/bin/env bash
# scripts/smoke_test.sh
# Staging/Pilot Deployment Smoke Test Script for Linux/macOS
#
# Usage:
#   ./scripts/smoke_test.sh "http://localhost:8000"

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

echo "=========================================="
echo "Running Staging Deployment Smoke Tests..."
echo "Target Base URL: ${BASE_URL}"
echo "=========================================="

test_endpoint() {
    local path="$1"
    local expected_status="$2"
    local expected_redirect="$3"
    
    local url="${BASE_URL}${path}"
    echo -n "Checking ${url} ... "
    
    # Use curl to inspect response headers/status without following redirect
    local response
    response=$(curl -s -w "%{http_code} %{redirect_url}" -o /dev/null "${url}")
    
    local status
    status=$(echo "${response}" | cut -d' ' -f1)
    local redirect_url
    redirect_url=$(echo "${response}" | cut -d' ' -f2-)
    
    if [ "${status}" -eq "${expected_status}" ]; then
        if [ -n "${expected_redirect}" ]; then
            if [[ "${redirect_url}" == *"${expected_redirect}"* ]]; then
                echo -e "\e[32mPASS (Status: ${status}, Redirect: ${redirect_url})\e[0m"
                return 0
            else
                echo -e "\e[31mFAIL (Status: ${status}, Redirect location '${redirect_url}' does not match '${expected_redirect}')\e[0m"
                return 1
            fi
        else
            echo -e "\e[32mPASS (Status: ${status})\e[0m"
            return 0
        fi
    else
        echo -e "\e[31mFAIL (Expected Status: ${expected_status}, Got: ${status})\e[0m"
        return 1
    fi
}

# 1. Verify Liveness Endpoint
test_endpoint "/healthz" 200 "" || { echo "Liveness check failed"; exit 1; }

# 2. Verify Readiness Endpoint
test_endpoint "/readyz" 200 "" || { echo "Readiness check failed"; exit 1; }

# 3. Verify Login Page Loads
test_endpoint "/login" 200 "" || { echo "Login load check failed"; exit 1; }

# 4. Verify Unauthenticated Root Redirects to Login
test_endpoint "/" 303 "/login" || { echo "Unauthenticated root redirect check failed"; exit 1; }

# 5. Verify Unauthenticated KPI Dashboard Redirects to Login
test_endpoint "/projects/ops/dashboard" 303 "/login" || { echo "Unauthenticated KPI dashboard redirect check failed"; exit 1; }

# 6. Verify Prometheus Metrics Page Loads
test_endpoint "/metrics" 200 "" || { echo "Metrics check failed"; exit 1; }

# 7. Check Static Assets Manifest
MANIFEST_PATH="app/static/dist/manifest.json"
echo -n "Checking static assets manifest existence ... "
if [ -f "${MANIFEST_PATH}" ]; then
    echo -e "\e[32mPASS (Manifest exists at ${MANIFEST_PATH})\e[0m"
else
    echo -e "\e[31mFAIL (Manifest missing)\e[0m"
    echo "Smoke test failed: static assets manifest does not exist. Run npm run assets:build first."
    exit 1
fi

echo "=========================================="
echo -e "\e[32mAll Staging Smoke Tests Passed Cleanly!\e[0m"
echo "=========================================="
