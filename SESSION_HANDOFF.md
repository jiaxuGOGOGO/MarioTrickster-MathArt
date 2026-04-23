# SESSION HANDOFF — SESSION-151

> **"ComfyUI 的节点 ID 是沙上之塔，唯有语义标记才是磐石。" —— 贯通 BFF 动态载荷变异 + 无头渲染闭环，打通纯数学→AI 视觉抛光的最后一公里。**

**Date**: 2026-04-23
**Status**: COMPLETE — 29/29 tests PASS
**Parent Commit**: `ebd00bd` (SESSION-150)
**Task ID**: P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH
**Smoke**: `tests/test_comfyui_render_backend.py` → 29/29 PASSED（Mutator 9 + Client 8 + Backend 7 + Integration 3 + Red-Line Guards 2）

---

## 1. Executive Summary

SESSION-151 实现了**完整的端到端 ComfyUI 无头渲染后端** —— 这是上游纯数学动画管线与下游 AI 视觉抛光层之间的关键缺失环节。三个工业级模块落地于 `mathart/backend/`，通过 `@register_backend` 完全融入现有 Registry Pattern：

1. **ComfyWorkflowMutator** (`comfy_mutator.py`) — BFF 动态 JSON 树遍历变异器
2. **ComfyAPIClient** (`comfy_client.py`) — 高可用 HTTP+WebSocket 渲染客户端
3. **ComfyUIRenderBackend** (`comfyui_render_backend.py`) — Registry-native 后端插件

所有三条 **SESSION-151 反模式红线** 均已强制执行并通过测试：

| 红线 | 防护机制 | 测试用例 |
|---|---|---|
| 严禁硬编码 ComfyUI 节点 ID | `_meta.title` 语义匹配 | `test_red_line_no_hardcoded_node_ids` |
| API 轮询死锁屏障 | `time.sleep()` + `RenderTimeoutError` | `test_red_line_poll_has_sleep` |
| 输出资产统一落盘 | 全部渲染 → `outputs/production/` | `test_red_line_output_repatriation` |

---

## 2. What Was Built

### 2.1 ComfyWorkflowMutator (`mathart/backend/comfy_mutator.py`)

**架构**: BFF (Backend for Frontend) 载荷变异引擎

变异器实现了**语义 JSON 树遍历**策略，通过 `_meta.title` 字段中的标记（如 `[MathArt_Prompt]`、`[MathArt_Input_Image]`）查找 ComfyUI 节点，**绝不**使用数字节点 ID。这一点至关重要，因为 ComfyUI 在每次工作流编辑时都会重新生成节点 ID。

**核心设计决策**：

- **不可变蓝图模式 (Immutable Blueprint Pattern)**：原始工作流 JSON 在变异前进行深拷贝。蓝图永远不会被原地修改。
- **标记注入 (Marker-Based Injection)**：每个可注入节点在 `_meta.title` 中携带 `[MathArt_*]` 标记。变异器扫描所有节点，根据 `class_type` 语义将值注入正确的 `inputs` 字段。
- **变异审计账本 (Mutation Audit Ledger)**：每次注入都记录为 `MutationRecord`，包含 `marker`、`node_id`、`class_type`、`input_key`、`old_value`、`new_value` 和 `timestamp`。为调试和 GA 适应度溯源提供完整审计轨迹。
- **歧义检测 (Ambiguity Detection)**：如果多个节点匹配同一标记，立即抛出 `MutationError` —— 绝不静默损坏。

**支持的标记**：

| 标记 | Class Type | Input Key | 注入内容 |
|---|---|---|---|
| `[MathArt_Input_Image]` | `LoadImage` | `image` | 上传后的文件名 |
| `[MathArt_Prompt]` | `CLIPTextEncode` | `text` | 正向提示词 |
| `[MathArt_Negative]` | `CLIPTextEncode` | `text` | 负向提示词 |
| `[MathArt_Seed]` | `KSampler` | `seed` | 随机种子 |
| `[MathArt_Output]` | `SaveImage` | `filename_prefix` | 输出前缀 |

