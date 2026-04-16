# SESSION-044 审计报告：Gap C1 工业级解析法 SDF 渲染落地

> 作者：Manus AI  
> 审计对象：`MarioTrickster-MathArt`  
> 审计目标：确认 Gap C1“工业级渲染管线缺失（基于 2D SDF 梯度直接生成 normal/depth）”是否已从研究、实现、测试、工件、三层进化接入五个层面形成闭环。[1] [2] [3] [4]

## 一、结论摘要

本轮 SESSION-044 已将 **解析法 SDF 渲染** 从研究结论正式落地为仓库能力。项目现在不需要借助 Blender 或额外 3D 烘焙链路，就可以直接从 **2D Signed Distance Field 的采样网格** 生成 **normal map、depth map 与 mask**，并通过工业渲染接口导出与 albedo 同步的辅助贴图。该能力已经被接入公共 API、工业渲染器、自进化报告与引擎状态面板，同时附带真实 Mario idle 工件与自动化测试覆盖。[1] [2] [3] [4]

下表给出本轮审计的总览。

| 审计维度 | 结果 | 证据 |
|---|---|---|
| 外部研究是否完成 | 是 | `evolution_reports/session044_sdf_rendering_research_notes.md` |
| 解析法 normal/depth 是否已实现 | 是 | `mathart/animation/sdf_aux_maps.py` |
| 工业渲染器是否支持 auxiliary maps | 是 | `render_character_maps_industrial()` |
| 三层进化循环是否纳入追踪 | 是 | `mathart/evolution/evolution_loop.py`, `mathart/evolution/engine.py` |
| 真实工件是否生成 | 是 | `evolution_reports/session044_aux_demo/` |
| 自动化测试是否通过 | 是 | `pytest -q tests/test_sdf_aux_maps.py tests/test_evolution_loop.py tests/test_layer3_closed_loop.py` |
| 待办列表是否已更新 | 是 | `gap_check.md` |

## 二、研究到实现的映射

本轮实现不是凭空新增功能，而是把研究协议中提取出的规则直接转译为工程接口。Inigo Quilez 关于 SDF 法线的核心论点是：**等值面的法线来自标量场梯度的归一化**，中心差分是通用而无方向偏置的计算方式。[1] 这一定义被转化为 `compute_sdf_gradients()`，并作为整个 auxiliary-map 烘焙的底层方向源。Quilez 关于 2D Distance + Gradient Functions 的论点则进一步提示：距离与梯度应该被视作一个共同契约，而不是在渲染末端临时推导出的副产品。[2] 这一思想直接落在 `bake_sdf_auxiliary_maps()` 的 API 设计上：当前项目中的任何 SDF callable 都可以先走中心差分回退路径，未来若某些图元能够原生输出 analytic gradient，也可以无缝接入同一接口。

Scott Lembcke 对 2D 光照技术的梳理说明，若希望 2D 精灵进入更完整的前向或延迟光照路径，单张 albedo 纹理并不够，至少还需要 normal 与 depth-like 的 surface properties。[3] Godot 文档则给出了主流 2D 引擎的消费习惯，即 normal map 以 RGB 编码 XYZ 分量，并可与其他辅助图共同增强 2D 精灵的立体受光。[4] 这两个下游消费视角决定了本轮实现不只是“内部算一组法线”，而是正式导出 **engine-consumable RGBA images**。

| 研究结论 | 工程转译 | 最终落点 |
|---|---|---|
| SDF 梯度就是法线方向源 [1] | 用中心差分从距离场恢复 `df/dx`, `df/dy` | `compute_sdf_gradients()` |
| 距离与梯度应作为统一契约 [2] | 设计可扩展的烘焙 API，而非一次性渲染技巧 | `bake_sdf_auxiliary_maps()` |
| 2D 光照需要 normal/depth-like surface properties [3] | 导出 albedo + normal + depth + mask 的打包接口 | `render_character_maps_industrial()` |
| 主流 2D 引擎以 RGB 编码 normal [4] | 将 normal vectors 编码为 RGBA 图片，alpha 复用 silhouette | `encode_normal_map()` |

