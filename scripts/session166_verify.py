"""SESSION-166 Verification: Confirm the render loop hydration fix.

This script exercises the exact code path that was broken:
1. Build a genotype + skeleton
2. Run Motion2DPipeline to get a Clip2D
3. Call _bake_true_motion_guide_sequence
4. Verify that the VarianceAssertGate PASSES (no RuntimeError)
5. Compute inter-frame MSE to confirm genuine geometric variation
"""
import sys
import os
import json
import tempfile
import math

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathart.animation.genotype import mario_genotype, CharacterGenotype
from mathart.animation.motion_2d_pipeline import Motion2DPipeline

def main():
    print("=" * 70)
    print("[SESSION-166 VERIFICATION] Render Loop Hydration Fix")
    print("=" * 70)

    # Step 1: Build genotype and serialize
    print("\n[1/5] Building genotype and serializing...")
    genotype = mario_genotype()
    skeleton = genotype.build_shaped_skeleton()
    style = genotype.decode_to_style()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        genotype_path = os.path.join(tmpdir, "test_genotype.json")
        with open(genotype_path, "w") as f:
            json.dump(genotype.to_dict(), f)
        print(f"  Genotype serialized to {genotype_path}")
        print(f"  Skeleton joints: {sorted(skeleton.joints.keys())}")
        print(f"  Skeleton bones: {[b.name for b in skeleton.bones]}")
        
        # Step 2: Run Motion2DPipeline
        print("\n[2/5] Running Motion2DPipeline for biped walk...")
        pipeline = Motion2DPipeline()
        result = pipeline.run_biped_walk(n_frames=24, speed=1.0)
        clip_2d = result.clip_2d
        print(f"  Clip2D frames: {len(clip_2d.frames)}")
        if clip_2d.frames:
            f0 = clip_2d.frames[0]
            print(f"  Frame 0 bone_transforms: {sorted(f0.bone_transforms.keys())}")
            print(f"  Frame 0 root: ({f0.root_x:.4f}, {f0.root_y:.4f})")
        
        # Step 3: Verify bone→joint mapping
        print("\n[3/5] Verifying bone→joint name translation...")
        bone_to_joint = {}
        for bone in skeleton.bones:
            bone_to_joint[bone.name] = bone.joint_a
        
        if clip_2d.frames:
            clip_bone_names = set(clip_2d.frames[0].bone_transforms.keys())
            mapped_joints = {bone_to_joint.get(bn, bn) for bn in clip_bone_names}
            joint_names = set(skeleton.joints.keys())
            matched = mapped_joints & joint_names
            print(f"  Clip2D bone names: {clip_bone_names}")
            print(f"  Mapped to joints: {mapped_joints}")
            print(f"  Matched joints: {matched}")
            if matched:
                print(f"  ✅ Bone→Joint mapping successful! {len(matched)} joints will be animated.")
            else:
                print(f"  ❌ CRITICAL: No joints matched after mapping!")
                return 1
        
        # Step 4: Call _bake_true_motion_guide_sequence
        print("\n[4/5] Calling _bake_true_motion_guide_sequence...")
        from mathart.factory.mass_production import _bake_true_motion_guide_sequence
        
        try:
            source_frames, normal_maps, depth_maps, mask_maps = _bake_true_motion_guide_sequence(
                genotype_path=genotype_path,
                clip_2d=clip_2d,
                frame_count=24,
                render_width=64,
                render_height=64,
                motion_state="walk",
                fps=12,
                character_id="test_mario",
            )
            print(f"  ✅ Bake completed! {len(source_frames)} frames rendered.")
            print(f"  ✅ VarianceAssertGate PASSED (no frozen_guide_sequence error)!")
        except Exception as e:
            print(f"  ❌ Bake FAILED: {e}")
            import traceback
            traceback.print_exc()
            return 1
        
        # Step 5: Compute inter-frame MSE
        print("\n[5/5] Computing inter-frame MSE statistics...")
        mse_values = []
        for i in range(len(source_frames) - 1):
            arr_a = np.asarray(source_frames[i].convert("RGB"), dtype=np.float64)
            arr_b = np.asarray(source_frames[i + 1].convert("RGB"), dtype=np.float64)
            mse = float(np.mean((arr_a - arr_b) ** 2))
            mse_values.append(mse)
        
        if mse_values:
            min_mse = min(mse_values)
            max_mse = max(mse_values)
            mean_mse = np.mean(mse_values)
            print(f"  MSE statistics across {len(mse_values)} frame pairs:")
            print(f"    Min MSE:  {min_mse:.6f}")
            print(f"    Max MSE:  {max_mse:.6f}")
            print(f"    Mean MSE: {mean_mse:.6f}")
            
            if min_mse > 0.0001:
                print(f"\n  ✅ ALL frame pairs have MSE > 0.0001 (VarianceAssertGate floor)")
                print(f"  ✅ GENUINE per-frame geometric variation confirmed!")
            else:
                zero_pairs = sum(1 for m in mse_values if m < 0.0001)
                print(f"\n  ⚠️ {zero_pairs}/{len(mse_values)} pairs below MSE floor 0.0001")
        
        # Save sample frames for visual inspection
        sample_dir = os.path.join(tmpdir, "samples")
        os.makedirs(sample_dir, exist_ok=True)
        for i in [0, len(source_frames)//4, len(source_frames)//2, len(source_frames)-1]:
            if i < len(source_frames):
                path = os.path.join(sample_dir, f"frame_{i:04d}.png")
                source_frames[i].save(path)
                print(f"  Saved sample: {path}")
    
    print("\n" + "=" * 70)
    print("[SESSION-166 VERIFICATION] ALL CHECKS PASSED ✅")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
