## Session Identity

| Field | Value |
|---|---|
| Session ID | SESSION-094 |
| Previous Session | SESSION-093 |
| Date | 2026-04-20 |
| Commit | Recorded in Git history after push |
| Base Commit Read At Start | `c43149b2264ca244144ba7d70eabb6f1a0e0f146` |
| PROJECT_BRAIN.json version | v0.84.0 |

## Session Outcome

**SESSION-094** executed **CODE RED: Campaign III — CRITICAL-2.2 WFC Constraint Propagation Lock-Tile Absolute Survival**. The mission moved the architecture focus from the already-hardened **XPBD physical contact layer** up to the **space-generation topology layer**, and closed the specific defect where locked or pre-forced WFC cells could be treated as ordinary mutable domains during propagation, allowing silent state drift or post-hoc compromise.

This session aligned implementation against the user-specified external references before landing code. The practical conclusion extracted from **Oskar Stålberg / Townscaper** was preserved as **user-driven constraints first**: a designer-placed or system-forced tile must act as ground truth and must continue radiating adjacency pressure outward. The practical conclusion extracted from **Paul Merrell / Model Synthesis / CSP hard constraints** was preserved as **hard initial domain semantics**: a locked cell is a read-only singleton domain, not a suggestion. The practical conclusion extracted from **Conflict-Directed Backjumping** was preserved as **explicit dead-end signaling**: when propagation would erase a locked domain, the solver must raise a contradiction instead of silently mutating the lock or ignoring the conflict.

| Axis | Status | Result |
|---|---|---|
| Locked tile domain immunity | CLOSED | `_Cell.is_locked` marks read-only singleton domains whose entropy is permanently `0.0` |
| Propagation queue anti-overwrite closure | CLOSED | Base WFC now routes through queue-driven `_propagate_queue()` + `_restrict_cell_options()` |
| Outward radiation from locked seeds | CLOSED | `_propagate_locked_cells()` seeds every locked cell into propagation before normal observe/collapse |
| Explicit conflict contract | CLOSED | `WFCConstraintConflictError` is raised when any propagation would shrink a locked domain |
| Silent constrained fallback removal | CLOSED | `ConstraintAwareWFC.generate()` now re-raises the last contradiction instead of dropping to unconstrained generation after lock conflicts |
| Dead-end adversarial verification | CLOSED | Deterministic 1×2 conflict cases assert exception emission and value-level lock survival |
| Downstream regression safety | VERIFIED | `46/46` targeted WFC + tilemap-exporter tests PASS |

## Architecture State After SESSION-094

The repository now enforces a strict **lock-immunity propagation contract** in the WFC kernel. A locked cell is no longer merely a collapsed cell by convention; it is a **read-only singleton domain** whose state is protected at the point of domain reduction, not repaired afterwards. This directly satisfies the anti-red-line requirement that conflict interception must happen **inside propagation itself**, rather than in some post-generation clean-up pass.

### WFC Propagation Pipeline (Post-Refactor)

```text
┌────────────────────────────────────────────────────────────────────┐
│                  WFC generate() / _try_generate()                 │
├────────────────────────────────────────────────────────────────────┤
│  1. Initialize each cell with full option domain                  │
│  2. Apply structural constraints                                  │
│     • bottom row => _lock_cell(..., '#')                          │
│     • top rows => soft option pruning only                        │
│  3. ★ Propagate ALL locked seeds before entropy loop ★            │
│     • _propagate_locked_cells()                                   │
│     • locked cells constrain neighbours outward                   │
│  4. Observe lowest-entropy non-locked cell                        │
│  5. Collapse selected cell                                        │
│  6. Queue-based propagation                                       │
│     • _restrict_cell_options() intersects neighbour domains       │
│     • if target.is_locked and domain would shrink =>              │
│       raise WFCConstraintConflictError                            │
│     • if non-locked domain becomes empty => raise _Contradiction  │
│  7. Upper layer retries / backtracks on explicit contradiction    │
└────────────────────────────────────────────────────────────────────┘
```

The decisive invariant is now:

> **No propagation step may mutate a locked cell domain, yet every locked cell continues to exert full adjacency pressure onto surrounding non-locked cells.**

This is the exact dual requirement the task imposed. The solution does **not** cheat by skipping locked nodes wholesale, and it does **not** cheat by restoring tiles after generation. Instead, it protects the locked domain **at the moment a domain-reduction attempt occurs**.

## Research-to-Code Traceability

| External reference / principle | Distilled engineering rule | Concrete repository landing |
|---|---|---|
| Oskar Stålberg / Townscaper / user-driven constraints first | User-placed or pre-forced tiles are authoritative facts and must drive surrounding topology | Locked cells are seeded into propagation before the entropy loop via `_propagate_locked_cells()` |
| Paul Merrell / Model Synthesis / hard initial domain | Initial hard constraints are singleton read-only domains, not soft hints | `_Cell.is_locked` + `_lock_cell()` + locked entropy `0.0` |
| CSP / AC-style domain pruning | Domain reduction must happen through the propagation kernel itself | `_propagate_queue()` + `_restrict_cell_options()` centralize all option intersection |
| Conflict-Directed Backjumping / domain wipe-out => dead-end | Contradictions must be raised explicitly and handled by restart/backtrack, not silently compromised | `WFCConstraintConflictError` and re-raise path in `ConstraintAwareWFC.generate()` |
| Anti-silent-failure red line from task | Never allow constrained WFC to “just keep going” by weakening the lock | Removed the old unconstrained fallback after constrained lock conflicts |

