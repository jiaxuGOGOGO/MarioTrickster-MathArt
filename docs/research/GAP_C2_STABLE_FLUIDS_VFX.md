# Gap C2 — 物理驱动的粒子特效（Stable Fluids / Grid-Based Vector Fields）

> Session: **SESSION-046**  
> Status: **implemented and integrated**  
> Primary reference figure: **Jos Stam**  
> Core papers: **Stable Fluids (SIGGRAPH 1999)**, **Real-Time Fluid Dynamics for Games**

## 1. Why this gap existed

项目原有 VFX 体系已经具备像素风粒子、序列输出和基础动画管线，但其主运动来源仍然偏向 **发射器初速度 + 生命周期衰减**。这足以做火花、爆炸和普通烟雾，却不足以表达“角色高速冲刺时尾烟自动卷曲”或“挥剑时烟尘绕过身体与武器路径回卷”的效果。

Gap C2 的本质不是“多做几套序列帧”，而是把 VFX 从**局部粒子脚本**升级为**可被角色速度场驱动的二维流体近似系统**。

## 2. Research distillation

| Source | What was extracted | Concrete code landing |
|---|---|---|
| Jos Stam, *Stable Fluids* | 稳定流体的核心分解：加源、扩散、平流、投影；平流采用反向追踪，投影用于强制不可压缩 | `mathart/animation/fluid_vfx.py::FluidGrid2D` |
| Jos Stam, *Real-Time Fluid Dynamics for Games* | 游戏级最小实现蓝图：ghost cells、Gauss-Seidel、`vel_step` / `dens_step` / `project` / `set_bnd` | `FluidGrid2D._velocity_step()` / `_density_step()` / `_project()` / `_set_bnd()` |
| Engineering reference implementations | 共点网格足以支撑 2D 烟雾可视结果，不必一开始就引入更重的 MAC/GPU 管线 | 采用 NumPy-only colocated grid，避免外部依赖膨胀 |

## 3. Implemented architecture

### 3.1 Runtime modules

| Module | Role |
|---|---|
| `mathart/animation/fluid_vfx.py` | Stable Fluids 求解器、障碍物遮挡、流体引导粒子、烟雾渲染 |
| `mathart/pipeline.py::produce_vfx()` | 新增 `smoke_fluid`、`dash_smoke`、`slash_smoke` 预设，并支持 `obstacle_mask` 与外部 `driver_impulses` |
| `mathart/evolution/fluid_vfx_bridge.py` | 将流体 VFX 接入三层进化循环的评价、蒸馏、状态持久化 |
| `tests/test_fluid_vfx.py` | 覆盖数值稳定性、障碍物阻挡、管线接入、桥接持久化 |

### 3.2 Three-layer evolution mapping

| Layer | Gap C2 behavior |
|---|---|
| **Layer 1: Internal Evolution** | 评估流体能量、障碍物泄漏率、粒子活跃度与 alpha 覆盖，拒绝“死烟雾”与“穿模烟雾” |
| **Layer 2: External Distillation** | 把 Stable Fluids 研究结果蒸馏为可复用规则，追加写入 `knowledge/fluid_vfx_rules.md` |
| **Layer 3: Self-Iterative Testing** | 跟踪 `best_flow_energy`、`lowest_obstacle_leak_ratio`、连续通过次数，并写入 `.fluid_vfx_state.json` 与 `PROJECT_BRAIN.json` |

## 4. Key engineering decisions

### 4.1 Why colocated 2D grids were chosen

当前仓库是一个偏 **CPU / NumPy / 像素风离线生产与轻量运行时** 的系统。引入更重的 CFD 表示法会带来复杂度与维护成本，却不会对当前 2D 烟尘目标形成同量级收益。因此当前版本优先采用：

1. **二维共点网格**；
2. **ghost boundary cells**；
3. **半拉格朗日平流**；
4. **Gauss-Seidel 线性求解**；
5. **内部 obstacle mask**；
6. **粒子仅负责风格化可视采样，而非主运动学**。

### 4.2 Why particles were kept

纯密度渲染虽然已经能形成烟雾轮廓，但对像素风项目而言，少量粒子覆盖层可以强化边缘、火花、尘粒和局部节奏感。因此本次不是删除旧粒子系统，而是把它升级为：

> **Grid-driven particles**：粒子从“各自飞行”改为“采样流体速度场并被其平流”。

这使现有资源和未来特效预设仍可共存。

## 5. Obstacle handling and future gameplay integration

本次已实现 `obstacle_mask` 输入接口，以及默认的 demo 身体障碍轮廓。真实接入角色表现时，只需把角色 alpha / SDF / silhouette raster 投影到流体网格，即可让烟雾自动绕开角色身体。

后续接入建议如下：

| Future input source | Recommended hook |
|---|---|
| 角色当前 alpha mask | `resize_mask_to_grid()` → `produce_vfx(..., obstacle_mask=...)` |
| 根节点速度 / dash velocity | 生成 `driver_impulses`，每帧写入 `velocity_x / velocity_y` |
| 武器挥砍轨迹 | 把轨迹切线速度转为 `slash_smoke` 风场驱动 |
| Motion vectors / UMR root transform | 作为外部知识蒸馏后的自动驱动源，减少手写特效参数 |

## 6. Validation completed

本次已新增并通过 `tests/test_fluid_vfx.py`，覆盖以下审计点：

| Audit item | Result |
|---|---|
| 流体网格数值稳定，无 NaN/Inf | Pass |
| 障碍物内部密度被阻挡 | Pass |
| `dash_smoke` 预设可输出有效帧与诊断数据 | Pass |
| `AssetPipeline.produce_vfx()` 已支持流体预设 | Pass |
| 三层桥接模块可持久化状态与知识规则 | Pass |

## 7. What is intentionally deferred

以下事项已经为未来留出接口，但本次不强制一次性做重：

1. **真实角色逐帧 silhouette 驱动**：接口已就位，等待与具体角色生成或动画导出流程绑定。
2. **MAC grid / FFT solver / 3D CFD**：当前 2D 像素烟雾目标下收益不足，先不引入。
3. **GPU shader runtime**：现有 CPU/NumPy 实现更适合仓库自演化和测试覆盖。
4. **更复杂的 obstacle pressure bleed 修正**：当前版本已足以阻止主要泄漏，后续可按实测继续加强。

## 8. Final conclusion

Gap C2 已从“概念研究”升级为“仓库内可运行、可测试、可蒸馏、可演进的实现”。其意义不只是新增三个 VFX 预设，而是把项目的特效生成方式正式推进到：

> **角色速度 → 网格矢量场 → 稳定流体 → 粒子受流体牵引 → 三层进化系统持续调优**。

这使后续你继续补充新的动作语义、角色 mask、战斗状态机或研究材料时，仓库可以在现有基础上继续自我迭代，而不是重新推翻重做。
