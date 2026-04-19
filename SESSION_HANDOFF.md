# SESSION-089 Handoff — Dead Cells 3D→2D Orthographic Pixel Render Pipeline

## Executive Summary

**SESSION-089** delivers the **Dead Cells-style 3D→2D Orthographic Pixel Render Pipeline**, closing **P1-INDUSTRIAL-34C** — the deterministic industrial rendering counterpart to the generative AI visual pipeline completed in SESSION-084/086/087. The system now has a complete, production-grade path from 3D mesh data through orthographic projection, multi-pass channel extraction, cel-shading, and nearest-neighbor downscale to engine-ready Albedo/Normal/Depth sprite bundles.

The new `OrthographicPixelRenderBackend` self-registers via `@register_backend` (zero trunk modification) and delegates rendering to a pure NumPy software rasterizer in `orthographic_pixel_render.py` — guaranteed headless, zero GLFW/X11 dependency. Three anti-pattern red lines are enforced by 22 E2E tests: no perspective distortion (orthographic matrix validation), no bilinear blur (edge pixel alpha ∈ {0, 255}), and no GUI window crash (zero windowed context imports).

| Area | SESSION-089 outcome |
|---|---|
| **Task closure** | **P1-INDUSTRIAL-34C fully closed** — Dead Cells 3D→2D pipeline landed |
| **New engine module** | `mathart/animation/orthographic_pixel_render.py` (1012 lines) |
| **New backend plugin** | `mathart/core/orthographic_pixel_backend.py` (422 lines) |
| **Anti-pattern guards** | Perspective Distortion Trap, Bilinear Blur Trap, GUI Window Crash Trap |
| **Test coverage** | **22 PASS, 0 FAIL** — 22 test cases across 11 test groups |
| **Research** | Dead Cells GDC 2018, Guilty Gear Xrd GDC 2015, Headless EGL architecture |
| **Total new code** | ~2,179 lines (engine + backend + tests + research notes) |

## What Landed in Code

### 1. Orthographic Pixel Render Engine (`mathart/animation/orthographic_pixel_render.py`)

A pure NumPy software rasterizer implementing the full Dead Cells 3D→2D pipeline:

- **Orthographic Projection Matrix**: `build_orthographic_matrix()` constructs a pure orthographic 4×4 matrix with zero perspective distortion. The matrix maps 3D world coordinates to 2D screen space with absolute flatness — no near-far size variation.
- **Edge-Function Triangle Rasterizer**: `rasterize_triangles()` implements scanline rasterization with edge functions and a Z-buffer. Barycentric coordinates interpolate per-vertex normals, colors, and UVs across triangle surfaces.
- **Multi-Pass Channel Extraction**: Simultaneous extraction of spatially-aligned Albedo (base color), Normal (world-space normal map), and Depth (linear orthographic depth) buffers in a single rasterization pass.
- **Cel-Shading Kernel**: `apply_cel_shading()` computes N·L dot product (max(dot(N, L), 0)) and applies hard stepped threshold banding (configurable 2-3 discrete levels). Zero smooth gradients — hard pixel boundaries only (Guilty Gear Xrd discipline).
- **Nearest-Neighbor Downscale**: `nearest_neighbor_downscale()` uses `PIL.Image.resize(Image.Resampling.NEAREST)` exclusively. NEVER bilinear/bicubic. Preserves hard pixel edges.
- **Hard Edge Validation**: `validate_hard_edges()` extracts edge pixels and asserts alpha channel values are strictly {0, 255} — no intermediate values allowed.
- **Mesh Primitives**: `create_cube_mesh()` and `create_sphere_mesh()` generate test geometry with per-vertex normals and colors for E2E validation.

### 2. OrthographicPixelRenderBackend (`mathart/core/orthographic_pixel_backend.py`)

A registry-native backend plugin following the project's IoC architecture:

- **Self-Registration**: `@register_backend("orthographic_pixel_render")` — discovered automatically by `get_registry()`. Zero modification to AssetPipeline, Orchestrator, or any trunk code.
- **Backend-Owned Validation**: `validate_config()` normalizes render dimensions, lighting parameters, cel-shading thresholds, channel selection, and FPS. All parameter parsing is physically sunk into this Adapter (Hexagonal Architecture).
- **Strongly-Typed Output**: `execute()` returns an `ArtifactManifest` with `backend_type="orthographic_pixel_render"`, `artifact_family="sprite_sheet"`, texture_channels payload with engine slot bindings (Unity `_MainTex`/`_NormalMap`, Godot `texture_albedo`/`texture_normal`), and quality metrics.
- **Render Report**: JSON report with pipeline metadata, mesh stats, render config, hard edge validation results, and timing.

### 3. Registry Integration

- **`backend_registry.py`**: Added auto-import of `orthographic_pixel_backend` in `get_registry()`.
- **`backend_types.py`**: Added `ORTHOGRAPHIC_PIXEL_RENDER` type alias for canonical backend addressing.

