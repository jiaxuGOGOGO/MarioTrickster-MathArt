"""SESSION-054 — Industrial auxiliary-map baking from 2D SDFs.

This module upgrades the earlier SESSION-044 baking path in three ways:

1. **Exact analytical gradients where available.** Core primitives can provide
   distance-plus-gradient directly, avoiding noisy finite-difference normals.
2. **Industrial material channels.** In addition to normal/depth/mask, the
   baker emits thickness and roughness proxies suited to 2.5D sprite lighting.
3. **Engine-facing metadata.** Export code can now describe the generated maps
   as a compact commercial-ready material bundle for Unity URP 2D / Godot style
   deferred or pseudo-deferred lighting workflows.

The implementation remains NumPy-only so it stays compatible with the existing
sandbox, tests, and evolution loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image

from .analytic_sdf import DistanceGradient2D, SDFGradientFunc2D

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class SDFSamplingGrid:
    """Regular sampling grid for 2D SDF baking."""

    width: int = 32
    height: int = 32
    x_min: float = -0.6
    x_max: float = 0.6
    y_max: float = 1.1
    y_min: float = -0.1

    def make_mesh(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        xs = np.linspace(self.x_min, self.x_max, self.width, dtype=np.float64)
        ys = np.linspace(self.y_max, self.y_min, self.height, dtype=np.float64)
        x, y = np.meshgrid(xs, ys)
        return xs, ys, x, y


@dataclass(frozen=True)
class SDFBakeConfig:
    """Configuration for turning an SDF into industrial material maps."""

    gradient_mode: str = "hybrid_exact"
    edge_order: int = 2
    normal_z_base: float = 0.35
    depth_to_z_scale: float = 0.85
    depth_percentile: float = 0.98
    thickness_percentile: float = 0.995
    roughness_percentile: float = 0.98
    min_depth_span: float = 1e-4
    flat_normal_outside: tuple[float, float, float] = (0.0, 0.0, 1.0)


@dataclass
class SDFAuxiliaryMaps:
    """Packed result from SDF auxiliary-map baking."""

    distance_field: np.ndarray
    gradient_x: np.ndarray
    gradient_y: np.ndarray
    inside_mask: np.ndarray
    depth_values: np.ndarray
    thickness_values: np.ndarray
    curvature_values: np.ndarray
    roughness_values: np.ndarray
    normal_vectors: np.ndarray
    normal_map_image: Image.Image
    depth_map_image: Image.Image
    thickness_map_image: Image.Image
    roughness_map_image: Image.Image
    mask_image: Image.Image
    metadata: dict[str, float | int | str | dict[str, object]]


def sample_sdf_grid(sdf: SDFFunc, grid: SDFSamplingGrid) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sample an SDF on a regular grid."""
    xs, ys, x, y = grid.make_mesh()
    dist = np.asarray(sdf(x, y), dtype=np.float64)
    if dist.shape != x.shape:
        raise ValueError(
            f"SDF returned shape {dist.shape}, expected {x.shape} for grid {grid.width}x{grid.height}."
        )
    return xs, ys, x, y, dist


