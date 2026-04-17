"""SESSION-034 — Industrial-Grade Pixel Art Rendering Pipeline.

Distilled from two GDC landmark presentations:

1. **Dead Cells** (Sébastien Bénard / Motion Twin, GDC 2018):
   "Art Design Deep Dive: Using a 3D Pipeline for 2D Animation in Dead Cells"
   - No anti-aliasing downsampling: render at exact target resolution
   - Pseudo-normal map lighting for pixel volume
   - Cel shading with hard threshold (not gradient)
   - Silhouette exaggeration: non-physical deformation for 2D clarity

2. **Guilty Gear Xrd** (Junya C. Motomura / Arc System Works, GDC 2015):
   "GuiltyGear Xrd's Art Style: The X Factor Between 2D and 3D"
   - Limited Animation / Stepped Keys: no interpolation between key poses
   - Hold Frames: freeze on impact/contact for 2-3 frames
   - Extreme Squash & Stretch: non-physical deformation for visual impact
   - Frame rate modulation: variable hold durations per animation phase

Target spec: 32×32 pixels, 12fps.
At this extreme resolution, 3D engine "physical smoothness" is POISON:
it causes pixel edges to crawl and actions to feel mushy.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  DeadCellsRenderPipeline                                            │
    │  ├─ No-AA SDF rendering (hard threshold, no smoothstep)            │
    │  ├─ Pseudo-normal map from SDF gradient                            │
    │  ├─ Cel shading: 2-band hard threshold (not dithered)              │
    │  ├─ Silhouette priority: 2px outline on key poses                  │
    │  └─ Volume-preserving squash/stretch coordinate transform          │
    ├─────────────────────────────────────────────────────────────────────┤
    │  GuiltyGearFrameScheduler                                           │
    │  ├─ Phase-aware hold frame detection                                │
    │  ├─ Impact/contact/apex hold (2-3 frame freeze)                    │
    │  ├─ Stepped key interpolation (snap, not blend)                    │
    │  └─ Dynamic squash/stretch per animation phase                     │
    ├─────────────────────────────────────────────────────────────────────┤
    │  render_character_frame_industrial()                                 │
    │  ├─ Drop-in replacement for render_character_frame()                │
    │  ├─ Integrates Dead Cells pipeline + GGXrd frame scheduling        │
    │  └─ Outputs crisp, high-contrast pixel art at 32×32                │
    └─────────────────────────────────────────────────────────────────────┘

Integration:
    - render_character_frame_industrial() has the same signature as
      render_character_frame() plus optional frame scheduling parameters
    - render_character_sheet_industrial() replaces render_character_sheet()
      with built-in hold frame and squash/stretch support
    - The GuiltyGearFrameScheduler can be used standalone for any renderer

References:
    - SESSION-028: AnglePoseProjector (squash/stretch metadata)
    - SESSION-033: PhaseVariable, PhaseDrivenAnimator (phase detection)
    - SESSION-034: MotionMatchingEvaluator (silhouette scoring)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Sequence

import numpy as np
from PIL import Image

from .skeleton import Skeleton
from .parts import CharacterStyle, BodyPart, assemble_character
from .sdf_aux_maps import (
    SDFBakeConfig,
    compute_sdf_gradients,
    compute_depth_map,
    compute_thickness_map,
    compute_curvature_proxy,
    compute_roughness_map,
    compute_normal_vectors,
    encode_normal_map,
    encode_depth_map,
    encode_thickness_map,
    encode_roughness_map,
    encode_mask,
)
from ..oklab.color_space import srgb_to_oklab, oklab_to_srgb


# ═══════════════════════════════════════════════════════════════════════════
#  Part 1: Guilty Gear Xrd — Frame Scheduling & Hold System
# ═══════════════════════════════════════════════════════════════════════════


class AnimationPhaseType(str, Enum):
    """Animation phase types for hold frame detection.

    Based on the Guilty Gear Xrd approach: certain animation phases
    should HOLD (freeze) for multiple frames to create visual impact,
    while transition phases advance normally.
    """
    CONTACT = "contact"       # Foot strike: hold 2 frames
    DOWN = "down"             # Weight absorption: hold 1 frame
    PASSING = "passing"       # Mid-stride: advance normally
    UP = "up"                 # Push-off: advance normally
    IMPACT = "impact"         # Hit/attack impact: hold 3 frames
    APEX = "apex"             # Jump apex: hold 2 frames
    LANDING = "landing"       # Landing impact: hold 2 frames
    ANTICIPATION = "anticipation"  # Wind-up: advance slowly
    RECOVERY = "recovery"     # Recovery: advance normally
    NEUTRAL = "neutral"       # Default: advance normally


@dataclass
class HoldFrameConfig:
    """Configuration for hold frame behavior per phase type.

    Distilled from Guilty Gear Xrd's animation system:
    - Key poses (contact, impact, apex) are HELD for multiple frames
    - Transition poses advance at normal or reduced speed
    - Squash/stretch is applied based on phase type
    """
    hold_duration: int = 1          # Frames to hold (1 = no hold)
    squash_y: float = 1.0           # Y scale factor (< 1 = squash)
    stretch_y: float = 1.0          # Y scale factor (> 1 = stretch)
    outline_boost: float = 0.0      # Extra outline thickness (0-1)
    interpolation: str = "step"     # "step" (GGXrd) or "linear"

    @property
    def squash_x(self) -> float:
        """X scale for volume preservation: x * y ≈ 1."""
        return 1.0 / max(self.squash_y, 0.1) if self.squash_y != 1.0 else 1.0

    @property
    def stretch_x(self) -> float:
        """X scale for volume preservation during stretch."""
        return 1.0 / max(self.stretch_y, 0.1) if self.stretch_y != 1.0 else 1.0


# Default hold frame configurations (Guilty Gear Xrd inspired)
HOLD_FRAME_DEFAULTS: dict[AnimationPhaseType, HoldFrameConfig] = {
    AnimationPhaseType.CONTACT: HoldFrameConfig(
        hold_duration=2, squash_y=0.88, outline_boost=0.3, interpolation="step",
    ),
    AnimationPhaseType.DOWN: HoldFrameConfig(
        hold_duration=1, squash_y=0.82, outline_boost=0.1, interpolation="step",
    ),
    AnimationPhaseType.PASSING: HoldFrameConfig(
        hold_duration=1, squash_y=1.0, outline_boost=0.0, interpolation="step",
    ),
    AnimationPhaseType.UP: HoldFrameConfig(
        hold_duration=1, stretch_y=1.08, outline_boost=0.0, interpolation="step",
    ),
    AnimationPhaseType.IMPACT: HoldFrameConfig(
        hold_duration=3, squash_y=0.75, outline_boost=0.5, interpolation="step",
    ),
    AnimationPhaseType.APEX: HoldFrameConfig(
        hold_duration=2, stretch_y=1.18, outline_boost=0.2, interpolation="step",
    ),
    AnimationPhaseType.LANDING: HoldFrameConfig(
        hold_duration=2, squash_y=0.78, outline_boost=0.4, interpolation="step",
    ),
    AnimationPhaseType.ANTICIPATION: HoldFrameConfig(
        hold_duration=1, squash_y=0.92, outline_boost=0.0, interpolation="step",
    ),
    AnimationPhaseType.RECOVERY: HoldFrameConfig(
        hold_duration=1, squash_y=1.0, outline_boost=0.0, interpolation="step",
    ),
    AnimationPhaseType.NEUTRAL: HoldFrameConfig(
        hold_duration=1, squash_y=1.0, outline_boost=0.0, interpolation="step",
    ),
}


class GuiltyGearFrameScheduler:
    """Frame scheduling system inspired by Guilty Gear Xrd.

    Implements the "Limited Animation" and "Stepped Keys" techniques:
    - Detects animation phase from pose data and phase variable
    - Applies hold frames at key poses (contact, impact, apex)
    - Provides squash/stretch parameters per frame
    - Uses stepped interpolation (snap) instead of smooth blending

    This solves the "uniform twitch" problem caused by evenly-spaced
    frames at low frame rates (6-12fps).

    Usage::

        scheduler = GuiltyGearFrameScheduler()
        for t in frame_times:
            phase_type = scheduler.detect_phase(pose, phase_var)
            should_render, config = scheduler.schedule_frame(t, phase_type)
            if should_render:
                render_frame(pose, squash_y=config.squash_y)
    """

    def __init__(
        self,
        hold_configs: Optional[dict[AnimationPhaseType, HoldFrameConfig]] = None,
        enable_holds: bool = True,
        enable_squash_stretch: bool = True,
    ):
        self._configs = dict(hold_configs or HOLD_FRAME_DEFAULTS)
        self._enable_holds = enable_holds
        self._enable_squash_stretch = enable_squash_stretch

        # State
        self._current_hold_remaining: int = 0
        self._current_config: HoldFrameConfig = self._configs[AnimationPhaseType.NEUTRAL]
        self._last_phase_type: AnimationPhaseType = AnimationPhaseType.NEUTRAL
        self._frame_counter: int = 0

    def detect_phase_type(
        self,
        phase: float,
        velocity_y: float = 0.0,
        is_contact_l: bool = False,
        is_contact_r: bool = False,
        is_impact: bool = False,
        is_apex: bool = False,
        is_landing: bool = False,
    ) -> AnimationPhaseType:
        """Detect the current animation phase type.

        Uses a combination of phase position, vertical velocity, and
        explicit flags to determine the phase type.

        Parameters
        ----------
        phase : float
            Current gait phase [0, 1).
        velocity_y : float
            Vertical velocity of the root/pelvis.
        is_contact_l, is_contact_r : bool
            Foot contact flags.
        is_impact, is_apex, is_landing : bool
            Explicit phase flags from the animation system.
        """
        # Explicit flags take priority
        if is_impact:
            return AnimationPhaseType.IMPACT
        if is_apex:
            return AnimationPhaseType.APEX
        if is_landing:
            return AnimationPhaseType.LANDING

        # Phase-based detection for walk/run cycles
        # PFNN convention: contact @ 0.0 and 0.5, down @ 0.125/0.625
        p = phase % 1.0

        # Contact phases (foot strike moments)
        if abs(p) < 0.06 or abs(p - 0.5) < 0.06:
            return AnimationPhaseType.CONTACT

        # Down phases (weight absorption)
        if abs(p - 0.125) < 0.06 or abs(p - 0.625) < 0.06:
            return AnimationPhaseType.DOWN

        # Up phases (push-off)
        if abs(p - 0.45) < 0.06 or abs(p - 0.95) < 0.06:
            return AnimationPhaseType.UP

        # Passing phases (mid-stride)
        if abs(p - 0.25) < 0.1 or abs(p - 0.75) < 0.1:
            return AnimationPhaseType.PASSING

        return AnimationPhaseType.NEUTRAL

    def schedule_frame(
        self,
        phase_type: AnimationPhaseType,
    ) -> tuple[bool, HoldFrameConfig]:
        """Determine whether to render a new frame or hold the current one.

        Returns
        -------
        (should_advance, config) : tuple
            should_advance: True if a new pose should be rendered
            config: HoldFrameConfig with squash/stretch parameters
        """
        self._frame_counter += 1

        config = self._configs.get(phase_type, self._configs[AnimationPhaseType.NEUTRAL])

        if not self._enable_holds:
            return True, config

        # If we're in a hold, decrement and return False
        if self._current_hold_remaining > 0:
            self._current_hold_remaining -= 1
            return False, self._current_config

        # New phase detected — start hold if configured
        if phase_type != self._last_phase_type:
            self._last_phase_type = phase_type
            self._current_config = config

            if config.hold_duration > 1:
                self._current_hold_remaining = config.hold_duration - 1
                return True, config  # Render first frame, then hold

        return True, config

    def get_squash_stretch(
        self,
        config: HoldFrameConfig,
    ) -> tuple[float, float]:
        """Get the effective squash/stretch scale factors.

        Returns (scale_x, scale_y) with volume preservation.
        """
        if not self._enable_squash_stretch:
            return 1.0, 1.0

        if config.squash_y != 1.0:
            return config.squash_x, config.squash_y
        if config.stretch_y != 1.0:
            return config.stretch_x, config.stretch_y
        return 1.0, 1.0

    def reset(self) -> None:
        """Reset scheduler state."""
        self._current_hold_remaining = 0
        self._current_config = self._configs[AnimationPhaseType.NEUTRAL]
        self._last_phase_type = AnimationPhaseType.NEUTRAL
        self._frame_counter = 0


# ═══════════════════════════════════════════════════════════════════════════
#  Part 2: Dead Cells — No-AA Pixel Art Rendering Pipeline
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class IndustrialRenderAuxiliaryResult:
    """Industrial renderer output bundle with auxiliary textures.

    The albedo sprite remains the primary baked frame. Normal/depth/mask maps
    are exported alongside it so downstream engines or deferred-lighting
    experiments can consume the same procedural character as a compact G-buffer.
    """

    albedo_image: Image.Image
    normal_map_image: Image.Image
    depth_map_image: Image.Image
    thickness_map_image: Image.Image
    roughness_map_image: Image.Image
    mask_image: Image.Image
    metadata: dict[str, float | int | str | dict[str, object]]


def _compute_pseudo_normal(
    x: np.ndarray,
    y: np.ndarray,
    dist: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute pseudo-normal map from SDF using actual field gradients.

    SESSION-044 upgrades the earlier position-only approximation to a true
    field-based method: normals come from the sampled SDF gradient, while the
    z component is lifted by the normalized interior distance. This remains a
    cheap 2D bake, but tracks the silhouette much more faithfully.
    """
    try:
        xs = np.asarray(x[0, :], dtype=np.float64)
        ys = np.asarray(y[:, 0], dtype=np.float64)
        grad_x, grad_y = compute_sdf_gradients(np.asarray(dist, dtype=np.float64), xs, ys)
    except Exception:
        grad_y_raw, grad_x_raw = np.gradient(np.asarray(dist, dtype=np.float64), edge_order=2)
        grad_x = np.asarray(grad_x_raw, dtype=np.float64)
        grad_y = np.asarray(grad_y_raw, dtype=np.float64)

    depth, _ = compute_depth_map(np.asarray(dist, dtype=np.float64))
    inside = np.asarray(dist, dtype=np.float64) < 0.0
    normals = compute_normal_vectors(
        grad_x,
        grad_y,
        depth,
        inside,
        normal_z_base=0.35,
        depth_to_z_scale=0.85,
    )
    return normals[..., 0], normals[..., 1], normals[..., 2]


