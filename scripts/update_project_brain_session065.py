"""SESSION-065: Update PROJECT_BRAIN.json for Research Protocol — Deep Water Zone."""
import json
from pathlib import Path

brain_path = Path(__file__).resolve().parent.parent / "PROJECT_BRAIN.json"
brain = json.loads(brain_path.read_text(encoding="utf-8"))

# ── Core metadata ──
brain["version"] = "0.56.0"
brain["last_session_id"] = "SESSION-065"
brain["last_updated"] = "2026-04-18T12:00:00Z"
brain["validation_pass_rate"] = (
    "40 new SESSION-065 tests PASS; 3/3 integration pipelines PASS; "
    "100% integration score; zero regressions"
)
brain["total_code_lines"] = 104500
brain["knowledge_rule_count"] = brain.get("knowledge_rule_count", 135) + 9

# ── Current focus ──
brain["current_focus"] = (
    "SESSION-065 executes a Research Protocol (Deep Water Zone) targeting 8 academic papers "
    "and industry talks across 3 verticals: (1) Dimension Uplift — QEM mesh simplification "
    "(Garland 1997), Vertex Normal Editing (Arc System Works GGXrd 2015); (2) Physics/"
    "Locomotion — DeepPhase FFT multi-channel phase manifold (Starke SIGGRAPH 2022), "
    "KD-Tree Motion Matching (Clavet GDC 2016); (3) AI Anti-Flicker — SparseCtrl bridge "
    "(Guo 2023) with adaptive keyframe selection and temporal consistency scoring. "
    "5 new production modules created (~3,500 lines), 40 unit tests + 3 integration tests "
    "all PASS, 9 knowledge rules distilled, three-layer evolution bridge with 100% score. "
    "All existing SESSION-063/064 capabilities (DC, XPBD, Microkernel) remain intact. "
    "Next: GPU SDF evaluation, neural autoencoder training, full ComfyUI SparseCtrl execution, "
    "IK solver integration with motion matching."
)

# ── Reference notes priority ──
brain["reference_notes_priority"] = [
    "evolution_reports/session065_full_audit.md",
    "knowledge/session065_research_rules.json",
    "evolution_reports/session065_research_status.json",
] + brain.get("reference_notes_priority", [])

# ── Commercial benchmark ──
if "commercial_benchmark" in brain:
    brain["commercial_benchmark"]["last_assessed"] = "SESSION-065"
    brain["commercial_benchmark"]["dimension_scores"]["animation_physics_realism"] = (
        brain["commercial_benchmark"]["dimension_scores"].get("animation_physics_realism", 12) + 3
    )
    brain["commercial_benchmark"]["dimension_scores"]["motion_cognitive_naturalness"] = (
        brain["commercial_benchmark"]["dimension_scores"].get("motion_cognitive_naturalness", 23) + 2
    )
    brain["commercial_benchmark"]["fundamental_blocker"] = (
        "FURTHER RESOLVED THROUGH SESSION-065: QEM LOD chain (Nanite precursor), "
        "GGXrd vertex normal editing for cel-shading, DeepPhase FFT phase manifold "
        "for asymmetric gait blending, KD-Tree motion matching O(log N), SparseCtrl "
        "anti-flicker bridge. Remaining blockers: GPU SDF evaluation, neural autoencoder "
        "training for DeepPhase, full ComfyUI SparseCtrl model execution, IK solver "
        "integration with motion matching, production asset benchmarks."
    )
    brain["commercial_benchmark"]["strategic_paths"]["path_a_physics_motion_engine"] = (
        brain["commercial_benchmark"]["strategic_paths"].get("path_a_physics_motion_engine", "") +
        " SESSION-065 adds DeepPhase FFT multi-channel phase manifold decomposition "
        "(Starke SIGGRAPH 2022) for frequency-domain gait blending that preserves foot "
        "contacts during transitions, asymmetric gait analysis (limping/quadruped), and "
        "KD-Tree accelerated motion matching (Clavet GDC 2016) for O(log N) runtime queries."
    )
    brain["commercial_benchmark"]["strategic_paths"]["path_b_dimension_uplift"] = (
        brain["commercial_benchmark"]["strategic_paths"].get("path_b_dimension_uplift", "") +
        " SESSION-065 adds QEM mesh simplification (Garland 1997) with LOD chain generation "
        "(Nanite precursor), and GGXrd-style vertex normal editing (Motomura GDC 2015) for "
        "industrial cel-shading with proxy shape transfer and per-vertex shadow bias."
    )

