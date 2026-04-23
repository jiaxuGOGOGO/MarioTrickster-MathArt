"""SESSION-152 — Knowledge Provenance Audit Report Generator.

P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT

This module generates the terminal-printed **参数血统溯源体检表** and the
persistent ``logs/knowledge_audit_trace.json`` audit log.

Architecture discipline:
- **Pure output module**: Consumes ``ProvenanceAuditContext`` and produces
  formatted output.  NEVER modifies any pipeline state.
- **XAI-aligned**: The report is an Explainable AI audit trail that maps
  high-dimensional semantic intent to low-dimensional physical parameters.
- **Tabulate-formatted**: Uses the ``tabulate`` library for clean terminal
  output with CJK-aware column alignment.

Report columns:
- [最终应用参数] — The parameter key (e.g., ``physics.bounce``)
- [实际推演数值] — The final float value applied
- [驱动该值的知识来源/书籍理论] — Source classification + knowledge file
- [具体推演原由] — Human-readable derivation reason

Red lines enforced:
- [防假账红线] Heuristic fallback is displayed with ``⚠️`` warning markers.
- [防断流红线] Dangling parameters are listed in a separate WARNING section.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .provenance_tracker import (
    KnowledgeLineageTracker,
    ParameterLineageRecord,
    ProvenanceAuditContext,
    ProvenanceSourceType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source Type Display Labels
# ---------------------------------------------------------------------------

_SOURCE_LABELS: Dict[str, str] = {
    ProvenanceSourceType.KNOWLEDGE_RULE.value: "Knowledge Rule (知识规则驱动)",
    ProvenanceSourceType.KNOWLEDGE_DEFAULT.value: "Knowledge Default (知识默认值合规)",
    ProvenanceSourceType.HEURISTIC_FALLBACK.value: "[Heuristic Fallback / 代码硬编码死区]",
    ProvenanceSourceType.USER_OVERRIDE.value: "User Override (用户显式覆写)",
    ProvenanceSourceType.VIBE_HEURISTIC.value: "Vibe Heuristic (语义启发式)",
    ProvenanceSourceType.BLUEPRINT_INHERITED.value: "Blueprint Inherited (蓝图继承)",
}


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

class ProvenanceReportGenerator:
    """Generates the Knowledge Provenance Audit Report.

    Two output formats:
    1. **Terminal table** — Printed via ``tabulate`` for human review.
    2. **JSON audit log** — Dumped to ``logs/knowledge_audit_trace.json``
       for machine consumption and future forensic analysis.
    """

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        output_fn: Optional[callable] = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.logs_dir = self.project_root / "logs"
        self.output_fn = output_fn or print

    # -- Main Entry Point ---------------------------------------------------

    def generate(
        self,
        context: Optional[ProvenanceAuditContext] = None,
    ) -> Dict[str, Any]:
        """Generate the full audit report.

        Returns the report payload dict (also written to JSON).
        """
        if context is None:
            context = KnowledgeLineageTracker.instance().finalize_audit()

        self._print_header(context)
        self._print_knowledge_snapshot(context)
        self._print_lineage_table(context)
        self._print_summary(context)
        self._print_dangling_warnings(context)
        self._print_hardcoded_dead_zones(context)

        # Build JSON payload
        payload = self._build_json_payload(context)

        # Dump to file
        json_path = self._dump_json(payload)

        self._print_footer(json_path)

        return payload

    # -- Terminal Output Sections -------------------------------------------

    def _print_header(self, ctx: ProvenanceAuditContext) -> None:
        self.output_fn("")
        self.output_fn("=" * 80)
        self.output_fn(
            "  KNOWLEDGE PROVENANCE AUDIT REPORT"
        )
        self.output_fn(
            "  (参数血统溯源体检表)"
        )
        self.output_fn("=" * 80)
        self.output_fn(f"  Run ID    : {ctx.run_id}")
        self.output_fn(f"  Session   : {ctx.session_id}")
        self.output_fn(f"  Timestamp : {ctx.start_time}")
        self.output_fn("-" * 80)

    def _print_knowledge_snapshot(self, ctx: ProvenanceAuditContext) -> None:
        snapshot = ctx.knowledge_snapshot
        if snapshot is None:
            self.output_fn("  [WARNING] No knowledge snapshot available!")
            return

        self.output_fn("")
        self.output_fn("  KNOWLEDGE BUS STATE SNAPSHOT")
        self.output_fn("  " + "-" * 40)
        self.output_fn(
            f"  Bus Available       : "
            f"{'YES' if snapshot.bus_available else 'NO (DISCONNECTED!)'}"
        )
        self.output_fn(f"  Knowledge Directory : {snapshot.knowledge_dir or 'N/A'}")
        self.output_fn(f"  Knowledge Files     : {len(snapshot.knowledge_files_found)}")
        self.output_fn(f"  Compiled Modules    : {len(snapshot.compiled_modules)}")
        self.output_fn(f"  Total Constraints   : {snapshot.total_constraints}")

        if snapshot.compiled_modules:
            self.output_fn(f"  Modules: {', '.join(snapshot.compiled_modules)}")
        self.output_fn("")

    def _print_lineage_table(self, ctx: ProvenanceAuditContext) -> None:
        """Print the core audit table using tabulate."""
        try:
            from tabulate import tabulate
        except ImportError:
            tabulate = None

        records = sorted(
            ctx.lineage_records.values(),
            key=lambda r: (r.param_family, r.param_key),
        )

        if not records:
            self.output_fn("  [INFO] No parameter lineage records to display.")
            return

        self.output_fn("  PARAMETER LINEAGE TABLE")
        self.output_fn("  " + "-" * 76)

        # Build table rows
        headers = [
            "最终应用参数",
            "实际推演数值",
            "驱动该值的知识来源/书籍理论",
            "具体推演原由",
        ]

        rows = []
        for record in records:
            source_label = _SOURCE_LABELS.get(
                record.source_type,
                record.source_type,
            )

            # Build knowledge source detail
            if record.knowledge_file:
                source_detail = f"{source_label}\n  File: {record.knowledge_file}"
                if record.knowledge_module:
                    source_detail += f"\n  Module: {record.knowledge_module}"
                if record.knowledge_rule_id:
                    source_detail += f"\n  Rule: {record.knowledge_rule_id}"
            else:
                source_detail = source_label

            # Truncate reason for display
            reason = record.derivation_reason
            if len(reason) > 120:
                reason = reason[:117] + "..."

            rows.append([
                record.param_key,
                f"{record.final_value:.4f}",
                source_detail,
                reason,
            ])

        if tabulate is not None:
            table_str = tabulate(
                rows,
                headers=headers,
                tablefmt="grid",
                maxcolwidths=[25, 12, 40, 50],
            )
            for line in table_str.split("\n"):
                self.output_fn(f"  {line}")
        else:
            # Fallback: simple formatted output
            self.output_fn(
                f"  {'最终应用参数':<25} {'实际推演数值':<12} "
                f"{'知识来源':<40} {'推演原由'}"
            )
            self.output_fn("  " + "-" * 120)
            for row in rows:
                # Clean up multiline source for simple format
                source_clean = row[2].replace("\n", " | ")
                reason_clean = row[3][:60]
                self.output_fn(
                    f"  {row[0]:<25} {row[1]:<12} "
                    f"{source_clean:<40} {reason_clean}"
                )

        self.output_fn("")

    def _print_summary(self, ctx: ProvenanceAuditContext) -> None:
        self.output_fn("  AUDIT SUMMARY")
        self.output_fn("  " + "-" * 40)
        self.output_fn(f"  Total Parameters        : {ctx.total_params}")
        self.output_fn(
            f"  Knowledge-Driven        : {ctx.knowledge_driven_count} "
            f"({_pct(ctx.knowledge_driven_count, ctx.total_params)})"
        )
        self.output_fn(
            f"  Heuristic Fallback      : {ctx.heuristic_fallback_count} "
            f"({_pct(ctx.heuristic_fallback_count, ctx.total_params)})"
        )
        self.output_fn(
            f"  Vibe Heuristic          : {ctx.vibe_heuristic_count} "
            f"({_pct(ctx.vibe_heuristic_count, ctx.total_params)})"
        )
        self.output_fn(
            f"  User Override           : {ctx.user_override_count} "
            f"({_pct(ctx.user_override_count, ctx.total_params)})"
        )
        self.output_fn(
            f"  Blueprint Inherited     : {ctx.blueprint_inherited_count} "
            f"({_pct(ctx.blueprint_inherited_count, ctx.total_params)})"
        )
        self.output_fn(
            f"  Dangling (Unused)       : {ctx.dangling_count} "
            f"({_pct(ctx.dangling_count, ctx.total_params)})"
        )

        # Health verdict
        if ctx.total_params > 0:
            knowledge_ratio = ctx.knowledge_driven_count / ctx.total_params
            if knowledge_ratio >= 0.5:
                verdict = "HEALTHY — 多数参数有知识总线驱动"
            elif knowledge_ratio >= 0.2:
                verdict = "PARTIAL — 部分参数有知识驱动，但存在显著硬编码死区"
            else:
                verdict = "CRITICAL — 绝大多数参数为硬编码兜底，知识总线几乎断连"
        else:
            verdict = "N/A — 无参数可审计"

        self.output_fn(f"\n  HEALTH VERDICT: {verdict}")
        self.output_fn("")

    def _print_dangling_warnings(self, ctx: ProvenanceAuditContext) -> None:
        if not ctx.dangling_params:
            return

        self.output_fn("  [WARNING: 悬空未被使用的废弃参数列表]")
        self.output_fn("  " + "-" * 50)
        self.output_fn(
            "  以下参数在 Intent 阶段生成，但在传给最终图形成像阶段时"
        )
        self.output_fn("  '半路丢了'，未被任何 Backend 消费：")
        self.output_fn("")
        for param in ctx.dangling_params:
            value = ctx.intent_params_seen.get(param, 0.0)
            self.output_fn(f"    - {param} = {value:.4f}")
        self.output_fn("")

    def _print_hardcoded_dead_zones(self, ctx: ProvenanceAuditContext) -> None:
        """Print a dedicated section listing all hardcoded dead zones."""
        dead_zones = [
            r for r in ctx.lineage_records.values()
            if r.source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value
        ]
        if not dead_zones:
            return

        self.output_fn("  [ALERT: 代码硬编码死区清单 — AI偷懒真凶暴露]")
        self.output_fn("  " + "-" * 50)
        self.output_fn(
            "  以下参数完全由代码默认值驱动，未接通任何外部蒸馏知识："
        )
        self.output_fn("")
        for record in sorted(dead_zones, key=lambda r: r.param_key):
            self.output_fn(
                f"    [{record.param_family}] {record.param_key} = "
                f"{record.final_value:.4f}  ← HARDCODED DEFAULT"
            )
        self.output_fn("")
        self.output_fn(
            f"  共 {len(dead_zones)} 个参数处于硬编码死区，"
            f"需要后续靶向修复战役逐个接通知识总线。"
        )
        self.output_fn("")

    def _print_footer(self, json_path: Optional[Path]) -> None:
        self.output_fn("=" * 80)
        if json_path:
            self.output_fn(
                f"  Audit JSON dumped → {json_path}"
            )
        self.output_fn(
            "  Knowledge Provenance Audit Complete"
        )
        self.output_fn("=" * 80)
        self.output_fn("")

    # -- JSON Payload & Dump ------------------------------------------------

    def _build_json_payload(
        self,
        ctx: ProvenanceAuditContext,
    ) -> Dict[str, Any]:
        """Build the full JSON audit payload."""
        return {
            "audit_version": "1.0.0",
            "session_id": ctx.session_id,
            "run_id": ctx.run_id,
            "timestamp": ctx.start_time,
            "generator": "ProvenanceReportGenerator (SESSION-152)",
            "knowledge_snapshot": (
                ctx.knowledge_snapshot.to_dict()
                if ctx.knowledge_snapshot else None
            ),
            "lineage_records": {
                k: v.to_dict() for k, v in ctx.lineage_records.items()
            },
            "summary": {
                "total_params": ctx.total_params,
                "knowledge_driven_count": ctx.knowledge_driven_count,
                "heuristic_fallback_count": ctx.heuristic_fallback_count,
                "vibe_heuristic_count": ctx.vibe_heuristic_count,
                "user_override_count": ctx.user_override_count,
                "blueprint_inherited_count": ctx.blueprint_inherited_count,
                "dangling_count": ctx.dangling_count,
            },
            "dangling_params": ctx.dangling_params,
            "dead_zones": [
                {
                    "param_key": r.param_key,
                    "final_value": round(r.final_value, 6),
                    "reason": r.derivation_reason,
                }
                for r in ctx.lineage_records.values()
                if r.source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value
            ],
        }

    def _dump_json(self, payload: Dict[str, Any]) -> Optional[Path]:
        """Dump the audit payload to ``logs/knowledge_audit_trace.json``."""
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            json_path = self.logs_dir / "knowledge_audit_trace.json"
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info("[Provenance] Audit JSON dumped → %s", json_path)
            return json_path
        except Exception as e:
            logger.warning("[Provenance] Failed to dump audit JSON: %s", e)
            return None


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def generate_provenance_report(
    project_root: Optional[str | Path] = None,
    context: Optional[ProvenanceAuditContext] = None,
    output_fn: Optional[callable] = None,
) -> Dict[str, Any]:
    """One-shot convenience: generate the full provenance audit report."""
    generator = ProvenanceReportGenerator(
        project_root=project_root,
        output_fn=output_fn,
    )
    return generator.generate(context=context)


def _pct(part: int, total: int) -> str:
    """Format a percentage string."""
    if total == 0:
        return "N/A"
    return f"{100.0 * part / total:.1f}%"


__all__ = [
    "ProvenanceReportGenerator",
    "generate_provenance_report",
]
