# SESSION-188 外网参考研究笔记

## 研究日期: 2026-04-24

## 1. Kinematic Retargeting for Multi-Species Topologies (多物种拓扑运动学重定向)

### 学术参考
- **AnyTop (Gat et al., 2025, arXiv:2502.17327)**: Diffusion framework for generating motions for arbitrary skeletal structures. Key insight: embed each joint independently at each frame, apply attention along both temporal and skeletal axes. Topology conditioning via graph characteristics (parent-child relations) integrated into attention maps. Textual joint descriptions bridge similarly-behaved parts across different skeletons.
- **Spatio-Temporal Motion Retargeting for Quadruped Robots (Yoon et al., 2025, IEEE)**: Two-stage approach — kinematic retargeting first, then dynamic adaptation. Common skeleton concept as intermediate latent space for cross-topology mapping.
- **Dog Code: Human to Quadruped Embodiment (Egan et al., 2024, ACM)**: Shared codebooks between human and quadruped skeletons. Dynamic state switching between bipedal and quadrupedal stances based on action detection.
- **Motion Strategy Generation for Quadruped Robots (Zhang et al., 2026, Biomimetics)**: Multimodal motion primitives with mapping between skeleton and mechanical topology.

### 工业参考
- **Unreal Engine 5 IK Rig**: Supports biped and quadruped retargeting via IK Rig editor. Bone chain mapping between different skeleton topologies.
- **Autodesk MotionBuilder**: Native biped/quadruped characterization with floor contact on all four limbs.
- **Reallusion**: Dynamic 3D characterization map for quadruped and custom characters.

### 落地策略
- 在 BackendRegistry 中注册独立的 `quadruped_physics_backend` 模块
- 使用 topology-aware 分支调度：在 Orchestrator/Weaver 层根据 `skeleton_topology` 字段动态切换
- 保持双足引擎内部零修改，所有切换逻辑在外部装配层完成

## 2. Data-Oriented Pipeline Bridging (面向数据的管线真实桥接)

### 学术参考
- **SideFX Houdini VAT 3.0**: Vertex Animation Texture 标准 — 将复杂动画预烘焙到纹理中，通过着色器在运行时回放。Float32 精度用于位置数据编码。Global Bounding Box Quantization 用于归一化。
- **PhysAnimator (Xie et al., 2025, CVPR)**: Physics-guided generative cartoon animation. External forces mapped to deformable mesh vertices.

### 工业参考
- **SideFX Labs VAT 3.0 ROP**: 标准 VAT 导出节点，支持 Soft Body / Rigid Body / Fluid / Sprite 四种模式。Float16/Float32 精度选择。Hi-Lo 16-bit PNG 编码用于无 HDR 支持的引擎。
- **OpenVAT for Blender**: 开源 VAT 烘焙工具，支持自定义 C# 编辑器工具进行位置数据编码。

### 落地策略
- VAT Backend 的 `execute()` 方法中，当 `context["positions"]` 存在时，直接消费真实物理蒸馏数据
- 当 `positions` 为 None 时才回退到 Catmull-Rom 合成数据（仅用于独立测试）
- 添加动态 Reshape 逻辑处理四足/双足关节数不匹配问题
- 确保 Numpy Shape 严格对齐：`(frames, vertices, channels)`

## 3. LLM as Orchestrator / Multi-Agent Patterns

### 学术参考
- **Multi-Agent LLM Orchestration (arXiv:2511.15755)**: Multi-agent orchestration coordinating specialized LLM agents for diagnosis, planning, and risk assessment.
- **Training-Free Multimodal LLM Orchestration (Xie et al., 2025, arXiv:2508.10016)**: LLM leverages inherent reasoning capabilities to dynamically analyze user intent and dispatch to specialized agents.

### 工业参考
- **Azure AI Agent Patterns (2026)**: 编排器 → 插件选择 → 执行的三阶段模式
- **Martin Fowler IoC / Dependency Injection**: BackendRegistry 作为 IoC 容器
- **ASP.NET Core Middleware Pipeline**: 中间件链模式
- **Beam AI Multi-Agent Orchestration Patterns (2026)**: Dispatcher sends work out, collector aggregates results

### 落地策略
- 在 SemanticOrchestrator 系统提示词中添加 `skeleton_topology` 字段推断
- LLM 输出结构中新增 `skeleton_topology: "biped" | "quadruped"` 字段
- 语义触发映射中添加四足生物关键词（如"机械狗"、"四足"、"奔跑的狗"等）

## 4. Ruthless Prioritization & Technical Debt Pruning

### 参考
- **"It's Not Difficult to Prioritize — You're Just Not Ruthless Enough" (Medium, 2024)**: 聚焦核心商业化 ROI，砍掉脱离当前目标的过度优化
- **Atlassian Agile Technical Debt Guide**: 在 sprint 中优先处理和解决技术债务

### 落地策略
- 彻底删除所有 CI 验证报表相关待办
- 彻底删除"复活全部孤儿桥"相关待办
- 精准聚焦：仅打捞四足相关代码
