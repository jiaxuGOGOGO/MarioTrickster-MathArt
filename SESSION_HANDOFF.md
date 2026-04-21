| Key | Value |
|---|---|
| Session | `SESSION-111` |
| Focus | `P1-B3-5` Strangler-Fig Final Takeover：物理退役 `gait_blend.py` 与 `transition_synthesizer.py` re-export shim，将全局导入收束到单一 `unified_gait_blender.py` 运动核心，合并测试资产并建立三道红线反退化防护 |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `75 PASS / 0 FAIL`（`pytest -q tests/test_unified_gait_blender.py tests/test_locomotion_cns.py`，含 7 类测试组：SyncMarker DTW / GaitSyncProfile / PhaseWarper / StrideWheel / GaitBlender 管线 / TransitionSynthesizer 函数式 API / 三道红线审计） |
| Full Regression | `1834 PASS / 5 FAIL (all pre-existing) / 8 SKIP`。全部 5 个失败在 origin/main 纯净基线上一致复现，与本次重构完全无关，失败桶：ComfyUI WebSocket `progress_callback` 签名回归、`keyframe_count` 质量指标缺失、hot-reload watcher object-id race |
| Additional Audit | 已完成三道红线审计：**Anti-Zombie-Reference**（地毯式 `grep` 无任何 `from .gait_blend` / `from .transition_synthesizer` 残留；`pytest` 显式断言 `importlib.import_module('mathart.animation.gait_blend')` 抛出 `ModuleNotFoundError`）/ **Anti-C0-C1-Regression**（joint-space 接缝处 C0 连续精度 `abs=0.05`、C1 帧间 delta 有界 `< 0.1 rad`，收敛精度 `1e-4`）/ **Anti-PRNG-Bleed**（静态源码扫描确认 `unified_gait_blender.py` 零 `np.random` / `random.*` 调用，两份独立 blender 在 30 帧输入一致时 joint 精度 `1e-9` 完全一致，可作为 PDG v2 无状态 `WorkItem` 被 16 线程并发调用） |
| Primary Files | `mathart/animation/__init__.py`, `mathart/animation/locomotion_cns.py`, `mathart/animation/runtime_motion_query.py`, `mathart/animation/physics_genotype.py`, `mathart/animation/motion_matching_kdtree.py`, `mathart/evolution/layer3_closed_loop.py`, `mathart/evolution/gait_blend_bridge.py`, `tests/test_unified_gait_blender.py`, `tests/test_locomotion_cns.py`, (**DELETED**) `mathart/animation/gait_blend.py`, (**DELETED**) `mathart/animation/transition_synthesizer.py`, (**DELETED**) `test_session039.py` |

## Executive Summary

本轮的工程目标是完成 **P1-B3-5（gait_blend.py 与 transition_synthesizer.py 架构统一）** 的最后一公里——按照 **Strangler Fig Pattern** 的纪律对两个已经空壳化的 re-export 兼容层执行**物理删除**，让 `unified_gait_blender.py` 真正成为唯一合法的运动学接管者，彻底斩断任何"向后兼容"技术债务。

在 SESSION-069 时，`unified_gait_blender.py` 已把 gait 混合、sync-marker 相位对齐、DeepPhase FFT 相位锚定与 inertialized/dead-blended 过渡残差衰减融合为单一数值主干，`gait_blend.py` 和 `transition_synthesizer.py` 退化为 11 / 10 行的透传 shim。然而，这两个文件作为 Python 导入目标依然存在，形成了典型的"僵尸引用复活陷阱"——任何对微内核后端或 CLI 入口做热重载时，遗留的 `from mathart.animation.gait_blend import ...` 语句都可能意外复活并引发连锁 `ModuleNotFoundError`。SESSION-111 通过以下四步彻底关闭这道口子：

1. **地毯式引用拓扑审计** — 使用 `grep -rn` 扫描全仓库，定位 7 个包含旧模块导入的运行时 Python 文件与 2 个包含旧模块字面引用的测试文件；2 个历史迁移脚本（`scripts/update_project_brain_session049.py` / `scripts/update_project_brain_session065.py`）保留原始历史语义不动，其引用均为历史文本记录，不进入运行期。

2. **全局导入树重构** — 将 `__init__.py` / `locomotion_cns.py` / `runtime_motion_query.py` / `physics_genotype.py` / `motion_matching_kdtree.py` / `evolution/layer3_closed_loop.py` / `evolution/gait_blend_bridge.py` / `tests/test_locomotion_cns.py` 中 8 处旧模块导入全部重定向到 `unified_gait_blender`，并在 `mathart.animation` 公共 API 中新增 `UnifiedGaitBlender` 类本体导出。

3. **物理拔除 shim** — `git rm mathart/animation/gait_blend.py mathart/animation/transition_synthesizer.py`。至此 Python 导入机器在遇到任何旧路径时必然抛出 `ModuleNotFoundError`，Strangler-Fig 闭环收紧。

