# P1-ARCH-4 Research Notes（SESSION-103）

## 1. SideFX Houdini PDG / TOPs

来源：<https://www.sidefx.com/docs/houdini/tops/pdg/index.html>

当前提炼出的核心设计约束如下。

| 主题 | 提炼结论 | 对 P1-ARCH-4 的直接约束 |
|---|---|---|
| WorkItem 中心模型 | PDG 把运行时执行单元建模为 **WorkItem**，节点参数表达式可显式访问当前 work item 的 attributes / inputs / local context。 | 轻量 DAG 不能继续只在节点之间传裸 `dict`；必须把运行期单元提升为强类型 WorkItem。 |
| 节点逻辑与调度分离 | PDG 文档把 `Graph`、`Node`、`WorkItem`、`Scheduler` 明确区分，说明“图定义 / 节点计算 / 调度执行”是分层责任。 | P1-ARCH-4 不能把 fan-out/fan-in 和缓存判断硬塞进单个节点函数内部，必须在图运行时层物理实现。 |
| Partition 语义 | SideFX 提供 `PartitionHolder` 等专门对象管理 partition 内 work items，说明“聚合”不是普通节点顺手 `append`，而是有独立生命周期和收集边界。 | Collect / Fan-in 必须成为显式调度原语，而不是“下游节点自己遍历上游列表”的伪拓扑。 |
| 动态生成 | PDG 支持运行期生成 work items，说明 DAG 结构在执行时可以基于数据裂变。 | `run()` 语义需要支持节点输出 1→N 的工作项扩展，而不只是静态串行节点表。 |

## 2. Bazel Remote Caching / Hermetic Cache

来源：<https://bazel.build/remote/caching>

| 主题 | 提炼结论 | 对 P1-ARCH-4 的直接约束 |
|---|---|---|
| Action Cache | Bazel 维护 **action hash → result metadata** 的映射。 | PDG v2 需要把“节点执行签名”和“产物元数据”分离存储，不能只缓存原始结果对象。 |
| CAS | Bazel 维护 **content-addressable store (CAS)**，按内容哈希存取输出文件。 | 输出物必须按内容哈希 / cache key 落盘或编目，避免使用 Python 内存对象身份作为缓存依据。 |
| Hermetic Inputs | Bazel 的 action hash 由输入、命令行、环境等共同决定。 | `cache_key` 必须至少覆盖：节点 identity、节点参数、运行环境上下文、上游负载摘要。 |
| Cache Hit Short-Circuit | 命中缓存时直接复用既有结果而不是重跑动作。 | 命中缓存必须触发 dry-run 短路，且测试要锁死“底层算子重算次数严格为 0”。 |
| Metadata / Payload 分离 | Action cache 保存结果元数据，CAS 保存内容本体。 | PDG v2 可以采用“运行索引 + 产物负载”双层缓存模型，减小热路径重复序列化开销。 |

## 3. 当前初步设计警戒线

| 红线 | 说明 |
|---|---|
| Anti-Fake Topology | 禁止在旧单函数里用 `for` 循环伪装 fan-out。必须存在真实的 work queue / barrier / collect 运行时语义。 |
| Anti-Fake Cache | 禁止使用 `id()`、`hash()` 或进程内全局字典模拟跨生命周期缓存。必须使用稳定序列化 + SHA-256。 |
| Zero Regression | 必须通过 Facade 与兼容层让现有单线串行 DAG 当作 `fan-out=1` 的特例继续运行。 |

## 4. Apache Airflow Dynamic Task Mapping

来源：<https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html>

| 主题 | 提炼结论 | 对 P1-ARCH-4 的直接约束 |
|---|---|---|
| Runtime Expansion | Airflow 的 dynamic task mapping 明确把任务实例的创建推迟到 **运行时**，由 scheduler 根据上游输出确定映射规模。 | PDG v2 的 fan-out 不能在图构建期静态展开；必须允许节点在执行结果出来后再派生子 work items。 |
| Map→Reduce | Airflow 显式支持 mapped task 的 collected output，被下游 reduce 任务消费。 | Collect 节点必须等待其聚合域内全部上游分支完成，再一次性看到完整输入。 |
| Lazy Aggregation | Airflow 对映射结果使用 lazy proxy，避免在 fan-in 阶段过早物化全部大列表。 | P1-ARCH-4 需要谨慎设计聚合 payload，避免无脑复制大对象导致内存膨胀。 |
| Repeated Mapping | 一个 mapped task 的结果可以继续作为下一个 mapped task 的输入。 | Fan-out 不能是一次性特例；运行时必须支持连续裂变链。 |

## 5. SideFX PartitionHolder（Fan-in / Collect 参考）

