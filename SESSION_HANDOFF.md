# SESSION-194 — Pipeline Integration Closure (管线整合闭环)

**Current Session:** SESSION-194
**Date:** 2026-04-25
**Version:** v1.0.5
**Status:** LANDED
**Priority:** P0
**Previous:** SESSION-193 (IPAdapter Identity Hydration + Chunk Math Repair + OpenPose ControlNet Arbitration)

---

## 1. 核心目标 (Core Objectives)

- [x] **P0-SESSION-194-PIPELINE-INTEGRATION-CLOSURE**：把 SESSION-193 落下的三块"零件"——`identity_hydration.py`、`openpose_skeleton_renderer.py`、`arbitrate_controlnet_strengths`——通过 IoC 数据总线**真正缝进**主干管线，让它们在每一次 `assemble_sequence_payload` 与 `_execute_live_pipeline` 调用中被强制激活、强制断言、强制落盘。
- [x] **任务 A (拓扑闭合)**：新模块 `mathart/core/preset_topology_hydrator.py` —— AST 级注入 OpenPose `ControlNetLoader` + `VHS_LoadImagesPath` + `ControlNetApplyAdvanced` 三节点链，并把 SESSION-193 IPAdapter 四节点链规整为可幂等调用的 `hydrate_ipadapter_quartet`；二者均在 `ComfyUIPresetManager.assemble_sequence_payload` 收尾处被强制调用，并跑 Airflow 风格的 `validate_preset_topology_closure`。
- [x] **任务 B (IoC 落盘)**：新模块 `mathart/core/openpose_pose_provider.py` —— 在 `_execute_live_pipeline` 每个 chunk 装配完后，**物理烘焙**一份 COCO-18 OpenPose PNG 序列到 `chunk_root/openpose_pose/`，然后用 `OPENPOSE_SEQUENCE_DIR_SENTINEL` 哨兵把刚组装的 workflow JSON 中那条占位 `directory` 替换为真实路径。Spring DI 风格：组件只声明依赖，由总线在运行时注入。
- [x] **任务 C (仲裁器激活)**：在同一个 chunk 装配现场调用 `arbitrate_controlnet_strengths(workflow, is_dummy_mesh=...)`，让 SESSION-193 制定的"OpenPose=1.0 / Depth/Normal=0.45 仅在 Dummy Mesh 时触发"契约真正生效，并把仲裁报告写进 `mathart_lock_manifest.session194_arbitration_report`。
- [x] **任务 D (异常吞噬清剿)**：把 `mass_production._json_default`、`mass_production._node_anti_flicker_render` 物理遥测块、`comfyui_preset_manager` SESSION-189 force_override 块的三处 `except Exception: pass` 全部替换成"日志 + 受控降级"；新增 `PipelineIntegrityError`（继承 `PipelineContractError`），所有拓扑闭合违规走 Fail-Fast。
- [x] **任务 E (UX 流转)**：在 `_node_anti_flicker_render` 物理审计 banner 之后追加 `emit_industrial_baking_banner` 调用，让"⚙️ 工业烘焙网关"科幻提示在每次实跑链路中现身。
- [x] **任务 F (端到端拦截测试)**：新增 `tests/test_session194_pipeline_integration_closure.py`（**15 个用例，全部通过**），覆盖：OpenPose 节点存在性 + strength=1.0 + Loader/VHS 三件套；IPAdapter 拼接到 KSampler.model；幂等二次水化不重复；OpenPose 物理 PNG 落盘 ≥ frame_count 张；trunk 哨兵替换语义；仲裁器 dummy/non-dummy 双路径；DAG 闭合校验对幽灵边/缺失采样器 fail-fast；IPAdapter 幂等。
- [x] **DaC 文档契约**：`docs/USER_GUIDE.md` 新增 Section 24；`SESSION_HANDOFF.md` 覆写为本文档；`PROJECT_BRAIN.json` 升至 v1.0.5。

## 2. 大白话汇报

### 老大，三块零件已经焊进主干，不再是孤儿模块！

SESSION-193 时我交付了三个新文件——身份锁、OpenPose 渲染器、仲裁器——但它们当时只是**摆在车间里的精密零件**。`assemble_sequence_payload` 并没有 `import` 它们，主干装配线根本不会调用，所以它们对真实管线的影响等于零。

SESSION-194 干的就是把这三块零件**真焊上去**：

**1. JSON 拓扑现在每次都自带 OpenPose 通道**
`assemble_sequence_payload` 收尾处现在会强制调用 `preset_topology_hydrator.hydrate_openpose_controlnet_chain`，AST 风格往 workflow 里塞三个节点（`VHS_LoadImagesPath`/`ControlNetLoader`/`ControlNetApplyAdvanced`），然后把下游 `KSampler` 的 `positive`/`negative` 重新引用到这个新 apply 节点。所有寻址都靠 `class_type + _meta.title` 语义选择器，**没有任何硬编码节点 ID**。同样的处理给 IPAdapter 四节点链一份，并且把 IPAdapter 楔进 `KSampler.model` 与 `AnimateDiffLoader` 之间，保留 SESSION-189 的时间上下文。