## Files Touched in SESSION-094

| Type | File | Purpose |
|---|---|---|
| MOD | `mathart/level/wfc.py` | Base propagation refactor: `is_locked`, `_lock_cell()`, `_propagate_locked_cells()`, `_propagate_queue()`, `_restrict_cell_options()`, `WFCConstraintConflictError` |
| MOD | `mathart/level/constraint_wfc.py` | Seed locked propagation before collapse; record `conflict_count` / `last_error`; stop silent unconstrained fallback after lock conflicts |
| MOD | `tests/test_constraint_wfc.py` | Replace weak existence assertions; add deterministic adversarial lock-conflict tests with value-level lock preservation checks |
| NEW | `research/session094_wfc_lock_audit_notes.md` | Session research notes tracing Stålberg / Merrell / CDB principles into implementation decisions |
| MOD | `SESSION_HANDOFF.md` | Session closure summary and next-step guidance |
| MOD | `PROJECT_BRAIN.json` | Session metadata, task state, capability-gap closure, next HIGH-2.3 task |

## Verification Summary

Two tiers of local verification were completed. First, the core WFC test suite was strengthened and executed with deterministic contradiction cases. Second, the downstream tilemap export chain was re-run to ensure the propagation-core change did not break environment export semantics.

| Test Suite | Result |
|---|---|
| `tests/test_constraint_wfc.py` | `36/36` PASS |
| `tests/test_wfc_tilemap_exporter.py` | `10/10` PASS |
| **Total targeted** | **46/46 PASS, 0 FAIL** |

### New Dead-End Tests Added

| Test | Scenario | Assertion |
|---|---|---|
| `test_locked_tile_radiates_constraints_to_neighbour` | Locked `A` next to non-locked `{A,B}` | Neighbour is pruned to `{A}` while locked tile stays unchanged |
| `test_conflicting_non_locked_tile_raises_explicit_error_and_preserves_lock` | Non-locked collapsed `B` propagates into locked `A` | Raises `WFCConstraintConflictError`; locked cell `options/tile/collapsed/is_locked` remain byte-for-byte/value-for-value identical |
| `test_incompatible_locked_tiles_raise_conflict_and_remain_intact` | 1×2 tiny grid with locked `A` and locked `B`, mutually incompatible | Guaranteed contradiction; both locked cells remain intact after exception |

These tests close the user’s anti-cheating guardrails: there is now a **100% deterministic dead-end case**, and the suite proves that the solver would rather **fail loudly** than rewrite a locked tile.

## Updated TODO Status

| Priority | Item | State |
|---|---|---|
| CRITICAL-2.1 | XPBD Post-Stabilization Final Contact Pass | CLOSED in SESSION-093 |
| CRITICAL-2.2 | WFC locked-tile absolute survival and explicit contradiction closure | **CLOSED in SESSION-094** |
| HIGH-2.3 | Boundary constraint verification and extreme-pressure purge | **READY / TODO** |
| P1-ARCH-4 | PDG v2 runtime semantics | Pending |
| P3-GPU-BENCH-1 | Sparse-cloth production CUDA evidence | Substantially closed, pending final large-scale run |

## Preparation for HIGH-2.3: Boundary Constraint Verification & Extreme Pressure Purge

With the **physical layer** and the **level-topology layer** now both hardened around “absolute survival of hard constraints,” the next seamless step is to attack **HIGH-2.3**, which should focus on **boundary/frontier semantics** rather than core lock immunity. The propagation kernel is now trustworthy enough that the remaining risk shifts from “locked tiles being rewritten” to “edge/topology assumptions not being exhaustively validated under extreme frontier layouts.”

### Recommended Micro-Adjustments Before HIGH-2.3

| Theme | Recommended preparation |
|---|---|
| Boundary observability | Add per-step diagnostics for frontier cells: source coordinate, target coordinate, direction, old domain, new domain, contradiction reason |
| Reproducible adversarial corpus | Introduce a tiny seed corpus for edge-heavy, corridor-heavy, and seam-heavy maps so contradictions can be replayed deterministically |
| Frontier-specific property tests | Add fuzz/property tests for left/right boundaries, top-air strip, bottom-ground lock strip, one-cell corridors, T-junctions, and fan-in/fan-out chokepoints |
| Exporter closure | Assert that exporter/autotiling layers never mask a topological contradiction by rendering a visually plausible but semantically inconsistent boundary |
| Mutation discipline | Add a narrow “recently-collapsed frontier set” so HIGH-2.3 can audit boundary mutations without touching the central IoC / backend architecture |

### Why HIGH-2.3 Is Now Well-Posed

Before SESSION-094, any boundary stress campaign would have produced noisy and misleading results, because the core solver could silently corrupt locked truth. That would have made boundary diagnostics observationally untrustworthy. After SESSION-094, the kernel now has a stable invariant: **locked truth cannot be overwritten silently**. This means future boundary-pressure failures can be interpreted as genuine topology/frontier defects rather than artifacts of hidden lock corruption.

## Handoff Note

If the next session begins **HIGH-2.3**, do **not** reopen the closed CRITICAL-2.2 problem by adding post-hoc repair passes. Keep all enforcement at the propagation kernel boundary. Start from the new lock-immunity architecture in `mathart/level/wfc.py`, then extend verification outward with boundary-specific adversarial seeds, exporter consistency assertions, and frontier diagnostics. The correct philosophy is now established across both major foundations:

> **Hard constraints survive absolutely, contradictions fail explicitly, and downstream quality work only proceeds after the core truth-preservation contract is secure.**
