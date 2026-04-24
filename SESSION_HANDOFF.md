# SESSION HANDOFF

**Current Session:** SESSION-189
**Date:** 2026-04-24
**Status:** CLOSED
**Priority:** P0
**Previous:** SESSION-188 (Quadruped Physics Awakening & VAT Real-Data Bridge)

---

## 1. 核心目标 (Core Objectives)

- [x] **P0-SESSION-189-LATENT-HEALING-AND-ANIME-RHYTHM**：潜空间治愈 + 日式作画节奏抽帧锁。
- [x] **日式节奏抽帧器 `anime_rhythmic_subsample`**：采用 `0.5 − 0.5·cos(π·phase)` 余弦 S 曲线（緩急 / Kan-Kyu）实现非均匀抽帧，严格 16 帧上限、强升序、去重回填。
- [x] **即时抠图上采样 `jit_matte_and_upscale`**：对 `RGBA`/带透明的法线、深度、Source 帧，在内存中用通道专属底板（Normal `(128, 128, 255)`、Depth `(0, 0, 0)`、Source `(0, 0, 0)`）做 alpha matting，再 LANCZOS 上采样到 512×512。**绝不触碰磁盘原图**。
- [x] **一站式治愈 `heal_guide_sequences`**：对外暴露的单入口函数，先抽帧再逐帧 matte+upscale，三通道同步。
- [x] **ComfyUI 工作流最后防线 `force_override_workflow_payload`**：纯 `class_type` 语义扫描，硬压 `EmptyLatentImage=512`、`KSampler*.cfg ≤ 4.5`、`ControlNetApply*/ACN_SparseCtrl*.strength ≤ 0.55`、`VHS_VideoCombine.frame_rate`，**零节点 ID 硬编码**。
- [x] **`AntiFlickerRenderBackend` 集成**：在 external guide bypass 点调用 `heal_guide_sequences`，并打印科幻级 UX 横幅；报告写入 `context["_session189_healing_report"]`。
- [x] **`ComfyUIPresetManager.assemble_sequence_payload` 集成**：在返回 payload 前调用 `force_override_workflow_payload`，override 报告写入 `mathart_lock_manifest["session189_override_report"]`。
- [x] **外网参考研究落地**：iD Tech / Richard Williams / animetudes / SD-v1-5 Model Card / stable-diffusion-art.com / sd-webui-animatediff#178 / lllyasviel sd-controlnet-normal。笔记见 `docs/RESEARCH_NOTES_SESSION_189.md`。
- [x] **知识入库**：`knowledge/anime_frame_rhythm.md` 上线。
- [x] **DaC 文档契约**：USER_GUIDE.md 新增 Section 19、SESSION_HANDOFF.md 覆写为本文档、PROJECT_BRAIN.json 追加 SESSION-189 条目。
- [x] **测试验收**：新增 `tests/test_session189_latent_healing_and_anime_rhythm.py` — **28 / 28 全部 PASS**。

## 2. 大白话汇报：老大，潜空间治愈已生效，十六帧日式节奏抽帧锁已闭合！

### 🌸 余弦 S 曲线 · 日式緩急（Kan-Kyu）抽帧

老大，系统再也不会把 40 帧物理动作硬塞进 AnimateDiff 了。`anime_rhythmic_subsample(total, max_frames=16)` 用一条 `1 − cos` 的半周期曲线把头尾慢、中间快的日式作画节奏直接编码进索引选择：头几帧几乎贴着 0 号帧（Anticipation Hold 极慢），中段跳 2~3 帧（Impact 一拍一），尾段再收回 Hold。这意味着同样的 16 帧预算，观众感受到的是"有蓄力、有爆发、有余韵"的日式分镜，而不是平均抽帧的僵尸步态。

### 🟣 紫蓝底板 · 切线空间零向量垫底

