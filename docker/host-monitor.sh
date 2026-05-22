#!/usr/bin/env bash
# =============================================================================
# SAM Trader V3 — Host-Level Container Monitor with Cooldown Protection
# =============================================================================
# Purpose:
#   Poll all sam-* containers every 60 seconds. Restart unhealthy containers.
#   Enforce cooldown to prevent restart loops (3 restarts in 15 min → 30 min
#   backoff).
#
# macOS usage (launchd):
#   1. Copy this script to /usr/local/bin/sam-host-monitor.sh
#   2. Copy docker/com.samtrader.monitor.plist to ~/Library/LaunchAgents/
#   3. launchctl load ~/Library/LaunchAgents/com.samtrader.monitor.plist
#   4. launchctl start com.samtrader.monitor
#
# Linux usage (systemd) — example service file:
#   [Unit]
#   Description=SAM Trader Host Monitor
#   After=docker.service
#   Requires=docker.service
#
#   [Service]
#   Type=simple
#   ExecStart=/opt/sam_trader/docker/host-monitor.sh
#   Restart=on-failure
#   RestartSec=10
#
#   [Install]
#   WantedBy=multi-user.target
#
# Linux usage (cron) — add to root crontab:
#   * * * * * /opt/sam_trader/docker/host-monitor.sh --oneshot >> /var/log/sam-monitor.log 2>&1
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------
POLL_INTERVAL_SEC="${SAM_MONITOR_INTERVAL:-60}"
COOLDOWN_WINDOW_SEC="${SAM_MONITOR_COOLDOWN_WINDOW:-900}"   # 15 minutes
COOLDOWN_BACKOFF_SEC="${SAM_MONITOR_COOLDOWN_BACKOFF:-1800}" # 30 minutes
MAX_RESTARTS_IN_WINDOW="${SAM_MONITOR_MAX_RESTARTS:-3}"
STATE_DIR="${SAM_MONITOR_STATE_DIR:-/tmp/sam-monitor}"
LOG_FILE="${SAM_MONITOR_LOG:-$(dirname "$0")/../logs/host-monitor.log}"
CONTAINER_PREFIX="${SAM_MONITOR_PREFIX:-sam-}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S %Z')
    printf '[%s] %s\n' "$ts" "$1" | tee -a "$LOG_FILE"
}

ensure_state_dir() {
    if [[ ! -d "$STATE_DIR" ]]; then
        mkdir -p "$STATE_DIR"
    fi
}

# Return the Docker health status for a container: healthy, unhealthy, starting,
# or "none" if no health check configured.
get_health_status() {
    local container="$1"
    docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true
}

# Return the container's running state: true / false
is_running() {
    local container="$1"
    local status
    status=$(docker inspect --format='{{.State.Running}}' "$container" 2>/dev/null || echo "false")
    echo "$status"
}

# Path to the JSON state file for a container
state_file() {
    local container="$1"
    printf '%s/%s.json\n' "$STATE_DIR" "$container"
}

# Read the state JSON for a container; emit empty object if missing.
read_state() {
    local container="$1"
    local f
    f=$(state_file "$container")
    if [[ -f "$f" ]]; then
        cat "$f"
    else
        echo '{}'
    fi
}

# Write state JSON for a container.
write_state() {
    local container="$1"
    local json="$2"
    local f
    f=$(state_file "$container")
    printf '%s\n' "$json" > "$f"
}

# Count how many restarts occurred within the cooldown window.
count_recent_restarts() {
    local json="$1"
    local now
    now=$(date +%s)
    local cutoff=$(( now - COOLDOWN_WINDOW_SEC ))
    # Extract restart timestamps, filter those within the window, count lines.
    printf '%s\n' "$json" | python3 -c "
import json, sys
obj = json.load(sys.stdin)
restarts = obj.get('restarts', [])
now = $now
cutoff = $cutoff
recent = [r for r in restarts if r >= cutoff]
print(len(recent))
"
}

# Check if the container is currently in a cooldown backoff.
is_in_cooldown() {
    local json="$1"
    local now
    now=$(date +%s)
    printf '%s\n' "$json" | python3 -c "
import json, sys
obj = json.load(sys.stdin)
now = $now
cooldown_until = obj.get('cooldown_until', 0)
if now < cooldown_until:
    print('true')
else:
    print('false')
"
}

