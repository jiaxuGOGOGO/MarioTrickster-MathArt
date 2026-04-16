"""SESSION-039 — Runtime Motion Matching Query & Optimal Entry Frame Selection.

Industrial-grade runtime motion query system distilled from:

  - Simon Clavet (Ubisoft), GDC 2016:
    "Motion Matching and The Road to Next-Gen Animation"
  - Daniel Holden (Epic Games), 2020:
    "Learned Motion Matching" (ACM TOG)
  - O3DE Motion Matching implementation (Benjamin Jillich, 2022)
  - Unreal Engine 5.4 Motion Matching (PoseSearch plugin)

Design Philosophy
-----------------
Traditional animation state machines always enter a new clip at frame 0 when
transitioning between states (e.g., Run → Jump). This creates visible pops
because frame 0 of the target clip rarely matches the character's current
velocity, foot contacts, and phase.

Runtime Motion Matching solves this by searching the target clip's database
for the **optimal entry frame** — the frame whose feature vector (velocity,
foot contacts, phase, trajectory) best matches the character's current state
at the moment of transition.

This module extends the existing ``MotionMatchingEvaluator`` (SESSION-034)
with a UMR-native runtime query interface that:

  1. Accepts ``UnifiedMotionFrame`` directly (no pose-dict conversion needed)
  2. Builds per-clip feature databases from ``UnifiedMotionClip`` instances
  3. Computes transition cost: ``Cost = w_vel * diff(velocity) + w_contact * diff(foot_contacts) + w_phase * diff(phase)``
  4. Returns the optimal entry frame index + transition cost
  5. Integrates with ``TransitionSynthesizer`` for seamless inertialized transitions

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────────────┐
    │  RuntimeMotionDatabase                                               │
    │  ├─ add_clip(clip: UnifiedMotionClip) → build per-clip features      │
    │  ├─ add_clips_from_library(ReferenceMotionLibrary) → batch build     │
    │  └─ get_clip_features(clip_name) → feature matrix                    │
    ├──────────────────────────────────────────────────────────────────────┤
    │  RuntimeMotionQuery                                                  │
    │  ├─ query_best_entry(current_frame, target_clip) → EntryResult       │
    │  ├─ query_best_clip_and_entry(current_frame) → global best match     │
    │  ├─ should_transition(current_cost) → bool (cost threshold check)    │
    │  └─ get_diagnostics() → per-feature cost breakdown                   │
    ├──────────────────────────────────────────────────────────────────────┤
    │  MotionMatchingRuntime                                               │
    │  ├─ tick(current_frame, desired_state) → output frame                │
    │  ├─ force_transition(target_state) → trigger immediate transition    │
    │  └─ get_state() → current playback state                             │
    └──────────────────────────────────────────────────────────────────────┘

Integration with TransitionSynthesizer:
    When a state change is detected, ``MotionMatchingRuntime`` uses
    ``RuntimeMotionQuery`` to find the optimal entry frame, then hands
    off to ``TransitionSynthesizer`` for inertialized blending.

Integration with Layer 3:
    ``RuntimeMotionQuery.get_diagnostics()`` returns per-feature cost
    breakdowns that feed into ``PhysicsTestBattery`` and evolution fitness.

References:
    [1] S. Clavet, "Motion Matching and The Road to Next-Gen Animation",
        GDC 2016, Ubisoft Montreal.
    [2] D. Holden et al., "Learned Motion Matching", ACM TOG 39(4), 2020.
    [3] O3DE Motion Matching, docs.o3de.org/blog/posts/blog-motionmatching/
    [4] UE5 PoseSearch, dev.epicgames.com/documentation/unreal-engine/motion-matching
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from .unified_motion import (
    PhaseState,
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
    pose_to_umr,
    infer_contact_tags,
)


# ── Feature Extraction for UMR Frames ─────────────────────────────────────


@dataclass
class RuntimeFeatureWeights:
    """Weights for the runtime motion matching cost function.

    These weights determine the relative importance of each feature
    when computing the matching cost. Tuned based on Clavet (GDC 2016)
    recommendations and O3DE defaults.

    The contact weight is intentionally high because contact mismatch
    is the primary cause of foot skating during transitions.
    """

    velocity: float = 1.0        # Root velocity matching
    foot_contact: float = 2.0    # Foot contact label matching (critical!)
    phase: float = 0.8           # Gait phase matching
    joint_pose: float = 0.6      # Joint rotation similarity
    trajectory: float = 1.0      # Future trajectory direction
    foot_velocity: float = 1.5   # Foot velocity (low = planted, high = swing)


@dataclass
class RuntimeFeatureVector:
    """Compact feature vector extracted from a UMR frame for runtime matching.

    This is deliberately simpler than the full IndustrialFeatureSchema used
    by the evaluator — runtime queries need to be fast, and the most important
    features for transition quality are velocity, contacts, and phase.
    """

    root_vx: float = 0.0
    root_vy: float = 0.0
    left_contact: float = 0.0
    right_contact: float = 0.0
    phase_sin: float = 0.0
    phase_cos: float = 0.0
    phase_velocity: float = 0.0
    # Foot velocities (for skating prevention)
    left_foot_vel: float = 0.0
    right_foot_vel: float = 0.0
    # Compact joint pose (hip angles as proxy for full pose)
    l_hip: float = 0.0
    r_hip: float = 0.0
    l_knee: float = 0.0
    r_knee: float = 0.0
    spine: float = 0.0
    # Future trajectory direction
    traj_dx: float = 0.0
    traj_dy: float = 0.0

    def to_array(self) -> np.ndarray:
        """Convert to numpy array for vectorized cost computation."""
        return np.array([
            self.root_vx, self.root_vy,
            self.left_contact, self.right_contact,
            self.phase_sin, self.phase_cos, self.phase_velocity,
            self.left_foot_vel, self.right_foot_vel,
            self.l_hip, self.r_hip, self.l_knee, self.r_knee, self.spine,
            self.traj_dx, self.traj_dy,
        ], dtype=np.float32)

    @staticmethod
    def dim() -> int:
        return 16


def extract_runtime_features(
    frame: UnifiedMotionFrame,
    prev_frame: Optional[UnifiedMotionFrame] = None,
    dt: float = 1.0 / 24.0,
) -> RuntimeFeatureVector:
    """Extract a compact runtime feature vector from a UMR frame.

    This is the UMR-native equivalent of ``MotionFeatureExtractor.extract_umr_context()``,
    optimized for fast runtime queries rather than comprehensive evaluation.

    Parameters
    ----------
    frame : UnifiedMotionFrame
        Current frame to extract features from.
    prev_frame : UnifiedMotionFrame, optional
        Previous frame for velocity computation.
    dt : float
        Time step between frames.

    Returns
    -------
    RuntimeFeatureVector
        Compact feature vector for matching.
    """
    rt = frame.root_transform
    ct = frame.contact_tags
    joints = frame.joint_local_rotations

    # Phase encoding via PhaseState (Gap 1: Generalized Phase State)
    # Uses the canonical PhaseState if available, otherwise falls back to legacy.
    ps = frame.phase_state
    if ps is None:
        ps = PhaseState(value=float(frame.phase), is_cyclic=True, phase_kind="cyclic")
    phase = ps.to_float()
    phase_sin, phase_cos = ps.to_sin_cos()

    # Phase velocity — gate mechanism determines wrapping behavior
    if prev_frame is not None:
        prev_ps = prev_frame.phase_state
        if prev_ps is None:
            prev_ps = PhaseState(value=float(prev_frame.phase), is_cyclic=True, phase_kind="cyclic")
        prev_phase = prev_ps.to_float()
        if ps.is_cyclic:
            # Cyclic: wrap-aware velocity
            phase_vel = ((phase - prev_phase) % 1.0) / max(dt, 1e-6)
        else:
            # Transient: direct difference, no wrapping
            phase_vel = (phase - prev_phase) / max(dt, 1e-6)
    else:
        phase_vel = 1.0

    # Foot velocity estimation from root velocity + contact state
    # When foot is in contact, its velocity should be near zero
    # When foot is in swing, velocity correlates with root velocity
    left_foot_vel = 0.0 if ct.left_foot else abs(rt.velocity_x) + abs(rt.velocity_y)
    right_foot_vel = 0.0 if ct.right_foot else abs(rt.velocity_x) + abs(rt.velocity_y)

    # Future trajectory direction (from root velocity)
    speed = math.hypot(rt.velocity_x, rt.velocity_y)
    if speed > 1e-4:
        traj_dx = rt.velocity_x / speed
        traj_dy = rt.velocity_y / speed
    else:
        traj_dx = 1.0  # Default facing right
        traj_dy = 0.0

    return RuntimeFeatureVector(
        root_vx=rt.velocity_x,
        root_vy=rt.velocity_y,
        left_contact=1.0 if ct.left_foot else 0.0,
        right_contact=1.0 if ct.right_foot else 0.0,
        phase_sin=phase_sin,
        phase_cos=phase_cos,
        phase_velocity=phase_vel,
        left_foot_vel=left_foot_vel,
        right_foot_vel=right_foot_vel,
        l_hip=joints.get("l_hip", 0.0),
        r_hip=joints.get("r_hip", 0.0),
        l_knee=joints.get("l_knee", 0.0),
        r_knee=joints.get("r_knee", 0.0),
        spine=joints.get("spine", 0.0),
        traj_dx=traj_dx,
        traj_dy=traj_dy,
    )


# ── Entry Frame Result ─────────────────────────────────────────────────────


@dataclass
class EntryFrameResult:
    """Result of a runtime motion matching query.

    Contains the optimal entry frame index, the matching cost, and
    per-feature cost breakdown for diagnostics.
    """

    clip_name: str
    entry_frame_idx: int
    cost: float
    similarity: float
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_name": self.clip_name,
            "entry_frame_idx": int(self.entry_frame_idx),
            "cost": float(self.cost),
            "similarity": float(self.similarity),
            "cost_breakdown": {k: float(v) for k, v in self.cost_breakdown.items()},
        }


# ── Runtime Motion Database ───────────────────────────────────────────────


class RuntimeMotionDatabase:
    """UMR-native motion database for runtime queries.

    Stores pre-computed feature vectors for all frames in all registered
    clips. Supports both ``UnifiedMotionClip`` and legacy ``ReferenceMotionLibrary``
    as clip sources.

    The database uses per-column normalization (Clavet GDC 2016) to ensure
    all features contribute proportionally to the matching cost.
    """

    def __init__(self, weights: Optional[RuntimeFeatureWeights] = None):
        self.weights = weights or RuntimeFeatureWeights()

        # Per-clip storage
        self._clip_features: dict[str, np.ndarray] = {}  # clip_name → (N, D) matrix
        self._clip_frames: dict[str, list[UnifiedMotionFrame]] = {}

        # Global normalization
        self._global_mean: Optional[np.ndarray] = None
        self._global_std: Optional[np.ndarray] = None
        self._normalized: bool = False

        # Weight vector for cost computation
        self._weight_vector = self._build_weight_vector()

    def _build_weight_vector(self) -> np.ndarray:
        """Build the per-feature weight vector from RuntimeFeatureWeights."""
        w = self.weights
        return np.array([
            w.velocity, w.velocity,           # root_vx, root_vy
            w.foot_contact, w.foot_contact,   # left_contact, right_contact
            w.phase, w.phase, w.phase,        # phase_sin, phase_cos, phase_velocity
            w.foot_velocity, w.foot_velocity, # left_foot_vel, right_foot_vel
            w.joint_pose, w.joint_pose,       # l_hip, r_hip
            w.joint_pose, w.joint_pose,       # l_knee, r_knee
            w.joint_pose,                     # spine
            w.trajectory, w.trajectory,       # traj_dx, traj_dy
        ], dtype=np.float32)

    def add_umr_clip(self, clip: UnifiedMotionClip) -> None:
        """Add a UMR clip to the database.

        Extracts runtime feature vectors for every frame in the clip
        and stores them for fast query-time matching.

        Parameters
        ----------
        clip : UnifiedMotionClip
            The motion clip to register.
        """
        if not clip.frames:
            return

        features = []
        prev_frame = None
        dt = 1.0 / max(clip.fps, 1)

        for frame in clip.frames:
            fv = extract_runtime_features(frame, prev_frame, dt)
            features.append(fv.to_array())
            prev_frame = frame

        self._clip_features[clip.clip_id] = np.stack(features, axis=0)
        self._clip_frames[clip.clip_id] = list(clip.frames)
        self._normalized = False  # Invalidate normalization

    def add_legacy_clip(
        self,
        name: str,
        frames: list[dict[str, float]],
        state: str = "",
        fps: int = 24,
    ) -> None:
        """Add a legacy pose-dict clip by converting to UMR first.

        This bridges the existing ``ReferenceMotionLibrary`` format into
        the runtime query system.
        """
        umr_frames = []
        dt = 1.0 / max(fps, 1)

        for i, pose in enumerate(frames):
            phase = (i / max(len(frames), 1)) % 1.0
            contact_tags = infer_contact_tags(phase, state or name)

            # Estimate root velocity from pose heuristics
            hip_angle = pose.get("l_hip", 0.0) - pose.get("r_hip", 0.0)
            root_vx = hip_angle * 2.0  # Rough velocity proxy
            root_vy = 0.0
            if state in ("jump", "fall"):
                root_vy = pose.get("spine", 0.0) * 1.5

            root_transform = MotionRootTransform(
                x=i * dt * max(root_vx, 0.1),
                y=0.0,
                velocity_x=root_vx,
                velocity_y=root_vy,
            )

            umr_frame = pose_to_umr(
                pose,
                time=i * dt,
                phase=phase,
                root_transform=root_transform,
                contact_tags=contact_tags,
                frame_index=i,
                source_state=state or name,
                metadata={"phase_kind": "cyclic", "generator": "legacy_import"},
            )
            umr_frames.append(umr_frame)

        clip = UnifiedMotionClip(
            clip_id=name,
            state=state or name,
            fps=fps,
            frames=umr_frames,
            metadata={"source": "legacy_reference_library"},
        )
        self.add_umr_clip(clip)

    def add_from_reference_library(self) -> None:
        """Import all clips from the project's ReferenceMotionLibrary.

        This is the primary bootstrap method for populating the runtime
        database from existing project assets.
        """
        from .rl_locomotion import ReferenceMotionLibrary

        lib = ReferenceMotionLibrary()
        for name in lib.list_motions():
            frames = lib.get_motion(name)
            if frames:
                self.add_legacy_clip(name, frames, state=name, fps=24)

    def normalize(self) -> None:
        """Compute global normalization statistics across all clips.

        Must be called after all clips are added and before querying.
        Uses per-column mean/std normalization (Clavet GDC 2016).
        """
        all_features = []
        for features in self._clip_features.values():
            all_features.append(features)

        if not all_features:
            self._global_mean = np.zeros(RuntimeFeatureVector.dim(), dtype=np.float32)
            self._global_std = np.ones(RuntimeFeatureVector.dim(), dtype=np.float32)
            self._normalized = True
            return

        combined = np.concatenate(all_features, axis=0)
        self._global_mean = np.mean(combined, axis=0).astype(np.float32)
        self._global_std = np.std(combined, axis=0).astype(np.float32)
        self._global_std = np.where(self._global_std < 1e-8, 1.0, self._global_std)

        # Normalize all stored features
        for name in self._clip_features:
            self._clip_features[name] = (
                (self._clip_features[name] - self._global_mean[None, :])
                / self._global_std[None, :]
            )

        self._normalized = True

    def get_clip_names(self) -> list[str]:
        """Return all registered clip names."""
        return list(self._clip_features.keys())

    def get_clip_frame_count(self, clip_name: str) -> int:
        """Return the number of frames in a clip."""
        features = self._clip_features.get(clip_name)
        return features.shape[0] if features is not None else 0

    def get_clip_frame(self, clip_name: str, frame_idx: int) -> Optional[UnifiedMotionFrame]:
        """Retrieve a specific UMR frame from a registered clip."""
        frames = self._clip_frames.get(clip_name)
        if frames and 0 <= frame_idx < len(frames):
            return frames[frame_idx]
        return None


# ── Runtime Motion Query ──────────────────────────────────────────────────


class RuntimeMotionQuery:
    """Runtime motion matching query engine.

    Given the character's current state (as a UMR frame), finds the optimal
    entry frame in a target clip or across all clips. This replaces the naive
    "always start at frame 0" approach with proper motion matching.

    The cost function follows Clavet (GDC 2016):
    ``Cost = sum(w_i * (query_i - db_i)^2)``

    where features are normalized per-column and weighted by importance.
    """

    def __init__(
        self,
        database: RuntimeMotionDatabase,
        transition_cost_threshold: float = 5.0,
    ):
        self.database = database
        self.transition_cost_threshold = transition_cost_threshold
        self._last_diagnostics: dict[str, Any] = {}

    def query_best_entry(
        self,
        current_frame: UnifiedMotionFrame,
        target_clip_name: str,
        prev_frame: Optional[UnifiedMotionFrame] = None,
        dt: float = 1.0 / 24.0,
        exclude_frames: Optional[set[int]] = None,
    ) -> EntryFrameResult:
        """Find the optimal entry frame in a specific target clip.

        Instead of always entering at frame 0, this searches the entire
        target clip for the frame whose velocity, contacts, and phase
        best match the character's current state.

        Parameters
        ----------
        current_frame : UnifiedMotionFrame
            The character's current state at the moment of transition.
        target_clip_name : str
            Name of the target clip to search.
        prev_frame : UnifiedMotionFrame, optional
            Previous frame for velocity estimation.
        dt : float
            Time step for feature extraction.
        exclude_frames : set[int], optional
            Frame indices to exclude from the search (e.g., already visited).

        Returns
        -------
        EntryFrameResult
            The optimal entry frame with cost and diagnostics.
        """
        if not self.database._normalized:
            self.database.normalize()

        clip_features = self.database._clip_features.get(target_clip_name)
        if clip_features is None or clip_features.shape[0] == 0:
            return EntryFrameResult(
                clip_name=target_clip_name,
                entry_frame_idx=0,
                cost=float("inf"),
                similarity=0.0,
            )

        # Extract query features
        query_fv = extract_runtime_features(current_frame, prev_frame, dt)
        query_arr = query_fv.to_array()

        # Normalize query
        if self.database._global_mean is not None:
            query_norm = (query_arr - self.database._global_mean) / self.database._global_std
        else:
            query_norm = query_arr

        # Weighted squared distance
        weights = self.database._weight_vector
        residual = clip_features - query_norm[None, :]
        weighted_residual = residual * weights[None, :]
        costs = np.sum(weighted_residual * weighted_residual, axis=1)

        # Exclude specified frames
        if exclude_frames:
            for idx in exclude_frames:
                if 0 <= idx < len(costs):
                    costs[idx] = float("inf")

        best_idx = int(np.argmin(costs))
        best_cost = float(costs[best_idx])

        # Similarity score
        dim = max(query_norm.shape[0], 1)
        similarity = float(math.exp(-0.5 * best_cost / dim))

        # Per-feature cost breakdown for diagnostics
        best_residual = weighted_residual[best_idx]
        breakdown = self._compute_breakdown(best_residual)

        self._last_diagnostics = {
            "query_clip": target_clip_name,
            "entry_frame": best_idx,
            "cost": best_cost,
            "similarity": similarity,
            "breakdown": breakdown,
            "total_candidates": int(clip_features.shape[0]),
        }

        return EntryFrameResult(
            clip_name=target_clip_name,
            entry_frame_idx=best_idx,
            cost=best_cost,
            similarity=similarity,
            cost_breakdown=breakdown,
        )

    def query_best_clip_and_entry(
        self,
        current_frame: UnifiedMotionFrame,
        candidate_clips: Optional[list[str]] = None,
        prev_frame: Optional[UnifiedMotionFrame] = None,
        dt: float = 1.0 / 24.0,
    ) -> EntryFrameResult:
        """Find the best matching frame across all (or specified) clips.

        This is the global motion matching query: it searches all registered
        clips and returns the single best match. Useful for open-ended
        animation systems where the target state isn't predetermined.

        Parameters
        ----------
        current_frame : UnifiedMotionFrame
            Current character state.
        candidate_clips : list[str], optional
            Restrict search to these clips. If None, searches all clips.
        prev_frame : UnifiedMotionFrame, optional
            Previous frame for velocity estimation.
        dt : float
            Time step.

        Returns
        -------
        EntryFrameResult
            Global best match across all searched clips.
        """
        clips_to_search = candidate_clips or self.database.get_clip_names()

        best_result = EntryFrameResult(
            clip_name="none",
            entry_frame_idx=0,
            cost=float("inf"),
            similarity=0.0,
        )

        for clip_name in clips_to_search:
            result = self.query_best_entry(
                current_frame, clip_name, prev_frame, dt
            )
            if result.cost < best_result.cost:
                best_result = result

        return best_result

    def should_transition(
        self,
        current_continuation_cost: float,
        best_match_cost: float,
    ) -> bool:
        """Determine whether a transition should be triggered.

        Follows the O3DE/UE5 pattern: transition only if the best match
        cost is significantly lower than continuing the current clip.

        Parameters
        ----------
        current_continuation_cost : float
            Cost of continuing the current clip at the next frame.
        best_match_cost : float
            Cost of the best matching frame in another clip.

        Returns
        -------
        bool
            True if transition should occur.
        """
        # Transition if: best match is at least 30% better than continuation
        # AND best match cost is below absolute threshold
        improvement = current_continuation_cost - best_match_cost
        relative_improvement = improvement / max(current_continuation_cost, 1e-6)

        return (
            best_match_cost < self.transition_cost_threshold
            and relative_improvement > 0.3
        )

    def get_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics from the last query for Layer 3 integration."""
        return dict(self._last_diagnostics)

    @staticmethod
    def _compute_breakdown(weighted_residual: np.ndarray) -> dict[str, float]:
        """Compute per-feature-group cost breakdown."""
        r = weighted_residual
        return {
            "velocity": float(r[0] ** 2 + r[1] ** 2),
            "foot_contact": float(r[2] ** 2 + r[3] ** 2),
            "phase": float(r[4] ** 2 + r[5] ** 2 + r[6] ** 2),
            "foot_velocity": float(r[7] ** 2 + r[8] ** 2),
            "joint_pose": float(r[9] ** 2 + r[10] ** 2 + r[11] ** 2 + r[12] ** 2 + r[13] ** 2),
            "trajectory": float(r[14] ** 2 + r[15] ** 2),
        }


