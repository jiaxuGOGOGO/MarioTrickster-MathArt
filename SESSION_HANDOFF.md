# SESSION-127 HANDOFF — P1-NEW-10 Audit Hardening

**Objective**：在既有 **P1-NEW-10 / Production benchmark asset suite** 闭环基础上，针对原始工业级量产总装协议执行一次逐项审计，修正仍与需求存在偏差的实现，并将修正后的状态重新固化到项目主干。

**Status**：**CLOSED（经 SESSION-127 审计加固）**。

本轮工作不是新增另一条平行流水线，而是对 **SESSION-126** 已落地的量产总线做一轮白盒审计与硬化。审计结论表明，原先总线的总体拓扑、PDG fan-out、GPU 节点标记与 CLI 入口都已存在，但正交渲染阶段仍有一个关键偏差：量产工厂传入的是原始 `mesh_data` 字典，而 `orthographic_pixel_render` 实际消费的是 `Mesh3D` 对象，因此运行时会触发后端默认球体回退。SESSION-127 已修正这一点，使正交渲染真正消费“角色身体 + 多槽位 3D 装备 + CPU 阶段组合网格”的装配结果；同时补齐了正交辅助图与跳过 AI 渲染时的归档副本，并把每个 PDG 阶段的 `SeedSequence` 裂变摘要写入批次索引，令确定性调度从“理论成立”提升为“产物可审计”。

## What Changed in the Audit Pass

本轮不是重写需求，而是把原始需求中最容易被“看似通过、实则偏离”的部分逐条压实。因此，当前量产总线的价值不只是“能跑通”，而是已经在关键白盒断言上证明自己**按要求跑通**。

| 文件 | 审计修正 | 结果 |
|---|---|---|
| `tools/run_mass_production_factory.py` | 向 `orthographic_pixel_render` 显式传入 `Mesh3D`，不再仅传原始 `mesh_data` | 正交渲染使用真实装配网格，不再回退为默认球体 |
| `tools/run_mass_production_factory.py` | 将正交辅助图与跳过 AI 渲染的报告副本纳入 `archive/` | 角色级交付目录可一次性检查 `.anim`、`preview.mp4`、辅助图与 AI 阶段证据 |
| `tools/run_mass_production_factory.py` | 在批次索引中增加各 PDG 阶段 `rng_spawn_digest` | `HIGH-2.7-FOLLOWUP` 的确定性 RNG 纪律可直接在产物层追踪 |
| `tests/test_mass_production.py` | 新增真实网格渲染、归档完整性、每阶段 RNG 裂变摘要断言 | dry-run 不再只验证“没锁死”，而是验证“按需求执行” |
| `PROJECT_BRAIN.json` / `SESSION_HANDOFF.md` | 刷新为 SESSION-127 审计结论 | 项目状态与交接文档与当前真实代码一致 |

## Locked Contract After Audit

第一条锁定约束是：**量产总线中的正交渲染必须消费组合后的真实角色网格，而不是任何演示回退网格**。当前实现已经在工厂侧把组合后的顶点、法线、三角形与颜色显式构造成 `Mesh3D` 对象，再交给 `orthographic_pixel_render`。这意味着正交法线图、深度图与材质辅助图终于与实际装备组合一致，后续 ControlNet / ComfyUI 链路才能基于真实几何条件工作。

第二条锁定约束是：**归档不是“顺便复制一下”，而是量产交付合同的一部分**。因此现在每个 `character_<id>/archive/` 目录不仅包含 `unity_2d_anim` 与 `spine_preview` 的最终交付物，也包含 `orthographic_pixel_render` 导出的辅助图，以及 `--skip-ai-render` 场景下的 AI 阶段跳过报告。主理人后续做人工抽检时，不必在后端私有目录里来回翻找。

第三条锁定约束是：**PDG 的确定性 RNG 必须在结果层可观察**。PDG v2 本身已经会为每个节点裂变 `SeedSequence` 并注入独立 `rng`，但现在量产工厂进一步把每个角色、每个阶段对应的 `rng_spawn_digest` 写入 `character_<id>_factory_index.json` 与 `batch_summary.json`。因此，并发确定性不再只是运行时隐式行为，而成为可以纳入 CI 与人工审计的显式证据。

## Local Production Commands for RTX 4070

如果主理人的本地环境仍为 **i5-12600KF（16 线程）+ 32GB RAM + RTX 4070 12GB**，建议继续使用以下命令格式。它们已经与当前代码和 SESSION-127 审计后的目录结构对齐。

| 场景 | 推荐命令 | 说明 |
|---|---|---|
| 纯 CPU / dry-run 审计 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --skip-ai-render --seed 20260421` | 验证 fan-out、CPU 数学阶段、真实网格正交渲染、归档结构与 CLI 出口，不依赖 ComfyUI。 |
| 标准本地量产 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 适配 RTX 4070 12GB 的默认安全方案，AI 渲染启用但 GPU 并发仍保持 1。 |
| 保守显存模式 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 12 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 当本地模型较重、ControlNet 更复杂或 ComfyUI 工作流峰值偏大时优先使用。 |
| 直接脚本入口 | `python3.11 tools/run_mass_production_factory.py --output-root outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 与 CLI 子命令等价，适合调试总线主脚本。 |

在这台机器上，**仍然建议把 `--gpu-slots` 固定在 1**。CPU 侧 16 线程足够支撑 genotype、motion、shell、ribbon 等阶段并发推进；而 GPU 侧真正的风险从来都不是算力闲置，而是正交辅助图烘焙与 ComfyUI 时序工作流叠加后的显存峰值。因此，除非先做本地峰值显存测量，否则不要为了追求吞吐去盲目提升 GPU 槽位。

