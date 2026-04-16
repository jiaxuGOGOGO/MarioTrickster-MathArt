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


## Browser Findings — SESSION-038 Refinement Pass

### 5. Unreal Engine Distance Matching 文档补充细节

来源：<https://dev.epicgames.com/documentation/en-us/unreal-engine/distance-matching-in-unreal-engine>

本轮新增确认的实现细节：

- Distance Matching 的运行时核心不是推进时间，而是用 **distance variable 直接查询 animation curve 上的 pose**。
- 官方工作流要求在动画序列上生成 **distance curve**，并在运行时通过 **Sequence Evaluator + Distance Match to Target** 以显式时间定位姿态。
- 文档明确指出：跳帧选择姿态的阈值单位应从“时间”切换为“距离”；也就是说动画帧序只是被查询的索引空间，而不是推进控制器。
- 对下落示例，官方直接使用 **Distance to Ground** 变量，并强调当角色下落速度变化时，动画无需重做，序列会根据接近地面的速度与距离自动调整。
- 该设计进一步支持本项目把 `jump` 与 `fall` 的 UMR `phase` 定义为距离驱动的瞬态进度，同时把原始距离量保存在 metadata 中供 Layer 3 和审计消费。

直接落地约束：

1. `jump` / `fall` 不应再把 `t` 作为一等公民；`t` 最多只能用于生成分析代理量，而不能成为动作语义本身。
2. UMR 中要把 **distance curve semantics** 明确为 `phase_source=distance_matching`，并保留 `distance_to_apex` / `distance_to_ground` 等可审计字段。
3. 如果现有代码还在用线性时间切片拼接 jump/fall pose，必须进一步收束为“距离查姿态”的统一合同。

### 6. Overgrowth / Rosen 程序动画资料入口的当前收获

来源：<https://media.gdcvault.com/GDC2014/Presentations/Rosen_David_Animation_Bootcamp_An.pdf>

本轮浏览时 PDF 页面未能直接抽取正文，但已确认该资料是可公开访问的 GDC 幻灯入口，可作为本轮 hit 恢复建模的辅助参考。结合用户指定的论文与 Rosen 的长期公开主张，可以先固化一个工程上可靠的解释边界：

- 受击恢复不应是固定时长动画，而应是**受力后回到稳定状态的控制过程**。
- 对工程落地而言，最稳妥的统一表达是 **单向衰减的 deficit + 单向增长的 recovery progress**。
- 若引入弹簧模型，必须选择 **临界阻尼或接近临界阻尼**，避免 underdamped 振铃破坏“卡肉感”和动作交还的可控性。
- 这与 SESSION-035 中“physics is follower, not leader”的原则一致：受击相位变量可以是控制量，但不能让低层纠偏把高层 intent 重新抖成另一段动画。

因此，本轮 refined hit 设计应从单纯的指数衰减，升级为：

- 一个显式的 `TransientPhaseVariable` 抽象；
- 支持 `critical_damping` / `damping_ratio` / `half_life` 等控制参数；
- 输出 `impact_deficit`, `recovery_progress`, `recovery_velocity`；
- 在缺口接近 0 时自动 clamp 并把控制权平滑交还给 run/idle 的循环相位系统。


### 7. SIGGRAPH History — Neural State Machine 关键表述补充

来源：<https://history.siggraph.org/learning/neural-state-machine-for-character-scene-interactions-by-starke-zhang-komura-and-saito/>

本轮再次确认的关键原句级语义包括：

- 系统目标是让角色实现 **goal-driven actions with precise scene interactions**。
- 难点在于动作需要同时处理 **periodic and non-periodic motions**，并且必须对场景几何作出精确反应。
- 给定高层目标位置和动作，系统会计算一系列 **movements and transitions to reach the goal in the desired state**。
- 为了提升运行时到达目标的精度，方法显式结合 **egocentric inference** 与 **goal-centric inference**。

