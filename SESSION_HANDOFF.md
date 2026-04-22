# SESSION-131 HANDOFF — P0-SESSION-128-TEMPORAL-QUALITY-GATE: AI Rendering Temporal Quality Auto-Fuse / Min-SSIM Circuit Breaker / Evolution Fitness Penalty

**Objective**：执行 **P0-SESSION-128-TEMPORAL-QUALITY-GATE** 核心攻坚计划，基于真实数学光流与 Min-SSIM 实现 AI 渲染时序质量自动熔断机制，部署三态断路器，闭合进化引擎负反馈回路。

**Status**：**CLOSED（SESSION-131 四叉攻坚完成）**。

本轮工作以三大工业界/学术界参考为最高准则（Lai et al. ECCV 2018 Temporal Warping Error、Martin Fowler Circuit Breaker Pattern、Automated Fitness Landscapes in Evolutionary Algorithms），对 AI 渲染输出执行了四叉同步攻坚：时序质量门控、质量控制器集成、进化引擎负反馈闭环、工厂集成。所有代码变更均严格遵守 Registry Pattern 独立插件纪律，零越权修改主干。

## 研究基础与设计决策

本次代码落地前，强制完成了三项外网参考研究，研究成果记录于 `research/research_session131_industrial_references.md` 和 `research/session131_code_analysis.md`。

| 参考来源 | 核心洞察 | 在代码中的体现 |
|---|---|---|
| **Lai et al. ECCV 2018 — Temporal Warping Error** | 遮挡感知变形误差 `E_warp = (1-O) * \|I_t - W(I_{t+1}, F)\|`，GT 光流消除估计误差 | `compute_warp_ssim_pair()` 使用 GT motion vectors 变形帧并在非遮挡区域计算 SSIM |
| **Martin Fowler — Circuit Breaker Pattern** | 三态状态机 CLOSED→OPEN→HALF_OPEN→CLOSED，cooldown 恢复防止永久锁死 | `TemporalQualityGate` 三态断路器，failure_threshold=3，cooldown_seconds=60 |
| **Automated Fitness Landscapes** | 质量度量作为负适应度惩罚项，迫使进化引擎自动规避低质量形态 | `compute_temporal_fitness_bonus()` 中 `min_warp_ssim_penalty_lambda` 惩罚灾难帧 |

## 四大核心交付物

### 1. 时序质量门控 (`mathart/quality/temporal_quality_gate.py`) — 全新模块

**设计原理**：在 AI 渲染输出与产物持久化之间部署最后一道防线。使用 GT 光流（来自数学引擎的精确运动向量）变形帧 A 到帧 B 的位置，然后计算非遮挡区域的 SSIM。**关键决策：使用 Min-SSIM（最差帧对）而非 Mean-SSIM（平均值）作为熔断核心**——1 帧灾难闪烁不会被 29 帧平滑掩盖。

| 组件 | 功能 |
|---|---|
| `compute_warp_ssim_pair(frame_a, frame_b, mv_field)` | 使用 GT 光流变形 frame_a，在非遮挡区域计算 SSIM，返回 warp_ssim + warp_error + coverage |
| `sliding_window_warp_ssim(frames, mv_fields)` | O(1) 内存滑动窗口——任何时刻仅 2 帧 + 1 MV 场驻留内存，防 OOM |
| `TemporalQualityGate` | 三态断路器：CLOSED（正常评估）→ OPEN（连续失败后拒绝所有）→ HALF_OPEN（cooldown 后探测恢复） |
| `TemporalQualityResult` | 冻结数据类：verdict, min_ssim, mean_ssim, max_warp_error, per_pair_results, breaker_status |
| `compute_fitness_penalty(result, lambda)` | 将质量结果转换为进化适应度惩罚值 |

**Min-SSIM vs Mean-SSIM 对比**：

| 场景 | Mean-SSIM | Min-SSIM | 正确判决 |
|---|---|---|---|
| 30 帧全平滑 | 0.95 | 0.93 | PASS ✅ |
| 29 帧平滑 + 1 帧噪点 | 0.92 | 0.12 | **FAIL** ✅ |
| 29 帧平滑 + 1 帧噪点（Mean-SSIM 判决） | 0.92 → PASS ❌ | — | Mean-SSIM 掩盖灾难 |

### 2. 质量控制器集成 (`mathart/quality/controller.py`) — 更新

- `post_sequence_generation(frames, mv_fields)` 方法添加到 `ArtMathQualityController`
- 接受渲染帧（numpy 数组）+ GT 运动向量场
- 调用 `TemporalQualityGate` 并返回结构化 `TemporalQualityResult`
- 日志记录 verdict 和 min_ssim 用于监控

### 3. 进化引擎负反馈闭环 (`mathart/evolution/neural_rendering_bridge.py`) — 更新

