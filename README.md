# MarioTrickster-MathArt

**数学驱动程序化像素美术管线** — 为 [MarioTrickster](https://github.com/jiaxuGOGOGO/MarioTrickster) 游戏项目提供可复现、可自动化的美术资产生产工具。

---

## 核心理念

> **knowledge/ 是项目的大脑。每一本书、每一份教程被蒸馏后，知识沉淀在这里。代码模块读取大脑中的知识，越来越接近那些教程想教会你的东西。**

```
用户上传 PDF/书籍/教程 → AI 蒸馏提炼 → knowledge/ 知识沉淀 + 代码改动 → git push → 用户 pull → 项目进化
```

本项目通过**持续蒸馏**美术、动画、透视、色彩、游戏设计等领域的专业知识，将其转化为数学公式和代码算法，让程序化生成的质量不断逼近手绘水准。

---

## 定位

| 仓库 | 职责 |
|------|------|
| **MarioTrickster** | 游戏主仓库：关卡逻辑、物理系统、Level Studio |
| **MarioTrickster-Art** | 旧美术管线：LoRA / ComfyUI / WAN 2.2（已归档为探索记录） |
| **MarioTrickster-MathArt**（本仓库） | 新美术管线：数学驱动 + 知识蒸馏持续进化 |

本仓库产出标准 PNG / Sprite Sheet，直接拖入主仓库 `Assets/Art/` 目录即可被 `TA_AssetValidator` 和 `AI_SpriteSlicer` 自动接收。

---

## 六大模块

### 1. OKLAB 调色板生成器 (`mathart.oklab`)

基于 Björn Ottosson 的 OKLAB 感知均匀色彩空间，生成像素画专用限色调色板。

- 6 种色彩和谐模式：互补、类似、三角、分裂互补、暖光冷影、色调阶梯
- 一键生成 `LevelThemeProfile` 全套配色
- sRGB 色域自动钳制（二分搜索降饱和度）
- 图像量化器：支持 Floyd-Steinberg 抖动

```bash
mathart-palette generate --harmony warm_cool_shadow --count 8 --hue 120 -o palette.json
mathart-palette theme --name grassland --seed 42 -o ./palettes/
mathart-palette quantize input.png palette.json -o output.png --dither
```

### 2. SDF 特效生成器 (`mathart.sdf`)

用 2D 有符号距离场程序化生成游戏特效精灵。

- 基础图元：圆、矩形、线段、三角形、星形、环形
- 布尔运算：并集、交集、差集、光滑混合
- 游戏特效预设：地刺、火焰、锯片、电弧、发光
- 支持输出静态精灵和动画 Sprite Sheet

```bash
mathart-sdf spike -o spike.png --size 32
mathart-sdf flame -o flame_sheet.png --size 32 --frames 8
```

### 3. L-系统植物生成器 (`mathart.sdf.lsystem`)

基于 Lindenmayer 系统的程序化植物生成，用数学文法描述植物生长规则。

- 支持确定性和随机文法规则
- 分支深度追踪、自动粗细衰减
- 5 种预设植物：橡树、灌木、藤蔓、蕨类、花卉
- 像素渲染带深度感知着色

```python
from mathart.sdf.lsystem import PlantPresets
oak = PlantPresets.oak_tree(seed=42)
oak.generate(iterations=4)
img = oak.render(64, 64)
img.save("oak.png")
```

### 4. 程序化骨骼动画 (`mathart.animation`)

基于蒸馏的解剖学知识（关节可动域、拮抗肌联动、挤压拉伸）生成角色动画。

- 人形骨骼系统：16 个关节、12 根骨骼、ROM 约束
- 动画曲线：ease-in-out、弹簧、正弦波、贝塞尔、弹跳
- 预设动画：idle / run / jump / fall / hit

```bash
mathart-anim run -o run_sheet.png --size 32 --frames 8
mathart-anim idle -o idle_sheet.png --frames 8
```

### 5. WFC 关卡生成器 (`mathart.level`)

基于波函数坍缩（Wave Function Collapse）算法，从经典片段库学习邻接规则，自动生成合法关卡布局。

- 19 种游戏元素映射，与主项目 Level Studio ASCII 系统完全对齐
- 从经典片段库自动学习邻接约束
- 支持结构约束：确保地面、出生点、目标点
- 批量生成多个关卡变体

```bash
mathart-level generate --width 22 --height 7 --seed 42 -o level.txt
mathart-level batch --count 10 --width 22 --height 7 -o ./levels/
```

### 6. 导出桥接层 (`mathart.export`)

强制执行 Unity 约束，生成元数据 JSON，一键批量导出。

```bash
mathart-export --generate-demo --output-dir ./output --style Style_MathArt
```

---

## 安装

```bash
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt
pip install -e ".[dev]"
pytest tests/ -v
```

**依赖**：仅 `numpy`、`Pillow`、`scipy`，无需 GPU，无需 ComfyUI。

**更新**（每次蒸馏后）：

```bash
git pull
pip install -e ".[dev]"
pytest tests/ -v
```

---

## 与主项目衔接

产出物通过 `mathart-export` 导出后，按以下路径放入主仓库：

```
MarioTrickster/
  Assets/Art/Style_MathArt/
    Characters/    ← mario_idle_a_sheet_v01.png + .meta.json
    Enemies/
    Environment/   ← ground_tile_a_v01.png + L-系统植物
    Hazards/       ← spike_trap_a_v01.png, fire_trap_a_sheet_v01.png
    VFX/
    Levels/        ← WFC 生成的关卡 ASCII 文本
```

---

## 知识蒸馏大脑 (`knowledge/`)

这是项目的核心竞争力。每一次蒸馏都让项目更"懂"美术。

```
knowledge/
  anatomy.md       ← 人体解剖：关节可动域、头身比、肌肉联动
  perspective.md   ← 透视法则：深度暗示、消失点、前缩
  color_light.md   ← 色彩光影：暖光冷影、色阶系统、环境光
  animation.md     ← 动画原理：12 法则数学映射、关键帧约定
  unity_rules.md   ← Unity 约束：PPU/Filter/Pivot/命名
  （持续扩展中...）
```

### 蒸馏工作流

```
你上传 PDF/书籍 → AI 阅读理解
                      ↓
              knowledge/*.md ← 知识沉淀（大脑长期记忆）
              mathart/*.py   ← 代码改动（知识落地执行）
              tests/*.py     ← 测试保障（确保不退化）
                      ↓
              git push → 你 git pull → 项目进化一步
```

每次蒸馏记录在 [`DISTILL_LOG.md`](DISTILL_LOG.md) 中，完整规范见 [`DISTILL_WORKFLOW.md`](DISTILL_WORKFLOW.md)。

### 大脑的成长路径

| 阶段 | 状态 | 目标 |
|------|------|------|
| 第 1 阶段 | **当前** | 基础知识骨架：每个领域 30 行核心规则 |
| 第 2 阶段 | 进行中 | 深化：每个领域 300+ 行，多来源交叉验证 |
| 第 3 阶段 | 规划中 | 交叉：领域间产生关联（透视影响动画前缩、色彩影响深度感知） |
| 第 4 阶段 | 远期 | 推理：从已有知识推导出教程没有直接教的规则 |

---

## 测试

167 个测试覆盖全部模块，每次 push 自动运行 GitHub Actions CI：

```bash
pytest tests/ -v                          # 全部测试
pytest tests/test_level.py -v             # WFC 关卡生成
pytest tests/test_lsystem.py -v           # L-系统植物
pytest tests/test_distill.py -v           # 蒸馏管道
pytest tests/ --cov=mathart               # 覆盖率报告
```

---

## 许可

MIT
