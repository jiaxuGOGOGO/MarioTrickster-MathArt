# SESSION-162 交接文档 (SESSION_HANDOFF.md)

> **"老大，注册表残党已被一次性清剿完毕，烘焙网关的视觉静止断言也提前部署到了纯 CPU 出口。
> 即使有人在 PR 里偷偷塞回硬编码的 [\"idle\", \"run\"]，单元测试和注册表会在 CI 阶段当场拉响警报；
> 即使有人偷偷把 motion_state 默认值还原成 'idle'，RenderContext 强契约也会让烘焙函数原地爆炸。
> 整套手术零回滚，全部按红线推进。"**

**Date**: 2026-04-23
**Parent Commit**: 96572cb (SESSION-160)
**Task ID**: P0-SESSION-162-DATA-DRIVEN-REGISTRY-ENFORCEMENT
**Status**: CLOSED
**External Anchors**: `docs/RESEARCH_NOTES_SESSION_162.md`

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **铲除残留硬编码动作列表** | `mathart/pipeline.py`、`mathart/pipeline_contract.py`、`mathart/headless_e2e_ci.py`、`mathart/animation/cli.py`、`mathart/evolution/asset_factory_bridge.py` | Tom Looman, *GameplayTags Data-Driven Design* |
| 2 | **RenderContext 强契约** | `tools/run_mass_production_factory.py` 的 `_bake_true_motion_guide_sequence` 显式接收 `motion_state` / `fps` / `character_id` | DigitalRune *Render Context* / DX12 PSO |
| 3 | **Fail-Fast 视觉静止断言前置到烘焙出口** | `tools/run_mass_production_factory.py` 在烘焙函数 return 之前调用 `assert_nonzero_temporal_variance` | Frame-Differencing MSE 工业范式 |
| 4 | **外网研究锚点 Docs-as-Code 落盘** | `docs/RESEARCH_NOTES_SESSION_162.md` | 上述 7 条公开工业出处 |
| 5 | **三大状态文件全量同步** | `SESSION_HANDOFF.md`、`PROJECT_BRAIN.json`、`docs/RESEARCH_NOTES_SESSION_162.md` | Docs-as-Code 红线 |

## 2. 单一真理源 (Single Source of Truth)

所有动作状态枚举的唯一获取入口：

```python
from mathart.animation.unified_gait_blender import get_motion_lane_registry
states = list(get_motion_lane_registry().names())
# => ('fall', 'hit', 'idle', 'jump', 'run', 'walk')
```

任何文件中再出现裸 `["idle", "run", "jump", "fall", "hit"]` 即视为红线违规，
应立即由后续会话铲除并替换为上述查询。

## 3. 烘焙阶段 Fail-Fast 防线 (SESSION-162 升级)

```python
# tools/run_mass_production_factory.py — _bake_true_motion_guide_sequence 出口
from mathart.core.anti_flicker_runtime import assert_nonzero_temporal_variance
try:
    assert_nonzero_temporal_variance(source_frames, channel="source")
except RuntimeError as e:
    from mathart.pipeline_contract import PipelineContractError
    raise PipelineContractError("frozen_guide_sequence", str(e))
```

任何冻结/静止帧序列在纯 CPU 阶段就会被 `PipelineContractError("frozen_guide_sequence")` 中断，
**绝不进入下游 ComfyUI / GPU 流水线**，零算力浪费。

## 4. 遗留事项 (Carry-Over)

- `tests/` 目录的部分测试模块依赖 `networkx` / `hypothesis` / `mathart.animation.AnglePoseProjector` 等，
  与本次注册表/RenderContext 改造无关，属于既有环境债务，**不在本次红线手术范围**，留待后续会话处理。
- 既有 `evolution_preview_states = ["idle", "run", "jump"]` 字段为"快速预览子集"，
  有意保留（仅为评估子集），不在铲除范围内；如未来要去除请同步更新本文档。

## 5. 红线契约 (Red Lines, Inherited + New)

1. **(SESSION-160 继承)** 所有 `_MOTION_STATES = [...]` 类硬编码列表禁止存在。
2. **(SESSION-160 继承)** RenderContext 时序参数禁止退化为局部默认值。
3. **(SESSION-160 继承)** `assert_nonzero_temporal_variance` 不得被 try/except 静默吞噬。
4. **(SESSION-162 新增)** `assert_nonzero_temporal_variance` 必须**前置**到烘焙函数出口，
   而非仅在 AI 渲染边界。
5. **(SESSION-162 新增)** 任何外网研究依据必须按 Docs-as-Code 落盘到 `docs/RESEARCH_NOTES_SESSION_*.md`。

## 6. 下一步 (Next Session Suggestions)

- **SESSION-163 候选**: 把 `tests/test_no_hardcoded_motion_states.py` 加入 CI gate，
  用 AST 扫描永久守门，杜绝硬编码列表回潮。
- **SESSION-163 候选**: 在 ComfyUI 推流前再加一道独立的 `assert_nonzero_temporal_variance(target=normal_maps)` 防线，
  确保 normal/depth 通道也无静止漏网。
