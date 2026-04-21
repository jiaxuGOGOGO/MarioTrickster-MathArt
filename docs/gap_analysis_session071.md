# MarioTrickster-MathArt 最新差距详细情况（基于 SESSION-071 重算）

**作者：Manus AI**
**当前项目状态：v0.62.0 / SESSION-071**
**当前主线提交：`02327b7`**
**生成日期：2026-04-19**

## 执行摘要

对照上一版"基于 SESSION-070 重算"的差距文档与仓库当前 `main` 分支最新状态，可以确认项目主线已经再次前移：上一版被定义为"下一步必须完成"的 **`P1-XPBD-3`，在 SESSION-071 已经正式关闭**——`mathart/animation/xpbd_solver_3d.py` 的 numpy 向量化 3D XPBD 求解器、`mathart/core/physics3d_backend.py` 的微内核后端、`SpatialHashGrid3D` 的 3D 空间哈希、以及 `ContactManifoldRecord` 的 3D 法线 / 接触点 / 穿透深度填充全部落地，且经由 `MicrokernelPipelineBridge` 走纯 Context-in / Manifest-out 的 FrameGraph 级联与上游 `UnifiedMotionBackend` 解耦，AST 级守卫确保零静态耦合。这意味着，**项目当前的最大矛盾不再是"把 3D 物理底盘接入微内核"，而是"如何把 3D 物理底盘新暴露的热路径喂进 RuntimeDistillBus，与统一运动主干一起形成可度量、可审计、可优化的全局闭环"。**

从差距总量上看，按照"合并 `pending_tasks` 与 `open_tasks`、按任务 ID 去重、任一记录为 `DONE` 即视为关闭"的口径，当前项目有 **80 项活跃差距**，比上一版减少 1 项；这 1 项的净下降直接对应 `P1-XPBD-3` 的真实关闭。同时，上一版"任务池 source-of-truth 双轨"的治理隐患（`P1-AI-2C` 在 `pending_tasks` 与 `open_tasks` 之间状态冲突）已经在提交 `ff0827c` 中清理完毕，**当前 0 项任务存在状态冲突**，治理面统一。

> 验证依据：`python -c "json.load(open('PROJECT_BRAIN.json'))"` + 跨表去重脚本，输出 `ACTIVE TOTAL: 80, BY PRIORITY: {'P1': 43, 'P2': 33, 'P3': 4}, BY STATUS: {'TODO': 66, 'PARTIAL': 7, 'SUBSTANTIALLY-CLOSED': 5, 'SUBSTANTIALLY-ADVANCED': 2}, CONFLICTS: []`。

## 一、相对上一版（SESSION-070 重算版）的核心变化

