> **"知识不是来者不拒，而是精准分诊。" —— SESSION-156 在 Auto-Compiler 管线前端部署了智能知识漏斗（Knowledge Triage & Dedup Funnel），将所有输入知识先经过原生去重引擎剔除冗余，再经过智能分诊引擎分流为"可编译微观约束"与"不可编译宏观哲学"，从物理层面阻止抽象哲学被强行编译为代码。**
>
> **"零新去重代码，100% 复用 DeduplicationEngine。" —— 严格遵循 DRY 极客原则，分诊漏斗直接调用项目已有的三层去重引擎（精确哈希 → 语义余弦 → 参数合并），没有重新发明任何轮子。**

**Date**: 2026-04-23
**Parent Commit**: SESSION-155 (Autonomous Knowledge Compiler)
**Task ID**: P0-SESSION-156-KNOWLEDGE-TRIAGE-DEDUP
**Status**: COMPLETE
**Smoke**: `python -m pytest tests/test_knowledge_triage.py tests/test_evolution.py::TestOuterLoopDistiller -v` → 全部 25 项 PASS
**Regression**: SESSION-155 Auto-Compiler、SESSION-154 知识网关、SESSION-153 UX 流程完全未受影响（69/69 PASS）

---

## 1. Executive Summary

SESSION-156 聚焦 **Knowledge Triage & Native Deduplication Funnel（知识智能分诊与原生去重漏斗）** —— 在 SESSION-155 建立的 Auto-Compiler 管线前端部署一道智能前置漏斗，实现 **"去重 → 分诊 → 路由"** 三级过滤。

核心交付物：

1. **KnowledgeTriageEngine（知识分诊引擎）**：新增 `mathart/distill/knowledge_triage.py`，基于信号计数启发式算法，将每条知识规则分类为 `[Actionable-Rule]`（微观约束，可编译）或 `[Macro-Guidance]`（宏观哲学，仅归档）。12 组 Actionable 信号模式 + 12 组 Macro 信号模式，保守策略：无信号时默认归为 Macro（安全归档）。
2. **KnowledgeFunnel（知识漏斗编排器）**：编排完整的 Dedup → Triage → Route 管线。**零新去重代码**，直接实例化并调用项目已有的 `DeduplicationEngine`。
3. **OuterLoopDistiller 集成**：`distill_text()` 方法现在在规则提取后、知识集成前，强制将所有规则通过 `KnowledgeFunnel` 过滤。**物理隔离**：只有 `[Actionable-Rule]` 规则才能到达 `_synthesize_enforcer_plugin()`，`[Macro-Guidance]` 规则被物理阻断，永远不会触发代码生成。
4. **Sci-Fi Terminal UX v2**：7 步流水线进度展示，透明展示"文档接收 → 规则提取 → 去重唤醒 → 分诊分流 → 编译合成 → AST 校验 → 结果汇总"全过程。
5. **DaC 文档同步**：`docs/USER_GUIDE.md` 新增第 7 章"知识智能分流与原生去重"，含傻瓜验收指引。

---

## 2. 核心落地清单

| 文件 | 改动类型 | 要点 |
|---|---|---|
| `mathart/distill/knowledge_triage.py` | **新增** | `KnowledgeTriageEngine` 分诊引擎 + `KnowledgeFunnel` 漏斗编排器 + `KnowledgeTier` 枚举 + `TriageDecision`/`TriageResult`/`FunnelResult` 数据类 |
| `mathart/distill/__init__.py` | **升级** | 导出 6 个新符号：`KnowledgeFunnel`, `KnowledgeTriageEngine`, `KnowledgeTier`, `TriageDecision`, `TriageResult`, `FunnelResult` |
| `mathart/evolution/outer_loop.py` | **升级** | `__init__` 初始化 `KnowledgeFunnel`；`distill_text()` 注入去重+分诊漏斗；新增 `_rebuild_rules_from_funnel()` 方法；`_format_log_entry()` 增加漏斗统计；`DistillResult` 新增 `triage_summary` 字段；7 步 UX 流水线 |
| `docs/USER_GUIDE.md` | **升级** | 新增第 7 章"知识智能分流与原生去重"，含分诊原理、终端输出示例、傻瓜验收指引 |
| `tests/test_knowledge_triage.py` | **新增** | 19 项测试：8 项分诊引擎单元测试 + 3 项漏斗集成测试 + 6 项 OuterLoop 集成测试 + 2 项物理隔离测试 |
| `PROJECT_BRAIN.json` | **升级** | `v0.99.7`；`SESSION-156`；新增 `P0-SESSION-156-KNOWLEDGE-TRIAGE-DEDUP=CLOSED` |
| `SESSION_HANDOFF.md` | **重写** | 本文件 |

---

## 3. 分诊漏斗如何接入系统？

### 数据流图

