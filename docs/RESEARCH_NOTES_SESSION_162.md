# SESSION-162 外网研究锚点（落盘备查）

> 本笔记为 SESSION-162「全链路硬编码动作铲除 + RenderContext 强契约 + Fail-Fast 视觉静止断言」工业改造的外网理论锚点。
> 所有引用均来源于公开发表的工业级文档/论文/工程博客，按 Docs-as-Code 红线落盘到仓库，确保任何下游审稿者都能在不联网的情况下复核理论依据。

---

## 1. 数据驱动 / 注册表替代硬编码字符串列表

**外部参考**：Tom Looman, *Why you should be using GameplayTags in Unreal Engine*（[tomlooman.com](https://tomlooman.com/unreal-engine-gameplaytags-data-driven-design/), 2026-03）

**核心结论**：
- 在中大型项目中，使用裸 `String` / `enum` 维护"动作集 / 状态集"是已知反模式，会导致拼写错误、跨模块漂移、审稿不可追踪。
- 工业最佳做法是把"可枚举集合"统一到一个**有层级的中央注册表**（GameplayTag Manager）中，由消费方按需查询；新增动作只在注册表落地一次，所有调用点零改动。
- Lyra Sample Game 的 `GameplayMessageRouter` 进一步把"按 Tag 广播事件"做成跨模块解耦的标准范式，与本项目 `MotionStateLaneRegistry` + `ActionRegistry` 的 IoC 哲学完全同源。

**对本项目的指导**：
- 任何残留的 `["idle", "walk", "run", ...]` 硬编码列表必须铲除，统一从 `mathart.animation.unified_gait_blender.get_motion_lane_registry().names()` 单一真理源获取。
- 红线测试 `tests/test_no_hardcoded_motion_states.py` 永久守门，确保后续任何 PR 不能再引入裸字符串列表。

## 2. RenderContext 强契约 / 时序参数显式穿透

**外部参考**：DigitalRune Graphics Documentation, *Render Context*（[digitalrune.github.io](http://digitalrune.github.io/DigitalRune-Documentation/html/ba13b3e9-cf11-4a8d-959d-338de0a4aa81.htm)）

**核心结论**：
- 工业级渲染管线把"每个 Render 调用所需的全部数据"封装为 `RenderContext` 对象**显式传参**，而不是依赖隐式全局/默认值。
- 文档明确建议："**If you have a piece of information and there is a render context property for it, then update the render context.**"——任何可命名的上下文字段都必须落到 RenderContext，禁止退化为函数局部默认值。
- DirectX 12 / Vulkan 的 PSO（Pipeline State Object）哲学同源：把状态显式编码到对象里，让管线在编译期/调用期就能 Fail-Fast，避免下游"不知道我用的是什么状态"的不确定性。

**对本项目的指导**：
- `_bake_true_motion_guide_sequence()` 必须把 `motion_state` / `fps` / `character_id` 作为**位置参数**显式接收，并在烘焙报告 JSON 里完整落盘，禁止使用 `motion_state="idle"` 之类的默认值兜底。
- `prepare_character → guide_baking_stage → ai_render_stage` 全链路 RenderContext 字段必须 100% 一致，由 `tests/test_render_context_wiring.py` 强契约守门。

## 3. Fail-Fast 帧差 MSE 静止自爆断言

**外部参考**：Isaac Berrios, *Introduction to Motion Detection: Part 1 - Frame Differencing*（[medium.com](https://medium.com/@itberrios6/introduction-to-motion-detection-part-1-e031b0bb9bb2), 2023-10-30）；CircleCI, *Regression Testing with CI*（2026-03）

**核心结论**：
- 帧差（Frame Differencing）是视频监控/动画 QA 业内的标准运动检测方法：相邻帧逐像素相减后取均值/方差，方差为 0 即**画面绝对静止**。
- CI 流水线的视觉回归红线哲学：**任何静止帧、任何冻结序列**都必须在 GPU 渲染前被拦截，避免下游浪费算力产出"会动的死人"。
- 工业实践中常用 **MSE 地板 + 比率断路器** 双线并行：地板线（任意单帧对 MSE < 阈值即终止）守"任何静止"，比率线（>50% 帧对 MSE < 阈值即终止）守"大面积静止"。

**对本项目的指导**：
- `mathart.core.anti_flicker_runtime.assert_nonzero_temporal_variance()` 已在 SESSION-160 部署 MSE 地板线（绝对地板 0.0001）。
- SESSION-162 进一步把该断言**前置**到 `_bake_true_motion_guide_sequence()` 出口，让纯 CPU 烘焙阶段就能拦截静止序列，避免任何静止帧浪费下游 ComfyUI GPU 算力。
- 由 `tests/test_variance_assert_gate.py` 用 6 帧全等图像做反向回归，确保断言任何时候都会爆。

---

## 引用清单（可离线复核）

1. Tom Looman, "Why you should be using GameplayTags in Unreal Engine", https://tomlooman.com/unreal-engine-gameplaytags-data-driven-design/
2. Epic Games, "Data Driven Gameplay Elements in Unreal Engine", https://dev.epicgames.com/documentation/unreal-engine/data-driven-gameplay-elements-in-unreal-engine
3. DigitalRune, "Render Context", http://digitalrune.github.io/DigitalRune-Documentation/html/ba13b3e9-cf11-4a8d-959d-338de0a4aa81.htm
4. Microsoft, "Managing Graphics Pipeline State in Direct3D 12", https://learn.microsoft.com/en-us/windows/win32/direct3d12/managing-graphics-pipeline-state-in-direct3d-12
5. NVIDIA, "Advanced API Performance: Pipeline State Objects", https://developer.nvidia.com/blog/advanced-api-performance-pipeline-state-objects/
6. Isaac Berrios, "Introduction to Motion Detection: Part 1 - Frame Differencing", https://medium.com/@itberrios6/introduction-to-motion-detection-part-1-e031b0bb9bb2
7. CircleCI, "Regression Testing and How to Automate It with CI", https://circleci.com/blog/regression-testing-and-how-to-automate-it-with-ci/
