# SESSION-143 · 文档↔代码契约反向审计报告

> **审计原则**：以 Python 代码的真实运行逻辑为唯一真理，文档必须单向贴合代码。
> **审计范围**：`README.md`、`docs/USER_GUIDE.md`、`docs/TROUBLESHOOTING.md`
> **审计日期**：2026-04-22
> **审计者**：Manus AI（充当无情的编译器）

---

## 一、已抓到的幻觉（Hallucinations）清单

| # | 严重级 | 文档位置 | 文档声称 | 代码真相 | 处置 |
|---|---|---|---|---|---|
| H1 | 🚫 FATAL | `README.md:32`、`USER_GUIDE.md:9` | 运行 `mathart-evolve` 会弹出 5 模式交互菜单 | `mathart-evolve` 映射到 `mathart.evolution.cli:main`，是纯 argparse 子命令 CLI，无交互菜单；菜单由 `mathart.cli:main` → `run_wizard()` 提供，但该入口未在 `pyproject.toml` 注册 | **修代码+改文档**：注册 `mathart` / `mathart-wizard` console script；文档统一改用 `mathart` |
| H2 | 🚫 FATAL | `cli_wizard.py:330/335/363` | 运行时抛 `SyntaxError: unterminated string literal`（SESSION-140 遗留字面换行 bug） | 三处 `output_fn("` 后跟物理换行，Python 无法解析 | **改代码**：替换为 `\n` 转义，保持原语义 |
| H3 | ⚠️ PHYSICAL | `USER_GUIDE.md:43` | `reference_image:` 字段被 `IP-Adapter 消费` | `director_intent.py` 解析器**不读取** `reference_image` 字段（无任何 `raw.get("reference_image")`） | **改文档**：删去 `reference_image` 段落或明确标注"未来路线图字段，当前解析器忽略" |
| H4 | ⚠️ PHYSICAL | `README.md:45` | "最低 6GB VRAM 保护" | `preflight_radar.py:906` 默认 `minimum_gpu_vram_mb = 6144` ≈ 6 GiB（MiB 而非 GB，文字 OK，但应补精确表达） | **改文档**：改为"最低 6144 MiB (≈6 GiB) VRAM 保护" |
| H5 | ⚠️ PHYSICAL | `TROUBLESHOOTING.md:34` | 环境变量 `MATHART_COMFYUI_WS_TIMEOUT` | 代码中真实变量名是 `MATHART_COMFYUI_WS_TIMEOUT`（`settings.py:136`）✅ 正确 | 无需改动 |
| H6 | ⚠️ PHYSICAL | `TROUBLESHOOTING.md:50-52` | `mathart-evolve --purge-cache` 可一键深度清理 | 代码中**不存在** `--purge-cache` argparse 参数；真实 GC 逻辑由 `GarbageCollector(project_root).sweep()` 在**每次 CLI 启动时自动冷扫**（`evolution/cli.py:33` + `cli_wizard`），手动清理需 `python -c "from mathart.workspace.garbage_collector import GarbageCollector; GarbageCollector('.').sweep()"` | **改文档**：去掉 `--purge-cache` 虚构参数，改为说明"每次启动自动冷扫"+"如需强制清扫可使用单行 Python" |
| H7 | ⚠️ PHYSICAL | `TROUBLESHOOTING.md:12` | "日志文件默认保留 7 天" | 代码中真实是 `TimedRotatingFileHandler(when="midnight", backupCount=7)`（`logger.py:194` + `BlackboxConfig.backup_count=7`）—— 保留 7 份日报而非"7 天"（每日午夜轮转，保留最近 7 份）。语义接近但口径不精确 | **改文档**：改为"每日午夜轮转，保留最近 7 份（即约 7 天）" |
| H8 | ⚠️ PHYSICAL | `README.md:6` | `Version 0.55.0` 徽章 | `pyproject.toml:7` 真实版本 `0.46.0` | **改文档**：同步徽章到 0.46.0 |
| H9 | ℹ️ INFO | `TROUBLESHOOTING.md:31` | ComfyUI 默认超时 600 秒 | `settings.py:138` `comfyui_ws_timeout: float = 600.0` ✅ 正确 | 无需改动 |
| H10 | ℹ️ INFO | `TROUBLESHOOTING.md:29` | 网络超时默认 60 秒 | `settings.py:82` `network_timeout: float = 60.0` ✅ 正确 | 无需改动 |
| H11 | ⚠️ PHYSICAL | `USER_GUIDE.md:74-78` | 基因家族列出 `physics / proportions / animation / palette` | `blueprint_evolution.py:58` `GENE_FAMILIES = {"physics", "proportions", "animation", "palette"}` ✅ 正确 | 无需改动 |
| H12 | ℹ️ INFO | `USER_GUIDE.md:89-96` | REPL 选项文案 `[1] 批准 / [2]+再夸张 / [3]-收敛 / [4]放弃` | `interactive_gate.py:540-543` 实际文案是 `[1] ✅ 完美出图 / [2] [+] 再夸张点 / [3] [-] 收敛点 / [4] ❌ 退出` | **改文档**：对齐到代码实际文案 |
| H13 | ⚠️ PHYSICAL | `USER_GUIDE.md:101` | PHYSICAL 违规可"强制覆盖 Override" | `interactive_gate.py:460-461` 代码文案是 `[1] 遵从科学 / [2] 人类意图覆盖`（选 `2` 才覆盖）。文档描述方向正确但未提到菜单编号口径 | **改文档**：补足真实提示文案 |

---

## 二、结论

- **FATAL 级 2 条**（H1 H2）——同时打破代码与文档一致性（H2 甚至让 `cli_wizard` 无法被 import）。
- **PHYSICAL 级 6 条**（H3 H4 H6 H7 H8 H11/H12/H13 中的 H12/H13 并列），需单向以代码为准更新文档。
- **INFO 级**：env var 与超时默认值全部已与代码一致。

统一处置策略：**代码能对齐的对齐，代码没有的删除，代码命名不同的以代码为准**，绝不反向让代码迁就文档幻觉（除 H1 为品牌级入口确实值得补代码）。

---

*Signed off by Manus AI*
