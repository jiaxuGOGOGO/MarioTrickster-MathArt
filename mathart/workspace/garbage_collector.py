"""Intelligent Garbage Collector & In-Flight Pruning Engine.

SESSION-141: P0-SESSION-138-SYSTEM-PURGE-AND-OBSERVABILITY

This module implements a **two-level** workspace cleanup system:

Level 1 — Cold GC (startup):
    Scans the workspace for stale artefacts older than a configurable TTL
    (default 7 days) and removes them.  Targets include ``.part`` download
    residues, ``temp/`` caches, and orphaned intermediate files.

Level 2 — Hot Pruning (in-flight):
    Provides a callable hook that evolution / retry loops invoke **after**
    the current generation's parameters have been safely extracted.  The
    hook physically deletes the previous generation's large image / video
    intermediates while preserving lightweight JSON gene records.

Safety red-lines enforced:
- **NEVER** touches ``knowledge/active/``, ``blueprints/``, ``outputs/``,
  or any file/directory whose path contains ``elite`` (case-insensitive).
- Hot pruning is gated by an explicit ``params_safe`` flag — the caller
  must confirm that next-gen parameters are in memory before deletion.
- All deletions are logged to the blackbox logger for full audit trail.

External research anchors:
- TTL-based cache eviction (Redis / Memcached patterns)
- Eager resource release in generational EA (Deb et al., NSGA-II)
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

__all__ = [
    "GarbageCollector",
    "GCConfig",
    "GCReport",
    "InFlightPruner",
    "PruneReport",
]

logger = logging.getLogger("mathart.gc")

# ---------------------------------------------------------------------------
# Protected paths — ABSOLUTE red-line: never delete these
# ---------------------------------------------------------------------------

_PROTECTED_FRAGMENTS: tuple[str, ...] = (
    "knowledge/active",
    "knowledge\\active",
    "blueprints",
    "outputs",
    "elite",
)


def _is_protected(path: Path) -> bool:
    """Return True if *path* must never be deleted."""
    lower = str(path).lower()
    return any(frag in lower for frag in _PROTECTED_FRAGMENTS)


# ---------------------------------------------------------------------------
# Cold GC — startup-time workspace sweep
# ---------------------------------------------------------------------------

@dataclass
class GCConfig:
    """Configuration for the cold garbage collector."""

    ttl_days: int = 7
    """Maximum age (in days) before a stale artefact is eligible for removal."""

    target_extensions: tuple[str, ...] = (".part", ".tmp", ".bak")
    """File extensions that are always eligible regardless of location."""

    target_dirs: tuple[str, ...] = ("temp", "tmp", "__pycache__")
    """Directory basenames whose contents are swept if older than TTL."""

    dry_run: bool = False
    """If True, report what *would* be deleted without actually removing."""


@dataclass
class GCReport:
    """Summary of a cold-GC sweep."""

    files_deleted: int = 0
    dirs_deleted: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    protected_skips: int = 0


class GarbageCollector:
    """Level-1 cold garbage collector — runs at system startup."""

    def __init__(
        self,
        project_root: Path,
        config: Optional[GCConfig] = None,
    ) -> None:
        self.root = project_root
        self.cfg = config or GCConfig()
        self._cutoff = time.time() - self.cfg.ttl_days * 86400

    # ------------------------------------------------------------------ #

    def sweep(self) -> GCReport:
        """Execute a full workspace sweep and return a report."""
        report = GCReport()
        logger.info(
            "Cold GC sweep started — root=%s, ttl=%d days, dry_run=%s",
            self.root,
            self.cfg.ttl_days,
            self.cfg.dry_run,
        )

        # 1. Stale files by extension
        for ext in self.cfg.target_extensions:
            for p in self.root.rglob(f"*{ext}"):
                self._try_remove_file(p, report)

        # 2. Stale directories
        for dirname in self.cfg.target_dirs:
            for d in self.root.rglob(dirname):
                if d.is_dir():
                    self._try_remove_dir(d, report)

        # 3. Stale files inside temp/ at project root
        temp_dir = self.root / "temp"
        if temp_dir.is_dir():
            for p in temp_dir.iterdir():
                if p.is_file():
                    self._try_remove_file(p, report)
                elif p.is_dir():
                    self._try_remove_dir(p, report)

        logger.info(
            "Cold GC sweep complete — files=%d, dirs=%d, freed=%d bytes, errors=%d, skips=%d",
            report.files_deleted,
            report.dirs_deleted,
            report.bytes_freed,
            len(report.errors),
            report.protected_skips,
        )
        return report

    # ------------------------------------------------------------------ #

    def _is_stale(self, path: Path) -> bool:
        try:
            return os.stat(path).st_mtime < self._cutoff
        except OSError:
            return False

    def _try_remove_file(self, path: Path, report: GCReport) -> None:
        if _is_protected(path):
            report.protected_skips += 1
            return
        if not self._is_stale(path):
            return
        try:
            size = path.stat().st_size
            if not self.cfg.dry_run:
                path.unlink(missing_ok=True)
            report.files_deleted += 1
            report.bytes_freed += size
            logger.debug("GC removed file: %s (%d bytes)", path, size)
        except OSError as exc:
            report.errors.append(f"file {path}: {exc}")
            logger.warning("GC failed to remove file %s: %s", path, exc)

    def _try_remove_dir(self, path: Path, report: GCReport) -> None:
        if _is_protected(path):
            report.protected_skips += 1
            return
        if not self._is_stale(path):
            return
        try:
            size = sum(
                f.stat().st_size for f in path.rglob("*") if f.is_file()
            )
            if not self.cfg.dry_run:
                shutil.rmtree(path, ignore_errors=True)
            report.dirs_deleted += 1
            report.bytes_freed += size
            logger.debug("GC removed dir: %s (%d bytes)", path, size)
        except OSError as exc:
            report.errors.append(f"dir {path}: {exc}")
            logger.warning("GC failed to remove dir %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Hot Pruning — in-flight generational waste removal
# ---------------------------------------------------------------------------

@dataclass
class PruneReport:
    """Summary of a single hot-prune invocation."""

    files_deleted: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    protected_skips: int = 0


class InFlightPruner:
    """Level-2 hot pruner — invoked inside evolution / retry loops.

    Usage::

        pruner = InFlightPruner(project_root)

        for generation in range(max_gen):
            # ... run generation, extract params into next_gen_params ...
            params_safe = next_gen_params is not None
            report = pruner.prune_generation_waste(
                waste_paths=[old_image_path, old_cache_dir],
                params_safe=params_safe,
            )

    The ``params_safe`` flag is the **temporal safety gate** (Red Line #3):
    deletion only proceeds when the caller confirms that the next generation's
    parameters have been safely captured in memory.
    """

    # File extensions considered "large waste" eligible for hot pruning
    WASTE_EXTENSIONS: frozenset[str] = frozenset({
        ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff",
        ".mp4", ".avi", ".mov", ".gif",
        ".part", ".tmp", ".bak",
    })

    # Extensions that are ALWAYS preserved (lightweight gene records)
    PRESERVE_EXTENSIONS: frozenset[str] = frozenset({
        ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".py",
    })

    def __init__(self, project_root: Path) -> None:
        self.root = project_root

    def prune_generation_waste(
        self,
        waste_paths: List[Path],
        *,
        params_safe: bool,
    ) -> PruneReport:
        """Delete large intermediates from the previous generation.

        Parameters
        ----------
        waste_paths : list[Path]
            Explicit list of files / directories to remove.
        params_safe : bool
            **Must be True** for deletion to proceed.  This is the temporal
            safety gate ensuring next-gen parameters are already in memory.

        Returns
        -------
        PruneReport
        """
        report = PruneReport()

        if not params_safe:
            logger.warning(
                "Hot prune ABORTED — params_safe=False (temporal safety gate)"
            )
            return report

        for p in waste_paths:
            p = Path(p)
            if _is_protected(p):
                report.protected_skips += 1
                logger.debug("Hot prune skipped protected path: %s", p)
                continue

            try:
                if p.is_file():
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    report.files_deleted += 1
                    report.bytes_freed += size
                    logger.debug("Hot prune removed file: %s (%d bytes)", p, size)
                elif p.is_dir():
                    size = sum(
                        f.stat().st_size for f in p.rglob("*") if f.is_file()
                    )
                    shutil.rmtree(p, ignore_errors=True)
                    report.files_deleted += 1
                    report.bytes_freed += size
                    logger.debug("Hot prune removed dir: %s (%d bytes)", p, size)
            except OSError as exc:
                report.errors.append(f"{p}: {exc}")
                logger.warning("Hot prune error on %s: %s", p, exc)

        if report.files_deleted > 0:
            logger.info(
                "Hot prune complete — deleted=%d, freed=%d bytes",
                report.files_deleted,
                report.bytes_freed,
            )
        return report

    def scan_and_prune_dir(
        self,
        directory: Path,
        *,
        params_safe: bool,
        keep_json: bool = True,
    ) -> PruneReport:
        """Scan a directory and prune large waste files, keeping gene records.

        This is a convenience method for evolution loops that dump all
        intermediates into a single generation directory.

        Parameters
        ----------
        directory : Path
            The generation's working directory.
        params_safe : bool
            Temporal safety gate.
        keep_json : bool
            If True (default), ``.json`` and other lightweight files are kept.
        """
        report = PruneReport()

        if not params_safe:
            logger.warning(
                "Hot prune scan ABORTED — params_safe=False"
            )
            return report

        if not directory.is_dir():
            return report

        waste: list[Path] = []
        for f in directory.rglob("*"):
            if not f.is_file():
                continue
            if _is_protected(f):
                report.protected_skips += 1
                continue
            ext = f.suffix.lower()
            if keep_json and ext in self.PRESERVE_EXTENSIONS:
                continue
            if ext in self.WASTE_EXTENSIONS:
                waste.append(f)

        sub = self.prune_generation_waste(waste, params_safe=True)
        report.files_deleted += sub.files_deleted
        report.bytes_freed += sub.bytes_freed
        report.errors.extend(sub.errors)
        report.protected_skips += sub.protected_skips
        return report
