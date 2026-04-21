| Field | Value |
|---|---|
| Session | `SESSION-108` |
| Focus | `P1-AI-2C` 反闪烁渲染链路的真实 ComfyUI 运行时闭环、16 帧安全切片与 CLI 机器契约收敛 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `5 PASS / 0 FAIL`（`pytest -q tests/test_p1_ai_2c_anti_flicker_live_cli.py`） |
| Additional Audit | 已完成 16 帧切片、`stdout/stderr` 分流、硬编码节点寻址、离线快速失败与可选依赖收集阻断审计 |
| Primary Files | `mathart/core/anti_flicker_runtime.py`，`mathart/comfy_client/comfyui_ws_client.py`，`mathart/core/builtin_backends.py`，`mathart/cli.py`，`tests/test_p1_ai_2c_anti_flicker_live_cli.py`，`PROJECT_BRAIN.json` |

## Executive Summary

本轮工作的目标，是把此前已经具备**离线可测能力**的 `anti_flicker_render`，继续向前推进到**生产运行时闭环**：不仅要能组装 ComfyUI 工作流，还要能在后端内执行真实的序列工作流、在 CLI 中以稳定命令入口暴露出去、在显存边界上做主动防御，并且保证自动化流水线可以继续把 `stdout` 当成**纯 JSON IPC 契约**消费，而不被运行时日志污染。[1] [2] [3]

最终落地的结果是：`AntiFlickerRenderBackend` 现在已经支持**live/offline 双路径执行**。在 live 路径下，后端会先对 ComfyUI 端点执行显式在线探针，若服务未启动则在第 0 秒直接失败，不再出现同步 CLI 空转等待的假死风险；同时，长序列推理会被强制切分为**最多 16 帧一批**的安全 chunk，以匹配 12GB VRAM 级别显卡的保守运行边界；运行进度则通过回调统一回传到 CLI 的 `stderr`，而 `stdout` 仍然只输出 manifest JSON，继续满足下游机器解析场景。[1] [2]

从任务状态看，`P1-AI-2C` 现在可以从此前的 **`SUBSTANTIALLY-CLOSED`** 推进到 **`CLOSED`**。不过需要说明的是，当前沙箱里完成的是**工程闭环、协议闭环与本地模拟验证闭环**；真正的 **RTX 4070 + 真实 ComfyUI 服务 + 真实权重** 的生产证据，仍然应作为下一步的 maintainer-side 运行归档项单独沉淀。这不是接口级未完成，而是**硬件环境证据**尚未在当前沙箱内生成。[5]

## Research Alignment Audit

