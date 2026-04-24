# SESSION-190 外网参考研究笔记

## 1. Appearance-Motion Decoupling（外貌与运动解耦）

### 学术背景
- **MoSA (Wang et al., 2025)**: "Motion-Coherent Human Video Generation via Structure-Appearance Decoupling" — 将人体视频生成解耦为结构生成和外观生成两个独立组件。结构流负责骨骼/运动，外观流负责纹理/颜色。这证明了在生成管线中，运动引导和外观引导必须分离处理。
- **MCM (NeurIPS 2024)**: "Motion Consistency Model" — 单阶段视频扩散蒸馏方法，显式解耦运动和外观学习。证明了当两者耦合时，模型容易产生"模态污染"。
- **DC-ControlNet (2025)**: "Decoupling Inter- and Intra-Element Conditions" — 多条件图像生成框架，证明了不同控制条件（如深度、法线、RGB）之间的解耦对生成质量至关重要。

### 工程映射
当物理引导退化为 Dummy Mesh（圆柱体白模）时，其 Albedo 会对 SparseCtrl RGB 引导产生毁灭性的"模态污染"。解决方案：
1. 检测到 `pseudo_3d_shell` 生成的假人时，强行将 SparseCtrl RGB 的 `strength` 归零
2. 强制 `denoise=1.0`（ComfyUI 中 denoise=1.0 等效于从纯噪声生成，完全忽略输入图像的色彩信息）
3. 仅保留 Depth/Normal 控制网（strength 降至 0.45），仅提取骨架动势

### ComfyUI 技术验证
- **denoise=1.0 行为**（GitHub ComfyUI #1077）：在 ComfyUI 中，denoise=1.0 仅在输入 latent 为全零时等效于 txt2img。当输入非零时，denoise=1.0 仍会添加最大噪声但保留微弱的输入信号。因此配合将 RGB 引导 strength=0.0 可彻底消除假人色块影响。
- **SparseCtrl 强度控制**（ComfyUI-AnimateDiff-Evolved #245）：SparseCtrl RGB 通过 `strength` 和 `motion_strength` 参数控制引导强度。将 strength 设为 0.0 可完全禁用 RGB 引导，同时保留 Depth/Normal 引导。

## 2. LookDev Pipeline & Granular Execution（视觉打样与颗粒度执行）

### 工业界参考
- **Foundry Katana LookDev Workflows**: 工业级 LookDev 工具支持单资产迭代预览，无需渲染完整场景。
- **AAA Game Art Pipeline**: 3A 游戏资产管线中，LookDev 阶段允许美术师对单个资产进行快速迭代，而非强制批量渲染所有变体。
- **Unreal Engine Animation Blueprint**: 状态机允许单独测试单个动画状态，无需遍历所有状态。

### 工程映射
在 3A 资产量产前，开发者必须能够进行单点迭代（Single-Action Execution）。强迫执行全状态机阵列会导致极大的算力浪费。解决方案：
1. 在 CLI 调度器中引入 LookDev 路由
2. 允许用户仅挑选单一动作（如 jump）进行极速渲染打样
3. 通过 `action_filter` 机制在批处理引擎中仅解算并推流选定动作

## 3. Robust I/O Sanitization & Semantic Hydration（鲁棒的输入净化与语义兜底）

### 工业界参考
- **OWASP Input Validation Cheat Sheet**: 所有用户输入必须经过严格验证和净化，包括路径分隔符、引号字符等。
- **Python Windows Path Handling (SO #13501140)**: Windows 终端复制路径时天然附带双引号，必须执行 `.strip('"')` 净化。
- **Fail-Fast Principle (Michael Nygard "Release It!")**: 当输入无效时，系统应立即报错而非静默降级。

### 工程映射
1. **双引号粉碎机**: 拦截所有路径输入处，强制执行 `.strip('"').strip("'").strip()`
2. **快速失败**: 路径无效时绝对禁止静默降级，必须红字警告并要求重新输入
3. **语义兜底**: 当用户 Prompt 为空且系统退化为白模时，强制注入高质量 3A 角色提示词

## 4. 参考文献汇总

| 编号 | 参考 | 用途 |
|------|------|------|
| 1 | MoSA (Wang et al., 2025) arXiv:2508.17404 | 结构-外观解耦理论基础 |
| 2 | MCM (NeurIPS 2024) | 运动-外观解耦蒸馏方法 |
| 3 | DC-ControlNet (2025) arXiv:2502.14779 | 多条件解耦控制 |
| 4 | SparseCtrl (Guo et al., 2023) arXiv:2311.16933 | 稀疏控制信号引导 |
| 5 | ComfyUI-AnimateDiff-Evolved #245 | SparseCtrl 强度控制实践 |
| 6 | ComfyUI #1077 | denoise=1.0 行为验证 |
| 7 | OWASP Input Validation Cheat Sheet | 输入净化最佳实践 |
| 8 | Foundry Katana LookDev Workflows | 工业级单资产迭代 |
| 9 | Unreal Engine Animation Blueprint | 动画状态机单状态测试 |
| 10 | Michael Nygard "Release It!" | Fail-Fast 原则 |
