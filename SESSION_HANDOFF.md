# SESSION HANDOFF

> **SESSION-207 (P0-NULL-SAFE-OUTPUT + EXCEPTION-LADDER + FULL-OBSERVABILITY)**
> WebSocket `executed` 事件 `output: null` 导致 AttributeError → 静默降级到 HTTP 轮询 + Poison Pill 被 except-all 吞掉 + 10 个日志盲区全部补全
>
> 状态：✅ 三重修复完成，已推送 GitHub。等待老大亲测反馈。

---

## 1. 一句话总结

> 老大，`comfyui_ws_client.py` 有两个致命缺陷 + 10 个日志盲区：
> 1. ComfyUI 发送 `"output": null` 时 → AttributeError → 静默降级到 HTTP 轮询
> 2. 外层 `except Exception` 吞掉 SESSION-168 Poison Pill → GPU 崩溃信号被静默降级
> 3. 大量关键事件（GPU 点火、null output、JSON 畸形、HTTP 轮询、下载进度等）在日志中完全不可见
>
> 本次 SESSION-207 三重修复：bug 修复 + 异常阶梯加固 + 全链路日志可观测性。

---

## 2. SESSION-207 修复内容

### 2.1 Bug 修复（SESSION-207 第一次推送已完成）

| 修复 | 描述 |
|------|------|
| Null-Safe Output | `isinstance(output, dict)` 类型守卫，防止 `output: null` 导致 AttributeError |
| Exception Ladder | `except ComfyUIExecutionError: raise` 插入在 `except Exception` 之前，Poison Pill 穿透 |

### 2.2 日志盲区补全（SESSION-207 第二次推送）

| # | 盲区 | 修复 | 日志级别 |
|---|------|------|----------|
| 1 | `execution_start` 事件完全未处理 | 新增 `elif event_type == "execution_start"` 分支 + logger.info | INFO |
| 2 | null output 场景无 WARNING | 新增 `logger.warning` 显示 type/value | WARNING |
| 3 | JSON 解析失败静默跳过 | `json.JSONDecodeError` 添加 `logger.warning` | WARNING |
| 4 | 未知事件类型静默丢弃 | 新增 `else` 分支 + `logger.debug` | DEBUG |
| 5 | WebSocket 超时截止无 logger | 新增 `logger.error` | ERROR |
| 6 | HTTP 轮询全程无日志 | 新增开始/每次轮询/成功/失败/超时全链路日志 | INFO/DEBUG/WARNING/ERROR |
| 7 | HTTP 轮询 error 状态无 logger | 新增 `logger.error` | ERROR |
| 8 | `_get_history` 成功无日志 | 新增 `logger.info` 含响应大小 | INFO |
| 9 | `_download_outputs` 开始/结束无日志 | 新增开始（节点数/文件数）+ 完成（图片数/视频数/目录）日志 | INFO |
| 10 | Poison Pill 穿透路径无日志 | 新增 `logger.critical` 在 re-raise 前 | CRITICAL |

### 2.3 日志级别设计原则

| 级别 | 使用场景 |
|------|----------|
| **CRITICAL** | Poison Pill 穿透、execution_error 致命崩溃 |
| **ERROR** | 超时、HTTP 轮询检测到 error、POST 失败 |
| **WARNING** | null output 归一化、JSON 畸形、WebSocket 降级、轮询网络错误 |
| **INFO** | GPU 点火、节点执行、进度、下载开始/完成、history 获取 |
| **DEBUG** | 未知事件类型、二进制事件、轮询等待 |

---

## 3. 修改清单

| 文件 | 操作 | 修改内容 |
|------|------|----------|
| `mathart/comfy_client/comfyui_ws_client.py` | **Edit** | (1) null-safe output + exception ladder；(2) 10 个日志盲区全部补全；(3) 文件头部 docstring 新增 SESSION-207 |
| `SESSION_HANDOFF.md` | **Rewritten** | This file |
| `PROJECT_BRAIN.json` | **Updated** | SESSION-207 entry |

---

## 4. 强制红线自检表

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
| 全链路日志可观测性：44 个 logger 调用覆盖所有路径 | ✅ |

---

## 5. 傻瓜验收指引（白话）

老大，修复后你在日志中能看到的新信息：

1. **GPU 点火确认** — `[ComfyUIClient] Execution started (GPU ignition): prompt_id=xxx`
2. **null output 警告** — `[ComfyUIClient] Node XX executed with non-dict output (type=NoneType, value=None) — normalized to {}`
3. **JSON 畸形警告** — `[ComfyUIClient] Received malformed JSON from WebSocket, skipping: ...`
4. **Poison Pill 穿透** — `[ComfyUIClient] ComfyUIExecutionError piercing through WebSocket exception handler`
5. **HTTP 轮询全过程** — 开始、每次轮询、成功/失败、超时，全部可见
6. **下载进度** — `Starting artifact download: 3 output nodes, 5 files to download` → `Artifact download complete: 5 images, 0 videos saved to /path/`
7. **History 获取** — `GET /history/xxx succeeded (1234 bytes)`

---

## 6. 继承红线（本会话未触动）

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
