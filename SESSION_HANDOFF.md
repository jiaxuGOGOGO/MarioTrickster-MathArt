# SESSION HANDOFF — MarioTrickster-MathArt

> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and always reflects the latest project state.

## Project Overview
- **Current version**: 0.8.0
- **Last updated**: 2026-04-16T00:00:00Z
- **Last session**: SESSION-008 (audit & task integration)
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
Manus proactively searches academic papers, GitHub projects, and Reddit discussions for relevant mathematical models. Useful models are converted into code and registered in the math registry. This covers: procedural generation, physics simulation, color science, animation math, shader math, and any other domain relevant to pixel art game asset creation.

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

### [HIGH] `TASK-001`: Full-Pipeline Quality Control & Invalid Iteration Prevention
- **Status**: In progress (3/4 QC checkpoints integrated)
- **Scope** (expanded from original):
  1. Complete `mid_generation` checkpoint integration — knowledge and math models must guide generation throughout, not just score at the end.
  2. Enhance `StagnationGuard` to detect consecutive identical/invalid outputs, auto-diagnose root cause (e.g., math-art parameter conflict), and generate a structured report.
  3. Ensure AUTONOMOUS mode (no AI) runs stably with local rules only — never blocks iteration due to missing AI connection.
  4. In ASSISTED mode (AI available), enable deeper quality arbitration via API proxy.
- **Files**: `mathart/evolution/inner_loop.py`, `mathart/evolution/stagnation.py`, `mathart/quality/controller.py`

### [HIGH] `TASK-007`: External Knowledge & Math Model Continuous Injection
- **Status**: Ongoing (ready whenever user uploads materials)
- **Scope** (expanded from original):
  1. **PDF/Book Distillation**: User uploads → Manus extracts art/design rules → writes to `knowledge/*.md` → pushes to GitHub.
  2. **Math Paper Mining**: Manus proactively searches academic papers and converts relevant math models into code registered in `math_registry.py`.
  3. **Sprite/Asset Learning**: Analyze user-provided Sprite and SpriteSheet references, extract mathematical features (palette, proportions, symmetry, edge density) to guide evolution constraints.
  4. **GitHub/Reddit Scouting**: Reference excellent open-source projects for implementation patterns and techniques.
  5. **Deduplication**: All knowledge injection is deduplicated via `DeduplicationEngine`, but valuable knowledge is never discarded.
- **Next distill session ID**: DISTILL-008
- **Files**: `knowledge/*.md`, `mathart/distill/parser.py`, `mathart/evolution/math_registry.py`, `mathart/evolution/paper_miner.py`

### [MEDIUM] `TASK-008`: Unity Shader Integration & Pseudo-3D Extension Path
- **Status**: Not started
- **Scope**:
  1. Study and integrate Unity Shader techniques to enhance final art asset quality (normal maps, lighting, post-processing).
  2. Design extension interfaces in the existing 2D pixel/SDF architecture for future pseudo-3D rendering (parallax layers, depth-based lighting, sprite stacking).
  3. Document a clear roadmap from current 2D to pseudo-3D capability.
- **Files**: `mathart/export/bridge.py`, new `mathart/shader/` module (to be created)

### [MEDIUM] `TASK-003`: Level Generation → Asset Export Pipeline Connection
- **Status**: Not started
- **Depends on**: TASK-001
- **Scope**: Connect `LevelSpecBridge` to `ExportBridge` for automatic asset sizing, tiling validation, and Unity-ready export based on level requirements.
- **File**: `mathart/export/bridge.py`

### [LOW] `TASK-005`: External Compute & Hardware Acceleration
- **Status**: Not started
- **Scope** (expanded from original):
  1. Enable CUDA-accelerated differentiable rendering when NVIDIA GPU is available.
  2. Explore Stable Diffusion / AI image model API integration for higher-quality sprite generation, while keeping the core pipeline independent.
  3. All hardware acceleration is **optional** — the core system must always work without it.
