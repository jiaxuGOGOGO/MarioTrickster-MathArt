# SESSION-037 Research — Distance Matching & Transient Phase Control

## Trigger

用户明确要求开启研究协议，并指定两组北极星参考：

1. **Epic / Paragon / UE5 Distance Matching**：以空间距离而非时间驱动 jump 与 fall。
2. **Sebastian Starke — Neural State Machine for Character-Scene Interactions (SIGGRAPH Asia 2019)**：以目标驱动动作进度刻画非周期动作，特别是受击恢复。

目标是在现有 **UMR** 总线上，将 `jump`、`fall`、`hit` 从 legacy time-sliced 动作改造成可随物理条件自适应的瞬态相位系统。

## Browser Findings Saved So Far

### 1. Unreal Engine 官方 Distance Matching 文档

来源：<https://dev.epicgames.com/documentation/en-us/unreal-engine/distance-matching-in-unreal-engine>

已确认的直接可落地原则：

- Distance Matching 的核心不是“按时间播动画”，而是：**用角色到目标的距离变量去索引动画姿态**。
- 动画序列需要有一条 **distance curve**，运行时用当前距离变量查询对应姿态。
- 官方示例明确使用 **Z 轴高度变化** 来做距离匹配，并举了 **Distance to Ground** 的例子。
- Sequence Evaluator 在运行时并不依赖时间阈值推进关键帧，而是由 `Distance Match to Target` 根据距离选择显式时间位置。
- 文档明确指出：距离匹配后，**落地动画会根据地面接近速度动态调整**，无需为了不同下落速率重新做动画，这正是本轮要解决的“太空步”问题。

对本项目的直接启发：

- `jump` 不应再由固定 `t` 分段控制，而应由**到 Apex 的垂直距离**映射成瞬态相位。
- `fall` 不应再由固定 `t` 分段控制，而应由**到 Ground 的距离**映射成瞬态相位。
- UMR 的 `phase` 字段虽然原本为统一归一化相位，但对非周期动作应允许承载 **distance-driven transient phase**，并把原始距离量保存在 metadata 中供审计和 Layer 3 评估使用。

### 2. Neural State Machine 公共摘要页

来源：<https://www.research.ed.ac.uk/en/publications/neural-state-machine-for-character-scene-interactions/>

已确认的直接可落地原则：

- 论文把角色控制定义为：**给定高层指令（goal location, action to be launched），系统生成一系列 movements and transitions，使角色到达目标状态**。
- 该方法同时处理 **periodic** 与 **non-periodic** motions，并强调要对场景几何和运行时目标进行精确适配。
- 运行时控制通过 **egocentric inference + goal-centric inference** 提高到达目标状态的精度。

对本项目的直接启发：

- `hit` 本质上不是循环 gait，而是一个**单向恢复过程**：从能量注入后的极值状态出发，逐步衰减并回到平衡。
- 因此 `hit` 相位应被建模为 **goal-progress / recovery-progress**，而不是像 walk/run 那样的循环相位，也不是固定时间切片。
- 在 UMR 中，`hit` 的 `phase` 可定义为一个 **单调衰减或单调恢复的 transient progress scalar**，同时保留 `impact_energy`、`recovery_velocity`、`stability_score` 等 metadata，供物理滤镜和三层进化循环使用。

## Preliminary Landing Hypothesis

基于现有证据，本轮实现方向应为：

1. 为 `jump` / `fall` / `hit` 引入 **Transient Phase Model**，区别于 walk/run 的循环 phase。
2. 在 UMR metadata 中新增：
   - `phase_kind`: `cyclic` / `distance_to_apex` / `distance_to_ground` / `hit_recovery`
   - `distance_to_apex`
   - `distance_to_ground`
   - `apex_height`
   - `ground_clearance`
   - `impact_energy`
   - `recovery_progress`
3. 在 `presets.py` 与 `phase_driven.py` 之间建立兼容层：对外继续保留旧 API，对内改成 UMR 驱动的距离/目标进度相位。
4. 在 Layer 3 / evolution loop 中，新增对 transient phase 的可审计特征，避免继续把 jump/fall/hit 当成 time-sliced 伪周期动作处理。

## Next Deep-Reading Targets

1. 继续补充 Epic / Paragon / Jay Hosfelt 资料，提取 jump apex、fall landing、distance curve 与单帧驱动的更细实现要点。
2. 深读 Neural State Machine 可公开材料，进一步提炼“动作启动—逼近目标—到达目标状态”的可编码 progress 结构。
3. 回读仓库中的 `presets.py`、`cli.py`、`pipeline.py` 和相关测试，把新 transient phase 模型接入 UMR 主干与 CLI 旁路入口。

## Browser Findings — Round 2

### 3. Starke `AI4Animation` — Local Motion Phases README

来源：<https://github.com/sebastianstarke/AI4Animation/blob/master/AI4Animation/SIGGRAPH_2020/ReadMe.md>

已确认的直接可落地原则：

