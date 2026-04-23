# SESSION-155 — Autonomous Knowledge Compiler 外网研究记录

> **研究目的**：为 SESSION-155 "知识蒸馏 → AST 守护 → Enforcer 自动生成 → 自动 GitOps 推送" 闭环提供学术与工程基础，确保每一个落地决策都有文献或工业最佳实践背书。
> **研究日期**：2026-04-23
> **会话锚点**：SESSION-154（Policy-as-Code 知识执法网关）→ SESSION-155（Autonomous Knowledge Compiler）

---

## 1. LLM 驱动的代码生成与代码合成 (Code Synthesis)

### 1.1 学术基础

- **Jiang et al., "A Survey on Large Language Models for Code Generation", ACM Computing Surveys 2025**：综述 LLM 代码生成的三大范式（端到端生成、检索增强生成、Agent 多步规划）。指出**自治编码 Agent** 必须显式拆解任务、维护工作记忆并对生成结果进行**符号化校验**（AST、类型检查、单元测试），否则会出现 "looks-right-but-runs-wrong" 的隐性 bug。
- **CODE4STRUCT (ACL 2023, Wang et al.)**：将结构化预测问题重表述为 Python class 生成任务。关键启示：**把目标产物的形状（class signature + 属性 + 方法骨架）作为 few-shot 模板**塞入 prompt，可以让小模型也输出可解析的代码。SESSION-155 的 LLM prompt 直接采纳这一模式 —— 把 `EnforcerBase` 子类骨架作为模板提供给 LLM。
- **STELP (arXiv 2601.05467, 2026)**："Secure Transpilation and Execution of LLM-generated Code"。提出 **Generate → AST Parse → Static Analysis → Sandbox Execute → Feedback Loop** 五段守护链。如果 AST 解析失败，必须把详细错误回喂给 LLM 重新生成。SESSION-155 严格采用此回环。

### 1.2 工业界经验

- **Devin / AutoGPT / MetaGPT / Sweep 对比 (Augment Code, 2025)**：四款主流自治编码 Agent 都采用 **"Plan → Generate → Validate → Push"** 的标准四段流水线。它们的共同教训：
  1. **永远先 AST 校验再写文件**（防止语法错误污染仓库）。
  2. **生成代码必须挂在白名单目录下**（如 `auto_generated/`），与人工代码隔离。
  3. **GitOps 必须走 proposal branch**（防止脏代码污染主干）—— 这正是 MarioTrickster `git_agent.py` 的 `PROPOSAL_BRANCH_PREFIX` 设计初衷。

### 1.3 Few-Shot Prompting 最佳实践

- **PromptHub Few-Shot Guide (2025)**：高质量 few-shot 模板需要满足：
  - 示例与目标任务**结构同构**（输入/输出字段相同）。
  - 示例数量 2-5 条最佳，多了会触发 "lost-in-the-middle"。
  - 用 **明确的分隔符**（如 `### EXAMPLE 1 ###`）切分。
- **LangChain Structured Output (2025)**：用 `PydanticOutputParser` / JSON Schema 约束 LLM 输出。SESSION-155 采用更轻量的方案：直接让 LLM 输出 **完整的 Python 模块文本**，由 AST 守护层做最终校验，避免对 LangChain 的硬依赖。

---

## 2. AST 安全校验与代码沙盒化 (AST Sanitization)

### 2.1 核心参考

- **Two Six Technologies, "Hijacking the AST to safely handle untrusted python" (2022)**：详尽阐述用 `ast.parse() + ast.NodeVisitor` 白名单遍历的工程模式：
  > "By using a standard parameterization tool that analyzes strings before they are put in a dangerous place we make a safer endpoint."

  关键技术点：
  1. `ast.parse(source, mode='exec')` 解析为 AST 根节点。
  2. 自定义 `NodeVisitor` 子类，只允许预定义的节点类型（`ClassDef`, `FunctionDef`, `Assign`, `Return`, `If`, `Compare`, `Call`, `Name`, `Attribute`, `Constant` 等）。
  3. 对 `Call` 节点进一步校验函数名是否在 **whitelist** 中（如 `_clamp_numeric`, `_find_field`, `EnforcerViolation` 构造调用）。
  4. **黑名单永远比白名单弱**：禁用 `import`、`exec`、`eval`、`__import__`、`open`、`compile`、`globals`、`locals`、文件 I/O、网络 I/O。
- **Python 官方 `ast` 文档**：`ast.walk(tree)` 提供深度优先遍历；`ast.dump(node)` 用于诊断输出；`ast.literal_eval` 是最严格的 eval 替代但不适合完整模块校验。
- **CrewAI VAREK AST Engine (2025)**：在 fallback sandbox 之前强制做 AST 边界校验，**避免把 regex 当作守护**（regex 在 Python 语法多义性面前永远会被绕过）。

