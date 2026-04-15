# SESSION_TRACKER — MarioTrickster-MathArt

> 本文件是 AI 接手本项目的第一读档点。

## 当前状态

| 维度 | 状态 |
|------|------|
| 版本 | v0.1.0 — 初始搭建 |
| 测试 | 58 passed, 0 failed |
| 模块 | OKLAB ✅ · SDF ✅ · Animation ✅ · Export ✅ |
| CI | GitHub Actions 已配置 |
| 蒸馏知识 | 从 MarioTrickster-Art/PROMPT_RECIPES 迁移完成 |

## 架构速览

```
mathart/
  oklab/        → 色彩空间转换 + 调色板生成 + 图像量化
  sdf/          → 2D SDF 图元 + 布尔运算 + 游戏特效 + 渲染器
  animation/    → 骨骼系统 + 动画曲线 + 预设动作 + Sprite Sheet 渲染
  export/       → Unity 约束校验 + 元数据生成 + 批量导出
knowledge/      → 蒸馏知识文档（解剖/透视/色彩/动画/Unity规则）
tests/          → 58 个测试（unit + integration）
```

## 防坑警告

1. **OKLAB 色域钳制**：高饱和度颜色可能超出 sRGB 色域，`PaletteGenerator._gamut_clamp()` 会自动降低 chroma。如果生成的颜色看起来灰暗，检查 chroma 参数是否过高。
2. **SDF 轮廓宽度**：`outline_width` 在归一化坐标系下，0.03 ≈ 1 像素（32px 精灵）。过大会吃掉填充区域。
3. **骨骼 FK**：当前 `forward_kinematics()` 是简化版，仅处理旋转不处理平移偏移。复杂姿态可能需要完善。
4. **导出校验**：`AssetExporter` 强制要求 RGBA 模式，RGB 图片会被拒绝。

## Session 日志

| # | 日期 | 内容 |
|---|------|------|
| 1 | 2026-04-14 | 项目初始搭建：4 模块 + 58 测试 + CI + 蒸馏知识迁移 |

---

## SESSION-018 (2026-04-15) — Foundation Overhaul

### Summary
Infrastructure session focused on fixing the core problems that prevented the system from producing real quality improvements through evolution. No new assets produced; focus was entirely on fixing the foundation.

### Changes Made

| Category | Change | File |
|----------|--------|------|
| Evaluator | Rewritten with 12 metrics (7 new pixel-art-specific) | `mathart/evaluator/evaluator.py` |
| Pipeline | Reference/palette now passed to evaluator | `mathart/pipeline.py` |
| Pipeline | GIF animation export | `mathart/pipeline.py` |
| Pipeline | Commercial sprite sheet metadata | `mathart/pipeline.py` |
| Pipeline | Fixed gem/star default radii | `mathart/pipeline.py` |
| CPPN | `create_enriched()` with hidden layers | `mathart/evolution/cppn.py` |
| CPPN | 70% enriched initial population | `mathart/evolution/cppn.py` |
| Animation | Verlet particle system (4 presets) | `mathart/animation/particles.py` |
| Animation | MVC cage deformation (4 presets) | `mathart/animation/cage_deform.py` |
| Knowledge | 12 production rules | `knowledge/rules.json` |
| Knowledge | 10 math models | `knowledge/math_models.json` |
| Process | Anti-duplication registry | `DEDUP_REGISTRY.json` |
| Process | Session efficiency protocol | `SESSION_PROTOCOL.md` |

### Research Conducted
8 parallel research directions:
1. Procedural pixel art generation algorithms
2. Sprite animation math models
3. Pixel art quality evaluation metrics
4. CPPN topology enrichment techniques
5. Game VFX particle systems
6. Commercial sprite sheet standards
7. itch.io pixel art asset benchmarks
8. 2D deformation techniques

### Test Results
- Evaluator: Blank=0.250(FAIL), Circle=0.687(PASS), Noise=0.597(FAIL) — proper discrimination
- Particle system: Fire/Explosion/Sparkle all produce valid frames and GIF
- Cage deformer: Squash-stretch/Wobble produce valid deformation frames

### Health Score
4.6 → 6.8 (+2.2)

### Next Priorities
1. P0-NEW-1: Integrate particles/cage into pipeline
2. P0-NEW-2: Run full evolution with new evaluator
3. P0-NEW-3: Palette-constrained SDF rendering

## SESSION-019 (2026-04-15) — Integration, Validation & Critical Bug Fix

### Summary
Integration and validation session that discovered and fixed a systemic SDF parameter order bug, implemented palette-constrained rendering, integrated particles and cage deformation into the main pipeline, and achieved 100% validation pass rate (19/19 tests).

### Critical Bug Fixed
**SDF Primitive Parameter Order** — All SDF primitives use `(cx, cy, ...)` signature, but callers used positional arguments assuming `(param1, param2, ...)`. For example, `star(5, 0.42, 0.22)` was interpreted as `cx=5, cy=0.42, r_outer=0.22`, placing the star at coordinates (5, 0.42) — completely off-screen. This was the true root cause of invisible gem/star shapes (SESSION-018 only increased radii, which didn't help). All 42 call sites across 8 files were converted to keyword arguments.

### Changes Made

| Category | Change | File(s) |
|----------|--------|---------|
| BUG FIX | SDF primitive kwargs (42 call sites) | `pipeline.py`, test files |
| Feature | Palette-constrained rendering (OKLAB Floyd-Steinberg) | `sdf/renderer.py` |
| Feature | `Palette.from_srgb_list()` class method | `oklab/palette.py` |
| Feature | `produce_vfx()` in AssetPipeline | `pipeline.py` |
| Feature | `produce_deform_animation()` in AssetPipeline | `pipeline.py` |
| Feature | Animation module exports updated | `animation/__init__.py` |
| BUG FIX | `result.overall` → `result.overall_score` | `pipeline.py` |
| Test | 19-test validation suite | `test_evolution_validation.py` |

### Validation Results (19/19 = 100%)

| Category | Tests | Key Scores |
|----------|-------|------------|
| Evaluator discrimination | 5 | good=0.725, medium=0.662, bad=0.642, noise=0.598 |
| Sprite production | 3 | coin=0.824, gem=0.770, star=0.754 |
| Animation | 1 | idle=0.824 |
| VFX | 4 | fire=0.494, explosion=0.240, sparkle=0.472, smoke=0.397 |
| Deformation | 3 | squash=0.796, wobble=0.796, breathe=0.796 |
| Palette constraint | 3 | all shapes constrained to 6 palette colors |

### Quality Improvements
- gem: 0.24 → 0.77 (+221%)
- star: 0.24 → 0.75 (+214%)
- coin: 0.78 → 0.82 (+5%)
- Validation: 84.2% → 100%

### Health Score
6.8 → 7.8 (+1.0)

### Next Priorities
1. P0-NEW-4: Multi-layer render compositing
2. P0-NEW-5: Large-scale evolution (100+ iterations)
3. P0-NEW-6: VFX evaluator tuning