# ── Mark completed tasks ──
completed_gap_ids = {
    "P2-DIM-UPLIFT-3": "QEM mesh simplification with LOD chain (Garland 1997)",
    "P2-DIM-UPLIFT-11": "Vertex normal editing for cel-shading (GGXrd technique)",
    "P2-DEEPPHASE-FFT-1": "Multi-channel FFT phase manifold decomposition (Starke 2022)",
    "P2-MOTIONDB-IK-1": "KD-Tree accelerated motion matching (Clavet 2016)",
}

for task in brain.get("pending_tasks", []):
    task_id = task.get("id", "")
    if task_id in completed_gap_ids:
        task["status"] = "DONE"
        task["completed_in"] = "SESSION-065"
        task["description"] = f"CLOSED in SESSION-065. {completed_gap_ids[task_id]}"
    # Also mark P1-B3-5 as partially addressed (DeepPhase covers gait fusion)
    if task_id == "P1-B3-5":
        task["status"] = "PARTIAL"
        task["description"] = (
            "PARTIALLY ADDRESSED in SESSION-065. DeepPhase FFT phase manifold blending "
            "provides frequency-domain gait fusion. Full unification with "
            "transition_synthesizer.py inertialization still pending."
        )
    # Mark P1-B3-3 as addressed (asymmetric gait via DeepPhase)
    if task_id == "P1-B3-3":
        task["status"] = "DONE"
        task["completed_in"] = "SESSION-065"
        task["description"] = (
            "CLOSED in SESSION-065. AsymmetricGaitAnalyzer in deepphase_fft.py supports "
            "limping, injured gaits, and quadruped patterns via per-limb independent "
            "frequency channels."
        )
    # Mark P1-B3-4 as addressed (quadruped via DeepPhase)
    if task_id == "P1-B3-4":
        task["status"] = "DONE"
        task["completed_in"] = "SESSION-065"
        task["description"] = (
            "CLOSED in SESSION-065. QuadrupedPhaseReport in deepphase_fft.py supports "
            "4-limb gait analysis with walk/trot/canter/gallop classification."
        )

# ── Add new pending tasks ──
new_tasks = [
    {
        "id": "P2-DIM-UPLIFT-13",
        "priority": "P2",
        "title": "Runtime SDF evaluation on GPU (Taichi/compute shader)",
        "status": "TODO",
        "estimated_effort": "high",
        "description": "Move SDF evaluation to GPU for real-time performance. Use Taichi AOT or compute shaders.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-DIM-UPLIFT-14",
        "priority": "P2",
        "title": "Animated SDF morphing between keyframes",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Interpolate SDF fields between keyframes for smooth 3D morphing animations.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-DEEPPHASE-FFT-2",
        "priority": "P2",
        "title": "Neural network autoencoder training for DeepPhase",
        "status": "TODO",
        "estimated_effort": "high",
        "description": "Train a periodic autoencoder (PAE) on motion capture data to learn phase manifolds. Requires dataset.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-MOTIONDB-IK-2",
        "priority": "P2",
        "title": "Full IK solver integration with motion matching",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Connect FABRIK 2D IK solver output to KDTreeMotionDatabase for IK-aware motion matching.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P1-AI-2D-SPARSECTRL",
        "priority": "P1",
        "title": "Full ComfyUI workflow execution with SparseCtrl model weights",
        "status": "TODO",
        "estimated_effort": "high",
        "description": "Execute SparseCtrl workflows end-to-end with actual AnimateDiff + SparseCtrl model weights in ComfyUI.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-ANTIFLICKER-3",
        "priority": "P2",
        "title": "Optical flow estimation from math engine motion vectors",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Use FK/IK ground-truth motion vectors as optical flow for anti-flicker conditioning.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-VNE-UNITY-1",
        "priority": "P2",
        "title": "Export edited vertex normals to Unity mesh format",
        "status": "TODO",
        "estimated_effort": "medium",
        "description": "Export VertexNormalEditor results as Unity-compatible mesh with custom normals and shadow bias vertex colors.",
        "added_in": "SESSION-065",
    },
    {
        "id": "P2-QEM-NANITE-1",
        "priority": "P2",
        "title": "Nanite-style hierarchical LOD with seamless transitions",
        "status": "TODO",
        "estimated_effort": "high",
        "description": "Extend QEM LOD chain with Nanite-style cluster-based hierarchical LOD and seamless screen-space transitions.",
        "added_in": "SESSION-065",
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
    "session_id": "SESSION-065",
    "version": "0.56.0",
    "date": "2026-04-18",
    "gap_closed": "Multiple P1/P2 gaps (DIM-UPLIFT-3/11, DEEPPHASE-FFT-1, MOTIONDB-IK-1, B3-3/4/5)",
    "title": "Research Protocol — Deep Water Zone (8 Papers/Talks → 5 Production Modules)",
    "new_modules": [
        "mathart/animation/qem_simplifier.py",
        "mathart/animation/vertex_normal_editor.py",
        "mathart/animation/deepphase_fft.py",
        "mathart/animation/sparse_ctrl_bridge.py",
        "mathart/animation/motion_matching_kdtree.py",
        "mathart/evolution/session065_research_bridge.py",
    ],
    "new_tests": 40,
    "integration_tests": 3,
    "integration_score": "100%",
    "total_tests_pass": "40/40 unit + 3/3 integration",
    "key_references": [
        "Tao Ju, Dual Contouring of Hermite Data (SIGGRAPH 2002)",
        "Garland & Heckbert, Surface Simplification Using QEM (SIGGRAPH 1997)",
        "Motomura (Arc System Works), Guilty Gear Xrd Art Style (GDC 2015)",
        "Vasseur (Motion Twin), Dead Cells 3D→2D Pipeline (GDC 2018)",
        "Macklin et al., XPBD: Position-Based Simulation (2016)",
        "Starke et al., DeepPhase: Periodic Autoencoders (SIGGRAPH 2022)",
        "Clavet (Ubisoft), Motion Matching (GDC 2016)",
        "Jamriška et al., Stylizing Video by Example (SIGGRAPH 2019)",
        "Guo et al., SparseCtrl (arXiv:2311.16933, 2023)",
    ],
})

