"""
SESSION-061: Animation 12 Principles Quantification System

Research foundations:
  1. **Thomas & Johnston (1981) — The Illusion of Life**: The original
     codification of Disney's 12 Principles of Animation.  This module
     provides computational metrics for each principle so that procedural
     and neural-network-generated animations can be objectively scored.

  2. **Lasseter (1987) — Principles of Traditional Animation Applied to
     3D Computer Animation (SIGGRAPH '87)**: Extended the 12 principles
     to computer graphics.  Our metrics bridge the gap between artistic
     intuition and quantitative evaluation.

  3. **Thesen (2020) — Reviewing and Updating the 12 Principles of
     Animation**: Contemporary refinement of the principles for modern
     animation pipelines.  We incorporate the updated perspective on
     timing, spacing, and secondary action.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  PrincipleScorer                                                    │
    │  ├─ score_squash_stretch(frames) → volume preservation ratio        │
    │  ├─ score_anticipation(frames) → pre-action displacement metric     │
    │  ├─ score_follow_through(frames) → post-action overshoot decay      │
    │  ├─ score_arcs(frames) → trajectory curvature smoothness            │
    │  ├─ score_timing(frames) → frame spacing variance analysis          │
    │  ├─ score_slow_in_out(frames) → easing curve conformity             │
    │  ├─ score_exaggeration(frames) → amplitude scaling factor           │
    │  ├─ score_secondary_action(frames) → phase offset correlation       │
    │  ├─ score_staging(frames) → silhouette clarity metric               │
    │  ├─ score_solid_drawing(frames) → volume consistency                │
    │  ├─ score_appeal(frames) → proportion harmony score                 │
    │  └─ score_straight_ahead_vs_pose(frames) → interpolation quality    │
    ├──────────────────────────────────────────────────────────────────────┤
    │  PrincipleReport                                                    │
    │  ├─ per_principle scores (0.0 – 1.0)                                │
    │  ├─ aggregate_score → weighted mean                                 │
    │  └─ recommendations → list of improvement suggestions               │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.principles_quantifier import (
        PrincipleScorer, PrincipleReport, PrincipleWeights,
    )

    scorer = PrincipleScorer()
    report = scorer.score_clip(frames)
    print(report.aggregate_score)
    print(report.recommendations)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Mapping, Optional, Sequence

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PrincipleWeights:
    """Weights for each of the 12 principles in the aggregate score."""

    squash_stretch: float = 1.0
    anticipation: float = 1.0
    staging: float = 0.8
    straight_ahead_pose: float = 0.7
    follow_through: float = 1.0
    slow_in_out: float = 1.0
    arcs: float = 1.0
    secondary_action: float = 0.9
    timing: float = 1.0
    exaggeration: float = 0.8
    solid_drawing: float = 0.7
    appeal: float = 0.6


@dataclass
class PrincipleReport:
    """Report containing scores for each of the 12 principles."""

    squash_stretch: float = 0.0
    anticipation: float = 0.0
    staging: float = 0.0
    straight_ahead_pose: float = 0.0
    follow_through: float = 0.0
    slow_in_out: float = 0.0
    arcs: float = 0.0
    secondary_action: float = 0.0
    timing: float = 0.0
    exaggeration: float = 0.0
    solid_drawing: float = 0.0
    appeal: float = 0.0
    aggregate_score: float = 0.0
    recommendations: list[str] = field(default_factory=list)
    frame_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# Frame Representation
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AnimFrame:
    """A single animation frame with joint positions and scale data.

    This is a lightweight representation that can be constructed from
    UMR frames, projected 2D poses, or raw joint position dicts.
    """

    joint_positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    joint_scales: dict[str, tuple[float, float]] = field(default_factory=dict)
    root_position: tuple[float, float] = (0.0, 0.0)
    time: float = 0.0
    phase: float = 0.0


@dataclass
class CognitiveTraceFrame:
    """Continuous telemetry frame for cognition-aware motion scoring.

    This representation is intentionally trace-centric rather than pose-centric.
    It is consumed by the P1-DISTILL-4 distillation loop where objective
    scoring must use real temporal trajectories exported by the backend
    sidecar instead of single-frame snapshots.
    """

    frame_index: int = 0
    time: float = 0.0
    phase: float = 0.0
    phase_kind: str = "cyclic"
    root_position: tuple[float, float] = (0.0, 0.0)
    root_velocity: tuple[float, float] = (0.0, 0.0)
    root_speed: float = 0.0
    root_acceleration: float = 0.0
    root_jerk: float = 0.0
    extremity_motion_energy: float = 0.0
    joint_angular_velocity: dict[str, float] = field(default_factory=dict)
    contact_expectation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CognitiveTraceFrame":
        root_pos = payload.get("root_position", {})
        root_vel = payload.get("root_velocity", {})
        return cls(
            frame_index=int(payload.get("frame_index", 0)),
            time=float(payload.get("time", 0.0)),
            phase=float(payload.get("phase", 0.0)),
            phase_kind=str(payload.get("phase_kind", "cyclic")),
            root_position=(
                float(root_pos.get("x", 0.0)),
                float(root_pos.get("y", 0.0)),
            ),
            root_velocity=(
                float(root_vel.get("x", 0.0)),
                float(root_vel.get("y", 0.0)),
            ),
            root_speed=float(payload.get("root_speed", 0.0)),
            root_acceleration=float(payload.get("root_acceleration", 0.0)),
            root_jerk=float(payload.get("root_jerk", 0.0)),
            extremity_motion_energy=float(payload.get("extremity_motion_energy", 0.0)),
            joint_angular_velocity={
                str(k): float(v)
                for k, v in dict(payload.get("joint_angular_velocity", {})).items()
            },
            contact_expectation=dict(payload.get("contact_expectation", {})),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Principle Scorer
# ═══════════════════════════════════════════════════════════════════════════════

class PrincipleScorer:
    """Quantitative scorer for Disney's 12 Principles of Animation.

    Each principle is evaluated on a [0, 1] scale where 1.0 represents
    ideal adherence.  The scorer analyses sequences of ``AnimFrame``
    objects and produces a ``PrincipleReport``.
    """

    def __init__(self, weights: Optional[PrincipleWeights] = None) -> None:
        self.weights = weights or PrincipleWeights()

    # ── 1. Squash & Stretch ─────────────────────────────────────────────

    def score_squash_stretch(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate volume preservation during squash and stretch.

        A perfect score means that when scale_x increases, scale_y
        decreases proportionally (and vice versa), preserving area.
        Score = 1 - mean |sx*sy - 1.0| across all joints and frames.
        """
        if not frames:
            return 0.0
        deviations: list[float] = []
        for frame in frames:
            for jname, (sx, sy) in frame.joint_scales.items():
                area = sx * sy
                deviations.append(abs(area - 1.0))
        if not deviations:
            return 1.0
        return max(0.0, 1.0 - float(np.mean(deviations)))

    # ── 2. Anticipation ─────────────────────────────────────────────────

    def score_anticipation(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate presence of anticipatory motion before main action.

        Looks for a brief reversal in velocity direction before the
        peak velocity frame.  Score is based on the ratio of
        anticipation magnitude to main action magnitude.
        """
        if len(frames) < 3:
            return 0.0

        velocities: list[float] = []
        for i in range(1, len(frames)):
            dx = frames[i].root_position[0] - frames[i - 1].root_position[0]
            dy = frames[i].root_position[1] - frames[i - 1].root_position[1]
            velocities.append(math.sqrt(dx * dx + dy * dy))

        if not velocities or max(velocities) < 1e-6:
            return 0.5

        peak_idx = int(np.argmax(velocities))
        if peak_idx < 1:
            return 0.3

        # Check for velocity dip before peak (anticipation)
        pre_peak = velocities[:peak_idx]
        if not pre_peak:
            return 0.3

        min_pre = min(pre_peak)
        max_vel = max(velocities)
        anticipation_ratio = 1.0 - (min_pre / max_vel) if max_vel > 1e-6 else 0.0
        return min(1.0, anticipation_ratio)

    # ── 3. Staging ──────────────────────────────────────────────────────

    def score_staging(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate staging clarity via silhouette spread.

        A well-staged pose has joints spread across the frame rather
        than bunched together.  Score is based on the normalised
        bounding box area of joint positions.
        """
        if not frames:
            return 0.0

        spreads: list[float] = []
        for frame in frames:
            if len(frame.joint_positions) < 2:
                continue
            xs = [p[0] for p in frame.joint_positions.values()]
            ys = [p[1] for p in frame.joint_positions.values()]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            spread = width * height
            spreads.append(spread)

        if not spreads:
            return 0.5

        mean_spread = float(np.mean(spreads))
        max_spread = max(spreads)
        if max_spread < 1e-6:
            return 0.5

        variance = float(np.std(spreads) / max_spread)
        return min(1.0, 0.5 + 0.5 * (1.0 - variance))

    # ── 4. Straight Ahead vs Pose to Pose ───────────────────────────────

    def score_straight_ahead_pose(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate interpolation quality between key poses.

        Measures smoothness of joint trajectories.  Jerky motion
        (high second derivative) scores lower.
        """
        if len(frames) < 3:
            return 0.5

        jerks: list[float] = []
        for jname in frames[0].joint_positions:
            positions = []
            for f in frames:
                if jname in f.joint_positions:
                    positions.append(f.joint_positions[jname])
            if len(positions) < 3:
                continue
            for i in range(2, len(positions)):
                ax = positions[i][0] - 2 * positions[i - 1][0] + positions[i - 2][0]
                ay = positions[i][1] - 2 * positions[i - 1][1] + positions[i - 2][1]
                jerks.append(math.sqrt(ax * ax + ay * ay))

        if not jerks:
            return 0.5

        mean_jerk = float(np.mean(jerks))
        return max(0.0, 1.0 - min(mean_jerk / 0.1, 1.0))

    # ── 5. Follow-Through & Overlapping Action ──────────────────────────

    def score_follow_through(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate follow-through: do extremities continue moving
        after the main body stops?

        Measures the ratio of extremity motion after peak body velocity.
        """
        if len(frames) < 3:
            return 0.0

        body_vels: list[float] = []
        for i in range(1, len(frames)):
            dx = frames[i].root_position[0] - frames[i - 1].root_position[0]
            dy = frames[i].root_position[1] - frames[i - 1].root_position[1]
            body_vels.append(math.sqrt(dx * dx + dy * dy))

        if not body_vels or max(body_vels) < 1e-6:
            return 0.5

        peak_idx = int(np.argmax(body_vels))
        post_peak = len(frames) - peak_idx - 1
        if post_peak < 2:
            return 0.3

        # Check if extremities still move after body peak
        extremity_names = [n for n in frames[0].joint_positions if "hand" in n or "foot" in n or "arm" in n]
        if not extremity_names:
            extremity_names = list(frames[0].joint_positions.keys())[:3]

        post_motion = 0.0
        for jname in extremity_names:
            for i in range(peak_idx + 1, min(peak_idx + post_peak, len(frames))):
                if jname in frames[i].joint_positions and jname in frames[i - 1].joint_positions:
                    dx = frames[i].joint_positions[jname][0] - frames[i - 1].joint_positions[jname][0]
                    dy = frames[i].joint_positions[jname][1] - frames[i - 1].joint_positions[jname][1]
                    post_motion += math.sqrt(dx * dx + dy * dy)

        return min(1.0, post_motion / (max(body_vels) * len(extremity_names) + 1e-6))

    # ── 6. Slow In / Slow Out ───────────────────────────────────────────

    def score_slow_in_out(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate ease-in / ease-out (slow in / slow out).

        Checks that velocity is lower at the start and end of the clip
        compared to the middle.  Score is based on the ratio of
        edge velocities to peak velocity.
        """
        if len(frames) < 5:
            return 0.5

        velocities: list[float] = []
        for i in range(1, len(frames)):
            dx = frames[i].root_position[0] - frames[i - 1].root_position[0]
            dy = frames[i].root_position[1] - frames[i - 1].root_position[1]
            velocities.append(math.sqrt(dx * dx + dy * dy))

        if not velocities or max(velocities) < 1e-6:
            return 0.5

        n = len(velocities)
        edge_count = max(1, n // 5)
        start_vel = float(np.mean(velocities[:edge_count]))
        end_vel = float(np.mean(velocities[-edge_count:]))
        mid_vel = float(np.mean(velocities[edge_count:-edge_count])) if n > 2 * edge_count else float(np.mean(velocities))

        if mid_vel < 1e-6:
            return 0.5

        ease_in = 1.0 - min(start_vel / mid_vel, 1.0)
        ease_out = 1.0 - min(end_vel / mid_vel, 1.0)
        return min(1.0, (ease_in + ease_out) / 2.0)

    # ── 7. Arcs ─────────────────────────────────────────────────────────

    def score_arcs(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate arc quality of joint trajectories.

        Natural motion follows smooth arcs rather than straight lines.
        Score is based on curvature smoothness of joint paths.
        """
        if len(frames) < 3:
            return 0.5

        curvature_variances: list[float] = []
        for jname in frames[0].joint_positions:
            positions = []
            for f in frames:
                if jname in f.joint_positions:
                    positions.append(f.joint_positions[jname])
            if len(positions) < 3:
                continue

            curvatures: list[float] = []
            for i in range(1, len(positions) - 1):
                x0, y0 = positions[i - 1]
                x1, y1 = positions[i]
                x2, y2 = positions[i + 1]

                # Menger curvature: 4 * triangle_area / (d01 * d12 * d02)
                area = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / 2.0
                d01 = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
                d12 = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                d02 = math.sqrt((x2 - x0) ** 2 + (y2 - y0) ** 2)
                denom = d01 * d12 * d02
                if denom > 1e-12:
                    curvatures.append(4.0 * area / denom)

            if len(curvatures) >= 2:
                curvature_variances.append(float(np.std(curvatures)))

        if not curvature_variances:
            return 0.5

        mean_var = float(np.mean(curvature_variances))
        return max(0.0, 1.0 - min(mean_var / 0.5, 1.0))

    # ── 8. Secondary Action ─────────────────────────────────────────────

    def score_secondary_action(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate secondary action via phase offset correlation.

        Secondary actions (e.g., arm swing during walk) should be
        correlated with but offset from the primary action.  Score
        measures cross-correlation between body and extremity motion.
        """
        if len(frames) < 5:
            return 0.5

        body_motion: list[float] = []
        extremity_motion: list[float] = []

        for i in range(1, len(frames)):
            dx = frames[i].root_position[0] - frames[i - 1].root_position[0]
            dy = frames[i].root_position[1] - frames[i - 1].root_position[1]
            body_motion.append(math.sqrt(dx * dx + dy * dy))

            ext_total = 0.0
            ext_count = 0
            for jname in frames[i].joint_positions:
                if jname in frames[i - 1].joint_positions:
                    jdx = frames[i].joint_positions[jname][0] - frames[i - 1].joint_positions[jname][0]
                    jdy = frames[i].joint_positions[jname][1] - frames[i - 1].joint_positions[jname][1]
                    ext_total += math.sqrt(jdx * jdx + jdy * jdy)
                    ext_count += 1
            extremity_motion.append(ext_total / max(ext_count, 1))

        if len(body_motion) < 3:
            return 0.5

        body_arr = np.array(body_motion)
        ext_arr = np.array(extremity_motion)

        if np.std(body_arr) < 1e-6 or np.std(ext_arr) < 1e-6:
            return 0.5

        correlation = float(np.corrcoef(body_arr, ext_arr)[0, 1])
        # Ideal: moderate positive correlation (0.3-0.7) indicating
        # related but distinct motion
        if abs(correlation) > 0.95:
            return 0.6  # Too correlated = no independence
        if abs(correlation) < 0.1:
            return 0.3  # Too uncorrelated = no relationship
        return min(1.0, 0.5 + abs(correlation))

    # ── 9. Timing ───────────────────────────────────────────────────────

    def score_timing(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate timing via frame spacing analysis.

        Good timing has intentional variation in spacing (not uniform).
        Score measures the coefficient of variation of inter-frame
        displacement.
        """
        if len(frames) < 3:
            return 0.5

        spacings: list[float] = []
        for i in range(1, len(frames)):
            dx = frames[i].root_position[0] - frames[i - 1].root_position[0]
            dy = frames[i].root_position[1] - frames[i - 1].root_position[1]
            spacings.append(math.sqrt(dx * dx + dy * dy))

        if not spacings or max(spacings) < 1e-6:
            return 0.5

        cv = float(np.std(spacings) / (np.mean(spacings) + 1e-6))
        # Some variation is good (0.2-0.8), too much or too little is bad
        if cv < 0.05:
            return 0.4  # Too uniform
        if cv > 2.0:
            return 0.3  # Too erratic
        return min(1.0, 0.5 + cv * 0.5)

    # ── 10. Exaggeration ────────────────────────────────────────────────

    def score_exaggeration(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate exaggeration via amplitude scaling.

        Measures whether scale deformations exceed neutral (1.0, 1.0)
        by a meaningful amount without being extreme.
        """
        if not frames:
            return 0.5

        max_deviations: list[float] = []
        for frame in frames:
            for jname, (sx, sy) in frame.joint_scales.items():
                dev = max(abs(sx - 1.0), abs(sy - 1.0))
                max_deviations.append(dev)

        if not max_deviations:
            return 0.5

        mean_dev = float(np.mean(max_deviations))
        max_dev = float(np.max(max_deviations))

        if max_dev < 0.01:
            return 0.3  # No exaggeration at all
        if max_dev > 1.0:
            return 0.4  # Over-exaggerated
        return min(1.0, 0.5 + mean_dev * 2.0)

    # ── 11. Solid Drawing ───────────────────────────────────────────────

    def score_solid_drawing(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate volume consistency across frames.

        Measures how stable joint-to-joint distances remain over time
        (bones should not stretch or compress erratically).
        """
        if len(frames) < 2:
            return 0.5

        bone_pairs: list[tuple[str, str]] = []
        joints = list(frames[0].joint_positions.keys())
        for i in range(len(joints)):
            for j in range(i + 1, min(i + 3, len(joints))):
                bone_pairs.append((joints[i], joints[j]))

        if not bone_pairs:
            return 0.5

        stability_scores: list[float] = []
        for j1, j2 in bone_pairs:
            distances: list[float] = []
            for frame in frames:
                if j1 in frame.joint_positions and j2 in frame.joint_positions:
                    p1 = frame.joint_positions[j1]
                    p2 = frame.joint_positions[j2]
                    d = math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
                    distances.append(d)
            if len(distances) >= 2:
                mean_d = float(np.mean(distances))
                if mean_d > 1e-6:
                    cv = float(np.std(distances) / mean_d)
                    stability_scores.append(max(0.0, 1.0 - cv * 5.0))

        if not stability_scores:
            return 0.5

        return float(np.mean(stability_scores))

    # ── 12. Appeal ──────────────────────────────────────────────────────

    def score_appeal(self, frames: Sequence[AnimFrame]) -> float:
        """Evaluate appeal via proportion harmony.

        Measures the golden ratio adherence of key body proportions
        and overall symmetry.
        """
        if not frames:
            return 0.5

        golden = 1.618
        harmony_scores: list[float] = []

        for frame in frames:
            positions = frame.joint_positions
            if "head" in positions and "hip" in positions and "l_foot" in positions:
                head = positions["head"]
                hip = positions["hip"]
                foot = positions["l_foot"]

                upper = math.sqrt((head[0] - hip[0]) ** 2 + (head[1] - hip[1]) ** 2)
                lower = math.sqrt((hip[0] - foot[0]) ** 2 + (hip[1] - foot[1]) ** 2)

                if lower > 1e-6:
                    ratio = upper / lower
                    deviation = abs(ratio - 1.0 / golden) / (1.0 / golden)
                    harmony_scores.append(max(0.0, 1.0 - deviation))

        if not harmony_scores:
            return 0.5

        return float(np.mean(harmony_scores))

    # ── Trace-Based Cognitive Telemetry Scoring ─────────────────────────

    def _coerce_trace_frames(
        self,
        traces: Sequence[CognitiveTraceFrame | Mapping[str, Any]],
    ) -> list[CognitiveTraceFrame]:
        frames: list[CognitiveTraceFrame] = []
        for trace in traces:
            if isinstance(trace, CognitiveTraceFrame):
                frames.append(trace)
            else:
                frames.append(CognitiveTraceFrame.from_mapping(trace))
        return frames

    @staticmethod
    def _wrapped_phase_delta(curr: float, prev: float, *, cyclic: bool) -> float:
        delta = float(curr) - float(prev)
        if not cyclic:
            return delta
        while delta > 0.5:
            delta -= 1.0
        while delta < -0.5:
            delta += 1.0
        return delta

    def score_trace_anticipation(
        self,
        traces: Sequence[CognitiveTraceFrame | Mapping[str, Any]],
        *,
        anticipation_bias: float = 0.12,
    ) -> float:
        """Score anticipation from continuous telemetry traces.

        The metric looks for a brief reverse or suppressed motion interval
        before the strongest forward impulse. The optimal anticipation amount
        is controlled by ``anticipation_bias`` rather than being hardcoded.
        """
        frames = self._coerce_trace_frames(traces)
        if len(frames) < 4:
            return 0.0

        velocities = np.array([f.root_velocity for f in frames], dtype=np.float64)
        speeds = np.array([f.root_speed for f in frames], dtype=np.float64)
        if speeds.size == 0 or float(np.max(speeds)) < 1e-8:
            return 0.5

        peak_idx = int(np.argmax(speeds))
        if peak_idx < 2:
            return 0.3

        lo = max(0, peak_idx - 1)
        hi = min(len(frames), peak_idx + 2)
        action_vec = np.mean(velocities[lo:hi], axis=0)
        norm = float(np.linalg.norm(action_vec))
        if norm < 1e-8:
            return 0.5
        action_dir = action_vec / norm

        signed_pre = velocities[:peak_idx] @ action_dir
        reverse_mag = max(0.0, float(-np.min(signed_pre))) if signed_pre.size else 0.0
        forward_peak = max(float(np.max(signed_pre)), float(np.max(speeds)), 1e-6)
        observed = reverse_mag / forward_peak
        target = max(0.01, float(anticipation_bias))
        ratio_error = abs(observed - target) / max(target, 1e-6)

        pre_window = speeds[:peak_idx]
        ramp_score = 0.5
        if pre_window.size >= 2:
            ramp = np.diff(pre_window)
            if ramp.size > 0:
                negative_then_positive = float(np.mean(ramp < 0.0) + np.mean(ramp > 0.0)) * 0.5
                ramp_score = min(1.0, negative_then_positive)

        bias_score = max(0.0, 1.0 - min(ratio_error, 1.0))
        return float(np.clip(0.75 * bias_score + 0.25 * ramp_score, 0.0, 1.0))

    def score_trace_follow_through(
        self,
        traces: Sequence[CognitiveTraceFrame | Mapping[str, Any]],
    ) -> float:
        """Score follow-through from trace-level extremity residual motion.

        Natural motion keeps extremities moving briefly after the root body
        begins to settle. The metric compares post-peak extremity motion to
        the root body's decaying speed profile.
        """
        frames = self._coerce_trace_frames(traces)
        if len(frames) < 4:
            return 0.0

        speeds = np.array([f.root_speed for f in frames], dtype=np.float64)
        ext = np.array([f.extremity_motion_energy for f in frames], dtype=np.float64)
        if speeds.size == 0 or float(np.max(speeds)) < 1e-8:
            return 0.5

        peak_idx = int(np.argmax(speeds))
        if peak_idx >= len(frames) - 2:
            return 0.3

        root_post = speeds[peak_idx + 1:]
        ext_post = ext[peak_idx + 1:]
        if root_post.size == 0 or ext_post.size == 0:
            return 0.3

        settle_threshold = max(float(np.max(speeds)) * 0.4, 1e-6)
        settling = root_post < settle_threshold
        if not settling.any():
            settling = np.ones_like(root_post, dtype=bool)

        residual = float(np.mean(ext_post[settling]))
        peak_ext = max(float(np.max(ext)), 1e-6)
        residual_ratio = residual / peak_ext
        return float(np.clip(residual_ratio / 0.35, 0.0, 1.0))

    def score_phase_manifold_consistency(
        self,
        traces: Sequence[CognitiveTraceFrame | Mapping[str, Any]],
        *,
        phase_salience: float = 1.0,
    ) -> float:
        """Approximate DeepPhase-style manifold smoothness from continuous traces.

        The trace is embedded into a compact multi-channel space using phase,
        speed, acceleration, contact activation, and extremity energy. Smooth
        first- and second-order evolution in this space yields a higher score.
        """
        frames = self._coerce_trace_frames(traces)
        if len(frames) < 4:
            return 0.5

        phase_salience = max(float(phase_salience), 1e-6)
        cyclic = all(f.phase_kind not in {"distance_to_apex", "distance_to_ground", "hit_recovery", "transient"} for f in frames)

        channel_rows: list[list[float]] = []
        for f in frames:
            left = 1.0 if bool(f.contact_expectation.get("left_foot")) else 0.0
            right = 1.0 if bool(f.contact_expectation.get("right_foot")) else 0.0
            angle = 2.0 * math.pi * float(f.phase)
            channel_rows.append([
                math.sin(angle) * phase_salience,
                math.cos(angle) * phase_salience,
                float(f.root_speed),
                float(f.root_acceleration),
                float(f.extremity_motion_energy),
                left,
                right,
            ])
        channels = np.array(channel_rows, dtype=np.float64)
        if channels.shape[0] < 4:
            return 0.5

        deltas = np.diff(channels, axis=0)
        second = np.diff(deltas, axis=0)
        if deltas.size == 0:
            return 0.5

        phase_deltas = [
            self._wrapped_phase_delta(frames[i].phase, frames[i - 1].phase, cyclic=cyclic)
            for i in range(1, len(frames))
        ]
        phase_jump_penalty = float(np.mean(np.abs(phase_deltas)))
        delta_energy = float(np.mean(np.linalg.norm(deltas, axis=1)))
        curvature = float(np.mean(np.linalg.norm(second, axis=1))) if second.size else 0.0
        direction_penalty = 0.0
        if phase_deltas:
            mean_phase_delta = float(np.mean(phase_deltas))
            sign_flips = float(np.mean(np.sign(phase_deltas[1:]) != np.sign(phase_deltas[:-1]))) if len(phase_deltas) > 1 else 0.0
            if mean_phase_delta <= 0.0:
                direction_penalty += abs(mean_phase_delta) + 0.25
            direction_penalty += 0.20 * sign_flips

        penalty = curvature + 0.25 * phase_jump_penalty + 0.1 * delta_energy + direction_penalty
        return float(np.clip(math.exp(-penalty), 0.0, 1.0))

    def score_perceptual_naturalness(
        self,
        traces: Sequence[CognitiveTraceFrame | Mapping[str, Any]],
        *,
        jerk_tolerance: float = 0.08,
        contact_expectation_weight: float = 1.0,
    ) -> float:
        """Score biological-motion naturalness from real continuous traces.

        The score combines jerk smoothness, centre-of-mass micro-motion
        continuity, and contact expectation stability. All components are based
        on traversing the full trace sequence rather than single-frame deltas.
        """
        frames = self._coerce_trace_frames(traces)
        if len(frames) < 4:
            return 0.5

        jerk_tolerance = max(float(jerk_tolerance), 1e-6)
        jerks = np.array([abs(f.root_jerk) for f in frames], dtype=np.float64)
        jerk_score = float(np.exp(-float(np.mean(jerks)) / jerk_tolerance))

        ys = np.array([f.root_position[1] for f in frames], dtype=np.float64)
        micro_motion = np.diff(ys, n=2) if ys.size >= 3 else np.zeros(0, dtype=np.float64)
        micro_score = float(np.exp(-float(np.mean(np.abs(micro_motion))) / max(jerk_tolerance, 1e-6))) if micro_motion.size else 0.5

        contact_vectors = np.array([
            [
                1.0 if bool(f.contact_expectation.get("left_foot")) else 0.0,
                1.0 if bool(f.contact_expectation.get("right_foot")) else 0.0,
            ]
            for f in frames
        ], dtype=np.float64)
        transitions = np.abs(np.diff(contact_vectors, axis=0)) if len(contact_vectors) >= 2 else np.zeros((0, 2), dtype=np.float64)
        contact_penalty = float(np.mean(transitions)) if transitions.size else 0.0
        contact_score = float(np.exp(-contact_penalty * max(contact_expectation_weight, 0.1)))

        return float(np.clip(0.45 * jerk_score + 0.25 * micro_score + 0.30 * contact_score, 0.0, 1.0))

    # ── Aggregate Scoring ───────────────────────────────────────────────

    def score_clip(self, frames: Sequence[AnimFrame]) -> PrincipleReport:
        """Score an animation clip against all 12 principles."""
        report = PrincipleReport(frame_count=len(frames))

        report.squash_stretch = self.score_squash_stretch(frames)
        report.anticipation = self.score_anticipation(frames)
        report.staging = self.score_staging(frames)
        report.straight_ahead_pose = self.score_straight_ahead_pose(frames)
        report.follow_through = self.score_follow_through(frames)
        report.slow_in_out = self.score_slow_in_out(frames)
        report.arcs = self.score_arcs(frames)
        report.secondary_action = self.score_secondary_action(frames)
        report.timing = self.score_timing(frames)
        report.exaggeration = self.score_exaggeration(frames)
        report.solid_drawing = self.score_solid_drawing(frames)
        report.appeal = self.score_appeal(frames)

        # Weighted aggregate
        w = self.weights
        scores = [
            (report.squash_stretch, w.squash_stretch),
            (report.anticipation, w.anticipation),
            (report.staging, w.staging),
            (report.straight_ahead_pose, w.straight_ahead_pose),
            (report.follow_through, w.follow_through),
            (report.slow_in_out, w.slow_in_out),
            (report.arcs, w.arcs),
            (report.secondary_action, w.secondary_action),
            (report.timing, w.timing),
            (report.exaggeration, w.exaggeration),
            (report.solid_drawing, w.solid_drawing),
            (report.appeal, w.appeal),
        ]
        total_weight = sum(wt for _, wt in scores)
        if total_weight > 0:
            report.aggregate_score = sum(s * wt for s, wt in scores) / total_weight

        # Generate recommendations
        threshold = 0.5
        principle_names = [
            ("squash_stretch", "Squash & Stretch"),
            ("anticipation", "Anticipation"),
            ("staging", "Staging"),
            ("straight_ahead_pose", "Straight Ahead / Pose to Pose"),
            ("follow_through", "Follow-Through & Overlapping Action"),
            ("slow_in_out", "Slow In / Slow Out"),
            ("arcs", "Arcs"),
            ("secondary_action", "Secondary Action"),
            ("timing", "Timing"),
            ("exaggeration", "Exaggeration"),
            ("solid_drawing", "Solid Drawing"),
            ("appeal", "Appeal"),
        ]
        for attr, name in principle_names:
            score = getattr(report, attr)
            if score < threshold:
                report.recommendations.append(
                    f"Improve {name} (score: {score:.2f}): "
                    f"consider reviewing reference animations for this principle."
                )

        return report


__all__ = [
    "PrincipleWeights",
    "PrincipleReport",
    "AnimFrame",
    "CognitiveTraceFrame",
    "PrincipleScorer",
]
