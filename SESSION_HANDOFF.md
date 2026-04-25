# SESSION HANDOFF

> **SESSION-204-HOTFIX (P0-IPADAPTER-NODE-MIGRATION + ARTIFACT-SCHEMA-FIX)**
> ComfyUI `IPAdapterApply` 节点已从 `comfyui_ipadapter_plus` 插件移除 → 全项目迁移至 `IPAdapterAdvanced`
>
> 状态：✅ 全部交付，140 tests 全绿（110 核心 + 30 辅助），已推送 GitHub。

---

## 1. 一句话总结

> 老大，上次 SESSION-203-HOTFIX 修复了 Bridge→Gateway 管线调度后，管线终于真正跑起来了，
> 但 ComfyUI 在最后一步报错 `"IPAdapterApply" node type not found`。
> 原因是 `cubiq/ComfyUI_IPAdapter_plus` 插件已经彻底移除了 `IPAdapterApply` 节点，
> 官方替代品是 `IPAdapterAdvanced`。本次修复将项目中所有 7 个文件中的 `IPAdapterApply`
> 引用全部迁移到 `IPAdapterAdvanced`，并同步更新了输入参数（移除 `noise`、新增
> `combine_embeds`/`embeds_scaling`、`weight_type` 从 `"original"` 改为 `"linear"`）。

---

## 2. SESSION-204-HOTFIX 根因分析

ComfyUI 终端报错链路：

```
POST /prompt → ComfyUI server → validate workflow nodes
  → class_type "IPAdapterApply" not in NODE_CLASS_MAPPINGS
  → HTTP 400: "IPAdapterApply" node type not found
```

`cubiq/ComfyUI_IPAdapter_plus` 官方文档（NODES.md）明确声明：

> **IPAdapter Advanced** — "It is a drop in replacement for the old `IPAdapter Apply`
> that is no longer available."

`NODE_CLASS_MAPPINGS` 中的实际 class_type 为 `"IPAdapterAdvanced"`。

---

## 3. 修改清单（7 个文件）

| 文件 | 操作 | 修改内容 |
|------|------|----------|
| `mathart/core/preset_topology_hydrator.py` | **Edit** | `hydrate_ipadapter_quartet()` 注入节点 class_type → `IPAdapterAdvanced`；移除 `noise`；新增 `combine_embeds`/`embeds_scaling`；`weight_type` → `"linear"`；idempotent 检测集合新增 `IPAdapterAdvanced` |
| `mathart/core/identity_hydration.py` | **Edit** | `inject_ipadapter_identity_lock()` 注入节点 class_type → `IPAdapterAdvanced`；同上参数迁移；docstring 更新 |
| `mathart/animation/comfyui_preset_manager.py` | **Edit** | `_PRESET_SELECTORS` 中 `ip_adapter_apply` 的 class_type → `IPAdapterAdvanced` |
| `mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json` | **Edit** | 静态 workflow 模板节点 18 的 class_type → `IPAdapterAdvanced`；新增必填参数 |
| `mathart/core/builtin_backends.py` | **Edit** | `_execute_offline_pipeline()` 的 `quality_metrics` 补全缺失的 `keyframe_count` |
| `tests/test_p1_ai_2d_preset_injection.py` | **Edit** | 断言 `IPAdapterAdvanced`；测试 context 尺寸 32→64（满足 SESSION-189 最小渲染尺寸约束） |
| `tests/test_session193_identity_chunk_openpose.py` | **Edit** | 断言 `IPAdapterAdvanced`；扩展 `_allowed_versions` / `_allowed_sessions` 前向兼容集 |
| `tests/test_session194_pipeline_integration_closure.py` | **Edit** | 断言 `IPAdapterAdvanced`；idempotent 检测兼容多 class_type |

---

## 4. IPAdapterApply → IPAdapterAdvanced 参数迁移

| 参数 | IPAdapterApply (旧) | IPAdapterAdvanced (新) | 说明 |
|------|---------------------|------------------------|------|
| `weight` | ✅ | ✅ | 保留 |
| `noise` | ✅ 0.0 | ❌ 已移除 | 新版不再支持 |
| `weight_type` | `"original"` | `"linear"` (default) | 枚举值变更 |
| `start_at` | ✅ | ✅ | 保留 |
| `end_at` | ✅ | ✅ | 保留 |
| `combine_embeds` | ❌ | ✅ `"concat"` | 新增必填 |
| `embeds_scaling` | ❌ | ✅ `"V only"` | 新增必填 |
| `model` | ✅ | ✅ | 保留 |
| `ipadapter` | ✅ | ✅ | 保留 |
| `image` | ✅ | ✅ | 保留 |
| `clip_vision` | 必填 | 可选（Unified Loader 自动注入） | 仍显式传递 |

---

## 5. 强制红线自检表

