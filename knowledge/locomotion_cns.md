# Locomotion CNS Rules

Durable rules for the repository's phase-aligned, inertialized locomotion transition stack.

## Cycle 1

- Case count: `5`
- Accepted ratio: `0.80`
- Mean runtime score: `0.78`
- Mean sliding error: `0.0288`
- Worst phase jump: `0.0000`
- Mean contact mismatch: `0.2000`
- Acceptance: `True`

## Distilled Rules

### CNS-001-A

- Rule: Before switching locomotion states, align the target gait to the source support phase; phase correspondence is established first, interpolation second.
- Parameter: `locomotion.phase_alignment`
- Constraint: `target_phase = phase_warp(source_phase, source_markers, target_markers)`

### CNS-001-B

- Rule: At transition time, render the target gait immediately and decay only the residual source offset; target contacts remain authoritative.
- Parameter: `locomotion.transition_mode`
- Constraint: `pose = target_pose + inertialized_residual(source_minus_target)`

### CNS-001-C

- Rule: Locomotion quality gates must be compiled into dense feature evaluators so batch audits can score phase jump, sliding, contact mismatch and foot lock in one hot path.
- Parameter: `locomotion.runtime_gate`
- Constraint: `features -> dense array -> compiled runtime mask`

### CNS-001-PASS

- Rule: Cycle 1 produced accepted_ratio=0.80, mean_sliding_error=0.0288, mean_runtime_score=0.78.
- Parameter: `locomotion.acceptance`
- Constraint: `state = pass`
