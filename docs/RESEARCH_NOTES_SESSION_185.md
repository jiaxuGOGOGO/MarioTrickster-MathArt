# SESSION-185 外网参考研究笔记

**日期**: 2026-04-24
**研究目标**: CPPN 纹理进化引擎与流体动量 VFX 控制器的理论基础与工业实践

---

## 1. CPPN 程序化纹理生成 (Compositional Pattern Producing Networks)

### 1.1 核心论文：Stanley (2007)

> **Kenneth O. Stanley. "Compositional Pattern Producing Networks: A Novel Abstraction of Development." Genetic Programming and Evolvable Machines, 2007.**

CPPN 是一种将空间坐标 (x, y) 映射到输出值（颜色、强度等）的数学函数网络。与传统神经网络不同，CPPN 使用**异构激活函数**（sin, cos, tanh, gaussian, abs, step 等），每个节点可以使用不同的激活函数，从而产生丰富的空间模式。

**关键特性**：
- **分辨率无关性**：CPPN 定义在连续坐标空间上，同一网络可以在任意分辨率下采样
- **对称性编码**：通过输入坐标的几何变换（如 |x|）自然编码对称性
- **可进化性**：网络拓扑和权重均可通过 NEAT 算法进化

**落地方式**：`cppn_texture_backend.py` 中通过 `CPPNGenome.create_enriched()` 创建包含多种激活函数的基因组，通过 NumPy 向量化坐标矩阵评估生成纹理。

### 1.2 MAP-Elites 多样性保持：Mouret & Clune (2015)

> **Jean-Baptiste Mouret and Jeff Clune. "Illuminating search spaces by mapping elites." arXiv:1504.04909, 2015.**

MAP-Elites 是一种质量-多样性（Quality-Diversity）算法，通过维护一个行为空间的网格存档，确保进化过程中不仅优化适应度，还保持表型多样性。

**落地方式**：`cppn_texture_backend.py` 中对每个基因组施加多轮随机变异（`genome.mutate()`），确保生成的纹理在视觉上具有多样性，防止所有基因组收敛到相似的表型。

### 1.3 Fourier-CPPNs：Tesfaldet et al. (2019)

> **Tesfaldet, Matiur, et al. "Fourier-CPPNs for Image Synthesis." arXiv:1909.09273, 2019.**

Fourier-CPPNs 在标准 CPPN 基础上引入傅里叶特征编码，通过将输入坐标映射到高维傅里叶空间，使网络能够更好地表示高频细节。

**落地方式**：作为未来扩展参考。当前实现使用标准 CPPN 激活函数组合（sin, cos, tanh, gaussian），已能产生丰富的纹理模式。

---

## 2. 欧拉-拉格朗日流固耦合 (Euler-Lagrangian Fluid-Solid Coupling)

### 2.1 Stable Fluids：Jos Stam (1999)

> **Jos Stam. "Stable Fluids." SIGGRAPH 1999.**

Stam 的 Stable Fluids 方法是实时流体模拟的基石，通过以下三步实现无条件稳定的流体求解：

1. **隐式扩散 (Implicit Diffusion)**：使用隐式欧拉方法求解粘性扩散，避免显式方法的时间步长限制
2. **半拉格朗日对流 (Semi-Lagrangian Advection)**：沿速度场反向追踪粒子位置，通过双线性插值获取对流后的值
3. **压力投影 (Pressure Projection)**：通过求解泊松方程并减去压力梯度，确保速度场的无散度条件

**落地方式**：`fluid_momentum_backend.py` 中通过 `FluidMomentumController` 调用 `FluidGrid2D.step()` 执行完整的 Navier-Stokes 求解步骤。

### 2.2 GPU Gems 3, Chapter 30：Real-Time Fluid Simulation

> **NVIDIA GPU Gems 3, Chapter 30: "Real-Time Simulation and Rendering of 3D Fluids."**

该章节详细介绍了在 GPU 上实现实时流体模拟的技术，包括：

- **高斯速度溅射 (Gaussian Velocity Splatting)**：将点源速度通过高斯核函数扩散到周围网格单元
- **自由滑移边界条件 (Free-Slip Boundary)**：在边界处设置法向速度为零，允许切向滑移
- **涡度增强 (Vorticity Confinement)**：通过添加涡度力补偿数值耗散

**落地方式**：`fluid_momentum_backend.py` 中通过 `LineSegmentSplatter` 实现连续线段高斯溅射，将 UMR 运动学脉冲转化为流体场注入。

### 2.3 动画驱动 VFX：Naughty Dog / Sucker Punch 工业实践

游戏工业中，角色动画驱动 VFX 的标准管线为：

