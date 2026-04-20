# SESSION-105 P3-GPU-BENCH-1 Audit

本轮在正式改代码前完成了两类同步：其一是仓库状态同步，当前主干头提交为 `23d0d0bd2b4f446669fd1bc92795715e851bda49`；其二是技术债同步，`SESSION_HANDOFF.md` 与 `PROJECT_BRAIN.json` 明确指出 `P1-ARCH-4` 已完成单机有界 fan-out 扇出，但 `P3-GPU-BENCH-1` 仍停留在“**SUBSTANTIALLY-CLOSED**”状态，最后一块缺口正是 16K `sparse_cloth` 的正式实机证据闭环与 GPU 生产并网。

从研究基线看，Jolt Physics 的 `JobSystem` 与 `Barrier` 说明**并发上限与汇聚屏障必须是 runtime contract 的一部分**，而 PhysX `TaskManager` 则进一步说明 ready task 可以交给不同 dispatcher，但资源稀缺路径必须由单独 dispatcher / 调度约束保护，而不是与普通 CPU 任务混在同一个无限制队列中。[1] [2] Taichi 官方 sparse 文档则证明 CPU/CUDA 对 spatially sparse data structures 的正式支持，但也同时提醒稀疏 SNode 的生命周期不能放任其跨批次驻留；对于本项目这种“数百帧、多 work item、单张 12GB 显卡”的流水线，work item 结束后的显式 runtime reset 仍然是必要的工程闭环。[3] NASA-STD-7009B 的标准语境则要求 CPU/GPU 结果比较不能只给一个速度数字，而必须形成带容差、场景、环境与结果分析的可信度证据结构。[4]

基于代码审计，当前 `mathart/core/taichi_xpbd_backend.py` 已经具备 `TaichiXPBDBackend` 注册表插件、`free_fall_cloud` / `sparse_cloth` 场景、NumPy 参考解算器、`speedup_ratio` / `cpu_gpu_max_drift` / `cpu_gpu_rmse` 输出，以及 `BENCHMARK_REPORT` typed manifest 等基础设施。但它仍然主要是“benchmark backend”，尚未被提拔为 **PDG work-item aware 的常驻物理算子**。当前 `execute()` 只消费通用 context，没有显式解析 PDG `WorkItem` 契约，也没有把硬件/显存清理状态写入报告契约。

同样地，`mathart/animation/xpbd_taichi.py` 当前已经提供 `reset_taichi_runtime()`、`sync_taichi_runtime()` 和 `TaichiXPBDClothSystem.run()` 等能力，但这些能力尚未被 work-item 生命周期强绑定。当前 backend 在 `execute()` 入口只做一次 `reset_taichi_runtime()`，之后 `_run_taichi_lane()` 会在每个 sample 内新建 cloth system 并取回 positions；然而 sample 结束后和整个 execute 结束后并没有强制性的 `finally: reset_taichi_runtime()` 风格清理闭环，因此**无法严格证明多批次 GPU work item 下的零显存泄漏**。

PDG 侧的缺口也非常清楚。`mathart/level/pdg.py` 已经在 `SESSION-104` 中具备 `max_workers` 受限 fan-out 与主线程汇聚，但目前既没有 `requires_gpu` 资源标签，也没有独立的 GPU semaphore / lock。换句话说，现有 runtime 能限制总工作线程数，却**不能区分“16 个 CPU 任务可并行”与“GPU 任务必须严格串行占用同一张卡”**。这与本轮的“CPU 狂奔 + GPU 克制”目标仍然有本质差距。

当前沙箱环境也存在一条必须记录的客观受限条件：本地审计命令显示 `nvidia-smi` 不存在，且 `taichi` 包当前未安装。因此，本沙箱**不具备真实 RTX 4070 + CUDA + Taichi 的直接实机执行条件**。这意味着本轮可以在仓库中完成架构收口、测试与 CI 兼容路径，但若要在此环境内直接生成真正的 16K CUDA 实机证据，客观上不可达。后续如果 benchmark 脚本在主理人的工作站上运行，则新增的报告与生命周期字段将用于承接那条真实证据链。

综合以上证据，本轮最合理的代码策略应当是：第一，在 PDG runtime 增加 GPU 资源闸门，并保持其纯 Python 原生实现；第二，把 `TaichiXPBDBackend` 升级为能够直接解析 PDG work-item payload 的 registry plugin，同时显式记录 `requires_gpu`、runtime cleanup 与硬件指纹；第三，用 `finally` 语义封住每个 GPU work item 的 `ti.reset()`；第四，补足测试，既证明 GPU semaphore 的透明阻塞行为，也保证 CI/无 GPU 环境继续平滑通过。

## References

[1]: https://jrouwe.github.io/JoltPhysicsDocs/5.1.0/class_job_system.html "Jolt Physics JobSystem Class Reference"
[2]: https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/Threading.html "PhysX Threading"
[3]: https://docs.taichi-lang.org/docs/sparse "Taichi Spatially Sparse Data Structures"
[4]: https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf "NASA-STD-7009B"
