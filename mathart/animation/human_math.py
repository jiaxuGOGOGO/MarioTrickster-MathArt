"""SESSION-031 — Distilled human math stack for 2D-first animation evolution.

This module integrates four research directions into the existing MarioTrickster-
MathArt architecture without forcing a full mesh-heavy 3D pipeline:

1. **SMPL / SMPL-X inspired body parameterization**
   A low-dimensional shape latent is mapped into the project's existing 2D body
   proportions and skeleton geometry.

2. **VPoser-inspired pose prior**
   A lightweight anatomical projector and scoring function regularize poses so they
   stay inside biomechanical limits and plausible joint correlations.

3. **Dual Quaternions**
   A future-proof rigid-transform backend for pseudo-3D / 3D evolution, while still
   being usable today for mathematically safe rotation + translation blending.

4. **Motion Matching**
   A compact 2D pose-and-trajectory retrieval layer that searches a low-dimensional
   feature database instead of operating on full meshes.

Design philosophy
-----------------
- Stay compatible with the existing 2D raster renderer and skeleton.
- Distill ideas, not licensed assets or heavyweight external dependencies.
- Provide immediately useful scoring/projector utilities for Layer 3 evolution.
- Keep the interface future-ready for pseudo-3D or true 3D extensions.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np


# ── SMPL-like low-dimensional body parameterization ───────────────────────────


@dataclass
class SMPLShapeLatent:
    """Low-dimensional body-shape latent distilled for this project.

    All values are centered around 0.0 and typically expected in [-1, 1].
    Unlike full SMPL betas, these are directly interpretable and mapped into the
    existing 2D `CharacterStyle` and `Skeleton` proportion system.
    """

    stature: float = 0.0
    shoulder_width: float = 0.0
    hip_width: float = 0.0
    torso_height: float = 0.0
    arm_length: float = 0.0
    leg_length: float = 0.0
    head_scale: float = 0.0
    limb_thickness: float = 0.0

    def clipped(self) -> "SMPLShapeLatent":
        return SMPLShapeLatent(**{
            name: float(np.clip(value, -1.0, 1.0))
            for name, value in self.to_dict().items()
        })

    def to_dict(self) -> dict[str, float]:
        return {
            "stature": self.stature,
            "shoulder_width": self.shoulder_width,
            "hip_width": self.hip_width,
            "torso_height": self.torso_height,
            "arm_length": self.arm_length,
            "leg_length": self.leg_length,
            "head_scale": self.head_scale,
            "limb_thickness": self.limb_thickness,
        }

    def to_vector(self) -> np.ndarray:
        return np.array(list(self.to_dict().values()), dtype=np.float32)

    @classmethod
    def from_dict(cls, data: Mapping[str, float]) -> "SMPLShapeLatent":
        return cls(**{k: float(data.get(k, 0.0)) for k in cls.__dataclass_fields__})

    @classmethod
    def from_vector(cls, vec: Sequence[float]) -> "SMPLShapeLatent":
        keys = list(cls.__dataclass_fields__.keys())
        values = {k: float(vec[i]) if i < len(vec) else 0.0 for i, k in enumerate(keys)}
        return cls(**values)


class DistilledSMPLBodyModel:
    """SMPL-like shape bridge for the project's 2D phenotype.

    This is not a full mesh model. Instead, it converts a compact shape latent into:
    1. Character-style proportion modifiers
    2. A deformed 2D humanoid skeleton suitable for the current renderer
    """

    @staticmethod
    def shape_to_proportion_modifiers(
        shape: SMPLShapeLatent | Mapping[str, float],
    ) -> dict[str, float]:
        if not isinstance(shape, SMPLShapeLatent):
            shape = SMPLShapeLatent.from_dict(shape)
        s = shape.clipped()

        return {
            "head_radius_mod": float(0.055 * s.head_scale - 0.010 * s.stature),
            "torso_width_mod": float(0.030 * s.shoulder_width + 0.015 * s.hip_width),
            "torso_height_mod": float(0.040 * s.torso_height + 0.015 * s.stature),
            "arm_thickness_mod": float(0.015 * s.limb_thickness + 0.004 * s.shoulder_width),
            "leg_thickness_mod": float(0.018 * s.limb_thickness + 0.006 * s.hip_width),
            "hand_radius_mod": float(0.010 * s.head_scale + 0.008 * s.limb_thickness),
            "foot_width_mod": float(0.010 * s.leg_length + 0.010 * s.hip_width),
            "foot_height_mod": float(0.008 * s.leg_length + 0.006 * s.limb_thickness),
        }

    def apply_shape_to_skeleton(
        self,
        skeleton,
        shape: SMPLShapeLatent | Mapping[str, float],
    ):
        """Return a copy of the input skeleton with SMPL-like shape offsets applied."""
        if not isinstance(shape, SMPLShapeLatent):
            shape = SMPLShapeLatent.from_dict(shape)
        s = shape.clipped()
        skel = copy.deepcopy(skeleton)

        stature_scale = 1.0 + 0.10 * s.stature
        torso_scale = 1.0 + 0.15 * s.torso_height
        arm_scale = 1.0 + 0.16 * s.arm_length
        leg_scale = 1.0 + 0.18 * s.leg_length
        shoulder_scale = 1.0 + 0.20 * s.shoulder_width
        hip_scale = 1.0 + 0.16 * s.hip_width
        head_scale = 1.0 + 0.12 * s.head_scale

        def set_if(name: str, x: Optional[float] = None, y: Optional[float] = None):
            if name in skel.joints:
                if x is not None:
                    skel.joints[name].x = float(x)
                if y is not None:
                    skel.joints[name].y = float(y)

        # Vertical chain
        root_y = skel.joints["root"].y if "root" in skel.joints else 0.0
        hip_y = skel.joints["hip"].y if "hip" in skel.joints else 0.33
        spine_y = hip_y + (skel.joints["spine"].y - hip_y) * torso_scale * stature_scale
        chest_y = spine_y + (skel.joints["chest"].y - skel.joints["spine"].y) * torso_scale
        neck_y = chest_y + (skel.joints["neck"].y - skel.joints["chest"].y) * torso_scale
        head_y = neck_y + (skel.joints["head"].y - skel.joints["neck"].y) * head_scale

        set_if("spine", y=spine_y)
        set_if("chest", y=chest_y)
        set_if("neck", y=neck_y)
        set_if("head", y=head_y)

        # Shoulders and arms
        for side, sign in (("l", -1.0), ("r", 1.0)):
            shoulder = skel.joints[f"{side}_shoulder"]
            elbow = skel.joints[f"{side}_elbow"]
            hand = skel.joints[f"{side}_hand"]
            upper_dx = (elbow.x - shoulder.x) * arm_scale * shoulder_scale
            upper_dy = (elbow.y - shoulder.y) * arm_scale
            lower_dx = (hand.x - elbow.x) * arm_scale
            lower_dy = (hand.y - elbow.y) * arm_scale
            set_if(f"{side}_shoulder", x=sign * abs(shoulder.x) * shoulder_scale, y=chest_y + (shoulder.y - skel.joints["chest"].y) * torso_scale)
            shoulder = skel.joints[f"{side}_shoulder"]
            set_if(f"{side}_elbow", x=shoulder.x + upper_dx, y=shoulder.y + upper_dy)
            elbow = skel.joints[f"{side}_elbow"]
            set_if(f"{side}_hand", x=elbow.x + lower_dx, y=elbow.y + lower_dy)

        # Hips and legs
        for side, sign in (("l", -1.0), ("r", 1.0)):
            hip = skel.joints[f"{side}_hip"]
            knee = skel.joints[f"{side}_knee"]
            foot = skel.joints[f"{side}_foot"]
            upper_dx = (knee.x - hip.x) * hip_scale
            upper_dy = (knee.y - hip.y) * leg_scale * stature_scale
            lower_dx = (foot.x - knee.x) * hip_scale
            lower_dy = (foot.y - knee.y) * leg_scale * stature_scale
            set_if(f"{side}_hip", x=sign * abs(hip.x) * hip_scale, y=hip.y)
            hip = skel.joints[f"{side}_hip"]
            set_if(f"{side}_knee", x=hip.x + upper_dx, y=hip.y + upper_dy)
            knee = skel.joints[f"{side}_knee"]
            set_if(f"{side}_foot", x=knee.x + lower_dx, y=max(root_y, knee.y + lower_dy))

        # Update perceived body scale for downstream systems.
        skel.head_units = float(np.clip(
            skel.head_units * (1.0 + 0.08 * s.stature - 0.06 * s.head_scale),
            2.2,
            4.0,
        ))

        # Recompute bone lengths.
        for bone in skel.bones:
            a = skel.joints[bone.joint_a]
            b = skel.joints[bone.joint_b]
            bone.length = float(math.hypot(b.x - a.x, b.y - a.y))

        return skel


# ── VPoser-like pose prior ────────────────────────────────────────────────────


@dataclass
class PosePriorScore:
    total: float
    rom_score: float
    hinge_score: float
    chain_score: float
    symmetry_score: float

    def to_dict(self) -> dict[str, float]:
        return {
            "total": self.total,
            "rom_score": self.rom_score,
            "hinge_score": self.hinge_score,
            "chain_score": self.chain_score,
            "symmetry_score": self.symmetry_score,
        }


class VPoserDistilledPrior:
    """Anatomical feasibility projector inspired by VPoser.

    This is intentionally lightweight: it regularizes angle-space poses using
    the repository's existing ROM limits plus a small set of learned-style priors
    distilled into interpretable rules.
    """

    _HINGE_JOINT_SIGNS = {
        "l_elbow": (0.0, 1.0),
        "r_elbow": (0.0, 1.0),
        "l_knee": (-1.0, 0.0),
        "r_knee": (-1.0, 0.0),
    }

    _SYMMETRY_PAIRS = (
        ("l_shoulder", "r_shoulder"),
        ("l_elbow", "r_elbow"),
        ("l_hip", "r_hip"),
        ("l_knee", "r_knee"),
        ("l_foot", "r_foot"),
    )

    _CHAINS = (
        ("spine", "chest", "neck", "head"),
        ("l_hip", "l_knee", "l_foot"),
        ("r_hip", "r_knee", "r_foot"),
        ("l_shoulder", "l_elbow", "l_hand"),
        ("r_shoulder", "r_elbow", "r_hand"),
    )

    def __init__(
        self,
        rom_blend: float = 1.0,
        chain_blend: float = 0.30,
        symmetry_blend: float = 0.18,
    ):
        self.rom_blend = float(np.clip(rom_blend, 0.0, 1.0))
        self.chain_blend = float(np.clip(chain_blend, 0.0, 1.0))
        self.symmetry_blend = float(np.clip(symmetry_blend, 0.0, 1.0))

    @staticmethod
    def _default_skeleton():
        from .skeleton import Skeleton
        return Skeleton.create_humanoid(head_units=3.0)

    def project_pose(
        self,
        pose: Mapping[str, float],
        skeleton=None,
        blend: float = 1.0,
    ) -> dict[str, float]:
        """Project a raw pose into the repository's plausible anatomical manifold."""
        if skeleton is None:
            skeleton = self._default_skeleton()
        blend = float(np.clip(blend, 0.0, 1.0))
        projected = {k: float(v) for k, v in pose.items()}

        # 1) ROM clamp
        for name, joint in skeleton.joints.items():
            if name not in projected:
                continue
            clamped = float(np.clip(projected[name], joint.min_angle, joint.max_angle))
            projected[name] = (1.0 - blend * self.rom_blend) * projected[name] + (blend * self.rom_blend) * clamped

        # 2) Hinge-joint sign fixes
        for name, (lo_sign, hi_sign) in self._HINGE_JOINT_SIGNS.items():
            if name not in projected:
                continue
            if lo_sign >= 0.0:  # elbow-like: non-negative
                projected[name] = max(0.0, projected[name])
            if hi_sign <= 0.0:  # knee-like: non-positive
                projected[name] = min(0.0, projected[name])

        # 3) Chain smoothing — softly reduce impossible alternating bends
        chain_strength = blend * self.chain_blend
        for chain in self._CHAINS:
            available = [name for name in chain if name in projected]
            if len(available) < 2:
                continue
            for prev, cur in zip(available[:-1], available[1:]):
                projected[cur] = (
                    (1.0 - chain_strength) * projected[cur]
                    + chain_strength * 0.65 * projected[prev]
                )

        # 4) Symmetry regularization — important for biped 2D locomotion
        sym_strength = blend * self.symmetry_blend
        for left, right in self._SYMMETRY_PAIRS:
            if left not in projected or right not in projected:
                continue
            mean_mag = 0.5 * (abs(projected[left]) + abs(projected[right]))
            left_target = math.copysign(mean_mag, projected[left] if projected[left] != 0 else -1.0)
            right_target = math.copysign(mean_mag, projected[right] if projected[right] != 0 else 1.0)
            projected[left] = (1.0 - sym_strength) * projected[left] + sym_strength * left_target
            projected[right] = (1.0 - sym_strength) * projected[right] + sym_strength * right_target

        # Final ROM safety pass
        for name, joint in skeleton.joints.items():
            if name in projected:
                projected[name] = float(np.clip(projected[name], joint.min_angle, joint.max_angle))

        return projected

    def score_pose(self, pose: Mapping[str, float], skeleton=None) -> PosePriorScore:
        if skeleton is None:
            skeleton = self._default_skeleton()

        rom_error = 0.0
        rom_count = 0
        for name, joint in skeleton.joints.items():
            if name not in pose:
                continue
            value = float(pose[name])
            below = max(0.0, joint.min_angle - value)
            above = max(0.0, value - joint.max_angle)
            joint_range = max(joint.max_angle - joint.min_angle, 1e-6)
            rom_error += (below + above) / joint_range
            rom_count += 1
        rom_score = math.exp(-4.0 * rom_error / max(rom_count, 1))

        hinge_error = 0.0
        for name, (lo_sign, hi_sign) in self._HINGE_JOINT_SIGNS.items():
            if name not in pose:
                continue
            v = float(pose[name])
            if lo_sign >= 0.0 and v < 0.0:
                hinge_error += abs(v)
            if hi_sign <= 0.0 and v > 0.0:
                hinge_error += abs(v)
        hinge_score = math.exp(-2.5 * hinge_error)

        chain_error = 0.0
        chain_pairs = 0
        for chain in self._CHAINS:
            vals = [float(pose[n]) for n in chain if n in pose]
            for a, b in zip(vals[:-1], vals[1:]):
                chain_error += abs(b - 0.65 * a)
                chain_pairs += 1
        chain_score = math.exp(-0.75 * chain_error / max(chain_pairs, 1))

        sym_error = 0.0
        sym_count = 0
        for left, right in self._SYMMETRY_PAIRS:
            if left in pose and right in pose:
                sym_error += abs(abs(float(pose[left])) - abs(float(pose[right])))
                sym_count += 1
        symmetry_score = math.exp(-0.9 * sym_error / max(sym_count, 1))

        total = float(np.clip(
            0.45 * rom_score + 0.20 * hinge_score + 0.20 * chain_score + 0.15 * symmetry_score,
            0.0,
            1.0,
        ))
        return PosePriorScore(total, rom_score, hinge_score, chain_score, symmetry_score)

    def project_sequence(
        self,
        sequence: Sequence[Mapping[str, float]],
        skeleton=None,
    ) -> list[dict[str, float]]:
        if skeleton is None:
            skeleton = self._default_skeleton()
        return [self.project_pose(pose, skeleton=skeleton) for pose in sequence]


