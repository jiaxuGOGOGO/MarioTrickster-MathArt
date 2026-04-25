# RESEARCH NOTES — SESSION-196 (P0 CLI Intent Threading + Orphan Rescue Phase 2)

> Session focus: 打通 CLI / `intent.yaml` 用户意图（`action` + `reference_image`）从前端网关到底层 ComfyUI/OpenPose payload 组装站点的**无损穿透闭环**；同时利用现有 `@register` 多轨注册表机制，对第二批高阶物理孤立模块（Orphan Rescue Phase 2）实施 IoC 发现与 VFX 插件挂载。

本文件汇总三大顶级工业 / 学术参考范式，并在每段末尾给出对本项目 (`MarioTrickster-MathArt`) 的**实施锚点**（Implementation Anchor）。

---

## 1. Redux / React Context — 单向数据流与不可变上下文透传

### 范式核心
Redux 强制 **State → View → Action → Reducer → State** 的单向闭环，并要求 reducer 是纯函数，state 必须是不可变快照 (immutable snapshot)。React Context 则解决了"prop drilling"——开发者无需为了把 `theme` 从 `App` 传到深层 `Button`，把每一层中间组件的函数签名都加一个 `theme` 形参。Context 把数据"挂在空中"，任意深度的组件都能 `useContext(...)` 一级取值。

### 反模式
| 反模式 | 后果 |
| --- | --- |
| 在中间函数加 `theme` / `user` 形参 | "签名污染"，重构地狱 |
| 在 reducer 里 `state.foo = bar` | 破坏不可变性，Time-travel debug 失效 |
| 子组件直接修改 props | 双向耦合，调试链断裂 |

### 实施锚点 (本项目)
- **Context Payload 化**：将 CLI Wizard / `intent.yaml` 解析出的 `action_name` + `reference_image_path` 一次性写入 `validated`（`director_studio_spec`）字典——这就是项目里的 "Context"。
- **零形参污染**：从 `cli_wizard.py` → `_execute_live_pipeline` → `bake_openpose_pose_sequence` 全链路，**禁止给中间任何函数新增 `action_name=` / `ref_img=` 形参**。底层在自己的 chunk 组装站点用 `extract_action_name(validated)` / `extract_visual_reference_path(validated)` **一级取值**。
- **不可变快照**：`director_studio_spec` 一旦构建完毕，下游只读不写。这与 SESSION-195 已有的 `extract_visual_reference_path()` 三级寻址完全同构，只是新增 `extract_action_name()` 兄弟函数。

---

## 2. ROS 2 — 节点发现、生命周期管理与 Registry Scanner

### 范式核心
ROS 2 把每一个机器人功能（激光雷达驱动、SLAM、控制器）封装为独立 **Node**。Node 通过 DDS 发现机制自动加入网络，并遵循 **lifecycle state machine**：`Unconfigured → Inactive → Active → Finalized`。系统不需要在任何"主调度器"里硬编码 `import lidar_driver`；只要 Node 在 `package.xml` 注册并在 ROS 2 网络中"亮相"，编排器就能发现它、按需激活它、按需关闭它。**主干永远不污染**。

### 反模式
| 反模式 | 后果 |
| --- | --- |
| 主调度器写 `if has_lidar: import lidar_driver` | 主干强耦合所有传感器，单一节点崩溃带崩主干 |
| 节点直接访问全局 state | 失去隔离性，无法热插拔 |
| 启动时同步加载所有节点 | 启动时间不可控，资源浪费 |

### 实施锚点 (本项目)
- **Registry Scanner**：项目已有 `mathart/core/backend_registry.py`（`@register` 装饰器 + `BackendRegistry`）。在收容 `fluid_momentum_backend.py`、`physics3d_backend.py` 等"高阶物理孤儿"时，**严禁在 orchestrator 里写 `if enable_physics: import physics3d`**。正确做法：在模块顶部加 `@register(name="fluid_momentum_vfx", lane="vfx_overlay")`，让注册表在 import 时自动收纳。
- **生命周期沙盒**：每个 VFX 插件用 `try/except` 包裹（类似 ROS 2 的 lifecycle `Inactive` 兜底），算子内部异常只允许"平滑降级"成 no-op，**严禁带崩主干 pipeline**。
- **按需激活**：上层 `validated["vfx_intents"] = ["fluid_momentum"]` 时才被注册表拉起；其它情况下保持休眠。

