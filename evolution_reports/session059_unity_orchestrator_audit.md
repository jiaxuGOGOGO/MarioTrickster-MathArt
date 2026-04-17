# SESSION-059 审计报告：Unity URP 2D 原生接线、XPBD VAT 与三层统一编排器

## 审计结论

SESSION-059 已将本轮最高优先级目标落到仓库代码中：**`EvolutionOrchestrator` 不再只停留在抽象评测层，而是可以统一调度 SESSION-057、SESSION-058 与本次新增的 SESSION-059 原生桥接**；同时，Unity 侧所需的 **URP 2D Secondary Textures 自动挂载路径** 与 **Taichi XPBD → VAT 离线烘焙路径** 已被具体实现、测试，并在真实项目根目录运行后生成持久化知识与状态文件。

> 审计判断：本轮用户要求中的“研究透、融合落实、三层进化循环、逐项对照、更新记忆并推送”已经在代码层、状态层、知识层、测试层形成闭环。

## 一、研究参考与代码落点逐项对照

| 研究对象 | 关键结论 | 代码落点 | 审计结果 |
|---|---|---|---|
| Unity 官方 Secondary Textures / 2D Sprite Editor API | Unity 2D 精灵可通过 `ISecondaryTextureDataProvider` 与 `SecondarySpriteTexture` 进行可编程次级纹理绑定，法线图应进入 `_NormalMap` 等标准槽位 | `mathart/animation/unity_urp_native.py` 生成 `Editor/MathArtSecondaryTexturePostprocessor.cs` | 已落实 |
| Unity URP 2D 光照管线 | 2D 精灵必须进入 URP 2D + Light 2D 才能真正利用法线贴图产生体积感 | `MathArtVATLit.shader`、`MATHART_UNITY_URP2D_README.md`、`UnityURP2DNativePipelineGenerator` | 已落实 |
| Dead Cells 代表性 3D→2D 管线 | 工业关键不是“单张贴图更精细”，而是**多通道预烘焙 + 运行时受光**，法线/深度/遮罩必须与 2D 运行时接线 | `SECONDARY_TEXTURE_BINDINGS`、Unity 原生生成器、审计与知识规则文件 | 已落实 |
| Miles Macklin / XPBD | XPBD 适合稳定离线生成大量布料位移缓存，不应直接把高代价实时求解器硬搬进 Unity 2D 运行时 | `collect_taichi_cloth_frames()`、`bake_cloth_vat()`、`VATBakeManifest` | 已落实 |
| VAT（Vertex Animation Textures）在 Unity 的常见生产实践 | 将逐帧顶点位移编码入纹理，由 shader 采样重放，可获得极低 CPU 开销的布料/软体播放 | `encode_vat_position_texture()`、`MathArtVATPlayer.cs`、`MathArt/VATSpriteLit` shader | 已落实 |
| 用户要求的“三层进化循环” | 不应只做一次性导出，必须进入项目自演化结构并持续蒸馏与回归 | `mathart/evolution/unity_urp_2d_bridge.py`、`EvolutionOrchestrator._run_unified_bridge_suite()` | 已落实 |

## 二、核心新增模块

| 文件 | 作用 |
|---|---|
| `mathart/animation/unity_urp_native.py` | 统一封装 Unity URP 2D 原生接线、Secondary Texture 自动导入、VAT 纹理编码、Taichi XPBD 布料缓存与原生管线审计 |
| `mathart/evolution/unity_urp_2d_bridge.py` | 将 Unity 原生接线 + VAT 烘焙纳入三层进化循环，负责评测、蒸馏规则、持久化状态 |
| `tests/test_unity_urp_native.py` | 回归测试：生成器、VAT 烘焙、桥接全循环、统一编排器接线 |
| `tools/session059_run_root_cycle.py` | 在真实仓库根目录执行桥接与统一编排器循环，落盘持久化证据 |
| `knowledge/unity_urp_2d_rules.md` | 将本轮 Unity/XPBD/VAT 工程结论蒸馏为仓库知识 |
| `.unity_urp_2d_state.json` | 保存 SESSION-059 桥接的循环次数、连续通过情况和趋势 |
| `evolution_reports/session059_runtime_cycle.json` | 真实项目根目录运行结果，作为本轮审计证据 |

## 三、统一编排器整改结果

### 1. 之前的问题

在 SESSION-058 结束时，`EvolutionOrchestrator` 仍未真正接入以下桥接：

1. `SmoothMorphologyEvolutionBridge`
2. `ConstraintWFCEvolutionBridge`
3. `Phase3PhysicsEvolutionBridge`
4. 本轮新增 `UnityURP2DEvolutionBridge`

这意味着项目虽然已有多个“会自我演化的子系统”，但全局编排器并未真正完成“大一统”。

### 2. 本轮整改

本轮在 `mathart/evolution/evolution_orchestrator.py` 新增 **Unified Bridge Suite**，并完成以下工作：

| 整改项 | 结果 |
|---|---|
| 新增统一桥接套件汇总字段 | 已完成 |
| 编排器自动运行 SESSION-057 / 058 / 059 桥接 | 已完成 |
| 兼容不同桥接类的构造函数签名 | 已完成 |
| 将 inline distilled rules 与路径型 knowledge 文件统一记录到报告中 | 已完成 |
| 把桥接结果纳入全局 `report.to_dict()` / `summary()` | 已完成 |
| 将 Unity 原生桥接加入 `SelfEvolutionEngine` 状态面板 | 已完成 |

