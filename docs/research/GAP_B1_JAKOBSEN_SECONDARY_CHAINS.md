# Gap B1：Jakobsen 轻量级刚柔耦合二次动画研究与仓库落地

## 研究目标

本轮研究聚焦于 **Gap B1：刚柔耦合（二次动画）**，目标是在不引入重型 XPBD 或全布料求解器的前提下，为仓库加入一个可复用、可演化、可审计的 **2D 轻量级二次动画链条系统**。研究结论是：对于当前 MarioTrickster-MathArt 的角色规模、渲染目标和实时约束，采用 **Thomas Jakobsen 2001 年提出的 velocity-less Verlet integration + distance constraints** 路线，比直接上完整柔体框架更契合项目阶段性需求。[1]

> Jakobsen 的核心思想不是先建立复杂的速度、力和刚体张量体系，而是直接在位置层更新粒子，再通过多轮约束松弛让系统回到合法状态。这一方法在实时角色物理中极具工程价值，因为它以极少的实现复杂度换取了良好的稳定性。[1]

与此同时，工程对照参考表明，现代 Verlet cloth / rope demo 依然广泛沿用“**Verlet 更新 + 重复约束满足**”这一主轴，并通常通过少量代理碰撞体、有限迭代和链条/布片分层建模来取得实时效果。[2] 因此，本项目将 Gap B1 的首轮闭环定义为：**先把披风与头发建模为挂在骨骼锚点上的轻量级链条系统，再把误差、拖尾和伸长比纳入三层进化循环。**

## 研究结论与实现映射

| 研究结论 | 论文/参考来源 | 仓库内落实方式 |
|---|---|---|
| 角色挂件可由少量质点与距离约束离散表示，无需先上重型柔体求解器 | Jakobsen 2001 [1] | 新增 `mathart/animation/jakobsen_chain.py`，实现 `JakobsenSecondaryChain` |
| 根锚点应直接共享骨骼/刚体运动，由此自然传播惯性与拖尾 | Jakobsen 2001 [1] | 新增 `SecondaryChainProjector`，将 UMR 骨骼姿态转换为链条锚点驱动 |
| 3–6 次 relaxation 往往即可得到稳定、可信的实时表现 | Jakobsen 2001 [1] | `SecondaryChainConfig.iterations` 进入配置层并暴露为可调参数 |
| 碰撞先用简单代理体，不必一步做到精细自碰撞 | Jakobsen 2001 [1]、ClothDemo [2] | 采用 `BodyCollisionCircle` 作为头、颈、胸、髋等部位的轻量碰撞代理 |
| 链条优先于完整布片，更适合披风边缘、头发束、挂件尾拖 | ClothDemo [2] | 提供仓库原生 `cape` 与 `hair` 两类默认 preset |
| 参数好坏必须能回写为趋势与规则，否则难以持续进化 | 项目内部三层架构 | 新增 `mathart/evolution/jakobsen_bridge.py`，记录误差、拖尾、拉伸比与知识规则 |

## 算法摘要

本项目实现遵循如下最小可行链路。首先，将披风或头发抽象为一条有序质点链，其中根节点被视为 **固定锚点**，始终绑定到骨骼世界坐标。随后，非根节点使用 Verlet 位置更新：当前位置由“当前点位置 + 保留的位移增量 + 外力加速度项”构成，其中外力项包含 **重力**、**锚点加速度反向注入的惯性项**，以及少量由根运动速度产生的拖曳项。[1]

在每一帧的积分之后，求解器会重复执行距离约束松弛，使相邻质点尽量回到名义段长；同时可选地加入一层较弱的“support constraint”，对隔一个节点的跨度进行约束，以减少过软或过折的形变。这一做法本质上延续了 Jakobsen 对“**约束优先于速度显式维护**”的工程哲学。[1]

碰撞阶段当前采用简单圆形代理体。头、颈、胸、脊柱和髋部都可以为链条提供排斥边界，链条粒子一旦侵入代理体内部，就会沿中心到粒子的方向被投影回边界外侧。这与 Jakobsen 在角色物理中倡导的“先以简化几何处理碰撞，再在需要时继续细化”的思路一致。[1]

