"""Optimization Layer: Evolutionary parameter search.

This module implements a lightweight evolutionary optimizer that searches
the parameter spaces defined by the compilation layer. It uses a simple
genetic algorithm with tournament selection, crossover, and mutation to
find parameter configurations that maximize a fitness function.

The fitness function is pluggable — it can be:
- A heuristic image quality metric (frequency analysis, contrast).
- A constraint satisfaction score.
- A human feedback score (RLHF loop).

Usage::

    from mathart.distill.optimizer import EvolutionaryOptimizer
    from mathart.distill.compiler import ParameterSpace

    optimizer = EvolutionaryOptimizer(space, population_size=50)
    best = optimizer.run(generations=100, fitness_fn=my_fitness)
    print(best.params, best.fitness)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional

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


# Type alias for fitness functions
FitnessFunction = Callable[[dict[str, float]], FitnessResult]


class EvolutionaryOptimizer:
    """Genetic algorithm optimizer for parameter spaces.

    Parameters
    ----------
    space : ParameterSpace
        The parameter space to search.
    population_size : int
        Number of individuals per generation.
    mutation_rate : float
        Probability of mutating each gene.
    mutation_strength : float
        Standard deviation of mutation (as fraction of range).
    crossover_rate : float
        Probability of crossover between parents.
    tournament_size : int
        Number of individuals in tournament selection.
    elite_count : int
        Number of top individuals preserved each generation.
    seed : int or None
        Random seed.
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
    ):
        self.space = space
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.mutation_strength = mutation_strength
        self.crossover_rate = crossover_rate
        self.tournament_size = tournament_size
        self.elite_count = elite_count
        self.rng = random.Random(seed)
        self._population: list[Individual] = []
        self._generation = 0
        self._history: list[dict] = []

    # ── Public API ────────────────────────────────────────────────────

    def run(
        self,
        fitness_fn: FitnessFunction,
        generations: int = 100,
        target_fitness: Optional[float] = None,
        callback: Optional[Callable[[int, Individual], None]] = None,
    ) -> Individual:
        """Run the evolutionary optimization.

        Parameters
        ----------
        fitness_fn : callable
            Function that takes a params dict and returns a FitnessResult.
        generations : int
            Maximum number of generations.
        target_fitness : float or None
            Stop early if this fitness is reached.
        callback : callable or None
            Called each generation with (generation_number, best_individual).

        Returns
        -------
        Individual
            The best individual found.
        """
        self._initialize_population()
        self._evaluate(fitness_fn)

        for gen in range(generations):
            self._generation = gen

            # Selection + reproduction
            new_pop = self._select_elite()
            while len(new_pop) < self.population_size:
                parent1 = self._tournament_select()
                parent2 = self._tournament_select()
                child = self._crossover(parent1, parent2)
                child = self._mutate(child)
                child = self._enforce_constraints(child)
                new_pop.append(child)

            self._population = new_pop
            self._evaluate(fitness_fn)

            best = self.best_individual
            self._history.append({
                "generation": gen,
                "best_fitness": best.fitness,
                "avg_fitness": sum(i.fitness for i in self._population) / len(self._population),
            })

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
        """Return the optimization history."""
        return list(self._history)

    # ── Internal methods ──────────────────────────────────────────────

    def _initialize_population(self) -> None:
        """Create initial random population within parameter ranges."""
        ranges = self.space.get_ranges()
        defaults = self.space.get_defaults()
        self._population = []

        # First individual uses defaults
        self._population.append(Individual(params=dict(defaults)))

        # Rest are random
        for _ in range(self.population_size - 1):
            params = {}
            for name, (lo, hi) in ranges.items():
                params[name] = self.rng.uniform(lo, hi)
            self._population.append(Individual(params=params))

    def _evaluate(self, fitness_fn: FitnessFunction) -> None:
        """Evaluate fitness for all individuals."""
        for ind in self._population:
            result = fitness_fn(ind.params)
            ind.fitness = result.score

    def _tournament_select(self) -> Individual:
        """Select an individual via tournament selection."""
        candidates = self.rng.sample(
            self._population,
            min(self.tournament_size, len(self._population)),
        )
        return max(candidates, key=lambda i: i.fitness)

    def _select_elite(self) -> list[Individual]:
        """Preserve top individuals (elitism)."""
        sorted_pop = sorted(self._population, key=lambda i: i.fitness, reverse=True)
        return [ind.copy() for ind in sorted_pop[: self.elite_count]]

    def _crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        """Uniform crossover between two parents."""
        if self.rng.random() > self.crossover_rate:
            return parent1.copy()

        child_params = {}
        for name in parent1.params:
            if self.rng.random() < 0.5:
                child_params[name] = parent1.params[name]
            else:
                child_params[name] = parent2.params.get(name, parent1.params[name])
        return Individual(params=child_params)

    def _mutate(self, individual: Individual) -> Individual:
        """Apply Gaussian mutation to an individual."""
        ranges = self.space.get_ranges()
        mutated = individual.copy()

        for name in mutated.params:
            if self.rng.random() < self.mutation_rate:
                if name in ranges:
                    lo, hi = ranges[name]
                    span = hi - lo
                    delta = self.rng.gauss(0, self.mutation_strength * span)
                    mutated.params[name] = mutated.params[name] + delta

        return mutated

    def _enforce_constraints(self, individual: Individual) -> Individual:
        """Clamp parameters to valid ranges."""
        ranges = self.space.get_ranges()
        for name in individual.params:
            if name in ranges:
                lo, hi = ranges[name]
                individual.params[name] = max(lo, min(hi, individual.params[name]))
        return individual


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
