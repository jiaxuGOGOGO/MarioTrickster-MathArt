# SESSION-062: Phase 4 Environment Closed-Loop Architecture Design

**Author**: Manus AI
**Date**: April 17, 2026

This document outlines the architectural design for integrating the Wave Function Collapse (WFC) algorithm and Taichi Stable Fluids into a Unity-native pipeline. The design focuses on bridging the gap between Python-based procedural generation and Unity's Tilemap and VFX Graph systems, establishing a robust closed-loop evolution mechanism.

## 1. WFC to Unity Tilemap Mapping

The current implementation of the WFC algorithm in `mathart/level/constraint_wfc.py` generates ASCII grid representations of levels. To seamlessly integrate this with Unity, we must translate these grids into structured JSON data that Unity can interpret as Tilemaps.

### 1.1. Dual Grid WFC Integration

Inspired by Oskar Stålberg's work on *Townscaper* and *Bad North* [1] [2], we will implement a Dual Grid WFC approach. This technique allows for the generation of organic, seamless terrain edges using a minimal tileset.

The base grid will continue to handle logical constraints (e.g., solid vs. air, reachability), while a secondary "dual grid" will be computed for visual representation. The dual grid vertices correspond to the centers of the base grid cells. By applying Marching Squares logic to the base grid, we can determine the appropriate tile for each dual grid cell.

### 1.2. JSON Export Schema

The Python pipeline will export a JSON file containing both logical and visual grid data:

```json
{
  "width": 30,
  "height": 10,
  "logical_grid": [
    [0, 0, 1, 1, ...],
    ...
  ],
  "dual_grid": [
    [5, 12, 3, ...],
    ...
  ],
  "physics_constraints": {
    "gravity": 26.0,
    "max_run_speed": 8.5,
    "jump_velocity": 12.0
  }
}
```

### 1.3. Unity Tilemap Instantiation

A generated C# script (`WFCTilemapLoader.cs`) will parse this JSON and instantiate the level in Unity. It will utilize Unity's `Rule Tile` system for the visual layer, mapping the dual grid indices to specific sprite assets. Crucially, it will attach a `CompositeCollider2D` to the logical grid's Tilemap, merging individual tile colliders into a single, optimized physics shape [3].

## 2. Taichi Stable Fluids to Unity VFX Graph

The existing `mathart/animation/fluid_vfx.py` module simulates fluid dynamics using a NumPy-based Stable Fluids solver [4]. To bring these effects into Unity, we will export the simulation results as sequence frames (flipbooks) and drive Unity's VFX Graph.

### 2.1. Sequence Frame Export

The Python pipeline will simulate the fluid over a set number of frames and export two primary assets:
1.  **Density Atlas**: A flipbook texture containing the visual representation of the fluid (e.g., smoke, fire).
2.  **Velocity Atlas**: A flow map texture where the Red and Green channels encode the normalized X and Y velocities of the fluid field.

### 2.2. VFX Graph Velocity Inheritance

In Unity, a VFX Graph will be configured to use the exported flipbook textures. To achieve dynamic interaction, the particle system must inherit velocity from the character [5].

A C# controller script (`FluidVFXController.cs`) will read the character's `Rigidbody2D.velocity` and pass it to the VFX Graph as an exposed property. The VFX Graph will then add this inherited velocity to the particles' motion, scaled by a multiplier. This allows effects like sword slashes or dash trails to realistically follow the character's movement trajectory.

## 3. Three-Layer Evolution Loop

To ensure continuous improvement and stability, these new subsystems will be integrated into the project's existing three-layer evolution architecture.

### 3.1. Layer 1: Internal Evaluation

The pipeline will automatically generate levels and fluid simulations, evaluating them against predefined metrics:
*   **WFC**: Playability (reachability validation), tile diversity, and generation time.
*   **Fluid VFX**: Flow energy, density mass, and obstacle avoidance.

### 3.2. Layer 2: External Knowledge Distillation

Successful generation parameters and rules will be distilled into Markdown files (e.g., `knowledge/wfc_tilemap_rules.md`, `knowledge/fluid_vfx_export_rules.md`). This captures the "why" behind successful configurations, building a persistent knowledge base.

### 3.3. Layer 3: Self-Iteration

The state of the evolution process, including best scores and historical trends, will be saved in JSON state files (e.g., `.wfc_tilemap_state.json`, `.fluid_vfx_export_state.json`). This allows the system to resume and refine its search for optimal parameters across multiple sessions.

## References

[1] Boris the Brave. "How does Planet Work". https://www.boristhebrave.com/2022/12/18/how-does-planet-work/
[2] Boris the Brave. "Quarter-Tile Autotiling". https://www.boristhebrave.com/2023/05/31/quarter-tile-autotiling/
[3] Unity Documentation. "Tilemap Collider 2D". https://docs.unity3d.com/2020.1/Documentation/Manual/class-TilemapCollider2D.html
[4] Jos Stam. "Stable Fluids". SIGGRAPH 1999.
[5] Unity Documentation. "Inherit Velocity module reference". https://docs.unity3d.com/6000.4/Documentation/Manual/PartSysInheritVelocity.html
