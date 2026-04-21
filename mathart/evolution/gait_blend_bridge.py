"""SESSION-049: Three-Layer Evolution Bridge for Gap B3 — Gait Transition Blending.

This module turns phase-preserving gait blending into a repository-native
closed loop:

1. Layer 1 — Evaluate whether gait transitions maintain phase continuity,
   eliminate foot sliding, and produce smooth weight transitions.
2. Layer 2 — Distill reusable rules from measured results (e.g. optimal
   blend speed, stride length ratios, bounce compensation).
3. Layer 3 — Persist trends so future sessions can tune toward lower
   sliding error and smoother transitions.

Research provenance:
  - David Rosen (GDC 2014): Stride Wheel + Synchronized Blend
  - UE Sync Groups / Sync Markers: Leader-Follower architecture
  - Bruderlin & Williams (SIGGRAPH 1995): Motion Signal Processing / DTW
  - Kovar & Gleicher (SCA 2003): Registration Curves
  - Ménardais et al. (SCA 2004): Support-Phase Synchronization
  - Rune Skovbo Johansen (2009): Semi-Procedural Locomotion
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# SESSION-111 P1-B3-5: The legacy ``gait_blend`` shim has been physically
# retired; the bridge now consumes the single-source ``unified_gait_blender``
# motion core. See Research Alignment — Strangler Fig Pattern closure.
from ..animation.unified_gait_blender import (
    GaitBlender,
    GaitMode,
    WALK_SYNC_PROFILE,
    RUN_SYNC_PROFILE,
    SNEAK_SYNC_PROFILE,
)


# ── Metrics ───────────────────────────────────────────────────────────────────


@dataclass
class GaitBlendMetrics:
    """Metrics captured from one Gap B3 evaluation cycle."""

    cycle_id: int = 0
    frame_count: int = 0
    transition_count: int = 0
    mean_phase_continuity_error: float = 0.0
    max_phase_jump: float = 0.0
    mean_sliding_error: float = 0.0
    weight_convergence_frames: int = 0
    all_poses_finite: bool = True
    leader_transitions_smooth: bool = True
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "transition_count": self.transition_count,
            "mean_phase_continuity_error": self.mean_phase_continuity_error,
            "max_phase_jump": self.max_phase_jump,
            "mean_sliding_error": self.mean_sliding_error,
            "weight_convergence_frames": self.weight_convergence_frames,
            "all_poses_finite": self.all_poses_finite,
            "leader_transitions_smooth": self.leader_transitions_smooth,
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


# ── Persistent State ──────────────────────────────────────────────────────────


@dataclass
class GaitBlendState:
    """Persistent state for Gap B3 gait blend evolution."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_mean_sliding_error: float = 1.0
    best_phase_continuity: float = 1.0
    knowledge_rules_total: int = 0
    sliding_error_trend: list[float] = field(default_factory=list)
    phase_continuity_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_mean_sliding_error": self.best_mean_sliding_error,
            "best_phase_continuity": self.best_phase_continuity,
            "knowledge_rules_total": self.knowledge_rules_total,
            "sliding_error_trend": self.sliding_error_trend[-50:],
            "phase_continuity_trend": self.phase_continuity_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GaitBlendState":
        return cls(
            total_cycles=data.get("total_cycles", 0),
            total_passes=data.get("total_passes", 0),
            total_failures=data.get("total_failures", 0),
            consecutive_passes=data.get("consecutive_passes", 0),
            best_mean_sliding_error=data.get("best_mean_sliding_error", 1.0),
            best_phase_continuity=data.get("best_phase_continuity", 1.0),
            knowledge_rules_total=data.get("knowledge_rules_total", 0),
            sliding_error_trend=data.get("sliding_error_trend", []),
            phase_continuity_trend=data.get("phase_continuity_trend", []),
            history=data.get("history", []),
        )


# ── Repository Status ─────────────────────────────────────────────────────────


