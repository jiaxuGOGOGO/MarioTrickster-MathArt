## Session Metadata

| Field | Value |
|---|---|
| Session | `SESSION-107` |
| Focus | `P1-AI-1` 数学渲染缓冲到 ControlNet / ComfyUI 序列资产的工程闭环 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Base Head Before Commit | `d4dd6c7` |
| Validation | `5 PASS / 0 SKIP / 0 FAIL`（`python3.11 -m pytest -q tests/test_p1_ai_1_controlnet_bridge.py`） |
| Additional Smoke Check | `normal_depth_dual_controlnet` 与 `sparsectrl_animatediff` 两套序列 payload 语义注入均通过本地验证 |
| Primary Files | `mathart/animation/controlnet_bridge_exporters.py`，`mathart/animation/frame_sequence_exporter.py`，`mathart/animation/comfyui_preset_manager.py`，`mathart/assets/comfyui_presets/normal_depth_dual_controlnet.json`，`tests/test_p1_ai_1_controlnet_bridge.py`，`PROJECT_BRAIN.json` |

## Executive Summary

本轮工作的目标，是把上一轮已经打通的**正交像素渲染实体产物**，正式桥接为可被 ComfyUI / ControlNet 工业工作流直接消费的条件资产。换句话说，项目现在不再停留在“数学引擎可以产出 Albedo / Normal / Depth”的阶段，而是进一步完成了**导出层协议收敛**：法线图被显式映射到 ControlNet NormalBae 所需的 8-bit RGB 视觉协议，深度图被归一化为可配置极性的灰度导引图，动画批次被整理为 VHS 兼容的连续编号帧目录，并且双 ControlNet 序列工作流也已经以**外置 preset + 语义选择器注入**的形式落地。[1] [2] [3]

这次闭环最关键的价值，不在于“多写了几个导出函数”，而在于**把核心数学渲染器与导出/量化/写盘行为物理隔离开来**。这一点与 Pixar RenderMan 的 ports-and-adapters 思路一致：核心渲染端负责产生高精度中间结果，导出器负责执行面向外部系统的格式降维与资产打包，二者通过强类型 manifest 对接，而不是让渲染器直接污染磁盘 I/O。[4]

从项目阶段角度看，`P1-AI-1` 已经从 “IN-PROGRESS” 推进到 **“SUBSTANTIALLY-CLOSED”**。`P1-AI-1A` 到 `P1-AI-1D` 已全部落地；唯一尚未闭环的是 `P1-AI-1E`，也就是在 maintainer 的 RTX 4070 工作站上，挂载真实 ComfyUI / ControlNet 权重并执行一次端到端真机推理，沉淀真实运行时证据、耗时与视觉结果。[5]

## Research Alignment Audit