### 4. E2E Tests (`tests/test_orthographic_pixel_render.py`)

22 tests across 11 test groups, all running headless with zero external dependencies:

| Test Group | Count | Purpose |
|---|---|---|
| Orthographic Matrix | 4 | Shape, values, anti-perspective guard, NDC mapping |
| Software Rasterizer | 2 | Cube and sphere coverage validation |
| Multi-Pass Channels | 1 | Albedo/Normal/Depth spatial alignment |
| Cel-Shading | 2 | Discrete band count, no smooth gradients |
| Nearest-Neighbor | 2 | Hard edges preserved, bilinear contamination detected |
| Backend Registry | 3 | Discovery, validate_config, execute→ArtifactManifest |
| Full Pipeline E2E | 3 | Cube, sphere, save/load round-trip |
| Headless Safety | 2 | No DISPLAY dependency, no GLFW import |
| BackendType Enum | 1 | Type alias exists |
| Edge Cases | 2 | Empty mesh, single triangle |

## Files Changed in SESSION-089

| File | Purpose |
|---|---|
| `mathart/animation/orthographic_pixel_render.py` | Pure NumPy software rasterizer engine (1012 lines) |
| `mathart/core/orthographic_pixel_backend.py` | Registry-native backend plugin (422 lines) |
| `mathart/core/backend_registry.py` | Auto-import of new backend in `get_registry()` |
| `mathart/core/backend_types.py` | `ORTHOGRAPHIC_PIXEL_RENDER` type alias |
| `tests/test_orthographic_pixel_render.py` | 22 E2E tests (619 lines) |
| `research_notes_session089.md` | Research notes: Dead Cells, Guilty Gear Xrd, Headless EGL |
| `PROJECT_BRAIN.json` | SESSION-089 metadata, P1-INDUSTRIAL-34C → CLOSED |
| `SESSION_HANDOFF.md` | This file |

## Research Decisions That Were Enforced

### Dead Cells GDC 2018 — Thomas Vasseur

The Dead Cells art pipeline [1] establishes that the correct 3D→2D workflow is: (1) author high-poly 3D models with skeletal animation, (2) render through an orthographic camera with zero perspective distortion, (3) downsample with nearest-neighbor interpolation (no anti-aliasing), (4) simultaneously export Albedo/Normal/Depth channels for 2D engine dynamic lighting. This directly constrained the implementation to use pure orthographic projection and nearest-neighbor downscale.

### Guilty Gear Xrd GDC 2015 — Junya C. Motomura

The Guilty Gear Xrd cel-shading system [2] establishes that 3D→2D lighting must use hard stepped thresholds to cut smooth transitions, producing discrete shadow bands rather than smooth gradients. Frame decimation to 12fps/15fps enhances the pixel animation punch. This directly constrained the cel-shading kernel to use threshold banding with configurable discrete levels and the default FPS to 12.

### Headless EGL / Software Rasterizer Architecture

CI environments and headless servers have no physical display [3]. The implementation uses a pure NumPy software rasterizer — zero dependency on GLFW, X11, EGL, or any windowed context. This guarantees silent headless execution in any environment.

| Research theme | Enforced implementation consequence |
|---|---|
| **Dead Cells orthographic render** | `build_orthographic_matrix()` — pure orthographic, zero perspective [1] |
| **Dead Cells nearest-neighbor** | `nearest_neighbor_downscale()` — PIL NEAREST only, never bilinear [1] |
| **Guilty Gear Xrd cel-shading** | `apply_cel_shading()` — N·L + stepped threshold banding [2] |
| **Headless architecture** | Pure NumPy rasterizer — zero GLFW/X11/EGL imports [3] |

## Anti-Pattern Guards (SESSION-089 Red Lines)

### 🚫 Perspective Distortion Trap

The render pipeline MUST NOT use perspective projection (FOV). Dead Cells 2D pixel quality requires absolute flatness — no near-far size variation. Tests `test_orthographic_matrix_shape_and_values` and `test_orthographic_matrix_anti_perspective_guard` verify the matrix is pure orthographic (row 3 col 3 is constant, no perspective divide).

### 🚫 Bilinear Blur Trap

The downscale step MUST NOT use bilinear or bicubic interpolation. These create intermediate color values at edges, producing a halo of "dirty pixels" that destroy pixel art aesthetics. Tests `test_nearest_neighbor_hard_edges` and `test_bilinear_would_contaminate` verify that edge pixels have alpha values strictly in {0, 255} and that bilinear interpolation would produce contaminated intermediate values.

### 🚫 GUI Window Crash Trap

The renderer MUST NOT import or initialize any windowed context (GLFW, X11, pygame, etc.). Any such dependency would crash in CI/headless environments with `X11 display not found` or `Failed to initialize GLFW`. Tests `test_headless_no_display_dependency` and `test_no_glfw_import` verify that the module runs without DISPLAY and that GLFW is not in `sys.modules`.

## Testing and Validation