对本项目 refined transient phase 的新增约束：

- `hit` 不应只被表示成“受击后剩余多少动画时间”，而应被表示成“距离 desired stable state 还有多远”。
- `jump` / `fall` 除了距离本身，还应尽量保留 `target_state` 语义，例如 `is_apex_window`、`is_landing_window`、`desired_contact_state`，这样后续三层进化与 motion matching 才能真正围绕目标状态优化。
- UMR metadata 除了 progress scalar，还应保留**状态到达语义**，例如 `target_state`, `recovery_velocity`, `contact_expectation`。

### 8. AI4Animation — Local Motion Phases 关键补充

来源：<https://github.com/sebastianstarke/AI4Animation/blob/master/AI4Animation/SIGGRAPH_2020/ReadMe.md>

本轮新增确认的关键点：

- Local Motion Phases 的明确目标是学习 **multi-contact character movements**。
- README 直接强调该方法能增强 **asynchronous character movements**，例如篮球中的运球、投篮、急停转向等技能。
- 文档指出 local phase 技术**不严格依赖神经网络**，也可以增强其他动画系统中对相似动作的聚类和控制。
- 这意味着本项目当前基于解析式与规则式的 UMR 系统，完全可以先吸收 local phase 的“表达结构”，不必等待神经网络 runtime 才能落地。

对本项目 refined transient phase 的新增约束：

1. 对 `jump` / `fall` / `hit` 不应只记录一个全局 `phase`，还应保留与接触、恢复、目标状态相关的 **局部事件与局部速度量**。
2. Layer 3 不仅要读 `phase`，还应能够读 `recovery_velocity`、`contact_expectation`、`apex_window` / `landing_window` 等局部进度信号，以便后续蒸馏与 runtime transition synthesis 使用。
3. 这进一步支持把 `TransientPhaseVariable` 设计成一个小型控制对象，而不是单一标量函数返回值。

## SESSION-038 Preliminary Refined Conclusion

在 SESSION-037 已经完成“去时间化”第一步之后，本轮更深入的研究说明：

| Refined Direction | SESSION-037 Baseline | SESSION-038 Needed Upgrade |
|------------------|----------------------|----------------------------|
| **Jump/Fall** | 已改为 distance-driven scalar phase | 进一步强化为接近工业 Distance Curve 语义：保留 target distance、接触预期与窗口事件，而不是只有归一化 phase |
| **Hit** | 指数衰减 deficit/progress | 升级为 `TransientPhaseVariable`，支持临界阻尼参数、恢复速度与稳定状态回交语义 |
| **Layer 3 / Evolution Loop** | 已能读取基本 transient metadata | 继续扩展为读取目标状态、恢复速度、接触期望等更丰富的非周期动作上下文 |

因此，下一阶段代码设计不应只“微调现有函数参数”，而应确认是否需要把 `hit` 的恢复变量从简单指数衰减提升为**临界阻尼控制量**，并把 jump/fall/hit 的 metadata 进一步标准化为更接近 distance curve / target-state manifold 的结构。


### 9. Critical Damping 工程参考补充

来源：<https://theorangeduck.com/page/spring-roll-call>

本轮新增确认的工程级结论如下。

| 结论 | 对本项目的直接意义 |
|------|--------------------|
| **Critical spring damper** 是“**最快趋近目标且不产生额外振荡**”的特例 | `hit` 的恢复变量若要保留卡肉感并避免反复回弹，应优先采用临界阻尼或接近临界阻尼，而不是欠阻尼弹簧 |
| 该类更新天然需要同时维护 **位置/状态值 `x`** 与 **速度 `v`** | `TransientPhaseVariable` 不能只是单一 `phase` 标量；至少还应维护 `recovery_velocity` 或等价速度状态 |
| `halflife` 可作为比“裸 damping 常数”更易调参的控制参数 | 代码实现中可优先支持 `halflife`，再映射到内部阻尼系数，降低未来蒸馏与自动调参门槛 |
| 存在 `decay_spring_damper_exact(x, v, halflife, dt)` 这类直接衰减到零的精确更新形式 | 对本项目 hit 恢复尤为适合，因为稳定平衡本身就可被抽象为零缺口目标 |

