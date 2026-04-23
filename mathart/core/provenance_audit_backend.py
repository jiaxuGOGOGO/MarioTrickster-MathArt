"""SESSION-152 — Provenance Audit Backend (Registry-Native Plugin).

P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT

This module implements the audit backend as a first-class ``@register_backend``
plugin.  It is auto-discovered by the ``BackendRegistry`` at import time and
can be invoked through the standard pipeline dispatch mechanism.

Architecture discipline:
- **Registry Pattern**: Self-registers via ``@register_backend`` decorator,
  no trunk code modification required.
- **Sidecar Pattern**: Reads pipeline state but NEVER modifies any computation.
- **Zero-Overhead**: When not invoked, the backend has zero runtime cost.
  When invoked, it produces the audit report as a side-effect artifact.

The backend's ``execute()`` method:
1. Snapshots the knowledge bus state.
2. Traces parameter derivation from the intent spec.
3. Detects dangling parameters.
4. Generates the terminal audit report + JSON log.

This is the "last mile" of the provenance audit — it runs at the end of the
pipeline to produce the final Knowledge Mapping Audit Trail.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .backend_registry import register_backend, BackendCapability
from .backend_types import BackendType
from .provenance_tracker import (
    KnowledgeLineageTracker,
    ProvenanceAuditContext,
    ProvenanceSourceType,
)
from .provenance_report import ProvenanceReportGenerator

if TYPE_CHECKING:
    from ..distill.runtime_bus import RuntimeDistillationBus
    from ..workspace.director_intent import CreatorIntentSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit Artifact
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceAuditArtifact:
    """Output artifact from the provenance audit backend.

    Contains the full audit context, the JSON log path, and summary metrics.
    """
    run_id: str = ""
    session_id: str = ""
    timestamp: str = ""
    json_log_path: str = ""
    total_params: int = 0
    knowledge_driven_count: int = 0
    heuristic_fallback_count: int = 0
    dangling_count: int = 0
    health_verdict: str = ""
    dead_zone_params: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "json_log_path": self.json_log_path,
            "total_params": self.total_params,
            "knowledge_driven_count": self.knowledge_driven_count,
            "heuristic_fallback_count": self.heuristic_fallback_count,
            "dangling_count": self.dangling_count,
            "health_verdict": self.health_verdict,
            "dead_zone_params": self.dead_zone_params,
        }


# ---------------------------------------------------------------------------
# Registered Backend
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.PROVENANCE_AUDIT,
    display_name="Knowledge Provenance Audit (知识血统溯源审计)",
    artifact_families=("provenance_audit_report", "knowledge_audit_json"),
    capabilities=(BackendCapability.KNOWLEDGE_DISTILL,),
    input_requirements=(),
    dependencies=(),
    author="MarioTrickster-MathArt",
    session_origin="SESSION-152",
)
class ProvenanceAuditBackend:
    """Non-intrusive sidecar backend that produces a full-chain knowledge
    provenance audit report.

    This backend is designed to be invoked at the END of any pipeline run
    (or standalone in dry-run mode) to audit the knowledge lineage of all
    parameters that flowed through the system.

    It reads:
    - The RuntimeDistillationBus (knowledge state)
    - The CreatorIntentSpec (parameter derivation)
    - The pipeline execution context (backend consumption)

    It produces:
    - Terminal-printed audit table
    - ``logs/knowledge_audit_trace.json``
    - ``ProvenanceAuditArtifact`` summary

    **[防破坏红线]**: This backend NEVER modifies any pipeline state.
    **[防假账红线]**: All provenance is derived from actual system state.
    """

    def __init__(self, project_root: Optional[str | Path] = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()

    def execute(
        self,
        *,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
        intent_spec: Optional["CreatorIntentSpec"] = None,
        genotype_flat: Optional[Dict[str, float]] = None,
        raw_vibe: str = "",
        vibe_adjustments: Optional[Dict[str, Dict[str, float]]] = None,
        user_overrides: Optional[Dict[str, float]] = None,
        base_blueprint_path: str = "",
        backend_consumed_params: Optional[Dict[str, float]] = None,
        backend_name: str = "",
        output_fn: Optional[callable] = None,
        session_id: str = "SESSION-152",
    ) -> ProvenanceAuditArtifact:
        """Execute the provenance audit.

        Parameters
        ----------
        knowledge_bus : optional
            The RuntimeDistillationBus instance (for knowledge state snapshot).
        intent_spec : optional
            The CreatorIntentSpec from the intent parser (for provenance records).
        genotype_flat : optional
            Flat parameter dict from the resolved genotype.  If not provided,
            extracted from ``intent_spec.genotype.flat_params()``.
        raw_vibe : str
            The original vibe string from the intent declaration.
        vibe_adjustments : optional
            Map of vibe_token → {param_key: delta} from SEMANTIC_VIBE_MAP.
        user_overrides : optional
            Explicit user overrides from the intent declaration.
        base_blueprint_path : str
            Path to the base blueprint (if any).
        backend_consumed_params : optional
            Parameters that were consumed by a downstream backend.
        backend_name : str
            Name of the downstream backend that consumed parameters.
        output_fn : optional
            Custom print function (for testing/capture).
        session_id : str
            Session identifier for the audit context.

        Returns
        -------
        ProvenanceAuditArtifact
            Summary artifact with audit results.
        """
        tracker = KnowledgeLineageTracker.instance()

        # Phase 1: Begin audit (snapshot knowledge state)
        ctx = tracker.begin_audit(
            knowledge_bus=knowledge_bus,
            session_id=session_id,
        )

        # Phase 2: Resolve genotype flat params
        if genotype_flat is None and intent_spec is not None:
            genotype_flat = intent_spec.genotype.flat_params()

        if genotype_flat is None:
            logger.warning(
                "[Provenance] No genotype flat params available — "
                "generating empty audit."
            )
            genotype_flat = {}

        # Extract applied knowledge rules from intent spec
        applied_rules = None
        if intent_spec is not None:
            applied_rules = intent_spec.applied_knowledge_rules
            if not raw_vibe:
                raw_vibe = intent_spec.raw_vibe
            if not base_blueprint_path:
                base_blueprint_path = intent_spec.base_blueprint_path

        # Phase 3: Trace parameter derivation
        tracker.trace_intent_derivation(
            genotype_flat,
            raw_vibe=raw_vibe,
            vibe_adjustments=vibe_adjustments,
            user_overrides=user_overrides,
            base_blueprint_path=base_blueprint_path,
            applied_knowledge_rules=applied_rules,
            knowledge_bus=knowledge_bus,
        )

        # Phase 4: Backend consumption checkpoint (if available)
        if backend_consumed_params is not None:
            tracker.checkpoint_backend(
                backend_name=backend_name or "unknown",
                consumed_params=backend_consumed_params,
            )

        # Phase 5: Finalize audit (dangling detection)
        ctx = tracker.finalize_audit()

        # Phase 6: Generate report
        report_gen = ProvenanceReportGenerator(
            project_root=self.project_root,
            output_fn=output_fn,
        )
        payload = report_gen.generate(context=ctx)

        # Build artifact
        artifact = ProvenanceAuditArtifact(
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            timestamp=ctx.start_time,
            json_log_path=str(
                self.project_root / "logs" / "knowledge_audit_trace.json"
            ),
            total_params=ctx.total_params,
            knowledge_driven_count=ctx.knowledge_driven_count,
            heuristic_fallback_count=ctx.heuristic_fallback_count,
            dangling_count=ctx.dangling_count,
            health_verdict=self._compute_verdict(ctx),
            dead_zone_params=[
                r.param_key
                for r in ctx.lineage_records.values()
                if r.source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value
            ],
        )

        logger.info(
            "[Provenance] Audit backend complete: %s, verdict=%s",
            artifact.run_id,
            artifact.health_verdict,
        )
        return artifact

    @staticmethod
    def _compute_verdict(ctx: ProvenanceAuditContext) -> str:
        """Compute the health verdict based on audit statistics."""
        if ctx.total_params == 0:
            return "N/A"
        ratio = ctx.knowledge_driven_count / ctx.total_params
        if ratio >= 0.5:
            return "HEALTHY"
        elif ratio >= 0.2:
            return "PARTIAL"
        else:
            return "CRITICAL"


# ---------------------------------------------------------------------------
# Standalone Audit Runner
# ---------------------------------------------------------------------------

def run_standalone_audit(
    project_root: Optional[str | Path] = None,
    vibe: str = "弹性 活泼",
    output_fn: Optional[callable] = None,
) -> ProvenanceAuditArtifact:
    """Run a standalone provenance audit (for CLI / dry-run mode).

    This function:
    1. Builds the knowledge bus from the project's knowledge directory.
    2. Parses a synthetic intent with the given vibe.
    3. Runs the full audit pipeline.
    4. Returns the audit artifact.

    Usage::

        python -m mathart.core.provenance_audit_backend
    """
    import sys
    project_root = Path(project_root) if project_root else Path.cwd()

    # Build knowledge bus
    try:
        sys.path.insert(0, str(project_root))
        from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus
        knowledge_bus = build_project_knowledge_bus(project_root=project_root)
    except Exception as e:
        logger.warning("[Provenance] Failed to build knowledge bus: %s", e)
        knowledge_bus = None

    # Build intent spec via DirectorIntentParser
    try:
        from mathart.workspace.director_intent import (
            DirectorIntentParser,
            SEMANTIC_VIBE_MAP,
        )
        parser = DirectorIntentParser(
            workspace_root=project_root,
            knowledge_bus=knowledge_bus,
        )
        spec = parser.parse_dict({
            "vibe": vibe,
            "description": "Provenance audit synthetic intent",
        })
    except Exception as e:
        logger.warning("[Provenance] Failed to parse intent: %s", e)
        spec = None

    # Reconstruct vibe adjustments for honest tracking
    vibe_adjustments: Dict[str, Dict[str, float]] = {}
    try:
        import re
        tokens = re.split(r"[,;，；\s的]+", vibe.strip().lower())
        for token in tokens:
            token = token.strip()
            if token and token in SEMANTIC_VIBE_MAP:
                vibe_adjustments[token] = dict(SEMANTIC_VIBE_MAP[token])
    except Exception:
        pass

    # Run audit
    backend = ProvenanceAuditBackend(project_root=project_root)
    artifact = backend.execute(
        knowledge_bus=knowledge_bus,
        intent_spec=spec,
        raw_vibe=vibe,
        vibe_adjustments=vibe_adjustments,
        output_fn=output_fn,
        session_id="SESSION-152",
    )

    return artifact


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project_root = Path(__file__).resolve().parents[2]
    vibe = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "弹性 活泼"

    print(f"\n[Provenance Audit] Project root: {project_root}")
    print(f"[Provenance Audit] Vibe: {vibe}\n")

    artifact = run_standalone_audit(
        project_root=project_root,
        vibe=vibe,
    )

    print(f"\n[Provenance Audit] Verdict: {artifact.health_verdict}")
    print(f"[Provenance Audit] Dead zones: {len(artifact.dead_zone_params)}")
    print(f"[Provenance Audit] JSON log: {artifact.json_log_path}")
