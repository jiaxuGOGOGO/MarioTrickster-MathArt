# SESSION_HANDOFF

> **READ THIS FIRST** if you are starting a new conversation about this project.  
> This document has been refreshed for **SESSION-052**.

## Project Overview

| Field | Value |
|---|---|
| Current version | **0.43.0** |
| Last updated | **2026-04-17** |
| Last session | **SESSION-052** |
| Best quality score achieved | **0.867** |
| Total iterations run | **500+** |
| Total code lines | **~73,636** |
| Latest validation status | **14/14 XPBD tests PASS; Physics Singularity (P0-GAP-2 + P1-B1-2) CLOSED** |

## What SESSION-052 Delivered

SESSION-052 executes the **first-priority battle: reforging the high-fidelity physics foundation (The Physics Singularity)**. It replaces the Jakobsen-based one-way visual-following system with a full **XPBD (Extended Position-Based Dynamics)** solver featuring true two-way rigid-soft coupling, spatial-hash self-collision, and a three-layer evolution loop. This directly closes **P0-GAP-2** (full two-way rigid-soft XPBD coupling) and **P1-B1-2** (volumetric contact and self-collision awareness). [1] [2] [3] [4]

### Core Insight

> 如果底层受力是假的（基于 Jakobsen 的单向视觉跟随），后续 AI 润色出来的帧也只是"没有灵魂的纸片"。SESSION-052 的关键突破是：用 XPBD 的 **柔顺度（Compliance, α）** 彻底替代弹簧刚度，将刚体 CoM 与柔体节点放入 **同一个求解池**，通过 **逆质量权重** 自动产生牛顿第三定律的反向冲量——角色挥舞重武器时必须产生真实的踉跄与受力代偿。

### New Subsystems

1. **XPBD Core Solver (`mathart/animation/xpbd_solver.py`, ~490 lines)**
   - Full XPBD algorithm: predict → sub-step → constraint solve (NPGS) → velocity update
   - Compliance α̃ = α/Δt² decoupling (iteration/timestep independent)
   - Lagrange multiplier accumulation for force estimation (f ≈ λ/Δt)
   - Distance, bending, attachment, ground, and self-collision constraint types
   - Two-way rigid-soft coupling: rigid CoM as particle with w = 1/m_body
   - Rayleigh damping via compliance-like parameter β (Eq 26 from XPBD paper)
   - Velocity clamping for tunnelling prevention
   - Coulomb friction model for collision response
   - Chain builder with configurable presets (cape, hair, weapon, tail)

2. **Spatial Hash Collision System (`mathart/animation/xpbd_collision.py`, ~260 lines)**
   - `SpatialHashGrid`: O(1) amortised neighbour queries via hash table
   - `BodyCollisionProxy`: body-part collision spheres from skeleton FK
   - `XPBDCollisionManager`: generates ground, body-contact, and self-collision constraints per frame
   - Connectivity-aware exclusion to prevent constraint fighting between adjacent chain nodes

3. **XPBD Bridge (`mathart/animation/xpbd_bridge.py`, ~230 lines)**
   - `XPBDChainProjector`: drop-in replacement for `SecondaryChainProjector`
   - Identical `step_frame()` / `project_frame_sequence()` interface
   - Exposes XPBD-specific diagnostics: reaction impulse, CoM displacement, energy estimate
   - Backward compatible with existing pipeline.py and frame export

4. **Three-Layer Evolution Loop (`mathart/animation/xpbd_evolution.py`, ~580 lines)**
   - **Layer 1 — Internal Evolution (`InternalEvolver`)**: monitors constraint errors, energy drift, velocity, collision counts; auto-tunes sub-steps, iterations, compliance, damping with cooldown
   - **Layer 2 — External Knowledge Distillation (`KnowledgeDistiller`)**: 6 foundational entries from XPBD/Müller papers pre-seeded; dynamic injection API; JSON persistence
   - **Layer 3 — Self-Iterating Test (`PhysicsTestHarness`)**: 7 physics scenario tests (free fall, pendulum energy, two-way coupling, constraint stability, self-collision separation, heavy weapon stagger, velocity clamping)
   - **Orchestrator (`XPBDEvolutionOrchestrator`)**: ties all three layers into a closed feedback loop: Test → Diagnose → Tune/Distill → Re-test

