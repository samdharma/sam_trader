#!/usr/bin/env bash
# SAM Trader V3 — Docker entrypoint
# Waits for PostgreSQL, Redis, and any enabled brokers (Futu OpenD, IB Gateway)
# gated on FUTU_ENABLED / IB_ENABLED env vars.
set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-sam-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-60}"

echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT} ..."

python3 -c "
import socket, time, sys
deadline = time.time() + ${WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${POSTGRES_HOST}', ${POSTGRES_PORT}), timeout=2)
        s.close()
        print('PostgreSQL is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: PostgreSQL not ready within ${WAIT_TIMEOUT}s')
sys.exit(1)
"

REDIS_HOST="${REDIS_HOST:-sam-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"

echo "Waiting for Redis at ${REDIS_HOST}:${REDIS_PORT} ..."

python3 -c "
import socket, time, sys
deadline = time.time() + ${WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${REDIS_HOST}', ${REDIS_PORT}), timeout=2)
        s.close()
        print('Redis is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: Redis not ready within ${WAIT_TIMEOUT}s')
sys.exit(1)
"

BROKER_WAIT_TIMEOUT="${BROKER_WAIT_TIMEOUT:-120}"

# Wait for IB Gateway only if IB is enabled.
if [[ "${IB_ENABLED:-false}" == "true" ]]; then
    IB_GW_HOST="${IB_GATEWAY_HOST:-sam-ib-gateway}"
    IB_GW_PORT="${IB_GATEWAY_PORT:-4004}"

    echo "Waiting for IB Gateway at ${IB_GW_HOST}:${IB_GW_PORT} (timeout ${BROKER_WAIT_TIMEOUT}s) ..."

    python3 -c "
import socket, time, sys
deadline = time.time() + ${BROKER_WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${IB_GW_HOST}', ${IB_GW_PORT}), timeout=2)
        s.close()
        print('IB Gateway is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: Cannot reach IB Gateway at ${IB_GW_HOST}:${IB_GW_PORT} within ${BROKER_WAIT_TIMEOUT}s')
sys.exit(1)
"
else
    echo "IB Gateway disabled (IB_ENABLED=${IB_ENABLED:-false}) — skipping wait."
fi

# Wait for Futu OpenD only if Futu is enabled.
if [[ "${FUTU_ENABLED:-false}" == "true" ]]; then
    FUTU_HOST="${FUTU_OPEND_HOST:-sam-futu-opend}"
    FUTU_PORT="${FUTU_OPEND_PORT:-11111}"

    echo "Waiting for Futu OpenD at ${FUTU_HOST}:${FUTU_PORT} (timeout ${BROKER_WAIT_TIMEOUT}s) ..."

    python3 -c "
import socket, time, sys
deadline = time.time() + ${BROKER_WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${FUTU_HOST}', ${FUTU_PORT}), timeout=2)
        s.close()
        print('Futu OpenD is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: Cannot reach Futu OpenD at ${FUTU_HOST}:${FUTU_PORT} within ${BROKER_WAIT_TIMEOUT}s')
sys.exit(1)
"
else
    echo "Futu OpenD disabled (FUTU_ENABLED=${FUTU_ENABLED:-false}) — skipping wait."
fi

# ── Environment consistency validation ──────────────────────────
# Guard against missing .env (docker compose -f sets project-dir to docker/,
# so root .env may not be auto-loaded).

if [[ "${FUTU_ENABLED:-false}" == "true" ]]; then
    if [[ -z "${FUTU_ACCOUNT_PWD_MD5:-}" ]]; then
        echo "FATAL: FUTU_ENABLED=true but FUTU_ACCOUNT_PWD_MD5 is empty." >&2
        echo "       Your .env may not be loaded by Docker Compose." >&2
        echo "       Ensure deploy.sh uses --env-file, or run:" >&2
        echo "         docker compose --env-file .env -f docker/docker-compose.yml up" >&2
        exit 1
    fi
fi

if [[ "${IB_ENABLED:-false}" == "true" ]]; then
    if [[ -z "${TWS_USERID:-}" || -z "${TWS_PASSWORD:-}" ]]; then
        echo "FATAL: IB_ENABLED=true but TWS_USERID or TWS_PASSWORD is empty." >&2
        echo "       Your .env may not be loaded by Docker Compose." >&2
        echo "       Ensure deploy.sh uses --env-file, or run:" >&2
        echo "         docker compose --env-file .env -f docker/docker-compose.yml up" >&2
        exit 1
    fi
fi

# ── End validation ───────────────────────────────────────────────

exec "$@"
