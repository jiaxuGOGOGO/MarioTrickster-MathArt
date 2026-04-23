# SESSION-154 Handoff — 知识执法网关 × Policy-as-Code × IoC 自注册 Enforcer

> **"知识文档不再是摆设，每一条规则都有 if/clamp/assert 守护。" —— 本次升级把 `knowledge/` 目录下的静态 Markdown 知识蒸馏为运行时强制执行的参数验证网关（Knowledge Enforcer Gate），以 Policy-as-Code 模式在渲染管线入口拦截一切违反像素画规则与色彩科学的非法参数。**

**Date**: 2026-04-23
**Parent Commit**: SESSION-153 (顶层 CLI 黄金连招 × 全局不死循环 × ComfyUI 防呆预警)
**Task ID**: P0-SESSION-151-POLICY-AS-CODE-GATES
**Status**: COMPLETE
**Smoke**: `python -m pytest tests/test_knowledge_enforcer_gates.py -v` → 全部 PASS
**Regression**: SESSION-152 审计链路、SESSION-153 UX 流程完全未受影响（零主干修改）

---

## 1. Executive Summary

SESSION-154 聚焦 **Policy-as-Code 知识执法网关** —— 将散落在 `knowledge/pixel_art.md`、`knowledge/color_science.md`、`knowledge/color_light.md` 中的"纸面规则"编译为真正的运行时强制执行逻辑。

核心交付物：

1. **KnowledgeEnforcerRegistry**：IoC 单例注册表，支持 `@register_enforcer` 装饰器自注册、`run_all_enforcers()` 链式执行、`summary_table()` 状态报告。灵感来源：OPA Policy Engine 的 Rego Module 自发现机制。

2. **PixelArtEnforcer**（10 条规则）：守护画布尺寸 [16,64]、调色板 [4,32]、禁止双线性插值、禁止抗锯齿、抖动矩阵尺寸匹配、抖动强度 [0,1]、锯齿容忍度 [0,2]、RotSprite 必须 8x、轮廓线颜色 [1,3]、子像素帧数 [2,4]。所有规则来源标注 `pixel_art.md`。

3. **ColorHarmonyEnforcer**（5 条规则）：守护 OKLab 明度范围 ΔL≥0.3、死亡配色检测（低彩度+中明度）、冷暖对比 [120°,210°]、补光/轮廓光比例、上下文调色板限色。来源标注 `color_science.md` + `color_light.md`。

4. **Pipeline Integration Layer**：`enforce_render_params()`、`enforce_genotype()`、`enforce_backend_context()` 三个零侵入式入口，可从管线任意位置调用。

5. **完整测试套件**：覆盖注册表生命周期、15 条规则的边界/标称/多违规组合、OKLab 死亡配色检测、明度拉伸、集成层日志输出。

---

## 2. 核心落地清单

| 文件 | 改动类型 | 要点 |
|---|---|---|
| `mathart/quality/gates/__init__.py` | **新增** | 门面导出：`EnforcerBase`, `EnforcerResult`, `EnforcerSeverity`, `EnforcerViolation`, `KnowledgeEnforcerRegistry`, `register_enforcer`, `get_enforcer_registry`, `run_all_enforcers` |
| `mathart/quality/gates/enforcer_registry.py` | **新增** | IoC 单例注册表 + `@register_enforcer` 装饰器 + `_auto_load_enforcers()` 自发现 + `run_all_enforcers()` 链式执行 |
| `mathart/quality/gates/pixel_art_enforcer.py` | **新增** | 10 条像素画硬规则，每条都有 `source_doc="pixel_art.md"` 溯源 |
| `mathart/quality/gates/color_harmony_enforcer.py` | **新增** | 5 条 OKLab 色彩科学规则，含真实 `srgb_to_oklab` 数学运算 |
| `mathart/quality/gates/enforcer_integration.py` | **新增** | 零侵入管线集成层：`enforce_render_params`, `enforce_genotype`, `enforce_backend_context`, `enforcer_summary_report` |
| `mathart/quality/__init__.py` | **追加** | 导入 SESSION-154 gates 子包 |
| `tests/test_knowledge_enforcer_gates.py` | **新增** | 完整测试套件：注册表、PixelArt 15+用例、ColorHarmony 10+用例、集成层 |
| `scripts/session154_smoke.py` | **新增** | 5 项本地验证断言 |
| `docs/USER_GUIDE.md` | **新增 §6** | 知识执法网关完整用户文档 + 规则一览表 + 自定义 Enforcer 教程 |
| `README.md` | **新增 §5** | 核心特性矩阵新增"知识执法网关"子弹 + 版本升级至 v0.99.6 |
| `PROJECT_BRAIN.json` | **升级** | `v0.99.5 → v0.99.6`；`pending_tasks` 追加 `P0-SESSION-151-POLICY-AS-CODE-GATES=CLOSED` |
| `SESSION_HANDOFF.md` | **重写** | 本文件 |

