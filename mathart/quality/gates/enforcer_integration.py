"""SESSION-154 (P0-SESSION-151-POLICY-AS-CODE-GATES): Pipeline Integration Layer.

This module provides **zero-trunk-modification** integration between the
Knowledge Enforcer Registry and the existing pipeline infrastructure.

Architecture:
  - ``enforce_render_params()``: The main entry point — wraps ``run_all_enforcers``
    with pipeline-aware context extraction and UX-friendly output.
  - ``enforce_genotype()``: Enforces knowledge gates on a Director Studio
    ``Genotype`` object by flattening to params, enforcing, and re-applying.
  - ``enforcer_summary_report()``: Generates a Markdown summary of all
    registered enforcers and their last enforcement results.

Design constraints:
  - **Zero Trunk Modification**: This module is an ADDITIVE layer.
    No existing pipeline files are modified — integration happens via
    optional hooks that callers can opt into.
  - **Clamp-Not-Reject**: Follows the project-wide principle of preferring
    safe auto-correction over hard rejection.
  - **Source Traceability**: All corrections are logged with their source
    knowledge document reference.

Research foundations:
  - **Policy-as-Code (OPA)**: Enforcement points decouple from policy logic
  - **Design by Contract (DbC)**: Preconditions checked at call boundaries
  - **Shift-Left Validation**: Validation before rendering, not after
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mathart.quality.gates.enforcer_registry import (
    EnforcerResult,
    EnforcerSeverity,
    get_enforcer_registry,
    run_all_enforcers,
)

logger = logging.getLogger("mathart.quality.gates.integration")


# ---------------------------------------------------------------------------
# Main Pipeline Integration Entry Points
# ---------------------------------------------------------------------------

def enforce_render_params(
    params: Dict[str, Any],
    *,
    verbose: bool = False,
    output_fn: Optional[Callable[[str], None]] = None,
    log_to_file: Optional[str | Path] = None,
) -> Tuple[Dict[str, Any], List[EnforcerResult]]:
    """Enforce all knowledge gates on a render parameter dictionary.

    This is the primary integration point for the pipeline.  It can be
    called from:
      - ``MicrokernelPipelineBridge.run_backend()`` (pre-execution hook)
      - ``Pipeline.produce_sprite()`` (before evolution)
      - ``DirectorStudioStrategy.execute()`` (before preview gate)
      - Any custom script that builds render params

    Args:
        params: The render parameter dictionary to validate.
        verbose: If True, print UX-friendly messages via output_fn.
        output_fn: Custom output function (defaults to print).
        log_to_file: Optional path to write enforcement log JSON.

    Returns:
        Tuple of (corrected_params, list_of_results).
    """
    _output = output_fn or print

    if verbose:
        _output("")
        _output("=" * 60)
        _output("  🛡️ 【知识执法网关 — Knowledge Enforcer Gate】")
        _output("=" * 60)

    corrected, results = run_all_enforcers(params, verbose=verbose)

    # Summary
    total_violations = sum(len(r.violations) for r in results)
    total_corrections = sum(
        1 for r in results for v in r.violations
        if v.severity == EnforcerSeverity.CLAMPED
    )
    total_rejections = sum(
        1 for r in results for v in r.violations
        if v.severity == EnforcerSeverity.REJECTED
    )

    if verbose:
        if total_violations == 0:
            _output("  ✅ 所有参数通过知识网关检查 — 无需校正")
        else:
            _output(f"\n  📊 执法摘要: {total_violations} 条规则触发")
            _output(f"     校正 (Clamped): {total_corrections}")
            _output(f"     拦截 (Rejected): {total_rejections}")
        _output("=" * 60)
        _output("")

    # Optional file logging
    if log_to_file:
        _write_enforcement_log(log_to_file, results)

    return corrected, results


def enforce_genotype(
    genotype: Any,
    *,
    verbose: bool = False,
    output_fn: Optional[Callable[[str], None]] = None,
) -> Tuple[Any, List[EnforcerResult]]:
    """Enforce knowledge gates on a Director Studio Genotype object.

    This function bridges the Genotype's structured data model with the
    flat parameter dictionary expected by the enforcers.

    Args:
        genotype: A ``Genotype`` object from ``director_intent``.
        verbose: If True, print UX-friendly messages.
        output_fn: Custom output function.

    Returns:
        Tuple of (corrected_genotype, list_of_results).
    """
    import copy

    corrected_genotype = copy.deepcopy(genotype)

    # Flatten genotype to params dict
    flat = corrected_genotype.flat_params()

    # Also include palette info if available
    if hasattr(corrected_genotype, "palette"):
        palette = corrected_genotype.palette
        if hasattr(palette, "colors") and palette.colors:
            flat["colors_srgb"] = palette.colors
        if hasattr(palette, "name") and palette.name:
            flat["palette_context"] = palette.name

    # Also include extra dict
    if hasattr(corrected_genotype, "extra") and corrected_genotype.extra:
        flat.update(corrected_genotype.extra)

    # Run enforcers
    corrected_flat, results = enforce_render_params(
        flat, verbose=verbose, output_fn=output_fn,
    )

    # Apply corrections back to genotype
    # Only apply numeric params that belong to genotype families
    numeric_params = {
        k: v for k, v in corrected_flat.items()
        if isinstance(v, (int, float)) and "." in k
    }
    if numeric_params:
        corrected_genotype.apply_flat_params(numeric_params)

    # Apply extra corrections
    if hasattr(corrected_genotype, "extra"):
        for key in corrected_genotype.extra:
            if key in corrected_flat:
                corrected_genotype.extra[key] = corrected_flat[key]

    return corrected_genotype, results


def enforce_backend_context(
    context: Dict[str, Any],
    backend_name: str = "",
    *,
    verbose: bool = False,
    output_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Enforce knowledge gates on a backend execution context.

    Designed to be called from ``MicrokernelPipelineBridge.run_backend()``
    as a pre-execution hook.  Returns the corrected context.

    Args:
        context: The backend execution context dictionary.
        backend_name: Name of the backend (for logging).
        verbose: If True, print UX-friendly messages.
        output_fn: Custom output function.

    Returns:
        Corrected context dictionary.
    """
    logger.info(
        "Knowledge Enforcer Gate: pre-execution check for backend '%s'",
        backend_name,
    )
    corrected, results = enforce_render_params(
        context, verbose=verbose, output_fn=output_fn,
    )

    # Log summary
    total_violations = sum(len(r.violations) for r in results)
    if total_violations > 0:
        logger.info(
            "Knowledge Enforcer Gate: %d violations found for backend '%s' "
            "(%d corrected, %d rejected)",
            total_violations,
            backend_name,
            sum(1 for r in results for v in r.violations
                if v.severity == EnforcerSeverity.CLAMPED),
            sum(1 for r in results for v in r.violations
                if v.severity == EnforcerSeverity.REJECTED),
        )
    else:
        logger.debug(
            "Knowledge Enforcer Gate: all params clean for backend '%s'",
            backend_name,
        )

    return corrected


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def enforcer_summary_report() -> str:
    """Generate a Markdown summary report of all registered enforcers.

    Returns:
        Markdown-formatted string with enforcer registry status.
    """
    registry = get_enforcer_registry()
    lines = [
        "# Knowledge Enforcer Gate — Registry Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Registered Enforcers",
        "",
        registry.summary_table(),
        "",
        "## Architecture",
        "",
        "| Component | Pattern | Reference |",
        "|-----------|---------|-----------|",
        "| Registry | IoC Singleton | OPA Policy Engine |",
        "| Enforcers | Self-registering plugins | OPA Rego Modules |",
        "| Integration | Zero-trunk hooks | Shift-Left Validation |",
        "| Correction | Clamp-Not-Reject | Design by Contract |",
        "| Traceability | Source doc references | Policy-as-Code |",
        "",
        "## Enforcement Flow",
        "",
        "```",
        "Pipeline Params → KnowledgeEnforcerRegistry.run_all()",
        "  → PixelArtEnforcer.validate(params)",
        "    → if canvas_size > 64: clamp to 64 (pixel_art.md)",
        "    → if interpolation == 'bilinear': force 'nearest' (pixel_art.md)",
        "    → if anti_aliasing == True: force False (pixel_art.md)",
        "  → ColorHarmonyEnforcer.validate(params)",
        "    → if palette L-range < 0.3: stretch lightness (color_science.md)",
        "    → if dead_colors > 1: boost chroma (color_light.md)",
        "    → if warm-cool shift < 120°: adjust shadow hue (color_light.md)",
        "→ Corrected Params (with full violation log)",
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _write_enforcement_log(
    path: str | Path,
    results: List[EnforcerResult],
) -> None:
    """Write enforcement results to a JSON log file."""
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session": "SESSION-154",
        "enforcers": [],
    }

    for result in results:
        enforcer_log = {
            "name": result.enforcer_name,
            "violations": [
                {
                    "rule_id": v.rule_id,
                    "severity": v.severity.value,
                    "source_doc": v.source_doc,
                    "field_name": v.field_name,
                    "original_value": _safe_serialize(v.original_value),
                    "corrected_value": _safe_serialize(v.corrected_value),
                    "message": v.message,
                }
                for v in result.violations
            ],
            "summary": result.summary(),
        }
        log_data["enforcers"].append(enforcer_log)

    log_path.write_text(
        json.dumps(log_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Enforcement log written to: %s", log_path)


def _safe_serialize(value: Any) -> Any:
    """Safely serialize a value for JSON output."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_serialize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_serialize(v) for k, v in value.items()}
    return str(value)
