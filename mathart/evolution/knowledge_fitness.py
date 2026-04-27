"""Knowledge-driven physics fitness for V6 evolution.

This module is the Phase 4 replacement for obsolete image/AI temporal fitness
inside ``mathart.evolution``. It evaluates solved skeletal motion as pure math:
positions, velocities, accelerations, anticipation displacement, and impact
sharpness. All weights are sourced from ``KnowledgeInterpreter.PhysicsParams``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from mathart.core.knowledge_interpreter import PhysicsParams, interpret_knowledge


_EPS = 1e-8


@dataclass(frozen=True)
class MotionSample:
    """One skeletal point sample used by the pure-math fitness engine."""

    time: float
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class BookLawFitnessReport:
    """Fitness dimensions derived from animation book laws."""

    anticipation_score: float
    impact_sharpness: float
    physics_prior_score: float
    combined_score: float
    weights: dict[str, float]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _vec_sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def _vec_scale(v: tuple[float, float], s: float) -> tuple[float, float]:
    return (v[0] * s, v[1] * s)


def _vec_dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _vec_len(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _normalize(v: tuple[float, float]) -> tuple[float, float]:
    length = _vec_len(v)
    if length <= _EPS:
        return (0.0, 0.0)
    return (v[0] / length, v[1] / length)


def _position_from_any_frame(frame: Any, joint: str) -> tuple[float, float] | None:
    if isinstance(frame, MotionSample):
        return frame.positions.get(joint) or frame.positions.get("root")

    if isinstance(frame, Mapping):
        positions = frame.get("positions") or frame.get("joint_positions") or frame.get("joints")
        if isinstance(positions, Mapping):
            raw = positions.get(joint) or positions.get("root")
            if isinstance(raw, Mapping):
                return (float(raw.get("x", 0.0)), float(raw.get("y", 0.0)))
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 2:
                return (float(raw[0]), float(raw[1]))
        root = frame.get("root_transform") or frame.get("root")
        if isinstance(root, Mapping):
            return (float(root.get("x", 0.0)), float(root.get("y", 0.0)))
        return None

    if hasattr(frame, "joint_local_rotations") and hasattr(frame, "root_transform"):
        root = getattr(frame, "root_transform")
        return (float(getattr(root, "x", 0.0)), float(getattr(root, "y", 0.0)))

    if hasattr(frame, "bone_transforms"):
        if joint == "root" or joint not in frame.bone_transforms:
            return (float(getattr(frame, "root_x", 0.0)), float(getattr(frame, "root_y", 0.0)))
        xform = frame.bone_transforms.get(joint)
        if isinstance(xform, Mapping):
            return (float(xform.get("tx", 0.0)), float(xform.get("ty", 0.0)))
    return None


def _time_from_any_frame(frame: Any, index: int, fps: float) -> float:
    if isinstance(frame, MotionSample):
        return float(frame.time)
    if isinstance(frame, Mapping) and "time" in frame:
        return float(frame.get("time", 0.0))
    if hasattr(frame, "time"):
        return float(getattr(frame, "time"))
    return index / max(float(fps), _EPS)


def extract_motion_samples(
    frames: Sequence[Any],
    *,
    joints: Sequence[str] = ("root", "l_hand", "r_hand", "l_foot", "r_foot", "weapon"),
    fps: float = 60.0,
) -> list[MotionSample]:
    """Normalize UMR/Pose2D/dict frames into mathematical point samples."""

    samples: list[MotionSample] = []
    for idx, frame in enumerate(frames):
        positions: dict[str, tuple[float, float]] = {}
        for joint in joints:
            pos = _position_from_any_frame(frame, joint)
            if pos is not None:
                positions[joint] = pos
        if positions:
            samples.append(MotionSample(time=_time_from_any_frame(frame, idx, fps), positions=positions))
    return samples


class KnowledgeDrivenFitnessEngine:
    """Pure-math fitness engine connected to KnowledgeInterpreter.PhysicsParams."""

    def __init__(self, physics_params: PhysicsParams | None = None, *, knowledge_path: str | None = None) -> None:
        self.knowledge = interpret_knowledge(knowledge_path)
        self.physics_params = physics_params or self.knowledge.physics

    @property
    def weights(self) -> dict[str, float]:
        return {
            "anticipation": max(0.0, float(self.physics_params.anticipation_weight)),
            "impact_sharpness": max(0.0, float(self.physics_params.impact_reward_weight)),
            "physics_prior": max(0.0, 0.5 * float(self.physics_params.anticipation_weight + self.physics_params.impact_reward_weight)),
        }

    def evaluate(self, frames: Sequence[Any], *, fps: float = 60.0, target_joint: str = "root") -> BookLawFitnessReport:
        samples = extract_motion_samples(frames, fps=fps)
        return self.evaluate_samples(samples, fps=fps, target_joint=target_joint)

    def evaluate_samples(
        self,
        samples: Sequence[MotionSample],
        *,
        fps: float = 60.0,
        target_joint: str = "root",
    ) -> BookLawFitnessReport:
        if len(samples) < 4:
            return BookLawFitnessReport(
                anticipation_score=0.0,
                impact_sharpness=0.0,
                physics_prior_score=0.0,
                combined_score=0.0,
                weights=self.weights,
                details={"reason": "insufficient_motion_samples", "sample_count": len(samples)},
            )

        positions = [sample.positions.get(target_joint) or sample.positions.get("root") for sample in samples]
        positions = [p for p in positions if p is not None]
        if len(positions) < 4:
            return BookLawFitnessReport(
                anticipation_score=0.0,
                impact_sharpness=0.0,
                physics_prior_score=0.0,
                combined_score=0.0,
                weights=self.weights,
                details={"reason": "target_joint_missing", "target_joint": target_joint},
            )

        dt = 1.0 / max(float(fps), _EPS)
        velocities = self._velocities(positions, dt)
        accelerations = self._accelerations(velocities, dt)
        speeds = [_vec_len(v) for v in velocities]
        accel_mags = [_vec_len(a) for a in accelerations]
        key_idx = max(range(len(accel_mags)), key=lambda i: accel_mags[i])

        anticipation = self._anticipation_score(positions, velocities, accelerations, key_idx)
        impact = self._impact_sharpness_score(speeds, accelerations, key_idx, fps=fps)
        prior = self._physics_prior_score()
        weights = self.weights
        total_w = max(sum(weights.values()), _EPS)
        combined = (
            anticipation * weights["anticipation"]
            + impact * weights["impact_sharpness"]
            + prior * weights["physics_prior"]
        ) / total_w

        return BookLawFitnessReport(
            anticipation_score=anticipation,
            impact_sharpness=impact,
            physics_prior_score=prior,
            combined_score=_clamp01(combined),
            weights=weights,
            details={
                "target_joint": target_joint,
                "key_frame_index": int(key_idx),
                "max_acceleration": float(accel_mags[key_idx]),
                "max_speed": float(max(speeds) if speeds else 0.0),
                "knowledge_source": self.knowledge.source_path,
                "physics_params": self.physics_params.to_dict(),
            },
        )

    def _velocities(self, positions: Sequence[tuple[float, float]], dt: float) -> list[tuple[float, float]]:
        velocities: list[tuple[float, float]] = []
        for idx, pos in enumerate(positions):
            if idx == 0:
                velocities.append(_vec_scale(_vec_sub(positions[1], pos), 1.0 / dt))
            elif idx == len(positions) - 1:
                velocities.append(_vec_scale(_vec_sub(pos, positions[idx - 1]), 1.0 / dt))
            else:
                velocities.append(_vec_scale(_vec_sub(positions[idx + 1], positions[idx - 1]), 0.5 / dt))
        return velocities

    def _accelerations(self, velocities: Sequence[tuple[float, float]], dt: float) -> list[tuple[float, float]]:
        accelerations: list[tuple[float, float]] = []
        for idx, vel in enumerate(velocities):
            if idx == 0:
                accelerations.append(_vec_scale(_vec_sub(velocities[1], vel), 1.0 / dt))
            elif idx == len(velocities) - 1:
                accelerations.append(_vec_scale(_vec_sub(vel, velocities[idx - 1]), 1.0 / dt))
            else:
                accelerations.append(_vec_scale(_vec_sub(velocities[idx + 1], velocities[idx - 1]), 0.5 / dt))
        return accelerations

    def _anticipation_score(
        self,
        positions: Sequence[tuple[float, float]],
        velocities: Sequence[tuple[float, float]],
        accelerations: Sequence[tuple[float, float]],
        key_idx: int,
    ) -> float:
        if key_idx < 2:
            return 0.0
        burst_dir = _normalize(accelerations[key_idx] if _vec_len(accelerations[key_idx]) > _EPS else velocities[key_idx])
        pre_window_start = max(0, key_idx - 4)
        pre_displacement = _vec_sub(positions[key_idx - 1], positions[pre_window_start])
        pre_dir = _normalize(pre_displacement)
        reverse_alignment = max(0.0, -_vec_dot(pre_dir, burst_dir))
        displacement_mag = _vec_len(pre_displacement)
        burst_mag = _vec_len(accelerations[key_idx])
        magnitude_score = _clamp01(displacement_mag * 8.0) * _clamp01(burst_mag / 50.0)
        disney_bias = _clamp01(float(self.physics_params.anticipation_weight))
        return _clamp01((0.65 * reverse_alignment + 0.35 * magnitude_score) * max(1.0, disney_bias))

    def _impact_sharpness_score(
        self,
        speeds: Sequence[float],
        accelerations: Sequence[tuple[float, float]],
        key_idx: int,
        *,
        fps: float,
    ) -> float:
        if key_idx >= len(speeds) - 1:
            return 0.0
        peak_speed = max(speeds[max(0, key_idx - 2): key_idx + 1] or [0.0])
        if peak_speed <= _EPS:
            return 0.0
        settle_threshold = peak_speed * 0.35
        settle_frames = 0
        for idx in range(key_idx + 1, len(speeds)):
            settle_frames += 1
            if speeds[idx] <= settle_threshold:
                break
        target_frames = max(1.0, float(self.physics_params.impact_reward_weight) * 2.0)
        speed_drop = max(0.0, peak_speed - speeds[min(key_idx + 1, len(speeds) - 1)]) / peak_speed
        decel_time_score = _clamp01(target_frames / max(float(settle_frames), 1.0))
        acceleration_snap = _clamp01(_vec_len(accelerations[key_idx]) / max(fps, 1.0))
        return _clamp01(0.45 * speed_drop + 0.35 * decel_time_score + 0.20 * acceleration_snap)

    def _physics_prior_score(self) -> float:
        stretch_ok = 1.0 - abs(float(self.physics_params.squash_max_stretch) - 1.35) / 1.35
        velocity_gain_ok = 1.0 - abs(float(self.physics_params.squash_velocity_to_stretch) - 0.18) / 0.18
        return _clamp01(0.5 * stretch_ok + 0.5 * velocity_gain_ok)


def evaluate_knowledge_fitness(
    frames: Sequence[Any],
    *,
    fps: float = 60.0,
    target_joint: str = "root",
    knowledge_path: str | None = None,
) -> dict[str, Any]:
    """Convenience function for genetic dry-runs."""

    engine = KnowledgeDrivenFitnessEngine(knowledge_path=knowledge_path)
    return engine.evaluate(frames, fps=fps, target_joint=target_joint).to_dict()


__all__ = [
    "MotionSample",
    "BookLawFitnessReport",
    "KnowledgeDrivenFitnessEngine",
    "evaluate_knowledge_fitness",
    "extract_motion_samples",
]
