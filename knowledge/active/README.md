# `knowledge/active/` — 生产可用区（Safe Store）

本目录遵循 Microsoft Learn *Quarantine Pattern* 中定义的 **safe store** 语义：这里的每一条 `KnowledgeRule` 都已经过 `SandboxValidator` 的 100% PASSED 审计，可以被 `RuntimeDistillationBus` 与其他量产总线直接消费。

## 进入条件

一条规则被允许出现在本目录，**必须同时满足**以下全部条件：

1. 规则对象含有非空 `source_quote`（可逐字回溯原文）；
2. 规则的所有公式表达式通过 AST 白名单校验（禁止 `Call`、`Attribute`、`Import`、`Subscript` 等危险节点）；
3. 在 `FUZZ_SAMPLES = [0, -1, 1, 1e-6, 1e6, +inf, -inf, nan]` 下计算均为**有限实数**；
4. 涉及物理参数的规则在 100 步纯 CPU 物理干跑中动能不爆炸、位置不穿模；
5. 整体沙盒验证耗时 ≤ 3 秒（超时即视为毒素）。

## 退出策略

任何一条 active 规则在后续回归测试中被检出违反上述条件，应被立即**回退**到 `knowledge/quarantine/` 并由人类 reviewer 通过 `knowledge-proposal/*` 分支的 Pull Request 决定是否重新晋升。**禁止自动化 Agent 直接在 `main` / `master` 上修改本目录。**

## 运行时读取路径

`mathart.distill.KnowledgeParser.parse_active_directory(knowledge_root)`
是运行时唯一授权入口。
