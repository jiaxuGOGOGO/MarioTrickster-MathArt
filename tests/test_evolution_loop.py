"""
SESSION-042: Three-Layer Evolution Loop Tests

Tests the evolution loop machinery:
  - Layer 1: Internal TODO/FIXME scanning
  - Layer 2: External knowledge distillation registry & validation
  - Layer 3: Test metrics collection
  - Full cycle report generation
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mathart.evolution.evolution_loop import (
    EvolutionProposal,
    DistillationRecord,
    TestEvolutionResult,
    EvolutionCycleReport,
    scan_internal_todos,
    get_distillation_registry,
    validate_distillations,
    count_test_functions,
    generate_evolution_report,
    save_evolution_report,
    run_evolution_cycle,
    GAP1_DISTILLATIONS,
)


PROJECT_ROOT = Path(__file__).parent.parent


class TestEvolutionProposal:
    def test_to_dict(self):
        p = EvolutionProposal(
            id="L1-0001", layer=1, category="todo_resolution",
            title="Test", description="Test desc",
        )
        d = p.to_dict()
        assert d["id"] == "L1-0001"
        assert d["layer"] == 1
        assert "created_at" in d

    def test_default_status(self):
        p = EvolutionProposal(
            id="L1-0002", layer=1, category="test",
            title="T", description="D",
        )
        assert p.status == "proposed"
        assert p.priority == "medium"


class TestDistillationRecord:
    def test_to_dict(self):
        r = DistillationRecord(
            paper_id="test001", paper_title="Test Paper",
            authors="Author", venue="Test 2024",
            concept="Test concept", target_module="test.py",
            target_class="TestClass",
        )
        d = r.to_dict()
        assert d["paper_id"] == "test001"
        assert "integration_date" in d


class TestLayer1InternalEvolution:
    def test_scan_finds_todos(self):
        proposals = scan_internal_todos(PROJECT_ROOT)
        # The project likely has some TODOs
        assert isinstance(proposals, list)
        for p in proposals:
            assert p.layer == 1
            assert p.id.startswith("L1-")

    def test_scan_with_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file with known TODOs
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text(
                "# TODO: Fix this thing\n"
                "# FIXME: Critical bug\n"
                "def foo():\n"
                "    raise NotImplementedError\n"
            )
            proposals = scan_internal_todos(tmpdir)
            assert len(proposals) >= 2  # At least TODO + FIXME
            categories = {p.category for p in proposals}
            assert "todo_resolution" in categories

    def test_scan_ignores_git_dir(self):
        proposals = scan_internal_todos(PROJECT_ROOT)
        for p in proposals:
            assert ".git" not in p.source_file


class TestLayer2KnowledgeDistillation:
    def test_gap1_distillations_registered(self):
        records = get_distillation_registry()
        assert len(records) >= 3  # At least 3 Gap 1 records
        paper_ids = {r.paper_id for r in records}
        assert "starke2020local" in paper_ids
        assert "starke2022deepphase" in paper_ids
        assert "gap1_architecture" in paper_ids

    def test_validate_distillations(self):
        results = validate_distillations(PROJECT_ROOT)
        assert len(results) >= 3
        for r in results:
            assert "paper_id" in r
            assert "target_exists" in r
            assert "status" in r

    def test_all_gap1_targets_exist(self):
        results = validate_distillations(PROJECT_ROOT)
        for r in results:
            assert r["target_exists"] is True, f"Target missing for {r['paper_id']}"

    def test_all_gap1_tests_exist(self):
        results = validate_distillations(PROJECT_ROOT)
        for r in results:
            assert r["test_exists"] is True, f"Test missing for {r['paper_id']}"


class TestLayer3TestEvolution:
    def test_count_test_functions(self):
        counts = count_test_functions(PROJECT_ROOT)
        assert isinstance(counts, dict)
        assert len(counts) > 0
        # Our new test file should be counted
        assert "test_phase_state.py" in counts
        assert counts["test_phase_state.py"] >= 30  # We wrote 36 tests

    def test_evolution_report_generation(self):
        report = generate_evolution_report(PROJECT_ROOT)
        assert isinstance(report, EvolutionCycleReport)
        assert report.session_id == "SESSION-042"
        assert len(report.distillations) >= 3
        assert report.test_result is not None
        assert report.test_result.total_tests > 0
        assert len(report.summary) > 0


class TestEvolutionCycleReport:
    def test_to_dict(self):
        report = generate_evolution_report(PROJECT_ROOT)
        d = report.to_dict()
        assert "cycle_id" in d
        assert "proposals" in d
        assert "distillations" in d
        assert "test_result" in d
        assert "summary" in d

    def test_save_and_load(self):
        report = generate_evolution_report(PROJECT_ROOT)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = save_evolution_report(report, f.name)
            with open(path, "r") as rf:
                loaded = json.load(rf)
            assert loaded["session_id"] == "SESSION-042"
            assert len(loaded["distillations"]) >= 3


class TestFullEvolutionCycle:
    def test_run_evolution_cycle(self):
        report = run_evolution_cycle(PROJECT_ROOT, session_id="SESSION-042")
        assert isinstance(report, EvolutionCycleReport)
        assert report.session_id == "SESSION-042"
        # Report should be saved
        report_dir = PROJECT_ROOT / "evolution_reports"
        assert report_dir.exists()
        report_files = list(report_dir.glob("CYCLE-*.json"))
        assert len(report_files) >= 1