老大，法线图再也不会被 ControlNet 误读了。`lllyasviel/sd-controlnet-normal` 官方规定 RGB `(128, 128, 255)` 才是切线空间的零向量（朝向相机），而带 Alpha 的 PNG 只要 alpha=0 区域的 RGB 被默认填 `(0, 0, 0)`，ControlNet 会以为角色周围都是朝向地面的悬崖内壁——整帧光影崩盘。`jit_matte_and_upscale` 在**纯内存**里把 alpha 当 mask paste 到紫蓝底板上，再 LANCZOS 上采样到 512×512，本地硬盘原图一根头发都不动。

### 🛡️ ComfyUI 最后防线 · 零节点 ID 硬编码

老大，assemble_sequence_payload 的语义选择器已经做了 90 分的工作，但 SESSION-189 再加了 10 分的"死不回头"保险：payload 组装完之后，我们用 `class_type` 在整张 workflow 里再扫一遍——`EmptyLatentImage` 全部改写为 512×512、`batch_size=min(frame_count, 16)`；任何 `KSampler*` 的 `cfg` > 4.5 一律压回 4.5；任何 `ControlNetApply*` / `ACN_SparseCtrl*` 的 `strength`/`motion_strength` > 0.55 一律压回 0.55；`VHS_VideoCombine.frame_rate` 按实际 `frame_rate` 修正。全程不看节点 ID，靠 `class_type` 认亲，预设模板以后随便改 node id 也不会破。

### 🎬 UX 工业级科幻横幅

进入外部引导旁路的瞬间，终端会亮起：

```
╔══ [ANTI_FLICKER_RENDER] SESSION-189 LATENT HEALING ACTIVE ══╗
║ Kan-Kyu rhythmic subsample : 40 → 16 (max=16)
║ Canvas forced to 512×512 (SD1.5 U-Net floor)
║ Normal matte : (128,128,255) • Depth matte : (0,0,0)
╠══ indices : [0, 2, 4, 6, 10, 13, 17, 19, 22, 26, 29, 31, 33, 35, 37, 39]
╚══ All guide channels upscaled via LANCZOS. ════
```

## 3. 本次修改的全部文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mathart/core/anti_flicker_runtime.py` | **修改** | 追加 `MAX_FRAMES` / `LATENT_EDGE` / `NORMAL_MATTE_RGB` / `anime_rhythmic_subsample` / `jit_matte_and_upscale` / `heal_guide_sequences` / `force_override_workflow_payload`，并扩展 `__all__`。 |
| `mathart/core/builtin_backends.py` | **修改** | 在 `AntiFlickerRenderBackend` external guide bypass 点接入 `heal_guide_sequences`，新增科幻 UX banner、治愈报告回填 `_session189_healing_report`。 |
| `mathart/animation/comfyui_preset_manager.py` | **修改** | 在 `assemble_sequence_payload` 返回前调用 `force_override_workflow_payload` 做最后防线覆写，报告回填 `lock_manifest["session189_override_report"]`。 |
| `tests/test_session189_latent_healing_and_anime_rhythm.py` | **新增** | SESSION-189 专属 pytest 套件：28 tests，覆盖抽帧单调性 / 长度 / 去重、matte 通道正确性、端到端治愈、workflow 语义覆写、代理环境变量红线自检。 |
| `knowledge/anime_frame_rhythm.md` | **新增** | 日式作画节奏 × 潜空间治愈知识入库，作为后续 Session 修改前的必读文档。 |
| `docs/RESEARCH_NOTES_SESSION_189.md` | **新增** | 外网七大参考的逐条引用与工程映射。 |
| `docs/USER_GUIDE.md` | **修改** | 追加 Section 19 「潜空间治愈 + 日式作画节奏抽帧锁」完整使用指南。 |
| `SESSION_HANDOFF.md` | **覆写** | 本文档。 |
| `PROJECT_BRAIN.json` | **修改** | 新增 SESSION-189 条目与 `latent_healing_contract` 字段。 |
| `SESSION_TRACKER.md` | **修改** | 追加 SESSION-189 日志行。 |

## 4. 严格红线遵守情况