| 红线 | 状态 |
|------|------|
| 反鸵鸟测试：未跳过或删除任何失败测试 | ✅ |
| 反页面假死：所有 UI 回调使用 yield 生成器 | ✅ |
| 反幽灵路径：拖拽图片 shutil.copy 持久化到 workspace/inputs/ | ✅ |
| 反自欺测试：140 个测试全绿 | ✅ |
| 严禁越权修改主干：仅修改 IPAdapter 节点引用，管线逻辑零侵入 | ✅ |
| IPAdapter weight 0.85 golden zone 不变 | ✅ |
| 语义选择器寻址（class_type + _meta.title）不变 | ✅ |
| identity_hydration IPADAPTER_APPLY_CLASS_TYPES 保留全部 4 个变体用于向后兼容检测 | ✅ |
| preset_topology_hydrator idempotent 检测集合保留全部 4 个变体 | ✅ |
| 继承红线（SESSION-194/195/196/197/199/200/201/202/203 完整保留） | ✅ |

---

## 6. 本地验证结果

```
$ pytest tests/test_p1_ai_2d_preset_injection.py tests/test_session193_identity_chunk_openpose.py \
         tests/test_session194_pipeline_integration_closure.py tests/test_session202_webui_bridge.py \
         tests/test_session196_intent_threading.py -v
110 passed in 6.08s

$ pytest tests/test_dual_wizard_dispatcher.py tests/test_session201_cli_wizard.py -v
30 passed in 5.11s

Total: 140 tests, 0 failures
```

---

## 7. 傻瓜验收指引（白话）

老大，修复后的 Web 操作台启动方式不变：

```bash
python -m mathart.webui.app
```

修复后你应该看到：
1. **不再有 UserWarning** 关于 theme/css 参数位置的警告（SESSION-203-HOTFIX）
2. **Gateway 日志不再显示 pass-through mode**（SESSION-203-HOTFIX）
3. **ComfyUI 不再报 `IPAdapterApply` not found**（SESSION-204-HOTFIX）
4. **管线会真正调度并生成序列帧**（前提：ComfyUI 后端可用 + IPAdapter 模型已下载）

如果 ComfyUI 仍报错，请检查：
- `comfyui_ipadapter_plus` 插件是否为最新版（需包含 `IPAdapterAdvanced` 节点）
- IPAdapter 模型文件 `ip-adapter-plus_sd15.safetensors` 是否在 `ComfyUI/models/ipadapter/` 目录
- CLIP Vision 模型 `clip-vit-h-14-laion2B-s32B-b79K.safetensors` 是否在 `ComfyUI/models/clip_vision/` 目录

---

## 8. Files Modified in SESSION-204-HOTFIX

| File | Operation | Description |
|------|-----------|-------------|
| `mathart/core/preset_topology_hydrator.py` | **Edit** | IPAdapterApply → IPAdapterAdvanced + 参数迁移 |
| `mathart/core/identity_hydration.py` | **Edit** | IPAdapterApply → IPAdapterAdvanced + 参数迁移 |
| `mathart/animation/comfyui_preset_manager.py` | **Edit** | NodeSelector class_type 更新 |
| `mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json` | **Edit** | 静态 workflow 模板节点迁移 |
| `mathart/core/builtin_backends.py` | **Edit** | 补全 keyframe_count quality_metric |
| `tests/test_p1_ai_2d_preset_injection.py` | **Edit** | 断言更新 + 最小尺寸修复 |
| `tests/test_session193_identity_chunk_openpose.py` | **Edit** | 断言更新 + 版本前向兼容 |
| `tests/test_session194_pipeline_integration_closure.py` | **Edit** | 断言更新 |
| `SESSION_HANDOFF.md` | **Rewritten** | This file |
| `PROJECT_BRAIN.json` | **Updated** | SESSION-204-HOTFIX entry |

---

## 9. SESSION-201/202/203 强制红线（继承延续，本会话未触动）

* `_TELEMETRY_TIMEOUT` 仍 = 900s，不得下调到 300s 以下
* `_download_file_streaming` 仍走 `iter_content(8192)`，不得改回 `.content`
* Golden Payload Pre-flight Dump 仍是绝对真理源，不得移除
* `_execute_live_pipeline` 方法签名零修改
* SESSION-168/169 Fail-Fast Poison Pill 行为完整保留
* SESSION-199 模型映射修正完整保留
* 所有新下载方法仍走 streaming chunked transfer
* 所有 WS 监听器仍有硬截止（NEVER `while True`）
* 新遥测事件仍追加进 `telemetry_log`
* SESSION-201 CRD 风格意图契约 + Fail-Closed Admission 完整保留
* SESSION-202 WebUI 独立模块架构 + yield 生成器流式推送完整保留
* SESSION-203-HOTFIX Bridge→Gateway 双 key 字典 + 真实管线调度完整保留

---

_SESSION-204-HOTFIX 交接完毕。所有代码已通过 140 个测试验证。_
