# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-097` |
| Focus | `HIGH-2.5` 全局测试确定性清扫：移除 legacy bare RNG、强化值级断言、修复 registry teardown 污染 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `247 PASS, 0 FAIL`（编辑范围定向回归）；广域审计 `1564 PASS, 97 FAIL, 7 SKIP` |
| Primary Files | `tests/test_motion_adaptive_keyframe.py`, `tests/test_motion_vector_baker.py`, `tests/test_session065_research_modules.py`, `tests/test_evaluator.py`, `tests/test_oklab.py`, `tests/test_p1_b3_2_rl_reference.py`, `tests/test_dimension_uplift.py`, `research/session097_test_determinism_research_notes.md`, `PROJECT_BRAIN.json` |

## Executive Summary

本轮 `HIGH-2.5` 的核心目标，不是单纯“把随机数都加个种子”，而是把项目测试层从**隐式、分散、可能掩盖问题的随机输入习惯**，推进到**显式、本地化、可回放、可值级校验**的 CI 友好状态。Martin Fowler 对非确定性测试的分析强调，flaky tests 的危害不只是偶发失败本身，而是它们会持续侵蚀团队对测试结果的信任，使 CI 反馈失去决策价值 [1]。NumPy NEP 19 也明确建议以独立 `Generator` 对象替代全局随机状态，从而避免模块间的隐藏耦合与污染 [2]。Google 对 flaky tests 的治理经验进一步说明，真正可维护的测试体系，必须优先消灭不可复现输入、执行顺序依赖与环境漂移 [3]；而持续集成的基本要求，则是每次提交都要面对一套高可信、可快速反馈的自测试构建 [4]。

这次落地正是沿着这条路线执行。项目中若干高风险回归文件此前仍散布着 `np.random.rand`、`np.random.randn`、`np.random.randint`、`RandomState(42)` 以及局部的全局 seed 模式。它们的问题在于，即便“多数时候能跑过”，也容易把真正的数值回归、边界退化或顺序污染埋在随机样本波动里。本轮已经把这些热点用例统一改成**每个测试上下文自己构造本地 `Generator`**，并把几类原本只有 shape、数量、范围的弱断言，升级为**精确 keyframe 列表、精确 gap 序列、精确颜色集合、精确统计均值、精确 temporal metrics** 等值级基线，从而让测试输出变成可追溯的、可解释的、可稳定回放的工程信号。

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `tests/test_motion_adaptive_keyframe.py` | 引入 `make_rng()` / `make_random_scores()` 本地生成器辅助函数，移除 bare `np.random.rand` | 关键帧选择、gap 约束、`end_percent` 映射不再依赖全局随机状态 |
| `tests/test_motion_adaptive_keyframe.py` | 为固定输入增加精确 keyframe 列表、gap 序列、`end_percent` 值级断言 | 原先只验证边界/范围的弱断言升级为可回放基线 |
| `tests/test_motion_adaptive_keyframe.py` | 修复 `BackendFileWatcher` 热重载测试的 registry teardown 恢复逻辑 | 解决测试结束后 builtin backends 未恢复、污染后续 `unified_motion` 套件的问题 |
| `tests/test_motion_vector_baker.py` | 用本地 `Generator` 固定 temporal-consistency 帧夹具 | `warp_error` / `warp_ssim_proxy` / `coverage` 可做精确字典断言 |
| `tests/test_session065_research_modules.py` | 移除 `np.random.seed()` 与分散 `randn` / `randint` 用法 | SparseCtrl 与 KD-tree 研究回归不再泄漏全局随机状态 |
| `tests/test_session065_research_modules.py` | 增加精确 temporal score、motion-vector 编码像素/均值/和校验 | 将“能跑通”升级为“输出数值正确且稳定” |
| `tests/test_evaluator.py` | 固定 `_rgb_to_oklab()` 批量输入 | 增加首行与均值基线，避免仅靠 shape 掩盖颜色空间回归 |
| `tests/test_oklab.py` | 固定量化输入图像 | 将“unique color 数量减少”升级为精确颜色集合、均值与左上角像素断言 |
| `tests/test_p1_b3_2_rl_reference.py` | 用 `default_rng(42)` 替换 `RandomState(42)`，并显式导入 builtin backend 注册模块 | RL 参考测试的随机输入更现代、更隔离，且不再依赖外部执行顺序导入 |
| `tests/test_dimension_uplift.py` | 用 `default_rng(42)` 替换 `RandomState(42)` | 3D SDF cache 误差采样点可稳定回放 |
| `research/session097_test_determinism_research_notes.md` | 新增外部研究与仓库审计记录 | 保留 determinism / PRNG / flaky test 的外部依据与热点清单 |

