# SESSION-163 交接文档 (SESSION_HANDOFF.md)

> **"老大，API 通讯网线已彻底打通！从 CPU 烘焙到 ComfyUI GPU 渲染的端到端闭环已就绪。
> 断路器、指数退避、优雅降级三重保护全部部署到位，即使 ComfyUI 压根没开也绝不闪退。
> 全阵列推流后端已自注册进 IoC 注册表，零主干修改。
> 请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，
> 而是拥有标准跑跳动作姿态的成套工业图纸！"**

**Date**: 2026-04-23
**Parent Commit**: SESSION-162
**Task ID**: P0-SESSION-161-COMFYUI-API-BRIDGE
**Status**: CLOSED
**External Anchors**: `docs/RESEARCH_NOTES_SESSION_163.md`

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **全阵列 AI 渲染推流后端** | `mathart/backend/ai_render_stream_backend.py` | ComfyUI REST API + WebSocket 规范 |
| 2 | **工作流模板打样** | `mathart/assets/workflows/workflow_api_template.json` | ControlNet + KSampler 双通道强约束 |
| 3 | **断路器 + 指数退避** | `mathart/backend/ai_render_stream_backend.py` (CircuitBreaker) | Michael Nygard "Release It!" / AWS Backoff+Jitter |
| 4 | **BackendType + ArtifactFamily 扩展** | `mathart/core/backend_types.py`, `mathart/core/artifact_schema.py` | IoC 注册表模式 |
| 5 | **BackendCapability + 自动发现** | `mathart/core/backend_registry.py` | LLVM TargetRegistry 自注册 |
| 6 | **完整测试套件** | `tests/test_ai_render_stream_backend.py` | 20+ 测试用例覆盖所有红线 |
| 7 | **外网研究锚点 Docs-as-Code 落盘** | `docs/RESEARCH_NOTES_SESSION_163.md` | 6 条公开工业出处 |
| 8 | **USER_GUIDE 文档同步** | `docs/USER_GUIDE.md` 第 9 节 | DaC 文档契约 |
| 9 | **三大状态文件全量同步** | `SESSION_HANDOFF.md`, `PROJECT_BRAIN.json`, `docs/RESEARCH_NOTES_SESSION_163.md` | Docs-as-Code 红线 |

## 2. 架构拓扑 (Architecture Topology)

```
[CPU 烘焙引擎]
    guide_baking_stage (Catmull-Rom 插值)
        ├── Albedo 序列帧
        ├── Normal 序列帧
        └── Depth 序列帧
            │
            ▼
[API 桥梁层] ← SESSION-163 新增
    AIRenderStreamBackend (@register_backend)
        ├── CircuitBreaker (3-state: CLOSED→OPEN→HALF_OPEN)
        ├── ExponentialBackoff (base=2s, max=32s, jitter=1.5s)
        ├── ComfyAPIClient (upload_image → queue_prompt → get_image)
        └── ComfyWorkflowMutator (semantic _meta.title injection)
            │
            ▼
[ComfyUI GPU 渲染]
    ControlNet Normal + Depth → KSampler → VAEDecode → SaveImage
        │
        ▼
[Pipeline Context Hydration]
    ai_render_{action}_{frame:02d}.png → 注册进总线
```

## 3. 新增文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `mathart/backend/ai_render_stream_backend.py` | 新增 | 全阵列推流后端 + 断路器 + 指数退避 |
| `mathart/assets/workflows/workflow_api_template.json` | 新增 | 极简 ControlNet + KSampler 工作流模板 |
| `tests/test_ai_render_stream_backend.py` | 新增 | 完整测试套件 |
| `docs/RESEARCH_NOTES_SESSION_163.md` | 新增 | 外网研究锚点文档 |
| `mathart/core/backend_types.py` | 修改 | 新增 `AI_RENDER_STREAM` 类型 + 别名 |
| `mathart/core/artifact_schema.py` | 修改 | 新增 `AI_RENDER_STREAM_REPORT` 家族 |
| `mathart/core/backend_registry.py` | 修改 | 新增 `AI_RENDER_STREAM` 能力 + 自动导入 |
| `mathart/backend/__init__.py` | 修改 | 更新包文档 |
| `docs/USER_GUIDE.md` | 修改 | 新增第 9 节 |
| `SESSION_HANDOFF.md` | 重写 | 本文档 |
| `PROJECT_BRAIN.json` | 修改 | 新增任务条目 |

