# 🎬 创作者白皮书与蓝图使用手册

欢迎来到 **MarioTrickster-MathArt** 导演工坊！本手册是专门为美术、策划与创作者编写的傻瓜式使用指南。我们将带你了解如何用自然语言指挥引擎，如何使用“蓝图”来量产变异怪物，以及如何与白模预演系统互动。

---

## 1. 终端向导说明 (Dual-Track Wizard)

当你在终端直接输入 `mathart`（或等价别名 `mathart-wizard`）时，系统会弹出一个极客风格的交互菜单。只需输入对应的数字并回车，即可进入不同的生产模式：

- **`[1] 🏭 工业量产 (Production)`**：当你的蓝图已经调优完毕，准备大规模生成最终的像素精灵图或特效时使用。此模式会唤醒 GPU 渲染管线。
- **`[2] 🧬 本地闭环进化 (Evolution)`**：让系统使用遗传算法，在约束空间内自动寻找最优参数组合。适合“我也不知道具体参数，让 AI 自己找”的场景。
- **`[3] 💻 本地 AI 科研蒸馏 (Local Distill + GitPush)`**：专门用于将外部知识（如 PDF 书籍、论文）转化为代码约束。
- **`[4] 🧪 纯 CPU 沙盒审计 (Dry-Run)`**：不调用 GPU，仅在纯物理沙盒中预演 100 步，验证参数是否会导致模型崩溃。
- **`[5] 🎬 语义导演工坊 (Director Studio)`**：**创作者最常用的模式！** 在这里，你可以用自然语言编写意图，系统会翻译为数学参数并提供白模预演。
- **`[0] 🚪 退出系统`**：安全退出顶层向导，返回系统终端。

> **🔰 命令对照**：
> - `mathart` / `mathart-wizard` → 顶层品牌命令，**调起交互向导**。向导内置了全局不死循环，任何子任务结束后都会自动返回主菜单，绝不会跑完就闪退。
> - `mathart-evolve <子命令>` → 旧版 argparse 子命令 CLI，**不弹出菜单**，保留用于自动化脚本与 CI。
> - 无交互环境（CI、非 TTY）下使用 `mathart --mode 5` / `--mode 2` 等显式指定模式，避免因无 TTY 触发的 JSON 错误载荷。

---

## 2. 魔法咒语书：如何编写 `intent.yaml`

在 `workspace/inbox/` 目录下创建一个名为 `intent.yaml` 的文件。这就是你向系统下达指令的“魔法咒语书”。

### 基础咒语：自然语言驱动 (Emotive Genesis)

你只需要写下你想要的“感觉”（Vibe），系统会自动将其翻译为具体的物理和动画参数：

```yaml
# workspace/inbox/intent.yaml
vibe: "活泼, 弹性"
description: "一个看起来非常轻盈、跳跃感很强的角色"
```

**系统内置支持的词汇包括：**
`活泼`、`lively`、`夸张`、`exaggerated`、`沉稳`、`轻盈`、`厚重`、`heavy`、`弹性`、`bouncy`、`沉重`、`落地`、`跳跃` 等。

### `director_intent.py` 当前实际解析字段

以下是 `mathart/workspace/director_intent.py` 中 `CreatorIntentParser` **真实读取**的 YAML 根字段（其余字段会被忽略）：

| 字段 | 类型 | 用途 |
|---|---|---|
| `vibe` | string | 语义 vibe 关键字，驱动 `SEMANTIC_VIBE_MAP` 参数偏移 |
| `description` | string | 自由文本说明，仅用于人类记忆，不参与参数化 |
| `base_blueprint` | string | 祖先蓝图的相对/绝对路径，由 `_resolve_blueprint_path` 解析 |
| `overrides` | mapping | 精确参数覆盖，字典会被逐键合并到基因型 |
| `evolve_variants` | int | 需要繁衍的变异体数量（0 = 不繁衍） |
| `freeze_locks` | list[string] | 基因家族锁，必须是 `GENE_FAMILIES` 子集 |

> **说明**：当前解析器**不消费** `reference_image` 字段。如果你的工作流需要参考图，请直接在 ComfyUI 工作流节点中配置 IP-Adapter，`intent.yaml` 中写入该字段只会被解析器忽略，不会报错但也不会被传递下去。此能力将作为后续 Roadmap 条目补齐。

---

## 3. 🧬 蓝图繁衍教程 (核心重点)

当你想基于一个完美的“祖先”来生成一支“控制变量的小怪军团”时，就需要使用蓝图系统。

### 什么是蓝图？
蓝图（Blueprint）是系统生成的 `.yaml` 文件，它精确记录了一个角色的所有基因（物理参数、比例、动画帧率等）。它不包含任何冗余数据（无 Base64、无绝对路径），可以跨版本永久保存，由 `CreatorBlueprint.save_yaml()` / `load_yaml()` 统一读写。

### 如何繁衍变异？

在 `intent.yaml` 中，你可以指定一个“基础蓝图”，并告诉系统你想让它繁衍出多少个变异体，同时**锁定**（Freeze）某些你不希望改变的核心手感。

```yaml
# workspace/inbox/intent.yaml
# 1. 指定祖先蓝图（会依次尝试：绝对路径 → workspace_root/<path> → workspace/blueprints/<basename>）
base_blueprint: "workspace/blueprints/perfect_jump_slime.yaml"

# 2. 我要生成 5 个变异体
evolve_variants: 5

# 3. 锁定核心物理手感，只允许外观和颜色发生变异
freeze_locks:
  - "physics"     # 锁定重力、质量、弹力等物理手感
  - "animation"   # 锁定动画帧率和节奏
```

**基因家族 (Gene Families) 列表**（`blueprint_evolution.GENE_FAMILIES`）：

- `physics`：物理手感（重力、质量、弹力、阻尼等）。
- `proportions`：身体比例（头身比、四肢比例、缩放等）。
- `animation`：动画节奏（帧率、预期动作、跟随动作等）。
- `palette`：调色板和色彩科学。

