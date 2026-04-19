"""SESSION-089 (P1-INDUSTRIAL-34C): Dead Cells-Style Orthographic 3D→2D
Pixel Render Engine — Pure NumPy Software Rasterizer.

Research Foundations
--------------------
1. **Motion Twin (Dead Cells) GDC 2018** — Thomas Vasseur:
   "Art Design Deep Dive: Using a 3D Pipeline for 2D Animation in Dead Cells"
   Core paradigm: high-poly 3D skeletal animation → orthographic camera →
   no-AA nearest-neighbor downsample → synchronous Albedo/Normal/Depth
   sequence export for 2D engine dynamic lighting.

2. **Arc System Works (Guilty Gear Xrd) GDC 2015** — Junya C. Motomura:
   "GuiltyGearXrd's Art Style: The X Factor Between 2D and 3D"
   Lighting must use hard stepped thresholds (Stepped Shading) to cut
   smooth transitions; support frame decimation to 12fps/15fps for
   pixel animation punch.

3. **Headless EGL / Software Rasterizer Architecture**:
   CI environments have no physical display.  This module uses a pure
   NumPy software rasterizer — zero dependency on GLFW, X11, EGL, or
   any windowed context.  Guaranteed silent headless execution.

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────────┐
    │  OrthographicPixelRenderEngine (Pure NumPy Software Rasterizer) │
    │                                                                  │
    │  1. build_orthographic_matrix()                                  │
    │     Pure orthographic projection — NO perspective FOV.           │
    │     ┌                        ┐                                   │
    │     │ 2/(r-l)  0       0   tx │                                  │
    │     │ 0        2/(t-b) 0   ty │                                  │
    │     │ 0        0      -2/d tz │                                  │
    │     │ 0        0       0    1 │                                  │
    │     └                        ┘                                   │
    │                                                                  │
    │  2. rasterize_triangles()                                        │
    │     Edge-function triangle rasterizer with Z-buffer.             │
    │     Barycentric interpolation for normals and UVs.               │
    │                                                                  │
    │  3. Multi-Pass Extraction:                                       │
    │     - Albedo  (base color per vertex/face)                       │
    │     - Normal  (interpolated world-space normals)                 │
    │     - Depth   (linear Z from orthographic projection)            │
    │                                                                  │
    │  4. Cel-Shading Kernel:                                          │
    │     - N·L dot product: max(dot(N, L), 0)                        │
    │     - Stepped threshold banding (2-3 discrete levels)            │
    │     - NO smooth gradients — hard pixel boundaries                │
    │                                                                  │
    │  5. Nearest-Neighbor Downscale:                                  │
    │     - PIL.Image.resize(NEAREST) — NEVER bilinear/bicubic        │
    │     - Assert: edge pixels alpha ∈ {0, 255}                       │
    └──────────────────────────────────────────────────────────────────┘

Anti-Pattern Traps (Enforced by Tests)
--------------------------------------
🚫 Perspective Distortion Trap: Matrix MUST be pure orthographic.
🚫 Bilinear Blur Trap: Downscale MUST use INTER_NEAREST only.
🚫 GUI Window Crash Trap: ZERO windowed context — pure NumPy.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OrthographicRenderConfig:
    """Configuration for the orthographic pixel render engine.

    Parameters
    ----------
    render_width : int
        Internal render resolution width (before downscale).
    render_height : int
        Internal render resolution height (before downscale).
    output_width : int
        Final output pixel art width (after nearest-neighbor downscale).
    output_height : int
        Final output pixel art height (after nearest-neighbor downscale).
    ortho_left, ortho_right, ortho_bottom, ortho_top : float
        Orthographic frustum bounds in world units.
    near_clip, far_clip : float
        Near/far clipping planes.
    light_direction : tuple[float, float, float]
        Normalized world-space light direction vector.
    cel_thresholds : tuple[float, ...]
        Hard step thresholds for cel-shading bands.
        Default (0.15, 0.55) → 3 bands: shadow / midtone / highlight.
    cel_colors_shadow : tuple[int, int, int]
        Shadow band color multiplier (0-255 per channel).
    cel_colors_midtone : tuple[int, int, int]
        Midtone band color multiplier.
    cel_colors_highlight : tuple[int, int, int]
        Highlight band color multiplier.
    background_color : tuple[int, int, int, int]
        RGBA background (default transparent).
    fps : int
        Target output frame rate (Dead Cells: 12-15fps).
    """
    render_width: int = 256
    render_height: int = 256
    output_width: int = 64
    output_height: int = 64
    ortho_left: float = -1.0
    ortho_right: float = 1.0
    ortho_bottom: float = -1.0
    ortho_top: float = 1.0
    near_clip: float = 0.1
    far_clip: float = 10.0
    light_direction: tuple[float, float, float] = (-0.577, 0.577, 0.577)
    cel_thresholds: tuple[float, ...] = (0.15, 0.55)
    cel_colors_shadow: tuple[int, int, int] = (60, 60, 80)
    cel_colors_midtone: tuple[int, int, int] = (140, 140, 160)
    cel_colors_highlight: tuple[int, int, int] = (220, 220, 240)
    background_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    fps: int = 12


# ═══════════════════════════════════════════════════════════════════════════
#  Data Structures
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Mesh3D:
    """A simple 3D triangle mesh for the software rasterizer.

    Attributes
    ----------
    vertices : np.ndarray
        (N, 3) float64 array of vertex positions.
    normals : np.ndarray
        (N, 3) float64 array of per-vertex normals.
    triangles : np.ndarray
        (M, 3) int array of triangle vertex indices.
    colors : np.ndarray
        (N, 3) uint8 array of per-vertex base colors (RGB).
    """
    vertices: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    colors: np.ndarray

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        self.normals = np.asarray(self.normals, dtype=np.float64)
        self.triangles = np.asarray(self.triangles, dtype=np.int32)
        self.colors = np.asarray(self.colors, dtype=np.uint8)

    @property
    def vertex_count(self) -> int:
        return self.vertices.shape[0]

    @property
    def triangle_count(self) -> int:
        return self.triangles.shape[0]


@dataclass
class MultiPassResult:
    """Result of a multi-pass orthographic render.

    All images are spatially aligned (same pixel grid).

    Attributes
    ----------
    albedo : np.ndarray
        (H, W, 4) uint8 RGBA albedo with cel-shading applied.
    normal : np.ndarray
        (H, W, 4) uint8 RGBA encoded normal map (R=X, G=Y, B=Z).
    depth : np.ndarray
        (H, W, 4) uint8 RGBA linear depth (grayscale, 0=far, 255=near).
    raw_depth : np.ndarray
        (H, W) float64 raw linear depth values (for downstream use).
    raw_normals : np.ndarray
        (H, W, 3) float64 raw world-space normal vectors.
    coverage_mask : np.ndarray
        (H, W) bool mask of pixels with geometry coverage.
    config : OrthographicRenderConfig
        The configuration used for this render.
    metadata : dict[str, Any]
        Render statistics and diagnostics.
    """
    albedo: np.ndarray
    normal: np.ndarray
    depth: np.ndarray
    raw_depth: np.ndarray
    raw_normals: np.ndarray
    coverage_mask: np.ndarray
    config: OrthographicRenderConfig
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PixelSpriteResult:
    """Final pixel art sprite result after nearest-neighbor downscale.

    Attributes
    ----------
    albedo_image : PIL.Image.Image
        Final pixel art albedo (RGBA).
    normal_image : PIL.Image.Image
        Final pixel art normal map (RGBA).
    depth_image : PIL.Image.Image
        Final pixel art depth map (RGBA).
    hi_res : MultiPassResult
        The high-resolution multi-pass result before downscale.
    metadata : dict[str, Any]
        Combined metadata from render + downscale.
    """
    albedo_image: Image.Image
    normal_image: Image.Image
    depth_image: Image.Image
    hi_res: MultiPassResult
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
#  Orthographic Projection Matrix
# ═══════════════════════════════════════════════════════════════════════════

def build_orthographic_matrix(
    left: float,
    right: float,
    bottom: float,
    top: float,
    near: float,
    far: float,
) -> np.ndarray:
    """Build a 4x4 orthographic projection matrix.

    This is the **absolute foundation** of the Dead Cells 3D→2D pipeline.
    The matrix maps world-space coordinates to normalized device coordinates
    (NDC) in [-1, 1]^3 with ZERO perspective distortion.

    The matrix form:
        ┌                                    ┐
        │ 2/(r-l)    0        0    -(r+l)/(r-l) │
        │ 0       2/(t-b)     0    -(t+b)/(t-b) │
        │ 0          0     -2/(f-n) -(f+n)/(f-n) │
        │ 0          0        0         1        │
        └                                    ┘

    Parameters
    ----------
    left, right : float
        Horizontal frustum bounds.
    bottom, top : float
        Vertical frustum bounds.
    near, far : float
        Depth clipping planes.

    Returns
    -------
    np.ndarray
        (4, 4) float64 orthographic projection matrix.

    Raises
    ------
    ValueError
        If frustum dimensions are degenerate (zero width/height/depth).
    """
    if abs(right - left) < 1e-12:
        raise ValueError("Orthographic frustum has zero width")
    if abs(top - bottom) < 1e-12:
        raise ValueError("Orthographic frustum has zero height")
    if abs(far - near) < 1e-12:
        raise ValueError("Orthographic frustum has zero depth")

    mat = np.zeros((4, 4), dtype=np.float64)
    mat[0, 0] = 2.0 / (right - left)
    mat[1, 1] = 2.0 / (top - bottom)
    mat[2, 2] = -2.0 / (far - near)
    mat[0, 3] = -(right + left) / (right - left)
    mat[1, 3] = -(top + bottom) / (top - bottom)
    mat[2, 3] = -(far + near) / (far - near)
    mat[3, 3] = 1.0
    return mat


def is_orthographic_matrix(mat: np.ndarray, tol: float = 1e-9) -> bool:
    """Verify that a 4x4 matrix is a pure orthographic projection.

    A pure orthographic matrix has:
    - mat[3, 0] == mat[3, 1] == mat[3, 2] == 0  (no perspective divide)
    - mat[3, 3] == 1  (homogeneous w = 1)

    This is the **anti-perspective-distortion guard** required by the
    Dead Cells pipeline spec.

    Parameters
    ----------
    mat : np.ndarray
        (4, 4) matrix to verify.
    tol : float
        Numerical tolerance for floating-point comparison.

    Returns
    -------
    bool
        True if the matrix is a valid orthographic projection.
    """
    if mat.shape != (4, 4):
        return False
    # Perspective row must be [0, 0, 0, 1]
    if abs(mat[3, 0]) > tol or abs(mat[3, 1]) > tol or abs(mat[3, 2]) > tol:
        return False
    if abs(mat[3, 3] - 1.0) > tol:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Software Rasterizer — Pure NumPy (Headless, CI-Safe)
# ═══════════════════════════════════════════════════════════════════════════

def _project_vertices(
    vertices: np.ndarray,
    proj_matrix: np.ndarray,
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Project 3D vertices to screen space using orthographic matrix.

    Returns
    -------
    screen_xy : np.ndarray
        (N, 2) float64 screen-space coordinates.
    ndc_z : np.ndarray
        (N,) float64 normalized depth values in [0, 1] (0=near, 1=far).
    """
    n = vertices.shape[0]
    # Homogeneous coordinates
    homo = np.ones((n, 4), dtype=np.float64)
    homo[:, :3] = vertices

    # Apply projection: clip = P @ v
    clip = (proj_matrix @ homo.T).T  # (N, 4)

    # Orthographic: w is always 1, no perspective divide needed
    ndc_x = clip[:, 0]  # [-1, 1]
    ndc_y = clip[:, 1]  # [-1, 1]
    ndc_z = clip[:, 2]  # [-1, 1]

    # NDC to screen space
    screen_x = (ndc_x + 1.0) * 0.5 * width
    screen_y = (1.0 - ndc_y) * 0.5 * height  # Flip Y for image coords

    screen_xy = np.column_stack([screen_x, screen_y])

    # Normalize depth to [0, 1] (0=near, 1=far)
    depth_01 = (ndc_z + 1.0) * 0.5

    return screen_xy, depth_01


