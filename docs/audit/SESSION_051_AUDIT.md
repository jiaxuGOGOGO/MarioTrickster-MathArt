# SESSION-051 Audit: Gap D1 State-Machine Graph Fuzzing

## Audit Scope

本次审计针对 **Gap D1: 端到端状态机测试覆盖**。审计目标不是确认“仓库增加了几个测试文件”，而是确认研究结论已经真正落到代码、知识、持久化状态和回归流程中，并且状态机覆盖能力已经从手工案例升级为**显式图模型 + 属性测试 + 三层演化闭环**。

## Research-to-Code Traceability

| 研究结论 | 代码落点 | 审计结论 |
|---|---|---|
| 状态机测试必须先拥有显式有向图模型 | `mathart/animation/state_machine_graph.py` | 已落地。新增 `RuntimeStateGraph`，可从 runtime clip 集动态构图，并提供 `expected_edges` / `expected_edge_pairs` 与覆盖统计。 |
| 属性测试应生成整段状态切换程序，而不是单个输入 | `tests/test_state_machine_graph_fuzz.py` | 已落地。新增 Hypothesis `RuleBasedStateMachine`，对 successor 集合进行程序级随机探索，并保留 shrink 能力。 |
| 图模型与真实执行器必须分离，但又要由同一合法边界约束 | `RuntimeStateMachineHarness` + `MotionMatchingRuntime` | 已落地。Harness 只执行图上合法边，运行时仍由真实 `MotionMatchingRuntime` 驱动。 |
| 覆盖能力应纳入三层进化循环，而不是停留在测试层 | `mathart/evolution/state_machine_coverage_bridge.py` | 已落地。Layer 1 评估覆盖率，Layer 2 写回知识，Layer 3 持久化状态。 |
| Gap D1 需要可复现入口，用于未来继续自我迭代 | `tools/run_state_machine_coverage_cycle.py` | 已落地。可直接运行一次状态机图遍历 cycle，并输出 JSON 结果。 |

## Implemented Artifacts

| Artifact | Purpose | Audit Result |
|---|---|---|
| `mathart/animation/state_machine_graph.py` | 显式状态图、边覆盖、边对覆盖、真实 runtime harness | Present |
| `mathart/evolution/state_machine_coverage_bridge.py` | Gap D1 三层进化桥接 | Present |
| `tests/test_state_machine_graph_fuzz.py` | Hypothesis 属性测试 + 确定性边覆盖 + bridge persistence tests | Present |
| `tools/run_state_machine_coverage_cycle.py` | 单次 Gap D1 cycle 执行入口 | Present |
| `knowledge/state_machine_graph_fuzzing.md` | Layer 2 持久化知识文件 | Present |
| `.state_machine_coverage_state.json` | Layer 3 覆盖历史状态文件 | Present |
| `docs/research/GAP_D1_STATE_MACHINE_GRAPH_FUZZING.md` | 研究总结与设计依据 | Present |

## Runtime Evidence

本轮执行 `python3.11 tools/run_state_machine_coverage_cycle.py` 后，仓库生成了第一份真实的 Gap D1 覆盖证据。当前 runtime graph 从真实可用 clip 推导出 **4 个状态** 与 **16 条合法边**。确定性 canonical walk 成功覆盖全部 16 条边；在额外随机游走 24 步后，边对覆盖达到 **31 / 64 = 0.484375**，且未出现非法边。

> 审计判断：Gap D1 的“Edge Coverage”目标已经在当前 runtime 图规模上落地；“Edge-Pair Coverage”已经进入可量化、可增长、可追踪状态，但仍保留继续扩张空间。

| Metric | Result |
|---|---|
| States | **4** |
| Expected edges | **16** |
| Covered edges | **16** |
| Edge coverage | **1.0** |
| Expected edge pairs | **64** |
| Covered edge pairs | **31** |
| Edge-pair coverage | **0.484375** |
| Invalid edges | **0** |
| Acceptance | **True** |

## Test Evidence

| Command | Result |
|---|---|
| `pytest -q tests/test_state_machine_graph_fuzz.py` | **5/5 PASS** |
| `pytest -q tests/test_state_machine_graph_fuzz.py tests/test_layer3_closed_loop.py` | **6 PASS, 1 SKIP** |
| `python3.11 tools/run_state_machine_coverage_cycle.py` | **Accepted cycle; knowledge + state persisted** |

新增测试并不是单一风格，而是覆盖了四种不同责任：状态图建模正确性、canonical 全边遍历、Hypothesis 状态程序生成、以及 bridge 的持久化写回。这说明本轮并非“只写一个 fuzz test”，而是建立了**模型、执行器、桥接器、知识沉淀**四位一体的闭环。

## Remaining Gaps After SESSION-051

虽然本轮已经实质推进并部分关闭 Gap D1，但仍存在两个剩余扩展方向。第一，`headless_e2e_ci.py` 还没有直接消费新的状态图覆盖模型，因此真正的“无头 E2E preset 批量生成”仍有继续接入空间。第二，当前 runtime 基于项目现有 clip 主要覆盖 `idle/walk/run/jump`；未来若补进 `fall`、`hit`、`dash`、`land` 等 clip，图规模和边对规模将继续扩张，届时应把新的缺口纳入持续循环，而不是回退到手工案例。

## Final Audit Verdict

本次 SESSION-051 满足“研究内容必须被代码、测试、知识、状态持久化共同证明”的要求。Gap D1 不再只是“建议多写几个测试”，而是已经获得一个明确的工程化实现：**显式状态图、属性驱动程序生成、边覆盖审计、知识蒸馏和 Layer 3 状态持久化**。因此，审计结论为：

> **Gap D1 has been materially implemented and promoted from ad-hoc example testing to graph-based, property-driven runtime coverage.**

但从项目治理角度，本轮更合适的状态不是“完全终结”，而是：**核心能力已落地，后续应继续把该模型接入 headless E2E 预设生成与更大状态集。**
