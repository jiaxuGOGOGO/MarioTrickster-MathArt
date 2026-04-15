# SESSION-024 去重并行研究综合笔记

> 目的：围绕当前最高优先级差距，补充**未被项目吸收过**、且对下一次对话最有帮助的参考资料，避免再次泛化调研或重复吸收旧材料。

## 研究范围与去重原则

本轮研究明确避开了项目已经吸收过的基础资料，包括 CPPN、Picbreeder、OKLAB、MVC、Quilez SDF、Disney 12 动画原则、Verlet、Floyd-Steinberg、一般性多状态角色动画参考、Pedro Medeiros / Lospec / Aseprite 等。新的研究集中在 **角色语义变异空间、生产级 benchmark、WFC 一等公民化、Shader/Export 闭环、SDF 帧级动画与二次运动/有机纹理** 五个剩余关键差距上。

## 一、角色进化 2.5：从参数微扰走向语义变异空间

当前角色进化 2.0 基础版已经具备角色专用评分、精英保留和停滞恢复，但它仍主要在比例、局部风格参数与调色板附近搜索。真正限制上限的，是**变异空间还不够“语义化”**。

| 方向 | 新资料带来的新增价值 | 对项目的直接启发 |
|------|----------------------|------------------|
| 结构语法 / 拓扑生成 | 将角色生成从数值抖动推进到骨架级、部件级规则生成 | 可以把角色 archetype、头身、肢体、背包、帽子、裙摆等抽象成 grammar / template 组合 |
| 程序化服饰与配件 | 说明服装不是贴图后处理，而可以作为参数化层级系统单独进化 | 可把 clothing/accessory 变成 `CharacterSpec` 的独立变异层 |
| 原型驱动变异 | 证明“骑士/法师/工人/怪物”等原型可以做成高层语义控制而非低层噪声 | 可以新增 `archetype`, `garment_layers`, `equipment_slots` 等语义字段 |

其中对下一次会话最有用的参考主要有三类。第一类是参数化模型与变体提取研究，它提示项目不应只做像素级突变，而应构造可迁移的结构化变异空间 [1]。第二类是程序化服装工作流，它证明服装/配件可以作为独立的规则层和曲线层接入，而不是临时绘制补丁 [2]。第三类是最近的角色行为/原型控制研究，它虽然来自更宽泛的生成领域，但给了“高层角色语义参数”的设计灵感 [3]。

下一步最值得落地的，不是继续微调评分，而是做一个 **Character Mutation Space Prototype**：先把 archetype、身体模板、服饰层、配件槽位做成可组合语义空间，再让当前已有的进化器在这个更有意义的空间里搜索。

## 二、生产级 Benchmark Asset Suite：定义“什么叫真的够好”

项目当前最大制度性缺口之一，是**没有生产级 benchmark 资产套件**。没有 benchmark，就算搜索分数更高，也仍可能只是“更会优化自己的评分器”，而不是真正接近你要的资产质量。

| 方向 | 新资料带来的新增价值 | 对项目的直接启发 |
|------|----------------------|------------------|
| Tileset 语义数据集 | 给了低分辨率游戏图块的层级语义标注方式 | 可为 tileset / props / biome 建立标准化 metadata |
| VFX 动态质量指标 | 给了时序强度与时间区间对齐这样的动态评价维度 | 可把 VFX benchmark 从静态截图扩展到时序验收 |
| 实际可用角色资产基准 | 提供真实可消费资产的部件拆分与状态覆盖参考 | 可构建内部 benchmark pack，而不是只凭抽象审美打分 |

`GameTileNet` 提供了低分辨率游戏艺术资产的层级语义组织方式，尤其适合作为 tileset benchmark 的元数据骨架 [4]。`VFX Creator` 虽然是生成模型研究，但它明确强调了动画效果的可控性与动态行为评价，这对本项目未来做 VFX 验收很有参考价值 [5]。`Universal LPC Spritesheet Character Generator` 则提供了一个接近“真实生产资产族”的角色层次和组合思路，适合反推 benchmark 维度，例如装备槽位、一致骨架、状态覆盖率与重用部件设计 [6]。

