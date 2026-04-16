# SESSION-046 Gap C2 研究笔记

## Trigger

用户明确要求“开启研究协议”，围绕 **Gap C2: 物理驱动的粒子特效 (VFX)** 做深度研究、代码落地、三层进化循环融合、全面审计并推送。

## Pre-research memory snapshot

| Field | Value |
|---|---|
| Latest commit hash | `783a9300044118d7acd4c642a132d8eb2bfc61f2` |
| Repo status before work | `SESSION-045` closed Gap C3 (Neural Rendering Bridge) |
| Current open target | `P1-VFX-1` physics-driven particle system |
| Existing particle baseline | `mathart/animation/particles.py` uses Verlet particles with gravity/drag/turbulence |
| Duplicate boundary | Do not re-research generic Verlet basics, generic VFX scoring, or already landed smoke/fire/sparkle/explosion presets |

## Browser findings saved

1. User repository page confirms current default branch is `main`, visible latest short commit is `783a930`, and the project already frames itself as a three-layer self-evolving art engine.
2. Jos Stam's **Stable Fluids** paper is available as a direct PDF source and is the designated North Star for deep reading on grid-based vector field fluid motion.

## Research framing block

| Field | Value |
|---|---|
| subsystem | Physics-driven VFX with grid-based vector field smoke/dust flow |
| decision_needed | How to upgrade current emitter-based particles into a stable-fluid-guided particle advection system that can ingest character velocity and write back into the three-layer evolution loop |
| already_known | Project already has Verlet particles, VFX evaluation, motion vectors, analytical SDF maps, and Layer 1/2/3 evolution plumbing |
| duplicate_forbidden | Generic particle presets, generic Verlet tutorials, generic three-layer loop descriptions |
| success_signal | A source must provide concrete algorithm steps, field injection rules, pressure projection/advection details, boundary handling, or production integration patterns |

## Browser findings batch 2

1. **Stable Fluids (SIGGRAPH 1999)** confirms the governing decomposition for a practical graphics solver: add external force, advect via backward tracing, diffuse implicitly, then project to a divergence-free velocity field.
2. **Real-Time Fluid Dynamics for Games** is a game-oriented follow-up by Jos Stam that provides the likely simplest implementation path for this repository: 2D square grid arrays, stable solver ordering, and code-structured routines usable without a heavyweight CFD stack.

## Deep-reading targets now locked

| Target | Why it matters |
|---|---|
| `stable_fluids_jos_stam.pdf` | Formal mechanism source for force injection, semi-Lagrangian advection, implicit diffusion, Helmholtz-Hodge projection |
| `StamFluidforGames.pdf` | Game-ready simplification and C-like implementation pattern suited to `numpy` and repository integration |

## Browser findings batch 3

1. **Real-Time Fluid Dynamics for Games** explicitly frames the target use case as smoke swirling past a moving character, which is directly aligned with the user's sword-swing / dash smoke requirement.
2. The `ohjay/stable_fluids` implementation notes confirm a practical engineering choice highly compatible with this repository: **colocated 2D grids**, row-major linear storage, extra boundary cells, and simple display/debug modes for density and velocity components.
3. The repo also notes that a staggered MAC grid was tried but did not materially improve visual quality for its use case; this supports starting from a colocated grid in `numpy` for fast project landing.

## Engineering implications updated

| Decision | Current conclusion |
|---|---|
| Grid layout | Start with a colocated 2D grid plus ghost boundary cells |
| Storage | Use `numpy` arrays with row-major semantics and separate fields for velocity x/y plus density |
| Interaction model | Inject force from character/body velocity into the grid each frame rather than relying only on emitter-local initial velocity |
| Visual target | Smoke/dust should be treated as density advected by the vector field, with particles optionally riding the field for sparkle/debris accents |

## Best new sources by topic