| Test command | Result |
|---|---|
| `pytest tests/test_orthographic_pixel_render.py -v` | **22 passed, 0 failed** |

## Recommended Next Priorities

| Priority | Recommendation | Reason |
|---|---|---|
| **Immediate** | **P1-MIGRATE-4** | Backend hot-reload — force multiplier for rapid iteration |
| **High** | **P1-AI-2E** | Motion-adaptive keyframe planning for high-nonlinearity action segments |
| **Medium** | **P1-INDUSTRIAL-44A** | Engine-ready export templates integration into standard asset pipeline |

### Architecture Micro-Adjustments for Next Tasks

**For P1-MIGRATE-4 (Backend Hot-Reload)**: The `OrthographicPixelRenderBackend` is already a self-registering plugin discovered via `get_registry()`. To enable true hot-reload:
1. `BackendRegistry.discover()` already supports package-path scanning via `pkgutil.walk_packages()`. The next step is to wire a filesystem watcher (e.g., `watchdog`) that calls `discover()` when new `.py` files appear in `mathart/core/` or `mathart/export/`.
2. The current `_builtins_loaded` flag prevents re-registration. Hot-reload needs a `BackendRegistry.reload(name)` method that unregisters and re-imports a specific backend module.
3. The `OrthographicPixelRenderBackend` already uses lazy imports in `execute()` (imports `orthographic_pixel_render` at call time, not module load time), which is hot-reload-friendly.

**For P1-AI-2E (Motion-Adaptive Keyframes)**: The `OrthographicPixelRenderBackend` output (Albedo/Normal/Depth channels) can feed directly into the ComfyUI SparseCtrl pipeline (SESSION-086/087) as ControlNet guide inputs. The next step is:
1. Wire `OrthographicPixelRenderBackend.execute()` output paths into `ComfyUIPresetManager.assemble_sequence_payload()` as guide directories.
2. Use motion complexity metrics from the motion vector baker to dynamically adjust `frame_count` and SparseCtrl `end_percent` — high-nonlinearity segments get more keyframes.
3. The `fps` parameter in `OrthographicRenderConfig` (default 12, Guilty Gear Xrd discipline) can be overridden per-segment based on action intensity.

**For P1-INDUSTRIAL-44A (Engine Export Templates)**: The `texture_channels` payload in `ArtifactManifest.metadata` already includes engine slot bindings for Unity (`_MainTex`, `_NormalMap`, `_DepthMap`) and Godot (`texture_albedo`, `texture_normal`, `texture_depth`). The next step is to wire these into `EngineImportPluginGenerator` (SESSION-056) so the orthographic render output can be consumed directly by engine-specific import plugins.

## Known Constraints and Non-Blocking Notes

| Constraint | Status |
|---|---|
| Software rasterizer performance | **Adequate for sprite-scale** — 64×64 to 256×256 renders in <100ms |
| No GPU acceleration | **By design** — headless CI safety takes priority over speed |
| No skeletal animation integration | **Next step** — current pipeline accepts static meshes; UMR clip integration is P1-INDUSTRIAL-34C-NEXT |
| No texture mapping | **Vertex colors only** — UV-mapped texture support is a future extension |

## Files to Inspect First in the Next Session

| File | Why it matters |
|---|---|
| `mathart/animation/orthographic_pixel_render.py` | The software rasterizer engine — all rendering math lives here |
| `mathart/core/orthographic_pixel_backend.py` | The registry plugin — backend-owned validation and ArtifactManifest output |
| `tests/test_orthographic_pixel_render.py` | 22 E2E tests — the contract specification for the render pipeline |
| `research_notes_session089.md` | Research notes: Dead Cells, Guilty Gear Xrd, Headless EGL |
| `mathart/core/backend_registry.py` | Registry singleton — auto-import wiring for new backend |

## SESSION-087 Archive (Previous Handoff)

**SESSION-087** delivered the **ComfyUI WebSocket End-to-End Async Execution Engine**, closing P1-AI-2D-SPARSECTRL. The `ComfyUIClient` class implements industrial-standard ComfyUI automation: HTTP POST `/prompt`, WebSocket event-stream monitoring, and HTTP GET `/history` + `/view` for artifact retrieval. One-click pipeline runner (`tools/run_sparsectrl_pipeline.py`) orchestrates the full chain. 35 E2E tests, cumulative P1-AI-2D: 79 PASS. SESSION-088 fixed placeholder frame generator for meaningful ControlNet guide data.

## References

[1]: https://www.gamedeveloper.com/production/art-design-deep-dive-using-a-3d-pipeline-for-2d-animation-in-i-dead-cells-i- "Thomas Vasseur, Art Design Deep Dive: Using a 3D Pipeline for 2D Animation in Dead Cells, GDC 2018"
[2]: https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf "Junya C. Motomura, GuiltyGearXrd's Art Style: The X Factor Between 2D and 3D, GDC 2015"
[3]: https://www.khronos.org/egl "Khronos EGL — Native Platform Graphics Interface for headless rendering"
