# SDF to 3D Mesh Conversion — Technology Comparison Research Notes

## Overview

This document compares the major algorithms for converting Signed Distance Fields (SDF) into explicit polygon meshes, with focus on feature preservation, adaptive resolution, and applicability to the MarioTrickster-MathArt project's existing 2D SDF system.

## Algorithm Comparison

| Algorithm | Feature Edges | Topology | Complexity | Adaptive | Hermite Data |
|---|---|---|---|---|---|
| Marching Cubes (Lorensen & Cline 1987) | Lost (rounded corners) | Primal grid | 15 cases (3D), 4 cases (2D) | Via octree | Not required |
| Dual Contouring (Ju et al. 2002) | Preserved via QEF | Dual grid | 1 vertex per cell | Native octree | Required (SDF + gradient) |
| Surface Nets (Gibson 1998) | Partially preserved | Dual grid | Average of edge crossings | Via octree | Not required |
| Marching Squares (2D) | Lost | Primal grid | 16 cases | Via quadtree | Not required |
| Dual Contouring 2D | Preserved | Dual grid | 1 vertex per cell | Via quadtree | Required |

## Marching Cubes Limitations

Marching Cubes places vertices on grid edges where sign changes occur, then connects them using a lookup table of 15 canonical cases (256 total, reduced by symmetry). The fundamental limitation is that vertices are constrained to lie on edges, which means sharp features (corners, creases) are always rounded off. For weapon blades, armor edges, and geometric details in game characters, this is unacceptable.

## Dual Contouring — The Preferred Method

Dual Contouring (Tao Ju, Frank Losasso, Scott Schaefer, Joe Warren, SIGGRAPH 2002) solves the feature preservation problem by placing one vertex per cell at the position that minimizes the Quadratic Error Function (QEF). The QEF is constructed from Hermite data: for each edge with a sign change, we record the intersection point and the surface normal (gradient) at that point.

### QEF Minimization

Given a set of intersection points p_i and normals n_i, the QEF is:

E(x) = Σ (n_i · (x - p_i))²

This is a least-squares problem: find x that minimizes the sum of squared distances to the tangent planes defined by (p_i, n_i). The solution is obtained via SVD of the normal matrix A = [n_1; n_2; ...], with b = [n_1·p_1; n_2·p_2; ...].

### Constrained QEF (Boris the Brave's improvement)

The original QEF can produce vertices outside the cell for flat surfaces (colinear normals). Two techniques fix this:

1. **Constrained solving**: Clamp the QEF solution to lie within the cell bounds.
2. **Bias term**: Add a quadratic penalty pulling toward the cell center: E'(x) = E(x) + λ||x - center||². This has stronger effect when normals are colinear (the problematic case) and minimal effect when normals are diverse (the good case).

### Octree Adaptivity

Dual Contouring naturally supports octree (3D) / quadtree (2D) adaptive refinement. Cells near the surface are subdivided more finely, while cells far from the surface remain coarse. The key challenge is stitching meshes between cells of different sizes, which Ju et al. solve by restricting the octree to be balanced (no more than one level difference between adjacent cells).

## 2D SDF to 3D Mesh Pipeline

For the MarioTrickster-MathArt project, the path from existing 2D SDF to 3D mesh involves:

1. **Revolution**: Rotate a 2D SDF profile around an axis to create a 3D SDF (body of revolution). For a 2D SDF f(x,y), the 3D revolution SDF is: f_3d(x,y,z) = f(sqrt(x²+z²), y).

2. **Extrusion**: Extend a 2D SDF along the Z axis with bounded depth: f_3d(x,y,z) = max(f_2d(x,y), |z| - depth/2).

3. **Smooth CSG in 3D**: Use IQ's smin variants (already implemented in smooth_morphology.py) to blend 3D primitives.

4. **Dual Contouring extraction**: Sample the 3D SDF on an adaptive octree, compute Hermite data (SDF values + analytical gradients), solve QEF per cell, extract mesh.

## Pujol & Chica 2023 — Adaptive SDF Approximation

Eduard Pujol and Antonio Chica ("Adaptive approximation of signed distance fields through piecewise continuous interpolation", Computers & Graphics 2023) present an adaptive octree that stores polynomial approximations (trilinear or tricubic) of the SDF per leaf node. This is directly applicable as a caching layer between the analytical SDF evaluation and Dual Contouring mesh extraction:

- The cached polynomial SDF provides both values and gradients (Hermite data) needed by Dual Contouring
- RMSE-based error control guides adaptive subdivision
- C⁰/C¹ continuity is guaranteed across cell boundaries
- Faster than re-evaluating complex analytical SDF at each query point

## Mesh Simplification and LOD

After Dual Contouring extraction, the mesh can be simplified using:

1. **QEM (Quadric Error Metrics)** by Garland & Heckbert 1997 — iterative edge collapse guided by quadric error
2. **Feature-aware decimation** — preserve edges where dihedral angle exceeds threshold
3. **LOD chain generation** — produce multiple resolution levels for runtime selection

## Key References

| Reference | Contribution |
|---|---|
| Tao Ju et al. (SIGGRAPH 2002) | Dual Contouring of Hermite Data — the foundational algorithm |
| Boris the Brave (2018) | Practical tutorial with constrained QEF and bias improvements |
| Matt Keeter (2D Contouring) | Clean 2D implementation comparing Marching Squares and Dual Contouring |
| Pujol & Chica (C&G 2023) | Adaptive SDF approximation with polynomial interpolation for fast queries |
| Garland & Heckbert (SIGGRAPH 1997) | Surface Simplification Using Quadric Error Metrics |
| Michael Fogleman (sdf library) | Python SDF→mesh library using Marching Cubes with Skimage |
