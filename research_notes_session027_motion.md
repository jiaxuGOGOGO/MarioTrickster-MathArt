# Research Notes: Motion Physics Deep Research (SESSION-027 Extension)

> **Date**: 2026-04-16
> **Scope**: 8-dimension parallel research on math/physics-driven motion simulation
> **Trigger**: User clarified core vision — "Math guarantees 'moves right', AI guarantees 'looks beautiful'"

## Core Vision Restatement

The project's fundamental purpose is NOT to generate beautiful pixels directly. It is to build a **mathematical brain** that produces physically correct, cognitively natural motion data. This motion data then serves as the deterministic foundation for downstream AI visual rendering (ComfyUI, Wan2.2, etc.).

The reason: Pure AI diffusion models (like Wan2.2) generate motion that often violates physics and human visual expectations. By grounding motion in math/physics first, we ensure correctness that AI alone cannot achieve.

## Research Dimensions and Key Findings

### Dimension 1: Verlet Integration Physics (P0)
- **Core**: Particle-constraint system where position is derived from current + previous position + acceleration
- **Formula**: `pos_next = pos_current + (pos_current - pos_previous) + acceleration * dt^2`
- **Application**: Build character skeleton as particles connected by distance constraints
- **Key Reference**: Pikuma blog on Verlet integration, desophos/ragdoll on GitHub

### Dimension 2: Mass-Spring Secondary Motion (P0)
- **Core**: Hooke's Law `F = -k * (l - l0)` + Damping `F_d = -d * v`
- **Application**: Hair, clothing, accessories jiggle naturally via spring physics
- **Advanced**: Squash-and-stretch via covariance matrix + time-warping (Kwon & Lee)
- **Key Reference**: jessicaione101/3d-mass-springs, GDC "Juicing Your Cameras With Math"

### Dimension 3: IK & Procedural Gait (P0)
- **Core**: FABRIK algorithm — forward-backward reaching, only needs vector math
- **Application**: Keyframeless walk/run/jump by moving IK targets along elliptical paths
- **Key Reference**: sean.fun FABRIK tutorial, Unity procedural walk cycle series

### Dimension 4: Easing Functions & Motion Curves (P0)
- **Core**: Robert Penner equations, cubic Bezier curves
- **Why natural**: Matches human expectation of inertia, friction, gravity
- **Key Reference**: easings.net, robertpenner.com/easing

### Dimension 5: Particle VFX Physics (P1)
- **Core**: Emitter + Forces (gravity, wind) + Collision + Lifecycle
- **Application**: Dust trails, impact effects, fire/smoke driven by character physics state
- **Key Reference**: SideFX Procedural Thinking, Particle Life sandbox

### Dimension 6: Math Motion + AI Visual Pipeline (P0)
- **Core**: ControlNet (OpenPose/Depth) as bridge between math motion and AI rendering
- **Workflow**: Math skeleton → Pose sequence → ControlNet condition → ComfyUI/AnimateDiff → Final pixels
- **Requirement**: GPU (≥12GB VRAM) for diffusion model inference
- **Key Reference**: ControlNet GitHub, AnimateDiff

### Dimension 7: Human Visual Perception of Motion (P0)
- **Core**: Biological motion perception — phase relationships between joints matter more than individual trajectories
- **Key Insight**: "Apprehension Principle" — motion must be easily perceived, not just physically accurate
- **Application**: Add cognitive constraints to GA fitness functions (penalize unclear key poses, reward anticipation)
- **Key Reference**: Tversky "Animation: can it facilitate?", Vanderbilt biological motion research

### Dimension 8: Open Source Animation Frameworks (P1)
- **Finding**: No mature Python procedural animation library exists
- **Best options**: SpookyGhost (Lua-based, closest to our philosophy), Spine/DragonBones runtimes (reference architecture)
- **Conclusion**: Must build custom Python motion engine, borrowing concepts from these frameworks

## Architecture Decision

The project should build a layered motion engine:

```
Layer 4: Cognitive Constraints (fitness functions for GA)
Layer 3: Procedural Controllers (gait generator, action state machine)
Layer 2: Physics Simulation (Verlet engine, spring systems, particle systems)
Layer 1: Math Foundation (easing functions, Bezier curves, vector math)
Layer 0: Existing SDF Character Generation (body structure, genotype)
```

Output format: Bone position sequences + particle position sequences → can be rendered as pixels (current) or fed to ControlNet (future GPU path).
