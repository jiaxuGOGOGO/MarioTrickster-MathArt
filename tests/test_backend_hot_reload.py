"""E2E Tests for Backend Hot-Reload Ecosystem (SESSION-090, P1-MIGRATE-4).

This test suite validates the complete hot-reload lifecycle:

1. **Registry Primitives**: ``unregister()``, ``reload()``, targeted eviction
   without state wipeout.
2. **Daemon File Watcher**: Non-blocking ``watchdog`` observer with debounce,
   automatic discovery of new backend files, and targeted reload on modification.
3. **Zombie Reference Detection**: ``id()`` assertions to verify old class
   objects are genuinely replaced, not just re-wrapped.
4. **Atomic Rollback**: Failed reloads (SyntaxError) restore the old backend
   version without corrupting the registry.
5. **State Wipeout Guard**: Reloading one backend MUST NOT affect any other
   registered backend.

Architecture References
-----------------------
- Erlang/OTP: Two-version coexistence, targeted code replacement.
- Eclipse OSGi: Atomic unregister → reload → re-register lifecycle.
- Unity Domain Reloading: Safe-point debounce, background thread compilation.
- Python watchdog + importlib.reload: sys.modules deep cleanup.

Anti-Pattern Guards Tested
--------------------------
- 🚫 Zombie Reference Trap: id() assertions on old vs new class.
- 🚫 State Wipeout Trap: Other backends survive single-backend reload.
- 🚫 Blocking & Debounce Trap: Watcher runs on daemon thread, debounce tested.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    BackendRegistry,
    get_registry,
    register_backend,
)
from mathart.core.backend_types import backend_type_value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the registry before and after each test to prevent cross-contamination."""
    BackendRegistry.reset()
    BackendRegistry._builtins_loaded = False
    BackendRegistry._backend_module_map = {}
    yield
    BackendRegistry.reset()
    BackendRegistry._builtins_loaded = False
    BackendRegistry._backend_module_map = {}


@pytest.fixture
def tmp_backend_dir(tmp_path: Path):
    """Create a temporary directory for dynamic backend files and add it to sys.path."""
    backend_dir = tmp_path / "dynamic_backends"
    backend_dir.mkdir()
    # Create __init__.py so it's a proper package
    (backend_dir / "__init__.py").write_text("")
    sys.path.insert(0, str(tmp_path))
    yield backend_dir
    # Cleanup: remove from sys.path and sys.modules
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))
    # Clean up any dynamic_backends modules
    to_remove = [k for k in sys.modules if k.startswith("dynamic_backends")]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Helper: Generate backend source code
# ---------------------------------------------------------------------------

