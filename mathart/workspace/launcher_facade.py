"""Launcher Facade — One-Shot ComfyUI Boot Bus (SESSION-134).

P0-SESSION-131-DAEMON-LAUNCHER — Phase 3 (integration)
======================================================

This Facade collapses the three infrastructure layers — Preflight Radar
(SESSION-132), Idempotent Surgeon (SESSION-133), Daemon Supervisor
(SESSION-134) — into a single, user-facing primitive. The exposed API is
intentionally narrow:

    facade = LauncherFacade(...)
    outcome = facade.start()   # radar -> surgeon -> supervisor
    try:
        ...                    # run workflows against outcome.endpoint_url
    finally:
        facade.stop()

Design anchors
--------------
- **Facade pattern (GoF)**: hide the three-stage pipeline behind a
  single ``.start() / .stop()`` contract.
- **Kubernetes Pod readiness**: the Facade does not hand out the
  endpoint URL until the supervisor has transitioned to READY or
  ATTACHED.
- **Strongly-typed outcomes**: :class:`LauncherOutcome` mirrors the
  semantics of Ansible / Terraform apply-reports (per-stage state plus
  an aggregated verdict).

Red lines
---------
- Facade MUST be idempotent: a second ``start()`` never spawns a second
  ComfyUI child.
- Facade MUST guarantee daemon cleanup on interpreter exit (delegated to
  ``DaemonSupervisor``'s atexit machinery).
- Facade MUST NOT enter the Supervisor stage if the radar/surgeon combo
  yielded ``manual_intervention_required`` — that contract is the whole
  point of Phase 1+2.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from .preflight_radar import (
    PreflightRadar,
    PreflightReport,
    PreflightVerdict,
)
from .idempotent_surgeon import (
    AssemblyReport,
    IdempotentSurgeon,
)
from .daemon_supervisor import (
    DaemonState,
    DaemonStatus,
    DaemonSupervisor,
    DaemonCrashedError,
    ComfyUINotResponsiveError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LauncherStage",
    "LauncherVerdict",
    "LauncherOutcome",
    "LauncherFacade",
]


class LauncherStage(str, Enum):
    NOT_STARTED = "not_started"
    RADAR = "radar"
    SURGEON = "surgeon"
    SUPERVISOR = "supervisor"
    RUNNING = "running"
    STOPPED = "stopped"
    ABORTED = "aborted"


class LauncherVerdict(str, Enum):
    READY = "ready"
    ATTACHED = "attached"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass
class LauncherOutcome:
    """Aggregated, strongly-typed result of a single ``start()`` call."""

    verdict: LauncherVerdict
    stage_reached: LauncherStage
    endpoint_url: Optional[str]
    preflight_verdict: Optional[str]
    preflight_report: Optional[dict] = None
    assembly_report: Optional[dict] = None
    daemon_status: Optional[dict] = None
    blocking_actions: tuple = ()
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "stage_reached": self.stage_reached.value,
            "endpoint_url": self.endpoint_url,
            "preflight_verdict": self.preflight_verdict,
            "preflight_report": self.preflight_report,
            "assembly_report": self.assembly_report,
            "daemon_status": self.daemon_status,
            "blocking_actions": list(self.blocking_actions),
            "error": self.error,
        }


class LauncherFacade:
    """One-shot Radar→Surgeon→Supervisor boot bus.

    Parameters are intentionally few; the heavy lifting lives in the
    three specialist classes. The Facade is responsible only for
    sequencing them and guaranteeing cleanup semantics.
    """

    def __init__(
        self,
        *,
        radar: Optional[PreflightRadar] = None,
        surgeon: Optional[IdempotentSurgeon] = None,
        supervisor: Optional[DaemonSupervisor] = None,
        comfyui_command: Optional[Sequence[str]] = None,
        comfyui_cwd: Optional[str | Path] = None,
        host: str = "127.0.0.1",
        port: int = 8188,
    ) -> None:
        self._radar = radar or PreflightRadar()
        self._surgeon = surgeon or IdempotentSurgeon()
        self._explicit_supervisor = supervisor
        self._supervisor: Optional[DaemonSupervisor] = supervisor
        self._comfyui_command = tuple(comfyui_command) if comfyui_command else None
        self._comfyui_cwd = str(comfyui_cwd) if comfyui_cwd else None
        self._host = host
        self._port = int(port)

        self._stage: LauncherStage = LauncherStage.NOT_STARTED
        self._lock = threading.RLock()
        self._last_outcome: Optional[LauncherOutcome] = None

    # ------------------------------------------------------------------
    # Public entry point — idempotent start
    # ------------------------------------------------------------------

    def start(self) -> LauncherOutcome:
        with self._lock:
            if self._stage is LauncherStage.RUNNING and self._supervisor is not None:
                # Already running — return cached status without side effects.
                return self._last_outcome or self._emit_running_outcome()

            # ---- Stage 1: Preflight Radar -------------------------------
            self._stage = LauncherStage.RADAR
            report = self._radar.scan()
            report_dict = _safe_to_dict(report)

            if report.verdict is PreflightVerdict.MANUAL_INTERVENTION_REQUIRED:
                return self._abort_manual(
                    report=report, report_dict=report_dict,
                    assembly=None,
                    reason="radar emitted manual_intervention_required",
                )

            # ---- Stage 2: Idempotent Surgeon ----------------------------
            assembly_dict: Optional[dict] = None
            if report.verdict is PreflightVerdict.AUTO_FIXABLE:
                self._stage = LauncherStage.SURGEON
                assembly = self._surgeon.operate(report)
                assembly_dict = _safe_to_dict(assembly)
                if not assembly.ok:
                    return self._abort_manual(
                        report=report, report_dict=report_dict,
                        assembly=assembly_dict,
                        reason="surgeon reported blocked/failed actions",
                    )

            # ---- Stage 3: Daemon Supervisor ----------------------------
            self._stage = LauncherStage.SUPERVISOR
            supervisor = self._ensure_supervisor(report)
            try:
                status = supervisor.start()
            except (DaemonCrashedError, ComfyUINotResponsiveError) as exc:
                self._stage = LauncherStage.ABORTED
                # SESSION-146: Log the full crash context with traceback
                # into the blackbox before returning the outcome.
                logger.warning(
                    "LauncherFacade supervisor CRASHED: %s",
                    exc,
                    exc_info=True,
                )
                outcome = LauncherOutcome(
                    verdict=LauncherVerdict.CRASHED,
                    stage_reached=LauncherStage.SUPERVISOR,
                    endpoint_url=None,
                    preflight_verdict=_verdict_value(report.verdict),
                    preflight_report=report_dict,
                    assembly_report=assembly_dict,
                    daemon_status=_safe_to_dict(supervisor.status()),
                    error=repr(exc),
                )
                self._last_outcome = outcome
                return outcome

            self._stage = LauncherStage.RUNNING
            verdict = (
                LauncherVerdict.ATTACHED
                if status.state is DaemonState.ATTACHED
                else LauncherVerdict.READY
            )
            outcome = LauncherOutcome(
                verdict=verdict,
                stage_reached=LauncherStage.RUNNING,
                endpoint_url=supervisor.endpoint_url,
                preflight_verdict=_verdict_value(report.verdict),
                preflight_report=report_dict,
                assembly_report=assembly_dict,
                daemon_status=_safe_to_dict(status),
            )
            self._last_outcome = outcome
            return outcome

    # ------------------------------------------------------------------
    # Public entry point — idempotent stop
    # ------------------------------------------------------------------

    def stop(self) -> LauncherOutcome:
        with self._lock:
            if self._supervisor is None:
                self._stage = LauncherStage.STOPPED
                return self._last_outcome or LauncherOutcome(
                    verdict=LauncherVerdict.STOPPED,
                    stage_reached=LauncherStage.STOPPED,
                    endpoint_url=None,
                    preflight_verdict=None,
                )
            status = self._supervisor.stop(reason="facade_stop")
            self._stage = LauncherStage.STOPPED
            outcome = LauncherOutcome(
                verdict=LauncherVerdict.STOPPED,
                stage_reached=LauncherStage.STOPPED,
                endpoint_url=None,
                preflight_verdict=(
                    self._last_outcome.preflight_verdict
                    if self._last_outcome else None
                ),
                preflight_report=(
                    self._last_outcome.preflight_report
                    if self._last_outcome else None
                ),
                assembly_report=(
                    self._last_outcome.assembly_report
                    if self._last_outcome else None
                ),
                daemon_status=_safe_to_dict(status),
            )
            self._last_outcome = outcome
            return outcome

    # ------------------------------------------------------------------
    # Context-manager ergonomics
    # ------------------------------------------------------------------

    def __enter__(self) -> "LauncherFacade":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_supervisor(self, report: PreflightReport) -> DaemonSupervisor:
        if self._supervisor is not None:
            return self._supervisor
        cmd = self._comfyui_command or self._infer_command(report)
        cwd = self._comfyui_cwd or getattr(report.comfyui, "root_path", None)
        self._supervisor = DaemonSupervisor(
            command=cmd, cwd=cwd, host=self._host, port=self._port
        )
        return self._supervisor

    @staticmethod
    def _infer_command(report: PreflightReport) -> Sequence[str]:
        comfy_root = getattr(report.comfyui, "root_path", None)
        if not comfy_root:
            raise RuntimeError(
                "LauncherFacade: no explicit comfyui_command provided and the "
                "radar did not locate a ComfyUI root to launch."
            )
        python = sys.executable
        return (python, str(Path(comfy_root) / "main.py"))

    def _abort_manual(
        self,
        *,
        report: PreflightReport,
        report_dict: dict,
        assembly: Optional[dict],
        reason: str,
    ) -> LauncherOutcome:
        self._stage = LauncherStage.ABORTED
        blocking: tuple = tuple(getattr(report, "blocking_actions", ()) or ())
        outcome = LauncherOutcome(
            verdict=LauncherVerdict.MANUAL_INTERVENTION_REQUIRED,
            stage_reached=self._stage,
            endpoint_url=None,
            preflight_verdict=_verdict_value(report.verdict),
            preflight_report=report_dict,
            assembly_report=assembly,
            blocking_actions=blocking,
            error=reason,
        )
        self._last_outcome = outcome
        # SESSION-146: Persist the full abort diagnostic into the blackbox
        # so that post-mortem never depends on terminal scrollback.
        import json as _json
        logger.warning(
            "LauncherFacade ABORTED — reason=%s, blocking=%s, "
            "preflight_report=%s",
            reason,
            list(blocking),
            _json.dumps(report_dict, ensure_ascii=False),
        )
        return outcome

    def _emit_running_outcome(self) -> LauncherOutcome:
        assert self._supervisor is not None
        status = self._supervisor.status()
        verdict = (
            LauncherVerdict.ATTACHED
            if status.state is DaemonState.ATTACHED
            else LauncherVerdict.READY
        )
        outcome = LauncherOutcome(
            verdict=verdict,
            stage_reached=LauncherStage.RUNNING,
            endpoint_url=self._supervisor.endpoint_url,
            preflight_verdict=None,
            daemon_status=_safe_to_dict(status),
        )
        self._last_outcome = outcome
        return outcome


# ---------------------------------------------------------------------------
# Serialization helpers (defensive — contracts may evolve independently)
# ---------------------------------------------------------------------------


def _safe_to_dict(obj) -> Optional[dict]:
    if obj is None:
        return None
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:  # pragma: no cover - defensive
            pass
    if hasattr(obj, "__dict__"):
        try:
            return asdict(obj)  # type: ignore[arg-type]
        except TypeError:
            pass
    return {"repr": repr(obj)}


def _verdict_value(v) -> str:
    return getattr(v, "value", str(v))
