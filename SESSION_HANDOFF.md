# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-104` |
| Focus | `P1-ARCH-4` 跟进收口 —— 让 PDG v2 具备真正的单机有界 Fan-out 调度语义 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `1726 PASS, 7 SKIP`（`python3.11 -m pytest tests/ -p no:cov`，无 FAIL） |
| Primary Files | `mathart/level/pdg.py`，`tests/test_level_pdg.py`，`research_notes_p1_arch4_session104.md`，`PROJECT_BRAIN.json`，`SESSION_HANDOFF.md` |

## Executive Summary

本轮不是从零重做 `P1-ARCH-4`，而是先对仓库头部已落地的 PDG v2 实现做了一次严格白盒审计。审计结果表明，`SESSION-103` 已经完成了 **WorkItem 契约、Hermetic SHA-256 磁盘缓存、Fan-out / Fan-in 运行时语义与 facade 向下兼容** 等关键升级，但距离本轮要求的“**单机特化版工业级 PDG 调度语义**”仍有一个剩余缺口：**mapped fan-out 虽然在数据结构上已存在，却仍通过顺序 `for` 循环执行，尚未真正进入有界本地并发池。** [1] [2] [3]

本轮的核心价值，就是把这个残余缺口彻底收口。`mathart/level/pdg.py` 现在在保持 `graph.run()` 外部返回契约不变的前提下，新增了 **可配置 `max_workers` 的本地有界调度路径**。对 Fan-out 产生的 mapped invocations，运行时不再顺序串行执行，而是通过 **受 `max_workers` 硬限制的本地线程池** 做物理派发；同时，结果汇总、trace 记录与 cache 统计仍在主线程按稳定顺序收口，确保 **Barrier/Fan-in 继续是运行时层职责，而不是 worker 内部互等 future 的伪拓扑**。[3]

与此同步，本轮还把磁盘缓存写入升级为**原子写入**，避免在并发 fan-out 下出现 CAS / Action Cache 文件竞争导致的脏读或半写入风险。新增白盒测试进一步证明了三条证据链：其一，哈希一致时旧算子真实重算次数仍然严格为零；其二，1→N fan-out 在 `max_workers=2` 时确实形成了**真实并发且上限被硬性钳住**；其三，N→1 collect 在并发执行之后没有产生 payload 污染、死锁或顺序漂移。全量回归达到 **1726 PASS / 7 SKIP / 0 FAIL**，说明此次收口没有破坏主干兼容性。[4]

## Research Alignment Audit

| Reference | Requested Industrial Principle | `SESSION-104` Concrete Closure |
|---|---|---|
| SideFX Houdini PDG / TOPs [1] | WorkItem 是运行期中心对象，Partition / Collect 是独立生命周期，调度与节点逻辑分层 | 继续保持 `WorkItem` 为执行单元，并把 mapped fan-out 的真实调度职责落在 runtime facade，而不是业务节点内部循环 |
| Bazel Remote Caching [2] | Action Cache 与 CAS 分层；内容哈希决定复用；命中缓存必须 dry-run 短路 | 保持 SHA-256 action key + payload digest 结构，并把并发写入改为原子落盘，避免并发下缓存污染 |
| Python `concurrent.futures` [3] | 使用受限 worker pool、安全 barrier 汇聚，并避免 worker 内 future 互等造成死锁 | 新增 `max_workers` 限流与有界 in-flight future 提交；汇聚发生在主线程，不让 worker 相互等待 |