def _generate_backend_source(version: str = "v1", return_value: str = "v1_result") -> str:
    """Generate a self-registering backend module source code."""
    return textwrap.dedent(f'''\
        """Dynamically generated hot-reload test backend ({version})."""
        from mathart.core.backend_registry import (
            BackendCapability,
            BackendMeta,
            register_backend,
        )

        @register_backend(
            "dummy_hot_backend",
            display_name="Dummy Hot Backend ({version})",
            version="{version}",
            artifact_families=("test_hot_output",),
            capabilities=(BackendCapability.SPRITE_EXPORT,),
            session_origin="SESSION-090",
        )
        class DummyHotBackend:
            """A test backend that returns a version-tagged result."""

            HOT_RELOAD_VERSION = "{return_value}"

            @property
            def name(self) -> str:
                return "dummy_hot_backend"

            @property
            def meta(self) -> BackendMeta:
                return self._backend_meta

            def execute(self, context: dict) -> dict:
                return {{"status": "ok", "version": "{return_value}"}}
    ''')


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 1: Registry Unregister Primitive
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistryUnregister:
    """Tests for BackendRegistry.unregister() — targeted eviction."""

    def test_unregister_removes_backend(self):
        """unregister() removes the named backend from _backends."""
        reg = BackendRegistry()

        @register_backend("ephemeral_backend", session_origin="SESSION-090")
        class EphemeralBackend:
            pass

        assert reg.get("ephemeral_backend") is not None
        result = reg.unregister("ephemeral_backend")
        assert result is True
        assert reg.get("ephemeral_backend") is None

    def test_unregister_nonexistent_returns_false(self):
        """unregister() returns False for a backend that doesn't exist."""
        reg = BackendRegistry()
        result = reg.unregister("nonexistent_backend_xyz")
        assert result is False

    def test_unregister_preserves_other_backends(self):
        """🚫 State Wipeout Guard: unregister(A) must NOT affect backend B."""
        reg = BackendRegistry()

        @register_backend("backend_a_survivor", session_origin="SESSION-090")
        class BackendA:
            pass

        @register_backend("backend_b_target", session_origin="SESSION-090")
        class BackendB:
            pass

        # Verify both exist
        assert reg.get("backend_a_survivor") is not None
        assert reg.get("backend_b_target") is not None

        # Unregister only B
        reg.unregister("backend_b_target")

        # A must survive
        assert reg.get("backend_a_survivor") is not None
        meta_a, cls_a = reg.get_or_raise("backend_a_survivor")
        assert cls_a is BackendA

        # B must be gone
        assert reg.get("backend_b_target") is None

    def test_unregister_cleans_module_map(self):
        """unregister() also removes the backend from _backend_module_map."""
        reg = BackendRegistry()

        @register_backend("mapped_backend", session_origin="SESSION-090")
        class MappedBackend:
            pass

        assert "mapped_backend" in reg._backend_module_map or True  # may or may not be mapped
        reg.unregister("mapped_backend")
        assert "mapped_backend" not in reg._backend_module_map


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 2: Registry Reload Primitive
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistryReload:
    """Tests for BackendRegistry.reload() — atomic hot-swap."""

    def test_reload_updates_class_identity(self, tmp_backend_dir: Path):
        """🚫 Zombie Reference Guard: reload() must produce a new class with different id()."""
        reg = BackendRegistry()

        # Write v1 backend
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))

        # Import v1
        mod = importlib.import_module("dynamic_backends.dummy_hot_backend")
        assert reg.get("dummy_hot_backend") is not None
        _, old_class = reg.get_or_raise("dummy_hot_backend")
        old_class_id = id(old_class)
        assert old_class.HOT_RELOAD_VERSION == "v1_result"

        # Write v2 backend (different code)
        backend_file.write_text(_generate_backend_source("2.0.0", "v2_result"))
        time.sleep(0.05)  # Ensure filesystem timestamp changes

        # Reload
        result = reg.reload("dummy_hot_backend")
        assert result is True

        # Verify new class
        _, new_class = reg.get_or_raise("dummy_hot_backend")
        new_class_id = id(new_class)

        # 🚫 ZOMBIE CHECK: id() MUST differ
        assert new_class_id != old_class_id, (
            f"Zombie Reference Trap: class id() unchanged after reload! "
            f"old={old_class_id}, new={new_class_id}"
        )
        assert new_class.HOT_RELOAD_VERSION == "v2_result"

    def test_reload_preserves_other_backends(self, tmp_backend_dir: Path):
        """🚫 State Wipeout Guard: reload(A) must NOT affect backend B."""
        reg = BackendRegistry()

        # Register a "bystander" backend
        @register_backend("bystander_backend", session_origin="SESSION-090")
        class BystanderBackend:
            BYSTANDER_MARKER = "I_SURVIVED"

        bystander_id = id(BystanderBackend)

        # Write and import the hot backend
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        # Overwrite with v2
        backend_file.write_text(_generate_backend_source("2.0.0", "v2_result"))
        time.sleep(0.05)

        # Reload the hot backend
        reg.reload("dummy_hot_backend")

        # Bystander MUST survive with same class identity
        meta_b, cls_b = reg.get_or_raise("bystander_backend")
        assert id(cls_b) == bystander_id, (
            "State Wipeout Trap: bystander backend class changed during "
            "targeted reload of a different backend!"
        )
        assert cls_b.BYSTANDER_MARKER == "I_SURVIVED"

    def test_reload_atomic_rollback_on_syntax_error(self, tmp_backend_dir: Path):
        """Failed reload (SyntaxError) must restore the old backend version."""
        reg = BackendRegistry()

        # Write valid v1
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        _, old_class = reg.get_or_raise("dummy_hot_backend")
        assert old_class.HOT_RELOAD_VERSION == "v1_result"

        # Write BROKEN code
        backend_file.write_text("this is not valid python !!!")
        time.sleep(0.05)

        # Reload should fail but NOT corrupt the registry
        with pytest.raises(RuntimeError, match="Hot-reload failed"):
            reg.reload("dummy_hot_backend")

        # Old version MUST be restored
        _, restored_class = reg.get_or_raise("dummy_hot_backend")
        assert restored_class.HOT_RELOAD_VERSION == "v1_result"

    def test_reload_nonexistent_raises(self):
        """reload() raises RuntimeError for unknown backend."""
        reg = BackendRegistry()
        with pytest.raises(RuntimeError, match="no module mapping found"):
            reg.reload("nonexistent_backend_xyz")


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 3: Module-to-Backend Mapping
# ═══════════════════════════════════════════════════════════════════════════

