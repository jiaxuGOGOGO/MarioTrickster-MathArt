"""CLI for batch asset export."""
from __future__ import annotations
import argparse
from pathlib import Path
from .bridge import AssetExporter, ExportConfig


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mathart-export", description="Export assets for Unity.")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--style", default="Style_MathArt")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--generate-demo", action="store_true", help="Generate demo assets")
    args = parser.parse_args(argv)

    config = ExportConfig(
        output_dir=args.output_dir,
        style_name=args.style,
        version=args.version,
    )

    if args.generate_demo:
        _generate_demo(config)
    else:
        print("Use --generate-demo to create sample assets, or import AssetExporter in your scripts.")


def _generate_demo(config: ExportConfig):
    """Generate a complete demo asset set."""
    from ..oklab.palette import PaletteGenerator
    from ..sdf.effects import spike_sdf, flame_sdf, saw_blade_sdf, glow_sdf
    from ..sdf.renderer import render_sdf, render_spritesheet
    from ..animation.skeleton import Skeleton
    from ..animation.presets import idle_animation, run_animation
    from ..animation.renderer import render_skeleton_sheet

    exporter = AssetExporter(config)
    gen = PaletteGenerator(seed=42)

    # Generate theme palettes
    palettes = gen.generate_theme_palette("grassland")

    # 1. Static hazards
    spike_pal = palettes["hazards"]
    spike_img = render_sdf(spike_sdf(), 32, 32, spike_pal)
    exporter.export_sprite(spike_img, "spike_trap", "Hazards", "SpikeTrap")
    print("  Exported: spike_trap")

    # 2. Animated effects
    def flame_animated(x, y, t):
        return flame_sdf(t=t)(x, y)
    flame_sheet = render_spritesheet(flame_animated, 8, 32, 32, spike_pal)
    exporter.export_spritesheet(flame_sheet, "fire_trap", "Hazards", 8, "FireTrap")
    print("  Exported: fire_trap (8 frames)")

    def saw_animated(x, y, t):
        return saw_blade_sdf(t=t)(x, y)
    saw_sheet = render_spritesheet(saw_animated, 8, 32, 32, spike_pal)
    exporter.export_spritesheet(saw_sheet, "saw_blade", "Hazards", 8, "SawBlade")
    print("  Exported: saw_blade (8 frames)")

    def glow_animated(x, y, t):
        return glow_sdf(t=t)(x, y)
    glow_pal = gen.generate("warm_cool_shadow", count=6, name="glow")
    glow_sheet = render_spritesheet(glow_animated, 8, 32, 32, glow_pal)
    exporter.export_spritesheet(glow_sheet, "collectible_glow", "VFX", 8, "Collectible")
    print("  Exported: collectible_glow (8 frames)")

    # 3. Character animations
    char_pal = palettes["characters"]
    skel = Skeleton.create_humanoid(head_units=3.0)

    idle_sheet = render_skeleton_sheet(skel, idle_animation, 8, 32, 32, char_pal)
    exporter.export_spritesheet(idle_sheet, "mario_idle", "Characters", 8)
    print("  Exported: mario_idle (8 frames)")

    run_sheet = render_skeleton_sheet(skel, run_animation, 8, 32, 32, char_pal)
    exporter.export_spritesheet(run_sheet, "mario_run", "Characters", 8)
    print("  Exported: mario_run (8 frames)")

    # 4. Environment tiles
    from ..sdf.primitives import box
    from ..sdf.renderer import render_sdf as render
    ground_pal = palettes["ground"]
    ground_img = render(box(0, 0, 0.9, 0.9), 32, 32, ground_pal, outline_width=0.05)
    exporter.export_sprite(ground_img, "ground_tile", "Environment")
    print("  Exported: ground_tile")

    platform_pal = palettes["platform"]
    platform_img = render(box(0, 0, 0.9, 0.3), 32, 32, platform_pal, outline_width=0.04)
    exporter.export_sprite(platform_img, "platform_tile", "Environment")
    print("  Exported: platform_tile")

    # Save manifest
    manifest_path = exporter.save_manifest()
    print(f"\nManifest: {manifest_path}")
    print(f"Total assets: {len(exporter.manifest)}")


if __name__ == "__main__":
    main()
