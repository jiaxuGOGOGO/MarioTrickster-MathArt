# SESSION HANDOFF

> **SESSION-206 (P0-REFUSE-TO-DEGRADE + TYPE-SAFE-MODEL-RESOLUTION)**
> 效果图严重失真（抽象对称色块）根因：模型缺失时"优雅降级"剥离了 IPAdapter/OpenPose 全链路 + 模糊匹配跨类型误解析
>
> 状态：✅ 全部修复，已推送 GitHub。等待老大亲测反馈。

---

## 1. 一句话总结

> 老大，上次 SESSION-205-HOTFIX 的"优雅降级"策略是**灾难性错误** —
> 当 IPAdapter 模型缺失时，代码把整条 IPAdapter 链剥离了，
> 当 OpenPose 模型缺失时，模糊匹配把 `openpose` 错误解析为 `normalbae`。
> 结果 AI 渲染器完全没有角色身份锚点，也没有正确的骨骼姿态控制，
> 产出的就是你看到的那些抽象对称色块。
>
> 本次修复核心原则：**拒绝降级，缺什么直接提示，只接受最标准的生产。**

---

## 2. SESSION-206 根因分析

从你的效果图和运行日志，定位到 **3 个致命问题**：

### 问题 1：IPAdapter 模型缺失 → 整条链被错误剥离

```
ComfyUI/models/ipadapter/ → 目录为空
SESSION-205 → "优雅降级" → 剥离 CLIPVisionLoader + IPAdapterModelLoader + IPAdapterAdvanced + LoadImage
→ AI 渲染器完全没有角色身份锚点
→ 产出抽象对称色块
```

### 问题 2：OpenPose 模糊匹配跨类型误解析

```
请求: control_v11p_sd15_openpose.pth
可用: [control_v11p_sd15_normalbae.pth, control_v11f1p_sd15_depth.pth, ...]
_fuzzy_match() → token 重叠 {control, v11p, sd15} ≥ 2 → 返回 normalbae ✗
→ OpenPose 节点加载了 NormalBae 模型
→ 骨骼姿态控制完全丢失
```

### 问题 3：KSampler model 接线错误

```
IPAdapter 链被剥离时，KSampler.model 被重新接回 IPAdapter 的上游
但 IPAdapter 的 model 输入指向 CheckpointLoader（不是 AnimateDiffLoader）
→ KSampler 跳过了 AnimateDiff
→ 时序一致性也丢失
```

---

## 3. 修改清单

| 文件 | 操作 | 修改内容 |
|------|------|----------|
| `mathart/core/comfyui_model_resolver.py` | **Edit** | 新增 `_detect_controlnet_type()` + `_CONTROLNET_TYPE_KEYWORDS` 类型安全字典；`_fuzzy_match()` 新增 `require_same_controlnet_type` 参数；`resolve_controlnet_model()` 启用类型安全匹配 |
| `mathart/core/builtin_backends.py` | **Rewrite** | SESSION-205 块完全重写：删除所有"优雅降级/剥离/重接"逻辑，替换为严格模型预检 + `PipelineIntegrityError` 硬性中断 + 明确缺失模型提示和下载链接 |
| `SESSION_HANDOFF.md` | **Rewritten** | This file |
| `PROJECT_BRAIN.json` | **Updated** | SESSION-206 entry |

---

## 4. SESSION-206 修复策略

### 4.1 拒绝降级 — 硬性预检关卡

| 节点类型 | 预检行为 | 缺失时行为 |
|----------|----------|------------|
| `CLIPVisionLoader` | `/object_info` 查询 → 模糊匹配 | **PipelineIntegrityError** + 提示下载链接 |
| `IPAdapterModelLoader` | `/object_info` 查询 → 模糊匹配 | **PipelineIntegrityError** + 提示下载链接 |
| `ControlNetLoader` (OpenPose) | `/object_info` 查询 → **类型安全**模糊匹配 | **PipelineIntegrityError** + 提示下载链接 |
| `LoadImage` (参考图) | 磁盘查找 → `/upload/image` 上传 | **PipelineIntegrityError** + 提示配置路径 |

**所有缺失模型一次性收集后统一报错**，用户可以看到完整的缺失清单和下载指引。

### 4.2 类型安全模糊匹配

`_fuzzy_match()` 新增 `require_same_controlnet_type=True` 参数：
- 解析请求文件名的 ControlNet 类型（openpose/normalbae/depth/canny/...）
- 候选文件名的类型必须与请求类型一致
- 类型不一致的候选即使 token 重叠度高也会被拒绝
- 防止 `openpose → normalbae` 这类跨类型灾难性误解析

### 4.3 已有指标零修改

SESSION-189/190/193 等已调好的参数**完全不动**：
- ControlNet 强度上限 0.55 不变
- Depth/Normal arbitration 0.45 不变
- OpenPose arbitration 1.0 不变
- IPAdapter weight 0.85 不变
- KSampler CFG 上限 4.5 不变
- denoise 1.0 不变

---

## 5. 强制红线自检表

| 红线 | 状态 |
|------|------|
| 拒绝降级：缺模型直接 PipelineIntegrityError | ✅ |
| 类型安全：ControlNet 模糊匹配拒绝跨类型 | ✅ |
| 已有指标零修改：SESSION-189/190/193 参数不动 | ✅ |
| 反鸵鸟测试：未跳过或删除任何失败测试 | ✅ |
| 反幽灵路径：参考图通过 `/upload/image` API 上传 | ✅ |
| `_execute_live_pipeline` 方法签名零修改 | ✅ |
| SESSION-205 块位于 SESSION-194/195/197 之后、SESSION-200 之前 | ✅ |
| 继承红线（SESSION-194~205 完整保留） | ✅ |

---

## 6. 傻瓜验收指引（白话）

老大，修复后的行为变化：

1. **如果模型缺失**，管线会**立即停止**并打印红色错误信息，告诉你：
   - 缺了哪个模型
   - 应该放到哪个目录
   - 从哪里下载

2. **你需要安装以下模型**（如果还没装的话）：
   ```
   ComfyUI/models/clip_vision/clip-vit-h-14-laion2B-s32B-b79K.safetensors
   → https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors

   ComfyUI/models/ipadapter/ip-adapter-plus_sd15.safetensors
   → https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter-plus_sd15.safetensors

   ComfyUI/models/controlnet/control_v11p_sd15_openpose.pth
   → https://huggingface.co/lllyasviel/ControlNet-v1-1/resolve/main/control_v11p_sd15_openpose.pth
   ```

3. **模型全部就位后**，管线会正常运行，所有参数保持原有设定。

4. **不再有"优雅降级"** — 要么全部模型就位产出标准质量，要么直接报错。

---

## 7. 继承红线（本会话未触动）

* `_TELEMETRY_TIMEOUT` 仍 = 900s
* `_download_file_streaming` 仍走 `iter_content(8192)`
* Golden Payload Pre-flight Dump 仍是绝对真理源
* `_execute_live_pipeline` 方法签名零修改
* SESSION-168/169 Fail-Fast Poison Pill 行为完整保留
* SESSION-199 模型映射修正完整保留
* SESSION-201 CRD 风格意图契约 + Fail-Closed Admission 完整保留
* SESSION-202 WebUI 独立模块架构 + yield 生成器流式推送完整保留
* SESSION-203-HOTFIX Bridge→Gateway 双 key 字典 + 真实管线调度完整保留
* SESSION-204-HOTFIX IPAdapterApply → IPAdapterAdvanced 迁移完整保留
* SESSION-205 运行时模型解析器核心逻辑保留，降级策略替换为硬性拒绝

---
