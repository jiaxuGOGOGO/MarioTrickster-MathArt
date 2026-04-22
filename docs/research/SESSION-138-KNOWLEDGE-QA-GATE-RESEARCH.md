# SESSION-138 研究摘要：四维知识质检防伪漏斗与隔离区架构

本文件记录 `P0-SESSION-135-KNOWLEDGE-QA-GATE` 在落地前对齐的外部工业界与学术界一手资料。所有引用均可溯源，本轮工程实现中任何关键约束都必须能回指到本文某一条研究结论，否则视为仓内自创，不得写入 `active/` 域。

## 1. RAG Citation Tracking 与证据链锚点（溯源）

Retrieval-Augmented Generation 在工业界已经被广泛视为降低 LLM 幻觉的主路线，但近两年的研究进一步指出：**仅仅“基于检索”并不能防幻觉，关键在于“每一条结论都必须能原文回指”**。关键结论：

- arXiv 2601.05866（*Mechanistic Detection of Citation Hallucination in Long-Form RAG*）明确把“citation hallucination”定义为“模型自信地给出了看似合理但并不由原文支撑的引文”，并强调长文本 RAG 系统必须在生成端增加对齐验证环节，而不是只在检索端把 Top-K 交给模型。
- AWS 官方文档《What is RAG》把 *source attribution* 列为 RAG 的第一等价值：“The output can include citations or references to sources”，这代表工业界对外宣称 RAG 可信赖的前提条件就是把引用作为硬契约。
- Medium / 《RAG Grounding: 11 Tests That Expose Fake Citations》（2026-03）则给出了 11 种反向测试，其中最重要的几项是“引文是否能命中原文段落”、“引文是否能通过字符串级子串回查”、“当原始 chunk 被修改后引文是否失效”。
- Substack 《Why Ghost References Still Haunt Us in 2025》记录了即便检索正确，生成阶段仍会“幻造页码、幻造作者、幻造年份”的故障模式。
- Medium 《RAG Citations Backfire When Chunks Keep Changing》强调引文必须锚定到 *stable anchor*（稳定锚点），推荐“chunk 版本号 + 原文精确片段”双保险。

**工程转化**：本项目蒸馏到的每一条 `KnowledgeRule`，必须强制携带

1. `source_quote`：原书/论文中可逐字校验的一段原文；
2. `page_number`：可选但强烈建议提供的页码/章节号锚点。

在反序列化阶段缺少 `source_quote` 的规则直接视为幻觉拒收，不得进入 quarantine，更不得进入 active。这一条红线直接来自 arXiv 2601.05866 与 Medium 11-tests 的共识。

## 2. AST 安全解析与基于属性的模糊测试

LLM 产出的“规则”本质上是**不可信字符串**，如果直接 `eval()`/`exec()` 会形成任意代码执行（RCE）。外部参考：

- Stack Overflow 经典问答 *Using ast and whitelists to make python's eval() safe* 给出了标准做法：先 `ast.parse`，再白名单化节点类型与函数名，最后才允许求值。
- Python 官方 `ast.literal_eval` 文档与 CPython issue #100305 同时警告：`literal_eval` 仅对字面量安全，对算术表达式或函数调用不保证。因此我们选择 **AST 白名单遍历 + 小型纯函数求值器**，而不是盲用 `literal_eval`。
- Two Six Technologies 《Hijacking the AST to safely handle untrusted python》描述了如何在 AST 层面拦截 `Call`、`Attribute`、`Import` 等节点，只放行 `BinOp`、`UnaryOp`、`Num/Constant`、白名单 `Name` 等，作为接受不可信数学表达式的通用模板。
- Anthropic Red 《Property-Based Testing with Claude》(2026-01) 进一步说明：对 LLM 输出的代码，property-based / fuzz 测试比示例测试更能发现边界崩溃，典型反例探测包括 0、负数、极大值、NaN、Inf 等。
- Dev.to 《Uncovering and Solving Data Wrangling Issues with Property-Based Testing》给出了具体的边界输入组合策略，作为本项目 `FUZZ_SAMPLES` 的依据。
- Andrew Healey 《Running Untrusted Python Code》强调必须加 **子进程 + 超时 + 资源限制**，否则不可信代码可以把验证进程本身拖死；这一条也促使本项目引入 `signal.SIGALRM` 或 `ThreadPoolExecutor` 超时拦截。

**工程转化**：

1. 所有来自规则 `constraint.expr` 的字符串只经过 `ast.parse(..., mode="eval")` → 白名单遍历 → 手写算子字典求值，禁止 `eval()`、`exec()`、`__import__`、属性访问、下划线名。
2. 对每条规则在 `FUZZ_SAMPLES = [0, -1, 1, 1e-6, 1e6, inf, -inf, nan]` 上进行数学 Fuzzing，任一值触发 `ZeroDivisionError`、`OverflowError`、`NaN`、`Inf` 即标记 FAILED。
3. 每次验证强制挂 3 秒超时；超时等价于 FAILED（毒素熔断）。

## 3. GitOps Quarantine 与保护分支 PR 流

自动化代理直接向 `main` 推送 LLM 生成的内容，是目前业界公认的高危反模式。对齐资料：

