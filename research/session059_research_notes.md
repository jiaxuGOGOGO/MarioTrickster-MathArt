# SESSION-059 研究笔记

## 已确认的仓库基线

| 项目 | 结论 |
|---|---|
| 仓库 | `jiaxuGOGOGO/MarioTrickster-MathArt` |
| 默认分支 | `main` |
| 最新提交哈希（读取时） | `723ce3499235e0aeac14a5c7432f72276a71c48b` |
| 当前版本基线 | `0.49.0` |
| 最近会话 | `SESSION-058` |

## Unity URP 2D Secondary Textures 官方结论

Unity 官方文档《Add a normal map or a mask map to a sprite in URP》确认了以下接线要求：

1. 在 2D URP 中，法线图和遮罩图属于 **sprite 的 secondary textures**。
2. 法线图导入时应将 Texture Type 设为 **Normal Map**，遮罩图设为 **Default**。
3. 两者都应关闭 **sRGB (Color Texture)**，以避免采样失真。
4. Secondary Textures 使用与主 Sprite 相同的 UV，因此法线图必须与主图像素级对齐。
5. 在 Sprite Editor 的 Secondary Textures 中，法线图标准命名为 **`_NormalMap`**，遮罩图标准命名为 **`_MaskTex`**。
6. SpriteRenderer 需要使用支持这些纹理的材质，例如 **`Sprite-Lit-Default`**。
7. Light 2D 侧必须启用 **Use Normal Map**（Fast 或 Accurate），遮罩则通过 Blend Style 的 masked 选项生效。

## 对项目的直接启示

| 需求 | 工程含义 |
|---|---|
| Python 已产出 normal/depth 等多通道图 | Unity 侧需要自动化导入器把 normal 绑定为 `_NormalMap` secondary texture |
| 需要“编排器大一统” | `EvolutionOrchestrator` 应该扩展到 Unity 原生导出/验证桥，而不是停留在离线产物层 |
| 需要后续 2D 光照体积感 | 必须优先复用或升级现有 Unity 插件生成器，而不是另起炉灶 |

## 待继续研究

1. Dead Cells 参考资料中关于 3D→2D 预烘焙法线与辅助贴图的可落地工作流。
2. Unity `AssetPostprocessor` / `ScriptedImporter` 是否能自动设置 sprite secondary textures。
3. Miles Macklin XPBD 论文与 VAT 方案在本仓库中的最佳落点：离线烘焙为顶点动画纹理，或转译为 2D 骨骼位移缓存。

## Dead Cells 参考资料结论

Game Developer 上的 Thomas Vasseur 文章给出了一条非常明确的工业实践路径：**用 3D 模型驱动 2D 动画生产**。其核心不是“伪 3D 风格”本身，而是把 3D 作为可复用、可附挂、可快速重渲染的中间资产层。文章明确强调三点：

1. 为了在有限带宽下保持高质量，他们需要一种**无需每次返工都手绘**的生产管线。
2. 一旦已有 3D 模型，追加护甲等部件只需要把新资产挂到旧模型上即可，复用效率极高。
3. 他们将 3D 模型以**低分辨率、无抗锯齿、卡通着色**方式输出为 2D 结果，优先保证动画迭代效率与动作表现。

这说明对本项目而言，最正确的落地方式不是让 Unity 端“猜测”材质语义，而是让 Python/离线阶段输出完整辅助通道，再由 Unity 原生导入链路把这些辅助通道直接变成可被 Sprite-Lit / Light 2D 使用的资产。

## XPBD 原论文结论

从 Macklin、Müller、Chentanez 的论文《XPBD: Position-Based Simulation of Compliant Constrained Dynamics》可提炼出与本项目最相关的工程含义：

1. XPBD 解决了传统 PBD 中**刚度依赖迭代次数与时间步长**的问题。
2. XPBD 通过 compliance 与总拉格朗日乘子更新，使约束行为更稳定，并能提供**约束力估计**。
3. 这类稳定性尤其适合在 Python/Taichi 离线阶段批量生成布料运动缓存，因为可以在不同时间步与迭代预算下保持更一致的形变特征。
4. 因此，把 Taichi XPBD 结果烘焙为 **VAT（Vertex Animation Textures）** 或等价位移缓存，再由 Unity shader/2D 骨骼系统重放，是比把 Taichi JIT 实时塞进 Unity 2D 更稳妥的路线。

## 当前工程结论升级

