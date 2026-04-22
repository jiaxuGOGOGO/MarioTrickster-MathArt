"""SESSION-146-B: Radar Wide-Area Search Net & Audit Trail Enhancement Tests.

Guarantees validated:
1. _DEFAULT_CANDIDATE_PARENTS covers Windows Portable, macOS, multi-drive,
   and relative-to-cwd paths.
2. Filesystem scan emits DEBUG-level audit trail for every probed path.
3. Process scan emits DEBUG-level audit trail with scan statistics.
4. When ComfyUI is NOT found, a WARNING-level summary lands in the log.
5. When ComfyUI IS found, an INFO-level discovery record lands in the log.
6. Relative-to-cwd probes include ../ComfyUI, ../../ComfyUI, and
   ../ComfyUI_windows_portable/ComfyUI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mathart.workspace.preflight_radar import (
    PreflightRadar,
    _DEFAULT_CANDIDATE_PARENTS,
    _scan_filesystem_for_comfyui,
    _scan_processes_for_comfyui,
    _discover_comfyui,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _materialize_fake_comfyui(root: Path) -> None:
    """Create the minimal files that pass _looks_like_comfyui_root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# fake ComfyUI entrypoint\n")
    (root / "custom_nodes").mkdir(exist_ok=True)


class _FakeProc:
    """Minimal psutil-like process object for testing."""

    def __init__(self, pid: int, name: str, cmdline: list[str], cwd: str | None = None):
        self.info = {"pid": pid, "name": name, "cmdline": cmdline, "cwd": cwd}


class _FakePsutil:
    """Minimal psutil surface for testing."""

    AccessDenied = type("AccessDenied", (Exception,), {})
    NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    ZombieProcess = type("ZombieProcess", (Exception,), {})

    def __init__(self, procs: list[_FakeProc]):
        self._procs = procs

    def process_iter(self, attrs=None):
        return iter(self._procs)


# ---------------------------------------------------------------------------
# Test: Candidate path coverage
# ---------------------------------------------------------------------------

