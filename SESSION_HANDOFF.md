# SESSION-147 HANDOFF — KNOWLEDGE-BUS THROUGH-WIRING & COMFYUI INTERACTIVE PATH RESCUE

> **打通“知识大一统”最后一公里：DirectorIntent 脚裂修复 + 雷达拦截变友好回收网关，.env 持久化 + 运行时热注入 + 雷达重唤**

**Date**: 2026-04-22
**Status**: COMPLETE
**Parent Commit**: `b9cdf05` (SESSION-146-B)
**Tests**: 24/24 SESSION-147 targeted (bus factory + rescue gateway) + 144/144 cross-suite regression (wizard / director / dispatcher / preflight-radar / 146-B audit / 146 telemetry / knowledge-synergy / idempotent-surgeon / hitl-boundary) = **168/168 PASS, 0 FAIL**

---

## 1. Problem Statement (SESSION-146 黑匣子抒出的两大最后一公里漏洞)

### 1.1 架构遗漏——知识总线脚裂 (Knowledge Bus Brain-Split)

SESSION-146 贯通后的黑匣子日志首次暴露：

```
DEBUG | mathart.workspace.director_intent | No knowledge bus injected — using heuristic fallback only
```

在之前的 *知识大一统* 任务中，`DirectorIntentParser` 已支持外注 `knowledge_bus`，但顶层向导入口 (`mathart/cli_wizard.py::_run_director_studio` 与非交互的 `mode_dispatcher.py::DirectorStudioStrategy.execute`) **漏掉了实例化并注入 `RuntimeDistillationBus`**。结果以下这套已数事上线的 18 模块 / 323 参数的知识总线对 Director Studio 全程物理断连，解析器退化到 heuristic fallback 。

### 1.2 UX 体验断层——雷达过度防御与死板阻断

SESSION-146-B 雷达道射到 **46 条路径**仍未命中时，能在黑匣子留下完整审计，但 `ProductionStrategy.execute` 直接用 `comfyui_not_found` 阻断信号抛出并退出程序。用户明知本机已装 ComfyUI（只是放在非常规盘符），却被向导一脚踢出，产品观感极差且没有救援航道。

---

## 2. Key Deliverables

### 2.1 修复一：知识总线工厂 + 双入口注入

| 文件 | 变更 |
|------|------|
| `mathart/workspace/knowledge_bus_factory.py` (新增) | 提供 `build_project_knowledge_bus()` 单一入口，从项目 `knowledge/` 目录编译 `RuntimeDistillationBus`；没有 `knowledge/` 时返回 `None` 或空 bus，绝不报错。 |
| `mathart/cli_wizard.py::_run_director_studio` | 在 parser 实例化前调用 `build_project_knowledge_bus()` 并作为 `knowledge_bus=` 注入 `DirectorIntentParser`。 |
| `mathart/workspace/mode_dispatcher.py::DirectorStudioStrategy.execute` | 非交互通道同步研産，保证 --mode 5 依然物理可观测知识总线。 |
| `mathart/workspace/__init__.py` | 导出 `build_project_knowledge_bus` 以供下游代码复用。 |

**证据**：运行 `scripts/session147_smoke.py` 即可观测到 `modules compiled : 18 / parameters total : 323`，且日志流中 `No knowledge bus injected` 警告彻底消失。

### 2.2 修复二：ComfyUI 交互式路径自愈网关

| 文件 | 变更 |
|------|------|
| `mathart/workspace/comfyui_rescue.py` (新增) | 实现 `prompt_comfyui_path_rescue()`、`_clean_pasted_path()`、`_looks_like_comfyui_root()`、`persist_comfyui_home()`、`hot_inject_env()`、`is_comfyui_not_found_payload()`。 |
| `mathart/workspace/mode_dispatcher.py::ProductionStrategy` | `__init__` 新增 `input_fn` / `output_fn` hooks；`execute()` 在雷达返回 `comfyui_not_found` 时挂起并调用 `prompt_comfyui_path_rescue`，成功后重新调用 `radar.scan()` 重唤放行。 |
| `mathart/workspace/mode_dispatcher.py::ModeDispatcher` | 构造函数接收 `input_fn` / `output_fn` 并透传给 `ProductionStrategy`。 |
| `mathart/cli_wizard.py::_run_interactive` | 实例化 `ModeDispatcher` 时透传向导的 REPL 通道，确保提示用与向导同片终端。 |
| `pyproject.toml` | 核心依赖新增 `python-dotenv>=1.0`（`set_key` 的墤馬；未安装时降级到纯 Python 追写补丁）。 |

