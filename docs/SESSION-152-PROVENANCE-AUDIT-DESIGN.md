# SESSION-152: Knowledge Provenance Audit — Architecture Design Document

## 1. Industrial & Academic Reference Alignment

### 1.1 OpenLineage (Data Provenance & Lineage Tracking)
- **Core Principle**: Every data transformation must emit a lineage event containing `run`, `job`, `inputs[]`, and `outputs[]` facets.
- **Application**: Each parameter in our pipeline is wrapped in a `ProvenanceRecord` that carries its origin context (heuristic fallback vs. distilled knowledge rule), the specific knowledge file and rule ID that drove it, and the derivation reason.
- **Key Insight**: Parameters must NOT be bare floats — they must be "provenance-wrapped" values that can be traced back to their source.

### 1.2 Explainable AI (XAI) in Procedural Generation
- **Core Principle**: The system's reasoning must be transparent and auditable. A "Knowledge Mapping Audit Trail" must prove that high-dimensional semantic intent was correctly mapped to low-dimensional physical parameters.
- **Application**: The audit report table maps `[Final Parameter] → [Actual Value] → [Knowledge Source] → [Derivation Reason]`, making the entire derivation chain visible.

### 1.3 Sidecar/Interceptor Pattern & Non-Intrusive Telemetry
- **Core Principle**: Observability must be added as a sidecar — never modifying the existing business logic.
- **Application**: The `ProvenanceTracker` is a pure observer. It reads from the knowledge bus and intent spec but NEVER modifies any computation. It uses Python's threading.local() for context propagation without altering function signatures.

## 2. Architecture Design

### 2.1 Module Layout
```
mathart/core/provenance_tracker.py    — Core tracker (singleton + thread-local context)
mathart/core/provenance_report.py     — Report generator (tabulate + JSON dump)
mathart/core/provenance_audit_backend.py — Registry-native backend plugin
tests/test_provenance_audit.py        — Full test suite
```

### 2.2 Data Flow (Non-Intrusive Sidecar)
```
[Knowledge Bus refresh]
       │
       ▼
[ProvenanceTracker.snapshot_knowledge_state()]  ← Captures all compiled spaces + rules
       │
       ▼
[DirectorIntentParser.parse_dict()]
       │
       ▼
[ProvenanceTracker.trace_parameter_derivation()]  ← For EACH parameter:
       │                                              - Was it driven by knowledge?
       │                                              - Which file/rule/theory?
       │                                              - Or is it heuristic fallback?
       ▼
[CreatorIntentSpec created]
       │
       ▼
[ProvenanceTracker.attach_to_context()]  ← Attaches audit metadata to global context
       │
       ▼
[Pipeline nodes / Backend execution]
       │
       ▼
[ProvenanceTracker.intercept_at_terminal()]  ← Extracts audit at pipeline end
       │
       ▼
[ProvenanceReportGenerator.generate()]  ← Prints table + dumps JSON
```

### 2.3 Key Design Decisions

1. **Honest Fallback Labeling**: If a parameter has NO knowledge source, it MUST be labeled `[Heuristic Fallback / 代码硬编码死区]`. No fake provenance.
2. **Dangling Parameter Detection**: Parameters present in intent but missing from backend execution are flagged as `[WARNING: 悬空未被使用的废弃参数]`.
3. **Thread-Safe Singleton**: Uses `threading.local()` for per-session audit context.
4. **Zero Business Logic Impact**: The tracker NEVER modifies any float value or computation path.

## 3. Anti-Pattern Red Lines

| Red Line | Enforcement |
|----------|-------------|
| No fake provenance | Tracker reads actual knowledge bus state; fallback is explicitly labeled |
| No business logic modification | Tracker is read-only; all existing computations unchanged |
| No dangling parameters | Terminal interceptor compares intent params vs. backend-consumed params |
| Registry Pattern compliance | Audit backend registered via `@register_backend` |
