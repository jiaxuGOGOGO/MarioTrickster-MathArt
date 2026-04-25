# SESSION HANDOFF — SESSION-195 → SESSION-196

> **Last Updated:** 2026-04-25
> **Head Commit:** SESSION-195 (pending push)
> **PROJECT_BRAIN.json Version:** v1.0.6
> **Test Status:** 125 passed, 0 failed

---

## SESSION-195 核心目标：P0 全面攻坚（Full Matrix Closure）

SESSION-195 是一次 **P0 级全面攻坚**，一次性关闭 SESSION-194 遗留的全部四项 deferred_followups 中的三项（测试债务清欠 + IPAdapter 图源闭环 + 步态全矩阵扩容），使项目从"能跑"升级到"工业级可信赖"。

---

## 1. 历史测试债务清欠 (Test Debt Clearance)

### 问题
SESSION-193 将 Depth/Normal ControlNet 强度从 0.85→0.45（因为 OpenPose 以 strength=1.0 接管了运动控制），但 `test_session190` 和 `test_session192` 中的断言仍在检查 `>= 0.85`，导致 5 个测试持续红灯。

### 解决方案
遵循 **Martin Fowler 演进式架构**原则：测试断言是架构的"适应度函数"（Fitness Function），当上游契约变更时，下游断言必须同步演进——不能跳过、不能注释掉。

| 修复的测试 | 原断言 | 新断言 | 原因 |
|-----------|--------|--------|------|
| `test_session190::test_decoupled_depth_normal_strength` | `>= 0.85` | `>= 0.40` | SESSION-193 OpenPose 仲裁契约 |
| `test_session192::test_depth_normal_strength_at_or_above_redline` | `>= 0.85` | `>= 0.40` | 同上 |
| `test_session192::test_force_decouple_payload_reports_min_strength` | `>= 0.85` | `>= 0.40` + `== 0.45` | 精确匹配新默认值 |
| `test_session192::test_telemetry_handshake_text_contract` | `"0.90"` / `">= 0.85"` | 动态 f-string | 随常量自适应 |
| `test_session192::test_telemetry_warns_when_strength_below_redline` | `0.45` 触发 ⚠️ | `0.30` 触发 ⚠️ | 0.45 已在新红线之上 |

---

## 2. IPAdapter 真实图源动态寻址闭环 (Identity Context Late-Binding)

### 问题
SESSION-193 实现了 `identity_hydration.py` 模块，但 `_visual_reference_path` 从未在主管线的 chunk 组装站点被解析和注入。用户即使提供了参考图，IPAdapter LoadImage 节点也永远收不到真实路径。

### 解决方案（Spring ResourceLoader Late-Binding 模式）
在 `builtin_backends.py::_execute_live_pipeline` chunk 组装站点：

1. `extract_visual_reference_path(validated)` 在运行时从三级上下文位置解析路径：
   - `context["_visual_reference_path"]`（SESSION-193 标准位置）
   - `context["identity_lock"]["reference_image_path"]`（嵌套配置）
   - `context["director_studio_spec"]["_visual_reference_path"]`（导演工坊规格）
2. 路径存在 → `inject_ipadapter_identity_lock()` 注入到 ComfyUI workflow
3. 路径非空但文件不存在 → `PipelineIntegrityError`（Fail-Fast，拒绝幽灵路径）
4. 路径为空/None → 优雅降级（跳过 IPAdapter，不报错）

---

## 3. OpenPose 步态全矩阵扩容 (Gait Registry Expansion)

### 问题
SESSION-194 仅实现了 `walk` 一种步态的 Catmull-Rom 关键帧模板。`run`、`jump`、`idle`、`dash` 等动作没有对应的 COCO-18 姿态序列。

### 解决方案（UE5 AnimGraph Chooser-Table / Registry Pattern）
引入 `OpenPoseGaitRegistry`（数据驱动注册表），每种步态是一个独立的 `OpenPoseGaitStrategy` 子类：

| 策略类 | action_name | 运动特征 |
|--------|-------------|---------|
| `_WalkGaitStrategy` | `walk` | 标准步行：摆臂 + 脚跟着地 |
| `_RunGaitStrategy` | `run` | 快跑：加大摆臂 + 高抬膝 + 前倾 |
| `_JumpGaitStrategy` | `jump` | 跳跃：蹲伏 → 起跳 → 顶点 → 落地 |
| `_IdleGaitStrategy` | `idle` | 待机：微呼吸摆动 + 重心转移 |
| `_DashGaitStrategy` | `dash` | 冲刺：极端前倾 + 爆发摆臂 |

