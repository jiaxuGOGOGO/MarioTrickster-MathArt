from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRAIN_PATH = REPO_ROOT / "PROJECT_BRAIN.json"


def upsert_open_task(tasks: list[dict], task: dict) -> None:
    for idx, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            tasks[idx] = {**existing, **task}
            return
    tasks.append(task)


def upsert_completed_task(tasks: list[dict], task: dict) -> None:
    for idx, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            tasks[idx] = {**existing, **task}
            return
    tasks.insert(0, task)


def main() -> None:
    data = json.loads(BRAIN_PATH.read_text(encoding="utf-8"))

    data["version"] = "0.51.0"
    data["last_session_id"] = "SESSION-060"
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["validation_pass_rate"] = (
        "28/28 Breakwall regression PASS; py_compile PASS; real SESSION-060 anti-flicker cycle persisted; "
        "prior SESSION-059 Unity/VAT regression remained green at session start"
    )
    data["total_iterations"] = max(int(data.get("total_iterations", 0)), 522)
    data["total_code_lines"] = max(int(data.get("total_code_lines", 0)), 90000)
    data["distill_session_id"] = "DISTILL-009"
    data["knowledge_rule_count"] = max(int(data.get("knowledge_rule_count", 0)), 109)
    data["commercial_benchmark"]["last_assessed"] = "SESSION-060"
    data["commercial_benchmark"]["overall_completion"] = "66-68%"
    data["commercial_benchmark"]["dimension_scores"]["character_visual_quality"] = 51
    data["commercial_benchmark"]["dimension_scores"]["engineering_automation"] = 94
    data["commercial_benchmark"]["fundamental_blocker"] = (
        "SESSION-060 upgrades the visual path from single-frame styling to an industrial anti-flicker baseline with sparse keyframe planning, guide locking, mask-guided propagation, and persistent Breakwall state. "
        "Main remaining blockers: expose this Phase 2 visual path through the standard AssetPipeline/CLI, ship real ComfyUI node-template packs for batch production, extend planner coverage to higher-nonlinearity action segments, and complete the upstream 3D-to-2D mesh bake workflow."
    )
    data["commercial_benchmark"]["strategic_paths"]["path_b_ai_visual_polish"] += (
        " SESSION-060 converts the same path into an industrial anti-flicker production baseline: "
        "headless_comfy_ebsynth.py now produces sparse motion-adaptive keyframe plans, mask outputs, workflow manifests, identity-lock metadata, long-range drift metrics, and temporal stability scores; "
        "breakwall_evolution_bridge.py now persists those signals, distills positive production recipes, and auto-tunes identity/mask guide weights across cycles."
    )

    data["current_focus"] = (
        "SESSION-060 executes the industrial anti-flicker push for the visual AI path: sparse AI keyframes, multi-guide locking, mask-guided propagation, identity-aware metadata, and Breakwall three-layer evolution now form a production baseline. "
        "Immediate next priorities: expose this path via standard CLI/AssetPipeline entrypoints, ship real ComfyUI node-template presets for batch jobs, and extend segment-aware planning to jump/fall/hit sequences."
    )

    ref = data.get("reference_notes_priority", [])
    new_refs = [
        "research/session060_research_notes.md",
        "evolution_reports/session060_visual_anti_flicker_cycle.json",
        "evolution_reports/session060_visual_anti_flicker_audit.md",
        "SESSION_HANDOFF.md",
    ]
    merged = []
    for item in new_refs + ref:
        if item not in merged:
            merged.append(item)
    data["reference_notes_priority"] = merged

    open_tasks = data.setdefault("open_tasks", [])
    upsert_open_task(open_tasks, {
        "id": "P1-AI-2C",
        "priority": "P1",
        "title": "Expose Phase 2 anti-flicker visual pipeline through CLI / AssetPipeline",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Promote the SESSION-060 sparse-keyframe + guide-lock + mask-guided propagation pipeline from bridge/runtime-helper status into the repository's standard export flow.",
        "added_in": "SESSION-060"
    })
    upsert_open_task(open_tasks, {
        "id": "P1-AI-2D",
        "priority": "P1",
        "title": "Ship real ComfyUI batch preset packs for IP-Adapter + ControlNet anti-flicker jobs",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Provide reusable node-template presets and parameter bundles for real ComfyUI-backed production jobs using sparse keyframes, IP-Adapter identity lock, dual ControlNet, masks, and motion vectors.",
        "added_in": "SESSION-060"
    })
    upsert_open_task(open_tasks, {
        "id": "P1-AI-2E",
        "priority": "P1",
        "title": "Extend motion-adaptive keyframe planning to high-nonlinearity action segments",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Generalize SESSION-060 motion-adaptive keyframe planning from idle-like sequences to jump, fall, hit, attack, and segmented action clips with tighter max-gap control.",
        "added_in": "SESSION-060"
    })
    upsert_open_task(open_tasks, {
        "id": "P1-AI-2F",
        "priority": "P1",
        "title": "Sequence-level anti-flicker audit data model for neural rendering",
        "status": "DONE",
        "estimated_effort": "medium",
        "description": "CLOSED in SESSION-060. The neural rendering pipeline now records keyframe plans, workflow manifests, mask outputs, temporal stability, drift, identity consistency, and guide-lock metrics.",
        "added_in": "SESSION-060",
        "closed_in": "SESSION-060"
    })
    upsert_open_task(open_tasks, {
        "id": "P1-AI-2G",
        "priority": "P1",
        "title": "Breakwall Phase 2 anti-flicker bridge upgrade",
        "status": "DONE",
        "estimated_effort": "medium",
        "description": "CLOSED in SESSION-060. BreakwallEvolutionBridge now persists and distills guide-lock, identity stability, long-range drift, temporal stability, and positive production recipes for visual anti-flicker cycles.",
        "added_in": "SESSION-060",
        "closed_in": "SESSION-060"
    })

    completed = data.setdefault("completed_tasks", [])
    upsert_completed_task(completed, {
        "id": "AUDIT-060",
        "title": "SESSION-060 visual anti-flicker industrialization audit",
        "completed_in": "SESSION-060",
        "result": "Breakwall visual pipeline upgraded with sparse keyframe planning, mask-guided propagation, identity-lock metadata, temporal stability metrics, and positive production-rule distillation. 28/28 Breakwall regression PASS. Real repository anti-flicker cycle persisted in evolution_reports/session060_visual_anti_flicker_cycle.json."
    })
    upsert_completed_task(completed, {
        "id": "KNOWLEDGE-060",
        "title": "SESSION-060 visual anti-flicker knowledge distillation",
        "completed_in": "SESSION-060",
        "result": "Research from EbSynth, ControlNet, IP-Adapter, and FlowVid was fused into the visual pipeline and Breakwall bridge. The repository now persists sparse-keyframe, guide-lock, identity, drift, and temporal-stability knowledge in code, state, and runtime evidence."
    })

    recent = data.setdefault("recent_sessions", [])
    recent.insert(0, {
        "id": "SESSION-060",
        "version": "0.51.0",
        "date": "2026-04-18",
        "title": "Industrial Anti-Flicker Upgrade for the Visual AI Pipeline",
        "summary": "Upgraded headless_comfy_ebsynth.py and BreakwallEvolutionBridge with sparse keyframe planning, identity-lock metadata, mask-guided propagation, temporal-stability metrics, and positive production-rule distillation. Real anti-flicker cycle PASS; Breakwall regression 28/28 PASS.",
        "tasks_closed": [
            "P1-AI-2F",
            "P1-AI-2G"
        ],
        "artifacts": [
            "mathart/animation/headless_comfy_ebsynth.py",
            "mathart/evolution/breakwall_evolution_bridge.py",
            "tests/test_breakwall_phase1.py",
            "evolution_reports/session060_visual_anti_flicker_audit.md",
            "evolution_reports/session060_visual_anti_flicker_cycle.json"
        ]
    })
    deduped_recent = []
    seen_ids = set()
    for entry in recent:
        sid = entry.get("id")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        deduped_recent.append(entry)
    data["recent_sessions"] = deduped_recent[:10]

    BRAIN_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(BRAIN_PATH)


if __name__ == "__main__":
    main()
