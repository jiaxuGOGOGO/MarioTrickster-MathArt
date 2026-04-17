# SESSION-055 Research Notes: Headless Automation & Asset Factory

## 1. Property-Based Graph Fuzzing (David R. MacIver — Hypothesis)

### Core Concepts from Official Documentation

**RuleBasedStateMachine** is Hypothesis's stateful testing API. Key features:
- **Rules**: Decorated methods that define actions; can take strategies and Bundles as arguments
- **Bundles**: Named collections of generated values that flow between rules (data dependencies)
- **Invariants**: Assertions checked after every rule execution (perfect for NaN/penetration checks)
- **Preconditions**: Filters that prevent rules from firing when state doesn't support them
- **Initialize**: Special rules guaranteed to run exactly once before normal rules

### Application to MarioTrickster-MathArt

The existing `state_machine_graph.py` already has:
- `RuntimeStateGraph.from_clip_names()` — builds a directed graph from clip names
- `canonical_edge_walk()` — deterministic coverage walk
- `random_walk()` — seeded random exploration
- `RuntimeStateMachineHarness` — executes walks through real MotionMatchingRuntime

**Gap**: The existing `test_state_machine_graph_fuzz.py` already uses `RuleBasedStateMachine` for basic transition fuzzing. What's missing:
1. **Feeding graph-generated sequences into `headless_e2e_ci.py`** for full pipeline validation
2. **NaN/penetration monitoring** during XPBD solver execution
3. **Extreme input sequence generation** (e.g., jump frame 2 + fall damage + forced attack)
4. **Integration with Optuna** for automated parameter tuning based on fuzz results

### Implementation Strategy

Create `mathart/headless_graph_fuzz_ci.py`:
- Generate thousands of transition sequences via Hypothesis stateful + graph model
- Execute each through RuntimeStateMachineHarness
- Monitor for NaN in XPBD solver outputs
- Monitor for penetration/tunneling violations
- Feed surviving sequences into headless E2E pipeline
- Report coverage metrics and failure modes

## 2. NR-IQA for Genetic Evolution (Multi-Modal Visual Fitness)

### Core Metrics

1. **Laplacian Variance** (sharpness/noise detection):
   - `cv2.Laplacian(img, cv2.CV_64F).var()`
   - High variance = sharp edges (good for normal maps)
   - Very high variance = high-frequency noise (bad)
   - Sweet spot: penalize both too-low (blurry) and too-high (noisy)

2. **SSIM (Structural Similarity Index)** (Wang et al. 2004):
   - Compares luminance, contrast, and structure between frames
   - Range [0, 1], higher = more similar
   - For frame-to-frame consistency: SSIM should be high between adjacent frames
   - For geometric deformation penalty: 1 - SSIM measures inter-frame change

3. **Combined Fitness Function**:
   ```
   fitness = w1 * physics_score 
           + w2 * laplacian_penalty(normal_map)
           + w3 * ssim_temporal_consistency(frame_i, frame_i+1)
           + w4 * depth_map_quality
   ```

### Application to Existing Layer 3 (Optuna)

The existing evolution loop uses Optuna for parameter tuning. The upgrade:
- Current: Only physics score in objective function
- New: Multi-modal objective combining physics + visual quality
- Normal map quality via Laplacian variance (penalize noise)
- Depth map smoothness via gradient magnitude
- Frame-to-frame SSIM for temporal consistency
- Thickness/roughness channel dynamic range

### Implementation Strategy

Create `mathart/quality/visual_fitness.py`:
- `compute_laplacian_sharpness(image)` — Laplacian variance with sweet-spot penalty
- `compute_frame_ssim(frame_a, frame_b)` — SSIM between consecutive frames
- `compute_normal_map_quality(normal_map)` — Laplacian + gradient coherence
- `compute_depth_map_quality(depth_map)` — Smoothness + range metrics
- `compute_visual_fitness(frames, aux_maps)` — Combined multi-modal score
- Integration point: `evolution_loop.py` objective function upgrade

## 3. Asset Factory (Commercial Tileset Benchmark)

### Strategy

The Asset Factory combines:
1. **Graph fuzzing** to discover all valid animation state paths
2. **NR-IQA scoring** to ensure visual quality of generated assets
3. **Automated batch generation** of commercial-grade asset packs
4. **Self-validation** through the three-layer evolution loop

### Implementation

Create `mathart/evolution/asset_factory_bridge.py`:
- Layer 1: Generate benchmark asset suite (characters, tiles, VFX)
- Layer 2: Score each asset with multi-modal visual fitness
- Layer 3: Persist quality trends, auto-reject below threshold
- Integration with headless CI for automated regression

## References

[1] D. R. MacIver, Z. Hatfield-Dodds, "Hypothesis: A new approach to property-based testing," JOSS, 2019
[2] Hypothesis Stateful Testing Docs: https://hypothesis.readthedocs.io/en/latest/stateful.html
[3] Z. Wang et al., "Image quality assessment: From error visibility to structural similarity," IEEE TIP, 2004
[4] BRISQUE NR-IQA: https://live.ece.utexas.edu/publications/2012/TIP%20BRISQUE.pdf
[5] Laplacian Variance for blur detection: OpenCV standard practice
[6] Optuna: https://optuna.org/
