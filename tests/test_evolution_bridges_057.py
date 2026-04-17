"""Tests for SESSION-057 Evolution Bridges — Smooth Morphology + Constraint WFC.

Validates:
  - SmoothMorphologyEvolutionBridge: evaluate, distill, fitness bonus, trends
  - ConstraintWFCEvolutionBridge: evaluate, distill, fitness bonus, trends
  - Status collectors: smooth_morphology, constraint_wfc
  - Full cycle integration: both bridges run end-to-end
"""
import json
import tempfile
from pathlib import Path

import pytest

from mathart.evolution.smooth_morphology_bridge import (
    SmoothMorphologyMetrics,
    SmoothMorphologyState,
    SmoothMorphologyStatus,
    SmoothMorphologyEvolutionBridge,
    collect_smooth_morphology_status,
)
from mathart.evolution.constraint_wfc_bridge import (
    ConstraintWFCMetrics,
    ConstraintWFCState,
    ConstraintWFCStatus,
    ConstraintWFCEvolutionBridge,
    collect_constraint_wfc_status,
)


# ── Smooth Morphology Bridge Tests ───────────────────────────────────────────


class TestSmoothMorphologyBridge:
    """Test the smooth morphology evolution bridge."""

    def test_metrics_serialization(self):
        """Metrics serialize to dict correctly."""
        m = SmoothMorphologyMetrics(
            cycle_id=1, population_size=10, best_fitness=0.75,
            avg_fitness=0.5, diversity_score=0.3,
        )
        d = m.to_dict()
        assert d["cycle_id"] == 1
        assert d["best_fitness"] == 0.75

    def test_state_roundtrip(self):
        """State serializes and deserializes correctly."""
        s = SmoothMorphologyState(
            total_cycles=5, best_fitness_ever=0.8,
            fitness_trend=[0.3, 0.5, 0.7],
        )
        d = s.to_dict()
        s2 = SmoothMorphologyState.from_dict(d)
        assert s2.total_cycles == 5
        assert s2.best_fitness_ever == 0.8
        assert len(s2.fitness_trend) == 3

    def test_evaluate_produces_metrics(self):
        """Evaluation produces valid metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = SmoothMorphologyEvolutionBridge(tmpdir)
            metrics = bridge.evaluate(resolution=32)
            assert metrics.population_size > 0
            assert metrics.best_fitness > 0
            assert 0 <= metrics.avg_fitness <= 1

    def test_distill_produces_rules(self):
        """Distillation produces rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = SmoothMorphologyEvolutionBridge(tmpdir)
            metrics = bridge.evaluate(resolution=32)
            rules = bridge.distill(metrics)
            assert "target_fitness_range" in rules
            assert "min_diversity" in rules

    def test_fitness_bonus_with_passing_gate(self):
        """Fitness bonus is positive when gate passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = SmoothMorphologyEvolutionBridge(tmpdir)
            metrics = SmoothMorphologyMetrics(
                pass_gate=True, diversity_score=0.5, all_valid_sdf=True,
            )
            bonus = bridge.compute_fitness_bonus(metrics)
            assert bonus > 0

    def test_full_cycle(self):
        """Full cycle runs end-to-end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = SmoothMorphologyEvolutionBridge(tmpdir)
            metrics, rules, bonus = bridge.run_full_cycle(resolution=32)
            assert metrics.population_size > 0
            assert isinstance(rules, dict)
            assert isinstance(bonus, float)
            # State should be persisted
            assert bridge.state.total_cycles == 1

    def test_state_persistence(self):
        """State persists across bridge instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge1 = SmoothMorphologyEvolutionBridge(tmpdir)
            bridge1.run_full_cycle(resolution=32)
            assert bridge1.state.total_cycles == 1

            bridge2 = SmoothMorphologyEvolutionBridge(tmpdir)
            assert bridge2.state.total_cycles == 1

    def test_knowledge_file_created(self):
        """Knowledge file is created after distillation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = SmoothMorphologyEvolutionBridge(tmpdir)
            bridge.run_full_cycle(resolution=32)
            knowledge_path = Path(tmpdir) / "knowledge" / "smooth_morphology_rules.md"
            assert knowledge_path.exists()
            content = knowledge_path.read_text()
            assert "Distilled Rules" in content


