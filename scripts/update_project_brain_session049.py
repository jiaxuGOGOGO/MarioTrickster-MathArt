"""SESSION-049: Update PROJECT_BRAIN.json for Gap B3 closure."""
import json
from pathlib import Path

brain_path = Path(__file__).resolve().parent.parent / "PROJECT_BRAIN.json"
brain = json.loads(brain_path.read_text(encoding="utf-8"))

# ── Core metadata ──
brain["version"] = "0.40.0"
brain["last_session_id"] = "SESSION-049"
brain["last_updated"] = "2026-04-17T18:00:00Z"
brain["validation_pass_rate"] = "54 new tests PASS (Gap B3); 949 core tests PASS; zero new regressions"
brain["total_code_lines"] = 66100
brain["knowledge_rule_count"] = 63

# ── Current focus ──
brain["current_focus"] = (
    "SESSION-049 closes Gap B3 by implementing Phase-Preserving Gait Transition Blending "
    "with Marker-based DTW, Stride Wheel (David Rosen), Leader-Follower Synchronized Blend "
    "(UE Sync Groups), and Adaptive Bounce. SyncMarker + GaitSyncProfile define foot-contact "
    "events; StrideWheel drives animation phase from distance traveled; GaitBlender performs "
    "Leader-Follower weight-based blending with phase warping; adaptive_bounce provides "
    "speed-dependent vertical oscillation. GaitBlendEvolutionBridge provides three-layer "
    "evaluation, distillation, and fitness integration. All previous SESSION-043/044/045/046/"
    "047/048 capabilities remain intact. Next: pipeline integration of GaitBlender, "
    "production benchmark assets, real-time EbSynth/ComfyUI demo, and full XPBD coupling."
)

# ── Reference notes priority ──
brain["reference_notes_priority"] = [
    "docs/research/GAP_B3_GAIT_TRANSITION_PHASE_BLEND.md",
    "docs/audit/SESSION_049_AUDIT.md",
] + brain.get("reference_notes_priority", [])

# ── Commercial benchmark ──
brain["commercial_benchmark"]["last_assessed"] = "SESSION-049"
brain["commercial_benchmark"]["animation_physics_realism"] = brain["commercial_benchmark"]["dimension_scores"].get("animation_physics_realism", 10)
brain["commercial_benchmark"]["dimension_scores"]["animation_physics_realism"] = 12
brain["commercial_benchmark"]["dimension_scores"]["motion_cognitive_naturalness"] = 23
brain["commercial_benchmark"]["fundamental_blocker"] = (
    "PARTIALLY RESOLVED THROUGH SESSION-049: analytical aux maps (Gap C1), neural rendering "
    "bridge (Gap C3), physics-driven particles (Gap C2), lightweight Jakobsen secondary chains "
    "(Gap B1-lite), scene-aware distance sensor (Gap B2), and gait transition blending (Gap B3) "
    "are now closed. Main remaining blockers: full XPBD two-way rigid/soft coupling, industrial "
    "runtime depth (cache/partition/layering), and benchmarked asset production."
)
brain["commercial_benchmark"]["strategic_paths"]["path_a_physics_motion_engine"] = (
    brain["commercial_benchmark"]["strategic_paths"].get("path_a_physics_motion_engine", "") +
    " SESSION-049 closes Gap B3 by implementing Marker-based DTW gait transition blending: "
    "SyncMarker/GaitSyncProfile define foot-contact events, StrideWheel drives phase from "
    "distance (Rosen), GaitBlender performs Leader-Follower synchronized blend (UE Sync Groups), "
    "and adaptive_bounce provides speed-dependent vertical oscillation. Phase warping ensures "
    "sync marker alignment before pose interpolation, eliminating foot sliding during walk/run/"
    "sneak transitions."
)

# ── Update P1-PHASE-33A to DONE ──
for task in brain.get("pending_tasks", []):
    if task.get("id") == "P1-PHASE-33A":
        task["status"] = "DONE"
        task["completed_in"] = "SESSION-049"
        task["description"] = (
            "CLOSED in SESSION-049. Phase-Preserving Gait Transition Blending with "
            "Marker-based DTW, Stride Wheel, Leader-Follower Synchronized Blend, "
            "Adaptive Bounce. SyncMarker + GaitSyncProfile + StrideWheel + GaitBlender + "
            "phase_warp + adaptive_bounce + blend_walk_run + blend_gaits_at_phase. "
            "GaitBlendEvolutionBridge provides three-layer evaluation. 54 tests PASS."
        )

