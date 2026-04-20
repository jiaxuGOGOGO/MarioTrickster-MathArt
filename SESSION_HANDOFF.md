# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-099` |
| Focus | `HIGH-2.7` Production code RNG dependency injection: refactor all bare `np.random` calls to NEP-19 compliant `np.random.default_rng` / `Generator` injection |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `1631 PASS, 0 FAIL, 8 SKIP` across full test suite |
| Primary Files | `mathart/animation/human_math.py`, `mathart/animation/rl_locomotion.py`, `mathart/animation/skill_embeddings.py`, `mathart/animation/smooth_morphology.py`, `mathart/evolution/session065_research_bridge.py`, `mathart/evolution/dimension_uplift_bridge.py`, `mathart/headless_e2e_ci.py`, `mathart/quality/visual_fitness.py`, `mathart/sdf/noise.py` |

## Executive Summary

本轮 `HIGH-2.7` 的核心目标，是将 SESSION-098 审计发现的 **43 处 bare `np.random` 用法**（分布在 8 个生产文件中）全部重构为 NEP-19 compliant 的 `np.random.default_rng` / `Generator` 依赖注入模式。这些调用使用全局随机状态，违反了 NumPy Enhancement Proposal 19 的显式依赖传递原则，导致测试之间存在隐性耦合风险。

重构策略严格遵守三条防混线护栏：

1. **防"全局污染逃课"红线**：没有在任何 `__init__.py` 或 `conftest.py` 中写 `np.random.seed(42)` 全局污染。每个上下文都通过显式传递 `default_rng` 实例来控制随机性。

2. **防"阉割物理复杂性"红线**：没有将任何随机矩阵替换为 `np.zeros()` 来降低测试难度。所有随机输入保持原有的高方差与扰动，确保算法鲁棒性得到充分压榨。