来源：<https://www.sidefx.com/docs/houdini/tops/pdg/PartitionHolder.html>

| 主题 | 提炼结论 | 对 P1-ARCH-4 的直接约束 |
|---|---|---|
| Partition 容器 | `PartitionHolder` 明确以独立对象管理多个 work items 属于哪个 partition。 | Collect 必须拥有独立的数据结构来跟踪“某一聚合键下有哪些 work items”。 |
| Named / Indexed Partitions | SideFX 支持按 index 或 name 放入 partition。 | 我们的 fan-in 设计应允许按稳定的 `partition_key` / `collect_key` 聚合，而不只是按节点名粗暴合并。 |
| Split Attributes | 文档显式暴露 split attribute / split value / missing split items。 | WorkItem 需要有稳定的 attributes/metadata，以支撑后续按属性分裂、收集和错误诊断。 |
| Add-to-all 语义 | `addItemToAllPartitions()` 说明存在广播式分发场景。 | 运行时后续应保留广播 work item 到多个 partition 的可扩展性，但本轮至少先支持 1→N 与 N→1。 |

## 6. 更新后的实现草案

| 层级 | 应落地的物理结构 |
|---|---|
| 数据契约层 | `WorkItem` 冻结 dataclass；显式区分 `payload`、`attributes`、`parent_ids`、`partition_key`、`cache_key`。 |
| 图定义层 | `PDGNode` 保持声明式，但需补充节点参数、运行策略、是否 partition / collect 等元信息。 |
| 运行时层 | 引入独立 work queue、cache store、fan-out 派发、fan-in barrier/collector；旧串行执行视作单 work item 特例。 |
| 兼容层 | 通过 facade 保持现有 `graph.run(...)->dict` 形态，避免上层 `AssetPipeline` / `MicrokernelOrchestrator` 回归。 |
| 验证层 | 白盒断言至少覆盖：缓存命中零重算、1→N 裂变完整、N→1 聚合完整、旧 API 无回归。 |

## 7. 仓库现状审计（代码事实）

已审计文件：`mathart/level/pdg.py`、`tests/test_level_pdg.py`、`mathart/pipeline.py`、`mathart/core/microkernel_orchestrator.py`。

| 观察点 | 当前事实 | 结论 |
|---|---|---|
| Work unit | `ProceduralDependencyGraph.run()` 只在 `results[name]` 和 `context[name]` 中传递裸 `dict`。 | 尚不存在强类型 WorkItem 契约。 |
| Scheduler | `run()` 按拓扑顺序线性 `for name in order` 执行。 | 当前完全没有 fan-out/fan-in 的真实运行时语义。 |
| Cache | 代码中没有任何稳定 cache key / CAS / action metadata 存储。 | 当前 DAG 每次运行必定重算。 |
| Trace | 只有 `PDGTraceEntry(node_name, dependencies, duration_ms, output_keys)`。 | 追踪粒度仍停留在节点级，而非 work-item 级。 |
| Collect | `bundle_level` 只是普通多依赖节点。 | 目前所谓 collect 只是“节点看到所有依赖字典”，不存在 barrier / partition 概念。 |
| Pipeline 接入 | `AssetPipeline.produce_level_pack()` 直接实例化 `ProceduralDependencyGraph`，并以现有 `graph.run(...)->dict` 结果回填 bundle。 | 兼容层必须保留旧返回形态，避免上层主干回归。 |
| Microkernel 现状 | `MicrokernelOrchestrator` 强调 registry、typed artifact、三层进化循环，但当前 level PDG 仍是轻量本地运行时。 | P1-ARCH-4 需要提供 facade/bridge，让 PDG v2 能与微内核语义对齐，而不是绕开主干原则。 |
| 测试缺口 | `tests/test_level_pdg.py` 只验证依赖顺序与 bundle 产物存在。 | 必须新增白盒测试来锁死 cache hit 零重算、1→N、N→1 聚合完整性。 |

## 8. 本轮实现方向（落地约束）

| 约束 | 实现方向 |
|---|---|
| 向下兼容 | 保持 `PDGNode(operation=context,deps->dict)` 旧调用约定可运行；新运行时在内部包装成 WorkItem 执行。 |
| 物理拓扑升级 | 引入独立的 work-item trace、partition queue、collect barrier，而不是继续在节点 operation 内部塞循环。 |
| 缓存语义 | 采用 `sha256(stable_json(node identity + params + env + upstream payload digests))` 作为 hermetic cache key。 |
| 生命周期隔离 | 缓存写入磁盘索引与 JSON payload；避免进程级全局 dict 累积导致 OOM。 |
| 测试策略 | 在不破坏现有 level pipeline 用法的前提下，新增面向 fan-out/fan-in/caching 的白盒 E2E 专项测试。 |
