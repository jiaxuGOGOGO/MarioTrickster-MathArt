# Research Notes: Four Biomechanics/Kinematics Directions

## 1. Zero Moment Point (ZMP) & Center of Mass (CoM)

### Core Mathematical Formulation

**ZMP Definition (MIT Underactuated Robotics, Russ Tedrake):**
The ZMP is the point on the ground where the total external wrench (force + torque) can be described by a single force vector with zero moment (torque).

**Equations of Motion:**
- m*x̄̈ = Σᵢ f_{i,x} (horizontal forces)
- m*z̄̈ = Σᵢ f_{i,z} - mg (vertical forces - gravity)
- I*θ̈ = Σᵢ [p_i × f_i]_y (rotational moment)

**ZMP-CoM relationship (key equation):**
z_zmp * m*ẍ - x_zmp * (m*z̈ + mg) = I*θ̈

**Simplified (flat terrain, z̈=0, θ̈=0):**
ẍ = g * (x - x_cop) / (z - z_cop)

**ZMP = CoP (Center of Pressure) for flat terrain:**
x_zmp = Σᵢ(x_i * f_{z,i}) / Σᵢ f_{z,i}

**Stability Criterion:**
- ZMP must lie within the support polygon (convex hull of foot contact points)
- If ZMP exits support polygon → character is falling/losing balance
- For animation: if computed ZMP is outside foot area → frame shows visual "weightlessness"

### Application to 2D Animation
- For each frame, compute approximate CoM from joint positions + mass distribution
- Compute ZMP from CoM acceleration (finite differences between frames)
- Check if ZMP falls within foot support area
- Use as a quality metric: frames with ZMP outside support = "unbalanced" frames
- Can be used to auto-correct poses by shifting CoM

## 2. Inverted Pendulum Model (IPM / LIPM)

### Core Mathematical Model (Kajita et al., 2001)

**3D Linear Inverted Pendulum Mode (3D-LIPM):**
The CoM is modeled as a point mass on a massless telescopic leg.

**Constraint:** CoM moves on a plane at height z_c (constant)

**Equations of motion (2D simplified):**
ẍ = (g / z_c) * (x - x_foot)

**Solution (hyperbolic functions):**
x(t) = x₀*cosh(ωt) + (ẋ₀/ω)*sinh(ωt)
ẋ(t) = x₀*ω*sinh(ωt) + ẋ₀*cosh(ωt)

where ω = sqrt(g / z_c)

**Key properties:**
- CoM trajectory is a hyperbolic curve between steps
- Sagittal (x-z) and lateral (y-z) motions decouple
- Walking = switching between inverted pendulums at each step

### Application to 2D Animation
- Model character's CoM as inverted pendulum during walk/run
- Compute natural CoM trajectory: slight rise at mid-stance, dip at double-support
- Use to generate realistic vertical bounce in walk cycle
- CoM height variation ≈ 2-4% of leg length during normal walking
- Lateral sway ≈ 1-2% of leg length

## 3. Foot Skating Cleanup Algorithm

### Core Algorithm (Kovar et al., SCA 2002)

**Problem:** Feet slide on ground during contact (skating artifact)

**Detection Phase:**
1. For each foot joint, compute height h(t) and velocity v(t)
2. Contact condition: h(t) ≤ h_threshold AND |v(t)| ≤ v_threshold
3. When both conditions met → foot should be planted (stationary)

**Cleanup Phase (velocity-based):**
When foot height → 0 (approaching ground):
1. Compute horizontal velocity: vx = dx/dt, vy = dy/dt  
2. If foot is in contact state: force vx = 0, vy = 0
3. Use IK to adjust leg joints to keep foot at locked position

**Mathematical formulation (calculus-based):**
- Let p(t) = foot position at time t
- Contact detection: h(t) ≤ ε AND |ḣ(t)| ≤ δ
- During contact: enforce dp/dt|_{xy} = 0 (zero horizontal velocity)
- Smooth transition: blend weight w(t) using Hermite interpolation
  w(t) = 3t² - 2t³ (smoothstep)
- Corrected position: p_corrected = p_contact + w(t) * (p_original - p_contact)

**PhysDiff approach (ICCV 2023):**
- Skating metric: average horizontal displacement of grounded feet between adjacent frames
- Penetration metric: foot below ground plane
- Float metric: foot suspiciously high when should be grounded

### Existing Implementation Status
- ContactDetector: height + velocity heuristic ✓
- ConstraintBlender: Hermite smoothstep ✓
- FootLockingConstraint: 2-bone analytical IK ✓
- **Missing:** Full velocity-based cleanup with calculus derivative, ZMP-aware contact scheduling

## 4. FABRIK (Forward And Backward Reaching Inverse Kinematics)

### Core Algorithm (Aristidou & Lasenby, 2011)

**Input:** Chain of n joints with bone lengths L₁...Lₙ₋₁, target position T, root position R

**Algorithm:**
```
repeat until convergence or max_iterations:
  // Forward pass (tip → root)
  joints[n-1] = T  // place end effector at target
  for i = n-2 down to 0:
    direction = normalize(joints[i] - joints[i+1])
    joints[i] = joints[i+1] + direction * L[i]
  
  // Backward pass (root → tip)  
  joints[0] = R  // fix root at original position
  for i = 0 to n-2:
    direction = normalize(joints[i+1] - joints[i])
    joints[i+1] = joints[i] + direction * L[i]
```

**Joint Constraints:**
After each pass, clamp relative angles between consecutive bones:
- relative_angle = angle(bone_i+1) - angle(bone_i)
- clamped = clamp(relative_angle, min_angle, max_angle)
- Recompute joint position from clamped angle

**Properties:**
- O(n) per iteration (linear in chain length)
- Typically converges in 3-10 iterations
- Preserves bone lengths exactly
- Handles unreachable targets gracefully (stretches toward target)

### Application to Procedural Gait
- Use FABRIK to solve leg IK: hip → knee → foot
- Foot target = planned contact point on ground
- Root = hip position (from CoM trajectory)
- Apply ROM constraints: knee backward-only, hip bounded
- Generate walk/run by scheduling foot targets along path

### Existing Implementation Status
- FABRIKSolver in physics.py ✓ (standalone, not wired to pipeline)
- 2-bone analytical IK in FootLockingConstraint ✓
- **Missing:** Full FABRIK integration into animation pipeline for procedural gait
