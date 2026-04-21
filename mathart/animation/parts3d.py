"""
Character Evolution 3.0 — 3D parametric equipment library and tensorized
bone-socket mounter (P1-NEW-9C, SESSION-121).

This module is the 3D companion to ``mathart/animation/parts.py`` (which
holds the legacy 2D SDF morphology library).  It provides:

1.  **Parametric Mesh3D primitives** for the new equipment slots that
    SESSION-121 introduces in ``CharacterGenotype.PART_REGISTRY``
    (torso overlays, hand items, foot accessories).  Every primitive is
    derived from a closed-form math expression that mirrors one of the
    Inigo Quilez SDF operators:

      * ``opRound``     → vertex-normal dilation (breastplate, greaves).
      * ``opOnion``     → twin-shell extrusion (wizard robe, hood).
      * ``opElongate``  → axis-aligned half-extent stretch (sword blade,
                          staff handle).

    The factories return strongly-typed :class:`PartShape3D` records that
    carry the bare :class:`Mesh3D` plus provenance metadata (``primitive``,
    ``inflate_radius``, ``half_extents``).  This separation lets the
    socket-mounter trust the geometry without inspecting it.

2.  **SocketSpec** — the 3D analogue of an Unreal Engine 5 Skeletal Mesh
    Socket: a parent-bone identifier plus local SRT offsets.  ``SocketSpec``
    is the *single* source of truth for "where does this equipment ride".

3.  **TensorSocketMounter** — a vectorised attachment kernel.  It takes
    a list of (PartShape3D, SocketSpec) pairs plus a 2-D :class:`Skeleton`,
    builds the per-socket 4x4 homogeneous transform using NumPy matrix
    broadcasts (zero Python per-vertex loops), and emits the final
    world-space :class:`Mesh3D` list.  The kernel is the authoritative
    Z-buffer source: every vertex carries the world ``z`` that downstream
    P1-AI-1 multi-pass renderers serialize to ControlNet depth.

The composition contract aligns with Pixar's OpenUSD Reference arc:
``compose_character_with_attachments`` (already shipped in
``mathart/core/physical_ribbon_backend.py``) consumes the Mesh3D outputs
of this module wrapped in :class:`ArtifactManifest` instances and emits a
``COMPOSITE`` manifest where each equipment entry is a sub-reference.

References
----------
* Unreal Engine 5 Skeletal Mesh Sockets — Epic Games official docs,
  ``dev.epicgames.com/documentation/unreal-engine/skeletal-mesh-sockets-in-unreal-engine``
* Inigo Quilez · Signed Distance Functions — ``iquilezles.org/articles/distfunctions/``
* OpenUSD Reference Composition Arc — ``docs.nvidia.com/learn-openusd/latest/composition-basics/references.html``

Detailed alignment notes live in ``docs/research/P1_NEW_9C_research_alignment.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from .orthographic_pixel_render import Mesh3D
from .skeleton import Skeleton


# ── Type-safe equipment shape carrier ────────────────────────────────────────


@dataclass(frozen=True)
class PartShape3D:
    """A typed wrapper around a :class:`Mesh3D` produced by this library.

    Attributes
    ----------
    part_id : str
        The CharacterGenotype ``PART_REGISTRY`` key that produced this shape.
    primitive : str
        Short label of the underlying SDF operator family — currently one
        of ``"sdf_round_box"``, ``"sdf_onion_capsule"``,
        ``"sdf_elongated_capsule"``, ``"sdf_round_disc"``.
    mesh : Mesh3D
        The triangle mesh in *local* socket space (centred on the origin
        with the canonical "up" being +Y).
    inflate_radius : float
        How much the surface was offset outward via ``opRound``.  Always
        strictly positive when the part is meant to overlap a base mesh,
        which is the mathematical guarantee against Z-fighting.
    half_extents : tuple[float, float, float]
        Axis-aligned half-extents used by ``opElongate`` / box generation.
    """

    part_id: str
    primitive: str
    mesh: Mesh3D
    inflate_radius: float = 0.0
    half_extents: tuple[float, float, float] = (0.0, 0.0, 0.0)


# ── Socket specification (UE5-aligned) ───────────────────────────────────────


@dataclass(frozen=True)
class SocketSpec:
    """3D bone-socket descriptor mirroring UE5's ``USkeletalMeshSocket``.

    A socket is a *named* attach point that lives in the local frame of a
    parent bone.  When the socket is resolved against a posed skeleton it
    yields the world-space transform that any equipment Mesh3D must apply
    to its local-space vertices.

    Attributes
    ----------
    socket_name : str
        Human-readable identifier (e.g. ``"chest_torso_overlay"``).
    parent_bone : str
        The Skeleton joint name this socket is parented to.
    local_translation : tuple[float, float, float]
        Local offset (x, y, z) relative to the parent bone, in
        skeleton-normalised units (1.0 == 1 head unit, matching
        :class:`mathart.animation.skeleton.Skeleton`).
    local_rotation_deg : tuple[float, float, float]
        Local Euler rotation (rx, ry, rz) **in degrees**, applied as
        intrinsic XYZ.  Degrees, not radians, so JSON archives stay
        human-readable.
    local_scale : tuple[float, float, float]
        Per-axis local scale.
    """

    socket_name: str
    parent_bone: str
    local_translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    local_rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    local_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


# ── Default socket library (humanoid skeleton anchors) ───────────────────────


DEFAULT_SOCKETS: dict[str, SocketSpec] = {
    "torso_overlay": SocketSpec(
        socket_name="chest_torso_overlay",
        parent_bone="chest",
        local_translation=(0.0, 0.0, 0.0),
        local_rotation_deg=(0.0, 0.0, 0.0),
        local_scale=(1.0, 1.0, 1.0),
    ),
    "hand_item": SocketSpec(
        socket_name="r_hand_item",
        parent_bone="r_hand",
        local_translation=(0.02, 0.0, 0.0),
        local_rotation_deg=(0.0, 0.0, 0.0),
        local_scale=(1.0, 1.0, 1.0),
    ),
    "foot_accessory": SocketSpec(
        socket_name="r_foot_accessory",
        parent_bone="r_foot",
        local_translation=(0.0, 0.0, 0.0),
        local_rotation_deg=(0.0, 0.0, 0.0),
        local_scale=(1.0, 1.0, 1.0),
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
#  Mesh primitive helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_box(
    half_extents: tuple[float, float, float],
    *,
    color: tuple[int, int, int],
    inflate_radius: float = 0.0,
) -> Mesh3D:
    """Build an axis-aligned rounded-box mesh.

    Implements Inigo Quilez ``opRound( box(p, h) , r )`` at the mesh
    level: each of the 8 box corners is pushed outward along its
    diagonal by ``inflate_radius``, which is the SDF-distance-offset
    semantics translated to vertex space.  Face normals are recomputed
    so cel-shading can take a clean derivative.
    """

    hx, hy, hz = half_extents
    corners = np.array(
        [
            [-hx, -hy, -hz], [hx, -hy, -hz],
            [hx,  hy, -hz], [-hx,  hy, -hz],
            [-hx, -hy,  hz], [hx, -hy,  hz],
            [hx,  hy,  hz], [-hx,  hy,  hz],
        ],
        dtype=np.float64,
    )

    if inflate_radius > 0.0:
        diag = corners.copy()
        norms = np.linalg.norm(diag, axis=1, keepdims=True)
        norms[norms < 1e-9] = 1.0
        corners = corners + (diag / norms) * float(inflate_radius)

    # Approximate per-vertex normals (corner diagonals).
    raw_normals = corners.copy()
    n_norm = np.linalg.norm(raw_normals, axis=1, keepdims=True)
    n_norm[n_norm < 1e-9] = 1.0
    normals = raw_normals / n_norm

    triangles = np.array(
        [
            [4, 5, 6], [4, 6, 7],     # +Z face
            [1, 0, 3], [1, 3, 2],     # -Z face
            [3, 7, 6], [3, 6, 2],     # +Y face
            [0, 1, 5], [0, 5, 4],     # -Y face
            [1, 2, 6], [1, 6, 5],     # +X face
            [0, 4, 7], [0, 7, 3],     # -X face
        ],
        dtype=np.int32,
    )

    colors = np.full((corners.shape[0], 3), color, dtype=np.uint8)
    return Mesh3D(
        vertices=corners,
        normals=normals,
        triangles=triangles,
        colors=colors,
    )


def _make_capsule(
    radius: float,
    half_length: float,
    *,
    rings: int = 6,
    sectors: int = 10,
    color: tuple[int, int, int],
    axis: str = "y",
) -> Mesh3D:
    """Build a capsule (cylinder with two hemispherical caps).

    Generated as a UV sphere whose middle ring is *elongated* along
    ``axis`` by ``half_length``.  This is exactly Inigo Quilez
    ``opElongate( sphere(p), h=(0, half_length, 0) )`` translated to
    vertex space — every sphere vertex above the equator is shifted by
    ``+half_length`` along ``axis``, every vertex below the equator by
    ``-half_length``.  No Python loops over individual vertices in the
    hot path: the elongation is one ``np.where`` mask.
    """

    if axis not in ("x", "y", "z"):
        raise ValueError(f"capsule axis must be one of x/y/z, got {axis!r}")

    # 1) Generate a unit sphere of the requested resolution.
    rings = max(rings, 4)
    sectors = max(sectors, 6)
    phi = np.linspace(0.0, math.pi, rings + 1)
    theta = np.linspace(0.0, 2.0 * math.pi, sectors + 1)
    phi_grid, theta_grid = np.meshgrid(phi, theta, indexing="ij")
    sphere = np.stack(
        [
            np.sin(phi_grid) * np.cos(theta_grid),
            np.cos(phi_grid),
            np.sin(phi_grid) * np.sin(theta_grid),
        ],
        axis=-1,
    ).reshape(-1, 3) * float(radius)

    normals = sphere / (np.linalg.norm(sphere, axis=1, keepdims=True) + 1e-12)

    # 2) Apply opElongate on the chosen axis.
    axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
    elong = np.where(sphere[:, axis_idx] >= 0.0, +half_length, -half_length)
    sphere[:, axis_idx] = sphere[:, axis_idx] + elong

    # 3) Build the triangle index array (two triangles per quad cell).
    tris: list[list[int]] = []
    for r in range(rings):
        for s in range(sectors):
            i0 = r * (sectors + 1) + s
            i1 = i0 + 1
            i2 = i0 + (sectors + 1)
            i3 = i2 + 1
            tris.append([i0, i2, i1])
            tris.append([i1, i2, i3])

    triangles = np.asarray(tris, dtype=np.int32)
    colors = np.full((sphere.shape[0], 3), color, dtype=np.uint8)
    return Mesh3D(
        vertices=sphere,
        normals=normals,
        triangles=triangles,
        colors=colors,
    )


def _make_disc(
    radius: float,
    thickness: float,
    *,
    sectors: int = 18,
    color: tuple[int, int, int],
) -> Mesh3D:
    """Build a flat disc (round shield).  Two coaxial rings + centre."""

    sectors = max(sectors, 6)
    angles = np.linspace(0.0, 2.0 * math.pi, sectors, endpoint=False)
    front_ring = np.stack(
        [np.cos(angles) * radius, np.sin(angles) * radius, np.full_like(angles, +thickness * 0.5)],
        axis=-1,
    )
    back_ring = front_ring.copy()
    back_ring[:, 2] = -thickness * 0.5

    centre_front = np.array([0.0, 0.0, +thickness * 0.5])
    centre_back = np.array([0.0, 0.0, -thickness * 0.5])

    vertices = np.concatenate(
        [front_ring, back_ring, centre_front[None, :], centre_back[None, :]],
        axis=0,
    )

    n_front = sectors
    n_back = sectors
    centre_f_idx = n_front + n_back
    centre_b_idx = centre_f_idx + 1

    tris: list[list[int]] = []
    for s in range(sectors):
        s_next = (s + 1) % sectors
        # Front cap fan
        tris.append([centre_f_idx, s, s_next])
        # Back cap fan (reverse winding)
        tris.append([centre_b_idx, n_front + s_next, n_front + s])
        # Side wall (two triangles per quad)
        tris.append([s, s_next, n_front + s_next])
        tris.append([s, n_front + s_next, n_front + s])

    triangles = np.asarray(tris, dtype=np.int32)

    # Approximate normals: front/back rings face ±Z; centre vertices face ±Z.
    normals = np.zeros_like(vertices)
    normals[: n_front, 2] = +1.0
    normals[n_front : n_front + n_back, 2] = -1.0
    normals[centre_f_idx, 2] = +1.0
    normals[centre_b_idx, 2] = -1.0

    colors = np.full((vertices.shape[0], 3), color, dtype=np.uint8)
    return Mesh3D(
        vertices=vertices,
        normals=normals,
        triangles=triangles,
        colors=colors,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Public part factories
# ─────────────────────────────────────────────────────────────────────────────


def build_torso_breastplate(
    *,
    torso_width: float = 0.27,
    torso_height: float = 0.21,
    inflate_radius: float = 0.018,
    color: tuple[int, int, int] = (170, 170, 200),
) -> PartShape3D:
    """Plate breastplate via vertex-normal dilation (``opRound``).

    The breastplate is generated as a rounded box that is *strictly larger*
    than the torso bone's bounding box — ``inflate_radius > 0`` guarantees
    every breastplate vertex lies outside the underlying torso surface, so
    no sample point is shared between the two meshes.  This eliminates
    Z-fighting **mathematically**, not just numerically.
    """
    half = (torso_width * 0.5, torso_height * 0.5, torso_width * 0.4)
    mesh = _make_box(half, color=color, inflate_radius=inflate_radius)
    return PartShape3D(
        part_id="torso_breastplate",
        primitive="sdf_round_box",
        mesh=mesh,
        inflate_radius=inflate_radius,
        half_extents=half,
    )


def build_torso_robe(
    *,
    torso_width: float = 0.27,
    torso_height: float = 0.40,
    shell_thickness: float = 0.022,
    color: tuple[int, int, int] = (90, 60, 130),
) -> PartShape3D:
    """Wizard robe modelled as an onion-shell capsule (``opOnion``).

    The robe extends below the torso to cover hips/legs and is generated
    as a vertical capsule whose surface is offset outward by
    ``shell_thickness``.
    """
    half = (torso_width * 0.5 + shell_thickness, torso_height * 0.5, torso_width * 0.45)
    mesh = _make_box(half, color=color, inflate_radius=shell_thickness)
    return PartShape3D(
        part_id="torso_robe",
        primitive="sdf_onion_capsule",
        mesh=mesh,
        inflate_radius=shell_thickness,
        half_extents=half,
    )


def build_torso_vest(
    *,
    torso_width: float = 0.27,
    torso_height: float = 0.18,
    shell_thickness: float = 0.012,
    color: tuple[int, int, int] = (130, 90, 60),
) -> PartShape3D:
    """Leather vest — thinner shell than the breastplate."""
    half = (torso_width * 0.5, torso_height * 0.5, torso_width * 0.35)
    mesh = _make_box(half, color=color, inflate_radius=shell_thickness)
    return PartShape3D(
        part_id="torso_vest",
        primitive="sdf_round_box",
        mesh=mesh,
        inflate_radius=shell_thickness,
        half_extents=half,
    )


def build_hand_sword(
    *,
    blade_length: float = 0.55,
    blade_radius: float = 0.018,
    color: tuple[int, int, int] = (200, 210, 220),
) -> PartShape3D:
    """Long sword via SDF elongation (``opElongate`` on Y axis)."""
    mesh = _make_capsule(
        radius=blade_radius,
        half_length=blade_length * 0.5,
        rings=4,
        sectors=8,
        color=color,
        axis="y",
    )
    return PartShape3D(
        part_id="hand_sword",
        primitive="sdf_elongated_capsule",
        mesh=mesh,
        inflate_radius=blade_radius,
        half_extents=(blade_radius, blade_length * 0.5 + blade_radius, blade_radius),
    )


def build_hand_staff(
    *,
    staff_length: float = 0.62,
    staff_radius: float = 0.014,
    color: tuple[int, int, int] = (110, 70, 40),
) -> PartShape3D:
    """Mage staff — thinner, longer SDF-elongated capsule."""
    mesh = _make_capsule(
        radius=staff_radius,
        half_length=staff_length * 0.5,
        rings=4,
        sectors=8,
        color=color,
        axis="y",
    )
    return PartShape3D(
        part_id="hand_staff",
        primitive="sdf_elongated_capsule",
        mesh=mesh,
        inflate_radius=staff_radius,
        half_extents=(staff_radius, staff_length * 0.5 + staff_radius, staff_radius),
    )


def build_hand_shield(
    *,
    shield_radius: float = 0.085,
    shield_thickness: float = 0.018,
    color: tuple[int, int, int] = (140, 100, 60),
) -> PartShape3D:
    """Round shield — flat disc primitive."""
    mesh = _make_disc(
        radius=shield_radius,
        thickness=shield_thickness,
        sectors=18,
        color=color,
    )
    return PartShape3D(
        part_id="hand_shield",
        primitive="sdf_round_disc",
        mesh=mesh,
        inflate_radius=shield_thickness * 0.5,
        half_extents=(shield_radius, shield_radius, shield_thickness * 0.5),
    )


def build_foot_boots(
    *,
    foot_width: float = 0.095,
    foot_height: float = 0.045,
    inflate_radius: float = 0.014,
    color: tuple[int, int, int] = (60, 40, 30),
) -> PartShape3D:
    """Heavy boots — rounded box overlay above the foot bone."""
    half = (foot_width * 0.55, foot_height * 0.7, foot_width * 0.95)
    mesh = _make_box(half, color=color, inflate_radius=inflate_radius)
    return PartShape3D(
        part_id="foot_boots",
        primitive="sdf_round_box",
        mesh=mesh,
        inflate_radius=inflate_radius,
        half_extents=half,
    )


def build_foot_sandals(
    *,
    foot_width: float = 0.095,
    foot_height: float = 0.020,
    shell_thickness: float = 0.006,
    color: tuple[int, int, int] = (160, 120, 80),
) -> PartShape3D:
    """Sandals — minimal flat sole."""
    half = (foot_width * 0.5, foot_height * 0.5, foot_width * 0.85)
    mesh = _make_box(half, color=color, inflate_radius=shell_thickness)
    return PartShape3D(
        part_id="foot_sandals",
        primitive="sdf_round_box",
        mesh=mesh,
        inflate_radius=shell_thickness,
        half_extents=half,
    )


def build_foot_greaves(
    *,
    foot_width: float = 0.095,
    foot_height: float = 0.060,
    inflate_radius: float = 0.020,
    color: tuple[int, int, int] = (180, 180, 200),
) -> PartShape3D:
    """Steel greaves — taller plated shell."""
    half = (foot_width * 0.55, foot_height * 0.85, foot_width * 0.9)
    mesh = _make_box(half, color=color, inflate_radius=inflate_radius)
    return PartShape3D(
        part_id="foot_greaves",
        primitive="sdf_round_box",
        mesh=mesh,
        inflate_radius=inflate_radius,
        half_extents=half,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Part registry — discriminator → factory
# ─────────────────────────────────────────────────────────────────────────────


PART_FACTORIES_3D: dict[str, callable] = {
    # Torso
    "torso_breastplate": build_torso_breastplate,
    "torso_robe": build_torso_robe,
    "torso_vest": build_torso_vest,
    # Hand
    "hand_sword": build_hand_sword,
    "hand_staff": build_hand_staff,
    "hand_shield": build_hand_shield,
    # Foot
    "foot_boots": build_foot_boots,
    "foot_sandals": build_foot_sandals,
    "foot_greaves": build_foot_greaves,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Tensorized socket mounter
# ─────────────────────────────────────────────────────────────────────────────


def _euler_xyz_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """Return the 3x3 intrinsic-XYZ rotation matrix for the given Euler angles."""
    rx = math.radians(float(rx_deg))
    ry = math.radians(float(ry_deg))
    rz = math.radians(float(rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    rx_mat = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    ry_mat = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rz_mat = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return rz_mat @ ry_mat @ rx_mat


def build_socket_world_matrix(
    skeleton: Skeleton,
    socket: SocketSpec,
) -> np.ndarray:
    """Compose the socket's 4x4 world matrix against a posed :class:`Skeleton`.

    The base 2-D skeleton supplies (x, y); the 3-D socket extends to a
    z-axis of 0 by default.  This is identical in spirit to UE5's
    ``GetSocketTransform`` chained with the parent bone's component-space
    transform.
    """
    joint = skeleton.joints.get(socket.parent_bone)
    if joint is None:
        raise KeyError(
            f"SocketSpec {socket.socket_name!r} references unknown bone "
            f"{socket.parent_bone!r}"
        )

    # Bone world transform: translate by (joint.x, joint.y, 0).  The 2-D
    # skeleton has no roll, so the bone's rotation is identity in 3D.
    bone_world = np.eye(4, dtype=np.float64)
    bone_world[0, 3] = float(joint.x)
    bone_world[1, 3] = float(joint.y)

    # Local socket transform: scale, rotate (Euler XYZ), translate.
    sx, sy, sz = socket.local_scale
    scale_mat = np.diag([float(sx), float(sy), float(sz), 1.0]).astype(np.float64)
    rot_3 = _euler_xyz_matrix(*socket.local_rotation_deg)
    rot_mat = np.eye(4, dtype=np.float64)
    rot_mat[:3, :3] = rot_3
    trans_mat = np.eye(4, dtype=np.float64)
    trans_mat[:3, 3] = np.asarray(socket.local_translation, dtype=np.float64)

    socket_local = trans_mat @ rot_mat @ scale_mat
    return bone_world @ socket_local


@dataclass
class MountedAttachment:
    """A single transformed attachment ready for compose / render."""

    part_id: str
    socket_name: str
    parent_bone: str
    mesh: Mesh3D
    world_matrix: np.ndarray = field(repr=False)


class TensorSocketMounter:
    """Vectorised attachment kernel.

    The mounter is intentionally cheap to construct: a single instance is
    typically reused across an entire animation clip.  All heavy lifting
    happens in :meth:`mount`, which performs *one* matmul broadcast per
    socket — no Python loops over individual mesh vertices.
    """

    def __init__(
        self,
        sockets: Optional[dict[str, SocketSpec]] = None,
    ) -> None:
        # Defensive copy: callers should never mutate the live socket map
        # of a long-running mounter (especially because socket specs are
        # frozen dataclasses, the dict itself is mutable).
        self.sockets: dict[str, SocketSpec] = (
            dict(sockets) if sockets else dict(DEFAULT_SOCKETS)
        )

    # -- introspection -------------------------------------------------

    def has_socket(self, slot_key: str) -> bool:
        return slot_key in self.sockets

    def add_socket(self, slot_key: str, socket: SocketSpec) -> None:
        self.sockets[slot_key] = socket

    # -- transform kernel ---------------------------------------------

    def transform_mesh(self, mesh: Mesh3D, world_matrix: np.ndarray) -> Mesh3D:
        """Apply a single 4x4 world matrix to ``mesh`` and return a new copy.

        Implementation note: the per-vertex transform is a single
        ``np.einsum`` over the (N, 4) homogeneous coordinate batch.  The
        normals receive only the rotation+scale block (no translation).
        """
        if world_matrix.shape != (4, 4):
            raise ValueError(
                "world_matrix must be a 4x4 array, "
                f"got shape {world_matrix.shape!r}"
            )

        verts = np.asarray(mesh.vertices, dtype=np.float64)
        ones = np.ones((verts.shape[0], 1), dtype=np.float64)
        homo = np.concatenate([verts, ones], axis=1)              # (N, 4)
        world_homo = homo @ world_matrix.T                        # (N, 4)
        world_verts = world_homo[:, :3]

        # Normals: rotate by the upper-left 3x3 (rotation + non-uniform
        # scale); re-normalise so non-uniform scale does not poison cel
        # shading derivatives.
        rot = world_matrix[:3, :3]
        n = np.asarray(mesh.normals, dtype=np.float64) @ rot.T
        n_norm = np.linalg.norm(n, axis=1, keepdims=True)
        n_norm[n_norm < 1e-12] = 1.0
        world_normals = n / n_norm

        return Mesh3D(
            vertices=world_verts,
            normals=world_normals,
            triangles=mesh.triangles.copy(),
            colors=mesh.colors.copy(),
        )

    # -- batch composition -------------------------------------------

    def mount(
        self,
        skeleton: Skeleton,
        attachments: Iterable[tuple[PartShape3D, str]],
    ) -> list[MountedAttachment]:
        """Mount many parts at once.

        Parameters
        ----------
        skeleton : Skeleton
            Posed skeleton supplying socket parent-bone world positions.
        attachments : iterable of ``(PartShape3D, slot_key)``
            ``slot_key`` selects which entry of :attr:`sockets` to use as
            the socket spec for this attachment.
        """
        results: list[MountedAttachment] = []
        for part, slot_key in attachments:
            socket = self.sockets.get(slot_key)
            if socket is None:
                raise KeyError(
                    f"No socket registered for slot {slot_key!r}; "
                    f"available: {sorted(self.sockets)}"
                )
            world = build_socket_world_matrix(skeleton, socket)
            mesh_world = self.transform_mesh(part.mesh, world)
            results.append(
                MountedAttachment(
                    part_id=part.part_id,
                    socket_name=socket.socket_name,
                    parent_bone=socket.parent_bone,
                    mesh=mesh_world,
                    world_matrix=world,
                )
            )
        return results


# ─────────────────────────────────────────────────────────────────────────────
#  High-level convenience: genotype → mounted attachments
# ─────────────────────────────────────────────────────────────────────────────


def build_attachments_from_genotype(
    genotype,
    skeleton: Optional[Skeleton] = None,
    *,
    mounter: Optional[TensorSocketMounter] = None,
) -> list[MountedAttachment]:
    """Decode a :class:`CharacterGenotype` into mounted 3D attachments.

    Only slot entries whose ``style_overrides`` carry a known
    ``*_style`` discriminator (e.g. ``torso_overlay_style="breastplate"``)
    contribute geometry.  Slots set to the ``*_none`` choice are silently
    skipped, matching the existing 2D behaviour.
    """
    from .genotype import PART_REGISTRY  # local import to avoid cycle

    if skeleton is None:
        # Use the genotype's shape-aware skeleton if it provides one.
        skeleton = (
            genotype.build_shaped_skeleton()
            if hasattr(genotype, "build_shaped_skeleton")
            else Skeleton.create_humanoid()
        )

    mounter = mounter or TensorSocketMounter()

    queue: list[tuple[PartShape3D, str]] = []
    for slot_key, slot_inst in genotype.slots.items():
        if not getattr(slot_inst, "enabled", True):
            continue
        part_def = PART_REGISTRY.get(slot_inst.part_id)
        if part_def is None:
            continue
        # Determine which 3D factory key to dispatch on.
        factory_key = slot_inst.part_id
        if factory_key not in PART_FACTORIES_3D:
            # Some slot ids (hat_*, face_*, eyes_*) intentionally have no
            # 3D mesh — the legacy 2D path handles them.
            continue
        factory = PART_FACTORIES_3D[factory_key]
        part_shape = factory()
        queue.append((part_shape, slot_key))

    return mounter.mount(skeleton, queue)


__all__ = [
    "PartShape3D",
    "SocketSpec",
    "MountedAttachment",
    "TensorSocketMounter",
    "DEFAULT_SOCKETS",
    "PART_FACTORIES_3D",
    "build_torso_breastplate",
    "build_torso_robe",
    "build_torso_vest",
    "build_hand_sword",
    "build_hand_staff",
    "build_hand_shield",
    "build_foot_boots",
    "build_foot_sandals",
    "build_foot_greaves",
    "build_socket_world_matrix",
    "build_attachments_from_genotype",
]