def compute_sdf_gradients(
    distance_field: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    edge_order: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute SDF gradients with central differences."""
    grad_y, grad_x = np.gradient(distance_field, ys, xs, edge_order=edge_order)
    return np.asarray(grad_x, dtype=np.float64), np.asarray(grad_y, dtype=np.float64)


def resolve_sdf_gradients(
    distance_field: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    gradient_mode: str = "hybrid_exact",
    edge_order: int = 2,
    analytic_gradient: SDFGradientFunc2D | None = None,
    x: np.ndarray | None = None,
    y: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Resolve gradients according to the requested strategy.

    Supported modes:
    - ``central_difference``: always use sampled gradients.
    - ``analytic``: require ``analytic_gradient`` and use it directly.
    - ``hybrid_exact``: prefer analytic, otherwise fall back to sampled.
    """
    mode = gradient_mode.strip().lower()
    if mode == "central_difference":
        gx, gy = compute_sdf_gradients(distance_field, xs, ys, edge_order=edge_order)
        return gx, gy, "central_difference"

    if mode in {"analytic", "hybrid_exact"} and analytic_gradient is not None:
        if x is None or y is None:
            x, y = np.meshgrid(xs, ys)
        result = analytic_gradient(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        return (
            np.asarray(result.gradient_x, dtype=np.float64),
            np.asarray(result.gradient_y, dtype=np.float64),
            "analytic",
        )

    if mode == "analytic":
        raise ValueError("Analytic gradient mode requested, but no analytic_gradient callable was provided.")

    gx, gy = compute_sdf_gradients(distance_field, xs, ys, edge_order=edge_order)
    return gx, gy, "central_difference"


def compute_depth_map(
    distance_field: np.ndarray,
    *,
    percentile: float = 0.98,
    min_depth_span: float = 1e-4,
) -> tuple[np.ndarray, float]:
    """Convert signed distance magnitudes inside the silhouette into a depth proxy."""
    interior = np.clip(-np.asarray(distance_field, dtype=np.float64), 0.0, None)
    positive = interior[interior > 0.0]
    if positive.size == 0:
        scale = 1.0
        depth = np.zeros_like(interior)
    else:
        q = float(np.clip(percentile, 0.0, 1.0))
        scale = float(np.quantile(positive, q))
        scale = max(scale, min_depth_span)
        depth = np.clip(interior / scale, 0.0, 1.0)
    return depth, scale


def compute_thickness_map(
    distance_field: np.ndarray,
    *,
    percentile: float = 0.995,
    min_depth_span: float = 1e-4,
) -> tuple[np.ndarray, float]:
    """Build a thickness proxy from interior negative distance.

    For 2D commercial sprites, exact volumetric thickness is not available, but
    the interior signed distance magnitude provides a stable pseudo-thickness
    channel that behaves well under 2D deferred lighting and stylized subsurface
    effects.
    """
    return compute_depth_map(distance_field, percentile=percentile, min_depth_span=min_depth_span)


def compute_curvature_proxy(
    gradient_x: np.ndarray,
    gradient_y: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    inside_mask: np.ndarray,
) -> np.ndarray:
    """Approximate curvature proxy from the divergence of the gradient field."""
    dgy_dy, dgy_dx = np.gradient(np.asarray(gradient_y, dtype=np.float64), ys, xs, edge_order=2)
    dgx_dy, dgx_dx = np.gradient(np.asarray(gradient_x, dtype=np.float64), ys, xs, edge_order=2)
    divergence = np.asarray(dgx_dx + dgy_dy, dtype=np.float64)
    divergence = np.where(np.asarray(inside_mask, dtype=bool), divergence, 0.0)
    return divergence


def compute_roughness_map(
    curvature_values: np.ndarray,
    inside_mask: np.ndarray,
    *,
    percentile: float = 0.98,
) -> tuple[np.ndarray, float]:
    """Turn curvature magnitude into an inverse roughness proxy.

    The user-requested industrial strategy is to derive curvature from field
    differentials and invert it into a roughness-style channel. We therefore
    normalize curvature magnitude inside the sprite and map it as
    ``roughness = 1 - normalized_curvature``.
    """
    inside = np.asarray(inside_mask, dtype=bool)
    curvature_mag = np.abs(np.asarray(curvature_values, dtype=np.float64))
    active = curvature_mag[inside]
    if active.size == 0:
        scale = 1.0
        roughness = np.zeros_like(curvature_mag)
    else:
        q = float(np.clip(percentile, 0.0, 1.0))
        scale = float(np.quantile(active, q))
        scale = max(scale, 1e-8)
        curvature_norm = np.clip(curvature_mag / scale, 0.0, 1.0)
        roughness = np.where(inside, 1.0 - curvature_norm, 0.0)
    return roughness, scale


def compute_normal_vectors(
    gradient_x: np.ndarray,
    gradient_y: np.ndarray,
    depth_values: np.ndarray,
    inside_mask: np.ndarray,
    *,
    normal_z_base: float = 0.35,
    depth_to_z_scale: float = 0.85,
    flat_normal_outside: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> np.ndarray:
    """Build pseudo-3D normals from SDF gradients plus interior depth."""
    gx = np.asarray(gradient_x, dtype=np.float64)
    gy = np.asarray(gradient_y, dtype=np.float64)
    depth = np.asarray(depth_values, dtype=np.float64)
    inside = np.asarray(inside_mask, dtype=bool)

    nx = -gx
    ny = -gy
    nz = np.full_like(depth, normal_z_base, dtype=np.float64) + depth * depth_to_z_scale

    normals = np.stack((nx, ny, nz), axis=-1)
    length = np.linalg.norm(normals, axis=-1, keepdims=True)
    length = np.maximum(length, 1e-8)
    normals = normals / length

    outside = np.array(flat_normal_outside, dtype=np.float64)
    normals[~inside] = outside
    return normals


def _encode_scalar_rgba(values: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    scalar = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    gray = np.round(scalar * 255.0).astype(np.uint8)
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((gray, gray, gray, alpha))
    return Image.fromarray(rgba, "RGBA")


def encode_normal_map(normal_vectors: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode XYZ normals in RGB and coverage in alpha."""
    normals = np.clip((np.asarray(normal_vectors, dtype=np.float64) * 0.5) + 0.5, 0.0, 1.0)
    rgb = np.round(normals * 255.0).astype(np.uint8)
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((rgb, alpha))
    return Image.fromarray(rgba, "RGBA")


def encode_depth_map(depth_values: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode depth/height proxy as grayscale + alpha mask."""
    return _encode_scalar_rgba(depth_values, inside_mask)


def encode_thickness_map(thickness_values: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode thickness proxy as grayscale + alpha mask."""
    return _encode_scalar_rgba(thickness_values, inside_mask)


def encode_roughness_map(roughness_values: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode roughness proxy as grayscale + alpha mask."""
    return _encode_scalar_rgba(roughness_values, inside_mask)


def encode_mask(inside_mask: np.ndarray) -> Image.Image:
    """Encode silhouette coverage as a binary mask."""
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((alpha, alpha, alpha, alpha))
    return Image.fromarray(rgba, "RGBA")


def bake_sdf_auxiliary_maps(
    sdf: SDFFunc,
    grid: SDFSamplingGrid | None = None,
    config: SDFBakeConfig | None = None,
    *,
    analytic_gradient: SDFGradientFunc2D | None = None,
) -> SDFAuxiliaryMaps:
    """Bake industrial material maps from a 2D SDF callable."""
    grid = grid or SDFSamplingGrid()
    config = config or SDFBakeConfig()

    xs, ys, x, y, dist = sample_sdf_grid(sdf, grid)
    grad_x, grad_y, gradient_source = resolve_sdf_gradients(
        dist,
        xs,
        ys,
        gradient_mode=config.gradient_mode,
        edge_order=config.edge_order,
        analytic_gradient=analytic_gradient,
        x=x,
        y=y,
    )
    inside = dist < 0.0
    depth, depth_scale = compute_depth_map(
        dist,
        percentile=config.depth_percentile,
        min_depth_span=config.min_depth_span,
    )
    thickness, thickness_scale = compute_thickness_map(
        dist,
        percentile=config.thickness_percentile,
        min_depth_span=config.min_depth_span,
    )
    curvature = compute_curvature_proxy(grad_x, grad_y, xs, ys, inside)
    roughness, roughness_scale = compute_roughness_map(
        curvature,
        inside,
        percentile=config.roughness_percentile,
    )
    normals = compute_normal_vectors(
        grad_x,
        grad_y,
        depth,
        inside,
        normal_z_base=config.normal_z_base,
        depth_to_z_scale=config.depth_to_z_scale,
        flat_normal_outside=config.flat_normal_outside,
    )

    pixel_width = float(abs(xs[1] - xs[0])) if xs.size > 1 else 1.0
    pixel_height = float(abs(ys[1] - ys[0])) if ys.size > 1 else 1.0

    metadata: dict[str, float | int | str | dict[str, object]] = {
        "width": grid.width,
        "height": grid.height,
        "gradient_mode": config.gradient_mode,
        "gradient_source": gradient_source,
        "edge_order": config.edge_order,
        "depth_percentile": config.depth_percentile,
        "depth_scale": depth_scale,
        "thickness_percentile": config.thickness_percentile,
        "thickness_scale": thickness_scale,
        "roughness_percentile": config.roughness_percentile,
        "roughness_scale": roughness_scale,
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "inside_pixel_count": int(np.count_nonzero(inside)),
        "distance_min": float(np.min(dist)),
        "distance_max": float(np.max(dist)),
        "engine_channels": {
            "normal": "RGB=XYZ, A=coverage",
            "depth": "RGBA grayscale depth proxy",
            "thickness": "RGBA grayscale interior-thickness proxy",
            "roughness": "RGBA grayscale inverse-curvature roughness proxy",
            "mask": "RGBA binary silhouette mask",
        },
    }

    return SDFAuxiliaryMaps(
        distance_field=dist,
        gradient_x=grad_x,
        gradient_y=grad_y,
        inside_mask=inside,
        depth_values=depth,
        thickness_values=thickness,
        curvature_values=curvature,
        roughness_values=roughness,
        normal_vectors=normals,
        normal_map_image=encode_normal_map(normals, inside),
        depth_map_image=encode_depth_map(depth, inside),
        thickness_map_image=encode_thickness_map(thickness, inside),
        roughness_map_image=encode_roughness_map(roughness, inside),
        mask_image=encode_mask(inside),
        metadata=metadata,
    )


__all__ = [
    "SDFSamplingGrid",
    "SDFBakeConfig",
    "SDFAuxiliaryMaps",
    "sample_sdf_grid",
    "compute_sdf_gradients",
    "resolve_sdf_gradients",
    "compute_depth_map",
    "compute_thickness_map",
    "compute_curvature_proxy",
    "compute_roughness_map",
    "compute_normal_vectors",
    "encode_normal_map",
    "encode_depth_map",
    "encode_thickness_map",
    "encode_roughness_map",
    "encode_mask",
    "bake_sdf_auxiliary_maps",
]
