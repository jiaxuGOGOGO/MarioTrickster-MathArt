# RESEARCH_NOTES_SESSION_167.md

> **Task ID**: P0-SESSION-167-COMPOSED-MESH-HYDRATION
> **Date**: 2026-04-23

---

## 1. Per-Frame Slice Hydration (逐帧切片水合)

在程序化动画流水线中，多个动态组件（Shell 躯干、Ribbon 附属物、Attachment 配件）各自拥有独立的时序顶点数据阵列 `[frames, V, 3]`。当这些组件被组合为 Composed Mesh 送入光栅化器时，必须在每一帧时间切片上动态刷新顶点缓存，否则整个序列将渲染同一帧的静态快照。

**核心原则**：

- 每一帧渲染前，必须从多帧顶点缓冲区中显式提取当前帧索引对应的变形顶点。
- 静态组件（Attachment、Ribbon）的顶点在每帧中保持不变，但必须与当前帧的动态组件顶点正确拼接。
- 组合后的时序张量 `[frames, V_total, 3]` 应作为一等公民持久化到磁盘，供下游消费者按需读取。

**工业参考**：

- Vertex Animation Textures (VAT)：将动画预烘焙到纹理中，在顶点着色器中采样——本质上也是逐帧更新顶点位置。
- Blender Mesh Cache Modifier：从外部文件逐帧加载变形网格数据。
- Houdini VAT Export：将 SOP 动画导出为 VAT 纹理，每帧一行像素。

## 2. Render Loop Context Mutability (渲染循环上下文可变性)

离线烘焙大循环中，传递给 Render Backend 的 Context 必须在每帧发生实质性数据变异（Mutation）。如果不显式将 `deformed_vertices[frame_idx]` 覆写给待渲染实体，所有上游动画将被静默截断。这是经典的"stale reference"问题：下游持有的是初始化时的引用或副本。

**核心原则**：

- 在 `for frame_idx in range(frame_count)` 循环内部，必须提取当前帧的 shell deformed vertices，合并组合到 composed_mesh.vertices（in-place），然后传递更新后的 mesh 给渲染器。
- 确保渲染器读取的是最新的顶点数据，而非深拷贝只读副本。
- Data-Oriented Design (DOD) 强调数据的内存布局和访问模式，避免指针追逐（Pointer Chasing）。

**工业参考**：

- Molecular Matters "Adventures in Data-Oriented Design"：mesh 数据应按渲染顺序连续存储。
- GDC Data-Driven Animation Pipelines：上游骨骼变换必须 1:1 流向下游渲染。
- Unity Per-frame Updates：每帧更新必须在渲染调用前完成。
- Autodesk 3ds Max `InvalidateGeomCache()`：强制刷新几何缓存以获取当前帧网格。

## 3. Fail-Loud Validation / VarianceAssertGate (显性失败验证模式)

`VarianceAssertGate` 成功拦截了 `MSE=0.0000` 异常并平滑降级，证明 CI/CD 质量门禁极其优秀。核心任务是专注修复 Implementation 使其 Green，而绝对不允许篡改或移除 Test 门槛。

**核心原则**：

- Quality Gate 是管线中的强制检查点，代码必须满足预定义阈值才能继续。
- Fail-Fast / Fail-Loud：在检测到异常时立即中止，而非静默继续。
- 修复应该针对产生问题的代码（Implementation），而非降低或移除检测标准（Test）。
- MSE < 0.0001 门禁：只要组合网格在循环内被正确同步，光栅化后的像素必然帧间变化。

**工业参考**：

- Jim Gray Fail-Fast 原则：系统应在检测到错误时立即停止。
- SonarQube Quality Gates：自动化质量门禁集成到 CI/CD 管线。
- OPA Policy-as-Code：将质量策略编码为可执行规则。

## 4. NVIDIA GPU Gems 3 Ch.2: Animated Crowd Rendering

NVIDIA GPU Gems 3 第二章详细描述了大规模动画人群渲染的技术方案。核心要点是：每个实例的动画数据必须在每帧独立更新，使用 `SV_InstanceID` 索引常量缓冲区中的骨骼变换矩阵。

**与本项目的关联**：

- pseudo3d_shell_backend 产出的多帧变形顶点 `[frames, V, 3]` 等价于 GPU Gems 中的"per-instance animation data"。
- compose_mesh_stage 的逐帧水合等价于"per-instance data update"——每帧必须用当前帧的变形顶点替换上一帧的数据。
- 如果跳过这一步，就等同于所有实例共享同一个静态 T-Pose，失去所有动画信息。

## 5. Catmull-Rom Spline Interpolation (帧间光滑插值)

Catmull-Rom 样条是 Cardinal 样条的特例（tension=0），经过所有控制点。在本项目的 CPU 离线烘焙路径中，用于对骨骼/顶点轨迹进行光滑插值，生成平滑的动画帧序列。

**核心特性**：

- 使用四个相邻关键帧构成几何矩阵，计算任意参数 t 处的插值值。
- 不需要 GPU，用 NumPy 即可高效计算。
- 输出 Albedo/Normal/Depth 多通道工业贴图。

## 6. SESSION-167 修复总结

### 6.1 根因回顾

SESSION-166 已修复 guide_baking_stage 的 2D SDF 渲染路径中的三个断链点：

| 断链点 | 修复方案 |
|--------|----------|
| Bone→Joint 命名空间断裂 | 构建 `_bone_to_joint` 映射字典，将 Clip2D 的骨骼名翻译为 Skeleton 的关节名 |
| 度→弧度转换缺失 | 对 `rz` 通道应用 `math.radians()` 转换 |
| 根位移未注入 | 将 `frame.root_x/root_y` 注入到 `scale_x/scale_y` 参数 |

### 6.2 SESSION-167 增强

SESSION-167 在 SESSION-166 的基础上，增强了 3D 管线路径中的组合网格节点：

| 增强项 | 说明 |
|--------|------|
| 时序组合顶点张量 | 当检测到 `shell_mesh["vertices"].ndim == 3` 时，逐帧拼接 shell 变形顶点与静态附件/飘带顶点 |
| 持久化存储 | 时序组合网格保存为 `_temporal_composed_mesh.npz` |
| 向下兼容 | 规范静态组合网格（最后一帧）仍然保存为 `_composed_mesh.npz` |
| 报告增强 | 组合报告新增 `has_temporal_data` 和 `temporal_frame_count` 字段 |
| UX 防腐蚀 | 烘焙网关终端打印新增 SESSION-167 组合网格水合状态行 |
