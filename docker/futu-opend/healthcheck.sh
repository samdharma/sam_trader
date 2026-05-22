#!/bin/bash
set -e

# 3-layer health check for Futu OpenD
# L1: Process check — verify FutuOpenD is running
# L2: Socket check — verify API port is accepting connections
# L3: Log scan — detect login/connection failure patterns

# --- L1: Process check ---
if ! pgrep -x "FutuOpenD" > /dev/null 2>&1; then
    echo "UNHEALTHY: FutuOpenD process not running"
    exit 1
fi

# --- L2: Socket check ---
if ! true > /dev/tcp/localhost/11111 2>/dev/null; then
    echo "UNHEALTHY: API port 11111 not accepting connections"
    exit 1
fi

# --- L3: Log scan for login failure patterns ---
LOG_DIR="/home/futu/.com.futunn.FutuOpenD/log"
if [ -d "$LOG_DIR" ]; then
    # Find the most recently modified log files (up to 3)
    RECENT_LOGS=$(find "$LOG_DIR" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n 3 | cut -d' ' -f2-)

    if [ -n "$RECENT_LOGS" ]; then
        if echo "$RECENT_LOGS" | xargs grep -iE "login fail|login failed|conn failed|authentication fail|auth fail|account login" > /dev/null 2>&1; then
            echo "UNHEALTHY: Login failure pattern detected in FutuOpenD logs"
            exit 1
        fi
    fi
fi

exit 0
