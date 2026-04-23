# SESSION-168: ComfyUI API 防死锁抛出与全局推流断路器 (Deadlock Breaker)

> **"老大，ComfyUI 推流死锁漏洞已彻底修复！当远端 ComfyUI 发生诸如 PyTorch 精度冲突等灾难性错误时，客户端不再默默吞掉错误并陷入无限等待。现在，它会精准捕获 `execution_error` 毒药消息，触发全局断路器强制熔断剩余推流，并在前端弹出高亮雪崩告警，将控制权和报错详情原原本本交还给你！"**

**Date**: 2026-04-23
**Parent Commit**: SESSION-167
**Task ID**: P0-SESSION-168-COMFYUI-CLIENT-DEADLOCK-BREAKER
**Status**: CLOSED
**External Anchors**: `docs/RESEARCH_NOTES_SESSION_168.md`

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **WebSocket Fail-Fast 抛出** | `mathart/comfy_client/comfyui_ws_client.py` | WebSocket Poison Pill Pattern — `execution_error` 强制撕裂 `ws.recv()` 循环 |
| 2 | **后端同步抛出与导出** | `mathart/backend/comfy_client.py`, `mathart/comfy_client/__init__.py` | Fail-Fast Principle — `_ws_wait()` 同步向上层抛出 `ComfyUIExecutionError` |
| 3 | **全局刹车踏板 (Circuit Breaker)** | `mathart/backend/ai_render_stream_backend.py` | Circuit Breaker Pattern (Michael Nygard) — 捕获毒药异常后强制开路并撤销后续所有动作 |
| 4 | **前端雪崩告警与 UX 防腐蚀** | `mathart/cli_wizard.py` | Fail-Loud Validation — 精准拦截 `ComfyUIExecutionError` 弹出红色 Banner 及节点详情，并在网关处增加状态行 |

## 2. 遗留已知问题 (Known Technical Debt)
- ComfyUI 端的具体环境冲突（如 `--fp16` 缺失或 ControlNet 版本过旧）仍需用户根据报错信息手动排查和修复。
- 纯 CPU 沙盒审计 (`Dry-Run`) 模式目前不会模拟 ComfyUI `execution_error`。

## 3. 下一步建议 (Next Steps)
1. 在配备显卡的物理机上运行 `mathart`，触发一个必崩的 ComfyUI 工作流，验证全局断路器和雪崩告警 Banner 是否完美展示。
2. 完善并优化 `ComfyUIExecutionError` 的错误追踪信息提取逻辑，以便为用户提供更清晰的修复指引。
