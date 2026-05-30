INFO: THIS IS A SANDBOX ENVIRONMENT
INFO: ALL TRANSACTIONS WILL BE PAPER/SIMULATE ONLY
RULE: WHEN YOU HOT_FIX OR CHANGE SOMETHING, NEVER COMMIT TO REMOTE, UNLESS SPECIFICALLY REQUESTED BY USER
RULE: IF YOU ARE UNSURE, ALWAYS ASK THE HUMAN (Sam Dharma), WHERE THERE ARE GAPS OR THINGS ARE UNCLEAR

# AGENTS.md — SAM Trader V3

> **SAM Trader V3** — Production-grade autonomous trading platform on NautilusTrader.
> Architecture & roadmap: `docs/reference/SAM_TRADER_V3_PLAN.md` (read first).  
> Ticket hierarchy: `docs/agent/TICKET_PLAN_V3.md`

## Project Overview

Dockerized trading system: **postgres** (journal) + **redis** (state cache) + **sam-trader** (nautilus engine) + **sam-futu-opend** (futu broker) + **sam-ib-gateway** (ibkr broker, optional) + **sam-services** (operations).
Single-script deployable on macOS via `./deploy.sh --with-futu`.

## Build & Test

```bash
# Run validation (tests + lint + type-check on changed files)
bash scripts/ralph/ralph_validate.sh --tier=targeted

# Run unit tests only
pytest tests/unit/ -q --tb=short

# Check git status
git status --short
```

## Build Phase Reference Docs

For each build phase, a `docs/reference/BUILD_PHASE_<N>.md` doc exists containing pre-discovered Nautilus types, Futu SDK mappings, and implementation patterns.

**Before starting work on any phase ticket, read the corresponding BUILD_PHASE doc first.**
This eliminates redundant research and saves ~20-30 steps per iteration.

| Phase | Doc | Status |
|-------|-----|--------|
| Phase 0 | `BUILD_PHASE_0.md` | ✅ Complete (+DM extension: always-on brokers) |
| Phase 1 | `BUILD_PHASE_1.md` | ✅ Complete (+DM extension: MarketConfig, MARKET env var) |
| Phase 2 | `BUILD_PHASE_2.md` | ✅ Complete (+DM extension: multi-market context verify) |
| Phase 3 | `BUILD_PHASE_3.md` | ✅ Complete |
| Phase 4 | `BUILD_PHASE_4.md` | ✅ Complete |
| Phase 5 | `BUILD_PHASE_5.md` | ✅ Complete (+DM extension: IB conditional enable) |
| Phase 6 | `BUILD_PHASE_6.md` | ✅ Complete (+DM extension: 3 new actors, tz refactor) |
| Phase 7 | `BUILD_PHASE_7.md` | ✅ Complete (+DM extension: market field, Controller, lunch pause) |
| Phase 8 | `BUILD_PHASE_8.md` | ✅ Complete (+DM extension: orchestrator, SOD/EOD CLI, cron) |
| Phase 9 | `BUILD_PHASE_9.md` | ✅ Complete (+DM extension: dual scanner, market pipeline) |
| Phase 10 | `BUILD_PHASE_10.md` | ✅ Complete |
| Phase 11 | `BUILD_PHASE_11.md` | ✅ Complete (+DM extension: deploy update, daily cycle E2E) |

The Ralph loop auto-detects the ticket's phase label and injects the corresponding BUILD_PHASE doc into the agent prompt.

## Key Decisions (see plan §2 for full rationale)

- NautilusTrader v1.227.0 as sole engine. Standard components only — no non-standard patterns for `DataEngine`, `LiveExecEngine`, etc.
- futu-api SDK for Futu protocol (not nautilus-futu Rust adapter).
- Futu OpenD in separate Docker container (official image). 
- IB Gateway in separate container.
- Parquet for historical data. PostgreSQL for relational data only. Redis for Nautilus cache persistence.
- YAML bundle registry for strategies. ImportableStrategyConfig pattern. Multi-venue from day 1.
- Graceful restart for all config changes (maintenance window 5am–8am HKT).
- sam-services container decoupled from sam-trader for independent ops lifecycle.

## Conventions

- Python 3.12+. Type hints on all public APIs. Ruff + Black.
- Configuration via env vars + frozen dataclasses.
- Secrets in `.env` only (never committed).
- Docker: one process per container.
- Package name: `sam_trader`. Docker prefix: `sam-`. Network: `sam-net`.

