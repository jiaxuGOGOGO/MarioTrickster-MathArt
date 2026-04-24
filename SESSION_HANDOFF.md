# SESSION HANDOFF

**Current Session:** SESSION-190
**Date:** 2026-04-24
**Status:** LANDED
**Priority:** P0
**Previous:** SESSION-189 (潜空间治愈 + 日式作画节奏抽帧锁)

---

## 1. 核心目标 (Core Objectives)

- [x] **P0-SESSION-190-MODAL-DECOUPLING-LOOKDEV-IO-SANITIZATION**：模态解耦 + LookDev 极速打样 + 双引号粉碎机。
- [x] **模态解耦 `detect_dummy_mesh`**：检测 `pseudo_3d_shell` 生成的假人圆柱体白模，当检测到时触发完整的 Appearance-Motion Decoupling 流程。
- [x] **语义兜底 `hydrate_prompt`**：当用户 Prompt 为空（< 10 字符）且系统退化为白模时，强制注入高质量 3A 角色提示词 `SEMANTIC_HYDRATION_POSITIVE` / `SEMANTIC_HYDRATION_NEGATIVE`。
- [x] **ComfyUI 模态解耦 `force_decouple_dummy_mesh_payload`**：纯 `class_type` 语义扫描，强制 `KSampler*.denoise=1.0`、`ACN_SparseCtrl*.strength=0.0`、`ControlNetApply*(Depth/Normal).strength=0.45`、正负提示词注入。**零节点 ID 硬编码**。
- [x] **LookDev 单动作极速打样**：黄金连招 V2 新增 `[4] ⚡ 单一动作打样`，用户可从已注册动作（idle/walk/run/jump/fall/hit）中挑选一个进行极速烘焙+渲染测试。通过 `action_filter` 参数注入生产管线。
- [x] **双引号粉碎机**：所有用户路径输入处（视觉临摹 GIF 路径、蓝图路径）强制执行 `.strip('"').strip("'").strip()` 净化。路径无效时红字警告并要求重新输入，绝对禁止静默降级。
- [x] **外网参考研究落地**：MoSA / MCM / DC-ControlNet / SparseCtrl / ComfyUI #245 / ComfyUI #1077 / OWASP / Katana / UE Animation Blueprint / "Release It!"。笔记见 `docs/RESEARCH_NOTES_SESSION_190.md`。
- [x] **DaC 文档契约**：USER_GUIDE.md 新增 Section 20、SESSION_HANDOFF.md 覆写为本文档、PROJECT_BRAIN.json 追加 SESSION-190 条目。
- [x] **测试验收**：新增 `tests/test_session190_modal_decoupling_and_lookdev.py`。

## 2. 大白话汇报：老大，模态解耦已生效，LookDev 极速打样已上线，双引号粉碎机已部署！

### 🔓 模态解耦 · 假人白模不再污染 AI 出图

老大，以前当物理引擎退化成圆柱体白模（`pseudo_3d_shell`）时，那个白色/灰色的圆柱体 Albedo 会通过 SparseCtrl RGB 引导通道直接灌进扩散模型，模型就锁死在圆柱体的色块上，生成出来的全是对称的方块怪物。

现在 `detect_dummy_mesh()` 会在管线入口检测假人，一旦确认就启动三重解耦：
1. **denoise → 1.0**：完全从纯噪声开始生成，彻底忽略圆柱体的色彩信息
2. **RGB ControlNet strength → 0.0**：杀死所有 SparseCtrl RGB 引导，圆柱体色块再也进不来
3. **Depth/Normal strength → 0.45**：只保留骨架动势引导，让模型知道"这里有个人形在做动作"

同时 `hydrate_prompt()` 会检测用户 Prompt 是否为空，如果是就自动注入 `(masterpiece, best quality, ultra-detailed:1.2), 1boy, handsome cyber-ninja superhero, dynamic action pose, vivid colors, clear background` 这样的 3A 级角色提示词，让模型有方向感。

### ⚡ LookDev 极速打样 · 不用等全阵列就能看效果

老大，以前想看一个动作的 AI 渲染效果，必须等全部 6 个动作（idle/walk/run/jump/fall/hit）全部烘焙完才行。现在黄金连招菜单多了 `[4] ⚡ 单一动作打样`，选完后输入 `jump`，系统只烘焙+渲染这一个动作，几秒内就能看到结果。满意了再选 [1] 或 [2] 全量产。

工业参考：Foundry Katana 的 LookDev 工作流就是这么干的——先单资产迭代到满意，再全场景渲染。UE 的 Animation Blueprint 也允许单独测试单个动画状态。

### 🔧 双引号粉碎机 · Windows 路径再也不会出错

老大，Windows 用户从资源管理器复制路径时，系统会自动加上双引号（如 `"C:\Users\xxx\ref.gif"`），这个双引号会导致 Python 的 `Path()` 找不到文件。现在所有路径输入处都会自动剥离引号，而且如果路径不存在，会红字告警要求重新输入，绝不会静默降级让用户一脸懵。

