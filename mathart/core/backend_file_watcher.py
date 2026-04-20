"""Backend File Watcher — Non-Blocking Daemon Thread for Hot-Reload.

SESSION-090 (P1-MIGRATE-4): Implements a decoupled filesystem monitoring bus
that watches backend source files for changes and triggers targeted hot-reload
through the ``BackendRegistry.reload()`` primitive.

Architecture References
-----------------------
1. **Erlang/OTP Hot Code Swapping**: Zero-downtime code replacement with strict
   state/code isolation.  The watcher never clears the global registry — it
   performs surgical per-backend reload only.

2. **Eclipse OSGi Dynamic Module System**: Strict lifecycle management with
   atomic unregister → reload → re-register sequences.  The watcher delegates
   the full lifecycle to ``BackendRegistry.reload()``.

3. **Unity Domain Reloading / Unreal Live Coding**: Compilation and patching
   happen on a background thread at a "safe point".  The watcher runs on a
   daemon thread with debounce to ensure files are fully written before
   triggering reload.

4. **Python ``watchdog`` + ``importlib.reload`` Best Practices**: Event
   coalescing via debounce timer (400ms default) to handle IDE save bursts.
   Deep ``sys.modules`` cleanup is delegated to ``BackendRegistry.reload()``.

Design Principles
-----------------
- **Zero hardcoded paths**: Watch directories are derived dynamically from
  ``BackendRegistry.get_watched_package_paths()``, which resolves the same
  package roots used by ``discover()``.

- **Daemon thread**: The ``watchdog.Observer`` runs as a daemon thread that
  automatically terminates when the main process exits.  No ``while True``
  blocking of the main thread.

- **Debounce / Throttle**: IDE saves often trigger multiple rapid filesystem
  events (CREATE → MODIFY → MODIFY).  A per-file debounce timer (default
  400ms) coalesces these into a single reload trigger, preventing
  ``SyntaxError`` from partial file writes.

- **Targeted reload**: Only the backend whose source file changed is reloaded.
  All other backends remain untouched (anti-State-Wipeout-Trap).

- **Fail-safe**: If ``reload()`` raises (e.g., ``SyntaxError``), the old
  backend version is atomically restored by the registry.  The watcher logs
  the error and continues monitoring.

Usage::

    from mathart.core.backend_file_watcher import BackendFileWatcher
    from mathart.core.backend_registry import get_registry

    registry = get_registry()
    watcher = BackendFileWatcher(registry)
    watcher.start()
    # ... application runs ...
    watcher.stop()

Or as a context manager::

    with BackendFileWatcher(registry) as watcher:
        # ... application runs ...
        pass
"""
from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from mathart.core.safe_point_execution import get_safe_point_lock

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

if TYPE_CHECKING:
    from mathart.core.backend_registry import BackendRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Debounce Timer — Coalesces rapid filesystem events
# ---------------------------------------------------------------------------

