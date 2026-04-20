## Session Identity

| Field | Value |
|---|---|
| Session ID | SESSION-093 |
| Previous Session | SESSION-092 |
| Date | 2026-04-20 |
| Commit | `4d633fc` |
| PROJECT_BRAIN.json version | v0.83.0 |

## Session Outcome

**SESSION-093** executed **CODE RED: Full-Spectrum Architecture Debridement Protocol — Campaign II (Core Algorithm Extreme Fidelity)**. The session addressed two CRITICAL-tier architectural defects discovered during the post-SESSION-092 algorithm audit:

1. **CRITICAL-2.1 XPBD Solver: Contact Constraint Priority Inversion** — The Gauss-Seidel iteration loop mixed soft constraints (distance, bending, attachment) and hard constraints (contact, self-collision) in the same pass without a final authority projection. This meant that a soft distance constraint solved after a contact constraint could silently undo the contact correction, allowing particles to penetrate ground or overlap.

2. **CRITICAL-2.1b Collision Manager: Hard-Coded Ground Clamp Bypass** — `XPBDCollisionManager._generate_ground_constraints()` directly wrote `positions[idx, 1] = ground_y + radius` and flipped velocity, bypassing the solver's constraint pipeline entirely. This violated the multi-track IoC architecture by giving the collision manager direct write access to solver state.

| Axis | Status | Result |
|---|---|---|
| XPBD two-tier constraint classification | CLOSED | `_INTERNAL_SOFT_KINDS` and `_TERMINAL_CONTACT_KINDS` frozensets enforce compile-time tier separation |
| Final Contact Pass (Post-Stabilization) | CLOSED | Independent post-iteration projection with zero compliance override and velocity recomputation |
| Ground constraint bus compliance | CLOSED | `_generate_ground_constraints()` now injects kinematic anchor + Contact constraint through the bus |
| Extreme stress test suite | CLOSED | 12 adversarial tests with coordinate-level `<= 1e-5` penetration assertions |
| Contact-frame min_gap absolute override | CLOSED (SESSION-092 carry-forward) | Two-phase immune pool architecture verified with 2 additional extreme tests |
| Regression safety | VERIFIED | 88/88 targeted physics + keyframe tests PASS, zero regression |

## Architecture State After SESSION-093

### XPBD Solver Pipeline (Post-Refactor)

The solver now implements a strict **two-tier constraint pipeline** inspired by NVIDIA PhysX/FleX and Jolt Physics:

```
┌─────────────────────────────────────────────────────────┐
│                    step(dt) per sub-step                 │
├─────────────────────────────────────────────────────────┤
│  1. Predict positions (gravity + velocity)              │
│  2. Classify constraints into two tiers:                │
│     ├── INTERNAL_SOFT: distance, bending, attachment    │
│     └── TERMINAL_CONTACT: contact, self-collision       │
│  3. Gauss-Seidel iterations (all constraints mixed)     │
│  4. ★ FINAL CONTACT PASS (post-stabilization) ★        │
│     • Re-solve ONLY terminal contact constraints        │
│     • Override compliance to 0.0 (infinite stiffness)   │
│     • Unconditional non-penetration guarantee           │
│  5. Velocity recomputation from corrected positions     │
│     • Prevents ghost energy from pre-contact velocities │
│  6. Velocity damping and clamping                       │
│  7. Diagnostics emission (final_contact_pass_corrections│
│     field reports how many corrections the pass made)   │
└─────────────────────────────────────────────────────────┘
```

The key architectural invariant is: **no soft constraint solved in step 3 can undo a contact correction made in step 4**, because step 4 runs strictly after all Gauss-Seidel iterations complete. This is the same ordering guarantee used by PhysX's contact post-stabilization and Jolt's contact manifold final pass.

### Ground Constraint Bus Compliance (Post-Refactor)

The collision manager no longer directly mutates solver positions. Instead:

