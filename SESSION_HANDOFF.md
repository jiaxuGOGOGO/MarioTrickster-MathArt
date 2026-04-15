> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and always reflects the latest project state.

## Project Overview
- **Current version**: 0.15.0
- **Last updated**: 2026-04-15T06:40:54Z
- **Last session**: SESSION-015 (end-of-day strict audit refresh)
- **Best quality score achieved**: 0.000
- **Total iterations run**: 0

## Knowledge Base Status
- **Distilled knowledge files**: 19 (in `knowledge/` directory)
- **Math models registered**: 10 (9 stable + 1 experimental)
- **Sprite references**: 1
- **Next distill session ID**: DISTILL-008
- **Next mine session ID**: MINE-001

---

## Core Vision: Self-Evolving Math-Driven Art Engine

This project is driven by **three interlocking loops** that together form a continuously evolving brain:

### Loop 1: Internal Self-Evolution (Local, Autonomous)
The project runs locally on the user's machine, generating art assets through math-driven parameter search. Knowledge and math models guide the **entire generation process** (not just final scoring). When no AI is available, the system runs in AUTONOMOUS mode using local rules — it must **never stop iterating** due to missing AI. However, the system must still respect the quality principle that invalid iterations are diagnosed and escalated safely rather than silently looping forever.

### Loop 2: Knowledge Distillation (External, Manus-Driven)
The user uploads PDFs (art books, game design docs, reference materials) to the Manus chat. Manus reads, understands, and distills the knowledge into structured `knowledge/*.md` files and mathematical constraints. This is the **primary** way to improve the project. Manus also analyzes user-provided Sprite/SpriteSheet references to extract mathematical features. All injected knowledge is **deduplicated** but never at the cost of missing valuable information.

### Loop 3: Math Model Discovery (External, Manus-Driven)
Manus proactively searches academic papers, GitHub projects, and Reddit discussions for relevant mathematical models. Useful models are converted into code and registered in the math registry. This covers procedural generation, physics simulation, color science, animation math, shader math, and any other domain relevant to pixel art game asset creation.

### Quality Principles
- **No invalid iterations**: If consecutive outputs are identical or show no improvement, the system must detect this, analyze the root cause, generate a diagnostic report, and safely halt or escalate for human review.
- **Knowledge permeates everything**: Math models and art knowledge guide generation at every stage — not just final evaluation.
- **Simplicity at the core**: Avoid excessive tool dependencies. External tools (GPU, Unity, Stable Diffusion) are optional add-ons, not requirements.
- **Pseudo-3D ready**: Architecture must preserve extension paths for future pseudo-3D rendering capabilities.
- **Cross-session continuity**: Every new Manus conversation picks up exactly where the last one left off via this document.

### Collaboration Model
1. **Manus executes** what can be done in the sandbox (code, knowledge injection, testing, audit).
2. **User provides** what requires local action (install software, GPU setup, run tests, upload PDFs).
3. **Feedback loop**: Manus pushes → User pulls and runs → User reports results → Manus optimizes.

---

## Strict Completion Policy

As of **SESSION-015**, this project uses a **strict completion standard**: a task only counts as truly complete when the user-visible requirement is satisfied **end-to-end**, without hidden manual glue, placeholder logic, demo-only scope, or runtime behavior that contradicts the core vision.

This means some earlier tasks remain listed as done only in their **narrow implementation scope**, while any remaining product-level gap is now tracked explicitly as a new pending task instead of being implicitly treated as “close enough.”

---

## Pending Tasks (Priority Order)

### [HIGH] `TASK-016`: Align invalid-iteration handling with the documented diagnose-and-safe-halt quality principle
- **Status**: Not started
- **Remaining Gap**: This is now the **highest-priority strict-audit gap**. `ArtMathQualityController.iteration_end()` currently auto-recovers instead of stopping when AUTONOMOUS mode reaches a `HUMAN_REQUIRED` stagnation state. That directly conflicts with the documented rule that invalid iterations must be diagnosed, reported, and halted or escalated safely.
- **Files**: `mathart/quality/controller.py`, `mathart/evolution/stagnation.py`, `mathart/evolution/inner_loop.py`

