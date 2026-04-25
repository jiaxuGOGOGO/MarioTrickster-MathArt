# SESSION-199 Research Notes — Safe Model Mapping & Adaptive Variance Scheduling

## Overview

SESSION-199 addresses two compounding technical debts identified via the
SESSION-068 E2E test regression analysis:

1. **反臆想模型注入红线** — The fluid/physics ControlNet model mapping in
   `vfx_topology_hydrator.py` was inverted relative to the correct
   photometric-stereo and depth-topology theory.
2. **PID 自适应增益调度** — A static ControlNet strength constant cannot
   adapt to the per-frame signal energy of the conditioning image, leading
   to over- or under-conditioning on high/low-variance frames.

---

## 1. Safe Model Mapping (反臆想模型注入红线)

### Problem

Prior to SESSION-199, the constants were:

```python
FLUID_CONTROLNET_MODEL_DEFAULT  = "control_v11f1p_sd15_depth.pth"   # WRONG
PHYSICS_CONTROLNET_MODEL_DEFAULT = "control_v11p_sd15_normalbae.pth" # WRONG
```

The comments claimed:
- Fluid flowmap → depth model (because "2D displacement field similar to depth")
- Physics 3D → normalbae (because "surface deformation similar to normal maps")

### Root Cause Analysis

The mapping was based on surface-level analogy rather than the actual
**signal-space semantics** of each ControlNet model:

| Model | Signal Space | Correct Conditioning Input |
|---|---|---|
| `control_v11p_sd15_normalbae.pth` | Surface normal perturbation (XYZ encoded as RGB) | Directional flow fields, momentum vectors |
| `control_v11f1p_sd15_depth.pth` | Monocular depth (Z-axis displacement) | 3D rigid-body deformation, volumetric collapse |

### Correct Mapping (SESSION-199)

**Fluid flowmap → normalbae**

The fluid momentum field encodes directional surface flow as a 2D vector
field. Under photometric stereo theory (Woodham 1980), the Lambertian
shading gradient of a surface is proportional to the surface normal
perturbation. A fluid flowmap's per-pixel directional vectors are
semantically equivalent to surface normal deltas — both encode
**directional perturbation in the image plane**.

Reference: R.J. Woodham, "Photometric method for determining surface
orientation from multiple images," *Optical Engineering* 19(1), 1980.

**Physics 3D → depth**

The physics simulation output encodes 3D rigid-body deformation fields
(collision response, gravity, spring forces). These deformations manifest
as Z-axis displacement relative to the camera frustum — exactly the signal
space of a monocular depth map. The depth ControlNet model (`f1p` variant)
is specifically designed for flow-like depth estimation, making it the
correct choice for physics deformation conditioning.

```python
# SESSION-199 CORRECT MAPPING:
FLUID_CONTROLNET_MODEL_DEFAULT  = "control_v11p_sd15_normalbae.pth"  # fluid → normalbae
PHYSICS_CONTROLNET_MODEL_DEFAULT = "control_v11f1p_sd15_depth.pth"   # physics → depth
```

### Regression Safety

`test_session197_physics_bus_unification.py` does **not** assert on the
specific model name strings for the fluid/physics loader nodes — it only
imports the constants for use in fixture construction. The swap is therefore
safe with respect to existing tests.

The new `test_session199_adaptive_scheduling.py` adds explicit regression
guards (`test_fluid_model_default_is_normalbae`,
`test_physics_model_default_is_depth`, `test_model_defaults_are_not_swapped`)
to prevent future reversion.

---

## 2. Adaptive Variance-Based ControlNet Strength Scheduler

### Problem

A static ControlNet strength (e.g., `FLUID_CONTROLNET_STRENGTH_DEFAULT = 0.35`)
cannot adapt to the per-frame signal energy of the conditioning image:

- **Low-variance frames** (near-uniform color, flat motion): static strength
  over-conditions, injecting phantom structure into the generation.
- **High-variance frames** (complex motion, high-frequency detail): static
  strength under-conditions, failing to propagate the physics/fluid signal.

### Solution: PID-Inspired Adaptive Gain Scheduling

