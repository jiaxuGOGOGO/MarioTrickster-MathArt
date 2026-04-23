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

## 5. 黄金连招：一键出图与知识溯源 (Golden Handoff)

当你在白模预演中选择 `[1] ✅ 完美出图` 并批准当前参数后，系统不会直接退回主菜单，而是会为你提供无缝衔接的**黄金连招**菜单。

这是为了避免你“好不容易调出完美参数，却还要退回主菜单重新输入路径去渲染”的割裂感。

```text
🎬 导演工坊预演通过 — 黄金连招
白模已获批，请选择下一步：
  [1] 🚀 趁热打铁：立刻将当前参数发往后台 ComfyUI 渲染最终大片！
  [2] 🔍 真理查账：打印【全链路知识血统溯源审计表】
  [0] 🏠 暂存并退回主菜单
```

### 如何使用连招

- **[1] 趁热打铁 (一键出图)**：
  系统会直接将你刚才在预演中确定的参数（保存在内存中，不会丢失）发往后端的 `ProductionStrategy` 唤醒 GPU 渲染。
  **出图前防呆预警**：在真正呼叫显卡前，终端会高亮弹出以下防呆提示，避免你误以为系统假死：
  ```text
  [🚨 提示] 即将呼叫显卡渲染！请确保您的 ComfyUI 服务端已在后台启动并就绪。
      * 默认地址：http://localhost:8188
      * 若尚未启动，请另开一个终端运行 `python main.py` 再回到本窗口继续。
  ```
- **[2] 真理查账 (知识溯源体检)**：
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

