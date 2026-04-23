"""SESSION-166 Diagnostic: Check bone name vs joint name mapping."""
import sys
sys.path.insert(0, ".")

from mathart.animation.skeleton import Skeleton

skel = Skeleton.create_humanoid(head_units=3.0)
print("=== JOINT NAMES ===")
for jn in sorted(skel.joints.keys()):
    print(f"  {jn}")
print()
print("=== BONE NAMES (name: joint_a -> joint_b) ===")
for b in skel.bones:
    print(f"  {b.name}: {b.joint_a} -> {b.joint_b}")
print()
print("=== BONE NAME -> CHILD JOINT MAPPING ===")
bone_to_child = {}
for b in skel.bones:
    bone_to_child[b.name] = b.joint_b
for bn, cj in sorted(bone_to_child.items()):
    print(f"  {bn} -> {cj}")

# Now check what Clip2D bone names look like
print()
print("=== TESTING Clip2D bone_transform keys ===")
try:
    from mathart.animation.motion_2d_pipeline import Motion2DPipeline
    pipeline = Motion2DPipeline()
    result = pipeline.run_biped_walk(n_frames=10, speed=1.0)
    clip_2d = result.clip_2d
    if clip_2d.frames:
        frame0 = clip_2d.frames[0]
        frame5 = clip_2d.frames[min(5, len(clip_2d.frames)-1)]
        print(f"  Frame count: {len(clip_2d.frames)}")
        print(f"  Frame 0 bone_transforms keys: {sorted(frame0.bone_transforms.keys())}")
        print(f"  Frame 0 root: ({frame0.root_x:.4f}, {frame0.root_y:.4f})")
        print(f"  Frame 5 root: ({frame5.root_x:.4f}, {frame5.root_y:.4f})")
        print()
        print("  Frame 0 bone_transforms values:")
        for k, v in sorted(frame0.bone_transforms.items()):
            print(f"    {k}: {v}")
        print()
        print("  Frame 5 bone_transforms values:")
        for k, v in sorted(frame5.bone_transforms.items()):
            print(f"    {k}: {v}")
        
        # Check overlap between bone_transform keys and joint names
        clip_keys = set(frame0.bone_transforms.keys())
        joint_keys = set(skel.joints.keys())
        print()
        print(f"  Clip2D keys in skeleton joints: {clip_keys & joint_keys}")
        print(f"  Clip2D keys NOT in skeleton joints: {clip_keys - joint_keys}")
        print(f"  Skeleton joints NOT in Clip2D: {joint_keys - clip_keys}")
except Exception as e:
    print(f"  Error: {e}")
    import traceback
    traceback.print_exc()
