#!/usr/bin/env bash
# SAM Trader V3 — Ralph Preflight Guardrails
# Filters out meta grouping tickets (epics, features) from Ralph's work queue.
# Sourced automatically by scripts/ralph/ralph_preflight.sh

# Skip meta-grouping tickets (epics, features) — they are containers, not work items
if [[ "${LABELS}" == *"meta-grouping"* ]]; then
    SKIP_REASON="meta_grouping_ticket_skip"
fi

# Belt-and-suspenders: also skip by container type
if [[ "${CAND_TYPE}" == "feature" ]] || [[ "${CAND_TYPE}" == "epic" ]]; then
    SKIP_REASON="container_type_skip"
fi

# --- .env hostname staleness guard (v2→v3 migration) ---
# Warn if hostnames in .env do not match docker-compose service names.
# This is non-blocking; it just prints warnings to stdout.
if [[ -f "${PROJECT_DIR}/scripts/ralph/validate_env_hostnames.sh" ]]; then
    bash "${PROJECT_DIR}/scripts/ralph/validate_env_hostnames.sh" >&2 || true
fi
