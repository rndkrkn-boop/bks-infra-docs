#!/usr/bin/env bash
# verify-runtime.sh — Automated drift detection between ARCHITECTURE-RUNTIME.md and actual runtime
# Run before deployment to ensure documentation matches reality

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CHECKS_PASSED=0
CHECKS_FAILED=0

# Helper functions
check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

check_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

echo "======================================"
echo "🔍 Runtime Drift Detection"
echo "======================================"
echo

# ============ DOCKER-COMPOSE VALIDATION ============
echo "${BLUE}[1/5] Docker Compose Validation${NC}"
echo

if ! docker compose version &> /dev/null; then
    check_fail "docker compose (v2 plugin) not installed"
    exit 1
fi

if ! docker compose config > /dev/null 2>&1; then
    check_fail "docker-compose.yml is invalid"
    exit 1
fi

check_pass "docker-compose.yml is valid"

# ============ SERVICE RUNNING STATUS ============
echo
echo "${BLUE}[2/5] Service Running Status${NC}"
echo

EXPECTED_SERVICES=("router" "memgraphrag" "monitoring" "gitlab" "registry")
MISSING_SERVICES=0

for service in "${EXPECTED_SERVICES[@]}"; do
    if docker compose ps "$service" 2>/dev/null | grep -q "running"; then
        check_pass "$service is running"
    else
        check_fail "$service is NOT running"
        MISSING_SERVICES=$((MISSING_SERVICES + 1))
    fi
done

if [ "$MISSING_SERVICES" -gt 2 ]; then
    check_fail "Critical: More than 2 services down"
    exit 1
fi

# ============ PORT ACCESSIBILITY ============
echo
echo "${BLUE}[3/5] Port Accessibility${NC}"
echo

check_port() {
    local host="127.0.0.1"
    local port=$1
    local service=$2
    
    if timeout 2 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null; then
        check_pass "$service (port $port) is accessible"
    else
        check_fail "$service (port $port) is NOT accessible"
    fi
}

check_port 4000 "router"
check_port 8010 "memgraphrag"
check_port 3000 "monitoring"
check_port 8929 "gitlab"
check_port 5050 "registry"

# ============ HEALTH ENDPOINTS ============
echo
echo "${BLUE}[4/5] Health Endpoints${NC}"
echo

check_health() {
    local url=$1
    local service=$2
    
    if curl -sf "$url" > /dev/null 2>&1; then
        check_pass "$service health endpoint OK"
    else
        check_warn "$service health endpoint UNREACHABLE (non-critical)"
    fi
}

check_health "http://127.0.0.1:4000/health" "router"
check_health "http://127.0.0.1:8010/health" "memgraphrag"
check_health "http://127.0.0.1:3000" "monitoring"
check_health "http://127.0.0.1:5050/v2/" "registry"

# ============ DOCUMENTATION SYNC ============
echo
echo "${BLUE}[5/5] Documentation Sync Check${NC}"
echo

# Verify ARCHITECTURE-RUNTIME.md exists and contains expected services
if [ -f "ARCHITECTURE-RUNTIME.md" ]; then
    check_pass "ARCHITECTURE-RUNTIME.md exists"
    
    for service in "${EXPECTED_SERVICES[@]}"; do
        if grep -q "$service" ARCHITECTURE-RUNTIME.md; then
            check_pass "ARCHITECTURE-RUNTIME.md documents $service"
        else
            check_fail "ARCHITECTURE-RUNTIME.md missing documentation for $service"
        fi
    done
else
    check_fail "ARCHITECTURE-RUNTIME.md not found"
fi

# ============ FINAL SUMMARY ============
echo
echo "======================================"
echo "📊 Drift Detection Summary"
echo "======================================"
echo -e "Checks Passed: ${GREEN}$CHECKS_PASSED${NC}"
echo -e "Checks Failed: ${RED}$CHECKS_FAILED${NC}"
echo -e "Total Checks:  $((CHECKS_PASSED + CHECKS_FAILED))"
echo

if [ "$CHECKS_FAILED" -eq 0 ]; then
    echo -e "${GREEN}✓ NO DRIFT DETECTED${NC}"
    echo "Architecture documentation matches runtime. Safe to proceed."
    exit 0
else
    echo -e "${RED}✗ DRIFT DETECTED${NC}"
    echo "Investigate differences above. Update ARCHITECTURE-RUNTIME.md or fix runtime."
    exit 1
fi
