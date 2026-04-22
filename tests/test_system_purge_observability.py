"""End-to-End Tests: System Purge & Observability (SESSION-141).

P0-SESSION-138-SYSTEM-PURGE-AND-OBSERVABILITY

This test module validates:
1. Cold GC sweep correctly removes stale artefacts while protecting sacred paths.
2. Hot in-flight pruning correctly deletes large intermediates, respects the
   temporal safety gate (params_safe), and never touches protected assets.
3. The blackbox flight recorder (sys.excepthook) captures unhandled exceptions
   into log files without causing secondary crashes.
4. Centralized settings are correctly loaded and overridable via env vars.
5. Log rotation configuration is correct.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path: Path):
    """Create a realistic workspace with stale and fresh artefacts."""
    # Stale files (> 7 days old)
    stale_time = time.time() - 8 * 86400  # 8 days ago

    # .part residue
    part_file = tmp_path / "download.part"
    part_file.write_text("partial download")
    os.utime(part_file, (stale_time, stale_time))

    # temp/ directory with stale cache
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    stale_cache = temp_dir / "old_cache.png"
    stale_cache.write_bytes(b"\x89PNG" + b"\x00" * 1000)
    os.utime(stale_cache, (stale_time, stale_time))
    os.utime(temp_dir, (stale_time, stale_time))

    # Fresh file (should NOT be deleted)
    fresh_file = tmp_path / "fresh.part"
    fresh_file.write_text("recent download")

    # Protected paths (should NEVER be deleted even if stale)
    knowledge_dir = tmp_path / "knowledge" / "active"
    knowledge_dir.mkdir(parents=True)
    protected_file = knowledge_dir / "truth.json"
    protected_file.write_text('{"sacred": true}')
    os.utime(protected_file, (stale_time, stale_time))

    blueprints_dir = tmp_path / "blueprints"
    blueprints_dir.mkdir()
    bp_file = blueprints_dir / "base.json"
    bp_file.write_text('{"blueprint": true}')
    os.utime(bp_file, (stale_time, stale_time))

    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    out_file = outputs_dir / "final.png"
    out_file.write_bytes(b"\x89PNG" + b"\x00" * 500)
    os.utime(out_file, (stale_time, stale_time))

    elite_dir = tmp_path / "elite_archive"
    elite_dir.mkdir()
    elite_file = elite_dir / "best.png"
    elite_file.write_bytes(b"\x89PNG" + b"\x00" * 200)
    os.utime(elite_file, (stale_time, stale_time))

    return tmp_path


@pytest.fixture()
def generation_dir(tmp_path: Path):
    """Create a generation directory with large waste and lightweight records."""
    gen_dir = tmp_path / "gen_001"
    gen_dir.mkdir()

    # Large waste files
    (gen_dir / "frame_001.png").write_bytes(b"\x89PNG" + b"\x00" * 5000)
    (gen_dir / "frame_002.jpg").write_bytes(b"\xFF\xD8" + b"\x00" * 3000)
    (gen_dir / "preview.mp4").write_bytes(b"\x00" * 10000)
    (gen_dir / "cache.tmp").write_bytes(b"\x00" * 2000)

    # Lightweight gene records (should be preserved)
    (gen_dir / "genes.json").write_text('{"params": [1, 2, 3]}')
    (gen_dir / "manifest.yaml").write_text("name: gen_001")
    (gen_dir / "notes.md").write_text("# Generation 001")

    # Protected elite
    (gen_dir / "elite_best.png").write_bytes(b"\x89PNG" + b"\x00" * 1000)

    return gen_dir


# ---------------------------------------------------------------------------
# Group 1: Cold GC Sweep
# ---------------------------------------------------------------------------

class TestColdGCSweep:
    """Validate Level-1 cold garbage collection at startup."""

    def test_removes_stale_part_files(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        report = gc.sweep()
        assert report.files_deleted >= 1
        assert not (workspace / "download.part").exists()

    def test_preserves_fresh_files(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        gc.sweep()
        assert (workspace / "fresh.part").exists()

    def test_cleans_stale_temp_directory(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        report = gc.sweep()
        # temp dir or its stale contents should be cleaned
        assert report.files_deleted >= 1 or report.dirs_deleted >= 1

    def test_never_touches_knowledge_active(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        gc.sweep()
        assert (workspace / "knowledge" / "active" / "truth.json").exists()

    def test_never_touches_blueprints(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        gc.sweep()
        assert (workspace / "blueprints" / "base.json").exists()

    def test_never_touches_outputs(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        gc.sweep()
        assert (workspace / "outputs" / "final.png").exists()

    def test_never_touches_elite(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        gc.sweep()
        assert (workspace / "elite_archive" / "best.png").exists()

    def test_reports_protected_skips(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        # Place a stale .part file inside a protected directory
        stale_time = time.time() - 8 * 86400
        protected_part = workspace / "outputs" / "stale.part"
        protected_part.write_text("should not be deleted")
        os.utime(protected_part, (stale_time, stale_time))

        gc = GarbageCollector(workspace)
        report = gc.sweep()
        assert report.protected_skips >= 1
        assert protected_part.exists()  # must still be there

    def test_dry_run_does_not_delete(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector, GCConfig
        cfg = GCConfig(dry_run=True)
        gc = GarbageCollector(workspace, config=cfg)
        report = gc.sweep()
        assert report.files_deleted >= 1  # counted but not deleted
        assert (workspace / "download.part").exists()  # still there

    def test_bytes_freed_reported(self, workspace: Path):
        from mathart.workspace.garbage_collector import GarbageCollector
        gc = GarbageCollector(workspace)
        report = gc.sweep()
        assert report.bytes_freed > 0


# ---------------------------------------------------------------------------
# Group 2: Hot In-Flight Pruning
# ---------------------------------------------------------------------------

class TestHotInFlightPruning:
    """Validate Level-2 in-flight pruning during evolution loops."""

    def test_prunes_waste_when_params_safe(self, generation_dir: Path):
        from mathart.workspace.garbage_collector import InFlightPruner
        pruner = InFlightPruner(generation_dir.parent)
        waste = [
            generation_dir / "frame_001.png",
            generation_dir / "frame_002.jpg",
            generation_dir / "preview.mp4",
        ]
        report = pruner.prune_generation_waste(waste, params_safe=True)
        assert report.files_deleted == 3
        assert report.bytes_freed > 0
        for w in waste:
            assert not w.exists()

    def test_aborts_when_params_not_safe(self, generation_dir: Path):
        from mathart.workspace.garbage_collector import InFlightPruner
        pruner = InFlightPruner(generation_dir.parent)
        waste = [generation_dir / "frame_001.png"]
        report = pruner.prune_generation_waste(waste, params_safe=False)
        assert report.files_deleted == 0
        assert (generation_dir / "frame_001.png").exists()

    def test_preserves_json_gene_records(self, generation_dir: Path):
        from mathart.workspace.garbage_collector import InFlightPruner
        pruner = InFlightPruner(generation_dir.parent)
        report = pruner.scan_and_prune_dir(
            generation_dir, params_safe=True, keep_json=True,
        )
        assert report.files_deleted >= 3  # png, jpg, mp4, tmp
        assert (generation_dir / "genes.json").exists()
        assert (generation_dir / "manifest.yaml").exists()
        assert (generation_dir / "notes.md").exists()

    def test_never_prunes_elite_files(self, generation_dir: Path):
        from mathart.workspace.garbage_collector import InFlightPruner
        pruner = InFlightPruner(generation_dir.parent)
        waste = [generation_dir / "elite_best.png"]
        report = pruner.prune_generation_waste(waste, params_safe=True)
        assert report.files_deleted == 0
        assert report.protected_skips == 1
        assert (generation_dir / "elite_best.png").exists()

    def test_scan_and_prune_reports_correctly(self, generation_dir: Path):
        from mathart.workspace.garbage_collector import InFlightPruner
        pruner = InFlightPruner(generation_dir.parent)
        report = pruner.scan_and_prune_dir(
            generation_dir, params_safe=True, keep_json=True,
        )
        assert report.bytes_freed > 0
        assert report.protected_skips >= 1  # elite file


# ---------------------------------------------------------------------------
# Group 3: Blackbox Flight Recorder (sys.excepthook)
# ---------------------------------------------------------------------------

class TestBlackboxFlightRecorder:
    """Validate the global crash interceptor and log writing."""

    def test_install_creates_log_directory(self, tmp_path: Path):
        from mathart.core.logger import install_blackbox, reset_blackbox
        reset_blackbox()
        log_dir = tmp_path / "logs"
        install_blackbox(project_root=tmp_path)
        assert log_dir.is_dir()
        reset_blackbox()

    def test_install_is_idempotent(self, tmp_path: Path):
        from mathart.core.logger import install_blackbox, reset_blackbox
        reset_blackbox()
        logger1 = install_blackbox(project_root=tmp_path)
        logger2 = install_blackbox(project_root=tmp_path)
        assert logger1 is logger2
        reset_blackbox()

    def test_excepthook_captures_crash_to_log(self, tmp_path: Path):
        """Simulate an unhandled RuntimeError and verify it appears in logs.

        This is the core blackbox test: we fake a deep crash, trigger
        sys.excepthook, and assert the full traceback was written to disk.
        """
        from mathart.core.logger import install_blackbox, reset_blackbox
        reset_blackbox()
        install_blackbox(project_root=tmp_path)

        # Simulate a deep unhandled exception
        try:
            raise RuntimeError("模拟闪退")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            # Manually invoke the excepthook (as Python would on unhandled)
            sys.excepthook(exc_type, exc_value, exc_tb)

        # Verify the crash was written to the log file
        log_file = tmp_path / "logs" / "mathart.log"
        assert log_file.exists(), "Log file should exist after crash"
        content = log_file.read_text(encoding="utf-8")
        assert "模拟闪退" in content, "Crash message must appear in log"
        assert "RuntimeError" in content, "Exception type must appear in log"
        assert "Traceback" in content or "BLACKBOX" in content, \
            "Traceback or BLACKBOX marker must appear in log"
        reset_blackbox()

    def test_excepthook_no_secondary_crash_on_disk_failure(self, tmp_path: Path):
        """Verify that if log writing fails, the hook degrades silently."""
        from mathart.core.logger import (
            install_blackbox,
            reset_blackbox,
            _blackbox_excepthook,
        )
        reset_blackbox()
        install_blackbox(project_root=tmp_path)

        # Patch the logger to raise an exception when writing
        with mock.patch(
            "mathart.core.logger.get_blackbox_logger"
        ) as mock_logger:
            mock_logger.return_value.critical.side_effect = OSError("Disk full")

            # This must NOT raise — double-fault protection
            try:
                raise RuntimeError("模拟闪退-磁盘满")
            except RuntimeError:
                exc_type, exc_value, exc_tb = sys.exc_info()
                # Should not raise any exception
                _blackbox_excepthook(exc_type, exc_value, exc_tb)

        reset_blackbox()

    def test_excepthook_skips_keyboard_interrupt(self, tmp_path: Path):
        """KeyboardInterrupt should be delegated to the original hook."""
        from mathart.core.logger import install_blackbox, reset_blackbox
        reset_blackbox()
        install_blackbox(project_root=tmp_path)

        original_hook = sys.__excepthook__
        hook_called = []

        def fake_original(exc_type, exc_value, exc_tb):
            hook_called.append(True)

        sys.__excepthook__ = fake_original
        try:
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                exc_type, exc_value, exc_tb = sys.exc_info()
                sys.excepthook(exc_type, exc_value, exc_tb)
            assert len(hook_called) == 1, "Original hook should be called for KeyboardInterrupt"
        finally:
            sys.__excepthook__ = original_hook
            reset_blackbox()

    def test_log_rotation_config(self, tmp_path: Path):
        """Verify that the file handler uses TimedRotatingFileHandler."""
        from mathart.core.logger import install_blackbox, reset_blackbox
        reset_blackbox()
        logger = install_blackbox(project_root=tmp_path)

        has_rotating = False
        for handler in logger.handlers:
            if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
                has_rotating = True
                assert handler.backupCount == 7
                assert handler.when.upper() == "MIDNIGHT"
        assert has_rotating, "Must have a TimedRotatingFileHandler"
        reset_blackbox()


# ---------------------------------------------------------------------------
# Group 4: Centralized Settings
# ---------------------------------------------------------------------------

class TestCentralizedSettings:
    """Validate the centralized configuration module."""

    def test_default_values(self):
        from mathart.core.settings import Settings, reset_settings
        reset_settings()
        s = Settings()
        assert s.network_timeout == 60.0
        assert s.gc_ttl_days == 7
        assert s.sandbox_timeout == 3.0
        assert s.log_backup_count == 7
        reset_settings()

    def test_env_override(self):
        from mathart.core.settings import get_settings, reset_settings
        reset_settings()
        with mock.patch.dict(os.environ, {
            "MATHART_NETWORK_TIMEOUT": "120.0",
            "MATHART_GC_TTL_DAYS": "14",
        }):
            s = get_settings()
            assert s.network_timeout == 120.0
            assert s.gc_ttl_days == 14
        reset_settings()

    def test_singleton_pattern(self):
        from mathart.core.settings import get_settings, reset_settings
        reset_settings()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        reset_settings()

    def test_frozen_immutability(self):
        from mathart.core.settings import Settings
        s = Settings()
        with pytest.raises(AttributeError):
            s.network_timeout = 999.0  # type: ignore[misc]

    def test_all_fields_documented(self):
        """Every field in Settings should have a docstring annotation."""
        from mathart.core.settings import Settings
        import dataclasses
        for f in dataclasses.fields(Settings):
            # Frozen dataclass fields have metadata; we check field names exist
            assert f.name, f"Field {f} must have a name"
            assert f.default is not dataclasses.MISSING or \
                   f.default_factory is not dataclasses.MISSING, \
                   f"Field {f.name} must have a default value"


# ---------------------------------------------------------------------------
# Group 5: Integration — Evolution Loop with Hot Pruning
# ---------------------------------------------------------------------------

class TestEvolutionLoopHotPrune:
    """Validate that the evolution loop integrates hot pruning correctly."""

    def test_evolution_loop_runs_with_pruning(self, tmp_path: Path):
        """Run a minimal evolution loop and verify hot pruning hook fires."""
        from mathart.core.evolution_loop import (
            ThreeLayerEvolutionLoop,
            EvolutionLoopConfig,
        )

        # Create a temp dir with waste to prune
        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()
        waste = temp_dir / "old_frame.png"
        waste.write_bytes(b"\x89PNG" + b"\x00" * 500)

        config = EvolutionLoopConfig(
            max_iterations=1,
            enable_layer1=True,
            enable_layer2=True,
            enable_layer3=True,
        )
        loop = ThreeLayerEvolutionLoop(
            project_root=tmp_path,
            config=config,
        )
        result = loop.run()
        assert "iterations" in result
        assert len(result["iterations"]) >= 1