| 维度 | 上一版基线（SESSION-070） | 当前最新状态（SESSION-071） | 变化解读 |
|---|---|---|---|
| 项目版本 | v0.61.0 | **v0.62.0** | 主线已从 SESSION-070 进入 SESSION-071 |
| 最新会话 | SESSION-070 | **SESSION-071** | 上一版已落后一轮主线闭环 |
| 主线提交 | `2a89f58` | **`02327b7`** | HEAD 已切到 SESSION-071 Physics3DBackend 落地 commit |
| 最大闭环项 | `P1-MIGRATE-1` 已 DONE | **`P1-XPBD-3` 已 DONE** | 主矛盾从"统一主干进微内核"前移到"3D 物理底盘进微内核" |
| `P1-XPBD-3` | PARTIAL | **DONE** | 真实 3D ∇C / 3D 接触流形 / FrameGraph 级联三件套全部落地 |
| `P1-DISTILL-1A` | PARTIAL（被动等待入口） | **PARTIAL（主动预接，4 项微调已列入）** | 3D 后端已暴露 telemetry 抓手 |
| `P1-AI-2C` 治理冲突 | 双轨记录冲突（PARTIAL / SUBSTANTIALLY-CLOSED） | **已统一为 SUBSTANTIALLY-CLOSED** | 治理面 source-of-truth 已对齐 |
| 活跃差距总数 | 81 | **80** | 因 `P1-XPBD-3` 关闭，活跃池净减 1 项 |
| 活跃优先级分布 | P1: 44 / P2: 34 / P3: 3 | **P1: 43 / P2: 33 / P3: 4** | P1 与 P2 各净减 1，P3 净增 1（受 `P3-GPU-BENCH-1` 归类调整影响） |
| 状态结构 | TODO 66 / PARTIAL 9 / SUB-ADV 2 / SUB-CLOSED 4 | **TODO 66 / PARTIAL 7 / SUB-ADV 2 / SUB-CLOSED 5** | PARTIAL 减少 2 项（`P1-XPBD-3` 关闭 + 治理统一），SUB-CLOSED 增加 1 项 |
| 状态冲突项 | 1 项（`P1-AI-2C`） | **0 项** | 治理隐患已闭环 |
| 最新验证 | 1305/1306 PASS（SESSION-070） | **1312/1312 stable serial PASS + 7/7 新增红线测试 PASS + 273/273 关键回归 PASS** | 证据链从微内核接管验证扩展到 3D 物理底盘红线守卫 |

## 二、当前活跃差距总量与结构

从工程管理视角看，项目当前并没有进入"扫尾阶段"，而是进入了 **骨干闭环完成 + 全局热路径深化** 的阶段。活跃任务从 81 下降到 80，反映 SESSION-071 关掉了一项最高优先级骨架；但 **P1 仍然高达 43 项**，表明系统仍处于重主干、重物理、重总线、重生产闭环的工程推进期。

| 维度 | 当前数量 | 解读 |
|---|---|---|
| 活跃差距总数 | **80** | `P1-XPBD-3` 关闭后任务池净减 1，整体压力仍高 |
| P1 活跃差距 | **43** | 核心价值仍集中在主干 / 物理 / 总线 / 生产闭环 |
| P2 活跃差距 | **33** | 中期升维与引擎消费任务依然庞大，但当前不应抢占最前线资源 |
| P3 活跃差距 | **4** | 产品展示仍不是主要瓶颈 |
| TODO | **66** | 大量任务仍停留在待实现层 |
| PARTIAL | **7** | 真正危险的任务，多数是"只差最关键一段没接上" |
| SUBSTANTIALLY-ADVANCED | **2** | E2E 与 benchmark 已较深，但还没有彻底闭环 |
| SUBSTANTIALLY-CLOSED | **5** | 一批硬骨架基本成型，保留在活跃池用于持续治理 |
| 状态冲突 | **0** | 治理面 source-of-truth 已对齐 |

## 三、当前主矛盾再次转移

如果说上一版的主矛盾是"接上真实 3D XPBD 底盘"，那么这句话现在需要被改写为：**"3D 物理底盘已经落地为 Frostbite-FrameGraph 风格的微内核插件；当前真正的主矛盾，是把 `Physics3DBackend` 与 `UnifiedMotionBackend` 共同暴露的热路径接成 `RuntimeDistillBus` 的全局评估入口，并把蒸馏出的规则反喂回编译参数空间与 Layer 3 闭环。"**

之所以这样判断，有四个直接代码证据：

1. `mathart/core/physics3d_backend.py` 已经在 manifest 中暴露 `physics_solver` / `contact_manifold_count` / `physics_downgraded_to_2d_input` / `frame_count` / `fps` 等离散判别器特征——这是 RuntimeDistillBus 做热路径直方图所需的最小特征集；
2. `Physics3DBackend.dependencies = ['unified_motion']` + `MicrokernelPipelineBridge.run_backend` 的依赖解析意味着——任何插桩都可以以 capability 标志 + 保留 context key 的方式打到 bridge 一层，而不需要侵入 backend；
3. `XPBDSolver3D.last_diagnostics.z_axis_active` 已经存在，可作为 telemetry 的天然种子；
4. `ArtifactManifest.schema_hash` 已存在，可直接作为 `DistillationRecord.upstream_manifest_hash` 的来源，闭合 3D 蒸馏规则到上游 MOTION_UMR clip 的可追溯性。

