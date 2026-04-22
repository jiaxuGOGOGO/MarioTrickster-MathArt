# SESSION-131 Industrial References — Temporal Quality Gate

## 1. Warping Error for Video Temporal Consistency (ECCV 2018, Lai et al.)

**核心公式**：Temporal Warping Error = ||W(F_t→t+1, O_t) - O_{t+1}||² × M_{t→t+1}

其中：
- W(F, I) = 使用光流场 F 将帧 I 变形（warp）到下一帧位置
- F_t→t+1 = 从帧 t 到帧 t+1 的光流场（forward optical flow）
- O_t, O_{t+1} = 连续输出帧
- M_t→t+1 = 遮挡掩码（occlusion mask），排除被遮挡区域的误差

**关键洞察**：
- 使用 Ground-Truth 光流（而非估计光流）可消除光流估计误差的干扰
- 遮挡感知是必须的：被遮挡像素不应参与误差计算
- 逐帧对计算（sliding window），不做全序列平均
- 最差帧对（Min-SSIM / Max Warp Error）才是真正的质量判据

**在本项目中的应用**：
- 我们拥有 `motion_vector_baker.py` 提供的数学精确光流（Ground-Truth Motion Vectors）
- 使用这些 GT 光流对 AI 渲染输出执行 warp，计算 warp error
- 使用 Min-SSIM 而非 Mean-SSIM 作为熔断判据

## 2. Circuit Breaker Pattern (Martin Fowler, 2014)

**三态状态机**：
- **Closed**（正常）：所有调用正常通过，失败计数器递增
- **Open**（熔断）：所有调用立即返回错误，不执行受保护操作
- **Half-Open**（试探恢复）：允许一次试探调用，成功则重置为 Closed，失败则回到 Open

**关键设计原则**：
- failure_threshold：连续失败次数阈值触发熔断
- reset_timeout：熔断后等待一段时间再试探恢复
- 监控与告警：状态变化必须被记录和监控
- 不同错误类型可以有不同阈值

**在本项目中的应用**：
- 将 AI 渲染质量检查包装为 Circuit Breaker
- Closed 状态：正常渲染并检查 Min-SSIM
- Open 状态：连续 N 批次 Min-SSIM 不达标 → 熔断，拒绝继续渲染
- Half-Open 状态：进化引擎调整参数后试探性重渲染一批
- 失败计数器基于 batch 级别，不是帧级别

## 3. Fitness Landscapes & Penalty Functions in Evolutionary Algorithms

**核心概念**：
- Fitness Landscape：搜索空间中每个候选解的适应度值构成的"地形"
- Penalty Function：对违反约束的候选解施加惩罚，降低其适应度
- 公式：F_penalized(x) = F_original(x) - λ × violation(x)

**在本项目中的应用**：
- 进化引擎的 Fitness 函数必须包含时序一致性惩罚项
- penalty = λ_temporal × max(0, ssim_threshold - min_warp_ssim)
- λ_temporal 权重应足够大，使时序崩坏的解永远不会被选中
- 这形成了"三层进化循环"的负反馈闭环：
  1. 外循环：进化引擎生成 ControlNet/SparseCtrl 参数
  2. 中循环：AI 渲染执行
  3. 内循环：时序质量熔断器评估 → 惩罚反馈给外循环

## 4. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 光流来源 | motion_vector_baker.py GT 光流 | 零外部AI模型依赖，数学精确 |
| 质量判据 | Min-SSIM（最差帧对） | 防止平均值掩盖单帧灾难 |
| 内存策略 | 滑动窗口逐帧对 | 防 OOM，O(1) 内存 |
| 熔断粒度 | batch 级别 | 与工厂 PDG 节点对齐 |
| 惩罚函数 | 线性惩罚 + 硬阈值 | 简单有效，可调参 |
