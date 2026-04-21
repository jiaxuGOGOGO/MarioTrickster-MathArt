# Session Handoff

| Key | Value |
|---|---|
| Session | `SESSION-116` |
| Focus | `P1-VAT-PRECISION-1` High-Precision Float VAT Baking & Unity Material Preset Closed-Loop |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `43 PASS / 0 FAIL` (`pytest tests/test_high_precision_vat.py`), covering eleven test groups: GlobalBoundsNormalizer / Hi-Lo 16-bit Encoding / NPY Export / HDR Export / Full Pipeline / Anti-Precision-Loss Guard / Anti-Local-Bounds Trap / Unity Material Preset / Evolution Bridge / Edge Cases / Shader Content |
| Full Regression | `111 PASS` (test_high_precision_vat 43 + test_fluid_momentum 35 + test_fluid_vfx 33 all green, proving new VAT precision pipeline did not break any existing fluid VFX, momentum injection, or dynamic boundary functionality) |
| Primary Files | `mathart/animation/high_precision_vat.py` (NEW), `tests/test_high_precision_vat.py` (NEW), `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## Executive Summary

This session closes **P1-VAT-PRECISION-1** — the critical upgrade from the SESSION-059 legacy 8-bit VAT encoder to an industrial-grade high-precision float VAT baking pipeline.  The implementation is grounded in three top-tier industrial/academic references:

1. **SideFX Houdini VAT 3.0 Specification** — Position displacement data MUST use HDR (float) textures; 8-bit sRGB PNG causes severe vertex jitter.
2. **Global Bounding Box Quantization** — Scale & Bias MUST be computed from the global min/max across ALL frames and ALL vertices; per-frame normalization causes catastrophic "scale pumping."
3. **Unity Texture Importer Discipline** — VAT position textures MUST be imported with `sRGB = False` (Linear), `Filter = Point`, `Compression = None`, `Generate Mip Maps = False`.

The legacy SESSION-059 `encode_vat_position_texture()` used `np.uint8` and `* 255` to encode positions into 8-bit RGBA PNG — this irreversibly destroys float precision and causes visible vertex jitter in the engine.  The new pipeline completely eliminates this precision catastrophe through a triple export strategy:

1. **Raw float32 binary (.npy)** — Zero precision loss.  RMSE = 0 for the round-trip.
2. **Radiance HDR (.hdr) via cv2** — Visual inspection format with float32 dtype.
3. **Hi-Lo 16-bit packed PNG pair** — Unity-compatible approach from Houdini VAT 3.0 "Split Positions into Two Textures."  Precision: 1/65535 ≈ 1.5e-5, well within the RMSE < 1e-4 requirement.

## Research Alignment Audit

| Reference | Requested Principle | SESSION-116 Concrete Closure |
|---|---|---|
| SideFX Houdini VAT 3.0 — HDR Position Texture | Position data MUST use HDR format; 8-bit PNG causes vertex jitter; "Select HDR if your performance budget can afford it" | Triple export: `.npy` (float32 zero-loss), `.hdr` (Radiance HDR via cv2), Hi-Lo PNG pair (16-bit precision). Zero `np.uint8` or `* 255` in the float export path. |
| Global Bounding Box Quantization | Scale & Bias MUST be global across ALL frames and vertices; per-frame normalization causes scale pumping | `GlobalBoundsNormalizer` computes `global_min = np.min(positions, axis=(0, 1))` in a single O(N) pass; verified by `test_global_bounds_differ_from_per_frame` and `test_per_frame_normalization_causes_scale_pumping` |
| Unity Texture Importer Discipline | sRGB = False, Filter = Point, Compression = None, MipMaps = False | `generate_unity_material_preset()` emits JSON with all settings enforced; verified by `test_preset_enforces_linear_space` |
| Houdini VAT 3.0 — Split Positions into Two Textures | Hi-Lo 16-bit packing for engines without HDR support | `encode_hilo_16bit()` / `decode_hilo_16bit()` with `value_16bit = round(value * 65535)`, `hi = value >> 8`, `lo = value & 0xFF`; round-trip RMSE < 1e-4 proven by 5 dedicated tests |
| Anti-C++-Build Trap | No OpenEXR C++ dependency; use only cv2/numpy | Only `cv2.imwrite('.hdr')` and `np.save()` used; zero C++ build dependencies |

## What Changed in Code

| File | Change | Lines |
|---|---|---|
| `mathart/animation/high_precision_vat.py` | **NEW** `GlobalBoundsNormalizer`, `HighPrecisionVATConfig`, `HighPrecisionVATManifest`, `HighPrecisionVATResult`, `encode_hilo_16bit`, `decode_hilo_16bit`, `export_vat_hdr`, `export_vat_npy`, `export_vat_hilo_png`, `bake_high_precision_vat`, `generate_unity_material_preset`, `UNITY_HIGH_PRECISION_VAT_SHADER`, `VATEvolutionMetrics`, `evaluate_vat_precision` | ~580 |
| `tests/test_high_precision_vat.py` | **NEW** 43 white-box regression tests across 11 test classes | ~650 |
| `PROJECT_BRAIN.json` | **UPDATED** P1-VAT-PRECISION-1 status `TODO` → `CLOSED` with full closure description | +10 / -3 |
| `SESSION_HANDOFF.md` | **REWRITTEN** for SESSION-116 | full rewrite |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| 43/43 new white-box tests all green | `pytest tests/test_high_precision_vat.py` → `43 passed in 3.59s` |
| GlobalBoundsNormalizer | `test_global_min_max_across_all_frames` — bounds match `np.min(axis=(0,1))`; `test_normalized_range_is_zero_to_one` — all values in [0,1]; `test_denormalize_recovers_original` — RMSE < 1e-5; `test_3d_positions` — 3-channel support; `test_rejects_invalid_shape` — error on bad input; `test_no_python_for_loops_in_normalize` — vectorised <1s for 50K points; `test_extent_is_positive` — degenerate case handled |
| Hi-Lo 16-bit Precision | `test_roundtrip_rmse_below_threshold` — RMSE < 1e-4; `test_max_error_below_half_lsb` — max error < 2e-5; `test_output_dtype_is_uint8` — correct dtype; `test_3d_channels` — 3-channel RMSE < 1e-4; `test_full_pipeline_rmse_with_denormalize` — normalized RMSE < 1e-4 |
| NPY Zero-Loss | `test_npy_roundtrip_exact_zero` — `np.testing.assert_array_equal`; `test_npy_dtype_is_float32` — no uint8 |
| HDR Export | `test_hdr_file_created` — file exists with size > 0; `test_hdr_preserves_float32_dtype` — loaded dtype is float32 |
| Full Pipeline | `test_all_files_created` — all 8 output files exist; `test_manifest_contains_global_bounds` — bounds match numpy; `test_manifest_json_is_valid` — precision=float32, encoding=global_bounds_normalized; `test_hilo_roundtrip_rmse_in_diagnostics` — RMSE < 1e-4; `test_3d_pipeline` — 3D support; `test_shader_file_contains_hilo_decode` — DecodeHiLo, DenormalizePosition, SAMPLE_TEXTURE2D_LOD |
| Anti-Precision-Loss Guard | `test_npy_contains_no_uint8` — loaded dtype ≠ uint8; `test_source_code_no_uint8_in_float_path` — regex audit of export_vat_npy, export_vat_hdr, GlobalBoundsNormalizer source code confirms zero uint8/255 |
| Anti-Local-Bounds Trap | `test_global_bounds_differ_from_per_frame` — no single frame matches global bounds; `test_per_frame_normalization_causes_scale_pumping` — per-frame error > 10x global error |
| Unity Material Preset | `test_preset_enforces_linear_space` — sRGB=False, Filter=Point, Compression=Uncompressed, MipMaps=False for both Hi/Lo textures; `test_preset_contains_shader_reference` — correct shader name; `test_preset_contains_decode_formula` — boundsMax/boundsMin in formula |
| Evolution Bridge | `test_evaluation_passes_for_good_bake` — precision_pass=True, global_bounds_valid=True, npy_rmse < 1e-5, hilo_rmse < 1e-4; `test_metrics_to_dict` — serialization verified |
| Edge Cases | `test_single_frame` — 1-frame animation; `test_single_vertex` — 1-vertex mesh; `test_all_same_position` — degenerate identical positions; `test_very_large_displacement` — amplitude=100; `test_negative_positions` — negative range; `test_config_disable_exports` — selective export; `test_rejects_invalid_positions` — error on bad shape |
| Shader Content | `test_shader_has_correct_name` — "MathArt/HighPrecisionVATLit"; `test_shader_has_urp_tags` — UniversalPipeline; `test_shader_has_bounds_properties` — VatBoundsMin/Max; `test_shader_uses_lod_sampling` — SAMPLE_TEXTURE2D_LOD; `test_shader_has_hilo_decode_comment` — documented decode |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| No modification to core orchestrator | Compliant. No changes to `AssetPipeline`, `Orchestrator`, `FluidGrid2D`, `FluidDrivenVFXSystem`, or any `if/else` routing. All new functionality is encapsulated in the standalone `high_precision_vat.py` module. |
| Independent encapsulation | Compliant. `high_precision_vat.py` is a self-contained plugin module. It does not import from or modify `unity_urp_native.py` (SESSION-059 legacy). The legacy 8-bit encoder remains untouched for backward compatibility. |
| Strong-typed contract | Compliant. `HighPrecisionVATManifest` explicitly declares precision="float32", encoding="global_bounds_normalized", and includes bounds_min/max/extent. `HighPrecisionVATConfig` and `HighPrecisionVATResult` are strongly-typed dataclasses. |
| SESSION-059 backward compatibility | Compliant. The legacy `encode_vat_position_texture()`, `bake_cloth_vat()`, and `VATBakeManifest` in `unity_urp_native.py` remain unchanged. All 68 existing tests pass. |
| Anti-Precision-Loss Guard | Compliant. Zero `np.uint8` or `* 255` in the float export path (export_vat_npy, export_vat_hdr). Source code audit test confirms. |
| Anti-Local-Bounds Trap | Compliant. `GlobalBoundsNormalizer` uses `np.min(positions, axis=(0, 1))` — global across ALL frames and vertices. Test proves per-frame normalization causes 10x+ worse error. |
| Anti-C++-Build Trap | Compliant. Only `cv2` (HDR) and `numpy` (npy) are used. No OpenEXR C++ dependency. |

## Dependency Graph

```
Vertex Animation Sequence [frames, vertices, C]
    │
    ▼
