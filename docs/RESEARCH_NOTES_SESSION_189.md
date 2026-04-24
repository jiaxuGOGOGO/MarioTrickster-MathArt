# SESSION-189 外网参考研究笔记 — 潜空间治愈 + 日式作画节奏抽帧

> 本文档是 SESSION-189（P0-SESSION-189-LATENT-HEALING-AND-ANIME-RHYTHM）落地前的外网高阶研究对齐。本次补丁的三条架构锚点**全部**溯源自业界共识与官方文档，绝非凭空设计。

---

## 一、日式作画节奏与非均匀抽帧（Anime-Style Rhythmic Subsampling）

### 1.1 一拍一 / 一拍二 / 一拍三（Animating on 1s / 2s / 3s）

参考：[iD Tech — What does animating on ones, twos, and threes mean (2021)](https://www.idtech.com/blog/what-does-animating-on-ones-twos-and-threes-mean)

> The default timing of 2D animation is 24 frames per second. Frames can be *held*, so the same drawing would be shown for multiple frames, rather than having to make a new drawing for every single frame.
>
> - **Animating on 1s** — 24 new drawings per second. Used for action sequences or fast motion that needs detail.
> - **Animating on 2s** — 12 new drawings per second. The most common type of animation.
> - **Animating on 3s** — 8 new drawings per second. Good for slow scenes, **often used in anime**.
>
> You can mix these differing timings as needed in your animation; you don’t have to stick to just one set.

**工程映射**：当源物理帧 `N > 16` 需压缩至 `MAX_FRAMES = 16` 时，我们不采用均匀的 `np.linspace(0, N-1, 16)`，而是对关键的「蓄力（Anticipation）—爆发（Impact）—硬直（Hold）」三阶段施加非线性的 Ease-In / Ease-Out 曲线：头尾密集（Hold），中间跨度大（Impact 一拍一），契合“緩急 / Kan-Kyu”的日式节奏。帧率在 `VHS_VideoCombine` 节点固定为 8（on 3s）或 10（在 on 2s 与 on 3s 之间折中），以保留日式作画观感。

### 1.2 Hold / Impact / Anticipation（Richard Williams *Animator's Survival Kit*）

Williams 在 *Animator's Survival Kit* 的 Disc 12 明确阐述：「Hold」指同一张画保持多帧（stretched time），使视觉停顿出极致张力；紧接一张「Impact frame」做一拍一爆发，再回到 Hold。这正是日式剧场版在战斗/变身/绝招等场面的标配节奏——**帧数少、张力大**。

### 1.3 Framerate Modulation 理论

参考：animetudes — *Animation and Subjectivity: Towards a Theory of Framerate Modulation (2020)* — 作者指出同一段动画可以 **在不同瞬间切换不同的「拍子」**，这种 framerate modulation 不是硬件限制，而是「个体动画师对主观时间的雕塑」。

**因此**，我们在 `anime_rhythmic_subsample()` 中使用：

```
normalized_phase = i / (MAX_FRAMES - 1)            # 0..1
ease_weight      = 0.5 - 0.5 * cos(pi * phase)     # S-curve: 头尾慢、中间快
source_index     = int(round(ease_weight * (N - 1)))
```

- 头尾（Anticipation / Hold）索引密集（`Δindex` 小）→ 一拍二、一拍三；
- 中段（Impact）索引跨度大（`Δindex` 最大）→ 近似一拍一；
- 再通过 `sorted(set(indices))` 去重后若不足 16 帧，则在“最稀疏区间”插帧回填，保证批量严格为 `MAX_FRAMES` 或 ≤ `MAX_FRAMES`。

---

## 二、SD1.5 / AnimateDiff 潜空间分辨率坍塌

### 2.1 512×512 是 SD 1.5 的训练分辨率

参考：[stable-diffusion-v1-5 · Hugging Face](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) — SD 1.5 在 **512×512** 上训练，U-Net 卷积块的 64/32/16/8 四级降采样严格假定输入潜空间为 64×64（即像素域 512×512）。

### 2.2 AnimateDiff 官方推荐

参考：[AnimateDiff: Easy text-to-video — stable-diffusion-art.com](https://stable-diffusion-art.com/animatediff/)

> - **Resolution**: 512×512 (default, well-supported)
> - **CFG scale**: 4–5 (recommended range for quality and stability)
> - Attempting to generate at resolutions **smaller than 512** (e.g., 480×480) may cause failures due to model constraints.

### 2.3 低分辨率产生的「高频彩色噪声马赛克」

参考：[continue-revolution/sd-webui-animatediff#178 *Weird noisy results*](https://github.com/continue-revolution/sd-webui-animatediff/issues/178) — 复现了一张 **极具标志性的彩色噪声图**，这就是潜空间感受野崩溃的典型外观。原因链：输入尺寸 < 512 → 潜空间尺寸 < 64×64 → U-Net 最深层 8×8 分支退化为 2×2 或更小 → 感受野完全丧失 → 解码器直接吐出高频噪声。

**工程映射**：

1. 所有送入 ComfyUI 的 source/normal/depth 图像，**必须在纯内存中**先通过 `PIL.Image.Resampling.LANCZOS` 上采样到 **512×512**，再转 Base64 / 写入 VHS 序列目录；
2. 落地 workflow JSON 后，遍历所有节点按 `class_type == "EmptyLatentImage"` 定位并 **强制覆写** `inputs["width"] = inputs["height"] = 512`（防止预设被上游改坏）；
3. `class_type == "KSampler"` 的 `inputs["cfg"]` 强制 `min(cfg, 4.5)`，避免 CFG 过高烧焦；
4. 所有 `ControlNetApplyAdvanced / SparseCtrl*` 节点的 `strength` 压制为 `0.55`，防止辅控模态抢戏。

---

## 三、ControlNet 法线图域规范（Alpha Matting）

### 3.1 法线图 RGB 编码规范

参考：[lllyasviel/sd-controlnet-normal — Hugging Face](https://huggingface.co/lllyasviel/sd-controlnet-normal)

官方示例代码的关键行：

```python
image = (image * 127.5 + 127.5).clip(0, 255).astype(np.uint8)
```

即 `normal ∈ [-1, 1] → RGB ∈ [0, 255]`，中性朝向 `(0, 0, 1)` 对应像素 `(128, 128, 255)`，这就是**紫蓝色中性底板**的理论来源。

### 3.2 透明背景 = 黑 = 极度扭曲的切线向量

若带 Alpha 的法线图直接喂入 ControlNet（例如 ComfyUI `LoadImage` 对 RGBA 的默认行为是将 Alpha=0 处 RGB 当作 `(0, 0, 0)`），像素 `(0, 0, 0)` 在法线切线空间对应的向量是 `(-1, -1, -1)` 归一化后 ≈ `(-0.577, -0.577, -0.577)`，这是一个**指向摄像机后方、朝向地面**的极度错误向量，会让 ControlNet 误以为角色周围整圈都是「悬崖内壁」，光影彻底崩溃。

### 3.3 工程映射

- `normal_maps` 序列：内存中新建 `Image.new('RGB', (512, 512), (128, 128, 255))` 紫蓝底板，使用原图的 Alpha 通道作为 mask 进行 `paste`；
- `depth_maps` & `source_frames`：用纯黑 `(0, 0, 0)` 底板 paste（Depth 远景 = 黑，RGB 透明 = 黑是安全默认）；
- 所有垫底操作完成后，再统一 `resize((512, 512), LANCZOS)`。
- 现存代码 `controlnet_bridge_exporters.py` 中 `_NEUTRAL_NORMAL = [0, 0, 1]` 已符合规范，本次新增的是**纯内存 JIT 环节**的 alpha matting + LANCZOS 上采样，补齐推流前的最后一公里。

---

## 四、强制红线复述与合规性

1. **网络环境绝对静默** — 本次补丁零行代码涉及 `HTTP_PROXY / HTTPS_PROXY / NO_PROXY`，也不调用 `os.environ.pop` / `os.environ.update` 对上述变量操作。
2. **防硬编码** — 所有 `EmptyLatentImage / KSampler / VideoCombine / ControlNetApplyAdvanced` 的节点定位一律使用 `for node in payload.values(): if node.get("class_type") == ...`，严禁 `payload["5"]` 这类数字 ID 硬编码。
3. **显存安全** — `MAX_FRAMES = 16` 写死在常量；`batch_size` 动态覆写为 `min(len(sampled_indices), MAX_FRAMES)`。

---

## 五、外部参考清单

| 参考 | 用途 |
|---|---|
| iD Tech *Ones/Twos/Threes* (2021) | 一拍一/二/三定义与帧率映射 |
| Richard Williams *Animator's Survival Kit* Disc 12 | Anticipation / Hold / Impact 节奏 |
| animetudes (2020) *Framerate Modulation Theory* | 非均匀抽帧的美学正当性 |
| HuggingFace `stable-diffusion-v1-5` Model Card | SD 1.5 训练分辨率 512 |
| stable-diffusion-art.com *AnimateDiff* | CFG 4–5、分辨率 512 推荐 |
| GitHub `sd-webui-animatediff#178` | 低分辨率潜空间坍塌典型噪声图 |
| HuggingFace `lllyasviel/sd-controlnet-normal` | 法线 RGB 编码 `(128, 128, 255)` |

— SESSION-189 研究笔记完 —
