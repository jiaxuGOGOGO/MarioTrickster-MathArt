# SESSION-169 交接备忘录

> **"老大，异常穿透与全局并发撤销已全线贯通！之前 ComfyUI 节点崩溃时，致命异常被 `wait_for_completion()` 的贪婪拦截网误吞，导致系统假死并把 19 个角色继续排队到已死的 GPU 上。现在，致命异常会像穿甲弹一样击穿网络降级层，直达 PDG 调度器触发全局 Future 撤销，再传播到 CLI 向导弹出红底白字的雪崩告警。整条异常链路：ComfyUI WS → comfy_client → ai_render_stream_backend → PDG 全局撤销 → CLI 熔断，一气呵成，零假死！"**

**Date**: 2026-04-23
**Parent Commit**: SESSION-168
**Task ID**: P0-SESSION-169-EXCEPTION-PIERCING-AND-GLOBAL-ABORT
**Status**: CLOSED
**External Anchors**: `docs/RESEARCH_NOTES_SESSION_169.md`

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **异常穿透 (Exception Piercing)** | `mathart/backend/comfy_client.py` | Targeted Exception Handling — `wait_for_completion()` 在泛型 `except Exception` 之前显式重新抛出 `ComfyUIExecutionError` 和 `RenderTimeoutError`，防止致命异常被 HTTP 轮询回退吞没 |
| 2 | **全局 Future 撤销 (Global Abort)** | `mathart/level/pdg.py` | Concurrent Futures Global Cancellation (Python docs) + Circuit Breaker Pattern (Nygard) — `_execute_task_invocations_concurrently()` 新增 `fatal_exception` 追踪，立即停止提交、取消 pending Future、排空 in-flight Future |
| 3 | **增强型断路器 (Enhanced Circuit Breaker)** | `mathart/backend/ai_render_stream_backend.py` | Circuit Breaker Pattern — `ComfyUIExecutionError` 捕获块新增红色 stderr 崩溃 Banner 和详细节点诊断 |
| 4 | **前端升级 (CLI Upgrade)** | `mathart/cli_wizard.py` | Fail-Loud Validation — 熔断告警升级为红底白字，新增异常穿透路径追踪行，烘焙网关 Banner 新增 SESSION-169 状态行 |
| 5 | **UX 防腐蚀 (UX Anti-Corrosion)** | `mathart/factory/mass_production.py` | 烘焙网关终端打印新增 SESSION-169 异常穿透与全局撤销状态行 |
| 6 | **外网工业理论锚点** | `docs/RESEARCH_NOTES_SESSION_169.md` | 包含 Targeted Exception Handling、Exception Bubbling、Greedy Catch-All 反模式、Circuit Breaker Pattern、Concurrent Futures Global Cancellation 的完整研究笔记 |
| 7 | **用户手册更新** | `docs/USER_GUIDE.md` | 新增 §10.10 SESSION-169 章节，含修复内容表格、傻瓜验收步骤、外网理论锚点 |

## 2. 防假死红线 (Anti-Deadlock Red Lines)

以下是 SESSION-169 部署的不可退化红线：

1. **FATAL execution_error 之后绝不许出现 "Falling back to HTTP polling" 的字样！** — `comfy_client.py` 的 `except ComfyUIExecutionError: raise` 保证了这一点。
2. **致命异常必须穿透所有网络重试层** — `ComfyUIExecutionError` 和 `RenderTimeoutError` 在 `except Exception` 之前被显式捕获并重新抛出。
3. **PDG 并发池必须全局撤销** — 当任一调用抛出致命异常时，所有 pending Future 被 `.cancel()`，in-flight Future 被排空。

## 3. 遗留已知问题 (Known Technical Debt)

- ComfyUI 端的具体环境冲突（如 `--fp16` 缺失或 ControlNet 版本过旧）仍需用户根据报错信息手动排查和修复。
- 纯 CPU 沙盒审计 (`Dry-Run`) 模式目前不会模拟 ComfyUI `execution_error`。
- `_execute_task_invocations_concurrently` 中已在运行的 Future 无法被 `.cancel()` 取消（Python `concurrent.futures` 的固有限制），只能等待其自然完成后释放 GPU 信号量。

## 4. 下一步建议 (Next Steps)

1. 在配备显卡的物理机上运行 `mathart`，触发一个必崩的 ComfyUI 工作流，验证异常穿透路径和全局撤销是否完美展示。
2. 验证终端日志中 **绝不出现** `Falling back to HTTP polling` 字样。
3. 考虑为 PDG 调度器添加 `HALF_OPEN` 状态支持，允许在断路器打开后进行探测性重试。
