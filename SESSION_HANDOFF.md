# SESSION_HANDOFF

## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-105` |
| Focus | `P3-GPU-BENCH-1` 生产化并网 —— 将 Taichi CUDA benchmark lane 正式接入 PDG v2 单机调度底座 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Base Head Before Commit | `23d0d0b` |
| Validation | `1729 PASS / 7 SKIP / 0 FAIL`（`python3.11 -m pytest tests/ -p no:cov`） |
| Targeted White-Box | `20 PASS / 2 SKIP / 0 FAIL`（`tests/test_level_pdg.py` + `tests/test_taichi_benchmark_backend.py`） |
| Benchmark Evidence In Sandbox | `cpu_fallback`（当前 sandbox 无可用 CUDA/Taichi 实机环境，因此未生成 RTX 4070 真机 CUDA 证据） |
| Primary Files | `mathart/level/pdg.py`，`mathart/core/taichi_xpbd_backend.py`，`mathart/animation/xpbd_taichi.py`，`tests/test_level_pdg.py`，`tests/test_taichi_benchmark_backend.py`，`tools/run_session085_gpu_benchmark.py`，`PROJECT_BRAIN.json` |

## Executive Summary

本轮工作的核心，不是再发明一个新的 benchmark harness，而是把 **`SESSION-085` 的 Taichi XPBD benchmark 能力** 真正推进到当前主干可生产编排、可审计、可防爆的 **PDG v2 runtime substrate** 里。上一轮 `SESSION-104` 已经把单机有界 Fan-out / Fan-in 调度语义补齐；本轮则继续向前，把 **GPU 作为稀缺资源** 显式纳入调度协议，使 CPU 线程不再能无约束地同时冲击单张 RTX 4070 的 12GB VRAM。[1] [2]

最终落地的结果可以概括为三条。第一，`mathart/level/pdg.py` 现在支持 **`requires_gpu=True` 的节点级资源声明**，并通过 **`gpu_slots` semaphore** 在 runtime 层硬性限制 GPU work-item 的同时在途数量；这意味着 CPU Fan-out 可以继续多线程狂奔，但真正进入 GPU lane 的 work-item 会被透明地串行或限量执行，完全符合“CPU 狂奔 + GPU 克制”的单机防爆纪律。[1] [2]

第二，`mathart/core/taichi_xpbd_backend.py` 已被提升为 **TaichiXPBDBackend v2.1.0**。它不再只是一个外部脚本专用 benchmark backend，而是能够 **直接消费 PDG WorkItem payload**，同时把 `pdg_input_work_item_id`、`pdg_input_partition_key`、`hardware_fingerprint`、`runtime_cleanup_*` 等证据字段写入 BENCHMARK_REPORT。更关键的是，当上下文显式要求 `strict_gpu_required=True` 时，backend 现在**绝不再静默回退到 CPU**；如果 CUDA lane 不可用，就会直接抛错，把算力流向完全暴露给上层编排，而不是偷偷“帮忙跑通”。[2] [3]

第三，Taichi runtime 生命周期已经从“可能残留显存状态”的研究态，提升到 **每个 sample 执行后强制 `sync + reset` 清理** 的生产态。这个动作并不是为了形式上调用清理 API，而是为了贯彻 Taichi 稀疏结构与 GPU runtime 生命周期管理的工业原则：**单个 work-item 用完即焚，显存上下文不跨样本、不跨 work-item 粘连驻留**，从而保护 12GB VRAM 底线，避免大批量 sparse_cloth 或后续实体渲染任务把显卡拖进不透明 OOM。[3]

## Research Alignment Audit

| Reference | Requested Principle | `SESSION-105` Concrete Closure |
|---|---|---|
| Jolt Physics Job System [1] | 作业依赖与 barrier 由 runtime 持有，稀缺执行资源需要明确调度边界 | PDG runtime 保持 fan-out / fan-in 由 facade 管理，并为 `requires_gpu` 节点增加 runtime 级 semaphore，不把 GPU 资源锁下沉到业务节点内部 |
| NVIDIA PhysX Threading / Dispatcher [2] | CPU dispatcher 与 GPU dispatcher 是异构资源，不能让 CPU 任务无限挤占 GPU lane | `gpu_slots` 明确成为图级配置；trace 增加 `requires_gpu` 与 `resource_wait_ms`，使 GPU 等待/占用对调度层透明可审计 |
| Taichi Sparse / Runtime Lifecycle [3] | 稀疏结构与 runtime context 必须显式回收，避免显存僵尸驻留 | Taichi benchmark lane 每个 sample 均执行 `sync + reset` cleanup，并把 cleanup 次数与耗时落入报告字段 |
| NASA-STD-7009B [4] | 性能结论必须伴随验证、确认与证据链字段 | BENCHMARK_REPORT 保留 `cpu_gpu_max_drift`、`cpu_gpu_rmse`、`parity_passed`，并补充 lineage 与 hardware fingerprint 以强化证据闭环 |