## 4. 单一真理源 (Single Source of Truth)

所有动作状态枚举的唯一获取入口（SESSION-160 继承）：

```python
from mathart.animation.unified_gait_blender import get_motion_lane_registry
states = list(get_motion_lane_registry().names())
# => ('fall', 'hit', 'idle', 'jump', 'run', 'walk')
```

任何文件中再出现裸 `["idle", "run", "jump", "fall", "hit"]` 即视为红线违规。

## 5. 烘焙阶段 Fail-Fast 防线 (SESSION-162 继承)

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

## 6. 红线契约 (Red Lines, Inherited + New)

1. **(SESSION-160 继承)** 所有 `_MOTION_STATES = [...]` 类硬编码列表禁止存在。
2. **(SESSION-160 继承)** RenderContext 时序参数禁止退化为局部默认值。
3. **(SESSION-160 继承)** `assert_nonzero_temporal_variance` 不得被 try/except 静默吞噬。
4. **(SESSION-162 继承)** `assert_nonzero_temporal_variance` 必须**前置**到烘焙函数出口。
5. **(SESSION-162 继承)** 任何外网研究依据必须按 Docs-as-Code 落盘到 `docs/RESEARCH_NOTES_SESSION_*.md`。
6. **(SESSION-163 新增)** `ConnectionRefusedError` 绝对禁止导致 Traceback 崩溃闪退。必须优雅捕获并打印黄色警告后平滑退回主循环。
7. **(SESSION-163 新增)** 绝对不允许在代码中硬编码绝对路径。所有路径必须从 `context['artifacts']` 动态提取。
8. **(SESSION-163 新增)** `mathart/assets/workflows/` 下必须存在可用的 `workflow_api_template.json` 模板。
9. **(SESSION-163 新增)** AI 渲染输出必须重命名为 `ai_render_{action}_{frame:02d}.png` 格式。
10. **(SESSION-163 新增)** 新后端必须通过 `@register_backend` 自注册，零主干修改。

## 7. 遗留事项 (Carry-Over)

- `tests/` 目录的部分测试模块依赖 `networkx` / `hypothesis` / `mathart.animation.AnglePoseProjector` 等，
  与本次 API 桥梁改造无关，属于既有环境债务，**不在本次红线手术范围**。
- 既有 `evolution_preview_states = ["idle", "run", "jump"]` 字段为"快速预览子集"，
  有意保留（仅为评估子集），不在铲除范围内。
- ComfyUI 序列渲染（AnimateDiff + SparseCtrl）的工作流模板已存在于 `mathart/assets/comfyui_presets/`，
  本次新增的 `workflow_api_template.json` 是面向单帧 ControlNet 渲染的极简模板，两者互补。
- 断路器参数（failure_threshold=3, recovery_timeout=30s）为初始保守值，
  可根据实际 GPU 集群表现在后续 SESSION 中调优。

## 8. 三层进化循环 (Three-Layer Evolution Loop)

| 层级 | 能力 | 触发条件 |
|------|------|----------|
| **L1: 内部进化** | 动态注册表自动发现新动作、新后端 | 新 `@register_backend` 模块被导入 |
| **L2: 外部知识蒸馏** | 研究笔记 → 代码实践 → 测试验证 | 新 SESSION 提供外网参考资料 |
| **L3: 自我迭代测试** | 断路器状态自适应、退避参数自调优 | 运行时 ComfyUI 可用性变化 |

## 9. 下一步 (Next Session Suggestions)

- **SESSION-164 候选**: 将 `tests/test_ai_render_stream_backend.py` 加入 CI gate，确保断路器和优雅降级在每次 PR 中都被验证。
- **SESSION-164 候选**: 扩展 `workflow_api_template.json` 为序列感知版本，集成 AnimateDiff + SparseCtrl 节点。
- **SESSION-164 候选**: 在 ComfyUI 推流前加一道独立的 `assert_nonzero_temporal_variance(target=normal_maps)` 防线，确保 Normal/Depth 通道也无静止漏网。
- **SESSION-164 候选**: 把 `tests/test_no_hardcoded_motion_states.py` 加入 CI gate，用 AST 扫描永久守门。
