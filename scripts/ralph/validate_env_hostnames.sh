#!/usr/bin/env bash
# validate_env_hostnames.sh — Warn if .env hostnames do not match docker-compose service names.
#
# Usage:
#   bash scripts/ralph/validate_env_hostnames.sh
#
# Exit codes: 0 = OK or warnings only, 1 = fatal error

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
EXAMPLE_FILE="${PROJECT_DIR}/.env.example"
COMPOSE_FILE="${PROJECT_DIR}/docker/docker-compose.yml"

# Determine which env file to audit
if [[ -f "${ENV_FILE}" ]]; then
    TARGET="${ENV_FILE}"
elif [[ -f "${EXAMPLE_FILE}" ]]; then
    TARGET="${EXAMPLE_FILE}"
else
    echo "WARN: Neither .env nor .env.example found — cannot validate hostnames"
    exit 0
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "WARN: ${COMPOSE_FILE} not found — cannot validate hostnames"
    exit 0
fi

# Extract service names from docker-compose.yml (top-level 'services:' keys)
SERVICE_NAMES=$(awk '
/^services:/ { in_services=1; next }
in_services && /^  [a-zA-Z0-9_-]+:/ { print substr($1, 1, length($1)-1) }
in_services && /^[a-zA-Z]/ && !/^  / { exit }
' "${COMPOSE_FILE}" 2>/dev/null || true)

if [[ -z "${SERVICE_NAMES}" ]]; then
    echo "WARN: Could not parse service names from ${COMPOSE_FILE}"
    exit 0
fi

WARNINGS=0

# Helper: check a single env var
_check_host() {
    local var_name="$1"
    local friendly_name="$2"
    local value
    value=$(grep -E "^${var_name}=" "${TARGET}" 2>/dev/null | tail -n1 | cut -d'=' -f2- || true)
    if [[ -z "${value}" ]]; then
        return
    fi

    if ! echo "${SERVICE_NAMES}" | grep -qx "${value}"; then
        echo "WARN: ${TARGET} sets ${var_name}=${value} which does not match any service name in ${COMPOSE_FILE} (${friendly_name})"
        WARNINGS=$((WARNINGS + 1))
    fi
}

_check_host "POSTGRES_HOST" "PostgreSQL"
_check_host "REDIS_HOST" "Redis"
_check_host "IB_GATEWAY_HOST" "IB Gateway"
_check_host "FUTU_OPEND_HOST" "Futu OpenD"

if [[ ${WARNINGS} -eq 0 ]]; then
    echo "OK: All hostnames in ${TARGET} match docker-compose service names"
    exit 0
else
    echo "WARN: ${WARNINGS} hostname mismatch(es) detected — review ${TARGET}"
    exit 0
fi