## Health Check Pattern

All containers use a standardized **3-layer health check** (see `docker/HEALTHCHECK_PATTERN.md`):

| Layer | Purpose | Example |
|-------|---------|---------|
| L1 | Process alive | `pgrep <process>` |
| L2 | Socket / service responding | `pg_isready`, `redis-cli ping`, TCP connect |
| L3 | Protocol / application healthy | `SELECT 1`, `INFO server`, HTTP GET `/health` |

**Timing parameters (all containers):** interval `30s`, timeout `10s`, start-period `60s`, retries `3`.

## Beads Ticket Hierarchy & Rules

### Hierarchy
```
EPIC (type: epic, labels: epic, meta-grouping, <phase-tag>) - not to be closed
└── FEATURE (type: feature, labels: <phase-tag>, meta-grouping) - not to be closed
    ├── WORK tickets (type: task|bug|test|docs, labels: <phase-tag> ONLY). Each ticket should be atomic and small. Number sequence according to build sequence (ticket N depends on ticket N-1 or has no deps) — to be closed after implementation
    │   - Use `bug` for regressions/defects; `task` for new work
    │   - Must have description with acceptance criteria
    │   - Must reference spec file or build phase doc
    │   - Dependencies set explicitly via `bd dep add`
    └── EXIT ticket (type: task, labels: exit, <phase-tag>)
        - Last ticket before feature is complete
        - Regression test and wired (e2e) for the whole feature
        - Blocks the NEXT phase's first work ticket(s)
```

### Label Rules
| Ticket Type | Allowed Labels |
|-------------|----------------|
| EPIC | `epic`, `meta-grouping`, optional `<phase-tag>` |
| FEATURE | `<phase-tag>`, `meta-grouping` |
| WORK (task, bug, test, docs) | `<phase-tag>` **only** |
| EXIT | `exit`, `<phase-tag>` |

### Dependency Rules
1. **FEATURE parents are pure containers** — they MUST NOT carry blocking dependencies (including on previous phase EXIT tickets).
2. **Phase gating** — a phase's first work ticket(s) depend on the previous phase's EXIT ticket ONLY. No cross-phase skip links (e.g., a phase-11 ticket must NOT directly depend on a phase-7 ticket).
3. **EXIT tickets** depend only on their own phase's work tickets.
4. **Work tickets** MUST have exactly one label (`<phase-tag>`). EXIT tickets are the exception: `exit, <phase-tag>`.
5. **Phase labels** use `phase-<N>`. Non-numeric suffixes break Ralph's build-doc lookup.
6. **No redundant transitive dependencies** — if A depends on B and B depends on C, A must NOT also directly depend on C. Keep the graph flat within each phase.
7. **Ralph deterministic selection** — the Ralph loop skips `epic` and `feature` tickets, then sorts ready work tickets by feature number ascending, then task number ascending. Lower numbers are always built first.

<!-- BEGIN RALPH LOOP INTEGRATION v:1 -->
## Ralph Wiggum Loop System

This project uses the **Ralph Wiggum Loop System** for agentic development.

### Quick Reference

```bash
# Background daemon wrapper
bash scripts/ralph/run_ralph_loop.sh

# Direct harness (single-shot)
bash scripts/ralph/ralph_loop.sh --ticket=<id> --agent=kimi

# Validate current work
bash scripts/ralph/ralph_validate.sh --tier=targeted

# Check loop health
bash scripts/ralph/ralph_health.sh --verbose
```

**Preflight guardrails:** `scripts/ralph/ralph_preflight.sh` gates ticket selection (sourcing project-specific overrides from `config/ralph_preflight.sh`). It must output exactly `READY` to stdout; anything else skips the ticket.
<!-- END RALPH LOOP INTEGRATION -->

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for command reference
- Use `bd remember <key>` for persistent notes (e.g., `bd remember ralph_checkpoint`)
- Do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Commit all changes** - Stage and commit everything:
   ```bash
   git add -A
   git commit -m "descriptive message summarizing the work"
   ```
4. **Update issue status** - Close finished work, update in-progress items
5. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push                # sync beads state to git remote
   git push
   git status                  # MUST show "up to date with origin"
   ```
6. **Clean up** - Clear stashes, prune remote branches
7. **Verify** - All changes committed AND pushed, `git status` clean
8. **Hand off** - Add context to the ticket or `bd remember` for the next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- Always run `bd dolt push` BEFORE `git push` so beads state syncs
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
