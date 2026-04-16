# Phase-Driven Animation Knowledge Base
> SESSION-033: 蒸馏自 PFNN、DeepPhase、Animator's Survival Kit 的相位驱动动画知识

## 来源

| 来源 | 作者 | 年份 | 类型 |
|------|------|------|------|
| Phase-Functioned Neural Networks for Character Control (PFNN) | Daniel Holden et al. | SIGGRAPH 2017 | 论文 |
| DeepPhase: Periodic Autoencoders for Learning Motion Phase Manifolds | Sebastian Starke et al. | SIGGRAPH 2022 | 论文 |
| The Animator's Survival Kit (Expanded Edition) | Richard Williams | 2009 | 书籍 |

## 核心概念：Phase Variable（相位变量）

相位变量 p 是动画控制的第一公民（first-class citizen），取代传统的绝对时间 t。

| 参数 | 值 | 说明 |
|------|-----|------|
| 相位范围 | [0, 1) 或 [0, 2π) | 循环变量，单调递增 |
| 左脚接触相位 | p = 0.0 (或 0) | PFNN 定义 |
| 右脚接触相位 | p = 0.5 (或 π) | PFNN 定义 |
| 插值方法 | Catmull-Rom 样条 | PFNN 使用的平滑插值 |
| 相位推进 | Δp = dt × speed × steps_per_sec / 2 | 速度调制的线性推进 |

## Walk Cycle 四关键帧（Animator's Survival Kit p.107-111）

| 关键帧 | 半周期相位 | 骨盆高度 | 特征 |
|--------|-----------|---------|------|
| Contact | 0.000 | 中等(neutral) | 脚最远分开，重量均匀分布 |
| Down | 0.125 | 最低(lowest) | 前脚承重，膝盖弯曲吸收力量 |
| Passing | 0.250 | 最高(highest) | 一腿经过另一腿，重心转移 |
| Up | 0.375 | 高点(high) | 后腿推离地面，向前推进 |

### Walk Cycle 关键规则
- "It's the DOWN position where the legs are bent and the body mass is down - where we feel the weight"
- "In a normal 'realistic' walk, the weight goes DOWN just after the step - just after the contact"
- "arm swing is at its widest on the DOWN" (不是 Contact！)
- 手臂始终与对侧腿相反（counter-rotation）

### Walk Timing 表（帧数/步）

| 帧数/步 | 速度 | 描述 |
|---------|------|------|
| 4 | 6 步/秒 | 极快跑 |
| 6 | 4 步/秒 | 跑或极快走 |
| 8 | 3 步/秒 | 慢跑或卡通走 |
| 12 | 2 步/秒 | 标准自然走路 |
| 16 | 2/3 秒/步 | 闲逛 |
| 20 | ~1 秒/步 | 老年人或疲惫 |

## Run Cycle 关键参数（Animator's Survival Kit p.176-182）

| 参数 | Walk 值 | Run 值 | 说明 |
|------|---------|--------|------|
| 前倾角度 | 0.06 rad | 0.12+ rad | 越快越倾 |
| 骨盆 UP 抬高 | ~0.020 | ~0.020 | 仅 1/2-1/3 头高度 |
| 骨盆 DOWN 下沉 | -0.025 | -0.035 | 冲击力更大 |
| Flight phase | 无 | 有 | 双脚离地 |
| 手臂弯曲 | 0.15 rad | 0.55+ rad | 跑步手臂更弯 |

### Run Cycle 关键规则
- "Rule of thumb on a run - when we raise the body in the UP position, raise it only 1/2 head or even 1/3 of a head. Never a whole head."
- "The FASTER the figure runs, the more it LEANS FORWARD"
- "Runs HAVE to be on ONES because of so much action in a short space of time"
- Shoulders oppose hips（肩膀与臀部反向）

## DeepPhase 多通道相位分解

每个运动维度可用正弦函数近似：Γ(p) = A·sin(2π(F·p - S)) + B

| 通道 | Walk A | Walk F | Run A | Run F | 说明 |
|------|--------|--------|-------|-------|------|
| torso_bob | 0.015 | 2.0 | 0.025 | 2.0 | 躯干上下弹跳（每步一次） |
| torso_twist | 0.04 | 1.0 | 0.06 | 1.0 | 躯干扭转（与腿反向） |
| head_stabilize | 0.008 | 2.0 | 0.012 | 2.0 | 头部补偿（抵消弹跳） |
| lateral_sway | 0.02 | 1.0 | 0.015 | 1.0 | 侧向摇摆 |
| arm_pump | - | - | 0.10 | 2.0 | 手臂泵动（仅跑步） |

### FFT 参数提取方法（DeepPhase/PAE）
1. B = c₀/N（零频率分量 = 信号均值）
2. F = Σ(fⱼ·pⱼ) / Σ(pⱼ)（功率加权平均频率）
3. A = √(2/N · Σpⱼ)（保持平均功率）
4. S = arctan2(sᵧ, sₓ)（通过 2D 相位表示避免不连续）

## 步态周期生物力学数据

| 阶段 | 周期百分比 | 说明 |
|------|-----------|------|
| Stance Phase | 60% | 脚在地面 |
| Swing Phase | 40% | 脚离地 |
| Double Support | 20% | 两脚同时着地 |
| Initial Contact | 0% | 脚跟触地 |
| Loading Response | 0-10% | 承重响应 |
| Midstance | 10-30% | 中间站立 |
| Terminal Stance | 30-50% | 终末站立 |
| Pre-swing | 50-60% | 预摆动 |
| Swing | 60-100% | 摆动期 |

## 代码映射

| 知识点 | 代码位置 | 实现方式 |
|--------|----------|----------|
| Phase Variable | `phase_driven.PhaseVariable` | 循环相位追踪器 |
| 四关键帧插值 | `phase_driven.PhaseInterpolator` | Catmull-Rom 样条 |
| 多通道叠加 | `phase_driven.PhaseChannel` | 正弦函数叠加 |
| Walk 关键帧 | `phase_driven.WALK_KEY_POSES` | Contact/Down/Pass/Up |
| Run 关键帧 | `phase_driven.RUN_KEY_POSES` | 含 Flight phase |
| FFT 提取 | `phase_driven.extract_phase_parameters` | numpy.fft |
| 预设替换 | `presets.run_animation` → `phase_driven_run` | 委托调用 |
| RL 参考动作 | `rl_locomotion._generate_walk/run_cycle` | 委托调用 |