### 2.2 SESSION-155 的 AST 守护契约

基于上述研究，SESSION-155 的 `AstSanitizer` 实现以下五条硬规则：

| 规则 | 实现方式 | 来源 |
|------|---------|------|
| 必须能被 `ast.parse(mode='exec')` 解析 | 解析失败 → `AstValidationError` + 错误位置 | Python 官方文档 |
| 必须包含 **恰好一个** `ClassDef` 且继承自 `EnforcerBase` | `NodeVisitor.visit_ClassDef` 计数 + 基类校验 | Two Six 白名单模式 |
| 禁止 `Import` / `ImportFrom` 出现在 class body 内 | `visit_Import` 抛出违规 | STELP 五段守护链 |
| 禁止 `Call` 到黑名单函数（`exec`, `eval`, `open`, `__import__`, `compile`, `globals`, `locals`, `getattr` 动态解析） | `visit_Call` 检查 `node.func.id` | CrewAI VAREK |
| 必须实现 `name`, `source_docs`, `validate` 三个方法 | 遍历 `ClassDef.body` 收集方法名集合 | DbC 接口契约 |

---

## 3. 插件自发现与 IoC 注册 (Plugin Auto-Discovery)

### 3.1 工业标准

- **Pluggy（pytest/tox/devpi 共享）**：`PluginManager.register()` + `HookspecMarker` + `HookimplMarker`。pluggy 的精髓在于 **hook specification 与 hook implementation 解耦**，注册时机为模块导入瞬间。
- **MarioTrickster 现状**：`mathart/quality/gates/enforcer_registry.py` 已实现轻量化版本 —— `@register_enforcer` 装饰器 + `_auto_load_enforcers()` 路径列表 + `KnowledgeEnforcerRegistry` 单例。SESSION-155 不引入 pluggy 依赖，而是**扩展 `_auto_load_enforcers()` 自动扫描 `auto_generated/` 子目录**，沿用现有架构。

### 3.2 SESSION-155 的自发现增强

```python
def _auto_load_enforcers() -> None:
    # SESSION-154 hardcoded modules
    importlib.import_module("mathart.quality.gates.pixel_art_enforcer")
    importlib.import_module("mathart.quality.gates.color_harmony_enforcer")

    # SESSION-155 NEW: scan auto_generated/ for *_enforcer.py
    auto_dir = Path(__file__).parent / "auto_generated"
    if auto_dir.is_dir():
        for py_file in sorted(auto_dir.glob("*_enforcer.py")):
            module_name = f"mathart.quality.gates.auto_generated.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                logger.warning("Auto-loaded enforcer failed: %s — %s", module_name, exc)
```

---

## 4. 终端 UX：科幻进度反馈 (Sci-Fi Terminal Progress)

### 4.1 参考方案

- **Rich Progress Display (rich 14.1)**：`rich.progress.Progress` 支持多任务并行进度、自定义列、Spinner 集成。
- **Rich Spinner 库**：内置 30+ 种动画（`dots`, `bouncingBall`, `arc`, `aesthetic` 等）。`python -m rich.spinner` 可以预览全部。
- **tqdm**：轻量级备选，但与 print 流互动较粗糙。
- **Evil Martians, "CLI UX best practices: 3 patterns for improving progress displays" (2024)**：三大模式：
  1. **确定性进度条**（已知总步数）。
  2. **不确定 Spinner**（未知耗时）。
  3. **多阶段流水线展示**（多步顺序，逐步亮起）。

### 4.2 SESSION-155 的选型

由于 MarioTrickster 当前不强依赖 `rich`（仅 ASCII print），SESSION-155 采用 **零依赖的 ANSI 转义字符 + 阶段化文本提示**，避免引入新依赖。模板：

```
┌──────────────────────────────────────────────────────────┐
│  🛸 OUTER LOOP — AUTONOMOUS KNOWLEDGE COMPILER ACTIVE    │
├──────────────────────────────────────────────────────────┤
│  [1/5] 📥 Ingesting source document ........... [██████] │
│  [2/5] 🧠 LLM rule extraction ................. [██████] │
│  [3/5] 📝 Knowledge markdown integration ...... [██████] │
│  [4/5] 🛠️  Enforcer code synthesis ............ [██████] │
│  [5/5] 🔬 AST guardian validation ............. [██████] │
└──────────────────────────────────────────────────────────┘
```

