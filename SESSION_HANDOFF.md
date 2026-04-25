# SESSION HANDOFF

> **SESSION-205-HOTFIX (P0-RUNTIME-MODEL-RESOLUTION + REFERENCE-IMAGE-UPLOAD)**
> ComfyUI `POST /prompt` 验证失败：硬编码模型名不匹配 + 参考图未上传 → 全链路运行时动态解析 + 优雅降级
>
> 状态：✅ 全部修复，已推送 GitHub。等待老大亲测反馈。

---

## 1. 一句话总结

> 老大，上次 SESSION-204-HOTFIX 修复了 `IPAdapterApply → IPAdapterAdvanced` 节点迁移后，
> 管线终于能正确组装 payload 了，但 ComfyUI 在 `POST /prompt` 验证阶段报了 **4 个致命错误**：
>
> 1. `CLIPVisionLoader` — 请求 `clip-vit-h-14-laion2B-s32B-b79K.safetensors`，服务器只有 `clip_vision_h.safetensors`
> 2. `IPAdapterModelLoader` — 请求 `ip-adapter-plus_sd15.safetensors`，服务器 ipadapter 目录为空
> 3. `ControlNetLoader` — 请求 `control_v11p_sd15_openpose.pth`，服务器没有 OpenPose 模型
> 4. `LoadImage` — `ref_a5a01a98.png` 只存在于项目本地，从未通过 `/upload/image` API 上传到 ComfyUI
>
> 本次修复新增 **运行时模型解析器** (`comfyui_model_resolver.py`)，在 payload 提交前：
> - 通过 `/object_info` API 查询 ComfyUI 真实模型清单
> - 模糊匹配解析正确的模型文件名
> - 模型不存在时优雅剥离整条节点链（而非崩溃）
> - 通过 `/upload/image` API 上传参考图到 ComfyUI

---

## 2. SESSION-205-HOTFIX 根因分析

ComfyUI 终端报错链路（4 个独立失败点）：

```
POST /prompt → ComfyUI server → validate workflow nodes
  → CLIPVisionLoader.clip_name: "clip-vit-h-14-laion2B-s32B-b79K.safetensors"
    not in [clip_vision_h.safetensors]                    ← 文件名不匹配
  → IPAdapterModelLoader.ipadapter_file: "ip-adapter-plus_sd15.safetensors"
    not in []                                              ← 模型目录为空
  → ControlNetLoader.control_net_name: "control_v11p_sd15_openpose.pth"
    not in [depth.pth, normalbae.pth, sparsectrl.ckpt]   ← 无 OpenPose 模型
  → LoadImage.image: "ref_a5a01a98.png"
    not in ComfyUI/input/                                  ← 从未上传
  → HTTP 400: value_not_in_list (多节点)
```

**根本原因**：项目在 `preset_topology_hydrator.py` 和 `identity_hydration.py` 中硬编码了模型文件名，
但用户的 ComfyUI 安装中模型文件名不同或缺失。同时，参考图仅通过 `os.path.basename()` 注入文件名，
从未通过 ComfyUI 的 `/upload/image` API 上传到服务器的 `input/` 目录。

---

## 3. 修改清单

| 文件 | 操作 | 修改内容 |
|------|------|----------|
| `mathart/core/comfyui_model_resolver.py` | **新增** | 运行时模型解析器：`/object_info` 查询 + 模糊匹配 + `/upload/image` 上传 |
| `mathart/core/builtin_backends.py` | **Edit** | SESSION-205 块：在 payload 提交前动态解析模型名、剥离不可用节点链、上传参考图 |
| `mathart/workspace/preflight_radar.py` | **Edit** | `REQUIRED_COMFYUI_ASSETS` 新增 IPAdapter/CLIP Vision 目录检查 |
| `SESSION_HANDOFF.md` | **Rewritten** | This file |
| `PROJECT_BRAIN.json` | **Updated** | SESSION-205-HOTFIX entry |