Inspired by PID (Proportional-Integral-Derivative) adaptive gain scheduling
(Åström & Hägglund 1995), the adaptive scheduler scales strength
proportionally to the normalised pixel variance of the conditioning frame:

```
normalised_variance = pixel_variance / 255.0
raw_strength = base_strength + variance_scale * normalised_variance
adaptive_strength = clamp(raw_strength, min_strength, max_strength)
```

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `base_strength` | 0.35 | Baseline strength at zero variance |
| `variance_scale` | 0.5 | Proportional gain coefficient |
| `min_strength` | 0.10 | Safety floor (prevents zero conditioning) |
| `max_strength` | 0.90 | Safety ceiling (prevents over-conditioning) |

**Implementation:** `compute_adaptive_controlnet_strength()` in
`mathart/core/vfx_topology_hydrator.py`.

### Design Decisions

1. **Normalisation by 255.0** — Uses uint8 pixel range as the normalisation
   denominator. This is consistent with PIL/NumPy image conventions and
   avoids dependency on the actual image statistics.

2. **Hard clamp** — The `[min_strength, max_strength]` clamp is a hard safety
   constraint, not a soft sigmoid. This ensures the scheduler never produces
   out-of-range values regardless of input.

3. **Pure function** — `compute_adaptive_controlnet_strength` has no side
   effects and no I/O. It can be called from any context (tests, pipeline,
   CLI) without setup.

4. **Default parameters match existing defaults** — `base_strength=0.35`
   matches `FLUID_CONTROLNET_STRENGTH_DEFAULT`, ensuring backward
   compatibility when called with default arguments.

### Reference

- K.J. Åström & T. Hägglund, *PID Controllers: Theory, Design, and Tuning*,
  2nd ed., ISA Press, 1995.

---

## 3. SESSION-068 E2E Test Fix

### Problem

`TestAntiFlickerRenderE2E` and `TestCrossBackendContract` used
`width=32, height=32` for `anti_flicker_render`. SESSION-198 introduced a
`render_dimensions_too_small` validation in `AntiFlickerRenderBackend.validate_config`
requiring minimum 64×64 dimensions.

### Fix

All `anti_flicker_render` invocations in `test_session068_e2e.py` updated
from `width=32, height=32` to `width=64, height=64` (11 occurrences).

`industrial_sprite` tests retain `width=32, height=32` — the industrial
sprite backend does not have the 64×64 minimum constraint.

---

## Files Changed

| File | Change |
|---|---|
| `mathart/core/vfx_topology_hydrator.py` | Swap FLUID/PHYSICS model defaults; add `compute_adaptive_controlnet_strength()`; update `__all__` |
| `tests/test_session068_e2e.py` | Update anti_flicker_render 32×32 → 64×64 (11 occurrences) |
| `tests/test_session199_adaptive_scheduling.py` | New: 16 unit tests for adaptive scheduler + model mapping regression guards |
| `docs/RESEARCH_NOTES_SESSION_199.md` | This file |
| `SESSION_HANDOFF.md` | Updated for SESSION-200 handoff |
| `PROJECT_BRAIN.json` | Updated session counter and feature registry |


---

## 4. Dead-Water Dynamic Pruning (死水动态剪枝)

### Problem

When a conditioning frame has near-zero pixel variance (e.g., a flat gray
image, a static noise pattern, or a corrupted all-black frame), injecting it
through a ControlNet node introduces **micro-noise pollution** into the latent
space. The ControlNet model amplifies this imperceptible noise into visible
artifacts in the generated output.

### Solution: Dead-Water Detection + DAG Surgery

Inspired by Houdini PDG's dead-end pruning pattern, SESSION-199 introduces a
two-stage dead-water handling mechanism:

**Stage 1: Detection (`should_prune_dead_water`)**

```python
DEAD_WATER_VARIANCE_THRESHOLD = 0.5

def should_prune_dead_water(pixel_variance, *, threshold=0.5) -> bool:
    # NaN / Inf / negative → True (prune for safety)
    return pixel_variance < threshold
```

