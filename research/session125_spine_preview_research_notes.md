# SESSION-125 外网对齐研究：Spine JSON 张量化 FK 预览器

## 研究目标

本轮研究的目标，是为 **P2-SPINE-PREVIEW-1** 提供一套可以直接落地到仓库中的实现约束：新的预览器必须脱离 Spine 编辑器与任何 GUI 依赖，只读取现有导出的 **Spine JSON** 文件，在 Python 侧完成骨骼层级的正向运动学解算，并以无头方式导出 `.mp4` / `.gif` 预览素材，用于快速检查骨骼断裂、父子拓扑错误、旋转翻折和时序异常。

## 研究结论总表

| 研究主题 | 外部来源 | 直接落地约束 |
|---|---|---|
| Spine 骨骼层级与世界变换 | Esoteric Software Spine Runtime Skeletons [1] | 必须先解析 setup pose 与 parent-child 层级，再按父先子后的顺序计算 world transform；预览器不能只看局部旋转数值。 |
| FK 形式化表达 | UCSD CSE169 Skeletons 课程讲义 [2] | 必须把每个骨骼拆分为局部矩阵构造与世界矩阵串接两个阶段；`world = parent_world @ local` 是核心数学主线。 |
| 张量批量矩阵乘法 | NumPy `matmul` 官方文档 [3] | 只要把矩阵整理为 `[..., 3, 3]`，就可以对全部帧执行广播矩阵乘法，避免逐帧逐骨的 Python 标量热路径。 |
| 无头视频导出 | OpenCV `VideoWriter` 官方文档 [4] | 视频导出必须走 `VideoWriter` 或等价无头 writer，不允许依赖任何窗口显示 API。 |
| 无 GUI 渲染后端 | Matplotlib 后端文档 [5] | 非交互后端是标准的无 GUI 方案，因此本项目的预览实现也必须坚持 headless-only，不可引入阻塞式 GUI 调用。 |

## 关键外部依据

Spine 官方运行时文档强调，骨骼系统的核心不是单独的局部通道，而是**由层级关系导出的世界变换**。这意味着预览器若想正确显示骨架姿态，就必须先解析骨骼树，再把局部变换逐级累计为世界空间结果，而不是简单地把每根骨的旋转值直接画成线段 [1]。

> “A skeleton has a hierarchy of bones. Bone transforms are applied relative to the parent bone and combined to produce world transforms used for rendering.” [1]

CSE169 的骨骼动画讲义把这个问题形式化得更清楚：每个关节先有自己的局部变换矩阵，而整条骨架的姿态来自父矩阵与子局部矩阵的连续乘积。对于本项目，这直接约束了解算器结构：**局部矩阵构造** 与 **世界矩阵传播** 必须分离，便于后续做张量化和调试诊断 [2]。

> “Each joint has a local transform, and global transforms are computed by concatenating transforms along the hierarchy.” [2]

NumPy 官方 `matmul` 文档进一步给出了性能路径。文档明确指出，当输入是高维数组时，最后两个维度会被视为矩阵，其余维度按广播规则批量处理。因此，只要把 `local_matrices` 与 `parent_world_matrices` 统一整理成 `[..., 3, 3]` 的齐次仿射张量，就可以一次性求解全部帧的 FK，而不必在 Python 中写 `for frame in ...` 再 `for bone in ...` 的双重标量循环 [3]。

在视频输出方面，OpenCV 文档说明 `VideoWriter` 的职责就是把逐帧图像流编码为视频文件；这是一条与 GUI 完全解耦的无头输出路径 [4]。Matplotlib 关于后端的说明则从反面印证了这一点：Agg 之类的非交互后端专门用于文件渲染而非屏幕显示，因此本项目的新预览器也必须坚持无窗口、无阻塞、可在 CI 与服务器环境中运行的设计 [5]。

## 已锁定的实现约束

| 编号 | 约束内容 | 在仓库中的具体实现含义 |
|---|---|---|
| C1 | 预览器只读取已落盘的 Spine JSON | 纯算法模块必须以文件路径为输入，不绑定编辑器、Unity、浏览器或交互式运行时。 |
| C2 | FK 必须基于父先子后的拓扑顺序 | 需要在载入 JSON 后先计算 `parent_indices`、拓扑序和深度层，禁止依赖“输入 bones 顺序永远正确”的隐式假设。 |
| C3 | FK 热路径必须张量化 | 允许按骨骼做元数据准备循环，但世界矩阵传播必须使用批量 `np.matmul`；禁止逐帧逐骨标量求解。 |
| C4 | 输出矩阵统一为 2D 齐次仿射格式 | 局部矩阵和世界矩阵都统一为 `[F, B, 3, 3]`，便于后续渲染、诊断和网络序列化。 |
| C5 | 必须支持 Translate / Rotate / Scale 插值 | 预览器不能只读离散关键帧；必须采样到完整帧序列，才能正确导出连续视频。 |
| C6 | 渲染必须无头 | 禁止 `cv2.imshow()`、`cv2.waitKey()` 或任何交互式窗口依赖；导出只允许写文件。 |
| C7 | 屏幕坐标必须显式翻转 Y | 图像坐标系 Y 轴向下增长，若不翻转，骨架会与数学坐标方向相反而倒置显示。 |
| C8 | 后端必须可在 CI 合成自愈 | 当上游没有提供真实 Spine JSON 时，后端要能合成一个最小 demo clip，从而通过动态注册表烟雾测试。 |

## 对后续网络实时版的启示

如果后续继续推进 **P2-REALTIME-COMM-1**，本轮研究已经说明一个关键事实：实时链路最适合传输的不是逐像素帧，而是**按帧采样后的骨骼世界空间数据包**。因为 FK 数学结构已经被压缩成 `[F, B, 3, 3]` 或 `[F, B, C]` 的规则张量，所以后续无论走 WebSocket、UDP 还是共享内存，都应优先序列化以下字段：`frame_index`、`timestamp`、`bone_name / bone_index`、`origin_xy`、`tip_xy`、`rotation_deg`、`parent_index`。这样可以显著降低带宽占用，并把显示层与解算层解耦。

## 研究结论

综合上述资料，可以确认：**用 Spine JSON → 局部矩阵张量 → 深度分层 world matrix 广播乘法 → 无头线段渲染 → MP4/GIF 输出** 的路径，是完全符合外部技术依据、且与当前仓库架构兼容的实现方案 [1] [2] [3] [4] [5]。因此，SESSION-125 的工程落地将以这一约束集为准，不再走任何 GUI 预览或逐帧逐骨标量求解的低效路线。

## References

[1]: http://esotericsoftware.com/spine-runtime-skeletons "Esoteric Software — Spine Runtimes Guide: Skeletons"
[2]: https://cseweb.ucsd.edu/classes/sp16/cse169-a/readings/2-Skeleton.html "UCSD CSE169 — Chapter 2: Skeletons"
[3]: https://numpy.org/doc/2.2/reference/generated/numpy.matmul.html "NumPy — numpy.matmul"
[4]: https://docs.opencv.org/4.x/dd/d9e/classcv_1_1VideoWriter.html "OpenCV — cv::VideoWriter"
[5]: https://matplotlib.org/stable/users/explain/figure/backends.html "Matplotlib — Backends"
