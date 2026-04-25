# SESSION-201 外网参考研究笔记 (Research Anchors)

> SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL
> 目标：把 CLI 启动重构为渐进式问答向导 + 显式意图契约 + 黄金通告单 + Headless 静默逃生门。
> 研究于 2026-04-25 完成。所有引用 URL 已经通过浏览器实地访问取证（snippets 已交叉验证）。

---

## 1. CLI 渐进式交互范式 (Progressive Disclosure)

### 1.1 Vue CLI `vue create`（Maintenance Mode 但仍是教科书级范式）
- 入口：`vue create hello-world`，**默认进入交互式向导**询问 preset。
- 旁路：提供 7 个非交互 flag（`-p/--preset`、`-d/--default`、`-i/--inlinePreset`、`-m/--packageManager`、`-r/--registry`、`-g/--git`、`-f/--force`）让 CI/CD 完全跳过提问。
- 关键设计：**默认问答 + 显式 flag 跳过**，而不是反过来。
- 来源：<https://cli.vuejs.org/guide/creating-a-project.html>

### 1.2 Nielsen Norman Group "Progressive Disclosure"
- 核心原则：先呈现**最常用、最基础**的选项；高级特性放二级面板。
- 在 CLI 里映射为：第一屏只让用户在 `[A] 感性创世 / [B] 蓝图派生 / [C] 混合 / [D] 视觉临摹` 之间四选一，再根据选择按需追问 reference image / vfx overrides。
- 来源：<https://www.nngroup.com/articles/progressive-disclosure/>

### 1.3 SESSION-201 落地要点
- 不再硬编码 gait 列表；通过反向查询 `OpenPoseGaitRegistry.names()` 动态打印。
- 第一次提问 = 动作；第二次 = 是否使用参考图；第三次 = 是否强制 VFX。
- 三个问题都允许"留空使用默认"，符合 Vue CLI 的"default 优先 + 进阶可选"。

---

## 2. Kubernetes CRD 显式声明式契约

### 2.1 OpenAPI v3.0 Schema (GA in 1.16)
- CRD 强制要求每个字段 **显式声明 type** —— `string` / `integer` / `array` / `object`。
- "Tolerant by default"：未知字段会被丢弃，不会让 `additionalProperties: false` 之外的字段污染上下文。
- 来源：<https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/>

### 2.2 Validating + Mutating Admission Webhooks（fail-closed）
- **Validating Webhook**：只看不改，看到非法字段直接拒绝。
- **Mutating Webhook**：先于 Validating 执行，可补默认值；但补完之后仍要过 Validating 这一关。
- `failurePolicy: Fail`（fail-closed）= 网络故障也拒绝放行，杜绝幽灵字段穿透到 etcd。
- 来源：<https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/>

### 2.3 SESSION-201 落地要点
- `CreatorIntentSpec` 不再用裸 dict / 自由格式 `vibe`；显式字段：
  - `action_name: str | None`（gait 名称，必须是 registry 中的合法值）
  - `visual_reference_path: str | None`（必须是磁盘上真实存在的文件）
  - `vfx_overrides: dict[str, bool]`（key 显式枚举：`force_fluid`, `force_physics`, `force_cloth`, `force_particles`）
- `IntentGateway` 已实现 Validating Admission（SESSION-196）；本轮新增 Mutating：把 vfx_overrides 注入 `active_vfx_plugins`。
- 旧 YAML（只有 `vibe`）走"宽容默认值"，与 K8s 的 tolerant-by-default 思想一致，但任何**显式给出**的非法值仍 fail-closed。

---

## 3. Headless 自动化与 Human-in-the-Loop 双轨

### 3.1 GitLab CLI 设计准则
- 引用 issue #8142：**`--yes` 仅在非 TTY 环境必填**；TTY 下仍弹出确认。
- 来源：<https://gitlab.com/gitlab-org/cli/-/issues/8142>

