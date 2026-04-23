> **"技术债不过夜，管线零缺陷。" —— SESSION-157 精准靶向修复了全量回归测试中暴露的 3 个远端 API 契约断裂点，实现 Provenance Audit 后端 LSP 签名对齐、Graduate CLI DTO 字段同步、L-System PlantPresets Facade 官方转正。所有修复均为皮外伤级别（变量改名、签名适配、导入路径），零核心算法修改。**
>
> **"下游适配上游，绝不反向修改调度器。" —— 严格遵循里氏替换原则（LSP），修改出问题的 Backend 本身来适应总线契约，pipeline_bridge.py 零改动。**

**Date**: 2026-04-23
**Parent Commit**: SESSION-156 (Knowledge Triage & Dedup Funnel)
**Task ID**: P0-SESSION-155-TECH-DEBT-ERADICATION
**Status**: COMPLETE
**Smoke**: `python -c "from mathart.sdf.lsystem import PlantPresets; t=PlantPresets.oak_tree(seed=42); print(len(t.generate(iterations=3)))"` → 正整数输出
**Regression**: SESSION-156 知识分诊、SESSION-155 Auto-Compiler、SESSION-154 知识网关、SESSION-153 UX 流程完全未受影响

---

## 1. Executive Summary

SESSION-157 聚焦 **P0-SESSION-155-TECH-DEBT-ERADICATION（全量测试遗留 Bug 靶向修复与远端 API 契约官方转正）** —— 精准修复全量端到端回归测试中暴露的 3 个管线阻塞 Bug，实现零缺陷（Zero-Defect）对齐。

核心交付物：

1. **Provenance Audit Backend LSP 签名对齐**：`execute()` 方法从 keyword-only (`*`) 签名重构为接受位置参数 `context` dict + `**kwargs`，完美适配 `pipeline_bridge.py` 的 `instance.execute(context)` 统一调度契约。向后兼容：`run_standalone_audit()` 等现有调用方以 keyword 方式传参仍然正常工作。
2. **Graduate CLI DTO 字段同步**：`cli.py` 中的打印逻辑全面对齐最新的 `GraduationReport` / `GraduationResult` 数据类字段：`total_checked` → `total`，`promoted` → `succeeded`，`new_status` → `to_status`，`message` → `notes`/`summary()`。
3. **L-System PlantPresets Facade 官方转正**：`mathart/sdf/__init__.py` 正式导出 `LSystem` 和 `PlantPresets`，消除 Facade 暴露断层。`docs/USER_GUIDE.md` 新增第 8 章"L-System 程序化植物生成"，含完整 API 示例和傻瓜验收指引。

---

## 2. 核心落地清单

| 文件 | 改动类型 | 要点 |
|---|---|---|
| `mathart/core/provenance_audit_backend.py` | **修复** | `execute()` 签名从 `(self, *, knowledge_bus=..., ...)` 改为 `(self, context=None, **kwargs)`；内部通过 `ctx_merged = {**context, **kwargs}` 合并两种调用方式；所有参数通过 `.get()` 安全提取 |
| `mathart/evolution/cli.py` | **修复** | 5 处属性引用同步：`report.total_checked` → `report.total`，`report.promoted` → `report.succeeded`，`r.new_status` → `r.to_status`，`r.message` → `r.notes`/`r.summary()`，`report.skipped` → 计算表达式 |
| `mathart/sdf/__init__.py` | **升级** | 新增 `from .lsystem import LSystem, PlantPresets`；`__all__` 增加 `"LSystem"`, `"PlantPresets"` |
| `docs/USER_GUIDE.md` | **升级** | 新增第 8 章"L-System 程序化植物生成 (PlantPresets 静态工厂)"，含 4 种植物预设示例、顶层包导入示例、傻瓜验收指引 |
| `PROJECT_BRAIN.json` | **升级** | `v0.99.8`；`SESSION-157`；新增 `P0-SESSION-155-TECH-DEBT-ERADICATION=CLOSED` |
| `SESSION_HANDOFF.md` | **重写** | 本文件 |

---

## 3. 三个 Bug 的精准病理分析与修复

### Bug 1: Provenance Audit Backend 签名 (LSP 违规)

