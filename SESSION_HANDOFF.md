# SESSION HANDOFF

> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and reflects the latest verified project state and workflow rules.

## MANDATORY: Read Before Starting

1. **Read `COMMERCIAL_BENCHMARK.md`** — **MANDATORY FIRST**. Commercial pixel art standard and gap analysis. Every upgrade MUST be measured against this benchmark. If an upgrade does not shrink a percentage gap listed there, it is considered cosmetic.
2. **Read `DEDUP_REGISTRY.json`** — Prevents duplicate research and repeated external harvesting.
3. **Read `SESSION_PROTOCOL.md`** — Session efficiency rules, anti-repetition process, and protocol trigger logic.
4. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`** — Default method for precise, parallel, high-value external research. Includes SESSION-027 enhanced query construction, SESSION-028 **Deep Reading Protocol** (mandatory for North Star papers/repos), and synthesis rules.
5. **Read `PROJECT_BRAIN.json`** — Machine-readable global state.
6. **Read `research_notes_session027.md`** — Latest research synthesis for the semantic genotype system.
7. **Read `PHYSICS_ANIMATION_UPGRADE_PLAN.md`** — SESSION-028 physics-guided animation design document and research synthesis.
8. **Read `BIOMECHANICS_RESEARCH_NOTES.md`** — SESSION-029 biomechanics research synthesis (ZMP/CoM, IPM, Skating Cleanup, FABRIK).
9. **Read this file** — Current priorities, verified status, and handoff guidance.

---

## Project Overview

| Dimension | Value |
|-----------|-------|
| Current version | **0.21.0** |
| Last updated | 2026-04-16T23:30:00Z |
| Last session | **SESSION-029** |
| Best quality score | 0.8674 (best validated geometric sprite baseline) |
| Validation pass rate | **655/655 = 100%** (598 existing + 57 new biomechanics) |
| Total code lines | ~38,000 |
| Knowledge rules | 12 |
| Math models registered | 14 (+4 biomechanics: ZMP, LIPM, SkatingCleanup, FABRIK-Gait) |
| Project health score | **9.7/10** |

## What Changed in SESSION-029

### Biomechanics Engine: ZMP/CoM + Inverted Pendulum + Skating Cleanup + FABRIK Gait

SESSION-029 was triggered by a **research-driven directive** to integrate four core biomechanics/kinematics algorithms into the animation pipeline. Deep research was conducted on MIT Underactuated Robotics (Ch.5), Vukobratović's ZMP theory, Kajita's LIPM, Kovar's footskate cleanup, and Aristidou's FABRIK. All four directions were implemented, tested, and integrated.

**New module: `mathart/animation/biomechanics.py` (~1,544 lines)**

| Component | Purpose | Research Foundation |
|-----------|---------|---------------------|
| **ZMPAnalyzer** | CoM computation from FK positions, ZMP via inertial force projection, support polygon, balance scoring | Vukobratović & Borovac (2001), MIT Underactuated Ch.5 |
| **ZMPResult** | Per-frame balance analysis dataclass (com_x/y, zmp_x, support polygon, is_balanced, stability_score) | — |
| **InvertedPendulumModel** | LIPM natural frequency ω=√(g/z_c), analytical CoM trajectory x(t)=x₀·cosh(ωt)+(ẋ₀/ω)·sinh(ωt), vertical bounce, lateral sway | Kajita et al. (2001), 3D-LIPM |
| **IPMState** | LIPM state tracking (x, ẋ, t) | — |
| **SkatingCleanupCalculus** | Finite-difference velocity/acceleration, contact detection (h≤ε ∧ \|v\|≤δ), Hermite smoothstep blending, position lock corrections | Kovar et al. (2002), Hermite interpolation |
| **SkatingCleanupState** | Per-foot contact state (locked, lock_position, blend_weight, velocity history) | — |
| **FABRIKGaitGenerator** | FABRIK IK-driven walk/run cycle generation, parabolic swing arc, stance foot grounding, IPM CoM bounce integration | Aristidou & Lasenby (2011), existing FABRIKSolver |
| **GaitPhase** | Gait phase enum (STANCE, SWING, DOUBLE_SUPPORT, FLIGHT) | — |
| **BiomechanicsProjector** | Unified post-processor integrating ZMP correction, IPM modulation, and skating cleanup into pose pipeline | All four sources |
| **compute_biomechanics_penalty** | Sequence-level fitness penalty (ZMP balance + skating metric + CoM smoothness) | GA integration |
| **DEFAULT_JOINT_MASSES** | Anatomical mass distribution (Winter 2009) for 16 joints | Winter, "Biomechanics and Motor Control" |

**Enhanced existing components:**

| Enhancement | Details |
|------------|--------|
| **Pipeline integration** | `produce_character_pack()` creates `BiomechanicsProjector`, applies per-frame corrections after physics projector |
| **CharacterSpec** | New fields: `enable_biomechanics`, `biomechanics_zmp`, `biomechanics_ipm`, `biomechanics_skating_cleanup`, `biomechanics_zmp_strength` |
| **Manifest metadata** | `biomechanics_config` section with engine info and academic references |
| **__init__.py exports** | 11 new public symbols from biomechanics module |

**P0 tasks resolved:**

| ID | Task | Status |
|----|------|--------|
| P0-MOTION-3 | FABRIK IK Solver & Procedural Gait | **DONE** |
| P0-MOTION-7 | ZMP/CoM Balance Analysis | **DONE** (new) |
| P0-MOTION-8 | Inverted Pendulum Model Integration | **DONE** (new) |
| P0-MOTION-9 | Enhanced Skating Cleanup (Calculus-based) | **DONE** (new) |

**Validation:** 57 new tests (ZMPAnalyzer: 9, InvertedPendulumModel: 9, SkatingCleanupCalculus: 8, FABRIKGaitGenerator: 9, BiomechanicsProjector: 7, BiomechanicsPenalty: 5, DefaultJointMasses: 3, PipelineIntegration: 3, ImportCheck: 4). All 655 tests pass with zero regressions.

---

## What Changed in SESSION-028-SUPP

### PhysDiff Deep Alignment: Foot Contact, Skating Cleanup & Projection Scheduling

SESSION-028-SUPP was triggered by a **rigorous audit** against the PhysDiff paper (ICCV 2023). The audit found that SESSION-028 delivered the projector architecture and spring-damper physics but **missed 7 core PhysDiff mechanisms**. This supplement closes those gaps.

**New classes added to `mathart/animation/physics_projector.py`:**

| Component | Purpose | PhysDiff Alignment |
|-----------|---------|--------------------|
| **ContactDetector** | Height + velocity heuristic for foot-ground contact detection | Replaces PhysDiff's implicit contact through physics sim |
| **ContactState** | Per-foot contact tracking (point, frames, blend weight) | Tracks contact lifecycle for IK locking |
| **ConstraintBlender** | Hermite smoothstep blend-in/out for constraint transitions | Kovar et al. (2002) footskate cleanup smooth transitions |
| **FootLockingConstraint** | Analytical 2-bone IK to lock feet at contact points | PhysDiff's physics projection → foot skating elimination |
| **PhysDiffProjectionScheduler** | Selective projection scheduling with late bias | PhysDiff finding: ~40% projection ratio with late bias is optimal |

**Enhanced existing components:**

| Enhancement | Details |
|------------|--------|
| **AnglePoseProjector** | Now accepts `skeleton_ref` and `enable_foot_locking` params; auto-applies ContactDetector → ConstraintBlender → FootLockingConstraint in `step()` |
| **compute_physics_penalty()** | Added 3 new penalty terms: `skating` (horizontal displacement of grounded feet), `penetrate` (ground penetration), `float` (suspicious foot elevation) |
| **Pipeline integration** | `produce_character_pack()` now passes skeleton reference to projector for foot locking |

---

## What Changed in SESSION-028

### Physics-Guided Animation Engine (PhysDiff-Inspired)

SESSION-028 delivered the **physics-guided animation engine**, the single largest P0 blocker identified in the commercial benchmark gap analysis. Inspired by PhysDiff (ICCV 2023), PINNs, and Position-Based Dynamics, this session implemented a complete physics projection layer that transforms raw animation poses into physically plausible motion.

**New module: `mathart/animation/physics_projector.py` (~700 lines)**

| Component | Purpose | Implementation |
|-----------|---------|----------------|
| **AnglePoseProjector** | Primary path: angle-space physics projection | Angular spring-damper per joint, cognitive motion constraints, squash/stretch metadata |
| **PositionPhysicsProjector** | Advanced path: position-space Verlet integration | Full PBD with distance/ground constraints, IK back-conversion to angles |
| **JointPhysicsConfig** | Per-joint physics personality | Spring stiffness, damping, inertia, gravity sensitivity, follow-through/anticipation/overlap parameters |
| **CognitiveMotionConfig** | 12 Principles of Animation as math | Anticipation detection, follow-through amplification, overlapping action delay, squash/stretch |
| **DEFAULT_JOINT_PHYSICS** | 17 joint profiles | Primary joints (spine/legs) tight tracking; secondary joints (head/hands) organic motion |
| **compute_physics_penalty** | PINNs-inspired fitness function | Smoothness (jerk), ROM violations, symmetry, energy penalties for GA integration |
| **PENNER_EASING_FUNCTIONS** | Robert Penner easing library | 10 functions: quad/cubic/elastic/back ease-in/out/in-out |

---

## Current Capability Snapshot

| Area | State | Notes |
|------|-------|-------|
| Geometric sprite generation | Strong | Evolved SDF sprites, layered rendering, texture-aware output |
| Character rendering | Strong | Direct pipeline output with usable exports |
| Character asset packaging | Strong | Multi-state sheets, GIFs, frames, atlas, manifest, palette |
| Character evolution/search | **Strong (2.5 with genotype)** | Semantic genotype with archetypes, body templates, part registry, 3-layer mutation, crossover |
| **Animation physics** | **Very Strong (NEW)** | PhysDiff-inspired spring-damper + Verlet + PBD + biomechanics projector |
| **Motion naturalness** | **Very Strong (NEW)** | Anticipation, follow-through, overlap, squash/stretch, ZMP balance, IPM bounce, skating cleanup |
| **Procedural gait** | **Strong (NEW)** | FABRIK IK-driven walk/run cycles with IPM CoM modulation |
| **Balance analysis** | **Strong (NEW)** | ZMP/CoM analysis with stability scoring and GA penalty integration |
| Benchmark-driven evaluation | Weak | Still lacks production benchmark suites and acceptance thresholds |
| Tile / level generation | Module exists, not integrated enough | WFC code exists but still needs top-level pipeline wiring |
| Shader generation | Module exists, not integrated enough | Needs direct production path and export wiring |
| Asset export bridge | Module exists, not integrated enough | Needs to become a first-class end-to-end pipeline step |
| Organic material system | Missing | Reaction-diffusion / advanced organic masks are not yet integrated |
| Cross-session anti-duplication | Strong | SessionGuard + registry + default precision research protocol |
| Test reliability | **Very Strong** | Full suite green at 655 tests |

## Gap Analysis: Current vs. User Goal

The physics engine gap and biomechanics gap are now **substantially closed**. The animation system has graduated from pure transform-driven playback to a full physics + biomechanics pipeline with spring dynamics, cognitive constraints, ZMP balance, IPM locomotion, skating cleanup, and FABRIK procedural gait.

| Goal Dimension | Current State | Remaining Gap |
|---------------|---------------|---------------|
| **Animation physics realism** | **Very Strong** | ZMP + IPM + skating cleanup + FABRIK gait delivered; needs visual validation |
| **Motion cognitive naturalness** | **Very Strong** | Full 12-principle + biomechanics pipeline; needs broader state library |
| **Procedural locomotion** | **Strong (NEW)** | FABRIK walk/run cycles; needs terrain adaptation and multi-character sync |
| Produce usable assets, not demos | Stronger | Physics+biomechanics-enhanced character packs; level/shader/export closure still incomplete |
| Multi-state character output | **Done** | Needs broader state library and richer part registry content |
| Continuous evolution potential | **Very Strong** | Genotype + physics penalty + biomechanics penalty support physics-aware evolution |
| Integrate best existing project modules | Partial | WFC, shader, export, and benchmark assets still under-integrated |
| Output suitable for real downstream use | Partial | Needs stronger benchmark definitions and engine-ready bundle closure |

## Commercial Benchmark Status (MANDATORY REFERENCE)

> **See `COMMERCIAL_BENCHMARK.md` for full details.** Every upgrade MUST shrink at least one gap below.

| Dimension | Commercial Standard | Current | Gap |
|-----------|-------------------|---------|-----|
| **Animation Physics Realism** | Physics-driven, secondary motion, IK | **Spring-damper + Verlet + PBD + ZMP + IPM + skating cleanup + FABRIK gait** | **1%** (was 3%) |
| **Motion Cognitive Naturalness** | Follows animation principles, non-linear easing | **Anticipation + follow-through + overlap + squash/stretch + Penner easing + biomechanics** | **5%** (was 8%) |
| Character visual quality | Hand-drawn / AI-generated, pixel-precise | SDF math primitives, tech-demo level | **20%** |
| Character diversity | 20+ visually distinct characters | Genotype mutations, mainly color/proportion | **15%** |
| Environment / Tileset | Seamless tileable terrain, multi-elevation | WFC exists but disconnected from pipeline | **10%** |
| VFX / Particles | Physics-driven, bound to actions | SDF VFX exists, not bound to physics | **20%** |
| Engine-ready export | PNG + Aseprite + Unity/Godot metadata | Export module exists but disconnected | **15%** |
| Engineering automation | One-click generation | Strong CLI + evolution pipeline | **60%** |
| **OVERALL** | | | **~18-20%** (was ~19-22%) |

## Biggest Remaining Gaps

1. **Architecture Integration Gaps (P1):** The `level` (WFC), `shader`, and `export` modules exist in the codebase but are completely disconnected from the main `AssetPipeline`.
2. **Production Benchmark Suite:** No formal benchmark characters/tiles/VFX with acceptance thresholds.
3. **Visual Quality Gap:** SDF-based rendering is still tech-demo level compared to hand-drawn or AI-generated pixel art.
4. **VFX/Particle Physics Binding:** Particles exist but are not bound to character actions or physics events.
5. **Advanced Simulation Research (P1-RESEARCH-30A/B/C):** Three frontier research directions queued for deep protocol study: (A) Metabolic fatigue engine (ATP/lactate → torque reduction + ControlNet body temperature), (B) MPM phase change (snow/mud terrain interaction → 2D material masks for ComfyUI), (C) Reaction-diffusion thermodynamics (rust/fire/poison texture evolution → diffusion model guidance maps).

## Pending Tasks (Priority Order)

### P0 — Critical

| ID | Task | Status | Effort | Description |
|----|------|--------|--------|-------------|
| P0-DISTILL-1 | Global Distillation Bus (The Brain) | TODO | High | Wire `RuleCompiler` to automatically inject `ParameterSpace` into all modules at runtime |

### P1 — Important

| ID | Task | Status | Effort | Description |
|----|------|--------|--------|-------------|
| P1-DISTILL-3 | Distill Verlet & Gait Parameters | TODO | Medium | Research and distill physics stability and procedural gait parameters into `knowledge/` |
| P1-DISTILL-4 | Distill Cognitive Science Rules | TODO | Medium | Research and distill phase relationships and biological motion perception rules |
| P1-AI-1 | Math-to-AI Pipeline Prototype | TODO | Medium | Export skeleton/pose data as ControlNet inputs for external AI diffusion models |
| P1-ARCH-1 | WFC tilemap pipeline integration | TODO | High | Add `produce_level()`, connect WFC to asset packs |
| P1-ARCH-2 | Export pipeline integration | TODO | High | Promote exporter to first-class stage |
| P1-ARCH-3 | Shader pipeline integration | TODO | Medium | Wire `ShaderCodeGenerator` into `AssetPipeline` |
| P1-VFX-1 | Physics-driven Particle System | TODO | Medium | Upgrade SDF VFX to use emitters, gravity, and collision |
| P1-NEW-9C | Character evolution 3.0: expand part registry | TODO | Medium | More slot types: torso overlays, hand items, foot accessories |
| P1-NEW-10 | Production benchmark asset suite | TODO | High | Benchmark characters/tiles/VFX with acceptance thresholds |
| P1-2 | Per-frame SDF parameter animation | TODO | Medium | Keyframed SDF parameter tracks |
| **P1-RESEARCH-30A** | **Metabolic Engine: ATP/Lactate Fatigue Model** | **TODO (Research Protocol)** | **High** | **Research-driven:** ATP consumption + lactate accumulation math model. When lactate threshold exceeded, reduce joint max torque → motion becomes heavy/staggering. Output "body temperature" parameter for ControlNet conditioning (flushed skin, sweat highlights). **Masters:** Scott Delp (Stanford, OpenSim). **Keywords:** Metabolic Energy Cost in Character Animation, Biomechanical Simulation of Muscle Fatigue. |
| **P1-RESEARCH-30B** | **MPM & Phase Change Simulation** | **TODO (Research Protocol)** | **High** | **Research-driven:** Material Point Method for terrain interaction — snow compaction/melting, mud drying/hardening. Compute material state as 2D mask for ComfyUI (wet/dry regions). **Masters:** Yuanming Hu (Taichi), Chenfanfu Jiang (UCLA, Hollywood MPM). **Keywords:** Differentiable Material Point Method (MPM) for Phase Change. |
| **P1-RESEARCH-30C** | **Reaction-Diffusion & Thermodynamics for Surface Chemistry** | **TODO (Research Protocol)** | **High** | **Research-driven:** Turing patterns / Navier-Stokes for chemical texture evolution — rust on armor (acid rain), fire spread on cloth, poison diffusion on skin. Output texture evolution maps as diffusion model guidance. **Masters:** Ron Fedkiw (Stanford, ILM, Oscar winner), Nils Thuerey (TUM, deep learning + physics). **Keywords:** Reaction-Diffusion Systems in Computer Graphics, Physically Based Combustion Simulation. Supersedes P2-3 (basic reaction-diffusion). |

### P2 — Nice to Have

| ID | Task | Status | Effort |
|----|------|--------|--------|
| P2-1 | Sub-pixel rendering | TODO | Medium |
| P2-2 | Production benchmark asset suite | TODO | High |
| P2-3 | Reaction-diffusion textures | TODO | Medium |
| P2-4 | Test coverage for missing modules | TODO | Medium |

### P3 — Future

| ID | Task | Status | Effort |
|----|------|--------|--------|
| P3-1 | Auto knowledge distillation | PARTIAL | Medium |
| P3-2 | Web preview UI | TODO | High |
| P3-3 | Unity/Godot exporter plugin | TODO | Medium |

## Completed Tasks

### SESSION-029

| ID | Task | Result |
|----|------|--------|
| P0-MOTION-3 | FABRIK IK Solver & Procedural Gait | **DONE** — FABRIKGaitGenerator with walk/run cycles, parabolic swing arc, IPM CoM bounce, integrated into pipeline |
| P0-MOTION-7 | ZMP/CoM Balance Analysis | **DONE** — ZMPAnalyzer with CoM computation, support polygon, stability scoring, balance penalty |
| P0-MOTION-8 | Inverted Pendulum Model Integration | **DONE** — InvertedPendulumModel with LIPM trajectory, vertical bounce, lateral sway, walk CoM generation |
| P0-MOTION-9 | Enhanced Skating Cleanup (Calculus-based) | **DONE** — SkatingCleanupCalculus with finite-difference velocity, Hermite blending, position lock corrections |
| INTEGRATE-029 | BiomechanicsProjector pipeline integration | **DONE** — Unified projector in `produce_character_pack()` with per-frame corrections |
| RESEARCH-029 | Biomechanics deep research (MIT Underactuated, ZMP, LIPM, FABRIK) | **DONE** — 4-direction research synthesis |
| AUDIT-029 | Self-audit: research-to-code traceability | **DONE** — All 4 directions verified with traceability matrix |
| VALIDATION-029 | Full repository validation | **DONE** — 655/655 PASS (57 new + 598 existing) |

### SESSION-028-SUPP

| ID | Task | Result |
|----|------|--------|
| P0-MOTION-6 | Foot Contact Detection & Skating Cleanup | **DONE** — ContactDetector + ConstraintBlender + FootLockingConstraint + PhysDiffProjectionScheduler |
| ENHANCE-028S-1 | Enhanced physics penalty (skating/penetrate/float) | **DONE** — 3 new penalty terms in `compute_physics_penalty()` |
| ENHANCE-028S-2 | AnglePoseProjector foot locking integration | **DONE** — `step()` auto-applies contact→blend→IK pipeline when skeleton_ref provided |
| ENHANCE-028S-3 | Pipeline skeleton pass-through | **DONE** — `produce_character_pack()` passes skeleton to projector |
| AUDIT-028S | PhysDiff alignment audit | **DONE** — 8 items confirmed, 7 gaps identified and closed |
| VALIDATION-028S | Supplement validation | **DONE** — 596/596 PASS (36 new + 560 existing) |

### SESSION-028

| ID | Task | Result |
|----|------|--------|
| P0-MOTION-1 | Verlet Integration Physics Engine | **DONE** — `PositionPhysicsProjector` with Verlet + PBD + distance/ground constraints |
| P0-MOTION-2 | Mass-Spring Secondary Animation | **DONE** — Angular spring-damper per joint with 17 configurable profiles |
| P0-MOTION-4 | Easing Functions & Motion Curves | **DONE** — 10 Robert Penner easing functions in `PENNER_EASING_FUNCTIONS` |
| P0-MOTION-5 | Cognitive Motion Constraints | **DONE** — Anticipation, follow-through, overlapping action, squash/stretch |
| P0-DISTILL-2 | Cognitive Constraints → Fitness Functions | **DONE** — `compute_physics_penalty()` with smoothness/ROM/symmetry/energy |
| P1-NEW-3 | Spring-based secondary animation | **DONE** — Per-joint follow-through and overlap in `AnglePoseProjector` |
| RESEARCH-028 | PhysDiff/PINNs/differentiable physics research | **DONE** — 6-dimension parallel research synthesis |
| VALIDATION-028 | Full repository validation | **DONE** — 562/562 PASS |

### SESSION-027

| ID | Task | Result |
|----|------|--------|
| P1-NEW-9B | Character evolution 2.5: semantic genotype system | **DONE** — CharacterGenotype with archetypes, body templates, part registry, 3-layer mutation, crossover |
| PROTOCOL-027A | Precision research protocol enhancement | Enhanced query construction rules, dimension selection, post-search synthesis |
| RESEARCH-027 | Semantic mutation space research | 5-dimension parallel research → implementation synthesis |
| AUDIT-027 | Full project audit and gap analysis | **DONE** |

### SESSION-026

| ID | Task | Result |
|----|------|--------|
| P0-PROTOCOL-026A | Repository precision parallel research protocol | Added `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` |
| P0-PROTOCOL-026B | Session workflow integration | `SESSION_PROTOCOL.md` now triggers the research protocol by default |
| P0-PROTOCOL-026C | Reusable skill validation | `mathart-precision-research` skill validated |

### Earlier Sessions (024-025)

| ID | Task | Result |
|----|------|--------|
| P1-NEW-9B-FOUNDATION | Character evolution 2.0 foundation | Silhouette/state-distinction scoring, elite diversity, adaptive strength, restart recovery |
| P1-RESEARCH-025A | Dedup-first parallel gap research | New non-duplicative references distilled |
| P1-RESEARCH-025B | TODO and priority refresh | Priorities narrowed and actionable |

## SESSION-029 Deliverables

| Category | Change | File(s) | Impact |
|----------|--------|---------|--------|
| **CORE** | Biomechanics engine module | `mathart/animation/biomechanics.py` | ZMPAnalyzer + InvertedPendulumModel + SkatingCleanupCalculus + FABRIKGaitGenerator + BiomechanicsProjector (~1,544 lines) |
| **CORE** | Pipeline biomechanics integration | `mathart/pipeline.py` | `produce_character_pack()` applies biomechanics projection after physics projection |
| **API** | Biomechanics public exports | `mathart/animation/__init__.py` | 11 new public symbols |
| **API** | CharacterSpec biomechanics fields | `mathart/pipeline.py` | `enable_biomechanics`, `biomechanics_zmp`, `biomechanics_ipm`, `biomechanics_skating_cleanup`, `biomechanics_zmp_strength` |
| **TEST** | Biomechanics test suite | `tests/test_biomechanics.py` | 57 tests covering all four research directions + pipeline integration |
| **AUDIT** | Self-audit report | `SESSION_029_AUDIT.md` | Research-to-code traceability matrix |
| **STATE** | Updated project memory | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` | Reflects SESSION-029 completion |

