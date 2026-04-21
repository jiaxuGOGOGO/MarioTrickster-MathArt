# SESSION-123 External Research Notes — PDG SeedSequence, Functional PRNG, Hermetic Determinism

## NumPy SeedSequence / Spawn
NumPy 官方 `SeedSequence.spawn(n_children)` 明确将子序列裂变建模为对 `spawn_key` 的扩展，并返回一组新的 `SeedSequence` 对象，供每个并发子任务派生各自独立的 bit generator / generator。[1] 这意味着项目中的 PDG mapped fan-out 不应把同一个 `Generator` 共享给多个子 `WorkItem`，而应当在调度边界先得到一个父 `SeedSequence`，再为每个子任务裂变一个子 `SeedSequence`，最后各自构造 `np.random.default_rng(child_ss)`。

## JAX PRNG Design
JAX 的 PRNG 设计把目标直接写成七项，其中与本次任务最相关的是：**可复现**、**与编译/后端边界无关的语义稳定性**、**避免无数据依赖时的顺序约束**、以及**可扩展到多核/分布式并行**。[2] 它通过“显式传递随机键 + split 裂变”的函数式模式，避免全局隐式状态成为并行调度中的序列化瓶颈。[2] 对本仓库的直接启示是：PDG 调度器必须把 RNG 视为 `WorkItem` 上下文的一部分显式注入到底层动作，而不是依赖动作内部回退到 `default_rng()`。

## Interim Design Rule
现阶段可以先形成三条落地规则。第一，**父级随机源只在调度边界存在**；进入 mapped fan-out 后，必须裂变为一任务一生成器。第二，**调度顺序不能影响结果**；因此子流索引必须与 fan-out 项的稳定顺序绑定，而不是与线程实际抢占顺序绑定。第三，**底层动作要优先消费注入的 `rng`**，否则会触发 `rng or default_rng()` 的隐式回退，破坏缓存命中与跨线程确定性。

## References
[1]: https://numpy.org/doc/2.2/reference/random/bit_generators/generated/numpy.random.SeedSequence.spawn.html "NumPy — SeedSequence.spawn"
[2]: https://docs.jax.dev/en/latest/jep/263-prng.html "JAX documentation — JAX PRNG Design"

## Scientific Python / NEP-19 工程实践
Scientific Python 对 NumPy RNG 的工程建议非常直接：避免全局 `np.random.*` 与 `np.random.seed`，而是创建新的 `Generator` 并在代码中显式传递；它还直接引用了 NEP-19 的核心表述，指出隐式全局 `RandomState` 在**线程或其他并发形式**下会带来问题，而可复现伪随机数的首选实践是“实例化一个带种子的 generator object 并把它传来传去”。[3] 这与用户文档中的红线完全一致，意味着 PDG 调度器不仅要给子任务分配 RNG，还要确保动作签名链条优先消费该实例。

## Bazel Hermeticity
Bazel 官方把 hermeticity 定义为：在相同输入源码与产品配置下，构建系统总是返回相同输出，并且对宿主机环境变化不敏感。[4] 其两个关键词是 **Isolation** 与 **Source identity**。[4] 对本项目的直接映射是：`WorkItem` 的随机种子派生不能依赖时钟、线程抢占顺序、宿主状态或非声明式输入，而应绑定于显式主种子或稳定的 `WorkItem` 身份摘要（例如 action 名称、输入载荷、映射索引等）的哈希。

## Consolidated Landing Rules
综合目前四份资料，可以固化为以下落地规则。第一，`ThreadPoolExecutor` 只负责调度，不负责随机语义；随机语义必须在 fan-out 边界按稳定顺序先裂变完成。第二，若用户提供主种子，则以该主种子构造父 `SeedSequence`；若未提供，则必须以稳定哈希构造父级熵源，严禁使用系统时间。第三，`WorkItem` 的 trace / cache identity 与其派生随机语义必须一致，避免同一逻辑节点在不同线程编排下得到不同随机流。第四，底层任务若缺失注入 RNG，应通过白盒测试尽可能暴露此问题，而不是让并发路径悄悄回退到 `default_rng(None)`。

## References
[1]: https://numpy.org/doc/2.2/reference/random/bit_generators/generated/numpy.random.SeedSequence.spawn.html "NumPy — SeedSequence.spawn"
[2]: https://docs.jax.dev/en/latest/jep/263-prng.html "JAX documentation — JAX PRNG Design"
[3]: https://blog.scientific-python.org/numpy/numpy-rng/ "Scientific Python Blog — Best Practices for Using NumPy's Random Number Generators"
[4]: https://bazel.build/basics/hermeticity "Bazel — Hermeticity"