# ── Dual Quaternion backend ───────────────────────────────────────────────────


def _quat_normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    norm = np.linalg.norm(q)
    if norm < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return q / norm


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], dtype=np.float64)


def _quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=np.float64)


def _quat_from_axis_angle(axis: Sequence[float], angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    axis_norm = np.linalg.norm(axis)
    if axis_norm < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    axis = axis / axis_norm
    half = 0.5 * float(angle)
    s = math.sin(half)
    return _quat_normalize(np.array([math.cos(half), axis[0] * s, axis[1] * s, axis[2] * s], dtype=np.float64))


@dataclass
class DualQuaternion:
    """Rigid transform represented as a unit dual quaternion.

    The implementation is intentionally compact but mathematically correct enough for
    future pseudo-3D / 3D skeletal blending experiments.
    """

    real: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64))
    dual: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float64))

    @classmethod
    def identity(cls) -> "DualQuaternion":
        return cls()

    @classmethod
    def from_rotation_translation(
        cls,
        rotation: Sequence[float],
        translation: Sequence[float],
    ) -> "DualQuaternion":
        r = _quat_normalize(np.asarray(rotation, dtype=np.float64))
        t = np.asarray(translation, dtype=np.float64)
        if t.shape != (3,):
            raise ValueError("translation must be a 3-vector")
        t_quat = np.array([0.0, t[0], t[1], t[2]], dtype=np.float64)
        d = 0.5 * _quat_mul(t_quat, r)
        return cls(r, d).normalized()

    @classmethod
    def from_z_rotation(
        cls,
        angle: float,
        translation_xy: Sequence[float] = (0.0, 0.0),
        z: float = 0.0,
    ) -> "DualQuaternion":
        r = _quat_from_axis_angle((0.0, 0.0, 1.0), angle)
        tx, ty = translation_xy
        return cls.from_rotation_translation(r, (float(tx), float(ty), float(z)))

    def normalized(self) -> "DualQuaternion":
        r = _quat_normalize(self.real)
        d = self.dual / max(np.linalg.norm(self.real), 1e-12)
        return DualQuaternion(r, d)

    def translation(self) -> np.ndarray:
        t_quat = 2.0 * _quat_mul(self.dual, _quat_conj(self.real))
        return t_quat[1:4]

    def transform_point(self, point: Sequence[float]) -> np.ndarray:
        p = np.asarray(point, dtype=np.float64)
        if p.shape != (3,):
            raise ValueError("point must be a 3-vector")
        p_quat = np.array([0.0, p[0], p[1], p[2]], dtype=np.float64)
        rotated = _quat_mul(_quat_mul(self.real, p_quat), _quat_conj(self.real))[1:4]
        return rotated + self.translation()

    @staticmethod
    def blend(weighted_transforms: Sequence[tuple[float, "DualQuaternion"]]) -> "DualQuaternion":
        if not weighted_transforms:
            return DualQuaternion.identity()
        accum_real = np.zeros(4, dtype=np.float64)
        accum_dual = np.zeros(4, dtype=np.float64)
        ref_real = weighted_transforms[0][1].real
        for weight, dq in weighted_transforms:
            sign = 1.0 if float(np.dot(ref_real, dq.real)) >= 0.0 else -1.0
            accum_real += float(weight) * sign * dq.real
            accum_dual += float(weight) * sign * dq.dual
        return DualQuaternion(accum_real, accum_dual).normalized()


