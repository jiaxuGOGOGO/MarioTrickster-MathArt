# SESSION-152: Knowledge Provenance Audit — 全链路知识血统溯源与参数贯通审计

> **"每一个浮点数都必须交代自己的来历。不是来自蒸馏知识，就必须诚实标红为'代码硬编码死区'。" —— 打通知识总线→参数推演→渲染管线的全链路可解释性闭环。**

**Date**: 2026-04-23
**Status**: COMPLETE — 9/9 tests PASS
**Parent Commit**: `fd90026` (SESSION-151)
**Task ID**: P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT
**Smoke**: `tests/test_provenance_audit.py` → 9/9 PASSED（Singleton 1 + Snapshot 1 + Classification 1 + Dangling 1 + Report 1 + Sidecar 1 + Registry 1 + E2E 1 + Integration 1）

---

## 1. Executive Summary

SESSION-152 实现了**完整的端到端知识血统溯源与参数贯通审计系统** —— 这是 MarioTrickster-MathArt 项目中首次对"知识总线到底有没有真正驱动参数推演"进行全链路可解释性审计。三个工业级模块落地于 `mathart/core/`，严格遵循**非侵入式旁路拦截器 (Sidecar/Interceptor Pattern)** 原则，通过 `@register_backend` 完全融入现有 Registry Pattern：

1. **KnowledgeLineageTracker** (`provenance_tracker.py`) — 知识血统追踪器（单例 + 线程安全）
2. **ProvenanceReportGenerator** (`provenance_report.py`) — 终极审计报告生成器（终端体检表 + JSON 落盘）
3. **ProvenanceAuditBackend** (`provenance_audit_backend.py`) — Registry-native 审计后端插件

所有三条 **SESSION-152 反模式红线** 均已强制执行并通过测试：

| 红线 | 防护机制 | 测试用例 |
|---|---|---|
| [防假账红线] 严禁伪造知识来源 | 实际查询 RuntimeDistillationBus 状态 | `test_lineage_classification` |
| [防破坏红线] 严禁修改任何浮点计算 | 只读旁路观测，原始 dict 不变 | `test_non_intrusive_sidecar` |
| [防断流红线] 检测悬空未使用参数 | Backend 消费检查点 + 差集检测 | `test_dangling_detection` |

### 审计结果摘要（诚实暴露）

| 指标 | 数值 | 占比 |
|---|---|---|
| 总追踪参数 | 18 | 100% |
| **知识驱动** (Knowledge-Driven) | 4 | 22.2% |
| **硬编码死区** (Heuristic Fallback) | **9** | **50.0%** |
| 语义启发式 (Vibe Heuristic) | 5 | 27.8% |
| 用户覆写 (User Override) | 0 | 0.0% |
| 蓝图继承 (Blueprint Inherited) | 0 | 0.0% |
| 悬空参数 (Dangling) | 0 | 0.0% |
| **健康判定** | **PARTIAL** | — |

---

## 2. What Was Built

### 2.1 KnowledgeLineageTracker (`mathart/core/provenance_tracker.py`)

**架构**: OpenLineage-aligned 知识血统追踪器

追踪器实现了**全链路参数溯源**策略，通过三阶段审计协议追踪每个参数的来源：

**核心设计决策**：

- **单例 + 线程安全 (Singleton + Thread-Local)**：使用 `threading.local()` 确保每个会话有独立的审计上下文，同时全局共享追踪器实例。
- **知识总线快照 (Knowledge Bus Snapshot)**：在审计开始时对 `RuntimeDistillationBus` 做只读快照，记录编译模块数、约束总数、知识文件列表。
- **六级溯源分类 (Six-Level Provenance Classification)**：

| 溯源类型 | 含义 | 标记 |
|---|---|---|
| `KNOWLEDGE_DRIVEN` | 知识总线约束范围内 + 有对应规则 | 合规 |
| `KNOWLEDGE_CLAMPED` | 被知识总线钳位修正 | 合规（已修正） |
| `VIBE_HEURISTIC` | 语义氛围词启发式调整 | ⚠️ 未经知识验证 |
| `USER_OVERRIDE` | 用户显式覆写 | 用户意图 |
| `BLUEPRINT_INHERITED` | 从蓝图文件继承 | 蓝图溯源 |
| `HEURISTIC_FALLBACK` | **代码硬编码死区** | **⚠️ AI偷懒断点** |

