"""Pseudo3DExtension — Reserved scaffold for pseudo-3D rendering.

Current status: SCAFFOLD (CPU math only, no GPU rendering)

This module provides the mathematical foundations for pseudo-3D rendering
that will be activated as the project evolves. All math is implemented
and tested; the rendering pipeline requires Unity/GPU integration.

Pseudo-3D techniques implemented (math only)
--------------------------------------------
1. Isometric projection: 3D world coords → 2D screen coords
2. Painter's algorithm: depth-sorted sprite rendering order
3. Normal map generation: 2D sprite → approximate normal map
4. Parallax scrolling: multi-layer depth simulation
5. Billboard rotation: sprite facing toward camera (360° views)
6. Depth-based scale: objects smaller when further away

Upgrade triggers
----------------
- TRIGGER_1: User provides Unity project path
  → Activate: ShaderCodeGenerator output → Unity .shader files
- TRIGGER_2: User provides NVIDIA GPU (CUDA 11.8+)
  → Activate: nvdiffrast differentiable rasterizer
- TRIGGER_3: User provides reference 3D model or depth map
  → Activate: Normal map baking from 3D geometry
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class IsometricConfig:
    """Configuration for isometric projection."""
    angle_deg:    float = 30.0    # Isometric angle (30° = classic iso)
    tile_width:   int   = 64      # Tile width in pixels
    tile_height:  int   = 32      # Tile height in pixels (= tile_width / 2)
    depth_scale:  float = 0.5     # Vertical offset per depth unit


@dataclass
class ParallaxConfig:
    """Configuration for parallax scrolling."""
    n_layers:     int   = 4       # Number of depth layers
    speed_factor: float = 0.5     # Speed ratio between layers
    max_offset:   float = 0.2     # Maximum UV offset


class Pseudo3DExtension:
    """Mathematical foundations for pseudo-3D rendering.

    All methods are pure math (no GPU required). They produce:
    - Projection matrices and UV offsets
    - Depth-sorted render order lists
    - Approximate normal maps from 2D sprites
    - Parallax UV offsets per layer

    These outputs are consumed by:
    - ShaderCodeGenerator (for Unity shader parameters)
    - Future GPU renderer (when available)
    """

    STATUS = "scaffold"  # "scaffold" | "active" | "gpu_required"

    def __init__(
        self,
        iso_config:  Optional[IsometricConfig] = None,
        para_config: Optional[ParallaxConfig]  = None,
    ) -> None:
        self.iso  = iso_config  or IsometricConfig()
        self.para = para_config or ParallaxConfig()

    # ── Isometric projection ───────────────────────────────────────────────────

    def world_to_screen(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Convert 3D world coordinates to 2D isometric screen coordinates.

        Math:
            screen_x = (x - z) * cos(angle) * tile_width/2
            screen_y = (x + z) * sin(angle) * tile_height/2 - y * tile_height

        Parameters
        ----------
        x, y, z : float
            World-space coordinates (y = height, z = depth)

        Returns
        -------
        (screen_x, screen_y) : tuple[float, float]
        """
        rad = math.radians(self.iso.angle_deg)
        tw = self.iso.tile_width / 2
        th = self.iso.tile_height / 2
        sx = (x - z) * math.cos(rad) * tw
        sy = (x + z) * math.sin(rad) * th - y * self.iso.tile_height
        return (sx, sy)

    def screen_to_world(self, sx: float, sy: float, y: float = 0.0) -> tuple[float, float, float]:
        """Inverse isometric projection (screen → world, assuming y=0 plane).

        Math (inverse of world_to_screen with y=0):
            x = (sx/cos(a)/tw + sy/sin(a)/th) / 2
            z = (sy/sin(a)/th - sx/cos(a)/tw) / 2
        """
        rad = math.radians(self.iso.angle_deg)
        tw = self.iso.tile_width / 2
        th = self.iso.tile_height / 2
        a = sx / (math.cos(rad) * tw)
        b = (sy + y * self.iso.tile_height) / (math.sin(rad) * th)
        world_x = (a + b) / 2
        world_z = (b - a) / 2
        return (world_x, y, world_z)

    def depth_sort(self, sprites: list[dict]) -> list[dict]:
        """Sort sprites by depth for painter's algorithm rendering.

        Parameters
        ----------
        sprites : list of dicts with keys 'x', 'y', 'z', 'name'

        Returns
        -------
        Sorted list (back-to-front, i.e., furthest first)
        """
        return sorted(sprites, key=lambda s: s.get("x", 0) + s.get("z", 0))

    # ── Normal map generation ──────────────────────────────────────────────────

    def generate_normal_map(
        self,
        sprite: Image.Image,
        strength: float = 1.0,
        blur_radius: int = 2,
    ) -> Image.Image:
        """Generate an approximate normal map from a 2D sprite.

        Algorithm: Sobel gradient of the luminance channel → XY normals,
        Z = sqrt(1 - X² - Y²) (hemisphere assumption).

        Math:
            Gx = Sobel horizontal gradient
            Gy = Sobel vertical gradient
            N = normalize(Gx * strength, Gy * strength, 1.0)
            RGB = (N + 1) / 2  (remap from [-1,1] to [0,1])

        Parameters
        ----------
        sprite : PIL.Image
            Source sprite (RGBA or RGB).
        strength : float
            Normal map intensity (1.0 = standard, 2.0 = embossed).
        blur_radius : int
            Pre-blur radius to reduce noise.

        Returns
        -------
        PIL.Image (RGB, normal map)
        """
        # Convert to grayscale
        gray = np.array(sprite.convert("L"), dtype=float) / 255.0

        # Simple Gaussian blur (box approximation)
        if blur_radius > 0:
            from scipy.ndimage import uniform_filter
            gray = uniform_filter(gray, size=blur_radius * 2 + 1)

        # Sobel kernels
        Kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)
        Ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)

        from scipy.ndimage import convolve
        gx = convolve(gray, Kx) * strength
        gy = convolve(gray, Ky) * strength

        # Build normal vectors
        gz = np.ones_like(gx)
        length = np.sqrt(gx**2 + gy**2 + gz**2)
        length = np.maximum(length, 1e-8)
        nx = gx / length
        ny = gy / length
        nz = gz / length

        # Remap to [0, 255]
        normal_rgb = np.stack([
            (nx + 1) / 2 * 255,
            (ny + 1) / 2 * 255,
            (nz + 1) / 2 * 255,
        ], axis=-1).astype(np.uint8)

        return Image.fromarray(normal_rgb, mode="RGB")

    # ── Parallax scrolling ─────────────────────────────────────────────────────

    def parallax_uv_offsets(
        self,
        camera_offset: tuple[float, float],
    ) -> list[tuple[float, float]]:
        """Compute UV offsets for each parallax layer.

        Math:
            offset_i = camera_offset * speed_factor^(n_layers - i)
            (closer layers move faster, background moves slower)

        Parameters
        ----------
        camera_offset : (dx, dy)
            Camera movement in UV space.

        Returns
        -------
        list of (du, dv) per layer, from background (index 0) to foreground
        """
        offsets = []
        for i in range(self.para.n_layers):
            # Layer 0 = background (slowest), layer n-1 = foreground (fastest)
            speed = self.para.speed_factor ** (self.para.n_layers - 1 - i)
            du = camera_offset[0] * speed
            dv = camera_offset[1] * speed
            # Clamp to max offset
            du = max(-self.para.max_offset, min(self.para.max_offset, du))
            dv = max(-self.para.max_offset, min(self.para.max_offset, dv))
            offsets.append((du, dv))
        return offsets

    # ── Billboard rotation ─────────────────────────────────────────────────────

    def billboard_frame_index(
        self,
        sprite_facing: float,
        camera_angle: float,
        n_frames: int = 8,
    ) -> int:
        """Compute which animation frame to show for billboard rotation.

        Math:
            relative_angle = (camera_angle - sprite_facing) % 360
            frame_index = round(relative_angle / (360 / n_frames)) % n_frames

        Parameters
        ----------
        sprite_facing : float
            Direction the sprite is facing (degrees, 0 = north).
        camera_angle : float
            Camera viewing angle (degrees).
        n_frames : int
            Number of directional frames (8 = N/NE/E/SE/S/SW/W/NW).

        Returns
        -------
        int : frame index [0, n_frames)
        """
        relative = (camera_angle - sprite_facing) % 360
        frame = round(relative / (360 / n_frames)) % n_frames
        return int(frame)

    # ── Depth-based scale ──────────────────────────────────────────────────────

    def depth_scale(self, depth: float, base_scale: float = 1.0) -> float:
        """Compute sprite scale based on depth (perspective foreshortening).

        Math (simple linear perspective):
            scale = base_scale / (1 + depth * depth_scale_factor)

        Parameters
        ----------
        depth : float
            World-space depth (0 = at camera, positive = further away).
        base_scale : float
            Scale at depth=0.

        Returns
        -------
        float : scale factor
        """
        return base_scale / (1.0 + depth * self.iso.depth_scale)

    # ── Status report ──────────────────────────────────────────────────────────

    def status_report(self) -> str:
        """Return a human-readable status report for this module."""
        lines = [
            "# Pseudo-3D Extension Status",
            f"Status: {self.STATUS.upper()}",
            "",
            "## Available (CPU math, no GPU required)",
            "  ✓ Isometric projection (world ↔ screen)",
            "  ✓ Painter's algorithm depth sort",
            "  ✓ Normal map generation from 2D sprites",
            "  ✓ Parallax UV offsets",
            "  ✓ Billboard rotation frame index",
            "  ✓ Depth-based scale",
            "",
            "## Pending (requires external upgrade)",
            "  ○ Unity shader integration → TRIGGER: Unity project path",
            "  ○ GPU-accelerated normal baking → TRIGGER: NVIDIA GPU + CUDA",
            "  ○ Differentiable rasterizer → TRIGGER: PyTorch + nvdiffrast",
            "  ○ 360° sprite generation → TRIGGER: 3D reference model",
        ]
        return "\n".join(lines)