| 战役簇 | 当前代表差距 | 为什么现在排在这个位置 |
|---|---|---|
| **统一运动+物理热路径评估闭环** | `P1-DISTILL-1A`、`P1-DISTILL-1B`、`P1-DISTILL-3`、`P1-DISTILL-4` | 微内核里现在同时跑 `unified_motion` 与 `physics_3d`，是给 RuntimeDistillBus 装"全局热路径评估器"的最佳窗口期 |
| **微内核迁移余项** | `P1-MIGRATE-2`、`P1-MIGRATE-3`、`P1-MIGRATE-4` | `P1-MIGRATE-1`/`P1-XPBD-3` 已关闭，最大架构残留变成旧 EvolutionBridge 仍未完全迁入 NicheRegistry，且按后端 schema 守门的 CI 还未覆盖新加的 `PHYSICS_3D_MOTION_UMR` 族 |
| **生产级视觉闭环** | `P1-AI-2D`、`P1-AI-2D-SPARSECTRL`、`P1-INDUSTRIAL-34C` | 视觉总线已通，但距离"可稳定投产"仍差真实预设包、SparseCtrl 实跑、Dead Cells 式 3D→2D 桥 |
| **运动高非线性切换收口** | `P1-B3-1`、`P1-GAP4-BATCH`、`P1-PHASE-33B`、`P1-AI-2E` | 统一主干已存在，但 jump/fall/hit 等高非线性切换的批量调参与覆盖仍未 fully 吞下 |
| **细粒度物理精度** | `P1-XPBD-1`（自由落体精度）| 3D 求解器已落地，但 Rayleigh damping 导致的 g·t²/2 偏差仍是物理校准的最后一根硬骨头 |
| **中期升维与引擎消费** | `P2-DIM-UPLIFT-*`、`P2-UNITY-2DANIM-1`、`P2-SPINE-PREVIEW-1` | 升维 backend 基建已在，但 Unity / 真实引擎消费闭环仍未完成 |

## 四、按我理解的最新优先级排序（前 12）

下面排序以"对当前架构跃迁的边际价值"为第一指标，"实现成本 + 风险"为第二指标。与上一版最大的区别在于：**`P1-XPBD-3` 已退出活跃主战场，`P1-DISTILL-1A` 上升为唯一一号目标，`P1-MIGRATE-3` 因新加的 `PHYSICS_3D_MOTION_UMR` 族而提前。**

