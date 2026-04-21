| Field | Value |
|---|---|
| Session | `SESSION-109` |
| Focus | `P1-ARCH-6` 富拓扑感知关卡语义底座：基于张量的 `LevelTopologyBackend` + `TopologyExtractor`，把 WFC 离散网格升维成 Recast/Townscaper 风格的 SemanticAnchor + TraversalLane + TopologyTensors |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `17 PASS / 0 FAIL`（`pytest -q tests/test_level_topology.py`，含 16 线程并行压测）；`level_topology` 顺利通过 `tests/test_ci_backend_schemas.py` 注册表/Schema 审计，未引入新失败 |
| Additional Audit | 已完成 anti-OOM（无 Python 嵌套 `for` 遍历网格，全部走 SciPy `convolve2d` + `ndimage.label`）/ anti-hardcoded-tiles（数据导向 `is_solid` / `is_empty` 掩膜，永不依赖 tile 名称）/ anti-data-silo（frozen dataclass + ArtifactManifest schema 强约束）三条红线审计 |
| Primary Files | `mathart/level/topology_types.py`, `mathart/level/topology_extractor.py`, `mathart/core/level_topology_backend.py`, `mathart/core/backend_types.py`, `mathart/core/artifact_schema.py`, `mathart/core/backend_registry.py`, `tests/test_level_topology.py`, `P1-ARCH-6_TOPOLOGY_DESIGN.md`, `PROJECT_BRAIN.json` |

## Executive Summary

本轮的工程目标，是把上游 `WFCTilemapBackend` 已经稳定输出的离散 tile 网格，**继续向上提升到一个具备拓扑语义的强类型表面层**。在此之前，关卡描述只能告诉下游"这一格是 SOLID/AIR"，下游因此只能通过临时字符串或 `dict` 对地形进行二次解释；这种数据形态既不满足后续 `P1-ARCH-5` OpenUSD 适配的 prim/relationship 需求，也无法承载 AI 导航、装饰锚点放置、刚体碰撞边界等下游消费者的 contract 接口。`SESSION-109` 通过引入一个完全 Data-Oriented、纯卷积驱动的 `TopologyExtractor`，配合自注册 `LevelTopologyBackend`，把这一层语义补齐为可序列化、可在 PDG v2 总线上调度、并被 `validate_artifact` 严格审计的 `LEVEL_TOPOLOGY` 制品族。

最终落地结果是：核心库实现了五阶段提取流水线（数据导向掩膜构建 → SciPy 二维卷积模式匹配 → 向量化表面法线场 → `scipy.ndimage` 联通分量标号 → frozen dataclass 聚合产物），把任意 `np.ndarray` tile 网格在 O(1) 卷积复杂度下抽取为 `SemanticAnchor` / `TraversalLane` / `TopologyTensors` 三类不可变制品。`LevelTopologyBackend` 通过 `@register_backend(BackendType.LEVEL_TOPOLOGY)` 自注册到全局注册表，沿用 `Physics3DBackend` 那一套 ports-and-adapters 纪律：仅从 `context` 读取声明的输入键（`logical_grid` / `wfc_tilemap_manifest` / `tilemap_json`），仅向声明的输出目录写出 `topology_json` + `tensors_npz` 两份产物，并产出一个被 `ArtifactFamily.LEVEL_TOPOLOGY` 严格 schema 校验过的 `ArtifactManifest`。这条总线和 `MicrokernelPipelineBridge.run_backend(...)` 完全打通，下游不再需要对 backend 名做任何硬编码。

## Research Alignment Audit

