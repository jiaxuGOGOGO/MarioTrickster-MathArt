"""
SESSION-056 — Breakwall Evolution Bridge: Three-Layer Evolution Loop for Phase 1.

Integrates the two new SESSION-056 subsystems (HeadlessNeuralRenderPipeline and
EngineImportPlugin) into the project's three-layer self-evolution architecture:

    ┌─────────────────────────────────────────────────────────────────────────┐
    │  Layer 1: Internal Evolution — Render → Validate → Accept/Reject       │
    │  • Run HeadlessNeuralRenderPipeline on test skeletons                  │
    │  • Validate temporal consistency (warp error, flicker, SSIM proxy)     │
    │  • Validate engine bundle completeness (all channels, contour, meta)   │
    │  • Gate: reject if warp_error > threshold OR bundle invalid            │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Layer 2: External Knowledge Distillation — Research → Rules → KB      │
    │  • Distill Jamriška EbSynth rules from temporal consistency results    │
    │  • Distill Bénard Dead Cells rules from bundle validation results     │
    │  • Distill ControlNet conditioning rules from keyframe quality         │
    │  • Write rules to knowledge/breakwall_phase1.md                       │
    ├─────────────────────────────────────────────────────────────────────────┤
    │  Layer 3: Self-Iteration — Trend → Diagnose → Evolve → Distill        │
    │  • Track warp error trend across sessions                             │
    │  • Track bundle quality trend across sessions                         │
    │  • Auto-tune keyframe_interval, ebsynth_uniformity, controlnet weights│
    │  • Compute fitness bonus/penalty for physics evolution integration     │
    └─────────────────────────────────────────────────────────────────────────┘

Research Provenance:
    - Jamriška et al., "Stylizing Video by Example", SIGGRAPH 2019
    - Zhang et al., "Adding Conditional Control to Text-to-Image Diffusion Models", ICCV 2023
    - Bénard, "Dead Cells: 2D Deferred Lighting", GDC 2019
    - Vasseur, "Dead Cells Art Pipeline", Game Developer 2018
    - FuouM/ReEzSynth, MIT License
"""
from __future__ import annotations
from .state_vault import resolve_state_path

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
class BreakwallMetrics:
    """Metrics from a single breakwall evolution cycle.

    Combines neural rendering temporal metrics and engine bundle quality.
    SESSION-060 extends this with guide-lock, identity stability, and sparse
    keyframe-planning signals for industrial anti-flicker production.
    """
    cycle_id: int = 0
    timestamp: str = ""

    # Neural rendering metrics
    neural_render_pass: bool = False
    mean_warp_error: float = 1.0
    max_warp_error: float = 1.0
    flicker_score: float = 1.0
    ssim_proxy: float = 0.0
    coverage: float = 0.0
    guide_lock_score: float = 0.0
    identity_consistency_proxy: float = 0.0
    long_range_drift: float = 1.0
    temporal_stability_score: float = 0.0
    keyframe_density: float = 0.0
    frame_count: int = 0
    keyframe_count: int = 0
    render_elapsed_seconds: float = 0.0
    workflow_manifest: dict[str, Any] = field(default_factory=dict)
    keyframe_plan: dict[str, Any] = field(default_factory=dict)

    # Engine bundle metrics
    bundle_valid: bool = False
    bundle_channels_found: int = 0
    bundle_contour_points: int = 0
    bundle_issues: list[str] = field(default_factory=list)

    # Combined gate
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "timestamp": self.timestamp,
            "neural_render_pass": self.neural_render_pass,
            "mean_warp_error": self.mean_warp_error,
            "max_warp_error": self.max_warp_error,
            "flicker_score": self.flicker_score,
            "ssim_proxy": self.ssim_proxy,
            "coverage": self.coverage,
            "guide_lock_score": self.guide_lock_score,
            "identity_consistency_proxy": self.identity_consistency_proxy,
            "long_range_drift": self.long_range_drift,
            "temporal_stability_score": self.temporal_stability_score,
            "keyframe_density": self.keyframe_density,
            "frame_count": self.frame_count,
            "keyframe_count": self.keyframe_count,
            "render_elapsed_seconds": self.render_elapsed_seconds,
            "workflow_manifest": self.workflow_manifest,
            "keyframe_plan": self.keyframe_plan,
            "bundle_valid": self.bundle_valid,
            "bundle_channels_found": self.bundle_channels_found,
            "bundle_contour_points": self.bundle_contour_points,
            "bundle_issues": self.bundle_issues,
            "accepted": self.accepted,
        }