| 红线 | 证据 |
|------|------|
| 严禁触碰代理环境变量 (`HTTP_PROXY/HTTPS_PROXY/NO_PROXY`) | `test_module_source_never_touches_proxy_env` 直接 grep 源码断言；新加代码全部零引用。 |
| 严禁节点 ID 硬编码 | `force_override_workflow_payload` 仅按 `class_type` 前缀匹配（`KSampler` / `EmptyLatent` / `ControlNetApply` / `ACN_SparseCtrl` / `VHS_VideoCombine`）。 |
| 严禁触碰本地硬盘上的原始烘焙图 | `jit_matte_and_upscale` 全程走 `PIL.Image.new` + `Image.alpha_composite` + `resize`，结果留在内存 `Image` 对象；`external_normal/depth` 列表原地替换，不写文件。 |
| 严禁破坏既有 SESSION-175/178 约束 | CFG `min(cfg_scale, 4.5)` 保留；Normal/Depth 紫蓝 & 纯黑垫底从 SESSION-178 的 Exporter 层上移到 SESSION-189 的 Runtime 层，两层防御互不冲突。 |
| 不重复建设 | 保留 `controlnet_bridge_exporters._NEUTRAL_NORMAL`、`NormalMapExporter` 等既有实现；本次新增的是"外部 guide 传入时才触发的" JIT 治愈层，与磁盘 Exporter 正交互补。 |

## 5. 外网参考研究落地情况

| 参考 | 工程映射 |
|------|---------|
| iD Tech (2021) *Ones/Twos/Threes* | `anime_rhythmic_subsample` 的非均匀间隔 |
| Richard Williams *Animator's Survival Kit* Disc 12 | 余弦 S 曲线参数化 Anticipation / Impact / Settle |
| animetudes (2020) *Framerate Modulation* | 在注释与知识文档中给出美学理论引用 |
| HuggingFace `stable-diffusion-v1-5` Model Card | `LATENT_EDGE = 512` 硬锚依据 |
| stable-diffusion-art.com *AnimateDiff* | `CFG ≤ 4.5` 硬锚依据 |
| sd-webui-animatediff#178 | "低分辨率潜空间 → 彩色噪声" 的典型 bug 佐证 |
| `lllyasviel/sd-controlnet-normal` | `NORMAL_MATTE_RGB = (128, 128, 255)` 硬锚依据 |

## 6. 傻瓜验收指引

```bash
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt
pip install -e .
PYTHONPATH=. python3 -m pytest tests/test_session189_latent_healing_and_anime_rhythm.py -v
# 预期：28 passed in ≈1.2s
```

功能性 smoke：

```python
from PIL import Image
from mathart.core.anti_flicker_runtime import heal_guide_sequences, LATENT_EDGE

frames = [Image.new("RGBA", (192, 192), (255, 255, 255, 0))] * 40
out = heal_guide_sequences(source_frames=frames, normal_maps=frames, depth_maps=frames)
assert len(out["source_frames"]) == 16
assert out["normal_maps"][0].size == (LATENT_EDGE, LATENT_EDGE)
assert out["normal_maps"][0].getpixel((0, 0)) == (128, 128, 255)   # 紫蓝底
assert out["depth_maps"][0].getpixel((0, 0)) == (0, 0, 0)          # 纯黑底
print(out["report"]["selected_indices"])                             # 非均匀索引
```

## 7. 下一步建议 (Next Session Recommendations)

1. 把 `session189_healing_report` / `session189_override_report` 写进 `ArtifactManifest`，让资产大管家（SESSION-174）的"真理查账"视图可以直接展示本次治愈发生了什么。
2. 给 `CreatorIntentSpec` 增加 `rhythm_profile: "kan_kyu" | "linear" | "on_threes"` 字段，让导演可以在 intent.yaml 中切换不同的日式节奏曲线。
3. 探索对 `mask_maps` 通道同样做 matte+upscale（当前只做尺寸对齐与被动抽帧，尚未做底板处理），以便将来做 rim light / silhouette ControlNet。

---

**执行者**: Manus AI (SESSION-189)
**研究笔记**: `docs/RESEARCH_NOTES_SESSION_189.md`
**知识入库**: `knowledge/anime_frame_rhythm.md`
**前序 SESSION**: SESSION-188 (Quadruped Physics Awakening & VAT Real-Data Bridge)
