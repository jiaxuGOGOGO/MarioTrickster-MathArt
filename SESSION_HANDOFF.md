# SESSION-128 HANDOFF — P0-SESSION-127-CORE-CONSTRAINTS: Fail-Fast Mesh3D / RNG Digest / Archive Delivery

**Objective**：执行 **P0-SESSION-127-CORE-CONSTRAINTS** 核心攻坚计划，彻底消除回退网格依赖，打通量产级归档与 RNG 追踪闭环，根治 `generator_invariant` 停滞灾难。

**Status**：**CLOSED（SESSION-128 三叉攻坚完成）**。

本轮工作以四大工业界/学术界参考为最高准则（Pixar USD Composition Semantics、Bazel Action Cache Determinism、Data Mesh Delivery Contract、Jim Gray Fail-Fast），对量产管线执行了三叉同步攻坚：斩断 Fallback 幽灵、打通 RNG 摘要透传、落实集中归档交付。所有代码变更均严格遵守 Registry Pattern 独立插件纪律，零越权修改主干。

## 研究基础与设计决策

本次代码落地前，强制完成了四项外网参考研究，研究成果记录于 `research/research_session128_industrial_references.md`。

| 参考来源 | 核心洞察 | 在代码中的体现 |
|---|---|---|
| **Pixar USD Composition Semantics** | 强类型引用必须解析到真实几何；缺失层级触发组合错误，不做静默替换 | `OrthographicPixelRenderBackend` 对 `Mesh3D` 的三级验证（存在性→类型→几何非空） |
| **Bazel / Buck Action Cache & Determinism** | 构建动作的输出仅取决于声明的输入；输入为 demo sphere 则输出哈希确定性地"错误" | `rng_spawn_digest` 注入所有 `ArtifactManifest.metadata`，实现 Bazel 级哈希可审计 |
| **Data Mesh Delivery Contract** | `/archive` 目录是最终交付契约；交付 demo sphere 渲染违反 SLA | `ArchiveDeliveryBackend` 作为独立注册表插件，集中收集所有上游产物 |
| **Jim Gray Fail-Fast (Tandem Computers, 1985)** | "每个模块自检；发现故障立即停止" | `PipelineContractError` 三级触发：`missing_mesh3d` / `invalid_mesh3d_type` / `empty_mesh3d` |

## 三大核心交付物

### 1. 斩断 Fallback 幽灵 — Fail-Fast Mesh3D Consumption Contract

`mathart/core/orthographic_pixel_backend.py` 完全重写。正交渲染后端现在执行**三级 Fail-Fast 验证**：

| 验证层级 | 条件 | 触发异常 |
|---|---|---|
| 存在性检查 | `context.get("mesh") is None` | `PipelineContractError(violation_type="missing_mesh3d")` |
| 类型检查 | `not isinstance(mesh, Mesh3D)` | `PipelineContractError(violation_type="invalid_mesh3d_type")` |
| 几何非空检查 | `mesh.vertex_count == 0 or mesh.triangle_count == 0` | `PipelineContractError(violation_type="empty_mesh3d")` |

**零 Fallback 球体生成**。代码库中与 Fallback Dummy Mesh 相关的所有逻辑已被永久移除。这从物理源头消灭了 22,422 次 `generator_invariant` 停滞迭代的病根。

### 2. PDG 节点级 RNG 摘要透传

`rng_spawn_digest` 现在被强制注入到量产工厂所有阶段的 `ArtifactManifest.metadata` 字典中：

| 工厂阶段 | 注入方式 |
|---|---|
| `orthographic_render_stage` | `manifest.metadata["rng_spawn_digest"] = rng_digest` |
| `unified_motion_stage` | `manifest.metadata["rng_spawn_digest"] = rng_digest` |
| `pseudo3d_shell_stage` | `manifest.metadata["rng_spawn_digest"] = rng_digest` |
| `physical_ribbon_stage` | `manifest.metadata["rng_spawn_digest"] = rng_digest` |
| `motion2d_export_stage` | 写入 motion2d report JSON + 返回值 |
| `final_delivery` | `unity_manifest.metadata["rng_spawn_digest"]` + `preview_manifest.metadata["rng_spawn_digest"]` |
| `ai_render` | `manifest.metadata["rng_spawn_digest"] = rng_digest` |

