# SESSION-153 Handoff — 顶层 CLI 黄金连招 × 全局不死循环 × ComfyUI 防呆预警

> **"终端绝不死机，菜单永不消失，黄金连招一气呵成。" —— 本次升级把 Director Studio 调参、ComfyUI 出图、知识血统查账三道工序无损串成一条流水线，让创作者在同一个终端窗口里一路通关，不再经历参数重填与文档错位的折腾。**

**Date**: 2026-04-23
**Parent Commit**: `3a236d1` (SESSION-152 · 知识血统溯源审计)
**Task ID**: P0-SESSION-150-UX-DOCS-SYNC
**Status**: COMPLETE
**Smoke**: `python scripts/session153_smoke.py` → **5/5 PASS**（主菜单 [0] 清退、无效编号容错、ComfyUI 防呆预警、质量熔断红字、文档 100% 对齐）
**Regression**: `python tests/test_provenance_audit.py` → **9/9 PASS**（SESSION-152 审计链路完全未受影响）

---

## 1. Executive Summary

SESSION-153 聚焦**顶层 CLI 路由 UX** —— 既不碰 ComfyUI 渲染内核，也不动知识血统审计内核，只在 `mathart/cli_wizard.py` 这一条装配线上完成三件事：

1. **全局 `while True` 主控循环**：把原先"一次性"的 `_run_interactive` 重写为 `_run_interactive_shell`。每一次菜单迭代都被一层 `try/except` 护栏包住，质量熔断、非法编号、键盘中断、乃至任何未预料到的 `Exception` 都会被优雅吸收并 `continue` 回主菜单。用户唯一的体面退出口是 `[0] 🚪 退出系统`（EOF/Ctrl-C 也作 `rc=0` 处理）。
2. **Director Studio 黄金连招菜单**：预演 REPL 通过后不再 `return 0`，而是进入 `_golden_handoff_menu`，三连击 `[1] 🚀 趁热打铁`、`[2] 🔍 真理查账`、`[0] 🏠 暂存并退回主菜单`。`[1]` 把内存里刚批准的 `CreatorIntentSpec` 与 `Genotype.flat_params()` 直接注入 `ProductionStrategy` 的 `options.director_studio_spec / director_studio_flat_params`，彻底消除"刚调完参数又要重填一遍"的割裂感；`[2]` 则把同一份 `spec + knowledge_bus` 递给 `ProvenanceAuditBackend.execute(...)`，在终端打印 CJK 对齐的四列审计表并落盘 `logs/knowledge_audit_trace.json`。
3. **ComfyUI 防呆预警 + 文档物理级对齐**：`emit_comfyui_preflight_warning()` 在任何会呼叫 ComfyUI HTTP API 的动作（顶层 `[1] 工业量产` 与黄金连招 `[1]`）之前先亮出红底黄字横幅，提醒"另开终端起 `python main.py` 再回来"。横幅文案与三个连招菜单标签在 `docs/USER_GUIDE.md §5` 中 **一字不差** 镜像，README.md 同步突出"全局不死循环 + 黄金连招"两个卖点，彻底消灭幽灵文档。

---

## 2. 核心落地清单

| 文件 | 改动类型 | 要点 |
|---|---|---|
| `mathart/cli_wizard.py` | **重写** | 新增 `_run_interactive_shell`、`_golden_handoff_menu`、`emit_comfyui_preflight_warning`、`COMFYUI_PREFLIGHT_WARNING` 常量、三个 `GOLDEN_HANDOFF_*` 常量；向后兼容保留 `_run_interactive` 别名 |
| `mathart/workspace/mode_dispatcher.py` | 零改动 | 本次严格遵守"绝对隔离"红线 |
| `mathart/core/provenance_audit_backend.py` | 零改动 | 黄金连招 `[2]` 直接复用其 `execute(intent_spec=..., knowledge_bus=...)` 纯参数接口 |
| `mathart/backend/comfyui_render_backend.py` | 零改动 | 防呆预警只加在 UI 层，渲染内核未受污染 |
| `docs/USER_GUIDE.md` | 新增 §5 | 黄金连招三个选项、ComfyUI 防呆预警横幅完整复刻 |
| `README.md` | 补一句话 + 一段话 | 品牌命令说明强调"全局不死循环"；核心特性矩阵新增"黄金连招 (Golden Handoff)"子弹 |
| `scripts/session153_smoke.py` | **新增** | 5 项不依赖网络的本地验证断言 |
| `PROJECT_BRAIN.json` | 升级 | `v0.99.4 → v0.99.5`；pending_tasks 追加 `P0-SESSION-150-UX-DOCS-SYNC`，标记 CLOSED |