GlobalBoundsNormalizer
    │  global_min = np.min(pos, axis=(0,1))
    │  global_max = np.max(pos, axis=(0,1))
    │  normalize() → [0, 1] float32
    │  denormalize() → world space (shader decode)
    │
    ├──→ export_vat_npy()     → .npy (float32, zero loss)
    ├──→ export_vat_hdr()     → .hdr (Radiance HDR via cv2)
    ├──→ export_vat_hilo_png() → Hi/Lo PNG pair (16-bit)
    │        │
    │        ├── encode_hilo_16bit() → (hi_uint8, lo_uint8)
    │        └── decode_hilo_16bit() → float64 (shader-side)
    │
    ▼
bake_high_precision_vat()
    │  Orchestrates full pipeline
    │
    ├──→ HighPrecisionVATManifest (JSON)
    ├──→ generate_unity_material_preset() → Material Preset JSON
    ├──→ UNITY_HIGH_PRECISION_VAT_SHADER → .shader (HLSL)
    └──→ evaluate_vat_precision() → VATEvolutionMetrics
```

## P1-ARCH-5 Readiness Analysis: USD TimeSamples Compatibility

For seamless integration with **P1-ARCH-5** (OpenUSD-compatible scene interchange for Omniverse), the current VAT generator data structure needs the following **Cache Track metadata** to be compatible with Pixar USD `TimeSamples` specification:

### Required USD TimeSamples Metadata

| Metadata Field | Current Status | Required for USD TimeSamples |
|---|---|---|
| `time_codes` (frame timestamps) | Implicit (0..N-1) | Must export explicit `Usd.TimeCode` array: `[0.0, 1.0, ..., N-1]` at the configured FPS |
| `stage_time_codes_per_second` | `fps` field exists in manifest | Must map to `stage.SetTimeCodesPerSecond(fps)` |
| `start_time_code` / `end_time_code` | Not explicitly stored | Must export `stage.SetStartTimeCode(0)` / `stage.SetEndTimeCode(frame_count - 1)` |
| `interpolation` qualifier | Not stored | Must declare `UsdGeom.Tokens.vertex` for per-vertex data; `held` interpolation for VAT (no inter-frame blending) |
| `primvar:st` (UV coordinates) | Vertex layout in manifest | Must export as `UsdGeom.PrimvarsAPI` with `texCoord2f[]` type |
| `points` attribute with TimeSamples | Positions stored in texture | Must also export as `UsdGeom.Mesh.points.Set(positions[f], time=f)` for each frame |
| `extent` attribute with TimeSamples | `bounds_min/max` in manifest | Must export per-frame `UsdGeom.Boundable.extent` or use global bounds |
| `customData:vat_encoding` | `encoding` field in manifest | Must persist as USD custom metadata for downstream tools |
| `customData:vat_bounds_min/max` | In manifest JSON | Must persist as USD `float3` custom attributes |

### Recommended Cache Track Structure

```python
# Future P1-ARCH-5 integration sketch:
from pxr import Usd, UsdGeom, Sdf, Vt

