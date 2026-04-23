> **"知识文档不再是摆设，每一条规则都有 if/clamp/assert 守护。" —— 本次升级把 `knowledge/` 目录下的静态 Markdown 知识蒸馏为运行时强制执行的参数验证网关（Knowledge Enforcer Gate），以 Policy-as-Code 模式在渲染管线入口拦截一切违反像素画规则与色彩科学的非法参数。**
>
> **"不仅要守护，还要自动生成守护者。" —— SESSION-155 引入了 Autonomous Knowledge Compiler，将知识蒸馏与代码生成闭环。LLM 提取规则后，自动合成 Enforcer 插件代码，经过严格的 AST 语法树安全校验后，自动注册到网关中。**

**Date**: 2026-04-23
**Parent Commit**: SESSION-154 (Policy-as-Code 知识执法网关)
**Task ID**: P0-SESSION-155-AUTONOMOUS-KNOWLEDGE-COMPILER
**Status**: COMPLETE
**Smoke**: `python -m pytest tests/test_evolution.py -v` → 全部 PASS
**Regression**: SESSION-154 知识网关、SESSION-153 UX 流程完全未受影响

---

## 1. Executive Summary

SESSION-155 聚焦 **Autonomous Knowledge Compiler (自治知识编译器)** —— 将 SESSION-154 建立的 Policy-as-Code 知识执法网关与 SESSION-136 建立的知识蒸馏引擎（Outer Loop）打通，实现 **"文档 → 规则 → 代码 → 注册"** 的全自动闭环。

核心交付物：

1. **LLM-Driven Enforcer Synthesis**：在 `OuterLoopDistiller` 中新增 `_synthesize_enforcer_plugin()`，利用 OpenAI 接口将提取出的 `DistillRule` 列表直接合成为完整的 `EnforcerBase` 子类 Python 代码。
2. **AST Guardian (语法树守护者)**：新增 `ast_sanitizer.py`，在将 LLM 生成的代码写入磁盘前，进行严格的 AST 白名单/黑名单校验。拦截 `exec`、`eval`、`open` 等危险函数，禁止类体内的 `import`，强制要求继承 `EnforcerBase` 并实现必要方法。
3. **Auto-Discovery & Registration**：扩展 `enforcer_registry.py` 的 `_auto_load_enforcers()`，使其自动扫描 `mathart/quality/gates/auto_generated/` 目录，将通过 AST 校验的生成插件动态加载到系统中。
4. **Sci-Fi Terminal UX**：在终端输出中增加了分阶段的进度反馈，清晰展示 "提取规则 → 知识沉淀 → 代码合成 → AST 校验" 的流水线过程。

---

## 2. 核心落地清单

| 文件 | 改动类型 | 要点 |
|---|---|---|
| `mathart/evolution/outer_loop.py` | **升级** | 注入 `_synthesize_enforcer_plugin()`，在 `distill_text()` 流程中调用 LLM 生成代码，并更新 `DistillResult` 与日志格式 |
| `mathart/quality/gates/ast_sanitizer.py` | **新增** | 基于 `ast.NodeVisitor` 的安全校验器，执行 5 条硬性安全规则 |
| `mathart/quality/gates/enforcer_registry.py` | **升级** | `_auto_load_enforcers()` 增加对 `auto_generated/` 目录的扫描 |
| `mathart/quality/gates/__init__.py` | **升级** | 导出 `AstValidationError` 和 `validate_enforcer_code` |
| `mathart/quality/gates/auto_generated/__init__.py` | **新增** | 自动生成插件的存放目录 |
| `docs/research/SESSION155_AUTONOMOUS_COMPILER_RESEARCH.md` | **新增** | 详尽的外网文献与工业界最佳实践研究记录 |
| `PROJECT_BRAIN.json` | **升级** | `v0.99.6`；`pending_tasks` 追加 `P0-SESSION-155-AUTONOMOUS-KNOWLEDGE-COMPILER=CLOSED` |
| `SESSION_HANDOFF.md` | **重写** | 本文件 |

---

## 3. 架构纪律与红线

| 红线 | 本次如何守住 |
|---|---|
| **[AST 绝对防御]** 拒绝危险代码 | `ast_sanitizer.py` 拦截了所有动态执行和文件/网络 I/O，确保生成的插件只能做纯内存参数校验 |
| **[Clamp-Not-Reject]** 裁剪优先 | LLM Prompt 中明确要求生成的 `validate()` 方法必须遵循 Clamp-Not-Reject 原则，返回 `EnforcerSeverity.CLAMPED` |
| **[IoC 自注册]** 零主干修改 | 生成的代码存放在 `auto_generated/`，通过 `pathlib.Path.glob` 动态发现，无需修改任何核心调度代码 |
| **[GitOps 隔离]** 保护主干 | 自动生成的代码虽然写入本地，但按既定契约，后续推送到远端时会走 Proposal Branch（由 `git_agent.py` 保障） |

---

## 4. 外网研究基础

本次实现基于以下外网研究方向的深入调研（详见 `docs/research/SESSION155_AUTONOMOUS_COMPILER_RESEARCH.md`）：

1. **LLM Code Synthesis & Few-Shot Prompting**：参考 CODE4STRUCT (ACL 2023) 和 PromptHub 最佳实践，将目标产物的形状（class signature）作为模板塞入 prompt。
2. **AST Sanitization**：参考 Two Six Technologies 的 "Hijacking the AST to safely handle untrusted python"，使用 `ast.parse(mode='exec')` + `ast.NodeVisitor` 实现白名单/黑名单校验。
3. **Plugin Auto-Discovery**：参考 Pluggy 架构，实现基于目录扫描的轻量级 IoC 插件自发现。
4. **Terminal UX**：参考 Evil Martians 的 CLI UX 最佳实践，实现多阶段流水线进度展示。