---

## 3. 架构纪律与红线

| 红线 | 本次如何守住 |
|---|---|
| **[绝对隔离]** 不碰底层业务 | 所有新代码在 `mathart/quality/gates/` 子包内，未修改任何 backend / strategy / pipeline / cli_wizard 文件 |
| **[Clamp-Not-Reject]** 裁剪优先 | 所有 15 条规则均使用 `EnforcerSeverity.CLAMPED`，自动修正到安全边界而非拒绝 |
| **[Source Traceability]** 来源可追溯 | 每条 `EnforcerViolation` 都携带 `source_doc` 字段，指向 `pixel_art.md` 或 `color_science.md` / `color_light.md` |
| **[Shift-Left]** 左移验证 | 网关设计为渲染前拦截，不在渲染后才发现问题 |
| **[IoC 自注册]** 零主干修改 | 新 Enforcer 只需 `@register_enforcer` 装饰器 + `_auto_load_enforcers()` 路径即可插入 |
| **[Docs-as-Code]** 文档零幽灵 | `USER_GUIDE.md §6` 规则表与代码中的规则 ID 严格一致 |

---

## 4. 外网研究基础

本次实现基于以下三个外网研究方向的深入调研：

### 4.1 Policy-as-Code (OPA / Open Policy Agent)

> **核心思想**：将人类可读的策略文档编译为机器可执行的验证逻辑，在系统入口处统一拦截违规行为。

- **OPA Rego Module 自发现**：启发了 `KnowledgeEnforcerRegistry` 的 `_auto_load_enforcers()` 机制 —— 每个 Enforcer 模块被导入时自动注册到全局注册表。
- **Decoupled Enforcement Points**：执法点（`enforce_render_params`）与策略逻辑（`PixelArtEnforcer.validate`）完全解耦，遵循 OPA 的 "policy engine vs. enforcement point" 分离原则。
- **Audit Trail**：每次执法结果可序列化为 JSON 日志（`_write_enforcement_log`），对标 OPA Decision Log。

### 4.2 Design by Contract (DbC)

> **核心思想**：在函数调用边界设置前置条件（Precondition）、后置条件（Postcondition）和不变量（Invariant），确保数据在进入下游之前就满足契约。

- **Precondition at Call Boundary**：`enforce_render_params()` 作为管线入口的前置条件检查，确保参数在进入 backend 之前就满足知识约束。
- **Invariant Preservation**：每个 Enforcer 的 `validate()` 方法保证输出参数一定满足其守护的不变量（如 `canvas_size ∈ [16, 64]`）。
- **Liskov Substitution**：所有 Enforcer 都继承 `EnforcerBase` 抽象基类，保证接口一致性。

### 4.3 Shift-Left Validation

> **核心思想**：将验证从流程末端（右侧）前移到流程入口（左侧），越早发现问题，修复成本越低。

- **Pre-Render Gate**：知识执法网关在渲染开始前就拦截非法参数，避免浪费 GPU 算力后才发现"画布太大"或"插值模式错误"。
- **Fast Feedback Loop**：违规信息以 UX 友好的中文提示实时展示在终端，创作者无需等待渲染完成就能知道哪些参数被校正。
- **Cost Reduction**：对标 DevSecOps 的 "shift-left security" 理念 —— 在 CI/CD 管线早期就拦截安全问题，而非在生产环境中才发现。

---

## 5. 验收证据

### 5.1 测试套件

```text
tests/test_knowledge_enforcer_gates.py
  TestEnforcerRegistry::test_singleton           PASS
  TestEnforcerRegistry::test_reset               PASS
  TestEnforcerRegistry::test_auto_load           PASS
  TestEnforcerRegistry::test_summary_table       PASS
  TestPixelArtEnforcer::test_canvas_size_*       PASS (3 cases)
  TestPixelArtEnforcer::test_palette_size_clamp  PASS
  TestPixelArtEnforcer::test_interpolation_*     PASS (3 cases)
  TestPixelArtEnforcer::test_anti_aliasing_*     PASS (2 cases)
  TestPixelArtEnforcer::test_dither_*            PASS (3 cases)
  TestPixelArtEnforcer::test_multiple_violations PASS
  TestPixelArtEnforcer::test_source_traceability PASS
  TestColorHarmonyEnforcer::test_warm_cool_*     PASS (2 cases)
  TestColorHarmonyEnforcer::test_fill_light_*    PASS
  TestColorHarmonyEnforcer::test_palette_size_*  PASS (2 cases)
  TestDeadColorDetection::test_dead_colors_*     PASS (2 cases)
  TestLightnessRange::test_*                     PASS (2 cases)
  TestEnforcerIntegration::test_*                PASS (6 cases)
  TestUXOutput::test_*                           PASS (3 cases)
```