@dataclass
class BreakwallState:
    """Persistent state for the breakwall evolution bridge."""
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_warp_error: float = 1.0
    best_flicker_score: float = 1.0
    best_temporal_stability_score: float = 0.0
    best_identity_consistency: float = 0.0
    best_bundle_channels: int = 0
    warp_error_trend: list[float] = field(default_factory=list)
    flicker_trend: list[float] = field(default_factory=list)
    temporal_stability_trend: list[float] = field(default_factory=list)
    identity_consistency_trend: list[float] = field(default_factory=list)
    bundle_quality_trend: list[float] = field(default_factory=list)
    knowledge_rules_total: int = 0
    optimal_keyframe_interval: int = 4
    optimal_ebsynth_uniformity: float = 4000.0
    optimal_controlnet_normal_weight: float = 1.0
    optimal_controlnet_depth_weight: float = 1.0
    optimal_ip_adapter_weight: float = 0.85
    optimal_mask_guide_weight: float = 1.25
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_warp_error": self.best_warp_error,
            "best_flicker_score": self.best_flicker_score,
            "best_temporal_stability_score": self.best_temporal_stability_score,
            "best_identity_consistency": self.best_identity_consistency,
            "best_bundle_channels": self.best_bundle_channels,
            "warp_error_trend": self.warp_error_trend[-50:],
            "flicker_trend": self.flicker_trend[-50:],
            "temporal_stability_trend": self.temporal_stability_trend[-50:],
            "identity_consistency_trend": self.identity_consistency_trend[-50:],
            "bundle_quality_trend": self.bundle_quality_trend[-50:],
            "knowledge_rules_total": self.knowledge_rules_total,
            "optimal_keyframe_interval": self.optimal_keyframe_interval,
            "optimal_ebsynth_uniformity": self.optimal_ebsynth_uniformity,
            "optimal_controlnet_normal_weight": self.optimal_controlnet_normal_weight,
            "optimal_controlnet_depth_weight": self.optimal_controlnet_depth_weight,
            "optimal_ip_adapter_weight": self.optimal_ip_adapter_weight,
            "optimal_mask_guide_weight": self.optimal_mask_guide_weight,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BreakwallState":
        return cls(
            total_cycles=data.get("total_cycles", 0),
            total_passes=data.get("total_passes", 0),
            total_failures=data.get("total_failures", 0),
            consecutive_passes=data.get("consecutive_passes", 0),
            best_warp_error=data.get("best_warp_error", 1.0),
            best_flicker_score=data.get("best_flicker_score", 1.0),
            best_temporal_stability_score=data.get("best_temporal_stability_score", 0.0),
            best_identity_consistency=data.get("best_identity_consistency", 0.0),
            best_bundle_channels=data.get("best_bundle_channels", 0),
            warp_error_trend=data.get("warp_error_trend", []),
            flicker_trend=data.get("flicker_trend", []),
            temporal_stability_trend=data.get("temporal_stability_trend", []),
            identity_consistency_trend=data.get("identity_consistency_trend", []),
            bundle_quality_trend=data.get("bundle_quality_trend", []),
            knowledge_rules_total=data.get("knowledge_rules_total", 0),
            optimal_keyframe_interval=data.get("optimal_keyframe_interval", 4),
            optimal_ebsynth_uniformity=data.get("optimal_ebsynth_uniformity", 4000.0),
            optimal_controlnet_normal_weight=data.get("optimal_controlnet_normal_weight", 1.0),
            optimal_controlnet_depth_weight=data.get("optimal_controlnet_depth_weight", 1.0),
            optimal_ip_adapter_weight=data.get("optimal_ip_adapter_weight", 0.85),
            optimal_mask_guide_weight=data.get("optimal_mask_guide_weight", 1.25),
            history=data.get("history", []),
        )


