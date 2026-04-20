# SESSION-097 Verification Layer Research Notes

## Source 1 — Martin Fowler, Eradicating Non-Determinism in Tests

- URL: https://martinfowler.com/articles/nonDeterminism.html
- Core conclusion: non-deterministic tests destroy the value of an automated regression suite because failures stop being trustworthy.
- Strong implementation consequences for this repository:
  - tests must remain isolated from each other and runnable in any order;
  - any randomness used in tests must be locally controlled rather than depending on cross-test shared state;
  - quarantine may be a temporary mitigation for flaky tests, but the real goal is to eliminate the source of non-determinism.

## Source 2 — NumPy NEP 19, Random number generator policy

- URL: https://numpy.org/neps/nep-0019-rng-policy.html
- Core conclusion: the implicit global `RandomState` behind `numpy.random.*` convenience functions is problematic, especially with concurrency, and should be avoided when reproducibility matters.
- Strong implementation consequences for this repository:
  - replace bare `np.random.*` convenience calls in tests with explicit generator instances;
  - prefer `np.random.default_rng(seed)` and pass generator objects down into helper calls;
  - do not “solve” determinism by writing a single top-level `np.random.seed(42)` because that still relies on mutable global state.

## Interim implementation rules

| Topic | Required rule |
|---|---|
| Test determinism | Every test should be reproducible when run alone and when run in a suite. |
| RNG control | Each test or fixture should create its own explicit generator instance. |
| Anti-pattern | Avoid module-global `np.random.seed()` and bare `np.random.rand()/randn()/random()` convenience calls. |
| CI reliability | Once random inputs are fixed, assertions should move from weak shape/existence checks toward value-level expectations where practical. |

## Additional source alignment

### Google Testing Blog — Flaky Tests at Google and How We Mitigate Them

Google 将 flaky test 定义为**同一份代码下既可能通过也可能失败**的测试结果，并给出关键统计：约 1.5% 的测试执行会呈现 flaky 结果，约 16% 的测试带有某种程度的 flakiness，而 post-submit 中约 84% 的 pass→fail 转换最终涉及 flaky tests。对本仓库的直接含义是：一旦测试结果不稳定，开发者就会开始忽略真正的失败信号，CI 作为裁决系统的可信度会被迅速侵蚀。因此，本轮不能满足于“多数时候能过”，而必须把随机输入与断言设计成在固定代码下稳定地产生同一结论。

### Martin Fowler — Continuous Integration

Fowler 对 CI 的关键要求是 **self-testing build**。程序“能跑”并不代表“做对了事”，而持续集成的前提正是：每次集成都由自动化测试套件进行验证，任何失败都足以让整个构建变红。对本仓库的直接含义是：当输入已经被显式固定时，测试就不应只验证形状、存在性或“不报错”，而应尽量验证可重复的数值语义；否则 CI 看到的仍只是低信息量绿灯，无法承担高可信门禁职责。

| Source | Key takeaway | Implementation consequence |
|---|---|---|
| Google Testing Blog (2016) | Flaky tests consume大量调查成本，并诱导开发者忽略真正失败。 | 必须消灭测试中的随机状态污染与跨测试耦合。 |
| Fowler CI | CI 需要 self-testing build，任何失败都应被当成真实信号。 | 固定输入后，应把弱断言升级为值级别强断言。 |
| NEP 19 | 全局 `numpy.random.*` 在并发和可复现性上有系统性问题。 | 测试中使用局部 `default_rng(seed)`，并显式传递 RNG。 |

## Working implementation doctrine for SESSION-097

本轮代码落地应遵守三条工作准则。第一，测试随机性必须是**显式依赖**，不能是模块级隐式全局状态。第二，测试确定性必须在**单测独跑**与**整套并跑**两种模式下同时成立。第三，一旦输入数据被固定，断言就应尽量从结构级升级到**值级别**，让微小数值回归能够在 CI 中被稳定观测。

## Repository audit — nondeterministic verification hotspots

本地代码审计显示，当前 `tests/` 目录内仍存在一批依赖全局 NumPy 随机状态或旧式 `RandomState` 的验证用例。按命中次数看，主要热点集中在 `tests/test_session065_research_modules.py`、`tests/test_motion_adaptive_keyframe.py`、`tests/test_motion_vector_baker.py`、`tests/test_evaluator.py`、`tests/test_oklab.py`、`tests/test_p1_b3_2_rl_reference.py` 与 `tests/test_dimension_uplift.py`。其中，`test_motion_adaptive_keyframe.py` 与 `test_motion_vector_baker.py` 属于用户明确点名的高优先级目标，而 `test_session065_research_modules.py` 是当前审计中命中最多的旧式随机生成集中区之一。

| File | Current pattern | Refactor priority | Expected action |
|---|---|---|---|
| `tests/test_motion_adaptive_keyframe.py` | 多处 `np.random.rand(...)` 直接生成 score 序列 | High | 以局部 `default_rng(seed)` 生成固定高方差序列，并把边界/间隔断言补强为值级或精确列表断言 |
| `tests/test_motion_vector_baker.py` | `np.random.randint(...)` 生成帧图像 | High | 以局部 RNG 生成固定像素阵列，并将 temporal metrics 断言升级为可重复数值断言 |
| `tests/test_evaluator.py` | `np.random.rand(...)` 仅验证输出 shape | High | 改为固定局部 RNG 输入，并增加 OKLAB 数值范围/基准点断言 |
| `tests/test_oklab.py` | `np.random.randint(...)` 仅验证颜色数量减少 | Medium | 改为固定局部 RNG 图像，增加量化输出唯一色集合与均值等稳定断言 |
| `tests/test_session065_research_modules.py` | `np.random.seed()`、`np.random.randn()`、`np.random.randint()` | High | 去除全局 seed，重写为每个测试或 helper 内部显式 RNG，并在可计算处加入值级断言 |
| `tests/test_p1_b3_2_rl_reference.py` | `np.random.RandomState(42)` | Medium | 统一替换为 `default_rng(42)`，保持 reward 区间验证不变或酌情补强 |
| `tests/test_dimension_uplift.py` | `np.random.RandomState(42)` | Medium | 替换为 `default_rng(42)`，保持采样点确定性与误差上界稳定 |

当前审计结论是：本轮不应采用任何模块级 `np.random.seed(42)` 的“全局镇压”做法，而应在每个测试上下文或测试 helper 内构造局部生成器，并把生成样本视为显式测试夹具的一部分。这样既符合 NEP 19 的显式随机状态管理建议，也满足 Fowler 与 Google 对高可信 CI 的要求。
