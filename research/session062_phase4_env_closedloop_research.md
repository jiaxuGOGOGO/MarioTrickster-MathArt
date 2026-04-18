# SESSION-062: Phase 4 Environment Closed-Loop & Content Volume Research Notes

## Research Protocol: Deep Reading

### 1. Maxim Gumin — Wave Function Collapse (WFC) Algorithm

**Source**: https://github.com/mxgmn/WaveFunctionCollapse (2016)

**Core Algorithm**:
- **Observe**: Find the cell with lowest Shannon entropy (fewest remaining options)
- **Collapse**: Select a tile weighted by frequency hints from the training data
- **Propagate**: Remove incompatible options from neighbors via arc consistency (AC-3)
- **Contradiction handling**: If any cell has zero options, backtrack or restart

**Key Insight for Unity Tilemap Mapping**:
WFC natively outputs a 2D integer grid where each cell contains a tile ID. This maps directly to Unity's `Tilemap.SetTile(Vector3Int, TileBase)` API. The critical bridge is:
1. Python WFC outputs `List[List[int]]` (tile ID grid)
2. Serialize to JSON: `{"width": W, "height": H, "tiles": [[id, ...], ...], "physics": {...}}`
3. Unity C# script reads JSON → instantiates `RuleTile` or `Tile` per cell
4. `CompositeCollider2D` on the Tilemap GameObject merges individual tile colliders

**Existing Implementation**: `mathart/level/constraint_wfc.py` already implements Gumin's algorithm with physics-based TTC vetoes. Current output is ASCII grid strings — needs upgrade to structured JSON with tile IDs.

### 2. Oskar Stålberg — Dual Grid WFC (Townscaper / Bad North / Planet)

**Sources**:
- Boris the Brave: "How does Planet Work" (2022) — https://www.boristhebrave.com/2022/12/18/how-does-planet-work/
- Boris the Brave: "Quarter-Tile Autotiling" (2023) — https://www.boristhebrave.com/2023/05/31/quarter-tile-autotiling/
- Oskar Stålberg Twitter (2021): "cut tiles along the dual grid instead of the main grid"

**Dual Grid Theory**:
The dual grid is constructed by placing a vertex at the center of every face of the base (primal) grid, then connecting vertices whose corresponding faces share an edge. For a square grid:
- Base grid stores terrain type per cell (solid/air/platform/hazard)
- Dual grid is offset by (0.5, 0.5) tile units
- Each dual cell's tile is selected based on the 4 surrounding base cell values
- This is equivalent to **Marching Squares** on the base grid's vertex data

**Minimal Tileset**:
- With rotation: only **5 quarter-tiles** (half-size) or **6 full-size marching squares tiles**
- Without rotation: **16 tiles** cover all 2⁴ corner combinations
- Stålberg's insight: by cutting tiles along the dual grid boundary, organic edges emerge naturally without visible seams

**Quarter-Tile Autotiling** (alternative to full dual grid):
- Split each base cell into 4 quarter-cells
- Each quarter-tile chosen by: current cell terrain + 3 adjacent/diagonal neighbors
- 6 rules per quadrant, rotated for other quadrants
- Fewer tiles needed, but less variation than full marching squares
- **Precomposition**: assemble quarter-tiles into full tiles ahead of time → 48 blob-pattern tiles

**Implementation Plan for Project**:
1. Add `DualGridMapper` class that takes WFC output grid and computes marching-squares indices
2. Each cell gets a 4-bit mask from its 4 corner values → selects from 16 tile variants
3. Output JSON includes both logical grid and dual-grid visual tile assignments
4. Unity side: `RuleTile` with neighbor-based rules, or direct tile assignment from JSON

### 3. Taichi Stable Fluids → Sequence Frame Export

**Source**: Jos Stam, "Stable Fluids" (SIGGRAPH 1999)

**Current Implementation**: `mathart/animation/fluid_vfx.py` — NumPy-based stable fluids with:
- Semi-Lagrangian advection
- Implicit diffusion
- Pressure projection (divergence-free)
- Obstacle masks
- Particle advection
- RGBA frame rendering

**Sequence Frame Export Strategy**:
1. Run simulation for N frames (e.g., 60 frames for a 5-second loop at 12fps)
2. Export each frame as:
   - **Density texture**: RGBA image (smoke/fire/water visualization)
   - **Velocity field texture**: RG channels encode (vx, vy) normalized to [0,1] range (0.5 = zero velocity)
   - **Metadata JSON**: per-frame diagnostics (flow energy, max speed, density mass)
3. Pack into flipbook atlas (grid of frames in single texture)
4. Generate Unity VFX Graph manifest referencing the atlas

**Taichi Upgrade Path** (optional GPU acceleration):
- Current NumPy solver works but is CPU-bound
- Taichi `@ti.kernel` can accelerate advection/projection 10-100x
- Export via `ti.tools.image` or direct numpy array extraction
- Graceful fallback already exists in `xpbd_taichi.py`