**现场行为**：当雷达给出 `comfyui_not_found` 且运行在交互模式时，用户会在同一个终端看到：

```
🚨 雷达未能自动定位到 ComfyUI 引擎。
如果您已安装，请直接将 ComfyUI 的根目录（包含 main.py 的文件夹）拖拽到此处并回车。
或直接按回车退回沙盒模式。
```

用户输入（带引号的拖拽路径亦可被辷边）→ `_clean_pasted_path` 去除 `"'` 和空白 → `_looks_like_comfyui_root` 检查 `main.py` + `custom_nodes/` → `persist_comfyui_home` 向 `.env` 写入 `COMFYUI_HOME="..."` (通过 `dotenv.set_key` 或比较自实现的 key-upsert) → `hot_inject_env` 把该值热注入 `os.environ` → 打印 `✅ 引擎绑定成功并永久保存！` → 重新 `PreflightRadar().scan()` → 重新进入量产渲染。

### 2.3 测试矩阵

| Suite | 规模 | 状态 |
|-------|------|------|
| `tests/test_session147_rescue_and_bus.py` (新增) | 24 | PASS |
| `tests/test_dual_wizard_dispatcher.py` | 4 | PASS |
| `tests/test_director_studio_blueprint.py` | 23 | PASS |
| `tests/test_session146_telemetry.py` | 11 | PASS |
| `tests/test_session146b_radar_enhancement.py` | 15 | PASS |
| `tests/test_preflight_radar.py` | 17 | PASS |
| `tests/test_knowledge_synergy_bridge.py` | 26 | PASS |
| `tests/test_idempotent_surgeon.py` | 22 | PASS |
| `tests/test_hitl_boundary_gateway.py` | 2 | PASS |
| `scripts/session147_smoke.py` E2E | 1 | GREEN |

### 2.4 如何现场确认修复生效

**知识总线接通**：执行 `PYTHONPATH=. python3 scripts/session147_smoke.py`；
- `[BUS] modules compiled : 18` + `[BUS] parameters total : 323` 即证明 `knowledge/` 目录已被全量装载；
- `[PARSER] knowledge_bus attached: True` 证明 `DirectorIntentParser` 持有 bus。
- `[LOG] brainsplit warning present? False` 证明 `logs/mathart.log` 不再写入 `No knowledge bus injected — using heuristic fallback only`。

**交互式路径救援现场表现**：执行同一脚本的 Part 2，可观测到
- `[RADAR] detected comfyui_not_found blocker` → 证明截获器命中；
- `[RESCUE] resolved = True` + `path = /tmp/.../ComfyUI` → 证明引号被剥离且路径校验通过；
- `[.env] contents = "COMFYUI_HOME='...'\n"` → 证明 `.env` 持久化完成；
- `[os.environ] COMFYUI_HOME=...` → 证明热注入完成；
- `[RADAR/REWAKE] found=True, root=...` → 证明雷达重唤后成功识别引擎，量产渲染可继续。

---

## 3. SESSION-147 四条硬多疆界（给下一任期的工程节点）

1. `comfyui_rescue.py` 依然是 **UX 网关**，不要在模块内反向调用 parser / gate 等上层 orchestrator；任何 "auto-detect" 、网络抓论均应在 `preflight_radar.py` 内完成。
2. `hot_inject_env()` 仅针对当前进程；任何跨会话的 `COMFYUI_HOME` 维护绝对依赖 `.env`，不允许写 shell 配置。
3. `persist_comfyui_home` 面对拿 dotenv 不到的环境时尾随附加，请保留该降级路径，以充当离线者的救生门。
4. 新增测试文件 `tests/test_session147_rescue_and_bus.py` 的 autouse fixture `_isolate_comfyui_env` 是 **存活红线**，不得被减弱或替换为 `monkeypatch.delenv`——它才是露出 SESSION-146 雷达回归底线的原因。

---

## 4. 待办列表 (TODO Reconciliation)