---

## 3. 架构纪律与红线

| 红线 | 本次如何守住 |
|---|---|
| **[绝对隔离]** 不碰底层业务 | `cli_wizard.py` 仅调用既有 `ModeDispatcher.dispatch(...)` 与 `ProvenanceAuditBackend.execute(...)`；没有修改任何 backend / strategy / gate 文件 |
| **[防假死]** 终端绝不闪退 | 每一层 `try/except` 都有兜底 `continue`，最外层还有一个 `outer_exc` 终极护栏 |
| **[防失忆]** 上下文无损传递 | 黄金连招 `[1]` 通过 `dispatch options["director_studio_spec"] / ["director_studio_flat_params"]` 把内存对象直接递给量产模块，不再走磁盘 |
| **[Docs-as-Code]** 文档零幽灵 | 预警横幅与三个黄金连招标签作为 **单一事实源常量** 从代码里导出，smoke 测试用 `in` 断言 Markdown 与代码完全对齐 |

---

## 4. 验收证据

### 4.1 smoke 测试 5/5 PASS

```text
[PASS] test_main_loop_exit                       — 清退 [0] 后 rc=0
[PASS] test_main_loop_invalid_choice_recovers    — 输 99 后菜单再次出现
[PASS] test_preflight_warning_emits_before_production  — 红字横幅完整可见
[PASS] test_quality_circuit_break_renders_red_notice   — RED ANSI + logs/mathart.log 指向
[PASS] test_docs_parity                          — 预警横幅 + 三个连招标签 + README 卖点齐备
============================================================
  Results: 5 passed, 0 failed
============================================================
```

### 4.2 回归保护：SESSION-152 审计链路 9/9 PASS

```text
[PASS] test_director_studio_integration: params=18, knowledge=4, fallback=14
============================================================
  Results: 9 passed, 0 failed, 0 skipped
============================================================
```

---

## 5. 傻瓜式验收步骤（本地终端三连招）

**下面这 3 步就是你本地"打开终端能不能跑通完整闭环"的唯一指标。照着念数字就行。**

### 第 0 步：一次性准备

```bash
cd MarioTrickster-MathArt
git pull                  # 拉到 SESSION-153 提交
pip install -e .          # 只需执行一次
mathart                   # 召唤顶层向导（等价于 mathart-wizard）
```

看见主菜单顶着 6 行选项（`[1] 🏭 工业量产` 到 `[5] 🎬 语义导演工坊` 加上 `[0] 🚪 退出系统`），说明已经进入全局不死循环。

### 第 1 步：走 `[5]` 导演工坊 → 感性创世 → 预演批准

1. 主菜单输入 `5` 回车。
2. 看到"请选择创作方式"时输入 `A` 回车（感性创世）。
3. 在"用自然语言描述你想要的风格"里随便输入比如：`活泼 弹性` 回车。
4. 进入白模预演后按一下 `1`（✅ 完美出图）回车，批准当前参数。

此时你**不会**被踢回主菜单，而是看到"🎬 导演工坊预演通过 — 黄金连招"三选项。

### 第 2 步：分别按 `2` → `1` → `0` 体验黄金连招

