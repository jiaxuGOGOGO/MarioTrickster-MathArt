# SESSION-040 Research Synthesis: CLI Pipeline Contract & End-to-End Determinism

## Research Protocol: Deep Reading + Parallel Scan

### North Star References

1. **Mike Acton — Data-Oriented Design (CppCon 2014)**
   - Core principle: "The transformation of data is the only purpose of any program"
   - Key insight: Understand the data first, then design the code around it
   - Application: `UMR_Context` must be the ONLY data shape flowing through the pipeline. Any code that tries to bypass it is a bug, not a feature.
   - Fail-fast: If a function receives data not in the canonical shape, it must reject immediately — no silent degradation.

2. **Pixar USD Schema Validation & CI (NVIDIA Omniverse VFI Guide)**
   - Validation as Quality Contract: transforms asset creation from one-off manual work into repeatable, automatable pipelines
   - Three pillars: **Inspectable** (reason about quality without opening files), **Mergeable** (updates don't break downstream), **Automatable** (quality gates execute repeatably)
   - Interoperability Contract Fulfillment: validating for correctness, structure, and content
   - Application: `.umr_manifest.json` serves as our USD-like validation contract — every pipeline output must pass schema validation before being accepted

3. **Glenn Fiedler — Deterministic Lockstep (Gaffer on Games, 2014)**
   - Core principle: "Determinism means given the same initial condition and the same set of inputs, your simulation gives exactly the same result. Not close. Not near enough. Exactly the same. Exact down to the bit-level."
   - Verification method: "Take a checksum of your entire physics state at the end of each frame and it would be identical"
   - Application: SHA-256 hash of pipeline output state — same seed + same config = same hash. Any deviation = pipeline corruption detected.

### Design Principles Extracted

#### Principle 1: Immutable Data Contract (DOD)
- `UMR_Context` as `@dataclass(frozen=True)` — the single source of truth for pipeline state
- Contains: random seed, character spec, animation config, pipeline version, all parameters needed for deterministic reproduction
- Once created, NEVER modified — new context = new object via `replace()`
- All pipeline functions accept `UMR_Context` as first argument — no exceptions

#### Principle 2: Fail-Fast Contract Enforcement
- `PipelineContractError` — custom exception for contract violations
- Raised immediately when:
  - A function receives non-UMR_Context input
  - Legacy `legacy_pose` paths are detected
  - Pipeline node order is violated
  - Required metadata fields are missing
- No backward compatibility for bypass paths — they are bugs

#### Principle 3: End-to-End Deterministic Hash Seal
- `UMR_Auditor` node at pipeline terminus
- Computes SHA-256 over: frame coordinates, contact tags, render config, node order
- Writes to `.umr_manifest.json` with:
  - `pipeline_hash`: SHA-256 of deterministic output
  - `seed`: random seed used
  - `version`: pipeline version
  - `node_order`: ordered list of pipeline nodes executed
  - `frame_count`: number of frames produced
  - `contact_tag_hash`: separate hash for contact integrity
- CI gate: if hash changes without explicit seed/config change → RED

#### Principle 4: Three-Layer Evolution Loop Integration
- **Layer 1 (Internal Evolution):** Genotype mutation → phenotype → pipeline → hash verification
- **Layer 2 (External Knowledge Distillation):** New research → knowledge rules → parameter updates → hash baseline update
- **Layer 3 (Self-Iteration Test):** Test battery → diagnosis → targeted fix → re-hash → convergence check

### Implementation Mapping to Existing Codebase

| Research Concept | Landing Target | Status |
|-----------------|---------------|--------|
| UMR_Context frozen dataclass | `mathart/pipeline/pipeline_context.py` (NEW) | TODO |
| PipelineContractError | `mathart/pipeline/contract.py` (NEW) | TODO |
| CLI entrypoint hardening | `mathart/pipeline.py` produce_character_pack() | TODO |
| UMR_Auditor hash node | `mathart/pipeline/auditor.py` (NEW) | TODO |
| .umr_manifest.json schema | `mathart/pipeline/manifest_schema.py` (NEW) | TODO |
| Layer 3 contract tests | `mathart/evolution/evolution_layer3.py` | TODO |
| Golden master CI gate | `tests/test_pipeline_determinism.py` (NEW) | TODO |

### References

- [1] Mike Acton, "Data-Oriented Design and C++", CppCon 2014
- [2] NVIDIA Omniverse, "USD Validation — VFI Guide", 2026
- [3] Glenn Fiedler, "Deterministic Lockstep", Gaffer on Games, 2014
- [4] Glenn Fiedler, "Floating Point Determinism", Gaffer on Games, 2010
- [5] Pixar, "Schema Versioning in USD", OpenUSD Documentation
- [6] Python dataclasses documentation, `frozen=True` semantics
- [7] Pydantic documentation, `model_config['frozen'] = True`
