# SESSION-184 外网参考研究笔记

**Date:** 2026-04-24
**Task:** P0-SESSION-184-SANDBOX-VALIDATOR-AND-GAIT-DISTILLATION

## 1. AST-Based Sandboxing & Zero-Trust Execution

**核心原则**: 在接收外部 LLM 生成的知识规则或动态表达式时，绝对禁止使用原生 `eval()` 或 `exec()`。

**研究来源**:
- Two Six Technologies (2022): "Hijacking the AST to Safely Handle Untrusted Python" — 使用 `ast.parse(mode='eval')` 将用户输入解析为 AST 树，通过自定义 `NodeVisitor` 遍历节点，仅允许白名单内的节点类型通过。关键技术：白名单优于黑名单（whitelist > blacklist），因为黑名单无法预见所有攻击向量。
- StackOverflow 社区共识: 使用 `ast` 模块配合白名单是 Python 中最安全的表达式求值方式，`ast.literal_eval()` 过于严格，自定义 AST walker 提供了灵活性与安全性的平衡。
- Python PEP-484 类型提示: `ast.parse` 的 `mode='eval'` 限制为单一表达式求值，从编译器层面阻止多语句注入。

**落地实践**:
- 项目 `sandbox_validator.py` 已实现 `_validate_node_whitelist()` 函数，采用 deny-by-default 策略
- 白名单节点: `Expression`, `BinOp`, `UnaryOp`, `Constant`, `Load`, `Name`(受限), `Call`(受限)
- 黑名单节点(隐式拒绝): `Attribute`, `Subscript`, `Lambda`, `ListComp`, `Import`, `Yield` 等
- `_MATH_SAFE_NAMES` 字典限制可用名称为 `sin`, `cos`, `sqrt`, `log` 等纯数学函数

## 2. Middleware Interceptor Pattern (中间件拦截器模式)

**核心原则**: 质量网关应作为预检中间件，在知识状态装载入 RuntimeDistillationBus 之前强制执行拦截验证。

**研究来源**:
- NestJS Request Lifecycle (2024): Guards → Interceptors → Pipes 的分层拦截模型，每层有明确职责
- ASP.NET Core Middleware Pipeline: 请求委托链模式，每个中间件可以短路请求或传递给下一个
- Martin Fowler "Intercepting Filter" Pattern: 在请求到达核心处理器之前，通过过滤器链进行预处理
- Google Cloud Architecture Framework: 质量门控作为 CI/CD 管线中的检查点

**落地实践**:
- 在 `knowledge_preloader.py` 的 `preload_all_distilled_knowledge()` 中插入 Validator 拦截层
- 在加载任何外部 Markdown 规则或内部 JSON 状态之前，强制调用 SandboxValidator
- 拦截器模式: `load → validate → mount` 三阶段管线

## 3. Graceful Degradation in Policy Gateways (策略网关的优雅降级)

**核心原则**: 当验证器拦截到非法规则时，绝对禁止让整个预热加载流崩溃。

**研究来源**:
- Google Cloud Architecture Framework "Design for Graceful Degradation": 系统在部分组件失败时应继续以降低的性能运行
- CMU S3D-25-104 "Architecture-based Graceful Degradation for Cybersecurity" (2025): 基于架构的优雅降级，通过规则隔离实现故障容忍
- SRE School "Graceful Degradation in SRE" (2025): 维持有限功能，防止完全中断
- The Coder Cafe "Graceful Degradation Explained" (2024): 计划降级以减少完全中断风险

**落地实践**:
- 采用 "丢弃异常规则 + Clamp 裁剪 + 记录预警日志 + 允许系统携健康规则继续启动" 策略
- 使用 `logger.warning` 打印黄字警告，不使用 `raise` 抛出致死异常
- 使用 `continue` 跳过脏数据，放行健康知识点

## 4. Automated Kinematic Sweeping (自动化运动学扫参蒸馏)

**核心原则**: 物理步态科研模块通过高并发或网格搜索寻找局部最优解，算力开销大，必须作为独立微内核挂载。

**研究来源**:
- NVIDIA Isaac Gym "Domain Randomization" (2021): 在训练期间反复随机化仿真动力学参数，学习在广泛物理参数范围下的鲁棒策略。参数蒸馏通过自动扫描物理参数组合提取 NaN 稳定、低误差配置。
- Google Vizier / MLPerf: 硬件感知多目标优化，Pareto 前沿提取平衡物理质量与计算成本
- Macklin & Müller (2016) XPBD: 合规性 α̃ = α/Δt²，子步数，阻尼是三个关键物理稳定性旋钮
- Macklin et al. (2019) "Small Steps in Physics Simulation": 子步方法以更少的数值阻尼实现刚度
- Clavet GDC 2016 "Motion Matching": 步态参数化，混合时间和相位对齐权重从脚滑惩罚分数反向推导
- Holden et al. (2020) "Learned Motion Matching": 脚滑与相位对齐质量成反比

**落地实践**:
- `PhysicsGaitDistillationBackend` 已实现完整的网格搜索 + Pareto 排序管线
- 通过 `@register_backend` 装饰器注册为微内核插件
- 输出路由到 `workspace/laboratory/physics_gait_distill/` 专属沙盒
- 产出符合实验性沙盒数据隔离规范的 JSON 知识资产
