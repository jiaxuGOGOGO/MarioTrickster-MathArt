# SESSION-119 Research Notes — Tensorized Gray-Scott Reaction-Diffusion & PBR Derivation

## Task Anchor

**P1-NEW-2 — Reaction-diffusion textures (Gray-Scott)**.  Target: convert the
raw Gray-Scott PDE system into a fully-tensorized NumPy/SciPy solver plugged
into the project's Registry Pattern, with PBR-style normal/albedo derivation,
and closed-loop white-box tests.  Downstream: provide an `advection_field`
interface so `P1-RESEARCH-30C` (Reaction-Diffusion & Thermodynamics) can
later inject a velocity field for advective rust/fire/poison VFX.

## 1. Karl Sims' Gray-Scott Reference (https://www.karlsims.com/rd.html)

Canonical numerical anchors recorded verbatim from Karl Sims:

> Some typical values used, for those interested, are: DA=1.0, DB=.5, f=.055,
> k=.062 (f and k vary for different patterns), and Δt=1.0.  The Laplacian is
> performed with a 3x3 convolution with center weight -1, adjacent neighbors
> .2, and diagonals .05.  The grid is initialized with A=1, B=0, and a small
> area is seeded with B=1.

Named patterns:

| Pattern | f | k |
|---|---|---|
| Mitosis (Karl Sims video) | 0.0367 | 0.0649 |
| Coral growth (Karl Sims video) | 0.0545 | 0.0620 |