> **⚠️ 核心红线**：一旦你锁定了 `physics`，系统生成的 5 个变异体在物理手感上将**绝对一模一样**（三级冻结会在 init / mutate / post-restore 三次再戳，方差严格为 0），差异仅存在于未锁定的基因（如身体比例和颜色）中。

---

## 4. 交互式白模预演 (Animatic REPL)

当你在导演工坊模式下提交了 `intent.yaml` 后，系统**不会立刻开始漫长的 GPU 渲染**。相反，它会在不到一秒的时间内生成一个纯线框的“白模预演”（Proxy），并在终端中询问你的意见。以下菜单文案与 `mathart/quality/interactive_gate.py` 中 `InteractivePreviewGate` 的实际输出严格一致：

```text
  [1] ✅ 完美出图
  [2] [+] 再夸张点
  [3] [-] 收敛点
  [4] ❌ 退出
```

- `[1]` → 批准当前参数，可进一步选择是否落地保存为 `Blueprint`。
- `[2]` → 振幅 +Δ，对 `freeze_locks` 锁定的家族不生效。
- `[3]` → 振幅 -Δ，同样受锁约束。
- `[4]` → 退出导演工坊流程，不触发后续进化。

### 真理网关警告 (Truth Gateway Warning)

如果你疯狂选择 `[+] 再夸张点`，导致参数突破了系统蒸馏出的“物理学知识边界”（例如，重力被你调成了负数，或者弹力大到永远停不下来），系统会拦截并通过如下菜单（同样来自 `interactive_gate.py` 的实际文案）请你裁决：

```text
  [1] 遵从科学 — 由系统自动安全裁剪
  [2] 人类意图覆盖 — 无视知识强行生成
```

- **PHYSICAL (物理违规)**：两个选项都可选。选择 `[2]` 即激活「艺术覆盖权」，系统会记录覆盖事件并写入 `ArtifactManifest.applied_knowledge_rules`，后续可追溯。
- **FATAL (致命数学错误)**：例如触发除零、NaN 传播。`[2]` 选项会被自动屏蔽，系统强制执行 Clamp-Not-Reject：把越界参数裁剪回安全边界，不允许覆盖。

---

## 5. 黄金连招 V2：全动作阵列仪表盘 (Golden Handoff V2)

当你在白模预演中选择 `[1] ✅ 完美出图` 并批准当前参数后，系统不会直接退回主菜单，而是会为你提供无缝衔接的**黄金连招 V2**菜单。

最新的交互菜单已实现全阵列图纸烘焙与 AI 画皮分离。无显卡用户请选 [1] 直接提取跑跳攻击等全套工业原画；有显卡用户选 [2] 自动连招出大片。

```text
🎬 导演工坊预演通过 — 黄金连招 V2 · 全动作阵列仪表盘
白模已获批，请选择资产输出策略：
  [1] 🏭 阵列量产：纯 CPU 算力，一键遍历烘焙【全套动作阵列】(包含跑/跳/攻击等) 的高清工业贴图，跳过 AI 画皮。(极度适合无显卡环境)
  [2] 🎨 终极降维：烘焙全套阵列贴图后，立刻推流至后台 ComfyUI 进行 3A 级 AI 批量渲染。(需后台就绪显卡)
  [3] 🔍 真理查账：打印全链路溯源体检表。
  [0] 🏠 暂存并退回主菜单
```

### 如何使用连招

- **[1] 阵列量产 (纯 CPU 烘焙)**：
  系统已解除管线截断。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth）。系统会安静地用纯 CPU 算力算出所有动作（跑、跳、攻击等）的全套工业图纸，并分类存放在 outputs 文件夹中，然后完美回到主菜单，绝不会假死！
  *科幻级进度播报*：在烘焙期间，终端会实时高亮打印 `[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列...` 以及每个动作的解算进度。

  > **SESSION-166 管线修复说明**：系统已修复渲染循环中的“静止帧”传参断链问题。此前由于 Clip2D 使用骨骼名称（如 `l_thigh`）而骨骼系统使用关节名称（如 `l_hip`），导致姿态数据无法正确应用到骨骼，每帧渲染结果完全相同。现已通过 Bone→Joint 名称映射和度→弧度转换彻底修复，每帧均有真实的物理位移变化。

  > **SESSION-167 组合网格逐帧水合说明**：系统已增强组合网格节点（`compose_mesh_stage`）的时序顶点保存能力。此前当上游 pseudo3d_shell 产出多帧变形顶点数据 `[frames, V, 3]` 时，组合节点仅提取最后一帧 `[-1]`，丢弃了所有中间帧的变形数据。现已完整保存时序组合顶点张量（`_temporal_composed_mesh.npz`），同时保留最后一帧作为规范静态组合网格，确保向下兼容。

- **[2] 终极降维 (全套烘焙 + AI 渲染)**：
  系统会先执行 [1] 的全套烘焙，然后自动将参数发往后端的 `ProductionStrategy` 唤醒 GPU 进行大模型推流渲染。
  **出图前防呆预警**：在真正呼叫显卡前，终端会高亮弹出以下防呆提示，避免你误以为系统假死：
  ```text
  [🚨 提示] 即将呼叫显卡渲染！请确保您的 ComfyUI 服务端已在后台启动并就绪。
      * 默认地址：http://localhost:8188
      * 若尚未启动，请另开一个终端运行 `python main.py` 再回到本窗口继续。
  ```
  **优雅降级**：即便后续因为本地无显卡导致外部 API 报错，系统也会在外部优雅捕获异常，高亮打印提示：`[⚠️ 显卡环境未就绪！但您的【全套工业级动作序列】已为您安全锁定保留在 outputs 文件夹中！]`，并平滑退回主菜单，绝不允许闪退！

