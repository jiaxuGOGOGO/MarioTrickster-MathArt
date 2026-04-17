# SESSION-053 中枢神经系统研究笔记

## 已确认研究入口

| 主题 | 来源 | URL | 当前状态 |
|---|---|---|---|
| 惯性化平滑过渡 | David Bollo / GDC 2018 PDF | https://media.gdcvault.com/gdc2018/presentations/bollo_david_inertialization_high_performance.pdf | 已打开，待提取公式与工程细节 |
| 局部相位 / 时间规整 | Starke et al. 2020 PDF | https://www.pure.ed.ac.uk/ws/files/157671564/Local_Motion_Phases_STARKE_DOA27042020_AFV.pdf | 已打开，待提取局部相位与接触对齐原则 |

## 与项目直接相关的初步判断

1. 现有项目已经具备 `transition_synthesizer.py` 的惯性化过渡基础、`gait_blend.py` 的 marker-based 相位对齐框架、以及 `runtime_bus.py` 的 JIT 规则执行基础，因此本轮更适合做 **主路径并网与统一闭环**，而不是从零发明子系统。
2. `P1-B3-1` 的关键不是再写一个新步态模块，而是把 **GaitBlender 真正接到 `pipeline.py` 的步态切换主路径**，让相位先对齐，再做惯性化残差衰减。
3. `P1-DISTILL-1A` 的关键不是单纯“能编译”，而是把 `runtime_bus` 生成的规则从物理脚锁定热路径推广到 **步态评分、复杂过渡批处理、物理惩罚批路径**。
4. 三层进化循环本轮应从 XPBD 闭环复制思想，但改造成 **Locomotion / Transition / Distill** 一体化闭环，形成“内部诊断 → 外部知识蒸馏 → 自迭代测试”的持续演进系统。

## 下一步研究重点

- 从 Bollo 材料中提炼：残差位置、残差速度、临界阻尼衰减、多通道独立惯性化、何时直接切目标姿态。
- 从 Holden / 相位论文中提炼：接触事件如何转化为相位锚点、局部时间规整如何服务复杂过渡批处理。
- 从 Mike Acton / DOD 资料中提炼：如何把规则评估组织成数组、掩码、批量 O(1) 内核，以及哪些对象需要避免 Python 字典热路径。

## DOD / JIT Brain 研究摘录

### 数据导向设计资源页

从 Data-Oriented Design 资源站与其引用材料中，可以提炼出几条对本项目最关键的工程原则：

1. **按查询组织数据，而不是按对象直觉组织类层级。** 对本项目而言，应让步态评分、过渡诊断、规则评估优先消费致密数组，而不是在热路径中反复走 `dict[str, float]` 和对象属性。
2. **热路径分支消除与掩码化。** 对复杂过渡批处理，应将规则下沉为数值阈值数组、布尔掩码与单次循环内核，避免每帧反复字符串解析。
3. **结构化数组 / SoA 优先。** 若要把 `runtime_bus` 推广到 gait blending 与 batch audit，最自然的布局是：`phase[]`、`contact[]`、`foot_lock[]`、`speed[]`、`transition_error[]` 分开存储，利于批量求值与后续 Numba/JIT。
4. **批处理而非逐对象决策。** `P1-GAP4-BATCH` 应优先做成 transition batch evaluator，而不是 N 次离散函数调用。

### Mike Acton 工作坊总结

文章最有价值的并不是某个具体容器替换，而是方法论：

1. **Know your data**：先统计，再设计。对于本项目，应先统计复杂过渡中的滑步、相位跳变、接触错位、残差峰值与规则命中率，再决定该把哪些字段放入 JIT 热路径。
2. **先问“是否真的需要这一步”**：这对当前项目意味着，不要在步态切换主路径中重复做 Python 字典拼装、重复解析同一批规则；可以预编译成批处理 evaluator。
3. **以分布而非平均值做优化**：文章强调看 percentile / histogram。对本项目而言，复杂过渡批处理中应记录 transition error 分布与最坏百分位，而不是只看均值。
4. **小而快的局部缓存优先**：可以为 phase-aligned transition batch 维护预编译 profile cache / marker cache / rule cache，减少重复构造成本。

## 当前设计方向进一步收敛

基于已读资料，本轮最合理的落地方向是：

- 用 **相位对齐 → 惯性化残差衰减** 替代单纯插值式状态切换。
- 用 **批量 TransitionCase 数组评估** 承接 `P1-GAP4-BATCH`。
- 用 **Runtime DistillBus 编译出的规则程序** 驱动步态评分、过渡批处理、物理惩罚批路径。
- 用 **三层进化闭环** 将上述三个系统绑在一起，而不是做分散的局部优化。

## 相位对齐资料补充与可达性记录

- `Runtime Motion Adaptation for Precise Character Locomotion`（ACM 2023）页面当前被 Cloudflare 验证拦截，暂未直接提取正文，但它进一步验证了一个方向：**现代工业 locomotion runtime 通常把相位参数化、接触约束与惯性化平滑组合使用**。
- `Local Motion Phases for Learning Multi-Contact Character Movements` PDF 已成功定位，但当前系统自带的 `pdftotext` 对该文件失败；后续将改用 Python PDF 解析方案提取文本。

### 阶段性研究结论

1. **惯性化** 的主价值不是“更平滑”，而是 **允许逻辑状态立即切换到目标动作，同时把连续性问题下沉为残差衰减问题**。这非常适合项目当前要消灭 walk↔run / hit / 突发受力切换滑步的需求。
2. **相位对齐** 的主价值不是“多加一个权重”，而是 **在插值前先保证接触事件同相**。这直接说明 `P1-B3-1` 应做成前置对齐层，而不是简单在 pose blend 上补补丁。
3. **DOD/JIT** 的主价值不是“把 Python 变快一点”，而是把运行时决策改造成 **预编译、数组化、批量化、可审计** 的内核。这与 `P1-DISTILL-1A` 和 `P1-GAP4-BATCH` 是强耦合关系。
