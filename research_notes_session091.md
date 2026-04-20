# SESSION-091 Research Notes — P1-AI-2E: Motion-Adaptive Keyframe Planning

## Research References Aligned

### 1. SparseCtrl (Guo et al., ECCV 2024)
- Non-uniform temporal conditioning: SparseCtrl allows injecting control signals at
  arbitrary frame indices, not just uniform intervals.
- `end_percent` parameter controls the strength of ControlNet guidance. Higher values
  (→1.0) enforce stronger adherence to the control signal.
- **Key insight for P1-AI-2E**: During high-nonlinearity motion segments (rapid
  acceleration, contact events), we MUST increase both keyframe density AND
  `end_percent` to suppress diffusion model divergence. During smooth locomotion,
  sparse keyframes with lower `end_percent` suffice.

### 2. Ubisoft Motion Matching (Clavet GDC 2016)
- Feature vector: root velocity, root angular velocity, foot positions, foot velocities.
- **Acceleration magnitude** = ‖v(t) - v(t-1)‖ / dt — measures abruptness of motion.
- **Angular velocity spikes** = |ω(t) - ω(t-1)| / dt — measures rotational jerk.
- **Contact events** = boolean transitions (foot_down→foot_up or vice versa) — these
  are phase boundaries that must be captured.
- These three signals map directly to UMR fields: `MotionRootTransform.velocity_x/y`,
  `angular_velocity`, and `MotionContactState.left_foot/right_foot`.

### 3. Guilty Gear Xrd (Motomura GDC 2015) — Hitstop/Contact Safe Points
- Hitstop frames (2-8 frames of freeze on contact) are the highest-tension moments.
- These MUST be force-captured as keyframes regardless of the adaptive algorithm.
- In UMR terms: any frame where `contact_tags` transitions AND `phase_state.is_cyclic=False`
  (transient phase like "hit") is a mandatory safe point.

## Implementation Architecture

### Nonlinearity Score Computation (NumPy Vectorized)

```
For each frame i in UMR clip:
  1. acceleration_magnitude[i] = sqrt(
       (vx[i] - vx[i-1])^2 + (vy[i] - vy[i-1])^2
     ) * fps
  2. angular_jerk[i] = |aw[i] - aw[i-1]| * fps
  3. contact_event[i] = 1.0 if any contact boolean changed, else 0.0
  4. nonlinearity_score[i] = w_acc * norm(acceleration_magnitude[i])
                            + w_ang * norm(angular_jerk[i])
                            + w_contact * contact_event[i]
```

Weights: w_acc=0.4, w_ang=0.3, w_contact=0.3 (configurable).
Normalization: per-clip min-max to [0, 1].

### Adaptive Keyframe Selection Algorithm

1. **Force-capture extrema**: All local maxima of `nonlinearity_score` above a
   threshold (default 0.7) are mandatory keyframes.
2. **Force-capture contact events**: All frames with `contact_event[i] > 0` are
   mandatory keyframes (Guilty Gear Xrd discipline).
3. **Force-capture first and last frame**: Boundary anchors.
4. **Fill gaps with min_gap/max_gap constraints**:
   - `min_gap` (default 2 frames): Prevent over-dense clustering at extrema.
     If two mandatory keyframes are closer than min_gap, keep only the higher-scored one.
   - `max_gap` (default 12 frames): Prevent starvation in smooth segments.
     If the gap between consecutive keyframes exceeds max_gap, insert intermediate
     keyframes at the highest-scored positions within the gap.
5. **Map to SparseCtrl end_percent**: Each keyframe's `end_percent` = 
   `base_end_percent + (1.0 - base_end_percent) * nonlinearity_score[frame_idx]`.
   High-score frames get end_percent → 1.0, low-score frames get base_end_percent.

### Anti-Pattern Guards

1. **Stale Cache Leak Trap**: Orchestrator `on_reload` callback must:
   - Clear `_result_cache[backend_name]`
   - Reset `_iteration_counters[backend_name]`
   - Test: simulate reload → assert old KEYFRAME_PLAN is gone

2. **Extrema Omission & Void Trap**: 
   - NEVER use `frame_idx % step == 0` static sampling
   - Test: assert no gap > max_gap, assert all contact events captured
   - Test: assert min_gap respected (no two keyframes within min_gap)

3. **Mid-Frame Reload Trap**:
   - `SafePointExecutionLock` wraps batch render entry
   - Hot-reload waits for lock release before applying
   - Test: simulate concurrent reload during batch → assert no AttributeError

### Three-Layer Evolution Bridge

`KeyframeEvolutionBridge`:
- Layer 1: Internal evolution — tune weights (w_acc, w_ang, w_contact),
  thresholds, min_gap/max_gap via Optuna-style parameter search.
- Layer 2: Knowledge distillation — persist winning parameter sets and
  motion complexity profiles as knowledge rules.
- Layer 3: Self-iterating test — validate keyframe plans against quality
  metrics (temporal coherence, coverage, no-void, no-cluster).

## Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `mathart/core/backend_types.py` | MODIFY | Add `MOTION_ADAPTIVE_KEYFRAME` enum |
| `mathart/core/artifact_schema.py` | MODIFY | Add `KEYFRAME_PLAN` family + required metadata |
| `mathart/core/motion_adaptive_keyframe_backend.py` | CREATE | Core backend + algorithm |
| `mathart/core/microkernel_orchestrator.py` | MODIFY | Hot-reload coordination |
| `mathart/core/safe_point_execution.py` | CREATE | Frame-boundary safe-point lock |
| `mathart/comfy_client/comfyui_ws_client.py` | MODIFY | Reload-resilient execution |
| `tests/test_motion_adaptive_keyframe.py` | CREATE | Full E2E test suite |
