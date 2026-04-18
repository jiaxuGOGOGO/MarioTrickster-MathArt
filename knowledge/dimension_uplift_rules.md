# Dimension Uplift — Distilled Knowledge

Last updated: 2026-04-18T02:48:23.976705+00:00
Cycle: 3

## Research Sources

- Inigo Quilez: SDF Smooth Min (smin) for 3D skeletal skinning
- Tao Ju et al.: Dual Contouring of Hermite Data (SIGGRAPH 2002)
- Pujol & Chica: Adaptive SDF Approximation (C&G 2023)
- Arc System Works / Junya Motomura: Cel-shading (GDC 2015)
- Isometric Camera 2.5D (Hades-style displacement)
- Taichi AOT: Vulkan SPIR-V → Unity bridge

## Distilled Rules

- **dc_resolution_range**: `[16, 64]`
- **dc_bias_strength**: `0.01`
- **cache_max_depth**: `7`
- **cache_error_threshold**: `0.005`
- **smin_k_ranges**: `{"tight_joint": [0.02, 0.08], "medium_joint": [0.08, 0.2], "loose_blend": [0.2, 0.5]}`
- **displacement_strength_range**: `[0.2, 0.5]`
- **min_feature_preservation_score**: `0.4161`

## Metrics Snapshot

- DC Vertices: 400
- DC Faces: 398
- Feature Preservation: 0.5201
- Cache Nodes: 10249
- Cache Avg Error: 0.445498
- Displacement Coverage: 0.1936
- Displacement Smoothness: 0.9878
- Smin Blend Quality: 0.6250
- 3D Primitives Tested: 7
- All Modules Valid: True
- Pass Gate: True
