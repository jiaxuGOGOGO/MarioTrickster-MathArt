# SESSION-034 Research Synthesis: Industrial Motion Matching & Rendering Pipeline

> Research protocol: Deep Reading Protocol on three GDC north-star sources.
> Triggered by: User request for industrial-grade animation evaluation and rendering.

## Research Sources

| # | Source | Author / Studio | Event | Core Contribution |
|---|--------|----------------|-------|-------------------|
| 1 | *Motion Matching and The Road to Next-Gen Animation* | Simon Clavet, Ubisoft | GDC 2016 | Feature-vector motion matching replaces state machines |
| 2 | *Art Design Deep Dive: Dead Cells* | Sébastien Bénard, Motion Twin | GDC 2018 | 3D-to-2D pixel art pipeline with no anti-aliasing |
| 3 | *Guilty Gear Xrd's Art Style: The X Factor* | Junya C Motomura, Arc System Works | GDC 2015 | Limited animation / hold frames / stepped keys |

## 1. Motion Matching (Clavet GDC 2016)

### Key Findings

The core innovation is replacing hand-authored animation state machines with a **feature-vector database search**. Every frame in the motion database is tagged with a multi-dimensional feature vector containing:

- **Current local pose** (joint positions/rotations relative to root)
- **Current joint velocities** (angular and linear)
- **Future predicted trajectory** (2-3 future root positions at 0.2s, 0.4s, 0.6s)
- **Foot contact labels** (binary left/right ground contact flags)

At runtime, the system computes the same feature vector for the *desired* state and finds the closest match in the database using weighted Euclidean distance. Per-column normalization (mean=0, std=1) ensures all feature dimensions contribute equally.

### Landing in MarioTrickster-MathArt

The `MotionMatchingEvaluator` in `motion_matching_evaluator.py` implements a **59-dimensional feature schema** adapted for 2D pixel art:

| Feature Group | Dimensions | Weight | Source |
|--------------|-----------|--------|--------|
| Pose (12 joints × 1 angle) | 12 | 1.0 | Clavet: local pose |
| Velocity (12 joints) | 12 | 0.8 | Clavet: joint velocities |
| Trajectory (3 future × 4 dims) | 12 | 1.2 | Clavet: future trajectory |
| Contact (left/right foot × 3) | 6 | 1.5 | Clavet: contact labels |
| Phase (sin/cos × 3 channels) | 6 | 1.0 | DeepPhase extension |
| Silhouette (5 spread metrics) | 5 | 0.6 | Dead Cells extension |
| Trajectory velocity (3 × 2) | 6 | 0.7 | Clavet: trajectory velocity |
| **Total** | **59** | — | — |

**Critical correction applied:** Layer 3 `evaluate_physics_fitness()` now uses feature-vector matching instead of the legacy joint-angle tolerance scoring. The `motion_match_score` in the overall fitness formula is now computed by `MotionMatchingEvaluator.compute_layer3_fitness()`.

## 2. Dead Cells 3D-to-2D Pipeline (GDC 2018)

### Key Findings

Motion Twin's pipeline achieves hand-drawn pixel art quality from 3D skeletal animation through:

1. **No anti-aliasing downsampling** — Hard binary threshold (pixel on/off), no smoothstep, no bilinear filtering. This preserves crisp pixel edges at 32×32.
2. **Normal map volume enhancement** — Pseudo-normal maps computed from SDF gradients provide directional lighting cues even at low resolution.
3. **Extreme silhouette priority** — Animations are designed for silhouette readability first. Poses may be anatomically impossible but must produce clear, exaggerated outlines.
4. **Intentional physical impossibility** — 3D rigs allow extreme stretching and even joint dislocation to achieve the desired 2D silhouette.

### Landing in MarioTrickster-MathArt

The `industrial_renderer.py` module implements:

- **Hard SDF threshold rendering** — `dist < 0` binary test, no smoothstep, no anti-aliasing
- **Pseudo-normal map** — `_compute_pseudo_normal()` derives nx/ny/nz from SDF gradient via finite differences
- **Hard cel shading** — `_cel_shade_hard()` with 2-band shading (lit/shadow), no dithering
- **OKLAB color space** — Warm highlight shift / cool shadow shift for perceptually uniform color manipulation
- **Outline boost on impact** — `effective_outline_iterations` increases during impact/contact phases
- **Volume-preserving squash/stretch** — `_transform_coords_with_squash()` applies scale while preserving pixel area

## 3. Guilty Gear Xrd Hold Frames (GDC 2015)

### Key Findings

Arc System Works achieves 2D anime aesthetic from 3D models through:

1. **Limited animation (有限動画)** — Not all frames are unique; key poses are held for 2-3 frames
2. **Stepped keys** — No interpolation between key poses; frames snap from one pose to the next
3. **Extreme squash & stretch** — Impact frames use 0.75× Y scale (squash), apex frames use 1.18× Y scale (stretch)
4. **Phase-aware hold timing** — Contact, impact, apex, and landing phases each have different hold durations

### Landing in MarioTrickster-MathArt

The `GuiltyGearFrameScheduler` in `industrial_renderer.py` implements:

| Phase | Hold Duration | Squash Y | Stretch Y | Interpolation |
|-------|-------------|----------|-----------|---------------|
| CONTACT | 2 frames | 0.88 | 1.0 | step |
| IMPACT | 3 frames | 0.75 | 1.0 | step |
| APEX | 2 frames | 1.0 | 1.18 | step |
| LANDING | 2 frames | 0.85 | 1.0 | step |
| TRANSITION | 1 frame | 1.0 | 1.0 | linear |

The `render_character_sheet_industrial()` function integrates the frame scheduler to produce sprite sheets with correct hold frame timing at 12fps.

## Integration with Three-Layer Evolution

### Layer 1 (Inner Loop) — Unchanged
Generate → Evaluate → Optimize → Repeat

### Layer 2 (Outer Loop) — 3 New Distillation Rules
- **Rule 8:** Silhouette quality (Dead Cells GDC 2018)
- **Rule 9:** Contact consistency (Motion Matching GDC 2016)
- **Rule 10:** Hold frame effectiveness (Guilty Gear Xrd GDC 2015)

### Layer 3 (Self-Iteration) — Upgraded
- `evaluate_physics_fitness()` now includes 3 new industrial metrics: `contact_consistency`, `silhouette_quality`, `skating_penalty`
- Overall fitness formula upgraded from 6 to 9 weighted components
- `PhysicsTestBattery` upgraded from 8 to 10 tests
- Strategy records now include `industrial_metrics` for cross-session tracking

## References

[1]: https://ribosome-rbx.github.io/files/motion_matching.pdf "Motion Matching and The Road to Next-Gen Animation (Clavet, GDC 2016)"
[2]: https://www.gamedeveloper.com/production/art-design-deep-dive-using-a-3d-pipeline-for-2d-animation-in-i-dead-cells-i- "Dead Cells Art Design Deep Dive (GDC 2018)"
[3]: https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf "Guilty Gear Xrd's Art Style (Motomura, GDC 2015)"
[4]: https://docs.o3de.org/blog/posts/blog-motionmatching/ "Motion Matching in Open 3D Engine"
[5]: https://github.com/Broxxar/PixelArtPipeline "Dead Cells Shader Pipeline (Unity recreation)"
