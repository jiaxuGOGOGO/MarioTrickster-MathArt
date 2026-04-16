# SESSION-036 Research — Unified Motion Representation (UMR) Architecture Closure

## Trigger

用户明确要求“开启研究协议”，并指定以 Pixar USD `UsdSkel`、SideFX Houdini KineFX、Unreal Engine 5 AnimGraph 作为本轮架构收束的北极星参考，用于在不破坏现有三层进化的前提下建立统一运动数据总线。

## Research Framing Block

| Field | Value |
|------|-------|
| **subsystem** | `mathart.pipeline` 主干、`mathart.animation.phase_driven`、`mathart.animation.physics_projector`、Layer 3 评估与三层进化循环之间的统一运动数据总线 |
| **decision_needed** | 是否以一个强约束的 `UnifiedMotionFrame`/UMR 对象作为唯一跨模块运动中间表示，并如何定义字段、节点职责、分层边界与回写机制 |
| **already_known** | 仓库已有 USD-like `UniversalSceneDescription` 处理 level/export；phase-driven 系统已经显式管理 `phase`、左右脚接触；Layer 3 已有相位连续性、接触一致性和 skating 评分；主干仍通过状态函数直接传递裸 pose dict |
| **duplicate_forbidden** | 不重复研究 SESSION-031/032/033/035 已吸收的 PhysDiff、PFNN、DeepPhase、Motion Matching、DeepMimic、AMP、VPoser 高层结论；本轮只关注它们尚未彻底闭合的数据总线与严格分层问题 |
| **success_signal** | 来源必须给出可直接转为代码结构的统一字段、属性流、节点职责、根运动/局部关节分离、IK 限权原则或工业分层约束 |

## Existing Repository Facts

1. 最新仓库 commit：`e2cde9090aaa14a3e33ccc13a306cd908d54c171`。
2. 仓库默认分支为 `main`，在线仓库最新提交短哈希为 `e2cde90`。
3. `SESSION_HANDOFF.md` 显示当前版本为 `0.27.0`，上一轮为 SESSION-035，已交付 compliant PD、AMP、VPoser 与 convergence bridge。
4. `PROJECT_BRAIN.json` 显示当前高优先任务仍包括相位驱动全覆盖、端到端主干验证与架构闭环增强。
5. `research_session032_pdg_framing.md` 已明确给出前例：level 管线采用轻量 PDG + USD-like scene contract，而 motion 侧尚缺对等的统一 contract。

## Browser Findings Saved So Far

### 1. GitHub 仓库页

- 仓库可访问，默认分支为 `main`。
- 根目录最新提交为 **SESSION-035: Compliant Physics & Adversarial Motion Priors**。
- 在线显示当前最新 commit 短哈希为 `e2cde90`，与本地克隆一致。

### 2. OpenUSD `UsdSkel` 官方文档

- 官方将 `UsdSkel` 描述为：**用于在 DCC 图形管线中交换骨骼蒙皮网格与关节动画的 schema 与 API 基础**。
- 这直接支持本轮的核心结论：运动数据应被视为**跨工具交换契约**，而不是某个模块私有的 pose 字典。
- 从页面导航可确认其重点概念包括：**skeleton structure、animation、joint order、transform spaces、bindings**。这说明运动总线至少必须显式维护：
  - 稳定的 joint ordering / naming
  - 局部关节变换序列
  - 根/骨架层级信息
  - 与时间采样绑定的动画帧语义

## Preliminary Landing Hypothesis

本轮极可能应仿照已有 `UniversalSceneDescription`，在 motion 侧新增一个**强约束、可序列化、可审计**的 `UnifiedMotionFrame` 与 `UnifiedMotionClip`：

1. `time`
2. `phase`（归一化 [0,1]）
3. `root_transform`
4. `joint_local_rotations`
5. `contact_tags`（左脚/右脚至少二值）
6. 可选 `metadata/diagnostics`，供 Layer 3 评估和审计使用

尚需继续从 KineFX 与 UE5 AnimGraph 中提炼：

- 点属性流 / DAG filter 的节点模式
- Root motion、pose generation、IK grounding 的严格层级边界
- 低层修正不得越级破坏高层意图的工程铁律

## Browser Findings — KineFX and Unreal AnimGraph

### 3. SideFX KineFX `Compute Transform` 官方文档

SideFX 官方把 KineFX 的核心数据模型明确为**层级点（points in hierarchy）上的属性流**，而不是封装在黑盒骨骼对象里的私有状态。文档给出的最关键字段与单向变换关系如下：

- `P`：点的世界空间平移。
- `transform`：点的世界空间 3×3 变换（旋转、缩放、切变）。
- `localtransform`：点相对父节点的局部变换。
- `scaleinheritance`：控制如何继承父级缩放。
- `Compute World from Local`：由局部层级属性重算世界变换。
- `Compute Local from World`：由世界变换反推局部变换。

对本项目的直接工程启发：

1. 运动总线必须把**根运动**与**局部关节变换**分开存储；
2. 下游节点应只读/重写标准字段，而不保留隐式私有姿态状态；
3. `PhysicsProjector` / IK / 评估器都应被建模为**属性滤镜节点**，单向读取上游 `UnifiedMotionFrame` 并回写新的同构 `UnifiedMotionFrame`；
4. 若一个节点修改了世界层结果，必须通过统一规则重算局部层，反之亦然，不能让两套表示悄悄漂移。

### 4. Unreal Engine `Using Layered Animations` 官方文档