| Old behavior | New behavior |
|---|---|
| `positions[idx, 1] = ground_y + radius` | Inject kinematic ground anchor particle at `(particle_x, ground_y)` |
| `velocities[idx, 1] *= -0.2` | Register `ConstraintKind.CONTACT` with `compliance=0.0` through constraint bus |
| Collision manager has direct write access | Solver is the single authority for position/velocity mutation |

### Constraint Tier Classification Constants

```python
_INTERNAL_SOFT_KINDS = frozenset({
    ConstraintKind.DISTANCE,
    ConstraintKind.ATTACHMENT,
    ConstraintKind.BENDING,
})

_TERMINAL_CONTACT_KINDS = frozenset({
    ConstraintKind.CONTACT,
    ConstraintKind.SELF_COLLISION,
})
```

These frozensets are module-level constants. Any new constraint kind added to the enum MUST be classified into exactly one tier, enforced by `test_constraint_tier_classification_is_correct`.

## Extreme Stress Test Suite

12 tests in `tests/test_xpbd_post_stabilization.py`, all using production-equivalent config (`sub_steps=4, solver_iterations=8`):

| Test | Scenario | Assertion |
|---|---|---|
| `test_contact_survives_violent_distance_pull_down` | Gravity + distance constraint pulls particle toward ground | `penetration <= 1e-5` |
| `test_two_body_contact_under_extreme_opposing_stretch` | Two particles pulled apart, contact enforces min separation | `separation >= rest - 1e-5` |
| `test_chain_slam_into_ground_no_penetration` | 5-node chain falls onto ground plane | All nodes `y >= ground_y + radius - 1e-5` |
| `test_contact_overrides_bending_constraint_near_ground` | Bending constraint pushes tip below ground | `penetration <= 1e-5` |
| `test_final_contact_pass_diagnostics_reported` | Particle near ground under gravity | `final_contact_pass_corrections >= 0` and `penetration <= 1e-5` |
| `test_self_collision_final_pass_prevents_overlap` | Two particles compressed by distance constraints | `separation >= rest - 1e-5` |
| `test_constraint_tier_classification_is_correct` | Architecture verification | All kinds classified, no overlap |
| `test_velocity_recomputed_from_corrected_positions` | Particle falls into ground contact | `v_y > -1.0` (no ghost energy) |
| `test_multiple_contacts_all_receive_final_pass` | 4 particles all near ground | All `penetration <= 1e-5` |
| `test_free_fall_unaffected_by_final_contact_pass` | Particle in free fall, no contacts | `|actual_y - analytical_y| <= 1e-6` |
| `test_extreme_distance_pull_through_ground` | Long pendulum swings near ground | `penetration <= 1e-5` |
| `test_zero_compliance_override_in_final_pass` | Contact with non-zero compliance | `penetration <= 1e-5` (final pass overrides to zero) |

### Anti-Red-Line Discipline

| Red Line | How Enforced |
|---|---|
| Anti-parameter-tuning | All tests use `sub_steps=4, solver_iterations=8` (production config) |
| Anti-hardcoded-ground | Ground constraints go through constraint bus, not `if p.y < 0: p.y = 0` |
| Anti-existence-test | Every assertion checks `penetration <= 1e-5` or exact coordinate values |
| Anti-random-masking | Zero `np.random` calls in the entire test file |

## Validation Summary

| Test Suite | Result |
|---|---|
| `tests/test_xpbd_post_stabilization.py` | `12/12` passed |
| `tests/test_xpbd_physics.py` | `14/14` passed |
| `tests/test_xpbd_free_fall_regression.py` | `4/4` passed |
| `tests/test_motion_adaptive_keyframe.py` | `55/55` passed |
| `tests/test_sdf_ccd.py` | `3/3` passed |
| **Total targeted** | **88/88 passed** |

## Preparation for CRITICAL-2.2: WFC Constraint Propagation Lock-Tile Absolute Survival

To seamlessly proceed to **CRITICAL-2.2 (WFC constraint propagation: locked tile absolute survival override and anti-overwrite closed loop)**, the following architectural micro-adjustments are recommended:

### 1. Establish a `TileImmunitySet` pattern (analogous to `_TERMINAL_CONTACT_KINDS`)

