#!/usr/bin/env bash
# Ralph Wiggum Pre-Flight Guardrails v1
# Checks if a task with given labels can run in current conditions.
#
# Usage: bash scripts/ralph/ralph_preflight.sh <label1,label2,...>
# Exit codes: 0 = ready, 1 = blocked (reason printed to stdout)
#
# HOW TO CONFIGURE:
#   Edit config/ralph_preflight.sh in your project to define label checks.
#   This file is sourced automatically if it exists.
#
#   Or set RALPH_PREFLIGHT_EXTRA to source an additional script.
#
# Environment variables:
#   RALPH_PREFLIGHT_EXTRA - Path to an extra preflight script to source

set -euo pipefail

LABELS="${1:-}"
CAND_TYPE="${2:-}"
SKIP_REASON=""

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# --- Source project-specific preflight configuration ---
PROJECT_PREFLIGHT="${PROJECT_DIR}/config/ralph_preflight.sh"
if [[ -f "${PROJECT_PREFLIGHT}" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_PREFLIGHT}"
fi

# --- Source extra preflight checks if provided ---
if [[ -n "${RALPH_PREFLIGHT_EXTRA:-}" && -f "${RALPH_PREFLIGHT_EXTRA}" ]]; then
    # shellcheck source=/dev/null
    source "${RALPH_PREFLIGHT_EXTRA}"
fi

# --- Result ---
if [[ -n "${SKIP_REASON}" ]]; then
    echo "BLOCKED: ${SKIP_REASON}"
    exit 1
fi

echo "READY"
exit 0
