# SESSION-108 P1-AI-2C 外部研究笔记

## 1. AnimateDiff / SparseCtrl / 12GB VRAM 安全窗口

根据 Hugging Face Diffusers 的 AnimateDiff 文档，`num_frames` 的默认值为 **16**，这是当前视频生成接口的基准批长度之一，能够直接作为 12GB VRAM 设备上的保守上下文窗口上限参考。[1]

ComfyUI-AnimateDiff-Evolved 项目明确强调了 **sliding context / context options** 与“无限长度动画”能力，并将其作为规避长视频一次性推理开销的核心机制之一。这意味着在本项目中，如果 CLI 或后端收到 `frame_count > 16` 的请求，应优先走 **显式 chunking / context window** 策略，而不是把超长帧序列整体塞进一次提交。[2]

## 2. CLI stdout / stderr 分流纪律

本轮用户要求与 Twelve-Factor / GNU CLI 设计一致：

- `stdout` 只能承载纯净 JSON IPC 契约；
- `stderr` 承载人类可读日志、告警、实时进度；
- 后端内部不得出现普通 `print()` 污染管线；
- 如果做实时 ComfyUI 进度回显，应把 WebSocket `progress` 事件重定向到 `stderr`。

## 3. Hexagonal Architecture 对 CLI 的约束

CLI 应是 Driving Adapter：

- 负责 argparse 参数解析；
- 将配置组装成强类型上下文；
- 委托给已注册 backend 执行；
- 不在 CLI 层内嵌具体网络握手、提交、下载、轮询、分块执行等核心流程。

因此，本轮正确做法应是：**把 ping fast-fail、chunking、ComfyUIClient 调度、分块合并与进度回调统一落在 AntiFlickerRenderBackend 内部（或其紧邻 adapter 层）**，CLI 只做同步/异步边界桥接与 stderr 回显。

## 4. 本轮代码实现的直接设计启示

1. 后端 `validate_config()` 需要新增或强化：`context_window`, `chunk_size`, `enable_chunking`, `comfyui_live_execution`, `fail_fast_on_offline` 一类配置。
2. `frame_count > 16` 时，后端必须把总序列拆为多个 payload chunk；若小于等于 16，则保持单批直推。
3. WebSocket 进度事件应通过 callback 往上传递，由 CLI/调用方决定如何写入 `stderr`。
4. CLI 不直接使用 HTTP/WebSocket 细节；它只接收 backend 返回的 manifest，并把 manifest JSON 写到 `stdout`。

## References

[1]: https://huggingface.co/docs/diffusers/api/pipelines/animatediff "Text-to-Video Generation with AnimateDiff · Hugging Face"
[2]: https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved "Kosinkadink/ComfyUI-AnimateDiff-Evolved"

## 5. Twelve-Factor 与 CLI 输出流隔离

Twelve-Factor 官方日志章节明确指出：应用不应自行管理日志文件，而应把事件流写到标准输出流，由运行环境接管路由与归档。[3] 本项目需要在这一原则之上进一步细分：**机器可解析的 IPC JSON 必须独占 `stdout`，而人类可读的运行时信息必须走 `stderr`**。否则，一旦把 ComfyUI 进度或握手警告混入 `stdout`，现有 CLI 子进程测试与自动化流水线都会被破坏。

## 6. Ports & Adapters 对 CLI 的直接启示

8th Light 的 Ports and Adapters 文章强调，CLI 这类入口天然属于 **incoming/driving adapter**，其职责是依赖 incoming port 驱动应用，而不是把底层网络、数据库或第三方 API 逻辑内嵌在适配器里。[4] 对应到本项目：

- `mathart/cli.py` 只能做参数解析、上下文合成、同步边界桥接与 JSON 发射；
- ComfyUI 的 ping、提交、WebSocket 进度监听、chunk 合并都应位于 backend / client adapter 层；
- 若要在 CLI 展示实时进度，应通过 callback 从 backend 向上传出，再由 CLI 输出到 `stderr`。

## 7. 研究结论对应的实现红线

| Red Line | Engineering Consequence |
|---|---|
| Anti-OOM Trap | `frame_count > 16` 时必须显式 chunk，禁止一次性全量提交 |
| Anti-Stdout-Pollution | 任何进度与日志只能走 `stderr`；`stdout` 只保留 manifest JSON |
| Anti-Async-Deadlock | CLI 若桥接异步行为，必须通过稳定边界封装；不能在同步函数中裸 `await` |
| Anti-Central-Router | 不得把 ComfyUI 调度写入 `AssetPipeline`/`Orchestrator`；必须在 anti-flicker backend 内闭环 |

## References

[3]: https://12factor.net/logs "The Twelve-Factor App — Logs"
[4]: https://8thlight.com/insights/a-color-coded-guide-to-ports-and-adapters "A Color Coded Guide to Ports and Adapters"