1. **运动学提取**：从骨骼动画中提取关节速度和加速度
2. **速度场注入**：将运动学速度映射到流体网格作为源项
3. **流体求解**：执行 Navier-Stokes 求解
4. **VFX 渲染**：将流体场可视化为粒子、烟雾、风压等效果

**落地方式**：`fluid_momentum_backend.py` 实现了完整的 UMR 运动学 → 流体场注入管线，通过 `UMRKinematicImpulseAdapter` 从 `UnifiedMotionClip` 中提取速度场脉冲。

---

## 3. CFL 稳定性条件 (Courant-Friedrichs-Lewy Condition)

> **Courant, R., Friedrichs, K., and Lewy, H. "Über die partiellen Differenzengleichungen der mathematischen Physik." Mathematische Annalen, 1928.**

CFL 条件是数值偏微分方程求解中的基本稳定性约束：

```
CFL = |v| * Δt / Δx ≤ 1
```

当 CFL 数超过 1 时，信息传播速度超过网格分辨率，导致数值不稳定和爆炸。

**落地方式**：`fluid_momentum_backend.py` 中实现双重速度钳制：
1. **`soft_tanh_clamp`**：通过 tanh 函数平滑限制速度幅值，避免硬截断的不连续性
2. **`np.clip`**：作为最终安全网，硬性限制速度在 `[-max_inject_speed, max_inject_speed]` 范围内

---

## 4. Mock 对象与适配器模式 (Mock Object & Adapter Pattern)

### 4.1 Mock Object Pattern

> **Gerard Meszaros. "xUnit Test Patterns: Refactoring Test Code." Addison-Wesley, 2007.**

Mock 对象是测试驱动开发中的核心模式，用于替代真实依赖，提供可控的测试环境。在本项目中，Mock 模式被提升为**运行时适配器策略**：

- **CPPN 后端**：`_generate_enriched_cppn_genomes()` 生成模拟基因组数据
- **流体后端**：`_generate_dummy_slash_clip()` 和 `_generate_dummy_dash_clip()` 生成合成 UMR 运动序列

### 4.2 Adapter Pattern

> **Gamma, Helm, Johnson, Vlissides. "Design Patterns: Elements of Reusable Object-Oriented Software." Addison-Wesley, 1994.**

适配器模式将一个类的接口转换为客户端期望的另一个接口。在本项目中：

- `cppn_texture_backend.py` 将 `CPPNGenome` 的进化接口适配为 `BackendRegistry` 的 `run_experiment()` 接口
- `fluid_momentum_backend.py` 将 `FluidMomentumController` 的模拟接口适配为 `BackendRegistry` 的 `run_experiment()` 接口

---

## 5. 优雅降级与断路器模式 (Graceful Degradation & Circuit Breaker)

### 5.1 Netflix Hystrix

> **Netflix OSS. "Hystrix: Latency and Fault Tolerance for Distributed Systems."**

Hystrix 的核心理念：当依赖服务不可用时，系统不应崩溃，而应返回降级响应。

**落地方式**：
- `fluid_momentum_backend.py` 中，当 NaN/Inf 检测到模拟结果异常时，返回降级的 `ArtifactManifest`（标记 `degraded=True`），而非抛出异常
- `cppn_texture_backend.py` 中，当纹理渲染失败时，记录警告并跳过该基因组，继续处理其余基因组

---

## 6. 研究落地总结

| 研究来源 | 核心概念 | 落地文件 | 落地位置 |
|----------|---------|---------|---------|
| Stanley (2007) CPPN | 坐标系复合数学映射 | `cppn_texture_backend.py` | 核心纹理生成算法 |
| Mouret & Clune (2015) MAP-Elites | 质量-多样性保持 | `cppn_texture_backend.py` | 基因组多轮变异策略 |
| Tesfaldet et al. (2019) Fourier-CPPNs | 频率感知合成 | 未来扩展参考 | — |
| Jos Stam (1999) Stable Fluids | 隐式扩散+半拉格朗日对流+压力投影 | `fluid_momentum_backend.py` | Navier-Stokes 求解 |
| GPU Gems 3 Ch.30 | 高斯速度溅射+自由滑移边界 | `fluid_momentum_backend.py` | 连续线段溅射 |
| Naughty Dog / Sucker Punch | 动画驱动 VFX 管线 | `fluid_momentum_backend.py` | UMR→流体场注入 |
| CFL Condition (1928) | 数值稳定性约束 | `fluid_momentum_backend.py` | 双重速度钳制 |
| Netflix Hystrix | 优雅降级 | 两个后端 | NaN 检测+降级清单 |
| xUnit Mock Object Pattern | 测试替身 | 两个后端 | Dummy 数据生成 |
| GoF Adapter Pattern | 接口适配 | 两个后端 | BackendRegistry 适配 |
