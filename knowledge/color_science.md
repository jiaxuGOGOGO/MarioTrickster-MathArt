# 色彩科学（Color Science）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、OKLAB/Björn Ottosson、Floyd-Steinberg/Wikipedia

## OKLAB感知均匀色彩空间（来源：Björn Ottosson 2020）

OKLAB是目前最优秀的感知均匀色彩空间，在此空间中进行颜色插值产生视觉上最自然的渐变。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `L` | 0.0-1.0 | 感知亮度（线性） | `mathart/oklab/color_space.py` |
| `a` | -0.5-0.5 | 绿-红轴 | `mathart/oklab/color_space.py` |
| `b` | -0.5-0.5 | 蓝-黄轴 | `mathart/oklab/color_space.py` |
| `chroma_max` | 0.3 | 最大色度（超出sRGB色域） | `mathart/oklab/palette.py` |
| `chroma_safe` | 0.15 | 安全色度（100%在sRGB内） | `mathart/oklab/palette.py` |

### 蒸馏洞察
> 这意味着：在OKLAB空间中，两点之间的欧氏距离≈人眼感知的色差。RGB空间中的"等距"插值在视觉上是不均匀的。

## OKLCH极坐标表示

OKLCH是OKLAB的极坐标形式，更直观地控制色相和饱和度。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `L` | 0.0-1.0 | 亮度（同OKLAB） | `mathart/oklab/color_space.py` |
| `C` | 0.0-0.4 | 色度（饱和度） | `mathart/oklab/palette.py` |
| `H` | 0-360° | 色相角度 | `mathart/oklab/palette.py` |

## 程序化调色板生成算法（来源：数学驱动研究报告 维度四）

基于James Gurney色彩理论的程序化调色板生成。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `harmony_type` | analogous/complementary/triadic | 色彩和谐类型 | `mathart/oklab/palette.py` |
| `analogous_spread` | 20-60° | 类比色扩展角度 | `mathart/oklab/palette.py` |
| `complementary_offset` | 150-210° | 互补色偏移角度（180°=正互补） | `mathart/oklab/palette.py` |
| `light_hue_shift` | +15 to +30° | 暖光色相偏移（暖光冷影法则） | `mathart/oklab/palette.py` |
| `shadow_hue_shift` | +150 to +180° | 冷影色相偏移 | `mathart/oklab/palette.py` |

### 蒸馏洞察
> 这意味着：shadow_hue = light_hue + 160°是"暖光冷影"的数学表达，在OKLAB空间中执行此偏移可保证感知上的和谐。

## 抖动算法参数（来源：数学驱动研究报告 维度九）

Floyd-Steinberg误差扩散权重矩阵：
```
    * 7/16
3/16 5/16 1/16
```

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `dither_strength` | 0.5-1.0 | 抖动强度，1.0=完整误差扩散 | `mathart/oklab/quantizer.py` |
| `serpentine` | True/False | 蛇形扫描减少条纹 | `mathart/oklab/quantizer.py` |
| `palette_size` | 4-32 | 目标调色板颜色数量 | `mathart/oklab/quantizer.py` |

### 蒸馏洞察
> 这意味着：serpentine=True可以消除Floyd-Steinberg产生的水平条纹伪影，是像素画的推荐设置。
