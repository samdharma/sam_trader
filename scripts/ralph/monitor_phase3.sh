#!/usr/bin/env bash
# Phase 3 Monitor — watches Ralph loop progress and validates outcomes.
# Usage: nohup bash scripts/ralph/monitor_phase3.sh &>/dev/null &
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

LOGFILE="${PROJECT_DIR}/logs/ralph_loop.log"
REPORT="${PROJECT_DIR}/docs/reference/PHASE_3_REPORT.md"
MONITOR_LOG="${PROJECT_DIR}/logs/monitor_phase3.log"
POLL_INTERVAL=180
STALL_THRESHOLD=60
TICKETS=(sam_trader-9z3.4.1 sam_trader-9z3.4.2 sam_trader-9z3.4.3)

mkdir -p "${PROJECT_DIR}/logs" "${PROJECT_DIR}/docs/reference"

log_msg() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${MONITOR_LOG}"; }

get_status() {
    bd show "$1" --json 2>/dev/null | python3 -c "import sys,json;d=json.loads(sys.stdin.read());print([x['status'] for x in d if 'id' in x][0])" 2>/dev/null || echo "unknown"
}

get_title() {
    bd show "$1" --json 2>/dev/null | python3 -c "import sys,json;d=json.loads(sys.stdin.read());print([x.get('title','')[:50] for x in d if 'id' in x][0])" 2>/dev/null || echo "?"
}

check_tickets() {
    log_msg "--- Tickets ---"
    for tid in "${TICKETS[@]}"; do
        local s t
        s=$(get_status "$tid")
        t=$(get_title "$tid")
        log_msg "  ${tid}  ${s}  ${t}"
    done
}

all_closed() {
    for tid in "${TICKETS[@]}"; do
        [[ "$(get_status "$tid")" != "closed" ]] && return 1
    done
    return 0
}

investigate_stall() {
    log_msg "!!! STALL — no log growth for ${STALL_THRESHOLD}s"

    if grep -q "Max number of steps reached" "${LOGFILE}" 2>/dev/null; then
        log_msg "  AGENT HIT STEP LIMIT"
    fi
    if grep -q "RALPH_GATE_FAILED" "${LOGFILE}" 2>/dev/null; then
        log_msg "  GATE FAILURE (last at line $(grep -n RALPH_GATE_FAILED "${LOGFILE}" | tail -1 | cut -d: -f1))"
    fi
    if grep -q "RALPH_GATE_PASSED" "${LOGFILE}" 2>/dev/null; then
        log_msg "  Last gate PASS at line $(grep -n RALPH_GATE_PASSED "${LOGFILE}" | tail -1 | cut -d: -f1)"
    fi
    pgrep -f "ralph_loop.sh" >/dev/null 2>&1 && log_msg "  Ralph loop: RUNNING" || log_msg "  Ralph loop: NOT FOUND"

    log_msg "  Tail: $(tail -1 "${LOGFILE}" 2>/dev/null | cut -c1-120)"
}

validate_outcomes() {
    log_msg "=== Validating outcomes ==="
    local out=""
    out+="\nModule Inventory:\n"
    for f in parsing/orders.py execution.py; do
        local fp="src/sam_trader/adapters/futu/${f}"
        if [[ -f "$fp" ]]; then
            out+="  ✅ ${f} ($(wc -l < "$fp") lines)\n"
        else
            out+="  ❌ ${f} MISSING\n"
        fi
    done

    out+="\nKey Checks:\n"
    if [[ -f src/sam_trader/adapters/futu/execution.py ]]; then
        grep -q "class FutuLiveExecutionClient" src/sam_trader/adapters/futu/execution.py 2>/dev/null && out+="  ✅ FutuLiveExecutionClient subclass\n" || out+="  ❌ Missing FutuLiveExecutionClient\n"
        grep -qE "submit_order|modify_order|cancel_order" src/sam_trader/adapters/futu/execution.py 2>/dev/null && out+="  ✅ submit/modify/cancel\n" || out+="  ❌ No order methods\n"
        grep -qE "OrderFilled|on_fill|fill_report" src/sam_trader/adapters/futu/execution.py 2>/dev/null && out+="  ✅ Fill handling\n" || out+="  ❌ No fill handling\n"
        grep -qE "AccountState|account" src/sam_trader/adapters/futu/execution.py 2>/dev/null && out+="  ✅ Account discovery\n" || out+="  ❌ No account discovery\n"
    fi
    if [[ -f src/sam_trader/adapters/futu/parsing/orders.py ]]; then
        grep -qE "def parse.*order|def parse.*fill|def parse.*position" src/sam_trader/adapters/futu/parsing/orders.py 2>/dev/null && out+="  ✅ Parsing functions\n" || out+="  ❌ No parsing functions\n"
    fi

    out+="\nTests:\n"
    local test_out
    test_out=$(cd "${PROJECT_DIR}" && .venv/bin/pytest \
        tests/unit/adapters/futu/test_parsing_orders.py \
        tests/unit/adapters/futu/test_execution.py \
        tests/integration/adapters/futu/test_execution_flow.py \
        -q --tb=line 2>&1 | tail -6) || true
    out+="${test_out}\n"

    echo -e "$out" | tee -a "${MONITOR_LOG}"
}