**病灶**：`ProvenanceAuditBackend.execute()` 使用了 `*` keyword-only 签名，而 `pipeline_bridge.py` 第 209 行以 `instance.execute(context)` 传入位置参数，导致 `TypeError: execute() takes 1 positional argument but 2 were given`。

**根因**：违反里氏替换原则（LSP）—— 所有注册到 BackendRegistry 的后端必须遵守总线契约 `execute(context: dict) -> manifest`，但 Provenance Audit 后端的签名与契约不兼容。

**修复**：将签名改为 `def execute(self, context=None, **kwargs)`，内部通过 `ctx_merged = {**context, **kwargs}` 合并位置参数和关键字参数，然后用 `.get()` 安全提取每个参数。这样既适配了总线的位置参数调度，又保持了 `run_standalone_audit()` 等现有 keyword 调用的向后兼容。

**红线遵守**：`pipeline_bridge.py` 零改动。

### Bug 2: Graduate CLI 属性名断层 (DTO 不同步)

**病灶**：`GraduationReport` 和 `GraduationResult` 数据类在之前的重构中已将属性名更新为 `total`、`succeeded`、`to_status`，但 `cli.py` 的打印逻辑仍引用旧属性名 `total_checked`、`promoted`、`new_status`、`message`，导致运行时 `AttributeError`。

**根因**：违反 API 契约演进原则 —— 底层 DTO 重构后，上层消费端（CLI 视图层）未同步更新。

**修复**：逐一替换所有陈旧属性引用，对齐最新 DTO 字段。`report.skipped` 改为计算表达式 `report.total - report.succeeded - report.failed`（因为 `GraduationReport` 没有 `skipped` 属性）。`r.message` 改为 `r.notes`（列表）或 `r.summary()`（格式化字符串）。

**红线遵守**：`graduation.py` 数据类零改动，仅修改 CLI 视图层。

### Bug 3: L-System PlantPresets Facade 暴露断层

**病灶**：`PlantPresets` 静态工厂类已在 `mathart/sdf/lsystem.py` 中实现，但未在 `mathart/sdf/__init__.py` 中导出，导致 `from mathart.sdf import PlantPresets` 失败。

**根因**：违反 Facade 模式原则 —— 底层库重构为静态工厂后，包级别的 Facade 入口未同步更新。

**修复**：在 `mathart/sdf/__init__.py` 中添加 `from .lsystem import LSystem, PlantPresets` 并更新 `__all__`。同时在 `docs/USER_GUIDE.md` 中新增第 8 章，提供完整的 API 使用示例。

**红线遵守**：`lsystem.py` 核心数学生成逻辑零改动。

---

## 4. 架构纪律与红线

| 红线 | 本次如何守住 |
|---|---|
| **[严禁修改核心算法]** L-System 数学逻辑纯净 | `lsystem.py` 零改动，仅在 `__init__.py` 增加导出 |
| **[严禁反向修改调度器]** pipeline_bridge.py 不可动 | `pipeline_bridge.py` 零改动，修改下游 Backend 适配总线 |
| **[DTO 同步]** CLI 视图层对齐数据模型 | 5 处属性引用全部同步为最新字段名 |
| **[Facade 暴露]** PlantPresets 包级可达 | `__init__.py` 正式导出，`from mathart.sdf import PlantPresets` 畅通 |
| **[UX 零退化]** 终端交互体验不变 | 所有修复为底层排雷，终端向导和交互循环完全不受影响 |
| **[DaC 文档同步]** 代码变则文档变 | `USER_GUIDE.md` 新增第 8 章 L-System API 示例 |

---

## 5. 傻瓜验收指引

**老大，热修复已全量在云端官方转正。您可以直接运行以下命令，亲眼验证绝不再报错了！**

### 验收 1：Provenance Audit 后端不再崩溃

```python
from mathart.core.provenance_audit_backend import ProvenanceAuditBackend

backend = ProvenanceAuditBackend()
# 模拟 pipeline_bridge 的位置参数调度方式
result = backend.execute({"session_id": "TEST-157"})
print(f"Verdict: {result.health_verdict}")  # 应正常输出，不再报 TypeError
```

### 验收 2：Graduate CLI 不再报 AttributeError

