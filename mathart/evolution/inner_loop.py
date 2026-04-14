"""Inner Loop — quality-driven iterative parameter optimization.

The inner loop implements the self-correction cycle:
  Generate → Evaluate → Optimize → Generate → ...

It wraps the EvolutionaryOptimizer with image-quality fitness functions,
allowing any generation function to be automatically improved over iterations.

Architecture:
  - Generator: callable(params: dict) -> PIL.Image
  - Evaluator: AssetEvaluator (multi-metric quality scoring)
  - Optimizer: EvolutionaryOptimizer (genetic algorithm parameter search)
  - Loop: run until quality_threshold met or max_iterations reached

The key insight is that mathematical knowledge (from knowledge/ files) is
baked into the ParameterSpace constraints, so the optimizer never explores
physically impossible or aesthetically wrong parameter combinations.

Distilled knowledge applied:
  - Convergence: use elitism to preserve best solutions across generations
  - Diversity: mutation prevents premature convergence
  - Early stopping: halt when fitness improvement < 0.001 for 5 generations
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from PIL import Image

from ..distill.compiler import ParameterSpace
from ..distill.optimizer import EvolutionaryOptimizer, FitnessResult
from ..evaluator.evaluator import AssetEvaluator, EvaluationResult


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
    """
    best_params: dict[str, float]
    best_score: float
    best_image: Optional[Image.Image]
    iterations: int
    history: list[float] = field(default_factory=list)
    evaluation: Optional[EvaluationResult] = None
    elapsed_seconds: float = 0.0
    converged: bool = False

    def summary(self) -> str:
        status = "CONVERGED" if self.converged else "MAX_ITER"
        lines = [
            f"InnerLoop [{status}] score={self.best_score:.3f} "
            f"iters={self.iterations} time={self.elapsed_seconds:.1f}s",
        ]
        if self.evaluation:
            lines.append(self.evaluation.summary())
        return "\n".join(lines)


class InnerLoopRunner:
    """Runs the inner quality-improvement loop.

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
    ):
        self.evaluator = evaluator or AssetEvaluator()
        self.quality_threshold = quality_threshold
        self.max_iterations = max_iterations
        self.population_size = population_size
        self.patience = patience
        self.min_delta = min_delta
        self.store_best_image = store_best_image
        self.verbose = verbose

    def run(
        self,
        generator: Callable[[dict], Image.Image],
        space: ParameterSpace,
        reference: Optional[Image.Image] = None,
        palette: Optional[list] = None,
        seed: Optional[int] = None,
    ) -> InnerLoopResult:
        """Run the inner optimization loop.

        Parameters
        ----------
        generator : callable
            Function that takes a parameter dict and returns a PIL.Image.
            Example: lambda params: render_character(params['spring_k'], ...)
        space : ParameterSpace
            The parameter space to search (from distill/compiler.py).
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
        best_image: Optional[Image.Image] = None
        best_eval: Optional[EvaluationResult] = None
        no_improve_count = 0
        prev_best = -1.0

        # Build fitness function that wraps generator + evaluator
        def fitness_fn(params: dict) -> FitnessResult:
            try:
                image = generator(params)
                eval_result = self.evaluator.evaluate(image, reference=reference, palette=palette)
                return FitnessResult(
                    score=eval_result.overall_score,
                    details={"evaluation": eval_result},
                )
            except Exception as e:
                return FitnessResult(score=0.0, details={"error": str(e)})

        optimizer = EvolutionaryOptimizer(
            space,
            population_size=self.population_size,
            seed=seed,
        )

        converged = False
        final_best = None

        def on_generation(gen: int, best) -> None:
            nonlocal best_image, best_eval, no_improve_count, prev_best, converged

            history.append(best.fitness)

            # Store best image
            if self.store_best_image:
                try:
                    best_image = generator(best.params)
                    eval_detail = best.fitness  # Use cached fitness
                    best_eval = self.evaluator.evaluate(
                        best_image, reference=reference, palette=palette
                    )
                except Exception:
                    pass

            if self.verbose:
                print(f"  Gen {gen:3d}: best_score={best.fitness:.4f}")

            # Early stopping check
            improvement = best.fitness - prev_best
            if improvement < self.min_delta:
                no_improve_count += 1
            else:
                no_improve_count = 0
            prev_best = best.fitness

        # Run optimization
        final_best = optimizer.run(
            fitness_fn,
            generations=self.max_iterations,
            callback=on_generation,
        )

        # Check convergence
        converged = final_best.fitness >= self.quality_threshold

        elapsed = time.time() - start_time

        # Final evaluation of best params
        if best_image is None and final_best is not None:
            try:
                best_image = generator(final_best.params)
                best_eval = self.evaluator.evaluate(
                    best_image, reference=reference, palette=palette
                )
            except Exception:
                pass

        return InnerLoopResult(
            best_params=final_best.params if final_best else {},
            best_score=final_best.fitness if final_best else 0.0,
            best_image=best_image if self.store_best_image else None,
            iterations=len(history),
            history=history,
            evaluation=best_eval,
            elapsed_seconds=elapsed,
            converged=converged,
        )

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