Note: In the ReactionDiffusionBackend we model `U` (≡ Karl's `A`) as the
substrate and `V` (≡ Karl's `B`) as the autocatalyst.  The PDE pair is

    ∂U/∂t = D_u ∇²U − U V²   + f (1 − U)
    ∂V/∂t = D_v ∇²V + U V²   − (f + k) V

The Laplacian kernel encoded as a 3×3 stencil:

    [[0.05, 0.20, 0.05],
     [0.20, −1.00, 0.20],
     [0.05, 0.20, 0.05]]

Center weight is −1 (not −1/h²) because Karl's tutorial uses a unit grid and
bakes the step size into Δt.

## 2. Pearson's Extended Classification (mrob.com/pub/comp/xmorphia/)

Pearson's letter classes give us a well-known, peer-reviewed coordinate grid
for picking (f, k) pairs that yield qualitatively distinct topologies.

| Class | (f, k) anchors | Phenomenology |
|---|---|---|
| α | (0.010, 0.047), (0.014, 0.053) | Turbulent mixing, "plasma" |
| β | (0.014, 0.039), (0.026, 0.051) | Chaotic negatons |
| γ | (0.022, 0.051), (0.026, 0.055) | Stripes with defects |
| δ | (0.030, 0.055), (0.042, 0.059) | "Negatons" / hexagonal dots |
| ε | (0.018, 0.055), (0.022, 0.059) | Rings / unstable negatons |
| ζ | (0.022, 0.061), (0.026, 0.059) | Mitosis (splitting spots) |
| η | (0.034, 0.063)                 | Worms |
| θ | (0.030, 0.057), (0.038, 0.061) | Stripes / worms / negatives |
| κ | (0.050, 0.063), (0.058, 0.063) | Classic SPOTS / solitons |
| λ | (0.026, 0.061), (0.034, 0.065) | Maze / labyrinth |
| μ | (0.046, 0.065), (0.058, 0.065) | Holes / maze |
| σ | (0.090, 0.057), (0.110, 0.0523)| Ripple / rho-sigma band |

## 3. Selected Presets for ReactionDiffusionBackend

Locked-down (f, k) presets aligned with the task brief (≥4 hardcore presets):

| Preset | f | k | Class | Art direction |
|---|---|---|---|---|
| `CORAL`      | 0.0545 | 0.0620 | θ/κ transition | 3D embossed coral from Karl Sims video. |
| `MITOSIS`    | 0.0367 | 0.0649 | ζ            | Dividing spots, organic cell growth. |
| `MAZE`       | 0.0290 | 0.0570 | λ            | Stable labyrinth, Turing stripes. |
| `SPOTS`      | 0.0500 | 0.0620 | κ            | Hexagonal alien-skin dots. |
| `ALIEN_SKIN` | 0.0780 | 0.0610 | μ/ν border   | Active holes, breathing skin. |
| `FLOW`       | 0.0820 | 0.0600 | ν            | Noisy rolling fronts, for Advection coupling. |

## 4. Tensorized Laplacian via SciPy

`scipy.ndimage.convolve(field, KERNEL, mode="wrap")` (or
`scipy.signal.convolve2d(..., boundary='wrap')`) performs the 3×3 stencil in
one vectorized C call with periodic boundary conditions.  Key invariants:

1. **No Python per-pixel `for` loops** — every step operates on entire arrays.
2. **`mode='wrap'`** — this is the crucial anti-boundary-artifact guard, it
   makes the output texture seamless-tileable.  Any other mode ('constant',
   'reflect', 'nearest') would inject visible seams at the 3D material edge.
3. **`np.clip(u, 0, 1)` / `np.clip(v, 0, 1)`** after each step as the
   CFL / divergence safety net.

Stability criterion (Forward-Euler explicit): `D * Δt ≤ 0.5` for the 3×3
stencil.  With `D_u = 1.0` the largest safe Δt is 0.5; we pin Δt ≤ 1.0 only
when the effective diffusion term is scaled down by the kernel centre
weight (Karl's kernel is already scaled).  We adopt Δt = 1.0 matching Karl
Sims, and document the CFL budget in the solver.

## 5. PBR Derivation from V Concentration Field

The V field is treated as a **height map** h(x, y) ∈ [0, 1]:

1. **Sobel partial derivatives** via two 3×3 convolutions (`mode='wrap'`):

        ∂h/∂x ≈ convolve(h, [[-1,0,1],[-2,0,2],[-1,0,1]] / 8, mode='wrap')
        ∂h/∂y ≈ convolve(h, [[-1,-2,-1],[0,0,0],[1,2,1]] / 8, mode='wrap')

2. **Tangent-space normal vector** for every pixel:

        N = normalize( [-strength·∂h/∂x, -strength·∂h/∂y, 1] )

3. **Encoding to RGB normal map**:

        RGB = 0.5 * (N + 1)   (clamped to [0, 1] then ×255)

4. **Albedo / palette** — linear interpolation along (V, |∇V|) between a
   low-density colour (substrate) and a high-density colour (precipitate).

5. **Mask** — simple threshold on V.

All operations are pure NumPy array math, no per-pixel loops.

## 6. Advection Interface for P1-RESEARCH-30C Handoff

A future Navier-Stokes / thermodynamics coupling will inject a velocity
field `w(x, y) = (w_x, w_y)` into the PDE.  The extended system becomes:

    ∂U/∂t = D_u ∇²U − (w · ∇U) − UV² + f(1−U)
    ∂V/∂t = D_v ∇²V − (w · ∇V) + UV² − (f+k)V

Planned contract additions on `GrayScottSolverConfig`:

- `advection_field: np.ndarray | None` — shape `(2, H, W)`, channel-0 = w_x,
  channel-1 = w_y.  When present, each step subtracts the upwind gradient.
- `advection_scheme: str = "semi_lagrangian"` — defaults to a stable
  semi-Lagrangian back-trace; other accepted values are `"upwind"` and
  `"none"`.  We leave the actual advection implementation for SESSION-120
  but expose a plugin hook `solver.set_advection(w)` now so downstream
  integrators can already wire velocity fields in without trunk edits.

## 7. Compliance Checklist Against Red Lines

| Red line | How SESSION-119 upholds it |
|---|---|
| O(N²) scalar loop core melt-down | Every step uses `scipy.ndimage.convolve` with `mode='wrap'`. |
| CFL breakage / NaN explosion | Δt hard-clamped to the largest stable value; `np.clip(u, 0, 1)` and `np.clip(v, 0, 1)` invariants each step; `np.isfinite` guard in tests. |
| Boundary truncation artifact | `mode='wrap'` for both Laplacian and Sobel kernels → seamless tiles. |
| No trunk modification | New code lives under `mathart/texture/`; plugin self-registers via `@register_backend`. |
| Strong-type manifest | Backend returns `ArtifactManifest(artifact_family=MATERIAL_BUNDLE)` with `texture_channels` payload. |
| Plugin discoverability | `ReactionDiffusionBackend` auto-loads from `get_registry()` and is listed in `tests/conftest.py` builtin modules. |
