# MarioTrickster-MathArt — Session Handoff

> **READ THIS FIRST** if you are starting a new conversation about this project.  
> This document is auto-generated and always reflects the latest project state.

## Project Overview

| Field | Value |
|---|---|
| Current version | **0.37.0** |
| Last updated | **2026-04-17** |
| Last session | **SESSION-046** |
| Best quality score achieved | **0.867** |
| Total iterations run | **500+** |
| Total code lines | **~63,100** |
| Latest validation status | **6 new tests PASS (Gap C2); targeted Stable Fluids audit PASS; zero local regressions in touched modules** |

## What SESSION-046 Delivered

SESSION-046 closes **Gap C2: 物理驱动的粒子特效（Stable Fluids / Grid-Based Vector Fields）** by implementing a repository-native **二维稳定流体烟雾系统**，把 VFX 从“发射器初速度粒子”升级为“动作速度驱动的网格矢量场 + 流体引导粒子”工作流。[1][2][3]

### Core Insight

> 挥剑和冲刺不再依赖手工序列帧。角色的物理速度被注入二维流体网格，烟雾密度和粒子在不可压速度场中被平流与卷曲，因而可以自然形成绕体、回卷、拖尾和旋涡感。

### New Subsystems

1. **Stable Fluids VFX Module (`mathart/animation/fluid_vfx.py`)**  
   实现 `FluidGrid2D`、`FluidDrivenVFXSystem`、`FluidVFXConfig`、`FluidImpulse`、obstacle mask 和 fluid-guided particles。求解流程采用 **semi-Lagrangian advection + implicit diffusion + projection**，并保留 ghost boundary cells 与内部障碍物支持。

2. **Pipeline Integration (`mathart/pipeline.py`)**  
   `AssetPipeline.produce_vfx()` 新增三种流体预设：`smoke_fluid`、`dash_smoke`、`slash_smoke`。同时新增 `obstacle_mask` 与 `driver_impulses` 输入接口，为未来把真实角色 silhouette、UMR root velocity、武器轨迹等信号直接绑定到流体驱动层预留了稳定接入口。

3. **Three-Layer Evolution Bridge (`mathart/evolution/fluid_vfx_bridge.py`)**  
   新增 `FluidVFXEvolutionBridge`，将 Gap C2 接入正式三层循环：  
   - **Layer 1**：评估 flow energy、obstacle leak ratio、particle activity、alpha coverage；  
   - **Layer 2**：把成功/失败经验蒸馏为 `knowledge/fluid_vfx_rules.md`；  
   - **Layer 3**：持久化 `.fluid_vfx_state.json`，并将状态写回 `PROJECT_BRAIN.json` 与 `SelfEvolutionEngine.status()`。

4. **Distillation Registry Upgrade (`mathart/evolution/evolution_loop.py`)**  
   新增 **3 条 Gap C2 distillation records**，分别对应 Jos Stam 的 *Stable Fluids*、*Real-Time Fluid Dynamics for Games*，以及仓库内部的三层桥接实现。

5. **Documentation and Audit**  
   新增 `docs/research/GAP_C2_STABLE_FLUIDS_VFX.md`，并生成 `evolution_reports/session046_gapc2_audit.json` 与演示产物目录 `evolution_reports/session046_gapc2_audit_assets/`。

## Research References Now Landed in Code

| Reference | Core idea | Landed in |
|---|---|---|
| **Jos Stam — Stable Fluids** (SIGGRAPH 1999) | 稳定流体分解：加源、扩散、半拉格朗日平流、压力投影 | `FluidGrid2D` in `mathart/animation/fluid_vfx.py` |
| **Jos Stam — Real-Time Fluid Dynamics for Games** | 游戏级最小实现：`dens_step`、`vel_step`、`project`、ghost cells、边界条件 | `FluidGrid2D._velocity_step()` / `_density_step()` / `_project()` |
| **工程实现经验（共点 2D 网格）** | 先用 NumPy colocated grid 快速落地，再按效果决定是否升级更复杂网格 | `FluidVFXConfig`, `FluidDrivenVFXSystem` |
| **项目内部 Gap C2 蒸馏** | 流体能量、障碍物泄漏和粒子活跃度进入三层演化状态机 | `FluidVFXEvolutionBridge` |

## Knowledge Base Status

| Metric | Status |
|---|---|
| Distilled knowledge rules | **52+** |
| Distillation records | **17** (including 3 new Gap C2 records) |
| Math models registered | **28** |
| Sprite references | **0** |
| Next distill session ID | **DISTILL-005** |
| Next mine session ID | **MINE-001** |

知识层新增 `knowledge/fluid_vfx_rules.md`，现已覆盖 phase、contract、visual regression、analytical rendering、neural rendering、fluid VFX 等多个关键域。

## Three-Layer Evolution System Status

