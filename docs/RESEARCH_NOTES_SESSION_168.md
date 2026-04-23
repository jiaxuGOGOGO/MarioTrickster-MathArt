# SESSION-168 外部研究笔记：WebSocket Poison Pill & Circuit Breaker Pattern

> **Task ID**: P0-SESSION-168-COMFYUI-CLIENT-DEADLOCK-BREAKER
> **Date**: 2026-04-23
> **Research Focus**: WebSocket Fail-Fast, Circuit Breaker, Graceful Degradation

---

## 1. WebSocket Poison Pill & Deadlock Prevention

### 1.1 ComfyUI WebSocket 消息协议（官方文档）

根据 ComfyUI 官方文档 (https://docs.comfy.org/development/comfyui-server/comms_messages)，`PromptExecutor` 通过 `send_sync` 方法向客户端发送以下内建消息类型：

| 消息类型 | 含义 |
|----------|------|
| `status` | 系统状态更新 |
| `execution_start` | 工作流执行开始 |
| `execution_cached` | 使用缓存结果 |
| `executing` | 节点执行中（实时状态） |
| `progress` | 长时间操作进度更新 |
| `executed` | 节点完成执行（带 UI 更新时） |
| `execution_error` | **执行错误（致命信号）** |

### 1.2 Poison Pill 模式

**Poison Pill** 是分布式系统中的经典模式：当生产者向消费者发送一个特殊的"毒药"消息时，消费者必须立即终止其监听循环。在 ComfyUI WebSocket 场景中，`execution_error` 就是这个 Poison Pill：

- 它代表远端工作流执行发生了**不可恢复的硬性错误**（如 PyTorch 精度冲突 `Half and Float`）
- 客户端如果仅 `logger.error()` 而不 `raise Exception`，将导致 `while True: ws.recv()` 永远阻塞
- 这是经典的 **Silent Failure → Deadlock** 反模式

### 1.3 Fail-Fast 原则

> **"Fail fast, fail loud"** — 当检测到不可恢复的错误时，系统必须立即抛出异常并终止当前操作，而非静默吞没错误继续等待。

工业界最佳实践：
- WebSocket 客户端必须对 `execution_error` 类型消息实施 **Fast-Fail** 机制
- 收到致命错误后必须 `raise RuntimeError` 撕裂当前的网络等待循环
- 绝对禁止对确定性执行崩溃进行重试等待

---

## 2. Circuit Breaker Pattern（断路器模式）

### 2.1 模式定义

断路器模式（Martin Fowler, Michael Nygard "Release It!"）是分布式系统中防止级联故障的核心模式：

| 状态 | 行为 |
|------|------|
| **Closed** | 正常运行，请求直接通过 |
| **Open** | 检测到故障，立即拒绝所有请求（Fast-Fail） |
| **Half-Open** | 尝试性地放行少量请求，测试恢复情况 |

### 2.2 在批量渲染场景中的应用

当底层 `ComfyUIClient` 抛出致命的远端崩溃异常时：

1. **外层调度器（PDG Orchestrator）** 必须捕获该异常
2. **触发熔断（Cancel all pending tasks）** — 立即取消所有排队中的渲染任务
3. **优雅降级（Graceful Degradation）** — 保留已完成的 CPU 烘焙结果，返回 UI 菜单
4. **Loud Crash Notification** — 在终端用醒目颜色打印崩溃原因和恢复建议

### 2.3 Python 实现要点

```python
# 自定义异常类型
class ComfyUIExecutionError(RuntimeError):
    """ComfyUI 远端执行崩溃异常 — Poison Pill 信号"""
    pass

# 在 WS 监听循环中
if message["type"] == "execution_error":
    raise ComfyUIExecutionError(f"ComfyUI 端点渲染崩溃: {error_details}")

# 在调度器中
try:
    backend.render(task)
except ComfyUIExecutionError as e:
    logger.critical(f"Circuit Breaker OPEN: {e}")
    cancel_all_pending_tasks()
    return graceful_degradation_result()
```

---

## 3. 参考来源

1. ComfyUI Official Documentation — Messages: https://docs.comfy.org/development/comfyui-server/comms_messages
2. ComfyUI Official Documentation — Routes: https://docs.comfy.org/development/comfyui-server/comms_routes
3. Martin Fowler — Circuit Breaker Pattern: https://martinfowler.com/bliki/CircuitBreaker.html
4. Michael Nygard — "Release It!" (Pragmatic Bookshelf) — Chapter on Stability Patterns
5. fabfuel/circuitbreaker — Python Circuit Breaker Implementation: https://github.com/fabfuel/circuitbreaker
6. WebSocket Error Handling Best Practices: https://piehost.com/websocket/ws-errors-explained
