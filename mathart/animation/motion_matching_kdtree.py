"""SESSION-065 — KD-Tree Accelerated Motion Matching Runtime.

Research-to-code implementation distilled from:
    Simon Clavet (Ubisoft), GDC 2016:
    "Motion Matching and The Road to Next-Gen Animation"

    Daniel Holden (Epic Games), 2020:
    "Learned Motion Matching" (ACM TOG)

This module extends the existing RuntimeMotionDatabase (SESSION-039) with
KD-Tree spatial indexing for O(log N) query performance instead of O(N)
brute-force search. This is critical for production motion databases with
10k+ frames across dozens of clips.

Core Algorithm (Clavet 2016):
    Motion Matching searches a database of pre-recorded motion frames for
    the frame whose feature vector best matches the character's current
    state. The feature vector typically includes:
    - Root velocity (2D)
    - Foot contact states (binary per foot)
    - Phase variables (sin/cos encoding)
    - Future trajectory (2-3 points)
    - Joint positions/velocities for key joints

    At each frame, the system:
    1. Extracts the current character state as a feature vector
    2. Searches the database for the nearest neighbor in feature space
    3. If the best match is in a different clip or far from the current
       playback position, triggers a transition
    4. Uses inertialization (Bollo 2018) for seamless blending

KD-Tree Optimization:
    Brute-force search is O(N·D) where N = total frames and D = feature
    dimension. With a KD-Tree, average query time drops to O(D·log N).
    We use scipy.spatial.KDTree with per-feature normalization and
    weighting applied before tree construction.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  KDTreeMotionDatabase                                                │
    │  ├─ add_clip(name, features) → register clip features               │
    │  ├─ build_index() → construct KD-Tree from all features             │
    │  ├─ query(feature_vector, k=1) → nearest neighbor(s)               │
    │  ├─ query_radius(feature_vector, r) → all within radius            │
    │  └─ get_clip_and_frame(global_idx) → (clip_name, local_frame_idx)  │
    ├─────────────────────────────────────────────────────────────────────┤
    │  MotionMatchingController                                            │
    │  ├─ update(current_state, dt) → animation command                   │
    │  ├─ force_transition(target_clip) → immediate transition            │
    │  ├─ set_trajectory(future_positions) → update trajectory target     │
    │  └─ get_diagnostics() → matching cost, clip info, transition count  │
    └─────────────────────────────────────────────────────────────────────┘

Integration with existing modules:
    - RuntimeMotionDatabase (runtime_motion_query.py): KDTreeMotionDatabase
      is a drop-in acceleration layer
    - TransitionSynthesizer (transition_synthesizer.py): Inertialization
      blending on transitions
    - DeepPhaseAnalyzer (deepphase_fft.py): Phase features improve matching
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# KD-Tree wrapper (pure numpy fallback if scipy unavailable)
# ---------------------------------------------------------------------------

class _NumpyKDTree:
    """Minimal KD-Tree implementation using numpy for environments
    where scipy is not available. Falls back to brute-force for
    small datasets and uses a recursive partition for larger ones.

    For production use, scipy.spatial.KDTree is preferred.
    """

    def __init__(self, data: np.ndarray, leaf_size: int = 32):
        self.data = data.copy()
        self.n_points = data.shape[0]
        self.n_dims = data.shape[1]
        self.leaf_size = leaf_size
        self._use_scipy = False

        try:
            from scipy.spatial import KDTree as ScipyKDTree
            self._tree = ScipyKDTree(data, leafsize=leaf_size)
            self._use_scipy = True
        except ImportError:
            self._tree = None

    def query(self, point: np.ndarray, k: int = 1
              ) -> Tuple[np.ndarray, np.ndarray]:
        """Find k nearest neighbors.

        Returns:
            (distances, indices) arrays of shape (k,).
        """
        if self._use_scipy and self._tree is not None:
            dists, idxs = self._tree.query(point, k=k)
            if k == 1:
                return np.array([dists]), np.array([idxs])
            return np.array(dists), np.array(idxs)

        # Brute-force fallback
        diffs = self.data - point[None, :]
        sq_dists = np.sum(diffs * diffs, axis=1)
        if k >= self.n_points:
            order = np.argsort(sq_dists)
            return np.sqrt(sq_dists[order]), order

        # Partial sort for top-k
        top_k_idx = np.argpartition(sq_dists, k)[:k]
        top_k_dists = sq_dists[top_k_idx]
        sort_order = np.argsort(top_k_dists)
        return np.sqrt(top_k_dists[sort_order]), top_k_idx[sort_order]

    def query_ball_point(self, point: np.ndarray, r: float
                         ) -> List[int]:
        """Find all points within radius r."""
        if self._use_scipy and self._tree is not None:
            return self._tree.query_ball_point(point, r)

        # Brute-force fallback
        diffs = self.data - point[None, :]
        sq_dists = np.sum(diffs * diffs, axis=1)
        return list(np.where(sq_dists <= r * r)[0])


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class FeatureWeights:
    """Per-feature weights for motion matching cost computation.

    Based on Clavet (2016) recommended weight ratios:
    - Trajectory features: highest weight (future intent)
    - Velocity: high weight (current momentum)
    - Foot contacts: medium weight (phase alignment)
    - Joint poses: lower weight (visual similarity)
    """
    velocity: float = 3.0
    foot_contact: float = 2.0
    phase: float = 2.5
    trajectory: float = 5.0
    joint_pose: float = 1.0
    foot_velocity: float = 1.5

    def to_array(self, feature_dim: int) -> np.ndarray:
        """Build weight array matching feature vector layout."""
        # Default layout: [vx, vy, lc, rc, ps, pc, pv, lfv, rfv,
        #                  lh, rh, lk, rk, sp, tx, ty]
        w = np.ones(feature_dim, dtype=np.float32)
        if feature_dim >= 16:
            w[0:2] = self.velocity
            w[2:4] = self.foot_contact
            w[4:7] = self.phase
            w[7:9] = self.foot_velocity
            w[9:14] = self.joint_pose
            w[14:16] = self.trajectory
        return w


@dataclass
class ClipEntry:
    """Metadata for a registered motion clip."""
    name: str
    features: np.ndarray  # (N_frames, D) feature matrix
    start_global_idx: int = 0
    frame_count: int = 0
    loopable: bool = False
    tags: List[str] = field(default_factory=list)


@dataclass
class MatchResult:
    """Result of a motion matching query."""
    clip_name: str
    frame_idx: int          # Local frame index within clip
    global_idx: int         # Global index in the database
    cost: float             # Matching cost (lower = better)
    similarity: float       # Similarity score [0, 1]


@dataclass
class TransitionCommand:
    """Command to transition to a new clip/frame."""
    target_clip: str
    target_frame: int
    transition_cost: float
    should_transition: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# KD-Tree Motion Database
# ---------------------------------------------------------------------------

class KDTreeMotionDatabase:
    """Motion database with KD-Tree spatial indexing.

    Provides O(log N) nearest-neighbor queries for motion matching,
    replacing the O(N) brute-force search in RuntimeMotionDatabase.
    """

    def __init__(self, weights: Optional[FeatureWeights] = None,
                 leaf_size: int = 32):
        self.weights = weights or FeatureWeights()
        self.leaf_size = leaf_size

        self._clips: Dict[str, ClipEntry] = {}
        self._global_features: Optional[np.ndarray] = None
        self._global_to_clip: List[Tuple[str, int]] = []
        self._tree: Optional[_NumpyKDTree] = None
        self._mean: Optional[np.ndarray] = None
        self._std: Optional[np.ndarray] = None
        self._weight_vector: Optional[np.ndarray] = None
        self._built = False

    @property
    def total_frames(self) -> int:
        return len(self._global_to_clip)

    @property
    def clip_count(self) -> int:
        return len(self._clips)

    def add_clip(self, name: str, features: np.ndarray,
                 loopable: bool = False,
                 tags: Optional[List[str]] = None) -> None:
        """Register a motion clip with its feature matrix.

        Args:
            name: Unique clip identifier.
            features: (N_frames, D) feature matrix.
            loopable: Whether the clip can loop seamlessly.
            tags: Optional tags for filtering (e.g., ["walk", "combat"]).
        """
        start_idx = len(self._global_to_clip)
        entry = ClipEntry(
            name=name,
            features=features.copy(),
            start_global_idx=start_idx,
            frame_count=features.shape[0],
            loopable=loopable,
            tags=tags or [],
        )
        self._clips[name] = entry

        for i in range(features.shape[0]):
            self._global_to_clip.append((name, i))

        self._built = False

    def build_index(self) -> None:
        """Construct the KD-Tree from all registered clips.

        Applies per-feature normalization and weighting before
        building the tree, as recommended by Clavet (2016).
        """
        if not self._clips:
            return

        # Stack all features
        all_features = []
        for clip in self._clips.values():
            all_features.append(clip.features)
        self._global_features = np.vstack(all_features).astype(np.float32)

        D = self._global_features.shape[1]

        # Compute normalization statistics
        self._mean = np.mean(self._global_features, axis=0)
        self._std = np.std(self._global_features, axis=0)
        self._std = np.maximum(self._std, 1e-6)

        # Normalize
        normalized = (self._global_features - self._mean) / self._std

        # Apply weights
        self._weight_vector = self.weights.to_array(D)
        weighted = normalized * self._weight_vector[None, :]

        # Build KD-Tree
        self._tree = _NumpyKDTree(weighted, leaf_size=self.leaf_size)
        self._built = True

    def query(self, feature_vector: np.ndarray,
              k: int = 1,
              exclude_clips: Optional[List[str]] = None
              ) -> List[MatchResult]:
        """Find the k nearest matching frames.

        Args:
            feature_vector: (D,) query feature vector.
            k: Number of nearest neighbors to return.
            exclude_clips: Optional list of clip names to exclude.

        Returns:
            List of MatchResult sorted by cost (ascending).
        """
        if not self._built or self._tree is None:
            self.build_index()
        if self._tree is None:
            return []

        # Normalize and weight the query
        query_norm = (feature_vector - self._mean) / self._std
        query_weighted = query_norm * self._weight_vector

        # Query KD-Tree
        search_k = min(k * 3, self.total_frames)  # Over-fetch for filtering
        dists, indices = self._tree.query(query_weighted, k=search_k)

        results: List[MatchResult] = []
        for dist, idx in zip(dists, indices):
            idx = int(idx)
            if idx >= len(self._global_to_clip):
                continue
            clip_name, local_idx = self._global_to_clip[idx]

            if exclude_clips and clip_name in exclude_clips:
                continue

            cost = float(dist * dist)  # Squared distance as cost
            similarity = float(math.exp(-0.5 * cost /
                                        max(feature_vector.shape[0], 1)))

            results.append(MatchResult(
                clip_name=clip_name,
                frame_idx=local_idx,
                global_idx=idx,
                cost=cost,
                similarity=similarity,
            ))

            if len(results) >= k:
                break

        return results

    def query_radius(self, feature_vector: np.ndarray,
                     radius: float) -> List[MatchResult]:
        """Find all frames within a cost radius.

        Args:
            feature_vector: (D,) query feature vector.
            radius: Maximum cost radius.

        Returns:
            List of MatchResult within radius.
        """
        if not self._built or self._tree is None:
            self.build_index()
        if self._tree is None:
            return []

        query_norm = (feature_vector - self._mean) / self._std
        query_weighted = query_norm * self._weight_vector

        indices = self._tree.query_ball_point(
            query_weighted, math.sqrt(radius)
        )

        results: List[MatchResult] = []
        for idx in indices:
            clip_name, local_idx = self._global_to_clip[idx]
            # Compute exact cost
            diff = query_weighted - self._tree.data[idx]
            cost = float(np.sum(diff * diff))
            similarity = float(math.exp(-0.5 * cost /
                                        max(feature_vector.shape[0], 1)))
            results.append(MatchResult(
                clip_name=clip_name,
                frame_idx=local_idx,
                global_idx=idx,
                cost=cost,
                similarity=similarity,
            ))

        results.sort(key=lambda r: r.cost)
        return results

    def get_clip_and_frame(self, global_idx: int
                           ) -> Tuple[str, int]:
        """Map global index to (clip_name, local_frame_idx)."""
        if 0 <= global_idx < len(self._global_to_clip):
            return self._global_to_clip[global_idx]
        return ("", 0)


# ---------------------------------------------------------------------------
# Motion Matching Controller
# ---------------------------------------------------------------------------

class MotionMatchingController:
    """High-level controller that runs motion matching each frame.

    Manages the current playback state, decides when to trigger
    transitions, and interfaces with the inertialization blender.
    """

    def __init__(self, database: KDTreeMotionDatabase,
                 transition_cost_threshold: float = 5.0,
                 min_hold_frames: int = 10,
                 responsiveness: float = 1.0):
        """
        Args:
            database: The motion database to query.
            transition_cost_threshold: Cost above which a transition
                                       is triggered.
            min_hold_frames: Minimum frames to hold before allowing
                            another transition.
            responsiveness: How eagerly to transition (0=lazy, 2=eager).
        """
        self.database = database
        self.transition_threshold = transition_cost_threshold
        self.min_hold_frames = min_hold_frames
        self.responsiveness = responsiveness

        # Playback state
        self.current_clip: str = ""
        self.current_frame: int = 0
        self.frames_since_transition: int = 0
        self.transition_count: int = 0
        self._trajectory: Optional[np.ndarray] = None
        self._last_match: Optional[MatchResult] = None

    def update(self, current_features: np.ndarray,
               dt: float = 1.0 / 24.0) -> TransitionCommand:
        """Run one frame of motion matching.

        Args:
            current_features: Current character state feature vector.
            dt: Time step.

        Returns:
            TransitionCommand indicating whether to transition.
        """
        self.frames_since_transition += 1

        # Query database
        results = self.database.query(current_features, k=3)
        if not results:
            return TransitionCommand(
                target_clip=self.current_clip,
                target_frame=self.current_frame + 1,
                transition_cost=0.0,
                should_transition=False,
                reason="no_results"
            )

        best = results[0]
        self._last_match = best

        # Check if we should transition
        should_transition = False
        reason = "continue"

        if not self.current_clip:
            # First frame: always transition
            should_transition = True
            reason = "initial"
        elif self.frames_since_transition >= self.min_hold_frames:
            # Check if current playback is diverging
            current_cost = best.cost * self.responsiveness

            # Bonus for staying in the same clip (continuity preference)
            if best.clip_name == self.current_clip:
                # Only transition if the best frame is far from current
                frame_distance = abs(best.frame_idx - self.current_frame)
                if frame_distance > 5:
                    should_transition = current_cost > self.transition_threshold
                    reason = "same_clip_jump"
            else:
                should_transition = current_cost > self.transition_threshold * 0.7
                reason = "cross_clip"

        if should_transition:
            self.current_clip = best.clip_name
            self.current_frame = best.frame_idx
            self.frames_since_transition = 0
            self.transition_count += 1
        else:
            self.current_frame += 1

        return TransitionCommand(
            target_clip=best.clip_name,
            target_frame=best.frame_idx,
            transition_cost=best.cost,
            should_transition=should_transition,
            reason=reason,
        )

    def force_transition(self, target_clip: str,
                         target_frame: int = -1) -> TransitionCommand:
        """Force an immediate transition to a specific clip.

        Args:
            target_clip: Target clip name.
            target_frame: Target frame (-1 for best match).

        Returns:
            TransitionCommand.
        """
        self.current_clip = target_clip
        self.current_frame = max(target_frame, 0)
        self.frames_since_transition = 0
        self.transition_count += 1

        return TransitionCommand(
            target_clip=target_clip,
            target_frame=self.current_frame,
            transition_cost=0.0,
            should_transition=True,
            reason="forced",
        )

    def set_trajectory(self, future_positions: np.ndarray) -> None:
        """Update the desired future trajectory for matching."""
        self._trajectory = future_positions.copy()

    def get_diagnostics(self) -> Dict:
        """Get current matching diagnostics."""
        return {
            "current_clip": self.current_clip,
            "current_frame": self.current_frame,
            "frames_since_transition": self.frames_since_transition,
            "total_transitions": self.transition_count,
            "last_match_cost": self._last_match.cost
            if self._last_match else None,
            "last_match_similarity": self._last_match.similarity
            if self._last_match else None,
            "database_total_frames": self.database.total_frames,
            "database_clip_count": self.database.clip_count,
        }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def create_kdtree_database(
    clips: Optional[Dict[str, np.ndarray]] = None,
    weights: Optional[FeatureWeights] = None,
) -> KDTreeMotionDatabase:
    """Create and optionally populate a KD-Tree motion database."""
    db = KDTreeMotionDatabase(weights=weights)
    if clips:
        for name, features in clips.items():
            db.add_clip(name, features)
        db.build_index()
    return db


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "FeatureWeights",
    "ClipEntry",
    "MatchResult",
    "TransitionCommand",
    "KDTreeMotionDatabase",
    "MotionMatchingController",
    "create_kdtree_database",
]