### 2.2 ComfyAPIClient (`mathart/backend/comfy_client.py`)

**架构**: 高可用无头 API 客户端

客户端实现了完整的 ComfyUI HTTP+WebSocket API 生命周期，具备工业级错误处理：

**临时资产推流 (Ephemeral Asset Upload)** — `upload_image()`：
- 通过 `POST /upload/image` multipart 表单上传代理图片
- 返回服务器端文件名用于工作流注入
- 绝不在工作流节点中引用本地文件系统路径

**渲染执行 (Render Execution)** — `render()`：
- 通过 `POST /prompt` 提交变异后的载荷
- 主通道：WebSocket 遥测 (`ws://{server}/ws?clientId={id}`)
- 备用通道：HTTP 轮询 `GET /history/{prompt_id}`
- 熔断器：可配置超时后抛出 `RenderTimeoutError`
- 轮询循环：`time.sleep(poll_interval)` 防止 CPU 空转

**输出回收 (Output Repatriation)** — `_download_outputs()`：
- 通过 `GET /view?filename=...&subfolder=...&type=output` 下载渲染图
- 保存至 `outputs/production/final_render_{timestamp}_{idx}.png`
- 绝不将资产遗留在 ComfyUI 内部 output 文件夹

**显存垃圾回收 (VRAM Garbage Collection)** — `free_vram()`：
- 调用 `POST /free`，参数 `{"unload_models": true, "free_memory": true}`
- 防止批量渲染时 OOM 崩溃

**优雅降级 (Graceful Degradation)**：
- 每次渲染前执行 `is_server_online()` 健康检查
- 服务器离线时返回 `RenderResult(degraded=True)` —— 绝不崩溃

### 2.3 ComfyUIRenderBackend (`mathart/backend/comfyui_render_backend.py`)

**架构**: Registry-Native `@register_backend` 插件

注册为 `BackendType.COMFYUI_RENDER`，声明 `COMFYUI_RENDER` 和 `GPU_ACCELERATED` 能力。产出 `ArtifactFamily.COMFYUI_RENDER_REPORT` 清单。

**执行管线**：
1. `validate_config()` — 后端拥有的参数规范化（六边形架构）
2. 健康检查 → 离线时优雅降级
3. 上传代理图片 → 临时推流
4. 构建变异载荷 → 语义注入
5. 提交渲染 → WebSocket/HTTP 轮询
6. 下载输出 → 回收至 `outputs/production/`
7. 释放显存 → 垃圾回收
8. 返回 `ArtifactManifest` 携带完整溯源元数据

**清单元数据契约** (`ArtifactFamily.COMFYUI_RENDER_REPORT` 必填字段)：

| 键 | 类型 | 描述 |
|---|---|---|
| `prompt_id` | `str` | ComfyUI 执行 ID |
| `server_address` | `str` | ComfyUI 服务器地址 |
| `render_elapsed_seconds` | `float` | 总渲染耗时 |
| `images_downloaded` | `int` | 输出图片数量 |
| `vram_freed` | `bool` | 是否已释放显存 |
| `mutation_count` | `int` | 应用的变异数量 |
| `blueprint_name` | `str` | 工作流蓝图文件名 |

---

## 3. Registry Integration Points

### BackendType 枚举 (`mathart/core/backend_types.py`)
```python
COMFYUI_RENDER = "comfyui_render"
```
别名: `comfyui_api_render`, `comfy_render`, `comfyui_headless`, `bff_render`

### BackendCapability 枚举 (`mathart/core/backend_registry.py`)
```python
COMFYUI_RENDER = auto()
```

### ArtifactFamily 枚举 (`mathart/core/artifact_schema.py`)
```python
COMFYUI_RENDER_REPORT = "comfyui_render_report"
```

