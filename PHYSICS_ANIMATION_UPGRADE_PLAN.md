# 物理引导动画升级方案 (Physics-Guided Animation Upgrade Plan)

**作者**: Manus AI
**日期**: 2026-04-16

## 1. 背景与目标

当前 `MarioTrickster-MathArt` 项目的动画系统主要依赖于纯数学变换（如正弦波、缓动函数）和关键帧插值。虽然这种方法在计算上非常高效，但它缺乏物理世界的真实感，导致生成的角色动画显得僵硬，缺乏次级运动（Secondary Motion）、重量感和与环境的自然交互。

根据 `SESSION_HANDOFF.md` 和 `PROJECT_BRAIN.json` 中的 P0 级待办事项，本项目亟需解决以下核心问题：
- **P0-MOTION-1**: 缺乏物理模拟（Verlet积分物理引擎）。
- **P0-MOTION-2**: 缺乏质量-弹簧系统驱动的次级动画。
- **P0-MOTION-3**: 缺乏 FABRIK IK 求解器驱动的程序化步态。
- **P0-MOTION-5**: 缺乏认知运动约束（预备动作、跟随动作等）。

本方案旨在借鉴学术界前沿的**物理引导扩散模型（Physics-Guided Diffusion Models, 如 PhysDiff）**和**可微物理引擎（Differentiable Physics Engines）**的核心思想，将其降维并落地到本项目的 2D 像素动画场景中。

## 2. 核心研究发现与理论基础

通过对 PhysDiff、PINNs（物理信息神经网络）和可微物理引擎的并行深度研究，我们提取了以下可直接应用于本项目的核心理论：

### 2.1 物理投影 (Physics Projection) 的降维应用

PhysDiff [1] 的核心创新在于其“生成-投影-修正”的迭代循环。在扩散模型的去噪过程中，它使用物理模拟器将不合法的运动状态“投影”回物理可行的子空间，从而消除脚底打滑和悬空等现象。

在我们的 2D 骨骼动画场景中，我们不需要完整的扩散模型，但可以完美复用**物理投影**的思想。我们可以将现有的动画生成器（如 `presets.py` 中的函数）视为“初步生成器”，然后引入一个轻量级的 2D 物理引擎作为“投影仪”。在每一帧渲染前，物理引擎接收初步的骨骼姿态，通过施加物理约束（如地面碰撞、骨骼长度保持），输出一个经过物理修正的最终姿态。

### 2.2 Verlet 积分与基于位置的动力学 (PBD)

为了实现高效且稳定的物理投影，我们选择 **Verlet 积分** [2] 结合**基于位置的动力学 (Position-Based Dynamics, PBD)**。

Verlet 积分不显式存储速度，而是通过当前位置 $x(t)$ 和上一帧位置 $x(t-\Delta t)$ 来隐式计算速度，其更新公式为：
$$x(t+\Delta t) = x(t) + (x(t) - x(t-\Delta t)) + a(t)\Delta t^2$$

PBD 的优势在于它通过直接修改质点的位置来满足约束（如骨骼长度固定），这比基于力的约束求解更稳定，非常适合用于角色骨骼的物理模拟。

### 2.3 质量-弹簧-阻尼系统 (Mass-Spring-Damper System)

对于头发、披风等次级动画，我们将采用经典的质量-弹簧-阻尼系统 [3]。根据胡克定律，弹簧力 $F_{spring}$ 与形变量成正比，阻尼力 $F_{damping}$ 与相对速度成正比。这能自然地产生跟随动作 (Follow-through) 和重叠动作 (Overlapping Action)。

## 3. 架构设计与改造方案

为了将上述理论落地，我们计划在 `mathart/animation/` 目录下引入一个新的模块 `physics_projector.py`，并对现有的 `character_renderer.py` 和 `pipeline.py` 进行非侵入式改造。

### 3.1 新增模块：`PhysicsProjector`