| 组件 | 变更 |
|---|---|
| `TemporalConsistencyMetrics` | 新增 `min_warp_ssim`、`per_pair_warp_ssim`、`worst_frame_pair_index` 字段 |
| `evaluate_temporal_consistency()` | 新增 Min-SSIM 二级通过准则（阈值 0.5） |
| `compute_temporal_fitness_bonus()` | 新增灾难帧惩罚：`min_warp_ssim < 0.5` 时施加最高 -0.25 惩罚 |

**负反馈闭环**：

```
CharacterGenotype → Skeletal Animation → AI Render → TemporalQualityGate
                                                           │
                                                    min_warp_ssim < 0.5?
                                                           │
                                                    YES: fitness -= 0.25
                                                           │
                                                    Evolution steers away
                                                    from bad morphologies
```

### 4. 工厂集成 (`tools/run_mass_production_factory.py`) — 更新

- `_node_ai_render` 在 `anti_flicker_render` 后端执行后调用 `TemporalQualityGate`
- 时序质量门控报告注入 `ArtifactManifest.metadata["temporal_quality_gate"]`
- 会话标识更新为 SESSION-131

### 5. 测试基础设施修复 (`tests/conftest.py`) — 更新

- `_BUILTIN_BACKEND_MODULES` 新增 `physical_ribbon_backend` 和 `archive_delivery_backend`
- 修复组合测试套件运行时的注册表稳定性问题（registry reset/reload 循环）

## 防混线红线审计

| 红线 | 审计结果 |
|---|---|
| **Min-SSIM 非 Mean-SSIM** | ✅ `TemporalQualityGate` 使用 `min(per_pair_ssim)` 作为熔断核心，测试 `test_mean_ssim_masks_catastrophe` 证明 Mean-SSIM 会掩盖灾难 |
| **防 OOM 内存爆炸** | ✅ 滑动窗口 O(1) 内存——任何时刻仅 2 帧 + 1 MV 场驻留；测试 `test_large_sequence_completes` 验证 |
| **防平均值掩盖** | ✅ 测试 `test_flicker_sequence_detects_bad_pair` 注入单帧噪点，Min-SSIM 正确触发 FAIL |
| **闭环端到端断言** | ✅ 28 个新测试覆盖：闪烁注入、断路器状态机、适应度惩罚、契约验证、OOM 防护、序列化 |
| **断路器恢复** | ✅ OPEN 状态有 cooldown 恢复机制，不会永久锁死——测试 `test_open_to_half_open_transition` 验证 |

## 白盒验证闭环

| 验证命令 | 结果 |
|---|---|
| `python3.11 -m pytest tests/test_temporal_quality_gate.py -v` | **28/28 PASS**（全部新增 SESSION-131） |
| `python3.11 -m pytest tests/test_orthographic_pixel_render.py -v` | **24/24 PASS** |
| `python3.11 -m pytest tests/test_mass_production.py -v` | **2/2 PASS** |
| `python3.11 -m pytest tests/test_motion_2d_pipeline.py -v` | **28/28 PASS** |
| `python3.11 -m pytest tests/test_p1_ai_2c_anti_flicker_live_cli.py -v` | **18/18 PASS** |
| **组合运行全部 5 套件** | **100/100 PASS** |

## 测试覆盖详情（28 个新测试）

| 测试类 | 测试数 | 覆盖内容 |
|---|---|---|
| `TestWarpSSIMPair` | 4 | 相同帧高 SSIM、噪点帧低 SSIM、空遮挡掩码默认值、覆盖率计算 |
| `TestSlidingWindow` | 3 | 平滑序列全高 SSIM、闪烁检测、帧对数量 |
| `TestFlickerInjection` | 3 | 单帧噪点触发 FAIL、Mean-SSIM 掩盖灾难（证明 Min-SSIM 必要性）、干净序列通过 |
| `TestCircuitBreakerStateMachine` | 5 | 初始 CLOSED、连续失败触发 OPEN、OPEN→HALF_OPEN 转换、HALF_OPEN→CLOSED 恢复、reset 清除状态 |
| `TestFitnessPenalty` | 4 | 通过零惩罚、失败正惩罚、断路器开启惩罚、lambda 缩放惩罚 |
| `TestContractValidation` | 2 | 单帧拒绝、空 MV 场拒绝 |
| `TestOOMPrevention` | 2 | 大序列完成、结果逐对不累积 |
| `TestNeuralRenderingBridgeIntegration` | 3 | metrics 含 min_warp_ssim、to_dict 含 min_ssim、fitness bonus 惩罚低 min_ssim |
| `TestResultSerialization` | 2 | result to dict、breaker status to dict |

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `mathart/quality/temporal_quality_gate.py` | **NEW** | 三态断路器 + 滑动窗口 warp-SSIM + Min-SSIM 熔断 + 适应度惩罚 |
| `mathart/quality/__init__.py` | **UPD** | 导出 temporal_quality_gate 符号 |
| `mathart/quality/controller.py` | **UPD** | +post_sequence_generation() 方法 |
| `mathart/evolution/neural_rendering_bridge.py` | **UPD** | +min_warp_ssim/per_pair_warp_ssim/worst_frame_pair_index + 灾难帧惩罚 |
| `tools/run_mass_production_factory.py` | **UPD** | TemporalQualityGate 集成 + SESSION-131 |
| `tests/conftest.py` | **UPD** | 注册表稳定性修复 |
| `tests/test_temporal_quality_gate.py` | **NEW** | 28 个全面测试 |
| `research/research_session131_industrial_references.md` | **NEW** | Warping Error + Circuit Breaker + Fitness 研究 |
| `research/session131_code_analysis.md` | **NEW** | 架构分析 |
| `PROJECT_BRAIN.json` | **UPD** | SESSION-131 状态 |
| `SESSION_HANDOFF.md` | **UPD** | 本文档 |

