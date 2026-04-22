# 🚑 排障与自救手册

当 **MarioTrickster-MathArt** 引擎出现异常时，请不要惊慌。系统内置了航空级的黑匣子记录仪和防爆雷达，能够帮你快速定位并解决绝大多数问题。

---

## 1. 🔍 黑匣子寻踪 (Blackbox Flight Recorder)

**如果系统无征兆闪退、卡死或无反应，绝对不要慌！**

系统在底层部署了全局崩溃拦截器（`sys.excepthook`），任何未被捕获的异常都会被安全地写入日志文件。

### 怎么做？
1. 立即前往项目根目录下的 `logs/` 文件夹。
2. 打开最新的 `mathart.log` 文件。
3. 滚动到文件最末尾，寻找标有 `[ERROR]` 或 `[CRITICAL]` 的日志行。
4. 将这些日志信息复制，发给 AI 助手（如 Manus），它能直接诊断并修复。

> **⚠️ 注意**：日志文件默认保留 7 天，系统会自动轮转（Log Rotation）并清理旧日志，你无需手动管理。

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

### 问题 B：网络超时 (API 或 ComfyUI 连接失败)
如果在下载模型、连接外部 API 或与本地 ComfyUI 通信时遇到 `TimeoutError`。

**自救方案**：
系统的所有网络超时参数都已集中管理，你可以通过设置环境变量来放宽限制。在终端中运行以下命令（或写入 `.env` 文件）：

```bash
# 延长网络请求超时时间（默认 60 秒）
export MATHART_NETWORK_TIMEOUT=120.0

# 延长 ComfyUI WebSocket 连接超时时间（默认 600 秒）
export MATHART_COMFYUI_WS_TIMEOUT=1200.0
```

---

## 3. 🧹 废料清理 (Garbage Collection)

引擎在生成图片、视频和中间态模型时，会产生大量的临时文件。虽然系统内置了**两级垃圾回收系统**（启动时的冷清扫和运行时的热修剪），但如果你发现硬盘空间依然紧张，可以手动触发清理。

### 怎么做？

在终端中运行带有 `--purge-cache` 标志的命令：

```bash
# 执行深度清理，删除所有过期（默认 7 天以上）的临时文件和中间产物
mathart-evolve --purge-cache
```

系统会安全地清扫 `temp/` 目录、`.part` 下载残留文件，但**绝对不会触碰**你的核心资产（如 `knowledge/active/`、`blueprints/`、`outputs/` 和 `elite` 文件夹）。

---

<div align="center">
  <p><i>遇到无法解决的问题？请将 <code>logs/mathart.log</code> 提交至 Issue 追踪器。</i></p>
</div>