该模块将封装轻量级的 2D 物理引擎，作为动画的后处理层。

**核心数据结构：**
- `Particle`: 代表骨骼关节点，包含 `pos`, `old_pos`, `acceleration`, `mass`, `is_pinned`（是否被主动画固定）等属性。
- `DistanceConstraint`: 代表骨骼，强制两个 `Particle` 保持固定距离。
- `SpringConstraint`: 代表弹性连接，用于次级动画。
- `GroundConstraint`: 地面碰撞约束，防止 `Particle` 穿透设定的地面高度。

**核心算法流程 (`project` 方法)：**
1. **状态同步**：接收来自主动画的姿态字典，更新所有 `is_pinned=True` 的 `Particle` 的位置。
2. **施加外力**：对所有非固定 `Particle` 施加重力。
3. **Verlet 积分**：更新所有非固定 `Particle` 的位置。
4. **约束求解 (松弛迭代)**：迭代多次（如 8 次），依次满足 `DistanceConstraint`、`SpringConstraint` 和 `GroundConstraint`，将不合法的位置“投影”回合法空间。
5. **结果输出**：将物理模拟后的 `Particle` 位置转换回姿态字典，供渲染器使用。

### 3.2 现有模块改造

- **`mathart/animation/skeleton.py`**: 增加方法以方便提取和应用物理粒子状态。
- **`mathart/animation/character_renderer.py`**: 保持其纯渲染职责不变。它将接收经过 `PhysicsProjector` 修正后的姿态字典进行渲染。
- **`mathart/pipeline.py`**: 在 `produce_character_pack` 方法中，在生成每一帧的原始姿态后，调用 `PhysicsProjector` 进行物理修正，然后再传递给渲染器。

## 4. 实施步骤与优先级

| 任务 ID | 描述 | 优先级 | 对应 P0 待办 |
| :--- | :--- | :--- | :--- |
| **PHASE-1** | 实现 `PhysicsProjector` 核心逻辑（Particle, Verlet 积分, DistanceConstraint）。 | 高 | P0-MOTION-1 |
| **PHASE-2** | 在 `pipeline.py` 中集成 `PhysicsProjector`，实现基础的“生成-投影”循环。 | 高 | P0-MOTION-1 |
| **PHASE-3** | 实现 `SpringConstraint`，为角色预设添加次级动画（如头发、披风的物理节点）。 | 中 | P0-MOTION-2 |
| **PHASE-4** | 实现 `GroundConstraint` 和基于 FABRIK 的简单 IK 步态修正。 | 中 | P0-MOTION-3 |

## 5. 参数调优建议

根据研究 [4]，以下是 2D 物理动画的推荐参数范围：
- **时间步长 (`dt`)**: 固定为 `1.0 / 60.0` 秒，以保证 Verlet 积分的稳定性。
- **松弛迭代次数 (`solver_iterations`)**: `4` 到 `16` 次。建议默认设为 `8`。
- **重力 (`gravity`)**: `(0, -9.8)` 到 `(0, -20.0)`，根据所需的“重量感”调整。
- **弹簧刚度 (`k`)**: `50.0` 到 `500.0`。
- **阻尼系数 (`c`)**: `2.0` 到 `20.0`。

## 参考文献

[1] F. Z. Dou et al., "PhysDiff: Physics-Guided Human Motion Diffusion Model," ICCV 2023. Available: https://arxiv.org/abs/2212.02500
[2] T. Jakobsen, "Advanced Character Physics," Game Developers Conference, 2001. Available: https://www.cs.cmu.edu/afs/cs/academic/class/15462-s13/www/lec_slides/Jakobsen.pdf
[3] M. Heckel, "The physics behind spring animations," 2021. Available: https://blog.maximeheckel.com/posts/the-physics-behind-spring-animations/
[4] "Verlet Integration and Cloth Physics Simulation," Pikuma. Available: https://pikuma.com/blog/verlet-integration-2d-cloth-physics-simulation
