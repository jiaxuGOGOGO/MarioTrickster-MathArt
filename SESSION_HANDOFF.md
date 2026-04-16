# SESSION HANDOFF

> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and reflects the latest verified project state and workflow rules.

## MANDATORY: Read Before Starting

1. **Read `COMMERCIAL_BENCHMARK.md`** — **MANDATORY FIRST**. Commercial pixel art standard and gap analysis. Every upgrade MUST be measured against this benchmark. If an upgrade does not shrink a percentage gap listed there, it is considered cosmetic.
2. **Read `DEDUP_REGISTRY.json`** — Prevents duplicate research and repeated external harvesting.
3. **Read `SESSION_PROTOCOL.md`** — Session efficiency rules, anti-repetition process, and protocol trigger logic.
4. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`** — Default method for precise, parallel, high-value external research. Includes Deep Reading Protocol rules for named north-star papers/repos.
5. **Read `PROJECT_BRAIN.json`** — Machine-readable global state.
6. **Read `research_session040_cli_pipeline_contract.md`** — SESSION-040 research synthesis for CLI Pipeline Contract, Data-Oriented Design (Mike Acton), Deterministic Lockstep (Glenn Fiedler), and USD Schema Validation (Pixar).
7. **Read `research_session039_transition_synthesis.md`** — SESSION-039 research synthesis for Inertialized Transition Synthesis (Bollo GDC 2018, Holden Dead Blending 2023) and Runtime Motion Matching Query (Clavet GDC 2016).
8. **Read `research_session036_umr_architecture.md`** — SESSION-036 research synthesis for OpenUSD `UsdSkel`, Houdini KineFX, Unreal AnimGraph layering, and the Unified Motion Representation (UMR) trunk.
9. **Read `research_session035_compliant_physics.md`** — SESSION-035 research synthesis for DeepMimic compliant PD tracking, AMP adversarial motion priors, and VPoser latent-space pose mutation.
10. **Read `research_session034_industrial_rendering.md`** — SESSION-034 research synthesis for Motion Matching (Clavet GDC 2016), Dead Cells 3D-to-2D pipeline (GDC 2018), and Guilty Gear Xrd hold frames (GDC 2015).
11. **Read `research_session033_phase_driven.md`** — SESSION-033 research synthesis for PFNN, DeepPhase, and Animator's Survival Kit phase-driven animation.
12. **Read `research_session032_pdg_framing.md`** — SESSION-032 research synthesis for PDG, USD-like scene description, and industrial PCG architecture closure.
13. **Read `research_session031_framing.md`** — SESSION-031 research synthesis for SMPL-like body latents, VPoser-style priors, dual quaternions, and motion matching.
14. **Read `research_notes_session030.md`, `BIOMECHANICS_RESEARCH_NOTES.md`, and `PHYSICS_ANIMATION_UPGRADE_PLAN.md`** for the physics / biomechanics / RL foundation.
15. **Read `SIM_CONDITIONED_NEURAL_RENDERING_EVALUATION.md`** when the goal touches diffusion rendering, ComfyUI/Wan pipelines, or simulation-conditioned neural rendering architecture.
16. **Read this file** — Current priorities, verified status, and handoff guidance.

---

## Project Overview

| Dimension | Value |
|-----------|-------|
| Current version | **0.31.0** |
| Last updated | 2026-04-16T15:30:00Z |
| Last session | **SESSION-040** |
| Best quality score | 0.8674 (best validated geometric sprite baseline) |
| Validation pass rate | **717/717 non-scipy full-suite PASS + 27 SESSION-040 pipeline contract tests PASS** |
| Total code lines | ~57,542 |
| Knowledge rules | 40 persisted rules |
| Math models registered | **28** |
| Project health score | **9.99/10** |

---

## What Changed in SESSION-040

### Attack Campaign 3: CLI Pipeline Contract & End-to-End Determinism (Gap 2, Gap 3 & P1 Closure)

SESSION-040 closed the **CLI pipeline contract enforcement** and **end-to-end deterministic validation** gaps that had been open since SESSION-035. The core philosophy is **"pipeline as data contract"** — leveraging Data-Oriented Design (DOD) to lock all backdoors, where any output that does not produce a valid hash is treated as scrap.

The implementation followed three north-star references through the Deep Reading Protocol:

1. **Mike Acton, CppCon 2014 — Data-Oriented Design and C++** — Transform the pipeline context into a strict immutable data class. The `UMR_Context` frozen dataclass enforces that all pipeline consumers receive the same deterministic context. Any attempt to bypass the UMR trunk triggers a Fail-Fast `PipelineContractError`.

2. **Glenn Fiedler, Gaffer on Games — Deterministic Lockstep** — Given the same random seed and state, every frame coordinate, contact tag, and render configuration must produce an **absolutely fixed SHA-256 hash**. The `UMR_Auditor` computes frame-level, state-level, and pipeline-level hashes and writes them to `.umr_manifest.json` as a golden master.

3. **Pixar USD Schema Validation & CI Mechanism** — Like validating a USD stage against its schema before rendering, the `PipelineContractGuard` validates pipeline order, state coverage, and bypass attempts before any frame is processed. The `.umr_manifest.json` enables CI regression detection: if anyone changes core logic and the hash drifts, CI immediately flags it.

> "The pipeline is a data contract. If it does not produce a valid hash, it is scrap." — SESSION-040 architectural rule

| Component | Landing in repo | Why it matters |
|-----------|-----------------|----------------|
| **`UMR_Context`** | `mathart/pipeline_contract.py` (357 lines) | Frozen dataclass with deterministic `context_hash`. All pipeline consumers receive the same immutable context. |
| **`PipelineContractGuard`** | `mathart/pipeline_contract.py` | Runtime contract enforcer. Blocks legacy bypass with `PipelineContractError`. Validates state coverage and pipeline order. |
| **`PipelineContractError`** | `mathart/pipeline_contract.py` | Fail-Fast exception for any contract violation. No backward compatibility for legacy paths. |
| **`UMR_Auditor`** | `mathart/pipeline_auditor.py` (390 lines) | Frame-level + state-level + pipeline-level SHA-256 hashes. Deterministic hash seal for CI regression. |
| **`ManifestSeal`** | `mathart/pipeline_auditor.py` | Serializable seal with `verify_against()` for golden master comparison. |
| **`ContactFlickerDetector`** | `mathart/pipeline_auditor.py` | Catches illegal high-frequency contact tag toggling (>50% toggle rate = failure). |
| **`phase_driven_idle_frame()`** | `mathart/animation/phase_driven_idle.py` (147 lines) | UMR-native idle frames with breathing oscillation and micro-sway. Eliminates last legacy fallback. |
| **`ContractEvolutionBridge`** | `mathart/evolution/evolution_contract_bridge.py` (485 lines) | Three-layer contract integration: L1 validation, L2 knowledge distillation, L3 fitness bonus. |
| **CLI rewrite** | `mathart/animation/cli.py` (103 lines) | All animation commands route through UMR pipeline. Legacy bypass permanently blocked. |
| **Research synthesis** | `research_session040_cli_pipeline_contract.md` | Full research document with DOD principles, deterministic lockstep, USD validation. |
| **Audit report** | `SESSION_040_AUDIT.md` | 27-item audit checklist across 5 categories. |

### Pipeline Data Flow (Post SESSION-040)

```
CharacterSpec
    │
    ▼
