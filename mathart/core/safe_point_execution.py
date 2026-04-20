"""Safe-Point Execution Coordination — SESSION-092 frame-boundary hotfix.

SESSION-092 replaces the coarse batch-wide execution fence from SESSION-091
with a **frame-boundary safe-point** protocol.

Architecture
------------
Inspired by:
- Unity Domain Reloading: patch code only at deterministic safe points.
- Unreal Live Coding: let the main loop yield between frames, not mid-frame.
- Erlang/OTP: code swap between work units, never during one work unit.

The corrected protocol has three moving parts:

1. ``request_reload(backend_name)``
   Called by a file-watcher thread when source changes are detected.  This does
   **not** reload code immediately.  It only marks a pending reload request.

2. ``frame_execution(backend_name)``
   Marks the execution of **one frame-sized work unit**.  This context must wrap
   the computation of an individual frame (or equivalent atomic slice), never an
   entire multi-frame batch.

3. ``process_reload_if_requested(backend_name, reload_callback, frame_index=...)``
   Called by the main render thread **after frame N completes and before frame
   N+1 begins**.  If a pending reload exists, the current thread temporarily
   yields the render loop, executes ``reload_callback`` at the boundary, and
   then resumes batch processing.

Anti-Pattern Guards (SESSION-092 Red Lines)
-------------------------------------------
- 🚫 No coarse lock around a whole multi-frame render batch.
- 🚫 The watcher thread must not call ``registry.reload()`` directly.
- 🚫 Reload must never start while any frame-sized compute section is active.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional

logger = logging.getLogger(__name__)


class SafePointExecutionLock:
    """Per-backend frame-boundary reload coordinator.

    The watcher thread only raises a pending-reload signal.  The main render
    thread performs the actual reload during a deterministic frame boundary.
    """

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
                    "reload_requested": False,
                    "reload_request_count": 0,
                    "reload_completed_count": 0,
                    "last_reload_boundary_after_frame": None,
                    "last_reload_error": None,
                    "condition": threading.Condition(threading.Lock()),
                }
            return self._backend_states[backend_name]

    @contextmanager
    def frame_execution(self, backend_name: str) -> Generator[None, None, None]:
        """Mark execution of one frame-sized atomic work unit.

        This context is intentionally **small**.  It should wrap one frame's
        computation only.  If a reload is currently executing, the next frame
        waits at the boundary until reload completes.
        """
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
        """Backward-compatible alias for ``frame_execution``.

        SESSION-092 repurposes this API for **single-frame** execution only.
        It must not be used as a batch-wide outer lock.
        """
        with self.frame_execution(backend_name):
            yield

    def request_reload(self, backend_name: str) -> int:
        """Register a pending reload request from a watcher thread.

        Returns the cumulative reload request count for observability.
        Multiple rapid requests are coalesced into a single pending flag.
        """
        state = self._get_state(backend_name)
        cond = state["condition"]
        with cond:
            state["reload_requested"] = True
            state["reload_request_count"] += 1
            cond.notify_all()
            return int(state["reload_request_count"])

    def has_pending_reload(self, backend_name: str) -> bool:
        """Whether a backend currently has a queued reload request."""
        state = self._get_state(backend_name)
        return bool(state["reload_requested"])

    def process_reload_if_requested(
        self,
        backend_name: str,
        reload_callback: Optional[Callable[[], Any]] = None,
        *,
        frame_index: int | None = None,
    ) -> bool:
        """Process a pending reload exactly at a frame boundary.

        Parameters
        ----------
        backend_name : str
            Backend whose boundary is being checked.
        reload_callback : callable, optional
            Actual reload operation to run if a pending request exists.  This is
            typically ``lambda: registry.reload(backend_name)``.
        frame_index : int | None
            The frame that just completed.  Stored for diagnostics.

        Returns
        -------
        bool
            ``True`` if a pending reload was consumed and processed, else
            ``False``.
        """
        state = self._get_state(backend_name)
        cond = state["condition"]

        with cond:
            if not state["reload_requested"]:
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
            state["reload_requested"] = False
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

    @contextmanager
    def reload_gate(self, backend_name: str) -> Generator[None, None, None]:
        """Manual exclusive reload context for boundary-safe direct reloads.

        This remains available for explicit maintenance flows, but the watcher
        thread must prefer ``request_reload()`` and let the main loop consume the
        request at a frame boundary.
        """
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
                    "reload_requested": bool(s["reload_requested"]),
                    "reload_request_count": int(s["reload_request_count"]),
                    "reload_completed_count": int(s["reload_completed_count"]),
                    "last_reload_boundary_after_frame": s["last_reload_boundary_after_frame"],
                    "last_reload_error": s["last_reload_error"],
                }
                for name, s in self._backend_states.items()
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

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