# ── Motion Matching Runtime ───────────────────────────────────────────────


@dataclass
class PlaybackState:
    """Current playback state of the motion matching runtime."""

    clip_name: str = ""
    frame_idx: int = 0
    elapsed: float = 0.0
    state: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_name": self.clip_name,
            "frame_idx": int(self.frame_idx),
            "elapsed": float(self.elapsed),
            "state": self.state,
        }


class MotionMatchingRuntime:
    """Complete runtime animation system with motion matching + inertialization.

    This is the top-level runtime that combines:
    1. ``RuntimeMotionQuery`` for optimal entry frame selection
    2. ``TransitionSynthesizer`` for inertialized transitions
    3. Clip playback management

    Usage::

        runtime = MotionMatchingRuntime()
        runtime.initialize()  # Loads reference library

        # Each frame:
        output_frame = runtime.tick(desired_state="run", dt=1/24)

    The runtime automatically handles:
    - Finding the best entry frame when state changes
    - Inertialized transitions between clips
    - Continuous playback within a clip
    - Quality metrics for Layer 3 integration
    """

    def __init__(
        self,
        transition_strategy: str = "dead_blending",
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
        transition_cost_threshold: float = 5.0,
    ):
        from .transition_synthesizer import (
            TransitionSynthesizer,
            TransitionStrategy,
        )

        self.database = RuntimeMotionDatabase()
        self.query_engine = RuntimeMotionQuery(
            self.database,
            transition_cost_threshold=transition_cost_threshold,
        )
        self.synthesizer = TransitionSynthesizer(
            strategy=TransitionStrategy(transition_strategy),
            blend_time=blend_time,
            decay_halflife=decay_halflife,
        )

        self._playback = PlaybackState()
        self._current_frame: Optional[UnifiedMotionFrame] = None
        self._prev_frame: Optional[UnifiedMotionFrame] = None
        self._initialized = False
        self._transition_log: list[dict[str, Any]] = []

    def initialize(self) -> None:
        """Initialize the runtime with the project's reference motion library."""
        self.database.add_from_reference_library()
        self.database.normalize()
        self._initialized = True

    def tick(
        self,
        desired_state: str = "",
        dt: float = 1.0 / 24.0,
    ) -> Optional[UnifiedMotionFrame]:
        """Advance one frame of the motion matching runtime.

        Parameters
        ----------
        desired_state : str
            The desired animation state (e.g., "run", "jump", "idle").
            If different from current state, triggers a transition.
        dt : float
            Time step.

        Returns
        -------
        UnifiedMotionFrame or None
            The output frame, or None if not initialized.
        """
        if not self._initialized:
            return None

        # State change detection
        if desired_state and desired_state != self._playback.state:
            self._handle_state_change(desired_state, dt)

        # Advance playback
        target_frame = self._advance_playback(dt)
        if target_frame is None:
            return self._current_frame

        # Apply transition synthesis
        output = self.synthesizer.update(target_frame, dt)

        # Update state
        self._prev_frame = self._current_frame
        self._current_frame = output

        return output

    def force_transition(
        self,
        target_state: str,
        dt: float = 1.0 / 24.0,
    ) -> Optional[UnifiedMotionFrame]:
        """Force an immediate transition to a new state.

        Bypasses the cost threshold check and always transitions.
        """
        if not self._initialized:
            return None
        self._handle_state_change(target_state, dt)
        return self.tick(target_state, dt)

    def get_state(self) -> PlaybackState:
        """Return the current playback state."""
        return PlaybackState(
            clip_name=self._playback.clip_name,
            frame_idx=self._playback.frame_idx,
            elapsed=self._playback.elapsed,
            state=self._playback.state,
        )

    def get_transition_log(self) -> list[dict[str, Any]]:
        """Return the log of all transitions for diagnostics."""
        return list(self._transition_log)

    def get_quality_metrics(self) -> dict[str, Any]:
        """Return quality metrics for Layer 3 integration."""
        transition_quality = self.synthesizer.get_transition_quality()
        return {
            "playback_state": self._playback.to_dict(),
            "transition_quality": transition_quality.to_dict(),
            "transition_count": len(self._transition_log),
            "query_diagnostics": self.query_engine.get_diagnostics(),
        }

    def _handle_state_change(self, new_state: str, dt: float) -> None:
        """Handle a state change by finding the optimal entry frame."""
        # Find best entry frame in the target clip
        if self._current_frame is not None:
            entry_result = self.query_engine.query_best_entry(
                current_frame=self._current_frame,
                target_clip_name=new_state,
                prev_frame=self._prev_frame,
                dt=dt,
            )
        else:
            entry_result = EntryFrameResult(
                clip_name=new_state,
                entry_frame_idx=0,
                cost=0.0,
                similarity=1.0,
            )

        # Get the target frame at the optimal entry point
        target_frame = self.database.get_clip_frame(
            new_state, entry_result.entry_frame_idx
        )

        # Trigger inertialized transition
        if target_frame is not None and self._current_frame is not None:
            self.synthesizer.request_transition(
                source_frame=self._current_frame,
                target_frame=target_frame,
                prev_source_frame=self._prev_frame,
                dt=dt,
            )

        # Update playback state
        self._playback = PlaybackState(
            clip_name=new_state,
            frame_idx=entry_result.entry_frame_idx,
            elapsed=0.0,
            state=new_state,
        )

        # Log transition
        self._transition_log.append({
            "from_state": self._current_frame.source_state if self._current_frame else "none",
            "to_state": new_state,
            "entry_frame": entry_result.entry_frame_idx,
            "cost": entry_result.cost,
            "similarity": entry_result.similarity,
            "breakdown": entry_result.cost_breakdown,
        })

    def _advance_playback(self, dt: float) -> Optional[UnifiedMotionFrame]:
        """Advance the current clip playback by one frame."""
        clip_name = self._playback.clip_name
        if not clip_name:
            return None

        frame_count = self.database.get_clip_frame_count(clip_name)
        if frame_count == 0:
            return None

        # Get current frame
        frame = self.database.get_clip_frame(clip_name, self._playback.frame_idx)

        # Advance frame index (loop for cyclic clips)
        self._playback.frame_idx = (self._playback.frame_idx + 1) % frame_count
        self._playback.elapsed += dt

        return frame


# ── Convenience Factories ─────────────────────────────────────────────────


def create_runtime_database() -> RuntimeMotionDatabase:
    """Create a RuntimeMotionDatabase pre-loaded with the reference library."""
    db = RuntimeMotionDatabase()
    db.add_from_reference_library()
    db.normalize()
    return db


def create_motion_matching_runtime(
    strategy: str = "dead_blending",
    blend_time: float = 0.2,
) -> MotionMatchingRuntime:
    """Create and initialize a complete motion matching runtime.

    This is the recommended entry point for using the runtime system.
    """
    runtime = MotionMatchingRuntime(
        transition_strategy=strategy,
        blend_time=blend_time,
    )
    runtime.initialize()
    return runtime


__all__ = [
    "RuntimeFeatureWeights",
    "RuntimeFeatureVector",
    "extract_runtime_features",
    "EntryFrameResult",
    "RuntimeMotionDatabase",
    "RuntimeMotionQuery",
    "PlaybackState",
    "MotionMatchingRuntime",
    "create_runtime_database",
    "create_motion_matching_runtime",
]
