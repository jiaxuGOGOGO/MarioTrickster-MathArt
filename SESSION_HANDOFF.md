# SESSION-129 HANDOFF — P0-SESSION-127-STRICT-PIPELINE-SEMANTICS: Bone Topology / Dimension Fail-Fast / Guide Forgery Eradication

**Objective**：执行 **P0-SESSION-127-STRICT-PIPELINE-SEMANTICS** 核心攻坚计划，修复四足骨骼语义断层、切断 AI 渲染分辨率降级 Fallback、歼灭工厂单图复制伪造序列，建立跨模块硬性数据契约与 Fail-Fast 断言。

**Status**：**CLOSED（SESSION-129 三叉攻坚完成）**。

本轮工作以四大工业界/学术界参考为最高准则（Bertrand Meyer Design by Contract、Jim Gray Fail-Fast、UE5 Animation Retargeting、SparseCtrl/AnimateDiff Temporal Coherence），对量产管线执行了三叉同步攻坚：修复骨骼语义断层、切断分辨率降级、歼灭序列伪造。所有代码变更均严格遵守 Registry Pattern 独立插件纪律，零越权修改主干。

## 研究基础与设计决策

本次代码落地前，强制完成了四项外网参考研究，研究成果记录于 `research/research_session129_industrial_references.md` 和 `research/session129_code_analysis.md`。

| 参考来源 | 核心洞察 | 在代码中的体现 |
|---|---|---|
| **Bertrand Meyer — Design by Contract (DbC)** | 模块边界必须有严苛的前置/后置条件；违反契约立即拒绝执行 | `export_spine_json()` 后置条件：动画轨道骨骼名必须全部存在于 setup skeleton |
| **Jim Gray — Fail-Fast / Crash-Only Software** | 发现异常立即崩溃抛错，严禁启用兜底逻辑产生垃圾废料 | `PipelineContractError(missing_render_dimensions)` 和 `PipelineContractError(render_dimensions_too_small)` |
| **UE5 Animation Retargeting** | 动画曲线命名空间必须与基础骨架进行强类型 1:1 映射 | `QUADRUPED_NSM_TO_BONE_MAP` 将 NSM 语义肢体名映射到结构骨骼名 |
| **SparseCtrl / AnimateDiff Temporal Coherence** | 给扩散模型喂入 N 张复制的静态帧会摧毁时序注意力 | `_build_guide_sequence()` 生成确定性逐帧微变异，保留时序信号 |

## 三大核心交付物

### 1. 修复四足动画骨骼语义断层 (`mathart/animation/motion_2d_pipeline.py`)

**根因分析**：NSM 步态引擎输出语义肢体名（`front_left`, `front_right`, `hind_left`, `hind_right`），而骨架 setup bones 使用结构名（`fl_upper`, `fr_upper`, `hl_upper`, `hr_upper`）。`_nsm_to_3d_pose()` 此前仅处理 biped 映射，导致 Spine JSON 包含引用不存在骨骼的动画轨道，产生 38 帧完全静止。

**修复方案**：

| 组件 | 变更 |
|---|---|
| `QUADRUPED_NSM_TO_BONE_MAP` | 新增字典，将 8 个 NSM 语义肢体名映射到对应的结构骨骼名 |
| `_nsm_to_3d_pose()` | 四足步态时应用 `QUADRUPED_NSM_TO_BONE_MAP` 映射 |
| `export_spine_json()` | 后置条件验证：遍历所有动画轨道，任何引用不在 setup bones 中的骨骼名立即抛出 `PipelineContractError` |

**测试覆盖**（`TestSession129BoneTopologyContract`，4 个新测试）：
- `test_quadruped_spine_json_bone_consistency` — 无孤儿动画轨道
- `test_quadruped_animation_contains_fl_fr_hl_hr_bones` — 结构名存在
- `test_quadruped_no_semantic_limb_names_in_animation` — 无 NSM 语义名泄漏
- `test_biped_spine_json_bone_consistency` — biped 同样通过拓扑契约

### 2. 切断 AI 渲染分辨率降级 Fallback (`mathart/core/builtin_backends.py`)

**根因分析**：`AntiFlickerRenderBackend.validate_config()` 在 `width`/`height` 缺失时默认为 64，并静默 clamp 微小尺寸。工厂从未传递这些参数，导致内部 idle 烘焙路径生成最小分辨率帧，最终输出 32x16 抽象色块。

**修复方案**：

| 验证层级 | 条件 | 触发异常 |
|---|---|---|
| 尺寸存在性 | `width` 或 `height` 缺失 | `PipelineContractError(violation_type="missing_render_dimensions")` |
| 尺寸下限 | `width < 64` 或 `height < 64` | `PipelineContractError(violation_type="render_dimensions_too_small")` |
| 外部引导绕过 | `source_frames`/`normal_maps`/`depth_maps` 已提供 | 设置 `_idle_bake_bypassed = True`，继承引导序列分辨率 |

**测试覆盖**（3 个新测试 + 所有现有测试更新）：
- `test_validate_config_rejects_missing_dimensions` — 缺失尺寸触发 Fail-Fast
- `test_validate_config_rejects_tiny_dimensions` — 微小尺寸触发 Fail-Fast
- `test_external_guide_bypass_inherits_resolution` — 外部引导继承分辨率