stage = Usd.Stage.CreateNew("vat_cache.usda")
stage.SetTimeCodesPerSecond(manifest.fps)
stage.SetStartTimeCode(0)
stage.SetEndTimeCode(manifest.frame_count - 1)

mesh = UsdGeom.Mesh.Define(stage, "/World/ClothMesh")
points_attr = mesh.GetPointsAttr()

for frame_idx in range(manifest.frame_count):
    # Denormalized world-space positions from VAT
    world_positions = denormalize(normalized[frame_idx])
    points_attr.Set(
        Vt.Vec3fArray(world_positions.tolist()),
        time=Usd.TimeCode(frame_idx)
    )

# Persist VAT metadata as custom attributes
prim = mesh.GetPrim()
prim.CreateAttribute("customData:vat_encoding", Sdf.ValueTypeNames.String).Set("global_bounds_normalized")
prim.CreateAttribute("customData:vat_bounds_min", Sdf.ValueTypeNames.Float3).Set(tuple(manifest.bounds_min))
prim.CreateAttribute("customData:vat_bounds_max", Sdf.ValueTypeNames.Float3).Set(tuple(manifest.bounds_max))
```

### Gap Analysis for P1-ARCH-5

1. **3D Position Requirement**: Current pipeline supports both 2D and 3D. USD requires 3D `Vec3f`. The `GlobalBoundsNormalizer` already handles 3-channel data — ready.
2. **TimeSamples Export**: Need a new `export_vat_usd()` function that iterates over frames and sets `points` attribute per `TimeCode`. The `GlobalBoundsNormalizer.denormalize()` method provides the exact inverse needed.
3. **Topology Stability**: USD `TimeSamples` on `points` assumes stable topology (same vertex count per frame). Our VAT pipeline guarantees this — ready.
4. **Material Binding**: USD material binding (`UsdShade.Material`) needs the Hi-Lo textures as `UsdShade.Shader` inputs. The `generate_unity_material_preset()` pattern can be adapted to emit `UsdPreviewSurface` nodes.

## Handoff Notes

- P1-VAT-PRECISION-1 is fully closed. The high-precision VAT pipeline eliminates the 8-bit precision catastrophe and provides industrial-grade float encoding.
- The legacy SESSION-059 8-bit encoder in `unity_urp_native.py` remains untouched for backward compatibility.
- The `GlobalBoundsNormalizer` is reusable for any tensor normalization task (not limited to VAT — applicable to motion capture, point clouds, etc.).
- The Hi-Lo 16-bit packing approach is the same technique used by Houdini VAT 3.0's "Split Positions into Two Textures" option.
- The Unity URP Shader (`HighPrecisionVATLit`) is production-ready with proper Hi-Lo decode, global bounds denormalize, and LOD-0 Point-filtered sampling.
- For P1-ARCH-5 integration, the key gap is a `export_vat_usd()` function that maps `GlobalBoundsNormalizer` output to USD `TimeSamples` on `UsdGeom.Mesh.points`. The data structures are already compatible; only the USD serialization layer needs to be added.
- Future work: (1) Add `export_vat_usd()` for P1-ARCH-5 Omniverse integration. (2) Add GPU-accelerated Hi-Lo encoding via Taichi for real-time baking. (3) Add normal map VAT channel for lighting. (4) Add velocity VAT channel for motion blur.