### 自动导入钩子 (`mathart/core/backend_registry.py`)
```python
importlib.import_module("mathart.backend.comfyui_render_backend")
```

---

## 4. Test Results

```
tests/test_comfyui_render_backend.py — 29/29 PASSED

TestComfyWorkflowMutator (9 tests):
  ✓ test_find_nodes_by_title
  ✓ test_find_node_by_title_unique
  ✓ test_find_node_by_title_missing_raises
  ✓ test_find_node_by_title_ambiguous_raises
  ✓ test_mutate_injects_values
  ✓ test_mutate_optional_marker_skipped
  ✓ test_build_payload
  ✓ test_red_line_no_hardcoded_node_ids
  ✓ test_mutation_ledger_audit_trail

TestComfyAPIClient (8 tests):
  ✓ test_client_initialization
  ✓ test_client_custom_config
  ✓ test_server_offline_graceful_degradation
  ✓ test_render_timeout_error_type
  ✓ test_upload_error_type
  ✓ test_render_result_to_dict
  ✓ test_red_line_poll_has_sleep
  ✓ test_red_line_output_repatriation

TestComfyUIRenderBackend (7 tests):
  ✓ test_backend_registered
  ✓ test_backend_type_enum
  ✓ test_artifact_family_enum
  ✓ test_required_metadata_keys
  ✓ test_validate_config
  ✓ test_validate_config_strips_protocol
  ✓ test_validate_config_clamps_timeout
  ✓ test_execute_offline_degraded
  ✓ test_backend_type_aliases

TestIntegration (3 tests):
  ✓ test_mutator_to_client_payload_contract
  ✓ test_blueprint_file_loading
  ✓ test_end_to_end_offline_graceful
```

---

## 5. Files Touched

| 文件 | 操作 | 描述 |
|---|---|---|
| `mathart/backend/__init__.py` | **新增** | 包初始化，导出公共 API |
| `mathart/backend/comfy_mutator.py` | **新增** | BFF 动态 JSON 树遍历变异器 |
| `mathart/backend/comfy_client.py` | **新增** | 高可用 HTTP+WebSocket 客户端 |
| `mathart/backend/comfyui_render_backend.py` | **新增** | Registry-native 后端插件 |
| `mathart/core/backend_types.py` | **修改** | 新增 `COMFYUI_RENDER` 枚举 + 别名 |
| `mathart/core/backend_registry.py` | **修改** | 新增 `COMFYUI_RENDER` 能力 + 自动导入 |
| `mathart/core/artifact_schema.py` | **修改** | 新增 `COMFYUI_RENDER_REPORT` 族 + 元数据 |
| `tests/test_comfyui_render_backend.py` | **新增** | 29 项全面测试 |
| `outputs/production/.gitkeep` | **新增** | 生产输出目录 |
| `scripts/update_brain_session151.py` | **新增** | PROJECT_BRAIN.json 更新脚本 |
| `SESSION_HANDOFF.md` | **改写** | 本文档 |
| `PROJECT_BRAIN.json` | **更新** | v0.99.3, SESSION-151 |

---

## 6. 接下来：无缝接入遗传算法 (GA) 内环的架构微调路线图

### 6.1 当前后端为 GA 适应度评估器提供了什么

`COMFYUI_RENDER_REPORT` 清单专门设计用于喂给**遗传算法 (Genetic Algorithm) 内环** —— 自动化评分与突变管线。清单元数据包含 GA 适应度评估所需的全部信息，无需检查图像文件本身。

### 6.2 需要的微调准备

#### 微调 1: GA 适应度评分函数 (`P1-SESSION-151-GA-FITNESS-EVALUATOR`)

**当前状态**: `COMFYUI_RENDER_REPORT` 清单携带了适应度评估所需的全部元数据。

**需要构建**:
```
mathart/evolution/comfyui_fitness_evaluator.py
```

适应度函数应消费 `ArtifactManifest` 并计算多维适应度分数：

