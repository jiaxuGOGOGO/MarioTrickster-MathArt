# SESSION-131 Code Analysis — Temporal Quality Gate Architecture

## Key Findings

### 1. motion_vector_baker.py — GT光流基础设施完备
- `compute_pixel_motion_field()` 提供精确的逐像素光流（基于FK骨骼位移+SDF蒙皮权重）
- `bake_motion_vector_sequence()` 可烘焙完整动画的MV序列
- `compute_temporal_consistency_score()` 已有基础的warp error计算（但使用mean而非min-SSIM）
- `MotionVectorField` 包含 dx, dy, magnitude, mask

### 2. neural_rendering_bridge.py — 进化桥接已有框架
- `evaluate_temporal_consistency()` 使用mean_warp_error判定pass/fail
- `compute_temporal_fitness_bonus()` 返回 [-0.3, +0.15] 的fitness修正
- 当前问题：使用 mean_warp_error 而非 min-SSIM（最差帧对），平均值掩盖单帧灾难
- 需要升级：添加 min_warp_ssim 字段，基于最差帧对判定

### 3. quality/controller.py — 质量控制器
- `post_generation()` 当前只接受单张图片，不支持序列
- 需要新增 `post_sequence_generation()` 方法接受帧序列+MV序列

### 4. visual_fitness.py — 现有SSIM计算
- `compute_frame_ssim()` 已有SSIM实现（支持skimage和fallback）
- `compute_temporal_consistency()` 使用 mean SSIM（违反min-SSIM原则）

## 实现计划

### 新增模块：`mathart/quality/temporal_quality_gate.py`
- `TemporalQualityGate` 类：三态熔断器（CLOSED/OPEN/HALF_OPEN）
- `compute_warp_ssim_sequence()` 滑动窗口逐帧对计算
- `evaluate_sequence()` 使用 min-SSIM 作为核心判据
- OOM防护：滑动窗口O(1)内存

### 升级 `neural_rendering_bridge.py`
- `TemporalConsistencyMetrics` 添加 min_warp_ssim, worst_frame_pair_index
- `evaluate_temporal_consistency()` 使用 min-SSIM 判定
- `compute_temporal_fitness_bonus()` 添加 min-SSIM 惩罚项

### 升级 `quality/controller.py`
- 新增 `post_sequence_generation()` 方法

### 测试
- 闪烁注入测试：人为替换某帧为噪点
- Min-SSIM熔断断言：验证最差帧触发熔断
- OOM防护测试：验证滑动窗口内存行为
- 进化惩罚断言：验证fitness惩罚正确传递