UMR_Context (frozen, deterministic hash)
    │
    ├── PipelineContractGuard (Fail-Fast on legacy bypass)
    │
    ▼
Per-State Loop:
    phase_driven_*_frame() → UMR clip
        │
        ├── AnglePoseProjector (physics)
        ├── BiomechanicsProjector (ZMP/IK)
        │
        ▼
    UMR_Auditor.register_state()
    ContactFlickerDetector.check()
    │
    ▼
UMR_Auditor.seal() → ManifestSeal
    │
    ├── .umr_manifest.json (SHA-256 golden master)
    ├── manifest.json (pipeline_contract section)
    └── per-state .umr.json clips
```

### Three-Layer Evolution Loop with Contract Integration

The contract pipeline is fully integrated into the three-layer evolution cycle:

- **Layer 1 (Inner Loop):** `PipelineContractGuard` rejects any generation attempt that bypasses the UMR trunk. Invalid outputs are immediately discarded.
- **Layer 2 (Outer Loop):** `ContractEvolutionBridge` auto-distills knowledge rules from contract violations and successes. Hash stability streaks are tracked and rewarded.
- **Layer 3 (Physics Evolution):** Contract compliance is a fitness dimension: +0.1 bonus for full compliance, -0.2 penalty for violations. Golden master tracking with `verify_against()` ensures hash stability across evolution generations.

### P1 Tasks Closed

| Task ID | Title | Status |
|---------|-------|--------|
| P1-PHASE-35A | Phase-driven coverage + CLI trunk enforcement | **DONE** |
| P1-BENCH-35A | End-to-end trunk reproducibility validation | **DONE** |
| P1-UMR-36A | Propagate UMR contract to CLI/exporters/distillation | **DONE** |

### Validation and Self-Audit

| Audit Category | Checks | Result |
|---------------|--------|--------|
| UMR_Context immutability + deterministic hash | 4 checks | **PASS** |
| PipelineContractGuard bypass detection | 5 checks | **PASS** |
| UMR_Auditor SHA-256 seal + verify_against() | 5 checks | **PASS** |
| ContactFlickerDetector | 3 checks | **PASS** |
| Phase-driven idle generator | 3 checks | **PASS** |
| End-to-end produce_character_pack integration | 4 checks | **PASS** |
| Cross-run deterministic hash consistency | 2 checks | **PASS** |
| ContractEvolutionBridge three-layer integration | 1 check | **PASS** |
| **Total** | **27 checks** | **27/27 PASS** |

Full-suite regression: **717/717 non-scipy PASS**, zero regressions (1 test updated for `PipelineContractError`).

---

## What Changed in SESSION-039

### Inertialized Transition Synthesis & Runtime Motion Matching Query (Bollo GDC 2018 / Holden Dead Blending 2023 / Clavet GDC 2016)

SESSION-039 closed the biggest remaining runtime gap in the animation pipeline: **state transitions (e.g., Run → Jump) had no principled blending mechanism**, risking foot skating and contact tag destruction. The session implemented two complementary industrial techniques and wired them into the three-layer evolution loop as a closed feedback cycle.

The implementation followed three north-star industrial references through the Deep Reading Protocol:

1. **Bollo Quintic Inertialization (Gears of War, GDC 2018)** — The target animation receives **100% rendering weight immediately** at the moment of transition. The source animation's residual momentum is captured as per-joint angular offsets and decayed via a quintic polynomial with C2 boundary conditions. This guarantees contact tags from the target are always authoritative — no blended contacts, no skating.

2. **Holden Dead Blending (Unreal Engine 5.3, 2023)** — A simpler alternative that extrapolates the source pose using recorded velocity with exponential decay, then cross-fades with the target. Only requires the current pose + velocity at transition time. More robust than quintic for edge cases.

3. **Clavet Runtime Motion Matching Query (Ubisoft, GDC 2016)** — Never enter a clip at frame 0 blindly. Compute `Cost = w_vel*diff(velocity) + w_contact*diff(foot_contacts) + w_phase*diff(phase)` and pick the lowest-cost frame. Contact weight is 2x velocity weight to prevent skating.

> "Traditional crossfade evaluates BOTH source and target during the blend window, doubling cost and destroying foot contact tags. Inertialization gives the target 100% weight immediately — the source's momentum decays as an additive offset that never touches contact semantics." — SESSION-039 architectural rule

| Component | Landing in repo | Why it matters |
|-----------|-----------------|----------------|
| **`TransitionSynthesizer`** | `mathart/animation/transition_synthesizer.py` (912 lines) | Two-strategy inertialized blending engine: Bollo quintic + Holden dead blending. Operates on `UnifiedMotionFrame` through the UMR bus. |
| **`RuntimeMotionQuery`** | `mathart/animation/runtime_motion_query.py` (929 lines) | UMR-native runtime entry-frame search with 16-D compact feature vectors, weighted cost function, and per-feature diagnostics. |
| **`MotionMatchingRuntime`** | `mathart/animation/runtime_motion_query.py` | Full runtime orchestrator: playback state, automatic/forced transitions, transition logging, quality metrics. |
| **Layer 3 Test Battery** | `mathart/evolution/evolution_layer3.py` (+139 lines) | Test 11 (transition quality) + Test 12 (entry frame cost) + 3 new diagnosis actions + 3 new diagnosis rules. |
| **Fitness evaluation** | `mathart/animation/physics_genotype.py` (+91 lines) | `evaluate_physics_fitness()` now runs a live Run→Jump transition test and feeds transition_quality (10%) + entry_quality (8%) into the overall formula. |
| **Knowledge distillation** | `mathart/evolution/evolution_layer3.py` | Rules 11-13: transition synthesis, runtime motion matching, and combined pipeline patterns. |
| **Convergence bridge** | `mathart/evolution/evolution_layer3.py` | `transition_quality`, `entry_frame_cost`, and `transition_strategy` now flow through the convergence bridge to downstream pipeline. |
| **Public API** | `mathart/animation/__init__.py` (+28 lines) | All SESSION-039 types exported. |
| **Research synthesis** | `research_session039_transition_synthesis.md` | Full research document with equations, architecture, and reference citations. |
| **Knowledge rules** | `knowledge/transition_synthesis_runtime_query.md` | 10 distilled rules: 3 hard constraints, 4 heuristics, 3 soft defaults. |

### Validation

- **15/15** SESSION-039 integration tests PASS
- **5/5** Python files pass syntax validation
- Fully backward-compatible: existing crossfade-free animation continues to work

---

## Remaining Top Gaps (Priority Order)

1. **P1-PHASE-37A:** Scene-aware distance matching sensors (terrain raycasts, apex prediction)
2. **P1-PHASE-33A:** Gait transition blending (walk/run/sneak phase-preserving)
3. **P1-PHASE-33B:** Terrain-adaptive phase modulation
4. **P1-INDUSTRIAL-34A:** Industrial renderer integration into AssetPipeline
5. **P1-INDUSTRIAL-34C:** 3D-to-2D mesh rendering path (Dead Cells full workflow)
6. **P1-NEW-10:** Production benchmark asset suite
7. **P1-AI-2:** Simulation-conditioned neural rendering bridge
8. **P1-HUMAN-31A:** Integrate SMPL-like shape latents into CharacterGenotype
9. **P1-HUMAN-31C:** Pseudo-3D paper-doll / mesh-shell backend using dual quaternions
10. **P3-4:** CI/CD + GitHub Actions (now has golden master hash seal to enforce)

---

## Three-Layer Evolution Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Internal Evolution (Inner Loop)                   │
│  Generate → Contract Validate → Evaluate → Optimize         │
│  [SESSION-040: PipelineContractGuard rejects legacy bypass] │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: External Knowledge Distillation (Outer Loop)      │
│  Ingest Research → Extract Rules → Validate Against Contract│
│  [SESSION-040: Auto-distill contract violation rules]       │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Self-Iteration Testing (Physics Evolution)        │
│  Train → Test (12+contract) → Diagnose → Evolve → Distill  │
│  [SESSION-040: Contract compliance as fitness dimension]    │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Files for Next Session

| File | Purpose |
|------|---------|
| `SESSION_HANDOFF.md` | This file — start here |
| `PROJECT_BRAIN.json` | Machine-readable project state |
| `COMMERCIAL_BENCHMARK.md` | Gap analysis reference |
| `SESSION_040_AUDIT.md` | SESSION-040 full audit checklist |
| `research_session040_cli_pipeline_contract.md` | SESSION-040 research synthesis |
| `mathart/pipeline_contract.py` | UMR_Context + PipelineContractGuard |
| `mathart/pipeline_auditor.py` | UMR_Auditor + ManifestSeal |
| `mathart/evolution/evolution_contract_bridge.py` | Three-layer contract bridge |
| `mathart/animation/phase_driven_idle.py` | Idle frame generator |
| `tests/test_pipeline_contract.py` | 27 contract tests |

---

## Next Session Instructions

1. **Read** `SESSION_HANDOFF.md` and `PROJECT_BRAIN.json` first
2. **Verify** the latest commit hash matches expectations
3. **Run** `python3 -m pytest tests/ -k "not scipy"` to confirm baseline
4. **Check** `COMMERCIAL_BENCHMARK.md` for the next highest-impact gap
5. **Pick** the top remaining P1 task and research before implementing
6. **Test** all changes against the existing 717+ test baseline
7. **Update** `SESSION_HANDOFF.md` and `PROJECT_BRAIN.json` before pushing
8. **Commit** with a descriptive message referencing the session ID

> **Golden Rule:** Every session must leave the project in a better, tested, documented state than it found it.