Epic 官方教程虽然是教学页面，但它揭示了 AnimGraph 非常重要的工业铁律：

1. **Locomotion State Machine 先输出基础姿态**；
2. 其结果先被保存为 **cached pose**；
3. 再用 **Layered Blend Per Bone** 只在指定骨骼子树上叠加局部动画；
4. 局部叠加遵循从指定骨骼开始的层级边界，不允许无边界污染全身；
5. 这意味着“全身 locomotion”与“局部上半身动作”在图中是显式分开的。

对 MarioTrickster-MathArt 的落地结论：

- `state intent -> base pose -> root motion extraction -> localized correction -> render/export` 必须成为主干顺序；
- 任何贴地 IK 或接触修正都只能在**足部链条与骨盆微调范围内**生效，不能越级扭动脊柱、手臂等高层表达骨骼；
- 若未来引入攻击/持枪/受击等局部动作，也应通过“按骨骼子树分层叠加”的方式在 UMR 上实现，而不是直接覆写整个 pose dict。

## Consolidated Design Direction After External Reading

到目前为止，三类参考已形成高度一致的工程结论：

| Reference | Industrial Principle | UMR Landing |
|------|----------------------|-------------|
| **UsdSkel** | 骨骼动画应作为稳定、可交换的统一 schema | `UnifiedMotionFrame` 成为唯一跨模块运动契约 |
| **KineFX** | 动画是点属性流，节点通过属性单向处理 | 物理、IK、评分器变成同构 frame/filter 节点 |
| **AnimGraph** | 基础姿态、局部叠加、根运动、IK 必须严格分层 | 建立 `intent -> phase pose -> root motion -> foot grounding -> render` 主干 |

因此，本轮实现不应只是“新增一个 dataclass”，而应把主干动画调用改造成**UMR clip/filter DAG**，并为三层进化循环提供统一、可审计、可蒸馏的运动中间表示。

## Final Design Decision for Implementation

### A. Introduce a strict motion-side shared contract

仿照 `UniversalSceneDescription`，新增 motion 侧统一合同：

- `MotionRootTransform`
- `MotionContactState`
- `UnifiedMotionFrame`
- `UnifiedMotionClip`
- `MotionPipelineNode` / `MotionPipelineResult`（轻量 DAG/filter 语义）

其中 `UnifiedMotionFrame` 至少固定以下字段：

| Field | Meaning | Why mandatory |
|------|---------|---------------|
| `time` | 帧时间（秒） | 对齐导出、评估、重采样、未来 runtime query |
| `phase` | 归一化相位 `[0,1]` | PFNN/DeepPhase/足接触/hold-frame/Layer 3 都依赖 |
| `root_transform` | 根平移/朝向/根速度 | 对齐 UsdSkel/KineFX/AnimGraph 的 root-vs-local 分离 |
| `joint_local_rotations` | 每个关节局部旋转字典 | 成为所有生成器、修正器、渲染器共同读写的主字段 |
| `contact_tags` | 左脚/右脚接触布尔值 + 可选强度 | 消除各模块重复猜测接触状态 |
| `metadata` | 可扩展诊断信息 | 支持三层进化、审计、未来蒸馏和缓存 |

### B. Clip/filter DAG order must be explicit

统一顺序固定为：

1. **Intent / state selection**
2. **Phase-driven base frame generation**
3. **Root motion extraction / normalization**
4. **Physics compliance filter**
5. **Biomechanics / grounding filter**
6. **Feature extraction / evaluation / export / render**

任何节点都只能消费上游 `UnifiedMotionFrame` 并返回新的 `UnifiedMotionFrame`；禁止把私有 pose 状态藏在节点外部并绕过总线。

### C. Strict layering rules

- 高层只决定 `state`, `phase`, `style intent`, `gait intent`；
- 中层负责生成基础局部姿态与根运动；
- 低层修正器只允许：
  - 足部链条
  - 骨盆微调
  - 接触标签
  - 稳定性相关 metadata
- 明确禁止：IK/physics 节点大幅扭转脊柱、胸腔、头部、上肢来补救底层问题。

### D. Backward-compatible migration strategy

为避免破坏现有 700+ 测试，本轮迁移应采用“内部先统一、外部继续兼容”的方式：

1. 保留 legacy `dict[str, float]` pose API 作为兼容层；
2. 新增 `pose_to_umr()` / `umr_to_pose()` 适配器；
3. 先把 `AssetPipeline.produce_character_pack()`、phase-driven、physics projector、biomechanics projector 接入 UMR 主干；
4. 渲染器暂时继续吃 pose dict，但由 `UnifiedMotionFrame.joint_local_rotations` 转出；
5. 再把 manifest / metadata / Layer 3 桥接逐步改为记录 UMR 审计信息。

### E. Evolution-loop write-back requirement

三层进化循环不能只优化参数；它必须开始消费并产出统一运动中间表示。最小闭环要求：

- Layer 1/2/3 都能读取 `phase/contact/root/joint-local` 的统一字段；
- 审计报告能检查是否存在越级写入；
- 未来待办实现应优先扩展 UMR 字段或新增 filter 节点，而不是重新定义并行 pose contract。

## Research Stop Condition Satisfied

已满足协议停止条件：

1. **至少两个以上强来源**（UsdSkel、KineFX、AnimGraph 官方文档）支持同一实践结论；
2. 下一步代码改造已经清晰，不再需要继续扩张检索范围；
3. 当前瓶颈已从“缺知识”转为“工程实现与审计落地”。