class TestSmoothMorphologyStatus:
    """Test the smooth morphology status collector."""

    def test_status_on_real_project(self):
        """Status collector works on the real project."""
        root = Path(__file__).parent.parent
        status = collect_smooth_morphology_status(root)
        assert status.module_exists
        assert status.test_exists
        assert len(status.tracked_exports) > 0

    def test_status_on_empty_dir(self):
        """Status collector handles empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status = collect_smooth_morphology_status(tmpdir)
            assert not status.module_exists
            assert not status.test_exists


# ── Constraint WFC Bridge Tests ──────────────────────────────────────────────


class TestConstraintWFCBridge:
    """Test the constraint WFC evolution bridge."""

    def test_metrics_serialization(self):
        """Metrics serialize to dict correctly."""
        m = ConstraintWFCMetrics(
            cycle_id=1, levels_generated=10, playability_rate=0.8,
        )
        d = m.to_dict()
        assert d["cycle_id"] == 1
        assert d["playability_rate"] == 0.8

    def test_state_roundtrip(self):
        """State serializes and deserializes correctly."""
        s = ConstraintWFCState(
            total_cycles=3, best_playability_ever=0.9,
            playability_trend=[0.5, 0.7, 0.9],
        )
        d = s.to_dict()
        s2 = ConstraintWFCState.from_dict(d)
        assert s2.total_cycles == 3
        assert s2.best_playability_ever == 0.9

    def test_evaluate_produces_metrics(self):
        """Evaluation produces valid metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = ConstraintWFCEvolutionBridge(tmpdir)
            metrics = bridge.evaluate(n_levels=3, seed=42)
            assert metrics.levels_generated > 0
            assert 0 <= metrics.playability_rate <= 1

    def test_distill_produces_rules(self):
        """Distillation produces rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = ConstraintWFCEvolutionBridge(tmpdir)
            metrics = bridge.evaluate(n_levels=3, seed=42)
            rules = bridge.distill(metrics)
            assert "target_playability" in rules

    def test_fitness_bonus_with_passing_gate(self):
        """Fitness bonus is positive when gate passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = ConstraintWFCEvolutionBridge(tmpdir)
            metrics = ConstraintWFCMetrics(
                pass_gate=True, playability_rate=0.95,
                difficulty_variance=0.1,
            )
            bonus = bridge.compute_fitness_bonus(metrics)
            assert bonus > 0

    def test_full_cycle(self):
        """Full cycle runs end-to-end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = ConstraintWFCEvolutionBridge(tmpdir)
            metrics, rules, bonus = bridge.run_full_cycle(n_levels=3, seed=42)
            assert metrics.levels_generated > 0
            assert isinstance(rules, dict)
            assert isinstance(bonus, float)
            assert bridge.state.total_cycles == 1

    def test_state_persistence(self):
        """State persists across bridge instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge1 = ConstraintWFCEvolutionBridge(tmpdir)
            bridge1.run_full_cycle(n_levels=3, seed=42)

            bridge2 = ConstraintWFCEvolutionBridge(tmpdir)
            assert bridge2.state.total_cycles == 1

    def test_knowledge_file_created(self):
        """Knowledge file is created after distillation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = ConstraintWFCEvolutionBridge(tmpdir)
            bridge.run_full_cycle(n_levels=3, seed=42)
            knowledge_path = Path(tmpdir) / "knowledge" / "constraint_wfc_rules.md"
            assert knowledge_path.exists()
            content = knowledge_path.read_text()
            assert "Distilled Rules" in content


class TestConstraintWFCStatus:
    """Test the constraint WFC status collector."""

    def test_status_on_real_project(self):
        """Status collector works on the real project."""
        root = Path(__file__).parent.parent
        status = collect_constraint_wfc_status(root)
        assert status.module_exists
        assert status.test_exists
        assert len(status.tracked_exports) > 0

    def test_status_on_empty_dir(self):
        """Status collector handles empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status = collect_constraint_wfc_status(tmpdir)
            assert not status.module_exists
            assert not status.test_exists