5. **Comprehensive Test Suite (`tests/test_xpbd_physics.py`, ~340 lines)**
   - 14 independent tests covering solver creation, particle management, distance constraints, compliance decoupling, two-way coupling, Lagrange force estimation, spatial hashing, chain building, evolution orchestrator, knowledge distiller, internal evolver, physics test harness, state persistence, and full evolution cycles
   - All 14 tests PASS

6. **Audit Report (`docs/SESSION-052-AUDIT.md`)**
   - Complete research-to-code traceability matrix
   - Three-layer evolution audit
   - Physics realism score improvement estimate: 12/100 → ~45/100

## Research References Now Landed in Code

| Reference | Core idea | Landed in |
|---|---|---|
| **Macklin & Müller, XPBD (SIGGRAPH 2016)** [1] | Compliance α replaces stiffness k; α̃ = α/Δt² decouples from timestep/iterations; Lagrange multiplier accumulation for force estimation | `xpbd_solver.py` core algorithm, `_solve_distance_constraint()` |
| **Müller et al., Detailed Rigid Body Simulation (SCA 2020)** [2] | Rigid CoM as XPBD particle with w=1/m; unified solve pool; NPGS immediate position update; inverse-mass reaction impulse | `xpbd_solver.py` two-way coupling, `ParticleKind.RIGID_COM` |
| **Matthias Müller, Ten Minute Physics** [3] | Array-based XPBD in ~100 lines; spatial hashing; self-collision 5-trick recipe | `xpbd_solver.py` array layout, `xpbd_collision.py` spatial hash |
| **Carmen Cincotti, XPBD Self-Collision Tutorial** [4] | Practical self-collision implementation with spatial hash, rest-length guard, friction | `xpbd_collision.py` self-collision generation |

## Runtime Evidence from SESSION-052

| Metric | Result |
|---|---|
| Total XPBD tests | **14** |
| Tests passed | **14** |
| Two-way coupling CoM displacement | **1.63 units** |
| Heavy weapon reaction impulse | **143.05** |
| Heavy weapon CoM displacement | **3.67 units** |
| Compliance decoupling verified | **α=0→d=1.0, α=1e-3→d=1.01** |
| Self-collision separation | **0.2000 (target: ≥0.18)** |
| Velocity clamping | **20.00/20.0** |
| Constraint stability | **max error 0.000324 (threshold: 0.05)** |
| Physics test harness | **6/7 pass** |
| Evolution cycles completed | **3** |

## Knowledge Base Status

| Metric | Status |
|---|---|
| Distilled knowledge rules | **75+** |
| Distillation records | **31+** |
| Math models registered | **28** |
| XPBD knowledge entries | **6** (foundational, pre-seeded) |
| Sprite references | **0** |
| Next distill session ID | **DISTILL-005** |
| Next mine session ID | **MINE-001** |

知识层本轮新增 6 条 XPBD 基础知识条目（Compliance 解耦、Lagrange 累积、双向耦合逆质量、NPGS 即时更新、自碰撞 5 技巧、Rayleigh 阻尼），全部预装在 `KnowledgeDistiller` 中，可通过 JSON 持久化跨 session 复用。

## Three-Layer Evolution System Status

### Layer 1: Internal Evolution

XPBD 求解器现在拥有 `InternalEvolver`，实时监控 7 个诊断指标（约束误差、能量漂移、速度、碰撞计数等），并通过 7 种 `TuningAction`（增减子步/迭代、收紧/放松柔顺度、增减阻尼、调整速度上限）自动调参。冷却机制防止频繁调参振荡。

### Layer 2: External Knowledge Distillation

SESSION-052 已将 XPBD 论文的 **Compliance 解耦**、**Lagrange 乘子累积**、**逆质量双向耦合**、**NPGS 即时更新**、**自碰撞 5 技巧**、**Rayleigh 阻尼** 转化为项目内部知识条目。未来如果用户继续提供关于 FEM、MPM、GPU XPBD 或其他物理模拟的资料，`KnowledgeDistiller` 可以直接吸收并映射到求解器参数。

### Layer 3: Self-Iteration

Layer 3 新增 `PhysicsTestHarness`，包含 7 个物理场景测试。`XPBDEvolutionOrchestrator` 将三层绑定为闭环：测试失败自动触发 Layer 1 调参或标记 Layer 2 知识缺口。完整进化状态可序列化为 JSON。

## Pending Tasks (Priority Order)

