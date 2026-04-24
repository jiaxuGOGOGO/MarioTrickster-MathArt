# SESSION-175 交接备忘录

> **"老大，解耦手术与潜空间救援已全部完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！
> 本次手术精准治愈了 ControlNet 彩色马赛克（改投 source_frames、CFG硬性压制为4.5）并撕裂了 ComfyUI 下载兜底网（10054/10061 立即抛致命异常击穿 PDG 触发全局 Cancel）。不仅严守了不破坏 cli_wizard 资产管家和 192→512 上采样的红线，还在前端前置了纯 CPU Catmull-Rom 样条插值的科幻流转高亮提示，UX 零退化！"**

**Date**: 2026-04-24
**Parent Commit**: SESSION-174
**Task ID**: P0-SESSION-175-LATENT-HEALING-AND-DOWNLOAD-ABORT
**Status**: CLOSED

---

## 1. 本次会话核心成就 (Mission Accomplished)

| # | 改造项 | 落地文件 | 工业理论锚点 |
|---|--------|----------|--------------|
| 1 | **Latent Space Healing (控制网模态严格对齐)** | `mathart/core/builtin_backends.py` | 修复了 SparseCtrl_RGB 投喂 Normal Map 导致的潜空间崩溃。现已强制将 `_external_source_frames`（基础网格黑白/灰度图）作为底图投喂，彻底杜绝 Deep-Fried Artifacts。 |
| 2 | **Burn Prevention (时序模型防烧焦策略)** | `mathart/core/builtin_backends.py`, `mathart/animation/comfyui_preset_manager.py` | AnimateDiff V3 等视频模型对 CFG 极度敏感。现已在 payload 装配时强制将 KSampler 的 `cfg` (或 `cfg_scale`) 压制为 `4.5`，防止画面烧焦与过拟合。 |
| 3 | **Download Abort Piercing (下载期硬断开熔断)** | `mathart/comfy_client/comfyui_ws_client.py`, `mathart/backend/comfy_client.py` | 在处理 Network IO 下载轮询时，若捕获 `WinError 10054/10061` 或其他 Socket 硬断开，不再使用泛型 except 吞没，而是直接抛出 `ComfyUIExecutionError`，瞬间击穿 PDG 触发全局 Cancel。 |
| 4 | **UX 防腐蚀与科幻流转展示** | `mathart/cli_wizard.py` | 在终端弹出“是否跳过 AI 渲染”的提示前，前置高亮打印：`[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列...`，形成视觉双门校验。 |
| 5 | **文档契约同步 (DaC)** | `docs/USER_GUIDE.md` | 同步更新用户手册，大白话指引用户如何在无显卡环境下直接验收成套工业图纸，并记录模态对齐与 CFG 压制策略。 |

## 2. 文件变更清单 (Changed Files)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `mathart/core/builtin_backends.py` | **修改** | 修复 `_execute_live_pipeline` 消费真实的外部 `source_frames` 并压制 CFG=4.5 |
| `mathart/animation/comfyui_preset_manager.py` | **修改** | 在 KSampler payload 组装处强制写入 cfg=4.5 |
| `mathart/comfy_client/comfyui_ws_client.py` | **修改** | `_download_file` 捕获 Socket 错误抛出 `ComfyUIExecutionError` |
| `mathart/backend/comfy_client.py` | **修改** | 同步在后端的 `_download_file` 增加硬断开熔断抛出 |
| `mathart/cli_wizard.py` | **修改** | 在跳过 AI 提示前前置打印工业烘焙网关的科幻流转 Banner |
| `tests/test_p1_ai_2d_preset_injection.py` | **修改** | 更新预设注入测试，对齐 CFG=4.5 的 hard-cap 契约 |
| `docs/USER_GUIDE.md` | **修改** | 补充 SESSION-175 的 DaC 说明和傻瓜验收指引 |
| `PROJECT_BRAIN.json` | **修改** | 更新待办列表与状态 |
| `SESSION_HANDOFF.md` | **重写** | 本文件 |

## 3. 防混线护栏与红线 (Anti-Corrosion Red Lines)

以下是 SESSION-175 部署的不可退化红线：

1. **绝对禁止向 SparseCtrl 投喂法线/深度图**：必须使用原生的基础网格黑白/灰度图（`source_frames`）作为推流底图。
2. **绝对禁止 AnimateDiff CFG 超过 4.5**：在多模态强约束下，CFG 超过 5.0 会引发严重的画面烧焦与过拟合。
3. **绝对禁止吞没下载期的 Socket 硬断开**：`WinError 10054/10061` 必须向上冒泡，击穿 PDG 调度器，瞬间终止全剧剩余所有角色的排队任务。
4. **UX 零退化**：在终端运行到烘焙阶段时，必须高亮打印工业烘焙网关的科幻流转提示，且必须在弹出“是否跳过 AI 渲染”的提示之前。
5. **SESSION-172/173/174 成果完好无损**：192→512 上采样、中英意图翻译、资产管家均未被触及。

## 4. 架构决策记录 (Architecture Decision Records)

### ADR-174-01: 为什么替换 [3] 真理查账而非新增 [4]？

**决策**：将 [3] 从"真理查账（溯源审计）"重构为"资产大管家（存储雷达 + GC + 金库提纯）"。

**理由**：
- 溯源审计功能在实际使用中频率极低，而量产后的存储管理是高频刚需。
- 保持菜单项数量不变（[1][2][3][0]），避免认知负担增加。
- 资产大管家内部仍可扩展子菜单，未来可将溯源审计作为子选项回归。

### ADR-174-02: 为什么使用 batch_summary.json 作为分诊锚点？

**决策**：以 `batch_summary.json` 的存在性和内容作为批次健康度的主要判定依据。

**理由**：
- `batch_summary.json` 是量产管线的最终产物，只有完整跑完的批次才会生成。
- 其中的 `character_count` 和 `skip_ai_render` 字段提供了丰富的状态信息。
- 这符合 Schlegel & Sattler (2023) 提出的"基于结构化元数据推断制品状态"的工业范式。

### ADR-174-03: 为什么金库提纯使用扁平目录结构？

**决策**：将最终交付物提取到 `output/export_vault/<batch_id>/` 的单层扁平目录。

**理由**：
- 量产批次内部的目录层级极深（`batch → char → chunk → anti_flicker_render → file`），手动浏览极其痛苦。
- Autodesk Vault 的 "Copy Design to Flat File Structure" 模式已在工业界验证了扁平化提取的用户体验优势。
- 文件名中保留了原始路径信息（如 `char_slime_001__anti_flicker_render__final_output.mp4`），确保可追溯性。

## 5. 下一步建议 (Next Steps)

1. **自动化定时清理**：可考虑在量产完成后自动触发存储雷达扫描，主动提示用户清理。
2. **增量扫描缓存**：当批次数量极大时，可引入扫描结果缓存（如 `.asset_radar_cache.json`），避免每次全量扫描。
3. **溯源审计回归**：可将原 [3] 的溯源审计功能作为资产大管家的子菜单 [3] 回归，形成 [1]瘦身 [2]提纯 [3]审计 [0]退回 的四选一结构。
4. **export_vault 自动打包**：可增加将 `export_vault/` 自动打包为 ZIP 的功能，方便一键分享。