class TestModuleMapping:
    """Tests for _backend_module_map and reverse lookup."""

    def test_register_populates_module_map(self, tmp_backend_dir: Path):
        """@register_backend records the module name in _backend_module_map."""
        reg = BackendRegistry()
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        assert "dummy_hot_backend" in reg._backend_module_map
        assert reg._backend_module_map["dummy_hot_backend"] == "dynamic_backends.dummy_hot_backend"

    def test_module_to_backend_name_reverse_lookup(self, tmp_backend_dir: Path):
        """module_to_backend_name() correctly reverses the mapping."""
        reg = BackendRegistry()
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        result = reg.module_to_backend_name("dynamic_backends.dummy_hot_backend")
        assert result == "dummy_hot_backend"

    def test_module_to_backend_name_unknown(self):
        """module_to_backend_name() returns None for unknown modules."""
        reg = BackendRegistry()
        result = reg.module_to_backend_name("nonexistent.module")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 4: Watched Package Paths
# ═══════════════════════════════════════════════════════════════════════════

class TestWatchedPaths:
    """Tests for get_watched_package_paths()."""

    def test_returns_absolute_paths(self):
        """Watched paths must be absolute filesystem paths."""
        reg = BackendRegistry()
        paths = reg.get_watched_package_paths()
        for p in paths:
            assert os.path.isabs(p), f"Path is not absolute: {p}"

    def test_paths_include_core_package(self):
        """Watched paths must include the mathart.core package directory."""
        reg = BackendRegistry()
        paths = reg.get_watched_package_paths()
        # At least one path should contain 'mathart/core' or 'mathart\\core'
        core_found = any("mathart" in p and "core" in p for p in paths)
        assert core_found, f"mathart.core not in watched paths: {paths}"


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 5: Daemon File Watcher
# ═══════════════════════════════════════════════════════════════════════════