- Local Motion Phases 的核心价值是为 **asynchronous character movements** 建模，也就是不同身体局部、不同接触点、不同技能子动作不必强行共享同一个全局周期相位。
- README 明确指出该技术可增强篮球中的运球、投篮、急转与冲刺等复杂技能，并且适用于多接触和动态动作，不局限于传统周期 locomotion。
- 文档还强调 local phase 不一定绑定神经网络，它也可以增强其他动画系统中对“相似动作聚类与控制”的能力。

对本项目的直接启发：

- 对 `jump`、`fall`、`hit` 这类瞬态动作，UMR 中不应只保留一个“像 walk/run 那样的循环 phase”。
- 更合理的做法是：允许 **全局 transient phase + 局部 metadata/局部进度量** 共存，例如：
  - 全局：`phase`
  - 局部：`distance_to_ground`、`distance_to_apex`、`impact_energy`、`recovery_progress`
  - 局部事件：`is_apex_window`、`is_landing_window`、`is_recovery_window`
- 这与现有 UMR 的 metadata 可扩展槽位完全兼容，也能让未来 motion matching / hold-frame / Layer 3 诊断直接读取局部进度，而不必再从原始时间反推。

### 4. SIGGRAPH History — Neural State Machine 条目

来源：<https://history.siggraph.org/learning/neural-state-machine-for-character-scene-interactions-by-starke-zhang-komura-and-saito/>

已确认的直接可落地原则：

- 官方摘要再次强调：系统的目标是让角色完成 **goal-driven actions with precise scene interactions**。
- 给定目标位置和待执行动作后，系统生成 **movements and transitions**，并最终达到 **desired state**。
- 该框架同时处理 **periodic and non-periodic motions reacting to scene geometry**。

对本项目的直接启发：

- `hit` 的正确抽象不应是“受击动画时间播到第几帧”，而应是“角色离恢复平衡这个目标状态还有多远”。
- 因此 `hit` 的相位可以定义为 **由高到低单调衰减的 recovery deficit**，或等价定义为 **由低到高单调增长的 recovery progress**；这两者都比固定时间切片更贴近目标驱动控制。
- 结合用户需求，本轮更适合把 `phase` 统一为 **[0,1] 的瞬态相位**，并在 metadata 中额外记录：
  - `phase_source = distance_matching | action_goal_progress`
  - `goal_progress`
  - `stability_recovery`
  - `impact_decay`

## Consolidated Deep-Reading Conclusion So Far

到目前为止，Epic 的 Distance Matching 与 Starke 系列工作已形成统一结论：

| Reference | Core Principle | Direct Landing in MarioTrickster-MathArt |
|-----------|----------------|------------------------------------------|
| **UE Distance Matching** | 动画姿态应由目标距离驱动，而不是由时间驱动 | `jump` 用 `distance_to_apex` 映射瞬态相位，`fall` 用 `distance_to_ground` 映射瞬态相位 |
| **Neural State Machine** | 非周期动作应围绕目标状态推进，而不是按固定时间片播放 | `hit` 用单向恢复进度 / 恢复缺口建模 |
| **Local Motion Phases** | 不同身体局部与接触过程可拥有不同步的局部进度语义 | UMR metadata 中显式保留 transient progress 与局部事件窗口 |

这说明本轮实现不只是给 `presets.py` 换一组公式，而是要把 **time-driven legacy jump/fall/hit** 升级成 **distance / goal-progress driven transient phases**，并把这些量写入 UMR、CLI 入口、导出审计与 Layer 3 特征桥接中。

## Implementation Blueprint for SESSION-037

基于当前仓库代码现状，可以把本轮落地点明确为四个收束层面。

| Landing Surface | Current Problem | SESSION-037 Upgrade |
|-----------------|----------------|---------------------|
| `mathart/animation/presets.py` | `jump` / `fall` / `hit` 仍是固定时间切片公式 | 保留原函数名，但改为委托到新的 transient-phase 驱动器 |
| `mathart/animation/phase_driven.py` | 已强于 walk/run，但缺少 jump/fall/hit 的 distance/progress 相位模型 | 新增 jump/fall/hit UMR-native frame emitters 与 transient phase 计算器 |
| `mathart/animation/unified_motion.py` | UMR 能承载 metadata，但没有 transient phase 规范字段 | 约定 `phase_kind`、`phase_source`、`distance_to_apex`、`distance_to_ground`、`recovery_progress` 等字段 |
| `mathart/animation/cli.py` | 仍直接走 legacy preset 函数，没有显式 UMR 语义 | 通过保持 preset 兼容 API，让 CLI 自动获得新 transient phase 行为；必要时补充参数或注释说明 |

### A. Jump — Distance-to-Apex Phase

`jump` 应拆成两个并存量：

1. **物理语义量**：`distance_to_apex`
2. **动画查询量**：`phase ∈ [0,1]`

建议定义：