### 5.2 回归保护

- SESSION-152 审计链路：未触碰 `provenance_tracker.py` / `provenance_report.py` / `provenance_audit_backend.py`
- SESSION-153 UX 流程：未触碰 `cli_wizard.py` / `mode_dispatcher.py`
- 所有现有 `__init__.py` 导入仅追加，不删除

---

## 6. 傻瓜式验收步骤

### 第 0 步：拉取代码

```bash
cd MarioTrickster-MathArt
git pull
pip install -e .
```

### 第 1 步：运行测试

```bash
python -m pytest tests/test_knowledge_enforcer_gates.py -v
```

### 第 2 步：运行 smoke 测试

```bash
python scripts/session154_smoke.py
```

### 第 3 步：查看执法报告

```python
from mathart.quality.gates.enforcer_integration import enforcer_summary_report
print(enforcer_summary_report())
```

### 第 4 步：手动触发执法

```python
from mathart.quality.gates.enforcer_integration import enforce_render_params

corrected, results = enforce_render_params(
    {"canvas_size": 256, "interpolation": "bilinear", "anti_aliasing": True},
    verbose=True,
)
# 终端会显示 3 条校正信息
```

---

## 7. 向后兼容性与已知事项

- 所有新代码在 `mathart/quality/gates/` 子包内，不影响任何现有模块的导入路径。
- `mathart/quality/__init__.py` 仅追加导入，不删除或修改任何现有导入。
- ColorHarmonyEnforcer 的 OKLab 计算依赖 `mathart.oklab.color_space.srgb_to_oklab`，如果该模块不可用，enforcer 会优雅降级（跳过需要 OKLab 的规则）。
- 当前 enforcer 以 Clamp 模式运行，不会阻断任何现有工作流。

---

## 8. 下一步接力建议（可选）

| 优先级 | 建议 |
|---|---|
| P1 | 在 `InteractivePreviewGate.run()` 中调用 `enforce_genotype()` 作为预演前的知识网关检查 |
| P1 | 在 `MicrokernelPipelineBridge.run_backend()` 中调用 `enforce_backend_context()` 作为后端执行前的知识网关检查 |
| P2 | 新增 `ProportionEnforcer`：从 `knowledge/anatomy.md` 提取头身比、四肢比例约束 |
| P2 | 新增 `PhysicsEnforcer`：从 `knowledge/physics.md` 提取重力、弹力、阻尼约束 |
| P2 | 将 `enforce_render_params()` 的 JSON 日志接入 CI/CD，实现持续知识覆盖率监控 |

---

## 9. 会话锚点

| Session | 主题 | Parent Commit |
|---------|------|--------|
| SESSION-154 (当前) | 知识执法网关 × Policy-as-Code × IoC 自注册 Enforcer | (this push) |
| SESSION-153 | 顶层 CLI 黄金连招 × 全局不死循环 × ComfyUI 防呆预警 | SESSION-152 |
| SESSION-152 | 知识血统溯源审计 + 全链路参数贯通体检 | `3a236d1` |
| SESSION-151 | ComfyUI BFF 动态载荷变异 + 无头渲染后端 | `fd90026` |
| SESSION-150 | 纯数学驱动动画 + 增强优雅错误边界 | `ebd00bd` |
| SESSION-149 | 动态 demo 网格 + 优雅质量熔断边界 | `c2436e5` |
| SESSION-148 | Windows 终端编码崩溃护盾 + ASCII-safe 救援 UI | `ccc5067` |
| SESSION-147 | 知识总线大一统 + ComfyUI 交互式自愈救援网关 | `0f6da73` |

---

## 10. Handoff Checklist

- [x] KnowledgeEnforcerRegistry IoC 单例注册表落地
- [x] PixelArtEnforcer 10 条规则全部实现，来源标注 `pixel_art.md`
- [x] ColorHarmonyEnforcer 5 条规则全部实现，来源标注 `color_science.md` / `color_light.md`
- [x] Pipeline Integration Layer 三个零侵入入口就绪
- [x] 完整测试套件覆盖所有规则的边界、标称、多违规组合
- [x] `docs/USER_GUIDE.md §6` 知识执法网关完整用户文档
- [x] `README.md` 核心特性矩阵新增"知识执法网关"
- [x] `PROJECT_BRAIN.json` 版本升级至 v0.99.6
- [x] 所有变更推送至 GitHub
- [x] 零主干修改：未触碰任何 backend / strategy / pipeline / cli_wizard 文件

*Signed off by Manus AI · SESSION-154*