| 项 | 状态 |
|----|------|
| 知识总线在 cli_wizard Director Studio 入口被注入 | ✅ DONE (SESSION-147) |
| 知识总线在非交互 ModeDispatcher 被注入 | ✅ DONE (SESSION-147) |
| 雷达 `comfyui_not_found` 阻断变交互式路径回收 | ✅ DONE (SESSION-147) |
| `.env` 持久化 `COMFYUI_HOME` | ✅ DONE (SESSION-147) |
| `os.environ` 热注入 + 雷达重唤 | ✅ DONE (SESSION-147) |
| `python-dotenv` 追加核心依赖 | ✅ DONE (SESSION-147) |
| 未来：`comfyui_rescue` 支持 Windows 缩略名路径 / UNC 路径预校验 | ⬜ TBD |
| 未来：`knowledge_bus_factory` 开放缓存层以避免每次 wizard 启动重复编译 | ⬜ TBD |

---

# SESSION-146 HANDOFF — FULL-CHAIN TELEMETRY THROUGH-BORE, DEPENDENCY HARDENING & RADAR WIDE-AREA SEARCH NET

> **全链路遥测贯通 + 依赖固化 + 双轨日志分流护栏 + 雷达广域探测网强化：黑匣子从此对核心业务轨迹零失明，ComfyUI 探测再无死角**

**Date**: 2026-04-22
**Status**: COMPLETE
**Parent Commit**: `3e2792d` (SESSION-145)
**Tests**: 11/11 SESSION-146 targeted + 15/15 SESSION-146-B radar + 17/17 radar regression + 27/27 system-purge + 4/4 wizard + 27/27 director = **101/101 PASS, 0 FAIL**

---

## 1. Problem Statement (现场硬证据)

SESSION-145 端到端环境探测测试暴露了系统在"底层依赖完整性"与"遥测黑匣子漏水"上的两大最后一公里漏洞：

1. **轻量依赖未显式注册**：
   - 模式 5 报错：`No module named 'matplotlib'`（已在 SESSION-144 修复）。
   - 模式 1 雷达 JSON 暴露出：`"psutil not importable — process scan skipped"`。
   - psutil 是 PreflightRadar 进程表反查（ComfyUI sniffer）的运行时依赖，但从未出现在 `pyproject.toml` 核心依赖清单中。

2. **极其致命的黑匣子业务失明 (Telemetry Gap)**：
   - 无论是模式 5 中极其重要的白模拦截报错，还是模式 1 中长达几十行的防撞雷达 JSON 诊断报告，**全部仅仅被打印在了终端（stdout），根本没有被写入 `logs/mathart.log`**。
   - 黑匣子仅有 GC 启动等生命周期记录，对核心业务轨迹和阻断快照完全失明。

3. **ComfyUI 广域探测盲区 (Search Net Gap)**（追加痛点）：
   - 宿主机明确安装了 ComfyUI，但雷达报出 `comfyui_not_found` 且 `candidate_roots: []`。
   - 静态搜索网仅覆盖 14 条路径，缺少 Windows Portable 版本、多盘符 AI 目录、macOS 路径、以及相对工作区的祖先目录探测。
   - 更致命的是：雷达未在黑匣子中留下"究竟去哪些路径找过"的搜查审计轨迹，导致排障无门。

---

## 2. Key Deliverables

### 2.1 修复一：依赖体系显式补齐与惰性加载捍卫

| 文件 | 变更 |
|------|------|
| `pyproject.toml` | 新增 `psutil>=5.9` 到核心依赖 |
| `pyproject.toml` | 新增 `[project.optional-dependencies].gpu` 分组，声明 `torch>=2.0`（重型 AI 运行时隔离） |

- **惰性加载纪律审计**：全仓库零顶级 `import matplotlib / torch / psutil` 违规。
- **torch 隔离策略**：torch 是多 GB 重型 AI 运行时，绝不可出现在默认依赖中。用户需要 GPU SDF 评估或神经渲染时通过 `pip install mathart[gpu]` 显式安装。

### 2.2 修复二：全链路业务日志漏水点贯通

