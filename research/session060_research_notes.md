# SESSION-060 研究笔记：第二阶段视觉 AI 工业化防抖与量产化

## 已核实资料与关键结论

### 1. Jamriška 等《Stylizing Video by Example》

来源页面：
- https://dcgi.fel.cvut.cz/home/sykorad/ebsynth.html

关键结论：
- 该方法的输入是**一个或多个由艺术家挑选并手工风格化的关键帧**，再将风格自动传播到整段序列。
- 论文明确强调其目标是同时保留**风格视觉质量、艺术家可控性、以及对任意视频的适用性**。
- 论文摘要明确指出，他们提出了适用于任意视频内容的**新型 guidance**，且除视频本身和用户指定的 stylization mask 外**不需要额外信息**。
- 论文进一步提出了**temporal blending** 用于在关键帧之间插值，并保持**texture coherence、contrast 和 high frequency details**。
- 对本项目的直接含义：MarioTrickster-MathArt 不应追求“每帧都让扩散模型重新想象”，而应追求**稀疏关键帧 + 物理引导传播** 的工业管线。

### 2. Zhang 等《Adding Conditional Control to Text-to-Image Diffusion Models》

来源页面：
- https://openaccess.thecvf.com/content/ICCV2023/html/Zhang_Adding_Conditional_Control_to_Text-to-Image_Diffusion_Models_ICCV_2023_paper.html

关键结论：
- ControlNet 的核心是给大规模预训练扩散模型添加**spatial conditioning controls**。
- 论文明确指出该结构会**锁定 production-ready 大模型**，并复用其预训练编码层作为 backbone。
- 其关键安全机制是 **zero convolutions**，它从零初始化逐步增长参数，避免微调时有害噪声破坏原模型。
- 论文验证了多种空间条件控制，包括 **edges、depth、segmentation、human pose** 等，并明确提到可在 **single or multiple conditions** 下工作。
- 对本项目的直接含义：第二阶段应将 Headless ComfyUI 从“普通双 ControlNet 模板”升级为**严格的多条件锁定工作流**，把 normal/depth/mask/motion 这类先验看作工业控制信号，而不是可选装饰。

## 当前设计判断

- 第二阶段的主线应是：**AI 仅负责稀疏关键帧材质化，时序稳定性交给物理传播链**。
- 现有 `headless_comfy_ebsynth.py` 已具备 ControlNet + EbSynth 基础，但还缺少更明确的**身份锁定层、关键帧选样策略、序列级防抖审计指标、以及桥接层持久化状态扩展**。
- 下一步应继续核实 IP-Adapter 的身份锁定角色与更接近生产实践的时序一致性资料，再决定代码落点。

## 补充核实资料与结论

### 3. IP-Adapter 官方论文

来源页面：
- https://arxiv.org/abs/2308.06721

关键结论：
- IP-Adapter 的目标是为预训练文生图扩散模型提供**image prompt capability**。
- 其核心结构是 **decoupled cross-attention**，把文本特征和图像特征的 cross-attention 分离开来。
- 论文明确强调，它只需要一个轻量适配器（文摘中为 **22M 参数**）即可获得接近或优于完全微调图像提示模型的能力。
- 更重要的是，论文明确指出：由于其**冻结预训练扩散模型**，它不仅能泛化到同底模的其他定制模型，还能够与**现有 controllable tools** 联合使用。
- 对本项目的直接含义：第二阶段完全有必要在 ComfyUI 工作流层新增 **IP-Adapter 身份锁定分支**，并将其与 ControlNet 的几何锁定分层处理：IP-Adapter 锁角色身份/外观，ControlNet 锁结构与轮廓。

### 4. FlowVid（CVPR 2024）

来源页面：
- https://openaccess.thecvf.com/content/CVPR2024/html/Liang_FlowVid_Taming_Imperfect_Optical_Flows_for_Consistent_Video-to-Video_Synthesis_CVPR_2024_paper.html

关键结论：
- 论文开宗明义指出，视频到视频扩散的一大核心难点就是**跨帧时序一致性**。
- 该方法通过**联合利用空间条件与时间光流线索**来提高一致性，说明即便在扩散视频方案里，显式时序约束仍不可缺失。
- 论文特别强调现实中的光流估计往往并不完美，因此它需要“驾驭不完美光流”。
- 对本项目的直接含义：MarioTrickster-MathArt 其实拥有更强的先天优势——我们已有程序运动学导出的**真实运动向量**。因此项目不应退化到依赖估计光流，而应把**ground-truth motion vectors** 作为比通用论文更强的工业控制信号。

## 研究汇总结论

当前已可确定第二阶段的正确工业化路线：

1. **关键帧极稀疏化**：AI 只负责少量关键帧，不负责逐帧重绘。
2. **身份与结构双锁定**：IP-Adapter 锁角色身份与纹理风格基准；ControlNet Depth/Normal/Mask 锁轮廓、几何和材质边界。
3. **物理传播而非逐帧重生**：整段序列的主体纹理传播应依赖 EbSynth 风格扩散与真实运动向量，而非逐帧扩散采样。
4. **序列级审计**：除现有 warp error 外，还应显式度量关键帧覆盖率、长程漂移、身份一致性代理指标、以及多条件锁定完整性。
5. **三层进化闭环继续保留**：第二阶段不是单纯增强 pipeline，而是要把新增的身份锁定、关键帧计划、传播审计、知识蒸馏、状态持久化全部纳入桥接与编排层。
