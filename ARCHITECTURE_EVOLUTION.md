# MarioTrickster-MathArt 自进化架构设计文档

> **版本**：v0.3.0 | **作者**：Manus AI | **更新日期**：2026-04-14

---

## 一、设计哲学

本项目的核心目标是构建一个**不依赖大量外部工具、能够自我迭代进化的美术生产大脑**。其设计哲学来源于以下三个洞察：

**洞察一：数学知识是美术质量的底层保证**。无论是人体比例（黄金比例、头身比）、色彩和谐（OKLAB 感知均匀空间）还是物理动画（弹簧阻尼 ODE），高质量美术背后都有严格的数学约束。将这些约束编码到生成参数空间中，就能从根本上避免"不对劲"的输出。

**洞察二：知识蒸馏贯穿迭代全过程，而非只是结果**。传统方法是先生成、再评估、再修改。本项目的方式是：在生成之前，知识约束已经限定了参数空间的边界（外循环）；在生成过程中，质量评估器实时反馈（内循环）；生成结束后，新的知识再次更新约束（下一轮外循环）。

**洞察三：暴露能力边界比假装无所不能更重要**。当系统遇到需要 GPU 加速可微渲染、或需要新的外部工具才能满足的需求时，应该明确地报告出来，让用户能够做出有针对性的外部升级决策。

---