## 仓库级实现结果

| 模块 | 新增/更新内容 | 作用 |
|---|---|---|
| `mathart/animation/jakobsen_chain.py` | 新增 Jakobsen 风格链条求解器、配置、诊断与 UMR projector | Gap B1 核心实现 |
| `mathart/animation/__init__.py` | 导出 Gap B1 公共 API | 统一外部调用入口 |
| `mathart/pipeline.py` | 新增 `enable_secondary_chains`、`secondary_chain_presets`，把 `secondary_chain_projection` 接入 UMR 节点 | 让角色管线直接产出带二次链条元数据的动作帧 |
| `mathart/evolution/jakobsen_bridge.py` | 新增三层进化桥 | 将误差、拖尾、拉伸趋势写入持久状态并蒸馏知识 |
| `mathart/evolution/__init__.py` | 导出 Gap B1 bridge | 演化包 API 接入 |
| `mathart/evolution/engine.py` | 新增 Gap B1 状态写回与状态面板 | 项目大脑与引擎报告可感知该能力 |
| `mathart/evolution/evolution_loop.py` | 注册 Gap B1 蒸馏记录与状态摘要 | 全局演化报告覆盖 B1 |
| `tests/test_jakobsen_chain.py` | 新增 5 项测试 | 验证求解、碰撞、UMR projector、管线、演化桥接 |

## 三层进化循环中的位置

### Layer 1：内部实现自进化

Gap B1 不再只是“能不能摆动”的演示代码，而是把每帧约束误差、tip lag、collision count 和 stretch ratio 显式写入 UMR 元数据。这意味着链条系统的输出已经从“不可观测的视觉附加物”升级为“可被分析和排序的工程对象”。

### Layer 2：外部知识蒸馏

本轮把 Jakobsen 原论文的最关键工程规则回写为仓库知识：**优先使用轻量级链条、优先使用简化代理碰撞、优先用迭代数和段长调优，而不是一开始升级到更重的求解器。** 这些规则被持久化到 `knowledge/jakobsen_secondary_chain_rules.md` 的追加式知识库中，用于后续会话复用。[1] [2]

### Layer 3：自我迭代测试

`JakobsenEvolutionBridge` 将 **mean constraint error**、**mean tip lag**、**max stretch ratio** 作为主要指标，并据此输出一个小范围 fitness bonus / penalty。这样后续如果项目继续做披风、头发、尾巴、围巾等附加件，就能在现有机制上迭代，而不必重新设计新的评估语言。

## 为什么当前阶段不直接升级到 XPBD

| 方案 | 当前阶段收益 | 当前阶段成本 | 本项目结论 |
|---|---|---|---|
| Jakobsen 链条 | 轻、稳定、易集成、适合披风/头发束 | 双向耦合能力有限，仍属近似 | **优先采用** |
| XPBD / 更重柔体 | 更严格约束、更强物理一致性 | 实现与调参成本更高，对现有 2D 角色管线偏重 | 暂作为未来路线 |

当前仓库仍处于“**让程序化角色管线先拥有可信二次动画外挂层**”的阶段。Jakobsen 方法已经能解决最急迫的拖尾、滞后和重量感问题，而不会把整个渲染/动作系统拖入更复杂的求解依赖。因此，本轮没有否定 XPBD 的价值，而是把它重新定位成 **当链条系统覆盖范围不够、需要更强双向耦合时的后续升级路线**。[1]

## 实践结论

本轮 Gap B1 已经形成一条完整闭环：研究资料完成深读，仓库内新增轻量链条求解器与 projector，角色管线能够输出二次动画元数据，三层进化桥能够评估和蒸馏该能力，且自动化测试已覆盖核心求解、碰撞、管线与桥接。换言之，**Jakobsen 方案在本项目中不再只是参考知识，而是已经成为可运行、可验证、可演化的仓库能力。**

## References

[1]: https://www.cs.cmu.edu/afs/cs/academic/class/15462-s13/www/lec_slides/Jakobsen.pdf "Thomas Jakobsen, Advanced Character Physics"
[2]: https://github.com/davemc0/ClothDemo "davemc0/ClothDemo"
