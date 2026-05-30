#!/bin/bash
set -e

# 3-layer health check for Futu OpenD
# L1: Process check — verify FutuOpenD is running
# L2: Socket check — verify API port is accepting connections
# L3: Protocol check — verify "Login successful" exists in any GTWLog file

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

# --- L3: Verify login success across ALL GTWLog files ---
LOG_DIR="/home/futu/.com.futunn.FutuOpenD/Log"
if [ ! -d "$LOG_DIR" ]; then
    echo "UNHEALTHY: FutuOpenD log directory not found"
    exit 1
fi

# Check that GTWLog files exist at all
if ! ls "$LOG_DIR"/GTWLog_* >/dev/null 2>&1; then
    echo "UNHEALTHY: No FutuOpenD GTWLog files found"
    exit 1
fi

# Positively verify "Login successful" in ANY GTWLog file
# grep -lq stops at the first match across all files — correct even after log rotation
if ! grep -lq "Login successful" "$LOG_DIR"/GTWLog_* 2>/dev/null; then
    echo "UNHEALTHY: Login successful not found in any GTWLog"
    exit 1
fi

# Retain failure-pattern scan as defense-in-depth (scan ALL GTWLog files)
if grep -liE "login fail|login failed|conn failed|authentication fail|auth fail|account login" "$LOG_DIR"/GTWLog_* > /dev/null 2>&1; then
    echo "UNHEALTHY: Login failure pattern detected in GTWLog"
    exit 1
fi

exit 0
