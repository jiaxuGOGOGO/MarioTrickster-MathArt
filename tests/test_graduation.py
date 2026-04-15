"""Tests for scaffold graduation workflow (TASK-015)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from mathart.evolution.graduation import (
    ScaffoldGraduator,
    GraduationResult,
    GraduationReport,
)
from mathart.evolution.math_registry import MathModelRegistry, ModelEntry, ModelCapability


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure for graduation tests."""
    # Create a scaffold module
    mined_dir = tmp_path / "mathart" / "mined"
    mined_dir.mkdir(parents=True)
    (mined_dir / "__init__.py").write_text("")

    # Create a working scaffold module
    (mined_dir / "test_model.py").write_text(
        '"""Test model scaffold."""\n'
        'def generate(params=None):\n'
        '    return {"status": "scaffold", "model": "test_model"}\n'
    )

    # Create a real (non-scaffold) module
    (mined_dir / "real_model.py").write_text(
        '"""Real model with actual implementation."""\n'
        'def generate(params=None):\n'
        '    import math\n'
        '    p = params or {}\n'
        '    return {"value": math.sin(p.get("x", 0.5)), "status": "ok"}\n'
    )

    # Create registry
    registry = MathModelRegistry.__new__(MathModelRegistry)
    registry._models = {}

    registry.register(ModelEntry(
        name="test_model",
        version="0.1.0",
        description="Test scaffold model",
        capabilities=[ModelCapability.TEXTURE],
        module_path="mathart.mined.test_model",
        function_name="generate",
        params={"seed": {"type": "int", "default": 42}},
        status="candidate",
    ))

    registry.register(ModelEntry(
        name="real_model",
        version="0.1.0",
        description="Real model",
        capabilities=[ModelCapability.TEXTURE],
        module_path="mathart.mined.real_model",
        function_name="generate",
        params={"x": {"type": "float", "default": 0.5}},
        knowledge_sources=["knowledge/test.md"],
        status="experimental",
    ))

    registry.save(tmp_path / "math_models.json")
    return tmp_path, registry


class TestGraduationResult:
    def test_summary_format(self):
        result = GraduationResult(
            model_name="test",
            from_status="candidate",
            to_status="experimental",
            success=True,
            checks_passed=["a", "b"],
        )
        summary = result.summary()
        assert "SUCCESS" in summary
        assert "test" in summary

    def test_failed_summary(self):
        result = GraduationResult(
            model_name="test",
            from_status="candidate",
            to_status="experimental",
            success=False,
            checks_failed=["module_exists"],
            notes=["Module not found"],
        )
        summary = result.summary()
        assert "FAILED" in summary
        assert "module_exists" in summary


class TestScaffoldGraduator:
    def test_graduate_candidate_scaffold(self, tmp_project):
        """Scaffold module should graduate to experimental (smoke test passes)."""
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(
            project_root=project_root,
            registry=registry,
            verbose=True,
        )
        result = graduator.graduate_candidate("test_model")
        assert result.success is True
        assert result.to_status == "experimental"
        assert "module_exists" in result.checks_passed
        assert "smoke_test_passes" in result.checks_passed

    def test_graduate_nonexistent_model(self, tmp_project):
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)
        result = graduator.graduate_candidate("nonexistent")
        assert result.success is False
        assert "model_not_found" in result.checks_failed

    def test_graduate_wrong_status(self, tmp_project):
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)
        result = graduator.graduate_candidate("real_model")  # already experimental
        assert result.success is False
        assert "wrong_status" in result.checks_failed

    def test_promote_to_stable(self, tmp_project):
        """Real model should promote to stable."""
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(
            project_root=project_root,
            registry=registry,
        )
        result = graduator.promote_to_stable("real_model")
        assert result.success is True
        assert result.to_status == "stable"

    def test_scaffold_cannot_promote_to_stable(self, tmp_project):
        """Scaffold model returns scaffold placeholder, should fail stable promotion."""
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)

        # First graduate to experimental
        graduator.graduate_candidate("test_model")
        # Then try to promote to stable — should fail because output is scaffold
        result = graduator.promote_to_stable("test_model")
        assert result.success is False
        assert "returns_valid_output" in result.checks_failed

    def test_demote(self, tmp_project):
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)
        result = graduator.demote("real_model", "candidate")
        assert result.success is True
        assert registry.get("real_model").status == "candidate"

    def test_audit_all(self, tmp_project):
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)
        report = graduator.audit_all()
        assert isinstance(report, GraduationReport)
        assert report.total >= 2

    def test_graduation_log_created(self, tmp_project):
        project_root, registry = tmp_project
        graduator = ScaffoldGraduator(project_root=project_root, registry=registry)
        graduator.graduate_candidate("test_model")
        log_path = project_root / "GRADUATION_LOG.md"
        assert log_path.exists()
        content = log_path.read_text()
        assert "test_model" in content