| 维度 | 范围 | 数据来源 |
|---|---|---|
| 渲染成功分 | 0/1 | `quality_metrics.render_success` |
| 时序一致性分 | 0-1 | `mutation_ledger` + 帧间 SSIM |
| 提示词遵从分 | 0-1 | CLIP 相似度（prompt vs 渲染图） |
| 风格一致性分 | 0-1 | 感知哈希距离（vs 参考风格） |
| 显存效率分 | 0-1 | `metadata.vram_freed` 惩罚项 |

评估器应实现 `EvolutionBridge` 协议，以便 `EvolutionOrchestrator` 通过 `BackendCapability.EVOLUTION_DOMAIN` 发现它。

#### 微调 2: 基因型→工作流映射 (`P1-SESSION-151-GENOTYPE-WORKFLOW-MAP`)

**当前状态**: 变异器接受显式注入字典。GA 需要将基因型向量映射为注入字典。

**需要构建**: `GenotypeWorkflowMapper` 将基因型（浮点向量）转换为：

| 基因维度 | 映射目标 | 范围 |
|---|---|---|
| dim[0:N] | `prompt` | 从提示词库索引选择 |
| dim[N] | `cfg_scale` | [5.0, 15.0] |
| dim[N+1] | `denoise_strength` | [0.3, 1.0] |
| dim[N+2] | `seed` | 基因型哈希确定性派生 |
| dim[N+3] | `sampler_name` | 分类选择（euler/dpmpp_2m/...） |

此映射器应为纯函数，无副作用。

#### 微调 3: PDG 批量渲染车道 (`P1-SESSION-151-BATCH-RENDER-LANE`)

**当前状态**: `run_mass_production_factory.py` 有 `ai_render_stage` 占位符。

**需要构建**: 将 `ComfyUIRenderBackend.execute()` 接入 PDG `ai_render_stage`：

```python
def ai_render_stage(context):
    backend = ComfyUIRenderBackend()
    manifest = backend.execute(context)
    return manifest
```

PDG 应扇出 N 个基因型 → N 个渲染任务 → N 个适应度分数 → 选择 + 交叉 + 突变。

#### 微调 4: 种群管理器 (`P2-SESSION-151-GA-POPULATION-MANAGER`)

**当前状态**: 项目已有 `evolution_loop.py` 中的 `InternalEvolver`。

**需要构建**: 扩展 `InternalEvolver` 为 `ComfyUIPopulationManager`：
- 维护 N 个基因型的种群（工作流参数向量）
- 通过批量渲染 + 适应度评估器评估适应度
- 应用锦标赛选择、交叉和突变
- 将精英基因型持久化至 `outputs/evolution/generation_{N}/`
- 发出 `EVOLUTION_COMFYUI_RENDER` 制品供知识蒸馏器消费

#### 微调 5: WebSocket 进度条 (`P1-SESSION-151-WEBSOCKET-PROGRESS-BAR`)

**当前状态**: `ComfyAPIClient` 接收 WebSocket 进度事件但仅记录日志。

**需要构建**: 将进度事件浮现到 CLI 向导 TUI：
- `progress` 事件 → 进度条百分比
- `status` 事件 → 状态行更新
- `error` 事件 → 即时错误显示

---

## 7. Updated Todo List

### P0 (立即)
- [x] ~~P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH~~ — **已关闭** (SESSION-151)

### P1 (下一冲刺)
- [ ] P1-SESSION-151-GA-FITNESS-EVALUATOR — 将 COMFYUI_RENDER_REPORT 接入 GA 适应度评分
- [ ] P1-SESSION-151-BATCH-RENDER-LANE — 在 PDG ai_render_stage 中添加批量渲染
- [ ] P1-SESSION-151-WEBSOCKET-PROGRESS-BAR — 将 WS 进度浮现到 CLI 向导 TUI
- [ ] P1-SESSION-151-GENOTYPE-WORKFLOW-MAP — 基因型向量 → 工作流注入映射
- [ ] P1-SESSION-149-LOG-THROTTLE-EXTRACT — 提升 _emit_demo_warning 为 mathart.core.log_throttle
- [ ] P1-SESSION-149-QUALITY-BOUNDARY-TESTS — 将烟测断言固化到 tests/

