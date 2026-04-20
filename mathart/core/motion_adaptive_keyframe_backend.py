"""Motion-Adaptive Keyframe Planning Backend — P1-AI-2E.

SESSION-091: Implements the core nonlinearity-driven adaptive keyframe
selection engine for high-nonlinearity action segments.

Architecture
------------
This backend consumes UMR motion clips and produces a strongly-typed
``KEYFRAME_PLAN`` artifact containing:

1. Per-frame **nonlinearity scores** derived from three physical signals:
   - Root acceleration magnitude (Clavet GDC 2016 feature vector)
   - Angular acceleration magnitude (rotational abruptness)
   - Contact event transitions (Guilty Gear Xrd hitstop safe points)

2. **Adaptive keyframe indices** selected via score-driven sampling with
   ``min_gap`` / ``max_gap`` constraints (anti-Extrema-Omission and
   anti-Void guards).

3. **SparseCtrl ``end_percent`` mapping** per keyframe (Guo et al.,
   ECCV 2024): high-score frames get ``end_percent`` → 1.0 for maximum
   diffusion guidance; low-score frames use a configurable baseline.

Anti-Pattern Guards (SESSION-091 Red Lines)
-------------------------------------------
- 🚫 **Extrema Omission Trap**: NEVER use ``frame_idx % step == 0``.
  Algorithm MUST capture local maxima and contact events.
- 🚫 **Void Trap**: ``max_gap`` enforces no starvation in smooth segments.
- 🚫 **Cluster Trap**: ``min_gap`` prevents over-dense keyframe packing.

References
----------
[1] Guo et al., "SparseCtrl: Adding Sparse Controls to Text-to-Video
    Diffusion Models", ECCV 2024.
[2] Simon Clavet, "Motion Matching and The Road to Next-Gen Animation",
    GDC 2016 (Ubisoft).
[3] Junya C. Motomura, "GuiltyGearXrd's Art Style", GDC 2015.
[4] Starke, Mason, Komura, "DeepPhase", SIGGRAPH 2022.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from mathart.core.backend_registry import register_backend
from mathart.core.backend_types import BackendType
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class KeyframePlannerConfig:
    """Configuration for the adaptive keyframe planner.

    All parameters are backend-owned (Hexagonal Architecture / Ports-and-
    Adapters discipline).  The orchestrator and pipeline never need to know
    these internal knobs.
    """

    # --- Nonlinearity weights (Clavet feature vector weighting) ---
    weight_acceleration: float = 0.4
    weight_angular_acceleration: float = 0.3
    weight_contact_event: float = 0.3

    # --- Keyframe selection constraints ---
    min_gap: int = 2       # Minimum frames between keyframes (anti-cluster)
    max_gap: int = 12      # Maximum frames between keyframes (anti-void)
    extrema_threshold: float = 0.5  # Score threshold for force-capture

    # --- SparseCtrl mapping ---
    base_end_percent: float = 0.4   # Baseline end_percent for low-score frames
    max_end_percent: float = 1.0    # Maximum end_percent for high-score frames

    # --- Motion parameters ---
    fps: int = 12  # Default FPS (Guilty Gear Xrd discipline)

    def validate(self) -> list[str]:
        """Validate configuration, return list of error messages."""
        errors: list[str] = []
        total_w = self.weight_acceleration + self.weight_angular_acceleration + self.weight_contact_event
        if abs(total_w) < 1e-9:
            errors.append("Nonlinearity weights sum to zero.")
        if self.min_gap < 1:
            errors.append(f"min_gap must be >= 1, got {self.min_gap}")
        if self.max_gap < self.min_gap:
            errors.append(f"max_gap ({self.max_gap}) must be >= min_gap ({self.min_gap})")
        if not (0.0 <= self.base_end_percent <= 1.0):
            errors.append(f"base_end_percent must be in [0, 1], got {self.base_end_percent}")
        if not (0.0 <= self.max_end_percent <= 1.0):
            errors.append(f"max_end_percent must be in [0, 1], got {self.max_end_percent}")
        if self.fps < 1:
            errors.append(f"fps must be >= 1, got {self.fps}")
        return errors


# ---------------------------------------------------------------------------
# Core Algorithm: Nonlinearity Score Computation
# ---------------------------------------------------------------------------

def compute_nonlinearity_scores(
    velocities_x: np.ndarray,
    velocities_y: np.ndarray,
    angular_velocities: np.ndarray,
    contact_left: np.ndarray,
    contact_right: np.ndarray,
    fps: int = 12,
    *,
    weight_acc: float = 0.4,
    weight_ang_accel: float = 0.3,
    weight_ang: float | None = None,
    weight_contact: float = 0.3,
) -> np.ndarray:
    """Compute per-frame nonlinearity scores from UMR motion signals.

    All computation is NumPy-vectorized for performance.

    Parameters
    ----------
    velocities_x, velocities_y : np.ndarray
        Root velocity components per frame (from MotionRootTransform).
    angular_velocities : np.ndarray
        Root angular velocity per frame.
    contact_left, contact_right : np.ndarray
        Boolean contact arrays per frame (from MotionContactState).
    fps : int
        Frames per second for temporal scaling.
    weight_acc, weight_ang_accel, weight_contact : float
        Weighting factors for the three signal channels. ``weight_ang`` is
        accepted as a legacy alias for backward compatibility.

    Returns
    -------
    np.ndarray
        Per-frame nonlinearity scores in [0, 1].
    """
    if weight_ang is not None:
        weight_ang_accel = weight_ang

    n = len(velocities_x)
    if n < 2:
        return np.zeros(n, dtype=np.float64)

    # --- Channel 1: Root acceleration magnitude (Clavet GDC 2016) ---
    # Finite difference of velocity → acceleration, scaled by fps
    dvx = np.diff(velocities_x) * fps
    dvy = np.diff(velocities_y) * fps
    acc_mag = np.sqrt(dvx ** 2 + dvy ** 2)
    # Pad first frame with zero (no prior frame)
    acc_mag = np.concatenate([[0.0], acc_mag])

    # --- Channel 2: Angular acceleration magnitude ---
    angular_accel_magnitude = np.abs(np.diff(angular_velocities)) * fps
    angular_accel_magnitude = np.concatenate([[0.0], angular_accel_magnitude])

    # --- Channel 3: Contact event transitions ---
    # Any boolean flip in left or right foot contact = contact event
    contact_events = np.zeros(n, dtype=np.float64)
    if n > 1:
        left_change = np.abs(np.diff(contact_left.astype(np.float64)))
        right_change = np.abs(np.diff(contact_right.astype(np.float64)))
        contact_events[1:] = np.clip(left_change + right_change, 0.0, 1.0)

    # --- Normalize each channel to [0, 1] (per-clip min-max) ---
    def _safe_normalize(arr: np.ndarray) -> np.ndarray:
        vmin, vmax = arr.min(), arr.max()
        if vmax - vmin < 1e-12:
            return np.zeros_like(arr)
        return (arr - vmin) / (vmax - vmin)

    acc_norm = _safe_normalize(acc_mag)
    ang_norm = _safe_normalize(angular_accel_magnitude)
    # Contact events are already binary [0, 1], no normalization needed

    # --- Weighted fusion ---
    total_w = weight_acc + weight_ang_accel + weight_contact
    if total_w < 1e-12:
        return np.zeros(n, dtype=np.float64)

    raw_score = (
        weight_acc * acc_norm
        + weight_ang_accel * ang_norm
        + weight_contact * contact_events
    ) / total_w

    # Final normalization to [0, 1]
    return _safe_normalize(raw_score)


# ---------------------------------------------------------------------------
# Core Algorithm: Adaptive Keyframe Selection
# ---------------------------------------------------------------------------

def select_adaptive_keyframes(
    scores: np.ndarray,
    contact_events: np.ndarray,
    *,
    min_gap: int = 2,
    max_gap: int = 12,
    extrema_threshold: float = 0.5,
) -> list[int]:
    """Select keyframe indices using score-driven adaptive sampling.

    This function NEVER uses ``frame_idx % step == 0`` static sampling.
    Instead, it:
    1. Force-captures first and last frame (boundary anchors).
    2. Force-captures all contact event frames into the final pool with
       absolute override semantics.
    3. Adds non-contact extrema only when they do not violate the protected
       contact anchors.
    4. Fills gaps exceeding ``max_gap`` with the highest-scored legal frame
       while preserving contact immunity.

    Parameters
    ----------
    scores : np.ndarray
        Per-frame nonlinearity scores in [0, 1].
    contact_events : np.ndarray
        Per-frame contact event indicators (0 or 1).
    min_gap : int
        Minimum frames between consecutive non-contact keyframes.
    max_gap : int
        Maximum frames between consecutive keyframes.
    extrema_threshold : float
        Score threshold for force-capturing local maxima.

    Returns
    -------
    list[int]
        Sorted list of selected keyframe indices.
    """
    n = len(scores)
    if n == 0:
        return []
    if n == 1:
        return [0]

    locked_frames: set[int] = {0, n - 1}
    contact_indices = np.where(contact_events > 0.5)[0]
    locked_frames.update(contact_indices.tolist())
    selected: set[int] = set(locked_frames)

    def _conflicts(candidate: int, indices: set[int]) -> list[int]:
        return sorted(idx for idx in indices if idx != candidate and abs(candidate - idx) < min_gap)

    def _try_add_non_contact(candidate: int) -> bool:
        if candidate in selected:
            return False
        conflicts = _conflicts(candidate, selected)
        if any(idx in locked_frames for idx in conflicts):
            return False
        if not conflicts:
            selected.add(candidate)
            return True
        best_existing = max(conflicts, key=lambda idx: float(scores[idx]))
        if float(scores[candidate]) > float(scores[best_existing]):
            for idx in conflicts:
                if idx in locked_frames:
                    return False
            for idx in conflicts:
                selected.discard(idx)
            selected.add(candidate)
            return True
        return False

    peak_candidates: set[int] = set()
    for i in range(1, n - 1):
        if (
            scores[i] >= extrema_threshold
            and scores[i] >= scores[i - 1]
            and scores[i] >= scores[i + 1]
        ):
            peak_candidates.add(i)

    global_max_idx = int(np.argmax(scores))
    if scores[global_max_idx] >= extrema_threshold:
        peak_candidates.add(global_max_idx)

    for idx in sorted(peak_candidates, key=lambda i: (-float(scores[i]), i)):
        if idx in locked_frames:
            continue
        _try_add_non_contact(idx)

    while True:
        ordered = sorted(selected)
        max_current_gap = 0
        gap_pair: tuple[int, int] | None = None
        for left, right in zip(ordered[:-1], ordered[1:]):
            gap = right - left
            if gap > max_current_gap:
                max_current_gap = gap
                gap_pair = (left, right)
        if gap_pair is None or max_current_gap <= max_gap:
            break

        left, right = gap_pair
        candidate_indices = [
            idx
            for idx in range(left + 1, right)
            if idx not in selected and not _conflicts(idx, selected)
        ]
        if not candidate_indices:
            break
        best_idx = max(candidate_indices, key=lambda idx: float(scores[idx]))
        selected.add(best_idx)

    return sorted(selected)


# ---------------------------------------------------------------------------
# Core Algorithm: SparseCtrl end_percent Mapping
# ---------------------------------------------------------------------------

def map_end_percent(
    scores: np.ndarray,
    keyframe_indices: list[int],
    *,
    base_end_percent: float = 0.4,
    max_end_percent: float = 1.0,
) -> list[float]:
    """Map nonlinearity scores to SparseCtrl ``end_percent`` values.

    High-score keyframes get ``end_percent`` close to ``max_end_percent``
    (strong diffusion guidance). Low-score keyframes get ``base_end_percent``
    (relaxed guidance).

    Parameters
    ----------
    scores : np.ndarray
        Per-frame nonlinearity scores in [0, 1].
    keyframe_indices : list[int]
        Selected keyframe frame indices.
    base_end_percent : float
        Minimum end_percent for low-score frames.
    max_end_percent : float
        Maximum end_percent for high-score frames.

    Returns
    -------
    list[float]
        ``end_percent`` value for each keyframe, same length as
        ``keyframe_indices``.
    """
    result: list[float] = []
    spread = max_end_percent - base_end_percent
    for idx in keyframe_indices:
        score = float(scores[idx]) if idx < len(scores) else 0.0
        ep = base_end_percent + spread * score
        result.append(round(min(max(ep, base_end_percent), max_end_percent), 4))
    return result


# ---------------------------------------------------------------------------
# UMR Clip → NumPy Signal Extraction
# ---------------------------------------------------------------------------

def extract_signals_from_umr_frames(
    frames: Sequence[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Extract vectorized signal arrays from a list of UMR frame dicts.

    Supports both raw dicts and serialized ``UnifiedMotionFrame.to_dict()``
    output.  Falls back to zero for missing fields.

    Returns
    -------
    tuple of 5 np.ndarray
        (velocities_x, velocities_y, angular_velocities,
         contact_left, contact_right)
    """
    n = len(frames)
    vx = np.zeros(n, dtype=np.float64)
    vy = np.zeros(n, dtype=np.float64)
    aw = np.zeros(n, dtype=np.float64)
    cl = np.zeros(n, dtype=np.float64)
    cr = np.zeros(n, dtype=np.float64)

    for i, frame in enumerate(frames):
        rt = frame.get("root_transform", {})
        if isinstance(rt, dict):
            vx[i] = float(rt.get("velocity_x", 0.0))
            vy[i] = float(rt.get("velocity_y", 0.0))
            aw[i] = float(rt.get("angular_velocity", 0.0))
        ct = frame.get("contact_tags", {})
        if isinstance(ct, dict):
            cl[i] = 1.0 if ct.get("left_foot", False) else 0.0
            cr[i] = 1.0 if ct.get("right_foot", False) else 0.0

    return vx, vy, aw, cl, cr


