# Runtime Distill Bus Rules

This file captures the repository's durable rules for the Gap A2 runtime distillation bus.

## Cycle 1

- Backend: `numba`
- Compiled module count: `18`
- Constraint count: `297`
- Contact-rule benchmark throughput: `458629.2` eval/s
- Acceptance: `True`

## Distilled Rules

### RUNTIME-BUS-001-A

- Rule: Repository knowledge must be lowered into dense runtime arrays before entering frame-critical loops; do not interpret nested dictionaries inside the 60fps contact path.
- Parameter: `distill_bus.execution_model`
- Constraint: `knowledge -> ParameterSpace -> dense arrays -> compiled closure`

### RUNTIME-BUS-001-B

- Rule: Foot contact detection should be compiled as a two-clause gate over foot height and vertical velocity, enabling direct machine-code execution and bitmask diagnostics.
- Parameter: `physics.contact.runtime_kernel`
- Constraint: `contact = (foot_height <= threshold) and (abs(foot_vertical_velocity) <= threshold)`

### RUNTIME-BUS-001-PASS

- Rule: Cycle 1 validated runtime bus execution with 297 compiled constraints and throughput 458629.2/s.
- Parameter: `runtime_distill.acceptance`
- Constraint: `compiled_runtime_bus = enabled`
