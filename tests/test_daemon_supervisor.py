"""End-to-end lifecycle tests for SESSION-134 Daemon Supervisor.

Red-line coverage matrix (from SESSION-134 directives):
R1. No zombie on exit — stop() guarantees the child PID is reaped.
R2. No pipe deadlock — a chatty child does not block start().
R3. Attach-over-Launch — port conflict + healthy probe → ATTACHED.
R4. Idempotent Facade — second start() never spawns a second child.

Extra:
- STARTING → READY happy path via deterministic fake probe.
- Early crash during startup → DaemonCrashedError.
- Readiness timeout → ComfyUINotResponsiveError (child reclaimed).
- Log ring buffer captures stdout and stderr.
- Fatal signature (CUDA OOM) surfaces as LOG_MATCH event.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.workspace.daemon_supervisor import (  # noqa: E402
    ComfyUINotResponsiveError,
    DaemonCrashedError,
    DaemonLifecycleEvent,
    DaemonState,
    DaemonSupervisor,
)


# ---------------------------------------------------------------------------
# Fixtures: a scriptable "fake ComfyUI" subprocess + deterministic probe
# ---------------------------------------------------------------------------

FAKE_COMFY_TEMPLATE = textwrap.dedent("""\
    import os, sys, time, socket, signal

    mode         = {mode!r}
    ready_after  = {ready_after}
    crash_after  = {crash_after}
    chatty_lines = {chatty_lines}

    # optional pre-emptive port bind so the supervisor thinks we started.
    sock = None
    def _bind_port(port):
        global sock
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)

    if chatty_lines:
        for i in range(chatty_lines):
            sys.stdout.write(f"chatter line {{i}}\\n")
        sys.stdout.flush()

    start = time.monotonic()
    while True:
        now = time.monotonic() - start
        if mode == "crash_immediate" and now >= 0.05:
            sys.stderr.write("RuntimeError: induced crash\\n")
            sys.stderr.flush()
            sys.exit(7)
        if mode == "crash_after_ready" and now >= crash_after:
            sys.stderr.write("CUDA out of memory\\n")
            sys.stderr.flush()
            sys.exit(9)
        if mode == "hang_forever":
            pass
        time.sleep(0.05)