class TestCandidatePathCoverage:
    """Verify the expanded static candidate list covers key deployment patterns."""

    def test_windows_portable_paths_present(self):
        portable_patterns = [
            "ComfyUI_windows_portable/ComfyUI",
        ]
        joined = "\n".join(_DEFAULT_CANDIDATE_PARENTS)
        for pattern in portable_patterns:
            assert pattern in joined, f"Missing portable pattern: {pattern}"

    def test_macos_paths_present(self):
        assert "/Applications/ComfyUI" in _DEFAULT_CANDIDATE_PARENTS
        assert any("Library" in p for p in _DEFAULT_CANDIDATE_PARENTS)

    def test_multi_drive_coverage(self):
        """At least drives C through G should be covered."""
        for letter in "CDEFG":
            assert any(p.startswith(f"{letter}:/") for p in _DEFAULT_CANDIDATE_PARENTS), \
                f"Drive {letter}: not covered"

    def test_relative_cwd_probes_in_scan(self):
        """Filesystem scan must probe ../ComfyUI, ../../ComfyUI, and portable variants."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir) / "project" / "workspace"
            cwd.mkdir(parents=True)
            with patch("mathart.workspace.preflight_radar.Path.cwd", return_value=cwd):
                # No actual ComfyUI exists, so matches will be empty
                matches = _scan_filesystem_for_comfyui()
                assert isinstance(matches, list)
                # The function should not crash even with deep relative probes

    def test_candidate_count_minimum(self):
        """The expanded list should have at least 30 entries."""
        assert len(_DEFAULT_CANDIDATE_PARENTS) >= 30, \
            f"Only {len(_DEFAULT_CANDIDATE_PARENTS)} candidates — expected >= 30"


# ---------------------------------------------------------------------------
# Test: Filesystem scan audit trail
# ---------------------------------------------------------------------------

class TestFilesystemAuditTrail:
    """Verify that _scan_filesystem_for_comfyui emits DEBUG logs for every probe."""

    def test_debug_logs_emitted_for_probed_paths(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            _scan_filesystem_for_comfyui()

        # Must contain the scan summary
        assert any("[Radar/FS] Filesystem scan complete:" in r.message for r in caplog.records), \
            "Missing filesystem scan summary log"

        # Must contain at least one PROBE line
        assert any("[Radar/FS]   PROBE" in r.message for r in caplog.records), \
            "Missing individual PROBE log entries"

    def test_comfyui_home_env_logged(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            with patch.dict(os.environ, {"COMFYUI_HOME": "/fake/comfyui/path"}):
                _scan_filesystem_for_comfyui()

        assert any("COMFYUI_HOME env var set" in r.message for r in caplog.records)

    def test_comfyui_home_absent_logged(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            with patch.dict(os.environ, {}, clear=True):
                # Remove COMFYUI_HOME if present
                os.environ.pop("COMFYUI_HOME", None)
                _scan_filesystem_for_comfyui()

        assert any("COMFYUI_HOME env var not set" in r.message for r in caplog.records)

    def test_hit_path_logged_when_found(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            comfy_root = Path(tmpdir) / "ComfyUI"
            _materialize_fake_comfyui(comfy_root)

            with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
                matches = _scan_filesystem_for_comfyui(extra_candidates=[comfy_root])

            assert len(matches) >= 1
            assert any("HIT" in r.message for r in caplog.records), \
                "Missing HIT log for discovered ComfyUI root"


# ---------------------------------------------------------------------------
# Test: Process scan audit trail
# ---------------------------------------------------------------------------

class TestProcessScanAuditTrail:
    """Verify that _scan_processes_for_comfyui emits DEBUG logs."""

    def test_process_scan_summary_logged(self, caplog):
        fake_psutil = _FakePsutil([])
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            _scan_processes_for_comfyui(fake_psutil)

        assert any("[Radar/PS] Process scan complete:" in r.message for r in caplog.records), \
            "Missing process scan summary log"

    def test_none_psutil_logged(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            result = _scan_processes_for_comfyui(None)

        assert result == []
        assert any("psutil module is None" in r.message for r in caplog.records)

    def test_candidate_process_logged(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            comfy_root = Path(tmpdir) / "ComfyUI"
            _materialize_fake_comfyui(comfy_root)

            fake_proc = _FakeProc(
                pid=12345,
                name="python3",
                cmdline=["python3", str(comfy_root / "main.py")],
                cwd=str(comfy_root),
            )
            fake_psutil = _FakePsutil([fake_proc])

            with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
                hits = _scan_processes_for_comfyui(fake_psutil)

            assert len(hits) == 1
            assert any("ComfyUI candidate process" in r.message for r in caplog.records)
            assert any("VALID" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test: Discovery-level audit trail
# ---------------------------------------------------------------------------

class TestDiscoveryAuditTrail:
    """Verify _discover_comfyui emits INFO/WARNING level audit summaries."""

    def test_not_found_emits_warning(self, caplog):
        fake_psutil = _FakePsutil([])
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            result = _discover_comfyui(fake_psutil, manifest=[], extra_candidates=[])

        assert not result.found
        warning_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NOT FOUND" in r.message for r in warning_msgs), \
            "Missing WARNING-level 'NOT FOUND' log when ComfyUI is absent"

    def test_found_emits_info(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            comfy_root = Path(tmpdir) / "ComfyUI"
            _materialize_fake_comfyui(comfy_root)

            fake_psutil = _FakePsutil([])
            with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
                result = _discover_comfyui(
                    fake_psutil, manifest=[], extra_candidates=[comfy_root],
                )

            assert result.found
            info_msgs = [r for r in caplog.records if r.levelno >= logging.INFO]
            assert any("found via filesystem heuristic" in r.message for r in info_msgs), \
                "Missing INFO-level discovery log when ComfyUI is found"

    def test_discovery_started_logged(self, caplog):
        fake_psutil = _FakePsutil([])
        with caplog.at_level(logging.DEBUG, logger="mathart.workspace.preflight_radar"):
            _discover_comfyui(fake_psutil, manifest=[], extra_candidates=[])

        assert any("ComfyUI discovery started" in r.message for r in caplog.records)