### 4. Unity VFX Graph — Velocity Inheritance

**Source**: Unity Documentation — Inherit Velocity Module, VFX Graph

**Velocity Inheritance Modes**:
- **Current**: Emitter's current velocity applied to all particles every frame
- **Initial**: Emitter's velocity applied once at particle birth
- **Multiplier**: Proportion of emitter velocity inherited (0.0 to 1.0+)

**VFX Graph Integration for Fluid Sequence Frames**:
1. **Flipbook Player Block**: Steps through sub-images in a texture atlas
   - `texIndex` attribute controls current frame
   - Supports smooth blending between frames
2. **Set Velocity from Map**: Sample velocity texture to drive particle motion
   - R channel → X velocity, G channel → Y velocity
   - 0.5 gray = zero velocity (standard flow map encoding)
3. **Custom Velocity Inheritance Script** (C#):
   ```csharp
   // Read character Rigidbody2D velocity
   Vector2 charVel = characterRb.velocity;
   // Pass to VFX Graph as exposed property
   vfxGraph.SetVector2("CharacterVelocity", charVel);
   // In VFX Graph: Add CharacterVelocity * InheritMultiplier to particle velocity
   ```

**Velocity Inheritance Architecture**:
```
Character Movement → Rigidbody2D.velocity
    ↓
VFX Graph Exposed Property: "CharacterVelocity" (Vector2)
    ↓
Initialize Particle: velocity += CharacterVelocity * inheritMultiplier
    ↓
Update Particle: 
  - Sample velocity field from flipbook atlas
  - Add character velocity offset (for sword qi / dash smoke)
  - Apply drag and lifetime decay
```

**Practical Example — Sword Qi with Running Speed Offset**:
- Character runs at speed V = (vx, 0)
- Sword slash emits particles with base direction (1, 0.3)
- Inherited velocity adds (vx * 0.7, 0) to each particle
- Result: sword qi curves forward when running, straight when standing

### 5. Integration Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Python Side (mathart/)                                              │
│                                                                      │
│  constraint_wfc.py                                                   │
│  ├─ ConstraintAwareWFC.generate() → ASCII grid                     │
│  ├─ NEW: WFCTilemapExporter.export_tilemap_json()                  │
│  │   ├─ Convert ASCII → tile ID grid                                │
│  │   ├─ Compute dual-grid marching squares indices                  │
│  │   ├─ Embed physics constraints metadata                          │
│  │   └─ Output: tilemap_data.json                                   │
│  │                                                                   │
│  fluid_vfx.py                                                        │
│  ├─ FluidDrivenVFXSystem.simulate() → frames                       │
│  ├─ NEW: FluidSequenceExporter.export_sequence()                    │
│  │   ├─ Render density frames → flipbook atlas PNG                  │
│  │   ├─ Render velocity field → flow map atlas PNG                  │
│  │   ├─ Generate VFX Graph manifest JSON                            │
│  │   └─ Output: vfx_atlas.png, flow_atlas.png, vfx_manifest.json   │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Unity Side (C# generated scripts)                                   │
│                                                                      │
│  WFCTilemapLoader.cs                                                 │
│  ├─ Load tilemap_data.json                                          │
│  ├─ Instantiate RuleTile per cell                                   │
│  ├─ Apply CompositeCollider2D                                       │
│  └─ Set up dual-grid visual layer                                   │
│                                                                      │
│  FluidVFXController.cs                                               │
│  ├─ Load vfx_manifest.json                                          │
│  ├─ Configure VFX Graph flipbook from atlas                         │
│  ├─ Read character velocity each frame                              │
│  └─ Apply velocity inheritance to VFX Graph                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Research-to-Code Traceability Matrix

| Research Reference | Core Idea | Target Code |
|---|---|---|
| Maxim Gumin — WFC (2016) | Observe/Collapse/Propagate constraint solver | `wfc_tilemap_exporter.py` tile ID grid output |
| Oskar Stålberg — Dual Grid WFC | Marching squares on dual grid for organic edges with minimal tiles | `wfc_tilemap_exporter.py` DualGridMapper |
| Jos Stam — Stable Fluids (1999) | Semi-Lagrangian advection + projection for divergence-free flow | `fluid_sequence_exporter.py` sequence frame export |
| Unity VFX Graph — Flipbook Player | Texture atlas animation for particles | `FluidVFXController.cs` flipbook configuration |
| Unity — Inherit Velocity Module | Parent velocity → particle velocity transfer | `FluidVFXController.cs` velocity inheritance |
| Unity — CompositeCollider2D | Merge tile colliders into optimized compound shape | `WFCTilemapLoader.cs` collider setup |
| Unity — RuleTile | Neighbor-based automatic tile selection | `WFCTilemapLoader.cs` dual-grid visual tiles |