def _cel_shade_hard(
    nx: np.ndarray,
    ny: np.ndarray,
    nz: np.ndarray,
    light_angle: float,
    light_elevation: float = 0.6,
) -> np.ndarray:
    """Hard cel shading (Dead Cells + Guilty Gear style).

    Unlike the original renderer's smooth lighting, this uses a HARD
    threshold to create the classic cel-shaded look:
    - Above threshold → highlight
    - Below negative threshold → shadow
    - Between → midtone

    No dithering, no gradients. This is critical at 32×32:
    smooth gradients cause pixel crawling artifacts.

    Returns integer shade level: -1 (shadow), 0 (midtone), 1 (highlight).
    """
    # Light direction (3D)
    lx = math.cos(light_angle)
    ly = math.sin(light_angle)
    lz = light_elevation

    # Normalize light
    ll = math.sqrt(lx * lx + ly * ly + lz * lz) + 1e-8
    lx, ly, lz = lx / ll, ly / ll, lz / ll

    # N·L dot product
    ndotl = nx * lx + ny * ly + nz * lz

    # Hard threshold (no smoothstep, no dithering)
    shade = np.zeros_like(ndotl, dtype=np.int8)
    shade[ndotl > 0.25] = 1    # Highlight
    shade[ndotl < -0.15] = -1  # Shadow

    return shade


