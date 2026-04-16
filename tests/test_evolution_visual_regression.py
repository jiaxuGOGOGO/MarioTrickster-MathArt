"""Tests for SESSION-041: Visual Regression Evolution Bridge integration.

Validates the three-layer evolution cycle with visual regression:
- Layer 1: Visual gate (SSIM + structural audit)
- Layer 2: Knowledge distillation from audit results
- Layer 3: Fitness integration for physics evolution

Also tests the full cycle: generate → audit → distill → evolve.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a minimal project structure for testing."""
    (tmp_path / "knowledge").mkdir()
    return tmp_path


@pytest.fixture
def bridge(project_root: Path):
    """Create a VisualRegressionEvolutionBridge instance."""
    from mathart.evolution.visual_regression_bridge import (
        VisualRegressionEvolutionBridge,
    )
    return VisualRegressionEvolutionBridge(project_root=project_root, verbose=False)


@pytest.fixture
def passing_audit_report() -> dict:
    """A synthetic audit report that passes all levels."""
    return {
        "session_id": "SESSION-041",
        "all_pass": True,
        "level0_pass": True,
        "level1_pass": True,
        "level2_pass": True,
        "ssim_score": 1.0,
        "pipeline_hash": "abc123def456",
        "golden_hash": "abc123def456",
        "diff_heatmap_path": "",
    }


@pytest.fixture
def failing_audit_report() -> dict:
    """A synthetic audit report that fails SSIM."""
    return {
        "session_id": "SESSION-041",
        "all_pass": False,
        "level0_pass": True,
        "level1_pass": True,
        "level2_pass": False,
        "ssim_score": 0.9950,
        "pipeline_hash": "abc123def456",
        "golden_hash": "xyz789abc000",
        "diff_heatmap_path": "/tmp/diff.png",
    }


# ── Layer 1: Visual Gate Tests ────────────────────────────────────────────────


class TestLayer1VisualGate:
    """Test Layer 1 visual regression gating."""

    def test_passing_audit_creates_metrics(self, bridge, passing_audit_report):
        metrics = bridge.evaluate_visual_regression(passing_audit_report)
        assert metrics.all_pass is True
        assert metrics.ssim_pass is True
        assert metrics.ssim_score == 1.0
        assert metrics.level0_pass is True
        assert metrics.level1_pass is True
        assert metrics.level2_pass is True

    def test_failing_audit_creates_metrics(self, bridge, failing_audit_report):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        assert metrics.all_pass is False
        assert metrics.ssim_pass is False
        assert metrics.ssim_score == 0.9950

    def test_state_updates_on_pass(self, bridge, passing_audit_report):
        bridge.evaluate_visual_regression(passing_audit_report)
        assert bridge.state.total_audit_cycles == 1
        assert bridge.state.total_passes == 1
        assert bridge.state.total_failures == 0
        assert bridge.state.consecutive_passes == 1

    def test_state_updates_on_failure(self, bridge, failing_audit_report):
        bridge.evaluate_visual_regression(failing_audit_report)
        assert bridge.state.total_failures == 1
        assert bridge.state.consecutive_passes == 0

    def test_ssim_trend_tracking(self, bridge, passing_audit_report):
        # Run 3 cycles
        for _ in range(3):
            bridge.evaluate_visual_regression(passing_audit_report)
        assert len(bridge.state.ssim_trend) == 3
        assert all(s == 1.0 for s in bridge.state.ssim_trend)

    def test_golden_baseline_auto_set(self, bridge, passing_audit_report):
        assert bridge.state.golden_baseline_set is False
        bridge.evaluate_visual_regression(passing_audit_report)
        assert bridge.state.golden_baseline_set is True
        assert bridge.state.golden_baseline_hash == "abc123def456"

    def test_pipeline_hash_match(self, bridge, passing_audit_report):
        metrics = bridge.evaluate_visual_regression(passing_audit_report)
        assert metrics.pipeline_hash_match is True

    def test_pipeline_hash_mismatch(self, bridge, failing_audit_report):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        assert metrics.pipeline_hash_match is False


