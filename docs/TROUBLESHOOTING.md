# 🚑 排障与自救手册

当 **MarioTrickster-MathArt** 引擎出现异常时，请不要惊慌。系统内置了航空级的黑匣子记录仪和防爆雷达，能够帮你快速定位并解决绝大多数问题。

---

## 1. 🔍 黑匣子寻踪 (Blackbox Flight Recorder)

**如果系统无征兆闪退、卡死或无反应，绝对不要慌！**

系统在底层部署了全局崩溃拦截器（`sys.excepthook`，见 `mathart/core/logger.py` 的 `install_blackbox()`），任何未被捕获的异常都会被安全地写入日志文件。

### 怎么做？
1. 立即前往项目根目录下的 `logs/` 文件夹（可通过 `MATHART_LOG_DIR` 环境变量自定义，默认 `<cwd>/logs`）。
2. 打开最新的 `mathart.log` 文件（文件名可通过 `MATHART_LOG_FILENAME` 自定义）。
3. 滚动到文件最末尾，寻找标有 `[ERROR]` 或 `[CRITICAL]` 的日志行。
4. 将这些日志信息复制，发给 AI 助手（如 Manus），它能直接诊断并修复。

> **⚠️ 日志轮转实际口径**：系统使用 `TimedRotatingFileHandler(when="midnight", backupCount=7)`，即**每日午夜轮转**、**保留最近 7 份**历史日志（约相当于 7 天的记录）。轮转间隔与保留份数可通过 `MATHART_LOG_ROTATION_WHEN` / `MATHART_LOG_BACKUP_COUNT` 环境变量覆盖。

---

## 2. 🛡️ 防爆装甲应对 (Defender & Network Issues)

### 问题 A：杀毒软件误报 / 无权限创建文件
系统在运行时会频繁调度子进程、读写缓存文件。如果你的 Windows Defender 或第三方杀软拦截了这些操作，系统可能会报错 `PermissionError` 或 `Access Denied`。

**自救方案**：
请手动将项目目录和 ComfyUI 目录加入白名单。如果你使用的是 Windows PowerShell，可以复制以下命令（请替换为你自己的实际路径）并以管理员身份运行：

```powershell
Add-MpPreference -ExclusionPath 'C:\path\to\MarioTrickster-MathArt'
Add-MpPreference -ExclusionPath 'C:\path\to\ComfyUI'
```

向导首次启动时也会打印这段 PowerShell 参考命令（由 `render_defender_whitelist_warning()` 生成），请由你本人确认后手动执行。

### 问题 B：网络超时 (API 或 ComfyUI 连接失败)
如果在下载模型、连接外部 API 或与本地 ComfyUI 通信时遇到 `TimeoutError`。

**自救方案**：
系统的所有网络超时参数都已在 `mathart/core/settings.py` 中集中管理，你可以通过设置环境变量来放宽限制。在终端中运行以下命令（或写入 `.env` 文件）：

```bash
# 延长 HTTP / WebSocket 默认超时（默认 60.0 秒）
export MATHART_NETWORK_TIMEOUT=120.0

# 延长 TCP 建连超时（默认 10.0 秒）
export MATHART_NETWORK_CONNECT_TIMEOUT=30.0

# 延长 ComfyUI WebSocket 操作超时（默认 600.0 秒）
export MATHART_COMFYUI_WS_TIMEOUT=1200.0

# 延长 ComfyUI WebSocket 连接超时（默认 5.0 秒）
export MATHART_COMFYUI_CONNECT_TIMEOUT=15.0
```

---

## 3. 🧹 废料清理 (Garbage Collection)

引擎在生成图片、视频和中间态模型时，会产生大量的临时文件。系统内置了**两级垃圾回收系统**：

- **冷清扫 (Cold Sweep)**：每次命令启动时，由 `mathart/workspace/garbage_collector.py::GarbageCollector.sweep()` 自动触发，扫描 TTL 过期（默认 7 天，见 `GarbageConfig.ttl_days=7`）的 `.part` 下载残留、`temp/` 目录与配置命中的目录。
- **热修剪 (Hot Pruning)**：进化循环内部按代淘汰低分个体，由各 `evolution_*` 后端自行实现。

> **📌 无 `--purge-cache` 命令行参数**：当前代码库的 `mathart` / `mathart-evolve` CLI 中**没有** `--purge-cache` flag，之前文档声称的该用法属于文档幻觉，已在 SESSION-143 校正。如果你希望立即强制执行一次冷清扫，请使用以下等价方式：

```bash
# 方式 A：一行 Python，立即扫一遍当前目录
python -c "from mathart.workspace.garbage_collector import GarbageCollector; GarbageCollector('.').sweep()"

# 方式 B：直接再跑一次任何顶层命令即可自动冷扫一次
mathart --mode 4      # Dry-Run 最轻量，仅走沙盒路径，不唤醒 GPU
```

系统会安全地清扫 `temp/` 目录、`.part` 下载残留文件，但**绝对不会触碰**你的核心资产（`knowledge/active/`、`workspace/blueprints/`、`outputs/` 与 `elite/` 受内置保护红线保护）。

---

## 4. 🧭 命令口径 FAQ

| 症状 | 根因 | 自救 |
|---|---|---|
| `mathart-evolve` 只打印 argparse 帮助，没弹出 5 模式菜单 | `mathart-evolve` 是**底层子命令 CLI**，不是向导入口 | 改用 `mathart` 或 `mathart-wizard` 顶层命令 |
| CI / 非 TTY 下 `mathart` 返回 JSON `MissingModeSelection` 错误 | 检测到非交互环境且未显式指定模式 | 加 `--mode 1..4` 参数（Director Studio `5` 需交互） |
| 控制台脚本报 `entry point not found` | `pip install -e .` 未执行或需要重装 | 在仓库根目录重新 `pip install -e .` 以刷新 console scripts |

---

<div align="center">
  <p><i>遇到无法解决的问题？请将 <code>logs/mathart.log</code> 提交至 Issue 追踪器。</i></p>
</div>