- **[3] 真理查账 (知识溯源体检)**：
  想知道你的参数是 AI 瞎编的还是真的遵循了物理规律？选择此项，系统会调用旁路审计探针，在终端打印出 CJK 对齐的四列审计表格。
  **怎么看懂体检表**：
  - 如果来源列显示 `KNOWLEDGE_DRIVEN`，说明参数由知识总线严格推演，合规。
  - 如果出现红色的 `⚠️ [Heuristic Fallback / 代码硬编码死区]`，说明该参数没有知识支撑，纯粹是代码写死的默认值（AI 偷懒了）。

- **[0] 暂存并退回**：
  将当前参数暂存，安全返回最顶层的主菜单。

---

## 6. 知识执法网关：Policy-as-Code 参数守卫 (Knowledge Enforcer Gate)

> **SESSION-154 新增** — 将 `knowledge/` 目录下的静态知识文档转化为运行时强制执行的参数验证网关。

### 什么是知识执法网关？

知识执法网关 (Knowledge Enforcer Gate) 是一套 **Policy-as-Code** 参数守卫系统。它把散落在 `knowledge/pixel_art.md`、`knowledge/color_science.md`、`knowledge/color_light.md` 等文档中的"纸面规则"，编译为真正的 `if/clamp/assert` 代码逻辑，在参数进入渲染管线 **之前** 就拦截非法值。

**核心设计原则**：

| 原则 | 说明 |
|------|------|
| **Clamp-Not-Reject (裁剪优先)** | 遇到越界参数时，系统优先自动修正到安全边界，而非直接报错拒绝 |
| **Source Traceability (来源可追溯)** | 每一条校正都会标注其来源知识文档（如 `pixel_art.md §基础规则`） |
| **Shift-Left Validation (左移验证)** | 在渲染开始前就拦截非法参数，避免浪费 GPU 算力 |
| **Zero Trunk Modification (零主干侵入)** | 网关以插件形式挂载，不修改任何现有管线核心代码 |

### 当前已注册的 Enforcer

| Enforcer | 来源知识文档 | 守护规则数 |
|----------|-------------|----------|
| `pixel_art_enforcer` | `pixel_art.md` | 10 条 |
| `color_harmony_enforcer` | `color_science.md`, `color_light.md` | 5 条 |

### PixelArtEnforcer 守护规则一览

| 规则 ID | 守护内容 | 合法范围 | 违规处理 |
|---------|---------|---------|----------|
| 禁止像素画画布尺寸越界 | 画布尺寸 | [16, 64] px | Clamp |
| 禁止像素画调色板越界 | 调色板大小 | [4, 32] 色 | Clamp |
| 禁止像素画双线性插值 | 插值模式 | 仅 `nearest` | 强制 nearest |
| 禁止像素画抗锯齿 | Anti-Aliasing | 必须关闭 | 强制 False |
| 像素画抖动矩阵尺寸不匹配 | Bayer 矩阵 | 16px→2x2, 32px+→4x4 | Clamp |
| 像素画抖动强度越界 | 抖动强度 | [0.0, 1.0] | Clamp |
| 像素画锯齿容忍度越界 | Jaggies | [0, 2] px | Clamp |
| RotSprite放大倍数非法 | RotSprite 倍率 | 必须 8x | 强制 8 |
| 像素画轮廓线颜色数越界 | 轮廓线颜色 | [1, 3] | Clamp |
| 像素画子像素帧数越界 | 子像素帧数 | [2, 4] | Clamp |

### ColorHarmonyEnforcer 守护规则一览

| 规则 ID | 守护内容 | 合法范围 | 违规处理 |
|---------|---------|---------|----------|
| 色彩明度范围不足 | 调色板 L-range | ΔL ≥ 0.3 | 拉伸明度 |
| 死亡配色检测 | 低彩度+中明度 | C ≥ 0.02 或 L 不在 [0.3, 0.7] | 提升彩度 |
| 冷暖对比不足/过度 | 光影色相差 | [120°, 210°] | 修正色相 |
| 补光/轮廓光比例越界 | 3-light ratio | Fill [0.3, 0.5], Rim [0.2, 0.4] | Clamp |
| 上下文调色板大小越界 | 角色/主题限色 | 角色 [8, 16], 主题 [16, 24] | Clamp |

### 终端中看到的执法信息

当知识执法网关激活时，终端会显示类似以下信息：

```text
============================================================
  🛡️ 【知识执法网关 — Knowledge Enforcer Gate】
============================================================
  [💡 知识网关激活] 依据《pixel_art.md》，系统已自动校正您的参数 canvas_size: 256 → 64 (规则: 禁止像素画画布尺寸越界)
  [💡 知识网关激活] 依据《pixel_art.md》，系统已自动校正您的参数 interpolation: 'bilinear' → 'nearest' (规则: 禁止像素画双线性插值)

  📊 执法摘要: 2 条规则触发
     校正 (Clamped): 2
     拦截 (Rejected): 0
============================================================
```

### 如何扩展：编写自定义 Enforcer

如果你想为新的知识文档添加执法逻辑，只需：

1. 在 `mathart/quality/gates/` 下新建 Python 文件
2. 继承 `EnforcerBase` 并实现 `name`、`source_docs`、`validate()` 三个接口
3. 用 `@register_enforcer` 装饰器自动注册
4. 在 `enforcer_registry.py` 的 `_auto_load_enforcers()` 中添加模块路径

```python
from mathart.quality.gates import EnforcerBase, register_enforcer, EnforcerResult

@register_enforcer
class MyCustomEnforcer(EnforcerBase):
    @property
    def name(self) -> str:
        return "my_custom_enforcer"

    @property
    def source_docs(self) -> list[str]:
        return ["my_knowledge.md"]

    def validate(self, params):
        violations = []
        corrected = dict(params)
        # ... your validation logic here ...
        return EnforcerResult(
            enforcer_name=self.name,
            params=corrected,
            violations=violations,
        )
```

---

## 7. 知识智能分流与原生去重 (Knowledge Triage & Dedup Funnel)

> **SESSION-156 新增** — 系统具备知识智能分流与去重能力。只提炼干货，宏观理论归档为指导思想，微观规则才会触发代码编译！