@dataclass
class GaitBlendStatus:
    """Repository integration status for Gap B3."""

    module_exists: bool = False
    bridge_exists: bool = False
    public_api_exports_blender: bool = False
    test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    total_cycles: int = 0
    consecutive_passes: int = 0
    best_mean_sliding_error: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def collect_gait_blend_status(project_root: str | Path) -> GaitBlendStatus:
    """Collect repository integration status for Gap B3.

    SESSION-111 P1-B3-5 retired the historical ``gait_blend.py`` shim. The
    canonical module is now ``unified_gait_blender.py``; the consolidated
    regression suite lives in ``tests/test_unified_gait_blender.py``.
    """
    root = Path(project_root)
    module_path = root / "mathart/animation/unified_gait_blender.py"
    bridge_path = root / "mathart/evolution/gait_blend_bridge.py"
    api_module = root / "mathart/animation/__init__.py"
    test_path = root / "tests/test_unified_gait_blender.py"
    notes_path = root / "docs/research/GAP_B3_GAIT_TRANSITION_PHASE_BLEND.md"
    state_path = root / ".gait_blend_state.json"

    tracked_exports: list[str] = []
    if module_path.exists():
        text = module_path.read_text(encoding="utf-8", errors="replace")
        for name in (
            "SyncMarker", "GaitSyncProfile", "GaitBlender",
            "StrideWheel", "phase_warp", "blend_walk_run",
            "blend_gaits_at_phase", "adaptive_bounce",
        ):
            if name in text:
                tracked_exports.append(name)

    api_exports = False
    if api_module.exists():
        api_text = api_module.read_text(encoding="utf-8", errors="replace")
        api_exports = "GaitBlender" in api_text and "phase_warp" in api_text

    total_cycles = 0
    consecutive_passes = 0
    best_mean_sliding_error = 1.0
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = state_data.get("total_cycles", 0)
            consecutive_passes = state_data.get("consecutive_passes", 0)
            best_mean_sliding_error = state_data.get("best_mean_sliding_error", 1.0)
        except (json.JSONDecodeError, OSError):
            pass

    return GaitBlendStatus(
        module_exists=module_path.exists(),
        bridge_exists=bridge_path.exists(),
        public_api_exports_blender=api_exports,
        test_exists=test_path.exists(),
        research_notes_path=str(notes_path.relative_to(root)) if notes_path.exists() else "",
        tracked_exports=tracked_exports,
        total_cycles=total_cycles,
        consecutive_passes=consecutive_passes,
        best_mean_sliding_error=best_mean_sliding_error,
    )


# ── Knowledge Distillation Rules ──────────────────────────────────────────────


DISTILLED_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "id": "B3-RULE-001",
        "source": "David Rosen, GDC 2014",
        "rule": "Animation phase must be driven by actual distance traveled "
                "(Stride Wheel), not by elapsed time, to eliminate foot sliding.",
        "parameter": "stride_wheel.circumference",
        "constraint": "circumference = stride_length_of_current_gait_blend",
    },
    {
        "id": "B3-RULE-002",
        "source": "UE Sync Groups / Sync Markers",
        "rule": "The gait with the highest blend weight is the Leader; all "
                "Followers warp their playback rate to align sync markers "
                "(foot contacts) with the Leader's timeline.",
        "parameter": "gait_blender.leader_selection",
        "constraint": "leader = argmax(weights)",
    },
    {
        "id": "B3-RULE-003",
        "source": "Kovar & Gleicher, SCA 2003",
        "rule": "Phase warping uses piecewise-linear interpolation between "
                "corresponding sync markers. Within each marker segment, "
                "the local normalized position maps directly.",
        "parameter": "phase_warp.segment_mapping",
        "constraint": "follower_phase = f_start + local_t * (f_end - f_start)",
    },
    {
        "id": "B3-RULE-004",
        "source": "David Rosen, GDC 2014",
        "rule": "Vertical bounce amplitude decreases with speed because "
                "gravity is constant: faster stride = less airtime per step "
                "= shallower bounce arc.",
        "parameter": "adaptive_bounce.amplitude",
        "constraint": "amplitude = base_amplitude * (ref_speed / current_speed)",
    },
    {
        "id": "B3-RULE-005",
        "source": "Ménardais et al., SCA 2004",
        "rule": "Gait cycles decompose into support phases bounded by foot "
                "contacts. Phase boundaries establish correspondence for "
                "linear time warping before pose interpolation.",
        "parameter": "sync_markers",
        "constraint": "markers = [(left_foot_down, 0.0), (right_foot_down, 0.5)]",
    },
    {
        "id": "B3-RULE-006",
        "source": "Bruderlin & Williams, SIGGRAPH 1995",
        "rule": "Motions treated as time-series signals; DTW finds optimal "
                "alignment before interpolation. Marker-based DTW is the "
                "discrete approximation using known contact events.",
        "parameter": "phase_warp.algorithm",
        "constraint": "marker_segment_linear_interpolation",
    },
]


