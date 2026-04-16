# SESSION-033 Research Synthesis: Phase-Driven Animation Control

> This document summarizes the research conducted in SESSION-033 and maps findings to code implementations.

## Research Sources

### 1. PFNN — Phase-Functioned Neural Networks for Character Control
- **Authors:** Daniel Holden, Taku Komura, Jun Saito
- **Venue:** SIGGRAPH 2017
- **Key Insight:** The phase variable p ∈ [0, 2π) is a cyclic scalar that indexes the gait cycle. Left foot contact occurs at p=0, right foot contact at p=π. Network weights are generated as a function of phase via cubic Catmull-Rom interpolation over four control points spaced at 0, π/2, π, 3π/2.
- **Code Landing:** `PhaseVariable`, `_catmull_rom()`, `_catmull_rom_array()`, `PhaseInterpolator`

### 2. DeepPhase — Periodic Autoencoders for Learning Motion Phase Manifolds
- **Authors:** Sebastian Starke, Ian Mason, Taku Komura
- **Venue:** SIGGRAPH 2022
- **Key Insight:** Motion can be decomposed into multiple periodic channels, each with amplitude A, frequency F, phase shift S, and offset B. FFT extracts these parameters from arbitrary signals. The 2D phase representation (cos θ, sin θ) avoids discontinuities at 0/2π boundaries.
- **Code Landing:** `PhaseChannel`, `extract_phase_parameters()`, `create_phase_channel_from_signal()`, `WALK_CHANNELS`, `RUN_CHANNELS`

### 3. The Animator's Survival Kit (Expanded Edition)
- **Author:** Richard Williams
- **Publisher:** Faber and Faber, 2009
- **Key Insight:** Walk and run cycles are defined by four canonical key poses: Contact, Down, Passing, Up. The pelvis height trajectory follows a characteristic pattern: neutral at Contact, lowest at Down, rising through Passing, highest at Up. Arms are widest at Down position, not Contact. Run cycles include a flight phase where both feet leave the ground.
- **Code Landing:** `WALK_KEY_POSES`, `RUN_KEY_POSES`, `KeyPose`, pelvis height interpolation

## Research-to-Code Mapping

| Research Concept | Source | Code Implementation | Verified |
|-----------------|--------|---------------------|----------|
| Phase variable p ∈ [0, 1) | PFNN | `PhaseVariable` class | Yes |
| Left contact at p=0, right at p=0.5 | PFNN | `PhaseVariable.left_contact/right_contact` | Yes |
| Catmull-Rom spline interpolation | PFNN | `_catmull_rom()`, `PhaseInterpolator` | Yes |
| Speed-modulated phase advancement | PFNN | `PhaseVariable.advance(dt, speed, sps)` | Yes |
| Periodic channel decomposition | DeepPhase | `PhaseChannel` with A/F/S/B | Yes |
| FFT parameter extraction | DeepPhase | `extract_phase_parameters()` | Yes |
| 2D phase representation | DeepPhase | `PhaseChannel.evaluate_2d()` | Yes |
| Multi-channel overlay | DeepPhase | `WALK_CHANNELS`, `RUN_CHANNELS` | Yes |
| Contact key pose | Animator's SK | `WALK_KEY_POSES[0]`, `RUN_KEY_POSES[0]` | Yes |
| Down key pose (lowest pelvis) | Animator's SK | `WALK_KEY_POSES[1]` with pelvis_height=-0.025 | Yes |
| Passing key pose | Animator's SK | `WALK_KEY_POSES[2]` | Yes |
| Up key pose (highest pelvis) | Animator's SK | `WALK_KEY_POSES[3]` with pelvis_height=0.020 | Yes |
| Flight phase (run only) | Animator's SK | `RUN_KEY_POSES` contains "flight" | Yes |
| Arms oppose legs | Animator's SK | Verified in tests | Yes |
| Knees never bend forward | Animator's SK | Verified in tests | Yes |
| Left-right mirroring | All sources | `PhaseInterpolator` with mirror pairs | Yes |
| C1 boundary continuity | PFNN + fix | Virtual mirrored-Contact anchor at p=0.5 | Yes |

## Key Design Decisions

1. **Phase range [0, 1) instead of [0, 2π):** Simplifies key-pose indexing and is equivalent via `phase_2pi` property.
2. **Half-cycle key poses with mirroring:** Defines poses for one step only; the second step is automatically mirrored. This halves the data and guarantees symmetry.
3. **Virtual mirrored-Contact anchor:** Appending a mirrored Contact pose at p=0.5 ensures the Catmull-Rom spline has a proper target at the half-cycle boundary, achieving C1 continuity.
4. **Channel overlay architecture:** DeepPhase channels are additive on top of key-pose interpolation, allowing secondary motion (torso bob, head stabilization) without modifying the primary gait structure.
5. **Backward compatibility:** `run_animation()` signature unchanged; legacy preserved as `run_animation_legacy()`.