```bash
python3 -m mathart.evolution.cli graduate --audit
```

预期：正常输出审计报告，不再报 `AttributeError: 'GraduationReport' object has no attribute 'total_checked'`。

### 验收 3：L-System PlantPresets 导入畅通

```python
from mathart.sdf import PlantPresets
from mathart.sdf.lsystem import LSystem, PlantPresets

tree = PlantPresets.oak_tree(seed=42)
segments = tree.generate(iterations=3)
print(f"生成了 {len(segments)} 个植物片段")
img = tree.render(32, 32)
print(f"渲染尺寸: {img.size}")
```

### 验收 4：自动化测试

```bash
python -m pytest tests/test_lsystem.py -v
python -m pytest tests/test_provenance_audit.py -v
```

---

## 6. 外网参考研究成果

本次修复严格对标以下工业界与学术界最佳实践：

| 设计原则 | 参考来源 | 在本项目中的应用 |
|---|---|---|
| **里氏替换原则 (LSP)** | Barbara Liskov & Jeannette Wing, 1994; SOLID 原则 | Backend 子类的 `execute()` 签名必须兼容总线基类契约 |
| **API 契约演进** | Martin Fowler, PoEAA; Interface Evolution Patterns (Lübke et al., 2019) | DTO 重构后所有消费端必须同步更新 |
| **Facade 模式** | GoF Design Patterns (Gamma et al., 1994); Refactoring.Guru | 底层库重构后包级 Facade 入口必须同步暴露 |

---

## 7. 下一步建议：游戏引擎接入准备 (SESSION-158+)

打通本层闭环后，若要无缝接入**一键打包导出至游戏引擎（如 Unity URP / Godot）的终极工业化组装管线**，当前架构还需要做以下微调准备：

| 优先级 | 任务 | 说明 |
|---|---|---|
| **P0** | **Export Backend 注册** | 新增 `UnityURPExportBackend` 和 `GodotExportBackend`，通过 `@register_backend` 装饰器无损挂载到 BackendRegistry，声明 `artifact_family="game_engine_package"` |
| **P0** | **Asset Manifest 标准化** | 当前 `ArtifactManifest` 需要扩展 `engine_target` 字段（`unity_urp` / `godot_4`），以及 `texture_format`（`.png` / `.exr`）、`atlas_layout`（`grid` / `packed`）等引擎特定元数据 |
| **P1** | **Sprite Atlas 打包器** | 实现 `SpriteAtlasPacker`，将多帧精灵图按引擎要求的 Atlas 格式打包（Unity 用 `.spriteatlas`，Godot 用 `AtlasTexture`） |
| **P1** | **Shader 导出适配层** | 当前 GLSL shader 需要转译为 Unity ShaderLab / Godot Shader Language，可通过 Facade 模式封装转译器 |
| **P2** | **Animation Clip 导出** | 将内部动画数据（帧序列、缓动曲线）导出为 Unity AnimationClip `.anim` 或 Godot AnimationPlayer 资源 |
| **P2** | **CI/CD 集成测试** | 在 GitHub Actions 中增加 Unity/Godot headless 导入测试，验证导出资产在目标引擎中可正常加载 |

### 架构微调要点

1. **Export Backend 遵循 IoC 注册模式**：新的导出后端必须通过 `@register_backend` 自注册，严禁在 Orchestrator 中写死 if/else。
2. **Manifest 扩展遵循 Open/Closed 原则**：通过 `extra_metadata: dict` 字段扩展引擎特定信息，不修改基类 `ArtifactManifest` 的核心字段。
3. **Shader 转译遵循 Facade 模式**：封装 `ShaderTranspiler` Facade，内部按目标引擎分派到具体转译器，上游只需调用 `transpile(shader, target="unity_urp")`。

---

## 8. 文件变更总览

```
mathart/core/provenance_audit_backend.py  — execute() 签名 LSP 对齐
mathart/evolution/cli.py                  — 5 处 DTO 属性名同步
mathart/sdf/__init__.py                   — PlantPresets/LSystem 导出
docs/USER_GUIDE.md                        — 新增第 8 章 L-System API
PROJECT_BRAIN.json                        — v0.99.8, SESSION-157
SESSION_HANDOFF.md                        — 本文件（重写）
```