# ── Evolution Bridge ──────────────────────────────────────────────────────────


class GaitBlendEvolutionBridge:
    """Three-layer evolution bridge for Gap B3 gait transition blending."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_gait_blend(
        self,
        *,
        max_phase_jump: float = 0.08,
        max_sliding_error: float = 0.05,
        min_finite_rate: float = 1.0,
    ) -> GaitBlendMetrics:
        """Layer 1: Evaluate gait blending quality.

        Runs a standardized Walk→Run→Walk→Sneak→Walk transition sequence
        and measures phase continuity, sliding error, and pose validity.
        """
        metrics = GaitBlendMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        blender = GaitBlender()
        dt = 1.0 / 60.0

        # Transition schedule: (frames, velocity, target_gait)
        schedule = [
            (60, 1.0, GaitMode.WALK),
            (90, 3.0, GaitMode.RUN),
            (90, 1.0, GaitMode.WALK),
            (90, 0.5, GaitMode.SNEAK),
            (60, 1.0, GaitMode.WALK),
        ]

        all_phases: list[float] = []
        all_finite = True
        total_phase_delta = 0.0
        expected_phase_delta = 0.0
        prev_phase = 0.0
        frame_count = 0
        transition_count = len(schedule) - 1
        phase_jumps: list[float] = []

        for seg_frames, velocity, target in schedule:
            for _ in range(seg_frames):
                pose = blender.update(dt=dt, velocity=velocity, target_gait=target)
                phase = pose.get("_phase", 0.0)
                stride = pose.get("_stride_length", 0.8)
                all_phases.append(phase)

                # Check finiteness
                for k, v in pose.items():
                    if not math.isfinite(v):
                        all_finite = False

                # Phase continuity
                if frame_count > 0:
                    delta = (phase - prev_phase) % 1.0
                    if delta > 0.5:
                        delta = delta - 1.0
                    phase_jumps.append(abs(delta))
                    total_phase_delta += delta

                # Per-frame expected phase delta: distance / current_stride
                expected_phase_delta += (velocity * dt) / max(stride, 0.001)
                prev_phase = phase
                frame_count += 1

        metrics.frame_count = frame_count
        metrics.transition_count = transition_count
        metrics.all_poses_finite = all_finite

        if phase_jumps:
            metrics.mean_phase_continuity_error = float(sum(phase_jumps) / len(phase_jumps))
            metrics.max_phase_jump = float(max(phase_jumps))

        # Sliding error: compare per-frame accumulated phase vs per-frame expected
        if expected_phase_delta > 0:
            metrics.mean_sliding_error = abs(total_phase_delta - expected_phase_delta) / expected_phase_delta
        else:
            metrics.mean_sliding_error = 0.0

        # Check leader transitions are smooth (no oscillation)
        metrics.leader_transitions_smooth = metrics.max_phase_jump <= max_phase_jump

        # Gate
        metrics.pass_gate = (
            metrics.all_poses_finite
            and metrics.max_phase_jump <= max_phase_jump
            and metrics.mean_sliding_error <= max_sliding_error
        )

        return metrics

    def distill_knowledge(self, metrics: GaitBlendMetrics) -> list[dict[str, Any]]:
        """Layer 2: Distill knowledge rules from evaluation results."""
        rules = list(DISTILLED_KNOWLEDGE)

        # Add dynamic rules based on metrics
        if metrics.mean_sliding_error < 0.02:
            rules.append({
                "id": f"B3-DYN-{metrics.cycle_id:03d}",
                "source": "internal_evaluation",
                "rule": f"Cycle {metrics.cycle_id}: Sliding error {metrics.mean_sliding_error:.4f} "
                        f"is excellent (<2%). Stride wheel calibration is accurate.",
                "parameter": "sliding_quality",
                "constraint": f"error < 0.02 (actual: {metrics.mean_sliding_error:.4f})",
            })

        if metrics.max_phase_jump > 0.03:
            rules.append({
                "id": f"B3-DYN-{metrics.cycle_id:03d}-WARN",
                "source": "internal_evaluation",
                "rule": f"Cycle {metrics.cycle_id}: Max phase jump {metrics.max_phase_jump:.4f} "
                        f"exceeds 3% threshold. Consider reducing blend_speed.",
                "parameter": "blend_speed",
                "constraint": f"max_phase_jump < 0.03 (actual: {metrics.max_phase_jump:.4f})",
            })

        self.state.knowledge_rules_total = len(rules)
        return rules

    def persist_and_evolve(self, metrics: GaitBlendMetrics) -> dict[str, Any]:
        """Layer 3: Persist state and compute evolution fitness."""
        self.state.total_cycles += 1

        if metrics.pass_gate:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0

        self.state.best_mean_sliding_error = min(
            self.state.best_mean_sliding_error,
            metrics.mean_sliding_error,
        )
        self.state.best_phase_continuity = min(
            self.state.best_phase_continuity,
            metrics.mean_phase_continuity_error,
        )

        self.state.sliding_error_trend.append(metrics.mean_sliding_error)
        self.state.phase_continuity_trend.append(metrics.mean_phase_continuity_error)
        self.state.history.append(metrics.to_dict())

        self._save_state()

        # Compute fitness
        sliding_score = max(0.0, 1.0 - metrics.mean_sliding_error * 20.0)
        continuity_score = max(0.0, 1.0 - metrics.mean_phase_continuity_error * 50.0)
        finite_score = 1.0 if metrics.all_poses_finite else 0.0
        fitness = 0.4 * sliding_score + 0.4 * continuity_score + 0.2 * finite_score

        return {
            "fitness": fitness,
            "sliding_score": sliding_score,
            "continuity_score": continuity_score,
            "finite_score": finite_score,
            "pass_gate": metrics.pass_gate,
            "consecutive_passes": self.state.consecutive_passes,
            "total_cycles": self.state.total_cycles,
        }

    def run_full_cycle(self) -> dict[str, Any]:
        """Execute all three layers in sequence."""
        # Layer 1: Evaluate
        metrics = self.evaluate_gait_blend()
        if self.verbose:
            print(f"[B3-L1] Cycle {metrics.cycle_id}: "
                  f"sliding={metrics.mean_sliding_error:.4f}, "
                  f"max_jump={metrics.max_phase_jump:.4f}, "
                  f"finite={metrics.all_poses_finite}, "
                  f"gate={'PASS' if metrics.pass_gate else 'FAIL'}")

        # Layer 2: Distill
        rules = self.distill_knowledge(metrics)
        if self.verbose:
            print(f"[B3-L2] Distilled {len(rules)} knowledge rules")

        # Layer 3: Persist & Evolve
        fitness_result = self.persist_and_evolve(metrics)
        if self.verbose:
            print(f"[B3-L3] Fitness={fitness_result['fitness']:.4f}, "
                  f"consecutive_passes={fitness_result['consecutive_passes']}")

        return {
            "metrics": metrics.to_dict(),
            "knowledge_rules": len(rules),
            "fitness": fitness_result,
            "state": self.state.to_dict(),
        }

    def _load_state(self) -> GaitBlendState:
        state_path = self.project_root / ".gait_blend_state.json"
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                return GaitBlendState.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return GaitBlendState()

    def _save_state(self) -> None:
        state_path = self.project_root / ".gait_blend_state.json"
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