## Instructions for Next AI Session

1. **Read `DEDUP_REGISTRY.json` first.**
2. **Read `SESSION_PROTOCOL.md` second.**
3. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` third.**
4. Read `PROJECT_BRAIN.json`, `PHYSICS_ANIMATION_UPGRADE_PLAN.md`, and this handoff before coding.
5. The physics + biomechanics engine is now **fully delivered and integrated**. The animation pipeline has ZMP balance, IPM locomotion, skating cleanup, and FABRIK procedural gait.
6. If the goal is better final character art quality, start with **P1-NEW-9C** (expand part registry) or **P1-NEW-10** (production benchmarks).
7. If the goal is end-to-end production usefulness, start with **P1-ARCH-1**, **P1-ARCH-2**, or **P1-ARCH-3**.
8. If motion quality tuning is the next focus, start with **P1-DISTILL-3** (physics parameter distillation) or **P1-VFX-1** (physics-driven particles).
9. If frontier simulation research is the goal, start with **P1-RESEARCH-30A** (metabolic fatigue), **P1-RESEARCH-30B** (MPM phase change), or **P1-RESEARCH-30C** (reaction-diffusion thermodynamics). These require the **Deep Reading Protocol** from `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`.
10. Always update this file and `PROJECT_BRAIN.json` before ending.

## Quick Start

```python
from mathart.pipeline import AssetPipeline, CharacterSpec