## ComfyUI Preparation Checklist

量产总线在启用 `anti_flicker_render` 时，默认把该阶段视为生产级 AI 渲染链路，而不是装饰性可选插件。因此，正式运行前请先完成本地 ComfyUI 环境准备。

| 准备项 | 要求 | 备注 |
|---|---|---|
| ComfyUI 地址 | 默认 `http://127.0.0.1:8188` | 如端口不同，用 `--comfyui-url` 覆盖。 |
| 服务可用性 | 浏览器与本地 API 都可访问 | 建议先手工打开页面确认服务存活。 |
| 模型准备 | SD 主模型、AnimateDiff、Normal / Depth ControlNet，以及工作流依赖的序列节点 | 应与 `anti_flicker_render` 既有工作流要求保持一致。 |
| API / WebSocket | 必须可用 | 量产总线会按实时生产工作流发起调用。 |
| 显存策略 | 保持 `gpu_slots=1` | RTX 4070 12GB 的默认安全边界。 |

推荐的本地准备顺序仍然是：先执行一次 `--skip-ai-render` 的 dry-run，确认真实角色网格正交渲染、批次目录与角色归档都正常；然后手工验证 ComfyUI 是否能接收 normal / depth / RGB 条件输入并完成一次小样；最后再去掉 `--skip-ai-render` 开始正式量产。这样如果 AI 阶段失败，排障范围就能被限制在 ComfyUI 与 `anti_flicker_render`，而不会误伤 PDG 总线或 CPU 数学链路。

## Output Contract After Audit

量产结果继续统一落在 `outputs/mass_production_batch_<timestamp>/`，但在 SESSION-127 审计后，这个目录结构的解释更严格了：现在不仅阶段目录存在，而且交付归档的内容与用途也被明确固定。

| 目录层级 | 内容 | 审计后要求 |
|---|---|---|
| `character_<id>/prep/` | genotype、装备挂载、预处理报告 | 必须能反查订单与角色装备组合 |
| `character_<id>/unified_motion/` | UMR 运动片段与 manifest | 必须保留并发确定性所需的状态来源 |
| `character_<id>/pseudo3d_shell/` | DQS 壳层网格与 manifest | 必须作为组合网格上游证据存在 |
| `character_<id>/physical_ribbon/` | 披风 / 带状物理网格与 manifest | 必须保留二级动画证据 |
| `character_<id>/orthographic_pixel_render/` | 正交辅助图输出与 manifest | 必须由真实 `Mesh3D` 渲染得出，而非后端回退网格 |
| `character_<id>/unity_2d_anim/` | Unity `.anim` 及 manifest | 最终交付之一 |
| `character_<id>/spine_preview/` | `preview.mp4` / `preview.gif` / diagnostics | 最终交付之一 |
| `character_<id>/archive/` | 面向交付的集中归档副本 | 必须集中包含 Unity、Spine preview、正交辅助图，以及 AI 阶段证据 |
| `character_<id>/character_<id>_factory_index.json` | 角色级索引 | 必须写入各阶段 `rng_spawn_digest` |
| `batch_summary.json` | 批次汇总索引 | 必须可反查每个角色的 manifest、归档与 RNG 摘要 |
| `pdg_runtime_trace.json` | 调度执行轨迹 | 必须保留 `requires_gpu` 与 `gpu_slots` 相关证据 |

## White-Box Validation Closure

本轮审计后的验证目标不再只是“量产总线能启动”，而是确认其**按原始需求的关键约束落地**。当前白盒验证结果如下。

| 验证命令 / 范围 | 结果 |
|---|---|
| `python3.11 -m pytest -q tests/test_mass_production.py` | **2/2 PASS** |
| `test_mass_production_factory_dry_run_skip_ai_render` | **PASS** — 验证 PDG fan-out、GPU 节点标记、真实组合网格被正交渲染消费、角色级归档完整，以及每阶段 `SeedSequence` 裂变摘要存在且互异 |
| `test_cli_mass_produce_dry_run_skip_ai_render` | **PASS** — 验证 `mathart.cli mass-produce` 入口在 dry-run 模式可正常退出 |

因此，这一轮验证已经确认四件事。第一，**拓扑没有在 collect 收口前锁死**。第二，**GPU 受控阶段仍在 trace 中保持 `requires_gpu=True`，并由 `gpu_slots` 节流**。第三，**正交渲染确实使用了装配后的角色网格，而不是默认球体回退**。第四，**最终交付目录中的角色归档已经足以让主理人直接做人工抽检，不需要深入后端私有输出路径**。

## Immediate Operator Guidance

如果主理人现在要在本地正式开跑，请先执行一次带 `--skip-ai-render` 的 dry-run，并重点检查三类文件。第一类，是 `batch_summary.json` 与各角色的 `character_<id>_factory_index.json`，确认每个阶段都有 `rng_spawn_digest` 且角色间不重复。第二类，是 `orthographic_pixel_render` 下的 render report，确认其 `mesh_stats` 与 `composed_mesh` 报告匹配。第三类，是 `archive/` 目录，确认 `.anim`、`preview.mp4`、正交辅助图与 AI 阶段证据都集中存在。

从路线图角度看，P1-NEW-10 目前已经不只是“名义闭环”，而是完成了一次符合原始协议的审计加固。后续若还要继续提升价值，优先级最高的方向不再是补批量入口，而是继续扩展 SKU 丰富度与 AI 渲染可复现性。前者决定商业化资产池的覆盖面，后者决定 ComfyUI 链路能否从“本地可跑”进一步收敛为“跨机器稳定可复跑”。
