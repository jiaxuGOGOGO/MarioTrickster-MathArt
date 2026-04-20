# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-096` |
| Focus | `HIGH-2.4` Phase Driven 非法状态转移拦截闭环：显式转移图、守卫失败语义、反向负路径验证 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `68 PASS, 0 FAIL` |
| Primary Files | `mathart/animation/phase_driven.py`, `mathart/animation/__init__.py`, `tests/test_phase_driven_state_machine.py`, `research/session096_phase_guard_research_notes.md`, `PROJECT_BRAIN.json` |

## Executive Summary

本轮任务已经把项目第四块刚性基石中的关键缺口——**Phase Driven 行为控制层对非法状态跃迁的静默放行**——从“语义错误但仍可能继续出帧”的脆弱态，升级为**显式状态图约束、可诊断守卫失败、负路径可重复验证**的闭环态。上一轮 `HIGH-2.3` 解决的是连续数值基因在极端扰动下的合法域投影问题；而本轮 `HIGH-2.4` 处理的是另一类完全不同的风险：**离散状态机控制错误**。对这类问题，正确策略不是继续做数值修复，而是把**允许的状态边**提升为一级架构对象，并在任何状态改变发生之前先执行守卫判断。

这次落地严格对齐了三条外部参考线。Harel 的 Statecharts 明确强调，系统语义必须建立在**显式状态**与**显式转移**之上，而不是靠隐式推断或宽松 fallback 拼接出来 [1]。Unreal Engine 的 AnimGraph / Transition Rules 文档则给出工业动画图的直观工程语义：**只有当某条边对应的 transition rule 计算为真时，状态才允许迁移** [2]。状态测试资料进一步说明，非法事件的负路径验证重点，不只是“报错了没有”，而是**被拒绝后机器内部是否仍维持最后一个合法状态且不产生隐藏副作用** [3]。本轮实现正是把这三条原则合并进 `mathart/animation/phase_driven.py` 的实际控制面。

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/phase_driven.py` | 新增 `_PHASE_DRIVEN_STATE_REGISTRY` 与 `PHASE_DRIVEN_ALLOWED_TRANSITIONS` | 把允许的语义状态与状态边从隐式习惯，提升为可审计、可扩展、O(1) 查询的数据契约 |
| `mathart/animation/phase_driven.py` | 新增 `IllegalStateTransitionError` | 为守卫失败提供显式、可诊断、可上抛的 typed error，而不是继续走泛化 fallback |
| `mathart/animation/phase_driven.py` | 新增 `PhaseDrivenStateMachine` | 把 `current_state`、`phase_clock`、`transition_blend_weight`、`cycle_count` 统一纳入受控状态机壳层 |
| `mathart/animation/phase_driven.py` | `transition_to()` 实现非法边 `False` 返回与 `strict=True` 异常路径 | 满足“可无异常安全拒绝”与“可强诊断失败上抛”双重调用语义 |
| `mathart/animation/phase_driven.py` | 非法转移拒绝路径保持 `current_state` / `phase_clock` / `transition_blend_weight` 不变 | 消除“请求失败但内部相位或混合权重已被污染”的隐蔽退化 |
| `mathart/animation/__init__.py` | 导出 `PhaseDrivenStateMachine`、`IllegalStateTransitionError`、`PHASE_DRIVEN_ALLOWED_TRANSITIONS` | 让后续运行时控制层、测试层与外部调用方可以通过统一包面导入新能力 |
| `tests/test_phase_driven_state_machine.py` | 新增 `test_illegal_state_transitions()` 与严格模式 / 合法恢复路径用例 | 形成显式负路径回归护栏，直接钉住 `hit → sprint` 与 `dead → idle` 两个禁止边 |

本轮真正的架构价值，不在于“多写了几个状态判断”，而在于把**状态机合法性**从分散于若干生成函数上下文中的默认行为，提升为独立、先行、可复用的控制面。以后新增语义状态时，不必继续把“当前状态能否跳到目标状态”的知识散落到多个分支里，只需要在转移表里声明，并自动受同一套守卫、快照与测试模板保护。

## Why This Fix Is Architecturally Correct

这次修复与 `HIGH-2.3` 的核心差异在于：前者处理的是**连续值越界**，后者处理的是**离散控制错误**。连续值越界适合用投影修复，因为目标是把候选值重新拉回合法数域；但非法状态转移则意味着控制逻辑本身出现了语义断裂，若继续“帮它兜过去”，只会把本应尽早暴露的上游错误扩散到动画生成、混合、测试与后续 AI 层。因此本轮不采用“自动修正到最近合法状态”的数值思路，而采用**显式拒绝 + 保持最后合法状态不变**的状态机语义，这与 Statecharts 的图约束思想完全一致 [1]。

从工业动画图视角看，这样做也更贴近 Unreal 的状态机写法。Unreal 并不是看到目标状态存在就允许切过去，而是先问“这条边是否存在，以及它的 transition rule 是否成立” [2]。这意味着**状态存在**与**状态可达**是两件不同的事。本轮 `PhaseDrivenStateMachine.transition_to()` 的职责，就是把这层区分正式放入项目代码：`target_state` 不再因为名字合法就默认被接纳，而必须先命中 `PHASE_DRIVEN_ALLOWED_TRANSITIONS[current_state]` 中声明的边。

负路径测试的设计也遵循了状态测试的标准思路：验证失败路径时，最重要的是**拒绝动作是否保持机器内部快照不变** [3]。因此，新测试并没有停留在“返回 False 就算过”，而是对 `current_state`、`phase_clock`、`transition_blend_weight` 做了值级别保持断言。这样一来，即便未来有人修改守卫逻辑、偷偷在拒绝路径上重置 phase 或 blend，这组测试也会第一时间抓到真正的回归。

## Test Closure

| Test Group | Coverage | Result |
|---|---|---|
| `tests/test_phase_driven_state_machine.py` | 非法 `hit → sprint`、非法 `dead → idle`、`strict=True` 异常路径、合法 `hit → stable_balance` 恢复路径 | `3 PASS` |
| `tests/test_phase_driven.py` | 既有 cyclic / transient 相位驱动行为、UMR 输出、辅助函数与历史回归 | `65 PASS` |
| Total | 新守卫层 + 既有 Phase Driven 回归 | `68 PASS, 0 FAIL` |

本轮测试的关键升级点，在于把“状态机负路径”从过去未明确验证的灰区，转成**显式案例集**。`test_illegal_state_transitions()` 直接验证两个审计红线：其一，`hit → sprint` 这种缺乏中间恢复语义的跳变必须被拒绝；其二，`dead → idle` 这种终止态回流必须被拒绝。更重要的是，测试不是只看拒绝信号，还逐项要求 `current_state`、`phase_clock`、`transition_blend_weight` 与调用前快照严格一致。这使得“返回 False 但内部数据偷偷变了”的伪安全实现无法蒙混过关。

## Files Touched This Session

| Category | Files |
|---|---|
| Code | `mathart/animation/phase_driven.py`, `mathart/animation/__init__.py` |
| Tests | `tests/test_phase_driven_state_machine.py` |
| Research Notes | `research/session096_phase_guard_research_notes.md` |
| Project State | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## PROJECT_BRAIN Update Summary

`PROJECT_BRAIN.json` 已完成以下同步。首先，`last_session_id`、`version`、`recent_focus_snapshot`、`validation_pass_rate` 已切换到 `SESSION-096` / `v0.86.0`。其次，`HIGH-2.4-PHASE-DRIVEN-ILLEGAL-TRANSITIONS` 已在 `pending_tasks` 中标记为 `CLOSED`，并同步写入 `completed_tasks`、`completed_work`、`closed_tasks_archive` 与 `session_log`。再次，下一阶段面向全局测试稳定性的清扫任务已经正式立项为 **`HIGH-2.5-CI-RANDOM-MASKING-DETERMINISM`**，用于系统性消灭无种子随机掩盖、分散 RNG 初始化与不可复现 CI 波动。最后，`resolved_issues` / `capability_gaps` 已移除 `REMAINING-S095`，并写入新的 `RESOLVED-S096` 与 `REMAINING-S096` 记录。

## Preparation Notes for HIGH-2.5

下一轮如果要无缝接入 **“彻底消灭全局测试中的无种子随机掩盖，以稳固 CI/CD 管道”** 的大扫除任务，当前测试架构已经具备良好地基，但还需要做四项微调准备。

第一，应该把项目中所有仍然各自 `random.seed(...)`、`np.random.seed(...)`、或隐式依赖全局 RNG 的测试入口，统一收口到**集中式 seeded fixture / helper**。也就是说，未来不应再让单个测试随手创建随机源，而要通过统一工厂显式声明：本次测试使用哪类 RNG、种子是多少、场景目录是什么。只有这样，CI 失败时才能稳定回放。

第二，建议把本轮 `PhaseDrivenStateMachine` 用到的**场景目录化思路**推广到更广的测试层。与其让测试在函数体里临时拼装随机输入，不如建立一套“合法场景目录 + 非法场景目录 + 断言模板”的结构化 case catalog。这样后续要清理 random masking 时，就能先把随机样本替换为有限但覆盖关键边界的显式案例，再决定哪些部分继续保留受控随机探索。

第三，属性测试或 fuzz 测试若继续保留，必须从“每次自动乱跑”升级为**固定种子、固定预算、失败样本可持久化**的 CI 友好模式。也就是说，随机探索并不是完全禁止，但它必须从“掩盖问题的不可复现噪声”转变为“可回放的定向搜索器”。本轮 phase-driven 负路径测试已经证明，很多最关键的回归其实可以通过少量高价值显式案例稳定抓住，这对下一轮缩减随机掩盖面非常有帮助。

第四，建议把“状态保持不变”的快照断言模板进一步抽象成可跨模块复用的测试 helper。`HIGH-2.4` 已经验证，负路径是否安全，本质上就是看**拒绝后对象是否仍保留最后合法快照**。这一模板不仅适用于状态机，也适用于 WFC 冲突、XPBD 非法输入、基因投影前后不该被污染的缓存对象。若先把这类 helper 规范化，`HIGH-2.5` 清扫全局随机掩盖时就能更快把大量“弱断言”升级为“快照级别强断言”。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `HIGH-2.5-CI-RANDOM-MASKING-DETERMINISM` | 统一 seeded fixture、场景目录、属性测试预算与可回放失败样本，清除全局随机掩盖 |
| 2 | 复用本轮 `PhaseDrivenStateMachine` 的快照保持断言模板 | 把“拒绝路径不污染内部状态”的强断言推广到更多模块 |
| 3 | 保留 `research/session096_phase_guard_research_notes.md` | 作为后续行为树 / AnimGraph / runtime state-machine 语义约束的长期参考 |

## References

[1]: https://www.state-machine.com/doc/Harel87.pdf "David Harel - Statecharts: A Visual Formalism for Complex Systems"
[2]: https://dev.epicgames.com/documentation/unreal-engine/transition-rules-in-unreal-engine?lang=en-US "Unreal Engine Documentation - Transition Rules"
[3]: https://www.eecs.yorku.ca/course_archive/2008-09/W/4313/slides/13-StateBasedTesting.pdf "York University - State-Based Testing"
