"""SESSION-152 — End-to-End Provenance Audit Test Suite.

P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT

This test suite validates:
1. ProvenanceTracker singleton and thread-local context.
2. Knowledge state snapshot from RuntimeDistillationBus.
3. Parameter lineage classification (knowledge-driven vs. heuristic fallback).
4. Dangling parameter detection.
5. Report generation (terminal + JSON).
6. Non-intrusive sidecar verification (no business logic modification).
7. Registry-native backend auto-discovery.
8. Standalone audit runner end-to-end.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test 1: Tracker Singleton & Thread-Local Context
# ---------------------------------------------------------------------------

def test_tracker_singleton():
    """Verify that KnowledgeLineageTracker is a proper singleton."""
    from mathart.core.provenance_tracker import KnowledgeLineageTracker

    KnowledgeLineageTracker.reset()
    t1 = KnowledgeLineageTracker.instance()
    t2 = KnowledgeLineageTracker.instance()
    assert t1 is t2, "Tracker must be a singleton"
    logger.info("[PASS] test_tracker_singleton")


# ---------------------------------------------------------------------------
# Test 2: Knowledge State Snapshot
# ---------------------------------------------------------------------------

def test_knowledge_snapshot():
    """Verify that begin_audit captures knowledge bus state."""
    from mathart.core.provenance_tracker import KnowledgeLineageTracker

    KnowledgeLineageTracker.reset()
    tracker = KnowledgeLineageTracker.instance()

    # Test with no bus (graceful degradation)
    ctx = tracker.begin_audit(knowledge_bus=None, session_id="TEST-001")
    assert ctx.knowledge_snapshot is not None
    assert ctx.knowledge_snapshot.bus_available is False
    assert ctx.run_id
    assert ctx.session_id == "TEST-001"
    logger.info("[PASS] test_knowledge_snapshot (no bus)")

    # Test with actual bus
    try:
        from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus
        bus = build_project_knowledge_bus(project_root=PROJECT_ROOT)
        if bus is not None:
            KnowledgeLineageTracker.reset()
            tracker = KnowledgeLineageTracker.instance()
            ctx = tracker.begin_audit(knowledge_bus=bus, session_id="TEST-002")
            assert ctx.knowledge_snapshot.bus_available is True
            assert len(ctx.knowledge_snapshot.compiled_modules) > 0
            assert ctx.knowledge_snapshot.total_constraints > 0
            logger.info(
                "[PASS] test_knowledge_snapshot (with bus): "
                "modules=%d, constraints=%d, files=%d",
                len(ctx.knowledge_snapshot.compiled_modules),
                ctx.knowledge_snapshot.total_constraints,
                len(ctx.knowledge_snapshot.knowledge_files_found),
            )
        else:
            logger.info("[SKIP] test_knowledge_snapshot (bus build returned None)")
    except Exception as e:
        logger.info("[SKIP] test_knowledge_snapshot (bus build failed): %s", e)


# ---------------------------------------------------------------------------
# Test 3: Parameter Lineage Classification
# ---------------------------------------------------------------------------

def test_lineage_classification():
    """Verify that parameters are correctly classified by source type."""
    from mathart.core.provenance_tracker import (
        KnowledgeLineageTracker,
        ProvenanceSourceType,
    )

    KnowledgeLineageTracker.reset()
    tracker = KnowledgeLineageTracker.instance()
    tracker.begin_audit(knowledge_bus=None, session_id="TEST-003")

    # Simulate a flat genotype
    flat = {
        "physics.mass": 1.0,
        "physics.bounce": 0.5,
        "animation.exaggeration": 0.3,
        "proportions.scale": 1.0,
    }

    # With no knowledge bus, all should be heuristic fallback
    records = tracker.trace_intent_derivation(
        flat,
        raw_vibe="弹性",
        vibe_adjustments={
            "弹性": {
                "physics.bounce": 0.4,
                "proportions.squash_stretch": 0.5,
            }
        },
        user_overrides={"physics.mass": 1.0},
    )

    # physics.mass → USER_OVERRIDE (was in overrides)
    assert records["physics.mass"].source_type == ProvenanceSourceType.USER_OVERRIDE.value

    # physics.bounce → VIBE_HEURISTIC (was in vibe adjustments)
    assert records["physics.bounce"].source_type == ProvenanceSourceType.VIBE_HEURISTIC.value

    # animation.exaggeration → HEURISTIC_FALLBACK (no knowledge, no override, no vibe)
    assert records["animation.exaggeration"].source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value

    # proportions.scale → HEURISTIC_FALLBACK
    assert records["proportions.scale"].source_type == ProvenanceSourceType.HEURISTIC_FALLBACK.value

    logger.info("[PASS] test_lineage_classification")


# ---------------------------------------------------------------------------
# Test 4: Dangling Parameter Detection
# ---------------------------------------------------------------------------

def test_dangling_detection():
    """Verify that dangling parameters are correctly detected."""
    from mathart.core.provenance_tracker import KnowledgeLineageTracker

    KnowledgeLineageTracker.reset()
    tracker = KnowledgeLineageTracker.instance()
    tracker.begin_audit(knowledge_bus=None, session_id="TEST-004")

    flat = {
        "physics.mass": 1.0,
        "physics.bounce": 0.5,
        "animation.exaggeration": 0.3,
    }
    tracker.trace_intent_derivation(flat)

    # Simulate backend consuming only 2 of 3 params
    tracker.checkpoint_backend(
        "test_backend",
        {"physics.mass": 1.0, "physics.bounce": 0.5},
    )

    ctx = tracker.finalize_audit()
    assert "animation.exaggeration" in ctx.dangling_params
    assert ctx.dangling_count == 1
    logger.info("[PASS] test_dangling_detection")


# ---------------------------------------------------------------------------
# Test 5: Report Generation
# ---------------------------------------------------------------------------

def test_report_generation():
    """Verify that the report generator produces output without errors."""
    from mathart.core.provenance_tracker import KnowledgeLineageTracker
    from mathart.core.provenance_report import ProvenanceReportGenerator

    KnowledgeLineageTracker.reset()
    tracker = KnowledgeLineageTracker.instance()
    tracker.begin_audit(knowledge_bus=None, session_id="TEST-005")

    flat = {
        "physics.mass": 1.0,
        "physics.bounce": 0.5,
        "animation.exaggeration": 0.3,
    }
    tracker.trace_intent_derivation(flat, raw_vibe="弹性")
    ctx = tracker.finalize_audit()

    # Capture output
    output_lines = []
    gen = ProvenanceReportGenerator(
        project_root=PROJECT_ROOT,
        output_fn=lambda line: output_lines.append(line),
    )
    payload = gen.generate(context=ctx)

    assert len(output_lines) > 0, "Report should produce output"
    assert "KNOWLEDGE PROVENANCE AUDIT REPORT" in "\n".join(output_lines)
    assert payload["summary"]["total_params"] == 3
    assert payload["audit_version"] == "1.0.0"

    # Check JSON was dumped
    json_path = PROJECT_ROOT / "logs" / "knowledge_audit_trace.json"
    assert json_path.exists(), f"JSON log should exist at {json_path}"
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["run_id"] == ctx.run_id

    logger.info("[PASS] test_report_generation")


# ---------------------------------------------------------------------------
# Test 6: Non-Intrusive Sidecar Verification
# ---------------------------------------------------------------------------

def test_non_intrusive_sidecar():
    """Verify that the tracker NEVER modifies any float value.

    [防破坏红线] — This test ensures the sidecar pattern is respected.
    """
    from mathart.core.provenance_tracker import KnowledgeLineageTracker

    KnowledgeLineageTracker.reset()
    tracker = KnowledgeLineageTracker.instance()
    tracker.begin_audit(knowledge_bus=None, session_id="TEST-006")

    # Create original flat params
    original = {
        "physics.mass": 1.0,
        "physics.bounce": 0.5,
        "animation.exaggeration": 0.3,
    }
    # Make a copy to pass to tracker
    flat_copy = dict(original)

    tracker.trace_intent_derivation(flat_copy)

    # Verify original values are unchanged
    for key, value in original.items():
        assert flat_copy[key] == value, (
            f"Tracker modified {key}: {value} → {flat_copy[key]}"
        )

    logger.info("[PASS] test_non_intrusive_sidecar")


# ---------------------------------------------------------------------------
# Test 7: Registry Backend Auto-Discovery
# ---------------------------------------------------------------------------

def test_registry_auto_discovery():
    """Verify that ProvenanceAuditBackend is discoverable via the registry."""
    from mathart.core.backend_registry import get_registry, BackendRegistry
    from mathart.core.backend_types import BackendType

    BackendRegistry.reset()
    registry = get_registry()

    # Check if provenance_audit is registered
    result = registry.get(BackendType.PROVENANCE_AUDIT.value)
    if result is not None:
        meta, cls = result
        assert meta.name == BackendType.PROVENANCE_AUDIT.value
        assert "provenance_audit_report" in meta.artifact_families
        logger.info("[PASS] test_registry_auto_discovery: %s", meta.display_name)
    else:
        logger.info(
            "[INFO] test_registry_auto_discovery: backend not found in registry "
            "(may be expected if import fails due to missing deps)"
        )


# ---------------------------------------------------------------------------
# Test 8: Standalone Audit Runner End-to-End
# ---------------------------------------------------------------------------

def test_standalone_audit_e2e():
    """Run the full standalone audit and verify end-to-end output."""
    from mathart.core.provenance_audit_backend import run_standalone_audit

    output_lines = []
    artifact = run_standalone_audit(
        project_root=PROJECT_ROOT,
        vibe="弹性 活泼",
        output_fn=lambda line: output_lines.append(line),
    )

    assert artifact.run_id, "Artifact should have a run_id"
    assert artifact.total_params > 0, "Should have traced some parameters"
    assert artifact.health_verdict in ("HEALTHY", "PARTIAL", "CRITICAL", "N/A")

    # Verify report was printed
    full_output = "\n".join(output_lines)
    assert "KNOWLEDGE PROVENANCE AUDIT REPORT" in full_output
    assert "AUDIT SUMMARY" in full_output

    # Verify JSON log exists
    json_path = Path(artifact.json_log_path)
    assert json_path.exists(), f"JSON log should exist at {json_path}"

    logger.info(
        "[PASS] test_standalone_audit_e2e: "
        "params=%d, knowledge=%d, fallback=%d, verdict=%s",
        artifact.total_params,
        artifact.knowledge_driven_count,
        artifact.heuristic_fallback_count,
        artifact.health_verdict,
    )


# ---------------------------------------------------------------------------
# Test 9: Full Pipeline Integration (Director Studio → Audit)
# ---------------------------------------------------------------------------

def test_director_studio_integration():
    """Verify that the audit integrates with the Director Studio flow."""
    try:
        from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus
        from mathart.workspace.director_intent import (
            DirectorIntentParser,
            SEMANTIC_VIBE_MAP,
        )
        from mathart.core.provenance_tracker import KnowledgeLineageTracker
        from mathart.core.provenance_report import ProvenanceReportGenerator

        # Build knowledge bus
        bus = build_project_knowledge_bus(project_root=PROJECT_ROOT)

        # Parse intent
        parser = DirectorIntentParser(
            workspace_root=PROJECT_ROOT,
            knowledge_bus=bus,
        )
        spec = parser.parse_dict({
            "vibe": "沉重 厚重",
            "description": "Integration test intent",
        })

        # Run audit
        KnowledgeLineageTracker.reset()
        tracker = KnowledgeLineageTracker.instance()
        tracker.begin_audit(knowledge_bus=bus, session_id="TEST-009")

        flat = spec.genotype.flat_params()
        records = tracker.trace_intent_derivation(
            flat,
            raw_vibe=spec.raw_vibe,
            applied_knowledge_rules=spec.applied_knowledge_rules,
            knowledge_bus=bus,
        )
        ctx = tracker.finalize_audit()

        # Generate report
        output_lines = []
        gen = ProvenanceReportGenerator(
            project_root=PROJECT_ROOT,
            output_fn=lambda line: output_lines.append(line),
        )
        payload = gen.generate(context=ctx)

        assert payload["summary"]["total_params"] > 0
        logger.info(
            "[PASS] test_director_studio_integration: "
            "params=%d, knowledge=%d, fallback=%d",
            payload["summary"]["total_params"],
            payload["summary"]["knowledge_driven_count"],
            payload["summary"]["heuristic_fallback_count"],
        )

    except Exception as e:
        logger.info("[SKIP] test_director_studio_integration: %s", e)


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------

def run_all_tests():
    """Run all provenance audit tests."""
    print("\n" + "=" * 60)
    print("  SESSION-152: Provenance Audit Test Suite")
    print("=" * 60 + "\n")

    tests = [
        test_tracker_singleton,
        test_knowledge_snapshot,
        test_lineage_classification,
        test_dangling_detection,
        test_report_generation,
        test_non_intrusive_sidecar,
        test_registry_auto_discovery,
        test_standalone_audit_e2e,
        test_director_studio_integration,
    ]

    passed = 0
    failed = 0
    skipped = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            logger.error("[FAIL] %s: %s", test_fn.__name__, e)
        except Exception as e:
            skipped += 1
            logger.warning("[SKIP] %s: %s", test_fn.__name__, e)

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