本轮审计的结论非常明确：仓库现在终于不仅“看起来像 PDG”，而且在**单机特化的 bounded scheduler 语义**上也真正越过了最后一道坎。[1] [2] [3]

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/level/pdg.py` | `ProceduralDependencyGraph.__init__()` 新增 `max_workers` 与 `scheduler_backend` | 让 PDG 运行时具备显式单机调度上限配置，而非隐式无限 fan-out |
| `mathart/level/pdg.py` | `_execute_task_node()` 改为“顺序回退 + 有界并发路径”双态执行 | 保持向下兼容；只有当 `max_workers > 1` 且存在多 invocation 时才启用真实并发 |
| `mathart/level/pdg.py` | 新增 `_execute_task_invocations_concurrently()` | 以 bounded in-flight futures 的方式进行物理派发，避免一次性无限提交撑爆单机 |
| `mathart/level/pdg.py` | `_execute_invocation()` 改为返回 `_InvocationResult`，主线程统一汇聚 trace / cache stats | 避免多线程直接修改共享 trace/counter，消除并发污染与顺序不确定性 |
| `mathart/level/pdg.py` | `_PDGDiskCache` 新增原子 JSON 写入 | 让 AC / CAS 在并发 fan-out 下依然保持一致性与可恢复性 |
| `tests/test_level_pdg.py` | 新增 `test_pdg_v2_bounded_fan_out_respects_max_workers_and_collects_without_contamination()` | 白盒锁死“真实并发且不超过上限”“collect 无污染”“结果顺序稳定”三条证据链 |
| `research_notes_p1_arch4_session104.md` | 新增本轮审计与收口笔记 | 记录为什么 `SESSION-103` 还差最后一层调度语义，以及 `SESSION-104` 如何补齐 |

## White-Box Physical Proof

| Assertion | Evidence |
|---|---|
| 相同哈希真实重算次数等于 0 | 既有 `test_pdg_v2_hermetic_cache_short_circuits_recomputation()` 继续通过，说明本轮并发改造没有破坏 dry-run cache short-circuit |
| Fan-out 不是“旧 for 循环换名” | 新测试通过 `active/max_active` 计数器证明并发峰值真实达到 `2`，不是伪并发 |
| Fan-out 不会无限并发打爆单机 | 同一测试证明 `max_active == max_workers == 2`，没有越上限失控 |
| Fan-in / Collect 不污染数据 | 并发后的 collect 输出稳定为 `p0..p3` 对应 `10,20,30,40`，没有分支 payload 串线 |
| 主干零退化 | 全量回归 `1726 PASS / 7 SKIP / 0 FAIL`，无旧测试回归 |

## Validation Closure

| Validation Layer | Command | Result |
|---|---|---|
| PDG 专项白盒 | `python3.11 -m pytest tests/test_level_pdg.py -p no:cov` | **8 PASS / 0 FAIL** |
| 全量回归 | `python3.11 -m pytest tests/ -p no:cov` | **1726 PASS / 7 SKIP / 0 FAIL** |

全量回归过程中唯一出现的非代码阻塞是 sandbox 环境缺少 `watchdog`，补齐依赖后重新执行即恢复全绿。因此这不是本轮代码回归，而是本地验证环境差异；`PROJECT_BRAIN.json` 已同步记录该点，便于后续复现。[4]

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 严禁写回中心中枢 if/else | 已合规。本轮只修改 `mathart/level/pdg.py` 的 runtime substrate，没有破坏 registry / microkernel 主干原则。 |
| 严禁内存字典式伪缓存 | 已合规。缓存仍为本地磁盘 `ac/` + `cas/`，且在并发下升级为原子写入。 |
| 严禁 fan-out 伪拓扑 | 已合规。mapped invocations 已经走真实 executor 路径，而不是旧串行 `for` 循环。 |
| 主干向下兼容零退化 | 已合规。`graph.run()` 返回契约保持不变，全量回归通过。 |
| 单机硬件约束 | 已合规。未引入 Redis、Celery、RabbitMQ 或任何分布式框架；调度严格限定在本地线程池 + 本地磁盘缓存。 |

## Preparation Notes for P3-GPU-BENCH-1 on RTX 4070

本轮把 PDG v2 的**单机 bounded scheduler 底盘**补齐之后，下一步若要在当前这台 **i5-12600KF / 32GB RAM / RTX 4070 12GB** 工作站上无缝推进 `P3-GPU-BENCH-1`，已经不需要再推倒运行时，只需要做几项**微调准备**。

第一，应该把 benchmark scenario 正式提升为 **WorkItem 级契约**。`free_fall_cloud`、`sparse_cloth`、`cpu_reference`、`cuda_real_device`、`particle_budget`、`warmup_count`、`sample_count` 等都应成为显式 payload / attributes，而不是散落在脚本参数里。这样一来，PDG runtime 才能自然地把“场景 × 设备 × 粒子预算”映射成可 fan-out 的 benchmark 分区。[1] [3]

第二，benchmark 节点的缓存指纹还需要补齐**硬件环境指纹维度**。建议至少显式纳入 `device_name`、`driver_version`、`cuda_available`、`cuda_runtime_version`、`taichi_version`、`arch`、`precision_mode`、`snode_layout`。否则，同一测试场景在 CPU 参考模式和真实 CUDA 模式之间，仍有被误判为同一 cache lineage 的风险。[2]

第三，collect 节点需要从“普通聚合”进一步升级为**统计归约协议**。下一步建议 collect 统一输出：warmup 后中位数、P95、最差帧、样本数、对照组 drift / RMSE、设备信息、原始明细路径、可审计报告路径。这样即可直接为 `P3-GPU-BENCH-1` 形成生产可信度证据闭环。[3]

第四，大型 benchmark 轨迹和诊断数据不应直接塞进 JSON payload。现在的 PDG v2 已经适合把这些产物改为“**文件路径 + 内容摘要**”模式：payload 中只保留文件引用与 digest，本体落盘为 artifact。这样既能保持 hermetic key，又能守住 32GB RAM 和 12GB 显存的单机底线。[2]

第五，等 benchmark lane 真正接入主干时，仍然要坚持 **lane 化 / registry 化挂载**，不要把 CUDA benchmark 逻辑重新塞回中心路由。PDG v2 现在已经提供运行时底盘，下一步应该挂新的 benchmark backend，而不是破坏当前的 registry discipline。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | `P3-GPU-BENCH-1` 最后一公里 | 将场景、设备、预算、warmup/sample 变成显式 WorkItem，形成正式 benchmark DAG |
| 2 | `P1-ARCH-5` | 在稳定 PDG substrate 之上推进 OpenUSD-compatible scene interchange |
| 3 | 文件化 payload 规范 | 为 GPU benchmark / 工业渲染的大对象缓存建立 payload→artifact path 契约 |
| 4 | benchmark lane registry 接入 | 以 backend/lane 方式挂载 GPU benchmark，避免中心中枢退化为 if/else 总线 |

## References

[1]: https://www.sidefx.com/docs/houdini/tops/pdg/index.html "SideFX Houdini PDG / TOPs"
[2]: https://bazel.build/remote/caching "Bazel Remote Caching"
[3]: https://docs.python.org/3/library/concurrent.futures.html "Python concurrent.futures"
[4]: ./PROJECT_BRAIN.json "PROJECT_BRAIN.json"