```
Raw Text
  │
  ▼
[OuterLoopDistiller.distill_text()]
  │
  ├─ [2/7] 规则提取 (LLM / Heuristic)
  │         ↓ list[DistillRule]
  │
  ├─ [3/7] KnowledgeFunnel.process()
  │         │
  │         ├─ Stage 1: DeduplicationEngine.deduplicate_rules()
  │         │   → 精确哈希 → 语义余弦 → 参数合并
  │         │   → 剔除重复，保留唯一规则
  │         │
  │         ├─ Stage 2: KnowledgeTriageEngine.triage_batch()
  │         │   → 信号计数：Actionable vs Macro
  │         │   → 保守策略：无信号 → Macro（安全归档）
  │         │
  │         └─ Output: actionable_rules[], macro_rules[]
  │
  ├─ [4/7] _integrate_rules() ← 两个 tier 都写入 knowledge/ 文件
  │         （Macro 规则带 [Macro-Guidance] 标签）
  │
  ├─ [5/7] _synthesize_enforcer_plugin() ← ⚠️ 仅 actionable_rules
  │         （Macro 规则在此被 PHYSICALLY BLOCKED）
  │
  └─ [7/7] DistillResult (含 triage_summary)
```

### 去重引擎如何被唤醒？

`KnowledgeFunnel.__init__()` 中直接实例化 `DeduplicationEngine`：

```python
from mathart.distill.deduplication import DeduplicationEngine
self.dedup_engine = DeduplicationEngine(project_root=self.project_root, verbose=verbose)
self.dedup_engine.load_existing()  # 加载已有知识库的哈希指纹
```

在 `process()` 方法中调用 `self.dedup_engine.deduplicate_rules(dedup_input)` 完成三层去重。**零新去重代码**。

### 改动了哪几个文件将分流漏斗接入了系统？

1. **`mathart/distill/knowledge_triage.py`**（新增）：分诊引擎 + 漏斗编排器
2. **`mathart/evolution/outer_loop.py`**（修改 3 处）：
   - `__init__` → 初始化 `self._funnel = KnowledgeFunnel(...)`
   - `distill_text()` → 在规则提取后调用 `self._funnel.process()`
   - 新增 `_rebuild_rules_from_funnel()` 辅助方法
3. **`mathart/distill/__init__.py`**（修改）：导出新符号

---

## 4. 架构纪律与红线

| 红线 | 本次如何守住 |
|---|---|
| **[严禁破坏现有底座]** Auto-Compiler 完整保留 | `_synthesize_enforcer_plugin()` 代码零修改，仅在其上游增加了漏斗过滤 |
| **[绝对复用]** DRY 极客原则 | `KnowledgeFunnel` 直接 `from mathart.distill.deduplication import DeduplicationEngine`，零新去重代码 |
| **[物理隔离]** Macro 不可编译 | `if tier == MACRO: continue` — 宏观哲学在 `triage_batch()` 中被跳过，永远不进入 `actionable_indices` |
| **[保守策略]** 宁可漏编不可误编 | 无信号规则默认归为 Macro（安全归档），防止 AI 幻觉 |
| **[UX 零退化]** 主循环不死 | 所有新增 print 均在 `if self.verbose:` 保护下，不影响非 TTY 环境 |

---

## 5. 傻瓜验收指引

### 测试 1：宏观废话阻断测试

```python
from mathart.evolution.outer_loop import OuterLoopDistiller

distiller = OuterLoopDistiller(use_llm=False, verbose=True)
result = distiller.distill_text(
    "游戏必须要好玩。好的游戏设计应该让玩家感到沉浸和满足。"
    "优秀的关卡设计需要有节奏感和情感曲线。"
    "游戏的美学应该追求和谐与平衡。",
    source_name="废话测试"
)
print("生成的插件:", result.enforcer_plugins_generated)  # 应为 []
print("分诊摘要:", result.triage_summary)  # macro_count > 0, actionable_count == 0
```

**预期**：终端显示 `[⚖️ 知识分诊] 判定为【宏观哲学 Macro-Guidance】，安全归档，跳过代码生成...`，`enforcer_plugins_generated` 为空。

### 测试 2：微观约束通过测试

```python
result2 = distiller.distill_text(
    "spring_k = 15.0\ndamping_c = 4.0\nmax_velocity = 200 px/s\ncanvas_size = 32",
    source_name="物理约束测试"
)
print("分诊摘要:", result2.triage_summary)  # actionable_count > 0
```

**预期**：终端显示 `[⚖️ 知识分诊] 判定为【微观约束 Actionable-Rule】，送入 Python 编译引擎...`。

### 测试 3：自动化测试

```bash
python -m pytest tests/test_knowledge_triage.py -v
# 19 passed
```

---

## 6. 下一步建议 (SESSION-157+)

| 优先级 | 任务 | 说明 |
|---|---|---|
| P1 | LLM-Assisted Triage | 当前分诊为纯启发式，可引入 LLM 作为二级分诊器提升准确率 |
| P1 | Triage Feedback Loop | 允许用户手动纠正分诊结果，形成反馈闭环 |
| P2 | Macro Knowledge Retrieval | 将归档的 Macro 知识接入 RAG 系统，供 LLM 推理时作为上下文 |
| P2 | Triage Dashboard | 在 CLI 中增加分诊历史统计面板 |
