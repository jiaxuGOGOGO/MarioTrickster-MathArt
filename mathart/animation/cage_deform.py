"""2D Cage Deformation — Harmonic-coordinate-based sprite deformation.

SESSION-018: New module for producing deformation animations (squish,
stretch, wobble, morph) without skeletal rigging.

Theory:
  A "cage" is a polygon surrounding the sprite. Each pixel inside the cage
  has barycentric/harmonic weights relative to cage vertices. Moving cage
  vertices smoothly deforms the interior.

  For pixel art, we use Mean Value Coordinates (MVC) which are:
  - Smooth (C-infinity inside the cage)
  - Reproduce linear functions exactly
  - Fast to compute (O(n) per pixel, n = cage vertices)

  Reference: Floater, M.S. (2003). "Mean value coordinates."
  Computer Aided Geometric Design.

Usage::

    from mathart.animation.cage_deform import CageDeformer, CagePreset

    deformer = CageDeformer(sprite_image)
    frames = deformer.animate(CagePreset.squash_stretch(), n_frames=12)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class CageKeyframe:
    """A keyframe defining cage vertex positions at a specific time."""
    time: float  # 0.0 to 1.0
    vertices: list[tuple[float, float]]  # Normalized [0,1] coordinates


@dataclass
class CageAnimation:
    """A sequence of cage keyframes defining a deformation animation."""
    keyframes: list[CageKeyframe]
    loop: bool = True
    name: str = "deform"


class CagePreset:
    """Preset cage animations for common deformation effects."""

    @staticmethod
    def _rect_cage(
        cx: float = 0.5, cy: float = 0.5,
        hw: float = 0.45, hh: float = 0.45,
    ) -> list[tuple[float, float]]:
        """Create a rectangular cage around center."""
        return [
            (cx - hw, cy - hh),  # top-left
            (cx + hw, cy - hh),  # top-right
            (cx + hw, cy + hh),  # bottom-right
            (cx - hw, cy + hh),  # bottom-left
        ]

    @classmethod
    def squash_stretch(cls, intensity: float = 0.2) -> CageAnimation:
        """Classic squash & stretch (Disney principle #1)."""
        rest = cls._rect_cage()
        s = intensity

        # Squash: wider, shorter
        squash = [
            (0.5 - 0.45 - s, 0.5 - 0.45 + s),
            (0.5 + 0.45 + s, 0.5 - 0.45 + s),
            (0.5 + 0.45 + s, 0.5 + 0.45),
            (0.5 - 0.45 - s, 0.5 + 0.45),
        ]

        # Stretch: taller, narrower
        stretch = [
            (0.5 - 0.45 + s, 0.5 - 0.45 - s),
            (0.5 + 0.45 - s, 0.5 - 0.45 - s),
            (0.5 + 0.45 - s, 0.5 + 0.45),
            (0.5 - 0.45 + s, 0.5 + 0.45),
        ]

        return CageAnimation(
            keyframes=[
                CageKeyframe(time=0.0, vertices=rest),
                CageKeyframe(time=0.25, vertices=squash),
                CageKeyframe(time=0.5, vertices=rest),
                CageKeyframe(time=0.75, vertices=stretch),
                CageKeyframe(time=1.0, vertices=rest),
            ],
            loop=True,
            name="squash_stretch",
        )

    @classmethod
    def wobble(cls, intensity: float = 0.1) -> CageAnimation:
        """Jelly-like wobble effect."""
        rest = cls._rect_cage()
        s = intensity

        kfs = [CageKeyframe(time=0.0, vertices=rest)]
        n_steps = 8
        for i in range(1, n_steps + 1):
            t = i / n_steps
            phase = t * 2 * math.pi
            verts = []
            for j, (vx, vy) in enumerate(rest):
                # Each vertex wobbles with different phase
                offset_x = s * math.sin(phase + j * math.pi / 2) * (1 - t * 0.5)
                offset_y = s * math.cos(phase + j * math.pi / 3) * (1 - t * 0.5)
                verts.append((vx + offset_x, vy + offset_y))
            kfs.append(CageKeyframe(time=t, vertices=verts))

        return CageAnimation(keyframes=kfs, loop=True, name="wobble")

    @classmethod
    def breathe(cls, intensity: float = 0.08) -> CageAnimation:
        """Gentle breathing/pulsing effect."""
        rest = cls._rect_cage()
        s = intensity

        expanded = [
            (v[0] + (v[0] - 0.5) * s, v[1] + (v[1] - 0.5) * s)
            for v in rest
        ]
        contracted = [
            (v[0] - (v[0] - 0.5) * s * 0.5, v[1] - (v[1] - 0.5) * s * 0.5)
            for v in rest
        ]

        return CageAnimation(
            keyframes=[
                CageKeyframe(time=0.0, vertices=rest),
                CageKeyframe(time=0.3, vertices=expanded),
                CageKeyframe(time=0.6, vertices=rest),
                CageKeyframe(time=0.8, vertices=contracted),
                CageKeyframe(time=1.0, vertices=rest),
            ],
            loop=True,
            name="breathe",
        )

    @classmethod
    def lean(cls, intensity: float = 0.12) -> CageAnimation:
        """Lean left/right (anticipation effect)."""
        rest = cls._rect_cage()
        s = intensity

        lean_right = [
            (rest[0][0] + s, rest[0][1]),
            (rest[1][0] + s, rest[1][1]),
            (rest[2][0], rest[2][1]),
            (rest[3][0], rest[3][1]),
        ]
        lean_left = [
            (rest[0][0] - s, rest[0][1]),
            (rest[1][0] - s, rest[1][1]),
            (rest[2][0], rest[2][1]),
            (rest[3][0], rest[3][1]),
        ]

        return CageAnimation(
            keyframes=[
                CageKeyframe(time=0.0, vertices=rest),
                CageKeyframe(time=0.25, vertices=lean_right),
                CageKeyframe(time=0.5, vertices=rest),
                CageKeyframe(time=0.75, vertices=lean_left),
                CageKeyframe(time=1.0, vertices=rest),
            ],
            loop=True,
            name="lean",
        )


class CageDeformer:
    """2D cage deformation using Mean Value Coordinates.

    Parameters
    ----------
    image : PIL.Image
        The sprite image to deform.
    cage_vertices : list of (x, y), optional
        Initial cage vertices in normalized [0,1] coordinates.
        Defaults to a rectangle around the sprite.
    """

    def __init__(
        self,
        image: Image.Image,
        cage_vertices: Optional[list[tuple[float, float]]] = None,
    ):
        self.original = image.convert("RGBA")
        self.w, self.h = self.original.size
        self.original_arr = np.array(self.original, dtype=np.float32)

        # Default cage: rectangle
        if cage_vertices is None:
            cage_vertices = CagePreset._rect_cage()
        self.rest_cage = np.array(cage_vertices, dtype=np.float64)

        # Precompute MVC weights for all pixels
        self._weights = self._compute_mvc_weights()

    def _compute_mvc_weights(self) -> np.ndarray:
        """Compute Mean Value Coordinate weights for each pixel.

        Returns array of shape (H, W, n_cage_vertices).
        """
        n = len(self.rest_cage)
        weights = np.zeros((self.h, self.w, n), dtype=np.float64)

        # Create pixel grid in normalized coordinates
        xs = np.linspace(0, 1, self.w)
        ys = np.linspace(0, 1, self.h)
        X, Y = np.meshgrid(xs, ys)

        for k in range(n):
            v_prev = self.rest_cage[(k - 1) % n]
            v_curr = self.rest_cage[k]
            v_next = self.rest_cage[(k + 1) % n]

            # Vectors from pixel to cage vertices
            dx_curr = v_curr[0] - X
            dy_curr = v_curr[1] - Y
            dist_curr = np.sqrt(dx_curr**2 + dy_curr**2) + 1e-10

            dx_prev = v_prev[0] - X
            dy_prev = v_prev[1] - Y
            dist_prev = np.sqrt(dx_prev**2 + dy_prev**2) + 1e-10

            dx_next = v_next[0] - X
            dy_next = v_next[1] - Y
            dist_next = np.sqrt(dx_next**2 + dy_next**2) + 1e-10

            # Angles
            def angle_between(ax, ay, bx, by):
                dot = ax * bx + ay * by
                cross = ax * by - ay * bx
                return np.arctan2(cross, dot)

            alpha_prev = angle_between(dx_prev, dy_prev, dx_curr, dy_curr)
            alpha_curr = angle_between(dx_curr, dy_curr, dx_next, dy_next)

            # MVC weight
            w_k = (np.tan(alpha_prev / 2) + np.tan(alpha_curr / 2)) / dist_curr
            weights[:, :, k] = np.abs(w_k)

        # Normalize weights to sum to 1
        weight_sum = weights.sum(axis=2, keepdims=True)
        weight_sum = np.maximum(weight_sum, 1e-10)
        weights /= weight_sum

        return weights

    def deform(
        self,
        target_cage: list[tuple[float, float]],
    ) -> Image.Image:
        """Deform the image using new cage vertex positions.

        Parameters
        ----------
        target_cage : list of (x, y)
            New cage vertex positions in normalized [0,1] coordinates.

        Returns
        -------
        PIL.Image (RGBA)
        """
        target = np.array(target_cage, dtype=np.float64)

        # Compute new position for each pixel using MVC weights
        # new_pos = sum(weight_k * target_k)
        new_x = np.sum(self._weights * target[:, 0][np.newaxis, np.newaxis, :], axis=2)
        new_y = np.sum(self._weights * target[:, 1][np.newaxis, np.newaxis, :], axis=2)

        # Convert to pixel coordinates for sampling
        src_x = (new_x * self.w).astype(np.float64)
        src_y = (new_y * self.h).astype(np.float64)

        # Inverse mapping: for each output pixel, find source pixel
        # We need the inverse: given output position, where does it come from?
        # Actually, MVC gives us forward mapping (rest -> deformed).
        # For rendering, we need inverse mapping.
        # Approximate: compute displacement and apply inverse.

        # Forward displacement
        rest_x = np.linspace(0, 1, self.w)[np.newaxis, :] * np.ones((self.h, 1))
        rest_y = np.linspace(0, 1, self.h)[:, np.newaxis] * np.ones((1, self.w))

        disp_x = new_x - rest_x
        disp_y = new_y - rest_y

        # Inverse: sample from (current_pos - displacement)
        sample_x = rest_x - disp_x
        sample_y = rest_y - disp_y

        # Convert to pixel coordinates
        px = (sample_x * (self.w - 1)).clip(0, self.w - 1)
        py = (sample_y * (self.h - 1)).clip(0, self.h - 1)

        # Nearest-neighbor sampling (pixel art style)
        px_int = np.round(px).astype(np.int32)
        py_int = np.round(py).astype(np.int32)

        result = self.original_arr[py_int, px_int]
        return Image.fromarray(result.astype(np.uint8), "RGBA")

    def animate(
        self,
        animation: CageAnimation,
        n_frames: int = 12,
    ) -> list[Image.Image]:
        """Generate animation frames from a cage animation.

        Parameters
        ----------
        animation : CageAnimation
            The cage animation with keyframes.
        n_frames : int
            Number of output frames.

        Returns
        -------
        list of PIL.Image (RGBA)
        """
        frames = []
        keyframes = animation.keyframes

        for i in range(n_frames):
            t = i / max(1, n_frames - 1) if not animation.loop else i / n_frames

            # Find surrounding keyframes
            kf_before = keyframes[0]
            kf_after = keyframes[-1]
            for j in range(len(keyframes) - 1):
                if keyframes[j].time <= t <= keyframes[j + 1].time:
                    kf_before = keyframes[j]
                    kf_after = keyframes[j + 1]
                    break

            # Interpolate
            if abs(kf_after.time - kf_before.time) < 1e-6:
                local_t = 0.0
            else:
                local_t = (t - kf_before.time) / (kf_after.time - kf_before.time)

            # Smooth interpolation (ease in-out)
            local_t = local_t * local_t * (3 - 2 * local_t)

            # Interpolate cage vertices
            interp_verts = []
            for v_before, v_after in zip(kf_before.vertices, kf_after.vertices):
                vx = v_before[0] * (1 - local_t) + v_after[0] * local_t
                vy = v_before[1] * (1 - local_t) + v_after[1] * local_t
                interp_verts.append((vx, vy))

            frame = self.deform(interp_verts)
            frames.append(frame)

        return frames

    def export_gif(
        self,
        frames: list[Image.Image],
        path: str,
        fps: int = 12,
        loop: bool = True,
    ) -> None:
        """Export frames as animated GIF."""
        duration = max(16, 1000 // fps)
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0 if loop else 1,
            disposal=2,
        )

    def export_spritesheet(
        self,
        frames: list[Image.Image],
        path: str,
    ) -> dict:
        """Export frames as horizontal spritesheet with metadata."""
        n = len(frames)
        cs = self.w
        sheet = Image.new("RGBA", (cs * n, self.h), (0, 0, 0, 0))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * cs, 0))
        sheet.save(path)

        return {
            "meta": {
                "image": str(path),
                "format": "RGBA8888",
                "size": {"w": cs * n, "h": self.h},
                "generator": "MarioTrickster-MathArt/CageDeformer",
            },
            "frames": [
                {"name": f"deform_{i:02d}", "rect": [i * cs, 0, cs, self.h]}
                for i in range(n)
            ],
        }