### 这是什么？

当你向系统喂入一本书、一篇论文或一段文字时，系统不再"来者不拒"地把所有内容都塞进代码编译器。而是先经过两道智能漏斗：

1. **原生去重漏斗 (Native Dedup)**：系统会自动对比已有知识库，剔除重复内容。如果你喂了两遍同一本书，第二遍的重复规则会被自动跳过，不会造成知识膨胀。
2. **智能分诊漏斗 (Knowledge Triage)**：系统会自动判断每条知识的"类型"——

| 类型 | 标签 | 说明 | 会生成代码吗？ |
|------|------|------|--------------|
| **微观硬核约束** | `[Actionable-Rule]` | 物理重力阈值、色彩数值限制、像素画禁忌等可量化规则 | **会** — 送入 Auto-Compiler 自动合成 Python 代码 |
| **宏观指导哲学** | `[Macro-Guidance]` | 游戏设计哲学、世界观设定、透视理论、"游戏必须好玩"等抽象概念 | **不会** — 仅归档为知识库中的高维上下文，绝对不生成代码 |

### 为什么要这样做？

如果把"游戏必须要好玩"这种宏观哲学强行编译成 Python 代码，系统会产生严重的 AI 幻觉和无意义的代码。智能分诊确保：

- **微观约束**（如 `canvas_size ∈ [16, 64]`）→ 变成真正的 `if/clamp` 守护代码
- **宏观哲学**（如 "好的关卡设计应该有节奏感"）→ 安全存入知识库，留给未来 AI 推理时作为上下文参考

### 终端中看到的分诊信息

当知识处理流转时，终端会透明展示系统的"思考过程"：

```text
============================================================
  🧠 MarioTrickster Knowledge Pipeline v2 (SESSION-156)
============================================================
  [1/7] 📄 文档接收: pixel_logic_book.pdf (45000 chars)
  [2/7] 🤖 LLM 规则提取引擎启动...
  [2/7] ✅ 提取完成: 12 条原始规则
  [3/7] 📖 原生去重引擎唤醒中...
  [📖 原生去重] 正在对比已有智库，剔除冗余...
  [📖 去重完成] DeduplicationResult: 12 input → 10 new, 2 exact-dup, 0 variant-kept, 0 param-merged
  [⚖️ 知识分诊] 正在对通过去重的规则进行智能分流...
  [⚖️ 知识分诊] 判定为【微观约束 Actionable-Rule】，送入 Python 编译引擎...
         规则: canvas_size must be between 16 and 64 pixels
         信号: numeric_params_present, \b(?:min|max|range|threshold|limit|clamp|cap)\b
  [⚖️ 知识分诊] 判定为【宏观哲学 Macro-Guidance】，安全归档，跳过代码生成...
         规则: Good pixel art should convey a sense of personality and charm
         原因: Macro signals (3) dominate over actionable signals (0). Blocking from Auto-Compiler.
  [⚖️ 分诊汇总] TriageResult: 10 rules → 7 actionable (→ compiler), 3 macro (→ archive only)
  [5/7] 🛠️  Enforcer code synthesis (actionable rules only) ............
  [6/7] 🔬 AST guardian validation PASSED ......
  [7/7] 📋 Session DISTILL-004: Extracted 12 rules, integrated 10. Triage: 7 actionable, 3 macro-archived.
```

### 傻瓜验收：如何测试分诊是否真的有效？

想亲眼验证系统是否真的聪明到能阻止宏观废话生成代码？试试这个：

**步骤 1**：在终端进入 Python 交互模式：

```python
from mathart.evolution.outer_loop import OuterLoopDistiller

distiller = OuterLoopDistiller(use_llm=False, verbose=True)

# 喂一段极度宏观的废话
result = distiller.distill_text(
    "游戏必须要好玩。好的游戏设计应该让玩家感到沉浸和满足。"
    "优秀的关卡设计需要有节奏感和情感曲线。"
    "游戏的美学应该追求和谐与平衡。",
    source_name="废话测试"
)
```

**预期结果**：终端会显示 `[⚖️ 知识分诊] 判定为【宏观哲学 Macro-Guidance】，安全归档，跳过代码生成...`，并且 `result.enforcer_plugins_generated` 为空列表（没有生成任何 Python 代码）。

**步骤 2**：再喂一段微观约束：

```python
result2 = distiller.distill_text(
    "spring_k = 15.0\ndamping_c = 4.0\nmax_velocity = 200 px/s\ncanvas_size = 32",
    source_name="物理约束测试"
)
```

**预期结果**：终端会显示 `[⚖️ 知识分诊] 判定为【微观约束 Actionable-Rule】，送入 Python 编译引擎...`，规则会正常进入编译流程。


---

## 8. 管线解耦：纯 CPU 工业级动画引导序列烘焙 (SESSION-158)

> **SESSION-158 新增** — 系统已解除管线截断。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth/Mask）。

### 这是什么？

在 SESSION-158 之前，工业级动画引导图的烘焙逻辑（Catmull-Rom 骨骼插值、SMPL 体型解算、RUN_KEY_POSES 步态驱动）被错误地锁死在 AI 渲染节点（`ai_render_stage`）内部。当用户使用 `--skip-ai-render` 标志跳过 GPU 渲染时，烘焙逻辑也被连坐截断，导致纯 CPU 模式下完全无法产出任何工业级资产。

SESSION-158 执行了精准的管线解耦外科手术：

| 改动 | 说明 |
|------|------|
| **新增 `guide_baking_stage`** | 独立的纯 CPU PDG 节点，`requires_gpu=False`，ALWAYS 执行 |
| **烘焙逻辑剥离** | `_bake_true_motion_guide_sequence()` 从 `ai_render_stage` 中完全移出 |
| **IR Hydration** | 烘焙产物（Albedo/Normal/Depth/Mask 序列帧）作为一等公民资产落盘到 `guide_baking/` 目录 |
| **AI 渲染解耦** | `ai_render_stage` 现在仅消费上游已烘焙好的引导图，不再自行烘焙 |

