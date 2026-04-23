"""SESSION-152: Update PROJECT_BRAIN.json for Knowledge Provenance Audit."""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRAIN_PATH = PROJECT_ROOT / "PROJECT_BRAIN.json"

brain = json.loads(BRAIN_PATH.read_text(encoding="utf-8"))

# --- Top-level fields ---
brain["version"] = "v0.99.4"
brain["last_session_id"] = "SESSION-152"
brain["last_updated"] = "2026-04-23"
brain["total_iterations"] = brain.get("total_iterations", 749) + 1
brain["total_code_lines"] = brain.get("total_code_lines", 166700) + 1800

# --- Update current_focus ---
brain["current_focus"] = (
    "SESSION-152: P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT — Full-chain "
    "knowledge provenance audit system. Non-intrusive sidecar pattern "
    "(OpenLineage + XAI + Interceptor). Three modules: "
    "provenance_tracker.py (singleton + thread-safe + 6-level classification), "
    "provenance_report.py (terminal audit table + JSON log), "
    "provenance_audit_backend.py (Registry-native @register_backend plugin). "
    "Audit result: 18 params traced, 4 knowledge-driven (22.2%), "
    "9 heuristic fallback (50.0%), 5 vibe heuristic (27.8%). "
    "Verdict: PARTIAL. 9 dead-zone params exposed for targeted fix campaigns. "
    "9/9 tests PASS."
)

# --- Add P0-SESSION-148 to top_priorities_ordered ---
new_task = {
    "id": "P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT",
    "title": "Full-Chain Knowledge Provenance Audit & Parameter Penetration Testing",
    "priority": "P0",
    "status": "CLOSED",
    "session_landed": "SESSION-152",
    "modules": [
        "mathart/core/provenance_tracker.py",
        "mathart/core/provenance_report.py",
        "mathart/core/provenance_audit_backend.py",
        "mathart/core/__init__.py",
        "mathart/core/backend_types.py",
        "mathart/core/backend_registry.py",
    ],
    "tests": [
        "tests/test_provenance_audit.py"
    ],
    "description": (
        "Implemented the complete end-to-end knowledge provenance audit system "
        "as a non-intrusive sidecar (OpenLineage + XAI + Interceptor Pattern). "
        "Three core modules: (1) provenance_tracker.py — KnowledgeLineageTracker "
        "singleton with thread-safe audit context, knowledge bus snapshot, "
        "6-level provenance classification (KNOWLEDGE_DRIVEN, KNOWLEDGE_CLAMPED, "
        "VIBE_HEURISTIC, USER_OVERRIDE, BLUEPRINT_INHERITED, HEURISTIC_FALLBACK), "
        "and dangling parameter detection via backend consumption checkpoints; "
        "(2) provenance_report.py — ProvenanceReportGenerator producing CJK-aligned "
        "terminal audit table and persistent logs/knowledge_audit_trace.json with "
        "full knowledge snapshot, lineage records, summary statistics, and dead-zone "
        "inventory; (3) provenance_audit_backend.py — @register_backend plugin "
        "(BackendType.PROVENANCE_AUDIT) with standalone audit runner for CLI/dry-run "
        "mode. Audit result: 18 params traced, 4 knowledge-driven (22.2%), "
        "9 heuristic fallback / dead zones (50.0%), 5 vibe heuristic (27.8%), "
        "verdict PARTIAL. Dead zones exposed: physics.gravity, proportions.* (4), "
        "animation.frame_rate/ease_in/ease_out/cycle_frames. All three red lines "
        "enforced: no fake provenance, no computation modification, dangling detection. "
        "9/9 tests PASS."
    ),
}

# Insert at position 0 (most recent)
top_priorities = brain.get("gap_inventory", {}).get("top_priorities_ordered", [])
top_priorities.insert(0, new_task)
brain["gap_inventory"]["top_priorities_ordered"] = top_priorities

# --- Add new P1 tasks to gap_inventory ---
brain["gap_inventory"]["active_total"] = brain["gap_inventory"].get("active_total", 76) + 5
p1_count = brain["gap_inventory"]["by_priority"].get("P1", 37)
brain["gap_inventory"]["by_priority"]["P1"] = p1_count + 4
p2_count = brain["gap_inventory"]["by_priority"].get("P2", 34)
brain["gap_inventory"]["by_priority"]["P2"] = p2_count + 1
todo_count = brain["gap_inventory"]["by_status"].get("TODO", 52)
brain["gap_inventory"]["by_status"]["TODO"] = todo_count + 5

