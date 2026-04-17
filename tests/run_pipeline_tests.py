"""
SESSION-061: Standalone test runner for Motion 2D Pipeline modules.
Bypasses __init__.py to avoid optional dependency issues (taichi, etc.)
"""
import sys
import os
import math
import json
import tempfile
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Direct imports (bypass __init__.py)
from mathart.animation.orthographic_projector import (
    OrthographicProjector, SpineJSONExporter, ProjectionConfig,
    create_biped_skeleton_3d, create_quadruped_skeleton_3d, create_sample_walk_clip_3d,
)
from mathart.animation.terrain_ik_2d import (
    TerrainProbe2D, FABRIK2DSolver, TerrainAdaptiveIKLoop, IKConfig,
    create_terrain_ik_loop, Joint2D,
)
from mathart.animation.principles_quantifier import PrincipleScorer, AnimFrame

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} — {detail}")


print("=" * 60)
print("Test Suite: Orthographic Projector")
print("=" * 60)

proj = OrthographicProjector(ProjectionConfig())

# Depth to sorting order
test("depth_to_sorting_order: near → high",
     proj.depth_to_sorting_order(-2.0) == 15)
test("depth_to_sorting_order: far → low",
     proj.depth_to_sorting_order(2.0) == 0)

# Project bone
from mathart.animation.orthographic_projector import Bone3D
bone = Bone3D("test", None, (1.0, 2.0, 0.5), (10.0, 20.0, 30.0), 0.5)
b2d = proj.project_bone(bone)
test("project_bone: preserves X", abs(b2d.x - 1.0) < 1e-6)
test("project_bone: preserves Y", abs(b2d.y - 2.0) < 1e-6)
test("project_bone: preserves Z rotation", abs(b2d.rotation - 30.0) < 1e-6)

# Project skeleton
bones_3d = create_biped_skeleton_3d()
bones_2d = proj.project_skeleton(bones_3d)
test("project_skeleton: count matches", len(bones_2d) == len(bones_3d))

# Project clip
clip_3d = create_sample_walk_clip_3d(n_frames=20)
clip_2d = proj.project_clip(clip_3d)
test("project_clip: frame count", len(clip_2d.frames) == 20)

# Quality metrics
metrics = proj.evaluate_quality(clip_3d, clip_2d)
print(f"  → bone_length_preservation: {metrics.bone_length_preservation:.4f}")
print(f"  → joint_angle_fidelity:     {metrics.joint_angle_fidelity:.4f}")
print(f"  → sorting_order_stability:  {metrics.sorting_order_stability:.4f}")
test("quality: bone length > 0.9", metrics.bone_length_preservation > 0.9)
test("quality: angle fidelity > 0.9", metrics.joint_angle_fidelity > 0.9)

# Quadruped skeleton
qbones = create_quadruped_skeleton_3d()
qnames = [b.name for b in qbones]
test("quadruped: has fl_ bones", any(n.startswith("fl_") for n in qnames))
test("quadruped: has hr_ bones", any(n.startswith("hr_") for n in qnames))

print()
print("=" * 60)
print("Test Suite: Spine JSON Exporter")
print("=" * 60)

exporter = SpineJSONExporter()
clip_2d.ik_constraints = [
    {"name": "left_leg", "order": 0, "bones": ["l_thigh", "l_shin"],
     "target": "l_foot", "mix": 1.0},
]
with tempfile.TemporaryDirectory() as tmpdir:
    path = exporter.export(clip_2d, Path(tmpdir) / "test.json")
    data = json.loads(path.read_text())
    test("export: file created", path.exists())
    test("export: has skeleton", "skeleton" in data)
    test("export: has bones", "bones" in data)
    test("export: has ik", "ik" in data)
    test("export: has animations", "animations" in data)
    test("export: bone count", len(data["bones"]) == len(clip_2d.skeleton_bones))
    print(f"  → Exported {len(data['bones'])} bones, {len(data['ik'])} IK constraints")

print()
print("=" * 60)
print("Test Suite: FABRIK 2D Solver")
print("=" * 60)

solver = FABRIK2DSolver(IKConfig(max_iterations=20, tolerance=0.001))
chain = [Joint2D(0, 0, "hip"), Joint2D(0, -0.3, "knee"), Joint2D(0, -0.6, "ankle")]
target = Joint2D(0.1, -0.55, "target")
solved, iters = solver.solve(chain, target)
dist = math.sqrt((solved[-1].x - target.x)**2 + (solved[-1].y - target.y)**2)
print(f"  → End effector distance: {dist:.6f} (iterations: {iters})")
test("solve: reaches target", dist < 0.01)
test("solve: used iterations", iters > 0)

# Unreachable target
chain2 = [Joint2D(0, 0, "a"), Joint2D(0, -0.3, "b"), Joint2D(0, -0.6, "c")]
target2 = Joint2D(5.0, 0.0, "far")
solved2, _ = solver.solve(chain2, target2)
test("solve: stretches toward unreachable", solved2[-1].x > 0.0)

# Constrained solve
chain3 = [Joint2D(0, 0, "hip"), Joint2D(0, -0.3, "knee"), Joint2D(0, -0.6, "ankle")]
target3 = Joint2D(0.05, -0.55, "target")
solved3, _ = solver.solve_with_constraints(chain3, target3, [(-170, -10)])
test("solve_with_constraints: returns 3 joints", len(solved3) == 3)

print()
print("=" * 60)
print("Test Suite: Terrain Adaptive IK Loop")
print("=" * 60)

