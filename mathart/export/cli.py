"""CLI for batch asset export."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .bridge import AssetExporter, ExportConfig


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mathart-export", description="Export assets for Unity.")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--style", default="Style_MathArt")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--generate-demo", action="store_true", help="Generate demo assets")
    parser.add_argument("--from-level", default=None, metavar="LEVEL_SPEC_JSON",
                        help="Export assets from a LevelSpec JSON file")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate level spec without exporting (use with --from-level)")
    args = parser.parse_args(argv)

    config = ExportConfig(
        output_dir=args.output_dir,
        style_name=args.style,
        version=args.version,
    )

    if args.from_level:
        _export_from_level(config, args.from_level, args.validate_only)
    elif args.generate_demo:
        _generate_demo(config)
    else:
        print("Usage:")
        print("  mathart-export --generate-demo              Generate demo assets")
        print("  mathart-export --from-level spec.json       Export from level spec")
        print("  mathart-export --from-level spec.json --validate-only")


def _export_from_level(config: ExportConfig, level_spec_path: str, validate_only: bool = False):
    """Export assets from a LevelSpec JSON file using LevelSpecBridge."""
    from ..level.spec_bridge import LevelSpecBridge

    spec_path = Path(level_spec_path)
    if not spec_path.exists():
        print(f"Error: Level spec file not found: {spec_path}")
        return

    bridge = LevelSpecBridge()
    print(f"Loading level spec: {spec_path}")

    try:
        asset_spec = bridge.load_spec(spec_path)
    except Exception as exc:
        print(f"Error loading level spec: {exc}")
        return

    print(f"  Level ID: {asset_spec.level_id}")
    print(f"  Theme: {asset_spec.theme.value}")
    print(f"  Render mode: {asset_spec.render_mode.value}")
    print(f"  Sprites defined: {len(asset_spec.sprites)}")

    # Validate all sprites
    exporter = AssetExporter(config)
    validation_results = []
    for sprite_spec in asset_spec.sprites:
        result = {
            "name": sprite_spec.name,
            "category": sprite_spec.category.value,
            "size": f"{sprite_spec.width}x{sprite_spec.height}",
            "frames": sprite_spec.frame_count,
            "palette_size": sprite_spec.palette_size,
            "status": "ok",
        }
        validation_results.append(result)
        print(f"  [{sprite_spec.category.value}] {sprite_spec.name}: "
              f"{sprite_spec.width}x{sprite_spec.height}, "
              f"{sprite_spec.frame_count} frame(s), "
              f"palette={sprite_spec.palette_size}")

    if validate_only:
        print(f"\nValidation complete: {len(validation_results)} sprite(s) defined.")
        print("No assets exported (--validate-only mode).")

        # Write validation report
        report_path = Path(config.output_dir) / "validation_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "level_id": asset_spec.level_id,
            "theme": asset_spec.theme.value,
            "render_mode": asset_spec.render_mode.value,
            "sprite_count": len(validation_results),
            "sprites": validation_results,
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Validation report: {report_path}")
        return

    # Generate placeholder assets for each sprite in the spec
    from PIL import Image
    from ..oklab.palette import PaletteGenerator

    gen = PaletteGenerator(seed=42)
    palettes = gen.generate_theme_palette(asset_spec.theme.value)
    fallback_pal = gen.generate("warm_cool_shadow", count=6, name="fallback")

    exported_count = 0
    for sprite_spec in asset_spec.sprites:
        # Pick palette based on category
        cat_key = sprite_spec.category.value
        pal = palettes.get(cat_key, palettes.get("ground", fallback_pal))

        if sprite_spec.frame_count > 1:
            # Create a placeholder spritesheet
            sheet_w = sprite_spec.width * sprite_spec.frame_count
            sheet_h = sprite_spec.height
            img = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
            # Fill each frame with a slightly different shade
            for f in range(sprite_spec.frame_count):
                for y in range(sheet_h):
                    for x in range(sprite_spec.width):
                        if 2 <= x < sprite_spec.width - 2 and 2 <= y < sheet_h - 2:
                            ci = (f * 37) % max(1, len(pal))
                            r, g, b = pal[ci] if ci < len(pal) else (128, 128, 128)
                            img.putpixel((f * sprite_spec.width + x, y), (r, g, b, 255))

            try:
                exporter.export_spritesheet(
                    img, sprite_spec.name, "Environment",
                    frame_count=sprite_spec.frame_count,
                    level_id=asset_spec.level_id,
                    render_mode=asset_spec.render_mode.value,
                    source_sprite_name=sprite_spec.name,
                    tags=sprite_spec.tags,
                )
                exported_count += 1
                print(f"  Exported: {sprite_spec.name} ({sprite_spec.frame_count} frames)")
            except Exception as exc:
                print(f"  Error exporting {sprite_spec.name}: {exc}")
        else:
            # Create a placeholder static sprite
            img = Image.new("RGBA", (sprite_spec.width, sprite_spec.height), (0, 0, 0, 0))
            for y in range(sprite_spec.height):
                for x in range(sprite_spec.width):
                    if 2 <= x < sprite_spec.width - 2 and 2 <= y < sprite_spec.height - 2:
                        ci = 0
                        r, g, b = pal[ci] if ci < len(pal) else (128, 128, 128)
                        img.putpixel((x, y), (r, g, b, 255))

            try:
                exporter.export_sprite(
                    img, sprite_spec.name, "Environment",
                    level_id=asset_spec.level_id,
                    render_mode=asset_spec.render_mode.value,
                    source_sprite_name=sprite_spec.name,
                    tags=sprite_spec.tags,
                )
                exported_count += 1
                print(f"  Exported: {sprite_spec.name}")
            except Exception as exc:
                print(f"  Error exporting {sprite_spec.name}: {exc}")

    # Save manifest
    manifest_path = exporter.save_manifest()
    print(f"\nManifest: {manifest_path}")
    print(f"Total assets exported: {exported_count}")


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