| 模块 | 埋点类型 | 日志级别 | 内容 |
|------|---------|---------|------|
| `mode_dispatcher.py` ProductionStrategy | 雷达诊断 payload 全量落盘 | `INFO` | `Radar diagnostic payload (verdict=...): {完整JSON}` |
| `mode_dispatcher.py` ProductionStrategy | 生产模式拦截警告 | `WARNING` | `Production mode BLOCKED by radar — verdict=..., blocking_actions=[...]` |
| `mode_dispatcher.py` ModeDispatcher.dispatch | 向导模式选择埋点 | `INFO` | `[CLI] User selected mode: ... (strategy=..., execute=...)` |
| `launcher_facade.py` _abort_manual | 中止诊断全量落盘 | `WARNING` | `LauncherFacade ABORTED — reason=..., blocking=..., preflight_report={JSON}` |
| `launcher_facade.py` supervisor crash | 崩溃堆栈入库 | `WARNING` | `LauncherFacade supervisor CRASHED: ...` + `exc_info=True` |
| `interactive_gate.py` proxy render | 白模成功记录 | `INFO` | `Proxy rendered successfully: round=..., path=...` |
| `interactive_gate.py` proxy render | 白模失败 + 完整堆栈 | `WARNING` | `Proxy render FAILED at round ... — degraded to parameter view` + `exc_info=True` |
| `interactive_gate.py` Truth Gateway | 致命约束自动裁剪 | `WARNING` | `Truth Gateway FATAL block — N violations auto-clamped: [...]` |
| `interactive_gate.py` Truth Gateway | 用户覆盖知识约束 | `WARNING` | `Truth Gateway: user OVERRODE knowledge constraints for: [...]` |
| `interactive_gate.py` Truth Gateway | 用户遵从知识裁剪 | `INFO` | `Truth Gateway: user COMPLIED with knowledge clamp for: [...]` |
| `cli_wizard.py` run_wizard | 向导启动 | `INFO` | `[CLI] Wizard invoked: argv=..., interactive=...` |
| `cli_wizard.py` _run_interactive | 交互模式选择 | `INFO` | `[CLI] Interactive mode selection: ...` |
| `cli_wizard.py` _run_interactive | 分发失败 + 堆栈 | `WARNING` | `[CLI] Interactive dispatch FAILED for selection=...` + `exc_info=True` |
| `cli_wizard.py` non-interactive | 非交互分发失败 | `WARNING` | `[CLI] Non-interactive dispatch FAILED for mode=...` + `exc_info=True` |
| `cli_wizard.py` Director Studio | 创作模式选择 | `INFO` | `[CLI] Director Studio creation mode: ...` |
| `cli_wizard.py` Director Studio | 意图解析成功 | `INFO` | `[CLI] Director intent parsed: vibe=..., evolve_variants=...` |
| `cli_wizard.py` Director Studio | 意图解析失败 | `WARNING` | `[CLI] Director intent parse FAILED` + `exc_info=True` |
| `cli_wizard.py` Director Studio | 门控决策 | `INFO` | `[CLI] Director gate decision: ... (rounds=...)` |
| `cli_wizard.py` Director Studio | 流程完成 | `INFO` | `[CLI] Director Studio workflow completed successfully` |

### 2.3 修复三：双轨日志分流护栏 (Log Multiplexing Guard)

| 组件 | 变更 |
|------|------|
| `cli.py` `_configure_logging` | 移除 `logging.basicConfig(force=True)` 防止覆盖黑匣子文件 handler；改为先调用 `install_blackbox()` 再仅调整 root logger level |
| `cli_wizard.py` `run_wizard` | 入口处主动调用 `install_blackbox()` 确保向导路径的日志也落盘 |
| `core/logger.py` | Console handler 级别死守 `WARNING`（已有，通过 `MATHART_LOG_CONSOLE_LEVEL` 环境变量配置） |

**效果**：
- **文件 handler** (`logs/mathart.log`)：`DEBUG` 级别，捕获全量业务遥测。
- **终端 handler** (stderr)：`WARNING` 级别，仅显示告警和错误，不冲刷向导菜单。

### 2.4 修复四：ComfyUI 广域探测网强化 (SESSION-146-B)

#### 2.4.1 扩容静态嗅探候选网 (Expand Candidate Roots)

`_DEFAULT_CANDIDATE_PARENTS` 从 **14 条** 扩容至 **42 条**，覆盖以下全部部署模式：

