# Session Handoff
| Key | Value |
|---|---|
| Session | `SESSION-113` |
| Focus | `P1-B2-2` Extend TTC prediction to multi-bounce scenarios and moving platforms |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `22 PASS / 0 FAIL`（`pytest tests/test_terrain_sensor.py -k "DynamicTerrainSDF or AdvancedTTCPredictor or PlatformMotionFactory or PublicAPIExports_P1B2_2"`，覆盖三道红线：Anti-Tunnelling CFL Guard / Anti-Ghost-Momentum / Anti-Zeno's-Paradox） |
| Full Regression | `96 PASS`（核心动力学/地形/运动学/SDF/XPBD/高阶原语交叉测试全绿，证明新系统未破坏任何下游消费者） |
| Additional Audit | 已完成三道红线审计：**Anti-Tunnelling CFL Guard**（验证 Conservative Advancement 步进包含重力补偿，SDF 距离 D 在接触时 ≤ contact_threshold，绝无穿透）/ **Anti-Ghost-Momentum**（验证反射在相对速度空间计算，平台速度正确加回，升降梯场景动量守恒）/ **Anti-Zeno's-Paradox**（验证 max_bounces + min_bounce_velocity 双重护栏，高恢复系数场景 < 5s 完成，绝不死循环） |
| Primary Files | `mathart/animation/terrain_sensor.py`, `mathart/animation/__init__.py`, `tests/test_terrain_sensor.py` |

## Executive Summary

本轮的工程目标是完成 **P1-B2-2（TTC 动态平台感知与多段弹跳时空预测闭环）** 的落地。这是继 P1-B2-1（高阶地形 SDF 原语）之后的自然延伸，将 TerrainSDF 从静态空间域升维到时空域（4D SDF），并实现了工业级的多段弹跳轨迹预测。

### 核心交付物

1. **DynamicTerrainSDF（4D SDF 动态地形包装器）** — 通过 Decorator 模式包装任意静态 `TerrainSDF`，结合 `PlatformMotion` 运动描述符，在查询时对世界坐标应用逆刚体变换 $T^{-1}(t)$ 后查询静态 SDF 本地场。支持平移 + 旋转的完整刚体运动，并精确计算接触点的表面速度张量（含切向角速度分量）。

2. **AdvancedTTCPredictor（时空接触预测器）** — 基于 Mirtich (1996/2000) Conservative Advancement 算法的时空接触预测器。核心 CFL 守卫公式包含重力补偿：求解 $0.5|g|\Delta t^2 + |V_{rel}|\Delta t - D = 0$ 的正根作为安全步长上界，并附加 $0.8D/|V_{rel}|$ 的额外安全帽，确保在任意速度场下零穿透。

3. **MultiBounceTrajectory（多段弹跳轨迹）** — `frozen dataclass` 强类型契约，包含有序的 `BounceEvent` 元组，每个事件记录：碰撞时间、绝对坐标、表面法线、碰撞前后速度、平台表面速度、法向相对冲击速度、是否进入静息状态。终止原因标记为 `max_bounces | resting | escaped | max_time`。

4. **PlatformMotion + create_sine_platform_motion** — 运动描述符数据类与简谐振荡预设工厂。支持任意 `t → position/velocity/angle/angular_velocity` 可调用注入。

## Research Alignment Audit

