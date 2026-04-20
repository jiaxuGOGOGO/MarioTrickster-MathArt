# SESSION-105 GPU Scheduling Research Findings

## Source 1: Jolt Physics JobSystem

- URL: https://jrouwe.github.io/JoltPhysicsDocs/5.1.0/class_job_system.html
- 关键语义：`JobSystem` 以 `Job` 为基本执行单元，支持依赖计数归零后开始执行。
- `Barrier` 明确负责“等待一组 jobs 完成”，而且 jobs 在等待 barrier 的同时还可以继续创建新 jobs 并加入 barrier，这说明**依赖图在运行时可继续增长**，不能假设必须预先静态展开。
- `GetMaxConcurrency()` 是显式调度上限接口，说明**并发上限应该是 runtime contract 的一部分，而不是隐式行为**。
- `WaitForJobs()` 期间，依赖归零顺序和 barrier 加入顺序共同决定启动顺序，提示我们在 Python PDG 中也应把**结果汇聚顺序固定在 runtime 层**。

## Source 2: NVIDIA PhysX Threading

- URL: https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/Threading.html
- 关键语义：`TaskManager` 管理 inter-task dependencies，并把 ready tasks 分发到对应 dispatcher。
- `CpuDispatcher` 只是线程池接口，PhysX 推荐应用自己实现，以便与应用的其它任务共享 CPU 资源；这与本项目“不能在核心中枢写死逻辑，而应通过 registry/lane 挂载”的方向一致。
- `submitTask()` 可能从 API 调用线程或其他运行中的任务线程调用，因此 dispatcher 必须线程安全。
- 文档明确指出：线程数不应超过硬件线程数；过量 worker 通常会降低性能。
- 指南强调避免长任务阻塞 PhysX 关键路径，并指出 LIFO / work-stealing 对降低延迟有帮助。对本项目的启发是：**CPU 任务可并行狂奔，但 GPU 任务属于稀缺资源，必须通过额外资源锁单独串行化，不能与普通 CPU work item 同等待遇。**

## Preliminary Design Implication for SESSION-105

1. 继续保持 PDG 的 WorkItem 为中心执行单元。
2. 在 runtime 层为 `requires_gpu=True` 的 work item 增加单独的资源闸门，而不是在业务节点中自行抢锁。
3. 保持 CPU fan-out 并行，但 GPU work item 必须通过全局 semaphore / lock 串行进入 CUDA lane。
4. 保持 barrier / collect 语义在 runtime 主线程收口，避免 worker 线程互相等待 future 造成死锁。

## Source 3: Taichi Spatially Sparse Data Structures

- URL: https://docs.taichi-lang.org/docs/sparse
- 关键语义：Taichi 在 CPU/CUDA 后端完整支持 spatially sparse data structures，说明当前项目的 sparse cloth 场景与 CUDA backend 路线是正交且官方支持的。
- 稀疏 SNode 通过 active / inactive 生命周期管理内存；写入 inactive cell 会自动激活，deactivate 后 runtime 会回收并清零容器内存。
- 这对本项目的启发是：**work item 内部可以依赖 Taichi 的局部稀疏回收，但跨 work item 级别仍应显式执行 runtime reset / context tear-down，避免长期驻留的 sparse SNode 僵尸占住 12GB VRAM。**

## Source 4: NASA-STD-7009B

- URL: https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf
- 当前通过官方 PDF 入口确认文档版本为 `NASA-STD-7009B`（Approved 2024-03-05）。
- 从标准搜索结果与既有项目上下文可确认其核心范式仍是：把 verification、validation、results analysis、使用场景相关可信度与证据链显式绑定，而不是只给一个“看起来差不多”的性能数字。
- 对本项目的直接含义是：CPU 与 GPU 在混沌物理系统中的比较必须形成**结构化证据闭环**，至少要固化场景、硬件、预算、误差口径、速度口径与产物路径，不能只在控制台打印一个 speedup。

## Consolidated Implementation Implication for SESSION-105

1. PDG runtime 需要引入 `requires_gpu` 资源标签与单机 `Semaphore(1)` 风格的 GPU gate。
2. Taichi CUDA work item 必须在一次进入 GPU lane 后尽量完成完整物理段，只在段尾做结果取回，避免每帧 CPU↔GPU 往返同步。
3. 每个 GPU work item 结束后必须执行显式 VRAM 生命周期清理，防止 sparse SNode / runtime context 在多批次流水线下持续驻留。
4. Benchmark 报告需要固化 `speedup_ratio`、`cpu_gpu_max_drift`、`cpu_gpu_rmse`、硬件指纹与 artifact path，才能满足 NASA 风格的可信度证据要求。
