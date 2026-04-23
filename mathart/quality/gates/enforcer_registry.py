"""SESSION-154 (P0-SESSION-151-POLICY-AS-CODE-GATES): Knowledge Enforcer Registry.

Implements the IoC-based **KnowledgeEnforcerRegistry** — a singleton registry
where knowledge enforcement plugins self-register via the ``@register_enforcer``
decorator, mirroring the project's established ``@register_backend`` pattern.

Architecture:
  - **Policy-as-Code (OPA-inspired)**: Static knowledge → executable gate
  - **Design by Contract (DbC)**: Precondition/postcondition enforcement
  - **Shift-Left Validation**: Validate BEFORE rendering, not after

Red-line compliance:
  - ✅ Independent plugin: each Enforcer is a standalone class
  - ✅ No trunk modification: registry is discovered via lazy import
  - ✅ Clamp-Not-Reject: ``EnforcerSeverity.CLAMPED`` preferred over ``REJECTED``
  - ✅ Source traceability: every violation carries ``source_doc`` reference
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger("mathart.quality.gates")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class EnforcerSeverity(str, Enum):
    """Severity level of an enforcer action."""
    INFO = "info"           # Informational — no correction needed
    CLAMPED = "clamped"     # Auto-corrected to safe value (preferred)
    REJECTED = "rejected"   # Hard rejection — PipelineContractError


@dataclass
class EnforcerViolation:
    """A single violation or correction reported by an Enforcer.

    Every violation MUST carry a ``source_doc`` reference so the log output
    can trace the rule back to the originating knowledge document.
    """
    rule_id: str
    message: str
    severity: EnforcerSeverity
    source_doc: str              # e.g. "pixel_art.md"
    field_name: str = ""         # The parameter field that was checked
    original_value: Any = None   # Value before correction
    corrected_value: Any = None  # Value after correction (if CLAMPED)

    def log_line(self) -> str:
        """Format a human-readable log line with source traceability."""
        tag = {
            EnforcerSeverity.INFO: "INFO",
            EnforcerSeverity.CLAMPED: "CLAMPED",
            EnforcerSeverity.REJECTED: "REJECTED",
        }.get(self.severity, "UNKNOWN")
        base = (
            f"[Knowledge Enforcer] [{tag}] Rule triggered: "
            f"'{self.rule_id}' (Source: {self.source_doc})"
        )
        if self.severity == EnforcerSeverity.CLAMPED:
            base += (
                f" | {self.field_name}: {self.original_value!r} → "
                f"{self.corrected_value!r}"
            )
        elif self.severity == EnforcerSeverity.REJECTED:
            base += f" | {self.field_name}: {self.original_value!r} REJECTED"
        return base

    def ux_line(self) -> str:
        """Format a user-facing terminal line with friendly emoji."""
        if self.severity == EnforcerSeverity.CLAMPED:
            return (
                f"[💡 知识网关激活] 依据《{self.source_doc}》，"
                f"系统已自动校正您的参数 {self.field_name}: "
                f"{self.original_value!r} → {self.corrected_value!r} "
                f"(规则: {self.rule_id})"
            )
        elif self.severity == EnforcerSeverity.REJECTED:
            return (
                f"[🚫 知识网关拦截] 依据《{self.source_doc}》，"
                f"系统已拦截非法参数 {self.field_name}: "
                f"{self.original_value!r} "
                f"(规则: {self.rule_id}) — {self.message}"
            )
        else:
            return (
                f"[ℹ️ 知识网关] 依据《{self.source_doc}》: {self.message}"
            )


@dataclass
class EnforcerResult:
    """Aggregated result from a single Enforcer's ``validate()`` call.

    Contains the (possibly corrected) parameter dictionary and a list of
    all violations/corrections applied.
    """
    enforcer_name: str
    params: Dict[str, Any]           # The (possibly corrected) params
    violations: List[EnforcerViolation] = field(default_factory=list)

    @property
    def has_rejections(self) -> bool:
        return any(
            v.severity == EnforcerSeverity.REJECTED for v in self.violations
        )

    @property
    def has_corrections(self) -> bool:
        return any(
            v.severity == EnforcerSeverity.CLAMPED for v in self.violations
        )

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> dict:
        return {
            "enforcer": self.enforcer_name,
            "total_violations": len(self.violations),
            "rejections": sum(
                1 for v in self.violations
                if v.severity == EnforcerSeverity.REJECTED
            ),
            "corrections": sum(
                1 for v in self.violations
                if v.severity == EnforcerSeverity.CLAMPED
            ),
            "clean": self.is_clean,
        }


# ---------------------------------------------------------------------------
# Abstract Base Class
# ---------------------------------------------------------------------------

class EnforcerBase(ABC):
    """Abstract base for all Knowledge Enforcer plugins.

    Subclasses MUST implement:
      - ``name``: unique identifier string
      - ``source_docs``: list of knowledge documents this enforcer consumes
      - ``validate(params)``: the core enforcement logic

    The ``validate()`` method receives a mutable parameter dictionary and
    returns an ``EnforcerResult``.  It SHOULD prefer clamping (auto-correction)
    over hard rejection, following the Clamp-Not-Reject principle.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique enforcer identifier."""
        ...

    @property
    @abstractmethod
    def source_docs(self) -> list[str]:
        """List of knowledge document filenames this enforcer consumes."""
        ...

    @abstractmethod
    def validate(self, params: Dict[str, Any]) -> EnforcerResult:
        """Validate and optionally correct the parameter dictionary.

        Args:
            params: Mutable parameter dictionary from the pipeline.

        Returns:
            EnforcerResult with corrected params and violation list.
        """
        ...


# ---------------------------------------------------------------------------
# Singleton Registry
# ---------------------------------------------------------------------------

class KnowledgeEnforcerRegistry:
    """Singleton registry for Knowledge Enforcer plugins.

    Follows the same IoC pattern as ``BackendRegistry``:
      - Enforcers self-register via ``@register_enforcer``
      - The registry is lazily initialized on first access
      - Thread-safe singleton via class-level ``_instance``
    """

    _instance: Optional["KnowledgeEnforcerRegistry"] = None
    _enforcers: Dict[str, Type[EnforcerBase]]

    def __init__(self) -> None:
        self._enforcers = {}

    @classmethod
    def get_instance(cls) -> "KnowledgeEnforcerRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def register(self, enforcer_cls: Type[EnforcerBase]) -> None:
        """Register an Enforcer class."""
        # Instantiate to read the name property
        instance = enforcer_cls()
        name = instance.name
        if name in self._enforcers:
            logger.warning(
                "KnowledgeEnforcerRegistry: overwriting existing enforcer '%s'",
                name,
            )
        self._enforcers[name] = enforcer_cls
        logger.debug(
            "KnowledgeEnforcerRegistry: registered '%s' (sources: %s)",
            name,
            instance.source_docs,
        )

    def get(self, name: str) -> Optional[Type[EnforcerBase]]:
        return self._enforcers.get(name)

    def list_all(self) -> list[str]:
        return list(self._enforcers.keys())

    def instantiate_all(self) -> list[EnforcerBase]:
        """Create instances of all registered enforcers."""
        return [cls() for cls in self._enforcers.values()]

    def summary_table(self) -> str:
        """Generate a Markdown summary table of all registered enforcers."""
        lines = [
            "| Enforcer | Source Docs | Status |",
            "|----------|------------|--------|",
        ]
        for name, cls in self._enforcers.items():
            instance = cls()
            docs = ", ".join(instance.source_docs)
            lines.append(f"| {name} | {docs} | Active |")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def register_enforcer(cls: Type[EnforcerBase]) -> Type[EnforcerBase]:
    """Class decorator to self-register an Enforcer into the singleton registry.

    Usage::

        @register_enforcer
        class MyEnforcer(EnforcerBase):
            ...
    """
    registry = KnowledgeEnforcerRegistry.get_instance()
    registry.register(cls)
    return cls


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def get_enforcer_registry() -> KnowledgeEnforcerRegistry:
    """Return the singleton enforcer registry, auto-loading built-in enforcers."""
    registry = KnowledgeEnforcerRegistry.get_instance()

    # Auto-load built-in enforcers on first access
    if not registry.list_all():
        _auto_load_enforcers()

    return registry


def _auto_load_enforcers() -> None:
    """Import built-in enforcer modules to trigger self-registration.

    If modules are already cached in ``sys.modules`` (e.g. after a
    ``reset()`` call in tests), we manually re-register the enforcer
    classes found in the module.
    """
    import importlib
    import sys as _sys
    from pathlib import Path

    modules = [
        "mathart.quality.gates.pixel_art_enforcer",
        "mathart.quality.gates.color_harmony_enforcer",
    ]
    
    # SESSION-155: Auto-discover generated enforcers
    auto_dir = Path(__file__).parent / "auto_generated"
    if auto_dir.is_dir():
        for py_file in sorted(auto_dir.glob("*_enforcer.py")):
            module_name = f"mathart.quality.gates.auto_generated.{py_file.stem}"
            if module_name not in modules:
                modules.append(module_name)

    registry = KnowledgeEnforcerRegistry.get_instance()
    for mod_name in modules:
        try:
            if mod_name in _sys.modules:
                # Module already imported — re-register classes manually
                mod = _sys.modules[mod_name]
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, EnforcerBase)
                        and attr is not EnforcerBase
                    ):
                        registry.register(attr)
            else:
                importlib.import_module(mod_name)
            logger.debug("Auto-loaded enforcer module: %s", mod_name)
        except Exception as e:
            logger.debug("Failed to auto-load enforcer module %s: %s", mod_name, e)


def run_all_enforcers(
    params: Dict[str, Any],
    *,
    verbose: bool = False,
) -> tuple[Dict[str, Any], list[EnforcerResult]]:
    """Run all registered enforcers against the given parameter dictionary.

    This is the main entry point for pipeline integration.  It chains all
    enforcers sequentially — each enforcer receives the (possibly corrected)
    params from the previous one.

    Args:
        params: The parameter dictionary to validate.
        verbose: If True, print UX-friendly messages to stdout.

    Returns:
        Tuple of (corrected_params, list_of_results).

    Raises:
        PipelineContractError: If any enforcer issues a REJECTED violation
            and the violation cannot be auto-corrected.
    """
    registry = get_enforcer_registry()
    enforcers = registry.instantiate_all()
    results: list[EnforcerResult] = []
    current_params = dict(params)  # Defensive copy

    for enforcer in enforcers:
        try:
            result = enforcer.validate(current_params)
            results.append(result)

            # Log all violations
            for v in result.violations:
                logger.info(v.log_line())
                if verbose:
                    print(v.ux_line())

            # Update params with corrections
            current_params = result.params

            # Check for hard rejections
            if result.has_rejections:
                from mathart.pipeline_contract import PipelineContractError
                rejection_msgs = [
                    v.message
                    for v in result.violations
                    if v.severity == EnforcerSeverity.REJECTED
                ]
                raise PipelineContractError(
                    "knowledge_enforcer_rejection",
                    f"[{enforcer.name}] Hard rejection: {'; '.join(rejection_msgs)}",
                )

        except Exception as e:
            # Re-raise PipelineContractError, swallow others gracefully
            from mathart.pipeline_contract import PipelineContractError
            if isinstance(e, PipelineContractError):
                raise
            logger.warning(
                "Enforcer '%s' raised unexpected error (non-fatal): %s",
                enforcer.name,
                e,
            )

    return current_params, results
