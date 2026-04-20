"""Safe-Point Execution Coordination — frame-boundary hot-reload polling.

The safe-point protocol intentionally separates **requesting** a reload from
**executing** a reload.  A file-watcher thread may observe backend source-file
changes at any time, but it must never perform ``registry.reload()`` directly.
Instead it raises a thread-safe request signal, and the main render loop polls
that signal only at deterministic frame boundaries.

Protocol
--------
1. ``request_reload(backend_name)``
   Called by a watcher/background thread.  This only sets a pending request
   signal; it never reloads code inline.

2. ``frame_execution(backend_name)``
   Wraps exactly one frame-sized unit of work.  This is intentionally tiny and
   must not surround a whole multi-frame network/render batch.

3. ``poll_safe_point(backend_name, reload_callback, frame_index=...)``
   Called by the main loop after frame *N* and before frame *N+1*.  If a reload
   request is pending, the current thread temporarily takes ownership, performs
   the reload at the boundary, clears the request signal, and resumes the batch.

Anti-Pattern Guards
-------------------
- No coarse lock around a whole multi-frame render batch.
- The watcher thread must never call ``registry.reload()`` directly.
- Reload must never start while a frame-sized compute section is active.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional

logger = logging.getLogger(__name__)


class SafePointExecutionLock:
    """Per-backend frame-boundary reload coordinator."""

    def __init__(self, *, reload_timeout: float = 10.0) -> None:
        self._reload_timeout = float(reload_timeout)
        self._lock = threading.Lock()
        self._backend_states: dict[str, dict[str, Any]] = {}

    def _get_state(self, backend_name: str) -> dict[str, Any]:
        """Get or create per-backend synchronization state."""
        with self._lock:
            if backend_name not in self._backend_states:
                self._backend_states[backend_name] = {
                    "active_frames": 0,
                    "reloading": False,
                    "reload_request_count": 0,
                    "reload_completed_count": 0,
                    "last_reload_boundary_after_frame": None,
                    "last_reload_error": None,
                    "reload_requested_event": threading.Event(),
                    "condition": threading.Condition(threading.Lock()),
                }
            return self._backend_states[backend_name]

    @contextmanager
    def frame_execution(self, backend_name: str) -> Generator[None, None, None]:
        """Mark execution of one frame-sized atomic work unit."""
        state = self._get_state(backend_name)
        cond = state["condition"]

        with cond:
            deadline = time.monotonic() + self._reload_timeout
            while state["reloading"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"SafePointExecutionLock: timed out waiting for reload "
                        f"to finish before starting next frame of {backend_name!r}."
                    )
                cond.wait(timeout=remaining)
            state["active_frames"] += 1

        try:
            yield
        finally:
            with cond:
                state["active_frames"] = max(0, int(state["active_frames"]) - 1)
                if state["active_frames"] == 0:
                    cond.notify_all()

    @contextmanager
    def execution_fence(self, backend_name: str) -> Generator[None, None, None]:
        """Backward-compatible alias kept for legacy single-frame tests only.

        This name used to encourage coarse batch-wide locking.  It now maps to
        ``frame_execution()`` so legacy callers do not regress, while the actual
        coordination protocol is driven by explicit frame-boundary polling.
        """
        with self.frame_execution(backend_name):
            yield

    def request_reload(self, backend_name: str) -> int:
        """Register a pending reload request from a watcher thread."""
        state = self._get_state(backend_name)
        cond = state["condition"]
        reload_event = state["reload_requested_event"]
        with cond:
            reload_event.set()
            state["reload_request_count"] += 1
            cond.notify_all()
            return int(state["reload_request_count"])

    def has_pending_reload(self, backend_name: str) -> bool:
        """Whether a backend currently has a queued reload request."""
        state = self._get_state(backend_name)
        return bool(state["reload_requested_event"].is_set())

    def poll_safe_point(
        self,
        backend_name: str,
        reload_callback: Optional[Callable[[], Any]] = None,
        *,
        frame_index: int | None = None,
    ) -> bool:
        """Poll the frame boundary and consume a pending reload request.

        Returns ``True`` only when a queued reload request is actually consumed
        and processed at this safe point.
        """
        state = self._get_state(backend_name)
        cond = state["condition"]
        reload_event = state["reload_requested_event"]

        with cond:
            if not reload_event.is_set():
                return False

            deadline = time.monotonic() + self._reload_timeout
            while state["reloading"] or state["active_frames"] > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"SafePointExecutionLock: timed out waiting for frame boundary "
                        f"of {backend_name!r} before reload. active_frames="
                        f"{state['active_frames']}"
                    )
                cond.wait(timeout=remaining)

            state["reloading"] = True
            reload_event.clear()
            state["last_reload_boundary_after_frame"] = frame_index
            state["last_reload_error"] = None

        try:
            if reload_callback is not None:
                reload_callback()
            return True
        except Exception as exc:
            with cond:
                state["last_reload_error"] = str(exc)
            raise
        finally:
            with cond:
                state["reloading"] = False
                state["reload_completed_count"] += 1
                cond.notify_all()

    def process_reload_if_requested(
        self,
        backend_name: str,
        reload_callback: Optional[Callable[[], Any]] = None,
        *,
        frame_index: int | None = None,
    ) -> bool:
        """Backward-compatible alias for ``poll_safe_point()``."""
        return self.poll_safe_point(
            backend_name,
            reload_callback,
            frame_index=frame_index,
        )

    @contextmanager
    def reload_gate(self, backend_name: str) -> Generator[None, None, None]:
        """Manual exclusive reload context for explicit maintenance flows."""
        state = self._get_state(backend_name)
        cond = state["condition"]

        with cond:
            deadline = time.monotonic() + self._reload_timeout
            while state["reloading"] or state["active_frames"] > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"SafePointExecutionLock: reload_gate timeout for {backend_name!r}."
                    )
                cond.wait(timeout=remaining)
            state["reloading"] = True
            state["last_reload_error"] = None

        try:
            yield
        except Exception as exc:
            with cond:
                state["last_reload_error"] = str(exc)
            raise
        finally:
            with cond:
                state["reloading"] = False
                state["reload_completed_count"] += 1
                cond.notify_all()

    def is_executing(self, backend_name: str) -> bool:
        """Check whether any frame-sized work unit is currently executing."""
        state = self._get_state(backend_name)
        return int(state["active_frames"]) > 0

    def is_reloading(self, backend_name: str) -> bool:
        """Check whether the backend is currently reloading."""
        state = self._get_state(backend_name)
        return bool(state["reloading"])

    def status(self) -> dict[str, dict[str, Any]]:
        """Return a diagnostic snapshot of all tracked backends."""
        with self._lock:
            return {
                name: {
                    "active_frames": int(s["active_frames"]),
                    "reloading": bool(s["reloading"]),
                    "reload_requested": bool(s["reload_requested_event"].is_set()),
                    "reload_request_count": int(s["reload_request_count"]),
                    "reload_completed_count": int(s["reload_completed_count"]),
                    "last_reload_boundary_after_frame": s["last_reload_boundary_after_frame"],
                    "last_reload_error": s["last_reload_error"],
                }
                for name, s in self._backend_states.items()
            }


_global_lock: SafePointExecutionLock | None = None
_singleton_mutex = threading.Lock()


def get_safe_point_lock() -> SafePointExecutionLock:
    """Get or create the global SafePointExecutionLock singleton."""
    global _global_lock
    if _global_lock is None:
        with _singleton_mutex:
            if _global_lock is None:
                _global_lock = SafePointExecutionLock()
    return _global_lock


__all__ = [
    "SafePointExecutionLock",
    "get_safe_point_lock",
]
