# SESSION-107 外部研究摘录：P1-AI-1 Math-to-AI Pipeline Prototype

## 1. ControlNet 1.1 官方说明（GitHub README）

来源：<https://github.com/lllyasviel/ControlNet-v1-1-nightly/blob/main/README.md>

### Normal 1.1

> "This Normal 1.1 ... can interpret real normal maps from rendering engines as long as the colors are correct (blue is front, red is left, green is top)."

当前可直接落地到代码的约束如下：

| 约束 | 工程含义 |
|---|---|
| `blue is front` | 法线 Z 正向应编码为高蓝值；标准基准向量 `[0,0,1]` 必须映射到接近 `[127,127,255]` |
| `red is left` | X 轴符号必须与 ControlNet 期望一致，不能简单暴力 `astype(uint8)` |
| `green is top` | 绿色通道表示“向上”，因此项目内部若是 `Z-up` 物理/数学约定，导出到 ControlNet 时需要显式坐标约定对齐 |
| rendering engines accepted | 渲染器输出的真实法线图可作为 ControlNet 输入，但前提是颜色编码协议正确 |

### Depth 1.1

> Depth 1.1 对不同深度预处理器（MiDaS / LeReS / Zoe）更鲁棒，并且可以工作于真实 3D 引擎导出的深度图。

| 约束 | 工程含义 |
|---|---|
| 多预处理器鲁棒 | 我们的 DepthMapExporter 应输出“规范化灰度深度”，而不是耦合某个特定预处理器的私有色板 |
| 支持真实 3D 引擎深度 | 可以直接消费 OrthographicPixelRenderEngine 的 float64 线性深度 |
| 多分辨率数据增强 | 导出器必须对输入尺寸和归一化稳定处理，避免尺寸和动态范围抖动 |
| 384 分辨率是常见 MiDaS 对齐点 | 虽然本仓库不直接做预处理推理，但在文档和元数据中应记录分辨率与归一化策略 |

## 2. VideoHelperSuite 官方 README

来源：<https://github.com/kosinkadink/ComfyUI-VideoHelperSuite>

| 观察 | 对本次实现的直接要求 |
|---|---|
| `Load Image Sequence` / `Load Image sequence path` 是目录式加载 | FrameSequenceExporter 必须按目录导出，而不是散装路径列表 |
| 节点一次读取“all image files from a subfolder” | 同一序列目录内必须只放同构帧，避免多种尺寸/命名混杂 |
| 支持 `skip_first_images` / `image_load_cap` / `select_every_nth` | 连续帧编号越稳定越利于可切片批处理，因此应导出从 0 开始的无间断帧号 |
| `Video Combine` 依赖 `frame_rate` 和 `filename_prefix` | `sequence_metadata.json` 应记录 fps、frame_count、prefix、真实分辨率，便于回填至 payload |
| 可将 workflow metadata 写入视频 | 我们的序列级 sidecar JSON 应保存 lineage 与物理来源，满足可追溯性 |

## 3. 当前已确认的代码设计红线

| 红线 | 落地解释 |
|---|---|
| Anti-Format-Corruption | 法线图必须显式 `(N*0.5+0.5)*255` 量化；禁止直接 `astype(np.uint8)` |
| Anti-Pipeline-Bleed | 磁盘写入只能发生在 Exporter / Adapter 中，不能污染 OrthographicPixelRenderEngine |
| Anti-Hardcoding | ComfyUI payload 组装必须复用 `ComfyUIPresetManager` 语义选择器，不能写死 node id |
| 8-pixel alignment | 所有导出图像与序列帧的宽高都要对齐到 8 的倍数，必要时自动 padding |

## 4. 待继续核实的外部点

1. Stable Diffusion VAE 的 8 倍潜空间下采样约束，需要再补一手更接近源码或实现说明的证据。
2. RenderMan AOV / Ports & Adapters 的资产导出隔离，需要补权威架构说明作为本轮会话审计引用。
3. 若能找到更直接的 NormalBae / sd-webui-controlnet 讨论，可进一步确认 Y-up / OpenGL 常见语义与 RGB 通道含义的工程习惯。

