# SESSION-054 Industrial Skin Research Notes

## Source 1 — Inigo Quilez, Normals for an SDF
- URL: https://iquilezles.org/articles/normalsSDF/
- Key point: surface normals align with the **gradient** of the scalar field; numerical central differences are common but are not the most accurate or fastest path.
- For this project, the actionable interpretation is stronger than the page itself: since our sprite pipeline already knows its base primitives, we should stop re-estimating gradients from sampled distance grids whenever an analytic derivative exists.
- Central differences are unbiased but still a sampling approximation. They are acceptable as a fallback for arbitrary composed fields, but **not** as the primary path for core primitives like circle/capsule/rounded box.
- IQ also stresses that gradient quality and sampling footprint matter for aliasing. This reinforces that we should keep an explicit distinction between:
  1. **analytic primitive gradients** for high-confidence normals,
  2. **sampled field gradients** as fallback for arbitrary composite fields.

## Source 2 — Dead Cells 3D-to-2D pipeline article (Thomas Vasseur / Motion Twin lineage)
- URL: https://www.gamedeveloper.com/production/art-design-deep-dive-using-a-3d-pipeline-for-2d-animation-in-i-dead-cells-i-
- Key point: render at very small size **without anti-aliasing**, export each animation frame to PNG **together with its normal map**, then light the sprite volume with a basic toon shader.
- Pipeline logic: start from simple model/skeleton, animate pose-to-pose, export frames plus per-frame material aids, keep retakes cheap, and reuse assets aggressively.
- Most relevant landing insight for this repository: the project should emit not just albedo, but a stable **material bundle** per frame/state: normal, depth, thickness/volume proxy, roughness proxy, and JSON metadata describing intended engine usage.
- This confirms the user’s requested direction: build **engine-ready 2.5D commercial assets** rather than only internal analysis textures.

## Immediate implementation mapping hypothesis
- Introduce an **analytic gradient path** in `sdf_aux_maps.py` for core primitives and keep central-difference as fallback.
- Extend the industrial renderer/export bridge to emit **fat-frame material bundles** containing at least `normal`, `depth`, `roughness`, `thickness`, `mask`, and export metadata.
- Use interior negative distance for **thickness** and derive a stable **roughness proxy** from curvature or gradient divergence.
- Add an industrial-skin evolution bridge that batch-audits exported material consistency and writes durable knowledge for later sessions.

## Source 3 — Inigo Quilez, 2D Distance and Gradient Functions
- URL: https://iquilezles.org/articles/distgradfunctions2d/
- This page is the decisive implementation reference for the current repository.
- IQ explicitly packages **distance and gradient together** for 2D primitives, returning distance in one component and analytic partial derivatives in the others.
- Extracted actionable primitives relevant to this project:
  - Circle: distance plus exact normalized radial direction.
  - Segment/capsule: project to line segment, compute exact offset vector, normalize to get gradient.
  - Box: piecewise analytic gradient depending on whether the point is outside corner regions or inside axis-dominant regions.
  - Rounded operations can reuse the base shape gradient while shifting only the signed distance term.
- Key landing implication: the project should define a compact **distance+gradient contract** for canonical primitives and only fall back to sampled differences for arbitrary composite fields.

## Source 4 — Curvature of a Distance Field / Implicit Surface
- URL: https://rodolphe-vaillant.fr/entry/118/curvature-of-a-distance-field-implicit-surface
- This source is useful not as a production-ready one-line formula, but as a confirmation that **curvature can be derived from SDF differential structure** and can be cheaply approximated for shading/material purposes.
- The page includes explicit formulas for implicit-curve/surface curvature and also a practical finite-difference curvature approximation used in shader workflows.
- For this repository, the best engineering compromise is:
  1. keep **analytic gradients** for normals,
  2. compute **curvature/roughness proxy** from gradient divergence or a Laplacian-like approximation on the already sampled field,
  3. expose this as a material proxy rather than over-claiming exact geometric curvature for complex composite shapes.
- Therefore, roughness should be treated as an **engine-facing material proxy** derived from stable field differentials, not a physically absolute BRDF truth.

## Refined implementation mapping

| Research conclusion | Repository landing decision |
|---|---|
| Core primitives have exact distance+gradient formulas | Add analytic-gradient support for canonical 2D primitives and keep central-difference only as fallback |
| Rounded variants preserve base gradient direction while shifting distance | Reuse primitive gradients for round/expand operators where mathematically valid |
| Dead Cells exports frame PNG plus normal map for lighting | Extend industrial renderer/export to emit per-frame material bundle, not just analysis textures |
| Thickness can come from interior negative distance | Add normalized thickness map from signed interior depth |
| Roughness can be proxied from curvature/divergence | Add curvature-derived roughness proxy with explicit metadata stating it is a stylized material channel |
| Asset delivery must be engine-ready | Export material metadata JSON describing normal/depth/thickness/roughness usage for Unity URP 2D / Godot 4 style workflows |
