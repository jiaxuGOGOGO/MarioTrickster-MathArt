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

## [DISTILL-002] PROMPT_RECIPES 全量蒸馏 — 2026-04-14

**来源**：MarioTrickster-Art/PROMPT_RECIPES.md（648行全文）—— 松岡/砂糖/みにまる/Peter Han/Christopher Hart/室井康雄/OCHABI/吉田誠治/Telecom Bible/Telecom2
**蒸馏内容**：将 PROMPT_RECIPES 中所有可数学化的美术规则蒸馏进 7 个 knowledge/ 文件，并更新 compiler.py 参数映射表和 parser.py 关键词库
**知识沉淀**：
- `knowledge/anatomy.md` — 大幅扩展：额外关节 ROM（手腕/足首）、肌肉联动法则、筋肉影禁止法则、5 Core Shapes、顔パーツ比率、手/足/耳/髪描画法则、目の形状バリエーション、シワ 5 パターン、ジェスチャードローイング、直線曲線組合せ、模写 3 段階
- `knowledge/animation.md` — 大幅扩展：コマ打ち帧数換算表、歩行周期法则、Squash & Stretch 体積保存、予備動作/フォロースルー、エフェクトタイミング、VFX ループ、キャラモデル一貫性、安全フレームオーバーサイズ、セルレイヤー分離、宽高比テーブル、動画 QA チェックリスト
- `knowledge/perspective.md` — 大幅扩展：場景分治規則、消失点安全法則、空気遠近法 6 技法、箱パース、前縮法則、OCHABI 6 等分構図、衰撃波グリッド、スピード感集中線
- `knowledge/color_light.md` — 大幅扩展：光の基本表現、時間帯別色温テーブル、キラキラ/ガラス光、エフェクト色彩 3 軸、色重量密度、炎色彩グラデーション、爆裂光、アニメ顔影山形法則、雲の種類別光影、水面反射/粘液光沢、3 光源ワークフロー
- `knowledge/vfx.md` — 大幅扩展：エフェクト基本形状 5 分類、発生消滅フロー、炎/爆発/雷/ビーム/魔法陣描画法則、破砕材質別飛散、水しぶき、風の不可視表現、スケール感、レイヤー分離、実装優先度、星キラキラ、F.I./F.O. 転場効果
- `knowledge/game_feel.md` — 拡展：ヒットスパーク、エフェクトキャラ相互作用、打撃感の数学モデル（5 要素同時発動）
- `knowledge/pixel_art.md` — 拡展：エフェクトピクセル表現法則、AI 生成パイプライン設定参照
- `knowledge/game_design.md` — 拡展：実体ブループリント库（角色/敵/地形/罠/交互物）の視覚特徴語 + SDF プリセット対応
**コード改動**：
- `mathart/distill/compiler.py` — _PARAM_MAPPING に 42 個の新パラメータマッピング追加（VFX/Animation/Perspective/Anatomy 各ドメイン）
- `mathart/distill/parser.py` — _MODULE_KEYWORDS に 4 モジュール分の新キーワード追加（ANIMATION/ANATOMY/VFX/PERSPECTIVE）
**テスト**：175 個のテスト、全部通過
**commit**：`feat: distill PROMPT_RECIPES.md - 7 knowledge files + 42 param mappings + 4 keyword sets`

---

*下一次蒸馏将从这里继续追加...*

---

## [DISTILL-003] 自进化大脑架构蒸馏 — 2026-04-14

**来源**：数学驱动统一美术生产：全网深度研究报告（Manus AI 调研报告，用户上传附件）+ 并行搜集最新数学驱动美术生产相关论文（6 个方向：可微渲染/程序化生成/物理动画/SDF/色彩科学/知识蒸馏）

**蒸馏内容**：基于报告的 10 个研究维度，提取 PCG 数学、PBR 渲染、SDF 数学、色彩科学、程序化动画、可微渲染等领域的核心知识规则，并将其落地为三层自进化架构。

**知识沉淀**：
- `knowledge/pcg_math.md` — Perlin 噪声参数（octaves/persistence/lacunarity）、WFC 熵阈值、L-System 分叉角度
- `knowledge/pbr_math.md` — Cook-Torrance BRDF 参数、材质预设表（皮肤/布料/金属/玻璃）
- `knowledge/sdf_math.md` — SDF 基础图元公式、布尔运算（含平滑并集 smin）、光线行进参数
- `knowledge/color_science.md` — OKLAB/OKLCH 参数范围、Floyd-Steinberg 抖动参数
- `knowledge/procedural_animation.md` — 弹簧阻尼参数（spring_k/damping_c）、FABRIK IK 参数、缓动函数
- `knowledge/differentiable_rendering.md` — 可微渲染升级路径、能力缺口说明

**代码落地**：
- `mathart/evaluator/` — 新增 `AssetEvaluator`（5 维质量评分：清晰度/调色板/对比度/风格/和谐度）
- `mathart/evolution/engine.py` — `SelfEvolutionEngine`（三层自进化架构总协调器）
- `mathart/evolution/inner_loop.py` — `InnerLoopRunner`（质量驱动遗传算法迭代）
- `mathart/evolution/outer_loop.py` — `OuterLoopDistiller`（LLM 知识蒸馏引擎，支持跨会话连续性）
- `mathart/evolution/math_registry.py` — `MathModelRegistry`（8 个稳定模型 + 1 个实验模型）
- `mathart/evolution/cli.py` — `mathart-evolve` 命令行工具（status/distill/registry/eval/gaps）
- `mathart/animation/physics.py` — `SpringDamper` + `FABRIKSolver` + `PerlinAnimator`
- `ARCHITECTURE_EVOLUTION.md` — 完整的三层自进化架构设计文档

**测试**：新增 66 个测试（test_evaluator.py 22 个 + test_evolution.py 21 个 + test_physics.py 23 个），总计 241 个测试全部通过

**版本**：v0.2.0 → v0.3.0

**commit**：`feat: implement self-evolution engine (inner loop, outer loop, math registry)`

---

*下一次蒸馏将从 [DISTILL-004] 继续追加...*
