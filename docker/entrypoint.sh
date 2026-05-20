#!/usr/bin/env bash
# SAM Trader V3 — Docker entrypoint
# Waits for PostgreSQL, Redis, and optionally IB Gateway / Futu OpenD.
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

# Only wait for IB Gateway when explicitly requested.
if [[ "${WAIT_FOR_IB_GATEWAY:-0}" == "1" ]]; then
    IB_GW_HOST="${IB_GATEWAY_HOST:-sam-ib-gateway}"
    IB_GW_PORT="${IB_GATEWAY_PORT:-4004}"

    echo "Waiting for IB Gateway at ${IB_GW_HOST}:${IB_GW_PORT} ..."

    python3 -c "
import socket, time, sys
deadline = time.time() + ${WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${IB_GW_HOST}', ${IB_GW_PORT}), timeout=2)
        s.close()
        print('IB Gateway is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: Cannot reach IB Gateway at ${IB_GW_HOST}:${IB_GW_PORT}')
sys.exit(1)
"
fi

if [[ "${WAIT_FOR_FUTU_OPEND:-0}" == "1" ]]; then
    FUTU_HOST="${FUTU_OPEND_HOST:-sam-futu-opend}"
    FUTU_PORT="${FUTU_OPEND_PORT:-11111}"

    echo "Waiting for Futu OpenD at ${FUTU_HOST}:${FUTU_PORT} ..."

    python3 -c "
import socket, time, sys
deadline = time.time() + ${WAIT_TIMEOUT}
while time.time() < deadline:
    try:
        s = socket.create_connection(('${FUTU_HOST}', ${FUTU_PORT}), timeout=2)
        s.close()
        print('Futu OpenD is ready')
        sys.exit(0)
    except (ConnectionRefusedError, OSError):
        time.sleep(2)
print('ERROR: Cannot reach Futu OpenD at ${FUTU_HOST}:${FUTU_PORT}')
sys.exit(1)
"
fi

exec "$@"