- Microsoft Learn 《Quarantine pattern》（Azure Architecture Center）给出了经典范式：**所有第三方/不可信制品必须先进入 quarantine store，由独立验证流程完成扫描与报告后，才能发布到 safe store 供消费者使用**。本项目 `knowledge/quarantine/` 与 `knowledge/active/` 直接对应这一范式：quarantine store = 隔离输入，safe store = 生产可用，验证报告 = sandbox_validator 输出。
- GitHub Docs 《About protected branches》、GitOps Security Champion 《Branch Protection》、OneUptime 《GitOps Pull Request Review Workflow》一致指出：main/master 必须启用 “require pull request + 1 reviewer + status check” 的保护策略，禁止任何自动化直接 push。
- Fensak Blog 《GitOps Supply Chains: Solving the Branch Protection dilemma》把“机器人写代码 → 机器人直接合并”列为链路漏洞，并推荐“机器人只能向独立的 proposal/xxx 分支推送，由人类/另一 reviewer 合入 main”。
- GitHub Marketplace 《Naming Conventions Bot》与 nautobot branching 讨论给出了工业界常用的分支命名前缀约定，本项目沿用 `knowledge-proposal/distill-<UTC-timestamp>` 作为提议分支前缀。

**工程转化**：

1. `GitAgent.sync_knowledge()` 引入 `proposal_branch: bool = True` 默认开启；在该模式下自动从 HEAD 拉出 `knowledge-proposal/distill-YYYYMMDDHHMMSS` 检疫分支并 push。
2. 新增 `PROTECTED_BRANCHES = {"main", "master"}` 常量；一旦探测到当前或目标分支属于保护集，即便调用方显式请求也拒绝推送，原因字符串以 `manual_action_required=True` 回传。
3. 只有从沙盒取得 100% PASSED 报告的规则才允许触发 proposal push；缺证据链、毒素或超时规则立即终止流水线。

## 4. 端到端毒素熔断断言

对齐 Anthropic Red 《Property-Based Testing with Claude》与 Andrew Healey 《Running Untrusted Python Code》，本轮必须把三类典型毒素作为回归测试永久锁死：

| 毒素类型 | 样本 | 期望行为 |
|---|---|---|
| 幻觉证据链 | 缺失 `source_quote` 的规则 | 反序列化即抛 `QuarantineContractError`，不得进入 quarantine |
| 数学毒素 | `"x / 0"`、`"np.log(-1)"`、`"1e308 * 1e308"` | Sandbox 精准捕获 `ZeroDivisionError` / NaN / Inf，标记 FAILED |
| 安全注入 | `"__import__('os').system('rm -rf /')"`、属性访问 `(1).__class__` | AST 白名单拦截，`UnsafeExpressionError`，从不执行 |

**且在上述任一情况下，必须断言 `git_agent.sync_knowledge()` 不被触发，即 proposal 分支绝不产生**。这保证“真理沙盒”在测试级别就被锁死坚不可摧。

## 5. 与 `AssetPipeline` 的边界纪律

本项目强调**不得越权修改 `AssetPipeline` 主干**。外部参考 Clean Architecture / Ports-and-Adapters 的通行做法：验证逻辑属于“蒸馏外循环”的前置网关（pre-merge gate），应聚合在 `mathart/distill/` 下的独立模块里，通过明确的契约与主干通信。这与本仓库既有的 `registry` / `UnifiedMotionBackend` / `MicrokernelOrchestrator` IoC 取向完全一致，因此本轮新增的 `sandbox_validator.py` 被严格限制在 `mathart/distill/` 目录内，`AssetPipeline` 代码零改动。

---

## 研究条目索引（可直接点击复核）

1. arXiv 2601.05866 — *Mechanistic Detection of Citation Hallucination in Long-Form RAG*
2. AWS — *What is RAG? Retrieval-Augmented Generation*
3. Medium @Nexumo — *RAG Grounding: 11 Tests That Expose Fake Citations* (2026-03-13)
4. Substack aarontay — *Why Ghost References Still Haunt Us in 2025* (2025-12-22)
5. Medium @npavfan2facts — *RAG Citations Backfire When Chunks Keep Changing*
6. Stack Overflow — *Using ast and whitelists to make python's eval() safe*
7. CPython Issue #100305 — *ast.literal_eval is still referred to as safe by the docs*
8. Two Six Technologies — *Hijacking the AST to safely handle untrusted python* (2022-12-19)
9. Anthropic Red — *Property-Based Testing with Claude* (2026-01-14)
10. Dev.to @rfmf — *Uncovering and Solving Data Wrangling Issues with Property-Based Testing*
11. Andrew Healey — *Running Untrusted Python Code* (2023-07-27)
12. Microsoft Learn — *Quarantine pattern (Azure Architecture Center)*
13. GitHub Docs — *About protected branches*
14. GitOps Security Champion — *Branch Protection*
15. OneUptime — *How to Implement GitOps Pull Request Review Workflow* (2026-03-13)
16. Fensak Blog — *GitOps Supply Chains: Solving the Branch Protection dilemma* (2023-10-18)
17. GitHub Marketplace — *Naming Conventions Bot*