### P2 (积压)
- [ ] P2-SESSION-151-MULTI-WORKFLOW-STRATEGY — 每批渲染支持多个工作流蓝图（风格 A/B 测试）
- [ ] P2-SESSION-151-COMFYUI-MODEL-CACHE — 批量渲染前预热模型缓存
- [ ] P2-SESSION-151-GA-POPULATION-MANAGER — 完整种群管理器 + 精英持久化
- [ ] P2-SESSION-149-DEMO-VIBE-PARAMS — 接通 vibe parser NL → intent params 自动映射

---

## 8. Architecture Decision Record

### ADR-SESSION-151: ComfyUI BFF 载荷变异与无头渲染架构

**上下文**: ComfyUI 工作流是 JSON 图，节点 ID 是自动生成的整数，每次工作流编辑都会改变。之前的方法硬编码节点 ID，导致艺术家修改工作流时立即崩溃。

**决策**: 所有节点发现必须使用语义 `_meta.title` 标记匹配。节点 ID 被视为运行时发现的不透明句柄，绝不在源代码中引用。图像资产必须通过临时 multipart 推流上传，绝不引用本地路径。HTTP 轮询循环必须包含 `time.sleep()` 和可配置超时的 `RenderTimeoutError` 熔断器。所有渲染输出必须从 ComfyUI 内部 output 文件夹回收到 `outputs/production/`。每次渲染批次后必须通过 `POST /free` 释放显存。

**影响**: BFF 变异架构使渲染管线对工作流编辑具有弹性 —— 艺术家可以自由修改 ComfyUI 工作流而不破坏自动化。临时上传模式消除了跨平台路径问题。超时熔断器防止长渲染时的终端死锁。输出回收确保所有生产资产可版本控制和可发现。显存 GC 防止批量渲染时的 OOM 崩溃。强类型 `COMFYUI_RENDER_REPORT` 清单提供了即将到来的 GA 适应度评估器所需的精确元数据契约。

---

## 9. Historical Index (Recent Sessions)

| Session | 主线 | Commit |
|---------|------|--------|
| SESSION-151 (当前) | ComfyUI BFF 动态载荷变异 + 无头渲染后端 | (this push) |
| SESSION-150 | 纯数学驱动动画 + 增强优雅错误边界 | `ebd00bd` |
| SESSION-149 | 动态 demo 网格 + 优雅质量熔断边界 | `c2436e5` |
| SESSION-148 | Windows 终端编码崩溃护盾 + ASCII-safe 救援 UI | `ccc5067` |
| SESSION-147 | 知识总线大一统 + ComfyUI 交互式自愈救援网关 | `0f6da73` |
| SESSION-146-B | 雷达广域探测网 + 深度审计轨迹 | `b9cdf05` |

---

## 10. Handoff Checklist

- [x] 所有新代码遵循 Registry Pattern (`@register_backend`)
- [x] 所有新代码遵循六边形架构 (`validate_config()` 在 Adapter 中)
- [x] 所有新代码遵循不可变蓝图模式（变异前深拷贝）
- [x] 代码库中无硬编码 ComfyUI 节点 ID
- [x] 工作流节点中无本地文件系统路径引用
- [x] 轮询循环有 `time.sleep()` 和超时熔断器
- [x] 所有输出回收至 `outputs/production/`
- [x] 每次渲染批次后释放显存
- [x] 29/29 测试通过
- [x] PROJECT_BRAIN.json 更新至 v0.99.3
- [x] SESSION_HANDOFF.md 更新完整上下文
- [x] 所有变更推送至 GitHub

*Signed off by Manus AI · SESSION-151*