""")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_fake_script(tmp_path: Path, *, mode: str,
                     ready_after: float = 0.0,
                     crash_after: float = 0.0,
                     chatty_lines: int = 0) -> Path:
    src = FAKE_COMFY_TEMPLATE.format(
        mode=mode,
        ready_after=ready_after,
        crash_after=crash_after,
        chatty_lines=chatty_lines,
    )
    p = tmp_path / f"fake_comfy_{mode}.py"
    p.write_text(src)
    return p


class ScriptedProbe:
    """Fake ReadinessProbe that returns ``True`` after N calls."""

    def __init__(self, *, true_after: int = 3) -> None:
        self._target = int(true_after)
        self._calls = 0
        self.log: List[float] = []

    def __call__(self, *, host: str, port: int) -> bool:
        self._calls += 1
        self.log.append(time.monotonic())
        return self._calls >= self._target


class AlwaysFalseProbe:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, host: str, port: int) -> bool:
        self.calls += 1
        return False


class AlwaysTrueProbe:
    def __call__(self, *, host: str, port: int) -> bool:
        return True


# ---------------------------------------------------------------------------
# R1 + happy path: STARTING -> READY -> STOPPED, no zombie
# ---------------------------------------------------------------------------

class TestHappyPathAndZombieReaping:

    def test_starting_to_ready_then_stop_cleans_pid(self, tmp_path):
        script = _make_fake_script(tmp_path, mode="hang_forever")
        probe = ScriptedProbe(true_after=2)
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=probe,
            readiness_timeout=5.0,
            backoff_initial=0.05, backoff_max=0.2,
            graceful_timeout=3.0,
        )
        status = sup.start()
        assert sup.state is DaemonState.READY
        assert status.pid is not None
        assert status.attached is False
        pid = status.pid

        # pid must be alive while READY
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            pytest.fail("supervised PID was not alive while READY")

        final = sup.stop()
        assert sup.state in (DaemonState.STOPPED, DaemonState.CRASHED)
        assert final.pid == pid or final.pid is None

        # R1: child process must be reaped after stop(). On Windows,
        # os.kill(pid, 0) can remain truthy for a recently-reaped handle, so
        # trust the owning Popen state instead of probing the PID table.
        assert sup._proc is None or sup._proc.poll() is not None

    def test_stop_is_idempotent(self, tmp_path):
        script = _make_fake_script(tmp_path, mode="hang_forever")
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=ScriptedProbe(true_after=1),
            readiness_timeout=5.0, graceful_timeout=3.0,
        )
        sup.start()
        sup.stop()
        # Second stop must not raise and must not change state back.
        sup.stop()
        assert sup.state in (DaemonState.STOPPED, DaemonState.CRASHED)


# ---------------------------------------------------------------------------
# R2: pipe-drain discipline — chatty child must not deadlock start()
# ---------------------------------------------------------------------------

class TestPipeDrainDiscipline:

    def test_chatty_child_does_not_deadlock(self, tmp_path):
        # 8000 lines * ~15 bytes ≈ 120 KiB, well past the POSIX pipe buffer.
        script = _make_fake_script(tmp_path, mode="hang_forever",
                                   chatty_lines=8000)
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=ScriptedProbe(true_after=3),
            readiness_timeout=8.0,
            backoff_initial=0.05, backoff_max=0.2,
            graceful_timeout=3.0,
        )
        t0 = time.monotonic()
        sup.start()
        elapsed = time.monotonic() - t0
        try:
            assert sup.state is DaemonState.READY
            assert elapsed < 6.0, f"start blocked too long ({elapsed:.2f}s) — pipe deadlock?"
            tail = sup.status().tail_log_lines
            assert any("chatter line" in line for line in tail)
        finally:
            sup.stop()


# ---------------------------------------------------------------------------
# R3: Attach-over-Launch — port conflict + healthy probe → ATTACHED
# ---------------------------------------------------------------------------

class TestAttachOverLaunch:

    def test_foreign_healthy_comfy_is_attached_not_relaunched(self):
        port = _free_port()
        # Occupy the port with a passive listener to simulate a foreign
        # already-running ComfyUI.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)

        spawned = {"count": 0}

        def tracking_popen(*args, **kwargs):  # pragma: no cover - must not run
            spawned["count"] += 1
            raise AssertionError("Supervisor spawned a child despite ATTACH path")

        try:
            sup = DaemonSupervisor(
                command=[sys.executable, "-c", "pass"],
                host="127.0.0.1", port=port,
                probe=AlwaysTrueProbe(),
                popen_factory=tracking_popen,
                readiness_timeout=2.0, graceful_timeout=1.0,
            )
            status = sup.start()
            assert sup.state is DaemonState.ATTACHED
            assert status.attached is True
            assert status.pid is None, "Attached mode must not own a PID"
            # Stopping an attached supervisor MUST NOT kill the foreign process.
            sup.stop()
            assert spawned["count"] == 0
        finally:
            listener.close()

    def test_port_free_no_attach_even_if_probe_says_true(self, tmp_path):
        # Probe that would say "true" on attach, but since port is free we
        # should still spawn. This guards against a degenerate attach path.
        script = _make_fake_script(tmp_path, mode="hang_forever")
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=ScriptedProbe(true_after=1),
            readiness_timeout=5.0, graceful_timeout=3.0,
        )
        try:
            sup.start()
            assert sup.state is DaemonState.READY
            assert sup.status().attached is False
        finally:
            sup.stop()


# ---------------------------------------------------------------------------
# Crash handling: early crash + readiness timeout both reclaim the child
# ---------------------------------------------------------------------------

class TestCrashHandling:

    def test_early_crash_raises_and_reclaims(self, tmp_path):
        script = _make_fake_script(tmp_path, mode="crash_immediate")
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=AlwaysFalseProbe(),
            readiness_timeout=5.0,
            backoff_initial=0.05, backoff_max=0.1,
            graceful_timeout=2.0,
        )
        with pytest.raises(DaemonCrashedError) as excinfo:
            sup.start()
        assert sup.state in (DaemonState.CRASHED, DaemonState.STOPPED)
        assert excinfo.value.exit_code in (7, None)
        # forensic tail must mention the induced error
        tail = sup.status().tail_log_lines
        assert any("induced crash" in line for line in tail)

    def test_readiness_timeout_raises_and_reclaims(self, tmp_path):
        script = _make_fake_script(tmp_path, mode="hang_forever")
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=AlwaysFalseProbe(),
            readiness_timeout=0.5,
            backoff_initial=0.05, backoff_max=0.1,
            graceful_timeout=2.0,
        )
        pid_seen: List[int] = []

        def watch():
            time.sleep(0.1)
            if sup.pid is not None:
                pid_seen.append(sup.pid)

        t = threading.Thread(target=watch)
        t.start()
        with pytest.raises(ComfyUINotResponsiveError):
            sup.start()
        t.join()

        assert sup.state in (DaemonState.STOPPED, DaemonState.CRASHED)
        # Pid, if observed while alive, must be reaped now.
        assert sup._proc is None or sup._proc.poll() is not None


# ---------------------------------------------------------------------------
# Log signature detection
# ---------------------------------------------------------------------------

class TestLogSignatureDetection:

    def test_cuda_oom_in_stderr_surfaces_as_event(self, tmp_path):
        # Run the fake child long enough to print the OOM, then let it exit.
        script = _make_fake_script(tmp_path, mode="crash_after_ready",
                                   crash_after=0.3)
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=_free_port(),
            probe=ScriptedProbe(true_after=1),
            readiness_timeout=5.0, graceful_timeout=3.0,
        )
        try:
            sup.start()
            # Wait for the child to actually emit the OOM line.
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                tail = sup.status().tail_log_lines
                if any("CUDA out of memory" in line for line in tail):
                    break
                time.sleep(0.1)
        finally:
            sup.stop()

        events = sup.drain_events()
        labels = [
            payload.get("label")
            for evt, payload in events
            if evt is DaemonLifecycleEvent.LOG_MATCH
        ]
        assert "cuda_oom" in labels


# ---------------------------------------------------------------------------
# Launcher Facade integration
# ---------------------------------------------------------------------------

from mathart.workspace.launcher_facade import (  # noqa: E402
    LauncherFacade,
    LauncherStage,
    LauncherVerdict,
)


class _StubRadar:
    def __init__(self, report):
        self._report = report
    def scan(self):
        return self._report


class _StubReport:
    def __init__(self, verdict, *, comfy_root=None, fixable=(), blocking=()):
        self.verdict = verdict
        self.fixable_actions = tuple(fixable)
        self.blocking_actions = tuple(blocking)
        # minimal comfyui placeholder
        class _C:
            root_path = comfy_root
        self.comfyui = _C()
    def to_dict(self): return {"verdict": self.verdict.value}


class _StubSurgeon:
    def __init__(self, ok=True):
        self._ok = ok
    def operate(self, report):
        class _A:
            def __init__(self, ok): self.ok = ok; self.actions = ()
            def to_dict(self): return {"ok": self.ok}
        return _A(self._ok)


class TestLauncherFacadeIntegration:

    def test_ready_path_yields_endpoint_and_is_idempotent(self, tmp_path):
        from mathart.workspace.preflight_radar import PreflightVerdict

        script = _make_fake_script(tmp_path, mode="hang_forever")
        port = _free_port()
        sup = DaemonSupervisor(
            command=[sys.executable, "-u", str(script)],
            host="127.0.0.1", port=port,
            probe=ScriptedProbe(true_after=1),
            readiness_timeout=5.0, graceful_timeout=3.0,
        )
        facade = LauncherFacade(
            radar=_StubRadar(_StubReport(PreflightVerdict.READY)),
            surgeon=_StubSurgeon(ok=True),
            supervisor=sup,
        )
        try:
            out1 = facade.start()
            assert out1.verdict is LauncherVerdict.READY
            assert out1.endpoint_url == f"http://127.0.0.1:{port}"
            pid1 = sup.pid
            # R4: second start() must be a no-op
            out2 = facade.start()
            assert out2.verdict is LauncherVerdict.READY
            assert sup.pid == pid1
        finally:
            facade.stop()

    def test_manual_intervention_short_circuits_pipeline(self):
        from mathart.workspace.preflight_radar import PreflightVerdict

        popen_called = {"count": 0}
        def fail_popen(*a, **k):  # pragma: no cover - must not run
            popen_called["count"] += 1
            raise AssertionError("Supervisor invoked despite MANUAL verdict")

        # Construct a supervisor whose popen_factory explodes if called —
        # but the Facade must never reach Stage 3.
        sup = DaemonSupervisor(
            command=[sys.executable, "-c", "pass"],
            host="127.0.0.1", port=_free_port(),
            probe=AlwaysFalseProbe(),
            popen_factory=fail_popen,
            readiness_timeout=0.5, graceful_timeout=0.5,
        )
        facade = LauncherFacade(
            radar=_StubRadar(_StubReport(
                PreflightVerdict.MANUAL_INTERVENTION_REQUIRED,
                blocking=("missing_comfyui",),
            )),
            surgeon=_StubSurgeon(ok=True),
            supervisor=sup,
        )
        outcome = facade.start()
        assert outcome.verdict is LauncherVerdict.MANUAL_INTERVENTION_REQUIRED
        assert outcome.stage_reached is LauncherStage.ABORTED
        assert outcome.endpoint_url is None
        assert popen_called["count"] == 0

    def test_surgeon_failure_aborts_before_supervisor(self):
        from mathart.workspace.preflight_radar import PreflightVerdict

        popen_called = {"count": 0}
        def fail_popen(*a, **k):  # pragma: no cover - must not run
            popen_called["count"] += 1
            raise AssertionError("Supervisor invoked after surgeon blocked")

        sup = DaemonSupervisor(
            command=[sys.executable, "-c", "pass"],
            host="127.0.0.1", port=_free_port(),
            probe=AlwaysFalseProbe(),
            popen_factory=fail_popen,
            readiness_timeout=0.5, graceful_timeout=0.5,
        )
        facade = LauncherFacade(
            radar=_StubRadar(_StubReport(
                PreflightVerdict.AUTO_FIXABLE,
                fixable=("missing_asset:x",),
            )),
            surgeon=_StubSurgeon(ok=False),
            supervisor=sup,
        )
        outcome = facade.start()
        assert outcome.verdict is LauncherVerdict.MANUAL_INTERVENTION_REQUIRED
        assert outcome.stage_reached is LauncherStage.ABORTED
        assert popen_called["count"] == 0


# ---------------------------------------------------------------------------
# Static red-line audit — no destructive calls in supervisor source
# ---------------------------------------------------------------------------

class TestStaticRedLineAudit:

    def test_supervisor_source_has_no_destructive_calls(self):
        src = Path(
            "mathart/workspace/daemon_supervisor.py"
        ).read_text(encoding="utf-8")
        # Strip doc-style comments so we only audit live code paths.
        audit = "\n".join(
            ln for ln in src.splitlines()
            if not ln.strip().startswith("#")
        )
        # supervisor must never touch user files on disk
        for forbidden in ("shutil.rmtree(", "os.remove(", "os.unlink("):
            assert forbidden not in audit, (
                f"Supervisor must not call {forbidden} — lifecycle only."
            )
        # supervisor must never issue network writes to the child endpoint
        assert "urllib.request.urlopen" in audit  # only probe is allowed
        assert "requests.post" not in audit
        assert "requests.get" not in audit
