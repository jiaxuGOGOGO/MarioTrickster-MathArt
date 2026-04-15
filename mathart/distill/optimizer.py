"""Evolutionary search utilities for compiled parameter spaces.

This module provides a lightweight optimizer for the parameter spaces defined
by the compilation layer. The original implementation used a very small genetic
algorithm with fixed mutation settings. To better support high-dimensional
search, the optimizer now exposes two stronger built-in upgrade paths while
remaining dependency-light:

- ``ga``: the original fixed-rate genetic algorithm behaviour.
- ``adaptive_ga``: an adaptive genetic algorithm with diversity monitoring,
  stagnation-aware mutation schedules, elite-guided local search, and random
  immigrants.
- ``cma_es_like``: a diagonal-covariance, elite-guided sampler that provides a
  practical upgrade path toward CMA-ES style exploration without requiring
  external libraries.

The fitness function is pluggable. It can be a heuristic image-quality metric,
a constraint-satisfaction score, or a human feedback score.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from .compiler import ParameterSpace


@dataclass
class Individual:
    """A single candidate solution in the evolutionary search."""

    params: dict[str, float]
    fitness: float = 0.0

    def copy(self) -> "Individual":
        return Individual(params=dict(self.params), fitness=self.fitness)


@dataclass
class FitnessResult:
    """Result of a fitness evaluation."""

    score: float
    details: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.score >= 0.0


FitnessFunction = Callable[[dict[str, float]], FitnessResult]
SearchStrategy = Literal["ga", "adaptive_ga", "cma_es_like"]


class EvolutionaryOptimizer:
    """Evolutionary optimizer for compiled parameter spaces.

    Parameters
    ----------
    space : ParameterSpace
        The parameter space to search.
    population_size : int
        Number of individuals per generation.
    mutation_rate : float
        Baseline probability of mutating each parameter.
    mutation_strength : float
        Baseline mutation standard deviation as a fraction of the parameter span.
    crossover_rate : float
        Probability of crossover between parents.
    tournament_size : int
        Number of individuals in tournament selection.
    elite_count : int
        Number of top individuals preserved each generation.
    seed : int or None
        Random seed.
    strategy : {"ga", "adaptive_ga", "cma_es_like"}
        Search strategy. ``adaptive_ga`` is the default and provides better
        behaviour on larger parameter spaces while keeping the implementation
        lightweight.
    immigrant_ratio : float
        Fraction of each generation reserved for random immigrants when the
        search stagnates or diversity collapses.
    stagnation_window : int
        Number of generations without meaningful improvement before exploration
        pressure is increased.
    local_search_ratio : float
        Fraction of the population devoted to elite-guided local proposals.
    """

    def __init__(
        self,
        space: ParameterSpace,
        population_size: int = 50,
        mutation_rate: float = 0.1,
        mutation_strength: float = 0.2,
        crossover_rate: float = 0.7,
        tournament_size: int = 3,
        elite_count: int = 2,
        seed: Optional[int] = None,
        strategy: SearchStrategy = "adaptive_ga",
        immigrant_ratio: float = 0.1,
        stagnation_window: int = 8,
        local_search_ratio: float = 0.2,
    ):
        if strategy not in {"ga", "adaptive_ga", "cma_es_like"}:
            raise ValueError(f"Unsupported strategy: {strategy}")
        self.space = space
        self.population_size = max(2, population_size)
        self.mutation_rate = mutation_rate
        self.mutation_strength = mutation_strength
        self.crossover_rate = crossover_rate
        self.tournament_size = tournament_size
        self.elite_count = max(1, min(elite_count, self.population_size))
        self.strategy = strategy
        self.immigrant_ratio = max(0.0, min(0.9, immigrant_ratio))
        self.stagnation_window = max(1, stagnation_window)
        self.local_search_ratio = max(0.0, min(0.8, local_search_ratio))
        self.rng = random.Random(seed)
        self._population: list[Individual] = []
        self._generation = 0
        self._history: list[dict] = []
        self._current_mutation_rate = mutation_rate
        self._current_mutation_strength = mutation_strength
        self._stagnation = 0
        self._best_seen = float("-inf")

    # ── Public API ────────────────────────────────────────────────────

    def run(
        self,
        fitness_fn: FitnessFunction,
        generations: int = 100,
        target_fitness: Optional[float] = None,
        callback: Optional[Callable[[int, Individual], None]] = None,
    ) -> Individual:
        """Run the evolutionary optimization and return the best individual."""
        self._reset_run_state()
        self._initialize_population()
        self._evaluate(fitness_fn)

        for gen in range(generations):
            self._generation = gen
            current_best = self.best_individual.fitness
            diversity = self._compute_diversity()
            self._update_adaptive_controls(current_best, diversity)

            if self.strategy == "ga":
                immigrant_count = 0
                local_search_count = 0
                new_pop = self._generate_ga_population()
            else:
                immigrant_count = self._determine_immigrant_count(diversity)
                local_search_count = self._determine_local_search_count()
                new_pop = self._generate_adaptive_population(
                    immigrant_count=immigrant_count,
                    local_search_count=local_search_count,
                )

            self._population = new_pop
            self._evaluate(fitness_fn)

            best = self.best_individual
            self._history.append(
                {
                    "generation": gen,
                    "best_fitness": best.fitness,
                    "avg_fitness": sum(i.fitness for i in self._population) / len(self._population),
                    "diversity": self._compute_diversity(),
                    "mutation_rate": self._current_mutation_rate,
                    "mutation_strength": self._current_mutation_strength,
                    "stagnation": self._stagnation,
                    "strategy": self.strategy,
                    "immigrant_count": immigrant_count,
                    "local_search_count": local_search_count,
                }
            )

            if callback:
                callback(gen, best)

            if target_fitness is not None and best.fitness >= target_fitness:
                break

        return self.best_individual

    @property
    def best_individual(self) -> Individual:
        """Return the individual with highest fitness."""
        return max(self._population, key=lambda i: i.fitness)

    @property
    def history(self) -> list[dict]:
        """Return a copy of the optimization history."""
        return list(self._history)

    # ── Internal state management ─────────────────────────────────────

    def _reset_run_state(self) -> None:
        self._population = []
        self._generation = 0
        self._history = []
        self._current_mutation_rate = self.mutation_rate
        self._current_mutation_strength = self.mutation_strength
        self._stagnation = 0
        self._best_seen = float("-inf")

    def _initialize_population(self) -> None:
        """Create the initial population within the parameter ranges."""
        defaults = self.space.get_defaults()
        self._population = [Individual(params=dict(defaults))]
        while len(self._population) < self.population_size:
            self._population.append(self._random_individual())

    def _evaluate(self, fitness_fn: FitnessFunction) -> None:
        """Evaluate fitness for all individuals."""
        for ind in self._population:
            result = fitness_fn(ind.params)
            ind.fitness = result.score

    def _update_adaptive_controls(self, current_best: float, diversity: float) -> None:
        """Adjust exploration pressure based on stagnation and diversity."""
        if current_best > self._best_seen + 1e-9:
            self._best_seen = current_best
            self._stagnation = 0
        elif self._best_seen != float("-inf"):
            self._stagnation += 1

        if self.strategy == "ga":
            self._current_mutation_rate = self.mutation_rate
            self._current_mutation_strength = self.mutation_strength
            return

        stagnation_factor = 1.0 + 0.25 * max(0, self._stagnation - 1)
        diversity_pressure = max(0.0, 0.2 - diversity) / 0.2 if diversity < 0.2 else 0.0

        rate = self.mutation_rate * (1.0 + 0.75 * diversity_pressure) * stagnation_factor
        strength = self.mutation_strength * (1.0 + 1.25 * diversity_pressure) * (1.0 + 0.35 * self._stagnation)

        if self.strategy == "cma_es_like":
            strength *= 1.15

        self._current_mutation_rate = self._clamp(rate, 0.02, 0.9)
        self._current_mutation_strength = self._clamp(strength, 0.01, 1.5)

    # ── Population generation ─────────────────────────────────────────

    def _generate_ga_population(self) -> list[Individual]:
        """Reproduce the original fixed-rate GA behaviour."""
        new_pop = self._select_elite()
        while len(new_pop) < self.population_size:
            parent1 = self._tournament_select()
            parent2 = self._tournament_select()
            child = self._crossover(parent1, parent2)
            child = self._mutate(child)
            child = self._enforce_constraints(child)
            new_pop.append(child)
        return new_pop

    def _generate_adaptive_population(
        self,
        immigrant_count: int,
        local_search_count: int,
    ) -> list[Individual]:
        """Generate a new population with adaptive exploration controls."""
        new_pop = self._select_elite()
        elite_pool = self._top_individuals(max(self.elite_count, self.population_size // 5))
        reproduction_target = max(self.population_size - immigrant_count - local_search_count, len(new_pop))

        while len(new_pop) < reproduction_target:
            parent1 = self._tournament_select()
            parent2 = self._tournament_select()
            if self.strategy == "cma_es_like" and self.rng.random() < 0.5:
                child = self._sample_elite_guided_candidate(elite_pool, exploration_scale=1.0)
            else:
                child = self._crossover(parent1, parent2)
                child = self._mutate(child)
                if self.rng.random() < 0.35:
                    child = self._blend_toward_elites(child, elite_pool)
                child = self._enforce_constraints(child)
            new_pop.append(child)

        while len(new_pop) < self.population_size - immigrant_count:
            new_pop.append(
                self._sample_elite_guided_candidate(
                    elite_pool,
                    exploration_scale=1.0 + 0.15 * self._stagnation,
                )
            )

        for _ in range(immigrant_count):
            new_pop.append(self._random_individual())

        return new_pop[: self.population_size]

    def _random_individual(self) -> Individual:
        """Sample a random individual uniformly within parameter bounds."""
        params = {}
        for name, (lo, hi) in self.space.get_ranges().items():
            params[name] = self.rng.uniform(lo, hi)
        return Individual(params=params)

    def _tournament_select(self) -> Individual:
        """Select an individual via tournament selection."""
        candidates = self.rng.sample(
            self._population,
            min(self.tournament_size, len(self._population)),
        )
        return max(candidates, key=lambda i: i.fitness)

    def _select_elite(self) -> list[Individual]:
        """Preserve top individuals via elitism."""
        return [ind.copy() for ind in self._top_individuals(self.elite_count)]

    def _top_individuals(self, count: int) -> list[Individual]:
        sorted_pop = sorted(self._population, key=lambda i: i.fitness, reverse=True)
        return [ind.copy() for ind in sorted_pop[: max(1, min(count, len(sorted_pop)))]]

    def _crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        """Blend-aware uniform crossover between two parents."""
        if self.rng.random() > self.crossover_rate:
            return parent1.copy()

        child_params = {}
        for name in parent1.params:
            p1 = parent1.params[name]
            p2 = parent2.params.get(name, p1)
            roll = self.rng.random()
            if roll < 0.4:
                child_params[name] = p1
            elif roll < 0.8:
                child_params[name] = p2
            else:
                alpha = self.rng.random()
                child_params[name] = (1.0 - alpha) * p1 + alpha * p2
        return Individual(params=child_params)

    def _mutate(self, individual: Individual) -> Individual:
        """Apply Gaussian mutation using current adaptive mutation settings."""
        ranges = self.space.get_ranges()
        mutated = individual.copy()

        for name in mutated.params:
            if self.rng.random() < self._current_mutation_rate and name in ranges:
                lo, hi = ranges[name]
                span = hi - lo
                delta = self.rng.gauss(0, self._current_mutation_strength * span)
                mutated.params[name] = mutated.params[name] + delta

        return mutated

    def _blend_toward_elites(self, individual: Individual, elites: list[Individual]) -> Individual:
        """Pull a candidate toward the elite centroid to refine promising regions."""
        if not elites:
            return individual

        blended = individual.copy()
        ranges = self.space.get_ranges()
        for name in blended.params:
            if name not in ranges:
                continue
            centroid = sum(e.params.get(name, blended.params[name]) for e in elites) / len(elites)
            if self.strategy == "cma_es_like":
                alpha = 0.5 + 0.25 * self.rng.random()
            else:
                alpha = 0.2 + 0.3 * self.rng.random()
            blended.params[name] = (1.0 - alpha) * blended.params[name] + alpha * centroid
        return self._enforce_constraints(blended)

    def _sample_elite_guided_candidate(
        self,
        elites: list[Individual],
        exploration_scale: float,
    ) -> Individual:
        """Sample a candidate around the elite centroid with diagonal covariance."""
        ranges = self.space.get_ranges()
        defaults = self.space.get_defaults()
        params: dict[str, float] = {}

        for name, (lo, hi) in ranges.items():
            values = [elite.params.get(name, defaults.get(name, lo)) for elite in elites]
            centroid = sum(values) / len(values) if values else defaults.get(name, lo)
            span = hi - lo
            if len(values) > 1:
                variance = sum((value - centroid) ** 2 for value in values) / len(values)
                sigma = max(variance ** 0.5, 0.05 * span)
            else:
                sigma = 0.1 * span
            sigma *= exploration_scale
            params[name] = centroid + self.rng.gauss(0, sigma)

        return self._enforce_constraints(Individual(params=params))

    def _enforce_constraints(self, individual: Individual) -> Individual:
        """Clamp parameters to valid ranges."""
        for name, (lo, hi) in self.space.get_ranges().items():
            if name in individual.params:
                individual.params[name] = max(lo, min(hi, individual.params[name]))
        return individual

    # ── Diagnostics helpers ────────────────────────────────────────────

    def _compute_diversity(self) -> float:
        """Compute a normalized diversity estimate across all parameters."""
        ranges = self.space.get_ranges()
        if not ranges or not self._population:
            return 0.0

        normalized_spreads: list[float] = []
        for name, (lo, hi) in ranges.items():
            values = [ind.params.get(name, lo) for ind in self._population]
            if not values:
                continue
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            span = max(hi - lo, 1e-9)
            normalized_spreads.append(min(1.0, (variance ** 0.5) / span))

        if not normalized_spreads:
            return 0.0
        return sum(normalized_spreads) / len(normalized_spreads)

    def _determine_immigrant_count(self, diversity: float) -> int:
        """Inject random individuals when the search plateaus or collapses."""
        if self.strategy == "ga":
            return 0
        if self._stagnation < self.stagnation_window and diversity >= 0.1:
            return 0
        base = max(1, int(round(self.population_size * self.immigrant_ratio)))
        if diversity < 0.05 or self._stagnation >= 2 * self.stagnation_window:
            base += max(1, self.population_size // 12)
        return min(base, max(0, self.population_size - self.elite_count - 1))

    def _determine_local_search_count(self) -> int:
        """Allocate elite-guided local search proposals each generation."""
        if self.strategy == "ga":
            return 0
        base = max(1, int(round(self.population_size * self.local_search_ratio)))
        if self.strategy == "cma_es_like":
            base += max(1, self.population_size // 10)
        return min(base, max(1, self.population_size - self.elite_count))

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))


# ── Built-in fitness functions ────────────────────────────────────────

def constraint_satisfaction_fitness(
    space: ParameterSpace,
) -> FitnessFunction:
    """Create a fitness function that scores constraint satisfaction.

    Returns 1.0 if all constraints are satisfied, lower for violations.
    """

    def fitness_fn(params: dict[str, float]) -> FitnessResult:
        violations = space.validate(params)
        total = max(len(space.constraints), 1)
        score = 1.0 - len(violations) / total
        return FitnessResult(
            score=max(0.0, score),
            details={"violations": violations, "total_constraints": total},
        )

    return fitness_fn


def combined_fitness(
    *fitness_fns: FitnessFunction,
    weights: Optional[list[float]] = None,
) -> FitnessFunction:
    """Combine multiple fitness functions with optional weights."""
    if weights is None:
        weights = [1.0] * len(fitness_fns)

    def fitness_fn(params: dict[str, float]) -> FitnessResult:
        total_score = 0.0
        total_weight = sum(weights)
        details = {}
        for i, (fn, w) in enumerate(zip(fitness_fns, weights)):
            result = fn(params)
            total_score += result.score * w
            details[f"component_{i}"] = result.score
        return FitnessResult(
            score=total_score / total_weight if total_weight > 0 else 0.0,
            details=details,
        )

    return fitness_fn
