from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "PROJECT_BRAIN.json"

LAST_UPDATED = "2026-04-22T08:40:00Z"
VALIDATION = (
    "SESSION-137 audit: 45/45 targeted PASS across tests/test_hitl_boundary_gateway.py, "
    "tests/test_dual_wizard_dispatcher.py, tests/test_preflight_radar.py, and "
    "tests/test_idempotent_surgeon.py. Landed HITL bounded-autonomy gateways for "
    "symlink privilege/UAC boundaries, CUDA/GPU preflight blocking, timeout-triggered "
    "network proxy-or-mirror recovery, and Windows Defender manual whitelist guidance. "
    "Research grounded in NASA HITL graceful-degradation guidance, Microsoft symbolic-link "
    "policy and Defender exclusion docs, Python urllib proxy behavior, NVIDIA CUDA Windows "
    "installation guidance, and bounded-autonomy human takeover literature."
)

SESSION_SUMMARY = (
    "Closed the HITL boundary-gateway hardening pass by adding a typed "
    "ManualInterventionRequiredError contract, Windows symlink privilege guard that refuses "
    "to silently copy large assets after WinError 1314, bounded download timeout escalation "
    "with proxy/mirror recovery via the standard wizard menu, GPU/CUDA preflight blocking "
    "before production launch, and a first-run Defender whitelist warning for Windows users. "
    "Targeted verification reached 45/45 PASS across the touched wizard, radar, surgeon, and "
    "new boundary-regression suites."
)

FILES_TOUCHED = [
    "mathart/cli_wizard.py",
    "mathart/workspace/hitl_boundary.py",
    "mathart/workspace/config_manager.py",
    "mathart/workspace/asset_injector.py",
    "mathart/workspace/atomic_downloader.py",
    "mathart/workspace/idempotent_surgeon.py",
    "mathart/workspace/preflight_radar.py",
    "mathart/workspace/mode_dispatcher.py",
    "tests/test_hitl_boundary_gateway.py",
    "docs/research/SESSION-137-HITL-BOUNDARY-RESEARCH.md",
    "PROJECT_BRAIN.json",
    "SESSION_HANDOFF.md",
]

FILES_CHANGED = [
    "UPD mathart/cli_wizard.py — add standard menu/text prompt helpers, manual-intervention prompt rendering, and Windows Defender whitelist warning banner",
    "NEW mathart/workspace/hitl_boundary.py — define ManualInterventionRequiredError, actionable operator options, timeout detection, symlink privilege detection, and Hugging Face mirror rewrite helper",
    "UPD mathart/workspace/config_manager.py — extend local-only config storage to include runtime proxy and HF mirror preferences with .gitignore shielding and env injection",
    "UPD mathart/workspace/asset_injector.py — stop silent large-file copy fallback on Windows privilege failures and escalate to manual intervention instead",
    "UPD mathart/workspace/atomic_downloader.py — convert repeated timeout transport failures into bounded manual handoff instead of endless retry behavior",
    "UPD mathart/workspace/idempotent_surgeon.py — route manual boundary errors through the standard wizard menu and persist proxy/mirror recovery choices locally",
    "UPD mathart/workspace/preflight_radar.py — add GPU-required boundary messages with CUDA-capable hardware and VRAM recommendations",
    "UPD mathart/workspace/mode_dispatcher.py — execute GPU preflight before production mode and downgrade blocked sessions to dry-run guidance",
    "NEW tests/test_hitl_boundary_gateway.py — assert WinError 1314 and HTTP Timeout scenarios raise ManualInterventionRequiredError without silent copy or crash",
    "NEW docs/research/SESSION-137-HITL-BOUNDARY-RESEARCH.md — record external industrial/academic grounding for bounded autonomy and graceful degradation",
    "UPD PROJECT_BRAIN.json — record SESSION-137 closure, validation, and next onboarding barriers",
    "UPD SESSION_HANDOFF.md — replace prior handoff with SESSION-137 boundary-hardening summary and novice-artist onboarding roadmap",
]

