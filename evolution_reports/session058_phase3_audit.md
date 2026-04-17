# SESSION-058 Phase 3 Audit — 深潜终局：物理与运动底座补完

## 审计摘要

本次 SESSION-058 针对用户指定的 **Phase 3: 深潜终局 —— 物理与运动底座补完 (P3)**，完成了三条主线的研究、实现、测试与三层进化闭环接线。交付内容分别对应 **Taichi GPU JIT XPBD 布料后端**、**基于 SDF sphere tracing 的连续碰撞检测 (CCD)**、以及 **面向非对称/四足步态的 DeepPhase / Neural State Machine 蒸馏运行时**。此外，这三项能力被进一步接入到新的 `Phase3PhysicsEvolutionBridge` 中，使其具备项目内部自演化、外部知识蒸馏与自我迭代测试的统一循环。

| 研究目标 | 研究来源 | 代码落点 | 验证结果 |
|---|---|---|---|
| Taichi GPU JIT 编译 XPBD | 胡渊鸣 / Taichi SIGGRAPH 2019 + 官方 cloth / sparse 文档 | `mathart/animation/xpbd_taichi.py` | `tests/test_taichi_xpbd.py` 通过；50k 级 cloth 基准已在研究笔记记录 |
| SDF Sphere Tracing CCD | Erwin Coumans / Bullet CCD 思路 + 连续碰撞 TOI 文献 | `mathart/animation/sdf_ccd.py`，并接入 `xpbd_bridge.py` | `tests/test_sdf_ccd.py` 通过 |
| DeepPhase / NSM / 非对称与四足步态 | Sebastian Starke / NSM / Local Motion Phases / DeepPhase | `mathart/animation/nsm_gait.py` | `tests/test_nsm_gait.py` 通过 |
| 三层进化循环接线 | 项目既有 evolution bridge 架构 | `mathart/evolution/phase3_physics_bridge.py`、`mathart/evolution/engine.py`、`mathart/evolution/__init__.py` | `tests/test_phase3_physics_bridge.py` 通过，bridge full cycle 实测 PASS |

## 1. Taichi GPU JIT XPBD 审计

Taichi 路线的核心目标不是“把 Python 包一层 GPU 名字”，而是把现有 XPBD 语义迁移到 **可即时编译的粒子-约束网格后端**。本次实现新增 `mathart/animation/xpbd_taichi.py`，提供独立的 `TaichiXPBDClothSystem`、配置对象、诊断对象以及基准接口。其结构约束、剪切约束、重力预测、XPBD 风格迭代修正和最终速度回写均在 Taichi kernel 中完成，满足“纯 Python 语义 → JIT 编译为底层算子”的落地要求。

从审计角度看，关键不是只验证“能 import”，而是验证 **状态、锚点、重力下垂、短基准运行** 是否全部成立。因此本次新增 `tests/test_taichi_xpbd.py`，并在研究笔记中追加了 50k 粒子量级的 cloth mesh 可运行证据。虽然当前沙箱没有独立 CUDA 运行时，Taichi 在本机以 CPU fallback 启动，但接口、JIT 行为与大规模网格路径已经贯通，后续在 GPU 环境中不需要再重写算法语义。

## 2. SDF Sphere Tracing CCD 审计

原有项目已经具备 SDF terrain sensor 与 sphere tracing 基础，因此本次工作重点不是从零造一个完全无关的 CCD，而是沿着既有 SDF 场语义，把 **前一帧位置 → 当前候选位置** 的连续运动段做 TOI 求解与命中前截断。新增模块 `mathart/animation/sdf_ccd.py` 提供单粒子 trace、批量粒子批改以及直接写回 XPBD solver 的接口。

更重要的是，这一能力不是孤立存在。本次将其接入 `mathart/animation/xpbd_bridge.py`，使 XPBD 软体粒子在 solver.step 之后，可以针对环境 SDF 触发连续防穿模修正，并把命中次数与最小 TOI 回写进 frame metadata。这样它不再只是“一个工具函数”，而是已经进入项目的统一动画/物理桥路径。

| 审计项 | 结论 |
|---|---|
| 是否有 TOI 概念 | 是，`SDFSphereTracingCCD.trace_motion()` 返回 `toi` |
| 是否在穿透前截断 | 是，返回 `safe_point` 并批量修正粒子位置 |
| 是否处理法线方向速度 | 是，命中后移除沿碰撞法线的入射速度分量，避免非物理加速 |
| 是否回接 XPBD | 是，`xpbd_bridge.py` 已集成 `clamp_solver_particle_motion_with_sdf_ccd()` |
| 是否有回归测试 | 是，`tests/test_sdf_ccd.py` 通过 |

