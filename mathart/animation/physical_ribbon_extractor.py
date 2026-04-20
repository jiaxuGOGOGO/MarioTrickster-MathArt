"""SESSION-106 (P1-B1-1): PhysicalRibbonExtractor — Physical Ribbon Mesh
Generator for Secondary Animation Chains.

Research Foundations
--------------------
1. **Unreal Engine 5 Niagara Ribbon Data Interface**:
   Discrete 1D particle positions → Tangent/Binormal orthogonal basis →
   width extrusion → continuous triangle-strip mesh with real geometry
   (Vertices, Normals, UVs, Indices).  The facing vector is fixed to the
   camera-forward axis for orthographic 2.5D rendering.

2. **Catmull-Rom Spline Interpolation** (Catmull & Rom, 1974):
   C1-continuous interpolation through control points.  Parametric form:
       q(t) = 0.5 * [(2P₁) + (-P₀+P₂)t + (2P₀-5P₁+4P₂-P₃)t²
                      + (-P₀+3P₁-3P₂+P₃)t³]
   Provides smooth curve reconstruction from coarse Jakobsen/XPBD chain
   snapshots without overshooting or C0 discontinuities.

3. **Guilty Gear Xrd (GDC 2015)** — Vertex Normal Editing:
   Inject proxy-shape-derived normals for predictable cel-shading response.
   For ribbon geometry, use capsule-aligned normals so that N·L dot product
   produces clean, artist-friendly stepped lighting bands.

4. **Frenet-Serret Frame** with fixed up-vector stabilization:
   For 2D→2.5D chains, the classical Frenet frame can twist unpredictably.
   We use a "fixed up-vector" variant (camera-forward = +Z) to construct
   a stable Tangent-Binormal-Normal (TBN) frame at each sample point.

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────────┐
    │  PhysicalRibbonExtractor                                        │
    │                                                                  │
    │  Input:  SecondaryChainSnapshot.points  (list[(x, y)])          │
    │          RibbonExtractorConfig  (width, subdivisions, z_depth)  │
    │                                                                  │
    │  Stage 1: Catmull-Rom Interpolation                             │
    │     Upsample coarse chain points to smooth curve.               │
    │                                                                  │
    │  Stage 2: Tangent-Binormal Frame Construction                   │
    │     T = normalize(P[i+1] - P[i-1])  (central difference)       │
    │     B = cross(T, facing_vector)                                 │
    │     N = cross(B, T)                                             │
    │                                                                  │
    │  Stage 3: Width Extrusion                                       │
    │     left[i]  = P[i] + B[i] * halfWidth                         │
    │     right[i] = P[i] - B[i] * halfWidth                         │
    │                                                                  │
    │  Stage 4: Triangle Strip Indexing                               │
    │     For each segment i:                                         │
    │       tri1 = (left[i], right[i], left[i+1])                    │
    │       tri2 = (right[i], right[i+1], left[i+1])                 │
    │                                                                  │
    │  Stage 5: Normal Injection (GGXrd-style)                        │
    │     Per-vertex normals from capsule proxy or direct computation  │
    │                                                                  │
    │  Stage 6: UV Generation                                         │
    │     U = [0, 1] across width                                     │
    │     V = accumulated_arc_length / total_arc_length               │
    │                                                                  │
    │  Output:  Mesh3D  (vertices, normals, triangles, colors)        │
    │           RibbonMeshMetadata  (diagnostic telemetry)            │
    └──────────────────────────────────────────────────────────────────┘

Anti-Pattern Guards
-------------------
🚫 Anti-Debug-Line Rendering: This module generates REAL 3D polygon geometry
   with vertices, faces, normals, and depth — NOT cv2.line() or matplotlib
   debug lines.  The output Mesh3D participates in Z-buffer depth testing
   through the orthographic software rasterizer.

🚫 Anti-Spaghetti Attachment: This module is a standalone extractor.  It does
   NOT modify any trunk code (AssetPipeline, Orchestrator, or the base
   character renderer).  It produces a Mesh3D that is composed with the base
   character through the CompositeManifestBuilder data contract.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RibbonExtractorConfig:
    """Configuration for the physical ribbon mesh extractor.

    Parameters
    ----------
    width : float
        Ribbon width in world units.  The ribbon is extruded ±width/2
        perpendicular to the chain tangent.
    subdivisions_per_segment : int
        Number of Catmull-Rom interpolation subdivisions between each
        pair of original chain control points.  Higher values produce
        smoother curves at the cost of more vertices.
    z_depth_base : float
        Base Z-depth for the ribbon in 2.5D space.  Controls the
        front-to-back layering relative to the character body.
    z_depth_range : float
        Z-depth variation along the ribbon length.  The tip of the
        ribbon is offset by this amount from the base, creating a
        subtle depth gradient for parallax and sorting.
    facing_vector : tuple[float, float, float]
        The fixed facing vector for orthographic 2.5D rendering.
        Default is +Z (camera-forward in orthographic projection).
    color : tuple[int, int, int]
        Base RGB color for the ribbon vertices (0-255 per channel).
    normal_smoothing : float
        Blending weight between geometric normals and capsule-proxy
        normals.  0.0 = pure geometric, 1.0 = pure proxy.
        Guilty Gear Xrd uses ~0.7 for hair/cloth.
    width_taper : float
        Width taper factor from root to tip.  1.0 = uniform width,
        0.0 = tip collapses to zero width.  Default 0.6 creates a
        natural cape/hair taper.
    min_segment_length : float
        Minimum distance between consecutive points to avoid
        degenerate triangles.  Points closer than this are merged.
    """
    width: float = 0.12
    subdivisions_per_segment: int = 4
    z_depth_base: float = -0.15
    z_depth_range: float = 0.05
    facing_vector: tuple[float, float, float] = (0.0, 0.0, 1.0)
    color: tuple[int, int, int] = (120, 60, 160)
    normal_smoothing: float = 0.7
    width_taper: float = 0.6
    min_segment_length: float = 1e-4
    double_sided: bool = True


# ═══════════════════════════════════════════════════════════════════════════
#  Ribbon Mesh Metadata
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RibbonMeshMetadata:
    """Diagnostic telemetry for a generated ribbon mesh.

    Attributes
    ----------
    chain_name : str
        Name of the source secondary chain (e.g., "cape", "hair").
    input_point_count : int
        Number of raw chain control points before interpolation.
    interpolated_point_count : int
        Number of points after Catmull-Rom subdivision.
    vertex_count : int
        Final vertex count in the output Mesh3D.
    triangle_count : int
        Final triangle count in the output Mesh3D.
    total_arc_length : float
        Total arc length of the interpolated curve.
    bounding_box : dict[str, float]
        Axis-aligned bounding box of the mesh.
    width : float
        Configured ribbon width.
    z_depth_range : tuple[float, float]
        (min_z, max_z) of the generated vertices.
    degenerate_segments_removed : int
        Number of degenerate (too-short) segments removed.
    """
    chain_name: str = ""
    input_point_count: int = 0
    interpolated_point_count: int = 0
    vertex_count: int = 0
    triangle_count: int = 0
    total_arc_length: float = 0.0
    bounding_box: dict[str, float] = field(default_factory=dict)
    width: float = 0.0
    z_depth_range: tuple[float, float] = (0.0, 0.0)
    degenerate_segments_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_name": self.chain_name,
            "input_point_count": self.input_point_count,
            "interpolated_point_count": self.interpolated_point_count,
            "vertex_count": self.vertex_count,
            "triangle_count": self.triangle_count,
            "total_arc_length": self.total_arc_length,
            "bounding_box": dict(self.bounding_box),
            "width": self.width,
            "z_depth_range": list(self.z_depth_range),
            "degenerate_segments_removed": self.degenerate_segments_removed,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Catmull-Rom Spline Interpolation
# ═══════════════════════════════════════════════════════════════════════════

def catmull_rom_interpolate(
    points: np.ndarray,
    subdivisions: int = 4,
    *,
    alpha: float = 0.5,
) -> np.ndarray:
    """Interpolate a sequence of 2D/3D points using Catmull-Rom splines.

    Uses the centripetal parameterization (alpha=0.5) by default, which
    avoids cusps and self-intersections that plague uniform (alpha=0.0)
    and chordal (alpha=1.0) variants.

    Parameters
    ----------
    points : np.ndarray
        (N, D) array of control points (D=2 or D=3).
    subdivisions : int
        Number of interpolated samples between each pair of control points.
    alpha : float
        Parameterization exponent.  0.0=uniform, 0.5=centripetal, 1.0=chordal.

    Returns
    -------
    np.ndarray
        (M, D) array of interpolated points where M ≈ (N-1) * subdivisions + 1.
    """
    n = len(points)
    if n < 2:
        return points.copy()

    if n == 2:
        # Linear interpolation for 2-point chains
        result = []
        for i in range(subdivisions + 1):
            t = i / max(subdivisions, 1)
            result.append(points[0] * (1.0 - t) + points[1] * t)
        return np.array(result, dtype=np.float64)

    result = []

    for seg in range(n - 1):
        # Get four control points with boundary clamping
        p0 = points[max(seg - 1, 0)]
        p1 = points[seg]
        p2 = points[min(seg + 1, n - 1)]
        p3 = points[min(seg + 2, n - 1)]

        # Compute knot intervals using centripetal parameterization
        def _knot_interval(pa: np.ndarray, pb: np.ndarray) -> float:
            d = float(np.linalg.norm(pb - pa))
            return max(d ** alpha, _EPS)

        t0 = 0.0
        t1 = t0 + _knot_interval(p0, p1)
        t2 = t1 + _knot_interval(p1, p2)
        t3 = t2 + _knot_interval(p2, p3)

        num_samples = subdivisions if seg < n - 2 else subdivisions + 1
        for i in range(num_samples):
            t = t1 + (t2 - t1) * i / max(subdivisions, 1)

            # Barry and Goldman's pyramidal formulation
            def _safe_div(num: np.ndarray, denom: float) -> np.ndarray:
                return num / max(abs(denom), _EPS)

            a1 = _safe_div(p0 * (t1 - t) + p1 * (t - t0), t1 - t0)
            a2 = _safe_div(p1 * (t2 - t) + p2 * (t - t1), t2 - t1)
            a3 = _safe_div(p2 * (t3 - t) + p3 * (t - t2), t3 - t2)

            b1 = _safe_div(a1 * (t2 - t) + a2 * (t - t0), t2 - t0)
            b2 = _safe_div(a2 * (t3 - t) + a3 * (t - t1), t3 - t1)

            c = _safe_div(b1 * (t2 - t) + b2 * (t - t1), t2 - t1)
            result.append(c)

    return np.array(result, dtype=np.float64)


def _remove_degenerate_points(
    points: np.ndarray,
    min_dist: float = 1e-4,
) -> tuple[np.ndarray, int]:
    """Remove consecutive points that are too close together.

    Returns
    -------
    tuple[np.ndarray, int]
        (cleaned_points, removed_count)
    """
    if len(points) < 2:
        return points.copy(), 0

    keep = [0]
    removed = 0
    for i in range(1, len(points)):
        dist = float(np.linalg.norm(points[i] - points[keep[-1]]))
        if dist >= min_dist:
            keep.append(i)
        else:
            removed += 1

    return points[keep], removed


# ═══════════════════════════════════════════════════════════════════════════
#  Tangent-Binormal Frame Construction (UE5 Ribbon Style)
# ═══════════════════════════════════════════════════════════════════════════

def compute_tangent_frames(
    points: np.ndarray,
    facing_vector: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute per-point Tangent-Binormal-Normal frames.

    Uses the UE5 Niagara Ribbon approach:
    - Tangent T = central difference of adjacent points
    - Binormal B = cross(T, facing_vector)
    - Normal N = cross(B, T)

    For 2.5D rendering with a fixed camera, the facing vector is typically
    +Z (into the screen), producing a stable frame without Frenet twisting.

    Parameters
    ----------
    points : np.ndarray
        (N, 3) array of 3D sample points along the ribbon centerline.
    facing_vector : np.ndarray
        (3,) normalized facing direction (typically [0, 0, 1]).

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray]
        (tangents, binormals, normals) — each (N, 3) float64.
    """
    n = len(points)
    tangents = np.zeros((n, 3), dtype=np.float64)
    binormals = np.zeros((n, 3), dtype=np.float64)
    normals = np.zeros((n, 3), dtype=np.float64)

    facing = facing_vector / (np.linalg.norm(facing_vector) + _EPS)

    for i in range(n):
        # Central difference tangent
        if i == 0:
            t = points[min(1, n - 1)] - points[0]
        elif i == n - 1:
            t = points[n - 1] - points[max(n - 2, 0)]
        else:
            t = points[i + 1] - points[i - 1]

        t_len = np.linalg.norm(t)
        if t_len < _EPS:
            # Fallback: use previous tangent or default
            if i > 0:
                t = tangents[i - 1]
            else:
                t = np.array([0.0, -1.0, 0.0])
        else:
            t = t / t_len

        # Binormal = cross(tangent, facing)
        b = np.cross(t, facing)
        b_len = np.linalg.norm(b)
        if b_len < _EPS:
            # Tangent is parallel to facing — use arbitrary perpendicular
            perp = np.array([1.0, 0.0, 0.0])
            if abs(np.dot(t, perp)) > 0.9:
                perp = np.array([0.0, 1.0, 0.0])
            b = np.cross(t, perp)
            b_len = np.linalg.norm(b)
        b = b / max(b_len, _EPS)

        # Normal = cross(binormal, tangent)
        normal = np.cross(b, t)
        n_len = np.linalg.norm(normal)
        if n_len > _EPS:
            normal = normal / n_len

        tangents[i] = t
        binormals[i] = b
        normals[i] = normal

    return tangents, binormals, normals


