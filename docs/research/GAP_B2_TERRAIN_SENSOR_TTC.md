# Gap B2: Scene-Aware Distance Sensor — SDF Terrain + TTC

**Session**: SESSION-048
**Status**: 🟢 Resolved
**Gap ID**: B2
**Category**: Animation / Physics / Scene Awareness

## Deep Research Summary

### Core Problem

The existing `fall_distance_phase()` assumes flat ground at a constant `ground_height`. This breaks on any non-trivial terrain: slopes, steps, platforms, or procedurally generated SDF landscapes. The character's landing animation either arrives too early (feet clip through elevated terrain) or too late (character hovers above a depression before the landing pose triggers).

### Research Foundations

#### 1. Simon Clavet — Motion Matching (GDC 2016, Ubisoft / For Honor)

Simon Clavet, the inventor of Motion Matching, demonstrated at GDC 2016 that the key to responsive character animation is **trajectory prediction**. In For Honor, the character's centre-of-mass is driven by a spring-damper simulation, and the animation system selects mocap clips whose trajectory best matches the predicted path. The entity is clamped to ≤15 cm around the simulated point.

**Key insight adopted**: The character should *predict* terrain contact before it happens, not react after the fact. By querying the SDF terrain ahead of time, we can anticipate the landing moment and synchronise the animation phase accordingly.

> "The goal is not to be as responsive as possible, the goal is to be as predictable as possible." — Simon Clavet

#### 2. UE5 Distance Matching (Laurent Delayen / Paragon / Epic Games)

Distance Matching in Unreal Engine 5 replaces time-based animation playback with **distance-driven playback**. Each animation clip has a baked Distance Curve that maps normalised playback position to accumulated root-motion distance. For landing:

1. A line trace (ray cast) measures distance-to-ground D.
2. The fall animation is advanced to the frame whose Distance Curve value matches D.
3. The feet touch down at exactly the right pose, regardless of fall height.

**Key insight adopted**: We replace the line trace with an SDF terrain query (`Terrain_SDF(foot_x, foot_y)`) and bind the transient phase directly to the distance, ensuring the animation reaches phase 1.0 at the exact moment of contact.

#### 3. Time-to-Contact (TTC) — Perceptual Science

TTC is a well-studied concept in perceptual psychology and robotics:

- **Simple TTC**: `TTC = D / |v|` where D is distance and v is approach velocity.
- **Gravity-corrected TTC**: Solves the quadratic `D = v₀·t + ½g·t²` for free-fall, giving `TTC = (-v₀ + √(v₀² + 2gD)) / g`.

**Key insight adopted**: By computing TTC from the SDF distance and current velocity, we can bind the transient phase to *time remaining until contact* rather than *distance remaining*. This naturally handles varying fall speeds and gravity.

#### 4. Environment-aware Motion Matching (Pontón et al., SIGGRAPH 2025)

This recent paper introduces environment features (2D ellipse body proxies) into the Motion Matching cost function. The system dynamically adapts full-body pose and trajectory to navigate obstacles and other agents.

**Key insight adopted**: The concept of environment features as *penalisation factors* in the matching cost. We adapt this to our SDF terrain: the terrain surface normal at the landing point influences the pose (lean into slopes).

#### 5. Falling and Landing Motion Control (Ha et al., SIGGRAPH Asia 2012)

This paper decomposes falling into an **airborne phase** (optimise moment of inertia) and a **landing phase** (distribute impact). The landing phase has three stages: impact, rolling, and getting-up.

**Key insight adopted**: Our TTC-driven phase naturally creates a two-phase structure: the airborne stretch phase (TTC > 0.5s) and the brace/landing phase (TTC < 0.5s), with the brace signal intensifying as TTC approaches zero.

## Implementation Architecture

### Module: `mathart/animation/terrain_sensor.py`

| Component | Purpose |
|---|---|
| `TerrainSDF` | Composable SDF terrain (flat, slope, step, sine, platforms) |
| `TerrainRaySensor` | Sphere-traced ray casting for distance queries |
| `TTCPredictor` | TTC computation with gravity-aware quadratic formula |
| `scene_aware_distance_phase()` | Drop-in upgrade for `fall_distance_phase()` |
| `scene_aware_fall_pose()` | TTC-driven pose with slope compensation |
| `scene_aware_fall_frame()` | UMR-native frame generator |
| `evaluate_terrain_sensor_accuracy()` | Diagnostics for evolution bridge |

### Module: `mathart/evolution/terrain_sensor_bridge.py`

| Component | Purpose |
|---|---|
| `TerrainSensorEvolutionBridge` | Three-layer evolution bridge |
| Layer 1: `evaluate_terrain_sensor()` | Accuracy + TTC convergence gates |
| Layer 2: `distill_terrain_sensor_knowledge()` | Knowledge rule extraction |
| Layer 3: `compute_terrain_sensor_fitness_bonus()` | Fitness bonus/penalty |

### Key Formulas

**SDF Terrain Query**:
```
D = Terrain_SDF(foot_x, foot_y)
```

**Gravity-Corrected TTC**:
```
TTC = (-|v₀| + √(v₀² + 2gD)) / g
```

**Phase Binding**:
```
phase = 1.0 - (TTC_current / TTC_reference)
```

**Brace Signal**:
```
brace = clamp01(1.0 - TTC / 0.3)  # activates in last 0.3s
```

**Landing Preparation**:
```
prep = ease_in_out(clamp01(1.0 - TTC / 0.5))  # smooth ramp from 0.5s
```

## Terrain Factory Functions

| Function | Description |
|---|---|
| `create_flat_terrain(y)` | Horizontal ground at height y |
| `create_slope_terrain(...)` | Linear ramp between two points |
| `create_step_terrain(x, h)` | Step up at position x with height h |
| `create_sine_terrain(A, f)` | Sinusoidal wavy ground |
| `create_platform_terrain(...)` | Multiple box platforms |

## Evolution Bridge Quality Gates

| Metric | Threshold | Meaning |
|---|---|---|
| `mean_distance_error` | ≤ 0.05 | SDF query accuracy |
| `mean_phase_at_contact` | ≥ 0.95 | Phase reaches ~1.0 at landing |
| `phase_monotonic_rate` | ≥ 0.90 | Phase never decreases during fall |
| `ttc_decreasing_rate` | ≥ 0.90 | TTC consistently decreases |

## References

1. Clavet, S. (2016). "Motion Matching and The Road to Next-Gen Animation." GDC 2016.
2. Delayen, L. (2016). "Bringing a Hero from Paragon to Life with UE4." nucl.ai 2016.
3. Pontón, J.L. et al. (2025). "Environment-aware Motion Matching." ACM TOG / SIGGRAPH 2025.
4. Ha, S., Ye, Y., Liu, C.K. (2012). "Falling and Landing Motion Control for Character Animation." SIGGRAPH Asia 2012.
5. Epic Games. "Distance Matching in Unreal Engine." UE5 Documentation.
6. Lugtigheid, A.J., Welchman, A.E. (2011). "Evaluating methods to measure time-to-contact." Vision Research.