## 三、代码级落地审计

### 3.1 新增的核心模块

`mathart/animation/sdf_aux_maps.py` 是本轮新增的核心落点。该模块提供了采样网格、烘焙配置、输出数据结构，以及从 2D SDF 到辅助贴图的完整 NumPy 管线。它完成了以下职责：首先在规则网格上采样 SDF；随后利用 `numpy.gradient` 的中心差分语义在内部区域估计梯度；再使用内部负距离的归一化值构造 depth proxy，并把该 depth 提升到 normal 的 Z 分量中，得到可直接编码成贴图的 pseudo-3D normals。[1] [5]

### 3.2 工业渲染器升级

`mathart/animation/industrial_renderer.py` 已不再只提供单张工业化着色后的 albedo 帧，而是新增 `IndustrialRenderAuxiliaryResult` 与 `render_character_maps_industrial()`。这意味着同一角色姿态、同一 SDF 轮廓现在能够在一次工业渲染流程中同时输出：albedo、normal map、depth map 与 binary mask。与此同时，旧的 `_compute_pseudo_normal()` 也从“基于位置半径的近似”升级为“基于实际距离场梯度与内部深度的近似”，使已有 Dead Cells 风格 cel shading 也真正站到了 SDF field 的方向信息之上。[1] [2]

### 3.3 三层进化循环接入

`mathart/evolution/evolution_loop.py` 已新增 SESSION-044 的 `GAPC1_DISTILLATIONS`，把本轮研究以可验证的 provenance 形式纳入 Layer 2 蒸馏注册表。同时，进化报告结构现在增加了 `AnalyticalRenderingStatus`，用于追踪 auxiliary-map 模块、工业渲染器导出能力、公共 API 暴露状态、研究笔记与测试文件。`mathart/evolution/engine.py` 的状态面板也新增了 **Layer 2.5: Analytical SDF Rendering (SESSION-044)** 区块，使该能力从“代码里存在”提升为“演化系统显式可见”。

下表给出关键文件变更与作用。

| 文件 | 变更性质 | 作用 |
|---|---|---|
| `mathart/animation/sdf_aux_maps.py` | 新增 | 通用 SDF auxiliary-map 烘焙核心模块 |
| `mathart/animation/industrial_renderer.py` | 扩展 | 从工业角色 SDF 直接导出 albedo/normal/depth/mask |
| `mathart/animation/__init__.py` | 扩展 | 对外暴露新的烘焙函数与工业级导出接口 |
| `mathart/evolution/evolution_loop.py` | 扩展 | 注册 SESSION-044 蒸馏记录并追踪 analytical rendering 状态 |
| `mathart/evolution/engine.py` | 扩展 | 在自进化引擎状态面板展示 SESSION-044 集成状态 |
| `tests/test_sdf_aux_maps.py` | 新增 | 覆盖 circle SDF 烘焙与工业角色 auxiliary-map 导出 |
| `scripts/run_session044_sdf_aux_demo.py` | 新增 | 生成真实工件用于审计与交接 |
| `gap_check.md` | 更新 | 将 Gap C1 纳入需求覆盖与后续待办体系 |

## 四、真实工件审计

本轮并非只停留在单元测试，而是实际执行了 `scripts/run_session044_sdf_aux_demo.py`，生成了 Mario idle 的四类工件与元数据文件，位于 `evolution_reports/session044_aux_demo/`。根据生成的 `session044_aux_demo.json`，当前导出为 `32×32`，总参与部件数为 `18`，实际 silhouette 内像素为 `468`，depth 归一化标尺为 `0.3417534259811256`。这说明新管线已经能对真实角色骨架姿态完成稳定采样，而非只对理论圆形 SDF 生效。