本轮最关键的“额外收益”是发现并修掉了一个**测试层全局状态污染问题**。`test_motion_adaptive_keyframe.py` 的 watcher 热重载用例会在测试内部 reset 全局 backend registry，如果 teardown 只恢复 `motion_adaptive_keyframe` 而不恢复 builtins，那么之后的 `unified_motion` 回归就会因 registry 被掏空而失败。这个问题并不是随机输入本身，但它和 flaky / non-deterministic test 的治理目标完全一致：**任何测试都不允许把全局状态污染留给下一组测试接盘**。因此，本轮把 teardown 扩展为显式 reload builtin backends，再 reload motion-adaptive backend，从而恢复稳定、可隔离的全局注册面。

## Why This Fix Is Architecturally Correct

这次修复之所以正确，在于它没有采用“给整个进程统一 `np.random.seed(42)`”这种看似简单、实则隐藏耦合的做法。NEP 19 的核心建议是：**随机状态应该被当成显式依赖传递和管理，而不是作为模块级共享隐变量** [2]。本轮所有修复都遵循这个原则：每个测试 helper 或测试函数都通过自己的 `Generator` 生成样本，从而做到输入可追溯、失败可回放、不同用例之间互不干扰。

从测试工程视角看，把弱断言升级为值级断言也同样重要。Fowler 讨论 non-determinism 时反复强调，测试必须提供可信、可解释的反馈 [1]。如果一个测试只断言“shape 没变”“颜色数量没超标”“score 在 0 到 1 之间”，那么真实输出即便已经发生明显漂移，测试也可能继续绿灯。本轮在 keyframe、motion-vector、OKLAB 和 quantization 路径上补入了固定值级基线后，回归不再只能以“粗略性质”暴露，而会以**具体数值差异**直接暴露，从而显著提高 CI 信号密度。

Google 对 flaky tests 的治理实践也说明，可靠测试不仅要避免随机输入，还要避免顺序依赖与环境偶然性 [3]。这正是本轮顺手修复 registry teardown 的原因。一个理想的测试文件，应该既能单独运行，也能和其它测试一起运行，不会因为先后顺序不同而得到不同结果。`BackendFileWatcher` 用例的清理逻辑现在已经朝这个方向完成闭环。

## Test Closure

| Validation Layer | Scope | Result |
|---|---|---|
| 定向回归 | `tests/test_evaluator.py` `tests/test_oklab.py` `tests/test_motion_adaptive_keyframe.py` `tests/test_motion_vector_baker.py` `tests/test_session065_research_modules.py` `tests/test_p1_b3_2_rl_reference.py` `tests/test_dimension_uplift.py` | `247 PASS, 0 FAIL` |
| 广域审计 | `pytest -q` 全仓测试 | `1564 PASS, 97 FAIL, 7 SKIP` |

定向回归已经完成闭环，覆盖了本轮所有改动文件，因此可以确认这次 deterministic cleanup 本身已经稳定落地。广域审计也已执行，其暴露出的剩余失败并非 `HIGH-2.5` 新引入回归，而主要来自两类既有问题。第一类是**可选依赖缺失**，例如 `SciPy`、`Taichi` 等未安装导致的 sprite / taichi 相关测试失败。第二类是**更广范围的 registry/bootstrap 问题**，例如部分非本轮范围内的 suite 在全量运行时仍会遇到 builtin backend 空集合或注册恢复不全的问题。换言之，本轮已经把“RNG 掩盖与局部 registry 污染”这一块拆干净，但全仓 CI 仍需要一个下一阶段任务来处理环境与 bootstrap 的系统性收口。

