"""Squash & Stretch export modifier for V6 motion tension.

This module is intentionally an export-layer modifier. It reads solved motion
frames, estimates velocity/acceleration, and annotates outgoing transform data
with a volume-preserving non-uniform deformation. It does not mutate XPBD,
IK, contact solving, or pose generation internals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence


from mathart.core.knowledge_interpreter import interpret_knowledge


_EPS = 1e-8


@dataclass(frozen=True)
class SquashStretchConfig:
    """Parameters for velocity-driven squash & stretch."""

    velocity_threshold: float = 0.05
    acceleration_threshold: float = 0.0
    max_stretch: float = 1.35
    velocity_to_stretch: float = 0.18
    acceleration_to_stretch: float = 0.025
    enabled: bool = True


@dataclass(frozen=True)
class SquashStretchSample:
    """Computed deformation sample for one frame/target."""

    velocity: tuple[float, float]
    acceleration: tuple[float, float]
    speed: float
    acceleration_magnitude: float
    direction: tuple[float, float]
    stretch_scale: float
    perpendicular_scale: float
    matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

    @property
    def determinant(self) -> float:
        a, b, _ = self.matrix[0]
        c, d, _ = self.matrix[1]
        return a * d - b * c

    def to_dict(self) -> dict[str, Any]:
        return {
            "velocity": [float(self.velocity[0]), float(self.velocity[1])],
            "acceleration": [float(self.acceleration[0]), float(self.acceleration[1])],
            "speed": float(self.speed),
            "acceleration_magnitude": float(self.acceleration_magnitude),
            "direction": [float(self.direction[0]), float(self.direction[1])],
            "stretch_scale": float(self.stretch_scale),
            "perpendicular_scale": float(self.perpendicular_scale),
            "matrix": [[float(v) for v in row] for row in self.matrix],
            "determinant": float(self.determinant),
            "volume_preservation": "determinant_1_area_preserving_2d",
        }


def _config_from_knowledge(config: SquashStretchConfig | None = None) -> SquashStretchConfig:
    if config is not None:
        return config
    physics = interpret_knowledge().physics
    return SquashStretchConfig(
        velocity_threshold=physics.squash_velocity_threshold,
        acceleration_threshold=physics.squash_acceleration_threshold,
        max_stretch=physics.squash_max_stretch,
        velocity_to_stretch=physics.squash_velocity_to_stretch,
        acceleration_to_stretch=physics.squash_acceleration_to_stretch,
        enabled=True,
    )


def _vec_len(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def _scale(v: tuple[float, float], s: float) -> tuple[float, float]:
    return (v[0] * s, v[1] * s)


def _normalize(v: tuple[float, float]) -> tuple[float, float]:
    length = _vec_len(v)
    if length <= _EPS:
        return (1.0, 0.0)
    return (v[0] / length, v[1] / length)


def _position_from_root_transform(root_transform: Any) -> tuple[float, float]:
    return (float(getattr(root_transform, "x", 0.0)), float(getattr(root_transform, "y", 0.0)))


def _position_from_pose2d(frame: Any, target: str = "root") -> tuple[float, float]:
    if target == "root" or not getattr(frame, "bone_transforms", None):
        return (float(getattr(frame, "root_x", 0.0)), float(getattr(frame, "root_y", 0.0)))
    xform = frame.bone_transforms.get(target)
    if not isinstance(xform, Mapping):
        return (float(getattr(frame, "root_x", 0.0)), float(getattr(frame, "root_y", 0.0)))
    return (float(xform.get("tx", 0.0)), float(xform.get("ty", 0.0)))


def compute_volume_preserving_matrix(
    velocity: tuple[float, float],
    acceleration: tuple[float, float] = (0.0, 0.0),
    *,
    config: SquashStretchConfig | None = None,
) -> SquashStretchSample:
    """Compute a 2D volume-preserving deformation matrix.

    The matrix stretches along the instantaneous velocity direction by ``s``
    and compresses along the perpendicular direction by ``1 / s``. Therefore
    the 2D determinant is exactly ``s * (1 / s) == 1`` before floating-point
    rounding.
    """

    cfg = _config_from_knowledge(config)
    speed = _vec_len(velocity)
    accel_mag = _vec_len(acceleration)
    active_speed = max(0.0, speed - cfg.velocity_threshold)
    active_accel = max(0.0, accel_mag - cfg.acceleration_threshold)

    if not cfg.enabled or active_speed <= 0.0:
        stretch = 1.0
    else:
        stretch = 1.0 + active_speed * cfg.velocity_to_stretch + active_accel * cfg.acceleration_to_stretch
        stretch = max(1.0, min(cfg.max_stretch, stretch))

    perp = 1.0 / stretch
    direction = _normalize(velocity if speed > _EPS else acceleration)
    dx, dy = direction
    px, py = -dy, dx

    a = stretch * dx * dx + perp * px * px
    b = stretch * dx * dy + perp * px * py
    c = stretch * dy * dx + perp * py * px
    d = stretch * dy * dy + perp * py * py
    matrix = ((a, b, 0.0), (c, d, 0.0), (0.0, 0.0, 1.0))

    return SquashStretchSample(
        velocity=velocity,
        acceleration=acceleration,
        speed=speed,
        acceleration_magnitude=accel_mag,
        direction=direction,
        stretch_scale=stretch,
        perpendicular_scale=perp,
        matrix=matrix,
    )


def compute_samples_from_positions(
    positions: Sequence[tuple[float, float]],
    *,
    fps: float,
    config: SquashStretchConfig | None = None,
) -> list[SquashStretchSample]:
    """Estimate per-frame velocity/acceleration and deformation samples."""

    if not positions:
        return []
    dt = 1.0 / max(float(fps), _EPS)
    velocities: list[tuple[float, float]] = []
    for idx, pos in enumerate(positions):
        if len(positions) == 1:
            velocities.append((0.0, 0.0))
        elif idx == 0:
            velocities.append(_scale(_sub(positions[1], pos), 1.0 / dt))
        elif idx == len(positions) - 1:
            velocities.append(_scale(_sub(pos, positions[idx - 1]), 1.0 / dt))
        else:
            velocities.append(_scale(_sub(positions[idx + 1], positions[idx - 1]), 0.5 / dt))

    accelerations: list[tuple[float, float]] = []
    for idx, vel in enumerate(velocities):
        if len(velocities) == 1:
            accelerations.append((0.0, 0.0))
        elif idx == 0:
            accelerations.append(_scale(_sub(velocities[1], vel), 1.0 / dt))
        elif idx == len(velocities) - 1:
            accelerations.append(_scale(_sub(vel, velocities[idx - 1]), 1.0 / dt))
        else:
            accelerations.append(_scale(_sub(velocities[idx + 1], velocities[idx - 1]), 0.5 / dt))

    return [
        compute_volume_preserving_matrix(v, a, config=config)
        for v, a in zip(velocities, accelerations)
    ]


def apply_to_unified_motion_clip(clip: Any, config: SquashStretchConfig | None = None) -> Any:
    """Return a UMR clip with squash/stretch metadata added before JSON export."""

    positions = [_position_from_root_transform(frame.root_transform) for frame in clip.frames]
    samples = compute_samples_from_positions(positions, fps=float(clip.fps), config=config)
    frames = []
    for frame, sample in zip(clip.frames, samples):
        root_dict = frame.root_transform.to_dict()
        root_dict["squash_stretch_matrix"] = sample.to_dict()["matrix"]
        root_dict["squash_stretch_scale_along_velocity"] = sample.stretch_scale
        root_dict["squash_stretch_scale_perpendicular"] = sample.perpendicular_scale
        metadata = dict(frame.metadata)
        metadata["squash_stretch"] = sample.to_dict()
        frames.append(replace(frame, metadata=metadata))

    metadata = dict(clip.metadata)
    metadata["squash_stretch_modifier"] = {
        "enabled": True,
        "target": "root_centroid",
        "volume_preservation": "determinant_1_area_preserving_2d",
    }
    return replace(clip, frames=frames, metadata=metadata)


def apply_to_clip2d(clip: Any, config: SquashStretchConfig | None = None, target: str = "root") -> Any:
    """Return a Clip2D with export-time scale transforms overlaid."""

    positions = [_position_from_pose2d(frame, target=target) for frame in clip.frames]
    samples = compute_samples_from_positions(positions, fps=float(clip.fps), config=config)
    frames = []
    for frame, sample in zip(clip.frames, samples):
        frame_copy = replace(frame)
        frame_copy.metadata = dict(frame.metadata)
        frame_copy.metadata["squash_stretch"] = sample.to_dict()
        for bone_name, xform in frame.bone_transforms.items():
            new_xform = dict(xform)
            new_xform["sx"] = float(new_xform.get("sx", 1.0)) * sample.stretch_scale
            new_xform["sy"] = float(new_xform.get("sy", 1.0)) * sample.perpendicular_scale
            new_xform["squash_stretch_matrix"] = sample.to_dict()["matrix"]
            new_xform["squash_stretch_determinant"] = sample.determinant
            frame_copy.bone_transforms[bone_name] = new_xform
        frames.append(frame_copy)

    metadata = getattr(clip, "metadata", {}) if hasattr(clip, "metadata") else {}
    clip_copy = replace(clip, frames=frames)
    if isinstance(metadata, dict):
        clip_copy.metadata = dict(metadata)
        clip_copy.metadata["squash_stretch_modifier"] = {
            "enabled": True,
            "target": target,
            "volume_preservation": "determinant_1_area_preserving_2d",
        }
    return clip_copy
