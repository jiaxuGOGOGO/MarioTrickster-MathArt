> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and always reflects the latest project state.

## Project Overview
- **Current version**: 0.13.0
- **Last updated**: 2026-04-15T06:17:44Z
- **Last session**: SESSION-013 (Math mining loop → registry-backed scaffolds + fresh comprehensive gap audit)
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
The project runs locally on the user's machine, generating art assets through math-driven parameter search. Knowledge and math models guide the **entire generation process** (not just final scoring). When no AI is available, the system runs in AUTONOMOUS mode using local rules — it must **never stop iterating** due to missing AI. When AI is available (ASSISTED mode), it provides deeper quality arbitration.

### Loop 2: Knowledge Distillation (External, Manus-Driven)
The user uploads PDFs (art books, game design docs, reference materials) to the Manus chat. Manus reads, understands, and distills the knowledge into structured `knowledge/*.md` files and mathematical constraints. This is the **primary** way to improve the project. Manus also analyzes user-provided Sprite/SpriteSheet references to extract mathematical features. All injected knowledge is **deduplicated** but never at the cost of missing valuable information.

### Loop 3: Math Model Discovery (External, Manus-Driven)
Manus proactively searches academic papers, GitHub projects, and Reddit discussions for relevant mathematical models. Useful models are converted into code and registered in the math registry. This covers procedural generation, physics simulation, color science, animation math, shader math, and any other domain relevant to pixel art game asset creation.

### Quality Principles
- **No invalid iterations**: If consecutive outputs are identical or show no improvement, the system must detect this, analyze the root cause (e.g., math-art conflict), generate a diagnostic report, and safely halt for human review.
- **Knowledge permeates everything**: Math models and art knowledge guide generation at every stage — not just final evaluation.
- **Simplicity at the core**: Avoid excessive tool dependencies. External tools (GPU, Unity, Stable Diffusion) are optional add-ons, not requirements.
- **Pseudo-3D ready**: Architecture must preserve extension paths for future pseudo-3D rendering capabilities.
- **Cross-session continuity**: Every new Manus conversation picks up exactly where the last one left off via this document.

### Collaboration Model
1. **Manus executes** what can be done in the sandbox (code, knowledge injection, testing).
2. **User provides** what requires local action (install software, GPU setup, run tests, upload PDFs).
3. **Feedback loop**: Manus pushes → User pulls and runs → User reports results → Manus optimizes.

---

## Pending Tasks (Priority Order)

### [HIGH] `TASK-013`: Add a finite-difference gradient optimizer path for CPU-only inner-loop acceleration
- **Status**: Not started
- **Remaining Gap**: This is now the **highest-impact remaining local-runtime gap**. The roadmap already identifies finite-difference gradients as the next no-GPU speed upgrade, but the current inner loop still relies on evolutionary search only. There is no CPU-friendly gradient approximation path yet.
- **Files**: `mathart/distill/optimizer.py`, `mathart/evolution/inner_loop.py`, `knowledge/differentiable_rendering.md`

### [MEDIUM] `TASK-014`: Build a reference-image to parameter inference workflow (Image-to-Math bootstrap)
- **Status**: Not started
- **Remaining Gap**: The project can ingest sprite references and evaluate against them, but it still lacks a workflow that infers parameter seeds or math-model hints directly from uploaded reference images. This means the long-term Image-to-Math goal remains open.
- **Files**: `mathart/evolution/cli.py`, `mathart/evaluator/evaluator.py`, `mathart/workspace/manager.py`

### [MEDIUM] `TASK-015`: Promote mined scaffolds into concrete math implementations and stable registry entries
- **Status**: Not started
- **Remaining Gap**: SESSION-013 closed Loop 3 to the **registry-backed scaffold stage**: mined candidates can now become executable module scaffolds, smoke tests, and JSON registry records. However, these promoted modules are still placeholder implementations. The remaining gap is a workflow that upgrades high-value promoted scaffolds into real mathematical implementations, evaluator hooks, and eventual stable-registry graduation.
- **Files**: `mathart/evolution/paper_miner.py`, `mathart/evolution/math_registry.py`, `mathart/mined/`, `tests/test_paper_miner.py`

