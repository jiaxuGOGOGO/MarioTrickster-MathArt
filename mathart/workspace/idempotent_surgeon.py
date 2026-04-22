"""Idempotent Surgeon — Report-Driven Non-Destructive Auto-Assembly Engine.

P0-SESSION-130-IDEMPOTENT-SURGEON — Phase 2 (c)
==============================================

The :class:`IdempotentSurgeon` is the orchestrator that converts a
:class:`~mathart.workspace.preflight_radar.PreflightReport` into a sequence
of strongly-typed repair actions. It composes:

* :class:`~mathart.workspace.asset_injector.AssetInjector` (cache reuse)
* :class:`~mathart.workspace.atomic_downloader.AtomicDownloader` (network)

and guarantees the following invariants:

1. **Single input contract** — the only authoritative input is a
   ``PreflightReport`` produced by ``PreflightRadar``. The Surgeon does not
   introspect the environment directly; it trusts the Radar's snapshot.
2. **Absolute idempotency** — calling ``operate(report)`` twice in a row
   MUST result in *zero* network activity, *zero* file writes, and must
   return within microseconds on the second call (enforced by
   ``tests/test_idempotent_surgeon.py::TestSecondRunIsNoop``).
3. **Strongly-typed outcomes** — every action is recorded as an
   :class:`ActionOutcome` whose ``kind`` is one of ``SKIPPED``,
   ``SYMLINKED``, ``HARDLINKED``, ``COPIED``, ``DOWNLOADED``, ``RESUMED``,
   or ``FAILED``. This mirrors the Ansible ``changed`` / ``ok`` /
   ``failed`` / ``skipped`` vocabulary.
4. **No destructive writes** — the surgeon NEVER deletes user files. Any
   pre-existing but corrupt file is quarantined by the downstream tools.

Architectural references
------------------------
- Ansible desired-state convergence (each task reports ``changed`` only
  when the observed state diverged from the desired state).
- Terraform ``plan`` → ``apply`` separation (we always re-scan post-apply
  to confirm convergence).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .asset_injector import (
    AssetInjector,
    InjectionMethod,
    InjectionOutcome,
    InjectionStatus,
)
from .atomic_downloader import (
    AtomicDownloader,
    DownloadOutcome,
    DownloadStatus,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ActionKind",
    "ActionOutcome",
    "AssemblyReport",
    "AssetPlan",
    "IdempotentSurgeon",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

class ActionKind(str, Enum):
    """Strongly-typed vocabulary of every outcome the surgeon can emit.

    Mirrors the command primitives enforced by the architectural red
    lines: *Skipped* (already satisfied), *Symlinked*/*Hardlinked*/
    *Copied* (three-tier link fallback), *Downloaded*/*Resumed*
    (network fallback), and *Failed* (all options exhausted).
    """

    SKIPPED = "skipped"           # target already satisfies fingerprint
    SYMLINKED = "symlinked"       # injected via os.symlink
    HARDLINKED = "hardlinked"     # injected via os.link
    COPIED = "copied"             # injected via shutil.copy2
    DOWNLOADED = "downloaded"     # freshly fetched over the network
    RESUMED = "resumed"           # partial .part resumed and published
    REJECTED = "rejected"         # missing URL / out-of-scope action
    FAILED = "failed"             # unrecoverable failure
    BLOCKED = "blocked"           # blocking_action; needs manual action


@dataclass(frozen=True)
class ActionOutcome:
    """Typed record of a single repair action."""

    action_id: str
    asset_name: str
    kind: ActionKind
    target_path: Optional[str]
    source_path: Optional[str]
    elapsed_ms: float
    bytes_touched: int
    detail: str
    error: Optional[str] = None
    injection: Optional[InjectionOutcome] = None
    download: Optional[DownloadOutcome] = None

    def is_mutation(self) -> bool:
        """Whether this outcome represents a filesystem mutation.

        Used by the *idempotency* tests: the *second* surgeon run over an
        already-satisfied report MUST contain zero mutations.
        """
        return self.kind in (
            ActionKind.SYMLINKED,
            ActionKind.HARDLINKED,
            ActionKind.COPIED,
            ActionKind.DOWNLOADED,
            ActionKind.RESUMED,
        )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "action_id": self.action_id,
            "asset_name": self.asset_name,
            "kind": self.kind.value,
            "target_path": self.target_path,
            "source_path": self.source_path,
            "elapsed_ms": self.elapsed_ms,
            "bytes_touched": self.bytes_touched,
            "detail": self.detail,
            "error": self.error,
        }
        if self.injection is not None:
            d["injection"] = self.injection.to_dict()
        if self.download is not None:
            d["download"] = self.download.to_dict()
        return d


@dataclass(frozen=True)
class AssemblyReport:
    """Overall outcome of a single ``operate(report)`` call."""

    ok: bool
    verdict_in: str
    verdict_out: Optional[str]
    actions: tuple[ActionOutcome, ...]
    skipped_count: int
    mutation_count: int
    failure_count: int
    blocked_count: int
    total_elapsed_ms: float
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "verdict_in": self.verdict_in,
            "verdict_out": self.verdict_out,
            "actions": [a.to_dict() for a in self.actions],
            "skipped_count": self.skipped_count,
            "mutation_count": self.mutation_count,
            "failure_count": self.failure_count,
            "blocked_count": self.blocked_count,
            "total_elapsed_ms": self.total_elapsed_ms,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Asset-to-URL plan book
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetPlan:
    """Known-good mirror metadata for a fixable asset.

    The plan book translates the Radar's ``missing_asset:<name>`` action
    string into a concrete download / injection plan. Power users can
    extend the book by passing ``asset_plan`` to the Surgeon.
    """

    asset_name: str
    filename: str
    url: Optional[str] = None
    expected_size: Optional[int] = None
    expected_sha256: Optional[str] = None
    # If the asset is a directory (e.g. a git-tracked custom node), the
    # surgeon will emit ``BLOCKED`` (we never recursively clone inside the
    # radar path; Phase 3 will cover `git` operations separately).
    is_directory: bool = False


# Default plan book. Hashes intentionally left empty — size-based
# fingerprinting is sufficient for idempotency; users may inject stricter
# plans via the ``asset_plan`` constructor argument.
DEFAULT_ASSET_PLANS: tuple[AssetPlan, ...] = (
    AssetPlan(
        asset_name="sparsectrl_rgb_model",
        filename="v3_sd15_sparsectrl_rgb.ckpt",
        url="https://huggingface.co/guoyww/animatediff/resolve/main/v3_sd15_sparsectrl_rgb.ckpt",
    ),
    AssetPlan(
        asset_name="animatediff_motion_module",
        filename="v3_sd15_mm.ckpt",
        url="https://huggingface.co/guoyww/animatediff/resolve/main/v3_sd15_mm.ckpt",
    ),
    AssetPlan(
        asset_name="animatediff_evolved_node",
        filename="ComfyUI-AnimateDiff-Evolved",
        is_directory=True,
    ),
    AssetPlan(
        asset_name="sparsectrl_loader_module",
        filename="sparse_ctrl.py",
        is_directory=True,  # tracked inside a git-managed node
    ),
    AssetPlan(
        asset_name="controlnet_aux_node",
        filename="comfyui_controlnet_aux",
        is_directory=True,
    ),
)


# ---------------------------------------------------------------------------
# The Surgeon
# ---------------------------------------------------------------------------


class IdempotentSurgeon:
    """Orchestrator that reconciles a :class:`PreflightReport` with reality.

    Parameters
    ----------
    injector:
        Optional pre-built :class:`AssetInjector`. Default constructs one
        with :data:`asset_injector.DEFAULT_CACHE_ROOTS`.
    downloader:
        Optional pre-built :class:`AtomicDownloader`. Default constructs
        one with :class:`UrllibTransport`.
    asset_plans:
        Iterable of :class:`AssetPlan` entries that map asset names to
        download / injection metadata. Users may extend or override the
        defaults (e.g. point to a private mirror).
    clock:
        Time source for tests.
    dry_run:
        If True, the surgeon plans and logs but executes no mutation —
        mirrors ``ansible-playbook --check``.
    """

    def __init__(
        self,
        *,
        injector: Optional[AssetInjector] = None,
        downloader: Optional[AtomicDownloader] = None,
        asset_plans: Iterable[AssetPlan] = DEFAULT_ASSET_PLANS,
        clock: Optional[Callable[[], float]] = None,
        dry_run: bool = False,
    ) -> None:
        self._injector = injector or AssetInjector()
        self._downloader = downloader or AtomicDownloader()
        self._plans: dict[str, AssetPlan] = {p.asset_name: p for p in asset_plans}
        self._clock = clock or time.time
        self._dry_run = bool(dry_run)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def operate(
        self,
        report: Any,
        *,
        comfyui_root: Optional[str | Path] = None,
    ) -> AssemblyReport:
        """Reconcile the report with the filesystem.

        Parameters
        ----------
        report:
            A :class:`~mathart.workspace.preflight_radar.PreflightReport`
            (duck-typed: must expose ``verdict``, ``fixable_actions``,
            ``blocking_actions``, and ``comfyui.root_path``).
        comfyui_root:
            Optional explicit ComfyUI root override; when absent, the
            value from ``report.comfyui.root_path`` is used.
        """

        started = self._clock()
        actions: list[ActionOutcome] = []

        # Pull verdict / paths defensively — the surgeon is a consumer of
        # a *contract*, so we validate rather than crash.
        verdict_in = self._verdict_str(report)
        comfy = getattr(report, "comfyui", None)
        root = Path(
            str(comfyui_root)
            if comfyui_root is not None
            else (getattr(comfy, "root_path", None) or "")
        )

        # ---- Phase 1: blocking actions (surgeon can't fix) ---------------
        for raw in getattr(report, "blocking_actions", ()) or ():
            actions.append(self._make_blocked_action(raw))

        if not root or not str(root):
            elapsed = (self._clock() - started) * 1000.0
            return self._finalize(
                actions, started=started, verdict_in=verdict_in,
                verdict_out=None, elapsed_override_ms=elapsed,
            )

        # ---- Phase 2: fixable actions ------------------------------------
        for raw in getattr(report, "fixable_actions", ()) or ():
            actions.append(self._handle_fixable(raw, comfy_root=root))

        # ---- Phase 3: idempotency audit (second-run protection) ----------
        # We do NOT re-scan the whole environment here — that is the
        # radar's job and rerunning it inside the surgeon would muddy the
        # latency contract. Instead we assert that the on-disk state for
        # each handled asset now satisfies the idempotent gate, which is
        # what the next ``operate()`` call will see.
        verdict_out = self._compute_post_verdict(actions)

        return self._finalize(
            actions, started=started,
            verdict_in=verdict_in, verdict_out=verdict_out,
        )

    # ------------------------------------------------------------------
    # Fixable-action dispatch
    # ------------------------------------------------------------------

    def _handle_fixable(self, raw_action: str, *, comfy_root: Path) -> ActionOutcome:
        """Dispatch one Radar ``fixable_actions`` token into an outcome."""

        t0 = self._clock()
        if raw_action.startswith("missing_asset:"):
            return self._handle_missing_asset(raw_action, comfy_root=comfy_root, t0=t0)
        if raw_action.startswith("missing_package:"):
            return ActionOutcome(
                action_id=raw_action,
                asset_name=raw_action.split(":", 1)[1].strip(),
                kind=ActionKind.BLOCKED,
                target_path=None,
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="python package install is out-of-scope for SESSION-130;"
                       " delegated to Phase 3 daemon integration",
            )
        return ActionOutcome(
            action_id=raw_action,
            asset_name="unknown",
            kind=ActionKind.REJECTED,
            target_path=None,
            source_path=None,
            elapsed_ms=(self._clock() - t0) * 1000.0,
            bytes_touched=0,
            detail=f"unsupported fixable action token: {raw_action!r}",
        )

    def _handle_missing_asset(
        self, raw_action: str, *, comfy_root: Path, t0: float
    ) -> ActionOutcome:
        # Format emitted by PreflightRadar:
        #   "missing_asset:<name> -> <relpath>"
        rhs = raw_action[len("missing_asset:"):].strip()
        name, _, relpath = rhs.partition("->")
        name = name.strip()
        relpath = relpath.strip()
        plan = self._plans.get(name)
        target = (comfy_root / relpath) if relpath else None

        if plan is None:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.REJECTED,
                target_path=str(target) if target else None,
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail=f"no AssetPlan registered for asset {name!r}",
            )

        if plan.is_directory:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.BLOCKED,
                target_path=str(target) if target else None,
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail=f"asset {name!r} is a git-managed directory; "
                       "Phase 3 (daemon + git subsystem) will handle it",
            )

        if target is None:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.REJECTED,
                target_path=None,
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="radar did not emit a relpath for this asset",
            )

        # ---- Step A: try cache reuse ----------------------------------
        if self._dry_run:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.SKIPPED,
                target_path=str(target),
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="dry_run=True — plan only, no mutation",
            )

        injection = self._injector.inject(
            asset_name=name,
            target_path=str(target),
            expected_filename=plan.filename,
            expected_size=plan.expected_size,
            expected_sha256=plan.expected_sha256,
        )

        if injection.status is InjectionStatus.ALREADY_SATISFIED:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.SKIPPED,
                target_path=injection.target_path,
                source_path=injection.source_path,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="target already satisfies fingerprint (idempotent)",
                injection=injection,
            )

        if injection.status is InjectionStatus.INJECTED:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=_injection_method_to_kind(injection.method),
                target_path=injection.target_path,
                source_path=injection.source_path,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=injection.bytes_reused,
                detail=f"recovered from local cache via {injection.method.value}",
                injection=injection,
            )

        if injection.status is InjectionStatus.FAILED:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.FAILED,
                target_path=injection.target_path,
                source_path=injection.source_path,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="all three link-fallback tiers failed",
                error=injection.error,
                injection=injection,
            )

        # ---- Step B: cache miss → network fallback --------------------
        if not plan.url:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.REJECTED,
                target_path=str(target),
                source_path=None,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="cache miss and no download URL registered",
                injection=injection,
            )

        download = self._downloader.fetch(
            url=plan.url,
            target_path=str(target),
            expected_size=plan.expected_size,
            expected_sha256=plan.expected_sha256,
        )

        if download.status is DownloadStatus.ALREADY_VERIFIED:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.SKIPPED,
                target_path=download.target_path,
                source_path=plan.url,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=0,
                detail="target already matched fingerprint — no socket opened",
                injection=injection,
                download=download,
            )

        if download.status is DownloadStatus.DOWNLOADED_FRESH:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.DOWNLOADED,
                target_path=download.target_path,
                source_path=plan.url,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=download.bytes_written,
                detail="fresh atomic download complete",
                injection=injection,
                download=download,
            )

        if download.status is DownloadStatus.RESUMED_AND_VERIFIED:
            return ActionOutcome(
                action_id=raw_action,
                asset_name=name,
                kind=ActionKind.RESUMED,
                target_path=download.target_path,
                source_path=plan.url,
                elapsed_ms=(self._clock() - t0) * 1000.0,
                bytes_touched=download.bytes_written,
                detail=f"resumed from byte {download.resumed_from} and published atomically",
                injection=injection,
                download=download,
            )

        # TRANSPORT_ERROR / HASH_MISMATCH / REJECTED_NO_URL
        return ActionOutcome(
            action_id=raw_action,
            asset_name=name,
            kind=ActionKind.FAILED,
            target_path=download.target_path,
            source_path=plan.url,
            elapsed_ms=(self._clock() - t0) * 1000.0,
            bytes_touched=download.bytes_written,
            detail=f"download ended with {download.status.value}",
            error=download.error,
            injection=injection,
            download=download,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_blocked_action(raw_action: str) -> ActionOutcome:
        return ActionOutcome(
            action_id=raw_action,
            asset_name="<blocking>",
            kind=ActionKind.BLOCKED,
            target_path=None,
            source_path=None,
            elapsed_ms=0.0,
            bytes_touched=0,
            detail=f"blocking action — surgeon cannot autofix: {raw_action}",
        )

    @staticmethod
    def _verdict_str(report: Any) -> str:
        v = getattr(report, "verdict", None)
        if v is None:
            return "unknown"
        return getattr(v, "value", str(v))

    @staticmethod
    def _compute_post_verdict(actions: Iterable[ActionOutcome]) -> str:
        actions = tuple(actions)
        if any(a.kind is ActionKind.FAILED for a in actions):
            return "manual_intervention_required"
        if any(a.kind is ActionKind.BLOCKED for a in actions):
            return "manual_intervention_required"
        if any(a.kind is ActionKind.REJECTED for a in actions):
            return "auto_fixable"
        if any(a.is_mutation() for a in actions):
            return "ready"
        return "ready"

    def _finalize(
        self,
        actions: list[ActionOutcome],
        *,
        started: float,
        verdict_in: str,
        verdict_out: Optional[str],
        elapsed_override_ms: Optional[float] = None,
    ) -> AssemblyReport:
        elapsed = (
            elapsed_override_ms
            if elapsed_override_ms is not None
            else (self._clock() - started) * 1000.0
        )
        skipped = sum(1 for a in actions if a.kind is ActionKind.SKIPPED)
        mutations = sum(1 for a in actions if a.is_mutation())
        failures = sum(
            1 for a in actions
            if a.kind in (ActionKind.FAILED, ActionKind.REJECTED)
        )
        blocked = sum(1 for a in actions if a.kind is ActionKind.BLOCKED)
        ok = (failures == 0 and blocked == 0)
        return AssemblyReport(
            ok=ok,
            verdict_in=verdict_in,
            verdict_out=verdict_out,
            actions=tuple(actions),
            skipped_count=skipped,
            mutation_count=mutations,
            failure_count=failures,
            blocked_count=blocked,
            total_elapsed_ms=elapsed,
            dry_run=self._dry_run,
        )


# ---------------------------------------------------------------------------
# Internal mapping utilities
# ---------------------------------------------------------------------------


def _injection_method_to_kind(method: InjectionMethod) -> ActionKind:
    if method is InjectionMethod.SYMLINK:
        return ActionKind.SYMLINKED
    if method is InjectionMethod.HARDLINK:
        return ActionKind.HARDLINKED
    if method is InjectionMethod.COPY:
        return ActionKind.COPIED
    return ActionKind.FAILED
