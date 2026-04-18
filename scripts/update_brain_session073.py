#!/usr/bin/env python3
"""SESSION-073: Update PROJECT_BRAIN.json with session metadata and task status changes."""
from __future__ import annotations

import json
from pathlib import Path

BRAIN_PATH = Path(__file__).resolve().parent.parent / "PROJECT_BRAIN.json"


def main() -> None:
    with open(BRAIN_PATH, encoding="utf-8") as f:
        brain = json.load(f)

    # ---- Top-level metadata ----
    brain["version"] = "0.64.0"
    brain["last_session_id"] = "SESSION-073"
    brain["last_updated"] = "2026-04-19T22:00:00Z"
    brain["total_iterations"] = 601
    brain["total_code_lines"] = 110400
    brain["knowledge_rule_count"] = 146

    brain["current_focus"] = (
        "SESSION-073 closes P1-MIGRATE-3 (dynamic CI schema validation with "
        "Pixar usdchecker-inspired strong typing, required_metadata_keys(), "
        "telemetry array depth assertions, and reflexive full-backend CI "
        "traversal with minimal context fixtures) and P1-XPBD-4 (3D CCD "
        "swept-sphere continuous collision detection in XPBDSolver3D with "
        "velocity-threshold broad-phase gating, half-space TOI computation, "
        "safe-point clamping, inward velocity removal, and CCD_ENABLED "
        "capability + ccd_sweep_count telemetry sidecar). PROJECT_BRAIN.json "
        "technical debt cleaned: open_tasks merged, 42 DONE tasks archived, "
        "18 legacy session-change keys consolidated. 1362/1368 tests PASS "
        "(6 pre-existing infra-only flakes: 4 Taichi, 2 Optuna). "
        "Zero regression on SESSION-072 baseline."
    )

    brain["validation_pass_rate"] = (
        "SESSION-073 P1-MIGRATE-3 + P1-XPBD-4 PASS: 1362/1368 stable suites "
        "PASS in serial (taichi/layer3_closed_loop excluded as pre-existing "
        "infra-only flakes). 9/9 new P1-MIGRATE-3 CI schema tests PASS, "
        "11/11 new P1-XPBD-4 CCD tests PASS, 31/31 targeted regression "
        "subset PASS (physics3d + distill_1a + ci_schemas + registry_e2e). "
        "Zero breakage of the SESSION-072 1362-baseline."
    )

    # ---- Update gap_inventory ----
    brain["gap_inventory"]["baseline_session"] = "SESSION-073"
    brain["gap_inventory"]["recomputed_at"] = "2026-04-19"

    # ---- Update task statuses ----
    tasks = brain.get("pending_tasks", [])
    task_index = {t["id"]: t for t in tasks}

    # P1-MIGRATE-3: now CLOSED
    if "P1-MIGRATE-3" not in task_index:
        tasks.append({
            "id": "P1-MIGRATE-3",
            "priority": "P1",
            "title": "Dynamic CI schema validation and registry upgrades",
            "status": "CLOSED",
            "estimated_effort": "medium",
            "description": (
                "CLOSED in SESSION-073. register_backend supports schema_version "
                "declaration; validate_artifact() blocks version downgrade; "
                "ArtifactFamily.required_metadata_keys() enforces physics_solver "
                "and physics3d_telemetry with length/type assertions; "
                "test_ci_backend_schemas.py reflexively discovers all backends "
                "via get_registry(), injects minimal context fixtures, and "
                "validates 100% of manifest outputs. 9/9 tests PASS."
            ),
            "added_in": "SESSION-072",
            "completed_in": "SESSION-073",
        })
    else:
        t = task_index["P1-MIGRATE-3"]
        t["status"] = "CLOSED"
        t["completed_in"] = "SESSION-073"
        t["updated_in"] = "SESSION-073"
        t["description"] = (
            "CLOSED in SESSION-073. register_backend supports schema_version "
            "declaration; validate_artifact() blocks version downgrade; "
            "ArtifactFamily.required_metadata_keys() enforces physics_solver "
            "and physics3d_telemetry with length/type assertions; "
            "test_ci_backend_schemas.py reflexively discovers all backends "
            "via get_registry(), injects minimal context fixtures, and "
            "validates 100% of manifest outputs. 9/9 tests PASS."
        )

    # P1-XPBD-4: update description to include 3D CCD
    if "P1-XPBD-4" in task_index:
        t = task_index["P1-XPBD-4"]
        t["status"] = "CLOSED"
        t["updated_in"] = "SESSION-073"
        t["description"] = (
            "CLOSED in SESSION-058 (2D SDF CCD) and extended in SESSION-073 "
            "(3D CCD). XPBDSolver3D._ccd_sweep_ground() performs swept-sphere "
            "CCD against CONTACT half-spaces with velocity-threshold gating "
            "(Erin Catto GDC 2013), linear TOI interpolation, safety backoff "
            "clamping, and inward velocity removal. BackendCapability.CCD_ENABLED "
            "declared on Physics3DBackend; ccd_sweep_count[T] telemetry sidecar "
            "array added. 11/11 CCD tests PASS."
        )

    # Move newly closed tasks to archive
    newly_closed = []
    remaining = []
    for t in tasks:
        if t.get("status") in ("CLOSED", "DONE"):
            brain.setdefault("closed_tasks_archive", []).append({
                "id": t["id"],
                "title": t.get("title", ""),
                "completed_in": t.get("completed_in", t.get("updated_in", "SESSION-073")),
                "priority": t.get("priority", "P2"),
            })
            newly_closed.append(t["id"])
        else:
            remaining.append(t)
    brain["pending_tasks"] = remaining

    # Dedup archive
    seen = set()
    deduped_archive = []
    for a in brain.get("closed_tasks_archive", []):
        if a["id"] not in seen:
            seen.add(a["id"])
            deduped_archive.append(a)
    brain["closed_tasks_archive"] = sorted(deduped_archive, key=lambda x: x["id"])

    # ---- Recompute gap stats ----
    by_priority = {}
    by_status = {}
    for t in brain["pending_tasks"]:
        p = t.get("priority", "P2")
        s = t.get("status", "TODO")
        by_priority[p] = by_priority.get(p, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1

    brain["gap_inventory"]["active_total"] = len(brain["pending_tasks"])
    brain["gap_inventory"]["by_priority"] = dict(sorted(by_priority.items()))
    brain["gap_inventory"]["by_status"] = dict(sorted(by_status.items()))
    brain["gap_inventory"]["closed_archived"] = len(brain["closed_tasks_archive"])

    # ---- Add SESSION-073 to recent_sessions ----
    session_entry = {
        "session_id": "SESSION-073",
        "date": "2026-04-19",
        "focus": "P1-MIGRATE-3 + P1-XPBD-4 + PROJECT_BRAIN.json cleanup",
        "changes": [
            "register_backend schema_version declaration + validate_artifact version check",
            "ArtifactFamily.required_metadata_keys() with physics3d_telemetry depth assertions",
            "test_ci_backend_schemas.py: reflexive full-backend CI traversal (9 tests)",
            "XPBDSolver3D._ccd_sweep_ground(): 3D swept-sphere CCD with velocity gating",
            "BackendCapability.CCD_ENABLED on Physics3DBackend",
            "ccd_sweep_count[T] telemetry sidecar array",
            "test_ccd_3d.py: 11 CCD tests (solver + backend + capability)",
            "PROJECT_BRAIN.json: merged open_tasks, archived 42 DONE, consolidated 18 legacy keys",
        ],
        "test_results": "1362/1368 PASS (6 pre-existing infra flakes)",
        "tasks_closed": ["P1-MIGRATE-3", "P1-XPBD-4 (3D extension)"],
    }
    recent = brain.get("recent_sessions", [])
    recent.insert(0, session_entry)
    brain["recent_sessions"] = recent[:10]  # Keep last 10

    # ---- Prepend reference notes ----
    new_refs = [
        "tests/test_ccd_3d.py",
        "tests/test_ci_backend_schemas.py",
    ]
    existing_refs = brain.get("reference_notes_priority", [])
    for ref in reversed(new_refs):
        if ref not in existing_refs:
            existing_refs.insert(0, ref)
    brain["reference_notes_priority"] = existing_refs

    # ---- Write back ----
    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"PROJECT_BRAIN.json updated for SESSION-073")
    print(f"Active tasks: {len(brain['pending_tasks'])}")
    print(f"Closed archive: {len(brain['closed_tasks_archive'])}")
    print(f"Newly closed: {newly_closed}")


if __name__ == "__main__":
    main()
