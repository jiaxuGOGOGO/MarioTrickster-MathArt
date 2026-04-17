# SESSION-061 Research Notes: Phase 3 Motion Cognitive Dimensionality Reduction & 2D IK Closed Loop

## Research Protocol Activated
- **Session**: SESSION-061
- **Base commit**: `87af39a7e4ab68516725ac2f92b4670bdb5a137a` (SESSION-060)
- **Focus**: 运动认知降维与2D IK闭环（解决57%运动缺口）

## Core Academic References

### 1. Sebastian Starke (Meta Research Scientist, Eurographics 2025 Young Researcher Award)

**Mode-Adaptive Neural Networks for Quadruped Motion Control (SIGGRAPH 2018)**
- Authors: He Zhang+, Sebastian Starke+, Taku Komura, Jun Saito (+Joint First Authors)
- Citations: 421+
- Core Innovation: **Gating Network** dynamically blends expert weights into the motion prediction network
- Key Architecture:
  - Motion Prediction Network: computes character state given previous state + user controls
  - Gating Network: selects and blends expert weights specialized in particular movements
  - No phase labels needed — learns from unstructured mocap data end-to-end
- Quadruped Gaits Covered: walk, pace, trot, canter, jump, sit, turn, idle
- **Critical for our project**: Asymmetric phase mixing for quadruped — the gating network handles non-periodic/periodic action transitions without manual labeling

**Neural State Machine for Character-Scene Interactions (SIGGRAPH Asia 2019)**
- Authors: Sebastian Starke+, He Zhang+, Taku Komura, Jun Saito
- Citations: 406+
- Core Innovation: Data-driven framework for goal-driven actions with precise scene interactions
- Key Features:
  - Handles periodic AND aperiodic movements
  - Data augmentation: randomly switches 3D geometry while maintaining motion context
  - Dual inference: egocentric + goal-centric control scheme
  - Single model handles: locomotion, sitting, carrying, opening doors, avoiding obstacles

**DeepPhase: Periodic Autoencoders for Learning Motion Phase Manifolds (SIGGRAPH 2022)**
- Authors: Sebastian Starke, Ian Mason, Taku Komura
- Citations: 217+
- Core Innovation: **Periodic Autoencoder** learns multi-dimensional phase space from full-body motion
- Key Architecture:
  - Decomposes movements into multiple latent channels capturing non-linear periodicity
  - Each channel captures different body segment periodicity
  - Produces manifold where feature distances = better similarity measure than original motion space
  - Unsupervised learning from unstructured motion data
- Applications: locomotion skills, style-based movements, dance, football dribbling, motion query

**Local Motion Phases for Learning Multi-Contact Character Movements (SIGGRAPH 2020)**
- Authors: Sebastian Starke, Yiwei Zhao, Taku Komura, Kazi Zaman
- Core Innovation: Local motion phases for multi-contact character movements
- Eliminates manual phase labeling for complex multi-contact scenarios

### 2. Daniel Holden (Industry Pioneer)

**Phase-Functioned Neural Networks for Character Control (SIGGRAPH 2017)**
- Authors: Daniel Holden, Taku Komura, Jun Saito
- Citations: 790+ (祖师爷级)
- Core Innovation: **Phase-Functioned Neural Network (PFNN)**
  - Network weights computed via cyclic function using phase as input
  - Input: user controls + previous character state + scene geometry
  - Output: high quality motions achieving desired control
- Terrain Adaptation:
  - Trained on locomotion data fitted into virtual environments
  - Automatically adapts to: rough terrain, large rocks, obstacles, low ceilings
  - Technique for fitting terrains from virtual environments to captured motion data
- Performance: milliseconds execution, megabytes memory (trained on gigabytes of data)
- **Critical for our project**: The terrain heightmap input and phase-driven weight cycling is the foundation for our 2D terrain adaptation

### 3. Spine JSON Export Format (2D Skeleton Standard)

Key structures for our orthographic projection export:
- **Bones**: name, parent, length, rotation, x, y, scaleX, scaleY
- **Slots**: name, bone, attachment, color
- **IK Constraints**: name, bones (1-2), target, mix (FK/IK blend), bendPositive, softness
- **Animations**: bone timelines (rotate, translate, scale), slot timelines, IK constraint timelines
- Draw order timeline for depth sorting (our Z-depth → sorting order mapping)

## Implementation Strategy for MarioTrickster-MathArt

### A. Orthographic Projection Pipeline (3D NSM → 2D)
1. Take 3D NSM bone data (from `nsm_gait.py`)
2. Apply orthographic projection: keep X/Y displacement, keep Z-axis rotation
3. Convert Z-depth to sorting order (foreground/background layer)
4. Export to Spine JSON format OR Unity 2D Animation format

### B. Terrain-Adaptive 2D IK (FABRIK)
1. Use `Physics2D.Raycast` equivalent to get terrain height ahead
2. Feed terrain data to Python gait planner OR
3. Use 2D FABRIK to force ankle attachment to collision body in real-time
4. Integrate with existing `terrain_sensor.py` SDF terrain system

### C. Animation 12 Principles Quantification
1. Squash & Stretch: volume preservation ratio metric
2. Anticipation: pre-action displacement measurement
3. Follow-through: post-action overshoot decay metric
4. Arcs: trajectory curvature smoothness score
5. Timing: frame spacing variance analysis
6. Exaggeration: amplitude scaling factor
7. Secondary Action: phase offset correlation metric

### D. Three-Layer Evolution Integration
- Layer 1: Evaluate projection quality, IK accuracy, terrain conformity
- Layer 2: Distill knowledge from Starke/Holden papers into persistent rules
- Layer 3: Auto-tune projection parameters, IK weights, terrain sampling rates
