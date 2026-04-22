"""Asset Injector — Zero-Copy Cache Recovery & Three-Tier Link Fallback (SESSION-133).

P0-SESSION-130-IDEMPOTENT-SURGEON — Phase 2 (a)
==============================================

This module implements the **Asset Injector** — the first-line healer that
tries to *recover* missing ComfyUI assets from already-present local caches
before any byte is fetched from the network.

Architectural anchors (binding; see ``docs/research/SESSION-133-SURGEON-RESEARCH.md``)
--------------------------------------------------------------------------------------
1. **HuggingFace content-addressable cache** —
   ``~/.cache/huggingface/hub/models--<org>--<repo>/blobs/<sha>`` plus
   ``snapshots/<rev>/<file> -> ../../blobs/<sha>`` symlinks. We scan the
   blobs by **size** and (optionally) **SHA-256** before touching the wire.
2. **pnpm content-addressable store** —
   pnpm hard-links packages from the global store into every project's
   ``node_modules``. We do the same: a single byte lives once on disk; the
   project's ComfyUI tree is *aliased* into it.
3. **Windows symlink privilege trap (WinError 1314)** —
   Non-Admin + non-Developer-Mode Windows users cannot create symlinks.
   Blindly calling ``os.symlink`` crashes the whole pipeline. We therefore
   employ a strict **three-tier fallback**: symlink → hardlink → copy.

Red lines enforced by this module
---------------------------------
- **R1 (no blind overwrite)**: if the target path already exists with a
  differing fingerprint, it is quarantined to ``<path>.bak-<ts>`` before
  replacement — never raw deletion (``os.remove`` / ``shutil``-style
  recursive removal).
- **R2 (zero exception escape)**: every FS mutation is wrapped so that the
  caller (``IdempotentSurgeon``) always gets a strongly-typed
  ``InjectionOutcome`` — never an uncaught ``OSError``.
- **R3 (Windows graceful degrade)**: ``OSError`` (including ``WinError
  1314``), ``NotImplementedError``, and ``AttributeError`` from
  ``os.symlink`` / ``os.link`` silently trigger the next fallback tier.
- **R4 (idempotency)**: if the target already points at the correct
  resolved source, return ``ALREADY_SATISFIED`` without any mutation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .hitl_boundary import (
    ManualInterventionRequiredError,
    is_windows_symlink_privilege_error,
    symlink_manual_error,
)

logger = logging.getLogger(__name__)

__all__ = [
    "InjectionMethod",
    "InjectionStatus",
    "InjectionOutcome",
    "AssetInjector",
    "DEFAULT_CACHE_ROOTS",
]


# ---------------------------------------------------------------------------
# Strongly-typed outcome vocabulary
# ---------------------------------------------------------------------------

class InjectionMethod(str, Enum):
    """Which physical technique was used to materialise the target."""

    NONE = "none"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    COPY = "copy"


class InjectionStatus(str, Enum):
    """Outcome of a single injection attempt."""

    ALREADY_SATISFIED = "already_satisfied"    # idempotent no-op
    INJECTED = "injected"                      # successful materialisation
    CACHE_MISS = "cache_miss"                  # needs downloader fallback
    QUARANTINED_CONFLICT = "quarantined_conflict"  # bad file moved to .bak
    FAILED = "failed"                          # all three tiers exhausted


@dataclass(frozen=True)
class InjectionOutcome:
    """Strongly-typed record returned for every injection attempt."""

    asset_name: str
    target_path: str
    status: InjectionStatus
    method: InjectionMethod
    source_path: Optional[str]
    quarantined_backup: Optional[str]
    bytes_reused: int
    elapsed_ms: float
    notes: tuple[str, ...] = field(default_factory=tuple)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["method"] = self.method.value
        return d


# ---------------------------------------------------------------------------
# Default cache roots (probed; NEVER created)
# ---------------------------------------------------------------------------

DEFAULT_CACHE_ROOTS: tuple[str, ...] = (
    # HuggingFace Hub
    "~/.cache/huggingface/hub",
    "~/.cache/huggingface",
    "~/AppData/Local/huggingface/hub",
    # Torch
    "~/.cache/torch/hub/checkpoints",
    "~/.cache/torch",
    # Civitai / AUTOMATIC1111 / ComfyUI alt installs
    "~/stable-diffusion-webui/models",
    "~/ComfyUI/models",
    "~/comfyui/models",
    # Windows drive conventions
    "C:/huggingface",
    "D:/huggingface",
    "D:/AI/models",
    "E:/AI/models",
    "F:/AI/models",
)


# ---------------------------------------------------------------------------
# Filesystem primitives (defensive helpers)
# ---------------------------------------------------------------------------

def _expand(raw: str | os.PathLike[str]) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(raw))))


def _safe_size(p: Path) -> Optional[int]:
    try:
        return p.stat().st_size
    except OSError:
        return None


def _safe_sha256(p: Path, max_bytes: Optional[int] = None) -> Optional[str]:
    """Streaming SHA-256; defaults to full-file hashing.

    ``max_bytes`` is an optional tripwire for quick fingerprint checks on
    giant models where a full hash is unaffordable on the startup path.
    """
    try:
        h = hashlib.sha256()
        read = 0
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                if max_bytes is not None and read >= max_bytes:
                    break
        return h.hexdigest()
    except OSError as exc:
        logger.debug("asset_injector: sha256 failed for %s: %s", p, exc)
        return None


def _is_same_inode(a: Path, b: Path) -> bool:
    """True iff ``a`` and ``b`` are the same on-disk object (hardlink or eq)."""
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return (
        sa.st_dev == sb.st_dev
        and sa.st_ino == sb.st_ino
        and sa.st_ino != 0
    )


def _resolve_noraise(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


# ---------------------------------------------------------------------------
# The Asset Injector
# ---------------------------------------------------------------------------


@dataclass
class _InjectionSpec:
    """Normalised internal view of "one asset to materialise"."""

    asset_name: str
    target_path: Path
    expected_filename: str
    expected_size: Optional[int] = None
    expected_sha256: Optional[str] = None


class AssetInjector:
    """Zero-copy, idempotent, Windows-safe asset materialiser.

    The caller (``IdempotentSurgeon``) supplies:

    * ``target_path`` — the absolute location ComfyUI expects to find the
      file (e.g. ``<COMFY>/models/controlnet/v3_sd15_sparsectrl_rgb.ckpt``).
    * ``expected_filename`` / ``expected_size`` / ``expected_sha256`` —
      fingerprint metadata used to recognise a valid cached copy.

    The injector will, in order:

    1. Confirm the target already matches (``ALREADY_SATISFIED``, microseconds).
    2. Scan configured cache roots for a matching file and try
       ``symlink → hardlink → copy`` until one succeeds (``INJECTED``).
    3. Report ``CACHE_MISS`` so the surgeon can dispatch to
       ``AtomicDownloader``.
    """

    def __init__(
        self,
        *,
        extra_cache_roots: Sequence[str | os.PathLike[str]] = (),
        allow_copy_fallback: bool = True,
        fingerprint_sample_bytes: Optional[int] = None,
        clock: Optional[callable] = None,
        platform_name: str | None = None,
        large_file_copy_threshold_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self._roots: tuple[Path, ...] = tuple(
            _expand(r) for r in (*DEFAULT_CACHE_ROOTS, *extra_cache_roots)
        )
        self._allow_copy = bool(allow_copy_fallback)
        self._fingerprint_sample = fingerprint_sample_bytes
        self._clock = clock or time.time
        self._platform = platform_name or sys.platform
        self._large_file_copy_threshold_bytes = int(large_file_copy_threshold_bytes)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def inject(
        self,
        *,
        asset_name: str,
        target_path: str | os.PathLike[str],
        expected_filename: Optional[str] = None,
        expected_size: Optional[int] = None,
        expected_sha256: Optional[str] = None,
        allow_large_copy_without_prompt: bool = False,
    ) -> InjectionOutcome:
        """Materialise *one* asset into ``target_path`` using cache reuse."""

        start = self._clock()
        target = _expand(target_path)
        spec = _InjectionSpec(
            asset_name=asset_name,
            target_path=target,
            expected_filename=expected_filename or target.name,
            expected_size=expected_size,
            expected_sha256=expected_sha256.lower() if expected_sha256 else None,
        )

        # ---- Step 1: idempotent short-circuit --------------------------
        if self._target_already_valid(spec):
            return InjectionOutcome(
                asset_name=asset_name,
                target_path=str(target),
                status=InjectionStatus.ALREADY_SATISFIED,
                method=InjectionMethod.NONE,
                source_path=str(_resolve_noraise(target)),
                quarantined_backup=None,
                bytes_reused=_safe_size(target) or 0,
                elapsed_ms=(self._clock() - start) * 1000.0,
                notes=("target already satisfies fingerprint",),
            )

        # ---- Step 2: scan caches ---------------------------------------
        source = self._find_cached_source(spec)
        if source is None:
            return InjectionOutcome(
                asset_name=asset_name,
                target_path=str(target),
                status=InjectionStatus.CACHE_MISS,
                method=InjectionMethod.NONE,
                source_path=None,
                quarantined_backup=None,
                bytes_reused=0,
                elapsed_ms=(self._clock() - start) * 1000.0,
                notes=(f"no cached match across {len(self._roots)} roots",),
            )

        # ---- Step 3: quarantine any bad pre-existing target ------------
        quarantined: Optional[str] = None
        if target.exists() or target.is_symlink():
            quarantined = self._quarantine_conflict(target)

        # ---- Step 4: three-tier link fallback --------------------------
        method, notes, err = self._inject_with_fallback(
            source,
            target,
            asset_name=asset_name,
            allow_large_copy_without_prompt=allow_large_copy_without_prompt,
        )
        elapsed_ms = (self._clock() - start) * 1000.0
        if method is InjectionMethod.NONE:
            return InjectionOutcome(
                asset_name=asset_name,
                target_path=str(target),
                status=InjectionStatus.FAILED,
                method=InjectionMethod.NONE,
                source_path=str(source),
                quarantined_backup=quarantined,
                bytes_reused=0,
                elapsed_ms=elapsed_ms,
                notes=tuple(notes),
                error=err,
            )
        return InjectionOutcome(
            asset_name=asset_name,
            target_path=str(target),
            status=InjectionStatus.INJECTED,
            method=method,
            source_path=str(source),
            quarantined_backup=quarantined,
            bytes_reused=_safe_size(source) or 0,
            elapsed_ms=elapsed_ms,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------------
    # Fingerprint-aware target validation
    # ------------------------------------------------------------------

    def _target_already_valid(self, spec: _InjectionSpec) -> bool:
        """Returns True iff ``spec.target_path`` satisfies the fingerprint.

        This is the *idempotency gate*: it MUST be cheap (no SHA-256 on
        giant files unless the caller explicitly asked for it) and MUST NOT
        make any FS mutation.
        """

        t = spec.target_path
        try:
            if not t.exists():
                return False
        except OSError:
            return False

        # Symlink dangling? Treat as invalid.
        try:
            real = t.resolve(strict=True)
        except OSError:
            return False
        if not real.is_file():
            return False

        if spec.expected_size is not None:
            actual = _safe_size(real)
            if actual is None or actual != spec.expected_size:
                return False

        if spec.expected_sha256 is not None:
            # Only hash if the caller *opted in* by providing a SHA;
            # otherwise size alone is the idempotency contract.
            actual_sha = _safe_sha256(real, max_bytes=None)
            if actual_sha != spec.expected_sha256:
                return False

        return True

    # ------------------------------------------------------------------
    # Cache scanner
    # ------------------------------------------------------------------

    def _find_cached_source(self, spec: _InjectionSpec) -> Optional[Path]:
        """Scan configured caches for a file matching the spec fingerprint.

        Match priorities (first win):
        (1) identical SHA-256 if caller provided one,
        (2) identical filename + identical size,
        (3) identical filename alone (last-resort, guard only by size when
            the caller didn't supply size either).
        """

        wanted_name = spec.expected_filename
        wanted_size = spec.expected_size
        wanted_sha = spec.expected_sha256

        # Early exit if no roots exist on this machine.
        live_roots = [r for r in self._roots if self._safe_isdir(r)]
        if not live_roots:
            return None

        name_match: Optional[Path] = None
        size_match: Optional[Path] = None

        for root in live_roots:
            try:
                for candidate in self._iter_candidate_files(root, wanted_name):
                    # Dead-symlink defence.
                    try:
                        real = candidate.resolve(strict=True)
                    except OSError:
                        continue
                    if not real.is_file():
                        continue

                    if wanted_sha is not None:
                        actual_sha = _safe_sha256(
                            real, max_bytes=self._fingerprint_sample
                        )
                        if actual_sha == wanted_sha:
                            return real

                    if wanted_size is not None:
                        sz = _safe_size(real)
                        if sz == wanted_size and size_match is None:
                            size_match = real

                    if name_match is None and real.name == wanted_name:
                        name_match = real
            except OSError as exc:
                logger.debug(
                    "asset_injector: scanning %s raised %s", root, exc
                )
                continue

        if size_match is not None:
            return size_match
        if name_match is not None and wanted_size is None and wanted_sha is None:
            return name_match
        return None

    @staticmethod
    def _safe_isdir(p: Path) -> bool:
        try:
            return p.is_dir()
        except OSError:
            return False

    @staticmethod
    def _iter_candidate_files(root: Path, wanted_name: str) -> Iterable[Path]:
        """Defensive ``rglob``: swallow permission errors per-directory.

        Standard ``Path.rglob`` aborts on the first ``PermissionError``.
        Power users on Windows hit this constantly. We therefore hand-roll
        a BFS that silently skips inaccessible branches.
        """

        stack: list[Path] = [root]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except (OSError, PermissionError):
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        # Yield symlinks; resolve happens at the call site.
                        yield entry
                    elif entry.is_dir():
                        stack.append(entry)
                    elif entry.is_file():
                        if entry.name == wanted_name:
                            yield entry
                except (OSError, PermissionError):
                    continue

    # ------------------------------------------------------------------
    # Three-tier link fallback
    # ------------------------------------------------------------------

    def _inject_with_fallback(
        self,
        source: Path,
        target: Path,
        *,
        asset_name: str,
        allow_large_copy_without_prompt: bool,
    ) -> tuple[InjectionMethod, list[str], Optional[str]]:
        """Try symlink → hardlink → copy, in that order.

        Returns ``(method, notes, error_string_or_None)``. The ``method``
        is :class:`InjectionMethod.NONE` iff every tier failed.
        """

        notes: list[str] = []
        target.parent.mkdir(parents=True, exist_ok=True)

        # -- Tier 1: symlink -------------------------------------------
        try:
            os.symlink(str(source), str(target))
            notes.append("tier1: symlink OK")
            return InjectionMethod.SYMLINK, notes, None
        except (OSError, NotImplementedError, AttributeError) as exc:
            # WinError 1314 = ERROR_PRIVILEGE_NOT_HELD; cross-FS; unsupported
            notes.append(f"tier1 symlink failed: {exc!r}")
            if self._should_require_manual_copy_confirmation(
                source,
                exc,
                allow_large_copy_without_prompt=allow_large_copy_without_prompt,
            ):
                raise symlink_manual_error(
                    asset_name=asset_name,
                    source_path=str(source),
                    target_path=str(target),
                    size_bytes=_safe_size(source) or 0,
                ) from exc
            # If a partially-created target exists, clean it up silently.
            self._remove_if_link_only(target)

        # -- Tier 2: hardlink ------------------------------------------
        try:
            os.link(str(source), str(target))
            notes.append("tier2: hardlink OK")
            return InjectionMethod.HARDLINK, notes, None
        except (OSError, NotImplementedError, AttributeError) as exc:
            notes.append(f"tier2 hardlink failed: {exc!r}")
            self._remove_if_link_only(target)

        # -- Tier 3: physical copy -------------------------------------
        if not self._allow_copy:
            notes.append("tier3 copy disabled by allow_copy_fallback=False")
            return InjectionMethod.NONE, notes, "all_tiers_exhausted"
        try:
            shutil.copy2(str(source), str(target))
            notes.append("tier3: shutil.copy2 OK")
            return InjectionMethod.COPY, notes, None
        except OSError as exc:
            notes.append(f"tier3 copy failed: {exc!r}")
            return InjectionMethod.NONE, notes, repr(exc)

    @staticmethod
    def _remove_if_link_only(target: Path) -> None:
        """Remove ``target`` *only* if it is a symlink with no real content.

        This is narrowly scoped: we never ``unlink`` a regular file (that
        would violate R1 / no blind overwrite). Used between fallback
        tiers to clear a half-created symlink stub.
        """

        try:
            if target.is_symlink():
                os.unlink(str(target))
        except OSError as exc:
            logger.debug(
                "asset_injector: cannot clear stale symlink %s: %s",
                target, exc,
            )

    # ------------------------------------------------------------------
    # Quarantine (the anti-destructive guardrail)
    # ------------------------------------------------------------------

    def _quarantine_conflict(self, target: Path) -> Optional[str]:
        """Move a conflicting target aside to ``<target>.bak-<timestamp>``.

        We *never* delete user data. The path is renamed atomically so a
        crash mid-quarantine leaves either the original or the backup,
        never both-or-neither. If the target is itself a broken symlink,
        the ``unlink`` path is used (symlinks hold no user payload).
        """

        try:
            if target.is_symlink():
                os.unlink(str(target))
                return None
        except OSError:
            pass

        ts = int(self._clock() * 1000)
        backup = target.with_name(f"{target.name}.bak-{ts}")
        try:
            os.replace(str(target), str(backup))
            logger.info(
                "asset_injector: quarantined conflicting target %s -> %s",
                target, backup,
            )
            return str(backup)
        except OSError as exc:
            logger.warning(
                "asset_injector: quarantine failed for %s: %s", target, exc
            )
            return None

    # ------------------------------------------------------------------
    # Introspection helpers (used by tests and the surgeon)
    # ------------------------------------------------------------------

    @property
    def cache_roots(self) -> tuple[Path, ...]:
        return self._roots

    def _should_require_manual_copy_confirmation(
        self,
        source: Path,
        exc: BaseException,
        *,
        allow_large_copy_without_prompt: bool,
    ) -> bool:
        if allow_large_copy_without_prompt:
            return False
        if not str(self._platform).startswith("win"):
            return False
        if not is_windows_symlink_privilege_error(exc):
            return False
        size_bytes = _safe_size(source) or 0
        return size_bytes >= self._large_file_copy_threshold_bytes

    def describe(self) -> dict:
        return {
            "cache_roots": [str(r) for r in self._roots],
            "allow_copy_fallback": self._allow_copy,
            "fingerprint_sample_bytes": self._fingerprint_sample,
            "platform": self._platform,
            "large_file_copy_threshold_bytes": self._large_file_copy_threshold_bytes,
        }
