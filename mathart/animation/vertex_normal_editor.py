"""SESSION-065 — Vertex Normal Editor for Cel-Shading Shadow Control.

Research-to-code implementation distilled from:
    Junya Motomura (Arc System Works), GDC 2015:
    "Guilty Gear Xrd's Art Style: The X Factor Between 2D and 3D"

Core Insight (Motomura 2015):
    Industrial-grade 2.5D cel-shading does NOT rely on physically-based
    lighting calculations. Instead, shadow boundaries are controlled by
    **manually edited vertex normals**. By redirecting normals, artists
    can place hard shadow edges exactly where they want them, regardless
    of the actual surface geometry.

    This is the single most important technique that separates
    professional cel-shading (Guilty Gear, Dragon Ball FighterZ,
    Genshin Impact) from amateur "toon shader" attempts.

Techniques Implemented:
    1. **Normal Transfer**: Copy normals from a proxy shape (sphere,
       cylinder) to the actual mesh, creating smooth shadow gradients
       on angular geometry.
    2. **Normal Smoothing by Group**: Average normals within artist-
       defined groups to eliminate unwanted shadow splits at hard edges.
    3. **Shadow Threshold Painting**: Per-vertex shadow bias stored in
       vertex color channel, allowing fine-grained shadow placement.
    4. **Rim Light Normal Adjustment**: Separate normal set for rim
       lighting that can differ from shadow normals.
    5. **Normal Baking**: Export edited normals as a normal map texture
       for use in standard rendering pipelines.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  VertexNormalEditor                                                  │
    │  ├─ transfer_normals_from_proxy(mesh, proxy_shape)                  │
    │  ├─ smooth_normals_by_group(mesh, groups)                           │
    │  ├─ paint_shadow_threshold(mesh, vertex_weights)                    │
    │  ├─ adjust_rim_normals(mesh, rim_direction, strength)               │
    │  ├─ compute_cel_shadow_boundary(mesh, light_dir, threshold)         │
    │  └─ bake_normal_map(mesh, resolution) → normal map texture          │
    ├─────────────────────────────────────────────────────────────────────┤
    │  ProxyShape                                                          │
    │  ├─ Sphere, Cylinder, Capsule, Custom SDF                           │
    │  └─ compute_normal_at(point) → direction for normal transfer        │
    └─────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.vertex_normal_editor import (
        VertexNormalEditor, ProxyShape, ShadowConfig
    )

    editor = VertexNormalEditor()
    # Transfer normals from a sphere proxy for smooth head shading
    edited = editor.transfer_normals_from_proxy(
        mesh, ProxyShape.sphere(center=[0, 1.5, 0], radius=0.3)
    )
    # Smooth normals across hard edges in the body group
    edited = editor.smooth_normals_by_group(edited, body_vertex_indices)
    # Compute shadow boundary for preview
    boundary = editor.compute_cel_shadow_boundary(
        edited, light_dir=[0.5, -1, 0.3], threshold=0.5
    )
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Proxy Shapes for Normal Transfer
# ---------------------------------------------------------------------------

class ProxyShapeType(Enum):
    """Types of proxy shapes for normal transfer."""
    SPHERE = "sphere"
    CYLINDER = "cylinder"
    CAPSULE = "capsule"
    CUSTOM_SDF = "custom_sdf"


@dataclass
class ProxyShape:
    """A proxy shape used for normal transfer.

    The proxy shape defines a smooth surface whose normals are transferred
    to the target mesh vertices. This is the core technique from GGXrd:
    by using a simple smooth proxy, angular mesh geometry receives smooth
    normals that produce clean cel-shading shadow boundaries.
    """
    shape_type: ProxyShapeType
    center: np.ndarray = field(default_factory=lambda: np.zeros(3))
    radius: float = 1.0
    axis: np.ndarray = field(default_factory=lambda: np.array([0., 1., 0.]))
    height: float = 2.0
    sdf_func: Optional[Callable] = None

    @staticmethod
    def sphere(center: Optional[List[float]] = None,
               radius: float = 1.0) -> "ProxyShape":
        """Create a sphere proxy shape."""
        c = np.array(center or [0, 0, 0], dtype=np.float64)
        return ProxyShape(
            shape_type=ProxyShapeType.SPHERE,
            center=c, radius=radius
        )

    @staticmethod
    def cylinder(center: Optional[List[float]] = None,
                 radius: float = 0.5,
                 axis: Optional[List[float]] = None,
                 height: float = 2.0) -> "ProxyShape":
        """Create a cylinder proxy shape."""
        c = np.array(center or [0, 0, 0], dtype=np.float64)
        a = np.array(axis or [0, 1, 0], dtype=np.float64)
        a_len = np.linalg.norm(a)
        if a_len > 1e-10:
            a /= a_len
        return ProxyShape(
            shape_type=ProxyShapeType.CYLINDER,
            center=c, radius=radius, axis=a, height=height
        )

    @staticmethod
    def capsule(center: Optional[List[float]] = None,
                radius: float = 0.5,
                axis: Optional[List[float]] = None,
                height: float = 2.0) -> "ProxyShape":
        """Create a capsule proxy shape."""
        c = np.array(center or [0, 0, 0], dtype=np.float64)
        a = np.array(axis or [0, 1, 0], dtype=np.float64)
        a_len = np.linalg.norm(a)
        if a_len > 1e-10:
            a /= a_len
        return ProxyShape(
            shape_type=ProxyShapeType.CAPSULE,
            center=c, radius=radius, axis=a, height=height
        )

    def compute_normal_at(self, point: np.ndarray) -> np.ndarray:
        """Compute the proxy surface normal at the closest point.

        This is the key operation: for any mesh vertex position, we find
        the closest point on the proxy surface and return its normal.
        The mesh vertex then receives this smooth normal instead of its
        geometric normal.
        """
        if self.shape_type == ProxyShapeType.SPHERE:
            return self._sphere_normal(point)
        elif self.shape_type == ProxyShapeType.CYLINDER:
            return self._cylinder_normal(point)
        elif self.shape_type == ProxyShapeType.CAPSULE:
            return self._capsule_normal(point)
        elif self.shape_type == ProxyShapeType.CUSTOM_SDF:
            return self._sdf_normal(point)
        else:
            return np.array([0, 1, 0], dtype=np.float64)

    def _sphere_normal(self, point: np.ndarray) -> np.ndarray:
        """Normal from sphere center to point."""
        d = point - self.center
        length = np.linalg.norm(d)
        if length < 1e-10:
            return np.array([0, 1, 0], dtype=np.float64)
        return d / length

    def _cylinder_normal(self, point: np.ndarray) -> np.ndarray:
        """Normal perpendicular to cylinder axis."""
        d = point - self.center
        proj = np.dot(d, self.axis) * self.axis
        radial = d - proj
        length = np.linalg.norm(radial)
        if length < 1e-10:
            # Point is on the axis; pick arbitrary perpendicular
            perp = np.cross(self.axis, np.array([1, 0, 0]))
            if np.linalg.norm(perp) < 1e-10:
                perp = np.cross(self.axis, np.array([0, 0, 1]))
            return perp / np.linalg.norm(perp)
        return radial / length

    def _capsule_normal(self, point: np.ndarray) -> np.ndarray:
        """Normal from capsule medial axis to point."""
        d = point - self.center
        t = np.dot(d, self.axis)
        half_h = self.height * 0.5
        t_clamped = np.clip(t, -half_h, half_h)
        closest = self.center + t_clamped * self.axis
        n = point - closest
        length = np.linalg.norm(n)
        if length < 1e-10:
            return self.axis.copy()
        return n / length

    def _sdf_normal(self, point: np.ndarray) -> np.ndarray:
        """Normal from SDF gradient (central differences)."""
        if self.sdf_func is None:
            return np.array([0, 1, 0], dtype=np.float64)
        eps = 1e-4
        f = self.sdf_func
        gx = f(point[0] + eps, point[1], point[2]) - \
             f(point[0] - eps, point[1], point[2])
        gy = f(point[0], point[1] + eps, point[2]) - \
             f(point[0], point[1] - eps, point[2])
        gz = f(point[0], point[1], point[2] + eps) - \
             f(point[0], point[1], point[2] - eps)
        g = np.array([gx, gy, gz])
        length = np.linalg.norm(g)
        if length < 1e-10:
            return np.array([0, 1, 0], dtype=np.float64)
        return g / length


# ---------------------------------------------------------------------------
# Shadow Configuration
# ---------------------------------------------------------------------------

@dataclass
class ShadowConfig:
    """Configuration for cel-shading shadow computation.

    Based on Guilty Gear Xrd's shadow system:
    - threshold: NdotL value below which shadow appears
    - softness: transition width (0 = perfectly hard edge)
    - tint: shadow color multiplier
    - vertex_bias_channel: which vertex color channel stores per-vertex bias
    """
    threshold: float = 0.5
    softness: float = 0.01
    tint: Tuple[float, float, float] = (0.7, 0.5, 0.8)
    vertex_bias_channel: int = 0  # R channel of vertex color
    rim_threshold: float = 0.3
    rim_power: float = 2.0


# ---------------------------------------------------------------------------
# Edited Mesh with Custom Normals
# ---------------------------------------------------------------------------

@dataclass
class EditedMesh:
    """Mesh with edited vertex normals and shadow metadata.

    Stores both the original geometric normals and the edited normals
    used for cel-shading. Also stores per-vertex shadow bias values
    and optional rim light normals.
    """
    vertices: np.ndarray           # (N, 3) positions
    triangles: np.ndarray          # (M, 3) indices
    geometric_normals: np.ndarray  # (N, 3) original normals
    edited_normals: np.ndarray     # (N, 3) edited normals for shadow
    shadow_bias: np.ndarray        # (N,) per-vertex shadow threshold bias
    rim_normals: Optional[np.ndarray] = None  # (N, 3) optional rim normals
    vertex_groups: Optional[Dict[str, Set[int]]] = None

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def face_count(self) -> int:
        return len(self.triangles)


# ---------------------------------------------------------------------------
# Vertex Normal Editor
# ---------------------------------------------------------------------------

class VertexNormalEditor:
    """Editor for vertex normals to control cel-shading shadow placement.

    This is the core tool for achieving Arc System Works-quality cel-shading.
    Instead of relying on geometric normals (which produce noisy, unpredictable
    shadows on low-poly models), we edit normals to create clean, art-directed
    shadow boundaries.
    """

    def __init__(self, shadow_config: Optional[ShadowConfig] = None):
        self.shadow_config = shadow_config or ShadowConfig()

    def compute_geometric_normals(self, vertices: np.ndarray,
                                  triangles: np.ndarray) -> np.ndarray:
        """Compute area-weighted vertex normals from geometry."""
        normals = np.zeros_like(vertices)

        for tri in triangles:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            face_normal = np.cross(v1 - v0, v2 - v0)
            area = np.linalg.norm(face_normal)
            if area > 1e-10:
                face_normal /= area  # Normalize but keep area as weight
                for vi in tri:
                    normals[vi] += face_normal * area

        # Normalize vertex normals
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths = np.maximum(lengths, 1e-10)
        normals /= lengths

        return normals

    def transfer_normals_from_proxy(
        self, vertices: np.ndarray, triangles: np.ndarray,
        proxy: ProxyShape,
        vertex_mask: Optional[Set[int]] = None,
        blend_weight: float = 1.0
    ) -> EditedMesh:
        """Transfer normals from a proxy shape to mesh vertices.

        This is the primary technique from GGXrd. For each vertex in the
        mesh (or in the specified mask), we compute the proxy surface
        normal at that vertex's position and use it as the vertex normal.

        Args:
            vertices: (N, 3) vertex positions.
            triangles: (M, 3) triangle indices.
            proxy: Proxy shape to transfer normals from.
            vertex_mask: Optional set of vertex indices to affect.
                         If None, all vertices are affected.
            blend_weight: Blend factor between geometric and proxy normals.
                          0.0 = pure geometric, 1.0 = pure proxy.

        Returns:
            EditedMesh with transferred normals.
        """
        geo_normals = self.compute_geometric_normals(vertices, triangles)
        edited = geo_normals.copy()

        affected = vertex_mask or set(range(len(vertices)))

        for vi in affected:
            proxy_normal = proxy.compute_normal_at(vertices[vi])
            edited[vi] = (1.0 - blend_weight) * geo_normals[vi] + \
                         blend_weight * proxy_normal
            length = np.linalg.norm(edited[vi])
            if length > 1e-10:
                edited[vi] /= length

        return EditedMesh(
            vertices=vertices.copy(),
            triangles=triangles.copy(),
            geometric_normals=geo_normals,
            edited_normals=edited,
            shadow_bias=np.zeros(len(vertices), dtype=np.float64)
        )

    def smooth_normals_by_group(
        self, edited_mesh: EditedMesh,
        vertex_indices: Set[int],
        iterations: int = 3,
        strength: float = 0.5
    ) -> EditedMesh:
        """Smooth normals within a vertex group using Laplacian smoothing.

        This eliminates unwanted shadow splits at hard edges within a
        group (e.g., across the seam of a character's body mesh) while
        preserving shadow boundaries between groups.

        Args:
            edited_mesh: Mesh with current edited normals.
            vertex_indices: Set of vertex indices in the group.
            iterations: Number of smoothing iterations.
            strength: Smoothing strength per iteration (0-1).

        Returns:
            EditedMesh with smoothed normals in the group.
        """
        # Build adjacency within the group
        neighbors: Dict[int, Set[int]] = {vi: set() for vi in vertex_indices}
        for tri in edited_mesh.triangles:
            for i in range(3):
                a, b = int(tri[i]), int(tri[(i + 1) % 3])
                if a in vertex_indices and b in vertex_indices:
                    neighbors[a].add(b)
                    neighbors[b].add(a)

        normals = edited_mesh.edited_normals.copy()

        for _ in range(iterations):
            new_normals = normals.copy()
            for vi in vertex_indices:
                if not neighbors[vi]:
                    continue
                avg = np.zeros(3, dtype=np.float64)
                for ni in neighbors[vi]:
                    avg += normals[ni]
                avg /= len(neighbors[vi])

                new_normals[vi] = (1.0 - strength) * normals[vi] + \
                                  strength * avg
                length = np.linalg.norm(new_normals[vi])
                if length > 1e-10:
                    new_normals[vi] /= length

            normals = new_normals

        result = EditedMesh(
            vertices=edited_mesh.vertices.copy(),
            triangles=edited_mesh.triangles.copy(),
            geometric_normals=edited_mesh.geometric_normals.copy(),
            edited_normals=normals,
            shadow_bias=edited_mesh.shadow_bias.copy(),
            rim_normals=edited_mesh.rim_normals.copy()
            if edited_mesh.rim_normals is not None else None,
            vertex_groups=edited_mesh.vertex_groups
        )
        return result

    def paint_shadow_threshold(
        self, edited_mesh: EditedMesh,
        vertex_weights: Dict[int, float]
    ) -> EditedMesh:
        """Paint per-vertex shadow threshold bias.

        In GGXrd, vertex color channels store shadow bias values that
        shift the NdotL threshold per-vertex. This allows artists to
        push shadows deeper into creases or pull them onto flat surfaces.

        Args:
            edited_mesh: Mesh with current edited normals.
            vertex_weights: Dict mapping vertex index to bias value [-1, 1].
                           Positive = more shadow, Negative = less shadow.

        Returns:
            EditedMesh with updated shadow bias.
        """
        bias = edited_mesh.shadow_bias.copy()
        for vi, weight in vertex_weights.items():
            if 0 <= vi < len(bias):
                bias[vi] = np.clip(weight, -1.0, 1.0)

        result = EditedMesh(
            vertices=edited_mesh.vertices.copy(),
            triangles=edited_mesh.triangles.copy(),
            geometric_normals=edited_mesh.geometric_normals.copy(),
            edited_normals=edited_mesh.edited_normals.copy(),
            shadow_bias=bias,
            rim_normals=edited_mesh.rim_normals.copy()
            if edited_mesh.rim_normals is not None else None,
            vertex_groups=edited_mesh.vertex_groups
        )
        return result

    def adjust_rim_normals(
        self, edited_mesh: EditedMesh,
        rim_direction: np.ndarray,
        strength: float = 0.5
    ) -> EditedMesh:
        """Adjust normals for rim lighting separately from shadow normals.

        GGXrd uses separate normal sets for shadow and rim lighting.
        Rim normals are biased toward the camera/rim direction to create
        consistent rim light outlines regardless of geometry complexity.

        Args:
            edited_mesh: Mesh with current edited normals.
            rim_direction: Direction to bias rim normals toward.
            strength: Blend strength (0 = shadow normals, 1 = full rim bias).

        Returns:
            EditedMesh with separate rim normals.
        """
        rim_dir = np.array(rim_direction, dtype=np.float64)
        rim_len = np.linalg.norm(rim_dir)
        if rim_len > 1e-10:
            rim_dir /= rim_len

        rim_normals = edited_mesh.edited_normals.copy()

        for vi in range(len(rim_normals)):
            biased = (1.0 - strength) * rim_normals[vi] + strength * rim_dir
            length = np.linalg.norm(biased)
            if length > 1e-10:
                rim_normals[vi] = biased / length

        result = EditedMesh(
            vertices=edited_mesh.vertices.copy(),
            triangles=edited_mesh.triangles.copy(),
            geometric_normals=edited_mesh.geometric_normals.copy(),
            edited_normals=edited_mesh.edited_normals.copy(),
            shadow_bias=edited_mesh.shadow_bias.copy(),
            rim_normals=rim_normals,
            vertex_groups=edited_mesh.vertex_groups
        )
        return result

    def compute_cel_shadow_boundary(
        self, edited_mesh: EditedMesh,
        light_dir: List[float],
        threshold: Optional[float] = None
    ) -> np.ndarray:
        """Compute per-vertex shadow state using edited normals.

        Returns a per-vertex array where 1.0 = lit, 0.0 = shadow.
        The shadow boundary is determined by the edited normals and
        per-vertex shadow bias, NOT by geometric normals.

        Args:
            edited_mesh: Mesh with edited normals and shadow bias.
            light_dir: Light direction vector (toward light).
            threshold: Shadow threshold override. Uses config if None.

        Returns:
            (N,) float array of shadow values [0, 1].
        """
        L = np.array(light_dir, dtype=np.float64)
        L_len = np.linalg.norm(L)
        if L_len > 1e-10:
            L /= L_len

        thresh = threshold if threshold is not None else \
            self.shadow_config.threshold
        softness = self.shadow_config.softness

        # NdotL using edited normals
        NdotL = np.sum(edited_mesh.edited_normals * L, axis=1)
        # Remap to [0, 1]
        NdotL_remapped = NdotL * 0.5 + 0.5

        # Apply per-vertex bias
        effective_threshold = thresh - edited_mesh.shadow_bias

        if softness < 1e-6:
            # Hard shadow (pure cel-shading)
            shadow = (NdotL_remapped >= effective_threshold).astype(np.float64)
        else:
            # Soft transition
            shadow = np.clip(
                (NdotL_remapped - effective_threshold) / softness,
                0.0, 1.0
            )

        return shadow

    def bake_normal_map(
        self, edited_mesh: EditedMesh,
        resolution: int = 512
    ) -> np.ndarray:
        """Bake edited normals to a normal map texture.

        Converts edited vertex normals to a tangent-space normal map
        that can be used in standard rendering pipelines.

        Args:
            edited_mesh: Mesh with edited normals.
            resolution: Output texture resolution.

        Returns:
            (resolution, resolution, 3) uint8 array (RGB normal map).
        """
        # Simple vertex-to-UV-to-pixel baking
        # For production, this would use rasterization; here we use
        # a simplified approach suitable for the project's 2D pipeline
        normal_map = np.full((resolution, resolution, 3), 128, dtype=np.uint8)

        # Encode normals: RGB = (N * 0.5 + 0.5) * 255
        for vi in range(edited_mesh.vertex_count):
            n = edited_mesh.edited_normals[vi]
            # Simple UV mapping (planar projection for now)
            u = int(np.clip(
                (edited_mesh.vertices[vi, 0] + 1.0) * 0.5 * (resolution - 1),
                0, resolution - 1
            ))
            v = int(np.clip(
                (edited_mesh.vertices[vi, 1] + 1.0) * 0.5 * (resolution - 1),
                0, resolution - 1
            ))
            r = int(np.clip((n[0] * 0.5 + 0.5) * 255, 0, 255))
            g = int(np.clip((n[1] * 0.5 + 0.5) * 255, 0, 255))
            b = int(np.clip((n[2] * 0.5 + 0.5) * 255, 0, 255))
            normal_map[v, u] = [r, g, b]

        return normal_map

    def generate_hlsl_vertex_normal_shader(self) -> str:
        """Generate HLSL shader code that uses edited normals for cel-shading.

        This shader reads edited normals from a normal map or vertex data
        and uses them for shadow computation instead of geometric normals.
        """
        cfg = self.shadow_config
        return f"""// Vertex Normal Edited Cel-Shading Shader
// Reference: Junya Motomura, Arc System Works, GDC 2015
// SESSION-065: Vertex Normal Editor integration

// --- Properties ---
// _EditedNormalMap: Baked edited normal map
// _ShadowThreshold: Global shadow threshold ({cfg.threshold})
// _ShadowSoftness: Shadow edge softness ({cfg.softness})
// _ShadowTint: Shadow color tint
// _RimThreshold: Rim light threshold ({cfg.rim_threshold})
// _RimPower: Rim light falloff ({cfg.rim_power})

struct appdata {{
    float4 vertex : POSITION;
    float3 normal : NORMAL;     // Geometric normal (unused for shadow)
    float4 tangent : TANGENT;
    float4 color : COLOR;       // R = shadow bias, G = rim bias
    float2 uv : TEXCOORD0;
}};

struct v2f {{
    float4 pos : SV_POSITION;
    float2 uv : TEXCOORD0;
    float3 worldNormal : TEXCOORD1;     // Edited normal
    float3 worldPos : TEXCOORD2;
    float shadowBias : TEXCOORD3;
    float3 viewDir : TEXCOORD4;
}};

sampler2D _EditedNormalMap;
sampler2D _BaseMap;
sampler2D _TintMap;
float3 _LightDir;
float _ShadowThreshold;
float _ShadowSoftness;
float4 _ShadowTint;
float _RimThreshold;
float _RimPower;

v2f vert(appdata v) {{
    v2f o;
    o.pos = UnityObjectToClipPos(v.vertex);
    o.uv = v.uv;

    // Read edited normal from normal map (tangent space → world space)
    float3 editedNormalTS = tex2Dlod(_EditedNormalMap, float4(v.uv, 0, 0)).rgb * 2.0 - 1.0;
    float3 worldNormal = mul((float3x3)unity_ObjectToWorld, v.normal);
    float3 worldTangent = mul((float3x3)unity_ObjectToWorld, v.tangent.xyz);
    float3 worldBinormal = cross(worldNormal, worldTangent) * v.tangent.w;
    float3x3 TBN = float3x3(worldTangent, worldBinormal, worldNormal);
    o.worldNormal = normalize(mul(editedNormalTS, TBN));

    o.worldPos = mul(unity_ObjectToWorld, v.vertex).xyz;
    o.shadowBias = v.color.r;  // Per-vertex shadow bias from vertex paint
    o.viewDir = normalize(_WorldSpaceCameraPos - o.worldPos);
    return o;
}}

float4 frag(v2f i) : SV_Target {{
    float3 N = normalize(i.worldNormal);
    float3 L = normalize(_LightDir);
    float3 V = normalize(i.viewDir);

    // === CEL SHADOW (using edited normals) ===
    float NdotL = dot(N, L) * 0.5 + 0.5;
    float threshold = _ShadowThreshold - i.shadowBias;
    float shadow;
    if (_ShadowSoftness < 0.001) {{
        shadow = step(threshold, NdotL);  // Hard cel-shadow
    }} else {{
        shadow = saturate((NdotL - threshold) / _ShadowSoftness);
    }}

    // === RIM LIGHT ===
    float rim = 1.0 - saturate(dot(N, V));
    rim = pow(rim, _RimPower) * step(_RimThreshold, rim);

    // === FINAL COLOR ===
    float3 baseColor = tex2D(_BaseMap, i.uv).rgb;
    float3 tintColor = tex2D(_TintMap, i.uv).rgb;
    float3 shadowColor = baseColor * tintColor * _ShadowTint.rgb;
    float3 litColor = baseColor;
    float3 finalColor = lerp(shadowColor, litColor, shadow);
    finalColor += rim * litColor * 0.3;  // Additive rim

    return float4(finalColor, 1.0);
}}"""


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def create_normal_editor(
    threshold: float = 0.5,
    softness: float = 0.01
) -> VertexNormalEditor:
    """Create a VertexNormalEditor with common settings."""
    config = ShadowConfig(threshold=threshold, softness=softness)
    return VertexNormalEditor(shadow_config=config)


def transfer_sphere_normals(
    vertices: np.ndarray,
    triangles: np.ndarray,
    center: Optional[List[float]] = None,
    radius: float = 1.0,
    blend: float = 1.0
) -> EditedMesh:
    """Quick sphere normal transfer for common use case."""
    editor = VertexNormalEditor()
    proxy = ProxyShape.sphere(center=center, radius=radius)
    return editor.transfer_normals_from_proxy(
        vertices, triangles, proxy, blend_weight=blend
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "ProxyShapeType",
    "ProxyShape",
    "ShadowConfig",
    "EditedMesh",
    "VertexNormalEditor",
    "create_normal_editor",
    "transfer_sphere_normals",
]