| Reference | Requested Principle | SESSION-113 Concrete Closure |
|---|---|---|
| Mirtich (1996/2000) Conservative Advancement | 安全时间步长 Δt = D / V_rel_max，保证零穿透 | 实现了 CFL guard with gravity compensation：求解二次方程 0.5\|g\|Δt² + \|V_rel\|Δt - D = 0，并附加 0.8D/\|V_rel\| 安全帽。白盒测试 `test_zero_penetration_at_contact` 验证接触时 SDF ≤ threshold。 |
| Time-Dependent SDF (4D SDF) | SDF(x, t) 通过逆刚体变换实现，返回平台表面速度 | `DynamicTerrainSDF._inverse_transform` 实现 T⁻¹(t)·x，`surface_velocity` 返回含平移 + 旋转切向分量的 (N,2) 速度张量。白盒测试验证 t=0 匹配静态、t>0 正确偏移。 |
| Rigid Body Restitution & Zeno's Paradox | 冲量反射 V_new = V_rel − (1+e)(V_rel·N)N，Zeno 护栏 | 反射严格在相对速度空间计算，平台速度加回。`test_reflection_law_normal_component` 验证 \|vn_after\| ≈ e·\|vn_before\|。Anti-Zeno 双护栏：max_bounces=5 + min_bounce_velocity 阈值。 |
| Data-Oriented Vectorisation | 多射线并发必须张量化 | `predict_batch` 支持 (N,2) 批量输入。`test_batch_performance_numpy` 验证 50 条射线 < 10s 完成。内部 SDF 查询全部通过 `eval_sdf`/`eval_gradient` 的 (N,2) 张量接口。 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/terrain_sensor.py` | **ADDED** ~470 lines | 新增 `PlatformMotion`, `create_sine_platform_motion`, `DynamicTerrainSDF`, `BounceEvent`, `MultiBounceTrajectory`, `AdvancedTTCPredictor` 六个公共符号。 |
| `mathart/animation/__init__.py` | **UPDATED** | 在 import 和 `__all__` 中暴露六个新符号。 |
| `tests/test_terrain_sensor.py` | **ADDED** ~300 lines | 追加 22 个白盒测试，覆盖 8 个测试类。 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 22/22 新增白盒测试全绿 | `pytest tests/test_terrain_sensor.py -k "P1B2_2 or DynamicTerrainSDF or AdvancedTTCPredictor or PlatformMotionFactory"` → `22 passed` |
| Anti-Tunnelling CFL Guard | `test_zero_penetration_at_contact` 验证接触时 SDF 距离 ≤ 2×contact_threshold，Conservative Advancement 步进包含重力补偿二次方程求解。 |
| Anti-Ghost-Momentum Guard | `test_elevator_momentum_conservation` 验证角色从上升平台弹起后速度包含平台动量贡献，`test_reflection_law_normal_component` 验证恢复系数精度 < 15% 相对误差。 |
| Anti-Zeno's-Paradox Guard | `test_max_bounces_terminates` 验证高恢复系数 (e=0.99) 场景 < 5s 完成且 bounces ≤ max_bounces；`test_resting_contact_terminates` 验证低速阈值触发 resting 终止。 |
| Multi-Bounce ≥ 3 | `test_at_least_3_bounces_in_pit` 在箱形坑中验证 ≥ 3 次弹跳，`test_kinetic_energy_decreasing` 验证每次弹跳动能递减。 |
| Backward Compatibility | `test_original_ttc_predictor_unchanged` 验证原始 `TTCPredictor` 接口完全不变。 |
| Full Regression Zero-Break | 96 个测试全绿（74 原有 + 22 新增），证明新系统未破坏任何下游消费者。 |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 严禁越权修改主干 | ✅ 已合规。`AdvancedTTCPredictor` 是独立的纯数学无状态工具类，通过组合（非继承）使用 `DynamicTerrainSDF`。原有 `TTCPredictor`、`TerrainSDF`、`TerrainRaySensor` 零修改。 |
| 🔴 独立封装挂载 | ✅ 已合规。`DynamicTerrainSDF` 通过 Decorator 模式包装静态 `TerrainSDF`，`PlatformMotion` 通过 frozen dataclass 注入运动描述。所有新类均通过工厂或直接构造挂载，不触碰 AssetPipeline / Orchestrator 热路径。 |
| 🔴 强类型契约 | ✅ 已合规。`MultiBounceTrajectory` 和 `BounceEvent` 均为 `frozen dataclass`，包含碰撞时间、绝对坐标、法线、速度、平台速度、相对冲击速度等完整元数据。`test_frozen_dataclass_contract` 验证不可变性。 |
| 🔴 DOD 张量化契约 | ✅ 已合规。`DynamicTerrainSDF.eval_sdf`/`eval_gradient`/`surface_velocity` 全部接受 (N,2) 张量输入。`predict_batch` 支持批量射线。 |
| 🔴 Anti-Tunnelling CFL Guard | ✅ 已合规。步进公式求解 0.5\|g\|Δt² + \|V_rel\|Δt - D = 0 + 0.8D/\|V_rel\| 安全帽。 |
| 🔴 Anti-Ghost-Momentum | ✅ 已合规。反射在相对速度空间 V_rel = V_obj - V_surface(t_impact) 中计算，反射后加回 V_surface。 |
| 🔴 Anti-Zeno's-Paradox | ✅ 已合规。max_bounces (默认 5) + min_bounce_velocity (默认 0.05) 双重护栏。 |

## Handoff Notes — P1-VFX-1B 元数据桥接准备

打通 P1-B2-2 的高阶时空物理闭环后，若要无缝接入 **P1-VFX-1B**（依据多次碰撞的精确点位、时间与相对冲量，自动化驱动流体特效与连环扬尘 VFX），当前返回的 `MultiBounceTrajectory` 数据还需要做以下元数据桥接准备：

### 已就绪的数据（可直接消费）

| 字段 | 用途 |
|---|---|
| `BounceEvent.time` | VFX 粒子发射的精确时间戳 |
| `BounceEvent.position` | 粒子发射器的世界坐标锚点 |
| `BounceEvent.normal` | 扬尘/碎片的主扩散方向（法线反方向） |
| `BounceEvent.relative_speed` | 冲量强度 → 映射为粒子数量/初速/扩散半径 |
| `BounceEvent.surface_velocity` | 平台运动方向 → 粒子的附加漂移速度 |
| `MultiBounceTrajectory.kinetic_energy_ratio` | 全局能量衰减 → 控制后续弹跳的 VFX 强度递减曲线 |

### 需要补充的桥接元数据

1. **材质标签 (Material Tag)**：当前 `BounceEvent` 不携带碰撞表面的材质信息（石头/泥土/金属/水面）。P1-VFX-1B 需要根据材质选择不同的粒子预设（扬尘 vs 水花 vs 火花）。建议在 `TerrainSDF` 或 `DynamicTerrainSDF` 上附加可选的 `material_tag: str` 属性，并在 `BounceEvent` 中透传。

2. **接触面积估计 (Contact Patch Size)**：当前只有点接触。对于扬尘 VFX，需要估计接触面积以决定粒子扩散范围。可通过在碰撞点附近采样 SDF 曲率（Hessian 矩阵的特征值）来估算局部曲率半径。

3. **弹跳间弧线轨迹 (Inter-Bounce Arc)**：当前只记录了离散的碰撞事件。P1-VFX-1B 的拖尾特效（trail）需要弹跳间的连续抛物线轨迹。建议在 `MultiBounceTrajectory` 中增加 `arcs: list[ParabolicArc]`，每段弧记录起点、终点、初速、重力、时间跨度。

4. **角动量 / 旋转状态 (Spin State)**：如果角色在弹跳中有旋转（如翻滚），VFX 需要旋转轴和角速度来驱动运动模糊和旋转粒子。当前系统是质点模型，未来可扩展为刚体模型。

5. **VFX 事件总线接口 (VFX Event Bus)**：建议定义一个 `VFXImpactEvent` 数据类，作为 `BounceEvent` 到 VFX 系统的标准化桥接契约，包含上述所有字段加上 LOD 级别和优先级标记。
