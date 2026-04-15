"""Finite-difference gradient optimizer for CPU-only inner-loop acceleration.

TASK-013 implementation.

This module provides a lightweight gradient-based optimizer that uses central
finite differences to approximate gradients. It is designed to work alongside
the existing EvolutionaryOptimizer as a **local refinement** step:

    1. The evolutionary optimizer (GA/adaptive_ga/cma_es_like) performs global
       exploration to find promising regions.
    2. The FD gradient optimizer refines the best candidate(s) using gradient
       descent, which converges faster in smooth regions.

This hybrid approach is well-established in optimization literature (see
"Memetic Algorithms" / Moscato 1989) and provides 2-10x speedup for
parameter spaces of 10-50 dimensions.

Key design decisions:
  - Pure NumPy — no PyTorch, JAX, or autograd dependency
  - Central differences for better accuracy (O(h²) vs O(h) for forward)
  - Adaptive step size per parameter dimension
  - Respects parameter space constraints (clamping)
  - Integrates with existing ParameterSpace and FitnessFunction interfaces

References:
  - Nocedal & Wright, "Numerical Optimization", 2006
  - Moscato, "On Evolution, Search, Optimization, Genetic Algorithms and
    Martial Arts", 1989 (Memetic Algorithms)
  - Berahas et al., "A theoretical and empirical comparison of gradient
    approximations in derivative-free optimization", 2019
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .compiler import ParameterSpace
from .optimizer import FitnessFunction, FitnessResult, Individual


@dataclass
class FDGradientConfig:
    """Configuration for the finite-difference gradient optimizer.

    Parameters
    ----------
    learning_rate : float
        Initial learning rate for gradient descent. Decays over iterations.
    h : float
        Perturbation size for finite differences. Automatically scaled per
        parameter based on the parameter range span.
    max_steps : int
        Maximum number of gradient descent steps per refinement call.
    min_improvement : float
        Minimum improvement per step before early stopping.
    lr_decay : float
        Learning rate decay factor per step (multiplicative).
    momentum : float
        Momentum coefficient (0 = no momentum, 0.9 = heavy momentum).
    adaptive_h : bool
        Whether to adapt h per parameter based on gradient magnitude.
    gradient_clip : float
        Maximum gradient norm (for stability). 0 = no clipping.
    """
    learning_rate: float = 0.01
    h: float = 1e-4
    max_steps: int = 30
    min_improvement: float = 1e-6
    lr_decay: float = 0.97
    momentum: float = 0.8
    adaptive_h: bool = True
    gradient_clip: float = 1.0


@dataclass
class FDRefinementResult:
    """Result of a finite-difference gradient refinement."""
    initial_fitness: float
    final_fitness: float
    steps_taken: int
    gradient_norms: list[float] = field(default_factory=list)
    fitness_history: list[float] = field(default_factory=list)
    converged: bool = False

    @property
    def improvement(self) -> float:
        return self.final_fitness - self.initial_fitness

    def summary(self) -> str:
        status = "converged" if self.converged else "max_steps"
        return (
            f"FD-Gradient [{status}]: {self.initial_fitness:.4f} → "
            f"{self.final_fitness:.4f} (+{self.improvement:.4f}) "
            f"in {self.steps_taken} steps"
        )


class FDGradientOptimizer:
    """Finite-difference gradient optimizer for parameter space refinement.

    This optimizer uses central finite differences to approximate the gradient
    of a fitness function with respect to parameter values, then applies
    gradient ascent (since we maximize fitness) with momentum.

    Usage
    -----
    >>> from mathart.distill.fd_gradient import FDGradientOptimizer, FDGradientConfig
    >>> from mathart.distill.compiler import ParameterSpace
    >>> optimizer = FDGradientOptimizer(space, config=FDGradientConfig())
    >>> result = optimizer.refine(individual, fitness_fn)
    >>> print(result.summary())
    """

    def __init__(
        self,
        space: ParameterSpace,
        config: Optional[FDGradientConfig] = None,
        verbose: bool = False,
    ):
        self.space = space
        self.config = config or FDGradientConfig()
        self.verbose = verbose
        self._param_names: list[str] = list(space.get_ranges().keys())
        self._ranges: dict[str, tuple[float, float]] = space.get_ranges()
        self._ndim = len(self._param_names)
        # Per-parameter step sizes, scaled by range span
        self._h_per_param = self._compute_initial_h()

    def _compute_initial_h(self) -> dict[str, float]:
        """Compute initial perturbation sizes scaled by parameter range."""
        h_map = {}
        for name, (lo, hi) in self._ranges.items():
            span = max(abs(hi - lo), 1e-12)
            # h is a fraction of the span, but never too small
            h_map[name] = max(self.config.h * span, 1e-8)
        return h_map

    def refine(
        self,
        individual: Individual,
        fitness_fn: FitnessFunction,
    ) -> FDRefinementResult:
        """Refine an individual using gradient ascent with finite differences.

        Parameters
        ----------
        individual : Individual
            The starting point (typically the best from evolutionary search).
        fitness_fn : FitnessFunction
            The fitness function to maximize.

        Returns
        -------
        FDRefinementResult
            The result of the refinement, including the updated individual.
        """
        params = dict(individual.params)
        current_fitness = self._evaluate(params, fitness_fn)
        initial_fitness = current_fitness

        lr = self.config.learning_rate
        velocity = {name: 0.0 for name in self._param_names}
        gradient_norms = []
        fitness_history = [current_fitness]
        steps_taken = 0
        converged = False

        for step in range(self.config.max_steps):
            # Compute gradient via central finite differences
            gradient = self._compute_gradient(params, fitness_fn)

            # Compute gradient norm
            grad_norm = math.sqrt(sum(g * g for g in gradient.values()))
            gradient_norms.append(grad_norm)

            # Gradient clipping
            if self.config.gradient_clip > 0 and grad_norm > self.config.gradient_clip:
                scale = self.config.gradient_clip / (grad_norm + 1e-12)
                gradient = {k: v * scale for k, v in gradient.items()}

            # Update with momentum (gradient ASCENT — we maximize fitness)
            new_params = {}
            for name in self._param_names:
                v = self.config.momentum * velocity[name] + lr * gradient.get(name, 0.0)
                velocity[name] = v
                new_params[name] = params[name] + v

            # Enforce constraints
            new_params = self._clamp(new_params)

            # Evaluate new position
            new_fitness = self._evaluate(new_params, fitness_fn)
            fitness_history.append(new_fitness)
            steps_taken = step + 1

            # Accept or reject step
            if new_fitness >= current_fitness:
                improvement = new_fitness - current_fitness
                params = new_params
                current_fitness = new_fitness

                if improvement < self.config.min_improvement:
                    converged = True
                    if self.verbose:
                        print(f"  [FD] Step {step}: converged (Δ={improvement:.6f})")
                    break
            else:
                # Step was bad — reduce learning rate and try smaller step
                lr *= 0.5
                velocity = {name: 0.0 for name in self._param_names}
                if lr < 1e-8:
                    converged = True
                    if self.verbose:
                        print(f"  [FD] Step {step}: lr too small, stopping")
                    break

            # Adaptive h: increase h for near-zero gradients
            if self.config.adaptive_h:
                self._adapt_h(gradient)

            # Learning rate decay
            lr *= self.config.lr_decay

            if self.verbose and step % 5 == 0:
                print(
                    f"  [FD] Step {step}: fitness={current_fitness:.4f} "
                    f"grad_norm={grad_norm:.4f} lr={lr:.6f}"
                )

        # Update the individual in-place
        individual.params = params
        individual.fitness = current_fitness

        return FDRefinementResult(
            initial_fitness=initial_fitness,
            final_fitness=current_fitness,
            steps_taken=steps_taken,
            gradient_norms=gradient_norms,
            fitness_history=fitness_history,
            converged=converged,
        )

    def _compute_gradient(
        self,
        params: dict[str, float],
        fitness_fn: FitnessFunction,
    ) -> dict[str, float]:
        """Compute gradient using central finite differences.

        For each parameter p_i:
            grad_i ≈ (f(x + h*e_i) - f(x - h*e_i)) / (2*h)

        This requires 2*D function evaluations where D is the number of
        parameters.
        """
        gradient = {}
        for name in self._param_names:
            h = self._h_per_param[name]
            lo, hi = self._ranges[name]

            # Forward perturbation
            params_plus = dict(params)
            params_plus[name] = min(params[name] + h, hi)

            # Backward perturbation
            params_minus = dict(params)
            params_minus[name] = max(params[name] - h, lo)

            # Actual step size (may be smaller near boundaries)
            actual_h = params_plus[name] - params_minus[name]
            if actual_h < 1e-12:
                gradient[name] = 0.0
                continue

            f_plus = self._evaluate(params_plus, fitness_fn)
            f_minus = self._evaluate(params_minus, fitness_fn)
            gradient[name] = (f_plus - f_minus) / actual_h

        return gradient

    def _adapt_h(self, gradient: dict[str, float]) -> None:
        """Adapt per-parameter step sizes based on gradient magnitude."""
        for name, g in gradient.items():
            span = self._ranges[name][1] - self._ranges[name][0]
            base_h = self.config.h * span
            if abs(g) < 1e-8:
                # Near-zero gradient: increase h to get a better signal
                self._h_per_param[name] = min(base_h * 10, span * 0.1)
            else:
                # Healthy gradient: use standard h
                self._h_per_param[name] = max(base_h, 1e-8)

    def _clamp(self, params: dict[str, float]) -> dict[str, float]:
        """Clamp parameters to valid ranges."""
        clamped = {}
        for name, value in params.items():
            if name in self._ranges:
                lo, hi = self._ranges[name]
                clamped[name] = max(lo, min(hi, value))
            else:
                clamped[name] = value
        return clamped

    @staticmethod
    def _evaluate(params: dict[str, float], fitness_fn: FitnessFunction) -> float:
        """Evaluate fitness and return the scalar score."""
        result = fitness_fn(params)
        return result.score


def hybrid_optimize(
    space: ParameterSpace,
    fitness_fn: FitnessFunction,
    individual: Individual,
    fd_config: Optional[FDGradientConfig] = None,
    verbose: bool = False,
) -> tuple[Individual, FDRefinementResult]:
    """Convenience function: refine an individual with FD gradient descent.

    Intended to be called after evolutionary search has found a good starting
    point. Returns the refined individual and the refinement result.

    Parameters
    ----------
    space : ParameterSpace
        The parameter space.
    fitness_fn : FitnessFunction
        The fitness function to maximize.
    individual : Individual
        The starting point.
    fd_config : FDGradientConfig, optional
        Configuration overrides.
    verbose : bool
        Print progress.

    Returns
    -------
    tuple[Individual, FDRefinementResult]
    """
    optimizer = FDGradientOptimizer(space, config=fd_config, verbose=verbose)
    result = optimizer.refine(individual, fitness_fn)
    return individual, result
