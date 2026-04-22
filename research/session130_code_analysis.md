# SESSION-130 Code Analysis: True Motion Guide Architecture

## Current Data Flow (Broken)

```
_node_prepare_character → genotype → skeleton → attachment_mesh
_node_unified_motion → motion_clip_json (bone transforms per frame)
_node_compose_mesh → composed_mesh.npz (STATIC single-pose mesh)
_node_orthographic_render → SINGLE static render (albedo/normal/depth/mask)
_image_sequence_from_render_manifest → micro-jitter on static image (SESSION-129)
_node_ai_render → anti_flicker_render backend → ComfyUI
```

**Root Cause**: The orthographic render produces ONE static render from the composed mesh.
The factory then applies micro-jitter to fake a sequence. This is still fundamentally
a single-image replication pattern — the micro-jitter (±0.5px, ±0.5% brightness) is
far below the threshold needed for temporal attention to detect real motion.

## Required Data Flow (True Motion)

```
_node_prepare_character → genotype → skeleton + style
_node_unified_motion → motion_clip_json (bone transforms per frame)
_node_motion2d_export → clip_2d (per-frame bone transforms)
_node_compose_mesh → composed_mesh.npz (base mesh for deformation)
_node_orthographic_render → SINGLE static render (kept for archive/preview)
NEW: _build_true_motion_guide_sequence → per-frame renders using:
  - skeleton + animation_func from clip_2d bone transforms
  - render_character_maps_industrial OR bake_auxiliary_maps pattern
  - Produces REAL per-frame albedo/normal/depth/mask with geometric variation
_node_ai_render → anti_flicker_render with TRUE motion guide sequence
```

## Key Implementation Decisions

1. **Use `bake_auxiliary_maps` pattern from `headless_comfy_ebsynth.py`**: This already
   implements the exact per-frame rendering loop we need. It takes `skeleton`, 
   `animation_func`, `style`, and produces `source_frames`, `normal_maps`, `depth_maps`,
   `mask_maps` with real geometric variation.

2. **Build animation_func from Motion2DPipeline clip_2d**: The `clip_2d.frames` contain
   per-frame `bone_transforms`. We can construct an `animation_func(t)` that interpolates
   between these frames.

3. **Temporal Variance Circuit Breaker**: Add `validate_temporal_variance()` to 
   `anti_flicker_runtime.py` that computes MSE between frame[0] and frame[N//2].
   If MSE < threshold, raise PipelineContractError.

4. **OOM Prevention**: Use chunked processing — render and consume frames in batches,
   explicitly `del` intermediate arrays after consumption.

## Global Forgery Pattern Audit

Suspicious patterns found:
- `headless_comfy_ebsynth.py:630` — `stylized = [None] * n_frames` — OK, pre-allocation
- `session065_research_bridge.py:694` — `cond_list = [None] * 15` — OK, pre-allocation
- `session065_research_bridge.py:711` — `[np.full(...)] * 5` — LEGACY test stub, not factory
- `headless_graph_fuzz_ci.py:314` — `[(n, n)] * 30` — LEGACY fuzz test, not factory
- `test_session065_research_modules.py:462` — `[np.full(...)] * 5` — test stub

**Verdict**: No additional factory-path forgery patterns beyond the already-identified
`_build_guide_sequence` in `run_mass_production_factory.py`. The SESSION-129 micro-jitter
approach must be replaced with true per-frame rendering.
