# SESSION-066 Golden Path 设计说明

## 结论摘要

当前仓库**不需要从零重写架构**。`SESSION-064` 已经提供了用户所要求 Golden Path 的主骨架：`ArtifactManifest`、装饰器驱动的 `BackendRegistry`、按车道隔离的 `MicrokernelOrchestrator`、以及 `ThreeLayerEvolutionLoop`。因此，本轮最优策略是对既有骨架做**强类型硬化、命名正规化、旧总线兼容收口、每日守护补齐**，而不是再发明一套新系统。

| 用户要求 | 仓库现状 | 本轮动作 |
|---|---|---|
| 强契约 `ArtifactManifest` + `BackendType` | 已有 `ArtifactManifest`，但 `backend_type` 仍是 `str` | 引入 `BackendType` 枚举与解析层，升级 manifest、registry、bridge 与内置后端 |
| `AssetPipeline` 动态注册表模式 | 已有 `@register_backend`，但 `AssetPipeline` 仍主要是硬编码业务入口 | 给 `AssetPipeline` 增加注册表驱动入口与后端运行接口，保留旧 API |
| `EvolutionOrchestrator` 联邦制 / Meta-report | `MicrokernelOrchestrator` 已实现该思想；旧 `EvolutionOrchestrator` 仍是 SESSION-055 风格 | 将旧 orchestrator 改造成**联邦 facade**，对外兼容、对内委托 microkernel + evolution loop |
| 每日遍历 Registry 的 E2E 守护 | 有局部测试与 headless E2E，但缺统一注册表巡检入口 | 新增脚本 + 测试 + GitHub Actions `schedule`/`workflow_dispatch` |
| 历史强权资产正规化入轨 | 工业渲染、Unity URP 2D、抗闪烁路径已经存在，但未完全按用户新命名统一 | 将其正规化为明确后端类型、别名与 manifest 输出 |
| 三层进化循环 | 已有 `ThreeLayerEvolutionLoop` | 对齐到新的后端类型与联邦编排，并让审计/文档反映其可继续自迭代 |

## 关键设计决策

### 1. 后端类型采用“强枚举 + 宽兼容解析”

直接把历史字符串全部硬切会引发大面积回归。因此应采用：

| 设计项 | 方案 |
|---|---|
| 主类型系统 | 新增 `BackendType` 枚举 |
| 用户要求核心值 | `motion_2d`、`urp2d_bundle`、`industrial_sprite`、`dimension_uplift_mesh` |
| 兼容历史命名 | 允许 `dimension_uplift`、`unity_urp_2d`、`industrial_sprite_bundle`、`unity_urp2d_bundle`、`anti_flicker_render` 等别名解析到规范类型 |
| 数据落盘策略 | `ArtifactManifest.to_dict()` 统一写规范值；`from_dict()` 容忍旧值并自动正规化 |

### 2. 联邦编排不重复造轮子

`MicrokernelOrchestrator` 已具备“车道隔离、禁止跨车道平均、只做 Meta-report”的核心特性，因此本轮不修改其原则，只做两个动作：一是让旧 `EvolutionOrchestrator` 变成 facade，二是把报告字段命名与用户期望的 lane/backend 术语对齐。

### 3. `AssetPipeline` 只做“插座化”入口，不强拆业务 API

用户要求先建插座、再造插头并封装老代码。这里的“插座”应理解为：

| 层 | 动作 |
|---|---|
| 核心层 | 后端类型枚举、注册表、manifest 合约 |
| 管线层 | `AssetPipeline.run_backend()` / `run_registered_backends()` 等统一入口 |
| 业务层 | 现有 `produce_character_pack()`、`produce_vfx()` 等继续保留，但允许逐步回填为标准后端 |

### 4. 每日守护采用“双层结构”

| 层级 | 作用 |
|---|---|
| Python 脚本 | 在本地和 CI 中统一遍历注册表后端、执行最小上下文 E2E、输出 JSON/Markdown 报告 |
| GitHub Actions | 使用 `workflow_dispatch` + `schedule` 每日运行，避免脚本存在但无人调用 |

### 5. 三层进化循环按“内部进化 / 外部蒸馏 / 自我迭代测试”继续演进

本轮应把 Golden Path 与三层循环直接扣合：

| 循环层 | 对应落实 |
|---|---|
| 内部进化 | 联邦车道评估所有已注册后端/生态位 |
| 外部知识蒸馏 | 把本轮研究结论、别名迁移规则、守护策略写回知识与项目记忆 |
| 自我迭代测试 | 用注册表 E2E 巡检、单元测试、文档审计构成自动回归闭环 |

## 直接实现计划

1. 新增 `BackendType` 枚举与解析工具，并升级 artifact/registry/bridge 使用方式。
2. 正规化内置后端：`motion_2d`、`urp2d_bundle`、`industrial_sprite`、`dimension_uplift_mesh`，同时保留旧名兼容。
3. 在 `AssetPipeline` 中加入注册表驱动入口。
4. 把 `EvolutionOrchestrator` 改造成对 `MicrokernelOrchestrator` / `ThreeLayerEvolutionLoop` 的兼容 facade。
5. 新增注册表全量巡检脚本、测试与 GitHub Actions 每日工作流。
6. 更新 TODO / handoff / brain / tracker / dedup / audit，并完成提交推送。
