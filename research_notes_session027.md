# SESSION-027 角色语义变异空间深度研究笔记

> **触发原因**: 针对 P1-NEW-9B (角色进化 2.5：语义变异空间) 差距，为避免直接编码流于形式（仅增加数值参数），启动精准并行研究协议，寻找程序化角色部件系统、结构化变异算子和混合参数优化的具体实现级参考。

## 1. 避免重复研究边界

本轮研究明确避开了项目已经吸收过的基础资料，包括：
- CPPN / Picbreeder / MAP-Elites 基础原理
- OKLAB 色彩空间
- Inigo Quilez SDF 基础函数
- Disney 12 动画原则与 Verlet 物理
- 一般性多状态角色动画参考
- SESSION-025 已引用的概念级文献（如 GameTileNet, VFX Creator 等）

## 2. 核心发现与实现模式

通过对理论算法、代码实现、进化搜索、生产实践和资产标准五个维度的并行搜索，我们提取了以下可以直接指导 `MarioTrickster-MathArt` 升级的具体实现模式：

### 2.1 层次化组件模型 (Hierarchical Component Model)

当前项目的 `CharacterSpec` 和 `CharacterStyle` 主要是一个扁平的数值向量，这限制了变异的语义深度。研究表明，生产级的程序化角色生成系统（如 Embark Studios 的实践 [1] 和 Spiritus 框架 [2]）普遍采用层次化的组件模型。

**具体实现思路**：
角色不应再被表示为一组全局参数，而应被表示为一个树状的组件图（类似于简化版的 USD 场景图或 ECS 架构中的实体-组件关系 [3]）。根节点定义角色的“原型”（Archetype），子节点定义具体的“部件槽位”（如头部、躯干、配件）。每个部件独立维护其自身的 SDF 生成逻辑和局部参数。

### 2.2 混合参数编码与变异 (Mixed-Parameter Encoding)

角色语义变异空间必然包含离散选择（如：帽子类型、身体模板）和连续参数（如：肢体长度、颜色值）。传统的进化算法在处理这种混合空间时往往效率低下。

**具体实现思路**：
参考《Mixed Integer-Discrete-Continuous Optimization by Differential Evolution》[4] 的模式，我们可以将所有参数（离散+连续）统一编码为一个连续的浮点数向量（基因型）。在适应度评估阶段，通过一个解码器将连续值映射回离散的部件选择或具体的数值参数（表型）。这种“结构在语法，参数在向量”的分离，使得底层的进化算法（如现有的 CMA-ES 或 MAP-Elites）可以无需修改地在更复杂的语义空间中搜索。

### 2.3 语法引导的结构组装 (Grammar-Guided Assembly)

为了确保随机变异或交叉操作不会产生结构上无效的角色（例如：把帽子长在脚上），需要引入形式化的组合规则。

**具体实现思路**：
借鉴形状语法（Shape Grammar）[5] 的概念，我们可以实现一个基于“槽位”（Slots）和“标签”（Tags）的兼容性系统。每个部件定义它提供的连接点（如 `head` 提供 `hat_slot`）和它需要的连接点。变异算子在替换部件时，必须遵守这些语法约束，从而保证生成的角色在语义上是合理的。

## 3. 对 MarioTrickster-MathArt 的具体代码影响

基于上述研究，下一次代码迭代应重点修改以下模块：

1. **重构 `CharacterSpec`**：将其从扁平的数据类升级为包含 `Archetype`、`BodyTemplate` 和 `PartSlots` 的层次化结构。
2. **引入 `PartRegistry`**：建立一个集中的部件注册表，管理所有可用的 SDF 部件及其兼容性规则。
3. **升级变异算子**：在 `mathart/pipeline.py` 中，将单纯的数值微扰扩展为支持“部件替换”（Swap Part）和“模板切换”（Change Template）的结构化变异。
4. **实现基因型解码器**：编写一个将连续进化向量映射为层次化 `CharacterSpec` 的转换层。

## 4. 建议的下一步实现路径

1. **第一步**：定义 `CharacterGenotype` 数据结构和 `PartRegistry`，这是整个语义变异空间的基础。
2. **第二步**：重构现有的 `character_presets.py`，将其中的硬编码预设转换为基于新注册表的组件组合。
3. **第三步**：修改进化管线，使其能够在这个新的、更丰富的语义空间中进行搜索和变异。

## 5. 参考文献

[1] Embark Studios. "Game Character Pipelines at Embark: Freedom Through Structure." GDC 2026. https://www.youtube.com/watch?v=UFeC-VBbO90
[2] "Spiritus: An AI-Assisted Tool for Creating 2D Characters and Animations." arXiv:2503.09127. https://arxiv.org/abs/2503.09127
[3] "Composition of sprites on an entity." Bevy Engine Discussions. https://github.com/bevyengine/bevy/discussions/2870
[4] "Mixed Integer-Discrete-Continuous Optimization by Differential Evolution." https://cse.engineering.nyu.edu/~mleung/CS4744/f04/ch06/DE.PDF
[5] "A shape grammar approach to computational creativity and procedural content generation in massively multiplayer online role playing games." https://www.researchgate.net/publication/257708816_A_shape_grammar_approach_to_computational_creativity_and_procedural_content_generation_in_massively_multiplayer_online_role_playing_games
