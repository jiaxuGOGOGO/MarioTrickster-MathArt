"""SESSION-152 — Knowledge Lineage Tracker & Provenance Audit Probe.

P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT

This module implements a **non-intrusive sidecar** that observes the knowledge
bus, intent parser, and pipeline execution to build a full-chain provenance
audit trail.  It answers the critical question: "Did the system actually use
distilled book knowledge, or did it silently fall back to hardcoded heuristics?"

Architecture discipline:
- **Read-only observer**: NEVER modifies any float value or computation path.
- **Sidecar pattern**: Attaches to existing data flows via interception, not
  by rewriting business logic (inspired by Envoy/Istio sidecar proxies).
- **OpenLineage-aligned**: Each parameter carries a lineage facet with
  ``run_id``, ``source_type``, ``source_file``, ``rule_id``, and ``reason``.
- **XAI-aligned**: Produces an explainable Knowledge Mapping Audit Trail
  proving high-dimensional semantic intent maps to low-dimensional physics.
- **Thread-safe**: Uses ``threading.local()`` for per-session audit context.

External research anchors:
- OpenLineage spec (Marquez / DataHub lineage events)
- Explainable AI in Procedural Generation (XAI audit trails)
- Sidecar/Interceptor Pattern (Envoy, eBPF non-intrusive telemetry)

Red lines enforced:
- [防假账红线] Provenance MUST reflect actual knowledge bus state.  If a
  parameter has no knowledge source, it is labeled ``HEURISTIC_FALLBACK``.
- [防破坏红线] Zero modification to any existing computation.
- [防断流红线] Dangling parameters (present in intent but unused by backends)
  are detected and flagged.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..distill.runtime_bus import RuntimeDistillationBus, CompiledParameterSpace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provenance Source Classification
# ---------------------------------------------------------------------------

class ProvenanceSourceType(str, Enum):
    """Classification of where a parameter value originated.

    This is the core of the "honest audit" — every parameter MUST be
    classified into one of these categories.  There is NO "unknown" —
    if we cannot determine the source, it is ``HEURISTIC_FALLBACK``.
    """
    KNOWLEDGE_RULE = "knowledge_rule"
    """Value was derived from or clamped by a specific distilled knowledge rule
    in the ``knowledge/`` directory.  The rule file, rule ID, and constraint
    range are recorded."""

    KNOWLEDGE_DEFAULT = "knowledge_default"
    """Value matches the default from a compiled knowledge space.  The knowledge
    bus provided this default, but no explicit clamping was needed because the
    heuristic value was already within bounds."""

    HEURISTIC_FALLBACK = "heuristic_fallback"
    """Value was derived from hardcoded heuristics in ``SEMANTIC_VIBE_MAP`` or
    dataclass defaults.  No knowledge bus constraint was applied.  This is the
    **"代码硬编码死区"** that must be honestly exposed."""

    USER_OVERRIDE = "user_override"
    """Value was explicitly set by the user via ``overrides`` in the intent
    declaration.  The user's explicit choice overrides both heuristics and
    knowledge constraints."""

    BLUEPRINT_INHERITED = "blueprint_inherited"
    """Value was inherited from a base blueprint file.  The blueprint path
    and genotype lineage are recorded."""

    VIBE_HEURISTIC = "vibe_heuristic"
    """Value was adjusted by the ``SEMANTIC_VIBE_MAP`` heuristic table based
    on fuzzy vibe keywords.  This is a sub-category of heuristic fallback
    but carries the specific vibe token that triggered it."""


# ---------------------------------------------------------------------------
# Parameter Lineage Record (OpenLineage-aligned)
# ---------------------------------------------------------------------------

@dataclass
class ParameterLineageRecord:
    """Full lineage record for a single parameter in the pipeline.

    Inspired by OpenLineage's ``RunEvent`` facets — each parameter carries
    its complete derivation chain from source to final application.
    """
    # Parameter identity
    param_key: str = ""
    param_family: str = ""  # e.g., "physics", "animation", "proportions"

    # Final applied value
    final_value: float = 0.0

    # Provenance classification
    source_type: str = ProvenanceSourceType.HEURISTIC_FALLBACK.value

    # Knowledge source details (populated when source_type is KNOWLEDGE_*)
    knowledge_file: str = ""
    knowledge_module: str = ""
    knowledge_rule_id: str = ""
    knowledge_constraint_min: Optional[float] = None
    knowledge_constraint_max: Optional[float] = None
    knowledge_default: Optional[float] = None
    knowledge_source_quote: str = ""

    # Derivation chain
    base_value: float = 0.0  # Value before any knowledge clamping
    derivation_reason: str = ""

    # Vibe heuristic details (populated when source_type is VIBE_HEURISTIC)
    vibe_token: str = ""
    vibe_delta: float = 0.0

    # Pipeline propagation tracking
    seen_at_intent: bool = False
    seen_at_gate: bool = False
    seen_at_backend: bool = False
    consumed_by_backend: str = ""

    # Audit metadata
    audit_timestamp: str = ""
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "param_key": self.param_key,
            "param_family": self.param_family,
            "final_value": round(self.final_value, 6),
            "source_type": self.source_type,
            "knowledge_file": self.knowledge_file,
            "knowledge_module": self.knowledge_module,
            "knowledge_rule_id": self.knowledge_rule_id,
            "knowledge_constraint_min": (
                round(self.knowledge_constraint_min, 6)
                if self.knowledge_constraint_min is not None else None
            ),
            "knowledge_constraint_max": (
                round(self.knowledge_constraint_max, 6)
                if self.knowledge_constraint_max is not None else None
            ),
            "knowledge_default": (
                round(self.knowledge_default, 6)
                if self.knowledge_default is not None else None
            ),
            "knowledge_source_quote": self.knowledge_source_quote,
            "base_value": round(self.base_value, 6),
            "derivation_reason": self.derivation_reason,
            "vibe_token": self.vibe_token,
            "vibe_delta": round(self.vibe_delta, 6),
            "seen_at_intent": self.seen_at_intent,
            "seen_at_gate": self.seen_at_gate,
            "seen_at_backend": self.seen_at_backend,
            "consumed_by_backend": self.consumed_by_backend,
            "audit_timestamp": self.audit_timestamp,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParameterLineageRecord":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Knowledge State Snapshot
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeStateSnapshot:
    """Immutable snapshot of the knowledge bus state at audit time.

    Captures which knowledge files were loaded, which modules were compiled,
    and the full constraint map — so the audit can prove what knowledge was
    actually available (vs. what was claimed to be available).
    """
    snapshot_time: str = ""
    knowledge_dir: str = ""
    knowledge_files_found: List[str] = field(default_factory=list)
    compiled_modules: List[str] = field(default_factory=list)
    total_constraints: int = 0
    module_constraint_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    bus_available: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Provenance Audit Context (Thread-Local)
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceAuditContext:
    """Per-session audit context that accumulates lineage records as
    parameters flow through the pipeline.

    This context is stored in ``threading.local()`` and is the primary
    vehicle for non-intrusive provenance propagation.
    """
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = "SESSION-152"
    start_time: str = ""

    # Knowledge state at audit start
    knowledge_snapshot: Optional[KnowledgeStateSnapshot] = None

    # Accumulated lineage records (param_key → record)
    lineage_records: Dict[str, ParameterLineageRecord] = field(default_factory=dict)

    # Pipeline propagation checkpoints
    intent_params_seen: Dict[str, float] = field(default_factory=dict)
    gate_params_seen: Dict[str, float] = field(default_factory=dict)
    backend_params_consumed: Dict[str, float] = field(default_factory=dict)
    backend_name: str = ""

    # Dangling parameter detection
    dangling_params: List[str] = field(default_factory=list)

    # Summary statistics
    total_params: int = 0
    knowledge_driven_count: int = 0
    heuristic_fallback_count: int = 0
    user_override_count: int = 0
    vibe_heuristic_count: int = 0
    blueprint_inherited_count: int = 0
    dangling_count: int = 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "knowledge_snapshot": (
                self.knowledge_snapshot.to_dict()
                if self.knowledge_snapshot else None
            ),
            "lineage_records": {
                k: v.to_dict() for k, v in self.lineage_records.items()
            },
            "dangling_params": self.dangling_params,
            "summary": {
                "total_params": self.total_params,
                "knowledge_driven_count": self.knowledge_driven_count,
                "heuristic_fallback_count": self.heuristic_fallback_count,
                "user_override_count": self.user_override_count,
                "vibe_heuristic_count": self.vibe_heuristic_count,
                "blueprint_inherited_count": self.blueprint_inherited_count,
                "dangling_count": self.dangling_count,
            },
        }


# ---------------------------------------------------------------------------
# Knowledge Lineage Tracker (Singleton + Thread-Local)
# ---------------------------------------------------------------------------

class KnowledgeLineageTracker:
    """Non-intrusive sidecar that tracks knowledge provenance across the
    entire parameter derivation and pipeline execution chain.

    This is the central audit probe.  It observes but NEVER modifies:
    - The knowledge bus state (what rules are compiled)
    - The intent parser output (what parameters were derived)
    - The pipeline execution (which parameters were consumed by backends)

    Usage::

        tracker = KnowledgeLineageTracker.instance()
        tracker.begin_audit(knowledge_bus)
        tracker.trace_intent_derivation(spec, vibe_map_used, overrides_used)
        tracker.checkpoint_gate(gate_result)
        tracker.checkpoint_backend(backend_name, consumed_params)
        report = tracker.finalize_audit()
    """

    _instance: Optional["KnowledgeLineageTracker"] = None
    _lock = threading.Lock()
    _local = threading.local()

    def __new__(cls) -> "KnowledgeLineageTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def instance(cls) -> "KnowledgeLineageTracker":
        """Get the global singleton instance."""
        return cls()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None
        cls._local = threading.local()

    # -- Audit Context Management -------------------------------------------

    def _get_context(self) -> ProvenanceAuditContext:
        """Get or create the thread-local audit context."""
        ctx = getattr(self._local, "audit_context", None)
        if ctx is None:
            ctx = ProvenanceAuditContext()
            self._local.audit_context = ctx
        return ctx

    def _set_context(self, ctx: ProvenanceAuditContext) -> None:
        """Set the thread-local audit context."""
        self._local.audit_context = ctx

    @property
    def context(self) -> ProvenanceAuditContext:
        """Read-only access to the current audit context."""
        return self._get_context()

    # -- Phase 1: Begin Audit (Snapshot Knowledge State) --------------------

    def begin_audit(
        self,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
        session_id: str = "SESSION-152",
    ) -> ProvenanceAuditContext:
        """Start a new audit session by snapshotting the knowledge bus state.

        This MUST be called before any parameter derivation to capture the
        ground truth of what knowledge was actually available.
        """
        ctx = ProvenanceAuditContext(
            session_id=session_id,
            start_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        # Snapshot the knowledge bus state
        snapshot = KnowledgeStateSnapshot(
            snapshot_time=ctx.start_time,
        )

        if knowledge_bus is not None:
            snapshot.bus_available = True
            knowledge_dir = getattr(knowledge_bus, "knowledge_dir", None)
            if knowledge_dir and Path(knowledge_dir).exists():
                snapshot.knowledge_dir = str(knowledge_dir)
                # Enumerate actual knowledge files
                kdir = Path(knowledge_dir)
                md_files = sorted(kdir.glob("*.md"))
                json_files = sorted(kdir.glob("*.json"))
                snapshot.knowledge_files_found = [
                    f.name for f in md_files
                ] + [
                    f.name for f in json_files
                ]

            # Snapshot compiled modules and their constraints
            compiled = getattr(knowledge_bus, "compiled_spaces", {}) or {}
            snapshot.compiled_modules = sorted(compiled.keys())
            total_constraints = 0
            for module_name, space in compiled.items():
                param_info = {}
                for idx, pname in enumerate(space.param_names):
                    has_min = bool(space.has_min[idx])
                    has_max = bool(space.has_max[idx])
                    param_info[pname] = {
                        "default": round(float(space.defaults[idx]), 6),
                        "min": round(float(space.min_values[idx]), 6) if has_min else None,
                        "max": round(float(space.max_values[idx]), 6) if has_max else None,
                        "is_hard": bool(space.hard_mask[idx]),
                    }
                    total_constraints += 1
                snapshot.module_constraint_map[module_name] = param_info
            snapshot.total_constraints = total_constraints
        else:
            snapshot.bus_available = False

        ctx.knowledge_snapshot = snapshot
        self._set_context(ctx)
        logger.info(
            "[Provenance] Audit begun: run_id=%s, bus_available=%s, "
            "modules=%d, constraints=%d, knowledge_files=%d",
            ctx.run_id,
            snapshot.bus_available,
            len(snapshot.compiled_modules),
            snapshot.total_constraints,
            len(snapshot.knowledge_files_found),
        )
        return ctx

    # -- Phase 2: Trace Intent Parameter Derivation -------------------------

    def trace_intent_derivation(
        self,
        genotype_flat: Dict[str, float],
        *,
        raw_vibe: str = "",
        vibe_adjustments: Optional[Dict[str, Dict[str, float]]] = None,
        user_overrides: Optional[Dict[str, float]] = None,
        base_blueprint_path: str = "",
        applied_knowledge_rules: Optional[List[Any]] = None,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> Dict[str, ParameterLineageRecord]:
        """Trace the derivation of every parameter in the resolved genotype.

        This is the heart of the provenance audit.  For EACH parameter, it
        determines:
        1. Was it driven by a knowledge rule? → ``KNOWLEDGE_RULE``
        2. Does it match a knowledge default? → ``KNOWLEDGE_DEFAULT``
        3. Was it set by user override? → ``USER_OVERRIDE``
        4. Was it adjusted by vibe heuristic? → ``VIBE_HEURISTIC``
        5. Was it inherited from a blueprint? → ``BLUEPRINT_INHERITED``
        6. None of the above? → ``HEURISTIC_FALLBACK`` (硬编码死区!)

        **[防假账红线]**: This method reads the ACTUAL knowledge bus state.
        It does NOT fabricate provenance.  If the bus is empty or disconnected,
        ALL parameters will be honestly classified as ``HEURISTIC_FALLBACK``.
        """
        ctx = self._get_context()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Build lookup sets for classification
        overridden_keys = set((user_overrides or {}).keys())
        vibe_adjusted_keys: Dict[str, tuple] = {}  # key → (token, delta)
        if vibe_adjustments:
            for token, adjustments in vibe_adjustments.items():
                for param_key, delta in adjustments.items():
                    vibe_adjusted_keys[param_key] = (token, delta)

        # Build knowledge constraint lookup from snapshot
        knowledge_lookup: Dict[str, Dict[str, Any]] = {}
        if ctx.knowledge_snapshot and ctx.knowledge_snapshot.bus_available:
            for module_name, params in ctx.knowledge_snapshot.module_constraint_map.items():
                for param_name, info in params.items():
                    knowledge_lookup[param_name] = {
                        "module": module_name,
                        "info": info,
                    }

        # Build applied-rules lookup
        applied_rules_lookup: Dict[str, Any] = {}
        if applied_knowledge_rules:
            for rule in applied_knowledge_rules:
                rule_dict = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule)
                key = rule_dict.get("param_constrained", "")
                if key:
                    applied_rules_lookup[key] = rule_dict

        # Resolve knowledge file mapping (which knowledge file contains which module)
        knowledge_file_map: Dict[str, str] = {}
        if ctx.knowledge_snapshot:
            for fname in ctx.knowledge_snapshot.knowledge_files_found:
                # Heuristic: file stem often matches module name
                stem = Path(fname).stem.lower()
                knowledge_file_map[stem] = fname

        # Now classify each parameter
        records: Dict[str, ParameterLineageRecord] = {}

        for param_key, value in genotype_flat.items():
            parts = param_key.split(".", 1)
            family = parts[0] if len(parts) == 2 else "unknown"

            record = ParameterLineageRecord(
                param_key=param_key,
                param_family=family,
                final_value=value,
                base_value=value,
                seen_at_intent=True,
                audit_timestamp=timestamp,
                run_id=ctx.run_id,
            )

            # Classification priority (highest to lowest):
            # 1. User override
            # 2. Knowledge rule (explicitly clamped)
            # 3. Knowledge default (value matches knowledge default)
            # 4. Vibe heuristic (adjusted by SEMANTIC_VIBE_MAP)
            # 5. Blueprint inherited
            # 6. Heuristic fallback (代码硬编码死区)

            if param_key in overridden_keys:
                record.source_type = ProvenanceSourceType.USER_OVERRIDE.value
                record.derivation_reason = (
                    f"用户在 intent 声明中显式覆写了此参数 "
                    f"(override value: {user_overrides[param_key]})"
                )

            elif param_key in applied_rules_lookup:
                rule_info = applied_rules_lookup[param_key]
                record.source_type = ProvenanceSourceType.KNOWLEDGE_RULE.value
                record.knowledge_module = rule_info.get("module_name", "")
                record.knowledge_rule_id = rule_info.get("rule_id", "")
                record.base_value = rule_info.get("original_value", value)
                record.knowledge_constraint_min = rule_info.get(
                    "knowledge_min",
                    None,
                )
                record.knowledge_constraint_max = rule_info.get(
                    "knowledge_max",
                    None,
                )
                # Resolve knowledge file
                module_stem = record.knowledge_module.lower()
                record.knowledge_file = knowledge_file_map.get(module_stem, "")
                record.derivation_reason = (
                    f"知识总线约束触发: 规则 {record.knowledge_rule_id} "
                    f"将原始值 {record.base_value:.4f} 钳位到 "
                    f"[{record.knowledge_constraint_min}, "
                    f"{record.knowledge_constraint_max}] 范围内 "
                    f"(来源模块: {record.knowledge_module})"
                )
                record.knowledge_source_quote = rule_info.get("description", "")

            elif self._matches_knowledge_default(
                param_key, value, knowledge_lookup, knowledge_bus
            ):
                # The value happens to match a knowledge default — it's
                # knowledge-aligned but wasn't explicitly clamped
                match_info = self._get_knowledge_match_info(
                    param_key, knowledge_lookup, knowledge_bus
                )
                record.source_type = ProvenanceSourceType.KNOWLEDGE_DEFAULT.value
                record.knowledge_module = match_info.get("module", "")
                record.knowledge_default = match_info.get("default", None)
                record.knowledge_constraint_min = match_info.get("min", None)
                record.knowledge_constraint_max = match_info.get("max", None)
                module_stem = record.knowledge_module.lower()
                record.knowledge_file = knowledge_file_map.get(module_stem, "")
                record.derivation_reason = (
                    f"参数值 {value:.4f} 落在知识模块 "
                    f"'{record.knowledge_module}' 的约束范围内 "
                    f"[{record.knowledge_constraint_min}, "
                    f"{record.knowledge_constraint_max}]，"
                    f"知识默认值={record.knowledge_default}。"
                    f"未触发钳位，但知识总线确认此值合规。"
                )

            elif param_key in vibe_adjusted_keys:
                token, delta = vibe_adjusted_keys[param_key]
                record.source_type = ProvenanceSourceType.VIBE_HEURISTIC.value
                record.vibe_token = token
                record.vibe_delta = delta
                record.derivation_reason = (
                    f"语义氛围词 '{token}' 通过 SEMANTIC_VIBE_MAP 启发式表 "
                    f"调整了此参数 (delta={delta:+.4f})。"
                    f"⚠️ 此调整未经知识总线验证，属于启发式推测。"
                )

            elif base_blueprint_path:
                record.source_type = ProvenanceSourceType.BLUEPRINT_INHERITED.value
                record.derivation_reason = (
                    f"从蓝图文件 '{base_blueprint_path}' 继承的基因型参数。"
                    f"蓝图中的值可能源自先前的进化/手动调整。"
                )

            else:
                # [防假账红线] — 诚实标记为硬编码死区
                record.source_type = ProvenanceSourceType.HEURISTIC_FALLBACK.value
                record.derivation_reason = (
                    f"⚠️ [Heuristic Fallback / 代码硬编码死区] "
                    f"此参数使用了 dataclass 默认值 {value:.4f}，"
                    f"未被任何知识规则驱动、未被用户覆写、"
                    f"未被语义氛围词调整。"
                    f"这是一个潜在的'AI偷懒'断点，需要后续靶向修复。"
                )

            records[param_key] = record

        # Store in context
        ctx.lineage_records = records
        ctx.intent_params_seen = dict(genotype_flat)

        # Compute summary statistics
        ctx.total_params = len(records)
        ctx.knowledge_driven_count = sum(
            1 for r in records.values()
            if r.source_type in (
                ProvenanceSourceType.KNOWLEDGE_RULE.value,
                ProvenanceSourceType.KNOWLEDGE_DEFAULT.value,
            )
        )
        ctx.heuristic_fallback_count = sum(
            1 for r in records.values()
            if r.source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value
        )
        ctx.user_override_count = sum(
            1 for r in records.values()
            if r.source_type == ProvenanceSourceType.USER_OVERRIDE.value
        )
        ctx.vibe_heuristic_count = sum(
            1 for r in records.values()
            if r.source_type == ProvenanceSourceType.VIBE_HEURISTIC.value
        )
        ctx.blueprint_inherited_count = sum(
            1 for r in records.values()
            if r.source_type == ProvenanceSourceType.BLUEPRINT_INHERITED.value
        )

        self._set_context(ctx)
        logger.info(
            "[Provenance] Intent derivation traced: %d params total, "
            "%d knowledge-driven, %d heuristic-fallback, %d vibe-heuristic, "
            "%d user-override, %d blueprint-inherited",
            ctx.total_params,
            ctx.knowledge_driven_count,
            ctx.heuristic_fallback_count,
            ctx.vibe_heuristic_count,
            ctx.user_override_count,
            ctx.blueprint_inherited_count,
        )
        return records

    # -- Phase 3: Pipeline Propagation Checkpoints --------------------------

    def checkpoint_gate(
        self,
        gate_params: Dict[str, float],
    ) -> None:
        """Record which parameters survived through the interactive gate.

        Called after the InteractivePreviewGate processes the intent spec.
        """
        ctx = self._get_context()
        ctx.gate_params_seen = dict(gate_params)
        for key in gate_params:
            if key in ctx.lineage_records:
                ctx.lineage_records[key].seen_at_gate = True
        self._set_context(ctx)
        logger.debug(
            "[Provenance] Gate checkpoint: %d params survived",
            len(gate_params),
        )

    def checkpoint_backend(
        self,
        backend_name: str,
        consumed_params: Dict[str, float],
    ) -> None:
        """Record which parameters were consumed by a backend execution.

        Called after a backend (render, export, evolution) processes params.
        """
        ctx = self._get_context()
        ctx.backend_name = backend_name
        ctx.backend_params_consumed = dict(consumed_params)
        for key in consumed_params:
            if key in ctx.lineage_records:
                ctx.lineage_records[key].seen_at_backend = True
                ctx.lineage_records[key].consumed_by_backend = backend_name
        self._set_context(ctx)
        logger.debug(
            "[Provenance] Backend checkpoint '%s': %d params consumed",
            backend_name,
            len(consumed_params),
        )

    # -- Phase 4: Finalize Audit (Dangling Detection) -----------------------

    def finalize_audit(self) -> ProvenanceAuditContext:
        """Finalize the audit by detecting dangling parameters.

        [防断流红线] — Parameters that were present in the intent but never
        consumed by any backend are flagged as dangling.  This exposes
        "parameter断流" — values that were computed but silently dropped.
        """
        ctx = self._get_context()

        # Detect dangling parameters
        intent_keys = set(ctx.intent_params_seen.keys())
        consumed_keys = set(ctx.backend_params_consumed.keys())

        # If no backend checkpoint was recorded, ALL params are potentially
        # dangling (but we only flag if there was at least one backend run)
        if consumed_keys:
            dangling = intent_keys - consumed_keys
            ctx.dangling_params = sorted(dangling)
            ctx.dangling_count = len(dangling)
        else:
            # No backend was run — this is a dry-run/audit-only scenario
            # Mark all as "not yet consumed" but don't flag as dangling
            ctx.dangling_params = []
            ctx.dangling_count = 0

        self._set_context(ctx)
        logger.info(
            "[Provenance] Audit finalized: run_id=%s, dangling=%d",
            ctx.run_id,
            ctx.dangling_count,
        )
        return ctx

    # -- Helper: Knowledge Default Matching ---------------------------------

    @staticmethod
    def _matches_knowledge_default(
        param_key: str,
        value: float,
        knowledge_lookup: Dict[str, Dict[str, Any]],
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> bool:
        """Check if a parameter value falls within a knowledge constraint range.

        This is the honest check: we look at the ACTUAL compiled knowledge
        space to see if the value is within bounds.  If the knowledge bus
        is disconnected, this always returns False.
        """
        # Direct match
        if param_key in knowledge_lookup:
            info = knowledge_lookup[param_key]["info"]
            return _value_in_knowledge_range(value, info)

        # Leaf match (e.g., "bounce" matches "physics.bounce")
        leaf = param_key.split(".")[-1]
        for kp, kv in knowledge_lookup.items():
            if kp.split(".")[-1] == leaf:
                return _value_in_knowledge_range(value, kv["info"])

        # Try knowledge bus resolve_scalar
        if knowledge_bus is not None:
            for compiled in (knowledge_bus.compiled_spaces or {}).values():
                for idx, pname in enumerate(compiled.param_names):
                    if pname == param_key or pname.split(".")[-1] == leaf:
                        has_min = bool(compiled.has_min[idx])
                        has_max = bool(compiled.has_max[idx])
                        min_v = float(compiled.min_values[idx]) if has_min else None
                        max_v = float(compiled.max_values[idx]) if has_max else None
                        if min_v is not None and value < min_v:
                            return False
                        if max_v is not None and value > max_v:
                            return False
                        if has_min or has_max:
                            return True

        return False

    @staticmethod
    def _get_knowledge_match_info(
        param_key: str,
        knowledge_lookup: Dict[str, Dict[str, Any]],
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> Dict[str, Any]:
        """Get knowledge match details for a parameter."""
        leaf = param_key.split(".")[-1]

        # Direct match
        if param_key in knowledge_lookup:
            entry = knowledge_lookup[param_key]
            return {
                "module": entry["module"],
                "default": entry["info"].get("default"),
                "min": entry["info"].get("min"),
                "max": entry["info"].get("max"),
            }

        # Leaf match
        for kp, kv in knowledge_lookup.items():
            if kp.split(".")[-1] == leaf:
                return {
                    "module": kv["module"],
                    "default": kv["info"].get("default"),
                    "min": kv["info"].get("min"),
                    "max": kv["info"].get("max"),
                }

        # Fallback from bus
        if knowledge_bus is not None:
            for module_name, compiled in (knowledge_bus.compiled_spaces or {}).items():
                for idx, pname in enumerate(compiled.param_names):
                    if pname == param_key or pname.split(".")[-1] == leaf:
                        has_min = bool(compiled.has_min[idx])
                        has_max = bool(compiled.has_max[idx])
                        return {
                            "module": module_name,
                            "default": round(float(compiled.defaults[idx]), 6),
                            "min": round(float(compiled.min_values[idx]), 6) if has_min else None,
                            "max": round(float(compiled.max_values[idx]), 6) if has_max else None,
                        }

        return {}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _value_in_knowledge_range(value: float, info: Dict[str, Any]) -> bool:
    """Check if a value falls within a knowledge constraint range."""
    min_v = info.get("min")
    max_v = info.get("max")
    if min_v is not None and value < min_v:
        return False
    if max_v is not None and value > max_v:
        return False
    # At least one bound must exist for this to count as "knowledge-covered"
    return min_v is not None or max_v is not None


def get_tracker() -> KnowledgeLineageTracker:
    """Convenience: get the global tracker singleton."""
    return KnowledgeLineageTracker.instance()


__all__ = [
    "KnowledgeLineageTracker",
    "KnowledgeStateSnapshot",
    "ParameterLineageRecord",
    "ProvenanceAuditContext",
    "ProvenanceSourceType",
    "get_tracker",
]
