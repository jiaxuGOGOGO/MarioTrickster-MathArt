# Anime Frame Rhythm — 日式作画节奏与潜空间治愈

> 本文档是 **P0-SESSION-189-LATENT-HEALING-AND-ANIME-RHYTHM** 对仓库治理契约（*Design-as-Code*）的知识入库产物。
> 任何后续 Session 在修改 `anti_flicker_runtime.anime_rhythmic_subsample` 或 `AntiFlickerRenderBackend` 外部引导的帧编排前，**必须**先阅读本文档。

---

## 0. 一眼看懂的三条硬锚

| 硬锚 | 数值 | 位置 | 违反后果 |
|---|---|---|---|
| `MAX_FRAMES` | **16** | `anti_flicker_runtime.MAX_FRAMES` | RTX 4070 12GB 段式爆显存；超过 16 帧 AnimateDiff `context_length` 会触发 OOM。 |
| `LATENT_EDGE` | **512** | `anti_flicker_runtime.LATENT_EDGE` | 小于 512 → SD1.5 U-Net 感受野坍塌 → 输出高频彩色噪声马赛克。 |
| `NORMAL_MATTE_RGB` | **(128, 128, 255)** | `anti_flicker_runtime.NORMAL_MATTE_RGB` | 用 `(0,0,0)` 做 alpha=0 垫底等于把"空气"告诉 ControlNet 是"指向摄像机后方的极端切线向量"，整帧光影崩溃。 |

## 1. 「一拍一/一拍二/一拍三」是什么？

引用 iD Tech (2021)：

> - **Animating on 1s** — 24 new drawings per second. Used for action sequences or fast motion that needs detail.
> - **Animating on 2s** — 12 new drawings per second. The most common type of animation.
> - **Animating on 3s** — 8 new drawings per second. Good for slow scenes, **often used in anime**.
> - You can mix these differing timings as needed; you don't have to stick to just one set.

工程翻译：**「混拍」就是非均匀抽帧**。头尾（Anticipation / Hold）画面跨度短 → 等价于 on 2s/3s；中段（Impact）画面跨度长 → 等价于 on 1s。这正是《鬼灭之刃》《JoJo》《咒术回战》战斗场景的招牌观感。

## 2. 「緩急（Kan-Kyu）」与余弦 S 曲线

Richard Williams 在 *Animator's Survival Kit* Disc 12 给出的经典节奏是

```
Anticipation —— Hold —— Impact —— Hold —— Settle
       (慢)    (极慢)   (极快)     (极慢)   (慢)
```

在连续参数化帧序列上，该曲线最干净的数学解是**1 − cos 的半周期映射**：

```
phase ∈ [0, 1]
ease_weight  = 0.5 − 0.5 · cos(π · phase)
source_index = round(ease_weight · (N − 1))
```

其性质：

- `phase=0` → `weight=0`（头端贴紧 0 号帧）；
- `phase=0.5` → `weight=0.5`，**导数最大**（中段跨度最大 = 一拍一）；
- `phase=1` → `weight=1`（尾端贴紧 N−1 号帧）；
- 权重单调非减，保证抽出的索引**严格升序**。

去重回填流程：余弦曲线在两端可能命中同一索引，因此产生 `unique_sorted` 后若 `< target`，在最稀疏 gap 中点补齐。若仍无法回填（例如 N 太小），直接返回 `list(range(N))`。本行为被 `TestAnimeRhythmicSubsample.test_passthrough_when_under_budget` 和 `test_exactly_equal_to_budget` 锁定。

## 3. SD1.5 × AnimateDiff 为什么是 512×512？

