# 蒸馏日志

> 记录每次知识蒸馏的来源、内容和对项目的影响。按时间倒序排列。

---

## [DISTILL-000] 初始知识迁移 — 2026-04-14

**来源**：MarioTrickster-Art/PROMPT_RECIPES（松岡/砂糖/みにまる/Peter Han/OCHABI/吉田誠治）+ Disney 12 Principles + 主仓库 TA_AssetValidator
**蒸馏内容**：从旧美术管线迁移基础蒸馏知识，建立知识大脑的初始骨架
**知识沉淀**：
- `knowledge/anatomy.md` — 头身比系统、关节可动域（ROM）
- `knowledge/animation.md` — 12 动画法则数学映射、跑步周期关键帧
- `knowledge/color_light.md` — 暖光冷影法则、3 值色阶、环境光反射、限色约束
- `knowledge/perspective.md` — 中线偏移深度、重叠深度、四面不等大、透视工作流
- `knowledge/unity_rules.md` — PPU/Filter/Pivot/命名/目录硬约束
**代码改动**：
- `mathart/oklab/` — OKLAB 调色板生成器，实现暖光冷影和谐模式
- `mathart/sdf/` — SDF 特效生成器，实现地刺/火焰/锯片预设
- `mathart/animation/` — 骨骼动画系统，实现 ROM 约束和 12 法则缓动曲线
- `mathart/export/` — Unity 导出桥接层，实现 PPU/Filter/Pivot 校验
**测试**：88 个初始测试
**commit**：项目初始提交

---

## [DISTILL-001] v0.2.0 宏观数学驱动扩展 — 2026-04-14

**来源**：数学驱动统一美术生产深度研究报告 + MarioTrickster 主项目关卡系统分析
**蒸馏内容**：将数学驱动从微观（像素/动画）延伸到宏观（关卡/植物），建立知识蒸馏管道
**知识沉淀**：
- 关卡系统 19 种元素映射（与主项目 Level Studio ASCII 系统对齐）
- 经典关卡片段库（tutorial_start, trap_corridor, vertical_climb 等）
- L-系统植物文法规则（5 种预设：橡树/灌木/藤蔓/蕨类/花卉）
**代码改动**：
- `mathart/level/` — WFC 关卡生成器（波函数坍缩算法）
- `mathart/sdf/lsystem.py` — L-系统植物程序化生成
- `mathart/distill/` — 三层蒸馏管道（感知/编译/寻优）
- `.github/workflows/ci.yml` — GitHub Actions CI
**测试**：167 个测试，全部通过
**commit**：`feat: add WFC level generator, L-System plant generator, knowledge distillation pipeline`

---

*下一次蒸馏将从这里继续追加...*
