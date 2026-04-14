# 程序化内容生成数学（PCG Math）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、WaveFunctionCollapse/mxgmn、Perlin Noise/Ken Perlin

## Perlin/Simplex 噪声（来源：数学驱动研究报告 维度一）

噪声函数是程序化生成的基石，通过叠加多个频率的噪声（倍频/Octaves）产生分形特性。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `octaves` | 3-8 | 叠加的噪声层数，越多细节越丰富 | `mathart/sdf/lsystem.py` |
| `persistence` | 0.4-0.6 | 每倍频振幅衰减比例，0.5=每层减半 | `mathart/animation/physics.py` |
| `lacunarity` | 1.8-2.2 | 每倍频频率增长比例，2.0=每层翻倍 | `mathart/sdf/` |
| `frequency` | 0.5-4.0 | 基础频率（周期/单位） | `mathart/animation/physics.py` |
| `amplitude` | 0.05-0.3 | 基础振幅（归一化坐标） | `mathart/animation/physics.py` |

### 蒸馏洞察
> 这意味着：噪声参数直接控制视觉"有机感"。persistence=0.5是自然界的黄金比例，产生1/f频谱特性（粉红噪声），与人类感知最和谐。

## 波函数坍缩（WFC）（来源：数学驱动研究报告 维度一）

WFC通过约束传播生成满足局部规则的全局结构。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `entropy_threshold` | 0.01 | 低于此熵值的格子优先坍缩 | `mathart/level/wfc.py` |
| `backtrack_limit` | 100-500 | 最大回溯次数，防止无限循环 | `mathart/level/wfc.py` |
| `tile_size` | 2-4 | 样本分析的瓦片大小（像素） | `mathart/level/wfc.py` |

### 蒸馏洞察
> 这意味着：WFC的"熵最小化"策略（先坍缩最确定的格子）是保证全局一致性的关键。选错顺序会导致大量回溯。

## L-系统文法（来源：数学驱动研究报告 维度一）

L-系统通过迭代字符串重写生成分形几何结构。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `branch_angle` | 15-45° | 分叉角度，25°≈自然树木 | `mathart/sdf/lsystem.py` |
| `length_decay` | 0.6-0.85 | 每级分支长度衰减比例 | `mathart/sdf/lsystem.py` |
| `width_decay` | 0.5-0.75 | 每级分支宽度衰减比例 | `mathart/sdf/lsystem.py` |
| `max_depth` | 3-7 | 最大递归深度，超过7层性能下降 | `mathart/sdf/lsystem.py` |

### 蒸馏洞察
> 这意味着：branch_angle=25°是黄金角（137.5°的约数），产生最接近真实植物的分叉模式。
