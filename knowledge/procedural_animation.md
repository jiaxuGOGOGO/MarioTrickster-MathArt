# 程序化动画（Procedural Animation）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、GDC talks、Squirrel Eiserloh noise-based motion

## 弹簧阻尼二次动画（来源：数学驱动研究报告 维度三）

弹簧阻尼系统是游戏中最常用的二次动画方法，用于披风、头发、配件等。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `spring_k` | 5-50 | 弹簧刚度，越大响应越快 | `mathart/animation/physics.py` |
| `damping_c` | 1-10 | 阻尼系数，越大振荡越少 | `mathart/animation/physics.py` |
| `mass` | 0.1-5.0 | 模拟质量，越大惯性越强 | `mathart/animation/physics.py` |
| `critical_damping` | `2*sqrt(k*m)` | 临界阻尼值（无振荡最快收敛） | `mathart/animation/physics.py` |
| `damping_ratio` | 0.6-1.2 | 阻尼比ζ，<1振荡，=1临界，>1过阻尼 | `mathart/animation/physics.py` |

### 蒸馏洞察
> 这意味着：damping_ratio=0.7是游戏中最常用的值，产生轻微弹性感但快速稳定，既有"果冻感"又不会过度振荡。

## FABRIK逆运动学（来源：数学驱动研究报告 维度三）

FABRIK是目前最高效的2D/3D IK算法，每次迭代O(n)复杂度。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `max_iterations` | 5-20 | 最大迭代次数，10次通常足够 | `mathart/animation/physics.py` |
| `tolerance` | 0.001-0.01 | 收敛容差（归一化坐标） | `mathart/animation/physics.py` |
| `elbow_constraint` | 0-145° | 肘关节最大弯曲角度（解剖学约束） | `mathart/animation/physics.py` |
| `knee_constraint` | 0-160° | 膝关节最大弯曲角度 | `mathart/animation/physics.py` |

### 蒸馏洞察
> 这意味着：FABRIK的前向传递（tip→root）+后向传递（root→tip）各一次即可收敛80%，10次迭代可达99.9%精度。

## 噪声驱动待机动画（来源：数学驱动研究报告 维度三）

使用分层Perlin噪声生成有机感的待机动画。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `idle_frequency` | 0.3-1.5 Hz | 待机呼吸/摇摆频率 | `mathart/animation/physics.py` |
| `idle_amplitude` | 0.02-0.08 | 待机动画幅度（归一化） | `mathart/animation/physics.py` |
| `idle_octaves` | 2-4 | 噪声倍频数，越多越自然 | `mathart/animation/physics.py` |
| `secondary_delay` | 2-4 frames | 二次动画相对主动画的延迟帧数 | `mathart/animation/physics.py` |

### 蒸馏洞察
> 这意味着：人类呼吸频率约0.25Hz，心跳约1.1Hz。待机动画混合这两个频率可产生最自然的生命感。

## 动画缓动函数（来源：数学驱动研究报告 维度三）

| 缓动类型 | 公式 | 适用场景 |
|----------|------|----------|
| ease_in_quad | `t²` | 起步加速（角色跑步起步） |
| ease_out_quad | `1-(1-t)²` | 减速停止（角色落地） |
| ease_in_out_cubic | `4t³ if t<0.5` | 平滑过渡（相机移动） |
| spring_ease | 弹簧阻尼解析解 | 弹性UI动画 |
| bounce_ease | 分段函数 | 物体弹跳落地 |