这些摘要同时写入 `character_<id>_factory_index.json` 和 `batch_summary.json`，达到 Bazel 级哈希可验证标准。

### 3. 集中归档 Backend — ArchiveDeliveryBackend

`mathart/core/archive_delivery_backend.py` 是一个全新的独立注册表插件：

- 通过 `@register_backend("archive_delivery", ...)` 自注册，零主干修改
- 消费 `archive_sources` 列表（每项包含 `label`、`manifest_path`、`files`、`rng_spawn_digest`）
- 将所有上游产物集中复制到 `character_<id>/archive/` 目录
- 生成 `<character_id>_archive_report.json` 详细清单
- 返回强类型 `ArtifactManifest`（`artifact_family=META_REPORT`）
- 聚合所有阶段的 `rng_spawn_digest` 到 `rng_digests` 元数据字段
- `BackendType.ARCHIVE_DELIVERY` 已添加到 `backend_types.py` 枚举与别名表

## 防混线红线审计

| 红线 | 审计结果 |
|---|---|
| 防越权架构污染 | ✅ 零 `AssetPipeline` / `Orchestrator` 主干修改；归档逻辑完全封装在独立 Backend 插件中 |
| 防伪造数据欺骗 | ✅ 零 Fallback Dummy Mesh；`PipelineContractError` 三级触发确保端到端流程从 Genotype 发起 |
| 防敷衍 RNG 追踪 | ✅ `rng_spawn_digest` 硬编码序列化写入 Manifest 和 `batch_summary.json`；测试可反解析 |
| 端到端闭环测试 | ✅ 24/24 正交渲染测试 PASS（0 警告 / 0 Fallback）；2/2 量产工厂测试 PASS |

## 白盒验证闭环

| 验证命令 | 结果 |
|---|---|
| `python3.11 -m pytest tests/test_orthographic_pixel_render.py -v` | **24/24 PASS** |
| `python3.11 -m pytest tests/test_mass_production.py -v` | **2/2 PASS** |
| `python3.11 -m pytest tests/test_ci_backend_schemas.py -v` | **13/14 PASS**（1 个预存 anti_flicker_render 问题，非 SESSION-128 引入） |

关键断言覆盖：

- `test_backend_execute_fail_fast_no_mesh`：确认无 mesh 时抛出 `PipelineContractError`
- `test_backend_execute_fail_fast_empty_mesh`：确认空 mesh（0 顶点/0 三角形）时抛出 `PipelineContractError`
- `test_backend_execute_returns_artifact_manifest`：使用真实 `Mesh3D` 验证完整渲染流程
- CI backend schema 动态发现测试：`orthographic_pixel_render` 和 `archive_delivery` 均通过 fixture 注入验证

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `mathart/core/orthographic_pixel_backend.py` | **UPD** | 完全重写：Fail-Fast Mesh3D Consumption Contract |
| `mathart/core/archive_delivery_backend.py` | **NEW** | 集中归档交付 Backend 插件 |
| `mathart/core/backend_types.py` | **UPD** | 添加 `BackendType.ARCHIVE_DELIVERY` 枚举与别名 |
| `tools/run_mass_production_factory.py` | **UPD** | RNG digest 注入所有阶段 Manifest metadata；导入 archive_delivery_backend |
| `tests/test_orthographic_pixel_render.py` | **UPD** | 新增 Fail-Fast 测试；更新 execute 测试使用真实 Mesh3D |
| `tests/test_ci_backend_schemas.py` | **UPD** | Backend 特定 fixture 覆盖 |
| `research/research_session128_industrial_references.md` | **NEW** | 四大参考研究笔记 |
| `PROJECT_BRAIN.json` | **UPD** | SESSION-128 状态、resolved issues、architecture notes |
| `SESSION_HANDOFF.md` | **UPD** | 本文档 |

## 接下来：ComfyUI 跨机渲染可复现性增强与 GPU 可微渲染加速

SESSION-128 建立了确定性基础（RNG digest 链、Fail-Fast mesh 契约、集中归档），这是 ComfyUI 跨机器可复现性和 GPU 可微渲染加速的前置条件。以下是当前架构还需要的微调准备：

### 微调准备 1：ComfyUI 工作流确定性固定

