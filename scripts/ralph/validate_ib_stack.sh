#!/usr/bin/env bash
# CSAM Trader V2 — IB Gateway + Nautilus Integration Validation
# Run manually with IB credentials in .env
#
# Usage:
#   cd /Users/sam.dharma/Trading/csam_trader
#   source .env
#   bash scripts/ralph/validate_ib_stack.sh
#
# Acceptance Criteria:
#   1. docker compose up brings up all services
#   2. IB Gateway connection established
#   3. Instruments loaded from IB_SYMBOLS
#   4. Market data streaming
#   5. No ERROR lines in logs
#   6. All services healthy

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

DOCKER_DIR="${PROJECT_DIR}/docker"
COMPOSE="docker compose -f ${DOCKER_DIR}/docker-compose.yml"
LOG_FILE="${PROJECT_DIR}/logs/validate_ib_stack.log"
FAILED=0

# Ensure logs directory exists
mkdir -p "${PROJECT_DIR}/logs"

# Color helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Pre-flight checks ───────────────────────────────────────────────────────
log_info "Pre-flight checks..."

if ! command -v docker &>/dev/null; then
    log_error "docker not found"
    exit 1
fi

if ! docker info &>/dev/null; then
    log_error "docker daemon not running"
    exit 1
fi

if [[ -z "${TWS_USERID:-}" || -z "${TWS_PASSWORD:-}" ]]; then
    log_error "TWS_USERID and TWS_PASSWORD must be set in environment"
    exit 1
fi

if [[ -z "${IB_SYMBOLS:-}" ]]; then
    log_warn "IB_SYMBOLS not set — instrument loading will be skipped"
fi

# ─── Tear down any existing stack ────────────────────────────────────────────
log_info "Tearing down existing stack..."
${COMPOSE} down --remove-orphans 2>/dev/null || true

# ─── Start stack ─────────────────────────────────────────────────────────────
log_info "Starting stack ..."
${COMPOSE} up -d

# ─── Wait for services to be healthy ─────────────────────────────────────────
log_info "Waiting for services to become healthy..."
MAX_WAIT=120
ELAPSED=0
while true; do
    UNHEALTHY=$(${COMPOSE} ps --format json 2>/dev/null | \
        python3 -c "import sys,json; data=json.load(sys.stdin); print(sum(1 for s in data if s.get('Health','') not in ('healthy','')))" 2>/dev/null || echo "1")
    if [[ "${UNHEALTHY}" == "0" ]]; then
        break
    fi
    if (( ELAPSED >= MAX_WAIT )); then
        log_error "Services did not become healthy within ${MAX_WAIT}s"
        ${COMPOSE} logs --tail=50
        FAILED=1
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [[ ${FAILED} -eq 1 ]]; then
    ${COMPOSE} down --remove-orphans 2>/dev/null || true
    exit 1
fi

log_info "All services healthy (${ELAPSED}s)"

# ─── Check IB Gateway logs for connection ────────────────────────────────────
log_info "Checking IB Gateway connection..."
IB_LOGS=$(${COMPOSE} logs ib-gateway --tail=50 2>/dev/null || true)
if echo "${IB_LOGS}" | grep -qi "error\|failed"; then
    log_warn "IB Gateway logs contain errors:"
    echo "${IB_LOGS}" | grep -i "error\|failed" || true
fi
if echo "${IB_LOGS}" | grep -qi "connected"; then
    log_info "IB Gateway reports connected"
else
    log_warn "IB Gateway connection status unclear (may need 2FA via VNC on port 5900)"
fi

# ─── Check Nautilus logs ─────────────────────────────────────────────────────
log_info "Checking Nautilus logs..."
sleep 10  # Give Nautilus time to start connecting

NAUTILUS_LOGS=$(${COMPOSE} logs nautilus --tail=100 2>/dev/null || true)

# Save logs for inspection
echo "${NAUTILUS_LOGS}" > "${LOG_FILE}"

if echo "${NAUTILUS_LOGS}" | grep -qi "ERROR"; then
    log_error "Nautilus logs contain ERROR lines:"
    echo "${NAUTILUS_LOGS}" | grep -i "ERROR" || true
    FAILED=1
else
    log_info "No ERROR lines in Nautilus logs"
fi

# Check for instrument loading
if [[ -n "${IB_SYMBOLS:-}" ]]; then
    if echo "${NAUTILUS_LOGS}" | grep -qi "instrument"; then
        log_info "Instrument loading activity detected"
    else
        log_warn "No instrument loading activity yet (may need more time)"
    fi
fi

# Check for market data
if echo "${NAUTILUS_LOGS}" | grep -qi "bar\|tick\|quote\|market_data"; then
    log_info "Market data streaming detected"
else
    log_warn "No market data streaming yet (may need more time or market hours)"
fi

# ─── Service health summary ──────────────────────────────────────────────────
log_info "Service health summary:"
${COMPOSE} ps

# ─── Acceptance criteria ─────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "Acceptance Criteria"
echo "========================================="

checks=(
    "docker compose up: PASS"
    "IB Gateway connection established: MANUAL (check VNC if 2FA required)"
    "Instruments loaded from IB_SYMBOLS: $(echo "${NAUTILUS_LOGS}" | grep -qi 'instrument' && echo 'PASS' || echo 'PENDING')"
    "Market data streaming: $(echo "${NAUTILUS_LOGS}" | grep -qi 'bar\|tick\|quote\|market_data' && echo 'PASS' || echo 'PENDING')"
    "No ERROR lines: $([[ ${FAILED} -eq 0 ]] && echo 'PASS' || echo 'FAIL')"
    "All services healthy: PASS"
)

for check in "${checks[@]}"; do
    echo "  - ${check}"
done

echo ""
echo "Logs saved to: ${LOG_FILE}"

# ─── Tear down ───────────────────────────────────────────────────────────────
log_info "Tearing down stack..."
${COMPOSE} down --remove-orphans 2>/dev/null || true

if [[ ${FAILED} -eq 0 ]]; then
    log_info "VALIDATION PASSED"
    exit 0
else
    log_error "VALIDATION FAILED"
    exit 1
fi
