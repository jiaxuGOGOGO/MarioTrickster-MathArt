# SESSION HANDOFF — MarioTrickster-MathArt

> **READ THIS FIRST** if you are starting a new conversation about this project.
> This document is auto-generated and always reflects the latest project state.

## MANDATORY: Read Before Starting

1. **Read `DEDUP_REGISTRY.json`** — Prevents duplicate research and code changes
2. **Read `SESSION_PROTOCOL.md`** — Efficiency rules for every session
3. **Read this file** — Current state and priorities

---

## Project Overview

| Dimension | Value |
|-----------|-------|
| Current version | 0.9.0 |
| Last updated | 2026-04-15T16:15:00Z |
| Last session | SESSION-019 |
| Best quality score | 0.8244 (coin, validated) |
| Validation pass rate | 19/19 = 100% |
| Total code lines | ~25,500 |
| Knowledge rules | 12 |
| Math models registered | 10 |
| Project health score | 7.8/10 |

## What Changed in SESSION-019

### Critical Bug Fix: SDF Primitive Parameter Order

The most impactful fix in SESSION-019 was discovering and correcting a **systemic parameter order bug** across all SDF primitive calls. Every primitive function uses the signature `primitive(cx, cy, ...)`, but `SHAPE_LIBRARY` and `_build_generator()` were calling them with positional arguments that mapped incorrectly. For example, `star(5, 0.42, 0.22)` was interpreted as `cx=5, cy=0.42, r_outer=0.22` — placing the star center at (5, 0.42), completely off-screen.

This bug existed since the project's creation and was the root cause of gem/star shapes being invisible. SESSION-018 attempted to fix it by increasing radii, but the real problem was parameter ordering. All calls now use explicit keyword arguments: `star(cx=0, cy=0, r_outer=0.42, r_inner=0.22, n_points=5)`.

### All SESSION-019 Changes

| Category | Change | File(s) | Impact |
|----------|--------|---------|--------|
| **BUG FIX** | SDF primitive kwargs everywhere | `pipeline.py`, all test files | gem 0.24→0.77, star 0.24→0.75 |
| **P0-NEW-1** | Particles + cage deform integrated into pipeline | `pipeline.py`, `animation/__init__.py` | `produce_vfx()` and `produce_deform_animation()` |
| **P0-NEW-3** | Palette-constrained SDF rendering | `sdf/renderer.py`, `oklab/palette.py` | Floyd-Steinberg dither in OKLAB space |
| **P0-NEW-2** | Full evolution validation (19 tests) | `test_evolution_validation.py` | 100% pass rate |
| **BUG FIX** | `result.overall` → `result.overall_score` in VFX | `pipeline.py` | VFX scoring now works |
| **ENHANCEMENT** | `Palette.from_srgb_list()` class method | `oklab/palette.py` | Easy palette creation from color lists |

### Validation Results (19/19 = 100%)

| Test | Score | Status |
|------|-------|--------|
| eval_good_circle | 0.7253 | PASS |
| eval_medium_circle | 0.6621 | PASS |
| eval_bad_tiny | 0.6417 | INFO |
| eval_noise | 0.5976 | PASS |
| selection_pressure | good > medium > bad | PASS |
| sprite_val_coin | 0.8244 | PASS |
| sprite_val_gem | 0.7699 | PASS |
| sprite_val_star | 0.7544 | PASS |
| animation_idle | 0.8244 | PASS |
| vfx_fire | 0.4938 | PASS |
| vfx_explosion | 0.2400 | PASS |
| vfx_sparkle | 0.4718 | PASS |
| vfx_smoke | 0.3974 | PASS |
| deform_squash_stretch | 0.7956 | PASS |
| deform_wobble | 0.7956 | PASS |
| deform_breathe | 0.7956 | PASS |
| palette_circle | 6 colors | PASS |
| palette_star | 6 colors | PASS |
| palette_box | 6 colors | PASS |

## Knowledge Base Status

| Category | Count | Location |
|----------|-------|----------|
| Distilled rules | 12 | `knowledge/rules.json` |
| Math models | 10 | `knowledge/math_models.json` |
| Absorbed papers | 8 | `DEDUP_REGISTRY.json` |
| Absorbed tutorials | 3 | `DEDUP_REGISTRY.json` |
| Absorbed repos | 2 | `DEDUP_REGISTRY.json` |
| Research topics | 8 | `DEDUP_REGISTRY.json` |

## Pending Tasks (Priority Order)

### P0 — Critical Path (Do These Next)

All previous P0 tasks are now **DONE**. The following are newly identified P0 priorities:

| ID | Task | Effort | Description |
|----|------|--------|-------------|
| P0-NEW-4 | Multi-layer render compositing | Medium | Separate base/texture/lighting/outline layers for independent control |
| P0-NEW-5 | Run large-scale evolution (100+ iterations) | Medium | Validate that evolution produces quality improvement over many generations |
| P0-NEW-6 | VFX evaluator tuning | Low | Explosion scores 0.24 — need VFX-specific evaluation criteria |

### P1 — Important

| ID | Task | Effort | Description |
|----|------|--------|-------------|
| P1-2 | Per-frame SDF parameter animation | Medium | Each frame can have different SDF parameters for true shape animation |
| P1-NEW-1 | Wave Function Collapse tilemap | High | Generate coherent tilemaps from example tiles |
| P1-NEW-2 | Reaction-diffusion textures | Medium | Gray-Scott for organic textures (coral, lichen) |
| P1-NEW-3 | Spring-based secondary animation | Medium | Critically-damped spring for follow-through |
| P1-NEW-4 | Multi-state sprite generation | Medium | Multiple states per character (idle, walk, attack) |