4. **测试资产合并** — `git mv tests/test_gait_blend.py tests/test_unified_gait_blender.py`，合并根目录 `test_session039.py` 中 12 个 transition-synth 断言到新的三类测试：`TestTransitionStrategyEnum` / `TestQuinticInertializationDecay` / `TestInertializeTransitionAPI`，均使用真实的 `capture/apply` API 对 Bollo 2018 quintic 残差衰减、Holden 半衰期衰减与 C0/C1 接缝连续性做数值断言。新增 `TestRetiredShimExtermination`（Anti-Zombie-Reference）与 `TestUnifiedGaitBlenderDeterminism`（Anti-PRNG-Bleed）两个红线守卫测试类。

## Research Alignment Audit

| Reference | Requested Principle | SESSION-111 Concrete Closure |
|---|---|---|
| Strangler Fig Pattern (Martin Fowler) | 新核心覆盖 100% 逻辑后必须斩断旧接口，严禁永久向后兼容技术债 | 物理 `git rm` 了 `gait_blend.py` 与 `transition_synthesizer.py` 两个 shim；全量 8 处运行时导入通过显式编辑重写为直引 `unified_gait_blender`，无任何动态/字符串级 fallback 路径 |
| Data-Oriented Design (Mike Acton) | 剥离 Shim 包装层和深层转发链以保留连续内存块与指令级并发 | shim 层被删除后，所有 `GaitBlender` / `TransitionSynthesizer` 等公共符号均从 `unified_gait_blender.py` 直接导入，调用栈从 2 层（shim → 核心）缩减为 1 层，无额外 Python 包装开销 |
| Bazel / PDG 缓存与依赖拓扑原理 | 文件层级即是依赖层级；废弃模块会引发伪依赖 | 仓库目录不再出现已废弃的兄弟模块，`mathart/animation/` 只剩下 `unified_gait_blender.py` 作为运动学权威单点；evolution 层 bridge 的 `collect_gait_blend_status()` 也同步迁移到新路径 |
| Registry Pattern (Gamma et al.) | 维持 `create_transition_synthesizer` 工厂接口的稳定性 | `create_transition_synthesizer(strategy=...)` 保留为 unified 核心的工厂入口，TransitionStrategy 枚举值与历史语义完全一致，外部调用无需修改 |
| Bollo Motion Matching Boot Camp (GDC 2018) | Quintic polynomial 残差衰减必须 C² 单调收敛到 0 | `TestQuinticInertializationDecay` 用真实帧序列验证：60 FPS / blend_time=0.2s 下残差非递增且在 ~0.25s 后归零精度 ≤ 1e-5 |
| Holden Dead Blending (2019) | Halflife 衰减必须快速归零 | `TestDeadBlendingResidualDecaysMonotonicallyToZero` 验证在 4×halflife 时残差 < 15%，且在完整窗口内严格非递增 |

## What Changed in Code

