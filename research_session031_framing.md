# SESSION-031 Research Framing

| Field | Value |
|------|-------|
| **subsystem** | `mathart/animation` 与 `mathart/evolution` 的三层进化闭环，重点是 2D 动画骨架表示、姿态先验、旋转表示与动作检索/拼接。 |
| **decision_needed** | 如何把 **SMPL / SMPL-X**、**VPoser**、**Dual Quaternions**、**Motion Matching** 以“低维数学中间层”的方式融入现有项目，使其先服务于 2D / 伪3D 平台动画，又为未来真实 3D 扩展保留兼容接口。 |
| **already_known** | 仓库已具备 PhysDiff 风格投影、Verlet/PBD、FABRIK、ZMP/IPM、生物力学、PD 控制、MuJoCo 风格接触、RL Locomotion、ASE、Layer 3 Physics Evolution。当前缺口不在基础物理，而在 **统一人体参数化、姿态先验约束、无万向节旋转表示、动作数据库检索与平滑拼接**。 |
| **duplicate_forbidden** | 不重复研究：CPPN、OKLAB、Verlet、FABRIK、PhysDiff、基础像素画标准、基础 WFC、基础粒子物理、ASE/DeepMimic 的已落地内容。仅保留能直接指导新增模块、评价指标、知识蒸馏、2D/伪3D/3D 兼容接口设计的资料。 |
| **success_signal** | 有用资料必须至少提供以下之一：1) 可压缩到低维形体/姿态向量的表示；2) 解剖学姿态先验或概率约束；3) 适合实时动画的旋转/蒙皮插值方案；4) 动作特征向量、代价函数、数据库检索、过渡拼接的工程模式；5) 可映射到本项目文件结构的实现接口。 |

## Initial Research Questions

1. SMPL / SMPL-X 在本项目里应被降维成什么样的 **2D-compatible body latent**？
2. VPoser 是否应作为 Layer 3 的姿态可行性约束器与诊断器，而不是生成器本身？
3. Dual Quaternion 在 2D 项目里是否仍有价值，若有，应作为未来伪3D/3D 扩展的旋转与蒙皮后端吗？
4. Motion Matching 能否先在 2D 以“关节轨迹特征检索器”落地，再向 3D 扩展？
5. 三层进化循环中，这些新模块各自属于哪一层，如何彼此反馈？

## Browser-Captured Findings Snapshot 01

### Repository state

The repository already positions itself as a **three-layer self-evolving math-driven asset brain** with strong 2D animation physics, biomechanics, RL locomotion, and distillation infrastructure. The new integration must therefore **avoid duplicating low-level physics** and instead add a **lower-dimensional body representation + pose prior + rotation backend + motion retrieval layer**.

### SMPL-X official page

1. **SMPL-X extends SMPL** into a unified expressive body model with articulated **body, hands, and face**.
2. The official abstract explicitly states that fitting benefits from a **neural network pose prior trained on a large MoCap dataset**, which strongly supports treating **VPoser-like latent priors as a feasibility filter** rather than a renderer.
3. The official ecosystem also references **conversion between SMPL-family models**, suggesting that future-proofing the project around an abstract body-parameter interface is better than hardwiring to one concrete topology.
4. For MarioTrickster-MathArt, the immediate value is **not full mesh reconstruction**, but using the same idea of low-dimensional **shape + pose parameters** as a mathematically stable intermediate state that can drive 2D rigs now and pseudo-3D / 3D later.

## Browser-Captured Findings Snapshot 02

### VPoser repository

1. VPoser is explicitly described as a **variational human pose prior** trained on **AMASS** and represented over **SMPL pose parameters**.
2. The repository emphasizes that it **penalizes impossible poses while admitting valid ones**, **models correlations among joints**, and provides an **efficient low-dimensional representation** of human pose.
3. It is also positioned as an **IK solver companion**, including optimization over body pose, translation, and global orientation from 2D or 3D keypoints.
4. This strongly suggests a MarioTrickster integration role as a **Pose Prior Projector / Feasibility Filter / IK regularizer**, not a mesh-heavy dependency.

### Motion Matching paper entry view

1. The paper title and abstract confirm the core principle: **generate character animation by blending and transitioning between prerecorded animation sequences multiple times per second**.
2. The system is positioned as a response-generation method for more **natural, dynamic animation** than handcrafted state machines.
3. For this repository, the immediate hypothesis is to compress motion states into a **small feature vector database** and use query-time retrieval rather than simulate full mesh trajectories.
4. The next step is to extract the paper text directly so that feature definitions and cost terms can be converted into concrete classes and tests.

## Browser-Captured Findings Snapshot 03

### Motion Matching in O3DE

1. Motion Matching is framed as a **data-driven alternative to brittle animation graphs**, selecting and blending prerecorded motions multiple times per second.
2. The core abstraction is a **feature schema** plus a **feature matrix**. Features can include joint positions, linear/angular velocities, and root trajectory history/future trajectory.
3. Features are stored **relative to the root / motion extraction joint in model space**, making retrieval invariant to world position and facing. This is highly compatible with a 2D platformer adaptation.
4. Each runtime query builds a **query vector** using the same schema; candidate frames are compared by feature-wise residuals / weighted squared distance.
5. Practical guidance from the article: a strong baseline schema is **root trajectory + left/right foot positions + left/right foot velocities**.
6. For MarioTrickster-MathArt, this points toward a first implementation as a **low-dimensional pose-and-trajectory database** for walk/run/jump/fall state retrieval rather than a full 3D mocap stack.

## Browser-Captured Findings Snapshot 04

### Dual Quaternion tutorial and paper

1. Dual Quaternion Skinning (DQS) is repeatedly motivated as a replacement for **Linear Blend Skinning (LBS)** because simple matrix blending introduces **non-rigid scale artifacts** that cause **volume loss / candy-wrapper twisting** around joints.
2. The tutorial makes the implementation path concrete: a rigid transform can be represented with **eight floats** (real quaternion + dual quaternion part), converted from a 4x4 bone transform, blended by weights, and normalized to remain a rigid transform.
3. The Kavan paper front page visually and numerically reinforces two project-relevant points: **better deformation quality** than matrix/log-matrix blending and **real-time performance viability**.
4. For MarioTrickster-MathArt, DQS is not a priority for today's pure-2D raster output path, but it is highly valuable as the **future rotation / pseudo-3D skinning backend** once the project starts emitting pose-driven mesh shells, paper-doll rigs, or 2.5D limb volumes.
5. Immediate landing strategy should therefore be **abstraction-first**: add dual-quaternion math utilities and pose-rotation interfaces now, while keeping the visible renderer on 2D joints/sprites.