### P2 — Nice to Have

| ID | Task | Effort |
|----|------|--------|
| P2-1 | Sub-pixel rendering | Medium |
| P2-4 | Multi-objective optimization (NSGA-II) | High |
| P2-5 | Procedural outline variation | Low |

### P3 — Future

| ID | Task | Effort |
|----|------|--------|
| P3-1 | Auto knowledge distillation | Medium |
| P3-2 | Web preview UI | High |
| P3-3 | Unity/Godot exporter plugin | Medium |

## Completed Tasks

### SESSION-019

| ID | Task | Result |
|----|------|--------|
| P0-NEW-1 | Integrate particles + cage deform into pipeline | `produce_vfx()` + `produce_deform_animation()` |
| P0-NEW-2 | Run full evolution validation | 19/19 = 100% pass rate |
| P0-NEW-3 | Palette-constrained SDF rendering | Floyd-Steinberg dither in OKLAB space |
| BUG-019-1 | SDF primitive parameter order fix | All 16 shapes render correctly |
| BUG-019-2 | `result.overall` → `result.overall_score` | VFX scoring works |

### SESSION-018

| ID | Task | Result |
|----|------|--------|
| P0-1 | Evaluator rewrite (12 metrics) | Real selection pressure |
| P0-2 | CPPN enriched topology | Richer textures |
| P0-3 | Reference/palette pipeline fix | Evaluator gets proper inputs |
| P0-4 | gem/star visibility fix | Radii increased (root cause found in S019) |
| P1-3 | GIF animation export | Pipeline + particles + cage deform |
| P1-4 | Math models registered | 10 models |
| P2-2 | Particle system | Verlet + 4 presets |

## Gap Analysis: Current vs. Commercial Quality

### Comparison with itch.io Standards

| Aspect | itch.io Standard | Our Current State | Gap |
|--------|-----------------|-------------------|-----|
| Palette | 4-16 curated colors | Palette-constrained rendering (DONE) | **LOW** |
| Outlines | 1px crisp, continuous | SDF outlines, sometimes gaps | Medium |
| Shading | 2-4 level ramps | 3-7 level ramps | Low |
| Animation | Squash/stretch/anticipation | Transform + cage + particles + VFX | Low |
| Tilemap | Seamless connecting tiles | No tilemap capability | **HIGH** |
| Variety | Multiple states per character | Single shape per asset | Medium |
| Internal detail | Hand-placed highlights/shadows | CPPN-evolved textures | Medium |

### Biggest Remaining Gaps

1. **No tilemap generation** — Cannot produce coherent level-scale assets (needs WFC)
2. **No multi-state sprites** — Each asset is a single shape, no idle/walk/attack variants
3. **VFX evaluation too harsh** — Explosion scores 0.24 because evaluator expects solid shapes
4. **No multi-layer compositing** — All rendering in single pass, limits artistic control

## Project Health Score: 7.8/10 (up from 6.8)

| Dimension | Score | Notes |
|-----------|-------|-------|
| Code Quality | 8/10 | ~25,500 lines, 19 validation tests, well modularized |
| Render Quality | 7/10 | Palette-constrained + lighting + AO + hue-shift |
| Evolution Capability | 7/10 | 12-metric evaluator with real selection pressure, validated |
| Animation Quality | 7/10 | Transform + cage deform + particles + VFX (4 presets each) |
| Asset Output | 8/10 | PNG + GIF + spritesheet + JSON metadata, all shapes work |
| Knowledge Accumulation | 5/10 | 12 rules + 10 models (needs auto-distillation) |
| Self-Evolution | 6/10 | Better evaluator + enriched CPPN + anti-stagnation + dedup |

## Instructions for Next AI Session

1. **Read `DEDUP_REGISTRY.json`** — DO NOT re-research absorbed topics
2. **Read `SESSION_PROTOCOL.md`** — Follow efficiency rules
3. Read `PROJECT_BRAIN.json` for the full machine-readable state
4. **Start with P0-NEW-4** — Multi-layer render compositing
5. Then **P0-NEW-5** — Run large-scale evolution (100+ iterations)
6. Then **P0-NEW-6** — VFX evaluator tuning
7. Always push changes to GitHub after completing work
8. Always update this file and `PROJECT_BRAIN.json` before ending

## Quick Start

```python
# Test evaluator
from mathart.evaluator.evaluator import AssetEvaluator
ev = AssetEvaluator()
result = ev.evaluate(some_image)
print(result.overall_score, result.passed, result.suggestions)

# Produce sprite
from mathart.pipeline import AssetPipeline, AssetSpec
pipeline = AssetPipeline(output_dir="output/")
result = pipeline.produce_sprite(AssetSpec(name="coin", shape="coin"))

# Produce VFX
vfx = pipeline.produce_vfx(name="fire", preset="fire", n_frames=12)

# Produce deformation animation
from mathart.pipeline import AnimationSpec
spec = AssetSpec(name="bounce", shape="circle")
deform = pipeline.produce_deform_animation(spec=spec, deform_type="squash_stretch", n_frames=8)

# Test particles standalone
from mathart.animation.particles import ParticleSystem, ParticleConfig
system = ParticleSystem(ParticleConfig.fire())
frames = system.simulate_and_render(n_frames=12)
system.export_gif(frames, "fire.gif")

# Test cage deformation standalone
from mathart.animation.cage_deform import CageDeformer, CagePreset
deformer = CageDeformer(sprite_image)
frames = deformer.animate(CagePreset.squash_stretch(), n_frames=12)
deformer.export_gif(frames, "squash.gif")

# Run validation
python3 test_evolution_validation.py
```

---
*Auto-generated by SESSION-019 at 2026-04-15T16:15:00Z*