### HIGH (P0/P1)
- `P1-E2E-COVERAGE`: **PARTIAL after SESSION-051**. Core graph-driven runtime coverage now exists; remaining work is to feed graph-generated sequences into `headless_e2e_ci.py` and expand runtime assets beyond `idle/walk/run/jump`.
- `P1-XPBD-1`: **NEW** — Free-fall test precision optimization (damping causes deviation from analytical g·t²/2)
- `P1-XPBD-2`: **NEW** — GPU-accelerated XPBD solver (reference: Müller Tutorial 16)
- `P1-DISTILL-1A`: Roll Runtime DistillBus into gait blending, locomotion scoring, and `compute_physics_penalty()` batch paths
- `P1-DISTILL-1B`: Add Taichi backend and benchmark suite for Runtime DistillBus
- `P1-GAP4-BATCH`: Batch-tune multiple hard transitions through active closed loop
- `P1-INDUSTRIAL-34A`: Wire industrial renderer as optional backend inside AssetPipeline
- `P1-INDUSTRIAL-44A`: Add engine-ready export templates for albedo/normal/depth/mask/flow packs
- `P1-INDUSTRIAL-34C`: 3D-to-2D mesh rendering path (Dead Cells full workflow)
- `P1-NEW-10`: Production benchmark asset suite
- `P1-B1-1`: Render visible cape/hair ribbons or meshes directly from XPBD chain snapshots
- `P1-VFX-1A`: Bind real character silhouette masks into fluid VFX obstacle grids
- `P1-VFX-1B`: Drive fluid VFX directly from UMR root velocity and weapon trajectories
- `P1-B2-1`: Add more terrain primitives (convex hull, Bézier curve, heightmap import)
- `P1-B2-2`: Extend TTC prediction to multi-bounce scenarios and moving platforms
- `P1-B3-1`: Integrate GaitBlender into `pipeline.py` gait switching path
- `P1-B3-2`: Add GaitBlender reference motions to RL environment (`rl_locomotion.py`)

### MEDIUM (P1/P2)
- `P1-XPBD-3`: **NEW** — 3D extension (current solver is 2D)
- `P1-XPBD-4`: **NEW** — Continuous Collision Detection (CCD) for fast-moving objects
- `P2-XPBD-5`: **NEW** — Cloth mesh simulation (current is 1D chain only)
- `P1-GAP4-CI`: Run active Layer 3 closed loop in scheduled/nightly audit mode
- `P1-INDUSTRIAL-44B`: Add analytic-gradient native primitives
- `P1-INDUSTRIAL-44C`: Export specular/roughness or engine-specific material metadata
- `P1-AI-2A`: Real-time EbSynth/ComfyUI integration demo with exported MV data
- `P1-AI-2B`: ControlNet conditioning pipeline using motion vector maps
- `P2-PHYSICS-DEFAULT`: Enforce Physics/Biomechanics defaults in CharacterSpec
- `P2-PHASE-CLEANUP`: Deprecate and remove legacy animation API surface
- `P1-PHASE-33C`: Animation preview / visualization tool
- `P1-DISTILL-3`: Distill Verlet & Gait Parameters
- `P1-DISTILL-4`: Distill Cognitive Science Rules
- `P1-B3-3`: Support asymmetric sync markers (limping, injured gaits)
- `P1-B3-4`: Support quadruped/multi-legged sync marker extensions
- `P1-B3-5`: Unify `transition_synthesizer.py` with `gait_blend.py` into complete transition pipeline

### DONE / CORE IMPLEMENTED
- `P0-GAP-2`: **Full two-way rigid-soft XPBD coupling — CLOSED in SESSION-052**
- `P1-B1-2`: **Volumetric contact and self-collision awareness — CLOSED in SESSION-052**
- `P0-DISTILL-1`: Global Distillation Bus (The Brain) — CLOSED in SESSION-050
- `P1-E2E-COVERAGE`: Core graph-based state-machine coverage implemented in SESSION-051; headless E2E rollout remains
- `P0-GAP-1`: Incomplete Phase Backbone — CLOSED in SESSION-042
- `P0-EVAL-BRIDGE`: Parameter Convergence Bridge / Layer 3 write-back loop — CLOSED in SESSION-043
- `P0-GAP-C1`: Analytical SDF normal/depth auxiliary-map pipeline — CLOSED in SESSION-044
- `P1-AI-2`: Neural Rendering Bridge (Gap C3 / 防闪烁终极杀器) — CLOSED in SESSION-045
- `P1-VFX-1`: Physics-driven Particle System / Stable Fluids VFX — CLOSED in SESSION-046
- `P1-GAP-B1`: Lightweight Jakobsen secondary chains for rigid-soft secondary animation — CLOSED-LITE in SESSION-047
- `P1-PHASE-37A`: Scene-Aware Distance Matching Sensors (SDF Terrain + TTC) — CLOSED in SESSION-048
- `P1-PHASE-33A`: Phase-Preserving Gait Transition Blending (Marker-based DTW) — CLOSED in SESSION-049

