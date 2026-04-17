# Gap D1: 端到端状态机测试覆盖的图遍历属性测试研究

## 研究结论

Gap D1 的根本问题不是“测试数量不够”，而是仓库此前缺少一个**显式、可审计、可自动遍历的状态图模型**。只要测试仍然依赖手工编写的 `Walk -> Run -> Jump` 级别示例，就无法系统性覆盖 `MotionMatchingRuntime` 与 `TransitionSynthesizer` 在真实运行时遇到的边界组合。基于本轮研究，最合适的技术组合不是继续堆叠 example-based tests，而是将运行时状态机提升为 **有向图模型**，由 **Hypothesis Rule-Based Stateful Testing** 负责生成整段操作程序，并由 **NetworkX** 负责维护边覆盖、边对覆盖与遗漏边审计。[1][2][3]

> 核心转变是：从“人工枚举几个动作案例”转向“让算法在合法状态图中自动生成长序列，并把每条边和边对的覆盖情况显式量化出来”。

## 关键参考资料与可落地机制

| 参考资料 | 关键机制 | 对本项目的直接启发 |
|---|---|---|
| Hypothesis Stateful Testing 文档 [1] | `RuleBasedStateMachine`、`@rule`、`@precondition`、`initialize`、失败序列 shrink | 用来生成完整状态切换程序，而不是单个输入样本 |
| Hypothesis Rule-Based Stateful Testing 文章 [2] | “轻量模型 + 被测系统并行运行”与最小反例收缩 | 用显式状态图做模型，用 `MotionMatchingRuntime` 做真实执行器 |
| NetworkX Traversal 文档 [3] | `DiGraph`、`dfs_edges`、`bfs_edges`、可达边枚举 | 用作合法边基线、遗漏边报告、覆盖统计容器 |
| 模型驱动测试的状态机覆盖说明 [4] | State / Transition / Transition-Pair Coverage 分层 | 让 Gap D1 具备渐进式成熟度目标，而不是笼统宣称“覆盖更多” |

这些资料共同指向同一结论：项目需要一层独立于执行器的**图模型边界**。该边界必须既能被测试框架消费，也能被演化桥接模块消费，从而让状态机覆盖成为“持续演化的系统能力”，而不是一次性的测试补丁。

## 对 MarioTrickster-MathArt 的设计判断

`MotionMatchingRuntime` 已经具备真实运行时状态切换能力，但在本轮之前，它没有提供一个可供覆盖审计复用的“合法状态图”。因此，测试只能围绕少数已知路径做局部断言，而无法回答三个关键问题：第一，当前仓库一共有多少合法状态边；第二，本轮测试究竟覆盖了多少边；第三，如果未来新增 `fall`、`hit`、`dash` 或更多 clip，覆盖模型是否能自动扩张。

为解决这个结构性问题，本轮设计采用如下边界划分。`RuntimeStateGraph` 负责从运行时已注册 clip 动态构建 `DiGraph`，把状态分类为 **cyclic**、**transient** 与 **unknown** 三类；`RuntimeStateMachineHarness` 负责通过真实 `MotionMatchingRuntime` 执行图上的边；Hypothesis 则只负责在合法 successor 集中生成长序列；覆盖率、遗漏边与边对覆盖则统一回收至图模型统计层。这样可以避免把合法性规则散落在测试逻辑和桥接逻辑中。

## 为什么选择 Property-Based Graph Fuzzing

Property-based stateful testing 最关键的价值在于，它生成的是“程序”，不是“数据”。对于动画状态机而言，真正危险的 bug 往往来自序列组合，例如连续自环、瞬时反复切换、暂态后立即回到循环态、以及跨多个状态的短链回跳。手工测试几乎不可能稳定枚举这些情况，而 Hypothesis 恰好可以在可达 successor 集中自动合成长序列，并在失败时缩减为**最短可复现边序列**。[1][2]

同时，仅有随机序列还不够。为了避免测试“跑了很多次却不知道还差哪些边”，必须让 NetworkX 显式维护 `expected_edges` 与 `expected_edge_pairs`。因此，本轮方案并不是“随机测试替代建模”，而是“图模型提供合法边界，随机程序负责探索，覆盖统计负责审计”。这三者缺一不可。[3][4]

## 本轮落地路线

| 层级 | 目标 | 落地模块 |
|---|---|---|
| 图模型层 | 显式表示运行时状态节点、合法边、边对与覆盖缺口 | `mathart/animation/state_machine_graph.py` |
| 属性测试层 | 自动生成整段状态切换程序并验证不变量 | `tests/test_state_machine_graph_fuzz.py` |
| 三层进化层 | 评估边覆盖、蒸馏规则、持久化历史与知识 | `mathart/evolution/state_machine_coverage_bridge.py` |
| 工具脚本层 | 在仓库内复现一次完整 D1 cycle | `tools/run_state_machine_coverage_cycle.py` |

本轮特别强调“面向未来待办自动扩张”。图模型不再硬编码只有 `idle/walk/run/jump` 四种状态，而是先从 runtime clip 名称推导状态集，再基于名称提示分类为循环态或暂态。这意味着未来如果项目补进 `fall`、`hit`、`dash`、`land` 等 clip，图模型、边覆盖统计与三层演化桥接会自动扩大检查范围，而不需要重写整套测试骨架。

## 与三层进化循环的耦合方式

Gap D1 不应该只是一个新测试文件，而应成为仓库的长期演化能力。因此，本轮把研究结论拆成三层闭环。Layer 1 使用显式图模型与真实运行时执行器评估**单边覆盖**、**边对覆盖**、**非法边数**；Layer 2 将经验沉淀到 `knowledge/state_machine_graph_fuzzing.md`，明确要求未来状态机测试必须基于显式有向图；Layer 3 将历史记录写入 `.state_machine_coverage_state.json`，追踪最佳覆盖率、最大图规模和周期通过情况。

这样一来，后续无论是新增状态、扩展 `headless_e2e_ci.py`，还是将图覆盖推广到更多动画子系统，都有统一的演化接口，而不必从零开始重新设计测试策略。

## 验收标准

本轮的核心验收标准是：第一，仓库必须拥有一个可执行的显式状态图模型；第二，必须存在基于 Hypothesis 的 Rule-Based Stateful Testing；第三，必须存在可复现的图遍历三层进化周期；第四，必须能输出边覆盖证据并持久化知识；第五，项目待办与 handoff 状态必须同步更新。只要其中任一项缺失，Gap D1 就不能算真正落地。

在实际执行中，本轮首先追求 **100% Edge Coverage**，同时把 **Edge-Pair Coverage** 作为持续增长指标，而不是一次性硬性关闭门槛。这一策略符合模型驱动测试的常见成熟度路径：先让每条合法边都被真实执行，再逐步扩大连续边组合的覆盖深度。[4]

## 参考文献

[1]: https://hypothesis.readthedocs.io/en/latest/stateful.html
[2]: https://hypothesis.works/articles/rule-based-stateful-testing/
[3]: https://networkx.org/documentation/stable/reference/algorithms/traversal.html
[4]: https://abstracta.us/blog/software-testing/model-based-testing-using-state-machines/
