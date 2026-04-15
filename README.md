# MarioTrickster-MathArt

**自进化数学驱动美术生产大脑** — 为 [MarioTrickster](https://github.com/jiaxuGOGOGO/MarioTrickster) 游戏项目提供可复现、可自动化的美术资产生产工具。

---

## 核心特性：三层自进化架构

本项目不仅是一个生成工具，更是一个**能够自我学习和进化的美术生产大脑**。系统分为三个核心循环：

### 1. 内循环：质量驱动的参数优化（Inner Loop）
通过 `AssetEvaluator` 自动评估生成图像的质量（清晰度、对比度、调色板贴合度、风格一致性），并使用**遗传算法 + 有限差分梯度混合优化器**在约束空间内自动寻找最优参数组合。
- **无需人工调参**：系统自动迭代直到达到质量阈值。
- **多维度评估**：包含 pHash 风格一致性检测和 OKLAB 色彩和谐度分析。
- **安全停止策略**：当检测到停滞时，系统自动生成诊断报告并安全停止，而非无限循环。
- **FD 梯度精炼**：在遗传算法找到候选解后，使用有限差分梯度下降进一步精炼参数。

### 2. 外循环：外部知识蒸馏（Outer Loop）
通过 `OuterLoopDistiller` 将外部的 PDF 书籍、Markdown 笔记或论文（如解剖学、色彩理论、物理动画公式）蒸馏为结构化的数学约束和规则。
- **知识沉淀**：提取的规则自动追加到 `knowledge/*.md` 文件中。
- **代码映射**：规则被编译为 `ParameterSpace`，直接约束内循环的生成边界。

### 3. 数学引擎：模型注册表与社区发现（Math Registry + Community Mining）
维护所有参与生成的数学模型（如 FABRIK IK、弹簧阻尼系统、WFC、L-System），并跟踪其能力边界。
- **能力发现**：系统知道自己能做什么，缺少什么。
- **持续扩展**：支持接入新的数学模型（如可微渲染）。
- **社区源搜索**：除 arXiv 和 GitHub 外，还支持 Papers with Code、Shadertoy、LLM 辅助建议。
- **毕业工作流**：挖掘到的模型经过 candidate → experimental → stable 三阶段验证后才正式纳入。

---

## 核心生产能力 (v0.19.0)

### 角色基因型进化系统 (Character Evolution 2.5)
系统实现了基于语义的层次化角色生成：
- **Archetype (原型)**：定义角色的语义身份（英雄、反派、NPC、怪物）。
- **Body Template (体型模板)**：定义身体比例和可用插槽（如 humanoid_standard, creature_round）。
- **Part Registry (部件注册表)**：管理所有可装备部件（帽子、面部配件等）及其兼容性。
- **3层变异算子**：支持结构变异（替换部件）、比例微调和调色板变异。
- **精英交叉**：支持在进化过程中交换角色的优秀基因。

### 资产管线 (Asset Pipeline)
提供端到端的资产生产能力：
- `produce_character_pack()`：生成包含多状态（idle, run, jump, fall, hit）的角色精灵图、动画 GIF 和元数据清单。
- `produce_texture_atlas()`：将多个精灵打包为纹理图集。
- `produce_vfx()`：生成基于 SDF 和粒子的视觉特效。

---

## 安装与使用

### 环境要求
- Python 3.10+
- 推荐使用虚拟环境

### 安装
```bash
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt
pip install -e ".[dev]"
```

### 快速开始：生成进化角色包

```python
from mathart.pipeline import AssetPipeline, CharacterSpec

pipeline = AssetPipeline(output_dir="output/", seed=7)

# 使用基因型模式生成并进化角色
character = pipeline.produce_character_pack(
    CharacterSpec(
        name="evolved_mario",
        preset="mario",
        use_genotype=True,  # 激活语义基因型进化
        frames_per_state=6,
        states=["idle", "run", "jump", "fall", "hit"],
        evolution_iterations=5,
        evolution_population=6,
        evolution_crossover_rate=0.25,
    )
)
```

### 命令行工具

项目提供了一系列 CLI 工具：

1. **自进化引擎**：
   ```bash
   # 查看系统状态和能力缺口
   mathart-evolve status

   # 运行进化（支持多种目标类型）
   mathart-evolve run --target texture --generations 50
   mathart-evolve run --target sprite --generations 30
   mathart-evolve run --target animation --generations 20
   ```

2. **资产导出**：
   ```bash
   # 从关卡规格导出资产
   mathart-export --from-level level_spec.json --output-dir ./exports
   ```

3. **基础生成工具**：
   ```bash
   mathart-palette --hue 30 --harmony complementary
   mathart-sdf --effect fire --intensity 1.5
   mathart-anim --preset run --frames 8
   ```

---

## 知识库结构 (`knowledge/`)

`knowledge/` 目录包含了驱动整个系统的"大脑记忆"：
- `anatomy.md`：解剖学比例与关节约束
- `color_science.md`：OKLAB 色彩科学与调色板生成规则
- `physics_sim.md`：弹簧阻尼与二次动画物理参数
- `pcg_math.md`：程序化生成（噪声、WFC、L-System）数学基础
- `sdf_math.md`：符号距离场与光线行进公式
- `pbr_math.md`：物理基础渲染降维应用
- `procedural_animation.md`：程序化动画与缓动函数
- `unity_rules.md`：Unity 约束（PPU/Filter/Pivot/命名）
- `differentiable_rendering.md`：有限差分梯度与可微渲染路线图

---

## 进化路线图

1. **已完成**：基于遗传算法的 CPU 内循环优化 + LLM 文本知识蒸馏 + 有限差分梯度精炼。
2. **已完成**：Image-to-Math 参数推断（从参考图像推断初始参数种子）。
3. **已完成**：多目标进化 CLI（texture / sprite / animation）。
4. **已完成**：社区源扩展（Papers with Code / Shadertoy / LLM Advisor）。
5. **已完成**：角色包管线集成与多状态精灵生成。
6. **已完成**：角色进化 2.5（语义基因型系统、部件注册表、交叉算子）。
7. **近期目标**：WFC 关卡生成管线集成、着色器导出管线集成、生产基准测试套件。
8. **远期目标**：集成 PyTorch 和 nvdiffrast，实现 GPU 加速的完整 2D 可微渲染。

---

## AI 协作协议

本项目包含专门为 AI 代理设计的协作协议，以确保高效迭代：
- `SESSION_PROTOCOL.md`：会话效率规则和防重复流程。
- `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md`：高价值外部参考的精准并行搜索机制。
- `SESSION_HANDOFF.md`：会话交接文档，记录最新状态和优先级。
- `PROJECT_BRAIN.json`：机器可读的全局状态和待办列表。

---

## 测试

538 个测试覆盖全部模块，每次 push 自动运行 GitHub Actions CI：

```bash
pytest tests/ -v                          # 全部测试
pytest tests/test_genotype.py -v          # 基因型系统测试
pytest tests/test_evolution.py -v         # 自进化引擎测试
pytest tests/test_fd_gradient.py -v       # FD 梯度优化器测试
pytest tests/ --cov=mathart               # 覆盖率报告
```

---

## 许可

MIT