---

## 4. 运行时模型解析策略

| 节点类型 | 硬编码值 | 解析策略 | 降级行为 |
|----------|----------|----------|----------|
| `CLIPVisionLoader` | `clip-vit-h-14-laion2B-s32B-b79K.safetensors` | `/object_info/CLIPVisionLoader` → `clip_name` 枚举 → 模糊匹配 | 匹配失败 → 剥离整条 IPAdapter 链 |
| `IPAdapterModelLoader` | `ip-adapter-plus_sd15.safetensors` | `/object_info/IPAdapterModelLoader` → `ipadapter_file` 枚举 → 模糊匹配 | 匹配失败 → 剥离整条 IPAdapter 链 |
| `ControlNetLoader` (OpenPose) | `control_v11p_sd15_openpose.pth` | `/object_info/ControlNetLoader` → `control_net_name` 枚举 → 模糊匹配 | 匹配失败 → 剥离整条 OpenPose 链 |
| `LoadImage` (参考图) | `ref_xxx.png` (basename) | `POST /upload/image` 上传到 ComfyUI `input/` | 上传失败 → 警告日志 |

**模糊匹配优先级**：
1. 精确匹配（大小写不敏感）
2. 词干包含（`clip_vision_h` ⊂ `clip-vit-h-14-laion2B-s32B-b79K`）
3. Token 重叠评分（至少 2 个共同 token）

**节点链剥离**：
- IPAdapter 链剥离时，自动将 KSampler 的 `model` 输入重新接回 IPAdapter 上游的模型源（如 AnimateDiffLoader）
- OpenPose 链剥离时，自动将下游 conditioning 输入重新接回 OpenPose 上游的 Depth ControlNet Apply 输出

---

## 5. 强制红线自检表

| 红线 | 状态 |
|------|------|
| 反鸵鸟测试：未跳过或删除任何失败测试 | ✅ |
| 反页面假死：所有 UI 回调使用 yield 生成器 | ✅ |
| 反幽灵路径：参考图通过 `/upload/image` API 上传到 ComfyUI | ✅ |
| 严禁越权修改主干：仅在 payload 提交前做运行时解析，管线逻辑零侵入 | ✅ |
| IPAdapter weight 0.85 golden zone 不变 | ✅ |
| 语义选择器寻址（class_type + _meta.title）不变 | ✅ |
| `_execute_live_pipeline` 方法签名零修改 | ✅ |
| SESSION-205 块位于 SESSION-194/195/197 之后、SESSION-200 Golden Dump 之前 | ✅ |
| 继承红线（SESSION-194/195/196/197/199/200/201/202/203/204 完整保留） | ✅ |

---

## 6. 傻瓜验收指引（白话）

老大，修复后的 Web 操作台启动方式不变：

```bash
python -m mathart.webui.app
```

修复后你应该看到：
1. **不再有 `value_not_in_list` 错误** — 模型名会自动解析为你 ComfyUI 上实际存在的文件
2. **参考图会自动上传到 ComfyUI** — 不再报 `ref_xxx.png` 找不到
3. **如果某个模型确实没装**（如 OpenPose ControlNet），管线会**优雅降级**而非崩溃 — 自动剥离该节点链，用剩余的 Normal+Depth 双 ControlNet 继续渲染
4. **日志会清晰显示** `SESSION-205 CLIPVision resolved: xxx → yyy` 或 `SESSION-205 Stripped N IPAdapter nodes`

如果渲染仍有问题，请检查：
- ComfyUI 是否正常运行并可访问
- `models/clip_vision/` 目录是否至少有一个 CLIP Vision 模型文件
- 如需 IPAdapter 功能，需下载 IPAdapter 模型到 `models/ipadapter/` 目录

---

## 7. SESSION-201/202/203/204 强制红线（继承延续，本会话未触动）

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
* SESSION-204-HOTFIX IPAdapterApply → IPAdapterAdvanced 迁移完整保留

---
