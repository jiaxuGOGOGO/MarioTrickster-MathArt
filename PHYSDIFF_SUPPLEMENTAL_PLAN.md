# 物理引导动画补充改造方案 (PhysDiff Supplemental Plan)

**作者**: Manus AI
**日期**: 2026-04-16

## 1. 审计背景与目标

在 SESSION-028 的交付中，我们初步引入了基于 Verlet 积分和 PBD 的物理投影器（`PhysicsProjector`），实现了弹簧次级运动、骨骼长度保持和基础的地面防穿透约束。然而，通过对《PhysDiff: Physics-Guided Human Motion Diffusion Model》[1] 论文的深度审计，我们发现先前的实现虽然在架构上借鉴了“生成-投影”的思想，但在几个关键机制上存在遗漏，导致未能完全解决 P0 级待办事项中的核心问题。

本补充方案旨在将 PhysDiff 论文中遗漏的核心机制（特别是脚底打滑修正和接触状态检测）以及 Kovar 等人的经典 Footskate Cleanup 算法 [2] 真正落地到本项目中，形成完整的闭环。

## 2. 核心遗漏机制分析

### 2.1 脚底打滑修正 (Foot Skating Cleanup)

PhysDiff 论文明确指出，消除脚底打滑（Foot Sliding/Skating）是其核心贡献之一。在论文的评估指标中，`Skate` 被定义为：“找到在相邻两帧中都接触地面的脚部关节，并计算它们在这些帧内的平均水平位移” [1]。

在 SESSION-028 的实现中，`GroundConstraint` 仅仅防止了脚部穿透地面（即保证 $y \ge 0$），但并未限制脚部在地面上的水平滑动。当角色的脚应该固定在地面上支撑身体时，它仍然可能随着主动画的牵引而发生不自然的滑动。

### 2.2 接触状态检测 (Contact Detection)

为了修正脚底打滑，系统必须首先知道脚在何时应该被固定。PhysDiff 通过物理模拟器隐式处理了这一点，而在我们的 2D 运动学管线中，我们需要显式的接触状态检测。根据相关研究 [3]，接触状态通常可以通过脚部关节的高度（接近地面）和速度（接近零）来启发式地判定。

### 2.3 FABRIK IK 与物理投影的联动

在先前的设计文档中，FABRIK IK 驱动的程序化步态被规划为 PHASE-4，但并未在 SESSION-028 中实现。为了实现真正的脚底固定（Foot Locking），我们需要将现有的 FABRIK 求解器集成到物理投影器中。当检测到脚部接触地面时，使用 IK 强制脚部保持在接触点，并反向调整腿部关节。

## 3. 补充改造方案

为了补齐上述遗漏，我们将对 `mathart/animation/physics_projector.py` 进行深度扩展，引入以下新机制：

### 3.1 接触状态检测器 (Contact Detector)

我们将实现一个基于启发式规则的接触状态检测器。对于指定的末端执行器（如 `l_foot` 和 `r_foot`），检测器将分析其在世界坐标系中的高度和速度。

**判定规则**：
当脚部高度低于设定的接触阈值（例如 5.0 像素），且其垂直速度向下或接近零时，判定为进入接触状态（Contact = True）。

### 3.2 基于 IK 的脚底固定 (IK-based Foot Locking)

当检测到脚部处于接触状态时，系统将记录其初始接触位置（Contact Point）。在随后的帧中，只要接触状态保持，物理投影器将强制该脚部关节固定在接触点。

为了实现这一点，我们将引入一个 `IKConstraint`。该约束利用现有的 FABRIK 求解器，在脚部被锁定的情况下，反向计算膝盖和髋关节的正确角度，确保腿部姿态自然且不发生骨骼拉伸。

### 3.3 约束平滑混合 (Constraint Blending)

正如 Kovar 等人 [2] 所指出的，简单地在每一帧独立应用 IK 会在接触状态切换（脚落地或抬起）时产生严重的视觉不连续性（Popping）。

为了解决这个问题，我们将实现约束混合机制。当脚部即将落地或刚刚抬起时，IK 约束的权重将在几帧内平滑过渡（例如从 0.0 渐变到 1.0），从而保证动画的流畅性。

## 4. 实施步骤

| 任务 ID | 描述 | 对应机制 |
| :--- | :--- | :--- |
| **SUPP-1** | 在 `physics_projector.py` 中实现 `ContactDetector`，基于高度和速度判定脚部接触状态。 | 接触状态检测 |
| **SUPP-2** | 实现 `IKConstraint`，集成 FABRIK 求解器，在接触状态下锁定脚部位置。 | 脚底打滑修正 |
| **SUPP-3** | 实现约束权重的平滑混合逻辑，消除状态切换时的突变。 | 约束平滑混合 |
| **SUPP-4** | 更新 `compute_physics_penalty` 函数，加入对 Foot Skating 的显式惩罚计算。 | 物理惩罚函数 |

## 5. 待办事项更新计划

在完成上述代码落地后，我们将更新 `PROJECT_BRAIN.json` 和 `SESSION_HANDOFF.md`，将以下任务标记为已解决或更新其状态：

- **P0-MOTION-6 (新增)**: Foot Contact Detection & Skating Cleanup（脚部接触检测与打滑清理）。
- **P0-MOTION-3 (更新)**: FABRIK IK 求解器已集成到物理投影器中，用于脚底固定。

## 参考文献

[1] Y. Yuan, J. Song, U. Iqbal, A. Vahdat, and J. Kautz, "PhysDiff: Physics-Guided Human Motion Diffusion Model," in Proceedings of the IEEE/CVF International Conference on Computer Vision (ICCV), 2023. Available: https://arxiv.org/abs/2212.02500

[2] L. Kovar, J. Schreiner, and M. Gleicher, "Footskate Cleanup for Motion Capture Editing," in ACM SIGGRAPH Symposium on Computer Animation, 2002. Available: https://research.cs.wisc.edu/graphics/Gallery/kovar.vol/Cleanup/

[3] L. Mourot et al., "UnderPressure: Deep Learning for Foot Contact Detection, Ground Reaction Force Estimation and Footskate Cleanup," 2022. Available: https://arxiv.org/abs/2208.04598
