# SESSION-160 交接文档 (SESSION_HANDOFF.md)

> **"动态注册，上下文穿透，防静止核弹。" —— SESSION-160 执行了三大核心重构：铲除硬编码动作列表建立动态 ActionRegistry、修复时序参数断链实现 RenderContext 全链路穿透、部署逐帧对 MSE 地板断言防止冻结动画泄漏到 AI 渲染。**
>
> **"注册表驱动，零修改扩展。" —— 新动作类型只需在 MotionStateLaneRegistry 注册新 Lane，工厂代码零修改自动发现。**

**Date**: 2026-04-23
**Parent Commit**: e3a05c8 (SESSION-158)
**Task ID**: P0-SESSION-160-ACTION-REGISTRY-TEMPORAL-WIRING-VARIANCE-GATE
**Status**: CLOSED
**Smoke**: `python -m pytest tests/test_mass_production.py -v` → PASS
**Regression**: 核心管线测试全部通过，零退化

---

## 1. Executive Summary

SESSION-160 聚焦三大 P0 核心重构任务，全部基于外网参考研究落地：

1. **动态动作注册表 (ActionRegistry)**：铲除 `_MOTION_STATES` 硬编码列表，替换为从 `MotionStateLaneRegistry` 动态获取。参考 Unreal Engine Animation Blueprint State Machine 的动态状态注册模式。
2. **时序参数断链修复 (Temporal Context Wiring)**：`motion_state`、`fps`、`character_id` 作为显式 RenderContext 参数无损穿透到 `_bake_true_motion_guide_sequence()`。参考 DigitalRune RenderContext 的显式上下文传递模式。
3. **防静止自爆核弹 (Variance Assert Gate)**：在 AI 渲染边界部署 `assert_nonzero_temporal_variance()` 逐帧对 MSE 地板断言。参考 MSE 帧差分运动检测的工业实践。

---

## 2. 老大，三大重构已完成！

### 2.1 动态动作注册表 — 硬编码彻底铲除

**问题根源**：工厂文件中存在硬编码的 `_MOTION_STATES = ["idle", "walk", "run", "jump", "fall", "hit"]`。每次新增动作类型（如 Dash、Climb、AttackCombo）都需要手动修改这个列表，违反 Open-Closed Principle。

**手术过程**：

1. 删除 `_MOTION_STATES` 硬编码列表
2. 新增 `_get_registered_motion_states()` 函数，从 `MotionStateLaneRegistry` 动态获取
3. 所有引用点（`_state_from_rng()`、`_node_seed_orders()`）全部切换到动态查询
4. 新增 `from mathart.animation.unified_gait_blender import get_motion_lane_registry` 导入

### 2.2 时序参数断链修复 — RenderContext 全链路穿透

**问题根源**：`_bake_true_motion_guide_sequence()` 只接收 `genotype_path`、`clip_2d`、`frame_count`、`render_width`、`render_height`，缺少 `motion_state`、`fps`、`character_id`。上游的动作意图无法穿透到烘焙函数，导致烘焙报告中缺少关键上下文信息。

**手术过程**：

1. 为 `_bake_true_motion_guide_sequence()` 新增 `motion_state`、`fps`、`character_id` 关键字参数
2. 在 `_node_guide_baking()` 调用处传入完整 RenderContext
3. 烘焙报告 JSON 中新增 `motion_state` 和 `fps` 字段
4. `_node_guide_baking()` 返回值中新增 `motion_state` 和 `fps` 字段供下游消费
5. 新增 RenderContext 诊断日志

### 2.3 防静止自爆核弹 — 逐帧对 MSE 地板断言

**问题根源**：SESSION-158 的 Temporal Variance Circuit Breaker 是比率式的（50% 帧对 MSE > 1.0），允许部分帧对完全静止。这在极端情况下可能让冻结动画泄漏到 AI 渲染。

**手术过程**：

1. 在 `mathart/core/anti_flicker_runtime.py` 新增 `assert_nonzero_temporal_variance()` 函数
2. MSE 地板值设为 0.0001（0-255 尺度），任何单帧对低于此值触发 `RuntimeError`
3. 在 `_node_ai_render()` 中部署，位于 SESSION-158 比率式断路器之后
4. 新增到 `__all__` 导出列表

---

## 3. 核心落地清单

