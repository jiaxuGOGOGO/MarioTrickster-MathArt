"""SESSION-154 (P0-SESSION-151-POLICY-AS-CODE-GATES): Knowledge Enforcer Gate Registry.

This package implements the **Policy-as-Code (PaC)** enforcement layer that
transforms static knowledge documents (``knowledge/*.md``) into runtime
validation gates with real ``if/clamp/assert`` logic.

Architecture references:
  - **Open Policy Agent (OPA)**: Decouple policy decision from enforcement.
    Knowledge rules are the "policies"; Enforcers are the "decision engines";
    Pipeline integration points are the "enforcement points".
  - **Design by Contract (DbC)**: Each Enforcer declares preconditions on
    render/export parameters.  Violations trigger either auto-correction
    (Clamp) or hard rejection (ContractError).
  - **Shift-Left Validation**: Enforcers run at PRE-GENERATION, catching
    illegal parameters before they waste GPU cycles.

Key design constraints:
  - IoC Registry Pattern: Enforcers self-register via ``@register_enforcer``
  - Clamp-Not-Reject: Prefer safe auto-correction over hard rejection
  - Source Traceability: Every correction logs its source knowledge document
  - Zero Trunk Modification: No if/else added to existing pipeline core
"""

from mathart.quality.gates.enforcer_registry import (
    EnforcerBase,
    EnforcerResult,
    EnforcerSeverity,
    EnforcerViolation,
    KnowledgeEnforcerRegistry,
    register_enforcer,
    get_enforcer_registry,
    run_all_enforcers,
)

__all__ = [
    "EnforcerBase",
    "EnforcerResult",
    "EnforcerSeverity",
    "EnforcerViolation",
    "KnowledgeEnforcerRegistry",
    "register_enforcer",
    "get_enforcer_registry",
    "run_all_enforcers",
]