### [HIGH] `TASK-013`: Add a finite-difference gradient optimizer path for CPU-only inner-loop acceleration
- **Status**: Not started
- **Remaining Gap**: The roadmap already identifies finite-difference gradients as the next no-GPU speed upgrade, but the current inner loop still relies on evolutionary search only. This remains the largest **local-runtime performance gap**.
- **Files**: `mathart/distill/optimizer.py`, `mathart/evolution/inner_loop.py`, `knowledge/differentiable_rendering.md`

### [HIGH] `TASK-019`: Generalize the evolution run workflow beyond the built-in texture demo target
- **Status**: Not started
- **Remaining Gap**: `mathart-evolve run` is real, but it currently exposes only `choices=["texture"]`. That means the CLI still does not provide a true user-facing evolution workflow for sprites, animations, or level-bound assets.
- **Files**: `mathart/evolution/cli.py`, `mathart/evolution/engine.py`, `mathart/evolution/inner_loop.py`

### [MEDIUM] `TASK-014`: Build a reference-image to parameter inference workflow (Image-to-Math bootstrap)
- **Status**: Not started
- **Remaining Gap**: The project can ingest sprite references and evaluate against them, but it still cannot infer parameter seeds or math-model hints directly from uploaded reference images. Upload support is therefore **not the same thing** as Image-to-Math.
- **Files**: `mathart/evolution/cli.py`, `mathart/evaluator/evaluator.py`, `mathart/workspace/manager.py`, `mathart/sprite/analyzer.py`

### [MEDIUM] `TASK-017`: Expand MathPaperMiner source coverage to include Reddit or equivalent community discovery channels
- **Status**: Not started
- **Remaining Gap**: Loop 3 is documented as mining academic papers, GitHub projects, and Reddit discussions. The current live implementation covers **arXiv + GitHub only**, so source coverage is still only partially aligned with the stated vision.
- **Files**: `mathart/evolution/paper_miner.py`, `README.md`, `SESSION_HANDOFF.md`

### [MEDIUM] `TASK-018`: Expose the level-aware export bridge through a true end-to-end CLI or workspace workflow
- **Status**: Not started
- **Remaining Gap**: The LevelSpecBridge-to-ExportBridge connection is implemented at the API layer, but `mathart-export` still only supports `--generate-demo` or manual scripting. A real user-facing export workflow is still missing.
- **Files**: `mathart/export/bridge.py`, `mathart/export/cli.py`, `mathart/level/spec_bridge.py`, `mathart/workspace/manager.py`

### [MEDIUM] `TASK-015`: Promote mined scaffolds into concrete math implementations and stable registry entries
- **Status**: Not started
- **Remaining Gap**: Loop 3 now closes to the **registry-backed scaffold stage**, but promoted modules are still placeholder implementations. The remaining gap is a workflow that upgrades high-value promoted scaffolds into real mathematical implementations, evaluator hooks, and stable-registry graduation.
- **Files**: `mathart/evolution/paper_miner.py`, `mathart/evolution/math_registry.py`, `mathart/mined/`, `tests/test_paper_miner.py`

### [EXTERNAL] `TASK-005`: Hardware Acceleration & Unity Integration
- **Status**: Blocked by external dependencies
- **Remaining Gap**: The code skeletons for differentiable rendering (`mathart/sdf/renderer.py`), Unity shader knowledge (`knowledge/unity_rules.md`), and pseudo-3D (`mathart/shader/pseudo3d.py`) are already implemented. Deep integration still requires the user to provide an NVIDIA GPU (CUDA 11+) and/or Unity Editor access.

---

## Scope-Complete Tasks (Not Equivalent to Final Product Completion)

The following tasks were completed in their **declared implementation scope**, but should **not** be interpreted as meaning the broader product need is fully solved.

- `TASK-001`: QC controller integration complete at the checkpoint wiring layer, but strict audit reopened the remaining policy mismatch as `TASK-016`.
- `TASK-002`: Sprite upload workflow complete, but the broader Image-to-Math requirement remains open as `TASK-014`.
- `TASK-003`: LevelSpecBridge-to-ExportBridge connection complete at the API layer, but the user-facing export workflow remains open as `TASK-018`.
- `TASK-009`: Evolution CLI entry exists, but the broader multi-target runtime workflow remains open as `TASK-019`.
- `TASK-011`: Real API mining exists for arXiv and GitHub, but broader Loop-3 source coverage remains open as `TASK-017`.
- `TASK-012`: Discovery-to-scaffold promotion exists, but scaffold-to-real-model graduation remains open as `TASK-015`.