# ── Motion Matching for 2D skeletons ──────────────────────────────────────────


@dataclass
class MotionFeatureSchema2D:
    pose_joints: tuple[str, ...] = (
        "hip", "spine", "chest", "l_hip", "r_hip", "l_knee", "r_knee", "l_foot", "r_foot",
    )
    velocity_joints: tuple[str, ...] = ("l_foot", "r_foot", "hip")
    trajectory_dims: int = 4
    pose_weight: float = 1.0
    velocity_weight: float = 0.7
    trajectory_weight: float = 1.2


@dataclass
class MotionMatchResult:
    clip_name: str
    frame_index: int
    pose: dict[str, float]
    cost: float
    similarity: float
    feature_vector: np.ndarray

    def to_dict(self) -> dict[str, object]:
        return {
            "clip_name": self.clip_name,
            "frame_index": self.frame_index,
            "pose": dict(self.pose),
            "cost": float(self.cost),
            "similarity": float(self.similarity),
            "feature_dim": int(self.feature_vector.shape[0]),
        }


class MotionMatcher2D:
    """Low-dimensional 2D motion matching backend.

    This is the 2D-compatible distillation of AAA motion matching:
    a feature matrix over compact pose/velocity/trajectory descriptors and a
    weighted nearest-neighbor search at runtime.
    """

    def __init__(self, schema: Optional[MotionFeatureSchema2D] = None):
        self.schema = schema or MotionFeatureSchema2D()
        self.entries: list[dict[str, object]] = []
        self.feature_matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    def _trajectory_vector(self, desired_trajectory: Optional[Sequence[float]]) -> np.ndarray:
        if desired_trajectory is None:
            return np.zeros(self.schema.trajectory_dims, dtype=np.float32)
        vec = np.asarray(desired_trajectory, dtype=np.float32).flatten()
        out = np.zeros(self.schema.trajectory_dims, dtype=np.float32)
        out[: min(len(vec), self.schema.trajectory_dims)] = vec[: self.schema.trajectory_dims]
        return out

    def vectorize_pose(
        self,
        pose: Mapping[str, float],
        prev_pose: Optional[Mapping[str, float]] = None,
        desired_trajectory: Optional[Sequence[float]] = None,
    ) -> np.ndarray:
        pose_feats = [float(pose.get(j, 0.0)) for j in self.schema.pose_joints]
        vel_feats = []
        for joint in self.schema.velocity_joints:
            current = float(pose.get(joint, 0.0))
            previous = float(prev_pose.get(joint, current)) if prev_pose else current
            vel_feats.append(current - previous)
        traj = self._trajectory_vector(desired_trajectory)
        feature = np.concatenate([
            self.schema.pose_weight * np.asarray(pose_feats, dtype=np.float32),
            self.schema.velocity_weight * np.asarray(vel_feats, dtype=np.float32),
            self.schema.trajectory_weight * traj.astype(np.float32),
        ])
        return feature.astype(np.float32)

    def build_from_clips(
        self,
        clips: Mapping[str, Sequence[Mapping[str, float]]],
        trajectory_hints: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> None:
        entries: list[dict[str, object]] = []
        features: list[np.ndarray] = []
        trajectory_hints = trajectory_hints or {}

        for clip_name, sequence in clips.items():
            prev_pose = None
            traj = trajectory_hints.get(clip_name)
            for frame_index, pose in enumerate(sequence):
                fv = self.vectorize_pose(pose, prev_pose=prev_pose, desired_trajectory=traj)
                entries.append({
                    "clip_name": clip_name,
                    "frame_index": frame_index,
                    "pose": dict(pose),
                    "feature_vector": fv,
                })
                features.append(fv)
                prev_pose = pose

        self.entries = entries
        self.feature_matrix = np.stack(features, axis=0) if features else np.zeros((0, 0), dtype=np.float32)

    def query(
        self,
        pose: Mapping[str, float],
        prev_pose: Optional[Mapping[str, float]] = None,
        desired_trajectory: Optional[Sequence[float]] = None,
    ) -> MotionMatchResult:
        if self.feature_matrix.size == 0 or not self.entries:
            raise RuntimeError("Motion matcher database is empty. Call build_from_clips() first.")
        q = self.vectorize_pose(pose, prev_pose=prev_pose, desired_trajectory=desired_trajectory)
        residual = self.feature_matrix - q[None, :]
        costs = np.sum(residual * residual, axis=1)
        best_idx = int(np.argmin(costs))
        best = self.entries[best_idx]
        cost = float(costs[best_idx])
        similarity = float(math.exp(-0.5 * cost / max(q.shape[0], 1)))
        return MotionMatchResult(
            clip_name=str(best["clip_name"]),
            frame_index=int(best["frame_index"]),
            pose=dict(best["pose"]),
            cost=cost,
            similarity=similarity,
            feature_vector=np.asarray(best["feature_vector"], dtype=np.float32),
        )


# ── Unified runtime bridge ────────────────────────────────────────────────────


class DistilledHumanMathRuntime:
    """Unified runtime bridge that fuses shape, pose prior and motion matching.

    This object gives the current repository a single, self-contained entry point
    for using the new math stack in Layer 1/2/3 or custom animation experiments.
    """

    def __init__(
        self,
        skeleton=None,
        motion_clips: Optional[Mapping[str, Sequence[Mapping[str, float]]]] = None,
        trajectory_hints: Optional[Mapping[str, Sequence[float]]] = None,
    ):
        from .skeleton import Skeleton

        self.skeleton = skeleton or Skeleton.create_humanoid(head_units=3.0)
        self.body_model = DistilledSMPLBodyModel()
        self.pose_prior = VPoserDistilledPrior()
        self.motion_matcher = MotionMatcher2D()
        if motion_clips:
            self.motion_matcher.build_from_clips(motion_clips, trajectory_hints=trajectory_hints)

    def apply_shape(self, shape: SMPLShapeLatent | Mapping[str, float]):
        return self.body_model.apply_shape_to_skeleton(self.skeleton, shape)

    def process_pose(
        self,
        pose: Mapping[str, float],
        prev_pose: Optional[Mapping[str, float]] = None,
        desired_trajectory: Optional[Sequence[float]] = None,
    ) -> dict[str, object]:
        projected = self.pose_prior.project_pose(pose, skeleton=self.skeleton)
        score = self.pose_prior.score_pose(projected, skeleton=self.skeleton)
        result = {
            "projected_pose": projected,
            "pose_prior_score": score.total,
            "pose_prior_breakdown": score.to_dict(),
        }
        if self.motion_matcher.entries:
            match = self.motion_matcher.query(
                projected,
                prev_pose=prev_pose,
                desired_trajectory=desired_trajectory,
            )
            result["motion_match"] = match.to_dict()
        return result


__all__ = [
    "SMPLShapeLatent",
    "DistilledSMPLBodyModel",
    "PosePriorScore",
    "VPoserDistilledPrior",
    "DualQuaternion",
    "MotionFeatureSchema2D",
    "MotionMatchResult",
    "MotionMatcher2D",
    "DistilledHumanMathRuntime",
]