# ── Layer 2: Knowledge Distillation Tests ─────────────────────────────────────


class TestLayer2KnowledgeDistillation:
    """Test Layer 2 knowledge rule generation from visual regression."""

    def test_no_rules_on_pass(self, bridge, passing_audit_report):
        metrics = bridge.evaluate_visual_regression(passing_audit_report)
        rules = bridge.distill_visual_knowledge(metrics)
        # First pass shouldn't generate enforcement rules
        assert all(r["rule_type"] != "enforcement" for r in rules)

    def test_ssim_failure_generates_rule(self, bridge, failing_audit_report):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        rules = bridge.distill_visual_knowledge(metrics)
        enforcement_rules = [r for r in rules if r["rule_type"] == "enforcement"]
        assert len(enforcement_rules) >= 1
        assert "visual_regression" in enforcement_rules[0]["domain"]

    def test_hash_drift_generates_rule(self, bridge, failing_audit_report):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        rules = bridge.distill_visual_knowledge(metrics)
        regression_rules = [r for r in rules if r["rule_type"] == "regression_guard"]
        assert len(regression_rules) >= 1

    def test_consecutive_passes_generate_confidence_boost(
        self, bridge, passing_audit_report
    ):
        # Need 5 consecutive passes for confidence boost
        for _ in range(5):
            metrics = bridge.evaluate_visual_regression(passing_audit_report)
        rules = bridge.distill_visual_knowledge(metrics)
        boost_rules = [r for r in rules if r["rule_type"] == "confidence_boost"]
        assert len(boost_rules) >= 1

    def test_knowledge_file_written(self, bridge, failing_audit_report, project_root):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        bridge.distill_visual_knowledge(metrics)
        kb_file = project_root / "knowledge" / "visual_regression_ci.md"
        assert kb_file.exists()
        content = kb_file.read_text()
        assert "visual_regression" in content

    def test_ssim_trend_degradation_warning(self, bridge):
        # Create declining SSIM trend
        for ssim in [1.0, 0.99999, 0.99998]:
            report = {
                "all_pass": True,
                "level0_pass": True,
                "level1_pass": True,
                "level2_pass": True,
                "ssim_score": ssim,
                "pipeline_hash": "abc",
                "golden_hash": "abc",
                "diff_heatmap_path": "",
            }
            metrics = bridge.evaluate_visual_regression(report)
        rules = bridge.distill_visual_knowledge(metrics)
        trend_rules = [r for r in rules if r["rule_type"] == "trend_warning"]
        assert len(trend_rules) >= 1


# ── Layer 3: Fitness Integration Tests ────────────────────────────────────────


class TestLayer3FitnessIntegration:
    """Test Layer 3 fitness bonus/penalty from visual regression."""

    def test_full_pass_gives_positive_bonus(self, bridge, passing_audit_report):
        metrics = bridge.evaluate_visual_regression(passing_audit_report)
        bonus = bridge.compute_visual_fitness_bonus(metrics)
        assert bonus > 0

    def test_ssim_failure_gives_penalty(self, bridge, failing_audit_report):
        metrics = bridge.evaluate_visual_regression(failing_audit_report)
        bonus = bridge.compute_visual_fitness_bonus(metrics)
        assert bonus < 0

    def test_level0_failure_severe_penalty(self, bridge):
        report = {
            "all_pass": False,
            "level0_pass": False,
            "level1_pass": False,
            "level2_pass": False,
            "ssim_score": None,
            "pipeline_hash": "",
            "golden_hash": "",
            "diff_heatmap_path": "",
        }
        metrics = bridge.evaluate_visual_regression(report)
        bonus = bridge.compute_visual_fitness_bonus(metrics)
        assert bonus <= -0.3

    def test_consecutive_passes_bonus(self, bridge, passing_audit_report):
        for _ in range(3):
            metrics = bridge.evaluate_visual_regression(passing_audit_report)
        bonus = bridge.compute_visual_fitness_bonus(metrics)
        # Should get base + SSIM + hash + consecutive bonus
        assert bonus > 0.1

    def test_fitness_bounded(self, bridge, passing_audit_report, failing_audit_report):
        metrics_pass = bridge.evaluate_visual_regression(passing_audit_report)
        bonus_pass = bridge.compute_visual_fitness_bonus(metrics_pass)
        assert -0.3 <= bonus_pass <= 0.15

        metrics_fail = bridge.evaluate_visual_regression(failing_audit_report)
        bonus_fail = bridge.compute_visual_fitness_bonus(metrics_fail)
        assert -0.3 <= bonus_fail <= 0.15


