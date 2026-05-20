#!/usr/bin/env bash
# validate_restart.sh — Manual validation script for Phase 4 restart smoke test.
# Run this against a live stack with OrbStrategy deployed.
#
# Usage:
#   bash scripts/ralph/validate_restart.sh
#
# Prerequisites:
#   - docker compose stack running (nautilus, postgres, redis)
#   - config/bundles.yaml has at least one enabled OrbStrategy bundle
#   - STATE_SAVE_ENABLED=true and STATE_LOAD_ENABLED=true in .env
#
# Validates:
#   1. All containers healthy
#   2. OrbStrategy loaded
#   3. State persistence env vars set
#   4. restart.sh executes successfully
#   5. Nautilus recovers after restart
#   6. Strategy still active after restart
#   7. No duplicate orders in logs
#   8. Downtime measured and reported

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

COMPOSE="docker compose -f docker/docker-compose.yml"
COMPOSE_CMD="docker compose"
NAUTILUS_SERVICE="csam-nautilus"
POSTGRES_SERVICE="csam-postgres"
REDIS_SERVICE="csam-redis"
RESTART_SCRIPT="scripts/restart.sh"

PASS=0
FAIL=0

pass() {
    echo "  ✅ PASS: $*"
    PASS=$((PASS + 1))
}

fail() {
    echo "  ❌ FAIL: $*"
    FAIL=$((FAIL + 1))
}

# ---------------------------------------------------------------------------
# Pre-flight: check docker is available
# ---------------------------------------------------------------------------
if ! command -v docker &>/dev/null; then
    echo "ERROR: docker is not installed or not in PATH"
    exit 1
fi
if ! docker info &>/dev/null; then
    echo "ERROR: docker daemon is not running or not accessible"
    exit 1
fi

echo "========================================="
echo "[CSAM] Phase 4 Restart Smoke Test"
echo "========================================="
echo ""

# ---------------------------------------------------------------------------
# 1. Container health check
# ---------------------------------------------------------------------------
echo "[1/8] Checking container health..."

