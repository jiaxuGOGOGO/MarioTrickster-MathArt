# DeepMimic Research Notes (SESSION-080)

## Core Reward Function (Peng et al., SIGGRAPH 2018)

### Total Reward
```
r_t = ω_I * r_t^I + ω_G * r_t^G
```
- `r_t^I` = imitation objective
- `r_t^G` = task-specific objective

### Imitation Reward Decomposition
```
r_t^I = w_p * r_t^p + w_v * r_t^v + w_e * r_t^e + w_c * r_t^c
w_p = 0.65, w_v = 0.1, w_e = 0.15, w_c = 0.1
```

### Sub-Rewards

1. **Pose Reward** (`r_t^p`): Match joint orientations via quaternion difference
   ```
   r_t^p = exp(-2 * Σ_j ||q̂_t^j ⊖ q_t^j||²)
   ```

2. **Velocity Reward** (`r_t^v`): Match joint angular velocities
   ```
   r_t^v = exp(-0.1 * Σ_j ||q̇̂_t^j - q̇_t^j||²)
   ```

3. **End-Effector Reward** (`r_t^e`): Match hand/foot world positions
   ```
   r_t^e = exp(-40 * Σ_e ||p̂_t^e - p_t^e||²)
   ```

4. **Center-of-Mass Reward** (`r_t^c`): Match center-of-mass position
   ```
   r_t^c = exp(-10 * ||p̂_t^c - p_t^c||²)
   ```

### Key Design Principles
- All sub-rewards use **exponential kernel** → always in [0,1], smooth gradients
- Weights are **orthogonal**: pose dominates (0.65), end-effectors second (0.15), velocity and CoM minor
- **Phase variable** φ ∈ [0,1] included in state for cyclic motions
- **Reference State Initialization (RSI)**: At episode start, sample random phase from reference motion
- **Early Termination**: Episode ends if character deviates too far from reference

### State Representation
- Relative positions of each link w.r.t. root (pelvis)
- Rotations as quaternions
- Linear and angular velocities
- All in character's local coordinate frame
- Phase variable φ

## Isaac Gym Vectorized Environment Discipline
- Reference motion data must be **pre-baked as contiguous tensors**
- O(1) lookup in step() — no dict traversal or I/O
- All environments share the same reference buffer, indexed by env_id and phase

## NVIDIA Isaac Gym Vectorized Environment Discipline (Detailed)

From Makoviychuk et al. (2021) "Isaac Gym: High Performance GPU-Based Physics Simulation for Robot Learning":

1. **Tensor API**: Isaac Gym provides GPU-resident tensor API for environment state and actions
2. **Contiguous Memory**: All observation/action buffers are contiguous GPU tensors shared across all parallel envs
3. **O(1) Indexing**: Reference data accessed by `env_idx * stride + offset` — no dict lookups in hot path
4. **Pre-bake Pattern**: Reference motions loaded once at init, stored as `(num_envs, max_steps, state_dim)` tensor
5. **Phase Indexing**: Each env tracks its own phase/time index into the reference buffer
6. **Zero-Copy**: Observations, rewards, dones computed in-place on GPU tensors

### Key Implementation Pattern:
```python
# At __init__:
self.ref_motion_buf = np.zeros((num_envs, max_frames, state_dim), dtype=np.float32)
# Pre-fill from motion clips

# At step():
current_ref = self.ref_motion_buf[env_ids, self.phase_indices]  # O(1) fancy indexing
reward = compute_imitation_reward(current_state, current_ref)  # vectorized
```

## EA Frostbite Data-Oriented Design (Detailed)

From EA/DICE GDC presentations on Frostbite engine:

1. **Struct of Arrays (SoA)**: Components stored in contiguous typed arrays, not as objects
   - Positions: `float[N][3]`, Velocities: `float[N][3]`, Phases: `float[N]`
   - NOT: `Object[N]` with mixed fields

2. **Data-Logic Separation**: 
   - Data providers (reference motion) are pure data stores
   - Control solvers (RL policy) are pure compute functions
   - No inheritance hierarchy — composition via typed channels

3. **Cache Coherence**: Hot-path data packed contiguously for L1/L2 cache efficiency
   - Joint angles for ALL frames packed together
   - Velocities for ALL frames packed together
   - Phase values for ALL frames packed together

4. **Schema-Driven Contracts**: Data interchange uses explicit schemas
   - Producer declares output schema (joint_channel_schema)
   - Consumer validates schema at bind time, not at runtime
   - Mismatch caught at initialization, not in hot loop

### Application to UMR→RL:
- UMR dict structure → flatten to SoA buffers at init
- RL step() reads only from pre-flattened contiguous arrays
- Schema validation happens once during pre-bake, never in step()