| 部署模式 | 新增候选路径示例 |
|---------|----------------|
| **POSIX 用户本地** | `~/Desktop/ComfyUI`, `~/Downloads/ComfyUI`, `~/git/ComfyUI`, `~/src/ComfyUI`, `~/workspace/ComfyUI` |
| **Windows Portable 发行版** | `~/ComfyUI_windows_portable/ComfyUI`, `~/Desktop/ComfyUI_windows_portable/ComfyUI`, `~/Downloads/ComfyUI_windows_portable/ComfyUI` |
| **Windows 多盘符** | `C:/Program Files/ComfyUI`, `C:/ComfyUI_windows_portable/ComfyUI`, `D:/Tools/ComfyUI`, `D:/ComfyUI_windows_portable/ComfyUI`, `E:/ComfyUI_windows_portable/ComfyUI`, `F:/ComfyUI_windows_portable/ComfyUI`, `G:/ComfyUI`, `G:/AI/ComfyUI`, `H:/ComfyUI`, `H:/AI/ComfyUI` |
| **macOS 常规** | `/Applications/ComfyUI`, `~/Library/Application Support/ComfyUI` |

相对工作区探测也从 3 条扩容至 8 条：

| 相对路径 | 说明 |
|---------|------|
| `./ComfyUI`, `./comfyui` | 当前目录子目录 |
| `../ComfyUI`, `../comfyui` | 父目录同级 |
| `../ComfyUI_windows_portable/ComfyUI` | 父目录 Portable 版本 |
| `../../ComfyUI`, `../../comfyui` | 祖父目录同级 |
| `../../ComfyUI_windows_portable/ComfyUI` | 祖父目录 Portable 版本 |

#### 2.4.2 侦察动作底层留痕 (Search Audit Trail Logging)

| 函数 | 日志标签 | 级别 | 内容 |
|------|---------|------|------|
| `_scan_filesystem_for_comfyui` | `[Radar/FS]` | `DEBUG` | COMFYUI_HOME 环境变量状态 |
| `_scan_filesystem_for_comfyui` | `[Radar/FS]` | `DEBUG` | 静态候选列表大小 |
| `_scan_filesystem_for_comfyui` | `[Radar/FS]` | `DEBUG` | 当前工作目录 |
| `_scan_filesystem_for_comfyui` | `[Radar/FS]` | `DEBUG` | 每条路径的 PROBE 结果 (HIT/miss) |
| `_scan_filesystem_for_comfyui` | `[Radar/FS]` | `DEBUG` | 扫描摘要（N 条去重路径探测，M 条命中） |
| `_scan_processes_for_comfyui` | `[Radar/PS]` | `DEBUG` | psutil 模块可用性 |
| `_scan_processes_for_comfyui` | `[Radar/PS]` | `DEBUG` | 进程表遍历启动 |
| `_scan_processes_for_comfyui` | `[Radar/PS]` | `DEBUG` | 每个 ComfyUI 候选进程的 pid/name/cmdline/cwd |
| `_scan_processes_for_comfyui` | `[Radar/PS]` | `DEBUG` | main.py 参数 / cwd 回退候选的验证结果 |
| `_scan_processes_for_comfyui` | `[Radar/PS]` | `DEBUG` | 扫描摘要（N 扫描/M python/K 候选/L 命中） |
| `_discover_comfyui` | `[Radar]` | `INFO` | 探测启动 |
| `_discover_comfyui` | `[Radar]` | `INFO` | 发现 ComfyUI（进程扫描/文件系统启发式） |
| `_discover_comfyui` | `[Radar]` | `WARNING` | ComfyUI 未找到（含已探测候选数量） |

**核心断言**：如果最终仍判定 `comfyui_not_found`，`logs/mathart.log` 中将留存一份极其详尽的"雷达曾尝试过哪些具体物理路径、扫过哪些进程表"的探查审计流水账。所有 `[Radar/FS]` 和 `[Radar/PS]` 标签的 DEBUG 日志仅落入文件，终端保持极度清爽。

#### 2.4.3 雷达版本升级

`radar_version` 从 `1.0.0` 升至 `1.1.0`。

---

## 3. Test Evidence

```
tests/test_session146_telemetry.py              — 11/11 PASS
tests/test_session146b_radar_enhancement.py      — 15/15 PASS
tests/test_preflight_radar.py                    — 17/17 PASS
tests/test_system_purge_observability.py         — 27/27 PASS
tests/test_dual_wizard_dispatcher.py             —  4/4  PASS
tests/test_director_studio_blueprint.py          — 27/27 PASS
──────────────────────────────────────────────────────────
TOTAL                                            — 101/101 PASS, 0 FAIL
```

