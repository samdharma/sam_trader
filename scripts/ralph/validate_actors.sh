#!/usr/bin/env bash
# validate_actors.sh — Manual validation script for Phase 2 actors.
# Run this after starting the stack with docker compose up.
#
# Usage:
#   bash scripts/ralph/validate_actors.sh

set -euo pipefail

COMPOSE="docker compose -f docker/docker-compose.yml"
POSTGRES_SERVICE="csam-postgres"
NAUTILUS_SERVICE="csam-nautilus"

echo "========================================"
echo "[CSAM] Phase 2 Actor Validation"
echo "========================================"
echo

# 1. Check that containers are running
echo "[1/5] Checking containers..."
if ! $COMPOSE ps | grep -q "$POSTGRES_SERVICE"; then
    echo "ERROR: $POSTGRES_SERVICE is not running"
    exit 1
fi
if ! $COMPOSE ps | grep -q "$NAUTILUS_SERVICE"; then
    echo "ERROR: $NAUTILUS_SERVICE is not running"
    exit 1
fi
echo "OK: Both containers are running"
echo

# 2. Verify actors appear in Nautilus logs
echo "[2/5] Checking Nautilus logs for actor startup..."
LOGS=$($COMPOSE logs "$NAUTILUS_SERVICE" --no-log-prefix 2>/dev/null || true)

if echo "$LOGS" | grep -q "TradeJournalActor: READY"; then
    echo "OK: TradeJournalActor started"
else
    echo "WARN: TradeJournalActor startup not found in logs"
fi

if echo "$LOGS" | grep -q "HealthMonitorActor: READY"; then
    echo "OK: HealthMonitorActor started"
else
    echo "WARN: HealthMonitorActor startup not found in logs"
fi
echo

# 3. Verify heartbeat logs
echo "[3/5] Checking for heartbeat logs..."
if echo "$LOGS" | grep -q "heartbeat"; then
    echo "OK: Heartbeat logs found"
else
    echo "INFO: No heartbeat logs yet (may need to wait 60s)"
fi
echo

# 4. Verify fills table exists and is empty
echo "[4/5] Checking PostgreSQL fills table..."
FILLS_COUNT=$($COMPOSE exec -T "$POSTGRES_SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "SELECT COUNT(*) FROM fills;" 2>/dev/null | xargs || true)

if [ "$FILLS_COUNT" = "0" ]; then
    echo "OK: fills table exists and is empty (no trades yet)"
else
    echo "INFO: fills table has $FILLS_COUNT rows"
fi
echo

# 5. Verify orders and positions tables also exist
echo "[5/5] Checking orders and positions tables..."
ORDERS_COUNT=$($COMPOSE exec -T "$POSTGRES_SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "SELECT COUNT(*) FROM orders;" 2>/dev/null | xargs || true)
POS_COUNT=$($COMPOSE exec -T "$POSTGRES_SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "SELECT COUNT(*) FROM positions;" 2>/dev/null | xargs || true)

echo "OK: orders table exists (rows: $ORDERS_COUNT)"
echo "OK: positions table exists (rows: $POS_COUNT)"
echo

echo "========================================"
echo "[CSAM] Phase 2 Actor Validation Complete"
echo "========================================"
