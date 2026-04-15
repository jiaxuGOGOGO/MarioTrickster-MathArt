# SESSION_HANDOFF.md
> This document is auto-generated and always reflects the latest project state.

## Project Overview
- **Current version**: 0.16.1
- **Last updated**: 2026-04-15T08:30:00Z
- **Last session**: SESSION-016-Audit (Strict Gap Audit & CLI Fixes)
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
Manus proactively searches academic papers, GitHub projects, Papers with Code, Shadertoy, and LLM-assisted suggestions for relevant mathematical models. Useful models are converted into code and registered in the math registry through a structured graduation workflow (candidate → experimental → stable). This covers procedural generation, physics simulation, color science, animation math, shader math, and any other domain relevant to pixel art game asset creation.

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

In **SESSION-016-Audit**, we discovered that several tasks (TASK-018, TASK-014, TASK-015) had only been completed at the API level, leaving users without a CLI way to use them. These have now been **strictly fixed** by adding proper CLI commands.

---

## Pending Tasks (Priority Order)

### [EXTERNAL] `TASK-005`: Hardware Acceleration & Unity Integration
- **Status**: Blocked by external dependencies
- **Remaining Gap**: The code skeletons for differentiable rendering (`mathart/sdf/renderer.py`), Unity shader knowledge (`knowledge/unity_rules.md`), and pseudo-3D (`mathart/shader/pseudo3d.py`) are already implemented. Deep integration still requires the user to provide an NVIDIA GPU (CUDA 11+) and/or Unity Editor access.

### [USER-ACTION] `TASK-020`: Local Environment Configuration for Optional AI APIs
- **Status**: Awaiting user action
- **Description**: Configure optional external API keys for enhanced functionality:
  - `ANTHROPIC_API_KEY` — Enables Claude Code integration for AI-assisted code review and evolution suggestions
  - `SHADERTOY_API_KEY` — Enables Shadertoy shader search for SDF/noise technique discovery
  - `OPENAI_API_KEY` — Already available in project; enables LLM-assisted technique recommendations
- **Note**: The system works fully without these keys. They are optional accelerators that improve discovery quality and evolution guidance.

---

## Completed Tasks in SESSION-016 & Audit

### `TASK-018` [MEDIUM]: Expose level-aware export through end-to-end CLI workflow
- **Status**: DONE (Strictly Fixed in Audit)
- **What was done**: Added `mathart-export --from-level <spec.json>` and `--validate-only` options. The CLI now fully bridges `LevelSpecBridge` and `AssetExporter` to generate and export level-compliant placeholder assets automatically.

### `TASK-014` [MEDIUM]: Build Image-to-Math parameter inference workflow
- **Status**: DONE (Strictly Fixed in Audit)
- **What was done**: Added `mathart-evolve infer <image>` command. Users can now pass a reference image, and the CLI will extract color, shape, and texture constraints, save the seed JSON, and optionally start evolution immediately via `--evolve`.

### `TASK-015` [MEDIUM]: Scaffold graduation workflow for mined models
- **Status**: DONE (Strictly Fixed in Audit)
- **What was done**: Added `mathart-evolve graduate` command. Users can now run `--model <name>`, `--batch`, or `--dry-run` to formally promote mined models through the candidate → experimental → stable pipeline, fully exposing the API to the terminal.

### `TASK-016` [HIGH]: Align invalid-iteration handling with diagnose-and-safe-halt quality principle
- **Status**: DONE
- **What was done**: Added `SAFE_HALT` decision to `CheckpointDecision`. Modified `ArtMathQualityController` and `InnerLoopRunner` to safely halt and report instead of infinitely looping during autonomous stagnation.

### `TASK-013` [HIGH]: Add finite-difference gradient optimizer for CPU-only acceleration
- **Status**: DONE
- **What was done**: Created `FDGradientOptimizer` and integrated it into the inner loop as a memetic refinement step after evolutionary search.

### `TASK-019` [HIGH]: Generalize evolution CLI beyond texture-only target
- **Status**: DONE
- **What was done**: Extended `mathart-evolve run` to support `--target texture|sprite|animation|level-asset`, each with dedicated parameter spaces and SDF/animation renderers.

### `TASK-017` [MEDIUM]: Expand community source coverage beyond arXiv + GitHub
- **Status**: DONE
- **What was done**: Added `CommunitySourceRegistry` with `PapersWithCodeSource`, `ShadertoySource`, and `LLMAdvisorSource`, integrated seamlessly into `MathPaperMiner`.

---

## Strict Gap Audit Snapshot

The latest audit (SESSION-016-Audit) successfully identified and closed **3 major CLI disconnects** that violated the strict completion policy. All 481 tests are passing with zero regressions, and the codebase is completely free of Ruff lint errors. The remaining gaps are strictly limited to external dependencies (GPU/Unity) and optional API configurations.

| Priority | Gap | Status |
|---|---|---|
| ~~High~~ | ~~Invalid-iteration semantics mismatch~~ | RESOLVED in TASK-016 |
| ~~High~~ | ~~CPU-only acceleration gap~~ | RESOLVED in TASK-013 |
| ~~High~~ | ~~Narrow evolution CLI scope~~ | RESOLVED in TASK-019 |
| ~~Medium~~ | ~~Image-to-Math gap~~ | RESOLVED in TASK-014 (CLI fixed in Audit) |
| ~~Medium~~ | ~~Loop-3 source coverage gap~~ | RESOLVED in TASK-017 |
| ~~Medium~~ | ~~Export workflow gap~~ | RESOLVED in TASK-018 (CLI fixed in Audit) |
| ~~Medium~~ | ~~Scaffold-to-real-model gap~~ | RESOLVED in TASK-015 (CLI fixed in Audit) |
| External | GPU / Unity deep integration | Still blocked on user-provided runtime environment |
| User-Action | Optional API key configuration | User can configure for enhanced functionality |

---

## Capability Gaps (External Upgrades Needed)

| Gap | Description | Requires | Priority |
|-----|-------------|----------|----------|
| `DIFFERENTIABLE_RENDERING` | Real-time parameter gradients via differentiable rasterization | NVIDIA GPU (CUDA 11+) | MEDIUM |
| `UNITY_SHADER_PREVIEW` | Live shader rendering feedback in Unity Editor | Unity 2021.3+ LTS | MEDIUM |
| `AI_IMAGE_MODEL` | High-quality sprite generation via diffusion model | GPU + Stable Diffusion API | HIGH |
