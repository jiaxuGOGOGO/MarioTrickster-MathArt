"""CLI for SDF effect generation."""
from __future__ import annotations
import argparse
from pathlib import Path
from .effects import spike_sdf, flame_sdf, saw_blade_sdf, electric_arc_sdf, glow_sdf
from .renderer import render_sdf, render_spritesheet
from ..oklab.palette import PaletteGenerator


EFFECT_MAP = {
    "spike": ("SpikeTrap", spike_sdf, False),
    "flame": ("FireTrap", flame_sdf, True),
    "saw": ("SawBlade", saw_blade_sdf, True),
    "electric": ("ElectricArc", electric_arc_sdf, True),
    "glow": ("Glow", glow_sdf, True),
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mathart-sdf", description="Generate SDF-based pixel art effects.")
    parser.add_argument("effect", choices=list(EFFECT_MAP.keys()), help="Effect type")
    parser.add_argument("-o", "--output", default=None, help="Output path")
    parser.add_argument("--size", type=int, default=32, help="Sprite size in pixels")
    parser.add_argument("--frames", type=int, default=8, help="Animation frames")
    parser.add_argument("--palette", default=None, help="Palette JSON path")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    element_key, factory, is_animated = EFFECT_MAP[args.effect]

    # Load or generate palette
    if args.palette:
        from ..oklab.palette import Palette
        pal = Palette.load_json(args.palette)
    else:
        gen = PaletteGenerator(seed=args.seed)
        pal = gen.generate("warm_cool_shadow", count=6, name=f"{element_key}_palette")

    output = args.output or f"{args.effect}_{'sheet' if is_animated else 'sprite'}.png"

    if is_animated:
        def animated_sdf(x, y, t):
            return factory(t=t)(x, y)
        img = render_spritesheet(animated_sdf, frames=args.frames,
                                 width=args.size, height=args.size, palette=pal)
    else:
        sdf_func = factory()
        img = render_sdf(sdf_func, args.size, args.size, palette=pal)

    img.save(output)
    print(f"Saved: {output} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
