# MarioTrickster-MathArt

**数学驱动程序化像素美术管线** — 为 [MarioTrickster](https://github.com/jiaxuGOGOGO/MarioTrickster) 游戏项目提供可复现、可自动化的美术资产生产工具。

---

## 定位

| 仓库 | 职责 |
|------|------|
| **MarioTrickster** | 游戏主仓库：关卡逻辑、物理系统、135 个测试 |
| **MarioTrickster-Art** | 旧美术管线：LoRA / ComfyUI / WAN 2.2 体系 |
| **MarioTrickster-MathArt**（本仓库） | 新美术管线：OKLAB 调色板 + SDF 特效 + 程序化骨骼动画 |

本仓库产出标准 PNG / Sprite Sheet，直接拖入主仓库 `Assets/Art/` 目录即可被 `TA_AssetValidator` 和 `AI_SpriteSlicer` 自动接收。

---

## 三大模块

### 1. OKLAB 调色板生成器 (`mathart.oklab`)

基于 Björn Ottosson 的 OKLAB 感知均匀色彩空间，生成像素画专用限色调色板。

- 6 种色彩和谐模式：互补、类似、三角、分裂互补、**暖光冷影**、色调阶梯
- 一键生成 `LevelThemeProfile` 全套配色（地面/平台/墙壁/背景/角色/陷阱）
- sRGB 色域自动钳制（二分搜索降饱和度）
- 图像量化器：将任意图片映射到指定调色板（支持 Floyd-Steinberg 抖动）

```bash
# 生成一个 8 色暖光冷影调色板
mathart-palette generate --harmony warm_cool_shadow --count 8 --hue 120 -o palette.json

# 生成完整主题配色集
mathart-palette theme --name grassland --seed 42 -o ./palettes/

# 将图片量化到指定调色板
mathart-palette quantize input.png palette.json -o output.png --dither
```

### 2. SDF 特效生成器 (`mathart.sdf`)

用 2D 有符号距离场（Signed Distance Field）程序化生成游戏特效精灵。

- 基础图元：圆、矩形、线段、三角形、星形、环形
- 布尔运算：并集、交集、差集、光滑混合
- 变换：平移、旋转、缩放、平铺
- 游戏特效预设：地刺（SpikeTrap）、火焰（FireTrap）、锯片（SawBlade）、电弧、发光
- 支持输出静态精灵和动画 Sprite Sheet

```bash
# 生成地刺精灵
mathart-sdf spike -o spike.png --size 32

# 生成火焰动画 Sprite Sheet（8 帧）
mathart-sdf flame -o flame_sheet.png --size 32 --frames 8
```

### 3. 程序化骨骼动画 (`mathart.animation`)

基于蒸馏的解剖学知识（关节可动域、拮抗肌联动、挤压拉伸）生成角色动画。

- 人形骨骼系统：16 个关节、12 根骨骼、ROM 约束
- 动画曲线：ease-in-out、弹簧、正弦波、贝塞尔、弹跳
- 预设动画：idle / run / jump / fall / hit
- 输出水平排列 Sprite Sheet，兼容 `AI_SpriteSlicer`

```bash
# 生成跑步动画 Sprite Sheet
mathart-anim run -o run_sheet.png --size 32 --frames 8

# 生成待机动画
mathart-anim idle -o idle_sheet.png --frames 8
```

### 4. 导出桥接层 (`mathart.export`)

强制执行 Unity 约束，生成元数据 JSON，一键批量导出。

```bash
# 生成完整演示资产集
mathart-export --generate-demo --output-dir ./output --style Style_MathArt
```

---

## 安装

```bash
# 克隆
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt

# 安装（需要 Python ≥ 3.10）
pip install -e ".[dev]"

# 运行测试（58 个测试）
pytest tests/ -v
```

**依赖**：仅 `numpy`、`Pillow`、`scipy`，无需 GPU，无需 ComfyUI。

---

## 与主项目衔接

产出物通过 `mathart-export` 导出后，按以下路径放入主仓库：

```
MarioTrickster/
  Assets/
    Art/
      Style_MathArt/
        Characters/    ← mario_idle_a_sheet_v01.png + .meta.json
        Enemies/
        Environment/   ← ground_tile_a_v01.png
        Hazards/       ← spike_trap_a_v01.png, fire_trap_a_sheet_v01.png
        VFX/           ← collectible_glow_a_sheet_v01.png
```

`TA_AssetValidator` 会自动校正 PPU=32、Point Filter、Alpha Transparency。`AI_SpriteSlicer` 根据 `.meta.json` 中的 `frame_count` 自动切片。最终将精灵拖入 `LevelThemeProfile` 对应槽位即可。

---

## 蒸馏知识体系

本项目继承了 MarioTrickster-Art 中蒸馏的美术知识，存放在 `knowledge/` 目录：

```
knowledge/
  anatomy.md       ← 关节可动域、头身比、肌肉联动（松岡/砂糖/みにまる）
  perspective.md   ← 透视法则（OCHABI/吉田誠治/Peter Han）
  color_light.md   ← 光影色温互补、3值色阶（PROMPT_RECIPES §光影）
  animation.md     ← 12 动画法则的数学参数化
  unity_rules.md   ← PPU/Filter/Pivot/命名/目录硬约束
```

**如何继续蒸馏**：你读完新书后，和 AI 对话提炼出规则，按上述格式追加到对应 `.md` 文件，然后 push。代码模块会读取这些知识文件作为参数约束。

---

## CI 自动测试

每次 push 和 PR 自动运行 58 个测试 + ruff 代码检查：

```yaml
# .github/workflows/ci.yml — 自动执行
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - pip install -e ".[dev]"
      - pytest tests/ -v
      - ruff check mathart/
```

---

## 许可

MIT