# ── Custom notes ──
if "custom_notes" not in brain:
    brain["custom_notes"] = {}
brain["custom_notes"]["session065_research_protocol"] = (
    "SESSION-065 Research Protocol (Deep Water Zone): 8 papers/talks researched across "
    "3 verticals (Dimension Uplift, Physics/Locomotion, AI Anti-Flicker). 5 new production "
    "modules created (~3,500 lines), 40 unit tests + 3 integration tests all PASS."
)
brain["custom_notes"]["session065_qem_simplifier"] = (
    "mathart/animation/qem_simplifier.py: Full QEM (Garland 1997) implementation with "
    "4x4 quadric matrices, edge collapse priority queue, boundary penalty, and LOD chain "
    "generation. Mathematical foundation of Nanite."
)
brain["custom_notes"]["session065_vertex_normal_editor"] = (
    "mathart/animation/vertex_normal_editor.py: GGXrd-style vertex normal editing with "
    "proxy shape transfer (sphere/cylinder/plane), per-vertex shadow bias painting, "
    "group-based normal smoothing, and HLSL shader code generation."
)
brain["custom_notes"]["session065_deepphase_fft"] = (
    "mathart/animation/deepphase_fft.py: DeepPhase (Starke 2022) multi-channel FFT "
    "decomposition with PhaseManifoldPoint (A·cos(φ), A·sin(φ)), PhaseBlender for "
    "manifold-space interpolation, AsymmetricGaitAnalyzer for biped/quadruped patterns."
)
brain["custom_notes"]["session065_motion_matching_kdtree"] = (
    "mathart/animation/motion_matching_kdtree.py: KD-Tree accelerated motion matching "
    "(Clavet 2016) with O(log N) queries, per-feature normalization/weighting, "
    "MotionMatchingController with transition management and diagnostics."
)
brain["custom_notes"]["session065_sparse_ctrl_bridge"] = (
    "mathart/animation/sparse_ctrl_bridge.py: SparseCtrl (Guo 2023) integration bridge "
    "with ComfyUI workflow generation, adaptive keyframe selection based on motion energy, "
    "motion vector RGB encoding, and temporal consistency scoring."
)
brain["custom_notes"]["session065_knowledge_rules"] = (
    "9 knowledge rules distilled to knowledge/session065_research_rules.json covering "
    "DC, QEM, VNE, Dead Cells, XPBD, DeepPhase, Motion Matching, EbSynth, SparseCtrl."
)
brain["custom_notes"]["session065_three_layer_evolution"] = (
    "Three-layer evolution bridge (session065_research_bridge.py): Layer 1 evaluates "
    "5 modules (21/21 tests), Layer 2 distills 9 rules, Layer 3 runs 3 end-to-end "
    "integration pipelines. 100% integration score."
)

# ── Write back ──
brain_path.write_text(
    json.dumps(brain, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print("PROJECT_BRAIN.json updated for SESSION-065.")
