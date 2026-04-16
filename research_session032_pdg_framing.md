# SESSION-032 Research Framing — Gap 1 Architecture Closure

## Trigger

用户要求围绕项目的 Gap 1，启动研究协议研究 **过程化依赖图（PDG）**、**通用场景描述（USD）**、工业界过程化内容生成（PCG）架构，以及 Oskar Stålberg / Houdini / Pixar USD 团队相关方向，并将研究成果实际融合落实到项目代码中，打通 `WFC → Shader → Export` 的断裂。

## Subsystem

`mathart.pipeline` 顶层生产管线、`mathart.level.wfc`、`mathart.shader.generator`、`mathart.export.bridge`，以及与三层进化系统的连接层。

## Decision Needed

需要确定：

1. 当前仓库应采用什么样的**轻量级 DAG / PDG 编排层**来取代写死的串行调用；
2. WFC 的 ASCII / tile 结果如何作为标准化场景数据传给 shader 与 export；
3. 是否有必要在当前阶段引入 **USD 风格的统一场景描述层**，以及应以何种轻量形式落地；
4. 如何把新的闭环结构写回三层进化与项目状态系统，避免只是增加一层“漂亮但不用”的框架。

## Already Known

仓库当前已有：

- `mathart.level.wfc.WFCGenerator`，输出 ASCII 关卡字符串；
- `mathart.shader.generator.ShaderCodeGenerator`，输出 Unity HLSL / ShaderGraph 文本文件；
- `mathart.export.bridge.AssetExporter`，已经支持带元数据的 Unity 就绪导出；
- `mathart.pipeline.AssetPipeline`，但尚未把上述三个模块做成顶层一等公民；
- `COMMERCIAL_BENCHMARK.md` 已明确把这条架构闭环列为高优先级差距。

## Duplicate Forbidden

本轮禁止重复研究以下已吸收方向：

- 通用 WFC 基础原理；
- 一般性程序化角色生成；
- SESSION-031 的 SMPL / VPoser / Motion Matching / Dual Quaternion；
- 已有的普通 shader 片段生成与导出模块内部机制。

## Success Signal

只有能提供以下至少一类直接价值的资料才保留：

1. **DAG / PDG 的可执行节点模型**，可直接映射到 Python 代码；
2. **USD / 场景描述层** 如何作为跨模块中间表示，而不是空洞概念；
3. **工业 PCG 架构** 中 WFC / 布局 / render / export 间的数据流设计；
4. 可直接指导 MarioTrickster-MathArt 在本轮实现的接口、数据结构、测试或演化回写方式。

## Stop Condition

当两个以上强来源对“轻量 DAG 编排 + 统一场景描述中间层 + 可插拔节点执行”的方案形成一致支持，并且下一步代码实现已经清晰时，立即停止继续搜索并转入实现。

## Browser Findings — Houdini PDG and OpenUSD

### Source A — Houdini PDG / TOPs official docs

Houdini 官方将 PDG 定义为一种 **defines tasks and their dependencies to structure, schedule, and distribute work** 的过程化架构 [1]。其最关键的工程启发不是“节点图很酷”，而是：

1. 上游输入会被转成 **work items**；
2. 每个 work item 携带可继承的 **attributes**；
3. 下游节点只消费属性，不需要重新理解上游内部实现；
4. 图中同时支持 **静态生成** 与 **动态生成** 的 work items；
5. `Wait for All` / partition 这类节点负责把动态多项结果重新收敛成可继续处理的单元。

这对 MarioTrickster-MathArt 的直接含义是：

- `WFCGenerator` 不应只返回裸 ASCII 字符串，而应产生带属性的 `work item` 风格结果；
- Shader 与 Export 节点不应直接耦合 WFC 内部算法，只应消费标准化 attributes，例如 level_ascii、tile_grid、theme、render_targets、export_targets；
- 需要一个轻量执行器来负责 DAG 拓扑排序、缓存、上下文传递和 `wait_all` 风格的汇聚。

### Source B — OpenUSD intro docs

OpenUSD 强调场景数据应组织为 **hierarchical namespaces of Prims**，每个 prim 可以拥有 attributes、relationships 和 metadata，并分布在 layers 中 [2]。更重要的是，USD 的核心并不只是文件格式，而是**统一场景描述 + 可组合覆盖**：强层可以在不破坏弱层的情况下新增、覆盖、屏蔽和重排内容 [2]。

