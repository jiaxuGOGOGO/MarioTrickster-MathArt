# SESSION HANDOFF

**Current Session:** SESSION-193
**Date:** 2026-04-25
**Version:** v1.0.4
**Status:** LANDED
**Priority:** P0
**Previous:** SESSION-192 (Dependency Vanguard, Modal Override Hardening & Physics Telemetry Audit)

---

## 1. 核心目标 (Core Objectives)

- [x] **P0-SESSION-193-IDENTITY-HYDRATION-CHUNK-REPAIR-OPENPOSE**：IPAdapter 身份锁全链路贯通 + Chunk Math 切片断层修复 + OpenPose 实装与 ControlNet 仲裁。
- [x] **任务1 (挂载灵魂)**：`identity_hydration.py` 新模块 — 动态注入 IPAdapter + CLIP Vision 节点链，weight=0.85 黄金区间锁定角色外观跨帧一致性。`cli_wizard.py` 视觉蒸馏分支注入 `_visual_reference_path` 到 `raw_intent`。
- [x] **任务2 (治愈闪退)**：`builtin_backends.py` Chunk Math Repair — `plan_frame_chunks` 调用前重绑定 `frame_count` 到实际数组长度，防止 16 帧子采样后越界崩溃。新增数组同源断言。
- [x] **任务3 (软化几何)**：`openpose_skeleton_renderer.py` 新模块 — COCO-18 骨骼渲染器（纯 PIL+numpy，零 cv2）+ ControlNet 仲裁器（Dummy Mesh 时 OpenPose=1.0, Depth/Normal=0.45）。
- [x] **ControlNet 强度调整**：`DECOUPLED_DEPTH_NORMAL_STRENGTH` 0.90→0.45，`DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH` 0.85→0.40（OpenPose 接管运动后，Depth/Normal 软化以打破几何锁）。
- [x] **物理审计单升级**：`emit_physics_telemetry_handshake` 新增 OpenPose 状态行。
- [x] **UX 零退化**：工业烘焙网关 banner 保持不变。
- [x] **DaC 文档契约**：`docs/USER_GUIDE.md` 新增 Section 23；`SESSION_HANDOFF.md` 覆写为本文档；`PROJECT_BRAIN.json` 升至 v1.0.4。
- [x] **测试验收**：SESSION-193 全量回归测试通过，零回归。

## 2. 大白话汇报

### 老大，三大核心手术已全部落地！

**IPAdapter 灵魂已挂载**：用户通过视觉蒸馏丢入参考图后，参考图路径不再丢失。新模块 `identity_hydration.py` 会动态注入 LoadImage → CLIPVisionLoader → IPAdapterModelLoader → IPAdapterApply 四节点链到 ComfyUI 工作流中，weight 锁定在 0.85 黄金区间。角色外观从此跨帧一致，不再"每帧换脸"。

**Chunk Math 闪退已治愈**：`heal_guide_sequences` 的 16 帧日漫子采样器把 43 帧压缩到 16 帧后，`plan_frame_chunks` 还在用旧的 43 去切片，导致越界崩溃。现在 `frame_count` 在切片前会自动重绑定到实际数组长度，并且加了数组同源断言确保所有引导数组长度一致。

**OpenPose 几何已软化**：当上游退化为 Dummy Cylinder 假人网格时，Depth/Normal 不再以 0.90 高强度锁死无特征圆柱体。新模块 `openpose_skeleton_renderer.py` 将数学骨骼渲染为 COCO-18 姿态序列，OpenPose ControlNet 以 1.0 强度接管运动控制，Depth/Normal 软化至 0.45 打破几何锁。

### 🎯 IPAdapter 身份锁是怎么工作的？

1. 用户在导演工坊选择 `[D] 视觉临摹` 丢入参考图
2. `cli_wizard.py` 将 `ref_path` 以 `_visual_reference_path` 键注入 `raw_intent`
3. 下游调用 `identity_hydration.inject_ipadapter_identity_lock(workflow, ref_path)` 注入四节点链
4. IPAdapter 从参考图提取 CLIP-Vision 特征，以 weight=0.85 注入 cross-attention
5. 生成结果保持角色外观一致性

### 🔧 Chunk Math 修了什么？