# ── Persistence Tests ─────────────────────────────────────────────────────────


class TestPersistence:
    """Test state persistence across bridge instances."""

    def test_state_persists(self, project_root, passing_audit_report):
        from mathart.evolution.visual_regression_bridge import (
            VisualRegressionEvolutionBridge,
        )
        bridge1 = VisualRegressionEvolutionBridge(
            project_root=project_root, verbose=False
        )
        bridge1.evaluate_visual_regression(passing_audit_report)
        assert bridge1.state.total_audit_cycles == 1

        # Create new instance — should load persisted state
        bridge2 = VisualRegressionEvolutionBridge(
            project_root=project_root, verbose=False
        )
        assert bridge2.state.total_audit_cycles == 1
        assert bridge2.state.total_passes == 1

    def test_state_file_created(self, bridge, passing_audit_report, project_root):
        bridge.evaluate_visual_regression(passing_audit_report)
        state_file = project_root / ".visual_regression_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["total_audit_cycles"] == 1

    def test_status_report(self, bridge, passing_audit_report):
        bridge.evaluate_visual_regression(passing_audit_report)
        report = bridge.status_report()
        assert "SESSION-041" in report
        assert "Total audit cycles: 1" in report


# ── Full Cycle Integration Test ───────────────────────────────────────────────


class TestFullCycle:
    """Test the complete three-layer cycle: audit → distill → fitness."""

    def test_full_cycle_pass(self, bridge, passing_audit_report):
        # Layer 1: Evaluate
        metrics = bridge.evaluate_visual_regression(passing_audit_report)
        assert metrics.all_pass is True

        # Layer 2: Distill
        rules = bridge.distill_visual_knowledge(metrics)
        # No enforcement rules on first pass
        assert all(r["rule_type"] != "enforcement" for r in rules)

        # Layer 3: Fitness
        bonus = bridge.compute_visual_fitness_bonus(metrics)
        assert bonus > 0

    def test_full_cycle_fail_and_recover(self, bridge, failing_audit_report, passing_audit_report):
        # Cycle 1: Failure
        m1 = bridge.evaluate_visual_regression(failing_audit_report)
        r1 = bridge.distill_visual_knowledge(m1)
        b1 = bridge.compute_visual_fitness_bonus(m1)
        assert not m1.all_pass
        assert len(r1) > 0
        assert b1 < 0

        # Cycle 2: Recovery
        m2 = bridge.evaluate_visual_regression(passing_audit_report)
        r2 = bridge.distill_visual_knowledge(m2)
        b2 = bridge.compute_visual_fitness_bonus(m2)
        assert m2.all_pass
        assert b2 > 0

        # State should reflect recovery
        assert bridge.state.total_passes == 1
        assert bridge.state.total_failures == 1
        assert bridge.state.consecutive_passes == 1

    def test_engine_integration(self, project_root, passing_audit_report):
        """Test that SelfEvolutionEngine correctly wires the bridge."""
        from mathart.evolution.engine import SelfEvolutionEngine
        from mathart.evolution.inner_loop import RunMode

        engine = SelfEvolutionEngine(
            project_root=project_root,
            mode=RunMode.AUTONOMOUS,
            verbose=False,
            enable_physics=False,
        )

        # Verify bridge is initialized
        assert engine.visual_regression_bridge is not None

        # Run evaluation through engine
        result = engine.evaluate_visual_regression(passing_audit_report)
        assert result["visual_status"] == "PASS"
        assert result["fitness_bonus"] > 0
