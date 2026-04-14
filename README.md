# MarioTrickster-MathArt

**自进化数学驱动美术生产大脑** — 为 [MarioTrickster](https://github.com/jiaxuGOGOGO/MarioTrickster) 游戏项目提供可复现、可自动化的美术资产生产工具。

---

## 🌟 核心特性：三层自进化架构

本项目不仅是一个生成工具，更是一个**能够自我学习和进化的美术生产大脑**。系统分为三个核心循环：

### 1. 内循环：质量驱动的参数优化（Inner Loop）
通过 `AssetEvaluator` 自动评估生成图像的质量（清晰度、对比度、调色板贴合度、风格一致性），并使用遗传算法（`EvolutionaryOptimizer`）在约束空间内自动寻找最优参数组合。
- **无需人工调参**：系统自动迭代直到达到质量阈值。
- **多维度评估**：包含 pHash 风格一致性检测和 OKLAB 色彩和谐度分析。

### 2. 外循环：外部知识蒸馏（Outer Loop）
通过 `OuterLoopDistiller` 将外部的 PDF 书籍、Markdown 笔记或论文（如解剖学、色彩理论、物理动画公式）蒸馏为结构化的数学约束和规则。
- **知识沉淀**：提取的规则自动追加到 `knowledge/*.md` 文件中。
- **代码映射**：规则被编译为 `ParameterSpace`，直接约束内循环的生成边界。

### 3. 数学引擎：模型注册表（Math Registry）
维护所有参与生成的数学模型（如 FABRIK IK、弹簧阻尼系统、WFC、L-System），并跟踪其能力边界。
- **能力发现**：系统知道自己能做什么，缺少什么。
- **持续扩展**：支持接入新的数学模型（如可微渲染）。

---

## 📦 安装与使用

### 环境要求
- Python 3.10+
- 推荐使用虚拟环境

### 安装
```bash
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt
pip install -e ".[dev]"
```

### 命令行工具

项目提供了一系列 CLI 工具：

1. **自进化引擎**：
   ```bash
   # 查看系统状态和能力缺口
   mathart-evolve status
   
   # 从新书籍/论文中蒸馏知识
   mathart-evolve distill path/to/new_book.pdf
   
   # 查看数学模型注册表
   mathart-evolve registry
   
   # 评估单张图像质量
   mathart-evolve eval image.png
   ```

2. **基础生成工具**：
   ```bash
   mathart-palette --hue 30 --harmony complementary
   mathart-sdf --effect fire --intensity 1.5
   mathart-anim --preset run --frames 8
   ```

3. **知识编译**：
   ```bash
   mathart-distill parse --input knowledge/
   mathart-distill compile
   ```

---

## 🧠 知识库结构 (`knowledge/`)

`knowledge/` 目录包含了驱动整个系统的"大脑记忆"：
- `anatomy.md`：解剖学比例与关节约束
- `color_science.md`：OKLAB 色彩科学与调色板生成规则
- `physics_sim.md`：弹簧阻尼与二次动画物理参数
- `pcg_math.md`：程序化生成（噪声、WFC、L-System）数学基础
- `sdf_math.md`：符号距离场与光线行进公式
- `pbr_math.md`：物理基础渲染降维应用
- `procedural_animation.md`：程序化动画与缓动函数
- `unity_rules.md`：Unity 约束（PPU/Filter/Pivot/命名）

---

## 🚀 进化路线图

1. **当前阶段**：基于遗传算法的 CPU 内循环优化 + LLM 文本知识蒸馏。
2. **近期目标**：引入有限差分梯度近似，提升内循环优化速度。
3. **中期目标**：集成 PyTorch 和 nvdiffrast，实现 GPU 加速的完整 2D 可微渲染。
4. **远期目标**：接入多模态视觉模型，实现对参考图的逆向工程（Image-to-Math）。

---

## 测试

241 个测试覆盖全部模块，每次 push 自动运行 GitHub Actions CI：

```bash
pytest tests/ -v                          # 全部测试
pytest tests/test_evolution.py -v         # 自进化引擎测试
pytest tests/test_evaluator.py -v         # 质量评估器测试
pytest tests/test_physics.py -v           # 物理动画测试
pytest tests/ --cov=mathart               # 覆盖率报告
```

---

## 许可

MIT
