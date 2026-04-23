# SESSION-169 外网工业理论锚点研究笔记

> **Task ID**: P0-SESSION-169-EXCEPTION-PIERCING-AND-GLOBAL-ABORT
> **Date**: 2026-04-23

---

## 1. Targeted Exception Handling (精准异常拦截与透传)

### 1.1 核心原则：区分瞬态异常与致命异常

在构建网络容错层时，**绝对禁止**使用泛型 `except Exception:` 拦截所有异常。必须严格区分：

- **瞬态网络波动 (Transient Network Errors)**：如 `ConnectionRefusedError`、`TimeoutError`、`OSError` — 这些是可重试的网络层故障。
- **致命领域异常 (Fatal Domain Errors)**：如 `ComfyUIExecutionError` — 远端 GPU 执行崩溃，重试无意义。

### 1.2 Exception Bubbling (异常冒泡)

致命异常必须显式向上传递（Exception Bubbling），击穿所有网络重试层。Python 的 `raise` 语句（无参数）在 `except` 块中会重新抛出当前异常，保留完整的调用栈。

### 1.3 反模式：贪婪拦截网 (Greedy Catch-All)

```python
# ❌ 反模式：吞掉所有异常，致命错误被误当成网络断开
try:
    return self._ws_wait(prompt_id, client_id)
except Exception as ws_err:
    logger.warning("WebSocket unavailable, falling back to HTTP polling.")
    return self._http_poll_wait(prompt_id)
```

```python
# ✅ 正确模式：致命异常优先拦截并向上冒泡
try:
    return self._ws_wait(prompt_id, client_id)
except ComfyUIExecutionError:
    raise  # 致命业务错误，绝不降级，直接穿透！
except Exception as ws_err:
    logger.warning("WebSocket unavailable, falling back to HTTP polling.")
    return self._http_poll_wait(prompt_id)
```

### 1.4 学术出处

- **"Slithering Through Exception Handling Bugs in Python"** (Souza et al., 2024): 收集了 1,649 个 Python 异常处理 Bug，发现 catch-all 是最常见的反模式。
- **Python 官方文档 (docs.python.org/3/tutorial/errors.html)**: 明确建议按异常类型精准捕获。
- **.NET Best Practices for Exceptions (Microsoft)**: "Do not catch exceptions you cannot handle."

---

## 2. Concurrent Futures Global Cancellation (并发池全局撤销)

### 2.1 Circuit Breaker Pattern (断路器模式)

> "In his excellent book Release It!, Michael Nygard popularized the Circuit Breaker pattern to prevent this kind of catastrophic cascade."
> — Martin Fowler, martinfowler.com/bliki/CircuitBreaker.html, 2014

三态状态机：
- **CLOSED**：正常运行，失败计数递增。
- **OPEN**：所有调用被短路，等待恢复超时。
- **HALF_OPEN**：允许单次探测调用，成功则重置为 CLOSED，失败则重新 OPEN。

### 2.2 Python `concurrent.futures` 取消机制

根据 Python 3.14 官方文档 (docs.python.org/3/library/concurrent.futures.html):

- **`Future.cancel()`**: 尝试取消调用。如果调用当前正在执行或已完成，则返回 `False`；否则取消调用并返回 `True`。
- **`Executor.shutdown(cancel_futures=True)`**: 取消所有执行器尚未开始运行的待处理 Future。

### 2.3 全局撤销策略

在 PDG 并发编排中，当一个任务节点发生不可恢复的致命故障时：

1. 立即设置全局 `fatal_abort` 标志。
2. 停止提交新的 Future。
3. 对所有 pending 的 Future 调用 `.cancel()`。
4. 等待已在运行的 Future 自然完成（释放 GPU 信号量）。
5. 将致命异常向上传播至 CLI 层。

### 2.4 学术出处

- **Michael Nygard, "Release It!" (2007)**: Circuit Breaker Pattern 的工业标准定义。
- **Martin Fowler, "Circuit Breaker" (2014)**: 对 Nygard 模式的权威解读。
- **AWS Architecture Blog, "Exponential Backoff And Jitter" (2015)**: 重试策略的工业标准。
- **Python concurrent.futures 官方文档**: `Future.cancel()` 和 `shutdown(cancel_futures=True)` 的行为规范。
- **StackOverflow "Cancelling All Tasks on Failure with concurrent.futures" (2024)**: 社区最佳实践。