The threshold of 0.5 was chosen empirically: a uint8 image with variance < 0.5
is effectively a single-color frame with sub-pixel noise — no meaningful
conditioning signal exists.

**Stage 2: DAG Surgery (`prune_controlnet_node_and_reseal_dag`)**

When dead water is detected, the corresponding ControlNet chain (VHS loader +
ControlNet loader + Apply node) must be removed from the ComfyUI workflow DAG
without breaking the conditioning flow. The algorithm:

1. Read the pruned node's `positive` and `negative` input references (upstream)
2. Find all downstream nodes that reference the pruned node
3. Rewire downstream references to point directly to the upstream node
4. Remove the pruned node and its orphaned feeder nodes
5. Verify KSampler conditioning wires remain connected

This is the **反拓扑断裂红线** (Anti-Topology-Fracture Red Line): after any
DAG surgery, the conditioning chain must remain fully connected from CLIP
encoder through to KSampler, with no dangling references.

### Integration

Dead-water pruning is integrated into `hydrate_vfx_topology()` between the
SESSION-198 rasterization bridge and the ControlNet injection phases:

```
SESSION-198 Rasterizer → SESSION-199 Variance Sampling → Dead-Water Gate
    ├── Below threshold → Skip injection, report "dead_water_pruned"
    └── Above threshold → Adaptive strength → Inject ControlNet chain
```

---

## 5. 反数学崩溃红线 (Anti-Math-Crash Red Line)

### Problem

In production pipelines, conditioning images may be corrupted, truncated, or
contain NaN/Inf values due to upstream failures (e.g., failed rasterization,
disk I/O errors, GPU memory corruption). If these values propagate into the
adaptive scheduler, they can cause:

- `NaN` propagation through all downstream computations
- `Inf` values causing overflow in strength calculations
- `ZeroDivisionError` if the normalisation denominator were variable
- Negative variance values from corrupted image statistics

### Solution: Belt-and-Suspenders Defense

Every numeric input to `compute_adaptive_controlnet_strength()` and
`should_prune_dead_water()` is sanitised through a multi-layer defense:

1. **Type coercion**: `float()` conversion with `try/except` fallback
2. **NaN/Inf detection**: `math.isnan()` and `math.isinf()` checks
3. **Negative rejection**: negative variance → immediate safe floor
4. **Constant divisor**: normalisation by 255.0 (never zero)
5. **Final paranoid guard**: result checked for NaN/Inf before return

The guarantee: **the return value is ALWAYS a finite float in
`[min_strength, max_strength]`**, regardless of any conceivable input.

### Test Coverage

5 dedicated test cases in `test_session199_adaptive_scheduling.py`:
- `test_nan_variance_returns_min_strength`
- `test_inf_variance_returns_min_strength`
- `test_negative_inf_variance_returns_min_strength`
- `test_negative_variance_returns_min_strength`
- `test_result_is_never_nan` (sweeps 7 edge cases)

---

## 6. Updated Files Changed Summary

| File | Change |
|---|---|
| `mathart/core/vfx_topology_hydrator.py` | Swap FLUID/PHYSICS model defaults; add `compute_adaptive_controlnet_strength()` with NaN/Inf defense; add `should_prune_dead_water()`; add `prune_controlnet_node_and_reseal_dag()`; integrate dead-water pruning + adaptive scheduling into `hydrate_vfx_topology()`; extend `emit_vfx_hydration_banner()` with industrial baking gateway; update `__all__` |
| `tests/test_session068_e2e.py` | Update anti_flicker_render 32×32 → 64×64 (11 occurrences) |
| `tests/test_session199_adaptive_scheduling.py` | New: 44 unit tests (adaptive scheduler + NaN defense + dead-water pruning + DAG resealing + model mapping regression guards) |
| `docs/RESEARCH_NOTES_SESSION_199.md` | This file |
| `docs/USER_GUIDE.md` | Appended SESSION-199 DaC documentation section |
| `SESSION_HANDOFF.md` | Rewritten with foolproof acceptance checklist |
| `PROJECT_BRAIN.json` | Updated session counter and feature registry |
