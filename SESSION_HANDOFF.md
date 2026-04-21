# SESSION-126 Handoff — P1-NEW-10 Mass Production Factory Closure

**Objective**：关闭 **P1-NEW-10 / Production benchmark asset suite**，将项目现有的高维数学后端、注册表插件与 **PDG v2** 调度内核整合为一条可批量落地的工业级量产总线。

**Status**：**CLOSED**。

本次落地把量产逻辑收束为一个公开入口：`tools/run_mass_production_factory.py`。该入口不再是松散脚本堆叠，而是一个由 **ProceduralDependencyGraph (PDG v2)** 驱动的批处理装配线。它将订单先扇出为多个独立 `WorkItem`，再在 CPU 侧并发执行 `CharacterGenotype` 变体生成、3D 装备挂载、`unified_motion`、`pseudo_3d_shell`、`physical_ribbon` 等数学与几何阶段，最后把 `orthographic_pixel_render` 与 `anti_flicker_render` 严格标记为 **requires_gpu**，将显存准入权完全交给 `gpu_slots` 信号量控制。最终交付阶段则统一调用 `unity_2d_anim` 与 `spine_preview`，并把结果按角色维度整齐归档到 `outputs/mass_production_batch_<timestamp>/` 下。

## What Landed

本次闭环的核心不是单个新算法，而是**生产级编排**。`mathart/cli.py` 现已新增 `mass-produce` 子命令，因此主理人不需要手工编写 Python 驱动脚本，就可以直接从命令行触发批量生产。对应的 `tests/test_mass_production.py` 则提供了 `--skip-ai-render` 的空跑白盒验证，用于确认拓扑不会锁死、GPU 节点的调度标签正确、批次目录结构完整，且 CLI 对外入口可正常退出。

| 落地文件 | 作用 | 关键价值 |
|---|---|---|
| `tools/run_mass_production_factory.py` | 新增量产总线入口 | 用 PDG v2 统一编排 fan-out、CPU 并发、GPU 限流、最终归档 |
| `mathart/cli.py` | 新增 `mass-produce` 子命令 | 将量产能力暴露为工业级 CLI，而不是隐藏内部脚本 |
| `tests/test_mass_production.py` | 新增 dry-run 回归测试 | 验证拓扑活性、GPU 节点标记、产物归档与 CLI 可用性 |
| `PROJECT_BRAIN.json` | 更新任务状态 | 将 **P1-NEW-10** 标记为 **CLOSED**，记录 SESSION-126 闭环元数据 |
| `SESSION_HANDOFF.md` | 当前文档 | 作为本地量产执行与交接的单一事实来源 |

## Locked Architecture Decisions

第一条锁定决策是：**大规模量产必须经由 PDG v2 fan-out 进入系统**。订单不再直接在外层 `for` 循环中串行驱动后端，而是统一转为带确定性 RNG 合约的 `WorkItem`。这意味着每个角色批次都有稳定的随机源拆分规则，后续复现实验、错误回放与缓存策略都可以围绕同一套调度语义展开。

第二条锁定决策是：**GPU 节点必须显式声明，而不能靠“约定俗成”限流**。本次已经把 `orthographic_pixel_render` 与 `anti_flicker_render` 作为 GPU 受控阶段固化进图结构，任何未来扩展只要继续遵守 `requires_gpu=True` 与 `gpu_slots` 信号量，即可在 12GB 显存等级机器上保持批量运行安全边界。

第三条锁定决策是：**最终交付必须回到强类型 ArtifactManifest 语义**。量产工厂不会绕开现有微内核插件，而是继续调用 `unity_2d_anim` 与 `spine_preview` 正式后端，并以各自 manifest 暴露的路径进行归档。因此，量产目录结构虽然是面向生产的批处理视角，但其资产来源仍然保持可追踪、可审计、可复跑。

## Local Production Commands for RTX 4070

如果主理人的本地环境为 **i5-12600KF（16 线程）+ 32GB RAM + RTX 4070 12GB**，建议优先采用如下执行方式。

| 场景 | 推荐命令 | 说明 |
|---|---|---|
| 纯 CPU / 拓扑空跑 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --skip-ai-render --seed 20260421` | 不触发 ComfyUI，仅验证 fan-out、CPU 计算、正交渲染前的批量装配与最终归档结构。 |
| 标准本地量产 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 启用 AI 渲染，适合 RTX 4070 12GB 的默认安全配置。 |
| 更保守的显存防线 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 12 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 当 ComfyUI 工作流较重或本地模型更大时，可先缩小 batch 数量，但仍保持 GPU 并发为 1。 |
| 直接使用脚本入口 | `python3.11 tools/run_mass_production_factory.py --output-root outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260421 --comfyui-url http://127.0.0.1:8188` | 与 CLI 子命令等价，适合直接调试脚本入口。 |

在这台机器上，**推荐保持 `--gpu-slots 1` 不变**。CPU 侧 16 线程已经足够把数学装配阶段打满，而 GPU 侧真正危险的是正交辅助图烘焙与 ComfyUI 时序渲染的叠加显存占用。当前总线设计已经把 GPU admission control 独立出来，因此不要试图通过提升 `gpu_slots` 去换取吞吐，除非先在本地完成显存监控与工作流实际峰值测量。