| Topic | Source type | Title | URL | Key takeaway | Novelty vs project | Integration value | Priority hint | Duplicate risk |
|---|---|---|---|---|---|---|---|---|
| Theory | Paper | Stable Fluids | https://pages.cs.wisc.edu/~chaol/data/cs777/stam-stable_fluids.pdf | 稳定流体的核心在于 **force → advect → diffuse → project**，其中平流用回溯采样，扩散与压力投影通过线性系统稳定求解。 | 项目已有 Verlet 粒子，但还没有基于网格的不可压矢量场与压力投影。 | 直接定义 Gap C2 的数学骨架。 | High | Low |
| Implementation | Paper | Real-Time Fluid Dynamics for Games | https://graphics.cs.cmu.edu/nsp/course/15-464/Fall09/papers/StamFluidforGames.pdf | 提供了可直接移植的二维数组布局、边界层、Gauss-Seidel、`dens_step` / `vel_step` / `project` 代码组织。 | 项目当前 VFX 仍以粒子初速度为主，不具备 `vel_step` + `dens_step` 框架。 | 适合作为 `numpy` 版 `FluidGrid2D` 的代码蓝图。 | High | Low |
| Engineering practice | Repo | ohjay/stable_fluids | https://github.com/ohjay/stable_fluids | 共点网格、行主序索引和显示密度/速度分量的调试面板足以得到稳定且视觉有效的结果。 | 支持从最简单可落地实现开始，而非过早切到更复杂 MAC 网格。 | 降低实现复杂度，加速项目集成。 | Medium | Low |

## Mechanism extraction

### Stable Fluids / Jos Stam 机制清单

1. **离散表示**：在二维方格上存储 `u, v, density`，并为边界保留 ghost cells。
2. **外力注入**：把用户或角色的速度 / 力量注入到速度场，不直接硬编码轨迹动画。
3. **半拉格朗日平流**：对每个目标网格点从当前速度场反向追踪到前一时刻位置，再做双线性采样。
4. **隐式扩散**：扩散项不要显式前进，而用稳定的线性求解，允许较大 `dt` 也不爆炸。
5. **压力投影**：通过求解 Poisson 方程消除散度，得到不可压速度场；这一步是卷曲烟雾自然出现的关键。
6. **双重投影意义**：游戏版实现会在扩散后和自平流后各做一次 `project()`，因为质量守恒的速度场更适合继续平流。
7. **边界条件**：固体边界让法向速度翻转/归零；标量场采用连续边界。对于角色身体这意味着可以把角色占用区域当作内部障碍。
8. **内部障碍物**：游戏版论文明确指出可用一个布尔占用网格表达内部物体，并在边界处理时从邻接值回填；这正适合利用项目已有角色 mask / SDF 信息。

### 对 MarioTrickster-MathArt 的直接实现含义

| Repository need | Research-backed decision |
|---|---|
| 烟尘会绕过角色身体卷曲 | 将角色身体投影为流体内部障碍（occupancy / mask），并在速度与密度边界处理中应用内部边界规则 |
| 挥剑与冲刺无需序列帧 | 从动作状态、角色根节点速度、以及已有 motion vector / pose delta 中提取外力脉冲并注入流体网格 |
| 保留像素风格 | 流体求解只负责生成密度和矢量场；最终渲染仍输出低分辨率像素烟尘帧 |
| 与现有粒子系统兼容 | 让粒子从“独立运动体”升级为“受流体速度场平流的可视采样点”，而不是完全推翻当前系统 |
| 三层进化循环融合 | Layer 1 评估流体 VFX 质量；Layer 2 蒸馏稳定参数与边界规则；Layer 3 跟踪历史最优网格配置并写回项目大脑 |

## Recommended next implementation step

先构建一个轻量但完整的 `FluidGrid2D`：支持 `add_velocity_impulse()`, `add_density()`, `step()`, `set_obstacle_mask()`, `sample_velocity()`。随后把 `ParticleSystem` 升级为可选的 **fluid-guided mode**，并在 `AssetPipeline.produce_vfx()` 中增加新的 `smoke_fluid` / `dash_smoke` / `slash_smoke` 预设。

## Do not re-search yet

1. 不要再泛化搜索“什么是 Stable Fluids”或“什么是半拉格朗日”。
2. 不要再重新研究通用 Verlet 粒子基础，本仓库已经掌握。
3. 不要先引入复杂 GPU/MAC/3D CFD 框架；当前最优路线是二维 `numpy` 网格 + 内部障碍 + 流体引导粒子。

