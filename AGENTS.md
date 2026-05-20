# AGENTS.md — SAM Trader V3

> **SAM Trader V3** — Production-grade autonomous trading platform on NautilusTrader.
> Architecture and roadmap: `docs/reference/SAM_TRADER_V3_PLAN.md` — read this first.
> Ticket plan and hierarchy: `docs/agent/TICKET_PLAN_V3.md`

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
| Phase 2 | `BUILD_PHASE_2.md` | — (market data patterns in existing code) |
| Phase 3 | `BUILD_PHASE_3.md` | ✅ Complete |
| Phase 4 | `BUILD_PHASE_4.md` | ✅ Complete |
| Phase 5 | `BUILD_PHASE_5.md` | ✅ Complete |
| Phase 6 | `BUILD_PHASE_6.md` | ✅ Complete |
| Phase 7 | `BUILD_PHASE_7.md` | ✅ Complete |
| Phase 8 | `BUILD_PHASE_8.md` | ✅ Complete |
| Phase 9 | `BUILD_PHASE_9.md` | ✅ Complete |
| Phase 10 | `BUILD_PHASE_10.md` | ✅ Complete |
| Phase 11 | `BUILD_PHASE_11.md` | ✅ Complete |

The Ralph loop auto-detects the ticket's phase label and injects the corresponding BUILD_PHASE doc into the agent prompt.

## Key Decisions (see plan §2 for full rationale)

- NautilusTrader v1.227.0 as sole engine. Standard components only. No custom implementations.
- futu-api SDK for Futu protocol (not nautilus-futu Rust adapter).
- Futu OpenD in separate Docker container (official image). IB Gateway in separate container (optional).
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

<!-- BEGIN RALPH LOOP INTEGRATION v:1 -->
## Ralph Wiggum Loop System

This project uses the **Ralph Wiggum Loop System** for agentic development.

### Quick Reference

```bash
# Start the Ralph loop (background)
bash scripts/ralph/run_ralph_loop.sh

# Run a single ticket
bash scripts/ralph/ralph_loop.sh --ticket=<id>

# Run with a specific agent
bash scripts/ralph/ralph_loop.sh --agent=kimi

# Validate current work
bash scripts/ralph/ralph_validate.sh --tier=targeted

# Check loop health
bash scripts/ralph/ralph_health.sh --verbose
```
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
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

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
8. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- Always run `bd dolt push` BEFORE `git push` so beads state syncs
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
