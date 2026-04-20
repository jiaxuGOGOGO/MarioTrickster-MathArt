"""
SESSION-100 (PERF-1): Targeted regression tests for evolution-loop OOM fix.

These tests verify that the memory optimizations introduced in PERF-1 are
effective and that the public API contract of ``scan_internal_todos`` and
``generate_evolution_report`` remains intact.

Architecture Discipline
-----------------------
- Tests use ``tempfile.TemporaryDirectory`` to construct controlled
  environments instead of scanning the full 120 MB project tree.
- Random inputs use explicit ``np.random.default_rng`` instances per
  NEP-19; no global seed pollution.
- No ``np.zeros`` shortcuts — test data retains realistic variance.
- No production-side seed hardcoding — all DI parameters are keyword-only.

References
----------
[1] NumPy NEP 19 — Random number generator policy
[2] Martin Fowler — Eradicating Non-Determinism in Tests
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mathart.evolution.evolution_loop import (
    EvolutionCycleReport,
    EvolutionProposal,
    _DEFAULT_EXCLUDE_DIRS,
    _DEFAULT_MAX_FILE_SIZE,
    _DEFAULT_PROPOSAL_LIMIT,
    generate_evolution_report,
    run_evolution_cycle,
    save_evolution_report,
    scan_internal_todos,
)


PROJECT_ROOT = Path(__file__).parent.parent


# ── scan_internal_todos: exclude_dirs ────────────────────────────────────────


class TestScanExcludeDirs:
    """Verify that excluded directories are skipped during scanning."""

    def test_default_excludes_evolution_reports(self):
        """evolution_reports/ must be excluded by default."""
        assert "evolution_reports" in _DEFAULT_EXCLUDE_DIRS

    def test_default_excludes_git(self):
        assert ".git" in _DEFAULT_EXCLUDE_DIRS

    def test_default_excludes_pycache(self):
        assert "__pycache__" in _DEFAULT_EXCLUDE_DIRS

    def test_scan_skips_excluded_dir(self):
        """Files inside excluded dirs must not produce proposals."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a TODO in a normal file
            (Path(tmpdir) / "good.py").write_text("# TODO: keep this\n")
            # Create a TODO inside an excluded directory
            excluded = Path(tmpdir) / "evolution_reports"
            excluded.mkdir()
            (excluded / "big.json").write_text("# TODO: should be skipped\n")

            proposals = scan_internal_todos(tmpdir)
            sources = {p.source_file for p in proposals}
            assert any("good.py" in s for s in sources)
            assert not any("evolution_reports" in s for s in sources)

    def test_custom_exclude_dirs(self):
        """Caller can inject custom exclude_dirs via DI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "normal.py").write_text("# TODO: visible\n")
            custom_dir = Path(tmpdir) / "custom_exclude"
            custom_dir.mkdir()
            (custom_dir / "hidden.py").write_text("# TODO: hidden\n")

            proposals = scan_internal_todos(
                tmpdir,
                exclude_dirs=frozenset({"custom_exclude"}),
            )
            sources = {p.source_file for p in proposals}
            assert any("normal.py" in s for s in sources)
            assert not any("custom_exclude" in s for s in sources)


# ── scan_internal_todos: max_file_size ───────────────────────────────────────


class TestScanMaxFileSize:
    """Verify that oversized files are skipped."""

    def test_default_max_file_size_is_1mb(self):
        assert _DEFAULT_MAX_FILE_SIZE == 1 * 1024 * 1024

    def test_large_file_skipped(self):
        """Files exceeding max_file_size must be silently skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            small = Path(tmpdir) / "small.py"
            small.write_text("# TODO: small file\n")

            big = Path(tmpdir) / "big.json"
            # Write a file just over the threshold
            big.write_text("# TODO: big file\n" + "x" * 2048)

            proposals = scan_internal_todos(tmpdir, max_file_size=1024)
            sources = {p.source_file for p in proposals}
            assert any("small.py" in s for s in sources)
            assert not any("big.json" in s for s in sources)

    def test_custom_max_file_size(self):
        """Caller can inject custom max_file_size via DI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "medium.py"
            f.write_text("# TODO: medium\n" + "x" * 500)

            # With a very large limit, the file is scanned
            p1 = scan_internal_todos(tmpdir, max_file_size=10_000)
            assert len(p1) >= 1

            # With a tiny limit, the file is skipped
            p2 = scan_internal_todos(tmpdir, max_file_size=100)
            assert len(p2) == 0


# ── scan_internal_todos: streaming read ──────────────────────────────────────


class TestScanStreaming:
    """Verify that line-by-line streaming produces correct results."""

    def test_multiline_todos(self):
        """Multiple TODOs in one file must all be found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "multi.py"
            f.write_text(
                "# TODO: first\n"
                "x = 1\n"
                "# FIXME: second\n"
                "# HACK: third\n"
            )
            proposals = scan_internal_todos(tmpdir)
            assert len(proposals) == 3
            categories = {p.category for p in proposals}
            assert "todo_resolution" in categories

    def test_incomplete_implementation_detected(self):
        """raise NotImplementedError must be detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "stub.py"
            f.write_text("def foo():\n    raise NotImplementedError\n")
            proposals = scan_internal_todos(tmpdir)
            assert any(p.category == "incomplete_implementation" for p in proposals)


# ── generate_evolution_report: proposal_limit ────────────────────────────────


class TestProposalLimit:
    """Verify that proposal_limit truncates the report."""

    def test_default_limit_is_500(self):
        assert _DEFAULT_PROPOSAL_LIMIT == 500

    def test_truncation_with_many_proposals(self):
        """When proposals exceed the limit, only limit-many are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create enough TODOs to exceed a small limit
            f = root / "many.py"
            lines = [f"# TODO: item {i}\n" for i in range(20)]
            f.write_text("".join(lines))
            # Also create minimal structure for report generation
            (root / "tests").mkdir()
            (root / "mathart/animation").mkdir(parents=True)
            (root / "mathart/evolution").mkdir(parents=True)

            report = generate_evolution_report(
                root,
                session_id="TEST",
                cycle_id="TEST-CYCLE",
                proposal_limit=5,
            )
            assert len(report.proposals) == 5
            assert "truncated" in report.summary

    def test_no_truncation_when_under_limit(self):
        """When proposals are under the limit, all are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "few.py"
            f.write_text("# TODO: just one\n")
            (root / "tests").mkdir()

            report = generate_evolution_report(
                root,
                session_id="TEST",
                cycle_id="TEST-CYCLE",
                proposal_limit=100,
            )
            assert len(report.proposals) <= 100
            assert "truncated" not in report.summary


# ── save_evolution_report: streaming write ───────────────────────────────────


class TestSaveReport:
    """Verify that report saving produces valid JSON."""

    def test_save_produces_valid_json(self):
        report = EvolutionCycleReport(
            cycle_id="TEST-CYCLE",
            session_id="TEST",
            proposals=[
                EvolutionProposal(
                    id="L1-0001",
                    layer=1,
                    category="todo_resolution",
                    title="Test",
                    description="Test desc",
                )
            ],
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = save_evolution_report(report, f.name)
            with open(path) as rf:
                loaded = json.load(rf)
            assert loaded["cycle_id"] == "TEST-CYCLE"
            assert len(loaded["proposals"]) == 1
            os.unlink(path)


# ── Memory regression guard ─────────────────────────────────────────────────


class TestMemoryRegression:
    """Guard against OOM regression on the real project tree."""

    def test_scan_real_project_memory_bounded(self):
        """scan_internal_todos on the real project must stay under 50 MB peak."""
        import tracemalloc

        tracemalloc.start()
        proposals = scan_internal_todos(PROJECT_ROOT)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        # Before PERF-1: ~445 MB peak.  After: should be well under 50 MB.
        assert peak_mb < 50, (
            f"scan_internal_todos peak memory {peak_mb:.1f} MB exceeds 50 MB guard"
        )
        # Proposals should be reasonable (not 36k+ from scanning CYCLE JSONs)
        assert len(proposals) < 1000, (
            f"scan_internal_todos returned {len(proposals)} proposals, "
            "suggesting evolution_reports/ is not being excluded"
        )

    def test_full_cycle_memory_bounded(self):
        """run_evolution_cycle on the real project must stay under 100 MB peak."""
        import tracemalloc

        tracemalloc.start()
        report = run_evolution_cycle(PROJECT_ROOT, session_id="PERF-1-TEST")
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 100, (
            f"run_evolution_cycle peak memory {peak_mb:.1f} MB exceeds 100 MB guard"
        )
        assert isinstance(report, EvolutionCycleReport)
        assert report.session_id == "PERF-1-TEST"