### 管线拓扑变化

```
[旧管线] orthographic_render + motion2d → ai_render_stage (烘焙+AI渲染 耦合)
                                          └─ skip_ai_render → 全部截断 ❌

[新管线] orthographic_render + motion2d → guide_baking_stage (纯CPU,永远执行) ✅
                                          └→ ai_render_stage (仅GPU渲染)
                                              └─ skip_ai_render → 仅跳过AI渲染
```

### 终端中看到的烘焙信息

当管线运行到烘焙阶段时，终端会高亮显示：

```text
[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列... [character_000]
[✅ 工业烘焙完成] 40 帧高精度引导图序列已落盘 → /path/to/guide_baking
```

### 傻瓜验收：如何确认解耦成功？

在终端运行（无需 GPU）：

```bash
python -m mathart mass-produce --output-dir ./test_output --batch-size 1 --skip-ai-render --seed 42
```

然后检查输出目录：

```bash
ls test_output/mass_production_batch_*/character_000/guide_baking/
# 应看到: albedo/ normal/ depth/ mask/ 四个子目录，每个包含完整的 PNG 序列帧
# 以及一份 character_000_guide_baking_report.json 烘焙报告
```

**关键验收点**：即使使用了 `--skip-ai-render`，`guide_baking/` 目录下也必须有完整的工业级引导图序列。这些不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！

---

## 8.1 动态动作注册表 + 时序上下文穿透 + 防静止断言 (SESSION-160)

> **SESSION-160 新增** — 三大核心重构：动态动作注册表铲除硬编码、RenderContext 时序参数断链修复、MSE 防静止自爆核弹部署。

### 8.1.1 动态动作注册表 (ActionRegistry)

SESSION-160 彻底铲除了工厂文件中的硬编码动作列表 `_MOTION_STATES = ["idle", "walk", "run", "jump", "fall", "hit"]`，替换为从 `MotionStateLaneRegistry`（IoC 注册表）动态获取。

| 改动 | 说明 |
|------|------|
| **铲除 `_MOTION_STATES`** | 不再存在硬编码动作列表，新增 `_get_registered_motion_states()` 动态查询 |
| **动态发现** | 新动作类型（如 Dash、Climb、AttackCombo）只需在 Registry 中注册新 Lane，零修改工厂代码 |
| **工业参考** | Unreal Engine Animation Blueprint State Machine — 状态动态注册，不硬编码 |

如何添加新动作类型：

```python
# 在 mathart/animation/unified_gait_blender.py 中注册新 Lane
registry = get_motion_lane_registry()
registry.register("dash", DashMotionLane())
# 工厂会自动发现并使用新动作，无需任何其他修改
```

### 8.1.2 时序上下文穿透 (Temporal Context Wiring)

SESSION-160 修复了从 `prepare_character` 到 `guide_baking_stage` 的时序参数断链。现在 `motion_state`、`fps`、`character_id` 作为显式的 RenderContext 参数无损穿透到烘焙函数。

| 参数 | 来源 | 穿透路径 |
|------|------|----------|
| `motion_state` | `prepare_character` | → `guide_baking_stage` → `baking_report.json` → `ai_render_stage` |
| `fps` | `prepare_character` | → `guide_baking_stage` → `baking_report.json` |
| `character_id` | `prepare_character` | → `_bake_true_motion_guide_sequence()` 诊断日志 |

工业参考：DigitalRune RenderContext — 渲染上下文必须显式传递，不允许隐式假设。

### 8.1.3 防静止自爆核弹 (Variance Assert Gate)

SESSION-160 在 AI 渲染边界部署了更严格的逐帧对 MSE 地板断言。与 SESSION-158 的比率式断路器不同，此断言要求 **每一对** 连续帧的 MSE 都必须超过绝对地板值。

| 防线 | 触发条件 | 行为 |
|------|----------|------|
| **SESSION-158 比率式断路器** | 超过50%帧对 MSE < 1.0 | `PipelineContractError` |
| **SESSION-160 地板断言** | 任何单帧对 MSE < 0.0001 | `RuntimeError`（立即终止） |

工业参考：MSE 帧差分是视频监控和动画 QA 管线的标准运动检测方法。

### 傻瓜验收：如何确认 SESSION-160 重构生效？

1. **动态注册表**：运行以下代码确认动作列表来自 Registry：

```python
from mathart.animation.unified_gait_blender import get_motion_lane_registry
print(get_motion_lane_registry().names())
# 应输出: ('fall', 'hit', 'idle', 'jump', 'run', 'walk')
```

2. **时序上下文**：检查烘焙报告中是否包含 `motion_state` 和 `fps` 字段：

```bash
cat test_output/mass_production_batch_*/character_000/guide_baking/*_guide_baking_report.json | python3 -m json.tool | grep -E 'motion_state|fps'
```

3. **防静止断言**：`assert_nonzero_temporal_variance()` 已部署在 AI 渲染边界，任何冻结动画将触发 `RuntimeError`。

---

## 9. L-System 程序化植物生成 (PlantPresets 静态工厂)

### 这是什么？

L-System（Lindenmayer System）是一种基于形式文法的程序化生成系统，本项目用它来生成像素风格的植物结构（树木、灌木、草丛、藤蔓）。生成的植物由 SDF 图元组成，可通过现有的 SDF 渲染器渲染并自动配色。

### API 使用方式（SESSION-157 官方转正）

自 SESSION-157 起，植物生成统一通过 `PlantPresets` 静态工厂类调用。旧的 `LSystemPlantGenerator` 类已废弃，请勿使用。

```python
from mathart.sdf.lsystem import LSystem, PlantPresets

# 生成一棵橡树
oak = PlantPresets.oak_tree(seed=42)
segments = oak.generate(iterations=4)
img = oak.render(32, 32)

# 生成灌木
bush = PlantPresets.bush(seed=123)
bush_img = bush.render(32, 32)

# 生成草丛
grass = PlantPresets.grass(seed=7)
grass_img = grass.render(16, 16)

# 生成藤蔓
vine = PlantPresets.vine(seed=99)
vine_img = vine.render(32, 64)
```