下一次对话应优先考虑建立一个 **benchmark_assets/** 目录及其 schema，至少覆盖角色、tileset、VFX 三类，每类都有样例、最低可接受阈值、自动检测项和目标元数据字段。

## 三、WFC / Tileset 一等公民化：从模块存在走向主管线闭环

WFC 已经作为模块存在，但仍不是主管线的一等公民。当前真正缺的不是“还能不能生成”，而是**能否以可控、可审计、可打包的方式进入 AssetPipeline**。

| 方向 | 新资料带来的新增价值 | 对项目的直接启发 |
|------|----------------------|------------------|
| 实用化 WFC 扩展 | 展示了比基础 WFC 更适合生产的模块依赖、零权重和多格结构支持 | 可把大块结构、依赖模块、空模块纳入规则系统 |
| 全局约束 | 解决 WFC 常见的局部合法但整体不可玩问题 | 可在生成后增加连通性/路径/无环等约束校验 |
| 多阶段管线 | 说明地形拓扑、细节填充、资产摆放应拆成多 pass | 可把 `produce_level()` 设计成 topology → wfc fill → prop placement 三阶段 |

`Tessera` 非常关键，因为它不只是讲 WFC 原理，而是面向实际生产，强调模块、约束和编辑流程 [7]。相关学位论文也提供了更清晰的资产生成角度，即如何让 WFC 与资产组合逻辑协同，而不是只生成一张拼贴图 [8]。Caves of Qud 的 GDC 分享则提供了实战视角：WFC 真正有价值之处，在于作为关卡结构约束和内容布置的一环，而不是孤立技术秀 [9]。

下一次对话的高价值任务，是把 WFC 从“存在于 `mathart/level/`”推进为 `AssetPipeline.produce_level()`，并明确输出 level PNG、tile legend、adjacency metadata、playability checks 和导出 manifest。

## 四、Shader + Export 闭环：让产物成为真正可消费资产

当前仓库已经能生成很多东西，但仍存在一个现实问题：**生成结果还不够像“真正面向引擎消费的资产包”**。这也是为什么 Shader 与 Export 必须被提升到一等公民，而不是继续作为边缘模块。

| 方向 | 新资料带来的新增价值 | 对项目的直接启发 |
|------|----------------------|------------------|
| Shader 烘焙与运行时参数分离 | 说明可以把复杂程序化部分预烘焙，同时保留少量动态 Uniform | 可让导出资产既轻量又保留可调性 |
| Pixel 风渲染管线实例 | 提供像素风渲染目标、缩放、后处理与图集输出思路 | 可帮助设计 shader artifact + metadata 结构 |
| 与 WFC/图集的联动 | 说明材质、图块、metadata 可以属于同一资产包 | 可把 shader/export 不再做成孤立脚本 |

`shader-texture-graph` 的价值在于它明确展示了“哪些东西该烘焙成贴图，哪些东西该保留为运行时参数”的思路 [10]。`PixelRenderUnity3D` 提供了像素风渲染落地的工程视角 [11]。`mxgmn/WaveFunctionCollapse` 虽然基础，但在这里的价值不是重新学习 WFC，而是提醒我们 tileset/shader/export 应从一开始就被看成同一条资产链上的步骤 [12]。

下一次对话可以考虑新增一个 `produce_shader_bundle()` 或统一的 `export_engine_bundle()`，其输出至少包含：基础纹理、调色板、shader 参数 JSON、atlas 索引、动画元数据与可选预览图。

## 五、SDF 帧级动画、二次运动与有机纹理：提升“生命力”和“材料感”

项目已经有较强的几何资产生成基础，但离真正让动画“活起来”、让材质“像东西”还差三块：**帧级 SDF 参数动画、二次运动系统、有机纹理系统**。

| 方向 | 新资料带来的新增价值 | 对项目的直接启发 |
|------|----------------------|------------------|
| 帧级 SDF 参数动画 | 说明 SDF 不应只做静态形状 + 变换，而应直接驱动形体参数随时间变化 | 可新增 keyframed parameter tracks 与 morphing |
| 2D 二次运动 | 提供角色附属物拖尾、摆动、回弹的数学模型 | 可为帽子、围巾、尾巴、特效挂件加入 spring follow-through |
| 反应扩散纹理 | 让材质从普通噪声升级为具有生长/侵蚀感的图案系统 | 可在 SDF / shader 管线中新增 organic mask 生成能力 |

`Secondary Motion for Performed 2D Animation` 对本项目很关键，因为它不是泛泛讲动画原则，而是直接给出 2D 二次运动的可计算框架 [13]。`ALICE-SDF` 给出了一种把 SDF 参数和时间轴更紧密结合的思路 [14]。`RDSystem` 则为反应扩散提供了可直接参考的实时有机纹理生成路线 [15]。

下一次对话若希望继续提升“不是演示，而是真能产出好东西”的能力，可以优先选这条线：先实现 **parameter tracks + spring attachments + reaction-diffusion mask** 三件套，再把它们接到现有角色或 VFX 生成流程中。

## 六、建议写入下一次对话的任务排序

| 排名 | 建议任务 | 原因 |
|------|----------|------|
| 1 | 角色进化 2.5：语义变异空间原型 | 当前最限制上限的仍是 mutation space 太窄 |
| 2 | 建立 benchmark asset suite | 没有 benchmark 就无法判断“进化是否真的更好” |
| 3 | WFC 正式接入主管线 | 这是从单资产走向游戏内容生态的必要一步 |
| 4 | Shader / Export 一等公民化 | 这是让产物真正下游可消费的关键闭环 |
| 5 | SDF 帧级动画 + 二次运动 + 有机纹理 | 这是提升观感生命力和材料感的核心工程线 |

## References

[1]: https://link.springer.com/article/10.1007/s00371-018-1589-4 "Automatic procedural model generation for 3D object variation"
[2]: https://80.lv/articles/fibric-procedural-clothing-creation-with-curves-in-houdini "Fibric: Procedural Clothing Creation With Curves In Houdini"
[3]: https://arxiv.org/abs/2601.03396 "Modeling Behavioral Variation in LLM Based Procedural Character Generation"
[4]: https://arxiv.org/html/2507.02941v2 "GameTileNet: A Semantic Dataset for Low-Resolution Game Art in Procedural Content Generation"
[5]: https://arxiv.org/html/2502.05979v4 "VFX Creator: Animated Visual Effect Generation with Controllable Diffusion Transformer"
[6]: https://liberatedpixelcup.github.io/Universal-LPC-Spritesheet-Character-Generator/ "Universal LPC Spritesheet Character Generator"
[7]: https://www.boristhebrave.com/permanent/21/08/Tessera_A_Practical_System_for_WFC.pdf "Tessera: A Practical System for Extended WaveFunctionCollapse"
[8]: https://is.muni.cz/th/ogid5/thesis.pdf "Wave Function Collapse Asset Generation (Thesis)"
[9]: https://gdcvault.com/play/1025913/Math-for-Game-Developers-Tile "Math for Game Developers: Tile-Based Map Generation using Wave Function Collapse in Caves of Qud"
[10]: https://github.com/lassiiter/shader-texture-graph "shader-texture-graph"
[11]: https://github.com/kode80/pixelrenderunity3d "PixelRenderUnity3D"
[12]: https://github.com/mxgmn/WaveFunctionCollapse "WaveFunctionCollapse"
[13]: https://gfx.cs.princeton.edu/gfx/pubs/Willett_2017_SMF/paper.pdf "Secondary Motion for Performed 2D Animation"
[14]: https://github.com/ext-sakamoro/ALICE-SDF "ALICE-SDF"
[15]: https://github.com/keijiro/RDSystem "RDSystem (Reaction-Diffusion)"
