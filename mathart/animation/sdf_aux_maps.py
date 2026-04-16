"""SESSION-044 — Analytical auxiliary-map baking from 2D SDF grids.

This module turns a sampled 2D signed distance field into engine-consumable
auxiliary textures without raymarching:

- normal maps (RGB encodes XYZ)
- depth maps (grayscale height/depth proxy)
- raw distance + gradient fields for downstream analysis

The implementation follows two principles distilled in SESSION-044:

1. The gradient of an SDF is the most stable source of surface orientation.
2. Depth/height for 2D sprites can be approximated from the interior distance
   magnitude, then encoded as a grayscale auxiliary channel.

The code is deliberately NumPy-only so it can run inside the existing project
pipeline, tests, and evolution loop without GPU or external tools.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image

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
    """Configuration for turning an SDF into normal/depth auxiliary maps."""

    gradient_mode: str = "central_difference"
    edge_order: int = 2
    normal_z_base: float = 0.35
    depth_to_z_scale: float = 0.85
    depth_percentile: float = 0.98
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
    normal_vectors: np.ndarray
    normal_map_image: Image.Image
    depth_map_image: Image.Image
    mask_image: Image.Image
    metadata: dict[str, float | int | str]


def sample_sdf_grid(sdf: SDFFunc, grid: SDFSamplingGrid) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sample an SDF on a regular grid.

    Returns
    -------
    xs, ys, x, y, dist
        One-dimensional coordinates, 2D mesh grids, and the evaluated distance field.
    """
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
    """Compute SDF gradients with second-order central differences when possible."""
    grad_y, grad_x = np.gradient(distance_field, ys, xs, edge_order=edge_order)
    return np.asarray(grad_x, dtype=np.float64), np.asarray(grad_y, dtype=np.float64)


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
    """Build pseudo-3D normals from SDF gradients plus interior depth.

    The xy components come directly from the SDF gradient, while the z component
    is lifted by the normalized interior distance. This keeps edge orientation
    faithful to the SDF while pushing interior pixels to face the camera more.
    """
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


def encode_normal_map(normal_vectors: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode XYZ normals in RGB and coverage in alpha."""
    normals = np.clip((np.asarray(normal_vectors, dtype=np.float64) * 0.5) + 0.5, 0.0, 1.0)
    rgb = np.round(normals * 255.0).astype(np.uint8)
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((rgb, alpha))
    return Image.fromarray(rgba, "RGBA")


def encode_depth_map(depth_values: np.ndarray, inside_mask: np.ndarray) -> Image.Image:
    """Encode depth/height proxy as grayscale + alpha mask."""
    depth = np.clip(np.asarray(depth_values, dtype=np.float64), 0.0, 1.0)
    gray = np.round(depth * 255.0).astype(np.uint8)
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((gray, gray, gray, alpha))
    return Image.fromarray(rgba, "RGBA")


def encode_mask(inside_mask: np.ndarray) -> Image.Image:
    """Encode silhouette coverage as a binary mask."""
    alpha = np.where(np.asarray(inside_mask, dtype=bool), 255, 0).astype(np.uint8)
    rgba = np.dstack((alpha, alpha, alpha, alpha))
    return Image.fromarray(rgba, "RGBA")


def bake_sdf_auxiliary_maps(
    sdf: SDFFunc,
    grid: SDFSamplingGrid | None = None,
    config: SDFBakeConfig | None = None,
) -> SDFAuxiliaryMaps:
    """Bake normal/depth auxiliary maps from a 2D SDF callable."""
    grid = grid or SDFSamplingGrid()
    config = config or SDFBakeConfig()

    xs, ys, _x, _y, dist = sample_sdf_grid(sdf, grid)

    if config.gradient_mode != "central_difference":
        raise ValueError(f"Unsupported gradient mode: {config.gradient_mode}")

    grad_x, grad_y = compute_sdf_gradients(dist, xs, ys, edge_order=config.edge_order)
    inside = dist < 0.0
    depth, depth_scale = compute_depth_map(
        dist,
        percentile=config.depth_percentile,
        min_depth_span=config.min_depth_span,
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

    metadata: dict[str, float | int | str] = {
        "width": grid.width,
        "height": grid.height,
        "gradient_mode": config.gradient_mode,
        "edge_order": config.edge_order,
        "depth_percentile": config.depth_percentile,
        "depth_scale": depth_scale,
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "inside_pixel_count": int(np.count_nonzero(inside)),
        "distance_min": float(np.min(dist)),
        "distance_max": float(np.max(dist)),
    }

    return SDFAuxiliaryMaps(
        distance_field=dist,
        gradient_x=grad_x,
        gradient_y=grad_y,
        inside_mask=inside,
        depth_values=depth,
        normal_vectors=normals,
        normal_map_image=encode_normal_map(normals, inside),
        depth_map_image=encode_depth_map(depth, inside),
        mask_image=encode_mask(inside),
        metadata=metadata,
    )


__all__ = [
    "SDFSamplingGrid",
    "SDFBakeConfig",
    "SDFAuxiliaryMaps",
    "sample_sdf_grid",
    "compute_sdf_gradients",
    "compute_depth_map",
    "compute_normal_vectors",
    "encode_normal_map",
    "encode_depth_map",
    "encode_mask",
    "bake_sdf_auxiliary_maps",
]
