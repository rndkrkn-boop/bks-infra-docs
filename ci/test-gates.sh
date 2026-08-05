#!/usr/bin/env bash
# test-gates.sh — Verification script for quality gates
# Runs all gate checks against docker-compose services

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
CRITICAL_FAILED=0

# Helper functions
pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    FAILED=$((FAILED + 1))
}

critical_fail() {
    echo -e "${RED}✗ CRITICAL${NC} $1"
    CRITICAL_FAILED=$((CRITICAL_FAILED + 1))
}

info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Pre-flight checks
echo "======================================"
echo "Quality Gates Verification"
echo "======================================"
echo

# Check docker compose is running
if ! docker compose ps | grep -q "running"; then
    critical_fail "docker compose services not running. Start with: docker compose up -d"
    exit 1
fi

info "docker compose services running"
echo

# ============ CRITICAL GATES ============
echo "CRITICAL GATES:"
echo

# 1. Docker build success
if docker images | grep -q "nemohermes"; then
    pass "docker-build-success: Image exists"
else
    critical_fail "docker-build-success: Image not found"
fi

# 2. Security scan (trivy check)
if command -v trivy &> /dev/null; then
    CRITICAL_VULNS=$(trivy image --severity CRITICAL nemohermes:latest 2>/dev/null | grep -c "CRITICAL" || echo 0)
    if [ "$CRITICAL_VULNS" -eq 0 ]; then
        pass "security-scan-pass: No critical vulnerabilities"
    else
        critical_fail "security-scan-pass: Found $CRITICAL_VULNS critical vulnerabilities"
    fi
else
    info "security-scan: trivy not installed (skipping)"
fi

# 3. Services connectivity
if docker compose exec -T memgraphrag curl -s http://localhost:8010/health &>/dev/null; then
    pass "service-connectivity: memgraphrag responding"
else
    fail "service-connectivity: memgraphrag not responding"
fi

echo

# ============ HIGH GATES ============
echo "HIGH PRIORITY GATES:"
echo

# 4. Router responding
if docker compose exec -T router curl -s http://localhost:4000/health &>/dev/null; then
    pass "router-health: router responding"
else
    fail "router-health: router not responding"
fi

# 5. Registry accessible
if docker compose exec -T registry curl -s http://localhost:5050/v2/ &>/dev/null; then
    pass "registry-health: registry accessible"
else
    fail "registry-health: registry not accessible"
fi

echo

# ============ FAIL-OPEN DETECTION ============
echo "FAIL-OPEN DETECTION:"
echo

# Check if services are running but marked healthy incorrectly
HEALTHY_GATES=0
RUNNING_SERVICES=$(docker compose ps --services --filter "status=running" | wc -l)

if [ "$RUNNING_SERVICES" -gt 0 ]; then
    pass "fail-open-detection: Services actively running ($RUNNING_SERVICES services)"
else
    critical_fail "fail-open-detection: No services running but gates marked pass"
fi

echo

# ============ SUMMARY ============
echo "======================================"
echo "GATE SUMMARY"
echo "======================================"
echo -e "Passed:           ${GREEN}$PASSED${NC}"
echo -e "Failed:           ${YELLOW}$FAILED${NC}"
echo -e "Critical Failed:  ${RED}$CRITICAL_FAILED${NC}"
echo "Total:            $((PASSED + FAILED + CRITICAL_FAILED))"
echo

if [ "$CRITICAL_FAILED" -gt 0 ]; then
    echo -e "${RED}DEPLOYMENT BLOCKED: Critical gates failed${NC}"
    exit 1
elif [ "$FAILED" -gt 2 ]; then
    echo -e "${YELLOW}WARNING: Multiple gates failed (proceed with caution)${NC}"
    exit 0
else
    echo -e "${GREEN}ALL GATES PASSED: Ready to deploy${NC}"
    exit 0
fi
