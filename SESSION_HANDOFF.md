# SESSION HANDOFF

> **SESSION-207 (P0-NULL-SAFE-OUTPUT-AND-EXCEPTION-LADDER)**
> WebSocket `executed` 事件 `output: null` 导致 AttributeError → 静默降级到 HTTP 轮询 + Poison Pill 被 except-all 吞掉
>
> 状态：✅ 双修复完成，已推送 GitHub。等待老大亲测反馈。

---

## 1. 一句话总结

> 老大，`comfyui_ws_client.py` 有两个致命缺陷：
> 1. ComfyUI 发送 `"output": null` 时，`data.get("output", {})` 返回 `None`（不是 `{}`），
>    `list(None.keys())` 抛 AttributeError → 被外层 `except Exception` 捕获 → **静默降级到 HTTP 轮询**。
> 2. 外层 `except Exception` 同时也会吞掉 SESSION-168 的 `ComfyUIExecutionError` Poison Pill，
>    导致 GPU 崩溃信号被静默降级而非传播到编排器。
>
> 后果链：WebSocket 断开 → 实时遥测丢失 → 2秒间隔 HTTP 轮询 → 文件下载不稳定 → `Hard-drop while downloading`。

---

## 2. SESSION-207 根因分析

### 问题 1：`output: null` → NoneType AttributeError

```
ComfyUI 发送: {"type": "executed", "data": {"node": "42", "output": null}}
Python 代码: output = data.get("output", {})  # key 存在但值为 null → 返回 None
             list(output.keys())              # → AttributeError: 'NoneType' object has no attribute 'keys'
→ 被 except Exception 捕获
→ 日志: "WebSocket connection failed: 'NoneType' object has no attribute 'keys'"
→ 静默降级到 HTTP 轮询
```

### 问题 2：Poison Pill 被 except-all 吞掉

```
execution_error 事件 → raise ComfyUIExecutionError (SESSION-168 Poison Pill)
→ 被同一个 except Exception 捕获
→ 日志: "WebSocket connection failed: ComfyUI 端点渲染崩溃..."
→ 静默降级到 HTTP 轮询（而非传播到编排器触发全局中断）
→ 后续角色继续排队到死 GPU
```

### Python `dict.get()` 陷阱（根因知识点）

```python
d = {"output": None}
d.get("output", {})  # → None（key 存在，返回实际值 None，不是默认值 {}）

d = {}
d.get("output", {})  # → {}（key 不存在，返回默认值）
```

---

## 3. 修改清单

| 文件 | 操作 | 修改内容 |
|------|------|----------|
| `mathart/comfy_client/comfyui_ws_client.py` | **Edit** | (1) `executed` 事件处理：`isinstance(output, dict)` 类型守卫 + `output_keys` 预计算；(2) 异常阶梯：`except ComfyUIExecutionError: raise` 插入在 `except Exception` 之前；(3) 文件头部 docstring 新增 SESSION-207 说明 |
| `SESSION_HANDOFF.md` | **Rewritten** | This file |
| `PROJECT_BRAIN.json` | **Updated** | SESSION-207 entry |

---

## 4. SESSION-207 修复策略

### 4.1 Null-Safe Output Handling（第 627-664 行）

```python
# BEFORE (BUG):
output = data.get("output", {})
list(output.keys())  # 💥 when output is None

# AFTER (FIX):
output = data.get("output", {})
if not isinstance(output, dict):
    output = {}
output_keys = list(output.keys())  # ✅ always safe
```

模式对齐 `comfy_client.py` SESSION-200 的已有防御写法。

### 4.2 Precision Exception Ladder（第 764-783 行）

```python
# BEFORE (BUG):
except (ConnectionRefusedError, OSError, TimeoutError, Exception) as e:
    # 吞掉了 ComfyUIExecutionError → 静默降级

# AFTER (FIX):
except ComfyUIExecutionError:
    raise  # Poison Pill MUST pierce through
except (ConnectionRefusedError, OSError, TimeoutError, Exception) as e:
    # 只有真正的网络/传输错误才触发 HTTP 轮询回退
```

模式对齐 `comfy_client.py` SESSION-169 的精确异常阶梯。

### 4.3 已有指标零修改

SESSION-189/190/193 等已调好的参数**完全不动**：
- ControlNet 强度上限 0.55 不变
- Depth/Normal arbitration 0.45 不变
- OpenPose arbitration 1.0 不变
- IPAdapter weight 0.85 不变
- KSampler CFG 上限 4.5 不变
- denoise 1.0 不变

---

## 5. 强制红线自检表

| 红线 | 状态 |
|------|------|
| 拒绝降级：ComfyUIExecutionError 穿透 except-all | ✅ |
| Null-Safe：output 必须是 dict 才调 .keys() | ✅ |
| 已有指标零修改：SESSION-189/190/193 参数不动 | ✅ |
| SESSION-168 Poison Pill 行为完整保留并加固 | ✅ |
| SESSION-175 Hard-Drop Download 完整保留 | ✅ |
| SESSION-206 拒绝降级 + 类型安全匹配完整保留 | ✅ |
| `_execute_live_pipeline` 方法签名零修改 | ✅ |
| 代理环境变量零接触 | ✅ |

---

## 6. 傻瓜验收指引（白话）

老大，修复后的行为变化：

1. **ComfyUI 发送 `output: null` 时**，WebSocket 连接**不再断开**，正常继续监听后续事件。日志会正常显示 `Node XX executed, output keys: []`。

2. **GPU 崩溃（execution_error）时**，异常**直接穿透**到编排器，触发全局中断。不再被静默降级到 HTTP 轮询。

3. **只有真正的网络故障**（连接拒绝、超时等）才会触发 HTTP 轮询回退。

4. **所有渲染参数不变** — 这是纯传输层修复，不涉及任何渲染指标。

---

## 7. 继承红线（本会话未触动）

* `_TELEMETRY_TIMEOUT` 仍 = 900s
* `_download_file_streaming` 仍走 `iter_content(8192)`
* Golden Payload Pre-flight Dump 仍是绝对真理源
* `_execute_live_pipeline` 方法签名零修改
* SESSION-168/169 Fail-Fast Poison Pill 行为完整保留并加固
* SESSION-175 Hard-Drop Download Circuit Breaker 完整保留
* SESSION-199 模型映射修正完整保留
* SESSION-200 全栈遥测契约完整保留
* SESSION-201 CRD 风格意图契约 + Fail-Closed Admission 完整保留
* SESSION-202 WebUI 独立模块架构 + yield 生成器流式推送完整保留
* SESSION-203-HOTFIX Bridge→Gateway 双 key 字典 + 真实管线调度完整保留
* SESSION-204-HOTFIX IPAdapterApply → IPAdapterAdvanced 迁移完整保留
* SESSION-205 运行时模型解析器核心逻辑保留
* SESSION-206 拒绝降级 + 类型安全 ControlNet 解析完整保留

---
