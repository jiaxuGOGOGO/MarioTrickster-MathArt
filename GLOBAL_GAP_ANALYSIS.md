# MarioTrickster-MathArt 全局差距清单与解决方案搜集指南

> **文档目标**：全面梳理项目当前（v0.33.0 / SESSION-042）存在的所有技术差距、架构断裂与商业基准短板。本清单旨在为后续的"精准并行研究协议"和技术攻坚提供明确的靶点。

---

## 核心架构与闭环差距 (The "Brain" Gaps)

这是阻碍项目从"手动工具"进化为"自主系统"的根本性架构断裂。

### Gap A1: 评估→导出的参数收敛闭环断裂 (P0-EVAL-BRIDGE)
**现状**：Layer 3 评估器（`MotionMatchingEvaluator`、`VisualRegressionEvolutionBridge`）能产出高质量的 fitness 分数和诊断规则（如"脚部滑动过多"），但这些数据仅被写入日志和报告，**不会自动回流**到下一次 `produce_character_pack()` 的参数空间中。
**搜集方向**：
- 如何构建轻量级的闭环参数优化器（Closed-loop Parameter Optimizer）？
- 游戏引擎中基于反馈的程序化动画调参方案（如基于梯度的可微物理，或贝叶斯优化）。
- 诊断规则（文本/分类）到连续参数空间（如 `foot_lock_strength`）的自动映射算法。

### Gap A2: 全局蒸馏总线未接入运行时 (P0-DISTILL-1)
**现状**：项目拥有强大的知识蒸馏系统（`distill/`），能将论文解析为 `ParameterSpace` 约束。但目前这些约束仅在代码注释中被引用，**没有任何生产代码在运行时自动消费编译后的知识**。
**搜集方向**：
- 依赖注入（Dependency Injection）在 Python 科学计算/数据管线中的最佳实践。
- 如何在不破坏性能的前提下，让底层数学模块（如 SDF 渲染、物理积分）在运行时动态读取并遵守全局 `ParameterSpace` 约束。

---

## 物理与运动引擎差距 (The "Body" Gaps)

这是决定动画"物理真实感"和"认知合理性"的核心维度，直接对标商业基准。

### Gap B1: 刚体与柔体的双向耦合 (P0-GAP-2)
**现状**：目前的物理投影器（`PhysicsProjector`）仅处理基于关节的刚体运动。缺乏对衣物、头发、肌肉等柔性附属物的物理模拟，导致角色运动缺乏"二次动画（Secondary Motion）"。
**搜集方向**：
- **XPBD (Extended Position Based Dynamics)** 在 2D/2.5D 角色动画中的轻量级实现。
- 刚体骨骼与 XPBD 柔体网格的双向耦合（Two-way coupling）算法。
- 适合像素画渲染的低分辨率质点-弹簧系统（Mass-Spring System）。

### Gap B2: 场景感知的距离匹配传感器 (P1-PHASE-37A)
**现状**：跳跃（Jump）和下落（Fall）虽然已升级为原生瞬态相位（Transient Phase），但其"距离到地面"（distance-to-ground）和"距离到顶点"（distance-to-apex）的计算是**解析式/分析式**的，假设地面永远是平的（$y=0$）。
**搜集方向**：
- 现代 3D 游戏（如《最后生还者2》、《战神》）中的 Motion Matching 射线检测（Raycast）传感器设计。
- 2D 平台游戏中的地形自适应（Terrain-adaptive）相位调制算法。
- 动态碰撞环境下的着陆预测（Landing Prediction）数学模型。

### Gap B3: 步态过渡的相位保持混合 (P1-PHASE-33A)
**现状**：虽然实现了惯性化过渡（Inertialization），但在 Walk、Run、Sneak 等不同周期性步态之间切换时，缺乏对**相位连续性**的严格保持，可能导致脚步滑步或动作抽搐。
**搜集方向**：
- 基于相位的步态混合（Phase-Synchronized Blending）算法。
- 频率不同（如 Walk 慢，Run 快）的周期动画如何进行时间扭曲（Time Warping）以对齐相位。

---

## 视觉与渲染管线差距 (The "Look" Gaps)

这是项目从"技术 Demo"走向"商业资产"的最后一公里。

### Gap C1: 工业级渲染器的缺失 (P1-INDUSTRIAL-34A/C)
**现状**：目前的渲染完全依赖纯数学的 SDF（有符号距离场），视觉表现力达到硬上限，缺乏商业像素画的质感（如《死亡细胞》的法线贴图+光照）。
**搜集方向**：
- 《死亡细胞》（Dead Cells）的 3D-to-2D 像素化渲染管线全流程解析。
- 骨骼动画 → 正交渲染 → 无抗锯齿降采样 → 法线贴图卡通渲染（Cel-shading）的自动化实现。
- 伪 3D（Pseudo-3D）纸娃娃系统或双四元数（Dual Quaternion）网格蒙皮在 2D 引擎中的应用。

### Gap C2: 物理驱动的粒子特效 (P1-VFX-1)
**现状**：VFX 管线存在，但特效（如攻击光效、烟尘）未与角色的物理状态（速度、碰撞、质量）强绑定，缺乏打击感。
**搜集方向**：
- 2D 游戏中的物理驱动粒子系统（Physics-driven Particle System）。
- 基于角色运动矢量场（Motion Vector Field）的流体/烟雾扰动算法。

### Gap C3: 模拟条件下的神经渲染桥接 (P1-AI-2)
**现状**：项目愿景是"数学保证动得对，AI保证看得美"，但目前缺乏将物理/运动数据导出为 AI 模型（如 ControlNet、Wan2.2）条件输入的桥接层。
**搜集方向**：
- 如何将 2D 骨骼、深度图、运动矢量（Motion Vectors）编码为 ControlNet/ComfyUI 可识别的控制信号。
- 保持 AI 视频生成时间连贯性（Temporal Consistency）的工程实践。

---

## 工程与测试覆盖差距 (The "Infrastructure" Gaps)

### Gap D1: 端到端测试覆盖面不足 (P1-E2E-COVERAGE)
**现状**：`headless_e2e_ci.py` 实现了严密的视觉回归测试，但目前仅覆盖了 `mario` 预设的周期性动作。未显式触发 `BiomechanicsProjector`（肌肉张力）、`MotionMatchingRuntime`（运行时查询）和 `TransitionSynthesizer`（惯性化过渡）。
**搜集方向**：
- 游戏引擎 CI/CD 中的复杂状态机遍历测试策略。
- 自动化生成覆盖所有动画状态转换边（Transition Edges）的测试预设。

### Gap D2: 遗留 API 表面积清理 (P2-PHASE-CLEANUP)
**现状**：内部已完全统一到 `PhaseState` 和 `generate_frame()`，但旧的 API（如 `walk_animation()`）仍暴露在 `__all__` 中，存在语义混淆风险。
**搜集方向**：
- Python 库的大规模 API 废弃（Deprecation）与向后兼容策略。

---

## 总结：下一步研究协议触发建议

当启动**精准并行研究协议**时，建议优先针对以下三个靶点输入 Query：

1. **针对 Gap A1 (P0)**: `"Closed-loop parameter optimization for procedural animation feedback"`
2. **针对 Gap B1 (P0)**: `"XPBD soft body coupling with rigid body skeleton 2D"`
3. **针对 Gap C1 (P1)**: `"Dead Cells 3D to 2D pixel art rendering pipeline normal map"`