| 工件 | 路径 | 说明 |
|---|---|---|
| Albedo | `evolution_reports/session044_aux_demo/mario_idle_albedo.png` | 工业风格主渲染结果 |
| Normal Map | `evolution_reports/session044_aux_demo/mario_idle_normal.png` | 由 SDF 梯度 + 内部深度提升得到 |
| Depth Map | `evolution_reports/session044_aux_demo/mario_idle_depth.png` | 由负距离归一化得到的灰度深度代理 |
| Mask | `evolution_reports/session044_aux_demo/mario_idle_mask.png` | silhouette alpha/coverage |
| 元数据 | `evolution_reports/session044_aux_demo/session044_aux_demo.json` | 记录尺寸、像素数、深度缩放与导出配置 |

## 五、测试与验证

本轮至少完成了三组关键验证。第一组是 `tests/test_sdf_aux_maps.py`，直接验证通用 circle SDF 烘焙路径与工业角色辅助贴图导出路径。第二组是 `tests/test_evolution_loop.py`，确认 SESSION-044 被纳入正式的三层演化报告结构，而不是悬空功能。第三组是 `tests/test_layer3_closed_loop.py`，用于回归确认 Gap 4 已有闭环没有被本轮改动破坏。三组合并执行后得到 **19 passed**、无失败，仅存在一个与 dataclass 收集相关的既有 Pytest 警告，不影响本轮能力落地结论。

> 验证命令：`pytest -q tests/test_sdf_aux_maps.py tests/test_evolution_loop.py tests/test_layer3_closed_loop.py`

| 测试文件 | 结论 | 说明 |
|---|---|---|
| `tests/test_sdf_aux_maps.py` | 通过 | 验证 depth 中心高于边缘、outside alpha 为 0、工业角色 auxiliary bundle 非空 |
| `tests/test_evolution_loop.py` | 通过 | 验证进化报告与蒸馏注册表兼容新增 SESSION-044 状态 |
| `tests/test_layer3_closed_loop.py` | 通过 | 验证 SESSION-043 主动闭环未被本轮破坏 |

## 六、对用户需求的覆盖判定

用户提出的核心要求是：基于 SDF 梯度，以几乎零额外负担直接输出可用于引擎打光的法线图和深度图，并让这项能力进入项目的三层进化与知识蒸馏框架。对照当前仓库状态，这一要求已经形成完整代码级承接。`gap_check.md` 已将该需求登记为第 22 项，并标记为已覆盖。与此同时，本轮实现并未与 SESSION-043 的 Layer 3 主动闭环相冲突，反而通过 `evolution_loop.py` 与 `engine.py` 把渲染能力与已有闭环共同纳入可审计的演化视图。

## 七、剩余待办与下一步建议

尽管 Gap C1 已完成首轮闭环，但仍有三个值得继续推进的方向。第一，当前项目对任意 SDF callable 采用中心差分回退路径，这足以支撑现有角色系统，但若未来核心图元能够原生输出 analytic gradient，则可以进一步减少采样噪声并提升边界稳定性。[2] 第二，depth proxy 目前是围绕内部负距离构造的灰度高度近似，已足够支持 2D lighting、specular 扩展与 deferred-like experiments，但未来可继续加入 specular、roughness 或 engine-specific import metadata，以便更顺滑地接入实际引擎材质系统。[3] [4] 第三，本轮工件仍以单姿态 Mario idle 为主，后续可以把 auxiliary-map 导出批量接入角色 sheet 导出、视觉回归与 Layer 3 调参诊断，让 normal/depth 贴图本身也参与质量评分和自演化。

## References

[1]: https://iquilezles.org/articles/normalsSDF/ "Inigo Quilez — Normals for an SDF"
[2]: https://iquilezles.org/articles/distgradfunctions2d/ "Inigo Quilez — 2D Distance and Gradient Functions"
[3]: https://www.slembcke.net/blog/2DLightingTechniques/ "Scott Lembcke — 2D Lighting Techniques"
[4]: https://docs.godotengine.org/en/latest/tutorials/2d/2d_lights_and_shadows.html "Godot Docs — 2D lights and shadows"
[5]: https://numpy.org/doc/1.25/reference/generated/numpy.gradient.html "NumPy — numpy.gradient"