## 3. 本次修改的全部文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mathart/cli_wizard.py` | **修改** | 新增 `GOLDEN_HANDOFF_OPTION_LOOKDEV` 常量、`[4]` 菜单选项处理、`action_filter` 参数传递、双引号粉碎机（视觉临摹路径+蓝图路径） |
| `mathart/core/anti_flicker_runtime.py` | **修改** | 追加 `SEMANTIC_HYDRATION_*` / `DECOUPLED_*` 常量、`detect_dummy_mesh()` / `hydrate_prompt()` / `force_decouple_dummy_mesh_payload()` 函数，扩展 `__all__` |
| `docs/USER_GUIDE.md` | **修改** | 黄金连招 V2 菜单新增 `[4]` 选项说明、追加 Section 20 完整使用指南 |
| `docs/RESEARCH_NOTES_SESSION_190.md` | **新增** | 外网十大参考的逐条引用与工程映射 |
| `tests/test_session190_modal_decoupling_and_lookdev.py` | **新增** | SESSION-190 专属 pytest 套件 |
| `SESSION_HANDOFF.md` | **覆写** | 本文档 |
| `PROJECT_BRAIN.json` | **修改** | 新增 SESSION-190 条目与 `modal_decoupling_contract` 字段 |

## 4. 严格红线遵守情况

| 红线 | 证据 |
|------|------|
| 不碰 SESSION-189 三条硬锚常量 | `MAX_FRAMES=16` / `LATENT_EDGE=512` / `NORMAL_MATTE_RGB=(128,128,255)` 未修改 |
| 严禁节点 ID 硬编码 | `force_decouple_dummy_mesh_payload` 仅按 `class_type` 前缀匹配 |
| 路径净化不可绕过 | 所有 `standard_text_prompt` 获取的路径均经过 `.strip('"').strip("'").strip()` |
| 语义兜底不可关闭 | `hydrate_prompt` 在 vibe/style_prompt 均 < 10 字符时强制注入 |
| 不破坏既有 SESSION-175/178/189 约束 | CFG ceiling、Normal/Depth matte、anime rhythm 均保持不变 |
| 严禁触碰代理环境变量 | 新加代码全部零引用 `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` |

## 5. 外网参考研究落地情况

| 参考 | 工程映射 |
|------|---------|
| MoSA (Wang et al., 2025) | 结构-外观解耦 → `detect_dummy_mesh` + `force_decouple_dummy_mesh_payload` |
| MCM (NeurIPS 2024) | 运动-外观解耦蒸馏 → denoise=1.0 强制全噪声生成 |
| DC-ControlNet (2025) | 多条件解耦 → RGB strength=0.0 / Depth-Normal strength=0.45 分离 |
| SparseCtrl (Guo et al., 2023) | 稀疏控制信号 → `ACN_SparseCtrl*` 节点 strength 归零 |
| ComfyUI-AnimateDiff-Evolved #245 | SparseCtrl 强度控制实践 → strength 参数精确控制 |
| ComfyUI #1077 | denoise=1.0 行为验证 → 确认全噪声可忽略输入色彩 |
| OWASP Input Validation | 输入净化 → `.strip('"').strip("'").strip()` + 路径存在性校验 |
| Foundry Katana LookDev | 单资产迭代 → LookDev 单动作打样模式 |
| UE Animation Blueprint | 单状态测试 → `action_filter` 参数注入 |
| "Release It!" Fail-Fast | 快速失败 → 路径无效时红字警告而非静默降级 |

## 6. 傻瓜验收指引

```bash
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt
pip install -e .
PYTHONPATH=. python3 -m pytest tests/test_session190_modal_decoupling_and_lookdev.py -v
# 预期：全部通过
```

功能性 smoke：

```python
from mathart.core.anti_flicker_runtime import (
    detect_dummy_mesh,
    hydrate_prompt,
    force_decouple_dummy_mesh_payload,
    DECOUPLED_RGB_STRENGTH,
    DECOUPLED_DENOISE,
)

# 检测假人白模
ctx = {"_pseudo3d_shell_active": True, "_dummy_cylinder_mesh": True}
assert detect_dummy_mesh(ctx) is True

# 语义兜底
ctx2 = {"vibe": "", "style_prompt": ""}
result = hydrate_prompt(ctx2)
assert len(result.get("style_prompt", "")) > 10  # 注入了 3A 提示词

# 模态解耦
workflow = {
    "1": {"class_type": "KSampler", "inputs": {"denoise": 0.75}},
    "2": {"class_type": "ACN_SparseCtrlRGBPreprocessor", "inputs": {"strength": 0.8}},
}
report = force_decouple_dummy_mesh_payload(workflow)
assert workflow["1"]["inputs"]["denoise"] == 1.0
assert workflow["2"]["inputs"]["strength"] == 0.0
```

## 7. 下一步建议 (Next Session Recommendations)

1. **ComfyUI 端到端集成测试**：在真实 ComfyUI 环境中验证 `force_decouple_dummy_mesh_payload` 的实际效果。
2. **LookDev AI 渲染模式扩展**：当前 LookDev 支持 CPU 烘焙 + AI 渲染，可进一步增加 A/B 对比模式。
3. **动态 Prompt 模板库**：扩展 `SEMANTIC_HYDRATION_POSITIVE` 为可配置的模板库，支持不同风格的兜底提示词。
4. **路径自动补全**：在 CLI 中实现路径 Tab 补全，进一步降低输入错误率。
5. 把 `session190_decoupling_report` 写进 `ArtifactManifest`，让资产大管家的"真理查账"视图可以展示模态解耦发生了什么。

---

**执行者**: Manus AI (SESSION-190)
**研究笔记**: `docs/RESEARCH_NOTES_SESSION_190.md`
**前序 SESSION**: SESSION-189 (潜空间治愈 + 日式作画节奏抽帧锁)