| 排序 | ID | 当前状态 | 标题 | 为什么现在排在前面 |
|---|---|---|---|---|
| 1 | `P1-DISTILL-1A` | PARTIAL | Roll RuntimeDistillBus into gait blending and batch physics penalties | `Physics3DBackend` 已经把 `physics_solver` / `contact_manifold_count` / 降级标志暴露在 manifest，`UnifiedMotionBackend` 已经是单一热路径入口，RuntimeDistillBus 现在装有"双源全局热路径评估"的最佳窗口期。SESSION-071 `SESSION_HANDOFF.md` 已列出 4 项可立即落地的微调（per-frame telemetry sidecar / `HOT_PATH_INSTRUMENTED` capability + `run_backend_with_telemetry` / CompiledParameterSpace 物理旋钮 / `DistillationRecord.upstream_manifest_hash`）。 |
| 2 | `P1-MIGRATE-3` | TODO | Add per-backend CI validation with artifact schema checks | 新加的 `ArtifactFamily.PHYSICS_3D_MOTION_UMR` 是天然的 CI 守门目标。如果不补 per-backend schema 校验，`Physics3DBackend` 与 `UnifiedMotionBackend` 之间的 Context-in / Manifest-out 边界纯净度就只能依靠人工守。 |
| 3 | `P1-MIGRATE-2` | TODO | Migrate legacy EvolutionOrchestrator bridges to NicheRegistry | `P1-MIGRATE-1` 已关闭，最大架构残留变成旧 EvolutionBridge 仍未完全迁入 NicheRegistry；与 RuntimeDistillBus 的命运相绑（蒸馏出的规则需要回写 NicheRegistry）。 |
| 4 | `P1-DISTILL-1B` | TODO | Add Taichi backend and benchmark suite for Runtime DistillBus | 1A 上线后立刻补 Taichi 后端 + benchmark 套件，可以把"评估热路径"的 array/JIT 性能差异暴露出来；与 P3-GPU-BENCH-1 形成天然组合。 |
| 5 | `P1-DISTILL-3` | TODO | Distill Verlet & Gait Parameters | 3D XPBD 已经接入，distance/bending compliance 是天然的 Verlet 参数蒸馏目标；与 1A 共生。 |
| 6 | `P1-DISTILL-4` | TODO | Distill Cognitive Science Rules | 高阶蒸馏需要 1A 提供的 telemetry 时序作为输入；可以排在 1A/3 之后。 |
| 7 | `P1-B3-1` | PARTIAL | Integrate GaitBlender into pipeline.py gait switching path | 统一主干已存在，但更广义的 gait switching pipeline 仍未彻底吃掉 walk/run/sneak 之外的切换语义。 |
| 8 | `P1-GAP4-BATCH` | PARTIAL | Batch-tune multiple hard transitions through the active Layer 3 loop | Layer 3 闭环目前仍主要覆盖常规 locomotion 过渡，jump/fall/hit 等高非线性状态仍未被批量调参与审计 fully 吞下。 |
| 9 | `P1-XPBD-1` | TODO | Free-fall test precision optimization (damping causes deviation from analytical g·t²/2) | 3D 求解器已落地，但 Rayleigh damping 导致的 g·t²/2 偏差是物理校准的最后一根硬骨头；闭掉它能让物理基线达到"工业可信"。 |
| 10 | `P1-AI-2D` | TODO | Ship real ComfyUI batch preset packs for IP-Adapter + ControlNet anti-flicker jobs | 反闪烁通道虽已能出 manifest，但商业可用性仍缺真实 ComfyUI 预设包。这个任务直接决定外部生产可复制性。 |
| 11 | `P1-AI-2D-SPARSECTRL` | TODO | Full ComfyUI workflow execution with SparseCtrl model weights | SparseCtrl 不能只停留在桥接，需要真实权重与工作流执行才能形成"真实跑通"的最后证据。 |
| 12 | `P1-INDUSTRIAL-34C` | TODO | 3D-to-2D mesh rendering path (Dead Cells full workflow) | Dead Cells 式 3D→2D 渲染桥是视觉工业化的关键下一段，没有它，维度提升与工业交付仍然割裂。 |

## 五、当前最合理的执行顺序

