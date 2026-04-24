"""SESSION-041: Visual Regression Evolution Bridge — Three-Layer CI Integration.

This module bridges the headless E2E visual regression pipeline into the
three-layer evolution cycle, enabling the system to:

1. **Layer 1 (Inner Loop):** Validate that every generated character pack
   passes SSIM visual regression before scoring. Reject packs that visually
   regress below the golden baseline.

2. **Layer 2 (Outer Loop):** Distill visual regression findings into
   reusable knowledge rules. When SSIM drops or structural drift occurs,
   auto-generate knowledge rules that prevent future regressions.

3. **Layer 3 (Self-Iteration):** Include visual fidelity as a fitness
   dimension. Physics evolution cycles now verify that evolved parameters
   don't cause visual degradation.

The three-layer visual regression cycle:

    ┌─────────────────────────────────────────────────────────────────┐
    │  Layer 1: Internal Evolution + Visual Gate                      │
    │  Generate → Render → SSIM Check → Evaluate → Optimize           │
    │  (reject visual regressions, verify atlas determinism)          │
    ├─────────────────────────────────────────────────────────────────┤
    │  Layer 2: External Knowledge Distillation + CI Findings         │
    │  Audit Report → Extract Rules → Update Knowledge Base           │
    │  (Skia Gold triage patterns, SSIM threshold tuning, heatmaps)  │
    ├─────────────────────────────────────────────────────────────────┤
    │  Layer 3: Self-Iteration Testing + Visual Regression            │
    │  Train → Test → SSIM Audit → Diagnose → Evolve → Distill       │
    │  (visual regression tracking, cross-run SSIM trends)           │
    └─────────────────────────────────────────────────────────────────┘

Industrial references:
    - Skia Gold (Google Chrome): hash-first triage, multi-baseline
    - OpenUSD Validation (Pixar): schema-aware validation, structured errors
    - SSIM (Wang et al. 2004): perceptual image quality metric
    - Hermetic Builds (Bazel): deterministic, isolated execution
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


# ── Metrics ──────────────────────────────────────────────────────────────────


@dataclass
class VisualRegressionMetrics:
    """Metrics from a visual regression audit cycle.

    Inspired by Skia Gold's structured triage output and OpenUSD's
    UsdValidationError — each metric has a severity and category.
    """
    cycle_id: int = 0
    ssim_score: Optional[float] = None
    ssim_threshold: float = 0.9999
    ssim_pass: bool = False
    structural_pass: bool = False
    pipeline_hash_match: bool = False
    pipeline_hash: str = ""
    golden_hash: str = ""
    diff_heatmap_generated: bool = False
    diff_heatmap_path: str = ""
    level0_pass: bool = False
    level1_pass: bool = False
    level2_pass: bool = False
    all_pass: bool = False
    knowledge_rules_generated: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "ssim_score": self.ssim_score,
            "ssim_threshold": self.ssim_threshold,
            "ssim_pass": self.ssim_pass,
            "structural_pass": self.structural_pass,
            "pipeline_hash_match": self.pipeline_hash_match,
            "pipeline_hash": self.pipeline_hash,
            "golden_hash": self.golden_hash,
            "diff_heatmap_generated": self.diff_heatmap_generated,
            "diff_heatmap_path": self.diff_heatmap_path,
            "level0_pass": self.level0_pass,
            "level1_pass": self.level1_pass,
            "level2_pass": self.level2_pass,
            "all_pass": self.all_pass,
            "knowledge_rules_generated": self.knowledge_rules_generated,
            "timestamp": self.timestamp,
        }


@dataclass
class VisualRegressionState:
    """Persistent state for visual regression evolution tracking."""
    total_audit_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    ssim_trend: list[float] = field(default_factory=list)
    best_ssim: float = 0.0
    worst_ssim: float = 1.0
    golden_baseline_hash: str = ""
    golden_baseline_set: bool = False
    consecutive_passes: int = 0
    knowledge_rules_total: int = 0
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_audit_cycles": self.total_audit_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "ssim_trend": self.ssim_trend[-50:],  # Keep last 50
            "best_ssim": self.best_ssim,
            "worst_ssim": self.worst_ssim,
            "golden_baseline_hash": self.golden_baseline_hash,
            "golden_baseline_set": self.golden_baseline_set,
            "consecutive_passes": self.consecutive_passes,
            "knowledge_rules_total": self.knowledge_rules_total,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VisualRegressionState":
        return cls(
            total_audit_cycles=d.get("total_audit_cycles", 0),
            total_passes=d.get("total_passes", 0),
            total_failures=d.get("total_failures", 0),
            ssim_trend=d.get("ssim_trend", []),
            best_ssim=d.get("best_ssim", 0.0),
            worst_ssim=d.get("worst_ssim", 1.0),
            golden_baseline_hash=d.get("golden_baseline_hash", ""),
            golden_baseline_set=d.get("golden_baseline_set", False),
            consecutive_passes=d.get("consecutive_passes", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            history=d.get("history", []),
        )


# ── Bridge ───────────────────────────────────────────────────────────────────


class VisualRegressionEvolutionBridge:
    """Bridge between the visual regression CI pipeline and the three-layer
    evolution cycle.

    This class integrates headless E2E audit results into the evolution
    engine's fitness evaluation, knowledge distillation, and self-iteration
    cycles.

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

    # ── Layer 1: Visual Gate ─────────────────────────────────────────────

    def evaluate_visual_regression(
        self,
        audit_report: Optional[dict] = None,
    ) -> VisualRegressionMetrics:
        """Evaluate visual regression from an audit report or by running
        the headless E2E audit.

        This is called by Layer 1 (Inner Loop) to gate character pack
        acceptance on visual fidelity.

        Parameters
        ----------
        audit_report : dict, optional
            Pre-computed audit report dict. If None, runs the full audit.

        Returns
        -------
        VisualRegressionMetrics
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.total_audit_cycles += 1

        metrics = VisualRegressionMetrics(
            cycle_id=self.state.total_audit_cycles,
            timestamp=now,
        )

        if audit_report is None:
            # Run the full headless E2E audit
            try:
                from mathart.headless_e2e_ci import run_full_audit
                report = run_full_audit()
                audit_report = report.to_dict()
            except Exception as e:
                logger.warning(f"Visual regression audit failed: {e}")
                self.state.total_failures += 1
                self.state.consecutive_passes = 0
                self._save_state()
                return metrics

        # Extract metrics from audit report
        metrics.level0_pass = audit_report.get("level0_pass", False)
        metrics.level1_pass = audit_report.get("level1_pass", False)
        metrics.level2_pass = audit_report.get("level2_pass", False)
        metrics.all_pass = audit_report.get("all_pass", False)
        metrics.ssim_score = audit_report.get("ssim_score")
        metrics.pipeline_hash = audit_report.get("pipeline_hash", "")
        metrics.golden_hash = audit_report.get("golden_hash", "")
        metrics.diff_heatmap_path = audit_report.get("diff_heatmap_path", "")
        metrics.diff_heatmap_generated = bool(metrics.diff_heatmap_path)

        # SSIM evaluation
        if metrics.ssim_score is not None:
            metrics.ssim_pass = metrics.ssim_score >= metrics.ssim_threshold
            self.state.ssim_trend.append(metrics.ssim_score)
            self.state.best_ssim = max(self.state.best_ssim, metrics.ssim_score)
            self.state.worst_ssim = min(self.state.worst_ssim, metrics.ssim_score)

        # Hash comparison
        if metrics.pipeline_hash and metrics.golden_hash:
            metrics.pipeline_hash_match = (
                metrics.pipeline_hash == metrics.golden_hash
            )

        # Structural evaluation
        metrics.structural_pass = metrics.level1_pass

        # Update state
        if metrics.all_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0

        # Track golden baseline
        if not self.state.golden_baseline_set and metrics.pipeline_hash:
            self.state.golden_baseline_hash = metrics.pipeline_hash
            self.state.golden_baseline_set = True

        self.state.history.append(metrics.to_dict())
        self._save_state()

        if self.verbose:
            status = "PASS" if metrics.all_pass else "FAIL"
            ssim_str = f"{metrics.ssim_score:.6f}" if metrics.ssim_score else "N/A"
            logger.info(
                f"[VisualRegression] Cycle {metrics.cycle_id}: {status} "
                f"(SSIM={ssim_str}, L0={metrics.level0_pass}, "
                f"L1={metrics.level1_pass}, L2={metrics.level2_pass})"
            )

        return metrics

    # ── Layer 2: Knowledge Distillation ──────────────────────────────────

    def distill_visual_knowledge(
        self,
        metrics: VisualRegressionMetrics,
    ) -> list[dict[str, Any]]:
        """Distill visual regression results into knowledge rules.

        Called by Layer 2 (Outer Loop) to generate reusable rules from
        visual regression outcomes. Inspired by Skia Gold's triage workflow
        where human-approved patterns become baseline rules.

        Parameters
        ----------
        metrics : VisualRegressionMetrics
            Metrics from the latest visual regression audit.

        Returns
        -------
        list[dict]
            Knowledge rules to add to the knowledge base.
        """
        rules: list[dict[str, Any]] = []

        # Rule: SSIM regression detected
        if metrics.ssim_score is not None and not metrics.ssim_pass:
            rules.append({
                "domain": "visual_regression",
                "rule_type": "enforcement",
                "rule_text": (
                    f"Visual regression detected: SSIM={metrics.ssim_score:.6f} "
                    f"below threshold {metrics.ssim_threshold}. The rendered atlas "
                    "has diverged from the golden baseline. Possible causes: "
                    "1) Non-deterministic rendering path, 2) Dithering seed change, "
                    "3) Anti-aliasing parameter drift, 4) Color palette mutation. "
                    "Run --update-golden after intentional changes."
                ),
                "params": {
                    "ssim_score": f"{metrics.ssim_score:.6f}",
                    "threshold": f"{metrics.ssim_threshold}",
                    "cycle_id": str(metrics.cycle_id),
                },
                "confidence": 0.95,
                "source": f"VisualRegressionBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Pipeline hash drift
        if metrics.pipeline_hash and metrics.golden_hash and not metrics.pipeline_hash_match:
            rules.append({
                "domain": "deterministic_seal",
                "rule_type": "regression_guard",
                "rule_text": (
                    "Pipeline hash drift detected in visual regression audit. "
                    f"Golden: {metrics.golden_hash[:16]}..., "
                    f"Current: {metrics.pipeline_hash[:16]}... "
                    "This indicates structural changes in the pipeline output. "
                    "Check: dict ordering, timestamp injection, seed leaks, "
                    "floating-point non-determinism."
                ),
                "params": {
                    "golden_hash": metrics.golden_hash[:24],
                    "current_hash": metrics.pipeline_hash[:24],
                },
                "confidence": 0.93,
                "source": f"VisualRegressionBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: Consecutive passes — confidence boost
        if metrics.all_pass and self.state.consecutive_passes >= 5:
            rules.append({
                "domain": "visual_regression",
                "rule_type": "confidence_boost",
                "rule_text": (
                    f"Visual regression audit has passed {self.state.consecutive_passes} "
                    "consecutive cycles. The rendering pipeline is stable. "
                    "Consider tightening SSIM threshold or adding new golden states."
                ),
                "params": {
                    "consecutive_passes": str(self.state.consecutive_passes),
                    "best_ssim": f"{self.state.best_ssim:.6f}",
                },
                "confidence": 0.90,
                "source": f"VisualRegressionBridge-Cycle-{metrics.cycle_id}",
            })

        # Rule: SSIM trend degradation
        if len(self.state.ssim_trend) >= 3:
            recent = self.state.ssim_trend[-3:]
            if all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
                rules.append({
                    "domain": "visual_regression",
                    "rule_type": "trend_warning",
                    "rule_text": (
                        "SSIM scores show a declining trend over the last 3 cycles: "
                        f"{[f'{s:.6f}' for s in recent]}. "
                        "While still above threshold, this may indicate gradual "
                        "visual degradation. Investigate rendering parameter drift."
                    ),
                    "params": {
                        "trend": [f"{s:.6f}" for s in recent],
                    },
                    "confidence": 0.85,
                    "source": f"VisualRegressionBridge-Cycle-{metrics.cycle_id}",
                })

        self.state.knowledge_rules_total += len(rules)
        metrics.knowledge_rules_generated = len(rules)

        if rules:
            self._save_knowledge_rules(rules)

        return rules

    # ── Layer 3: Fitness Integration ─────────────────────────────────────

    def compute_visual_fitness_bonus(
        self,
        metrics: VisualRegressionMetrics,
    ) -> float:
        """Compute a fitness bonus/penalty based on visual regression results.

        Used by Layer 3 (Self-Iteration) to incorporate visual fidelity
        into the physics evolution fitness function.

        Parameters
        ----------
        metrics : VisualRegressionMetrics
            Metrics from the latest visual regression audit.

        Returns
        -------
        float
            Fitness modifier in [-0.3, +0.15].
        """
        bonus = 0.0

        # Full audit pass bonus
        if metrics.all_pass:
            bonus += 0.05

        # SSIM excellence bonus (above 0.9999)
        if metrics.ssim_score is not None and metrics.ssim_score >= 0.9999:
            bonus += 0.05

        # Hash match bonus
        if metrics.pipeline_hash_match:
            bonus += 0.03

        # Consecutive passes bonus (stability)
        if self.state.consecutive_passes >= 3:
            bonus += 0.02

        # SSIM failure penalty
        if metrics.ssim_score is not None and not metrics.ssim_pass:
            # Proportional penalty based on how far below threshold
            deficit = metrics.ssim_threshold - metrics.ssim_score
            bonus -= min(0.2, deficit * 100)

        # Structural failure penalty
        if not metrics.structural_pass:
            bonus -= 0.1

        # Level 0 failure (pipeline crash) — severe penalty
        if not metrics.level0_pass:
            bonus -= 0.3

        return max(-0.3, min(0.15, bonus))

    # ── Status Report ────────────────────────────────────────────────────

    def status_report(self) -> str:
        """Generate a status report for the visual regression bridge."""
        lines = [
            "--- Visual Regression Evolution Bridge (SESSION-041) ---",
            f"   Total audit cycles: {self.state.total_audit_cycles}",
            f"   Passes: {self.state.total_passes}",
            f"   Failures: {self.state.total_failures}",
            f"   Consecutive passes: {self.state.consecutive_passes}",
            f"   Best SSIM: {self.state.best_ssim:.6f}",
            f"   Worst SSIM: {self.state.worst_ssim:.6f}",
            f"   Golden baseline set: {self.state.golden_baseline_set}",
            f"   Knowledge rules generated: {self.state.knowledge_rules_total}",
        ]
        if self.state.golden_baseline_hash:
            lines.append(f"   Golden baseline: {self.state.golden_baseline_hash[:16]}...")
        if self.state.ssim_trend:
            recent = self.state.ssim_trend[-5:]
            lines.append(f"   Recent SSIM trend: {[f'{s:.4f}' for s in recent]}")
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────────

    def _save_knowledge_rules(self, rules: list[dict]) -> None:
        """Save visual regression knowledge rules to the knowledge base."""
        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_file = knowledge_dir / "visual_regression_ci.md"

        lines = []
        if not knowledge_file.exists():
            lines = [
                "# Visual Regression CI Knowledge Base",
                "",
                "> Auto-generated by SESSION-041 Visual Regression Evolution Bridge.",
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

    def _load_state(self) -> VisualRegressionState:
        """Load persistent state from disk."""
        state_path = resolve_state_path(self.project_root, ".visual_regression_state.json")
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                return VisualRegressionState.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return VisualRegressionState()

    def _save_state(self) -> None:
        """Save persistent state to disk."""
        state_path = resolve_state_path(self.project_root, ".visual_regression_state.json")
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = [
    "VisualRegressionMetrics",
    "VisualRegressionState",
    "VisualRegressionEvolutionBridge",
]
