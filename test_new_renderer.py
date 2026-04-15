"""Test the upgraded SDF renderer with all new features."""
import os
from mathart.sdf.primitives import circle, box, star, triangle, ring
from mathart.sdf.operations import smooth_union
from mathart.sdf.renderer import render_sdf, render_textured_sdf, render_sdf_simple
from mathart.sdf.effects import spike_sdf, flame_sdf, saw_blade_sdf, glow_sdf
from mathart.oklab.palette import Palette, PaletteGenerator

os.makedirs("test_outputs/new", exist_ok=True)

# Palette
gen = PaletteGenerator(seed=42)
pal = gen.generate("analogous", base_hue=0.0, count=8)

# 1. Compare old vs new rendering
print("=== Old vs New Rendering ===")
for name, sdf in [("circle", circle(cx=0, cy=0, r=0.5)), ("box", box(cx=0, cy=0, hw=0.4, hh=0.4)),
                   ("star", star(cx=0, cy=0, r_outer=0.5, r_inner=0.25, n_points=5)), ("triangle", triangle()),
                   ("ring", ring(cx=0, cy=0, r=0.4, thickness=0.1))]:
    old = render_sdf_simple(sdf, 64, 64, fill_color=(180, 60, 60, 255), outline_color=(40, 20, 20, 255))
    new = render_sdf(sdf, 64, 64, fill_color=(180, 60, 60, 255), outline_color=(40, 20, 20, 255))
    old.save(f"test_outputs/new/old_{name}.png")
    new.save(f"test_outputs/new/new_{name}.png")
    print(f"  {name}: old={old.size}, new={new.size}")

# 2. Lighting angles
print("=== Lighting Angles ===")
import math
for angle_deg in [0, 45, 90, 135, 180, 225, 270, 315]:
    img = render_sdf(circle(cx=0, cy=0, r=0.5), 64, 64, fill_color=(100, 150, 200, 255),
                     light_angle=math.radians(angle_deg))
    img.save(f"test_outputs/new/light_{angle_deg}.png")
    print(f"  angle={angle_deg}deg: OK")

# 3. Textured SDFs
print("=== Textured SDFs ===")
for tex in ["stone", "wood", "metal", "organic", "crystal"]:
    img = render_textured_sdf(circle(cx=0, cy=0, r=0.5), texture_type=tex, width=64, height=64,
                              fill_color=(150, 100, 80, 255))
    img.save(f"test_outputs/new/textured_{tex}.png")
    print(f"  {tex}: OK")

# 4. Dithering comparison
print("=== Dithering ===")
for dither_size in [2, 4, 8]:
    img = render_sdf(circle(cx=0, cy=0, r=0.5), 64, 64, fill_color=(100, 180, 100, 255),
                     dither_matrix_size=dither_size)
    img.save(f"test_outputs/new/dither_{dither_size}.png")
    print(f"  bayer_{dither_size}: OK")

# No dithering
img = render_sdf(circle(cx=0, cy=0, r=0.5), 64, 64, fill_color=(100, 180, 100, 255),
                 enable_dithering=False)
img.save("test_outputs/new/no_dither.png")

# 5. Effects with new renderer
print("=== Effects ===")
spike = spike_sdf()
img = render_sdf(spike, 64, 64, fill_color=(200, 200, 50, 255))
img.save("test_outputs/new/effect_spike.png")

# 6. Smooth union with lighting
print("=== Smooth Union ===")
su = smooth_union(circle(cx=0, cy=0, r=0.3), box(cx=0, cy=0, hw=0.25, hh=0.25), k=0.15)
img = render_sdf(su, 64, 64, fill_color=(80, 120, 200, 255))
img.save("test_outputs/new/smooth_union_lit.png")

# 7. Color ramp levels
print("=== Color Ramp Levels ===")
for levels in [3, 5, 7]:
    img = render_sdf(circle(cx=0, cy=0, r=0.5), 64, 64, fill_color=(200, 100, 50, 255),
                     color_ramp_levels=levels)
    img.save(f"test_outputs/new/ramp_{levels}.png")
    print(f"  {levels} levels: OK")

# 8. No outline
img = render_sdf(circle(cx=0, cy=0, r=0.5), 64, 64, fill_color=(200, 100, 50, 255),
                 enable_outline=False)
img.save("test_outputs/new/no_outline.png")

# 9. AO strength comparison
print("=== AO Strength ===")
for ao in [0.0, 0.3, 0.6, 0.9]:
    img = render_sdf(star(cx=0, cy=0, r_outer=0.5, r_inner=0.25, n_points=5), 64, 64, fill_color=(200, 100, 50, 255),
                     ao_strength=ao)
    img.save(f"test_outputs/new/ao_{int(ao*100)}.png")
    print(f"  ao={ao}: OK")

# 10. Larger sprites (64x64 for game use)
print("=== Game-Ready Sprites ===")
for name, sdf, color in [
    ("coin", ring(cx=0, cy=0, r=0.35, thickness=0.12), (255, 200, 50, 255)),
    ("gem", star(cx=0, cy=0, r_outer=0.4, r_inner=0.2, n_points=4), (50, 200, 255, 255)),
    ("shield", smooth_union(circle(cx=0, cy=0, r=0.35), box(cx=0, cy=0, hw=0.3, hh=0.1), k=0.1), (100, 100, 200, 255)),
]:
    img = render_sdf(sdf, 64, 64, fill_color=color, color_ramp_levels=5)
    img.save(f"test_outputs/new/game_{name}.png")
    print(f"  {name}: OK")

print(f"\n=== All outputs saved to test_outputs/new/ ===")
print(f"Total files: {len(os.listdir('test_outputs/new'))}")