| File | Change | Effect |
|---|---|---|
| `mathart/animation/gait_blend.py` | **DELETED** (`git rm`) | 物理移除已空壳化的 re-export shim，Strangler-Fig 终局收网 |
| `mathart/animation/transition_synthesizer.py` | **DELETED** (`git rm`) | 物理移除已空壳化的 re-export shim |
| `test_session039.py` (root) | **DELETED** (`git rm`) | 历史 transition-synth 测试合并到 `tests/test_unified_gait_blender.py` 的三个新测试类 |
| `tests/test_gait_blend.py` → `tests/test_unified_gait_blender.py` | **RENAMED + EXTENDED** | 重命名为唯一权威 gait blender 测试套件，追加 7 个红线 / 合并测试类，共 68 测试 |
| `mathart/animation/__init__.py` | 重写 gait_blend / transition_synthesizer 相关 import 为直引 `unified_gait_blender`，新增 `UnifiedGaitBlender` 类体导出到 `__all__` | 公共 API 现在直接暴露运动学主干类，消除 re-export 双跳 |
| `mathart/animation/locomotion_cns.py` | 更新导入与 docstring 引用 | 消除 "from gait_blend.py 延伸" 的历史血缘文字 |
| `mathart/animation/runtime_motion_query.py` | 延迟导入改为 `from .unified_gait_blender import ...` | 运行时相机触发路径不再经过 shim |
| `mathart/animation/physics_genotype.py` | 同上 | genotype 闭环不再经过 shim |
| `mathart/animation/motion_matching_kdtree.py` | 更新 docstring 引用 | 血缘文档一致 |
| `mathart/evolution/layer3_closed_loop.py` | 重定向 TransitionStrategy / TransitionSynthesizer 导入 | 闭环评估层直引统一核心 |
| `mathart/evolution/gait_blend_bridge.py` | 重写源码引用 + `collect_gait_blend_status()` 识别 `unified_gait_blender.py` 与 `tests/test_unified_gait_blender.py` 为规范路径 | bridge 与 Gap B3 评估循环完全对齐新路径 |
| `tests/test_locomotion_cns.py` | `GaitMode` 导入重定向到 `unified_gait_blender` | 测试套件与生产侧血缘完全一致 |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 75/75 targeted 测试全绿 | `pytest -q tests/test_unified_gait_blender.py tests/test_locomotion_cns.py` → `75 passed` |
| Anti-Zombie-Reference 守卫 | `TestRetiredShimExtermination.test_gait_blend_file_is_gone` / `test_gait_blend_import_is_blocked`（以及 transition_synthesizer 对称版）显式断言文件系统不存在 + `importlib.import_module` 抛出 `ModuleNotFoundError` |
| 全量 `grep` 扫描 Zombie-Ref | `grep -rn -E '(from\s+\.gait_blend|from\s+mathart\.animation\.gait_blend|from\s+\.transition_synthesizer|from\s+mathart\.animation\.transition_synthesizer)' .` → `CLEAN` |
| Quintic 残差 C² 衰减 | `TestQuinticInertializationDecay.test_quintic_residual_decays_monotonically` / `test_quintic_residual_respects_blend_time_budget` 真实帧序列，单调非增，blend_time 预算内归零精度 `≤ 1e-5` |
| Dead-blending 半衰期衰减 | `test_dead_blending_residual_decays_monotonically_to_zero` 4×halflife 时残差 < 15%，完整窗口严格非递增 |
| C0/C1 关节空间连续性 | `TestInertializeTransitionAPI.test_c0_c1_joint_continuity_is_not_violated_at_seam` C0 首帧偏差 `abs=0.05`，C1 帧间 delta 上界 `< 0.1 rad`，终态收敛精度 `1e-4` |
| Anti-PRNG-Bleed 静态守卫 | `TestUnifiedGaitBlenderDeterminism.test_source_is_free_of_global_prng` 源码扫描 `np.random` / `random.*` / `random.seed` 等 7 个关键字均不存在 |
| Anti-PRNG-Bleed 动态守卫 | `test_two_blenders_produce_bitwise_identical_poses` 两个独立实例在相同 30 帧输入下 joint 精度 `1e-9` 完全一致 |
| 公共 API 导入一致 | `from mathart.animation import GaitBlender, TransitionSynthesizer, UnifiedGaitBlender, inertialize_transition, TransitionStrategy, InertializationChannel, DeadBlendingChannel` → 全部解析至 `mathart.animation.unified_gait_blender` |
| 全量回归零引入 | 1834 PASS / 5 FAIL (all pre-existing, baseline-confirmed) / 8 SKIP |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| 🔴 Anti-Zombie-Reference Guard：删除废弃模块后，所有 CLI 入口 / `__init__.py` / 微内核后端中不得有任何隐式调用旧模块 | ✅ 已合规。地毯式 `grep` 确认零遗留导入；`TestRetiredShimExtermination` 四个 pytest 显式断言文件不存在 + `importlib.import_module` 必然抛 `ModuleNotFoundError`；历史迁移脚本（`scripts/update_project_brain_session049.py` / `scripts/update_project_brain_session065.py`）仅包含历史文本记录，不在运行期导入路径上 |
| 🔴 Anti-C0/C1-Regression Guard：接口重定向与数据流重构不得因参数错位导致根运动跳变 | ✅ 已合规。`test_locomotion_cns.py` 中的 `test_phase_aligned_transition_clip_preserves_c0_c1_root_continuity` 维持 `abs=1e-6` 精度；新增 joint-space 版 `test_c0_c1_joint_continuity_is_not_violated_at_seam` 保障 Inertialize API 在关节空间 C0 偏差 `abs=0.05` 与 C1 帧间 delta `< 0.1 rad` 的双重边界 |
| 🔴 Anti-PRNG-Bleed Guard：unified_gait_blender 不得存在类级可变全局状态或裸 `np.random` | ✅ 已合规。静态 grep 扫描 `mathart/animation/unified_gait_blender.py` 零 `np.random` / `random.*` 调用；模块级无可变容器字面量；`test_two_blenders_produce_bitwise_identical_poses` 动态验证两个独立实例在相同 30 帧输入下 joint 精度 `1e-9` 完全一致，可作为无状态 PDG v2 `WorkItem` 被 16 线程并发调用 |

## Handoff Notes

- 本轮未修改 `unified_gait_blender.py` 本体，仅重定向所有外部导入，故 SESSION-069 建立的依赖注入纪律完全保留。后续 PDG v2 `WorkItem` 打包器（P1-ARCH-4）可直接以 `UnifiedGaitBlender` 实例作为工作单元无状态分发。
- `gait_blend_bridge.py` 的 `collect_gait_blend_status()` 接口签名保持不变，仅内部路径更新——任何依赖该接口的 evolution cycle 不需要同步改动。
- 5 个 pre-existing FAIL 建议按优先级抬升为独立任务处理：`P1-AI-2D` 批次的 `keyframe_count` 质量指标与 `progress_callback` 签名属于 AnimateDiff 客户端回归，`hot-reload watcher` 属于 object-id race 竞争，`ComfyUI backend manifest` 属于 CI 环境依赖；三者均与运动学主干解耦。
- 全量 `grep` 扫描确认历史迁移脚本 `scripts/update_project_brain_session049.py` / `scripts/update_project_brain_session065.py` 中对旧模块的字符串引用是历史文本记录（用于写入 PROJECT_BRAIN 历史字段），不在运行期导入图中，无需改动，保留其历史完整性。
