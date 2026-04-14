# 符号距离场数学（SDF Math）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、Inigo Quilez/iquilezles.org、The Book of Shaders

## SDF基础图元（来源：数学驱动研究报告 维度五）

SDF（Signed Distance Field）：对空间中任意点p，返回到最近表面的有符号距离。
负值=内部，正值=外部，零=表面。

| 图元 | SDF公式 | 参数 | 代码映射 |
|------|---------|------|----------|
| 圆形 | `length(p) - r` | r=半径 | `mathart/sdf/primitives.py` |
| 矩形 | `length(max(abs(p)-b, 0))` | b=半尺寸向量 | `mathart/sdf/primitives.py` |
| 胶囊体 | `length(p-clamp(p,a,b)) - r` | a,b=端点，r=半径 | `mathart/sdf/primitives.py` |
| 线段 | 胶囊体r=0的特例 | - | `mathart/sdf/primitives.py` |

### 蒸馏洞察
> 这意味着：所有复杂形状都可以通过SDF布尔运算（并集/交集/差集）组合基础图元得到，无需手绘。

## SDF布尔运算（来源：Inigo Quilez）

| 运算 | 公式 | 效果 |
|------|------|------|
| 并集（Union） | `min(d1, d2)` | 合并两个形状 |
| 交集（Intersection） | `max(d1, d2)` | 保留重叠部分 |
| 差集（Subtraction） | `max(d1, -d2)` | 从d1中挖去d2 |
| 平滑并集 | `smin(d1, d2, k)` | 平滑融合，k控制融合半径 |

平滑并集公式：`smin(a,b,k) = -log(exp(-a/k) + exp(-b/k)) * k`

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `smooth_k` | 0.01-0.3 | 平滑融合半径，越大融合越柔和 | `mathart/sdf/operations.py` |
| `outline_width` | 0.02-0.08 | 轮廓线宽度（归一化坐标） | `mathart/sdf/renderer.py` |
| `render_scale` | 16-64 | 渲染分辨率（像素/单位） | `mathart/sdf/renderer.py` |

### 蒸馏洞察
> 这意味着：smooth_k=0.03在32px精灵中约等于1像素的融合宽度，是角色身体部件连接的推荐值。

## 光线行进（Ray Marching）（来源：数学驱动研究报告 维度五）

光线行进通过迭代步进SDF来渲染复杂场景，无需显式几何。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `max_steps` | 64-256 | 最大步进次数 | `mathart/sdf/renderer.py` |
| `hit_threshold` | 0.001 | 命中判定距离阈值 | `mathart/sdf/renderer.py` |
| `max_distance` | 10.0-100.0 | 最大追踪距离 | `mathart/sdf/renderer.py` |