# --- Add architecture decision ---
arch_decisions = brain.get("architecture_decisions", [])
arch_decisions.append({
    "session_id": "SESSION-152",
    "topic": "knowledge_provenance_audit_and_non_intrusive_sidecar_architecture",
    "decision": (
        "All parameter provenance MUST be tracked through a non-intrusive sidecar "
        "pattern (inspired by Envoy/Istio service mesh sidecars and eBPF kernel "
        "telemetry). The tracker NEVER modifies any float computation — it only "
        "reads knowledge bus state and parameter derivation paths. Each parameter "
        "is classified into one of six provenance types: KNOWLEDGE_DRIVEN, "
        "KNOWLEDGE_CLAMPED, VIBE_HEURISTIC, USER_OVERRIDE, BLUEPRINT_INHERITED, "
        "or HEURISTIC_FALLBACK. Parameters with no knowledge source MUST be "
        "honestly labeled as 'Heuristic Fallback / 代码硬编码死区' in the audit "
        "report. The audit backend self-registers via @register_backend "
        "(BackendType.PROVENANCE_AUDIT) and can be invoked standalone or as a "
        "pipeline terminal step. JSON audit logs are persisted to "
        "logs/knowledge_audit_trace.json for CI/CD consumption."
    ),
    "implication": (
        "The provenance audit system exposes that 50% of parameters (9/18) are "
        "in hardcoded dead zones, providing a precise attack target list for "
        "subsequent targeted fix campaigns. The non-intrusive design ensures "
        "zero risk of breaking existing pipeline computations. The JSON audit "
        "log enables continuous knowledge coverage monitoring in CI/CD. "
        "Five targeted fix campaigns have been identified: "
        "P1-SESSION-152-PROPORTIONS-KNOWLEDGE-BIND (4 params), "
        "P1-SESSION-152-ANIMATION-KNOWLEDGE-BIND (4 params), "
        "P1-SESSION-152-PHYSICS-GRAVITY-KNOWLEDGE-BIND (1 param), "
        "P1-SESSION-152-VIBE-KNOWLEDGE-VALIDATION (5 params), "
        "P2-SESSION-152-BACKEND-CONSUMPTION-AUDIT (AOP checkpoint)."
    ),
})
brain["architecture_decisions"] = arch_decisions

# --- Add design decision ---
design_decisions = brain.get("design_decisions", [])
design_decisions.append({
    "session_id": "SESSION-152",
    "topic": "provenance_six_level_classification_and_dead_zone_exposure",
    "decision": (
        "Parameter provenance classification uses exactly six levels: "
        "(1) KNOWLEDGE_DRIVEN — value within knowledge bus constraint range; "
        "(2) KNOWLEDGE_CLAMPED — value was clamped by knowledge constraint; "
        "(3) VIBE_HEURISTIC — adjusted by SEMANTIC_VIBE_MAP without knowledge validation; "
        "(4) USER_OVERRIDE — explicitly set by user in intent declaration; "
        "(5) BLUEPRINT_INHERITED — inherited from a base blueprint file; "
        "(6) HEURISTIC_FALLBACK — dataclass default with no external knowledge source. "
        "The audit report MUST display HEURISTIC_FALLBACK parameters with red warning "
        "markers and list them in a dedicated 'Dead Zone Inventory' section."
    ),
    "implication": (
        "This classification schema provides the exact granularity needed for "
        "targeted fix campaigns. Each dead-zone parameter has a clear remediation "
        "path: add the corresponding constraint to the knowledge directory and "
        "verify via re-running the audit. The six-level schema is extensible — "
        "future sources (e.g., A/B test results, user analytics) can be added "
        "as new ProvenanceSourceType enum values without breaking existing records."
    ),
})
brain["design_decisions"] = design_decisions

# --- Write back ---
BRAIN_PATH.write_text(
    json.dumps(brain, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print(f"[OK] PROJECT_BRAIN.json updated to {brain['version']}")
print(f"     last_session_id = {brain['last_session_id']}")
print(f"     total_iterations = {brain['total_iterations']}")
print(f"     total_code_lines = {brain['total_code_lines']}")
print(f"     active_total = {brain['gap_inventory']['active_total']}")