- SD 1.5 在 **512×512** 上训练（[HF 模型卡](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5)），U-Net 降采样为 64/32/16/8 四级；
- 当输入像素域 < 512，潜空间 < 64×64，U-Net 最深层 8×8 分支退化为 2×2 → **感受野完全丢失** → 解码器吐噪声（见 [sd-webui-animatediff#178](https://github.com/continue-revolution/sd-webui-animatediff/issues/178)）；
- AnimateDiff 官方教程明确推荐 **CFG 4–5 @ 512×512**（[stable-diffusion-art.com](https://stable-diffusion-art.com/animatediff/)）。

因此：

- `jit_matte_and_upscale` **总是** LANCZOS 上采样到 512×512；
- `force_override_workflow_payload` **总是** 把 `EmptyLatentImage.width/height` 改写为 512，不相信任何上游；
- 所有 `KSampler*.cfg` 天花板钉死在 **4.5**；
- 所有 `ControlNetApply*` / `ACN_SparseCtrl*` 的 `strength` 天花板钉死在 **0.55**，防止辅控模态盖过 prompt。

## 4. 法线紫蓝色 (128, 128, 255) 的官方由来

[lllyasviel/sd-controlnet-normal](https://huggingface.co/lllyasviel/sd-controlnet-normal) 官方示例末行：

```python
image = (image * 127.5 + 127.5).clip(0, 255).astype(np.uint8)
```

即 `[-1, 1]` 的法线切线向量 → 8-bit RGB。零向量 `(0, 0, 1)`（朝向相机）→ `(128, 128, 255)`。

**禁忌**：带 Alpha 的法线图直接进 ComfyUI `LoadImage` 时，Alpha=0 处 RGB 会被当成 `(0, 0, 0)`，对应切线向量 `(-0.577, -0.577, -0.577)`（指向相机后方、朝向地面）——ControlNet 会以为角色周围都是悬崖内壁，光影彻底崩盘。

修复：`jit_matte_and_upscale(matte_rgb=(128,128,255))` 会在纯内存中先把 Alpha 通道作为 mask paste 到紫蓝底板上，再 LANCZOS 上采样。

## 5. 红线 — 什么不能做

1. **不碰代理环境变量**：任何 `HTTP_PROXY / HTTPS_PROXY / NO_PROXY` 的读写都会破坏外部 ComfyUI 连接，`test_module_source_never_touches_proxy_env` 会自动失败。
2. **不用节点 ID 硬编码**：所有对 ComfyUI workflow 的编辑都必须通过 `class_type` 语义扫描，参考 `force_override_workflow_payload` 的实现。
3. **不破坏三条硬锚常量**：`MAX_FRAMES / LATENT_EDGE / NORMAL_MATTE_RGB` 若需调整，必须同步修改 `tests/test_session189_latent_healing_and_anime_rhythm.py` 并在 `SESSION_HANDOFF.md` 以新的 SESSION 条目公告。

## 6. 入口 API 一览

```python
from mathart.core.anti_flicker_runtime import (
    MAX_FRAMES,                      # 16
    LATENT_EDGE,                     # 512
    NORMAL_MATTE_RGB,                # (128, 128, 255)
    anime_rhythmic_subsample,        # int -> list[int]
    jit_matte_and_upscale,           # PIL.Image -> PIL.Image (RGB, 512×512)
    heal_guide_sequences,            # 一站式三通道治愈
    force_override_workflow_payload, # ComfyUI payload 最后防线
)
```

`AntiFlickerRenderBackend.render()` 会在接到 `context.source_frames/normal_maps/depth_maps` 后，**首先**调用 `heal_guide_sequences`，再把处理后的结果推入下游 `assemble_sequence_payload`，后者在返回前再调用一次 `force_override_workflow_payload` 作为"双保险"。整个路径对上游调用者透明、对下游 ComfyUI 也是纯数据层面 patch，不触动任何 IO / 网络 / 模型加载逻辑。

## 7. 外部参考

1. iD Tech — *What does animating on ones, twos, and threes mean* (2021)
2. Richard Williams — *Animator's Survival Kit*, Disc 12 (Anticipation / Hold / Impact)
3. animetudes — *Animation and Subjectivity: Towards a Theory of Framerate Modulation* (2020)
4. HuggingFace `stable-diffusion-v1-5` Model Card — 512×512 训练分辨率
5. stable-diffusion-art.com — *AnimateDiff: Easy text-to-video* (CFG 4–5 推荐)
6. GitHub `continue-revolution/sd-webui-animatediff#178` — 低分辨率坍塌的典型噪声图
7. HuggingFace `lllyasviel/sd-controlnet-normal` — 法线 RGB 编码规范

— anime_frame_rhythm.md 完 —