pipeline = AssetPipeline(output_dir="output/", seed=7)

# SESSION-029: Full physics + biomechanics character pack (default)
character = pipeline.produce_character_pack(
    CharacterSpec(
        name="mario_biomechanics",
        preset="mario",
        frames_per_state=8,
        states=["idle", "run", "jump", "fall", "hit"],
        enable_physics=True,         # Default: True
        enable_biomechanics=True,    # Default: True (SESSION-029)
        biomechanics_zmp=True,       # ZMP balance correction
        biomechanics_ipm=True,       # IPM vertical bounce
        biomechanics_skating_cleanup=True,  # Foot skating elimination
        biomechanics_zmp_strength=0.3,      # ZMP correction strength
    )
)

# Physics only (no biomechanics)
character_physics_only = pipeline.produce_character_pack(
    CharacterSpec(
        name="mario_physics",
        preset="mario",
        enable_physics=True,
        enable_biomechanics=False,
        frames_per_state=8,
        states=["idle", "run", "jump"],
    )
)

# Legacy mode (no physics, no biomechanics)
character_legacy = pipeline.produce_character_pack(
    CharacterSpec(
        name="mario_legacy",
        preset="mario",
        enable_physics=False,
        enable_biomechanics=False,
        frames_per_state=6,
        states=["idle", "run", "jump"],
    )
)

# Standalone biomechanics analysis
from mathart.animation.biomechanics import ZMPAnalyzer, BiomechanicsProjector
from mathart.animation.skeleton import Skeleton

skeleton = Skeleton.create_humanoid(head_units=3.0)
analyzer = ZMPAnalyzer(skeleton)
result = analyzer.analyze_frame({"spine": 0.1, "l_hip": 0.3})
print(f"ZMP: {result.zmp_x:.3f}, Balanced: {result.is_balanced}, Score: {result.stability_score:.2f}")
```

---
*Auto-generated by SESSION-029-SUPP at 2026-04-17T00:30:00Z*