| Reference | Requested Principle | `SESSION-109` Concrete Closure |
|---|---|---|
| Recast / Detour 算法基准 | 体素 → 轮廓 → 可走表面三段式提取 | `TopologyExtractor` 在 `is_solid` 体素场上做 walkable surface 检测（"格子为实心 AND 上方为空气"），并以 4-邻接 `ndimage.label` 抽出独立 traversal lane，沿用 Recast 的连通可达表面思想 |
| Oskar Stålberg / Townscaper 双网格 | Marching Squares 模式匹配出凸/凹角与表面法线 | 通过五个 `convolve2d` 卷积核（floor/ceiling/wall_left/wall_right + 两个 3×3 角点核）一次性识别六类锚点；表面法线由四个一阶差分卷积合成单位向量，与 Marching Squares 邻接判别同源 |
| SideFX Houdini VEX / SOPs 几何属性 | 点/面上绑定强类型语义属性 | `SemanticAnchor` 以 frozen dataclass 携带 `position / normal / up vector / anchor_type / metadata`，并提供 `transform_matrix()` 直接落到 4×4 实例化矩阵，对接未来 OpenUSD `xformOp` |
| Data-Oriented Design + SciPy | 严禁 Python 嵌套循环遍历网格 | 整个流水线在网格上零 `for` 循环：分类、法线、连通分量、统计全部走 NumPy/SciPy 矢量算子；只有最终把每条 lane 的标量统计打包成 dataclass 时才有线性遍历 lane 数（远小于网格总格子数） |
| Pixar OpenUSD `usdchecker` | Schema 必须独立可校验 | `ArtifactFamily.LEVEL_TOPOLOGY` 在 `FAMILY_SCHEMAS` 中固化了 `required_outputs=("topology_json", "tensors_npz")` 与 7 个 `required_metadata` 字段，被 `validate_artifact` / `validate_artifact_strict` 端到端拦截 |
| Bazel hermetic actions | 后端只读声明输入、只写声明目录 | `LevelTopologyBackend.execute()` 严格只从 `context` 取键、严格只向 `output_dir` 落盘；同时支持来自 CI Minimal Context Fixture 的占位字符串自动回退到合成 8×8 测试网格，确保审计闭环不依赖外部生成物 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/level/topology_types.py` | **NEW**。`SemanticAnchor` / `TraversalLane` / `TopologyTensors` / `TopologyExtractionResult` 四个 frozen dataclass + `KNOWN_ANCHOR_TYPES` / `KNOWN_SURFACE_KINDS` 词汇表 | 把 `LEVEL_TOPOLOGY` 制品族的强类型契约从注释下放为可被构造期校验的代码合约，杜绝任何 `dict` / `tuple` 黑盒 |
| `mathart/level/topology_extractor.py` | **NEW**。`TopologyExtractor` 五阶段提取流水线 | 把 `np.ndarray` tile 网格在 O(1) 卷积复杂度下提升为 frozen 拓扑制品；包含降级路径处理空网格 / 全实心网格 |
| `mathart/core/level_topology_backend.py` | **NEW**。`LevelTopologyBackend` + `@register_backend(BackendType.LEVEL_TOPOLOGY)` | 把核心库以纯插件形式挂上 PDG v2 总线；上游可通过 `logical_grid` / `wfc_tilemap_manifest` / `tilemap_json` 三种声明式输入触发 |
| `mathart/core/backend_types.py` | 新增 `BackendType.LEVEL_TOPOLOGY` 与 4 个别名（`topology_extractor` / `level_topology_extractor` / `recast_topology` / `dual_grid_topology`） | 把新 backend 名固化进规范化别名表，杜绝下游再用临时字符串 |
| `mathart/core/artifact_schema.py` | 新增 `ArtifactFamily.LEVEL_TOPOLOGY` + `FAMILY_SCHEMAS` 条目 + `required_metadata_keys` 7 字段约束 | 让 `validate_artifact` 在第一时间拒收任何缺字段的 manifest |
| `mathart/core/backend_registry.py` | 在 `get_registry()` 中追加 `mathart.core.level_topology_backend` 的自动 `import_module` | 让 backend 与现有 31+ 内置 backend 同样在第一次取注册表时被自动发现 |
| `tests/test_level_topology.py` | **NEW**。3 层、17 个测试 | 包含 5 个 frozen dataclass 契约测试、7 个算法测试（含 256×256 时间预算与单位法线断言）、4 个 backend×bridge 集成测试、1 个 16 线程 / 32 批 / 200×200 ThreadPoolExecutor 压测 |
| `P1-ARCH-6_TOPOLOGY_DESIGN.md` | **NEW**。设计文档 | 记录算法选型、数据契约、红线对策与对接 P1-ARCH-5 的接口预留 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 17/17 测试全绿 | `pytest -q tests/test_level_topology.py` → `17 passed` |
| 卷积流水线满足时间预算 | `TestTopologyExtractor::test_extract_no_python_for_loop_overhead` 在 256×256 随机网格上 < 1s |
| 16 线程并行压测不破红线 | `TestTopologyParallelPressure::test_parallel_extraction_within_budget` 在 32 批 200×200 网格、16 worker 池下严格 < 30s |
| Backend 在 PDG v2 总线上可被发现并产出合法 manifest | `TestLevelTopologyBackend::test_backend_produces_valid_manifest_via_bridge` 走 `MicrokernelPipelineBridge.run_backend` 全链路、`validate_artifact` 返回空错误列表 |
| 上游 WFC manifest / 直传 JSON 路径均能 hermetic 解析 | `TestLevelTopologyBackend::test_backend_can_read_upstream_wfc_manifest` 验证 manifest.metadata.source 中携带溯源信息 |
| Frozen dataclass 拒绝任何未知锚点/表面词汇 | `TestFrozenContracts::test_semantic_anchor_rejects_unknown_type` / `test_traversal_lane_rejects_unknown_kind` |
| 全局 CI guard 不被本次改动破坏 | `pytest tests/test_ci_backend_schemas.py` 中 `level_topology` 不在任何失败列表，剩余失败均为 ComfyUI 环境性问题 |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 Anti-OOM & Loop Trap：严禁 Python 嵌套 `for` 遍历数万级网格 | ✅ 已合规。所有空间分类、法线计算、连通分量都走 SciPy `convolve2d` + `ndimage.label`；唯一的线性遍历是 `len(lanes)` 级别的产物聚合 |
| 🔴 Anti-Hardcoded-Tiles：严禁 `if tile == "GRASS"` | ✅ 已合规。`TopologyExtractor` 只接受 `solid_tile_ids` / `platform_tile_ids` 两个整数集合；默认值在模块顶部以常量声明，可被任意 caller 覆盖；任何关卡主题词汇都不会渗入算法核心 |
| 🔴 Anti-Data-Silo Guard：严禁临时 `dict` / `tuple` | ✅ 已合规。提取产物全部为 `frozen=True` 的 dataclass，由 `ArtifactManifest` + `FAMILY_SCHEMAS` 双重 schema 校验；JSON 序列化通过 `to_json_dict()` 显式投影，`tensors_npz` 通过 `np.savez_compressed` 二进制持久化 |

## Preparing for `P1-ARCH-5` OpenUSD-Compatible Scene Interchange

`SESSION-109` 之后，`P1-ARCH-5` 的前置条件已经更具体了。`SemanticAnchor.transform_matrix()` 现在直接返回 4×4 矩阵，可以直接灌进 `UsdGeomXformable` 的 `xformOp:transform`；`TraversalLane.bounds` 与 `centroid` 提供了 `UsdGeomBoundable.extentsHint` 与 `UsdGeomXformable` 中心点的天然来源；`TopologyTensors.connected_components` 标号天生适合做 `UsdRelationship` 的 lane membership。下一步只需要一个 adapter-only 的 `UsdLevelTopologyExporter`，就能把当前的 `topology_json + tensors_npz` 平移成 `Sdf.Layer` 上的 prim 树，而不需要再回头改 backend / runtime 边界。

## Handoff Notes

* `BackendRegistry.reset()` 会清空 `_backends` 但保留 `_builtins_loaded=True` 的语义在本仓库是历史既定行为；新写的测试已规避此陷阱（不再 reset，直接 `get_registry()`），后续若要做 hot-reload 需要走 `BackendRegistry.unregister(...) + reload(...)` 的成对 API。
* 沙箱中既有的 `anti_flicker_render` CI 失败完全由本地未运行 ComfyUI / 缺 `gymnasium` 等环境性原因导致，与本轮改动无关。`level_topology` 在同一份 CI guard 中通过 `_build_minimal_context` 的回退路径自洽运行。
* 后续若要把本 backend 接入 `WFCTilemapBackend → LevelTopologyBackend → UsdLevelTopologyExporter` 的 PDG 链，建议在 `WFCTilemapBackend.outputs` 中明确加上 `tilemap_json` 的相对路径并在 manifest 中记录 grid 形状，这样 PDG v2 的 fan-out 节点可以零拷贝把 manifest 直接喂给本 backend。
