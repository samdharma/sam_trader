#!/usr/bin/env bash
# Ralph Wiggum Loop Health Checker v1
# Checks the health of the Ralph agentic loop and its supporting infrastructure.
#
# Usage:
#   bash scripts/ralph/ralph_health.sh [--verbose]
#
# Checks:
#   1. Age of last metrics entry
#   2. Stale checkpoint file age
#   3. Beads DB integrity
#   4. Git worktree divergence from remote
#   5. Uncommitted beads sync
#
# Environment variables:
#   RALPH_METRICS_FILE   - Path to metrics jsonl (default: logs/ralph_metrics.jsonl)
#   RALPH_CHECKPOINT     - Path to checkpoint file (default: .ralph_checkpoint.json)
#   RALPH_MAX_METRICS_AGE_SEC  - Max age of last metrics entry (default: 7200 = 2h)
#   RALPH_MAX_CHECKPOINT_AGE_SEC - Max age of checkpoint (default: 1800 = 30m)
#
# Exit codes:
#   0 = healthy
#   1 = unhealthy (reason printed to stdout)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

VERBOSE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose)
            VERBOSE=1
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Thresholds (seconds)
METRICS_MAX_AGE_SEC="${RALPH_MAX_METRICS_AGE_SEC:-7200}"
CHECKPOINT_MAX_AGE_SEC="${RALPH_MAX_CHECKPOINT_AGE_SEC:-1800}"

METRICS_FILE="${RALPH_METRICS_FILE:-${PROJECT_DIR}/logs/ralph_metrics.jsonl}"
CHECKPOINT_FILE="${RALPH_CHECKPOINT:-${PROJECT_DIR}/.ralph_checkpoint.json}"
BEADS_DOLT_DIR="${PROJECT_DIR}/.beads/embeddeddolt/$(basename "${PROJECT_DIR}")"

FAILED=0
REASONS=()

log_check() {
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "[CHECK] $1"
    fi
}

fail() {
    REASONS+=("$1")
    FAILED=1
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "[FAIL]  $1"
    fi
}

pass() {
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "[PASS]  $1"
    fi
}

# ---------------------------------------------------------------------------
# 1. Age of last metrics entry
# ---------------------------------------------------------------------------
log_check "Metrics file age"
if [[ -f "${METRICS_FILE}" ]]; then
    LAST_LINE=$(tail -1 "${METRICS_FILE}" 2>/dev/null || true)
    if [[ -n "${LAST_LINE}" ]]; then
        LAST_TS=$(echo "${LAST_LINE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['timestamp'])" 2>/dev/null || true)
        if [[ -n "${LAST_TS}" ]]; then
            LAST_EPOCH=$(python3 -c "import datetime; print(int(datetime.datetime.fromisoformat('${LAST_TS}'.replace('Z', '+00:00')).timestamp()))" 2>/dev/null || true)
            NOW_EPOCH=$(python3 -c "import datetime; print(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))" 2>/dev/null || true)
            AGE_SEC=$((NOW_EPOCH - LAST_EPOCH))
            if [[ ${AGE_SEC} -gt ${METRICS_MAX_AGE_SEC} ]]; then
                fail "ralph_metrics.jsonl last entry is ${AGE_SEC}s old (max ${METRICS_MAX_AGE_SEC}s)"
            else
                pass "ralph_metrics.jsonl last entry is ${AGE_SEC}s old"
            fi
        else
            fail "ralph_metrics.jsonl last line has no parseable timestamp"
        fi
    else
        fail "ralph_metrics.jsonl is empty"
    fi
else
    fail "ralph_metrics.jsonl not found"
fi

# ---------------------------------------------------------------------------
# 2. Stale checkpoint file age
# ---------------------------------------------------------------------------
log_check "Checkpoint file age"
if [[ -f "${CHECKPOINT_FILE}" ]]; then
    MTIME=$(python3 -c "import os,sys; print(int(os.path.getmtime('${CHECKPOINT_FILE}')))" 2>/dev/null || true)
    if [[ -n "${MTIME}" ]]; then
        NOW_EPOCH=$(python3 -c "import datetime; print(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))" 2>/dev/null || true)
        AGE_SEC=$((NOW_EPOCH - MTIME))
        if [[ ${AGE_SEC} -gt ${CHECKPOINT_MAX_AGE_SEC} ]]; then
            fail "Checkpoint file is ${AGE_SEC}s old (max ${CHECKPOINT_MAX_AGE_SEC}s) — iteration may have crashed"
        else
            pass "Checkpoint file is ${AGE_SEC}s old (active iteration)"
        fi
    else
        fail "Cannot determine checkpoint file age"
    fi
else
    pass "No checkpoint file (loop idle)"
fi

# ---------------------------------------------------------------------------
# 3. Beads DB integrity
# ---------------------------------------------------------------------------
log_check "Beads DB integrity"
if [[ -d "${BEADS_DOLT_DIR}" ]]; then
    if (cd "${BEADS_DOLT_DIR}" && dolt status &>/dev/null); then
        pass "Beads DB responds to dolt status"
    else
        fail "Beads DB integrity check failed (dolt status errored)"
    fi
else
    # Try generic embeddeddolt path
    if [[ -d "${PROJECT_DIR}/.beads/embeddeddolt" ]]; then
        pass "Beads embeddeddolt directory exists"
    else
        fail "Beads DB directory not found"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Git worktree divergence from remote
# ---------------------------------------------------------------------------
log_check "Git worktree divergence"
if git rev-parse --git-dir &>/dev/null; then
    # Uncommitted changes
    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        fail "Git worktree has uncommitted changes"
    else
        pass "Git worktree is clean"
    fi

    # Divergence from remote (only if a remote exists)
    if git remote &>/dev/null && [[ -n "$(git remote)" ]]; then
        git fetch --dry-run 2>/dev/null || true
        LOCAL=$(git rev-parse @ 2>/dev/null || true)
        REMOTE=$(git rev-parse '@{u}' 2>/dev/null || true)
        if [[ -n "${LOCAL}" && -n "${REMOTE}" && "${LOCAL}" != "${REMOTE}" ]]; then
            BASE=$(git merge-base @ '@{u}' 2>/dev/null || true)
            if [[ "${LOCAL}" == "${BASE}" ]]; then
                fail "Git branch is behind remote"
            elif [[ "${REMOTE}" == "${BASE}" ]]; then
                fail "Git branch is ahead of remote (unpushed commits)"
            else
                fail "Git branch has diverged from remote"
            fi
        else
            pass "Git branch is in sync with remote (or no tracking branch)"
        fi
    else
        pass "No git remote configured"
    fi
else
    fail "Not a git repository"
fi

# ---------------------------------------------------------------------------
# 5. Uncommitted beads sync
# ---------------------------------------------------------------------------
log_check "Beads sync status"
if [[ -d "${BEADS_DOLT_DIR}" ]]; then
    DOLT_STATUS=$(cd "${BEADS_DOLT_DIR}" && dolt status 2>/dev/null || true)
    if echo "${DOLT_STATUS}" | grep -q "nothing to commit, working tree clean"; then
        pass "Beads DB has no uncommitted changes"
    else
        fail "Beads DB has uncommitted changes — run 'bd dolt push'"
    fi
else
    pass "Beads DB directory not checked (embeddeddolt not found)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ ${FAILED} -eq 0 ]]; then
    echo "RALPH_LOOP_HEALTHY"
    exit 0
else
    echo "RALPH_LOOP_UNHEALTHY"
    for r in "${REASONS[@]}"; do
        echo "  - ${r}"
    done
    exit 1
fi
