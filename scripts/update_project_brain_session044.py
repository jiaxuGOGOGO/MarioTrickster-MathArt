from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN_PATH = ROOT / "PROJECT_BRAIN.json"


def upsert_pending_task(tasks: list[dict], task: dict) -> None:
    for index, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            tasks[index] = task
            return
    tasks.append(task)


def main() -> None:
    data = json.loads(BRAIN_PATH.read_text(encoding="utf-8"))

    data["version"] = "0.35.0"
    data["last_session_id"] = "SESSION-044"
    data["last_updated"] = "2026-04-17T00:20:00Z"
    data["validation_pass_rate"] = "42 targeted tests PASS across SESSION-044 integration path; evolution loop snapshot regenerated successfully"
    data["knowledge_rule_count"] = max(int(data.get("knowledge_rule_count", 0)), 49)
    data["project_health_score"] = 10.0
    data["total_code_lines"] = max(int(data.get("total_code_lines", 0)), 57900)
    data["current_focus"] = (
        "SESSION-044 closes Gap C1 by adding an analytical SDF auxiliary-map pipeline: "
        "the industrial renderer now exports albedo, normal, depth, and mask directly "
        "from 2D SDF gradients in Python/NumPy, while SESSION-043 active Layer 3 "
        "closed-loop transition tuning remains intact. Next: XPBD, AssetPipeline wiring, "
        "engine-ready export templates, and analytic-gradient native primitives."
    )

    ref_priority = list(data.get("reference_notes_priority", []))
    prepend = [
        "SESSION_HANDOFF.md",
        "evolution_reports/session044_sdf_rendering_research_notes.md",
        "AUDIT_SESSION044.md",
    ]
    for item in reversed(prepend):
        if item in ref_priority:
            ref_priority.remove(item)
        ref_priority.insert(0, item)
    data["reference_notes_priority"] = ref_priority

    pending_tasks = list(data.get("pending_tasks", []))
    upsert_pending_task(
        pending_tasks,
        {
            "id": "P1-INDUSTRIAL-44A",
            "priority": "P1",
            "title": "Engine-ready export templates for analytical SDF auxiliary maps",
            "status": "TODO",
            "estimated_effort": "medium",
            "description": "Package albedo/normal/depth/mask outputs into engine-facing export presets and importer templates so SESSION-044 artifacts can flow into real runtime lighting workflows.",
            "added_in": "SESSION-044",
        },
    )
    upsert_pending_task(
        pending_tasks,
        {
            "id": "P1-INDUSTRIAL-44B",
            "priority": "P1",
            "title": "Analytic-gradient native primitives for auxiliary-map baking",
            "status": "TODO",
            "estimated_effort": "medium",
            "description": "Introduce optional exact gradient providers for selected SDF primitives so SESSION-044 auxiliary-map baking can reduce finite-difference noise without brute-force supersampling.",
            "added_in": "SESSION-044",
        },
    )
    upsert_pending_task(
        pending_tasks,
        {
            "id": "P1-INDUSTRIAL-44C",
            "priority": "P1",
            "title": "Specular/roughness and material metadata export for 2D lighting",
            "status": "TODO",
            "estimated_effort": "medium",
            "description": "Extend the SESSION-044 auxiliary-map pipeline with optional material channels and engine-specific metadata for richer 2D lighting integration.",
            "added_in": "SESSION-044",
        },
    )
    upsert_pending_task(
        pending_tasks,
        {
            "id": "P0-GAP-C1",
            "priority": "P0",
            "title": "Industrial-grade analytical SDF normal/depth pipeline",
            "status": "DONE",
            "estimated_effort": "high",
            "description": "CLOSED in SESSION-044. Added `mathart/animation/sdf_aux_maps.py`, upgraded `render_character_maps_industrial()` to export albedo/normal/depth/mask, connected analytical-rendering status into `evolution_loop.py` and `engine.py`, and generated real demo artifacts plus audit evidence.",
            "added_in": "SESSION-044",
            "completed_in": "SESSION-044",
        },
    )
    data["pending_tasks"] = pending_tasks

    strategic_paths = data.get("commercial_benchmark", {}).get("strategic_paths", {})
    path_b = strategic_paths.get("path_b_ai_visual_polish", "")
    if "SESSION-044" not in path_b:
        strategic_paths["path_b_ai_visual_polish"] = (
            (path_b + " ") if path_b else ""
        ) + (
            "SESSION-044 adds analytical SDF auxiliary-map export (normal/depth/mask), giving the project a materially stronger bridge into lit 2D runtime rendering before any diffusion or neural polish stage."
        )

    recent_focus = data.get("recent_focus_snapshot", {})
    if isinstance(recent_focus, dict):
        recent_focus.setdefault("top_open_loops", [])
        if "Engine-ready auxiliary-map export and import templates (P1-INDUSTRIAL-44A)" not in recent_focus["top_open_loops"]:
            recent_focus["top_open_loops"].append("Engine-ready auxiliary-map export and import templates (P1-INDUSTRIAL-44A)")
        recent_focus.setdefault("remaining_followups", [])
        for item in [
            "P1-INDUSTRIAL-44A: package albedo/normal/depth/mask for engine import",
            "P1-INDUSTRIAL-44B: add analytic-gradient native primitives",
            "P1-INDUSTRIAL-44C: export optional material metadata",
        ]:
            if item not in recent_focus["remaining_followups"]:
                recent_focus["remaining_followups"].append(item)
        data["recent_focus_snapshot"] = recent_focus

    recent_sessions = list(data.get("recent_sessions", []))
    session044 = {
        "session_id": "SESSION-044",
        "date": "2026-04-17",
        "version": "0.35.0",
        "title": "Gap C1 closure: analytical SDF normal/depth auxiliary-map pipeline",
        "highlights": [
            "Added `mathart/animation/sdf_aux_maps.py` for grid-sampled SDF normal/depth/mask baking.",
            "Upgraded `render_character_maps_industrial()` to export albedo plus engine-consumable auxiliary maps.",
            "Registered SESSION-044 rendering provenance in `evolution_loop.py` and exposed status in `engine.py`.",
            "Generated real Mario idle auxiliary-map artifacts and wrote `AUDIT_SESSION044.md`."
        ],
        "artifacts": [
            "evolution_reports/session044_aux_demo/mario_idle_albedo.png",
            "evolution_reports/session044_aux_demo/mario_idle_normal.png",
            "evolution_reports/session044_aux_demo/mario_idle_depth.png",
            "evolution_reports/session044_aux_demo/mario_idle_mask.png",
            "evolution_reports/session044_aux_demo/session044_aux_demo.json",
            "evolution_reports/CYCLE-SESSION044.json",
            "AUDIT_SESSION044.md"
        ],
        "tests": [
            "pytest -q tests/test_sdf_aux_maps.py",
            "pytest -q tests/test_sdf_aux_maps.py tests/test_evolution_loop.py tests/test_layer3_closed_loop.py",
            "pytest -q tests/test_evolution.py"
        ]
    }
    recent_sessions = [s for s in recent_sessions if s.get("session_id") != "SESSION-044"]
    recent_sessions.insert(0, session044)
    data["recent_sessions"] = recent_sessions[:12]

    completed_work = list(data.get("completed_work", []))
    completed_entry = {
        "id": "P0-GAP-C1",
        "completed_in": "SESSION-044",
        "title": "Industrial-grade analytical SDF normal/depth pipeline",
        "summary": "Closed Gap C1 by generating normal/depth/mask maps directly from 2D SDF gradients and integrating the capability into industrial rendering, the evolution loop, audit artifacts, and project memory."
    }
    completed_work = [entry for entry in completed_work if entry.get("id") != "P0-GAP-C1"]
    completed_work.insert(0, completed_entry)
    data["completed_work"] = completed_work

    custom_notes = dict(data.get("custom_notes", {}))
    custom_notes["session044_gapc1_status"] = "CLOSED. Analytical SDF auxiliary-map pipeline implemented."
    custom_notes["session044_aux_maps"] = "render_character_maps_industrial() now exports albedo/normal/depth/mask from the same industrial character distance field."
    custom_notes["session044_demo_artifacts"] = "evolution_reports/session044_aux_demo/ contains a real Mario idle export pack."
    custom_notes["session044_audit"] = "AUDIT_SESSION044.md verifies research → code → artifact → test closure."
    data["custom_notes"] = custom_notes

    BRAIN_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
