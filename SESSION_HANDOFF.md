# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-103` |
| Focus | `P1-ARCH-4` — 轻量级 DAG 核心升级为 PDG v2 运行时语义 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `1725 PASS, 7 SKIP`（`python3.11 -m pytest tests/ -p no:cov`，无 FAIL） |
| Primary Files | `mathart/level/pdg.py`，`mathart/level/__init__.py`，`mathart/pipeline.py`，`tests/test_level_pdg.py`，`research_notes_p1_arch4_session103.md` |

## Executive Summary

本轮聚焦 **P1-ARCH-4: PDG v2 runtime semantics**，目标不是给旧 DAG 套一层注释式包装，而是把仓库中原本“只有拓扑排序 + 裸 `dict` 传递”的轻量执行器，升级为具备 **冻结态 WorkItem 契约、Hermetic SHA-256 缓存、真实 Fan-out / Fan-in 调度原语，以及向下兼容 facade** 的工业化运行底盘。[1] [2] [3]

改造后的 `mathart/level/pdg.py` 不再把运行期单元视为“节点名 -> 输出字典”的松散映射，而是显式引入 `WorkItem`、`PDGFanOutItem`、`PDGFanOutResult`、磁盘式 Action Cache + CAS、映射分区派发、Collect 屏障汇聚和 work-item 级 trace。现有串行 DAG 未被破坏；旧图仍然作为 **fan-out = 1** 的特例继续运行，`ProceduralDependencyGraph.run()` 对外仍保持返回 `dict` 风格结果，确保 `AssetPipeline.produce_level_pack()` 等上游调用点零签名回归。[4] [5]

## Research Alignment

| Reference | Applied Principle | Concrete Landing |
|---|---|---|
| SideFX Houdini PDG / TOPs [1] | WorkItem 是运行时核心对象；节点逻辑与调度分层；Partition/Collect 有独立生命周期 | `WorkItem` 成为运行时传递单元；`_PDGv2RuntimeFacade` 单独负责映射、收集与缓存；`topology="collect"` 触发真实聚合路径 |
| Bazel Remote Caching [2] | Action Cache 与 CAS 分层；依据输入、环境与上游产物内容摘要命中缓存 | `_PDGDiskCache` 显式拆分 `ac/` 与 `cas/`；每次节点执行用稳定序列化 + SHA-256 生成 cache key |
| Apache Airflow Dynamic Task Mapping [3] | 1→N runtime expansion、map→reduce、lazy aggregate、repeated mapping | fan-out 节点通过 `PDGFanOutResult` 在运行时裂变 work items；普通 task 节点对分区 work items 自动映射；collect 节点建立 N→1 屏障 |
| Repository state / SESSION-102 handoff [4] [5] | 保持主干兼容与三层进化连续性 | `ProceduralDependencyGraph.run()` 输出仍兼容旧 `results/target_outputs/execution_order` 结构；level pipeline 只需显式设置隔离缓存目录 |

研究笔记已落盘到 `research_notes_p1_arch4_session103.md`，后续若继续扩展 OpenUSD 互换、GPU benchmark 或多轨微内核编排，可直接复用本轮约束结论。[6]

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/level/pdg.py` | 新增 `WorkItem`、`PDGFanOutItem`、`PDGFanOutResult` | 将运行期数据单元从裸 `dict` 提升为显式契约对象 |
| `mathart/level/pdg.py` | 新增 `_PDGDiskCache` | 以 Action Cache + CAS 结构实现基于 SHA-256 的跨生命周期磁盘缓存 |
| `mathart/level/pdg.py` | 新增 `_PDGv2RuntimeFacade` | 将拓扑排序、映射分区、collect 屏障、缓存判定与 trace 收集从节点逻辑中分离 |
| `mathart/level/pdg.py` | 扩展 `PDGNode(topology, config, cache_enabled, collect_by_partition)` | 允许节点声明真实运行时语义，而非在业务函数内部偷渡拓扑控制 |
| `mathart/level/pdg.py` | `ProceduralDependencyGraph.run()` 代理到 facade | 保留旧公共 API，同时让新运行时在物理层面接管执行 |
| `mathart/level/__init__.py` | 导出 `WorkItem` / `PDGFanOutItem` / `PDGFanOutResult` | 为白盒测试与后续主干接入暴露稳定类型入口 |
| `mathart/pipeline.py` | Level PDG 显式使用 `level_dir/.pdg_cache` | 避免默认工作目录缓存污染，落实生命周期隔离 |
| `tests/test_level_pdg.py` | 新增缓存、fan-out/fan-in、WorkItem 契约白盒测试 | 锁死“相同哈希零重算”“1→N、N→1 数据完备”“WorkItem 暴露 cache key/partition”三条硬约束 |

## Architecture Closure

| Red Line | Outcome |
|---|---|
| 防“改注释逃课” | 已合规。Fan-out、Collect、Cache 均在独立运行时路径中物理存在，不是旧 `for` 循环换名。 |
| 防“内存字典式伪缓存” | 已合规。缓存为磁盘 `ac/` + `cas/` 结构，cache key 基于稳定序列化与 SHA-256，未使用 `id()`、`hash()` 或模块级全局 dict。 |
| 防“主干零退化” | 已合规。`ProceduralDependencyGraph.run()` 仍返回兼容字典结果；旧串行 DAG 测试与 `AssetPipeline` 级回归全部通过。 |
| 防“生命周期污染/OOM” | 已合规。缓存目录显式落在图实例范围内，可按运行隔离清理，未引入长驻进程级结果池。 |

本轮真正完成的，不是“让 DAG 看起来更像 PDG”，而是把 **WorkItem / Cache / Fan-out / Fan-in** 从文档口号变成了可执行、可审计、可测试的代码事实。[1] [2] [3]

## Test Closure

| Validation Layer | Scope | Result |
|---|---|---|
| 专项白盒 | `python3.11 -m pytest tests/test_level_pdg.py -p no:cov` | **7 PASS / 0 FAIL** |
| 全量回归 | `python3.11 -m pytest tests/ -p no:cov` | **1725 PASS / 7 SKIP / 0 FAIL** |

本轮全量结果较 `SESSION-102` 多出 3 个新增通过项，来自本轮新增的 PDG v2 白盒测试。未修改任何既有断言去迁就新实现；旧回归在原契约下保持通过。[5]

## Files Touched This Session

| Category | Files |
|---|---|
| Production | `mathart/level/pdg.py`, `mathart/level/__init__.py`, `mathart/pipeline.py` |
| Tests | `tests/test_level_pdg.py` |
| Research | `research_notes_p1_arch4_session103.md` |
| Project State | `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## PROJECT_BRAIN Update Summary