3. **防"生产代码被定死"红线**：没有在生产逻辑中把 seed 写死为 42。生产侧函数通过 `rng: np.random.Generator | None = None` 参数接受外部注入，`rng is None` 时构造无种子的 `default_rng()`，保持生产环境的真随机性。只有确定性投影矩阵（如 `human_math.py` 的 Johnson-Lindenstrauss 投影）使用固定种子缓存，这是算法正确性要求而非测试便利。

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/human_math.py` | `encode_to_latent` / `decode_from_latent` 中的 `np.random.seed(42)` + `np.random.randn` 替换为实例级缓存的 `_get_projection_matrix()` 使用 `default_rng(42)` | 确定性投影矩阵通过实例缓存生成，消除全局状态污染 |
| `mathart/animation/rl_locomotion.py` | `LocomotionEnv.reset`: `np.random.seed(seed)` → `self._rng = default_rng(seed)`；`LocomotionPolicy.act`: 添加 `rng` 参数；`_init_mlp`: 添加 `rng` 参数 | 环境重置和策略采样的随机性完全可注入 |
| `mathart/animation/skill_embeddings.py` | `SkillEncoder._init_mlp/sample`, `MotionDiscriminator.train_step/gradient_penalty`, `LowLevelController.act`, `HighLevelController.select_skill`, `SkillLibrary._register_default_skills`, `ASEFramework.pretrain_llc`: 全部添加 `rng` 参数 | ASE 框架全链路随机性可控 |
| `mathart/animation/smooth_morphology.py` | `MorphologyFactory.__init__`: `RandomState(seed)` → `default_rng(seed)`；所有 `randint` → `integers` | 形态学进化工厂迁移到新 API |
| `mathart/evolution/session065_research_bridge.py` | `ResearchModuleEvaluator` / `ResearchIntegrationTester`: 构造函数添加 `rng` 参数，内部 `np.random.seed(42)` + `np.random.randn` 替换为 `self._rng` | 研究桥评估器的随机性可注入 |
| `mathart/evolution/dimension_uplift_bridge.py` | 缓存精度测试: `RandomState(42)` → `default_rng(42)` | 消除 legacy RandomState 依赖 |
| `mathart/headless_e2e_ci.py` | `np.random.seed(HERMETIC_SEED)` → `default_rng(HERMETIC_SEED)`；测试函数使用局部生成器 | E2E CI 的随机性隔离 |
| `mathart/quality/visual_fitness.py` | 内嵌测试函数: `np.random.RandomState(42)` → `default_rng(42)`；`randint` → `integers` | 视觉适应度测试迁移到新 API |
| `mathart/sdf/noise.py` | `_build_permutation`: `RandomState(seed)` → `default_rng(seed)` | Perlin 噪声排列表生成迁移到新 API |

## Why This Fix Is Architecturally Correct

本轮修复严格遵守了项目的三条架构红线。

**第一，严禁越权修改主干。** 所有修改都局限在各自的生产模块内部，通过在函数签名中添加可选的 `rng` 参数来实现依赖注入。没有修改任何核心中枢（AssetPipeline / Orchestrator / BackendRegistry）。每个模块自行管理自己的随机数控制权移交。

**第二，独立封装挂载。** 每个文件的重构都是自包含的：`human_math.py` 使用实例级缓存投影矩阵，`rl_locomotion.py` 使用实例属性 `self._rng`，`skill_embeddings.py` 使用方法级 `rng` 参数。没有引入跨模块的全局随机数管理器或中央种子分发机制。

**第三，强类型契约。** 所有新增的 `rng` 参数都使用 `np.random.Generator | None` 类型注解，明确声明接口契约。`None` 默认值保持完全的向后兼容性——现有的所有调用方无需任何修改即可继续工作。

**第四，NEP-19 合规。** 从 legacy `np.random.RandomState` 和 bare `np.random` 全局状态，统一迁移到 `np.random.default_rng` / `np.random.Generator` 新 API。`randint` → `integers`，`np.random.randn(shape)` → `rng.standard_normal(shape)`，`np.random.rand(shape)` → `rng.random(shape)`。

## Test Closure

| Validation Layer | Scope | Result |
|---|---|---|
| Full test suite | 全部测试文件 | `1631 PASS, 0 FAIL, 8 SKIP` |
| Bare np.random audit | `grep -rn "np\.random\.\(seed\|randn\|rand\b\|randint\|RandomState\)" mathart/` | **0 matches** — 生产代码中不再有任何 bare np.random 用法 |
| Global seed audit | `grep -rn "np\.random\.seed" tests/` | **0 executable matches** — 仅 conftest.py docstring 中有禁止性说明 |

从基线的 **43 处 bare np.random 用法**降至 **0 处**，所有生产代码的随机数控制权已完全移交给外层调用方。

## Files Touched This Session

| Category | Files |
|---|---|
| Production (modified) | `mathart/animation/human_math.py`, `mathart/animation/rl_locomotion.py`, `mathart/animation/skill_embeddings.py`, `mathart/animation/smooth_morphology.py`, `mathart/evolution/session065_research_bridge.py`, `mathart/evolution/dimension_uplift_bridge.py`, `mathart/headless_e2e_ci.py`, `mathart/quality/visual_fitness.py`, `mathart/sdf/noise.py` |
| Project State | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## PROJECT_BRAIN Update Summary

`PROJECT_BRAIN.json` 已在本轮同步为 `SESSION-099` / `v0.89.0`。`HIGH-2.7-PRODUCTION-CODE-RNG-DEPENDENCY-INJECTION` 已标记为 `CLOSED`，并写入 `completed_tasks`、`closed_tasks_archive`、`session_summaries`、`recent_sessions`、`recent_focus_snapshot`、`session_log` 与 `resolved_issues`。`REMAINING-S098` 条目已替换为 `RESOLVED-S099`。

## Preparation Notes for Next Session

下一轮的重点应从以下三个方向中选择：

1. **PERF-1-EVOLUTION-LOOP-OOM**：`test_evolution_loop.py` 的 OOM 问题（单个测试文件 >2.4 GB RSS）值得作为独立的性能优化任务。需要审计 `run_evolution_cycle()` 的内存分配策略，可能涉及中间结果的及时释放、生成器模式替代列表累积、或分批处理。

2. **P1-ARCH-4**：继续 PDG v2 运行时语义闭合工作。

3. **P1-AI-2D-SPARSECTRL**：在真实 ComfyUI 环境中执行完整的 SparseCtrl + AnimateDiff 工作流。

此外，本轮建立的 NEP-19 依赖注入模式可以作为项目的标准实践推广到未来所有新增的随机数使用场景。建议在 `CONTRIBUTING.md` 或项目规范文档中明确记录这一模式要求。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `PERF-1-EVOLUTION-LOOP-OOM` | 审计 `run_evolution_cycle()` 内存使用，解决 `test_evolution_loop.py` OOM |
| 2 | `P1-ARCH-4` | 继续 PDG v2 运行时语义闭合 |
| 3 | 推广 NEP-19 模式到 CONTRIBUTING.md | 将本轮建立的依赖注入模式文档化为项目标准 |

## References

[1]: https://numpy.org/neps/nep-0019-rng-policy.html "NumPy NEP 19 — Random number generator policy"
[2]: https://martinfowler.com/articles/nonDeterminism.html "Martin Fowler - Eradicating Non-Determinism in Tests"
[3]: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html "Google Testing Blog - Flaky Tests at Google and How We Mitigate Them"
[4]: https://martinfowler.com/articles/continuousIntegration.html "Martin Fowler - Continuous Integration"
[5]: https://numpy.org/doc/stable/reference/random/generator.html "NumPy Random Generator API Reference"
