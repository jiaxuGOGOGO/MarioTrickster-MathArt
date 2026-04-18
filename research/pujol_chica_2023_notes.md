# Pujol & Chica 2023 — Adaptive Approximation of SDF through Piecewise Continuous Interpolation

**Paper**: Eduard Pujol, Antonio Chica. "Adaptive approximation of signed distance fields through piecewise continuous interpolation." Computers & Graphics 114 (2023) 337–346.
**DOI**: https://doi.org/10.1016/j.cag.2023.06.020
**GitHub**: https://github.com/UPC-ViRVIG/SdfLib

## Key Contributions

1. **Adaptive octree data structure** — represents SDF field using polynomial approximations per leaf node, built top-down with user-specified target error threshold
2. **Trilinear and tricubic interpolants** — polynomial form g(x,y,z) = Σ a_ijk * x^i * y^j * z^k (n=1 for trilinear, n=3 for tricubic)
3. **RMSE-based error estimation** — uses quadratures to estimate polynomial approximation error, guides adaptive subdivision
4. **C⁰ continuity guaranteed** for both trilinear and tricubic; **C¹ continuity** achieved for tricubic between nodes of different levels
5. **Minimized field evaluations** — reuses queries between neighbor nodes during octree construction

## Core Algorithm

### Polynomial Representation
- Each octree leaf stores polynomial coefficients a_ijk
- Trilinear: 8 coefficients (2³), uses corner SDF values
- Tricubic: 64 coefficients (4³), uses corner SDF values + gradients + cross-derivatives
- Polynomial: g(x,y,z) = Σ_{i,j,k=0}^{n} a_ijk * x^i * y^j * z^k

### Adaptive Subdivision
- Start at user-specified depth
- For each node, compute interpolant and estimate RMSE via quadrature
- If RMSE > target_error → subdivide
- Continue until max depth or error threshold met

### Continuity Enforcement
- C⁰: guaranteed by trilinear/tricubic formulation (shared corner values)
- C¹: for tricubic, force derivative matching at shared faces between nodes of different levels
- Cross-derivatives ∂²f/∂xy, ∂²f/∂xz, ∂²f/∂yz, ∂³f/∂xyz set to 0 at boundary constraints

## Applications
- Direct rendering via sphere marching on the approximated field
- Faster than evaluating complex analytical SDF at each step
- Useful for converting procedural SDF to queryable cached representation

## Relevance to MarioTrickster-MathArt
- **Direct application**: Cache the project's 2D/3D SDF fields into adaptive octree for fast GPU queries
- **Mesh extraction bridge**: The cached polynomial SDF can feed directly into Dual Contouring (Hermite data = SDF values + gradients already stored)
- **LOD system**: Different octree depths = different LOD levels for the same SDF
- **Integration point**: `smooth_morphology.py` SDF evaluation → Pujol-Chica adaptive cache → Dual Contouring mesh extraction
