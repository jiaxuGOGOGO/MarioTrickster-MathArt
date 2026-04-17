# State Machine Graph Fuzzing Rules

This file captures the durable rules and audit summary for Gap D1 runtime graph coverage.

## Cycle 2

- States: `4`
- Expected edges: `16`
- Covered edges: `16`
- Edge coverage: `1.000`
- Expected edge pairs: `64`
- Covered edge pairs: `31`
- Edge-pair coverage: `0.484`
- Invalid edges: `0`
- Acceptance: `True`

## Runtime Graph Nodes

### idle

- Kind: `cyclic`
- Successors: `idle, jump, run, walk`

### jump

- Kind: `transient`
- Successors: `idle, jump, run, walk`

### run

- Kind: `cyclic`
- Successors: `idle, jump, run, walk`

### walk

- Kind: `cyclic`
- Successors: `idle, jump, run, walk`

## Distilled Rules

### STATE-GRAPH-002-A

- Rule: End-to-end animation state testing must operate on an explicit directed graph so the repository can distinguish expected edges, covered edges, and missing edges instead of relying on hand-written example paths only.
- Parameter: `state_machine.coverage_model`
- Constraint: `runtime states -> directed graph -> edge coverage audit`

### STATE-GRAPH-002-B

- Rule: Property-based stateful tests should generate whole transition programs and shrink failures to minimal edge sequences, while the runtime graph remains the single source of truth for legal transitions.
- Parameter: `state_machine.fuzzing_mode`
- Constraint: `Hypothesis stateful program generation + NetworkX coverage baseline`

### STATE-GRAPH-002-PASS

- Rule: Cycle 2 reached full edge coverage (16/16) with edge-pair coverage 0.484 over the runtime state graph.
- Parameter: `state_machine.coverage_status`
- Constraint: `edge_coverage = 1.0`
