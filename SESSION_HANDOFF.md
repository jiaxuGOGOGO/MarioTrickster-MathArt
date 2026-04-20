# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-095` |
| Focus | `HIGH-2.3` 基因型变异边界约束闭环：统一硬截断吸附、可复现极限压测、去除弱断言 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `50 PASS, 0 FAIL` |
| Primary Files | `mathart/animation/genotype.py`, `tests/test_genotype.py`, `PROJECT_BRAIN.json`, `research/session095_genotype_boundary_research_notes.md` |

## Executive Summary

本轮任务已经把项目的第三块刚性基石——**生物形态生成与演化层（Morphology & Evolution）**——从“零散截断、测试盲区、极端扰动下可能越界”的脆弱态，升级为**契约驱动、统一投影、可复现高压验证**的闭环态。前置两轮战役分别关闭了 **XPBD 接触/穿透稳定性** 与 **WFC 锁定图块绝对生存** 的结构性漏洞；本轮则在 `mathart/animation/genotype.py` 中正式建立了**连续基因值域契约（continuous-domain contract）**，并让 `mutate_genotype()` 在所有扰动叠加完成后统一走入 `enforce_genotype_bounds()` 的**后置硬截断投影器**。这意味着系统面对任意强度的形态噪声时，不再依赖零散 `clip()` 片段和侥幸覆盖，而是以一个显式、集中、可审计的边界吸附阶段来保证下游解码与物理消费端永远读取到合法形态参数。