- **悬空参数检测 (Dangling Parameter Detection)**：比较 Intent 阶段的参数集与 Backend 消费的参数集，差集即为"半路丢失"的废弃参数。

### 2.2 ProvenanceReportGenerator (`mathart/core/provenance_report.py`)

**架构**: XAI-aligned 可解释性审计报告生成器

报告生成器消费 `ProvenanceAuditContext` 并产出两种格式：

1. **终端体检表**：使用 `tabulate` 库生成 CJK 对齐的四列审计表格
2. **JSON 审计日志**：落盘至 `logs/knowledge_audit_trace.json`，包含完整的知识快照、血统记录、摘要统计和死区清单

**报告列定义**：

| 列 | 含义 |
|---|---|
| 最终应用参数 | 参数键名（如 `physics.bounce`） |
| 实际推演数值 | 最终浮点值 |
| 驱动该值的知识来源 | 溯源分类 + 知识文件路径 |
| 具体推演原由 | 人类可读的推演原因 |

**[防假账红线]** 硬编码死区参数以 `⚠️ [Heuristic Fallback / 代码硬编码死区]` 标红显示，报告末尾单独列出完整死区清单。

### 2.3 ProvenanceAuditBackend (`mathart/core/provenance_audit_backend.py`)

**架构**: Registry-native 旁路审计后端

后端通过 `@register_backend(BackendType.PROVENANCE_AUDIT)` 自注册，可被 `BackendRegistry` 自动发现。支持两种运行模式：

1. **管线内模式**：作为管线最后一步执行，消费上游 Intent + Backend 状态
2. **独立模式**：`run_standalone_audit()` 函数可直接从 CLI 调用

---

## 3. 审计发现：诚实暴露的偷懒断点

### 3.1 硬编码死区清单（AI偷懒真凶）

以下 **9 个参数** 完全由代码 `dataclass` 默认值驱动，未接通任何外部蒸馏知识：

| 参数 | 硬编码值 | 所属模块 | 诊断 |
|---|---|---|---|
| `physics.gravity` | 9.81 | physics | 经典物理常数，但游戏物理应由知识规则定制 |
| `proportions.head_ratio` | 0.25 | proportions | 角色比例应由解剖学知识驱动 |
| `proportions.body_ratio` | 0.50 | proportions | 角色比例应由解剖学知识驱动 |
| `proportions.limb_ratio` | 0.25 | proportions | 角色比例应由解剖学知识驱动 |
| `proportions.scale` | 1.00 | proportions | 缩放因子应由游戏设计知识驱动 |
| `animation.frame_rate` | 12.0 | animation | 帧率应由动画原则知识驱动 |
| `animation.ease_in` | 0.30 | animation | 缓入曲线应由动画原则知识驱动 |
| `animation.ease_out` | 0.30 | animation | 缓出曲线应由动画原则知识驱动 |
| `animation.cycle_frames` | 24.0 | animation | 循环帧数应由动画原则知识驱动 |

### 3.2 知识驱动参数（合规区）

以下 **4 个参数** 确认由知识总线约束验证：

| 参数 | 值 | 知识模块 | 约束范围 |
|---|---|---|---|
| `physics.bounce` | 0.9 | physics | [0.0, 1.0] |
| `physics.mass` | 1.0 | physics | [0.1, 5.0] |
| `physics.stiffness` | 75.0 | physics | [10.0, 500.0] |
| `physics.damping` | 0.3 | physics | [0.0, 1.0] |

### 3.3 语义启发式参数（部分合规区）

以下 **5 个参数** 由 `SEMANTIC_VIBE_MAP` 启发式表调整，但**未经知识总线验证**：

| 参数 | 值 | 氛围词 | Delta |
|---|---|---|---|
| `animation.exaggeration` | 0.7 | 活泼 | +0.3 |
| `physics.elasticity` | 0.8 | 弹性 | +0.3 |
| `proportions.squash_stretch` | 1.5 | 弹性 | +0.5 |
| `animation.anticipation` | 0.7 | 活泼 | +0.3 |
| `animation.follow_through` | 0.7 | 活泼 | +0.3 |

---

## 4. Test Results

