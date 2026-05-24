# Beads Ticket Audit & Cleanup Plan

> **Date:** 2026-05-23  
> **Status:** ✅ COMPLETE  
> **Scope:** All open tickets (53 open after cleanup)

---

## 1. Executive Summary

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Cross-phase skip links | 4 | **0** | 0 |
| Redundant transitive deps | 48 | **0** | 0 |
| Missing phase gates | 3 | **0** | 0 |
| Umbrella tickets causing depth | 2 (10.3, 10.5) | **0** | 0 |
| Naming/label violations | 1 (12.4 `[GATE]`) | **0** | 0 |
| Max depth (open tickets) | 35 | **~19** | Minimal |

**Root cause of original excessive depth:** Cross-phase skip links (e.g., P11 → P7), redundant transitive dependencies, and umbrella tickets (10.3, 10.5) that overlapped with their own sub-tickets.

**What remains:** Natural sequential chains within phases (e.g., 10.7 → 10.8 → 10.9 for risk components, 10.10 → 10.11 → 10.12 for pipeline components). These are logically required and match the decomposition specs in `BUILD_PHASE_9.md`.

---

## 2. Changes Applied

### 2.1 Naming & Label Fixes

| Ticket | Fix |
|--------|-----|
| `sam_trader-9z3.12.4` | Title: `[GATE]` → `[EXIT]`; Labels: `phase-11` → `exit,phase-11` |

### 2.2 Missing Phase Gates Added

| Ticket | Added Dependency |
|--------|------------------|
| `sam_trader-9z3.6.2` | `sam_trader-9z3.5.6` (P4 EXIT) |
| `sam_trader-9z3.7.7` | `sam_trader-9z3.6.4` (P5 EXIT) |
| `sam_trader-9z3.10.4` (renumbered → 9z3.10.19) | `sam_trader-9z3.9.6` (P8 EXIT) |

### 2.3 Cross-Phase Skip Links Removed

| Ticket | Removed Illegal Dependency |
|--------|---------------------------|
| `sam_trader-9z3.9.1` | `sam_trader-9z3.5.4` (P4) |
| `sam_trader-9z3.12.1` | `sam_trader-9z3.8.6` (P7) |
| `sam_trader-9z3.12.2` | `sam_trader-9z3.8.6` (P7) |
| `sam_trader-9z3.12.3` | `sam_trader-9z3.8.6` (P7) |

### 2.4 Redundant Transitive Dependencies Removed

#### Phase 5
- `6.3`: removed `6.1`, `6.2`, `6.5` (covered via `6.3 → 6.6 → 6.5 → 6.2`)
- `6.4` (EXIT): removed `6.2`, `6.3`, `6.5`, `6.6` (covered via `6.4 → 6.7 → 6.3 → 6.6`)
- `6.5`: removed `6.1` (closed ticket)

#### Phase 6
- `7.6` (EXIT): removed `7.1`, `7.2` (covered via `7.8 → 7.2 → 7.1`)

#### Phase 7
- `8.2`: removed `8.1` (covered via `8.2 → 8.4 → 8.1`)
- `8.3`: removed `8.1` (covered via `8.3 → 8.4 → 8.1`)
- `8.6` (EXIT): removed `8.1`, `8.4` (covered via `8.2/8.3 → 8.4 → 8.1`)

#### Phase 8
- `9.3`: removed `9.1` (covered via `9.3 → 9.2 → 9.1`)
- `9.5`: removed `9.1`, `9.2` (covered via `9.5 → 9.3 → 9.2 → 9.1`)
- `9.6` (EXIT): removed `9.1`, `9.2`, `9.3` (covered via `9.5 → 9.3` and `9.4 → 9.1`)

#### Phase 9
- Closed `10.3` and `10.5` as superseded by sub-tickets `10.7-10.9` and `10.10-10.12`
- `10.5`: removed `10.1`, `10.2`, `10.3`
- `10.10`: removed `10.5` (was backwards); kept `10.4`, `10.9`
- `10.11`: removed `10.5`
- `10.12`: removed `10.5`
- `10.6` (EXIT): removed `10.1`, `10.2`, `10.3`, `10.4`, `10.5`, `10.10`, `10.11` (covered via `10.6 → 10.12 → 10.11 → 10.10`)

#### Phase 10
- `11.5` (EXIT): removed `11.1`, `11.2`, `11.3` (covered via `11.5 → 11.4 → 11.3 → 11.1/11.2`)

#### Phase 11
- `12.2`: removed `11.5` (covered via `12.2 → 12.1 → 11.5`)
- `12.3`: removed `11.5`, `12.1` (covered via `12.3 → 12.2 → 12.1 → 11.5`)
- `12.4` (EXIT): removed `12.1`, `12.2` (covered via `12.4 → 12.3 → 12.2 → 12.1`)

---

## 3. Target Dependency Structure (Post-Cleanup)

### Phase 5: IBKR Adapter
```
5.6 (P4 EXIT) → 6.2 → 6.5 → 6.6 → 6.3 → 6.7 → 6.4 (EXIT)
```

### Phase 6: Actors & State
```
6.4 (P5 EXIT) → 7.1 ─┬→ 7.2 → 7.8
                     ├→ 7.3
                     ├→ 7.4
                     ├→ 7.5
                     └→ 7.7
                     → 7.6 (EXIT) depends on 7.3, 7.4, 7.5, 7.7, 7.8
```

### Phase 7: Strategy Library
```
7.6 (P6 EXIT) → 8.1 ─┬→ 8.4 → 8.2
                     │      → 8.3
                     └→ 8.5
                     → 8.6 (EXIT) depends on 8.2, 8.3, 8.5
```

### Phase 8: sam-services
```
8.6 (P7 EXIT) → 9.1 ─┬→ 9.2 → 9.3 → 9.5
                     └→ 9.4
                     → 9.6 (EXIT) depends on 9.4, 9.5
```

### Phase 9: Pre-Market Pipeline
```
9.6 (P8 EXIT) → 10.1 → 10.2 → 10.7 → 10.8 → 10.9 ─┐
                                                    ├──→ 10.10 → 10.11 → 10.12 → 10.6 (EXIT)
9.6 (P8 EXIT) → 10.4 ────────────────────────────────┘
```

### Phase 10: Safety & Dashboard
```
10.6 (P9 EXIT) → 11.1 ─┐
                       ├──→ 11.3 → 11.4 → 11.5 (EXIT)
10.6 (P9 EXIT) → 11.2 ─┘
```

### Phase 11: Deploy & E2E
```
11.5 (P10 EXIT) → 12.1 → 12.2 → 12.3 → 12.4 (EXIT)
```

---

## 4. Verified Checks

- [x] `bd dep cycles` — no cycles
- [x] `bd ready --json` — shows correctly gated tickets
- [x] All EXIT tickets have `exit` label
- [x] No work tickets have extra labels
- [x] No cross-phase skip links remain
- [x] No redundant transitive dependencies remain
- [x] Closed `10.3` and `10.5` have zero open dependents

---

## 5. Docs Updated

- `docs/reference/update-beads-tickets.md` (this file)
- `docs/agent/TICKET_PLAN_V3.md` — Phase 9 visual tree updated
- `docs/reference/BUILD_PHASE_9.md` — decomposition notes updated

---

*End of audit. Dependency graph is now clean and follows AGENTS.md rules.*
