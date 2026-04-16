# SESSION-035 Research Synthesis: Compliant Physics & Adversarial Motion Priors

> **Core Pain Point:** The `BiomechanicsProjector` was too aggressive — its spring-damper system forcefully distorted slightly-off source motions into unrecognizable poses. Physics must step back half a step and become **compliant guidance**, not rigid correction.

## Research Sources

### 1. DeepMimic: Example-Guided Deep Reinforcement Learning of Physics-Based Character Skills (SIGGRAPH 2018)

**Lead Researcher:** Xue Bin (Jason) Peng (NVIDIA / UC Berkeley)

**Key Technical Insights:**

The core of DeepMimic's tracking is a **PD (Proportional-Derivative) controller** operating at each joint. The policy network outputs target joint angles, and the PD controller computes torques to track those targets:

> τ = k_p × (θ_target − θ_current) − k_d × ω_current

The reward function decomposes into four weighted terms:
- **Pose reward** r_p: exp(-2 × Σ ||q̂_j ⊖ q_j||²) — quaternion difference per joint
- **Velocity reward** r_v: exp(-0.1 × Σ ||θ̇_j − θ̇_ref||²)
- **End-effector reward** r_e: exp(-40 × Σ ||p_j − p_ref||²)
- **Center-of-mass reward** r_c: exp(-10 × ||p_com − p_com_ref||²)

Combined: r_t = w_p × r_p + w_v × r_v + w_e × r_e + w_c × r_c (default: 0.65, 0.1, 0.15, 0.1)

**Critical Insight for Our Project:** The PD controller **does not create or modify motion** — it merely applies virtual muscle torques to **compliantly track** the reference pose. The physics layer should be a **follower**, not a **leader**. This is the exact opposite of our previous spring-damper approach which tried to enforce physical constraints at the expense of motion quality.

### 2. AMP: Adversarial Motion Priors for Stylized Physics-Based Character Control (SIGGRAPH 2021)

**Lead Researcher:** Xue Bin (Jason) Peng

**Key Technical Insights:**

AMP replaces hand-crafted reward functions with a **learned discriminator** that distinguishes real motion from generated motion. The discriminator is trained on state-transition pairs (s_t, s_{t+1}) from a reference motion dataset.

**LSGAN formulation** (more stable than vanilla GAN):
- D_loss = E_real[(D(s,s') - 1)²] + E_fake[D(s,s')²]
- G_reward = 1 - (1 - D(s,s'))² (clipped to [0,1])

**Gradient penalty** (critical for training stability):
- GP = E_real[||∇_s D(s,s')||² - 1]²

**Replay buffer** prevents catastrophic forgetting by mixing old generated samples with new ones during discriminator training.

**Critical Insight for Our Project:** Instead of hand-writing `coverage_score` and other heuristic metrics, Layer 3 should ask a discriminator: "Does this motion look like real motion?" The discriminator learns what "good motion" looks like from reference data, eliminating the need for manual scoring rules.

### 3. VPoser: Variational Human Pose Prior (CVPR 2019)

**Lead Researchers:** Nima Ghorbani, Naureen Mahmood, Michael J. Black (MPI for Intelligent Systems)

**Key Technical Insights:**

VPoser learns a **low-dimensional latent space** of anatomically valid human poses using a VAE (Variational Autoencoder). The latent space has the property that:
- Any point sampled from N(0, I) in latent space decodes to a valid human pose
- Poses can be interpolated smoothly in latent space
- Anatomically impossible poses map to high-energy regions far from the origin

**Naturalness scoring:** The Mahalanobis distance from the origin in latent space serves as a naturalness metric. Poses close to the origin are common/natural; poses far from the origin are rare/unnatural.

**Critical Insight for Our Project:** Mutations in the evolution system should happen **in latent space**, not in joint-angle space. This guarantees that every mutated pose is anatomically legal. The `latent_mutate()` function adds noise in latent space and decodes back, ensuring the result is always on the human pose manifold.

## Implementation Landing

| Research Concept | Implementation | File | Lines |
|-----------------|----------------|------|-------|
| DeepMimic PD tracking | `_simulate_compliant_pd()` | physics_projector.py | ~90 |
| Compliance mode parameter | `compliance_mode`, `compliance_alpha` | physics_projector.py | ~20 |
| Per-joint compliance map | `_JOINT_COMPLIANCE` dict | physics_projector.py | ~15 |
| AMP LSGAN discriminator | `MotionDiscriminator.train_step()` | skill_embeddings.py | ~60 |
| AMP gradient penalty | `MotionDiscriminator.gradient_penalty()` | skill_embeddings.py | ~25 |
| AMP replay buffer | `add_to_replay()`, `_replay_real/fake` | skill_embeddings.py | ~20 |
| AMP sequence scoring | `style_reward_sequence()` | skill_embeddings.py | ~30 |
| VPoser latent encode/decode | `encode_to_latent()`, `decode_from_latent()` | human_math.py | ~70 |
| VPoser latent mutation | `latent_mutate()` | human_math.py | ~40 |
| VPoser naturalness scoring | `naturalness_score()` | human_math.py | ~25 |
| VPoser latent interpolation | `latent_interpolate()` | human_math.py | ~15 |
| Layer 3 AMP integration | AMP reward in evolution loop | evolution_layer3.py | ~35 |
| Layer 3 VPoser integration | VPoser scoring in evolution loop | evolution_layer3.py | ~25 |
| Convergence bridge | `converged_params` → `LAYER3_CONVERGENCE_BRIDGE.json` | evolution_layer3.py + engine.py | ~50 |
| Pipeline bridge consumption | Auto-load convergence params | pipeline.py | ~30 |

## Gap Audit Results

| Gap | Status | Resolution |
|-----|--------|------------|
| #1: Physics/biomechanics not default | ✅ Already fixed | `enable_physics=True`, `enable_biomechanics=True` are defaults since SESSION-029 |
| #2: Phase-driven not enforced for all actions | ⚠️ Partial | run/walk delegated; jump/fall/hit remain legacy; cli.py bypasses trunk → **P1-PHASE-35A** |
| #3: Evaluation→export gap | ✅ Fixed in SESSION-035 | `LAYER3_CONVERGENCE_BRIDGE.json` bridges Layer 3 → pipeline |
| #4: End-to-end reproducibility | ⚠️ Needs task | No zero-to-export trunk validation → **P1-BENCH-35A** |