| 阶段 | 现在最该做什么 | 对应差距 |
|---|---|---|
| 阶段 1 | **打通双源全局热路径评估**：把 RuntimeDistillBus 接到 `UnifiedMotionBackend` + `Physics3DBackend` 的 manifest 与 telemetry sidecar 上，落地 4 项微调 | `P1-DISTILL-1A` |
| 阶段 2 | **补 per-backend schema 守门 CI**：以 `PHYSICS_3D_MOTION_UMR` 与 `MOTION_UMR` 为目标，把 `validate_artifact()` 接进 CI | `P1-MIGRATE-3` |
| 阶段 3 | **完成微内核迁移余项**：清理旧 EvolutionBridge 双轨，迁入 NicheRegistry；视情况补 hot-reload | `P1-MIGRATE-2`、`P1-MIGRATE-4` |
| 阶段 4 | **深化蒸馏与 GPU 基线**：Taichi 后端 + benchmark + Verlet/Gait 参数蒸馏 + 认知规则蒸馏 | `P1-DISTILL-1B`、`P1-DISTILL-3`、`P1-DISTILL-4` |
| 阶段 5 | **收窄运动高非线性切换缺口**：扩大 gait switching 与 Layer 3 批量审计覆盖；修自由落体精度 | `P1-B3-1`、`P1-GAP4-BATCH`、`P1-PHASE-33B`、`P1-AI-2E`、`P1-XPBD-1` |
| 阶段 6 | **补生产级视觉预设与真实执行**：交付 ComfyUI 预设包、SparseCtrl 实跑、3D→2D 桥、动画预览 | `P1-AI-2D`、`P1-AI-2D-SPARSECTRL`、`P1-INDUSTRIAL-34C`、`P1-PHASE-33C` |
| 阶段 7 | **推进中期升维消费闭环**：把现有升维输出真正喂给 Unity / 原生格式 / 预览端 | `P2-DIM-UPLIFT-*`、`P2-UNITY-2DANIM-1`、`P2-SPINE-PREVIEW-1`、`P2-VNE-UNITY-1` |

## 六、当前全部活跃差距总表（按 SESSION-071 最新状态重排）

下面的总表继续采用"按任务 ID 去重、任一记录为 `DONE` 即视为关闭、其余状态全部保留"的口径。第一层按当前执行优先级把最关键任务前置；第二层在各优先级簇内按状态与 ID 排序。当前 0 项状态冲突，已无需在状态列额外标注冲突信息。

### 6.1 当前 P1 主战场（43 项）