**反意面条红线**：`bake_openpose_pose_sequence` 中**零 if/elif 分支**。步态解析完全通过 `_GAIT_REGISTRY.get(action_name)` 完成。新步态只需子类化 `OpenPoseGaitStrategy` 并调用 `register_gait_strategy()`，无需修改任何现有代码（OCP）。

---

## 4. 本次修改的全部文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mathart/core/openpose_pose_provider.py` | **重写** | Gait Registry Pattern（5 策略 + ABC + Registry） |
| `mathart/core/builtin_backends.py` | **修改** | IPAdapter late-binding 注入到 chunk 组装站点 |
| `tests/test_session190_modal_decoupling_and_lookdev.py` | **修改** | 断言对齐（0.85→0.40） |
| `tests/test_session192_dependency_seal_and_telemetry.py` | **修改** | 4 处断言对齐 |
| `tests/test_session195_full_matrix_closure.py` | **新增** | 30+ 综合测试 |
| `docs/RESEARCH_NOTES_SESSION_195.md` | **新增** | 工业参考资料综合笔记 |
| `docs/USER_GUIDE.md` | **追加** | Section 25 |
| `PROJECT_BRAIN.json` | **修改** | v1.0.5→v1.0.6，新增 SESSION-195 条目 |
| `SESSION_HANDOFF.md` | **覆写** | 本文档 |

---

## 5. 红线合规声明

| 红线 | 状态 |
|------|------|
| 代理环境变量零接触 | ✅ |
| SESSION-189 锚点（MAX_FRAMES=16/LATENT_EDGE=512/NORMAL_MATTE_RGB）| ✅ |
| SESSION-190 `force_decouple_dummy_mesh_payload` 算法 | ✅ |
| SESSION-191 action_filter Deep Pruning 链路 | ✅ |
| SESSION-192 物理遥测 banner 文案 | ✅ |
| SESSION-193 `arbitrate_controlnet_strengths` 算法 | ✅ |
| SESSION-194 OpenPose IoC 契约 | ✅ |
| 语义选择器寻址（class_type + _meta.title） | ✅ |
| 测试断言演进（非跳过/注释） | ✅ |

---

## 6. 测试结果摘要

```
tests/test_session190_modal_decoupling_and_lookdev.py   17 passed
tests/test_session192_dependency_seal_and_telemetry.py   25 passed
tests/test_session193_identity_chunk_openpose.py         38 passed
tests/test_session194_pipeline_integration_closure.py    15 passed
tests/test_session195_full_matrix_closure.py             30 passed
─────────────────────────────────────────────────────────
TOTAL                                                   125 passed, 0 failed
```

---

## 7. 傻瓜验收指引

```bash
# 1. 拉代码
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt

# 2. 安装
sudo pip3 install -q pytest hypothesis pillow numpy scipy networkx

# 3. 跑全量测试（无需 ComfyUI / 无需 GPU / 无需网络）
PYTHONPATH=. python3.11 -m pytest \
    tests/test_session190_modal_decoupling_and_lookdev.py \
    tests/test_session192_dependency_seal_and_telemetry.py \
    tests/test_session193_identity_chunk_openpose.py \
    tests/test_session194_pipeline_integration_closure.py \
    tests/test_session195_full_matrix_closure.py -v
# 期望输出：125 passed

# 4. 快速 sanity check — 步态注册表
PYTHONPATH=. python3.11 -c "
from mathart.core.openpose_pose_provider import get_gait_registry
r = get_gait_registry()
print('Registered gaits:', r.names())
for name in ['walk','run','jump','idle','dash']:
    frames = r.get(name).generate(8)
    print(f'  {name}: {len(frames)} frames, joints={len(frames[0])}')
"
```

---

## 8. 工业参考文献

| 参考 | 应用 |
|------|------|
| UE5 Game Animation Sample — Motion Matching + Chooser Table | 步态注册表 Registry Pattern |
| Spring Framework ResourceLoader — Late-Binding + Fail-Fast | IPAdapter 图源动态寻址 |
| Martin Fowler — Contract Test + Evolutionary Architecture | 测试债务清欠方法论 |

---

## 9. 给 SESSION-196 的接力建议

1. **真实 ComfyUI 集成回放**：拿一台带 GPU 的环境，把 SESSION-194/195 的完整管线推给真实 ComfyUI server 跑一次，截图归档。
2. **Gait-aware action_name threading**：将 `action_name` 从 CLI/intent 穿透到 `bake_openpose_pose_sequence`，让每个动作自动获得对应步态模板。
3. **IPAdapter reference image CLI wizard 集成**：在 `intent.yaml` 解析中增加 `reference_image` 字段支持。
4. **步态过渡混合**：实现动作边界处的步态交叉淡入淡出（如 walk→run 过渡）。

---

**SESSION-195 任务交付完毕。等老大点头进 SESSION-196。** 🎯