也可以通过顶层 `mathart.sdf` 包直接导入：

```python
from mathart.sdf import PlantPresets

tree = PlantPresets.oak_tree()
```

### 傻瓜验收：如何测试植物生成是否畅通？

在终端运行：

```bash
python -m pytest tests/test_lsystem.py -v
```

或在 Python 交互模式中：

```python
from mathart.sdf.lsystem import PlantPresets
tree = PlantPresets.oak_tree(seed=42)
segments = tree.generate(iterations=3)
print(f"生成了 {len(segments)} 个植物片段")  # 应输出正整数
img = tree.render(32, 32)
print(f"渲染尺寸: {img.size}")  # 应输出 (32, 32)
```

---

## 8.2 注册表残党清剿 + 烘焙阶段 Fail-Fast 静止断言 (SESSION-162)

> **SESSION-162 新增** — 在 SESSION-160 的基础上完成"最后一公里"清剿，并把视觉静止断言**前置**到纯 CPU 烘焙出口，让冻结序列在到达 GPU 之前就被原地击落。

### 8.2.1 残留硬编码动作列表的彻底铲除

SESSION-160 已经在工厂主文件 `mathart/factory/mass_production.py` 中拆掉了 `_MOTION_STATES = ["idle", "walk", ...]` 的硬编码，但全仓搜索发现仍有 5 个文件残留同样模式的字符串列表。SESSION-162 一次性收尾如下：

| 文件 | 旧值 | 新值 |
|------|------|------|
| `mathart/pipeline.py` (`states` 字段) | `["idle", "run", "jump", "fall", "hit"]` | `list(get_motion_lane_registry().names())` |
| `mathart/pipeline.py` (`states=` kwarg) | `["idle", "run", "jump", "fall", "hit"]` | `list(get_motion_lane_registry().names())` |
| `mathart/pipeline_contract.py` (`UMR_Context.states`) | `("idle", "run", "jump", "fall", "hit")` | `tuple(get_motion_lane_registry().names())` |
| `mathart/headless_e2e_ci.py` (`GOLDEN_STATES`) | `("idle", "run", "jump", "fall", "hit")` | `tuple(get_motion_lane_registry().names())` |
| `mathart/animation/cli.py` (`VALID_STATES`) | `{"idle", "run", "jump", "fall", "hit"}` | `set(get_motion_lane_registry().names())` |
| `mathart/evolution/asset_factory_bridge.py` (`states`) | `["idle", "walk", "run", "jump"]` | `list(get_motion_lane_registry().names())` |

> **设计说明**：`evolution_preview_states = ["idle", "run", "jump"]` 字段为有意保留的"快速预览子集"，仅在进化预览阶段使用，不属于"全集"枚举，因此**不在铲除范围**。

### 8.2.2 烘焙阶段 Fail-Fast 视觉静止断言（前置防线）

SESSION-160 已经在 AI 渲染边界（`_node_ai_render`）部署了 `assert_nonzero_temporal_variance` 防止冻结序列污染 ComfyUI。SESSION-162 把同一道断言**前置**到 `mathart/factory/mass_production.py::_bake_true_motion_guide_sequence` 的 return 之前：

```python
# mathart/factory/mass_production.py — _bake_true_motion_guide_sequence 出口
from mathart.core.anti_flicker_runtime import assert_nonzero_temporal_variance
try:
    assert_nonzero_temporal_variance(source_frames, channel="source")
except RuntimeError as e:
    from mathart.pipeline_contract import PipelineContractError
    raise PipelineContractError("frozen_guide_sequence", str(e))
```

这意味着任何"冻结的烘焙序列"将在**纯 CPU 阶段就被 `PipelineContractError("frozen_guide_sequence")` 中断**，绝不浪费任何 GPU 算力。

### 8.2.3 傻瓜验收：如何确认 SESSION-162 改造生效？

1. **残留硬编码扫描**（应只剩注释/文档/有意保留的 `evolution_preview_states`）：

```bash
grep -rn '"idle", "run", "jump", "fall", "hit"' mathart/ tools/
# 期望输出：仅有少量注释/字符串字面量，无 = [...] 赋值
```

2. **注册表一致性**（所有铲除点都从同一个真理源拉取）：

```python
from mathart.animation.unified_gait_blender import get_motion_lane_registry
from mathart.pipeline_contract import UMR_Context
from mathart.headless_e2e_ci import GOLDEN_STATES
from mathart.animation.cli import VALID_STATES
print(set(get_motion_lane_registry().names()))   # 真理源
print(set(UMR_Context().states))                  # 应一致
print(set(GOLDEN_STATES))                          # 应一致
print(VALID_STATES)                                # 应一致
```

3. **烘焙阶段静止断言**：构造一段 6 帧全等图像，调用 `_bake_true_motion_guide_sequence` 应在 return 前抛 `PipelineContractError("frozen_guide_sequence")`。

### 8.2.4 外网工业理论锚点（Docs-as-Code）

SESSION-162 的三大改造均有公开工业出处可离线复核，详见仓库内归档：

> 见 [`docs/RESEARCH_NOTES_SESSION_162.md`](RESEARCH_NOTES_SESSION_162.md)，包括：
>
> - Tom Looman, *Why you should be using GameplayTags in Unreal Engine*（数据驱动注册表替代硬编码）
> - DigitalRune, *Render Context*（强类型上下文显式传播）
> - Isaac Berrios, *Introduction to Motion Detection: Part 1 - Frame Differencing*（MSE 帧差检测静止）


---

## 9. ComfyUI API 桥梁：无头大模型渲染枢纽与全阵列推流 (SESSION-163)

> **SESSION-163 新增** — 系统已解除管线截断。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth）。当 ComfyUI GPU 后端就绪时，系统将自动通过 API 桥梁将全阵列工业图纸推流至 AI 炼丹炉，完成真实的端到端渲染闭环。