| 文件 | 改动类型 | 要点 |
|------|---------|------|
| `tools/run_mass_production_factory.py` | **重大重构** | 铲除 `_MOTION_STATES` 硬编码, 新增 `_get_registered_motion_states()`, RenderContext 穿透, Variance Assert Gate 部署, SESSION_ID 更新为 SESSION-160 |
| `mathart/core/anti_flicker_runtime.py` | **新增函数** | `assert_nonzero_temporal_variance()` 逐帧对 MSE 地板断言 |
| `tests/test_mass_production.py` | **更新** | SESSION-160 断言：动态注册表验证、RenderContext 穿透验证、Variance Assert Gate 验证 |
| `docs/USER_GUIDE.md` | **新增章节** | 第 8.1 章：动态动作注册表 + 时序上下文穿透 + 防静止断言 |
| `PROJECT_BRAIN.json` | **全面更新** | SESSION-160; P0-ACTION-REGISTRY-TEMPORAL-WIRING-VARIANCE-GATE=CLOSED |
| `SESSION_HANDOFF.md` | **全面重写** | 本文件 |

---

## 4. 外网参考研究对标

| 设计原则 | 参考来源 | 在本项目中的应用 |
|---------|---------|----------------|
| **Data-Driven State Machine** | UE5 Animation Blueprint State Machine (Epic Games) | 动作类型通过 Registry 动态注册，不硬编码在 if/else 树中 |
| **RenderContext Hydration** | DigitalRune RenderContext; 游戏引擎渲染管线上下文传递 | motion_state/fps/character_id 作为显式参数穿透到每个渲染调用点 |
| **MSE Frame Differencing** | 视频监控运动检测; PSNR/MSE 视频质量评估 | 逐帧对 MSE 地板断言防止冻结动画泄漏 |
| **IoC / Registry Pattern** | Spring IoC Container; UE5 Subsystem Registry | MotionStateLaneRegistry 作为动作类型的权威数据源 |

---

## 5. 红线执行证据

| 红线 | 状态 |
|------|------|
| 硬编码动作列表必须铲除 | PASS — `_MOTION_STATES` 已删除，替换为 `_get_registered_motion_states()` |
| 时序参数必须全链路穿透 | PASS — motion_state/fps/character_id 穿透到烘焙函数和报告 |
| 逐帧对 MSE 地板断言必须部署 | PASS — `assert_nonzero_temporal_variance()` 已部署在 AI 渲染边界 |
| 外网参考研究必须完成才能落地 | PASS — UE5 State Machine / DigitalRune RenderContext / MSE 帧差分 |
| DaC 文档同步 | PASS — USER_GUIDE.md 新增 8.1 章, PROJECT_BRAIN.json 更新 |

---

## 6. 傻瓜验收指引

### 验收 1：动态注册表

```python
from mathart.animation.unified_gait_blender import get_motion_lane_registry
print(get_motion_lane_registry().names())
# 预期: ('fall', 'hit', 'idle', 'jump', 'run', 'walk')
```

### 验收 2：时序上下文穿透

```bash
python -m mathart mass-produce --output-dir ./test_output --batch-size 1 --skip-ai-render --seed 42
cat test_output/mass_production_batch_*/character_000/guide_baking/*_guide_baking_report.json | python3 -m json.tool | grep -E 'motion_state|fps'
# 预期: 报告中包含 motion_state 和 fps 字段
```

### 验收 3：自动化测试

```bash
python -m pytest tests/test_mass_production.py -v
# 预期: 全部 PASS
```

---

## 7. 下一步建议

| 优先级 | 任务 ID | 说明 |
|--------|---------|------|
| **P1** | P1-SESSION-160-REGISTRY-EXPANSION | 注册新动作类型（Dash、Climb、AttackCombo）验证零修改扩展 |
| **P1** | P1-SESSION-158-GUIDE-QUALITY-VISUAL-REGRESSION | 对烘焙引导图序列做 pHash/SSIM 视觉回归测试 |
| **P2** | P2-SESSION-160-RENDERCONTEXT-DATACLASS | 将 RenderContext 参数提升为强类型 dataclass |

---

## 8. 文件变更总览

```
tools/run_mass_production_factory.py  — 铲除硬编码, ActionRegistry, RenderContext穿透, Variance Assert Gate
mathart/core/anti_flicker_runtime.py  — +assert_nonzero_temporal_variance()
tests/test_mass_production.py         — SESSION-160 断言更新
docs/USER_GUIDE.md                    — 新增 8.1 章 SESSION-160 重构
PROJECT_BRAIN.json                    — SESSION-160 更新
SESSION_HANDOFF.md                    — 本文件（重写）
```

> **上一个会话**: SESSION-158 (P0-SESSION-158-PIPELINE-DECOUPLING)
> **基线 commit**: e3a05c8 (SESSION-158)
> **本次 commit**: SESSION-160 ActionRegistry + Temporal Wiring + Variance Assert Gate