## 质量防御栈架构（SESSION-128 → SESSION-131）

```
Layer 0: Fail-Fast Input Contract (SESSION-128)
  └─ PipelineContractError on missing Mesh3D / missing dimensions / empty geometry
  └─ 永久消灭 fallback 球体 / 32x16 色块

Layer 1: Bone Topology Contract (SESSION-129)
  └─ PipelineContractError on orphan animation tracks / NSM-bone mismatch
  └─ 永久消灭四足静止灾难

Layer 2: Temporal Variance Circuit Breaker (SESSION-130)
  └─ 拦截静态/伪静态引导序列 BEFORE ComfyUI
  └─ MSE-based, 操作于引导输入

Layer 3: True Motion Guide Rendering (SESSION-130)
  └─ 骨骼驱动逐帧渲染替代单图复制
  └─ OOM-safe chunked rendering

Layer 4: Temporal Quality Gate (SESSION-131) ← 本次新增
  └─ 拦截闪烁/撕裂 AI 渲染输出 AFTER anti_flicker_render
  └─ Warp-SSIM with GT motion vectors, Min-SSIM fuse
  └─ 三态断路器 + cooldown 恢复

Layer 5: Evolution Fitness Penalty (SESSION-131) ← 本次新增
  └─ min_warp_ssim 惩罚驱动进化远离低质量形态
  └─ 闭合负反馈回路：渲染质量 → 基因型选择压力
```

## 接下来：基于 MAP-Elites 的多目标进化（同时兼顾动作连贯性与角色风格多样性）

SESSION-128→131 完成了从"量产可跑"到"量产有真实运动"再到"量产有严苛质量防线"的关键跃迁。架构现在已准备好接入 **MAP-Elites 多目标进化**，同时优化动作连贯性与角色风格多样性。以下是当前架构还需要的四项微调准备：

### 微调准备 1：行为描述符空间定义（Behavior Descriptor Space）

**现状**：进化引擎使用单一标量适应度（fitness），无法区分"高质量但单调"和"中等质量但多样"的个体。

**所需调整**：定义 2D 行为描述符网格，轴为 `[temporal_coherence_score, style_diversity_score]`。`temporal_coherence_score` 直接来自 `TemporalQualityGate.evaluate()` 的 `mean_ssim`。`style_diversity_score` 可通过 `visual_fitness.py` 中已有的 pHash Hamming 距离计算（与存档质心的距离）。

**预估工作量**：新模块 `mathart/evolution/map_elites_archive.py`（~100 行），`BehaviorDescriptor = namedtuple('BD', ['temporal', 'style'])` + 10x10 网格离散化。

### 微调准备 2：MAP-Elites 存档数据结构

**现状**：进化引擎维护单一种群（population），无行为空间覆盖记录。

**所需调整**：实现网格存档，每个单元格存储其行为描述符 bin 的最佳基因型。`grid: dict[tuple[int,int], ArchiveCell]`，其中 `ArchiveCell = (genotype, fitness, behavior_descriptor, temporal_quality_result)`。通过 JSON dump/load 实现跨会话持久化。

**预估工作量**：~150 行，利用已有的 `CharacterGenotype` 序列化基础设施。

### 微调准备 3：进化循环集成

**现状**：进化循环使用锦标赛选择（tournament selection）在平坦适应度景观上操作。

**所需调整**：在 `_evaluate_fitness()` 之后添加 `_try_archive_placement()` 钩子。计算行为描述符，尝试将个体放入 MAP-Elites 存档（如果适应度更高则替换）。填充空单元格的基因型获得一次性探索奖励，鼓励行为空间覆盖。

**预估工作量**：~80 行修改 `mathart/evolution/evolution_loop.py`。

