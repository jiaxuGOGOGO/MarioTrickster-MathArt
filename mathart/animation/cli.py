"""CLI for skeletal animation generation."""
from __future__ import annotations
import argparse
from .skeleton import Skeleton
from .presets import idle_animation, run_animation, jump_animation, fall_animation, hit_animation
from .renderer import render_skeleton_sheet
from ..oklab.palette import PaletteGenerator

ANIM_MAP = {
    "idle": idle_animation,
    "run": run_animation,
    "jump": jump_animation,
    "fall": fall_animation,
    "hit": hit_animation,
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mathart-anim", description="Generate skeletal animation sprite sheets.")
    parser.add_argument("action", choices=list(ANIM_MAP.keys()), help="Animation type")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--size", type=int, default=32, help="Frame size")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--head-units", type=float, default=3.0, help="Character head units")
    parser.add_argument("--palette", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    skel = Skeleton.create_humanoid(head_units=args.head_units)
    anim_func = ANIM_MAP[args.action]

    if args.palette:
        from ..oklab.palette import Palette
        pal = Palette.load_json(args.palette)
    else:
        gen = PaletteGenerator(seed=args.seed)
        pal = gen.generate("warm_cool_shadow", count=6, name=f"char_{args.action}")

    output = args.output or f"anim_{args.action}_sheet.png"
    sheet = render_skeleton_sheet(skel, anim_func, args.frames, args.size, args.size, pal)
    sheet.save(output)
    print(f"Saved: {output} ({sheet.width}x{sheet.height}, {args.frames} frames)")


if __name__ == "__main__":
    main()
