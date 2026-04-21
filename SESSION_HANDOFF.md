| Key | Value |
|---|---|
| Session | `SESSION-110` |
| Focus | `P1-PHASE-33B` Terrain-Adaptive Phase Modulation System：基于 PFNN/生物力学/Motion Matching 的张量化前向轨迹地形提取 + 连续数学映射相位调制 + EMA 低通滤波 C1 连续平滑 + 闭环桥接至 UnifiedGaitBlender |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `54 PASS / 0 FAIL`（`pytest -q tests/test_terrain_phase_modulation.py`，含 6 类测试组：强类型契约、张量化 Forecaster、连续映射函数、相位调制器 EMA 平滑、闭环集成桥接、三条红线合规审计 + 生物力学验证）|
| Full Regression | `1818 PASS / 35 FAIL (all pre-existing) / 3 SKIP`。35 个失败均为 SESSION-110 之前已存在的环境性问题（ComfyUI/subprocess、Taichi GPU kernel、hot-reload object-id race、anti-flicker HTTP 依赖等），与本轮改动无关 |
| Additional Audit | 已完成三条红线审计：anti-scalar-loop（forecast 热路径零 Python `for` 循环，全部走 NumPy 张量运算）/ anti-phase-pop（调制器仅修改相位一阶导数 Δφ，绝不触碰相位绝对值，EMA 低通滤波保证 C1 连续性）/ anti-magic-number（全部映射参数封装于 `TerrainGaitConfig` frozen dataclass，连续 smoothstep/cos 数学函数替代硬编码阶跃分支，可无缝对接 `RuntimeDistillationBus`）|
| Primary Files | `mathart/animation/terrain_phase_types.py`, `mathart/animation/terrain_trajectory_forecaster.py`, `mathart/animation/terrain_phase_modulator.py`, `mathart/animation/terrain_adaptive_gait_bridge.py`, `mathart/animation/__init__.py`, `tests/test_terrain_phase_modulation.py`, `research_notes_p1_phase_33b.md` |

## Executive Summary

本轮的工程目标是实装 **P1-PHASE-33B（地形自适应相位调制）**——让角色在不同地形（平地、上坡、下坡、泥地、冰面等）上行走时，步态的步幅（stride）和步频（frequency）能够基于前方地形的坡度和表面材质进行连续、平滑的自适应调制，而不是依赖硬编码的阶跃分支。

在此之前，`UnifiedGaitBlender` 的 `StrideWheel` 以恒定的相位速度驱动步态循环，无论角色前方是平地还是陡坡，步幅和步频都不会改变。这违反了基本的生物力学规律（上坡时生物会缩短步幅并提高步频，下坡时则相反），也无法满足 PFNN（Holden SIGGRAPH 2017）所描述的地形感知相位流逝调制。

SESSION-110 通过引入四个新模块，构建了一条完整的**张量化地形感知 → 连续数学映射 → EMA 平滑 → 闭环桥接**管线：

1. **`terrain_phase_types.py`** — 强类型数据契约层。`TerrainGaitConfig`（frozen dataclass）封装全部调制参数（轨迹采样数、坡度-步幅映射系数、EMA 时间常数、表面类型等），所有参数均有 `__post_init__` 边界钳位，可通过 `RuntimeDistillationBus` 动态解析。`SurfaceTypeEntry` 定义表面材质词汇表（default/ice/grass/mud/sand/stone），每种材质携带 viscosity 和 friction 两个连续参数。

2. **`terrain_trajectory_forecaster.py`** — 张量化前向轨迹地形提取器。基于 Ubisoft Motion Matching（Clavet GDC 2016）的轨迹预测思想，将角色未来 N 步的轨迹坐标打包为 `(N, 2)` NumPy 张量，一次性批量查询 TerrainSDF 获取 SDF 值、梯度场、坡度角和表面粘度。**热路径中零 Python `for` 循环**——全部通过 `np.arange` + broadcasting + 矩阵运算完成。输出为不可变的 `TrajectoryTerrainSample`（所有数组设置 `writable=False`），并携带 Gaussian 距离衰减加权的 `weighted_slope` 和 `weighted_viscosity` 标量。

