# SESSION-164 外网研究锚点 (Docs-as-Code)

> **Task**: P0-SESSION-163-FINAL-UI-ASSEMBLY
> **Date**: 2026-04-23
> **Scope**: 前端 CLI 向导与 161/162 底层引擎的全链路大满贯流转对接

---

## 1. End-to-End UI/Backend Impedance Matching (端到端阻抗对齐)

在微服务或多级管线架构中，当后端核心模块（如 Registry 和 API Client）在表现层之后完成重构时，前端的 View/CLI 路由必须进行一轮整合对齐（Integration Pass），对接真实的内存实例，防止前端由于历史硬编码导致状态脱节。

| 出处 | 核心观点 |
|------|----------|
| Sam Newman, *Building Microservices* (O'Reilly, 2021) | Backend-for-Frontend (BFF) 模式要求前端与后端之间的载荷契约在每次后端重构后必须重新对齐 |
| Martin Fowler, *Integration Testing* patterns | 契约测试（Contract Testing）确保 UI 与后端服务之间的接口一致性 |
| Zalando Engineering, *End-to-End Microservices Testing* (2019) | 端到端测试技术用于验证应用程序在业务事务中的完整流程 |

**本次应用**: `cli_wizard.py` 的 `_dispatch_mass_production` 函数必须从真实的 `MotionStateLaneRegistry`（SESSION-162 创建）读取动作名称，而非使用任何硬编码字符串。

## 2. Dynamic UI Hydration (前端动态水合)

终端交互的遥测反馈（Telemetry）严禁使用魔法字符串硬编码，必须通过反射或调用底层的 `ActionRegistry` 真实 API，动态生成进度播报。

| 出处 | 核心观点 |
|------|----------|
| LLVM `TargetRegistry` 自注册模式 (`llvm/include/llvm/Support/TargetRegistry.h`) | 目标后端通过静态构造器自注册，注册表永远不硬编码目标名称 |
| Python Registry Pattern with Decorators (Open/Closed Principle) | 自组装插件系统，模块通过 `@decorator` 自注册，消除 if/else 链 |
| Tihomir Manushev, *Implementing the Registry Pattern with Decorators in Python* (Medium, 2025) | 使用 Python 一等函数和导入生命周期替换混乱的 if/else 链 |

**本次应用**: 进度遥测通过 `get_motion_lane_registry().names()` 动态枚举所有已注册动作，逐行打印 `[⚙️ 工业量产] 正在解算 <动作名> 序列贴图...`。

## 3. Precise Exception Catching (精确异常降级网关)

前端对底层流水线的容灾保护，必须精确绑定至 161/162 实际抛出的领域级异常，杜绝全盘拦截宽泛的 `Exception` 导致无法优雅恢复。

| 出处 | 核心观点 |
|------|----------|
| Michael Nygard, *Release It!* (Pragmatic Bookshelf, 2007/2018) | 断路器三态机 (CLOSED → OPEN → HALF_OPEN) 防止级联故障 |
| Martin Fowler, *CircuitBreaker* (martinfowler.com, 2014) | 将受保护的函数调用包装在断路器对象中，监控故障并在阈值后短路 |
| AWS Architecture Blog, *Exponential Backoff And Jitter* (2015) | `min(base × 2^attempt + random_jitter, cap)` 防止雷群效应 |
| Azure Architecture Center, *Circuit Breaker Pattern* (2025) | 优雅降级：熔断后提供回退响应，维持部分功能 |

**本次应用**: 异常捕获精确分层：
1. `PipelineQualityCircuitBreak` — SESSION-162 的 MSE 静止帧自爆异常
2. `ConnectionRefusedError` — ComfyUI 服务未启动/端口拒绝
3. `OSError` — 网络层异常（ConnectionError, TimeoutError 等子类）
4. `Exception` with `violation_type` — PipelineContractError 管线契约违规

## 4. Intent Propagation (意图参数全链路穿透)

用户在前端输入的 `vibe`（意图提示词）必须顺畅地从 UI 菜单穿过 162 的动作阶段，最终原封不动地注入到 161 的 `workflow_api.json` 的 Prompt 节点中。

| 出处 | 核心观点 |
|------|----------|
| Sam Newman, *Building Microservices* (2021) | BFF 载荷变异模式：前端意图必须无损穿透到后端服务 |
| LLVM Pass Infrastructure | 语义寻址（`_meta.title` 标记）vs 硬编码 ID，确保参数注入的稳定性 |

**本次应用**: `_dispatch_mass_production` 从 `spec.raw_vibe` 提取 vibe 字符串，通过 dispatch options 的 `"vibe"` 键传递给 `ProductionStrategy`，最终由 `AIRenderStreamBackend` 注入到 workflow 模板的 `[MathArt_Prompt]` 节点。

## 5. 参考文献完整列表

1. Sam Newman, *Building Microservices*, 2nd ed., O'Reilly, 2021.
2. Michael Nygard, *Release It! Design and Deploy Production-Ready Software*, 2nd ed., Pragmatic Bookshelf, 2018.
3. Martin Fowler, "CircuitBreaker," martinfowler.com, March 6, 2014. https://martinfowler.com/bliki/CircuitBreaker.html
4. AWS Architecture Blog, "Exponential Backoff And Jitter," 2015. https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
5. LLVM Project, `TargetRegistry.h`, https://github.com/llvm-mirror/llvm/blob/master/include/llvm/Support/TargetRegistry.h
6. Tihomir Manushev, "Implementing the Registry Pattern with Decorators in Python," Medium, Dec 2025.
7. Azure Architecture Center, "Circuit Breaker Pattern," Microsoft, 2025. https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
8. Zalando Engineering, "A Journey On End To End Testing A Microservices Architecture," Feb 2019. https://engineering.zalando.com/posts/2019/02/end-to-end-microservices.html