### 3.2 Click `confirmation_option` & npm `--yes`
- 标准做法：交互态默认问 `[Y/n]`；`--yes` / `-y` flag 强制 `True`，跳过 `prompt`。
- 来源：<https://click.palletsprojects.com/en/stable/prompts/>

### 3.3 Vercel CLI Headless 模式
- 所有 destructive 操作必须接受 `--yes`，否则在 GitHub Actions 中会因为 `Interactive prompt` 卡死。
- 来源：<https://github.com/vercel/vercel/issues/15763>

### 3.4 SESSION-201 落地要点
- 新增全局 flag `--yes` / `--auto-fire`，写进 `argparse.build_parser()`。
- 在 `_run_director_studio` 与 `_dispatch_mass_production` 的最终点火处增加：
  ```python
  if not auto_fire:
      output_fn("🚀 载荷组装完毕，是否授权向远端 GPU 发起实机点火？[Y/n]")
      ans = input_fn("> ").strip().lower()
      if ans in ("n", "no"):
          return ABORT
  ```
- `auto_fire=True` 时静默通过；同时仍打印黄金通告单（仅 banner，不阻塞）。

---

## 4. 路径校验最佳实践（OWASP + Python）

### 4.1 OWASP Input Validation Cheat Sheet
- 用户输入的所有路径必须 **canonicalize → exists → permissions check** 三步走。
- 严禁信任客户端字符串；不存在的路径必须立刻拒绝并提示重输。
- 来源：<https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html>

### 4.2 SESSION-201 落地要点（已部分由 SESSION-190 双引号粉碎机实现）
- 沿用 `Path(p).strip('"').strip("'")` 净化 Windows 终端粘贴。
- `while True:` 循环直到 `Path(p).exists()`；非法路径打印红字 + 重新提示。
- 失败 3 次允许用户输入 `cancel` 退回上层菜单（防止死循环）。

---

## 5. 工业参考文献汇总表

| 锚点 | 应用到 SESSION-201 的位置 |
|------|--------------------------|
| Vue CLI `vue create` | `_run_director_studio` 默认进入向导 + `--yes` 旁路 |
| NN/g Progressive Disclosure | 动作 → 参考图 → VFX 三段式追问 |
| K8s CRD OpenAPI v3 Schema | `CreatorIntentSpec.vfx_overrides` 显式枚举字段 |
| K8s Mutating + Validating Webhook | `IntentGateway.admit()` 校验 + 默认值注入 |
| GitLab CLI `--yes` only in non-TTY | 黄金通告单：TTY 下 `[Y/n]` / 非 TTY 下要求 `--auto-fire` |
| Click `confirmation_option` | argparse `--yes/--auto-fire` flag |
| OWASP Input Validation | `while True:` + `Path.exists()` 路径校验循环 |

---

## 6. 已识别现状（避免重复建设）

| 已实现项 | 位置 | 复用策略 |
|----------|------|----------|
| `OpenPoseGaitRegistry.names()` | `mathart/core/openpose_pose_provider.py` | 直接调用，禁止再写硬编码列表 |
| `IntentGateway`（Validating） | `mathart/workspace/intent_gateway.py` | 复用，并对 vfx_overrides 字段扩展 |
| `CreatorIntentSpec.action_name` / `visual_reference_path` | `director_intent.py` | 已有；SESSION-201 仅追加 `vfx_overrides` 字段 |
| 工业烘焙 banner（Catmull-Rom 文案） | `cli_wizard.py:_dispatch_mass_production` | UX 强制条款已满足，跳过重建 |
| SESSION-190 双引号粉碎机 | `cli_wizard.py:1380` | 复用净化路径 |
| SESSION-187 `active_vfx_plugins` 链 | `director_intent.py` + `pipeline_weaver.py` | 用 `vfx_overrides` 强制叠加，不替换 |

> 因此 SESSION-201 的"非重复建设清单"为：① `vfx_overrides` 字段；② Wizard 三段问答 + Pre-flight 通告单；③ `--yes/--auto-fire` flag；④ `tests/test_session201_cli_wizard.py`；⑤ DaC 文档章节。