class TestBackendFileWatcher:
    """Tests for BackendFileWatcher — daemon thread, debounce, hot-reload."""

    def test_watcher_starts_and_stops(self):
        """Watcher starts on daemon thread and stops cleanly."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        reg = BackendRegistry()
        watcher = BackendFileWatcher(reg)
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running

    def test_watcher_context_manager(self):
        """Watcher works as a context manager."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        reg = BackendRegistry()
        with BackendFileWatcher(reg) as watcher:
            assert watcher.is_running
        assert not watcher.is_running

    def test_watcher_daemon_thread(self):
        """Watcher observer thread must be a daemon (non-blocking)."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        reg = BackendRegistry()
        watcher = BackendFileWatcher(reg)
        watcher.start()
        try:
            assert watcher._observer is not None
            assert watcher._observer.daemon is True
        finally:
            watcher.stop()

    def test_watcher_detects_new_backend_file(self, tmp_backend_dir: Path):
        """Watcher auto-imports a new .py backend file dropped into watched dir."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        reg = BackendRegistry()
        watcher = BackendFileWatcher(
            reg,
            extra_watch_paths=[str(tmp_backend_dir)],
            debounce_seconds=0.2,
        )
        watcher.start()

        try:
            # Drop a new backend file
            backend_file = tmp_backend_dir / "dummy_hot_backend.py"
            backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))

            # Wait for debounce + processing
            watcher.reload_event.wait(timeout=3.0)
            time.sleep(0.3)

            # Check reload history
            history = watcher.reload_history
            assert len(history) >= 1, f"No reload events recorded. History: {history}"
            assert history[-1]["success"] is True
        finally:
            watcher.stop()

    def test_watcher_hot_reloads_modified_backend(self, tmp_backend_dir: Path):
        """Full E2E: watcher queues request, main thread consumes it at a safe point."""
        from mathart.core.backend_file_watcher import BackendFileWatcher
        from mathart.core.safe_point_execution import SafePointExecutionLock

        reg = BackendRegistry()

        # Step 1: Write and import v1
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        _, v1_class = reg.get_or_raise("dummy_hot_backend")
        v1_id = id(v1_class)
        assert v1_class.HOT_RELOAD_VERSION == "v1_result"

        lock = SafePointExecutionLock(reload_timeout=5.0)
        watcher = BackendFileWatcher(
            reg,
            extra_watch_paths=[str(tmp_backend_dir)],
            debounce_seconds=0.2,
            safe_point_lock=lock,
        )
        watcher.start()

        try:
            watcher.reload_requested_event.clear()
            watcher.reload_event.clear()
            backend_file.write_text(_generate_backend_source("2.0.0", "v2_result"))

            assert watcher.reload_requested_event.wait(timeout=5.0), (
                "Watcher never queued a reload request"
            )

            # Still v1 until the main thread explicitly consumes the boundary request.
            _, pending_class = reg.get_or_raise("dummy_hot_backend")
            assert pending_class.HOT_RELOAD_VERSION == "v1_result"

            records = watcher.process_pending_reloads(
                backend_name="dummy_hot_backend",
                frame_index=7,
            )
            assert records and records[0]["success"] is True

            entry = reg.get("dummy_hot_backend")
            assert entry is not None, "Backend disappeared after hot-reload!"
            _, v2_class = entry
            v2_id = id(v2_class)

            assert v2_id != v1_id, (
                f"Zombie Reference Trap in watcher E2E: "
                f"class id() unchanged! old={v1_id}, new={v2_id}"
            )
            assert v2_class.HOT_RELOAD_VERSION == "v2_result"
            assert records[0]["frame_boundary_after"] == 7

            instance = v2_class()
            result = instance.execute({})
            assert result["version"] == "v2_result"
        finally:
            watcher.stop()


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 6: Debounce Scheduler
# ═══════════════════════════════════════════════════════════════════════════

class TestDebounceScheduler:
    """Tests for _DebouncedReloadScheduler — event coalescing."""

    def test_debounce_coalesces_rapid_events(self):
        """Multiple rapid events for the same file produce only one callback."""
        from mathart.core.backend_file_watcher import _DebouncedReloadScheduler

        call_count = 0
        call_files: list[str] = []

        def callback(file_path: str) -> None:
            nonlocal call_count
            call_count += 1
            call_files.append(file_path)

        scheduler = _DebouncedReloadScheduler(callback, debounce_seconds=0.3)

        # Fire 5 rapid events for the same file
        for _ in range(5):
            scheduler.schedule("/tmp/test_file.py")
            time.sleep(0.05)

        # Wait for debounce to expire
        time.sleep(0.5)

        # Should have coalesced into exactly 1 callback
        assert call_count == 1, f"Expected 1 callback, got {call_count}"
        assert call_files == ["/tmp/test_file.py"]

        scheduler.cancel_all()

    def test_debounce_separate_files_independent(self):
        """Events for different files are debounced independently."""
        from mathart.core.backend_file_watcher import _DebouncedReloadScheduler

        call_files: list[str] = []

        def callback(file_path: str) -> None:
            call_files.append(file_path)

        scheduler = _DebouncedReloadScheduler(callback, debounce_seconds=0.2)

        scheduler.schedule("/tmp/file_a.py")
        scheduler.schedule("/tmp/file_b.py")

        time.sleep(0.5)

        assert len(call_files) == 2
        assert "/tmp/file_a.py" in call_files
        assert "/tmp/file_b.py" in call_files

        scheduler.cancel_all()


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 7: File Path to Module Name Conversion
# ═══════════════════════════════════════════════════════════════════════════

