# MarioTrickster-MathArt 自进化数学驱动美术生产大脑架构设计

**作者**：Manus AI  
**日期**：2026-04-14  

---

## 1. 架构概述

为了实现《MarioTrickster-MathArt》项目中"用底层数学驱动统一的美术生产"并具备"自我进化"能力的愿景，我们设计了一个**三层自进化架构**。该架构不仅能够根据现有的数学模型和美术规则生成高质量的2D像素资产，还能通过内部质量反馈循环和外部知识蒸馏不断迭代升级，形成一个真正意义上的"美术生产大脑"。

整个系统分为三个核心循环/模块：
1. **内部自循环迭代（Inner Loop）**：基于生成资产的质量评估进行参数寻优。
2. **外部知识蒸馏（Outer Loop）**：从PDF、书籍、论文中提取结构化知识并注入系统。
3. **数学驱动模型库（Math Engine）**：将知识转化为可执行的数学函数和算法。

---

## 2. 核心模块设计

### 2.1 内部自循环迭代（Inner Loop）

内部循环的目标是**无监督的质量提升**。系统在生成美术资产后，自动评估其质量，并利用进化算法调整生成参数。

*   **资产生成器（Generator）**：调用数学模型库（如SDF、WFC、OKLAB）生成像素图像、动画序列或关卡布局。
*   **质量评估器（Evaluator）**：
    *   **客观指标**：使用 LPIPS（感知相似度）、NIQE（自然图像质量评估）等无参考图像质量指标评估像素画的清晰度和自然度 [1]。
    *   **风格一致性**：计算生成图像与项目基准风格图像的感知哈希（pHash）距离或特征向量余弦相似度。
    *   **规则校验**：检查是否违反 `knowledge/` 中的硬约束（如关节角度是否越界、颜色是否超出安全色域）。
*   **进化优化器（Optimizer）**：基于 `mathart/distill/optimizer.py` 中的遗传算法，将质量评估分数作为适应度函数（Fitness Function），对生成参数（如弹簧阻尼系数、SDF混合平滑度、调色板色度）进行交叉和变异，寻找最优解。

### 2.2 外部知识蒸馏（Outer Loop）

外部循环的目标是**吸收新知识，扩展系统的能力边界**。当用户上传新的美术教程、解剖学书籍或数学图形学论文时，系统自动将其转化为代码可读的规则。

*   **文档解析器（Parser）**：支持PDF、Markdown等格式。利用大语言模型（LLM）和RAG（检索增强生成）技术，从非结构化文本中提取与美术、动画、数学相关的描述 [2]。
*   **知识提炼引擎（Distiller）**：
    *   将提取的描述映射到预定义的知识领域（如 `anatomy`, `color_light`, `physics`）。
    *   将自然语言规则转化为**可参数化的约束**（如"暖光冷影" -> `shadow_hue = light_hue + 160`）。
*   **知识沉淀与代码映射（Compiler）**：
    *   更新 `knowledge/*.md` 文件，作为系统的长期记忆。
    *   通过 `mathart/distill/compiler.py`，将新规则注册到参数空间（Parameter Space）中，供内部循环的优化器使用。
    *   自动生成或更新测试用例（`tests/`），确保新知识的正确性。

### 2.3 数学驱动模型库（Math Engine）

这是系统的"肌肉"，负责将抽象的参数和规则转化为具体的视觉像素。它需要不断吸收最新的图形学和数学研究成果。

*   **程序化动画（Procedural Animation）**：
    *   实现基于胡克定律的弹簧-阻尼系统（Spring-Damper System），用于披风、头发等次级动画的物理模拟 [3]。
    *   集成2D逆运动学（IK）求解器（如FABRIK算法），实现精准的肢体放置和环境交互。
*   **色彩与渲染（Color & Rendering）**：
    *   在OKLAB感知均匀色彩空间中进行所有颜色插值和调色板生成，确保渐变自然 [4]。
    *   实现基于误差扩散（如Floyd-Steinberg）的抖动算法，在有限色深下提升像素画质感。
    *   探索可微渲染（Differentiable Rendering）的2D降维应用，通过梯度下降反向优化着色器参数 [5]。
*   **程序化内容生成（PCG）**：
    *   使用波函数坍缩（WFC）算法生成符合设计约束的关卡布局和无缝纹理 [6]。
    *   利用L-系统（L-System）和分形噪声生成植物和自然环境。
    *   使用符号距离场（SDF）进行布尔运算，生成动态特效（如火焰、水流）。

---

## 3. 系统工作流示例

1.  **触发**：用户上传了一份关于"2D像素游戏打击感"的PDF文档。
2.  **蒸馏（Outer Loop）**：
    *   解析器读取PDF，提取出"攻击命中时需要3帧的顿帧（Hit-stop）"和"屏幕震动幅度应与伤害值成正比"的规则。
    *   提炼引擎将这些规则写入 `knowledge/game_feel.md`。
    *   编译器在 `mathart/animation/presets.py` 中新增 `hit_stop_frames` 和 `screen_shake_multiplier` 参数。
3.  **生成与评估（Inner Loop）**：
    *   系统使用新参数生成一组攻击动画。
    *   评估器检查动画的流畅度和视觉冲击力。
    *   优化器微调弹簧阻尼系数和震动衰减率，直到找到最佳的视觉表现。
4.  **沉淀**：最优参数被固化为默认预设，系统能力得到永久提升。

---

## 4. 参考文献

[1] IQA-PyTorch. *Image Quality Assessment Toolbox*. https://github.com/chaofengc/IQA-PyTorch  
[2] GameTileNet. *Narrative-to-Scene Generation Pipeline*. https://github.com/RimiChen/2024-GameTileNet  
[3] Daniel Shervheim. *Unity Cloth Simulation*. https://github.com/danielshervheim/unity-cloth-simulation  
[4] Evil Martians. *OKLCH Color Picker*. https://github.com/evilmartians/oklch-picker  
[5] Voxify3D. *Orthographic Pixel Art Supervision*. https://github.com/yichuanH/Voxify3D_official  
[6] Maxim Gumin. *WaveFunctionCollapse*. https://github.com/mxgmn/WaveFunctionCollapse  
