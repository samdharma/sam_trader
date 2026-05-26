#!/bin/bash
set -e

# 3-layer health check for Futu OpenD
# L1: Process check — verify FutuOpenD is running
# L2: Socket check — verify API port is accepting connections
# L3: Protocol check — verify most recent GTWLog contains "Login successful"

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

# --- L3: Verify login success in most recent GTWLog ---
LOG_DIR="/home/futu/.com.futunn.FutuOpenD/Log"
if [ ! -d "$LOG_DIR" ]; then
    echo "UNHEALTHY: FutuOpenD log directory not found"
    exit 1
fi

# Find the most recently modified GTWLog file (portable: ls -t works on GNU and BSD)
# Filter to GTWLog_* only — .ftlog and Monitor.log don't contain "Login successful"
MOST_RECENT_LOG=$(ls -t "$LOG_DIR"/GTWLog_* 2>/dev/null | head -n 1)

if [ -z "$MOST_RECENT_LOG" ] || [ ! -f "$MOST_RECENT_LOG" ]; then
    echo "UNHEALTHY: No FutuOpenD log files found"
    exit 1
fi

# Positively verify "Login successful" in the most recent log
if ! grep -q "Login successful" "$MOST_RECENT_LOG" 2>/dev/null; then
    echo "UNHEALTHY: Login successful not found in most recent GTWLog"
    exit 1
fi

# Retain failure-pattern scan as defense-in-depth (most recent log only)
if grep -iE "login fail|login failed|conn failed|authentication fail|auth fail|account login" "$MOST_RECENT_LOG" > /dev/null 2>&1; then
    echo "UNHEALTHY: Login failure pattern detected in most recent GTWLog"
    exit 1
fi

exit 0