### Layer 1: Internal Evolution

Gap C2 现已拥有自己的内部质量门。流体特效会记录 **mean_flow_energy**、**max_flow_speed**、**obstacle_leak_ratio**、**active_particles** 和 **visual alpha coverage**，从而把“看起来像不像会流动的烟雾”转化为可复用的可测量指标。

### Layer 2: External Knowledge Distillation

外部知识蒸馏已将 Stable Fluids 的核心求解结构与游戏版最小实现映射到仓库代码，并且通过 `GAPC2_DISTILLATIONS` 固化到演化注册表中。后续若继续输入新的论文、角色障碍物方案或游戏 VFX 资料，系统可在此基础上继续内化，而不是重新从头研究。

### Layer 3: Self-Iteration

Layer 3 现在不只管 physics、contract、visual regression 和 neural rendering，也开始追踪 **fluid VFX state**。当前持久化文件为 `.fluid_vfx_state.json`，关键指标包括：  
- total cycles  
- total passes / failures  
- best flow energy  
- lowest obstacle leak ratio  
- consecutive passes  
- knowledge rules total

## Pending Tasks (Priority Order)

### HIGH (P0/P1)
- `P0-GAP-2`: Rigid Body/Soft Body Coupling (XPBD integration)
- `P0-DISTILL-1`: Global Distillation Bus (The Brain)
- `P1-GAP4-BATCH`: Batch-tune multiple hard transitions through active closed loop
- `P1-E2E-COVERAGE`: Expand E2E tests to include MV export regression and temporal consistency validation
- `P1-INDUSTRIAL-34A`: Wire industrial renderer as optional backend inside AssetPipeline
- `P1-INDUSTRIAL-44A`: Add engine-ready export templates for albedo/normal/depth/mask/flow packs
- `P1-PHASE-37A`: Scene-aware distance matching sensors (raycast/terrain)
- `P1-INDUSTRIAL-34C`: 3D-to-2D mesh rendering path (Dead Cells full workflow)
- `P1-PHASE-33A`: Gait transition blending (walk/run/sneak)
- `P1-PHASE-33B`: Terrain-adaptive phase modulation
- `P1-NEW-10`: Production benchmark asset suite
- `P1-VFX-1A`: Bind real character silhouette masks into fluid VFX obstacle grids
- `P1-VFX-1B`: Drive fluid VFX directly from UMR root velocity and weapon trajectories

### MEDIUM (P1/P2)
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

### DONE
- `P0-GAP-1`: Incomplete Phase Backbone — CLOSED in SESSION-042.
- `P0-EVAL-BRIDGE`: Parameter Convergence Bridge / Layer 3 write-back loop — CLOSED in SESSION-043.
- `P0-GAP-C1`: Analytical SDF normal/depth auxiliary-map pipeline — CLOSED in SESSION-044.
- `P1-AI-2`: Neural Rendering Bridge (Gap C3 / 防闪烁终极杀器) — CLOSED in SESSION-045.
- `P1-VFX-1`: **Physics-driven Particle System / Stable Fluids VFX — CLOSED in SESSION-046** via `fluid_vfx.py`, `fluid_vfx_bridge.py`, pipeline preset integration, audit artifacts, and dedicated tests.

## Audit and Verification Evidence

| Evidence | Result |
|---|---|
| `python3.11 -m pytest -q tests/test_fluid_vfx.py` | **6/6 PASS** |
| `evolution_reports/session046_gapc2_audit.json` | smoke_fluid / dash_smoke / slash_smoke all generated successfully |
| `docs/research/GAP_C2_STABLE_FLUIDS_VFX.md` | comprehensive research synthesis |
| `.fluid_vfx_state.json` | bridge state persisted |
| `knowledge/fluid_vfx_rules.md` | rule distillation persisted |
| `SelfEvolutionEngine.status()` | Gap C2 bridge visible in formal status panel |

## Recent Evolution History (Last 8 Sessions)

### SESSION-046 — v0.37.0 (2026-04-17)
- **Gap C2 closure**: Stable Fluids physics-driven particle VFX
- Added `mathart/animation/fluid_vfx.py` with `FluidGrid2D`, `FluidDrivenVFXSystem`, obstacle masks, and fluid-guided particles
- `AssetPipeline.produce_vfx()` now supports `smoke_fluid`, `dash_smoke`, `slash_smoke`, plus `obstacle_mask` and `driver_impulses`
- Added `mathart/evolution/fluid_vfx_bridge.py` and integrated it into engine status + PROJECT_BRAIN write-back
- Registered 3 new Gap C2 distillation records and generated audit artifacts
- 6 new tests all PASS

