# SESSION-038 审计：Refined Transient Phase 攻坚落地核对

本轮审计围绕三项主张逐项核对：其一，**Jump 不再以时间为主导，而以 Distance-to-Apex 驱动**；其二，**Fall 不再以时间为主导，而以 Distance-to-Ground 驱动并显式输出落地准备度**；其三，**Hit 不再只是指数衰减代理，而升级为带速度状态的临界阻尼恢复变量**。审计方法包括源码核对、回归测试与真实 UMR 导出产物验证。

| 审计对象 | 研究主张 | 代码落地位置 | 审计结果 |
|---|---|---|---|
| Jump transient phase | `phase = f(distance_to_apex)`，apex 为目标状态 | `mathart/animation/phase_driven.py::jump_distance_phase`、`phase_driven_jump_frame` | 通过 |
| Fall transient phase | `phase = f(distance_to_ground)`，ground contact 为目标状态 | `mathart/animation/phase_driven.py::fall_distance_phase`、`phase_driven_fall_frame` | 通过 |
| Hit transient phase | `impact_deficit` 采用临界阻尼恢复，保留速度状态 | `mathart/animation/phase_driven.py::TransientPhaseVariable`、`critically_damped_hit_phase`、`hit_recovery_phase`、`phase_driven_hit_frame` | 通过 |
| Layer 3 consumption | 三层进化可直接读取 refined metadata | `mathart/animation/motion_matching_evaluator.py::extract_umr_context` | 通过 |
| 主干导出 | pipeline 真实导出 jump/fall/hit 的 refined metadata | `mathart/pipeline.py` + `session038_audit_output/.../*.umr.json` | 通过 |
| 回归保护 | monotonicity、metadata contract、真实导出链路 | `tests/test_unified_motion.py` | 通过 |

## 一、源码层审计

本轮在 `phase_driven.py` 中新增了显式的 **`TransientPhaseVariable`**，并通过 `critically_damped_hit_phase()` 把 hit 状态建模为一个**向 0 收敛的临界阻尼缺口变量**。这使 `impact_deficit` 不再只是单帧公式，而是具备 `deficit`、`deficit_velocity`、`recovery_velocity`、`recovery_progress` 等成体系的恢复语义。对 jump/fall，则保持与 SESSION-037 一致的 UMR 主干收束，但补充了 `target_state`、`contact_expectation`、`window_signal`、`distance_window`、`target_distance`、`landing_preparation` 等更接近工业控制图语义的字段。

| 文件 | 关键改动 | 目的 |
|---|---|---|
| `mathart/animation/phase_driven.py` | 新增 `TransientPhaseVariable`、`critically_damped_hit_phase`，升级 jump/fall/hit metadata | 让非循环动作以显式瞬态变量进入 UMR |
| `mathart/animation/motion_matching_evaluator.py` | 扩展 `extract_umr_context()` | 让 Layer 3 可直接消费 `target_state`、`contact_expectation`、`recovery_velocity`、`landing_preparation` |
| `mathart/pipeline.py` | hit 主干显式传入 `half_life=0.18` | 把临界阻尼恢复参数接入真实导出路径 |
| `mathart/animation/__init__.py` | 导出新 transient API | 便于测试、主干与后续会话复用 |
| `tests/test_unified_motion.py` | 增加 refined transient 断言 | 防止 future regression |

## 二、回归测试审计

执行 `pytest -q tests/test_unified_motion.py`，结果为 **6/6 通过**。这意味着 UMR 合同、jump/fall 的距离驱动语义、hit 的临界阻尼恢复语义，以及最小角色导出链路在当前代码库中均未被破坏。

> 结果摘要：`6 passed in 0.67s`

## 三、真实导出产物审计

使用 `tools/session038_audit_probe.py` 生成 `session038_probe` 角色包，对 jump、fall、hit 的 UMR 导出文件进行逐帧核对。审计重点不是函数返回值，而是**真实主干产物中是否已经写入 refined transient metadata**。

### 1. Jump 审计

`session038_probe_jump.umr.json` 显示，jump 首帧 `phase=0.0`、`distance_to_apex=0.18`，末帧 `phase=1.0`、`distance_to_apex=0.0`。metadata 中持续写入 `phase_kind=distance_to_apex`、`target_state=apex`、`contact_expectation` 从 `airborne` 过渡到 `apex_window`，并在 apex 帧产生 `window_signal=true`。这与“用到达最高点的空间距离驱动姿态”的工业要求一致。

### 2. Fall 审计

`session038_probe_fall.umr.json` 显示，fall 首帧 `distance_to_ground=0.22`，末帧收敛到 `0.0`；`phase` 从 `0.0` 单调推进到 `1.0`。metadata 中持续写入 `phase_kind=distance_to_ground`、`target_state=ground_contact`，且 `landing_preparation` 随接近地面逐步抬升，末帧达到 `1.0`，同时 `contact_expectation` 由 `airborne` 转入 `landing_window`。这与“用离地距离铺垫落地缓冲动作”的目标一致。

### 3. Hit 审计

`session038_probe_hit.umr.json` 显示，hit 首帧 `phase=1.0`，之后依次衰减为约 `0.4266 -> 0.1032 -> 0.0210`。更关键的是，metadata 中已真实写入 `impact_deficit`、`deficit_velocity`、`recovery_velocity`、`recovery_progress`、`half_life=0.18`、`phase_source=critical_damped_recovery`、`target_state=stable_balance`。这说明 hit 已经从“单值指数代理”升级为“带恢复速度状态的临界阻尼恢复变量”。

| 产物 | 关键证据 | 结论 |
|---|---|---|
| `session038_probe_jump.umr.json` | `distance_to_apex` 单调下降，`phase` 单调上升，apex 帧 `window_signal=true` | Jump 距离匹配真实落地 |
| `session038_probe_fall.umr.json` | `distance_to_ground` 单调下降，`landing_preparation` 单调抬升，落地帧 `window_signal=true` | Fall 距离匹配真实落地 |
| `session038_probe_hit.umr.json` | `impact_deficit` 单调衰减，`recovery_velocity` 非零，`phase_source=critical_damped_recovery` | Hit 临界阻尼恢复真实落地 |

## 四、与研究主张的差距核对

当前实现已经把用户要求的**非循环瞬态相位化**接入 UMR 主干，并完成最小闭环验证。但仍有两个“下一阶段优化项”，它们属于增强项而非本轮阻塞项。

| 剩余差距 | 当前状态 | 结论 |
|---|---|---|
| Jump/Fall 的 pose 曲线仍包含少量手工艺术权重 | 已由距离变量驱动，但尚未接入更复杂的 learned distance curve | 非阻塞，保留为后续增强 |
| Hit 的临界阻尼为单变量恢复 | 已满足“能量注入与恢复”主张，但尚未细分上半身/骨盆/头部多通道冲击恢复 | 非阻塞，适合后续 Layer 3 蒸馏 |

## 五、审计结论

本轮研究内容与代码实践**已形成闭环**。Jump、Fall、Hit 现在都以 UMR 为唯一主干合同进入生成、评估与导出路径；Jump/Fall 已具备更明确的 distance-matching 目标语义，Hit 已升级为显式临界阻尼恢复变量，并能被 Layer 3 与未来知识蒸馏逻辑直接消费。下一步可把剩余的增强项写入待办，而无需回滚当前架构。
