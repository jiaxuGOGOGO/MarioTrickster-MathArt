# SESSION-044 — Analytical SDF Rendering Research Notes

## Source 1 — Inigo Quilez: Normals for an SDF
- URL: https://iquilezles.org/articles/normalsSDF/
- Core landing insight: **surface normals come directly from the normalized gradient of the scalar field**, i.e. `n = normalize(∇f(p))`.
- Practical rule 1: for generic SDF compositions, **central differences** are the safest default because they avoid directional bias.
- Practical rule 2: the `1/(2h)` factor can be omitted before normalization when only the direction matters.
- Practical rule 3: choose `h` with respect to numerical stability and sampling footprint; in a 2D sprite pipeline, `h` should scale with pixel resolution rather than using a hardcoded world epsilon only.
- Practical rule 4: tetrahedron / reduced-sample variants are useful when evaluation is expensive, but for Python/Numpy offline baking the project can prefer readable central differences first, then add optimized paths if needed.
- Direct code implication for MarioTrickster-MathArt: add a reusable utility that evaluates SDF on a grid and returns `dist`, `grad_x`, `grad_y`, `normal_xyz`, so both industrial shading and exported texture maps share the same field.

## Source 2 — Inigo Quilez: Distance + Gradient 2D SDF
- URL: https://iquilezles.org/articles/distgradfunctions2d/
- Core landing insight: many 2D primitives can return **distance and analytic gradient together**, with gradient norm ≈ 1 for proper SDFs.
- Practical rule 1: represent the packed field as `(distance, dfdx, dfdy)` so downstream lighting/export code does not need to recompute gradients.
- Practical rule 2: transforms must move points into local shape space and transform gradients back carefully; **non-uniform scale breaks exact distance preservation**.
- Practical rule 3: even if not every current primitive in the project exposes analytic gradients, the rendering/export API should be designed so future primitives can plug in exact gradients while current ones fall back to finite differences.
- Direct code implication for MarioTrickster-MathArt: implement a gradient-capable baking layer with two modes:
  1. `finite_difference` fallback for any existing SDF callable.
  2. future-ready analytic path for shapes or packed field providers that can emit `(dist, grad)` directly.

## Immediate architectural decisions for Gap C1
1. **Do not route through Blender**. Bake normal/depth directly from the 2D SDF grid.
2. Introduce a dedicated module for **SDF auxiliary map baking** rather than hiding the logic inside the frame renderer.
3. Export at least three artifacts from the same bake:
   - signed distance field
   - tangent-space-like normal map derived from `(dfdx, dfdy, z)`
   - pseudo depth map derived from interior distance or normalized thickness proxy
4. Feed the baked metrics into the **three-layer evolution loop** so Layer 2 can distill rendering knowledge and Layer 3 can audit whether the industrial map path is active.

## Source 3 — Scott Lembcke: 2D Lighting Techniques
- URL: https://www.slembcke.net/blog/2DLightingTechniques/
- Core landing insight: once sprites expose **color + normal + depth-like surface properties**, they can participate in forward or deferred 2D lighting similarly to 3D materials.
- Practical rule 1: **deferred rendering** scales lighting cost roughly with `lights + objects`, so exporting auxiliary buffers from the baking stage is a strategically correct direction even if the project itself does not implement a whole engine renderer yet.
- Practical rule 2: screen-space lightmaps alone do not support normal mapping, so MarioTrickster-MathArt should explicitly target **engine-consumable normal maps** rather than only internal shading tricks.
- Practical rule 3: if future runtime wants many lights, a compact G-buffer-style export contract should include at least albedo, normal, and depth/height proxy.
- Direct code implication: design the new baking API so one call can return a structured bundle (`albedo`, `normal_map`, `depth_map`, optional metadata) instead of only a shaded sprite.

## Source 4 — Godot Docs: 2D Lights and Shadows
- URL: https://docs.godotengine.org/en/latest/tutorials/2d/2d_lights_and_shadows.html
- Core landing insight: mainstream engines consume **RGB normal maps** with `R=X`, `G=Y`, `B=Z`, and normal maps are often auto-generated rather than hand-painted.
- Practical rule 1: the project should export normals in standard 8-bit RGB image form so they can drop into engines directly.
- Practical rule 2: depth/height is not always a separate texture in 2D engines, but a **depth or height proxy remains useful** for internal deferred experiments, parallax-like effects, future specular generation, and engine-specific importers.
- Practical rule 3: the auxiliary map interface should stay extensible so specular/roughness/height can be added later without redesigning the renderer.
- Direct code implication: implement a normal-map encoder that maps `[-1,1] -> [0,255]` per channel and a depth-map encoder that maps normalized interior depth to grayscale.

## Consolidated implementation blueprint after four sources

| Design question | Decision |
|---|---|
| Where should Gap C1 land? | Add a dedicated auxiliary-map baking module under the animation/industrial rendering path rather than hiding it in generic SDF helpers. |
| How to get normals? | Use analytic-gradient-ready API with central-difference fallback on any existing SDF callable. |
| How to get depth? | Use signed-distance interior thickness proxy from the same baked field, normalized inside the silhouette and clamped outside. |
| What to export? | At minimum: albedo sprite, normal map, depth map, metadata JSON. |
| How should engines consume it? | Standard RGB normals (`X,Y,Z`) plus grayscale depth/height proxy suitable for Godot/Unity-style downstream tooling. |
| How should evolution see it? | Register the new path in Layer 2 provenance and Layer 3 audit so auxiliary-map baking becomes part of the self-evolving architecture instead of a one-off renderer trick. |
