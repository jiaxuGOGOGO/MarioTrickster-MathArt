# SESSION-056 Research: Phase 1 — Breaking the Wall (破壁之战)

## Research Protocol: Activated

**Session**: SESSION-056
**Date**: 2026-04-17
**Base commit**: `acf5c9a5e943c620d7ecaf288d781c477ecf662e` (SESSION-055)

---

## 1. Striking Through the Visual Black Box: Zero-Flicker Neural Rendering Closed Loop

### 1.1 Jamriška & Sýkora — "Stylizing Video by Example" (SIGGRAPH 2019)

**Core Algorithm**: PatchMatch-based example-driven video stylization.

**Key Technical Points**:
- Artist paints one or more **keyframes**; the algorithm propagates style to all intermediate frames
- Uses **guide channels** (edges, positional maps, optical flow) to direct patch matching
- **Bidirectional synthesis**: forward pass from keyframe A, reverse pass from keyframe B, then Poisson blending to merge
- **Temporal NNF propagation**: reuses nearest-neighbor field from previous frame to initialize current frame, reducing flicker
- PatchMatch operates in a multi-scale pyramid (typically 5 levels)
- **Critical insight**: quality of temporal consistency is DIRECTLY proportional to quality of optical flow input

**Why This Matters for MarioTrickster-MathArt**:
- Traditional AI video (Sora, Wan2.1) ESTIMATES optical flow → inherent noise → flicker
- Our engine computes EXACT motion vectors from FK → zero estimation error
- Feeding ground-truth MV into EbSynth guide channels = theoretically perfect temporal consistency

### 1.2 ReEzSynth — Python Implementation of EbSynth

**Repository**: `FuouM/ReEzSynth` (MIT License)
**Key API**:
```python
from ezsynth.api import Ezsynth, ImageSynth, RunConfig, load_guide

# Video synthesis
config = RunConfig(
    pyramid_levels=5,
    uniformity=4000.0,
    use_sparse_feature_guide=True,
    use_temporal_nnf_propagation=True
)
synth = Ezsynth(
    content_dir="frames/",
    style_paths=["keyframes/0000.png"],
    style_indices=[0],
    output_dir="output/",
    config=config
)
final_frames = synth.run()

# Single image synthesis with custom guides
synth = ImageSynth(style_image="style.png", config=RunConfig(patch_size=7))
result, error = synth.run(guides=[
    load_guide("source_guide.png", "target_guide.png", weight=2.0),
])
```

**Features relevant to our pipeline**:
- Multi-guide synthesis with individual weights (edges, colors, positional maps)
- Temporal NNF propagation (experimental) — reduces flicker
- Sparse feature guiding (experimental) — pins style to moving objects
- Bidirectional synthesis with Poisson blending
- RAFT / NeuFlow v2 optical flow integration
- Full Python API for headless automation

### 1.3 Lvmin Zhang — ControlNet (ICCV 2023)

**Paper**: "Adding Conditional Control to Text-to-Image Diffusion Models"

**Core Architecture**:
- **Locked copy**: preserves pretrained SD weights (production-ready backbone)
- **Trainable copy**: learns conditional control via zero convolutions
- Zero convolutions start at zero → no harmful noise during fine-tuning
- Supports multiple conditioning types: edges, depth, normals, pose, segmentation

**Key Conditioning Modes for Our Pipeline**:
1. **ControlNet-Depth**: Uses depth map to preserve 3D structure
   - Our engine exports EXACT analytical depth maps (not estimated)
   - Weight 1.0 locks the geometric silhouette
2. **ControlNet-NormalBae**: Uses normal maps for surface detail
   - Our engine exports EXACT analytical normal maps
   - Weight 1.0 preserves XPBD physics contours
3. **Multi-ControlNet**: Stack depth + normal simultaneously
   - Combined weight 1.0 + 1.0 = AI only does material/texture rendering
   - Physics geometry is mathematically locked

**ComfyUI Headless API**:
- ComfyUI exposes REST API at `/prompt` endpoint
- Workflow JSON defines node graph programmatically
- Python `requests` library can submit workflows headlessly
- No browser/GUI needed — pure API automation

### 1.4 Synthesis: The headless_comfy_ebsynth Pipeline

**Architecture**:
```
MathArt Engine → FK poses → MotionVectorBaker → exact MV fields
                         → IndustrialRenderer → normal/depth/albedo
                         ↓
              ControlNet (NormalBae + Depth @ weight 1.0)
                         ↓
              AI generates ONLY material/texture on locked geometry
                         ↓
              Keyframes (every N frames, AI-stylized)
                         ↓
              EbSynth (ReEzSynth Python API)
              + ground-truth optical flow as guide channel
                         ↓
              60fps zero-flicker stylized animation
```

---

## 2. Industrial Asset Airdrop: Engine-Native Depth Importer

### 2.1 Sébastien Bénard — Dead Cells Pipeline (GDC 2018/2019)

