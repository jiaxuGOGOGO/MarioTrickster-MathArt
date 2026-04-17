# SESSION-057 Research Notes: P2 Cross-Dimensional Mass Production

## Research Protocol: Precision Parallel Research

### Topic 1: Parametric Morphology via Smooth CSG (Inigo Quilez)

**Core Reference**: Inigo Quilez — https://iquilezles.org/articles/smin/

#### Smooth Minimum (smin) Family — IQ's Normalized Formulations

IQ defines 8 smooth-minimum variants, all normalized so parameter `k` maps directly to blending thickness in distance units:

1. **Quadratic Polynomial** (most commonly used, C1 continuous):
   ```
   h = max(k - abs(a-b), 0) / k
   smin = min(a,b) - h*h*k*(1/4)
   ```
   - k premultiplied by 4.0 for normalization
   - Compact support: blending only happens within |a-b| < k

2. **Cubic Polynomial** (C2 continuous, smoother transitions):
   ```
   h = max(k - abs(a-b), 0) / k
   smin = min(a,b) - h*h*h*k*(1/6)
   ```
   - k premultiplied by 6.0

3. **Exponential** (infinite support, true smooth):
   ```
   r = exp2(-a/k) + exp2(-b/k)
   smin = -k * log2(r)
   ```

4. **Root** (C∞ continuous):
   ```
   x = b - a
   smin = 0.5 * (a + b - sqrt(x*x + k*k))
   ```

#### Key Insights for Character Morphology

- **Mix Factor**: smin returns both blended distance AND a blend factor [0,1] for material mixing. This is critical for mixing body part colors/textures at blend boundaries.
- **Normalization**: k parameter = exact bounding box expansion needed. Essential for collision detection and BVH acceleration.
- **DD Family**: Most variants belong to a generalized family `smin(a,b,k) = min(a,b) - g(0) * K((b-a)/k)` where K is a kernel function.

#### 2D SDF Primitives for Body Parts (from IQ's distfunctions2d)

| Primitive | Parameters | Use Case |
|-----------|-----------|----------|
| Circle | r (radius) | Head, joints, eyes |
| Rounded Box | b (half-size), r (4 corner radii) | Torso, shields |
| Capsule | r1, r2 (end radii), h (height) | Arms, legs, tentacles |
| Ellipse | a, b (semi-axes) | Organic body parts |
| Trapezoid | r1, r2 (top/bottom width), h | Torso variations |
| Vesica | d (distance), r (radius) | Leaf/wing shapes |
| Moon | d, ra, rb | Crescent/horn shapes |
| Egg | ra, rb | Organic body cores |
| Heart | (parametric) | Decorative elements |

#### Application to Parametric Monster Generation

The key insight: **Don't hand-draw 20+ character part libraries**. Instead:
1. Define each body part as a parametric SDF primitive (circle, capsule, rounded box, etc.)
2. Use `smin` with varying `k` to blend adjacent parts (arm→torso, head→neck)
3. Encode all parameters as genes in the genotype
4. When Layer 3 evolution randomly throws parameters, smin automatically generates "stretched muscle-like" smooth adhesion between parts
5. Run overnight → hundreds of topologically distinct, non-interpenetrating monster base libraries

### Topic 2: Constraint-Aware WFC (Maxim Gumin + Oskar Stålberg)

**Core References**:
- Maxim Gumin — https://github.com/mxgmn/WaveFunctionCollapse
- Oskar Stålberg — EPC2018 "WFC in Bad North", Townscaper
- Lee et al. — "Precomputing Player Movement for Reachability Constraints" (2020)

#### WFC Algorithm Core (Gumin)

1. **Observe**: Find cell with lowest Shannon entropy
2. **Collapse**: Select tile weighted by learned frequency distribution
3. **Propagate**: Remove incompatible options from neighbors via arc consistency

WFC supports **constraints** natively — the algorithm is essentially constraint propagation with a saved stationary distribution.

#### Oskar Stålberg's Contributions

- Combined WFC with **marching cubes on irregular grids** (Townscaper)
- Used WFC for **Bad North** island generation with structural constraints
- Key insight: WFC is not just tile matching — it's a **constraint solver** that can incorporate arbitrary domain constraints

#### Constraint-Aware WFC for Platformer Levels

The critical missing piece in our current WFC: **reachability validation during collapse**.

**Approach: TTC-Veto during Collapse Phase**

1. During `_collapse()`, before finalizing a tile choice, compute whether the resulting configuration is physically reachable
2. Use the existing `TTCPredictor` from terrain_sensor.py to validate jump feasibility
3. If a cliff-to-cliff tile combination exceeds maximum jump integral, **veto** that combination during collapse
4. This guarantees 100% playability mathematically

**Jump Integral Validation**:
```
max_jump_distance = v_x * t_air
max_jump_height = v_y^2 / (2 * g)
t_air = 2 * v_y / g

For a gap between platforms:
- horizontal_gap <= max_jump_distance
- vertical_gap <= max_jump_height
```

**Integration with Existing TTC**:
- `TerrainSDF` already supports `compose_smooth_union` for terrain composition
- `TTCPredictor.compute_ttc()` already computes time-to-contact
- `scene_aware_distance_phase()` already binds phase to TTC
- We reverse-connect these into WFC's propagation phase

### Topic 3: Three-Layer Evolution Integration

Both subsystems (Morphology + WFC) must integrate into the existing three-layer architecture:

**Layer 1 (Internal Evolution)**:
- Morphology: Evaluate visual diversity, non-interpenetration, silhouette distinctiveness
- WFC: Evaluate playability (spawn→goal reachability), structural validity, difficulty metrics

**Layer 2 (External Knowledge Distillation)**:
- Morphology: Distill rules about which smin k-values produce good organic blends
- WFC: Distill rules about which tile combinations produce interesting but playable levels

**Layer 3 (Self-Iteration)**:
- Morphology: Auto-tune blend parameters, evolve new body templates
- WFC: Auto-tune constraint thresholds, evolve tile frequency weights

### References

[1] Inigo Quilez, "Smooth Minimum," https://iquilezles.org/articles/smin/
[2] Inigo Quilez, "2D Distance Functions," https://iquilezles.org/articles/distfunctions2d/
[3] Inigo Quilez, "3D Distance Functions," https://iquilezles.org/articles/distfunctions/
[4] Maxim Gumin, "WaveFunctionCollapse," https://github.com/mxgmn/WaveFunctionCollapse
[5] Oskar Stålberg, "WFC in Bad North," EPC2018
[6] Lee et al., "Precomputing Player Movement for Reachability Constraints," 2020
[7] Babin et al., "Leveraging RL and WFC for Improved Procedural Level Generation," 2021
