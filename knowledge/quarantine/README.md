# `knowledge/quarantine/` — 检疫隔离区

> **合约来源：** SESSION-138 `P0-SESSION-135-KNOWLEDGE-QA-GATE`，详见
> `docs/research/SESSION-138-KNOWLEDGE-QA-GATE-RESEARCH.md` 与
> `mathart/distill/sandbox_validator.py`。

这里是**所有未经沙盒验证的**蒸馏产物的唯一落脚点。任何新提取的 `KnowledgeRule`（JSON 或 Markdown 形式）必须先写入本目录，由 `mathart.distill.sandbox_validator.SandboxValidator` 接管并执行：

1. `source_quote` 证据链完整性检查（缺失即视为幻觉，立刻拒绝）。
2. AST 白名单解析 + 纯 CPU 数学 Fuzzing（0/-1/NaN/inf 等边界值）。
3. 轻量物理稳定性干跑（100 步，能量/穿模熔断）。
4. 3 秒硬超时熔断。

**绝对禁止**人工或自动化 Agent 绕过 `SandboxValidator` 把本目录的规则直接搬运到 `knowledge/active/`。`RuntimeDistillationBus.refresh_from_knowledge()` 与 `preload_all_distilled_knowledge()` 都已加装过滤，不会读取本目录。