- 输入 `2` 回车：终端会立刻打印一张 CJK 对齐的"全链路知识血统溯源审计表"，底部会告诉你 verdict、硬编码死区条数与 `logs/knowledge_audit_trace.json` 的完整路径。这一步**不需要启动 ComfyUI**，纯旁路观测。
- 黄金连招菜单再次出现时输入 `1` 回车：终端会跳出黄底红字的 `[🚨 提示] 即将呼叫显卡渲染！请确保您的 ComfyUI 服务端已在后台启动并就绪。`——这就是防呆预警。如果本地 ComfyUI 没起，随便按个字符回车取消即可，不会假死。
- 最后输入 `0` 回车，系统把内存里的参数暂存后，把你稳稳送回主菜单。

### 第 3 步：输 `0` 回车退出

主菜单里输 `0` 回车，看到"已退出顶层向导。再见！"即为完整闭环通过。

> **💡 提示**：在黄金连招菜单或主菜单的任何位置乱输 `99`、`abc` 之类的非法值，都只会看到"[提示] 无法识别的选项"或重新弹出菜单，绝对不会闪退 —— 这就是全局不死循环。

---

## 6. 向后兼容性与已知事项

- `from mathart.cli_wizard import _run_interactive` 仍然可用，内部委托到 `_run_interactive_shell`，旧测试不受影响。
- `mathart --mode 5 --execute`（非 TTY 路径）完全沿用 argparse 分支，CI/自动化脚本语义不变。
- 沙盒里如果 `networkx` 未安装，旧 `scripts/session149_smoke.py` 会在 import 阶段报错，这是 SESSION-149 阶段已知依赖，与本次无关；本次 SESSION-153 smoke 不依赖 `networkx`。

---

## 7. 下一步接力建议（可选）

| 优先级 | 建议 |
|---|---|
| P1 | 把 `scripts/session153_smoke.py` 提升为 `tests/test_session153_ux_docs_sync.py`，纳入 CI 与 SESSION-149 boundary 测试同级收敛 |
| P2 | 给黄金连招 `[1]` 追加"渲染完成后自动跳 `[2]` 查账"的 auto-chain 选项，让连招可选四连招 |
| P2 | 把 `director_studio_spec` / `director_studio_flat_params` 的 options 键写进 `docs/USER_GUIDE.md` 的 Roadmap，提醒量产链路后续消费方同步接口契约 |

---

## 8. 会话锚点

| Session | 主题 | Parent Commit |
|---------|------|--------|
| SESSION-153 (当前) | 顶层 CLI 黄金连招 × 全局不死循环 × ComfyUI 防呆预警 × 文档对齐 | (this push) |
| SESSION-152 | 知识血统溯源审计 + 全链路参数贯通体检 | `3a236d1` |
| SESSION-151 | ComfyUI BFF 动态载荷变异 + 无头渲染后端 | `fd90026` |
| SESSION-150 | 纯数学驱动动画 + 增强优雅错误边界 | `ebd00bd` |
| SESSION-149 | 动态 demo 网格 + 优雅质量熔断边界 | `c2436e5` |
| SESSION-148 | Windows 终端编码崩溃护盾 + ASCII-safe 救援 UI | `ccc5067` |
| SESSION-147 | 知识总线大一统 + ComfyUI 交互式自愈救援网关 | `0f6da73` |

---

## 9. Handoff Checklist

- [x] 所有新代码遵循"绝对隔离"红线，未触碰底层渲染/审计/策略模块
- [x] 全局 `while True` 主控循环落地，`[0]` / EOF / Ctrl-C 均作优雅退出
- [x] 黄金连招菜单 `[1] / [2] / [0]` 全部走 in-memory 参数，不重新问路径
- [x] ComfyUI 防呆预警覆盖顶层 `[1]` 与黄金连招 `[1]`
- [x] `docs/USER_GUIDE.md §5` 与 `README.md` 已物理级对齐，smoke 测试持续守护
- [x] `PROJECT_BRAIN.json` 版本升级至 v0.99.5，`pending_tasks` 追加 `P0-SESSION-150-UX-DOCS-SYNC=CLOSED`
- [x] `SESSION_HANDOFF.md` 提供傻瓜式三步验收指引
- [x] 所有变更推送至 GitHub

*Signed off by Manus AI · SESSION-153*
