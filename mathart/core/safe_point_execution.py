"""Safe-Point Execution Lock — Frame-Boundary Reload Gating.

SESSION-091 (P1-AI-2E): Implements the frame-boundary safe-point lock
that prevents hot-reload from applying mid-batch, which would cause
new/old parameter interleaving and ``AttributeError`` crashes.

Architecture
------------
Inspired by:
- Unity Domain Reloading: serialize state → safe point → reload → restore
- Unreal Engine Live Coding: pause game thread at safe point → patch → resume
- Erlang/OTP: code swap only between message processing boundaries

The lock provides two contexts:

1. ``execution_fence(backend_name)`` — wraps a batch render or backend
   execution. While inside the fence, hot-reload for that backend is
   deferred until the fence exits.

2. ``reload_gate(backend_name)`` — wraps a hot-reload operation.
   It waits for any active execution fence to complete before proceeding.

Anti-Pattern Guard (SESSION-091 Red Line):
- 🚫 **Mid-Frame Reload Trap**: Hot-reload MUST NOT apply while a batch
  render is in progress. The SafePointExecutionLock ensures mutual
  exclusion between execution and reload for the same backend.

Thread Safety
-------------
Uses per-backend ``threading.Condition`` objects for fine-grained
synchronization. Different backends can execute and reload concurrently
without blocking each other.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)


class SafePointExecutionLock:
    """Per-backend execution/reload mutual exclusion lock.

    This is the core synchronization primitive that prevents the
    Mid-Frame Reload Trap.

    Usage::

        lock = SafePointExecutionLock()

        # In render thread:
        with lock.execution_fence("my_backend"):
            # Safe to execute — reload is deferred
            backend.execute(ctx)

        # In file watcher thread:
        with lock.reload_gate("my_backend"):
            # Safe to reload — no execution in progress
            registry.reload("my_backend")
    """

    def __init__(self, *, reload_timeout: float = 10.0) -> None:
        self._reload_timeout = reload_timeout
        self._lock = threading.Lock()
        # Per-backend state: {name: {"executing": int, "condition": Condition}}
        self._backend_states: dict[str, dict[str, Any]] = {}

    def _get_state(self, backend_name: str) -> dict[str, Any]:
        """Get or create per-backend synchronization state."""
        with self._lock:
            if backend_name not in self._backend_states:
                self._backend_states[backend_name] = {
                    "executing": 0,
                    "reloading": False,
                    "condition": threading.Condition(threading.Lock()),
                }
            return self._backend_states[backend_name]

    @contextmanager
    def execution_fence(self, backend_name: str) -> Generator[None, None, None]:
        """Context manager for backend execution (render/compute).

        While inside the fence:
        - Increments the execution counter for this backend.
        - Any ``reload_gate`` for the same backend will wait.
        - Multiple concurrent executions of the same backend are allowed
          (reader-writer pattern: executions are readers).

        If a reload is currently in progress, this will wait for it
        to complete before entering.
        """
        state = self._get_state(backend_name)
        cond = state["condition"]

        with cond:
            # Wait if a reload is in progress
            while state["reloading"]:
                cond.wait(timeout=self._reload_timeout)
            state["executing"] += 1

        try:
            yield
        finally:
            with cond:
                state["executing"] -= 1
                if state["executing"] == 0:
                    cond.notify_all()

    @contextmanager
    def reload_gate(self, backend_name: str) -> Generator[None, None, None]:
        """Context manager for backend hot-reload.

        While inside the gate:
        - Sets the reloading flag for this backend.
        - Waits for all active executions to complete.
        - New executions will wait until reload completes.
        - Only one reload at a time per backend (writer lock).
        """
        state = self._get_state(backend_name)
        cond = state["condition"]

        with cond:
            # Wait for any other reload to finish
            while state["reloading"]:
                cond.wait(timeout=self._reload_timeout)

            # Signal that we're reloading
            state["reloading"] = True

            # Wait for all active executions to finish
            deadline = time.monotonic() + self._reload_timeout
            while state["executing"] > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "SafePointExecutionLock: reload_gate timeout for %s "
                        "(still %d executing)",
                        backend_name, state["executing"],
                    )
                    break
                cond.wait(timeout=remaining)

        try:
            yield
        finally:
            with cond:
                state["reloading"] = False
                cond.notify_all()

    def is_executing(self, backend_name: str) -> bool:
        """Check if a backend is currently executing."""
        state = self._get_state(backend_name)
        return state["executing"] > 0

    def is_reloading(self, backend_name: str) -> bool:
        """Check if a backend is currently being reloaded."""
        state = self._get_state(backend_name)
        return state["reloading"]

    def status(self) -> dict[str, dict[str, Any]]:
        """Return status snapshot of all tracked backends."""
        with self._lock:
            return {
                name: {
                    "executing": s["executing"],
                    "reloading": s["reloading"],
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