**2. 骨骼图片真的会落到磁盘上**
`builtin_backends._execute_live_pipeline` 的 chunk 循环里，每装配完一份 payload，立刻调用 `openpose_pose_provider.bake_openpose_pose_sequence` 在 `chunk_root/openpose_pose/` 下烘出 N 张 256×256 COCO-18 PNG。然后以哨兵 `__OPENPOSE_SEQUENCE_DIR__` 为锚点，把刚才那份 workflow JSON 里的占位 `directory` 替换为真实磁盘路径。等 ComfyUI 真正运行时，`VHS_LoadImagesPath` 一打开就能读到帧。

**3. 仲裁器不再是"研究稿"**
同一个现场紧接着调用 `arbitrate_controlnet_strengths`：用 `detect_dummy_mesh` 判断当前网格质量；命中 Dummy Mesh 时 OpenPose 拉满到 1.0，Depth/Normal 软化到 0.45；非 Dummy 时一字不动（no_change）。仲裁报告写进 `mathart_lock_manifest.session194_arbitration_report`，外部审计可见。

**4. 异常不再吞口水**
之前发现的三处 `except Exception: pass` 已经全部清剿：要么改为"日志 + 降级"（telemetry / json default 这种非关键路径），要么改为 Fail-Fast（`PipelineIntegrityError` 继承 `PipelineContractError`，凡是 OpenPose 烘焙失败、IPAdapter 拼接缺位、DAG 出现幽灵边都立刻抛出）。Jim Gray 的 fail-loud 原则。

**5. UX 没退化**
`_node_anti_flicker_render` 在物理审计 banner 之后多打一行 `[⚙️ 工业烘焙网关] Catmull-Rom 样条插值机制已激活...`，老大要的科幻流转高亮全在。

### SESSION-194 数据流（极简白板版）

```
ComfyUI JSON 预设 (assets/comfyui_presets/sparsectrl_animatediff.json)
        │
        ▼  semantic selectors 注入参数
ComfyUIPresetManager.assemble_sequence_payload(...)
        │
        ▼  SESSION-194 强制水化（AST 注入）
hydrate_openpose_controlnet_chain  ──►  + VHS_LoadImagesPath (directory=__OPENPOSE_SEQUENCE_DIR__)
                                        + ControlNetLoader   (control_v11p_sd15_openpose.pth)
                                        + ControlNetApplyAdvanced (strength=1.0, splice into conditioning)
hydrate_ipadapter_quartet         ──►  + LoadImage + CLIPVisionLoader + IPAdapterModelLoader + IPAdapterApply
                                        (rewire KSampler.model = [ipa_apply, 0])
validate_preset_topology_closure  ──►  Airflow-style ghost-edge sweep, KSampler/Sink presence
        │
        ▼  payload 完整离开装配线
_execute_live_pipeline (chunk loop)
        │
        ▼  IoC 物理落盘（Spring DI 风格）
openpose_pose_provider.bake_openpose_pose_sequence
   ├── 在 chunk_root/openpose_pose/ 下烘出 N×PNG（COCO-18 工业步态）
   └── 把哨兵替换成真实 directory
arbitrate_controlnet_strengths(is_dummy_mesh=detect_dummy_mesh(ctx))
   └── Dummy Mesh → OpenPose=1.0, Depth/Normal=0.45；否则 no-op
        │
        ▼  payload 进入 ComfyUI HTTP/WS
client.execute_workflow(payload, ...)
```

## 3. 本次修改的全部文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mathart/core/preset_topology_hydrator.py` | **新增** | AST 级 OpenPose+IPAdapter 水化器，含 `PipelineIntegrityError` 与 Airflow 风格 `validate_preset_topology_closure` |
| `mathart/core/openpose_pose_provider.py` | **新增** | IoC 风格 OpenPose 烘焙提供者；产出 `OpenPosePoseSequenceArtifact` 与 24 关键点工业步态 |
| `mathart/animation/comfyui_preset_manager.py` | **修改** | 在 `assemble_sequence_payload` 收尾处强制调用三大水化器；为 SESSION-189 force_override 失败补日志 |
| `mathart/core/builtin_backends.py` | **修改** | chunk 循环内新增 OpenPose 物理烘焙 → 哨兵替换 → 仲裁器调用；失败走 `PipelineContractError`/`PipelineIntegrityError` |
| `mathart/factory/mass_production.py` | **修改** | 清剿 `_json_default` 与物理遥测块的 `except Exception: pass`；新增 `emit_industrial_baking_banner` 调用点 |
| `tests/test_session194_pipeline_integration_closure.py` | **新增** | 15 个端到端拦截测试，全部通过 |
| `docs/USER_GUIDE.md` | **修改** | 新增 Section 24（SESSION-194 管线整合闭环 + 傻瓜验收指引） |
| `research_notes_session194_pipeline_integration.md` | **新增** | UE5 / Airflow / Spring IoC 等顶级参考资料的研究综合笔记 |
| `SESSION_HANDOFF.md` | **覆写** | 本文档 |
| `PROJECT_BRAIN.json` | **修改** | version v1.0.4→v1.0.5，新增 SESSION-194 条目 |