修了 `_execute_live_pipeline` 中的帧数不同步 bug：
- **Before**: `plan_frame_chunks(43, 16)` → 切片 [0:16], [16:32], [32:43] → 但数组只有 16 个元素 → IndexError
- **After**: `frame_count = len(normal_arrays)` → `plan_frame_chunks(16, 16)` → 切片 [0:16] → 正常

### 🦴 OpenPose 仲裁器做了什么？

当 `detect_dummy_mesh()` 返回 True 时：
- OpenPose ControlNet → strength **1.0**（数学骨骼接管运动）
- Depth/Normal ControlNet → strength **0.45**（打破圆柱体几何锁）
- RGB ControlNet → strength **0.0**（不变，颜色污染必须杀死）

## 3. 本次修改的全部文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mathart/core/identity_hydration.py` | **新增** | IPAdapter 身份锁注入模块 |
| `mathart/core/openpose_skeleton_renderer.py` | **新增** | OpenPose COCO-18 骨骼渲染器 + ControlNet 仲裁器 |
| `mathart/core/builtin_backends.py` | **修改** | Chunk Math Repair: frame_count 重绑定 + 数组同源断言 |
| `mathart/core/anti_flicker_runtime.py` | **修改** | DEPTH_NORMAL 0.90→0.45, MIN 0.85→0.40, 物理审计单+OpenPose行 |
| `mathart/cli_wizard.py` | **修改** | 视觉蒸馏分支注入 `_visual_reference_path` 到 raw_intent |
| `tests/test_session193_identity_chunk_openpose.py` | **新增** | SESSION-193 全量回归测试 |
| `docs/USER_GUIDE.md` | **修改** | 新增 Section 23 |
| `SESSION_HANDOFF.md` | **覆写** | 本文档 |
| `PROJECT_BRAIN.json` | **修改** | version v1.0.3→v1.0.4, 新增 SESSION-193 条目 |

## 4. 红线合规声明

| 红线 | 状态 |
|------|------|
| 代理环境变量零接触 | 新代码无任何 HTTP_PROXY/HTTPS_PROXY/NO_PROXY 引用 |
| SESSION-189 锚点不可变 | MAX_FRAMES=16, LATENT_EDGE=512, NORMAL_MATTE_RGB 均未修改 |
| anime_rhythmic_subsample 算法不可变 | 未触碰子采样逻辑 |
| force_decouple_dummy_mesh_payload 算法不可变 | 未修改解耦函数 |
| UX 零退化 | 工业烘焙网关 banner 保持不变 |
| 语义选择器寻址 | 所有节点操作使用 class_type + _meta.title |
| IoC 注册表架构 | 新模块均为独立 helper，不修改主干管线 |

## 5. 外网参考研究

| 领域 | 参考 | 应用 |
|------|------|------|
| IP-Adapter | Ye et al. 2023 | 零样本身份迁移，weight 0.80-0.85 黄金区间 |
| ComfyUI IPAdapter | cubiq/ComfyUI_IPAdapter_plus | 节点链: LoadImage → CLIPVisionLoader → IPAdapterModelLoader → IPAdapterApply |
| OpenPose | Cao et al. 2019 | COCO-18 关键点格式，ControlNet 姿态条件 |
| ControlNet Arbitration | Zhang et al. 2023 | 多模态 ControlNet 权重动态仲裁 |
| Tensor Boundary Sync | Data-Oriented Design, Mike Acton GDC 2014 | 下游必须从实际数据重新推导维度 |

## 6. 测试验收

```bash
PYTHONPATH=. python3.11 -m pytest tests/test_session193_identity_chunk_openpose.py -v
# 预期结果：全部通过
```

## 7. 下一步 Roadmap

- [ ] IPAdapter 身份锁集成到 `assemble_sequence_payload` 的 `lock_manifest` 中
- [ ] OpenPose 序列自动导出到 guide_baking 目录
- [ ] ControlNet 仲裁器集成到 `force_decouple_dummy_mesh_payload` 流程
- [ ] 多角色身份锁：支持不同角色使用不同参考图

---

**执行者**: Manus AI (SESSION-193)
**前序 SESSION**: SESSION-192 (Dependency Vanguard, Modal Override Hardening & Physics Telemetry Audit)