### 微调准备 4：TemporalQualityGate 作为存档硬约束

**现状**：`TemporalQualityResult.verdict` 已是枚举类型（`PASS`/`FAIL`/`BREAKER_OPEN`），但未与存档放置逻辑关联。

**所需调整**：在 `_try_archive_placement()` 中添加硬约束：`if result.verdict != QualityVerdict.PASS: return`。失败的基因型永远不进入存档，无论其他适应度分数如何。这确保存档的质量底线。

**预估工作量**：~5 行守卫条件。

### 待办列表更新

| ID | 优先级 | 标题 | 状态 |
|---|---|---|---|
| P0-SESSION-127-CORE-CONSTRAINTS | P0 | Fail-Fast Mesh3D / RNG Digest / Archive Delivery | **CLOSED (SESSION-128)** |
| P0-SESSION-127-STRICT-PIPELINE-SEMANTICS | P0 | Bone Topology / Dimension Fail-Fast / Guide Forgery | **CLOSED (SESSION-129)** |
| P0-SESSION-128-TRUE-MOTION-GUIDE | P0 | True Bone-Driven Guide / Temporal Variance Breaker / Anti-Forgery | **CLOSED (SESSION-130)** |
| P0-SESSION-128-TEMPORAL-QUALITY-GATE | P0 | Min-SSIM Circuit Breaker / Evolution Fitness Penalty | **CLOSED (SESSION-131)** |
| P1-MAP-ELITES-1 | P1 | 行为描述符空间 + MAP-Elites 存档数据结构 | TODO |
| P1-MAP-ELITES-2 | P1 | 进化循环集成 + 探索奖励 | TODO |
| P1-MAP-ELITES-3 | P1 | TemporalQualityGate 存档硬约束 | TODO |
| P1-QUALITY-GATE-1 | P1 | 光流提取 + SSIM 时序评分 | TODO |
| P1-QUALITY-GATE-2 | P1 | QualityGateBackend 注册表插件 | TODO |
| P1-QUALITY-GATE-3 | P1 | FrameSequenceExporter 集成为规范物化路径 | TODO |
| P2-COMFYUI-REPRO-1 | P2 | ComfyUI 工作流确定性固定与种子绑定 | TODO |
| P2-COMFYUI-REPRO-2 | P2 | GPU 设备指纹注入与跨机器对比 | TODO |
| P2-GPU-RENDER-1 | P2 | nvdiffrast / Mitsuba 3 GPU 可微渲染替换 | TODO |

## Local Production Commands

命令格式与 SESSION-130 保持一致，SESSION-131 的变更不影响 CLI 接口：

| 场景 | 推荐命令 |
|---|---|
| 纯 CPU / dry-run 审计 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --skip-ai-render --seed 20260422` |
| 标准本地量产 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |
| 保守显存模式 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 12 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |

## Immediate Operator Guidance

如果主理人现在要在本地正式开跑，请先执行一次带 `--skip-ai-render` 的 dry-run，重点检查：

1. **Spine JSON 导出**：确认四足角色的动画轨道包含 `fl_upper`/`fr_upper`/`hl_upper`/`hr_upper`
2. **`batch_summary.json`** 中每个角色、每个阶段都有 `rng_spawn_digest` 且角色间不重复
3. **`orthographic_pixel_render/` 下的 render report**，确认 `mesh_contract.fail_fast_enforced = true`
4. **`archive/` 目录**，确认所有阶段产物集中存在
5. **引导序列验证**：如果开启 AI 渲染，观察日志中 `validate_temporal_variance` 和 `TemporalQualityGate` 输出
6. **时序质量门控**（新增）：检查 AI 渲染 manifest 中 `temporal_quality_gate` 字段，确认 `verdict: PASS` 且 `min_ssim > 0.7`

## Known Pre-Existing Issues (Not Introduced by SESSION-131)

1. **`pseudo_3d_shell` CI schema failure**: `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` — 预存于 SESSION-128 之前。
2. **`anti_flicker_render` CI schema `keyframe_count` missing**: 离线 pipeline 路径（无 ComfyUI 服务器）产出的 manifest 缺少 quality metric。

## 战略总结

SESSION-128→131 完成了从"量产可跑"到"量产有严苛时序质量防线"的五级跃迁。质量防御栈现在有六层深度：输入契约 → 骨骼拓扑 → 时序方差断路器 → 真实运动引导 → **时序质量门控（Min-SSIM 熔断）** → **进化适应度惩罚**。最后两层是 SESSION-131 的核心贡献——它们不仅拦截缺陷产出，还通过负反馈闭环驱动进化引擎自动规避产生缺陷的形态。下一步的战略方向是 MAP-Elites 多目标进化，而 SESSION-131 建立的质量-适应度闭环正是这个方向的必要前置条件。