- `distance_to_apex = apex_height - current_height`
- 归一化时不再使用原始时间 `t`，而是使用 `distance_to_apex / max_apex_height`
- 在起跳并逼近 apex 的过程中，`distance_to_apex` 从大到小趋近 0
- 为了兼容统一的 `[0,1]` 相位，可令：
  - `jump_phase = 1 - clamp(distance_to_apex / apex_height, 0, 1)`
- 这样：
  - 刚离地时 `jump_phase ≈ 0`
  - 接近 apex 时 `jump_phase ≈ 1`

动作姿态上：

- 低相位：保留起跳伸展、双臂后摆到上举的趋势
- 高相位：过渡到 apex spread / stabilization pose
- metadata 记录：`phase_kind=distance_to_apex`、`distance_to_apex`、`apex_height`、`vertical_velocity`

### B. Fall — Distance-to-Ground Phase

`fall` 的控制核心应是角色离地面的距离，而不是“已经下落多久”。

建议定义：

- `distance_to_ground = max(current_height - ground_height, 0)`
- 使用可审计的 `fall_reference_height` 做归一化
- `fall_phase = 1 - clamp(distance_to_ground / fall_reference_height, 0, 1)`

这样：

- 刚进入下落时，距地面远，`fall_phase ≈ 0`
- 越接近地面，`fall_phase` 越接近 1
- 落地前窗口可用 `distance_to_ground <= landing_threshold` 或 `fall_phase >= landing_phase_threshold` 触发

动作姿态上：

- 低相位：保持伸展/失重下落姿态
- 中高相位：逐渐进入防冲击预备姿态，膝髋开始预弯曲
- 接近 1.0：进入 landing-ready / impact-prep，而不是等到时间末端再突然 spring squash

metadata 记录：`phase_kind=distance_to_ground`、`distance_to_ground`、`ground_height`、`fall_reference_height`、`is_landing_window`

### C. Hit — Action Goal Progress / Recovery Deficit

受击应被视为“能量注入后逐渐恢复平衡”的单向过程。

建议同时保留两个等价量：

- `impact_deficit`: 当前离稳定平衡状态还有多远
- `recovery_progress`: 已恢复了多少

用户指定的语义是：

- `1.0` 代表受击极限僵直
- `0.0` 代表恢复平衡
- 到达 0 后自动截断

因此最直接的 UMR `phase` 定义可为：

- `hit_phase = clamp(exp(-damping * elapsed_like_input)), 0, 1)` 的缺口版本
- 但为了摆脱纯时间依赖，本项目更适合把输入量命名为 **recovery signal**，它可来自物理层的稳定性、速度衰减、姿态偏差或简化后的代理量
- 对外如果暂时还只能接受 `t`，则也应把 `t` 解释为“恢复驱动输入”，并在 metadata 中明确：`phase_source=action_goal_progress_proxy`

建议在代码中统一提供：

- `hit_recovery_phase(progress_driver, damping)`
- 输出：
  - `phase = impact_deficit`
  - `recovery_progress = 1 - phase`

动作姿态上：

- 高 phase：脊柱后仰、胸廓反扭、头部后甩、肩膀抬起
- 随着 phase 衰减：逐步回正，不再使用带回弹振铃的 spring 作为主导时间曲线

metadata 记录：`phase_kind=hit_recovery`、`impact_energy`、`impact_deficit`、`recovery_progress`、`stability_score`

### D. UMR / Layer 3 / Audit Integration

为了让“三层进化循环”真正能消费本轮升级，而不是只让渲染层变漂亮，必须同步落地以下约束：

1. **UMR frame metadata 固化**：每个 `jump` / `fall` / `hit` 帧都要写入 transient phase 字段。
2. **Layer 3 evaluator bridge**：为评估器补充读取 transient phase 的入口，使后续进化、知识蒸馏与诊断都能直接读取这些量。
3. **Pipeline audit**：导出 manifest / `.umr.json` 时，要显式写出 `phase_kind` 覆盖率与状态级 motion contract。
4. **Todo update**：将“legacy time-driven jump/fall/hit 已被收束”为已完成项，同时把更进一步的真实物理驱动输入、raycast ground sensing 等作为后续待办。

### Concrete Code Targets for Next Phase

下一阶段应优先改动：

| Priority | File | Planned Change |
|----------|------|----------------|
| P0 | `mathart/animation/phase_driven.py` | 新增 transient phase 计算器与 `phase_driven_jump/fall/hit` + frame emitters |
| P0 | `mathart/animation/presets.py` | 让 `jump_animation/fall_animation/hit_animation` 全部委托给新驱动器 |
| P0 | `tests/` | 新增 transient phase 回归测试，验证距离/恢复相位的单调性与 UMR metadata |
| P1 | `mathart/animation/cli.py` | 审核并必要时最小更新，确保 CLI 入口不再绕开新语义 |
| P1 | `mathart/animation/motion_matching_evaluator.py` / `mathart/pipeline.py` | 扩展对 transient phase metadata 的提取与导出审计 |
