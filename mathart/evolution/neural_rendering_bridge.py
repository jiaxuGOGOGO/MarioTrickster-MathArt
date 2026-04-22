"""SESSION-045 — Neural Rendering Evolution Bridge: Three-Layer Temporal Consistency.

Gap C3 research distillation: the neural rendering bridge turns procedural
ground-truth motion vectors into a three-layer evolution loop for temporal
consistency in AI-stylized video output.

Three-Layer Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  Layer 1: Internal Evolution — Temporal Consistency Gate            │
    │  Render → Bake MV → Warp-Check → Score → Accept/Reject             │
    │  (reject frames with warp error above threshold)                   │
    ├─────────────────────────────────────────────────────────────────────┤
    │  Layer 2: External Knowledge Distillation — Neural Rendering Rules │
    │  Research → Extract Rules → Update Knowledge Base                   │
    │  (EbSynth patch matching, ControlNet conditioning, flow encoding)  │
    ├─────────────────────────────────────────────────────────────────────┤
    │  Layer 3: Self-Iteration — Temporal Fitness Integration            │
    │  Animate → MV Bake → Warp Validate → Diagnose → Evolve → Distill  │
    │  (motion energy tracking, flicker trend detection, sigma tuning)   │
    └─────────────────────────────────────────────────────────────────────┘

Research Provenance:
    - Jamriška et al., "Stylizing Video by Example", SIGGRAPH 2019
    - Koroglu et al., "OnlyFlow", CVPR 2025 Workshop
    - Nam et al., "MotionPrompt", CVPR 2025
    - Unity URP Motion Vectors documentation
    - ReEzSynth (Python EbSynth implementation)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Metrics & State
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class TemporalConsistencyMetrics:
    """Metrics from a temporal consistency evaluation cycle.

    Tracks warp error, motion energy, and flicker indicators across
    a sequence of animation frames.
    """
    cycle_id: int = 0
    frame_count: int = 0
    mean_warp_error: float = 0.0
    max_warp_error: float = 0.0
    min_warp_error: float = 1.0
    mean_motion_energy: float = 0.0
    max_motion_energy: float = 0.0
    warp_ssim_proxy: float = 1.0
    coverage: float = 0.0
    flicker_score: float = 0.0
    temporal_pass: bool = False
    warp_error_threshold: float = 0.15
    per_frame_errors: list[float] = field(default_factory=list)
    per_frame_energies: list[float] = field(default_factory=list)
    # SESSION-131: Min-SSIM fuse fields
    min_warp_ssim: float = 1.0
    per_pair_warp_ssim: list[float] = field(default_factory=list)
    worst_frame_pair_index: int = -1
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "mean_warp_error": self.mean_warp_error,
            "max_warp_error": self.max_warp_error,
            "min_warp_error": self.min_warp_error,
            "mean_motion_energy": self.mean_motion_energy,
            "max_motion_energy": self.max_motion_energy,
            "warp_ssim_proxy": self.warp_ssim_proxy,
            "coverage": self.coverage,
            "flicker_score": self.flicker_score,
            "temporal_pass": self.temporal_pass,
            "warp_error_threshold": self.warp_error_threshold,
            "per_frame_errors": self.per_frame_errors,
            "per_frame_energies": self.per_frame_energies,
            "min_warp_ssim": self.min_warp_ssim,
            "per_pair_warp_ssim": self.per_pair_warp_ssim,
            "worst_frame_pair_index": self.worst_frame_pair_index,
            "timestamp": self.timestamp,
        }


@dataclass
class NeuralRenderingState:
    """Persistent state for neural rendering evolution tracking."""
    total_evaluation_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    warp_error_trend: list[float] = field(default_factory=list)
    motion_energy_trend: list[float] = field(default_factory=list)
    best_warp_error: float = 1.0
    worst_warp_error: float = 0.0
    best_flicker_score: float = 1.0
    consecutive_passes: int = 0
    knowledge_rules_total: int = 0
    skinning_sigma_history: list[float] = field(default_factory=list)
    optimal_skinning_sigma: float = 0.15
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_evaluation_cycles": self.total_evaluation_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "warp_error_trend": self.warp_error_trend[-50:],
            "motion_energy_trend": self.motion_energy_trend[-50:],
            "best_warp_error": self.best_warp_error,
            "worst_warp_error": self.worst_warp_error,
            "best_flicker_score": self.best_flicker_score,
            "consecutive_passes": self.consecutive_passes,
            "knowledge_rules_total": self.knowledge_rules_total,
            "skinning_sigma_history": self.skinning_sigma_history[-20:],
            "optimal_skinning_sigma": self.optimal_skinning_sigma,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NeuralRenderingState":
        return cls(
            total_evaluation_cycles=d.get("total_evaluation_cycles", 0),
            total_passes=d.get("total_passes", 0),
            total_failures=d.get("total_failures", 0),
            warp_error_trend=d.get("warp_error_trend", []),
            motion_energy_trend=d.get("motion_energy_trend", []),
            best_warp_error=d.get("best_warp_error", 1.0),
            worst_warp_error=d.get("worst_warp_error", 0.0),
            best_flicker_score=d.get("best_flicker_score", 1.0),
            consecutive_passes=d.get("consecutive_passes", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            skinning_sigma_history=d.get("skinning_sigma_history", []),
            optimal_skinning_sigma=d.get("optimal_skinning_sigma", 0.15),
            history=d.get("history", []),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Neural Rendering Status (for evolution_loop.py integration)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class NeuralRenderingStatus:
    """Snapshot of the neural rendering bridge integration status."""
    motion_vector_module_exists: bool = False
    bridge_module_exists: bool = False
    public_api_exports_mv: bool = False
    test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    evaluation_cycles: int = 0
    consecutive_passes: int = 0
    optimal_skinning_sigma: float = 0.15

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def collect_neural_rendering_status(project_root: str | Path) -> NeuralRenderingStatus:
    """Collect the persisted state of neural rendering bridge integration."""
    root = Path(project_root)
    mv_module = root / "mathart/animation/motion_vector_baker.py"
    bridge_module = root / "mathart/evolution/neural_rendering_bridge.py"
    api_module = root / "mathart/animation/__init__.py"
    test_path = root / "tests/test_motion_vector_baker.py"
    research_notes = root / "docs/research/GAP_C3_NEURAL_RENDERING_BRIDGE.md"
    state_path = root / ".neural_rendering_state.json"

    tracked_exports: list[str] = []
    if mv_module.exists():
        try:
            text = mv_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "MotionVectorField",
            "compute_pixel_motion_field",
            "bake_motion_vector_sequence",
            "export_ebsynth_project",
            "encode_motion_vector_rgb",
            "compute_temporal_consistency_score",
        ):
            if name in text:
                tracked_exports.append(name)

    api_exports_mv = False
    if api_module.exists():
        try:
            api_text = api_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            api_text = ""
        api_exports_mv = "bake_motion_vector_sequence" in api_text

    eval_cycles = 0
    consec_passes = 0
    optimal_sigma = 0.15
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            eval_cycles = state_data.get("total_evaluation_cycles", 0)
            consec_passes = state_data.get("consecutive_passes", 0)
            optimal_sigma = state_data.get("optimal_skinning_sigma", 0.15)
        except (json.JSONDecodeError, OSError):
            pass

    return NeuralRenderingStatus(
        motion_vector_module_exists=mv_module.exists(),
        bridge_module_exists=bridge_module.exists(),
        public_api_exports_mv=api_exports_mv,
        test_exists=test_path.exists(),
        research_notes_path=str(research_notes.relative_to(root)) if research_notes.exists() else "",
        tracked_exports=tracked_exports,
        evaluation_cycles=eval_cycles,
        consecutive_passes=consec_passes,
        optimal_skinning_sigma=optimal_sigma,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Bridge: Three-Layer Evolution Integration
# ═══════════════════════════════════════════════════════════════════════════


class NeuralRenderingEvolutionBridge:
    """Bridge between the motion vector / temporal consistency pipeline and
    the three-layer evolution cycle.

    Modeled after VisualRegressionEvolutionBridge (SESSION-041), this class
    integrates temporal consistency metrics into the evolution engine's
    fitness evaluation, knowledge distillation, and self-iteration cycles.

    Parameters
    ----------
    project_root : Path
        Root directory of the project.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    # ── Layer 1: Temporal Consistency Gate ──────────────────────────────

    def evaluate_temporal_consistency(
        self,
        rendered_frames: Optional[list[np.ndarray]] = None,
        mv_sequence: Optional[Any] = None,
        warp_error_threshold: float = 0.15,
    ) -> TemporalConsistencyMetrics:
        """Evaluate temporal consistency of a rendered animation sequence.

        This is called by Layer 1 (Inner Loop) to gate animation acceptance
        on temporal coherence. Frames that flicker or deviate from
        motion-vector-predicted positions are penalized.

        Parameters
        ----------
        rendered_frames : list[np.ndarray], optional
            Rendered RGBA frames as numpy arrays.
        mv_sequence : MotionVectorSequence, optional
            Pre-computed motion vector sequence.
        warp_error_threshold : float
            Maximum acceptable mean warp error.

        Returns
        -------
        TemporalConsistencyMetrics
        """
        from ..animation.motion_vector_baker import (
            compute_temporal_consistency_score,
        )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.total_evaluation_cycles += 1

        metrics = TemporalConsistencyMetrics(
            cycle_id=self.state.total_evaluation_cycles,
            warp_error_threshold=warp_error_threshold,
            timestamp=now,
        )

        if rendered_frames is None or mv_sequence is None:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
            self._save_state()
            return metrics

        if len(rendered_frames) < 2 or len(mv_sequence.fields) == 0:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
            self._save_state()
            return metrics

        metrics.frame_count = len(rendered_frames)
        per_frame_errors = []
        per_frame_energies = []
        warp_ssim_proxies = []
        coverages = []

        n_fields = min(len(mv_sequence.fields), len(rendered_frames) - 1)
        for i in range(n_fields):
            frame_a = rendered_frames[i]
            frame_b = rendered_frames[i + 1]
            mv_field = mv_sequence.fields[i]

            scores = compute_temporal_consistency_score(frame_a, frame_b, mv_field)
            per_frame_errors.append(scores["warp_error"])
            per_frame_energies.append(mv_field.mean_magnitude)
            warp_ssim_proxies.append(scores["warp_ssim_proxy"])
            coverages.append(scores["coverage"])

        metrics.per_frame_errors = per_frame_errors
        metrics.per_frame_energies = per_frame_energies
        # SESSION-131: Track per-pair warp SSIM for Min-SSIM fuse
        metrics.per_pair_warp_ssim = warp_ssim_proxies

        if per_frame_errors:
            metrics.mean_warp_error = float(np.mean(per_frame_errors))
            metrics.max_warp_error = float(np.max(per_frame_errors))
            metrics.min_warp_error = float(np.min(per_frame_errors))
            metrics.mean_motion_energy = float(np.mean(per_frame_energies))
            metrics.max_motion_energy = float(np.max(per_frame_energies))
            metrics.warp_ssim_proxy = float(np.mean(warp_ssim_proxies))
            metrics.coverage = float(np.mean(coverages))

            # SESSION-131: Min-SSIM — worst frame pair is the fuse
            if warp_ssim_proxies:
                metrics.min_warp_ssim = float(np.min(warp_ssim_proxies))
                metrics.worst_frame_pair_index = int(np.argmin(warp_ssim_proxies))

            # Flicker score: variance of warp errors (high variance = flicker)
            if len(per_frame_errors) > 1:
                metrics.flicker_score = float(np.std(per_frame_errors))
            else:
                metrics.flicker_score = 0.0

        # SESSION-131: Use Min-SSIM as secondary pass criterion
        # Primary: mean_warp_error <= threshold
        # Secondary: min_warp_ssim >= 0.5 (catastrophic frame detection)
        warp_pass = metrics.mean_warp_error <= warp_error_threshold
        ssim_pass = metrics.min_warp_ssim >= 0.5
        metrics.temporal_pass = warp_pass and ssim_pass

        # Update state
        if metrics.temporal_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0

        self.state.warp_error_trend.append(metrics.mean_warp_error)
        self.state.motion_energy_trend.append(metrics.mean_motion_energy)
        self.state.best_warp_error = min(self.state.best_warp_error, metrics.mean_warp_error)
        self.state.worst_warp_error = max(self.state.worst_warp_error, metrics.mean_warp_error)
        self.state.best_flicker_score = min(self.state.best_flicker_score, metrics.flicker_score)

        self.state.history.append(metrics.to_dict())
        self._save_state()

        if self.verbose:
            status = "PASS" if metrics.temporal_pass else "FAIL"
            logger.info(
                f"[NeuralRendering] Cycle {metrics.cycle_id}: {status} "
                f"(WarpErr={metrics.mean_warp_error:.4f}, "
                f"Flicker={metrics.flicker_score:.4f}, "
                f"Energy={metrics.mean_motion_energy:.2f})"
            )

        return metrics

    # ── Layer 2: Knowledge Distillation ────────────────────────────────

    def distill_temporal_knowledge(
        self,
        metrics: TemporalConsistencyMetrics,
    ) -> list[dict[str, Any]]:
        """Distill temporal consistency results into knowledge rules.

        Called by Layer 2 (Outer Loop) to generate reusable rules from
        temporal consistency outcomes.

        Parameters
        ----------
        metrics : TemporalConsistencyMetrics
            Metrics from the latest temporal consistency evaluation.

        Returns
        -------
        list[dict]
            Knowledge rules to add to the knowledge base.
        """
        rules: list[dict[str, Any]] = []

        # Rule: Temporal consistency failure
        if not metrics.temporal_pass:
            rules.append({
                "domain": "temporal_consistency",
                "rule_type": "enforcement",
                "rule_text": (
                    f"Temporal consistency failure: mean warp error "
                    f"{metrics.mean_warp_error:.4f} exceeds threshold "
                    f"{metrics.warp_error_threshold}. Possible causes: "
                    "1) Skinning sigma too large (motion bleeding), "
                    "2) Large pose discontinuity between frames, "
                    "3) SDF union boundary artifacts. "
                    "Consider reducing skinning_sigma or adding intermediate frames."
                ),
                "params": {
                    "mean_warp_error": f"{metrics.mean_warp_error:.4f}",
                    "threshold": f"{metrics.warp_error_threshold}",
                    "cycle_id": str(metrics.cycle_id),
                },
                "confidence": 0.92,
                "source": f"NeuralRenderingBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: High flicker detected
        if metrics.flicker_score > 0.05:
            rules.append({
                "domain": "temporal_consistency",
                "rule_type": "flicker_warning",
                "rule_text": (
                    f"High flicker score detected: {metrics.flicker_score:.4f}. "
                    "Warp error variance across frames is elevated, indicating "
                    "inconsistent motion between frame pairs. This will cause "
                    "visible flicker in AI-stylized output. Investigate: "
                    "1) Non-uniform frame timing, 2) Abrupt pose changes, "
                    "3) Skinning weight discontinuities at part boundaries."
                ),
                "params": {
                    "flicker_score": f"{metrics.flicker_score:.4f}",
                    "per_frame_errors": [f"{e:.4f}" for e in metrics.per_frame_errors[:8]],
                },
                "confidence": 0.88,
                "source": f"NeuralRenderingBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Consecutive passes — stability confirmation
        if metrics.temporal_pass and self.state.consecutive_passes >= 5:
            rules.append({
                "domain": "temporal_consistency",
                "rule_type": "confidence_boost",
                "rule_text": (
                    f"Temporal consistency has passed {self.state.consecutive_passes} "
                    "consecutive cycles. The motion vector pipeline is stable. "
                    "Current optimal skinning sigma: "
                    f"{self.state.optimal_skinning_sigma:.3f}. "
                    "Consider tightening warp error threshold for higher quality."
                ),
                "params": {
                    "consecutive_passes": str(self.state.consecutive_passes),
                    "best_warp_error": f"{self.state.best_warp_error:.4f}",
                    "optimal_sigma": f"{self.state.optimal_skinning_sigma:.3f}",
                },
                "confidence": 0.90,
                "source": f"NeuralRenderingBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Warp error trend degradation
        if len(self.state.warp_error_trend) >= 3:
            recent = self.state.warp_error_trend[-3:]
            if all(recent[i] > recent[i - 1] for i in range(1, len(recent))):
                rules.append({
                    "domain": "temporal_consistency",
                    "rule_type": "trend_warning",
                    "rule_text": (
                        "Warp error shows an increasing trend over the last 3 cycles: "
                        f"{[f'{e:.4f}' for e in recent]}. "
                        "While still within threshold, this may indicate gradual "
                        "degradation of temporal coherence. Investigate animation "
                        "parameter drift or skinning weight changes."
                    ),
                    "params": {
                        "trend": [f"{e:.4f}" for e in recent],
                    },
                    "confidence": 0.85,
                    "source": f"NeuralRenderingBridge-Cycle-{metrics.cycle_id}",
                })

        self.state.knowledge_rules_total += len(rules)

        if rules:
            self._save_knowledge_rules(rules)

        return rules

    # ── Layer 3: Temporal Fitness Integration ──────────────────────────

    def compute_temporal_fitness_bonus(
        self,
        metrics: TemporalConsistencyMetrics,
    ) -> float:
        """Compute a fitness bonus/penalty based on temporal consistency.

        Used by Layer 3 (Self-Iteration) to incorporate temporal coherence
        into the physics evolution fitness function.

        Parameters
        ----------
        metrics : TemporalConsistencyMetrics
            Metrics from the latest temporal consistency evaluation.

        Returns
        -------
        float
            Fitness modifier in [-0.3, +0.15].
        """
        bonus = 0.0

        # Full temporal pass bonus
        if metrics.temporal_pass:
            bonus += 0.05

        # Low warp error bonus (below half threshold)
        if metrics.mean_warp_error < metrics.warp_error_threshold * 0.5:
            bonus += 0.04

        # Low flicker bonus
        if metrics.flicker_score < 0.02:
            bonus += 0.03

        # Consecutive passes bonus (stability)
        if self.state.consecutive_passes >= 3:
            bonus += 0.03

        # Warp error failure penalty
        if not metrics.temporal_pass:
            excess = metrics.mean_warp_error - metrics.warp_error_threshold
            bonus -= min(0.2, excess * 5.0)

        # High flicker penalty
        if metrics.flicker_score > 0.1:
            bonus -= min(0.15, metrics.flicker_score * 1.5)

        # SESSION-131: Min-SSIM catastrophic frame penalty
        # If worst frame pair SSIM is below 0.5, apply heavy penalty
        # proportional to the deficit.  This forces evolution to avoid
        # parameter combinations that produce even one bad frame.
        if metrics.min_warp_ssim < 0.5:
            ssim_deficit = 0.5 - metrics.min_warp_ssim
            bonus -= min(0.25, ssim_deficit * 2.0)

        # Zero coverage penalty (no valid motion data)
        if metrics.coverage < 0.01:
            bonus -= 0.3

        return max(-0.3, min(0.15, bonus))

    # ── Skinning Sigma Optimization ───────────────────────────────────

    def suggest_skinning_sigma(self) -> float:
        """Suggest an optimal skinning sigma based on historical performance.

        Analyzes the relationship between skinning sigma values and warp
        errors from past evaluations to recommend the best sigma.

        Returns
        -------
        float
            Suggested skinning sigma value.
        """
        if len(self.state.skinning_sigma_history) < 3:
            return self.state.optimal_skinning_sigma

        # Simple: pick the sigma that produced the lowest warp error
        pairs = list(zip(
            self.state.skinning_sigma_history[-20:],
            self.state.warp_error_trend[-20:],
        ))
        if not pairs:
            return self.state.optimal_skinning_sigma

        best_pair = min(pairs, key=lambda p: p[1])
        self.state.optimal_skinning_sigma = best_pair[0]
        return best_pair[0]

    def record_sigma_trial(self, sigma: float, warp_error: float) -> None:
        """Record a skinning sigma trial result for future optimization."""
        self.state.skinning_sigma_history.append(sigma)
        self._save_state()

    # ── Status Report ────────────────────────────────────────────────

    def status_report(self) -> str:
        """Generate a status report for the neural rendering bridge."""
        lines = [
            "--- Neural Rendering Evolution Bridge (SESSION-045 / Gap C3) ---",
            f"   Total evaluation cycles: {self.state.total_evaluation_cycles}",
            f"   Passes: {self.state.total_passes}",
            f"   Failures: {self.state.total_failures}",
            f"   Consecutive passes: {self.state.consecutive_passes}",
            f"   Best warp error: {self.state.best_warp_error:.4f}",
            f"   Worst warp error: {self.state.worst_warp_error:.4f}",
            f"   Best flicker score: {self.state.best_flicker_score:.4f}",
            f"   Optimal skinning sigma: {self.state.optimal_skinning_sigma:.3f}",
            f"   Knowledge rules generated: {self.state.knowledge_rules_total}",
        ]
        if self.state.warp_error_trend:
            recent = self.state.warp_error_trend[-5:]
            lines.append(f"   Recent warp error trend: {[f'{e:.4f}' for e in recent]}")
        if self.state.motion_energy_trend:
            recent = self.state.motion_energy_trend[-5:]
            lines.append(f"   Recent motion energy trend: {[f'{e:.2f}' for e in recent]}")
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────

    def _save_knowledge_rules(self, rules: list[dict]) -> None:
        """Save temporal consistency knowledge rules to the knowledge base."""
        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_file = knowledge_dir / "temporal_consistency.md"

        lines = []
        if not knowledge_file.exists():
            lines = [
                "# Temporal Consistency Knowledge Base",
                "",
                "> Auto-generated by SESSION-045 Neural Rendering Evolution Bridge.",
                "> Research provenance: Gap C3 — Jamriška (EbSynth), OnlyFlow, MotionPrompt.",
                "",
            ]

        for rule in rules:
            lines.extend([
                f"## [{rule['domain']}] {rule['rule_type']} "
                f"(confidence: {rule['confidence']:.2f})",
                "",
                f"> {rule['rule_text']}",
                "",
                f"Source: {rule['source']}",
                "",
                "Parameters:",
            ])
            params = rule.get("params", {})
            for k, v in params.items():
                lines.append(f"  - `{k}`: {v}")
            lines.append("")

        with knowledge_file.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _load_state(self) -> NeuralRenderingState:
        """Load persistent state from disk."""
        state_path = self.project_root / ".neural_rendering_state.json"
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                return NeuralRenderingState.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return NeuralRenderingState()

    def _save_state(self) -> None:
        """Save persistent state to disk."""
        state_path = self.project_root / ".neural_rendering_state.json"
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = [
    "TemporalConsistencyMetrics",
    "NeuralRenderingState",
    "NeuralRenderingStatus",
    "NeuralRenderingEvolutionBridge",
    "collect_neural_rendering_status",
]