## 二、三层架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                    外部知识输入（用户驱动）                        │
│  PDF书籍 / 论文 / Markdown笔记 / 游戏美术参考                     │
└────────────────────────┬────────────────────────────────────────┘
                         │ mathart-evolve distill <file>
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  外循环：知识蒸馏引擎（Outer Loop）               │
│                                                                 │
│  OuterLoopDistiller                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    │
│  │ 文本解析器   │ →  │ LLM规则提取  │ →  │ knowledge/*.md  │    │
│  │ (PDF/MD/TXT)│    │ (gpt-4.1-mini)│    │ 知识库追加更新   │    │
│  └─────────────┘    └──────────────┘    └────────┬────────┘    │
│                                                  │             │
│                                         ┌────────▼────────┐    │
│                                         │ ParameterSpace  │    │
│                                         │ 约束编译（规则→  │    │
│                                         │ 参数边界）       │    │
│                                         └────────┬────────┘    │
└──────────────────────────────────────────────────┼─────────────┘
                                                   │ 约束注入
                                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                  内循环：质量驱动优化（Inner Loop）               │
│                                                                 │
│  InnerLoopRunner                                                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ 生成函数      │ →  │ AssetEvaluator│ →  │ 遗传算法优化器   │  │
│  │ generator(p) │    │ 多维质量评估  │    │ EvolutionaryOpt  │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
│         │                  │                      │            │
│         │◄─────────────────┴──────────────────────┘            │
│         │              参数反馈循环                             │
│         ▼                                                       │
│    最优图像输出                                                  │
└──────────────────────────────────────────────────┬─────────────┘
                                                   │
                                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                  数学引擎：模型注册表（Math Registry）            │
│                                                                 │
│  MathModelRegistry                                              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 稳定模型（Stable）                                       │   │
│  │  oklab_palette_generator  |  wfc_level_generator        │   │
│  │  spring_damper_animator   |  fabrik_ik_solver           │   │
│  │  sdf_effect_renderer      |  lsystem_plant_generator    │   │
│  │  asset_quality_evaluator  |  floyd_steinberg_ditherer   │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │ 实验模型（Experimental）                                 │   │
│  │  differentiable_renderer_2d（需要GPU）                   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、内循环详解（Inner Loop）

### 3.1 质量评估器（AssetEvaluator）

`mathart/evaluator/evaluator.py` 实现了五个互补的质量维度：

| 指标 | 权重 | 算法 | 通过阈值 |
|------|------|------|----------|
| `sharpness`（清晰度） | 30% | Laplacian 方差 | ≥ 0.50 |
| `palette_adherence`（调色板贴合） | 25% | RGB 欧氏距离 | ≥ 0.60 |
| `contrast`（对比度） | 20% | Michelson 对比度 | ≥ 0.40 |
| `style_consistency`（风格一致性） | 15% | pHash Hamming 距离 | ≥ 0.30 |
| `color_harmony`（色彩和谐度） | 10% | OKLAB 色相聚类 | ≥ 0.30 |

**清晰度**通过 Laplacian 算子检测高频边缘能量，像素画的清晰边缘会产生高方差。**调色板贴合度**计算每个像素到最近调色板颜色的 RGB 距离，容差为 15/255（约 6%）。**风格一致性**使用 pHash 感知哈希，将图像压缩为 64 位指纹后计算 Hamming 距离，对风格漂移敏感。**色彩和谐度**在 OKLAB 空间中分析色相分布的圆形方差，适度聚类（circ_r ≈ 0.5）得分最高。

### 3.2 内循环调度器（InnerLoopRunner）

```python
from mathart.evolution import SelfEvolutionEngine
from mathart.distill.compiler import ParameterSpace, Constraint

engine = SelfEvolutionEngine()
space = ParameterSpace(name="character")
space.add_constraint(Constraint(param_name="spring_k", min_value=5.0, max_value=50.0))
space.add_constraint(Constraint(param_name="hue_offset", min_value=0.0, max_value=360.0))

result = engine.inner_loop.run(
    generator=my_generator_fn,  # callable(params: dict) -> PIL.Image
    space=space,
    quality_threshold=0.75,     # 达到此分数停止
    max_iterations=50,
)
print(result.summary())
```

---

## 四、外循环详解（Outer Loop）

### 4.1 知识蒸馏流程

```
用户上传 PDF/书籍
       │
       ▼
OuterLoopDistiller.distill_file(path)
       │
       ├─ 文本提取（pdftotext / 直接读取）
       │
       ├─ 分块（每块 ≤ 3000 字符）
       │
       ├─ LLM 提取（gpt-4.1-mini）→ 结构化规则 JSON
       │   {domain, rule_text, params, rule_type, code_target, confidence}
       │
       ├─ 规则集成 → knowledge/{domain}.md 追加
       │
       └─ 日志记录 → DISTILL_LOG.md
```

### 4.2 跨会话连续性

每次蒸馏会话都会在 `DISTILL_LOG.md` 中记录唯一的 `DISTILL-NNN` 编号。下次在新的对话框中上传书籍时，系统会自动读取日志确定下一个编号，从而实现**跨会话的知识积累**，不会重复也不会丢失。

### 4.3 领域映射表

| 知识领域 | 知识文件 | 影响的代码模块 |
|----------|----------|----------------|
| `anatomy` | `knowledge/anatomy.md` | `mathart/animation/skeleton.py` |
| `color_light` | `knowledge/color_light.md` | `mathart/oklab/palette.py` |
| `color_science` | `knowledge/color_science.md` | `mathart/oklab/` |
| `physics_sim` | `knowledge/physics_sim.md` | `mathart/animation/physics.py` |
| `pcg` | `knowledge/pcg_math.md` | `mathart/level/wfc.py`, `mathart/sdf/lsystem.py` |
| `sdf_math` | `knowledge/sdf_math.md` | `mathart/sdf/` |
| `pbr` | `knowledge/pbr_math.md` | `mathart/sdf/renderer.py` |
| `procedural_animation` | `knowledge/procedural_animation.md` | `mathart/animation/physics.py` |
| `differentiable_rendering` | `knowledge/differentiable_rendering.md` | `mathart/evolution/diff_render.py` |

---

## 五、数学引擎详解（Math Registry）

### 5.1 当前已注册模型

| 模型名称 | 版本 | 数学基础 | 状态 |
|----------|------|----------|------|
| `oklab_palette_generator` | 1.1.0 | OKLAB 感知色彩空间（Ottosson 2020） | 稳定 |
| `floyd_steinberg_ditherer` | 1.0.0 | Floyd-Steinberg 误差扩散 | 稳定 |
| `wfc_level_generator` | 1.2.0 | 约束传播 + Shannon 熵最小化 | 稳定 |
| `lsystem_plant_generator` | 1.0.0 | Lindenmayer 系统形式文法 | 稳定 |
| `spring_damper_animator` | 1.1.0 | Hooke 定律 F=-kx-cv，Verlet 积分 | 稳定 |
| `fabrik_ik_solver` | 1.0.0 | FABRIK 前向/后向到达 IK | 稳定 |
| `sdf_effect_renderer` | 1.0.0 | SDF 布尔运算 + Perlin 噪声 | 稳定 |
| `asset_quality_evaluator` | 1.0.0 | Laplacian 方差 + pHash + OKLAB | 稳定 |
| `differentiable_renderer_2d` | 0.1.0 | 可微光栅化 + 感知损失 | 实验性 |

### 5.2 能力缺口报告

运行 `mathart-evolve gaps` 可查看当前能力缺口，以下是已知的缺口和升级路径：

| 能力 | 当前状态 | 升级条件 |
|------|----------|----------|
| `TEXTURE`（噪声纹理生成） | 未实现 | 添加 `mathart/sdf/noise.py` |
| `SHADER_PARAMS`（完整着色器参数） | 实验性 | 需要 GPU + PyTorch + nvdiffrast |
| `PIXEL_IMAGE`（可微渲染） | 实验性 | 需要 NVIDIA GPU（CUDA 11.8+） |

---

## 六、进化路线图

### 近期（无需外部升级）
- [ ] 添加 `mathart/sdf/noise.py`：Perlin/Simplex 噪声纹理生成器
- [ ] 内循环引入有限差分梯度近似（替代纯遗传算法）
- [ ] 外循环支持批量 PDF 蒸馏

### 中期（需要 GPU）
- [ ] 集成 PyTorch 可微渲染（需要 NVIDIA GPU）
- [ ] 训练轻量级质量代理网络（替代 pHash）

### 远期（需要多模态模型）
- [ ] 接入多模态视觉模型，实现参考图逆向工程（Image-to-Math）
- [ ] 自动从游戏截图中提取风格约束

---

## 七、如何向项目注入新知识

### 方式一：上传 PDF/书籍（推荐）
```bash
mathart-evolve distill path/to/new_art_book.pdf
```

### 方式二：直接编辑知识文件
在 `knowledge/` 目录下的对应 `.md` 文件中追加新规则，格式参考现有文件。

### 方式三：通过对话框（跨会话）
在新的对话框中上传书籍附件，并说明"蒸馏进 MarioTrickster-MathArt 项目"。系统会自动识别项目状态并继续上次的蒸馏编号。

---

## 八、文件结构

```
MarioTrickster-MathArt/
├── mathart/
│   ├── evaluator/              ← 美术资产质量评估器（新增）
│   │   ├── __init__.py
│   │   └── evaluator.py        ← AssetEvaluator（5维质量评分）
│   ├── evolution/              ← 自进化引擎（新增）
│   │   ├── __init__.py
│   │   ├── engine.py           ← SelfEvolutionEngine（总协调器）
│   │   ├── inner_loop.py       ← InnerLoopRunner（质量驱动迭代）
│   │   ├── outer_loop.py       ← OuterLoopDistiller（知识蒸馏）
│   │   ├── math_registry.py    ← MathModelRegistry（模型注册表）
│   │   └── cli.py              ← mathart-evolve 命令行工具
│   ├── animation/
│   │   └── physics.py          ← SpringDamper + FABRIKSolver（新增）
│   ├── oklab/                  ← OKLAB 色彩科学（已有）
│   ├── sdf/                    ← SDF 特效生成（已有）
│   ├── level/                  ← WFC 关卡生成（已有）
│   └── distill/                ← 知识蒸馏管道（已有）
├── knowledge/
│   ├── anatomy.md              ← 解剖学约束
│   ├── color_light.md          ← 色彩光影
│   ├── color_science.md        ← OKLAB 色彩科学（新增）
│   ├── pcg_math.md             ← 程序化生成数学（新增）
│   ├── pbr_math.md             ← 物理基础渲染（新增）
│   ├── sdf_math.md             ← SDF 数学（新增）
│   ├── procedural_animation.md ← 程序化动画（新增）
│   └── differentiable_rendering.md ← 可微渲染（新增）
├── tests/
│   ├── test_evaluator.py       ← 22 个质量评估器测试（新增）
│   ├── test_evolution.py       ← 21 个进化引擎测试（新增）
│   └── test_physics.py         ← 23 个物理动画测试（新增）
├── ARCHITECTURE_EVOLUTION.md   ← 本文档
├── DISTILL_LOG.md              ← 蒸馏历史记录
└── pyproject.toml              ← v0.3.0，新增 mathart-evolve CLI
```
