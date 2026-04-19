# Research Notes for P1-DISTILL-3

## 1. NVIDIA Isaac Gym / PhysX Domain Randomization
- **Core concept**: Automated parameter search across massive physics parameter combinations
- **Key technique**: Domain randomization randomizes physics sim parameters (compliance, damping, substeps, friction, mass) to find robust configurations
- **NaN stability**: Isaac Gym forums confirm NaN explosions from unstable dt/substep/stiffness combos; solution is systematic parameter sweep with validity checks
- **Parameter distillation**: Best configs extracted from grid/Bayesian search, not hand-tuned
- **Application to our project**: Sweep compliance, damping, substeps combos; filter NaN/divergence; rank by physics stability score

## 2. Google Vizier / Multi-Objective Pareto Optimization
- **Core concept**: Black-box multi-objective optimization finding Pareto frontier
- **Key technique**: Simultaneously optimize competing objectives (quality vs cost)
- **Hardware-aware**: Penalize configurations that are computationally expensive (wall_time_ms)
- **Pareto frontier**: Set of non-dominated solutions where no objective can improve without worsening another
- **Application to our project**: Joint optimization of physics accuracy (low penetration, no sliding) AND computational cost (wall_time_ms, ccd_sweep_count)

## 3. Ubisoft Motion Matching (Clavet GDC 2016)
- **Core concept**: Data-driven animation selection replacing state machines
- **Foot sliding penalty**: Key quality metric - measure foot bone velocity when foot should be planted
- **Blend time parameterization**: Transition blend duration affects both quality and responsiveness
- **Phase alignment**: Sync foot contact phases before blending to minimize artifacts
- **Application to our project**: Blend time and phase weights must be inversely derived from foot-sliding penalties

## 4. EA Frostbite Data-Driven Configuration
- **Core concept**: Microkernel architecture with data-driven JSON asset files
- **Key technique**: Configuration as data, not code; loaded at startup, overrides defaults
- **CompiledParameterSpace pattern**: Pre-compiled parameter spaces loaded from JSON assets
- **Application to our project**: Distilled knowledge → JSON asset file → preloaded at startup → overrides hardcoded defaults

## 5. XPBD (Macklin & Müller 2016)
- **Compliance α̃ = α/Δt²**: Timestep-independent stiffness control
- **Substeps**: "Small Steps" paper (2019) shows substep method achieves stiffness with less numerical damping
- **Damping β**: Block diagonal constraint damping coefficients
- **Key insight**: compliance, damping, substeps are the THREE critical knobs for physics stability
- **Per-frame simulation time**: 4ms for 100 XPBD iterations at 60fps target

## Synthesis: Design Principles for PhysicsGaitDistillationBackend
1. **Parameter space**: Grid/combinatorial search over {compliance, damping, substeps, blend_time, phase_weight}
2. **Fitness function**: Multi-objective combining physics_error + gait_sliding + wall_time_penalty + ccd_cost
3. **Pareto selection**: Extract non-dominated solutions from search results
4. **Knowledge output**: JSON asset file with ranked parameter configurations
5. **Closed loop**: CompiledParameterSpace loads JSON at startup, overrides defaults
