# SESSION-174 研究笔记：资产治理与金库提纯

## 研究主题

本次研究聚焦于三大工业级资产管理范式，为 P0-SESSION-174-ASSET-GOVERNANCE-AND-VAULT 任务提供理论锚点。

## 1. Artifact Lifecycle Management & Garbage Collection

在自动化量产管线中，因报错、打断或参数调试产生的无效 Batch 会引发严重的存储膨胀（Storage Bloat）。业界的标准做法是基于元数据状态进行自动分诊，区分 Ephemeral Junk（临时废品）与 Golden Artifacts（黄金产物）。

**核心参考文献**：

| 来源 | 核心观点 | 落地映射 |
|------|----------|----------|
| JFrog Artifactory GC Guide (2022) | 基于元数据的两阶段 GC：先标记(mark)再清除(sweep)，通过 Retention Policy 自动回收过期制品 | `batch_summary.json` 作为元数据锚点，判定批次完整性 |
| Argo Workflows Artifact GC | 在 Workflow 和 Artifact 两个层级定义 GC 策略，临时制品自动回收 | 扫描 `output/production/` 下所有批次，按状态分类 |
| Schlegel & Sattler, ACM SIGMOD Record (2023) | ML 生命周期制品管理：artifact 的状态推断依赖结构化元数据 | 通过 `batch_summary.json` 中的 `character_count` 和 `skip_ai_render` 字段推断批次健康度 |

## 2. Gold Master Vault Segregation

在复杂渲染管线中，中间态图纸（法线、深度、未加工的骨骼图）与最终交付态（最终 AI 渲染出的视频和图集）深埋在极其复杂的嵌套文件树中。必须实施"交付提取（Vault Extraction）"。

**核心参考文献**：

| 来源 | 核心观点 | 落地映射 |
|------|----------|----------|
| Autodesk Vault "Copy Design to Flat File Structure" | 使用 Copy Design 将嵌套设计文件平铺到单层扁平结构 | `output/export_vault/<batch_id>/` 扁平提取 |
| Render Farm Best Practices (SuperRenders 2026) | 渲染农场输出管理：最终帧与中间帧分离存储 | 只提取 `*.mp4` 和高清 `*.png`，忽略中间态 |
| Immutable Source Data Principle (SESSION-172) | 提取时使用 copy 而非 move，保持原始数据完整 | `shutil.copy2` 复制，原始批次目录不动 |

## 3. Human-in-the-Loop Safe Pruning

涉及大批量 `shutil.rmtree` 操作时，必须具备绝对的爆炸半径限制（Blast Radius Containment）。

**核心参考文献**：

| 来源 | 核心观点 | 落地映射 |
|------|----------|----------|
| "Blast Radius as a Design Constraint" (Medium 2026) | 操作范围即安全控制；大范围操作放大错误影响 | `assert "output" in path` 路径沙盒隔离 |
| Michael Nygard "Release It!" Circuit Breaker | 断路器模式：在故障传播前切断链路 | `PermissionError` 跳过而非崩溃 |
| Python shutil.rmtree Best Practices (Reddit 2021) | `onerror` 回调处理被占用文件，避免整体失败 | `shutil.rmtree(path, onerror=handler)` |

## 落地总结

以上三大研究成果已融合落实到 `mathart/factory/asset_governance.py` 模块中，实现了存储雷达扫描、智能分诊、安全核爆和金库提纯四大核心功能。所有操作严格遵循路径沙盒隔离和防呆异常处理的工业级安全标准。