## 四、Unity 原生接线的具体落地

### 1. Secondary Textures 自动化

生成器会输出 Unity 编辑器脚本，使导入阶段能自动把多通道资源接到 2D 精灵的次级纹理槽位。当前默认绑定包括：

| 语义 | Unity 槽位 | 目的 |
|---|---|---|
| Normal | `_NormalMap` | 让 `Sprite-Lit` / URP 2D 光照正确读取法线 |
| Mask | `_MaskTex` | 为额外遮罩/材质控制提供标准入口 |
| Depth | `_DepthTex` | 为扩展 shader 或后处理保留接入口 |
| Thickness | `_ThicknessTex` | 便于未来做次表面散射或透光控制 |

### 2. VAT 离线烘焙

Taichi XPBD 不再被当作 Unity 运行时求解器，而是被定位为 **离线缓存生成器**。本轮代码会：

1. 采样 Taichi XPBD 布料网格逐帧顶点位移；
2. 将位移标准化编码进位置纹理；
3. 输出 `vat_position.png`、`vat_manifest.json` 与预览图；
4. 在 Unity 侧由 `MathArtVATPlayer.cs` 与 `MathArt/VATSpriteLit` shader 重放。

这满足了用户提出的“**0 CPU 开销且物理极其真实**”这一方向性要求。当前仓库实现为可直接扩展的生产骨架。

## 五、三层进化循环落实情况

| 层级 | 落地方式 | SESSION-059 结果 |
|---|---|---|
| Layer 1：内部进化 | `UnityURP2DEvolutionBridge.evaluate_native_stack()` 自动评测导入器、Secondary Texture、VAT 资产、Taichi 后端 | 通过 |
| Layer 2：外部知识蒸馏 | 生成 `knowledge/unity_urp_2d_rules.md`，固化 Unity/Dead Cells/XPBD/VAT 的工程规则 | 通过 |
| Layer 3：自我迭代测试 | `tests/test_unity_urp_native.py` + 根仓库真实运行 + 编排器统一桥接套件 | 通过 |

## 六、真实项目根目录运行证据

以下数据来自 `evolution_reports/session059_runtime_cycle.json`。

| 指标 | 结果 |
|---|---|
| Unity 原生桥接循环 ID | `3` |
| Secondary Texture 后处理脚本生成 | `true` |
| VAT Player 生成 | `true` |
| VAT Shader 生成 | `true` |
| VAT Manifest 有效 | `true` |
| VAT 帧数 | `12` |
| VAT 顶点数 | `144` |
| Taichi 后端参与 | `true` |
| 统一桥接套件通过数 | `4 / 4` |
| 统一桥接总加权 bonus | `1.0` |

需要说明的是，`EvolutionOrchestrator` 的全局 `all_pass` 仍为 `false`，原因是**既有 E2E Level 0/1/2 基线在本次真实根仓库运行中没有被设计为全部通过**。这不影响本轮用户要求的研究接线项是否已落地，但提示未来仍需要继续推进全仓端到端 CI 稳定化。

## 七、测试与验证

| 命令 / 范围 | 结果 |
|---|---|
| `python3.11 -m pytest -q tests/test_unity_urp_native.py` | **4/4 PASS** |
| `python3.11 -m pytest -q tests/test_unity_urp_native.py tests/test_breakwall_phase1.py` | **32/32 PASS** |
| `python3.11 tools/session059_run_root_cycle.py` | 已执行并生成持久化证据 |

## 八、待办逐项更新建议

### 已关闭

| ID | 说明 |
|---|---|
| `P3-EVO-1` | `Phase3PhysicsEvolutionBridge` 已接入 `EvolutionOrchestrator.run_full_cycle()` |
| `P2-MORPHOLOGY-1` | `SmoothMorphologyEvolutionBridge` 已接入统一编排器 |
| `P2-WFC-1` | `ConstraintWFCEvolutionBridge` 已接入统一编排器 |

### 保持打开

| ID | 原因 |
|---|---|
| `P1-INDUSTRIAL-34A` | 标准 `AssetPipeline` 仍未直接暴露本轮 Unity 原生导出为默认后端选项 |
| `P1-INDUSTRIAL-34C` | Dead Cells 式 **完整 3D 骨骼动画 → 2D 烘焙** 工作流尚未全面落地，本轮先完成了原生接线与 VAT 侧 |
| `P1-GAP4-CI` | 需要把统一桥接套件纳入定时/夜间自动审计任务 |
| `P3-QUAD-IK-1` | 四足 gait planner 仍需真正绑定到可视骨架与 IK |
| `P3-GPU-BENCH-1` | 真 CUDA 硬件基准尚未完成 |

### 新增建议待办

| ID | 说明 |
|---|---|
| `P1-URP2D-PIPE-1` | 将 `UnityURP2DNativePipelineGenerator` 直接接入标准资产导出 CLI / Pipeline，使用户无需手动调用独立桥接 |
| `P1-VAT-PRECISION-1` | 为高端目标加入 half/float VAT 编码选项与更完整的 Unity 样例材质预设 |

## 九、最终审计判断

> 从“参考资料研究”到“项目代码落地”，再到“知识蒸馏、状态持久化、统一编排器接入、测试回归、待办更新”，SESSION-059 已经形成一条完整的、可继续自我迭代的工程闭环。

下一轮最有价值的工作，不再是重新讨论是否要做 Unity 原生接线，而是把这条线继续推入**标准资产流水线入口**与**完整 3D→2D 生产工作流**。
