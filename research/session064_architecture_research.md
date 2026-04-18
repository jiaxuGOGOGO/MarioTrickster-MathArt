# SESSION-064 Architecture Research: Three Paradigm Shifts

## 1. LLVM Registry Pattern & Dynamic Backend Registration

**Source**: Chris Lattner, "The Architecture of Open Source Applications" (AOSA Book); LLVM Pass Infrastructure

### Core Principles

1. **Three-Phase Decoupling**: Frontend (parsing) → IR (intermediate representation) → Backend (code generation). Each phase is fully independent and communicates only through the well-defined IR contract.

2. **Library-Based Design**: LLVM is designed as a set of reusable libraries, not a monolithic compiler. Each pass is a standalone C++ class in its own `.cpp` file, with only a single factory function exported.

3. **Pass Registration Pattern**: Each pass registers itself via a factory function (`createXxxPass()`). The PassManager discovers and chains passes based on declared dependencies. **The trunk code never needs to know about specific passes.**

4. **Dynamic Plugin Loading**: LLVM supports `--load` for dynamically loading pass plugins at runtime. New passes can be added without recompiling the optimizer.

5. **Explicit Dependency Declaration**: Passes declare what analyses they require. The PassManager resolves dependencies automatically.

### Application to MarioTrickster-MathArt

- **SDF/UMR = Frontend/IR**: The mathematical SDF computation and UMR data bus are the "frontend" and "IR" respectively.
- **Export Backends = Code Generators**: Motion 2D, URP 2D, 3D Mesh, Fluid Sequence, WFC Tilemap should each be independent "backends" that consume the IR.
- **Registry Pattern**: Use `@register_backend("name")` decorators so new backends self-register. The pipeline discovers them at import time.
- **No trunk modification needed**: Adding a new export backend should require zero changes to `AssetPipeline` or `EvolutionOrchestrator`.

## 2. Pixar USD Schema & Composition Arcs + Frostbite Render Graph

**Source**: Pixar OpenUSD; Yuriy O'Donnell, "FrameGraph: Extensible Rendering Architecture in Frostbite" (GDC 2017)

### Core Principles (USD)

1. **Schema-Driven Validation**: Every USD prim has a schema that defines its expected attributes. Validation is automated and composable.

2. **Composition Arcs (LIVRPS)**: Layers, Inherits, Variants, References, Payloads, Specializes — six operators for combining scene description. Each arc has strict precedence rules.

3. **Typed Artifact Families**: USD prims carry explicit type information (`UsdGeomMesh`, `UsdLuxLight`, etc.). There is no ambiguity about what a prim represents.

4. **Non-Destructive Overrides**: Higher layers can override lower layers without modifying them. This enables collaborative workflows.

### Core Principles (Frostbite Render Graph)

1. **Self-Contained Nodes**: Each render pass declares its inputs (resources read) and outputs (resources written). The graph system handles resource lifetime, transitions, and memory aliasing automatically.

2. **Three-Phase Execution**: Setup (declare passes and resources) → Compile (optimize resource usage, cull unused passes) → Execute (record GPU commands).

3. **Transient vs External Resources**: Graph-managed resources (transient) have automatic lifetime. External resources (like swapchain) are managed outside.

### Application to MarioTrickster-MathArt

- **Artifact Schema**: Replace loose `output_paths` lists with typed `ArtifactManifest` containing `artifact_family` (e.g., "sprite_sheet", "mesh_obj", "vat_bundle") and `backend_type` (e.g., "unity_urp_2d", "godot_4", "raw").
- **Composition**: Allow artifact manifests to reference and compose other artifacts (e.g., a character pack manifest references sprite sheets, VAT bundles, and shader configs).
- **Validation**: Every artifact must pass schema validation before acceptance — similar to USD's `usdchecker`.
- **Render Graph Analogy**: The pipeline becomes a DAG where each node declares inputs/outputs. The orchestrator handles resource flow and dependency resolution.

## 3. MAP-Elites & Pareto Front Multi-Objective Optimization

**Source**: Jean-Baptiste Mouret & Jeff Clune, "Illuminating Search Spaces by Mapping Elites" (2015); Kalyanmoy Deb, "NSGA-II" (2002)

### Core Principles (MAP-Elites)

1. **Behavioral Niches**: The behavior space is divided into cells (niches). Each cell stores only the highest-performing individual for that behavioral region.

2. **Quality-Diversity**: Unlike traditional optimization that seeks a single optimum, MAP-Elites seeks the best solution in EVERY niche. This produces a diverse archive of high-quality solutions.

3. **Illumination**: The algorithm "illuminates" the search space by revealing what is possible in each behavioral region, not just what is optimal overall.

4. **Decoupled Fitness**: Each niche has its own fitness function. There is no cross-niche comparison or weighted averaging.

### Core Principles (NSGA-II / Pareto Front)

1. **Non-Dominated Sorting**: Solutions are ranked by Pareto dominance. A solution dominates another if it is better in at least one objective and no worse in all others.

2. **Crowding Distance**: Among solutions of the same rank, those in less crowded regions of the objective space are preferred, maintaining diversity.

3. **No Weighted Sum**: Objectives are NEVER combined into a single scalar. The Pareto front preserves the full trade-off surface.

### Application to MarioTrickster-MathArt

- **Lane-Based Niches**: Each evolution lane (2D Contour, 3D Mesh, Fluid VFX, WFC Tilemap, Motion 2D) is a separate niche with its own fitness function.
- **No Cross-Lane Averaging**: The `EvolutionOrchestrator` must NEVER compute `(2D_score + 3D_score) / 2`. Each lane reports independently.
- **Meta-Report Aggregation**: The orchestrator produces a Meta-Report that shows the Pareto front across lanes — which lanes are strong, which need attention.
- **MAP-Elites Archive**: Each lane maintains its own archive of elite solutions indexed by behavioral descriptors (e.g., for morphology: body proportion × limb count).

## Key Implementation Decisions

| Decision | LLVM Analogy | USD Analogy | MAP-Elites Analogy |
|---|---|---|---|
| Backend registration | `@register_backend` decorator | Schema registration | Niche registration |
| Artifact typing | IR type system | Prim schema | Behavioral descriptor |
| Pipeline composition | Pass chaining | Composition arcs | Archive composition |
| Quality evaluation | Pass validation | `usdchecker` | Per-niche fitness |
| Extensibility | Plugin `.so` loading | Layer stacking | New niche creation |
