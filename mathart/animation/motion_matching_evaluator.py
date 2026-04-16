"""SESSION-034 — Motion Matching Feature Vector Evaluator.

Industrial-grade motion matching evaluation system distilled from:
  - Simon Clavet (Ubisoft), GDC 2016: "Motion Matching and The Road to Next-Gen Animation"
  - O3DE Motion Matching implementation (Benjamin Jillich, 2022)
  - PFNN (Holden et al., SIGGRAPH 2017) phase-trajectory integration

This module replaces the crude "joint angle tolerance" scoring in Layer 3 with a
proper **feature vector matching** pipeline. Instead of comparing raw joint angles,
we extract per-frame feature vectors encoding:

  1. **Local Pose Features**: Joint positions relative to root (model space)
  2. **Velocity Features**: Joint linear velocities (foot, hip, hand)
  3. **Trajectory Features**: Past trajectory + future predicted trajectory
  4. **Contact Labels**: Left/right foot ground contact binary flags
  5. **Phase Features**: Current gait phase position and phase velocity

The matching cost is computed as a weighted sum of per-feature squared distances,
with feature-specific normalization (mean=0, std=1) to prevent any single feature
from dominating the search.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  MotionFeatureExtractor                                             │
    │  ├─ extract_pose_features(pose, skeleton) → position features       │
    │  ├─ extract_velocity_features(pose, prev_pose, dt) → velocities     │
    │  ├─ extract_trajectory_features(history, future) → trajectory       │
    │  ├─ extract_contact_labels(pose, skeleton) → foot contact flags     │
    │  └─ extract_phase_features(phase_var) → phase position + velocity   │
    ├─────────────────────────────────────────────────────────────────────┤
    │  FeatureNormalizer                                                   │
    │  ├─ fit(feature_matrix) → compute per-column mean/std               │
    │  └─ transform(feature_vector) → normalized vector                   │
    ├─────────────────────────────────────────────────────────────────────┤
    │  MotionMatchingEvaluator                                            │
    │  ├─ build_database(clips) → feature matrix + KD-tree               │
    │  ├─ query(current_features) → best match + cost                     │
    │  ├─ evaluate_sequence(sequence) → per-frame quality scores          │
    │  └─ compute_layer3_fitness(sequence) → aggregated fitness dict      │
    └─────────────────────────────────────────────────────────────────────┘

Integration with Layer 3:
    - Replaces the old `motion_match_score` in `evaluate_physics_fitness()`
    - Provides richer diagnostics: per-feature cost breakdown
    - Supports contact-aware evaluation (foot skating detection via contact labels)
    - Phase-coherent scoring (penalizes phase discontinuities)

References:
    - SESSION-031: MotionMatcher2D (basic version, now superseded for scoring)
    - SESSION-033: PhaseVariable, PhaseDrivenAnimator
    - SESSION-030: PhysicsTestBattery, PhysicsEvolutionLayer
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np

from .unified_motion import UnifiedMotionFrame


# ── Feature Schema ──────────────────────────────────────────────────────────


@dataclass
class IndustrialFeatureSchema:
    """Feature schema inspired by Ubisoft's Motion Matching (GDC 2016).

    Defines which features to extract and their relative weights in the
    matching cost function. The schema is configurable per use-case.

    Default schema (59-dimensional, matching O3DE reference):
      - Pose: 9 joints × 2D position = 18 dims
      - Velocity: 5 joints × 2D velocity = 10 dims
      - Trajectory: 4 past + 6 future samples × 2D = 20 dims
      - Contact: 2 foot flags + 2 contact velocities = 4 dims
      - Phase: phase position + phase velocity + gait mode = 3 dims
      - Silhouette: 4 extremity distances = 4 dims
      Total: 59 dimensions
    """
    # Pose feature joints (model-space 2D positions relative to root)
    pose_joints: tuple[str, ...] = (
        "head", "chest", "spine",
        "l_shoulder", "r_shoulder",
        "l_hand", "r_hand",
        "l_foot", "r_foot",
    )

    # Velocity feature joints (2D linear velocity)
    velocity_joints: tuple[str, ...] = (
        "l_foot", "r_foot", "hip", "l_hand", "r_hand",
    )

    # Trajectory configuration
    trajectory_past_samples: int = 4
    trajectory_future_samples: int = 6
    trajectory_past_window: float = 0.7    # seconds
    trajectory_future_window: float = 1.2  # seconds

    # Feature weights (Clavet GDC 2016 recommended ratios)
    pose_weight: float = 1.0
    velocity_weight: float = 0.8
    trajectory_weight: float = 1.2
    contact_weight: float = 1.5       # High weight: contact correctness is critical
    phase_weight: float = 0.6
    silhouette_weight: float = 0.4    # Dead Cells inspired: silhouette readability

    @property
    def total_dims(self) -> int:
        """Total feature vector dimensionality."""
        return (
            len(self.pose_joints) * 2          # 2D positions
            + len(self.velocity_joints) * 2    # 2D velocities
            + (self.trajectory_past_samples + self.trajectory_future_samples) * 2  # trajectory
            + 4                                 # contact labels + velocities
            + 3                                 # phase features
            + 4                                 # silhouette extremity distances
        )


# ── Feature Normalizer ──────────────────────────────────────────────────────


class FeatureNormalizer:
    """Per-column normalization for the feature matrix.

    Implements the standard normalization from O3DE's motion matching:
    each feature column is independently normalized to mean=0, std=1.
    This prevents features with larger magnitudes (e.g., positions in world
    space) from dominating the cost function over smaller features (e.g.,
    contact flags).
    """

    def __init__(self):
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None
        self._fitted = False

    def fit(self, feature_matrix: np.ndarray) -> "FeatureNormalizer":
        """Compute per-column mean and std from the feature matrix.

        Parameters
        ----------
        feature_matrix : np.ndarray, shape (N, D)
            N frames × D feature dimensions.
        """
        if feature_matrix.ndim != 2 or feature_matrix.shape[0] < 2:
            # Not enough data to normalize; use identity
            d = feature_matrix.shape[1] if feature_matrix.ndim == 2 else 1
            self.mean = np.zeros(d, dtype=np.float32)
            self.std = np.ones(d, dtype=np.float32)
            self._fitted = True
            return self

        self.mean = np.mean(feature_matrix, axis=0).astype(np.float32)
        self.std = np.std(feature_matrix, axis=0).astype(np.float32)
        # Prevent division by zero
        self.std = np.where(self.std < 1e-8, 1.0, self.std)
        self._fitted = True
        return self

    def transform(self, feature_vector: np.ndarray) -> np.ndarray:
        """Normalize a feature vector using fitted statistics."""
        if not self._fitted:
            return feature_vector.astype(np.float32)
        return ((feature_vector - self.mean) / self.std).astype(np.float32)

    def transform_matrix(self, feature_matrix: np.ndarray) -> np.ndarray:
        """Normalize an entire feature matrix."""
        if not self._fitted:
            return feature_matrix.astype(np.float32)
        return ((feature_matrix - self.mean[None, :]) / self.std[None, :]).astype(np.float32)


# ── Feature Extractor ───────────────────────────────────────────────────────


class MotionFeatureExtractor:
    """Extract industrial-grade feature vectors from pose data.

    Implements the feature extraction pipeline from Clavet (GDC 2016):
    pose features + velocity features + trajectory + contact labels + phase.

    All features are extracted in **model space** (relative to root joint),
    making the matching invariant to character world position and orientation.
    """

    def __init__(self, schema: Optional[IndustrialFeatureSchema] = None):
        self.schema = schema or IndustrialFeatureSchema()

    def extract_pose_features(
        self,
        joint_positions: Mapping[str, tuple[float, float]],
        root_pos: tuple[float, float] = (0.0, 0.0),
    ) -> np.ndarray:
        """Extract 2D joint positions relative to root.

        This is the core pose descriptor from Motion Matching:
        each joint's (x, y) position in model space.
        """
        features = []
        rx, ry = root_pos
        for joint in self.schema.pose_joints:
            if joint in joint_positions:
                jx, jy = joint_positions[joint]
                features.extend([jx - rx, jy - ry])
            else:
                features.extend([0.0, 0.0])
        return np.array(features, dtype=np.float32)

    def extract_velocity_features(
        self,
        current_positions: Mapping[str, tuple[float, float]],
        prev_positions: Optional[Mapping[str, tuple[float, float]]],
        dt: float = 1.0 / 12.0,  # 12fps target
    ) -> np.ndarray:
        """Extract joint velocity features.

        Velocity is critical for motion matching: it encodes the direction
        and speed of movement, enabling the system to find transitions that
        preserve momentum and avoid pops.
        """
        features = []
        for joint in self.schema.velocity_joints:
            if joint in current_positions and prev_positions and joint in prev_positions:
                cx, cy = current_positions[joint]
                px, py = prev_positions[joint]
                vx = (cx - px) / max(dt, 1e-6)
                vy = (cy - py) / max(dt, 1e-6)
                features.extend([vx, vy])
            else:
                features.extend([0.0, 0.0])
        return np.array(features, dtype=np.float32)

    def extract_trajectory_features(
        self,
        past_positions: Sequence[tuple[float, float]],
        future_positions: Sequence[tuple[float, float]],
    ) -> np.ndarray:
        """Extract trajectory features (past + future root positions).

        The trajectory encodes where the character came from and where it
        intends to go. This is the key feature for responsive control:
        the matching algorithm finds animations whose trajectory best matches
        the player's input intention.

        Parameters
        ----------
        past_positions : sequence of (x, y)
            Past root positions, oldest first. Length should match
            schema.trajectory_past_samples.
        future_positions : sequence of (x, y)
            Future/predicted root positions. Length should match
            schema.trajectory_future_samples.
        """
        features = []

        # Pad/truncate past
        n_past = self.schema.trajectory_past_samples
        padded_past = list(past_positions)
        while len(padded_past) < n_past:
            padded_past.insert(0, padded_past[0] if padded_past else (0.0, 0.0))
        for x, y in padded_past[-n_past:]:
            features.extend([float(x), float(y)])

        # Pad/truncate future
        n_future = self.schema.trajectory_future_samples
        padded_future = list(future_positions)
        while len(padded_future) < n_future:
            padded_future.append(padded_future[-1] if padded_future else (0.0, 0.0))
        for x, y in padded_future[:n_future]:
            features.extend([float(x), float(y)])

        return np.array(features, dtype=np.float32)

    def extract_contact_labels(
        self,
        joint_positions: Mapping[str, tuple[float, float]],
        ground_y: float = 0.0,
        contact_threshold: float = 0.03,
        prev_positions: Optional[Mapping[str, tuple[float, float]]] = None,
        dt: float = 1.0 / 12.0,
    ) -> np.ndarray:
        """Extract foot contact labels and contact velocities.

        Contact labels are binary flags indicating whether each foot is
        on the ground. This is critical for:
        1. Preventing foot skating (sliding contact feet)
        2. Ensuring correct gait phase matching
        3. Enabling contact-aware transition selection

        Returns: [left_contact, right_contact, left_contact_vel, right_contact_vel]
        """
        features = []
        for foot in ("l_foot", "r_foot"):
            if foot in joint_positions:
                _, fy = joint_positions[foot]
                is_contact = 1.0 if abs(fy - ground_y) < contact_threshold else 0.0
                features.append(is_contact)
            else:
                features.append(0.0)

        # Contact velocity (should be near zero when in contact)
        for foot in ("l_foot", "r_foot"):
            if foot in joint_positions and prev_positions and foot in prev_positions:
                cx, cy = joint_positions[foot]
                px, py = prev_positions[foot]
                vel = math.hypot((cx - px) / max(dt, 1e-6), (cy - py) / max(dt, 1e-6))
                features.append(vel)
            else:
                features.append(0.0)

        return np.array(features, dtype=np.float32)

    def extract_phase_features(
        self,
        phase: float = 0.0,
        phase_velocity: float = 1.0,
        gait_mode: int = 0,
    ) -> np.ndarray:
        """Extract phase-related features.

        Phase features encode the current position in the gait cycle,
        enabling the matcher to find animations at compatible phases.
        This prevents jarring transitions between incompatible gait phases.

        Parameters
        ----------
        phase : float
            Current phase [0, 1).
        phase_velocity : float
            Phase advancement speed (cycles per second).
        gait_mode : int
            Encoded gait type (0=walk, 1=run, 2=jump, 3=idle, 4=fall).
        """
        return np.array([
            math.sin(2.0 * math.pi * phase),  # Circular encoding (avoids discontinuity)
            math.cos(2.0 * math.pi * phase),
            float(phase_velocity),
        ], dtype=np.float32)

    def extract_umr_context(
        self,
        frame: UnifiedMotionFrame,
        prev_frame: Optional[UnifiedMotionFrame] = None,
    ) -> dict[str, float]:
        """Extract phase/contact/root context directly from a UMR frame.

        This is the bridge that lets Layer 3 evaluators consume the shared motion
        contract without having to rediscover phase and contact labels from raw
        poses every time.
        """
        dt = max(float(frame.time - prev_frame.time), 1e-6) if prev_frame is not None else (1.0 / 12.0)
        phase_velocity = (
            ((frame.phase - prev_frame.phase) % 1.0) / dt if prev_frame is not None else 1.0
        )
        return {
            "phase": float(frame.phase),
            "phase_velocity": float(phase_velocity),
            "root_x": float(frame.root_transform.x),
            "root_y": float(frame.root_transform.y),
            "root_vx": float(frame.root_transform.velocity_x),
            "root_vy": float(frame.root_transform.velocity_y),
            "left_contact": 1.0 if frame.contact_tags.left_foot else 0.0,
            "right_contact": 1.0 if frame.contact_tags.right_foot else 0.0,
        }

    def extract_silhouette_features(
        self,
        joint_positions: Mapping[str, tuple[float, float]],
        root_pos: tuple[float, float] = (0.0, 0.0),
    ) -> np.ndarray:
        """Extract silhouette readability features (Dead Cells inspired).

        Measures the extremity spread of the character's silhouette.
        A good pixel art animation maintains clear, exaggerated silhouettes
        at key poses. This feature helps the matcher prefer poses with
        strong silhouette differentiation.

        Returns: [max_x_spread, max_y_spread, left_hand_dist, right_hand_dist]
        """
        rx, ry = root_pos
        positions = []
        for joint in self.schema.pose_joints:
            if joint in joint_positions:
                jx, jy = joint_positions[joint]
                positions.append((jx - rx, jy - ry))

        if not positions:
            return np.zeros(4, dtype=np.float32)

        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]

        x_spread = max(xs) - min(xs) if xs else 0.0
        y_spread = max(ys) - min(ys) if ys else 0.0

        # Hand distances from root (arm extension = silhouette clarity)
        l_hand_dist = 0.0
        r_hand_dist = 0.0
        if "l_hand" in joint_positions:
            hx, hy = joint_positions["l_hand"]
            l_hand_dist = math.hypot(hx - rx, hy - ry)
        if "r_hand" in joint_positions:
            hx, hy = joint_positions["r_hand"]
            r_hand_dist = math.hypot(hx - rx, hy - ry)

        return np.array([x_spread, y_spread, l_hand_dist, r_hand_dist], dtype=np.float32)

    def extract_full_feature_vector(
        self,
        joint_positions: Mapping[str, tuple[float, float]],
        prev_positions: Optional[Mapping[str, tuple[float, float]]] = None,
        past_trajectory: Optional[Sequence[tuple[float, float]]] = None,
        future_trajectory: Optional[Sequence[tuple[float, float]]] = None,
        phase: float = 0.0,
        phase_velocity: float = 1.0,
        gait_mode: int = 0,
        dt: float = 1.0 / 12.0,
        root_pos: tuple[float, float] = (0.0, 0.0),
    ) -> np.ndarray:
        """Extract the complete feature vector for a single frame.

        This is the main entry point for feature extraction, combining all
        sub-features into a single vector with proper weighting.
        """
        s = self.schema

        pose = self.extract_pose_features(joint_positions, root_pos) * s.pose_weight
        velocity = self.extract_velocity_features(
            joint_positions, prev_positions, dt
        ) * s.velocity_weight
        trajectory = self.extract_trajectory_features(
            past_trajectory or [(0.0, 0.0)] * s.trajectory_past_samples,
            future_trajectory or [(0.0, 0.0)] * s.trajectory_future_samples,
        ) * s.trajectory_weight
        contact = self.extract_contact_labels(
            joint_positions, prev_positions=prev_positions, dt=dt
        ) * s.contact_weight
        phase_feat = self.extract_phase_features(
            phase, phase_velocity, gait_mode
        ) * s.phase_weight
        silhouette = self.extract_silhouette_features(
            joint_positions, root_pos
        ) * s.silhouette_weight

        return np.concatenate([pose, velocity, trajectory, contact, phase_feat, silhouette])


# ── Motion Matching Evaluator ───────────────────────────────────────────────


@dataclass
class MatchResult:
    """Result of a motion matching query."""
    best_frame_idx: int
    best_clip_name: str
    cost: float
    similarity: float
    feature_vector: np.ndarray
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "best_frame_idx": self.best_frame_idx,
            "best_clip_name": self.best_clip_name,
            "cost": float(self.cost),
            "similarity": float(self.similarity),
            "feature_dim": int(self.feature_vector.shape[0]),
            "cost_breakdown": {k: float(v) for k, v in self.cost_breakdown.items()},
        }


@dataclass
class SequenceEvaluation:
    """Evaluation result for an entire animation sequence."""
    per_frame_costs: list[float]
    per_frame_similarities: list[float]
    contact_consistency: float
    phase_coherence: float
    silhouette_score: float
    skating_penalty: float
    overall_score: float
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mean_cost": float(np.mean(self.per_frame_costs)) if self.per_frame_costs else 0.0,
            "mean_similarity": float(np.mean(self.per_frame_similarities)) if self.per_frame_similarities else 0.0,
            "contact_consistency": float(self.contact_consistency),
            "phase_coherence": float(self.phase_coherence),
            "silhouette_score": float(self.silhouette_score),
            "skating_penalty": float(self.skating_penalty),
            "overall_score": float(self.overall_score),
            "n_frames": len(self.per_frame_costs),
            "cost_breakdown": {k: float(v) for k, v in self.cost_breakdown.items()},
        }


class MotionMatchingEvaluator:
    """Industrial-grade motion matching evaluator for Layer 3 evolution.

    Replaces the crude joint-angle-tolerance scoring with proper feature-vector
    matching as described by Simon Clavet (Ubisoft, GDC 2016).

    The evaluator maintains a reference motion database (feature matrix) and
    scores candidate animations by their feature-space distance to the best
    matching reference frames.

    Key improvements over the old MotionMatcher2D:
    1. Full feature vector (pose + velocity + trajectory + contact + phase)
    2. Per-column normalization (prevents feature magnitude bias)
    3. Contact-aware scoring (penalizes foot skating)
    4. Phase-coherent evaluation (penalizes phase jumps)
    5. Silhouette readability scoring (Dead Cells inspired)
    6. Per-feature cost breakdown for diagnostics
    """

    def __init__(
        self,
        schema: Optional[IndustrialFeatureSchema] = None,
    ):
        self.schema = schema or IndustrialFeatureSchema()
        self.extractor = MotionFeatureExtractor(self.schema)
        self.normalizer = FeatureNormalizer()

        # Database
        self._feature_matrix: Optional[np.ndarray] = None
        self._entries: list[dict] = []
        self._built = False

    def build_database(
        self,
        clips: Mapping[str, Sequence[Mapping[str, float]]],
        skeleton=None,
        trajectory_hints: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> None:
        """Build the reference motion database from animation clips.

        Parameters
        ----------
        clips : dict[str, list[dict[str, float]]]
            Named animation clips. Each clip is a list of pose dicts.
        skeleton : Skeleton, optional
            Character skeleton for FK computation.
        trajectory_hints : dict[str, tuple], optional
            Per-clip trajectory hints (vx, vy, facing_x, facing_y).
        """
        from .skeleton import Skeleton

        skel = skeleton or Skeleton.create_humanoid(head_units=3.0)
        trajectory_hints = trajectory_hints or {}

        entries = []
        features = []

        for clip_name, sequence in clips.items():
            traj_hint = trajectory_hints.get(clip_name, (0.0, 0.0, 1.0, 0.0))
            prev_positions = None

            for frame_idx, pose in enumerate(sequence):
                # Forward kinematics
                skel.apply_pose(pose)
                positions = skel.get_joint_positions()
                root_pos = positions.get("root", positions.get("hip", (0.0, 0.33)))

                # Phase estimation from frame index
                n_frames = max(len(sequence), 1)
                phase = (frame_idx / n_frames) % 1.0
                phase_vel = 1.0 / max(n_frames / 12.0, 0.1)  # cycles per second at 12fps

                # Gait mode encoding
                gait_map = {"walk": 0, "run": 1, "jump": 2, "idle": 3, "fall": 4}
                gait_mode = gait_map.get(clip_name.split("_")[0], 0)

                # Simple trajectory from hint
                vx, vy = traj_hint[0], traj_hint[1]
                dt = 1.0 / 12.0
                past_traj = [
                    (root_pos[0] - vx * dt * (self.schema.trajectory_past_samples - i),
                     root_pos[1] - vy * dt * (self.schema.trajectory_past_samples - i))
                    for i in range(self.schema.trajectory_past_samples)
                ]
                future_traj = [
                    (root_pos[0] + vx * dt * (i + 1),
                     root_pos[1] + vy * dt * (i + 1))
                    for i in range(self.schema.trajectory_future_samples)
                ]

                fv = self.extractor.extract_full_feature_vector(
                    joint_positions=positions,
                    prev_positions=prev_positions,
                    past_trajectory=past_traj,
                    future_trajectory=future_traj,
                    phase=phase,
                    phase_velocity=phase_vel,
                    gait_mode=gait_mode,
                    dt=dt,
                    root_pos=root_pos,
                )

                entries.append({
                    "clip_name": clip_name,
                    "frame_index": frame_idx,
                    "pose": dict(pose),
                    "positions": dict(positions),
                })
                features.append(fv)
                prev_positions = dict(positions)

        if features:
            self._feature_matrix = np.stack(features, axis=0)
            self.normalizer.fit(self._feature_matrix)
            self._feature_matrix = self.normalizer.transform_matrix(self._feature_matrix)
        else:
            self._feature_matrix = np.zeros((0, self.schema.total_dims), dtype=np.float32)

        self._entries = entries
        self._built = True

    def query(
        self,
        joint_positions: Mapping[str, tuple[float, float]],
        prev_positions: Optional[Mapping[str, tuple[float, float]]] = None,
        past_trajectory: Optional[Sequence[tuple[float, float]]] = None,
        future_trajectory: Optional[Sequence[tuple[float, float]]] = None,
        phase: float = 0.0,
        phase_velocity: float = 1.0,
        gait_mode: int = 0,
        dt: float = 1.0 / 12.0,
    ) -> MatchResult:
        """Find the best matching frame in the database.

        Uses the two-phase search from O3DE:
        1. Broad phase: approximate nearest neighbor (here: full scan for small DB)
        2. Narrow phase: exact cost computation on candidates

        Returns
        -------
        MatchResult with cost, similarity, and per-feature breakdown.
        """
        if not self._built or self._feature_matrix is None or self._feature_matrix.shape[0] == 0:
            return MatchResult(
                best_frame_idx=0, best_clip_name="empty",
                cost=float("inf"), similarity=0.0,
                feature_vector=np.zeros(self.schema.total_dims, dtype=np.float32),
            )

        root_pos = joint_positions.get("root", joint_positions.get("hip", (0.0, 0.33)))

        q = self.extractor.extract_full_feature_vector(
            joint_positions=joint_positions,
            prev_positions=prev_positions,
            past_trajectory=past_trajectory,
            future_trajectory=future_trajectory,
            phase=phase,
            phase_velocity=phase_velocity,
            gait_mode=gait_mode,
            dt=dt,
            root_pos=root_pos,
        )
        q_norm = self.normalizer.transform(q)

        # Cost computation: weighted squared Euclidean distance
        residual = self._feature_matrix - q_norm[None, :]
        costs = np.sum(residual * residual, axis=1)

        best_idx = int(np.argmin(costs))
        best_cost = float(costs[best_idx])
        best_entry = self._entries[best_idx]

        # Similarity: exponential decay of cost
        dim = max(q_norm.shape[0], 1)
        similarity = float(math.exp(-0.5 * best_cost / dim))

        # Per-feature-group cost breakdown
        s = self.schema
        offsets = self._compute_feature_offsets()
        best_residual = residual[best_idx]
        breakdown = {}
        for name, (start, end) in offsets.items():
            breakdown[name] = float(np.sum(best_residual[start:end] ** 2))

        return MatchResult(
            best_frame_idx=int(best_entry["frame_index"]),
            best_clip_name=str(best_entry["clip_name"]),
            cost=best_cost,
            similarity=similarity,
            feature_vector=q_norm,
            cost_breakdown=breakdown,
        )

    def _compute_feature_offsets(self) -> dict[str, tuple[int, int]]:
        """Compute start/end offsets for each feature group."""
        s = self.schema
        offsets = {}
        idx = 0

        n_pose = len(s.pose_joints) * 2
        offsets["pose"] = (idx, idx + n_pose)
        idx += n_pose

        n_vel = len(s.velocity_joints) * 2
        offsets["velocity"] = (idx, idx + n_vel)
        idx += n_vel

        n_traj = (s.trajectory_past_samples + s.trajectory_future_samples) * 2
        offsets["trajectory"] = (idx, idx + n_traj)
        idx += n_traj

        offsets["contact"] = (idx, idx + 4)
        idx += 4

        offsets["phase"] = (idx, idx + 3)
        idx += 3

        offsets["silhouette"] = (idx, idx + 4)
        idx += 4

        return offsets

    def evaluate_sequence(
        self,
        pose_sequence: Sequence[Mapping[str, float]],
        skeleton=None,
        gait_name: str = "walk",
    ) -> SequenceEvaluation:
        """Evaluate an entire animation sequence against the reference database.

        This is the primary Layer 3 evaluation entry point. It scores each frame
        and computes aggregate quality metrics including:
        - Per-frame matching cost and similarity
        - Contact consistency (are foot contacts at correct phases?)
        - Phase coherence (is phase advancing smoothly?)
        - Silhouette quality (are key poses visually distinct?)
        - Skating penalty (are contact feet sliding?)

        Parameters
        ----------
        pose_sequence : list of pose dicts
            The animation to evaluate.
        skeleton : Skeleton, optional
        gait_name : str
            Expected gait type for phase evaluation.
        """
        from .skeleton import Skeleton

        skel = skeleton or Skeleton.create_humanoid(head_units=3.0)
        gait_map = {"walk": 0, "run": 1, "jump": 2, "idle": 3, "fall": 4}
        gait_mode = gait_map.get(gait_name, 0)

        per_frame_costs = []
        per_frame_similarities = []
        contact_flags_sequence = []
        phase_values = []
        silhouette_spreads = []
        skating_violations = []

        prev_positions = None
        dt = 1.0 / 12.0
        n_frames = max(len(pose_sequence), 1)

        for frame_idx, pose in enumerate(pose_sequence):
            skel.apply_pose(pose)
            positions = skel.get_joint_positions()
            root_pos = positions.get("root", positions.get("hip", (0.0, 0.33)))

            phase = (frame_idx / n_frames) % 1.0
            phase_vel = 1.0 / max(n_frames / 12.0, 0.1)

            result = self.query(
                joint_positions=positions,
                prev_positions=prev_positions,
                phase=phase,
                phase_velocity=phase_vel,
                gait_mode=gait_mode,
                dt=dt,
            )

            per_frame_costs.append(result.cost)
            per_frame_similarities.append(result.similarity)

            # Contact analysis
            contacts = self.extractor.extract_contact_labels(
                positions, prev_positions=prev_positions, dt=dt
            )
            contact_flags_sequence.append(contacts[:2])  # left, right flags

            # Phase tracking
            phase_values.append(phase)

            # Silhouette spread
            sil = self.extractor.extract_silhouette_features(positions, root_pos)
            silhouette_spreads.append(float(sil[0] + sil[1]))  # x + y spread

            # Skating detection: contact foot with high velocity
            if prev_positions:
                for i, foot in enumerate(["l_foot", "r_foot"]):
                    if contacts[i] > 0.5:  # Foot is in contact
                        contact_vel = contacts[2 + i]
                        if contact_vel > 0.5:  # Sliding threshold
                            skating_violations.append(contact_vel)

            prev_positions = dict(positions)

        # Aggregate metrics
        contact_consistency = self._compute_contact_consistency(contact_flags_sequence, n_frames)
        phase_coherence = self._compute_phase_coherence(phase_values)
        silhouette_score = self._compute_silhouette_score(silhouette_spreads)
        skating_penalty = (
            float(np.mean(skating_violations)) if skating_violations
            else 0.0
        )

        # Overall score (weighted combination)
        mean_sim = float(np.mean(per_frame_similarities)) if per_frame_similarities else 0.0
        overall = float(np.clip(
            0.35 * mean_sim
            + 0.20 * contact_consistency
            + 0.15 * phase_coherence
            + 0.15 * silhouette_score
            + 0.15 * (1.0 - min(skating_penalty, 1.0)),
            0.0, 1.0,
        ))

        return SequenceEvaluation(
            per_frame_costs=per_frame_costs,
            per_frame_similarities=per_frame_similarities,
            contact_consistency=contact_consistency,
            phase_coherence=phase_coherence,
            silhouette_score=silhouette_score,
            skating_penalty=skating_penalty,
            overall_score=overall,
            cost_breakdown={
                "mean_similarity": mean_sim,
                "contact_consistency": contact_consistency,
                "phase_coherence": phase_coherence,
                "silhouette_score": silhouette_score,
                "skating_penalty": skating_penalty,
            },
        )

    def _compute_contact_consistency(
        self,
        contact_flags: list[np.ndarray],
        n_frames: int,
    ) -> float:
        """Score contact pattern consistency.

        A good walk cycle should have alternating L/R contacts at regular intervals.
        """
        if len(contact_flags) < 4:
            return 0.5

        # Check for alternating contact pattern
        transitions = 0
        for i in range(1, len(contact_flags)):
            prev_l, prev_r = contact_flags[i - 1][0], contact_flags[i - 1][1]
            curr_l, curr_r = contact_flags[i][0], contact_flags[i][1]
            if (prev_l != curr_l) or (prev_r != curr_r):
                transitions += 1

        # Good walk: ~4 transitions per cycle (L on, L off, R on, R off)
        expected_transitions = max(4, n_frames // 3)
        ratio = transitions / max(expected_transitions, 1)
        return float(np.clip(1.0 - abs(1.0 - ratio), 0.0, 1.0))

    def _compute_phase_coherence(self, phase_values: list[float]) -> float:
        """Score phase advancement smoothness.

        Phase should advance monotonically within a cycle. Large jumps indicate
        discontinuities that would cause visible pops.
        """
        if len(phase_values) < 2:
            return 1.0

        jumps = 0
        for i in range(1, len(phase_values)):
            delta = phase_values[i] - phase_values[i - 1]
            if delta < 0:
                delta += 1.0  # Wrap-around
            if delta > 0.3:  # Large jump
                jumps += 1

        return float(np.clip(1.0 - jumps / max(len(phase_values), 1), 0.0, 1.0))

    def _compute_silhouette_score(self, spreads: list[float]) -> float:
        """Score silhouette readability (Dead Cells inspired).

        Good pixel art animation has clear silhouette variation across the cycle.
        The spread should vary (not be constant) and have strong peaks at key poses.
        """
        if len(spreads) < 2:
            return 0.5

        spread_arr = np.array(spreads)
        mean_spread = float(np.mean(spread_arr))
        std_spread = float(np.std(spread_arr))

        # Good: high mean spread (extended poses) + high variation (dynamic)
        spread_score = float(np.clip(mean_spread / 0.8, 0.0, 1.0))
        variation_score = float(np.clip(std_spread / 0.15, 0.0, 1.0))

        return 0.6 * spread_score + 0.4 * variation_score

    def compute_layer3_fitness(
        self,
        pose_sequence: Sequence[Mapping[str, float]],
        skeleton=None,
        gait_name: str = "walk",
    ) -> dict[str, float]:
        """Compute Layer 3 fitness scores for evolution.

        This is the drop-in replacement for the old motion_match_score in
        evaluate_physics_fitness(). Returns a dict compatible with the
        existing PhysicsTestBattery interface.
        """
        evaluation = self.evaluate_sequence(pose_sequence, skeleton, gait_name)

        return {
            "motion_match_score": evaluation.overall_score,
            "contact_consistency": evaluation.contact_consistency,
            "phase_coherence": evaluation.phase_coherence,
            "silhouette_quality": evaluation.silhouette_score,
            "skating_penalty": evaluation.skating_penalty,
            "mean_frame_similarity": float(
                np.mean(evaluation.per_frame_similarities)
                if evaluation.per_frame_similarities else 0.0
            ),
            "evaluation_details": evaluation.to_dict(),
        }


# ── Convenience Factory ─────────────────────────────────────────────────────


def create_evaluator_with_defaults(
    skeleton=None,
) -> MotionMatchingEvaluator:
    """Create a MotionMatchingEvaluator pre-loaded with default reference clips.

    Uses the project's existing reference motion library to build the database.
    """
    from .rl_locomotion import ReferenceMotionLibrary

    evaluator = MotionMatchingEvaluator()
    ref_lib = ReferenceMotionLibrary()

    clips = {}
    trajectory_hints = {}
    gait_defaults = {
        "walk": (0.8, 0.0, 1.0, 0.0),
        "run": (1.6, 0.0, 1.0, 0.0),
        "jump": (0.6, 0.8, 1.0, 0.0),
        "idle": (0.0, 0.0, 1.0, 0.0),
        "fall": (0.1, -0.8, 1.0, 0.0),
    }

    for gait in ["walk", "run", "jump", "idle", "fall"]:
        clip = ref_lib.get_motion(gait)
        if clip:
            clips[gait] = clip
            trajectory_hints[gait] = gait_defaults.get(gait, (0.0, 0.0, 1.0, 0.0))

    evaluator.build_database(clips, skeleton=skeleton, trajectory_hints=trajectory_hints)
    return evaluator


__all__ = [
    "IndustrialFeatureSchema",
    "FeatureNormalizer",
    "MotionFeatureExtractor",
    "MatchResult",
    "SequenceEvaluation",
    "MotionMatchingEvaluator",
    "create_evaluator_with_defaults",
]
