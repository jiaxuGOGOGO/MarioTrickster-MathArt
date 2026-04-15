"""
End-to-end test of the complete asset production pipeline.
This is the definitive test that proves the project can produce real game assets.
"""
import os
import time

from mathart.pipeline import AssetPipeline, AssetSpec, AnimationSpec

# Create pipeline
pipeline = AssetPipeline(output_dir="test_outputs/pipeline", verbose=True)

print("=" * 60)
print("ASSET PRODUCTION PIPELINE - END-TO-END TEST")
print("=" * 60)

# ── Test 1: Single Sprite Production ──
print("\n>>> Test 1: Single Sprite (Evolved Coin)")
coin_result = pipeline.produce_sprite(AssetSpec(
    name="gold_coin",
    shape="coin",
    style="metal",
    size=64,
    evolution_iterations=15,
    population_size=12,
    quality_threshold=0.5,
    seed=42,
))
print(f"  Score: {coin_result.score:.4f}")
print(f"  Files: {len(coin_result.output_paths)}")

# ── Test 2: Textured Sprite ──
print("\n>>> Test 2: Textured Sprite (Stone Platform)")
platform_result = pipeline.produce_sprite(AssetSpec(
    name="stone_platform",
    shape="platform",
    style="stone",
    size=64,
    evolution_iterations=10,
    population_size=10,
    quality_threshold=0.5,
))
print(f"  Score: {platform_result.score:.4f}")

# ── Test 3: Animated Spritesheet ──
print("\n>>> Test 3: Animated Spritesheet (Bouncing Gem)")
gem_anim = pipeline.produce_animation(AnimationSpec(
    asset=AssetSpec(
        name="bouncing_gem",
        shape="gem",
        style="crystal",
        size=64,
        evolution_iterations=10,
        population_size=10,
        quality_threshold=0.5,
    ),
    animation_type="jump",
    n_frames=8,
    fps=12,
))
print(f"  Score: {gem_anim.score:.4f}")
print(f"  Frames: {len(gem_anim.frames)}")
print(f"  Files: {len(gem_anim.output_paths)}")

# ── Test 4: CPPN Texture Atlas ──
print("\n>>> Test 4: CPPN Texture Atlas")
tex_result = pipeline.produce_texture_atlas(
    name="evolved_textures",
    n_textures=16,
    evolution_steps=100,
    tile_size=64,
    seed=42,
)
print(f"  Textures: {tex_result.metadata.get('n_textures', 0)}")
print(f"  Archive cells: {tex_result.metadata.get('archive_size', 0)}")
print(f"  Best fitness: {tex_result.score:.4f}")

# ── Test 5: Idle Animation ──
print("\n>>> Test 5: Idle Animation (Breathing Circle)")
idle_result = pipeline.produce_animation(AnimationSpec(
    asset=AssetSpec(
        name="idle_orb",
        shape="circle",
        style="default",
        size=64,
        evolution_iterations=8,
        population_size=8,
        quality_threshold=0.5,
    ),
    animation_type="idle",
    n_frames=6,
))
print(f"  Score: {idle_result.score:.4f}")
print(f"  Frames: {len(idle_result.frames)}")

# ── Summary ──
print("\n" + "=" * 60)
print("PRODUCTION SUMMARY")
print("=" * 60)
log = pipeline.get_production_log()
total_time = sum(e["elapsed"] for e in log)
total_score = sum(e["score"] for e in log) / len(log) if log else 0
print(f"  Total assets produced: {len(log)}")
print(f"  Average score: {total_score:.4f}")
print(f"  Total time: {total_time:.1f}s")

# Count all output files
all_files = []
for root, dirs, files in os.walk("test_outputs/pipeline"):
    for f in files:
        all_files.append(os.path.join(root, f))
print(f"  Total output files: {len(all_files)}")

for entry in log:
    print(f"    {entry['name']}: score={entry['score']:.4f}, time={entry['elapsed']:.1f}s")

print("\n=== PIPELINE E2E TEST COMPLETE ===")
