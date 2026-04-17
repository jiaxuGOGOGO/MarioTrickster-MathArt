"""Jakobsen-style lightweight rigid-soft coupling for 2D secondary animation.

This module implements a velocity-less Verlet chain with distance constraints,
root pinning, and simple collision proxies. It is designed for cape/hair style
secondary animation attached to a kinematic 2D skeleton.

Research distillation:
  - Thomas Jakobsen, *Advanced Character Physics* (GDC 2001)
  - Engineering validation from modern Verlet cloth demos using repeated
    constraint satisfaction.

Core design choices for MarioTrickster-MathArt:
  1. Prefer a lightweight 1D chain over full sheet cloth for immediate quality.
  2. Inject rigid-body acceleration as inertial force so the chain lags naturally.
  3. Solve constraints by iterative relaxation with early-stop-friendly behavior.
  4. Use simple circle/capsule proxies before considering heavier XPBD coupling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

import numpy as np

from .skeleton import Skeleton
from .unified_motion import UnifiedMotionFrame, umr_to_pose


_EPS = 1e-8


@dataclass(frozen=True)
class BodyCollisionCircle:
    """Simple circular collision proxy in world space."""

    center: tuple[float, float]
    radius: float
    label: str = "body"


@dataclass(frozen=True)
class SecondaryChainConfig:
    """Configuration for a Jakobsen-style 2D secondary chain."""

    name: str
    anchor_joint: str
    segment_count: int = 6
    segment_length: float = 0.08
    iterations: int = 5
    velocity_retention: float = 0.985
    gravity: tuple[float, float] = (0.0, -0.65)
    anchor_inertia: float = 0.55
    anchor_velocity_influence: float = 0.05
    particle_radius: float = 0.015
    rest_direction: tuple[float, float] = (0.0, -1.0)
    anchor_offset: tuple[float, float] = (0.0, 0.0)
    body_collision_joints: tuple[str, ...] = ("head", "neck", "chest", "spine", "hip")
    body_collision_radii: tuple[float, ...] = (0.10, 0.08, 0.12, 0.10, 0.09)
    ground_y: Optional[float] = 0.0
    stiffness_support: float = 0.25
    tip_mass_scale: float = 1.35

    def __post_init__(self) -> None:
        if self.segment_count < 2:
            raise ValueError("segment_count must be at least 2")
        if self.iterations < 1:
            raise ValueError("iterations must be at least 1")
        if len(self.body_collision_joints) != len(self.body_collision_radii):
            raise ValueError("body_collision_joints and body_collision_radii must align")


@dataclass(frozen=True)
class SecondaryChainDiagnostics:
    """Per-frame diagnostics for one simulated chain."""

    mean_constraint_error: float = 0.0
    max_constraint_error: float = 0.0
    collision_count: int = 0
    tip_speed: float = 0.0
    tip_lag: float = 0.0
    anchor_speed: float = 0.0
    stretch_ratio: float = 1.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "mean_constraint_error": float(self.mean_constraint_error),
            "max_constraint_error": float(self.max_constraint_error),
            "collision_count": int(self.collision_count),
            "tip_speed": float(self.tip_speed),
            "tip_lag": float(self.tip_lag),
            "anchor_speed": float(self.anchor_speed),
            "stretch_ratio": float(self.stretch_ratio),
        }


@dataclass
class SecondaryChainSnapshot:
    """Serializable snapshot of a simulated chain."""

    name: str
    anchor_joint: str
    points: list[tuple[float, float]]
    diagnostics: SecondaryChainDiagnostics

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "anchor_joint": self.anchor_joint,
            "points": [[float(x), float(y)] for x, y in self.points],
            "diagnostics": self.diagnostics.to_dict(),
        }


class JakobsenSecondaryChain:
    """Velocity-less Verlet chain with distance-constraint relaxation."""

    def __init__(self, config: SecondaryChainConfig):
        self.config = config
        self._positions = np.zeros((config.segment_count, 2), dtype=np.float64)
        self._previous_positions = np.zeros_like(self._positions)
        self._rest_lengths = np.full(config.segment_count - 1, config.segment_length, dtype=np.float64)
        self._inv_masses = np.ones(config.segment_count, dtype=np.float64)
        self._inv_masses[0] = 0.0  # anchored root
        if config.segment_count > 1:
            mass_ramp = np.linspace(1.0, config.tip_mass_scale, config.segment_count - 1)
            self._inv_masses[1:] = 1.0 / np.maximum(mass_ramp, _EPS)
        self._initialized = False
        self._previous_anchor_position = np.zeros(2, dtype=np.float64)
        self._previous_anchor_velocity = np.zeros(2, dtype=np.float64)
        self._last_diagnostics = SecondaryChainDiagnostics()

    def reset(self) -> None:
        self._positions[:] = 0.0
        self._previous_positions[:] = 0.0
        self._previous_anchor_position[:] = 0.0
        self._previous_anchor_velocity[:] = 0.0
        self._initialized = False
        self._last_diagnostics = SecondaryChainDiagnostics()

    @property
    def last_diagnostics(self) -> SecondaryChainDiagnostics:
        return self._last_diagnostics

    def _initial_direction(self) -> np.ndarray:
        direction = np.asarray(self.config.rest_direction, dtype=np.float64)
        norm = float(np.linalg.norm(direction))
        if norm < _EPS:
            return np.array([0.0, -1.0], dtype=np.float64)
        return direction / norm

    def _initialize(self, anchor_position: np.ndarray, anchor_velocity: np.ndarray, dt: float) -> None:
        direction = self._initial_direction()
        offset = np.asarray(self.config.anchor_offset, dtype=np.float64)
        root = anchor_position + offset
        for idx in range(self.config.segment_count):
            self._positions[idx] = root + direction * (self.config.segment_length * idx)
            self._previous_positions[idx] = self._positions[idx] - anchor_velocity * dt
        self._positions[0] = root
        self._previous_positions[0] = root - anchor_velocity * dt
        self._previous_anchor_position = root.copy()
        self._previous_anchor_velocity = anchor_velocity.copy()
        self._initialized = True

    def _project_collisions(
        self,
        circles: Iterable[BodyCollisionCircle],
    ) -> int:
        collision_count = 0
        for idx in range(1, self.config.segment_count):
            point = self._positions[idx]
            for circle in circles:
                center = np.asarray(circle.center, dtype=np.float64)
                delta = point - center
                dist = float(np.linalg.norm(delta))
                min_dist = float(circle.radius + self.config.particle_radius)
                if dist < min_dist:
                    if dist < _EPS:
                        fallback = point - self._positions[idx - 1]
                        fb_norm = float(np.linalg.norm(fallback))
                        if fb_norm < _EPS:
                            fallback = self._initial_direction()
                            fb_norm = float(np.linalg.norm(fallback))
                        delta = fallback / max(fb_norm, _EPS)
                        dist = 1.0
                    point = center + delta / dist * min_dist
                    self._positions[idx] = point
                    collision_count += 1
            if self.config.ground_y is not None and point[1] < self.config.ground_y + self.config.particle_radius:
                self._positions[idx, 1] = self.config.ground_y + self.config.particle_radius
                collision_count += 1
        return collision_count

    def _satisfy_distance_constraints(self) -> tuple[float, float]:
        total_error = 0.0
        max_error = 0.0
        support_rest = self.config.segment_length * 2.0
        for idx in range(self.config.segment_count - 1):
            delta = self._positions[idx + 1] - self._positions[idx]
            dist = float(np.linalg.norm(delta))
            if dist < _EPS:
                continue
            diff = (dist - self._rest_lengths[idx]) / dist
            correction = delta * diff
            w1 = self._inv_masses[idx]
            w2 = self._inv_masses[idx + 1]
            wsum = w1 + w2
            if wsum > _EPS:
                self._positions[idx] += correction * (w1 / wsum)
                self._positions[idx + 1] -= correction * (w2 / wsum)
            err = abs(dist - self._rest_lengths[idx])
            total_error += err
            max_error = max(max_error, err)

        if self.config.stiffness_support > 0.0 and self.config.segment_count > 2:
            weight = float(np.clip(self.config.stiffness_support, 0.0, 1.0))
            for idx in range(self.config.segment_count - 2):
                delta = self._positions[idx + 2] - self._positions[idx]
                dist = float(np.linalg.norm(delta))
                if dist < _EPS:
                    continue
                diff = (dist - support_rest) / dist
                correction = delta * diff * weight
                w1 = self._inv_masses[idx]
                w2 = self._inv_masses[idx + 2]
                wsum = w1 + w2
                if wsum > _EPS:
                    self._positions[idx] += correction * (w1 / wsum)
                    self._positions[idx + 2] -= correction * (w2 / wsum)
        mean_error = total_error / max(1, self.config.segment_count - 1)
        return mean_error, max_error

    def step(
        self,
        anchor_position: tuple[float, float],
        dt: float = 1.0 / 60.0,
        *,
        anchor_velocity: Optional[tuple[float, float]] = None,
        anchor_acceleration: Optional[tuple[float, float]] = None,
        collision_circles: Iterable[BodyCollisionCircle] = (),
    ) -> SecondaryChainSnapshot:
        dt = max(float(dt), 1e-4)
        anchor = np.asarray(anchor_position, dtype=np.float64)
        velocity = (
            np.asarray(anchor_velocity, dtype=np.float64)
            if anchor_velocity is not None
            else (anchor - self._previous_anchor_position) / dt
        )
        acceleration = (
            np.asarray(anchor_acceleration, dtype=np.float64)
            if anchor_acceleration is not None
            else (velocity - self._previous_anchor_velocity) / dt
        )

        if not self._initialized:
            self._initialize(anchor, velocity, dt)

        root = anchor + np.asarray(self.config.anchor_offset, dtype=np.float64)
        self._positions[0] = root
        self._previous_positions[0] = root - velocity * dt

        inertial_acc = -acceleration * self.config.anchor_inertia
        velocity_drag = -velocity * self.config.anchor_velocity_influence
        total_acc = np.asarray(self.config.gravity, dtype=np.float64) + inertial_acc + velocity_drag

        for idx in range(1, self.config.segment_count):
            current = self._positions[idx].copy()
            delta = (self._positions[idx] - self._previous_positions[idx]) * self.config.velocity_retention
            self._positions[idx] = self._positions[idx] + delta + total_acc * (dt * dt)
            self._previous_positions[idx] = current

        mean_error = 0.0
        max_error = 0.0
        collision_count = 0
        for _ in range(self.config.iterations):
            self._positions[0] = root
            self._previous_positions[0] = root - velocity * dt
            mean_error, max_error = self._satisfy_distance_constraints()
            collision_count += self._project_collisions(collision_circles)
            self._positions[0] = root

        tip_velocity = (self._positions[-1] - self._previous_positions[-1]) / dt
        tip_speed = float(np.linalg.norm(tip_velocity))
        anchor_speed = float(np.linalg.norm(velocity))
        tip_lag = float(np.linalg.norm(self._positions[-1] - root))
        current_lengths = np.linalg.norm(np.diff(self._positions, axis=0), axis=1)
        stretch_ratio = float(np.mean(current_lengths / np.maximum(self._rest_lengths, _EPS))) if len(current_lengths) else 1.0

        self._previous_anchor_position = root.copy()
        self._previous_anchor_velocity = velocity.copy()
        self._last_diagnostics = SecondaryChainDiagnostics(
            mean_constraint_error=mean_error,
            max_constraint_error=max_error,
            collision_count=collision_count,
            tip_speed=tip_speed,
            tip_lag=tip_lag,
            anchor_speed=anchor_speed,
            stretch_ratio=stretch_ratio,
        )
        return SecondaryChainSnapshot(
            name=self.config.name,
            anchor_joint=self.config.anchor_joint,
            points=[(float(p[0]), float(p[1])) for p in self._positions],
            diagnostics=self._last_diagnostics,
        )


def create_default_secondary_chain_configs(head_units: float = 3.0) -> list[SecondaryChainConfig]:
    """Create repository-native cape/hair presets."""
    hu = 1.0 / max(float(head_units), 1.0)
    return [
        SecondaryChainConfig(
            name="cape",
            anchor_joint="chest",
            segment_count=6,
            segment_length=hu * 0.38,
            iterations=5,
            velocity_retention=0.982,
            gravity=(0.0, -hu * 2.8),
            anchor_inertia=0.65,
            anchor_velocity_influence=0.05,
            particle_radius=hu * 0.10,
            rest_direction=(0.0, -1.0),
            anchor_offset=(0.0, -hu * 0.18),
            body_collision_joints=("head", "neck", "chest", "spine", "hip"),
            body_collision_radii=(hu * 0.34, hu * 0.24, hu * 0.38, hu * 0.28, hu * 0.24),
            ground_y=0.0,
            stiffness_support=0.35,
            tip_mass_scale=1.45,
        ),
        SecondaryChainConfig(
            name="hair",
            anchor_joint="head",
            segment_count=5,
            segment_length=hu * 0.20,
            iterations=4,
            velocity_retention=0.986,
            gravity=(0.0, -hu * 1.6),
            anchor_inertia=0.48,
            anchor_velocity_influence=0.04,
            particle_radius=hu * 0.08,
            rest_direction=(0.0, -1.0),
            anchor_offset=(0.0, -hu * 0.06),
            body_collision_joints=("head", "neck", "chest"),
            body_collision_radii=(hu * 0.32, hu * 0.18, hu * 0.20),
            ground_y=0.0,
            stiffness_support=0.18,
            tip_mass_scale=1.20,
        ),
    ]


class SecondaryChainProjector:
    """Attach lightweight Jakobsen chains to a kinematic UMR skeleton."""

    def __init__(
        self,
        configs: Optional[list[SecondaryChainConfig]] = None,
        *,
        skeleton_ref: Optional[Skeleton] = None,
        head_units: float = 3.0,
    ):
        self._skeleton = skeleton_ref or Skeleton.create_humanoid(head_units=head_units)
        self._chains = {
            cfg.name: JakobsenSecondaryChain(cfg)
            for cfg in (configs or create_default_secondary_chain_configs(head_units=head_units))
        }

    def reset(self) -> None:
        for chain in self._chains.values():
            chain.reset()

    def _joint_positions_for_frame(self, frame: UnifiedMotionFrame) -> dict[str, tuple[float, float]]:
        pose = umr_to_pose(frame)
        self._skeleton.apply_pose(pose)
        positions = self._skeleton.forward_kinematics()
        root = frame.root_transform
        return {
            name: (float(x + root.x), float(y + root.y))
            for name, (x, y) in positions.items()
        }

    def _build_collision_circles(
        self,
        cfg: SecondaryChainConfig,
        joint_positions: dict[str, tuple[float, float]],
    ) -> list[BodyCollisionCircle]:
        circles: list[BodyCollisionCircle] = []
        for joint_name, radius in zip(cfg.body_collision_joints, cfg.body_collision_radii):
            if joint_name in joint_positions:
                circles.append(
                    BodyCollisionCircle(
                        center=joint_positions[joint_name],
                        radius=float(radius),
                        label=joint_name,
                    )
                )
        return circles

    def step_frame(self, frame: UnifiedMotionFrame, dt: float = 1.0 / 60.0) -> UnifiedMotionFrame:
        joint_positions = self._joint_positions_for_frame(frame)
        root = frame.root_transform
        root_velocity = np.array([root.velocity_x, root.velocity_y], dtype=np.float64)
        chain_tracks: dict[str, list[list[float]]] = {}
        chain_debug: dict[str, dict[str, float | int | str]] = {}

        for name, chain in self._chains.items():
            cfg = chain.config
            if cfg.anchor_joint not in joint_positions:
                continue
            snapshot = chain.step(
                joint_positions[cfg.anchor_joint],
                dt=dt,
                anchor_velocity=(float(root_velocity[0]), float(root_velocity[1])),
                collision_circles=self._build_collision_circles(cfg, joint_positions),
            )
            chain_tracks[name] = [[float(x), float(y)] for x, y in snapshot.points]
            chain_debug[name] = {
                "anchor_joint": cfg.anchor_joint,
                **snapshot.diagnostics.to_dict(),
            }

        merged_metadata = dict(frame.metadata)
        merged_metadata.update(
            {
                "secondary_chain_projected": True,
                "secondary_chain_count": len(chain_tracks),
                "secondary_chains": chain_tracks,
                "secondary_chain_debug": chain_debug,
            }
        )
        return replace(frame, metadata=merged_metadata)

    def project_frame_sequence(
        self,
        frames: list[UnifiedMotionFrame],
        dt: float = 1.0 / 60.0,
    ) -> list[UnifiedMotionFrame]:
        self.reset()
        return [self.step_frame(frame, dt=dt) for frame in frames]


__all__ = [
    "BodyCollisionCircle",
    "SecondaryChainConfig",
    "SecondaryChainDiagnostics",
    "SecondaryChainSnapshot",
    "JakobsenSecondaryChain",
    "SecondaryChainProjector",
    "create_default_secondary_chain_configs",
]
