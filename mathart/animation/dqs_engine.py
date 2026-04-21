"""SESSION-118 (P1-HUMAN-31C): Tensorized Dual Quaternion Skinning Engine.

This module implements a fully tensorized, zero-scalar-loop dual quaternion
skinning (DQS) engine for the pseudo-3D paper-doll / mesh-shell backend.

Research Foundations
--------------------
1. **Kavan et al. (SIGGRAPH 2007)** — "Skinning with Dual Quaternions":
   The foundational DQS paper.  Dual Quaternion Linear Blending (DLB)
   preserves volume by interpolating rigid transforms on the unit
   dual-quaternion manifold, eliminating the candy-wrapper collapse
   inherent to Linear Blend Skinning (LBS).

2. **Data-Oriented Tensor Skinning** — Industrial-grade skinning pipelines
   map bone transforms to dual quaternion arrays ``[B, 8]``, combine with
   skinning weight matrices ``[V, B]`` via ``np.einsum``, and produce
   blended per-vertex dual quaternions in a single tensor operation.

3. **Arc System Works / Guilty Gear Xrd (GDC 2015)** — Even in 2D-first
   paper-doll workflows, joint deformation must use 3D DQS to achieve
   smooth perspective warping and correct normal rotation for cel-shading.

Mathematical Invariants (Red-Line Guards)
-----------------------------------------
🔴 **Anti-Normalization-Failure Guard**: After DLB weighted summation the
   blended dual quaternion is NOT unit.  We MUST divide by ``||q_real||``
   to re-normalize.  Failure causes coordinate-frame explosion.

🔴 **Anti-Scalar-Loop Trap**: All operations use ``np.einsum`` / broadcast
   tensor ops.  Zero Python ``for v in vertices`` loops.

🔴 **Anti-Antipodal Tearing**: Quaternions ``q`` and ``-q`` encode the same
   rotation.  Before blending, we check ``dot(bone_dq.real, ref.real)``
   and flip the sign of any bone DQ whose dot product is negative.  This
   forces interpolation along the shortest arc.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
#  Low-Level Quaternion Tensor Utilities (Vectorized)
# ═══════════════════════════════════════════════════════════════════════════

def quat_mul_batch(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Batch quaternion multiplication.  Hamilton convention [w, x, y, z].

    Parameters
    ----------
    a, b : np.ndarray
        Quaternion arrays of shape ``(..., 4)``.

    Returns
    -------
    np.ndarray
        Product quaternions, same leading shape, last dim 4.
    """
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], axis=-1)


def quat_conj_batch(q: np.ndarray) -> np.ndarray:
    """Batch quaternion conjugate.  ``(..., 4) -> (..., 4)``."""
    return q * np.array([1.0, -1.0, -1.0, -1.0], dtype=q.dtype)


