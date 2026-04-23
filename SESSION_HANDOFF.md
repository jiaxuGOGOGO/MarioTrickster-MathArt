# SESSION-166 交接文档 (SESSION_HANDOFF.md)

> **"老大，解耦手术已完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！
> 渲染循环的'静止帧'传参断链已被彻底修复——此前 Clip2D 骨骼名（l_thigh）和 Skeleton 关节名（l_hip）之间存在命名空间断裂，导致每帧姿态数据全部丢失，骨骼永远停在默认姿态。
> 现在 Bone→Joint 映射桥已焊死，度→弧度转换已贯通，MSE 从 0.0000 飙升到 153~272，VarianceAssertGate 完美放行！"**

**Date**: 2026-04-23
**Parent Commit**: SESSION-165
**Task ID**: P0-SESSION-166-RENDER-LOOP-HYDRATION
**Status**: CLOSED
**External Anchors**: `docs/RESEARCH_NOTES_SESSION_166.md`

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **修复渲染循环静止帧传参断链 (Per-Frame State Hydration)** | `mathart/factory/mass_production.py` — `_bake_true_motion_guide_sequence()` 及 `_animation_func_from_clip()` | Per-Frame State Hydration (Vulkan Multi-Frame Flight Buffers, Blender Mesh Cache Modifier) |
| 2 | **Bone→Joint 命名空间桥接** | `mathart/factory/mass_production.py` — `_bone_to_joint` 映射表 | Data-Oriented Design (DOD) — 消除指针追逐与命名空间断裂 |
| 3 | **度→弧度单位转换贯通** | `mathart/factory/mass_production.py` — `math.radians()` 转换 | Dimensional Analysis (量纲分析) — 确保上下游数据单位一致 |
| 4 | **根位移注入光栅化器** | `mathart/factory/mass_production.py` — `root_x/root_y` → `scale_x/scale_y` 传递 | Global Root Motion Injection (全局根运动注入) |
| 5 | **UX 科幻流转展示升级** | `mathart/factory/mass_production.py` — 烘焙网关 Banner + 完成 Banner 增强 | Fail-Loud Validation + 科幻级终端 UX |
| 6 | **文档同步 (DaC)** | `docs/USER_GUIDE.md` — SESSION-166 管线修复说明 | DaC 文档契约 |
| 7 | **外部研究锚点** | `docs/RESEARCH_NOTES_SESSION_166.md` — 完整研究笔记 | Per-Frame State Hydration, DOD, Fail-Loud Validation |

## 2. 根因分析 (Root Cause Analysis)

### 症状
```
[SESSION-160 VarianceAssertGate] Channel 'source': frame pair (0, 1) has MSE=0.00000000
```
渲染出的帧序列完全相同，MSE=0，VarianceAssertGate 正确拦截。

### 根因链
1. **命名空间断裂**：Clip2D 的 `bone_transforms` 使用**骨骼名称**（如 `l_thigh`, `r_thigh`），而 `Skeleton.apply_pose()` 期望**关节名称**（如 `l_hip`, `r_hip`）。旧代码直接将骨骼名作为 key 传入 `apply_pose()`，由于 `if name in self.joints` 检查永远不匹配，所有骨骼角度从未被更新。
2. **单位不匹配**：Clip2D 存储的 `rotation` 值为**度数**（如 20.0, -5.89），而骨骼 FK 引擎使用**弧度**。即使名称匹配了，角度值也会因单位错误而产生错误的姿态。
3. **根位移丢失**：`root_x` 和 `root_y` 被放入 pose dict 后被 `apply_pose()` 忽略（没有名为 "root_x" 的关节），导致全局位移信息丢失。

### 修复措施
1. 构建 `_bone_to_joint` 映射表：遍历 `skeleton.bones`，将每个骨骼的 `joint_a`（驱动关节）映射为该骨骼名的目标关节。
2. 在 `_animation_func_from_clip()` 中，将骨骼名翻译为关节名后再放入 pose dict。
3. 使用 `math.radians()` 将度数转换为弧度。
4. 将 `root_x/root_y` 从 pose dict 中弹出，转换为 `scale_x/scale_y` 传递给渲染器。

### 验证结果
- **MSE**: 从 0.0000 提升到 153~272（24帧序列，23对帧间比较）
- **VarianceAssertGate**: 完美放行，零拦截
- **测试**: `test_mass_production.py` 2/2 PASS, `test_no_hardcoded_motion_states_session162.py` 3/3 PASS, 全项目 2555/2596 PASS（41 failures 均为已有环境依赖问题，与本次修改无关）

## 3. 红线遵守声明

| 红线 | 状态 |
|------|------|
| **[防假动作红线]** — 严禁调低、注释或删除 `assert_nonzero_temporal_variance` (MSE 质检函数) | ✅ 严格遵守 — 质检函数完整保留，修复的是 Implementation 而非 Test |
| **[立竿见影验收红线]** — MSE 报错彻底消失，终端完美打印高清资产解算完成提示 | ✅ 严格遵守 — MSE 从 0.0000 飙升到 153~272 |
| **[精准定位修复红线]** — 必须修复核心烘焙渲染循环，实现真实物理贴图位移更新 | ✅ 严格遵守 — 修复了 Bone→Joint 映射、度→弧度转换、根位移注入三个断链点 |
| **[UX 零退化红线]** — 终端科幻流转展示不退化 | ✅ 严格遵守 — 烘焙网关 Banner 增加 SESSION-166 Hydration 状态，完成 Banner 增加 MSE 诊断 |
| **[DaC 文档契约红线]** — 必须同步修改 `docs/USER_GUIDE.md` | ✅ 严格遵守 — 已补充管线修复说明 |

## 4. 傻瓜验收指引

老大，解耦手术已完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！

具体验收步骤：
1. 在终端运行 `mathart`，选择 `[1] 工业量产` 或通过导演工坊的黄金连招 `[1] 阵列量产`
2. 观察终端输出：应看到 `[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列...`
3. 等待烘焙完成：应看到 `[✅ 工业烘焙完成] XX 帧高精度引导图序列已落盘`
4. 检查 outputs 文件夹：每个角色目录下的 `guide_baking/albedo/` 应包含帧间有明显差异的 PNG 序列
5. **关键验证**：不再出现 `frozen_guide_sequence` 错误，MSE 质检完美放行

## 5. 修改文件清单

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `mathart/factory/mass_production.py` | **核心修复** | `_animation_func_from_clip()` Bone→Joint 映射 + 度→弧度转换 + 根位移注入；渲染循环 `root_x/root_y` → `scale_x/scale_y` 传递；UX Banner 升级 |
| `docs/USER_GUIDE.md` | **文档同步** | 补充 SESSION-166 管线修复说明，更新 [1] 阵列量产描述 |
| `docs/RESEARCH_NOTES_SESSION_166.md` | **新增** | 外部研究笔记：Per-Frame State Hydration, DOD, Fail-Loud Validation |
| `scripts/session166_verify.py` | **新增** | 端到端验证脚本，确认修复效果 |
| `scripts/session166_diag.py` | **新增** | 诊断脚本，用于确认 Bone→Joint 命名空间断裂 |
| `SESSION_HANDOFF.md` | **更新** | 本文档 |
| `PROJECT_BRAIN.json` | **更新** | 任务状态更新 |