# ── Add new pending tasks ──
new_tasks = [
    {
        "id": "P1-B3-1",
        "priority": "P1",
        "title": "Integrate GaitBlender into pipeline.py gait switching path",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Wire GaitBlender into AssetPipeline so gait transitions during walk/run/sneak use phase-preserving blending instead of hard cuts.",
        "added_in": "SESSION-049",
    },
    {
        "id": "P1-B3-2",
        "priority": "P1",
        "title": "Add GaitBlender reference motions to RL environment",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Use GaitBlender output as reference motions for rl_locomotion.py DeepMimic reward.",
        "added_in": "SESSION-049",
    },
    {
        "id": "P1-B3-3",
        "priority": "P1",
        "title": "Support asymmetric sync markers (limping, injured gaits)",
        "status": "TODO",
        "estimated_effort": "low",
        "description": "Extend SyncMarker with non-uniform phase positions to support asymmetric gaits like limping or injured movement.",
        "added_in": "SESSION-049",
    },
    {
        "id": "P1-B3-4",
        "priority": "P1",
        "title": "Support quadruped/multi-legged sync marker extensions",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Extend GaitSyncProfile to support 4+ foot contacts for quadruped or multi-legged characters.",
        "added_in": "SESSION-049",
    },
    {
        "id": "P1-B3-5",
        "priority": "P1",
        "title": "Unify transition_synthesizer.py with gait_blend.py",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Merge TransitionSynthesizer (inertialized blending) with GaitBlender (phase-preserving blending) into a complete transition pipeline.",
        "added_in": "SESSION-049",
    },
]
existing_ids = {t["id"] for t in brain.get("pending_tasks", [])}
for task in new_tasks:
    if task["id"] not in existing_ids:
        brain["pending_tasks"].append(task)

# ── Recent sessions ──
if "recent_sessions" not in brain:
    brain["recent_sessions"] = []
brain["recent_sessions"].insert(0, {
    "session_id": "SESSION-049",
    "version": "0.40.0",
    "date": "2026-04-17",
    "gap_closed": "B3",
    "title": "Phase-Preserving Gait Transition Blending (Marker-based DTW)",
    "new_modules": [
        "mathart/animation/gait_blend.py",
        "mathart/evolution/gait_blend_bridge.py",
    ],
    "new_tests": 54,
    "total_tests_pass": 949,
    "key_references": [
        "David Rosen (GDC 2014): Stride Wheel + Synchronized Blend + Bounce Gravity",
        "UE Sync Groups / Sync Markers: Leader-Follower architecture",
        "Bruderlin & Williams (SIGGRAPH 1995): Motion Signal Processing / DTW",
        "Kovar & Gleicher (SCA 2003): Registration Curves",
        "Ménardais et al. (SCA 2004): Support-Phase Synchronization",
        "Rune Skovbo Johansen (2009): Semi-Procedural Locomotion",
    ],
})

# ── Custom notes ──
if "custom_notes" not in brain:
    brain["custom_notes"] = {}
brain["custom_notes"]["session049_gapb3_status"] = (
    "CLOSED. Phase-Preserving Gait Transition Blending fully implemented with "
    "Marker-based DTW, Stride Wheel, Leader-Follower, and Adaptive Bounce."
)
brain["custom_notes"]["session049_gait_blend_module"] = (
    "mathart/animation/gait_blend.py adds SyncMarker, GaitSyncProfile, StrideWheel, "
    "GaitBlendLayer, GaitBlender, phase_warp, adaptive_bounce, blend_walk_run, "
    "blend_gaits_at_phase."
)
brain["custom_notes"]["session049_gait_blend_bridge"] = (
    "mathart/evolution/gait_blend_bridge.py implements three-layer evaluation, "
    "rule distillation, fitness scoring, and persistent state tracking."
)
brain["custom_notes"]["session049_stride_wheel_fix"] = (
    "StrideWheel.set_circumference() now preserves current phase by rescaling "
    "accumulated distance — critical for eliminating phase jumps during gait transitions."
)
brain["custom_notes"]["session049_test_count"] = "54 new tests, all PASS."
brain["custom_notes"]["session049_audit"] = (
    "docs/audit/SESSION_049_AUDIT.md confirms research → code → artifact → test "
    "closure for all 8 research items."
)

# ── Write back ──
brain_path.write_text(
    json.dumps(brain, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print("PROJECT_BRAIN.json updated for SESSION-049.")