- **Requires**: NVIDIA GPU (CUDA 11+) and/or Stable Diffusion API access
- **File**: `mathart/sdf/renderer.py`

---

### [DONE] Completed Tasks
- `TASK-002`: Sprite reference upload workflow (CLI: add-sprite, add-sheet, sprites) — SESSION-006
- `TASK-004`: Noise texture generator (Perlin, Simplex, fBm, ridged, turbulence, domain warp) — SESSION-007
- `TASK-006`: Workspace management (inbox hot folder, output classification, file picker) — SESSION-007

---

## Capability Gaps (External Upgrades Needed)

| Gap | Description | Requires | Priority |
|-----|-------------|----------|----------|
| `DIFFERENTIABLE_RENDERING` | Real-time parameter gradients via differentiable rasterization | NVIDIA GPU (CUDA 11+) | MEDIUM |
| `UNITY_SHADER_PREVIEW` | Live shader rendering feedback in Unity Editor | Unity 2021.3+ LTS | MEDIUM |
| `AI_IMAGE_MODEL` | High-quality sprite generation via diffusion model | GPU + Stable Diffusion API | HIGH |

---

## Recent Evolution History (Last 5 Sessions)

### SESSION-008 — v0.8.0 (2026-04-16)
- Audit & task integration session
  - Fixed 2 Windows compatibility bugs (GBK encoding, JSON parsing)
  - Comprehensive audit of all user requirements from conversation history
  - Integrated and deduplicated all tasks into unified task list
  - Added TASK-008 (Unity Shader & Pseudo-3D)
  - Expanded TASK-001 scope (full-pipeline QC + invalid iteration prevention)
  - Expanded TASK-007 scope (math paper mining + GitHub scouting + sprite learning)
  - Expanded TASK-005 scope (Stable Diffusion + optional hardware)
  - Recorded core vision: three interlocking loops + quality principles + collaboration model

### SESSION-007 — v0.8.0 (2026-04-15)
- Best score: 0.000 | Tests: 424
  - TASK-004 DONE: Noise texture generator
  - TASK-006 DONE: Workspace management
  - Registered noise_texture_generator in MathModelRegistry

### SESSION-006 — v0.7.0 (2026-04-15)
- Best score: 0.000 | Tests: 380
  - TASK-002 DONE: Sprite CLI workflow
  - BUG FIX: _try_widen_space() optimizer.space

### SESSION-005 — v0.6.0 (2026-04-14)
- Best score: 0.000 | Tests: 371
  - Major architecture upgrade: self-evolving brain system

---

## Instructions for Next AI Session

1. Read `PROJECT_BRAIN.json` for the full machine-readable state.
2. Read `DISTILL_LOG.md` to see what knowledge has been distilled.
3. Read `MINE_LOG.md` to see what math papers have been mined.
4. Check `STAGNATION_LOG.md` for any unresolved stagnation issues.
5. **Review the Core Vision section above** to understand the three-loop architecture and quality principles.
6. Continue from the highest-priority pending task.
7. **CRITICAL — PDF Distillation**: When the user uploads PDFs or reference materials, manually read them, extract mathematical/artistic rules, and write them into `knowledge/*.md` using the standard table format. Deduplicate against existing knowledge but never discard valuable information.
8. **CRITICAL — Math Mining**: When the user requests math model discovery, search academic papers and GitHub, evaluate relevance, and implement useful models in `mathart/` with registration in `math_registry.py`.
9. **CRITICAL — Sprite Learning**: When the user provides Sprite/SpriteSheet images, analyze them for mathematical features and inject constraints into the evolution parameter space.
10. **CRITICAL — No Invalid Iterations**: If the user reports repeated identical outputs, diagnose the root cause (math-art conflict, parameter space exhaustion, etc.) and produce a structured report before attempting fixes.
11. Always push changes to GitHub after completing a task so the user can `git pull` and run evolution locally.
12. When external hardware/software is needed, clearly communicate what the user needs to install and why.

---
*Auto-generated by ProjectMemory at 2026-04-16T00:00:00Z*
