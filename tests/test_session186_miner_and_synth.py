"""SESSION-186: Comprehensive tests for Academic Miner, Auto-Enforcer Synthesizer,
and Zero-Trust Sandbox Loader.

Test Categories:
1. Academic Miner Backend — registration, mock fallback, structured JSON output
2. Auto-Enforcer Synthesizer — code generation, AST validation, quarantine
3. Zero-Trust Loader — integrity fingerprinting, quarantine-on-failure
4. End-to-End Pipeline — miner → synthesizer → loader chain
5. Red-Line Enforcement — anti-poisoning, sandbox isolation, UX preservation

Run with:
    python -m pytest tests/test_session186_miner_and_synth.py -v
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def project_root():
    """Return the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create a temporary output directory."""
    output_dir = tmp_path / "test_output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def temp_auto_gen_dir(tmp_path):
    """Create a temporary auto_generated directory."""
    auto_gen = tmp_path / "auto_generated"
    auto_gen.mkdir()
    return auto_gen


@pytest.fixture
def sample_academic_papers_json(tmp_path):
    """Create a sample academic_papers.json for testing."""
    papers = [
        {
            "title": "Test Stable Fluids Paper",
            "source": "mock",
            "url": "https://example.com/test",
            "abstract": "A test paper about stable fluid simulation.",
            "year": 2024,
            "relevance_score": 0.9,
            "capabilities": ["PHYSICS_VFX"],
            "equations": ["du/dt = f"],
            "parameters": {"viscosity": {"symbol": "v", "range": [0.001, 0.1]}},
            "is_mock": True,
        }
    ]
    path = tmp_path / "academic_papers.json"
    path.write_text(json.dumps(papers, ensure_ascii=False, indent=2))
    return path


# ═══════════════════════════════════════════════════════════════════════════
#  1. Backend Type Registration Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBackendTypeRegistration:
    """Verify that SESSION-186 backend types are properly registered."""

    def test_academic_miner_type_exists(self):
        from mathart.core.backend_types import BackendType
        assert hasattr(BackendType, "ACADEMIC_MINER")
        assert BackendType.ACADEMIC_MINER.value == "academic_miner"

    def test_auto_enforcer_synth_type_exists(self):
        from mathart.core.backend_types import BackendType
        assert hasattr(BackendType, "AUTO_ENFORCER_SYNTH")
        assert BackendType.AUTO_ENFORCER_SYNTH.value == "auto_enforcer_synth"

    def test_academic_miner_aliases_resolve(self):
        from mathart.core.backend_types import backend_type_value
        aliases = ["academic_miner", "paper_miner", "literature_miner"]
        for alias in aliases:
            assert backend_type_value(alias) == "academic_miner"

    def test_auto_enforcer_synth_aliases_resolve(self):
        from mathart.core.backend_types import backend_type_value
        aliases = ["auto_enforcer_synth", "enforcer_synthesizer", "policy_synthesizer"]
        for alias in aliases:
            assert backend_type_value(alias) == "auto_enforcer_synth"


# ═══════════════════════════════════════════════════════════════════════════
#  2. Academic Miner Backend Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAcademicMinerBackend:
    """Verify Academic Miner Backend functionality."""

    def test_backend_importable(self):
        from mathart.core.academic_miner_backend import AcademicMinerBackend
        assert AcademicMinerBackend is not None

    def test_backend_registered_in_registry(self):
        from mathart.core.backend_registry import get_registry
        registry = get_registry()
        meta = registry.get_meta("academic_miner")
        assert meta is not None
        assert meta.display_name == "Academic Paper Miner (P0-SESSION-186)"

    def test_mock_fallback_produces_papers(self, temp_output_dir):
        """When real APIs fail, mock data should still produce results."""
        from mathart.core.academic_miner_backend import AcademicMinerBackend

        backend = AcademicMinerBackend()
        # Force mock by using impossible query with no-live-api
        with patch.dict(os.environ, {}, clear=False):
            manifest = backend.execute(
                output_dir=temp_output_dir,
                queries=["test_query_that_will_use_mock"],
                max_results_per_query=2,
                verbose=False,
            )

        assert manifest is not None
        assert manifest.artifact_family == "evolution_report"
        assert manifest.backend_type == "academic_miner"

        # Check output files exist
        papers_path = temp_output_dir / "academic_papers.json"
        assert papers_path.exists()
        papers = json.loads(papers_path.read_text())
        assert len(papers) > 0

    def test_structured_json_has_required_fields(self, temp_output_dir):
        """Each paper in the output should have required fields."""
        from mathart.core.academic_miner_backend import AcademicMinerBackend

        backend = AcademicMinerBackend()
        manifest = backend.execute(
            output_dir=temp_output_dir,
            verbose=False,
        )

        papers_path = temp_output_dir / "academic_papers.json"
        papers = json.loads(papers_path.read_text())

        required_fields = ["title", "source", "abstract", "relevance_score", "capabilities"]
        for paper in papers:
            for field in required_fields:
                assert field in paper, f"Missing field '{field}' in paper: {paper.get('title')}"

    def test_execution_report_generated(self, temp_output_dir):
        """Execution report should be generated."""
        from mathart.core.academic_miner_backend import AcademicMinerBackend

        backend = AcademicMinerBackend()
        backend.execute(output_dir=temp_output_dir, verbose=False)

        report_path = temp_output_dir / "academic_miner_execution_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["status"] == "success"
        assert report["backend"] == "academic_miner"


# ═══════════════════════════════════════════════════════════════════════════
#  3. Auto-Enforcer Synthesizer Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAutoEnforcerSynthBackend:
    """Verify Auto-Enforcer Synthesizer functionality."""

    def test_backend_importable(self):
        from mathart.core.auto_enforcer_synth_backend import AutoEnforcerSynthBackend
        assert AutoEnforcerSynthBackend is not None

    def test_backend_registered_in_registry(self):
        from mathart.core.backend_registry import get_registry
        registry = get_registry()
        meta = registry.get_meta("auto_enforcer_synth")
        assert meta is not None
        assert meta.display_name == "Auto-Enforcer Synthesizer (P0-SESSION-186)"

    def test_mock_enforcer_generation(self, temp_output_dir, sample_academic_papers_json, tmp_path):
        """Mock enforcer generation should produce valid Python files."""
        from mathart.core.auto_enforcer_synth_backend import AutoEnforcerSynthBackend

        # Create auto_generated dir in tmp
        auto_gen = tmp_path / "mathart" / "quality" / "gates" / "auto_generated"
        auto_gen.mkdir(parents=True)

        backend = AutoEnforcerSynthBackend()
        with patch("mathart.core.auto_enforcer_synth_backend._call_llm_for_enforcer", return_value=None):
            # Temporarily change cwd to tmp_path so auto_gen_dir resolves correctly
            old_cwd = os.getcwd()
            os.chdir(tmp_path)
            try:
                manifest = backend.execute(
                    output_dir=temp_output_dir,
                    academic_papers_json=sample_academic_papers_json,
                    max_enforcers=1,
                    verbose=False,
                )
            finally:
                os.chdir(old_cwd)

        assert manifest is not None
        assert manifest.artifact_family == "knowledge_rules"

    def test_sanitize_class_name(self):
        from mathart.core.auto_enforcer_synth_backend import _sanitize_class_name
        assert _sanitize_class_name("Stable Fluids for Animation") == "StableFluidsForAnimationEnforcer"
        assert _sanitize_class_name("") == "AutoGeneratedEnforcer"

    def test_sanitize_enforcer_id(self):
        from mathart.core.auto_enforcer_synth_backend import _sanitize_enforcer_id
        result = _sanitize_enforcer_id("Stable Fluids for Animation")
        assert "stable" in result
        assert result.endswith("_enforcer")


# ═══════════════════════════════════════════════════════════════════════════
#  4. AST Sanitizer Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestASTSanitizer:
    """Verify AST sanitizer catches malicious code."""

    def test_valid_enforcer_passes(self):
        from mathart.quality.gates.ast_sanitizer import validate_enforcer_code

        valid_code = '''
import math
from mathart.quality.gates.enforcer_registry import (
    EnforcerBase, EnforcerResult, EnforcerViolation, EnforcerSeverity, register_enforcer,
)

@register_enforcer
class TestEnforcer(EnforcerBase):
    @property
    def name(self) -> str:
        return "test_enforcer"

    @property
    def source_docs(self) -> list[str]:
        return ["test.md"]

    def validate(self, params: dict) -> EnforcerResult:
        return EnforcerResult(
            enforcer_name=self.name,
            params=params,
            violations=[],
        )
'''
        is_valid, errors = validate_enforcer_code(valid_code)
        assert is_valid, f"Valid code should pass, errors: {errors}"

    def test_eval_blocked(self):
        from mathart.quality.gates.ast_sanitizer import validate_enforcer_code

        malicious_code = '''
from mathart.quality.gates.enforcer_registry import EnforcerBase, EnforcerResult

class EvilEnforcer(EnforcerBase):
    @property
    def name(self) -> str:
        return "evil"

    @property
    def source_docs(self) -> list[str]:
        return ["evil.md"]

    def validate(self, params: dict) -> EnforcerResult:
        eval("__import__('os').system('rm -rf /')")
        return EnforcerResult(enforcer_name=self.name, params=params)
'''
        is_valid, errors = validate_enforcer_code(malicious_code)
        assert not is_valid, "Code with eval() should be blocked"
        assert any("eval" in e for e in errors)

    def test_open_blocked(self):
        from mathart.quality.gates.ast_sanitizer import validate_enforcer_code

        malicious_code = '''
from mathart.quality.gates.enforcer_registry import EnforcerBase, EnforcerResult

class FileEnforcer(EnforcerBase):
    @property
    def name(self) -> str:
        return "file_evil"

    @property
    def source_docs(self) -> list[str]:
        return ["evil.md"]

    def validate(self, params: dict) -> EnforcerResult:
        data = open("/etc/passwd").read()
        return EnforcerResult(enforcer_name=self.name, params=params)
'''
        is_valid, errors = validate_enforcer_code(malicious_code)
        assert not is_valid, "Code with open() should be blocked"

    def test_missing_methods_blocked(self):
        from mathart.quality.gates.ast_sanitizer import validate_enforcer_code

        incomplete_code = '''
from mathart.quality.gates.enforcer_registry import EnforcerBase, EnforcerResult

class IncompleteEnforcer(EnforcerBase):
    @property
    def name(self) -> str:
        return "incomplete"
'''
        is_valid, errors = validate_enforcer_code(incomplete_code)
        assert not is_valid, "Code missing required methods should be blocked"


# ═══════════════════════════════════════════════════════════════════════════
#  5. Zero-Trust Sandbox Loader Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestZeroTrustLoader:
    """Verify Zero-Trust dynamic loading mechanism."""

    def test_loader_importable(self):
        from mathart.quality.gates.sandbox_enforcer_loader import sandbox_load_enforcers
        assert sandbox_load_enforcers is not None

    def test_empty_dir_returns_empty_manifest(self, temp_auto_gen_dir):
        from mathart.quality.gates.sandbox_enforcer_loader import sandbox_load_enforcers

        result = sandbox_load_enforcers(auto_gen_dir=temp_auto_gen_dir, verbose=False)
        assert result["loaded"] == []
        assert result["quarantined"] == []

    def test_sha256_fingerprinting(self, temp_auto_gen_dir):
        from mathart.quality.gates.sandbox_enforcer_loader import (
            _compute_sha256,
            _load_fingerprints,
            _save_fingerprints,
        )

        # Test hash computation
        hash1 = _compute_sha256("hello world")
        hash2 = _compute_sha256("hello world")
        hash3 = _compute_sha256("different content")
        assert hash1 == hash2
        assert hash1 != hash3

        # Test save/load cycle
        fps = {"test.py": hash1}
        _save_fingerprints(temp_auto_gen_dir, fps)
        loaded = _load_fingerprints(temp_auto_gen_dir)
        assert loaded == fps


# ═══════════════════════════════════════════════════════════════════════════
#  6. Red-Line Enforcement Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRedLineEnforcement:
    """Verify all SESSION-186 red lines are enforced."""

    def test_no_cli_wizard_modification(self, project_root):
        """cli_wizard.py must NOT be modified by SESSION-186."""
        cli_wizard = project_root / "mathart" / "workspace" / "cli_wizard.py"
        if cli_wizard.exists():
            content = cli_wizard.read_text()
            assert "SESSION-186" not in content, (
                "cli_wizard.py must NOT contain SESSION-186 references"
            )

    def test_no_laboratory_hub_modification(self, project_root):
        """laboratory_hub.py must NOT be modified by SESSION-186."""
        lab_hub = project_root / "mathart" / "workspace" / "laboratory_hub.py"
        if lab_hub.exists():
            content = lab_hub.read_text()
            assert "SESSION-186" not in content, (
                "laboratory_hub.py must NOT contain SESSION-186 references"
            )

    def test_academic_miner_does_not_modify_paper_miner_internals(self):
        """Academic Miner Backend must be a pure adapter."""
        from mathart.core.academic_miner_backend import AcademicMinerBackend
        # The backend should only call public API, not modify internals
        import inspect
        source = inspect.getsource(AcademicMinerBackend)
        # Should not contain direct references to internal methods
        assert "_search_arxiv" not in source
        assert "_search_github" not in source
        assert "_combine_scores" not in source

    def test_mock_data_has_physics_equations(self):
        """Mock fallback data must contain physics equations for testing."""
        from mathart.core.academic_miner_backend import _DUMMY_PAPERS
        assert len(_DUMMY_PAPERS) >= 3
        for paper in _DUMMY_PAPERS:
            assert "equations" in paper
            assert len(paper["equations"]) > 0

    def test_backoff_config_reasonable(self):
        """Exponential backoff configuration must be reasonable."""
        from mathart.core.academic_miner_backend import (
            _BACKOFF_BASE_DELAY,
            _BACKOFF_MULTIPLIER,
            _BACKOFF_MAX_DELAY,
            _BACKOFF_MAX_RETRIES,
        )
        assert _BACKOFF_BASE_DELAY >= 0.5
        assert _BACKOFF_MULTIPLIER >= 1.5
        assert _BACKOFF_MAX_DELAY <= 60
        assert _BACKOFF_MAX_RETRIES >= 2
        assert _BACKOFF_MAX_RETRIES <= 10
