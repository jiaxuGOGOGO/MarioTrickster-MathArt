# SESSION-122 外部参考研究摘录（进行中）

## 已核对的仓库上下文

- GitHub 仓库：`jiaxuGOGOGO/MarioTrickster-MathArt`
- 默认分支：`main`
- 浏览器页可见最新提交短哈希：`4df0215`
- 本地克隆得到完整提交哈希：`4df0215966646dec2c6ac5242b8e25fb0d107884`

## 已提取的外部参考要点

### 1. Inigo Quilez — Smooth Minimum
来源：<https://iquilezles.org/articles/smin/>

关键落地点：

1. **smooth minimum 的参数 `k` 在归一化后直接表示融合厚度（distance units）**。这意味着如果把 `k` 设计成时间连续函数 `k(t)`，则可以自然地把“两个 SDF 部件之间的融合颈部厚度”做成时变轨道，而不是通过顶点层硬改拓扑。
2. **quadratic smin 是 C1 连续，cubic smin 是 C2 连续**。因此若本次任务要求至少保证形变体积/包围盒随时间不突跳，优先保持参数轨道至少 C1 连续，并让平滑融合在场函数层完成。
3. **`k` 同时给出包围盒膨胀界**。这对后续逐帧网格提取非常重要：每一帧的包围盒或安全采样域可以由静态基元包围盒加上 `k(t)` 的上界推导，而不必全量缓存 4D 体素场。
4. **smooth minimum 可返回 mix factor**。这提示当前项目若将来做 USD/材质时间采样，可以把几何融合与材质混合轨分别统一到同一时间采样契约。

## 当前落地准则（临时）

1. 时间动画应当优先参数化 **SDF 参数**（如半径、尺度、smin_k），而不是直接参数化顶点位置。
2. 参数轨道必须输出整段时间轴的向量化数组，避免 Python 层逐帧插值循环。
3. 网格/光栅求值必须按帧流式执行，不能把整段 4D 距离场缓存进内存。
4. 对半径、缩放、融合厚度等危险参数必须实施安全裁剪，以维持场函数连续与数值稳定。

### 2. OpenUSD — Time Codes and Time Samples
来源：<https://docs.nvidia.com/learn-openusd/latest/stage-setting/timecodes-timesamples.html>

关键落地点：

1. **TimeCode 是无单位时间点，真实时间通过 `timeCodesPerSecond` 映射**。这说明本次参数轨设计应区分“内部帧索引/采样时间张量”和“可导出的时间码”。
2. **TimeSamples 本质上是“时间码 → 属性值”的映射**，而且属性在求值时会从相邻样本中插值。因此本次参数轨结果如果以 `{time_code: value}` 或等价结构存储，就能自然为未来 OpenUSD 导出做准备。
3. **动画属性不仅限于位移，也包括材质与任意属性**。因此本次实现不应把轨道限制死在几何尺寸，应允许标量与向量参数统一进入时间轨接口。

### 3. Squash & Stretch 体积守恒技术文章
来源：<https://adammadej.com/posts/202403-squashstretch/>

关键落地点：

1. **体积守恒的本质是轴向缩放联动，而不是单轴孤立放大**。文章直接指出：当对象沿 `y` 轴按系数 `n` 拉伸时，为保持体积，`x` 与 `z` 应按反向比例缩放。
2. 对近似球/椭球体，如果令 `scale_y = n`，则可用 `scale_x = scale_z = 1/sqrt(n)` 来保持体积近似恒定；若是更一般 3D 体积约束，则保持 `scale_x * scale_y * scale_z ≈ const`。
3. 该规律很适合被实现为**派生轨道**或**联动约束**：主轨控制 `stretch_y(t)`，其余轴轨由解析公式自动生成，而不是让用户分别手工关键帧化，从而减少非物理膨胀。

## 更新后的落地准则（临时）

5. 参数轨道数据结构应天然支持导出为 **TimeSamples 风格字典**，即 `time -> scalar/vector`。
6. 对挤压/拉伸类参数应提供体积守恒辅助函数，至少覆盖单主轴驱动下的三轴联动缩放。
7. 所有轨道值在进入 SDF 评估前应先经过数值安全投影，例如对半径、尺寸、平滑系数、缩放因子实施 `clip`，避免负尺度或过大 `smin_k` 破坏场的 Lipschitz 连续性。

### 4. OpenUSD spline animation proposal
来源：<https://github.com/PixarAnimationStudios/OpenUSD-proposals/blob/main/proposals/spline-animation/README.md>

关键落地点：

1. **Splines 与 time samples 在 USD 中会共存**；若同一属性同时存在两者，则提案中说明 `timeSamples` 优先。这意味着当前项目最稳妥的准备方式，是先把参数轨内部表示做成“可离散采样”的主干，并额外保留关键帧/样条元数据作为上层信息。
2. 提案把 spline 视为**稀疏源格式**，把 time samples 视为**稠密计算值**。这与本次任务高度一致：关键帧轨是作者输入，整段 `N` 帧张量矩阵是求值结果。
3. 提案强调 **constant-time Hermite evaluation** 与运行时友好性。这提示我们当前若不直接实现完整 USD spline，也应优先采用低成本、可批量化、局部控制良好的三次插值核。

### 5. Catmull-Rom splines 参考
来源：<https://graphics.cs.cmu.edu/nsp/course/15-462/Fall04/assts/catmullRom.pdf>

从 PDF 首屏可直接确认的关键落地点：

1. **Catmull-Rom 是 cubic interpolating spline**，即曲线穿过关键点，适合参数关键帧系统。
2. **Catmull-Rom 具有 C1 continuity**，满足本次任务对连续形变、避免速度突跳的基本要求。
3. **具备 local control**，这对局部编辑某几个关键帧而不影响整段轨道非常重要。

## 再次更新后的落地准则（临时）

8. 当前实现层应把“关键帧稀疏轨”和“逐帧稠密采样矩阵”明确区分，以便后续对接 USD spline / TimeSamples 双层表示。
9. 默认插值方案可优先采用 **Catmull-Rom / cubic Hermite 风格的 C1 连续插值**，并为未来更严格的 USD spline 导出保留关键帧与张量切线信息。
10. 即使导出层未来采用 TimeSamples，内部也要保留足够元数据（关键帧时间、值、插值模式、导数/切线或可重建信息）以避免只能导出线性采样结果。
