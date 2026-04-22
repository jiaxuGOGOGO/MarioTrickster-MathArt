"""Atomic Downloader — Resumable, Hash-Verified, Atomically-Published (SESSION-133).

P0-SESSION-130-IDEMPOTENT-SURGEON — Phase 2 (b)
==============================================

The :class:`AtomicDownloader` is the surgeon's second-line healer. It runs
only when :class:`mathart.workspace.asset_injector.AssetInjector` reports a
``CACHE_MISS``, and is responsible for safely fetching the missing payload
from the network.

Architectural anchors (binding; see ``docs/research/SESSION-133-SURGEON-RESEARCH.md``)
--------------------------------------------------------------------------------------
1. **aria2 control file discipline** —
   aria2 writes the in-flight data to ``<target>`` plus ``<target>.aria2``
   control file; completion is signaled by *deleting* the control file.
   We simplify this to a single ``<target>.part`` data file and a final
   atomic ``os.replace`` to ``<target>``. The *only* file ever visible at
   ``<target>`` is a fully-verified artifact.
2. **HTTP Range requests (MDN / RFC 7233)** —
   When a prior ``.part`` exists, we send ``Range: bytes=<existing>-`` and
   accept ``206 Partial Content``. If the server replies ``200 OK`` (Range
   not honoured) we truncate and restart from offset 0 — but we NEVER
   silently append partial bytes onto a misaligned stream.
3. **POSIX ``rename()`` atomicity** —
   ``os.replace`` is guaranteed atomic on the same filesystem on both
   POSIX and Windows. This is the *only* operation that makes the artifact
   visible to the rest of the project.

Red lines
---------
- **R1 (no half-downloaded pollution)**: the final ``<target>`` path is
  *never* opened for writing. Every byte lands in ``<target>.part`` and
  is only promoted after a successful SHA-256 / size check.
- **R2 (corrupt quarantine)**: a failed hash check does NOT ``os.remove``
  the ``.part`` file; it renames it to ``<target>.part.corrupt-<ts>`` so
  the operator can diagnose (matches ``asset_injector`` behaviour).
- **R3 (idempotency)**: if the final target already exists and its
  fingerprint matches the expected size/SHA, return
  ``DownloadStatus.ALREADY_VERIFIED`` without opening a TCP socket.
- **R4 (no hidden side effects in tests)**: the transport is fully
  pluggable through ``transport`` so tests never hit the network.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol

from .hitl_boundary import is_timeout_boundary, network_timeout_manual_error

logger = logging.getLogger(__name__)

__all__ = [
    "DownloadStatus",
    "DownloadOutcome",
    "TransportResponse",
    "DownloadTransport",
    "AtomicDownloader",
    "UrllibTransport",
]


# ---------------------------------------------------------------------------
# Strongly-typed outcomes
# ---------------------------------------------------------------------------

class DownloadStatus(str, Enum):
    ALREADY_VERIFIED = "already_verified"     # idempotent no-op
    DOWNLOADED_FRESH = "downloaded_fresh"     # full download + atomic publish
    RESUMED_AND_VERIFIED = "resumed_and_verified"  # Range resume success
    HASH_MISMATCH = "hash_mismatch"           # bytes quarantined, raises later
    TRANSPORT_ERROR = "transport_error"       # network / IO failure
    REJECTED_NO_URL = "rejected_no_url"       # caller passed empty URL


@dataclass(frozen=True)
class DownloadOutcome:
    url: str
    target_path: str
    status: DownloadStatus
    bytes_written: int
    total_bytes: Optional[int]
    sha256: Optional[str]
    resumed_from: int
    elapsed_ms: float
    part_path: Optional[str]
    quarantined_part: Optional[str]
    notes: tuple[str, ...] = field(default_factory=tuple)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Transport abstraction (for dependency-injection in tests)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportResponse:
    """Minimal view of an HTTP response we need for resumable download."""

    status_code: int
    total_length: Optional[int]
    accepted_range_from: Optional[int]
    stream: Iterator[bytes]


class DownloadTransport(Protocol):
    """Pluggable transport. Real impl uses urllib; tests inject fakes."""

    def open(self, url: str, *, offset: int = 0) -> TransportResponse: ...


class UrllibTransport:
    """Default transport based on ``urllib.request``.

    We use urllib rather than ``requests`` to keep zero extra dependencies
    on the surgeon path (``requests`` is already in ``dependencies`` for
    the main project, but this module should remain stdlib-only as a
    defence-in-depth measure: it must work even if the user's venv is
    partially broken).
    """

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        chunk_size: int = 1 << 20,  # 1 MiB
        user_agent: str = "MarioTrickster-MathArt/AtomicDownloader/1.0",
    ) -> None:
        self._timeout = timeout
        self._chunk_size = chunk_size
        self._ua = user_agent

    def open(self, url: str, *, offset: int = 0) -> TransportResponse:
        headers = {"User-Agent": self._ua}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=self._timeout)
        code = getattr(resp, "status", None) or resp.getcode()

        # Content-Length semantics depend on whether Range was honoured.
        cl_header = resp.headers.get("Content-Length")
        try:
            content_length = int(cl_header) if cl_header is not None else None
        except ValueError:
            content_length = None

        accepted_from: Optional[int]
        if code == 206:
            accepted_from = offset
            # 206 Content-Length is *the remainder*, not total. We reconstruct
            # total length from Content-Range if present.
            cr = resp.headers.get("Content-Range")
            total: Optional[int] = None
            if cr and "/" in cr:
                tail = cr.rsplit("/", 1)[1]
                if tail.isdigit():
                    total = int(tail)
            total_length = total if total is not None else (
                (content_length + offset) if content_length is not None else None
            )
        else:
            accepted_from = 0 if code == 200 else None
            total_length = content_length

        chunk_size = self._chunk_size

        def _iter() -> Iterator[bytes]:
            try:
                while True:
                    buf = resp.read(chunk_size)
                    if not buf:
                        break
                    yield buf
            finally:
                try:
                    resp.close()
                except Exception:  # pragma: no cover - best-effort close
                    pass

        return TransportResponse(
            status_code=int(code),
            total_length=total_length,
            accepted_range_from=accepted_from,
            stream=_iter(),
        )


# ---------------------------------------------------------------------------
# Atomic Downloader
# ---------------------------------------------------------------------------


class AtomicDownloader:
    """Resumable, hash-verified, atomically-published downloader.

    Parameters
    ----------
    transport:
        Object implementing :class:`DownloadTransport`. Defaults to
        :class:`UrllibTransport`. Tests inject in-memory fakes.
    clock:
        Time source; defaults to ``time.time``.
    chunk_size:
        How many bytes to append to the ``.part`` between fsync hints.
    max_retries:
        Number of transparent retry attempts when the stream drops.
    backoff_seconds:
        Base sleep between retries (exponential).
    """

    def __init__(
        self,
        *,
        transport: Optional[DownloadTransport] = None,
        clock: Optional[Callable[[], float]] = None,
        chunk_size: int = 1 << 20,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        self._transport = transport or UrllibTransport(chunk_size=chunk_size)
        self._clock = clock or time.time
        self._chunk_size = chunk_size
        self._max_retries = max_retries
        self._backoff = backoff_seconds

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def fetch(
        self,
        *,
        url: str,
        target_path: str | os.PathLike[str],
        expected_size: Optional[int] = None,
        expected_sha256: Optional[str] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> DownloadOutcome:
        """Fetch ``url`` into ``target_path`` with the full safety contract."""

        started = self._clock()
        target = Path(os.path.expanduser(os.path.expandvars(str(target_path))))
        part = target.with_name(target.name + ".part")

        if not url:
            return DownloadOutcome(
                url=url,
                target_path=str(target),
                status=DownloadStatus.REJECTED_NO_URL,
                bytes_written=0,
                total_bytes=None,
                sha256=None,
                resumed_from=0,
                elapsed_ms=(self._clock() - started) * 1000.0,
                part_path=str(part),
                quarantined_part=None,
                error="empty url",
            )

        target.parent.mkdir(parents=True, exist_ok=True)

        # ---- Step 0: idempotent short-circuit ---------------------------
        if target.is_file() and self._matches_fingerprint(
            target, expected_size, expected_sha256
        ):
            return DownloadOutcome(
                url=url,
                target_path=str(target),
                status=DownloadStatus.ALREADY_VERIFIED,
                bytes_written=0,
                total_bytes=_safe_size(target),
                sha256=expected_sha256,
                resumed_from=0,
                elapsed_ms=(self._clock() - started) * 1000.0,
                part_path=None,
                quarantined_part=None,
                notes=("target already matches fingerprint — no socket opened",),
            )

        sleeper = sleep or time.sleep

        # ---- Step 1: resumable streaming ---------------------------------
        last_error: Optional[str] = None
        last_exception: BaseException | None = None
        for attempt in range(1, self._max_retries + 1):
            offset = part.stat().st_size if part.exists() else 0
            try:
                response = self._transport.open(url, offset=offset)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_exception = exc
                last_error = f"open failed (attempt {attempt}): {exc!r}"
                logger.warning("atomic_downloader: %s", last_error)
                sleeper(self._backoff * attempt)
                continue

            try:
                bytes_written, resumed_from = self._stream_to_part(
                    response=response, part=part, prior_offset=offset
                )
            except (OSError, TimeoutError) as exc:
                last_exception = exc
                last_error = f"stream failed (attempt {attempt}): {exc!r}"
                logger.warning("atomic_downloader: %s", last_error)
                sleeper(self._backoff * attempt)
                continue

            # ---- Step 2: verify -----------------------------------------
            verified, actual_sha = self._verify(
                part, expected_size, expected_sha256
            )
            if not verified:
                quarantined = self._quarantine_corrupt_part(part)
                return DownloadOutcome(
                    url=url,
                    target_path=str(target),
                    status=DownloadStatus.HASH_MISMATCH,
                    bytes_written=bytes_written,
                    total_bytes=response.total_length,
                    sha256=actual_sha,
                    resumed_from=resumed_from,
                    elapsed_ms=(self._clock() - started) * 1000.0,
                    part_path=str(part),
                    quarantined_part=quarantined,
                    error="fingerprint mismatch",
                )

            # ---- Step 3: atomic publish ---------------------------------
            try:
                os.replace(str(part), str(target))
            except OSError as exc:
                last_error = f"atomic rename failed: {exc!r}"
                logger.warning("atomic_downloader: %s", last_error)
                sleeper(self._backoff * attempt)
                continue

            status = (
                DownloadStatus.RESUMED_AND_VERIFIED
                if resumed_from > 0
                else DownloadStatus.DOWNLOADED_FRESH
            )
            return DownloadOutcome(
                url=url,
                target_path=str(target),
                status=status,
                bytes_written=bytes_written,
                total_bytes=response.total_length,
                sha256=actual_sha,
                resumed_from=resumed_from,
                elapsed_ms=(self._clock() - started) * 1000.0,
                part_path=None,
                quarantined_part=None,
                notes=(f"atomic publish via os.replace after attempt {attempt}",),
            )

        if is_timeout_boundary(last_exception):
            raise network_timeout_manual_error(
                url=url,
                target_path=str(target),
                attempts=self._max_retries,
                last_error=last_error or "unknown timeout transport error",
            ) from last_exception

        return DownloadOutcome(
            url=url,
            target_path=str(target),
            status=DownloadStatus.TRANSPORT_ERROR,
            bytes_written=0,
            total_bytes=None,
            sha256=None,
            resumed_from=0,
            elapsed_ms=(self._clock() - started) * 1000.0,
            part_path=str(part) if part.exists() else None,
            quarantined_part=None,
            error=last_error or "unknown transport error",
        )

    # ------------------------------------------------------------------
    # Streaming core
    # ------------------------------------------------------------------

    def _stream_to_part(
        self,
        *,
        response: TransportResponse,
        part: Path,
        prior_offset: int,
    ) -> tuple[int, int]:
        """Stream the response into ``part`` respecting Range semantics.

        Returns ``(bytes_written_this_call, offset_from_which_we_resumed)``.
        """

        if response.accepted_range_from is None:
            raise OSError(
                f"transport returned unexpected status {response.status_code}"
            )

        if response.status_code == 200 and prior_offset > 0:
            # Server ignored the Range header: we MUST restart from 0 to
            # avoid concatenating misaligned bytes onto the .part.
            logger.info(
                "atomic_downloader: server ignored Range; restarting from 0"
            )
            try:
                os.unlink(str(part))
            except OSError:
                pass
            prior_offset = 0

        mode = "ab" if (prior_offset > 0 and part.exists()) else "wb"
        bytes_written = 0
        with part.open(mode) as fh:
            for chunk in response.stream:
                if not chunk:
                    continue
                fh.write(chunk)
                bytes_written += len(chunk)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync is advisory — do not crash if unavailable (e.g. tmpfs).
                pass
        return bytes_written, prior_offset

    # ------------------------------------------------------------------
    # Verification & quarantine
    # ------------------------------------------------------------------

    def _verify(
        self,
        part: Path,
        expected_size: Optional[int],
        expected_sha256: Optional[str],
    ) -> tuple[bool, Optional[str]]:
        if not part.is_file():
            return False, None

        if expected_size is not None:
            actual_sz = _safe_size(part)
            if actual_sz != expected_size:
                logger.warning(
                    "atomic_downloader: size mismatch expected=%s actual=%s",
                    expected_size, actual_sz,
                )
                return False, None

        if expected_sha256 is None:
            # Size-only contract honoured; skip the SHA for speed.
            return True, None

        actual_sha = _streaming_sha256(part)
        if actual_sha is None:
            return False, None
        if actual_sha.lower() != expected_sha256.lower():
            logger.warning(
                "atomic_downloader: sha mismatch expected=%s actual=%s",
                expected_sha256, actual_sha,
            )
            return False, actual_sha
        return True, actual_sha

    def _quarantine_corrupt_part(self, part: Path) -> Optional[str]:
        if not part.exists():
            return None
        ts = int(self._clock() * 1000)
        quarantine = part.with_name(f"{part.name}.corrupt-{ts}")
        try:
            os.replace(str(part), str(quarantine))
            logger.info(
                "atomic_downloader: quarantined corrupt part %s -> %s",
                part, quarantine,
            )
            return str(quarantine)
        except OSError as exc:
            logger.warning(
                "atomic_downloader: cannot quarantine corrupt part %s: %s",
                part, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Fingerprint helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_fingerprint(
        target: Path,
        expected_size: Optional[int],
        expected_sha256: Optional[str],
    ) -> bool:
        if expected_size is not None and _safe_size(target) != expected_size:
            return False
        if expected_sha256 is not None:
            actual = _streaming_sha256(target)
            if actual is None or actual.lower() != expected_sha256.lower():
                return False
        # Fingerprint wholly unspecified: a pre-existing target is assumed
        # valid (prevents re-downloading user-supplied blobs).
        return True


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_size(p: Path) -> Optional[int]:
    try:
        return p.stat().st_size
    except OSError:
        return None


def _streaming_sha256(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
