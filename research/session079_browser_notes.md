# SESSION-079 外部参考研究笔记

## 1. Ubisoft Motion Matching / Simon Clavet GDC 2016

来源页面：<https://www.gdcvault.com/play/1023280/Motion-Matching-and-The-Road>

提炼出的可落地约束：

1. Motion Matching 的核心运行时策略是：在运行时持续从动作数据库中找到同时匹配“当前姿态”和“未来计划”的帧，然后以极小 blend time 切换过去。这意味着高非线性片段不能只被当作普通 gait 参数的连续外推，而应被视为另一类需要单独建模与查询的动作域。
2. 页面概述虽然没有完整展开 transient state 细节，但它明确强调“无需手工组织 starts / stops / turns 等过渡动画，而是在运行时针对语义化标记和期望未来轨迹做匹配”。这支持本轮任务里的红线：jump / fall / hit 不能偷塞进 steady-state gait 配置，而要形成独立的瞬态参数契约和动作族切片。
3. 对本项目的工程含义是：高非线性片段的恢复参数、阻尼参数、预备窗口参数应该与平稳步态配置隔离，并在状态进入或 lane binding 时一次性解析，而不是在热路径上持续分支判断。

## 2. EA Frostbite / Introduction to Data Oriented Design

来源页面：<https://www.ea.com/frostbite/news/introduction-to-data-oriented-design>

提炼出的可落地约束：

1. 文章的明确主题是：Data-Oriented Design 的收益来自按数据形状组织计算，而不是按对象边界分散逻辑；同时强调“计算本身的代价往往低于读内存的代价”。
2. 对本轮任务的直接启发是：瞬态动作配置必须收拢成高内聚 Config Object，而不是把 `recovery_half_life`、`impact_damping_weight`、`landing_anticipation_window` 等值零散地挂在多个对象或热路径查表里。
3. 对热路径纪律的可落地翻译是：在动作节点生成（lane binding）或状态进入时，一次性 resolve `transient_motion.*` 命名空间，构造独立的 `UnifiedTransitionRuntimeConfig`，之后让每帧计算只消费已经排布好的标量，避免动态字符串解析和语义混线。

## 3. Google Vizier for Multi-Objective Optimization (MOO)

来源页面：<https://medium.com/google-cloud/google-vizier-for-multi-objective-optimization-moo-ce607e3e5ee3>

提炼出的可落地约束：

1. Vizier 对多目标优化的直接定义是：同时优化多个往往相互冲突的目标，并返回一组 Pareto front 最优解，而不是只输出单一平均分冠军。
2. 文中给出的工程接口很关键：objective 需要以带有 `metric_id` 和 `goal` 的独立度量项上报；trial 需要报告多个 measurements；最终通过 `get_pareto_optimal_trials()` 读取前沿解集。
3. 对本项目的直接映射是：P1-GAP4-BATCH 不应该为 jump / fall / hit 分别定义彼此不可比的“方言分数”，而应统一上报正交指标，例如 `frames_to_stability`、`peak_residual`、`peak_jerk`、`peak_root_velocity_delta` 之类，在同一数学空间上做 Pareto 排序。
4. 文中同时强调 parallel trials、early stopping、conditional search，这意味着我们当前的闭环评估入口应保持“试验参数 → 统一测量 → Pareto 选择”的结构，而不要把动作族判断和评分细节散落在热路径里。

## 4. Google Vizier: A Service for Black-Box Optimization

来源页面：<https://research.google.com/pubs/archive/46180.pdf>

从论文首页可直接确认的要点：

1. 论文将 Vizier定义为面向黑盒优化的服务，目标是在有限预算下优化目标函数；系统通过多次 evaluation / measurement 来选择后续试验点。
2. 首页摘要明确强调：Vizier 是服务化的黑盒优化基础设施，支持多种算法并提供可扩展的调参工作流，而不是单次脚本式穷举。
3. 对本轮任务的工程含义是：Layer 3 批量闭环不应只生成一次性的 best guess，而应继续保持 `trial` / `measurement` / `Pareto frontier` 这种结构，使新加入的 transient metrics 能自然并入既有搜索框架。

## 5. Ubisoft / Ragdoll Motion Matching

来源页面：<https://staticctf.ubisoft.com/J3yJr34U2pZ2Ieem48Dwy9uqj5PNUQTn/74NXgJKzhhZw5sy4XsRag8/1327abfd28611ed5fd5e66efbdfb8a17/GDC20RagdollMotionMatching4.pdf>

从演示文稿首页可直接确认的要点：

1. Ubisoft 将 Motion Matching 进一步推进到了 ragdoll / physically simulated 角色恢复场景，说明 Motion Matching 并不只服务于 steady locomotion，而可以覆盖物理驱动的高非线性恢复问题。
2. 这一点对本轮任务的意义非常直接：run->jump、fall->land、hit_stagger 等转场不应继续依赖 steady-state gait 的参数语义，而应以“瞬态恢复”视角拥有独立配置对象和独立评估窗口。
3. 工程上，这支持我们把 impact 后恢复速度、落地前预备窗口、冲击阻尼权重等量放到 `UnifiedTransitionRuntimeConfig` 而不是 `UnifiedGaitRuntimeConfig` 中。

## 6. MLPerf Endpoints

来源页面：<https://mlcommons.org/benchmarks/endpoints/>

提炼出的可落地约束：

1. MLPerf Endpoints 强调系统性能是“复杂、非线性、多维曲面”，并明确指出真实世界里延迟会在利用率接近峰值时突然爆炸，**只看平均值会忽略现实**。
2. 文中明确提出 **Standardized Pareto curves**，每次 run 应捕获 Throughput、Interactivity、TTFT、Query Latency 等多维指标，让用户按具体使用场景查看整个 operating range，而不是单点峰值。
3. 对本轮任务的直接翻译是：jump/fall/hit 的批量闭环不能把数十帧误差简单平均掉，而应在“瞬态窗口”内抓取 peak / convergence 类指标，例如 `peak_residual`、`peak_jerk`、`frames_to_stability`，再把这些指标喂给 Pareto 选择器。
4. 文中同时强调透明、可复现、可审计、自包含，这直接支持我们将瞬态批量调参与遥测结果继续保存在 typed JSON / manifest / knowledge 资产中，而不是只在测试日志里输出临时数字。

## 7. 汇总后的实现准则

结合以上资料，本轮 P1-GAP4-BATCH 的落地必须满足以下研究约束：

1. **语义隔离**：steady gait 与 transient recovery 必须是平级数据域，不可混用配置对象。
2. **一次性解析**：所有 `transient_motion.*` 参数都应在状态进入或 lane binding 时完成 resolve，热路径只消费已排布标量。
3. **统一度量空间**：jump / fall / hit 必须共享正交可比指标，而非各写一套不可横向比较的分数方言。
4. **峰值 + 收敛优先**：瞬态质量主要看 transient window 内的峰值残差、峰值 jerk、收敛帧数，而不是整段平均误差。
5. **可审计闭环**：批量评估、Pareto 选择、知识写入、运行时 preload/resolve、以及极值参数 E2E 偏转证明都必须同时成立，才算真正闭环。

