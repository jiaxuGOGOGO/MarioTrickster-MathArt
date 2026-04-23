# SESSION-166 外部研究笔记 (RESEARCH_NOTES_SESSION_166.md)

> **Task ID**: P0-SESSION-166-RENDER-LOOP-HYDRATION
> **Date**: 2026-04-23

---

## 1. Per-Frame State Hydration (逐帧状态水合)

在离线图形渲染管线（Offline Rendering Pipeline）中，当上游物理求解器输出包含多帧形变的 Vertex Buffer Array（如 `[frames, vertices, channels]`）时，渲染器（Rasterizer）在循环渲染时必须对该数组进行精确的时序切片抽取，并强制更新给相机上下文，否则会引发全序列静态死锁。

**核心原则**：
- 每一帧渲染前，必须从多帧顶点缓冲区中显式提取当前帧索引对应的变形顶点
- 渲染上下文（Camera Context / Render Context）必须在每次迭代中被强制刷新
- 如果渲染器在循环外只绑定了一次静态网格引用，则整个序列将渲染同一帧的静态快照

**工业参考**：
- Vulkan/Metal 多帧飞行缓冲区（Multiple Frames in Flight）：每帧使用独立的 Vertex Buffer 避免 GPU 竞争
- Blender Mesh Cache Modifier：从外部文件逐帧加载变形网格数据
- Three.js Animation Loop：每帧更新场景状态后再调用 renderer.render()
- Autodesk 3ds Max `InvalidateGeomCache()`：强制刷新几何缓存以获取当前帧网格

## 2. Data-Oriented Design (DOD) & Pointers (面向数据设计与指针引用)

排查闭包或上下文中，是否在循环外提前评估了静态的 Mesh 对象，导致循环内无论 `frame_idx` 怎么推进，送入后端贴图渲染的始终是同一个未更新缓存的静态副本。

**核心原则**：
- DOD 强调数据的内存布局和访问模式，避免指针追逐（Pointer Chasing）
- 在动画渲染循环中，如果 mesh 对象是在循环外创建的引用，循环内的修改可能不会反映到渲染调用中
- 必须确保每次循环迭代都使用当前帧的数据副本，而非共享的可变引用

**工业参考**：
- Molecular Matters "Adventures in DOD"：mesh 数据应按渲染顺序连续存储
- GDC Data-Driven Animation Pipelines：上游骨骼变换必须 1:1 流向下游渲染
- Unity Per-frame Updates：每帧更新必须在渲染调用前完成

## 3. Fail-Loud Validation Success (显性失败验证模式)

`VarianceAssertGate` 成功拦截了 `MSE=0.0000` 异常并平滑降级，证明 CI/CD 质量门禁极其优秀。核心任务是专注修复 Implementation 使其 Green，而绝对不允许篡改或移除 Test 门槛。

**核心原则**：
- Quality Gate 是管线中的强制检查点，代码必须满足预定义阈值才能继续
- Fail-Fast / Fail-Loud：在检测到异常时立即中止，而非静默继续
- 修复应该针对产生问题的代码（Implementation），而非降低或移除检测标准（Test）
- SonarQube Quality Gate 模式：新代码必须通过质量门禁，旧问题仍在 UI 中报告但不阻塞

**工业参考**：
- Jim Gray Fail-Fast 原则：系统应在检测到错误时立即停止
- SonarQube Quality Gates：自动化质量门禁集成到 CI/CD 管线
- OPA Policy-as-Code：将质量策略编码为可执行规则

## 4. 案发现场分析

### 4.1 日志证据

```
[pseudo_3d_shell] Deformed 352 vertices x 46 frames...
```

这证明上游物理引擎（pseudo_3d_shell_backend）已成功计算出多帧变形顶点。

```
[SESSION-160 VarianceAssertGate] Channel 'source': frame pair (0, 1) has MSE=0.00000000
```

这证明下游渲染器输出的帧与帧之间完全相同（MSE=0），即渲染循环没有正确使用逐帧变形数据。

### 4.2 根因定位

`_bake_true_motion_guide_sequence` 函数（mass_production.py:391-583）的渲染循环中：
- 函数接收 `clip_2d`（Clip2D 骨骼动画数据）而非 pseudo3d_shell 的变形顶点
- 函数使用 `render_character_maps_industrial(skeleton, pose, style, ...)` 进行渲染
- 渲染器是基于 skeleton+pose 的 2D 渲染器，而非基于 3D 变形网格的光栅化器
- 关键问题：`pose` 是从 `_animation_func_from_clip(t)` 正确提取的逐帧姿态
- 但如果 `render_character_maps_industrial` 内部未正确应用 pose 到骨骼变换，或者 skeleton 对象的状态未被正确更新，则会导致静态输出

### 4.3 修复方向

需要确保：
1. `skeleton.apply_pose(pose)` 在每帧渲染前被正确调用
2. 渲染器接收的是已应用当前帧姿态的骨骼状态
3. 如果 industrial_renderer 失败回退到 character_renderer，同样需要确保逐帧姿态应用