loop = create_terrain_ik_loop(None)
pose_data = {
    "l_hip": (0.0, 0.8), "l_knee": (0.0, 0.5), "l_ankle": (0.0, 0.2),
    "r_hip": (0.0, 0.8), "r_knee": (0.0, 0.5), "r_ankle": (0.0, 0.2),
}
contacts = {"l_foot": 1.0, "r_foot": 0.0}
adapted = loop.adapt_pose(pose_data, contacts)
test("adapt_pose: solved 1 contact", adapted["_contacts_solved"] == 1)

# Quadruped
qpose = {
    "fl_upper": (0.3, 0.45), "fl_lower": (0.3, 0.25), "fl_paw": (0.3, 0.05),
    "hr_upper": (-0.3, 0.45), "hr_lower": (-0.3, 0.25), "hr_paw": (-0.3, 0.05),
}
qcontacts = {"front_left": 1.0, "hind_right": 1.0, "front_right": 0.0, "hind_left": 0.0}
qadapted = loop.adapt_quadruped_pose(qpose, qcontacts)
test("adapt_quadruped: solved 2 contacts", qadapted["_quadruped_contacts_solved"] == 2)

# IK quality
orig = {"l_ankle": (0.0, 0.2)}
adpt = {"l_ankle": (0.0, 0.0), "_hip_adjustment": -0.1, "_ik_iterations": 5, "_contacts_solved": 1}
ik_m = loop.evaluate_ik_quality(orig, adpt, {"l_foot": 1.0, "r_foot": 0.0})
test("ik_quality: chains solved", ik_m.total_chains_solved == 1)
test("ik_quality: contact accuracy", ik_m.contact_accuracy == 1.0)

print()
print("=" * 60)
print("Test Suite: Principles Quantifier")
print("=" * 60)

scorer = PrincipleScorer()
frames = []
for i in range(20):
    t = i / 19
    phase = t * 2 * math.pi
    frames.append(AnimFrame(
        joint_positions={
            "hip": (t*2, 0.8 + 0.02*math.sin(4*phase)),
            "l_foot": (t*2 - 0.1, max(0, math.sin(phase)) * 0.1),
            "r_foot": (t*2 + 0.1, max(0, -math.sin(phase)) * 0.1),
            "head": (t*2, 1.2),
            "l_arm": (t*2 - 0.15, 0.9 - 0.05*math.sin(phase)),
            "r_arm": (t*2 + 0.15, 0.9 + 0.05*math.sin(phase)),
        },
        joint_scales={
            "hip": (1.0, 1.0),
            "l_foot": (1.0 + 0.05*max(0, -math.sin(phase)),
                       1.0 - 0.05*max(0, -math.sin(phase))),
        },
        root_position=(t*2, 0.8 + 0.02*math.sin(4*phase)),
        time=t,
    ))

report = scorer.score_clip(frames)
print(f"  → Aggregate:       {report.aggregate_score:.4f}")
print(f"  → Squash/Stretch:  {report.squash_stretch:.4f}")
print(f"  → Anticipation:    {report.anticipation:.4f}")
print(f"  → Arcs:            {report.arcs:.4f}")
print(f"  → Timing:          {report.timing:.4f}")
print(f"  → Solid Drawing:   {report.solid_drawing:.4f}")
print(f"  → Recommendations: {len(report.recommendations)}")
test("score_clip: returns report", report.frame_count == 20)
test("score_clip: aggregate in [0,1]", 0.0 <= report.aggregate_score <= 1.0)
test("score_clip: squash_stretch > 0.8", report.squash_stretch > 0.8)
test("score_clip: to_dict works", "aggregate_score" in report.to_dict())

print()
print("=" * 60)
print("Test Suite: End-to-End Motion 2D Pipeline")
print("=" * 60)

# Must import after direct module imports work
from mathart.animation.motion_2d_pipeline import Motion2DPipeline, PipelineConfig

pipeline = Motion2DPipeline(PipelineConfig())

# Biped walk
result = pipeline.run_biped_walk(n_frames=15)
print(f"  → Biped: {result.total_frames} frames, pass={result.pipeline_pass}")
print(f"  → Projection: bone_len={result.projection_quality.bone_length_preservation:.4f}")
print(f"  → IK: contact_acc={result.ik_quality.contact_accuracy:.4f}")
print(f"  → Principles: {result.principles_report.aggregate_score:.4f}")
test("biped: frame count", result.total_frames == 15)
test("biped: has clip_2d", result.clip_2d is not None)
test("biped: has projection quality", result.projection_quality is not None)
test("biped: has IK quality", result.ik_quality is not None)
test("biped: has principles", result.principles_report is not None)

# Quadruped trot
result_q = pipeline.run_quadruped_trot(n_frames=15)
print(f"  → Quadruped: {result_q.total_frames} frames, pass={result_q.pipeline_pass}")
test("quadruped: frame count", result_q.total_frames == 15)
test("quadruped: has clip_2d", result_q.clip_2d is not None)

# Spine export
with tempfile.TemporaryDirectory() as tmpdir:
    path = pipeline.export_spine_json(result, Path(tmpdir) / "walk.json")
    data = json.loads(path.read_text())
    test("export: file created", path.exists())
    test("export: has ik constraints", len(data.get("ik", [])) > 0)
    print(f"  → Exported: {len(data['bones'])} bones, {len(data['ik'])} IK")

# Result to dict
d = result.to_dict()
test("to_dict: has total_frames", "total_frames" in d)
test("to_dict: has pipeline_pass", "pipeline_pass" in d)

print()
print("=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

sys.exit(1 if failed > 0 else 0)
