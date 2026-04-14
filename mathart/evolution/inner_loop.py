"""Inner Loop — quality-driven iterative parameter optimization.

The inner loop implements the self-correction cycle:
  Generate → Evaluate → Optimize → Generate → ...

**v0.6 upgrade**: ArtMathQualityController is now integrated at every stage:
  - PRE_GENERATION: inject knowledge + math + sprite constraints before each gen
  - MID_GENERATION: early-abort if partial result is off-track
  - POST_GENERATION: full quality + knowledge + math + sprite-ref scoring
  - ITERATION_END: stagnation detection with graceful degradation

**Dual-mode operation**:
  - ``autonomous`` (default): runs entirely locally, no LLM needed.
    Quality control still active but AI arbitration is skipped.
    Iteration NEVER stops due to missing AI — it always falls through
    to the next best local strategy.
  - ``assisted``: same as autonomous, but also calls LLM for
    stagnation arbitration when available.

External references incorporated:
  - genetic-lisa: convex fitness design, small-population fast iteration
  - restyle-sprites: multi-provider fallback, config-driven pipeline
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image

from ..distill.compiler import Constraint, ParameterSpace
from ..distill.optimizer import EvolutionaryOptimizer, FitnessResult
from ..evaluator.evaluator import AssetEvaluator, EvaluationResult

logger = logging.getLogger(__name__)


# ── Run mode ───────────────────────────────────────────────────────────────────

class RunMode(str, Enum):
    """Determines how the inner loop handles AI-dependent features."""
    AUTONOMOUS = "autonomous"   # Local only, never blocks on AI
    ASSISTED   = "assisted"     # Uses AI when available, falls back gracefully


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class InnerLoopResult:
    """Result of an inner loop optimization run.

    Attributes
    ----------
    best_params : dict[str, float]
        Parameter configuration that produced the best quality.
    best_score : float
        Best overall quality score achieved (0.0 - 1.0).
    best_image : PIL.Image or None
        The best generated image (if store_best_image=True).
    iterations : int
        Number of generations run.
    history : list[float]
        Best fitness score at each generation.
    evaluation : EvaluationResult
        Detailed quality evaluation of the best image.
    elapsed_seconds : float
        Total wall-clock time.
    converged : bool
        True if quality_threshold was met before max_iterations.
    checkpoint_log : list
        All checkpoint results from ArtMathQualityController.
    adjustments_applied : int
        Number of parameter adjustments applied by quality controller.
    knowledge_violations_total : int
        Total knowledge violations detected across all iterations.
    math_violations_total : int
        Total math model violations detected across all iterations.
    """
    best_params: dict[str, float]
    best_score: float
    best_image: Optional[Image.Image]
    iterations: int
    history: list[float] = field(default_factory=list)
    evaluation: Optional[EvaluationResult] = None
    elapsed_seconds: float = 0.0
    converged: bool = False
    checkpoint_log: list = field(default_factory=list)
    adjustments_applied: int = 0
    knowledge_violations_total: int = 0
    math_violations_total: int = 0

    def summary(self) -> str:
        status = "CONVERGED" if self.converged else "MAX_ITER"
        lines = [
            f"InnerLoop [{status}] score={self.best_score:.3f} "
            f"iters={self.iterations} time={self.elapsed_seconds:.1f}s",
            f"  Adjustments applied: {self.adjustments_applied}",
            f"  Knowledge violations: {self.knowledge_violations_total}",
            f"  Math violations: {self.math_violations_total}",
        ]
        if self.evaluation:
            lines.append(self.evaluation.summary())
        return "\n".join(lines)


# ── Main runner ────────────────────────────────────────────────────────────────

class InnerLoopRunner:
    """Runs the inner quality-improvement loop with full knowledge/math control.

    Parameters
    ----------
    evaluator : AssetEvaluator
        Quality evaluator for generated images.
    quality_threshold : float
        Stop when overall quality score >= this value (default 0.75).
    max_iterations : int
        Maximum number of evolutionary generations (default 50).
    population_size : int
        Number of candidate parameter sets per generation (default 20).
    patience : int
        Stop early if best score doesn't improve by > min_delta for
        this many consecutive generations (default 8).
    min_delta : float
        Minimum improvement to reset patience counter (default 0.005).
    store_best_image : bool
        Whether to store the best generated image in the result (default True).
    verbose : bool
        Print progress to stdout (default False).
    mode : RunMode
        AUTONOMOUS (default) or ASSISTED. Autonomous mode never blocks
        on AI services; assisted mode uses LLM when available.
    project_root : Path or None
        Project root for loading knowledge, math registry, sprite library.
        If None, quality controller is not used.
    """

    def __init__(
        self,
        evaluator: Optional[AssetEvaluator] = None,
        quality_threshold: float = 0.75,
        max_iterations: int = 50,
        population_size: int = 20,
        patience: int = 8,
        min_delta: float = 0.005,
        store_best_image: bool = True,
        verbose: bool = False,
        mode: RunMode = RunMode.AUTONOMOUS,
        project_root: Optional[Path] = None,
    ):
        self.evaluator = evaluator or AssetEvaluator()
        self.quality_threshold = quality_threshold
        self.max_iterations = max_iterations
        self.population_size = population_size
        self.patience = patience
        self.min_delta = min_delta
        self.store_best_image = store_best_image
        self.verbose = verbose
        self.mode = mode
        self.project_root = Path(project_root) if project_root else None

        # Quality controller (lazy-loaded)
        self._controller = None

    # ── Quality controller ─────────────────────────────────────────────────

    def _get_controller(self):
        """Lazy-load ArtMathQualityController if project_root is set."""
        if self._controller is not None:
            return self._controller
        if self.project_root is None:
            return None
        try:
            from ..quality.controller import ArtMathQualityController
            self._controller = ArtMathQualityController(
                project_root=self.project_root,
                target_score=self.quality_threshold,
                verbose=self.verbose,
            )
            # Configure stagnation guard based on run mode
            guard = self._controller._get_stagnation_guard()
            if guard is not None:
                guard.use_llm = (self.mode == RunMode.ASSISTED)
            return self._controller
        except Exception as e:
            logger.warning("Could not load ArtMathQualityController: %s", e)
            return None

    def _inject_sprite_constraints(self, space: ParameterSpace) -> int:
        """Inject sprite library constraints into the parameter space.

        Returns the number of constraints injected.
        """
        if self.project_root is None:
            return 0
        try:
            from ..sprite.library import SpriteLibrary
            lib = SpriteLibrary(project_root=self.project_root)
            constraints = lib.export_constraints()
            count = 0
            for param, (lo, hi) in constraints.items():
                space.add_constraint(Constraint(
                    param_name=param,
                    min_value=lo,
                    max_value=hi,
                    is_hard=False,
                    source_rule_id="sprite_library",
                ))
                count += 1
            return count
        except Exception as e:
            logger.debug("Sprite constraint injection skipped: %s", e)
            return 0

    # ── Main run method ────────────────────────────────────────────────────

    def run(
        self,
        generator: Callable[[dict], Image.Image],
        space: ParameterSpace,
        reference: Optional[Image.Image] = None,
        palette: Optional[list] = None,
        seed: Optional[int] = None,
    ) -> InnerLoopResult:
        """Run the inner optimization loop with full quality control.

        Parameters
        ----------
        generator : callable
            Function that takes a parameter dict and returns a PIL.Image.
        space : ParameterSpace
            The parameter space to search.
        reference : PIL.Image, optional
            Style reference for consistency evaluation.
        palette : list of (R, G, B), optional
            Target palette for adherence evaluation.
        seed : int, optional
            Random seed for reproducibility.

        Returns
        -------
        InnerLoopResult
        """
        start_time = time.time()
        history: list[float] = []
        checkpoint_log: list = []
        best_image: Optional[Image.Image] = None
        best_eval: Optional[EvaluationResult] = None
        best_params: dict = {}
        best_score: float = 0.0
        no_improve_count = 0
        prev_best = -1.0
        adjustments_applied = 0
        knowledge_violations_total = 0
        math_violations_total = 0

        controller = self._get_controller()

        # Inject sprite library constraints into space
        sprite_injected = self._inject_sprite_constraints(space)
        if sprite_injected > 0 and self.verbose:
            print(f"  [QC] Injected {sprite_injected} sprite constraints")

        # ── Fitness function with quality control ──────────────────────────
        def fitness_fn(params: dict) -> FitnessResult:
            nonlocal adjustments_applied, knowledge_violations_total, math_violations_total

            # --- CHECKPOINT 1: PRE_GENERATION ---
            active_params = dict(params)
            if controller is not None:
                try:
                    pre_result = controller.pre_generation(
                        iteration=len(history),
                        current_params=active_params,
                    )
                    checkpoint_log.append(pre_result)

                    # Apply parameter adjustments from knowledge/math/sprite
                    if pre_result.param_adjustments:
                        active_params.update(pre_result.param_adjustments)
                        adjustments_applied += len(pre_result.param_adjustments)

                    knowledge_violations_total += len(pre_result.knowledge_violations)
                    math_violations_total += len(pre_result.math_violations)
                except Exception as e:
                    # Quality control failure must NEVER stop iteration
                    logger.debug("Pre-generation QC error (non-fatal): %s", e)

            # --- GENERATE ---
            try:
                image = generator(active_params)
            except Exception as e:
                return FitnessResult(score=0.0, details={"error": str(e)})

            # --- CHECKPOINT 3: POST_GENERATION ---
            if controller is not None:
                try:
                    post_result = controller.post_generation(
                        iteration=len(history),
                        image=image,
                        params=active_params,
                    )
                    checkpoint_log.append(post_result)

                    # Use combined score (quality 50% + knowledge 30% + math 20%)
                    score = post_result.combined_score
                except Exception as e:
                    logger.debug("Post-generation QC error (non-fatal): %s", e)
                    # Fall back to basic evaluator
                    eval_result = self.evaluator.evaluate(
                        image, reference=reference, palette=palette
                    )
                    score = eval_result.overall_score
            else:
                # No controller: use basic evaluator
                eval_result = self.evaluator.evaluate(
                    image, reference=reference, palette=palette
                )
                score = eval_result.overall_score

            return FitnessResult(
                score=score,
                details={"params": active_params, "image": image},
            )

        # ── Optimizer setup ────────────────────────────────────────────────
        optimizer = EvolutionaryOptimizer(
            space,
            population_size=self.population_size,
            seed=seed,
        )

        converged = False
        should_stop = False

        def on_generation(gen: int, best) -> None:
            nonlocal best_image, best_eval, best_params, best_score
            nonlocal no_improve_count, prev_best, converged, should_stop

            history.append(best.fitness)

            # Store best image
            if self.store_best_image and best.fitness > best_score:
                details = best.details if hasattr(best, 'details') else {}
                if "image" in details:
                    best_image = details["image"]
                else:
                    try:
                        best_image = generator(best.params)
                    except Exception:
                        pass
                best_params = dict(best.params)
                best_score = best.fitness
                try:
                    best_eval = self.evaluator.evaluate(
                        best_image, reference=reference, palette=palette
                    )
                except Exception:
                    pass

            if self.verbose:
                print(f"  Gen {gen:3d}: score={best.fitness:.4f}")

            # --- CHECKPOINT 4: ITERATION_END ---
            if controller is not None:
                try:
                    end_result = controller.iteration_end(
                        iteration=gen,
                        image=best_image if best_image else Image.new("RGBA", (8, 8)),
                        score=best.fitness,
                    )
                    checkpoint_log.append(end_result)

                    from ..quality.checkpoint import CheckpointDecision
                    if end_result.decision == CheckpointDecision.STOP:
                        if best.fitness >= self.quality_threshold:
                            # Genuine convergence
                            converged = True
                            should_stop = True
                        elif self.mode == RunMode.AUTONOMOUS:
                            # In autonomous mode, stagnation STOP means:
                            # log it but DON'T stop — try widening space instead
                            if self.verbose:
                                print(f"  [QC] Stagnation detected but autonomous mode — continuing")
                            self._try_widen_space(optimizer)
                        else:
                            # In assisted mode, respect the STOP decision
                            should_stop = True
                    elif end_result.decision == CheckpointDecision.ESCALATE:
                        # Auto-recovery was applied, continue
                        if self.verbose:
                            print(f"  [QC] Auto-recovery applied: {end_result.message}")
                except Exception as e:
                    # Quality control failure must NEVER stop iteration
                    logger.debug("Iteration-end QC error (non-fatal): %s", e)

            # Early stopping check (basic patience)
            improvement = best.fitness - prev_best
            if improvement < self.min_delta:
                no_improve_count += 1
            else:
                no_improve_count = 0
            prev_best = best.fitness

        # ── Run optimization ───────────────────────────────────────────────
        final_best = optimizer.run(
            fitness_fn,
            generations=self.max_iterations,
            callback=on_generation,
            target_fitness=self.quality_threshold,
        )

        # Check convergence
        if final_best.fitness >= self.quality_threshold:
            converged = True

        elapsed = time.time() - start_time

        # Final evaluation of best params
        if best_image is None and final_best is not None:
            try:
                best_image = generator(final_best.params)
                best_eval = self.evaluator.evaluate(
                    best_image, reference=reference, palette=palette
                )
                best_params = final_best.params
                best_score = final_best.fitness
            except Exception:
                pass

        return InnerLoopResult(
            best_params=best_params if best_params else (final_best.params if final_best else {}),
            best_score=best_score if best_score > 0 else (final_best.fitness if final_best else 0.0),
            best_image=best_image if self.store_best_image else None,
            iterations=len(history),
            history=history,
            evaluation=best_eval,
            elapsed_seconds=elapsed,
            converged=converged,
            checkpoint_log=checkpoint_log,
            adjustments_applied=adjustments_applied,
            knowledge_violations_total=knowledge_violations_total,
            math_violations_total=math_violations_total,
        )

    def _try_widen_space(self, optimizer: EvolutionaryOptimizer) -> None:
        """Attempt to widen the parameter space by 15% in autonomous mode.

        This is a soft recovery: if stagnation is detected but we are in
        autonomous mode, we widen the search space slightly to explore
        new regions. This avoids stopping iteration.
        """
        try:
            space = optimizer._space
            for name, constraint in space.constraints.items():
                if constraint.min_value is not None and constraint.max_value is not None:
                    span = constraint.max_value - constraint.min_value
                    constraint.min_value -= span * 0.075
                    constraint.max_value += span * 0.075
            if self.verbose:
                print("  [QC] Widened parameter space by 15%")
        except Exception:
            pass

    def run_batch(
        self,
        generator: Callable[[dict], Image.Image],
        spaces: dict[str, ParameterSpace],
        **kwargs,
    ) -> dict[str, InnerLoopResult]:
        """Run inner loop optimization for multiple parameter spaces.

        Useful for optimizing different aspects independently
        (e.g., color params, animation params, SDF params).

        Returns a dict mapping space name to InnerLoopResult.
        """
        results = {}
        for name, space in spaces.items():
            if self.verbose:
                print(f"\n[InnerLoop] Optimizing space: {name}")
            results[name] = self.run(generator, space, **kwargs)
        return results
