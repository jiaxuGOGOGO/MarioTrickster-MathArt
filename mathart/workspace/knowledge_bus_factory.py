"""Project-level factory for ``RuntimeDistillationBus`` wiring.

SESSION-147 — Knowledge Bus Wiring (大一统知识总线物理贯通).

Rationale
---------
Even though ``DirectorIntentParser`` has supported dependency injection of a
``RuntimeDistillationBus`` since SESSION-140, several top-level wizard
entrypoints (interactive CLI wizard and :class:`DirectorStudioStrategy`
non-interactive path) used to construct the parser *without* passing a bus
instance.  The runtime log therefore emitted:

    DEBUG | mathart.workspace.director_intent |
        No knowledge bus injected — using heuristic fallback only

which proves that the "大一统知识" (unified knowledge bus) was physically
disconnected from the Director Studio workflow — the system silently
degraded to heuristic-only translation.

This module provides a single, idempotent factory that:

1. Instantiates a project-scoped ``RuntimeDistillationBus`` rooted at the
   workspace (so it reads the actual ``knowledge/`` directory shipped with
   the repo, which currently compiles to **18 modules / 323 constraints**).
2. Eagerly calls ``refresh_from_knowledge()`` so the bus is hot-ready before
   any ``DirectorIntentParser`` query arrives.
3. Gracefully degrades to ``None`` (legacy behaviour) on any unexpected
   error, guaranteeing that knowledge-bus wiring NEVER becomes a new crash
   vector for the wizard.

Every top-level Director Studio route — both interactive (``cli_wizard``)
and non-interactive (``mode_dispatcher``) — MUST consume this factory to
resolve the bus before constructing ``DirectorIntentParser`` /
``InteractivePreviewGate``.  That way, the blackbox will record:

    INFO | mathart.workspace.knowledge_bus_factory |
        Knowledge bus activated: <N> modules, <K> constraints

instead of the "No knowledge bus injected" degradation warning.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover — typing only
    from mathart.distill.runtime_bus import RuntimeDistillationBus

logger = logging.getLogger(__name__)


def build_project_knowledge_bus(
    project_root: Path | str | None = None,
    *,
    backend_preference: tuple[str, ...] = ("numba", "python"),
    verbose: bool = False,
) -> Optional["RuntimeDistillationBus"]:
    """Instantiate the project-level ``RuntimeDistillationBus`` and eagerly
    refresh it from the local ``knowledge/`` directory.

    Parameters
    ----------
    project_root:
        Project root containing the ``knowledge/`` directory.  When ``None``,
        falls back to :func:`Path.cwd`.
    backend_preference:
        Runtime backend preference passed straight through to the bus.
    verbose:
        Propagated to the bus for optional trace logging.

    Returns
    -------
    RuntimeDistillationBus | None
        A hot-refreshed bus on success, or ``None`` when the bus could not be
        constructed (missing knowledge dir, runtime import failure, etc.).
        Returning ``None`` preserves the legacy graceful-degradation contract
        of :class:`mathart.workspace.director_intent.DirectorIntentParser`.
    """
    root = Path(project_root or Path.cwd()).resolve()
    try:
        # Lazy import to keep wizard startup light when the bus is never needed.
        from mathart.distill.runtime_bus import RuntimeDistillationBus
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "[KnowledgeBus] Failed to import RuntimeDistillationBus — "
            "DirectorIntent will degrade to heuristic fallback.",
            exc_info=True,
        )
        return None

    try:
        bus = RuntimeDistillationBus(
            project_root=root,
            backend_preference=backend_preference,
            verbose=verbose,
        )
        summary = bus.refresh_from_knowledge()
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "[KnowledgeBus] RuntimeDistillationBus refresh FAILED — "
            "DirectorIntent will degrade to heuristic fallback.",
            exc_info=True,
        )
        return None

    module_count = int(summary.get("module_count", 0) or 0)
    constraint_count = int(summary.get("constraint_count", 0) or 0)
    knowledge_files = int(summary.get("knowledge_files", 0) or 0)

    if module_count == 0 and constraint_count == 0:
        logger.warning(
            "[KnowledgeBus] Bus built but yielded 0 compiled modules from %s "
            "(knowledge_files=%d) — DirectorIntent will run without "
            "knowledge-grounded clamps.",
            summary.get("knowledge_dir", str(root / "knowledge")),
            knowledge_files,
        )
        # Still return the (empty) bus so downstream consumers stop emitting
        # the "No knowledge bus injected" DEBUG warning.  An empty bus is the
        # explicit "knowledge vacuum" state, not a physical dis-connection.
        return bus

    logger.info(
        "[KnowledgeBus] Knowledge bus activated: %d modules, %d constraints "
        "(backend=%s, knowledge_files=%d, knowledge_dir=%s)",
        module_count,
        constraint_count,
        summary.get("backend", "python"),
        knowledge_files,
        summary.get("knowledge_dir", str(root / "knowledge")),
    )
    return bus


__all__ = ["build_project_knowledge_bus"]