```
tests/test_provenance_audit.py — 9/9 PASSED

test_tracker_singleton:
  ✓ KnowledgeLineageTracker is a proper singleton

test_knowledge_snapshot:
  ✓ No-bus graceful degradation
  ✓ With bus: 18 modules, 323 constraints, 42 knowledge files

test_lineage_classification:
  ✓ USER_OVERRIDE correctly classified
  ✓ VIBE_HEURISTIC correctly classified
  ✓ HEURISTIC_FALLBACK correctly classified

test_dangling_detection:
  ✓ Dangling parameter correctly detected

test_report_generation:
  ✓ Terminal report produced
  ✓ JSON log dumped to logs/knowledge_audit_trace.json

test_non_intrusive_sidecar:
  ✓ Original float values unchanged after tracking

test_registry_auto_discovery:
  ✓ ProvenanceAuditBackend discovered via BackendRegistry

test_standalone_audit_e2e:
  ✓ Full standalone audit: 18 params, 4 knowledge, 9 fallback, verdict=PARTIAL

test_director_studio_integration:
  ✓ Director Studio → Audit integration: 18 params, 4 knowledge, 14 fallback
```

---

## 5. Files Touched

| 文件 | 操作 | 描述 |
|---|---|---|
| `mathart/core/provenance_tracker.py` | **新增** | 知识血统追踪器（单例 + 线程安全 + 六级溯源） |
| `mathart/core/provenance_report.py` | **新增** | 审计报告生成器（终端体检表 + JSON 落盘） |
| `mathart/core/provenance_audit_backend.py` | **新增** | Registry-native 审计后端插件 |
| `mathart/core/__init__.py` | **修改** | 导出 provenance 模块公共 API |
| `mathart/core/backend_types.py` | **修改** | 新增 `PROVENANCE_AUDIT` 枚举 + 别名 |
| `mathart/core/backend_registry.py` | **修改** | 新增 provenance_audit_backend 自动导入 |
| `tests/test_provenance_audit.py` | **新增** | 9 项全面测试 |
| `docs/SESSION-152-PROVENANCE-AUDIT-DESIGN.md` | **新增** | 架构设计文档 |
| `logs/knowledge_audit_trace.json` | **生成** | 审计 JSON 日志（运行时生成） |

---

## 6. 接下来：根据体检报告暴露的"代码硬编码死区"和"参数断流区"逐个派发靶向修复战役

### 6.1 当前审计系统为靶向修复提供了什么

`ProvenanceAuditBackend` 的 JSON 审计日志精确定位了每个参数的来源类型和缺失的知识绑定。后续修复战役可以直接消费 `logs/knowledge_audit_trace.json` 中的 `dead_zones` 数组，逐个为硬编码参数编写知识规则并接通知识总线。

### 6.2 需要的微调准备

#### 靶向修复战役 1: `P1-SESSION-152-PROPORTIONS-KNOWLEDGE-BIND` — 角色比例知识绑定

**死区参数**: `proportions.head_ratio`, `proportions.body_ratio`, `proportions.limb_ratio`, `proportions.scale`

**当前状态**: 这 4 个参数使用 `dataclass` 硬编码默认值，知识目录中已有 `anatomy.md` 但其约束未覆盖 `proportions.*` 命名空间。

**需要构建**:
1. 在 `knowledge/anatomy.md` 中补充 `proportions.*` 参数的约束规则（参考 Andrew Loomis 人体比例理论）
2. 在 `mathart/distill/parser.py` 中确认 `proportions` 模块的编译路径
3. 运行审计验证 `proportions.*` 参数从 `HEURISTIC_FALLBACK` 升级为 `KNOWLEDGE_DRIVEN`

**架构微调**: 需要在 `RuntimeDistillationBus` 的 `CompiledParameterSpace` 中为 `proportions` 命名空间注册约束映射。当前知识总线的 18 个编译模块中没有 `proportions` 模块 —— 这是根因。

#### 靶向修复战役 2: `P1-SESSION-152-ANIMATION-KNOWLEDGE-BIND` — 动画参数知识绑定

**死区参数**: `animation.frame_rate`, `animation.ease_in`, `animation.ease_out`, `animation.cycle_frames`

**当前状态**: 知识目录中已有 `animation.md`，且 `animation` 模块已在知识总线的 18 个编译模块中。但这 4 个参数的命名空间与知识约束的键名不匹配（知识约束可能使用了不同的参数名）。

**需要构建**:
1. 审计 `knowledge/animation.md` 中的约束键名与 `CreatorIntentSpec.genotype.flat_params()` 的键名映射
2. 补充缺失的约束规则（参考 Disney 12 Principles of Animation、Richard Williams "The Animator's Survival Kit"）
3. 确保 `_apply_knowledge_grounding()` 中的键名匹配逻辑能正确关联