### SESSION-045 — v0.36.0 (2026-04-17)
- Gap C3 closure: Neural rendering bridge / 防闪烁终极杀器
- Ground-truth motion vector baker from procedural FK with SDF-weighted skinning
- Three encoding formats: RGB (128-neutral), HSV (direction visualization), Raw float32
- EbSynth project export with frames + flow + keyframes + metadata
- Neural rendering evolution bridge: temporal consistency gate + knowledge distillation + fitness integration
- 5 distillation records and 37 targeted tests PASS

### SESSION-044 — v0.35.0 (2026-04-17)
- Gap C1 closure: analytical SDF normal/depth/mask export pipeline
- Industrial renderer upgraded to export albedo + auxiliary maps from the same distance field
- Three-layer evolution loop now tracks analytical rendering status and provenance

### SESSION-043 — v0.34.0 (2026-04-16)
- Gap 4 closure: active Layer 3 runtime closed loop
- Optuna-based bounded search for runtime transition tuning
- Real `run->jump` rule distilled into repository state

### SESSION-042 — v0.33.0 (2026-04-16)
- Gap 1 closure: Generalized Phase State (`PhaseState`) and Gate Mechanism
- Three-Layer Evolution Loop (`evolution_loop.py`)

### SESSION-041 — v0.32.0 (2026-04-16)
- Gap 3 closure: end-to-end reproducibility and visual regression pipeline

### SESSION-040 — v0.31.0 (2026-04-16)
- CLI Pipeline Contract and end-to-end determinism

### SESSION-039 — v0.30.0
- Inertialized transition synthesis and runtime motion matching query

## Custom Notes

- **session046_gapc2_status**: CLOSED. Stable Fluids / grid-based vector-field VFX implemented.
- **session046_fluid_module**: `mathart/animation/fluid_vfx.py` adds `FluidGrid2D`, `FluidDrivenVFXSystem`, obstacle masks, and fluid-guided particles.
- **session046_fluid_bridge**: `mathart/evolution/fluid_vfx_bridge.py` implements three-layer evaluation, rule distillation, and persistent state tracking.
- **session046_pipeline_presets**: `AssetPipeline.produce_vfx()` now supports `smoke_fluid`, `dash_smoke`, `slash_smoke` with optional `obstacle_mask` and `driver_impulses`.
- **session046_test_count**: 6 new tests, all PASS.
- **session046_audit**: `evolution_reports/session046_gapc2_audit.json` confirms research → code → artifact → test closure.
- **session046_artifacts**: `evolution_reports/session046_gapc2_audit_assets/` contains generated smoke_fluid, dash_smoke, and slash_smoke demo packs.

## Instructions for Next AI Session

1. Read `PROJECT_BRAIN.json` for the machine-readable state.
2. Read `SESSION_HANDOFF.md` and `GLOBAL_GAP_ANALYSIS.md` for the human-readable overview.
3. Read `docs/research/GAP_C2_STABLE_FLUIDS_VFX.md` before modifying fluid VFX behavior.
4. Inspect `mathart/animation/fluid_vfx.py`, `mathart/evolution/fluid_vfx_bridge.py`, and `mathart/pipeline.py::produce_vfx()` before extending slash/dash smoke behavior.
5. If the next task concerns combat VFX, prefer feeding real body masks into `obstacle_mask` instead of adding new sequence frames.
6. If the next task concerns runtime coupling, derive `driver_impulses` from UMR root transforms, motion vectors, or weapon trajectories rather than inventing hand-tuned emitters.
7. Preserve SESSION-043 runtime closed-loop behavior unless the task explicitly targets transition tuning.
8. Preserve SESSION-044 analytical SDF rendering behavior unless the task explicitly targets auxiliary maps.
9. Preserve SESSION-045 motion-vector conditioning path unless the task explicitly targets temporal-consistency changes.
10. Always rerun the relevant targeted tests before pushing.
11. Push changes to GitHub after task completion.

## References

[1]: https://pages.cs.wisc.edu/~chaol/data/cs777/stam-stable_fluids.pdf "Jos Stam — Stable Fluids (SIGGRAPH 1999)"
[2]: https://graphics.cs.cmu.edu/nsp/course/15-464/Fall09/papers/StamFluidforGames.pdf "Jos Stam — Real-Time Fluid Dynamics for Games"
[3]: https://github.com/ohjay/stable_fluids "Engineering reference implementation for Stable Fluids"
[4]: https://dcgi.fel.cvut.cz/~sykorad/Jamriska19-SIG.pdf "Jamriška et al. — Stylizing Video by Example (SIGGRAPH 2019)"
[5]: https://obvious-research.github.io/onlyflow/ "Koroglu et al. — OnlyFlow: Optical Flow based Motion Conditioning (CVPR 2025W)"
[6]: https://motionprompt.github.io/ "Nam et al. — MotionPrompt: Optical Flow Guided Prompt Optimization (CVPR 2025)"

---
*Auto-generated by SESSION-046 at 2026-04-17*