本轮对齐结论非常明确：仓库现在不只是“有 GPU benchmark 代码”，而是已经具备了**把 GPU benchmark 作为正式单机生产 lane 编入 PDG v2 的底盘条件**。[1] [2] [3] [4]

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/level/pdg.py` | `PDGNode` 新增 `requires_gpu`；`ProceduralDependencyGraph` 新增 `gpu_slots` | 让 GPU 成为显式声明的 runtime 资源，而不是隐式约定 |
| `mathart/level/pdg.py` | runtime facade 新增 GPU semaphore、`gpu_max_inflight_observed`、`resource_wait_ms` | 对稀缺 GPU lane 做硬闸门控制，并把等待证据写入 trace / scheduler metadata |
| `mathart/level/pdg.py` | `_pdg` 上下文新增 `dependency_work_items`、`requires_gpu`、`gpu_slots` | 让下游节点或 backend 能直接消费 WorkItem lineage，而不是只看到扁平 payload |
| `mathart/core/taichi_xpbd_backend.py` | 升级到 `v2.1.0`，新增 `build_context_from_work_item()` 与 `execute_work_item()` | 让 Taichi backend 正式具备 PDG work-item 直接接入能力 |
| `mathart/core/taichi_xpbd_backend.py` | 增加 `strict_gpu_required`、`requires_gpu`、`hardware_fingerprint`、`runtime_cleanup_*` | 明确禁止生产 CUDA 请求下的静默 CPU 回退，并强化证据字段 |
| `mathart/core/taichi_xpbd_backend.py` | `_run_taichi_lane()` 改为每 sample 后 `sync + reset` | 显式清理 Taichi runtime / VRAM 生命周期，防止 work-item 间状态污染 |
| `mathart/animation/xpbd_taichi.py` | `get_taichi_xpbd_backend_status()` 与 `_ensure_taichi_initialized()` 增加 `strict_gpu` | 为真实 CUDA lane 提供“不回退 CPU”的底层初始化语义 |
| `tools/run_session085_gpu_benchmark.py` | 真 GPU case 自动设置 `strict_gpu_required=True` 与 `requires_gpu=True` | 使本地真机 benchmark 入口自动遵守新的生产级 GPU 纪律 |
| `tests/test_level_pdg.py` | 新增 GPU semaphore 白盒测试 | 证明多线程 Fan-out 下 GPU lane 仍被 `gpu_slots=1` 硬性串行化 |
| `tests/test_taichi_benchmark_backend.py` | 新增 strict GPU 抛错、work-item lineage、cleanup 次数断言 | 证明 backend 不会静默降级，且 cleanup/lineage 字段真实存在 |

## White-Box Physical Proof

| Assertion | Evidence |
|---|---|
| GPU 不是被 CPU Fan-out 暴力淹没 | `test_pdg_v2_requires_gpu_nodes_are_serialized_by_gpu_slots()` 证明 `max_workers=4` 下 GPU node 的 `max_active == 1`，且 scheduler 记录 `gpu_max_inflight_observed == 1` |
| strict GPU 不会静默降级 | `test_taichi_benchmark_backend_strict_gpu_raises_without_gpu()` 明确要求在 GPU 不可用时抛出异常，而不是退回 CPU |
| Taichi lane 真正吃 WorkItem 而非脚本私参 | `test_execute_work_item_consumes_pdg_payload_and_records_lineage()` 证明 backend 可从 WorkItem 构建上下文并落下 lineage 字段 |
| VRAM cleanup 不是口头承诺 | `test_taichi_benchmark_backend_gpu_report_contains_median_sync_and_parity()` 断言 `runtime_cleanup_calls == sample_count`，并验证 reset/sync 次数 |
| 主干零退化 | 全量回归达到 `1729 PASS / 7 SKIP / 0 FAIL`，比 `SESSION-104` 多出本轮新增测试而未引入任何旧测试回归 |

## Validation Closure

| Validation Layer | Command | Result |
|---|---|---|
| PDG + Taichi 专项白盒 | `python3.11 -m pytest tests/test_level_pdg.py tests/test_taichi_benchmark_backend.py -p no:cov` | **20 PASS / 2 SKIP / 0 FAIL** |
| 本地 benchmark 入口脚本 | `python3.11 tools/run_session085_gpu_benchmark.py` | **成功运行，但当前 sandbox 为 `cpu_fallback`；未检测到 CUDA/Taichi 真机环境** |
| 全量回归 | `python3.11 -m pytest tests/ -p no:cov` | **1729 PASS / 7 SKIP / 0 FAIL** |

这里需要明确说明一件事：本轮代码路径已经把 **strict GPU 生产语义、GPU semaphore 调度、VRAM cleanup 证据链** 全部落地完毕，但当前 sandbox 并不具备 maintainer 那台 **RTX 4070 / CUDA / Taichi** 真机条件，因此 `tools/run_session085_gpu_benchmark.py` 在本地只生成了 `cpu_fallback` 形式的结构化报告。也就是说，**代码已经 ready，真机 CUDA 证据还需要在 maintainer 的工作站上按新语义再跑一次**；这已经被同步写入 `PROJECT_BRAIN.json` 的 top gap 与 custom notes 中。

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 严禁让 CPU 任务 DDoS GPU | 已合规。GPU lane 由 `gpu_slots` semaphore 控制，且 trace 可见等待代价。 |
| 严禁静默回退 CPU | 已合规。`strict_gpu_required=True` 时 backend 直接抛异常。 |
| 严禁 VRAM 僵尸驻留 | 已合规。Taichi lane 每 sample 执行 `sync + reset`，并记录 cleanup 字段。 |
| 严禁回写中心中枢 if/else | 已合规。改造集中在 PDG runtime、Taichi backend 与独立 benchmark 入口，未破坏 registry / backend 架构。 |
| 主干零退化 | 已合规。全量回归 `1729 PASS / 7 SKIP / 0 FAIL`。 |

## Preparation Notes for P1-B1-1 After This GPU Closure

在打通这套“**CPU 狂奔 + GPU 克制**”的绝对防爆底座之后，下一步若要在单机上无缝接入 `P1-B1-1` —— 也就是把 cape / hair / cloth 一类二次动画点链真正转成**可视化实体渲染输出** —— 现在已经不需要再改调度哲学，而是要补齐**资产渲染管道的几个微调准备**。

第一，当前物理 lane 产物仍以 benchmark payload / positions 阵列思维为主；而进入可视渲染阶段后，必须把中间结果改造成 **文件背书的 artifact**，例如 `mesh_cache.npz`、`ribbon_keyframes.jsonl`、`preview_strip.png`、`material_recipe.json`。这样做的目的并不是形式化存盘，而是要让大批量 cloth/hair frame 数据从内存 JSON 中抽离出来，保持 PDG cache lineage 可重现、可命中，同时守住 32GB RAM 与 12GB VRAM 底线。[2] [3]

第二，`P1-B1-1` 需要尽快固定 **渲染拓扑契约**。建议明确至少四类字段：其一是 mesh/ribbon 顶点顺序与 segment 索引；其二是 UV 生成策略、缝边规则与切线/法线来源；其三是材质槽位、双面/厚度/法线贴图等材质约束；其四是 keyframe 采样率与 motion blur / interpolation 策略。只有这些字段先被稳定下来，后续实体渲染节点才能在 PDG collect 阶段稳定聚合，不至于“物理解算对了，但渲染拓扑每次都漂”。

第三，`P1-B1-1` 需要沿用本轮已经建立好的 **`requires_gpu` lane discipline**。将来无论是 CUDA cloth bake、OpenGL/Vulkan preview bake，还是 neural render 辅助 pass，都不应重新绕开 PDG 去私开 GPU 通道。建议后续所有显卡相关节点统一声明 `requires_gpu=True`，并在图级以 `gpu_slots=1` 起步，等 RTX 4070 真机 evidence 稳定后再谨慎评估是否允许 `gpu_slots=2` 的细分 lane 并发。

第四，render collect 节点应该被升级为 **实体包归约协议**，而不仅是 benchmark 数字归约。建议 collect 后至少生成四类正式产物：其一是实体渲染输入包（mesh/ribbon + material recipe）；其二是可快速人工复核的 preview 序列；其三是诊断摘要（顶点数、三角数、包围盒、帧数、源物理 lineage）；其四是可交给下游引擎导入的 manifest。这样一来，`P1-B1-1` 就会自然成为当前 PDG + GPU substrate 的延续，而不是重新旁路出一个新的单体脚本。

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | 在 maintainer 的 RTX 4070 工作站重跑 `tools/run_session085_gpu_benchmark.py` | 生成 `SESSION-105` 语义下的真实 CUDA 证据，尤其是 `sparse_cloth` 的 strict-GPU 报告 |
| 2 | 启动 `P1-B1-1` 的文件化 artifact 规范 | 将 cloth / ribbon / hair 输出从 JSON-inline 阵列改造为 mesh/frame artifact 路径 + digest |
| 3 | 定义 render topology contract | 固定顶点顺序、UV、法线/切线、材质槽位、采样率，避免渲染拓扑漂移 |
| 4 | 将后续渲染/烘焙节点统一纳入 `requires_gpu` 调度 | 保证实体渲染阶段继续遵守单机 GPU 防爆底座 |
| 5 | 推进 `P1-ARCH-5` | 把稳定下来的 PDG/GPU artifact 流继续向 OpenUSD-compatible interchange 扩展 |

## References

[1]: https://jrouwe.github.io/JoltPhysicsDocs/5.1.0/class_job_system.html "Jolt Physics JobSystem"
[2]: https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/Threading.html "NVIDIA PhysX Threading"
[3]: https://docs.taichi-lang.org/docs/sparse "Taichi Sparse Data Structures"
[4]: https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf "NASA-STD-7009B"
[5]: ./PROJECT_BRAIN.json "PROJECT_BRAIN.json"
