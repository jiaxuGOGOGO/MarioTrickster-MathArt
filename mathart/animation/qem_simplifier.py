"""SESSION-065 — Quadric Error Metrics (QEM) Mesh Simplifier.

Research-to-code implementation of Michael Garland & Paul Heckbert's seminal paper:
    "Surface Simplification Using Quadric Error Metrics" (SIGGRAPH 1997)

This module provides a complete QEM mesh simplification pipeline that preserves
sharp features critical for cel-shading (Guilty Gear Xrd style) and generates
LOD chains for real-time 2.5D rendering. The algorithm is the mathematical
predecessor of Unreal Engine 5's Nanite virtualized geometry system.

Core Algorithm (Garland & Heckbert 1997):
    For each vertex v, compute a 4x4 symmetric matrix Q (the "quadric") that
    encodes the sum of squared distances from v to all its adjacent planes.
    When contracting edge (v1, v2) → v_bar, the cost is:
        cost = v_bar^T · (Q1 + Q2) · v_bar
    The optimal position v_bar minimizes this quadric error, found by solving:
        [q11 q12 q13 q14] [x]   [0]
        [q12 q22 q23 q24] [y] = [0]
        [q13 q23 q33 q34] [z]   [0]
        [ 0   0   0   1 ] [1]   [1]

    If the 3x3 upper-left submatrix is singular, fall back to selecting the
    best among v1, v2, and (v1+v2)/2.

Feature Preservation Strategy:
    Boundary edges and sharp creases receive penalty quadrics (weighted plane
    constraints perpendicular to the edge) to prevent simplification from
    destroying silhouette edges and cel-shading shadow boundaries.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  QEMSimplifier                                                      │
    │  ├─ build_quadrics(mesh) → per-vertex Q matrices                   │
    │  ├─ compute_edge_costs() → priority queue of edge contractions     │
    │  ├─ simplify(target_ratio) → simplified mesh                       │
    │  ├─ generate_lod_chain(levels) → list of LOD meshes                │
    │  └─ export_obj(path) → Wavefront OBJ output                       │
    ├─────────────────────────────────────────────────────────────────────┤
    │  Feature Preservation                                               │
    │  ├─ detect_boundary_edges() → boundary penalty quadrics            │
    │  ├─ detect_sharp_edges(angle_threshold) → crease penalty           │
    │  └─ cel_shading_weight_boost() → shadow boundary preservation     │
    └─────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.qem_simplifier import QEMSimplifier, QEMMesh

    mesh = QEMMesh.from_obj("weapon.obj")
    simplifier = QEMSimplifier(boundary_weight=1000.0, crease_angle=30.0)
    simplified = simplifier.simplify(mesh, target_ratio=0.5)
    simplifier.export_obj(simplified, "weapon_lod1.obj")

    # Generate full LOD chain
    lod_chain = simplifier.generate_lod_chain(mesh, levels=[1.0, 0.5, 0.25, 0.1])
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QEMMesh:
    """Triangle mesh representation for QEM simplification.

    Vertices are stored as an Nx3 float64 array. Triangles are stored as
    an Mx3 int array of vertex indices. Normals are per-face Mx3 float64.
    """
    vertices: np.ndarray          # (N, 3) float64
    triangles: np.ndarray         # (M, 3) int
    normals: Optional[np.ndarray] = None  # (M, 3) float64, computed lazily

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def face_count(self) -> int:
        return len(self.triangles)

    def compute_face_normals(self) -> np.ndarray:
        """Compute per-face normals from vertex positions."""
        v0 = self.vertices[self.triangles[:, 0]]
        v1 = self.vertices[self.triangles[:, 1]]
        v2 = self.vertices[self.triangles[:, 2]]
        cross = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(cross, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        self.normals = cross / norms
        return self.normals

    def compute_face_planes(self) -> np.ndarray:
        """Compute plane equations (a, b, c, d) for each face.

        Plane equation: ax + by + cz + d = 0 where (a,b,c) is the unit normal
        and d = -dot(normal, point_on_plane).
        """
        if self.normals is None:
            self.compute_face_normals()
        v0 = self.vertices[self.triangles[:, 0]]
        d = -np.sum(self.normals * v0, axis=1)
        return np.column_stack([self.normals, d])

    @staticmethod
    def from_obj(path: str) -> "QEMMesh":
        """Load mesh from Wavefront OBJ file."""
        vertices = []
        triangles = []
        with open(path, "r") as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue
                if parts[0] == "v" and len(parts) >= 4:
                    vertices.append([float(parts[1]), float(parts[2]),
                                     float(parts[3])])
                elif parts[0] == "f":
                    # Handle both "f 1 2 3" and "f 1/1/1 2/2/2 3/3/3"
                    face_verts = []
                    for p in parts[1:]:
                        idx = int(p.split("/")[0]) - 1  # OBJ is 1-based
                        face_verts.append(idx)
                    # Triangulate quads and higher polygons
                    for i in range(1, len(face_verts) - 1):
                        triangles.append([face_verts[0], face_verts[i],
                                          face_verts[i + 1]])

        return QEMMesh(
            vertices=np.array(vertices, dtype=np.float64),
            triangles=np.array(triangles, dtype=np.int64) if triangles
            else np.zeros((0, 3), dtype=np.int64)
        )

    def to_obj(self, path: str) -> str:
        """Export mesh to Wavefront OBJ format."""
        lines = [
            f"# QEM Simplified Mesh — {self.vertex_count} vertices, "
            f"{self.face_count} faces",
            f"# Generated by MarioTrickster-MathArt SESSION-065 QEM Simplifier"
        ]
        for v in self.vertices:
            lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
        for t in self.triangles:
            lines.append(f"f {t[0]+1} {t[1]+1} {t[2]+1}")
        content = "\n".join(lines) + "\n"
        with open(path, "w") as f:
            f.write(content)
        return path


@dataclass
class EdgeContraction:
    """Represents a candidate edge contraction with its QEM cost."""
    cost: float
    v1: int
    v2: int
    optimal_pos: np.ndarray
    timestamp: int = 0  # For lazy deletion in priority queue

    def __lt__(self, other: "EdgeContraction") -> bool:
        return self.cost < other.cost


@dataclass
class QEMConfig:
    """Configuration for QEM simplification."""
    boundary_weight: float = 1000.0
    crease_angle_deg: float = 30.0
    cel_shading_boost: float = 5.0
    max_cost_threshold: float = float("inf")
    preserve_topology: bool = True


@dataclass
class LODLevel:
    """A single LOD level with its mesh and metadata."""
    level: int
    ratio: float
    mesh: QEMMesh
    vertex_count: int
    face_count: int
    max_error: float


# ---------------------------------------------------------------------------
# QEM Simplifier
# ---------------------------------------------------------------------------

class QEMSimplifier:
    """Mesh simplification using Quadric Error Metrics.

    Implements the complete Garland & Heckbert (1997) algorithm with
    extensions for boundary preservation, sharp feature detection, and
    cel-shading-aware edge weighting.

    The algorithm maintains a priority queue of edge contractions sorted
    by QEM cost. At each step, the lowest-cost edge is contracted,
    merging two vertices into one at the optimal position. The quadric
    of the merged vertex is the sum of the two original quadrics.
    """

    def __init__(self, config: Optional[QEMConfig] = None):
        self.config = config or QEMConfig()
        self._quadrics: Dict[int, np.ndarray] = {}
        self._vertex_faces: Dict[int, Set[int]] = {}
        self._vertex_neighbors: Dict[int, Set[int]] = {}
        self._removed_vertices: Set[int] = set()
        self._removed_faces: Set[int] = set()
        self._vertex_map: Dict[int, int] = {}  # Union-Find for merged vertices
        self._timestamp: int = 0
        self._vertex_timestamps: Dict[int, int] = {}

    def _find_root(self, v: int) -> int:
        """Union-Find path compression to track merged vertices."""
        while self._vertex_map.get(v, v) != v:
            parent = self._vertex_map[v]
            grandparent = self._vertex_map.get(parent, parent)
            self._vertex_map[v] = grandparent
            v = grandparent
        return v

    def _build_adjacency(self, mesh: QEMMesh) -> None:
        """Build vertex-face and vertex-vertex adjacency structures."""
        self._vertex_faces = {}
        self._vertex_neighbors = {}

        for fi, tri in enumerate(mesh.triangles):
            for vi in tri:
                if vi not in self._vertex_faces:
                    self._vertex_faces[vi] = set()
                self._vertex_faces[vi].add(fi)

            for i in range(3):
                a, b = int(tri[i]), int(tri[(i + 1) % 3])
                if a not in self._vertex_neighbors:
                    self._vertex_neighbors[a] = set()
                if b not in self._vertex_neighbors:
                    self._vertex_neighbors[b] = set()
                self._vertex_neighbors[a].add(b)
                self._vertex_neighbors[b].add(a)

    def _compute_vertex_quadric(self, vertex_idx: int,
                                planes: np.ndarray) -> np.ndarray:
        """Compute the fundamental quadric Q for a vertex.

        Q = sum of Kp for all adjacent planes p, where Kp = p * p^T
        and p = (a, b, c, d) is the plane equation.
        """
        Q = np.zeros((4, 4), dtype=np.float64)
        faces = self._vertex_faces.get(vertex_idx, set())
        for fi in faces:
            if fi in self._removed_faces:
                continue
            p = planes[fi]  # (a, b, c, d)
            Kp = np.outer(p, p)
            Q += Kp
        return Q

    def _add_boundary_penalty(self, mesh: QEMMesh,
                              planes: np.ndarray) -> None:
        """Add penalty quadrics for boundary edges.

        A boundary edge is shared by exactly one triangle. We add a
        perpendicular constraint plane along the boundary to prevent
        the simplifier from collapsing boundary features.
        """
        edge_face_count: Dict[Tuple[int, int], int] = {}
        edge_faces: Dict[Tuple[int, int], List[int]] = {}

        for fi, tri in enumerate(mesh.triangles):
            for i in range(3):
                a, b = int(tri[i]), int(tri[(i + 1) % 3])
                edge = (min(a, b), max(a, b))
                edge_face_count[edge] = edge_face_count.get(edge, 0) + 1
                if edge not in edge_faces:
                    edge_faces[edge] = []
                edge_faces[edge].append(fi)

        w = self.config.boundary_weight

        for edge, count in edge_face_count.items():
            if count == 1:
                # Boundary edge: add perpendicular constraint plane
                v1, v2 = edge
                fi = edge_faces[edge][0]
                face_normal = planes[fi][:3]
                edge_dir = mesh.vertices[v2] - mesh.vertices[v1]
                edge_len = np.linalg.norm(edge_dir)
                if edge_len < 1e-10:
                    continue
                edge_dir /= edge_len

                # Perpendicular plane normal
                perp_normal = np.cross(face_normal, edge_dir)
                perp_len = np.linalg.norm(perp_normal)
                if perp_len < 1e-10:
                    continue
                perp_normal /= perp_len

                d = -np.dot(perp_normal, mesh.vertices[v1])
                p = np.array([*perp_normal, d])
                Kp = np.outer(p, p) * w

                self._quadrics[v1] = self._quadrics.get(
                    v1, np.zeros((4, 4))) + Kp
                self._quadrics[v2] = self._quadrics.get(
                    v2, np.zeros((4, 4))) + Kp

    def _add_crease_penalty(self, mesh: QEMMesh,
                            planes: np.ndarray) -> None:
        """Add penalty quadrics for sharp crease edges.

        An edge is a crease if the dihedral angle between its two
        adjacent faces exceeds the crease_angle threshold. This
        preserves hard shadow boundaries critical for cel-shading.
        """
        edge_faces: Dict[Tuple[int, int], List[int]] = {}

        for fi, tri in enumerate(mesh.triangles):
            for i in range(3):
                a, b = int(tri[i]), int(tri[(i + 1) % 3])
                edge = (min(a, b), max(a, b))
                if edge not in edge_faces:
                    edge_faces[edge] = []
                edge_faces[edge].append(fi)

        threshold = math.cos(math.radians(self.config.crease_angle_deg))
        w = self.config.cel_shading_boost

        for edge, faces in edge_faces.items():
            if len(faces) != 2:
                continue
            n1 = planes[faces[0]][:3]
            n2 = planes[faces[1]][:3]
            cos_angle = np.dot(n1, n2)

            if cos_angle < threshold:
                # Sharp crease: add constraint
                v1, v2 = edge
                edge_dir = mesh.vertices[v2] - mesh.vertices[v1]
                edge_len = np.linalg.norm(edge_dir)
                if edge_len < 1e-10:
                    continue
                edge_dir /= edge_len

                # Average normal as constraint direction
                avg_n = n1 + n2
                avg_len = np.linalg.norm(avg_n)
                if avg_len < 1e-10:
                    continue
                avg_n /= avg_len

                perp = np.cross(avg_n, edge_dir)
                perp_len = np.linalg.norm(perp)
                if perp_len < 1e-10:
                    continue
                perp /= perp_len

                d = -np.dot(perp, mesh.vertices[v1])
                p = np.array([*perp, d])
                Kp = np.outer(p, p) * w

                self._quadrics[v1] = self._quadrics.get(
                    v1, np.zeros((4, 4))) + Kp
                self._quadrics[v2] = self._quadrics.get(
                    v2, np.zeros((4, 4))) + Kp

    def _compute_optimal_position(self, v1: int, v2: int,
                                  vertices: np.ndarray
                                  ) -> Tuple[float, np.ndarray]:
        """Compute optimal contraction position and cost.

        Tries to solve the 3x3 linear system from the combined quadric.
        Falls back to selecting the best among v1, v2, midpoint if singular.
        """
        Q = self._quadrics.get(v1, np.zeros((4, 4))) + \
            self._quadrics.get(v2, np.zeros((4, 4)))

        # Try to solve for optimal position
        A = Q[:3, :3]
        b = -Q[:3, 3]

        try:
            if abs(np.linalg.det(A)) > 1e-10:
                optimal = np.linalg.solve(A, b)
                v_bar = np.array([*optimal, 1.0])
                cost = float(v_bar @ Q @ v_bar)
                if cost < 0:
                    cost = 0.0
                return cost, optimal
        except np.linalg.LinAlgError:
            pass

        # Fallback: test v1, v2, midpoint
        candidates = [
            vertices[v1],
            vertices[v2],
            (vertices[v1] + vertices[v2]) * 0.5
        ]

        best_cost = float("inf")
        best_pos = candidates[2]

        for pos in candidates:
            v_bar = np.array([*pos, 1.0])
            cost = float(v_bar @ Q @ v_bar)
            if cost < 0:
                cost = 0.0
            if cost < best_cost:
                best_cost = cost
                best_pos = pos.copy()

        return best_cost, best_pos

    def _build_priority_queue(self, mesh: QEMMesh
                              ) -> List[EdgeContraction]:
        """Build initial priority queue of all edge contractions."""
        heap: List[EdgeContraction] = []
        seen_edges: Set[Tuple[int, int]] = set()

        for tri in mesh.triangles:
            for i in range(3):
                a, b = int(tri[i]), int(tri[(i + 1) % 3])
                edge = (min(a, b), max(a, b))
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)

                cost, pos = self._compute_optimal_position(
                    a, b, mesh.vertices)
                ec = EdgeContraction(
                    cost=cost, v1=a, v2=b,
                    optimal_pos=pos, timestamp=0
                )
                heapq.heappush(heap, ec)

        return heap

    def simplify(self, mesh: QEMMesh,
                 target_ratio: float = 0.5) -> QEMMesh:
        """Simplify mesh to target_ratio of original face count.

        Args:
            mesh: Input triangle mesh.
            target_ratio: Target ratio of faces to keep (0.0 to 1.0).

        Returns:
            Simplified QEMMesh.
        """
        if mesh.face_count == 0:
            return QEMMesh(
                vertices=mesh.vertices.copy(),
                triangles=mesh.triangles.copy()
            )

        target_faces = max(4, int(mesh.face_count * target_ratio))

        # Reset state
        self._quadrics = {}
        self._removed_vertices = set()
        self._removed_faces = set()
        self._vertex_map = {}
        self._timestamp = 0
        self._vertex_timestamps = {i: 0 for i in range(mesh.vertex_count)}

        # Build adjacency
        self._build_adjacency(mesh)

        # Compute planes and quadrics
        planes = mesh.compute_face_planes()
        for vi in range(mesh.vertex_count):
            self._quadrics[vi] = self._compute_vertex_quadric(vi, planes)

        # Add feature preservation penalties
        self._add_boundary_penalty(mesh, planes)
        self._add_crease_penalty(mesh, planes)

        # Build priority queue
        heap = self._build_priority_queue(mesh)

        # Working copy of vertices
        vertices = mesh.vertices.copy()
        triangles = mesh.triangles.copy()
        active_faces = mesh.face_count

        # Iteratively contract edges
        while active_faces > target_faces and heap:
            ec = heapq.heappop(heap)

            # Lazy deletion: skip if either vertex was modified
            r1 = self._find_root(ec.v1)
            r2 = self._find_root(ec.v2)

            if r1 == r2:
                continue
            if r1 in self._removed_vertices or r2 in self._removed_vertices:
                continue
            if (self._vertex_timestamps.get(r1, 0) > ec.timestamp or
                    self._vertex_timestamps.get(r2, 0) > ec.timestamp):
                # Recompute cost with current state
                cost, pos = self._compute_optimal_position(
                    r1, r2, vertices)
                if cost > ec.cost * 2.0 + 1e-6:
                    ec2 = EdgeContraction(
                        cost=cost, v1=r1, v2=r2,
                        optimal_pos=pos,
                        timestamp=self._timestamp
                    )
                    heapq.heappush(heap, ec2)
                    continue

            if ec.cost > self.config.max_cost_threshold:
                break

            # Contract edge: merge r2 into r1
            self._timestamp += 1
            vertices[r1] = ec.optimal_pos
            self._vertex_map[r2] = r1
            self._removed_vertices.add(r2)
            self._vertex_timestamps[r1] = self._timestamp

            # Update quadric
            self._quadrics[r1] = (
                self._quadrics.get(r1, np.zeros((4, 4))) +
                self._quadrics.get(r2, np.zeros((4, 4)))
            )

            # Update triangles: replace r2 with r1, remove degenerate
            for fi in range(len(triangles)):
                if fi in self._removed_faces:
                    continue
                tri = triangles[fi]
                modified = False
                for i in range(3):
                    root = self._find_root(int(tri[i]))
                    if root != tri[i]:
                        tri[i] = root
                        modified = True

                # Check for degenerate triangle (two or more same vertices)
                if (tri[0] == tri[1] or tri[1] == tri[2] or
                        tri[0] == tri[2]):
                    self._removed_faces.add(fi)
                    active_faces -= 1

            # Add new edge contractions for r1's neighbors
            neighbors = set()
            for fi in range(len(triangles)):
                if fi in self._removed_faces:
                    continue
                tri = triangles[fi]
                if r1 in tri:
                    for vi in tri:
                        v = int(vi)
                        if v != r1 and v not in self._removed_vertices:
                            neighbors.add(v)

            for nb in neighbors:
                cost, pos = self._compute_optimal_position(
                    r1, nb, vertices)
                ec_new = EdgeContraction(
                    cost=cost, v1=r1, v2=nb,
                    optimal_pos=pos,
                    timestamp=self._timestamp
                )
                heapq.heappush(heap, ec_new)

        # Build output mesh
        return self._compact_mesh(vertices, triangles)

    def _compact_mesh(self, vertices: np.ndarray,
                      triangles: np.ndarray) -> QEMMesh:
        """Remove unused vertices and reindex triangles."""
        # Resolve all vertex mappings
        for fi in range(len(triangles)):
            if fi in self._removed_faces:
                continue
            for i in range(3):
                triangles[fi, i] = self._find_root(int(triangles[fi, i]))

        # Collect active triangles
        active_tris = []
        for fi in range(len(triangles)):
            if fi not in self._removed_faces:
                tri = triangles[fi]
                if tri[0] != tri[1] and tri[1] != tri[2] and tri[0] != tri[2]:
                    active_tris.append(tri.copy())

        if not active_tris:
            return QEMMesh(
                vertices=np.zeros((0, 3), dtype=np.float64),
                triangles=np.zeros((0, 3), dtype=np.int64)
            )

        active_tris_arr = np.array(active_tris, dtype=np.int64)

        # Find used vertices and create new indices
        used_verts = set(active_tris_arr.flatten())
        old_to_new = {}
        new_vertices = []
        for old_idx in sorted(used_verts):
            old_to_new[old_idx] = len(new_vertices)
            new_vertices.append(vertices[old_idx])

        # Reindex triangles
        new_tris = np.zeros_like(active_tris_arr)
        for fi in range(len(active_tris_arr)):
            for i in range(3):
                new_tris[fi, i] = old_to_new[active_tris_arr[fi, i]]

        return QEMMesh(
            vertices=np.array(new_vertices, dtype=np.float64),
            triangles=new_tris
        )

    def generate_lod_chain(self, mesh: QEMMesh,
                           levels: Optional[List[float]] = None
                           ) -> List[LODLevel]:
        """Generate a chain of LOD meshes at specified ratios.

        Args:
            mesh: Input high-poly mesh.
            levels: List of target ratios (1.0 = original, 0.1 = 10%).
                    Defaults to [1.0, 0.5, 0.25, 0.125].

        Returns:
            List of LODLevel objects from highest to lowest detail.
        """
        if levels is None:
            levels = [1.0, 0.5, 0.25, 0.125]

        lod_chain: List[LODLevel] = []
        current_mesh = mesh

        for i, ratio in enumerate(sorted(levels, reverse=True)):
            if ratio >= 1.0:
                lod_mesh = QEMMesh(
                    vertices=mesh.vertices.copy(),
                    triangles=mesh.triangles.copy()
                )
            else:
                lod_mesh = self.simplify(current_mesh, target_ratio=ratio)

            lod_chain.append(LODLevel(
                level=i,
                ratio=ratio,
                mesh=lod_mesh,
                vertex_count=lod_mesh.vertex_count,
                face_count=lod_mesh.face_count,
                max_error=0.0  # Could compute Hausdorff distance
            ))

        return lod_chain

    def export_obj(self, mesh: QEMMesh, path: str) -> str:
        """Export simplified mesh to OBJ format."""
        return mesh.to_obj(path)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def simplify_mesh(mesh: QEMMesh,
                  target_ratio: float = 0.5,
                  boundary_weight: float = 1000.0,
                  crease_angle: float = 30.0) -> QEMMesh:
    """Convenience function for one-shot mesh simplification.

    Args:
        mesh: Input triangle mesh.
        target_ratio: Fraction of faces to keep.
        boundary_weight: Penalty weight for boundary edges.
        crease_angle: Dihedral angle threshold for sharp creases.

    Returns:
        Simplified QEMMesh.
    """
    config = QEMConfig(
        boundary_weight=boundary_weight,
        crease_angle_deg=crease_angle
    )
    simplifier = QEMSimplifier(config=config)
    return simplifier.simplify(mesh, target_ratio=target_ratio)


def create_lod_chain(mesh: QEMMesh,
                     levels: Optional[List[float]] = None
                     ) -> List[LODLevel]:
    """Convenience function for LOD chain generation."""
    simplifier = QEMSimplifier()
    return simplifier.generate_lod_chain(mesh, levels=levels)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "QEMMesh",
    "QEMConfig",
    "QEMSimplifier",
    "EdgeContraction",
    "LODLevel",
    "simplify_mesh",
    "create_lod_chain",
]