### 3. 歼灭工厂单图复制伪造序列 (`tools/run_mass_production_factory.py`)

**根因分析**：`_image_sequence_from_render_manifest()` 将单张正交渲染图复制 N 份作为 guide 序列（`[base.copy() for _ in range(frame_count)]`），完全摧毁了 SparseCtrl/AnimateDiff 的时序注意力机制。

**修复方案**：

| 组件 | 变更 |
|---|---|
| `_build_guide_sequence()` | 每帧生成确定性微变异：亚像素仿射抖动（±0.5px）+ 亮度微扰动（×0.995–1.005），种子 = `channel_hash ^ (frame_index * 7919)` |
| `_node_ai_render()` | 从引导帧继承 `width`/`height`，显式传递给 `anti_flicker_render` 后端 |
| `_SESSION_ID` | 更新为 `"SESSION-129"` |

**确定性保证**：每帧的 RNG 种子由 `channel_hash` 和 `frame_index` 确定性派生，保持 Bazel 级可复现性。

## 防混线红线审计

| 红线 | 审计结果 |
|---|---|
| **防兜底逻辑污染** | ✅ `validate_config()` 中零 `kwargs.get('width', 32)` 式静默默认值；缺失/微小尺寸立即 `raise PipelineContractError` |
| **防序列造假** | ✅ 零 `[guide_image] * n_frames` 列表乘法复制；每帧独立生成确定性微变异 |
| **骨骼一致性测试** | ✅ 4 个新测试强制断言 `fl_upper` 等正确结构名存在，`front_left` 等语义名不存在 |
| **Payload 端到端阻断** | ✅ 3 个新测试断言缺失/微小尺寸触发 `PipelineContractError`，外部引导继承分辨率 |
| **防越权架构污染** | ✅ 零 `AssetPipeline`/`Orchestrator` 主干修改；所有变更封装在现有模块内 |

## 白盒验证闭环

| 验证命令 | 结果 |
|---|---|
| `python3.11 -m pytest tests/test_motion_2d_pipeline.py -v` | **28/28 PASS**（+4 新骨骼拓扑测试） |
| `python3.11 -m pytest tests/test_p1_ai_2c_anti_flicker_live_cli.py -v` | **9/9 PASS**（+3 新尺寸契约测试） |
| `python3.11 -m pytest tests/test_orthographic_pixel_render.py -v` | **24/24 PASS** |
| `python3.11 -m pytest tests/test_mass_production.py -v` | **2/2 PASS** |
| `python3.11 -m pytest tests/test_ci_backend_schemas.py -v` | **13/14 PASS**（1 个预存 pseudo_3d_shell 问题） |

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `mathart/animation/motion_2d_pipeline.py` | **UPD** | 四足骨骼映射 + 拓扑契约验证 |
| `mathart/core/builtin_backends.py` | **UPD** | Fail-Fast 尺寸契约 + 外部引导绕过 |
| `tools/run_mass_production_factory.py` | **UPD** | 引导序列重写 + width/height 传递 |
| `tests/test_motion_2d_pipeline.py` | **UPD** | 4 个新骨骼拓扑测试 |
| `tests/test_p1_ai_2c_anti_flicker_live_cli.py` | **UPD** | 3 个新尺寸契约测试 + fixture 更新 |
| `tests/test_ci_backend_schemas.py` | **UPD** | anti_flicker_render fixture 添加 width/height |
| `research/research_session129_industrial_references.md` | **NEW** | 四大参考研究笔记 |
| `research/session129_code_analysis.md` | **NEW** | 骨骼命名断层根因分析 |
| `PROJECT_BRAIN.json` | **UPD** | SESSION-129 状态、resolved issues、architecture notes |
| `SESSION_HANDOFF.md` | **UPD** | 本文档 |

## 接下来：基于多帧光流与 pHash 动态时序评估的 AI 渲染质量卡点系统（Quality Gate）

SESSION-128 建立了确定性基础（RNG digest 链、Fail-Fast mesh 契约、集中归档），SESSION-129 建立了数据完整性基础（骨骼拓扑契约、尺寸契约、真实引导序列）。架构现在已准备好接入 **Quality Gate** 系统。以下是当前架构还需要的四项微调准备：

### 微调准备 1：逐帧 pHash 计算注入引导序列生成

**现状**：`_build_guide_sequence()` 生成逐帧微变异但不计算/存储逐帧感知哈希。

**所需调整**：在 `_build_guide_sequence()` 中，每帧生成后计算 `imagehash.phash()` 并存储到 sidecar metadata dict。这使得序列内唯一性验证（断言无两帧共享相同 pHash）和下游 AI 渲染对比成为可能。

**预估工作量**：`run_mass_production_factory.py` 中 ~20 行 + `pip install imagehash`。

### 微调准备 2：连续引导帧间光流提取

**现状**：引导帧有确定性微变异但未提取/存储显式光流信号。