# Record a restart event in the container's state.
record_restart() {
    local container="$1"
    local json
    json=$(read_state "$container")
    local now
    now=$(date +%s)
    local updated
    updated=$(printf '%s\n' "$json" | python3 -c "
import json, sys
obj = json.load(sys.stdin)
now = $now
window = $COOLDOWN_WINDOW_SEC
backoff = $COOLDOWN_BACKOFF_SEC
max_restarts = $MAX_RESTARTS_IN_WINDOW

if 'restarts' not in obj:
    obj['restarts'] = []

# Prune old entries outside the window
cutoff = now - window
obj['restarts'] = [r for r in obj['restarts'] if r >= cutoff]

# Add this restart
obj['restarts'].append(now)

# If we've hit the threshold, enter cooldown
if len(obj['restarts']) >= max_restarts:
    obj['cooldown_until'] = now + backoff
    print(json.dumps(obj))
else:
    print(json.dumps(obj))
")
    write_state "$container" "$updated"
}

# Clear cooldown for a container (called when health returns to healthy).
clear_cooldown() {
    local container="$1"
    local json
    json=$(read_state "$container")
    local updated
    updated=$(printf '%s\n' "$json" | python3 -c "
import json, sys
obj = json.load(sys.stdin)
obj.pop('cooldown_until', None)
print(json.dumps(obj))
")
    write_state "$container" "$updated"
}

# Restart a container via docker restart.
restart_container() {
    local container="$1"
    log "RESTARTING container: $container"
    if docker restart "$container" >/dev/null 2>&1; then
        log "RESTART OK: $container"
        record_restart "$container"
    else
        log "RESTART FAILED: $container"
    fi
}

# ---------------------------------------------------------------------------
# Main monitoring loop
# ---------------------------------------------------------------------------
run_once() {
    ensure_state_dir

    # Discover all sam-* containers known to Docker (running or not).
    local containers
    containers=$(docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format '{{.Names}}' | sort || true)

    if [[ -z "$containers" ]]; then
        log "No containers found with prefix '${CONTAINER_PREFIX}'"
        return 0
    fi

    while IFS= read -r container; do
        [[ -n "$container" ]] || continue

        local running
        running=$(is_running "$container")
        if [[ "$running" != "true" ]]; then
            log "WARNING: $container is not running (state=$running)"
            # If not running, we treat it as needing a restart (unless in cooldown).
        fi

        local health
        health=$(get_health_status "$container")

        local json
        json=$(read_state "$container")
        local in_cooldown
        in_cooldown=$(is_in_cooldown "$json")
        local recent_count
        recent_count=$(count_recent_restarts "$json")

        if [[ "$in_cooldown" == "true" ]]; then
            local cooldown_until
            cooldown_until=$(printf '%s\n' "$json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cooldown_until',0))")
            local human_until
            human_until=$(date -r "$cooldown_until" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || date -d "@$cooldown_until" '+%Y-%m-%d %H:%M:%S %Z')
            log "COOLDOWN: $container health=$health (skipping, cooldown until $human_until, recent restarts=$recent_count)"
            continue
        fi

        if [[ "$health" == "healthy" ]]; then
            log "OK: $container health=healthy (recent restarts=$recent_count)"
            # If the container recovers to healthy, clear any stale cooldown.
            if printf '%s\n' "$json" | python3 -c "import json,sys; print('cooldown_until' in json.load(sys.stdin))" | grep -q "True"; then
                clear_cooldown "$container"
                log "CLEARED cooldown state for $container (back to healthy)"
            fi
            continue
        fi

        if [[ "$health" == "starting" ]]; then
            log "WAIT: $container health=starting (recent restarts=$recent_count)"
            continue
        fi

        # Health is unhealthy, none, or container not running.
        log "ALERT: $container health=$health (recent restarts=$recent_count) — attempting restart"
        restart_container "$container"

    done <<< "$containers"
}

run_loop() {
    log "=== SAM Trader Host Monitor started (interval=${POLL_INTERVAL_SEC}s, window=${COOLDOWN_WINDOW_SEC}s, max=${MAX_RESTARTS_IN_WINDOW}, backoff=${COOLDOWN_BACKOFF_SEC}s) ==="
    while true; do
        run_once
        sleep "$POLL_INTERVAL_SEC"
    done
}

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
show_help() {
    cat <<'EOF'
Usage: host-monitor.sh [OPTION]

  --oneshot    Run a single poll cycle and exit (useful for cron / manual test)
  --status     Print current state of all sam-* containers and cooldown info
  --help       Show this help message

Environment variables:
  SAM_MONITOR_INTERVAL        Poll interval in seconds (default: 60)
  SAM_MONITOR_COOLDOWN_WINDOW  Window for restart counting in seconds (default: 900)
  SAM_MONITOR_COOLDOWN_BACKOFF Backoff duration in seconds (default: 1800)
  SAM_MONITOR_MAX_RESTARTS    Max restarts before cooldown (default: 3)
  SAM_MONITOR_STATE_DIR       Directory for restart counter state (default: /tmp/sam-monitor)
  SAM_MONITOR_LOG             Path to log file (default: ../logs/host-monitor.log)
  SAM_MONITOR_PREFIX          Container name prefix to watch (default: sam-)
EOF
}

cmd_status() {
    ensure_state_dir
    local containers
    containers=$(docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format '{{.Names}}' | sort || true)
    if [[ -z "$containers" ]]; then
        echo "No containers found with prefix '${CONTAINER_PREFIX}'"
        return 0
    fi

    printf '%-25s %-10s %-10s %-10s %-25s\n' "CONTAINER" "RUNNING" "HEALTH" "RECENT" "COOLDOWN_UNTIL"
    while IFS= read -r container; do
        [[ -n "$container" ]] || continue
        local running
        running=$(is_running "$container")
        local health
        health=$(get_health_status "$container")
        local json
        json=$(read_state "$container")
        local recent_count
        recent_count=$(count_recent_restarts "$json")
        local cooldown_until
        cooldown_until=$(printf '%s\n' "$json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cooldown_until','-'))")
        local human_until="-"
        if [[ "$cooldown_until" != "-" && "$cooldown_until" != "0" ]]; then
            human_until=$(date -r "$cooldown_until" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "@$cooldown_until" '+%Y-%m-%d %H:%M:%S')
        fi
        printf '%-25s %-10s %-10s %-10s %-25s\n' "$container" "$running" "$health" "$recent_count" "$human_until"
    done <<< "$containers"
}

main() {
    case "${1:-}" in
        --oneshot)
            run_once
            ;;
        --status)
            cmd_status
            ;;
        --help|-h)
            show_help
            ;;
        "")
            run_loop
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
