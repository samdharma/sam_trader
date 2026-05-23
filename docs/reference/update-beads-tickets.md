# Beads Ticket Audit & Cleanup Plan

> **Date:** 2026-05-22  
> **Scope:** All 108 tickets (53 closed, 54 open, 1 in-progress)  
> **Purpose:** Align open ticket structure with updated AGENTS.md rules, eliminate redundant dependencies, and flatten dependency depth.

---

## 1. Executive Summary

| Metric | Current Value | Target |
|--------|---------------|--------|
| Max dependency depth (open tickets) | **38 levels** | ≤ 12 levels (one per phase) |
| Total dependency edges | 150 | ~100 (after removing redundant transitive links) |
| Redundant dependencies | **48** | 0 |
| Feature parents with blocking deps | 4 (all closed) | 0 |
| Work tickets with extra labels | 25 (mostly closed) | 0 |
| Open work tickets with extra labels | 0 | 0 |

**Root cause of excessive depth:** Cross-phase skip links (e.g., P11 tickets directly depending on P7 exit tickets) and redundant transitive dependencies within phases.

---

## 2. AGENTS.md Fixes Applied

The following AGENTS.md inconsistencies were found and fixed in this session:

| # | Issue | Location | Fix |
|---|-------|----------|-----|
| 1 | Typo: "should me atomic" | Hierarchy > WORK tickets | Changed to "should be atomic" |
| 2 | EXIT label missing `exit` | Hierarchy > EXIT ticket | Added `exit` label: `labels: exit, <phase-tag>` |
| 3 | Missing "no skip links" rule | Dependency Rules | Added Rule 6: "No cross-phase skip links" |
| 4 | Missing "no redundant transitive deps" rule | Dependency Rules | Added Rule 7: "Keep graph flat within each phase" |
| 5 | Missing Ralph deterministic selection rule | Dependency Rules | Added Rule 8: "Ralph sorts by feature num, then task num" |
| 6 | Preflight path misleading | Ralph Loop section | Clarified `scripts/ralph/ralph_preflight.sh` is the executable; `config/ralph_preflight.sh` is the override |
| 7 | WORK ticket label rule ambiguous | Dependency Rules #4 | Clarified EXIT tickets are the exception to "exactly one label" |

---

## 3. Open Ticket Violations

### 3.1 Cross-Phase Skip Links (CRITICAL)

These open tickets directly depend on tickets from non-adjacent phases, creating the 38-level depth.

| Ticket | Phase | Illegal Dependency | Should Be |
|--------|-------|-------------------|-----------|
| `sam_trader-9z3.9.1` | P8 | `sam_trader-9z3.5.4` (P4) | Remove — already gated by `9z3.8.6` (P7 exit) |
| `sam_trader-9z3.12.1` | P11 | `sam_trader-9z3.8.6` (P7) | Remove — already gated by `9z3.11.5` (P10 exit) |
| `sam_trader-9z3.12.2` | P11 | `sam_trader-9z3.8.6` (P7) | Remove — transitively covered |
| `sam_trader-9z3.12.3` | P11 | `sam_trader-9z3.8.6` (P7) | Remove — transitively covered |

**Commands to fix:**
```bash
bd dep remove sam_trader-9z3.9.1 sam_trader-9z3.5.4
bd dep remove sam_trader-9z3.12.1 sam_trader-9z3.8.6
bd dep remove sam_trader-9z3.12.2 sam_trader-9z3.8.6
bd dep remove sam_trader-9z3.12.3 sam_trader-9z3.8.6
```

### 3.2 Redundant Transitive Dependencies within Phases

These open tickets depend on other tickets that are already transitively covered.

#### Phase 11
| Ticket | Redundant Dep | Covered Via |
|--------|---------------|-------------|
| `9z3.12.2` | `9z3.11.5` | `9z3.12.1` |
| `9z3.12.3` | `9z3.11.5` | `9z3.12.1` |
| `9z3.12.3` | `9z3.12.1` | `9z3.12.2` |
| `9z3.12.4` | `9z3.12.1` | `9z3.12.3` |
| `9z3.12.4` | `9z3.12.2` | `9z3.12.3` |

**Recommendation for P11:**
- `9z3.12.1` should depend ONLY on `9z3.11.5` (phase gate).
- `9z3.12.2` should depend ONLY on `9z3.12.1`.
- `9z3.12.3` should depend ONLY on `9z3.12.2`.
- `9z3.12.4` (EXIT/gate) should depend ONLY on `9z3.12.3`.

This reduces P11 depth from 4 levels to a clean linear chain.

**Commands:**
```bash
bd dep remove sam_trader-9z3.12.2 sam_trader-9z3.11.5
bd dep remove sam_trader-9z3.12.3 sam_trader-9z3.11.5
bd dep remove sam_trader-9z3.12.3 sam_trader-9z3.12.1
bd dep remove sam_trader-9z3.12.4 sam_trader-9z3.12.1
bd dep remove sam_trader-9z3.12.4 sam_trader-9z3.12.2
```

#### Phase 10
| Ticket | Redundant Dep | Covered Via |
|--------|---------------|-------------|
| `9z3.11.5` | `9z3.11.1` | `9z3.11.4` |
| `9z3.11.5` | `9z3.11.2` | `9z3.11.4` |
| `9z3.11.5` | `9z3.11.3` | `9z3.11.4` |

**Recommendation:** EXIT tickets MAY keep explicit deps on all work tickets for robustness. However, to reduce depth, `9z3.11.5` can depend only on `9z3.11.4` since `11.4` already chains `11.3 -> 11.2 -> 11.1`.