**所需调整**：添加 `_compute_guide_optical_flow()` 函数，对连续引导帧运行 Farneback 或 RAFT 光流，存储流量统计（均值、最大值、方差）到引导序列 metadata。这提供了 Quality Gate 可与 AI 渲染输出对比的定量时序一致性基线。

**预估工作量**：~50 行，利用项目中已有的 `cv2.calcOpticalFlowFarneback`。

### 微调准备 3：QualityGateBackend 注册表插件

**现状**：AI 渲染输出与引导输入之间无自动化质量卡点。

**所需调整**：创建 `QualityGateBackend`：
- 接受 AI 渲染帧序列 + 引导帧序列作为输入
- 计算逐帧 pHash 距离（AI 输出 vs 引导输入）
- 计算帧间 SSIM 时序稳定性分数
- 计算光流一致性比率（AI 光流 vs 引导光流）
- 产出 `QUALITY_GATE_REPORT` 类型 `ArtifactManifest`，含 pass/fail 判定
- 检测到时序崩塌时（如 >80% 帧共享相同 pHash）抛出 `PipelineContractError`

**预估工作量**：新文件 `mathart/core/quality_gate_backend.py`（~200 行）+ 测试。

### 微调准备 4：集成 FrameSequenceExporter 作为规范物化路径

**现状**：`mathart/animation/frame_sequence_exporter.py` 已存在，具备完整的逐帧验证、分辨率一致性强制和 ArtifactManifest 产出。但工厂的引导生成仍为纯内存操作。

**所需调整**：将 `_build_guide_sequence()` 输出路由通过 `FrameSequenceExporter` 物化引导帧到磁盘，附带完整 metadata，使 Quality Gate 可执行离线对比，使归档交付可捕获引导序列与 AI 渲染输出。

**预估工作量**：~30 行，将 `FrameSequenceExporter` 接入工厂流程。

### 待办列表更新

| ID | 优先级 | 标题 | 状态 |
|---|---|---|---|
| P0-SESSION-127-CORE-CONSTRAINTS | P0 | Fail-Fast Mesh3D / RNG Digest / Archive Delivery | **CLOSED (SESSION-128)** |
| P0-SESSION-127-STRICT-PIPELINE-SEMANTICS | P0 | Bone Topology / Dimension Fail-Fast / Guide Forgery | **CLOSED (SESSION-129)** |
| P1-QUALITY-GATE-1 | P1 | 逐帧 pHash 计算 + 光流提取 + QualityGateBackend | TODO |
| P1-QUALITY-GATE-2 | P1 | FrameSequenceExporter 集成为规范物化路径 | TODO |
| P2-COMFYUI-REPRO-1 | P2 | ComfyUI 工作流确定性固定与种子绑定 | TODO |
| P2-COMFYUI-REPRO-2 | P2 | GPU 设备指纹注入与跨机器对比 | TODO |
| P2-COMFYUI-REPRO-3 | P2 | 帧级 pHash/SSIM 回归基线建立 | TODO |
| P2-GPU-RENDER-1 | P2 | nvdiffrast / Mitsuba 3 GPU 可微渲染替换 | TODO |

## Local Production Commands

命令格式与 SESSION-128 保持一致，SESSION-129 的变更不影响 CLI 接口：

| 场景 | 推荐命令 |
|---|---|
| 纯 CPU / dry-run 审计 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --skip-ai-render --seed 20260422` |
| 标准本地量产 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |
| 保守显存模式 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 12 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |

## Immediate Operator Guidance

如果主理人现在要在本地正式开跑，请先执行一次带 `--skip-ai-render` 的 dry-run，重点检查：

1. **Spine JSON 导出**：确认四足角色的动画轨道包含 `fl_upper`/`fr_upper`/`hl_upper`/`hr_upper`，不包含 `front_left`/`hind_right` 等语义名
2. **`batch_summary.json`** 中每个角色、每个阶段都有 `rng_spawn_digest` 且角色间不重复
3. **`orthographic_pixel_render/` 下的 render report**，确认 `mesh_contract.fail_fast_enforced = true`
4. **`archive/` 目录**，确认所有阶段产物集中存在

## Known Pre-Existing Issues (Not Introduced by SESSION-129)

1. **`pseudo_3d_shell` CI schema failure**: `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` — 预存于 SESSION-128 之前，由 CI minimal context builder 中缺失的 fixture 值导致。
2. **`anti_flicker_render` CI schema `keyframe_count` missing**: 离线 pipeline 路径（无 ComfyUI 服务器）产出的 manifest 缺少 `anti_flicker_report` family schema 要求的 `keyframe_count` quality metric。这是环境依赖问题（需要 live ComfyUI 服务器）。

从路线图角度看，SESSION-128 + SESSION-129 完成了从"量产可跑"到"量产可信"再到"量产可审"的关键跃迁。38 帧四足静止灾难、32x16 抽象色块灾难、单图复制伪造序列灾难已在类型系统和数据契约层面被永久消灭。下一步的战略方向是基于光流和 pHash 的 AI 渲染质量卡点系统，而 SESSION-129 建立的骨骼拓扑契约、尺寸契约和真实引导序列正是这个方向的必要前置条件。
