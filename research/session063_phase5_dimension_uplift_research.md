# SESSION-063: Phase 5 — Dimension Uplift Research Report

## 2.5D & True 3D Smooth Dimension Upgrade

> **Research Protocol**: This report consolidates deep research across 6 major
> topics for the project's Phase 5 "dimension uplift" roadmap. All findings are
> grounded in primary academic sources, industry GDC talks, and authoritative
> technical references.

---

## 1. Inigo Quilez (IQ) — SDF Smooth Min for 3D Skeletal Skinning

### 1.1 Background

Inigo Quilez is the co-founder of Shadertoy and the foremost authority on
real-time SDF techniques. His `smin` (Smooth Minimum) operators enable organic
blending of SDF primitives — critical for skeletal joint smoothing in 3D.

### 1.2 Core Mathematics

The **DD Family** of smin functions share a unified kernel form:

```
smin(a, b, k) = b - k · g((b-a)/k)
```

where `g(x)` is the kernel function defining the blend profile:

| Variant | Kernel g(x) | Continuity | Cost |
|---|---|---|---|
| Quadratic | (x+1)²/4 for x∈[-1,1] | C¹ | Lowest |
| Cubic | (1+3x(x+1)-\|x³\|)/6 | C² | Low |
| Quartic | (x+1)²(3-x(x-2))/16 | C³ | Medium |
| Circular | 1+0.5(x-√(2-x²)) | C¹ | Medium |
| Exponential | x/(1-exp2(-x)) | C∞ | Highest |

**Gradient after smin** (critical for Dual Contouring Hermite data):

```
∇smin(a,b) = (1 - g'(x))·∇a + g'(x)·∇b, where x = (b-a)/k
```

### 1.3 Application to Skeletal Skinning

Represent each bone segment as an SDF capsule primitive. Apply `smin_cubic`
between adjacent bone capsules with `k` proportional to joint flexibility.
The smooth blend region automatically creates organic joint bulging without
manual weight painting.

### 1.4 3D SDF Primitives Library (from IQ)

Sphere, Box, Capsule, Torus, Cylinder, Cone, Ellipsoid, Rounded Box,
Rounded Cylinder, Capped Cone, Capped Torus, Link, Hexagonal Prism,
Triangular Prism, Octahedron, Pyramid — all with exact distance formulas.

---

## 2. Tao Ju — Dual Contouring of Hermite Data (SIGGRAPH 2002)

### 2.1 Why Not Marching Cubes

Marching Cubes constrains vertices to grid edges → sharp features (weapon
blades, armor edges) are rounded off. Dual Contouring places one vertex per
cell at the QEF-optimal position → sharp features preserved.

### 2.2 Algorithm

1. **Hermite Data Extraction**: For each grid edge with sign change, compute
   intersection point `p` and surface normal `n = ∇SDF(p)`.
2. **QEF Minimization**: Per cell, solve `E[x] = Σ(nᵢ·(x-pᵢ))²` via SVD-based
   least squares → vertex position preserving sharp features.
3. **Mesh Construction**: For each sign-change edge, connect the 4 adjacent
   cell vertices into a quad.

### 2.3 Constrained QEF (Boris the Brave improvement)

- **Bias term**: Add `λ·‖x - center‖²` to QEF → pulls solution toward cell
  center when normals are colinear (flat surfaces).
- **Clamping**: Clamp final vertex to cell AABB bounds.

### 2.4 Octree Adaptivity

Cells near surface subdivide more finely. Balanced octree (max 1 level
difference between neighbors) simplifies crack-free stitching.

---

## 3. Pujol & Chica 2023 — Adaptive SDF Approximation

### 3.1 Key Contribution

*"Adaptive approximation of signed distance fields through piecewise
continuous interpolation"* (Computers & Graphics, Vol. 114, 2023).

Uses adaptive octree with **trilinear/tricubic polynomial** approximation per
leaf node. RMSE-based error control guides subdivision. Guarantees C⁰/C¹
continuity across cell boundaries.

### 3.2 Integration with Dual Contouring

The cached polynomial SDF provides both values AND gradients (Hermite data)
needed by Dual Contouring — faster than re-evaluating complex analytical SDF.
Acts as an acceleration cache between SDF evaluation and mesh extraction.

---

## 4. Arc System Works — Guilty Gear Xrd "三渲二" Pipeline

### 4.1 Core Techniques (Junya Motomura, GDC 2015)