| 研究对象 | 已确认结论 | 对代码的直接要求 |
|---|---|---|
| Unity URP 2D 官方文档 | `_NormalMap` / `_MaskTex` Secondary Textures 是标准接线方式 | 需要新增或升级 Unity 导入器/后处理器，自动完成次级纹理挂载 |
| Dead Cells 管线 | 3D/离线中间资产 + 低分辨率输出 + 高复用 | 需要把项目的多通道导出与 Unity 资产组装打通 |
| XPBD 原论文 | 稳定、时间步/迭代不敏感，适合离线缓存 | 需要新增布料缓存/VAT 烘焙模块，而不是尝试在 Unity 端实时重演 Taichi |

## Unity 编辑器 API 研究结论

进一步查看 Unity 2D Sprite 编辑器 API 文档后，可以确认自动化次级纹理接线并非只能靠手工 Sprite Editor：

1. `SpriteDataProviderFactories` 可以从目标对象取得 `ISpriteEditorDataProvider`。
2. `ISecondaryTextureDataProvider` 明确暴露了 `textures` 属性，其类型为 `SecondarySpriteTexture[]`，说明编辑器脚本可以**程序化读写** sprite 的 secondary textures。
3. 因此最稳妥的 Unity 落地方案是：
   - 用 `AssetPostprocessor` 识别主贴图与其 `_normal` / `_mask` / `_depth` / `_thickness` / `_roughness` 等兄弟文件；
   - 通过 `SpriteDataProviderFactories` + `ISecondaryTextureDataProvider` 把 `_NormalMap`、`_MaskTex` 等标准槽位自动写入 sprite 导入数据；
   - 再由材质/导入器把非 Unity 标准通道（深度、厚度、粗糙度）写入自定义材质或 bundle manifest。

## 研究导向的实现策略更新

| 问题 | 结论 | 推荐实现 |
|---|---|---|
| Unity 能否自动设置 Secondary Textures | 能 | 生成 `AssetPostprocessor`，利用 `ISecondaryTextureDataProvider` 程序化挂接 |
| 应使用 ScriptedImporter 还是 AssetPostprocessor | 两者都可并用 | `.mathart` bundle 继续用 ScriptedImporter；普通 png 多通道目录额外用 AssetPostprocessor 自动识别 |
| Depth/Thickness/Roughness 如何接入 | 不属于 Sprite-Lit 默认 secondary texture 标准槽 | 保留在 bundle manifest 中，并在自定义材质或 importer 里设置 shader 属性 |

## VAT 研究结论

SideFX 的 Unity VAT 教程进一步确认了离线布料缓存路线的工程可行性：

1. VAT 的本质是把**网格动画逐帧烘焙到纹理**中，再由 Unity shader 在 GPU 上采样重放。
2. 该方案特别适合 **cloth / soft body / fluids / particles** 这类不容易用传统骨骼动画表达的非刚体形变。
3. VAT 的优势是**只依赖纹理与 shader，在 CPU 上比传统动画系统更轻**。
4. 对布料场景，常见输入是 **Position map + Rotation map**；对本项目的 2D/2.5D 目标，也可以简化为 **Position/Offset map + bounds metadata**。
5. 资料中也暴露出一个很实际的工程点：高精度缓存格式比普通 PNG 更稳，这意味着本项目若要做高质量布料缓存，应优先支持 EXR 或 float/16-bit 编码，再视导出目标回退到压缩格式。

## 由研究收敛出的实现框架

| 层级 | 目标 | 推荐形态 |
|---|---|---|
| Python/Taichi 离线层 | 计算 XPBD 布料形变 | 逐帧输出顶点位置与可选法线/边界 |
| 烘焙层 | 压缩并标准化缓存 | 生成 VAT 纹理、manifest、采样范围、帧率 |
| Unity 接入层 | GPU 重放与 2D 管线接线 | Shader 采样 VAT，`AssetPostprocessor` 自动接线，Sprite/mesh renderer 自动赋材质 |
| 三层进化循环 | 持续演化 | 编排器评估 VAT 有效性、蒸馏规则、持久化状态 |

## 阶段 2 研究结论总括

当前研究已经足够支撑进入实现阶段：

1. **Unity URP 2D**：官方支持 `_NormalMap` / `_MaskTex` secondary textures，且可通过编辑器 API 自动写入。
2. **Dead Cells 参考路线**：强调离线 3D/中间资产复用，再低分辨率输出 2D，多通道辅助贴图应随主图一起进入引擎。
3. **XPBD**：适合在离线阶段稳定生成布料缓存，不宜强行搬到 Unity 2D 实时执行。
4. **VAT**：是把 Taichi XPBD 布料结果低 CPU 成本接入 Unity 的合理桥梁。

下一步应直接把这些结论落实到仓库代码中，尤其是统一编排器、Unity 插件生成器、VAT 烘焙模块、审计与项目记忆更新链路。
