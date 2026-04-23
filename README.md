<div align="center">
  <h1>🎮 MarioTrickster-MathArt</h1>
  <p><b>融合大一统知识库、纯物理干跑预演与受控基因繁衍的双轨制美术引擎</b></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+" />
    <img src="https://img.shields.io/badge/Version-0.46.0-success.svg" alt="Version 0.46.0" />
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License MIT" />
    <img src="https://img.shields.io/badge/Architecture-Three_Layer_Evolution-orange.svg" alt="Architecture" />
  </p>
</div>

---

## 🚀 一句话 Pitch

MarioTrickster-MathArt 并非传统意义上的“绘图脚本”，而是一个**能够自我学习、自我审查并安全进化的工业级生产大脑**。它将模糊的美术意图（Vibe）精准翻译为受控的数学参数，通过四维防爆雷达拦截系统故障，利用 GitOps 总线实现人类知识向代码约束的无损蒸馏，最终在沙盒中孵化出符合商业级规范的像素艺术与动画资产。

---

## ⚡ 一键极速起步 (Zero-Friction Quick Start)

无论你是美术、策划还是程序，只需复制以下 3 行命令，即可无脑拉起整个工业级导演工坊：

```bash
# 1. 克隆仓库并进入目录
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git && cd MarioTrickster-MathArt

# 2. 安装核心依赖 (推荐使用虚拟环境)
pip install -e .

# 3. 启动品牌级交互式向导 (Dual-Track Wizard)
mathart
```

终端将弹出极客风格的交互菜单，你只需输入数字即可选择对应的生产模式（如 `[5] 🎬 语义导演工坊 (Director Studio)`）。

> **🔰 品牌命令说明**：`mathart` 与 `mathart-wizard` 是等价的顶层入口，二者都会直接调起五模式向导。向导内置了**全局不死循环**，任何子任务结束后都会自动返回主菜单。旧版子命令式工具 `mathart-evolve` 保留为底层兼容入口，用于自动化脚本与 CI，**不会**弹出交互菜单。
>
> **💡 杀软误报提示**：系统在底层会频繁调度子进程与文件读写，若遇到 Windows Defender 拦截，请参考 [排障与自救手册](docs/TROUBLESHOOTING.md) 添加白名单。

---

## 💎 核心特性矩阵 (The Diátaxis Reference)

### 1. 🛡️ 四维防爆雷达 (Preflight Radar)
在启动任何耗时渲染前，系统会进行微秒级的环境干跑预演：
- **GPU 探针**：校验显存与驱动状态（默认最低 6144 MiB ≈ 6 GiB VRAM 保护，通过 `minimum_gpu_vram_mb` 可调）。
- **Python 探针**：检查依赖完整性与版本兼容性。
- **ComfyUI 探针**：跨进程发现与连接检测。
- **Verdict 仲裁**：输出 `READY` / `AUTO_FIXABLE` / `MANUAL_INTERVENTION_REQUIRED` 三态结论。

### 2. 🧠 知识蒸馏总线 (GitOps Distillation Bus)
人类的纸本知识（解剖学、物理公式、色彩理论）不再是死文档：
- **知识沙盒**：Markdown/JSON 知识被 `KnowledgeParser` 解析、`RuleCompiler` 编译并在沙盒中进行 100 步物理干跑验证。
- **动态约束**：通过验证的知识自动挂载为 `RuntimeDistillationBus` 上的参数边界（`CompiledParameterSpace` 密集 NumPy + 可选 Numba JIT），实时纠正“反物理”或“反直觉”的生成参数。

### 3. 🎬 导演级创作者工坊 (Director Studio)
告别冰冷的代码调参，用自然语言指挥引擎：
- **语义到参数翻译**：输入 `"活泼, 弹性"`，系统自动映射为具体的 physics/animation 参数偏移。
- **交互式 Animatic REPL**：生成前提供毫秒级的白模线框预演，支持 `[+] 再夸张点` / `[-] 收敛点` 的多轮调优，并由真理网关守护物理与数学边界。
- **黄金连招 (Golden Handoff)**：预演通过后，提供无缝的 `[1] 一键出图` 与 `[2] 知识血统查账` 连招，告别割裂的终端体验。

### 4. 🧬 蓝图受控繁衍 (Blueprint Evolution)
像培育变异生物一样量产美术资产：
- **基因型锁定**：通过 `freeze_locks: ["physics"]` 锁定核心物理手感，仅允许外观基因发生变异（三级冻结 + 知识驱动裁剪）。
- **纯 YAML 序列化**：资产血统记录完全去毒（无 Base64、无绝对路径），支持跨版本兼容加载。

### 5. ✈️ 航空级黑匣子与自净系统 (Aviation-Grade Blackbox & GC)
- **全局崩溃拦截**：`sys.excepthook` 配合双重故障保护，任何闪退都会被记录到 `logs/mathart.log`（每日午夜轮转，保留最近 7 份）。
- **两级垃圾回收**：每次 CLI 启动触发冷清扫（`GarbageCollector.sweep()`，TTL 默认 7 天）+ 进化循环内的热修剪（Hot Pruning），永远告别硬盘爆满。

---

## 📚 工业级文档矩阵 (Documentation as Code)

根据 **Diátaxis** 框架，我们为你准备了严格分类的黄金文档：

- **🎓 Tutorials (教程)**：[创作者白皮书与蓝图使用手册](docs/USER_GUIDE.md) —— 手把手教你如何编写意图、繁衍蓝图、以及在导演工坊中挥洒创意。
- **🚑 Reference (参考)**：[排障与自救手册](docs/TROUBLESHOOTING.md) —— 遇到网络超时、无响应或磁盘爆满时，如何通过黑匣子寻踪自救。
- **🔬 Explanation (解析)**：请参阅 `PROJECT_BRAIN.json` 与 `SESSION_HANDOFF.md`，了解系统从 V0.1 到完全体的史诗级架构演进。
- **🔍 Audit (审计)**：[文档↔代码契约审计](docs/audit/SESSION-143-DOC-CODE-CONTRACT-AUDIT.md) —— SESSION-143 单向契约对齐的完整证据链。

---

<div align="center">
  <p><i>"The most reliable software is the one that knows how to fail safely."</i></p>
  <p>Signed off by <b>Manus AI</b></p>
</div>
