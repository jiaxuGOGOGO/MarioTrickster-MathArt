# SESSION HANDOFF

> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and reflects the latest verified project state and workflow rules.

## MANDATORY: Read Before Starting

1. **Read `DEDUP_REGISTRY.json`** — Prevents duplicate research and repeated external harvesting.
2. **Read `SESSION_PROTOCOL.md`** — Session efficiency rules, anti-repetition process, and protocol trigger logic.
3. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`** — Default method for precise, parallel, high-value external research. Now includes SESSION-027 enhanced query construction and synthesis rules.
4. **Read `PROJECT_BRAIN.json`** — Machine-readable global state.
5. **Read `research_notes_session027.md`** — Latest research synthesis for the semantic genotype system.
6. **Read this file** — Current priorities, verified status, and handoff guidance.

---

## Project Overview

| Dimension | Value |
|-----------|-------|
| Current version | **0.19.0** |
| Last updated | 2026-04-16T12:00:00Z |
| Last session | **SESSION-027** |
| Best quality score | 0.8674 (best validated geometric sprite baseline) |
| Validation pass rate | **538/538 = 100%** |
| Total code lines | 35,278 |
| Knowledge rules | 12 |
| Math models registered | 10 |
| Project health score | **9.3/10** |

## What Changed in SESSION-027

### P1-NEW-9B DONE: Character Evolution 2.5 — Semantic Genotype System

This is the **largest single-session code delivery** in the project's history. SESSION-027 replaced the flat numerical parameter mutation space with a hierarchical, component-based genotype system that enables genuinely diverse character families.

**New module: `mathart/animation/genotype.py` (823 lines)**

The genotype system introduces five architectural layers:

| Layer | Purpose | Implementation |
|-------|---------|----------------|
| **Archetype** | High-level semantic identity | `Archetype` enum: hero, villain, npc_worker, npc_merchant, monster_basic |
| **Body Template** | Defines proportions and available slots | `BodyTemplate` dataclass: humanoid_standard, humanoid_chibi, humanoid_tall, creature_round, creature_tall |
| **Part Registry** | Manages all equippable parts with compatibility rules | `PART_REGISTRY` dict: 11 registered parts across hat and face_accessory slots |
| **Genotype** | Complete evolvable character representation | `CharacterGenotype` dataclass with slots, proportion modifiers, palette genes |
| **Operators** | Mutation and crossover for evolution | `mutate_genotype()` (3-layer: structural + proportional + palette) and `crossover_genotypes()` |

**Key design decisions:**

- The genotype is the **search space**; the phenotype is `CharacterStyle` + `BodyPart` list
- All mutations operate on the genotype; `decode_to_style()` produces valid characters
- Slot compatibility is enforced by the registry, not by the mutation operator
- Mixed discrete/continuous encoding: discrete choices (archetype, part IDs) are categorical; continuous params are floats with defined ranges
- Backward-compatible: legacy `use_genotype=False` path is completely unchanged

**New hat SDF shapes:** crown, helmet, hood (added to `parts.py`)

**Pipeline integration:** `produce_character_pack()` now supports `use_genotype=True` which activates the semantic evolution path with crossover support.

### Research Protocol Enhancement

The precision parallel research protocol (`PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`) was enhanced with:

1. **Precision query construction rules** — Anchor to implementation artifacts, include constraints, name output formats, use domain vocabulary, create cross-domain bridge queries
2. **Parallel search dimension selection** — Choose between abstraction layer, competing approach, output consumer, or project subsystem dimensions based on gap type
3. **Post-search synthesis rule** — Require a decision matrix mapping findings to code files, rating novelty/feasibility/impact, and ending with a concrete implementation plan

### Validation Outcome

| Scope | Result |
|-------|--------|
| Full repository test suite | **538/538 PASS** (was 493) |
| New genotype unit tests | **45/45 PASS** |
| E2E integration tests | **4/4 PASS** |
| Legacy mode regression | **Zero regressions** |
| Code delta | +3,605 lines (genotype module + tests + pipeline integration + research notes) |

## SESSION-027 Deliverables

| Category | Change | File(s) | Impact |
|----------|--------|---------|--------|
| **CORE** | Semantic genotype system | `mathart/animation/genotype.py` | Replaces flat parameter mutation with hierarchical component-based evolution |
| **CORE** | Pipeline genotype integration | `mathart/pipeline.py` | `produce_character_pack()` supports `use_genotype=True` with semantic evolution + crossover |
| **CORE** | New hat SDF shapes | `mathart/animation/parts.py` | crown, helmet, hood added to hat_sdf |
| **API** | Genotype public exports | `mathart/animation/__init__.py` | Full genotype API exported |
| **TEST** | Genotype unit tests | `tests/test_genotype.py` | 45 tests covering structure, registry, decoding, mutation, crossover, presets, new hats |
| **TEST** | E2E integration tests | `test_genotype_e2e.py` | 4 tests covering pipeline integration, evolution, all presets, legacy mode |
| **RESEARCH** | Research notes | `research_notes_session027.md` | 5-dimension parallel research synthesis |
| **PROTOCOL** | Enhanced search mechanism | `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` | Query construction rules, dimension selection, synthesis requirements |
| **STATE** | Updated project memory | `PROJECT_BRAIN.json`, `DEDUP_REGISTRY.json` | Reflects SESSION-027 completion |

## Current Capability Snapshot

| Area | State | Notes |
|------|-------|-------|
| Geometric sprite generation | Strong | Evolved SDF sprites, layered rendering, texture-aware output |
| Character rendering | Strong | Direct pipeline output with usable exports |
| Character asset packaging | Strong | Multi-state sheets, GIFs, frames, atlas, manifest, palette |
| Character evolution/search | **Strong (2.5 with genotype)** | Semantic genotype with archetypes, body templates, part registry, 3-layer mutation, crossover, elite diversity, stagnation recovery |
| Character evolution depth | **Significantly improved** | Mutation space now includes structural changes (archetype, template, parts) not just proportions and palette |
| Benchmark-driven evaluation | Weak | Still lacks production benchmark suites and acceptance thresholds |
| Tile / level generation | Module exists, not integrated enough | WFC code exists but still needs top-level pipeline wiring |
| Shader generation | Module exists, not integrated enough | Needs direct production path and export wiring |
| Asset export bridge | Module exists, not integrated enough | Needs to become a first-class end-to-end pipeline step |
| Animation liveliness | Partial | Still missing per-frame parameter tracks and spring overlap |
| Organic material system | Missing | Reaction-diffusion / advanced organic masks are not yet integrated |
| Cross-session anti-duplication | Strong | SessionGuard + registry + default precision research protocol |
| Search / reference harvesting discipline | **Stronger** | Protocol now includes precision query construction and synthesis rules |
| Test reliability | Strong | Full suite green at 538 tests |

## Gap Analysis: Current vs. User Goal

The character evolution system is now **architecturally complete at the 2.5 level**. The genotype layer provides the structural foundation for rich character families. The remaining evolution gap is **content breadth** (more parts in the registry, more slot types supported by the renderer) rather than architecture.

| Goal Dimension | Current State | Remaining Gap |
|---------------|---------------|---------------|
| Produce usable assets, not demos | Stronger | Genotype-evolved character packs exist; level/shader/export closure still incomplete |
| Multi-state character output | **Done** | Needs broader state library and richer part registry content |
| Continuous evolution potential | **Strong** | Genotype architecture supports arbitrary expansion; needs more registered parts |
| Avoid repeated wasted sessions | Strong | Protocol, registry, skill, and enhanced search mechanism reduce duplication |
| Integrate best existing project modules | Partial | WFC, shader, export, and benchmark assets still under-integrated |
| Minimal software sprawl / self-contained | Good | Core generation path remains repo-local and controllable |
| Output suitable for real downstream use | Partial | Needs stronger benchmark definitions and engine-ready bundle closure |
| Search quality during upgrades | **Improved and validated** | Protocol was used in practice during SESSION-027 and enhanced based on real results |

## Biggest Remaining Gaps

1. **Production benchmark asset suites are still missing.** The repository validates correctness well, but lacks benchmark targets and acceptance thresholds.
2. **Part registry content is narrow.** Only hat and face_accessory slots have registered parts. Torso overlays, hand items, and foot accessories need SDF functions and renderer support.
3. **WFC tilemap pipeline integration remains unfinished.** The module exists but needs a top-level production path.
4. **Shader and export modules remain under-integrated.** Not yet first-class pipeline outputs.
5. **Per-frame SDF parameter animation is still absent.** Geometric animation remains largely transform-driven.
6. **Secondary motion and organic texture systems are still roadmap items.**

## Pending Tasks (Priority Order)

### P1 — Important

| ID | Task | Status | Effort | Description |
|----|------|--------|--------|-------------|
| P1-NEW-9C | Character evolution 3.0: expand part registry | TODO | Medium | Add torso overlays, hand items, foot accessories with new SDF functions and renderer support |
| P1-NEW-10 | Production benchmark asset suite | TODO | High | Add benchmark characters, tiles, and VFX with structured metadata, acceptance thresholds, and automated validators |
| P1-NEW-1 | WFC tilemap pipeline integration | TODO | High | Add `produce_level()`, connect WFC to asset packs, include playability validation |
| P1-NEW-7 | Export pipeline integration | TODO | High | Promote exporter to first-class stage with engine-ready bundles |
| P1-NEW-6 | Shader pipeline integration | TODO | Medium | Wire `ShaderCodeGenerator` into `AssetPipeline` |
| P1-2 | Per-frame SDF parameter animation | TODO | Medium | Add keyframed SDF parameter tracks |
| P1-NEW-3 | Spring-based secondary animation | TODO | Medium | Add follow-through / overlap motion |
| P1-NEW-2 | Reaction-diffusion textures | TODO | Medium | Add Gray-Scott organic texture generation |
| P1-NEW-8 | Quality checkpoint mid-generation | TODO | Low | Wire mid-generation quality checkpoint |

### P2 — Nice to Have

| ID | Task | Status | Effort |
|----|------|--------|--------|
| P2-1 | Sub-pixel rendering | TODO | Medium |
| P2-4 | Multi-objective optimization (NSGA-II) | TODO | High |
| P2-5 | Procedural outline variation | PARTIAL | Low |
| P2-6 | CMA-ES optimizer upgrade | TODO | Medium |
| P2-7 | Performance benchmarks | TODO | Low |

### P3 — Future

| ID | Task | Status | Effort |
|----|------|--------|--------|
| P3-1 | Auto knowledge distillation | PARTIAL | Medium |
| P3-2 | Web preview UI | TODO | High |
| P3-3 | Unity/Godot exporter plugin | TODO | Medium |
| P3-4 | CI/CD + GitHub Actions | TODO | Medium |
| P3-5 | End-to-end demo showcase script | TODO | Low |
| P3-6 | README update for SESSION-018~027 features | TODO | Low |

## Completed Tasks

### SESSION-027

| ID | Task | Result |
|----|------|--------|
| P1-NEW-9B | Character evolution 2.5: semantic genotype system | **DONE** — CharacterGenotype with archetypes, body templates, part registry, 3-layer mutation, crossover, pipeline integration. 538/538 tests passing. |
| PROTOCOL-027A | Precision research protocol enhancement | Enhanced query construction rules, dimension selection, post-search synthesis |
| RESEARCH-027 | Semantic mutation space research | 5-dimension parallel research → implementation synthesis |

### SESSION-026

| ID | Task | Result |
|----|------|--------|
| P0-PROTOCOL-026A | Repository precision parallel research protocol | Added `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` as the default external-research method |
| P0-PROTOCOL-026B | Session workflow integration | `SESSION_PROTOCOL.md` now triggers the research protocol by default when needed |
| P0-PROTOCOL-026C | Reusable skill validation | `mathart-precision-research` skill validated successfully for future reuse |

### SESSION-025

| ID | Task | Result |
|----|------|--------|
| P1-RESEARCH-025A | Dedup-first parallel gap research | New non-duplicative references distilled into `research_notes_session025.md` |
| P1-RESEARCH-025B | TODO and priority refresh | Next-session priorities are now narrower and more actionable |

### SESSION-024

| ID | Task | Result |
|----|------|--------|
| P1-NEW-9B-FOUNDATION | Character evolution 2.0 foundation | Character search now includes silhouette/state-distinction scoring, elite diversity, adaptive strength, and restart recovery |
| TEST-024 | Character evolution recovery/metadata test coverage | New metadata and recovery behavior are now covered by tests |
| VALIDATION-024 | Full repository validation | **493/493 PASS** |

### SESSION-023

| ID | Task | Result |
|----|------|--------|
| P1-NEW-9A | Baseline character evolution search | Character pack export runs a controllable search over anatomy/style/palette candidates before export |
| P0-HARDEN-4 | Palette adherence compatibility fix | Evaluator handles `Palette` objects in character evolution path |
| TEST-023 | Character evolution pipeline test | Search artifact and metadata export are covered by tests |
| VALIDATION-023 | Full repository validation | **492/492 PASS** |

### SESSION-022

| ID | Task | Result |
|----|------|--------|
| P1-NEW-5A | Character pack pipeline integration | `CharacterSpec` + `produce_character_pack()` + manifest / atlas / frame / GIF export |
| P1-NEW-4 | Multi-state sprite generation | Multi-state character packs with `idle`, `run`, `jump`, `fall`, `hit` |
| P0-HARDEN-1 | Character renderer runtime hardening | NumPy outline dilation fallback added |
| P0-HARDEN-2 | Cross-session duplicate-work session guard | `session_guard.py` + tests |
| P0-HARDEN-3 | Evaluator no-palette neutral skip fix | No-palette metric now neutral skip |
| VALIDATION-022 | Full repository validation | **491/491 PASS** |

## Instructions for Next AI Session

1. **Read `DEDUP_REGISTRY.json` first.**
2. **Read `SESSION_PROTOCOL.md` second.**
3. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` third** — now includes SESSION-027 enhanced query construction and synthesis rules.
4. Read `PROJECT_BRAIN.json`, `research_notes_session027.md`, and this handoff before coding.
5. Do **not** launch another broad external research sweep unless a new subsystem focus is chosen or the precision protocol explicitly justifies it.
6. If the user uses standard trigger wording such as **启动精准并行研究协议**, **优先启用精准并行研究协议**, or **本轮禁止直接开做，必须先启动精准并行研究协议**, treat it as an immediate protocol trigger.
7. If the goal is better final character art quality, start with **P1-NEW-9C** (expand part registry) or **P1-NEW-10** (production benchmarks).
8. If the goal is end-to-end production usefulness, start with **P1-NEW-1**, **P1-NEW-7**, or **P1-NEW-6**.
9. If motion/material quality is the next focus, start with **P1-2**, **P1-NEW-3**, or **P1-NEW-2**.
10. If the session starts to drift toward vague or repetitive iteration, trigger the precision protocol before searching broadly.
11. Always update this file and `PROJECT_BRAIN.json` before ending.
12. Preserve new scoring heuristics, benchmark schemas, harvested references, and protocol improvements in dedicated notes rather than re-harvesting the same material later.

## Quick Start

```python
from mathart.pipeline import AssetPipeline, CharacterSpec

pipeline = AssetPipeline(output_dir="output/", seed=7)

# Legacy mode (unchanged)
character = pipeline.produce_character_pack(
    CharacterSpec(
        name="mario_character",
        preset="mario",
        frames_per_state=6,
        states=["idle", "run", "jump", "fall", "hit"],
        evolution_iterations=4,
        evolution_population=5,
        evolution_preview_states=["idle", "run"],
    )
)

# SESSION-027: Genotype mode (new)
genotype_character = pipeline.produce_character_pack(
    CharacterSpec(
        name="evolved_mario",
        preset="mario",
        use_genotype=True,  # Activates semantic genotype evolution
        frames_per_state=6,
        states=["idle", "run", "jump", "fall", "hit"],
        evolution_iterations=5,
        evolution_population=6,
        evolution_crossover_rate=0.25,
    )
)
```

---
*Auto-generated by SESSION-027 at 2026-04-16T12:00:00Z*
