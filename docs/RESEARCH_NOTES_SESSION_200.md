# SESSION-200 外网参考研究笔记：史诗级带卡点火全链路通车

## 1. SpaceX F9 Launch Countdown & Telemetry Protocol（航天级遥测与点火协议）

SpaceX Falcon 9 的发射倒计时流程遵循严格的阶段性检查协议。在 T-minus 阶段，所有子系统必须完成 **Pre-flight Dump**（飞行前数据转储），将当前载荷配置、遥测参数和系统状态以结构化格式写入黑匣子记录器。这一模式的核心思想是：在向不可逆的重计算节点（如 GPU 渲染引擎）发送致命指令之前，必须将完整的载荷快照物理落盘，作为事后溯源和故障排查的绝对基准。

在本项目中的映射：
- **Pre-flight Dump → Golden Payload Snapshot**：在 `_execute_live_pipeline` 向 ComfyUI 后端推流之前，拦截最终装配完成的 `payload`，使用 `json.dump(indent=4)` 美化输出到 `outputs/session200_epic_ignition_payload.json`。
- **Telemetry Channel → WebSocket 双向遥测**：Falcon 9 在飞行期间通过 S-band/Ka-band 遥测链路实时回传引擎状态、姿态数据和推力曲线。映射到本项目即为 WebSocket 连接，实时监听 ComfyUI 的 `execution_start`、`executing`、`progress`、`executed` 事件。

参考来源：
- SpaceX Falcon Payload User's Guide (spacex.com)
- Falcon 9 Countdown Timeline (spaceflightnow.com)
- NASA GSFC-STD-7000B Environmental Verification Standard

## 2. Actor Model 与分布式防挂死（Circuit Breaker Pattern）

**Circuit Breaker（熔断器）模式** 源自 Michael T. Nygard 的《Release It!》一书，灵感来自电气工程中的断路器。其核心原则：

> 当远程服务持续失败时，熔断器"跳闸"，后续请求立即 **Fail-Fast** 返回错误，而非继续等待超时。这保护了调用方的线程、连接和内存资源。

熔断器的三种状态：
| 状态 | 行为 | 触发条件 |
|------|------|----------|
| **Closed**（闭合） | 正常转发请求 | 默认状态 |
| **Open**（断开） | 立即拒绝请求，Fail-Fast | 连续失败次数超过阈值 |
| **Half-Open**（半开） | 允许少量探测请求 | Open 状态超时后自动进入 |

在本项目中的映射：
- WebSocket 监听循环必须设置 `timeout=900` 秒的硬性截止时间，绝不允许 `while True` 无限等待。
- 当 ComfyUI 后端发送 `execution_error`（如模型缺失、显卡 OOM）时，必须立即 Fail-Fast 退出监听循环并抛出 `ComfyUIExecutionError`。
- 使用 Python `pybreaker` 库的设计理念（但不引入外部依赖），在 `comfy_client.py` 中内建超时熔断逻辑。

参考来源：
- Michael T. Nygard, "Release It!" (Circuit Breaker Pattern)
- Microsoft Azure Architecture Center: Circuit Breaker Pattern
- danielfm/pybreaker: Python implementation (GitHub)
- codecentric AG: Resilience Design Patterns (Retry, Fallback, Timeout, Circuit Breaker)

## 3. 流式文件拉取（Streaming Artifact Fetch）

GPU 渲染完毕后产生的视觉资产（图片序列、视频文件）可能达到几十兆甚至上百兆。Python `requests` 库的最佳实践明确指出：

> 对于大文件下载，**必须** 使用 `stream=True` 参数，并通过 `response.iter_content(chunk_size=8192)` 以数据块形式迭代写入本地磁盘。绝对禁止使用 `response.content` 一次性将整个响应体载入内存，这会导致 OOM。

关键实现要点：
- `requests.get(url, stream=True)` 延迟下载响应体，仅在迭代时才真正拉取数据。
- `iter_content(chunk_size=8192)` 以 8KB 块大小逐块写入，内存占用恒定。
- 下载完成后应验证文件完整性（文件大小 > 0）。
- 使用 `with` 语句确保连接在下载完成或异常时正确关闭。

在本项目中的映射：
- `_download_file_streaming()` 方法替代原有的 `resp.read()` 全量读取。
- 所有从 ComfyUI `/view` 端点拉取的媒体资产必须使用流式分块写入。
- 下载目标目录为 `outputs/final_renders/`。

参考来源：
- Python Requests Advanced Usage Documentation (requests.readthedocs.io)
- Stack Overflow: Download large files in Python with Requests
- Python for All: Streaming Responses Best Practices

## 4. 综合架构映射

| 航天/工业概念 | 本项目映射 | 实现位置 |
|--------------|-----------|---------|
| Pre-flight Dump | Golden Payload Snapshot | `builtin_backends._execute_live_pipeline` |
| S-band Telemetry | WebSocket 双向遥测 | `comfy_client.py` → `_ws_listen_with_telemetry` |
| Circuit Breaker | 超时熔断 + Fail-Fast | `comfy_client.py` → timeout=900s + execution_error raise |
| Streaming Downlink | 流式资产分块拉取 | `comfy_client.py` → `_download_file_streaming` |
| Launch Pad | 独立点火脚本 | `tools/session200_epic_ignition.py` |
| Flight Recorder | Mock 遥测测试 | `tests/test_session200_ws_telemetry.py` |