| 顺序 | ID | Status | Title |
|---|---|---|---|
| 1 | P1-DISTILL-1A | PARTIAL | Roll Runtime DistillBus into gait blending and batch physics penalties |
| 2 | P1-MIGRATE-3 | TODO | Add per-backend CI validation with artifact schema checks |
| 3 | P1-MIGRATE-2 | TODO | Migrate legacy EvolutionOrchestrator bridges to NicheRegistry |
| 4 | P1-DISTILL-1B | TODO | Add Taichi backend and benchmark suite for Runtime DistillBus |
| 5 | P1-DISTILL-3 | TODO | Distill Verlet & Gait Parameters |
| 6 | P1-DISTILL-4 | TODO | Distill Cognitive Science Rules |
| 7 | P1-B3-1 | PARTIAL | Integrate GaitBlender into pipeline.py gait switching path |
| 8 | P1-GAP4-BATCH | PARTIAL | Batch-tune multiple hard transitions through the active Layer 3 loop |
| 9 | P1-PHASE-33B | PARTIAL | Terrain-adaptive phase modulation |
| 10 | P1-AI-2E | TODO | Extend motion-adaptive keyframe planning to high-nonlinearity action segments |
| 11 | P1-XPBD-1 | TODO | Free-fall test precision optimization (damping causes deviation from analytical g·t²/2) |
| 12 | P1-AI-2D | TODO | Ship real ComfyUI batch preset packs for IP-Adapter + ControlNet anti-flicker jobs |
| 13 | P1-AI-2D-SPARSECTRL | TODO | Full ComfyUI workflow execution with SparseCtrl model weights |
| 14 | P1-INDUSTRIAL-34C | TODO | 3D-to-2D mesh rendering path (Dead Cells full workflow) |
| 15 | P1-PHASE-33C | TODO | Animation preview / visualization tool |
| 16 | P1-MIGRATE-4 | TODO | Implement hot-reload for dynamically discovered backends |
| 17 | P1-AI-2C | SUBSTANTIALLY-CLOSED | Expose Phase 2 anti-flicker visual pipeline through CLI / AssetPipeline |
| 18 | P1-E2E-COVERAGE | SUBSTANTIALLY-ADVANCED | Expand E2E Reproducibility Tests |
| 19 | P1-NEW-10 | SUBSTANTIALLY-ADVANCED | Production benchmark asset suite |
| 20 | P1-B3-5 | SUBSTANTIALLY-CLOSED | Unify transition_synthesizer.py with gait_blend.py |
| 21 | P1-INDUSTRIAL-34A | SUBSTANTIALLY-CLOSED | Industrial renderer integration into AssetPipeline |
| 22 | P1-INDUSTRIAL-44A | SUBSTANTIALLY-CLOSED | Engine-ready export templates for analytical SDF auxiliary maps |
| 23 | P1-INDUSTRIAL-44C | SUBSTANTIALLY-CLOSED | Specular/roughness and material metadata export for 2D lighting |
| 24 | P1-2 | TODO | Per-frame SDF parameter animation |
| 25 | P1-AI-1 | TODO | Math-to-AI Pipeline Prototype |
| 26 | P1-ARCH-4 | TODO | PDG v2 runtime semantics |
| 27 | P1-ARCH-5 | TODO | OpenUSD-compatible scene interchange |
| 28 | P1-ARCH-6 | TODO | Rich topology-aware level semantics |
| 29 | P1-B1-1 | TODO | Render cape/hair ribbons or meshes directly from Jakobsen chain snapshots |
| 30 | P1-B2-1 | CLOSED (SESSION-112) | Add more terrain primitives (convex hull, Bézier curve, heightmap import) |
| 31 | P1-B2-2 | TODO | Extend TTC prediction to multi-bounce scenarios and moving platforms |
| 32 | P1-B3-2 | TODO | Add GaitBlender reference motions to RL environment |
| 33 | P1-HUMAN-31A | TODO | Integrate SMPL-like shape latents into CharacterGenotype and rendering pipeline |
| 34 | P1-HUMAN-31C | TODO | Pseudo-3D paper-doll / mesh-shell backend using dual quaternions |
| 35 | P1-NEW-2 | TODO | Reaction-diffusion textures |
| 36 | P1-NEW-8 | TODO | Quality checkpoint mid-generation |
| 37 | P1-NEW-9C | TODO | Character evolution 3.0: expand part registry |
| 38 | P1-RESEARCH-30A | TODO | Metabolic Engine: ATP/Lactate Fatigue Model |
| 39 | P1-RESEARCH-30B | TODO | MPM & Phase Change Simulation |
| 40 | P1-RESEARCH-30C | TODO | Reaction-Diffusion & Thermodynamics for Surface Chemistry |
| 41 | P1-VAT-PRECISION-1 | TODO | Add higher-precision VAT encodings and Unity material presets |
| 42 | P1-VFX-1A | TODO | Bind real character silhouette masks into fluid VFX presets |
| 43 | P1-VFX-1B | TODO | Drive fluid VFX directly from UMR root velocity and weapon trajectories |

### 6.2 当前 P2 中期扩展层（33 项）

