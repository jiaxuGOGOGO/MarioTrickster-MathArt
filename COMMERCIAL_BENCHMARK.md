> **核心原则**：本项目的所有迭代和进化，必须始终以本文件定义的商业标准为最终参考坐标系。任何技术升级如果不能缩小这里的百分比差距，即被视为"流于形式"。

## 一、 核心愿景与技术哲学

本项目的核心愿景是：**用数学/物理模拟驱动出"符合人类视觉认知的运动"，以此作为数字大脑，后续再通过AI模型（如GPU+ComfyUI/Wan2.2）进行视觉润色。**

**核心哲学：数学保证"动得对" → AI保证"看得美"**

纯靠AI扩散模型（如Wan2.2）生成的运动往往缺乏物理真实感和逻辑连贯性，难以满足人类视觉对运动的苛刻要求。因此，本项目的首要任务是建立一个强大的、基于物理和认知的**程序化运动引擎**，在运动达标的基础上，再追求像素级的视觉美化。

## 二、 商业像素素材的基准线

通过对业界顶尖 AI 像素生成工具（如 PixelLab [1]）和 itch.io 畅销商业素材包（如 Tiny Swords [2]）的调研，一个达到商业可用标准的像素资产包必须具备以下特征：

### 1. 动画与运动的物理真实感（本项目当前最高优先级）
- **多状态覆盖**：包含 Idle, Run, Attack, Hit, Death 等标准游戏状态。
- **物理驱动的动态响应**：运动不应是死板的关键帧，而应能对速度、碰撞、重力做出动态响应（如基于Verlet积分的布娃娃系统 [3]）。
- **二次动画（Secondary Motion）**：衣服、头发、武器等附属物必须有基于弹簧-质点系统（Mass-Spring System）的自然跟随和重叠运动 [4]。
- **程序化步态与IK**：行走和奔跑应由逆运动学（IK）解算器驱动，确保脚部与地面的精确接触，而非滑动 [5]。
- **符合认知的运动曲线**：加减速必须遵循物理规律和人类视觉预期，广泛应用非线性缓动函数（Easing Functions）[6]。

### 2. 视觉表现力与一致性（后续AI润色的目标）
- **高辨识度**：角色轮廓清晰，像素点放置精准，无多余噪点。
- **风格统一**：整个素材包（角色、环境、UI）在色彩、光影、线条粗细上保持绝对一致。
- **多方向视图**：至少支持 4 方向（上下左右）或 8 方向视图。

### 3. 资产广度（世界构建）
- **角色多样性**：包含多种职业和不同阵营的敌人。
- **环境与地形**：提供可无缝拼接的瓦片集（Tilesets）。
- **物理驱动的视觉特效（VFX）**：攻击特效、爆炸、烟尘、水花必须由粒子物理系统驱动，与角色运动状态强绑定 [7]。

### 4. 引擎就绪度
- **格式标准**：提供 PNG 精灵图集和原始工程文件。
- **元数据**：包含切片信息、动画帧率、碰撞体边界等，可直接导入 Unity/Godot。

---

## 三、 MarioTrickster-MathArt 当前能力评估 (v0.19.0)

**总体商业完成度评估：约 25% - 30%**

| 评估维度 | 商业标准要求 | 本项目当前现状 | 完成度 | 核心瓶颈 |
| :--- | :--- | :--- | :--- | :--- |
| **动画物理真实感** | 物理驱动，有二次动画，IK步态 | 6帧/状态，纯骨骼变换驱动，无物理模拟，无弹簧二次动画 | **15%** | **最大短板**：缺少物理引擎（Verlet/弹簧）和IK解算器 |
| **运动认知合理性** | 遵循动画12原则，非线性缓动 | 线性插值居多，动作僵硬，缺乏预期和跟随 | **20%** | 缺少基于认知科学的运动曲线和适应度约束 |
| **角色视觉质量** | 手绘级/AI生成级，像素精准 | 基于 SDF 数学原语组合，偏向技术 Demo | **20%** | SDF 渲染的硬上限，需等待运动达标后引入AI润色 |
| **特效与粒子** | 物理驱动，与动作强绑定 | VFX 管线存在，但未与角色物理状态（速度/碰撞）绑定 | **20%** | 缺乏统一的物理事件触发机制 |
| **角色多样性** | 20+ 差异巨大的角色与敌人 | 基因型系统支持变异，但部件库极小 | **15%** | 部件注册表（Part Registry）内容匮乏 |
| **环境与地形** | 完整可拼接瓦片集，多高度层 | WFC 模块存在，但未接入主生产管线 | **10%** | 架构断裂，`produce_level()` 缺失 |
| **导出与集成** | 引擎就绪的图集与元数据 | 导出模块存在，但未作为管线一等公民 | **15%** | 架构断裂，Export 模块未集成 |
| **工程自动化** | 一键生成或高度易用的工具链 | 管线自动化程度高，支持遗传算法自进化 | **60%** | 本项目的最大优势，"大脑"强于"手" |

