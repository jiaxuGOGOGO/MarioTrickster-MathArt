# P1-PHASE-33B Research Notes: Terrain-Adaptive Phase Modulation

## 1. PFNN (Holden et al., SIGGRAPH 2017) — Phase Progression Modulation

**Core insight**: Phase is NOT advanced at constant rate. The network weights are
computed as a cyclic function of phase, and the phase itself is updated based on
terrain geometry. The terrain heightmap is sampled at multiple points along the
character's future trajectory to extract:

- **Height samples**: SDF values at N points ahead of the character
- **Gradient (slope)**: ∇SDF at each sample point → surface normal → slope angle
- **Phase update rule**: Δφ = f(velocity, slope, surface_type) where f is a
  learned/designed nonlinear mapping

**Key mathematical formulation for our implementation**:
```
slope_angle = atan2(∇SDF_y, ∇SDF_x)  # terrain slope at each trajectory point
phase_velocity_scale = g(slope_angle, surface_viscosity)
Δφ_new = Δφ_base × phase_velocity_scale
```

The phase velocity should be modulated continuously, not discretely.

## 2. Biomechanics of Incline Walking

**Empirical findings from literature**:

### Uphill (positive slope):
- Stride length DECREASES (shorter steps)
- Step frequency INCREASES (faster cadence)
- Metabolic cost increases ~linearly above 15° grade
- Stance phase proportion increases
- Net effect: circumference ↓, phase_velocity ↑

### Downhill (negative slope):
- Stride length INCREASES (longer steps for braking)
- Step frequency DECREASES (slower cadence)
- Eccentric muscle work increases (braking forces)
- Stance phase proportion changes for stability
- Net effect: circumference ↑, phase_velocity ↓

### Mathematical model (continuous mapping):
```python
# Slope angle θ in radians, positive = uphill
# stride_scale: multiplier on StrideWheel.circumference
# freq_scale: multiplier on phase_velocity (Δφ per frame)

# Uphill: shorter stride, higher frequency
# Downhill: longer stride, lower frequency
# Flat: no change (scale = 1.0)

stride_scale(θ) = 1.0 - α_s * sin(θ)   # α_s ≈ 0.3-0.5
freq_scale(θ)  = 1.0 + α_f * sin(θ)    # α_f ≈ 0.2-0.4

# With smoothstep clamping to avoid extreme values
stride_scale = clamp(stride_scale, 0.4, 1.6)
freq_scale   = clamp(freq_scale, 0.5, 2.0)
```

### Surface viscosity model:
```python
# Different surface types have different friction/viscosity
# viscosity ∈ [0, 1] where 0 = ice, 1 = deep mud
# Higher viscosity → shorter stride, slower phase

surface_stride_scale = 1.0 - β_s * viscosity   # β_s ≈ 0.3
surface_freq_scale   = 1.0 - β_f * viscosity   # β_f ≈ 0.15
```

## 3. Motion Matching Trajectory Forecasting (Clavet, GDC 2016)

**Core insight**: Don't just sample terrain at the current foot position.
Sample at N future trajectory points predicted from:
- Current root position
- Current root velocity (direction + magnitude)
- Spring-damper trajectory model

**Trajectory prediction**:
```python
# Given root position p, velocity v, and dt
# Predict N future positions at intervals dt_sample
future_positions = np.zeros((N, 2))
for i in range(N):
    t = (i + 1) * dt_sample
    future_positions[i] = p + v * t  # simple linear prediction
```

**CRITICAL**: This must be VECTORIZED (no Python for loop in hot path):
```python
# Vectorized trajectory prediction
t_samples = np.arange(1, N+1) * dt_sample  # (N,)
future_xy = root_pos[np.newaxis, :] + root_vel[np.newaxis, :] * t_samples[:, np.newaxis]  # (N, 2)

# Batch SDF query for all N points
sdf_values = terrain.query_batch(future_xy[:, 0], future_xy[:, 1])  # (N,)

# Batch gradient computation via central differences
eps = 1e-4
grad_x = (terrain.query_batch(future_xy[:, 0] + eps, future_xy[:, 1]) -
           terrain.query_batch(future_xy[:, 0] - eps, future_xy[:, 1])) / (2 * eps)
grad_y = (terrain.query_batch(future_xy[:, 0], future_xy[:, 1] + eps) -
           terrain.query_batch(future_xy[:, 0], future_xy[:, 1] - eps)) / (2 * eps)

# Slope angles from gradients
slope_angles = np.arctan2(grad_y, grad_x) - np.pi/2  # relative to vertical
```

## 4. Anti-Phase-Pop: C1 Continuous Smoothing

**CRITICAL**: Never modify phase absolute value. Only modify phase velocity (dφ/dt).
Use exponential moving average (EMA) or critically damped spring:

```python
# EMA low-pass filter for phase velocity
smoothed_phase_vel += (target_phase_vel - smoothed_phase_vel) * (1 - exp(-dt / tau))
# tau = smoothing time constant ≈ 0.1-0.2s

# Or critically damped spring:
# ω = 2π / response_time
# x'' + 2ωx' + ω²x = ω²target
```

## 5. Architecture Integration Points

### TerrainSDF.query_batch() — already exists!
### TerrainSDF.gradient() — exists but scalar only, need batch version
### StrideWheel.circumference — modulate this for stride length
### StrideWheel.advance(distance_delta) — modulate distance_delta for phase velocity
### UnifiedGaitBlender.sample_continuous_gait() — the hot path to intercept

### New components needed:
1. `TrajectoryTerrainForecaster` — vectorized future terrain sampling
2. `TerrainPhaseModulator` — maps terrain features to stride/phase modulation
3. `TerrainGaitConfig` — strongly typed config dataclass
4. `TransientPhaseMetadata` — terrain metadata for UMR frames
