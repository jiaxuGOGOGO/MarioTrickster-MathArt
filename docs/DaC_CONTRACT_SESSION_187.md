# DaC Contract — SESSION-187: 语义编排器大一统

> **Documentation-as-Code (DaC) 契约**: 本文件记录 SESSION-187 所有新增/修改模块的
> 公开 API 契约、UX 文案常量、以及红线遵守承诺。任何后续 SESSION 修改这些契约时，
> 必须同步更新本文件，否则视为契约违规。

---

## 1. 新增模块清单

| 模块路径 | 类型 | 描述 |
|---|---|---|
| `mathart/workspace/semantic_orchestrator.py` | 新建 | 语义编排器：LLM/启发式 VFX 插件解析 |
| `mathart/workspace/pipeline_weaver.py` | 新建 | 动态管线缝合器：中间件链模式执行 VFX 插件 |
| `mathart/workspace/director_intent.py` | 修改 | CreatorIntentSpec 新增 `active_vfx_plugins` 字段 |
| `mathart/cli_wizard.py` | 修改 | `_print_main_menu()` 升级为系统健康仪表盘 |
| `docs/RESEARCH_NOTES_SESSION_187.md` | 新建 | 外网参考研究笔记 |
| `tests/test_session187_semantic_orchestrator.py` | 新建 | 单元测试 |

---

## 2. SemanticOrchestrator API 契约

### 2.1 `VFX_PLUGIN_CAPABILITIES: dict[str, dict]`

已知 VFX 插件能力声明映射表。键为 BackendRegistry 中的注册名。

**当前已知插件**:
- `cppn_texture_evolution` — CPPN 纹理进化引擎
- `fluid_momentum_controller` — 流体动量 VFX 控制器
- `high_precision_vat` — 高精度 VAT 导出器

### 2.2 `SEMANTIC_VFX_TRIGGER_MAP: dict[str, list[str]]`

关键词 → 插件名称列表的映射表。用于启发式路径。

### 2.3 `SemanticOrchestrator.resolve_vfx_plugins(raw_intent, vibe, registry) → list[str]`

**输入**:
- `raw_intent: dict` — 原始意图字典
- `vibe: str` — 语义 vibe 字符串
- `registry` — BackendRegistry 实例

**输出**: `list[str]` — 已验证的 VFX 插件名称列表

**契约**:
1. 返回的每个名称都必须存在于 `registry.all_backends()` 中
2. LLM 建议的不存在名称必须被丢弃并记录 WARNING
3. 空列表是合法返回值（表示无 VFX 需求）

---

## 3. DynamicPipelineWeaver API 契约

### 3.1 `DynamicPipelineWeaver.__init__(registry, observer=None)`

**参数**:
- `registry` — BackendRegistry 实例
- `observer` — 可选的 `PipelineObserver` 实例

### 3.2 `DynamicPipelineWeaver.execute(plugin_names, context) → WeaverResult`

**输入**:
- `plugin_names: list[str]` — 要执行的插件名称列表
- `context: dict` — 共享执行上下文

**输出**: `WeaverResult` 包含:
- `executed: list[str]` — 成功执行的插件
- `skipped: list[str]` — 跳过的插件
- `errors: dict[str, str]` — 错误信息
- `total_ms: float` — 总耗时

**契约**:
1. 零 `if "plugin_name"` 硬编码分支
2. 失败的插件不中断管线
3. Observer 事件在每个插件执行前后触发

---

## 4. CreatorIntentSpec 字段契约

### 4.1 新增字段: `active_vfx_plugins`

| 属性 | 值 |
|---|---|
| 类型 | `list[str]` |
| 默认值 | `[]` |
| 序列化键 | `"active_vfx_plugins"` |
| 来源 | `DirectorIntentParser.parse_dict()` Step 6 |

---

## 5. CLI UX 文案契约

### 5.1 主菜单仪表盘

| 元素 | 文案 |
|---|---|
| 标题 | `MarioTrickster-MathArt · 工业级交互向导主控台` |
| 仪表盘标题 | `[⚙️  系统健康仪表盘]` |
| 知识总线行 | `├─ 知识总线容量: {N} 模块 / {M} 约束条目` |
| 执法者行 | `├─ 活跃执法者: {N} 个知识执法器已加载` |
| 插件行 | `├─ 微内核插件: {N} 个后端已注册` |
| VFX 行 | `└─ VFX 特效算子: {names}` |
| [5] 标注 | `🎬 语义导演工坊 (全自动生产模式 + VFX 缝合)` |
| [6] 标注 | `🔬 黑科技实验室 (独立沙盒空跑测试)` |

### 5.2 VFX 缝合 Banner (导演工坊内)

当 `spec.active_vfx_plugins` 非空时，在意图解析完成后显示：

```
═══════════════════════════════════════════════════════════════
[🎬 SESSION-187 语义缝合器] 已激活 VFX 特效插件链：
    [1] cppn_texture_evolution
    [2] fluid_momentum_controller
═══════════════════════════════════════════════════════════════
```

---

## 6. 红线遵守承诺

| 红线 | 承诺 | 验证方式 |
|---|---|---|
| Anti-Hardcoded | 零 `if "cppn"` 分支 | `grep -rn 'if.*cppn\|if.*fluid\|if.*vat' pipeline_weaver.py` 应返回空 |
| 幻觉防呆 | set intersection 过滤 | 单元测试 `test_hallucination_guard` |
| Graceful Degradation | 失败跳过 | 单元测试 `test_graceful_degradation` |
| UX 零退化 | 不删除已有功能 | `git diff` 不含 `-[0-9].*退出系统` |
| Zero-Trunk-Modification | 新模块独立 | 新文件不修改 `microkernel_orchestrator.py` |