这对当前项目的蒸馏式落地结论是：

- 本轮没必要直接引入完整 USD 依赖；
- 但非常有必要引入一个 **USD-like lightweight scene description**，即：
  - 统一的 scene / prim / attributes / relationships / metadata 结构；
  - 允许 WFC、Shader、Export 在同一场景对象上追加各自层的信息；
  - 为未来 2D、伪 3D、AI 渲染和引擎导出共享同一中间表示打基础。

### Current Implementation Direction Becoming Clear

综合 A 与 B，当前最清晰的本轮落地方向是：

1. 在仓库中新增 **轻量级 PDG 执行层**，节点执行单位输出/消费标准上下文；
2. 新增 **轻量级场景描述对象**，作为 USD 思想的项目内蒸馏版本，而不是直接上完整 OpenUSD；
3. 将 `WFC → SceneDescription → ShaderArtifact / ExportBundle` 串成可复用 DAG；
4. 再把该闭环接回 `AssetPipeline` 与三层进化的知识蒸馏中。

## References

[1]: https://www.sidefx.com/docs/houdini/tops/intro.html "Introduction to PDG and TOPs"
[2]: https://openusd.org/release/intro.html "Introduction to USD — Universal Scene Description"

## Browser Findings — Townscaper and PDG Workflow Example

### Source C — Townscaper case study

Townscaper 的关键不是单独的 WFC，而是把 **规则求解、拓扑装配、装饰填充、最终表现** 分成多个层级 [3]。文中最有价值的几点是：

1. WFC 先解决“**这个格子可能是什么模块**”，装饰与表现后置；
2. 一个局部改动会触发周边结构重新评估，说明上游布局结果是下游表现的条件源；
3. 小尺度装饰规则和高优先级 recipes 可以作为独立层附着在同一结构之上；
4. 视觉呈现层并不一定直接嵌在 tile 本体里，而可以基于 topology / stencil / surface 再生成。

这对本项目的蒸馏结论是：

- `WFC node` 应只负责输出结构解，而不是直接把最终表现写死；
- 应允许 `decorate` / `shader_plan` / `export_plan` 作为后续节点消费同一场景描述；
- Scene description 中需要记录 topology-aware 信息，而不只是 ascii 字符串。

### Source D — SideFX PDG FX Workflow tutorial

SideFX 教程给出的基础范式是：先定义 variations，再缓存几何、导入模拟输出、过滤、渲染、切换、合成、分区收集，最终汇聚成最终产物 [4]。这说明一个工业 PDG 流水线的最小可迁移要点包括：

1. **每个阶段都应是可独立缓存、可复跑、可替换的节点**；
2. 需要显式的 **switch / partition / collect / wait_all** 语义；
3. 结果既可以是文件，也可以是元数据与属性；
4. 执行器需要知道哪些节点是 fan-out，哪些节点是 gather。

映射到当前项目，最合理的第一版节点职责是：

- `wfc_generate`: 产出关卡矩阵与 scene prims；
- `scene_describe`: 将 ASCII / tag / theme 转成统一场景层；
- `shader_plan` / `shader_generate`: 由 scene 属性决定 shader 类型与 preset；
- `export_plan` / `export_assets`: 由场景与关卡约束决定导出目标；
- `collect_bundle`: 汇总所有中间结果，供 pipeline 与三层进化系统消费。

### Consolidated Design Decision

截至目前，研究已经足以支持本轮代码实现，不再需要继续扩展搜索范围。当前应立即转入实现：

1. 新增轻量级 PDG / DAG 模块；
2. 新增 USD-like scene description 模块；
3. 在主流水线中接入 `produce_level_pack()` 或等价入口，把 WFC、shader、export 真正串起来；
4. 把 DAG 执行记录和场景摘要接回三层进化知识蒸馏。

## References

[3]: https://www.gamedeveloper.com/game-platforms/how-townscaper-works-a-story-four-games-in-the-making "How Townscaper Works: A Story Four Games in the Making"
[4]: https://www.sidefx.com/docs/houdini/tops/tutorial_pdgfxworkflow.html "PDG Tutorial 1 FX Workflow"