### [EXTERNAL] `TASK-005`: Hardware Acceleration & Unity Integration
- **Status**: Blocked by external dependencies
- **Remaining Gap**: The code skeletons for differentiable rendering (`mathart/sdf/renderer.py`), Unity Shader knowledge (`knowledge/unity_rules.md`), and Pseudo-3D (`mathart/shader/pseudo3d.py`) are already implemented. Deep integration requires the user to provide an NVIDIA GPU (CUDA 11+) and/or Unity Editor access.

---

### [DONE] Completed Tasks
- `TASK-001`: Complete QC Controller Integration (`mid_generation` now wired into `InnerLoopRunner`; retry-aware progress callbacks supported; scipy-free quick quality fallback) — SESSION-009
- `TASK-002`: Sprite reference upload workflow (CLI: add-sprite, add-sheet, sprites) — SESSION-006
- `TASK-003`: Connect LevelSpecBridge to ExportBridge (level-aware export APIs, tile/frame/palette validation, and level metadata in export manifests) — SESSION-012
- `TASK-004`: Noise texture generator (Perlin, Simplex, fBm, ridged, turbulence, domain warp) — SESSION-007
- `TASK-006`: Workspace management (inbox hot folder, output classification, file picker) — SESSION-007
- `TASK-009`: Add CLI command to run evolution loop (`mathart-evolve run` with built-in texture target and JSON metadata export) — SESSION-009
- `TASK-010`: Upgrade Evolutionary Optimizer (adaptive GA controls, diversity tracking, random immigrants, elite-guided local search, and `cma_es_like` strategy) — SESSION-010
- `TASK-011`: Connect MathPaperMiner to Real APIs (real arXiv Atom API + GitHub Search REST API integration, optional GitHub token auth, weighted relevance scoring from public metadata, LLM fallback retained) — SESSION-011
- `TASK-012`: Close the math mining loop from discovery candidates to registry-backed implementation scaffolds (enriched registry candidates, executable scaffold generation, smoke-test generation, registry JSON persistence, candidate manifest saving, and CLI `--promote`) — SESSION-013

---

## Comprehensive Gap Audit Snapshot

The latest audit indicates that the project has now closed three major gaps in succession: **real API-backed math mining**, **level-aware export bridging**, and **mine-to-registry scaffold promotion**. The remaining mismatch between the codebase and the original three-loop vision is no longer at the “missing plumbing” layer. It is now concentrated in three higher-order product gaps.

| Priority | Gap | Current status |
|---|---|---|
| High | CPU-only local optimization speed | Still missing finite-difference gradient path; local search remains evolutionary only. |
| Medium | Image-to-Math bootstrap | Reference images can be ingested and evaluated, but not converted into parameter seeds or model hints. |
| Medium | Scaffold-to-real-model graduation | Loop 3 now lands in executable scaffolds, but not yet in concrete mathematical implementations. |
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

### SESSION-013 — v0.13.0 (2026-04-15)
- Best score: 0.000 | Tests: 83
  - TASK-012 DONE: closed the math-mining loop to the registry-backed scaffold stage
  - Enriched mined candidates with registry metadata, parameter specs, quality metrics, and math-foundation summaries
  - Added promotion workflow that scaffolds Python modules and smoke tests, persists promoted entries into `math_models.json`-compatible records, and stores candidate manifests
  - Added CLI `--promote` support so mined or text-extracted candidates can be promoted without extra manual plumbing
  - Re-ran comprehensive gap audit and promoted finite-difference optimization to the highest remaining task while adding scaffold-to-real-model follow-up tracking