| Reference | Requested Principle | `SESSION-107` Concrete Closure |
|---|---|---|
| ControlNet 1.1 / NormalBae [1] | 法线条件图应为真实法线视觉编码，R=X，G=Y，B=Z，8-bit RGB | `NormalMapExporter` 采用显式 `(N*0.5+0.5)*255` 映射，禁止对原始法线矩阵直接暴切；并加入值级断言，锁定 `[0,0,1] -> [127,127,255]` |
| MiDaS / Zoe Depth 常规实践 [1] | 深度导引图需做稳定归一化，并允许极性切换以适配不同控制模型 | `DepthMapExporter` 提供线性归一化、覆盖掩码外填充值与 `invert_polarity` 选项 |
| ComfyUI VideoHelperSuite [2] | 帧序列必须使用连续编号目录，目录注入优先，建议带批次元数据 sidecar | `FrameSequenceExporter` 输出 `frame_%05d.png` 连续编号目录，并伴随 `sequence_metadata.json` |
| Stable Diffusion / Diffusers VAE 缩放约束 [3] | 输入宽高应满足 8 的整数倍，以避免潜空间对齐问题 | 单帧与序列导出均统一经过 8-pixel alignment padding，metadata 中显式记录原始尺寸与 padding 信息 |
| Pixar RenderMan / OpenUSD Adapter 思路 [4] | 核心渲染与外部格式导出分层，资产关系通过 typed manifest / composition 表达 | 本轮新增 `SPRITE_SINGLE` / `IMAGE_SEQUENCE` 强类型 schema，并保持导出器为 adapter 层、渲染核心不写盘 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/controlnet_bridge_exporters.py` | **NEW**。实现 `NormalMapExporter`、`DepthMapExporter`、`PaddingInfo`、`ExportResult` | 将 float64 法线/深度缓冲安全转换为 ControlNet 兼容 PNG + JSON sidecar + typed manifest |
| `mathart/animation/frame_sequence_exporter.py` | **NEW**。实现 `FrameSequenceExporter` | 将多帧导引图导出为 VHS 连续编号序列目录，并生成 `sequence_metadata.json` |
| `mathart/assets/comfyui_presets/normal_depth_dual_controlnet.json` | **NEW**。外置 dual-ControlNet 序列预设 | 提供纯 Normal + Depth 双条件序列工作流，不依赖硬编码 node id |
| `mathart/animation/comfyui_preset_manager.py` | 扩展序列 preset 选择器体系与 payload 组装 | 同一套 API 现在同时支持 `sparsectrl_animatediff` 与 `normal_depth_dual_controlnet` |
| `mathart/core/artifact_schema.py` | 新增 `IMAGE_SEQUENCE` schema，并补全 `SPRITE_SINGLE` required outputs/metadata | 导出产物不再是松散文件列表，而是可校验的强类型 manifest |
| `mathart/core/backend_types.py` | 新增 `CONTROLNET_NORMAL_EXPORT`、`CONTROLNET_DEPTH_EXPORT`、`FRAME_SEQUENCE_EXPORT` | Math-to-AI 导出器成为 registry 语义上的一等后端类型 |
| `mathart/animation/__init__.py` | 导出新桥接模块 | 使外部代码可从稳定公共命名空间访问桥接导出器 |
| `tests/test_p1_ai_1_controlnet_bridge.py` | **NEW**。5 个定向白盒测试 | 锁定法线值映射、深度极性、VHS 连号、manifest 合规与双控 preset 注入语义 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 法线值级映射不偏色 | `test_encode_normal_rgb_maps_z_axis_basis_vector_exactly` 断言 `[0,0,1]` 精确映射为 `[127,127,255]` |
| 8 像素对齐强制生效 | `test_normal_map_exporter_pads_to_8_and_validates_manifest` 验证 `10x14 -> 16x16` |
| 深度极性可配置 | `test_depth_map_exporter_supports_polarity_switch` 验证 near/far 像素值可随 `invert_polarity` 翻转 |
| VHS 序列规范落地 | `test_frame_sequence_exporter_writes_vhs_contiguous_frames_and_metadata` 验证 `frame_00000.png` 连续编号与 metadata 一致性 |
| 双 ControlNet 序列 preset 可用 | `test_dual_controlnet_sequence_payload_injects_only_normal_and_depth` 验证 dual preset 只注入 `normal` / `depth`，不混入 `rgb` |
| 主干兼容性未被破坏 | 额外 smoke check 证明 `sparsectrl_animatediff` 仍可组装 payload，说明新增逻辑未破坏既有 SparseCtrl 路径 |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 Anti-Format-Corruption：严禁对原始法线矩阵直接 `.astype(np.uint8)` | ✅ 已合规。法线导出走显式 `(N*0.5+0.5)*255` 映射，并由值级测试锁定基准向量输出 |
| 🔴 Anti-Pipeline-Bleed：核心渲染器内严禁写盘 | ✅ 已合规。PNG/JSON/manifest 写盘全部位于 exporter adapter；渲染核心未引入 `os.makedirs` / `cv2.imwrite` |
| 🔴 Anti-Hardcoding：严禁 `node["14"]` 一类脆弱寻址 | ✅ 已合规。ComfyUI payload 注入统一走 `class_type + _meta.title` 语义选择器 |
| 🔴 OOM / latent 对齐风险 | ✅ 已合规。所有导出图像均经过 8-pixel alignment，并把 padding 结果写入 metadata |
| 🔴 拓扑污染 | ✅ 已合规。新增 dual-control preset 作为外部 JSON 资产落地，注入器不增删节点，只改输入值 |

## What Remains for Full Closure

`P1-AI-1` 还剩最后一步，即 `P1-AI-1E`：在 maintainer 的 RTX 4070 工作站上做一次真实端到端验证。当前仓库已经具备离线可测的协议闭环，但还没有沉淀**真实权重、真实 ComfyUI runtime、真实显卡、真实输出样片**的运行证据。因此，严格来说，本轮关闭的是**工程接口与导出协议层**，而不是最终生产运行证据层。[5]

| Remaining Item | Why It Still Matters |
|---|---|
| 真机 ComfyUI 权重校验 | 需要确认 `control_v11p_sd15_normalbae.pth` 与 `control_v11f1p_sd15_depth.pth` 的实际运行兼容性 |
| 实际生成画质检查 | 需要观察 Normal / Depth 双导引在本项目像素风格资产上的稳定性与边缘保真 |
| RTX 4070 性能证据 | 需要记录 batch size、显存占用、单批时长、失败样例与推荐参数 |
| 真实 artifact 存档 | 需要把输入导引图、工作流 payload、输出样片、运行报告统一存档，形成可回放证据链 |

## Preparing for `P1-ARCH-5` OpenUSD-Compatible Scene Interchange

现在如果要无缝接入 `P1-ARCH-5`，当前架构**不需要大改**，但需要做几处“先小幅归一化、后再上 USD 序列化”的微调。这些准备工作的核心思想是：先把仓库内部的逻辑 scene contract 稳定为**prim-like typed graph**，再让 USD / USDA / USDC 导出器作为独立 adapter 落在最外层，而不是反过来让业务代码直接长出 USD 专属细节。[4] [5]

### 建议的微调准备

| Order | Micro-adjustment | Purpose |
|---|---|---|
| 1 | 为 `ArtifactManifest` / `CompositeManifestBuilder` 引入**稳定 prim path 约定** | 例如 `/World/Character/BaseMesh`、`/World/Character/Guides/NormalSequence`，避免后续 USD exporter 只能从文件名反推层级 |
| 2 | 把“逻辑组合关系”和“具体文件格式序列化”彻底拆开 | 先保持仓库内部是 typed scene graph，随后再新增 `UsdSceneExporter` / `UsdaExporter` 之类的 adapter |
| 3 | 为单帧图像、帧序列、材质通道建立统一的 relationship 语义 | 例如 `material:normalGuide`、`material:depthGuide`、`sequence:frames`，便于后续映射到 USD relationships / asset paths |
| 4 | 给序列资产加入稳定 asset identifier | 除磁盘路径外，再给 sequence/bundle 分配逻辑 `asset_id`，让跨目录搬迁不破坏 scene 引用 |
| 5 | 提前定义 transform / material / guide 三类 schema 边界 | 这样 OpenUSD 导出时可以清楚映射到 `Xform`、`Material`、自定义 guide prim 或 relationship layer |
| 6 | 保持 adapter-only export 原则 | 渲染器、求解器、导出器仍然各司其职，避免为了 OpenUSD 支持再次把写盘逻辑倒灌回核心引擎 |

### 为什么这组微调足够关键

一旦上述 6 点完成，`P1-ARCH-5` 的实现将会从“重构式改造”下降为“新增序列化 adapter”。届时，无论是基础角色、Ribbon Mesh、Normal/Depth Guide、还是 VHS 图像序列，都可以被统一看作**具备稳定 prim identity、typed outputs、relationship arcs 与 lineage metadata 的场景节点**。这样一来，OpenUSD 兼容层只需要负责把这些节点映射到标准化场景描述，而不是重新发明上游业务对象模型。[4] [5]

## Recommended Next Actions

| Order | Task | Purpose |
|---|---|---|
| 1 | 执行 `P1-AI-1E` 真机闭环验证 | 在 RTX 4070 上完成真实 ComfyUI / ControlNet 运行，并归档样片与运行证据 |
| 2 | 为 sequence / material / guide 资产增加稳定 `asset_id` 与 prim path | 给 `P1-ARCH-5` 铺平内部语义层 |
| 3 | 为 `CompositeManifestBuilder` 增加关系类型枚举 | 让 base mesh、attachment mesh、guide sequence 的组合语义更接近 USD relationship arcs |
| 4 | 新增 adapter-only 的 OpenUSD exporter 原型 | 在不污染核心渲染器的前提下，把现有 typed manifests 投影到 USD-compatible scene description |

## References

[1]: https://github.com/lllyasviel/ControlNet-v1-1-nightly/blob/main/README.md "ControlNet 1.1 README"
[2]: https://github.com/kosinkadink/ComfyUI-VideoHelperSuite "ComfyUI VideoHelperSuite"
[3]: https://github.com/huggingface/diffusers/blob/main/src/diffusers/image_processor.py "Diffusers image_processor.py"
[4]: https://rmanwiki-26.pixar.com/space/REN26/19661867 "Pixar RenderMan Output Driver / Display Pipeline Documentation"
[5]: ./PROJECT_BRAIN.json "PROJECT_BRAIN.json"
