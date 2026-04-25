# SESSION-197 Research Notes: Physics Data Bus Unification

## 1. Houdini PDG (Procedural Dependency Graph) & VFX Graph Pipeline

**Core Insight**: PDG is a procedural architecture designed to distribute tasks and manage dependencies to better scale, automate, and analyze content pipelines. TOP networks use nodes that generate and organize work items, define dependencies, and pass data through attributes.

**Application to MarioTrickster-MathArt**:
- Physics/fluid computation outputs (Vector Math fields, Fluid Dynamics maps) are treated as **work items** flowing through a dependency graph
- These work items must be **dynamically transformed** into conditioning streams (ControlNet inputs) at the assembly site
- The transformation is a **layer-based injection**: physics artifacts become independent control flow layers woven into the render pipeline
- The PDG model validates our approach: each physics/fluid artifact is a data attribute on a work item, consumed by downstream TOP nodes (ControlNet apply nodes)

## 2. Entity Component System (ECS) — Data-Oriented Design

**Core Insight**: ECS separates entities (IDs), components (data), and systems (logic). Components are pure data structs; systems scan for entities with matching component signatures and process them.

**Application to MarioTrickster-MathArt**:
- `VFX_FLOWMAP` and `PHYSICS_3D` outputs are **components** attached to the render entity
- The VFX Topology Hydrator acts as a **system**: it scans the payload context for these component signatures
- When detected, it triggers the corresponding AST node graph hydration logic
- This achieves **absolute decoupling**: the hydrator doesn't know about specific physics engines; it only reacts to the presence of artifact components
- Registry Pattern alignment: new artifact types register themselves; the hydrator discovers them via the artifact_family field

## 3. ONNX/TensorRT Computation Graph Multi-Path Fusion Topology

**Core Insight**: TensorRT aggressively fuses layers to minimize memory bandwidth. Graph optimizations include node fusion, constant folding, and layer elimination. The key principle is **serial topology** — operations must chain correctly without parallel disconnected paths.

**Application to MarioTrickster-MathArt (ControlNet Daisy-Chaining)**:
- Multiple ControlNets MUST be chained in serial topology: `Conditioning → ControlNetApply(Depth) → ControlNetApply(Normal) → ControlNetApply(OpenPose) → ControlNetApply(Fluid) → ControlNetApply(Physics) → KSampler`
- Each `ControlNetApplyAdvanced` node takes `positive`/`negative` from the **previous** node's output
- **CRITICAL**: Parallel/disconnected ControlNet paths cause "断头覆盖" (decapitation override) — the last one overwrites all previous conditioning
- ComfyUI Wiki confirms: "The key is to chain the conditions of the Apply ControlNet nodes when using multiple ControlNets"

### ControlNet Weight Guidelines (from ComfyUI Wiki research):
| ControlNet Type | Recommended Weight | Role |
|---|---|---|
| Depth | 0.7-0.8 | Build spatial perspective |
| Normal | 0.5 | Enhance surface details |
| OpenPose | 1.0 | Control character posture |
| Fluid/Flowmap (new) | 0.30-0.40 | External perturbation force |
| Physics/Soft-body (new) | 0.25-0.35 | Structural deformation hint |

### Serial Chain Order (SESSION-197 Design):
```
[CLIP Text Encode] → positive/negative
  → [ControlNetApply: Depth] 
    → [ControlNetApply: Normal]
      → [ControlNetApply: OpenPose]
        → [ControlNetApply: Fluid/Flowmap]  ← NEW
          → [ControlNetApply: Physics/3D]   ← NEW
            → [KSampler]
```

## 4. Design Decisions for SESSION-197

1. **New module**: `mathart/core/vfx_topology_hydrator.py` — ECS-style system that scans context for physics/fluid artifacts and injects ControlNet nodes
2. **Daisy-chain injection**: New nodes splice AFTER the OpenPose apply node (which is currently the last in the chain before KSampler)
3. **Fail-Fast with `os.path.exists()`**: Every artifact path is validated before injection
4. **Arbitrator upgrade**: `arbitrate_controlnet_strengths` extended with fluid/physics weight bands
5. **Pure runtime injection**: Zero modification to base JSON presets
6. **DAG closure validation**: Extended to cover new node types