The following tasks remain genuinely complete in their intended scope with no new strict-audit reopening at this time:

- `TASK-004`: Noise texture generator.
- `TASK-006`: Workspace management primitives.
- `TASK-010`: Evolutionary optimizer upgrade.

---

## Strict Gap Audit Snapshot

The latest audit concludes that the project is **still farther from true end-state completion than milestone-oriented summaries would imply**. Today’s closing review did **not** uncover any new hidden completions beyond the strict backlog already identified in SESSION-014, which means the current pending-task list remains the authoritative map of what still blocks the real product vision.

| Priority | Gap | Why it still matters |
|---|---|---|
| High | Invalid-iteration semantics mismatch | Runtime behavior still contradicts the documented quality rule in autonomous stagnation cases. |
| High | CPU-only acceleration gap | Local search still lacks the planned finite-difference path for non-GPU users. |
| High | Narrow evolution CLI scope | End users can only run a built-in texture demo target, not the broader asset workflows implied by the project vision. |
| Medium | Image-to-Math gap | Reference upload exists, but no parameter inference or model bootstrap exists. |
| Medium | Loop-3 source coverage gap | Live mining still does not include Reddit or an equivalent community source. |
| Medium | Export workflow gap | Level-aware export exists only as library plumbing, not as a user-ready command path. |
| Medium | Scaffold-to-real-model gap | Promoted mining results still stop at placeholder implementations. |
| External | GPU / Unity deep integration | Still blocked on user-provided runtime environment. |

---

## Capability Gaps (External Upgrades Needed)

| Gap | Description | Requires | Priority |
|-----|-------------|----------|----------|
| `DIFFERENTIABLE_RENDERING` | Real-time parameter gradients via differentiable rasterization | NVIDIA GPU (CUDA 11+) | MEDIUM |
| `UNITY_SHADER_PREVIEW` | Live shader rendering feedback in Unity Editor | Unity 2021.3+ LTS | MEDIUM |
| `AI_IMAGE_MODEL` | High-quality sprite generation via diffusion model | GPU + Stable Diffusion API | HIGH |

---

## Recent Evolution History (Last 5 Sessions)

### SESSION-015 — v0.15.0 (2026-04-15)
- Best score: 0.000 | Tests: 0
  - Performed an **end-of-day strict audit refresh** against the current codebase and user-visible workflows
  - Reconfirmed that `TASK-016`, `TASK-013`, `TASK-019`, `TASK-014`, `TASK-017`, `TASK-018`, `TASK-015`, and `TASK-005` remain the real attack order
  - Confirmed that no additional features should be promoted to “truly complete” beyond the narrowed scope already documented in SESSION-014
  - Kept the next development target on `TASK-016`

### SESSION-014 — v0.14.0 (2026-04-15)
- Best score: 0.000 | Tests: 0
  - Performed a **strict completion audit** instead of treating implementation milestones as final product completion
  - Added `TASK-016`, `TASK-017`, `TASK-018`, and `TASK-019` to capture gaps that were previously hidden behind narrower “done” statements
  - Reframed several earlier completed tasks as **scope-complete only**, with explicit follow-up tasks for the remaining requirement-level gaps
  - Reset the next development target to `TASK-016`

### SESSION-013 — v0.13.0 (2026-04-15)
- Best score: 0.000 | Tests: 83
  - `TASK-012` DONE: closed the math-mining loop to the registry-backed scaffold stage
  - Enriched mined candidates with registry metadata, parameter specs, quality metrics, and math-foundation summaries
  - Added promotion workflow that scaffolds Python modules and smoke tests, persists promoted entries into `math_models.json`-compatible records, and stores candidate manifests
  - Added CLI `--promote` support so mined or text-extracted candidates can be promoted without extra manual plumbing
  - Re-ran comprehensive gap audit and promoted finite-difference optimization to the highest remaining task while adding scaffold-to-real-model follow-up tracking