NEW_TASKS = [
    {
        "id": "P0-SESSION-137-HITL-BOUNDARY",
        "priority": "P0",
        "title": "Bounded Autonomy HITL Boundary Gateways for non-technical Windows operators",
        "status": "CLOSED",
        "estimated_effort": "high",
        "description": "CLOSED in SESSION-137. Added typed ManualInterventionRequiredError and reusable CLI wizard menus so four physical-boundary classes now degrade gracefully instead of crashing or overreaching: (1) Windows symlink/UAC failures on large model assets stop before silent full-copy fallback; (2) production GPU mode runs a CUDA-aware preflight and blocks with actionable dry-run fallback when no suitable GPU/VRAM is available; (3) downloader timeout storms now trigger a bounded proxy-or-mirror recovery handoff instead of open-ended retries; and (4) first-run CLI now prints manual Defender exclusion guidance for project and ComfyUI directories without attempting privileged system changes. Research and targeted validation: 45/45 PASS.",
        "completed_in": "SESSION-137",
        "updated_in": "SESSION-137",
        "primary_files": [
            "mathart/cli_wizard.py",
            "mathart/workspace/hitl_boundary.py",
            "mathart/workspace/config_manager.py",
            "mathart/workspace/asset_injector.py",
            "mathart/workspace/atomic_downloader.py",
            "mathart/workspace/idempotent_surgeon.py",
            "mathart/workspace/preflight_radar.py",
            "mathart/workspace/mode_dispatcher.py",
            "tests/test_hitl_boundary_gateway.py",
            "docs/research/SESSION-137-HITL-BOUNDARY-RESEARCH.md"
        ]
    },
    {
        "id": "P0-SESSION-138-NOVICE-FIRST-RUN-STUDIO",
        "priority": "P0",
        "title": "Novice-first startup studio for pure artist users",
        "status": "OPEN",
        "estimated_effort": "high",
        "description": "The new HITL safeguards stop dangerous automation, but a pure artist still faces a cognition gap around 'which mode should I choose now?'. Build a first-run studio that translates production / evolution / local distill / dry-run into outcome-oriented language, shows recommended presets, and explains trade-offs in plain art-production vocabulary rather than infrastructure terms.",
        "added_in": "SESSION-137",
        "updated_in": "SESSION-137"
    },
    {
        "id": "P0-SESSION-138-ENV-DOCTOR-BUNDLE",
        "priority": "P0",
        "title": "One-click environment doctor and shareable support bundle",
        "status": "OPEN",
        "estimated_effort": "medium",
        "description": "Even with manual takeover menus, novices still struggle to distinguish GPU, driver, proxy, disk, and antivirus causes. Add a one-click environment doctor that emits a human-readable checklist plus a support bundle (JSON + Markdown) users can attach when asking for help, without exposing secrets.",
        "added_in": "SESSION-137",
        "updated_in": "SESSION-137"
    },
    {
        "id": "P1-SESSION-138-ARTIST-GLOSSARY-PRESETS",
        "priority": "P1",
        "title": "Artist glossary, preset stories, and anti-jargon UX pass",
        "status": "OPEN",
        "estimated_effort": "medium",
        "description": "Current CLI and handoff language still contains terms such as ComfyUI, distill, mirror, proxy, GPU slots, and PDG workers. Ship an artist-facing glossary and preset story layer that rewrites these concepts into plain-language creation goals, with contextual explanations embedded in the wizard.",
        "added_in": "SESSION-137",
        "updated_in": "SESSION-137"
    },
    {
        "id": "P1-SESSION-138-MANUAL-RECOVERY-CHECKLISTS",
        "priority": "P1",
        "title": "Visual manual-recovery checklists for OS and network interventions",
        "status": "OPEN",
        "estimated_effort": "medium",
        "description": "The current boundary menus are actionable, but non-technical creators may still fail at multi-step OS actions such as enabling Developer Mode, verifying Defender exclusions, setting a local proxy, or checking free disk space. Convert each manual branch into step-by-step checklist assets with screenshots/GIF-ready placeholders for future desktop embedding.",
        "added_in": "SESSION-137",
        "updated_in": "SESSION-137"
    }
]


def upsert_pending_task(tasks: list[dict], payload: dict) -> None:
    for index, task in enumerate(tasks):
        if task.get("id") == payload["id"]:
            tasks[index] = payload
            return
    tasks.insert(0, payload)


with BRAIN.open("r", encoding="utf-8") as fh:
    data = json.load(fh)

data["last_session_id"] = "SESSION-137"
data["last_updated"] = LAST_UPDATED
data["validation_pass_rate"] = VALIDATION
data["total_iterations"] = int(data.get("total_iterations", 0)) + 1

for payload in NEW_TASKS:
    upsert_pending_task(data.setdefault("pending_tasks", []), payload)

recent_sessions = data.setdefault("recent_sessions", [])
recent_sessions = [
    item for item in recent_sessions
    if not isinstance(item, dict) or item.get("session_id") != "SESSION-137"
]
recent_sessions.insert(0, {
    "session_id": "SESSION-137",
    "focus": "Bounded-autonomy HITL boundary gateways and novice-safe manual degradation UX",
    "status": "COMPLETE",
    "date": "2026-04-22",
    "summary": SESSION_SUMMARY,
    "files_touched": FILES_TOUCHED,
    "next_gaps": [
        "P0-SESSION-138-NOVICE-FIRST-RUN-STUDIO: translate technical mode selection into plain artist outcomes",
        "P0-SESSION-138-ENV-DOCTOR-BUNDLE: produce a one-click support bundle for GPU/driver/network triage",
        "P1-SESSION-138-MANUAL-RECOVERY-CHECKLISTS: convert OS/network handoff branches into visual checklists"
    ]
})
data["recent_sessions"] = recent_sessions[:20]

session_log = data.setdefault("session_log", [])
session_log = [
    item for item in session_log
    if not isinstance(item, dict) or item.get("session_id") != "SESSION-137"
]
session_log.insert(0, {
    "session_id": "SESSION-137",
    "summary": SESSION_SUMMARY,
    "files_changed": FILES_CHANGED,
    "files_touched": FILES_TOUCHED,
    "validation": "45/45 targeted PASS across tests/test_hitl_boundary_gateway.py, tests/test_dual_wizard_dispatcher.py, tests/test_preflight_radar.py, and tests/test_idempotent_surgeon.py."
})
data["session_log"] = session_log

with BRAIN.open("w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