# ═══════════════════════════════════════════════════════════════════════════
#  Width Extrusion & UV Generation
# ═══════════════════════════════════════════════════════════════════════════

def extrude_ribbon_mesh(
    points: np.ndarray,
    tangents: np.ndarray,
    binormals: np.ndarray,
    normals: np.ndarray,
    config: RibbonExtractorConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Extrude ribbon geometry from centerline points and TBN frames.

    Creates a triangle strip with left/right vertex pairs at each sample
    point, with proper UV coordinates and per-vertex normals.

    Parameters
    ----------
    points : np.ndarray
        (N, 3) centerline sample points.
    tangents, binormals, normals : np.ndarray
        (N, 3) per-point TBN frame vectors.
    config : RibbonExtractorConfig
        Ribbon configuration.

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        (vertices, vert_normals, uvs, triangles, colors)
        - vertices: (2N, 3) float64
        - vert_normals: (2N, 3) float64
        - uvs: (2N, 2) float64
        - triangles: (M, 3) int32
        - colors: (2N, 3) uint8
    """
    n = len(points)
    half_width = config.width / 2.0

    # Compute arc lengths for UV V-coordinate
    arc_lengths = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        arc_lengths[i] = arc_lengths[i - 1] + np.linalg.norm(
            points[i] - points[i - 1]
        )
    total_arc = arc_lengths[-1] if n > 1 else 1.0
    total_arc = max(total_arc, _EPS)

    # Allocate vertex arrays
    vertices = np.zeros((2 * n, 3), dtype=np.float64)
    vert_normals = np.zeros((2 * n, 3), dtype=np.float64)
    uvs = np.zeros((2 * n, 2), dtype=np.float64)

    for i in range(n):
        # Width taper: interpolate from full width at root to tapered at tip
        t_param = arc_lengths[i] / total_arc
        taper = 1.0 - (1.0 - config.width_taper) * t_param
        local_half_width = half_width * taper

        # Extrude left and right vertices along binormal
        left_idx = 2 * i
        right_idx = 2 * i + 1

        vertices[left_idx] = points[i] + binormals[i] * local_half_width
        vertices[right_idx] = points[i] - binormals[i] * local_half_width

        # Per-vertex normals (Guilty Gear Xrd style: blend geometric + proxy)
        # Geometric normal is the face normal (facing direction)
        # Proxy normal is a smoothed capsule-like normal
        geo_normal = normals[i]

        # Capsule proxy normal: blend between facing and lateral direction
        # Left vertex leans slightly outward, right vertex leans slightly inward
        proxy_left = normals[i] + binormals[i] * 0.3
        proxy_right = normals[i] - binormals[i] * 0.3
        proxy_left_len = np.linalg.norm(proxy_left)
        proxy_right_len = np.linalg.norm(proxy_right)
        if proxy_left_len > _EPS:
            proxy_left = proxy_left / proxy_left_len
        if proxy_right_len > _EPS:
            proxy_right = proxy_right / proxy_right_len

        # Blend geometric and proxy normals
        s = config.normal_smoothing
        vert_normals[left_idx] = geo_normal * (1.0 - s) + proxy_left * s
        vert_normals[right_idx] = geo_normal * (1.0 - s) + proxy_right * s

        # Normalize
        for idx in (left_idx, right_idx):
            nl = np.linalg.norm(vert_normals[idx])
            if nl > _EPS:
                vert_normals[idx] /= nl

        # UV coordinates
        v_coord = arc_lengths[i] / total_arc
        uvs[left_idx] = [0.0, v_coord]
        uvs[right_idx] = [1.0, v_coord]

    # Build triangle strip indices
    triangles = []
    for i in range(n - 1):
        l0 = 2 * i
        r0 = 2 * i + 1
        l1 = 2 * (i + 1)
        r1 = 2 * (i + 1) + 1

        # Front face: two triangles per quad
        triangles.append([l0, r0, l1])
        triangles.append([r0, r1, l1])

        # Back face (double-sided) for correct rendering from both sides
        if config.double_sided:
            triangles.append([l1, r0, l0])
            triangles.append([l1, r1, r0])

    tri_array = np.array(triangles, dtype=np.int32) if triangles else np.zeros(
        (0, 3), dtype=np.int32
    )

    # Vertex colors
    color_array = np.full((2 * n, 3), config.color, dtype=np.uint8)

    return vertices, vert_normals, uvs, tri_array, color_array


# ═══════════════════════════════════════════════════════════════════════════
#  PhysicalRibbonExtractor — Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════

class PhysicalRibbonExtractor:
    """Extract a 3D ribbon mesh from a secondary animation chain snapshot.

    This is the core P1-B1-1 component that transforms discrete 1D physics
    chain positions (from Jakobsen or XPBD solvers) into a proper 3D
    triangle mesh suitable for the orthographic pixel render pipeline.

    The extractor is a **standalone, stateless processor** — it does not
    modify any trunk code and produces a ``Mesh3D`` that can be composed
    with the base character through the data contract.

    Usage
    -----
    >>> from mathart.animation.physical_ribbon_extractor import (
    ...     PhysicalRibbonExtractor, RibbonExtractorConfig,
    ... )
    >>> extractor = PhysicalRibbonExtractor()
    >>> mesh, metadata = extractor.extract(
    ...     chain_points=[(0.0, 0.5), (0.0, 0.3), (0.05, 0.1), (0.1, -0.1)],
    ...     chain_name="cape",
    ... )
    >>> mesh.vertex_count > 0
    True
    >>> mesh.triangle_count > 0
    True
    """

    def __init__(
        self,
        config: Optional[RibbonExtractorConfig] = None,
    ) -> None:
        self.config = config or RibbonExtractorConfig()

    def extract(
        self,
        chain_points: Sequence[tuple[float, float]] | np.ndarray,
        chain_name: str = "ribbon",
        *,
        config_override: Optional[RibbonExtractorConfig] = None,
    ) -> tuple["_Mesh3DLike", RibbonMeshMetadata]:
        """Extract a 3D ribbon mesh from 2D chain points.

        Parameters
        ----------
        chain_points : sequence of (x, y) tuples or (N, 2) ndarray
            The 2D positions of the secondary chain particles.
            Typically from ``SecondaryChainSnapshot.points``.
        chain_name : str
            Name of the chain for metadata tagging.
        config_override : RibbonExtractorConfig, optional
            Override the instance config for this extraction.

        Returns
        -------
        tuple[Mesh3D, RibbonMeshMetadata]
            The generated 3D mesh and diagnostic metadata.

        Raises
        ------
        ValueError
            If fewer than 2 chain points are provided.
        """
        from mathart.animation.orthographic_pixel_render import Mesh3D

        cfg = config_override or self.config

        # Convert to numpy array
        pts_2d = np.asarray(chain_points, dtype=np.float64)
        if pts_2d.ndim == 1:
            pts_2d = pts_2d.reshape(-1, 2)
        if len(pts_2d) < 2:
            raise ValueError(
                f"PhysicalRibbonExtractor requires at least 2 chain points, "
                f"got {len(pts_2d)}"
            )

        input_count = len(pts_2d)

        # Stage 1: Catmull-Rom interpolation for smooth curve
        interpolated_2d = catmull_rom_interpolate(
            pts_2d,
            subdivisions=cfg.subdivisions_per_segment,
        )

        # Remove degenerate points
        interpolated_2d, removed = _remove_degenerate_points(
            interpolated_2d,
            min_dist=cfg.min_segment_length,
        )

        if len(interpolated_2d) < 2:
            # Fallback: use original points if interpolation collapsed
            interpolated_2d = pts_2d.copy()
            removed = 0

        # Stage 2: Lift 2D points to 2.5D (assign Z-depth)
        n_pts = len(interpolated_2d)
        points_3d = np.zeros((n_pts, 3), dtype=np.float64)
        points_3d[:, 0] = interpolated_2d[:, 0]  # X
        points_3d[:, 1] = interpolated_2d[:, 1]  # Y

        # Z-depth gradient: base at root, offset at tip
        for i in range(n_pts):
            t = i / max(n_pts - 1, 1)
            points_3d[i, 2] = cfg.z_depth_base + cfg.z_depth_range * t

        # Stage 3: Compute tangent-binormal-normal frames
        facing = np.asarray(cfg.facing_vector, dtype=np.float64)
        tangents, binormals, normals = compute_tangent_frames(points_3d, facing)

        # Stage 4: Extrude ribbon mesh
        vertices, vert_normals, uvs, triangles, colors = extrude_ribbon_mesh(
            points_3d, tangents, binormals, normals, cfg,
        )

        # Compute metadata
        arc_lengths = np.zeros(n_pts, dtype=np.float64)
        for i in range(1, n_pts):
            arc_lengths[i] = arc_lengths[i - 1] + np.linalg.norm(
                points_3d[i] - points_3d[i - 1]
            )
        total_arc = float(arc_lengths[-1]) if n_pts > 1 else 0.0

        bbox = {}
        if len(vertices) > 0:
            bbox = {
                "min_x": float(vertices[:, 0].min()),
                "max_x": float(vertices[:, 0].max()),
                "min_y": float(vertices[:, 1].min()),
                "max_y": float(vertices[:, 1].max()),
                "min_z": float(vertices[:, 2].min()),
                "max_z": float(vertices[:, 2].max()),
            }

        z_range = (
            (float(vertices[:, 2].min()), float(vertices[:, 2].max()))
            if len(vertices) > 0
            else (0.0, 0.0)
        )

        metadata = RibbonMeshMetadata(
            chain_name=chain_name,
            input_point_count=input_count,
            interpolated_point_count=n_pts,
            vertex_count=len(vertices),
            triangle_count=len(triangles),
            total_arc_length=total_arc,
            bounding_box=bbox,
            width=cfg.width,
            z_depth_range=z_range,
            degenerate_segments_removed=removed,
        )

        # Build Mesh3D
        mesh = Mesh3D(
            vertices=vertices,
            normals=vert_normals,
            triangles=triangles,
            colors=colors,
        )

        logger.info(
            "[PhysicalRibbonExtractor] Generated ribbon '%s': "
            "%d input pts → %d interpolated → %d verts, %d tris, "
            "arc_length=%.4f, bbox=%s",
            chain_name,
            input_count,
            n_pts,
            mesh.vertex_count,
            mesh.triangle_count,
            total_arc,
            bbox,
        )

        return mesh, metadata


# ═══════════════════════════════════════════════════════════════════════════
#  Mesh Merging Utility (for compositing multiple meshes)
# ═══════════════════════════════════════════════════════════════════════════

def merge_meshes(
    meshes: Sequence[Any],
) -> Any:
    """Merge multiple Mesh3D instances into a single combined mesh.

    This is the geometry-level composition utility that combines a base
    character mesh with one or more physical attachment meshes (cape, hair)
    into a single draw call.  It preserves all vertex attributes.

    Parameters
    ----------
    meshes : sequence of Mesh3D
        The meshes to merge.  Empty meshes are skipped.

    Returns
    -------
    Mesh3D
        A single merged mesh containing all vertices and triangles.

    Raises
    ------
    ValueError
        If no valid meshes are provided.
    """
    from mathart.animation.orthographic_pixel_render import Mesh3D

    valid = [m for m in meshes if m.vertex_count > 0 and m.triangle_count > 0]
    if not valid:
        raise ValueError("merge_meshes requires at least one non-empty mesh")

    if len(valid) == 1:
        return valid[0]

    all_verts = []
    all_normals = []
    all_tris = []
    all_colors = []
    offset = 0

    for m in valid:
        all_verts.append(m.vertices)
        all_normals.append(m.normals)
        all_colors.append(m.colors)
        # Offset triangle indices
        all_tris.append(m.triangles + offset)
        offset += m.vertex_count

    return Mesh3D(
        vertices=np.concatenate(all_verts, axis=0),
        normals=np.concatenate(all_normals, axis=0),
        triangles=np.concatenate(all_tris, axis=0),
        colors=np.concatenate(all_colors, axis=0),
    )


__all__ = [
    "RibbonExtractorConfig",
    "RibbonMeshMetadata",
    "PhysicalRibbonExtractor",
    "catmull_rom_interpolate",
    "compute_tangent_frames",
    "extrude_ribbon_mesh",
    "merge_meshes",
]