def quat_normalize_batch(q: np.ndarray) -> np.ndarray:
    """Batch-normalize quaternions to unit length.

    Parameters
    ----------
    q : np.ndarray
        Shape ``(..., 4)``.

    Returns
    -------
    np.ndarray
        Normalized quaternions, same shape.
    """
    norms = np.linalg.norm(q, axis=-1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return q / norms


def quat_rotate_points_batch(q: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Rotate 3D points by unit quaternions (vectorized).

    Parameters
    ----------
    q : np.ndarray
        Unit quaternions, shape ``(..., 4)`` [w, x, y, z].
    pts : np.ndarray
        3D points, shape ``(..., 3)``.

    Returns
    -------
    np.ndarray
        Rotated points, shape ``(..., 3)``.
    """
    # Promote points to pure quaternion (0, x, y, z)
    zeros = np.zeros(pts.shape[:-1] + (1,), dtype=pts.dtype)
    p_quat = np.concatenate([zeros, pts], axis=-1)  # (..., 4)
    # q * p * conj(q)
    q_conj = quat_conj_batch(q)
    rotated = quat_mul_batch(quat_mul_batch(q, p_quat), q_conj)
    return rotated[..., 1:4]


def quat_from_axis_angle_batch(
    axes: np.ndarray, angles: np.ndarray,
) -> np.ndarray:
    """Create quaternions from axis-angle representation (batch).

    Parameters
    ----------
    axes : np.ndarray
        Rotation axes, shape ``(..., 3)``.  Will be normalized internally.
    angles : np.ndarray
        Rotation angles in radians, shape ``(...,)``.

    Returns
    -------
    np.ndarray
        Unit quaternions, shape ``(..., 4)`` [w, x, y, z].
    """
    axes = np.asarray(axes, dtype=np.float64)
    angles = np.asarray(angles, dtype=np.float64)
    # Normalize axes
    norms = np.linalg.norm(axes, axis=-1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    axes = axes / norms
    half = 0.5 * angles
    s = np.sin(half)
    c = np.cos(half)
    # [w, x, y, z]
    w = c
    xyz = axes * s[..., np.newaxis]
    return np.concatenate([w[..., np.newaxis], xyz], axis=-1)


# ═══════════════════════════════════════════════════════════════════════════
#  Dual Quaternion Construction & Extraction (Tensorized)
# ═══════════════════════════════════════════════════════════════════════════

def dq_from_rotation_translation(
    rotations: np.ndarray, translations: np.ndarray,
) -> np.ndarray:
    """Build dual quaternion 8-vectors from rotation quaternions and translations.

    Parameters
    ----------
    rotations : np.ndarray
        Unit quaternions [w, x, y, z], shape ``(..., 4)``.
    translations : np.ndarray
        Translation vectors, shape ``(..., 3)``.

    Returns
    -------
    np.ndarray
        Dual quaternions, shape ``(..., 8)``.
        Layout: ``[real_w, real_x, real_y, real_z, dual_w, dual_x, dual_y, dual_z]``.
    """
    rotations = np.asarray(rotations, dtype=np.float64)
    translations = np.asarray(translations, dtype=np.float64)
    r = quat_normalize_batch(rotations)
    # Build translation quaternion: (0, tx, ty, tz)
    zeros = np.zeros(translations.shape[:-1] + (1,), dtype=np.float64)
    t_quat = np.concatenate([zeros, translations], axis=-1)  # (..., 4)
    # dual = 0.5 * t_quat * r
    d = 0.5 * quat_mul_batch(t_quat, r)
    return np.concatenate([r, d], axis=-1)


def dq_from_axis_angle_translation(
    axes: np.ndarray, angles: np.ndarray, translations: np.ndarray,
) -> np.ndarray:
    """Build dual quaternions from axis-angle + translation (batch).

    Parameters
    ----------
    axes : np.ndarray
        Rotation axes, shape ``(..., 3)``.
    angles : np.ndarray
        Rotation angles in radians, shape ``(...,)``.
    translations : np.ndarray
        Translation vectors, shape ``(..., 3)``.

    Returns
    -------
    np.ndarray
        Dual quaternions, shape ``(..., 8)``.
    """
    q = quat_from_axis_angle_batch(axes, angles)
    return dq_from_rotation_translation(q, translations)


def dq_identity(shape: tuple[int, ...] = ()) -> np.ndarray:
    """Return identity dual quaternion(s).

    Parameters
    ----------
    shape : tuple
        Leading batch dimensions.

    Returns
    -------
    np.ndarray
        Shape ``(*shape, 8)`` identity dual quaternions.
    """
    dq = np.zeros(shape + (8,), dtype=np.float64)
    dq[..., 0] = 1.0  # real part w = 1
    return dq


def dq_extract_translation(dq: np.ndarray) -> np.ndarray:
    """Extract translation vectors from dual quaternions.

    Parameters
    ----------
    dq : np.ndarray
        Dual quaternions, shape ``(..., 8)``.

    Returns
    -------
    np.ndarray
        Translation vectors, shape ``(..., 3)``.
    """
    real = dq[..., :4]
    dual = dq[..., 4:]
    # t_quat = 2 * dual * conj(real)
    t_quat = 2.0 * quat_mul_batch(dual, quat_conj_batch(real))
    return t_quat[..., 1:4]


# ═══════════════════════════════════════════════════════════════════════════
#  Tensorized DQS Skinning Engine
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DQSSkinningResult:
    """Result of tensorized DQS skinning.

    Attributes
    ----------
    deformed_vertices : np.ndarray
        Shape ``(F, V, 3)`` — deformed vertex positions per frame.
    deformed_normals : np.ndarray
        Shape ``(F, V, 3)`` — deformed normals per frame.
    blended_dqs : np.ndarray
        Shape ``(F, V, 8)`` — per-vertex blended dual quaternions (for debug).
    """
    deformed_vertices: np.ndarray
    deformed_normals: np.ndarray
    blended_dqs: np.ndarray


def tensorized_dqs_skin(
    base_vertices: np.ndarray,
    base_normals: np.ndarray,
    skin_weights: np.ndarray,
    bone_dqs: np.ndarray,
) -> DQSSkinningResult:
    """Perform tensorized Dual Quaternion Skinning (DQS).

    This is the core DQS engine.  It implements the full Kavan et al. (2007)
    DLB pipeline in pure NumPy tensor operations with ZERO scalar loops.

    Parameters
    ----------
    base_vertices : np.ndarray
        Rest-pose vertex positions, shape ``(V, 3)``.
    base_normals : np.ndarray
        Rest-pose vertex normals, shape ``(V, 3)``.
    skin_weights : np.ndarray
        Skinning weight matrix, shape ``(V, B)``.
        Each row sums to 1.0 (convex weights).
    bone_dqs : np.ndarray
        Per-frame bone dual quaternions, shape ``(F, B, 8)``.
        Layout: ``[real_w, real_x, real_y, real_z, dual_w, dual_x, dual_y, dual_z]``.

    Returns
    -------
    DQSSkinningResult
        Deformed vertices ``(F, V, 3)``, deformed normals ``(F, V, 3)``,
        and blended dual quaternions ``(F, V, 8)``.

    Notes
    -----
    **Antipodal Correction** (Anti-Tearing Guard):
        Before blending, we check the dot product of each bone's real
        quaternion against a reference (bone 0).  If negative, we flip
        the entire 8-component DQ to ensure shortest-arc interpolation.

    **Normalization** (Anti-Explosion Guard):
        After weighted summation, the blended DQ is NOT unit.  We divide
        all 8 components by ``||real_part||`` to re-normalize.

    **Normal Rotation**:
        Normals are rotated by the real (rotation) part of the blended DQ
        only — no translation is applied.  This preserves lighting continuity
        across the deformed mesh (Guilty Gear Xrd GDC 2015 requirement).
    """
    base_vertices = np.asarray(base_vertices, dtype=np.float64)
    base_normals = np.asarray(base_normals, dtype=np.float64)
    skin_weights = np.asarray(skin_weights, dtype=np.float64)
    bone_dqs = np.asarray(bone_dqs, dtype=np.float64)

    V = base_vertices.shape[0]
    F = bone_dqs.shape[0]
    B = bone_dqs.shape[1]

    assert base_vertices.shape == (V, 3), f"Expected (V,3), got {base_vertices.shape}"
    assert base_normals.shape == (V, 3), f"Expected (V,3), got {base_normals.shape}"
    assert skin_weights.shape == (V, B), f"Expected (V,B), got {skin_weights.shape}"
    assert bone_dqs.shape == (F, B, 8), f"Expected (F,B,8), got {bone_dqs.shape}"

    # ── Step 1: Antipodal Correction ──────────────────────────────────────
    # Reference: real part of bone 0 for each frame.
    # Shape: (F, 1, 4)
    ref_real = bone_dqs[:, 0:1, :4]
    # Dot product of each bone's real part with reference: (F, B)
    dots = np.sum(bone_dqs[:, :, :4] * ref_real, axis=-1)  # (F, B)
    # Sign correction: flip entire DQ if dot < 0
    signs = np.where(dots >= 0.0, 1.0, -1.0)  # (F, B)
    # Broadcast signs to all 8 components: (F, B, 1)
    corrected_dqs = bone_dqs * signs[..., np.newaxis]  # (F, B, 8)

    # ── Step 2: Weighted Blend via einsum ─────────────────────────────────
    # blended[f, v, d] = sum_b weights[v, b] * corrected_dqs[f, b, d]
    # Shape: (F, V, 8)
    blended = np.einsum("vb, fbd -> fvd", skin_weights, corrected_dqs)

    # ── Step 3: Normalize by real-part norm ───────────────────────────────
    # ||q_real|| for each (frame, vertex): (F, V, 1)
    real_norm = np.linalg.norm(blended[..., :4], axis=-1, keepdims=True)
    real_norm = np.maximum(real_norm, 1e-12)
    blended = blended / real_norm  # (F, V, 8) — now unit dual quaternion

    # ── Step 4: Extract rotation and translation ──────────────────────────
    q_real = blended[..., :4]   # (F, V, 4) — rotation quaternion
    q_dual = blended[..., 4:]   # (F, V, 4) — dual part

    # Translation: t = 2 * q_dual * conj(q_real)
    t_quat = 2.0 * quat_mul_batch(q_dual, quat_conj_batch(q_real))
    translation = t_quat[..., 1:4]  # (F, V, 3)

    # ── Step 5: Transform vertices ────────────────────────────────────────
    # Broadcast base_vertices: (1, V, 3) -> (F, V, 3)
    verts_broadcast = np.broadcast_to(base_vertices[np.newaxis], (F, V, 3))
    # Rotate vertices: q_real * (0, v) * conj(q_real)
    rotated_verts = quat_rotate_points_batch(q_real, verts_broadcast)
    # Add translation
    deformed_vertices = rotated_verts + translation  # (F, V, 3)

    # ── Step 6: Transform normals (rotation only, no translation) ─────────
    normals_broadcast = np.broadcast_to(base_normals[np.newaxis], (F, V, 3))
    deformed_normals = quat_rotate_points_batch(q_real, normals_broadcast)
    # Re-normalize normals to unit length
    n_norms = np.linalg.norm(deformed_normals, axis=-1, keepdims=True)
    n_norms = np.maximum(n_norms, 1e-12)
    deformed_normals = deformed_normals / n_norms

    return DQSSkinningResult(
        deformed_vertices=deformed_vertices,
        deformed_normals=deformed_normals,
        blended_dqs=blended,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Mesh Utilities for Pseudo-3D Shell Generation
# ═══════════════════════════════════════════════════════════════════════════

def create_cylinder_mesh(
    radius: float = 0.5,
    height: float = 2.0,
    radial_segments: int = 32,
    height_segments: int = 10,
    axis: str = "y",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create a cylinder mesh for testing DQS volume preservation.

    The cylinder is centered at the origin with the specified axis.
    This is the canonical test mesh for DQS: a cylinder simulating
    a human arm, with two bones splitting at the midpoint.

    Parameters
    ----------
    radius : float
        Cylinder radius.
    height : float
        Cylinder height.
    radial_segments : int
        Number of radial subdivisions.
    height_segments : int
        Number of height subdivisions.
    axis : str
        Primary axis: "x", "y", or "z".

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray]
        (vertices, normals, triangles) where:
        - vertices: (V, 3) float64
        - normals: (V, 3) float64
        - triangles: (T, 3) int32
    """
    verts = []
    norms = []
    tris = []

    for j in range(height_segments + 1):
        v = j / height_segments
        h = -height / 2.0 + v * height
        for i in range(radial_segments):
            theta = 2.0 * math.pi * i / radial_segments
            cx = radius * math.cos(theta)
            cz = radius * math.sin(theta)
            nx = math.cos(theta)
            nz = math.sin(theta)

            if axis == "y":
                verts.append([cx, h, cz])
                norms.append([nx, 0.0, nz])
            elif axis == "x":
                verts.append([h, cx, cz])
                norms.append([0.0, nx, nz])
            else:  # z
                verts.append([cx, cz, h])
                norms.append([nx, nz, 0.0])

    # Triangles
    for j in range(height_segments):
        for i in range(radial_segments):
            i_next = (i + 1) % radial_segments
            v00 = j * radial_segments + i
            v10 = j * radial_segments + i_next
            v01 = (j + 1) * radial_segments + i
            v11 = (j + 1) * radial_segments + i_next
            tris.append([v00, v10, v01])
            tris.append([v10, v11, v01])

    vertices = np.array(verts, dtype=np.float64)
    normals_arr = np.array(norms, dtype=np.float64)
    triangles = np.array(tris, dtype=np.int32)

    return vertices, normals_arr, triangles


def compute_cylinder_skin_weights(
    vertices: np.ndarray,
    height: float = 2.0,
    axis: str = "y",
    blend_width: float = 0.3,
) -> np.ndarray:
    """Compute smooth skinning weights for a two-bone cylinder.

    Bone 0 controls the bottom half, bone 1 controls the top half,
    with a smooth blend zone around the midpoint.

    Parameters
    ----------
    vertices : np.ndarray
        Vertex positions, shape ``(V, 3)``.
    height : float
        Total cylinder height.
    axis : str
        Primary axis ("x", "y", or "z").
    blend_width : float
        Width of the smooth blend zone around the midpoint.

    Returns
    -------
    np.ndarray
        Skinning weights, shape ``(V, 2)`` — two bones.
    """
    axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
    h = vertices[:, axis_idx]  # (V,)
    # Normalize to [0, 1] along height
    t = (h - (-height / 2.0)) / height  # 0 at bottom, 1 at top
    # Smooth blend: bone 1 weight rises from 0 to 1 around t=0.5
    half = 0.5
    w1 = np.clip((t - half + blend_width / 2) / blend_width, 0.0, 1.0)
    w0 = 1.0 - w1
    return np.stack([w0, w1], axis=-1)  # (V, 2)


def compute_cross_section_area(
    vertices: np.ndarray,
    axis: str = "y",
    slice_pos: float = 0.0,
    tolerance: float = 0.15,
) -> float:
    """Compute the approximate cross-sectional area at a given position.

    Selects vertices within ``tolerance`` of ``slice_pos`` along the
    specified axis and computes the area of their 2D convex hull.

    Parameters
    ----------
    vertices : np.ndarray
        Vertex positions, shape ``(V, 3)`` or ``(F, V, 3)`` (takes last frame).
    axis : str
        Slicing axis.
    slice_pos : float
        Position along the axis to slice.
    tolerance : float
        Half-width of the slice band.

    Returns
    -------
    float
        Approximate cross-sectional area.
    """
    if vertices.ndim == 3:
        vertices = vertices[-1]  # take last frame
    axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
    other_axes = [i for i in range(3) if i != axis_idx]

    h = vertices[:, axis_idx]
    mask = np.abs(h - slice_pos) < tolerance
    if np.sum(mask) < 3:
        return 0.0

    pts_2d = vertices[mask][:, other_axes]  # (N, 2)

    # Compute convex hull area using the shoelace formula
    # Sort points by angle from centroid
    centroid = pts_2d.mean(axis=0)
    angles = np.arctan2(pts_2d[:, 1] - centroid[1], pts_2d[:, 0] - centroid[0])
    order = np.argsort(angles)
    sorted_pts = pts_2d[order]

    # Shoelace formula
    n = len(sorted_pts)
    x = sorted_pts[:, 0]
    y = sorted_pts[:, 1]
    area = 0.5 * np.abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return float(area)


# ═══════════════════════════════════════════════════════════════════════════
#  Module Exports
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Quaternion utilities
    "quat_mul_batch",
    "quat_conj_batch",
    "quat_normalize_batch",
    "quat_rotate_points_batch",
    "quat_from_axis_angle_batch",
    # Dual quaternion construction
    "dq_from_rotation_translation",
    "dq_from_axis_angle_translation",
    "dq_identity",
    "dq_extract_translation",
    # DQS engine
    "DQSSkinningResult",
    "tensorized_dqs_skin",
    # Mesh utilities
    "create_cylinder_mesh",
    "compute_cylinder_skin_weights",
    "compute_cross_section_area",
]