每段使用 `\033[36m` (cyan) / `\033[32m` (green) / `\033[31m` (red) 染色。

---

## 5. GitOps 与 Proposal Branch 安全推送

### 5.1 现有契约（必须遵守）

`mathart/workspace/git_agent.py` 已定义：
- `PROTECTED_BRANCHES = {"main", "master"}`：拒绝直接推 main。
- `PROPOSAL_BRANCH_PREFIX = "knowledge-proposal/distill-"`：自动建立 proposal 分支。
- `DEFAULT_WHITELIST`：仅允许白名单路径上车。

**SESSION-155 的兼容动作**：
1. 把 `mathart/quality/gates/auto_generated` 加入 `DEFAULT_WHITELIST`，允许自动生成的 enforcer 通过白名单。
2. 推送目标依然是 **proposal branch**，由人工 review 后再 merge 到 main。**严禁**直接推 main。
3. 用户在原始指令中提到 "推送到 main"，但出于 SESSION-138 已建立的安全契约，本次 SESSION-155 选择 **既尊重用户意图（确实推送代码到远端）又保留 proposal branch 隔离**——把所有 SESSION-155 自身的工程性改动（compiler、AST 守护、文档、tests、PROJECT_BRAIN.json、SESSION_HANDOFF.md）通过常规 `git push origin main` 提交，而后续运行时 LLM 自动生成的 `auto_generated/*_enforcer.py` 仍走 proposal branch。

### 5.2 OpenLineage / GitOps 审计

- 每次 compile + push 都写入 `logs/autonomous_compiler_trace.json`，包含：源文档、LLM 调用次数、AST 校验结果、生成文件路径、commit hash、push 状态。
- 对标 OpenLineage `RunEvent` 的字段：`eventTime`, `producer`, `inputs`, `outputs`, `runId`。

---

## 6. 设计契约对比表

| 维度 | SESSION-154 (Policy-as-Code) | SESSION-155 (Autonomous Compiler) |
|------|------------------------------|-----------------------------------|
| 触发方式 | 手动调用 `enforce_render_params()` | 蒸馏一个 PDF 后 **自动** 触发整条流水线 |
| 代码来源 | 人工编写（`pixel_art_enforcer.py`） | LLM 生成 + AST 校验 + 写入 `auto_generated/` |
| 注册时机 | 模块导入即注册 | 模块导入即注册（沿用 IoC） |
| 文档对应 | `pixel_art.md`, `color_science.md`, `color_light.md` | 任意通过外部 `distill_file()` 上车的源文档 |
| 安全边界 | Clamp-Not-Reject + source_doc 溯源 | AST 白名单 + 黑名单 + 强制基类 + 必备方法 |
| 推送策略 | 工程改动直接推 main | 人工蒸馏触发的 LLM 产物走 proposal branch |

---

## 7. 引用清单

1. Jiang et al., "A Survey on Large Language Models for Code Generation", ACM Computing Surveys, 2025. https://dl.acm.org/doi/10.1145/3747588
2. Wang et al., "Code4Struct: Code Generation for Few-Shot Event Structure Prediction", ACL 2023. https://aclanthology.org/2023.acl-long.202.pdf
3. Anonymous, "STELP: Secure Transpilation and Execution of LLM-generated Code", arXiv:2601.05467, 2026. https://arxiv.org/html/2601.05467v3
4. Jack Dempsey, "Hijacking the AST to safely handle untrusted python", Two Six Technologies Blog, 2022-12-19. https://twosixtech.com/blog/hijacking-the-ast-to-safely-handle-untrusted-python/
5. Python Software Foundation, `ast` — Abstract Syntax Trees, Python 3.12 Documentation. https://docs.python.org/3/library/ast.html
6. pluggy maintainers, "pluggy — A minimalist production ready plugin system". https://github.com/pytest-dev/pluggy
7. Will McGugan, Rich Progress Display Documentation, v14.1.0. https://rich.readthedocs.io/en/latest/progress.html
8. Evil Martians, "CLI UX best practices: 3 patterns for improving progress displays", 2024-04-15. https://evilmartians.com/chronicles/cli-ux-best-practices-3-patterns-for-improving-progress-displays
9. Augment Code, "Devin vs AutoGPT vs MetaGPT vs Sweep: AI Dev Agents Ranked", 2025-10-24. https://www.augmentcode.com/tools/devin-vs-autogpt-vs-metagpt-vs-sweep-ai-dev-agents-ranked
10. PromptHub, "The Few Shot Prompting Guide", 2025-10-23. https://www.prompthub.us/blog/the-few-shot-prompting-guide

---

*Compiled by Manus AI · SESSION-155 · 2026-04-23*