## Files Touched This Session

| Category | Files |
|---|---|
| Tests | `tests/test_motion_adaptive_keyframe.py`, `tests/test_motion_vector_baker.py`, `tests/test_session065_research_modules.py`, `tests/test_evaluator.py`, `tests/test_oklab.py`, `tests/test_p1_b3_2_rl_reference.py`, `tests/test_dimension_uplift.py` |
| Research Notes | `research/session097_test_determinism_research_notes.md` |
| Project State | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## PROJECT_BRAIN Update Summary

`PROJECT_BRAIN.json` 已在本轮同步为 `SESSION-097` / `v0.87.0`。`HIGH-2.5-CI-RANDOM-MASKING-DETERMINISM` 已标记为 `CLOSED`，并写入 `completed_tasks`、`completed_work`、`closed_tasks_archive`、`session_summaries`、`recent_sessions` 与 `session_log`。同时，新的后续任务 **`HIGH-2.6-CI-OPTIONAL-DEPENDENCY-AND-REGISTRY-BOOTSTRAP`** 已进入 backlog，专门承接本轮广域审计暴露出的 full-suite 依赖与 bootstrap 闭环问题。

更新后的项目状态也更准确地区分了两件事。其一，`HIGH-2.5` 已经完成，因为“无种子随机掩盖与弱断言热点”确实已从审计范围内移除。其二，全仓 CI 还没有完全闭环，因为 optional deps 与剩余 registry/bootstrap 缺口依然存在。这种拆分比把所有问题都混在一个任务里更利于后续追踪，也更符合项目当前的架构治理节奏。

## Preparation Notes for HIGH-2.6

下一轮 `HIGH-2.6` 的重点，应该从“输入随机性治理”切换到“环境与 bootstrap 稳定性治理”。首先，需要梳理哪些测试是真正**必须**依赖 `SciPy`、`Taichi` 之类的重型可选依赖，哪些其实应该通过 `pytest.importorskip()`、feature flag、或更明确的 environment marker 做条件化收口。否则 full-suite 会继续因为环境差异而失真，CI 结果也很难具有跨机器一致性。

其次，需要扩展本轮对 `BackendFileWatcher` teardown 的修复思路，把所有涉及 `BackendRegistry.reset()`、动态导入、热重载、或 registry monkeypatch 的测试都做一次统一审计。凡是会修改全局注册面的测试，都必须在结束时显式恢复 builtin backends 与目标 backend 插件，而不能默认指望后续套件自行修复环境。

再次，建议把本轮成功的 deterministic fixture 模式抽象成更通用的测试 helper 规范。例如，按模块提供 `make_rng(seed)`、`scenario_catalog()`、`snapshot_assert_*()` 等 helper，可以把未来新测试天然拉到“局部 RNG + 显式场景 + 值级断言”的正确轨道上，避免项目再次回到“想测点东西就随手 `np.random.rand()`”的状态。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `HIGH-2.6-CI-OPTIONAL-DEPENDENCY-AND-REGISTRY-BOOTSTRAP` | 收敛 SciPy/Taichi 等可选依赖策略，并系统修复 full-suite 的 registry/bootstrap 漏口 |
| 2 | 抽象通用 deterministic fixture / snapshot helper | 把本轮成功模式推广到更多测试模块，继续提高 CI 信号密度 |
| 3 | 保留 `research/session097_test_determinism_research_notes.md` | 作为后续 flaky-test 治理、属性测试预算固定、PRNG 规范化的长期参考 |

## References

[1]: https://martinfowler.com/articles/nonDeterminism.html "Martin Fowler - Eradicating Non-Determinism in Tests"
[2]: https://numpy.org/neps/nep-0019-rng-policy.html "NumPy NEP 19 — Random number generator policy"
[3]: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html "Google Testing Blog - Flaky Tests at Google and How We Mitigate Them"
[4]: https://martinfowler.com/articles/continuousIntegration.html "Martin Fowler - Continuous Integration"