| Technique | Purpose | Implementation |
|---|---|---|
| Manual Vertex Normal Editing | 2D-style flat shading on 3D models | Artists hand-edit normals per vertex |
| Limited Animation (Stepped) | Preserve 2D animation "snap" feel | Disable interpolation, keyframe every pose |
| Inverted Hull Outlines | Thick exterior outlines | Cull-front pass, extrude along normals |
| UV-based Inner Lines | Interior structure lines | Special UV channel encodes line data |
| Dual Color LUT | Precise shadow color control | Base texture + tint texture, step threshold |
| Vertex Color Shadow Bias | Per-vertex shadow shape sculpting | R channel offsets NdotL threshold |

### 4.2 Cel-Shading Formula

```glsl
float NdotL = dot(edited_normal, light_dir) * 0.5 + 0.5;
float shadow = step(0.5 - vertex_color.r, NdotL);
vec3 color = mix(base * tint, base, shadow);
```

### 4.3 Relevance to Project

The project's existing 2D SDF → depth map pipeline can drive the same
vertex-normal-editing workflow: SDF gradient = surface normal, which can be
artistically modified per-vertex for cel-shading control.

---

## 5. Isometric Camera 2.5D (Hades-style)

### 5.1 Camera Setup

- **Projection**: Orthographic (no perspective foreshortening)
- **Rotation**: (30°, 45°, 0°) — standard isometric angles
- **orthographicSize**: Controls visible world area

### 5.2 Sprite → Tessellated Plane → Displacement

1. Import 2D sprite as tight custom-outline mesh
2. Apply GPU tessellation (Hull + Domain shader stages)
3. Sample depth map as height map in Domain shader
4. Displace vertices along normal: `P' = P + N · H · S`

### 5.3 Depth Sorting

For dynamic objects in isometric view, use topological sort on AABB overlap
graph (DAG) rather than simple Y-sort. O(n²) pairwise, optimize with spatial
partitioning for large scenes.

---

## 6. SDF → 3D Mesh Conversion Pipeline

### 6.1 Algorithm Comparison

| Algorithm | Feature Edges | Hermite Data | Adaptive | Best For |
|---|---|---|---|---|
| Marching Cubes | Lost | Not needed | Via octree | Quick preview |
| Dual Contouring | Preserved | Required | Native octree | Production meshes |
| Surface Nets | Partial | Not needed | Via octree | Simple cases |

### 6.2 2D SDF → 3D SDF Operations

- **Extrusion**: `f_3d(x,y,z) = max(f_2d(x,y), |z| - depth/2)`
- **Revolution**: `f_3d(x,y,z) = f_2d(√(x²+z²), y)`
- **Smooth CSG**: Combine 3D primitives with smin variants

### 6.3 Complete Pipeline

```
2D SDF → [Extrude/Revolve] → 3D SDF Volume
       → [Pujol-Chica Adaptive Cache] → Polynomial Octree
       → [Dual Contouring + QEF] → Quad Mesh
       → [QEM Simplification] → LOD Chain
       → [OBJ Export] → Unity Import
```

---

## 7. Taichi AOT → Vulkan SPIR-V → Unity

### 7.1 Compilation Pipeline

```
Python @ti.kernel → Taichi CHI IR → SPIR-V bytecode → AOT Module
                                                        ↓
Unity C# → [DllImport] → C++ Native Plugin → Taichi C-API Runtime
                                               → Load AOT Module
                                               → Execute SPIR-V kernels
```

### 7.2 Data Bridge

- Taichi `ti.ndarray` ↔ Unity `ComputeBuffer` via `GetNativeBufferPtr()`
- Zero-copy GPU memory sharing when both use Vulkan backend

### 7.3 XPBD Cloth on GPU

The project's existing `TaichiXPBDClothSystem` (JIT) can be converted to AOT:

```python
mod = ti.aot.Module(ti.vulkan)
mod.add_kernel(substep_xpbd, template_args={...})
mod.save("cloth_aot_module")
```

Then loaded in Unity via Taichi C-API runtime for real-time cloth simulation.

---

## Key References

| # | Reference | Year |
|---|---|---|
| 1 | Inigo Quilez, "Smooth Minimum" | 2013 |
| 2 | Inigo Quilez, "3D Distance Functions" | 2013+ |
| 3 | Tao Ju et al., "Dual Contouring of Hermite Data", SIGGRAPH | 2002 |
| 4 | Boris the Brave, "Dual Contouring Tutorial" | 2018 |
| 5 | Eduard Pujol & Antonio Chica, "Adaptive approximation of SDF", C&G | 2023 |
| 6 | Junya Motomura, "GuiltyGearXrd's Art Style", GDC | 2015 |
| 7 | Matt Keeter, "2D Contouring" | 2015 |
| 8 | Taichi AOT Documentation | 2022+ |
| 9 | Catlike Coding, "Surface Displacement" | 2017 |
| 10 | Ned Makes Games, "Mastering Tessellation Shaders" | 2021 |
