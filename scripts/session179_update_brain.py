"""SESSION-179: Update PROJECT_BRAIN.json with session results."""

import json
from datetime import datetime

with open("PROJECT_BRAIN.json", "r") as f:
    brain = json.load(f)

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
today = datetime.now().strftime("%Y-%m-%d")

# ── Update top-level metadata ──
brain["last_session_id"] = "SESSION-179"
brain["last_updated"] = now
brain["current_session"] = "SESSION-179"
brain["total_iterations"] = brain.get("total_iterations", 0) + 1

# ── Add SESSION-179 to session_log ──
session_entry = {
    "session_id": "SESSION-179",
    "date": today,
    "title": "Visual Distillation & Reskinning + SESSION-176 Research Landing",
    "summary": (
        "Full landing of SESSION-176 research findings: "
        "(1) SparseCtrl-RGB end_percent time-window clamping (0.4~0.6, strength 0.825~0.9) "
        "to eliminate long-shot flashing and color drift; "
        "(2) Normal Map encoding formula (image*127.5+127.5) with (128,128,255) base pad verified; "
        "(3) cancel_futures global meltdown via executor.shutdown(cancel_futures=True); "
        "(4) Dynamic batch_size safety bounds [1, 128]. "
        "Additionally deployed three new creative paradigms in the Director Studio: "
        "[D] Visual Distillation Gateway (GIF-to-Physics reverse engineering via Vision LLM), "
        "Style Retargeting (motion skeleton reuse with vibe override), "
        "and Blueprint Vault (custom naming with timestamp fallback). "
        "UX banner upgraded with SESSION-179 status lines. "
        "USER_GUIDE.md updated with pipeline unclamping declaration. "
        "24/24 tests passing."
    ),
    "files_modified": [
        "mathart/backend/ai_render_stream_backend.py",
        "mathart/level/pdg.py",
        "mathart/factory/mass_production.py",
        "mathart/cli_wizard.py",
        "mathart/quality/interactive_gate.py",
        "mathart/workspace/visual_distillation.py (NEW)",
        "tests/test_session179_visual_distillation_and_reskinning.py (NEW)",
        "tests/test_session178_full_sync_and_true_abort.py",
        "docs/USER_GUIDE.md",
    ],
    "tests_added": 15,
    "tests_total_passing": 24,
}
if "session_log" not in brain:
    brain["session_log"] = []
brain["session_log"].append(session_entry)

# ── Add to recent_sessions ──
if "recent_sessions" not in brain:
    brain["recent_sessions"] = []
brain["recent_sessions"].append({
    "id": "SESSION-179",
    "date": today,
    "title": session_entry["title"],
})
# Keep only last 10
brain["recent_sessions"] = brain["recent_sessions"][-10:]

# ── Update key_landings ──
if "key_landings" not in brain:
    brain["key_landings"] = []
brain["key_landings"].extend([
    {
        "session": "SESSION-179",
        "feature": "SparseCtrl-RGB Time-Window Clamping",
        "module": "ai_render_stream_backend.py",
        "description": "end_percent clamped to 0.55, strength to 0.825~0.9 sweet spot for SparseCtrl nodes",
    },
    {
        "session": "SESSION-179",
        "feature": "cancel_futures Global Meltdown",
        "module": "pdg.py",
        "description": "executor.shutdown(wait=False, cancel_futures=True) on fatal exception",
    },
    {
        "session": "SESSION-179",
        "feature": "Visual Distillation Gateway",
        "module": "visual_distillation.py + cli_wizard.py",
        "description": "GIF-to-Physics reverse engineering via Vision LLM (gpt-4o-mini)",
    },
    {
        "session": "SESSION-179",
        "feature": "Style Retargeting",
        "module": "cli_wizard.py",
        "description": "Motion skeleton reuse with vibe override in Blueprint Derivation mode",
    },
    {
        "session": "SESSION-179",
        "feature": "Blueprint Vault",
        "module": "interactive_gate.py",
        "description": "Custom naming with timestamp fallback for blueprint saves",
    },
])

# ── Update pending_tasks — mark SESSION-176 research as LANDED ──
for task in brain.get("pending_tasks", []):
    if "SESSION-176" in task.get("id", "") or "SESSION-176" in task.get("title", ""):
        task["status"] = "LANDED_IN_SESSION_179"

# ── Add SESSION-179 completed task ──
if "completed_tasks" not in brain:
    brain["completed_tasks"] = []
brain["completed_tasks"].append({
    "id": "SESSION-179-VISUAL-DISTILLATION-RESKINNING",
    "title": "Visual Distillation & Reskinning + SESSION-176 Research Full Landing",
    "session_landed": "SESSION-179",
    "date": today,
    "status": "COMPLETED",
    "modules": [
        "mathart/backend/ai_render_stream_backend.py",
        "mathart/level/pdg.py",
        "mathart/factory/mass_production.py",
        "mathart/cli_wizard.py",
        "mathart/quality/interactive_gate.py",
        "mathart/workspace/visual_distillation.py",
    ],
    "tests": [
        "tests/test_session179_visual_distillation_and_reskinning.py",
    ],
})

# ── Update architecture_decisions ──
if "architecture_decisions" not in brain:
    brain["architecture_decisions"] = []
brain["architecture_decisions"].extend([
    {
        "session": "SESSION-179",
        "decision": "SparseCtrl nodes (strength >= 0.8) are classified as RGB temporal controllers and clamped to 0.825~0.9 sweet spot with end_percent <= 0.55",
        "rationale": "SESSION-176 research: GitHub #476 confirms end_percent 0.4~0.6 eliminates long-shot flashing",
    },
    {
        "session": "SESSION-179",
        "decision": "Vision LLM (gpt-4o-mini) used for GIF-to-Physics reverse engineering with graceful fallback to DEFAULT_PHYSICS_PARAMS",
        "rationale": "Zero-crash guarantee: API failure returns safe defaults, never blocks pipeline",
    },
    {
        "session": "SESSION-179",
        "decision": "cv2 is FORBIDDEN in visual_distillation.py — PIL.ImageSequence only",
        "rationale": "Prevent OpenCV DLL hell on Windows; PIL is already a dependency",
    },
])

# ── Update red_lines ──
if "red_lines" not in brain:
    brain["red_lines"] = []
brain["red_lines"].append({
    "session": "SESSION-179",
    "rule": "visual_distillation.py must NEVER import cv2 — PIL.ImageSequence only",
    "enforced_by": "test_no_cv2_import in test_session179",
})

# ── Write back ──
with open("PROJECT_BRAIN.json", "w") as f:
    json.dump(brain, f, indent=2, ensure_ascii=False)

print(f"[BRAIN] PROJECT_BRAIN.json updated — SESSION-179 landed")
print(f"[BRAIN] session_log entries: {len(brain['session_log'])}")
print(f"[BRAIN] key_landings entries: {len(brain['key_landings'])}")
print(f"[BRAIN] completed_tasks entries: {len(brain['completed_tasks'])}")