当前 `HeadlessNeuralRenderPipeline` 生成的 ComfyUI 工作流 JSON 中，节点 ID 和随机种子尚未与 PDG 的 `rng_spawn_digest` 强绑定。需要：

- 将 `rng_spawn_digest` 作为 ComfyUI 工作流中所有 KSampler 节点的 `seed` 输入源
- 固定 ComfyUI 节点 ID 分配策略（确定性递增，而非随机 UUID）
- 在 `ArtifactManifest.metadata` 中记录完整的 ComfyUI workflow JSON hash

### 微调准备 2：GPU 设备指纹注入

跨机器对比需要知道渲染环境差异。需要在 `ArtifactManifest.metadata` 中添加：

- `gpu_device_name`（如 "NVIDIA GeForce RTX 4070"）
- `cuda_version`（如 "12.1"）
- `driver_version`（如 "535.129.03"）
- `comfyui_version` 和已加载模型的 SHA-256 hash

### 微调准备 3：帧级 pHash/SSIM 回归基线

建立 CPU dry-run 与 GPU live-run 之间的可量化差异基线：

- 对正交渲染阶段（纯 CPU NumPy）：两次运行应产生 bit-exact 相同输出
- 对 AI 渲染阶段（ComfyUI GPU）：记录帧级 pHash 和 SSIM，建立"可接受差异"阈值
- 将差异指标写入 `batch_summary.json` 的 `reproducibility_metrics` 字段

### 微调准备 4：GPU 可微渲染替换路径

当前正交渲染使用纯 NumPy 软件光栅化器。如需 GPU 加速：

- **nvdiffrast**（NVIDIA）：可直接替换光栅化步骤，保持相同的 `Mesh3D` 输入契约和 `ArtifactManifest` 输出 schema
- **Mitsuba 3**（EPFL）：提供可微分渲染，适合未来的风格迁移优化
- 替换时只需修改 `OrthographicPixelRenderBackend.execute()` 内部实现，Fail-Fast Mesh3D 契约和 `ArtifactManifest` 输出格式保持不变

### 待办列表更新

| ID | 优先级 | 标题 | 状态 |
|---|---|---|---|
| P0-SESSION-127-CORE-CONSTRAINTS | P0 | Fail-Fast Mesh3D / RNG Digest / Archive Delivery | **CLOSED (SESSION-128)** |
| P2-COMFYUI-REPRO-1 | P2 | ComfyUI 工作流确定性固定与种子绑定 | TODO |
| P2-COMFYUI-REPRO-2 | P2 | GPU 设备指纹注入与跨机器对比 | TODO |
| P2-COMFYUI-REPRO-3 | P2 | 帧级 pHash/SSIM 回归基线建立 | TODO |
| P2-GPU-RENDER-1 | P2 | nvdiffrast / Mitsuba 3 GPU 可微渲染替换 | TODO |

## Local Production Commands

命令格式与 SESSION-127 保持一致，SESSION-128 的变更不影响 CLI 接口：

| 场景 | 推荐命令 |
|---|---|
| 纯 CPU / dry-run 审计 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --skip-ai-render --seed 20260422` |
| 标准本地量产 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 20 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |
| 保守显存模式 | `python3.11 -m mathart.cli mass-produce --output-dir outputs --batch-size 12 --pdg-workers 16 --gpu-slots 1 --seed 20260422 --comfyui-url http://127.0.0.1:8188` |

## Immediate Operator Guidance

如果主理人现在要在本地正式开跑，请先执行一次带 `--skip-ai-render` 的 dry-run，重点检查：

1. **`batch_summary.json`** 中每个角色、每个阶段都有 `rng_spawn_digest` 且角色间不重复
2. **`orthographic_pixel_render/` 下的 render report**，确认 `mesh_contract.fail_fast_enforced = true` 且 `mesh_stats` 反映真实角色几何
3. **`archive/` 目录**，确认 Unity `.anim`、`preview.mp4`、正交辅助图与 AI 阶段证据都集中存在

从路线图角度看，SESSION-128 完成了从"量产可跑"到"量产可信"的关键跃迁。22,422 次 `generator_invariant` 停滞灾难已在类型系统层面被永久消灭。下一步的战略方向是 ComfyUI 跨机器可复现性与 GPU 可微渲染加速，而 SESSION-128 建立的 RNG digest 链和 Fail-Fast mesh 契约正是这两个方向的必要前置条件。