| Reference | Requested Principle | `SESSION-108` Concrete Closure |
|---|---|---|
| AnimateDiff / sequence context practice [1] | 长视频不能无上限整段推理，应使用 context window 或 batch chunking | `anti_flicker_runtime.plan_frame_chunks()` 固化为显式 chunk plan；后端将 `chunk_size` 和 `context_window` 都限制在 16 以内 |
| Twelve-Factor Logs [2] | 日志应视为事件流，CLI 输出契约必须可被外部系统稳定消费 | CLI 现在把人类可读进度全部发往 `stderr`，`stdout` 只保留 JSON manifest |
| Ports & Adapters / Hexagonal Architecture [3] | CLI 只应是 driving adapter，不应承载业务求解逻辑 | 业务逻辑继续收敛在 backend / runtime helper 内，CLI 只做参数合并、回调注入与结果发射 |
| ComfyUI endpoint resilience [4] | 运行前应先做轻量 online probe，离线时快速失败 | `AntiFlickerRenderBackend` 在 live 路径开始前调用 `ComfyUIClient.is_server_online()`，离线则立即抛错 |
| OpenUSD / adapter discipline [5] | 逻辑 scene contract 与外部格式序列化应解耦 | 本轮进一步把 chunk plan、payload path、report path、frame_sequence 都沉淀到 typed manifest metadata，为后续 USD adapter 做 scene lifting 铺路 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/core/anti_flicker_runtime.py` | **NEW**。实现 chunk planner、地址规范化、RGB/guide 序列导出与 chunk 输出物化 | 把 runtime 细节从后端本体中剥离，形成更干净的 adapter helper 层 |
| `mathart/comfy_client/comfyui_ws_client.py` | 扩展 `progress_callback` | ComfyUI WebSocket / HTTP 轮询进度现在可以安全回传到 CLI，而不会污染 `stdout` |
| `mathart/core/builtin_backends.py` | 重写 `AntiFlickerRenderBackend` 为 live/offline 双路径 | 支持端点探针、16 帧 chunking、chunk payload/report 持久化、typed manifest metadata，以及离线兼容路径 |
| `mathart/cli.py` | 新增一等命令 `anti-flicker-render` / `anti_flicker_render` | CLI 现在可直接驱动 anti-flicker live runtime，并把进度固定路由到 `stderr` |
| `mathart/core/builtin_backends.py` 其他导入区 | 将重型动画/Unity 依赖改为按需局部导入 | 避免测试收集阶段因可选依赖缺失而阻断 anti-flicker 回归 |
| `tests/test_p1_ai_2c_anti_flicker_live_cli.py` | **NEW**。5 个定向测试 | 覆盖 16 帧切片、context clamp、offline fast-fail、chunked materialization 与 `stdout/stderr` 分流 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 16 帧安全切片强制生效 | `test_plan_frame_chunks_enforces_16_frame_windows` 验证 `37 -> 16 + 16 + 5` |
| 运行时配置不会突破 12GB VRAM 红线 | `test_validate_config_clamps_context_window_and_chunk_size` 验证 `chunk_size/context_window` 被限制到 `16` |
| 服务离线时不会空转假死 | `test_live_backend_fast_fails_before_render_when_comfyui_is_offline` 验证 bake 阶段前即抛出 offline 错误 |
| 大批量序列可被分块执行并回收产物 | `test_live_backend_chunks_large_sequences_and_materializes_outputs` 验证 37 帧请求被按三段 chunk 执行并回收 37 个结果帧 |
| CLI 不污染 `stdout` | `test_cli_anti_flicker_render_keeps_stdout_json_and_progress_on_stderr` 验证进度事件只出现在 `stderr`，`stdout` 始终是合法 JSON |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 Anti-OOM：严禁无上限帧数整段并发推理 | ✅ 已合规。`chunk_size` / `context_window` 都经后端校验并限制在 16；长序列自动切块 |
| 🔴 Anti-Stdout-Pollution：严禁用普通 `print()` 破坏 CLI IPC | ✅ 已合规。CLI 进度输出显式走 `stderr`；本轮审计未发现新增业务路径向 `stdout` 打印日志 |
| 🔴 Anti-Async-Deadlock：同步 CLI 与异步客户端边界必须健壮桥接 | ✅ 已合规。CLI 不直接承载异步求解；运行时边界被收敛到客户端/后端内部，先探针后执行，避免同步空转 |
| 🔴 Anti-Hardcoding：严禁在 payload 扩展中写死脆弱节点 ID | ✅ 已延续合规。序列 payload 仍通过 `ComfyUIPresetManager` 的语义选择器注入，本轮未引入 `node["14"]` 类脆弱寻址 |
| 🔴 Pipeline Bleed：CLI 不得倒灌业务逻辑 | ✅ 已合规。chunk 计划、payload 组装、输出物化全部在 backend/runtime helper，CLI 只是 driving adapter |

## What Remains for Production Evidence

虽然本轮已把**运行时与 CLI 工程闭环**打通，但“生产证据”还差最后一段 maintainer-side 真机执行。原因很简单：当前沙箱没有 maintainer 的 RTX 4070，也没有持续在线、已装权重的真实 ComfyUI 服务，因此无法在这里留下最终的 GPU 耗时、显存峰值和输出样片证据。[5]

| Remaining Item | Why It Still Matters |
|---|---|
| RTX 4070 + 真实 ComfyUI endpoint 跑通一次 `anti_flicker_render` | 需要把当前 live 路径从“工程上可运行”推进到“生产上已留证” |
| 真实权重与 checkpoint 兼容性记录 | 需要确认 `SparseCtrl`、`AnimateDiff`、`ControlNet` 组合在 maintainer 环境中的实际版本兼容矩阵 |
| 长序列显存/耗时曲线 | 需要记录 16 帧 chunk 策略在真实 12GB VRAM 设备上的峰值显存与总时长 |
| 输出样片归档 | 需要把输入 guide、chunk payload、chunk report、最终图像/视频一并存档，形成可回放证据链 |

## Preparing for `P1-ARCH-5` OpenUSD-Compatible Scene Interchange

经过 `SESSION-107` 和 `SESSION-108` 两轮之后，项目对接 `P1-ARCH-5` 的前置条件已经更成熟了。上一轮主要解决的是**typed image / sequence artifacts**，这一轮则进一步稳定了**driving adapter 与 machine-readable runtime contract**：现在不仅有单帧/序列 typed manifest，还有了 chunk plan、chunk report、payload path、runtime metadata，以及不被日志污染的 CLI JSON IPC 输出。换句话说，未来 OpenUSD 适配器将面对的，不再是一堆零散文件，而是一组更接近 scene node graph 的 typed objects。[3] [5]

### 建议的微调准备

| Order | Micro-adjustment | Purpose |
|---|---|---|
| 1 | 为 manifest 中的主要产物引入稳定 `prim_path` / `node_path` 约定 | 例如 `/World/Character/Temporal/Chunk_0000/Frames`，避免 USD adapter 只能从文件名猜层级 |
| 2 | 为 `frame_sequence`、`chunk_plan`、`chunk_reports` 增加统一 relationship 语义 | 便于后续映射到 USD relationships，而不是把 JSON 路径当成无语义字符串 |
| 3 | 给 guide sequence、payload、report 赋予稳定 `asset_id` | 使跨目录移动、缓存或远程同步时仍能保持 scene identity |
| 4 | 保持 CLI 的 `stdout` 只输出 machine contract | 这样未来 `mathart cli -> usd exporter -> downstream orchestrator` 可以形成稳定的链式 IPC，不会被进度日志击穿 |
| 5 | 把“运行事件流”和“场景状态快照”继续分层 | `stderr` 保留事件流，manifest/JSON 保留状态快照，未来接入 USD/PDG/Omniverse 时边界会更清楚 |
| 6 | 新增 adapter-only 的 `UsdSceneExporter` / `UsdaExporter` | 在不污染 render backend 的前提下，把既有 typed manifests 投影为 OpenUSD-compatible scene description |

### 为什么这一轮的 CLI 改造对 OpenUSD 很关键

很多团队在走向场景互操作时，会把 CLI 当作“随便打日志的脚本层”，结果导致自动化 orchestrator 很难把 CLI 输出稳定接到 scene ingest pipeline 里。本轮把 `stdout` 与 `stderr` 的职责严格分离，实际上是在为将来的**scene-level IPC**打地基：当 `stdout` 始终是纯净 JSON 时，未来无论是 PDG、Omniverse bridge，还是自定义 scene assembler，都可以把 CLI 当作**可靠的 ports-and-adapters 边界**使用，而不是把它当作一段不可预测的 shell 脚本。[2] [3] [5]

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | 在 maintainer 的 RTX 4070 环境执行一次真实 `anti_flicker_render` live 路径 | 为 `P1-AI-2C` 增补最终生产证据 |
| 2 | 给 sequence / chunk / payload / report 统一增加 `asset_id` 与 `prim_path` | 为 `P1-ARCH-5` 铺平内部 scene identity 语义层 |
| 3 | 为 manifest relationships 增加显式类型枚举 | 让 `guide`、`payload`、`chunk-output`、`final-output` 之间的关系更容易投影到 USD arcs |
| 4 | 新增 adapter-only 的 OpenUSD exporter 原型 | 在不污染核心运行时的前提下，验证 typed manifests 到 scene description 的 lifting 路径 |

## References

[1]: https://huggingface.co/docs/diffusers/api/pipelines/animatediff "Hugging Face Diffusers AnimateDiff Documentation"
[2]: https://12factor.net/logs "The Twelve-Factor App — Logs"
[3]: https://8thlight.com/insights/a-color-coded-guide-to-ports-and-adapters "A Color Coded Guide to Ports and Adapters"
[4]: ./mathart/comfy_client/comfyui_ws_client.py "ComfyUIClient implementation in repository"
[5]: ./PROJECT_BRAIN.json "PROJECT_BRAIN.json"