class _DebouncedReloadScheduler:
    """Per-file debounce timer that coalesces rapid FS events.

    When a file is modified, the scheduler starts (or resets) a timer.
    Only when the timer expires without further events does the actual
    reload callback fire.  This prevents reloading a half-written file
    (IDE save bursts, rsync partial writes, etc.).

    The debounce window defaults to 400ms — within the 300-500ms range
    mandated by the project's anti-pattern guard (Blocking & Debounce Trap).
    """

    def __init__(
        self,
        callback: Callable[[str], None],
        debounce_seconds: float = 0.4,
    ) -> None:
        self._callback = callback
        self._debounce_seconds = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def schedule(self, file_path: str) -> None:
        """Schedule a debounced reload for the given file path."""
        with self._lock:
            # Cancel any pending timer for this file
            existing = self._timers.pop(file_path, None)
            if existing is not None:
                existing.cancel()

            # Start a new timer
            timer = threading.Timer(
                self._debounce_seconds,
                self._fire,
                args=(file_path,),
            )
            timer.daemon = True
            timer.name = f"debounce-{Path(file_path).stem}"
            self._timers[file_path] = timer
            timer.start()

    def _fire(self, file_path: str) -> None:
        """Timer expired — execute the reload callback."""
        with self._lock:
            self._timers.pop(file_path, None)
        try:
            self._callback(file_path)
        except Exception as exc:
            logger.error(
                "Debounced reload callback failed for %s: %s",
                file_path, exc,
            )

    def cancel_all(self) -> None:
        """Cancel all pending timers (used during shutdown)."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()


# ---------------------------------------------------------------------------
# File System Event Handler
# ---------------------------------------------------------------------------

class _BackendFileEventHandler(FileSystemEventHandler if _WATCHDOG_AVAILABLE else object):
    """Handles filesystem events and routes them to the debounce scheduler.

    Only ``.py`` file modifications and creations are processed.
    Directories, ``__pycache__``, and non-Python files are ignored.
    """

    def __init__(self, scheduler: _DebouncedReloadScheduler) -> None:
        super().__init__()
        self._scheduler = scheduler

    def on_modified(self, event: Any) -> None:
        self._handle(event)

    def on_created(self, event: Any) -> None:
        self._handle(event)

    def _handle(self, event: Any) -> None:
        if getattr(event, "is_directory", False):
            return
        src_path = str(getattr(event, "src_path", ""))
        if not src_path.endswith(".py"):
            return
        if "__pycache__" in src_path:
            return
        # Ignore test files — they are not backend modules
        basename = os.path.basename(src_path)
        if basename.startswith("test_"):
            return
        logger.debug("FS event detected: %s", src_path)
        self._scheduler.schedule(src_path)


# ---------------------------------------------------------------------------
# BackendFileWatcher — Public API
# ---------------------------------------------------------------------------

class BackendFileWatcher:
    """Non-blocking daemon file watcher for backend hot-reload.

    SESSION-092 corrects the hot-reload chain so the watcher **never** calls
    ``BackendRegistry.reload()`` directly for existing backends.  Instead it
    queues a ``reload_requested`` signal, and the main render loop consumes that
    request at a frame-boundary safe point.

    Parameters
    ----------
    registry : BackendRegistry
        The singleton registry instance.
    debounce_seconds : float
        Debounce window in seconds (default 0.4, range 0.3-0.5).
    extra_watch_paths : list[str] | None
        Additional filesystem paths to monitor beyond the auto-discovered
        package paths.
    on_reload : Callable[[str, bool], None] | None
        Optional callback invoked after each reload attempt.
        Signature: ``on_reload(backend_name, success)``.

    Raises
    ------
    ImportError
        If ``watchdog`` is not installed.
    """

    def __init__(
        self,
        registry: "BackendRegistry",
        *,
        debounce_seconds: float = 0.4,
        extra_watch_paths: Optional[list[str]] = None,
        on_reload: Optional[Callable[[str, bool], None]] = None,
        safe_point_lock: Any | None = None,
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is required for BackendFileWatcher. "
                "Install with: pip install watchdog"
            )
        self._registry = registry
        self._debounce_seconds = debounce_seconds
        self._extra_watch_paths = extra_watch_paths or []
        self._on_reload = on_reload
        self._safe_point_lock = safe_point_lock or get_safe_point_lock()
        self._observer: Optional[Observer] = None
        self._scheduler = _DebouncedReloadScheduler(
            callback=self._on_file_changed,
            debounce_seconds=debounce_seconds,
        )
        self._handler = _BackendFileEventHandler(self._scheduler)
        # Reload history for introspection / testing
        self._reload_history: list[dict[str, Any]] = []
        self._reload_event = threading.Event()
        self._reload_requested_event = threading.Event()
        self._pending_lock = threading.Lock()
        self._pending_reload_requests: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        """Start the daemon file watcher.

        The ``watchdog.Observer`` thread is set as a daemon so it
        automatically terminates when the main process exits.  This
        method is non-blocking — it returns immediately.
        """
        if self._observer is not None:
            logger.warning("BackendFileWatcher already running.")
            return

        watch_paths = self._registry.get_watched_package_paths()
        watch_paths.extend(self._extra_watch_paths)

        if not watch_paths:
            logger.warning(
                "No watch paths discovered. BackendFileWatcher has nothing "
                "to monitor."
            )
            return

        self._observer = Observer()
        self._observer.daemon = True

        for path in watch_paths:
            if os.path.isdir(path):
                self._observer.schedule(
                    self._handler,
                    path=path,
                    recursive=False,
                )
                logger.info("Watching for backend changes: %s", path)

        self._observer.start()
        logger.info(
            "BackendFileWatcher started (debounce=%.1fs, paths=%d)",
            self._debounce_seconds,
            len(watch_paths),
        )

    def stop(self) -> None:
        """Stop the daemon file watcher and cancel pending debounce timers."""
        self._scheduler.cancel_all()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
            logger.info("BackendFileWatcher stopped.")

    @property
    def is_running(self) -> bool:
        """Whether the observer thread is alive."""
        return self._observer is not None and self._observer.is_alive()

    @property
    def reload_history(self) -> list[dict[str, Any]]:
        """List of reload events for introspection and testing."""
        return list(self._reload_history)

    @property
    def reload_event(self) -> threading.Event:
        """Threading event set after a queued reload is actually processed."""
        return self._reload_event

    @property
    def reload_requested_event(self) -> threading.Event:
        """Threading event set once the watcher has queued a reload request."""
        return self._reload_requested_event

    def _queue_reload_request(
        self,
        *,
        file_path: str,
        module_name: str,
        backend_name: str,
    ) -> None:
        """Queue a backend reload request instead of reloading on watcher thread."""
        record = {
            "file": file_path,
            "module": module_name,
            "backend": backend_name,
            "action": "reload_requested",
            "success": None,
            "timestamp": time.time(),
        }
        with self._pending_lock:
            self._pending_reload_requests[backend_name] = record
        self._reload_history.append(record)
        self._safe_point_lock.request_reload(backend_name)
        self._reload_requested_event.set()
        logger.info(
            "Queued hot-reload request for backend %r (module=%s, file=%s)",
            backend_name, module_name, file_path,
        )

    def poll_safe_point(
        self,
        *,
        backend_name: str | None = None,
        frame_index: int | None = None,
        on_reload_complete: Optional[Callable[[str], None]] = None,
    ) -> list[dict[str, Any]]:
        """Poll for queued reload requests at a frame boundary.

        This is the main-thread handshake point for hot reload.  The watcher
        thread only raises ``reload_requested_event``; the render/orchestration
        loop calls this method between work units so reload occurs exactly in the
        gap after the previous task and before the next task starts.
        """
        if not self._reload_requested_event.is_set():
            return []

        if backend_name is None:
            with self._pending_lock:
                pending_names = sorted(self._pending_reload_requests.keys())
        else:
            pending_names = [backend_name]

        results: list[dict[str, Any]] = []
        for pending_name in pending_names:
            with self._pending_lock:
                request = self._pending_reload_requests.get(pending_name)
            if request is None:
                continue

            success = False
            error_msg = None
            try:
                consumed = self._safe_point_lock.poll_safe_point(
                    pending_name,
                    reload_callback=lambda name=pending_name: self._registry.reload(name),
                    frame_index=frame_index,
                )
                success = bool(consumed)
                if success and on_reload_complete is not None:
                    on_reload_complete(pending_name)
            except Exception as exc:
                error_msg = str(exc)
                logger.error("Hot-reload FAILED for %s: %s", pending_name, exc)

            if success:
                with self._pending_lock:
                    self._pending_reload_requests.pop(pending_name, None)
                    if not self._pending_reload_requests:
                        self._reload_requested_event.clear()

            record = {
                "file": request["file"],
                "module": request["module"],
                "backend": pending_name,
                "action": "reload",
                "success": success,
                "error": error_msg,
                "frame_boundary_after": frame_index,
                "timestamp": time.time(),
            }
            self._reload_history.append(record)
            results.append(record)

            if self._on_reload:
                self._on_reload(pending_name, success)
            self._reload_event.set()

        return results

    def process_pending_reloads(
        self,
        *,
        backend_name: str | None = None,
        frame_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """Backward-compatible alias for ``poll_safe_point()``."""
        return self.poll_safe_point(
            backend_name=backend_name,
            frame_index=frame_index,
        )

    def _on_file_changed(self, file_path: str) -> None:
        """Debounce callback — resolve file to backend and queue reload request."""
        module_name = self._file_path_to_module(file_path)
        if module_name is None:
            logger.debug(
                "Cannot derive module name from %s — skipping reload request.",
                file_path,
            )
            return

        backend_name = self._registry.module_to_backend_name(module_name)

        if backend_name is None:
            logger.info(
                "New backend module detected: %s — attempting import.",
                module_name,
            )
            try:
                importlib.import_module(module_name)
                record = {
                    "file": file_path,
                    "module": module_name,
                    "backend": None,
                    "action": "new_import",
                    "success": True,
                    "timestamp": time.time(),
                }
                self._reload_history.append(record)
                if self._on_reload:
                    self._on_reload(module_name, True)
                self._reload_event.set()
            except Exception as exc:
                logger.error("Failed to import new module %s: %s", module_name, exc)
                record = {
                    "file": file_path,
                    "module": module_name,
                    "backend": None,
                    "action": "new_import",
                    "success": False,
                    "error": str(exc),
                    "timestamp": time.time(),
                }
                self._reload_history.append(record)
                if self._on_reload:
                    self._on_reload(module_name, False)
                self._reload_event.set()
            return

        self._queue_reload_request(
            file_path=file_path,
            module_name=module_name,
            backend_name=backend_name,
        )

    @staticmethod
    def _file_path_to_module(file_path: str) -> Optional[str]:
        """Convert an absolute ``.py`` file path to a dotted module name.

        Scans ``sys.path`` to find the longest matching prefix and converts
        the relative path to a dotted module name.  Returns ``None`` if no
        ``sys.path`` entry matches.
        """
        import sys as _sys

        abs_path = os.path.abspath(file_path)
        if not abs_path.endswith(".py"):
            return None

        # Remove .py extension
        module_path = abs_path[:-3]

        # Try each sys.path entry (longest first for specificity)
        candidates = sorted(_sys.path, key=len, reverse=True)
        for sp in candidates:
            sp_abs = os.path.abspath(sp)
            if not sp_abs:
                continue
            if module_path.startswith(sp_abs + os.sep):
                relative = module_path[len(sp_abs) + 1:]
                module_name = relative.replace(os.sep, ".")
                # Skip __init__ modules
                if module_name.endswith(".__init__"):
                    module_name = module_name[:-9]
                return module_name
        return None

    # --- Context Manager Protocol ---

    def __enter__(self) -> "BackendFileWatcher":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