与用户本轮要求结合后，可得到更具体的 hit 设计约束：

1. `impact_deficit` 可以作为 `x`；
2. `recovery_velocity` 作为 `v`；
3. 受击瞬间设置 `impact_deficit = 1.0` 并注入初始恢复速度；
4. 每帧用临界阻尼更新，得到单调趋近 0 的 deficit 与单调趋近 1 的 `recovery_progress = 1 - impact_deficit`；
5. 当 deficit 足够接近 0 时 clamp，并把控制权交回 run/idle 等循环 phase 系统。

## Deep-Reading Stop Condition Check

截至当前，已有多组强资料对同一工程结论形成交叉支持：

| Topic | Strong Sources | Stable Conclusion |
|-------|----------------|------------------|
| **Jump/Fall should not be time-driven** | UE Distance Matching 官方文档 + 用户指定 Paragon/Distance Matching 方向 | 用距离变量查询姿态；时间只可作为代理量，不可作为动作本体语义 |
| **Hit is goal/recovery process, not cyclic playback** | Neural State Machine 摘要 + Local Motion Phases README + 用户指定 Overgrowth/程序动画方向 | 用目标状态恢复缺口与恢复进度建模，而不是固定时间切片 |
| **Critical damping is the right recovery regime** | 用户要求 + game animation spring engineering reference | 用临界阻尼或近临界阻尼，保留速度状态，快速收敛且不振铃 |

因此，研究协议在本轮已经达到“下一步代码改造已清晰”的停止条件，可以进入 refined transient phase 结构设计阶段。


## SESSION-038 Refined Implementation Blueprint

基于当前代码基线，`jump_distance_phase` 与 `fall_distance_phase` 已经完成第一层“去时间化”，但仍偏向把距离量压缩成单一 `phase` 后再驱动姿态；`hit_recovery_phase` 则仍是指数衰减代理，还没有升级为真正的临界阻尼状态变量。因此，本轮设计目标不是推翻 UMR，而是在 **不破坏 SESSION-036 / 037 统一总线** 的前提下，把瞬态相位从“距离标量 + pose 函数”进一步升级为“**显式状态变量 + 更丰富目标状态 metadata + 更强 Layer 3 可消费性**”。

| 模块 | 当前状态 | 本轮 refined 目标 |
|------|----------|-------------------|
| `phase_driven.py::jump_distance_phase` | 已输出 `distance_to_apex`、`is_apex_window` | 增加更明确的 `target_state` / `contact_expectation` / `distance_window` 语义，并尽量让 pose 计算更多依赖这些语义变量而不是裸 `t` |
| `phase_driven.py::fall_distance_phase` | 已输出 `distance_to_ground`、`is_landing_window` | 增加 `landing_preparation`、`contact_expectation`、`target_distance` 语义，强化“接近落地时 brace”的工业式距离曲线含义 |
| `phase_driven.py::hit_recovery_phase` | 指数衰减 deficit | 升级为 `TransientPhaseVariable` / `CriticalDampedTransientPhase`，显式维护 deficit 与 velocity，并输出 `recovery_velocity` |
| `pipeline.py` | jump/fall/hit 已通过 UMR frame 进入主干 | 主干继续保持 UMR 收束，但为 hit 增加临界阻尼参数和审计字段；为 jump/fall 增加目标状态 metadata 写回 |
| `motion_matching_evaluator.py` | 已读取基础 transient metadata | 扩展读取 `recovery_velocity`、`contact_expectation`、`target_state`、`landing_preparation` 等字段，为三层进化和蒸馏准备 |
| `tests/test_unified_motion.py` | 已验证基础 transient phase 单调性和导出链路 | 新增对临界阻尼恢复、速度状态、窗口语义与尾帧 clamp 的断言 |