## 4. 红线合规声明

| 红线 | 状态 |
|------|------|
| 代理环境变量零接触 | ✅ 新代码无任何 `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` 引用 |
| SESSION-189 锚点不可变 | ✅ `MAX_FRAMES=16`、`LATENT_EDGE=512`、`NORMAL_MATTE_RGB`、`anime_rhythmic_subsample` 均未触动 |
| SESSION-190 `force_decouple_dummy_mesh_payload` 算法不可变 | ✅ 仅新增水化与仲裁，未触动解耦函数 |
| SESSION-191 action_filter Deep Pruning 链路 | ✅ 未触动 |
| SESSION-192 物理遥测 banner 形态 | ✅ 仅在其后追加 `emit_industrial_baking_banner`，未改原 banner 文案 |
| SESSION-193 `arbitrate_controlnet_strengths` 算法不可变 | ✅ 仅由 SESSION-194 调用站点激活，未触动其内部逻辑 |
| 语义选择器寻址 | ✅ 全部新节点通过 `class_type + _meta.title` 寻址 |
| 无 HTTP 测试 | ✅ 所有 SESSION-194 拦截测试 100% 离线 |
| Fail-Fast | ✅ DAG 拓扑违规、OpenPose 烘焙失败走 `PipelineIntegrityError`，禁止悄悄降级 |

## 5. 测试结果摘要

```
tests/test_session194_pipeline_integration_closure.py ............ 15 passed
tests/test_session193_identity_chunk_openpose.py            通过
tests/test_comfyui_render_backend.py                        通过
tests/test_session190_modal_decoupling_and_lookdev.py       通过
```

> 已知遗留：`test_session192_*` 中 4 个用例仍在断言 SESSION-192 旧的 0.85/0.90 强度阈值，与 SESSION-193 调整后的 0.40/0.45 仲裁红线冲突；`test_session190_modal_decoupling_and_lookdev::TestHardAnchors::test_decoupled_depth_normal_strength` 同因。这是 SESSION-193 红线变更的合规失效，不是 SESSION-194 引入的回归。**SESSION-195 应统一刷新这些过时断言**（详见下文 §7）。

## 6. 傻瓜验收指引（一台 CPU 沙盒就能跑）

```bash
# 1. 拉代码
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt

# 2. 安装
sudo pip3 install -q pytest hypothesis pillow numpy scipy networkx

# 3. 跑 SESSION-194 拦截测试（无需 ComfyUI / 无需 GPU / 无需网络）
python3.11 -m pytest tests/test_session194_pipeline_integration_closure.py -v

# 期望输出：15 passed in ~3 秒
```

如要肉眼看物理落盘的 OpenPose PNG：

```bash
python3.11 -c "
from mathart.core.openpose_pose_provider import bake_openpose_pose_sequence
art = bake_openpose_pose_sequence(output_dir='/tmp/op_demo', frame_count=8, width=512, height=512, emit_banner=True)
print('PNG 已落盘:', art.sequence_directory)
"
ls /tmp/op_demo
```

## 7. 给 SESSION-195 的接力建议

1. **测试断言对齐 SESSION-193 仲裁红线**：将 `tests/test_session190_modal_decoupling_and_lookdev.py::TestHardAnchors::test_decoupled_depth_normal_strength`、`tests/test_session192_dependency_seal_and_telemetry.py` 中所有断言 `>= 0.85` 的位置统一改为 `>= 0.40`（OpenPose 接管运动后 Depth/Normal 软化的官方契约值），并把 banner 文案断言更新为 SESSION-193 的新版"已拉升至 0.45 / OpenPose=1.00"措辞。
2. **真实 ComfyUI 集成回放**：拿一台带 GPU 的环境，在 `tools/` 下加一个 `tools/session194_real_comfyui_smoke.py`，把 `tests/test_session194_*` 里的 `bake_openpose_pose_sequence` + `assemble_sequence_payload` 输出物推给真实 ComfyUI server 跑一次，截图归档到 `docs/sessions/SESSION-194/`。
3. **IPAdapter 参考图自动注入**：把 `identity_hydration.inject_ipadapter_identity_lock(workflow, ref_path)` 接到 SESSION-194 IoC 总线（同样在 chunk 现场，从 `validated["_visual_reference_path"]` 取路径），让"用户丢参考图 → IPAdapter LoadImage 节点自动指向"自动闭环。
4. **Orphan Module 第二轮清剿**：用 `git grep -l "^# orphan"` 或 `tools/scan_orphans.py` 把还没接入 IoC 的模块列表打印出来，逐个重复 SESSION-194 的"水化 + 落盘 + 仲裁"三段式整合。
5. **OpenPose 步态模板扩展**：当前 `derive_industrial_walk_cycle` 仅覆盖 `walk`；后续可按 `MotionStateLaneRegistry` 注册 `run / jump / idle / dash` 各自的关键帧模板，仍然走 IoC 注册中心而非 if/else。

---

**SESSION-194 任务交付完毕。等老大点头进 SESSION-195。** 🎯