**Command (optional):**
```bash
bd dep remove sam_trader-9z3.11.5 sam_trader-9z3.11.1
bd dep remove sam_trader-9z3.11.5 sam_trader-9z3.11.2
bd dep remove sam_trader-9z3.11.5 sam_trader-9z3.11.3
```

#### Phase 9
| Ticket | Redundant Dep | Covered Via |
|--------|---------------|-------------|
| `9z3.10.6` | `9z3.10.1` | `9z3.10.3` |
| `9z3.10.6` | `9z3.10.2` | `9z3.10.3` |
| `9z3.10.6` | `9z3.10.3` | `9z3.10.5` |
| `9z3.10.6` | `9z3.10.4` | `9z3.10.5` |
| `9z3.10.10` | `9z3.10.4` | `9z3.10.5` |
| `9z3.10.11` | `9z3.10.5` | `9z3.10.10` |
| `9z3.10.12` | `9z3.10.5` | `9z3.10.11` |

**Recommendation:**
- `9z3.10.6` (EXIT) currently depends on 8 tickets. Reduce to direct deps on `9z3.10.5`, `9z3.10.9`, `9z3.10.12` only (the terminal nodes of each branch).
- Alternatively, keep EXIT deps flat by depending only on `9z3.10.5`, `9z3.10.9`, `9z3.10.12`.

### 3.3 Feature Parents with Blocking Dependencies (CLOSED — historical)

These closed feature tickets violate Rule 1. They cannot be changed, but note for future phases:

| Feature | Illegal Dependency |
|---------|-------------------|
| `sam_trader-9z3.2` | `sam_trader-9z3.1.9` (P0 exit) |
| `sam_trader-9z3.3` | `sam_trader-vec` (unknown ticket) |
| `sam_trader-9z3.4` | `sam_trader-9z3.3.7` (P2 exit) |
| `sam_trader-9z3.5` | `sam_trader-9z3.4.3` (P3 work) |

**Lesson:** Future features must never carry blocking dependencies. Phase gating belongs on the first work ticket of each phase, not on the feature container.

### 3.4 Work Tickets with Extra Labels (CLOSED — historical)

25 closed tickets have extra labels like `new`, `port`. These violate the "exactly one label" rule but are historical. No action needed unless reopening.

---

## 4. Target Dependency Depth After Cleanup

Simulated depth after applying all recommended removals:

| Ticket | Current Depth | After Cleanup | Reduction |
|--------|--------------|---------------|-----------|
| `9z3.12.4` | 38 | **12** | -26 |
| `9z3.12.3` | 37 | **11** | -26 |
| `9z3.12.2` | 36 | **10** | -26 |
| `9z3.12.1` | 35 | **9** | -26 |
| `9z3.11.5` | 34 | **8** | -26 |
| `9z3.11.4` | 33 | **7** | -26 |
| `9z3.11.3` | 32 | **6** | -26 |
| `9z3.11.2` | 31 | **5** | -26 |
| `9z3.11.1` | 31 | **5** | -26 |
| `9z3.10.6` | 30 | **5** | -25 |

**New max depth: ~12 levels** (one per phase from P0 to P11), which is exactly the intended architecture.

---

## 5. Recommended Ticket Sequence for Ralph

After cleanup, the deterministic sort in `ralph_loop.sh` will naturally pick tickets in this order:

1. **Phase 5** (feature 6): `9z3.6.5`, `9z3.6.6`, `9z3.6.7` → `9z3.6.4` (EXIT)
2. **Phase 6** (feature 7): `9z3.7.1`, `9z3.7.7`, `9z3.7.2`, `9z3.7.3`, `9z3.7.4`, `9z3.7.5`, `9z3.7.8` → `9z3.7.6` (EXIT)
3. **Phase 7** (feature 8): `9z3.8.1`, `9z3.8.4`, `9z3.8.2`, `9z3.8.3`, `9z3.8.5` → `9z3.8.6` (EXIT)
4. **Phase 8** (feature 9): `9z3.9.1`, `9z3.9.2`, `9z3.9.4`, `9z3.9.3`, `9z3.9.5` → `9z3.9.6` (EXIT)
5. **Phase 9** (feature 10): `9z3.10.1`, `9z3.10.2`, `9z3.10.4`, `9z3.10.7`, `9z3.10.3`, `9z3.10.5`, `9z3.10.8`, `9z3.10.9`, `9z3.10.10`, `9z3.10.11`, `9z3.10.12` → `9z3.10.6` (EXIT)
6. **Phase 10** (feature 11): `9z3.11.1`, `9z3.11.2`, `9z3.11.3`, `9z3.11.4` → `9z3.11.5` (EXIT)
7. **Phase 11** (feature 12): `9z3.12.1`, `9z3.12.2`, `9z3.12.3` → `9z3.12.4` (EXIT)

---

## 6. Action Checklist

### Immediate (this session)
- [x] Update `scripts/ralph/ralph_loop.sh` with deterministic sorting
- [x] Update `AGENTS.md` with clarified rules
- [x] Write this audit document

### Next Session (ticket cleanup)
- [ ] Remove cross-phase skip links (5 deps)
- [ ] Remove redundant transitive deps in P11 (5 deps)
- [ ] Remove redundant transitive deps in P10 (optional: 3 deps)
- [ ] Remove redundant transitive deps in P9 (optional: 7 deps)
- [ ] Run `bd dep cycles` to verify no cycles introduced
- [ ] Run `bd ready --json` to verify depth is reduced
- [ ] Commit `bd dolt push` to sync changes

### Ongoing
- [ ] New tickets: never add cross-phase skip links
- [ ] New tickets: never add redundant transitive dependencies
- [ ] New features: ensure zero blocking dependencies
- [ ] New work tickets: exactly one label (`phase-N`)
- [ ] New EXIT tickets: exactly two labels (`exit, phase-N`)