@dataclass
class BreakwallStatus:
    """Status snapshot for the breakwall evolution bridge."""
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_warp_error: float = 1.0
    best_flicker_score: float = 1.0
    knowledge_rules_total: int = 0
    neural_render_available: bool = True
    engine_import_available: bool = True


def collect_breakwall_status(project_root: Path) -> BreakwallStatus:
    """Collect current breakwall evolution status from persistent state."""
    state_path = resolve_state_path(project_root, ".breakwall_evolution_state.json")
    status = BreakwallStatus()
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            state = BreakwallState.from_dict(data)
            status.total_cycles = state.total_cycles
            status.total_passes = state.total_passes
            status.total_failures = state.total_failures
            status.consecutive_passes = state.consecutive_passes
            status.best_warp_error = state.best_warp_error
            status.best_flicker_score = state.best_flicker_score
            status.knowledge_rules_total = state.knowledge_rules_total
        except (json.JSONDecodeError, OSError):
            pass

    # Check module availability
    try:
        from mathart.animation.headless_comfy_ebsynth import HeadlessNeuralRenderPipeline
        status.neural_render_available = True
    except ImportError:
        status.neural_render_available = False

    try:
        from mathart.animation.engine_import_plugin import EngineImportPluginGenerator
        status.engine_import_available = True
    except ImportError:
        status.engine_import_available = False

    return status


# ═══════════════════════════════════════════════════════════════════════════
#  Breakwall Evolution Bridge
# ═══════════════════════════════════════════════════════════════════════════