write_report() {
    log_msg "=== Writing report ==="
    local now s1 s2 s3
    now=$(date '+%Y-%m-%d %H:%M:%S')
    s1=$(get_status sam_trader-9z3.4.1)
    s2=$(get_status sam_trader-9z3.4.2)
    s3=$(get_status sam_trader-9z3.4.3)

    local status_str="IN PROGRESS"
    [[ "$s1" == "closed" && "$s2" == "closed" && "$s3" == "closed" ]] && status_str="COMPLETE"

    local mods=""
    for f in parsing/orders.py execution.py; do
        local fp="src/sam_trader/adapters/futu/${f}"
        [[ -f "$fp" ]] && mods+="| \`adapters/futu/${f}\` | $(wc -l < "$fp") lines |\n"
    done

    local tests
    tests=$(cd "${PROJECT_DIR}" && .venv/bin/pytest \
        tests/unit/adapters/futu/test_parsing_orders.py \
        tests/unit/adapters/futu/test_execution.py \
        tests/integration/adapters/futu/test_execution_flow.py \
        -q --tb=short 2>&1 | tail -10) || true

    local commits
    commits=$(git log --oneline --grep="phase-3\|P3\|sam_trader-9z3.4\|order.pars\|execution" -10 2>/dev/null || echo "(none)")

    cat > "${REPORT}" << EOF
# Phase 3: Futu Execution Adapter — Build Report

> **Date**: ${now}
> **Status**: ${status_str}
> **Epic**: sam_trader-9z3 — SAM Trader V3

## Summary

Phase 3 implements the Futu execution adapter, enabling order submission,
modification, cancellation, fill handling, and account auto-discovery through
the Futu OpenD SDK, integrated into the NautilusTrader live execution framework.

## Children

| Ticket | Title | Status |
|---|---|---|
| sam_trader-9z3.4.1 | P3: Futu order parsing | ${s1} |
| sam_trader-9z3.4.2 | P3: FutuLiveExecutionClient | ${s2} |
| sam_trader-9z3.4.3 | [EXIT] P3: Order submission → fill flow | ${s3} |

## Module Inventory

${mods}

## Test Results

\`\`\`
${tests}
\`\`\`

## Goal

> FutuLiveExecutionClient submits/modifies/cancels orders. OrderFilled events
> flow to message bus. Account auto-discovery. Order/fill push handling.

## Commits

\`\`\`
${commits}
\`\`\`
EOF

    log_msg "Report: ${REPORT}"
}

# --- Main ---
log_msg "========================================="
log_msg "Phase 3 Monitor started (PID $$)"
log_msg "Poll: ${POLL_INTERVAL}s  Stall threshold: ${STALL_THRESHOLD}s"
log_msg "========================================="

prev_size=$(wc -c < "${LOGFILE}" 2>/dev/null || echo 0)
stall_start=0

while true; do
    if all_closed; then
        log_msg ""
        log_msg "=== ALL PHASE-3 TICKETS CLOSED ==="
        validate_outcomes
        write_report
        bd close sam_trader-9z3.4 2>/dev/null && log_msg "Closed sam_trader-9z3.4" || log_msg "sam_trader-9z3.4 status unchanged"
        log_msg "Monitor exiting. Report: ${REPORT}"
        exit 0
    fi

    check_tickets

    cur_size=$(wc -c < "${LOGFILE}" 2>/dev/null || echo 0)

    if [[ "$cur_size" -gt "$prev_size" ]]; then
        local diff=$((cur_size - prev_size))
        log_msg "Log +${diff}B — agent working. Next check: ${POLL_INTERVAL}s"
        prev_size=$cur_size
        stall_start=0
        sleep "${POLL_INTERVAL}"
        continue
    fi

    if [[ "$stall_start" -eq 0 ]]; then
        stall_start=$(date +%s)
        log_msg "Log static. Monitoring for stall..."
        sleep "${POLL_INTERVAL}"
        continue
    fi

    local elapsed=$(($(date +%s) - stall_start))
    if [[ "$elapsed" -ge "$STALL_THRESHOLD" ]]; then
        investigate_stall
        stall_start=0
        prev_size=$cur_size
    else
        log_msg "Log static ${elapsed}s (threshold: ${STALL_THRESHOLD}s)"
    fi

    sleep "${POLL_INTERVAL}"
done