**Key Pipeline Steps**:
1. Basic 2D pixel art model sheet → 3D model in 3ds Max
2. Skeletal animation with keyframe pose-to-pose
3. Custom tool renders mesh at low resolution WITHOUT anti-aliasing → pixel art
4. Export each frame as PNG + **normal map** for toon shader volume rendering
5. Cel shading on 3D models rendered in low resolution

**Critical Technical Details**:
- Normal maps enable 2D deferred lighting — sprites react to dynamic lights
- The "fat frame" concept: each sprite frame carries auxiliary data (normal, depth, etc.)
- Flickering pixels remain an unsolved issue in their pipeline (our MV approach solves this!)
- Asset reuse across characters is the single most time-saving trick

### 2.2 Dead Cells Shader Pipeline (Dan Moran / Broxxar Analysis)

**Normal-mapped 2D sprites**:
- Normal maps generated from 3D render pass
- Applied to 2D sprites via custom shader
- Dynamic 2D lights interact with normal-mapped sprites
- Creates convincing volume/depth on flat sprites

### 2.3 2D Deferred Lighting in Game Engines

**Godot 4 CanvasItem Lighting**:
- `CanvasItem` shader supports `NORMAL_MAP` built-in
- `PointLight2D` and `DirectionalLight2D` interact with normal-mapped sprites
- `light()` function in CanvasItem shader receives light direction
- Can implement custom SSS/rim light in `light()` function
- Thickness map → `LIGHT_VERTEX` attenuation for backlit translucency

**Unity URP 2D**:
- `Sprite-Lit-Default` shader supports normal maps natively
- `Light2D` component (freeform, sprite, global, point, spot)
- Custom `SpriteLit` shader graph supports:
  - Normal map input
  - Custom lighting model
  - Rim light via Fresnel-like edge detection on normal map
  - SSS approximation using thickness map as transmission mask

### 2.4 Engine Import Plugin Architecture

**Godot 4 Plugin** (`EditorSceneFormatImporter` / `EditorImportPlugin`):
```gdscript
# addons/mathart_importer/plugin.gd
@tool
extends EditorPlugin

func _enter_tree():
    add_import_plugin(MathArtImporter.new())

# addons/mathart_importer/importer.gd
@tool
extends EditorImportPlugin

func _get_recognized_extensions():
    return ["mathart"]

func _import(source_file, save_path, options, r_platform_variants, r_gen_files):
    # Parse JSON metadata
    # Auto-assemble CanvasItemMaterial with normal map
    # Generate PolygonCollider2D from point cloud data
    # Apply thickness to SSS/rim light shader
```

**Unity Plugin** (`ScriptedImporter`):
```csharp
[ScriptedImporter(1, "mathart")]
public class MathArtImporter : ScriptedImporter {
    public override void OnImportAsset(AssetImportContext ctx) {
        // Parse JSON metadata
        // Create Material with Sprite-Lit-Default or custom shader
        // Assign normal/depth/thickness/roughness textures
        // Generate PolygonCollider2D from contour points
        // Configure SSS/rim light parameters from thickness
    }
}
```

---

## 3. Research-to-Code Mapping

| Research Source | Core Idea | Target Implementation |
|---|---|---|
| Jamriška & Sýkora (SIGGRAPH 2019) | PatchMatch + optical flow guide channels for temporal style propagation | `headless_comfy_ebsynth.py` — EbSynth pipeline with ground-truth MV |
| ReEzSynth (FuouM) | Pure Python EbSynth API with multi-guide synthesis | `headless_comfy_ebsynth.py` — Python API integration |
| Lvmin Zhang ControlNet (ICCV 2023) | Locked geometry + trainable texture via zero convolutions | `headless_comfy_ebsynth.py` — ControlNet conditioning with normal+depth |
| Sébastien Bénard (Dead Cells) | 3D→2D pipeline with normal maps for deferred lighting | `engine_import_plugin.py` — Godot/Unity importer |
| Dead Cells shader pipeline | Normal-mapped sprites with dynamic 2D lighting | Engine plugin shader templates |
| Godot 4 CanvasItem lighting | Built-in normal map support + custom light() function | `addons/mathart_importer/` GDScript plugin |
| Unity URP 2D Sprite Lit | Native normal map + custom shader graph | `Editor/MathArtImporter.cs` C# plugin |

---

## References

[1] Jamriška, O., Sochorová, Š., Texler, O., et al. "Stylizing Video by Example." ACM TOG (SIGGRAPH), 2019.
[2] Zhang, L., Rao, A., Agrawala, M. "Adding Conditional Control to Text-to-Image Diffusion Models." ICCV, 2023.
[3] FuouM/ReEzSynth. "EbSynth in Python, version 2." GitHub, MIT License.
[4] Vasseur, T. "Art Design Deep Dive: Using a 3D pipeline for 2D animation in Dead Cells." Game Developer, 2018.
[5] Moran, D. "Shaders Case Study — Dead Cells' Character Art Pipeline." YouTube / GitHub.
[6] Godot Engine Documentation. "2D lights and shadows" / "CanvasItem shaders."
[7] Unity Documentation. "Sprite Lit shader graph reference for URP."