## 3. DeepPhase / NSM / 非对称与四足步态 审计

用户要求的重点并不是复刻完整论文训练管线，而是把其“可运行的工程本质”降维落地到当前项目。为此，本次新增 `mathart/animation/nsm_gait.py`，使用项目已有的 `PhaseChannel` 作为 DeepPhase 风格周期信号基底，扩展出 **逐肢体 local phase、contact probability、stance/swing 占空比、stride scale、swing height scale** 等运行时描述。

在双足路径上，`DistilledNeuralStateMachine` 可以输出左右脚不同的接触概率与目标偏移；`generate_asymmetric_biped_pose()` 则把该输出真正注入到现有 `FABRIKGaitGenerator`，因此非对称步态不是“元数据说明”，而是会进入 FABRIK IK 目标求解、覆盖腿部关节角并反馈到上身代偿。

在四足路径上，`plan_quadruped_gait()` 支持 front/hind limbs 的多接触局部相位规划。当前项目还没有完整四足骨架 IK，因此本次交付的是 **可直接驱动未来四足 rig 的 contact-phase planner**。这满足用户要求中“现有包括未来待办的实现自成一体”的条件：当前已有双足实接线，未来四足只需补 skeleton/IK，不必重构 gait intelligence。

## 4. 三层进化循环审计

本次不满足于单纯加模块，而是新增 `mathart/evolution/phase3_physics_bridge.py`，把 Taichi XPBD、SDF CCD、NSM gait 统一包装进新的三层进化桥。其 Layer 1 负责烟雾测试与指标提取，Layer 2 负责把 Hu / Coumans / Starke 的研究结论写入 `knowledge/phase3_physics_rules.md`，Layer 3 则负责持久化 `.phase3_physics_state.json` 并计算 bounded fitness bonus。与此同时，`mathart/evolution/engine.py` 与 `mathart/evolution/__init__.py` 已完成正式接线，使该桥进入全局状态巡检路径。

| 三层 | 本次实现 |
|---|---|
| Layer 1 内部进化 | 验证 Taichi cloth finite、CCD hit/TOI、安全高度、双足非对称度、四足对角相位误差、FABRIK 接线可运行 |
| Layer 2 外部知识蒸馏 | 产出 `knowledge/phase3_physics_rules.md`，固化 5 条 P3 工程规则 |
| Layer 3 自我迭代测试 | 持久化 `.phase3_physics_state.json`，维护 trend/history，并输出 fitness bonus |

## 5. 最终验证证据

本次 SESSION-058 重点验证已运行如下命令对应的回归测试：

| 命令 / 范围 | 结果 |
|---|---|
| `python3.11 -m pytest tests/test_taichi_xpbd.py -q` | PASS |
| `python3.11 -m pytest tests/test_sdf_ccd.py -q` | PASS |
| `python3.11 -m pytest tests/test_nsm_gait.py -q` | PASS |
| `python3.11 -m pytest tests/test_phase3_physics_bridge.py -q` | PASS |
| `python3.11 -m pytest tests/test_phase3_physics_bridge.py tests/test_nsm_gait.py tests/test_sdf_ccd.py tests/test_taichi_xpbd.py -q` | **13/13 PASS** |
| `Phase3PhysicsEvolutionBridge.run_full_cycle()` | **PASS** |

此外，研究笔记 `research/session058_phase3_working_notes.md` 已记录 Taichi 文献、CCD 资料、NSM/DeepPhase 研究摘要、50k cloth 证据以及 evolution bridge 运行结果。

## 6. 审计结论与剩余待办

结论是：**本次用户指定的三项核心研究内容均已进入项目代码并具备测试闭环，且已经形成仓库原生的三层演化子系统。** 其中 Taichi JIT、CCD、双足非对称 gait 属于“已明确落地”；四足部分则属于“接触相位与目标规划已落地，完整 rig/IK 尚待后续骨架侧补完”。

剩余待办不再是“有没有研究”，而是工程扩展问题：其一，将新的 `Phase3PhysicsEvolutionBridge` 进一步接入 `EvolutionOrchestrator.run_full_cycle()`，使其与 SESSION-057 的 morphology/WFC bridge 一样进入统一总编排器；其二，把 `xpbd_bridge.py` 的 CCD 钩子向更多环境 SDF 源推广；其三，在未来新增 quadruped skeleton 后，让 `plan_quadruped_gait()` 驱动真实四足 IK 求解；其四，在具备真正 GPU 环境时执行 Taichi cloth 的正式 GPU benchmark 与长期稳定性测试。
