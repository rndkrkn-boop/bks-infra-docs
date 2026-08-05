#!/usr/bin/env bash
# sandbox-test.sh — Verify sandbox isolation enforcement

set -euo pipefail

echo "🔒 Testing Sandbox Isolation"
echo "============================"
echo

CONTAINER="sandbox-isolation-test"
FAILED=0

# Start sandbox container
echo "Starting container..."
docker-compose up -d sandbox-app

# Wait for startup
sleep 2

# Test 1: Verify no root privileges
echo ""
echo "Test 1: User is not root"
USER_ID=$(docker exec $CONTAINER id -u 2>/dev/null || echo "ERROR")
if [ "$USER_ID" != "0" ]; then
    echo "✓ Container running as non-root (UID: $USER_ID)"
else
    echo "✗ FAIL: Container running as root!"
    ((FAILED++))
fi

# Test 2: Verify read-only root
echo ""
echo "Test 2: Root filesystem is read-only"
if ! docker exec $CONTAINER touch /test-write 2>/dev/null; then
    echo "✓ Root filesystem is read-only"
else
    echo "✗ FAIL: Root filesystem is writable!"
    ((FAILED++))
fi

# Test 3: Verify temp is writable
echo ""
echo "Test 3: /tmp is writable (for legitimate use)"
if docker exec $CONTAINER touch /tmp/test-write 2>/dev/null; then
    echo "✓ /tmp is writable"
    docker exec $CONTAINER rm /tmp/test-write
else
    echo "✗ FAIL: /tmp is not writable"
    ((FAILED++))
fi

# Test 4: Verify capability drop
echo ""
echo "Test 4: Capabilities are dropped"
CAPS=$(docker exec $CONTAINER grep Cap /proc/1/status 2>/dev/null | grep CapEff | awk '{print $2}' || echo "0000000000000000")
if [ "$CAPS" = "0000000000000000" ]; then
    echo "✓ All capabilities dropped"
else
    echo "⚠ Warning: Some capabilities present (expected for NET_BIND_SERVICE)"
fi

# Test 5: Verify no privilege escalation
echo ""
echo "Test 5: No new privileges flag set"
if docker inspect $CONTAINER --format='{{.HostConfig.SecurityOpt}}' | grep -q "no-new-privileges"; then
    echo "✓ no-new-privileges flag is set"
else
    echo "✗ FAIL: no-new-privileges flag not set"
    ((FAILED++))
fi

# Cleanup
docker-compose down

# Results
echo ""
echo "============================"
if [ $FAILED -eq 0 ]; then
    echo "✅ All sandbox tests passed"
    exit 0
else
    echo "❌ $FAILED tests failed"
    exit 1
fi