def _edge_function(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
) -> np.ndarray:
    """Compute the edge function for triangle rasterization.

    edge(a, b, c) = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])

    Parameters
    ----------
    a, b : np.ndarray
        (2,) vertex positions.
    c : np.ndarray
        (M, 2) or (2,) sample positions.

    Returns
    -------
    np.ndarray
        Edge function values (positive = left side of edge a→b).
    """
    return (b[0] - a[0]) * (c[..., 1] - a[1]) - (b[1] - a[1]) * (c[..., 0] - a[0])


def rasterize_triangles(
    mesh: Mesh3D,
    config: OrthographicRenderConfig,
) -> MultiPassResult:
    """Rasterize a triangle mesh using pure NumPy software rasterization.

    This is the core headless renderer.  It produces spatially-aligned
    Albedo, Normal, and Depth buffers in a single pass using edge-function
    triangle traversal with Z-buffer depth testing.

    **Guaranteed headless**: No GLFW, X11, EGL, or any windowed context.
    Pure NumPy computation only.

    Parameters
    ----------
    mesh : Mesh3D
        The 3D mesh to rasterize.
    config : OrthographicRenderConfig
        Render configuration.

    Returns
    -------
    MultiPassResult
        Multi-channel render result with albedo, normal, depth buffers.
    """
    w = config.render_width
    h = config.render_height

    # Build orthographic projection matrix
    proj = build_orthographic_matrix(
        config.ortho_left, config.ortho_right,
        config.ortho_bottom, config.ortho_top,
        config.near_clip, config.far_clip,
    )

    # Verify matrix is pure orthographic (anti-perspective guard)
    assert is_orthographic_matrix(proj), (
        "FATAL: Projection matrix is NOT orthographic! "
        "Dead Cells pipeline requires pure orthographic projection."
    )

    # Project vertices to screen space
    screen_xy, depth_01 = _project_vertices(mesh.vertices, proj, w, h)

    # Initialize buffers
    albedo_buf = np.zeros((h, w, 4), dtype=np.uint8)
    albedo_buf[:, :] = config.background_color
    normal_buf = np.zeros((h, w, 3), dtype=np.float64)
    depth_buf = np.ones((h, w), dtype=np.float64)  # 1.0 = far (cleared)
    coverage = np.zeros((h, w), dtype=bool)

    # Pixel grid
    py, px = np.mgrid[0:h, 0:w]
    pixel_centers = np.stack([px + 0.5, py + 0.5], axis=-1)  # (H, W, 2)

    triangles_rendered = 0

    for tri_idx in range(mesh.triangle_count):
        i0, i1, i2 = mesh.triangles[tri_idx]

        v0 = screen_xy[i0]
        v1 = screen_xy[i1]
        v2 = screen_xy[i2]

        # Bounding box (clipped to screen)
        min_x = max(0, int(math.floor(min(v0[0], v1[0], v2[0]))))
        max_x = min(w - 1, int(math.ceil(max(v0[0], v1[0], v2[0]))))
        min_y = max(0, int(math.floor(min(v0[1], v1[1], v2[1]))))
        max_y = min(h - 1, int(math.ceil(max(v0[1], v1[1], v2[1]))))

        if min_x > max_x or min_y > max_y:
            continue

        # Extract tile of pixel centers
        tile = pixel_centers[min_y:max_y + 1, min_x:max_x + 1]  # (th, tw, 2)

        # Edge functions
        area = _edge_function(v0, v1, np.array([v2[0], v2[1]]))
        if abs(area) < 1e-10:
            continue  # Degenerate triangle

        w0 = _edge_function(v1, v2, tile)
        w1 = _edge_function(v2, v0, tile)
        w2 = _edge_function(v0, v1, tile)

        # Inside test (top-left rule approximation)
        if area > 0:
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        else:
            inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
            area = -area
            w0 = -w0
            w1 = -w1
            w2 = -w2

        if not np.any(inside):
            continue

        # Barycentric coordinates
        inv_area = 1.0 / area
        bary0 = w0 * inv_area
        bary1 = w1 * inv_area
        bary2 = w2 * inv_area

        # Interpolate depth
        z_interp = (
            bary0 * depth_01[i0]
            + bary1 * depth_01[i1]
            + bary2 * depth_01[i2]
        )

        # Depth test (closer = smaller z)
        depth_tile = depth_buf[min_y:max_y + 1, min_x:max_x + 1]
        closer = inside & (z_interp < depth_tile)

        if not np.any(closer):
            continue

        triangles_rendered += 1

        # Update depth buffer
        depth_tile[closer] = z_interp[closer]

        # Interpolate normals
        n0 = mesh.normals[i0]
        n1 = mesh.normals[i1]
        n2 = mesh.normals[i2]
        nx = bary0 * n0[0] + bary1 * n1[0] + bary2 * n2[0]
        ny = bary0 * n0[1] + bary1 * n1[1] + bary2 * n2[1]
        nz = bary0 * n0[2] + bary1 * n1[2] + bary2 * n2[2]

        # Normalize
        length = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2) + 1e-12
        nx /= length
        ny /= length
        nz /= length

        normal_tile = normal_buf[min_y:max_y + 1, min_x:max_x + 1]
        normal_tile[closer, 0] = nx[closer]
        normal_tile[closer, 1] = ny[closer]
        normal_tile[closer, 2] = nz[closer]

        # Interpolate base color
        c0 = mesh.colors[i0].astype(np.float64)
        c1 = mesh.colors[i1].astype(np.float64)
        c2 = mesh.colors[i2].astype(np.float64)
        cr = bary0 * c0[0] + bary1 * c1[0] + bary2 * c2[0]
        cg = bary0 * c0[1] + bary1 * c1[1] + bary2 * c2[1]
        cb = bary0 * c0[2] + bary1 * c1[2] + bary2 * c2[2]

        albedo_tile = albedo_buf[min_y:max_y + 1, min_x:max_x + 1]
        albedo_tile[closer, 0] = np.clip(cr[closer], 0, 255).astype(np.uint8)
        albedo_tile[closer, 1] = np.clip(cg[closer], 0, 255).astype(np.uint8)
        albedo_tile[closer, 2] = np.clip(cb[closer], 0, 255).astype(np.uint8)
        albedo_tile[closer, 3] = 255  # Full alpha for covered pixels

        # Update coverage
        coverage[min_y:max_y + 1, min_x:max_x + 1] |= closer

    # Store raw data before encoding
    raw_depth = 1.0 - depth_buf  # Invert: 1=near, 0=far
    raw_depth[~coverage] = 0.0

    metadata = {
        "render_width": w,
        "render_height": h,
        "triangles_total": mesh.triangle_count,
        "triangles_rendered": triangles_rendered,
        "coverage_pixels": int(np.count_nonzero(coverage)),
        "coverage_ratio": float(np.count_nonzero(coverage)) / max(1, w * h),
        "projection_type": "orthographic",
        "renderer": "numpy_software_rasterizer",
        "headless": True,
    }

    # Encode normal map: [-1,1] → [0,255]
    normal_encoded = np.zeros((h, w, 4), dtype=np.uint8)
    normal_encoded[coverage, 0] = np.clip(
        (normal_buf[coverage, 0] * 0.5 + 0.5) * 255, 0, 255
    ).astype(np.uint8)
    normal_encoded[coverage, 1] = np.clip(
        (normal_buf[coverage, 1] * 0.5 + 0.5) * 255, 0, 255
    ).astype(np.uint8)
    normal_encoded[coverage, 2] = np.clip(
        (normal_buf[coverage, 2] * 0.5 + 0.5) * 255, 0, 255
    ).astype(np.uint8)
    normal_encoded[coverage, 3] = 255

    # Encode depth map: [0,1] → [0,255] grayscale
    depth_encoded = np.zeros((h, w, 4), dtype=np.uint8)
    depth_gray = np.clip(raw_depth * 255, 0, 255).astype(np.uint8)
    depth_encoded[coverage, 0] = depth_gray[coverage]
    depth_encoded[coverage, 1] = depth_gray[coverage]
    depth_encoded[coverage, 2] = depth_gray[coverage]
    depth_encoded[coverage, 3] = 255

    return MultiPassResult(
        albedo=albedo_buf,
        normal=normal_encoded,
        depth=depth_encoded,
        raw_depth=raw_depth,
        raw_normals=normal_buf,
        coverage_mask=coverage,
        config=config,
        metadata=metadata,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Cel-Shading Kernel — Stepped Threshold (Guilty Gear Xrd Style)
# ═══════════════════════════════════════════════════════════════════════════

def apply_cel_shading(
    result: MultiPassResult,
    config: Optional[OrthographicRenderConfig] = None,
) -> MultiPassResult:
    """Apply hard-stepped cel-shading to the albedo buffer.

    Implements the Guilty Gear Xrd / Dead Cells lighting model:
    1. Compute N·L (dot product of normal and light direction).
    2. Apply hard step thresholds — NO smooth gradients.
    3. Map each pixel to one of 2-3 discrete light bands.

    The mathematical formulation:
        shade_level = Σ step(N·L - threshold_i)

    Where step() is the Heaviside step function (hard cutoff).

    Parameters
    ----------
    result : MultiPassResult
        The rasterized multi-pass result.
    config : OrthographicRenderConfig, optional
        Override configuration (defaults to result.config).

    Returns
    -------
    MultiPassResult
        Updated result with cel-shaded albedo.
    """
    cfg = config or result.config
    coverage = result.coverage_mask
    normals = result.raw_normals  # (H, W, 3)

    if not np.any(coverage):
        return result

    # Normalize light direction
    lx, ly, lz = cfg.light_direction
    l_len = math.sqrt(lx * lx + ly * ly + lz * lz) + 1e-12
    light_dir = np.array([lx / l_len, ly / l_len, lz / l_len])

    # Compute N·L for covered pixels
    # N·L = nx*lx + ny*ly + nz*lz
    ndotl = (
        normals[:, :, 0] * light_dir[0]
        + normals[:, :, 1] * light_dir[1]
        + normals[:, :, 2] * light_dir[2]
    )
    ndotl = np.clip(ndotl, 0.0, 1.0)  # max(N·L, 0)

    # Hard stepped thresholds (Guilty Gear Xrd: NO smooth ramp)
    thresholds = sorted(cfg.cel_thresholds)

    # Determine band for each pixel
    # band 0 = shadow, band 1 = midtone, band 2 = highlight, ...
    band = np.zeros_like(ndotl, dtype=np.int32)
    for thresh in thresholds:
        band += (ndotl > thresh).astype(np.int32)

    # Color lookup table for bands
    band_colors = [
        np.array(cfg.cel_colors_shadow, dtype=np.float64),
        np.array(cfg.cel_colors_midtone, dtype=np.float64),
        np.array(cfg.cel_colors_highlight, dtype=np.float64),
    ]
    # Extend if more thresholds
    while len(band_colors) <= len(thresholds):
        band_colors.append(band_colors[-1])

    # Apply cel-shading to albedo
    shaded = result.albedo.copy()
    base_colors = shaded[:, :, :3].astype(np.float64)

    for band_idx, band_color in enumerate(band_colors):
        mask = coverage & (band == band_idx)
        if not np.any(mask):
            continue
        # Modulate base color by band color (normalized to [0,1])
        factor = band_color / 255.0
        shaded[mask, 0] = np.clip(
            base_colors[mask, 0] * factor[0], 0, 255
        ).astype(np.uint8)
        shaded[mask, 1] = np.clip(
            base_colors[mask, 1] * factor[1], 0, 255
        ).astype(np.uint8)
        shaded[mask, 2] = np.clip(
            base_colors[mask, 2] * factor[2], 0, 255
        ).astype(np.uint8)

    return MultiPassResult(
        albedo=shaded,
        normal=result.normal,
        depth=result.depth,
        raw_depth=result.raw_depth,
        raw_normals=result.raw_normals,
        coverage_mask=result.coverage_mask,
        config=result.config,
        metadata={
            **result.metadata,
            "cel_shading_applied": True,
            "cel_thresholds": list(cfg.cel_thresholds),
            "cel_band_count": len(thresholds) + 1,
            "light_direction": list(cfg.light_direction),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Nearest-Neighbor Downscale (Dead Cells: NO Bilinear/Bicubic!)
# ═══════════════════════════════════════════════════════════════════════════

def nearest_neighbor_downscale(
    image: np.ndarray,
    target_width: int,
    target_height: int,
) -> Image.Image:
    """Downscale an image using STRICT nearest-neighbor interpolation.

    🚫 BILINEAR BLUR TRAP: This function MUST use PIL.Image.NEAREST.
    Using INTER_LINEAR, INTER_CUBIC, LANCZOS, or any other interpolation
    method would create sub-pixel color blending that destroys the hard
    pixel edges essential to Dead Cells-style pixel art.

    Parameters
    ----------
    image : np.ndarray
        (H, W, 4) uint8 RGBA image to downscale.
    target_width : int
        Target width in pixels.
    target_height : int
        Target height in pixels.

    Returns
    -------
    PIL.Image.Image
        Downscaled RGBA image with hard pixel edges.
    """
    pil_img = Image.fromarray(image, "RGBA")
    # CRITICAL: Image.NEAREST — the ONLY acceptable resampling for pixel art
    downscaled = pil_img.resize(
        (target_width, target_height),
        resample=Image.NEAREST,
    )
    return downscaled


def validate_hard_edges(image: Image.Image) -> tuple[bool, dict[str, Any]]:
    """Validate that a pixel art image has hard edges (no sub-pixel blending).

    Dead Cells pipeline requirement: Alpha channel must be binary (0 or 255).
    Any intermediate alpha values indicate bilinear/bicubic contamination.

    Parameters
    ----------
    image : PIL.Image.Image
        RGBA image to validate.

    Returns
    -------
    tuple[bool, dict]
        (is_valid, diagnostics)
    """
    arr = np.array(image)
    if arr.ndim < 3 or arr.shape[2] < 4:
        return False, {"error": "Image is not RGBA"}

    alpha = arr[:, :, 3]
    unique_alpha = np.unique(alpha)

    # All alpha values must be either 0 or 255
    is_binary = np.all((alpha == 0) | (alpha == 255))

    diagnostics = {
        "unique_alpha_values": unique_alpha.tolist(),
        "alpha_is_binary": bool(is_binary),
        "total_pixels": int(alpha.size),
        "opaque_pixels": int(np.count_nonzero(alpha == 255)),
        "transparent_pixels": int(np.count_nonzero(alpha == 0)),
        "contaminated_pixels": int(
            np.count_nonzero((alpha > 0) & (alpha < 255))
        ),
    }

    return bool(is_binary), diagnostics


# ═══════════════════════════════════════════════════════════════════════════
#  Full Pipeline: Render → Cel-Shade → Downscale
# ═══════════════════════════════════════════════════════════════════════════

def render_orthographic_sprite(
    mesh: Mesh3D,
    config: Optional[OrthographicRenderConfig] = None,
) -> PixelSpriteResult:
    """Full Dead Cells-style 3D→2D pixel art rendering pipeline.

    Pipeline stages:
    1. Rasterize mesh at high resolution using orthographic projection.
    2. Apply hard-stepped cel-shading (Guilty Gear Xrd style).
    3. Downscale all channels using nearest-neighbor interpolation.
    4. Validate hard edges (no bilinear contamination).

    Parameters
    ----------
    mesh : Mesh3D
        The 3D mesh to render.
    config : OrthographicRenderConfig, optional
        Render configuration.

    Returns
    -------
    PixelSpriteResult
        Final pixel art sprite with all channels.
    """
    cfg = config or OrthographicRenderConfig()

    # Stage 1: Rasterize at high resolution
    hi_res = rasterize_triangles(mesh, cfg)

    # Stage 2: Apply cel-shading
    hi_res = apply_cel_shading(hi_res, cfg)

    # Stage 3: Nearest-neighbor downscale all channels
    albedo_img = nearest_neighbor_downscale(
        hi_res.albedo, cfg.output_width, cfg.output_height,
    )
    normal_img = nearest_neighbor_downscale(
        hi_res.normal, cfg.output_width, cfg.output_height,
    )
    depth_img = nearest_neighbor_downscale(
        hi_res.depth, cfg.output_width, cfg.output_height,
    )

    # Stage 4: Validate hard edges
    albedo_valid, albedo_diag = validate_hard_edges(albedo_img)
    normal_valid, normal_diag = validate_hard_edges(normal_img)
    depth_valid, depth_diag = validate_hard_edges(depth_img)

    metadata = {
        **hi_res.metadata,
        "output_width": cfg.output_width,
        "output_height": cfg.output_height,
        "downscale_method": "nearest_neighbor",
        "albedo_hard_edges": albedo_valid,
        "normal_hard_edges": normal_valid,
        "depth_hard_edges": depth_valid,
        "albedo_diagnostics": albedo_diag,
        "fps": cfg.fps,
        "pipeline": "dead_cells_orthographic_pixel_render",
    }

    return PixelSpriteResult(
        albedo_image=albedo_img,
        normal_image=normal_img,
        depth_image=depth_img,
        hi_res=hi_res,
        metadata=metadata,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Mesh Generation Helpers (for testing and demo)
# ═══════════════════════════════════════════════════════════════════════════

def create_unit_cube_mesh(
    color: tuple[int, int, int] = (180, 120, 80),
) -> Mesh3D:
    """Create a unit cube mesh centered at origin for testing.

    The cube spans [-0.5, 0.5] in all axes.
    """
    vertices = np.array([
        [-0.5, -0.5, -0.5], [0.5, -0.5, -0.5],
        [0.5,  0.5, -0.5], [-0.5,  0.5, -0.5],
        [-0.5, -0.5,  0.5], [0.5, -0.5,  0.5],
        [0.5,  0.5,  0.5], [-0.5,  0.5,  0.5],
    ], dtype=np.float64)

    # Face normals (one per vertex, approximated)
    normals = np.array([
        [-0.577, -0.577, -0.577], [0.577, -0.577, -0.577],
        [0.577,  0.577, -0.577], [-0.577,  0.577, -0.577],
        [-0.577, -0.577,  0.577], [0.577, -0.577,  0.577],
        [0.577,  0.577,  0.577], [-0.577,  0.577,  0.577],
    ], dtype=np.float64)
    # Normalize
    norms = np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
    normals = normals / norms

    triangles = np.array([
        # Front face
        [4, 5, 6], [4, 6, 7],
        # Back face
        [1, 0, 3], [1, 3, 2],
        # Top face
        [3, 7, 6], [3, 6, 2],
        # Bottom face
        [0, 1, 5], [0, 5, 4],
        # Right face
        [1, 2, 6], [1, 6, 5],
        # Left face
        [0, 4, 7], [0, 7, 3],
    ], dtype=np.int32)

    colors = np.full((8, 3), color, dtype=np.uint8)

    return Mesh3D(
        vertices=vertices,
        normals=normals,
        triangles=triangles,
        colors=colors,
    )


def create_sphere_mesh(
    radius: float = 0.5,
    rings: int = 12,
    sectors: int = 16,
    color: tuple[int, int, int] = (200, 100, 80),
) -> Mesh3D:
    """Create a UV sphere mesh centered at origin.

    Useful for testing cel-shading (smooth normals show band transitions).
    """
    vertices = []
    normals = []

    for r in range(rings + 1):
        phi = math.pi * r / rings
        for s in range(sectors + 1):
            theta = 2.0 * math.pi * s / sectors
            x = radius * math.sin(phi) * math.cos(theta)
            y = radius * math.cos(phi)
            z = radius * math.sin(phi) * math.sin(theta)
            vertices.append([x, y, z])
            # Normal = normalized position for a centered sphere
            length = math.sqrt(x * x + y * y + z * z) + 1e-12
            normals.append([x / length, y / length, z / length])

    triangles = []
    for r in range(rings):
        for s in range(sectors):
            i0 = r * (sectors + 1) + s
            i1 = i0 + 1
            i2 = i0 + (sectors + 1)
            i3 = i2 + 1
            triangles.append([i0, i2, i1])
            triangles.append([i1, i2, i3])

    verts = np.array(vertices, dtype=np.float64)
    norms = np.array(normals, dtype=np.float64)
    tris = np.array(triangles, dtype=np.int32)
    cols = np.full((len(vertices), 3), color, dtype=np.uint8)

    return Mesh3D(vertices=verts, normals=norms, triangles=tris, colors=cols)


# ═══════════════════════════════════════════════════════════════════════════
#  File I/O
# ═══════════════════════════════════════════════════════════════════════════

def save_sprite_result(
    result: PixelSpriteResult,
    output_dir: Path,
    stem: str = "sprite",
) -> dict[str, str]:
    """Save all channels of a PixelSpriteResult to disk.

    Returns a dict mapping channel name → file path.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}

    albedo_path = output_dir / f"{stem}_albedo.png"
    result.albedo_image.save(str(albedo_path))
    paths["albedo"] = str(albedo_path)

    normal_path = output_dir / f"{stem}_normal.png"
    result.normal_image.save(str(normal_path))
    paths["normal"] = str(normal_path)

    depth_path = output_dir / f"{stem}_depth.png"
    result.depth_image.save(str(depth_path))
    paths["depth"] = str(depth_path)

    return paths


__all__ = [
    "OrthographicRenderConfig",
    "Mesh3D",
    "MultiPassResult",
    "PixelSpriteResult",
    "build_orthographic_matrix",
    "is_orthographic_matrix",
    "rasterize_triangles",
    "apply_cel_shading",
    "nearest_neighbor_downscale",
    "validate_hard_edges",
    "render_orthographic_sprite",
    "create_unit_cube_mesh",
    "create_sphere_mesh",
    "save_sprite_result",
]