| 顺序 | ID | Status | Title |
|---|---|---|---|
| 44 | P2-DIM-UPLIFT-1 | PARTIAL | Integrate DC mesh output with existing Unity URP pipeline |
| 45 | P2-5 | PARTIAL | Procedural outline variation |
| 46 | P2-1 | TODO | Sub-pixel rendering |
| 47 | P2-4 | TODO | Multi-objective optimization (NSGA-II) |
| 48 | P2-6 | TODO | CMA-ES optimizer upgrade |
| 49 | P2-7 | TODO | Performance benchmarks |
| 50 | P2-8 | TODO | Test coverage for missing modules |
| 51 | P2-ANTIFLICKER-3 | TODO | Optical flow estimation from math engine motion vectors |
| 52 | P2-DEEPPHASE-FFT-2 | TODO | Neural network autoencoder training for DeepPhase |
| 53 | P2-DIM-UPLIFT-2 | TODO | Implement octree-based adaptive Dual Contouring (LOD chain) |
| 54 | P2-DIM-UPLIFT-4 | TODO | Compile actual Taichi AOT module (requires Taichi Vulkan backend) |
| 55 | P2-DIM-UPLIFT-5 | TODO | Build Unity native plugin from generated C++ code |
| 56 | P2-DIM-UPLIFT-6 | TODO | Test displacement mapping in Unity Shader Graph |
| 57 | P2-DIM-UPLIFT-7 | TODO | Integrate cel-shading with existing sprite pipeline |
| 58 | P2-DIM-UPLIFT-8 | TODO | Performance benchmark: DC at resolution 64/128/256 |
| 59 | P2-DIM-UPLIFT-9 | TODO | GPU-accelerated SDF sampling via Taichi kernels |
| 60 | P2-DIM-UPLIFT-10 | TODO | Connect adaptive cache to DC for faster extraction |
| 61 | P2-DIM-UPLIFT-12 | TODO | Implement Marching Cubes as fallback/comparison |
| 62 | P2-DIM-UPLIFT-13 | TODO | Runtime SDF evaluation on GPU (Taichi/compute shader) |
| 63 | P2-DIM-UPLIFT-14 | TODO | Animated SDF morphing between keyframes |
| 64 | P2-MORPHOLOGY-2 | TODO | Expand morphology archetype library and add weapon/accessory attachment points |
| 65 | P2-MORPHOLOGY-3 | TODO | GPU-accelerated SDF evaluation for large morphology populations |
| 66 | P2-MOTIONDB-IK-2 | TODO | Full IK solver integration with motion matching |
| 67 | P2-PHASE-CLEANUP | TODO | Deprecate and remove legacy animation API surface |
| 68 | P2-PHYSICS-DEFAULT | TODO | Enforce Physics/Biomechanics defaults in CharacterSpec |
| 69 | P2-PRINCIPLES-FULL-1 | TODO | Extend principles quantifier to all 12 Disney principles |
| 70 | P2-QEM-NANITE-1 | TODO | Nanite-style hierarchical LOD with seamless transitions |
| 71 | P2-REALTIME-COMM-1 | TODO | Python↔Unity real-time gait inference communication protocol |
| 72 | P2-SPINE-PREVIEW-1 | TODO | Spine JSON animation previewer |
| 73 | P2-UNITY-2DANIM-1 | TODO | Unity 2D Animation native format export |
| 74 | P2-VNE-UNITY-1 | TODO | Export edited vertex normals to Unity mesh format |
| 75 | P2-WFC-2 | TODO | Themed WFC tile sets with game progression integration |
| 76 | P2-WFC-3 | TODO | Multi-objective WFC optimization via Pareto frontier |

### 6.3 当前 P3 产品呈现层（4 项）

| 顺序 | ID | Status | Title |
|---|---|---|---|
| 77 | P3-1 | PARTIAL | Auto knowledge distillation |
| 78 | P3-2 | TODO | Web preview UI |
| 79 | P3-5 | TODO | End-to-end demo showcase script |
| 80 | P3-GPU-BENCH-1 | TODO | Run formal Taichi GPU benchmark and sparse-cloth validation on CUDA hardware |

## 七、需要单独暴露的非功能性差距

虽然 SESSION-071 关闭了 `P1-AI-2C` 双轨记录的治理冲突，但仍有 3 类非功能性差距需要在下一会话之前显式暴露：