## ComfyUI Preparation Checklist

开启实时 AI 渲染之前，主理人需要先准备好本地 ComfyUI。当前总线假定 `anti_flicker_render` 通过 WebSocket / HTTP 连接到一个可用的 ComfyUI 实例，并在需要时提交序列工作流。

| 准备项 | 要求 | 备注 |
|---|---|---|
| ComfyUI 服务地址 | 默认 `http://127.0.0.1:8188` | 如端口不同，使用 `--comfyui-url` 覆盖。 |
| 服务状态 | 必须能被本地访问 | 建议先在浏览器打开 ComfyUI，确认页面可正常响应。 |
| 关键模型 | SD 主模型、AnimateDiff、Normal ControlNet、Depth ControlNet、SparseCtrl（若工作流使用） | 与 `anti_flicker_render` 既有后端默认字段保持一致。 |
| WebSocket / API | 必须启用 | 量产总线会把 AI 阶段视为生产工作流，而不是离线占位符。 |
| 显存策略 | 保持单 GPU 槽位 | RTX 4070 12GB 下，默认使用 `gpu_slots=1` 防止并发 OOM。 |

建议的本地准备顺序如下。首先，先以 `--skip-ai-render` 完成一次纯 CPU 空跑，确认批次目录、角色归档、`unity_2d_anim` 与 `spine_preview` 正常生成。其次，单独确认 ComfyUI 本地工作流能够接受 normal / depth / RGB 条件输入，并完成一次人工小样试跑。最后，再去掉 `--skip-ai-render` 触发正式量产，这样一旦 AI 链路失败，排障范围就被局限在 ComfyUI 与 `anti_flicker_render` 之间，而不会误判为 PDG 总线或 CPU 数学阶段故障。

## Output Contract

量产完成后，资产会被归档到 `outputs/mass_production_batch_<timestamp>/`。目录下的每个角色子目录都是独立订单单元，包含准备报告、阶段 manifest、最终交付与归档索引。

| 目录层级 | 内容 |
|---|---|
| `character_<id>/prep/` | genotype、装备挂载、预处理报告 |
| `character_<id>/unified_motion/` | UMR 运动片段与 manifest |
| `character_<id>/pseudo3d_shell/` | DQS 壳层网格与 manifest |
| `character_<id>/physical_ribbon/` | 二级动画 ribbon 网格与 manifest |
| `character_<id>/orthographic_pixel_render/` | 正交辅助图输出与 manifest |
| `character_<id>/unity_2d_anim/` | Unity `.anim` 及 manifest |
| `character_<id>/spine_preview/` | `preview.mp4` / `preview.gif` 等预览资产 |
| `character_<id>/archive/` | 面向交付的集中归档副本 |
| `batch_summary.json` | 全批次汇总索引 |
| `pdg_runtime_trace.json` | 调度执行轨迹与 GPU 限流痕迹 |

## White-Box Validation Closure

本次触碰面验证聚焦在**量产总线是否可运行、是否可控、是否可归档**。验证结果如下。

| 验证命令 / 范围 | 结果 |
|---|---|
| `python3.11 -m pytest -q tests/test_mass_production.py` | **2/2 PASS** |
| `test_mass_production_factory_dry_run_skip_ai_render` | **PASS** — 验证 PDG fan-out、GPU 节点标记、归档索引、summary / trace 文件、批次目录生成 |
| `test_cli_mass_produce_dry_run_skip_ai_render` | **PASS** — 验证 `mathart.cli mass-produce` 入口在 dry-run 模式可正常退出 |

这一轮验证已经确认三件关键事实。第一，**拓扑不会在 collect 收口前锁死**。第二，**GPU 受控阶段在 trace 中保持了 `requires_gpu=True` 语义，并受到 `gpu_slots` 的节流约束**。第三，**最终交付目录的主产物能够按角色维度落到批次根目录之下，而不是散落在各后端默认输出路径中**。

## Immediate Operator Guidance

如果主理人下一步要在本地正式开跑，建议顺序为：先执行一次 `--skip-ai-render` 空跑，检查 `batch_summary.json` 是否完整；再启动 ComfyUI，确认模型与工作流可用；最后运行不带 `--skip-ai-render` 的正式命令。如果正式运行中出现问题，优先查看 `pdg_runtime_trace.json` 与各角色目录下的 manifest，再判断故障是出在 CPU 数学阶段、GPU 正交烘焙阶段，还是 ComfyUI 实时渲染阶段。

从项目路线图看，P1-NEW-10 已经闭环，后续更高价值的增量将不再是“有没有批量入口”，而是“如何进一步提升批次 SKU 丰富度与 AI 渲染质量稳定性”。因此，后续演进最值得优先推进的方向有两个：其一，是扩展 genotype 预设与装备 SKU 覆盖，让量产工厂能直接对应更多商业角色组合；其二，是继续收紧 `anti_flicker_render` 的工作流模板和模型版本，使 ComfyUI 链路从“可调用”迈向“稳定可复现”。