### SESSION-012 — v0.12.0 (2026-04-15)
- Best score: 0.000 | Tests: 79
  - TASK-003 DONE: connected `LevelSpecBridge` to `AssetExporter`
  - Added level-aware export APIs that accept `AssetSpec` directly for static sprites and animated sheets
  - Added validation for sprite dimensions, frame counts, palette limits, and tile-size-derived constraints before export
  - Added level metadata, source sprite names, and validation payloads to export metadata and manifests
  - Ran a comprehensive gap audit and added TASK-012, TASK-013, and TASK-014 to track remaining product gaps against the three-loop vision

### SESSION-011 — v0.11.0 (2026-04-15)
- Best score: 0.000 | Tests: 58
  - TASK-011 DONE: connected `MathPaperMiner` to the real arXiv Atom API and GitHub Search REST API
  - Added capability-aware heuristic scoring over public metadata instead of LLM-only simulated search results
  - Added optional GitHub bearer-token authentication via environment variables
  - Preserved LLM fallback behavior for offline or failed live-search paths
  - Added regression tests for Atom parsing, GitHub search parsing, auth headers, and live-source-first orchestration

### SESSION-010 — v0.10.0 (2026-04-15)
- Best score: 0.000 | Tests: 110
  - TASK-010 DONE: upgraded `EvolutionaryOptimizer` beyond a fixed GA baseline
  - Added adaptive mutation schedules driven by stagnation and diversity
  - Added elite-guided local search and random immigrant injection to recover from plateaus
  - Added lightweight `cma_es_like` diagonal-covariance sampling without external dependencies
  - Verified expanded regression coverage across distill, evolution, and quality test suites

### SESSION-009 — v0.9.0 (2026-04-15)
- Best score: 0.000 | Tests: 66
  - TASK-009 DONE: added `mathart-evolve run` with a built-in texture evolution target
  - TASK-001 DONE: integrated `mid_generation` checkpoint into `InnerLoopRunner`
  - Added retry-aware progress callback path for multi-step generators
  - Added scipy-free fallback in quick quality check so mid-generation QC works in minimal environments
  - Added regression tests for CLI run and mid-generation checkpoint invocation

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

   > **Welcome back! I have read the project state (v0.13.0). What would you like to do next?**
   > 
   > 1. **Distill Knowledge (Continuous)**: Upload a PDF, book, or Sprite image now. I will extract the math/art rules, inject them into the project, and optimize the evolution pipeline for better local generation.
   > 2. **Continue Development**: Work on the highest priority pending task (currently TASK-013: add a finite-difference gradient optimizer path for CPU-only inner-loop acceleration).
   > 3. **Mine Math Models**: Ask me to search the web/GitHub for specific math models (e.g., fluid dynamics, procedural animation) to add to the engine.
   > 4. **Diagnose Stagnation**: If your local evolution is stuck or producing identical results, paste the logs here and I will analyze the math-art conflicts.

7. **CRITICAL — PDF Distillation Workflow**: When the user chooses option 1 and uploads materials, manually read them, extract mathematical/artistic rules, and write them into `knowledge/*.md` using the standard table format. Use this opportunity to optimize the project's internal math models based on the new knowledge to make local evolution more efficient.
8. **CRITICAL — No Invalid Iterations**: If the user reports repeated identical outputs, diagnose the root cause (math-art conflict, parameter space exhaustion, etc.) and produce a structured report before attempting fixes.
9. **CRITICAL — Continuous Gap Audit**: At the start of every new session, or when the user provides new requirements, the AI MUST proactively audit the gap between the user's vision and the current codebase. Any new gaps discovered must be immediately added to the Pending Tasks list in this document and `PROJECT_BRAIN.json` to ensure they are tracked and resolved.
10. Always push changes to GitHub after completing a task so the user can `git pull` and run evolution locally.

---
*Auto-generated by ProjectMemory at 2026-04-15T06:17:44Z*