从研究对齐角度看，这次实现严格遵守了三条工业/学术基线。Karl Sims 在经典虚拟生物系统中把肢体尺度、连接与关节限制视为真实物理系统的输入，而不是可任意漂移的装饰量；一旦参数越过物理合法域，下游系统就会走向不稳定甚至奇异 [1]。Gymnasium 的 `Box` 空间与 OpenAI Gym 的 `ClipAction` 工业实现则提供了极其直接的现代工程范式：**先探索，再用统一 `np.clip(low, high)` 投影回合法超矩形**，而不是用异常中断探索过程 [2] [3]。Hansen 对 CMA-ES 的边界处理讨论进一步说明，工业级演化算法面对越界扰动时，应优先采用**分量级投影回最近边界**的连续修复机制，而非把越界个体留给下游系统承担风险 [4]。本仓库的基因变异闭环，已经与这三条准则完成结构对齐。

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/genotype.py` | 新增 `BoundInterval`、统一比例/标量/调色板边界契约、`get_genotype_mutation_contract()`、`enforce_genotype_bounds()` | 把分散在各处的隐式边界收拢为单一契约与单一投影入口 |
| `mathart/animation/genotype.py` | 重构 `mutate_genotype()`：先累计扰动，最后统一硬截断 | 消除“边变异边局部截断”的散装行为，改为工业级后置投影闭环 |
| `mathart/animation/genotype.py` | 将负强度按幅值处理：`mutation_strength = abs(strength)` | 使 `-1e6` 这种极端负强度不再被静默弱化为小扰动，真正进入高压边界验证路径 |
| `mathart/animation/genotype.py` | `decode_to_style()` 改为复用同一组样式边界 | 解码侧与变异侧共享一套硬性值域语义，减少双轨规则漂移 |
| `tests/test_genotype.py` | 引入固定种子生成器与显式值级别边界断言 helper | 消灭弱断言与“随机运气过关”，把测试升级为坐标级边界审计 |
| `tests/test_genotype.py` | 新增手工越界投影测试与 `±1e6` 变异核压测 | 证明系统在极端条件下会**吸附到边界**而不是报错、越界或侥幸漂移 |
| `test_genotype_e2e.py` | 未改代码，但纳入回归验证 | 证明统一硬截断未破坏 AssetPipeline 下游演化/渲染链路 |

本轮代码调整的核心思想不是“多补几个 `clip()`”，而是把 **基因型合法域** 从隐式经验值提升为**一级架构对象**。现在，比例修饰量、`outline_width`、`light_angle`、以及 `palette_genes` 中各个连续通道，都受统一契约表约束；变异器只负责产生候选扰动，真正的合法化由集中化投影器完成。这样一来，未来若需要扩展新的连续形态基因，工程上只需向契约表添加合法域定义，并自动纳入相同的后置拦截流程，而不必在多个调用点手工追查边界遗漏。

## Why This Fix Is Architecturally Correct

本轮闭环的关键，不在于“有没有边界”，而在于**边界被放在了正确的时序位置**。如果在变异过程中零散截断，就会出现两个问题。第一，合法域知识被分散到多个局部语句，后续新增参数时极易漏网；第二，局部截断会掩盖整个候选向量的真实扰动幅度，造成测试无法证明“系统在极端压力下仍然通过统一投影而稳定存活”。因此，本轮将所有连续参数统一改为**先扰动、后投影**。这与 Gym 的 `ClipAction` 包装器直接在动作送入环境前执行 `np.clip(action, low, high)` 的工业写法一致 [3]，也与演化策略中“先采样，再按边界规则修复”的主流做法一致 [4]。

另一方面，负强度的处理也从过去的“被 `max(strength, 0.05)` 吃掉”改成了按**绝对值幅度**参与噪声尺度计算。这一点非常关键。因为审计红线要求系统必须能承受 `1e6` 与 `-1e6` 这种荒谬强度，而不是把负值偷偷缩回温和区间。如果负值被静默降格，就无法证明边界吸附器真的在**核爆级扰动**下工作。现在，正负极端强度在相同种子下会映射到相同的扰动幅值与相同的边界吸附结果，这既满足工程稳定性，也满足测试可复现性。

## Test Closure

| Test Group | Coverage | Result |
|---|---|---|
| `tests/test_genotype.py` | 数据结构、序列化、样式解码、固定种子复现、手工越界投影、`±1e6` 极端变异压力、原对象不被污染 | `46 PASS` |
| `test_genotype_e2e.py` | AssetPipeline 下游 genotype 端到端链路 | `4 PASS` |
| Total | 核心 + E2E | `50 PASS, 0 FAIL` |

这次测试不是“对象存在即可”“结果不为 `None` 即可”的温室测试，而是显式进入**值级别断言**。测试文件新增了 `assert_all_continuous_genes_within_contract()`，会逐项拉出当前 genotype 契约中的每一维连续参数，验证其是否落在声明区间内，并统计有多少维确实被**吸附到边界**。手工越界投影测试直接构造一组极端非法值，让 28 个连续坐标全部越界后走入 `enforce_genotype_bounds()`，再要求至少 28 个边界命中；这证明拦截器不仅“没崩”，而且确实在进行坐标级边缘投影。正负 `1e6` 的变异压力测试则进一步证明，即便在极端随机扰动规模下，系统也会把所有连续基因吸附回合法域，而且负强度不会被偷偷退化成小噪声。

## Files Touched This Session

| Category | Files |
|---|---|
| Code | `mathart/animation/genotype.py` |
| Tests | `tests/test_genotype.py` |
| Research Notes | `research/session095_genotype_boundary_research_notes.md` |
| Project State | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## PROJECT_BRAIN Update Summary

`PROJECT_BRAIN.json` 已完成以下同步：其一，`last_session_id` 与 `recent_focus_snapshot` 已切换到 `SESSION-095`；其二，`HIGH-2.3` 被标记为已关闭，并在 `completed_tasks`、`completed_work`、`closed_tasks_archive` 与 `session_log` 中留下结构化记录；其三，下一阶段主攻任务已前推为 **`HIGH-2.4-PHASE-DRIVEN-ILLEGAL-TRANSITIONS`**，明确要求在 `mathart/animation/phase_driven.py` 内建立非法状态转移拦截与反向测试机制；其四，验证摘要已更新为 **50 PASS, 0 FAIL**。

## Preparation Notes for HIGH-2.4

下一轮若要无缝接入 **`HIGH-2.4 状态机驱动（Phase Driven）：非法状态转移静默通过的拦截与反向测试`**，当前架构已经具备良好的前置条件，但还应做四个微调准备。

首先，需要把 `mathart/animation/phase_driven.py` 中当前“接受 `PhaseState / PhaseVariable / float` 并路由到 cyclic/transient 生成路径”的机制，进一步提升为**显式转移契约层**。当前模块已经存在 `PhaseDrivenAnimator.generate()`、`generate_frame()`、`TransientPhaseVariable` 以及若干 jump/fall/hit 辅助构造函数，这为状态机防线提供了天然落点；但下一步需要的不是再补几个 `if`，而是把**允许的 source_state → target_state 转移图**独立为可审计数据结构，并在真正生成帧之前执行守卫判断。只有这样，非法跃迁才不会在路由过程中被默认吞掉。

其次，需要把 HIGH-2.4 的测试设计从“正向可达”升级为“反向拦截”。也就是说，除了验证 `jump → apex → fall → landing` 这样的合法路径，还必须显式喂入**不允许的反向或跨相位跳变**，例如从 `stable_balance` 直接伪造进入某些只应由冲击或空中动力学触发的 transient 状态，并要求系统返回明确的守卫失败信号，而不是继续生成看似正常但语义错误的帧。审计报告已经明确指出，这类负路径缺失是当前 Phase Driven 的最大盲区之一。

再次，建议在 HIGH-2.4 中复用本轮已经跑通的**契约化测试样板**。本轮在 genotype 上已经证明：只要把合法域变成显式契约，再配合固定种子、显式遍历和边界命中断言，测试就能从“碰运气”变成“抓现行”。同样的原则完全可以平移到 phase-driven 层：把**允许转移表**作为契约，把**非法输入案例集**作为高压样本，把**守卫命中统计**作为回归指标。这样做能最大化减少未来状态机回归时的静默退化。

最后，建议把 HIGH-2.4 的异常语义设计成**可诊断但不污染正常路径**。本轮 genotype 选择的是“静默硬截断”，因为演化问题的本质是边界内搜索；而 Phase Driven 则不同，非法状态转移属于**控制逻辑错误**而非连续数值漂移，因此更适合返回显式守卫失败、拒绝生成对应非法路径帧，并把失败原因写入 metadata/session log，以便 Layer 3 与后续自动化测试做最小化归因。也就是说，HIGH-2.4 需要沿用本轮“契约优先”的思维，但在**失败策略**上要从数值修复切换到状态拦截。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `HIGH-2.4-PHASE-DRIVEN-ILLEGAL-TRANSITIONS` | 为 `phase_driven.py` 建立显式合法转移图、反向测试与守卫失败元数据 |
| 2 | 复用本轮 contract-driven 测试模板 | 把 genotype 的“值域契约 + 固定种子 + 全维断言”迁移到状态转移契约测试 |
| 3 | 保留 `research/session095_genotype_boundary_research_notes.md` | 作为形态/演化层边界设计的长期依据，供后续进化与 RL 接口继续复用 |

## References

[1]: https://www.karlsims.com/papers/siggraph94.pdf "Karl Sims - Evolving Virtual Creatures (SIGGRAPH 1994)"
[2]: https://gymnasium.farama.org/api/spaces/fundamental/ "Gymnasium Documentation - Fundamental Spaces / Box"
[3]: https://github.com/openai/gym/blob/master/gym/wrappers/clip_action.py "OpenAI Gym - ClipAction wrapper"
[4]: http://www.cmap.polytechnique.fr/~nikolaus.hansen/cmatutorial110628.pdf "Nikolaus Hansen - CMA-ES Tutorial"