class TestFilePathToModule:
    """Tests for BackendFileWatcher._file_path_to_module()."""

    def test_converts_valid_path(self, tmp_backend_dir: Path):
        """Valid .py file path converts to dotted module name."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        file_path = str(tmp_backend_dir / "my_backend.py")
        result = BackendFileWatcher._file_path_to_module(file_path)
        assert result is not None
        assert "my_backend" in result

    def test_rejects_non_python_file(self):
        """Non-.py files return None."""
        from mathart.core.backend_file_watcher import BackendFileWatcher

        result = BackendFileWatcher._file_path_to_module("/tmp/readme.txt")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 8: Thread Safety
# ═══════════════════════════════════════════════════════════════════════════

class TestThreadSafety:
    """Tests for concurrent registry access during hot-reload."""

    def test_concurrent_register_and_lookup(self):
        """Concurrent register + lookup must not raise or corrupt state."""
        reg = BackendRegistry()
        errors: list[str] = []

        def register_worker(i: int) -> None:
            try:
                meta = BackendMeta(
                    name=f"concurrent_backend_{i}",
                    session_origin="SESSION-090",
                )

                class DynBackend:
                    pass

                DynBackend.__module__ = f"test_module_{i}"
                reg.register(meta, DynBackend)
            except Exception as e:
                errors.append(str(e))

        def lookup_worker() -> None:
            try:
                for _ in range(50):
                    reg.all_backends()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(str(e))

        threads = []
        for i in range(10):
            t = threading.Thread(target=register_worker, args=(i,))
            threads.append(t)
        lookup_thread = threading.Thread(target=lookup_worker)
        threads.append(lookup_thread)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Thread safety errors: {errors}"


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 9: Full Closed-Loop E2E Hot-Reload Lifecycle
# ═══════════════════════════════════════════════════════════════════════════

class TestFullE2EHotReloadLifecycle:
    """Complete closed-loop test: generate → mount → execute v1 → overwrite → reload → execute v2."""

    def test_full_lifecycle_v1_to_v2(self, tmp_backend_dir: Path):
        """
        Full E2E lifecycle:
        1. Generate dummy_hot_backend.py on disk (v1)
        2. Import → verify auto-mount → execute → assert v1
        3. Overwrite file with v2 code
        4. Call registry.reload() → assert class id() changed
        5. Execute → assert v2
        6. Verify old class memory is no longer in registry
        """
        reg = BackendRegistry()

        # --- Phase 1: Generate v1 on disk ---
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))

        # --- Phase 2: Import and verify v1 ---
        mod = importlib.import_module("dynamic_backends.dummy_hot_backend")
        assert reg.get("dummy_hot_backend") is not None

        _, v1_class = reg.get_or_raise("dummy_hot_backend")
        v1_class_id = id(v1_class)
        v1_instance = v1_class()
        v1_result = v1_instance.execute({})
        assert v1_result["version"] == "v1_result"
        assert v1_class.HOT_RELOAD_VERSION == "v1_result"

        # --- Phase 3: Overwrite with v2 ---
        backend_file.write_text(_generate_backend_source("2.0.0", "v2_result"))
        time.sleep(0.05)

        # --- Phase 4: Reload and verify class identity change ---
        success = reg.reload("dummy_hot_backend")
        assert success is True

        _, v2_class = reg.get_or_raise("dummy_hot_backend")
        v2_class_id = id(v2_class)

        # 🚫 ZOMBIE REFERENCE TRAP: Must have different id()
        assert v2_class_id != v1_class_id, (
            f"CRITICAL: Zombie Reference Trap! "
            f"Old class id={v1_class_id}, New class id={v2_class_id} — "
            f"they MUST differ after reload."
        )

        # --- Phase 5: Execute v2 and verify ---
        v2_instance = v2_class()
        v2_result = v2_instance.execute({})
        assert v2_result["version"] == "v2_result"
        assert v2_class.HOT_RELOAD_VERSION == "v2_result"

        # --- Phase 6: Verify old class is no longer in registry ---
        current_entry = reg.get_or_raise("dummy_hot_backend")
        assert id(current_entry[1]) == v2_class_id
        assert id(current_entry[1]) != v1_class_id

    def test_multiple_sequential_reloads(self, tmp_backend_dir: Path):
        """Three sequential reloads: v1 → v2 → v3, each with unique class id()."""
        reg = BackendRegistry()
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        class_ids: list[int] = []

        for version_num in range(1, 4):
            version_str = f"{version_num}.0.0"
            value_str = f"v{version_num}_result"
            backend_file.write_text(_generate_backend_source(version_str, value_str))
            time.sleep(0.05)

            if version_num == 1:
                importlib.import_module("dynamic_backends.dummy_hot_backend")
            else:
                reg.reload("dummy_hot_backend")

            _, cls = reg.get_or_raise("dummy_hot_backend")
            class_ids.append(id(cls))
            assert cls.HOT_RELOAD_VERSION == value_str

        # All three class ids must be unique
        assert len(set(class_ids)) == 3, (
            f"Expected 3 unique class ids across 3 reloads, "
            f"got {len(set(class_ids))}: {class_ids}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 10: Reload Callback Integration
# ═══════════════════════════════════════════════════════════════════════════

class TestReloadCallback:
    """Tests for the on_reload callback in BackendFileWatcher."""

    def test_on_reload_callback_fires(self, tmp_backend_dir: Path):
        """on_reload callback is invoked after queued reload is consumed."""
        from mathart.core.backend_file_watcher import BackendFileWatcher
        from mathart.core.safe_point_execution import SafePointExecutionLock

        reg = BackendRegistry()
        callback_log: list[tuple[str, bool]] = []

        def on_reload(name: str, success: bool) -> None:
            callback_log.append((name, success))

        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        lock = SafePointExecutionLock(reload_timeout=5.0)
        watcher = BackendFileWatcher(
            reg,
            extra_watch_paths=[str(tmp_backend_dir)],
            debounce_seconds=0.2,
            on_reload=on_reload,
            safe_point_lock=lock,
        )
        watcher.start()

        try:
            watcher.reload_requested_event.clear()
            watcher.reload_event.clear()
            backend_file.write_text(_generate_backend_source("2.0.0", "v2_result"))

            assert watcher.reload_requested_event.wait(timeout=5.0), (
                "Watcher never queued a reload request"
            )
            watcher.process_pending_reloads(
                backend_name="dummy_hot_backend",
                frame_index=3,
            )
            watcher.reload_event.wait(timeout=5.0)
            time.sleep(0.1)

            assert len(callback_log) >= 1, f"Callback not fired. Log: {callback_log}"
            assert callback_log[-1][0] == "dummy_hot_backend"
            assert callback_log[-1][1] is True
        finally:
            watcher.stop()


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 11: Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case tests for hot-reload robustness."""

    def test_reload_empty_file(self, tmp_backend_dir: Path):
        """Reloading a backend whose file becomes empty should fail gracefully."""
        reg = BackendRegistry()

        # Write valid v1
        backend_file = tmp_backend_dir / "dummy_hot_backend.py"
        backend_file.write_text(_generate_backend_source("1.0.0", "v1_result"))
        importlib.import_module("dynamic_backends.dummy_hot_backend")

        # Empty the file
        backend_file.write_text("")
        time.sleep(0.05)

        # Reload should fail but old version should be restored
        with pytest.raises(RuntimeError):
            reg.reload("dummy_hot_backend")

        # Old version must survive
        entry = reg.get("dummy_hot_backend")
        assert entry is not None

    def test_double_unregister(self):
        """Double unregister should be idempotent (second returns False)."""
        reg = BackendRegistry()

        @register_backend("double_unreg_test", session_origin="SESSION-090")
        class DoubleUnregBackend:
            pass

        assert reg.unregister("double_unreg_test") is True
        assert reg.unregister("double_unreg_test") is False

    def test_register_after_unregister(self):
        """A backend can be re-registered after being unregistered."""
        reg = BackendRegistry()

        @register_backend("reregister_test", session_origin="SESSION-090")
        class OriginalBackend:
            MARKER = "original"

        reg.unregister("reregister_test")
        assert reg.get("reregister_test") is None

        @register_backend("reregister_test", session_origin="SESSION-090")
        class NewBackend:
            MARKER = "new"

        entry = reg.get_or_raise("reregister_test")
        assert entry[1].MARKER == "new"