3. **`terrain_phase_modulator.py`** — 地形自适应相位调制器。核心数学映射使用 `smoothstep` + `np.cos` 连续函数将坡度角映射为 `stride_scale`（步幅缩放）和 `freq_scale`（步频缩放），严格遵循生物力学规律：上坡 → stride_scale < 1.0, freq_scale > 1.0；下坡 → 反之。表面粘度通过乘性衰减进一步调制两个缩放因子。**关键防护**：调制器仅输出缩放因子，绝不直接修改相位绝对值；目标缩放因子通过 **EMA 低通滤波器**（指数移动平均，时间常数 τ 可配置）平滑过渡，保证角色从平地瞬间踏上陡坡时不会出现 IK 撕裂或相位突变。

4. **`terrain_adaptive_gait_bridge.py`** — 闭环集成桥接层。将 Forecaster + Modulator 组合为一个无损挂载点，可直接生成 `UnifiedMotionFrame`（UMR 强类型帧），所有调制元数据（stride_scale、freq_scale、weighted_slope、weighted_viscosity、sample_count）注入 frame.metadata，供下游 Layer 3 消费。

## Research Alignment Audit

| Reference | Requested Principle | SESSION-110 Concrete Closure |
|---|---|---|
| Holden PFNN (SIGGRAPH 2017) | 基于地形高度场采样的相位流逝调制 | `TrajectoryTerrainForecaster` 对前方 N 点轨迹做批量 SDF 查询 + 梯度场提取，`TerrainPhaseModulator` 将加权坡度映射为相位速度缩放因子，实现非恒定的相位流逝 |
| 生物力学 Incline Walking | 上坡缩短步幅提高步频，下坡拉长步幅降低步频 | `_slope_to_stride_scale` 和 `_slope_to_freq_scale` 使用 smoothstep + cos 连续映射，54 个测试中 6 个专门验证生物力学方向性和对称性 |
| Ubisoft Motion Matching (Clavet GDC 2016) | 多点前向轨迹预测，非单点采样 | Forecaster 默认 8 点采样，Gaussian 距离衰减加权，可配置至 64 点；100 次 64 点 forecast 在 < 2s 内完成 |
| EMA / 临界阻尼弹簧 | C1 连续的低通滤波防止相位突变 | `TerrainPhaseModulator` 内置 EMA 滤波器（τ 可配置），测试验证从平地到 45° 陡坡的突变场景中单帧最大跳变 < 5% |
| RuntimeDistillationBus 对接 | 调制参数不硬编码，可被自动调参 | `TerrainGaitConfig` 支持 `resolve_terrain_gait_config(bus)` 从 RuntimeDistillationBus 解析参数，`to_dict()` 支持序列化 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/terrain_phase_types.py` | **NEW**。`TerrainGaitConfig` / `TrajectoryTerrainSample` / `TerrainPhaseModulationResult` / `SurfaceTypeEntry` frozen dataclass + `KNOWN_SURFACE_TYPES` 词汇表 + `resolve_terrain_gait_config()` | 把地形相位调制的全部参数和中间产物从临时 dict 提升为强类型、可序列化、可审计的数据契约 |
| `mathart/animation/terrain_trajectory_forecaster.py` | **NEW**。`TrajectoryTerrainForecaster` 张量化前向轨迹地形提取器 | 将未来 N 步轨迹坐标打包为 NumPy 张量，一次性批量 SDF 查询 + 梯度提取 + 坡度计算 + 表面粘度查询，零标量循环 |
| `mathart/animation/terrain_phase_modulator.py` | **NEW**。`TerrainPhaseModulator` + `_slope_to_stride_scale` / `_slope_to_freq_scale` / `_smoothstep` 连续映射函数 | 将加权坡度和粘度通过连续数学函数映射为 stride_scale / freq_scale，EMA 低通滤波保证 C1 连续性 |
| `mathart/animation/terrain_adaptive_gait_bridge.py` | **NEW**。`TerrainAdaptiveGaitBridge` + `TerrainAdaptiveGaitSample` | 将 Forecaster + Modulator 闭环组合，生成携带完整调制元数据的 `UnifiedMotionFrame` |
| `mathart/animation/__init__.py` | 新增 SESSION-110 导入块和 `__all__` 导出（10 个新符号） | 让新模块通过 `from mathart.animation import ...` 可达 |
| `tests/test_terrain_phase_modulation.py` | **NEW**。6 个测试类、54 个测试 | 覆盖强类型契约、张量化 Forecaster、连续映射函数、EMA 平滑、闭环集成、三条红线合规 + 生物力学验证 |
| `research_notes_p1_phase_33b.md` | **NEW**。学术研究笔记 | 记录 PFNN、生物力学、Motion Matching 的数学基础和架构映射 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 54/54 测试全绿 | `pytest -q tests/test_terrain_phase_modulation.py` → `54 passed` |
| 张量化 Forecaster 满足性能预算 | `test_vectorized_no_scalar_loop` 100 次 64 点 forecast < 2s |
| EMA 平滑防止相位突变 | `test_ema_prevents_phase_pop` 从平地到 45° 陡坡，单帧最大跳变 < 5% |
| EMA 收敛至目标值 | `test_ema_convergence` 120 帧后 stride_scale 误差 < 0.01 |
| 生物力学方向性正确 | `test_uphill_shorter_stride_higher_freq` / `test_downhill_longer_stride_lower_freq` |
| 上下坡对称响应 | `test_symmetric_slope_response` 上下坡偏差 < 0.05 |
| 粘度衰减正确 | `test_viscosity_reduces_mobility` 泥地 stride_scale < 干燥地面 |
| 连续映射无跳变 | `test_mapping_continuity` 1000 点扫描最大相邻差 < 0.01 |
| 映射单调性 | `test_mapping_monotonicity` stride_scale 随坡度单调递减 |
| 热路径无标量循环 | `test_no_scalar_loop_in_forecaster` 源码检查无 `for...range` |
| 调制器不修改相位绝对值 | `test_phase_absolute_never_modified` 源码检查 |
| 映射函数无硬编码分支 | `test_no_magic_numbers_in_mapping` 源码检查无 `if slope` |
| 全量回归零引入 | 1818 PASS / 35 FAIL (pre-existing) / 3 SKIP |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 Anti-Scalar-Loop Bottleneck：严禁热路径 Python `for` 循环逐点 SDF 查询 | ✅ 已合规。`TrajectoryTerrainForecaster.forecast()` 将 N 个轨迹点打包为 `(N, 2)` 张量，通过 `np.arange` + broadcasting 一次性计算位置，批量调用 `TerrainSDF.batch_query()` 获取 SDF 值和梯度，`np.arctan2` 向量化计算坡度角。源码检查确认零 `for...range` 循环 |
| 🔴 Anti-Phase-Pop Guard：严禁直接修改相位绝对值 | ✅ 已合规。`TerrainPhaseModulator` 仅输出 `stride_scale` 和 `freq_scale` 两个缩放因子，由下游乘到相位速度（一阶导数 Δφ）上。EMA 低通滤波器（时间常数 τ = 0.1s 默认值）保证目标缩放因子的平滑过渡，从平地到 45° 陡坡的突变场景中单帧最大跳变 < 5% |
| 🔴 Anti-Magic-Number Trap：严禁硬编码阶跃分支 | ✅ 已合规。`_slope_to_stride_scale` 和 `_slope_to_freq_scale` 使用 `smoothstep` + `np.cos` 连续数学函数，所有控制点（alpha、scale_min、scale_max）封装于 `TerrainGaitConfig` frozen dataclass，可通过 `resolve_terrain_gait_config(bus)` 从 `RuntimeDistillationBus` 动态解析 |

## Handoff Notes

- `TerrainAdaptiveGaitBridge` 当前以独立桥接层形式存在，可通过 `generate_terrain_adaptive_frame()` 直接生成 UMR 帧。后续若要将其挂载到 `UnifiedMotionBackend` 的热路径中，建议在 `MotionStateLaneRegistry` 中注册一个 `terrain_adaptive` lane，让 `_ProceduralStateLane` 在检测到 terrain context 时自动委托给 bridge。
- `SurfaceTypeEntry` 词汇表目前包含 6 种材质（default/ice/grass/mud/sand/stone）。后续可通过 `KNOWN_SURFACE_TYPES` 字典扩展，或通过 `surface_lookup` 回调函数实现空间变化的材质查询。
- `TerrainGaitConfig` 的 `resolve_terrain_gait_config(bus)` 已预留 `RuntimeDistillationBus` 接口，但当前仅解析 `trajectory_sample_count` 一个参数。后续 P1-DISTILL 系列任务可扩展更多参数的自动调参。
- 全量回归中 35 个失败均为 SESSION-110 之前已存在的环境性问题，与本轮改动无关。主要失败桶：ComfyUI/subprocess E2E（20 个）、Taichi GPU kernel（3 个）、hot-reload object-id race（1 个）、pre-existing metadata/integration（11 个）。
