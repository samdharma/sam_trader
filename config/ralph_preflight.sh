#!/usr/bin/env bash
# SAM Trader V3 — Ralph Preflight Guardrails
# Filters out meta grouping tickets (epics, features) from Ralph's work queue.
# Sourced automatically by scripts/ralph/ralph_preflight.sh

# Skip meta-grouping tickets (epics, features) — they are containers, not work items
if [[ "${LABELS}" == *"meta-grouping"* ]]; then
    SKIP_REASON="meta_grouping_ticket_skip"
fi