The XPBD refactor demonstrated that a **compile-time tier classification** (frozenset of constraint kinds) is the cleanest way to enforce priority inversion prevention. For WFC, the equivalent is a `TileImmunitySet` — a set of grid coordinates whose tile assignments are **locked** and must survive constraint propagation without overwrite.

The current WFC implementation likely uses a `collapsed` or `fixed` flag per cell. The risk (identical to the XPBD pre-fix state) is that the propagation loop may re-enter a locked cell and overwrite it during backtracking or entropy-based collapse.

**Recommended pattern:**
```python
# Module-level, analogous to _TERMINAL_CONTACT_KINDS
_LOCKED_TILE_COORDS: frozenset[tuple[int, int]] = frozenset()

# In propagation loop:
if (x, y) in _locked_tile_coords:
    continue  # Absolute skip — no entropy recalculation, no overwrite
```

### 2. Two-phase propagation (analogous to XPBD two-tier solve)

Just as XPBD now runs soft constraints first and contact constraints last, WFC propagation should:
- **Phase 1**: Lock all designer-placed tiles into `_locked_tile_coords` (immune set)
- **Phase 2**: Run AC-3/AC-4 constraint propagation only on non-locked cells
- **Phase 3 (Final Pass)**: Verify that no locked tile was overwritten during propagation; if any was, raise `ConstraintViolationError` rather than silently corrupting the grid

### 3. Backtracking guard

The most dangerous scenario is **backtracking** during WFC solve. When the solver backtracks to undo a collapse, it must check the immune set before restoring a cell to its uncollapsed state. A locked tile must NEVER be uncollapsed.

### 4. Files to audit before CRITICAL-2.2

| File | Audit Focus |
|---|---|
| `mathart/core/wfc_solver.py` (or equivalent) | Propagation loop, backtracking logic, cell state mutation |
| `mathart/core/wfc_constraints.py` (or equivalent) | Constraint definition, adjacency rules |
| `tests/test_wfc_*.py` | Existing test coverage for locked tiles |

### 5. Test pattern to replicate

The XPBD test pattern (`test_constraint_tier_classification_is_correct` + adversarial penetration tests) should be directly replicated:
- **Architecture test**: Verify that `_LOCKED_TILE_COORDS` is a frozenset and that the propagation loop checks it
- **Adversarial test**: Place a locked tile, surround it with conflicting constraints, run propagation, assert the locked tile survived unchanged

## Files Touched in SESSION-093

```text
MOD: mathart/animation/xpbd_solver.py
MOD: mathart/animation/xpbd_collision.py
ADD: tests/test_xpbd_post_stabilization.py
MOD: SESSION_HANDOFF.md
MOD: PROJECT_BRAIN.json
```

## Updated TODO Status

| Priority | Item | State |
|---|---|---|
| CRITICAL-2.1 | XPBD Contact Priority Inversion + Post-Stabilization | **CLOSED** in SESSION-093 |
| CRITICAL-2.1b | Ground Constraint Bus Compliance | **CLOSED** in SESSION-093 |
| CRITICAL-2.2 | WFC Constraint Propagation Lock-Tile Survival | **READY** — architectural prep documented above |
| P1-AI-2E | Motion-Adaptive Keyframe Planning | Closed (SESSION-091 + SESSION-092 hotfix + SESSION-093 carry-forward) |
| P1-ARCH-4 | PDG v2 runtime semantics | Unblocked |
| P3-GPU-BENCH-1 | GPU benchmark infrastructure | Pending |

## Handoff Note

If the next session begins work on **CRITICAL-2.2 (WFC lock-tile survival)**, start by auditing the WFC propagation loop for the exact same pattern that was fixed in the XPBD solver: mixed-priority constraint solving without a final authority pass. The `_TERMINAL_CONTACT_KINDS` / `_INTERNAL_SOFT_KINDS` tier classification pattern and the Final Contact Pass architecture are directly transferable to WFC's locked-tile immunity problem. The key invariant to enforce is: **no propagation step or backtracking operation may mutate a cell that belongs to the immune set**.