def _get_cel_colors(
    base_color_srgb: np.ndarray,
    light_angle: float,
) -> dict[str, tuple[int, ...]]:
    """Generate cel-shaded color triplet (Dead Cells style).

    Uses OKLAB for perceptually correct shifts.
    Key difference from original: STRONGER contrast for pixel art readability.
    """
    base_oklab = srgb_to_oklab(base_color_srgb[:3])
    L, a, b = base_oklab[0], base_oklab[1], base_oklab[2]

    # Highlight: stronger warm shift than original
    hl_L = min(L + 0.20, 0.95)
    hl_a = a + 0.03
    hl_b = b + 0.04
    highlight = oklab_to_srgb(np.array([hl_L, hl_a, hl_b]))

    # Shadow: stronger cool shift
    sh_L = max(L - 0.25, 0.06)
    sh_a = a - 0.03
    sh_b = b - 0.05
    shadow = oklab_to_srgb(np.array([sh_L, sh_a, sh_b]))

    return {
        "highlight": (*np.clip(highlight, 0, 255).astype(int), 255),
        "midtone": (*base_color_srgb[:3].astype(int), 255),
        "shadow": (*np.clip(shadow, 0, 255).astype(int), 255),
    }


def _binary_dilate(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Dilate a boolean mask (NumPy-only, no SciPy dependency)."""
    result = mask.astype(bool)
    for _ in range(max(1, iterations)):
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        neighbors = [
            padded[y:y + result.shape[0], x:x + result.shape[1]]
            for y in range(3)
            for x in range(3)
        ]
        result = np.logical_or.reduce(neighbors)
    return result


def _transform_local_gradient_to_world(
    gradient_x: np.ndarray,
    gradient_y: np.ndarray,
    *,
    angle: float,
    scale_x: float,
    scale_y: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Map a local-space gradient back to world/sample space via the Jacobian transpose."""
    c = float(np.cos(angle))
    s = float(np.sin(angle))
    inv_sx = 1.0 / max(scale_x, 0.1)
    inv_sy = 1.0 / max(scale_y, 0.1)
    world_gx = (c * gradient_x - s * gradient_y) * inv_sx
    world_gy = (s * gradient_x + c * gradient_y) * inv_sy
    length = np.sqrt(world_gx * world_gx + world_gy * world_gy)
    length = np.maximum(length, 1e-8)
    return world_gx / length, world_gy / length


def _build_union_gradient_field(
    part_layers: list[tuple[BodyPart, np.ndarray, np.ndarray, np.ndarray]],
    union_dist: np.ndarray,
    *,
    scale_x: float,
    scale_y: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Assemble a union gradient field from per-part analytical gradients.

    For the min-union of SDFs, the active gradient is the gradient of the part
    that currently provides the smallest distance. Where a part has no analytic
    gradient, the caller can later fall back to finite differences.
    """
    best_dist = np.full_like(union_dist, np.inf, dtype=np.float64)
    grad_x = np.zeros_like(union_dist, dtype=np.float64)
    grad_y = np.zeros_like(union_dist, dtype=np.float64)
    covered = np.zeros_like(union_dist, dtype=bool)

    for part, dist, local_x, local_y in part_layers:
        if part.is_outline_only or part.sdf_gradient is None:
            continue
        local = part.sdf_gradient(local_x, local_y)
        world_gx, world_gy = _transform_local_gradient_to_world(
            np.asarray(local.gradient_x, dtype=np.float64),
            np.asarray(local.gradient_y, dtype=np.float64),
            angle=part.rotation,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        use = dist < best_dist
        best_dist[use] = dist[use]
        grad_x[use] = world_gx[use]
        grad_y[use] = world_gy[use]
        covered[use] = True

    return grad_x, grad_y, covered


def _transform_coords_with_squash(
    x: np.ndarray,
    y: np.ndarray,
    tx: float,
    ty: float,
    angle: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    pivot_y: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Transform world coords to local part coords with squash/stretch.

    The squash/stretch is applied relative to a pivot point (typically
    the character's feet for landing squash, or center for general use).
    Volume preservation: scale_x * scale_y ≈ 1.0.
    """
    # Translate
    lx = x - tx
    ly = y - ty

    # Apply squash/stretch around pivot
    if abs(scale_x - 1.0) > 1e-4 or abs(scale_y - 1.0) > 1e-4:
        lx = lx / max(scale_x, 0.1)
        ly_offset = ly - pivot_y
        ly = ly_offset / max(scale_y, 0.1) + pivot_y

    # Rotate (inverse = negative angle)
    if abs(angle) > 1e-6:
        cos_a = np.cos(-angle)
        sin_a = np.sin(-angle)
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a
        return rx, ry

    return lx, ly


# ═══════════════════════════════════════════════════════════════════════════
#  Part 3: Unified Industrial Renderer
# ═══════════════════════════════════════════════════════════════════════════


def _build_character_distance_field(
    skeleton: Skeleton,
    pose: dict[str, float],
    style: CharacterStyle,
    width: int = 32,
    height: int = 32,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[tuple[BodyPart, np.ndarray, np.ndarray, np.ndarray]], np.ndarray]:
    """Build the union distance field for the filled character silhouette."""
    skeleton.apply_pose(pose)
    positions = skeleton.get_joint_positions()

    xs = np.linspace(-0.6, 0.6, width)
    ys = np.linspace(1.1, -0.1, height)
    x, y = np.meshgrid(xs, ys)
    parts = assemble_character(style)
    part_layers: list[tuple[BodyPart, np.ndarray, np.ndarray, np.ndarray]] = []
    union_dist = np.full((height, width), np.inf, dtype=np.float64)

    pivot_y = -0.05
    for part in parts:
        if part.joint_name not in positions:
            continue
        jx, jy = positions[part.joint_name]
        local_x, local_y = _transform_coords_with_squash(
            x,
            y,
            jx + part.offset_x,
            jy + part.offset_y,
            part.rotation,
            scale_x=scale_x,
            scale_y=scale_y,
            pivot_y=pivot_y,
        )
        dist = np.asarray(part.sdf(local_x, local_y), dtype=np.float64)
        part_layers.append((part, dist, local_x, local_y))
        if not part.is_outline_only:
            union_dist = np.minimum(union_dist, dist)

    if not part_layers:
        raise ValueError("Industrial renderer could not build any character parts for the current skeleton/style.")

    return xs, ys, x, y, part_layers, union_dist


def render_character_frame_industrial(
    skeleton: Skeleton,
    pose: dict[str, float],
    style: CharacterStyle,
    width: int = 32,
    height: int = 32,
    palette=None,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    outline_boost: float = 0.0,
    enable_pseudo_normals: bool = True,
    enable_cel_shading: bool = True,
    enable_outline: bool = True,
) -> Image.Image:
    """Render a character frame using the Dead Cells + Guilty Gear pipeline.

    Key differences from the original render_character_frame():

    1. **No anti-aliasing** (Dead Cells): Hard SDF threshold, no smoothstep.
       At 32×32, AA causes pixel edge crawling and mushy silhouettes.

    2. **Pseudo-normal lighting** (Dead Cells): SDF gradient → normal map →
       hard cel shading. Creates volume without 3D geometry.

    3. **Hard cel bands** (Dead Cells + GGXrd): Two-threshold shading with
       NO dithering. Clean, readable at tiny resolution.

    4. **Squash/stretch transform** (GGXrd): Volume-preserving deformation
       applied to the coordinate system before SDF evaluation.

    5. **Boosted outline** (GGXrd): Thicker outline on impact frames for
       enhanced silhouette readability.

    Parameters
    ----------
    skeleton : Skeleton
    pose : dict[str, float]
    style : CharacterStyle
    width, height : int
        Frame size (default 32×32).
    palette : Palette, optional
    scale_x, scale_y : float
        Squash/stretch factors (from GuiltyGearFrameScheduler).
    outline_boost : float
        Extra outline thickness [0, 1] (from GuiltyGearFrameScheduler).
    enable_pseudo_normals : bool
        Use SDF-gradient normal map for lighting (Dead Cells).
    enable_cel_shading : bool
        Use hard cel shading (Dead Cells + GGXrd).
    enable_outline : bool
        Draw pixel outline.
    """
    _, _, X, Y, part_layers, union_dist = _build_character_distance_field(
        skeleton,
        pose,
        style,
        width=width,
        height=height,
        scale_x=scale_x,
        scale_y=scale_y,
    )

    # Get palette colors
    if palette is not None and hasattr(palette, 'colors_srgb') and palette.count >= 6:
        pal_srgb = palette.colors_srgb
    else:
        pal_srgb = np.array([
            [240, 200, 160],  # skin
            [200, 50, 50],    # hair/hat
            [200, 50, 50],    # shirt
            [80, 80, 200],    # pants
            [100, 60, 30],    # shoes
            [30, 20, 15],     # outline
        ], dtype=np.float64)

    img = np.zeros((height, width, 4), dtype=np.uint8)
    all_inside = union_dist < 0.0

    # Outline (Dead Cells: crisp 1px, GGXrd: boosted on impact)
    outline_mask = np.zeros((height, width), dtype=bool)
    effective_outline_iterations = 1
    if outline_boost > 0.3:
        effective_outline_iterations = 2  # Thicker outline on impact

    if enable_outline:
        dilated = _binary_dilate(all_inside, iterations=effective_outline_iterations)
        outline_mask = dilated & ~all_inside

    outline_color = tuple(int(c) for c in pal_srgb[style.outline_color_idx]) + (255,)

    # Render parts back-to-front
    for part, dist, local_x, local_y in part_layers:
        inside = dist < 0  # Hard threshold (Dead Cells: no AA)

        if part.is_outline_only:
            color = tuple(int(c) for c in pal_srgb[part.color_idx]) + (255,)
            img[inside] = color
            continue

        base_srgb = pal_srgb[part.color_idx]

        if enable_cel_shading:
            colors = _get_cel_colors(base_srgb, style.light_angle)

            if enable_pseudo_normals:
                # Dead Cells: pseudo-normal from SDF gradient
                nx, ny, nz = _compute_pseudo_normal(local_x, local_y, dist)
                shade = _cel_shade_hard(nx, ny, nz, style.light_angle)
            else:
                # Fallback: position-based shading
                r = np.sqrt(local_x ** 2 + local_y ** 2) + 1e-6
                ndotl = (local_x * math.cos(style.light_angle) +
                         local_y * math.sin(style.light_angle)) / r
                shade = np.zeros_like(ndotl, dtype=np.int8)
                shade[ndotl > 0.25] = 1
                shade[ndotl < -0.15] = -1

            # Hard cel bands (NO dithering — Dead Cells principle)
            highlight_mask = inside & (shade == 1)
            shadow_mask = inside & (shade == -1)
            midtone_mask = inside & ~highlight_mask & ~shadow_mask

            img[shadow_mask] = colors["shadow"]
            img[midtone_mask] = colors["midtone"]
            img[highlight_mask] = colors["highlight"]
        else:
            color = tuple(int(c) for c in base_srgb) + (255,)
            img[inside] = color

    # Apply outline
    if enable_outline:
        img[outline_mask] = outline_color

        # Internal edge outlines (Dead Cells: part boundaries visible)
        pixel_size = 1.2 / width
        for part, dist, local_x, local_y in part_layers:
            if part.is_outline_only:
                continue
            boundary = (dist > -pixel_size * 1.5) & (dist < pixel_size * 0.5)
            img[boundary & all_inside] = outline_color

    return Image.fromarray(img, "RGBA")


def render_character_maps_industrial(
    skeleton: Skeleton,
    pose: dict[str, float],
    style: CharacterStyle,
    width: int = 32,
    height: int = 32,
    palette=None,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    outline_boost: float = 0.0,
    bake_config: Optional[SDFBakeConfig] = None,
) -> IndustrialRenderAuxiliaryResult:
    """Render an industrial albedo frame plus normal/depth/mask auxiliary maps."""
    bake_config = bake_config or SDFBakeConfig()
    xs, ys, _x, _y, part_layers, union_dist = _build_character_distance_field(
        skeleton,
        pose,
        style,
        width=width,
        height=height,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    inside = union_dist < 0.0
    fd_grad_x, fd_grad_y = compute_sdf_gradients(union_dist, xs, ys, edge_order=bake_config.edge_order)
    analytic_grad_x, analytic_grad_y, analytic_coverage = _build_union_gradient_field(
        part_layers,
        union_dist,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    if bake_config.gradient_mode == "central_difference":
        grad_x, grad_y = fd_grad_x, fd_grad_y
        gradient_source = "central_difference"
    else:
        grad_x = np.where(analytic_coverage, analytic_grad_x, fd_grad_x)
        grad_y = np.where(analytic_coverage, analytic_grad_y, fd_grad_y)
        coverage_ratio = float(np.count_nonzero(analytic_coverage)) / float(max(1, analytic_coverage.size))
        gradient_source = "analytic_union" if coverage_ratio > 0.995 else "analytic_union_hybrid"
    depth_values, depth_scale = compute_depth_map(
        union_dist,
        percentile=bake_config.depth_percentile,
        min_depth_span=bake_config.min_depth_span,
    )
    thickness_values, thickness_scale = compute_thickness_map(
        union_dist,
        percentile=bake_config.thickness_percentile,
        min_depth_span=bake_config.min_depth_span,
    )
    curvature_values = compute_curvature_proxy(grad_x, grad_y, xs, ys, inside)
    roughness_values, roughness_scale = compute_roughness_map(
        curvature_values,
        inside,
        percentile=bake_config.roughness_percentile,
    )
    normal_vectors = compute_normal_vectors(
        grad_x,
        grad_y,
        depth_values,
        inside,
        normal_z_base=bake_config.normal_z_base,
        depth_to_z_scale=bake_config.depth_to_z_scale,
        flat_normal_outside=bake_config.flat_normal_outside,
    )

    albedo_image = render_character_frame_industrial(
        Skeleton.create_humanoid(skeleton.head_units),
        pose,
        style,
        width=width,
        height=height,
        palette=palette,
        scale_x=scale_x,
        scale_y=scale_y,
        outline_boost=outline_boost,
        enable_pseudo_normals=True,
        enable_cel_shading=True,
        enable_outline=True,
    )
    metadata: dict[str, float | int | str | dict[str, object]] = {
        "width": width,
        "height": height,
        "part_count": len(part_layers),
        "inside_pixel_count": int(np.count_nonzero(inside)),
        "depth_scale": float(depth_scale),
        "thickness_scale": float(thickness_scale),
        "roughness_scale": float(roughness_scale),
        "outline_boost": float(outline_boost),
        "gradient_mode": bake_config.gradient_mode,
        "gradient_source": gradient_source,
        "analytic_coverage_pixels": int(np.count_nonzero(analytic_coverage)),
        "analytic_inside_coverage_pixels": int(np.count_nonzero(analytic_coverage & inside)),
        "normal_z_base": float(bake_config.normal_z_base),
        "depth_to_z_scale": float(bake_config.depth_to_z_scale),
        "engine_channels": {
            "albedo": "Primary sprite color",
            "normal": "RGB=XYZ, A=coverage",
            "depth": "RGBA grayscale depth proxy",
            "thickness": "RGBA grayscale subsurface thickness proxy",
            "roughness": "RGBA grayscale inverse-curvature proxy",
            "mask": "RGBA silhouette mask",
        },
        "engine_targets": ["Unity URP 2D", "Godot 4 CanvasItem/2D lighting"],
    }

    return IndustrialRenderAuxiliaryResult(
        albedo_image=albedo_image,
        normal_map_image=encode_normal_map(normal_vectors, inside),
        depth_map_image=encode_depth_map(depth_values, inside),
        thickness_map_image=encode_thickness_map(thickness_values, inside),
        roughness_map_image=encode_roughness_map(roughness_values, inside),
        mask_image=encode_mask(inside),
        metadata=metadata,
    )


def render_character_sheet_industrial(
    skeleton: Skeleton,
    animation_func: Callable[[float], dict[str, float]],
    style: CharacterStyle,
    frames: int = 8,
    frame_width: int = 32,
    frame_height: int = 32,
    palette=None,
    enable_hold_frames: bool = True,
    enable_squash_stretch: bool = True,
    phase_detector: Optional[Callable[[float, dict[str, float]], AnimationPhaseType]] = None,
    **kwargs,
) -> Image.Image:
    """Render a full animation as a sprite sheet with industrial pipeline.

    Integrates the Guilty Gear Xrd frame scheduler:
    - Key poses are held for multiple frames (no interpolation)
    - Squash/stretch is applied per phase
    - The result has variable timing built into the sprite sheet

    Parameters
    ----------
    skeleton : Skeleton
    animation_func : Callable[[float], dict[str, float]]
        Maps t ∈ [0, 1] → pose dict.
    style : CharacterStyle
    frames : int
        Number of output frames.
    frame_width, frame_height : int
    palette : Palette, optional
    enable_hold_frames : bool
        Enable Guilty Gear Xrd hold frame system.
    enable_squash_stretch : bool
        Enable squash/stretch deformation.
    phase_detector : callable, optional
        Custom phase detection function (t, pose) → AnimationPhaseType.
    """
    scheduler = GuiltyGearFrameScheduler(
        enable_holds=enable_hold_frames,
        enable_squash_stretch=enable_squash_stretch,
    )

    sheet = Image.new("RGBA", (frame_width * frames, frame_height), (0, 0, 0, 0))

    # Pre-generate all poses at higher resolution, then apply hold logic
    n_source = frames * 2  # Generate more source frames for hold selection
    source_poses = []
    source_phases = []
    for i in range(n_source):
        t = i / n_source
        pose = animation_func(t)
        source_poses.append(pose)
        source_phases.append(t)

    # Apply frame scheduling
    output_frames = []
    last_rendered_pose = source_poses[0] if source_poses else {}
    last_config = HOLD_FRAME_DEFAULTS[AnimationPhaseType.NEUTRAL]

    source_idx = 0
    while len(output_frames) < frames and source_idx < n_source:
        t = source_phases[source_idx]
        pose = source_poses[source_idx]

        # Detect phase
        if phase_detector:
            phase_type = phase_detector(t, pose)
        else:
            phase_type = scheduler.detect_phase_type(phase=t)

        should_advance, config = scheduler.schedule_frame(phase_type)

        if should_advance:
            last_rendered_pose = pose
            last_config = config
            output_frames.append((last_rendered_pose, last_config))
        else:
            # Hold: repeat last rendered pose with same config
            output_frames.append((last_rendered_pose, last_config))

        source_idx += 1

    # Fill remaining frames if needed
    while len(output_frames) < frames:
        output_frames.append((last_rendered_pose, last_config))

    # Render each frame
    for i, (pose, config) in enumerate(output_frames[:frames]):
        sx, sy = scheduler.get_squash_stretch(config)

        fresh_skel = Skeleton.create_humanoid(skeleton.head_units)
        frame = render_character_frame_industrial(
            fresh_skel, pose, style,
            frame_width, frame_height,
            palette=palette,
            scale_x=sx,
            scale_y=sy,
            outline_boost=config.outline_boost,
            **kwargs,
        )
        sheet.paste(frame, (i * frame_width, 0))

    return sheet


__all__ = [
    "AnimationPhaseType",
    "HoldFrameConfig",
    "HOLD_FRAME_DEFAULTS",
    "GuiltyGearFrameScheduler",
    "IndustrialRenderAuxiliaryResult",
    "render_character_frame_industrial",
    "render_character_maps_industrial",
    "render_character_sheet_industrial",
]