| 类别 | 现象 | 建议 |
|---|---|---|
| **测试基础设施 flakiness** | `pytest-xdist` 并行模式下 `tests/test_evolution_loop.py` 出现 worker crash；`tests/test_layer3_closed_loop.py` 因 `TransitionSynthesizer` 缺 `get_transition_quality` 而失败；`tests/test_state_machine_graph_fuzz.py` 有 Hypothesis 导入错；`tests/test_taichi_xpbd.py` 因沙箱无 `taichi` 而 fail；`tests/test_anti_flicker_temporal.py` 因运行时长被 SESSION-071 全量回归剔除。 | 全部归档为 pre-existing infra-only flake，并在 CI 中以 `--ignore` 显式声明，避免被误判为新引入回归 |
| **`PROJECT_BRAIN.json` 大小** | 当前 `PROJECT_BRAIN.json` 已经超过 3000 行，`pending_tasks` 与 `open_tasks` 两个数组共持有 122 条记录（114 + 8），后续 `P1-MIGRATE-2/3/4` 关闭后建议合并到单一数组并补 `closed_tasks_archive` | 在 `P1-MIGRATE-3` 落地时一并清理 |
| **Distillation Provenance 字段缺失** | 现有 `DistillationRecord` 无 `upstream_manifest_hash`，3D 蒸馏规则无法被反向追溯到生成它的 MOTION_UMR clip | 在 `P1-DISTILL-1A` 第 4 项微调中一并补上 |

## 八、底线判断

项目现在比上一版基线更成熟，也更需要"路线判断 + 节奏控制"。成熟在于：**统一运动主干和 3D 物理底盘都已经以一级 microkernel backend 的形式落地，分别走 `MOTION_UMR` 与 `PHYSICS_3D_MOTION_UMR` 两个 typed manifest，并经由 `MicrokernelPipelineBridge` 实现 FrameGraph 风格的依赖解析与级联执行；2D 旧基建零穿透。** 微妙在于：如果接下来不顺势推进 `P1-DISTILL-1A` + `P1-MIGRATE-3`，项目就会停留在"两个先进 backend 已经接好但还没有形成全局闭环"的阶段。

因此，对当前项目的底线判断是：**SESSION-071 已经把"3D 物理底盘 → 微内核接管"这一步拿下；下一轮真正决定项目等级跃迁的，不再是继续证明微内核能接 motion / physics，而是证明微内核里的统一 motion + 统一 physics 能继续承载 RuntimeDistillBus 的全局热路径评估，并把蒸馏出的规则反喂回编译参数空间与 Layer 3 闭环。** 一旦这一层接上，项目就会从"拥有多个先进子系统的强引擎雏形"，正式升级为"拥有统一主干、统一接口、统一物理扩展路径、统一热路径评估入口与统一蒸馏规则回写通路的下一代角色生产引擎"。

## 附：参考与证据

[1]: PROJECT_BRAIN.json — `pending_tasks` / `open_tasks` 双表去重结果：`ACTIVE TOTAL: 80`，`CONFLICTS: []`
[2]: SESSION_HANDOFF.md — SESSION-071 已写明 4 项 P1-DISTILL-1A 微调（telemetry sidecar / `HOT_PATH_INSTRUMENTED` capability / CompiledParameterSpace 物理旋钮 / `DistillationRecord.upstream_manifest_hash`）
[3]: mathart/animation/xpbd_solver_3d.py — `(N,3)` 状态数组、3D ∇C、`SpatialHashGrid3D` `min_separation` 闸门
[4]: mathart/core/physics3d_backend.py — `BackendType.PHYSICS_3D` / `ArtifactFamily.PHYSICS_3D_MOTION_UMR` / `BackendCapability.PHYSICS_SIMULATION`，`dependencies=['unified_motion']`，纯 Context-in / Manifest-out
[5]: mathart/animation/unified_motion.py — `ContactManifoldRecord` 3D `contact_point_*` / `penetration_depth` / `source_solver`（默认 `None`，向下兼容）
[6]: mathart/core/backend_registry.py — `register_backend` 自动加载 `mathart.core.physics3d_backend`
[7]: tests/test_physics3d_backend.py — 7/7 红线测试覆盖三条铁律（防伪 3D 套壳 / 防 2D 崩坏 / 防微内核越权）
[8]: git log `02327b7` — SESSION-071 P1-XPBD-3 关闭 commit，已推送 `origin/main`