### 9.1 这是什么？

SESSION-163 打通了从本地 CPU 烘焙引擎到远程/本地 ComfyUI GPU 渲染节点的 API 通讯网线。系统现在具备完整的三层能力：

| 层级 | 能力 | 依赖 |
|------|------|------|
| **第一层：纯 CPU 烘焙** | Catmull-Rom 样条插值，工业级 Albedo/Normal/Depth 序列 | 零 GPU，零网络 |
| **第二层：API 桥梁** | HTTP/WS 通讯、幂等上传、工作流变异、熔断保护 | ComfyUI 服务在线 |
| **第三层：AI 渲染** | ControlNet 强约束注入、KSampler 采样、高清大图回收 | GPU + ComfyUI |

### 9.2 核心组件

#### ComfyUI API 客户端 (`ComfyAPIClient`)

高可用 API 客户端，封装 ComfyUI 的完整 HTTP API 生命周期：

- `upload_image()` — 通过 `/upload/image` 多部分上传，将烘焙好的法线/深度底图静默推送至服务器
- `queue_prompt()` — 通过 `POST /prompt` 发送工作流 JSON 触发渲染
- `wait_for_completion()` — WebSocket 实时监听 + HTTP 轮询降级
- `download_outputs()` — 通过 `/history` + `/view` 回收最终高清大图
- `free_vram()` — 通过 `POST /free` 强制释放 GPU 显存

#### 工作流变异器 (`ComfyWorkflowMutator`)

BFF 载荷变异引擎，通过语义 `_meta.title` 标记定位节点并注入运行时值：

- 读取 `mathart/assets/workflows/workflow_api_template.json` 模板
- 深拷贝（不可变蓝图原则）
- 动态替换 `[MathArt_Input_Image]`、`[MathArt_Normal_Guide]`、`[MathArt_Depth_Guide]` 节点
- 注入美术风格 Prompt 和随机种子
- 完整变异审计账本

#### 全阵列推流后端 (`AIRenderStreamBackend`)

注册表原生的全阵列资产推流后端：

- 从动态注册表获取所有可用动作（run, jump, idle, fall, hit, walk, ...）
- 逐一上传每个动作的烘焙底图至 ComfyUI
- 通过 ControlNet 双通道（Normal + Depth）强约束注入
- AI 渲染返回的高清大图自动重命名为 `ai_render_{action}_{frame:02d}.png`
- 所有资产路径注册进总线 Pipeline Context

### 9.3 熔断保护与优雅降级

系统实现了工业级的三重保护机制：

| 机制 | 说明 |
|------|------|
| **指数退避重试** | `min(2s × 2^attempt + jitter, 32s)`，防止雷群效应 |
| **断路器** | 连续 3 次失败后熔断，30 秒后半开探测 |
| **优雅降级** | ComfyUI 离线时打印黄色警告，保留原生物理底图，平滑退回主循环 |

当 ComfyUI 未启动时，终端会显示：

```text
[⚠️ AI 渲染服务器未就绪，已为您安全保留原生物理底图并终止推流]
```

**绝对不会闪退！** 系统会安全保留所有 CPU 烘焙的工业级资产。

### 9.4 傻瓜验收：如何确认 API 桥梁已打通？

老大，解耦手术已完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！

**无 GPU 验收**（纯 CPU 模式）：

```bash
python -m mathart mass-produce --output-dir ./test_output --batch-size 1 --skip-ai-render --seed 42
```

检查输出：
```bash
ls test_output/mass_production_batch_*/character_000/guide_baking/
# 应看到完整的 Albedo/Normal/Depth/Mask 序列帧
```

**有 GPU 验收**（需 ComfyUI 后端运行）：

```bash
# 1. 启动 ComfyUI（默认 127.0.0.1:8188）
# 2. 运行全量产线
python -m mathart mass-produce --output-dir ./test_output --batch-size 1 --seed 42
# 3. 检查 AI 渲染输出
ls test_output/mass_production_batch_*/character_000/ai_render_*/
# 应看到 ai_render_run_00.png, ai_render_jump_00.png 等高清大图
```

### 9.5 外网工业理论锚点（Docs-as-Code）

SESSION-163 的所有架构决策均有公开工业出处可离线复核，详见仓库内归档：

> 见 [`docs/RESEARCH_NOTES_SESSION_163.md`](RESEARCH_NOTES_SESSION_163.md)，包括：
>
> - ComfyUI Official API Routes Documentation（REST + WebSocket 规范）
> - Michael Nygard, *Release It!* (2007)（断路器三态机）
> - AWS Architecture Blog, *Exponential Backoff And Jitter* (2015)（指数退避 + 抖动）
> - Sam Newman, *Building Microservices* (2021)（BFF 载荷变异模式）
> - LLVM Pass Infrastructure（语义寻址 vs 硬编码 ID）

## 10. SESSION-164: 前端 CLI 全链路对接 (Final UI Assembly)

### 10.1 管线截断已解除

系统已完成前端 CLI 向导与 SESSION-161（ComfyUI API 通讯网线）和 SESSION-162（动态动作注册表与防呆核弹）底层引擎的全链路大满贯流转对接。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth），并且终端进度播报、异常捕获、意图穿透均已与底层引擎实时绑定。

### 10.2 动态进度播报

烘焙阶段的终端进度播报现已与动态动作注册表（`MotionStateLaneRegistry`）实时绑定。系统不再使用任何硬编码动作字符串，而是通过 `get_motion_lane_registry().names()` 动态获取所有已注册动作，逐行打印实时进度：

```text
[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列...
[⚙️  工业量产] 动态注册表已就绪 — 共发现 6 种已注册动作
    ↳ 已注册动作阵列: fall, hit, idle, jump, run, walk
    [⚙️  工业量产] 正在解算 fall 序列贴图...
    [⚙️  工业量产] 正在解算 hit 序列贴图...
    ...
```