## Audit and Verification Evidence

| Evidence | Result |
|---|---|
| `python3 tests/test_xpbd_physics.py` | **14/14 PASS** |
| Two-way coupling CoM displacement | **1.63 units (verified Newton's Third Law)** |
| Heavy weapon stagger | **CoM displaced 3.67, reaction impulse 143.05** |
| Compliance decoupling | **α=0→d=1.0, α=1e-5→d=1.00, α=1e-3→d=1.01** |
| Self-collision separation | **0.2000 ≥ 0.18 threshold** |
| Velocity clamping | **20.00 ≤ 20.0 limit** |
| Constraint stability | **max error 0.000324 < 0.05 threshold** |
| Physics test harness (Layer 3) | **6/7 pass (free-fall deviation due to damping)** |
| Evolution cycles | **3 completed successfully** |
| `docs/SESSION-052-AUDIT.md` | Complete research-to-code traceability matrix |

## Recent Evolution History (Last 8 Sessions)

### SESSION-052 — v0.43.0 (2026-04-17)
- **Physics Singularity: P0-GAP-2 + P1-B1-2 CLOSED**
- Added `mathart/animation/xpbd_solver.py`: full XPBD solver with compliance decoupling, Lagrange multiplier accumulation, two-way rigid-soft coupling, NPGS
- Added `mathart/animation/xpbd_collision.py`: spatial hash grid, body collision proxies, self-collision constraint generation
- Added `mathart/animation/xpbd_bridge.py`: drop-in replacement for SecondaryChainProjector
- Added `mathart/animation/xpbd_evolution.py`: three-layer evolution loop (InternalEvolver, KnowledgeDistiller, PhysicsTestHarness, XPBDEvolutionOrchestrator)
- Added `tests/test_xpbd_physics.py`: 14 comprehensive tests, all PASS
- Added `docs/SESSION-052-AUDIT.md`: full research-to-code audit report
- Physics realism score estimate: 12/100 → ~45/100

### SESSION-051 — v0.42.0 (2026-04-17)
- **Gap D1 core implementation**: graph-based property fuzzing for runtime state-machine coverage
- Added `mathart/animation/state_machine_graph.py` with explicit state graph, coverage accounting, canonical walk, and runtime harness
- Added `mathart/evolution/state_machine_coverage_bridge.py` with three-layer evaluation, rule write-back, and persistent state
- Added `tests/test_state_machine_graph_fuzz.py`, `knowledge/state_machine_graph_fuzzing.md`, `.state_machine_coverage_state.json`, and `tools/run_state_machine_coverage_cycle.py`
- 5 new tests PASS; targeted regression batch 6 PASS, 1 SKIP; first accepted coverage cycle persisted

### SESSION-050 — v0.41.0 (2026-04-17)
- **Gap A2 closure**: Runtime Distillation Bus connected to runtime
- Added `mathart/distill/runtime_bus.py` with dense ParameterSpace lowering and Numba JIT runtime rule programs
- Added `mathart/evolution/runtime_distill_bridge.py` with three-layer evaluation, rule write-back, and persistent state
- Integrated compiled foot-contact rule path into `mathart/animation/physics_projector.py`
- Integrated global compiled constraint injection into `mathart/quality/controller.py`
- Added `knowledge/runtime_distill_bus.md`, `.runtime_distill_state.json`, `tools/run_runtime_distill_cycle.py`
- 5 new tests PASS; 118 targeted regression tests PASS

### SESSION-049 — v0.40.0 (2026-04-17)
- Gap B3 closure: Phase-Preserving Gait Transition Blending (Marker-based DTW)
- Added `mathart/animation/gait_blend.py` and `mathart/evolution/gait_blend_bridge.py`
- 54 new tests all PASS; 949 core tests PASS

### SESSION-048 — v0.39.0 (2026-04-17)
- Gap B2 closure: Scene-Aware Distance Sensor (SDF Terrain + TTC)
- Added `mathart/animation/terrain_sensor.py`
- 51 new tests all PASS; 895 total tests PASS

### SESSION-047 — v0.38.0 (2026-04-17)
- Gap B1-lite closure: Jakobsen lightweight rigid-soft secondary animation
- Added `mathart/animation/jakobsen_chain.py`
- 5 new tests all PASS

### SESSION-046 — v0.37.0 (2026-04-17)
- Gap C2 closure: Stable Fluids physics-driven particle VFX
- Added `mathart/animation/fluid_vfx.py`
- 6 new tests all PASS

### SESSION-045 — v0.36.0 (2026-04-17)
- Gap C3 closure: Neural rendering bridge / 防闪烁终极杀器
- Ground-truth motion vector baker from procedural FK with SDF-weighted skinning
- 37 targeted tests PASS

## Custom Notes

- **session052_physics_singularity**: P0-GAP-2 and P1-B1-2 CLOSED. Full XPBD solver with two-way rigid-soft coupling, spatial-hash self-collision, and three-layer evolution loop.
- **session052_xpbd_solver**: `mathart/animation/xpbd_solver.py` implements the complete XPBD algorithm from Macklin & Müller (2016) with compliance decoupling, Lagrange multiplier accumulation, and Rayleigh damping.
- **session052_two_way_coupling**: Rigid CoM as XPBD particle with w=1/m_body. Constraint corrections distribute proportionally to inverse masses, automatically producing Newton's Third Law reactions. Verified: heavy weapon (15kg) displaces 70kg CoM by 3.67 units with 143.05 reaction impulse.
- **session052_self_collision**: Spatial hash grid (O(1) queries) + connectivity-aware exclusion + Coulomb friction. Self-collision maintains 0.2000 separation (threshold: 0.18).
- **session052_evolution**: Three-layer loop: InternalEvolver (7 TuningActions with cooldown), KnowledgeDistiller (6 foundational entries, JSON persistence), PhysicsTestHarness (7 scenarios). Orchestrator runs closed feedback loop.
- **session052_test_count**: 14 new tests PASS; physics test harness 6/7 pass.
- **session052_audit**: `docs/SESSION-052-AUDIT.md` provides complete research-to-code traceability matrix.
- **session052_physics_score**: Estimated improvement from 12/100 to ~45/100.

## Instructions for Next AI Session

1. Read `PROJECT_BRAIN.json` for machine-readable state.
2. Read `SESSION_HANDOFF.md`, `docs/SESSION-052-AUDIT.md` before modifying XPBD code.
3. Inspect `mathart/animation/xpbd_solver.py` before changing constraint types, compliance formulas, or coupling logic.
4. Inspect `mathart/animation/xpbd_collision.py` before changing spatial hash, body proxies, or self-collision generation.
5. Inspect `mathart/animation/xpbd_evolution.py` before changing evolution loop, knowledge distillation, or test harness.
6. The `XPBDChainProjector` in `xpbd_bridge.py` is a drop-in replacement for `SecondaryChainProjector`; both can coexist.
7. To add new knowledge, use `KnowledgeDistiller.add_knowledge()` with a `KnowledgeEntry` specifying source, topic, insight, and parameter_effects.
8. To run the evolution cycle: `XPBDEvolutionOrchestrator().evolve()` returns an updated `XPBDSolverConfig`.
9. Preserve SESSION-051 state-machine coverage behavior unless the task explicitly targets it.
10. Preserve SESSION-050 Runtime DistillBus behavior unless the task explicitly targets runtime knowledge lowering.
11. Preserve SESSION-049 gait blending behavior unless the task explicitly targets cross-gait rollout.
12. Preserve SESSION-043 closed-loop tuning behavior unless the task explicitly targets its optimization policy.

## References

[1]: https://matthias-research.github.io/pages/publications/XPBD.pdf
[2]: https://matthias-research.github.io/pages/publications/PBDBodies.pdf
[3]: https://matthias-research.github.io/pages/tenMinutePhysics/index.html
[4]: https://carmencincotti.com/2022-11-21/cloth-self-collisions/