`PROJECT_BRAIN.json` 需要同步为 `SESSION-103`：将 `P1-ARCH-4` 标记为 `CLOSED`，刷新 `last_session_id`、`last_updated`、`validation_pass_rate`、`recent_focus_snapshot`、`recent_sessions`、`session_summaries`、`session_log` 与 `resolved_issues`，并把本轮验证结果更新为 `1725 PASS / 7 SKIP / 0 FAIL`。[5]

## Preparation Notes for P3-GPU-BENCH-1

本轮打通 PDG v2 后，接下来若要无缝接入 **P3-GPU-BENCH-1（CUDA 硬件稀疏布料真实硬件验证与生产可信度证据闭环）**，当前架构只需做几处**微调准备**，而不再需要推倒重来。

### 1. 将 Benchmark Scenario 提升为独立 WorkItem 契约

GPU benchmark 的 `free_fall_cloud`、`sparse_cloth`、`CPU reference`、`CUDA real device`、`particle_budget`、`warmup_count`、`sample_count` 等，都应作为显式 payload/attributes 进入 `WorkItem`，而不是继续散落在脚本参数或局部变量中。这样一来，不同硬件、不同场景、不同预算将天然成为可 fan-out 的 benchmark 分区，且每个分区都有自己的 cache lineage 与 trace。

### 2. 为 GPU 证据链补充环境指纹维度

当前 cache key 已覆盖节点契约、上下文与上游产物，但若要进入真实 CUDA 证据闭环，建议在 benchmark 节点的 `config` / `context` 中显式纳入以下环境指纹：`device_name`、`driver_version`、`cuda_available`、`taichi_version`、`arch`、`precision_mode`、`snode_layout`。否则不同硬件环境下的 benchmark 可能被误判为同一缓存命中。

### 3. 为 Collect 节点补充统计归约协议

现在的 collect 已经能完成 N→1 屏障汇聚，但 GPU benchmark 下一步需要把多个 work items 规整成 **可信统计报告**。建议在 benchmark collect 节点上统一输出：样本数、warmup 后中位数、P95、最差帧、设备信息、误差界、对照组差异、原始报告路径。这样可直接对接后续 `ArtifactManifest` 或外部审计文档。

### 4. 给不可 JSON 化的大型数值产物增加“文件化 payload”约定

当前缓存层对 JSON 友好 payload 最稳定。若 GPU benchmark 要缓存更大的曲线、逐步诊断或粒子快照，建议采用“payload 保存文件路径 + 内容摘要，文件本体落盘”的约定，而不是把巨量数组直接塞进 payload。这样既保持 Hermetic key，又不会把 CAS 推向 OOM 或过度序列化开销。

### 5. 把 GPU benchmark 接入 registry/microkernel 时坚持 lane 化而非中心路由化

下一步若需要让 GPU benchmark 进入更大的主干编排，应新增独立 benchmark backend / lane，由 registry 挂载；不要把 CUDA 逻辑重新写回中心中枢的 `if/else`。PDG v2 现在已经提供 runtime substrate，下一步要做的是 **把 benchmark lane 挂在这块底盘上**，而不是破坏它。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `P3-GPU-BENCH-1` 最后一公里 | 利用当前 PDG v2 fan-out/fan-in + hermetic cache，把 CPU/GPU、场景、预算、设备证据编排成正式 benchmark DAG |
| 2 | `P1-ARCH-5` | 在 PDG v2 底盘稳定后推进 OpenUSD-compatible scene interchange，让场景与工序契约进一步统一 |
| 3 | `P1-B1-1` | 把 Jakobsen 链真正提升为可见二次动画成品，而不止是运动学元数据 |
| 4 | 文件化 payload 规范 | 为未来 GPU/工业渲染大对象缓存做好 payload→artifact path 约束 |

## References

[1]: https://www.sidefx.com/docs/houdini/tops/pdg/index.html "SideFX Houdini PDG / TOPs"
[2]: https://bazel.build/remote/caching "Bazel Remote Caching"
[3]: https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html "Apache Airflow Dynamic Task Mapping"
[4]: ./research_notes_p1_arch4_session103.md "research_notes_p1_arch4_session103.md"
[5]: ./PROJECT_BRAIN.json "PROJECT_BRAIN.json"
[6]: ./SESSION_HANDOFF.md "SESSION_HANDOFF.md"