### 10.3 精准异常捕获

异常捕获已从宽泛的 `except Exception` 升级为精确的分层拦截：

| 异常类型 | 来源 | 终端表现 |
|----------|------|----------|
| `PipelineQualityCircuitBreak` | SESSION-162 MSE 静止帧自爆 | 红色质量防线拦截通知 |
| `ConnectionRefusedError` | ComfyUI 服务未启动 | 黄色警告 + 物理底图保留 |
| `OSError` | 网络层异常 | 黄色警告 + 异常详情 |
| `PipelineContractError` | 管线契约违规 | 红色契约违规通知 |

当 ComfyUI 未启动时，终端会显示：

```text
[⚠️  ComfyUI 炼丹炉未响应/未启动！但您的全阵列物理底图已为您安全落盘保留。]
```

**绝对不会闪退！** 系统会安全保留所有 CPU 烘焙的工业级资产并平滑返回主循环。

### 10.4 意图参数全链路穿透

用户在导演工坊输入的 `vibe`（意图提示词，如"极具野性的跳跃"）现已实现全链路穿透：

```
UI 菜单输入 → spec.raw_vibe → dispatch options["vibe"]
    → ProductionStrategy → AIRenderStreamBackend
    → workflow_api.json [MathArt_Prompt] 节点
```

参数不会在半路被丢弃。

### 10.5 资产闭环绿色通知

当全阵列烘焙或渲染流程完美结束时，终端会打印醒目的绿色完成通知：

```text
[✅ 资产闭环] 流程完美结束！全套动作序列高清工业贴图已安全落盘至 outputs 文件夹！
```

### 10.6 外网工业理论锚点（Docs-as-Code）

SESSION-164 的所有架构决策均有公开工业出处可离线复核，详见仓库内归档：

> 见 [`docs/RESEARCH_NOTES_SESSION_164.md`](RESEARCH_NOTES_SESSION_164.md)，包括：
>
> - End-to-End UI/Backend Impedance Matching（端到端阻抗对齐）
> - Dynamic UI Hydration（LLVM TargetRegistry 自注册模式）
> - Precise Exception Catching（Michael Nygard 断路器 + 精确异常分层）
> - Intent Propagation（BFF 载荷变异 + 语义寻址注入）

## 10.7 SESSION-165: 架构内聚与 Fail-Loud 异常透传

系统已解除管线截断。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth）。

老大，解耦手术已完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！

## 10.8 SESSION-167: 组合网格逐帧顶点切片同步 (Per-Frame Slice Hydration)

系统已增强组合网格节点（`compose_mesh_stage`）的时序顶点保存能力。此前当上游 `pseudo3d_shell_backend` 产出多帧变形顶点数据 `[frames, V, 3]` 时，组合节点仅提取最后一帧 `[-1]`，丢弃了所有中间帧的变形数据。

### 修复内容

| 修复项 | 说明 |
|--------|------|
| 时序组合顶点张量 | 当检测到 `shell_mesh["vertices"].ndim == 3` 时，将每帧的变形顶点与静态附件/飘带顶点拼接，生成完整的 `[frames, V_total, 3]` 时序张量 |
| 持久化存储 | 时序组合网格保存为 `_temporal_composed_mesh.npz`，包含 `temporal_vertices`、`frame_count`、`shell_vertex_count` |
| 向下兼容 | 规范静态组合网格（最后一帧）仍然保存为 `_composed_mesh.npz`，不影响现有下游消费者 |
| 报告增强 | 组合报告新增 `has_temporal_data` 和 `temporal_frame_count` 字段 |
| UX 防腐蚀 | 烘焙网关终端打印新增 SESSION-167 组合网格水合状态行 |

### 外网工业理论锚点

> 见 [`docs/RESEARCH_NOTES_SESSION_167.md`](RESEARCH_NOTES_SESSION_167.md)，包括：
>
> - Per-Frame Slice Hydration（时序网格复合与水合）
> - Render Loop Context Mutability（渲染循环上下文可变性）
> - Fail-Loud Validation / VarianceAssertGate（显性失败验证模式）
> - NVIDIA GPU Gems 3 Ch.2: Animated Crowd Rendering（逐帧实例数据更新）
> - Catmull-Rom Spline Interpolation（帧间光滑插值）

## 10.9 SESSION-168: ComfyUI API 防死锁抛出与全局推流断路器 (Deadlock Breaker)

系统已修复了当 ComfyUI 内部节点发生崩溃（如 PyTorch 精度冲突）时，终端死锁挂起的严重缺陷。现在，系统能够精确捕获远程致命错误，并强制熔断整个渲染管线，将控制权安全交还给用户。

### 修复内容

| 修复项 | 说明 |
|--------|------|
| WebSocket Fail-Fast | `comfyui_ws_client` 接收到 `execution_error` 时不再吞没错误，而是直接抛出专属异常 `ComfyUIExecutionError`，撕裂无限等待的 `ws.recv()` 循环 |
| 全局刹车踏板 | `ai_render_stream_backend` 捕获到 Poison Pill 异常后，强制开启断路器（Circuit Breaker OPEN），不再尝试重试或渲染后续动作，立即撤销所有待推流任务 |
| 前端雪崩告警 | CLI 向导精确捕获异常并弹出高亮红色崩溃 Banner，打印发生崩溃的具体节点和报错详情，并提供清晰的修复建议（如 `--fp16`） |
| UX 防腐蚀 | 烘焙网关终端打印新增 SESSION-168 状态行，明确告知用户 AI 推流断路器已就绪 |

### 外网工业理论锚点

> 见 [`docs/RESEARCH_NOTES_SESSION_168.md`](RESEARCH_NOTES_SESSION_168.md)，包括：
>
> - WebSocket Poison Pill Pattern（毒药消息模式）
> - Circuit Breaker Pattern (Michael Nygard, "Release It!" 2007)
> - Fail-Fast Principle（快速失败原则）
> - ComfyUI Server Comm Messages (execution_error)