**架构微调**: `DirectorIntentParser._apply_knowledge_grounding()` 当前使用 `param_key.split('.')[0]` 提取模块名。如果知识约束的键名格式与 `flat_params()` 不一致（如 `frame_rate` vs `animation.frame_rate`），需要在知识解析器中添加命名空间前缀映射。

#### 靶向修复战役 3: `P1-SESSION-152-PHYSICS-GRAVITY-KNOWLEDGE-BIND` — 物理重力知识绑定

**死区参数**: `physics.gravity`

**当前状态**: `physics` 模块已在知识总线中，且 `physics.bounce`, `physics.mass`, `physics.stiffness`, `physics.damping` 均已知识驱动。但 `physics.gravity` 仍为硬编码 `9.81`。

**需要构建**:
1. 在 `knowledge/physics_sim.md` 中补充 `gravity` 约束（游戏物理中重力通常在 5.0-30.0 范围内，参考 Celeste/Hollow Knight 的重力调参经验）
2. 运行审计验证 `physics.gravity` 从 `HEURISTIC_FALLBACK` 升级为 `KNOWLEDGE_DRIVEN`

**架构微调**: 无需架构修改，仅需补充知识规则。

#### 靶向修复战役 4: `P1-SESSION-152-VIBE-KNOWLEDGE-VALIDATION` — 语义启发式知识验证

**死区参数**: `animation.exaggeration`, `physics.elasticity`, `proportions.squash_stretch`, `animation.anticipation`, `animation.follow_through`

**当前状态**: 这 5 个参数由 `SEMANTIC_VIBE_MAP` 启发式表调整，但调整后的值未经知识总线验证。

**需要构建**:
1. 在 `DirectorIntentParser._apply_knowledge_grounding()` 中添加"vibe 后验证"步骤：vibe 调整完成后，再次查询知识总线确认调整后的值是否在约束范围内
2. 如果 vibe 调整后的值超出知识约束，记录为 `KNOWLEDGE_CLAMPED` 而非 `VIBE_HEURISTIC`

**架构微调**: 需要在 `_apply_knowledge_grounding()` 中调整调用顺序 —— 当前是先 knowledge grounding 再 vibe adjustment，应改为 vibe adjustment 后再做一次 knowledge validation pass。

#### 靶向修复战役 5: `P2-SESSION-152-BACKEND-CONSUMPTION-AUDIT` — 后端消费审计

**当前状态**: 审计系统已实现 `checkpoint_backend()` 方法，但当前管线中没有后端主动调用此方法。因此 `dangling_count` 始终为 0（因为没有后端消费检查点）。

**需要构建**:
1. 在每个 `@register_backend` 的 `execute()` 方法中添加一行旁路调用：`get_tracker().checkpoint_backend(self.name, consumed_params)`
2. 这样审计系统就能检测"Intent 阶段有但 Backend 没用"的悬空参数

**架构微调**: 需要在 `BackendRegistry` 的 `execute_backend()` 包装器中添加自动检查点钩子，而非要求每个后端手动调用。这是一个 AOP (Aspect-Oriented Programming) 切面。

---

## 7. Updated Todo List

### P0 (立即)
- [x] ~~P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT~~ — **已关闭** (SESSION-152)
- [x] ~~P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH~~ — **已关闭** (SESSION-151)

### P1 (下一冲刺 — 靶向修复战役)
- [ ] P1-SESSION-152-PROPORTIONS-KNOWLEDGE-BIND — 角色比例 4 参数接通知识总线
- [ ] P1-SESSION-152-ANIMATION-KNOWLEDGE-BIND — 动画 4 参数接通知识总线
- [ ] P1-SESSION-152-PHYSICS-GRAVITY-KNOWLEDGE-BIND — 物理重力接通知识总线
- [ ] P1-SESSION-152-VIBE-KNOWLEDGE-VALIDATION — 语义启发式后验证
- [ ] P1-SESSION-151-GA-FITNESS-EVALUATOR — 将 COMFYUI_RENDER_REPORT 接入 GA 适应度评分
- [ ] P1-SESSION-151-BATCH-RENDER-LANE — 在 PDG ai_render_stage 中添加批量渲染
- [ ] P1-SESSION-151-WEBSOCKET-PROGRESS-BAR — 将 WS 进度浮现到 CLI 向导 TUI
- [ ] P1-SESSION-151-GENOTYPE-WORKFLOW-MAP — 基因型向量 → 工作流注入映射
- [ ] P1-SESSION-149-LOG-THROTTLE-EXTRACT — 提升 _emit_demo_warning 为 mathart.core.log_throttle
- [ ] P1-SESSION-149-QUALITY-BOUNDARY-TESTS — 将烟测断言固化到 tests/

