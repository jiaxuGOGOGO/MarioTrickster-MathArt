"""Daemon Supervisor — ComfyUI Process Lifecycle Ownership (SESSION-134).

P0-SESSION-131-DAEMON-LAUNCHER — Phase 3
========================================

This module runs ComfyUI as an embedded, supervised service for the
MarioTrickster-MathArt client. It converts the fragile "launch a python
child and hope" pattern into a production-grade supervisor that:

* Models the service lifecycle as a strongly-typed state machine.
* Detects foreign ComfyUI instances already listening on the target port
  and *attaches* to them instead of colliding.
* Runs the child inside its own POSIX session / Windows process group so
  cleanup is atomic across the entire child tree.
* Drains stdout/stderr in daemon threads to guarantee that a chatty
  child never deadlocks the supervisor via pipe saturation.
* Polls a Kubernetes-style readiness probe with exponential backoff
  before declaring the service ``READY``.
* Registers ``atexit`` and ``SIGINT``/``SIGTERM`` hooks to guarantee that
  the ComfyUI child is reclaimed on every exit path.

Architectural anchors (see ``docs/research/SESSION-134-DAEMON-RESEARCH.md``):
- Kubernetes Pod Probes (startup / readiness / liveness).
- PM2 & systemd pipe-drain discipline + streaming log triage.
- POSIX process groups (``start_new_session=True``) and Windows
  ``CREATE_NEW_PROCESS_GROUP``.

Red lines (enforced by ``tests/test_daemon_supervisor.py``):
- R1  (no zombie on exit) — ``stop()`` always leaves the child PID gone.
- R2  (no pipe deadlock)  — log consumers run on daemon threads.
- R3  (attach over launch) — port conflict never aborts the pipeline when
      a healthy ComfyUI is reachable.
- R4  (idempotent Facade)  — repeated ``start()`` never spawns twice.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import platform
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import weakref
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "DaemonState",
    "DaemonLifecycleEvent",
    "DaemonStatus",
    "DaemonCrashedError",
    "ComfyUINotResponsiveError",
    "ReadinessProbe",
    "HttpReadinessProbe",
    "DaemonSupervisor",
]


# ---------------------------------------------------------------------------
# Strongly-typed state contract
# ---------------------------------------------------------------------------

class DaemonState(str, Enum):
    """The lifecycle states a supervised service can occupy.

    The ordering follows Kubernetes pod lifecycle semantics: the service
    is assumed *not ready* until the startup probe explicitly passes.
    """

    IDLE = "idle"                # before any start() call
    STARTING = "starting"        # subprocess spawned; probe not yet green
    READY = "ready"              # startup probe succeeded
    ATTACHED = "attached"        # reused a foreign healthy ComfyUI
    STOPPING = "stopping"        # graceful termination in flight
    STOPPED = "stopped"          # terminated cleanly
    CRASHED = "crashed"          # exited non-zero or failed readiness
    FAILED_TO_START = "failed_to_start"  # startup probe never went green


class DaemonLifecycleEvent(str, Enum):
    """Structured event emitted on every state transition."""

    SPAWN_REQUESTED = "spawn_requested"
    SPAWN_COMPLETED = "spawn_completed"
    PROBE_RETRY = "probe_retry"
    READINESS_OK = "readiness_ok"
    ATTACH_ACCEPTED = "attach_accepted"
    STOP_REQUESTED = "stop_requested"
    STOP_COMPLETED = "stop_completed"
    CRASH_DETECTED = "crash_detected"
    LOG_MATCH = "log_match"


@dataclass(frozen=True)
class DaemonStatus:
    """Point-in-time snapshot of the supervisor."""

    state: DaemonState
    pid: Optional[int]
    host: str
    port: int
    attached: bool
    uptime_seconds: float
    exit_code: Optional[int]
    probe_attempts: int
    last_event: Optional[str]
    tail_log_lines: Tuple[str, ...]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------

class DaemonCrashedError(RuntimeError):
    """Raised when the supervised process terminates unexpectedly."""

    def __init__(self, *, pid: Optional[int], exit_code: Optional[int],
                 tail: Sequence[str] = ()) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.tail = tuple(tail)
        super().__init__(
            f"ComfyUI daemon crashed (pid={pid}, exit={exit_code}); "
            f"log_tail={list(self.tail)[-5:]}"
        )


class ComfyUINotResponsiveError(TimeoutError):
    """Raised when the readiness probe never goes green."""


# ---------------------------------------------------------------------------
# Readiness probe abstraction
# ---------------------------------------------------------------------------

class ReadinessProbe(Protocol):
    """Pluggable readiness probe — real impl hits /system_stats;
    tests inject deterministic fakes."""

    def __call__(self, *, host: str, port: int) -> bool: ...


class HttpReadinessProbe:
    """Default readiness probe: HTTP GET ``/system_stats``.

    Success is defined, Kubernetes-style, as any response with a status
    code in ``[200, 400)``. Timeouts and connection refusals count as
    "not yet ready", not "crashed" (matching the K8s ``Unknown`` state).
    """

    def __init__(self, *, path: str = "/system_stats", timeout: float = 3.0) -> None:
        self._path = path
        self._timeout = timeout

    def __call__(self, *, host: str, port: int) -> bool:
        url = f"http://{host}:{port}{self._path}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                code = getattr(resp, "status", None) or resp.getcode()
                return 200 <= int(code) < 400
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


# ---------------------------------------------------------------------------
# Streaming log pump
# ---------------------------------------------------------------------------

# Common fatal signatures captured PM2-style and elevated to events.
_DEFAULT_LOG_SIGNATURES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("cuda_oom", re.compile(r"CUDA out of memory", re.IGNORECASE)),
    ("port_in_use", re.compile(r"address already in use", re.IGNORECASE)),
    ("torch_missing", re.compile(r"ModuleNotFoundError:.*torch", re.IGNORECASE)),
    ("fatal", re.compile(r"RuntimeError:|FATAL:", re.IGNORECASE)),
)


class _LogPump:
    """Consumes a single child pipe on a daemon thread.

    Rationale: if ComfyUI prints tens of thousands of lines and nobody
    drains the pipe, the OS buffer fills and the child blocks on
    ``write()``. PM2 / systemd both run dedicated readers for exactly
    this reason. See ``docs/research/SESSION-134-DAEMON-RESEARCH.md``.
    """

    def __init__(
        self,
        *,
        stream,
        name: str,
        ring: "_RingBuffer",
        signatures: Sequence[Tuple[str, re.Pattern]] = _DEFAULT_LOG_SIGNATURES,
        on_signature: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._stream = stream
        self._name = name
        self._ring = ring
        self._signatures = tuple(signatures)
        self._on_signature = on_signature
        self._thread = threading.Thread(
            target=self._run, name=f"comfyui-{name}-pump", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            for raw in iter(self._stream.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._ring.append(f"[{self._name}] {line}")
                if self._on_signature is None:
                    continue
                for label, pattern in self._signatures:
                    if pattern.search(line):
                        with contextlib.suppress(Exception):
                            self._on_signature(label, line)
                        break
        except (ValueError, OSError) as exc:
            # ValueError: stream closed; OSError: pipe broken on child exit.
            logger.debug("log pump %s exit: %r", self._name, exc)
        finally:
            with contextlib.suppress(Exception):
                self._stream.close()


class _RingBuffer:
    """Thread-safe bounded log ring used for crash forensics."""

    def __init__(self, capacity: int = 500) -> None:
        self._capacity = int(capacity)
        self._buf: List[str] = []
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._buf.append(line)
            if len(self._buf) > self._capacity:
                # Drop oldest half; O(n) but only when we overflow.
                del self._buf[: len(self._buf) - self._capacity]

    def tail(self, n: int = 50) -> Tuple[str, ...]:
        with self._lock:
            if n <= 0:
                return ()
            return tuple(self._buf[-n:])

    def snapshot(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._buf)


# ---------------------------------------------------------------------------
# Port utilities
# ---------------------------------------------------------------------------

def _port_is_bound(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Returns True iff a TCP socket can connect to ``host:port`` right now."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ---------------------------------------------------------------------------
# Cross-platform process-group helpers
# ---------------------------------------------------------------------------

def _popen_group_kwargs() -> dict:
    """Return Popen kwargs that place the child in its own process group.

    - POSIX: ``start_new_session=True`` → ``setsid`` → new session + pgid.
    - Windows: ``creationflags=CREATE_NEW_PROCESS_GROUP`` (prereq for
      sending CTRL_BREAK_EVENT to the tree).
    """
    if os.name == "nt":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _terminate_process_tree(
    proc: subprocess.Popen, *, graceful_timeout: float
) -> Optional[int]:
    """Try graceful then forceful termination of ``proc`` and its group.

    Returns the final exit code, or ``None`` if the process could not be
    reaped at all (which should only happen on extreme OS failure).
    """
    if proc.poll() is not None:
        return proc.returncode

    # Step 1: graceful signal
    try:
        if os.name == "nt":
            sig_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if sig_break is not None:
                try:
                    proc.send_signal(sig_break)
                except (OSError, ValueError):
                    proc.terminate()
            else:
                proc.terminate()
        else:
            # Prefer killing the whole group so workers die with the parent.
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                with contextlib.suppress(Exception):
                    proc.terminate()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("graceful signal failed: %r", exc)

    # Step 2: wait up to graceful_timeout
    try:
        return proc.wait(timeout=graceful_timeout)
    except subprocess.TimeoutExpired:
        pass

    # Step 3: forceful kill
    with contextlib.suppress(Exception):
        if os.name == "nt":
            proc.kill()
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                with contextlib.suppress(Exception):
                    proc.kill()

    try:
        return proc.wait(timeout=max(graceful_timeout, 5.0))
    except subprocess.TimeoutExpired:
        return None


# ---------------------------------------------------------------------------
# Global supervisor registry for atexit/signal cleanup
# ---------------------------------------------------------------------------

_LIVE_SUPERVISORS: "weakref.WeakSet[DaemonSupervisor]" = weakref.WeakSet()
_ATEXIT_INSTALLED = False
_ATEXIT_LOCK = threading.Lock()


def _shutdown_all_live_supervisors() -> None:
    for sup in list(_LIVE_SUPERVISORS):
        with contextlib.suppress(Exception):
            sup.stop(reason="interpreter_exit")


def _install_process_lifecycle_hooks_once() -> None:
    global _ATEXIT_INSTALLED
    with _ATEXIT_LOCK:
        if _ATEXIT_INSTALLED:
            return
        atexit.register(_shutdown_all_live_supervisors)

        def _signal_cleanup(signum, frame):  # pragma: no cover - signal path
            _shutdown_all_live_supervisors()
            # Re-raise default behaviour so pytest / Ctrl-C still work.
            signal.signal(signum, signal.SIG_DFL)
            with contextlib.suppress(Exception):
                os.kill(os.getpid(), signum)

        # Only main thread can install signals, and only if not in a forked
        # pytest worker. We wrap in try/except to stay CI-safe.
        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _signal_cleanup)
            except (ValueError, OSError):
                # Not on main thread or signal unsupported; cleanup still
                # runs via atexit. Safe to swallow.
                continue
        _ATEXIT_INSTALLED = True


# ---------------------------------------------------------------------------
# The Supervisor
# ---------------------------------------------------------------------------


class DaemonSupervisor:
    """Own the full lifecycle of an embedded ComfyUI subprocess.

    Typical usage::

        sup = DaemonSupervisor(
            command=[sys.executable, "main.py", "--port", "8188"],
            cwd="/opt/ComfyUI",
            host="127.0.0.1",
            port=8188,
        )
        sup.start()            # blocks until READY or raises
        try:
            run_workflows(sup.endpoint_url)
        finally:
            sup.stop()
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        cwd: Optional[str | os.PathLike[str]] = None,
        host: str = "127.0.0.1",
        port: int = 8188,
        env: Optional[dict] = None,
        probe: Optional[ReadinessProbe] = None,
        readiness_timeout: float = 120.0,
        backoff_initial: float = 0.2,
        backoff_max: float = 5.0,
        graceful_timeout: float = 8.0,
        log_capacity: int = 1000,
        allow_attach: bool = True,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not command:
            raise ValueError("command must be a non-empty sequence")
        self._command = tuple(command)
        self._cwd = str(cwd) if cwd is not None else None
        self._host = host
        self._port = int(port)
        self._env = env
        self._probe = probe or HttpReadinessProbe()
        self._readiness_timeout = float(readiness_timeout)
        self._backoff_initial = float(backoff_initial)
        self._backoff_max = float(backoff_max)
        self._graceful_timeout = float(graceful_timeout)
        self._log_capacity = int(log_capacity)
        self._allow_attach = bool(allow_attach)
        self._popen_factory = popen_factory
        self._clock = clock
        self._sleep = sleep

        self._state = DaemonState.IDLE
        self._proc: Optional[subprocess.Popen] = None
        self._stdout_pump: Optional[_LogPump] = None
        self._stderr_pump: Optional[_LogPump] = None
        self._ring = _RingBuffer(capacity=self._log_capacity)
        self._start_time: Optional[float] = None
        self._probe_attempts = 0
        self._last_event: Optional[str] = None
        self._lock = threading.RLock()
        self._events: "queue.Queue[Tuple[DaemonLifecycleEvent, dict]]" = queue.Queue()

        _install_process_lifecycle_hooks_once()
        _LIVE_SUPERVISORS.add(self)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> DaemonState:
        return self._state

    @property
    def endpoint_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def pid(self) -> Optional[int]:
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    def status(self) -> DaemonStatus:
        with self._lock:
            exit_code = self._proc.poll() if self._proc is not None else None
            uptime = (self._clock() - self._start_time) if self._start_time else 0.0
            return DaemonStatus(
                state=self._state,
                pid=self._proc.pid if self._proc is not None else None,
                host=self._host,
                port=self._port,
                attached=self._state is DaemonState.ATTACHED,
                uptime_seconds=max(uptime, 0.0),
                exit_code=exit_code,
                probe_attempts=self._probe_attempts,
                last_event=self._last_event,
                tail_log_lines=self._ring.tail(50),
            )

    def drain_events(self) -> List[Tuple[DaemonLifecycleEvent, dict]]:
        drained: List[Tuple[DaemonLifecycleEvent, dict]] = []
        while True:
            try:
                drained.append(self._events.get_nowait())
            except queue.Empty:
                return drained

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> DaemonStatus:
        """Spawn the child (or attach to a foreign healthy instance).

        The call blocks until the readiness probe succeeds *or* the
        ``readiness_timeout`` budget is exceeded, in which case
        :class:`ComfyUINotResponsiveError` is raised and the supervisor
        cleanly tears down any half-spawned child.
        """

        with self._lock:
            if self._state in (DaemonState.READY, DaemonState.ATTACHED):
                return self.status()
            if self._state is DaemonState.STARTING:
                raise RuntimeError(
                    "DaemonSupervisor.start() called while already STARTING"
                )

            # Attach-over-Launch: if port is already bound AND the foreign
            # process answers the readiness probe, don't spawn anything.
            if self._allow_attach and _port_is_bound(self._host, self._port):
                if self._probe(host=self._host, port=self._port):
                    self._state = DaemonState.ATTACHED
                    self._start_time = self._clock()
                    self._emit(
                        DaemonLifecycleEvent.ATTACH_ACCEPTED,
                        {"port": self._port, "host": self._host},
                    )
                    return self.status()
                # Foreign process is bound but not healthy → fall through
                # to spawn, which will almost certainly fail with "address
                # already in use". The caller is better off seeing that
                # explicit error than silently attaching to a dead stub.

            self._state = DaemonState.STARTING
            self._emit(DaemonLifecycleEvent.SPAWN_REQUESTED, {"cmd": self._command})
            try:
                self._proc = self._popen_factory(
                    list(self._command),
                    cwd=self._cwd,
                    env=self._env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    bufsize=0,
                    **_popen_group_kwargs(),
                )
            except (OSError, ValueError) as exc:
                self._state = DaemonState.FAILED_TO_START
                raise DaemonCrashedError(
                    pid=None, exit_code=None, tail=(f"spawn failed: {exc!r}",)
                ) from exc

            self._start_time = self._clock()
            self._stdout_pump = _LogPump(
                stream=self._proc.stdout, name="stdout", ring=self._ring,
                on_signature=self._on_log_signature,
            )
            self._stderr_pump = _LogPump(
                stream=self._proc.stderr, name="stderr", ring=self._ring,
                on_signature=self._on_log_signature,
            )
            self._stdout_pump.start()
            self._stderr_pump.start()
            self._emit(DaemonLifecycleEvent.SPAWN_COMPLETED,
                       {"pid": self._proc.pid})

        # Readiness poll with exponential backoff (K8s-style).
        try:
            self._wait_for_ready()
        except BaseException:
            # On any exception during readiness wait, reclaim the child
            # so we never leak a process (zombie red line R1).
            self.stop(reason="readiness_failed")
            raise

        with self._lock:
            self._state = DaemonState.READY
            self._emit(DaemonLifecycleEvent.READINESS_OK,
                       {"attempts": self._probe_attempts})
            return self.status()

    def stop(self, *, reason: str = "stop_requested") -> DaemonStatus:
        """Terminate the child (or detach). Always safe to call twice."""

        with self._lock:
            if self._state in (DaemonState.IDLE, DaemonState.STOPPED):
                return self.status()
            if self._state is DaemonState.ATTACHED:
                # We did not spawn it; we MUST NOT terminate a foreign
                # process. Just detach.
                self._state = DaemonState.STOPPED
                self._emit(DaemonLifecycleEvent.STOP_COMPLETED,
                           {"reason": reason, "attached": True})
                return self.status()

            self._state = DaemonState.STOPPING
            self._emit(DaemonLifecycleEvent.STOP_REQUESTED, {"reason": reason})
            proc = self._proc

        exit_code: Optional[int] = None
        if proc is not None:
            exit_code = _terminate_process_tree(
                proc, graceful_timeout=self._graceful_timeout
            )
            # Drain log pumps so their file descriptors close.
            for pump in (self._stdout_pump, self._stderr_pump):
                if pump is not None:
                    pump.join(timeout=1.0)

        with self._lock:
            self._state = (
                DaemonState.STOPPED
                if exit_code in (0, None)
                else DaemonState.CRASHED
            )
            self._emit(DaemonLifecycleEvent.STOP_COMPLETED,
                       {"exit_code": exit_code, "reason": reason})
            return self.status()

    # ------------------------------------------------------------------
    # Context-manager ergonomics
    # ------------------------------------------------------------------

    def __enter__(self) -> "DaemonSupervisor":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop(reason="context_exit")

    # ------------------------------------------------------------------
    # Readiness loop
    # ------------------------------------------------------------------

    def _wait_for_ready(self) -> None:
        deadline = self._clock() + self._readiness_timeout
        backoff = self._backoff_initial
        while True:
            # Early abort: child died while we were waiting.
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                with self._lock:
                    self._state = DaemonState.CRASHED
                tail = self._ring.tail(20)
                self._emit(DaemonLifecycleEvent.CRASH_DETECTED,
                           {"exit_code": proc.returncode, "tail": list(tail)})
                raise DaemonCrashedError(
                    pid=proc.pid, exit_code=proc.returncode, tail=tail
                )

            if self._probe(host=self._host, port=self._port):
                return

            self._probe_attempts += 1
            self._emit(DaemonLifecycleEvent.PROBE_RETRY,
                       {"attempts": self._probe_attempts, "backoff": backoff})
            if self._clock() >= deadline:
                raise ComfyUINotResponsiveError(
                    f"ComfyUI did not become ready within "
                    f"{self._readiness_timeout:.1f}s; "
                    f"probe_attempts={self._probe_attempts}"
                )
            self._sleep(backoff)
            backoff = min(backoff * 2.0, self._backoff_max)

    # ------------------------------------------------------------------
    # Event plumbing
    # ------------------------------------------------------------------

    def _emit(self, event: DaemonLifecycleEvent, payload: dict) -> None:
        self._last_event = event.value
        self._events.put((event, dict(payload)))
        logger.debug("daemon_supervisor event=%s payload=%s", event.value, payload)

    def _on_log_signature(self, label: str, line: str) -> None:
        self._emit(DaemonLifecycleEvent.LOG_MATCH, {"label": label, "line": line})
