"""ArtMathQualityController — the central quality brain of the pipeline.

This controller runs at four checkpoints and ensures that:
  1. Art knowledge rules are active constraints, not post-hoc filters
  2. Math models guide parameter generation, not just validate results
  3. Sprite references provide living benchmarks that evolve with the library
  4. Stagnation is detected early and escalated appropriately

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                  ArtMathQualityController                       │
  │                                                                 │
  │  KnowledgeBase ──► pre_generation() ──► param constraints       │
  │  MathRegistry  ──► pre_generation() ──► param constraints       │
  │  SpriteLibrary ──► pre_generation() ──► reference targets       │
  │                                                                 │
  │  AssetEvaluator ──► post_generation() ──► quality score         │
  │  KnowledgeBase  ──► post_generation() ──► rule compliance       │
  │  MathRegistry   ──► post_generation() ──► model compliance      │
  │  SpriteLibrary  ──► post_generation() ──► reference similarity  │
  │                                                                 │
  │  StagnationGuard ──► iteration_end() ──► continue/escalate      │
  └─────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from mathart.quality.checkpoint import (
    CheckpointDecision,
    CheckpointResult,
    CheckpointStage,
    KnowledgeViolation,
    MathViolation,
)


class ArtMathQualityController:
    """Central quality controller that runs at all four pipeline checkpoints.

    Parameters
    ----------
    project_root : Path, optional
        Root directory of the project.
    pass_threshold : float
        Minimum combined score to consider an asset passing (default 0.60).
    target_score : float
        Score at which to stop iterating (default 0.80).
    verbose : bool
        Print checkpoint results to stdout.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        pass_threshold: float = 0.60,
        target_score: float = 0.80,
        verbose: bool = False,
    ) -> None:
        self.root           = Path(project_root) if project_root else Path.cwd()
        self.pass_threshold = pass_threshold
        self.target_score   = target_score
        self.verbose        = verbose

        # Lazy-loaded components
        self._evaluator     = None
        self._stagnation    = None
        self._sprite_lib    = None
        self._knowledge     = self._load_knowledge()
        self._math_registry = self._load_math_registry()

        # Iteration history
        self._score_history: list[float] = []
        self._checkpoint_log: list[CheckpointResult] = []

    # ── Checkpoint 1: PRE-GENERATION ──────────────────────────────────────────

    def pre_generation(
        self,
        iteration: int,
        current_params: dict[str, float],
    ) -> CheckpointResult:
        """Run before asset generation.

        Injects knowledge constraints and math model bounds into params.
        Returns adjusted params in result.param_adjustments.
        """
        adjustments: dict[str, float] = {}
        violations: list[KnowledgeViolation] = []
        math_violations: list[MathViolation] = []

        # 1. Check params against knowledge rules
        for rule in self._knowledge:
            viol = self._check_knowledge_rule(rule, current_params)
            if viol:
                violations.append(viol)
                # Auto-adjust if we know the target value
                if viol.severity > 0.5 and rule.get("param") and rule.get("target"):
                    adjustments[rule["param"]] = rule["target"]

        # 2. Check params against math model constraints
        for model_name, constraints in self._math_registry.items():
            for param, (lo, hi) in constraints.items():
                if param in current_params:
                    val = current_params[param]
                    if val < lo or val > hi:
                        severity = abs(val - np.clip(val, lo, hi)) / max(hi - lo, 1e-6)
                        math_violations.append(MathViolation(
                            model_name=model_name,
                            param_name=param,
                            actual=val,
                            expected_lo=lo,
                            expected_hi=hi,
                            severity=min(1.0, severity),
                        ))
                        # Clamp to valid range
                        adjustments[param] = float(np.clip(val, lo, hi))

        # 3. Inject sprite library constraints
        sprite_constraints = self._get_sprite_constraints()
        for param, (lo, hi) in sprite_constraints.items():
            if param in current_params:
                val = current_params[param]
                if val < lo or val > hi:
                    adjustments[param] = float((lo + hi) / 2)

        # Compute knowledge compliance score
        k_score = max(0.0, 1.0 - len(violations) * 0.1)
        m_score = max(0.0, 1.0 - len(math_violations) * 0.1)

        decision = CheckpointDecision.CONTINUE
        if adjustments:
            decision = CheckpointDecision.ADJUST

        result = CheckpointResult(
            stage=CheckpointStage.PRE_GENERATION,
            iteration=iteration,
            decision=decision,
            quality_score=1.0,  # Not yet evaluated
            knowledge_score=k_score,
            math_score=m_score,
            sprite_ref_score=1.0,
            knowledge_violations=violations,
            math_violations=math_violations,
            param_adjustments=adjustments,
            message=f"Pre-generation: {len(adjustments)} param adjustments applied",
        )

        self._checkpoint_log.append(result)
        if self.verbose:
            print(result.summary())
        return result

    # ── Checkpoint 2: MID-GENERATION ─────────────────────────────────────────

    def mid_generation(
        self,
        iteration: int,
        partial_image: Image.Image,
        step: int,
        total_steps: int,
    ) -> CheckpointResult:
        """Run during multi-step generation (e.g., after each animation frame).

        Provides early warning if the generation is going off-track.
        """
        # Quick quality check on partial result
        q_score = self._quick_quality_check(partial_image)

        # If quality is very low at early steps, suggest retry
        progress = step / max(total_steps, 1)
        if q_score < 0.2 and progress < 0.5:
            decision = CheckpointDecision.RETRY
            message = f"Mid-generation quality too low ({q_score:.2f}) at step {step}/{total_steps}"
        else:
            decision = CheckpointDecision.CONTINUE
            message = f"Mid-generation step {step}/{total_steps}: quality={q_score:.2f}"

        result = CheckpointResult(
            stage=CheckpointStage.MID_GENERATION,
            iteration=iteration,
            decision=decision,
            quality_score=q_score,
            knowledge_score=1.0,
            math_score=1.0,
            sprite_ref_score=1.0,
            message=message,
            image=partial_image,
        )

        if self.verbose:
            print(result.summary())
        return result

    # ── Checkpoint 3: POST-GENERATION ────────────────────────────────────────

    def post_generation(
        self,
        iteration: int,
        image: Image.Image,
        params: dict[str, float],
    ) -> CheckpointResult:
        """Run after asset generation.

        Scores the final asset against:
          - Quality metrics (sharpness, contrast, palette adherence)
          - Knowledge rule compliance
          - Math model compliance
          - Sprite reference similarity
        """
        # 1. Quality score
        q_score = self._evaluate_quality(image)

        # 2. Knowledge compliance
        k_score, k_violations = self._check_all_knowledge(image, params)

        # 3. Math model compliance
        m_score, m_violations = self._check_all_math_models(params)

        # 4. Sprite reference similarity
        ref_score = self._compare_to_references(image)

        # Combined score
        combined = q_score * 0.50 + k_score * 0.30 + m_score * 0.20

        # Decision
        if combined >= self.target_score:
            decision = CheckpointDecision.STOP
            message = f"Quality target reached: {combined:.3f} >= {self.target_score}"
        elif combined >= self.pass_threshold:
            decision = CheckpointDecision.CONTINUE
            message = f"Passing quality: {combined:.3f}"
        else:
            decision = CheckpointDecision.RETRY
            message = f"Below threshold: {combined:.3f} < {self.pass_threshold}"

        self._score_history.append(combined)

        result = CheckpointResult(
            stage=CheckpointStage.POST_GENERATION,
            iteration=iteration,
            decision=decision,
            quality_score=q_score,
            knowledge_score=k_score,
            math_score=m_score,
            sprite_ref_score=ref_score,
            knowledge_violations=k_violations,
            math_violations=m_violations,
            message=message,
            image=image,
        )

        self._checkpoint_log.append(result)
        if self.verbose:
            print(result.summary())
        return result

    # ── Checkpoint 4: ITERATION-END ───────────────────────────────────────────

    def iteration_end(
        self,
        iteration: int,
        image: Image.Image,
        score: float,
    ) -> CheckpointResult:
        """Run at the end of each iteration.

        Checks for stagnation and decides whether to continue or escalate.
        """
        guard = self._get_stagnation_guard()
        stagnation_event = guard.update(iteration, score, image)

        if stagnation_event is not None:
            from mathart.evolution.stagnation import EscalationLevel
            if stagnation_event.escalation == EscalationLevel.HUMAN_REQUIRED:
                decision = CheckpointDecision.STOP
                message  = f"Stagnation: {stagnation_event.cause.value} — human review required"
            else:
                decision = CheckpointDecision.ESCALATE
                message  = f"Stagnation: {stagnation_event.cause.value} — auto-recovering"
        elif score >= self.target_score:
            decision = CheckpointDecision.STOP
            message  = f"Target score reached: {score:.3f}"
        else:
            decision = CheckpointDecision.CONTINUE
            message  = f"Iteration {iteration} complete: score={score:.3f}"

        result = CheckpointResult(
            stage=CheckpointStage.ITERATION_END,
            iteration=iteration,
            decision=decision,
            quality_score=score,
            knowledge_score=1.0,
            math_score=1.0,
            sprite_ref_score=1.0,
            message=message,
            image=image,
        )

        self._checkpoint_log.append(result)
        if self.verbose:
            print(result.summary())
        return result

    # ── Public utilities ──────────────────────────────────────────────────────

    def get_score_trend(self) -> dict:
        """Return score trend statistics."""
        if len(self._score_history) < 2:
            return {"trend": "insufficient_data", "scores": self._score_history}
        scores = self._score_history
        delta = scores[-1] - scores[0]
        recent_delta = scores[-1] - scores[max(0, len(scores)-3)]
        return {
            "trend":        "improving" if delta > 0.01 else ("stagnant" if abs(delta) < 0.01 else "declining"),
            "total_delta":  round(delta, 4),
            "recent_delta": round(recent_delta, 4),
            "best_score":   round(max(scores), 4),
            "latest_score": round(scores[-1], 4),
            "iterations":   len(scores),
            "scores":       [round(s, 4) for s in scores],
        }

    def get_checkpoint_log(self) -> list[CheckpointResult]:
        return list(self._checkpoint_log)

    def reset(self) -> None:
        """Reset iteration state (keep knowledge and math registry)."""
        self._score_history.clear()
        self._checkpoint_log.clear()
        if self._stagnation:
            self._stagnation.reset()

    def status_report(self) -> str:
        """Generate a human-readable status report."""
        trend = self.get_score_trend()
        lines = [
            "## ArtMathQualityController Status",
            f"- Knowledge rules loaded: {len(self._knowledge)}",
            f"- Math models loaded: {len(self._math_registry)}",
            f"- Sprite references: {self._sprite_lib.count() if self._sprite_lib else 0}",
            f"- Score trend: {trend.get('trend', 'N/A')}",
            f"- Best score: {trend.get('best_score', 0.0):.3f}",
            f"- Latest score: {trend.get('latest_score', 0.0):.3f}",
            f"- Iterations: {trend.get('iterations', 0)}",
        ]
        return "\n".join(lines)

    # ── Internal evaluation helpers ───────────────────────────────────────────

    def _quick_quality_check(self, image: Image.Image) -> float:
        """Fast quality check for mid-generation monitoring."""
        arr = np.array(image.convert("L"), dtype=float)
        # Sharpness via Laplacian variance
        if arr.size < 4:
            return 0.5
        lap = np.array([
            [0, -1, 0],
            [-1, 4, -1],
            [0, -1, 0],
        ], dtype=float)
        from scipy.ndimage import convolve
        response = convolve(arr, lap)
        sharpness = float(np.clip(response.var() / 1000.0, 0, 1))
        return sharpness

    def _evaluate_quality(self, image: Image.Image) -> float:
        """Full quality evaluation using AssetEvaluator."""
        evaluator = self._get_evaluator()
        if evaluator is None:
            return self._quick_quality_check(image)
        result = evaluator.evaluate(image)
        return result.overall_score

    def _check_all_knowledge(
        self,
        image: Image.Image,
        params: dict[str, float],
    ) -> tuple[float, list[KnowledgeViolation]]:
        """Check image and params against all knowledge rules."""
        violations: list[KnowledgeViolation] = []
        arr = np.array(image.convert("RGBA"))

        for rule in self._knowledge:
            viol = self._check_knowledge_rule_image(rule, arr, params)
            if viol:
                violations.append(viol)

        score = max(0.0, 1.0 - sum(v.severity for v in violations) / max(len(self._knowledge), 1))
        return float(score), violations

    def _check_all_math_models(
        self,
        params: dict[str, float],
    ) -> tuple[float, list[MathViolation]]:
        """Check params against all math model constraints."""
        violations: list[MathViolation] = []

        for model_name, constraints in self._math_registry.items():
            for param, (lo, hi) in constraints.items():
                if param in params:
                    val = params[param]
                    if val < lo or val > hi:
                        severity = abs(val - float(np.clip(val, lo, hi))) / max(hi - lo, 1e-6)
                        violations.append(MathViolation(
                            model_name=model_name,
                            param_name=param,
                            actual=val,
                            expected_lo=lo,
                            expected_hi=hi,
                            severity=min(1.0, severity),
                        ))

        score = max(0.0, 1.0 - sum(v.severity for v in violations) * 0.2)
        return float(score), violations

    def _compare_to_references(self, image: Image.Image) -> float:
        """Compare image to best sprite references."""
        lib = self._get_sprite_lib()
        if lib is None or lib.count() == 0:
            return 0.5  # Neutral when no references

        refs = lib.get_best_references(top_n=3)
        if not refs:
            return 0.5

        # Compare color profile similarity
        arr = np.array(image.convert("RGB"))
        img_mean = arr.mean(axis=(0, 1))

        scores = []
        for ref in refs:
            if ref.color.palette:
                ref_mean = np.mean(ref.color.palette, axis=0)
                diff = np.abs(img_mean - ref_mean).mean() / 255.0
                scores.append(1.0 - diff)

        return float(np.mean(scores)) if scores else 0.5

    def _check_knowledge_rule(
        self,
        rule: dict,
        params: dict[str, float],
    ) -> Optional[KnowledgeViolation]:
        """Check a single knowledge rule against params."""
        param = rule.get("param")
        if not param or param not in params:
            return None

        val = params[param]
        lo  = rule.get("min", -1e9)
        hi  = rule.get("max",  1e9)

        if val < lo or val > hi:
            severity = abs(val - float(np.clip(val, lo, hi))) / max(hi - lo, 1e-6)
            return KnowledgeViolation(
                rule_id=rule.get("id", "unknown"),
                rule_text=rule.get("text", ""),
                severity=min(1.0, severity),
                suggestion=f"Adjust {param} to [{lo}, {hi}]",
            )
        return None

    def _check_knowledge_rule_image(
        self,
        rule: dict,
        arr: np.ndarray,
        params: dict[str, float],
    ) -> Optional[KnowledgeViolation]:
        """Check a knowledge rule against an image array."""
        rule_type = rule.get("type", "param")

        if rule_type == "param":
            return self._check_knowledge_rule(rule, params)

        if rule_type == "color_count":
            # Check number of unique colors
            alpha = arr[:, :, 3] if arr.shape[2] == 4 else None
            mask = alpha > 10 if alpha is not None else np.ones(arr.shape[:2], bool)
            rgb = arr[mask, :3]
            unique_colors = len(np.unique(rgb.reshape(-1, 3), axis=0))
            lo = rule.get("min", 0)
            hi = rule.get("max", 256)
            if unique_colors < lo or unique_colors > hi:
                severity = abs(unique_colors - np.clip(unique_colors, lo, hi)) / max(hi - lo, 1)
                return KnowledgeViolation(
                    rule_id=rule.get("id", "color_count"),
                    rule_text=rule.get("text", f"Color count should be {lo}-{hi}"),
                    severity=min(1.0, severity / 10),
                    suggestion=f"Reduce palette to {lo}-{hi} colors (current: {unique_colors})",
                )

        return None

    def _get_sprite_constraints(self) -> dict[str, tuple[float, float]]:
        """Get merged constraints from sprite library."""
        lib = self._get_sprite_lib()
        if lib is None:
            return {}
        return lib.export_constraints()

    # ── Lazy component loading ─────────────────────────────────────────────────

    def _get_evaluator(self):
        """Lazy-load the AssetEvaluator."""
        if self._evaluator is None:
            try:
                from mathart.evaluator.evaluator import AssetEvaluator
                self._evaluator = AssetEvaluator()
            except ImportError:
                pass
        return self._evaluator

    def _get_stagnation_guard(self):
        """Lazy-load the StagnationGuard."""
        if self._stagnation is None:
            try:
                from mathart.evolution.stagnation import StagnationGuard
                self._stagnation = StagnationGuard(use_llm=False, verbose=self.verbose)
                self._stagnation.reset()
            except ImportError:
                pass
        return self._stagnation

    def _get_sprite_lib(self):
        """Lazy-load the SpriteLibrary."""
        if self._sprite_lib is None:
            try:
                from mathart.sprite.library import SpriteLibrary
                self._sprite_lib = SpriteLibrary(project_root=self.root)
            except ImportError:
                pass
        return self._sprite_lib

    # ── Knowledge loading ──────────────────────────────────────────────────────

    def _load_knowledge(self) -> list[dict]:
        """Load distilled knowledge rules from knowledge/ directory."""
        rules: list[dict] = []
        knowledge_dir = self.root / "knowledge"
        if not knowledge_dir.exists():
            return rules

        for md_file in knowledge_dir.glob("*.md"):
            rules.extend(self._parse_knowledge_file(md_file))

        return rules

    def _parse_knowledge_file(self, filepath: Path) -> list[dict]:
        """Parse a knowledge markdown file into structured rules."""
        rules: list[dict] = []
        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception:
            return rules

        # Extract rules with numeric constraints
        # Pattern: "param_name: min=X max=Y" or "param_name in [X, Y]"
        param_pattern = re.compile(
            r'(?:param|constraint):\s*(\w+)\s+(?:in\s+\[|min=)([0-9.+-]+)[,\s]+(?:max=)?([0-9.+-]+)',
            re.IGNORECASE,
        )
        for match in param_pattern.finditer(text):
            param, lo, hi = match.group(1), float(match.group(2)), float(match.group(3))
            rules.append({
                "id":    f"{filepath.stem}_{param}",
                "text":  f"{param} should be in [{lo}, {hi}]",
                "type":  "param",
                "param": param,
                "min":   lo,
                "max":   hi,
                "source": filepath.name,
            })

        # Extract color count rules
        color_pattern = re.compile(
            r'(?:color|palette)\s+(?:count|size).*?(\d+).*?(?:to|-).*?(\d+)',
            re.IGNORECASE,
        )
        for match in color_pattern.finditer(text):
            lo, hi = int(match.group(1)), int(match.group(2))
            if 1 <= lo < hi <= 256:
                rules.append({
                    "id":    f"{filepath.stem}_color_count",
                    "text":  f"Palette should have {lo}-{hi} colors",
                    "type":  "color_count",
                    "min":   lo,
                    "max":   hi,
                    "source": filepath.name,
                })

        return rules

    def _load_math_registry(self) -> dict[str, dict[str, tuple[float, float]]]:
        """Load math model constraints from the registry."""
        registry: dict[str, dict[str, tuple[float, float]]] = {}
        try:
            from mathart.evolution.math_registry import MathModelRegistry
            reg = MathModelRegistry(project_root=self.root)
            for model in reg.list_models():
                info = reg.get_model(model)
                if info and hasattr(info, "param_ranges"):
                    registry[model] = info.param_ranges
        except (ImportError, Exception):
            pass
        return registry