### P2 (积压)
- [ ] P2-SESSION-152-BACKEND-CONSUMPTION-AUDIT — 后端消费检查点 AOP 切面
- [ ] P2-SESSION-151-MULTI-WORKFLOW-STRATEGY — 每批渲染支持多个工作流蓝图
- [ ] P2-SESSION-151-COMFYUI-MODEL-CACHE — 批量渲染前预热模型缓存
- [ ] P2-SESSION-151-GA-POPULATION-MANAGER — 完整种群管理器 + 精英持久化
- [ ] P2-SESSION-149-DEMO-VIBE-PARAMS — 接通 vibe parser NL → intent params 自动映射

---

## 8. Architecture Decision Record

### ADR-SESSION-152: 知识血统溯源与非侵入式审计架构

**上下文**: 项目的知识蒸馏管线 (`RuntimeDistillationBus`) 已经建立，但缺乏对"知识是否真正驱动了参数推演"的可解释性审计。大量参数可能使用了 `dataclass` 硬编码默认值而非蒸馏知识，但这一事实在之前的管线中是不可见的。

**决策**: 实现 OpenLineage-aligned 的知识血统追踪系统，作为非侵入式旁路拦截器 (Sidecar Pattern) 挂载到现有管线。追踪器 NEVER 修改任何浮点计算值，仅读取知识总线状态和参数推演路径，生成可解释的审计报告。每个参数被分类为六级溯源类型之一。硬编码死区参数必须诚实标红显示。审计后端通过 `@register_backend` 自注册，遵循 Registry Pattern。

**影响**: 审计系统首次暴露了项目中 50% 的参数处于硬编码死区的事实。这为后续靶向修复战役提供了精确的攻击目标清单。审计 JSON 日志可被 CI/CD 管线消费，实现持续知识覆盖率监控。非侵入式设计确保审计系统的加入不会破坏任何现有功能。

### 工业界/学术界参考对齐

| 参考 | 对齐点 | 落地模块 |
|---|---|---|
| OpenLineage (Marquez/DataHub) | `run_id` + `source_type` + `source_file` 血统事件 | `provenance_tracker.py` |
| XAI in Procedural Generation | 可解释知识映射审计轨迹 | `provenance_report.py` |
| Sidecar/Interceptor Pattern (Envoy) | 非侵入式旁路观测 | 全部三个模块 |
| Data Provenance & Lineage Tracking | 参数值包裹来源上下文 | `ParameterLineageRecord` |

---

## 9. Historical Index (Recent Sessions)

| Session | 主线 | Commit |
|---------|------|--------|
| SESSION-152 (当前) | 知识血统溯源审计 + 全链路参数贯通体检 | (this push) |
| SESSION-151 | ComfyUI BFF 动态载荷变异 + 无头渲染后端 | `fd90026` |
| SESSION-150 | 纯数学驱动动画 + 增强优雅错误边界 | `ebd00bd` |
| SESSION-149 | 动态 demo 网格 + 优雅质量熔断边界 | `c2436e5` |
| SESSION-148 | Windows 终端编码崩溃护盾 + ASCII-safe 救援 UI | `ccc5067` |
| SESSION-147 | 知识总线大一统 + ComfyUI 交互式自愈救援网关 | `0f6da73` |

---

## 10. Handoff Checklist

- [x] 所有新代码遵循 Registry Pattern (`@register_backend`)
- [x] 所有新代码遵循 Sidecar Pattern（非侵入式旁路观测）
- [x] 所有新代码遵循 OpenLineage 血统事件规范
- [x] 审计报告诚实暴露硬编码死区（50% 参数标红）
- [x] 悬空参数检测机制已实现（`checkpoint_backend` + 差集检测）
- [x] 知识总线快照包含完整的编译模块和约束统计
- [x] JSON 审计日志落盘至 `logs/knowledge_audit_trace.json`
- [x] 9/9 测试通过
- [x] PROJECT_BRAIN.json 更新至 v0.99.4, SESSION-152
- [x] SESSION_HANDOFF.md 更新完整上下文
- [x] 所有变更推送至 GitHub

*Signed off by Manus AI · SESSION-152*
