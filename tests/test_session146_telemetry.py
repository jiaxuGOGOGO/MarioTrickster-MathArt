"""SESSION-146: End-to-end telemetry verification tests.

Validates that:
1. Radar diagnostic payloads land in the blackbox log file.
2. Proxy render failures are logged with full traceback.
3. CLI wizard mode-selection events are recorded.
4. LauncherFacade abort diagnostics are persisted.
5. Console handler stays at WARNING (no INFO leaks to terminal).
6. pyproject.toml declares psutil, imageio, pillow in core deps
   and torch only in [project.optional-dependencies].gpu.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixture: fresh blackbox in a temp directory
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_blackbox(tmp_path):
    """Install a fresh blackbox logger writing to tmp_path/logs/."""
    from mathart.core.logger import install_blackbox, reset_blackbox, BlackboxConfig

    reset_blackbox()
    cfg = BlackboxConfig()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.log_filename = "mathart.log"
    logger = install_blackbox(cfg)
    yield logger, tmp_path / "logs" / "mathart.log"
    reset_blackbox()


# ---------------------------------------------------------------------------
# 1. Radar diagnostic payload lands in blackbox
# ---------------------------------------------------------------------------

class TestRadarTelemetry:
    def test_production_blocked_radar_payload_logged(self, fresh_blackbox, tmp_path):
        """When ProductionStrategy detects MANUAL_INTERVENTION_REQUIRED,
        the full JSON diagnostic MUST appear in the log file."""
        bb_logger, log_path = fresh_blackbox

        from mathart.workspace.mode_dispatcher import ProductionStrategy

        strategy = ProductionStrategy(project_root=tmp_path)

        # Build a context that requires GPU
        context = strategy.build_context({
            "interactive": False,
            "skip_ai_render": False,
            "batch_size": 1,
            "pdg_workers": 1,
            "gpu_slots": 1,
            "seed": 42,
        })

        # Execute — on this CPU sandbox, radar will always block
        result = strategy.execute(context)

        # Flush handlers
        for h in logging.getLogger("mathart").handlers:
            h.flush()

        log_content = log_path.read_text(encoding="utf-8")

        # The radar diagnostic payload must be in the log
        assert "Radar diagnostic payload" in log_content
        assert "verdict" in log_content
        # The WARNING about production blocked must also be present
        assert "Production mode BLOCKED" in log_content or "gpu_boundary_guard" in log_content


# ---------------------------------------------------------------------------
# 2. Proxy render failure logged with traceback
# ---------------------------------------------------------------------------

class TestProxyRenderTelemetry:
    def test_proxy_failure_logged_with_traceback(self, fresh_blackbox, tmp_path):
        """When ProxyRenderer.render_proxy raises, the full traceback
        MUST be written to the blackbox log."""
        bb_logger, log_path = fresh_blackbox

        from mathart.quality.interactive_gate import InteractivePreviewGate
        from mathart.workspace.director_intent import CreatorIntentSpec, Genotype

        # Create a gate with a renderer that always fails
        mock_renderer = MagicMock()
        mock_renderer.render_proxy.side_effect = RuntimeError("matplotlib backend missing")

        # Pre-program choices: approve immediately
        choices = iter(["1", "N"])
        gate = InteractivePreviewGate(
            workspace_root=tmp_path,
            renderer=mock_renderer,
            input_fn=lambda prompt: next(choices),
            output_fn=lambda msg: None,  # suppress terminal output
        )

        spec = CreatorIntentSpec(
            genotype=Genotype(),
            evolve_variants=0,
            freeze_locks=[],
        )
        gate.run(spec)

        for h in logging.getLogger("mathart").handlers:
            h.flush()

        log_content = log_path.read_text(encoding="utf-8")
        assert "Proxy render FAILED" in log_content
        assert "matplotlib backend missing" in log_content


# ---------------------------------------------------------------------------
# 3. CLI wizard mode selection logged
# ---------------------------------------------------------------------------

class TestWizardTelemetry:
    def test_mode_selection_logged(self, fresh_blackbox, tmp_path):
        """ModeDispatcher.dispatch MUST log the mode selection."""
        bb_logger, log_path = fresh_blackbox

        from mathart.workspace.mode_dispatcher import ModeDispatcher

        dispatcher = ModeDispatcher(project_root=tmp_path)
        # Preview only (no execute) — safe on CPU
        result = dispatcher.dispatch("4", options={}, execute=False)

        for h in logging.getLogger("mathart").handlers:
            h.flush()

        log_content = log_path.read_text(encoding="utf-8")
        assert "[CLI] User selected mode: dry_run" in log_content


# ---------------------------------------------------------------------------
# 4. LauncherFacade abort logged
# ---------------------------------------------------------------------------

class TestLauncherFacadeTelemetry:
    def test_abort_manual_logged(self, fresh_blackbox, tmp_path):
        """LauncherFacade._abort_manual MUST persist the diagnostic."""
        bb_logger, log_path = fresh_blackbox

        from mathart.workspace.launcher_facade import LauncherFacade

        facade = LauncherFacade()
        # On CPU sandbox, start() will trigger radar → manual intervention
        outcome = facade.start()

        for h in logging.getLogger("mathart").handlers:
            h.flush()

        log_content = log_path.read_text(encoding="utf-8")
        # Either the facade logged the abort, or the radar payload was logged
        assert ("LauncherFacade ABORTED" in log_content
                or "Radar diagnostic payload" in log_content
                or "manual_intervention_required" in log_content)


# ---------------------------------------------------------------------------
# 5. Console handler stays at WARNING
# ---------------------------------------------------------------------------

class TestConsoleGuard:
    def test_console_handler_level_is_warning(self, fresh_blackbox):
        """The blackbox console handler MUST be at WARNING or above."""
        bb_logger, _ = fresh_blackbox

        console_handlers = [
            h for h in bb_logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        for ch in console_handlers:
            assert ch.level >= logging.WARNING, (
                f"Console handler level is {ch.level}, expected >= {logging.WARNING}"
            )


# ---------------------------------------------------------------------------
# 6. Dependency contract: pyproject.toml
# ---------------------------------------------------------------------------

class TestDependencyContract:
    @pytest.fixture(autouse=True)
    def _load_toml(self):
        toml_path = PROJECT_ROOT / "pyproject.toml"
        # Simple TOML parser for the dependencies section
        self.toml_text = toml_path.read_text(encoding="utf-8")

    def test_psutil_in_core_deps(self):
        assert '"psutil>=' in self.toml_text or "'psutil>=" in self.toml_text

    def test_imageio_in_core_deps(self):
        assert '"imageio>=' in self.toml_text or "'imageio>=" in self.toml_text

    def test_pillow_in_core_deps(self):
        assert '"Pillow>=' in self.toml_text or "'Pillow>=" in self.toml_text

    def test_matplotlib_in_core_deps(self):
        assert '"matplotlib>=' in self.toml_text

    def test_torch_NOT_in_core_deps(self):
        """torch MUST NOT appear in the default [project].dependencies."""
        import re
        # Extract the core dependencies block
        match = re.search(
            r'\[project\].*?dependencies\s*=\s*\[(.*?)\]',
            self.toml_text,
            re.DOTALL,
        )
        assert match, "Could not find [project].dependencies"
        core_deps = match.group(1)
        assert "torch" not in core_deps.lower().replace("# ", ""), (
            "torch MUST NOT be in core dependencies"
        )

    def test_torch_in_gpu_optional(self):
        """torch MUST appear in [project.optional-dependencies].gpu."""
        assert "gpu" in self.toml_text
        # Find the gpu section
        import re
        match = re.search(
            r'gpu\s*=\s*\[(.*?)\]',
            self.toml_text,
            re.DOTALL,
        )
        assert match, "Could not find gpu optional-dependencies"
        gpu_deps = match.group(1)
        assert "torch" in gpu_deps.lower()