---

## 四、 缩小差距的战略路径（重构版）

基于"数学驱动运动 → AI润色视觉"的核心愿景，我们重新定义了技术演进的战略路径。当前的绝对重心是**路径A（物理与运动引擎）**。

### 路径 A：构建程序化物理运动引擎（当前最高优先级）
彻底重构当前的6帧骨骼动画系统，引入真实的物理和数学模拟，解决"动得不对"的根本问题。
- **A1. Verlet积分物理动画**：引入质点-约束系统，让角色整体运动受重力、碰撞和外力驱动 [3]。
- **A2. 弹簧-质点二次运动**：实现胡克定律和阻尼模型，为头发、衣物添加自然的物理跟随（Jiggle Physics）[4]。
- **A3. IK与程序化步态**：集成FABRIK等2D IK解算器，实现无关键帧的、适应地形的真实步态 [5]。
- **A4. 认知运动约束**：将人类视觉感知规律（如相位关系、预期动作）转化为数学约束，融入遗传算法的适应度函数 [8]。

### 路径 B：数学运动 + AI视觉润色的混合管线（中期目标）
在路径A实现"运动达标"后，解决SDF视觉表现力的天花板。
- **核心方法**：将路径A生成的精确物理运动数据（骨骼轨迹、物理粒子位置）作为条件输入（如ControlNet的OpenPose/Depth图）。
- **渲染环节**：调用外部强大的AI扩散模型（如配置了特定LoRA的ComfyUI工作流，或Wan2.2等视频模型）进行最终的像素级视觉渲染 [9]。
- **优势**：完美结合了数学的确定性/物理真实感与AI的极致视觉表现力。

### 路径 C：架构闭环与资产广度（并行推进）
将项目中已有的"孤岛"模块接入主生产管线，提升资产包的完整度。
- **核心方法**：修复架构断裂，将 WFC（关卡生成）、Export（引擎导出）和 Shader（着色器）模块正式接入 `AssetPipeline`。
- **特效升级**：将现有的SDF特效升级为受物理引擎（重力、风力）驱动的粒子系统 [7]。

---

## 参考文献

[1] PixelLab. "AI Generator for Pixel Art Game Assets." *PixelLab.ai*, 2026. https://www.pixellab.ai/
[2] Pixel Frog. "Tiny Swords - 2D Environments and Characters." *itch.io*, 2026. https://pixelfrog-assets.itch.io/tiny-swords
[3] Pikuma. "Verlet Integration and Cloth Physics Simulation." *Pikuma Blog*, 2023. https://pikuma.com/blog/verlet-integration-2d-cloth-physics-simulation
[4] Jessica Ione. "3D mass-spring systems for physics-based animation." *GitHub*, 2022. https://github.com/jessicaione101/3d-mass-springs
[5] Sean. "FABRIK Algorithm (2D)." *sean.fun*, 2021. https://sean.fun/a/fabrik-algorithm-2d/
[6] Robert Penner. "Robert Penner's Easing Functions." *robertpenner.com*, 2001. http://robertpenner.com/easing/
[7] SideFX. "Procedural Thinking." *SideFX Tutorials*, 2024. https://www.sidefx.com/tutorials/procedural-thinking/
[8] Barbara Tversky et al. "Animation: can it facilitate?" *International Journal of Human-Computer Studies*, 2002.
[9] Illyasviel. "ControlNet: Adding Conditional Control to Text-to-Image Diffusion Models." *GitHub*, 2023. https://github.com/lllyasviel/ControlNet