class BreakwallEvolutionBridge:
    """Three-layer evolution bridge for Phase 1 Breakwall subsystems.

    Integrates HeadlessNeuralRenderPipeline and EngineImportPlugin into
    the project's self-evolution architecture.

    Parameters
    ----------
    project_root : Path
        Project root directory.
    verbose : bool
        Enable verbose logging.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        verbose: bool = False,
    ):
        self.project_root = project_root or Path(".")
        self.verbose = verbose
        self.state = self._load_state()

    # ── Layer 1: Internal Evolution — Evaluate ────────────────────────

    def evaluate_neural_rendering(
        self,
        skeleton: Any = None,
        animation_func: Any = None,
        style: Any = None,
        frames: int = 8,
        width: int = 64,
        height: int = 64,
        warp_error_threshold: float = 0.15,
    ) -> BreakwallMetrics:
        """Layer 1: Evaluate neural rendering pipeline temporal consistency.

        Runs the HeadlessNeuralRenderPipeline on a test skeleton and
        validates temporal consistency via warp-check.

        Parameters
        ----------
        skeleton : Skeleton
            Test skeleton.
        animation_func : Callable
            Animation function.
        style : CharacterStyle
            Character style.
        frames : int
            Number of test frames.
        width, height : int
            Frame dimensions.
        warp_error_threshold : float
            Maximum acceptable warp error.

        Returns
        -------
        BreakwallMetrics
            Evaluation metrics.
        """
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline,
            NeuralRenderConfig,
        )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.total_cycles += 1

        metrics = BreakwallMetrics(
            cycle_id=self.state.total_cycles,
            timestamp=now,
        )

        if skeleton is None or animation_func is None or style is None:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
            self._save_state()
            return metrics

        try:
            config = NeuralRenderConfig(
                keyframe_interval=self.state.optimal_keyframe_interval,
                ebsynth_uniformity=self.state.optimal_ebsynth_uniformity,
                controlnet_normal_weight=self.state.optimal_controlnet_normal_weight,
                controlnet_depth_weight=self.state.optimal_controlnet_depth_weight,
                ip_adapter_weight=self.state.optimal_ip_adapter_weight,
                mask_guide_weight=self.state.optimal_mask_guide_weight,
                use_ip_adapter_identity=True,
                use_mask_guide=True,
                keyframe_selection_strategy="motion_adaptive",
                warp_error_threshold=warp_error_threshold,
                output_dir=str(self.project_root / "output" / "breakwall_eval"),
            )

            pipeline = HeadlessNeuralRenderPipeline(config)
            result = pipeline.run(
                skeleton=skeleton,
                animation_func=animation_func,
                style=style,
                frames=frames,
                width=width,
                height=height,
                export=False,
            )

            # Extract metrics
            tm = result.temporal_metrics
            metrics.neural_render_pass = tm.get("temporal_pass", False)
            metrics.mean_warp_error = tm.get("mean_warp_error", 1.0)
            metrics.max_warp_error = tm.get("max_warp_error", 1.0)
            metrics.flicker_score = tm.get("flicker_score", 1.0)
            metrics.ssim_proxy = tm.get("mean_ssim_proxy", 0.0)
            metrics.coverage = tm.get("mean_coverage", 0.0)
            metrics.guide_lock_score = tm.get("guide_lock_score", 0.0)
            metrics.identity_consistency_proxy = tm.get("identity_consistency_proxy", 0.0)
            metrics.long_range_drift = tm.get("long_range_drift", 1.0)
            metrics.temporal_stability_score = tm.get("temporal_stability_score", 0.0)
            metrics.keyframe_density = tm.get("keyframe_density", 0.0)
            metrics.frame_count = result.frame_count
            metrics.keyframe_count = len(result.keyframe_indices)
            metrics.render_elapsed_seconds = result.elapsed_seconds
            metrics.workflow_manifest = dict(getattr(result, "workflow_manifest", {}) or {})
            metrics.keyframe_plan = (
                result.keyframe_plan.to_dict() if getattr(result, "keyframe_plan", None) else {}
            )

        except Exception as e:
            logger.warning(f"Neural rendering evaluation failed: {e}")
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
            self._save_state()
            return metrics

        return metrics

    def evaluate_engine_bundle(
        self,
        skeleton: Any = None,
        pose: Optional[dict] = None,
        style: Any = None,
        width: int = 64,
        height: int = 64,
    ) -> BreakwallMetrics:
        """Layer 1: Evaluate engine bundle generation and validation.

        Generates a .mathart bundle and validates completeness.

        Parameters
        ----------
        skeleton : Skeleton
            Test skeleton.
        pose : dict
            Joint angles.
        style : CharacterStyle
            Character style.
        width, height : int
            Render dimensions.

        Returns
        -------
        BreakwallMetrics
            Evaluation metrics (bundle fields only).
        """
        from mathart.animation.engine_import_plugin import (
            generate_mathart_bundle,
            validate_mathart_bundle,
        )
        import tempfile

        metrics = BreakwallMetrics(
            cycle_id=self.state.total_cycles,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        if skeleton is None or pose is None or style is None:
            return metrics

        try:
            bundle = generate_mathart_bundle(
                skeleton=skeleton,
                pose=pose,
                style=style,
                width=width,
                height=height,
                name="breakwall_test",
            )

            # Save to temp dir and validate
            with tempfile.TemporaryDirectory() as tmpdir:
                bundle_path = bundle.save(tmpdir)
                validation = validate_mathart_bundle(bundle_path)

                metrics.bundle_valid = validation.get("valid", False)
                metrics.bundle_channels_found = len(
                    validation.get("channels_found", [])
                )
                metrics.bundle_contour_points = len(bundle.contour_points)
                metrics.bundle_issues = validation.get("issues", [])

        except Exception as e:
            logger.warning(f"Engine bundle evaluation failed: {e}")
            metrics.bundle_issues = [str(e)]

        return metrics

    def evaluate_full(
        self,
        skeleton: Any = None,
        animation_func: Any = None,
        style: Any = None,
        pose: Optional[dict] = None,
        frames: int = 8,
        width: int = 64,
        height: int = 64,
        warp_error_threshold: float = 0.15,
    ) -> BreakwallMetrics:
        """Layer 1: Full evaluation combining neural rendering and engine bundle.

        Parameters
        ----------
        skeleton, animation_func, style, pose : various
            Test inputs.
        frames : int
            Number of test frames.
        width, height : int
            Frame dimensions.
        warp_error_threshold : float
            Maximum acceptable warp error.

        Returns
        -------
        BreakwallMetrics
            Combined evaluation metrics.
        """
        # Neural rendering evaluation
        render_metrics = self.evaluate_neural_rendering(
            skeleton=skeleton,
            animation_func=animation_func,
            style=style,
            frames=frames,
            width=width,
            height=height,
            warp_error_threshold=warp_error_threshold,
        )

        # Engine bundle evaluation
        bundle_metrics = self.evaluate_engine_bundle(
            skeleton=skeleton,
            pose=pose or {},
            style=style,
            width=width,
            height=height,
        )

        # Combine
        render_metrics.bundle_valid = bundle_metrics.bundle_valid
        render_metrics.bundle_channels_found = bundle_metrics.bundle_channels_found
        render_metrics.bundle_contour_points = bundle_metrics.bundle_contour_points
        render_metrics.bundle_issues = bundle_metrics.bundle_issues

        # Combined gate
        render_metrics.accepted = (
            render_metrics.neural_render_pass and render_metrics.bundle_valid
        )

        # Update state
        if render_metrics.accepted:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0

        self.state.best_warp_error = min(
            self.state.best_warp_error, render_metrics.mean_warp_error
        )
        self.state.best_flicker_score = min(
            self.state.best_flicker_score, render_metrics.flicker_score
        )
        self.state.best_temporal_stability_score = max(
            self.state.best_temporal_stability_score,
            render_metrics.temporal_stability_score,
        )
        self.state.best_identity_consistency = max(
            self.state.best_identity_consistency,
            render_metrics.identity_consistency_proxy,
        )
        self.state.best_bundle_channels = max(
            self.state.best_bundle_channels, render_metrics.bundle_channels_found
        )
        self.state.warp_error_trend.append(render_metrics.mean_warp_error)
        self.state.flicker_trend.append(render_metrics.flicker_score)
        self.state.temporal_stability_trend.append(render_metrics.temporal_stability_score)
        self.state.identity_consistency_trend.append(
            render_metrics.identity_consistency_proxy
        )
        bundle_quality = render_metrics.bundle_channels_found / 6.0
        self.state.bundle_quality_trend.append(bundle_quality)
        self.state.history.append(render_metrics.to_dict())
        self._save_state()

        if self.verbose:
            status = "PASS" if render_metrics.accepted else "FAIL"
            logger.info(
                f"[Breakwall] Cycle {render_metrics.cycle_id}: {status} "
                f"(WarpErr={render_metrics.mean_warp_error:.4f}, "
                f"Flicker={render_metrics.flicker_score:.4f}, "
                f"Identity={render_metrics.identity_consistency_proxy:.4f}, "
                f"Stability={render_metrics.temporal_stability_score:.4f}, "
                f"Bundle={render_metrics.bundle_channels_found}/6)"
            )

        return render_metrics

    # ── Layer 2: External Knowledge Distillation ──────────────────────

    def distill_knowledge(
        self,
        metrics: BreakwallMetrics,
    ) -> list[dict[str, Any]]:
        """Layer 2: Distill breakwall evaluation results into knowledge rules.

        Generates reusable rules from neural rendering and engine bundle
        evaluation outcomes.

        Parameters
        ----------
        metrics : BreakwallMetrics
            Metrics from the latest evaluation.

        Returns
        -------
        list[dict]
            Knowledge rules to add to the knowledge base.
        """
        rules: list[dict[str, Any]] = []

        # Rule: Neural rendering temporal consistency
        if not metrics.neural_render_pass:
            rules.append({
                "domain": "breakwall_neural_rendering",
                "rule_type": "enforcement",
                "rule_text": (
                    f"Neural rendering temporal consistency failure: "
                    f"mean warp error {metrics.mean_warp_error:.4f}. "
                    "Possible causes: 1) Keyframe interval too large, "
                    "2) EbSynth uniformity too low, "
                    "3) ControlNet weights insufficient. "
                    "Research ref: Jamriška SIGGRAPH 2019, Zhang ICCV 2023."
                ),
                "params": {
                    "mean_warp_error": f"{metrics.mean_warp_error:.4f}",
                    "flicker_score": f"{metrics.flicker_score:.4f}",
                    "keyframe_count": str(metrics.keyframe_count),
                },
                "confidence": 0.92,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: High flicker
        if metrics.flicker_score > 0.05:
            rules.append({
                "domain": "breakwall_neural_rendering",
                "rule_type": "flicker_warning",
                "rule_text": (
                    f"High flicker score: {metrics.flicker_score:.4f}. "
                    "EbSynth propagation may have inconsistent patch matching. "
                    "Consider: 1) Increase ebsynth_uniformity, "
                    "2) Reduce keyframe_interval, "
                    "3) Enable temporal NNF propagation. "
                    "Research ref: Jamriška PatchMatch SIGGRAPH 2019."
                ),
                "params": {
                    "flicker_score": f"{metrics.flicker_score:.4f}",
                },
                "confidence": 0.88,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.long_range_drift > 0.20 or metrics.identity_consistency_proxy < 0.60:
            rules.append({
                "domain": "breakwall_temporal_consistency",
                "rule_type": "identity_lock_enforcement",
                "rule_text": (
                    f"Temporal identity drift detected: drift={metrics.long_range_drift:.4f}, "
                    f"identity_consistency={metrics.identity_consistency_proxy:.4f}. "
                    "Lock sparse keyframes with IP-Adapter identity reference and preserve "
                    "optical-flow-guided propagation across the full sequence. "
                    "Research ref: Ye et al. IP-Adapter 2023, Liang et al. FlowVid CVPR 2024."
                ),
                "params": {
                    "long_range_drift": f"{metrics.long_range_drift:.4f}",
                    "identity_consistency_proxy": f"{metrics.identity_consistency_proxy:.4f}",
                    "guide_lock_score": f"{metrics.guide_lock_score:.4f}",
                },
                "confidence": 0.91,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.guide_lock_score < 0.70:
            rules.append({
                "domain": "breakwall_temporal_consistency",
                "rule_type": "multi_condition_lock_warning",
                "rule_text": (
                    f"Guide lock score is low ({metrics.guide_lock_score:.4f}). "
                    "Industrial anti-flicker mode should keep Normal, Depth, Mask, Motion Vectors "
                    "and identity reference aligned so AI only paints sparse keyframes. "
                    "Research ref: Zhang ControlNet ICCV 2023, Jamriška SIGGRAPH 2019."
                ),
                "params": {
                    "guide_lock_score": f"{metrics.guide_lock_score:.4f}",
                    "keyframe_density": f"{metrics.keyframe_density:.4f}",
                },
                "confidence": 0.89,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Engine bundle issues
        if not metrics.bundle_valid:
            rules.append({
                "domain": "breakwall_engine_import",
                "rule_type": "bundle_warning",
                "rule_text": (
                    f"Engine bundle validation failed with {len(metrics.bundle_issues)} issues: "
                    f"{'; '.join(metrics.bundle_issues[:3])}. "
                    "Ensure all 6 channels (albedo, normal, depth, thickness, roughness, mask) "
                    "are exported. Research ref: Bénard Dead Cells GDC 2019."
                ),
                "params": {
                    "channels_found": str(metrics.bundle_channels_found),
                    "issues": metrics.bundle_issues[:5],
                },
                "confidence": 0.90,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.accepted and metrics.guide_lock_score >= 0.80:
            rules.append({
                "domain": "breakwall_temporal_consistency",
                "rule_type": "production_recipe",
                "rule_text": (
                    "Accepted industrial anti-flicker recipe: generate sparse AI keyframes only, "
                    "lock geometry with ControlNet Normal+Depth, keep procedural masks and motion vectors "
                    "available for propagation, and preserve a stable identity reference path. "
                    "This is the recommended production baseline for SESSION-060 visual animation batches."
                ),
                "params": {
                    "keyframe_density": f"{metrics.keyframe_density:.4f}",
                    "guide_lock_score": f"{metrics.guide_lock_score:.4f}",
                    "identity_consistency_proxy": f"{metrics.identity_consistency_proxy:.4f}",
                    "temporal_stability_score": f"{metrics.temporal_stability_score:.4f}",
                },
                "confidence": 0.93,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Consecutive passes — stability
        if metrics.accepted and self.state.consecutive_passes >= 3:
            rules.append({
                "domain": "breakwall_stability",
                "rule_type": "confidence_boost",
                "rule_text": (
                    f"Breakwall pipeline has passed {self.state.consecutive_passes} "
                    "consecutive cycles. Both neural rendering and engine bundle "
                    "are stable. Current optimal parameters: "
                    f"keyframe_interval={self.state.optimal_keyframe_interval}, "
                    f"ebsynth_uniformity={self.state.optimal_ebsynth_uniformity:.0f}."
                ),
                "params": {
                    "consecutive_passes": str(self.state.consecutive_passes),
                    "best_warp_error": f"{self.state.best_warp_error:.4f}",
                },
                "confidence": 0.90,
                "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Warp error trend degradation
        if len(self.state.warp_error_trend) >= 3:
            recent = self.state.warp_error_trend[-3:]
            if all(recent[i] > recent[i - 1] for i in range(1, len(recent))):
                rules.append({
                    "domain": "breakwall_neural_rendering",
                    "rule_type": "trend_warning",
                    "rule_text": (
                        "Warp error increasing over last 3 cycles: "
                        f"{[f'{e:.4f}' for e in recent]}. "
                        "Temporal coherence may be degrading. "
                        "Investigate animation parameter drift."
                    ),
                    "params": {"trend": [f"{e:.4f}" for e in recent]},
                    "confidence": 0.85,
                    "source": f"BreakwallBridge-Cycle-{metrics.cycle_id}",
                })

        self.state.knowledge_rules_total += len(rules)
        if rules:
            self._save_knowledge_rules(rules)
        return rules

    # ── Layer 3: Self-Iteration — Fitness & Auto-Tuning ──────────────

    def compute_fitness_bonus(
        self,
        metrics: BreakwallMetrics,
    ) -> float:
        """Layer 3: Compute fitness bonus/penalty for physics evolution.

        Parameters
        ----------
        metrics : BreakwallMetrics
            Metrics from the latest evaluation.

        Returns
        -------
        float
            Fitness modifier in [-0.3, +0.2].
        """
        bonus = 0.0

        # Neural rendering pass bonus
        if metrics.neural_render_pass:
            bonus += 0.05

        # Low warp error bonus
        if metrics.mean_warp_error < 0.075:
            bonus += 0.04

        # Low flicker bonus
        if metrics.flicker_score < 0.02:
            bonus += 0.03

        if metrics.temporal_stability_score > 0.75:
            bonus += 0.03
        if metrics.identity_consistency_proxy > 0.70:
            bonus += 0.02
        if metrics.guide_lock_score > 0.70:
            bonus += 0.02

        # Bundle completeness bonus
        if metrics.bundle_valid:
            bonus += 0.03
        if metrics.bundle_channels_found >= 6:
            bonus += 0.02
        if metrics.bundle_contour_points > 10:
            bonus += 0.01

        # Consecutive passes stability bonus
        if self.state.consecutive_passes >= 3:
            bonus += 0.02

        # Penalties
        if not metrics.neural_render_pass:
            excess = metrics.mean_warp_error - 0.15
            bonus -= min(0.2, max(0, excess * 5.0))

        if metrics.flicker_score > 0.1:
            bonus -= min(0.15, metrics.flicker_score * 1.5)

        if metrics.long_range_drift < 0.999 and metrics.long_range_drift > 0.25:
            bonus -= min(0.12, metrics.long_range_drift * 0.4)
        if metrics.identity_consistency_proxy > 0.0 and metrics.identity_consistency_proxy < 0.55:
            bonus -= min(0.10, (0.55 - metrics.identity_consistency_proxy) * 0.5)

        if not metrics.bundle_valid:
            bonus -= 0.05

        return max(-0.3, min(0.2, bonus))

    def auto_tune_parameters(self) -> dict[str, Any]:
        """Layer 3: Auto-tune pipeline parameters based on historical trends.

        Analyzes warp error and flicker trends to suggest parameter adjustments.

        Returns
        -------
        dict
            Suggested parameter changes.
        """
        changes: dict[str, Any] = {}

        # If warp error is consistently high, reduce keyframe interval
        if len(self.state.warp_error_trend) >= 3:
            recent_warp = self.state.warp_error_trend[-3:]
            mean_recent = np.mean(recent_warp)
            if mean_recent > 0.12 and self.state.optimal_keyframe_interval > 1:
                self.state.optimal_keyframe_interval = max(
                    1, self.state.optimal_keyframe_interval - 1
                )
                changes["keyframe_interval"] = self.state.optimal_keyframe_interval

        # If flicker is high, increase EbSynth uniformity
        if len(self.state.flicker_trend) >= 3:
            recent_flicker = self.state.flicker_trend[-3:]
            mean_flicker = np.mean(recent_flicker)
            if mean_flicker > 0.05:
                self.state.optimal_ebsynth_uniformity = min(
                    8000.0, self.state.optimal_ebsynth_uniformity * 1.2
                )
                changes["ebsynth_uniformity"] = self.state.optimal_ebsynth_uniformity

        if len(self.state.identity_consistency_trend) >= 3:
            recent_identity = self.state.identity_consistency_trend[-3:]
            if np.mean(recent_identity) < 0.60:
                self.state.optimal_ip_adapter_weight = min(
                    1.2, self.state.optimal_ip_adapter_weight + 0.05
                )
                changes["ip_adapter_weight"] = self.state.optimal_ip_adapter_weight

        if len(self.state.temporal_stability_trend) >= 3:
            recent_stability = self.state.temporal_stability_trend[-3:]
            if np.mean(recent_stability) < 0.60:
                self.state.optimal_mask_guide_weight = min(
                    2.0, self.state.optimal_mask_guide_weight + 0.1
                )
                changes["mask_guide_weight"] = self.state.optimal_mask_guide_weight

        # If everything is stable, try increasing keyframe interval for efficiency
        if (
            self.state.consecutive_passes >= 5
            and self.state.optimal_keyframe_interval < 8
        ):
            self.state.optimal_keyframe_interval += 1
            changes["keyframe_interval"] = self.state.optimal_keyframe_interval

        if changes:
            self._save_state()

        return changes

    # ── Status Report ────────────────────────────────────────────────

    def status_report(self) -> str:
        """Generate a status report for the breakwall evolution bridge."""
        lines = [
            "--- Breakwall Evolution Bridge (SESSION-056/060 / Phase 1+2) ---",
            f"   Total cycles: {self.state.total_cycles}",
            f"   Passes: {self.state.total_passes}",
            f"   Failures: {self.state.total_failures}",
            f"   Consecutive passes: {self.state.consecutive_passes}",
            f"   Best warp error: {self.state.best_warp_error:.4f}",
            f"   Best flicker score: {self.state.best_flicker_score:.4f}",
            f"   Best temporal stability: {self.state.best_temporal_stability_score:.4f}",
            f"   Best identity consistency: {self.state.best_identity_consistency:.4f}",
            f"   Best bundle channels: {self.state.best_bundle_channels}/6",
            f"   Knowledge rules: {self.state.knowledge_rules_total}",
            f"   Optimal keyframe interval: {self.state.optimal_keyframe_interval}",
            f"   Optimal EbSynth uniformity: {self.state.optimal_ebsynth_uniformity:.0f}",
            f"   Optimal IP-Adapter weight: {self.state.optimal_ip_adapter_weight:.2f}",
            f"   Optimal mask guide weight: {self.state.optimal_mask_guide_weight:.2f}",
        ]
        if self.state.warp_error_trend:
            recent = self.state.warp_error_trend[-5:]
            lines.append(f"   Recent warp error trend: {[f'{e:.4f}' for e in recent]}")
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────

    def _save_knowledge_rules(self, rules: list[dict]) -> None:
        """Save breakwall knowledge rules to the knowledge base."""
        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_file = knowledge_dir / "breakwall_phase1.md"

        lines = []
        if not knowledge_file.exists():
            lines = [
                "# Breakwall Knowledge Base",
                "",
                "> Auto-generated by SESSION-056/060 Breakwall Evolution Bridge.",
                "> Research provenance: Jamriška (EbSynth), Zhang (ControlNet), Ye (IP-Adapter), Liang (FlowVid), "
                "Bénard (Dead Cells).",
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

    def _load_state(self) -> BreakwallState:
        """Load persistent state from disk."""
        state_path = resolve_state_path(self.project_root, ".breakwall_evolution_state.json")
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                return BreakwallState.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return BreakwallState()

    def _save_state(self) -> None:
        """Save persistent state to disk."""
        state_path = resolve_state_path(self.project_root, ".breakwall_evolution_state.json")
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = [
    "BreakwallMetrics",
    "BreakwallState",
    "BreakwallStatus",
    "collect_breakwall_status",
    "BreakwallEvolutionBridge",
]