### SESSION-012 — v0.12.0 (2026-04-15)
- Best score: 0.000 | Tests: 79
  - `TASK-003` DONE: connected `LevelSpecBridge` to `AssetExporter`
  - Added level-aware export APIs that accept `AssetSpec` directly for static sprites and animated sheets
  - Added validation for sprite dimensions, frame counts, palette limits, and tile-size-derived constraints before export
  - Added level metadata, source sprite names, and validation payloads to export metadata and manifests
  - Ran a comprehensive gap audit and added `TASK-012`, `TASK-013`, and `TASK-014` to track remaining product gaps against the three-loop vision

### SESSION-011 — v0.11.0 (2026-04-15)
- Best score: 0.000 | Tests: 58
  - `TASK-011` DONE: connected `MathPaperMiner` to the real arXiv Atom API and GitHub Search REST API
  - Added capability-aware heuristic scoring over public metadata instead of LLM-only simulated search results
  - Added optional GitHub bearer-token authentication via environment variables
  - Preserved LLM fallback behavior for offline or failed live-search paths
  - Added regression tests for Atom parsing, GitHub search parsing, auth headers, and live-source-first orchestration

### SESSION-010 — v0.10.0 (2026-04-15)
- Best score: 0.000 | Tests: 110
  - `TASK-010` DONE: upgraded `EvolutionaryOptimizer` beyond a fixed GA baseline
  - Added adaptive mutation schedules driven by stagnation and diversity
  - Added elite-guided local search and random immigrant injection to recover from plateaus
  - Added lightweight `cma_es_like` diagonal-covariance sampling without external dependencies
  - Verified expanded regression coverage across distill, evolution, and quality test suites

---

## How to Talk to AI (User Guide)
Please refer to `HOW_TO_TALK_TO_AI.md` for the complete workflow on how to start, collaborate, and end a session with Manus to ensure continuous evolution.

---

## Instructions for Next AI Session

1. Read `PROJECT_BRAIN.json` for the full machine-readable state.
2. Read `DISTILL_LOG.md` to see what knowledge has been distilled.
3. Read `MINE_LOG.md` to see what math papers have been mined.
4. Check `STAGNATION_LOG.md` for any unresolved stagnation issues.
5. **Review the Core Vision section above** to understand the three-loop architecture and quality principles.
6. **CRITICAL — AI Greeting & Menu**: After reading this document, the AI MUST greet the user and present the following interactive menu to let the user choose the next action:

   > **Welcome back! I have read the project state (v0.14.0). What would you like to do next?**
   > 
   > 1. **Distill Knowledge (Continuous)**: Upload a PDF, book, or Sprite image now. I will extract the math/art rules, inject them into the project, and optimize the evolution pipeline for better local generation.
   > 2. **Continue Development**: Work on the highest priority pending task (currently `TASK-016`: align invalid-iteration handling with the documented diagnose-and-safe-halt quality principle).
   > 3. **Mine Math Models**: Ask me to search the web/GitHub/community sources for specific math models (e.g., fluid dynamics, procedural animation) to add to the engine.
   > 4. **Diagnose Stagnation**: If your local evolution is stuck or producing identical results, paste the logs here and I will analyze the math-art conflicts.

7. **CRITICAL — PDF Distillation Workflow**: When the user chooses option 1 and uploads materials, manually read them, extract mathematical/artistic rules, and write them into `knowledge/*.md` using the standard table format. Use this opportunity to optimize the project's internal math models based on the new knowledge to make local evolution more efficient.
8. **CRITICAL — No Invalid Iterations**: If the user reports repeated identical outputs, diagnose the root cause (math-art conflict, parameter space exhaustion, etc.) and produce a structured report before attempting fixes.
9. **CRITICAL — Continuous Gap Audit**: At the start of every new session, or when the user provides new requirements, the AI MUST proactively audit the gap between the user's vision and the current codebase. Any new gaps discovered must be immediately added to the Pending Tasks list in this document and `PROJECT_BRAIN.json` to ensure they are tracked and resolved.
10. Always push changes to GitHub after completing a task or audit update so the user can `git pull` and continue from the latest state.

---
*Auto-generated by ProjectMemory at 2026-04-15T06:28:27Z*
