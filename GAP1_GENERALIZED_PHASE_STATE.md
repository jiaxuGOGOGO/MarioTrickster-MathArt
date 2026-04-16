# 架构演进：广义相位状态（Generalized Phase State）

## 1. 背景与痛点分析

在当前的 `MarioTrickster-MathArt` 项目中，动画引擎的核心是 `PhaseDrivenAnimator`，它基于 PFNN（Phase-Functioned Neural Networks）的理念构建。PFNN 的核心假设是运动是无限循环的，因此相位变量 $p$ 被定义为一个在 $[0, 1)$ 之间循环的标量。

然而，这种设计在处理跳跃（Jump）、受击（Hit）等一次性的**非周期（Transient/Aperiodic）**动作时遇到了拓扑学上的撕裂。目前项目通过引入 `TransientPhaseVariable` 和在 `UnifiedMotionFrame` 的 `metadata` 中附加 `phase_kind` 标签来绕过主干，这虽然是一个合理的妥协，但破坏了引擎的绝对统一性。

## 2. 学术理论支撑

为了解决这一问题，我们引入了 Sebastian Starke 等人在 SIGGRAPH 2020 和 2022 发表的两项核心研究成果：

1. **Local Motion Phases (SIGGRAPH 2020)** [1]：打破了“全身共用一个全局周期相位”的限制，提出为非周期动作定义独立的、从 $0 \to 1$ 激活并衰减的“局部相位”。
2. **DeepPhase: Periodic Autoencoders (SIGGRAPH 2022)** [2]：将一维的标量相位升级为多维高维向量（Latent Channels）。在这个流形中，行走是某些通道的正弦波动，而受击则是某个通道的“一次性脉冲（Activation Spike）”。根据 Ian Mason 的解释，PAE 提取的相位特征包括振幅（A）、频率（F）、相位偏移（S）和直流偏移（B）。对于非周期性运动，其振幅 A 会经历从 0 到峰值再回到 0 的过程，而频率 F 则捕捉了该瞬态运动的时间尺度。

## 3. 广义相位状态（PhaseState）设计

为了彻底淘汰适配器，我们将重构 `PhaseDrivenAnimator` 的输入输出，引入 `PhaseState` 对象。

### 3.1 数据结构定义

```python
@dataclass(frozen=True)
class PhaseState:
    """广义相位状态，统一周期性与非周期性运动的相位表示。"""
    value: float          # 相位值，周期性为 [0, 1)，非周期性为 [0, 1]
    is_cyclic: bool       # 是否为周期性运动
    phase_kind: str       # 相位类型（如 "cyclic", "distance_to_apex", "hit_recovery"）
    amplitude: float = 1.0 # 振幅（DeepPhase 风格，用于非周期性脉冲的强度）
    
    def to_float(self) -> float:
        """向后兼容的标量输出"""
        return self.value % 1.0 if self.is_cyclic else max(0.0, min(1.0, self.value))
```

### 3.2 引擎内部的多路复用（Multiplexer）

`PhaseDrivenAnimator.generate_frame(PhaseState)` 将成为绝对唯一的统一入口。在引擎底层，我们将引入门控（Gating）机制：

- **周期性分支（`is_cyclic == True`）**：插值引擎经过传统的三角函数（$Sin/Cos$）映射，驱动行走、奔跑等循环帧。
- **非周期性分支（`is_cyclic == False`）**：插值引擎绕过三角映射，将传入的 $0 \to 1.0$ 标量直接作为贝塞尔/样条曲线的时间参数（$t$）驱动跳跃、受击等一次性帧。

### 3.3 淘汰适配器

原有的 `TransientPhaseVariable` 将从一个外挂适配器，转变为为统一引擎提供 `PhaseState` 的数据生成源。所有下游消费者（如 `runtime_motion_query.py` 和 `motion_matching_evaluator.py`）将直接消费 `PhaseState` 对象，而不再依赖 `metadata.phase_kind` 进行分支判断。

## 4. 实施计划

1. **重构 `unified_motion.py`**：引入 `PhaseState` 数据类，并更新 `UnifiedMotionFrame` 以接受 `PhaseState`。
2. **重构 `phase_driven.py`**：更新 `PhaseDrivenAnimator` 的 `generate_frame` 方法，实现门控多路复用。
3. **更新下游消费者**：修改 `runtime_motion_query.py` 和 `motion_matching_evaluator.py`，使其直接处理 `PhaseState`。
4. **更新管道契约**：修改 `pipeline_contract.py`，确保验证器支持新的 `PhaseState` 序列化格式。

## 参考文献

[1] Sebastian Starke, Yiwei Zhao, Taku Komura, and Kazi Zaman. 2020. Local Motion Phases for Learning Multi-Contact Character Movements. ACM Transactions on Graphics (TOG) 39, 4, Article 54. https://doi.org/10.1145/3386569.3392450
[2] Sebastian Starke, Ian Mason, and Taku Komura. 2022. DeepPhase: Periodic Autoencoders for Learning Motion Phase Manifolds. ACM Transactions on Graphics (TOG) 41, 4, Article 136. https://doi.org/10.1145/3528223.3530178