## 5. Stable Diffusion / diffusers 的 8 像素对齐实现证据

来源：<https://github.com/huggingface/diffusers/blob/main/src/diffusers/image_processor.py>

从页面文本可直接提取到如下实现级描述：

> `do_resize`: Whether to downscale the image's (height, width) dimensions to multiples of `vae_scale_factor`.
>
> `vae_scale_factor` (`int`, defaults to `8`): VAE scale factor. If `do_resize` is `True`, the image is automatically resized to multiples of this factor.

这意味着 8 像素对齐不是经验建议，而是主流 Stable Diffusion 工程栈中的显式输入前处理规则。对本仓库的直接结论如下：

| 结论 | 代码要求 |
|---|---|
| VAE 默认缩放因子是 8 | 所有面向 SD / ControlNet 的导出图必须确保 `height % 8 == 0 && width % 8 == 0` |
| 主流实现会自动 resize 到 8 的倍数 | 我们更稳妥的策略是 Exporter 端显式 padding，对外记录真实内容分辨率与 padding 后分辨率，避免暗中 resize 破坏物理几何 |
| 输入尺寸与 latent 网格耦合 | FrameSequenceExporter 必须保证整批帧同分辨率、同对齐策略，禁止序列内部尺寸漂移 |

## 6. RenderMan AOV / Display Driver 研究状态

来源尝试：<https://rmanwiki-26.pixar.com/space/REN26/19661867>

本次浏览未成功抓取正文，但从搜索结果与 Pixar 文档入口命名可确认其术语体系由 **Outputs / AOV / Display Driver** 组成。当前可保守采用的架构性结论如下：

| 保守结论 | 对本仓库的架构含义 |
|---|---|
| AOV 是渲染器产出的多通道图像数据 | OrthographicPixelRenderEngine 只负责在内存中产生 float64 normal/depth 等物理真实缓冲 |
| Display Driver / Output 负责写入具体文件格式 | PNG/序列/sidecar JSON 的写盘逻辑必须位于独立 Exporter / Adapter，而不能混入渲染核心 |
| 输出层与渲染层职责分离是行业常见模式 | 本轮新增导出器应以 plugin/adapter 形态存在，并返回强类型 ArtifactManifest |

## 7. 研究结论汇总：本轮代码必须满足的最小外部规范

| 规范源 | 强制要求 |
|---|---|
| ControlNet 1.1 NormalBae | 法线图输出 8-bit RGB，采用显式 `(N*0.5+0.5)*255` 映射；`[0,0,1]` 基准必须落到 `[127,127,255]` |
| ControlNet Depth 1.1 | 深度图为规范化灰度图，允许极性配置，并兼容真实引擎深度 |
| ComfyUI VideoHelperSuite | 序列导出使用目录式同构帧；文件名必须连续、稳定、可批处理；sidecar JSON 应记录 fps/frame_count/resolution/lineage |
| Stable Diffusion / diffusers | 输入图像及整批序列帧必须严格对齐到 8 的倍数 |
| RenderMan AOV / Display Driver 范式 | 渲染器与导出器物理隔离；导出层以适配器插件形式承担量化与写盘 |

## 8. 下一步代码设计方向（供实现阶段直接使用）

1. 在 `mathart/animation` 或 `mathart/export` 下落地新的导出器模块，禁止修改渲染器写盘逻辑。
2. 导出器 API 需要接受纯 NumPy 张量/矩阵，返回包含 `artifact_family`、`backend_type`、输出路径和元数据的强类型 Manifest。
3. Normal 与 Depth 的单帧导出逻辑要能被 FrameSequenceExporter 复用，避免双份格式逻辑漂移。
4. ComfyUI payload 扩展只允许基于 `ComfyUIPresetManager` 的语义选择器注入新目录路径和强度字段。
