#!/usr/bin/env bash
# Ralph Wiggum Metrics Logger v1
# Appends one JSON line per event to logs/ralph_metrics.jsonl
#
# Usage: bash scripts/ralph/ralph_metrics.sh <event> [key=value ...]
# Events: iteration_start, iteration_end, task_claimed, validation_gate, checkpoint_cleared,
#         checkpoint_retained, task_rolled_back, all_tasks_blocked
#
# Environment variables:
#   RALPH_METRICS_FILE - Override metrics jsonl path (default: logs/ralph_metrics.jsonl)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
METRICS_FILE="${RALPH_METRICS_FILE:-${PROJECT_DIR}/logs/ralph_metrics.jsonl}"

EVENT="${1:-unknown}"
shift || true

# Build JSON object from key=value pairs
JSON_PAIRS=""
for pair in "$@"; do
    key="${pair%%=*}"
    val="${pair#*=}"
    # Escape quotes in value
    val="${val//\"/\\\"}"
    if [[ -n "${JSON_PAIRS}" ]]; then
        JSON_PAIRS="${JSON_PAIRS},"
    fi
    JSON_PAIRS="${JSON_PAIRS}\"${key}\":\"${val}\""
done

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
HOSTNAME=$(hostname)

mkdir -p "$(dirname "${METRICS_FILE}")"
echo "{\"timestamp\":\"${TIMESTAMP}\",\"hostname\":\"${HOSTNAME}\",\"event\":\"${EVENT}\",${JSON_PAIRS}}" >> "${METRICS_FILE}"
