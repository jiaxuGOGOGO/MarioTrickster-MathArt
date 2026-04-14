# 蒸馏工作流规范

> 本文件定义了 MarioTrickster-MathArt 项目的知识蒸馏驱动进化机制。

## 核心理念

```
knowledge/ 是项目的大脑。
每一本书、每一份教程、每一页 PDF 被蒸馏后，知识沉淀在这里。
代码模块读取大脑中的知识，越来越接近那些教程想教会你的东西。
```

## 蒸馏流程

```
用户上传 PDF/书籍/教程
       ↓
  AI 阅读、理解、提炼
       ↓
  ┌────────────────────────────────┐
  │  knowledge/ ← 知识沉淀（大脑）  │
  │  mathart/*  ← 代码改动（肌肉）  │
  │  tests/*    ← 测试保障（免疫）  │
  └────────────────────────────────┘
       ↓
  git push → GitHub
       ↓
  用户 git pull → 项目进化一步
```

**每次蒸馏同时产出三样东西**：

1. **知识文件**（`knowledge/*.md`）—— 结构化的规则沉淀，是项目的长期记忆
2. **代码改动**（`mathart/**/*.py`）—— 知识的落地执行，让项目真正变强
3. **测试用例**（`tests/*.py`）—— 确保新知识不破坏已有能力

## knowledge/ 目录 —— 项目的大脑

### 知识领域映射

| 知识文件 | 领域 | 驱动的代码模块 | 典型来源 |
|----------|------|---------------|----------|
| `anatomy.md` | 人体解剖 | `animation/skeleton.py` 关节约束 | 松岡、伯里曼、人体解剖学教材 |
| `perspective.md` | 透视法则 | `sdf/` 投影算法、深度暗示 | OCHABI、吉田誠治、Scott Robertson |
| `color_light.md` | 色彩与光影 | `oklab/palette.py` 和谐规则 | Color and Light、像素画配色理论 |
| `animation.md` | 动画原理 | `animation/presets.py` 缓动/预设 | Animator's Survival Kit、Disney 12法则 |
| `unity_rules.md` | Unity 约束 | `export/` 导出校验 | 主项目 TA_AssetValidator |
| `pixel_art.md` | 像素画技法 | `sdf/` 抖动、子像素 | Pixel Logic、像素画专著 |
| `game_design.md` | 游戏设计 | `level/wfc.py` 关卡约束 | 关卡设计理论、游戏机制 |
| `vfx.md` | 特效设计 | `sdf/effects.py` 特效预设 | VFX 参考、粒子系统理论 |
| `plant_botany.md` | 植物形态学 | `sdf/lsystem.py` 文法规则 | 植物学、分形几何 |

### 知识文件格式

每个知识文件遵循统一格式：

```markdown
# 领域名称
> 来源汇总：书名1/作者1、书名2/作者2 ...

## 章节标题（来源：具体书名 第X章 / 页码）

描述性文字：解释原理和背后的逻辑。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 参数名 | 数值或范围 | 为什么是这个值 | 对应的代码位置 |

### 蒸馏洞察

> 这条知识意味着：（用一句话说明对代码的影响）
```

**关键列 `代码映射`**：每条知识都标注它影响的代码位置，确保知识和代码的双向可追溯。

## 蒸馏日志

每次蒸馏在 `DISTILL_LOG.md` 中追加一条记录，格式：

```markdown
## [DISTILL-xxx] 来源名称 — 日期

**来源**：书名/作者/章节
**蒸馏内容**：一句话概括提炼了什么
**知识沉淀**：
- `knowledge/xxx.md` — 新增/更新了什么
**代码改动**：
- `mathart/xxx/yyy.py` — 改了什么、为什么改
**测试**：
- `tests/test_xxx.py` — 新增了什么测试
**commit**：`distill(xxx): 简要描述`
```

## Commit 规范

蒸馏相关的提交使用 `distill()` 前缀：

```
distill(anatomy): add muscle co-contraction rules from 伯里曼
distill(color): add subsurface scattering heuristic from Color and Light
distill(animation): add anticipation timing curves from Animator's Survival Kit
distill(game_design): add difficulty curve constraints from Game Feel
distill(pixel_art): add dithering patterns from Pixel Logic
```

括号内是知识领域，冒号后是具体内容。

## 蒸馏质量标准

好的蒸馏应该满足：

| 标准 | 说明 | 示例 |
|------|------|------|
| **可参数化** | 能转化为数字、范围、公式 | "肘关节最大屈曲 145°" → `elbow_rom_max = 145` |
| **可代码化** | 能直接改进某个算法或预设 | "暖光冷影" → `shadow_hue = light_hue + 160` |
| **可测试** | 能写成断言验证 | `assert 0 <= elbow_angle <= 145` |
| **有来源** | 标注书名/作者/页码 | "来源：松岡 人体解剖 P.47" |
| **有洞察** | 说明为什么这条知识重要 | "这意味着跑步动画中肘部永远不应该反向弯曲" |

## 知识的三种落地方式

| 类型 | 说明 | 落地方式 |
|------|------|----------|
| **硬约束** | 违反就是错误 | 写入代码常量 + 断言 |
| **软默认** | 推荐值，可覆盖 | 写入预设默认参数 |
| **算法改进** | 新的数学方法 | 新增/重写算法实现 |

## 大脑的成长路径

```
第 1 阶段（当前）：基础知识骨架
  anatomy.md ← 关节约束
  color_light.md ← 暖光冷影
  animation.md ← 12 法则
  unity_rules.md ← 导出约束
  perspective.md ← 透视法则

第 2 阶段：深化每个领域
  每个文件从 30 行 → 300+ 行
  更多参数、更精确的范围、更多来源交叉验证

第 3 阶段：领域交叉
  知识之间产生关联（如：透视影响动画的前缩、色彩影响深度感知）
  代码模块之间产生联动

第 4 阶段：自主推理
  积累足够多的知识后，可以推导出教程没有直接教的规则
  例如：从"暖光冷影"+"环境光反射"推导出"草地关卡的角色阴影应偏绿"
```