### Jump / Fall refined semantics

`jump` 的核心不是“时间过去了多少”，而是“距离理想 apex 状态还有多远”。因此保留 `phase = normalized_distance_progress` 仍然有价值，但它只能被视为**视图字段**，真正的一等公民应是 `distance_to_apex`、`apex_height`、`vertical_velocity` 与 `target_state='apex'`。与此同时，`fall` 的一等公民则应是 `distance_to_ground`、`fall_reference_height`、`target_state='ground_contact'` 与 `landing_preparation`。`phase` 继续保留给 UMR 兼容层与已有下游使用，但在 metadata 中应补齐更接近工业 Distance Curve 的目标状态语义。

### Hit refined semantics

`hit` 应从“输入一个 `t`，返回一个指数衰减 phase”升级为“维护一个受力恢复控制变量”。这个变量可记为 `impact_deficit`，其稳定目标是 0；同时维护 `recovery_velocity` 作为一阶速度状态。受击瞬间，把 `impact_deficit` 设为 1.0，并允许通过 `impact_velocity` 或等价参数注入一个初始恢复速度。随后使用临界阻尼精确更新或工程近似更新，使 deficit **快速、单调、无振铃** 地逼近 0。对外语义上，仍保留用户要求的定义：`phase = impact_deficit`，`recovery_progress = 1 - impact_deficit`。但与 SESSION-037 不同，本轮会让 `recovery_velocity` 成为一等元数据，从而让 Layer 3、知识蒸馏与未来自我迭代测试能识别“正在恢复但尚未稳定”和“已经回稳”之间的差别。

### UMR metadata contract refinement

本轮推荐将三类瞬态动作的 metadata 收束为下表所示的统一风格。

| Field | Jump | Fall | Hit | 说明 |
|------|------|------|-----|------|
| `phase_kind` | `distance_to_apex` | `distance_to_ground` | `hit_recovery` | 保持不变，兼容既有审计 |
| `phase_source` | `distance_matching` | `distance_matching` | `critical_damped_recovery` | hit 的来源语义升级 |
| `target_state` | `apex` | `ground_contact` | `stable_balance` | 直接表达动作的目标状态 |
| `contact_expectation` | `airborne`/`apex_window` | `airborne`/`landing_window` | `planted_recovery` | 为 Layer 3 和后续 IK/接触纠偏提供低耦合提示 |
| `distance_to_apex` / `distance_to_ground` | 必填 | 必填 | 0 | 保留工业式 distance curve 语义 |
| `impact_deficit` | 0 | 0 | 必填 | hit 专属恢复缺口 |
| `recovery_velocity` | 0 | 可选 | 必填 | 允许识别阻尼恢复速度 |
| `landing_preparation` | 可选 | 必填 | 0 | 表示落地缓冲程度 |
| `window_signal` | `is_apex_window` | `is_landing_window` | `is_recovery_complete` | 用于非周期动作事件检测 |

### Evolution-loop integration refinement

三层进化循环在本轮不需要推翻，只需要把新的 transient metadata 纳入统一进化输入。Layer 1 仍负责生成基础 pose；Layer 2 继续负责局部物理与接触纠偏；Layer 3 评分器与蒸馏器则需要开始把 `target_state`、`contact_expectation`、`recovery_velocity` 视作可学习、可审计、可回归的控制特征。这样一来，未来无论用户再补充 Paragon、Overgrowth 还是 Starke 系列资料，都能直接被蒸馏进同一个 UMR 合同里，而不会再次出现 legacy adapter 横插主干的问题。

结论上，下一步代码改造的最小闭环应是：**引入临界阻尼 transient 变量类；让 hit 使用该变量生成 deficit/progress/velocity；让 jump/fall metadata 增强目标状态与接触预期字段；同步更新 Layer 3 提取器与回归测试。**