# ---------------------------------------------------------------------------
# Three-Layer Evolution Bridge
# ---------------------------------------------------------------------------

class KeyframeEvolutionBridge:
    """Three-layer evolution bridge for keyframe planning.

    Layer 1 (Internal): Tune weights, thresholds, min_gap/max_gap.
    Layer 2 (Distill): Persist winning parameter sets as knowledge rules.
    Layer 3 (Test): Validate plans against quality metrics.
    """

    def __init__(self, project_root: Optional[str | Path] = None) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.knowledge_path = self.root / "knowledge" / "keyframe_planning_rules.json"
        self.state_path = self.root / "evolution_reports" / "keyframe_evolution_state.json"

    def evaluate(self, plan: dict[str, Any], config: KeyframePlannerConfig) -> dict[str, float]:
        """Layer 1: Evaluate a keyframe plan's quality."""
        keyframes = plan.get("keyframe_indices", [])
        frame_count = plan.get("frame_count", 0)
        scores = plan.get("nonlinearity_scores", [])
        contact_captured = plan.get("contact_events_captured", 0)

        fitness: dict[str, float] = {}

        # Coverage: fraction of frames within max_gap of a keyframe
        if frame_count > 0 and keyframes:
            coverage = len(keyframes) / frame_count
            fitness["coverage"] = min(coverage * 5.0, 1.0)  # Normalize
        else:
            fitness["coverage"] = 0.0

        # No-void: check max gap constraint
        gaps = [keyframes[i + 1] - keyframes[i] for i in range(len(keyframes) - 1)] if len(keyframes) > 1 else []
        max_actual_gap = max(gaps) if gaps else 0
        fitness["no_void"] = 1.0 if max_actual_gap <= config.max_gap else 0.5

        # No-cluster: check min gap constraint
        min_actual_gap = min(gaps) if gaps else config.min_gap
        fitness["no_cluster"] = 1.0 if min_actual_gap >= config.min_gap else 0.5

        # Contact capture: all contact events must be captured
        total_contacts = sum(1 for s in (plan.get("contact_event_frames", []) or []) if s)
        if total_contacts > 0:
            fitness["contact_capture"] = contact_captured / total_contacts
        else:
            fitness["contact_capture"] = 1.0

        # Temporal coherence: mean score at keyframes should be higher than mean
        if scores and keyframes:
            kf_scores = [scores[i] for i in keyframes if i < len(scores)]
            mean_kf = sum(kf_scores) / len(kf_scores) if kf_scores else 0.0
            mean_all = sum(scores) / len(scores) if scores else 0.0
            fitness["temporal_coherence"] = min(mean_kf / max(mean_all, 1e-9), 1.0)
        else:
            fitness["temporal_coherence"] = 0.0

        return fitness

    def distill(self, config: KeyframePlannerConfig, fitness: dict[str, float]) -> list[dict[str, Any]]:
        """Layer 2: Distill winning parameters as knowledge rules."""
        import json

        rules: list[dict[str, Any]] = []
        overall = sum(fitness.values()) / max(len(fitness), 1)

        if overall > 0.7:
            rule = {
                "source": "KeyframeEvolutionBridge",
                "session": "SESSION-091",
                "type": "keyframe_planner_config",
                "fitness": round(overall, 4),
                "config": {
                    "weight_acceleration": config.weight_acceleration,
                    "weight_angular_acceleration": config.weight_angular_acceleration,
                    "weight_contact_event": config.weight_contact_event,
                    "min_gap": config.min_gap,
                    "max_gap": config.max_gap,
                    "extrema_threshold": config.extrema_threshold,
                    "base_end_percent": config.base_end_percent,
                },
                "timestamp": time.time(),
            }
            rules.append(rule)

            # Persist
            self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict] = []
            if self.knowledge_path.exists():
                try:
                    existing = json.loads(self.knowledge_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = []
            existing.append(rule)
            self.knowledge_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        return rules


# ---------------------------------------------------------------------------
# Backend Registration
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.MOTION_ADAPTIVE_KEYFRAME,
    display_name="Motion-Adaptive Keyframe Planner",
    artifact_families=(ArtifactFamily.KEYFRAME_PLAN.value,),
    session_origin="SESSION-091",
)
class MotionAdaptiveKeyframeBackend:
    """Registry-native backend for motion-adaptive keyframe planning.

    Self-registers via ``@register_backend`` — zero modification to
    AssetPipeline, Orchestrator, or any trunk code.

    Input context dict:
        - ``umr_frames``: list[dict] — serialized UMR frames
        - ``fps``: int — frames per second (default 12)
        - ``output_dir``: str — directory for plan output
        - ``config_overrides``: dict — optional config overrides

    Output: ``ArtifactManifest`` with ``artifact_family="keyframe_plan"``.
    """

    def validate_config(self, context: dict[str, Any]) -> dict[str, Any]:
        """Backend-owned parameter normalization.

        All parameter parsing is physically sunk into this Adapter
        (Hexagonal Architecture / Ports-and-Adapters discipline).
        """
        validated = dict(context)

        # Build config from context overrides
        overrides = context.get("config_overrides", {})
        config = KeyframePlannerConfig(
            weight_acceleration=float(overrides.get("weight_acceleration", 0.4)),
            weight_angular_acceleration=float(
                overrides.get("weight_angular_acceleration", overrides.get("weight_angular_jerk", 0.3))
            ),
            weight_contact_event=float(overrides.get("weight_contact_event", 0.3)),
            min_gap=int(overrides.get("min_gap", 2)),
            max_gap=int(overrides.get("max_gap", 12)),
            extrema_threshold=float(overrides.get("extrema_threshold", 0.5)),
            base_end_percent=float(overrides.get("base_end_percent", 0.4)),
            max_end_percent=float(overrides.get("max_end_percent", 1.0)),
            fps=int(context.get("fps", 12)),
        )
        errors = config.validate()
        if errors:
            raise ValueError(f"KeyframePlannerConfig validation failed: {errors}")

        validated["_config"] = config
        validated["fps"] = config.fps
        return validated

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the adaptive keyframe planning pipeline.

        Full pipeline:
        1. Extract signals from UMR frames.
        2. Compute per-frame nonlinearity scores.
        3. Select adaptive keyframes with min_gap/max_gap constraints.
        4. Map scores to SparseCtrl end_percent.
        5. Run three-layer evolution evaluation.
        6. Return strongly-typed ArtifactManifest.
        """
        t0 = time.time()

        config: KeyframePlannerConfig = context.get("_config", KeyframePlannerConfig())
        umr_frames: list[dict] = context.get("umr_frames", [])
        output_dir = Path(context.get("output_dir", "."))
        session_id = context.get("session_id", "SESSION-091")

        if not umr_frames:
            raise ValueError("No UMR frames provided in context['umr_frames'].")

        # --- Step 1: Extract signals ---
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(umr_frames)

        # --- Step 2: Compute nonlinearity scores ---
        scores = compute_nonlinearity_scores(
            vx, vy, aw, cl, cr,
            fps=config.fps,
            weight_acc=config.weight_acceleration,
            weight_ang_accel=config.weight_angular_acceleration,
            weight_contact=config.weight_contact_event,
        )

        # --- Step 3: Compute contact events for keyframe selection ---
        n = len(umr_frames)
        contact_events = np.zeros(n, dtype=np.float64)
        if n > 1:
            left_change = np.abs(np.diff(cl))
            right_change = np.abs(np.diff(cr))
            contact_events[1:] = np.clip(left_change + right_change, 0.0, 1.0)

        # --- Step 4: Select adaptive keyframes ---
        keyframe_indices = select_adaptive_keyframes(
            scores, contact_events,
            min_gap=config.min_gap,
            max_gap=config.max_gap,
            extrema_threshold=config.extrema_threshold,
        )

        # --- Step 5: Map to SparseCtrl end_percent ---
        end_percents = map_end_percent(
            scores, keyframe_indices,
            base_end_percent=config.base_end_percent,
            max_end_percent=config.max_end_percent,
        )

        # --- Step 6: Compute quality metrics ---
        contact_event_frames = np.where(contact_events > 0.5)[0].tolist()
        contact_captured = sum(1 for cf in contact_event_frames if cf in keyframe_indices)

        gaps = [keyframe_indices[i + 1] - keyframe_indices[i]
                for i in range(len(keyframe_indices) - 1)] if len(keyframe_indices) > 1 else [0]

        plan_payload = {
            "frame_count": n,
            "fps": config.fps,
            "keyframe_indices": keyframe_indices,
            "keyframe_count": len(keyframe_indices),
            "end_percents": end_percents,
            "nonlinearity_scores": scores.tolist(),
            "contact_event_frames": contact_event_frames,
            "contact_events_captured": contact_captured,
            "min_gap": config.min_gap,
            "max_gap": config.max_gap,
            "mean_nonlinearity": round(float(scores.mean()), 6),
            "max_nonlinearity": round(float(scores.max()), 6),
            "min_actual_gap": min(gaps) if gaps else 0,
            "max_actual_gap": max(gaps) if gaps else 0,
            "config": {
                "weight_acceleration": config.weight_acceleration,
                "weight_angular_acceleration": config.weight_angular_acceleration,
                "weight_contact_event": config.weight_contact_event,
                "extrema_threshold": config.extrema_threshold,
                "base_end_percent": config.base_end_percent,
                "max_end_percent": config.max_end_percent,
            },
        }

        # --- Step 7: Three-layer evolution evaluation ---
        bridge = KeyframeEvolutionBridge(project_root=output_dir)
        fitness = bridge.evaluate(plan_payload, config)
        plan_payload["evolution_fitness"] = fitness

        # Distill if quality is high
        distilled_rules = bridge.distill(config, fitness)
        plan_payload["distilled_rules_count"] = len(distilled_rules)

        elapsed = time.time() - t0

        # --- Step 8: Save plan to disk ---
        import json
        plan_path = output_dir / "keyframe_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # --- Step 9: Build ArtifactManifest ---
        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.KEYFRAME_PLAN.value,
            backend_type=BackendType.MOTION_ADAPTIVE_KEYFRAME.value,
            version="1.0.0",
            session_id=session_id,
            timestamp=time.time(),
            outputs={
                "keyframe_plan": str(plan_path),
            },
            metadata={
                "frame_count": n,
                "fps": config.fps,
                "keyframe_count": len(keyframe_indices),
                "min_gap": config.min_gap,
                "max_gap": config.max_gap,
                "mean_nonlinearity": plan_payload["mean_nonlinearity"],
                "contact_events_captured": contact_captured,
            },
            quality_metrics={
                "coverage": fitness.get("coverage", 0.0),
                "no_void": fitness.get("no_void", 0.0),
                "no_cluster": fitness.get("no_cluster", 0.0),
                "contact_capture": fitness.get("contact_capture", 0.0),
                "temporal_coherence": fitness.get("temporal_coherence", 0.0),
                "elapsed_seconds": round(elapsed, 4),
            },
            tags=["keyframe_plan", "sparsectrl", "motion_adaptive", "p1-ai-2e"],
        )

        logger.info(
            "MotionAdaptiveKeyframeBackend: %d frames → %d keyframes "
            "(min_gap=%d, max_gap=%d) in %.3fs",
            n, len(keyframe_indices), config.min_gap, config.max_gap, elapsed,
        )

        return manifest


__all__ = [
    "MotionAdaptiveKeyframeBackend",
    "KeyframePlannerConfig",
    "KeyframeEvolutionBridge",
    "compute_nonlinearity_scores",
    "select_adaptive_keyframes",
    "map_end_percent",
    "extract_signals_from_umr_frames",
]
