# SESSION-176: 动态潜空间对齐与多模态控制网垫底研究笔记

**Author:** Manus AI
**Date:** 2026-04-23

## 1. 动态潜空间时序对齐 (Dynamic Latent Batch Alignment)

在基于 AnimateDiff 的视频生成管线中，潜空间画布（`EmptyLatentImage`）的 `batch_size` 必须与输入的控制帧序列长度严格对齐。

根据 AnimateDiff Evolved 作者 Kosinkadink 的说明，AnimateDiff 默认训练于 16 帧（2秒，8fps）的视频片段 [1]。如果输入的控制帧数（如 SparseCtrl 接收的图像序列）与 `EmptyLatentImage` 的 `batch_size` 不匹配，会导致严重的生成错误：
- **截断与碎片化**：如果 `batch_size` 锁定为 16，而物理引擎输入了 40 帧的动作序列，生成过程将在第 16 帧被无情截断，导致动作残缺。
- **画面烧焦 (Deep Fried)**：如果帧数远少于 16 帧，模型权重无法处理，会导致画面出现高饱和度的彩色噪声（Deep Fried Artifacts）[1]。
- **时序闪烁 (Flashing)**：在长序列生成中，如果 SparseCtrl 的控制强度过高或时段未受限，会导致生成的帧之间出现亮度闪烁和色彩漂移（如偏向橙色调）[2]。

**架构决策**：
必须在 Payload 组装阶段，动态读取物理引擎传入的帧数（`len(source_frames)`），并强制覆写 `EmptyLatentImage` 的 `batch_size` 参数。同时，为了防止长序列过拟合，需要将主控制网 SparseCtrl 的强度（`strength`）限制在 0.8，并将法线/深度控制网的强度降至 0.45。

## 2. 控制网图集底色标准化 (ControlNet Matting & Modal Routing)

ComfyUI 的图像处理节点（包括 ControlNet 预处理器和应用节点）通常不支持带有 Alpha 透明通道的图像。如果直接输入带透明背景的 PNG 图像，Alpha 通道会被丢弃，导致透明区域被默认填充为黑色，这在法线贴图（Normal Map）中是致命的错误 [3]。

在切线空间（Tangent Space）法线贴图中，RGB 颜色编码了表面法线的方向。一个完全平坦、朝向摄像机的表面（即没有法线扰动），其法线向量为 `(0, 0, 1)`。将其映射到 `[0, 255]` 的 RGB 空间时，计算公式为 `(N + 1) * 127.5`，结果为 `(128, 128, 255)`，即一种特定的紫蓝色 [4]。

如果法线贴图的透明背景被错误地填充为黑色 `(0, 0, 0)`，这在切线空间中代表一个极度倾斜的法线方向，会导致 ControlNet 产生极其错误的光影推断，彻底破坏 3A 级立体质感。

**架构决策**：
必须在内存中（`BytesIO`）对上传的图像进行底色标准化（Matting），绝对不修改物理硬盘上的原图（Immutable Source Data Principle）：
- **Normal Maps**：使用 `PIL.Image.new('RGB', (512, 512), (128, 128, 255))` 创建紫蓝色底图，然后将带 Alpha 通道的原图 `paste` 上去。
- **Depth Maps & Source Frames**：使用纯黑 `(0, 0, 0)` 创建底图，然后将原图 `paste` 上去。

## 3. 下载环异常硬击穿 (Download Loop Poison Pill)

在处理与 ComfyUI 服务器的 WebSocket/HTTP 通信时，网络不稳定或远端 OOM 宕机会引发 `urllib.error.URLError` 或 `ConnectionResetError`（如 Windows 下的 `WinError 10054` 或 `10061`）。

如果这些致命异常被底层的轮询循环（Polling Loop）通过宽泛的 `try...except` 捕获并仅作为 `logger.warning` 记录，系统将陷入死等状态（Deadlock），导致上层的 PDG 调度器无法感知任务失败，整个并发池被阻塞。

Python 的 `concurrent.futures` 模块在 3.9 版本引入了 `cancel_futures` 参数，允许在 `Executor.shutdown()` 时取消所有尚未开始的挂起任务 [5]。结合 Poison Pill 模式，当检测到远端宕机时，必须立即抛出致命异常，触发全局级联撤销。

**架构决策**：
在 `comfyui_ws_client.py` 的下载逻辑中，捕获到 `10054`/`10061` 等连接重置异常时，必须显式 `raise ComfyUIExecutionError`，击穿轮询循环，触发全局并发任务撤销。

## References

[1] Kosinkadink. (2023). Question: Why "batch size" affects sampler results? #174. GitHub. https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved/issues/174
[2] aihopper. (2024). SparseCtrl-RGB, flashing in generated frames #476. GitHub. https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved/issues/476
[3] Polycount Community. (2017). How to convert this world normal to a flat tangent normal? https://polycount.com/discussion/193559/how-to-convert-this-world-normal-to-a-flat-tangent-normal
[4] lllyasviel. (2023). sd-controlnet-normal. Hugging Face. https://huggingface.co/lllyasviel/sd-controlnet-normal
[5] Python Software Foundation. (2020). Issue 39349: Add "cancel_futures" parameter to concurrent.futures.Executor.shutdown(). https://bugs.python.org/issue39349