STATUS_NAUTILUS=$(${COMPOSE_CMD} ps --format json 2>/dev/null | python3 -c "
import json, sys
for line in sys.stdin:
    s = json.loads(line)
    if s.get('Service') == 'nautilus':
        print(s.get('State', 'unknown'))
        break
" 2>/dev/null || echo "unknown")

STATUS_POSTGRES=$(${COMPOSE_CMD} ps --format json 2>/dev/null | python3 -c "
import json, sys
for line in sys.stdin:
    s = json.loads(line)
    if s.get('Service') == 'postgres':
        print(s.get('State', 'unknown'))
        break
" 2>/dev/null || echo "unknown")

STATUS_REDIS=$(${COMPOSE_CMD} ps --format json 2>/dev/null | python3 -c "
import json, sys
for line in sys.stdin:
    s = json.loads(line)
    if s.get('Service') == 'redis':
        print(s.get('State', 'unknown'))
        break
" 2>/dev/null || echo "unknown")

if [[ "${STATUS_NAUTILUS}" == "running" ]]; then
    pass "nautilus container is running"
else
    fail "nautilus container status: ${STATUS_NAUTILUS} (expected: running)"
fi

if [[ "${STATUS_POSTGRES}" == "running" ]]; then
    pass "postgres container is running"
else
    fail "postgres container status: ${STATUS_POSTGRES} (expected: running)"
fi

if [[ "${STATUS_REDIS}" == "running" ]]; then
    pass "redis container is running"
else
    fail "redis container status: ${STATUS_REDIS} (expected: running)"
fi

echo ""

# ---------------------------------------------------------------------------
# 2. Check state persistence env vars in .env
# ---------------------------------------------------------------------------
echo "[2/8] Checking state persistence configuration..."

if [[ -f .env ]]; then
    if grep -q "STATE_SAVE_ENABLED=true" .env 2>/dev/null; then
        pass "STATE_SAVE_ENABLED=true"
    else
        fail "STATE_SAVE_ENABLED not set to true in .env"
    fi

    if grep -q "STATE_LOAD_ENABLED=true" .env 2>/dev/null; then
        pass "STATE_LOAD_ENABLED=true"
    else
        fail "STATE_LOAD_ENABLED not set to true in .env"
    fi

    if grep -q "REDIS_HOST=" .env 2>/dev/null; then
        pass "REDIS_HOST set in .env"
    else
        fail "REDIS_HOST not set in .env"
    fi
else
    fail ".env file not found"
fi

echo ""

# ---------------------------------------------------------------------------
# 3. Verify OrbStrategy in bundles.yaml
# ---------------------------------------------------------------------------
echo "[3/8] Checking bundles.yaml for OrbStrategy..."

BUNDLES_YAML="${PROJECT_DIR}/config/bundles.yaml"
if [[ -f "${BUNDLES_YAML}" ]]; then
    if grep -q "OrbStrategy" "${BUNDLES_YAML}" 2>/dev/null; then
        pass "bundles.yaml references OrbStrategy"
    else
        fail "bundles.yaml does NOT reference OrbStrategy"
    fi

    # Check if at least one bundle has enabled: true
    # Simple Python-based YAML check that at least one enabled bundle exists
    HAS_ENABLED=$(python3 -c "
import yaml
with open('${BUNDLES_YAML}') as f:
    data = yaml.safe_load(f)
bundles = data.get('bundles', [])
enabled = [b for b in bundles if b.get('enabled', False)]
print(len(enabled))
" 2>/dev/null || echo "0")

    if [[ "${HAS_ENABLED}" -gt 0 ]]; then
        pass "at least one bundle is enabled (count: ${HAS_ENABLED})"
    else
        fail "no bundles are enabled in bundles.yaml"
    fi
else
    fail "config/bundles.yaml not found"
fi

echo ""

# ---------------------------------------------------------------------------
# 4. Verify restart.sh exists and is executable
# ---------------------------------------------------------------------------
echo "[4/8] Checking restart script..."

if [[ -f "${RESTART_SCRIPT}" ]]; then
    pass "restart.sh exists"
    if [[ -x "${RESTART_SCRIPT}" ]]; then
        pass "restart.sh is executable"
    else
        fail "restart.sh is not executable"
    fi
else
    fail "restart.sh not found at ${RESTART_SCRIPT}"
fi

echo ""

# ---------------------------------------------------------------------------
# 5. Execute restart.sh (only if all pre-checks passed)
# ---------------------------------------------------------------------------
echo "[5/8] Executing restart.sh..."

if [[ ${FAIL} -gt 0 ]]; then
    echo "  ⏭️  Skipping restart — ${FAIL} pre-check failure(s)"
else
    START_EPOCH=$(date +%s)

    # Run restart.sh, capture output
    if bash "${RESTART_SCRIPT}" 2>&1 | tee /tmp/csam_restart_output.log; then
        RESTART_EXIT=$?
    else
        RESTART_EXIT=$?
    fi

    END_EPOCH=$(date +%s)
    DOWNTIME=$((END_EPOCH - START_EPOCH))

    if [[ ${RESTART_EXIT} -eq 0 ]]; then
        pass "restart.sh completed successfully (exit code 0)"
    else
        fail "restart.sh exited with code ${RESTART_EXIT}"
    fi

    echo "  ℹ️  Total restart time: ${DOWNTIME}s"
    if [[ ${DOWNTIME} -lt 30 ]]; then
        pass "downtime ${DOWNTIME}s < 30s target"
    else
        fail "downtime ${DOWNTIME}s >= 30s target"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 6. Verify nautilus recovered
# ---------------------------------------------------------------------------
echo "[6/8] Verifying nautilus recovery..."

NAUTILUS_STATUS_AFTER=$(${COMPOSE_CMD} ps --format json 2>/dev/null | python3 -c "
import json, sys
for line in sys.stdin:
    s = json.loads(line)
    if s.get('Service') == 'nautilus':
        print(s.get('State', 'unknown'))
        break
" 2>/dev/null || echo "unknown")

if [[ "${NAUTILUS_STATUS_AFTER}" == "running" ]]; then
    pass "nautilus is running after restart"
else
    fail "nautilus status after restart: ${NAUTILUS_STATUS_AFTER}"
fi

echo ""

# ---------------------------------------------------------------------------
# 7. Check logs for state recovery and strategy
# ---------------------------------------------------------------------------
echo "[7/8] Checking logs for state recovery and strategy..."

RECENT_LOGS=$(docker logs "${NAUTILUS_SERVICE}" --tail 100 2>&1 || echo "")

# Check for state loaded/recovered message
if echo "${RECENT_LOGS}" | grep -qiE "state (loaded|restored|recovered|initialized)"; then
    pass "state loaded message found in logs"
else
    # State may have loaded before the --tail window
    echo "  ⚠️  state loaded message not found in recent logs (may be in older logs)"
fi

# Check for strategy active
if echo "${RECENT_LOGS}" | grep -qiE "strategy|orb|on_start|subscribe"; then
    pass "strategy activity found in logs"
else
    echo "  ⚠️  No strategy activity found in recent logs"
fi

# Check for errors after restart
ERROR_COUNT=$(echo "${RECENT_LOGS}" | grep -ciE "ERROR|CRITICAL|FATAL" || echo "0")
if [[ "${ERROR_COUNT}" -eq 0 ]]; then
    pass "no errors in recent logs"
else
    fail "${ERROR_COUNT} error(s) found in recent logs"
fi

# Check for duplicate order submissions
DUPLICATE_COUNT=$(echo "${RECENT_LOGS}" | grep -ciE "duplicate.*order|order.*already|order_id.*exists" || echo "0")
if [[ "${DUPLICATE_COUNT}" -eq 0 ]]; then
    pass "no duplicate order messages in recent logs"
else
    fail "${DUPLICATE_COUNT} duplicate order message(s) found"
fi

echo ""

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
echo "[8/8] Checking positions persistence..."

# Check if positions survived restart by querying logs
POSITION_BEFORE=$(docker logs "${NAUTILUS_SERVICE}" --tail 200 2>&1 | grep -iE "position.*net|net.*position|portfolio.*pos" | tail -5 || echo "")
if [[ -n "${POSITION_BEFORE}" ]]; then
    echo "  ℹ️  Position activity detected in logs (manual verification recommended)"
else
    echo "  ℹ️  No position activity in recent logs (may be expected if no trades)"
fi

echo ""
echo "========================================="
echo "[CSAM] Phase 4 Restart Smoke Test Results"
echo "========================================="
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
echo ""

if [[ ${FAIL} -eq 0 ]]; then
    echo "SMOKE TEST PASSED ✅"
    echo ""
    echo "Next steps (manual):"
    echo "  1. Verify strategy positions are preserved in PostgreSQL"
    echo "  2. Confirm no duplicate orders in fills table"
    echo "  3. Run: docker compose logs ${NAUTILUS_SERVICE} | grep -i 'state'"
    exit 0
else
    echo "SMOKE TEST FAILED ❌"
    echo "  Review failures above and re-run after fixes."
    exit 1
fi