---

## 3. Kubernetes Admission Webhook — 边缘准入控制层与 Fail-Fast

### 范式核心
K8s API server 在写入 etcd 之前会调用 **Validating Admission Webhook**（拒绝非法资源）和 **Mutating Admission Webhook**（修订资源）。webhook 是**集群最外缘**的"门神"——只要请求不合规，**立刻在网关层 reject**，根本不让脏数据进入存储层、调度层、执行层。`failurePolicy: Fail` 表示 webhook 不可达时直接拒绝请求（Fail-Closed）。

### 反模式
| 反模式 | 后果 |
| --- | --- |
| 在 etcd 写入之后才校验 | 脏数据已落盘，回滚成本爆炸 |
| webhook 无声吞掉异常 | 集群进入未定义状态 |
| 在 deep call site 才发现路径不存在 | 调用栈深、错误信息丢失、用户无法定位 |

### 实施锚点 (本项目)
- **CLI / YAML 网关即门神**：`cli_wizard.py` 与 `intent.yaml` 解析逻辑就是本项目的 "API server admission layer"。
  - **Action 校验**：`action` 必须 ∈ `get_gait_registry().names()`（动态查询），否则**第一秒**抛 `IntentValidationError("unknown action: dash → expected one of [walk, run, jump, idle, dash]")`。
  - **参考图物理探活**：`reference_image` 给定时，`Path(p).is_file()` 必须为 True，否则同样 Fail-Fast 抛 `IntentValidationError("reference_image not found: /tmp/ghost.png")`。
- **failurePolicy = Fail (Closed)**：严禁在底层"隐式回退到默认 walk + 静默跳过换脸"。**回退即背叛**——必须让用户在终端第一行就看到红色错误。
- **MutatingWebhook 定位**：网关在校验通过后，将 `action_name` 与 `reference_image_path` **写入 `validated["director_studio_spec"]`** —— 这就是 mutating 阶段，把意图固化进上下文 payload，下游纯消费。

---

## 4. 三层进化循环（Three-Layer Evolution Loop）落地映射

| 层 | 角色 | SESSION-196 体现 |
| --- | --- | --- |
| **L1 内部进化** | 注册表自动发现 + 适配器无损挂载 | `ORPHAN RESCUE` Phase 2 把 `fluid_momentum`、`physics3d` 包成 VFX Adapter |
| **L2 知识蒸馏** | 把工业范式提炼为内部纪律 | 本研究文档 + `cli_wizard` Fail-Fast / `validated` payload 透传 |
| **L3 自我迭代测试** | 端到端拦截测试守门 | `tests/test_session196_intent_threading.py` 强制断言 `action_name` 已喂给 OpenPose、`ref_img` 已喂给 IPAdapter |

---

## 5. 红线总览（Anti-Pattern Red Lines for SESSION-196）

1. 🚫 **反隐式降级**：网关 Fail-Fast，禁止盲目兜底成 walk / 静默跳过换脸。
2. 🚫 **反面条加载**：禁止在主调度器硬编码 `import physics3d`；只能通过 `@register` + `try/except` 沙盒。
3. 🚫 **反签名污染**：禁止在中间函数加 `action_name=` / `ref_img=` 形参；统一走 `validated` 字典一级取值。
4. 🚫 **反自欺测试**：必须新增端到端拦截测试，断言 OpenPose 渲染了对应步态、ComfyUI Payload 携带了真实图源路径。

---

## 6. 参考资料链接

- Redux Fundamentals — Concepts & Data Flow: <https://redux.js.org/tutorials/fundamentals/part-2-concepts-data-flow>
- React Context API: <https://react.dev/learn/passing-data-deeply-with-context>
- ROS 2 Managed Nodes / Lifecycle: <https://design.ros2.org/articles/node_lifecycle.html>
- ROS 2 Discovery (DDS): <https://docs.ros.org/en/jazzy/Concepts/Intermediate/About-Discovery.html>
- Kubernetes Dynamic Admission Control: <https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/>
- K8s Admission Webhook Good Practices: <https://kubernetes.io/docs/concepts/cluster-administration/admission-webhooks-good-practices/>
- Martin Fowler — Evolutionary Architecture / Fitness Functions (背景延续): <https://martinfowler.com/articles/evolutionary-architecture.html>