SESSION-146-B 专项测试覆盖 5 大类 15 项：

| 测试类 | 测试项 | 验证内容 |
|--------|-------|---------|
| `TestCandidatePathCoverage` | 5 项 | Windows Portable 路径、macOS 路径、多盘符覆盖、相对 cwd 探测、候选总数 >= 30 |
| `TestFilesystemAuditTrail` | 4 项 | DEBUG 日志逐路径探测、COMFYUI_HOME 有/无记录、HIT 路径记录 |
| `TestProcessScanAuditTrail` | 3 项 | 进程扫描摘要、psutil=None 记录、候选进程详情记录 |
| `TestDiscoveryAuditTrail` | 3 项 | 未找到 WARNING、找到 INFO、探测启动记录 |

---

## 4. Architecture Discipline Enforced

- **依赖即契约 (Dependencies as Contract)**：所有运行时必然 `import` 的第三方库都必须显式列入 `pyproject.toml`，重型可选依赖隔离到 `[project.optional-dependencies]`。
- **遥测即审计 (Telemetry as Audit Trail)**：每一个业务阻断点、降级拦截、用户决策都必须在黑匣子中留存完整的 JSON 诊断证据与异常堆栈。
- **双轨分流 (Log Multiplexing)**：文件 handler 全量捕获 DEBUG+，终端 handler 死守 WARNING+，两者互不干扰。
- **惰性加载纪律 (Lazy Import Discipline)**：重型依赖（torch, matplotlib, psutil）禁止出现在模块顶级作用域。
- **广域探测纪律 (Wide-Area Search Discipline)**：雷达的静态候选网必须覆盖所有主流 OS 的常见部署模式，每次探测必须在黑匣子中留下逐路径的审计流水账。

---

## 5. Files Modified

| File | Change Type |
|------|-------------|
| `pyproject.toml` | 新增 psutil 核心依赖 + gpu optional-dependencies |
| `mathart/workspace/preflight_radar.py` | 候选路径 14→42 条 + 进程/文件系统审计轨迹 + radar_version 1.1.0 |
| `mathart/workspace/mode_dispatcher.py` | 新增 logger + 雷达 payload 落盘 + 拦截警告 + 模式选择埋点 |
| `mathart/workspace/launcher_facade.py` | 新增中止诊断落盘 + 崩溃堆栈入库 |
| `mathart/quality/interactive_gate.py` | 白模失败 exc_info + 真理网关警告/裁剪/覆盖入库 |
| `mathart/cli_wizard.py` | 新增 logger + install_blackbox + 全链路向导轨迹埋点 |
| `mathart/cli.py` | 移除 force=True + 改用 install_blackbox + root level 调整 |
| `tests/test_session146_telemetry.py` | 新增 11 项端到端遥测验证测试 |
| `tests/test_session146b_radar_enhancement.py` | 新增 15 项雷达广域探测网验证测试 |
| `tests/test_preflight_radar.py` | radar_version 断言更新为 1.1.0 |

---

## 6. Coronation & Next Steps

**Coronation**:
> "黑匣子从此对核心业务轨迹零失明，雷达探测网从此覆盖全平台全部署模式。无论是雷达拦截、白模崩溃、真理网关裁剪，还是用户的每一次模式选择，都将被完整烙印在 `logs/mathart.log` 中，永不丢失。即使 ComfyUI 最终仍未找到，黑匣子中也将留存一份逐路径、逐进程的完整探查审计流水账，为人工排障提供绝对的硬证据。"

**Next Steps**:
- P1：`tests/test_install_contract.py` — 扫描仓库内所有 `import` 语句与 `pyproject.toml` 声明依赖做自动化契约比对。
- P2：为 `ProxyRenderer` 增加 `matplotlib` 缺失时的 ASCII-art 回退渲染器。
- P2：为黑匣子增加结构化 JSON Lines 输出格式，便于 ELK/Loki 等日志平台接入。
- P2：雷达增加 Windows Registry 查询策略（`HKLM\SOFTWARE\ComfyUI`）作为第三探测层。

*Signed off by Manus AI*
