# 需求 vs 待办覆盖关系核对

## 用户需求逐项提取 → 对应实现/待办

| # | 用户需求（原文提炼） | 已实现的代码 | 待办中的差距 | 覆盖？ |
|---|----------------------|-------------|-------------|--------|
| 1 | 内部自循环：根据产出美术资产质量进行迭代 | InnerLoopRunner + EvolutionaryOptimizer + AssetQualityEvaluator | TASK-009: 缺 CLI 入口 `mathart-evolve run` | ✅ |
| 2 | 蒸馏知识贯穿迭代全程（不只是末尾打分） | ArtMathQualityController 4 个检查点 | TASK-001: mid_generation 还没接入 | ✅ |
| 3 | 本地无 AI 也能迭代不停止 | AUTONOMOUS 模式 + try/except 降级 | 无差距 | ✅ |
| 4 | 有 AI 时能贯穿全程更好 | ASSISTED 模式 + LLM arbitrator | 无差距 | ✅ |
| 5 | 上传 PDF → Manus 蒸馏 → 推送 → 本地进化 | KnowledgeParser + distill CLI | 工作流说明已写入（持续性工作） | ✅ |
| 6 | 数学论文主动搜索（arXiv/GitHub/Reddit） | MathPaperMiner（LLM 模拟） | TASK-011: 需接入真实 API | ✅ |
| 7 | 借鉴 GitHub/Reddit 优秀项目 | MathPaperMiner 支持 GitHub 源 | TASK-011: 同上 | ✅ |
| 8 | Sprite/SpriteSheet 学习 | SpriteAnalyzer + SpriteLibrary + CLI | 无差距 | ✅ |
| 9 | 去重不遗漏 | DeduplicationEngine（3层去重） | 无差距 | ✅ |
| 10 | 拒绝无效迭代 + 诊断报告 | StagnationGuard（4种诊断 + 自动恢复） | 无差距 | ✅ |
| 11 | Unity Shader 学习 | shader/generator.py + knowledge/unity_rules.md | TASK-005: 深度集成需 Unity | ✅ |
| 12 | 伪 3D 预留 | shader/pseudo3d.py（深度排序/法线/视差/billboard） | TASK-005: 同上 | ✅ |
| 13 | 跨对话衔接 | SESSION_HANDOFF.md + PROJECT_BRAIN.json | 无差距 | ✅ |
| 14 | 工作区管理（不用手动输文件名） | inbox/ 热文件夹 + 弹窗选文件 + output/ 分类 | 无差距 | ✅ |
| 15 | 噪声纹理生成 | noise.py（6种算法+6种预设） | 无差距 | ✅ |
| 16 | 高维参数空间收敛效率 | EvolutionaryOptimizer（基础GA） | TASK-010: 需升级 CMA-ES | ✅ |
| 17 | 关卡生成→资产导出连接 | LevelSpecBridge + ExportBridge（各自完整） | TASK-003: 需桥接 | ✅ |
| 18 | GPU/硬件加速 | differentiable_renderer_2d（骨架） | TASK-005: 需用户硬件 | ✅ |
| 19 | AI 接手后给菜单选项 | SESSION_HANDOFF.md 第6条 CRITICAL | 无差距 | ✅ |
| 20 | 定期审计差距并更新待办 | ？ | 需要写入 AI 指引 | ❌ 缺失 |

## 结论
- 19/20 项需求已覆盖
- 第 20 项"定期审计差距"机制尚未写入 AI 指引，需要补充
