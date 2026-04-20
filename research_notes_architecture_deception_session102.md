# SESSION-102 架构欺骗修复研究笔记

## 已同步项目上下文

- 仓库：`jiaxuGOGOGO/MarioTrickster-MathArt`
- 当前 HEAD：`9ac332152340b7124753e86c5ba5189cead8bdbe`
- GitHub 页面显示主分支最新可见短哈希：`9ac3321`
- `SESSION_HANDOFF.md` 说明上一轮 `SESSION-101` 聚焦测试盲区，且明确把 `terrain_sensor` 架构收尾留作后续专项。
- `PROJECT_BRAIN.json` 将 `P1-ARCH-4` 列为最高优先级之一，并保留了 `session048_terrain_module` 等 terrain sensor 历史上下文。

## 外部参考 1：Robert C. Martin《Screaming Architecture》

来源：<https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html>

提炼出的本轮硬约束：

1. **架构必须尖叫出系统用例，而不是框架或偶然实现细节。** 对本任务而言，既然文档宣称 `scene_aware_fall_pose` 是三阶段管线，那么代码拓扑就必须在物理结构上直接显式体现三阶段，而不能把三个阶段揉进一个巨型函数里。
2. **架构首先服务用例。** 这里的用例是“场景感知下落姿态生成”，因此顶层函数应该像轻量级编排器一样顺序组织阶段，而不是混杂查询、启发式决策和 IK 姿态改写。
3. **框架与外围细节应保持臂距。** 对本任务的映射是：阶段之间必须通过显式数据契约传递，而不是隐式共享局部变量或跨阶段写入可变状态。
4. **可测试性是结构正确性的旁证。** 若三阶段真正解耦，则每个阶段都应能被独立白盒测试，而不是只能通过最终姿态黑盒验证。

## 对本轮代码重构的直接约束

- `scene_aware_fall_pose` 必须降级为**轻量调度器**。
- 同模块中必须存在 3 个职责单一的私有阶段函数。
- 阶段间必须使用 `NamedTuple` 或 `dataclass` 形成强类型中间产物。
- 新测试必须覆盖阶段级中间值，而不是只看最终 `pose`。

## 外部参考 2：Data-Oriented Design

来源：<https://gamesfromwithin.com/data-oriented-design>

提炼出的本轮硬约束：

1. **程序首先是数据变换。** 场景感知跌落姿态生成应被视为“输入状态 → 中间工件 → 输出姿态”的顺序变换，而不是在单函数内杂糅控制流。
2. **小函数、少依赖、扁平拓扑。** 将查询、评估、融合拆成小而纯的变换函数，可显著降低依赖、提升替换和维护难度。
3. **显式输入输出有利于测试。** 最容易写强断言测试的形式是：构造输入数据，调用变换函数，断言输出数据；这直接支持对三阶段中间值逐层校验。
4. **减少隐式共享状态。** 阶段函数应尽量只读取输入 dataclass / NamedTuple，并返回新的 dataclass / NamedTuple，避免跨阶段借用局部变量和可变全局状态。

## 外部参考 3：Design by Contract

来源：<https://en.wikipedia.org/wiki/Design_by_contract>

提炼出的本轮硬约束：

1. **前置条件（Preconditions）**：每个阶段函数必须明确自己接受什么输入类型与字段；不允许依赖“上一段代码刚好定义过某个局部变量”。
2. **后置条件（Postconditions）**：每个阶段函数必须明确输出什么中间工件；下一阶段只能消费该工件声明过的字段。
3. **不变量（Invariants）**：`scene_aware_fall_pose` 的公共 API 签名和返回字典结构必须保持兼容；三阶段顺序和数据流必须稳定，不可被隐藏副作用破坏。
4. **契约即文档。** 若文档声称存在 3 个 phase，而实现无法在函数拓扑上定位这 3 个 phase，则属于拓扑违约；修复必须体现到真实代码结构与测试结构，而非仅修改注释。

## 研究到代码的映射策略

- 将 `scene_aware_fall_pose` 重写为调度器，只负责串行组织 Phase 1/2/3。
- 为每个阶段建立强类型中间工件，显式承载 geometry query 结果、clearance/compensation 评估结果、以及最终姿态融合输入。
- 用阶段级白盒测试固定“射线距离 / 补偿向量 / 最终矩阵”等中间值，确保文档、代码、测试三者同构。
