# SESSION-084 AI 2D Preset Research Notes

## Browser findings batch 1

### Source A
- Title: Executing ComfyUI Workflows as Standalone Scripts
- URL: https://www.timlrx.com/blog/executing-comfyui-workflows-as-standalone-scripts/
- Key findings:
  - `workflow_api.json` 需要从 ComfyUI 的 `dev` 模式导出，属于后端执行格式。
  - API 工作流移除了节点位置、尺寸等 UI 元数据，只保留 `class_type`、`inputs` 与节点连接关系。
  - 节点之间的连线直接编码在 `inputs` 中，例如 `"clip": ["20", 1]`。
  - 该结构适合作为**数据驱动执行资产**，而不是在 Python 中重新硬编码图拓扑。

### Source B
- Title: RunComfy API Docs — Workflow Files
- URL: https://docs.runcomfy.com/serverless/workflow-files
- Key findings:
  - `workflow.json` 负责完整 UI 图编辑与布局恢复；`workflow_api.json` 负责 API 执行；`object_info.json` 提供运行时节点 schema。
  - 生产调用中推荐把 `workflow_api.json` 作为部署基底，再通过运行时 `overrides` 覆盖输入参数，而不是每次重传或重建整张图。
  - 这直接支持本次任务要求的**图拓扑与执行参数解耦**。

## Immediate implementation constraints distilled

1. ComfyUI 节点图拓扑必须存放在独立 JSON 资产中，后端只能做读取、索引、校验与值注入。
2. 需要保留 `_meta.title` / `class_type` 等可识别特征，避免基于硬编码节点 ID 注入。
3. 若未来要进一步做 schema 级校验，可考虑利用 `object_info.json` 风格的节点 schema 思路做输入验证，但本轮至少要保证注入器具备按节点类型与标题寻址的能力。

## Browser findings batch 2

### Source C
- Title: Adding Conditional Control to Text-to-Image Diffusion Models
- URL: https://openaccess.thecvf.com/content/ICCV2023/html/Zhang_Adding_Conditional_Control_to_Text-to-Image_Diffusion_Models_ICCV_2023_paper.html
- Key findings:
  - ControlNet 的核心是向大型预训练扩散模型加入 **spatial conditioning controls**。
  - 原论文明确强调：锁定 production-ready 预训练大模型主干，仅通过 zero-convolution 分支渐进接入条件控制，避免微调噪声破坏底模。
  - ControlNet 支持 depth、pose、segmentation、edge 等多种空间条件，也支持单条件或多条件并用。
  - 对本项目的直接启示是：**Depth/Normal 等几何导引应作为结构锁定轨，而不是与身份/风格参考混在同一条件通道里。**

### Source D
- Title: IP-Adapter: Text Compatible Image Prompt Adapter for Text-to-Image Diffusion Models
- URL: https://arxiv.org/abs/2308.06721
- Key findings:
  - IP-Adapter 的目标是为预训练文生图扩散模型提供轻量的图像提示能力。
  - 原文强调其使用 **decoupled cross-attention**，把文本特征与图像特征分开注入。
  - 该方法仅需约 **22M** 参数，同时保持底模冻结，并可与其他 controllable tools 协同工作。
  - 对本项目的直接启示是：**身份/外观参考适合作为独立图像条件支路，与 ControlNet 的几何锁定形成双轨结构，而不是把身份参考硬塞进深度或法线条件。**

## Updated architectural constraints

| Constraint | Why it matters |
|---|---|
| Geometry lock must remain separate from identity lock | ControlNet 负责空间结构控制，IP-Adapter 负责图像提示/身份外观参考，二者职责不同 |
| Preserve data-driven preset topology | API workflow JSON 本身就是执行图，不应在 Python 中重新手搓节点图 |
| Injection must target semantic node signatures | 由于 API JSON 节点 ID 可变，必须通过 `class_type` 与 `_meta.title` 等语义特征定位节点 |
| Runtime should override only values, not graph structure | 这样才能保持 Frostbite 式数据资产与运行参数解耦 |

## Browser findings batch 3

### Source E
- Title: Introduction to Data Oriented Design - Frostbite
- URL: https://www.ea.com/frostbite/news/introduction-to-data-oriented-design
- Key findings:
  - Frostbite 明确强调数据导向设计的收益来自于简单数据布局，以及避免把性能与行为散落在复杂对象层级中。
  - 虽然页面摘要较短，但其公开定位已经足以支撑本项目的实现约束：运行时应消费结构清晰、可顺序访问的数据资产，而不是把图配置埋进后端逻辑里。

### Source F
- Title: Data Driven Rendering: Pipelines
- URL: https://jorenjoestar.github.io/post/data_driven_rendering_pipeline/
- Key findings:
  - 文中把渲染管线抽象为一系列 Pass 对 Render Target 的读写依赖描述，本质是**把图结构和资源依赖显式数据化**。
  - 这种做法的价值不在于某一组具体节点，而在于：图结构作为外部描述可被读取、验证、替换和组合，运行时代码则只负责解析和执行。
  - 这与本任务中的 ComfyUI `workflow_api.json` 资产策略高度一致：后端应消费外部 JSON 图并进行受控参数覆盖，而不是在 Python 里重建一份隐藏图。

## Consolidated research verdict

综合 ComfyUI、ControlNet、IP-Adapter 与数据驱动管线资料，本轮实现必须满足以下硬约束：第一，**节点图拓扑 100% 存放在外部 JSON 资产中**；第二，**Depth/Normal 作为几何锁定轨，IP-Adapter 作为身份锁定轨，二者分离建模**；第三，**运行时仅做语义寻址和值覆盖，不做硬编码节点 ID 注入**；第四，**测试只验证拓扑解析、路径注入、payload 生成与 manifest 契约，不触发真实 HTTP 服务器调用**。这些约束将直接指导后续代码改造与审计。 
