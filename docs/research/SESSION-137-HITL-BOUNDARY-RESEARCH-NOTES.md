# SESSION-137 HITL Boundary Gateway Research Notes

## Source 1 — Microsoft Learn: Create symbolic links
- URL: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/create-symbolic-links
- Key findings:
  - Windows 将创建符号链接定义为 `SeCreateSymbolicLinkPrivilege` 用户权限，默认仅管理员组拥有该权利。
  - 官方明确指出该权限应仅授予可信用户，因为符号链接可能带来安全风险。
  - 这意味着在标准用户权限下，程序不应把“无法创建符号链接”视为一个可以静默越过的小故障，更不能在未告知用户的情况下退化为大体量复制。
  - 对本项目的直接约束是：当 Windows 权限不足且目标资产很大时，应当熔断为人工干预向导，而不是继续自动复制塞满磁盘。

## Source 2 — Python Standard Library: urllib.request
- URL: https://docs.python.org/3/library/urllib.request.html
- Key findings:
  - `urllib.request.getproxies()` 会按不区分大小写的方式扫描环境变量 `<scheme>_proxy`，例如 `http_proxy` / `HTTP_PROXY`、`https_proxy` / `HTTPS_PROXY`。
  - `ProxyHandler` 默认就会读取这些代理环境变量；若未设置，则在 Windows 环境下还会回退读取系统代理配置。
  - 这为本项目的网络熔断恢复提供了标准路径：当下载 3 次超时后，不应继续死循环，而应让用户通过统一向导写入本地代理配置，再把代理注入环境变量后重试。
  - 代理配置属于本地运行时偏好，不应写入版本控制，应复用现有本地配置管理机制持久化。

## Source 3 — Microsoft Defender: Configure custom exclusions
- URL: https://learn.microsoft.com/en-us/defender-endpoint/configure-exclusions-microsoft-defender-antivirus
- Key findings:
  - 官方明确支持对文件、文件夹、进程和进程打开的文件设置排除项，且这些排除项会影响计划扫描、按需扫描以及实时保护。
  - 这说明本项目完全可以把“请将本项目目录与 ComfyUI 目录加入白名单”作为标准化人工操作指引输出，而不是让用户自己猜测。

## Source 4 — PowerShell Defender: Add-MpPreference
- URL: https://learn.microsoft.com/en-us/powershell/module/defender/add-mppreference?view=windowsserver2025-ps
- Key findings:
  - `Add-MpPreference -ExclusionPath '<path>'` 是微软官方支持的添加排除目录方式。
  - 官方示例说明该命令会把指定目录加入排除列表，从而禁用该目录上的计划扫描与实时扫描。
  - 这为 CLI Wizard 中打印可复制的参考命令提供了可靠来源，但程序本身不应越权代替用户执行系统级修改。

## Source 5 — NVIDIA: CUDA Installation Guide for Microsoft Windows
- URL: https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/
- Key findings:
  - NVIDIA 官方明确写明使用 CUDA 需要 CUDA-capable GPU、受支持的 Windows 版本，以及驱动与工具链的正确安装。
  - 官方还明确指出：驱动和 toolkit 都必须安装，CUDA 才能工作。
  - 这直接支持本项目的 Driver & CUDA Guard：当当前模式需要 GPU 且检测不到 NVIDIA/CUDA 前提时，系统应在下载和启动前就阻断，而不是等到大模型下载后再失败。

## Source 6 — MDPI: Human-in-the-Loop Artificial Intelligence: A Systematic Review of Concepts, Methods, and Applications
- URL: https://www.mdpi.com/1099-4300/28/4/377
- Key findings:
  - 该综述明确指出，在高错误代价与需要可解释决策的场景中，Human-in-the-Loop 特别重要。
  - 文中强调当前研究重心正从“追求完全自治”转向“增强而不是替代人类决策”，并把 human oversight、trust calibration、责任边界视为核心设计点。
  - 这为本项目的边界网关提供方法论依据：一旦自动化触碰权限、驱动、网络或安全软件等物理边界，就应把控制权交还给用户，并提供结构化、可执行的操作选项，而不是继续盲目自动化。
