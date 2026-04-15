"""Tests for the finite-difference gradient optimizer (TASK-013)."""
from __future__ import annotations


from mathart.distill.compiler import ParameterSpace, Constraint
from mathart.distill.optimizer import Individual, FitnessResult
from mathart.distill.fd_gradient import (
    FDGradientOptimizer,
    FDGradientConfig,
    FDRefinementResult,
    hybrid_optimize,
)


def make_quadratic_fitness():
    """A simple quadratic fitness: max at x=0.7, y=0.3."""
    def fitness_fn(params):
        x = params.get("x", 0.5)
        y = params.get("y", 0.5)
        # Maximum at (0.7, 0.3), score = 1.0
        score = 1.0 - ((x - 0.7) ** 2 + (y - 0.3) ** 2)
        return FitnessResult(score=max(0.0, score))
    return fitness_fn


def make_space():
    space = ParameterSpace(name="test_fd")
    space.add_constraint(Constraint(param_name="x", min_value=0.0, max_value=1.0, default_value=0.5))
    space.add_constraint(Constraint(param_name="y", min_value=0.0, max_value=1.0, default_value=0.5))
    return space


class TestFDGradientOptimizer:
    def test_refine_improves_fitness(self):
        """FD gradient should improve fitness from a suboptimal starting point."""
        space = make_space()
        fitness_fn = make_quadratic_fitness()
        config = FDGradientConfig(max_steps=50, learning_rate=0.05)
        optimizer = FDGradientOptimizer(space, config=config)

        individual = Individual(params={"x": 0.2, "y": 0.8}, fitness=0.0)
        # Evaluate initial fitness
        individual.fitness = fitness_fn(individual.params).score

        result = optimizer.refine(individual, fitness_fn)
        assert result.final_fitness > result.initial_fitness
        assert result.improvement > 0
        assert result.steps_taken > 0

    def test_refine_converges_near_optimum(self):
        """Starting near the optimum should converge quickly."""
        space = make_space()
        fitness_fn = make_quadratic_fitness()
        config = FDGradientConfig(max_steps=50, learning_rate=0.02)
        optimizer = FDGradientOptimizer(space, config=config)

        individual = Individual(params={"x": 0.68, "y": 0.32}, fitness=0.0)
        individual.fitness = fitness_fn(individual.params).score

        result = optimizer.refine(individual, fitness_fn)
        assert result.final_fitness > 0.99
        assert abs(individual.params["x"] - 0.7) < 0.05
        assert abs(individual.params["y"] - 0.3) < 0.05

    def test_respects_constraints(self):
        """Parameters should stay within bounds after refinement."""
        space = make_space()
        fitness_fn = make_quadratic_fitness()
        config = FDGradientConfig(max_steps=20, learning_rate=0.1)
        optimizer = FDGradientOptimizer(space, config=config)

        individual = Individual(params={"x": 0.01, "y": 0.99}, fitness=0.0)
        individual.fitness = fitness_fn(individual.params).score

        optimizer.refine(individual, fitness_fn)
        assert 0.0 <= individual.params["x"] <= 1.0
        assert 0.0 <= individual.params["y"] <= 1.0

    def test_result_has_history(self):
        """Result should contain fitness history and gradient norms."""
        space = make_space()
        fitness_fn = make_quadratic_fitness()
        config = FDGradientConfig(max_steps=10)
        optimizer = FDGradientOptimizer(space, config=config)

        individual = Individual(params={"x": 0.3, "y": 0.6}, fitness=0.0)
        individual.fitness = fitness_fn(individual.params).score

        result = optimizer.refine(individual, fitness_fn)
        assert len(result.fitness_history) > 1
        assert len(result.gradient_norms) > 0

    def test_summary_format(self):
        """Summary should contain key information."""
        result = FDRefinementResult(
            initial_fitness=0.5,
            final_fitness=0.8,
            steps_taken=10,
            converged=True,
        )
        summary = result.summary()
        assert "FD-Gradient" in summary
        assert "converged" in summary
        assert "0.5" in summary

    def test_hybrid_optimize_convenience(self):
        """hybrid_optimize should work as a convenience wrapper."""
        space = make_space()
        fitness_fn = make_quadratic_fitness()
        individual = Individual(params={"x": 0.3, "y": 0.6}, fitness=0.0)
        individual.fitness = fitness_fn(individual.params).score

        refined, result = hybrid_optimize(space, fitness_fn, individual)
        assert result.final_fitness >= result.initial_fitness
        assert refined is individual  # Same object, modified in-place

    def test_high_dimensional_space(self):
        """Should work with higher-dimensional parameter spaces."""
        space = ParameterSpace(name="high_dim")
        for i in range(20):
            space.add_constraint(Constraint(
                param_name=f"p{i}",
                min_value=0.0,
                max_value=1.0,
                default_value=0.5,
            ))

        def fitness_fn(params):
            # Sum of squared distances from 0.5
            score = 1.0 - sum((v - 0.5) ** 2 for v in params.values()) / len(params)
            return FitnessResult(score=max(0.0, score))

        config = FDGradientConfig(max_steps=15, learning_rate=0.01)
        optimizer = FDGradientOptimizer(space, config=config)

        params = {f"p{i}": 0.2 + 0.03 * i for i in range(20)}
        individual = Individual(params=params, fitness=0.0)
        individual.fitness = fitness_fn(individual.params).score

        result = optimizer.refine(individual, fitness_fn)
        assert result.final_fitness >= result.initial_fitness
